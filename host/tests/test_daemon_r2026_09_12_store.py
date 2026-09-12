"""Store fixes from the 2026-09-12 adversarial round (D1, D5, D6, D9) and improvements 5, 7, 12.

Every test here drives the store directly: each one is about which rows a bound admits, or
about the plan the statement gets, and a live daemon adds nothing to either.
"""

from __future__ import annotations

import asyncio
import inspect

from mcuscope import store as store_mod
from mcuscope.store import Store
from tests.test_hardening import _captured_plan, await_line

T0 = 1_700_000_000.0


def _run(coro_fn) -> None:
    asyncio.run(coro_fn())


async def _started(path) -> Store:
    """A store whose age retention cannot reach T0: the startup sweep runs concurrently
    with the test's own writes, and a decade-old `ts` is expired by the default."""
    store = Store(str(path))
    await store.start(retention_days=36_500)
    return store


async def _add(store: Store, ts: float, raw: str, port: str = "board") -> dict:
    return await store.add_line(ts=ts, port=port, dir="rx", chan="debug", seq=None, raw=raw)


def _pages(store: Store) -> tuple[int, int]:
    """(page_count, freelist_count) on the writer connection."""
    return (
        int(store._conn.execute("PRAGMA page_count").fetchone()[0]),
        int(store._conn.execute("PRAGMA freelist_count").fetchone()[0]),
    )


# -- D1: a can id list must not throw away the ORDER BY index order ---------------------


def test_can_id_list_keeps_driving_from_the_frame_table(tmp_path) -> None:
    """Class 20. The list form regressed the plan the single-id form and CROSS JOIN keep.

    `cf.can_id IN (...)` made the planner drive from idx_can_id_line and sort every match
    through a temp b-tree before LIMIT could apply: 0.66 s against 0.01 s on a 100-row
    page at 1M lines, and 47.0 s against 3.85 s on the CSV export, which re-issues the
    statement per page. The plan is pinned rather than the timing, and both forms are
    pinned in one test so the `len(ids) == 1` split cannot rot.
    """

    async def run() -> None:
        store = Store(str(tmp_path / "canlist.db"))
        await store.start()
        try:
            for i, port in enumerate(("A", "B")):
                await await_line(store, port, i)
            cases = {
                "one id": {"can_ids": [0x100]},
                "two ids": {"can_ids": [0x100, 0x101]},
                "three ids, with a port": {"can_ids": [0x100, 0x101, 0x102], "port": "A"},
                "list plus a window": {"can_ids": [0x100, 0x101], "last_ms": 5000},
            }
            for label, kwargs in cases.items():
                rows = _captured_plan(store, lambda k=kwargs: store.query_can_frames(**k))
                assert " cf" in rows[0], f"{label} does not drive from can_frames: {rows}"
                assert not any("TEMP B-TREE" in r for r in rows), \
                    f"{label} sorts every match before LIMIT: {rows}"
        finally:
            await store.stop()

    _run(run)


def test_a_can_id_list_selects_every_id_in_it(tmp_path) -> None:
    """The `+` de-optimisation is a plan hint only: the rows must not change."""

    async def run() -> None:
        store = Store(str(tmp_path / "canrows.db"))
        await store.start()
        try:
            for i in range(4):
                await await_line(store, "A", i)
            rows, _ = store.query_can_frames(can_ids=[0x100, 0x102], limit=100)
            assert sorted(r["can_id"] for r in rows) == [0x100, 0x102]
        finally:
            await store.stop()

    _run(run)


# -- D5: a window term no caller could reach -------------------------------------------


def test_count_lines_takes_no_unreachable_window_term() -> None:
    """Class 31. `count_lines(until_ts=)` had no caller anywhere and no test could drive it."""
    assert "until_ts" not in inspect.signature(Store.count_lines).parameters


# -- D6: the ceiling is the highest id in the window, not the id of the newest ts -------


def test_until_ts_wider_than_the_capture_keeps_every_row_after_a_clock_step(tmp_path) -> None:
    """A backwards wall-clock step made the widest possible window drop the newest rows.

    `_window_id_ceiling` answered with the id of the newest *ts* at or below the cutoff,
    so with `ts` out of id order it named an id below rows the window still covers. An
    `until_ts` above every stored `ts` is what a caller writes for "no upper bound", which
    is the one shape where silent loss is least likely to be noticed.
    """

    async def run() -> None:
        store = await _started(tmp_path / "ceiling.db")
        try:
            for i in range(5):
                await _add(store, T0 + i, f"before{i}")
            for i in range(3):     # NTP steps the clock back mid-capture
                await _add(store, T0 - 100 + i, f"after{i}")
            rows, _ = store.query_lines(until_ts=T0 + 1e6, limit=1000, order="asc")
            assert [r["raw"] for r in rows] == [
                "before0", "before1", "before2", "before3", "before4",
                "after0", "after1", "after2",
            ], "an until_ts above every stored ts must select the whole capture"
            assert store._window_id_ceiling(T0 + 1e6) == 8
            assert store._window_id_ceiling(T0 - 99) == 7, "the bound still bounds"
            assert store._window_id_ceiling(T0 - 1000) == 0, "nothing at or below is empty"
        finally:
            await store.stop()

    _run(run)


def test_the_until_ts_ceiling_stays_inside_the_ts_index(tmp_path) -> None:
    """Class 20: MAX(id) over a ts range must not fall onto the table btree and the blobs."""

    async def run() -> None:
        store = await _started(tmp_path / "ceilplan.db")
        try:
            for i in range(3):
                await _add(store, T0 + i, f"line{i}")
            # The exact branch: the newest row is above the cutoff, so the answer comes
            # from the index walk rather than from the primary-key fast path.
            rows = _captured_plan(store, lambda: store._window_id_ceiling(T0 + 1))
            assert any("idx_lines_ts" in r for r in rows), rows
            assert any("COVERING INDEX" in r.upper() for r in rows), rows
            assert not any("SCAN lines" in r for r in rows), rows
            # And the fast path is a primary-key lookup, not a walk of anything.
            fast = _captured_plan(store, lambda: store._window_id_ceiling(T0 + 1e6))
            assert len(fast) == 1 and "B-TREE" not in fast[0].upper(), fast
        finally:
            await store.stop()

    _run(run)


# -- D9: the two halves of since_ts are pinned separately -------------------------------


def test_since_ts_excludes_its_own_instant_where_the_id_floor_cannot(tmp_path) -> None:
    """Class 29. The `ts > ?` term was revertible in silence.

    The paired strict id floor already excludes the boundary row whenever `ts` rises with
    `id`, so flipping the comparison to `ts >= ?` changed no result and the suite stayed
    green. Here the boundary row's id is *above* the floor (its ts is out of id order), so
    only the `ts` term can exclude it.
    """

    async def run() -> None:
        store = await _started(tmp_path / "sincets.db")
        try:
            await _add(store, T0 + 10, "later-first")
            await _add(store, T0, "exactly-at-the-bound")   # out of id order
            await _add(store, T0 + 20, "newest")
            assert store._window_id_floor(T0, strict=True) == 1, \
                "the id floor admits the boundary row, so the ts term has to exclude it"
            rows, _ = store.query_lines(since_ts=T0, limit=100, order="asc")
            assert [r["raw"] for r in rows] == ["later-first", "newest"]
        finally:
            await store.stop()

    _run(run)


# -- improvement 5: the freelist is drained by the tick, not by whether it trimmed -------


async def _fill_and_free(store: Store, rows: int = 1500) -> None:
    """Write `rows` fat lines, then delete most of them without going through a reclaim."""
    for i in range(rows):
        await store.submit_line(
            ts=T0 + i, port="board", dir="rx", chan="debug", seq=None, raw="x" * 900
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

    _run(run)


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

    _run(run)


def test_the_age_sweep_path_reclaims_too(tmp_path, monkeypatch) -> None:
    """`_sweep_retention_locked` had no reclaim of any kind, so the default configuration
    (age retention, no size cap) freed pages into the freelist for ever."""
    monkeypatch.setattr(store_mod, "_RECLAIM_MIN_PAGES", 1)

    async def run() -> None:
        store = await _started(tmp_path / "agesweep.db")
        try:
            old = T0 - 30 * 86400
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

    _run(run)


# -- improvement 7: a subscriber parked on the queue is woken when the capture closes ----


def test_stop_wakes_every_subscriber_with_a_sentinel(tmp_path) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "sentinel.db"))
        await store.start()
        empty = store.subscribe()
        full = store.subscribe(maxsize=1)
        full.put_nowait({"id": 1})            # drop-oldest has to make room for the sentinel
        await store.stop()
        assert empty.get_nowait() is None
        assert full.get_nowait() is None, "a full queue must still receive the sentinel"

    _run(run)


# -- improvement 12: the writer's page cache -------------------------------------------


def test_the_writer_connection_gets_a_page_cache(tmp_path) -> None:
    """64 MB, against SQLite's 2 MB default, on a capture that reaches hundreds of MB.

    The pragma read-back is the honest test: there is no throughput assertion that is not
    flaky. A capture must still open and commit if the value were ever refused, which the
    write below covers.
    """

    async def run() -> None:
        store = Store(str(tmp_path / "cache.db"))
        await store.start()
        try:
            assert store._conn.execute("PRAGMA cache_size").fetchone()[0] == -65536
            row = await _add(store, T0, "still commits")
            assert row["id"] == 1
        finally:
            await store.stop()

    _run(run)
