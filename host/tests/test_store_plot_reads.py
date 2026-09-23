"""Plot series and plot export reads: bounded work, and no read snapshot held open.

`/plot/series` with no window numbered every point of the channel before its LIMIT, and
`/plot/export` held one cursor across every yield (an aborted download pinned the WAL for
the life of the daemon) after sorting the whole selection before its first byte.
"""

from __future__ import annotations

import time

from mcuscope import protocol as p
from mcuscope import store as store_mod
from mcuscope.store import Store


def _pt(tick: int, name: str, value: float) -> p.PlotPoint:
    return (tick, "0", name, value)


async def _store(tmp_path, lines: int, names=("b", "a")) -> Store:
    store = Store(str(tmp_path / "plots.db"))
    await store.start()
    for i in range(lines):
        await store.submit_line(ts=time.time(), port="board", dir="rx", chan="event", seq=None,
                                raw="!p", plot=[_pt(i, n, i + k) for k, n in enumerate(names)])
    await store.drain_writes()
    return store


def _vm_steps(conn, run) -> int:
    steps = [0]

    def tick() -> int:
        steps[0] += 1
        return 0

    conn.set_progress_handler(tick, 100)
    try:
        run()
    finally:
        conn.set_progress_handler(None, 0)
    return steps[0] * 100


# The pre-fix statement, kept as the reference for what the rows must be.
_OLD_SERIES = (
    "SELECT line_id, ts, tick_ms, value FROM ("
    "  SELECT line_id, ts, tick_ms, value, rn,"
    "    ROW_NUMBER() OVER (PARTITION BY (rn - 1) / ? ORDER BY value, rn) AS lo,"
    "    ROW_NUMBER() OVER (PARTITION BY (rn - 1) / ? ORDER BY value DESC, rn) AS hi"
    "  FROM (SELECT pp.line_id, l.ts, pp.tick_ms, pp.value,"
    "        ROW_NUMBER() OVER (ORDER BY pp.line_id DESC) AS rn"
    "        FROM plot_points pp JOIN lines l ON l.id = pp.line_id WHERE pp.name = ?)"
    "  WHERE rn <= ?) WHERE lo = 1 OR hi = 1 ORDER BY line_id"
)


async def test_series_work_is_bounded_by_the_limit_not_the_history(tmp_path) -> None:
    store = await _store(tmp_path, 3000, names=("v",))
    try:
        few = _vm_steps(store._conn, lambda: store.query_plot_series(name="v", limit=10))
        assert few < 2_000, f"{few} VM steps for 10 of 3000 points: the history was walked"
        rows = store.query_plot_series(name="v", limit=10)
        assert [r["value"] for r in rows] == [float(i) for i in range(2990, 3000)]
        for decimate, limit in ((1, 50), (4, 50), (7, 3000), (3, 10_000)):
            got = [dict(r) for r in store.query_plot_series(
                name="v", limit=limit, decimate=decimate)]
            ref = [dict(r) for r in store._conn.execute(
                _OLD_SERIES, (decimate, decimate, "v", limit)).fetchall()]
            if decimate == 1:
                ref = ref[-limit:]
            assert got == ref, (decimate, limit)
    finally:
        await store.stop()


async def test_export_rows_are_ordered_across_pages(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store_mod, "_EXPORT_CHUNK", 3)
    store = Store(str(tmp_path / "pages.db"))
    await store.start()
    try:
        widths = [1, 2, 5, 3, 1, 7, 2, 2]   # a 7-point line is wider than a page
        for i, w in enumerate(widths):
            names = [f"n{(w - k) % 10}" for k in range(w)]   # declared out of name order
            await store.add_line(ts=time.time(), port="board", dir="rx", chan="event",
                                 seq=None, raw="!p",
                                 plot=[_pt(i, n, float(k)) for k, n in enumerate(names)])
        wanted = sorted({f"n{k}" for k in range(10)})
        got = [(r["line_id"], r["name"]) for r in store.iter_plot_export(names=wanted)]
        ref = [(r[0], r[1]) for r in store._conn.execute(
            "SELECT line_id, name FROM plot_points ORDER BY line_id, name")]
        assert got == ref and len(got) == sum(widths)
    finally:
        await store.stop()


async def test_an_export_holds_no_read_snapshot_between_pages(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store_mod, "_EXPORT_CHUNK", 10)
    store = await _store(tmp_path, 200)
    try:
        rows = store.iter_plot_export(names=["a", "b"])
        first = next(rows)   # the generator is now parked mid-export, as a stalled client is
        for i in range(50):
            await store.submit_line(ts=time.time(), port="board", dir="rx", chan="event",
                                    seq=None, raw="!p", plot=[_pt(i, "a", -1.0)])
        await store.drain_writes()
        busy, log, done = store._conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        assert log > 0 and done == log, f"a parked export pinned the WAL: {done} of {log}"
        rest = list(rows)
        assert first["line_id"] == 1 and len(rest) == 399, "the export ran past its start"
        assert all(r["value"] != -1.0 for r in rest)
    finally:
        await store.stop()


async def test_the_export_walks_line_order_without_sorting_the_selection(tmp_path) -> None:
    store = await _store(tmp_path, 20)
    try:
        seen: list[str] = []
        opened = store._open_export_conn

        def traced():
            conn = opened()
            conn.set_trace_callback(seen.append)
            return conn

        store._open_export_conn = traced
        list(store.iter_plot_export(names=["a", "b"], last_ms=60_000))
        export = [s for s in seen if "FROM plot_points pp" in s][-1]
        plan = [str(r[3]) for r in store._conn.execute("EXPLAIN QUERY PLAN " + export)]
        assert any("idx_plot_line" in r and "line_id>" in r for r in plan), plan
        assert not any("TEMP B-TREE" in r for r in plan), plan
    finally:
        await store.stop()


async def test_the_wal_size_limit_is_set(tmp_path) -> None:
    store = Store(str(tmp_path / "wal.db"))
    await store.start()
    try:
        limit = store._conn.execute("PRAGMA journal_size_limit").fetchone()[0]
        assert limit == 64 * 1024 * 1024
    finally:
        await store.stop()
