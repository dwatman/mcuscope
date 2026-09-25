"""`mcu wait`/`mcu assert` over a refused send, an empty window, and their refusals (SPEC 4)."""

from __future__ import annotations

import json

import httpx
import pytest

from mcuscope import cli
from tests.support import UNREACHABLE, canned, recorder
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
    # The daemon judges no window after a failed send: verdict(0, ...), server.py.
    res = _verdict("fail", cmd_result=sent, checked_lines=0)
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _answer(res),
                                  "assert", "--send", "reset", "--forbid", "PANIC",
                                  "--timeout", "2000")
    assert rc == 1
    assert f"FAILED  send 'reset': {why}" in err
    assert "-       forbid 'PANIC': not judged" in out
    assert "never seen" not in out
    assert "FAIL  0 lines checked" in out


def test_an_unmatched_forbid_after_a_good_send_is_never_seen(monkeypatch, capsys) -> None:
    """Positive control: only a failed send turns the forbid line into "not judged"."""
    res = _verdict("pass", cmd_result={"status": "ok", "data": ""})
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _answer(res),
                                  "assert", "--send", "go", "--forbid", "PANIC",
                                  "--timeout", "2000")
    assert rc == 0, err
    assert "ok      forbid 'PANIC': never seen" in out and "not judged" not in out


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


# -- improvement 3: what the wait timed out on -----------------------------------------


def test_the_wait_timeout_line_names_the_pattern_the_port_and_the_wait(monkeypatch,
                                                                       capsys) -> None:
    recorder(monkeypatch, wait={"status": "timeout", "waited_ms": 1200.4})
    rc = cli.main(["-p", "sim", "wait", "--match", "^NEVER", "--timeout", "1200",
                   *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 2, "a timeout stays exit 2; die() would have made it 1"
    assert "^NEVER" in err and "sim" in err and "1200" in err, err


def test_the_wait_timeout_line_carries_the_send_counts_after_a_send(monkeypatch,
                                                                    capsys) -> None:
    # The only timeout a single send can reach: a failed write is a 400 before any wait.
    recorder(monkeypatch, wait={"status": "timeout", "waited_ms": 5.0, "sends": 1,
                                "send_failures": 0})
    rc = cli.main(["wait", "--match", "^NEVER", "--send", "ping", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 2
    assert err.rstrip().endswith("(sent 1)"), err


@pytest.mark.parametrize(
    ("argv", "body"),
    [
        # No --send: the daemon still reports sends=0, which says nothing worth printing.
        ([], {"sends": 0, "send_failures": 0}),
        # --repeat-ms prints its own counts line; the timeout line must not repeat them.
        (["--send", "ping", "--repeat-ms", "100"], {"sends": 7, "send_failures": 0}),
    ],
    ids=["no-send", "repeat"],
)
def test_the_wait_timeout_line_has_no_send_counts_when_they_do_not_apply(
        monkeypatch, capsys, argv, body) -> None:
    recorder(monkeypatch, wait={"status": "timeout", "waited_ms": 5.0, **body})
    rc = cli.main(["wait", "--match", "^NEVER", *argv, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 2
    assert "timeout: no line matched" in err, err
    assert "(sent " not in err, err


def test_the_wait_timeout_json_is_the_body_and_nothing_else(monkeypatch, capsys) -> None:
    body = {"status": "timeout", "waited_ms": 1200.4}
    recorder(monkeypatch, wait=body)
    rc = cli.main(["--json", "wait", "--match", "^NEVER", *UNREACHABLE])
    out = capsys.readouterr()
    assert rc == 2
    assert json.loads(out.out) == body
    assert "timeout: no line matched" not in out.err, "the prose form is text mode only"


@pytest.mark.parametrize("value", ["0", "-5000", str(10**15 + 1)])
def test_assert_last_ms_out_of_range_is_a_usage_error_before_any_request(value, capsys) -> None:
    rc = cli.main([*UNREACHABLE, "assert", "--forbid", "ERR", "--last-ms", value])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "is not in the range" in err
    assert "unreachable" not in err, "the bound was left to the daemon"


def test_assert_last_ms_in_range_reaches_the_daemon(capsys) -> None:
    """Positive control: the same command with a valid window gets as far as the request."""
    rc = cli.main([*UNREACHABLE, "assert", "--forbid", "ERR", "--last-ms", "1"])
    err = capsys.readouterr().err
    assert rc == 3, err
    assert "unreachable" in err


@pytest.mark.parametrize("argv", [
    ["wait", "--match", "READY"],
    ["assert", "--expect", "READY", "--timeout", "100"],
])
def test_a_daemon_that_never_answers_the_verdict_is_exit_1_not_2(monkeypatch, capsys, argv) -> None:
    """Exit 2 on `wait` means "nothing matched"; a transport timeout is not a verdict."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    canned(monkeypatch, handler)
    rc = cli.main([*UNREACHABLE, *argv])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "stopped answering" in err


def test_wait_that_matches_nothing_is_still_exit_2(monkeypatch, capsys) -> None:
    """Positive control: the daemon's own timeout verdict keeps exit 2."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "timeout", "line": None})

    canned(monkeypatch, handler)
    rc = cli.main([*UNREACHABLE, "wait", "--match", "READY"])
    assert rc == 2, capsys.readouterr().err
