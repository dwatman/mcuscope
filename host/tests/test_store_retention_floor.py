"""Age retention is counted and announced (RES-6), and the `min_sessions` floor protects
only ended sessions (SOAK-1): a daemon left running is one session and must still age out."""

from __future__ import annotations

import time

from mcuscope.store import Store

DAY = 86400.0


async def _old(store: Store, raw: str, days: float) -> dict:
    return await store.add_line(ts=time.time() - days * DAY, port="b", dir="rx", chan="debug",
                                seq=None, raw=raw)


def _raws(store: Store) -> list[str]:
    return [r["raw"] for r in store.query_lines(limit=1000, order="asc")[0]]


async def test_the_running_session_ages_out_and_the_expiry_is_announced(tmp_path) -> None:
    store = Store(str(tmp_path / "c.db"))
    await store.start(retention_days=10, min_sessions=5)
    try:
        await store._initial_sweep_task
        await store.start_session("auto-run", auto=True)
        for days in (40, 20, 11):
            await _old(store, f"aged {days}", days)
        await _old(store, "fresh", 0)
        tick = await store.sweep_tick(60)            # the hourly age sweep is due
        assert tick == 0
        raws = _raws(store)
        assert not any(r.startswith("aged") for r in raws), raws
        assert "fresh" in raws
        assert store.lines_expired == 3
        notice = [r for r in raws if r.startswith("storage: expired ")]
        assert len(notice) == 1 and notice[0].startswith("storage: expired 3 lines older than ")
        await store.sweep_tick(60)                    # nothing left to expire: no new row
        assert store.lines_expired == 3
        assert len([r for r in _raws(store) if r.startswith("storage: expired ")]) == 1
    finally:
        await store.stop()


async def test_ended_sessions_stay_protected_and_lines_after_them_do_not(tmp_path) -> None:
    store = Store(str(tmp_path / "c.db"))
    await store.start(retention_days=10, min_sessions=1)
    try:
        await store._initial_sweep_task
        await _old(store, "before", 40)
        await store.start_session("kept")
        await _old(store, "inside", 40)
        await store.stop_session()
        await _old(store, "after", 40)               # no session running: not protected
        await store.start_session("running")
        await _old(store, "running", 40)
        assert await store._sweep_retention_async() == 3
        raws = _raws(store)
        assert "inside" in raws
        assert not {"before", "after", "running"} & set(raws), raws
    finally:
        await store.stop()


async def test_the_size_cap_spends_the_running_session_before_a_protected_run(tmp_path) -> None:
    store = Store(str(tmp_path / "c.db"))
    await store.start(retention_days=10, min_sessions=1)
    try:
        await store._initial_sweep_task
        await store.start_session("kept")
        for i in range(5):
            await _old(store, f"kept {i}", 0)
        await store.stop_session()
        await store.start_session("running")
        for i in range(5):
            await _old(store, f"run {i}", 0)
        span = store.retention_span()
        assert span is not None
        while store._delete_oldest_chunk(2, span):
            pass
        raws = _raws(store)
        assert [r for r in raws if r.startswith("kept ")] == [f"kept {i}" for i in range(5)]
        assert not any(r.startswith("run ") for r in raws)
    finally:
        await store.stop()


async def test_a_span_that_lost_its_top_walks_everything_again(tmp_path) -> None:
    # Forgetting the newest protected session lowers the span's top: its rows lose their
    # protection however old they are, so the next sweep cannot start at the last cutoff.
    store = Store(str(tmp_path / "c.db"))
    await store.start(retention_days=10, min_sessions=2)
    try:
        await store._initial_sweep_task
        await store.start_session("a")
        await _old(store, "in a", 40)
        await store.stop_session()
        b = await store.start_session("b")
        await _old(store, "in b", 40)
        await store.stop_session()
        assert await store._sweep_retention_async() == 0
        assert store._last_age_sweep is not None
        store.delete_session(b["id"])          # the label only; its lines stay
        assert await store._sweep_retention_async() == 1
        raws = _raws(store)
        assert "in a" in raws and "in b" not in raws
    finally:
        await store.stop()
