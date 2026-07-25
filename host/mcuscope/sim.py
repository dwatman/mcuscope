#!/usr/bin/env python3
"""MCU simulator: speaks the full monitor protocol (SPEC section 7).

The simulator lets the whole system run with zero hardware: `mcuscoped` attaches to
it exactly as it would a real serial port. It imports `mcuscope.protocol` so the sim
and the daemon share one encoding implementation.

Two transports (SPEC 7):

- Default TCP (cross-platform): listens on 127.0.0.1 and prints its device string
  `socket://127.0.0.1:<port>` on stdout. The daemon attaches with that as `device`.
- `--pty` (POSIX only): opens a pty pair and prints the slave path, for attaching
  exactly like a real `/dev/tty*` device.

Run standalone (installed: the `mcu-sim` console script; source checkout: the
`tools/mcu_sim.py` shim):

    mcu-sim [--tcp-port PORT] [--plot] [--drop-response N] ...
    mcu-sim --pty [--symlink PATH] ...     # POSIX only

It serves until interrupted. `mcuscoped --sim` runs it in-process instead, for a
zero-setup demo of the whole stack.
"""

from __future__ import annotations

import argparse
import math
import os
import select
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass, field

from . import protocol as p

PROJECT_NAME = "sim"

# --- simulated peripheral state ------------------------------------------------------


@dataclass
class SimState:
    """All mutable state of the simulated MCU."""

    start_ns: int
    gpio: dict[str, bool] = field(default_factory=lambda: {"led": False, "en_5v": False})
    eeprom: bytearray = field(default_factory=lambda: bytearray(256))
    temp_raw: int = 0x0640  # ~1600, slowly drifting (fake temperature sensor at 0x48)
    can_rx: int = 0
    can_tx: int = 0
    can_err: int = 0
    can_counter: int = 0
    can_filter_id: int = 0
    can_filter_mask: int = 0
    can_filter_mode: str = "all"  # "all" | "none" | "one"
    alive_count: int = 0

    def tick_ms(self) -> int:
        return (time.monotonic_ns() - self.start_ns) // 1_000_000 & 0xFFFFFFFF


I2C_SCAN_ADDRS = (0x48, 0x50)
SPI_CS_NAMES = ("imu", "flash")
ADC_NAMES = ("vbat",)

# Extra periodic CAN traffic beyond the 0x100 heartbeat, so the decoded CAN view shows a
# realistic multi-id bus (mix of rates, an extended id, and a remote frame). Each tuple is
# (can_id, period_s, ext, rtr, dlc); data frames carry a rolling counter of dlc bytes.
# Most `--flood` lines emitted in one serve pass, so a scheduling stall cannot turn into a
# single enormous write. At the default 10 ms poll interval this bounds the rate at which
# a stalled sim catches up, not the configured rate itself.
FLOOD_MAX_BURST = 5000

CAN_BUS = (
    (0x200, 0.5, False, False, 2),   # 2 Hz, 2-byte payload
    (0x18A, 1.0, True, False, 8),    # 1 Hz, extended id, 8-byte payload
    (0x321, 0.2, False, False, 1),   # 5 Hz, 1-byte payload
    (0x400, 2.0, False, True, 8),    # 0.5 Hz, remote request, dlc 8
)


# --- command handling ----------------------------------------------------------------


class Simulator:
    """Parses commands and produces response/event lines, decoupled from I/O."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.state = SimState(start_ns=time.monotonic_ns())
        self.cmd_count = 0
        # scheduling (all in seconds, monotonic)
        now = time.monotonic()
        self.next_heartbeat = now + 0.1
        self.next_can = {cid: now + period for cid, period, *_ in CAN_BUS}
        self.can_bus_counter = 0
        self.next_alive = now + 2.0
        self.next_plot = now + 0.05
        self.next_plot_def = (now + 5.0) if args.plot_late_def else now
        self.last_plot_def_broadcast = 0.0
        self.pending_echoes: list[tuple[float, p.CanFrame]] = []
        self.garbage_counter = 0
        self.next_flood = now
        self.flood_seq = 0

    # -- top-level dispatch -----------------------------------------------------------

    def handle_line(self, raw: str) -> list[str]:
        """Handle one received line, returning zero or more lines to send back."""
        if p.classify(raw) is not p.LineClass.COMMAND:
            # The monitor silently ignores lines that do not start with '>' (SPEC 2.2).
            return []
        try:
            cmd = p.parse_command(raw)
        except p.ProtocolError:
            # Unparseable seq: stay silent (SPEC 2.1/5.4).
            return []
        self.cmd_count += 1
        if self.args.drop_response and self.cmd_count == self.args.drop_response:
            # Swallow the response to the Nth command to exercise the timeout path.
            return []
        out: list[str] = []
        resp = self.dispatch(cmd)
        out.append(resp)
        return out

    def dispatch(self, cmd: p.Command) -> str:
        seq = cmd.seq
        tokens = cmd.tokens
        name = tokens[0] if tokens else ""
        sub = tokens[1] if len(tokens) > 1 else ""
        rest = tokens[2:]
        try:
            if name == "ping":
                return p.format_response_ok(seq, f"monitor {p.PROTO_VERSION} {PROJECT_NAME}")
            if name == "info":
                up = self.state.tick_ms()
                return p.format_response_ok(seq, f"up={up} rst=por fw=sim-0.1")
            if name == "can":
                return self._can(seq, sub, rest)
            if name == "i2c":
                return self._i2c(seq, sub, rest)
            if name == "spi":
                return self._spi(seq, sub, rest)
            if name == "gpio":
                return self._gpio(seq, sub, rest)
            if name == "adc":
                return self._adc(seq, sub, rest)
            return p.format_response_err(seq, p.ERROR_CODES["badcmd"], f"unknown {name}")
        except p.ProtocolError as exc:
            return p.format_response_err(seq, p.ERROR_CODES["badarg"], str(exc).split(":")[0])

    # -- CAN --------------------------------------------------------------------------

    def _can(self, seq: int, sub: str, rest: tuple[str, ...]) -> str:
        st = self.state
        if sub == "tx":
            frame = p.parse_can_tx_args(rest)
            st.can_tx += 1
            # Echo the transmitted frame back with id+1 after 20 ms (SPEC 7).
            echo = p.CanFrame(
                can_id=frame.can_id + 1,
                data=frame.data,
                ext=frame.ext,
                rtr=frame.rtr,
                dlc=frame.dlc,
            )
            self.pending_echoes.append((time.monotonic() + 0.02, echo))
            return p.format_response_ok(seq)
        if sub == "filter":
            return self._can_filter(seq, rest)
        if sub == "stat":
            state = "active"
            return p.format_response_ok(
                seq, f"rx={st.can_rx} tx={st.can_tx} err={st.can_err} state={state}"
            )
        return p.format_response_err(seq, p.ERROR_CODES["badcmd"], "bad can subcmd")

    def _can_filter(self, seq: int, rest: tuple[str, ...]) -> str:
        st = self.state
        if len(rest) == 1 and rest[0] == "all":
            st.can_filter_mode = "all"
            return p.format_response_ok(seq)
        if len(rest) == 1 and rest[0] == "none":
            st.can_filter_mode = "none"
            return p.format_response_ok(seq)
        if len(rest) >= 2:
            st.can_filter_id = p.parse_hex_int(rest[0])
            st.can_filter_mask = p.parse_hex_int(rest[1])
            st.can_filter_mode = "one"
            return p.format_response_ok(seq)
        return p.format_response_err(seq, p.ERROR_CODES["badarg"], "can filter args")

    def _can_passes_filter(self, can_id: int) -> bool:
        st = self.state
        if st.can_filter_mode == "all":
            return True
        if st.can_filter_mode == "none":
            return False
        return (can_id & st.can_filter_mask) == (st.can_filter_id & st.can_filter_mask)

    # -- I2C --------------------------------------------------------------------------

    def _i2c(self, seq: int, sub: str, rest: tuple[str, ...]) -> str:
        if sub == "scan":
            found = " ".join(f"{a:02X}" for a in I2C_SCAN_ADDRS)
            return p.format_response_ok(seq, found)
        if sub == "wr":
            if len(rest) != 2:
                return p.format_response_err(seq, p.ERROR_CODES["badarg"], "i2c wr args")
            addr = p.parse_hex_int(rest[0])
            data = p.hex_to_bytes(rest[1])
            return self._i2c_write(seq, addr, data)
        if sub == "rd":
            if len(rest) != 2:
                return p.format_response_err(seq, p.ERROR_CODES["badarg"], "i2c rd args")
            addr = p.parse_hex_int(rest[0])
            n = _parse_dec(rest[1], 1, 64)
            return self._i2c_read(seq, addr, None, n)
        if sub == "wrrd":
            if len(rest) != 3:
                return p.format_response_err(seq, p.ERROR_CODES["badarg"], "i2c wrrd args")
            addr = p.parse_hex_int(rest[0])
            wr = p.hex_to_bytes(rest[1])
            n = _parse_dec(rest[2], 1, 64)
            return self._i2c_read(seq, addr, wr, n)
        return p.format_response_err(seq, p.ERROR_CODES["badcmd"], "bad i2c subcmd")

    def _i2c_write(self, seq: int, addr: int, data: bytes) -> str:
        if addr == 0x50:
            # EEPROM: first byte is the offset, rest is written from there.
            if not data:
                return p.format_response_ok(seq)
            off = data[0]
            for i, b in enumerate(data[1:]):
                self.state.eeprom[(off + i) & 0xFF] = b
            return p.format_response_ok(seq)
        if addr == 0x48:
            return p.format_response_ok(seq)  # temp sensor: register pointer write, ignored
        return p.format_response_err(seq, p.ERROR_CODES["nack"], "no device")

    def _i2c_read(self, seq: int, addr: int, wr: bytes | None, n: int) -> str:
        if addr == 0x48:
            # Temperature sensor: reg 0x00 returns two big-endian bytes, slowly drifting.
            self.state.temp_raw = (self.state.temp_raw + 1) & 0xFFFF
            val = struct.pack(">H", self.state.temp_raw)
            data = (val * ((n // 2) + 1))[:n]
            return p.format_response_ok(seq, p.bytes_to_hex(data))
        if addr == 0x50:
            off = wr[0] if wr else 0
            data = bytes(self.state.eeprom[(off + i) & 0xFF] for i in range(n))
            return p.format_response_ok(seq, p.bytes_to_hex(data))
        return p.format_response_err(seq, p.ERROR_CODES["nack"], "no device")

    # -- SPI --------------------------------------------------------------------------

    def _spi(self, seq: int, sub: str, rest: tuple[str, ...]) -> str:
        if sub != "xfer" or len(rest) != 2:
            return p.format_response_err(seq, p.ERROR_CODES["badarg"], "spi xfer args")
        cs, data_hex = rest
        if cs not in SPI_CS_NAMES:
            return p.format_response_err(seq, p.ERROR_CODES["badarg"], f"unknown cs {cs}")
        tx = p.hex_to_bytes(data_hex)
        rx = bytes((~b) & 0xFF for b in tx)  # echo inverted (SPEC 7)
        return p.format_response_ok(seq, p.bytes_to_hex(rx))

    # -- GPIO -------------------------------------------------------------------------

    def _gpio(self, seq: int, sub: str, rest: tuple[str, ...]) -> str:
        st = self.state
        if sub == "set":
            if len(rest) != 2 or rest[0] not in st.gpio or rest[1] not in ("0", "1"):
                return p.format_response_err(seq, p.ERROR_CODES["badarg"], "gpio set args")
            st.gpio[rest[0]] = rest[1] == "1"
            return p.format_response_ok(seq)
        if sub == "get":
            if len(rest) != 1 or rest[0] not in st.gpio:
                return p.format_response_err(seq, p.ERROR_CODES["badarg"], "gpio get args")
            return p.format_response_ok(seq, "1" if st.gpio[rest[0]] else "0")
        return p.format_response_err(seq, p.ERROR_CODES["badcmd"], "bad gpio subcmd")

    # -- ADC --------------------------------------------------------------------------

    def _adc(self, seq: int, sub: str, rest: tuple[str, ...]) -> str:
        if sub != "read" or len(rest) != 1:
            return p.format_response_err(seq, p.ERROR_CODES["badarg"], "adc read args")
        if rest[0] not in ADC_NAMES:
            return p.format_response_err(seq, p.ERROR_CODES["badarg"], f"unknown adc {rest[0]}")
        # Slightly noisy value around 3300 mV.
        tick = self.state.tick_ms()
        mv = 3300 + int(20 * math.sin(tick / 500.0))
        raw = mv * 4095 // 3300
        return p.format_response_ok(seq, f"raw={raw} mv={mv}")

    # -- periodic / asynchronous emissions --------------------------------------------

    def poll_events(self) -> list[str]:
        """Return any asynchronous lines due now (heartbeats, echoes, debug, plot)."""
        out: list[str] = []
        now = time.monotonic()
        st = self.state

        # CAN heartbeat frame, id 0x100 at 10 Hz, counter payload.
        while now >= self.next_heartbeat:
            self.next_heartbeat += 0.1
            st.can_counter = (st.can_counter + 1) & 0xFFFFFFFF
            frame = p.CanFrame(
                can_id=0x100,
                data=struct.pack(">I", st.can_counter),
                tick_ms=st.tick_ms(),
            )
            st.can_rx += 1
            if self._can_passes_filter(frame.can_id):
                out.append(p.format_can_event(frame))

        # Additional periodic CAN traffic (multi-id bus) for a realistic decoded view.
        for cid, period, ext, rtr, dlc in CAN_BUS:
            while now >= self.next_can[cid]:
                self.next_can[cid] += period
                st.can_rx += 1
                if rtr:
                    frame = p.CanFrame(can_id=cid, ext=ext, rtr=True, dlc=dlc, tick_ms=st.tick_ms())
                else:
                    self.can_bus_counter = (self.can_bus_counter + 1) & 0xFFFFFFFFFFFFFFFF
                    data = struct.pack(">Q", self.can_bus_counter)[-dlc:]
                    frame = p.CanFrame(can_id=cid, data=data, ext=ext, tick_ms=st.tick_ms())
                if self._can_passes_filter(cid):
                    out.append(p.format_can_event(frame))

        # Delayed echoes of transmitted frames (id+1 after 20 ms).
        still_pending: list[tuple[float, p.CanFrame]] = []
        for due, frame in self.pending_echoes:
            if now >= due:
                frame.tick_ms = st.tick_ms()
                st.can_rx += 1
                if self._can_passes_filter(frame.can_id):
                    out.append(p.format_can_event(frame))
            else:
                still_pending.append((due, frame))
        self.pending_echoes = still_pending

        # Debug line every 2 s.
        while now >= self.next_alive:
            self.next_alive += 2.0
            st.alive_count += 1
            out.append(f"sim alive n={st.alive_count}")

        if self.args.plot:
            out.extend(self._poll_plot(now))

        if getattr(self.args, "flood", 0):
            out.extend(self._poll_flood(now))

        if self.args.garbage:
            self.garbage_counter += 1
            if self.garbage_counter % 500 == 0:
                out.append("\x01\x02\x7f binary junk \x00 line")

        return out

    def _poll_flood(self, now: float) -> list[str]:
        """Emit plain debug lines at the `--flood` rate, for load and back-pressure testing.

        Catches up on whatever is owed since the last pass rather than emitting one line
        per poll, so the requested rate is met regardless of how often the serve loop runs.
        The per-pass burst is bounded so a scheduling hiccup (or a long stall) cannot turn
        into one enormous write.
        """
        rate = self.args.flood
        if rate <= 0 or now < self.next_flood:
            return []
        owed = min(int((now - self.next_flood) * rate) + 1, FLOOD_MAX_BURST)
        self.next_flood += owed / rate
        out = []
        for _ in range(owed):
            self.flood_seq += 1
            out.append(f"flood line {self.flood_seq} payload=0123456789ABCDEF")
        return out

    def burst_debug(self) -> list[str]:
        """A short burst of debug lines, emitted right after any `gpio set`."""
        return [f"sim gpio-burst {i}" for i in range(3)]

    # -- plot streams (SPEC 2.5) ------------------------------------------------------

    def _poll_plot(self, now: float) -> list[str]:
        out: list[str] = []
        # Typed stream definitions: emit on first eligibility, then rebroadcast every 5 s.
        if now >= self.next_plot_def:
            if now - self.last_plot_def_broadcast >= 5.0 or self.last_plot_def_broadcast == 0.0:
                out.append("!pd 0 tri:s2*0.01:V ramp:u2 ftest:f4")
                out.append("!pd 1 state:u1:=0=IDLE,1=ARMED,2=RUN")
                out.append("!pd 2 gpio:u1:/led,irq,pwm_en")
                self.last_plot_def_broadcast = now
        # Samples at 20 Hz for both the ad-hoc and typed streams.
        while now >= self.next_plot:
            self.next_plot += 0.05
            tick = self.state.tick_ms()
            phase = tick / 1000.0
            # Ad-hoc !p: sine and noisy (sine plus small deterministic wobble).
            sine = math.sin(phase * 2 * math.pi)
            noisy = sine + 0.05 * math.sin(phase * 37.0)
            out.append(f"!p {tick} sine={sine:.4f} noisy={noisy:.4f}")
            # Typed !ps samples always flow. With --plot-late-def the !pd above is held
            # back 5 s, so these early samples are undecodable at the consumer (SPEC 7).
            tri = int(2000 * _triangle(phase))          # s2, scaled by 0.01 -> +-20 V
            ramp = tick & 0xFFFF                          # u2
            ftest = math.sin(phase * 0.5 * 2 * math.pi)  # f4, slow sine
            packed = struct.pack("<hHf", _clip_s16(tri), ramp, ftest)
            out.append(_format_typed_sample("0", tick, packed, ("h", "H", "f")))
            # Enum state machine (stream 1): step 0->1->2->0 every ~1 s.
            state = (tick // 1000) % 3
            out.append(_format_typed_sample("1", tick, struct.pack("<B", state), ("B",)))
            # Packed bits (stream 2): led ~1 Hz, irq ~0.7 Hz, pwm_en fast.
            bits = ((tick // 500) & 1) | (((tick // 1500) & 1) << 1) | (((tick // 200) & 1) << 2)
            out.append(_format_typed_sample("2", tick, struct.pack("<B", bits), ("B",)))
        return out


# --- typed-sample encoding (SPEC 2.5) ------------------------------------------------

# Struct format char -> (byte width). Fields are packed little-endian on the wire from
# the MCU's struct, then re-emitted as big-endian fixed-width hex.
_FIELD_WIDTH = {"b": 1, "B": 1, "h": 2, "H": 2, "i": 4, "I": 4, "f": 4}


def _format_typed_sample(sid: str, tick: int, packed: bytes, fmt: tuple[str, ...]) -> str:
    """Encode one `!ps` line: big-endian fixed-width uppercase hex, comma separated.

    `packed` is a little-endian struct (as the MCU would hold it); each field is
    re-read and emitted big-endian, matching SPEC 2.5's natural reading order.
    """
    vals: list[str] = []
    off = 0
    for ch in fmt:
        width = _FIELD_WIDTH[ch]
        field_le = packed[off : off + width]
        off += width
        be = field_le[::-1]  # little-endian struct bytes reversed to big-endian
        vals.append(be.hex().upper())
    return f"!ps {sid} {tick:X} " + ",".join(vals)


def _triangle(phase: float) -> float:
    """A triangle wave in [-1, 1] with period 1.0."""
    frac = phase - math.floor(phase)
    return 4.0 * abs(frac - 0.5) - 1.0


def _clip_s16(v: int) -> int:
    return max(-32768, min(32767, v))


def _parse_dec(text: str, lo: int, hi: int) -> int:
    if not text.isdigit():
        raise p.ProtocolError(f"expected decimal in {lo}..{hi}")
    val = int(text)
    if not (lo <= val <= hi):
        raise p.ProtocolError(f"value {val} out of range {lo}..{hi}")
    return val


# --- connection serving (shared by both transports) ----------------------------------


def _process_incoming(sim: Simulator, rx: bytearray, chunk: bytes) -> list[str]:
    """Feed received bytes into the sim, returning the lines to send back.

    Detects a `gpio set` command and appends the debug burst that SPEC 7 requires
    right after one, to exercise line interleaving.
    """
    out: list[str] = []
    rx.extend(chunk)
    while b"\n" in rx:
        raw, _, remainder = rx.partition(b"\n")
        rx[:] = remainder
        line = raw.decode("ascii", "replace")
        was_gpio_set = line.startswith(">") and " gpio set " in f" {line} "
        out.extend(sim.handle_line(line))
        if was_gpio_set:
            out.extend(sim.burst_debug())
    return out


# --- TCP transport (default, cross-platform) -----------------------------------------


def open_tcp_listener(port: int) -> socket.socket:
    """Bind a TCP listener on 127.0.0.1:port (port 0 picks an ephemeral port)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    return srv


def serve_listener(
    args: argparse.Namespace,
    srv: socket.socket,
    stop: threading.Event | None = None,
) -> None:
    """Accept one client at a time and serve it, looping back to accept on disconnect.

    A serial port is point-to-point, so only one connection is served at once. When
    the daemon drops and reconnects, the next accept picks it up, which is what the
    phase 2 reconnect test exercises.
    """
    srv.settimeout(0.5)
    while stop is None or not stop.is_set():
        try:
            conn, _ = srv.accept()
        except TimeoutError:
            continue
        except OSError:
            break
        with conn:
            _serve_socket_client(args, conn, stop)


def _serve_socket_client(
    args: argparse.Namespace,
    conn: socket.socket,
    stop: threading.Event | None,
) -> None:
    sim = Simulator(args)
    conn.setblocking(False)
    rx = bytearray()
    while stop is None or not stop.is_set():
        readable, _, _ = select.select([conn], [], [], 0.01)
        if readable:
            try:
                chunk = conn.recv(4096)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                return
            if not chunk:
                return  # client closed the connection
            if not _sock_send_lines(conn, _process_incoming(sim, rx, chunk)):
                return
        if not _sock_send_lines(conn, sim.poll_events()):
            return


def _sock_send_lines(conn: socket.socket, lines: list[str]) -> bool:
    """Write a whole pass's output in one call. Returns False once the peer is gone.

    One `sendall` per line is a syscall per line, which shows up as soon as the sim emits
    at any rate (`--flood`); the lines are due at the same instant anyway.
    """
    if not lines:
        return True
    try:
        conn.sendall(("\n".join(lines) + "\n").encode("ascii", "replace"))
        return True
    except OSError:
        return False


def serve_tcp(args: argparse.Namespace) -> int:
    srv = open_tcp_listener(args.tcp_port)
    port = srv.getsockname()[1]
    # The device string the daemon attaches to; also directly usable by test scripts.
    print(f"socket://127.0.0.1:{port}", flush=True)
    try:
        serve_listener(args, srv)
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()
    return 0


# --- pty transport (POSIX only, opt-in) ----------------------------------------------


def serve_pty(args: argparse.Namespace) -> int:
    if os.name == "nt":
        print("--pty requires a POSIX pty and is not available on Windows.", file=sys.stderr)
        return 2

    import pty  # POSIX only; imported here so the module still imports on Windows.

    master, slave = pty.openpty()
    slave_path = os.ttyname(slave)
    print(slave_path, flush=True)
    if args.symlink:
        _make_symlink(args.symlink, slave_path)

    sim = Simulator(args)
    rx = bytearray()

    def write_lines(lines: list[str]) -> None:
        if lines:
            os.write(master, ("\n".join(lines) + "\n").encode("ascii", "replace"))

    try:
        while True:
            readable, _, _ = select.select([master], [], [], 0.01)
            if readable:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                write_lines(_process_incoming(sim, rx, chunk))
            write_lines(sim.poll_events())
    except KeyboardInterrupt:
        pass
    finally:
        os.close(master)
        os.close(slave)
        if args.symlink and os.path.islink(args.symlink):
            try:
                os.remove(args.symlink)
            except OSError:
                pass
    return 0


def _make_symlink(link: str, target: str) -> None:
    try:
        if os.path.islink(link) or os.path.exists(link):
            os.remove(link)
        os.symlink(target, link)
    except OSError as exc:
        print(f"warning: could not create symlink {link}: {exc}", file=sys.stderr)


# --- entry point ---------------------------------------------------------------------


def serve(args: argparse.Namespace) -> int:
    if args.pty:
        return serve_pty(args)
    return serve_tcp(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcu_sim",
        description="Simulated MCU speaking the monitor protocol over TCP or a pty (SPEC 7).",
    )
    parser.add_argument(
        "--tcp-port",
        type=int,
        default=9900,
        metavar="PORT",
        help="TCP port to listen on (default 9900; 0 picks an ephemeral port).",
    )
    parser.add_argument(
        "--pty",
        action="store_true",
        help="POSIX only: serve over a pty instead of TCP, printing the slave path.",
    )
    parser.add_argument(
        "--symlink",
        metavar="PATH",
        help="With --pty: create a stable symlink to the pty slave path.",
    )
    parser.add_argument(
        "--drop-response",
        type=int,
        default=0,
        metavar="N",
        help="Swallow the response to the Nth command (exercises the timeout path).",
    )
    parser.add_argument(
        "--garbage",
        action="store_true",
        help="Occasionally emit binary junk lines.",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Emit ad-hoc !p and typed !pd/!ps plot streams at 20 Hz.",
    )
    parser.add_argument(
        "--plot-late-def",
        action="store_true",
        help="Delay the first !pd by 5 s (tests the undecodable-sample path).",
    )
    parser.add_argument(
        "--flood",
        type=int,
        default=0,
        metavar="LINES_PER_S",
        help="Emit this many extra debug lines per second (0 = off). For load testing "
        "the capture path and the web UI's high-rate behaviour.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return serve(args)


if __name__ == "__main__":
    raise SystemExit(main())
