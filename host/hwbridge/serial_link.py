"""Serial port ownership: reader thread, line assembly, seq machinery, reconnect.

Built on plain pyserial (NOT pyserial-asyncio, which is unreliable on Windows, per
SPEC 3.1). Each port runs one blocking reader thread that opens the device with
`serial.serial_for_url` (so `COM7`, `/dev/ttyACM0`, and `socket://host:port` all
work) and hands received bytes to the event loop via `loop.call_soon_threadsafe`. All
parsing, storage, and response matching happen on the loop; the only thread-shared
state is the serial object (writes guarded by a lock) and the stop event.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from typing import Any

import serial
from serial.tools import list_ports

from . import protocol as p
from .store import Store

BACKOFF_MIN = 0.5
BACKOFF_MAX = 10.0
READ_TIMEOUT = 0.2      # seconds; lets the reader thread notice the stop event
READ_CHUNK = 256
RX_SAFETY_CAP = 4096    # drop a partial line longer than this (SPEC: 4 KB host cap)


class PortError(RuntimeError):
    """Raised when an operation needs a connected port and there is none."""


class _Pending:
    __slots__ = ("seq", "future", "sent_ts")

    def __init__(self, seq: int, future: asyncio.Future, sent_ts: float) -> None:
        self.seq = seq
        self.future = future
        self.sent_ts = sent_ts


class SerialPort:
    def __init__(
        self,
        store: Store,
        loop: asyncio.AbstractEventLoop,
        alias: str,
        device: str | None = None,
        baud: int = 115200,
        serial_number: str | None = None,
    ) -> None:
        self._store = store
        self._loop = loop
        self.alias = alias
        self.device = device
        self.baud = baud
        self.serial_number = serial_number

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._serial: serial.SerialBase | None = None
        self._write_lock = threading.Lock()

        self._rx_bytes = bytearray()
        self._rx_lines: asyncio.Queue[tuple[float, str]] = asyncio.Queue()
        self._consumer_task: asyncio.Task | None = None

        self._seq = 0
        self._cmd_lock = asyncio.Lock()
        self._pending: dict[int, _Pending] = {}
        self._can_decode_failed = False
        self._plot_defs: dict[str, p.PlotDef] = {}  # latest !pd per sid (SPEC 2.5)

        self.connected = False
        self.lines_rx = 0
        self.lines_tx = 0

    # -- lifecycle --------------------------------------------------------------------

    def start(self) -> None:
        self._consumer_task = self._loop.create_task(self._consume())
        self._thread = threading.Thread(
            target=self._reader, name=f"serial-{self.alias}", daemon=True
        )
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        ser = self._serial
        if ser is not None:
            with contextlib.suppress(Exception):
                ser.cancel_read()  # unblock a pending read where supported
        if self._thread is not None:
            await self._loop.run_in_executor(None, self._thread.join, 2.0)
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._consumer_task
        for pend in self._pending.values():
            if not pend.future.done():
                pend.future.cancel()
        self._pending.clear()

    # -- reader thread ----------------------------------------------------------------

    def _resolve_device(self) -> str | None:
        """Resolve the device string, mapping serial_number -> device if requested."""
        if self.serial_number:
            for info in list_ports.comports():
                if info.serial_number == self.serial_number:
                    return info.device
            return None
        return self.device

    def _reader(self) -> None:
        backoff = BACKOFF_MIN
        while not self._stop.is_set():
            dev = self._resolve_device()
            if dev is None:
                self._post(self._on_error, f"no device for serial_number {self.serial_number}")
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, BACKOFF_MAX)
                continue
            try:
                ser = serial.serial_for_url(dev, baudrate=self.baud, timeout=READ_TIMEOUT)
            except Exception as exc:
                self._post(self._on_error, f"open {dev} failed: {exc}")
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, BACKOFF_MAX)
                continue
            self._serial = ser
            self._post(self._on_connect, dev)
            backoff = BACKOFF_MIN
            try:
                while not self._stop.is_set():
                    data = ser.read(READ_CHUNK)
                    if data:
                        self._post(self._on_bytes, time.time(), bytes(data))
            except Exception as exc:
                self._post(self._on_error, f"read error: {exc}")
            finally:
                with contextlib.suppress(Exception):
                    ser.close()
                self._serial = None
                self._post(self._on_disconnect)
            if self._stop.is_set():
                break
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, BACKOFF_MAX)

    def _post(self, fn, *args) -> None:
        """Schedule a loop-side callback from the reader thread."""
        self._loop.call_soon_threadsafe(fn, *args)

    # -- loop-side callbacks ----------------------------------------------------------

    def _on_connect(self, dev: str) -> None:
        self.connected = True
        self._loop.create_task(self._store_sys(f"port {self.alias} connected: {dev}"))

    def _on_disconnect(self) -> None:
        if self.connected:
            self.connected = False
            self._loop.create_task(self._store_sys(f"port {self.alias} disconnected"))

    def _on_error(self, msg: str) -> None:
        self._loop.create_task(self._store_sys(f"port {self.alias}: {msg}"))

    def _on_bytes(self, ts: float, data: bytes) -> None:
        buf = self._rx_bytes
        buf.extend(data)
        if len(buf) > RX_SAFETY_CAP and buf.rfind(b"\n") == -1:
            buf.clear()  # oversized partial line with no terminator: drop it
        while True:
            idx = buf.find(b"\n")
            if idx == -1:
                break
            raw = bytes(buf[:idx])
            del buf[: idx + 1]
            line = raw.decode("ascii", "replace").rstrip("\r")
            self._rx_lines.put_nowait((ts, line))

    async def _consume(self) -> None:
        while True:
            ts, line = await self._rx_lines.get()
            with contextlib.suppress(Exception):
                await self._store_rx_line(ts, line)

    # -- line storage + response matching ---------------------------------------------

    async def _store_rx_line(self, ts: float, line: str) -> dict[str, Any]:
        cls = p.classify(line)
        seq: int | None = None
        can: dict[str, Any] | None = None
        plot: list[dict[str, Any]] | None = None
        resp: p.Response | None = None
        if cls is p.LineClass.RESPONSE:
            chan = "resp"
            with contextlib.suppress(p.ProtocolError):
                resp = p.parse_response(line)
                seq = resp.seq
        elif cls is p.LineClass.EVENT:
            chan = "event"
            if line.startswith("!can"):
                can = self._decode_can(line)
            elif line.startswith("!p"):
                plot = self._decode_plot(line)
        else:
            chan = "debug"
        self.lines_rx += 1
        row = await self._store.add_line(
            ts=ts, port=self.alias, dir="rx", chan=chan, seq=seq, raw=line, can=can, plot=plot
        )
        if chan == "resp" and seq is not None:
            pend = self._pending.pop(seq, None)
            if pend is not None and not pend.future.done():
                pend.future.set_result((resp, row))
        return row

    def _decode_can(self, line: str) -> dict[str, Any] | None:
        frame = p.parse_can_event(line)
        if frame is None:
            if not self._can_decode_failed:
                self._can_decode_failed = True
                self._loop.create_task(
                    self._store_sys(f"port {self.alias}: !can decode failure")
                )
            return None
        self._can_decode_failed = False
        return {
            "tick_ms": frame.tick_ms,
            "can_id": frame.can_id,
            "ext": frame.ext,
            "rtr": frame.rtr,
            "dlc": frame.dlc,
            "data": bytes(frame.data),
        }

    def _decode_plot(self, line: str) -> list[dict[str, Any]] | None:
        """Decode a plot line (SPEC 2.5) into store points, updating the def cache.

        `!pd` refreshes this port's definition cache and carries no points itself. `!ps`
        decodes against the cached def for its sid; `!p` decodes directly. A sample with
        no known def (or a width/count mismatch) yields None, so it is stored as a plain
        event.
        """
        if line.startswith("!pd"):
            definition = p.parse_plot_def(line)
            if definition is not None:
                self._plot_defs[definition.sid] = definition
            return None
        sample: p.PlotSample | None = None
        if line.startswith("!ps"):
            parts = line.split()
            if len(parts) >= 2:
                definition = self._plot_defs.get(parts[1])
                if definition is not None:
                    sample = p.decode_plot_sample(line, definition)
        else:  # ad-hoc !p
            sample = p.parse_plot_adhoc(line)
        if sample is None:
            return None
        return [
            {"tick_ms": sample.tick_ms, "sid": sample.sid, "name": name, "value": value}
            for name, value in sample.points
        ]

    def prime_plot_defs(self) -> None:
        """Rebuild the typed-stream def cache from this port's recently stored `!pd` lines.

        Lets a restarted daemon decode `!ps` samples immediately instead of waiting up to
        2 s for the firmware's next `!pd` rebroadcast (SPEC 9.2).
        """
        rows, _ = self._store.query_lines(
            port=self.alias, chans=["event"], match=r"^!pd ", limit=1000, order="desc"
        )
        for row in rows:  # newest first: the first def seen per sid is the current one
            definition = p.parse_plot_def(row["raw"])
            if definition is not None and definition.sid not in self._plot_defs:
                self._plot_defs[definition.sid] = definition

    async def _store_sys(self, text: str) -> None:
        await self._store.add_line(
            ts=time.time(), port=self.alias, dir="-", chan="sys", seq=None, raw=text
        )

    # -- transmit ---------------------------------------------------------------------

    def _write_bytes(self, data: bytes) -> None:
        ser = self._serial
        if ser is None:
            raise PortError(f"port {self.alias} is not connected")
        with self._write_lock:
            ser.write(data)

    async def send_raw(self, line: str) -> dict[str, Any]:
        """Write one raw line (LF appended), logged as chan cmd, seq null (SPEC /send)."""
        body = line.rstrip("\r\n")
        self._write_bytes((body + "\n").encode("ascii", "replace"))
        self.lines_tx += 1
        return await self._store.add_line(
            ts=time.time(), port=self.alias, dir="tx", chan="cmd", seq=None, raw=body
        )

    async def send_command(self, cmd_text: str, timeout_ms: int) -> dict[str, Any]:
        """Assign a seq, send, and await the matching response or timeout (SPEC /cmd)."""
        async with self._cmd_lock:
            self._seq = p.next_seq(self._seq)
            seq = self._seq
            line = p.format_command(seq, cmd_text)
            fut: asyncio.Future = self._loop.create_future()
            pend = _Pending(seq, fut, time.time())
            self._pending[seq] = pend
            try:
                self._write_bytes((line + "\n").encode("ascii", "replace"))
            except PortError:
                self._pending.pop(seq, None)
                raise
            self.lines_tx += 1
            await self._store.add_line(
                ts=pend.sent_ts, port=self.alias, dir="tx", chan="cmd", seq=seq, raw=line
            )
            try:
                resp, row = await asyncio.wait_for(fut, timeout=timeout_ms / 1000.0)
            except TimeoutError:
                # Mark the seq dead: a late response will be logged but not delivered.
                self._pending.pop(seq, None)
                return {
                    "status": "timeout",
                    "seq": seq,
                    "latency_ms": (time.time() - pend.sent_ts) * 1000.0,
                    "line_id": None,
                }
            latency = (time.time() - pend.sent_ts) * 1000.0
            if resp is None:
                return {
                    "status": "err",
                    "seq": seq,
                    "err_code": p.ERROR_CODES["internal"],
                    "err_name": "internal",
                    "err_detail": "unparseable response",
                    "latency_ms": latency,
                    "line_id": row["id"],
                }
            if resp.ok:
                return {
                    "status": "ok",
                    "seq": seq,
                    "data": resp.data,
                    "latency_ms": latency,
                    "line_id": row["id"],
                }
            return {
                "status": "err",
                "seq": seq,
                "err_code": resp.err_code,
                "err_name": resp.err_name,
                "err_detail": resp.err_detail,
                "latency_ms": latency,
                "line_id": row["id"],
            }

    def status(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "device": self.device if self.device is not None else self.serial_number,
            "baud": self.baud,
            "connected": self.connected,
            "lines_rx": self.lines_rx,
            "lines_tx": self.lines_tx,
        }


class PortManager:
    def __init__(self, store: Store, loop: asyncio.AbstractEventLoop) -> None:
        self._store = store
        self._loop = loop
        self._ports: dict[str, SerialPort] = {}

    async def attach(
        self,
        alias: str,
        device: str | None = None,
        baud: int = 115200,
        serial_number: str | None = None,
    ) -> SerialPort:
        if alias in self._ports:
            await self.detach(alias)  # replacing an alias is how a baud change is done
        port = SerialPort(self._store, self._loop, alias, device, baud, serial_number)
        port.prime_plot_defs()  # recover typed-stream defs from stored lines (SPEC 9.2)
        port.start()
        self._ports[alias] = port
        return port

    async def detach(self, alias: str) -> bool:
        port = self._ports.pop(alias, None)
        if port is None:
            return False
        await port.stop()
        return True

    def get(self, alias: str) -> SerialPort | None:
        return self._ports.get(alias)

    def list(self) -> list[SerialPort]:
        return list(self._ports.values())

    def plot_channel_meta(self) -> dict[str, dict[str, Any]]:
        """Map channel name -> {sid, type, scale, unit} from every port's live def cache.

        Channels are keyed by name globally (SPEC 2.5), so a flat merge is correct; this
        lets /plot/channels annotate stored channels with type and unit.
        """
        meta: dict[str, dict[str, Any]] = {}
        for port in self._ports.values():
            for sid, definition in port._plot_defs.items():
                for chan in definition.channels:
                    meta[chan.name] = {
                        "sid": sid,
                        "type": chan.type,
                        "scale": chan.scale,
                        "unit": chan.unit,
                    }
        return meta

    def resolve(self, alias: str | None) -> SerialPort:
        """Return the named port, or the sole port if `alias` is None (SPEC 4)."""
        if alias is not None:
            port = self._ports.get(alias)
            if port is None:
                raise PortError(f"no such port: {alias}")
            return port
        if len(self._ports) == 1:
            return next(iter(self._ports.values()))
        if not self._ports:
            raise PortError("no ports attached")
        raise PortError("port is ambiguous; specify one")

    async def stop_all(self) -> None:
        for alias in list(self._ports):
            await self.detach(alias)
