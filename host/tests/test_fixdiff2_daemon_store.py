"""The capture writer's stamp-order notices after a delete, and a failed notice's future."""

from __future__ import annotations

import asyncio
import gc
import time

import pytest

from mcuscope.store import Store, StoreError


async def _add(store: Store, ts: float, raw: str) -> dict:
    return await store.add_line(ts=ts, port="p", dir="rx", chan="debug", seq=None, raw=raw)


def _sys_rows(store: Store) -> list[str]:
    return [r["raw"] for r in store.query_lines(chans=["sys"], order="asc", limit=100)[0]]


@pytest.mark.parametrize("purge", ["range", "before_ts"])
async def test_a_purge_of_the_rows_ahead_ends_the_late_count(tmp_path, purge) -> None:
    store = Store(str(tmp_path / "cap.db"))
    await store.start()
    try:
        t = time.time()
        for i in range(3):   # a clock an hour fast, then stepped back
            await _add(store, t + 3600 + i, f"ahead {i}")
        await _add(store, t, "stepped back")
        assert len(_sys_rows(store)) == 1, "the step back opens an episode"
        if purge == "range":
            await store.delete_range(1, store.max_id())
        else:
            await store.delete_before_ts(t + 7200)
        assert store.count_lines() == 0
        for i in range(3):
            await _add(store, time.time(), f"in order {i}")
        rows = _sys_rows(store)
        assert len(rows) == 1 and "back in time order; 1 committed" in rows[0], rows
        # A genuine inversion after the purge still opens an episode of its own.
        await _add(store, time.time() - 30, "stalled 30 s")
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
        await _add(store, t, "newest")
        real = store._conn
        store._conn = _FailingCommit(real)
        with pytest.raises(StoreError, match="commit failed"):
            await _add(store, t - 20, "late, with a notice in its batch")
        store._conn = real
        assert store.write_errors == 2, "the row and its notice both failed"
        await _add(store, t + 1, "cycles the writer's batch locals")
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
