"""SerialPort's device writes: their private pool, when they are stamped, and the pending
entry a failed tx row leaves behind (SPEC 3.2)."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from mcuscope.serial_link import SerialPort


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
