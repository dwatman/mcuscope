"""The page reclaim is bounded in time as well as pages.

Its cost per page is not steady: after a 1.5M-line trim of a 6M-line capture one 2000-page
call took up to 1.2 s on the event loop, every minute, for about an hour. So the pages go
in small steps and no step starts past the budget.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

from mcuscope import store as store_mod
from mcuscope.store import _VACUUM_PAGES, Store, _reclaim_pages
from tests.support import T0_FIXED, run_coro, started_store


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


def _pages(store: Store) -> tuple[int, int]:
    """(page_count, freelist_count) on the writer connection."""
    return (
        int(store._conn.execute("PRAGMA page_count").fetchone()[0]),
        int(store._conn.execute("PRAGMA freelist_count").fetchone()[0]),
    )


# -- improvement 5: the freelist is drained by the tick, not by whether it trimmed -------


async def _fill_and_free(store: Store, rows: int = 1500) -> None:
    """Write `rows` fat lines, then delete most of them without going through a reclaim."""
    for i in range(rows):
        await store.submit_line(
            ts=T0_FIXED + i, port="board", dir="rx", chan="debug", seq=None, raw="x" * 900
        )
    await store.drain_writes()
    store._conn.execute("DELETE FROM lines WHERE id <= ?", (rows - 50,))
    store._conn.commit()


def test_successive_ticks_hand_the_freelist_back_with_nothing_left_to_trim(
    tmp_path, monkeypatch
) -> None:
    """The size sweep reclaimed only `if dropped`, so a capture sitting at its cap kept
    97 MB of free pages for ever, and the age sweep - the default configuration - never
    reclaimed at all."""
    monkeypatch.setattr(store_mod, "_RECLAIM_MIN_PAGES", 1)

    async def run() -> None:
        store = Store(str(tmp_path / "reclaim.db"))
        await store.start()
        try:
            await _fill_and_free(store)
            _pc0, free0 = _pages(store)
            assert free0 > 100, f"the probe did not build a backlog: {free0}"
            prev_pc, prev_free = _pages(store)
            for _ in range(40):
                trimmed = await store.sweep_tick(1)   # no size cap: nothing to trim
                assert trimmed == 0
                pc, free = _pages(store)
                assert pc <= prev_pc and free <= prev_free
                prev_pc, prev_free = pc, free
                if free == 0:
                    break
            assert prev_free == 0, f"the backlog never drained: {prev_free} pages left"
            assert prev_pc < _pc0
        finally:
            await store.stop()

    run_coro(run)


def test_one_tick_reclaims_at_most_the_bound(tmp_path, monkeypatch) -> None:
    """Dropping the bound would pass the convergence test above and put an O(freelist)
    stall back on the event loop, which is the whole reason the reclaim is bounded."""
    monkeypatch.setattr(store_mod, "_RECLAIM_MIN_PAGES", 1)
    monkeypatch.setattr(store_mod, "_VACUUM_PAGES", 20)

    async def run() -> None:
        store = Store(str(tmp_path / "bounded.db"))
        await store.start()
        try:
            await _fill_and_free(store)
            pc0, free0 = _pages(store)
            assert free0 > 100
            await store.sweep_tick(1)
            pc1, free1 = _pages(store)
            assert pc0 - pc1 <= 20, f"one tick handed back {pc0 - pc1} pages, bound is 20"
            assert pc1 < pc0, "and it handed back something"
        finally:
            await store.stop()

    run_coro(run)


def test_the_age_sweep_path_reclaims_too(tmp_path, monkeypatch) -> None:
    """`_sweep_retention_locked` had no reclaim of any kind, so the default configuration
    (age retention, no size cap) freed pages into the freelist for ever."""
    monkeypatch.setattr(store_mod, "_RECLAIM_MIN_PAGES", 1)

    async def run() -> None:
        store = await started_store(tmp_path / "agesweep.db")
        try:
            old = T0_FIXED - 30 * 86400
            for i in range(1500):
                await store.submit_line(
                    ts=old + i, port="board", dir="rx", chan="debug", seq=None, raw="x" * 900
                )
            await store.drain_writes()
            pc0, free0 = _pages(store)
            assert free0 == 0, "nothing is free before the sweep runs"
            store._retention_days = 1   # armed only now: the startup sweep must not race
            await store.sweep_tick(store_mod._RETENTION_TICKS)   # the hourly age sweep
            pc1, _free1 = _pages(store)
            assert pc1 < pc0, "the age sweep deleted rows and handed no pages back"
        finally:
            await store.stop()

    run_coro(run)


def test_the_page_reclaim_stays_bounded_per_call(tmp_path, monkeypatch) -> None:
    # The page bound alone; the time bound is pinned in test_store_reclaim_budget.py.
    monkeypatch.setattr("mcuscope.store._RECLAIM_BUDGET_S", 3600.0)
    """The reclaim is bounded because both callers run on the event loop.

    Its sibling test asserts the freelist drains, which is the defect that was fixed (an
    unfetched `PRAGMA incremental_vacuum` reclaims exactly one page). That test passes just
    as well against an *unbounded* reclaim, which would be O(freelist) on the loop: a
    capture that has plateaued has a large one. This pins the bound itself, per the rule
    that a fix a measurement justified leaves a check on the mechanism rather than on how
    long it took.
    """
    async def run() -> None:
        store = Store(str(tmp_path / "bound.db"))
        await store.start()
        try:
            # Inserted straight onto the connection rather than through the write queue:
            # this pins _reclaim_pages, not the ingest path, and the freelist has to be
            # several times _VACUUM_PAGES for the assertion to tell bounded from unbounded.
            now = time.time()
            store._conn.executemany(
                "INSERT INTO lines(ts, port, chan, dir, raw) VALUES(?,?,?,?,?)",
                [(now, "p", "debug", "rx", "x" * 400) for _ in range(_VACUUM_PAGES * 20)],
            )
            store._conn.execute("DELETE FROM lines")
            store._conn.commit()

            def freelist() -> int:
                return store._conn.execute("PRAGMA freelist_count").fetchone()[0]

            before = freelist()
            assert before > _VACUUM_PAGES * 1.5, \
                f"only {before} free pages; the fixture cannot tell bounded from unbounded"
            _reclaim_pages(store._conn)
            after = freelist()
            assert before - after == _VACUUM_PAGES, \
                f"one call reclaimed {before - after} pages, not {_VACUUM_PAGES}"
            # And it still makes progress across calls, so a backlog drains over sweeps.
            _reclaim_pages(store._conn)
            assert freelist() == after - _VACUUM_PAGES
        finally:
            await store.stop()

    asyncio.run(run())


def test_the_reclaim_does_not_lean_on_execute_stepping_the_pragma(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("mcuscope.store._RECLAIM_BUDGET_S", 3600.0)
    """The reclaim must step the pragma itself, not through a cursor's row consumption.

    On Python 3.11 `PRAGMA incremental_vacuum(N)` yields no rows at all, so
    execute(...).fetchall() steps it exactly once and reclaims one page - the same
    one-page reclaim the fetchall was added to fix, now silent on 3.11 only. Both sibling
    tests above pass on 3.12+ and failed the 3.11 CI legs, so this leg emulates the 3.11
    driver on whatever version runs it: an execute() of the pragma reclaims a single page.
    """
    db = tmp_path / "step.db"

    def one_page(sql: str) -> str:
        return "PRAGMA incremental_vacuum(1)" if "incremental_vacuum" in sql.lower() else sql

    class OneStepCursor(sqlite3.Cursor):
        def execute(self, sql, *args):
            return super().execute(one_page(sql), *args)

    class OneStepPerExecute(sqlite3.Connection):
        """execute() advances the pragma one page, whatever N says, as sqlite3 3.11 does.

        On a cursor too: `conn.cursor().execute(...)` is the same 3.11 statement path.
        """

        def execute(self, sql, *args):
            return super().execute(one_page(sql), *args)

        def cursor(self, factory=OneStepCursor):
            return super().cursor(factory)

    conn = sqlite3.connect(db, factory=OneStepPerExecute)
    try:
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")   # before WAL: see test_store_schema
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE lines(raw TEXT)")
        conn.executemany(
            "INSERT INTO lines(raw) VALUES(?)", [("x" * 400,) for _ in range(_VACUUM_PAGES * 20)]
        )
        conn.execute("DELETE FROM lines")
        conn.commit()

        def freelist() -> int:
            return conn.execute("PRAGMA freelist_count").fetchone()[0]

        before = freelist()
        assert before > _VACUUM_PAGES * 1.5, \
            f"only {before} free pages; the fixture cannot tell one page from {_VACUUM_PAGES}"
        _reclaim_pages(conn)
        assert before - freelist() == _VACUUM_PAGES, \
            f"one call reclaimed {before - freelist()} pages, not {_VACUUM_PAGES}"
    finally:
        conn.close()
