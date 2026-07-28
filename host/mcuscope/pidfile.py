"""The daemon pid record: how `mcu daemon stop` finds the process to signal.

Written by the daemon itself (claim() in daemon.main), not only by `mcu daemon
start`: a daemon launched as plain `mcuscoped` used to leave no record, so the
documented stop path silently did not apply to it - which on a windowless Windows
interpreter (no console, no Ctrl-C) meant there was no way to stop it at all.

Keyed by host:port, not one file per user: a single shared path meant that starting
a second daemon on another port overwrote the first one's record, so `daemon stop`
then matched a pid from one daemon against a /status from another - and the first
daemon became unstoppable.

claim() refuses to overwrite a record naming a live process: on Windows `mcu daemon
start`'s Popen pid is the venv launcher, whose pid is the process group id that
CTRL_BREAK_EVENT is delivered to - replacing it with the worker's own pid would
downgrade a graceful stop into TerminateProcess.
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
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _recorded_pid(path: str) -> int | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def claim(host: str, port: int) -> str | None:
    """Record this process's pid for host:port; the path, or None if not claimed.

    An existing record naming a live process other than us is left alone (see the
    module docstring); a stale one from a crashed daemon is overwritten.
    """
    try:
        path = pid_file_path(host, port)
    except OSError:
        return None
    existing = _recorded_pid(path)
    if existing is not None and existing != os.getpid() and pid_running(existing):
        return None
    try:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        return None
    return path


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
