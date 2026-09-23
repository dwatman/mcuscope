"""/wait and /assert verdicts (SPEC 3.4) and the port a request names or defaults to: a call's
own send is not its match, a failed send fails the assert, a window of no lines is `empty`,
an unknown port is refused, and live matching has a pool and a deadline of its own."""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from mcuscope import server as server_mod
from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import create_app
from mcuscope.store import MATCH_WORKERS, match_executor
from tests.support import UNOPENABLE, Stack

OWN_SEND = r"^>\d+ ping$"   # the stored tx row of `ping`, and nothing the sim answers


def _http(stack: Stack) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=20.0)


@pytest.fixture
def c(tmp_path):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db")),
    )
    app = create_app(config, config_path=tmp_path / "config.toml")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        yield client


def _add(c: TestClient, port: str, chan: str = "debug", raw: str = "row") -> None:
    store = c.app.state.store
    asyncio.run_coroutine_threadsafe(
        store.add_line(ts=time.time(), port=port, dir="rx", chan=chan, seq=None, raw=raw),
        c.app.state.ports._loop,
    ).result(5)


# -- CLI-1: a call's own send is not its match ----------------------------------------------


def test_a_wait_does_not_match_its_own_command(stack: Stack) -> None:
    with _http(stack) as h:
        mine = h.post("/wait", json={"send": "ping", "match": OWN_SEND, "timeout_ms": 400}).json()
        assert mine["status"] == "timeout", mine
        assert mine["cmd_result"]["status"] == "ok", "positive control: the send happened"
        named = h.post("/wait", json={
            "send": "ping", "match": OWN_SEND, "timeout_ms": 1500, "chan": "cmd",
        }).json()
        assert named["status"] == "match" and named["line"]["dir"] == "tx", named


def test_a_live_assert_does_not_expect_its_own_command(stack: Stack) -> None:
    with _http(stack) as h:
        r = h.post("/assert", json={"send": "ping", "expect": [OWN_SEND], "timeout_ms": 400})
        body = r.json()
        assert body["status"] == "fail" and body["checked_lines"] >= 1, body


# -- CLI-2: an assert reports its send, and a refused send fails it --------------------------


def test_an_assert_whose_send_is_refused_fails_and_says_why(stack: Stack) -> None:
    with _http(stack) as h:
        ok = h.post("/assert", json={"send": "ping", "forbid": ["PANIC"], "timeout_ms": 300}).json()
        assert ok["status"] == "pass" and ok["reason"] is None, ok        # positive control
        assert ok["cmd_result"]["status"] == "ok"
        bad = h.post("/assert", json={
            "send": "reset", "forbid": ["PANIC"], "timeout_ms": 1500,
        }).json()
        assert bad["status"] == "fail", bad
        assert bad["cmd_result"]["status"] == "err"
        assert bad["reason"].startswith("send answered ERR 1 badcmd"), bad["reason"]
        assert bad["checked_lines"] == 0, "the verdict was decided; the window must not run"


def test_an_assert_whose_send_timed_out_fails_and_says_why(make_stack) -> None:
    stack = make_stack(["--drop-response", "2"])   # 1 is the connect-time ping
    with _http(stack) as h:
        body = h.post("/assert", json={
            "send": "ping", "forbid": ["PANIC"], "timeout_ms": 500,
        }).json()
    assert body["status"] == "fail" and body["cmd_result"]["status"] == "timeout", body
    assert body["reason"] == "send got no response in 500 ms"


def test_a_retrospective_or_raw_assert_carries_no_command_result(c) -> None:
    _add(c, "board")
    body = c.post("/assert", json={"forbid": ["PANIC"]}).json()
    assert body["cmd_result"] is None and body["reason"] is None and body["status"] == "pass"


# -- CLI-3: a verdict over no lines, and an unknown port -------------------------------------


def test_a_retrospective_verdict_over_no_lines_is_empty(c) -> None:
    _add(c, "board", chan="debug")
    scope = {"forbid": ["PANIC"], "port": "board", "chan": "resp"}
    empty = c.post("/assert", json=scope).json()
    assert empty["status"] == "empty" and empty["checked_lines"] == 0, empty
    assert empty["reason"] == "no lines were checked in the window"
    allowed = c.post("/assert", json={**scope, "allow_empty": True}).json()
    assert allowed["status"] == "pass" and allowed["reason"] is None
    judged = c.post("/assert", json={**scope, "chan": "debug"}).json()
    assert judged["status"] == "pass" and judged["checked_lines"] == 1   # positive control


def test_a_live_verdict_over_no_lines_is_empty(c) -> None:
    body = c.post("/assert", json={"forbid": ["PANIC"], "timeout_ms": 200}).json()
    assert body["status"] == "empty" and body["checked_lines"] == 0, body
    fine = c.post("/assert", json={"forbid": ["PANIC"], "timeout_ms": 200, "allow_empty": True})
    assert fine.json()["status"] == "pass"


READS = [
    ("GET", "/lines", {}), ("GET", "/lines/export", {}), ("GET", "/can/frames", {}),
    ("GET", "/plot/channels", {}), ("GET", "/plot/series", {"name": "v"}),
    ("GET", "/plot/export", {"names": "v"}),
]


@pytest.mark.parametrize("method, path, extra", READS)
def test_a_read_scoped_to_an_unknown_port_is_refused(c, method, path, extra) -> None:
    _add(c, "gone")   # a detached board's history
    known = c.request(method, path, params={**extra, "port": "gone"})
    assert known.status_code != 400 or "no such port" not in known.text, known.text
    r = c.request(method, path, params={**extra, "port": "nosuch"})
    assert r.status_code == 400 and r.json() == {"error": "no such port: nosuch"}


def test_a_verdict_or_marker_for_an_unknown_port_is_refused(c) -> None:
    _add(c, "gone")
    assert c.post("/assert", json={"forbid": ["x"], "port": "gone"}).status_code == 200
    r = c.post("/assert", json={"forbid": ["x"], "port": "nosuch"})
    assert r.status_code == 400 and r.json() == {"error": "no such port: nosuch"}
    assert c.post("/marker", json={"text": "m", "port": "gone"}).status_code == 200
    r = c.post("/marker", json={"text": "m", "port": "nosuch"})
    assert r.status_code == 400 and r.json() == {"error": "no such port: nosuch"}


# -- CLI-18: a write defaults only to the sole attached port ---------------------------------


WRITES = [
    ("/send", {"line": "x"}), ("/cmd", {"cmd": "ping"}), ("/break", {"ms": 5}),
    ("/wait", {"match": "x", "send": "ping", "timeout_ms": 100}),
    ("/assert", {"forbid": ["x"], "send": "ping", "timeout_ms": 100}),
]


@pytest.mark.parametrize("path, body", WRITES)
def test_an_unnamed_write_with_two_ports_attached_is_refused(stack: Stack, path, body) -> None:
    with _http(stack) as h:
        assert h.post("/ports", json={"alias": "spare", "device": UNOPENABLE}).status_code == 200
        try:
            r = h.post(path, json=body)   # "spare" never connects, "board" is up
            assert r.status_code == 400
            assert r.json() == {"error": "port is ambiguous; specify one of: board, spare"}
            named = h.post(path, json={**body, "port": stack.alias})
            assert named.status_code == 200 or "ambiguous" not in named.text, named.text
        finally:
            h.delete("/ports/spare")


def test_the_sole_attached_port_is_the_default_even_when_down(c) -> None:
    assert c.post("/send", json={"line": "x"}).json() == {"error": "no ports attached"}
    assert c.post("/ports", json={"alias": "solo", "device": UNOPENABLE}).status_code == 200
    try:
        r = c.post("/send", json={"line": "x"})
        assert r.status_code == 400 and "solo" in r.json()["error"], r.text
        assert "ambiguous" not in r.text
    finally:
        c.delete("/ports/solo")


# -- PERF-4 / API-2: live matching has its own pool, bounded by the window ------------------


def test_a_live_wait_matches_while_the_shared_match_pool_is_busy(stack: Stack) -> None:
    release = threading.Event()
    busy = [match_executor().submit(release.wait, 20) for _ in range(MATCH_WORKERS)]
    try:
        with _http(stack) as h:
            r = h.post("/wait", json={"send": "ping", "match": "OK monitor", "timeout_ms": 2000})
            assert r.json()["status"] == "match", r.json()
    finally:
        release.set()
        for f in busy:
            f.result(5)


@pytest.mark.parametrize("path, body, scan", [
    ("/wait", {"send": "ping", "match": "OK", "timeout_ms": 300}, "_search_batch"),
    ("/assert", {"send": "ping", "forbid": ["PANIC"], "timeout_ms": 300}, "_scan_batch"),
])
def test_a_live_scan_is_cut_at_the_window_and_its_rows_reported(
    stack: Stack, monkeypatch, path, body, scan
) -> None:
    finish, entered, returned = threading.Event(), threading.Event(), threading.Event()

    def stuck(*_args):
        entered.set()
        finish.wait(20)
        returned.set()
        return None if scan == "_search_batch" else []

    monkeypatch.setattr(server_mod, scan, stuck)
    try:
        with _http(stack) as h:
            res = h.post(path, json=body).json()
        assert entered.is_set(), "positive control: a scan was started"
        assert not returned.is_set(), "the response waited for the scan past its window"
        assert res["dropped"] >= 1, f"the unjudged rows were not reported ({res})"
        assert res["status"] in ("timeout", "empty"), res
    finally:
        finish.set()
