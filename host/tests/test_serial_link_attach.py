"""PortManager.attach(replaces=...): the reconnect path must not bring back an
alias that a detach removed while the attach was priming."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import gc
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import serial

from mcuscope import serial_link
from mcuscope.link import Link, SourceLink
from mcuscope.serial_link import JOIN_TIMEOUT, PortError, PortManager, SerialPort
from mcuscope.store import Store
from tests.support import UNOPENABLE, until


def _never_opens(dev: str, baud: int):
    raise OSError("no such device")


async def test_a_reconnect_racing_a_detach_does_not_reattach(tmp_path, monkeypatch) -> None:
    store = Store(str(tmp_path / "attach.db"))
    await store.start()
    mgr = PortManager(store, asyncio.get_running_loop(), open_link_fn=_never_opens)
    try:
        await mgr.attach("r", "/dev/mcuscope-nonexistent", identify=False)
        entered, gate = asyncio.Event(), asyncio.Event()

        async def slow_prime(self) -> None:
            entered.set()
            await gate.wait()

        monkeypatch.setattr(SerialPort, "prime_plot_defs", slow_prime)

        # Positive control: with the alias still there, the reconnect replaces it.
        old = mgr.get("r")
        gate.set()
        new = await mgr.attach("r", "/dev/mcuscope-nonexistent", identify=False, replaces=old)
        assert new is not old and mgr.get("r") is new

        gate.clear()
        entered.clear()
        reconnect = asyncio.create_task(
            mgr.attach("r", "/dev/mcuscope-nonexistent", identify=False, replaces=new)
        )
        await entered.wait()
        assert await mgr.detach("r")
        gate.set()
        with pytest.raises(PortError, match=r"^no such port: r$"):
            await reconnect
        assert mgr.get("r") is None
    finally:
        await mgr.stop_all()
        await store.stop()


OLD, NEW = "/dev/mcuscope-old", "/dev/mcuscope-new"


def _never_opens(dev: str, baud: int):
    raise OSError("no such device")


class _Once:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def feed(self, data: bytes) -> bytes:
        return b""

    def poll(self) -> bytes:
        data, self.data = self.data, b""
        return data


@pytest.fixture
async def mgr(tmp_path):
    store = Store(str(tmp_path / "race.db"))
    await store.start()
    manager = PortManager(store, asyncio.get_running_loop(), open_link_fn=_never_opens)
    try:
        yield manager
    finally:
        await manager.stop_all()
        await store.stop()


@pytest.fixture
def old_prime_gate(monkeypatch):
    """Only a port on OLD blocks in its prime; any other attach passes straight through."""
    entered, gate = asyncio.Event(), asyncio.Event()

    async def slow_prime(self) -> None:
        if self.device == OLD:
            entered.set()
            await gate.wait()

    monkeypatch.setattr(SerialPort, "prime_plot_defs", slow_prime)
    return entered, gate


async def test_a_reconnect_does_not_undo_a_reattach_made_during_its_prime(
    mgr, old_prime_gate
) -> None:
    entered, gate = old_prime_gate
    gate.set()
    old = await mgr.attach("r", OLD, identify=False)
    gate.clear()
    entered.clear()
    reconnect = asyncio.create_task(mgr.attach("r", OLD, identify=False, replaces=old))
    await entered.wait()
    moved = await mgr.attach("r", NEW, identify=False)
    gate.set()
    with pytest.raises(PortError, match=r"^port r was re-attached during the reconnect$"):
        await reconnect
    assert mgr.get("r") is moved and moved.device == NEW


async def test_a_reconnect_does_not_undo_a_disconnect_made_during_its_prime(
    mgr, old_prime_gate
) -> None:
    entered, gate = old_prime_gate
    gate.set()
    old = await mgr.attach("r", OLD, identify=False)
    gate.clear()
    entered.clear()
    reconnect = asyncio.create_task(mgr.attach("r", OLD, identify=False, replaces=old))
    await entered.wait()
    assert await mgr.hold("r")
    gate.set()
    with pytest.raises(PortError, match=r"^port r was disconnected during the reconnect$"):
        await reconnect
    assert mgr.get("r") is old and old.held


async def test_a_reconnect_resumes_a_port_held_before_it_began(mgr, old_prime_gate) -> None:
    """Positive control for the disconnect check: resuming a held port is what reconnect is."""
    _entered, gate = old_prime_gate
    gate.set()
    old = await mgr.attach("r", OLD, identify=False)
    assert await mgr.hold("r")
    new = await mgr.attach("r", OLD, identify=False, replaces=old)
    assert mgr.get("r") is new and not new.held


def _sys_rows(store: Store) -> list[str]:
    rows, _more = store.query_lines(chans=["sys"], limit=1000, order="asc")
    return [r["raw"] for r in rows]


async def _manager_with_a_partial(tmp_path) -> tuple[Store, PortManager]:
    store = Store(str(tmp_path / "cause.db"))
    await store.start()
    mgr = PortManager(store, asyncio.get_running_loop(),
                      open_link_fn=lambda dev, baud: SourceLink(_Once(b"whole\nPART")))
    port = await mgr.attach("board", OLD, identify=False)
    deadline = time.monotonic() + 5.0
    while port.lines_rx < 1 or not port._rx_bytes:
        assert time.monotonic() < deadline, "the burst never arrived"
        await asyncio.sleep(0.01)
    return store, mgr


@pytest.mark.parametrize(("stop", "cause"), [
    (lambda mgr: mgr.hold("board"), "disconnect"),
    (lambda mgr: mgr.attach("board", OLD, identify=False), "re-attach"),
    (lambda mgr: mgr.detach("board"), "detach"),
])
async def test_the_dropped_partial_row_names_what_stopped_the_port(
    tmp_path, stop, cause
) -> None:
    store, mgr = await _manager_with_a_partial(tmp_path)
    try:
        await stop(mgr)
        await mgr.stop_all()
        rows = [r for r in _sys_rows(store) if "partial line" in r]
        assert rows[0] == f"port board: dropped a 4-byte partial line at {cause}"
    finally:
        await mgr.stop_all()
        await store.stop()


# -- C-5 and C-4: what a reattach of the same alias keeps --------------------------------


class _Pipe:
    """A far end emitting what the test queues; `fail_writes` makes every write time out."""

    def __init__(self) -> None:
        self.out: collections.deque[bytes] = collections.deque()
        self.fail_writes = False

    def feed(self, data: bytes) -> bytes:
        if self.fail_writes:
            raise serial.SerialTimeoutException("Write timeout")
        return b""

    def poll(self) -> bytes:
        chunks = b""
        while self.out:
            chunks += self.out.popleft()
        return chunks


async def _manager(tmp_path, pipe: _Pipe):
    store = Store(str(tmp_path / "link.db"))
    await store.start()
    mgr = serial_link.PortManager(
        store, asyncio.get_running_loop(),
        open_link_fn=lambda dev, baud: SourceLink(pipe, device=dev),
    )
    return store, mgr


@pytest.mark.parametrize("path", ["replace", "detach then attach"])
async def test_a_reattach_keeps_the_last_write_error(tmp_path, path) -> None:
    pipe = _Pipe()
    store, mgr = await _manager(tmp_path, pipe)
    try:
        port = await mgr.attach("b", device="sim://x", identify=False)
        await until(lambda: port.connected)
        pipe.fail_writes = True
        with pytest.raises(PortError, match="Write timeout"):
            await port.send_raw("x")
        before = port.status()
        assert before["write_failures"] == 1
        pipe.fail_writes = False
        if path != "replace":
            await mgr.detach("b")
        again = await mgr.attach("b", device="sim://x", identify=False)
        st = again.status()
        assert st["last_write_error"] == "Write timeout"
        assert st["last_write_error_ts"] == before["last_write_error_ts"]
        # The streak ends with the attachment, as it does with a disconnect.
        assert (st["write_failures"], st["write_failing_since"]) == (0, None)
    finally:
        await mgr.stop_all()
        await store.stop()


async def test_a_reattach_decodes_with_a_def_stored_after_its_prime(tmp_path, monkeypatch) -> None:
    """The old port keeps capturing while the new one primes; a redefinition it learns in
    that window is newer than anything the prime read."""
    pipe = _Pipe()
    store, mgr = await _manager(tmp_path, pipe)
    try:
        old = await mgr.attach("b", device="sim://x", identify=False)
        pipe.out.append(b"!pd 0 x:u2\n")
        await until(lambda: old.plot_decoder.definition("0") is not None)

        prime = SerialPort.prime_plot_defs

        async def prime_then_redefine(self) -> None:
            await prime(self)
            pipe.out.append(b"!pd 0 x:s2*0.5\n")
            await until(lambda: old.plot_decoder.definition("0").channels[0].type == "s2")

        monkeypatch.setattr(SerialPort, "prime_plot_defs", prime_then_redefine)
        new = await mgr.attach("b", device="sim://x", identify=False)
        monkeypatch.setattr(SerialPort, "prime_plot_defs", prime)
        assert new is not old

        pipe.out.append(b"!ps 0 10 FFFE\n")

        def values() -> list[float]:
            return [r[0] for r in store._conn.execute(
                "SELECT value FROM plot_points WHERE name = 'x'")]

        await until(values)
        assert values() == [-1.0], "decoded with the definition the prime read"
    finally:
        await mgr.stop_all()
        await store.stop()


def test_detach_and_reattach_carries_the_tx_counter() -> None:
    """lines_rx and rx_dropped survived a reconnect; lines_tx silently reset to zero."""
    from mcuscope.serial_link import PortManager

    async def run() -> None:
        store = Store(":memory:")
        await store.start()
        mgr = PortManager(store, asyncio.get_running_loop())
        try:
            port = await mgr.attach("t", device=UNOPENABLE)
            port.lines_rx, port.lines_tx, port.rx_dropped = 100, 5, 2
            await mgr.detach("t")
            again = await mgr.attach("t", device=UNOPENABLE)
            assert (again.lines_rx, again.lines_tx, again.rx_dropped) == (100, 5, 2)
            await mgr.detach("t")
        finally:
            await store.stop()

    asyncio.run(run())


async def test_detach_carries_the_drops_stop_itself_counted(tmp_path) -> None:
    """The carried snapshot was taken before port.stop(), which is what counts the lines
    stranded in the rx queue: those drops vanished on the next attach, erasing the record
    a flaky link is being reconnected because of."""
    from mcuscope.serial_link import PortManager

    store = Store(str(tmp_path / "carry.db"))
    await store.start()
    pm = PortManager(store, asyncio.get_running_loop())
    try:
        port = await pm.attach("p1", UNOPENABLE)
        port._consumer_task.cancel()   # stands in for "the store is behind"
        with contextlib.suppress(asyncio.CancelledError):
            await port._consumer_task
        port._rx_lines.extend((time.time(), f"line {i}") for i in range(7))
        port.rx_dropped = 3
        await pm.detach("p1")
        assert port.rx_dropped == 10    # 3 already counted + 7 stranded, counted by stop()
        port2 = await pm.attach("p1", UNOPENABLE)
        assert port2.rx_dropped == 10
    finally:
        await pm.stop_all()
        await store.stop()


async def test_attach_does_not_hold_the_manager_lock_across_the_prime_query(
    tmp_path, monkeypatch
) -> None:
    """prime_plot_defs is a match query carrying the full 30 s budget. Run under the
    manager lock, hostile /lines?match= traffic queued every attach, detach and the
    stop_all that lifespan shutdown depends on."""
    from mcuscope import serial_link

    store = Store(str(tmp_path / "prime.db"))
    await store.start()
    pm = serial_link.PortManager(store, asyncio.get_running_loop())
    gate = asyncio.Event()

    async def slow_prime(self) -> None:
        await gate.wait()

    try:
        await pm.attach("a", UNOPENABLE)
        monkeypatch.setattr(serial_link.SerialPort, "prime_plot_defs", slow_prime)
        attaching = asyncio.create_task(pm.attach("b", UNOPENABLE))
        await asyncio.sleep(0)  # let the attach reach the prime
        assert await asyncio.wait_for(pm.detach("a"), timeout=5.0) is True
        gate.set()
        await asyncio.wait_for(attaching, timeout=5.0)
    finally:
        gate.set()
        await pm.stop_all()
        await store.stop()


async def test_an_attach_racing_stop_all_does_not_start_an_orphan_port(
    tmp_path, monkeypatch
) -> None:
    """The prime query runs before the manager lock, so stop_all could drain every port
    while an attach sat in it; the attach then started a reader thread and a device handle
    with no owner, against a store that was about to stop."""
    from mcuscope import serial_link

    store = Store(str(tmp_path / "race.db"))
    await store.start()
    pm = serial_link.PortManager(store, asyncio.get_running_loop())
    gate = asyncio.Event()
    started: list[str] = []

    async def slow_prime(self) -> None:
        await gate.wait()

    real_start = serial_link.SerialPort.start

    def counting_start(self) -> None:
        started.append(self.alias)
        real_start(self)

    monkeypatch.setattr(serial_link.SerialPort, "prime_plot_defs", slow_prime)
    monkeypatch.setattr(serial_link.SerialPort, "start", counting_start)
    try:
        attaching = asyncio.create_task(pm.attach("b", UNOPENABLE))
        await asyncio.sleep(0)                       # let the attach reach the prime
        await asyncio.wait_for(pm.stop_all(), timeout=5.0)
        gate.set()
        with pytest.raises(serial_link.PortError):
            await asyncio.wait_for(attaching, timeout=5.0)
    finally:
        gate.set()
        await store.stop()
    assert pm.list() == []
    assert started == [], "a port started after stop_all had drained the manager"


async def test_an_unraced_attach_reaches_the_start_the_race_test_counts(
    tmp_path, monkeypatch
) -> None:
    """The positive control for the test above: `started == []` means something only if an
    attach of the same device, left alone, does call the patched SerialPort.start."""
    from mcuscope import serial_link

    store = Store(str(tmp_path / "race.db"))
    await store.start()
    pm = serial_link.PortManager(store, asyncio.get_running_loop())
    started: list[str] = []
    real_start = serial_link.SerialPort.start

    def counting_start(self) -> None:
        started.append(self.alias)
        real_start(self)

    monkeypatch.setattr(serial_link.SerialPort, "start", counting_start)
    try:
        await pm.attach("b", UNOPENABLE)
        await pm.stop_all()
    finally:
        await store.stop()
    assert started == ["b"]


class _Wedged(Link):
    """A link whose read blocks until it is closed and which cannot be cancelled.

    This is what makes `stop()` take its handle-close branch: the reader thread is still
    inside `read()` when the join deadline passes, so the loop closes the handle itself.
    """

    def __init__(self) -> None:
        self.released = threading.Event()
        self.closed = threading.Event()

    def read(self, n: int) -> bytes:
        self.released.wait(30)
        return b""

    def drain(self, buf: bytearray) -> None:
        pass

    def write(self, data: bytes) -> None:
        pass

    def close(self) -> None:
        self.closed.set()
        self.released.set()

    def cancel_read(self) -> bool:
        return False   # pyserial's URL handlers cannot cancel either

    def cancel_write(self) -> bool:
        return False


def test_detach_handle_close_does_not_queue_behind_the_default_executor(tmp_path) -> None:
    """The close that frees an exclusive handle belongs on `_join_pool` like the join.

    Same starvation shape as the join test: the default pool is reduced to a single
    occupied worker, so the close cannot pass on spare capacity. With it back on
    `asyncio.to_thread` the stop never completes and the next attach of the same COM port
    fails with ERROR_ACCESS_DENIED against a handle this daemon still holds.
    """

    async def run() -> None:
        loop = asyncio.get_running_loop()
        release = threading.Event()
        occupied = threading.Event()
        pool = ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(pool)

        def hog() -> None:
            occupied.set()
            release.wait(30)

        hogged = loop.run_in_executor(None, hog)
        assert occupied.wait(5), "the default pool's only worker never started"

        link = _Wedged()
        store = Store(str(tmp_path / "close.db"))
        await store.start()
        port = SerialPort(
            store, loop, "board", device="/dev/fake", open_link_fn=lambda dev, baud: link
        )
        port.start()
        try:
            # JOIN_TIMEOUT for the join that cannot succeed, then the close under test.
            await asyncio.wait_for(port.stop(), timeout=JOIN_TIMEOUT + 5.0)
            assert link.closed.is_set(), "the handle was never closed"
        finally:
            link.close()
            release.set()
            await hogged
            await store.stop()
            pool.shutdown(wait=True)

    asyncio.run(run())


def test_attach_against_a_stopped_store_is_a_port_error(tmp_path) -> None:
    """An attach landing in the shutdown window answers PortError (400), not 500.

    `prime_plot_defs` ran before every check, and it calls `store.max_id()`, which is an
    `assert self._conn is not None`: an attach that started after `store.stop()` nulled
    the connection answered AssertionError, which the handler maps to 500, and under
    `python -O` an AttributeError on None instead.
    """

    async def run() -> None:
        store = Store(str(tmp_path / "shutdown.db"))
        await store.start()
        mgr = serial_link.PortManager(store, asyncio.get_running_loop())
        await store.stop()
        with pytest.raises(PortError, match="detached"):
            await mgr.attach("board", device="/dev/fake")
        assert "board" not in mgr._ports

    asyncio.run(run())


def test_sys_row_on_a_stopped_store_is_not_an_orphaned_task(tmp_path) -> None:
    """A StoreError from a stopping store is shutdown noise, not an unretrieved task.

    `_spawn_sys` keeps no result, so the exception surfaced only as asyncio's
    "Task exception was never retrieved" traceback on the daemon's stderr.
    """

    async def run() -> None:
        loop = asyncio.get_running_loop()
        unhandled: list[dict] = []
        loop.set_exception_handler(lambda lp, ctx: unhandled.append(ctx))

        store = Store(str(tmp_path / "sys.db"))
        await store.start()
        port = SerialPort(store, loop, "board", device="/dev/fake")
        await store.stop()   # the writer is gone; every add_line now fails immediately

        port._spawn_sys("port board disconnected")
        assert port._bg_tasks, "no sys task was spawned"
        # Never touch the task object: awaiting or gathering it retrieves the exception,
        # which is exactly what nothing does in production. Wait it out through the
        # done-callback that empties _bg_tasks, then let the collector fire the report.
        for _ in range(200):
            if not port._bg_tasks:
                break
            await asyncio.sleep(0.01)
        assert not port._bg_tasks, "the sys task never finished"
        gc.collect()
        await asyncio.sleep(0.05)
        assert not unhandled, f"asyncio reported an unhandled task exception: {unhandled}"

    asyncio.run(run())
