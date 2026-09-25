"""The hourly age sweep starts its walk at the previous sweep's cutoff while the session floor
has not risen (REVIEW class 20): with a floor protecting a long expired run, every sweep read
and rejected each protected row, on the event loop."""

from __future__ import annotations

import time

from mcuscope.store import Store

DAY = 86400.0


async def _store(tmp_path, name: str) -> Store:
    store = Store(str(tmp_path / name))
    await store.start(retention_days=1, min_sessions=1)
    await store._initial_sweep_task
    return store


async def _old(store: Store, raw: str, days: float) -> dict:
    return await store.add_line(ts=time.time() - days * DAY, port="b", dir="rx", chan="debug",
                                seq=None, raw=raw)


def _raws(store: Store) -> set[str]:
    return {r["raw"] for r in store.query_lines(limit=10_000, order="asc")[0]}


async def _swept_steps(store: Store) -> int:
    count = [0]

    def tick() -> int:
        count[0] += 1
        return 0

    store._conn.set_progress_handler(tick, 1)
    try:
        await store._sweep_retention_async()
    finally:
        store._conn.set_progress_handler(None, 1)
    return count[0]


async def test_a_protected_expired_run_is_walked_once_not_every_sweep(tmp_path) -> None:
    store = await _store(tmp_path, "walk.db")
    try:
        await store.start_session("protected")
        for i in range(2000):
            await _old(store, f"p{i}", days=30)
        first = await _swept_steps(store)    # the writer saw old rows: a full walk
        second = await _swept_steps(store)
        kept = store._conn.execute("SELECT COUNT(*) FROM lines WHERE raw GLOB 'p*'").fetchone()
        assert kept[0] == 2000, "the floor held"
        assert first > 2000, first            # positive control: the full walk is visible
        assert second < 200, (first, second)
    finally:
        await store.stop()


async def test_a_risen_floor_walks_everything_again(tmp_path) -> None:
    store = await _store(tmp_path, "rise.db")
    try:
        await store.start_session("old-run")
        await _old(store, "old-run row", days=30)
        await store.stop_session()
        await store._sweep_retention_async()
        assert "old-run row" in _raws(store)
        await store.start_session("new-run")   # the floor rises past the old run
        await store._sweep_retention_async()
        assert "old-run row" not in _raws(store)
    finally:
        await store.stop()


async def test_an_old_row_committed_after_a_sweep_is_still_found(tmp_path) -> None:
    store = await _store(tmp_path, "late.db")
    try:
        await store.start_session("run")
        await store._sweep_retention_async()
        assert store._last_age_sweep is not None   # the next sweep would start at its cutoff
        store.set_min_sessions(0)                  # the floor rises: pure age from here
        await store._sweep_retention_async()
        await _old(store, "stamped long ago", days=30)   # a clock stepped back, say
        await store._sweep_retention_async()
        assert "stamped long ago" not in _raws(store)
    finally:
        await store.stop()


async def test_an_old_row_landing_mid_sweep_leaves_no_bound_behind(tmp_path) -> None:
    store = await _store(tmp_path, "mid.db")
    try:
        await store._sweep_retention_async()
        assert store._last_age_sweep is not None
        real = store._delete_expired_chunk

        def chunk(*args):
            store._last_age_sweep = None   # what the writer does for a row below the cutoff
            return real(*args)

        store._delete_expired_chunk = chunk
        await store._sweep_retention_async()
        assert store._last_age_sweep is None
    finally:
        await store.stop()
