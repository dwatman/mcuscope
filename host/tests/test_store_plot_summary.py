"""The plot channel summary: deletes subtract, the rebuild stays off a per-point join.

Every delete used to mark the summary dirty and the next read rebuilt it from all of
`plot_points`, joining each point to its line for the port (15-22 s at 4.4M points), after
every retention chunk, size trim and purge.
"""

from __future__ import annotations

import asyncio
import threading
import time

from mcuscope import protocol as p
from mcuscope.store import Store


def _pt(tick: int, name: str, value: float) -> p.PlotPoint:
    return (tick, "0", name, value)


async def _add(store: Store, port: str, plot) -> dict:
    return await store.add_line(ts=time.time(), port=port, dir="rx", chan="event", seq=None,
                                raw="!p", plot=plot)


def _fields(summary) -> dict:
    return {k: (s.sid, s.last_value, s.last_tick, s.last_ts, s.last_line_id, s.count)
            for k, s in summary.items()}


async def _mixed(tmp_path) -> Store:
    """Three ports; the busiest by lines is not the busiest for every name."""
    store = Store(str(tmp_path / "plots.db"))
    await store.start()
    await store.query_plot_channels_safe()    # settle the startup rebuild on an empty file
    for i in range(40):
        await _add(store, "busy", [_pt(i, "temp", i), _pt(i, "rpm", 10 * i)])
    for i in range(6):
        await _add(store, "aux", [_pt(i, "temp", -i), _pt(i, "aux_only", i),
                                  _pt(i, "aux_only", i + 0.5)])   # a name twice in one line
        await store.add_line(ts=time.time(), port="aux", dir="rx", chan="debug", seq=None,
                             raw="no points")
    await _add(store, "busy", [_pt(99, "rpm", 990)])
    await _add(store, "", [_pt(1, "hostside", 1)])
    return store


def _counting(store: Store) -> list[int]:
    calls = [0]
    real = store._scan_plot_summary

    def scan(**kw):
        calls[0] += 1
        return real(**kw)

    store._scan_plot_summary = scan
    return calls


def _rescanned(store: Store) -> dict:
    """A from-scratch rebuild on the loop connection, not counted as a read's rebuild."""
    return _fields(Store._scan_plot_summary(store, conn=store._conn, high=store.max_id()))


async def test_the_rebuild_equals_what_the_writer_folded(tmp_path) -> None:
    store = await _mixed(tmp_path)
    try:
        folded = _fields(store._plot_summary)
        assert folded[("aux", "aux_only")][1] == 5.5, "the writer keeps a line's last point"
        store._plot_dirty = True
        await store.query_plot_channels_safe()
        assert _fields(store._plot_summary) == folded
        for port in (None, "busy", "aux", "", "nosuch"):
            names = {c["name"] for c in await store.query_plot_channels_safe(port)}
            assert names == {c["name"] for c in store.query_plot_channels(port=port)}, port
    finally:
        await store.stop()


async def test_no_whole_table_walk_of_plot_points_joins_lines(tmp_path) -> None:
    store = await _mixed(tmp_path)
    try:
        seen: list[str] = []
        store._conn.set_trace_callback(seen.append)
        store._scan_plot_summary(conn=store._conn, high=store.max_id())
        store._conn.set_trace_callback(None)
        plans = [[str(r[3]) for r in store._conn.execute("EXPLAIN QUERY PLAN " + s)]
                 for s in seen if s.lstrip().upper().startswith(("SELECT", "WITH"))]
        assert any(any("COVERING INDEX idx_plot_name_line" in r for r in plan)
                   and not any("lines" in r or " li " in r or " l " in r for r in plan)
                   for plan in plans), plans
        for plan in plans:
            walks = [r for r in plan if "plot_points" in r or " pp " in r]
            whole = [r for r in walks if "name=?" not in r and "line_id=?" not in r]
            joined = [r for r in plan if "lines" in r or " li " in r or " l " in r]
            assert not (whole and joined), f"a whole-table walk joins lines: {plan}"
        # The busiest port's counts come by subtraction: its lines are never walked.
        assert not any("idx_lines_port_id" in s and "'busy'" in s for s in seen), seen
        assert any("idx_lines_port_id" in s and "'aux'" in s for s in seen), seen
    finally:
        await store.stop()


async def test_deletes_subtract_instead_of_rescanning(tmp_path) -> None:
    store = await _mixed(tmp_path)
    try:
        scans = _counting(store)
        await store.delete_range(1, 5)                      # oldest busy lines
        await store.query_plot_channels_safe()
        assert _fields(store._plot_summary) == _rescanned(store)
        await store.delete_before_ts(time.time() - 3600)    # nothing that old
        store._delete_oldest_chunk(3)                       # the size trim's chunk
        await store.query_plot_channels_safe()
        assert _fields(store._plot_summary) == _rescanned(store)
        assert store._plot_summary[("busy", "temp")].count == 32
        assert scans[0] == 0
        aux_ids = [r["id"] for r in store.query_lines(port="aux", limit=100)[0]]
        await store.delete_range(min(aux_ids), max(aux_ids))   # every aux_only point
        assert "aux_only" not in {c["name"] for c in await store.query_plot_channels_safe()}
        assert "aux" not in await store.plot_ports_safe()
        assert scans[0] == 0
    finally:
        await store.stop()


async def test_deleting_a_channels_newest_point_rescans_it(tmp_path) -> None:
    store = await _mixed(tmp_path)
    try:
        scans = _counting(store)
        newest = next(c for c in store.query_plot_channels(port="busy") if c["name"] == "rpm")
        await store.delete_range(newest["last_line_id"], newest["last_line_id"])
        rpm = next(c for c in await store.query_plot_channels_safe("busy") if c["name"] == "rpm")
        assert scans[0] == 1
        assert rpm["last_value"] == 390 and rpm["count"] == 40
    finally:
        await store.stop()


async def test_concurrent_reads_of_a_dirty_summary_share_one_rebuild(tmp_path) -> None:
    store = await _mixed(tmp_path)
    try:
        scans = _counting(store)
        store._plot_dirty = True
        a, b = await asyncio.gather(store.query_plot_channels_safe(),
                                    store.query_plot_channels_safe())
        assert a == b and _fields(store._plot_summary) == _rescanned(store)
        assert scans[0] == 1, "the reader queued on the lock rescanned instead of waiting"
    finally:
        await store.stop()


async def test_a_delete_during_a_rebuild_is_not_lost(tmp_path) -> None:
    # The rebuild's scan predates the delete, and the summary being rebuilt is not the one
    # the delete could subtract from: it has to leave the summary due for another scan.
    store = await _mixed(tmp_path)
    try:
        scanned, release = threading.Event(), threading.Event()
        real = store._scan_plot_summary

        def parked(**kw):
            out = real(**kw)
            scanned.set()
            release.wait(10)
            return out

        store._scan_plot_summary = parked
        store._plot_dirty = True
        reading = asyncio.create_task(store.query_plot_channels_safe())
        assert await asyncio.to_thread(scanned.wait, 10)
        await store.delete_range(1, 5)
        release.set()
        await reading
        await store.query_plot_channels_safe()
        assert _fields(store._plot_summary) == _rescanned(store)
    finally:
        await store.stop()
