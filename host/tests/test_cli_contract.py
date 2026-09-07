"""CLI contract tests from the 2026-09-04 adversarial review (C1..C11, D6, D10).

Every path out of `mcu` maps to SPEC 4's 0/1/2/3, with no traceback: these drive the
paths that did not - an unwritable stdout, an interrupt inside a command, an export the
daemon dies under, and the bounds the client was leaving to the daemon.
"""

from __future__ import annotations

import errno
import json
import sys

import httpx
import pytest
import typer

from mcuscope import cli
from mcuscope import protocol as p
from tests.support import Stack

UNREACHABLE = ["--url", "http://127.0.0.1:1"]


class _FullStdout:
    """A stdout whose every write fails the way a full disk or a quota does.

    No fileno(): _silence_stdout must not repoint the test runner's own descriptor.
    """

    def __init__(self) -> None:
        self.encoding = "utf-8"

    def write(self, text: str) -> int:
        raise OSError(errno.ENOSPC, "No space left on device")

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def _canned(monkeypatch, body):
    """Point every request at a transport answering `body` as JSON."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    monkeypatch.setattr(cli.Client, "open", lambda self: httpx.Client(transport=transport))


# -- C1 / C10: a stdout that cannot be written -----------------------------------------


def test_json_output_to_a_full_stdout_exits_1(monkeypatch, capsys) -> None:
    _canned(monkeypatch, {"version": "0", "uptime_s": 1, "db_path": "x", "ports": []})
    monkeypatch.setattr(sys, "stdout", _FullStdout())
    rc = cli.main(["--json", "status", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, "a write that failed is not a success, and never exit 120"
    assert "cannot write output" in err
    assert "Traceback" not in err


def test_json_rows_to_a_full_stdout_exit_1(monkeypatch, capsys) -> None:
    """The per-row emitter half of the same guard (`mcu --json tail -n 1`)."""
    _canned(monkeypatch, {"lines": [{"id": 1, "ts": 0.0, "chan": "debug", "raw": "hi"}],
                          "truncated": False})
    monkeypatch.setattr(sys, "stdout", _FullStdout())
    rc = cli.main(["--json", "tail", "-n", "1", *UNREACHABLE])
    assert rc == 1
    assert "cannot write output" in capsys.readouterr().err


def test_follow_output_error_is_not_reported_as_unreachable(stack: Stack, monkeypatch,
                                                            capsys) -> None:
    """C10: emit_stream's failure is ours, not the daemon's.

    The follow's `except OSError` arm wraps the emit as well as the socket, so a local
    write error was attributed to the daemon and exited 3.
    """
    monkeypatch.setattr(sys, "stdout", _FullStdout())
    rc = cli.main(["--json", "tail", "-n", "0", "-f", "--url", stack.base_url])
    err = capsys.readouterr().err
    assert rc == 1
    assert "unreachable" not in err
    assert "cannot write output" in err


# -- C2: Ctrl-C inside a command --------------------------------------------------------


def test_ctrl_c_inside_a_command_exits_1(monkeypatch, capsys) -> None:
    def boom(self, path: str, **kw: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.Client, "get", boom)
    rc = cli.main(["--json", "status", *UNREACHABLE])
    out, err = capsys.readouterr()
    assert rc == 1, "typer converts it to Exit(130); SPEC 4 says an interrupt is 1"
    assert json.loads(out) == {"error": "interrupted", "exit_code": 1}
    assert "interrupted" in err


# -- C3: a partial export is removed ----------------------------------------------------


def test_log_export_removes_a_partial_file_when_the_daemon_dies(monkeypatch, tmp_path,
                                                                capsys) -> None:
    out_file = tmp_path / "run.txt"

    def pages(s, params):
        yield [{"id": 1, "ts": 0.0, "chan": "debug", "raw": "first page"}]
        cli.die("daemon unreachable at http://127.0.0.1:1: gone", 3)

    monkeypatch.setattr(cli, "_iter_pages_asc", pages)
    rc = cli.main(["log", "export", "--limit", "0", "-o", str(out_file), *UNREACHABLE])
    assert rc == 3
    assert not out_file.exists(), "a short export reads exactly like a whole one"


# -- C4: a negative count is bad usage, not an empty answer ------------------------------


@pytest.mark.parametrize("args, opt", [
    (["lines", "--limit", "-1"], "--limit"),
    (["tail", "-n", "-1"], "-n"),
    (["can", "dump", "-n", "-1"], "-n"),
    (["session", "list", "--limit", "-1"], "--limit"),
])
def test_negative_limit_is_bad_usage(monkeypatch, capsys, args, opt) -> None:
    _canned(monkeypatch, {"lines": [], "truncated": False, "sessions": [], "frames": []})
    rc = cli.main([*args, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, "the daemon clamps a negative limit to 0, which reads as 'nothing found'"
    assert opt in err


# -- C5 / C11: timeout and min-window bounds --------------------------------------------


@pytest.mark.parametrize("args", [
    ["cmd", "x", "--timeout", "0"],
    ["wait", "--match", "x", "--timeout", "0"],
])
def test_a_zero_timeout_is_bad_usage_where_the_daemon_needs_one(capsys, args) -> None:
    rc = cli.main([*args, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1
    assert f"expected 1 to {cli.MAX_TIMEOUT_MS} ms" in err, \
        "the exit code alone is satisfied by the daemon's 422"


def test_assert_still_takes_a_zero_timeout(monkeypatch, capsys) -> None:
    """0 is `assert`'s retrospective mode, and stays legal."""
    _canned(monkeypatch, {"status": "pass", "checked_lines": 3, "elapsed_ms": 1.0,
                          "expect": [], "forbid": []})
    rc = cli.main(["assert", "--expect", "x", "--timeout", "0", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err


@pytest.mark.parametrize("args, msg", [
    (["--timeout", "1000", "--min-window", "5000"], "cannot exceed timeout_ms"),
    (["--min-window", "5000"], "needs a live window"),
    (["--timeout", "1000", "--min-window", "999999999"], "--min-window"),
])
def test_min_window_is_bounded_by_the_client(capsys, args, msg) -> None:
    rc = cli.main(["assert", "--expect", "x", *args, *UNREACHABLE])
    assert rc == 1
    assert msg in capsys.readouterr().err


# -- C8 / C9: the guide and the wire vocabularies ---------------------------------------

# Long spellings of short flags the guide gives (-f, -o); the guide names the short one.
GUIDE_EXEMPT = {"--follow", "--out"}


def _option_strings():
    """Every non-hidden option of every non-hidden subcommand, as (path, flag)."""
    root = typer.main.get_command(cli.app)
    found: list[tuple[str, str]] = []

    def walk(command, path: list[str]) -> None:
        # A group's own params are collected too: the root group carries the global
        # options, which the guide has to name like any other flag.
        for param in command.params:
            for opt in [*getattr(param, "opts", []), *getattr(param, "secondary_opts", [])]:
                if opt.startswith("-"):
                    found.append((" ".join(path) or "<root>", opt))
        # Duck-typed, not isinstance(click.Group): typer's group does not derive from it
        # under click 8.4.
        for name, sub in (getattr(command, "commands", None) or {}).items():
            if not getattr(sub, "hidden", False):
                walk(sub, [*path, name])

    walk(root, [])
    return found


def test_ai_guide_names_every_flag() -> None:
    missing = [(cmd, opt) for cmd, opt in _option_strings()
               if opt not in GUIDE_EXEMPT and opt not in cli.AI_GUIDE]
    assert not missing, f"AI_GUIDE (the agent's only view of the CLI) omits: {missing}"


def test_cli_eol_choices_match_the_protocol() -> None:
    assert set(cli.EOL_CHOICES) == set(p.EOL_BYTES)
    assert (cli.BUS_OPTION.min, cli.BUS_OPTION.max) == (p.CAN_BUS_MIN, p.CAN_BUS_MAX)


# -- D6: a daemon that ignores repeat_ms -------------------------------------------------


def test_wait_repeat_survives_a_daemon_without_the_send_counters(monkeypatch,
                                                                 capsys) -> None:
    """An older daemon accepts repeat_ms, ignores it, and answers without `sends`."""
    _canned(monkeypatch, {"status": "timeout", "line": None, "waited_ms": 1.0,
                          "cmd_result": None, "dropped": 0})
    rc = cli.main(["wait", "--match", "x", "--send", "", "--repeat-ms", "50",
                   "--timeout", "1000", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 2
    assert "Traceback" not in err and "unexpected response" not in err


# -- D10: sysrq takes one printable character --------------------------------------------


@pytest.mark.parametrize("char", ["\n", "\r", "\x00"])
def test_sysrq_refuses_a_non_printable_character(capsys, char) -> None:
    rc = cli.main(["sysrq", char, *UNREACHABLE])
    assert rc == 1, ("a non-printable SysRq character is bad usage, not a write the daemon "
                     "has to refuse")
    assert "printable" in capsys.readouterr().err


def test_sysrq_still_takes_one_printable_character(monkeypatch, capsys) -> None:
    _canned(monkeypatch, {"ok": True})
    assert cli.main(["sysrq", "b", *UNREACHABLE]) == 0, capsys.readouterr().err


# -- R1: an export that could not open must not delete the file ---------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="chmod 444 does not deny write on Windows")
def test_log_export_keeps_a_file_it_could_not_open(tmp_path, capsys) -> None:
    """The removal guard is armed only after the open succeeds."""
    target = tmp_path / "keep.txt"
    target.write_text("PRECIOUS DATA\n", encoding="utf-8")
    target.chmod(0o444)
    rc = cli.main(["log", "export", "-o", str(target), *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "cannot write" in err
    assert target.exists(), "a file this command never opened must survive the failure"
    assert target.read_text(encoding="utf-8") == "PRECIOUS DATA\n"


# -- R3: --eol without --send is bad usage, in the daemon's words -------------------------


@pytest.mark.parametrize("argv", [
    ["wait", "--match", "x", "--eol", "crlf", "--timeout", "1000"],
    ["assert", "--expect", "x", "--eol", "crlf", "--timeout", "1000"],
])
def test_eol_without_send_is_refused_client_side(capsys, argv) -> None:
    rc = cli.main([*argv, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "eol applies to send; set send too" in err
