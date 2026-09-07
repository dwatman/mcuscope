"""The ingest fast paths: the plot channel summary, the cached read connection, the sync
submit, per-batch fan-out, the commit-failure resync and the tokenized decoders.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time

import pytest

from mcuscope import protocol as p
from mcuscope import server
from mcuscope import store as store_mod
from mcuscope.store import MatchBudgetExceeded, Store, StoreError


def _pt(tick: int, name: str, value: float, sid: str | None = "0") -> p.PlotPoint:
    return (tick, sid, name, value)


async def _add(store: Store, port: str, raw: str = "!p 1 x=1", plot=None, chan="event"):
    return await store.add_line(
        ts=time.time(), port=port, dir="rx", chan=chan, seq=None, raw=raw, plot=plot
    )


def _sql_channels(store: Store, port: str | None = None) -> list[dict]:
    return store.query_plot_channels(port=port)


async def _summary_channels(store: Store, port: str | None = None) -> list[dict]:
    return await store.query_plot_channels_safe(port=port)


# -- plot channel summary -------------------------------------------------------------


async def test_summary_matches_sql_across_mixed_ports(tmp_path) -> None:
    store = Store(str(tmp_path / "mixed.db"))
    await store.start()
    try:
        for i in range(30):
            port = ("A", "B", "C")[i % 3]
            await _add(store, port, plot=[_pt(i, "temp", float(i)), _pt(i, f"only{port}", 1.0)])
        for port in (None, "A", "B", "C", "nosuch"):
            assert await _summary_channels(store, port) == _sql_channels(store, port), port
        # A shared name reports the port of its newest sample and the merged count.
        temp = next(c for c in await _summary_channels(store) if c["name"] == "temp")
        assert temp["count"] == 30 and temp["port"] == "C" and temp["last_value"] == 29.0
    finally:
        await store.stop()


async def test_summary_is_rebuilt_after_a_purge_and_after_retention(tmp_path) -> None:
    store = Store(str(tmp_path / "purge.db"))
    await store.start()
    try:
        ids = []
        for i in range(40):
            row = await _add(store, "A" if i % 2 else "B", plot=[_pt(i, "v", float(i))])
            ids.append(row["id"])
        assert await _summary_channels(store) == _sql_channels(store)
        # A hole in the middle, then the oldest end, then everything: each one must be
        # reflected on the next read, unfiltered and per port.
        removed = await store.delete_range(ids[10], ids[19])
        assert removed == 10
        for port in (None, "A", "B"):
            assert await _summary_channels(store, port) == _sql_channels(store, port)
        assert store._delete_oldest_chunk(5) == 5
        assert await _summary_channels(store) == _sql_channels(store)
        assert await store.delete_range(ids[0], ids[-1]) == 25
        assert await _summary_channels(store) == [] == _sql_channels(store)
        # And the summary keeps counting once the capture refills.
        await _add(store, "A", plot=[_pt(1, "fresh", 2.0)])
        assert await _summary_channels(store) == _sql_channels(store)
        assert [c["name"] for c in await _summary_channels(store)] == ["fresh"]
    finally:
        await store.stop()


async def test_summary_rebuild_keeps_rows_written_during_the_scan(tmp_path) -> None:
    """The scan covers ids <= high; what the writer lands meanwhile must not be lost or
    counted twice."""
    store = Store(str(tmp_path / "race.db"))
    await store.start()
    try:
        for i in range(10):
            await _add(store, "A", plot=[_pt(i, "v", float(i))])
        real_scan = store._scan_plot_summary
        landed = asyncio.Event()

        def slow_scan(conn=None, high=0):
            # Runs on a worker: block until the loop has written more rows, THEN scan, so
            # the rows written meanwhile are inside the scan's reach and only the
            # `line_id <= high` bound keeps them out of it.
            while not landed.is_set():
                time.sleep(0.005)
            return real_scan(conn=conn, high=high)

        store._scan_plot_summary = slow_scan
        store._plot_dirty = True
        rebuild = asyncio.create_task(store.query_plot_channels_safe())
        await asyncio.sleep(0.05)   # the scan is now blocked on a worker thread
        for i in range(10, 15):
            await _add(store, "A", plot=[_pt(i, "v", float(i)), _pt(i, "new", 1.0)])
        landed.set()
        chans = {c["name"]: c for c in await rebuild}
        assert chans["v"]["count"] == 15 and chans["v"]["last_value"] == 14.0
        assert chans["new"]["count"] == 5
        assert await _summary_channels(store) == _sql_channels(store)
    finally:
        await store.stop()


async def test_summary_is_not_polluted_by_a_batch_whose_commit_failed(tmp_path) -> None:
    store = Store(str(tmp_path / "commitfail.db"))
    await store.start()
    try:
        await _add(store, "A", plot=[_pt(1, "v", 1.0)])
        real_conn = store._conn

        class BrokenCommitOnce:
            calls = 0

            def commit(self):
                self.calls += 1
                if self.calls == 1:
                    raise sqlite3.OperationalError("disk I/O error")
                real_conn.commit()

            def __getattr__(self, name):
                return getattr(real_conn, name)

        store._conn = BrokenCommitOnce()
        fut = await store.submit_line(
            ts=time.time(), port="A", dir="rx", chan="event", seq=None, raw="!p 2 v=2",
            plot=[_pt(2, "v", 2.0)],
        )
        with pytest.raises(StoreError, match="commit failed"):
            await asyncio.wait_for(fut, 2)
        store._conn = real_conn
        assert store.write_errors == 1
        # The rolled-back id is handed out again, not skipped (the resync this test is for).
        row = await _add(store, "A", plot=[_pt(3, "v", 3.0)])
        assert row["id"] == 2
        assert store.max_id() == 2 == store._max_id_sql(store._conn)
        chans = await _summary_channels(store)
        assert [(c["name"], c["count"], c["last_value"]) for c in chans] == [("v", 2, 3.0)]
        assert chans == _sql_channels(store)
    finally:
        await store.stop()


async def test_row_by_row_fallback_resyncs_the_sequence_from_sql(tmp_path) -> None:
    """A collision on the daemon's next id (another writer) falls back to SQLite-assigned
    ids and the sequence is re-read from the file, not from itself."""
    store = Store(str(tmp_path / "collide.db"))
    await store.start()
    try:
        await _add(store, "A", plot=[_pt(1, "v", 1.0)])
        store._conn.execute(
            "INSERT INTO lines(id, ts, port, dir, chan, seq, raw) "
            "VALUES(?, 0, 'other', 'rx', 'debug', NULL, 'foreign')", (store._next_id,)
        )
        store._conn.commit()
        row = await _add(store, "A", plot=[_pt(2, "v", 2.0)])
        assert row["id"] == 3
        assert store.max_id() == 3 == store._max_id_sql(store._conn)
        assert (await _add(store, "A"))["id"] == 4
        assert await _summary_channels(store) == _sql_channels(store)
    finally:
        await store.stop()


async def test_query_plot_channels_safe_does_not_scan_between_deletes(tmp_path) -> None:
    store = Store(str(tmp_path / "noscan.db"))
    await store.start()
    try:
        await _add(store, "A", plot=[_pt(1, "v", 1.0)])
        await _summary_channels(store)   # first read seeds the summary
        scans = 0
        real = store._scan_plot_summary

        def counting(**kw):
            nonlocal scans
            scans += 1
            return real(**kw)

        store._scan_plot_summary = counting
        for i in range(5):
            await _add(store, "A", plot=[_pt(i, "v", float(i))])
            assert (await _summary_channels(store))[0]["count"] == i + 2
        assert scans == 0
        await store.delete_range(1, 1)
        await _summary_channels(store)
        assert scans == 1
    finally:
        await store.stop()


# -- cached read connection -----------------------------------------------------------


async def test_cached_read_conn_survives_a_query_error_and_is_closed_at_stop(tmp_path) -> None:
    store = Store(str(tmp_path / "readconn.db"))
    await store.start()
    for _ in range(3):
        await _add(store, "A", raw="a" * 60, chan="debug")
    seen: list[sqlite3.Connection] = []
    errors: list[Exception] = []

    def worker() -> None:
        def bad(conn):
            seen.append(conn)
            conn.execute("SELECT * FROM no_such_table")

        def good(conn):
            seen.append(conn)
            return conn.execute("SELECT COUNT(*) FROM lines").fetchone()[0]

        try:
            store._read_on_private_conn(bad)
        except sqlite3.OperationalError as exc:
            errors.append(exc)
        assert store._read_on_private_conn(good) == 3
        assert store._read_on_private_conn(good) == 3

    t = threading.Thread(target=worker)
    t.start()
    t.join(10)
    assert not t.is_alive()
    assert len(errors) == 1
    assert len(seen) == 3 and len({id(c) for c in seen}) == 1, "the error cost the connection"
    # The regex budget is re-armed per query on the same connection: a catastrophic
    # pattern is refused and the next honest match on that worker still answers.
    with pytest.raises(MatchBudgetExceeded):
        await store.query_lines_safe(match=r"(?:a{1,3}){2,40}b", limit=10)
    rows, _ = await store.query_lines_safe(match=r"^a{60}$", limit=10)
    assert len(rows) == 3
    assert store._read_conns, "match_executor workers should now hold cached connections"
    held = list(store._read_conns)
    await store.stop()
    assert not store._read_conns
    for c in held:
        with pytest.raises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")


async def test_read_conn_is_per_store_not_per_thread(tmp_path) -> None:
    """Two stores served by the same worker thread must not share a handle."""
    a = Store(str(tmp_path / "a.db"))
    b = Store(str(tmp_path / "b.db"))
    await a.start()
    await b.start()
    try:
        await _add(a, "A", chan="debug", raw="only in a")
        assert await a.count_lines_safe() == 1
        assert await b.count_lines_safe() == 0
        assert not (a._read_conns & b._read_conns)
    finally:
        await a.stop()
        await b.stop()


# -- sync submit fast path ------------------------------------------------------------


async def test_submit_nowait_refuses_a_full_queue_and_submit_line_waits(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store_mod, "_WRITE_QUEUE_MAX", 2)
    store = Store(str(tmp_path / "full.db"))
    await store.start()
    try:
        kw = dict(ts=time.time(), port="A", dir="rx", chan="debug", seq=None, raw="x")
        f1 = store.submit_line_nowait(**kw)
        f2 = store.submit_line_nowait(**kw)
        with pytest.raises(asyncio.QueueFull):
            store.submit_line_nowait(**kw)
        # The slow path suspends until the writer makes room, then lands in order.
        f3 = await asyncio.wait_for(store.submit_line(**kw), 2)
        rows = [await asyncio.wait_for(f, 2) for f in (f1, f2, f3)]
        assert [r["id"] for r in rows] == [1, 2, 3]
    finally:
        await store.stop()


async def test_submit_nowait_fails_fast_on_a_dead_writer(tmp_path) -> None:
    store = Store(str(tmp_path / "dead.db"))
    await store.start()
    await store.stop()
    store._queue = asyncio.Queue()   # a queue but no writer: the refusal is the writer check
    with pytest.raises(StoreError, match="not running"):
        store.submit_line_nowait(
            ts=time.time(), port="A", dir="rx", chan="debug", seq=None, raw="x"
        )
    assert store.write_errors == 1


# -- per-batch fan-out ----------------------------------------------------------------


async def test_batch_fanout_serialises_once_and_filters_per_row(tmp_path) -> None:
    store = Store(str(tmp_path / "fanout.db"))
    await store.start()
    try:
        as_json = store.subscribe(as_json=True)
        only_b = store.subscribe(port_filter="B")
        plain = store.subscribe()
        futs = [
            store.submit_line_nowait(
                ts=1.0, port=("A", "B")[i % 2], dir="rx", chan="debug", seq=None, raw=f"r{i}"
            )
            for i in range(4)
        ]
        rows = [await asyncio.wait_for(f, 2) for f in futs]
        got_json = [as_json.get_nowait() for _ in range(4)]
        assert all(isinstance(t, str) for t in got_json)
        assert [json.loads(t) for t in got_json] == rows
        assert got_json == [json.dumps(r, separators=(",", ":")) for r in rows]
        assert [plain.get_nowait() for _ in range(4)] == rows
        assert [only_b.get_nowait()["raw"] for _ in range(2)] == ["r1", "r3"]
        assert only_b.empty()
    finally:
        await store.stop()


async def test_batch_fanout_drop_oldest_counts_per_row(tmp_path) -> None:
    store = Store(str(tmp_path / "drop.db"))
    await store.start()
    try:
        q = store.subscribe(maxsize=2)
        futs = [
            store.submit_line_nowait(
                ts=1.0, port="A", dir="rx", chan="debug", seq=None, raw=f"r{i}"
            )
            for i in range(5)
        ]
        for f in futs:
            await asyncio.wait_for(f, 2)
        assert store.take_dropped(q) == 3
        assert [q.get_nowait()["raw"] for _ in range(2)] == ["r3", "r4"]
    finally:
        await store.stop()


# -- max_id fast path -----------------------------------------------------------------


async def test_max_id_tracks_the_writer_and_a_top_delete_still_resets_capture(tmp_path):
    store = Store(str(tmp_path / "maxid.db"))
    await store.start()
    try:
        assert store.max_id() == 0
        for _ in range(3):
            await _add(store, "A", chan="debug", raw="x")
        assert store.max_id() == 3 == store._max_id_sql(store._conn)
        before = store.capture_id
        assert await store.delete_range(3, 3) == 1
        assert store.capture_id != before, "deleting the top must still mint a new capture"
        # The sequence does not reuse the freed id, so ids stay unique for anyone holding 3.
        assert (await _add(store, "A", chan="debug", raw="y"))["id"] == 4
    finally:
        await store.stop()
    # Writer stopped: the answer comes from SQL again.
    conn = sqlite3.connect(str(tmp_path / "maxid.db"))
    conn.row_factory = sqlite3.Row
    assert store.max_id(conn) == 4


# -- tokenized decoders ---------------------------------------------------------------


@pytest.mark.parametrize("line", [
    "!can 100 - 100 DEADBEEF", "!can3 5 xr 1FFFFFFF 8", "!can 1 - 800 00", "!can bad",
    "!p 10 temp=21.5 v=3", "!p x temp=1", "!ps 0 1A 03", "!ps 9 1A 03", "!pd 0 gpio:u1:/led,irq",
])
def test_token_entry_points_agree_with_the_raw_ones(line: str) -> None:
    for raw in (line, line + "\r\n", line + "\n"):
        assert p.parse_can_event(raw) == p.parse_can_event_tokens(raw.split())
        a, b = p.PlotDecoder(), p.PlotDecoder()
        a.learn("!pd 0 gpio:u1:/led,irq")
        b.learn("!pd 0 gpio:u1:/led,irq")
        assert a.feed(raw) == b.feed_tokens(raw.split())
        assert a.points(raw) == b.points_from_tokens(raw.split())


def test_feed_tokens_learns_a_definition_from_tokens() -> None:
    d = p.PlotDecoder()
    assert d.feed_tokens("!pd 0 temp:u1".split()) is None
    assert d.feed_tokens("!ps 0 10 07".split()).points == (("temp", 7.0),)
    assert d.feed_tokens([]) is None
    assert d.points_from_tokens("!pdx 0 temp:u1".split()) is None


# -- uvicorn backpressure guard -------------------------------------------------------


def test_ws_backpressure_patch_refuses_a_protocol_without_writable(monkeypatch, caplog):
    import uvicorn.protocols.websockets.websockets_sansio_impl as impl

    class NoWritable:
        def __init__(self) -> None:
            self.other = True

    monkeypatch.setattr(impl, "WebSocketsSansIOProtocol", NoWritable)
    with caplog.at_level(logging.WARNING, logger="mcuscope.server"):
        server._enable_ws_backpressure()
    assert "no writable event" in caplog.text
    assert "pause_writing" not in vars(NoWritable)


async def test_a_full_queue_makes_the_port_wait_for_room_and_lose_nothing(tmp_path) -> None:
    """serial_link's fast path is `submit_line_nowait`; on QueueFull it must fall back to
    the awaiting `submit_line` (backpressure), not drop the line or raise into the reader."""
    from mcuscope.serial_link import SerialPort

    store = Store(str(tmp_path / "full.db"))
    await store.start()
    try:
        loop = asyncio.get_running_loop()
        port = SerialPort(store, loop, "board")
        real_nowait = store.submit_line_nowait
        calls = {"nowait": 0, "slow": 0}
        real_slow = store.submit_line

        def nowait(**kw):
            calls["nowait"] += 1
            if calls["nowait"] % 2 == 1:
                raise asyncio.QueueFull
            return real_nowait(**kw)

        async def slow(**kw):
            calls["slow"] += 1
            return await real_slow(**kw)

        store.submit_line_nowait = nowait
        store.submit_line = slow
        lines = [f"line {i}" for i in range(6)]
        await port._store_rx_batch([(time.time(), ln) for ln in lines])
        await store.drain_writes()
        rows, _ = store.query_lines(chans=["debug"], limit=100)
        assert sorted(r["raw"] for r in rows) == lines, "a QueueFull must not lose the line"
        assert calls["slow"] == 3 and calls["nowait"] == 6
        assert port.rx_dropped == 0
    finally:
        await store.stop()


async def test_a_read_retried_once_when_stop_closed_the_cached_handle(tmp_path) -> None:
    """The epoch check and the query are not atomic against stop(): a worker holding a
    handle stop() just closed retries on a fresh one instead of failing the request."""
    store = Store(str(tmp_path / "retry.db"))
    await store.start()
    try:
        await _add(store, "A", raw="hello")
        rows, _ = await store.query_lines_safe(match="hello", limit=10)
        assert len(rows) == 1
        real = store._read_conn
        raced = []

        def racy():
            conn = real()
            if not raced:
                raced.append(1)
                store._close_read_conns()   # stop() lands between the check and the query
            return conn

        store._read_conn = racy
        rows, _ = await store.query_lines_safe(match="hello", limit=10)
        assert len(rows) == 1 and raced, "the read must recover on a fresh handle"
    finally:
        await store.stop()
