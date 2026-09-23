"""`mcu wait`/`mcu assert` over a refused send, an empty window, and their refusals (SPEC 4)."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.test_cli import run_mcu_canned

ERR = {"status": "err", "err_code": 1, "err_name": "badcmd", "err_detail": "unknown reset"}
LINE = {"id": 7, "ts": 1.0, "port": "b", "dir": "rx", "chan": "event", "seq": None,
        "raw": "BOOT OK"}


def _answer(body: dict, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ports":
            return httpx.Response(200, json={"ports": [{"alias": "b"}]})
        if seen is not None:
            seen.append(json.loads(request.content or b"{}"))
        return httpx.Response(200, json=body)
    return handler


def _verdict(status: str, **extra) -> dict:
    return {"status": status, "expect": [], "forbid": [
        {"pattern": "PANIC", "matched": False, "line": None}],
        "checked_lines": 0 if status == "empty" else 5, "elapsed_ms": 1.0, **extra}


# -- wait --send answered with ERR -----------------------------------------------------


def test_wait_whose_send_was_refused_exits_1_naming_the_err(monkeypatch, capsys) -> None:
    res = {"status": "timeout", "line": None, "waited_ms": 1500.0, "cmd_result": ERR,
           "sends": 1}
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _answer(res),
                                  "wait", "--send", "reset", "--match", "X")
    assert rc == 1, err
    assert "--send 'reset' was refused: ERR 1 badcmd unknown reset" in err
    assert "timeout: no line matched" not in err


def test_wait_whose_send_was_refused_is_1_even_on_a_match(monkeypatch, capsys) -> None:
    res = {"status": "match", "line": LINE, "waited_ms": 5.0, "cmd_result": ERR, "sends": 1}
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _answer(res),
                                  "wait", "--send", "reset", "--match", "BOOT")
    assert rc == 1, err
    assert "BOOT OK" in out and "was refused" in err


def test_wait_send_refusal_in_json_mode_is_one_object_and_exit_1(monkeypatch, capsys) -> None:
    res = {"status": "timeout", "line": None, "waited_ms": 1.0, "cmd_result": ERR, "sends": 1}
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _answer(res),
                                  "--json", "wait", "--send", "reset", "--match", "X")
    assert rc == 1
    assert json.loads(out)["cmd_result"]["status"] == "err"


def test_wait_whose_send_went_unanswered_is_still_a_timeout(monkeypatch, capsys) -> None:
    """The ruling covers ERR only; a send with no response keeps exit 2 and says so."""
    res = {"status": "timeout", "line": None, "waited_ms": 1500.0,
           "cmd_result": {"status": "timeout"}, "sends": 1}
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _answer(res),
                                  "wait", "--send", "reset", "--match", "X")
    assert rc == 2, err
    assert "(sent 1, the command got no response)" in err


def test_wait_whose_send_was_accepted_matches_as_before(monkeypatch, capsys) -> None:
    """Positive control for the ERR branch: an ok cmd_result is a plain match."""
    res = {"status": "match", "line": LINE, "waited_ms": 5.0,
           "cmd_result": {"status": "ok", "data": ""}, "sends": 1}
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _answer(res),
                                  "wait", "--send", "go", "--match", "BOOT")
    assert rc == 0, err
    assert "BOOT OK" in out and "refused" not in err


# -- assert: a failed send, an empty window -----------------------------------------------


@pytest.mark.parametrize("sent, why", [
    (ERR, "ERR 1 badcmd unknown reset"),
    ({"status": "timeout"}, "no response (timeout)"),
])
def test_assert_names_a_send_that_failed(monkeypatch, capsys, sent, why) -> None:
    res = _verdict("fail", cmd_result=sent)
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _answer(res),
                                  "assert", "--send", "reset", "--forbid", "PANIC",
                                  "--timeout", "2000")
    assert rc == 1
    assert f"FAILED  send 'reset': {why}" in err
    assert "FAIL  5 lines checked" in out


def test_an_empty_verdict_is_exit_1_with_its_own_message(monkeypatch, capsys) -> None:
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _answer(_verdict("empty")),
                                  "assert", "--forbid", "PANIC", "--last-ms", "10")
    assert rc == 1
    assert "EMPTY  the window held no lines" in err and "--allow-empty" in err
    assert "PASS" not in out


def test_an_empty_verdict_in_json_mode_is_exit_1(monkeypatch, capsys) -> None:
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, _answer(_verdict("empty")),
                                "--json", "assert", "--forbid", "PANIC")
    assert rc == 1 and json.loads(out)["status"] == "empty"


def test_allow_empty_is_sent_only_when_given(monkeypatch, capsys) -> None:
    seen: list = []
    handler = _answer(_verdict("pass"), seen)
    rc, *_ = run_mcu_canned(monkeypatch, capsys, handler,
                            "assert", "--forbid", "PANIC", "--allow-empty")
    assert rc == 0 and seen[-1]["allow_empty"] is True
    rc, *_ = run_mcu_canned(monkeypatch, capsys, handler, "assert", "--forbid", "PANIC")
    assert rc == 0 and "allow_empty" not in seen[-1]


# -- refusals in option names, not the daemon's field names -------------------------------


@pytest.mark.parametrize("argv, msg", [
    (["wait", "--match", "x", "--repeat-ms", "50"], "--repeat-ms needs --send"),
    (["wait", "--match", "x", "--send", "a", "--repeat-ms", "5"],
     "--repeat-ms must be between 10 and --timeout (2000)"),
    (["wait", "--match", "x", "--send", "a", "--repeat-ms", "3000"],
     "--repeat-ms must be between 10 and --timeout (2000)"),
    (["assert", "--expect", "x", "--min-window", "5"], "--min-window needs a live window"),
])
def test_refusals_name_the_options_typed(monkeypatch, capsys, argv, msg) -> None:
    rc, _, err = run_mcu_canned(monkeypatch, capsys, _answer({}), *argv)
    assert rc == 1
    assert msg in err
    assert "repeat_ms" not in err and "timeout_ms" not in err and "min_window_ms" not in err
