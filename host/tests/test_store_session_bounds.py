"""Where a session begins and ends: a rollover abuts, and a crashed run ends where it did.

SPEC 3.4: the capture is always covered by exactly one session. A rollover that sampled the
next session's start after draining the queue left the lines committed meanwhile in
neither, and a crashed run's automatic session was closed at the next start's time with
that start's rows.
"""

from __future__ import annotations

import asyncio
import time

from mcuscope.store import Store


def _submit_burst(store: Store, n: int, tag: str) -> list[asyncio.Future]:
    return [
        store.submit_line_nowait(
            ts=time.time(), port="board", dir="rx", chan="debug", seq=None, raw=f"{tag} {i}"
        )
        for i in range(n)
    ]


def _uncovered(store: Store) -> list[int]:
    """Line ids from the first session's start that no session, or more than one, covers."""
    sessions = store._conn.execute(
        "SELECT start_id, COALESCE(end_id, ?) FROM sessions ORDER BY start_id",
        (store.max_id(),),
    ).fetchall()
    ids = [r[0] for r in store._conn.execute(
        "SELECT id FROM lines WHERE id >= ? ORDER BY id", (sessions[0][0],)
    )]
    return [i for i in ids if sum(lo <= i <= hi for lo, hi in sessions) != 1]


async def test_a_rollover_leaves_no_line_between_the_two_sessions(tmp_path) -> None:
    store = Store(str(tmp_path / "rollover.db"))
    await store.start()
    try:
        await store.start_session("run-a")
        _submit_burst(store, 50, "a")
        rollover = asyncio.create_task(store.start_session("run-b"))
        await asyncio.sleep(0)   # run-a's end marker is queued; this burst lands behind it
        late = _submit_burst(store, 200, "in flight")
        b = await rollover
        await asyncio.gather(*late)
        a = store.resolve_session("run-a")
        assert b["start_id"] == a["end_id"] + 1
        assert _uncovered(store) == []
        rows, _ = store.query_lines(id_from=b["start_id"], limit=1000, order="asc")
        assert sum(r["raw"].startswith("in flight") for r in rows) == 200
    finally:
        await store.stop()


async def test_stop_with_reopen_abuts_the_automatic_session(tmp_path) -> None:
    store = Store(str(tmp_path / "reopen.db"))
    await store.start()
    try:
        await store.start_session("named")
        _submit_burst(store, 20, "named")
        stop = asyncio.create_task(store.stop_session(reopen_auto="auto-next"))
        await asyncio.sleep(0)
        late = _submit_burst(store, 100, "in flight")
        closed = await stop
        await asyncio.gather(*late)
        auto = store.active_session()
        assert auto["name"] == "auto-next" and auto["auto"]
        assert auto["start_id"] == closed["end_id"] + 1
        assert _uncovered(store) == []
        # Without reopen_auto nothing is opened.
        assert (await store.stop_session())["name"] == "auto-next"
        assert store.active_session() is None
    finally:
        await store.stop()


async def test_a_start_with_nothing_running_still_excludes_the_queued_backlog(tmp_path) -> None:
    store = Store(str(tmp_path / "fresh.db"))
    await store.start()
    try:
        before = _submit_burst(store, 100, "before")
        s = await store.start_session("run")
        assert max([(await f)["id"] for f in before]) < s["start_id"]
    finally:
        await store.stop()


async def _crashed_run(path, *, traffic: bool) -> tuple[dict, dict | None]:
    """A run whose automatic session is still open when its store goes away."""
    store = Store(path)
    await store.start()
    await store.start_session("auto-crashed", auto=True)
    last = None
    if traffic:
        for i in range(3):
            last = await store.add_line(
                ts=time.time() - 100 + i, port="board", dir="rx", chan="debug", seq=None,
                raw=f"device {i}",
            )
    session = store.active_session()
    await store.stop()   # the lifespan's close never ran
    return session, last


async def test_a_crashed_automatic_session_ends_at_its_own_last_row(tmp_path) -> None:
    path = str(tmp_path / "crash.db")
    session, last = await _crashed_run(path, traffic=True)
    store = Store(path)
    await store.start()
    try:
        assert store.active_session() is None, "Store.start() left the crashed run open"
        closed = store.get_session(session["id"])
        assert closed["end_id"] == last["id"]
        assert closed["ended_ts"] == last["ts"], "ended at the restart, not at the crash"
        after = await store.add_line(
            ts=time.time(), port="", dir="-", chan="sys", seq=None, raw="daemon start"
        )
        assert after["id"] > closed["end_id"]
        rows, _ = store.query_lines(id_from=closed["start_id"], id_to=closed["end_id"], limit=50)
        assert not any(r["raw"].startswith("session end") for r in rows)
    finally:
        await store.stop()


async def test_a_crashed_automatic_session_with_no_traffic_is_dropped(tmp_path) -> None:
    path = str(tmp_path / "crash_empty.db")
    session, _ = await _crashed_run(path, traffic=False)
    store = Store(path)
    await store.start()
    try:
        assert store.get_session(session["id"]) is None
        assert store.active_session() is None
    finally:
        await store.stop()


async def test_a_named_session_left_open_is_not_closed_at_start(tmp_path) -> None:
    path = str(tmp_path / "named.db")
    store = Store(path)
    await store.start()
    await store.start_session("bench-run")
    await store.stop()
    store = Store(path)
    await store.start()
    try:
        active = store.active_session()
        assert active is not None and active["name"] == "bench-run" and active["ended_ts"] is None
    finally:
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
