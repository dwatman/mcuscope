"""SerialPort's device writes: their private pool, when they are stamped, and the pending
entry a failed tx row leaves behind (SPEC 3.2)."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import mcu_sim
import pytest
import serial

from mcuscope.serial_link import PortError, SerialPort, _Pending
from mcuscope.store import Store, StoreError
from tests.support import UNOPENABLE, CommitBoom, Stack, add_sys


class _RowStore:
    """Records every row's kwargs; `fail` makes the tx-row insert raise."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.rows: list[dict] = []

    async def add_line(self, **kw):
        if self.fail:
            raise RuntimeError("the row never landed")
        self.rows.append(kw)
        return {"id": len(self.rows), **kw}


class _Wire:
    """A connected link that records when each write (or break) reached it."""

    def __init__(self) -> None:
        self.write_entered: list[float] = []

    def write(self, data: bytes) -> None:
        self.write_entered.append(time.time())

    def send_break(self, seconds: float) -> bool:
        self.write_entered.append(time.time())
        return True


def _port(store) -> tuple[SerialPort, _Wire]:
    port = SerialPort(store, asyncio.get_running_loop(), "board", identify=False)
    wire = _Wire()
    port._link = wire
    return port, wire


@pytest.mark.parametrize("op", ["send_raw", "send_break", "send_command"])
async def test_device_writes_do_not_queue_behind_the_default_executor(op) -> None:
    """Every default-executor worker is held (an export build, say): writes still go out."""
    loop = asyncio.get_running_loop()
    gate, occupied = threading.Event(), threading.Event()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))

    def hog() -> None:
        occupied.set()
        gate.wait(30)

    blocker = loop.run_in_executor(None, hog)
    assert occupied.wait(5), "the default pool's only worker never started"
    try:
        port, wire = _port(_RowStore())
        call = {
            "send_raw": lambda: port.send_raw("ping"),
            "send_break": lambda: port.send_break(1),
            "send_command": lambda: port.send_command("ping", 20),
        }[op]
        await asyncio.wait_for(call(), timeout=10.0)
        assert len(wire.write_entered) == 1
    finally:
        gate.set()
        await blocker


async def _held_write_lock(port: SerialPort, hold_s: float) -> tuple[threading.Thread, list]:
    """Hold the port's write lock from another thread, as an in-flight write does."""
    released: list[float] = []
    taken = threading.Event()

    def hold() -> None:
        with port._write_lock:
            taken.set()
            time.sleep(hold_s)
            released.append(time.time())

    holder = threading.Thread(target=hold)
    holder.start()
    assert taken.wait(5)
    return holder, released


async def test_a_queued_command_is_stamped_when_its_write_begins() -> None:
    store = _RowStore()
    port, wire = _port(store)
    holder, released = await _held_write_lock(port, 0.3)
    result = await port.send_command("ping", 20)
    done = time.time()
    holder.join()
    (row,) = store.rows
    assert released[0] <= row["ts"] <= wire.write_entered[0]
    # Measured from the write, so the wait behind the lock is not reported as latency.
    assert result["status"] == "timeout"
    assert result["latency_ms"] <= (done - released[0]) * 1000.0


async def test_a_raw_send_is_stamped_when_its_write_begins() -> None:
    store = _RowStore()
    port, wire = _port(store)
    holder, released = await _held_write_lock(port, 0.3)
    await port.send_raw("ping")
    holder.join()
    (row,) = store.rows
    assert released[0] <= row["ts"] <= wire.write_entered[0]


async def test_a_failed_tx_row_leaves_no_pending_entry() -> None:
    port, _wire = _port(_RowStore(fail=True))
    with pytest.raises(RuntimeError, match="the row never landed"):
        await port.send_command("ping", 5000)
    assert not port._pending


# -- port-level failure paths ----------------------------------------------------------


def test_disconnect_fails_pending_promptly(tmp_path) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "d.db"))
        await store.start()
        try:
            port = SerialPort(store, asyncio.get_running_loop(), "board")
            port.connected = True
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            port._pending[5] = _Pending(5, fut, time.time())
            port._on_disconnect()
            with pytest.raises(PortError, match="disconnected"):
                await asyncio.wait_for(fut, timeout=1.0)
            assert not port._pending
        finally:
            await store.stop()

    asyncio.run(run())


# -- lost writes are visible, and counting stays off the loop ---------------------------


def test_failed_write_is_counted(tmp_path) -> None:
    # A write the store cannot persist is a line the serial layer already counted as
    # received: with no counter, the loss showed up nowhere but the log.
    async def run() -> None:
        store = Store(str(tmp_path / "we.db"))
        await store.start()
        try:
            assert store.write_errors == 0
            store._conn = CommitBoom(store._conn)
            with pytest.raises(StoreError):
                await add_sys(store, "lost")
            assert store.write_errors == 1
            await add_sys(store, "kept")     # a later success must not reset the count
            assert store.write_errors == 1
        finally:
            await store.stop()

    asyncio.run(run())


# -- C-6: write health read-modify-write against a second writer and the close ----------


class _SlowText(serial.SerialException):
    """A write error whose text is read while the failure is being booked, which is the
    one hook between the health load and its store. Blocks once, bounded."""

    def __init__(self, entered: threading.Event, release: threading.Event) -> None:
        super().__init__("Write timeout")
        self._entered, self._release, self._first = entered, release, True

    def __str__(self) -> str:
        if self._first:
            self._first = False
            self._entered.set()
            self._release.wait(0.5)
        return "Write timeout"


class _FailingLink:
    def __init__(self, first_error: Exception | None = None) -> None:
        self._first = first_error

    def write(self, data: bytes) -> None:
        err, self._first = self._first, None
        raise err or serial.SerialTimeoutException("Write timeout")

    def close(self) -> None:
        pass


def _write_quietly(port: SerialPort) -> None:
    with contextlib.suppress(PortError):
        port._write_bytes(b">1 ping\n")


def test_two_failing_writes_in_two_threads_both_count() -> None:
    port = SerialPort(None, None, "board")
    entered, release = threading.Event(), threading.Event()
    port._link = _FailingLink(_SlowText(entered, release))
    first = threading.Thread(target=_write_quietly, args=(port,))
    first.start()
    assert entered.wait(5)

    def second() -> None:
        _write_quietly(port)
        release.set()

    other = threading.Thread(target=second)
    other.start()
    other.join(5)
    first.join(5)
    assert port.status()["write_failures"] == 2


def test_a_close_during_a_failing_write_ends_the_streak_after_it() -> None:
    """The reader's disconnect path, in its order: locked close, then `_on_disconnect`."""
    port = SerialPort(None, None, "board")
    entered, release = threading.Event(), threading.Event()
    link = _FailingLink(_SlowText(entered, release))
    port._link = link
    writer = threading.Thread(target=_write_quietly, args=(port,))
    writer.start()
    assert entered.wait(5)

    def disconnect() -> None:
        port._close_link_locked(link)
        port._on_disconnect()
        release.set()

    closer = threading.Thread(target=disconnect)
    closer.start()
    closer.join(5)
    writer.join(5)
    st = port.status()
    assert (st["write_failures"], st["write_failing_since"]) == (0, None), st
    assert st["last_write_error"] == "Write timeout"


def test_a_late_disconnect_callback_keeps_the_next_links_streak() -> None:
    """`_on_disconnect` is posted to the loop and can run after the reader has reopened; a
    write that already failed on the new link is a streak of that link, not the old one."""
    port = SerialPort(None, None, "board")
    old = _FailingLink()
    port._link = old
    port._close_link_locked(old)
    port._link = _FailingLink()           # reopened before the loop ran the callback
    _write_quietly(port)
    port._on_disconnect()
    assert port.status()["write_failures"] == 1


def test_cancelling_a_command_does_not_leak_its_pending_entry(tmp_path) -> None:
    """Only TimeoutError popped the seq, so every cancelled /cmd leaked one entry.

    CancelledError is a BaseException (client disconnect, Ctrl-C, uvicorn cancelling the
    handler), so it escaped the cleanup and the entry survived until the next disconnect.
    The sim is told to swallow the first response, so the command is reliably still
    in flight when it is cancelled. identify is off: with the connect ping in the mix the
    "something is pending" wait below was satisfied by the ping's own entry, and on a slow
    Windows loopback its response had not arrived by the time the assertion ran.
    """
    from mcuscope.serial_link import SerialPort

    async def run() -> None:
        stop = threading.Event()
        sock = mcu_sim.open_tcp_listener(0)
        tcp_port = sock.getsockname()[1]
        args = mcu_sim.build_parser().parse_args(["--drop-response", "1"])
        thread = threading.Thread(
            target=mcu_sim.serve_listener, args=(args, sock, stop), daemon=True
        )
        thread.start()
        store = Store(str(tmp_path / "pending.db"))
        await store.start()
        port = SerialPort(
            store, asyncio.get_running_loop(), "board",
            device=f"socket://127.0.0.1:{tcp_port}", identify=False,
        )
        port.start()
        try:
            deadline = time.monotonic() + 5.0
            while not port.connected and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert port.connected, "the port never connected to the simulator"

            task = asyncio.ensure_future(port.send_command("ping", 10_000))
            # The response to this one is swallowed, so it is certainly still pending.
            deadline = time.monotonic() + 5.0
            while not port._pending and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert port._pending, "the command never registered a pending entry"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert port._pending == {}
        finally:
            await port.stop()
            await store.stop()
            stop.set()
            thread.join(timeout=2.0)
            sock.close()

    asyncio.run(run())


def test_cancelling_a_command_mid_write_does_not_leak_its_pending_entry(tmp_path) -> None:
    """The to_thread write made registration-to-wait a cancellation window.

    The write used to be synchronous, so no cancel could land between registering the
    pending entry and the guarded response wait; offloading it opened a window guarded
    only for PortError. Seen as a Windows 3.11/3.12 CI failure of the sibling test above,
    where to_thread dispatch is slow enough for the cancel to land inside the write.
    Deterministic here: the write blocks until released, so the cancel always lands in it.
    """
    from mcuscope.serial_link import SerialPort

    async def run() -> None:
        stop = threading.Event()
        sock = mcu_sim.open_tcp_listener(0)
        tcp_port = sock.getsockname()[1]
        args = mcu_sim.build_parser().parse_args([])
        thread = threading.Thread(
            target=mcu_sim.serve_listener, args=(args, sock, stop), daemon=True
        )
        thread.start()
        store = Store(str(tmp_path / "pending_write.db"))
        await store.start()
        port = SerialPort(
            store, asyncio.get_running_loop(), "board",
            device=f"socket://127.0.0.1:{tcp_port}",
        )
        port.start()
        release = threading.Event()
        try:
            deadline = time.monotonic() + 5.0
            while not port.connected and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert port.connected, "the port never connected to the simulator"

            def blocked_write(payload: bytes) -> float:
                release.wait(timeout=5.0)
                return time.time()

            port._write_bytes = blocked_write
            task = asyncio.ensure_future(port.send_command("ping", 10_000))
            deadline = time.monotonic() + 5.0
            while 2 not in port._pending and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert 2 in port._pending, "the command never registered a pending entry"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert port._pending == {}
        finally:
            release.set()
            await port.stop()
            await store.stop()
            stop.set()
            thread.join(timeout=2.0)
            sock.close()

    asyncio.run(run())


async def test_a_blocking_write_does_not_freeze_the_event_loop(tmp_path) -> None:
    """pyserial's write blocks for up to WRITE_TIMEOUT when the target asserts flow
    control, and it was called inline from send_raw/send_command: the whole daemon (every
    other port, every request, the WebSocket pumps) stopped for up to 2 s per line."""
    from mcuscope.serial_link import SerialPort

    store = Store(str(tmp_path / "wr.db"))
    await store.start()
    port = SerialPort(store, asyncio.get_running_loop(), "board", device=UNOPENABLE)
    ticks = 0
    progressed: list[bool] = []

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    def blocking_write(data: bytes) -> float:
        before = ticks
        time.sleep(0.1)             # the driver holding the write, as flow control does
        progressed.append(ticks > before)
        return time.time()

    port._write_bytes = blocking_write
    tick_task = asyncio.create_task(ticker())
    try:
        await asyncio.wait_for(port.send_raw("ping"), timeout=10.0)
    finally:
        tick_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tick_task
        await store.stop()
    assert progressed == [True], "the loop made no progress while the write was in flight"


async def test_a_stalled_write_does_not_freeze_stop(tmp_path) -> None:
    """stop()'s join-timeout branch closed the handle under _write_lock on the loop thread.

    Since the write moved to a worker (the test above), that lock can be held for a whole
    WRITE_TIMEOUT, so a detach, a reconnect or shutdown landing during a stalled write
    stopped the entire daemon until the driver let go.
    """
    from mcuscope import serial_link

    store = Store(str(tmp_path / "stall.db"))
    await store.start()
    port = serial_link.SerialPort(store, asyncio.get_running_loop(), "board", device=UNOPENABLE)
    closed: list[bool] = []
    by_progress: list[bool] = []
    held = threading.Event()
    release = threading.Event()
    ticks = 0
    target: int | None = None

    class _StuckLink:
        def cancel_read(self) -> bool:
            return False

        def close(self) -> None:
            closed.append(True)

    class _StuckReader:
        """A reader that outlives its join deadline: the branch that closes the handle."""

        def join(self, timeout: float | None = None) -> None:
            nonlocal target
            target = ticks + 200   # loop iterations owed while the close is pending

        def is_alive(self) -> bool:
            return True

    # The write lock is released by loop *progress*, never by the clock: the holder lets go
    # only once the ticker has run 200 more iterations, so a close that blocks the loop
    # never gets its release and falls back to the 5 s backstop, which is the failure.
    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            if target is not None and ticks >= target:
                release.set()
            await asyncio.sleep(0)

    def hold_the_write_lock() -> None:
        with port._write_lock:
            held.set()
            by_progress.append(release.wait(timeout=5.0))

    port._link = _StuckLink()
    port._thread = _StuckReader()
    holder = threading.Thread(target=hold_the_write_lock, daemon=True)
    holder.start()
    assert held.wait(timeout=5.0)
    tick_task = asyncio.create_task(ticker())
    try:
        await port.stop()
    finally:
        release.set()
        holder.join(timeout=5.0)
        tick_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tick_task
        await store.stop()
    assert by_progress == [True], "the loop made no progress while the close waited"
    assert closed == [True], "the handle must still be closed once the lock is free"


async def test_concurrent_raw_sends_are_single_flight_per_port(tmp_path) -> None:
    """send_raw had no serialisation, so N concurrent POST /send against a target that had
    deasserted flow control parked N executor workers inside the write for 2 s each."""
    from mcuscope.serial_link import SerialPort

    store = Store(str(tmp_path / "raw.db"))
    await store.start()
    port = SerialPort(store, asyncio.get_running_loop(), "board", device=UNOPENABLE)
    guard = threading.Lock()
    inflight = 0
    peak = 0

    def slow_write(data: bytes) -> float:
        nonlocal inflight, peak
        with guard:
            inflight += 1
            peak = max(peak, inflight)
        time.sleep(0.05)
        with guard:
            inflight -= 1
        return time.time()

    port._write_bytes = slow_write
    try:
        await asyncio.wait_for(
            asyncio.gather(*(port.send_raw(f"ping {i}") for i in range(4))), timeout=20.0
        )
    finally:
        await store.stop()
    assert peak == 1, f"{peak} raw writes were in flight at once"
    assert port.lines_tx == 4


def test_send_passes_a_line_over_the_command_token_cap(stack: Stack) -> None:
    """/send is the escape hatch for non-monitor firmware (SPEC 3.4).

    SPEC 2.3's 12-token rule is a command-line rule, enforced by format_command on the
    /cmd path only; a raw line to a non-monitor target may be any shape the wire allows,
    so a 13-token /send must reach the port, not die to a monitor-grammar check.
    """
    with httpx.Client(base_url=stack.base_url, timeout=5.0) as c:
        r = c.post("/send", json={"line": " ".join(["word"] * 13)})
    assert r.status_code == 200
