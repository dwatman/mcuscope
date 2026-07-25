"""Sessions: naming a span of the capture so one run can be queried on its own (SPEC 3.4).

A session is an id range over the single capture timeline, so the tests here care about
the boundaries: what falls inside, what a second session does to the first, and that an
unknown name cannot silently widen a query to the whole capture.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import create_app
from mcuscope.store import Store


def _mk_app(tmp_path):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "sessions.db")),
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
        assert listing["active"] is None
        assert listing["sessions"][0]["name"] == "run-1"
        assert listing["sessions"][0]["lines"] == len(raws)


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

        assert c.delete(f"/sessions/{started['id']}").json() == {"ok": True}
        assert c.delete(f"/sessions/{started['id']}").status_code == 400
        assert c.get("/sessions").json()["sessions"] == []
        raws = [r["raw"] for r in c.get("/lines", params={"limit": 100}).json()["lines"]]
        assert "kept" in raws, "deleting the label must not delete the capture"


@pytest.mark.parametrize("name", ["", "x" * 200])
def test_session_name_bounds(tmp_path, name: str) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app) as c:
        assert c.post("/sessions", json={"name": name}).status_code == 422
