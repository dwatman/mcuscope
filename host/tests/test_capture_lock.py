"""The single-writer guard on a capture database (SPEC 3.2).

Two daemons on one capture collide on `lines.id`, and neither the listening port nor a
pid file is a sound guard: ports do not cover two daemons with different ports and one
db_path, and a pid file survives the process that wrote it. These tests pin the behaviour
that makes an OS lock the right choice - it is released however the holder dies - and the
two escape hatches around it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

import pytest

from mcuscope import daemon as daemon_mod
from mcuscope.lockfile import CaptureLock, LockError
from tests.support import CHILD_TEXT, free_port


def test_second_holder_is_refused_and_told_who_has_it(tmp_path) -> None:
    db = str(tmp_path / "capture.db")
    first = CaptureLock(db)
    first.acquire()
    try:
        with pytest.raises(LockError) as exc:
            CaptureLock(db).acquire(timeout=0)
        assert db in str(exc.value)
        assert exc.value.holder is not None
        assert exc.value.holder["pid"] == os.getpid()
        # The message has to point somewhere useful, not just say "no".
        assert "--ignore-capture-lock" in str(exc.value)
    finally:
        first.release()


def test_a_holder_record_that_cannot_be_read_still_refuses(tmp_path) -> None:
    # The lock is the guard; the holder details are diagnostics. An unreadable or
    # part-written record degrades the message, never the refusal.
    assert "held by pid" not in str(LockError(str(tmp_path / "a.db"), None))
    vague = str(LockError(str(tmp_path / "a.db"), {"pid": 7, "host": "h", "started": "soon"}))
    assert "held by pid 7" in vague and "an unknown time" in vague


def test_acquire_creates_the_data_dir_it_locks_in(tmp_path) -> None:
    # First run on a fresh machine: the platformdirs data dir does not exist yet.
    lock = CaptureLock(str(tmp_path / "fresh" / "nested" / "capture.db"))
    lock.acquire()
    try:
        assert os.path.exists(lock.path)
    finally:
        lock.release()


def test_separate_captures_do_not_contend(tmp_path) -> None:
    # Two setups (`mcuscoped --config other.toml`) are supported and must not block
    # each other: the invariant is one writer per database, not one daemon per machine.
    a, b = CaptureLock(str(tmp_path / "a.db")), CaptureLock(str(tmp_path / "b.db"))
    a.acquire()
    b.acquire()
    a.release()
    b.release()


def test_a_killed_holder_leaves_nothing_to_clean_up(tmp_path) -> None:
    # The whole reason this is an OS lock rather than a pid file. The child is killed
    # outright, so it runs no cleanup of its own.
    db = str(tmp_path / "capture.db")
    script = textwrap.dedent(
        f"""
        import time
        from mcuscope.lockfile import CaptureLock
        CaptureLock({db!r}).acquire()
        print("locked", flush=True)
        time.sleep(60)
        """
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, **CHILD_TEXT,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)},
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(LockError):
            CaptureLock(db).acquire(timeout=0)
        child.kill()
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)

    # No manual recovery step: the kernel dropped the lock when the process died. The
    # retry window absorbs the moment the OS takes to reap it.
    taken = CaptureLock(db)
    taken.acquire(timeout=5.0)
    taken.release()


def test_lock_file_survives_release_without_holding_anything(tmp_path) -> None:
    db = str(tmp_path / "capture.db")
    lock = CaptureLock(db)
    lock.acquire()
    lock.release()
    assert os.path.exists(lock.path), "the file stays; only the lock is dropped"
    # Only the lock byte is left: a departed holder must not be reported as the current
    # one by the next daemon that fails to take the lock.
    assert os.path.getsize(lock.path) == 1, "the released holder's details are still there"
    # A leftover file is not a leftover lock, which is exactly the pid-file failure this
    # design avoids.
    again = CaptureLock(db)
    again.acquire(timeout=0)
    again.release()


def test_retry_window_waits_for_a_departing_holder(tmp_path) -> None:
    # The realistic stuck case is a restart racing its predecessor's shutdown, not a crash.
    import threading

    db = str(tmp_path / "capture.db")
    first = CaptureLock(db)
    first.acquire()
    threading.Timer(0.3, first.release).start()

    started = time.monotonic()
    second = CaptureLock(db)
    second.acquire(timeout=5.0)
    second.release()
    assert time.monotonic() - started >= 0.25, "it should have waited, not walked in"


def test_a_refused_acquire_does_not_leak_its_descriptor(tmp_path, monkeypatch) -> None:
    # acquire() opens the lock file before it knows whether it can lock it. The retry in
    # daemon startup, and every `mcuscoped` a user restarts against a running one, would
    # otherwise walk the process towards its descriptor limit.
    db = str(tmp_path / "capture.db")
    holder = CaptureLock(db)
    holder.acquire()
    real_open, real_close = os.open, os.close
    open_paths: dict[int, str] = {}

    def tracking_open(p, *args, **kw):
        fd = real_open(p, *args, **kw)
        open_paths[fd] = os.fspath(p)
        return fd

    def tracking_close(fd):
        open_paths.pop(fd, None)
        return real_close(fd)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "close", tracking_close)
    try:
        with pytest.raises(LockError):
            CaptureLock(db).acquire(timeout=0)
        assert holder.path not in open_paths.values(), "the refused acquire kept its fd"
    finally:
        holder.release()


# -- the daemon's use of the lock ---------------------------------------------------------
#
# The class is well covered above; what was not covered at all is that `mcuscoped` consults
# it. Replacing the acquire in daemon.main with `pass` left the whole suite green, so SPEC
# 3.2's single-writer guard rested on nothing.


@pytest.fixture
def daemon_run(tmp_path, monkeypatch):
    """Run daemon.main() up to the point it would serve, and report whether it got there.

    An absent config path, so the daemon takes its defaults: the db_path is then the
    patched data dir's, and no TOML has to be written (or escaped, on Windows).
    """
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app: str(tmp_path / "data"))
    # main() keys the startup log by host:port; restore it, or the next test in the
    # session writes its reports under this one's key.
    monkeypatch.setattr("mcuscope._stdio._report_key", "")
    served: list[dict] = []
    monkeypatch.setattr(daemon_mod.uvicorn, "run", lambda *a, **kw: served.append(kw))
    absent_cfg = str(tmp_path / "no-such-config.toml")

    def run(*argv: str) -> tuple[int, bool]:
        argv = ("-c", absent_cfg, "--port", str(free_port()), *argv)
        return daemon_mod.main(list(argv)), bool(served)

    run.db = str(tmp_path / "data" / "capture.db")      # type: ignore[attr-defined]
    return run


def test_a_second_daemon_on_one_capture_is_refused_before_it_serves(daemon_run, capsys) -> None:
    held = CaptureLock(daemon_run.db)
    held.acquire()
    try:
        rc, served = daemon_run()
    finally:
        held.release()
    err = capsys.readouterr().err   # startup refusals go to stderr, not stdout
    assert rc == 1, "the second daemon started on a capture that was already owned"
    assert not served, "it reached uvicorn with someone else's capture"
    assert "already in use" in err and "--ignore-capture-lock" in err


def test_the_override_downgrades_the_refusal_to_a_warning(daemon_run, capsys) -> None:
    held = CaptureLock(daemon_run.db)
    held.acquire()
    try:
        rc, served = daemon_run("--ignore-capture-lock")
    finally:
        held.release()
    err = capsys.readouterr().err   # the override's warning rides the same stream
    assert (rc, served) == (0, True), "the override did not get the daemon past the lock"
    assert "WARNING" in err and "collide on row ids" in err


def test_a_daemon_that_started_first_releases_the_lock_on_the_way_out(daemon_run) -> None:
    rc, served = daemon_run()
    assert (rc, served) == (0, True)
    # Nothing to clean up by hand: the next daemon takes the capture straight away.
    after = CaptureLock(daemon_run.db)
    after.acquire(timeout=0)
    after.release()
