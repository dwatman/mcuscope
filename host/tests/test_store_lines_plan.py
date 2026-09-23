"""Plans for `/lines` and `count_lines` filtered by `port` and `chan` together (class 20).

Neither single-column index serves both a quiet port with a busy channel and a busy port
with a rare one: whichever the planner took, the other shape walked every row of the busy
side on the event loop (7.7 s for `port=aux&chan=debug` at 6M lines). The statements are
taken off the trace callback, so the plan pinned is the one the store issues, on a capture
with no sqlite_stat1 (the shipped condition; a few rows reproduce the planner's choice).
"""

from __future__ import annotations

import time

import pytest

from mcuscope.store import Store

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
