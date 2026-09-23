"""Live /assert verdicts over cut scans, the `purge before_ts` span, and a PlotJuggler default
saved disabled (SPEC 3.3.1, 3.4)."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from mcuscope import server as server_mod
from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import create_app


@pytest.fixture
def c(tmp_path):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db")),
    )
    app = create_app(config, config_path=tmp_path / "config.toml")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        yield client


def _on_loop(c: TestClient, coro):
    return asyncio.run_coroutine_threadsafe(coro, c.app.state.ports._loop).result(10)


def _add_later(c: TestClient, raw: str, delay: float = 0.2) -> threading.Timer:
    store = c.app.state.store
    t = threading.Timer(delay, lambda: _on_loop(c, store.add_line(
        ts=time.time(), port="board", dir="rx", chan="debug", seq=None, raw=raw)))
    t.start()
    return t


@pytest.fixture
def stall(monkeypatch):
    """_scan_batch that parks the scans of the patterns in `stall.patterns` past the window."""
    release = threading.Event()
    real = server_mod._scan_batch

    def scan(pats, texts):
        if {p.pattern for p in pats} & scan.patterns:
            release.wait(10)
            return []
        return real(pats, texts)

    scan.patterns = set()
    monkeypatch.setattr(server_mod, "_scan_batch", scan)
    monkeypatch.setattr(server_mod, "LIVE_SCAN_GRACE_S", 0.1)
    yield scan
    release.set()


# -- finding 2: a forbid hit is decided before the expect scan can be cut ---------------------


def test_a_forbid_hit_survives_a_cut_expect_scan(c, stall) -> None:
    stall.patterns = {"NEVER"}
    timer = _add_later(c, "OK boot")
    res = c.post("/assert", json={"forbid": ["OK"], "expect": ["NEVER"], "timeout_ms": 1500}).json()
    timer.join()
    assert res["status"] == "fail" and res["reason"] is None, res
    assert res["forbid"][0]["matched"] and res["forbid"][0]["line"]["raw"] == "OK boot", res
    assert res["checked_lines"] == 1 and res["dropped"] == 0, res


def test_a_cut_expect_scan_after_a_clean_forbid_scan_counts_the_batch_unjudged(c, stall) -> None:
    stall.patterns = {"NEVER"}
    timer = _add_later(c, "fine")
    res = c.post("/assert", json={"forbid": ["PANIC"], "expect": ["NEVER"],
                                  "timeout_ms": 400}).json()
    timer.join()
    assert res["checked_lines"] == 0 and res["dropped"] == 1, res
    assert not res["forbid"][0]["matched"]
    assert res["status"] == "empty", res


# -- finding 3: lines dropped unjudged are not the empty window allow_empty accepts ------------


@pytest.mark.parametrize("allow_empty", [False, True])
def test_a_window_whose_lines_were_all_cut_is_empty_and_says_why(c, stall, allow_empty) -> None:
    stall.patterns = {"PANIC"}
    timer = _add_later(c, "ordinary")
    res = c.post("/assert", json={"forbid": ["PANIC"], "timeout_ms": 400,
                                  "allow_empty": allow_empty}).json()
    timer.join()
    assert res["status"] == "empty", res
    assert res["reason"] == "no lines were judged: 1 dropped unjudged", res
    assert res["checked_lines"] == 0 and res["dropped"] == 1


def test_a_quiet_window_still_passes_under_allow_empty(c, stall) -> None:
    stall.patterns = {"PANIC"}   # armed, but no line arrives to be scanned
    res = c.post("/assert", json={"forbid": ["PANIC"], "timeout_ms": 200,
                                  "allow_empty": True}).json()
    assert res["status"] == "pass" and res["reason"] is None, res
    assert res["dropped"] == 0
    quiet = c.post("/assert", json={"forbid": ["PANIC"], "timeout_ms": 200}).json()
    assert quiet["reason"] == "no lines were checked in the window", quiet


# -- finding 4: purge before_ts deletes the span it reports -----------------------------------


def test_purge_before_ts_spares_a_row_committed_after_its_count(c, monkeypatch) -> None:
    store = c.app.state.store
    t0 = time.time() - 100
    for i in range(3):
        _on_loop(c, store.add_line(ts=t0 + i, port="board", dir="rx", chan="debug", seq=None,
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


# -- finding 8 (M3): a disabled PlotJuggler dest is grammar-checked only ----------------------


def test_a_multicast_dest_saves_disabled_and_is_refused_enabled(c) -> None:
    body = {"enabled": False, "dest": "239.1.2.3:9870"}
    assert c.put("/config/plotjuggler", json=body).status_code == 200
    refused = c.put("/config/plotjuggler", json={**body, "enabled": True})
    assert refused.status_code == 400, refused.text
