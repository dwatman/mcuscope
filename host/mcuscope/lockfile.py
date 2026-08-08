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

The lock covers writers only. Readers - `sqlite3 capture.db`, a session export - are safe
under WAL and are deliberately not blocked.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time

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


class LockError(RuntimeError):
    """The capture is owned by another daemon."""

    def __init__(self, path: str, holder: dict | None) -> None:
        self.path = path
        self.holder = holder
        super().__init__(self._describe())

    def _describe(self) -> str:
        lines = [f"capture database is already in use by another mcuscoped: {self.path}"]
        if self.holder:
            since = self.holder.get("started")
            when = (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since))
                if isinstance(since, (int, float)) else "an unknown time"
            )
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


class CaptureLock:
    """Exclusive ownership of one capture database, held for the daemon's lifetime."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.path = db_path + ".lock"
        self._fd: int | None = None

    def acquire(self, timeout: float = 2.0) -> None:
        """Take the lock, retrying briefly. Raises LockError if another daemon holds it."""
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
        fd = os.open(self.path, flags, 0o644)
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                _try_lock(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    holder = self._read_holder(fd)
                    os.close(fd)
                    raise LockError(self.db_path, holder) from None
                time.sleep(0.05)
        self._fd = fd
        self._write_holder()

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
