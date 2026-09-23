"""The capture writer, 2026-09-24 fix-diff leg: commit holds under mixed load, a
cancel during a hold, and rows committing past the window slack."""

from __future__ import annotations

import asyncio
import contextlib
import time

import pytest

from mcuscope import store as store_mod
from mcuscope.store import Store, StoreError


async def _add(store: Store, ts: float, raw: str, chan: str = "debug") -> dict:
    return await store.add_line(ts=ts, port="p", dir="rx", chan=chan, seq=None, raw=raw)


def _sys_rows(store: Store) -> list[str]:
    return [r["raw"] for r in store.query_lines(chans=["sys"], order="asc", limit=100)[0]]


async def test_awaited_rows_beside_a_slow_stream_are_never_held(tmp_path, monkeypatch) -> None:
    # Two commits a few ms apart (a stream line, then a cmd's tx row) once read as
    # thousands of lines/s: 42 holds in 4 s at 50 lines/s.
    holds: list[float] = []
    real = store_mod._commit_hold
    monkeypatch.setattr(store_mod, "_commit_hold",
                        lambda *a: holds.append(real(*a)) or holds[-1])
    store = Store(str(tmp_path / "mixed.db"))
    await store.start()
    stop = False

    async def stream() -> None:   # 50 lines/s
        while not stop:
            store.submit_line_nowait(ts=time.time(), port="busy", dir="rx", chan="debug",
                                     seq=None, raw="s")
            await asyncio.sleep(0.02)

    task = asyncio.create_task(stream())
    try:
        for i in range(10):
            await store.add_line(ts=time.time(), port="board", dir="tx", chan="cmd", seq=i,
                                 raw="ping")
            await asyncio.sleep(0.2)
        assert len(holds) > 50, "the stream and the awaited rows reached the hold policy"
        assert not any(holds), [h for h in holds if h]
    finally:
        stop = True
        await task
        await store.stop()


async def test_a_writer_cancelled_during_a_hold_fails_the_row_it_took(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store_mod, "_commit_hold", lambda *a: 5.0)
    store = Store(str(tmp_path / "held.db"))
    await store.start()
    try:
        fut = store.submit_line_nowait(ts=time.time(), port="p", dir="rx", chan="debug",
                                       seq=None, raw="in hand")
        await asyncio.sleep(0.05)
        assert store._queue.qsize() == 0, "the writer took the row and is holding"
        store._writer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await store._writer_task
        store._fail_queued("store stopped")
        assert fut.done(), "the held row's future was left pending"
        with pytest.raises(StoreError, match="writer exited"):
            fut.result()
        assert store.write_errors == 1
    finally:
        await store.stop()


async def test_rows_committing_past_the_slack_are_announced_with_a_count(tmp_path) -> None:
    store = Store(str(tmp_path / "late.db"))
    await store.start()
    try:
        t = time.time()
        await _add(store, t, "newest stamp first")
        await _add(store, t - 10.5, "late by 10.5 s")
        assert len(_sys_rows(store)) == 1, "the episode is announced as its first row commits"
        await _add(store, t - 11, "late by 11 s")
        await _add(store, t + 1, "back in order")
        start, end = _sys_rows(store)
        assert "up to 10.5 s behind newer timestamps, past the 10 s window slack" in start
        assert "back in time order; 2 committed up to 11.0 s behind" in end
        raws = [r["raw"] for r in store.query_lines(order="asc", limit=100)[0]]
        assert raws.index(end) > raws.index("back in order")
        assert raws.index(start) < raws.index("late by 11 s")
    finally:
        await store.stop()


async def test_a_capture_restarted_behind_its_newest_stamp_announces_it(tmp_path) -> None:
    path = str(tmp_path / "stepped.db")
    store = Store(path)
    await store.start()
    t = time.time()
    await _add(store, t + 60, "stamped before the clock stepped back")
    await store.stop()
    store = Store(path)
    await store.start()
    try:
        await _add(store, t, "after the restart")
        assert len(_sys_rows(store)) == 1
    finally:
        await store.stop()


async def test_an_inversion_inside_the_slack_keeps_every_row_and_says_nothing(tmp_path) -> None:
    # The 10 s slack is pinned: at 3 s the id floor drops the first row.
    store = Store(str(tmp_path / "inside.db"))
    await store.start()
    try:
        t = time.time()
        await _add(store, t, "committed first, stamped 9.5 s later")
        await _add(store, t - 9.5, "committed second")
        rows, _ = store.query_lines(since_ts=t - 0.001, order="asc")
        assert [r["raw"] for r in rows] == ["committed first, stamped 9.5 s later"]
        assert store.count_lines(floor_ts=t - 0.001) == 1
        assert _sys_rows(store) == []
    finally:
        await store.stop()
