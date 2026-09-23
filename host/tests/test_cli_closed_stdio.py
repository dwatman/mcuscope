"""A stdout closed at start, and a prompt nobody can answer (SPEC 4)."""

from __future__ import annotations

import io
import json
import subprocess
import sys

import httpx
import pytest

from tests.support import CHILD_TEXT, child_env
from tests.test_cli import MCU, run_mcu_canned

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="`>&-` is a POSIX shell form")
DEAD = "http://127.0.0.1:1"


def _run(shell_suffix: str, *args: str) -> subprocess.CompletedProcess[str]:
    """`mcu ARGS <shell_suffix>`, through sh so the fd can be closed before exec."""
    return subprocess.run(
        ["sh", "-c", f'exec "$@" {shell_suffix}', "sh", *MCU, *args],
        capture_output=True, **CHILD_TEXT, timeout=60, env=child_env(),
    )


@posix_only
@pytest.mark.parametrize("args", [["ai-guide"], ["--json", "ai-guide"]])
def test_a_command_with_stdout_closed_at_start_exits_1(args) -> None:
    r = _run(">&-", *args)
    assert r.returncode == 1, r.stderr
    assert "cannot write output" in r.stderr and "closed when mcu started" in r.stderr
    assert "Traceback" not in r.stderr


@posix_only
def test_the_same_command_with_stdout_open_exits_0() -> None:
    """Positive control: the shell wrapper alone changes nothing."""
    r = _run("", "ai-guide")
    assert r.returncode == 0 and "EXIT CODES" in r.stdout


@posix_only
def test_a_follow_with_stdout_closed_at_start_ends_at_once() -> None:
    """`tail -f` would follow into devnull for ever; it must end, before it connects."""
    r = _run(">&-", "--url", DEAD, "tail", "-f")
    assert r.returncode == 1, r.stderr
    assert "unreachable" not in r.stderr   # ended before it ever tried the daemon
    open_ = _run("", "--url", DEAD, "tail", "-f")
    assert open_.returncode == 3             # positive control: it does try, with stdout open


# -- confirmation prompts need a terminal -------------------------------------------------


def _purge_handler(sent: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        sent.append(body)
        return httpx.Response(200, json={"deleted": 5, "id_from": 1, "id_to": 5})
    return handler


@pytest.mark.parametrize("json_flag", [[], ["--json"]])
def test_a_prompt_on_a_piped_stdin_is_refused_not_read(monkeypatch, capsys, json_flag) -> None:
    sent: list[dict] = []
    stdin = io.StringIO("y\nprotocol line\n")
    monkeypatch.setattr(sys, "stdin", stdin)
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _purge_handler(sent),
                                  *json_flag, "purge", "--all")
    assert rc == 1
    assert "stdin is not a terminal; pass -y" in err
    assert [b["dry_run"] for b in sent] == [True]      # previewed, never deleted
    assert stdin.read() == "y\nprotocol line\n"         # not a byte consumed
    assert "[y/N]" not in err


def test_a_terminal_still_gets_the_prompt(monkeypatch, capsys) -> None:
    """Positive control: an interactive stdin is asked, and `y` deletes."""
    from mcuscope import cli_output

    sent: list[dict] = []
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
    monkeypatch.setattr(cli_output, "_stdin_is_interactive", lambda: True)
    rc, _, err = run_mcu_canned(monkeypatch, capsys, _purge_handler(sent), "purge", "--all")
    assert rc == 0, err
    assert "[y/N]" in err
    assert [b["dry_run"] for b in sent] == [True, False]
