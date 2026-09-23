"""Deletes and scans racing the writer, 2026-09-24 fix-diff leg: a purge bounded by its
span, a plot summary scan on one snapshot, a crashed session over an emptied capture."""

from __future__ import annotations

import time

from mcuscope.store import Store


async def _add(store: Store, ts: float, raw: str) -> dict:
    return await store.add_line(ts=ts, port="p", dir="rx", chan="debug", seq=None, raw=raw)


async def test_a_purge_keeps_rows_committed_after_its_span(tmp_path) -> None:
    store = Store(str(tmp_path / "purge.db"))
    await store.start()
    try:
        cutoff = time.time() + 30   # SPEC 3.4 allows a cutoff up to 60 s ahead
        for i in range(5):
            await _add(store, time.time(), f"old {i}")
        n, _lo, hi = store.before_ts_span(cutoff)
        for i in range(3):
            await _add(store, time.time(), f"after the span {i}")
        assert await store.delete_before_ts(cutoff, max_id=hi) == n == 5
        rows, _ = store.query_lines(order="asc")
        assert [r["raw"] for r in rows] == [f"after the span {i}" for i in range(3)]
        # Without the bound, the old behaviour: everything stamped before the cutoff.
        assert await store.delete_before_ts(cutoff) == 3
    finally:
        await store.stop()


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


def _fields(summary) -> dict:
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
            assert _fields(torn) == want, where
            assert not conn.in_transaction, "the snapshot is released"
        finally:
            conn.close()
            await store.stop()


async def test_a_crashed_auto_session_over_an_emptied_capture_is_dropped(tmp_path) -> None:
    path = str(tmp_path / "crash.db")
    store = Store(path)
    await store.start()
    session = await store.start_session("auto-x", auto=True)
    await store.delete_range(1, store.max_id())   # purge all, then a crash
    assert store.count_lines() == 0
    await store.stop()                            # the session is still open
    store = Store(path)
    await store.start()
    try:
        assert store.active_session() is None
        assert store.get_session(session["id"]) is None
    finally:
        await store.stop()
