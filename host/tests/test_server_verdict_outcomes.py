"""Verdicts that must not read as success (SPEC 3.4): a `/wait` whose cmd-mode send failed
ends at once as `send_failed`, and an `/assert` over a window with shed rows is
`incomplete` unless it is decided (a forbid matched; no forbid and every expect met) or
`allow_dropped` accepts it."""

from __future__ import annotations

import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from mcuscope import server as server_mod
from mcuscope.serial_link import SerialPort
from tests.support import Stack, mk_app, on_loop


def _http(stack: Stack) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=20.0)


# -- /wait: a refused or unanswered send ends the window ------------------------------------


def test_a_wait_whose_send_is_answered_err_ends_as_send_failed(stack: Stack) -> None:
    with _http(stack) as h:
        # `.` matches the ERR row itself: before the fix that was the wait's "match".
        body = h.post("/wait", json={"send": "reset", "match": ".", "timeout_ms": 5000}).json()
    assert body["status"] == "send_failed", body
    assert body["line"] is None
    assert body["cmd_result"]["status"] == "err"
    assert body["sends"] == 1 and body["send_failures"] == 0


def test_a_wait_whose_send_is_answered_ok_still_waits(stack: Stack) -> None:
    # Positive control: the same call with a command the sim accepts is judged as before.
    with _http(stack) as h:
        body = h.post("/wait", json={
            "send": "ping", "match": "OK", "timeout_ms": 3000, "chan": "resp",
        }).json()
    assert body["status"] == "match" and body["cmd_result"]["status"] == "ok", body


def test_a_wait_whose_send_times_out_ends_as_send_failed(stack: Stack, monkeypatch) -> None:
    async def unanswered(self, cmd, timeout_ms, eol=None):
        return {"status": "timeout", "seq": 1, "line_id": None, "latency_ms": None}

    monkeypatch.setattr(SerialPort, "send_command", unanswered)
    with _http(stack) as h:
        body = h.post("/wait", json={"send": "ping", "match": ".", "timeout_ms": 5000}).json()
    assert body["status"] == "send_failed" and body["cmd_result"]["status"] == "timeout", body


def test_a_raw_send_never_ends_as_send_failed(stack: Stack) -> None:
    # Only a cmd-mode send has an answer to judge; a raw one is judged by the match alone.
    with _http(stack) as h:
        body = h.post("/wait", json={
            "send": "nonsense", "send_mode": "raw", "match": "zzzz", "timeout_ms": 300,
        }).json()
    assert body["status"] == "timeout" and body["cmd_result"] is None, body


# -- /assert: a window with holes ------------------------------------------------------------


@pytest.fixture
def c(tmp_path):
    with TestClient(mk_app(tmp_path), base_url="http://127.0.0.1") as client:
        yield client


def _live_assert(c: TestClient, body: dict, raw: str = "HELLO") -> dict:
    """A live /assert, fed `raw` device lines until it answers."""
    done = threading.Event()
    out: dict = {}

    def feed() -> None:
        while not done.is_set():
            on_loop(c, c.app.state.store.add_line(
                ts=time.time(), port="", dir="rx", chan="debug", seq=None, raw=raw))
            time.sleep(0.02)

    t = threading.Thread(target=feed, daemon=True)
    t.start()
    try:
        out = c.post("/assert", json={"timeout_ms": 600, **body}).json()
    finally:
        done.set()
        t.join(5)
    return out


@pytest.fixture
def shedding(monkeypatch):
    monkeypatch.setattr(server_mod.CaptureWatch, "dropped_total", lambda self: 3)


def test_a_pass_over_shed_rows_is_incomplete(c, shedding) -> None:
    body = _live_assert(c, {"forbid": ["PANIC"]})
    assert body["status"] == "incomplete", body
    assert body["checked_lines"] > 0 and body["dropped"] == 3
    assert body["reason"] == "3 lines were dropped unjudged; retry, or set allow_dropped"


def test_an_unmet_expect_over_shed_rows_is_incomplete_not_fail(c, shedding) -> None:
    # The expected line may be among the shed rows: "not seen" is not judged.
    body = _live_assert(c, {"expect": ["NEVER"]})
    assert body["status"] == "incomplete", body


def test_every_expect_met_with_no_forbid_passes_whatever_was_shed(c, shedding) -> None:
    # A shed row cannot undo a match, and there is no forbid it could have hidden.
    body = _live_assert(c, {"expect": ["HELLO", "HEL+O"]})
    assert body["status"] == "pass" and body["reason"] is None and body["dropped"] == 3, body


def test_every_expect_met_beside_an_unmatched_forbid_is_incomplete(c, shedding) -> None:
    # The forbidden line may be among the shed rows.
    body = _live_assert(c, {"expect": ["HELLO"], "forbid": ["PANIC"]})
    assert body["status"] == "incomplete", body


def test_a_forbid_that_matched_fails_whatever_was_shed(c, shedding) -> None:
    body = _live_assert(c, {"forbid": ["HELLO"]})
    assert body["status"] == "fail" and body["reason"] is None, body


def test_allow_dropped_judges_the_window_as_it_is(c, shedding) -> None:
    body = _live_assert(c, {"forbid": ["PANIC"], "allow_dropped": True})
    assert body["status"] == "pass", body
    assert body["dropped"] == 3


def test_a_window_with_nothing_shed_passes(c) -> None:
    # Positive control for the fixture: without shedding the same call is a pass.
    body = _live_assert(c, {"forbid": ["PANIC"]})
    assert body["status"] == "pass" and body["dropped"] == 0, body


def test_allow_dropped_is_refused_as_a_non_boolean(c) -> None:
    r = c.post("/assert", json={"forbid": ["x"], "allow_dropped": "yes"})
    assert r.status_code == 422 and "allow_dropped" in r.json()["error"]


# -- /assert refusals name the CLI flag beside the wire field ---------------------------------


@pytest.mark.parametrize(("body", "text"), [
    ({"forbid": ["x"], "session": "s", "timeout_ms": 100},
     "session needs a retrospective window (leave timeout_ms at 0; drop --timeout on the CLI)"),
    ({"forbid": ["x"], "last_ms": 5, "timeout_ms": 100},
     "last_ms needs a retrospective window (leave timeout_ms at 0; drop --timeout on the CLI)"),
    ({"forbid": ["x"], "send": "ping"},
     "send needs a live window (set timeout_ms too; --timeout on the CLI)"),
    ({"forbid": ["x"], "min_window_ms": 5},
     "min_window_ms needs a live window (set timeout_ms too; --timeout on the CLI)"),
    ({"forbid": ["x"], "min_window_ms": 500, "timeout_ms": 100},
     "min_window_ms cannot exceed timeout_ms (--timeout on the CLI)"),
])
def test_a_mode_refusal_names_the_cli_flag(c, body, text) -> None:
    r = c.post("/assert", json=body)
    assert r.status_code == 400 and r.json() == {"error": text}
