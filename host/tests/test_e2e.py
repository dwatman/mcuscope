"""End-to-end tests for the daemon (SPEC 3.4), driven over real HTTP + sockets.

A fresh sim + daemon stack runs per test in background threads (see support.Stack),
so every endpoint is exercised against a live pyserial `socket://` connection to the
simulator. Cross-platform: no pty, no subprocess.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import httpx
import pytest
import websockets

from tests.support import Stack, free_port


@pytest.fixture
def make_stack() -> Callable[..., Stack]:
    created: list[Stack] = []

    def _make(sim_args: list[str] | None = None) -> Stack:
        s = Stack(sim_args)
        created.append(s)
        return s

    yield _make
    for s in created:
        s.close()


@pytest.fixture
def stack(make_stack: Callable[..., Stack]) -> Stack:
    return make_stack()


def client(stack: Stack) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=5.0)


def poll(fn: Callable[[], bool], timeout: float = 3.0, interval: float = 0.03) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(interval)
    return False


# -- status / ports / devices ---------------------------------------------------------


def test_status(stack: Stack) -> None:
    with client(stack) as c:
        body = c.get("/status").json()
    assert body["version"]
    assert body["uptime_s"] >= 0
    assert body["ports"][0]["alias"] == "board"
    assert body["ports"][0]["connected"] is True
    assert "db_size_bytes" in body


def test_devices_shape(stack: Stack) -> None:
    with client(stack) as c:
        body = c.get("/devices").json()
    assert "devices" in body
    for d in body["devices"]:
        assert set(d) == {"device", "by_id", "description", "vid_pid", "serial_number"}


def test_ports_attach_detach(stack: Stack) -> None:
    dead = f"socket://127.0.0.1:{free_port()}"  # nothing listening: attaches, won't connect
    with client(stack) as c:
        r = c.post("/ports", json={"alias": "board2", "device": dead, "baud": 9600})
        assert r.status_code == 200
        aliases = {p["alias"] for p in c.get("/ports").json()["ports"]}
        assert aliases == {"board", "board2"}
        assert c.delete("/ports/board2").json() == {"ok": True}
        aliases = {p["alias"] for p in c.get("/ports").json()["ports"]}
        assert aliases == {"board"}
        assert c.delete("/ports/nope").status_code == 400


# -- cmd ------------------------------------------------------------------------------


def test_cmd_ok(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/cmd", json={"cmd": "i2c scan"}).json()
    assert r["status"] == "ok"
    assert r["data"] == "48 50"
    assert isinstance(r["line_id"], int)
    assert r["latency_ms"] >= 0


def test_cmd_err(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/cmd", json={"cmd": "gpio get nope"}).json()
    assert r["status"] == "err"
    assert r["err_code"] == 2
    assert r["err_name"] == "badarg"


def test_cmd_timeout_on_dropped_response(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--drop-response", "1"])  # sim swallows the first response
    with client(stack) as c:
        r = c.post("/cmd", json={"cmd": "ping", "timeout_ms": 600}).json()
    assert r["status"] == "timeout"
    assert r["line_id"] is None


def test_late_response_logged_not_delivered(stack: Stack) -> None:
    # A tiny timeout makes the daemon give up before the (real) response arrives; the
    # late response must still be logged as a resp row, just not delivered (SPEC 3.2).
    with client(stack) as c:
        r = c.post("/cmd", json={"cmd": "ping", "timeout_ms": 1}).json()
        seq = r["seq"]

        def resp_logged() -> bool:
            rows = c.get("/lines", params={"chan": "resp", "limit": 200}).json()["lines"]
            return any(row["seq"] == seq for row in rows)

        assert poll(resp_logged), "late response was not logged"
    if r["status"] == "timeout":
        assert r["line_id"] is None  # timed-out command does not deliver the late row


# -- send / marker --------------------------------------------------------------------


def test_send_raw_logged(stack: Stack) -> None:
    with client(stack) as c:
        assert c.post("/send", json={"line": "hello raw"}).json() == {"ok": True}

        def logged() -> bool:
            rows = c.get("/lines", params={"chan": "cmd", "match": "hello raw"}).json()["lines"]
            return len(rows) >= 1

        assert poll(logged)


def test_marker(stack: Stack) -> None:
    with client(stack) as c:
        line_id = c.post("/marker", json={"text": "checkpoint A"}).json()["line_id"]
        rows = c.get("/lines", params={"chan": "marker"}).json()["lines"]
    assert any(row["id"] == line_id and row["raw"] == "checkpoint A" for row in rows)


# -- lines queries --------------------------------------------------------------------


def test_lines_chan_and_match_filters(stack: Stack) -> None:
    with client(stack) as c:
        assert poll(lambda: len(c.get("/lines", params={"chan": "event"}).json()["lines"]) > 0)
        rows = c.get("/lines", params={"chan": "event"}).json()["lines"]
        assert all(row["chan"] == "event" for row in rows)
        # regex match applied to raw
        matched = c.get("/lines", params={"match": "^!can"}).json()["lines"]
        assert all(row["raw"].startswith("!can") for row in matched)


def test_lines_since_id_and_limit_cap(stack: Stack) -> None:
    with client(stack) as c:
        top = c.get("/lines", params={"limit": 1}).json()["lines"][0]["id"]
        newer = c.get("/lines", params={"since_id": top, "limit": 1000}).json()["lines"]
        assert all(row["id"] > top for row in newer)
        # limit is hard-capped at 1000; asking for more must not error
        body = c.get("/lines", params={"limit": 5000}).json()
        assert len(body["lines"]) <= 1000


def test_lines_last_ms(stack: Stack) -> None:
    with client(stack) as c:
        assert poll(lambda: len(c.get("/lines", params={"last_ms": 100000}).json()["lines"]) > 0)


# -- CAN decode -----------------------------------------------------------------------


def test_can_frames_by_id(stack: Stack) -> None:
    with client(stack) as c:
        # The sim streams an id 0x100 heartbeat at 10 Hz.
        def have_frames() -> bool:
            return len(c.get("/can/frames", params={"id": "0x100"}).json()["frames"]) > 0

        assert poll(have_frames, timeout=3.0)
        frames = c.get("/can/frames", params={"id": "100"}).json()["frames"]
    f = frames[0]
    assert f["can_id"] == 0x100 and f["ext"] is False and f["rtr"] is False
    assert f["dlc"] == 4 and len(f["data_hex"]) == 8


# -- wait -----------------------------------------------------------------------------


def test_wait_match(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/wait", json={"match": "^!can", "timeout_ms": 2000}).json()
    assert r["status"] == "match"
    assert r["line"]["raw"].startswith("!can")


def test_wait_timeout(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/wait", json={"match": "WILLNEVERMATCH_ZZZ", "timeout_ms": 300}).json()
    assert r["status"] == "timeout"
    assert r["line"] is None


def test_wait_with_send(stack: Stack) -> None:
    # Send a CAN frame; the sim echoes it back with id+1 (0x300 -> 0x301) after 20 ms.
    with client(stack) as c:
        r = c.post(
            "/wait",
            json={"send": "can tx 300 AABB", "match": "301 AABB", "timeout_ms": 2000},
        ).json()
    assert r["status"] == "match"
    assert r["cmd_result"]["status"] == "ok"
    assert "301 AABB" in r["line"]["raw"]


# -- WebSocket ------------------------------------------------------------------------


async def test_ws_streams_live_rows(stack: Stack) -> None:
    url = stack.base_url.replace("http", "ws") + "/ws"
    async with websockets.connect(url) as ws:
        raw = await ws.recv()
    row = json.loads(raw)
    assert set(row) >= {"id", "ts", "port", "dir", "chan", "raw"}


# -- reconnect ------------------------------------------------------------------------


def test_reconnect_after_sim_drop(stack: Stack) -> None:
    assert stack.wait_connected(True)
    stack.stop_sim()
    assert stack.wait_connected(False), "daemon did not notice the dropped connection"
    stack.restart_sim()
    assert stack.wait_connected(True), "daemon did not reconnect"
    with client(stack) as c:
        r = c.post("/cmd", json={"cmd": "ping", "timeout_ms": 1500}).json()
    assert r["status"] == "ok"
