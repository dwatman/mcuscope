"""Repair null std streams and record otherwise-invisible startup crashes.

Some Windows interpreters (notably vendored ones such as KiCad's bundled Python)
start with sys.stdout/stderr/stdin set to None even under a real console. print()
then silently discards output, and any library that probes the stream - uvicorn's
ColourizedFormatter calls sys.stdout.isatty() - dies with an AttributeError whose
traceback also goes nowhere, so the process exits 1 with no output at all.

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


def repair_std_streams() -> list[str]:
    """Point any null std stream at the console, or failing that at devnull.

    Returns the names of the streams that had to be repaired, so the caller can warn.
    """
    repaired: list[str] = []
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is not None:
            continue
        stream = None
        if sys.platform == "win32":
            try:
                # CONOUT$ reaches the attached console regardless of the handles the
                # process inherited. errors="replace" so a console on a non-UTF-8 code
                # page cannot turn the repair itself into a UnicodeEncodeError.
                stream = open(  # noqa: SIM115 - stream intentionally outlives this call
                    "CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1
                )
            except OSError:
                stream = None
        if stream is None:
            stream = open(  # noqa: SIM115 - stream intentionally outlives this call
                os.devnull, "w", encoding="utf-8", errors="replace"
            )
        setattr(sys, name, stream)
        # Some libraries probe the dunder originals instead; keep them non-None too.
        if getattr(sys, f"__{name}__", None) is None:
            setattr(sys, f"__{name}__", stream)
        repaired.append(name)
    if getattr(sys, "stdin", None) is None:
        sys.stdin = io.StringIO()
        if getattr(sys, "__stdin__", None) is None:
            sys.__stdin__ = sys.stdin
        repaired.append("stdin")
    return repaired


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
    return f"python {sys.version.split()[0]} ({sys.executable})"


def _crash_dir() -> str:
    try:
        import platformdirs

        return platformdirs.user_data_dir(APP_NAME)
    except Exception:
        import tempfile

        return tempfile.gettempdir()


def _write_crash_log(prog: str) -> str | None:
    import traceback

    path = os.path.join(_crash_dir(), f"{prog}-crash.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(interpreter_report() + "\n\n")
            traceback.print_exc(file=fh)
    except OSError:
        return None
    return path


def console_entry(main: Callable[[], int], prog: str) -> int:
    """Run a console-script main() with repaired streams and a crash-file backstop."""
    repaired = repair_std_streams()
    if repaired:
        print(
            f"{prog}: WARNING: this interpreter started with {', '.join(repaired)} set to "
            f"None; reattached to the console. Output may be unreliable.\n"
            + interpreter_report(),
            flush=True,
        )
    try:
        return main()
    except Exception:
        # Not BaseException: Ctrl-C and SystemExit are normal exits, not crashes.
        crash = _write_crash_log(prog)
        if crash is not None:
            # May be a no-op on a broken stream, but costs nothing and usually works.
            print(f"{prog}: fatal error; traceback written to {crash}", flush=True)
        raise
