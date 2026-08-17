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
import time
from typing import Any
from urllib.parse import urlsplit

from .cli_client import Client, Settings, die_bad_url
from .cli_output import die, finite, out_json


def _host_port(s: Settings) -> tuple[str, int]:
    try:
        parsed = urlsplit(s.url)
        return parsed.hostname or "127.0.0.1", parsed.port or 8765
    except ValueError as exc:
        # An unterminated IPv6 literal, or a non-numeric port: urlsplit and .port both raise.
        die_bad_url(s.url, exc)


def _pid_file(s: Settings) -> str:
    """Path of the pid record for the daemon at `s.url` (see pidfile.py)."""
    from .pidfile import pid_file_path

    return pid_file_path(*_host_port(s))


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


DAEMON_START_TIMEOUT_S = _start_timeout_default()
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


def _status_body(s: Settings, timeout: float = 2.0) -> dict[str, Any] | None:
    """The daemon's /status body, or None if nothing at `s.url` is mcuscoped.

    A reachable URL that answers with something else (a stray service, a proxy, a stale
    process on the port) counts as "not running" rather than crashing on non-JSON or on
    missing keys. Shared by every `mcu daemon` subcommand so they agree on what "running"
    means.
    """
    body = Client(s).probe("GET", "/status", timeout=timeout)
    return body if _is_status_body(body) else None


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


def _abandon_daemon(
    proc: subprocess.Popen[Any], pid_path: str, s: Settings, wait_s: float
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
        die(f"mcuscoped exited with status {exited} without answering at {s.url}", 1)
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
            f"(raise --timeout if it just needs longer)", 1)
    # Could not be stopped: keep the pid record so it stays addressable, and say so.
    die(f"mcuscoped did not come up at {s.url} within {wait_s:g}s and could not be "
        f"stopped; it is still running as pid {proc.pid} (pid file {pid_path})", 1)


def _serving_pid(body: dict[str, Any], recorded: int | None) -> int | None:
    """The pid serving /status, falling back to the recorded one.

    The pid file can name a launcher shim rather than the daemon itself (Windows venv
    launchers spawn the interpreter as a child, and `daemon start` recorded the pid it
    spawned). /status reports the serving process, which is what a fallback kill must
    target: terminating the shim can leave the real daemon running. Older daemons
    (pre 0.1.2) do not report it; then the recorded pid is all there is.
    """
    status_pid = body.get("pid")
    if isinstance(status_pid, int) and not isinstance(status_pid, bool) and status_pid > 0:
        return status_pid
    return recorded


def _stop_running_daemon(s: Settings, real_pid: int | None, pid_path: str | None) -> None:
    """Stop a daemon that is answering at `s.url`, then report. Never returns normally.

    `real_pid` is None only for a pre-0.1.2 daemon with no pid record: nothing can be
    signalled, so POST /shutdown is the whole of it and its effect is judged on /status
    going quiet. `pid_path` is None when there is no record to tidy up afterwards.
    """
    named = f"pid {real_pid}" if real_pid is not None else s.url
    if not (_request_shutdown(s) and _wait_daemon_gone(s, real_pid, DAEMON_STOP_GRACE_S)):
        if real_pid is None:
            die(f"the daemon at {s.url} did not accept a shutdown request and no pid is "
                "recorded for it; stop it from the process list", 1)
        # No POST /shutdown (older daemon), or it accepted and then failed to exit.
        try:
            _signal_daemon_stop(real_pid)
        except (ProcessLookupError, OSError) as exc:
            if pid_path is not None:
                with contextlib.suppress(OSError):
                    os.remove(pid_path)
            die(f"could not stop pid {real_pid}: {exc}", 1)
        if not _wait_pid_gone(real_pid, DAEMON_STOP_GRACE_S):
            die(f"pid {real_pid} did not exit within {DAEMON_STOP_GRACE_S:g}s", 1)
    # The daemon removes its own record when it owns one; this covers the launcher-pid
    # record it refused to clobber, tolerating whichever of us got there first.
    if pid_path is not None:
        with contextlib.suppress(OSError):
            os.remove(pid_path)
    # Belt and braces for the shim case: if something still answers, the recorded pid
    # was not the daemon and the kill did not propagate. Say so rather than lie.
    if _status_body(s, timeout=1.0) is not None:
        die(f"a process is still answering at {s.url} after stopping {named}; "
            "the daemon runs under a different pid - stop it from the process list", 1)
    if s.json_out:
        out_json({"ok": True, "pid": real_pid})
    else:
        print(f"stopped mcuscoped ({named})")


def _wait_daemon_gone(s: Settings, pid: int | None, timeout_s: float) -> bool:
    """True once the daemon is gone: judged by pid where one is known, else by /status."""
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
