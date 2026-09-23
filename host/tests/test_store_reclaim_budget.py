"""The page reclaim is bounded in time as well as pages.

Its cost per page is not steady: after a 1.5M-line trim of a 6M-line capture one 2000-page
call took up to 1.2 s on the event loop, every minute, for about an hour. So the pages go
in small steps and no step starts past the budget.
"""

from __future__ import annotations

import time

from mcuscope import store as store_mod
from mcuscope.store import Store, _reclaim_pages


async def _freed(tmp_path) -> Store:
    store = Store(str(tmp_path / "reclaim.db"))
    await store.start()
    for i in range(1500):
        await store.submit_line(ts=time.time(), port="p", dir="rx", chan="debug", seq=None,
                                raw=f"{i} " + "x" * 900)
    await store.drain_writes()
    store._conn.execute("DELETE FROM lines WHERE id <= 1400")
    store._conn.commit()
    return store


def _free(store: Store) -> int:
    return store._conn.execute("PRAGMA freelist_count").fetchone()[0]


async def test_a_spent_budget_stops_after_one_step(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store_mod, "_RECLAIM_BUDGET_S", 0.0)
    store = await _freed(tmp_path)
    try:
        before = _free(store)
        assert before > 3 * store_mod._VACUUM_STEP_PAGES
        _reclaim_pages(store._conn)
        assert before - _free(store) == store_mod._VACUUM_STEP_PAGES
    finally:
        await store.stop()


async def test_within_budget_the_steps_continue_up_to_the_page_bound(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store_mod, "_RECLAIM_BUDGET_S", 3600.0)
    monkeypatch.setattr(store_mod, "_VACUUM_PAGES", 3 * store_mod._VACUUM_STEP_PAGES + 5)
    store = await _freed(tmp_path)
    try:
        before = _free(store)
        assert before > store_mod._VACUUM_PAGES
        _reclaim_pages(store._conn)
        assert before - _free(store) == store_mod._VACUUM_PAGES
    finally:
        await store.stop()
