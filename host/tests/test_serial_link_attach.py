"""PortManager.attach(require_existing=True): the reconnect path must not bring back an
alias that a detach removed while the attach was priming."""

from __future__ import annotations

import asyncio

import pytest

from mcuscope.serial_link import PortError, PortManager, SerialPort
from mcuscope.store import Store


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
        new = await mgr.attach("r", "/dev/mcuscope-nonexistent", identify=False,
                               require_existing=True)
        assert new is not old and mgr.get("r") is new

        gate.clear()
        entered.clear()
        reconnect = asyncio.create_task(
            mgr.attach("r", "/dev/mcuscope-nonexistent", identify=False,
                       require_existing=True)
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
