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
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import typer

from mcuscope import cli, cli_daemonctl
from mcuscope.cli_client import Settings

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
        cli_daemonctl._stop_running_daemon(s, None, 4242)
    out, err = capsys.readouterr()
    assert ei.value.exit_code == 1
    assert "still answering" in err and "stopped" not in out


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
