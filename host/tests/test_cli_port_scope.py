"""`-p` on every command and the `[port]` column of a finished read (SPEC 4, rulings CLI-3
and OP-8)."""

from __future__ import annotations

import json

import httpx
import pytest

from mcuscope import cli
from mcuscope.render import fmt_line
from tests.support import UNREACHABLE, Stack, canned
from tests.test_cli import run_mcu, run_mcu_canned


def _ports(attached: list[str], stored: list[str], seen: list[str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request.url.path)
        if request.url.path == "/ports":
            return httpx.Response(200, json={"ports": [{"alias": a} for a in attached],
                                             "stored": stored})
        return httpx.Response(200, json={"sessions": [], "ports": [], "version": "x",
                                         "uptime_s": 1, "db_path": "x", "devices": [],
                                         "deleted": 0, "enabled": False, "dest": "x"})
    return handler


_PORT_UNUSED_ARGV = [
    ["status"], ["ports"], ["devices"], ["plotjuggler"], ["pj"], ["session", "list"],
    ["session", "start", "q"], ["detach", "x"],
    ["attach", "socket://127.0.0.1:1"],
]


@pytest.mark.parametrize("argv", _PORT_UNUSED_ARGV)
def test_a_command_that_ignores_p_still_refuses_an_unknown_one(monkeypatch, capsys,
                                                               argv) -> None:
    seen: list[str] = []
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _ports(["a"], ["a"], seen),
                                  "--json", "-p", "nosuch", *argv)
    assert rc == 1, err
    assert json.loads(out) == {"error": "no such port: nosuch", "kind": "no_such_port",
                               "exit_code": 1}
    assert seen == ["/ports"], "refused before the command's own request"


@pytest.mark.parametrize("argv", _PORT_UNUSED_ARGV)
def test_help_after_an_unknown_p_asks_no_daemon(monkeypatch, capsys, argv) -> None:
    seen: list[str] = []
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _ports(["a"], [], seen),
                                  "-p", "nosuch", *argv, "--help")
    assert rc == 0, err
    assert "Usage:" in out and seen == []


@pytest.mark.parametrize("alias", ["a", "nosuch"])
def test_purge_refuses_any_p_before_asking_the_daemon(monkeypatch, capsys, alias) -> None:
    """Purge deletes every port's rows: a -p that looked like it scoped it would not."""
    seen: list[str] = []
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _ports(["a"], ["a"], seen),
                                  "--json", "-p", alias, "purge", "--all", "-y")
    assert rc == 1, err
    assert json.loads(out) == {"error": "purge removes every port's rows; -p does not scope it",
                               "kind": "usage", "exit_code": 1}
    assert seen == []
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, _ports(["a"], ["a"], seen),
                                "-p", alias, "purge", "--help")
    assert rc == 0 and "Usage:" in out and seen == []


@pytest.mark.parametrize("stored", ["x", 7, None])
def test_a_stored_field_that_is_not_a_list_matches_nothing(monkeypatch, capsys,
                                                           stored) -> None:
    """A string `stored` must not match by its characters, nor a number crash the check."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ports": [{"alias": "a"}], "stored": stored})
    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler, "--json", "-p", "x", "status")
    assert rc == 1, err
    assert json.loads(out)["kind"] == "no_such_port", out


@pytest.mark.parametrize("alias", ["a", "gone"])
def test_an_attached_or_stored_port_passes(monkeypatch, capsys, alias) -> None:
    """A detached board's history stays addressable with -p."""
    seen: list[str] = []
    rc, _, err = run_mcu_canned(monkeypatch, capsys, _ports(["a"], ["a", "gone"], seen),
                                "-p", alias, "session", "list")
    assert rc == 0, err
    assert seen == ["/ports", "/sessions"]


def test_a_local_command_makes_no_request_for_p(monkeypatch, capsys) -> None:
    seen: list[str] = []
    canned(monkeypatch, _ports([], [], seen))
    assert cli.main(["-p", "nosuch", "ai-guide", *UNREACHABLE]) == 0
    assert cli.main(["-p", "nosuch", "config", "path", *UNREACHABLE]) == 0
    assert seen == []


def test_without_p_nothing_is_checked(monkeypatch, capsys) -> None:
    seen: list[str] = []
    rc, _, _ = run_mcu_canned(monkeypatch, capsys, _ports([], [], seen), "session", "list")
    assert rc == 0 and seen == ["/sessions"]


# -- the [port] column of a finished result ---------------------------------------------------


def _row(i: int, port: str) -> dict:
    return {"id": i, "ts": 1.0 + i / 1000, "port": port, "dir": "rx", "chan": "debug",
            "seq": None, "raw": f"row {i}"}


def _lines(rows: list[dict], attached: list[str], stored: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ports":
            return httpx.Response(200, json={"ports": [{"alias": a} for a in attached],
                                             "stored": stored})
        return httpx.Response(200, json={"lines": rows[::-1], "truncated": False})
    return handler


def test_a_one_board_result_is_tagged_when_two_boards_are_attached(monkeypatch,
                                                                   capsys) -> None:
    """AGENTUX-3: an ERR hit from one of two boards came back untagged."""
    rows = [_row(1, "a"), _row(2, "a")]
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, _lines(rows, ["a", "b"], ["a"]),
                                "lines")
    assert rc == 0 and out.count("[a] ") == 2, out


def test_a_board_with_stored_rows_counts_too(monkeypatch, capsys) -> None:
    rows = [_row(1, "a")]
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, _lines(rows, ["a"], ["a", "old"]),
                                "tail", "-n", "5")
    assert rc == 0 and "[a] " in out, out


def test_one_board_attached_and_stored_is_untagged(monkeypatch, capsys) -> None:
    """Positive control: the rule does not tag everything."""
    rows = [_row(1, "a"), _row(2, "")]
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, _lines(rows, ["a"], ["a"]), "lines")
    assert rc == 0 and "[" not in out, out


def test_the_daemons_own_row_is_tagged_with_a_dash() -> None:
    row = {"ts": 1.0, "port": "", "chan": "marker", "raw": "boundary"}
    assert " [-] marker| boundary" in fmt_line(row, show_port=True)
    assert "[" not in fmt_line(row, show_port=False)


def test_a_p_read_returns_the_daemons_own_rows_but_a_verdict_does_not(stack: Stack) -> None:
    """OP-7: a marker made without -p is context in every board's read, never a match."""
    assert run_mcu(stack, "mark", "UNSCOPED-MARK").returncode == 0
    r = run_mcu(stack, "-p", stack.alias, "lines", "--chan", "marker", "--match", "UNSCOPED")
    assert r.returncode == 0 and "UNSCOPED-MARK" in r.stdout, r.stderr
    r = run_mcu(stack, "-p", stack.alias, "assert", "--chan", "marker", "--expect",
                "UNSCOPED-MARK", "--last-ms", "60000")
    assert r.returncode == 1, r.stdout
    assert "never seen" in r.stderr
