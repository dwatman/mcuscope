"""`/can/frames` answers `next_since_id` (SPEC 3.4, owner ruling D-7): a follow on a port that
sends no frames advances on it, so each poll costs what is new rather than every frame above a
watermark that never moves."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from mcuscope.store import Store
from tests.support import mk_app, on_loop


async def _frames(store: Store, port: str, n: int) -> None:
    for i in range(n):
        fut = await store.submit_line(
            ts=time.time(), port=port, dir="rx", chan="event", seq=None, raw=f"!can {i}",
            can={"tick_ms": i, "bus": 1, "can_id": 0x100, "ext": False, "rtr": False,
                 "dlc": 1, "data": bytes([i & 0xFF])},
        )
        await fut


def _steps(store: Store, **kw) -> tuple[tuple, int]:
    count = [0]

    def tick() -> int:
        count[0] += 1
        return 0

    store._conn.set_progress_handler(tick, 1)
    try:
        page = store.can_frames_page(**kw)
    finally:
        store._conn.set_progress_handler(None, 1)
    return page, count[0]


async def test_a_quiet_port_answers_the_newest_id_and_the_next_poll_is_cheap(tmp_path) -> None:
    costs = {}
    for n in (20, 400):
        store = Store(str(tmp_path / f"cw{n}.db"))
        await store.start()
        try:
            await _frames(store, "A", n)
            quiet = await store.add_line(ts=time.time(), port="B", dir="rx", chan="debug",
                                         seq=None, raw="no frames here")
            (rows, truncated, nxt), first = _steps(store, port="B", since_id=0, limit=100)
            assert (rows, truncated, nxt) == ([], False, quiet["id"])
            (rows, _, again), costs[n] = _steps(store, port="B", since_id=nxt, limit=100)
            assert rows == [] and again == nxt
            # Positive control: the stale watermark walks every frame of the busy port.
            assert first > n
        finally:
            await store.stop()
    assert costs[400] == costs[20] and costs[20] < 50, costs


async def test_the_watermark_covers_frames_and_never_moves_backwards(tmp_path) -> None:
    store = Store(str(tmp_path / "cw.db"))
    await store.start()
    try:
        await _frames(store, "A", 3)
        rows, truncated, nxt = store.can_frames_page(port="A", limit=2)
        assert truncated and len(rows) == 2 and nxt == store.max_id()
        # A frame arriving after the answer lies above it: the next poll returns it.
        await _frames(store, "A", 1)
        rows, _, nxt2 = store.can_frames_page(port="A", since_id=nxt, limit=100)
        assert [r["line_id"] for r in rows] == [nxt2] and nxt2 > nxt
        # An id_to window covers up to its ceiling, and a watermark past MAX(id) holds.
        assert store.can_frames_page(id_to=2, limit=100)[2] == 2
        assert store.can_frames_page(since_id=10**6, limit=100)[2] == 10**6
    finally:
        await store.stop()


def test_the_endpoint_answers_next_since_id(tmp_path) -> None:
    with TestClient(mk_app(tmp_path), base_url="http://127.0.0.1") as c:
        store = c.app.state.store
        on_loop(c, _frames(store, "A", 5))
        quiet = on_loop(c, store.add_line(ts=time.time(), port="B", dir="rx", chan="debug",
                                          seq=None, raw="quiet"))
        body = c.get("/can/frames", params={"port": "B", "since_id": 0}).json()
        assert body == {"frames": [], "truncated": False, "next_since_id": quiet["id"]}
        body = c.get("/can/frames", params={"port": "A"}).json()
        assert len(body["frames"]) == 5 and body["next_since_id"] == quiet["id"]


async def test_a_frame_committed_between_the_two_reads_stays_above_the_watermark(tmp_path) -> None:
    import sqlite3

    store = Store(str(tmp_path / "snap.db"))
    await store.start()
    try:
        await _frames(store, "A", 2)
        conn = store._open_read_conn()
        writer = sqlite3.connect(store._db_path)
        landed: list[int] = []

        def between(sql: str) -> None:
            # Once the frames are read, as the newest-id read starts: a frame commits.
            if sql.startswith("SELECT MAX(id) FROM lines") and not landed:
                new_id = writer.execute("SELECT MAX(id) FROM lines").fetchone()[0] + 1
                writer.execute("INSERT INTO lines VALUES(?, ?, 'A', 'rx', 'event', NULL, ?)",
                               (new_id, time.time(), "!can late"))
                writer.execute("INSERT INTO can_frames(line_id, tick_ms, bus, can_id, ext, rtr,"
                               " dlc, data) VALUES(?, 9, 1, 256, 0, 0, 0, x'')", (new_id,))
                writer.commit()
                landed.append(new_id)

        conn.set_trace_callback(between)
        try:
            rows, _, nxt = store.can_frames_page(conn=conn, port="A", limit=100)
        finally:
            conn.set_trace_callback(None)
            conn.close()
            writer.close()
        assert landed, "positive control: the late frame was committed mid-answer"
        assert landed[0] not in [r["line_id"] for r in rows]
        assert nxt < landed[0], "the watermark passed a frame the answer never showed"
    finally:
        await store.stop()
