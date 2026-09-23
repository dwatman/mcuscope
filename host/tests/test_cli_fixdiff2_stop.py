"""A record corroborated only as the daemon's Windows `ppid` names its parent, which is a
launcher shim only by assumption: `daemon stop` must not signal it once the daemon has gone,
and `daemon restart` waits for it to exit before starting the new daemon.

Windows is simulated by patching cli_daemonctl's `sys`; the stand-in parent is a local
sleeper this test started."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import types

import pytest

from mcuscope import cli, cli_daemonctl
from mcuscope.pidfile import pid_running
from tests import test_cli_daemon_stop_scope as scope
from tests.test_cli_daemon_stop_scope import _FakeDaemon, _stop, _write_record, posix_only

victim, data_dir = scope.victim, scope.data_dir   # fixtures

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
