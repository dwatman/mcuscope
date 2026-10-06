"""Single-writer guard for a capture database (SPEC 3.2).

Only one daemon may own a capture. `lines.id` is allocated by the daemon rather than by
SQLite - that is what lets the writer insert a whole batch with one `executemany` - so two
daemons on one file collide on the primary key. The listening port cannot enforce this:
two daemons on different ports can share a `db_path`, and uvicorn runs the app lifespan
before it binds, so even the same-port case has already opened the database and written
rows by the time the bind fails.

The guard is an **OS lock** on a file beside the capture rather than a pid file, because
the kernel drops the lock when the process exits however it exits. A crash, a SIGKILL or
a power cut cannot leave a lock behind for someone to clear by hand, which is the failure
mode every pid file eventually has. Two escape hatches cover what the kernel does not:

- A short retry, because the realistic "stuck" case is not a crash but a restart racing
  its own predecessor's shutdown, and Windows in particular can hold a handle briefly
  after the process is gone.
- An explicit override, for a filesystem that does not implement locking at all (some
  network mounts). The error message names it, so nobody has to find it here first.
  It does not cover a filesystem whose file ids are unstable: `verify()` would stop
  capture on the first tick, so that start is refused outright.

The lock is keyed on `realpath(db_path)`, so every spelling of one file (a symlink, a
relative path) shares one lock file. A hard link to the database is a second name for the
same inode and is NOT covered. `verify()` re-checks that the locked file is still the one
at the path.

The lock covers writers only. Readers - `sqlite3 capture.db`, a session export - are safe
under WAL and are deliberately not blocked.
"""

from __future__ import annotations

import json
import math
import os
import socket
import sys
import time

from .dirs import make_private_dirs

# Byte 0 is the lock target and holds a filler character; the holder's details are written
# from byte 1 on. Windows locks byte *ranges* and fails reads inside them, so keeping the
# metadata outside the locked byte is what lets a second daemon report who holds the lock
# instead of just that it could not get it. POSIX flock is advisory and whole-file, so it
# does not care either way.
_LOCK_BYTE = b"#"
_META_OFFSET = 1

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)   # non-blocking; raises OSError if held

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


# Year 2100, comfortably inside every platform's time_t and far past any real "started".
_MAX_STARTED = 4102444800.0


def _format_started(since: object) -> str:
    """Format the holder's start time, or "an unknown time" for anything unusable.

    The record is untrusted: it is hand-editable and _read_holder validates only that the
    JSON decodes. time.localtime raises OverflowError (ValueError or OSError on some
    platforms) outside time_t, and that raise happened inside LockError's constructor, so a
    corrupt record replaced the documented refusal - --ignore-capture-lock hint included -
    with a traceback.
    """
    if isinstance(since, bool) or not isinstance(since, (int, float)):
        return "an unknown time"
    if not math.isfinite(since) or not 0 <= since <= _MAX_STARTED:
        return "an unknown time"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since))
    except (OverflowError, ValueError, OSError):
        return "an unknown time"


class LockError(RuntimeError):
    """The capture is owned by another daemon."""

    def __init__(self, path: str, holder: dict | None) -> None:
        self.path = path
        self.holder = holder
        super().__init__(self._describe())

    def _describe(self) -> str:
        lines = [f"capture database is already in use by another mcuscoped: {self.path}"]
        if self.holder:
            when = _format_started(self.holder.get("started"))
            lines.append(
                f"  held by pid {self.holder.get('pid', '?')} "
                f"on {self.holder.get('host', '?')} since {when}"
            )
        lines.append(
            "  A crashed daemon cannot leave this behind - the OS releases the lock when "
            "the process exits - so look for a second mcuscoped, or point this one at a "
            "different db_path. If the capture is on a filesystem without working file "
            "locks, start with --ignore-capture-lock."
        )
        return "\n".join(lines)


class LockLost(RuntimeError):
    """The lock file no longer is the file at its path, so the guard protects nothing."""


class CaptureLock:
    """Exclusive ownership of one capture database, held for the daemon's lifetime."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.path = os.path.realpath(db_path) + ".lock"
        self._fd: int | None = None

    def acquire(self, timeout: float = 2.0) -> None:
        """Take the lock, retrying briefly. Raises LockError if another daemon holds it."""
        parent = os.path.dirname(self.path)
        if parent:
            make_private_dirs(parent)
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            fd = self._open()
            try:
                _try_lock(fd)
            except OSError:
                if time.monotonic() >= deadline:
                    holder = self._read_holder(fd)
                    os.close(fd)
                    raise LockError(self.db_path, holder) from None
                os.close(fd)
                time.sleep(0.05)
                continue
            # The holder may have unlinked the file between our open and our lock; we
            # would then hold a lock on a name nobody else looks at.
            if self._is_current(fd):
                break
            os.close(fd)
            if time.monotonic() >= deadline:
                # An OSError, so the daemon reports it as a failed claim, not a holder.
                raise OSError(
                    f"the locked file is not the one at {self.path} (replaced on every "
                    "attempt, or the filesystem reports unstable file ids, which this "
                    "daemon cannot capture on; --ignore-capture-lock does not apply)"
                )
            time.sleep(0.05)
        self._fd = fd
        self._write_holder()

    def _open(self) -> int:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
        return os.open(self.path, flags, 0o600)

    def _is_current(self, fd: int) -> bool:
        try:
            a, b = os.fstat(fd), os.stat(self.path)
        except OSError:
            return False
        return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)

    def verify(self) -> None:
        """Raise LockLost when the lock file was replaced or removed. No-op when not held."""
        if self._fd is not None and not self._is_current(self._fd):
            raise LockLost(f"capture lock file replaced or removed: {self.path}")

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            os.ftruncate(fd, _META_OFFSET)   # drop stale details; the file itself stays
            _unlock(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    # -- holder details (diagnostic only; the OS lock is the actual guard) --------------

    def _write_holder(self) -> None:
        assert self._fd is not None
        record = json.dumps({
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started": time.time(),
            "db": self.db_path,
        }).encode("utf-8")
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.write(self._fd, _LOCK_BYTE + record)
        os.ftruncate(self._fd, _META_OFFSET + len(record))

    @staticmethod
    def _read_holder(fd: int) -> dict | None:
        try:
            os.lseek(fd, _META_OFFSET, os.SEEK_SET)
            raw = os.read(fd, 4096)
            return json.loads(raw.decode("utf-8")) if raw else None
        except (OSError, ValueError, UnicodeDecodeError):
            return None   # the message degrades to "held by someone"; the guard still holds
