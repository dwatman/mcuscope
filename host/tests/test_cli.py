"""CLI tests: drive the `mcu` entry point as a subprocess against a live daemon.

Drives the **installed `mcu` console script** where the environment has one, with
MCUSCOPE_URL pointed at the per-test stack, so the real exit-code contract and --json
output shapes are exercised end to end. Cross-platform.
"""

from __future__ import annotations

import contextlib
import errno
import io
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
import typer

from mcuscope.cli import Client, Settings
from tests.support import CHILD_TEXT, Stack


def _mcu_command() -> list[str]:
    """The shipped `mcu` wrapper when this environment has one, else `python -m`.

    The wrapper is the artifact a user runs, and `python -m` is demonstrably not it: it
    reports a different prog name in every usage and error message, and it prepends the
    CWD to sys.path, so the suite imports the source tree where `mcu` imports the
    installed package - which is how a packaging regression ships with a green suite.
    Every Windows startup bug in the changelog originates in the wrapper.

    test_scaffold guarantees the fallback is not a silent hole: a declared-but-missing
    console script fails there rather than skipping.
    """
    from tests.test_scaffold import _console_script

    script = _console_script("mcu")
    return [script] if script else [sys.executable, "-m", "mcuscope.cli"]


MCU = _mcu_command()
# Below pytest-timeout's 90 s, above what a loaded Windows CI runner under coverage needs:
# 20 s timed out `mcu session export` on a 10-minute run (2026-08-31), with nothing hung.
CLI_TIMEOUT_S = 60.0


def run_mcu(
    stack: Stack | None,
    *args: str,
    url: str | None = None,
    timeout: float = CLI_TIMEOUT_S,
    stdin: str = "",
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MCUSCOPE_URL"] = url if url is not None else (stack.base_url if stack else "")
    return subprocess.run(
        [*MCU, *args], capture_output=True, **CHILD_TEXT, env=env, timeout=timeout, input=stdin
    )


def run_mcu_closed_pipe(
    stack: Stack | None, *args: str, url: str | None = None, timeout: float = CLI_TIMEOUT_S,
) -> tuple[int, str]:
    """Run `mcu ...` with its stdout closed under it, the way `| head -1` ends.

    Closing the read end is exactly what head does when it has read enough, and doing it
    from Popen rather than through a shell pipeline behaves the same on Windows. stderr is
    drained while the child runs, so a chatty command cannot deadlock on a full pipe.
    """
    env = os.environ.copy()
    env["MCUSCOPE_URL"] = url if url is not None else (stack.base_url if stack else "")
    proc = subprocess.Popen(
        [*MCU, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, **CHILD_TEXT, env=env
    )
    assert proc.stdout is not None and proc.stderr is not None
    proc.stdout.close()
    try:
        errout = proc.stderr.read()
        proc.wait(timeout=timeout)
    finally:
        proc.kill()          # a follow that ignored the closed pipe must not outlive the test
        proc.stderr.close()
    return proc.returncode, errout


# -- exit-code contract ---------------------------------------------------------------


def test_cmd_ok_exit0_prints_data(stack: Stack) -> None:
    r = run_mcu(stack, "cmd", "i2c scan")
    assert r.returncode == 0
    assert r.stdout.strip() == "48 50"


def test_cmd_err_exit1_stderr(stack: Stack) -> None:
    r = run_mcu(stack, "cmd", "gpio get nope")
    assert r.returncode == 1
    assert "badarg" in r.stderr


def test_cmd_timeout_exit2(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--drop-response", "1"])
    r = run_mcu(stack, "cmd", "ping", "--timeout", "500")
    assert r.returncode == 2
    assert "timeout" in r.stderr


def test_unreachable_exit3() -> None:
    r = run_mcu(None, "status", url="http://127.0.0.1:1")
    assert r.returncode == 3
    assert r.stderr.strip()  # one-line message on stderr


def test_bad_usage_exit1() -> None:
    r = run_mcu(None, "no-such-command", url="http://127.0.0.1:1")
    assert r.returncode == 1


def test_a_url_httpx_cannot_parse_is_exit_3_on_every_client_policy(tmp_path) -> None:
    """A url no daemon can be reached at is exit 3 wherever it is noticed (SPEC 4).

    `httpx.InvalidURL` is not an HTTPError, so it escaped every handler catching one and
    reached the user as a traceback. One mapping now serves all three call policies, which
    makes it a single point of failure for all three: request (status), download (session
    export) and stream_text (plot export) are each driven through it here. The two
    existing bad-url tests reach neither - `daemon status` is answered by Client.probe and
    `daemon stop` fails earlier in the pid-file host/port split.
    """
    bad = "http://[::1"
    for args in (
        ("status",),
        ("session", "export", "run", "-o", str(tmp_path / "out.db")),
        ("plot", "export", "--names", "sine"),
    ):
        r = run_mcu(None, *args, url=bad)
        assert r.returncode == 3, (args, r.stdout, r.stderr)
        assert "bad daemon url" in r.stderr, args
        assert "Traceback" not in r.stderr, args


def test_a_pipe_closed_inside_a_command_is_success(stack: Stack) -> None:
    """`mcu tail -f | head -1`: the reader is done, which is its exit and not our failure.

    EPIPE raised inside a command never reaches this CLI's own handler - typer catches it,
    swaps stdout for a PacifyFlushWrapper and exits 1 - so the dispatcher translates that
    1 back to 0. `tail -f` flushes every row, so the write that fails is inside the
    command; a hang here (the follow ignoring the closed pipe) fails on the wait timeout.
    """
    rc, errout = run_mcu_closed_pipe(stack, "tail", "-n", "5", "-f")
    assert rc == 0, errout
    assert "Traceback" not in errout


def test_a_pipe_closed_before_the_final_flush_is_success() -> None:
    """`mcu ai-guide | head -1`: the other half, where nothing failed inside the command.

    Output this small sits in the buffer until main() flushes it. Without that flush being
    handled where it can be, the interpreter's shutdown flush prints "Exception ignored
    ... BrokenPipeError" and exits 120 over the top of the real exit code.
    """
    rc, errout = run_mcu_closed_pipe(None, "ai-guide", url="http://127.0.0.1:1")
    assert rc == 0, errout
    assert "Exception ignored" not in errout and "Traceback" not in errout


def test_a_pipe_closed_during_help_is_success() -> None:
    """`mcu --help | head -1`: rich renders help and every usage error, and answers a
    broken pipe by devnulling stdout and raising SystemExit(1) itself - a third way for a
    closed pipe to arrive, and not a failure either."""
    rc, errout = run_mcu_closed_pipe(None, "--help", url="http://127.0.0.1:1")
    assert rc == 0, errout
    assert "Traceback" not in errout


# -- the same closed pipe, spelled the way Windows spells it ---------------------------
#
# Windows reports a write or flush to a pipe whose reader has closed as a plain
# OSError(EINVAL), not BrokenPipeError, so every handler above matched nothing there: the
# three tests before this one failed on all three Windows CI jobs with exit 1, a crash log
# and "Exception ignored on flushing sys.stdout". The platform gate is one module constant,
# so a Linux run can be put on Windows semantics rather than owing the answer to CI, and
# each test asserts both states of it: with the gate off, EINVAL stays a real error.


def test_the_harness_survives_child_output_that_is_not_clean_text() -> None:
    """The harness reads every child as UTF-8 with replacement, never as platform text.

    `mcu --help | head -1` failed a Windows job in this file's own reader: the crash
    message came back through cp1252 and byte 0x90 raised UnicodeDecodeError before a
    single assertion ran. A closed pipe also cuts a UTF-8 sequence mid-character, which
    strict decoding rejects on every platform - so this asserts the tolerance, and the
    "no bare text=True" sweep asserts the codec at the other 20-odd call sites.
    """
    r = subprocess.run(
        [sys.executable, "-c", r"import sys; sys.stdout.buffer.write(b'ok\x90\xff\n')"],
        capture_output=True, timeout=20, **CHILD_TEXT,
    )
    assert r.stdout.startswith("ok")
    assert "�" in r.stdout, "an undecodable byte must be replaced, not raised"


def _einval() -> OSError:
    """The error Windows raises from a write or flush to a pipe with no reader."""
    return OSError(errno.EINVAL, "Invalid argument")


class _DeadPipe(io.StringIO):
    """A redirected stdout whose reader has gone, spelled the way Windows spells it."""

    def write(self, text: str) -> int:
        raise _einval()

    def flush(self) -> None:
        raise _einval()

    def fileno(self) -> int:
        return 1        # rich probes it while sizing the terminal; StringIO has none


class _FlushFailsStdout(io.StringIO):
    """A stdout whose buffered content cannot be flushed, the way a dead pipe's cannot."""

    def flush(self) -> None:
        raise _einval()


def _on_windows(monkeypatch, windows: bool) -> None:
    """Put this run on the other platform's closed-pipe spelling."""
    from mcuscope import _stdio

    monkeypatch.setattr(_stdio, "PIPE_CLOSE_IS_EINVAL", windows)


def test_the_stream_wrapper_translates_a_redirected_einval_and_nothing_else(monkeypatch) -> None:
    """The one place that knows an EINVAL is a closed pipe: the stream it happened on.

    Classifying it at the handlers instead was wrong in both directions - a whole-program
    handler cannot tell a dead pipe from a bad path, and rich and click (which render
    --help and every usage error) catch BrokenPipeError and nothing else, so on Windows
    their own handling never fired and `mcu --help | head -1` wrote a crash log.
    """
    from mcuscope import _stdio

    _on_windows(monkeypatch, True)
    monkeypatch.setattr(sys, "stdout", _DeadPipe())
    _stdio.translate_closed_pipe_errors()
    assert isinstance(sys.stdout, _stdio._PipeErrorStream)
    with pytest.raises(BrokenPipeError):
        sys.stdout.write("x")
    with pytest.raises(BrokenPipeError):
        sys.stdout.flush()
    _stdio.translate_closed_pipe_errors()                 # idempotent: no double wrap
    assert not isinstance(sys.stdout._stream, _stdio._PipeErrorStream)

    # Any other OSError keeps its own meaning, on the same stream.
    class _Denied(io.StringIO):
        def write(self, text: str) -> int:
            raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(sys, "stdout", _Denied())
    _stdio.translate_closed_pipe_errors()
    with pytest.raises(OSError, match="Permission denied") as ei:
        sys.stdout.write("x")
    assert not isinstance(ei.value, BrokenPipeError)

    # A console is not a pipe: EINVAL from one is a real error and must not be rewritten.
    class _Console(_DeadPipe):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdout", _Console())
    _stdio.translate_closed_pipe_errors()
    assert not isinstance(sys.stdout, _stdio._PipeErrorStream)

    # And POSIX, where EINVAL never meant this: no wrapper at all.
    _on_windows(monkeypatch, False)
    monkeypatch.setattr(sys, "stdout", _DeadPipe())
    _stdio.translate_closed_pipe_errors()
    assert not isinstance(sys.stdout, _stdio._PipeErrorStream)


def test_the_stream_wrapper_keeps_the_stream_it_wraps_recognisable() -> None:
    """rich and click choose colour, width and encoding by probing the stream, so a proxy
    that answers for itself instead of the real stream changes how output is rendered."""
    from mcuscope import _stdio

    real = open(os.devnull, "w", encoding="utf-8")
    try:
        wrapped = _stdio._PipeErrorStream(real)
        for attr in ("encoding", "errors", "name", "mode", "newlines", "buffer"):
            assert getattr(wrapped, attr, None) == getattr(real, attr, None), attr
        assert wrapped.isatty() == real.isatty()
        assert wrapped.fileno() == real.fileno()
        assert wrapped.writable() == real.writable()
        assert wrapped.write("ok") == 2
    finally:
        real.close()


@pytest.mark.parametrize("windows,expected", [(True, 0), (False, 1)])
def test_windows_einval_from_the_final_flush_is_success(monkeypatch, windows, expected) -> None:
    """`mcu ai-guide | head -1` on Windows: output small enough to still be in the buffer.

    main() flushes where the failure can still be handled. It read the closed pipe as an
    unclassified OSError and returned 1 over a command that had done its job.
    """
    from mcuscope import cli

    _on_windows(monkeypatch, windows)
    monkeypatch.setattr(cli, "_silence_stdout", lambda: None)   # no fileno to dup2 onto
    monkeypatch.setattr(sys, "stdout", _FlushFailsStdout())
    assert cli.main(["ai-guide"]) == expected


@pytest.mark.parametrize("windows", [True, False])
def test_an_einval_escaping_a_command_is_a_real_failure(monkeypatch, windows) -> None:
    """The whole-program handlers stay on BrokenPipeError.

    _dispatch's OSError arm sees an error from anywhere in the command - a bad path, a
    socket operation, a serial URL - so reading any EINVAL there as "the reader left"
    reported exit 0 for genuine failures on Windows and left no crash log. Nothing but a
    stream that failed can produce a BrokenPipeError now, so that one is still success.
    """
    from mcuscope import cli

    _on_windows(monkeypatch, windows)
    monkeypatch.setattr(cli, "_silence_stdout", lambda: None)

    def explode(*a, **kw):
        raise _einval()

    monkeypatch.setattr(cli, "app", explode)
    with pytest.raises(OSError, match="Invalid argument"):
        cli._dispatch(["status"])

    def broken_pipe(*a, **kw):
        raise BrokenPipeError()

    monkeypatch.setattr(cli, "app", broken_pipe)
    assert cli._dispatch(["status"]) == 0


@pytest.mark.parametrize("windows,expected", [(True, 0), (False, None)])
def test_windows_einval_from_a_follow_write_is_success(monkeypatch, windows, expected) -> None:
    """`mcu tail -f | head -1` on Windows: the write that fails is a stdio site, and it
    reaches the follow already spelled as a closed pipe."""
    from mcuscope import _stdio, cli, cli_output

    _on_windows(monkeypatch, windows)
    monkeypatch.setattr(cli_output, "_silence_stdout", lambda: None)
    monkeypatch.setattr(sys, "stdout", _DeadPipe())
    _stdio.translate_closed_pipe_errors()
    if expected is None:
        with pytest.raises(OSError, match="Invalid argument"):
            cli.emit_stream("a row")
    else:
        with pytest.raises(typer.Exit) as ei:
            cli.emit_stream("a row")
        assert ei.value.exit_code == expected


@pytest.mark.parametrize("windows,expected", [(True, 0), (False, 3)])
def test_windows_einval_inside_a_follow_ends_it_as_a_closed_pipe(
    monkeypatch, windows, expected
) -> None:
    """`mcu tail -f | head -1` on Windows: the write that fails is inside the command.

    The follow's OSError arm reads any OSError as "daemon unreachable" and exits 3, which
    is what the Windows job reported for a pipe the reader had simply closed.
    """
    import websockets

    from mcuscope import _stdio, cli, cli_output

    _on_windows(monkeypatch, windows)
    monkeypatch.setattr(cli_output, "_silence_stdout", lambda: None)
    row = json.dumps([{"ts": 1.0, "chan": "log", "raw": "row", "port": "p", "id": 1}])
    monkeypatch.setattr(
        websockets, "connect", lambda url, **kw: _ScriptedWS([row]), raising=False
    )
    monkeypatch.setattr(sys, "stdout", _DeadPipe())
    _stdio.translate_closed_pipe_errors()
    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)
    with pytest.raises(typer.Exit) as ei:
        cli._follow_ws(s, "log", None)
    # 3 is the unclassified reading: an EINVAL no stream translated looks like a daemon
    # that went away, which is exactly what the Windows job reported for a closed pipe.
    assert ei.value.exit_code == expected


@pytest.mark.parametrize("windows,expected", [(True, 0), (False, 1)])
def test_windows_einval_while_rich_renders_help_is_success(monkeypatch, windows, expected) -> None:
    """`mcu --help | head -1` on Windows, in process: the CI test this file already runs
    on POSIX. rich renders help and answers a broken pipe itself, but it catches only
    BrokenPipeError, so an untranslated EINVAL walked out of the command and crash-logged.
    """
    from mcuscope import cli

    _on_windows(monkeypatch, windows)
    monkeypatch.setattr(cli, "_silence_stdout", lambda: None)
    monkeypatch.setattr(sys, "stdout", _DeadPipe())
    monkeypatch.setattr(sys, "stderr", _DeadPipe())
    try:
        rc = cli.main(["--help"])
    except OSError as exc:               # untranslated: the crash-log path
        assert not windows, exc
        rc = expected
    assert rc == expected


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf"])
def test_a_non_finite_start_timeout_from_the_environment_is_ignored(raw, monkeypatch) -> None:
    """MCUSCOPE_START_TIMEOUT=nan made `daemon start` kill the daemon it had just spawned.

    `max(nan, 0.0)` is nan, so `while time.monotonic() < deadline` was false on its first
    evaluation: the readiness loop never ran once, the spawned daemon was abandoned, and
    the advice it printed ("raise --timeout if it just needs longer") could never work.
    """
    from mcuscope import cli

    monkeypatch.setenv("MCUSCOPE_START_TIMEOUT", raw)
    assert cli._start_timeout_default() == 20.0
    monkeypatch.setenv("MCUSCOPE_START_TIMEOUT", "7.5")   # a real value still gets through
    assert cli._start_timeout_default() == 7.5


def test_a_non_finite_number_on_the_command_line_is_bad_usage(stack: Stack) -> None:
    """click's FLOAT accepts "nan" and "inf" as readily as float() does, so every float
    option taking one from argv needs the same grammar as the environment variable."""
    r = run_mcu(None, "daemon", "start", "--timeout", "nan", url="http://127.0.0.1:1")
    assert r.returncode == 1
    assert "--timeout" in r.stderr and "finite" in r.stderr

    r = run_mcu(stack, "purge", "--before-days", "inf", "--dry-run")
    assert r.returncode == 1
    assert "--before-days" in r.stderr and "finite" in r.stderr


# -- --json output shapes -------------------------------------------------------------


def test_cmd_json_shape(stack: Stack) -> None:
    r = run_mcu(stack, "--json", "cmd", "i2c scan")
    assert r.returncode == 0
    obj = json.loads(r.stdout)
    assert obj["status"] == "ok" and obj["data"] == "48 50"
    assert isinstance(obj["line_id"], int)


def test_status_json_shape(stack: Stack) -> None:
    obj = json.loads(run_mcu(stack, "--json", "status").stdout)
    assert obj["ports"][0]["connected"] is True
    assert "db_size_bytes" in obj


def test_lines_json_shape(stack: Stack) -> None:
    r = run_mcu(stack, "--json", "lines", "--last-ms", "100000", "--limit", "5")
    obj = json.loads(r.stdout)
    assert "lines" in obj and "truncated" in obj


def test_lines_reports_truncation_on_stderr(stack: Stack) -> None:
    # A capped query used to look like a complete one in human output: only --json ever
    # showed "truncated", so "the error never happened" could be read off a short window.
    for i in range(3):
        run_mcu(stack, "mark", f"trunc-{i}")
    r = run_mcu(stack, "lines", "--chan", "marker", "--limit", "1")
    assert r.returncode == 0
    assert len(r.stdout.strip().splitlines()) == 1     # stdout stays pure rows
    assert "truncated" in r.stderr

    # ... and --json still prints exactly one object, with the flag inside it.
    j = run_mcu(stack, "--json", "lines", "--chan", "marker", "--limit", "1")
    assert json.loads(j.stdout)["truncated"] is True


def test_die_emits_a_json_error_object(stack: Stack) -> None:
    # With --json, a failure used to print nothing on stdout at all.
    r = run_mcu(None, "--json", "status", url="http://127.0.0.1:1")
    assert r.returncode == 3
    obj = json.loads(r.stdout)
    assert obj["exit_code"] == 3 and "unreachable" in obj["error"]
    assert r.stderr.strip()          # the human message still goes to stderr


def test_wait_json_match(stack: Stack) -> None:
    r = run_mcu(stack, "--json", "wait", "--match", "^!can", "--timeout", "2000")
    assert r.returncode == 0
    assert json.loads(r.stdout)["status"] == "match"


def test_wait_timeout_exit2(stack: Stack) -> None:
    r = run_mcu(stack, "wait", "--match", "ZZZ_NEVER", "--timeout", "300")
    assert r.returncode == 2


def test_can_dump_json(stack: Stack) -> None:
    # Backfill (non-follow) --json must emit one JSON object per frame (JSONL), matching
    # the follow-mode wire format instead of a single aggregate body.
    frames: list[dict] = []
    for _ in range(30):
        r = run_mcu(stack, "--json", "can", "dump", "--id", "100", "-n", "5")
        frames = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
        if frames:
            break
        time.sleep(0.1)
    assert frames
    assert frames[0]["can_id"] == 0x100


def test_can_dump_follow_json(stack: Stack) -> None:
    # `can dump -f --json` must be consistent JSONL end to end: no human-format lines
    # mixed into the backfill portion before the live follow portion kicks in.
    env = os.environ.copy()
    env["MCUSCOPE_URL"] = stack.base_url
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [*MCU, "--json", "can", "dump", "--id", "100", "-n", "0", "-f"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **CHILD_TEXT,
        env=env,
    )
    out_lines: list[str] = []
    got_frame = threading.Event()

    def drain() -> None:
        for line in proc.stdout:           # type: ignore[union-attr]
            out_lines.append(line)
            got_frame.set()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        # Wait for the first heartbeat frame (the sim emits at 10 Hz) rather than sleeping a
        # fixed 1.5s. That sleep had to cover python startup plus the subscription, not just
        # the 100ms frame interval, and a loaded runner can spend longer than it on startup
        # alone and see nothing at all.
        assert got_frame.wait(20.0), "no CAN frame arrived from the 10 Hz heartbeat"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        reader.join(timeout=2)
    lines = [json.loads(line) for line in out_lines if line.strip()]
    assert lines
    assert all(fr["can_id"] == 0x100 for fr in lines)


# -- i2c sugar: --reg maps to wrrd ----------------------------------------------------


def test_i2c_rd_trailing_json(stack: Stack) -> None:
    # The SPEC acceptance form puts --json last: `mcu i2c rd 48 2 --json`.
    r = run_mcu(stack, "i2c", "rd", "48", "2", "--json")
    assert r.returncode == 0
    obj = json.loads(r.stdout)
    assert obj["status"] == "ok" and len(obj["data"]) == 4  # two temp bytes


def test_i2c_reg_maps_to_wrrd(stack: Stack) -> None:
    # Write CAFE at EEPROM offset 0x10, then read it back via a register read (--reg).
    assert run_mcu(stack, "i2c", "wr", "50", "10CAFE").returncode == 0
    obj = json.loads(run_mcu(stack, "--json", "i2c", "rd", "50", "2", "--reg", "10").stdout)
    assert obj["status"] == "ok" and obj["data"] == "CAFE"


# -- ai-guide (no daemon needed) ------------------------------------------------------


def test_ai_guide() -> None:
    r = subprocess.run([*MCU, "ai-guide"], capture_output=True, **CHILD_TEXT, timeout=20)
    assert r.returncode == 0
    assert "EXIT CODES" in r.stdout
    assert "--json" in r.stdout


# -- plot channels / export (SPEC 9.2) ------------------------------------------------


def _wait_plot_names(stack: Stack, need: set[str], tries: int = 60) -> dict[str, dict]:
    by_name: dict[str, dict] = {}
    for _ in range(tries):
        obj = json.loads(run_mcu(stack, "--json", "plot", "channels").stdout)
        by_name = {ch["name"]: ch for ch in obj["channels"]}
        if need <= set(by_name):
            break
        time.sleep(0.1)
    return by_name


def test_plot_channels_json(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--plot"])
    by_name = _wait_plot_names(stack, {"tri", "sine"})
    assert by_name["tri"]["sid"] == "0" and by_name["tri"]["unit"] == "V"
    assert by_name["sine"]["sid"] is None


def test_plot_export_wide_csv(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--plot"])
    for _ in range(60):
        by_name = {ch["name"]: ch for ch in
                   json.loads(run_mcu(stack, "--json", "plot", "channels").stdout)["channels"]}
        if by_name.get("tri", {}).get("count", 0) >= 10:
            break
        time.sleep(0.1)
    r = run_mcu(stack, "plot", "export", "--names", "tri,ramp,ftest", "--wide")
    assert r.returncode == 0
    lines = r.stdout.strip().splitlines()
    assert lines[0] == "ts,tick_ms,tri,ramp,ftest"
    assert len(lines) >= 2


def test_plot_export_json_wraps_the_csv(make_stack: Callable[..., Stack]) -> None:
    # SPEC 4: with --json a command prints exactly one JSON object and no prose. This one
    # used to print raw CSV to stdout, which no --json consumer can parse.
    stack = make_stack(["--plot"])
    _wait_plot_names(stack, {"tri"})
    r = run_mcu(stack, "plot", "export", "--names", "tri", "--json")
    assert r.returncode == 0, r.stderr
    obj = json.loads(r.stdout)             # exactly one object, or this raises
    assert obj["names"] == "tri" and obj["format"] == "long"
    assert obj["csv"].startswith("ts,")
    assert obj["rows"] == max(obj["csv"].count("\n") - 1, 0)


def test_plot_export_json_to_file_reports_the_file(make_stack: Callable[..., Stack], tmp_path):
    # With -o the CSV goes to the file, but --json used to print nothing at all.
    stack = make_stack(["--plot"])
    _wait_plot_names(stack, {"tri"})
    dest = tmp_path / "plot.csv"
    r = run_mcu(stack, "plot", "export", "--names", "tri", "--json", "-o", str(dest))
    assert r.returncode == 0, r.stderr
    obj = json.loads(r.stdout)
    assert obj["file"] == str(dest)
    assert obj["bytes"] == dest.stat().st_size > 0
    assert obj["rows"] == len(dest.read_text(encoding="utf-8").splitlines()) - 1


def test_plot_export_unwritable_path_exit1(make_stack: Callable[..., Stack], tmp_path) -> None:
    stack = make_stack(["--plot"])
    _wait_plot_names(stack, {"tri"})
    bad = tmp_path / "no-such-dir" / "out.csv"
    r = run_mcu(stack, "plot", "export", "--names", "tri", "-o", str(bad))
    assert r.returncode == 1
    assert "cannot write" in r.stderr
    assert "Traceback" not in r.stderr


def test_plot_export_streams_the_response(monkeypatch, capsys) -> None:
    """The CSV body is consumed chunk by chunk, never materialised whole.

    `/plot/export` is the one endpoint that can answer with a very large body; it used to
    be read via `resp.text`. Both unstreamed doors are nailed shut here: `httpx.request`
    and `Response.text`.
    """
    from mcuscope import cli

    chunks = ["ts,tick_ms,sid,name,value\n", "1.0,10,0,tri,1.5\n", "1.1,20,0,tri,2.5\n"]

    class _Resp:
        status_code = 200

        def iter_text(self):
            yield from chunks

        @property
        def text(self):
            raise AssertionError("body was read whole instead of streamed")

    class _Stream:
        def __enter__(self):
            return _Resp()

        def __exit__(self, *exc):
            return False

    class _FakeHttp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def stream(self, *a, **kw):
            return _Stream()

        def request(self, *a, **kw):
            raise AssertionError("plot export used an unstreamed request")

    # Client.open() is the seam; patching it beats reaching into httpx's module globals.
    monkeypatch.setattr(cli.Client, "open", lambda self: _FakeHttp())
    rc = cli.main(["plot", "export", "--names", "tri", "--url", "http://127.0.0.1:1"])
    assert rc == 0
    assert capsys.readouterr().out == "".join(chunks)


def test_plot_export_wide_mixed_streams_exit1(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--plot"])
    _wait_plot_names(stack, {"tri", "sine"})
    r = run_mcu(stack, "plot", "export", "--names", "sine,tri", "--wide")
    assert r.returncode == 1
    assert "error" in r.stderr


# -- devices ---------------------------------------------------------------------------


def test_devices_json_passthrough(stack: Stack) -> None:
    # The sim is a socket:// URL so it never shows up in the host's real serial device
    # list; the list may legitimately be empty. Just check the shape passes through raw.
    r = run_mcu(stack, "--json", "devices")
    assert r.returncode == 0
    body = json.loads(r.stdout)
    assert "devices" in body
    for d in body["devices"]:
        assert set(d) == {"device", "by_id", "description", "vid_pid", "serial_number"}


def test_devices_human(stack: Stack) -> None:
    r = run_mcu(stack, "devices")
    assert r.returncode == 0
    body = json.loads(run_mcu(stack, "--json", "devices").stdout)
    if not body["devices"]:
        assert r.stdout.strip() == "no serial devices found"
    else:
        assert r.stdout.strip()
        first = body["devices"][0]
        assert first["device"] in r.stdout


# -- _hoist_global_opts honors `--` -----------------------------------------------------


def test_hoist_respects_end_of_options(stack: Stack) -> None:
    # Before the fix, a literal "--json" after "--" would be hoisted as the global flag,
    # leaving `send`'s required TEXT argument unfilled (usage error, exit 1).
    r = run_mcu(stack, "send", "--", "--json")
    assert r.returncode == 0
    assert r.stdout.strip() == "ok"


# -- Client.request timeout classification (exit 2, not 3) -----------------------------


def test_read_timeout_exit2_not_unreachable() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def _accept_and_stall() -> None:
        conn, _ = srv.accept()
        stop.wait(5)
        conn.close()

    t = threading.Thread(target=_accept_and_stall, daemon=True)
    t.start()
    try:
        s = Settings(url=f"http://127.0.0.1:{port}", json_out=False, port=None)
        with pytest.raises(typer.Exit) as ei:
            Client(s).request("GET", "/status", timeout=0.2)
        assert ei.value.exit_code == 2
    finally:
        stop.set()
        srv.close()
        t.join(timeout=2)


# -- daemon start ------------------------------------------------------------------------


def test_daemon_start_already_running_exit1(stack: Stack) -> None:
    # Also exercises the "looks like a real /status body" verification: the live test
    # stack's daemon genuinely returns version/uptime_s/ports, so this must short-circuit
    # before ever trying to spawn a second mcuscoped process.
    r = run_mcu(stack, "daemon", "start")
    assert r.returncode == 1
    assert "already running" in r.stderr


_PIDDIR_ENV_SKIP = pytest.mark.skipif(
    os.name == "nt",
    reason="platformdirs resolves the Windows data dir via the shell API, not env vars",
)


def _spawn_env(data_home: str, url: str) -> dict[str, str]:
    env = os.environ.copy()
    env["XDG_DATA_HOME"] = data_home
    env["MCUSCOPE_URL"] = url
    return env


def _daemon_config(tmp_path, name: str) -> str:
    cfg = tmp_path / f"{name}.toml"
    cfg.write_text(
        f'[storage]\ndb_path = "{(tmp_path / (name + ".db")).as_posix()}"\n',
        encoding="utf-8", newline="\n")
    return str(cfg)


def _answers(url: str) -> bool:
    try:
        return httpx.get(url + "/status", timeout=1.0).status_code == 200
    except httpx.HTTPError:
        return False


@_PIDDIR_ENV_SKIP
def test_daemon_start_timeout_does_not_orphan_the_child(tmp_path) -> None:
    """A readiness timeout must not leave a daemon running that nothing can stop.

    The old code deleted the pid file and returned, so a merely slow daemon came up a few
    seconds later with `daemon status` reporting it running and `daemon stop` answering
    "no pid file". --timeout 0.05 makes the race certain to be lost: nothing starts a
    uvicorn app in 50ms, and daemon_start honours the value exactly. It did not always
    lose while a 0.5s floor sat in daemon_start, which an idle machine could beat.
    """
    from tests.support import free_port

    data_home = str(tmp_path / "data")
    url = f"http://127.0.0.1:{free_port()}"
    env = _spawn_env(data_home, url)
    r = subprocess.run(
        [*MCU, "daemon", "start", "-c", _daemon_config(tmp_path, "orphan"), "--timeout", "0.05"],
        capture_output=True, **CHILD_TEXT, timeout=60, env=env,
    )
    try:
        assert r.returncode == 1
        assert "did not come up" in r.stderr
        assert "Traceback" not in r.stderr
        # Whatever was spawned is gone, and stays gone: nothing answers at that URL.
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            assert not _answers(url), f"orphaned daemon still running at {url}: {r.stderr}"
            time.sleep(0.25)
        pid_dir = os.path.join(data_home, "mcuscope")
        names = os.listdir(pid_dir) if os.path.isdir(pid_dir) else []
        left = [f for f in names if f.endswith(".pid")]
        assert left == []   # the child was stopped, so its pid record is gone with it
    finally:
        # If this test ever fails because the daemon DID come up, that daemon is live and
        # nothing else will ever stop it: the assertion above aborts before any cleanup and
        # the CLI already handed off. Earlier flaky runs leaked one process each, which
        # outlived the whole pytest session. Failing must not also litter.
        if _answers(url):
            subprocess.run(
                [*MCU, "daemon", "stop"], capture_output=True, **CHILD_TEXT, timeout=30, env=env
            )


@_PIDDIR_ENV_SKIP
def test_daemon_start_pid_file_is_keyed_by_host_port(tmp_path) -> None:
    """One shared pid file meant a second daemon clobbered the first one's record.

    Spawns a real daemon, then checks that `daemon stop` aimed at a different URL does not
    claim it, and that the correctly aimed one does.
    """
    from tests.support import free_port

    data_home = str(tmp_path / "data")
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    other = f"http://127.0.0.1:{free_port()}"
    started = subprocess.run(
        [*MCU, "daemon", "start", "-c", _daemon_config(tmp_path, "keyed")],
        capture_output=True, **CHILD_TEXT, timeout=90, env=_spawn_env(data_home, url),
    )
    try:
        assert started.returncode == 0, started.stderr
        pid_files = sorted(os.listdir(os.path.join(data_home, "mcuscope")))
        assert f"mcuscoped-127.0.0.1-{port}.pid" in pid_files

        # A stop aimed elsewhere must not correlate this daemon's pid with that URL.
        miss = subprocess.run(
            [*MCU, "daemon", "stop"], capture_output=True, **CHILD_TEXT, timeout=30,
            env=_spawn_env(data_home, other),
        )
        assert miss.returncode == 1
        assert "no pid file" in miss.stderr
        assert _answers(url), "a stop aimed at another URL killed this daemon"
    finally:
        stopped = subprocess.run(
            [*MCU, "daemon", "stop"], capture_output=True, **CHILD_TEXT, timeout=30,
            env=_spawn_env(data_home, url),
        )
    assert stopped.returncode == 0, stopped.stderr
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and _answers(url):
        time.sleep(0.2)
    assert not _answers(url)


# -- daemon status / stop ---------------------------------------------------------------


def run_mcu_canned(monkeypatch, capsys, handler, *args: str):
    """Run `mcu <args>` in this process against a canned transport.

    For the cases that only need a particular response body: the transport seam answers
    them in-process, where a threaded HTTP server per body used to. No subprocess either,
    so a traceback surfaces as a failing test rather than as text in a captured stderr.
    """
    from mcuscope import cli

    # main() builds its own Client, so there is no argument to reach; patch the one seam
    # that already exists rather than keeping a second, test-only one in the package.
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(cli.Client, "open", lambda self: httpx.Client(transport=transport))
    rc = cli.main([*args, "--url", "http://127.0.0.1:1"])
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _json_body(body):
    """A transport answering every request with `body` as JSON."""
    return lambda request: httpx.Response(200, json=body)


def _serve_http(handler) -> tuple[HTTPServer, threading.Thread, str]:
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t, f"http://127.0.0.1:{httpd.server_address[1]}"


def test_daemon_status_running_exit0(stack: Stack) -> None:
    r = run_mcu(stack, "daemon", "status")
    assert r.returncode == 0
    assert r.stdout.startswith("running:")


def test_daemon_status_unreachable_exit3() -> None:
    r = run_mcu(None, "daemon", "status", url="http://127.0.0.1:1")
    assert r.returncode == 3
    assert "not running" in r.stdout


def test_daemon_status_non_mcuscoped_json_exit3(monkeypatch, capsys) -> None:
    # A reachable server returning JSON that is not a /status body: exit 3, no traceback.
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, _json_body({"hello": 1}),
                                "daemon", "status")
    assert rc == 3
    assert "not running" in out


def test_daemon_status_non_json_body_exit3(monkeypatch, capsys) -> None:
    # A reachable server that answers HTML, the way a proxy or a stray service would.
    rc, _, _ = run_mcu_canned(
        monkeypatch, capsys,
        lambda request: httpx.Response(501, text="<html>Unsupported method</html>"),
        "daemon", "status",
    )
    assert rc == 3


def _run_mcu_data_home(data_home: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["XDG_DATA_HOME"] = data_home
    env["MCUSCOPE_URL"] = "http://127.0.0.1:1"
    return subprocess.run(
        [*MCU, *args], capture_output=True, **CHILD_TEXT, env=env, timeout=20
    )


@_PIDDIR_ENV_SKIP
def test_daemon_stop_no_pidfile_exit1(tmp_path) -> None:
    r = _run_mcu_data_home(str(tmp_path), "daemon", "stop")
    assert r.returncode == 1
    assert "no pid file" in r.stderr


def _child_data_dir(data_home: str) -> str:
    """Where the `mcu` child resolves its data dir, which is not where this process does.

    conftest's autouse platformdirs isolation patches this process only, so a path read
    in-process names a directory no subprocess ever looks in: the corrupt pid record below
    was written where the CLI could not see it, and the test then passed its returncode
    assertion and matched "no pid file" instead of the message it exists to pin.
    """
    env = os.environ.copy()
    env["XDG_DATA_HOME"] = data_home
    probe = "import platformdirs; print(platformdirs.user_data_dir('mcuscope'))"
    r = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, env=env, timeout=20, check=True, **CHILD_TEXT,
    )
    return r.stdout.strip()


@_PIDDIR_ENV_SKIP
def test_daemon_stop_corrupt_pidfile_exit1(tmp_path) -> None:
    data_dir = _child_data_dir(str(tmp_path))
    os.makedirs(data_dir, exist_ok=True)
    pid_path = os.path.join(data_dir, "mcuscoped-127.0.0.1-1.pid")
    with open(pid_path, "w", encoding="utf-8") as fh:
        fh.write("not-a-pid")
    r = _run_mcu_data_home(str(tmp_path), "daemon", "stop")
    assert r.returncode == 1
    assert "corrupt" in r.stderr
    # Kept, not deleted: nothing here proves the daemon is dead (review class 7), and
    # with no daemon answering at the URL there is nothing to correlate it against.
    assert os.path.exists(pid_path)
    assert "left it in place" in r.stderr


# -- sessions -------------------------------------------------------------------------


def test_session_start_stop_list_and_scoping(stack: Stack) -> None:
    # The whole agent-facing workflow: name a run, do something in it, close it, and get
    # back only that run's lines.
    started = run_mcu(stack, "session", "start", "cli-run", "--note", "from the CLI", "--json")
    assert started.returncode == 0
    session = json.loads(started.stdout)["session"]
    assert session["name"] == "cli-run" and session["ended_ts"] is None

    assert run_mcu(stack, "mark", "inside cli-run").returncode == 0
    assert run_mcu(stack, "session", "stop").returncode == 0
    assert run_mcu(stack, "mark", "after cli-run").returncode == 0

    scoped = run_mcu(stack, "lines", "--session", "cli-run", "--limit", "200", "--json")
    assert scoped.returncode == 0
    raws = [r["raw"] for r in json.loads(scoped.stdout)["lines"]]
    assert "inside cli-run" in raws
    assert "after cli-run" not in raws

    listing = run_mcu(stack, "session", "list", "--json")
    assert listing.returncode == 0
    names = [s["name"] for s in json.loads(listing.stdout)["sessions"]]
    assert "cli-run" in names

    # Human output stays parseable at a glance.
    human = run_mcu(stack, "session", "list")
    assert human.returncode == 0
    assert "cli-run" in human.stdout


def test_session_list_shows_the_date(stack: Stack) -> None:
    # Time of day alone made yesterday's run and today's indistinguishable in a listing.
    run_mcu(stack, "session", "start", "dated-run")
    r = run_mcu(stack, "session", "list")
    assert r.returncode == 0
    row = next(line for line in r.stdout.splitlines() if "dated-run" in line)
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", row), row
    run_mcu(stack, "session", "stop")


def test_session_stop_without_one_exits_1(stack: Stack) -> None:
    run_mcu(stack, "session", "stop")          # ensure nothing is running
    r = run_mcu(stack, "session", "stop")
    assert r.returncode == 1
    assert "no session" in r.stderr


def test_session_export_writes_a_capture_file(stack: Stack, tmp_path) -> None:
    run_mcu(stack, "session", "start", "archive-me")
    run_mcu(stack, "mark", "inside the archived run")
    run_mcu(stack, "session", "stop")

    out = tmp_path / "run.db"
    r = run_mcu(stack, "session", "export", "archive-me", "-o", str(out))
    assert r.returncode == 0, r.stderr
    assert out.stat().st_size > 0

    conn = sqlite3.connect(str(out))
    raws = [row[0] for row in conn.execute("SELECT raw FROM lines")]
    conn.close()
    assert "inside the archived run" in raws


def test_session_delete_with_data(stack: Stack) -> None:
    run_mcu(stack, "session", "start", "junk-run")
    run_mcu(stack, "mark", "junk payload")
    run_mcu(stack, "session", "stop")

    r = run_mcu(stack, "session", "delete", "junk-run", "--data", "--yes")
    assert r.returncode == 0, r.stderr
    left = run_mcu(stack, "lines", "--limit", "500", "--json")
    assert "junk payload" not in [x["raw"] for x in json.loads(left.stdout)["lines"]]


# -- assert ---------------------------------------------------------------------------


def test_assert_retrospective_pass_and_fail(stack: Stack) -> None:
    run_mcu(stack, "session", "start", "verdict-run")
    run_mcu(stack, "mark", "BOOT OK")
    run_mcu(stack, "mark", "CALIB DONE")
    run_mcu(stack, "session", "stop")

    ok = run_mcu(stack, "assert", "--session", "verdict-run",
                 "--expect", "BOOT OK", "--forbid", "ERR")
    assert ok.returncode == 0, ok.stderr
    assert "PASS" in ok.stdout

    bad = run_mcu(stack, "assert", "--session", "verdict-run", "--expect", "NEVER PRINTED")
    assert bad.returncode == 1
    assert "FAILED" in bad.stderr


def test_assert_json_verdict(stack: Stack) -> None:
    run_mcu(stack, "mark", "READY 1")
    r = run_mcu(stack, "assert", "--expect", "READY 1", "--last-ms", "60000", "--json")
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout)
    assert body["status"] == "pass"
    assert body["expect"][0]["line"]["raw"] == "READY 1"


def test_assert_live_window_with_send(stack: Stack) -> None:
    # The live form: send something, then judge what comes back within the window.
    r = run_mcu(stack, "assert", "--send", "ping", "--expect", "monitor", "--timeout", "3000")
    assert r.returncode == 0, r.stderr + r.stdout

    miss = run_mcu(stack, "assert", "--expect", "NOTHING EMITS THIS", "--timeout", "600")
    assert miss.returncode == 1


def test_assert_needs_a_pattern(stack: Stack) -> None:
    r = run_mcu(stack, "assert")
    assert r.returncode == 1
    assert "expect" in r.stderr


# -- purge ----------------------------------------------------------------------------


def test_purge_dry_run_then_delete(stack: Stack) -> None:
    run_mcu(stack, "session", "start", "purge-me")
    run_mcu(stack, "mark", "delete this line")
    run_mcu(stack, "session", "stop")

    dry = run_mcu(stack, "purge", "--session", "purge-me", "--dry-run")
    assert dry.returncode == 0
    assert "would delete" in dry.stdout
    still = run_mcu(stack, "lines", "--limit", "500", "--json")
    assert "delete this line" in [x["raw"] for x in json.loads(still.stdout)["lines"]]

    done = run_mcu(stack, "purge", "--session", "purge-me", "--yes")
    assert done.returncode == 0, done.stderr
    gone = run_mcu(stack, "lines", "--limit", "500", "--json")
    assert "delete this line" not in [x["raw"] for x in json.loads(gone.stdout)["lines"]]


def test_purge_requires_one_selector(stack: Stack) -> None:
    r = run_mcu(stack, "purge", "--yes")
    assert r.returncode == 1
    assert "exactly one" in r.stderr


@pytest.mark.parametrize("answer", ["n\n", ""])   # declined, and stdin closed
def test_purge_without_yes_asks_and_deletes_nothing_when_refused(
    stack: Stack, answer: str
) -> None:
    # Declining a destructive prompt is a normal outcome: it must print a plain message
    # (typer's own abort path renders a traceback at whoever answered "n") and leave the
    # capture alone, with a non-zero exit so a script never reads "cancelled" as "done".
    run_mcu(stack, "mark", "must survive a refused purge")
    r = run_mcu(stack, "purge", "--all", stdin=answer)
    assert r.returncode == 1
    assert "cancelled" in r.stderr
    assert "Traceback" not in r.stderr and "Abort" not in r.stderr
    left = run_mcu(stack, "lines", "--limit", "500", "--json")
    assert "must survive a refused purge" in [x["raw"] for x in json.loads(left.stdout)["lines"]]


def test_purge_prompt_never_lands_on_stdout(stack: Stack) -> None:
    # The confirmation is a message to a human, so it goes to stderr: on stdout it is a
    # prose fragment in the middle of a parse.
    run_mcu(stack, "mark", "prompt-routing")
    r = run_mcu(stack, "purge", "--all", stdin="n\n")
    assert r.returncode == 1
    assert "delete" in r.stderr and "[y/N]" in r.stderr
    assert r.stdout.strip() == ""
    # Under --json there is no prompt at all unless stdin is a terminal (a consumer that
    # never answers would hang), and stdout carries the one object SPEC 4 promises.
    j = run_mcu(stack, "--json", "purge", "--all", stdin="n\n")
    assert j.returncode == 1
    obj = json.loads(j.stdout)
    assert obj["exit_code"] == 1 and "-y" in obj["error"]


def test_session_delete_prompt_never_lands_on_stdout(stack: Stack) -> None:
    run_mcu(stack, "session", "start", "prompt-run")
    run_mcu(stack, "mark", "prompt payload")
    run_mcu(stack, "session", "stop")
    r = run_mcu(stack, "session", "delete", "prompt-run", "--data", stdin="n\n")
    assert r.returncode == 1
    assert "[y/N]" in r.stderr
    assert r.stdout.strip() == ""
    j = run_mcu(stack, "--json", "session", "delete", "prompt-run", "--data", stdin="n\n")
    assert j.returncode == 1
    assert json.loads(j.stdout)["exit_code"] == 1   # refused, not prompted (see purge)
    # Declining left the session alone.
    names = [s["name"] for s in json.loads(run_mcu(stack, "--json", "session", "list").stdout)[
        "sessions"
    ]]
    assert "prompt-run" in names


# -- ports / attach / detach ----------------------------------------------------------


def test_ports_lists_the_attached_port(stack: Stack) -> None:
    r = run_mcu(stack, "ports")
    assert r.returncode == 0
    assert stack.alias in r.stdout and "connected" in r.stdout

    obj = json.loads(run_mcu(stack, "--json", "ports").stdout)
    assert [pt["alias"] for pt in obj["ports"]] == [stack.alias]


def test_attach_then_detach_round_trip(stack: Stack) -> None:
    # A socket:// URL with nothing listening: attach records the port and hands it to the
    # reconnect loop rather than failing, so the round trip needs no second simulator.
    from tests.support import free_port

    device = f"socket://127.0.0.1:{free_port()}"
    att = run_mcu(stack, "attach", device, "--alias", "spare")
    assert att.returncode == 0
    assert "attached spare" in att.stdout
    assert "spare" in run_mcu(stack, "ports").stdout

    det = run_mcu(stack, "detach", "spare")
    assert det.returncode == 0
    assert "detached spare" in det.stdout
    assert "spare" not in run_mcu(stack, "ports").stdout


def test_attach_says_so_when_the_port_is_not_connected(stack: Stack) -> None:
    """Attaching a device that is not there is a supported flow (presence-gated reconnect)
    and stays exit 0, but the line used to read exactly like a live connection, so a typo
    in a device path looked like success."""
    from tests.support import free_port

    device = f"socket://127.0.0.1:{free_port()}"     # nothing is listening
    att = run_mcu(stack, "attach", device, "--alias", "ghost")
    assert att.returncode == 0
    assert "connecting" in att.stdout, att.stdout
    run_mcu(stack, "detach", "ghost")


def test_attach_derives_an_alias_and_detach_of_nothing_exits_1(stack: Stack) -> None:
    from tests.support import free_port

    obj = json.loads(
        run_mcu(stack, "--json", "attach", f"socket://127.0.0.1:{free_port()}").stdout
    )
    assert obj["port"]["alias"] == "board"   # _derive_alias default for a URL device

    r = run_mcu(stack, "detach", "never-attached")
    assert r.returncode == 1


# -- send / mark ----------------------------------------------------------------------


def test_send_writes_a_raw_line_into_the_capture(stack: Stack) -> None:
    r = run_mcu(stack, "send", ">9001 ping")
    assert r.returncode == 0
    assert r.stdout.strip() == "ok"

    rows = json.loads(run_mcu(stack, "lines", "--limit", "500", "--json").stdout)["lines"]
    sent = [x for x in rows if x["raw"] == ">9001 ping"]
    assert sent and sent[0]["dir"] == "tx"


def test_mark_reports_its_line_id(stack: Stack) -> None:
    r = run_mcu(stack, "mark", "phase two begins")
    assert r.returncode == 0
    assert r.stdout.startswith("marker ")

    obj = json.loads(run_mcu(stack, "--json", "mark", "and again").stdout)
    rows = json.loads(run_mcu(stack, "lines", "--limit", "500", "--json").stdout)["lines"]
    hit = [x for x in rows if x["id"] == obj["line_id"]]
    assert hit and hit[0]["chan"] == "marker" and hit[0]["raw"] == "and again"


# -- tail (including -f) --------------------------------------------------------------


def follow_mcu(
    stack: Stack, *args: str, expect: str, poke: Callable[[], None] | None = None,
    timeout: float = CLI_TIMEOUT_S,
) -> list[str]:
    """Run a never-terminating `mcu` follow command until `expect` appears, then stop it.

    A reader thread drains stdout so the child cannot block on a full pipe, and `poke` runs
    once the stream is open, which is what makes the expected line arrive after (not
    before) the follow started.
    """
    env = os.environ.copy()
    env["MCUSCOPE_URL"] = stack.base_url
    proc = subprocess.Popen(
        [*MCU, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, **CHILD_TEXT, env=env
    )
    seen: list[str] = []
    found = threading.Event()

    def drain() -> None:
        for line in proc.stdout:           # type: ignore[union-attr]
            seen.append(line.rstrip("\n"))
            if expect in line:
                found.set()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        if poke is not None:
            # Poke until the follower reports it, rather than once after a fixed 0.5s guess
            # at how long `mcu ... -f` needs to start python and subscribe. That guess holds
            # on an idle machine and silently loses the line on a loaded CI runner, where the
            # single poke lands before the subscription exists. Every poke is a `mcu mark`,
            # so repeating it only adds another marker line.
            deadline = time.monotonic() + timeout
            while not found.is_set() and time.monotonic() < deadline:
                poke()
                found.wait(0.5)
            assert found.is_set(), f"{expect!r} never appeared; saw {seen}"
        else:
            assert found.wait(timeout), f"{expect!r} never appeared; saw {seen}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        reader.join(timeout=2)
    return seen


def test_tail_prints_recent_lines_oldest_first(stack: Stack) -> None:
    run_mcu(stack, "mark", "tail-marker-one")
    run_mcu(stack, "mark", "tail-marker-two")
    r = run_mcu(stack, "tail", "-n", "200", "--chan", "marker")
    assert r.returncode == 0
    assert r.stdout.index("tail-marker-one") < r.stdout.index("tail-marker-two")


def test_tail_json_emits_one_object_per_line(stack: Stack) -> None:
    run_mcu(stack, "mark", "tail-json-marker")
    r = run_mcu(stack, "--json", "tail", "-n", "5", "--chan", "marker")
    rows = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
    assert rows and all(row["chan"] == "marker" for row in rows)


def test_new_rows_drops_only_ids_the_snapshot_printed() -> None:
    """The staging watermark: dedupe by row id, never by position.

    Control objects and rows that lost their id carry no id to compare and must survive,
    or a {"gap"} notice staged during the snapshot would be swallowed - the one frame
    whose whole job is to say data was lost.
    """
    from mcuscope import cli

    rows = [
        {"id": 5, "raw": "old"},
        {"gap": 3},
        {"capture": "abc"},
        {"raw": "no id at all"},
        {"id": 6, "raw": "boundary"},
        {"id": 7, "raw": "new"},
        "not even an object",
    ]
    kept = cli._new_rows(rows, 6)
    assert kept == [
        {"gap": 3}, {"capture": "abc"}, {"raw": "no id at all"},
        {"id": 7, "raw": "new"}, "not even an object",
    ]
    # An empty snapshot has no watermark, so nothing staged may be dropped.
    assert cli._new_rows(rows, 0) == rows


def test_new_rows_keeps_arrival_order_and_duplicate_free_ids() -> None:
    from mcuscope import cli

    rows = [{"id": 9}, {"id": 4}, {"id": 9}, {"id": 10}]
    assert cli._new_rows(rows, 4) == [{"id": 9}, {"id": 9}, {"id": 10}]


class _FakeWs:
    """A /ws stand-in whose frames a canned REST handler can push mid-snapshot."""

    def __init__(self) -> None:
        self.payloads: list[str] = []
        self.closed = False

    async def recv(self) -> str:
        import asyncio

        import websockets

        while True:
            if self.payloads:
                return self.payloads.pop(0)
            if self.closed:
                raise websockets.exceptions.ConnectionClosedOK(None, None)
            await asyncio.sleep(0.005)

    async def __aenter__(self) -> _FakeWs:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


def test_tail_follow_subscribes_before_its_snapshot(monkeypatch, capsys) -> None:
    """`mcu tail -f` opens /ws first, then fetches /lines, then replays what arrived.

    The other order dropped every line captured between the REST answer and the
    subscription, with nothing on any surface to say so. The fake socket here delivers a
    frame *during* the snapshot fetch: it must reach stdout after the snapshot rows and
    exactly once, with the row the snapshot already printed deduped by id.
    """
    import websockets

    ws = _FakeWs()
    order: list[str] = []

    def row(rid: int, raw: str) -> dict:
        return {"id": rid, "ts": 1700000000.0 + rid, "port": "board",
                "dir": "rx", "chan": "debug", "seq": None, "raw": raw}

    def handler(request: httpx.Request) -> httpx.Response:
        order.append("snapshot")
        # Lands while the snapshot is in flight: row 2 overlaps it, row 3 is new.
        ws.payloads.append(json.dumps([row(2, "second"), row(3, "third")]))
        ws.closed = True    # ends the follow once the staged frame has been drained
        time.sleep(0.05)
        return httpx.Response(200, json={"lines": [row(2, "second"), row(1, "first")]})

    def fake_connect(*a: object, **kw: object) -> _FakeWs:
        order.append("connect")
        return ws

    monkeypatch.setattr(websockets, "connect", fake_connect)
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, handler, "tail", "-n", "2", "-f")

    assert order == ["connect", "snapshot"], "the socket must be open before the fetch"
    assert rc == 3          # the fake socket closing is an ordinary end of stream
    printed = [line for line in out.splitlines() if line.strip()]
    assert [p.split()[-1] for p in printed] == ["first", "second", "third"]


def test_tail_without_follow_makes_one_rest_fetch(monkeypatch, capsys) -> None:
    """Plain `mcu tail` stays a single GET; the staging path is follow-only."""
    import websockets

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"lines": [
            {"id": 1, "ts": 1.0, "port": "b", "dir": "rx", "chan": "debug",
             "seq": None, "raw": "only"},
        ]})

    def boom(*a: object, **kw: object) -> None:
        raise AssertionError("plain tail must not open a websocket")

    monkeypatch.setattr(websockets, "connect", boom)
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, handler, "tail", "-n", "5")
    assert rc == 0 and len(calls) == 1 and out.strip().endswith("only")


def test_stage_backfill_consumes_its_recv_when_the_snapshot_raises() -> None:
    """A snapshot that raises must not orphan the in-flight recv task.

    `mcu tail -f | head` ends with the snapshot print raising BrokenPipeError while a
    recv is still in flight. Left unawaited, that task resolves with the socket
    teardown's ConnectionClosed and asyncio prints a "Task exception was never
    retrieved" traceback to stderr at loop shutdown - which is GC-timing dependent, so
    it surfaced only on the CI runners (windows py3.11/py3.13) and never locally. Both
    exits are forced here: the recv still pending, and the recv already failed.
    """
    import asyncio
    import gc

    import websockets

    from mcuscope.cli import _stage_backfill

    reports: list[str] = []

    class _ClosableWs:
        """recv blocks until the socket "closes", then fails the way a real one does.

        The shape that matters: at the moment the snapshot raises, recv is still
        pending, and it is the socket teardown afterwards that resolves it with
        ConnectionClosedOK. A recv cancelled at loop shutdown files no report, so a
        fake that merely hangs cannot reproduce the orphan.
        """

        def __init__(self) -> None:
            self._closed = asyncio.Event()

        async def recv(self) -> str:
            await self._closed.wait()
            raise websockets.exceptions.ConnectionClosedOK(None, None)

        def close(self) -> None:
            self._closed.set()

    def broken_snapshot() -> int:
        raise BrokenPipeError

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda loop, ctx: reports.append(ctx["message"]))
        ws = _ClosableWs()
        with pytest.raises(BrokenPipeError):
            await _stage_backfill(ws, broken_snapshot)
        # The teardown a real follow does next: the connection closes, which resolves
        # any recv left in flight with the close exception.
        ws.close()
        await asyncio.sleep(0.05)
        # Task.__del__ is what files the report, so the orphan (if any) has to be
        # collected while this loop and its handler are still alive.
        gc.collect()
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert [m for m in reports if "never retrieved" in m] == []


def test_tail_follow_streams_new_lines(stack: Stack) -> None:
    seen = follow_mcu(
        stack, "tail", "-n", "1", "-f", "--chan", "marker",
        expect="follow-me-now",
        poke=lambda: run_mcu(stack, "mark", "follow-me-now"),
    )
    assert any("follow-me-now" in line for line in seen)


def test_tail_follow_json_streams_objects(stack: Stack) -> None:
    seen = follow_mcu(
        stack, "--json", "tail", "-n", "1", "-f", "--match", "follow-json-marker",
        expect="follow-json-marker",
        poke=lambda: run_mcu(stack, "mark", "follow-json-marker"),
    )
    rows = [json.loads(line) for line in seen if line.startswith("{")]
    assert any(row["raw"] == "follow-json-marker" for row in rows)


# -- log export -----------------------------------------------------------------------


def test_log_export_to_stdout_and_file(stack: Stack, tmp_path) -> None:
    run_mcu(stack, "mark", "export-me-please")

    to_stdout = run_mcu(stack, "log", "export", "--chan", "marker", "--limit", "500")
    assert to_stdout.returncode == 0
    assert "export-me-please" in to_stdout.stdout

    dest = tmp_path / "capture.log"
    to_file = run_mcu(
        stack, "log", "export", "--chan", "marker", "--limit", "500", "-o", str(dest)
    )
    assert to_file.returncode == 0
    assert "wrote" in to_file.stdout
    assert "export-me-please" in dest.read_text(encoding="utf-8")


def test_log_export_json_is_jsonl(stack: Stack, tmp_path) -> None:
    run_mcu(stack, "mark", "jsonl-export-marker")
    dest = tmp_path / "capture.jsonl"
    run_mcu(
        stack, "--json", "log", "export", "--chan", "marker", "--limit", "500", "-o", str(dest)
    )
    rows = [json.loads(line) for line in dest.read_text(encoding="utf-8").splitlines() if line]
    assert any(row["raw"] == "jsonl-export-marker" for row in rows)


def test_log_export_json_to_file_emits_one_object(stack: Stack, tmp_path) -> None:
    # -o with --json used to print 0 bytes, so a consumer parsing stdout got nothing.
    run_mcu(stack, "mark", "json-result-object")
    dest = tmp_path / "capture.jsonl"
    r = run_mcu(
        stack, "--json", "log", "export", "--chan", "marker", "--limit", "500", "-o", str(dest)
    )
    assert r.returncode == 0, r.stderr
    obj = json.loads(r.stdout)
    assert obj["file"] == str(dest)
    assert obj["lines"] == len(dest.read_text(encoding="utf-8").splitlines())
    assert obj["bytes"] == dest.stat().st_size


def test_log_export_unwritable_path_exit1(stack: Stack, tmp_path) -> None:
    bad = tmp_path / "no-such-dir" / "capture.log"
    r = run_mcu(stack, "log", "export", "--limit", "5", "-o", str(bad))
    assert r.returncode == 1
    assert "cannot write" in r.stderr
    assert "Traceback" not in r.stderr


def test_log_export_match_narrows_the_dump(stack: Stack) -> None:
    run_mcu(stack, "mark", "keep-this-one")
    run_mcu(stack, "mark", "drop-that-one")
    r = run_mcu(stack, "log", "export", "--match", "keep-this-one", "--limit", "500")
    assert "keep-this-one" in r.stdout and "drop-that-one" not in r.stdout


# -- bus sugar: can / spi / gpio / adc ------------------------------------------------


def test_can_tx_and_echo(stack: Stack) -> None:
    # The simulator echoes a transmitted frame back with id+1 after 20 ms (SPEC 7), so a
    # successful tx is observable in the decoded CAN view rather than only in the ok.
    tx = run_mcu(stack, "can", "tx", "123", "DEADBEEF")
    assert tx.returncode == 0

    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        dump = run_mcu(stack, "--json", "can", "dump", "--id", "124", "-n", "5")
        frames = [json.loads(line) for line in dump.stdout.splitlines() if line.strip()]
        if any(fr["data_hex"].upper() == "DEADBEEF" for fr in frames):
            return
        time.sleep(0.2)
    raise AssertionError("echoed frame 0x124 never arrived")


def test_can_tx_rtr_and_ext_flags(stack: Stack) -> None:
    assert run_mcu(stack, "can", "tx", "1ABCDEF", "--ext", "11").returncode == 0
    assert run_mcu(stack, "can", "tx", "200", "--rtr", "4").returncode == 0


def _dump_frames(stack: Stack, *args: str) -> list[dict]:
    dump = run_mcu(stack, "--json", "can", "dump", *args)
    return [json.loads(line) for line in dump.stdout.splitlines() if line.strip()]


def test_can_tx_on_bus_2_echoes_on_bus_2(stack: Stack) -> None:
    # `--bus 2` sends `can2 tx`; the sim echoes on the same bus (SPEC 7), so the frame shows
    # under `--bus 2` with bus=2 in the row, and never under `--bus 1`.
    assert run_mcu(stack, "can", "tx", "--bus", "2", "610", "BEEF").returncode == 0
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        hits = [fr for fr in _dump_frames(stack, "--bus", "2", "--id", "611", "-n", "5")
                if fr["data_hex"] == "BEEF"]
        if hits:
            assert hits[0]["bus"] == 2
            break
        time.sleep(0.2)
    else:
        raise AssertionError("echoed frame 0x611 never arrived on bus 2")
    assert not [fr for fr in _dump_frames(stack, "--bus", "1", "--id", "611", "-n", "50")
                if fr["data_hex"] == "BEEF"], "a bus 2 echo leaked into the bus 1 view"
    # The human format tags bus 2 rows and leaves bus 1 rows as they were (SPEC 4).
    human = run_mcu(stack, "can", "dump", "--bus", "2", "--id", "611", "-n", "5").stdout
    assert "bus=2 id=611" in human
    human1 = run_mcu(stack, "can", "dump", "--bus", "1", "--id", "100", "-n", "1").stdout
    assert "id=100" in human1 and "bus=" not in human1


def test_can_stat_and_filter_take_the_bus_option(stack: Stack) -> None:
    # The family token is what carries the bus on the wire, so the sent command line, which
    # the capture stores as a `cmd` row, is the evidence.
    assert run_mcu(stack, "can", "stat", "--bus", "2").returncode == 0
    assert run_mcu(stack, "can", "filter", "--bus", "2", "all").returncode == 0
    assert run_mcu(stack, "can", "filter", "--bus", "2", "600", "700").returncode == 0
    assert run_mcu(stack, "can", "filter", "--bus", "2", "all").returncode == 0
    r = run_mcu(stack, "--json", "lines", "--chan", "cmd", "--match", "can2 ", "--limit", "10")
    sent = [row["raw"] for row in json.loads(r.stdout)["lines"]]
    assert any(raw.endswith(" can2 stat") for raw in sent), sent
    assert any(raw.endswith(" can2 filter 600 700") for raw in sent), sent
    # The default stays the unmarked form, so an older target keeps answering.
    assert run_mcu(stack, "can", "stat").returncode == 0
    r = run_mcu(stack, "--json", "lines", "--chan", "cmd", "--match", "can stat", "--limit", "5")
    assert json.loads(r.stdout)["lines"], "the bare `can stat` was not sent"


@pytest.mark.parametrize("bus", ["0", "10", "x"])
def test_a_bus_outside_1_to_9_is_refused_before_anything_is_sent(stack: Stack, bus: str) -> None:
    for args in (("tx", "--bus", bus, "100", "-"), ("stat", "--bus", bus),
                 ("filter", "--bus", bus, "all"), ("dump", "--bus", bus)):
        r = run_mcu(stack, "can", *args)
        assert r.returncode == 1, (args, r.stdout, r.stderr)   # usage error
        assert "'--bus'" in r.stderr, r.stderr


def test_can_stat_counts_the_transmit(stack: Stack) -> None:
    before = run_mcu(stack, "can", "stat").stdout
    assert "tx=" in before and "state=" in before
    run_mcu(stack, "can", "tx", "321", "01")
    after = run_mcu(stack, "can", "stat").stdout

    def tx_of(text: str) -> int:
        return int(dict(tok.split("=", 1) for tok in text.split() if "=" in tok)["tx"])

    assert tx_of(after) == tx_of(before) + 1


def test_can_filter_variants(stack: Stack) -> None:
    for args in (["all"], ["none"], ["100", "700"]):
        r = run_mcu(stack, "can", "filter", *args)
        assert r.returncode == 0, r.stderr
    run_mcu(stack, "can", "filter", "all")     # leave the sim receiving again

    bad = run_mcu(stack, "can", "filter")
    assert bad.returncode == 1
    assert "badarg" in bad.stderr


def test_spi_xfer_echoes_inverted(stack: Stack) -> None:
    r = run_mcu(stack, "spi", "xfer", "imu", "AABB")
    assert r.returncode == 0
    assert r.stdout.strip().upper() == "5544"     # the sim inverts each byte (SPEC 7)

    bad = run_mcu(stack, "spi", "xfer", "nosuchcs", "AA")
    assert bad.returncode == 1
    assert "badarg" in bad.stderr


def test_gpio_set_then_get(stack: Stack) -> None:
    assert run_mcu(stack, "gpio", "set", "led", "1").returncode == 0
    assert run_mcu(stack, "gpio", "get", "led").stdout.strip() == "1"
    assert run_mcu(stack, "gpio", "set", "led", "0").returncode == 0
    assert run_mcu(stack, "gpio", "get", "led").stdout.strip() == "0"

    bad = run_mcu(stack, "gpio", "set", "led", "2")
    assert bad.returncode == 1


def test_adc_read(stack: Stack) -> None:
    r = run_mcu(stack, "adc", "read", "vbat")
    assert r.returncode == 0
    fields = dict(tok.split("=", 1) for tok in r.stdout.split() if "=" in tok)
    assert int(fields["raw"]) > 0 and 3000 < int(fields["mv"]) < 3600

    obj = json.loads(run_mcu(stack, "--json", "adc", "read", "vbat").stdout)
    assert obj["status"] == "ok" and obj["data"].startswith("raw=")

    bad = run_mcu(stack, "adc", "read", "nosuchchannel")
    assert bad.returncode == 1
    assert "badarg" in bad.stderr


# -- stub daemons: bodies a real stack cannot easily produce ---------------------------


_STATUS_STUB_BODY = {"version": "9.9-stub", "uptime_s": 1.0, "db_path": "/tmp/x.db", "ports": []}


class _StoppableDaemon(BaseHTTPRequestHandler):
    """Answers /status like mcuscoped and really stops on POST /shutdown.

    Reports no "pid", which is also the pre-0.1.2 shape: `daemon stop` then has nothing
    to signal and must judge the stop by /status going quiet. Deliberately not the live
    test stack - that one runs inside pytest and reports pytest's own pid, so a fallback
    kill would terminate the test session.
    """

    def _reply(self, obj: dict) -> None:
        payload = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        self._reply({"version": "9.9-stub", "uptime_s": 1.0, "ports": []})

    def do_POST(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        self._reply({"ok": True})
        # From another thread: shutdown() waits for the serving loop this handler runs in.
        threading.Thread(
            target=lambda: (self.server.shutdown(), self.server.server_close()), daemon=True
        ).start()

    def log_message(self, *args):
        pass


# -- daemon stop / start: a live daemon must never become unstoppable ------------------


def _write_pid_record(data_home: str, host: str, port: int, pid: int) -> str:
    """Fabricate the pid record `mcu daemon stop` reads for host:port."""
    data_dir = os.path.join(data_home, "mcuscope")
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, f"mcuscoped-{host}-{port}.pid")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(str(pid))
    return path


@_PIDDIR_ENV_SKIP
def test_daemon_stop_keeps_the_record_of_a_pid_that_is_still_running(tmp_path) -> None:
    """A daemon that is up but not answering /status must keep its pid record.

    /status not answering with a full envelope is not proof of death: a slow start and a
    401 from a token-guarded daemon both look like it. Removing the record there left a
    live daemon that `daemon stop` could never find again.
    """
    path = _write_pid_record(str(tmp_path), "127.0.0.1", 1, os.getpid())
    r = _run_mcu_data_home(str(tmp_path), "daemon", "stop")   # url is 127.0.0.1:1, dead
    assert r.returncode == 1
    assert str(os.getpid()) in r.stderr and "http://127.0.0.1:1" in r.stderr
    assert os.path.exists(path), "the record of a running pid was removed"


def test_daemon_start_leaves_a_pid_record_that_names_another_daemon(tmp_path) -> None:
    """Giving up on a spawned daemon must not delete a record another one now owns.

    The claim-to-bind window of a big capture is seconds long, so B can be spawned while
    A holds the record, die on the capture lock, and take A's record with it - leaving A
    live and unstoppable.
    """
    from mcuscope import cli

    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)

    class _Died:
        pid = 424242

        def poll(self):
            return 3

    class _Unresponsive:
        pid = 424243

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    path = tmp_path / "mcuscoped-127.0.0.1-1.pid"
    for proc in (_Died(), _Unresponsive()):
        # another daemon claimed it meanwhile
        path.write_text("999999", encoding="utf-8", newline="\n")
        with pytest.raises(typer.Exit) as ei:
            cli._abandon_daemon(proc, str(path), s, 0.05)
        assert ei.value.exit_code == 1
        assert path.read_text(encoding="utf-8") == "999999"
    # The record it does own is still cleaned up.
    path.write_text("424243", encoding="utf-8", newline="\n")
    with pytest.raises(typer.Exit):
        cli._abandon_daemon(_Unresponsive(), str(path), s, 0.05)
    assert not path.exists()


@_PIDDIR_ENV_SKIP
def test_daemon_stop_falls_back_to_the_api_when_no_record_exists(tmp_path) -> None:
    """An unwritable data dir left no pid record, and `daemon stop` refused to try.

    POST /shutdown stops the daemon perfectly well without one, so a missing record is a
    reason to ask /status who is there, not to declare the daemon unstoppable.
    """
    httpd, t, url = _serve_http(_StoppableDaemon)
    env = os.environ.copy()
    env["XDG_DATA_HOME"] = str(tmp_path)     # empty: no pid record anywhere
    env["MCUSCOPE_URL"] = url
    try:
        r = subprocess.run(
            [*MCU, "daemon", "stop"], capture_output=True, **CHILD_TEXT, env=env, timeout=60
        )
        assert r.returncode == 0, r.stderr
        assert "stopped mcuscoped" in r.stdout
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=2)


@_PIDDIR_ENV_SKIP
@pytest.mark.parametrize("record", ["", "not-a-pid", "٣"])
def test_daemon_stop_asks_status_before_giving_up_on_a_corrupt_record(tmp_path, record):
    """An unreadable pid record must not destroy a live daemon's record (class 7).

    This branch removed the record and exited 1 without ever asking /status - while the
    no-record branch, with strictly less information, stopped the daemon correctly. Driven
    against a live daemon, `daemon stop` reported "unreadable or corrupt", exit 1, the
    record was gone and the daemon was still answering. The empty record is not a
    hypothetical: pidfile.claim() creates the file before writing the pid into it.
    """
    httpd, t, url = _serve_http(_StoppableDaemon)
    port = int(url.rsplit(":", 1)[1])
    path = os.path.join(str(tmp_path), "mcuscope", f"mcuscoped-127.0.0.1-{port}.pid")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(record)
    env = os.environ.copy()
    env["XDG_DATA_HOME"] = str(tmp_path)
    env["MCUSCOPE_URL"] = url
    try:
        r = subprocess.run(
            [*MCU, "daemon", "stop"], capture_output=True, **CHILD_TEXT, env=env, timeout=60
        )
        assert r.returncode == 0, f"{r.stdout}{r.stderr}"
        assert "stopped mcuscoped" in r.stdout
        assert "Traceback" not in r.stderr
    finally:
        httpd.shutdown()
        with contextlib.suppress(Exception):
            httpd.server_close()
        t.join(timeout=2)


# -- review regressions: patterns, output shapes, bad input ----------------------------


def test_tail_follow_match_compiles_with_the_regex_module(stack: Stack) -> None:
    """--match is a `regex` pattern in the daemon, so it must be one in the follow too.

    `\\p{L}` matched the first batch through GET /lines and then killed the follow with a
    re.error traceback the moment the WebSocket loop compiled the same pattern.
    """
    seen = follow_mcu(
        stack, "tail", "-n", "1", "-f", "--chan", "marker",
        "--match", r"follow-\p{L}+-unicode",
        expect="follow-regex-unicode",
        poke=lambda: run_mcu(stack, "mark", "follow-regex-unicode"),
    )
    assert any("follow-regex-unicode" in line for line in seen)


def test_follow_bad_pattern_exits_1_in_process() -> None:
    """The follow's own compile must die on the contract, not raise regex.error.

    Driven in-process: end to end the daemon rejects an unparseable pattern first (400
    from GET /lines), so nothing reaches this compile through the CLI.
    """
    from mcuscope import cli

    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)
    with pytest.raises(typer.Exit) as ei:
        cli._follow_ws(s, None, "(unclosed")
    assert ei.value.exit_code == 1


def test_follow_match_is_time_bounded() -> None:
    """The daemon runs every user pattern with `regex` *and* a timeout; only the engine
    came across to the follow. Without the timeout `(a|a)+$` hangs the CLI with no error,
    no exit code, and no working Ctrl-C, since the match holds the interpreter in C code.

    Run in a thread so a regression fails the test rather than hanging the suite.
    """
    import regex

    from mcuscope import cli

    pat = regex.compile(r"(a|a)+$")
    result: list[object] = []

    def match_it() -> None:
        try:
            result.append(cli._follow_match(pat, "a" * 30 + "!"))
        except BaseException as exc:      # typer.Exit is not an Exception
            result.append(exc)

    thread = threading.Thread(target=match_it, daemon=True)
    thread.start()
    thread.join(timeout=10.0)
    assert not thread.is_alive(), "--match ran unbounded on a catastrophic pattern"
    assert isinstance(result[0], typer.Exit) and result[0].exit_code == 1


class _ScriptedWS:
    """A WebSocket replaying text frames, then closing like the daemon.

    A frame may be an exception instead of text, for the failures that arrive through
    recv() rather than in a payload (a Ctrl-C landing in the follow loop).
    """

    def __init__(self, frames: list[str | BaseException]) -> None:
        self._frames = list(frames)

    async def __aenter__(self) -> _ScriptedWS:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def recv(self) -> str:
        from websockets.exceptions import ConnectionClosedOK

        if not self._frames:
            raise ConnectionClosedOK(None, None)
        frame = self._frames.pop(0)
        if isinstance(frame, BaseException):
            raise frame
        return frame


def test_follow_skips_a_bad_frame_instead_of_ending_the_follow(monkeypatch, capsys) -> None:
    """One malformed frame or row must cost that item, not the whole follow (class 16).

    `mcu tail -f` used to die() on the first unparseable frame or missing key, so a
    single bad item ended a follow that is meant to run until Ctrl-C. The failure is
    charged to the item, counted, and reported once per episode on stderr, so stdout
    carries rows and nothing else while the follow runs.

    Run with the output mode `mcu --json tail -f` really has: die() reads the global mode,
    so with it left False this asserted against a state production never reaches. The one
    thing die() does add to stdout is its closing envelope, which SPEC 4 owes a --json
    consumer as the reason the stream ended.
    """
    import websockets

    from mcuscope import cli, cli_output

    monkeypatch.setattr(cli_output, "_JSON_MODE", True)

    good = json.dumps([{"ts": 1.0, "chan": "log", "raw": "kept-one", "port": "p", "id": 1}])
    later = json.dumps([{"ts": 2.0, "chan": "log", "raw": "kept-two", "port": "p", "id": 2}])
    frames = [
        "{not json",                                  # unparseable frame
        json.dumps([{"chan": "log"}]),                # a row missing "raw"
        json.dumps(["a string, not a row"]),          # not indexable at all
        good,
        json.dumps({"ts": 3.0, "raw": "no-chan"}),    # missing "chan"
        later,
    ]
    monkeypatch.setattr(
        websockets, "connect", lambda url, **kw: _ScriptedWS(frames), raising=False
    )
    s = Settings(url="http://127.0.0.1:1", json_out=True, port=None)
    with pytest.raises(typer.Exit) as ei:
        cli._follow_ws(s, "log", ".")   # the filters read row["chan"] and row["raw"]

    assert ei.value.exit_code == 3            # closed by the daemon: not a bad frame
    out, errout = capsys.readouterr()
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert [r["raw"] for r in rows[:-1]] == ["kept-one", "kept-two"]
    assert rows[-1] == {"error": "stream closed by daemon", "exit_code": 3}
    assert "warning: skipping bad frame" in errout
    assert "skipped 3 frames" in errout       # once per episode, not once per item


def test_follow_reads_control_objects_as_control(monkeypatch, capsys) -> None:
    """A frame carries control objects beside its rows; neither is a bad frame (class 16).

    They are told apart by having no "id" (SPEC 3.4). Without that test the follow ran
    `row["chan"]` on them and charged the KeyError to _DropCounter, so the shed-rows notice
    - the one thing on the wire that says data was lost - printed as "skipping bad frame:
    'chan'" and hid itself. The capture identity is for a stateful client and is simply
    not a line, so it must be silent on both streams.
    """
    import websockets

    from mcuscope import cli, cli_output

    monkeypatch.setattr(cli_output, "_JSON_MODE", True)
    frames = [
        json.dumps([{"capture": "abc"},
                    {"ts": 1.0, "chan": "log", "raw": "kept", "port": "p", "id": 1}]),
        json.dumps([{"gap": 12},
                    {"ts": 2.0, "chan": "log", "raw": "after", "port": "p", "id": 2}]),
    ]
    monkeypatch.setattr(
        websockets, "connect", lambda url, **kw: _ScriptedWS(frames), raising=False
    )
    s = Settings(url="http://127.0.0.1:1", json_out=True, port=None)
    with pytest.raises(typer.Exit):
        cli._follow_ws(s, "log", ".")

    out, errout = capsys.readouterr()
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert [r["raw"] for r in rows[:-1]] == ["kept", "after"], \
        "a control object cost the rows that shared its frame"
    assert "skipping bad frame" not in errout, "a control object was charged as a bad frame"
    assert "shed 12 line(s)" in errout, "the shed-rows notice never reached the operator"
    assert "abc" not in out and "abc" not in errout, "the capture identity was printed"


def test_can_dump_follow_survives_a_failed_poll(monkeypatch, capsys) -> None:
    """A transient httpx failure must cost that poll, not the follow (classes 16 and 9).

    The poll loop had no handling at all, so one dropped connection ended `mcu can dump
    -f` with a traceback. Errors no retry can fix (a url httpx cannot parse, a 4xx) still
    end it, with an exit code.
    """
    from mcuscope import cli

    frame = {"line_id": 7, "ts": 1.0, "can_id": 0x100, "dlc": 1, "data": "00",
             "rtr": False, "ext": False, "port": "p"}
    polls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            # The capture-token check, not a frame poll: it must not count here.
            return httpx.Response(200, json={"capture": "A"})
        polls.append(1)
        if len(polls) in (1, 2):
            raise httpx.ConnectError("connection reset")
        if len(polls) == 3:
            return httpx.Response(200, json={"frames": [frame]})
        raise KeyboardInterrupt        # Ctrl-C ends the follow

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    s = Settings(url="http://127.0.0.1:1", json_out=True, port=None)
    client = Client(s, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(client, "get", lambda path, **kw: {"frames": []})   # the priming call

    with pytest.raises(typer.Exit) as ei:
        cli._dump_follow(client, s, None)

    assert ei.value.exit_code == 0
    out, errout = capsys.readouterr()
    assert [json.loads(line)["line_id"] for line in out.splitlines() if line.strip()] == [7]
    assert "warning: skipping bad update" in errout
    assert "skipped 2 updates" in errout


def test_can_dump_follow_gives_up_on_a_daemon_that_never_comes_back(monkeypatch, capsys):
    """Retrying must not become polling a dead URL for ever, silently (class 16 mirror)."""
    from mcuscope import cli


    # The give-up bound is wall clock, so the test owns the clock: a skipped sleep advances
    # it, and so does a failing poll, which is the whole point. A dead peer that drops the
    # SYN costs the full 10 s connect timeout per attempt, so counting iterations at
    # FOLLOW_POLL_S called 30 s after about 25 real minutes. Left on the real clock this
    # loop would never reach the deadline and the test would hang rather than fail.
    clock = [0.0]

    def always_down_slowly(request: httpx.Request) -> httpx.Response:
        clock[0] += 10.0        # the connect timeout in _poll_frames
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(time, "sleep", lambda sec: clock.__setitem__(0, clock[0] + sec))
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)
    client = Client(s, transport=httpx.MockTransport(always_down_slowly))
    monkeypatch.setattr(client, "get", lambda path, **kw: {"frames": []})

    with pytest.raises(typer.Exit) as ei:
        cli._dump_follow(client, s, None)
    assert ei.value.exit_code == 3
    assert "unreachable" in capsys.readouterr().err
    # Generous ceiling: one poll's timeout of overshoot is fine, 25 minutes is not.
    assert clock[0] < 2 * cli.FOLLOW_GIVE_UP_S, (
        f"gave up after {clock[0]:g}s of a {cli.FOLLOW_GIVE_UP_S:g}s bound; the deadline "
        "is counting iterations rather than measuring elapsed time"
    )


def test_bad_frames_are_not_evidence_that_the_daemon_is_gone(monkeypatch, capsys) -> None:
    """A poll that answers 200 is the daemon being alive, whatever the frames look like.

    One counter served both guards, so undecodable frames drove the episode count that the
    give-up test reads: 149 bad frames from a daemon answering every poll turned the next
    transient error into exit 3 "unreachable for 30s" after 0.011 s of wall clock.
    """
    from mcuscope import cli

    clock = [0.0]
    polls = [0]

    def answering(request: httpx.Request) -> httpx.Response:
        polls[0] += 1
        if polls[0] > 150:
            raise httpx.ConnectError("connection refused")
        # 200, and every frame undecodable: the daemon is plainly alive.
        return httpx.Response(200, json={"frames": [{"no_line_id": 1}]})

    monkeypatch.setattr(time, "sleep", lambda sec: clock.__setitem__(0, clock[0] + sec))
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)
    client = Client(s, transport=httpx.MockTransport(answering))
    monkeypatch.setattr(client, "get", lambda path, **kw: {"frames": []})

    with pytest.raises(typer.Exit) as ei:
        cli._dump_follow(client, s, None)
    assert ei.value.exit_code == 3
    # It must have given up only after a real FOLLOW_GIVE_UP_S of unreachability, which
    # can only start once the polls actually start failing.
    gave_up_at = clock[0]
    assert gave_up_at >= 150 * cli.FOLLOW_POLL_S, (
        f"gave up at {gave_up_at:g}s; bad frames from a live daemon were counted as "
        "evidence that it was unreachable"
    )
    # And the drops are reported as what they were. Sharing one counter also mislabelled
    # them, which is the reporting half of the same defect (class 17): a frame the client
    # could not decode is not an "update" the daemon failed to answer.
    errs = capsys.readouterr().err
    assert "bad frame" in errs, f"frame drops were not reported as frames: {errs!r}"


def test_can_dump_follow_stops_on_an_error_no_retry_can_fix(monkeypatch, capsys) -> None:
    """The mirror-image clause of class 16: a guard that keeps looping must still know
    what is not per-item. A 4xx is the daemon rejecting the request itself."""
    from mcuscope import cli

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)
    client = Client(
        s,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(400, json={"error": "bad id filter"})
        ),
    )
    monkeypatch.setattr(client, "get", lambda path, **kw: {"frames": []})

    with pytest.raises(typer.Exit) as ei:
        cli._dump_follow(client, s, "999")
    assert ei.value.exit_code == 1
    assert "bad id filter" in capsys.readouterr().err


def test_can_dump_follow_reseeds_when_the_capture_token_changes(monkeypatch, capsys) -> None:
    """SPEC 3.4: a client caching ids compares the capture token and re-seeds on a change.

    `can dump -f` cached a lines.id watermark and never looked at the token, so after
    `mcu purge --all` (or a recreated DB) the new capture's low ids sat below it and the
    follow was silent for ever.
    """
    from mcuscope import cli

    frame = {"line_id": 3, "ts": 1.0, "can_id": 0x100, "dlc": 1, "data": "00",
             "rtr": False, "ext": False, "port": "p"}
    token = ["A"]
    polls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"capture": token[0]})
        since = request.url.params.get("since_id")
        if since is None:
            return httpx.Response(200, json={"frames": [{**frame, "line_id": 5000}]})
        polls[0] += 1
        if polls[0] == 1:
            token[0] = "B"      # the purge lands: same daemon, brand new id space
            return httpx.Response(200, json={"frames": []})
        if int(since) == 0:
            return httpx.Response(200, json={"frames": [frame]})
        if polls[0] > 4:
            raise KeyboardInterrupt          # the watermark never reset: end the test
        return httpx.Response(200, json={"frames": []})

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    s = Settings(url="http://127.0.0.1:1", json_out=True, port=None)
    client = Client(s, transport=httpx.MockTransport(handler))

    with pytest.raises(typer.Exit):
        cli._dump_follow(client, s, None)
    out = capsys.readouterr().out
    ids = [json.loads(line)["line_id"] for line in out.splitlines() if line.strip()]
    assert ids == [3], "the follow stayed silent across a capture change"


def test_follow_ws_auth_refusal_is_exit_1_and_capacity_stays_3(monkeypatch, capsys) -> None:
    """SPEC 4 reserves exit 3 for a daemon that cannot be reached. A refused subscription
    is the daemon answering, and the same refusal over REST exits 1."""
    import websockets
    import websockets.datastructures
    import websockets.exceptions
    import websockets.frames
    import websockets.http11

    from mcuscope import cli

    def refusing(exc: Exception):
        def connect(*a, **kw):
            raise exc
        return connect

    close_1008 = websockets.exceptions.ConnectionClosedError(
        websockets.frames.Close(1008, "token required"), None
    )
    close_1013 = websockets.exceptions.ConnectionClosedError(
        websockets.frames.Close(1013, "too many subscribers"), None
    )
    http_403 = websockets.exceptions.InvalidStatus(
        websockets.http11.Response(403, "Forbidden", websockets.datastructures.Headers())
    )
    http_502 = websockets.exceptions.InvalidStatus(
        websockets.http11.Response(502, "Bad Gateway", websockets.datastructures.Headers())
    )
    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)

    for exc, expected in ((close_1008, 1), (http_403, 1), (close_1013, 3), (http_502, 3)):
        monkeypatch.setattr(websockets, "connect", refusing(exc))
        with pytest.raises(typer.Exit) as ei:
            cli._follow_ws(s, None, None)
        assert ei.value.exit_code == expected, f"{exc!r} exited {ei.value.exit_code}"
        capsys.readouterr()


def test_a_null_field_in_a_daemon_body_is_an_exit_code_not_a_traceback(
    monkeypatch, capsys
) -> None:
    """Only KeyError was caught, so 200 {"lines": null} reached the user as a traceback:
    reversed(None) is a TypeError, and a short list would be an IndexError."""
    rc, _, errout = run_mcu_canned(monkeypatch, capsys, _json_body({"lines": None}), "lines")
    assert rc == 1
    assert "unexpected response from daemon" in errout


@pytest.mark.parametrize("cmd,key", [
    (("lines",), "lines"),
    (("can", "dump"), "frames"),
    (("session", "list"), "sessions"),
    (("ports",), "ports"),
    (("devices",), "devices"),
    (("plot", "channels"), "channels"),
])
def test_a_field_of_the_wrong_type_is_an_exit_code_not_a_traceback(
    monkeypatch, capsys, cmd, key
) -> None:
    """Swept class-wide, not just the probed command: every list field a command indexes
    directly gets the same answer when a 200 carries null (or an object) instead."""
    rc, _, errout = run_mcu_canned(monkeypatch, capsys, _json_body({key: None}), *cmd)
    assert rc == 1, (cmd, errout)
    assert "unexpected response from daemon" in errout and repr(key) in errout


def test_a_typeerror_from_our_own_code_is_not_blamed_on_the_daemon(monkeypatch) -> None:
    """The dispatcher caught TypeError as "malformed response", so a genuine CLI bug came
    out as "unexpected response from daemon" and left no crash log to debug (class 18)."""
    from mcuscope import cli

    def our_own_bug(*a, **kw):
        raise TypeError("'NoneType' object is not subscriptable")

    monkeypatch.setattr(cli, "app", our_own_bug)
    with pytest.raises(TypeError):
        cli._dispatch(["status"])


def test_status_says_so_when_the_store_writer_is_dead(monkeypatch, capsys) -> None:
    """/status carries writer_alive, but nothing a human reads showed it: with the writer
    dead the daemon answers, the port stays "connected" and rx keeps climbing while not
    one line is being stored (review class 12)."""
    body = {"version": "9.9", "uptime_s": 1.0, "db_path": "/x", "ports": [], "writer_alive": False}
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, _json_body(body), "status")
    assert rc == 0
    assert "CAPTURE STOPPED" in out
    body["writer_alive"] = True
    _rc, healthy, _e = run_mcu_canned(monkeypatch, capsys, _json_body(body), "status")
    assert "CAPTURE STOPPED" not in healthy


def test_plot_export_leaves_no_file_when_the_request_fails(monkeypatch, capsys, tmp_path):
    """The output file was opened before the request, so a 4xx left an empty CSV sitting
    where the user asked for an export. Client.download already removes its wreckage."""
    out_file = str(tmp_path / "export.csv")
    rc, _, errout = run_mcu_canned(
        monkeypatch, capsys,
        lambda request: httpx.Response(422, json={"error": "unknown channel"}),
        "plot", "export", "--names", "ax", "-o", out_file,
    )
    assert rc == 1
    assert "unknown channel" in errout
    assert not os.path.exists(out_file), "a failed export left a file behind"


def test_can_tx_rejects_an_impossible_rtr(monkeypatch, capsys) -> None:
    """SPEC 2.4: the DLC is one digit, 0..8, and an RTR frame carries no data. Both used
    to pass silently: `--rtr 12` emitted a two-digit token, and DATA was discarded."""
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the command reached the daemon")

    for args in (("can", "tx", "100", "--rtr", "12"),
                 ("can", "tx", "100", "--rtr", "-1"),
                 ("can", "tx", "100", "AABB", "--rtr", "2")):
        rc, _, _ = run_mcu_canned(monkeypatch, capsys, refuse, *args)
        assert rc == 1, args
    rc, _, _ = run_mcu_canned(
        monkeypatch, capsys, _json_body({"status": "ok", "data": ""}),
        "can", "tx", "100", "--rtr", "8",
    )
    assert rc == 0


def test_ai_guide_json_is_one_object() -> None:
    r = subprocess.run([*MCU, "--json", "ai-guide"], capture_output=True, **CHILD_TEXT, timeout=20)
    assert r.returncode == 0
    obj = json.loads(r.stdout)          # SPEC 4: exactly one JSON object, no prose
    assert "EXIT CODES" in obj["guide"]


def test_unparseable_url_exits_3_without_a_traceback() -> None:
    # httpx.InvalidURL is not an HTTPError, so it escaped every handler that caught one.
    from mcuscope import cli

    r = run_mcu(None, "daemon", "status", url="http://[::1")
    assert r.returncode == 3
    assert "Traceback" not in r.stderr
    assert cli._request_shutdown(Settings(url="http://[::1", json_out=False, port=None)) is False


def test_bad_port_in_url_exits_3_without_a_traceback() -> None:
    r = run_mcu(None, "daemon", "stop", url="http://127.0.0.1:notaport")
    assert r.returncode == 3
    assert "Traceback" not in r.stderr
    assert "bad daemon url" in r.stderr


def test_session_export_unsupported_scheme_exits_3(tmp_path) -> None:
    out = tmp_path / "out.db"
    r = run_mcu(None, "session", "export", "run", "-o", str(out), url="ftp://127.0.0.1:8765")
    assert r.returncode == 3
    assert "Traceback" not in r.stderr
    assert not out.exists()


def test_session_export_removes_a_partial_file(tmp_path, monkeypatch, capsys) -> None:
    # A stream that dies mid-transfer used to leave a truncated .db at the user's path,
    # indistinguishable from a complete export.
    def dies_midway(request: httpx.Request) -> httpx.Response:
        def body():
            yield b"SQLite format"
            raise httpx.ReadError("connection dropped")

        return httpx.Response(200, headers={"Content-Length": "1024"}, content=body())

    out = tmp_path / "partial.db"
    rc, _, _ = run_mcu_canned(monkeypatch, capsys, dies_midway,
                              "session", "export", "run", "-o", str(out))
    assert rc == 3
    assert not out.exists(), "a truncated export was left behind"


def test_null_uptime_is_not_a_traceback(monkeypatch, capsys) -> None:
    # _is_status_body checked key presence only, so `uptime_s: null` reached a format
    # specifier and raised TypeError at the user.
    body = _json_body({**_STATUS_STUB_BODY, "uptime_s": None})
    rc, _, _ = run_mcu_canned(monkeypatch, capsys, body, "daemon", "status")
    assert rc == 3
    _, out, _ = run_mcu_canned(monkeypatch, capsys, body, "status")
    assert "up ?s" in out


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({**_STATUS_STUB_BODY, "write_errors": 3}, "write_errors=3"),
        ({**_STATUS_STUB_BODY, "write_errors": 0}, None),
        (_STATUS_STUB_BODY, None),
    ],
)
def test_status_shows_write_errors_only_when_non_zero(body, expected, monkeypatch, capsys):
    # A store-wide write-failure count, displayed like a port's rx_dropped: mentioned
    # only when there are some, and read with .get so an older daemon still works.
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _json_body(body), "status")
    assert rc == 0, err
    if expected:
        assert expected in out
    else:
        assert "write_errors" not in out


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        ({"latest": "9.9.9", "available": True, "checked_at": 1.0, "url": "x"}, "mcuscope 9.9.9"),
        ({"latest": "0.1.0", "available": False, "checked_at": 1.0, "url": "x"}, None),
        (None, None),          # the check is switched off
    ],
)
def test_status_reports_an_available_release(update, expected, monkeypatch, capsys) -> None:
    # The release check (SPEC 3.6) used to reach only the web UI badge, so nobody driving
    # the CLI - the normal way an agent or a headless bench uses this - ever learned a new
    # version existed. Absent field and null both mean "nothing to say", not a traceback.
    body = {**_STATUS_STUB_BODY, "update": update} if update else _STATUS_STUB_BODY
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _json_body(body), "status")
    assert rc == 0, err
    if expected:
        assert f"update available: {expected}" in out
        # The upgrade command must name an installer README.md actually documents.
        assert "uv tool upgrade mcuscope" in out
        assert "pipx upgrade mcuscope" in out
    else:
        assert "update available" not in out


def test_log_export_json_with_no_rows_prints_nothing(stack: Stack) -> None:
    r = run_mcu(stack, "--json", "log", "export", "--match", "zzz-nothing-matches-this")
    assert r.returncode == 0
    assert r.stdout == ""       # a bare newline is not a JSON document


def test_global_option_without_a_value_names_the_option() -> None:
    r = run_mcu(None, "status", "-p", url="http://127.0.0.1:1")
    assert r.returncode == 1
    assert "-p" in r.stderr and "value" in r.stderr
    assert "Missing command" not in r.stderr


def test_version_is_hoisted_like_the_other_globals() -> None:
    r = subprocess.run([*MCU, "daemon", "--version"], capture_output=True, **CHILD_TEXT, timeout=20)
    assert r.returncode == 0
    assert "mcuscope" in r.stdout


def test_version_json_is_one_object() -> None:
    r = subprocess.run(
        [*MCU, "--version", "--json"], capture_output=True, **CHILD_TEXT, timeout=20
    )
    assert r.returncode == 0
    obj = json.loads(r.stdout)
    assert obj["version"] and "python" in obj


def test_usage_error_json_is_one_object() -> None:
    r = subprocess.run(
        [*MCU, "--json", "nosuchcommand"], capture_output=True, **CHILD_TEXT, timeout=20
    )
    assert r.returncode == 1
    obj = json.loads(r.stdout)
    assert obj["exit_code"] == 1 and "nosuchcommand" in obj["error"]


def test_json_confirmation_refuses_rather_than_prompting(stack: Stack) -> None:
    """A --json consumer that never writes an answer waited on the prompt forever."""
    run_mcu(stack, "mark", "survives-json-refusal")
    r = run_mcu(stack, "--json", "purge", "--all")
    assert r.returncode == 1
    obj = json.loads(r.stdout)
    assert obj["exit_code"] == 1 and "-y" in obj["error"]
    assert "[y/N]" not in r.stderr
    left = run_mcu(stack, "lines", "--limit", "500", "--json")
    assert "survives-json-refusal" in [x["raw"] for x in json.loads(left.stdout)["lines"]]


def test_ctrl_c_ends_a_follow_with_success(monkeypatch, capsys) -> None:
    """Ctrl-C is how a follow is meant to end, so `mcu tail -f` exits 0.

    Driven through the WebSocket seam rather than a real signal: SIGINT to a child is not
    portable (Windows needs a process group and CTRL_BREAK), and what the code has to get
    right is the interrupt arriving inside the follow loop, not the signal delivery.
    """
    import websockets

    from mcuscope import cli

    row = json.dumps([{"ts": 1.0, "chan": "log", "raw": "before-the-interrupt",
                       "port": "p", "id": 1}])
    monkeypatch.setattr(
        websockets, "connect",
        lambda url, **kw: _ScriptedWS([row, KeyboardInterrupt()]), raising=False,
    )
    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)
    with pytest.raises(typer.Exit) as ei:
        cli._follow_ws(s, None, None)

    assert ei.value.exit_code == 0
    assert "before-the-interrupt" in capsys.readouterr().out


def test_a_response_missing_a_key_is_an_exit_code_not_a_traceback(monkeypatch, capsys) -> None:
    """Every command indexes the fields it prints, so a short body (version skew, a proxy,
    the wrong port answering) reached the user as a rich traceback instead of an exit code.
    """
    body = _json_body({"version": "9.9-stub", "uptime_s": 1.0, "db_path": "/x"})  # no "ports"
    rc, out, errout = run_mcu_canned(monkeypatch, capsys, body, "status")
    assert rc == 1
    assert "unexpected response from daemon" in errout and "'ports'" in errout
    assert out.strip() == "" or "ports" not in out


def test_a_write_failing_mid_stream_is_an_exit_code_not_a_traceback(capsys) -> None:
    """The sink failing partway through a streamed export (a disk filling up).

    Not the same failure as a destination that will not open: `plot export` dies on that
    one before the request is made, which is the site the unwritable-path test reaches.
    """
    from mcuscope import cli

    def sink(chunk: str) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    client = cli.Client(
        Settings(url="http://127.0.0.1:1", json_out=False, port=None),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="ts,name,value\n1.0,sine,0.5\n")
        ),
    )
    with pytest.raises(typer.Exit) as ei:
        client.stream_text("/plot/export", sink, what="out.csv")

    assert ei.value.exit_code == 1
    assert "cannot write out.csv" in capsys.readouterr().err


def test_the_follow_match_budget_is_the_daemons() -> None:
    """The client's --match ceiling is a copy of the daemon's, and a copy is what drifts.

    `store` is imported here rather than in cli.py on purpose: the value is duplicated so
    the CLI does not pull the daemon's SQLite stack into its ~190 ms startup.
    """
    from mcuscope import cli, store

    assert cli.FOLLOW_MATCH_TIMEOUT_S == store.MATCH_TIMEOUT_S


def test_hoisting_survives_a_command_tree_it_cannot_read(monkeypatch) -> None:
    """Resolving the subcommand tells hoisting which tokens are option values. Any failure
    there costs that refinement, never the command line."""
    from mcuscope import cli

    def boom(*a, **kw):
        raise RuntimeError("no command tree today")

    monkeypatch.setattr(typer.main, "get_command", boom)

    # None, not an empty set: an empty set reads as "nothing here takes a value" and
    # hoisting then ran without the guard, re-arming the value-stealing defect the guard
    # exists to prevent. A resolver failure degrades to no hoisting at all.
    assert cli._value_taking_opts(["lines", "--limit", "5"]) is None
    argv = ["lines", "--limit", "5", "--json"]
    assert cli._hoist_global_opts(list(argv)) == argv
    assert cli._split_global_opts(list(argv)) == ([], argv)


def test_hoisting_is_a_pure_rewrite() -> None:
    """Rearranging argv must not also decide the output mode.

    It did, and nothing reset it: a unit test of the rewriter then left every later
    command in the process in --json mode, which made the suite order-dependent and hid
    what die() really does on a --json stream.
    """
    from mcuscope import cli, cli_output

    before = cli_output._JSON_MODE
    assert cli._hoist_global_opts(["tail", "-f", "--json"]) == ["--json", "tail", "-f"]
    assert cli_output._JSON_MODE is before


def test_the_output_mode_does_not_leak_into_the_next_invocation(monkeypatch, capsys) -> None:
    """--json is resolved per invocation, not once per process.

    die() reads a module global, because it is called from helpers that hold no Settings;
    a second command in the same process must not inherit the first one's mode.
    """
    from mcuscope import cli

    body = lambda request: httpx.Response(500, json={"error": "boom"})  # noqa: E731
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, body, "--json", "status")
    assert rc == 1 and json.loads(out)["exit_code"] == 1

    # A usage error is refused before the group callback runs, so this second command
    # never sets the mode itself - it can only inherit one.
    rc = cli.main(["nosuchcommand"])
    out, errout = capsys.readouterr()
    assert rc == 1
    assert out == ""            # no envelope: this invocation was not asked for --json
    assert "nosuchcommand" in errout


def run_mcu_closed_stderr(
    stack: Stack | None, *args: str, url: str | None = None, timeout: float = CLI_TIMEOUT_S,
) -> tuple[int, str]:
    """Run `mcu ...` with its stderr closed under it, stdout still drained.

    The mirror of run_mcu_closed_pipe: `mcu status 2>&-`, or any parent that stops reading
    the diagnostics stream. The exit code is the CLI's contract and must not depend on
    whether the message could be delivered.
    """
    env = os.environ.copy()
    env["MCUSCOPE_URL"] = url if url is not None else (stack.base_url if stack else "")
    proc = subprocess.Popen(
        [*MCU, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, **CHILD_TEXT, env=env
    )
    assert proc.stdout is not None and proc.stderr is not None
    proc.stderr.close()
    try:
        out = proc.stdout.read()
        proc.wait(timeout=timeout)
    finally:
        proc.kill()
        proc.stdout.close()
    return proc.returncode, out


def test_a_closed_stderr_does_not_turn_an_error_into_success() -> None:
    """A message that cannot be delivered is not a command that succeeded.

    err() printed to stderr unguarded, so a closed stderr raised BrokenPipeError out of
    die() before its typer.Exit, and the dispatcher's broken-pipe arm - which reasons about
    a *stdout* reader being finished - answered 0. Every error exit became 0.
    """
    attached = run_mcu(None, "status", url="http://127.0.0.1:1")
    assert attached.returncode == 3, attached.stderr        # the differential baseline
    rc, _ = run_mcu_closed_stderr(None, "status", url="http://127.0.0.1:1")
    assert rc == 3


def test_a_closed_stderr_still_leaves_json_on_stdout() -> None:
    """--json owes stdout its one object even when the human message goes nowhere."""
    rc, out = run_mcu_closed_stderr(None, "--json", "status", url="http://127.0.0.1:1")
    assert rc == 3
    assert json.loads(out.strip())["exit_code"] == 3


def test_a_closed_stdout_is_still_success(stack: Stack) -> None:
    """The other half of the pair: a finished reader on stdout stays exit 0 (SPEC 4)."""
    rc, _ = run_mcu_closed_pipe(stack, "status")
    assert rc == 0


def test_a_global_option_missing_its_value_still_prints_json(stack: Stack) -> None:
    """`mcu --json status --url`: rejected during hoisting, before the dispatcher had
    classified argv, so --json got an empty stdout and a consumer parsing it got nothing.
    """
    r = run_mcu(stack, "--json", "status", "--url")
    assert r.returncode == 1
    body = json.loads(r.stdout.strip())
    assert body["exit_code"] == 1 and "--url" in body["error"]
    assert r.stderr.strip()          # the human message is unchanged


def test_the_json_equals_spelling_shapes_errors_too(stack: Stack) -> None:
    """`--json=1` is hoisted as a global token but is not equal to "--json", so the exact
    match never set the mode: click's rejection of a value on a flag then reached a --json
    consumer as an empty stdout.
    """
    r = run_mcu(stack, "--json=1", "status")
    assert r.returncode == 1
    assert json.loads(r.stdout.strip())["exit_code"] == 1


def test_a_list_of_non_objects_is_an_exit_code_not_a_traceback(monkeypatch, capsys) -> None:
    """_list_field checked the field was a list and not what was in it.

    Every caller subscripts the elements by name, so a daemon answering {"lines": ["x"]}
    (version skew, a proxy, the wrong port) reached the user as a TypeError traceback and
    a crash log - the same skew the type check exists to report, one level down.
    """
    rc, out, errout = run_mcu_canned(monkeypatch, capsys, _json_body({"lines": ["x"]}), "lines")
    assert rc == 1
    assert "unexpected response from daemon" in errout and "'lines'" in errout
    assert "Traceback" not in errout

    body = _json_body({"version": "9.9", "uptime_s": 1.0, "db_path": "/x", "ports": ["p"]})
    rc, _, errout = run_mcu_canned(monkeypatch, capsys, body, "status")
    assert rc == 1
    assert "unexpected response from daemon" in errout and "'ports'" in errout


def test_can_follow_resets_the_watermark_when_the_first_token_read_failed(
    monkeypatch, capsys
) -> None:
    """The reset was gated on already holding a token, and priming it is allowed to fail.

    One transient /status error at start left `capture` None, so a later restart onto a
    fresh capture stored the new token without resetting `since` and the follow was silent
    for ever. Adopting a first token resets too: a bounded replay against permanent silence.
    """
    from mcuscope import cli

    frame = {"line_id": 3, "ts": 1.0, "can_id": 0x100, "dlc": 1, "data": "00",
             "rtr": False, "ext": False, "port": "p"}
    status_calls = [0]
    polls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            status_calls[0] += 1
            if status_calls[0] == 1:
                raise httpx.ConnectError("transient", request=request)   # priming fails
            return httpx.Response(200, json={"capture": "B"})
        since = request.url.params.get("since_id")
        if since is None:
            return httpx.Response(200, json={"frames": [{**frame, "line_id": 5000}]})
        polls[0] += 1
        if int(since) == 0:
            return httpx.Response(200, json={"frames": [frame]})
        if polls[0] > 4:
            raise KeyboardInterrupt          # the watermark never reset: end the test
        return httpx.Response(200, json={"frames": []})

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    s = Settings(url="http://127.0.0.1:1", json_out=True, port=None)
    client = Client(s, transport=httpx.MockTransport(handler))

    with pytest.raises(typer.Exit):
        cli._dump_follow(client, s, None)
    ids = [json.loads(line)["line_id"] for line in capsys.readouterr().out.splitlines()
           if line.strip()]
    assert ids == [3], "a token first read after a failed prime left the follow silent"
