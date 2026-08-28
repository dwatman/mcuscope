"""Regression tests for the round-2 review's CLI findings.

Each test here pins one behaviour that was reverted to check it fails without its fix:
the daemon-start pid guards, the dispatcher's --json arms, the purge refusals and output,
the argv hoisting, the daemon-error mapping, and the two closed-stdout/orphaned-task
shapes. The subprocess-driven ones use the same helpers as test_cli.py.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import subprocess
import sys
import time

import httpx
import pytest
import typer

from mcuscope.cli import Settings
from tests.test_cli import (
    _PIDDIR_ENV_SKIP,
    _run_mcu_data_home,
    _write_pid_record,
    run_mcu_canned,
)

# -- F1: `daemon start` must not clobber a live daemon's record, nor report a dead pid ---


def test_write_pid_record_refuses_a_record_naming_a_live_process(tmp_path) -> None:
    """pidfile.claim's rule, applied to the CLI's own write.

    A start that loses the bind race used to replace the winner's correct record with its
    own dying child's pid, leaving the live daemon addressed by a dead number.
    """
    from mcuscope.cli_daemonctl import _write_pid_record

    path = tmp_path / "mcuscoped-127.0.0.1-1.pid"
    path.write_text(str(os.getpid()), encoding="utf-8", newline="\n")
    assert _write_pid_record(str(path), 424242) is False
    assert path.read_text(encoding="utf-8") == str(os.getpid())
    # A stale record (nothing running under that pid) is still taken over.
    path.write_text(str(_dead_pid()), encoding="utf-8", newline="\n")
    assert _write_pid_record(str(path), 424242) is True
    assert path.read_text(encoding="utf-8") == "424242"
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]   # no .tmp left


def _dead_pid() -> int:
    """The pid of a process that has certainly exited (and been reaped)."""
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    return proc.pid


class _FakeChild:
    """A `daemon start` child: alive or dead on demand, without spawning anything."""

    def __init__(self, pid: int, exited: int | None = None) -> None:
        self.pid = pid
        self._exited = exited
        self.terminated = False

    def poll(self) -> int | None:
        return self._exited

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self._exited = 0
        return 0


def test_daemon_start_refuses_when_another_daemon_serves_the_url(
    monkeypatch, tmp_path, capsys
) -> None:
    """A URL answering for a different pid is this start's failure, not its success.

    Two concurrent starts: the loser's child dies on the port conflict, the winner answers
    /status, and the loser printed "started mcuscoped (pid <dead>)" and replaced the
    winner's record with that dead pid. Driven with a fake child, so nothing is spawned.
    """
    from mcuscope import cli

    pid_path = tmp_path / "d.pid"
    pid_path.write_text("777", encoding="utf-8", newline="\n")   # the winner's record
    child = _FakeChild(pid=424242, exited=1)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **kw: child)
    monkeypatch.setattr(cli, "_pid_file", lambda s: str(pid_path))
    monkeypatch.setattr("mcuscope.pidfile.pid_running", lambda pid: pid == 777)

    calls = {"n": 0}

    def status(s, timeout=2.0):
        calls["n"] += 1
        # Nothing is running when the start begins; then the winner answers.
        if calls["n"] == 1:
            return None
        return {"version": "9.9", "uptime_s": 1.0, "ports": [], "pid": 777}

    monkeypatch.setattr(cli, "_status_body", status)

    rc = cli.main(["daemon", "start", "--url", "http://127.0.0.1:1"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "another daemon is already serving" in err and "777" in err
    assert "started mcuscoped" not in out
    assert pid_path.read_text(encoding="utf-8") == "777", "the live daemon's record was taken"


# -- F3: the pid path is resolved before the spawn, and its failure is an exit code ------


@_PIDDIR_ENV_SKIP
def test_daemon_start_reports_an_unusable_data_dir_without_spawning(tmp_path) -> None:
    """XDG_DATA_HOME pointing at a regular file: exit 1 with the path, not a traceback."""
    from tests.support import free_port
    from tests.test_cli import MCU

    data_home = tmp_path / "not-a-dir"
    data_home.write_text("", encoding="utf-8", newline="\n")
    env = os.environ.copy()
    env["XDG_DATA_HOME"] = str(data_home)
    env["MCUSCOPE_URL"] = f"http://127.0.0.1:{free_port()}"
    r = subprocess.run(
        [*MCU, "daemon", "start", "--timeout", "0.05"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=60,
    )
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert "pid file" in r.stderr and "mcuscope" in r.stderr


# -- F2: every dispatcher arm emits exactly one JSON object under --json ----------------


def test_json_mode_gets_one_object_from_the_key_error_arm(monkeypatch, capsys) -> None:
    rc, out, err = run_mcu_canned(
        monkeypatch, capsys, lambda request: httpx.Response(200, json={"ok": True}),
        "--json", "purge", "--all", "-y",
    )
    assert rc == 1
    assert json.loads(out) == {
        "error": "unexpected response from daemon: 'deleted'", "exit_code": 1
    }


def _run_json_status_raising(monkeypatch, capsys, exc: BaseException):
    """`mcu --json status` where the request raises `exc` from inside the command."""
    from mcuscope import cli

    def boom(self, path: str, **kw: object) -> None:
        raise exc

    monkeypatch.setattr(cli.Client, "get", boom)
    rc = cli.main(["--json", "status", "--url", "http://127.0.0.1:1"])
    out, err = capsys.readouterr()
    return rc, out, err


def test_json_mode_gets_one_object_from_the_abort_arm(monkeypatch, capsys) -> None:
    """Declining a confirmation prompt: click's Abort, on the --json contract."""
    rc, out, err = _run_json_status_raising(monkeypatch, capsys, typer.Abort())
    assert rc == 1
    assert json.loads(out) == {"error": "aborted", "exit_code": 1}
    assert "aborted" in err


def test_json_mode_gets_one_object_from_the_keyboard_interrupt_arm(monkeypatch, capsys) -> None:
    """A Ctrl-C the app call did not convert.

    typer turns an interrupt raised *inside* a command into Exit(130) (typer/core.py), so
    this arm covers one that escapes the call itself; it is driven here at that boundary
    rather than through a command, which cannot reach it.
    """
    from mcuscope import cli

    def boom(**kw: object) -> int:
        raise KeyboardInterrupt

    # The hoist result this argv really produces; stubbed because the app it walks is the
    # object being replaced below.
    monkeypatch.setattr(cli, "_split_global_opts", lambda argv: (["--json"], ["status"]))
    monkeypatch.setattr(cli, "app", boom)
    rc = cli.main(["--json", "status"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert json.loads(out) == {"error": "interrupted", "exit_code": 1}
    assert "interrupted" in err


# -- F6 / measurement F1: --before-days is an age, never a wipe -------------------------


@pytest.mark.parametrize("value", ["-1", "0", "-0.5"])
def test_purge_before_days_refuses_a_non_positive_age(monkeypatch, capsys, value: str) -> None:
    """A negative age puts before_ts in the future, which selects the whole capture."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"deleted": 0, "id_from": None, "id_to": None})

    rc, out, err = run_mcu_canned(
        monkeypatch, capsys, handler, "purge", "--before-days", value, "--dry-run"
    )
    assert rc == 1
    assert "--before-days must be greater than 0" in err
    assert seen == [], "the purge was sent to the daemon anyway"


# -- measurement F3: no None-None id range in the purge preview -------------------------


def test_purge_dry_run_omits_the_id_range_when_nothing_matched(monkeypatch, capsys) -> None:
    rc, out, _ = run_mcu_canned(
        monkeypatch, capsys,
        lambda request: httpx.Response(200, json={"deleted": 0, "id_from": None, "id_to": None}),
        "purge", "--before-days", "999", "--dry-run",
    )
    assert rc == 0
    assert out.strip() == "would delete 0 lines"


def test_purge_dry_run_keeps_the_id_range_when_there_is_one(monkeypatch, capsys) -> None:
    rc, out, _ = run_mcu_canned(
        monkeypatch, capsys,
        lambda request: httpx.Response(200, json={"deleted": 3, "id_from": 1, "id_to": 9}),
        "purge", "--before-days", "1", "--dry-run",
    )
    assert rc == 0 and out.strip() == "would delete 3 lines (ids 1-9)"


# -- measurement F2: the truncation note reports what came back -------------------------


def test_truncation_note_reports_the_returned_rows_not_the_request(capsys) -> None:
    """The daemon caps /lines at 1000 below any bigger request.

    Naming the request read as "your limit did this" and offered a remedy ("raise
    --limit") that is inert above the cap.
    """
    from mcuscope.cli_output import note_truncated

    body = {"lines": [{"id": i} for i in range(1000)], "truncated": True}
    note_truncated(body, 20000)
    err = capsys.readouterr().err
    assert "truncated at 1000 rows" in err
    assert "raise --limit" not in err
    assert "use --since-id" in err


def test_truncation_note_still_offers_a_bigger_limit_when_the_user_capped_it(capsys) -> None:
    from mcuscope.cli_output import note_truncated

    body = {"lines": [{"id": i} for i in range(5)], "truncated": True}
    note_truncated(body, 5)
    err = capsys.readouterr().err
    assert "truncated at 5 rows" in err and "raise --limit" in err


# -- F8: hoisting tracks value consumption ---------------------------------------------


def test_hoisting_sees_a_global_after_a_value_that_looks_like_an_option() -> None:
    """`mcu lines --match --limit --json`: --limit is the regex, so --json is still global.

    The old guard compared the literal previous token, so the *value* --limit read as an
    option awaiting a value and --json stayed behind the subcommand, where click rejects
    it - and a --json consumer got exit 1 with an empty stdout.
    """
    from mcuscope.cli import _hoist_global_opts as hoist

    assert hoist(["lines", "--match", "--limit", "--json"]) == \
        ["--json", "lines", "--match", "--limit"]
    # The value's own value is still not hoisted: --limit here is data, not an option.
    assert hoist(["lines", "--match", "--limit", "5"]) == ["lines", "--match", "--limit", "5"]


# -- RG-F6: a token that cannot go on the wire is a refusal, not a traceback -------------


def test_non_ascii_token_is_refused_as_bad_usage(monkeypatch, capsys) -> None:
    from mcuscope import cli

    rc = cli.main(["--token", "tökén", "status", "--url", "http://127.0.0.1:1"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "token must be ASCII" in err


def test_non_ascii_token_from_the_environment_is_refused(monkeypatch, capsys) -> None:
    from mcuscope import cli

    monkeypatch.setenv("MCUSCOPE_TOKEN", "tökén")
    rc = cli.main(["--json", "status", "--url", "http://127.0.0.1:1"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "token must be ASCII" in err
    assert json.loads(out)["exit_code"] == 1


def test_a_value_error_from_the_transport_is_mapped_not_raised(monkeypatch, capsys) -> None:
    """_daemon_errors covers ValueError, which httpx raises while encoding a request."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise UnicodeEncodeError("ascii", "x", 0, 1, "not ascii")

    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler, "status")
    assert rc == 1
    assert "cannot send request to" in err


# -- RG-F9: a millisecond timeout is bounded client-side --------------------------------


@pytest.mark.parametrize("command", ["cmd", "wait", "assert"])
def test_ms_timeout_out_of_range_is_a_usage_refusal(monkeypatch, capsys, command: str) -> None:
    """`--timeout 99999999999999999999` raised OverflowError out of httpx."""
    args = {
        "cmd": ["cmd", "x"], "wait": ["wait", "--match", "x"],
        "assert": ["assert", "--expect", "x"],
    }[command]
    rc, out, err = run_mcu_canned(
        monkeypatch, capsys, lambda request: httpx.Response(200, json={}),
        *args, "--timeout", "99999999999999999999",
    )
    assert rc == 1
    assert "300000 ms" in err
    assert "OverflowError" not in err


# -- RG-F10: daemon fields are vouched for at the point of use ---------------------------


@pytest.mark.parametrize(
    ("args", "body", "key"),
    [
        (["status"], {"version": "1", "uptime_s": 1.0, "db_path": "x", "ports": [],
                      "session": "notadict"}, "'session'"),
        (["attach", "/dev/null"], {"port": "notadict"}, "'port'"),
        (["assert", "--expect", "x"], {"status": "fail", "checked_lines": 0,
                                       "elapsed_ms": 1, "expect": None, "forbid": []},
         "'expect'"),
        (["session", "start", "run1"], {"session": "notadict"}, "'session'"),
        (["session", "stop"], {"session": "notadict"}, "'session'"),
    ],
)
def test_a_wrongly_typed_daemon_field_is_reported_not_a_traceback(
    monkeypatch, capsys, args: list[str], body: dict, key: str
) -> None:
    rc, out, err = run_mcu_canned(
        monkeypatch, capsys, lambda request: httpx.Response(200, json=body), *args
    )
    assert rc == 1
    assert "unexpected response from daemon" in err and key in err
    assert "Traceback" not in err


# -- R35 (class 35): a closed stdout must not turn an error exit into 0 -------------------


def test_json_error_exit_survives_a_closed_stdout() -> None:
    """`mcu --json status | head -0` still exits 3 when the daemon is unreachable.

    die() writes its JSON object to stdout, and that write raising BrokenPipeError landed
    in the dispatcher's broken-pipe arm, which answers 0: every --json error exit did.
    """
    from tests.test_cli import run_mcu_closed_pipe

    rc, _ = run_mcu_closed_pipe(None, "--json", "status", url="http://127.0.0.1:1")
    assert rc == 3


# -- F7 / class 7: the stale-record stop path says what it did ---------------------------


@_PIDDIR_ENV_SKIP
def test_daemon_stop_reports_removing_a_stale_pid_record(tmp_path) -> None:
    """No daemon answering and the recorded pid gone: remove the record and say so."""
    from tests.test_cli import _child_data_dir

    dead = _dead_pid()
    _write_pid_record(str(tmp_path), "127.0.0.1", 1, dead)
    # The child resolves its own data dir; write the record where it will look.
    data_dir = _child_data_dir(str(tmp_path))
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "mcuscoped-127.0.0.1-1.pid")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(str(dead))
    r = _run_mcu_data_home(str(tmp_path), "daemon", "stop")
    assert r.returncode == 1
    assert "removed stale pid file" in r.stderr and str(dead) in r.stderr
    assert not os.path.exists(path)


# -- TQ-F2 half B: _follow_ws must consume the recv it was handed -------------------------


def test_follow_ws_consumes_its_pending_recv_when_the_staged_drain_raises(
    monkeypatch, caplog
) -> None:
    """`mcu tail -f | head` ends in the staged drain, with a recv still in flight.

    Half A of this fix (_stage_backfill) has a test; the half that runs on the ordinary
    follow path did not, and could be deleted with the suite green. Left unawaited, the
    recv resolves with the socket teardown's ConnectionClosed and asyncio reports "Task
    exception was never retrieved" when the task is collected.
    """
    import asyncio

    import websockets

    from mcuscope import cli

    class _StagingWs:
        """Hands out one frame, then blocks; the close resolves the recv with an error."""

        def __init__(self) -> None:
            self._closed = asyncio.Event()
            self._sent = False

        async def recv(self) -> str:
            if not self._sent:
                self._sent = True
                return json.dumps([{"id": 1, "ts": 0.0, "port": "b", "dir": "rx",
                                    "chan": "debug", "seq": None, "raw": "staged"}])
            await self._closed.wait()
            raise websockets.exceptions.ConnectionClosedOK(None, None)

        async def __aenter__(self) -> _StagingWs:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            # A real close awaits its handshake, so an orphaned recv resolves here rather
            # than being cancelled by asyncio.run's shutdown (which files no report).
            self._closed.set()
            await asyncio.sleep(0.05)
            return False

    class _ClosedStdout:
        def write(self, text: str) -> int:
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self) -> None:
            raise BrokenPipeError(32, "Broken pipe")

        def fileno(self) -> int:
            raise OSError("no fd")

    def slow_snapshot() -> int:
        # Slower than the first frame, so the frame is *staged* and a second recv is in
        # flight when the snapshot returns: that pair is the only window where the drain
        # can raise with a recv still owned by the caller.
        time.sleep(0.2)
        return 0

    monkeypatch.setattr(websockets, "connect", lambda *a, **kw: _StagingWs())
    monkeypatch.setattr(sys, "stdout", _ClosedStdout())
    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)

    code = None
    with caplog.at_level(logging.ERROR, logger="asyncio"):
        try:
            cli._follow_ws(s, None, None, backfill=slow_snapshot)
        except typer.Exit as exc:
            code = exc.exit_code
        # Not pytest.raises: its ExceptionInfo keeps the traceback, and with it the frame
        # holding the recv, alive - so an orphaned task is never collected and files no
        # report. Task.__del__ is what reports, so the reference has to be gone first.
        gc.collect()
    assert code == 0, "the closed pipe must end the follow with exit 0"
    assert "never retrieved" not in caplog.text
