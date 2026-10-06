"""A --json error names its cause from a fixed vocabulary (SPEC 4 `kind`)."""

from __future__ import annotations

import json

import httpx
import pytest

from mcuscope import cli
from mcuscope.cli_client import cli_field_names
from mcuscope.cli_output import ERROR_KINDS
from tests.support import DEAD, UNREACHABLE, canned


def _json_error(monkeypatch, capsys, status: int, msg: str, argv: list[str]) -> dict:
    canned(monkeypatch, lambda request: httpx.Response(status, json={"error": msg}))
    rc = cli.main(["--json", *argv, *UNREACHABLE])
    out = capsys.readouterr().out
    obj = json.loads(out)
    assert rc == obj["exit_code"] == 1, out
    return obj


@pytest.mark.parametrize(("status", "msg", "kind"), [
    (400, "port is ambiguous; specify one of: a, b", "ambiguous_port"),
    (400, "no such port: nope", "no_such_port"),
    (400, "port board is not connected", "port_disconnected"),
    (400, "no such session: run-9", "no_such_session"),
    (400, "bad match regex: missing )", "bad_regex"),
    (400, "session needs a retrospective window", "usage"),
    (413, "request body too large", "usage"),
    (500, "database is locked", "daemon_error"),
    (503, "too many subscribers (max 256)", "daemon_error"),
])
def test_a_daemon_refusal_carries_its_kind(monkeypatch, capsys, status, msg, kind) -> None:
    obj = _json_error(monkeypatch, capsys, status, msg, ["lines"])
    assert obj["kind"] == kind
    assert not obj["error"].startswith("error: "), obj   # the human prefix stays on stderr


def test_the_human_message_keeps_its_prefix(monkeypatch, capsys) -> None:
    canned(monkeypatch, lambda request: httpx.Response(400, json={"error": "no such port: x"}))
    assert cli.main(["lines", *UNREACHABLE]) == 1
    assert capsys.readouterr().err == "error: no such port: x\n"


def test_a_422_names_the_flags_the_user_typed(monkeypatch, capsys) -> None:
    obj = _json_error(monkeypatch, capsys, 422,
                      "timeout_ms: Input should be greater than 0 (got 0); "
                      "forbid.16: String too long", ["lines"])
    assert obj == {"error": "--timeout: Input should be greater than 0 (got 0); "
                            "--forbid #17: String too long",
                   "kind": "usage", "exit_code": 1}


def test_an_unknown_field_is_left_as_the_daemon_named_it() -> None:
    assert cli_field_names("frobnicate: bad") == "frobnicate: bad"
    # Only a leading field name is mapped, not the same word inside the message.
    assert cli_field_names("request: limit is wrong") == "request: limit is wrong"


def test_an_unreachable_daemon_is_kind_unreachable(capsys) -> None:
    assert cli.main(["--json", "status", "--url", DEAD]) == 3
    assert json.loads(capsys.readouterr().out)["kind"] == "unreachable"


def test_a_cli_refusal_before_any_request_is_kind_usage(capsys) -> None:
    assert cli.main(["--json", "purge", *UNREACHABLE]) == 1
    obj = json.loads(capsys.readouterr().out)
    assert obj["kind"] == "usage" and "exactly one of" in obj["error"]


def test_a_click_usage_error_is_kind_usage(capsys) -> None:
    assert cli.main(["--json", "lines", "--limit", "x", *UNREACHABLE]) == 1
    assert json.loads(capsys.readouterr().out)["kind"] == "usage"


def test_an_unknown_session_named_by_the_cli_is_no_such_session(monkeypatch, capsys) -> None:
    canned(monkeypatch, lambda request: httpx.Response(200, json={"sessions": []}))
    assert cli.main(["--json", "session", "delete", "gone", *UNREACHABLE]) == 1
    obj = json.loads(capsys.readouterr().out)
    assert obj == {"error": "no such session: gone", "kind": "no_such_session",
                   "exit_code": 1}


def test_a_client_side_bad_match_is_bad_regex(monkeypatch, capsys) -> None:
    canned(monkeypatch, lambda request: httpx.Response(200, json={"ports": [], "lines": []}))
    assert cli.main(["--json", "tail", "-f", "--match", "(", *UNREACHABLE]) == 1
    rows = [json.loads(ln) for ln in capsys.readouterr().out.splitlines()]
    assert rows[-1]["kind"] == "bad_regex", rows


def test_a_malformed_daemon_answer_is_daemon_error(monkeypatch, capsys) -> None:
    canned(monkeypatch, lambda request: httpx.Response(200, json={"lines": "nope"}))
    assert cli.main(["--json", "lines", *UNREACHABLE]) == 1
    assert json.loads(capsys.readouterr().out)["kind"] == "daemon_error"


def test_the_vocabulary_is_the_documented_one() -> None:
    assert set(ERROR_KINDS) == {
        "ambiguous_port", "no_such_port", "port_disconnected", "no_such_session",
        "bad_regex", "usage", "unreachable", "daemon_error",
    }
    guide = cli.AI_GUIDE
    assert all(kind in guide for kind in ERROR_KINDS)


@pytest.mark.parametrize(("reason", "kind"), [
    ("no such port: typo", "no_such_port"),
    ("token required", "daemon_error"),
])
def test_a_refused_follow_takes_its_kind_from_the_close_reason(
    monkeypatch, capsys, reason, kind
) -> None:
    import typer
    import websockets
    import websockets.exceptions
    import websockets.frames

    from mcuscope.cli_client import Settings
    from mcuscope.cli_output import set_json_mode

    def connect(*a, **kw):
        raise websockets.exceptions.ConnectionClosedError(
            websockets.frames.Close(1008, reason), None)

    monkeypatch.setattr(websockets, "connect", connect)
    set_json_mode(True)
    try:
        with pytest.raises(typer.Exit):
            cli._follow_ws(Settings(url=DEAD, json_out=True, port=None), None, None)
    finally:
        set_json_mode(False)
    assert json.loads(capsys.readouterr().out)["kind"] == kind
