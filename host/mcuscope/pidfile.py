"""The daemon pid record: how `mcu daemon stop` finds the process to signal.

Written by the daemon itself (claim() in daemon.main), not only by `mcu daemon
start`: a daemon launched as plain `mcuscoped` used to leave no record, so the
documented stop path silently did not apply to it - which on a windowless Windows
interpreter (no console, no Ctrl-C) meant there was no way to stop it at all.

Keyed by host:port, not one file per user: a single shared path meant that starting
a second daemon on another port overwrote the first one's record, so `daemon stop`
then matched a pid from one daemon against a /status from another - and the first
daemon became unstoppable.

claim() refuses to overwrite only a live record naming our own parent: on Windows
`mcu daemon start`'s Popen pid is the venv launcher shim - this process's parent -
whose pid is the process group id that CTRL_BREAK_EVENT is delivered to, and
replacing it with the worker's own pid would downgrade a graceful stop into
TerminateProcess. Any other live pid in the file is a recycled pid sitting in a
crashed daemon's leftover record; keeping it would leave the new daemon unrecorded
and point `mcu daemon stop` at an innocent process, so it is overwritten.
"""

from __future__ import annotations

import os
import re
import sys

from .config import APP_NAME


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


def legacy_pid_file() -> str:
    """The pre-keying pid path, still read by `daemon stop` so an already-running
    daemon started by an older `mcu` can still be stopped."""
    import platformdirs

    return os.path.join(platformdirs.user_data_dir(APP_NAME), "mcuscoped.pid")


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


def _recorded_pid(path: str) -> int | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def claim(host: str, port: int) -> str | None:
    """Record this process's pid for host:port; the path, or None if not claimed.

    The only foreign record left alone is a live one naming our own parent:
    `mcu daemon start` records the pid of the launcher it spawned, which on
    Windows is our parent shim and the CTRL_BREAK process-group id (see the
    module docstring). Any other live pid here is a recycled pid from a crashed
    daemon's leftover record and is overwritten - refusing would leave this
    daemon unrecorded and point `mcu daemon stop` at an innocent process.

    The file is created with O_EXCL so two racing daemons cannot both pass the
    read-check; after removing a stale record the create is retried once, and a
    second collision means a genuine concurrent claimer won.
    """
    try:
        path = pid_file_path(host, port)
    except OSError:
        return None
    for attempt in (0, 1):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = _recorded_pid(path)
            if existing == os.getpid():
                # Already ours (a re-claim, or `mcu daemon start` recorded the pid of the
                # process it spawned - us). Removing and recreating it would open a window
                # in which `mcu daemon stop` finds no pid file and exits 1.
                return path
            if (
                existing is not None
                and existing == os.getppid()
                and pid_running(existing)
            ):
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
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(str(os.getpid()))
        except OSError:
            return None
        return path
    return None


def release(path: str | None) -> None:
    """Remove our own record; a record someone else rewrote meanwhile is kept."""
    if path is None:
        return
    if _recorded_pid(path) != os.getpid():
        return
    try:
        os.remove(path)
    except OSError:
        pass
