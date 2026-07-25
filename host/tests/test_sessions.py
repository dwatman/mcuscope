"""Sessions: naming a span of the capture so one run can be queried on its own (SPEC 3.4).

A session is an id range over the single capture timeline, so the tests here care about
the boundaries: what falls inside, what a second session does to the first, and that an
unknown name cannot silently widen a query to the whole capture.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import create_app
from mcuscope.store import Store


def _mk_app(tmp_path, **storage):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "sessions.db"), **storage),
    )
    return create_app(config, config_path=tmp_path / "config.toml")


async def _fresh_store(tmp_path) -> Store:
    store = Store(str(tmp_path / "s.db"))
    await store.start()
    return store


async def _line(store: Store, raw: str) -> dict:
    return await store.add_line(
        ts=time.time(), port="board", dir="rx", chan="debug", seq=None, raw=raw
    )


# -- store level -----------------------------------------------------------------------


def test_session_bounds_what_it_contains(tmp_path) -> None:
    async def run() -> None:
        store = await _fresh_store(tmp_path)
        try:
            await _line(store, "before")
            session = await store.start_session("run-a", note="first go")
            await _line(store, "during")
            ended = await store.stop_session()
            await _line(store, "after")

            assert ended is not None
            assert ended["name"] == "run-a" and ended["note"] == "first go"
            rows, _ = store.query_lines(
                id_from=ended["start_id"], id_to=ended["end_id"], limit=100, order="asc"
            )
            raws = [r["raw"] for r in rows]
            assert "during" in raws
            assert "before" not in raws and "after" not in raws
            # Both boundary markers fall inside the session they delimit.
            assert raws[0] == "session start: run-a (first go)"
            assert raws[-1] == "session end: run-a"
            assert session["start_id"] == ended["start_id"]
        finally:
            await store.stop()

    asyncio.run(run())


def test_starting_a_session_closes_the_previous_one(tmp_path) -> None:
    async def run() -> None:
        store = await _fresh_store(tmp_path)
        try:
            first = await store.start_session("run-a")
            await _line(store, "in a")
            second = await store.start_session("run-b")
            await _line(store, "in b")

            closed = store.resolve_session(str(first["id"]))
            assert closed["ended_ts"] is not None, "the first session should have been closed"
            assert closed["end_id"] < second["start_id"], "the ranges must not overlap"
            assert store.active_session()["id"] == second["id"]

            rows, _ = store.query_lines(
                id_from=closed["start_id"], id_to=closed["end_id"], limit=100, order="asc"
            )
            assert "in b" not in [r["raw"] for r in rows]
        finally:
            await store.stop()

    asyncio.run(run())


def test_stop_without_a_session_is_a_noop(tmp_path) -> None:
    async def run() -> None:
        store = await _fresh_store(tmp_path)
        try:
            assert await store.stop_session() is None
            assert store.active_session() is None
        finally:
            await store.stop()

    asyncio.run(run())


def test_resolve_by_name_takes_the_newest(tmp_path) -> None:
    async def run() -> None:
        store = await _fresh_store(tmp_path)
        try:
            old = await store.start_session("retry")
            await store.stop_session()
            new = await store.start_session("retry")
            assert store.resolve_session("retry")["id"] == new["id"]
            assert store.resolve_session(str(old["id"]))["id"] == old["id"]
            assert store.resolve_session("nope") is None
        finally:
            await store.stop()

    asyncio.run(run())


def test_line_count_reflects_retention(tmp_path) -> None:
    # A finished run whose lines have aged out must read as 0 rather than claiming rows
    # that are no longer there.
    async def run() -> None:
        store = await _fresh_store(tmp_path)
        try:
            await store.start_session("old-run")
            await _line(store, "will expire")
            await store.stop_session()
            assert store.list_sessions()[0]["lines"] > 0

            # Age the rows explicitly (as test_plot's cascade test does) rather than
            # leaning on retention_days = 0 to expire lines written moments ago: the
            # sweep deletes ts < cutoff, and Windows' ~16 ms clock granularity can put
            # every row in the same tick as the cutoff, where none of them expire.
            store._conn.execute("UPDATE lines SET ts = ts - 999999")
            store._conn.commit()
            store._retention_days = 0
            await store._sweep_retention_async()
            assert store.list_sessions()[0]["lines"] == 0
        finally:
            await store.stop()

    asyncio.run(run())


# -- API level -------------------------------------------------------------------------


def test_session_api_roundtrip_and_scoping(tmp_path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app) as c:
        assert c.post("/send", json={"line": "before"}).status_code in (200, 400)

        started = c.post("/sessions", json={"name": "run-1", "note": "smoke"}).json()["session"]
        assert started["ended_ts"] is None
        assert c.get("/status").json()["session"]["name"] == "run-1"

        c.post("/marker", json={"text": "inside the run"})
        ended = c.post("/sessions/stop").json()["session"]
        c.post("/marker", json={"text": "outside the run"})

        scoped = c.get("/lines", params={"session": "run-1", "limit": 100}).json()["lines"]
        raws = [r["raw"] for r in scoped]
        assert "inside the run" in raws
        assert "outside the run" not in raws
        assert ended["end_id"] >= started["start_id"]

        # By numeric id as well as by name.
        by_id = c.get("/lines", params={"session": str(ended["id"]), "limit": 100}).json()
        assert [r["raw"] for r in by_id["lines"]] == raws

        listing = c.get("/sessions").json()
        # Stopping a named run hands the capture back to the daemon's automatic session.
        assert listing["active"]["auto"] is True
        named = [s for s in listing["sessions"] if not s["auto"]]
        assert named[0]["name"] == "run-1"
        assert named[0]["lines"] == len(raws)


def test_unknown_session_matches_nothing(tmp_path) -> None:
    # A typo must not widen the query to the whole capture.
    app = _mk_app(tmp_path)
    with TestClient(app) as c:
        c.post("/marker", json={"text": "some line"})
        assert c.get("/lines", params={"limit": 100}).json()["lines"], "sanity: rows exist"
        for path, key, params in (
            ("/lines", "lines", {"session": "typo"}),
            ("/can/frames", "frames", {"session": "typo"}),
            ("/plot/series", "points", {"session": "typo", "name": "v"}),
        ):
            body = c.get(path, params=params).json()
            assert body[key] == [], f"{path} leaked rows for an unknown session"


def test_stop_with_no_session_is_a_clean_error(tmp_path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app) as c:
        r = c.post("/sessions/stop")
        assert r.status_code == 400
        assert "no session" in r.json()["error"]


def test_delete_session_keeps_the_lines(tmp_path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app) as c:
        started = c.post("/sessions", json={"name": "throwaway"}).json()["session"]
        c.post("/marker", json={"text": "kept"})
        c.post("/sessions/stop")

        assert c.delete(f"/sessions/{started['id']}").json() == {"ok": True, "lines_deleted": 0}
        assert c.delete(f"/sessions/{started['id']}").status_code == 400
        assert [s for s in c.get("/sessions").json()["sessions"] if not s["auto"]] == []
        raws = [r["raw"] for r in c.get("/lines", params={"limit": 100}).json()["lines"]]
        assert "kept" in raws, "deleting the label must not delete the capture"


@pytest.mark.parametrize("name", ["", "x" * 200])
def test_session_name_bounds(tmp_path, name: str) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app) as c:
        assert c.post("/sessions", json={"name": name}).status_code == 422


# -- automatic sessions ----------------------------------------------------------------
#
# The normal way to use MCUscope names no sessions at all: the daemon runs, an agent issues
# commands, nobody types `session start`. Without an automatic session the retention floor
# would protect nothing in exactly the case it was built for.


def test_daemon_run_opens_a_session_by_default(tmp_path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app) as c:
        active = c.get("/status").json()["session"]
        assert active is not None and active["auto"] is True
        assert active["name"].startswith("auto-")


def test_auto_session_can_be_turned_off(tmp_path) -> None:
    app = _mk_app(tmp_path, auto_session=False)
    with TestClient(app) as c:
        assert c.get("/status").json()["session"] is None


def test_the_automatic_session_is_not_the_callers_to_stop(tmp_path) -> None:
    # `session start` / `session stop` stay a matched pair: stopping something you never
    # started would be surprising, and it belongs to the daemon run either way.
    app = _mk_app(tmp_path)
    with TestClient(app) as c:
        r = c.post("/sessions/stop")
        assert r.status_code == 400 and "no session" in r.json()["error"]
        assert c.get("/status").json()["session"]["auto"] is True


def test_named_run_displaces_the_automatic_one_and_hands_back(tmp_path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app) as c:
        auto_first = c.get("/status").json()["session"]
        named = c.post("/sessions", json={"name": "real-run"}).json()["session"]
        assert named["auto"] is False
        assert c.get("/status").json()["session"]["id"] == named["id"]

        c.post("/marker", json={"text": "inside the named run"})
        c.post("/sessions/stop")
        auto_again = c.get("/status").json()["session"]
        assert auto_again["auto"] is True
        assert auto_again["id"] not in (auto_first["id"], named["id"]), "a fresh auto session"

        # The named run's span is its own, not the whole daemon run.
        raws = [r["raw"] for r in c.get("/lines", params={
            "session": "real-run", "limit": 100
        }).json()["lines"]]
        assert "inside the named run" in raws


def test_empty_automatic_session_is_dropped_on_close(tmp_path) -> None:
    # A daemon started with no board attached is not a run; a list full of those would
    # bury the ones that are.
    async def check() -> None:
        store = Store(str(tmp_path / "sessions.db"))
        await store.start()
        try:
            assert store.list_sessions() == []
        finally:
            await store.stop()

    app = _mk_app(tmp_path)
    with TestClient(app) as c:
        assert c.get("/status").json()["session"]["auto"] is True
        c.post("/marker", json={"text": "a marker is not device traffic"})
    asyncio.run(check())


def test_automatic_session_with_traffic_is_kept(tmp_path) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "s.db"))
        await store.start()
        try:
            await store.start_session("auto-x", auto=True)
            await store.add_line(
                ts=time.time(), port="board", dir="rx", chan="debug", seq=None, raw="hello"
            )
            await store.stop_session()
            kept = store.list_sessions()
            assert len(kept) == 1 and kept[0]["auto"] is True
        finally:
            await store.stop()

    asyncio.run(run())


def test_automatic_sessions_carry_the_retention_floor(tmp_path) -> None:
    # The whole reason automatic sessions exist: "keep the newest N sessions" has to mean
    # "keep the newest N daemon runs" when nobody has named anything.
    async def run() -> None:
        store = Store(str(tmp_path / "s.db"))
        await store.start()
        try:
            store.set_min_sessions(2)
            for i in range(3):
                await store.start_session(f"auto-{i}", auto=True)
                await _old_line(store, f"run {i} payload", days_ago=30)
                await store.stop_session()

            store._retention_days = 1
            await store._sweep_retention_async()

            raws = _raws(store)
            assert "run 0 payload" not in raws, "the oldest run should have expired"
            assert "run 1 payload" in raws and "run 2 payload" in raws
        finally:
            await store.stop()

    asyncio.run(run())


def test_capture_predating_the_auto_column_is_migrated(tmp_path) -> None:
    # An existing capture has a sessions table without `auto`; CREATE TABLE IF NOT EXISTS
    # does nothing to it, so the column has to arrive by ALTER TABLE.
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE lines(
          id INTEGER PRIMARY KEY, ts REAL NOT NULL, port TEXT NOT NULL,
          dir TEXT NOT NULL, chan TEXT NOT NULL, seq INTEGER, raw TEXT NOT NULL);
        CREATE TABLE sessions(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
          started_ts REAL NOT NULL, ended_ts REAL, start_id INTEGER NOT NULL, end_id INTEGER);
        INSERT INTO sessions(name, started_ts, start_id, end_id, ended_ts)
          VALUES('old-run', 1.0, 1, 5, 2.0);
        """
    )
    conn.commit()
    conn.close()

    async def run() -> None:
        store = Store(str(db))
        await store.start()
        try:
            sessions = store.list_sessions()
            assert len(sessions) == 1
            assert sessions[0]["name"] == "old-run"
            assert sessions[0]["auto"] is False, "pre-existing runs are not automatic"
        finally:
            await store.stop()

    asyncio.run(run())


# -- session-aware retention -----------------------------------------------------------


async def _old_line(store: Store, raw: str, days_ago: float) -> dict:
    return await store.add_line(
        ts=time.time() - days_ago * 86400, port="board", dir="rx", chan="debug",
        seq=None, raw=raw,
    )


def _raws(store: Store) -> list[str]:
    rows, _ = store.query_lines(limit=1000, order="asc")
    return [r["raw"] for r in rows]


def test_min_sessions_floor_survives_age_expiry(tmp_path) -> None:
    # The point of the floor: a board captured over a quiet fortnight must not lose its
    # only recorded runs to the calendar. Everything here is 30 days old.
    async def run() -> None:
        store = await _fresh_store(tmp_path)
        try:
            store.set_min_sessions(2)
            for name in ("run-1", "run-2", "run-3"):
                await store.start_session(name)
                await _old_line(store, f"{name} payload", days_ago=30)
                await store.stop_session()

            store._retention_days = 1
            await store._sweep_retention_async()

            raws = _raws(store)
            assert "run-1 payload" not in raws, "the oldest run should have expired"
            assert "run-2 payload" in raws, "a protected run must survive its age"
            assert "run-3 payload" in raws
        finally:
            await store.stop()

    asyncio.run(run())


def test_all_sessions_protected_when_fewer_than_the_floor(tmp_path) -> None:
    async def run() -> None:
        store = await _fresh_store(tmp_path)
        try:
            store.set_min_sessions(5)
            for name in ("only-1", "only-2"):
                await store.start_session(name)
                await _old_line(store, f"{name} payload", days_ago=90)
                await store.stop_session()

            store._retention_days = 1
            await store._sweep_retention_async()

            raws = _raws(store)
            assert "only-1 payload" in raws and "only-2 payload" in raws
        finally:
            await store.stop()

    asyncio.run(run())


def test_min_sessions_zero_is_pure_age_retention(tmp_path) -> None:
    async def run() -> None:
        store = await _fresh_store(tmp_path)
        try:
            store.set_min_sessions(0)
            await store.start_session("run-1")
            await _old_line(store, "run-1 payload", days_ago=30)
            await store.stop_session()

            store._retention_days = 1
            await store._sweep_retention_async()
            assert "run-1 payload" not in _raws(store)
        finally:
            await store.stop()

    asyncio.run(run())


def test_lines_outside_any_session_are_not_protected(tmp_path) -> None:
    # The floor protects sessions, not ambient capture: a line recorded while nothing was
    # running expires on age like any other.
    async def run() -> None:
        store = await _fresh_store(tmp_path)
        try:
            store.set_min_sessions(5)
            await _old_line(store, "ambient", days_ago=30)
            await store.start_session("run-1")
            await _old_line(store, "run-1 payload", days_ago=30)
            await store.stop_session()

            store._retention_days = 1
            await store._sweep_retention_async()

            raws = _raws(store)
            assert "ambient" not in raws
            assert "run-1 payload" in raws
        finally:
            await store.stop()

    asyncio.run(run())


def test_size_cap_prefers_unprotected_data_but_stays_a_bound(tmp_path) -> None:
    # The cap honours the floor where it can, then overrides it: a cap that can be
    # silently suspended is not a bound on disk use at all.
    async def run() -> None:
        store = await _fresh_store(tmp_path)
        try:
            store.set_min_sessions(1)
            for i in range(1500):
                await _old_line(store, f"ambient {i} " + "x" * 200, days_ago=0)
            await store.start_session("keep-me")
            for i in range(1500):
                await _old_line(store, f"protected {i} " + "x" * 200, days_ago=0)

            # A cap that only the unprotected half needs to give up for.
            store.set_max_db_bytes(int(store.content_bytes() * 0.7))
            assert await store._sweep_size_async() > 0
            raws = _raws(store)
            assert any(r.startswith("protected ") for r in raws), \
                "the protected session should have been spared first"
            assert sum(r.startswith("ambient ") for r in raws) < 1500

            # A cap the protected session alone cannot meet: it must still be enforced.
            store.set_max_db_bytes(1 << 20)
            await store._sweep_size_async()
            assert store.content_bytes() <= 2 << 20
        finally:
            await store.stop()

    asyncio.run(run())
