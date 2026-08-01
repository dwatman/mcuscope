"""Tests for mcuscope.pidfile: the daemon-side pid record.

Regression coverage for the unstoppable-daemon bug: only `mcu daemon start` wrote
the pid file, so a daemon launched as plain `mcuscoped` could not be stopped with
`mcu daemon stop` - which, on a windowless Windows interpreter with no Ctrl-C,
left no stop path at all.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from mcuscope import pidfile


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "platformdirs.user_data_dir", lambda app: str(tmp_path / "data" / app)
    )
    return tmp_path / "data" / "mcuscope"


def test_pid_file_path_is_keyed_and_filename_safe(data_dir):
    path = pidfile.pid_file_path("127.0.0.1", 8765)
    assert path == str(data_dir / "mcuscoped-127.0.0.1-8765.pid")
    # An IPv6 literal must key a file too: colons cannot appear in the name.
    assert ":" not in os.path.basename(pidfile.pid_file_path("::1", 8765))


def test_claim_writes_own_pid_and_release_removes_it(data_dir):
    path = pidfile.claim("127.0.0.1", 8770)
    assert path is not None
    with open(path, encoding="utf-8") as fh:
        assert int(fh.read()) == os.getpid()
    pidfile.release(path)
    assert not os.path.exists(path)


def test_claim_does_not_clobber_a_live_record(data_dir):
    """`mcu daemon start` records the launcher pid it spawned; the daemon must not
    replace a live record with its own (on Windows that pid is the CTRL_BREAK
    process-group id, and replacing it downgrades a graceful stop to a kill)."""
    path = pidfile.pid_file_path("127.0.0.1", 8771)
    live = os.getppid()  # a real, running process that is not us
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(str(live))

    assert pidfile.claim("127.0.0.1", 8771) is None
    with open(path, encoding="utf-8") as fh:
        assert int(fh.read()) == live  # untouched


def test_claim_overwrites_a_stale_record(data_dir):
    path = pidfile.pid_file_path("127.0.0.1", 8772)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("999999999")  # far beyond any real pid space

    assert pidfile.claim("127.0.0.1", 8772) == path
    with open(path, encoding="utf-8") as fh:
        assert int(fh.read()) == os.getpid()


def test_claim_leaves_alone_a_live_record_that_is_not_our_parent(data_dir):
    """A live record is never taken over, whoever owns it.

    This used to overwrite, on the argument that a live pid which is neither us nor our
    parent must be a recycled pid in a crashed daemon's record. The port probe cannot
    support that argument: it closes long before either daemon binds, so two daemons with
    different db_path (the capture lock does not stop the second) on one host:port both
    pass it. The loser of the bind race then took the winner's record on the way in and
    deleted it on the way out, leaving a live daemon `mcu daemon stop` could not find.

    The recycled-pid case is covered from the other side instead: `mcu daemon stop` acts
    on the pid /status reports rather than the recorded one, and signals nothing when no
    daemon answers, so it neither misses the live daemon nor kills the innocent process
    wearing its old pid.
    """
    path = pidfile.pid_file_path("127.0.0.1", 8776)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(str(child.pid))

        assert pidfile.claim("127.0.0.1", 8776) is None
        with open(path, encoding="utf-8") as fh:
            assert int(fh.read()) == child.pid  # untouched
        # And the unrecorded daemon's release() must not delete the record either.
        pidfile.release(path)
        assert os.path.exists(path)
    finally:
        child.kill()
        child.wait(timeout=10)


def test_claim_twice_from_the_same_process(data_dir):
    """A reclaim of our own record (e.g. after a restart on the same key) must not
    trip over the O_EXCL create: the first file is ours, so it is removed and
    recreated."""
    path = pidfile.claim("127.0.0.1", 8777)
    assert path is not None
    assert pidfile.claim("127.0.0.1", 8777) == path
    with open(path, encoding="utf-8") as fh:
        assert int(fh.read()) == os.getpid()


def test_claim_reclaims_our_own_record(data_dir):
    """POSIX `daemon start`: Popen pid == the daemon's own pid, so the record the
    parent just wrote is ours to keep owning (and to remove on exit)."""
    path = pidfile.pid_file_path("127.0.0.1", 8773)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(str(os.getpid()))

    assert pidfile.claim("127.0.0.1", 8773) == path


def test_release_keeps_a_record_someone_else_rewrote(data_dir):
    path = pidfile.claim("127.0.0.1", 8774)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("12345")
    pidfile.release(path)
    assert os.path.exists(path)
    pidfile.release(None)  # no claim: must be a no-op, not an error


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM is TerminateProcess on Windows")
def test_daemon_releases_pid_file_on_sigterm(tmp_path):
    """uvicorn replays a handled SIGTERM with the default disposition after its
    graceful shutdown, so the process dies inside uvicorn.run and main()'s finally
    never runs - the pre-installed handler must release the pid record instead."""
    from tests.support import free_port

    port = free_port()
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path),
        "MCUSCOPED_CONFIG": "",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcuscope.daemon", "--port", str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    pid_file = tmp_path / "mcuscope" / f"mcuscoped-127.0.0.1-{port}.pid"
    startup_log = tmp_path / "mcuscope" / "mcuscoped-startup.log"
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            assert proc.poll() is None, "daemon exited before writing its pid file"
            # Readiness is the *content*, not file existence: open("w") creates the
            # file before anything lands in it, so an existence check can win the
            # race against a partial write.
            try:
                ready = (
                    pid_file.read_text(encoding="utf-8") == str(proc.pid)
                    and "to stop" in startup_log.read_text(encoding="utf-8")
                )
            except OSError:
                ready = False
            if ready:
                break
            time.sleep(0.05)
        assert pid_file.read_text(encoding="utf-8") == str(proc.pid)
        assert "to stop" in startup_log.read_text(encoding="utf-8")

        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=15) == -signal.SIGTERM
        assert not pid_file.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_pid_running_probes_without_signalling():
    assert pidfile.pid_running(os.getpid()) is True
    assert pidfile.pid_running(999999999) is False
    assert pidfile.pid_running(0) is False
    assert pidfile.pid_running(-1) is False
