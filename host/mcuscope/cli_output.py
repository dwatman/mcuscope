"""Output and error plumbing for the `mcu` CLI (cli.py).

Everything the CLI writes - human text, --json objects, stderr diagnostics - and the
SPEC 4 exit discipline around writing it: die() and the module-level --json mode it
reads, the closed-pipe handling on both std streams, the row/frame formatters, and the
confirmation prompt. cli.py holds the commands; this module changes only when how they
report does.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import sys
import time
from typing import TYPE_CHECKING, Any

import click
import typer

if TYPE_CHECKING:
    from .cli_client import Settings


def err(msg: str) -> None:
    """Write one human message to stderr; a closed stderr drops it silently.

    An undeliverable message must not change the exit code (an unguarded BrokenPipeError
    turned every error exit into 0).
    """
    err_write(msg + "\n")


def err_write(text: str) -> None:
    """Write text to stderr, discarding it (and the stream) if stderr is closed.

    Every stderr write goes through here: a failed write left in the buffer makes the
    interpreter's shutdown flush raise and exit 120, whatever the command returned.
    """
    try:
        sys.stderr.write(text)
        sys.stderr.flush()
    except BrokenPipeError:
        _silence_stderr()


def _silence_stderr() -> None:
    """Point stderr at devnull so interpreter shutdown cannot re-raise a broken pipe."""
    _to_devnull(sys.stderr)


# Set once by the global callback. `die()` is called from helpers that have no Settings
# in hand (Client.request, the stream helpers), so the mode is kept here rather than
# threaded through every signature.
_JSON_MODE = False


def set_json_mode(on: bool) -> None:
    global _JSON_MODE
    _JSON_MODE = on


def json_mode() -> bool:
    """The current --json mode, for callers in other modules (the state itself is here)."""
    return _JSON_MODE


def die(msg: str, code: int) -> None:
    """Report a fatal error and exit with the SPEC 4 code.

    In --json mode the error is also emitted on stdout as the command's one JSON object,
    so a consumer parsing stdout gets `{"error": ..., "exit_code": ...}` instead of
    nothing at all. The human message still goes to stderr, which no stdout parser reads.
    """
    err(msg)
    if _JSON_MODE:
        out_json({"error": msg, "exit_code": code})
    raise typer.Exit(code)


def _list_field(body: Any, key: str) -> list:
    """One documented list field of a daemon response, or exit 1 with a clean message.

    A 200 whose `lines` is null or an object is version skew, a proxy, or the wrong port
    answering - not a CLI bug - and it used to reach the user as `reversed(None)` deep in
    a command. Checked here rather than by a blanket `except TypeError` in the dispatcher,
    which would swallow genuine bugs and blame the daemon for them (review class 18).
    """
    val = body.get(key) if isinstance(body, dict) else None
    if not isinstance(val, list):
        die(f"unexpected response from daemon: {key!r} is not a list", 1)
    # The elements too: every caller subscripts them by name, so a list of strings or
    # numbers reached the user as a TypeError traceback and a crash log - the same skew
    # this function exists to report, one level down. One all() pass over rows we are
    # about to format anyway. A dict *missing* a key stays the caller's business; those
    # paths already handle KeyError cleanly.
    if not all(isinstance(item, dict) for item in val):
        die(f"unexpected response from daemon: {key!r} has non-object entries", 1)
    return val


# Windows spells a closed-pipe write or flush as OSError(EINVAL) rather than
# BrokenPipeError. That is translated once, at the stream itself
# (_stdio.translate_closed_pipe_errors), so every handler here - and every library that
# renders our output - sees the one exception type on both platforms. Nothing in the CLI
# classifies errnos: an OSError that is not a BrokenPipeError is a real failure.


def _to_devnull(stream: Any) -> None:
    """Repoint a stream's file descriptor at devnull.

    The bytes a failed write left in the buffer are then flushed somewhere harmless, so
    the interpreter's shutdown flush cannot raise over the top of our exit code.
    """
    with contextlib.suppress(Exception):
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, stream.fileno())


def _silence_stdout() -> None:
    """Point stdout at devnull so interpreter shutdown cannot re-raise a broken pipe."""
    _to_devnull(sys.stdout)


def out_json(obj: Any) -> None:
    print(json.dumps(obj))


def emit_stream(text: str) -> None:
    """Print one line of a follow stream, flushed.

    A follow loop writes to a pipe as often as to a terminal (`mcu tail -f --json | jq`,
    or an agent reading the stream), and Python block-buffers a pipe at 8 KB - which makes
    a live follow look like it has hung until enough output piles up.
    """
    try:
        print(text, flush=True)
    except BrokenPipeError:
        # The reader is done, so the follow is too (`mcu tail -f | head -1`). Silence
        # stdout first or the interpreter's shutdown flush prints over the top of us.
        _silence_stdout()
        raise typer.Exit(0) from None


def fmt_ts(ts: float) -> str:
    """Time of day with milliseconds, for per-line output where the date is noise."""
    return time.strftime("%H:%M:%S", time.localtime(ts)) + f".{int(ts * 1000) % 1000:03d}"


def fmt_datetime(ts: float) -> str:
    """Date and time, for listings that can span days (sessions, notably)."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def fmt_num(value: Any, spec: str = ".0f") -> str:
    """Format a number the daemon sent, tolerating one that is not a number (`null`)."""
    try:
        return format(float(value), spec)
    except (TypeError, ValueError):
        return "?"


def finite(value: float | None) -> bool:
    """True unless `value` is a nan or an infinity.

    `float()` and click's FLOAT accept "nan"/"inf", and a deadline built from either is
    already past.
    """
    return value is None or math.isfinite(value)


def finite_option(value: float | None) -> float | None:
    """Click callback rejecting a non-finite value as bad usage rather than passing it on."""
    if not finite(value):
        raise typer.BadParameter(f"expected a finite number, got {value!r}")
    return value


def fmt_line(row: dict[str, Any]) -> str:
    return f"{fmt_ts(row['ts'])} {row['chan']:>6}| {row['raw']}"


def fmt_frame(fr: dict[str, Any]) -> str:
    flags = ("x" if fr["ext"] else "") + ("r" if fr["rtr"] else "") or "-"
    return (
        f"{fmt_ts(fr['ts'])}  id={fr['can_id']:X} {flags} "
        f"dlc={fr['dlc']} data={fr['data_hex'] or '-'}"
    )


def note_truncated(body: dict[str, Any], limit: int) -> None:
    """Warn on stderr when /lines capped the result set.

    `/lines` answers `{"lines": [...], "truncated": bool}`, but only --json ever showed
    the flag: a capped query read as a complete one, which is how "the error never
    happened" gets concluded from a window that simply did not reach back far enough.
    stderr keeps stdout a clean stream of rows (or of JSON) either way.
    """
    if body.get("truncated"):
        err(f"note: results truncated at limit {limit}; older matches exist "
            f"(raise --limit or use --since-id)")


# Typer vendors its own copy of click (`typer._click`), so a control-flow exception raised
# from inside a typer command is NOT the class of the same name in the installed `click`.
# Catching only one of the two lets the other escape to typer's rich exception hook, which
# answers "n" at a confirmation prompt with a traceback. Both are always caught together.
ABORT_EXCEPTIONS = tuple({click.exceptions.Abort, typer.Abort})
EXIT_EXCEPTIONS = tuple({click.exceptions.Exit, typer.Exit})
USAGE_ERRORS = tuple({click.exceptions.UsageError, typer._click.exceptions.UsageError})


def confirm_or_exit(question: str) -> None:
    """Ask before a destructive action; exit 1 if the answer is no.

    Declining is a normal outcome: a plain message, non-zero so a script never reads
    "cancelled" as "done". A closed stdin (no tty, no --yes) counts as no. The prompt goes
    to stderr and stdin is read directly, since `typer.confirm` still writes to stdout
    even with err=True and would corrupt a --json consumer's parse.
    """
    if _JSON_MODE and not _stdin_is_interactive():
        # A --json consumer is a program, and one that never writes an answer waits for
        # this prompt forever. Refuse instead of hanging, and name the way through.
        die("refusing to prompt for confirmation in --json mode; pass -y to confirm", 1)
    err_write(f"{question} [y/N]: ")
    try:
        answer = sys.stdin.readline()
    except (*ABORT_EXCEPTIONS, EOFError, KeyboardInterrupt, OSError, ValueError):
        answer = ""
    if not answer.endswith("\n"):
        err_write("\n")                  # EOF or ^C left the cursor mid-line
    if answer.strip().lower() not in {"y", "yes"}:
        die("cancelled", 1)


def _stdin_is_interactive() -> bool:
    """True if stdin is a terminal a human could answer on. Never raises."""
    try:
        return bool(sys.stdin is not None and sys.stdin.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def emit_cmd_result(s: Settings, res: dict[str, Any]) -> None:
    """Print a /cmd (or wait cmd) result and exit with the contract code."""
    if s.json_out:
        out_json(res)
    status = res.get("status")
    if status == "ok":
        if not s.json_out and res.get("data"):
            print(res["data"])
        raise typer.Exit(0)
    if status == "timeout":
        if not s.json_out:
            err("timeout")
        raise typer.Exit(2)
    if not s.json_out:
        detail = res.get("err_detail") or ""
        err(f"ERR {res.get('err_code')} {res.get('err_name')} {detail}".rstrip())
    raise typer.Exit(1)
