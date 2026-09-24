"""`mcu daemon stop` signals only a pid a local record names; `daemon start`'s stderr file.

SPEC 4: the pid /status reports can belong to another machine (a remote --url, a tunnelled
loopback port), so with no local record the stop is POST /shutdown alone.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import types
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import typer

from mcuscope import cli, cli_daemonctl
from mcuscope.cli_client import Settings
from mcuscope.pidfile import pid_running
from tests.support import dead_pid
from tests.test_cli import _PIDDIR_ENV_SKIP, _run_mcu_data_home, _write_pid_record

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="signals a POSIX child")


class _FakeDaemon:
    """/status naming `pid` while `alive()`; /shutdown refused (403) or accepted."""

    def __init__(self, pid: int, alive: Callable[[], bool], accept: bool) -> None:
        state = {"alive": alive}
        self.state = state

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def _send(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if self.path == "/status" and state["alive"]():
                    self._send(200, {"version": "9.9.9", "uptime_s": 1, "ports": [],
                                     "pid": pid})
                else:
                    self._send(404, {"error": "gone"})

            def do_POST(self) -> None:
                if not accept:
                    self._send(403, {"error": "shutdown is a local operation"})
                    return
                state["alive"] = lambda: False
                self._send(200, {"ok": True})

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def victim():
    """A local process whose pid the fake daemon reports; reaped as soon as it dies."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    reaper = threading.Thread(target=proc.wait, daemon=True)
    reaper.start()
    yield proc
    proc.kill()
    reaper.join(5)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli_daemonctl, "DAEMON_STOP_GRACE_S", 1.0)
    return tmp_path


def _stop(url: str) -> int:
    return cli.main(["--url", url, "daemon", "stop"])


@posix_only
def test_no_record_and_a_refused_shutdown_signals_nothing(victim, data_dir, capsys) -> None:
    fake = _FakeDaemon(victim.pid, lambda: True, accept=False)
    try:
        rc = _stop(fake.url)
    finally:
        fake.close()
    err = capsys.readouterr().err
    assert rc == 1
    assert "no local pid record names it, so no process was signalled" in err
    assert victim.poll() is None, "a process this machine does not know was signalled"


@posix_only
def test_no_record_and_an_accepted_shutdown_is_judged_by_status(victim, data_dir,
                                                               capsys) -> None:
    fake = _FakeDaemon(victim.pid, lambda: True, accept=True)
    try:
        rc = _stop(fake.url)
    finally:
        fake.close()
    out = capsys.readouterr().out
    assert rc == 0
    assert "no local pid record: asked it to shut down, signalled nothing" in out
    assert victim.poll() is None


@posix_only
def test_a_record_naming_the_pid_still_lets_the_signal_fallback_work(victim, data_dir,
                                                                    capsys) -> None:
    """Positive control: the same refused shutdown, with a local record, stops the pid."""
    fake = _FakeDaemon(victim.pid, lambda: victim.poll() is None, accept=False)
    try:
        record = cli_daemonctl._pid_file(Settings(url=fake.url, json_out=False, port=None))
        with open(record, "w", encoding="utf-8", newline="") as fh:
            fh.write(str(victim.pid))
        rc = _stop(fake.url)
    finally:
        fake.close()
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert f"stopped mcuscoped (pid {victim.pid})" in out
    assert victim.wait(5) is not None
    assert not os.path.exists(record)


def test_a_daemon_still_answering_after_the_stop_is_reported(monkeypatch, capsys) -> None:
    """HEALTH-15 D01: the stop "worked" but something still serves the URL."""
    monkeypatch.setattr(cli_daemonctl, "_request_shutdown", lambda s: True)
    monkeypatch.setattr(cli_daemonctl, "_wait_daemon_gone", lambda s, pid, t: True)
    monkeypatch.setattr(cli_daemonctl, "_status_body", lambda s, timeout=2.0: {"version": "9"})
    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)
    with pytest.raises(typer.Exit) as ei:
        cli_daemonctl._stop_running_daemon(s, {"pid": 4242})
    out, err = capsys.readouterr()
    assert ei.value.exit_code == 1
    assert "still answering" in err and "stopped" not in out


def _write_record(url: str, pid: int) -> str:
    record = cli_daemonctl._pid_file(Settings(url=url, json_out=False, port=None))
    with open(record, "w", encoding="utf-8", newline="") as fh:
        fh.write(str(pid))
    return record


@posix_only
def test_a_stale_record_naming_a_live_unrelated_pid_is_not_signalled(victim, data_dir,
                                                                    capsys) -> None:
    """A crashed daemon's record names a recycled pid; another daemon serves the URL."""
    fake = _FakeDaemon(4000000, lambda: True, accept=True)
    try:
        record = _write_record(fake.url, victim.pid)
        rc = _stop(fake.url)
    finally:
        fake.close()
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert (f"its pid record named pid {victim.pid}, not the serving process: asked it to "
            "shut down, signalled nothing") in out
    assert victim.poll() is None, "an unrelated process named by a stale record was signalled"
    assert not os.path.exists(record)


@posix_only
def test_a_stale_record_and_a_refused_shutdown_signals_nothing(victim, data_dir,
                                                              capsys) -> None:
    fake = _FakeDaemon(4000000, lambda: True, accept=False)
    try:
        _write_record(fake.url, victim.pid)
        rc = _stop(fake.url)
    finally:
        fake.close()
    err = capsys.readouterr().err
    assert rc == 1
    assert (f"names pid {victim.pid}, which is not the process serving it, so no process "
            "was signalled") in err
    assert victim.poll() is None


def test_a_record_naming_a_dead_pid_waits_for_status_to_go_quiet(data_dir, capsys) -> None:
    """The stop is judged on /status, not on a stale record's pid being gone at once."""
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    until = [float("inf")]
    fake = _FakeDaemon(4000000, lambda: time.monotonic() < until[0], accept=False)

    def slow_shutdown(handler) -> None:     # accepted; /status answers 0.4 s longer
        until[0] = time.monotonic() + 0.4
        handler._send(200, {"ok": True})

    fake.httpd.RequestHandlerClass.do_POST = slow_shutdown
    try:
        _write_record(fake.url, dead.pid)
        rc = _stop(fake.url)
    finally:
        fake.close()
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert f"its pid record named pid {dead.pid}" in out


def test_the_launcher_parent_corroborates_a_record_only_on_windows(monkeypatch) -> None:
    body = {"pid": 5, "ppid": 7}
    monkeypatch.setattr(sys, "platform", "linux")
    assert cli_daemonctl._serving_pids(body) == {5}
    monkeypatch.setattr(sys, "platform", "win32")
    assert cli_daemonctl._serving_pids(body) == {5, 7}
    assert cli_daemonctl._serving_pids({"version": "0.1.0"}) == set()


# -- daemon start: the shared stderr file, and --timeout below half a second ---------------


class _Proc:
    def __init__(self, pid: int, exited: int | None) -> None:
        self.pid, self._exited = pid, exited

    def poll(self):
        return self._exited

    def terminate(self) -> None:
        self._exited = -15

    def wait(self, timeout=None):
        return self._exited

    def kill(self) -> None:
        self._exited = -9


def test_a_start_appends_to_the_stderr_file_and_shows_only_its_own_lines(
    data_dir, monkeypatch, capsys,
) -> None:
    """Two starts racing for one host:port share the file; neither may wipe the other's."""
    url = "http://127.0.0.1:1"
    err_path = cli_daemonctl._stderr_log_path(
        cli_daemonctl._pid_file(Settings(url=url, json_out=False, port=None)))
    with open(err_path, "w", encoding="utf-8", newline="") as fh:
        fh.write("the serving daemon's warning\n")

    def spawn(args, **kw):
        os.write(kw["stderr"].fileno(), b"this start's own error\n")
        return _Proc(999999, exited=3)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    rc = cli.main(["--url", url, "daemon", "start", "--timeout", "5"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "this start's own error" in err
    assert "the serving daemon's warning" not in err
    with open(err_path, encoding="utf-8") as fh:
        assert fh.read() == "the serving daemon's warning\nthis start's own error\n"


def test_start_timeout_below_half_a_second_is_honoured(data_dir, monkeypatch, capsys) -> None:
    """HEALTH-15 C11: `--timeout 0.05` waits 0.05 s of readiness, not a hidden 0.5 s floor.

    The clock is the test's, advanced only by the loop's own sleep, so the number of
    readiness probes is the measurement.
    """
    clock = [100.0]
    probes = [0]

    def probe(s, timeout=2.0):
        probes[0] += 1
        return None, None

    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cli.time, "sleep", lambda sec: clock.__setitem__(0, clock[0] + sec))
    monkeypatch.setattr(cli, "_status_or_refusal", probe)
    monkeypatch.setattr(cli, "_status_body", lambda s, timeout=2.0: None)
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: _Proc(999998, exited=None))
    rc = cli.main(["--url", "http://127.0.0.1:1", "daemon", "start", "--timeout", "0.05"])
    assert rc == 1
    assert probes[0] == 1, f"{probes[0]} readiness probes for a 0.05 s wait"
    assert "did not come up" in capsys.readouterr().err


def _start_answered_by(monkeypatch, body: dict, shim: int = 999997) -> int:
    """`daemon start` whose Popen pid is `shim` and whose readiness probe answers `body`."""
    monkeypatch.setattr(cli, "_status_body", lambda s, timeout=2.0: None)
    monkeypatch.setattr(cli, "_status_or_refusal", lambda s, timeout=2.0: (body, None))
    monkeypatch.setattr(cli, "_open_append", lambda path: open(path, "ab"))  # noqa: SIM115
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: _Proc(shim, exited=None))
    return cli.main(["--url", "http://127.0.0.1:1", "daemon", "start", "--timeout", "5"])


_STATUS = {"version": "9.9.9", "uptime_s": 0, "ports": []}


def test_a_windows_venv_start_is_answered_by_the_shims_child(data_dir, monkeypatch,
                                                            capsys) -> None:
    """Finding 10: the venv redirector is proc.pid, the daemon its child (`ppid`)."""
    monkeypatch.setattr(sys, "platform", "win32")
    rc = _start_answered_by(monkeypatch, {**_STATUS, "pid": 4242, "ppid": 999997})
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert "started mcuscoped (pid 999997)" in out


@pytest.mark.parametrize("platform, body", [
    ("win32", {**_STATUS, "pid": 4242}),                   # an older daemon: no ppid
    ("win32", {**_STATUS, "pid": 4242, "ppid": 1}),        # another daemon's parent
    ("linux", {**_STATUS, "pid": 4242, "ppid": 999997}),   # no launcher shim off Windows
])
def test_a_start_answered_by_another_process_still_fails(data_dir, monkeypatch, capsys,
                                                         platform, body) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    rc = _start_answered_by(monkeypatch, body)
    assert rc == 1
    assert "another daemon is already serving at http://127.0.0.1:1 (pid 4242)" in \
        capsys.readouterr().err


DAEMON_PID = 4000000


def _ppid_daemon(ppid_of, alive, accept: bool) -> _FakeDaemon:
    """A fake whose nth /status (from 1) reports pid DAEMON_PID and ppid `ppid_of(n)`."""
    fake = _FakeDaemon(DAEMON_PID, alive, accept=accept)
    orig_get = fake.httpd.RequestHandlerClass.do_GET
    n = [0]

    def do_get(handler) -> None:
        if handler.path == "/status" and fake.state["alive"]():
            n[0] += 1
            handler._send(200, {"version": "9.9.9", "uptime_s": 1, "ports": [],
                                "pid": DAEMON_PID, "ppid": ppid_of(n[0])})
        else:
            orig_get(handler)

    fake.httpd.RequestHandlerClass.do_GET = do_get
    return fake


@pytest.fixture
def win32(monkeypatch):
    monkeypatch.setattr(cli_daemonctl, "sys", types.SimpleNamespace(platform="win32"))


def test_the_parent_of_a_daemon_that_shut_down_is_not_signalled(win32, victim, data_dir,
                                                                capsys) -> None:
    """The recorded pid is the daemon's parent (a cmd.exe it was run from), which never
    exits; the daemon accepts /shutdown and goes."""
    fake = _ppid_daemon(lambda n: victim.pid, lambda: True, accept=True)
    try:
        record = _write_record(fake.url, victim.pid)
        rc = _stop(fake.url)
    finally:
        fake.close()
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert f"stopped mcuscoped (pid {victim.pid})" in out
    assert victim.poll() is None, "the daemon's parent was signalled after it had gone"
    assert not os.path.exists(record)


@posix_only
def test_a_parent_status_still_names_after_the_grace_is_signalled(win32, victim, data_dir,
                                                                  capsys) -> None:
    """Positive control: the daemon refuses /shutdown and keeps naming the parent, which is
    then signalled (and the daemon, a shim's child, goes with it)."""
    fake = _ppid_daemon(lambda n: victim.pid, lambda: victim.poll() is None, accept=False)
    try:
        _write_record(fake.url, victim.pid)
        rc = _stop(fake.url)
    finally:
        fake.close()
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert victim.wait(5) is not None
    assert f"stopped mcuscoped (pid {victim.pid})" in out


def test_a_parent_status_stops_naming_before_the_signal_is_spared(win32, victim, data_dir,
                                                                  monkeypatch, capsys) -> None:
    """Corroborated when the stop began, not at the moment of signalling."""
    monkeypatch.setattr(cli_daemonctl, "_wait_daemon_gone", lambda s, pid, t: False)
    fake = _ppid_daemon(lambda n: victim.pid if n == 1 else 1, lambda: True, accept=False)
    try:
        _write_record(fake.url, victim.pid)
        rc = _stop(fake.url)
    finally:
        fake.close()
    err = capsys.readouterr().err
    assert rc == 1
    assert f"names pid {victim.pid}, which is not the process serving it" in err
    assert "no process was signalled" in err
    assert victim.poll() is None


def test_a_daemon_gone_by_the_moment_of_signalling_spares_its_parent(win32, victim,
                                                                     data_dir, monkeypatch,
                                                                     capsys) -> None:
    """The grace ran out, but /status is quiet by the re-check: stopped, nothing signalled."""
    monkeypatch.setattr(cli_daemonctl, "_wait_daemon_gone", lambda s, pid, t: False)
    probes: list[int] = []
    fake = _ppid_daemon(lambda n: victim.pid,
                        lambda: probes.append(0) or len(probes) == 1, accept=False)
    try:
        _write_record(fake.url, victim.pid)
        rc = _stop(fake.url)
    finally:
        fake.close()
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert f"stopped mcuscoped (pid {victim.pid})" in out
    assert victim.poll() is None


def test_restart_starts_only_once_the_launcher_has_exited(win32, data_dir, monkeypatch,
                                                          capsys) -> None:
    """/status goes quiet before the old daemon releases its capture lock; the launcher
    exits only after its child has, so the new start waits for it."""
    shim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    reaper = threading.Thread(target=shim.wait, daemon=True)
    reaper.start()
    seen: list[bool] = []
    monkeypatch.setattr(cli, "_start_daemon",
                        lambda ctx, config, sim, wait_s, open_ui:
                        seen.append(pid_running(shim.pid)))
    monkeypatch.setattr(cli_daemonctl, "DAEMON_STOP_GRACE_S", 10.0)
    fake = _ppid_daemon(lambda n: shim.pid, lambda: True, accept=True)
    try:
        _write_record(fake.url, shim.pid)
        rc = cli.main(["--url", fake.url, "daemon", "restart"])
    finally:
        fake.close()
        shim.kill()
        reaper.join(5)
    assert rc == 0, capsys.readouterr().err
    assert seen == [False], "the new daemon was started while the old launcher still ran"


# -- F7 / class 7: the stale-record stop path says what it did ---------------------------


@_PIDDIR_ENV_SKIP
def test_daemon_stop_reports_removing_a_stale_pid_record(tmp_path) -> None:
    """No daemon answering and the recorded pid gone: remove the record and say so."""
    from tests.test_cli import _child_data_dir

    dead = dead_pid()
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
