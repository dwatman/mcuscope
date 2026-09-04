"""The `mcu` command-line client: a thin HTTP client of mcuscoped (SPEC 4).

Exit-code contract (for AI use): 0 success/match, 1 error (bus ERR, HTTP error, bad
usage), 2 timeout, 3 daemon unreachable. With --json, each command prints exactly one
JSON object (streaming commands print one object per line).
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import httpx
import typer

from . import __version__, _stdio, cli_argv
from . import protocol as p
from .cli_client import DEFAULT_URL, Client, Settings, die_bad_url, error_text
from .cli_daemonctl import (
    DAEMON_START_TIMEOUT_S,
    _abandon_daemon,
    _host_port,
    _pid_file,
    _request_shutdown,  # noqa: F401  (re-exported for the tests)
    _serving_pid,
    _start_timeout_default,  # noqa: F401  (re-exported for the tests)
    _status_body,
    _stop_running_daemon,
    _write_pid_record,
)
from .cli_output import (
    ABORT_EXCEPTIONS,
    EXIT_EXCEPTIONS,
    USAGE_ERRORS,
    LineDecoder,
    _field,
    _list_field,
    _silence_stdout,
    confirm_or_exit,
    die,
    emit_cmd_result,
    emit_stream,
    err,
    finite_option,
    fmt_age,
    fmt_datetime,
    fmt_frame,
    fmt_line,
    fmt_num,
    fmt_ts,
    json_mode,
    note_truncated,
    out_json,
    parse_clock,
    positive_option,
    set_json_mode,
)

# `asyncio`, `websockets` and `platformdirs` are imported where they are used (the follow
# loop and the pid-file helper), not here. They cost about 60 ms of the CLI's ~190 ms
# startup, and every command that is not `tail -f` or `daemon start|stop` pays it for
# nothing - which matters when an agent runs `mcu` dozens of times in a session.

def settings_of(ctx: typer.Context) -> Settings:
    return ctx.obj


# -- app + global options -------------------------------------------------------------

app = typer.Typer(
    add_completion=False, no_args_is_help=True, help="mcu: hardware debug bridge CLI."
)


def _version_callback(value: bool) -> None:
    if value:
        # is_eager, so this runs before the group callback sets the output mode; the mode
        # is read from the argv split in _dispatch instead, which is why --json reaches
        # this at all. Two prose lines here used to escape the SPEC 4 promise.
        if json_mode():
            out_json({"version": __version__, "python": _stdio.python_line()})
        else:
            print(f"mcuscope {__version__}")
            print(_stdio.python_line())
        raise typer.Exit()


@app.callback()
def _global(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    port: str | None = typer.Option(None, "--port", "-p", help="Port alias (default: sole port)."),
    url: str | None = typer.Option(None, "--url", help="Daemon base URL (or env MCUSCOPE_URL)."),
    token: str | None = typer.Option(
        None, "--token", help="Access token for a remote daemon (or env MCUSCOPE_TOKEN)."
    ),
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Show the mcu client version and exit.",
    ),
) -> None:
    resolved = url or os.environ.get("MCUSCOPE_URL") or DEFAULT_URL
    resolved_token = token or os.environ.get("MCUSCOPE_TOKEN") or None
    set_json_mode(json_out)
    if resolved_token is not None and not resolved_token.isascii():
        # An HTTP header value is ASCII (SPEC 3.4's token is), and httpx refuses anything
        # else with a UnicodeEncodeError from deep inside its header encoding - a traceback
        # and a crash log where the user's mistake is right here on the command line.
        die("token must be ASCII (--token, or MCUSCOPE_TOKEN)", 1)
    ctx.obj = Settings(
        url=resolved.rstrip("/"), json_out=json_out, port=port, token=resolved_token
    )


# -- status / ports -------------------------------------------------------------------


@app.command()
def status(ctx: typer.Context) -> None:
    """Daemon and port health."""
    s = settings_of(ctx)
    body = Client(s).get("/status")
    if s.json_out:
        out_json(body)
        return
    # Only mention write failures when there are some, as with a port's drops below.
    # `.get`, because a daemon older than the counter does not send the field at all.
    write_errors = body.get("write_errors", 0)
    errs = f"  write_errors={write_errors}" if write_errors else ""
    print(
        f"mcuscoped {body['version']}  up {fmt_num(body['uptime_s'])}s  "
        f"db {body['db_path']}{errs}"
    )
    # The store's writer task is what turns received lines into rows; with it dead the
    # daemon still answers, still reads the port and still counts rx, so every other line
    # of this output looks healthy while nothing is being captured (review class 12).
    # Default True: a daemon older than the field does not send it.
    if body.get("writer_alive", True) is False:
        print("  CAPTURE STOPPED: the store writer is not running; no lines are being saved")
    # The release check (SPEC 3.6) had only one delivery, the web UI badge, which reaches
    # nobody driving the CLI - and an agent or a headless bench is the normal way to use
    # this. `.get`, because the block is absent on an older daemon and null when the check
    # is switched off.
    upd = _field(body, "update", optional=True)
    if upd and upd.get("available"):
        # The two installers README.md documents, in the same order. Not `pip install -U`:
        # plain pip is not an install path this project recommends (no isolation, and a
        # Debian/Ubuntu system python refuses it under PEP 668), so it must not be the
        # command the tool itself hands people.
        print(
            f"  update available: mcuscope {upd['latest']}  "
            f"(uv tool upgrade mcuscope, or pipx upgrade mcuscope)"
        )
    sess = _field(body, "session", optional=True)
    if sess:
        # With the start time: a named session outlives daemon runs, so one left open for
        # a week is worth noticing here (it also holds the retention floor there).
        print(f"  session: {sess['name']} (id {sess['id']}, running since "
              f"{fmt_datetime(sess['started_ts'])})")
    # Quiet when off, like drops and write errors: only an active stream is news.
    pj = _field(body, "plotjuggler", optional=True)
    if pj and pj.get("enabled"):
        print(f"  plotjuggler: streaming to {pj['dest']}")
    for pt in _list_field(body, "ports"):
        state = _port_state(pt)
        # Only mention drops when there are some; a clean capture should stay quiet.
        dropped = f" dropped={pt['rx_dropped']}" if pt.get("rx_dropped") else ""
        print(
            f"  {pt['alias']:<10} {_port_name(pt)}  @{pt['baud']}  {state}{_port_target(pt)}  "
            f"rx={pt['lines_rx']} tx={pt['lines_tx']}{dropped}"
        )


def _port_name(pt: dict) -> str:
    """The port it landed on, with pyserial's description; the requested string is in --json."""
    name = pt.get("resolved_device") or pt["device"]
    desc = pt.get("description")
    return f"{name} ({desc})" if desc else name


def _port_state(pt: dict) -> str:
    if pt.get("held"):
        return "held (disconnected on request)"
    if not pt["connected"]:
        reason = pt.get("disconnect_reason")
        return f"disconnected ({reason})" if reason else "disconnected"
    if pt.get("write_failures"):
        # RX still flowing while every write times out is not "connected" in any useful
        # sense (a V3PWR VCP did this after a target power cycle); say so with the streak.
        since = fmt_ts(pt["write_failing_since"]) if pt.get("write_failing_since") else "?"
        return f"DEGRADED: {pt['write_failures']} write failures since {since}"
    return "connected"


def _port_target(pt: dict) -> str:
    """` target=<name>` from the connect-time ping, or nothing when the board did not answer."""
    return f" target={pt['target']}" if pt.get("target") else ""


@app.command()
def ports(ctx: typer.Context) -> None:
    """List attached ports."""
    s = settings_of(ctx)
    body = Client(s).get("/ports")
    if s.json_out:
        out_json(body)
        return
    for pt in _list_field(body, "ports"):
        state = _port_state(pt)
        print(f"{pt['alias']:<10} {_port_name(pt)}  @{pt['baud']}  {state}{_port_target(pt)}")


@app.command(name="plotjuggler")
@app.command(name="pj", hidden=True)
def plotjuggler(
    ctx: typer.Context,
    state: str | None = typer.Argument(
        None, metavar="[on|off]", help="Enable or disable; omit to show the current state."
    ),
    dest: str | None = typer.Argument(
        None, metavar="[HOST:PORT]", help="UDP destination; omit to keep the current one."
    ),
    save: bool = typer.Option(
        False, "--save", help="Also write the result to the config file as the default."
    ),
) -> None:
    """Show or set UDP plot streaming to PlotJuggler (SPEC 3.7). Alias: mcu pj."""
    s = settings_of(ctx)
    client = Client(s)
    if state is None:
        if save:
            die("--save needs on or off: there is no state change to save", 1)
        body = client.get("/plotjuggler")
    else:
        if state not in ("on", "off"):
            die(f"expected 'on' or 'off', got {state!r}", 1)
        body = client.put("/plotjuggler", {"enabled": state == "on", "dest": dest})
        if save:
            client.put(
                "/config/plotjuggler",
                {"enabled": body["enabled"], "dest": body["dest"]},
            )
    if s.json_out:
        out_json(body)
        return
    word = "on" if body["enabled"] else "off"
    saved = "  (saved to config)" if save and state is not None else ""
    print(f"plotjuggler: {word}  dest {body['dest']}{saved}")


@app.command()
def devices(ctx: typer.Context) -> None:
    """List host serial devices (find /dev/ttyACM0, COMx before `mcu attach`)."""
    s = settings_of(ctx)
    body = Client(s).get("/devices")
    if s.json_out:
        out_json(body)
        return
    devs = _list_field(body, "devices")
    if not devs:
        print("no serial devices found")
        return
    for d in devs:
        vid_pid = d.get("vid_pid") or "-"
        serial = d.get("serial_number") or "-"
        by_id = d.get("by_id")
        line = f"{d['device']:<16} {d['description'] or '-':<28} {vid_pid:<10} {serial:<16}"
        if by_id:
            line += f" {by_id}"
        print(line)


def _derive_alias(device: str) -> str:
    if "://" in device:
        return "board"
    # Normalize Windows separators so \\.\COM7 and C:\...\dev yield the last component
    # on any host platform (os.path.basename only splits on the native separator).
    dev = device.replace("\\", "/")
    return os.path.basename(dev.rstrip("/")) or "board"


# The line endings an outgoing line may carry (SPEC 2.1), mirroring protocol.EOL_BYTES.
# Validated here rather than left to the daemon so a typo is bad usage (exit 1) with a
# message naming the option, not a 422 from a request that should never have been sent.
EOL_CHOICES = ("none", "lf", "crlf")


def eol_option(value: str | None) -> str | None:
    if value is not None and value not in EOL_CHOICES:
        raise typer.BadParameter(
            f"expected one of {', '.join(EOL_CHOICES)}, got {value!r}", param_hint="--eol"
        )
    return value


EOL_OPTION = typer.Option(
    None, "--eol", callback=eol_option, metavar="none|lf|crlf",
    help="Line ending for this send (default: the port's own setting). "
         "`--eol none` appends nothing, which is how a bare control character is sent.",
)


@app.command()
def attach(
    ctx: typer.Context,
    device: str = typer.Argument(..., help="Device: /dev/ttyACM0, COM7, socket://host:port"),
    baud: int = typer.Option(115200, "--baud"),
    alias: str | None = typer.Option(None, "--alias"),
    eol: str = typer.Option(
        "lf", "--eol", callback=eol_option, metavar="none|lf|crlf",
        help="Line ending this port appends to everything sent to it.",
    ),
) -> None:
    """Attach a serial port."""
    s = settings_of(ctx)
    body = {"alias": alias or _derive_alias(device), "device": device, "baud": baud,
            "eol": eol}
    res = Client(s).post("/ports", body)
    if s.json_out:
        out_json(res)
    else:
        # Attach is registration, not a connection: a device that is absent (or not yet
        # plugged in) is a supported flow, and the daemon retries in the background. The
        # message used to read as if the link were live, so a typo in a device path looked
        # like a successful attach. Reported as the daemon returns it: the open has not
        # happened yet at this point even for a device that is present.
        # "connecting", not "not connected, retrying": the open has not been attempted
        # yet when POST /ports returns, so a healthy device also reads false here and
        # must not be announced like a failure.
        port = _field(res, "port")
        state = "" if port.get("connected") else " (connecting; see 'mcu status')"
        print(f"attached {port['alias']} -> {device}{state}")


@app.command()
def detach(ctx: typer.Context, alias: str = typer.Argument(...)) -> None:
    """Detach a serial port."""
    s = settings_of(ctx)
    res = Client(s).delete(f"/ports/{alias}")
    if s.json_out:
        out_json(res)
    else:
        print(f"detached {alias}")


# -- cmd / send / mark ----------------------------------------------------------------

# Widest millisecond timeout a command may ask for, mirroring server.MAX_TIMEOUT_MS. The
# value is duplicated rather than imported so the CLI does not pull the daemon's stack in.
MAX_TIMEOUT_MS = 300_000


def timeout_ms_option(value: int | None) -> int | None:
    """Click callback bounding a millisecond timeout, refusing it as bad usage.

    Unbounded, the value went into `timeout / 1000 + 5` and reached httpx as an
    OverflowError traceback with no exit code - `finite_option` guards the two float
    timeouts for the same reason and these three were left open.
    """
    if value is not None and not 0 <= value <= MAX_TIMEOUT_MS:
        raise typer.BadParameter(f"expected 0 to {MAX_TIMEOUT_MS} ms, got {value}")
    return value


RETRY_OPTION = typer.Option(
    0, "--retry-ms", min=0,
    help="Keep retrying an `ERR 6 busy` answer until this much time has passed "
         "(each attempt still waits its own --timeout).",
)


@app.command()
def cmd(
    ctx: typer.Context,
    text: str = typer.Argument(..., help='Command without ">" and seq, e.g. "i2c rd 48 2"'),
    timeout: int = typer.Option(
        1000, "--timeout", help="Response timeout in ms.", callback=timeout_ms_option
    ),
    retry_ms: int = RETRY_OPTION,
    eol: str | None = EOL_OPTION,
) -> None:
    """Send a monitor command and print its response."""
    _run_cmd(ctx, text, timeout, retry_ms, eol)


@app.command()
def send(
    ctx: typer.Context,
    text: str = typer.Argument(...),
    eol: str | None = EOL_OPTION,
) -> None:
    """Write one raw line (no response wait)."""
    s = settings_of(ctx)
    res = Client(s).post("/send", {"port": s.port, "line": text, "eol": eol})
    if s.json_out:
        out_json(res)
    else:
        print("ok")


# A break is bounded by the daemon at 1..2000 ms; bounded here too, so an out-of-range
# value is bad usage rather than a 422 from a request that need not have been sent.
BREAK_MS_OPTION = typer.Option(
    250, "--ms", min=1, max=2000, help="How long to hold the line in break, in ms."
)


@app.command(name="break")
def break_(ctx: typer.Context, ms: int = BREAK_MS_OPTION) -> None:
    """Send a serial break: hold the TX line low, the way a terminal's Ctrl-Break does."""
    s = settings_of(ctx)
    res = Client(s).post("/break", {"port": s.port, "ms": ms})
    if s.json_out:
        out_json(res)
    else:
        print(f"break {ms} ms")


@app.command()
def sysrq(
    ctx: typer.Context,
    char: str = typer.Argument(..., help="One SysRq key: b reboot, t tasks, w blocked tasks."),
    ms: int = BREAK_MS_OPTION,
) -> None:
    """Linux magic SysRq over a serial console: a break, then one character.

    The target's kernel must have SysRq enabled (`kernel.sysrq`) and its console on this
    UART; without both, the break and the character are simply ignored.
    """
    if len(char) != 1:
        # One character, because the break is the SysRq *modifier*: a second character
        # would arrive as ordinary console input, so "reboot" would type "eboot".
        die(f"sysrq takes exactly one character, got {char!r}", 1)
    s = settings_of(ctx)
    client = Client(s)
    client.post("/break", {"port": s.port, "ms": ms})
    client.post("/send", {"port": s.port, "line": char, "eol": "none"})
    if s.json_out:
        out_json({"ok": True, "char": char, "ms": ms})
    else:
        print(f"sysrq {char} (break {ms} ms)")


@app.command()
def mark(ctx: typer.Context, text: str = typer.Argument(...)) -> None:
    """Insert a marker annotation."""
    s = settings_of(ctx)
    res = Client(s).post("/marker", {"port": s.port, "text": text})
    if s.json_out:
        out_json(res)
    else:
        print(f"marker {res['line_id']}")


# -- lines / tail / wait / log --------------------------------------------------------


LINES_PAGE = 1000   # the /lines cap (SPEC 4); the CLI pages past it
DEF_LOOKBACK = 20000   # rows before a window's end searched for its !pd definitions


def _lines_params(
    s: Settings, chan: str | None, match: str | None, last_ms: int | None,
    limit: int, since_id: int | None, session: str | None = None,
    since_ts: float | None = None, id_to: int | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if s.port:
        params["port"] = s.port
    if chan:
        params["chan"] = chan
    if match:
        params["match"] = match
    if last_ms is not None:
        params["last_ms"] = last_ms
    if since_id is not None:
        params["since_id"] = since_id
    if session:
        params["session"] = session
    if since_ts is not None:
        params["since_ts"] = since_ts
    if id_to is not None:
        params["id_to"] = id_to
    return params


def _fetch_lines(s: Settings, params: dict[str, Any], limit: int) -> dict[str, Any]:
    """GET /lines for the newest `limit` rows, paging past the endpoint's 1000-row cap.

    Pages walk `id_to` downwards, so every filter applies unchanged to each page. The
    result has the endpoint's shape (`lines` newest first, `truncated`), with `truncated`
    meaning rows exist beyond the `limit` asked for, not beyond one page.
    """
    rows: list[dict[str, Any]] = []
    params = dict(params)
    while True:
        # Always at least one request: `limit=0` is the "no backfill" probe, and its
        # `truncated` flag is the whole answer (SPEC 4).
        params["limit"] = min(LINES_PAGE, limit - len(rows))
        body = Client(s).get("/lines", params=params)
        page = _list_field(body, "lines")
        rows.extend(page)
        truncated = bool(body.get("truncated"))
        if not truncated or not page or len(rows) >= limit:
            break
        oldest = page[-1].get("id") if isinstance(page[-1], dict) else None
        if not isinstance(oldest, int) or oldest <= 1:
            break
        params["id_to"] = oldest - 1
    return {"lines": rows, "truncated": truncated}


def _iter_pages_asc(s: Settings, params: dict[str, Any]) -> Iterator[list[dict[str, Any]]]:
    """Pages of matching rows, oldest first, until the window is exhausted (exports)."""
    params = {**params, "order": "asc", "limit": LINES_PAGE}
    while True:
        body = Client(s).get("/lines", params=params)
        page = _list_field(body, "lines")
        yield page
        last = page[-1].get("id") if page and isinstance(page[-1], dict) else None
        if not body.get("truncated") or not isinstance(last, int):
            return
        params["since_id"] = last


def _absolute_window(since_ts: float | None, last_ms: int | None) -> float | None:
    """`--last-ms` as a fixed `since_ts`, taken once before paging.

    The daemon evaluates `last_ms` against its clock per request, so a paged query would
    slide its old edge forward by however long the earlier pages took, and drop rows
    there while reporting the export complete.
    """
    if last_ms is None:
        return since_ts
    cut = time.time() - last_ms / 1000
    return cut if since_ts is None else max(since_ts, cut)


EMPTY_WINDOW = {"lines": [], "truncated": False}


def _clock_bounds(
    s: Settings, from_: str | None, to: str | None, session: str | None
) -> tuple[float | None, int | None, bool]:
    """--from as `since_ts`; --to as the `id_to` just before the first row after it.

    The third value is True when nothing can precede --to (the capture's first row is
    already past it), which no `id_to` can express: the endpoint takes 1 or more.
    """
    since_ts = parse_clock(from_) if from_ else None
    if to is None:
        return since_ts, None, False
    to_ts = parse_clock(to)
    if since_ts is not None and since_ts > to_ts:
        # Silent emptiness reads as "nothing happened"; backwards bounds are a mistake
        # (an overnight window needs the date form, since bare clocks are today's).
        raise typer.BadParameter(f"--from {from_} is after --to {to}", param_hint="--to")
    params = _lines_params(s, None, None, None, 1, None, session, since_ts=to_ts)
    params["order"] = "asc"
    first = _list_field(Client(s).get("/lines", params=params), "lines")
    if not first:
        return since_ts, None, False   # nothing after --to: no upper bound needed
    first_id = first[0].get("id") if isinstance(first[0], dict) else None
    if not isinstance(first_id, int):
        die("daemon answered /lines with a row that has no id", 1)
    if first_id <= 1:
        return since_ts, None, True
    return since_ts, first_id - 1, False


def _make_decoder(
    s: Settings, decode: bool, changes: bool, names: str | None, session: str | None,
    id_to: int | None = None,
) -> LineDecoder | None:
    """A primed LineDecoder for --decode, or None when decoding is off.

    Primed with the newest definitions as of `id_to` (the window's last row), so a run
    recorded before a reflash decodes against the firmware that produced it, and a window
    shorter than the 5 s !pd rebroadcast still decodes at all. Anything redeclared inside
    the window is learned as it streams past, which is why rows must be fed oldest first.
    """
    if not (decode or changes or names):
        return None
    wanted = [n for n in names.split(",") if n] if names else None
    dec = LineDecoder(names=wanted, changes=changes)
    if id_to is None:   # live (tail): as of the newest row
        newest = _list_field(Client(s).get("/lines", params={"limit": 1}), "lines")
        id_to = newest[0]["id"] if newest else None
    # Bounded the way the daemon bounds its own priming (serial_link.PLOT_DEF_LOOKBACK):
    # an unbounded `match` walks every event row back to id 1 on a capture with no plot
    # streams, against the store's regex budget.
    since_id = max(0, id_to - DEF_LOOKBACK) if id_to is not None else None
    params = _lines_params(s, "event", "^!pd ", None, 40, since_id, session, id_to=id_to)
    dec.prime(r["raw"] for r in _list_field(Client(s).get("/lines", params=params), "lines"))
    return dec


def _decode_pages(
    s: Settings, pages: Iterable[list[dict[str, Any]]], decode: bool, changes: bool,
    names: str | None, session: str | None, filtered: bool = False,
) -> Iterator[dict[str, Any]]:
    """Rows from chronological `pages`, decoded (or as they are when decoding is off).

    Definitions are primed as of the window's *first* row and every `!pd` inside the
    window is learned as it streams past, so a stream redefined mid-window (same wire
    width, new names) decodes each half with its own definition. `filtered` means a
    `--match`/`--chan` kept those `!pd` rows out of the pages, so each page's id range
    is asked for them separately.
    """
    dec: LineDecoder | None = None
    primed = False
    for page in pages:
        rows = [r for r in page if isinstance(r, dict)]
        ids = [r["id"] for r in rows if isinstance(r.get("id"), int)]
        if not primed and ids:
            dec = _make_decoder(s, decode, changes, names, session, id_to=ids[0])
            primed = True
        if dec is None:
            yield from rows
            continue
        defs: list[dict[str, Any]] = []
        if filtered and ids:
            params = _lines_params(
                s, "event", "^!pd ", None, LINES_PAGE, ids[0] - 1, session, id_to=ids[-1]
            )
            defs = [d for pg in _iter_pages_asc(s, params) for d in pg]
        di = 0
        for row in rows:
            rid = row.get("id")
            while di < len(defs) and isinstance(rid, int) and defs[di]["id"] < rid:
                dec.decode(defs[di]["raw"])   # learn only; it is not one of the rows
                di += 1
            out = _decoded_row(dec, row)
            if out is not None:
                yield out


def _decoded_row(dec: LineDecoder | None, row: dict[str, Any]) -> dict[str, Any] | None:
    """`row` with its raw text decoded (in `raw`, and `decoded` for --json); None to drop."""
    if dec is None:
        return row
    text = dec.decode(row["raw"])
    if text is None:
        return None
    return {**row, "raw": text, "decoded": text}


DECODE_OPTION = typer.Option(
    False, "--decode", help="Render !ps/!p samples as named fields (enums, bit lanes, units)."
)
CHANGES_OPTION = typer.Option(
    False, "--changes", help="With --decode: print a sample only when a rendered field changed."
)
NAMES_OPTION = typer.Option(
    None, "--names", help="With --decode: comma-separated field or lane names to render."
)
FROM_OPTION = typer.Option(
    None, "--from", help="Wall-clock lower bound, HH:MM[:SS[.mmm]] today or ISO date-time.",
)
TO_OPTION = typer.Option(None, "--to", help="Wall-clock upper bound, same forms as --from.")


@app.command()
def lines(
    ctx: typer.Context,
    last_ms: int | None = typer.Option(None, "--last-ms"),
    from_: str | None = FROM_OPTION,
    to: str | None = TO_OPTION,
    chan: str | None = typer.Option(None, "--chan"),
    match: str | None = typer.Option(None, "--match"),
    limit: int = typer.Option(100, "--limit"),
    since_id: int | None = typer.Option(None, "--since-id"),
    session: str | None = typer.Option(None, "--session", help="Scope to a session name/id."),
    decode: bool = DECODE_OPTION,
    changes: bool = CHANGES_OPTION,
    names: str | None = NAMES_OPTION,
) -> None:
    """Query the capture (the AI workhorse). Text is oldest first; --json newest first."""
    s = settings_of(ctx)
    since_ts, id_to, empty = _clock_bounds(s, from_, to, session)
    since_ts = _absolute_window(since_ts, last_ms)
    params = _lines_params(s, chan, match, None, limit, since_id, session, since_ts, id_to)
    body = EMPTY_WINDOW if empty else _fetch_lines(s, params, limit)
    rows = list(_decode_pages(
        s, [body["lines"][::-1]], decode, changes, names, session, bool(match or chan)
    ))   # oldest first
    if s.json_out:
        out_json({"lines": rows[::-1], "truncated": body["truncated"]})   # the API's order
        return
    for row in rows:
        print(fmt_line(row))
    note_truncated(body, limit)


@app.command()
def tail(
    ctx: typer.Context,
    n: int = typer.Option(20, "-n", help="Number of recent lines to show first."),
    follow: bool = typer.Option(False, "-f", "--follow", help="Follow live via WebSocket."),
    chan: str | None = typer.Option(None, "--chan"),
    match: str | None = typer.Option(None, "--match"),
    decode: bool = DECODE_OPTION,
    changes: bool = CHANGES_OPTION,
    names: str | None = NAMES_OPTION,
) -> None:
    """Show recent lines, optionally following live."""
    s = settings_of(ctx)
    dec = _make_decoder(s, decode, changes, names, None)
    if not follow:
        _tail_snapshot(s, chan, match, n, dec)
        return
    # Subscribe *first*, then take the snapshot. The other order silently lost every line
    # that landed between the GET /lines answer and the /ws subscription: the follow only
    # ever saw what arrived after it connected. With the socket already open those lines
    # are staged in memory while the snapshot prints, then replayed after it and deduped
    # by row id - the order the web UI's backfill uses, for the same reason.
    _follow_ws(s, chan, match, backfill=lambda: _tail_snapshot(s, chan, match, n, dec), dec=dec)


def _tail_snapshot(
    s: Settings, chan: str | None, match: str | None, n: int, dec: LineDecoder | None = None
) -> int:
    """Print the recent-lines snapshot, oldest first. Returns the newest id printed.

    That id is the follow's dedupe watermark; 0 when the snapshot was empty (or carried
    no ids), which lets the follow replay everything it staged.
    """
    params = _lines_params(s, chan, match, None, n, None)
    body = _fetch_lines(s, params, n)
    watermark = 0
    for row in body["lines"]:
        rid = row.get("id") if isinstance(row, dict) else None
        if isinstance(rid, int) and rid > watermark:
            watermark = rid
    rows: Iterable[dict[str, Any]] = body["lines"][::-1]   # oldest first for reading
    if dec is not None:
        # `dec` says decoding is on and how; the snapshot primes as of its own window,
        # and the follow keeps `dec` itself live from the watermark on.
        rows = _decode_pages(
            s, [list(rows)], True, dec.changes, dec.names, None, bool(chan or match)
        )
    for row in rows:
        out_json(row) if s.json_out else print(fmt_line(row))
    note_truncated(body, n)   # stderr, so a JSONL stdout stream stays parseable
    return watermark


# Ceiling on one client-side `--match` search, mirroring store.MATCH_TIMEOUT_S. The value is
# duplicated rather than imported so the CLI does not pull the daemon's SQLite stack in.
FOLLOW_MATCH_TIMEOUT_S = 0.25


def _follow_match(pat, raw: str) -> bool:
    """Apply a user `--match` pattern to one row under a timeout.

    The daemon runs every user pattern with `regex` *and* a `timeout=` (store._make_regexp);
    only the engine came across to the client. Adopting `regex` alone gains nothing here,
    because the point of the engine is that its timeout works: `(a|a)+$` against 30 characters
    hangs the follow with no error and no exit code, and Ctrl-C does not land while the match
    is running.
    """
    try:
        return pat.search(raw, timeout=FOLLOW_MATCH_TIMEOUT_S) is not None
    except TimeoutError:
        die(f"--match pattern too slow (over {FOLLOW_MATCH_TIMEOUT_S}s on one line)", 1)
        return False        # unreachable; die() raises


class _DropCounter:
    """Per-episode reporting for a follow loop that skips bad items.

    A follow runs until Ctrl-C, so a message per bad item would bury the stream (and a
    silent skip hides that data was lost). One line when an episode of failures starts,
    one when it ends with the total, both on stderr so `--json` stdout stays parseable
    (SPEC 4).
    """

    def __init__(self, item: str) -> None:
        self.item = item
        self.total = 0
        self.episode = 0   # consecutive failures, so a caller can stop retrying

    def bad(self, exc: Exception) -> None:
        self.total += 1
        self.episode += 1
        if self.episode == 1:
            err(f"warning: skipping bad {self.item}: {exc}")

    def ok(self) -> None:
        if self.episode > 1:
            err(f"warning: skipped {self.episode} {self.item}s")
        self.episode = 0


def _new_rows(rows: list[Any], watermark: int) -> list[Any]:
    """The rows of one staged frame that the snapshot has not already printed.

    A follow subscribes before it fetches its snapshot, so the frames staged during the
    fetch overlap it. A row is a duplicate only when it carries an id at or below the
    newest id printed. Control objects ({"gap": n}, {"capture": ...}) and rows that lost
    their id have no id to compare and are always kept, so the follow loop still reports
    or charges them exactly as it does live. Arrival order is capture order and stands.
    """
    if watermark <= 0:
        return rows
    return [
        row for row in rows
        if not (
            isinstance(row, dict)
            and isinstance(row.get("id"), int)
            and not isinstance(row.get("id"), bool)
            and row["id"] <= watermark
        )
    ]


async def _stage_backfill(ws: Any, backfill: Callable[[], int]) -> tuple[int, list, Any]:
    """Run the snapshot with the socket already open, staging whatever arrives.

    Returns `(watermark, staged payloads, a recv already in flight)`. The recv is handed
    back rather than cancelled so no frame is dropped on the way into the live loop; the
    caller awaits it as its first payload. Frames are drained into memory rather than
    left in the socket, because the websockets receive queue is small and applying TCP
    backpressure to the daemon during the snapshot is what makes it shed lines instead.

    A receive that fails mid-snapshot (the daemon went away) does not cancel the
    snapshot: it is awaited so its rows still print, and the failed recv comes back as
    the pending one, so the close surfaces through the loop's own handlers.
    """
    import asyncio

    task = asyncio.create_task(asyncio.to_thread(backfill))
    staged: list = []
    recv = asyncio.create_task(ws.recv())
    try:
        while True:
            done, _ = await asyncio.wait({recv, task}, return_when=asyncio.FIRST_COMPLETED)
            if recv in done:
                if recv.exception() is not None:
                    return await task, staged, recv
                staged.append(recv.result())
                recv = asyncio.create_task(ws.recv())
            if task in done:
                return task.result(), staged, recv
    except BaseException:
        # The snapshot raising (the reader closed the pipe mid-print, exit 0 territory)
        # must not orphan the in-flight recv: left unawaited, it resolves with the
        # ConnectionClosed of the socket teardown and asyncio prints a "Task exception
        # was never retrieved" traceback to stderr at loop shutdown - which turned this
        # very exit path into test-visible noise on the CI runners. Consume it here.
        recv.cancel()
        try:
            await recv
        except BaseException:
            pass
        raise


def _follow_ws(
    s: Settings, chan: str | None, match: str | None,
    backfill: Callable[[], int] | None = None, dec: LineDecoder | None = None,
) -> None:
    import asyncio

    import regex
    import websockets

    ws_url = s.url.replace("http", "ws", 1) + "/ws"
    if s.port:
        ws_url += f"?port={s.port}"
    # `regex`, not stdlib `re`, so --match means the same thing here as it does in the
    # daemon (which compiles every user pattern with it): `\p{L}` matched the first
    # batch through GET /lines and then killed the follow with a re.error traceback.
    try:
        pat = regex.compile(match) if match else None
    except regex.error as exc:
        die(f"bad --match pattern: {exc}", 1)

    headers = s.headers()

    async def run() -> None:
        drops = _DropCounter("frame")
        watermark = 0

        def handle(payload: Any) -> None:
            # A malformed frame or row is charged to that item, never to the follow: one
            # bad frame used to end `mcu tail -f` outright. Only parsing is guarded - a
            # closed connection is not a bad frame, and stays with the outer handlers.
            try:
                # Each frame is an array of rows (SPEC 3.4); a bare object is still
                # accepted so the CLI works against an older daemon.
                msg = json.loads(payload)
                rows = msg if isinstance(msg, list) else [msg]
            except (json.JSONDecodeError, ValueError) as exc:
                # ValueError as well as its JSONDecodeError subclass, like every sibling
                # guard in cli_client: a binary frame whose bytes are not valid UTF-8
                # raises UnicodeDecodeError, which cleared a JSONDecodeError-only guard.
                drops.bad(exc)
                return
            before = drops.total
            for row in _new_rows(rows, watermark):
                # Control objects, not lines (SPEC 3.4). Recognised by their own key
                # rather than by the absence of "id", so a row that merely lost its id is
                # still charged as malformed. Without this the follow ran row["chan"] on
                # them and charged the KeyError to the drop counter, so a shed-rows notice
                # printed as "skipping bad frame: 'chan'" and hid the very thing it was
                # sent to report.
                if isinstance(row, dict) and "id" not in row and (
                    "gap" in row or "capture" in row
                ):
                    if "gap" in row:
                        err(f"warning: daemon shed {row['gap']} line(s) "
                            f"to this subscriber")
                    continue
                try:
                    if dec is not None and row["raw"].startswith("!pd"):
                        dec.decode(row["raw"])   # learn it even where the filters hide it
                    if chan and row["chan"] != chan:
                        continue
                    if pat and not _follow_match(pat, row["raw"]):
                        continue
                    row = _decoded_row(dec, row)
                    if row is None:
                        continue
                    text = json.dumps(row) if s.json_out else fmt_line(row)
                except (KeyError, TypeError, ValueError) as exc:
                    drops.bad(exc)
                    continue
                emit_stream(text)   # outside the guard: EPIPE ends the follow
            if drops.total == before:
                drops.ok()

        try:
            async with websockets.connect(ws_url, additional_headers=headers or None) as ws:
                pending = None
                try:
                    if backfill is not None:
                        watermark, staged, pending = await _stage_backfill(ws, backfill)
                        for payload in staged:
                            handle(payload)
                    while True:
                        if pending is not None:
                            recv, pending = pending, None
                            payload = await recv   # the staging loop's last recv
                        else:
                            payload = await ws.recv()
                        handle(payload)
                except BaseException:
                    # Same discipline as _stage_backfill's own cleanup: a staged drain
                    # that raises (the reader closed the pipe) must consume the recv it
                    # was handed, or the socket teardown resolves it unretrieved and
                    # asyncio tracebacks to stderr on exit.
                    if pending is not None:
                        pending.cancel()
                        try:
                            await pending
                        except BaseException:
                            pass
                    raise
        except BrokenPipeError:
            raise                       # handled in main(): the reader closed the pipe, exit 0
        except OSError as exc:
            die(f"daemon unreachable at {s.url}: {exc}", 3)
        except websockets.exceptions.ConnectionClosed as exc:
            # The daemon restarted or shut down under a live follow. That is an ordinary
            # end of stream, not a crash: this used to escape as a 6 KB rich traceback
            # because websockets' exceptions derive from Exception, not OSError.
            # `exc.rcvd`, not the deprecated `exc.code`: only a close the daemon sent
            # carries a policy code at all.
            if exc.rcvd is not None and exc.rcvd.code == 1008:
                # Host, same-origin or token guard (SPEC 3.4). The daemon is plainly
                # there, and the same refusal over REST exits 1, so 3 (unreachable) would
                # be a lie in both directions. 1013 is capacity, and stays 3.
                die("stream refused by daemon: not authorised", 1)
            die("stream closed by daemon", 3)
        except websockets.exceptions.InvalidStatus as exc:
            status = exc.response.status_code
            die(f"websocket refused by daemon: HTTP {status}",
                1 if status in (401, 403) else 3)
        except websockets.exceptions.WebSocketException as exc:
            die(f"websocket error: {exc}", 3)
        except (json.JSONDecodeError, ValueError) as exc:
            # Same widening as the per-frame guard above: a frame that cannot even be
            # decoded to text is a malformed frame, not a traceback.
            die(f"malformed frame from daemon: {exc}", 1)
        except KeyError as exc:
            die(f"unexpected row shape from daemon: missing {exc}", 1)
        finally:
            drops.ok()   # report an episode still open when the follow ends

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        raise typer.Exit(0) from None


@app.command()
def wait(
    ctx: typer.Context,
    match: str = typer.Option(..., "--match", help="Regex to match against raw lines."),
    timeout: int = typer.Option(
        2000, "--timeout", help="Timeout in ms.", callback=timeout_ms_option
    ),
    send_cmd: str | None = typer.Option(None, "--send", help="Send this first, then wait."),
    chan: str | None = typer.Option(None, "--chan"),
    raw: bool = typer.Option(False, "--raw", help="Treat --send as a raw line, not a command."),
    eol: str | None = EOL_OPTION,
    repeat_ms: int | None = typer.Option(
        None, "--repeat-ms",
        help="Resend --send every N ms until the match (implies --raw).",
    ),
) -> None:
    """Wait for a line matching a regex, optionally sending first (the AI primitive)."""
    s = settings_of(ctx)
    # --repeat-ms implies --raw: what it is for is spraying a keystroke at a bootloader,
    # and a monitor command carries a seq that must not be reused across writes.
    raw = raw or repeat_ms is not None
    if repeat_ms is not None:
        refusal = p.repeat_refusal(
            repeat_ms, timeout, has_send=send_cmd is not None, raw=raw
        )
        # Refused here in the daemon's own words, so the two answers read alike and the
        # round trip is skipped for a value that can never be accepted.
        if refusal is not None:
            die(f"error: {refusal}", 1)
    body: dict[str, Any] = {"port": s.port, "match": match, "timeout_ms": timeout, "chan": chan}
    if send_cmd is not None:
        body["send"] = send_cmd
        body["send_mode"] = "raw" if raw else "cmd"
        body["eol"] = eol
    if repeat_ms is not None:
        body["repeat_ms"] = repeat_ms
    res = Client(s).post("/wait", body, timeout=timeout / 1000 + 5)
    # A wait whose feed shed rows has not seen the whole window, so a "timeout" from it is
    # not a clean negative. Always to stderr, so --json stdout stays one document (SPEC 4).
    if res.get("dropped"):
        err(f"warning: {res['dropped']} lines were shed while waiting; "
            "the result may be a false negative, so retry rather than trust it")
    if s.json_out:
        out_json(res)
    if repeat_ms is not None and not s.json_out:
        # Stderr, so --json stdout stays one document and a match still prints only the line.
        err(f"sent {res['sends']} times, {res['send_failures']} writes failed")
    if res["status"] == "match":
        if not s.json_out:
            print(fmt_line(res["line"]))
        raise typer.Exit(0)
    if not s.json_out:
        err("timeout")
    raise typer.Exit(2)


@app.command(name="assert")
def assert_(
    ctx: typer.Context,
    expect: list[str] = typer.Option(  # noqa: B008 - typer option factory
        [], "--expect", help="Regex that MUST match at least once. Repeatable."
    ),
    forbid: list[str] = typer.Option(  # noqa: B008 - typer option factory
        [], "--forbid", help="Regex that must NEVER match. Repeatable."
    ),
    timeout: int = typer.Option(
        0, "--timeout", help="Live window in ms. Omit to judge already-captured lines.",
        callback=timeout_ms_option,
    ),
    min_window: int = typer.Option(
        0, "--min-window",
        help="Keep a live window open at least this long (ms) even once --expect is met.",
    ),
    session: str | None = typer.Option(None, "--session", help="Judge a stored session."),
    last_ms: int | None = typer.Option(None, "--last-ms", help="Judge the last N ms."),
    send_cmd: str | None = typer.Option(None, "--send", help="Send this first (live mode)."),
    chan: str | None = typer.Option(None, "--chan"),
    raw: bool = typer.Option(False, "--raw", help="Treat --send as a raw line, not a command."),
    eol: str | None = EOL_OPTION,
) -> None:
    """Judge a capture window: every --expect seen, no --forbid seen. Exit 0 pass, 1 fail.

    Where `wait` answers "did this line appear?", `assert` answers "did this run pass?":
    several conditions at once, negative conditions included, reduced to one verdict an
    agent or a CI job can act on without reading the log.

    Retrospective (the default) judges lines already stored, so a run can be checked after
    the fact - `--session boot-test` turns last week's capture into a test oracle. With
    `--timeout` it judges a live window instead, optionally sending something first.

    A live window closes as soon as every --expect is met, which would leave --forbid
    judged over only the span the expects happened to take. `--min-window` holds it open
    for a stated period regardless: "boot within 20 s, and stay clean for at least 10" is
    `--expect 'BOOT OK' --forbid ERR --min-window 10000 --timeout 20000`.
    """
    s = settings_of(ctx)
    if not expect and not forbid:
        die("at least one --expect or --forbid is required", 1)
    body: dict[str, Any] = {
        "expect": list(expect), "forbid": list(forbid),
        "timeout_ms": timeout, "min_window_ms": min_window, "chan": chan, "port": s.port,
    }
    if session:
        body["session"] = session
    if last_ms is not None:
        body["last_ms"] = last_ms
    if send_cmd is not None:
        body["send"] = send_cmd
        body["send_mode"] = "raw" if raw else "cmd"
        body["eol"] = eol
    # timeout_code=1: SPEC 4 states `mcu assert` never exits 2, and a transport timeout
    # (loaded or wedged daemon) was the one path that still could.
    res = Client(s).post("/assert", body, timeout=timeout / 1000 + 30, timeout_code=1)
    # Same as `wait`: a window with holes in it has not been judged over that window, and a
    # forbid that "did not match" over it is the dangerous direction.
    if res.get("dropped"):
        err(f"warning: {res['dropped']} lines were shed during the window; "
            "the verdict does not cover them, so retry rather than trust it")
    if s.json_out:
        out_json(res)
    else:
        for check in _list_field(res, "expect"):
            if check["matched"]:
                print(f"  ok      expect {check['pattern']!r}: {_field(check, 'line')['raw']}")
            else:
                err(f"  FAILED  expect {check['pattern']!r}: never seen")
        for check in _list_field(res, "forbid"):
            if check["matched"]:
                err(f"  FAILED  forbid {check['pattern']!r}: {_field(check, 'line')['raw']}")
            else:
                print(f"  ok      forbid {check['pattern']!r}: never seen")
        verdict = "PASS" if res["status"] == "pass" else "FAIL"
        print(f"{verdict}  {res['checked_lines']} lines checked in "
              f"{fmt_num(res['elapsed_ms'])} ms")
    raise typer.Exit(0 if res["status"] == "pass" else 1)


session_app = typer.Typer(help="Name a span of the capture so a run can be queried later.")
app.add_typer(session_app, name="session")


@session_app.command("start")
def session_start(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Session name, e.g. 'boot-test'."),
    note: str = typer.Option("", "--note", help="Free-text description of the run."),
) -> None:
    """Start a session (closes any running one). Everything captured from now belongs to it."""
    s = settings_of(ctx)
    res = Client(s).post("/sessions", {"name": name, "note": note})
    if s.json_out:
        out_json(res)
    else:
        sess = _field(res, "session")
        print(f"session {sess['id']} started: {sess['name']}")


@session_app.command("stop")
def session_stop(ctx: typer.Context) -> None:
    """Close the running session."""
    s = settings_of(ctx)
    res = Client(s).post("/sessions/stop", {})
    if s.json_out:
        out_json(res)
    else:
        sess = _field(res, "session")
        print(f"session {sess['id']} ended: {sess['name']} "
              f"(lines {sess['start_id']}-{sess['end_id']})")


@session_app.command("list")
def session_list(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """List recent sessions, newest first."""
    s = settings_of(ctx)
    body = Client(s).get("/sessions", params={"limit": limit})
    if s.json_out:
        out_json(body)
        return
    sessions = _list_field(body, "sessions")
    if not sessions:
        print("no sessions recorded")
        return
    for sess in sessions:
        state = "running" if sess["ended_ts"] is None else "ended"
        kind = "auto" if sess.get("auto") else "named"
        note = f"  {sess['note']}" if sess["note"] else ""
        print(
            f"{sess['id']:<5} {sess['name']:<26} {fmt_datetime(sess['started_ts'])} "
            f"{kind:<6} {state:<8} {sess['lines']} lines{note}"
        )


@session_app.command("export")
def session_export(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Session name or id."),
    out_file: str = typer.Option(..., "-o", "--out", help="Destination .db path."),
) -> None:
    """Save one session as a standalone capture database.

    The file is a normal MCUscope capture, so an archived run stays queryable with the
    same tools as the live one instead of becoming a dead format.
    """
    s = settings_of(ctx)
    written = Client(s).download(f"/sessions/{name}/export", out_file)
    if s.json_out:
        out_json({"file": out_file, "bytes": written})
    else:
        print(f"wrote {written} bytes to {out_file}")


@session_app.command("delete")
def session_delete(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Session name or id."),
    data: bool = typer.Option(False, "--data", help="Also delete the lines it covers."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Delete a session label, and with --data the capture it covers."""
    s = settings_of(ctx)
    # name= resolves server-side through the sessions name index, so a session past the
    # first page is still found (paging the list was capped at the endpoint's own 1000).
    body = Client(s).get("/sessions", params={"name": name})
    # The identity is re-checked here: a daemon too old to know `name=` ignores it and
    # answers the default page, whose first row is the newest session, and deleting that
    # is not what was asked for.
    match = next(
        (x for x in _list_field(body, "sessions") if str(x["id"]) == name or x["name"] == name),
        None,
    )
    if match is None:
        die(f"no such session: {name}", 1)
    if data and not yes:
        confirm_or_exit(
            f"delete session {match['name']} and its {match['lines']} captured lines?"
        )
    res = Client(s).delete(f"/sessions/{match['id']}", params={"data": str(bool(data)).lower()})
    if s.json_out:
        out_json(res)
    else:
        print(f"deleted session {match['name']} ({res['lines_deleted']} lines)")


def _ids_clause(preview: dict[str, Any]) -> str:
    """The " (ids A-B)" part of a purge count, empty when the selection is empty.

    /purge answers null for both ends when nothing matches, which reached the user as
    "would delete 0 lines (ids None-None)".
    """
    lo, hi = preview.get("id_from"), preview.get("id_to")
    return "" if lo is None or hi is None else f" (ids {lo}-{hi})"


@app.command()
def purge(
    ctx: typer.Context,
    session: str | None = typer.Option(None, "--session", help="Delete a session's lines."),
    before_days: float | None = typer.Option(
        None, "--before-days", help="Delete lines older than N days.", callback=finite_option
    ),
    id_from: int | None = typer.Option(None, "--id-from", help="Delete from this line id."),
    id_to: int | None = typer.Option(None, "--id-to", help="Delete up to this line id."),
    all_: bool = typer.Option(False, "--all", help="Delete the entire capture."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would go, delete nothing."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Delete captured lines by session, age, or id range. Not recoverable.

    Retention only ever truncates the oldest end of the capture; this removes exactly the
    span asked for, so a big useless run can go without waiting for it to age out. The
    count is always shown before the delete: pass --dry-run to see it and stop there.
    """
    s = settings_of(ctx)
    selectors = [session is not None, before_days is not None,
                 id_from is not None or id_to is not None, all_]
    if sum(selectors) != 1:
        die("exactly one of --session, --before-days, --id-from/--id-to, --all is required", 1)
    if before_days is not None and before_days <= 0:
        # A negative puts before_ts in the future, so "older than N days" selects the whole
        # capture - an unlabelled second route to --all, reachable from one mistyped sign.
        die("--before-days must be greater than 0 (use --all to delete everything)", 1)
    body: dict[str, Any] = {"dry_run": True}
    if session is not None:
        body["session"] = session
    elif before_days is not None:
        body["before_ts"] = time.time() - before_days * 86400
    elif all_:
        body["all"] = True
    else:
        body["id_from"] = id_from
        body["id_to"] = id_to

    client = Client(s)
    preview = client.post("/purge", body, timeout=120.0)
    if dry_run:
        if s.json_out:
            out_json(preview)
        else:
            print(f"would delete {preview['deleted']} lines{_ids_clause(preview)}")
        raise typer.Exit(0)
    if preview["deleted"] == 0:
        if s.json_out:
            out_json(preview)
        else:
            print("nothing to delete")
        raise typer.Exit(0)
    if not yes:
        confirm_or_exit(
            f"permanently delete {preview['deleted']} lines{_ids_clause(preview)}?"
        )
    body["dry_run"] = False
    res = client.post("/purge", body, timeout=600.0)
    if s.json_out:
        out_json(res)
    else:
        print(f"deleted {res['deleted']} lines")


log_app = typer.Typer(help="Export captured lines.")
app.add_typer(log_app, name="log")


@log_app.command("export")
def log_export(
    ctx: typer.Context,
    last_ms: int | None = typer.Option(None, "--last-ms"),
    from_: str | None = FROM_OPTION,
    to: str | None = TO_OPTION,
    chan: str | None = typer.Option(None, "--chan"),
    match: str | None = typer.Option(None, "--match"),
    limit: int = typer.Option(
        0, "--limit", min=0, help="Newest N matching rows; 0 (default) means every row."
    ),
    session: str | None = typer.Option(None, "--session", help="Scope to a session name/id."),
    out_file: str | None = typer.Option(None, "-o", "--out"),
    decode: bool = DECODE_OPTION,
    changes: bool = CHANGES_OPTION,
    names: str | None = NAMES_OPTION,
) -> None:
    """Dump matching lines as JSONL (--json) or text.

    With -o the dump goes to the file and stdout carries only the result: a "wrote N
    lines" note, or with --json the one object SPEC 4 promises (`{"file", "lines"}`),
    where it used to print nothing at all.
    """
    s = settings_of(ctx)
    since_ts, id_to, empty = _clock_bounds(s, from_, to, session)
    since_ts = _absolute_window(since_ts, last_ms)
    params = _lines_params(s, chan, match, None, limit, None, session, since_ts, id_to)
    truncated = False
    if empty:
        pages: Iterable[list[dict[str, Any]]] = []
    elif limit:
        body = _fetch_lines(s, params, limit)
        pages, truncated = [body["lines"][::-1]], body["truncated"]
    else:
        # Every row by default (SPEC 4), streamed a page at a time rather than held whole:
        # a capture is routinely far larger than the process should buffer.
        pages = _iter_pages_asc(s, params)
    rows = _decode_pages(s, pages, decode, changes, names, session, bool(match or chan))
    render = json.dumps if s.json_out else fmt_line
    count = size = 0
    if out_file:
        try:
            # newline="\n" so the export is LF on every platform: the default (None)
            # translates to CRLF on Windows, which both inflates the file past the
            # "bytes" count below and makes the same capture export differently there.
            with open(out_file, "w", encoding="utf-8", newline="\n") as fh:
                for row in rows:
                    line = render(row) + "\n"
                    fh.write(line)
                    count += 1
                    size += len(line.encode("utf-8"))
        except OSError as exc:
            # An unwritable path is a user error, not a crash: it used to reach the user
            # as a raw FileNotFoundError traceback with no exit-code contract.
            die(f"cannot write {out_file}: {exc}", 1)
        if s.json_out:
            out_json({"file": out_file, "lines": count, "bytes": size, "truncated": truncated})
        else:
            print(f"wrote {count} lines to {out_file}")
    else:
        for row in rows:
            print(render(row))
            count += 1
    if truncated:
        note_truncated({"lines": [None] * count, "truncated": True}, limit)


# -- bus sugar: can / i2c / spi / gpio / adc ------------------------------------------


def _run_cmd(
    ctx: typer.Context, text: str, timeout: int = 1000, retry_ms: int = 0,
    eol: str | None = None,
) -> None:
    s = settings_of(ctx)
    # `ERR 6 busy` is transient by definition (the target's TX spacing timer, a bus
    # arbitration loss), so a caller that says how long it can wait gets it retried.
    deadline = time.monotonic() + retry_ms / 1000
    while True:
        res = Client(s).post(
            "/cmd", {"port": s.port, "cmd": text, "timeout_ms": timeout, "eol": eol},
            timeout=timeout / 1000 + 5,
        )
        busy = res.get("status") == "err" and res.get("err_name") == "busy"
        if not busy or time.monotonic() >= deadline:
            emit_cmd_result(s, res)
            return   # emit_cmd_result exits; never spin if that ever changes
        time.sleep(0.02)


can_app = typer.Typer(help="CAN commands.")
app.add_typer(can_app, name="can")
BUS_OPTION = typer.Option(1, "--bus", min=1, max=9, help="CAN bus, 1 to 9 (default 1).")


@can_app.command("tx")
def can_tx(
    ctx: typer.Context,
    can_id: str = typer.Argument(..., metavar="ID"),
    data: str | None = typer.Argument(None, metavar="DATA"),
    ext: bool = typer.Option(False, "--ext", help="29-bit extended id."),
    rtr: int | None = typer.Option(None, "--rtr", help="Send an RTR frame requesting N bytes."),
    bus: int = BUS_OPTION,
    retry_ms: int = RETRY_OPTION,
) -> None:
    """Transmit a CAN frame."""
    if rtr is not None and not 0 <= rtr <= 8:
        # SPEC 2.4: the DLC token is a single digit 0..8; a sender must not emit anything else.
        raise typer.BadParameter("--rtr takes a DLC of 0 to 8", param_hint="--rtr")
    if rtr is not None and data:
        # An RTR frame carries no data, so the positional was being dropped in silence.
        raise typer.BadParameter("--rtr and DATA are mutually exclusive", param_hint="--rtr")
    parts = [p.format_can_family(bus), "tx", can_id]
    flags = ""
    if rtr is not None:
        parts.append(str(rtr))
        flags += "r"
    else:
        parts.append(data if data else "-")
    if ext:
        flags = "x" + flags
    if flags:
        parts.append(flags)
    _run_cmd(ctx, " ".join(parts), retry_ms=retry_ms)


@can_app.command("stat")
def can_stat(ctx: typer.Context, bus: int = BUS_OPTION) -> None:
    """Show CAN counters and state for one bus."""
    _run_cmd(ctx, f"{p.format_can_family(bus)} stat")


@can_app.command(
    "filter",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def can_filter(ctx: typer.Context, bus: int = BUS_OPTION) -> None:
    """Set the CAN receive filter (e.g. `can filter all`, `can filter 100 700`)."""
    _run_cmd(ctx, " ".join([p.format_can_family(bus), "filter", *ctx.args]))


@can_app.command("dump")
def can_dump(
    ctx: typer.Context,
    can_id: str | None = typer.Option(None, "--id"),
    bus: int | None = typer.Option(None, "--bus", min=1, max=9, help="Only this bus."),
    last_ms: int | None = typer.Option(None, "--last-ms"),
    n: int = typer.Option(20, "-n"),
    follow: bool = typer.Option(False, "-f", "--follow"),
) -> None:
    """Show decoded CAN frames from the capture."""
    s = settings_of(ctx)
    client = Client(s)
    params: dict[str, Any] = {"limit": n}
    if s.port:
        params["port"] = s.port
    if can_id:
        params["id"] = can_id
    if bus is not None:
        params["bus"] = bus
    if last_ms is not None:
        params["last_ms"] = last_ms
    body = client.get("/can/frames", params=params)
    frames = list(reversed(_list_field(body, "frames")))
    for fr in frames:
        out_json(fr) if s.json_out else print(fmt_frame(fr))
    if follow:
        _dump_follow(client, s, can_id, bus)


FOLLOW_POLL_S = 0.2       # `can dump -f` poll interval
FOLLOW_GIVE_UP_S = 30.0   # ... and how long it keeps polling a daemon that never answers


def _capture_token(client: Client) -> str | None:
    """The daemon's current capture identity (SPEC 3.4), or None if it cannot be read.

    `probe`, not `get`: a follow must not end because one extra status call failed.
    """
    body = client.probe("GET", "/status")
    token = body.get("capture") if isinstance(body, dict) else None
    return token if isinstance(token, str) else None


def _dump_follow(
    client: Client, s: Settings, can_id: str | None, bus: int | None = None
) -> None:
    since = 0
    params: dict[str, Any] = {"limit": 1000}
    if s.port:
        params["port"] = s.port
    if can_id:
        params["id"] = can_id
    if bus is not None:
        params["bus"] = bus
    # prime `since` with the newest frame so we only print new ones
    body = client.get("/can/frames", params={**params, "limit": 1})
    seen = _list_field(body, "frames")
    if seen:
        since = seen[0]["line_id"]
    # The id space `since` belongs to (SPEC 3.4). A purge, a recreated DB or a restored
    # backup mints a new one and restarts ids low, so a watermark held across the change
    # matches nothing ever again and the follow goes silent for good.
    capture = _capture_token(client)
    # Two counters, because a poll and a frame are different items and only one of them
    # is evidence about the daemon. Sharing one made a poll that answered 200 with
    # undecodable frames count towards "the daemon is gone": 149 such frames then turned
    # the next transient error into exit 3 "unreachable for 30s" after 0.011 s.
    polls = _DropCounter("update")
    frame_drops = _DropCounter("frame")
    giveup_at: float | None = None
    try:
        while True:
            time.sleep(FOLLOW_POLL_S)
            # A failed poll is charged to that poll, not to the follow: this loop had no
            # handling at all, so one transient httpx error ended `can dump -f` with a
            # traceback (SPEC 4). _poll_frames still dies on what no retry can fix.
            try:
                body = _poll_frames(client, {**params, "since_id": since})
                frames = list(reversed(_list_field(body, "frames")))
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                polls.bad(exc)
                # Retrying tolerates a daemon restart under a live follow, but a daemon
                # that is simply gone must end the follow with an exit code rather than
                # poll a dead URL for ever (the other half of review class 16). Measured
                # against the clock, not counted in iterations: each failed poll can pay
                # the 10 s connect timeout, so `episode * FOLLOW_POLL_S` called 30 s after
                # what could be 25 minutes.
                if giveup_at is None:
                    giveup_at = time.monotonic() + FOLLOW_GIVE_UP_S
                elif time.monotonic() >= giveup_at:
                    die(f"daemon unreachable at {s.url} for {FOLLOW_GIVE_UP_S:g}s: {exc}", 3)
                continue
            giveup_at = None      # the daemon answered; the clock starts fresh next time
            polls.ok()
            if not frames:
                # Only on an empty poll: frames arriving are proof the watermark still
                # works, and a /status call per poll would be pure chatter.
                token = _capture_token(client)
                if token is not None and token != capture:
                    # Also when the held token is None, i.e. the priming read failed: the
                    # two costs are not symmetric. Adopting a first token without resetting
                    # risks a watermark from a capture that no longer exists, and the follow
                    # is then silent for ever; resetting costs at most one bounded replay of
                    # frames already on screen.
                    since = 0         # the new capture's ids restart below the watermark
                    capture = token
            before = frame_drops.total
            for fr in frames:
                try:
                    since = max(since, fr["line_id"])
                    text = json.dumps(fr) if s.json_out else fmt_frame(fr)
                except (KeyError, TypeError, ValueError) as exc:
                    frame_drops.bad(exc)
                    continue
                emit_stream(text)   # outside the guard: EPIPE ends the follow
            if frame_drops.total == before:
                frame_drops.ok()
    except KeyboardInterrupt:
        raise typer.Exit(0) from None
    finally:
        polls.ok()
        frame_drops.ok()


def _poll_frames(client: Client, params: dict[str, Any]) -> Any:
    """One `can dump -f` poll, raising on a failure a later poll could survive.

    The mirror image of the per-item rule (review class 16): a guard that keeps looping
    must still recognise what is not per-item. A url httpx cannot parse and a 4xx (a
    filter the daemon rejects) are answers no retry changes, so they end the follow;
    transport failures, timeouts and 5xx are left to the caller to count and retry.
    """
    s = client.s
    try:
        with client.open() as http:
            resp = http.get(
                s.url + "/can/frames", params=params, headers=s.headers(), timeout=10.0
            )
    except httpx.InvalidURL as exc:
        die_bad_url(s.url, exc)
    if 400 <= resp.status_code < 500:
        die(f"error: {error_text(resp)}", 1)
    resp.raise_for_status()
    return resp.json()


i2c_app = typer.Typer(help="I2C commands.")
app.add_typer(i2c_app, name="i2c")


@i2c_app.command("scan")
def i2c_scan(ctx: typer.Context) -> None:
    """Scan the I2C bus for devices."""
    _run_cmd(ctx, "i2c scan")


@i2c_app.command("rd")
def i2c_rd(
    ctx: typer.Context,
    addr: str = typer.Argument(..., metavar="ADDR"),
    n: int = typer.Argument(..., metavar="N"),
    reg: str | None = typer.Option(None, "--reg", help="Register hex; uses wrrd."),
) -> None:
    """Read N bytes from an I2C device, optionally from a register (--reg uses wrrd)."""
    if reg is not None:
        _run_cmd(ctx, f"i2c wrrd {addr} {reg} {n}")
    else:
        _run_cmd(ctx, f"i2c rd {addr} {n}")


@i2c_app.command("wr")
def i2c_wr(
    ctx: typer.Context,
    addr: str = typer.Argument(..., metavar="ADDR"),
    data: str = typer.Argument(..., metavar="DATA"),
) -> None:
    """Write hex bytes to an I2C device."""
    _run_cmd(ctx, f"i2c wr {addr} {data}")


spi_app = typer.Typer(help="SPI commands.")
app.add_typer(spi_app, name="spi")


@spi_app.command("xfer")
def spi_xfer(
    ctx: typer.Context,
    cs: str = typer.Argument(..., metavar="CS"),
    data: str = typer.Argument(..., metavar="DATA"),
) -> None:
    """Full-duplex SPI transfer with the named chip-select."""
    _run_cmd(ctx, f"spi xfer {cs} {data}")


gpio_app = typer.Typer(help="GPIO commands.")
app.add_typer(gpio_app, name="gpio")


@gpio_app.command("set")
def gpio_set(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    value: str = typer.Argument(..., metavar="0|1"),
) -> None:
    """Set a GPIO output."""
    _run_cmd(ctx, f"gpio set {name} {value}")


@gpio_app.command("get")
def gpio_get(ctx: typer.Context, name: str = typer.Argument(...)) -> None:
    """Read a GPIO input."""
    _run_cmd(ctx, f"gpio get {name}")


adc_app = typer.Typer(help="ADC commands.")
app.add_typer(adc_app, name="adc")


@adc_app.command("read")
def adc_read(ctx: typer.Context, name: str = typer.Argument(...)) -> None:
    """Read an ADC channel."""
    _run_cmd(ctx, f"adc read {name}")


# -- plot data (SPEC 9.2) -------------------------------------------------------------


plot_app = typer.Typer(help="List and export decoded plot channels.")
app.add_typer(plot_app, name="plot")


@plot_app.command("channels")
def plot_channels(
    ctx: typer.Context,
    active: float | None = typer.Option(
        None, "--active", help="Only channels with a sample in the last N seconds (N > 0).",
        callback=positive_option,
    ),
) -> None:
    """List discovered plot channels (name, stream, unit, last value, age, point count)."""
    s = settings_of(ctx)
    body = Client(s).get("/plot/channels")
    channels = _list_field(body, "channels")
    now = time.time()
    if active is not None:
        # Channels from firmware flashed weeks ago sit next to live ones with the same
        # last_value; the age is what tells them apart.
        channels = [ch for ch in channels if now - (ch.get("last_ts") or 0) <= active]
        body = {**body, "channels": channels}
    if s.json_out:
        out_json(body)
        return
    if not channels:
        print("no plot channels captured yet" if active is None else "no active plot channels")
        return
    for ch in channels:
        sid = f"s{ch['sid']}" if ch["sid"] is not None else "adhoc"
        unit = f" {ch['unit']}" if ch.get("unit") else ""
        typ = ch.get("type") or "-"
        age = fmt_age(now - ch["last_ts"]) if ch.get("last_ts") else "?"
        print(
            f"{ch['name']:<16} {sid:<6} {typ:<3} "
            f"last={ch['last_value']}{unit}  age={age}  n={ch['count']}"
        )


@plot_app.command("export")
def plot_export(
    ctx: typer.Context,
    names: str = typer.Option(..., "--names", help="Comma-separated channel names."),
    last_ms: int | None = typer.Option(None, "--last-ms"),
    session: str | None = typer.Option(None, "--session", help="Scope to a session name/id."),
    wide: bool = typer.Option(False, "--wide", help="One sample per row (shared stream)."),
    out_file: str | None = typer.Option(None, "-o", "--out"),
) -> None:
    """Export channel history as CSV (long by default, --wide for one sample per row).

    The body is streamed rather than read whole: this is the one endpoint that can return
    a very large response (every sample of every named channel over a long run).

    CSV is not JSON, so --json wraps it: with -o, one object describing the file; without,
    one object carrying the CSV in a "csv" field. Either way stdout stays parseable, where
    it used to be raw CSV (or, with -o, empty).
    """
    s = settings_of(ctx)
    params: dict[str, Any] = {"names": names, "format": "wide" if wide else "long"}
    if last_ms is not None:
        params["last_ms"] = last_ms
    if session:
        params["session"] = session
    client = Client(s)
    newlines = 0

    def count(chunk: str) -> None:
        nonlocal newlines
        newlines += chunk.count("\n")

    if out_file:
        try:
            fh = open(out_file, "w", encoding="utf-8", newline="")
        except OSError as exc:
            die(f"cannot write {out_file}: {exc}", 1)

        def to_file(chunk: str) -> None:
            fh.write(chunk)
            count(chunk)

        ok = False
        try:
            client.stream_text("/plot/export", to_file, what=out_file, params=params)
            # Closed inside the guarded region: the buffered write is flushed by the close,
            # so a full disk raised out of the `finally` where nothing mapped it, and this
            # one export was a traceback where `log export` on the same target exits 1.
            fh.close()
            ok = True
        except BrokenPipeError:
            raise                        # handled in main(): the reader closed the pipe
        except OSError as exc:
            die(f"cannot write {out_file}: {exc}", 1)
        finally:
            with contextlib.suppress(OSError):
                fh.close()               # a no-op once the guarded close above succeeded
            if not ok:
                # A request the daemon refuses (or a stream that dies mid-transfer) left an
                # empty or truncated CSV where the user asked for an export, indistinguishable
                # from a whole one. Same guard as Client.download.
                with contextlib.suppress(OSError):
                    os.remove(out_file)
        rows = max(newlines - 1, 0)  # minus the header
        if s.json_out:
            out_json({"file": out_file, "rows": rows, "bytes": os.path.getsize(out_file)})
        else:
            print(f"wrote {rows} rows to {out_file}")
        return

    if s.json_out:
        parts: list[str] = []

        def to_list(chunk: str) -> None:
            parts.append(chunk)
            count(chunk)

        client.stream_text("/plot/export", to_list, what="stdout", params=params)
        out_json({
            "names": names, "format": "wide" if wide else "long",
            "rows": max(newlines - 1, 0), "csv": "".join(parts),
        })
        return

    def to_stdout(chunk: str) -> None:
        try:
            sys.stdout.write(chunk)
        except BrokenPipeError:
            # `mcu plot export | head`: the reader is done, so we are too. Silence stdout
            # first or the interpreter's own shutdown flush prints over the top of us.
            _silence_stdout()
            raise typer.Exit(0) from None

    client.stream_text("/plot/export", to_stdout, what="stdout", params=params)
    sys.stdout.flush()


# -- daemon control -------------------------------------------------------------------


daemon_app = typer.Typer(help="Start/stop/check the local mcuscoped daemon.")
app.add_typer(daemon_app, name="daemon")


@daemon_app.command("start")
def daemon_start(
    ctx: typer.Context,
    config: str | None = typer.Option(
        None, "--config", "-c", help="Config file for the daemon (forwarded as mcuscoped -c)."
    ),
    sim: bool = typer.Option(
        False, "--sim", help="Start with the bundled simulator attached (zero-hardware demo)."
    ),
    wait_s: float = typer.Option(
        DAEMON_START_TIMEOUT_S, "--timeout", "-t", metavar="SECONDS",
        help="Seconds to wait for the daemon to answer /status (env MCUSCOPE_START_TIMEOUT).",
        callback=finite_option,
        show_default="20 unless MCUSCOPE_START_TIMEOUT is set",
    ),
) -> None:
    """Spawn mcuscoped as a detached background process (cross-platform).

    The global --token (or MCUSCOPE_TOKEN) is forwarded to the daemon via its
    environment, so `mcu --token X daemon start` both requires X of network
    clients and uses it for this CLI's own requests.

    Opening a large capture on a cold filesystem is not instant, so the readiness wait is
    generous and adjustable (--timeout / MCUSCOPE_START_TIMEOUT). If it does run out the
    spawned process is stopped rather than left running with its pid record deleted, which
    is how a daemon used to end up alive and unstoppable.
    """
    s = settings_of(ctx)
    if _status_body(s, timeout=1.0) is not None:   # already running
        die("daemon already running", 1)
    host, port = _host_port(s)
    # Before the spawn: resolving it creates the data directory and can fail, and doing
    # that afterwards left a running daemon behind a traceback.
    pid_path = _pid_file(s)
    args = [sys.executable, "-m", "mcuscope.daemon", "--host", host, "--port", str(port)]
    if config:
        args += ["--config", config]
    if sim:
        args.append("--sim")
    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if s.token:
        # Via the environment, not argv: the token must not show in the process list.
        kwargs["env"] = {**os.environ, "MCUSCOPED_TOKEN": s.token}
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        )
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, **kwargs)
    try:
        if not _write_pid_record(pid_path, proc.pid):
            # The record names a live process: another daemon for this host:port claimed
            # it, and taking it leaves that one addressed by nothing (pidfile's rule). The
            # readiness check below decides whether this spawn was the redundant one.
            err(f"warning: {pid_path} already names a running process; left it in place")
    except OSError as exc:
        # The daemon was already spawned above, so this must not become a traceback: that
        # would break the SPEC 4 exit-code contract *and* leave a running daemon behind.
        # It also is not fatal - the daemon claims its own record for the same host:port
        # on startup (pidfile.claim), which is what `daemon stop` reads - so say so and
        # carry on to the readiness wait rather than killing a healthy daemon.
        err(f"warning: could not write the pid file {pid_path}: {exc}")
    # Honour --timeout as given (clamped only against negatives). A 0.5s floor used to sit
    # here, which silently overrode the documented "Seconds to wait" for any smaller value
    # and turned "wait 0.05s" into a race the daemon could win on an idle machine.
    deadline = time.monotonic() + max(wait_s, 0.0)
    body: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        body = _status_body(s, timeout=0.5)
        if body is not None:
            break
        if proc.poll() is not None:      # it died; no point waiting out the deadline
            break
        time.sleep(0.1)
    if body is None:
        _abandon_daemon(proc, pid_path, s, wait_s)
    # "Something mcuscoped answers here" is not "the daemon I spawned is up". Two starts
    # racing for one host:port leave the loser's child dead on the port conflict while the
    # winner answers, and the loser then reported success with a dead pid. A URL answering
    # for a different process is a failure of *this* start: nothing is written, nothing is
    # removed, and the pid named is the one that actually holds the port.
    serving = _serving_pid(body, None)
    if serving is not None and serving != proc.pid:
        die(f"another daemon is already serving at {s.url} (pid {serving})", 1)
    if proc.poll() is not None:
        die(f"mcuscoped exited with status {proc.poll()} although {s.url} answers; "
            "something else is serving that port", 1)
    if s.json_out:
        out_json({"ok": True, "pid": proc.pid})
    else:
        print(f"started mcuscoped (pid {proc.pid})")


@daemon_app.command("stop")
def daemon_stop(ctx: typer.Context) -> None:
    """Stop the local mcuscoped daemon, however it was started."""
    s = settings_of(ctx)
    pid_path = _pid_file(s)
    if not os.path.exists(pid_path):
        # No record - a daemon started some other way, or one whose data dir was
        # unwritable when it tried to claim one. It still answers POST /shutdown,
        # so ask /status who is serving instead of refusing to stop a live daemon.
        body = _status_body(s)
        if body is None:
            die("no pid file; daemon not started by this CLI", 1)
        _stop_running_daemon(s, _serving_pid(body, None), None)
        return
    from .pidfile import pid_running, read_pid_record

    # None when the record is unreadable, empty or not a pid. That is not proof the
    # daemon is dead: it used to delete the record and exit 1 here, which destroyed a
    # healthy daemon's record without ever asking /status - while the no-record branch
    # above, with strictly less information, stopped the daemon correctly. /status is
    # asked first now, and the record is only ever removed when provably stale.
    pid = read_pid_record(pid_path)
    # Only act on a pid that a live mcuscoped is answering for. A pid file left behind
    # by a crashed daemon eventually names an unrelated, recycled process, and killing
    # that would be a nasty surprise; a stale file is simply removed instead.
    body = _status_body(s)
    if body is None:
        if pid is None:
            die(f"pid file {pid_path} was unreadable or corrupt, and no daemon is "
                f"responding at {s.url}; left it in place", 1)
        if pid_running(pid):
            # /status did not answer with a usable body, but the process it names is
            # there: a daemon still starting up, or one behind a token this CLI does not
            # hold. Removing the record of a live daemon is how one becomes unstoppable,
            # so keep it and report what was found.
            die(f"no usable /status from {s.url}, but pid {pid} is still running; "
                f"left its record {pid_path} in place", 1)
        with contextlib.suppress(OSError):
            os.remove(pid_path)
        die(f"no daemon responding at {s.url}; removed stale pid file (was pid {pid})", 1)
    _stop_running_daemon(s, _serving_pid(body, pid), pid_path, pid)


@daemon_app.command("status")
def daemon_status(ctx: typer.Context) -> None:
    """Report whether the daemon is reachable."""
    s = settings_of(ctx)
    body = _status_body(s)   # anything that is not mcuscoped counts as not running (exit 3)
    if body is None:
        if s.json_out:
            out_json({"running": False})
        else:
            print("not running")
        raise typer.Exit(3)
    if s.json_out:
        out_json({"running": True, "version": body["version"], "uptime_s": body["uptime_s"]})
    else:
        print(f"running: mcuscoped {body['version']} up {body['uptime_s']:.0f}s")


# -- ai-guide -------------------------------------------------------------------------

AI_GUIDE = """\
mcu: hardware debug bridge CLI (talks to the mcuscoped daemon over 127.0.0.1)

WHAT IT IS
  mcuscoped owns the serial link to an MCU running the "monitor" firmware and logs
  every line to SQLite. `mcu` is a thin client. Prefer --json for machine parsing.

EXIT CODES (contract)
  0 success / match    1 error (bus ERR, HTTP error, bad usage)
  2 timeout            3 daemon unreachable

GLOBAL OPTIONS
  --json            one JSON object per command (streaming cmds: one per line)
  -p, --port ALIAS  choose a port (default: the only attached port)
  --url URL         daemon base URL (or env MCUSCOPE_URL); default http://127.0.0.1:8558
  --token TOKEN     access token for a remote daemon (or env MCUSCOPE_TOKEN)

HEALTH
  mcu status                      daemon + port health; each port line shows its state
                                  (connected / disconnected (REASON) / DEGRADED: N write
                                  failures since HH:MM:SS, i.e. RX flows but nothing gets
                                  through to the board) and target=<name>, the monitor's
                                  own name from a ping at connect, so you know which board
                                  is behind a debugger that moves between boards
  disconnect_reason (--json, and in brackets above), what to do about each:
    no_device     board powered off or unplugged: fix power/cable, then wait for the sys
                  row (mcu wait --chan sys --match "port board connected")
    open_failed   present but will not open: another process holds it, or permissions;
                  free it, then POST /ports/<alias>/reconnect (no CLI verb; the web UI
                  chip dot does it too)
    read_error    the link dropped mid-session; the daemon is retrying on its own, wait
    manual        closed by POST /ports/<alias>/disconnect; resume with .../reconnect
  mcu wait --chan sys --match "port board connected" --timeout 60000
                                  block until the port (re)connects, e.g. after a power-up;
                                  "port board disconnected" for the other direction. There is
                                  no port-state flag: the sys channel already carries it.
  mcu ports                       list attached ports
  mcu devices                     list host serial devices (find /dev/ttyACM0, COMx)
  mcu attach socket://127.0.0.1:9900 --alias board [--eol none|lf|crlf]
                                  --eol sets what the port appends to every line it sends
                                  (default lf, what the monitor expects)
  mcu detach board

THE CORE LOOP (send, wait, query)
  mcu cmd "i2c rd 48 2"           send a command, print response data; ERR -> stderr, exit 1
  mcu send "reset"                write one raw line, no response wait (fire-and-forget)
  mcu wait --match "^!can" --timeout 2000        block until a line matches; exit 2 on timeout
  mcu wait --send "can tx 300 AABB" --match "301 AABB"   send then wait for the reply
  mcu wait --send "" --repeat-ms 50 --match "=>" --timeout 30000
      resend the line every 50 ms until it matches, to catch a bootloader's autoboot window
      start it BEFORE powering the target: writes to a disconnected port are counted, not fatal
  mcu lines --last-ms 5000 --chan event --match "1A3"    query the capture (the workhorse)
  mcu tail -f --chan debug        follow live output
  mcu mark "starting test"        drop an annotation into the log
  --eol none|lf|crlf              line ending for one send (cmd/send/wait/assert); the
                                  port's own setting applies when omitted. `--eol none`
                                  appends nothing, which is how a bare control character
                                  is sent: mcu send --eol none $'\\x03'   (Ctrl-C)
  mcu break --ms 250              serial break (line held low), 1..2000 ms
  mcu sysrq b                     break, then one character with no terminator: Linux
                                  magic SysRq (b reboot, t tasks, w blocked tasks). Needs
                                  the target's kernel sysrq enabled and its console on
                                  this UART; one character only.
  `cmd` and `--send` take the monitor's own grammar, not the `mcu` sugar: a CAN frame is
  `can tx ID DATA [x][r]` (x = extended id, r = RTR), on bus 2 `can2 tx ...`. `--ext` is
  sugar only: `mcu can tx C0103 B400 --ext` sends `can tx C0103 B400 x`.

READING THE CAPTURE (lines, tail and log export share these options)
  Windows: --last-ms N, --session NAME, --since-id N, and wall-clock bounds
    --from HH:MM[:SS[.mmm]] --to HH:MM[:SS[.mmm]]   today, local time; give the date for
                                  another day (2026-09-01T19:53:35); --from after --to is refused
  Size: any --limit works (the CLI pages past the daemon's 1000-row answers itself);
    `lines` defaults to the newest 100, `log export` to EVERY matching row (--limit N = newest N)
  Order: text output is oldest first (a boot log reads top to bottom); --json is newest
    first, the API's order, so reverse the "lines" array for a chronological read
  Filters: --chan debug|event|cmd|resp|sys|marker, --match REGEX (matches the raw line)
  Decoding plot samples (the readable timeline for a test run):
    --decode        render !ps/!p samples as named fields from the firmware's !pd definition:
                    "s0 state=CHARGING vbat=25.54V io=robot|relay|bat" (enum labels, bit-lane
                    names, units resolved; the !pd rows themselves are hidden)
    --changes       print a stream's sample only when a rendered field changed (implies --decode):
                    a 60 s run at 10 Hz becomes a few dozen lines of state transitions
    --names a,b     render only these fields or lanes (implies --decode)
  mcu lines --session run-3 --decode --changes           the whole run as state transitions
  mcu lines --from 19:53:35 --to 19:54:00 --decode --names state,vbat
  mcu log export --session run-3 --decode --changes -o run.txt   same, to a file, every row
  mcu tail -f --decode --changes                         live, only when something changes
  mcu lines --match "^!e"         firmware error notices: "!e plot 3 badarg def" means the
                                  monitor rejected plot stream 3 (bad definition, duplicate
                                  name, table full, wrong length); the stream never appears
  Every --json row carries the decoded text in "decoded" (and in "raw") when decoding.

VERDICTS (one pass/fail answer instead of a log to read)
  `wait` asks "did this line appear?"; `assert` asks "did this run pass?".
  Exit 0 = pass, 1 = fail. Several conditions at once, negative ones included.
  mcu assert --session boot-test --expect "CALIB DONE" --forbid "ERR|retry" --json
                                  judge a stored run after the fact
  mcu assert --send reset --expect "BOOT OK" --forbid "PANIC" --timeout 5000
                                  live: send, then judge the window that follows
  mcu assert --last-ms 10000 --forbid "ERR"     judge the last 10 s
  mcu assert --expect "BOOT OK" --forbid "ERR" --min-window 10000 --timeout 20000
                                  boot within 20 s AND stay clean for at least 10 s
  Live windows close as soon as every --expect is met, so --forbid would otherwise
  only cover the span the expects took; --min-window holds the window open. With no
  --expect the whole window is used (absence cannot be proven early).

SESSIONS (name a run, then query just that run)
  The daemon already records one session per run of its own ("auto-<timestamp>"), so
  every capture belongs to some session. Naming one carves your run out of that.
  mcu session start boot-test     everything captured from now belongs to this session
  mcu session stop                close it (starting another also closes the current one)
  A named session survives a daemon restart (the run continues; `mcu status` shows how
  long it has been running). Stop it when the run is over.
  mcu session list                recent runs with their line counts ("auto" vs "named")
  mcu lines --session boot-test --json           only that run's lines
  mcu log export --session boot-test -o run.txt  and the same for exports
  mcu plot export --session boot-test --names vbat -o run.csv
  mcu session export boot-test -o run.db         archive it as a standalone capture DB
  mcu session delete boot-test --data --yes      drop the label, and its lines with --data

DELETING CAPTURE (not recoverable; always previewed first)
  mcu purge --session junk-run --dry-run         see how many lines would go
  mcu purge --session junk-run --yes             delete that run's lines
  mcu purge --before-days 2 --yes                delete anything older than 2 days
  mcu purge --id-from 100 --id-to 500 --yes      delete an explicit id range
  mcu purge --all --yes                          wipe the whole capture

PLOTS (numeric channels the firmware emits as `!p <tick> name=value`)
  mcu plot channels               discovered channels: name, unit, last value, age, count
  mcu plot channels --active 60   only channels seen in the last 60 s (channels from
                                  firmware flashed weeks ago otherwise sit next to live ones)
  mcu plot export --last-ms 10000 --names vbat,temp -o run.csv
  mcu plotjuggler on [host:port]  mirror points to PlotJuggler's UDP Server (default
                                  127.0.0.1:9870); `off` stops, no args shows state; alias pj

BUS SUGAR (all wrap `cmd`)
  mcu can tx 1A3 DEADBEEF [--ext] [--rtr 4] [--bus 2]   --bus N: CAN controller N (default 1)
  mcu can tx 1A3 00 --retry-ms 500   keep retrying `ERR 6 busy` (the target's TX spacing on a
                                  busy bus) for up to 500 ms; `mcu cmd` takes it too
  mcu can dump --id 100 -f        decoded CAN frames, live; --bus N shows one controller
  mcu can stat / mcu can filter all           both take --bus N
  mcu i2c scan
  mcu i2c rd 48 2 --reg 00        register read (uses wrrd)
  mcu i2c wr 50 0011AA
  mcu spi xfer imu 00FF
  mcu gpio set led 1 / mcu gpio get led / mcu adc read vbat

TYPICAL AGENT PATTERN
  1. mcu status --json                       (is a board connected?)
  2. mcu cmd "..." --json                     (act; check "status": ok|err|timeout)
  3. mcu wait --send "..." --match "..." --json   (send-and-wait for the effect)
  4. mcu lines --last-ms N --json              (inspect what happened)
     mcu lines --last-ms N --decode --changes    (the same, as readable state transitions)
  5. mcu assert --last-ms N --expect ... --forbid ...   (decide pass/fail on an exit code)

TIMING-CRITICAL WORK (anything faster than about 1 Hz)
  Every `mcu` call is a new process (about 200 ms), so a tight loop cannot be built from
  them. The daemon's REST API is the same thing without the start-up; two primitives:
    POST http://127.0.0.1:8558/send   {"port": "board", "line": "...", "eol": "none|lf|crlf"}
    GET  http://127.0.0.1:8558/lines?since_id=N&limit=1000   rows with id > N, newest first;
                                     keep the highest id seen and pass it back as N
  Before writing that loop, check `mcu wait --repeat-ms` (above): it runs the send-until-match
  loop inside the daemon, with no client latency in the timing.

DAEMON CONTROL
  mcu daemon start | stop | status
  mcu daemon start --timeout 60      wait longer for a big capture to open (env
                                     MCUSCOPE_START_TIMEOUT); on failure the spawned
                                     daemon is stopped, never left orphaned
"""


@app.command("ai-guide")
def ai_guide(ctx: typer.Context) -> None:
    """Print a compact usage guide written for an AI agent."""
    if settings_of(ctx).json_out:
        # SPEC 4: with --json every command prints exactly one JSON object, no prose.
        out_json({"guide": AI_GUIDE})
        return
    print(AI_GUIDE)


# -- entry point ----------------------------------------------------------------------


# Hoisting is parameterized by the click app (cli_argv); these wrappers bind this
# module's `app` and keep the historical signatures.


def _value_taking_opts(argv: list[str]) -> set[str] | None:
    return cli_argv.value_taking_opts(app, argv)


def _split_global_opts(argv: list[str]) -> tuple[list[str], list[str]]:
    return cli_argv.split_global_opts(app, argv)


def _hoist_global_opts(argv: list[str]) -> list[str]:
    return cli_argv.hoist_global_opts(app, argv)


def _is_broken_pipe_exit(exc: BaseException) -> bool:
    """True when this SystemExit(1) is a library ending the process over a closed pipe.

    Two of them do, and neither is a failure of the command: click/typer catch EPIPE
    raised inside a command, swap stdout for a PacifyFlushWrapper and exit 1 (duck-typed,
    because typer vendors its own copy of click), and rich - which renders --help and
    every usage error - answers a broken pipe by devnulling stdout and raising
    SystemExit(1) from the handler, leaving the BrokenPipeError on the exception chain.
    """
    if type(sys.stdout).__name__ == "PacifyFlushWrapper":
        return True
    for _ in range(10):          # bounded: a __context__ chain can be circular
        # BrokenPipeError only, for the reason the dispatcher's OSError arm gives: any
        # OSError from anywhere in the command can land on this chain.
        if isinstance(exc, BrokenPipeError):
            return True
        if exc.__context__ is None:
            break
        exc = exc.__context__
    return False


def main(argv: list[str] | None = None) -> int:
    _stdio.widen_stdout_encoding()
    # Windows' closed-pipe EINVAL becomes BrokenPipeError at the stream, so rich, click and
    # every handler below recognise it. console_entry does this too; main() is also driven
    # directly (by the tests, and by `python -m`).
    _stdio.translate_closed_pipe_errors()
    code = _dispatch(argv)
    # The interpreter flushes stdout during shutdown, and a closed pipe there prints
    # "Exception ignored ... BrokenPipeError" and exits 120 over whatever we returned.
    # Flushing here, where it can be handled, keeps the exit-code contract intact.
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        _silence_stdout()
        return 0
    except OSError:
        _silence_stdout()
        return 1
    return code


def _dispatch_error(msg: str, code: int) -> int:
    """Report a failure the dispatcher itself caught, and answer with its exit code.

    Not die(): these arms are outside the command, so raising typer.Exit here would land
    nowhere. The --json half is the same promise die() keeps (SPEC 4: exactly one JSON
    object per command), which the usage-error arm honoured and these three did not.
    """
    err(msg)
    if json_mode():
        out_json({"error": msg, "exit_code": code})
    return code


def _dispatch(argv: list[str] | None = None) -> int:
    # With standalone_mode=False, click returns a command's `Exit` code as the call's
    # return value (rather than exiting), so capture it. Older clicks raise instead,
    # so the except clauses below cover both behaviors.
    if argv is None:
        argv = sys.argv[1:]
    # Resolved per invocation, not per process: main() is callable more than once (the
    # tests do), and a mode left over from the previous call is not this one's.
    set_json_mode(False)
    try:
        # Inside the try: hoisting can itself reject the command line (a global option
        # with no value), and that exit has to land on the contract like any other.
        head, rest = _split_global_opts(argv)
        if cli_argv.wants_json(head):
            # Set the mode here, not only in the group callback: an eager option
            # (--version) and every error raised before the callback runs (a bad global
            # option, an unknown command) still owe --json its one object.
            set_json_mode(True)
        argv = head + rest
        rv = app(args=argv, standalone_mode=False)
        return rv if isinstance(rv, int) else 0
    except EXIT_EXCEPTIONS as exc:
        return int(getattr(exc, "exit_code", 0) or 0)
    except USAGE_ERRORS as exc:
        exc.show()
        if json_mode():
            # SPEC 4 promises exactly one JSON object per command, and click writes its
            # usage message to stderr only; without this, --json got nothing on stdout.
            out_json({"error": exc.format_message(), "exit_code": 1})
        return 1
    except ABORT_EXCEPTIONS:
        return _dispatch_error("aborted", 1)
    except OSError as exc:
        # `mcu tail | head` closes the pipe early. That is the reader's normal exit, not
        # our failure, so report success - and redirect stdout to devnull first, because
        # the interpreter flushes it during shutdown and would print its own
        # "Exception ignored ... BrokenPipeError" and exit 120 over the top of us.
        # Any other OSError is a real failure and keeps going to the crash handler - which
        # is why this arm is BrokenPipeError alone: it sees errors from the whole program,
        # and reading any Windows EINVAL here made a bad path, a socket operation or a
        # serial URL exit 0. The streams themselves translate the one case that qualifies.
        if not isinstance(exc, BrokenPipeError):
            raise
        _silence_stdout()
        return 0
    except KeyboardInterrupt:
        return _dispatch_error("interrupted", 1)
    except (KeyError, IndexError) as exc:
        # A daemon response we index directly but that does not have the shape we index it
        # with (version skew, a proxy, the wrong port): a missing key or a short list.
        # Every command indexed body["..."] unguarded, so this reached the user as a rich
        # traceback instead of an exit code.
        #
        # TypeError is deliberately NOT here (review class 18): it is the shape a genuine
        # CLI bug takes, and catching it blamed the daemon for our own defect and replaced
        # the crash log with "unexpected response from daemon". The bodies whose *type* can
        # be wrong go through _list_field, which says the same thing at the point of use.
        return _dispatch_error(f"unexpected response from daemon: {exc}", 1)
    except SystemExit as exc:  # e.g. --help
        code = int(exc.code) if isinstance(exc.code, int) else 0
        # `mcu lines | head` is not a failure, so a library's own answer to EPIPE is
        # translated back to 0.
        return 0 if code == 1 and _is_broken_pipe_exit(exc) else code


def console_entry() -> int:
    """Console-script entry: repaired std streams plus a crash-file backstop."""
    return _stdio.console_entry(main, "mcu")


if __name__ == "__main__":
    raise SystemExit(console_entry())
