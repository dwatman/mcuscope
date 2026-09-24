"""Plans for `/lines` and `count_lines` filtered by `port` and `chan` together (class 20).

Neither single-column index serves both a quiet port with a busy channel and a busy port
with a rare one: whichever the planner took, the other shape walked every row of the busy
side on the event loop (7.7 s for `port=aux&chan=debug` at 6M lines). The statements are
taken off the trace callback, so the plan pinned is the one the store issues, on a capture
with no sqlite_stat1 (the shipped condition; a few rows reproduce the planner's choice).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from mcuscope.store import WINDOW_TS_SLACK_S, Store, _make_regexp
from tests.support import add_sys, captured_plan, run_coro

C5 = ["debug", "event", "resp", "cmd", "marker"]


def _plan(store: Store, run, keyword: str = "SELECT") -> list[str]:
    seen: list[str] = []
    store._conn.set_trace_callback(seen.append)
    try:
        run()
    finally:
        store._conn.set_trace_callback(None)
    stmts = [s for s in seen if s.lstrip().upper().startswith(keyword)]
    assert stmts, seen
    return [str(r[3]) for r in store._conn.execute("EXPLAIN QUERY PLAN " + stmts[-1])]


@pytest.fixture
async def store(tmp_path):
    s = Store(str(tmp_path / "plan.db"))
    await s.start()
    for i in range(20):
        await s.add_line(ts=time.time(), port="busy", dir="rx", chan="debug", seq=None,
                         raw=f"b{i}")
    await s.add_line(ts=time.time(), port="busy", dir="-", chan="marker", seq=None, raw="m")
    await s.add_line(ts=time.time(), port="quiet", dir="rx", chan="debug", seq=None, raw="q")
    assert not s._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='sqlite_stat1'"
    ).fetchall()
    yield s
    await s.stop()


BOUNDS = [
    ("none", {}),
    ("id_to", {"id_to": 10}),
    ("since_id", {"since_id": 5}),
    ("last_ms", {"last_ms": 60_000}),
    ("since_ts", {"since_ts": time.time() - 60}),
]
SHAPES = [
    ("quiet port, busy chan", "quiet", ["debug"]),
    ("quiet port, chan list", "quiet", C5),
    ("busy port, rare chan", "busy", ["marker"]),
    ("busy port, rare chan list", "busy", ["marker", "sys"]),
    ("absent port, chan list", "nosuch", C5),
    ("daemon port, chan list", "", ["sys", "marker"]),
]


@pytest.mark.parametrize("label,port,chans", SHAPES)
@pytest.mark.parametrize("bound,kw", BOUNDS)
async def test_port_and_chan_seek_both_columns(store, label, port, chans, bound, kw) -> None:
    for order in ("desc", "asc"):
        plan = _plan(store, lambda o=order: store.query_lines(
            port=port, chans=chans, limit=5, order=o, **kw))
        assert any("idx_lines_port_chan_id" in r and "port=?" in r and "chan=?" in r
                   for r in plan), f"{label} {bound} {order}: {plan}"
    if bound in ("none", "id_to", "last_ms"):
        cplan = _plan(store, lambda: store.count_lines(port=port, chans=chans, **{
            k: v for k, v in kw.items() if k != "since_id"}))
        assert any("idx_lines_port_chan_id" in r and "port=? AND chan=?" in r
                   for r in cplan), cplan


async def test_the_single_column_filters_keep_their_own_index(store) -> None:
    assert any("idx_lines_port_id" in r for r in _plan(
        store, lambda: store.query_lines(port="quiet", limit=5)))
    assert any("idx_lines_chan_id" in r for r in _plan(
        store, lambda: store.query_lines(chans=["marker"], limit=5)))
    assert any("idx_lines_chan_id" in r for r in _plan(
        store, lambda: store.query_lines(chans=C5, limit=5)))
    plan = _plan(store, lambda: store.query_lines(port="busy", limit=5))
    assert not any("TEMP B-TREE" in r for r in plan), plan


async def test_port_and_chan_rows_are_the_filtered_rows_newest_first(store) -> None:
    rows, truncated = store.query_lines(port="busy", chans=["marker", "debug"], limit=3)
    assert [r["raw"] for r in rows] == ["m", "b19", "b18"] and truncated
    rows, _ = store.query_lines(port="quiet", chans=C5, limit=5, order="asc")
    assert [r["raw"] for r in rows] == ["q"]
    assert store.count_lines(port="busy", chans=["marker", "debug"]) == 21


async def test_the_daemon_port_selects_only_the_daemons_rows(store) -> None:
    """`port=""` is the daemon's own port (SPEC 3.5), filtered like any alias."""
    await store.add_line(ts=time.time(), port="", dir="-", chan="sys", seq=None, raw="start")
    await store.add_line(ts=time.time(), port="busy", dir="rx", chan="event", seq=None,
                         raw="!p 1 v=1", plot=[(1, None, "v", 1.0)])
    rows, _ = store.query_lines(port="", limit=50)
    assert [r["raw"] for r in rows] == ["start"]
    assert store.count_lines(port="") == 1 and store.count_lines() == 24   # control
    assert store.query_plot_channels(port="") == []
    assert await store.query_plot_channels_safe("") == []
    assert [ch["name"] for ch in await store.query_plot_channels_safe()] == ["v"]   # control


async def test_a_dir_term_selects_by_writer(store) -> None:
    assert store.count_lines(port="busy", dir="rx") == 20
    rows, _ = store.query_lines(port="busy", dir="-", limit=50)
    assert [r["raw"] for r in rows] == ["m"]


async def test_an_rx_count_is_every_row_less_the_hosts_in_any_scope(store) -> None:
    """count_lines(dir="rx") subtracts idx_lines_host from the window; it must equal the
    rows the term selects, whatever else narrows the window."""
    await store.add_line(ts=time.time(), port="busy", dir="tx", chan="cmd", seq=1, raw="c")
    await store.add_line(ts=time.time(), port="", dir="-", chan="sys", seq=None, raw="s")
    await store.add_line(ts=time.time(), port="busy", dir="rx", chan="marker", seq=None,
                         raw="!m fw")
    top = store.max_id()
    scopes = [{}, {"port": "busy"}, {"port": ""}, {"port": "nosuch"}, {"chans": ["marker"]},
              {"port": "busy", "chans": ["marker", "cmd"]}, {"id_from": 3, "id_to": top - 2},
              {"last_ms": 60_000}, {"port": "busy", "id_from": 21}]
    for kw in scopes:
        want = sum(r["dir"] == "rx" for r in store.query_lines(limit=1000, **kw)[0])
        assert store.count_lines(dir="rx", **kw) == want, kw
    assert store.count_lines(dir="rx") == 22 and store.count_lines() == 25   # control
    assert store.count_lines(dir="tx") == 1 and store.count_lines(dir="-") == 2


async def test_an_rx_count_reads_dir_only_through_the_host_index(store) -> None:
    """No index carries `dir`: a plain `dir = ?` term read every row of the window off the
    table (0.9 s against 47 ms for a whole 6M-line capture)."""
    for kw, total in [({}, "COVERING INDEX"), ({"port": "busy"}, "COVERING INDEX"),
                      ({"last_ms": 60_000}, "COVERING INDEX idx_lines_ts")]:
        plan = _plan(store, lambda kw=kw: store.count_lines(dir="rx", **kw))
        assert any("idx_lines_host" in r for r in plan), (kw, plan)
        assert any(total in r for r in plan), (kw, plan)
        assert not any(r.startswith("SCAN lines") and "INDEX" not in r for r in plan), plan


async def test_has_port_rows_is_one_index_seek(store) -> None:
    assert store.has_port_rows("quiet") and store.has_port_rows("busy")
    assert not store.has_port_rows("nosuch") and not store.has_port_rows("quie")
    plan = _plan(store, lambda: store.has_port_rows("nosuch"))
    assert any("SEARCH" in r and "port=?" in r for r in plan), plan


async def test_an_older_capture_gets_the_index_and_says_it_is_building_it(tmp_path, caplog) -> None:
    path = str(tmp_path / "old.db")
    s = Store(path)
    await s.start()   # a new file: nothing to announce
    assert not any("index" in r.getMessage() for r in caplog.records)
    await s.add_line(ts=time.time(), port="p", dir="rx", chan="debug", seq=None, raw="x")
    s._conn.execute("DROP INDEX idx_lines_port_chan_id")
    s._conn.execute("DROP INDEX idx_plot_line")
    s._conn.commit()
    await s.stop()
    caplog.clear()
    s = Store(path)
    await s.start()
    try:
        # `mcu daemon start` keeps waiting between these two notices.
        said = [r.getMessage() for r in caplog.records]
        assert any("building index idx_lines_port_chan_id, idx_plot_line once" in m
                   for m in said), said
        assert any("built index idx_lines_port_chan_id, idx_plot_line in" in m
                   for m in said), said
        assert s.query_lines(port="p", chans=["debug", "sys"], limit=5)[0]
    finally:
        await s.stop()
    caplog.clear()
    s = Store(path)
    await s.start()   # built: no second notice
    await s.stop()
    assert not any("building index" in r.getMessage() for r in caplog.records)


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
                rows = captured_plan(store, lambda k=kwargs: store.query_can_frames(**k))
                assert " cf" in rows[0], f"{label} does not drive from can_frames: {rows}"
                assert not any("TEMP B-TREE" in r for r in rows), \
                    f"{label} sorts every match before LIMIT: {rows}"
        finally:
            await store.stop()

    run_coro(run)


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

    run_coro(run)


def test_the_plan_words_the_negative_assertions_rely_on(tmp_path) -> None:
    """The positive control for every `not any("TEMP B-TREE" ...)` and `"SCAN lines"` check.

    An absence of plan text passes on any SQLite that words the step differently, so this
    build must be shown to spell a sort and a full scan the way those assertions look for.
    """
    async def run() -> None:
        store = Store(str(tmp_path / "words.db"))
        await store.start()
        try:
            rows = captured_plan(
                store, lambda: store._conn.execute("SELECT raw FROM lines ORDER BY raw").fetchall()
            )
            assert any("TEMP B-TREE" in r for r in rows), rows
            assert any("SCAN lines" in r for r in rows), rows
        finally:
            await store.stop()

    asyncio.run(run())


def test_can_frames_always_drives_from_the_frame_table(tmp_path) -> None:
    # Class 20. `lines` has no index on `port`, so a filter landing on `l` reads as
    # selective and the planner drives the join from `lines` - which also discards the
    # `ORDER BY cf.line_id DESC` index order and pushes every matching frame through a temp
    # b-tree before LIMIT can apply. Measured at 1M lines over two ports: 131 ms against
    # 0.4 ms. The plan is what is pinned, not the time: the planner picks this without
    # sqlite_stat1 (the store never runs ANALYZE), so a two-row capture reproduces it and a
    # timing test on one would not.
    async def run() -> None:
        store = Store(str(tmp_path / "canplan.db"))
        await store.start()
        try:
            for i, port in enumerate(("A", "B")):
                await await_line(store, port, i)
            cases = {
                "port": {"port": "A"},
                "port+last_ms": {"port": "A", "last_ms": 5000},
                "last_ms": {"last_ms": 5000},
                "since_id": {"since_id": 0},
                "can_id": {"can_id": 0x100},
                "unfiltered": {},
            }
            for label, kwargs in cases.items():
                rows = captured_plan(store, lambda k=kwargs: store.query_can_frames(**k))
                # The outer loop must read can_frames. Every phrasing SQLite has used names
                # the alias there ("SCAN cf", "SCAN TABLE can_frames AS cf"), and the
                # lines-driven plan names only `l`, so this discriminates on any build.
                assert " cf" in rows[0], f"{label} does not drive from can_frames: {rows}"
                assert not any("TEMP B-TREE" in r for r in rows), \
                    f"{label} sorts every match before LIMIT: {rows}"
        finally:
            await store.stop()

    asyncio.run(run())


async def await_line(store: Store, port: str, i: int) -> None:
    fut = await store.submit_line(
        ts=time.time(), port=port, dir="rx", chan="event", seq=None, raw=f"!can {i}",
        can={"tick_ms": i, "bus": 1, "can_id": 0x100 + i, "ext": False, "rtr": False,
             "dlc": 1, "data": bytes([i])},
    )
    await fut


def test_plot_channels_port_filter_does_not_scan_lines(tmp_path) -> None:
    # Class 20, the other half. The aggregate scans plot_points either way - it counts every
    # point of every channel, which is the endpoint - but `line_id IN (SELECT id FROM lines
    # WHERE port = ?)` also scanned all of `lines` to build the id list, with a bloom filter
    # and a second temp b-tree for the GROUP BY. 190 ms against 138 ms at 1M lines.
    async def run() -> None:
        store = Store(str(tmp_path / "chanplan.db"))
        await store.start()
        try:
            fut = await store.submit_line(
                ts=time.time(), port="A", dir="rx", chan="event", seq=None, raw="!p v 1",
                plot=[(1, None, "v", 1.0)],
            )
            await fut
            rows = captured_plan(store, lambda: store.query_plot_channels(port="A"))
            # Positive form, as above: `lines` must be reached by primary-key probe, never
            # scanned to build an id list. Both halves of the old plan are named.
            assert any("SEARCH li" in r and "PRIMARY KEY" in r for r in rows), rows
            assert not any("BLOOM" in r for r in rows), rows
            # And the filter still selects: the unfiltered call is the control.
            assert store.query_plot_channels(port="B") == []
            assert [c["name"] for c in store.query_plot_channels(port="A")] == ["v"]
        finally:
            await store.stop()

    asyncio.run(run())


def test_lines_port_filter_seeks_rather_than_scans(tmp_path) -> None:
    # Class 20. `port` had no index of its own, so `/lines?port=` with no `chan` planned as
    # a scan of the whole table btree, and query_lines_safe runs it inline on the event loop
    # because only a `match`-bearing query is offloaded. A busy port hides it (the LIMIT
    # fills from the newest rows); a quiet one pays it in full, which is the case that
    # matters, because a board silent while idle still gets polled. Measured at 1M rows with
    # no ANALYZE: 0.3 ms busy against 80 ms quiet, linear from there.
    #
    # The plan is pinned rather than the time: the planner chooses this with no sqlite_stat1
    # (the store never runs ANALYZE, so that is the shipped condition), which a two-row
    # capture reproduces and a timing test would need bulk data to see. Asserted positively
    # - the index is named in the plan - because asserting the absence of "SCAN" passes on
    # any SQLite that words its output differently.
    async def run() -> None:
        store = Store(str(tmp_path / "portplan.db"))
        await store.start()
        try:
            for i, port in enumerate(("busy", "quiet")):
                fut = await store.submit_line(
                    ts=time.time(), port=port, dir="rx", chan="debug", seq=None, raw=f"l{i}"
                )
                await fut
            assert not store._conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_stat1'"
            ).fetchall(), "the store must never ANALYZE; the shipped plan is the statless one"

            rows = captured_plan(store, lambda: store.query_lines(port="quiet", limit=200))
            assert any("idx_lines_port_id" in r for r in rows), \
                f"/lines?port= does not seek on the port index: {rows}"
            assert not any("TEMP B-TREE" in r for r in rows), \
                f"/lines?port= sorts every match before LIMIT: {rows}"

            # And the combination, which the first version of this test did not cover and
            # the fix-diff leg caught: with both columns indexed and no stats, the planner
            # took the port index and discarded the chan seek. `chan` is the selective side
            # (one board, many channels), measured at 319 ms against 0.09 ms on the loop.
            rows = captured_plan(
                store, lambda: store.query_lines(port="quiet", chans=["marker"], limit=200)
            )
            assert any("idx_lines_port_chan_id" in r for r in rows), \
                f"/lines?port=&chan= does not seek on the port+chan index: {rows}"

            # count_lines takes the same pair through the same assembler and was the one
            # caller that did not ask for the de-optimisation, so it kept the defect after
            # query_lines was fixed: 95 ms against 0.04 ms at 300k rows on the match pool.
            # The class is closed here by pinning every combination, not just the pair.
            for label, kwargs, wanted in (
                ("port", {"port": "quiet"}, "idx_lines_port_id"),
                ("chan", {"chans": ["marker"]}, "idx_lines_chan_id"),
                ("port+chan", {"port": "quiet", "chans": ["marker"]}, "idx_lines_port_chan_id"),
                ("last_ms", {"last_ms": 5000}, "idx_lines_ts"),
            ):
                rows = captured_plan(store, lambda k=kwargs: store.count_lines(**k))
                assert any(wanted in r for r in rows), \
                    f"count_lines {label} does not seek on {wanted}: {rows}"
        finally:
            await store.stop()

    asyncio.run(run())


def test_a_last_ms_window_seeks_by_id_rather_than_reading_the_table(tmp_path) -> None:
    # Class 20. `ts >= ?` alone is not sargable for a query ordered by id: SQLite reads the
    # table btree backwards and stops early only when the window really holds `limit+1`
    # rows, so a QUIET window reads the whole table - 46 ms against 0.6 ms at 300k rows,
    # inline on the event loop, the same busy/quiet asymmetry idx_lines_port_id was added
    # for. Resolving the window's floor to an id gives every reader a primary-key range.
    #
    # Both windows are pinned because only the empty one exposed it, and both are driven
    # through the reader rather than a hand-written query: the anchor SELECT that resolves
    # the floor is part of what is being asserted.
    async def run() -> None:
        store = Store(str(tmp_path / "windowplan.db"))
        await store.start()
        try:
            for i, port in enumerate(("busy", "quiet")):
                fut = await store.submit_line(
                    ts=time.time(), port=port, dir="rx", chan="debug", seq=None, raw=f"l{i}"
                )
                await fut
            assert not store._conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_stat1'"
            ).fetchall(), "the store must never ANALYZE; the shipped plan is the statless one"

            def plan_and_rows(label: str) -> None:
                plan = captured_plan(
                    store, lambda: store.query_lines(last_ms=60_000, limit=200)
                )
                assert any("PRIMARY KEY" in r and "rowid>" in r for r in plan), \
                    f"/lines?last_ms= ({label}) reads the table rather than a range: {plan}"

            plan_and_rows("busy")
            assert len(store.query_lines(last_ms=60_000, limit=200)[0]) >= 2

            # The empty window is the expensive one, and the bound has to survive having
            # nothing to point at: one past the newest id, not no bound at all.
            store._conn.execute("UPDATE lines SET ts = ts - 999999")
            store._conn.commit()
            plan_and_rows("quiet")
            assert store.query_lines(last_ms=60_000, limit=200)[0] == []
            # And that bound is past the newest id, not `>= 1`: the plan reads the same
            # either way, while a floor of 1 leaves the whole table inside the range.
            assert store._window_id_floor(time.time()) == store.max_id() + 1
        finally:
            await store.stop()

    asyncio.run(run())


def test_since_ts_seeks_by_id_rather_than_scanning_the_table(tmp_path) -> None:
    # Class 20, the `last_ms` shape one selector over. `/lines?since_ts=` appended a bare
    # `ts > ?`, which under `ORDER BY id DESC` planned as a full reverse scan of the table
    # btree: 23.3 ms at 500k rows with zero matches, linear in table size, on the event loop
    # (query_lines_safe offloads only a match-bearing query). Polling with a recent ts is the
    # natural use and is exactly the zero-match case. Resolving the ts to an id floor through
    # idx_lines_ts gives it the primary-key range every other window read already rides.
    #
    # Every combination is pinned, not the motivating case alone: the id term composes with
    # port, chan and the session bounds, and any of them could take the plan somewhere else.
    async def run() -> None:
        store = Store(str(tmp_path / "sincetsplan.db"))
        await store.start()
        try:
            for i, port in enumerate(("busy", "quiet")):
                fut = await store.submit_line(
                    ts=time.time(), port=port, dir="rx",
                    chan="marker" if i else "debug", seq=None, raw=f"l{i}"
                )
                await fut
            assert not store._conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_stat1'"
            ).fetchall(), "the store must never ANALYZE; the shipped plan is the statless one"

            # The id bound is named in every case, not just the index: with `port` or `chan`
            # the planner already reached an index before the fix, and walked the whole of it
            # backwards for want of a floor. Naming the index alone would pass on that.
            cut = time.time()
            # The loop connection carries no `regexp` (every live match path builds its own,
            # because the budget arms on first use and never re-arms), and this test calls
            # query_lines directly to keep the statement on the traced connection.
            store._conn.create_function("regexp", 2, _make_regexp(), deterministic=True)
            for label, kwargs, wanted in (
                ("since_ts", {}, ("PRIMARY KEY", "rowid>")),
                ("since_ts+port", {"port": "quiet"}, ("idx_lines_port_id", "id>")),
                ("since_ts+chan", {"chans": ["marker"]}, ("idx_lines_chan_id", "id>")),
                ("since_ts+port+chan",
                 {"port": "quiet", "chans": ["marker"]}, ("idx_lines_port_chan_id", "id>")),
                ("since_ts+match", {"match": "l"}, ("PRIMARY KEY", "rowid>")),
                ("since_ts+session", {"id_from": 1, "id_to": 5}, ("PRIMARY KEY", "rowid>")),
                ("since_ts+last_ms", {"last_ms": 60_000}, ("PRIMARY KEY", "rowid>")),
                ("since_ts+asc", {"order": "asc"}, ("PRIMARY KEY", "rowid>")),
            ):
                plan = captured_plan(
                    store,
                    lambda k=kwargs: store.query_lines(since_ts=cut, limit=200, **k),
                )
                # Positive form (class 21's vocabulary note): the bound the planner reached
                # is named. Every phrasing SQLite has used spells a seek "SEARCH", and an
                # unbounded read names no bound at all.
                assert any(
                    "SEARCH" in r and all(w in r for w in wanted) for r in plan
                ), f"/lines?since_ts= ({label}) is not a bounded seek on {wanted}: {plan}"
                assert not any("TEMP B-TREE" in r for r in plan), \
                    f"/lines?since_ts= ({label}) sorts every match before LIMIT: {plan}"

            # The anchor SELECT is itself the thing that must not scan, and it is a separate
            # statement, so it is explained on its own rather than through the query.
            anchor_plan = [
                str(r[3]) for r in store._conn.execute(
                    "EXPLAIN QUERY PLAN SELECT id FROM lines WHERE ts < ? ORDER BY ts DESC LIMIT 1",
                    (cut,),
                )
            ]
            assert any("idx_lines_ts" in r for r in anchor_plan), anchor_plan
            # And with everything older than the cut by more than the slack, the bound is
            # one past the newest id rather than no bound: that keeps an empty window off
            # the table btree.
            assert store._window_id_floor(cut + WINDOW_TS_SLACK_S + 1) == store.max_id() + 1
        finally:
            await store.stop()

    asyncio.run(run())


def test_since_ts_keeps_its_strictly_greater_boundary(tmp_path) -> None:
    # The correctness half of the anchor above: the id floor is derived with the same strict
    # comparison the term uses, so rows sharing the given ts stay excluded and the first row
    # past it stays included. A `>=` anchor would pass every plan assertion while quietly
    # admitting the boundary row through the id bound, and a `>` term over a `>=` floor is
    # the harmless direction that hides the reverse mistake, so both edges are named.
    async def run() -> None:
        store = Store(str(tmp_path / "sincetsedge.db"))
        await store.start()
        try:
            # Explicit ts values, so the boundary is derived from the data rather than from
            # two clock reads (class 21), and three rows share the cut exactly. Anchored at
            # `now` because a ts far in the past is what the retention sweep exists to delete.
            base = time.time()
            for i, ts in enumerate((base, base, base, base + 0.5, base + 1.0)):
                fut = await store.submit_line(
                    ts=ts, port="p", dir="rx", chan="debug", seq=None, raw=f"l{i}"
                )
                await fut
            ids = {r["raw"]: r["id"] for r in store.query_lines(limit=100, order="asc")[0]}

            got = [r["raw"] for r in store.query_lines(since_ts=base, limit=100)[0]]
            assert got == ["l4", "l3"], f"since_ts is no longer strictly greater: {got}"
            assert store._window_id_floor(base) <= ids["l0"]
            # Just below the shared ts admits all five; the exact ts excludes the three.
            assert len(store.query_lines(since_ts=base - 0.001, limit=100)[0]) == 5
            # And past the newest row, nothing - the case the anchor makes cheap.
            assert store.query_lines(since_ts=base + 1.0, limit=100)[0] == []
        finally:
            await store.stop()

    asyncio.run(run())


def test_the_age_sweep_does_not_read_the_table_when_nothing_has_expired(tmp_path) -> None:
    # Class 20, on the one statement in the store that deletes by age. `ORDER BY id` made
    # the planner take the table btree and read every `raw` blob; the LIMIT cuts that short
    # only when rows really are expired, and nothing expired is the steady state of a
    # capture inside its retention window. That case scanned the whole table on the loop
    # every hourly sweep: 45 ms at 300k rows, ~0.4 s at 1M, uninterruptible.
    #
    # Both variants are pinned - the floored delete carries an extra `id < ?` term and had
    # the same plan - and the DELETE itself is explained, not its subselect.
    async def run() -> None:
        store = Store(str(tmp_path / "sweepplan.db"))
        await store.start()
        try:
            for i in range(2):
                await add_sys(store, f"ambient {i}")
            await store.start_session("protected")
            await add_sys(store, "inside the run")
            assert not store._conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_stat1'"
            ).fetchall(), "the store must never ANALYZE; the shipped plan is the statless one"
            cutoff = time.time() - 86400
            floor_id = store.retention_floor_id()

            for label, floor in (("no floor", None), ("floored", floor_id or 1)):
                rows = captured_plan(
                    store,
                    lambda f=floor: store._delete_expired_chunk(cutoff, 5000, f),
                    keyword="DELETE",
                )
                assert any("idx_lines_ts" in r for r in rows), \
                    f"the age sweep ({label}) does not seek expired rows by ts: {rows}"
                assert not any("TEMP B-TREE" in r for r in rows), \
                    f"the age sweep ({label}) sorts the whole expired set: {rows}"

            # Still deletes oldest-first, and still stops at the floor: the plan is only
            # worth pinning if the delete it belongs to is right.
            store.set_min_sessions(1)
            store._retention_days = 0
            store._conn.execute("UPDATE lines SET ts = ts - 999999")
            store._conn.commit()
            assert await store._sweep_retention_async() > 0
            rows, _ = store.query_lines(limit=1000, order="asc")
            assert rows and min(r["id"] for r in rows) >= store.retention_floor_id()
        finally:
            await store.stop()

    asyncio.run(run())
