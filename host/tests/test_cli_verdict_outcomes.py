"""`wait` and `assert` outcomes the daemon reports beyond pass/match: a failed send, a shed
window, a timeout on a port that is down (SPEC 4, rulings OP-9, AGENTUX-4, AGENTUX-7)."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.support import Stack
from tests.test_cli import run_mcu, run_mcu_canned

LINE = {"id": 7, "ts": 1.0, "port": "b", "dir": "rx", "chan": "event", "seq": None,
        "raw": "BOOT OK"}


def _answer(body: dict, ports: list[dict], seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ports":
            return httpx.Response(200, json={"ports": ports, "stored": []})
        if seen is not None:
            seen.append(json.loads(request.content or b"{}"))
        return httpx.Response(200, json=body)
    return handler


TIMEOUT = {"status": "timeout", "line": None, "waited_ms": 500.0, "cmd_result": None,
           "dropped": 0, "sends": 0, "send_failures": 0}
DOWN = {"alias": "b2", "connected": False, "disconnect_reason": "open_failed"}
UP = {"alias": "b", "connected": True}


# -- a wait that timed out on a port that is down says so ------------------------------------


def test_a_wait_timeout_on_a_down_port_names_its_state(monkeypatch, capsys) -> None:
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _answer(TIMEOUT, [UP, DOWN]),
                                  "-p", "b2", "wait", "--match", "x")
    assert rc == 2, err
    assert err.strip().endswith("; port b2 is disconnected (open_failed)"), err


def test_a_wait_timeout_on_a_connected_port_adds_nothing(monkeypatch, capsys) -> None:
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _answer(TIMEOUT, [UP, DOWN]),
                                  "-p", "b", "wait", "--match", "x")
    assert rc == 2 and "timeout: no line matched 'x' on port b in 500 ms" in err
    assert "disconnected" not in err


def test_without_p_the_one_attached_port_is_the_one_watched(monkeypatch, capsys) -> None:
    rc, _, err = run_mcu_canned(monkeypatch, capsys, _answer(TIMEOUT, [DOWN]),
                                "wait", "--match", "x")
    assert rc == 2 and "port b2 is disconnected (open_failed)" in err


def test_without_p_several_ports_name_no_single_culprit(monkeypatch, capsys) -> None:
    rc, _, err = run_mcu_canned(monkeypatch, capsys, _answer(TIMEOUT, [DOWN, UP]),
                                "wait", "--match", "x")
    assert rc == 2 and "disconnected" not in err


def test_a_port_whose_state_is_not_reported_gets_no_claim(monkeypatch, capsys) -> None:
    rc, _, err = run_mcu_canned(monkeypatch, capsys, _answer(TIMEOUT, [{"alias": "b"}]),
                                "-p", "b", "wait", "--match", "x")
    assert rc == 2 and err.strip().endswith("in 500 ms"), err


# -- assert: incomplete, --allow-dropped, and where each verdict line goes -------------------


def _verdict(status: str, **extra) -> dict:
    return {"status": status, "reason": None, "expect": [], "forbid": [
        {"pattern": "PANIC", "matched": False, "line": None}],
        "checked_lines": 0 if status == "empty" else 5, "elapsed_ms": 1.0, "dropped": 0,
        **extra}


def test_an_incomplete_verdict_is_exit_1_on_stdout(monkeypatch, capsys) -> None:
    rc, out, err = run_mcu_canned(monkeypatch, capsys,
                                  _answer(_verdict("incomplete", dropped=3), [UP]),
                                  "assert", "--forbid", "PANIC")
    assert rc == 1
    assert "INCOMPLETE  3 lines were shed unjudged" in out and "--allow-dropped" in out
    assert "PASS" not in out and "FAIL " not in out
    assert "3 lines were shed during the window" in err   # the warning stays on stderr


def test_an_incomplete_verdict_in_json_is_exit_1(monkeypatch, capsys) -> None:
    rc, out, _ = run_mcu_canned(monkeypatch, capsys,
                                _answer(_verdict("incomplete", dropped=3), [UP]),
                                "--json", "assert", "--forbid", "PANIC")
    assert rc == 1 and json.loads(out)["status"] == "incomplete"


def test_allow_dropped_is_sent_only_when_given(monkeypatch, capsys) -> None:
    seen: list = []
    handler = _answer(_verdict("pass"), [UP], seen)
    rc, *_ = run_mcu_canned(monkeypatch, capsys, handler,
                            "assert", "--forbid", "PANIC", "--allow-dropped")
    assert rc == 0 and seen[-1]["allow_dropped"] is True
    rc, *_ = run_mcu_canned(monkeypatch, capsys, handler, "assert", "--forbid", "PANIC")
    assert rc == 0 and "allow_dropped" not in seen[-1]


@pytest.mark.parametrize("status", ["pass", "fail", "empty", "incomplete"])
def test_every_verdict_line_goes_to_stdout(monkeypatch, capsys, status) -> None:
    word = {"pass": "PASS", "fail": "FAIL", "empty": "EMPTY", "incomplete": "INCOMPLETE"}
    _, out, err = run_mcu_canned(monkeypatch, capsys,
                                  _answer(_verdict(status, dropped=1), [UP]),
                                  "assert", "--forbid", "PANIC")
    assert any(line.startswith(word[status] + " ") for line in out.splitlines()), out
    assert not any(line.startswith(word[status] + " ") for line in err.splitlines()), err


# -- against a running daemon -----------------------------------------------------------------


def test_a_refused_send_ends_the_wait_at_once(stack: Stack) -> None:
    r = run_mcu(stack, "--json", "wait", "--send", "bogus", "--match", "zzzz",
                "--timeout", "60000")
    assert r.returncode == 1, r.stderr
    res = json.loads(r.stdout)
    assert res["status"] == "send_failed" and res["cmd_result"]["status"] == "err"
    assert res["waited_ms"] < 60000   # the window was not waited out
    r = run_mcu(stack, "wait", "--send", "bogus", "--match", ".")
    assert r.returncode == 1 and r.stdout == ""   # no "match" line printed as success
    assert "--send 'bogus' failed: ERR 1 badcmd" in r.stderr
