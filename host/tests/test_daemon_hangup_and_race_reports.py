"""mcuscoped: SIGHUP shuts down like SIGTERM, and a start that loses the pid record to a
live daemon keys its startup reports by its own pid instead of overwriting that daemon's."""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from mcuscope import _stdio, pidfile
from mcuscope import daemon as daemon_mod
from tests.support import child_env, free_port

# -- LIFECYCLE-5: the start-race loser's reports -----------------------------------------


@pytest.fixture
def foreign_pid():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait(timeout=10)


def _record(host: str, port: int, pid: int) -> str:
    path = pidfile.pid_file_path(host, port)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(str(pid))
    return path


def test_report_key_follows_the_record_holder(foreign_pid) -> None:
    host, port = "127.0.0.1", free_port()
    own = f"{host}-{port}"
    assert daemon_mod._report_key(host, port, None) == own            # no record at all
    path = _record(host, port, foreign_pid)
    assert daemon_mod._report_key(host, port, path) == own            # we hold it
    assert daemon_mod._report_key(host, port, None) == f"{own}-{os.getpid()}"
    _record(host, port, os.getppid())   # the Windows launcher shim `daemon start` recorded
    assert daemon_mod._report_key(host, port, None) == own
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=10)
    _record(host, port, dead.pid)
    assert daemon_mod._report_key(host, port, None) == own


def test_report_key_survives_an_unusable_data_dir(monkeypatch) -> None:
    def refuse(_h: str, _p: int) -> str:
        raise PermissionError("data dir")

    monkeypatch.setattr(pidfile, "pid_file_path", refuse)
    assert daemon_mod._report_key("127.0.0.1", 8558, None) == "127.0.0.1-8558"


def test_a_start_that_loses_the_bind_race_leaves_the_winners_log(
    tmp_path: Path, monkeypatch, foreign_pid
) -> None:
    """The winner holds the record and is listening; the loser passed the port probe."""
    monkeypatch.setattr(_stdio, "_report_key", "")
    monkeypatch.setattr(daemon_mod, "_release_pid_on_terminating_signal", lambda _p: None)
    monkeypatch.setattr(daemon_mod, "_port_conflict", lambda _h, _p: None)
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    port = busy.getsockname()[1]
    record = _record("127.0.0.1", port, foreign_pid)
    data_dir = Path(record).parent
    winners = data_dir / f"mcuscoped-127.0.0.1-{port}-startup.log"
    winners.write_text(f"mcuscoped 0.0 started, pid {foreign_pid}\n", encoding="utf-8")
    cfg = tmp_path / "loser.toml"
    cfg.write_text(f'[storage]\ndb_path = "{(tmp_path / "loser.db").as_posix()}"\n',
                   encoding="utf-8", newline="\n")
    try:
        with pytest.raises(SystemExit) as exc:
            daemon_mod.main(["-c", str(cfg), "--port", str(port)])
    finally:
        busy.close()
    assert exc.value.code == 3
    # Positive control: this start did write its failure, under its own pid.
    loser = data_dir / f"mcuscoped-127.0.0.1-{port}-{os.getpid()}-startup.log"
    assert f"failed to start, pid {os.getpid()}, exit 3" in loser.read_text(encoding="utf-8")
    assert winners.read_text(encoding="utf-8") == f"mcuscoped 0.0 started, pid {foreign_pid}\n"
    assert pidfile.read_pid_record(record) == foreign_pid


# -- LIFECYCLE-6: SIGHUP --------------------------------------------------------------------

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="SIGHUP is POSIX-only")


def _spawn(tmp_path: Path, **popen_kw):
    port = free_port()
    db = tmp_path / "cap.db"
    cfg = tmp_path / "hup.toml"
    cfg.write_text(f'[storage]\ndb_path = "{db.as_posix()}"\n', encoding="utf-8", newline="\n")
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcuscope.daemon", "-c", str(cfg), "--port", str(port)],
        env=child_env(str(tmp_path)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **popen_kw,
    )
    url = f"http://127.0.0.1:{port}/status"
    deadline = time.monotonic() + 20
    while True:
        assert proc.poll() is None, "daemon exited during startup"
        assert time.monotonic() < deadline, "daemon never answered /status"
        try:
            if httpx.get(url, timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    pid_file = tmp_path / "mcuscope" / f"mcuscoped-127.0.0.1-{port}.pid"
    assert pid_file.read_text(encoding="utf-8") == str(proc.pid)
    return proc, url, pid_file, db


def _sys_rows(db: Path) -> list[str]:
    with contextlib.closing(sqlite3.connect(db)) as conn:
        return [r[0] for r in conn.execute(
            "SELECT raw FROM lines WHERE chan = 'sys' AND port = '' ORDER BY id")]


@posix_only
def test_sighup_shuts_down_gracefully(tmp_path: Path) -> None:
    proc, _url, pid_file, db = _spawn(tmp_path)
    try:
        proc.send_signal(signal.SIGHUP)
        assert proc.wait(timeout=15) == -signal.SIGTERM
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    assert not pid_file.exists()
    rows = _sys_rows(db)
    assert "daemon start" in rows, rows     # positive control for the query
    assert rows[-1] == "daemon stop", rows


@posix_only
def test_an_ignored_sighup_stays_ignored(tmp_path: Path) -> None:
    """`nohup mcuscoped`: the hangup the user asked to survive is not turned into a stop."""
    proc, url, pid_file, _db = _spawn(
        tmp_path, preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN))
    try:
        proc.send_signal(signal.SIGHUP)
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=2)
        assert httpx.get(url, timeout=2).status_code == 200
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=15) == -signal.SIGTERM
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    assert not pid_file.exists()
