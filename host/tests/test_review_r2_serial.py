"""Round-2 review fixes in `serial_link.py`.

Five defects, one test each: the detach handle-close sharing the default executor, an
attach that primed the plot defs before checking the cap and the store, a serial_number
attach that never reported the device it opened, and a sys-row task orphaning a
StoreError from a stopping store; plus the /send escape-hatch guarantee (no token cap).
"""

from __future__ import annotations

import asyncio
import gc
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from mcuscope import serial_link
from mcuscope.link import Link, SourceLink
from mcuscope.serial_link import JOIN_TIMEOUT, PortError, SerialPort
from mcuscope.store import Store
from tests.support import Scripted, Stack


class _Info:
    """The two fields of a pyserial ListPortInfo that the port code reads."""

    def __init__(self, device: str, serial_number: str | None = None) -> None:
        self.device = device
        self.serial_number = serial_number


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


def test_serial_number_port_reports_the_device_it_opened(monkeypatch) -> None:
    """`/status` must say which device a serial number landed on, not echo the serial.

    Driven through the reader with a fake enumeration and a scripted link, so the value
    reported is the one `_resolve_device` actually handed to the open. `self.device` must
    stay None: the next reconnect has to re-resolve, since the board can come back on a
    different node.
    """

    async def run() -> None:
        store = Store(":memory:")
        await store.start()
        monkeypatch.setattr(
            serial_link, "cached_comports", lambda: [_Info("/dev/ttyFAKE7", "SN1")]
        )
        port = SerialPort(
            store, asyncio.get_running_loop(), "board", serial_number="SN1",
            open_link_fn=lambda dev, baud: SourceLink(Scripted([], idle_after=True)),
        )
        assert port.status()["device"] == "SN1"   # nothing opened yet
        port.start()
        try:
            for _ in range(100):
                if port.connected:
                    break
                await asyncio.sleep(0.05)
            assert port.connected, "the scripted link never connected"
            assert port.status()["device"] == "/dev/ttyFAKE7"
            assert port.device is None, "device must stay unset so reconnect re-resolves"
        finally:
            await port.stop()
            await store.stop()

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


def test_send_passes_a_line_over_the_command_token_cap(stack: Stack) -> None:
    """/send is the escape hatch for non-monitor firmware (SPEC 3.4).

    SPEC 2.3's 12-token rule is a command-line rule, enforced by format_command on the
    /cmd path only; a raw line to a non-monitor target may be any shape the wire allows,
    so a 13-token /send must reach the port, not die to a monitor-grammar check.
    """
    with httpx.Client(base_url=stack.base_url, timeout=5.0) as c:
        r = c.post("/send", json={"line": " ".join(["word"] * 13)})
    assert r.status_code == 200
