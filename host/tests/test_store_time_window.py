"""Time-bounded reads and `purge before_ts` when `ts` is not monotonic in `id`.

Rows are stamped before they queue for the writer, so a row can take a higher id than one
stamped after it (two ports, or a marker racing a backlogged port). The windows must stay
exact while that inversion is below `WINDOW_TS_SLACK_S`, and a purge by age must select by
`ts`, not by an id range standing in for it.
"""

from __future__ import annotations

import asyncio
import inspect
import random
import time

from mcuscope.store import WINDOW_TS_SLACK_S, Store
from tests.support import T0_FIXED, add_row, add_row_p, captured_plan, run_coro, started_store

T0 = time.time() - 3600   # inside the default retention, so the startup sweep leaves it


async def _store(tmp_path, stamps: list[float]) -> Store:
    store = Store(str(tmp_path / "window.db"))
    await store.start()
    for i, ts in enumerate(stamps):
        await store.add_line(ts=ts, port="p", dir="rx", chan="debug", seq=None, raw=f"r{i}")
    return store


def _exact(store: Store, keep) -> list[int]:
    rows, _ = store.query_lines(limit=1000, order="asc")
    return [r["id"] for r in rows if keep(r["ts"])]


async def test_a_row_stamped_after_the_floor_but_committed_first_is_kept(tmp_path) -> None:
    # The shape an id floor sought at the first row at or after `cutoff - slack` gets
    # wrong: nothing is stamped in the slack interval, so that seek lands on the late row
    # (id 2) and the early-committed row id 1, inside the window, falls below the bound.
    store = await _store(tmp_path, [T0 - 60, T0 + 0.5, T0])
    try:
        cut = T0 + 0.2
        got = [r["raw"] for r in store.query_lines(since_ts=cut, limit=100, order="asc")[0]]
        assert got == ["r1"], got
        got = [r["raw"] for r in store.query_lines(
            floor_ts=cut, limit=100, order="asc")[0]]
        assert got == ["r1"], got
    finally:
        await store.stop()


async def test_every_cutoff_matches_the_exact_ts_filter_under_inversions(tmp_path) -> None:
    # Two interleaved "ports" whose stamps run up to 1.5 s out of id order, with quiet gaps
    # longer than the slack, checked at every distinct cutoff in and around the data.
    rng = random.Random(7)
    stamps: list[float] = []
    t = T0
    for _ in range(12):
        t += rng.choice((0.3, 2.0, WINDOW_TS_SLACK_S * 3))
        stamps.extend(t + rng.uniform(-1.5, 1.5) for _i in range(rng.randint(1, 15)))
    store = await _store(tmp_path, stamps)
    try:
        pairs = zip(stamps, stamps[1:], strict=False)
        assert any(a > b for a, b in pairs), "no inversion was built"
        cuts = sorted(set(stamps)) + [T0 - 1, max(stamps) + 1]
        for cut in cuts + [c + 1e-4 for c in cuts]:
            got = [r["id"] for r in store.query_lines(since_ts=cut, limit=1000, order="asc")[0]]
            assert got == _exact(store, lambda ts, c=cut: ts > c), cut
            got = [r["id"] for r in store.query_lines(floor_ts=cut, limit=1000, order="asc")[0]]
            assert got == _exact(store, lambda ts, c=cut: ts >= c), cut
            assert store.count_lines(floor_ts=cut) == len(_exact(store, lambda ts, c=cut: ts >= c))
    finally:
        await store.stop()


async def test_the_id_floor_is_a_bound_the_empty_window_can_seek_to(tmp_path) -> None:
    # Past everything by more than the slack, the floor is one past the newest id, so an
    # empty poll reads nothing; inside the slack it drops back to the rows that could be
    # in the window.
    store = await _store(tmp_path, [T0, T0 + 1, T0 + 2])
    try:
        assert store._window_id_floor(T0 + 2 + WINDOW_TS_SLACK_S + 1) == 4
        assert store._window_id_floor(T0 + 2) == 1
        assert store.query_lines(since_ts=T0 + 2, limit=10)[0] == []
    finally:
        await store.stop()


async def test_purge_before_ts_deletes_by_age_not_by_an_id_range(tmp_path) -> None:
    # id 2 is new and sits below old rows; ids 3 and 4 are old and sit above it. Deleting
    # the id range up to the newest old row would take ids 1-4, new id 2 among them.
    # Inversions of 6 s, inside the slack, so no sys row takes an id.
    cut = T0 + 10
    store = await _store(tmp_path, [T0, cut + 1, T0 + 5, T0 + 6, cut + 0.5, cut + 0.7])
    try:
        span = await store.before_ts_span_safe(cut)
        assert span == (3, 1, 4), span
        assert store.before_ts_span(cut) == span
        deleted = await store.delete_before_ts(cut)
        assert deleted == 3 == span[0], "the dry-run count is not what the purge deleted"
        left = [(r["id"], r["raw"]) for r in store.query_lines(limit=100, order="asc")[0]]
        assert left == [(2, "r1"), (5, "r4"), (6, "r5")], left
        assert await store.before_ts_span_safe(cut) == (0, None, None)
        assert await store.delete_before_ts(cut) == 0
    finally:
        await store.stop()


async def test_purge_before_ts_goes_past_one_chunk(tmp_path, monkeypatch) -> None:
    import mcuscope.store as store_mod

    monkeypatch.setattr(store_mod, "_RETENTION_CHUNK", 3)
    store = await _store(tmp_path, [T0 + i for i in range(10)] + [T0 + 1000])
    try:
        assert await store.delete_before_ts(T0 + 500) == 10
        assert [r["raw"] for r in store.query_lines(limit=100)[0]] == ["r10"]
    finally:
        await store.stop()


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
        store = await started_store(tmp_path / "ceiling.db")
        try:
            for i in range(5):
                await add_row(store, T0_FIXED + i, f"before{i}")
            after = [   # NTP steps the clock back mid-capture (announced in a sys row)
                (await add_row(store, T0_FIXED - 100 + i, f"after{i}"))["id"] for i in range(3)
            ]
            rows, _ = store.query_lines(until_ts=T0_FIXED + 1e6, limit=1000, order="asc")
            assert [r["raw"] for r in rows] == [
                "before0", "before1", "before2", "before3", "before4",
                "after0", "after1", "after2",
            ], "an until_ts above every stored ts must select the whole capture"
            assert store._window_id_ceiling(T0_FIXED + 1e6) == after[2]
            assert store._window_id_ceiling(T0_FIXED - 99) == after[1], "the bound still bounds"
            assert store._window_id_ceiling(T0_FIXED - 1000) == 0, "nothing at or below is empty"
        finally:
            await store.stop()

    run_coro(run)


def test_the_until_ts_ceiling_stays_inside_the_ts_index(tmp_path) -> None:
    """Class 20: MAX(id) over a ts range must not fall onto the table btree and the blobs."""

    async def run() -> None:
        store = await started_store(tmp_path / "ceilplan.db")
        try:
            for i in range(3):
                await add_row(store, T0_FIXED + i, f"line{i}")
            # The exact branch: the newest row is above the cutoff, so the answer comes
            # from the index walk rather than from the primary-key fast path.
            rows = captured_plan(store, lambda: store._window_id_ceiling(T0_FIXED + 1))
            assert any("idx_lines_ts" in r for r in rows), rows
            assert any("COVERING INDEX" in r.upper() for r in rows), rows
            assert not any("SCAN lines" in r for r in rows), rows
            # And the fast path is a primary-key lookup, not a walk of anything.
            fast = captured_plan(store, lambda: store._window_id_ceiling(T0_FIXED + 1e6))
            assert len(fast) == 1 and "B-TREE" not in fast[0].upper(), fast
        finally:
            await store.stop()

    run_coro(run)


# -- D9: the two halves of since_ts are pinned separately -------------------------------


def test_since_ts_excludes_its_own_instant_where_the_id_floor_cannot(tmp_path) -> None:
    """Class 29. The `ts > ?` term was revertible in silence.

    The paired strict id floor already excludes the boundary row whenever `ts` rises with
    `id`, so flipping the comparison to `ts >= ?` changed no result and the suite stayed
    green. Here the boundary row's id is *above* the floor (its ts is out of id order), so
    only the `ts` term can exclude it.
    """

    async def run() -> None:
        store = await started_store(tmp_path / "sincets.db")
        try:
            await add_row(store, T0_FIXED - 20, "older-than-the-slack")
            await add_row(store, T0_FIXED + 5, "later-first")
            await add_row(store, T0_FIXED, "exactly-at-the-bound")   # id 3, out of id order
            await add_row(store, T0_FIXED + 20, "newest")
            assert store._window_id_floor(T0_FIXED) == 2, \
                "the id floor admits the boundary row (id 3), so the ts term has to exclude it"
            rows, _ = store.query_lines(since_ts=T0_FIXED, limit=100, order="asc")
            assert [r["raw"] for r in rows] == ["later-first", "newest"]
        finally:
            await store.stop()

    run_coro(run)


def test_the_ceiling_walk_names_the_highest_id_not_the_newest_ts(tmp_path) -> None:
    """F-11: the walk branch runs only when the newest row is past the cutoff, which the
    clock-step fixture never had, so the `ORDER BY ts DESC` form (id 3 here) passed."""

    async def run() -> None:
        store = await started_store(tmp_path / "walk.db")
        try:
            for i in range(5):
                await add_row(store, T0_FIXED + i, f"before{i}")
            # the clock steps back
            after = [(await add_row(store, T0_FIXED - 100 + i, f"after{i}"))["id"]
                     for i in range(3)]
            await add_row(store, T0_FIXED + 50, "newest")   # past the cutoff: forces the walk
            assert store._window_id_ceiling(T0_FIXED + 2) == after[-1]
            rows, _ = store.query_lines(until_ts=T0_FIXED + 2, limit=100, order="asc")
            assert [r["raw"] for r in rows] == [
                "before0", "before1", "before2", "after0", "after1", "after2",
            ]
        finally:
            await store.stop()

    asyncio.run(run())


async def test_a_purge_keeps_rows_committed_after_its_span(tmp_path) -> None:
    store = Store(str(tmp_path / "purge.db"))
    await store.start()
    try:
        cutoff = time.time() + 30   # SPEC 3.4 allows a cutoff up to 60 s ahead
        for i in range(5):
            await add_row_p(store, time.time(), f"old {i}")
        n, _lo, hi = store.before_ts_span(cutoff)
        for i in range(3):
            await add_row_p(store, time.time(), f"after the span {i}")
        assert await store.delete_before_ts(cutoff, max_id=hi) == n == 5
        rows, _ = store.query_lines(order="asc")
        assert [r["raw"] for r in rows] == [f"after the span {i}" for i in range(3)]
        # Without the bound, the old behaviour: everything stamped before the cutoff.
        assert await store.delete_before_ts(cutoff) == 3
    finally:
        await store.stop()
