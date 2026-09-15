"""A usage or error message with a closed std stream: the mapped exit code, no traceback and no
crash log (classes 9 and 35). Each case runs the real console entry in a child with the stream
connected to a pipe whose read end is closed, and the crash-log dir pointed at tmp_path."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.support import CHILD_TEXT, child_env

# The crash-log dir is pinned twice over: MCUSCOPE_DATA_DIR in the child's env (which wins),
# and the platformdirs patch below for the resolution behind it. A conftest monkeypatch does
# not reach a subprocess (class 33), and XDG variables do not move platformdirs on Windows.
CHILD = """
import sys, platformdirs
DATA = sys.argv[1]                     # bound now: sys.argv is replaced before the CLI runs
platformdirs.user_data_dir = lambda *a, **k: DATA
from mcuscope import _stdio
mode, argv = sys.argv[2], sys.argv[3:]
if mode == "no-stdout":
    sys.stdout = None                  # what an interpreter started with fd 1 closed has
if mode == "crash":
    def main():
        raise RuntimeError("closed-pipe-crash-sentinel")
    raise SystemExit(_stdio.console_entry(main, "mcu"))
from mcuscope import cli
sys.argv = ["mcu", *argv]
raise SystemExit(cli.console_entry())
"""


def _run(tmp_path, closed: str, *argv: str, mode: str = "cli") -> tuple[int, str, list[str]]:
    """Exit code, the open stream's text, and the files written to the crash-log dir."""
    data = tmp_path / "data"
    env = child_env(MCUSCOPE_URL="http://127.0.0.1:1", MCUSCOPE_DATA_DIR=str(data))
    r, w = os.pipe()
    os.close(r)
    kw = {"stdout": w, "stderr": subprocess.PIPE} if closed == "stdout" else \
         {"stdout": subprocess.PIPE, "stderr": w}
    try:
        p = subprocess.run([sys.executable, "-c", CHILD, str(data), mode, *argv],
                           env=env, timeout=60, **kw, **CHILD_TEXT)
    finally:
        os.close(w)
    other = p.stderr if closed == "stdout" else p.stdout
    files = sorted(os.listdir(data)) if data.exists() else []
    return p.returncode, other, files


def _attached(tmp_path, *argv: str) -> int:
    data = tmp_path / "attached"
    env = child_env(MCUSCOPE_URL="http://127.0.0.1:1", MCUSCOPE_DATA_DIR=str(data))
    return subprocess.run([sys.executable, "-c", CHILD, str(data), "cli", *argv], env=env,
                          capture_output=True, timeout=60, **CHILD_TEXT).returncode


# argv, and text only that path prints
CASES = {
    "nosuchcmd": (["nosuchcmd"], "No such command"),
    "bogus": (["lines", "--bogus"], "No such option: --bogus"),
    "range": (["lines", "--limit", "-1"], "is not in the range"),
    "group": (["daemon"], "Missing command"),              # a group with no subcommand
    "hoist": (["status", "--url"], "option --url needs a value"),   # caught while hoisting
    "die": (["status"], "daemon unreachable"),             # not usage: die(), exit 3
}
USAGE = [argv for argv, _ in CASES.values()]
TEXT = {" ".join(argv): text for argv, text in CASES.values()}


@pytest.mark.parametrize("argv", USAGE, ids=" ".join)
def test_a_closed_stderr_keeps_the_exit_code_and_writes_no_crash_log(tmp_path, argv) -> None:
    rc, _, files = _run(tmp_path, "stderr", *argv)
    assert rc == _attached(tmp_path, *argv) and rc in (1, 3), rc
    assert files == [], f"crash log written: {files}"


@pytest.mark.parametrize("argv", USAGE[:2], ids=" ".join)
def test_a_closed_stderr_still_emits_the_json_object(tmp_path, argv) -> None:
    rc, out, files = _run(tmp_path, "stderr", "--json", *argv)
    assert rc == 1 and files == []
    assert '"exit_code": 1' in out, out


@pytest.mark.parametrize("argv", USAGE, ids=" ".join)
def test_a_closed_stdout_keeps_the_exit_code_and_the_message(tmp_path, argv) -> None:
    rc, err, files = _run(tmp_path, "stdout", *argv)
    assert rc == _attached(tmp_path, *argv) and rc in (1, 3), rc
    assert files == []
    assert "Traceback" not in err, err
    assert TEXT[" ".join(argv)] in err, err


@pytest.mark.skipif(os.name == "nt", reason="on Windows the stream repair opens the console")
@pytest.mark.parametrize("argv", [["nosuchcmd"], ["status"], ["--help"]], ids=" ".join)
def test_the_repair_warning_on_a_closed_stderr_does_not_own_the_exit(tmp_path, argv) -> None:
    """stdout None makes console_entry warn on stderr; with stderr a closed pipe that write
    raised before main() ran, and every call exited 120."""
    rc, _, files = _run(tmp_path, "stderr", *argv, mode="no-stdout")
    assert rc == {"nosuchcmd": 1, "status": 3, "--help": 0}[argv[0]], rc
    assert files == []


def test_a_crash_with_a_closed_stderr_is_still_logged_and_exits_1(tmp_path) -> None:
    """The positive control for the no-crash-log assertions: a real crash lands in the dir."""
    rc, _, files = _run(tmp_path, "stderr", mode="crash")
    assert rc == 1, rc
    assert files == ["mcu-crash.log"]
    log = (tmp_path / "data" / "mcu-crash.log").read_text(encoding="utf-8")
    assert "closed-pipe-crash-sentinel" in log
    assert "BrokenPipeError" not in log
