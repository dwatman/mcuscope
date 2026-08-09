"""The daemon pid record: how `mcu daemon stop` finds the process to signal.

Written by the daemon itself (claim() in daemon.main), not only by `mcu daemon
start`: a daemon launched as plain `mcuscoped` used to leave no record, so the
documented stop path silently did not apply to it - which on a windowless Windows
interpreter (no console, no Ctrl-C) meant there was no way to stop it at all.

Keyed by host:port, not one file per user: a single shared path meant that starting
a second daemon on another port overwrote the first one's record, so `daemon stop`
then matched a pid from one daemon against a /status from another - and the first
daemon became unstoppable.

claim() never overwrites a *live* record, only a stale one. Two reasons, and the
first alone is enough: on Windows `mcu daemon start`'s Popen pid is the venv
launcher shim - this process's parent - whose pid is the process group id that
CTRL_BREAK_EVENT is delivered to, and replacing it with the worker's own pid would
downgrade a graceful stop into TerminateProcess. The second is a race: two daemons
with different db_path (so the capture lock does not stop the second) on one
host:port both pass daemon.py's port probe, because the probe closes long before
either binds. Overwriting let the loser of the bind race take the winner's record
on the way in and delete it on the way out, leaving a live daemon with no record.

The cost is that a recycled pid in a crashed daemon's leftover record leaves the
new daemon unrecorded, and that is already covered from the other side: `mcu daemon
stop` acts on the pid /status reports, not the recorded one, and signals nothing at
all when no daemon answers - so it can neither miss the live daemon nor kill the
innocent process wearing its old pid.
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
import time

from .config import APP_NAME
from .protocol import is_decimal_token

# How long a claimer's empty record is given to be filled in before it counts as stale.
# Generously over the two syscalls it covers; it is only ever paid on a collision.
CLAIM_SETTLE_S = 0.25

_O_BINARY = getattr(os, "O_BINARY", 0)   # Windows-only flag; a no-op elsewhere


def pid_file_path(host: str, port: int) -> str:
    """Pid record path for the daemon at host:port. Creates the data dir.

    Only the characters a filename cannot hold are substituted, so an IPv6
    literal keys a file too.
    """
    import platformdirs  # lazy: keeps `mcu` CLI startup light

    data_dir = platformdirs.user_data_dir(APP_NAME)
    os.makedirs(data_dir, exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9._-]", "-", f"{host}-{port}")
    return os.path.join(data_dir, f"mcuscoped-{key}.pid")


def pid_running(pid: int) -> bool:
    """Best-effort liveness check, without ever signalling the process.

    os.kill(pid, 0) is the POSIX idiom but is NOT safe on Windows, where every
    signal number outside the two CTRL events maps onto TerminateProcess - probing
    would kill the probed process. Query a handle instead.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION: SYNCHRONIZE for the wait below.
        handle = k32.OpenProcess(0x00100000 | 0x1000, False, pid)
        if not handle:
            # ERROR_ACCESS_DENIED means the process exists but is not ours to open
            # (elevation mismatch) - the mirror of the POSIX PermissionError branch.
            return ctypes.get_last_error() == 5
        try:
            # Not GetExitCodeProcess == STILL_ACTIVE (259): a process that exited
            # with code 259 would read as alive forever. A zero-timeout wait on the
            # handle cannot be fooled: WAIT_TIMEOUT (0x102) means still running.
            return k32.WaitForSingleObject(handle, 0) == 0x102
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # os.kill(pid, 0) also succeeds for a zombie: a daemon launched by a script that never
    # reaps it has exited but still holds a process table entry. Left as "running", `mcu
    # daemon stop` waits out its full grace period and then fails after a shutdown that
    # actually worked. /proc is Linux-specific; anywhere without it, fall through as before.
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fh:
            # The comm field can contain spaces and parentheses, so state is the first
            # field after the last ')'.
            stat = fh.read()
        return stat[stat.rindex(")") + 1:].split()[0] != "Z"
    except (OSError, ValueError, IndexError):
        return True


def read_pid_record(path: str) -> int | None:
    """The pid a record names, or None if it is missing, empty or not a pid.

    The record is a hand-editable file outside this process, and its grammar is one
    ASCII decimal integer - so it is matched against that, never against bare `int()`.
    `int()` accepts other scripts' digits, a sign and underscores (`٣` -> 3, `+17` ->
    17, `1_7` -> 17, `-1` -> -1), and a garbled record that resolves to a small number
    reads as a *live* process: claim() then refuses to record and the daemon runs
    unrecorded, which is the state this module exists to prevent.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            token = fh.read().strip()
    except OSError:
        return None
    return int(token) if is_decimal_token(token) else None


def claim(host: str, port: int) -> str | None:
    """Record this process's pid for host:port; the path, or None if not claimed.

    Any foreign record naming a live process is left alone, and this process goes
    unrecorded: it may be our launcher shim's pid, or a daemon that is about to win
    the bind race for this host:port, and stealing either leaves a live daemon
    unstoppable (see the module docstring). Only a stale record is overwritten.

    The file is created with O_EXCL so two racing daemons cannot both pass the
    read-check; after removing a stale record the create is retried once, and a
    second collision means a genuine concurrent claimer won.

    Creating the file and writing the pid into it are two syscalls, so the record is
    briefly present and empty. Both halves of that window are covered: a failed write
    removes the file rather than leaving an empty record behind for good, and a reader
    that finds an unreadable record re-reads after CLAIM_SETTLE_S before calling it
    stale, since taking a claimer's record leaves that daemon unrecorded.
    """
    try:
        path = pid_file_path(host, port)
    except OSError:
        return None
    for attempt in (0, 1):
        try:
            # O_BINARY (Windows only) for the same reason the write below is bytes: no
            # CRT text-mode translation, so the record is identical on both platforms.
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_BINARY)
        except FileExistsError:
            existing = read_pid_record(path)
            if existing is None:
                # Empty or garbled. It may be another claimer mid-write (two syscalls
                # away from naming itself), so give it time to finish before treating
                # the record as stale and taking it.
                time.sleep(CLAIM_SETTLE_S)
                existing = read_pid_record(path)
            if existing == os.getpid():
                # Already ours (a re-claim, or `mcu daemon start` recorded the pid of the
                # process it spawned - us). Removing and recreating it would open a window
                # in which `mcu daemon stop` finds no pid file and exits 1.
                return path
            if existing is not None and pid_running(existing):
                return None
            if attempt:
                return None  # removed once already: someone else is claiming right now
            try:
                os.remove(path)
            except OSError:
                return None
            continue
        except OSError:
            return None
        # os.write, not fdopen: one unbuffered write of ASCII digits, so there is no
        # flush-on-close that can fail after the caller has been told the claim held,
        # and no newline translation to differ between platforms.
        try:
            try:
                os.write(fd, str(os.getpid()).encode("ascii"))
            finally:
                with contextlib.suppress(OSError):
                    os.close(fd)
        except OSError:
            # A full or read-only disk would otherwise leave an empty record that names
            # no process and outlives us: the next claimer has to treat it as stale.
            # The close above has to come first: Windows refuses to unlink a file that is
            # still open, so removing inside the except left the empty record behind.
            with contextlib.suppress(OSError):
                os.remove(path)
            return None
        return path
    return None


def release(path: str | None) -> None:
    """Remove our own record; a record someone else rewrote meanwhile is kept."""
    if path is None:
        return
    if read_pid_record(path) != os.getpid():
        return
    try:
        os.remove(path)
    except OSError:
        pass
