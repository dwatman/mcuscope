"""POST /purge by time and POST /sessions/stop (SPEC 3.4): a time purge selects rows by their
`ts`, not by an id range, and the automatic session reopened after a named one abuts it."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import create_app
from mcuscope.store import Store
from tests.support import Stack, on_loop, stack_client
from tests.test_e2e import poll


def _client(tmp_path) -> TestClient:
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db")),
    )
    app = create_app(config, config_path=tmp_path / "config.toml")
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


def _add(c: TestClient, ts: float, raw: str) -> int:
    store = c.app.state.store
    return asyncio.run_coroutine_threadsafe(
        store.add_line(ts=ts, port="board", dir="rx", chan="debug", seq=None, raw=raw),
        c.app.state.ports._loop,
    ).result(5)["id"]


def _raws(c: TestClient) -> list[str]:
    return [r["raw"] for r in c.get("/lines", params={"limit": 1000}).json()["lines"]]


def test_a_time_purge_takes_the_old_rows_whatever_their_ids(tmp_path) -> None:
    t0 = time.time() - 1000
    with _client(tmp_path) as c:
        _add(c, t0 + 10, "newer-low-id")
        old = _add(c, t0, "older-high-id")        # a clock step back: ts falls, id rises
        _add(c, t0 + 20, "newest")
        cut = {"before_ts": t0 + 5}
        dry = c.post("/purge", json={**cut, "dry_run": True}).json()
        assert dry == {"deleted": 1, "id_from": old, "id_to": old, "dry_run": True}
        done = c.post("/purge", json=cut).json()
        assert done == {"deleted": 1, "id_from": old, "id_to": old, "dry_run": False}
        raws = _raws(c)
        assert "older-high-id" not in raws
        assert "newer-low-id" in raws and "newest" in raws, "a newer row below the id went too"
        assert c.post("/purge", json=cut).json()["deleted"] == 0


def test_the_reopened_automatic_session_abuts_the_named_one(tmp_path, monkeypatch) -> None:
    # A row landing between the stop and a separate reopen fell outside both sessions.
    real_start = Store.start_session

    async def with_a_row_in_between(self, *a, **kw):
        await self.add_line(ts=time.time(), port="board", dir="rx", chan="debug", seq=None,
                            raw="between")
        return await real_start(self, *a, **kw)

    with _client(tmp_path) as c:
        c.post("/sessions", json={"name": "run"})
        monkeypatch.setattr(Store, "start_session", with_a_row_in_between)
        named = c.post("/sessions/stop").json()["session"]
        auto = c.get("/status").json()["session"]
        assert auto["auto"] is True and auto["id"] != named["id"]
        assert auto["start_id"] == named["end_id"] + 1, (named, auto)


def test_can_frames_name_their_board(stack: Stack) -> None:
    with httpx.Client(base_url=stack.base_url, timeout=5.0) as h:
        frames: list = []

        def seen() -> bool:
            frames[:] = h.get("/can/frames", params={"limit": 1}).json()["frames"]
            return bool(frames)

        assert poll(seen, 5), "the sim's heartbeat never arrived"
        assert frames[0]["port"] == stack.alias


# -- measurement F1 (REST half): a purge cutoff in the future ---------------------------


def test_purge_before_ts_in_the_future_is_refused(stack: Stack) -> None:
    with stack_client(stack) as c:
        r = c.post("/purge", json={"before_ts": time.time() + 3600, "dry_run": True})
    assert r.status_code == 400
    body = r.json()["error"]
    assert "future" in body
    assert "all" in body   # the message must point at the deliberate full wipe


def test_purge_before_ts_inside_the_skew_slack_is_accepted(stack: Stack) -> None:
    # The other side of the same boundary: a client clock a few seconds ahead of the
    # daemon must not have its retention purge refused.
    with stack_client(stack) as c:
        r = c.post("/purge", json={"before_ts": time.time() + 5, "dry_run": True})
    assert r.status_code == 200
    assert r.json()["dry_run"] is True


@pytest.fixture
def c(tmp_path):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db")),
    )
    app = create_app(config, config_path=tmp_path / "config.toml")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        yield client


# -- finding 4: purge before_ts deletes the span it reports -----------------------------------


def test_purge_before_ts_spares_a_row_committed_after_its_count(c, monkeypatch) -> None:
    store = c.app.state.store
    t0 = time.time() - 100
    for i in range(3):
        on_loop(c, store.add_line(ts=t0 + i, port="board", dir="rx", chan="debug", seq=None,
                                   raw=f"old {i}"))
    real = store.before_ts_span_safe
    late: list[dict] = []

    async def count_then_commit(before_ts: float):
        span = await real(before_ts)
        # A live row landing between the count and the delete, stamped before the cutoff.
        late.append(await store.add_line(ts=t0 + 50, port="board", dir="rx", chan="debug",
                                         seq=None, raw="late"))
        return span

    monkeypatch.setattr(store, "before_ts_span_safe", count_then_commit)
    res = c.post("/purge", json={"before_ts": t0 + 60}).json()
    assert res["deleted"] == 3, res
    assert res["id_to"] < late[0]["id"], (res, late)
    left = c.get("/lines", params={"match": "^(late|old)", "limit": 10}).json()
    raws = [r["raw"] for r in (left["lines"] if isinstance(left, dict) else left)]
    assert raws == ["late"], raws


def test_purge_before_ts_of_nothing_spares_a_row_committed_after_its_count(c, monkeypatch) -> None:
    """With nothing counted the span has no max id, so the delete must not run unbounded."""
    store = c.app.state.store
    t0 = time.time() - 100
    real = store.before_ts_span_safe

    async def count_then_commit(before_ts: float):
        span = await real(before_ts)
        await store.add_line(ts=t0, port="board", dir="rx", chan="debug", seq=None, raw="late")
        return span

    monkeypatch.setattr(store, "before_ts_span_safe", count_then_commit)
    res = c.post("/purge", json={"before_ts": t0 + 60}).json()
    assert res == {"deleted": 0, "id_from": None, "id_to": None, "dry_run": False}, res
    left = c.get("/lines", params={"match": "^late$", "limit": 10}).json()
    raws = [r["raw"] for r in (left["lines"] if isinstance(left, dict) else left)]
    assert raws == ["late"], raws
