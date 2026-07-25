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

from mcuscope.daemon import build_parser
from mcuscope.lockfile import CaptureLock, LockError


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


def test_lock_is_reusable_once_released(tmp_path) -> None:
    db = str(tmp_path / "capture.db")
    with CaptureLock(db):
        pass
    with CaptureLock(db):   # no manual cleanup needed between runs
        pass


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
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True,
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


def test_daemon_exposes_the_override_flag() -> None:
    args = build_parser().parse_args(["--ignore-capture-lock"])
    assert args.ignore_capture_lock is True
    assert build_parser().parse_args([]).ignore_capture_lock is False
