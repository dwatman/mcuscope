"""The plot channel summary: deletes subtract, the rebuild stays off a per-point join.

Every delete used to mark the summary dirty and the next read rebuilt it from all of
`plot_points`, joining each point to its line for the port (15-22 s at 4.4M points), after
every retention chunk, size trim and purge.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from mcuscope import protocol as p
from mcuscope.store import Store
from tests.support import T0_FIXED, started_store


def query_plot_channels(store: Store, port: str | None = None) -> list[dict]:
    """The oracle for Store.query_plot_channels_safe: the same rows by a plain GROUP BY over
    plot_points, the query the summary replaced (test-only; no handler runs it)."""
    where, params = "", []
    if port is not None:
        where = "CROSS JOIN lines li ON li.id = plot_points.line_id WHERE li.port = ? "
        params = [port]
    sql = (
        "SELECT pp.name, pp.sid, pp.value AS last_value, pp.tick_ms AS last_tick, "
        "       l.ts AS last_ts, l.port AS port, "
        "       pp.line_id AS last_line_id, g.count AS count "
        "FROM (SELECT name, MAX(line_id) AS mx, COUNT(*) AS count "
        f"      FROM plot_points {where}GROUP BY name) g "
        "JOIN plot_points pp ON pp.name = g.name AND pp.line_id = g.mx "
        "JOIN lines l ON l.id = pp.line_id "
        "ORDER BY pp.name"
    )
    return [dict(r) for r in store._conn.execute(sql, params).fetchall()]


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
            assert names == {c["name"] for c in query_plot_channels(store, port=port)}, port
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
        newest = next(c for c in query_plot_channels(store, port="busy") if c["name"] == "rpm")
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


def test_plot_ports_rebuilds_after_a_delete_on_its_own(tmp_path) -> None:
    """F-30: `/plot/channels` always rebuilt through `query_plot_channels_safe` first, so a
    stale summary in `plot_ports_safe` alone was never observed."""

    async def run() -> None:
        store = await started_store(tmp_path / "ports.db")
        try:
            ids = {}
            for port in ("A", "B"):
                row = await store.add_line(
                    ts=T0_FIXED, port=port, dir="rx", chan="event", seq=None, raw="!p v=1",
                    plot=[(1, None, "v", 1.0)],
                )
                ids[port] = row["id"]
            assert await store.plot_ports_safe() == ["A", "B"]
            await store.delete_range(ids["B"], ids["B"])
            assert await store.plot_ports_safe() == ["A"], "a purged board is still listed"
        finally:
            await store.stop()

    asyncio.run(run())


class _DeleteBeforeSecondStatement:
    """A read connection that lets a delete commit before its second statement."""

    def __init__(self, real, delete) -> None:
        self.real, self.delete, self.n = real, delete, 0

    def execute(self, *a):
        self.n += 1
        if self.n == 3:   # BEGIN, the totals, then this
            self.delete()
        return self.real.execute(*a)

    def __getattr__(self, name):
        return getattr(self.real, name)


async def _plotted(tmp_path) -> Store:
    store = Store(str(tmp_path / "scan.db"))
    await store.start()
    for port, n, sign in (("busy", 40, 1.0), ("aux", 5, -1.0)):
        for i in range(n):
            await store.add_line(ts=time.time(), port=port, dir="rx", chan="event",
                                 seq=None, raw="!p", plot=[(i, "0", "temp", sign * i)])
    return store


def _count_and_last_id(summary) -> dict:
    return {k: (v.count, v.last_line_id) for k, v in summary.items()}


async def test_a_summary_scan_reads_one_snapshot(tmp_path) -> None:
    # Busy's lines are ids 1-40, aux's 41-45; the scan sees them as they were at its start.
    want = {("busy", "temp"): (40, 40), ("aux", "temp"): (5, 45)}
    for i, where in enumerate(("port = 'aux'", "port = 'busy' AND id > 30")):
        store = await _plotted(tmp_path / str(i))
        high = store.max_id()

        def delete(store=store, where=where) -> None:
            store._conn.execute(f"DELETE FROM lines WHERE {where}")
            store._conn.commit()

        conn = store._open_read_conn()
        try:
            torn = store._scan_plot_summary(
                conn=_DeleteBeforeSecondStatement(conn, delete), high=high
            )
            assert store.count_lines() < 45, "the delete committed during the scan"
            assert _count_and_last_id(torn) == want, where
            assert not conn.in_transaction, "the snapshot is released"
        finally:
            conn.close()
            await store.stop()


async def _slow_rebuild(store: Store):
    """Start a rebuild whose SQL scan blocks on a worker until the returned event is set."""
    real_scan = store._scan_plot_summary
    release = threading.Event()

    def slow_scan(conn=None, high=0):
        release.wait(10)
        return real_scan(conn=conn, high=high)

    store._scan_plot_summary = slow_scan
    store._plot_dirty = True
    rebuild = asyncio.create_task(store.query_plot_channels_safe())
    await asyncio.sleep(0.05)   # the scan is now blocked on the worker
    return rebuild, release


@pytest.mark.parametrize("reader", ["query_plot_channels_safe", "plot_ports_safe"])
def test_a_summary_read_during_a_rebuild_waits_for_it(tmp_path, reader) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "rebuild.db"))
        await store.start()
        try:
            await store.add_line(ts=time.time(), port="A", dir="rx", chan="event", seq=None,
                                 raw="!p 1 v=1", plot=[(1, None, "v", 1.0)])
            rebuild, release = await _slow_rebuild(store)
            concurrent = asyncio.create_task(getattr(store, reader)())
            await asyncio.sleep(0.05)
            release.set()
            got = await concurrent
            await rebuild
            if reader == "plot_ports_safe":
                assert got == ["A"], f"read the half-built summary: {got}"
            else:
                assert [c["name"] for c in got] == ["v"], f"read the half-built summary: {got}"
        finally:
            await store.stop()

    asyncio.run(run())


def test_a_failed_rebuild_scan_leaves_the_summary_dirty(tmp_path) -> None:
    """The rebuild cleared the flag before its scan: a scan that raised left the summary
    holding only the rows written since, and nothing rebuilt it until the next delete."""

    async def run() -> None:
        store = Store(str(tmp_path / "scanfail.db"))
        await store.start()
        try:
            await store.add_line(ts=time.time(), port="A", dir="rx", chan="event", seq=None,
                                 raw="!p 1 v=1", plot=[(1, None, "v", 1.0)])
            real_scan = store._scan_plot_summary

            def failing_scan(conn=None, high=0):
                raise OSError("disk I/O error ZZ-scan")

            store._scan_plot_summary = failing_scan
            store._plot_dirty = True
            with pytest.raises(OSError, match="ZZ-scan"):
                await store.query_plot_channels_safe()
            store._scan_plot_summary = real_scan
            got = await store.query_plot_channels_safe()
            assert [c["name"] for c in got] == ["v"], f"the half-built summary stood: {got}"
        finally:
            await store.stop()

    asyncio.run(run())
