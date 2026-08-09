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
import logging
import os
import re
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import serial
from serial.tools import list_ports

from . import link as _link
from . import protocol as p
from .link import Link, is_url_device, open_link
from .store import Store

# Joining a reader thread must never queue behind unrelated work, because detach and
# shutdown both wait on it. Reserving the *default* executor for that was the previous
# rule, and it is unenforceable: `asyncio.to_thread` is `run_in_executor(None, ...)`, so
# every ordinary use of the obvious stdlib idiom silently shares the pool (nine of them
# had accumulated). A private pool nothing else can reach removes the rule instead of
# restating it. Never shut down explicitly: every task submitted here is a join bounded
# at JOIN_TIMEOUT, so the atexit worker join is bounded too.
_join_pool = ThreadPoolExecutor(thread_name_prefix="mcu-join")

JOIN_TIMEOUT = 2.0        # seconds to wait for a reader thread before taking its handle
BACKOFF_MIN = 0.5
BACKOFF_MAX = 5.0
PRESENCE_POLL_S = 0.25    # how often an absent device is checked for while reconnecting
PRESENCE_SETTLE_S = 0.15  # grace after a device reappears, before the first open attempt
# Must be >= PRESENCE_POLL_S, or a polling reader outlives every entry it writes and the
# cache never hits: at 0.2 s against a 0.25 s poll, eight polls cost eight real scans.
COMPORTS_TTL_S = 0.3      # shared cache window over list_ports.comports()
RX_SAFETY_CAP = 4096    # drop a partial line longer than this (SPEC: 4 KB host cap)
MAX_PORTS = 32          # cap concurrent attaches so a flood cannot exhaust threads/sockets
CARRIED_MAX = 256       # detached-alias counters kept for a later re-attach (see _carried)
RX_QUEUE_MAX = 10_000   # bound the loop-side line queue; overflow drops oldest, counted
RX_BATCH_MAX = 1000     # lines handed to the store per consumer pass (one commit each)
# Distinct failure notices recorded per disconnected episode. Reconnect retries repeat
# for as long as the device stays away, so a sys row per attempt buries the capture in
# exactly the state where it most needs reading: an unplugged board overnight is
# thousands of identical "open failed" rows around the lines that matter. The reason is
# recorded once and the retries go to the daemon log instead. A few *distinct* reasons
# still get through, because a changed reason (node back, but permission denied) is news.
MAX_ERR_NOTICES = 3

log = logging.getLogger(__name__)



_comports_lock = threading.Lock()
_comports_cache: tuple[float, list[Any]] = (0.0, [])

# Only a bare device name, so a crafted device string cannot walk out of /sys/class/tty.
_TTY_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _is_absent_uart(device: str) -> bool:
    """True for a kernel serial-core port whose own driver found no hardware behind it.

    The 8250 driver registers a fixed bank of /dev/ttyS* (CONFIG_SERIAL_8250_RUNTIME_UARTS,
    32 on a typical desktop kernel) whether or not anything is wired to them, so `mcu
    devices` on a Linux host listed 32 unopenable ports and buried the one real adapter.
    pyserial intends to hide these - its comports() drops `subsystem == "platform"` for
    exactly this reason - but that check went stale when Linux 6.7 moved these devices onto
    the new `serial-base` bus, so they come through again on a current kernel.

    serial_core publishes port->type in sysfs, where PORT_UNKNOWN (0) is the driver saying
    it probed the slot and found nothing. Only ports carrying that attribute are judged at
    all, which is what makes this safe on embedded hardware: a real on-chip UART (a
    Raspberry Pi's ttyS0 mini-UART, an ARM SoC's ttyS1, a ttyAMA0) reports a nonzero type,
    and USB adapters (ttyUSB, ttyACM) do not use serial_core and have no `type` at all.
    Anything missing or unreadable is kept - never hide a port on a guess.
    """
    if sys.platform != "linux":
        return False
    name = device.rpartition("/")[2]
    if not _TTY_NAME_RE.fullmatch(name):
        return False
    try:
        with open(f"/sys/class/tty/{name}/type", encoding="ascii") as fh:
            return fh.read().strip() == "0"
    except OSError:
        return False   # no such attribute (USB adapters) or unreadable: keep the port


def cached_comports(max_age: float = COMPORTS_TTL_S) -> list[Any]:
    """`list_ports.comports()` behind a short shared TTL.

    Enumerating ports is a sysfs walk on Linux and a setupapi query on Windows (tens of
    milliseconds there). Every reader thread hits it while its device is missing, so one
    scan is shared across all of them for a poll interval instead of each paying its own.
    """
    global _comports_cache
    stamp, ports = _comports_cache
    if stamp and (time.monotonic() - stamp) < max_age:
        return ports
    started = time.monotonic()
    # Slow and blocking (a sysfs walk or a setupapi query): never under the lock.
    scanned = [p for p in list_ports.comports() if not _is_absent_uart(p.device)]
    with _comports_lock:
        # Stamp the scan by when it started, and only install it if it is newer than what
        # is cached. Two threads can scan concurrently; without this the slower one (which
        # saw the older state of the world) overwrites the fresher snapshot under a newer
        # stamp, so a device that just appeared reads as absent for another full TTL.
        if started > _comports_cache[0]:
            _comports_cache = (started, scanned)
    return scanned


def _normalize_com(name: str) -> str:
    r"""Fold a Windows port name so `COM12` and `\\.\COM12` compare equal."""
    return name.upper().removeprefix("\\\\.\\")


class PortError(RuntimeError):
    """Raised when an operation needs a connected port and there is none."""


def validate_device(device: str | None) -> None:
    """`link.validate_device`, as a PortError so the endpoints answer 400 rather than 500."""
    try:
        _link.validate_device(device)
    except ValueError as exc:
        raise PortError(str(exc)) from None


def _response_seq(line: str) -> int | None:
    """Extract the seq integer from a `<...` response line, without validating the rest.

    Returns None if the line is not a response or carries no integer seq token. Used so a
    malformed response that still names a seq can resolve its pending command promptly.
    """
    norm = p.normalize_line(line)
    if not norm.startswith("<"):
        return None
    parts = norm[1:].split()
    if not parts:
        return None
    # Same strictness as protocol.parse_seq_token, minus the lower bound: ASCII decimal
    # digits only and bounded in length, so `<+17 OK` or `<1_7 OK` cannot resolve the
    # pending command for 17, and a 5000-digit token answers None instead of raising the
    # bare ValueError that int() gives past CPython's digit limit.
    tok = parts[0]
    if not p.is_decimal_token(tok):
        return None
    seq = int(tok)
    # Bound it so a hostile token cannot overflow the SQLite INTEGER bind downstream;
    # 0 is kept (stored as-is) even though valid command seqs start at 1.
    return seq if 0 <= seq <= p.SEQ_MAX else None


class _RxPrep:
    """A received line whose write is queued: what `_settle_rx_line` needs to finish it."""

    __slots__ = ("future", "cls", "seq", "resp")

    def __init__(
        self,
        future: asyncio.Future,
        cls: p.LineClass,
        seq: int | None,
        resp: p.Response | None,
    ) -> None:
        self.future = future
        self.cls = cls
        self.seq = seq
        self.resp = resp


class _Pending:
    __slots__ = ("seq", "future", "sent_ts")

    def __init__(self, seq: int, future: asyncio.Future, sent_ts: float) -> None:
        self.seq = seq
        self.future = future
        self.sent_ts = sent_ts


class _EpisodeNotice:
    """Report a recurring condition once per episode, not once per occurrence.

    Five conditions in SerialPort shed data and want a sys row: unterminated garbage, an
    oversized line, rx queue overflow, a line that would not store, a `!can` that would not
    decode. Only the once-per-episode rule lives here; the clear conditions genuinely differ
    (a clean line, a clean burst, a drain below half), so `clear()` is still the caller's.
    """

    def __init__(self) -> None:
        self._armed = True

    @property
    def triggered(self) -> bool:
        """True while an episode is open (something was reported and not yet cleared)."""
        return not self._armed

    def report(self, emit: Callable[[], None]) -> None:
        """Emit only if this occurrence opens the episode."""
        if self._armed:
            self._armed = False
            emit()

    def clear(self) -> None:
        """The condition stopped: the next occurrence starts a new episode."""
        self._armed = True


class SerialPort:
    def __init__(
        self,
        store: Store,
        loop: asyncio.AbstractEventLoop,
        alias: str,
        device: str | None = None,
        baud: int = 115200,
        serial_number: str | None = None,
        open_link_fn: Callable[[str, int], Link] | None = None,
    ) -> None:
        self._store = store
        self._loop = loop
        self.alias = alias
        self.device = device
        self.baud = baud
        self.serial_number = serial_number
        # Accepted rather than created, so a test can drive the reader with an in-memory
        # Link instead of the only transport a real device offers (see link.SourceLink).
        self._open_link = open_link_fn or open_link

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._link: Link | None = None
        self._write_lock = threading.Lock()

        self._rx_bytes = bytearray()
        # Producer (_on_bytes, a loop callback) and consumer (_consume, a task) both run
        # on the event-loop thread, so a plain deque plus a wake Event does the job that
        # an asyncio.Queue would - without its per-item getter/waiter bookkeeping, which
        # is pure overhead on the hottest path in the daemon.
        self._rx_lines: deque[tuple[float, str]] = deque()
        self._rx_wake = asyncio.Event()
        self._consumer_task: asyncio.Task | None = None
        self._queue_overflow = _EpisodeNotice()
        self._bg_tasks: set[asyncio.Task] = set()

        self._seq = 0
        self._cmd_lock = asyncio.Lock()
        self._raw_lock = asyncio.Lock()   # single-flight for send_raw (see send_raw)
        self._pending: dict[int, _Pending] = {}
        self._can_undecodable = _EpisodeNotice()
        self.plot_decoder = p.PlotDecoder()   # typed-stream defs for this port (SPEC 2.5)

        self.connected = False
        self.lines_rx = 0
        self.lines_tx = 0
        self.rx_dropped = 0
        # Once-per-episode sys rows for the ways a port sheds data (see _EpisodeNotice).
        self._unterminated = _EpisodeNotice()
        self._oversized = _EpisodeNotice()
        self._unstorable = _EpisodeNotice()
        # Per-episode failure bookkeeping, all loop-side (see _on_error): reasons already
        # recorded, notices withheld as repeats, and failed open attempts, which the next
        # successful connect reports as a single count.
        self._err_seen: set[str] = set()
        self._err_suppressed = 0
        self._open_failures = 0

    # -- lifecycle --------------------------------------------------------------------

    def start(self) -> None:
        self._consumer_task = self._loop.create_task(self._consume())
        self._thread = threading.Thread(
            target=self._reader, name=f"serial-{self.alias}", daemon=True
        )
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        link = self._link
        if link is not None:
            # Native ports implement this; pyserial's URL handlers (socket://, rfc2217://)
            # do not, and the Link says so with a bool rather than letting a suppressed
            # AttributeError make the call look effective when it never was.
            # Suppressed: a transport that raises from cancel must not cost the join and
            # the handle close below, which are what detach is actually here for.
            with contextlib.suppress(Exception):
                link.cancel_read()
        if self._thread is not None:
            await self._loop.run_in_executor(_join_pool, self._thread.join, JOIN_TIMEOUT)
            if self._thread.is_alive():
                log.warning(
                    "port %s: reader thread did not exit within 2 s; "
                    "closing the device handle from here", self.alias
                )
                # Close it ourselves rather than leaving the handle held by a thread that
                # is not coming back. Windows serial handles are exclusive, so a re-attach
                # of the same COM port would otherwise fail with ERROR_ACCESS_DENIED.
                link = self._link
                if link is not None:
                    # Off the loop: a stalled write holds _write_lock for up to
                    # WRITE_TIMEOUT (send_raw/send_command reach _write_bytes through
                    # to_thread), so acquiring it here froze the whole daemon for 2 s on
                    # any detach, reconnect or shutdown that landed during one.
                    await asyncio.to_thread(self._close_link_locked, link)
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._consumer_task
        # Whatever the consumer never reached is lost here, so count it like every other
        # shedding path: with a store that is behind, a detach or a reconnect threw away
        # up to RX_QUEUE_MAX received lines while /status still reported rx_dropped 0.
        stranded = len(self._rx_lines)
        if stranded:
            self._rx_lines.clear()
            self.rx_dropped += stranded
            self._spawn_sys(
                f"port {self.alias}: dropped {stranded} received "
                f"line{'s' if stranded != 1 else ''} not yet stored at detach",
                stopping=True,
            )
        # A PortError (not cancel()): CancelledError is a BaseException and would blow
        # through send_command's caller instead of resolving as a normal error envelope.
        self._fail_pending(PortError(f"port {self.alias} detached"))
        # Let in-flight sys-row writes land (or cancel them if the store is wedged),
        # so no task dies pending at loop close.
        if self._bg_tasks:
            _done, still_pending = await asyncio.wait(set(self._bg_tasks), timeout=2.0)
            for task in still_pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    # -- reader thread ----------------------------------------------------------------

    def _resolve_device(self) -> str | None:
        """Resolve the device string, mapping serial_number -> device if requested."""
        if self.serial_number:
            for info in cached_comports():
                if info.serial_number == self.serial_number:
                    return info.device
            return None
        return self.device

    def _device_present(self) -> bool:
        """Cheap test for "is the device node there at all", used to gate reconnect polling.

        Deliberately conservative: anything that cannot be tested without actually opening
        something (socket://, rfc2217://, an unset device) answers True, which leaves the
        plain exponential backoff in charge. A failed test answers True for the same
        reason, and because this runs inside the reader's retry wait: an escaping exception
        would kill the thread and end the reconnect attempts for good.
        """
        try:
            if self.serial_number:
                return self._resolve_device() is not None
            dev = self.device
            if is_url_device(dev):
                return True   # nothing to stat; connecting is the only test
            if os.name == "nt":
                # os.path.exists is useless for COM names; match the enumeration instead.
                want = _normalize_com(dev)
                return any(_normalize_com(info.device) == want for info in cached_comports())
            return os.path.exists(dev)
        except Exception as exc:
            log.debug("port %s: presence test failed: %s", self.alias, exc)
            return True

    def _retry_wait(
        self, backoff: float, wait: Callable[[float], bool] | None = None
    ) -> float | None:
        """Wait out one retry interval; return the next backoff, or None if stopping.

        A device that is merely absent (unplugged, or still enumerating after a replug) is
        cheap to test for, so poll for it every PRESENCE_POLL_S and attempt the open the
        moment it is back. Reconnect latency is then a fraction of a second rather than
        however far the backoff had doubled while the device was out - by the time a human
        finished replugging, that was typically the whole BACKOFF_MAX.

        A device that *is* present but will not open (busy, permissions, udev rules still
        landing) gets the full interval instead, since retrying that at 4 Hz only spins.

        `wait` is the sleep, defaulting to the stop event's. Passing one lets the policy
        (which interval, when the backoff restarts) be asserted exactly, instead of
        inferred from elapsed wall clock against a slop that Windows CI has already grazed
        (0.391 s measured against a 0.4 s wait). Not a Clock: one real implementation plus
        a test double is a hypothetical seam, and nothing else here needs the time.
        """
        sleep = wait if wait is not None else self._stop.wait
        if self._device_present():
            if sleep(backoff):
                return None
            return min(backoff * 2, BACKOFF_MAX)
        deadline = time.monotonic() + backoff
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return min(backoff * 2, BACKOFF_MAX)
            if sleep(min(PRESENCE_POLL_S, remaining)):
                return None
            if self._device_present():
                # The node can appear a moment before it is openable, so settle briefly;
                # a reappearance is a fresh situation, so the backoff starts over.
                if sleep(PRESENCE_SETTLE_S):
                    return None
                return BACKOFF_MIN

    def _reader(self) -> None:
        backoff = BACKOFF_MIN
        while not self._stop.is_set():
            dev = None
            try:
                dev = self._resolve_device()
                msg = (
                    f"no device for serial_number {self.serial_number}"
                    if self.serial_number else "no device configured"
                )
            except Exception as exc:
                # Enumerating ports can fail (a setupapi hiccup on Windows, an unreadable
                # sysfs attribute). This call sat outside the retry path, so such a failure
                # killed the reader thread outright: `connected` was already false, so the
                # port read exactly like one still retrying, and it never retried again.
                msg = f"device lookup failed: {exc}"
            if dev is None:
                self._post(self._on_error, msg, True)
                backoff = self._retry_wait(backoff)
                if backoff is None:
                    break
                continue
            try:
                link = self._open_link(dev, self.baud)
            except Exception as exc:
                self._post(self._on_error, f"open {dev} failed: {exc}", True)
                backoff = self._retry_wait(backoff)
                if backoff is None:
                    break
                continue
            # stop() may have given up waiting for this thread while the open was still
            # blocking (a socket:// connect runs to pyserial's 5 s POLL_TIMEOUT, past the
            # 2 s join deadline), in which case it read self._serial while it was still
            # None and closed nothing. Nobody else will close this handle, so do it here.
            if self._stop.is_set():
                with contextlib.suppress(Exception):
                    link.close()
                break
            self._link = link
            self._post(self._on_connect, dev)
            backoff = BACKOFF_MIN
            try:
                while not self._stop.is_set():
                    # Block for the first byte of a burst, timestamp it, then drain whatever
                    # else has already arrived. This stamps each burst at its arrival instead
                    # of lumping up to READ_TIMEOUT of lines under one coarse time, which
                    # matters for host-time plotting of fast streams (SPEC 9.2).
                    data = link.read(1)
                    if not data:
                        continue                    # read timeout: loop to recheck the stop event
                    ts = time.time()
                    buf = bytearray(data)
                    try:
                        link.drain(buf)
                    finally:
                        # Post whatever arrived even when drain raises. At EOF a socket://
                        # port reports readable and pyserial raises from inside drain, with
                        # complete lines already sitting in buf; posting only on success
                        # threw that burst away, so a response received just before the link
                        # dropped was neither delivered nor logged (SPEC 3.2 requires the
                        # logging half). Native ports fail the same way via in_waiting.
                        if buf:
                            self._post(self._on_bytes, ts, bytes(buf))
            except Exception as exc:
                self._post(self._on_error, f"read error: {exc}")
            finally:
                # Under the write lock, so a write blocked inside the driver finishes before
                # the handle goes away (see _write_bytes). cancel_write first, where the
                # transport has it, so a stalled write cannot hold the lock for the full
                # WRITE_TIMEOUT while a disconnect is being processed. Suppressed because
                # this sits outside the reader's own guard: a Link raising here would kill
                # the thread and leak the handle the close below is about to take.
                with contextlib.suppress(Exception):
                    link.cancel_write()
                with self._write_lock:
                    self._link = None
                    with contextlib.suppress(Exception):
                        link.close()
                self._post(self._on_disconnect)
            if self._stop.is_set():
                break
            backoff = self._retry_wait(backoff)
            if backoff is None:
                break

    def _post(self, fn, *args) -> None:
        """Schedule a loop-side callback from the reader thread.

        A reader that outlived stop()'s join deadline can still be running after the loop
        has closed (a socket:// open blocks for pyserial's 5 s POLL_TIMEOUT, longer than
        the 2 s join). call_soon_threadsafe then raises RuntimeError, which would escape
        _reader and kill the thread through the excepthook, leaving its device handle open.
        There is nothing left to deliver to at that point, so drop the callback instead.
        """
        try:
            self._loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:
            if not self._loop.is_closed():
                raise
            log.debug("port %s: loop closed, dropping late callback %s", self.alias, fn)

    # -- loop-side callbacks ----------------------------------------------------------

    def _spawn_sys(self, text: str, stopping: bool = False) -> None:
        """Fire-and-forget a sys-row write, keeping a strong task reference.

        The event loop holds only weak references to tasks, so an unreferenced
        create_task can be garbage-collected mid-flight and the row silently lost.

        `stopping` is for the rows stop() itself files: it has already set the stop event,
        and its _bg_tasks barrier below still bounds and cancels them.
        """
        if self._stop.is_set() and not stopping:
            # Stopping: stop() has (or is about to have) awaited the _bg_tasks barrier;
            # a task spawned after it would die pending at loop close. The reader thread
            # can still post callbacks that land here after the join timeout.
            return
        task = self._loop.create_task(self._store_sys(text))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _on_connect(self, dev: str) -> None:
        self.connected = True
        # Report the retries as one number on the way back up. The individual attempts were
        # withheld (see _on_error), so without this a link that took ten minutes and 300
        # attempts to come back looks exactly like one that reconnected immediately.
        failed = self._open_failures
        self._err_seen.clear()
        self._err_suppressed = 0
        self._open_failures = 0
        note = ""
        if failed:
            note = f" (after {failed} failed attempt{'s' if failed != 1 else ''})"
        self._spawn_sys(f"port {self.alias} connected: {dev}{note}")

    def _on_disconnect(self) -> None:
        if self.connected:
            self.connected = False
            self._spawn_sys(f"port {self.alias} disconnected")
        # Drop any partial line from the old connection. Keeping it glued the trailing
        # fragment onto the first line received after reconnect ("PARTIAL-" + "NEW LINE"),
        # corrupting exactly one line per replug - and if that line was a `<seq` response
        # or a `!can` frame, it was misclassified rather than merely ugly.
        self._rx_bytes.clear()
        # Fail in-flight commands promptly: no response can arrive on a dead link,
        # so callers should not wait out their full timeout.
        self._fail_pending(PortError(f"port {self.alias} disconnected"))

    def _fail_pending(self, exc: Exception) -> None:
        for pend in list(self._pending.values()):
            if not pend.future.done():
                pend.future.set_exception(exc)
        self._pending.clear()

    def _on_error(self, msg: str, attempt: bool = False) -> None:
        """Record a port failure once per distinct reason per disconnected episode.

        `attempt` marks a failed (re)connect attempt, which is what repeats: those are
        counted so the eventual connect row can say how many there were. The latch clears
        in _on_connect, so every episode reports its reason again.
        """
        if attempt:
            self._open_failures += 1
        if msg in self._err_seen or len(self._err_seen) >= MAX_ERR_NOTICES:
            self._err_suppressed += 1
            log.debug(
                "port %s: %s (repeat %d, not recorded)", self.alias, msg, self._err_suppressed
            )
            return
        self._err_seen.add(msg)
        self._spawn_sys(f"port {self.alias}: {msg}")

    def _on_bytes(self, ts: float, data: bytes) -> None:
        buf = self._rx_bytes
        buf.extend(data)
        if b"\n" not in buf:
            if len(buf) > RX_SAFETY_CAP:
                # Oversized partial line with no terminator: drop it, but record the loss.
                # Silently clearing produced a plausible-looking truncated line with nothing
                # anywhere saying bytes had gone missing - the one shedding path that was
                # not instrumented, while the rx-queue overflow beside it counts and logs.
                dropped = len(buf)
                buf.clear()
                self.rx_dropped += 1
                # Latched like the !can decode notice: a target emitting continuous
                # unterminated garbage would otherwise write a sys row per 4 KB. The latch
                # clears as soon as a complete line arrives, so each episode reports once.
                self._unterminated.report(lambda: self._spawn_sys(
                    f"port {self.alias}: dropped {dropped} bytes of an unterminated "
                    f"line longer than the {RX_SAFETY_CAP} byte cap"
                ))
            return
        self._unterminated.clear()
        # Split the whole burst in one pass and keep only the trailing partial line.
        # Cutting one line off the front at a time is quadratic in the burst size (every
        # cut memmoves the rest of the buffer), which became the largest per-line cost
        # once the reader started delivering multi-kilobyte bursts.
        parts = buf.split(b"\n")
        buf[:] = parts.pop()   # whatever follows the last LF is the next line's prefix
        queue = self._rx_lines
        oversized = 0
        for raw in parts:
            # The cap applies to a terminated line too. It used to bound only the partial
            # buffer, so a line that did arrive with its LF was stored whole however long
            # it was (up to a drain's worth past the cap), while the unterminated form of
            # the same garbage was dropped and counted. SPEC 2.1 caps a line at 255 bytes;
            # RX_SAFETY_CAP is the generous host-side bound on top of that.
            if len(raw) > RX_SAFETY_CAP:
                oversized += 1
                continue
            queue.append((ts, raw.decode("ascii", "replace").rstrip("\r")))
        if oversized:
            self.rx_dropped += oversized
            # Latched per episode like the unterminated case beside it; the latch clears
            # on the first burst that carries no oversized line.
            self._oversized.report(lambda: self._spawn_sys(
                f"port {self.alias}: dropped {oversized} received "
                f"line{'s' if oversized != 1 else ''} longer than the "
                f"{RX_SAFETY_CAP} byte cap"
            ))
        else:
            self._oversized.clear()
        excess = len(queue) - RX_QUEUE_MAX
        if excess > 0:
            # Storage cannot keep up: shed the oldest lines, keep the newest, and record
            # the loss once per overflow episode (plus a running counter).
            for _ in range(excess):
                queue.popleft()
            self.rx_dropped += excess
            self._queue_overflow.report(lambda: self._spawn_sys(
                f"port {self.alias}: rx queue overflow, dropping oldest lines"
            ))
        if not self._rx_wake.is_set():
            self._rx_wake.set()

    async def _consume(self) -> None:
        queue = self._rx_lines
        while True:
            if not queue:
                self._rx_wake.clear()
                await self._rx_wake.wait()
                continue
            # Take the whole burst that is already queued, not one line at a time: the
            # store can then commit it as a single batch (see _store_rx_batch).
            batch = [queue.popleft() for _ in range(min(len(queue), RX_BATCH_MAX))]
            if self._queue_overflow.triggered and len(queue) < RX_QUEUE_MAX // 2:
                self._queue_overflow.clear()   # drained: re-arm the overflow sys row
            try:
                await self._store_rx_batch(batch)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The lines are lost to storage; say so instead of dropping them silently.
                log.warning("port %s: failed to store rx lines: %s", self.alias, exc)

    # -- line storage + response matching ---------------------------------------------

    async def _store_rx_batch(self, batch: list[tuple[float, str]]) -> None:
        """Classify, decode and store one burst of received lines.

        Every line is queued with the store before the first await, so the writer drains
        the burst as one batch and spends a single commit on it. Awaiting each line's row
        before queueing the next (the obvious shape) defeats that: it costs one commit and
        one event-loop wakeup per line.

        One bad line costs that line and nothing else. The submit half used to be a list
        comprehension, so a single line that raised abandoned the rest of the burst - up to
        RX_BATCH_MAX lines discarded with no drop counted, only a log warning. A malformed
        line is exactly what a misbehaving target emits, so the resilience belongs here and
        not only in the parsers.
        """
        prepared: list[_RxPrep] = []
        for ts, line in batch:
            try:
                prepared.append(await self._submit_rx_line(ts, line))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._drop_rx_line(exc)
        for prep in prepared:
            try:
                await self._settle_rx_line(prep)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._drop_rx_line(exc)

    def _drop_rx_line(self, exc: Exception) -> None:
        """Account for one received line that could not be classified or stored.

        Counted with the other rx drops (so `/status` and `mcu port list` show it) and
        recorded once per episode, the way the queue overflow and the !can decode failure
        are: a target emitting a bad line every time would otherwise write a sys row per
        line. The latch clears as soon as a line stores cleanly.
        """
        self.rx_dropped += 1
        log.warning("port %s: dropping unstorable rx line: %s", self.alias, exc)
        self._unstorable.report(lambda: self._spawn_sys(
            f"port {self.alias}: dropped an rx line that could not be stored"
        ))

    async def _submit_rx_line(self, ts: float, line: str) -> _RxPrep:
        """Classify and decode one received line, and queue its write (no await of the row)."""
        cls = p.classify(line)
        seq: int | None = None
        can: dict[str, Any] | None = None
        plot: list[dict[str, Any]] | None = None
        resp: p.Response | None = None
        if cls is p.LineClass.RESPONSE:
            chan = "resp"
            # Pull the seq token out first, independent of full response validation: a
            # malformed-but-seq-bearing response (e.g. "<12 GARBAGE") must still pop its
            # pending entry so the caller resolves immediately (as an err) instead of
            # waiting out the whole timeout while holding the cmd lock.
            seq = _response_seq(line)
            with contextlib.suppress(p.ProtocolError):
                resp = p.parse_response(line)
        elif cls is p.LineClass.EVENT:
            chan = "event"
            # Dispatch on the whole first token, not a prefix: `startswith("!can")` also
            # matched a future `!candy on` and pushed it into the CAN decoder, which then
            # logged a spurious "!can decode failure" sys row for a line that was simply
            # not a CAN event. Same for `!p` against `!power`.
            tag = line.split(maxsplit=1)[0]
            if tag == "!can":
                can = self._decode_can(line)
            elif tag in ("!p", "!pd", "!ps"):
                plot = self._decode_plot(line)
            elif tag == "!m" and p.parse_marker(line) is not None:
                # A firmware marker files under chan "marker", so it lands in the same
                # filter and the same full-width divider as `mcu mark` and the session
                # boundaries. An unparseable one stays a generic event, as with !can.
                chan = "marker"
        else:
            chan = "debug"
        self.lines_rx += 1
        fut = await self._store.submit_line(
            ts=ts, port=self.alias, dir="rx", chan=chan, seq=seq, raw=line, can=can, plot=plot
        )
        return _RxPrep(fut, cls, seq, resp)

    async def _settle_rx_line(self, prep: _RxPrep) -> dict[str, Any]:
        """Await a submitted line's stored row and hand it to any command waiting on it."""
        try:
            row = await prep.future
        except Exception as exc:
            # Storing the response failed: resolve the pending command with an error
            # now, instead of leaving the caller to time out with a misleading status.
            if prep.cls is p.LineClass.RESPONSE and prep.seq is not None:
                pend = self._pending.pop(prep.seq, None)
                if pend is not None and not pend.future.done():
                    pend.future.set_exception(
                        PortError(f"response received but storing it failed: {exc}")
                    )
            raise
        self._unstorable.clear()   # a line stored cleanly: re-arm the drop notice
        if prep.cls is p.LineClass.RESPONSE and prep.seq is not None:
            pend = self._pending.pop(prep.seq, None)
            if pend is not None and not pend.future.done():
                pend.future.set_result((prep.resp, row))
        return row

    async def _store_rx_line(self, ts: float, line: str) -> dict[str, Any]:
        """Store one received line: the single-line form of the batch path above."""
        return await self._settle_rx_line(await self._submit_rx_line(ts, line))

    def _decode_can(self, line: str) -> dict[str, Any] | None:
        frame = p.parse_can_event(line)
        if frame is None:
            self._can_undecodable.report(
                lambda: self._spawn_sys(f"port {self.alias}: !can decode failure")
            )
            return None
        self._can_undecodable.clear()
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

        A sample with no known def, or a width mismatch, yields None and is stored as a
        plain event. The grammar itself lives in the decoder (see protocol.PlotDecoder);
        this port owns only the counters and the sys-row latch around it.
        """
        return self.plot_decoder.points(line)

    async def prime_plot_defs(self) -> None:
        """Rebuild the typed-stream def cache from this port's recently stored `!pd` lines.

        Lets a restarted daemon decode `!ps` samples immediately instead of waiting up to
        2 s for the firmware's next `!pd` rebroadcast (SPEC 9.2). The match query scans
        with REGEXP, so it runs off the event loop (query_lines_safe) - a big capture DB
        must not stall ingestion during an attach.
        """
        rows, _ = await self._store.query_lines_safe(
            port=self.alias, chans=["event"], match=r"^!pd ", limit=1000, order="desc"
        )
        for row in rows:  # newest first: the first def seen per sid is the current one
            self.plot_decoder.learn(row["raw"], keep_existing=True)

    async def _store_sys(self, text: str) -> None:
        await self._store.add_line(
            ts=time.time(), port=self.alias, dir="-", chan="sys", seq=None, raw=text
        )

    # -- transmit ---------------------------------------------------------------------

    def _close_link_locked(self, link: Link) -> None:
        """Close a handle under the write lock. Runs on a worker thread, never the loop."""
        with self._write_lock, contextlib.suppress(Exception):
            link.close()

    def _write_bytes(self, data: bytes) -> None:
        # Blocking: pyserial's write waits out flow control up to WRITE_TIMEOUT, so both
        # callers reach it through asyncio.to_thread rather than freezing the loop for 2 s
        # when the target deasserts. Ordering is unaffected: one whole line is written
        # under _write_lock, and send_command's own _cmd_lock still serializes commands.
        #
        # The reader thread can close and null out the serial object concurrently, so the
        # write may hit a closed/broken handle. Translate that into PortError so send_command's
        # cleanup runs (pops the pending seq) and the endpoint returns an envelope, not a 500.
        try:
            # Re-read _link *inside* the lock: the reader's close takes the same lock, so
            # holding it is what guarantees the handle cannot be closed underneath a write
            # already in flight. On Windows the port is opened FILE_FLAG_OVERLAPPED and
            # write() blocks in GetOverlappedResult for up to WRITE_TIMEOUT, while close()
            # frees the OVERLAPPED buffer before clearing is_open - so a lock-free close
            # let the kernel complete into freed memory.
            #
            # Known residual, deliberately not fixed: the *read* side has the same hazard.
            # stop() can close the handle from the loop thread (when the reader outlives its
            # join deadline) while the reader is blocked in ser.read(), which does not hold
            # this lock. Holding it across a read is not an option - it would block every
            # write for a whole READ_TIMEOUT - and the alternative to closing is leaking an
            # exclusive COM handle, which breaks the next attach for good. Reviewed and
            # accepted as the lesser evil; do not re-litigate without a third option.
            with self._write_lock:
                link = self._link
                if link is None:
                    raise PortError(f"port {self.alias} is not connected")
                link.write(data)
        except (serial.SerialException, OSError) as exc:
            raise PortError(f"port {self.alias} write failed: {exc}") from exc

    @staticmethod
    def _encode_wire(body: str) -> bytes:
        """Validate and encode one outgoing line body (LF appended).

        Rejects embedded newlines (which would silently become multiple wire lines
        logged as one row) and non-ASCII text (SPEC 2.1: 7-bit ASCII), instead of
        mangling either silently.
        """
        if "\n" in body or "\r" in body:
            raise PortError("line must not contain embedded newlines")
        if p.is_oversized(body):
            raise PortError(f"line exceeds {p.MAX_LINE_BYTES}-byte limit")
        try:
            return (body + "\n").encode("ascii")
        except UnicodeEncodeError as exc:
            raise PortError("line must be 7-bit ASCII") from exc

    async def send_raw(self, line: str) -> dict[str, Any]:
        """Write one raw line (LF appended), logged as chan cmd, seq null (SPEC /send)."""
        body = line.rstrip("\r\n")
        payload = self._encode_wire(body)
        # One raw write at a time per port. Without it, N concurrent POST /send against a
        # target that has deasserted flow control park N executor workers inside
        # _write_bytes for WRITE_TIMEOUT each. Not _cmd_lock: that one is held across a
        # command's whole round trip, so sharing it would make a raw send wait out an
        # unrelated command's response timeout.
        async with self._raw_lock:
            await asyncio.to_thread(self._write_bytes, payload)
        self.lines_tx += 1
        return await self._store.add_line(
            ts=time.time(), port=self.alias, dir="tx", chan="cmd", seq=None, raw=body
        )

    async def send_command(self, cmd_text: str, timeout_ms: int) -> dict[str, Any]:
        """Assign a seq, send, and await the matching response or timeout (SPEC /cmd)."""
        async with self._cmd_lock:
            self._seq = p.next_seq(self._seq)
            seq = self._seq
            try:
                line = p.format_command(seq, cmd_text)
            except p.ProtocolError as exc:
                # Everything else that rejects an outgoing line raises PortError, which the
                # handlers map to 400 (see _encode_wire on the next line, and _write_bytes).
                # format_command was the one validation on this path still raising
                # ProtocolError, so an empty `cmd` reached FastAPI unmapped: the caller got a
                # 500 for its own bad input, and the daemon log got a traceback for a routine
                # typo - the log a real bug needs, spent on a rejected empty string.
                raise PortError(str(exc)) from exc
            payload = self._encode_wire(line)  # validates length, newlines, ASCII
            fut: asyncio.Future = self._loop.create_future()
            pend = _Pending(seq, fut, time.time())
            self._pending[seq] = pend
            try:
                await asyncio.to_thread(self._write_bytes, payload)
            except PortError:
                self._pending.pop(seq, None)
                raise
            self.lines_tx += 1
            try:
                await self._store.add_line(
                    ts=pend.sent_ts, port=self.alias, dir="tx", chan="cmd", seq=seq, raw=line
                )
            except BaseException:
                # Logging the tx line failed: drop the pending entry so a later
                # disconnect or response does not touch a future nobody awaits.
                # BaseException, so a cancellation delivered at this await (the entry is
                # registered before it) leaks no differently from one at the wait below.
                self._pending.pop(seq, None)
                raise
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
            except BaseException:
                # Same cleanup for the non-timeout exits. CancelledError (client
                # disconnect, uvicorn cancelling the handler, Ctrl-C) is a BaseException
                # and used to escape without popping, leaking one pending entry per
                # cancelled command until the next disconnect cleared them.
                self._pending.pop(seq, None)
                raise
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
            # Non-zero means capture could not keep up and lines were shed (SPEC 3.2).
            # It is counted either way; surfacing it is what makes the loss visible
            # instead of only landing in a sys row nobody reads.
            "rx_dropped": self.rx_dropped,
        }


class PortManager:
    def __init__(
        self,
        store: Store,
        loop: asyncio.AbstractEventLoop,
        open_link_fn: Callable[[str, int], Link] | None = None,
    ) -> None:
        self._store = store
        self._loop = loop
        # How every port this manager builds obtains its transport. `SerialPort` has taken
        # this since the link seam landed, but it stopped here, so nothing above could reach
        # it. `mcuscoped --sim` passes the simulator's; a test passes its own.
        self._open_link_fn = open_link_fn
        self._ports: dict[str, SerialPort] = {}
        # Serialize attach/detach so two concurrent attaches of the same alias cannot both
        # pass the existence check and orphan a reader thread.
        self._lock = asyncio.Lock()
        # Line/drop totals and the command seq survive a detach + reattach of the same
        # alias. Re-attaching builds a fresh SerialPort, so without this `mcu port reconnect`
        # silently reset the counters to zero - erasing the very record of dropped lines
        # that a flaky link is being reconnected because of. The seq rides along for a
        # different reason: an automatic reconnect keeps counting, so restarting an explicit
        # re-attach at 1 was the one path where a late response to a pre-detach command
        # could resolve a *new* command carrying the same seq (SPEC 3.2 wants that response
        # logged, not delivered). Counters are (lines_rx, lines_tx, rx_dropped, seq).
        self._carried: dict[str, tuple[int, int, int, int]] = {}
        # Set by stop_all(): the manager is shutting down and takes no new ports. Checked
        # under the lock in attach, because priming now runs before the lock is taken.
        self._closed = False

    async def attach(
        self,
        alias: str,
        device: str | None = None,
        baud: int = 115200,
        serial_number: str | None = None,
    ) -> SerialPort:
        validate_device(device)  # reject file-write/SSRF device gadgets before opening anything
        port = SerialPort(
            self._store, self._loop, alias, device, baud, serial_number,
            open_link_fn=self._open_link_fn,
        )
        # Prime before the manager lock is taken, and so before the old port is torn down.
        # Before the lock: this is a match query carrying the full 30 s budget, and holding
        # the lock across it queued every attach, detach and stop_all (lifespan shutdown)
        # behind hostile /lines?match= traffic. Before the detach: a StoreError here would
        # otherwise answer 500 *and* leave the alias attached to nothing, the one outcome
        # `POST /ports/{alias}/reconnect` must not produce. Constructing the port is inert;
        # nothing starts until start() below.
        await port.prime_plot_defs()  # recover typed-stream defs (SPEC 9.2)
        async with self._lock:
            # Re-checked here, not before the prime: stop_all() can drain every port while
            # this attach sits in its (unlocked) prime query, and starting the port then
            # left a reader thread and a device handle running with no owner, writing into
            # a store that had already been stopped.
            if self._closed:
                raise PortError(f"port {alias} detached")
            replacing = alias in self._ports
            if not replacing and len(self._ports) >= MAX_PORTS:
                raise PortError(f"too many ports attached (max {MAX_PORTS})")
            if replacing:
                await self._detach_locked(alias)  # replacing an alias is how a baud change is done
            carried = self._carried.get(alias)     # written by the detach above
            if carried is not None:
                port.lines_rx, port.lines_tx, port.rx_dropped, port._seq = carried
            port.start()
            self._ports[alias] = port
            return port

    async def detach(self, alias: str) -> bool:
        async with self._lock:
            return await self._detach_locked(alias)

    async def _detach_locked(self, alias: str) -> bool:
        port = self._ports.pop(alias, None)
        if port is None:
            return False
        try:
            # Snapshot after stop(), not before: stop() adds the lines stranded in the rx
            # queue to rx_dropped, and an earlier snapshot erased exactly those drops on
            # the next attach. In a finally so a raising stop() still carries the counters.
            await port.stop()
        finally:
            # Insertion-ordered, so re-inserting keeps the most recently detached aliases
            # at the end and the oldest fall off first. Bounded because nothing else prunes
            # this: a client looping attach/detach over fresh aliases grew it without limit.
            self._carried.pop(alias, None)
            self._carried[alias] = (port.lines_rx, port.lines_tx, port.rx_dropped, port._seq)
            while len(self._carried) > CARRIED_MAX:
                self._carried.pop(next(iter(self._carried)))
        return True

    def get(self, alias: str) -> SerialPort | None:
        return self._ports.get(alias)

    def list(self) -> list[SerialPort]:
        return list(self._ports.values())

    def plot_channel_meta(self) -> dict[str, dict[str, Any]]:
        """Merge every port's channel metadata (SPEC 9.2).

        Channels are keyed by name globally (SPEC 2.5), so a name declared on two ports
        resolves to the last one merged. Each port's decoder builds its own entries.
        """
        meta: dict[str, dict[str, Any]] = {}
        for port in self._ports.values():
            meta.update(port.plot_decoder.channel_meta())
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
        # Flag first and re-snapshot each round: an attach already past its prime can still
        # take the lock between two detaches, and a `for alias in list(...)` taken up front
        # would leave that port running. The flag makes such an attach refuse instead.
        self._closed = True
        while self._ports:
            await self.detach(next(iter(self._ports)))
