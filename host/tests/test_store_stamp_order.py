"""The capture writer's stamp-order notices (SPEC 3.2): rows committing past the window
slack, a capture restarted behind its newest stamp, and a purge of the rows ahead."""

from __future__ import annotations

import asyncio
import gc
import time

import pytest

from mcuscope.store import Store, StoreError
from tests.support import add_row_p


async def test_rows_committing_past_the_slack_are_announced_with_a_count(tmp_path) -> None:
    store = Store(str(tmp_path / "late.db"))
    await store.start()
    try:
        t = time.time()
        await add_row_p(store, t, "newest stamp first")
        await add_row_p(store, t - 10.5, "late by 10.5 s")
        assert len(_sys_rows(store)) == 1, "the episode is announced as its first row commits"
        await add_row_p(store, t - 11, "late by 11 s")
        await add_row_p(store, t + 1, "back in order")
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
    await add_row_p(store, t + 60, "stamped before the clock stepped back")
    await store.stop()
    store = Store(path)
    await store.start()
    try:
        await add_row_p(store, t, "after the restart")
        assert len(_sys_rows(store)) == 1
    finally:
        await store.stop()


async def test_an_inversion_inside_the_slack_keeps_every_row_and_says_nothing(tmp_path) -> None:
    # The 10 s slack is pinned: at 3 s the id floor drops the first row.
    store = Store(str(tmp_path / "inside.db"))
    await store.start()
    try:
        t = time.time()
        await add_row_p(store, t, "committed first, stamped 9.5 s later")
        await add_row_p(store, t - 9.5, "committed second")
        rows, _ = store.query_lines(since_ts=t - 0.001, order="asc")
        assert [r["raw"] for r in rows] == ["committed first, stamped 9.5 s later"]
        assert store.count_lines(floor_ts=t - 0.001) == 1
        assert _sys_rows(store) == []
    finally:
        await store.stop()


def _sys_rows(store: Store) -> list[str]:
    return [r["raw"] for r in store.query_lines(chans=["sys"], order="asc", limit=100)[0]]


@pytest.mark.parametrize("purge", ["range", "before_ts"])
async def test_a_purge_of_the_rows_ahead_ends_the_late_count(tmp_path, purge) -> None:
    store = Store(str(tmp_path / "cap.db"))
    await store.start()
    try:
        t = time.time()
        for i in range(3):   # a clock an hour fast, then stepped back
            await add_row_p(store, t + 3600 + i, f"ahead {i}")
        await add_row_p(store, t, "stepped back")
        assert len(_sys_rows(store)) == 1, "the step back opens an episode"
        if purge == "range":
            await store.delete_range(1, store.max_id())
        else:
            await store.delete_before_ts(t + 7200)
        assert store.count_lines() == 0
        for i in range(3):
            await add_row_p(store, time.time(), f"in order {i}")
        rows = _sys_rows(store)
        assert len(rows) == 1 and "back in time order; 1 committed" in rows[0], rows
        # A genuine inversion after the purge still opens an episode of its own.
        await add_row_p(store, time.time() - 30, "stalled 30 s")
        rows = _sys_rows(store)
        assert len(rows) == 2 and "rows are committing up to 30." in rows[1], rows
    finally:
        await store.stop()


class _FailingCommit:
    """The writer's connection, with its next commit failing (disk full)."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def commit(self) -> None:
        raise OSError("disk full")

    def __getattr__(self, name):
        return getattr(self._conn, name)


async def test_a_notice_in_a_failed_commit_logs_no_unretrieved_exception(tmp_path) -> None:
    loop = asyncio.get_running_loop()
    seen: list[str] = []
    loop.set_exception_handler(lambda _loop, ctx: seen.append(ctx["message"]))
    store = Store(str(tmp_path / "cap.db"))
    await store.start()
    try:
        t = time.time()
        await add_row_p(store, t, "newest")
        real = store._conn
        store._conn = _FailingCommit(real)
        with pytest.raises(StoreError, match="commit failed"):
            await add_row_p(store, t - 20, "late, with a notice in its batch")
        store._conn = real
        assert store.write_errors == 2, "the row and its notice both failed"
        await add_row_p(store, t + 1, "cycles the writer's batch locals")
        gc.collect()
        await asyncio.sleep(0)
        assert not [m for m in seen if "never retrieved" in m], seen
        # Positive control: an unretrieved failed future does reach this handler.
        fut = loop.create_future()
        fut.set_exception(StoreError("control"))
        del fut
        gc.collect()
        assert [m for m in seen if "never retrieved" in m], seen
    finally:
        loop.set_exception_handler(None)
        await store.stop()
