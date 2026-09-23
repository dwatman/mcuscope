"""Daemon lifecycle for the `mcu daemon` subcommands: spawn, readiness, pid record, stop.

The commands themselves (typer wiring, output) stay in cli.py; this module holds the
machinery that decides whether a daemon is running, writes and tidies the pid record,
abandons one that never came up, and stops one however it was started. The daemon's own
side of the pid record lives in pidfile.py.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit

from .cli_client import Client, Settings, die_bad_url
from .cli_output import die, finite, out_json


def _host_port(s: Settings) -> tuple[str, int]:
    try:
        parsed = urlsplit(s.url)
        return parsed.hostname or "127.0.0.1", parsed.port or 8558
    except ValueError as exc:
        # An unterminated IPv6 literal, or a non-numeric port: urlsplit and .port both raise.
        die_bad_url(s.url, exc)


def _pid_file(s: Settings) -> str:
    """Path of the pid record for the daemon at `s.url` (see pidfile.py).

    Resolving it creates the data directory, which can fail (a read-only home, an
    XDG_DATA_HOME that is a file or a dangling symlink). pidfile.claim wraps the same call;
    both CLI call sites did not, so it escaped the SPEC 4 exit-code contract as a traceback
    - on `daemon start` after the child had already been spawned.
    """
    from .pidfile import pid_file_path

    try:
        return pid_file_path(*_host_port(s))
    except OSError as exc:
        die(f"cannot use the daemon pid file: {exc}", 1)
    raise AssertionError("unreachable")  # for type-checkers; die() always raises


def _stderr_log_path(pid_path: str) -> str:
    """Where a spawned daemon's stderr goes: the pid record's name with `.err`, appended to
    by every start (a truncating open by a start that loses the bind race would wipe the
    winner's file). Keyed by host:port like the record."""
    base = pid_path[:-4] if pid_path.endswith(".pid") else pid_path
    return base + ".err"


def _open_append(path: str) -> Any:
    """`open(path, "ab")` whose appends hold in a child that inherits the handle.

    On POSIX O_APPEND lives on the shared open file description. Windows' CRT emulates it
    in the opening process only, so a spawned daemon would write at its own offset, over a
    racing start's lines; a handle opened with FILE_APPEND_DATA alone appends in every
    process that holds it.
    """
    if sys.platform != "win32":
        return open(path, "ab")  # noqa: SIM115  (the caller closes it after the spawn)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                wintypes.HANDLE)
    file_append_data, synchronize = 0x0004, 0x00100000
    share_all = 0x1 | 0x2 | 0x4          # read, write, delete: as a CRT open shares
    open_always, file_attribute_normal = 4, 0x80
    handle = k32.CreateFileW(path, file_append_data | synchronize, share_all, None,
                             open_always, file_attribute_normal, None)
    if handle is None or handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    fd = msvcrt.open_osfhandle(handle, os.O_APPEND | getattr(os, "O_BINARY", 0))
    return open(fd, "ab")  # noqa: SIM115


def _stderr_lines(err_path: str | None, start: int = 0) -> list[str]:
    """What the daemon wrote to its stderr file from byte `start` (where this start's child
    began appending); [] if nothing can be read."""
    if not err_path:
        return []
    try:
        with open(err_path, "rb") as fh:
            fh.seek(start)
            return fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []


# store.INDEX_BUILD_NOTICE and INDEX_BUILT_NOTICE, which the store logs around the index
# build an older capture needs before the daemon answers. Copied, not imported: store.py
# is heavy for the CLI. A test holds them equal.
INDEX_BUILD_NOTICE = "building index"
INDEX_BUILT_NOTICE = "built index"


def _index_build(err_path: str | None, start: int = 0) -> tuple[str | None, bool]:
    """(the index names the daemon's stderr says it is building, None if it says nothing
    of a build; whether a later line says the build finished). Other warnings can follow
    either notice, so each is matched anywhere in the lines, never only at the end."""
    names, built = None, False
    for line in _stderr_lines(err_path, start):
        if INDEX_BUILD_NOTICE in line:
            names = line.rpartition(INDEX_BUILD_NOTICE)[2].partition(" once")[0].strip()
        elif INDEX_BUILT_NOTICE in line:
            built = True
    return names, built


def _stderr_tail(err_path: str | None, n: int = 10, start: int = 0) -> str:
    """The last `n` lines the daemon wrote to its stderr file from byte `start` (where this
    start's child began appending), for a start that failed; "" if none."""
    lines = _stderr_lines(err_path, start)
    if not lines:
        return ""
    k = min(n, len(lines))
    return f"\nlast {k} line{'s' if k != 1 else ''} of {err_path}:\n" + "\n".join(lines[-n:])


def _start_timeout_default() -> float:
    """Readiness wait for `daemon start`, overridable from the environment.

    Three seconds was optimistic: opening a multi-gigabyte capture, or a first run on a
    cold or network filesystem, can take longer, and the old code called that a failure.
    """
    raw = os.environ.get("MCUSCOPE_START_TIMEOUT")
    if raw:
        with contextlib.suppress(ValueError):
            wait_s = float(raw)
            if finite(wait_s):     # "nan" would skip the readiness wait entirely
                return max(wait_s, 0.5)
    return 20.0


# The function, not its value: click calls a callable default at invocation, so
# MCUSCOPE_START_TIMEOUT is read per `daemon start` rather than frozen at import. It was
# fixed for the life of the interpreter, which is not what an environment variable means
# (and is how the tests drive two runs in one process).
DAEMON_START_TIMEOUT_S = _start_timeout_default
# How long `daemon stop` waits for the daemon to exit after a clean stop request
# (POST /shutdown, or the SIGTERM fallback on POSIX). Graceful shutdown itself is
# capped at 5s of in-flight requests (daemon.GRACEFUL_SHUTDOWN_S) plus the store flush.
DAEMON_STOP_GRACE_S = 10.0

_STATUS_BODY_KEYS = {"version", "uptime_s", "ports"}


def _is_status_body(body: Any) -> bool:
    """True if `body` looks like a genuine mcuscoped /status response.

    `uptime_s` is type-checked, not merely present: it goes straight into a format
    specifier, and a responder sending null for it raised TypeError at the user. Newer
    fields (`pid`, `write_errors`) stay optional so an older daemon still qualifies.
    """
    if not (isinstance(body, dict) and _STATUS_BODY_KEYS <= body.keys()):
        return False
    return isinstance(body["uptime_s"], (int, float)) and not isinstance(body["uptime_s"], bool)


def _status_or_refusal(
    s: Settings, timeout: float = 2.0,
) -> tuple[dict[str, Any] | None, tuple[int, str] | None]:
    """The /status body, plus the daemon's own guard refusal (code, message) when it
    answered with one.

    The guard answering (token, Host, lockout) means the daemon is running, which every
    caller but one reads as a refusal to report. `daemon start`'s post-spawn readiness wait
    reads it as "the daemon I just started is up", so it takes the pair instead.
    """
    code, body = Client(s).probe_status("GET", "/status", timeout=timeout)
    if code in (401, 403, 429) and isinstance(body, dict) and isinstance(body.get("error"), str):
        return None, (code, body["error"])
    return (body if _is_status_body(body) else None), None


def _status_body(s: Settings, timeout: float = 2.0) -> dict[str, Any] | None:
    """The daemon's /status body, or None if nothing at `s.url` is mcuscoped.

    A reachable URL that answers with something else (a stray service, a proxy, a stale
    process on the port) counts as "not running" rather than crashing on non-JSON or on
    missing keys. Shared by every `mcu daemon` subcommand so they agree on what "running"
    means.
    """
    body, refusal = _status_or_refusal(s, timeout)
    if refusal is not None:
        # It is running, and reading this as absent would spawn a second daemon that dies
        # on the port.
        code, message = refusal
        die(f"daemon at {s.url} refused the request (HTTP {code}): {message}", 1)
    return body


def _remove_pid_record(pid_path: str, pid: int) -> None:
    """Remove the pid record only while it still names `pid`.

    Between writing a record and giving up on the process it names, another daemon can
    have claimed the same host:port record (pidfile.claim). Removing that one leaves a
    live daemon with nothing addressing it, which is exactly the unstoppable-daemon
    state this whole path exists to avoid.
    """
    from .pidfile import read_pid_record

    recorded = read_pid_record(pid_path)   # the record's grammar, not bare int()
    if recorded != pid:
        return
    with contextlib.suppress(OSError):
        os.remove(pid_path)


def _write_pid_record(pid_path: str, pid: int) -> bool:
    """Record `pid` at `pid_path`; False when a live daemon's record is already there.

    pidfile.claim's rule, applied to the CLI's own write: a record naming a *running*
    process is never overwritten, because overwriting lets the loser of a start race take
    the winner's record (see pidfile's module docstring). `daemon start` wrote this file
    with a plain open + replace, with no read, no liveness check and no comparison, which
    is exactly the case the rule exists for. Raises OSError like the write it wraps.
    """
    from .pidfile import pid_running, read_pid_record

    existing = read_pid_record(pid_path)
    if existing is not None and existing != pid and pid_running(existing):
        return False
    from .config import replace_atomic

    # Atomically: a plain open("w") truncates first, and a concurrent `daemon stop` reading
    # at that instant would see an empty file, call it corrupt and delete it. The temp name
    # carries our pid so two concurrent starts do not write each other's bytes.
    tmp_path = f"{pid_path}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(str(pid))
        replace_atomic(tmp_path, pid_path)
    except OSError:
        with contextlib.suppress(OSError):
            os.remove(tmp_path)   # do not leave the half-written .tmp lying beside it
        raise
    return True


def _abandon_daemon(
    proc: subprocess.Popen[Any], pid_path: str, s: Settings, wait_s: float,
    err_path: str | None = None, err_start: int = 0,
) -> None:
    """Deal with a spawned daemon that never answered, then exit 1. Never returns.

    The old failure path deleted the pid file and left the process running, so a daemon
    that was merely slow became one nothing could stop: `daemon status` reported it up and
    `daemon stop` said "no pid file". Either the child goes away, or its pid record stays
    and the message names the pid.
    """
    exited = proc.poll()
    if exited is not None:
        _remove_pid_record(pid_path, proc.pid)
        die(f"mcuscoped exited with status {exited} without answering at {s.url}"
            f"{_stderr_tail(err_path, start=err_start)}", 1)
    stopped = False
    with contextlib.suppress(OSError):
        proc.terminate()
        try:
            proc.wait(timeout=5)
            stopped = True
        except subprocess.TimeoutExpired:
            proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
                stopped = True
    if stopped:
        _remove_pid_record(pid_path, proc.pid)
        die(f"mcuscoped did not come up at {s.url} within {wait_s:g}s; stopped it "
            f"(raise --timeout if it just needs longer)"
            f"{_stderr_tail(err_path, start=err_start)}", 1)
    # Could not be stopped: keep the pid record so it stays addressable, and say so.
    die(f"mcuscoped did not come up at {s.url} within {wait_s:g}s and could not be "
        f"stopped; it is still running as pid {proc.pid} (pid file {pid_path})", 1)


def _status_pid(body: dict[str, Any], key: str) -> int | None:
    value = body.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _serving_pids(body: dict[str, Any]) -> set[int]:
    """The pids that are the daemon answering /status: its own `pid`, and on Windows its
    `ppid`, the venv launcher shim `daemon start` spawned and recorded. Empty for a daemon
    that reports neither (pre 0.1.2 has no `pid`, pre 0.5.0 no `ppid`).

    Only ever compared with a local pid, never signalled on its own: it may be another
    machine's (a remote --url, a tunnelled loopback port).
    """
    pids = {_status_pid(body, "pid")}
    if sys.platform == "win32":
        pids.add(_status_pid(body, "ppid"))
    pids.discard(None)
    return pids  # type: ignore[return-value]


def _stop_running_daemon(
    s: Settings, body: dict[str, Any], pid_path: str | None = None,
    recorded: int | None = None, quiet: bool = False,
) -> None:
    """Stop the daemon whose /status `body` answered at `s.url`, then report; dies on any
    failure.

    `recorded` is the pid the local record at `pid_path` names. It is signalled or waited
    on only when `body` corroborates it (_serving_pids): a crashed daemon's record can name
    a recycled pid while another daemon, local or tunnelled, serves the URL. Otherwise POST
    /shutdown is the whole of it, judged on /status going quiet. The tidy-up removes the
    record only while it still names `recorded` (see _remove_pid_record).
    """
    pid = recorded if recorded is not None and recorded in _serving_pids(body) else None
    named = f"pid {pid}" if pid is not None else s.url
    if not (_request_shutdown(s) and _wait_daemon_gone(s, pid, DAEMON_STOP_GRACE_S)):
        if pid is None:
            why = ("no local pid record names it" if recorded is None else
                   f"its pid record {pid_path} names pid {recorded}, which is not the "
                   "process serving it")
            die(f"the daemon at {s.url} did not stop on a shutdown request, and {why}, so "
                "no process was signalled; stop it where it runs", 1)
        # No POST /shutdown (older daemon), or it accepted and then failed to exit.
        try:
            _signal_daemon_stop(pid)
        except (ProcessLookupError, OSError) as exc:
            if pid_path is not None:
                _remove_pid_record(pid_path, pid)
            die(f"could not stop pid {pid}: {exc}", 1)
        if not _wait_pid_gone(pid, DAEMON_STOP_GRACE_S):
            die(f"pid {pid} did not exit within {DAEMON_STOP_GRACE_S:g}s", 1)
    # The daemon removes its own record when it owns one; this covers the launcher-pid
    # record it refused to clobber, and a stale record naming another process. Through
    # _remove_pid_record, so a record a *new* daemon claimed for this host:port between
    # the stop and here is left alone rather than deleted out from under it.
    if pid_path is not None and recorded is not None:
        _remove_pid_record(pid_path, recorded)
    # Belt and braces for the shim case: if something still answers, the recorded pid
    # was not the daemon and the kill did not propagate. Say so rather than lie.
    if _status_body(s, timeout=1.0) is not None:
        die(f"a process is still answering at {s.url} after stopping {named}; "
            "the daemon runs under a different pid - stop it from the process list", 1)
    if quiet:      # `daemon restart` reports once, for the start
        return
    if s.json_out:
        out_json({"ok": True, "pid": pid})
    elif pid is not None:
        print(f"stopped mcuscoped ({named})")
    elif recorded is None:
        print(f"stopped mcuscoped at {s.url} (no local pid record: asked it to shut down, "
              "signalled nothing)")
    else:
        print(f"stopped mcuscoped at {s.url} (its pid record named pid {recorded}, not the "
              "serving process: asked it to shut down, signalled nothing)")


def _wait_daemon_gone(s: Settings, pid: int | None, timeout_s: float) -> bool:
    """True once the daemon is gone: judged by its corroborated pid where one is known,
    else by /status."""
    if pid is not None:
        return _wait_pid_gone(pid, timeout_s)
    deadline = time.monotonic() + timeout_s
    while _status_body(s, timeout=1.0) is not None:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


def _request_shutdown(s: Settings) -> bool:
    """True if the daemon accepted POST /shutdown (graceful stop on every platform).

    The endpoint exists because Windows has no graceful *signal* that crosses console
    boundaries (see _signal_daemon_stop); a REST call reaches the daemon no matter how
    it was launched. Absent on pre-0.1.2 daemons, which answer with an error envelope.
    """
    body = Client(s).probe("POST", "/shutdown")
    return isinstance(body, dict) and body.get("ok") is True


def _wait_pid_gone(pid: int, timeout_s: float) -> bool:
    """Wait for `pid` to exit; True once it is gone. Probes without signalling: on
    Windows any real os.kill probe is destructive (see pidfile.pid_running)."""
    from .pidfile import pid_running

    deadline = time.monotonic() + timeout_s
    while pid_running(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


def _signal_daemon_stop(pid: int) -> None:
    """Fallback stop for a daemon without POST /shutdown, or one that failed to exit.

    On POSIX SIGTERM is graceful (uvicorn runs the lifespan). On Windows os.kill is
    TerminateProcess and the console ctrl events cannot reach a detached daemon, so
    POST /shutdown is the graceful stop there and this is the hard last resort.
    """
    os.kill(pid, signal.SIGTERM)
