"""The store writer dying of something nobody guarded, and what happens to the queue.

`_fail_queued` existed but was wired only into `stop()`, so a writer that died on its own
left every queued future pending: `SerialPort._store_rx_batch` awaits exactly those, and
they resolve only when the loop closes.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from mcuscope.store import Store, StoreError


async def _submit(store: Store, raw: str):
    return await store.submit_line(
        ts=time.time(), port="t", dir="rx", chan="debug", seq=None, raw=raw
    )


async def test_a_writer_that_dies_fails_what_is_still_queued(tmp_path, monkeypatch) -> None:
    """One row per batch, so the writer dies with the rest of the queue behind it."""
    monkeypatch.setattr("mcuscope.store._MAX_BATCH_ROWS", 1)
    store = Store(str(tmp_path / "died.db"))
    await store.start()
    try:
        def boom(row) -> None:      # outside the insert/commit guards: it ends the task
            raise RuntimeError("broadcast exploded")

        monkeypatch.setattr(store, "_broadcast", boom)
        # No await between the puts (the queue is not full, so put does not yield), so all
        # three are queued before the writer wakes and takes the first one alone.
        futures = [await _submit(store, f"line {i}") for i in range(3)]
        for fut in futures[1:]:
            with pytest.raises(StoreError, match="store writer exited"):
                await asyncio.wait_for(fut, 2)
        assert not store.writer_alive
    finally:
        await store.stop()


async def test_a_writer_that_dies_mid_batch_fails_that_batch(tmp_path, monkeypatch) -> None:
    """The rows already taken off the queue are reachable from nowhere else."""
    store = Store(str(tmp_path / "batch.db"))
    await store.start()
    try:
        def boom(row) -> None:
            raise RuntimeError("broadcast exploded")

        monkeypatch.setattr(store, "_broadcast", boom)
        futures = [await _submit(store, f"line {i}") for i in range(3)]
        # The first row is stored and resolved before the broadcast raises; the rest of the
        # batch is what the writer would otherwise take down with it.
        assert (await asyncio.wait_for(futures[0], 2))["raw"] == "line 0"
        for fut in futures[1:]:
            with pytest.raises(StoreError, match="store writer exited"):
                await asyncio.wait_for(fut, 2)
    finally:
        await store.stop()


async def test_a_clean_stop_does_not_report_a_dead_writer(tmp_path, caplog) -> None:
    """The sentinel exit is not a death: stop() owns the queue on that path."""
    store = Store(str(tmp_path / "clean.db"))
    await store.start()
    await asyncio.wait_for(_submit(store, "kept"), 2)
    await store.stop()
    assert not [r for r in caplog.records if "store writer died" in r.getMessage()]
