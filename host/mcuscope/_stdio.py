"""Repair null std streams and record otherwise-invisible startup crashes.

A GUI-subsystem interpreter (pythonw.exe - which uv can pick as a tool venv's base
when a vendored runtime such as KiCad's is first on PATH) gets no console from
Windows, so sys.stdout/stderr/stdin are None by design. print() then silently
discards output, any library that probes the stream - uvicorn's ColourizedFormatter
calls sys.stdout.isatty() - dies with an AttributeError whose traceback also goes
nowhere, and with no console there is no CTRL_C_EVENT either, so the process cannot
be stopped from the terminal that launched it.

repair_std_streams() therefore does two jobs on Windows: first make sure a console
exists at all (join the parent's with AttachConsole, or create one), then point the
null streams at it via CONOUT$/CONIN$. A console attached after interpreter startup
has no Ctrl-C wiring - CPython only installs its CTRL_C_EVENT handler when a console
existed at startup - so attaching also installs a console ctrl handler that routes
Ctrl-C/Break to the main thread as SIGINT, restoring graceful shutdown. Only when no
console can be had do the streams fall back to devnull, and that outcome is reported
distinctly so callers can leave a trace on disk instead.

console_entry() wraps each console-script main(): it repairs the streams first,
and if main() still crashes it writes the traceback plus an interpreter report to
a crash file in the platformdirs data dir, so no failure is ever invisible.

Stdlib-only on purpose: this must work even when the rest of the package fails to
import (platformdirs is imported lazily, with a temp-dir fallback).
"""

from __future__ import annotations

import io
import os
import sys
from collections.abc import Callable

APP_NAME = "mcuscope"  # keep in sync with config.APP_NAME (not imported: see docstring)

# The console ctrl callback must stay referenced for the life of the process, or
# ctypes garbage-collects the thunk and the next Ctrl-C jumps to freed memory.
_ctrl_handler_ref = None


def have_console() -> bool:
    """True if this process is attached to a console (always True off Windows)."""
    if sys.platform != "win32":
        return True
    import ctypes

    # GetConsoleCP, not GetConsoleWindow: a console can exist without a window
    # (ConPTY, service hosts), but an attached console always has an input code
    # page and a process without one gets 0.
    return bool(ctypes.windll.kernel32.GetConsoleCP())


def install_console_ctrl_handler() -> bool:
    """Route console Ctrl-C/Break/close to SIGINT in the main thread (Windows).

    Needed only when the console was attached after interpreter startup: CPython
    wires CTRL_C_EVENT to SIGINT only when a console existed at startup, so after a
    late AttachConsole the default handler terminates the process outright
    (STATUS_CONTROL_C_EXIT) and no graceful shutdown ever runs.
    """
    global _ctrl_handler_ref
    if sys.platform != "win32":
        return False
    import _thread
    import ctypes
    import time
    from ctypes import wintypes

    def _on_event(event: int) -> bool:
        # 0=CTRL_C_EVENT, 1=CTRL_BREAK_EVENT, 2=CTRL_CLOSE_EVENT.
        if event in (0, 1):
            _thread.interrupt_main()
            return True  # handled: suppress the default terminate
        if event == 2:
            # On CTRL_CLOSE Windows kills the process the moment this handler
            # returns; the ~5s grace applies only while it is still executing. So
            # deliver SIGINT and then hold the handler thread open to give the main
            # thread time to shut down cleanly. If shutdown finishes sooner, process
            # exit ends this sleeping thread anyway.
            _thread.interrupt_main()
            time.sleep(4.5)
            return True
        return False

    _ctrl_handler_ref = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)(_on_event)
    k32 = ctypes.windll.kernel32
    k32.SetConsoleCtrlHandler(None, False)  # clear any inherited "ignore Ctrl-C" flag
    return bool(k32.SetConsoleCtrlHandler(_ctrl_handler_ref, True))


def _ensure_console() -> bool:
    """Make sure a console exists; True if one does afterwards (Windows only).

    Prefer AttachConsole(ATTACH_PARENT_PROCESS): in the pythonw launch chain the
    parent venv launcher sits in the terminal the user typed into, so output lands
    there. AllocConsole (a new window) is the last resort. Either way the console
    arrived after interpreter startup, so the ctrl handler must be installed too.

    Attaching to the parent console ties the daemon's lifetime to that terminal
    window: closing it delivers CTRL_CLOSE and ends the daemon. Inherent to
    Windows consoles.
    """
    if sys.platform != "win32":
        return False
    import ctypes

    k32 = ctypes.windll.kernel32
    if k32.GetConsoleCP():  # same window-less-console-safe probe as have_console()
        return True
    if k32.AttachConsole(0xFFFFFFFF) or k32.AllocConsole():  # -1 == ATTACH_PARENT_PROCESS
        # Streams open CONOUT$ as UTF-8; align the console's output code page so
        # non-ASCII output is not mojibake. Best effort, failure is harmless.
        k32.SetConsoleOutputCP(65001)
        install_console_ctrl_handler()
        return True
    return False


def repair_std_streams() -> tuple[list[str], bool]:
    """Point any null std stream at the console, or failing that at devnull.

    Returns (repaired stream names, console_available). console_available is False
    when the process has no console at all - a GUI-subsystem interpreter such as
    pythonw.exe with no parent console to join. In that state everything printed
    goes to devnull and Ctrl-C can never arrive, so the caller must not behave
    like a normal foreground program: leave a trace on disk instead.
    """
    repaired: list[str] = []
    console = True
    if sys.platform == "win32" and (
        sys.stdout is None or sys.stderr is None or sys.stdin is None
    ):
        console = _ensure_console()
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is not None:
            continue
        stream = None
        if sys.platform == "win32":
            # Tried even when the console probe said no: an open CONOUT$ is proof a
            # console exists, so a wrong probe can never force the devnull fallback.
            try:
                # CONOUT$ reaches the attached console regardless of the handles the
                # process inherited. errors="replace" so a console on a non-UTF-8 code
                # page cannot turn the repair itself into a UnicodeEncodeError.
                stream = open(  # noqa: SIM115 - stream intentionally outlives this call
                    "CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1
                )
                console = True
            except OSError:
                stream = None
        if stream is None:
            console = False
            stream = open(  # noqa: SIM115 - stream intentionally outlives this call
                os.devnull, "w", encoding="utf-8", errors="replace"
            )
        setattr(sys, name, stream)
        # Some libraries probe the dunder originals instead; keep them non-None too.
        if getattr(sys, f"__{name}__", None) is None:
            setattr(sys, f"__{name}__", stream)
        repaired.append(name)
    if getattr(sys, "stdin", None) is None:
        stdin = None
        if sys.platform == "win32" and console:
            try:
                stdin = open("CONIN$", encoding="utf-8", errors="replace")  # noqa: SIM115
            except OSError:
                stdin = None
        sys.stdin = stdin if stdin is not None else io.StringIO()
        if getattr(sys, "__stdin__", None) is None:
            sys.__stdin__ = sys.stdin
        repaired.append("stdin")
    return repaired, console


def widen_stdout_encoding() -> None:
    """Stop a non-ASCII character from turning into a traceback when stdout is redirected.

    Attached to a console, Python writes through the console API and is safe. Redirected
    to a pipe or file (`mcuscoped > startup.log`) it falls back to the locale encoding,
    which on Windows is the ANSI code page with errors="strict" - and both entry points
    print user-controlled text: a config error quotes the offending TOML value verbatim,
    and the lock error interpolates the database path and hostname. UTF-8 matches what the
    export paths already write, and errors="replace" degrades an unencodable character to
    `?` rather than raising.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # not a text stream (a replaced or wrapped stream may not be)
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def interpreter_report() -> str:
    """Which Python is actually running: the detail that turns a silent failure on a
    stray vendored interpreter into a two-minute diagnosis."""
    base = getattr(sys, "base_prefix", sys.prefix)
    return (
        f"  python      {sys.version.split()[0]} ({sys.executable})\n"
        f"  prefix      {sys.prefix}\n"
        f"  base_prefix {base}\n"
        f"  stdout={sys.stdout!r} stderr={sys.stderr!r}"
    )


def python_line() -> str:
    """One-line interpreter summary for --version output."""
    line = f"python {sys.version.split()[0]} ({sys.executable})"
    if not have_console():
        line += "  [windowless: no console - output and Ctrl-C unavailable]"
    return line


def _crash_dir() -> str:
    try:
        import platformdirs

        return platformdirs.user_data_dir(APP_NAME)
    except Exception:
        import tempfile

        return tempfile.gettempdir()


# Suffix keying the on-disk reports to one daemon, set by the daemon once it knows its
# host:port (set_report_key). Unset for `mcu` and `mcu-sim`, which are foreground
# programs whose failures are visible on the console anyway.
_report_key = ""


def set_report_key(key: str) -> None:
    """Key this process's startup and crash reports, so two daemons cannot share a file.

    Same defect and same fix as the pid record (pidfile.py): one shared path meant a
    second daemon on another port overwrote the first one's report, and the startup log
    is precisely the artifact that exists because a windowless start leaves no other
    trace - the daemon whose file was overwritten was left running with no record of it
    anywhere. Only the characters a filename cannot hold are substituted, exactly as
    pid_file_path does, so an IPv6 literal keys a file too.
    """
    global _report_key
    import re

    _report_key = "-" + re.sub(r"[^A-Za-z0-9._-]", "-", key) if key else ""


def _write_report(name: str, text: str) -> str | None:
    path = os.path.join(_crash_dir(), name)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    except OSError:
        return None
    return path


def write_startup_log(prog: str, text: str) -> str | None:
    """Record a start on disk (<data_dir>/<prog><key>-startup.log); None if unwritable.

    The crash log only fires on an exception; a *successful but invisible* start
    (no console, streams on devnull) would otherwise leave no trace anywhere.
    """
    return _write_report(f"{prog}{_report_key}-startup.log", text)


def _write_crash_log(prog: str) -> str | None:
    import traceback

    return _write_report(
        f"{prog}{_report_key}-crash.log",
        interpreter_report() + "\n\n" + traceback.format_exc(),
    )


def console_entry(main: Callable[[], int], prog: str) -> int:
    """Run a console-script main() with repaired streams and a crash-file backstop."""
    repaired, console = repair_std_streams()
    widen_stdout_encoding()  # every entry point, not just `mcu`: see the helper's docstring
    if repaired:
        if console and sys.platform == "win32":
            where = "reattached to the console"
        elif any(name in repaired for name in ("stdout", "stderr")):
            where = "no console is attached, so that output goes to devnull"
        else:
            where = "replaced with an empty stream"
        # To stderr, never stdout: this used to print on stdout, so `mcu --json status`
        # with stderr closed emitted five lines of warning ahead of the JSON object and
        # broke every parsing consumer. A repaired stderr points at devnull and swallows
        # the warning, which is the right trade - the daemon's startup log and the crash
        # file are the discoverable trace, and stdout stays machine-readable.
        print(
            f"{prog}: WARNING: this interpreter started with {', '.join(repaired)} set to "
            f"None; {where}. Output may be unreliable.\n" + interpreter_report(),
            file=sys.stderr, flush=True,
        )
    try:
        return main()
    except Exception:
        # Not BaseException: Ctrl-C and SystemExit are normal exits, not crashes.
        crash = _write_crash_log(prog)
        if crash is not None:
            # May be a no-op on a broken stream, but costs nothing and usually works.
            print(f"{prog}: fatal error; traceback written to {crash}",
                  file=sys.stderr, flush=True)
        raise
