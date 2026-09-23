"""A reconnect's prime races a re-attach or a disconnect of the same alias; the stop cause
names the dropped-lines row."""

from __future__ import annotations

import asyncio
import time

import pytest

from mcuscope.link import SourceLink
from mcuscope.serial_link import PortError, PortManager, SerialPort
from mcuscope.store import Store

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
