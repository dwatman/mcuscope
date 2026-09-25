"""Output and error plumbing for the `mcu` CLI (cli.py).

Everything the CLI writes - human text, --json objects, stderr diagnostics - and the
SPEC 4 exit discipline around writing it: die() and the module-level --json mode it
reads, the closed-pipe handling on both std streams, the row/frame formatters, and the
confirmation prompt. cli.py holds the commands; this module changes only when how they
report does.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import math
import os
import re
import stat
import sys
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import click
import typer

from . import protocol as p

# fmt_line is re-exported for cli.py; the daemon imports it from render directly.
from .render import fmt_line as fmt_line
from .render import fmt_ts

if TYPE_CHECKING:
    from .cli_client import Settings


def err(msg: str) -> None:
    """Write one human message to stderr; a closed stderr drops it silently.

    An undeliverable message must not change the exit code (an unguarded BrokenPipeError
    turned every error exit into 0).
    """
    err_write(msg + "\n")


# SGR (colour) sequences, kept on a terminal; every other C0 or C1 control but TAB and LF
# is shown escaped, so a target's cursor moves, clipboard writes (OSC 52) and bells never
# act on the operator's terminal.
_CONTROLS = re.compile(r"(\x1b\[[0-9;:]*m)|[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def visible(text: str) -> str:
    """`text` for a terminal: SGR kept, any other control byte as `\\xNN`."""
    return _CONTROLS.sub(lambda m: m.group(1) or f"\\x{ord(m.group()):02x}", text)


def _isatty(stream: Any) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def err_write(text: str) -> None:
    """Write text to stderr, discarding it (and the stream) if stderr is closed.

    Every stderr write goes through here: a failed write left in the buffer makes the
    interpreter's shutdown flush raise and exit 120, whatever the command returned.
    """
    try:
        if _isatty(sys.stderr):
            text = visible(text)
        sys.stderr.write(text)
        sys.stderr.flush()
    except OSError:          # a closed pipe or a full disk: either way nowhere to report it
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


def _field(body: Any, key: str, optional: bool = False) -> Any:
    """One documented object field of a daemon response, or exit 1 with a clean message.

    The sibling of _list_field for the fields a command subscripts or calls .get() on: the
    same version skew reaches them (`"session": "x"`), where it landed as a TypeError
    traceback and a crash log rather than as the mapped exit code. `optional` is for the
    blocks an older daemon omits and a current one sends as null: those come back None and
    the caller's `if` skips them, but a non-null value still has to be an object.
    """
    val = body.get(key) if isinstance(body, dict) else None
    if optional and val is None:
        return None
    if not isinstance(val, dict):
        die(f"unexpected response from daemon: {key!r} is not an object", 1)
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


def remove_partial(path: str) -> None:
    """Remove what a failed export left at `path`, when that resolves to a regular file.

    A symlink is kept and the regular file it resolves to is removed: the open truncated
    it, so it holds only the partial bytes. A FIFO or a device (`/dev/null`) is never
    removed, since the export never owned it.
    """
    with contextlib.suppress(OSError):
        real = os.path.realpath(path)
        if stat.S_ISREG(os.lstat(real).st_mode):
            os.remove(real)


def _silence_stdout() -> None:
    """Point stdout at devnull so interpreter shutdown cannot re-raise a broken pipe."""
    _to_devnull(sys.stdout)


# A stdout write that failed for anything other than a closed pipe (a full disk, a quota).
# The write must not own the exit code - unhandled it escapes as a traceback and exit 120 -
# so it is recorded here and main() maps it to 1.
_OUT_FAILED = False


def reset_output_state() -> None:
    """Clear the failed-stdout flag; main() is callable more than once in one process."""
    global _OUT_FAILED
    _OUT_FAILED = False


def output_failed() -> bool:
    return _OUT_FAILED


def _stdout_unwritable(exc: OSError) -> None:
    global _OUT_FAILED
    if _OUT_FAILED:          # the guard and its caller both see one failure: report it once
        return
    _OUT_FAILED = True
    _silence_stdout()
    err(f"cannot write output: {exc}")


class _GuardedStdout:
    """stdout that records its own write failure (a full disk) before raising it.

    A bare print reached the dispatcher as an OSError it could not tell from any other, so it
    crash-logged; output_failed() now tells it. Raised, not swallowed, so a guarded caller
    (out_json, emit_stream) still owns its exit code. Everything else is delegated, as _stdio's
    _PipeErrorStream does, so rich and click still see the real stream.

    On a terminal, human output passes through visible(); --json, pipes and files get the
    bytes as captured. Every print and export write crosses this one point.
    """

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._tty = _isatty(stream)

    def write(self, s: str) -> int:
        try:
            if self._tty and not _JSON_MODE:
                self._stream.write(visible(s))
                return len(s)
            return self._stream.write(s)
        except BrokenPipeError:
            raise
        except OSError as exc:
            _stdout_unwritable(exc)
            raise

    def flush(self) -> None:
        try:
            self._stream.flush()
        except BrokenPipeError:
            raise
        except OSError as exc:
            _stdout_unwritable(exc)
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def guard_stdout() -> None:
    """Install _GuardedStdout once; main() runs more than once in one process."""
    if sys.stdout is not None and not isinstance(sys.stdout, _GuardedStdout):
        sys.stdout = _GuardedStdout(sys.stdout)


def out_json(obj: Any) -> None:
    """Write the command's one JSON object to stdout, dropping it if stdout is closed.

    The stderr half of this guard (err_write) exists because an undeliverable message must
    not change the exit code; the same is true here, and worse: the write is flushed so the
    failure lands where it can be handled, instead of at the interpreter's shutdown flush
    where every --json error exit came back 0 (the broken-pipe arm's answer).
    """
    try:
        print(json.dumps(obj), flush=True)
    except BrokenPipeError:
        _silence_stdout()
    except OSError as exc:
        _stdout_unwritable(exc)


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
    except OSError as exc:
        # Our own stdout, not the daemon: raised as typer.Exit so the follow loop's
        # "daemon unreachable" OSError arm cannot claim it.
        _stdout_unwritable(exc)
        raise typer.Exit(1) from None


def fmt_datetime(ts: float) -> str:
    """Date and time, for listings that can span days (sessions, notably)."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def fmt_age(seconds: float) -> str:
    """A duration as one coarse unit: 12s, 5m, 3h, 2d."""
    seconds = max(0.0, seconds)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return f"{int(seconds)}s"


# [0-9], never \d: re matches every Unicode decimal digit with \d, so Arabic-Indic digits
# opened a window at 12:30. Every sibling grammar in the tree spells the set out.
_CLOCK_RE = re.compile(
    r"^(?:(?P<y>[0-9]{4})-(?P<mo>[0-9]{2})-(?P<d>[0-9]{2})[T ])?"
    r"(?P<h>[0-9]{2}):(?P<mi>[0-9]{2})(?::(?P<s>[0-9]{2})(?:\.(?P<f>[0-9]{1,6}))?)?$"
)


def parse_clock(text: str) -> float:
    """`[YYYY-MM-DDT]HH:MM[:SS[.fff]]`, local time, today by default, as a POSIX timestamp.

    An explicit grammar rather than `fromisoformat`: that accepts forms outside the
    documented one (`20260901` as 20:26:09.01, a bare hour, a `+09:00` offset that
    silently shifts the window) and its fraction rules differ between 3.10 and 3.11.
    """
    m = _CLOCK_RE.match(text)
    try:
        if m is None:
            raise ValueError
        g = m.groupdict()
        day = (
            datetime.date(int(g["y"]), int(g["mo"]), int(g["d"])) if g["y"]
            else datetime.date.today()
        )
        clock = datetime.time(
            int(g["h"]), int(g["mi"]), int(g["s"] or 0),
            int((g["f"] or "0").ljust(6, "0")),
        )
        dt = datetime.datetime.combine(day, clock)
        # Within a day of either end the local-offset conversion overflows in some
        # zones and not others (9999-12-31T23:59 raises east of UTC, converts west
        # of it), so reject both ends outright: the answer must not depend on the
        # machine's zone. Still inside the try, for the platform's own epoch limit
        # (Windows refuses anything before 1970).
        one_day = datetime.timedelta(days=1)
        if not datetime.datetime.min + one_day <= dt <= datetime.datetime.max - one_day:
            raise ValueError
        return dt.timestamp()
    except (ValueError, OverflowError, OSError):
        raise typer.BadParameter(
            f"expected HH:MM[:SS[.mmm]] or YYYY-MM-DDTHH:MM:SS, got {text!r}"
        ) from None


def positive_option(value: float | None) -> float | None:
    """Click callback: a bound that must be greater than zero to be satisfiable."""
    if value is not None and not (value > 0):
        raise typer.BadParameter(f"must be greater than 0, got {value!r}")
    return value


def _fmt_value(v: float) -> str:
    return str(int(v)) if v.is_integer() and abs(v) < 1e15 else f"{v:.6g}"


class LineDecoder:
    """Render plot lines as named fields (`--decode`), optionally only on change.

    `!pd` definitions are learned as they stream past (and primed newest-first from the
    store, see `prime`), `!ps` samples render against them with enum labels and bit-lane
    names resolved, `!p` ad-hoc lines carry their own names. Every other line passes
    through untouched, so the decoded stream keeps its debug and CAN context.
    Definitions and the --changes baseline are per port: a sid is unique only within one.
    """

    def __init__(self, names: Iterable[str] | None = None, changes: bool = False) -> None:
        self._pds: dict[str | None, p.PlotDecoder] = {}
        self.names = ",".join(names) if names is not None else None   # as --names took it
        self._names = set(names) if names is not None else None
        self.changes = changes
        self._changes = changes
        self._last: dict[tuple[str | None, str], tuple[str, ...]] = {}

    def share_changes(self, other: LineDecoder) -> None:
        """Continue `other`'s --changes baseline, so a sample it printed is not new here."""
        self._last = other._last

    def _pd(self, port: str | None) -> p.PlotDecoder:
        return self._pds.setdefault(port, p.PlotDecoder())

    def prime(self, raws: Iterable[str], port: str | None = None) -> None:
        """Learn `port`'s definitions from rows read newest-first out of the store."""
        for raw in raws:
            self._pd(port).learn(raw, keep_existing=True)

    def decode(self, raw: str, port: str | None = None) -> str | None:
        """Decoded text for `raw`; the line itself when it is not a sample; None to drop."""
        if not raw.startswith("!p"):
            return raw
        pd = self._pd(port)
        if raw.startswith("!pd"):
            # A definition is metadata, rebroadcast every 5 s: noise once learned. A line
            # learn() rejects (a malformed one, or another token such as `!pdo`) taught
            # nothing, so it stays visible.
            return None if pd.learn(raw) else raw
        sample = pd.feed(raw)
        if sample is None:
            return raw    # a sample ahead of its definition, or malformed: show as is
        fields = self._fields(sample, pd)
        if self._names is not None:
            fields = [f for f in fields if f[2] & self._names]
            if not fields:
                return None
        key = f"s{sample.sid}" if sample.sid is not None else "p:" + ",".join(f[0] for f in fields)
        rendered = tuple(f"{name}={text}" for name, text, _ in fields)
        if self._changes and self._last.get((port, key)) == rendered:
            return None
        self._last[(port, key)] = rendered
        return f"{key} " + " ".join(rendered)

    def _fields(
        self, sample: p.PlotSample, pd: p.PlotDecoder
    ) -> list[tuple[str, str, set[str]]]:
        """(name, rendered value, names it answers to) per channel, in definition order."""
        definition = pd.definition(sample.sid) if sample.sid is not None else None
        if definition is None:
            return [(n, _fmt_value(v), {n}) for n, v in sample.points]
        out: list[tuple[str, str, set[str]]] = []
        # By name: SPEC 2.5 drops a non-finite point, so the points are not one per channel.
        # Names are unique within a definition, lanes included. A missing point renders `-`.
        points = dict(sample.points)
        for ch in definition.channels:
            if ch.kind == "bits":
                lanes = [lane for lane in (ch.lanes or ()) if lane is not None]
                on = [lane for lane in lanes if points.get(lane)]
                out.append((ch.name, "|".join(on) or "-", {ch.name, *lanes}))
                continue
            value = points.get(ch.name)
            if value is None:
                text = "-"
            elif ch.kind == "enum":
                label = dict(ch.labels or ()).get(int(value))
                text = label if label is not None else str(int(value))
            else:
                text = _fmt_value(value) + (ch.unit or "")
            out.append((ch.name, text, {ch.name}))
        return out


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


# ASCII decimal, an exponent allowed: float() also takes other scripts' digits, `_`
# grouping, padding, `nan` and `inf`, and rewrites them into a number nobody typed.
_DECIMAL_FLOAT = re.compile(r"-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?")


def decimal_float(text: str) -> float | None:
    """`text` as a finite float when it is ASCII decimal, else None."""
    if not _DECIMAL_FLOAT.fullmatch(text):
        return None
    value = float(text)
    return value if math.isfinite(value) else None


# Typer vendors click, so these subclass the types the command tree really holds.
click_types = typer._click.types


class _AsciiNumber:
    """Click number type refusing what int()/float() would rewrite (SPEC 4).

    Integers take `protocol.int_arg`'s grammar (ASCII digits, a leading `-`), floats
    `decimal_float`'s: `mcu i2c rd 48 ٣` sent `i2c rd 48 3` to the board.
    """

    def convert(self, value: Any, param: Any, ctx: Any) -> Any:
        if isinstance(value, str):
            if isinstance(self, click_types.IntParamType):
                ok, kind = p.is_decimal_token(value.removeprefix("-")), "integer"
            else:
                ok, kind = decimal_float(value) is not None, "finite number"
            if not ok:
                self.fail(f"{value!r} is not an ASCII decimal {kind}", param, ctx)
        return super().convert(value, param, ctx)


class AsciiInt(_AsciiNumber, click_types.IntParamType):
    pass


class AsciiIntRange(_AsciiNumber, click_types.IntRange):
    pass


class AsciiFloat(_AsciiNumber, click_types.FloatParamType):
    pass


class AsciiFloatRange(_AsciiNumber, click_types.FloatRange):
    pass


def _ascii_type(t: Any) -> Any:
    """`t` with the ASCII grammar, keeping its range; any other type as it is."""
    if isinstance(t, _AsciiNumber):
        return t
    for plain, ranged, ascii_plain, ascii_ranged in (
        (click_types.IntParamType, click_types.IntRange, AsciiInt, AsciiIntRange),
        (click_types.FloatParamType, click_types.FloatRange, AsciiFloat, AsciiFloatRange),
    ):
        if isinstance(t, ranged):
            return ascii_ranged(t.min, t.max, t.min_open, t.max_open, t.clamp)
        if isinstance(t, plain):
            return ascii_plain()
    return t


class AsciiNumbersGroup(typer.core.TyperGroup):
    """The root group: every numeric option and argument below it takes ASCII decimal.

    Applied to the built tree, so a `typer.Option(min=, max=)` declared anywhere keeps its
    range and cannot be missed.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        todo: list[Any] = [self]
        while todo:
            cmd = todo.pop()
            for prm in cmd.params:
                prm.type = _ascii_type(prm.type)
            todo.extend(getattr(cmd, "commands", {}).values())


def fmt_frame(fr: dict[str, Any]) -> str:
    flags = ("x" if fr["ext"] else "") + ("r" if fr["rtr"] else "") or "-"
    # Bus 1 is unmarked, as on the wire (SPEC 2.4); a row from before the column reads as 1.
    bus = fr.get("bus", 1)
    tag = f"bus={bus} " if bus != 1 else ""
    return (
        f"{fmt_ts(fr['ts'])}  {tag}id={fr['can_id']:X} {flags} "
        f"dlc={fr['dlc']} data={fr['data_hex'] or '-'}"
    )


def note_truncated(
    body: dict[str, Any], limit: int, opt: str = "--limit",
    fallback: str = "use 'mcu log export' for every row", beyond: str = "older",
) -> None:
    """Warn on stderr when /lines capped the result set.

    `/lines` answers `{"lines": [...], "truncated": bool}`, but only --json ever showed
    the flag: a capped query read as a complete one, which is how "the error never
    happened" gets concluded from a window that simply did not reach back far enough.
    stderr keeps stdout a clean stream of rows (or of JSON) either way.

    The count reported is the one that came back, not the one that was asked for: the
    daemon caps the result set below the request, so naming the request read as "your
    limit did this" and offered "raise --limit" where raising it changes nothing. That
    remedy is only offered when the user's own limit was the binding cap.
    `opt` and `fallback` come from the caller: the remedy names options its command has.
    `beyond` is "newer" for a `--since-id` page, which walks upwards.
    """
    if not body.get("truncated"):
        return
    rows = body.get("lines")
    got = len(rows) if isinstance(rows, list) else 0
    remedy = f"raise {opt} or {fallback}" if got == limit else fallback
    err(f"note: results truncated at {got} rows; {beyond} matches exist ({remedy})")


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
    "cancelled" as "done". The prompt goes to stderr and stdin is read directly, since
    `typer.confirm` still writes to stdout even with err=True and would corrupt a --json
    consumer's parse.
    """
    if not _stdin_is_interactive():
        # No human can answer: a program that never writes one waits for ever, and one whose
        # stdin carries its own stream loses a line to the read. Refuse, naming the way through.
        die("refusing to prompt for confirmation: stdin is not a terminal; pass -y to confirm",
            1)
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
    return _isatty(sys.stdin)


def emit_cmd_result(s: Settings, res: dict[str, Any]) -> None:
    """Print a /cmd (or wait cmd) result and exit with the contract code."""
    if s.json_out:
        out_json(res)
    status = res.get("status")
    if status == "ok":
        if not s.json_out:
            print(res.get("data") or "ok")   # a command with no data still says it landed
        raise typer.Exit(0)
    if status == "timeout":
        if not s.json_out:
            err("timeout")
        raise typer.Exit(2)
    if not s.json_out:
        err(cmd_err_text(res))
    raise typer.Exit(1)


def cmd_err_text(res: dict[str, Any]) -> str:
    """`ERR <code> <name> <detail>` for a /cmd result that answered err."""
    detail = res.get("err_detail") or ""
    return f"ERR {res.get('err_code')} {res.get('err_name')} {detail}".rstrip()
