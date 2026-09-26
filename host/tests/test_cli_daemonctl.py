"""`mcu daemon start` and `restart` (`cli_daemonctl.py`): the spawn, the readiness probe and
what counts as a running daemon, the pid record, and the config they carry (SPEC 4).

A named config that does not exist is refused (exit 1, `no such config file: <path>`) before
anything is probed, stopped or spawned; a missing default config still means defaults."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import types
from ctypes import wintypes

import httpx
import pytest

from mcuscope import cli, cli_client, cli_daemonctl
from mcuscope.config import default_config_path
from tests.support import DEAD, STATUS, UNREACHABLE, canned, dead_pid, record_params
from tests.test_cli import _PIDDIR_ENV_SKIP


class _Daemon:
    spawned: list[_Daemon] = []

    def __init__(self, args, **kwargs):
        self.pid, self.args = 4242, args
        self.start_id = kwargs["env"]["MCUSCOPED_START_ID"]
        _Daemon.spawned.append(self)

    def poll(self):
        return None


@pytest.fixture
def spawn(monkeypatch, tmp_path):
    """Fake Popen and /status; records probes and stops. Nothing answers until spawned."""
    log: dict[str, list] = {"probes": [], "stops": []}
    pid_path = tmp_path / "mcuscoped-127.0.0.1-1.pid"
    monkeypatch.setattr(cli, "_pid_file", lambda s: str(pid_path))
    monkeypatch.setattr(_Daemon, "spawned", [])
    monkeypatch.setattr(cli.subprocess, "Popen", _Daemon)
    log["running"] = None      # a /status body answered before the spawn, for restart

    def stop(s, restarting=False):
        log["stops"].append(s)
        log["running"] = None

    monkeypatch.setattr(cli, "_stop_daemon", stop)
    monkeypatch.setattr(cli.Client, "probe", lambda self, m, path: {"ports": []})
    monkeypatch.delenv("MCUSCOPED_CONFIG", raising=False)

    def status_body(s, timeout=2.0):
        log["probes"].append(1)
        if _Daemon.spawned:
            return {"version": "0", "uptime_s": 0.0, "ports": [], "pid": 4242}
        return log["running"]

    monkeypatch.setattr(cli, "_status_body", status_body)
    monkeypatch.setattr(cli, "_status_or_refusal",
                        lambda s, timeout=2.0: (status_body(s, timeout), None,
                                                _Daemon.spawned[-1].start_id))
    log["pid_path"] = pid_path
    return log


def _refused(capsys, argv, path) -> None:
    rc = cli.main([*argv, "--url", DEAD])
    out, err = capsys.readouterr()
    assert rc == 1, err
    assert f"no such config file: {path}" in err, err
    assert _Daemon.spawned == [], "refused before the spawn"


def _config_arg() -> str | None:
    args = _Daemon.spawned[-1].args
    return args[args.index("--config") + 1] if "--config" in args else None


def test_a_missing_relative_config_is_refused_as_its_resolved_path(spawn, tmp_path, monkeypatch,
                                                                   capsys) -> None:
    monkeypatch.chdir(tmp_path)
    _refused(capsys, ["daemon", "start", "-c", "typo.toml"], tmp_path / "typo.toml")
    assert spawn["probes"] == [], "refused before even probing for a running daemon"
    assert not spawn["pid_path"].exists()


def test_a_missing_config_under_home_is_refused_expanded(spawn, tmp_path, monkeypatch,
                                                         capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    _refused(capsys, ["daemon", "start", "-c", "~/typo.toml"], tmp_path / "typo.toml")


def test_a_refusal_in_json_mode_is_the_one_json_object(spawn, tmp_path, capsys) -> None:
    missing = tmp_path / "typo.toml"
    rc = cli.main(["--json", "daemon", "start", "-c", str(missing), "--url", DEAD])
    out, err = capsys.readouterr()
    assert rc == 1
    assert json.loads(out) == {"error": f"no such config file: {missing}", "exit_code": 1}


def test_a_directory_is_not_a_config_file(spawn, tmp_path, capsys) -> None:
    _refused(capsys, ["daemon", "start", "-c", str(tmp_path)], tmp_path)


def test_a_missing_file_named_by_the_env_var_is_refused(spawn, tmp_path, monkeypatch,
                                                        capsys) -> None:
    monkeypatch.setenv("MCUSCOPED_CONFIG", str(tmp_path / "env-typo.toml"))
    _refused(capsys, ["daemon", "start"], tmp_path / "env-typo.toml")


def test_the_flag_wins_over_a_missing_env_file(spawn, tmp_path, monkeypatch, capsys) -> None:
    good = tmp_path / "good.toml"
    good.write_text("", encoding="utf-8")
    monkeypatch.setenv("MCUSCOPED_CONFIG", str(tmp_path / "env-typo.toml"))
    assert cli.main(["daemon", "start", "-c", str(good), "--url", DEAD]) == 0, \
        capsys.readouterr().err
    assert _config_arg() == str(good)


def test_an_existing_relative_or_home_config_is_forwarded_resolved(spawn, tmp_path,
                                                                   monkeypatch, capsys) -> None:
    """The daemon does not expand `~`, and a restart from another directory re-reads
    config_path: the CLI forwards the path it checked."""
    (tmp_path / "rel.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["daemon", "start", "-c", "rel.toml", "--url", DEAD]) == 0
    assert _config_arg() == str(tmp_path / "rel.toml")

    _Daemon.spawned.clear()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("MCUSCOPED_CONFIG", "~/rel.toml")
    assert cli.main(["daemon", "start", "--url", DEAD]) == 0, capsys.readouterr().err
    assert _config_arg() == str(tmp_path / "rel.toml"), "the env file, forwarded expanded"


def test_a_missing_default_config_still_starts_on_defaults(spawn, capsys) -> None:
    assert not default_config_path().exists()
    assert cli.main(["daemon", "start", "--url", DEAD]) == 0, capsys.readouterr().err
    assert _config_arg() is None
    assert "no such config file" not in capsys.readouterr().err


def test_restart_with_a_missing_config_refuses_before_stopping(spawn, tmp_path, capsys) -> None:
    spawn["running"] = {"version": "0", "uptime_s": 0.0, "ports": [], "pid": 1}
    _refused(capsys, ["daemon", "restart", "-c", str(tmp_path / "typo.toml")],
             tmp_path / "typo.toml")
    assert spawn["stops"] == [], "the running daemon was stopped for a start that cannot run"


def test_restart_refuses_when_the_running_daemons_config_is_gone(spawn, tmp_path,
                                                                capsys) -> None:
    gone = tmp_path / "deleted.toml"
    spawn["running"] = {"version": "0", "uptime_s": 0.0, "ports": [], "pid": 1,
                        "config_path": str(gone)}
    _refused(capsys, ["daemon", "restart"], gone)
    assert spawn["stops"] == []


def test_restart_of_a_daemon_on_the_missing_default_config_is_not_refused(spawn,
                                                                         capsys) -> None:
    """A daemon started without a config reports the default path as config_path; carrying
    it as --config turned "missing default means defaults" into a refusal."""
    assert not default_config_path().exists()
    spawn["running"] = {"version": "0", "uptime_s": 0.0, "ports": [], "pid": 1,
                        "config_path": str(default_config_path())}
    rc = cli.main(["daemon", "restart", "--url", DEAD])
    err = capsys.readouterr().err
    assert rc == 0, err
    assert "no such config file" not in err
    assert len(spawn["stops"]) == 1
    assert _config_arg() is None


def test_the_ai_guide_and_help_do_not_promise_a_warning() -> None:
    assert "warns if missing" not in cli.AI_GUIDE
    assert "no such config file" in cli.AI_GUIDE


@pytest.fixture
def fake_win(monkeypatch, tmp_path):
    calls: list[tuple] = []
    result = {"handle": 42}

    last_error = {"on": False}

    class CreateFileW:
        restype = argtypes = None

        def __call__(self, *args):
            calls.append(args)
            handle = result["handle"]
            if handle == wintypes.HANDLE(-1).value and self.restype is not wintypes.HANDLE:
                return -1       # ctypes' default c_int restype: INVALID_HANDLE_VALUE as -1
            return handle

    class K32:
        def __init__(self, use_last_error: bool) -> None:
            self.CreateFileW = CreateFileW()
            last_error["on"] = use_last_error

    backing = tmp_path / "backing.err"
    msvcrt = types.SimpleNamespace(
        open_osfhandle=lambda handle, flags: os.open(backing, os.O_WRONLY | os.O_CREAT
                                                     | os.O_APPEND))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "msvcrt", msvcrt)
    monkeypatch.setattr(ctypes, "WinDLL",
                        lambda name, use_last_error=False: K32(use_last_error), raising=False)
    # ERROR_ACCESS_DENIED is kept for get_last_error() only by a use_last_error=True DLL.
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5 if last_error["on"] else 0,
                        raising=False)
    monkeypatch.setattr(ctypes, "WinError",
                        lambda code: OSError(code, "access denied" if code == 5 else "no error"),
                        raising=False)
    return calls, result


def test_the_windows_open_asks_for_append_data_only(fake_win, tmp_path) -> None:
    calls, _ = fake_win
    fh = cli_daemonctl._open_append(str(tmp_path / "d.err"))
    fh.close()
    (path, access, share, _sa, disposition, _attrs, _tmpl), = calls
    assert path == str(tmp_path / "d.err")
    # FILE_APPEND_DATA | FILE_READ_ATTRIBUTES (os.fstat) | SYNCHRONIZE, no FILE_WRITE_DATA
    assert access == 0x0004 | 0x0080 | 0x00100000
    assert disposition == 4                     # OPEN_ALWAYS: never truncates
    assert share & 0x2                          # a racing start can open it too


def test_a_failed_windows_open_falls_back_to_no_log(fake_win, monkeypatch, capsys,
                                                    tmp_path) -> None:
    _, result = fake_win
    result["handle"] = wintypes.HANDLE(-1).value
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_status_body", lambda s, timeout=2.0: None)
    monkeypatch.setattr(cli, "_status_or_refusal", lambda s, timeout=2.0: (None, None, None))
    spawned: list[object] = []

    class Proc:
        pid = 999996

        def poll(self):
            return 1

    def popen(args, **kw):
        spawned.append(kw["stderr"])
        return Proc()

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    rc = cli.main(["--url", "http://127.0.0.1:1", "daemon", "start", "--timeout", "1"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "warning: cannot write the daemon log" in err and "access denied" in err
    assert spawned == [cli.subprocess.DEVNULL]


# -- FC-2 / FC-3: daemon start and restart ----------------------------------------------

class _FakeProc:
    """The spawned daemon: alive, with a pid the readiness probe can be made to report."""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.calls: list[str] = []

    def poll(self):
        return None

    def terminate(self) -> None:
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")

    def wait(self, timeout=None) -> int:
        self.calls.append("wait")
        return 0


# The start ids the fake spawns were handed, newest last; emptied by each _fake_spawn.
_SPAWNED_IDS: list[str] = []


def _fake_spawn(monkeypatch, tmp_path, pid: int = 4242, procs: list | None = None) -> list:
    """Record the argv of each `daemon start` spawn (and the process in `procs`); keep the
    pid record out of the user's data dir (nothing real is started)."""
    spawns: list = []
    _SPAWNED_IDS.clear()

    def popen(args, **kwargs):
        spawns.append(list(args))
        _SPAWNED_IDS.append(kwargs["env"]["MCUSCOPED_START_ID"])
        proc = _FakeProc(pid)
        if procs is not None:
            procs.append(proc)
        return proc

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    monkeypatch.setattr(cli, "_pid_file", lambda s: str(tmp_path / "mcuscoped.pid"))
    return spawns


def _phased(monkeypatch, phases: list) -> list:
    """Answer /status from `phases`, one entry per probe, the last repeating for ever.

    Each entry is (status_code, json body) or None for "nothing answered there".
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/status":
            return httpx.Response(200, json={"ports": []})
        entry = phases[min(calls["n"], len(phases) - 1)]
        calls["n"] += 1
        if entry is None:
            raise httpx.ConnectError("refused", request=request)
        code, body = entry
        # The last fake spawn's daemon answering: it echoes the id its start handed it.
        echo = {cli_client.START_ID_HEADER: _SPAWNED_IDS[-1]} if _SPAWNED_IDS else {}
        return httpx.Response(code, json=body, headers=echo)

    return record_params(monkeypatch, handler)


def test_restart_carries_a_config_path_outside_the_clis_cwd(
        monkeypatch, tmp_path, capsys) -> None:
    """The daemon reports its config path absolute, so a restart from another directory
    checks and forwards that file, not a same-named one under the CLI's cwd."""
    elsewhere = tmp_path / "elsewhere" / "mcuscoped.toml"
    elsewhere.parent.mkdir()
    elsewhere.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    running = {**STATUS, "config_path": str(elsewhere), "pid": 4242}
    _phased(monkeypatch, [(200, running), None, (200, {**running, "pid": 4242})])
    spawns = _fake_spawn(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_stop_daemon", lambda s, restarting=False: None)
    rc = cli.main([*UNREACHABLE, "daemon", "restart"])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    assert "no such config file" not in cap.err
    assert spawns, "nothing was spawned"
    assert spawns[0][spawns[0].index("--config") + 1] == str(elsewhere), spawns[0]


def test_start_still_refuses_a_config_the_user_typed_that_does_not_exist(
        monkeypatch, tmp_path, capsys) -> None:
    """Positive control for the above: _named_config still runs on a --config, and nothing
    is spawned."""
    _phased(monkeypatch, [None])
    spawns = _fake_spawn(monkeypatch, tmp_path)
    rc = cli.main([*UNREACHABLE, "daemon", "start", "--config", str(tmp_path / "gone.toml")])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "no such config file" in err
    assert spawns == [], spawns


def test_start_reports_a_daemon_it_spawned_that_answers_with_its_guard(
        monkeypatch, tmp_path, capsys) -> None:
    """The 401 is the daemon this command just started: a success, not a failed start."""
    _phased(monkeypatch, [None, (401, {"error": "guard-sentinel-YY"})])
    spawns = _fake_spawn(monkeypatch, tmp_path)
    rc = cli.main([*UNREACHABLE, "daemon", "start"])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    assert spawns, "nothing was spawned"
    assert "started mcuscoped (pid 4242)" in cap.out
    assert "requires a token" in cap.err and "guard-sentinel-YY" in cap.err
    assert "MCUSCOPE_TOKEN" in cap.err
    assert (tmp_path / "mcuscoped.pid").read_text(encoding="utf-8") == "4242"


def test_start_before_the_spawn_still_refuses_a_daemon_behind_a_guard(
        monkeypatch, tmp_path, capsys) -> None:
    """Positive control: the pre-spawn probe keeps the exit-1 refusal and spawns nothing,
    or a second daemon would die on the port."""
    _phased(monkeypatch, [(401, {"error": "guard-sentinel-YY"})])
    spawns = _fake_spawn(monkeypatch, tmp_path)
    rc = cli.main([*UNREACHABLE, "daemon", "start"])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "refused the request (HTTP 401): guard-sentinel-YY" in err
    assert spawns == [], spawns


def test_start_whose_daemon_never_answers_is_still_a_failed_start(
        monkeypatch, tmp_path, capsys) -> None:
    """Positive control for the refusal arm: silence is not an answer, and the spawned
    daemon is dealt with rather than reported as started."""
    _phased(monkeypatch, [None])
    procs: list = []
    _fake_spawn(monkeypatch, tmp_path, procs=procs)
    monkeypatch.setenv("MCUSCOPE_START_TIMEOUT", "0.5")
    rc = cli.main([*UNREACHABLE, "daemon", "start"])
    cap = capsys.readouterr()
    assert rc == 1, cap.err
    assert "did not come up" in cap.err and "; stopped it" in cap.err
    assert "started mcuscoped" not in cap.out
    assert [p.calls for p in procs] == [["terminate", "wait"]]


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
    path.write_text(str(dead_pid()), encoding="utf-8", newline="\n")
    assert _write_pid_record(str(path), 424242) is True
    assert path.read_text(encoding="utf-8") == "424242"
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]   # no .tmp left


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
    monkeypatch.setattr(cli, "_status_or_refusal",
                        lambda s, timeout=2.0: (status(s, timeout), None, None))

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
    from tests.support import child_env, free_port
    from tests.test_cli import MCU

    data_home = tmp_path / "not-a-dir"
    data_home.write_text("", encoding="utf-8", newline="\n")
    env = child_env(str(data_home), MCUSCOPE_URL=f"http://127.0.0.1:{free_port()}")
    r = subprocess.run(
        [*MCU, "daemon", "start", "--timeout", "0.05"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=60,
    )
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert "pid file" in r.stderr and "mcuscope" in r.stderr


# -- F14: the three one-liners ---------------------------------------------------------


def test_the_start_timeout_is_read_per_call(monkeypatch) -> None:
    """MCUSCOPE_START_TIMEOUT was read once at import, so it could not be varied between
    two runs in one interpreter - which is exactly how an environment variable is used."""
    from mcuscope import cli_daemonctl

    assert callable(cli_daemonctl.DAEMON_START_TIMEOUT_S), \
        "an import-time float freezes the environment variable"
    monkeypatch.setenv("MCUSCOPE_START_TIMEOUT", "7.5")
    assert cli_daemonctl.DAEMON_START_TIMEOUT_S() == 7.5
    monkeypatch.setenv("MCUSCOPE_START_TIMEOUT", "31")
    assert cli_daemonctl.DAEMON_START_TIMEOUT_S() == 31.0


class _Spawned(Exception):
    pass


def _answer_status(monkeypatch, code: int, **kw) -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, **kw)

    canned(monkeypatch, handler)

    def fake_popen(args, **kwargs):
        raise _Spawned(args)

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)


@pytest.mark.parametrize("code", [401, 403, 429])
@pytest.mark.parametrize("sub", ["status", "start"])
def test_a_daemon_refusing_the_probe_is_running_not_absent(monkeypatch, capsys, code, sub) -> None:
    _answer_status(monkeypatch, code, json={"error": "guard-sentinel-ZZ"})
    rc = cli.main([*UNREACHABLE, "daemon", sub])   # a _Spawned escaping here is the bug
    err = capsys.readouterr().err
    assert rc == 1, err
    assert f"refused the request (HTTP {code}): guard-sentinel-ZZ" in err


def test_a_stray_service_answering_403_is_still_not_mcuscoped(monkeypatch, capsys) -> None:
    """Positive control: a 403 without the daemon's error envelope is not our daemon."""
    _answer_status(monkeypatch, 403, json={"detail": "forbidden"})
    rc = cli.main([*UNREACHABLE, "daemon", "status"])
    err = capsys.readouterr().err
    assert rc == 3, err
    assert "refused the request" not in err
