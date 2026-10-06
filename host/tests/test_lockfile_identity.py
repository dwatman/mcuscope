"""CaptureLock identity (symlinks, replaced lock file), memory captures and POSIX modes."""

from __future__ import annotations

import os
import stat

import pytest

from mcuscope import daemon as daemon_mod
from mcuscope import pidfile
from mcuscope.dirs import make_private_dirs
from mcuscope.lockfile import CaptureLock, LockError, LockLost

posix = pytest.mark.skipif(os.name != "posix", reason="POSIX modes and symlinks")


@posix
def test_a_symlinked_spelling_of_the_capture_is_refused(tmp_path) -> None:
    real = tmp_path / "real.db"
    link = tmp_path / "link.db"
    link.symlink_to(real)
    first = CaptureLock(str(real))
    first.acquire()
    try:
        with pytest.raises(LockError):
            CaptureLock(str(link)).acquire(timeout=0)
    finally:
        first.release()


@posix
def test_acquire_retries_when_the_lock_file_was_swapped_under_it(tmp_path, monkeypatch) -> None:
    from mcuscope import lockfile

    lock = CaptureLock(str(tmp_path / "c.db"))
    real_try = lockfile._try_lock
    calls = []

    def swap_once(fd: int) -> None:
        real_try(fd)
        calls.append(fd)
        if len(calls) == 1:
            os.unlink(lock.path)   # a holder removed the file after our open

    monkeypatch.setattr(lockfile, "_try_lock", swap_once)
    lock.acquire()
    try:
        assert len(calls) == 2   # the swapped file was dropped and the lock retaken
        assert lock._is_current(lock._fd)
        lock.verify()
    finally:
        lock.release()


@posix
def test_verify_raises_once_the_lock_file_is_removed_or_replaced(tmp_path) -> None:
    lock = CaptureLock(str(tmp_path / "c.db"))
    CaptureLock(str(tmp_path / "other.db")).verify()   # not held: no-op
    lock.acquire()
    try:
        lock.verify()
        os.unlink(lock.path)
        with pytest.raises(LockLost, match="replaced or removed"):
            lock.verify()
        open(lock.path, "w").close()   # a new file at the path is still not ours
        with pytest.raises(LockLost):
            lock.verify()
    finally:
        lock.release()


@posix
def test_lock_file_is_0600_and_new_dirs_0700_but_existing_modes_are_kept(tmp_path) -> None:
    old = tmp_path / "old"
    old.mkdir(mode=0o755)
    old.chmod(0o755)
    lock = CaptureLock(str(old / "new" / "deep" / "c.db"))
    lock.acquire()
    try:
        assert stat.S_IMODE(os.stat(lock.path).st_mode) == 0o600
        for d in (old / "new", old / "new" / "deep"):
            assert stat.S_IMODE(d.stat().st_mode) == 0o700
        assert stat.S_IMODE(old.stat().st_mode) == 0o755
    finally:
        lock.release()
    # An existing lock file keeps whatever mode it has.
    os.chmod(lock.path, 0o640)
    again = CaptureLock(str(old / "new" / "deep" / "c.db"))
    again.acquire()
    again.release()
    assert stat.S_IMODE(os.stat(lock.path).st_mode) == 0o640


@posix
def test_pid_file_path_creates_the_data_dir_0700(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", str(tmp_path / "data"))
    pidfile.pid_file_path("127.0.0.1", 1)
    assert stat.S_IMODE((tmp_path / "data").stat().st_mode) == 0o700


def test_make_private_dirs_raises_under_a_file_parent(tmp_path) -> None:
    (tmp_path / "f").write_text("x")
    with pytest.raises(OSError):
        make_private_dirs(str(tmp_path / "f" / "sub"))


def test_make_private_dirs_raises_on_a_file_at_the_path(tmp_path) -> None:
    (tmp_path / "f").write_text("x")
    with pytest.raises(FileExistsError):
        make_private_dirs(str(tmp_path / "f"))


@posix
def test_make_private_dirs_accepts_a_dir_another_process_made_first(tmp_path, monkeypatch) -> None:
    real_mkdir = os.mkdir

    def racing_mkdir(path, mode=0o777):
        real_mkdir(path, mode)   # the other process wins the race
        raise FileExistsError(path)

    monkeypatch.setattr(os, "mkdir", racing_mkdir)
    make_private_dirs(str(tmp_path / "a" / "b"))
    assert (tmp_path / "a" / "b").is_dir()


def test_acquire_gives_up_when_the_lock_file_identity_never_matches(tmp_path, monkeypatch) -> None:
    lock = CaptureLock(str(tmp_path / "c.db"))
    monkeypatch.setattr(CaptureLock, "_is_current", lambda self, fd: False)
    with pytest.raises(OSError, match="the locked file is not the one at"):
        lock.acquire(timeout=0.2)
    assert lock._fd is None


@pytest.mark.parametrize("db_path", [":memory:"])
def test_a_memory_capture_takes_no_lock(tmp_path, monkeypatch, db_path) -> None:
    monkeypatch.chdir(tmp_path)
    acquired = []
    monkeypatch.setattr(CaptureLock, "acquire", lambda self, timeout=2.0: acquired.append(1))
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[storage]\ndb_path = "{db_path}"\n[server]\nport = 18860\n')
    # Stop right after the lock step: the port conflict check is the next thing to run.
    monkeypatch.setattr(daemon_mod, "_port_conflict", lambda h, p: "stop here")
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("MCUSCOPE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("MCUSCOPE_CACHE_DIR", str(tmp_path / "cache"))
    assert daemon_mod.main(["--config", str(cfg)]) == 1
    assert acquired == []
    assert not list(tmp_path.glob("*.lock"))
