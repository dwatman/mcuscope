"""The `mcu` command-line client: a thin HTTP client of mcuscoped (SPEC 4).

Exit-code contract (for AI use): 0 success/match, 1 error (bus ERR, HTTP error, bad
usage), 2 timeout, 3 daemon unreachable. With --json, each command prints exactly one
JSON object (streaming commands print one object per line).
"""

from __future__ import annotations

import contextlib
import errno
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.parse
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import typer

from . import __version__, _stdio, cli_argv, cli_daemonctl
from . import protocol as p
from .cli_client import (
    DEFAULT_URL,
    Client,
    Settings,
    check_daemon_version,
    die_bad_url,
    error_text,
    start_hint,
)
from .cli_daemonctl import (
    DAEMON_START_TIMEOUT_S,
    _abandon_daemon,
    _host_port,
    _index_build,
    _open_append,
    _pid_file,
    _request_shutdown,  # noqa: F401  (re-exported for the tests)
    _serving_pids,
    _start_timeout_default,  # noqa: F401  (re-exported for the tests)
    _status_body,
    _status_or_refusal,
    _status_pid,
    _stderr_log_path,
    _stop_running_daemon,
    _write_pid_record,
)
from .cli_output import (
    ABORT_EXCEPTIONS,
    EXIT_EXCEPTIONS,
    USAGE_ERRORS,
    AsciiNumbersGroup,
    LineDecoder,
    _field,
    _fmt_value,
    _list_field,
    _silence_stderr,
    _silence_stdout,
    _stdout_unwritable,
    cmd_err_text,
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
    guard_stdout,
    json_mode,
    note_truncated,
    out_json,
    output_failed,
    parse_clock,
    positive_option,
    remove_partial,
    reset_output_state,
    set_json_mode,
)
from .render import one_line

# `asyncio`, `websockets`, `platformdirs` and `httpx` are imported where they are used
# (the follow loop, the pid-file helper, the client), not here. They cost about 100 ms of
# the CLI's startup, and `--help`, `--version` and `ai-guide` would pay it for nothing -
# which matters when an agent runs `mcu` dozens of times in a session.

def settings_of(ctx: typer.Context) -> Settings:
    return ctx.obj


# -- app + global options -------------------------------------------------------------

app = typer.Typer(
    cls=AsciiNumbersGroup, add_completion=True, no_args_is_help=True,
    help="mcu: hardware debug bridge CLI.",
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
    port: str | None = typer.Option(
        None, "--port", "-p",
        help="Port alias from 'mcu ports'. Commands that write to a board need it whenever "
             "more than one port is attached; reads without it span every port.",
    ),
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
    if port == "":
        # The daemon reads port "" as its own rows (SPEC 3.5), so reads that drop an empty
        # value and bodies that forward it would disagree; refuse it here for every command.
        die("-p/--port is empty: name a port alias, or leave -p out to span every port", 1)
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
    write_errors = body.get("write_errors", 0)
    errs = f"  write_errors={write_errors}" if write_errors else ""
    # Same treatment: a capture at its size cap is silently deleting the oldest half of
    # the run about to be queried, and this was the one /status counter never shown.
    trimmed = body.get("lines_trimmed", 0)
    trim = f"  trimmed={trimmed}" if trimmed else ""
    print(
        f"mcuscoped {body['version']}  up {fmt_num(body['uptime_s'])}s  "
        f"db {body['db_path']}{errs}{trim}"
    )
    # The store's writer task is what turns received lines into rows; with it dead the
    # daemon still answers, still reads the port and still counts rx, so every other line
    # of this output looks healthy while nothing is being captured (review class 12).
    if body.get("writer_alive", True) is False:
        print("  CAPTURE STOPPED: the store writer is not running; no lines are being saved")
    # The release check (SPEC 3.6) had only one delivery, the web UI badge, which reaches
    # nobody driving the CLI - and an agent or a headless bench is the normal way to use
    # this. The block is null when the check is switched off.
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
    ports = _list_field(body, "ports")
    if not ports:
        print("  no ports attached (see 'mcu devices', then 'mcu attach DEV')")
    for pt in ports:
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
    ports = _list_field(body, "ports")
    if not ports:
        print("no ports attached (see 'mcu devices', then 'mcu attach DEV')")
        return
    for pt in ports:
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
    # After the empty check, not before it: "no serial devices found" under a header row
    # reads as a table that failed to load. The widths match the rows below.
    print(f"{'device':<16} {'description':<28} {'vid:pid':<10} {'serial':<16}")
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
    base = os.path.basename(dev.rstrip("/")) or "board"
    # A serial number is arbitrary USB text; keep the derived alias inside ALIAS_RE so the
    # refusal, if any, names an option the user typed.
    return re.sub(r"[^A-Za-z0-9_.-]", "-", base)[:32].lstrip("_.-") or "board"


# The line endings an outgoing line may carry (SPEC 2.1), taken from protocol.EOL_BYTES
# rather than copied: a fourth spelling added there would otherwise be refused here.
# Validated in the client so a typo is bad usage (exit 1) with a message naming the
# option, not a 422 from a request that should never have been sent.
EOL_CHOICES = tuple(p.EOL_BYTES)


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
    device: str | None = typer.Argument(
        None, help="Device: /dev/ttyACM0, COM7, socket://host:port (or use --serial)"
    ),
    serial: str | None = typer.Option(
        None, "--serial", metavar="SN",
        help="Attach the USB device with this serial number (the 4th column of "
             "`mcu devices`), whatever name it enumerates under; survives a replug.",
    ),
    baud: int = typer.Option(115200, "--baud"),
    alias: str | None = typer.Option(
        None, "--alias",
        help="Name for this port (default: the device's basename, or 'board' for a URL).",
    ),
    eol: str = typer.Option(
        "lf", "--eol", callback=eol_option, metavar="none|lf|crlf",
        help="Line ending this port appends to everything sent to it.",
    ),
) -> None:
    """Attach a serial port, by device name or by USB serial number."""
    s = settings_of(ctx)
    if serial is not None:
        serial = serial.strip()
        if not serial:
            # A blank serial attaches a port that can never connect.
            die("error: --serial is blank; give the serial number from 'mcu devices'", 1)
    # Named refusals: the daemon resolves a serial number to a device on every open, so
    # a body carrying both would leave which one wins up to the endpoint.
    if device and serial:
        die("error: give a device or --serial, not both", 1)
    if not device and not serial:
        die("error: give a device, or --serial SN (see 'mcu devices')", 1)
    target = device or serial or ""
    body: dict[str, Any] = {"alias": alias or _derive_alias(target), "baud": baud, "eol": eol}
    if device:
        body["device"] = device
    else:
        body["serial_number"] = serial
    client = Client(s)
    listed = client.probe("GET", "/ports")
    ports = listed.get("ports") if isinstance(listed, dict) else None
    before = next((pt for pt in ports if isinstance(pt, dict) and pt.get("alias") == body["alias"]),
                  None) if isinstance(ports, list) else None
    res = client.post("/ports", body)
    if before is not None:
        # The daemon retargets an existing alias; say so, or a typo'd --alias silently moves
        # another board's name. A serial-bound port reports its resolved device (or, until
        # it connects, the serial) as `device`, so the binding is read from `serial_number`.
        was_sn = before.get("serial_number")
        was = f"serial {was_sn}" if was_sn else before.get("device")
        now = f"serial {serial}" if serial else target
        if was != now:
            err(f"note: {body['alias']} was attached to {was}; it now names {now}")
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
        shown = device if device else f"serial {serial}"
        print(f"attached {port['alias']} -> {shown}{state}")


@app.command()
def detach(ctx: typer.Context, alias: str = typer.Argument(...)) -> None:
    """Detach a serial port."""
    s = settings_of(ctx)
    if "/" in alias:
        # The daemon decodes %2F back to a path separator, so no route could receive it.
        die(f"invalid alias {alias!r}: an alias cannot contain '/'", 1)
    # Quoted: `board?x` or `board#1` otherwise detached `board`.
    res = Client(s).delete(f"/ports/{urllib.parse.quote(alias, safe='')}")
    if s.json_out:
        out_json(res)
    else:
        print(f"detached {alias}")


# -- cmd / send / mark ----------------------------------------------------------------

# Widest millisecond timeout a command may ask for, mirroring server.MAX_TIMEOUT_MS. The
# value is duplicated rather than imported so the CLI does not pull the daemon's stack in.
MAX_TIMEOUT_MS = 300_000


def timeout_ms_option(value: int | None, lo: int = 1) -> int | None:
    """Click callback bounding a millisecond timeout, refusing it as bad usage.

    Unbounded, the value went into `timeout / 1000 + 5` and reached httpx as an
    OverflowError traceback with no exit code - `finite_option` guards the two float
    timeouts for the same reason and these three were left open.

    lo is 1 for /cmd and /wait, which the daemon declares gt=0; only `assert` takes 0,
    where it means "judge what is already captured". Click gets the two one-argument
    wrappers below, not this: a second parameter makes it pass (ctx, param, value).
    """
    if value is not None and not lo <= value <= MAX_TIMEOUT_MS:
        raise typer.BadParameter(f"expected {lo} to {MAX_TIMEOUT_MS} ms, got {value}")
    return value


def live_timeout_option(value: int | None) -> int | None:
    """`cmd`/`wait --timeout`: the daemon declares it gt=0."""
    return timeout_ms_option(value, lo=1)


def retrospective_timeout_option(value: int | None) -> int | None:
    """`assert --timeout`: 0 is legal and means a retrospective judgement."""
    return timeout_ms_option(value, lo=0)


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
        1000, "--timeout", help="Response timeout in ms.", callback=live_timeout_option
    ),
    retry_ms: int = RETRY_OPTION,
    eol: str | None = EOL_OPTION,
) -> None:
    """Send a monitor command and print its response."""
    _run_cmd(ctx, text, timeout, retry_ms, eol)


# A line or marker may start with '-' (`mcu mark "-pwm duty 50"`): unknown options reach the
# command as its text rather than as a usage error.
_TEXT_ARG = {"ignore_unknown_options": True}


@app.command(context_settings=_TEXT_ARG)
def send(
    ctx: typer.Context,
    text: str = typer.Argument(...),
    eol: str | None = EOL_OPTION,
) -> None:
    """Write one raw line (no response wait). A monitor ignores it: use `cmd` for those."""
    if text == "-":
        die("error: send does not read stdin; give the line itself", 1)
    s = settings_of(ctx)
    client = Client(s)
    res = client.post("/send", {"port": s.port, "line": text, "eol": eol})
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
    if not (char.isascii() and char.isprintable()):
        # One byte, unterminated, is the whole point of SysRq; anything the wire cannot
        # carry as a single visible character is a usage error, refused before the break
        # (a break alone arms SysRq on the target).
        die(f"sysrq takes a printable ASCII character, got {char!r}", 1)
    s = settings_of(ctx)
    client = Client(s)
    client.post("/break", {"port": s.port, "ms": ms})
    client.post("/send", {"port": s.port, "line": char, "eol": "none"})
    if s.json_out:
        out_json({"ok": True, "char": char, "ms": ms})
    else:
        print(f"sysrq {char} (break {ms} ms)")


@app.command(context_settings=_TEXT_ARG)
def mark(ctx: typer.Context, text: str = typer.Argument(...)) -> None:
    """Insert a marker annotation."""
    s = settings_of(ctx)
    res = Client(s).post("/marker", {"port": s.port, "text": text})
    if s.json_out:
        out_json(res)
    else:
        print(f"marker {res['line_id']}")


# -- lines / tail / wait / log --------------------------------------------------------


LINES_PAGE = 1000   # the /lines cap (SPEC 3.4); the CLI pages past it
DEF_LOOKBACK = 20000   # rows before a window's end searched for its !pd definitions


def _lines_params(
    s: Settings, chan: str | None, match: str | None, limit: int, since_id: int | None,
    session: str | None = None, since_ts: float | None = None, id_to: int | None = None,
    until_ts: float | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if s.port:
        params["port"] = s.port
    if chan:
        params["chan"] = chan
    if match:
        params["match"] = match
    if since_id is not None:
        params["since_id"] = since_id
    if session:
        params["session"] = session
    if since_ts is not None:
        params["since_ts"] = since_ts
    if until_ts is not None:
        params["until_ts"] = until_ts
    if id_to is not None:
        params["id_to"] = id_to
    return params


# The daemon's per-query regex budget (store.MATCH_BUDGET_S), duplicated like MAX_TIMEOUT_MS.
# A request carrying `match` waits past it, so the daemon's own answer (the rows, or its
# budget refusal) arrives instead of a client timeout that reads as something else.
MATCH_BUDGET_S = 30.0
READ_TIMEOUT_S = 30.0


def _get_rows(s: Settings, path: str, params: dict[str, Any]) -> Any:
    """GET a row endpoint, allowing a `match` scan the daemon's whole budget."""
    extra = MATCH_BUDGET_S if params.get("match") else 0.0
    return Client(s).get(path, params=params, timeout=READ_TIMEOUT_S + extra)


def _fetch_lines(s: Settings, params: dict[str, Any], limit: int) -> dict[str, Any]:
    """GET /lines for the newest `limit` rows, paging past the endpoint's 1000-row cap."""
    return _fetch_newest(s, "/lines", "lines", "id", params, limit)


def _fetch_after(s: Settings, params: dict[str, Any], limit: int) -> dict[str, Any]:
    """The `limit` rows just above `params["since_id"]`, walked upwards (`--since-id`).

    Returned newest first like _fetch_lines. `truncated` means more rows follow the newest
    one returned: call again from its id, and nothing between two calls is skipped.
    """
    rows: list[dict[str, Any]] = []
    params = _pin_ceiling(s, {**params, "order": "asc"})
    truncated = False
    while True:
        params["limit"] = min(LINES_PAGE, limit - len(rows))
        body = _get_rows(s, "/lines", params)
        page = _list_field(body, "lines")
        rows.extend(page)
        truncated = bool(body.get("truncated"))
        last = _highest_id(page)
        if not truncated or len(rows) >= limit or last is None:
            break
        params["since_id"] = last
    return {"lines": rows[::-1], "truncated": truncated}


def _highest_id(page: list[dict[str, Any]]) -> int | None:
    """The highest integer `id` on an ascending page, None when no row carries one.

    Not the last row's: one malformed row there ended a walk as if the window were
    complete (as api.js `oldestId` reads it).
    """
    ids = [r["id"] for r in page
           if isinstance(r.get("id"), int) and not isinstance(r["id"], bool)]
    return max(ids) if ids else None


def _newest_id(page: list[Any], id_key: str) -> int:
    """The id of a newest-first page's first row, or 0 when it has none."""
    head = page[0] if page else None
    value = head.get(id_key) if isinstance(head, dict) else None
    return value if isinstance(value, int) else 0


def _fetch_newest(
    s: Settings, path: str, key: str, id_key: str, params: dict[str, Any], limit: int,
) -> dict[str, Any]:
    """The newest `limit` rows of `path` (/lines or /can/frames), past its 1000-row cap.

    Pages walk `id_to` downwards, so every filter applies unchanged to each page. The
    result has the endpoint's shape (`key` newest first, `truncated`), with `truncated`
    meaning rows exist beyond the `limit` asked for, not beyond one page.
    """
    rows: list[dict[str, Any]] = []
    params = dict(params)
    while True:
        # Always at least one request: `limit=0` is the "no backfill" probe, and its
        # `truncated` flag is the whole answer (SPEC 4).
        params["limit"] = min(LINES_PAGE, limit - len(rows))
        body = _get_rows(s, path, params)
        page = _list_field(body, key)
        truncated = bool(body.get("truncated"))
        if "id_to" in params and _newest_id(page, id_key) > params["id_to"]:
            break   # a daemon ignoring `id_to` answers the same page again, for ever
        rows.extend(page)
        if not truncated or not page or len(rows) >= limit:
            break
        oldest = page[-1].get(id_key) if isinstance(page[-1], dict) else None
        if not isinstance(oldest, int) or oldest <= 1:
            break
        params["id_to"] = oldest - 1
    return {key: rows, "truncated": truncated}


def _pin_ceiling(s: Settings, params: dict[str, Any]) -> dict[str, Any]:
    """`params` with `id_to` pinned at the daemon's id ceiling for `until_ts`, if one is set.

    The ceiling is the newest row at or below `until_ts`, asked once with no other filter;
    `until_ts` still rides on every page, and the daemon then skips its ceiling walk, the
    length of the window, on each page of an ascending walk.
    """
    if params.get("until_ts") is None:
        return params
    top = _list_field(Client(s).get("/lines", params={
        "until_ts": params["until_ts"], "order": "desc", "limit": 1,
    }), "lines")
    ceiling = top[0].get("id") if top and isinstance(top[0], dict) else None
    if not isinstance(ceiling, int):
        # Nothing at or below until_ts. 0 still sends the page, so the daemon's own
        # refusals (an unknown session, a bad match) are answered.
        ceiling = 0
    return {**params, "id_to": min(params.get("id_to", ceiling), ceiling)}


def _iter_pages_asc(s: Settings, params: dict[str, Any]) -> Iterator[list[dict[str, Any]]]:
    """Pages of matching rows, oldest first, until the window is exhausted (exports)."""
    params = _pin_ceiling(s, {**params, "order": "asc", "limit": LINES_PAGE})
    while True:
        body = _get_rows(s, "/lines", params)
        page = _list_field(body, "lines")
        yield page
        if not body.get("truncated"):
            return
        last = _highest_id(page)
        if last is None:
            die(f"unexpected response from daemon: a truncated page after id "
                f"{params.get('since_id', 0)} carries no row id to continue from", 1)
        params["since_id"] = last


def _absolute_window(
    s: Settings, since_ts: float | None, last_ms: int | None, session: str | None,
) -> float | None:
    """`--last-ms` as a fixed `since_ts`, taken once before paging.

    The daemon evaluates `last_ms` against its clock per request, so a paged query would
    slide its old edge forward by however long the earlier pages took, and drop rows
    there while reporting the export complete.

    Counted back from where the daemon anchors `last_ms` (SPEC 3.4): the newest line of an
    ended `--session`, else now on the daemon's clock (`/status` `now`), which stamps the
    rows. A session this lookup cannot find counts from now, and the query itself then
    answers for it.
    """
    if last_ms is None:
        return since_ts
    anchor = None
    if session:
        client = Client(s)
        row = next(iter(
            _list_field(client.get("/sessions", params={"name": session}), "sessions")
        ), None)
        if row is not None and isinstance(row.get("end_id"), int):
            newest = _list_field(
                client.get("/lines", params={"id_to": row["end_id"], "limit": 1}), "lines"
            )
            if newest and isinstance(newest[0], dict):
                anchor = newest[0].get("ts")
    if not isinstance(anchor, (int, float)) or isinstance(anchor, bool):
        status = Client(s).get("/status")
        anchor = status.get("now") if isinstance(status, dict) else None
        if not isinstance(anchor, (int, float)) or isinstance(anchor, bool):
            die("unexpected response from daemon: /status 'now' is not a number", 1)
    cut = anchor - last_ms / 1000
    # One float below: `since_ts` is strict and the daemon's `last_ms` floor inclusive, so
    # `--last-ms 0` keeps the anchor row as the daemon does.
    cut = math.nextafter(cut, -math.inf)
    return cut if since_ts is None else max(since_ts, cut)


# Widest `--last-ms`, mirroring server.MAX_MS; duplicated like MAX_TIMEOUT_MS. Bounded here
# because `--last-ms` becomes a `since_ts` before the daemon's own bound can see it.
MAX_WINDOW_MS = 10**15
LAST_MS_OPTION = typer.Option(
    None, "--last-ms", min=0, max=MAX_WINDOW_MS, help="Only the last N ms of the capture."
)


def _clock_bounds(
    from_: str | None, to: str | None,
) -> tuple[float | None, float | None]:
    """--from as `since_ts` and --to as `until_ts`, both applied by the daemon itself."""
    since_ts = parse_clock(from_) if from_ else None
    until_ts = parse_clock(to) if to else None
    if since_ts is not None and until_ts is not None and since_ts > until_ts:
        # Refused here as well as in the daemon: silent emptiness reads as "nothing
        # happened", and backwards bounds are a mistake (an overnight window needs the
        # date form, since bare clocks are today's).
        raise typer.BadParameter(f"--from {from_} is after --to {to}", param_hint="--to")
    return since_ts, until_ts


def _make_decoder(
    s: Settings, decode: bool, changes: bool, names: str | None, id_to: int | None = None,
) -> LineDecoder | None:
    """A primed LineDecoder for --decode, or None when decoding is off.

    Primed with the newest definition per port and sid as of `id_to` (the window's first
    row, or the newest row for a live tail), so a run recorded before a reflash decodes
    against the firmware that produced it, and a window shorter than the 5 s !pd rebroadcast
    still decodes at all. Anything redeclared inside the window is learned as it streams
    past, which is why rows must be fed oldest first. No session bound: a definition declared
    just before a session started describes its samples, as on /plot/export (SPEC 9.2).
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
    # streams, against the store's regex budget. Every !pd in that lookback, not a newest-N:
    # with many boards one board's rebroadcasts crowded another's definition out of the cap.
    since_id = max(0, id_to - DEF_LOOKBACK) if id_to is not None else None
    params = _lines_params(s, "event", "^!pd ", LINES_PAGE, since_id, id_to=id_to)
    rows = [r for page in _iter_pages_asc(s, params) for r in page if isinstance(r, dict)]
    for r in reversed(rows):   # newest first: prime keeps the first seen per port and sid
        dec.prime([r["raw"]], r.get("port"))
    return dec


def _decode_pages(
    s: Settings, pages: Iterable[list[dict[str, Any]]], decode: bool, changes: bool,
    names: str | None, session: str | None, filtered: bool = False,
    baseline: LineDecoder | None = None,
) -> Iterator[dict[str, Any]]:
    """Rows from chronological `pages`, decoded (or as they are when decoding is off).

    Definitions are primed as of the window's *first* row and every `!pd` inside the
    window is learned as it streams past, so a stream redefined mid-window (same wire
    width, new names) decodes each half with its own definition. `filtered` means a
    `--match`/`--chan` kept those `!pd` rows out of the pages, so each page's id range
    is asked for them separately. `baseline` shares its --changes state with this decoder.
    """
    dec: LineDecoder | None = None
    primed = False
    for page in pages:
        rows = [r for r in page if isinstance(r, dict)]
        ids = [r["id"] for r in rows if isinstance(r.get("id"), int)]
        if not primed and ids:
            dec = _make_decoder(s, decode, changes, names, id_to=ids[0])
            if dec is not None and baseline is not None:
                dec.share_changes(baseline)
            primed = True
        if dec is None:
            yield from rows
            continue
        defs: list[dict[str, Any]] = []
        if filtered and ids:
            params = _lines_params(
                s, "event", "^!pd ", LINES_PAGE, ids[0] - 1, session, id_to=ids[-1]
            )
            defs = [d for pg in _iter_pages_asc(s, params) for d in pg]
        di = 0
        for row in rows:
            rid = row.get("id")
            while di < len(defs) and isinstance(rid, int) and defs[di]["id"] < rid:
                dec.decode(defs[di]["raw"], defs[di].get("port"))   # learn only, not a row
                di += 1
            out = _decoded_row(dec, row)
            if out is not None:
                yield out


def _decoded_row(dec: LineDecoder | None, row: dict[str, Any]) -> dict[str, Any] | None:
    """`row` with its raw text decoded (in `raw`, and `decoded` for --json); None to drop."""
    if dec is None:
        return row
    text = dec.decode(row["raw"], row.get("port"))
    if text is None:
        return None
    return {**row, "raw": text, "decoded": text}


def _port_column(s: Settings, rows: Iterable[Any] | None = None) -> bool:
    """Whether text rows carry a `[port]` column: they can come from more than one board.

    Judged on `rows` for a finished result, else (a stream) on the ports attached plus the
    ports with stored rows, the daemon's own rule for its text export. Without -p a read
    spans every port, and interleaved boards are otherwise indistinguishable.
    """
    if rows is None:
        return not (s.port or s.json_out) and len(_stream_boards(s)) > 1
    if s.json_out:
        return False
    # Port "" is the daemon's own rows (SPEC 3.5), not a board.
    return len({r.get("port") for r in rows if isinstance(r, dict)} - {""}) > 1


def _stream_boards(s: Settings) -> set[str]:
    """The boards a stream's rows can come from: the ports attached plus the ports with
    stored rows."""
    body = Client(s).probe("GET", "/ports")
    body = body if isinstance(body, dict) else {}
    ports, stored = body.get("ports"), body.get("stored")
    # One board detached with history plus another attached is two boards, so the union.
    names = [p.get("alias") for p in ports if isinstance(p, dict)] \
        if isinstance(ports, list) else []
    if isinstance(stored, list):
        names += stored
    return {n for n in names if isinstance(n, str)} - {""}


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


def order_option(value: str | None) -> str | None:
    if value is not None and value not in ("asc", "desc"):
        raise typer.BadParameter(f"expected asc or desc, got {value!r}", param_hint="--order")
    return value


@app.command()
def lines(
    ctx: typer.Context,
    last_ms: int | None = LAST_MS_OPTION,
    from_: str | None = FROM_OPTION,
    to: str | None = TO_OPTION,
    chan: str | None = typer.Option(None, "--chan"),
    match: str | None = typer.Option(None, "--match"),
    limit: int = typer.Option(
        100, "--limit", min=0, help="Rows to return (raw rows, before --changes/--names)."
    ),
    since_id: int | None = typer.Option(
        None, "--since-id",
        help="The next --limit rows above this id, oldest first from the daemon; a "
             "truncated answer means call again from the newest id returned.",
    ),
    session: str | None = typer.Option(None, "--session", help="Scope to a session name/id."),
    decode: bool = DECODE_OPTION,
    changes: bool = CHANGES_OPTION,
    names: str | None = NAMES_OPTION,
    order: str | None = typer.Option(
        None, "--order", callback=order_option, metavar="asc|desc",
        help="Row order: asc is oldest first, desc newest first "
             "(default: asc for text, desc for --json).",
    ),
) -> None:
    """Query the capture (the AI workhorse). Text is oldest first; --json newest first."""
    s = settings_of(ctx)
    since_ts, until_ts = _clock_bounds(from_, to)
    since_ts = _absolute_window(s, since_ts, last_ms, session)
    params = _lines_params(
        s, chan, match, limit, since_id, session, since_ts, until_ts=until_ts
    )
    fetch = _fetch_lines if since_id is None else _fetch_after
    body = fetch(s, params, limit)
    rows = list(_decode_pages(
        s, [body["lines"][::-1]], decode, changes, names, session, bool(match or chan)
    ))   # oldest first
    if s.json_out:
        newest_first = rows if order == "asc" else rows[::-1]   # the API's order by default
        out_json({"lines": newest_first, "truncated": body["truncated"]})
        return
    show_port = _port_column(s, body["lines"])
    for row in rows[::-1] if order == "desc" else rows:
        print(fmt_line(row, show_port))
    if since_id is None:
        note_truncated(body, limit)
    else:
        newest = body["lines"][0].get("id") if body["lines"] else since_id
        note_truncated(body, limit, fallback=f"call again with --since-id {newest}",
                       beyond="newer")


@app.command()
def tail(
    ctx: typer.Context,
    n: int = typer.Option(20, "-n", min=0, help="Number of recent lines to show first."),
    follow: bool = typer.Option(False, "-f", "--follow", help="Follow live via WebSocket."),
    chan: str | None = typer.Option(None, "--chan"),
    match: str | None = typer.Option(None, "--match"),
    decode: bool = DECODE_OPTION,
    changes: bool = CHANGES_OPTION,
    names: str | None = NAMES_OPTION,
) -> None:
    """Show recent lines, optionally following live."""
    s = settings_of(ctx)
    dec = _make_decoder(s, decode, changes, names)
    if not follow:
        _tail_snapshot(s, chan, match, n, dec)
        return
    # A stream: judged on the ports attached or stored, and again on each row's port, since a
    # board attached during the follow makes its rows ambiguous from then on.
    boards = None if s.port or s.json_out else _stream_boards(s)
    # Subscribe *first*, then take the snapshot. The other order silently lost every line
    # that landed between the GET /lines answer and the /ws subscription: the follow only
    # ever saw what arrived after it connected. With the socket already open those lines
    # are staged in memory while the snapshot prints, then replayed after it and deduped
    # by row id - the order the web UI's backfill uses, for the same reason.
    _follow_ws(
        s, chan, match, dec=dec, boards=boards,
        backfill=lambda: _tail_snapshot(s, chan, match, n, dec, bool(boards and len(boards) > 1)),
    )


def _tail_snapshot(
    s: Settings, chan: str | None, match: str | None, n: int, dec: LineDecoder | None = None,
    show_port: bool | None = None,
) -> int:
    """Print the recent-lines snapshot, oldest first. Returns the newest id printed.

    That id is the follow's dedupe watermark; 0 when the snapshot was empty (or carried
    no ids), which lets the follow replay everything it staged. `show_port` None judges
    the port column on the snapshot's own rows.
    """
    params = _lines_params(s, chan, match, n, None)
    body = _fetch_lines(s, params, n)
    if show_port is None:
        show_port = _port_column(s, body["lines"])
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
            s, [list(rows)], True, dec.changes, dec.names, None, bool(chan or match), baseline=dec
        )
    for row in rows:
        # emit_stream, not out_json: that swallows a closed pipe, and `--json tail -f | head -1`
        # then followed into devnull for ever.
        emit_stream(json.dumps(row) if s.json_out else fmt_line(row, show_port))
    # stderr, so a JSONL stdout stream stays parseable. Not for `-n 0`, the follow-only form,
    # where older rows exist by definition.
    if n:
        note_truncated(body, n, opt="-n")
    return watermark


# Ceiling on one client-side `--match` search, mirroring store.MATCH_TIMEOUT_S. The value is
# duplicated rather than imported so the CLI does not pull the daemon's SQLite stack in.
FOLLOW_MATCH_TIMEOUT_S = 0.25
MAX_MATCH_LEN = 200   # server.MAX_MATCH_LEN


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


def _accepts_tcp(url: str) -> bool:
    """True if a TCP connection to `url`'s host:port opens within 2 s: tells a daemon that
    accepted and then stalled (exit 1) from one that is not there (3)."""
    import socket

    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)   # as websockets
        with socket.create_connection((parsed.hostname or "127.0.0.1", port), timeout=2.0):
            return True
    except (OSError, ValueError):
        return False


def _follow_ws(
    s: Settings, chan: str | None, match: str | None,
    backfill: Callable[[], int] | None = None, dec: LineDecoder | None = None,
    boards: set[str] | None = None,
) -> None:
    """Follow /ws. `boards` is the set a `[port]` column is judged on, grown by each row's
    port; None prints no column (`-p`, `--json`)."""
    import asyncio

    import regex
    import websockets

    if output_failed():
        raise typer.Exit(1)   # stdout closed at start: nothing could ever be delivered
    try:
        # websockets raises the ValueError of a bad port at connect, where it read as a
        # malformed frame (exit 1); the REST commands answer it as a bad url (exit 3).
        parsed = urllib.parse.urlsplit(s.url)
        parsed.port   # noqa: B018 - evaluated for its ValueError
        if not parsed.hostname:
            raise ValueError("no host")
    except ValueError as exc:
        die_bad_url(s.url, exc)
    ws_url = s.url.replace("http", "ws", 1) + "/ws"
    if s.port:
        # Quoted, or `-p 'sim&chan=sys'` followed port `sim` with every channel.
        ws_url += f"?port={urllib.parse.quote(s.port, safe='')}"
    # `regex`, not stdlib `re`, so --match means the same thing here as it does in the
    # daemon (which compiles every user pattern with it): `\p{L}` matched the first
    # batch through GET /lines and then killed the follow with a re.error traceback.
    # The daemon's own compile (flags and repeat budget) and length cap, so the follow
    # refuses exactly what `lines --match` does.
    if match and len(match) > MAX_MATCH_LEN:
        die(f"bad --match pattern: too long (max {MAX_MATCH_LEN} chars)", 1)
    from .store import PatternTooLarge, compile_user_regex

    try:
        pat = compile_user_regex(match) if match else None
    except PatternTooLarge as exc:
        die(f"bad --match pattern: too large: {exc}", 1)
    except regex.error as exc:
        die(f"bad --match pattern: {exc}", 1)
    except RecursionError:
        die("bad --match pattern: nested too deeply", 1)

    headers = s.headers()

    show_port = boards is not None and len(boards) > 1

    async def run() -> None:
        drops = _DropCounter("frame")
        watermark = 0

        def handle(payload: Any) -> None:
            nonlocal show_port
            # A malformed frame or row is charged to that item, never to the follow: one
            # bad frame used to end `mcu tail -f` outright. Only parsing is guarded - a
            # closed connection is not a bad frame, and stays with the outer handlers.
            try:
                # Each frame is an array of rows (SPEC 3.4).
                rows = json.loads(payload)
                if not isinstance(rows, list):
                    raise ValueError("frame is not an array of rows")
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
                        dec.decode(row["raw"], row.get("port"))   # learn it even where hidden
                    if chan and row["chan"] != chan:
                        continue
                    if pat and not _follow_match(pat, row["raw"]):
                        continue
                    row = _decoded_row(dec, row)
                    if row is None:
                        continue
                    port = row.get("port")
                    if boards is not None and isinstance(port, str) and port not in boards:
                        boards.add(port)   # port "" (the daemon's rows) is not a board
                        show_port = len(boards - {""}) > 1
                    text = json.dumps(row) if s.json_out else fmt_line(row, show_port)
                except (AttributeError, KeyError, TypeError, ValueError) as exc:
                    # AttributeError: a `raw` that is not a string, under --decode.
                    drops.bad(exc)
                    continue
                emit_stream(text)   # outside the guard: EPIPE ends the follow
            if drops.total == before:
                drops.ok()

        try:
            # 16 MiB: a frame coalesces up to 500 rows of up to 4 KB each (about 2 MB), past
            # the library's 1 MiB default, which closed a healthy follow as exit 3. A cap
            # with headroom, not None: no limit buffers whatever a wrong service sends.
            async with websockets.connect(
                ws_url, additional_headers=headers or None, max_size=16 * 1024 * 1024
            ) as ws:
                check_daemon_version(s.url, ws.response.headers)
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
        except (TimeoutError, asyncio.TimeoutError) as exc:
            # The opening timeout spans the TCP connect and the handshake. Before OSError:
            # on 3.10 asyncio's TimeoutError is not an OSError and escaped as a traceback.
            if _accepts_tcp(s.url):
                die(f"the daemon at {s.url} accepted the connection but stopped answering: "
                    f"{exc}", 1)
            die(f"daemon unreachable at {s.url}: {exc}{start_hint(s.url)}", 3)
        except OSError as exc:
            die(f"daemon unreachable at {s.url}: {exc}{start_hint(s.url)}", 3)
        except websockets.exceptions.ConnectionClosed as exc:
            # The daemon restarted or shut down under a live follow. That is an ordinary
            # end of stream, not a crash: this used to escape as a 6 KB rich traceback
            # because websockets' exceptions derive from Exception, not OSError.
            # `exc.rcvd`, not the deprecated `exc.code`: only a close the daemon sent
            # carries a policy code at all.
            if exc.rcvd is not None and exc.rcvd.code == 1008:
                # Host, same-origin or token guard, or a `port` naming no attached port
                # (SPEC 3.4). The daemon is plainly there, and the same refusal over REST
                # exits 1, so 3 (unreachable) would be a lie in both directions. The reason
                # distinguishes the two; the auth closes send none.
                die(f"stream refused by daemon: {exc.rcvd.reason or 'not authorised'}", 1)
            if exc.rcvd is not None and exc.rcvd.code == 1013:
                # The subscriber cap: a running daemon, so 1 as for the cap's 503 on /wait
                # and /assert. The close carries no reason, so the CLI names the cap.
                die("error: too many subscribers (the daemon's subscriber cap is reached); "
                    "try again later", 1)
            die("stream closed by daemon", 3)
        except websockets.exceptions.InvalidStatus as exc:
            # Something answered the upgrade, so the daemon is reachable: 1, as the same
            # refusal is over REST. A gateway's 502/504 says nothing answered behind it (3).
            status = exc.response.status_code
            die(f"websocket refused by daemon: HTTP {status}", 3 if status in (502, 504) else 1)
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
        2000, "--timeout", help="Timeout in ms.", callback=live_timeout_option
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
        # The daemon's rule, refused here before the round trip and in option names: the
        # daemon's field names are not what this user typed.
        if send_cmd is None:
            die("error: --repeat-ms needs --send", 1)
        if p.repeat_refusal(repeat_ms, timeout, has_send=True, raw=True) is not None:
            die(f"error: --repeat-ms must be between {p.REPEAT_MIN_MS} and --timeout "
                f"({timeout})", 1)
    # An eol the path never reads is bad usage, and the CLI otherwise dropped it silently.
    if eol is not None and send_cmd is None:
        die("error: --eol applies to --send; give --send too", 1)
    body: dict[str, Any] = {"port": s.port, "match": match, "timeout_ms": timeout, "chan": chan}
    if send_cmd is not None:
        body["send"] = send_cmd
        body["send_mode"] = "raw" if raw else "cmd"
        body["eol"] = eol
    if repeat_ms is not None:
        body["repeat_ms"] = repeat_ms
    client = Client(s)
    res = client.post("/wait", body, timeout=timeout / 1000 + 5)
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
    sent = res.get("cmd_result") if isinstance(res.get("cmd_result"), dict) else {}
    if res["status"] == "match" and not s.json_out:
        print(fmt_line(res["line"], _port_column(s)))
    if sent.get("status") == "err":
        # The command the wait was to observe was refused, so its premise is gone: an exit
        # 2 would send the caller to retry with a longer timeout.
        if not s.json_out:
            err(f"--send {send_cmd!r} was refused: " + cmd_err_text(sent))
        raise typer.Exit(1)
    if res["status"] == "match":
        raise typer.Exit(0)
    if not s.json_out:
        # What was waited for, where, for how long and what was sent, so the reader needs
        # no second command. Built from what is in hand; no wire field is added.
        where = f" on port {s.port}" if s.port else ""
        waited = res.get("waited_ms")
        took = f" in {round(waited)} ms" if isinstance(waited, (int, float)) else ""
        count = ""
        # --repeat-ms has already printed its counts above. A single send that failed was
        # a 400, so a timeout never has a failure to report.
        if send_cmd is not None and repeat_ms is None:
            count = f" (sent {res['sends']}"
            count += ", the command got no response)" if sent.get("status") == "timeout" else ")"
        err(f"timeout: no line matched {match!r}{where}{took}{count}")
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
        callback=retrospective_timeout_option,
    ),
    min_window: int = typer.Option(
        0, "--min-window", min=0, max=MAX_TIMEOUT_MS,
        help="Keep a live window open at least this long (ms) even once --expect is met.",
    ),
    session: str | None = typer.Option(None, "--session", help="Judge a stored session."),
    # min 1, not 0: the daemon refuses a zero retrospective window, which would pass every --forbid.
    last_ms: int | None = typer.Option(
        None, "--last-ms", min=1, max=MAX_WINDOW_MS, help="Judge the last N ms."
    ),
    send_cmd: str | None = typer.Option(None, "--send", help="Send this first (live mode)."),
    chan: str | None = typer.Option(None, "--chan"),
    raw: bool = typer.Option(False, "--raw", help="Treat --send as a raw line, not a command."),
    eol: str | None = EOL_OPTION,
    allow_empty: bool = typer.Option(
        False, "--allow-empty",
        help="Pass a window that held no lines at all (by default that is a failure, "
             "'empty': a --forbid over nothing proves nothing).",
    ),
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
    if min_window:
        # The daemon's rules, refused before the round trip and in option names.
        if timeout <= 0:
            die("error: --min-window needs a live window (give --timeout too)", 1)
        if min_window > timeout:
            die("error: --min-window cannot exceed --timeout", 1)
    # An eol the path never reads is bad usage, and the CLI otherwise dropped it silently.
    if eol is not None and send_cmd is None:
        die("error: --eol applies to --send; give --send too", 1)
    body: dict[str, Any] = {
        "expect": list(expect), "forbid": list(forbid),
        "timeout_ms": timeout, "min_window_ms": min_window, "chan": chan, "port": s.port,
    }
    if allow_empty:
        body["allow_empty"] = True
    if session:
        body["session"] = session
    if last_ms is not None:
        body["last_ms"] = last_ms
    if send_cmd is not None:
        body["send"] = send_cmd
        body["send_mode"] = "raw" if raw else "cmd"
        body["eol"] = eol
    client = Client(s)
    # Retrospective, each pattern is one daemon scan with its own match budget; the answer
    # (a verdict, or the budget's refusal) must arrive before this client gives up.
    budget = timeout / 1000 if timeout else (len(expect) + len(forbid)) * MATCH_BUDGET_S
    res = client.post("/assert", body, timeout=budget + READ_TIMEOUT_S)
    # Same as `wait`: a window with holes in it has not been judged over that window, and a
    # forbid that "did not match" over it is the dangerous direction.
    if res.get("dropped"):
        err(f"warning: {res['dropped']} lines were shed during the window; "
            "the verdict does not cover them, so retry rather than trust it")
    if s.json_out:
        out_json(res)
    else:
        sent = res.get("cmd_result")
        send_failed = isinstance(sent, dict) and sent.get("status") in ("err", "timeout")
        if send_failed:
            why = cmd_err_text(sent) if sent["status"] == "err" else "no response (timeout)"
            err(f"  FAILED  send {send_cmd!r}: {why}; the window judged no stimulus")
        for check in _list_field(res, "expect"):
            if check["matched"]:
                print(f"  ok      expect {check['pattern']!r}: "
                      f"{one_line(_field(check, 'line')['raw'])}")
            else:
                err(f"  FAILED  expect {check['pattern']!r}: never seen")
        for check in _list_field(res, "forbid"):
            if check["matched"]:
                err(f"  FAILED  forbid {check['pattern']!r}: "
                    f"{one_line(_field(check, 'line')['raw'])}")
            elif send_failed:
                # The daemon judges no window after a failed send: "never seen" would read
                # as a clean result.
                print(f"  -       forbid {check['pattern']!r}: not judged")
            else:
                print(f"  ok      forbid {check['pattern']!r}: never seen")
        if res["status"] == "empty":
            err("EMPTY  the window held no lines, so nothing was judged (wrong -p, --session "
                "or --chan, or a silent board?); --allow-empty accepts it")
        else:
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
    limit: int = typer.Option(20, "--limit", min=0),
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
    bundle: bool = typer.Option(
        False, "--bundle",
        help="Write a zip of the capture DB, the decoded lines, the plot and CAN CSVs "
             "and a manifest, instead of the bare .db.",
    ),
) -> None:
    """Save one session as a standalone capture database, or with --bundle as a zip.

    The .db is a normal MCUscope capture, so an archived run stays queryable with the same
    tools as the live one instead of becoming a dead format. --bundle adds the rendered
    views of the same run for anything that will not open a SQLite file.
    """
    s = settings_of(ctx)
    _refuse_stdout_token(out_file)
    if out_file.endswith(("/", os.sep)):
        # Before the .zip suffix below, which turned `-o DIR/` into a hidden `DIR/.zip`.
        die(f"-o {out_file} is a directory; give a file path", 1)
    if bundle:
        # Case-folded: on Windows, the OS the cross-platform mandate exists for, "run.DB"
        # and "run.db" name the same file, so a case-sensitive guard is no guard.
        if out_file.lower().endswith(".db"):
            die("--bundle writes a zip, not a .db", 1)
        if not os.path.splitext(out_file)[1]:
            out_file += ".zip"
    if os.path.isdir(out_file):
        # The final path: `-o run-3` beside a `run-3/` directory writes `run-3.zip`.
        die(f"-o {out_file} is a directory; give a file path", 1)
    client = Client(s)
    # By id: a session name is free text, and `/`, `?` or `#` in it would restructure the path.
    ref = _resolve_session(client, name)["id"]
    path = f"/sessions/{ref}/{'bundle' if bundle else 'export'}"
    written = client.download(path, out_file)
    if s.json_out:
        out_json({"file": out_file, "bytes": written})
    else:
        print(f"wrote {written} bytes to {out_file}")


def _resolve_session(client: Client, name: str) -> dict[str, Any]:
    """The session row `name` (a name or an id) refers to, or exit 1."""
    # name= resolves server-side through the sessions name index, so a session past the
    # first page is still found (paging the list was capped at the endpoint's own 1000).
    body = client.get("/sessions", params={"name": name})
    match = next(iter(_list_field(body, "sessions")), None)
    if match is None:
        die(f"no such session: {name}", 1)
    return match


@session_app.command("delete")
def session_delete(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Session name or id."),
    data: bool = typer.Option(False, "--data", help="Also delete the lines it covers."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Delete a session label, and with --data the capture it covers."""
    s = settings_of(ctx)
    match = _resolve_session(Client(s), name)
    if data and not yes:
        confirm_or_exit(
            f"delete session {match['name']} and its {match['lines']} captured lines?"
        )
    res = Client(s).delete(f"/sessions/{match['id']}", params={"data": str(bool(data)).lower()})
    if s.json_out:
        out_json(res)
    else:
        print(f"deleted session {match['name']} ({res['lines_deleted']} lines)")


def _refuse_stdout_token(out_file: str | None) -> None:
    """`-o -` is the conventional stdout token, and no export here honours it.

    Every `-o` path opens the string as a file, so it wrote one named `-` (and `-.zip`
    from the bundle branch, which appends an extension). Refused on all of them rather
    than honoured on none: the commands that can stream already do it when `-o` is left
    off. Called by every command taking `-o`.
    """
    if out_file == "-":
        die("error: -o - is not stdout; omit -o for stdout, or give a file path", 1)


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
    if id_from is not None and id_to is not None and id_from > id_to:
        die(f"--id-from {id_from} is after --id-to {id_to}", 1)
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


def _stdout_untranslated() -> None:
    """Stop stdout translating line endings, so `> FILE` and `-o FILE` write one thing.

    A text stream opened with newline=None (stdout's default) turns every LF it is given
    into os.linesep, so on Windows the piped form of an export came out CRLF where the -o
    form (opened newline="") came out LF: two spellings of the same command, different
    bytes, different sizes, and a CSV whose embedded newlines stop being quoting.
    Guarded like _stdio.widen_stdout_encoding: a replaced or wrapped stdout may not
    reconfigure, and an export must not fail because of it.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    with contextlib.suppress(OSError, ValueError):
        reconfigure(newline="")


class _OutFile:
    """An export's `-o FILE`, opened at its first write rather than before the request.

    Opened up front, a refusal (a 4xx before any byte) truncated the target and the failure
    guard then removed it. Nothing is opened until the daemon has answered below 400 and
    sent something, or the export ended empty, and only a file that was opened is removed.
    """

    def __init__(self, path: str, newline: str) -> None:
        self.path = path
        self.newline = newline
        self.fh: Any = None

    def write(self, text: str) -> None:
        if self.fh is None:
            self.fh = open(self.path, "w", encoding="utf-8", newline=self.newline)  # noqa: SIM115
        self.fh.write(text)

    def close(self) -> None:
        """Open (an empty export is an empty file) and close, flushing inside the caller's guard."""
        self.write("")
        self.fh.close()

    def discard(self) -> None:
        """After a failure: close, and remove what the open created or truncated."""
        if self.fh is None:
            return
        with contextlib.suppress(OSError):
            self.fh.close()
        remove_partial(self.path)


def _stream_export(
    client: Client, path: str, params: dict[str, Any], out_file: str | None
) -> tuple[int, int]:
    """Stream a text export to `out_file` (stdout when None). Returns (lines, bytes).

    A stream that dies mid-transfer leaves a short file that reads exactly like a whole
    one, so a partial file is removed.
    """
    lines = written = 0

    def measure(chunk: str) -> None:
        nonlocal lines, written
        lines += chunk.count("\n")
        written += len(chunk.encode("utf-8"))

    if out_file is None:
        _stdout_untranslated()
        tail = ""

        def to_stdout(chunk: str) -> None:
            nonlocal tail
            measure(chunk)
            # Whole lines only: a failure mid-row appends the --json error object as the
            # final JSONL line (SPEC 4), which a half-written row before it would corrupt.
            head, sep, rest = (tail + chunk).rpartition("\n")
            tail = rest
            try:
                if sep:
                    sys.stdout.write(head + sep)
            except BrokenPipeError:
                # `mcu log export | head`: the reader is done, so we are too. Silence
                # stdout first or the interpreter's shutdown flush prints over us.
                _silence_stdout()
                raise typer.Exit(0) from None

        client.stream_text(path, to_stdout, what="stdout", params=params)
        sys.stdout.write(tail)           # a body whose last line has no newline
        sys.stdout.flush()
        return lines, written

    # newline="" so the body reaches the file byte for byte: the default translates its LF
    # to CRLF on Windows, which breaks both the byte count and a CSV's quoting.
    out = _OutFile(out_file, newline="")
    ok = False
    try:
        def to_file(chunk: str) -> None:
            measure(chunk)
            out.write(chunk)

        client.stream_text(path, to_file, what=out_file, params=params)
        # Closed inside the guarded region: the buffered write is flushed by the close,
        # so a full disk is mapped to exit 1 here rather than raised out of the finally.
        out.close()
        ok = True
    except BrokenPipeError:
        raise                        # handled in main(): the reader closed the pipe
    except OSError as exc:
        die(f"cannot write {out_file}: {exc}", 1)
    finally:
        if not ok:
            out.discard()
    return lines, written


@log_app.command("export")
def log_export(
    ctx: typer.Context,
    last_ms: int | None = LAST_MS_OPTION,
    from_: str | None = FROM_OPTION,
    to: str | None = TO_OPTION,
    chan: str | None = typer.Option(None, "--chan"),
    match: str | None = typer.Option(None, "--match"),
    limit: int = typer.Option(
        0, "--limit", min=0, help="Newest N matching rows; 0 (default) means every row."
    ),
    session: str | None = typer.Option(None, "--session", help="Scope to a session name/id."),
    out_file: str | None = typer.Option(None, "-o", "--out"),
    csv: bool = typer.Option(False, "--csv", help="Export as CSV instead of text."),
    decode: bool = DECODE_OPTION,
    changes: bool = CHANGES_OPTION,
    names: str | None = NAMES_OPTION,
) -> None:
    """Dump matching lines as text, JSONL (--json) or CSV (--csv).

    With -o the dump goes to the file and stdout carries only the result: a "wrote N
    lines" note, or with --json the one object SPEC 4 promises (`{"file", "lines"}`),
    where it used to print nothing at all.

    The whole window is streamed from `/lines/export`, which renders it daemon-side in one
    response. `--limit N` (newest N) and `--decode`/`--changes`/`--names` (decoded against
    the definitions as they change through the window) need rows the client walks itself,
    so those take the paged `/lines` path instead; the output contract is the same either
    way.
    """
    s = settings_of(ctx)
    _refuse_stdout_token(out_file)
    if csv and s.json_out:
        die("--csv and --json are two output formats; pick one", 1)
    paged = bool(limit or decode or changes or names)
    if csv and paged:
        # Name the option actually passed, not a fixed pair.
        passed = ("--limit" if limit else "--decode" if decode
                  else "--changes" if changes else "--names")
        die(f"--csv exports the whole window; it does not take {passed}", 1)
    since_ts, until_ts = _clock_bounds(from_, to)
    since_ts = _absolute_window(s, since_ts, last_ms, session)
    if not paged:   # the daemon renders it, `[port]` column included
        fmt = "csv" if csv else ("jsonl" if s.json_out else "text")
        params = _lines_params(
            s, chan, match, 0, None, session, since_ts, until_ts=until_ts
        )
        params.pop("limit")            # /lines/export takes every matching row
        params["format"] = fmt
        count, size = _stream_export(Client(s), "/lines/export", params, out_file)
        if csv:
            count = max(count - 1, 0)  # the header is not a captured line
        if out_file and s.json_out:
            out_json({"file": out_file, "lines": count, "bytes": size, "truncated": False})
        elif out_file:
            print(f"wrote {count} lines to {out_file}")
        return
    params = _lines_params(
        s, chan, match, limit, None, session, since_ts, until_ts=until_ts
    )
    truncated = False
    show_port = _port_column(s)   # a stream: judged on the ports attached or stored
    pages: Iterable[list[dict[str, Any]]]
    if limit:
        body = _fetch_lines(s, params, limit)
        pages, truncated = [body["lines"][::-1]], body["truncated"]
    else:
        # Decoding needs the rows themselves, streamed a page at a time rather than held
        # whole: a capture is routinely far larger than the process should buffer.
        pages = _iter_pages_asc(s, params)
    rows = _decode_pages(s, pages, decode, changes, names, session, bool(match or chan))

    def render(row: dict[str, Any]) -> str:
        return json.dumps(row) if s.json_out else fmt_line(row, show_port)
    count = size = 0
    if out_file:
        # newline="\n" so the export is LF on every platform: the default (None) translates
        # to CRLF on Windows, which both inflates the file past the "bytes" count below and
        # makes the same capture export differently there. The rows are paged lazily, so
        # the first write, and with it the open, follows the daemon's first answer.
        out = _OutFile(out_file, newline="\n")
        ok = False
        try:
            for row in rows:
                line = render(row) + "\n"
                out.write(line)
                count += 1
                size += len(line.encode("utf-8"))
            out.close()
            ok = True
        except OSError as exc:
            # An unwritable path is a user error, not a crash: it used to reach the user
            # as a raw FileNotFoundError traceback with no exit-code contract.
            die(f"cannot write {out_file}: {exc}", 1)
        finally:
            if not ok:
                # A daemon that dies mid-walk (typer.Exit, not OSError) left a short file
                # that reads as a whole one. Same guard as plot export and Client.download.
                out.discard()
        if s.json_out:
            out_json({"file": out_file, "lines": count, "bytes": size, "truncated": truncated})
        else:
            print(f"wrote {count} lines to {out_file}")
    else:
        # Stdout gets the -o file's bytes: untranslated, as on the streamed path.
        _stdout_untranslated()
        for row in rows:
            print(render(row))
            count += 1
    if truncated:
        note_truncated({"lines": [None] * count, "truncated": True}, limit,
                       fallback="use --limit 0 for every row")


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
BUS_OPTION = typer.Option(
    1, "--bus", min=p.CAN_BUS_MIN, max=p.CAN_BUS_MAX,
    help=f"CAN bus, {p.CAN_BUS_MIN} to {p.CAN_BUS_MAX} (default 1).",
)


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
    can_id: list[str] = typer.Option(  # noqa: B008 - typer option factory
        [], "-i", "--id", help="Only this CAN id (hex). Repeatable."
    ),
    bus: int | None = typer.Option(
        None, "--bus", min=p.CAN_BUS_MIN, max=p.CAN_BUS_MAX, help="Only this bus."
    ),
    last_ms: int | None = LAST_MS_OPTION,
    from_: str | None = FROM_OPTION,
    to: str | None = TO_OPTION,
    session: str | None = typer.Option(None, "--session", help="Scope to a session name/id."),
    n: int = typer.Option(20, "-n", min=0),
    follow: bool = typer.Option(False, "-f", "--follow"),
    out_file: str | None = typer.Option(
        None, "-o", "--out", help="Write the CSV export here (implies --csv)."
    ),
    csv: bool = typer.Option(
        False, "--csv", help="Stream every matching frame as CSV; -n does not apply."
    ),
) -> None:
    """Show decoded CAN frames from the capture, or export them with --csv.

    --csv streams the whole window from the daemon (no `-n` limit and no row cap) to -o or
    to stdout; without it the newest `-n` frames are printed and -f follows live.
    """
    s = settings_of(ctx)
    client = Client(s)
    # The refusals come first, so bad usage costs no request.
    _refuse_stdout_token(out_file)
    csv = csv or out_file is not None
    if csv and follow:
        die("--csv does not follow", 1)
    if to is not None and follow:
        # The follow polls live frames and knows nothing of the bound, so the backfill
        # stopped at --to and the stream then ran past it for ever. --from is fine: it
        # bounds the backfill, and everything live is after it by definition.
        die("error: --to cannot be combined with -f; a follow has no end", 1)
    if csv and s.json_out and out_file is None:
        # With -o the CSV goes to the file, and --json describes it as the siblings do.
        die("--csv and --json are two output formats; pick one", 1)
    since_ts, until_ts = _clock_bounds(from_, to)
    # As `lines` and `log export` do: the daemon re-evaluates `last_ms` against its clock on
    # every request, so a `-n` walk that pages would slide its old edge forward and drop the
    # rows it was walking towards, while reporting the dump complete (SPEC 4).
    since_ts = _absolute_window(s, since_ts, last_ms, session)
    params = _can_params(s, ",".join(can_id) or None, bus, session)
    if since_ts is not None:
        params["since_ts"] = since_ts
    if until_ts is not None:
        params["until_ts"] = until_ts
    if csv:
        params["format"] = "csv"
        rows, size = _stream_export(client, "/can/frames", params, out_file)
        frames_written = max(rows - 1, 0)   # minus the header
        if out_file and s.json_out:
            out_json({"file": out_file, "frames": frames_written, "bytes": size})
        elif out_file:
            print(f"wrote {frames_written} frames to {out_file}")
        return
    body = _fetch_newest(s, "/can/frames", "frames", "line_id", params, n)
    frames = list(reversed(body["frames"]))
    for fr in frames:
        emit_stream(json.dumps(fr) if s.json_out else fmt_frame(fr))   # as in _tail_snapshot
    # stderr, so a JSONL stdout stream stays parseable; not for `-n 0`, the follow-only form
    if n:
        note_truncated({"lines": frames, "truncated": body["truncated"]}, n, opt="-n",
                       fallback="use --csv for every frame")
    if follow:
        _dump_follow(client, s, ",".join(can_id) or None, bus, session)


def _can_params(
    s: Settings, can_id: str | None, bus: int | None, session: str | None,
) -> dict[str, Any]:
    """The `/can/frames` filters `can dump` and its follow both send."""
    params: dict[str, Any] = {}
    if s.port:
        params["port"] = s.port
    if session:
        params["session"] = session   # an ended session's follow then prints nothing new
    if can_id:
        params["id"] = can_id
    if bus is not None:
        params["bus"] = bus
    return params


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
    client: Client, s: Settings, can_id: str | None, bus: int | None = None,
    session: str | None = None,
) -> None:
    if output_failed():
        raise typer.Exit(1)   # stdout closed at start: nothing could ever be delivered
    since = 0
    params = {"limit": 1000, **_can_params(s, can_id, bus, session)}
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
    import httpx

    polls = _DropCounter("update")
    frame_drops = _DropCounter("frame")
    giveup_at: float | None = None
    try:
        while True:
            time.sleep(FOLLOW_POLL_S)
            # A failed poll is charged to that poll, not to the follow: this loop had no
            # handling at all, so one transient httpx error ended `can dump -f` with a
            # traceback (SPEC 4). _poll_frames still dies on what no retry can fix.
            covered: list[int] = []
            try:
                frames = list(reversed(_poll_new_frames(client, params, since, covered)))
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
                    if isinstance(exc, httpx.TimeoutException) and not isinstance(
                        exc, httpx.ConnectTimeout
                    ):
                        die(f"the daemon at {s.url} accepted the request but stopped "
                            f"answering for {FOLLOW_GIVE_UP_S:g}s: {exc}", 1)
                    if not isinstance(exc, httpx.TransportError):
                        # A 5xx or a malformed body is a daemon that answers (SPEC 4: 1).
                        die(f"error: the daemon at {s.url} kept failing for "
                            f"{FOLLOW_GIVE_UP_S:g}s: {exc}", 1)
                    die(f"daemon unreachable at {s.url} for {FOLLOW_GIVE_UP_S:g}s: {exc}", 3)
                continue
            giveup_at = None      # the daemon answered; the clock starts fresh next time
            polls.ok()
            # Past what the answer covered even with no frame in it, so a quiet port's poll
            # costs only what is new rather than a re-read of the whole capture (SPEC 3.4).
            since = max([since, *covered])
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


def _poll_new_frames(
    client: Client, params: dict[str, Any], since: int, covered: list[int] | None = None,
) -> list[Any]:
    """Every frame past `since`, newest first, paging down `id_to` past the 1000-frame cap.

    More than a page arrives between polls at a high frame rate or after a failed-poll
    episode, and one capped page silently dropped the older frames. `since == 0` (a new
    capture) stays one page: the replay after a capture change is bounded by design.
    Each page's `next_since_id`, the highest line id it covered, is appended to `covered`.
    """
    page_params = {**params, "since_id": since}
    frames: list[Any] = []
    while True:
        body = _poll_frames(client, page_params)
        # Not _list_field, whose die() is outside the follow's guards: a malformed answer is
        # a failed poll (counted, retried), and a non-object entry is the per-frame guard's.
        page = body.get("frames") if isinstance(body, dict) else None
        if not isinstance(page, list):
            raise ValueError("'frames' is not a list")
        mark = body.get("next_since_id")
        if covered is not None and isinstance(mark, int) and not isinstance(mark, bool):
            covered.append(mark)
        if "id_to" in page_params and _newest_id(page, "line_id") > page_params["id_to"]:
            return frames   # a daemon ignoring `id_to` answers the same page again, for ever
        frames.extend(page)
        if since == 0 or not body.get("truncated") or not page:
            return frames
        oldest = page[-1].get("line_id") if isinstance(page[-1], dict) else None
        if not isinstance(oldest, int) or oldest <= since + 1:
            return frames
        page_params["id_to"] = oldest - 1


def _poll_frames(client: Client, params: dict[str, Any]) -> Any:
    """One `can dump -f` poll, raising on a failure a later poll could survive.

    The mirror image of the per-item rule (review class 16): a guard that keeps looping
    must still recognise what is not per-item. A url httpx cannot parse and a 4xx (a
    filter the daemon rejects) are answers no retry changes, so they end the follow;
    transport failures, timeouts and 5xx are left to the caller to count and retry.
    """
    import httpx

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
    # -p narrows to one board: two boards declaring one name otherwise merge into one row.
    body = Client(s).get("/plot/channels", params={"port": s.port} if s.port else None)
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
        # Through the --decode renderer's formatter: a raw float repr shows seventeen
        # significant figures of a 32-bit float next to a value that happens to round,
        # and the inconsistency reads as a fault in the capture. --json keeps the float.
        last = ch["last_value"]
        print(
            f"{ch['name']:<16} {sid:<6} {typ:<3} "
            f"last={_fmt_value(last) if isinstance(last, float) else last}{unit}  "
            f"age={age}  n={ch['count']}"
        )


@plot_app.command("export")
def plot_export(
    ctx: typer.Context,
    names: str = typer.Option(..., "--names", help="Comma-separated channel names."),
    last_ms: int | None = LAST_MS_OPTION,
    from_: str | None = FROM_OPTION,
    to: str | None = TO_OPTION,
    session: str | None = typer.Option(None, "--session", help="Scope to a session name/id."),
    wide: bool = typer.Option(False, "--wide", help="One sample per row (shared stream)."),
    out_file: str | None = typer.Option(None, "-o", "--out"),
    decode: bool = typer.Option(
        False, "--decode",
        help="Render values through the stream's !pd: enum labels, and one "
             "<channel>.<lane> column per bit lane.",
    ),
    changes: bool = typer.Option(
        False, "--changes", help="With --decode: emit a row only when a value changed."
    ),
    deadband: str | None = typer.Option(
        None, "--deadband", metavar="NAME=V,...",
        help="With --changes: treat a numeric move of V or less as unchanged.",
    ),
) -> None:
    """Export channel history as CSV (long by default, --wide for one sample per row).

    The body is streamed rather than read whole: this is the one endpoint that can return
    a very large response (every sample of every named channel over a long run).

    CSV is not JSON, so --json wraps it: with -o, one object describing the file; without,
    one object carrying the CSV in a "csv" field. Either way stdout stays parseable, where
    it used to be raw CSV (or, with -o, empty).
    """
    s = settings_of(ctx)
    _refuse_stdout_token(out_file)
    # Refused client-side in the daemon's own words, so the two refusals read alike and
    # the round trip is skipped for a request it can never accept.
    if changes and not decode:
        die("error: changes requires decode", 1)
    if deadband is not None and not changes:
        die("error: deadband requires changes", 1)
    since_ts, until_ts = _clock_bounds(from_, to)
    params: dict[str, Any] = {"names": names, "format": "wide" if wide else "long"}
    if last_ms is not None:
        params["last_ms"] = last_ms
    if since_ts is not None:
        params["since_ts"] = since_ts
    if until_ts is not None:
        params["until_ts"] = until_ts
    if session:
        params["session"] = session
    if s.port:
        params["port"] = s.port   # two boards can declare one channel name (SPEC 9.2)
    if decode:
        params["decode"] = "1"
    if changes:
        params["changes"] = "1"
    if deadband is not None:
        params["deadband"] = deadband
    client = Client(s)

    if s.json_out and not out_file:
        parts: list[str] = []
        newlines = 0

        def to_list(chunk: str) -> None:
            nonlocal newlines
            newlines += chunk.count("\n")
            parts.append(chunk)

        client.stream_text("/plot/export", to_list, what="stdout", params=params)
        out_json({
            "names": names, "format": "wide" if wide else "long",
            "rows": max(newlines - 1, 0), "csv": "".join(parts),
        })
        return

    newlines, size = _stream_export(client, "/plot/export", params, out_file)
    if out_file:
        rows = max(newlines - 1, 0)  # minus the header
        if s.json_out:
            out_json({"file": out_file, "rows": rows, "bytes": size})
        else:
            print(f"wrote {rows} rows to {out_file}")


# -- daemon control -------------------------------------------------------------------


daemon_app = typer.Typer(help="Start/stop/check the local mcuscoped daemon.")
app.add_typer(daemon_app, name="daemon")


CONFIG_OPTION = typer.Option(
    None, "--config", "-c", help="Config file for the daemon (forwarded as mcuscoped -c)."
)
SIM_OPTION = typer.Option(
    False, "--sim", help="Start with the bundled simulator attached (zero-hardware demo)."
)
START_TIMEOUT_OPTION = typer.Option(
    DAEMON_START_TIMEOUT_S, "--timeout", "-t", metavar="SECONDS",
    help="Seconds to wait for the daemon to answer /status (env MCUSCOPE_START_TIMEOUT).",
    callback=finite_option,
    show_default="20 unless MCUSCOPE_START_TIMEOUT is set",
)
OPEN_OPTION = typer.Option(
    False, "--open", help="Open the web UI in the default browser once the daemon answers."
)


def _ui_url(s: Settings) -> str:
    return s.url + "/ui/"


def _named_config(config: str | None) -> str | None:
    """The config file a start names (--config, else MCUSCOPED_CONFIG) as an absolute path.

    A named file that does not exist is refused before anything is probed, stopped or
    spawned, as mcuscoped refuses it. `~` and a relative path are resolved here and the
    result forwarded, so the daemon opens the file that was checked.
    """
    named = config or os.environ.get("MCUSCOPED_CONFIG")
    if not named:
        return None
    path = os.path.abspath(os.path.expanduser(named))
    if not os.path.isfile(path):
        die(f"no such config file: {path}", 1)
    return path


@daemon_app.command("start")
def daemon_start(
    ctx: typer.Context,
    config: str | None = CONFIG_OPTION,
    sim: bool = SIM_OPTION,
    wait_s: float = START_TIMEOUT_OPTION,
    open_ui: bool = OPEN_OPTION,
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
    if open_ui and settings_of(ctx).json_out:
        # The browser command inherits this stdout (a console browser, BROWSER=cmd), and
        # anything it prints lands after the JSON object.
        die("--open cannot be combined with --json", 1)
    _start_daemon(ctx, _named_config(config), sim, wait_s, open_ui)


def _start_daemon(
    ctx: typer.Context, config: str | None, sim: bool, wait_s: float, open_ui: bool,
) -> None:
    """The spawn itself. `config` is already settled: either a path the user typed, checked
    and resolved by _named_config, or one a running daemon reported, which `restart`
    forwards unchanged (it is resolved in the daemon's frame, not in the CLI's)."""
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
    # The daemon's stderr goes to a file rather than DEVNULL: a start that fails (a bad
    # config, a port in use, a missing module) otherwise leaves nothing to read.
    err_path: str | None = _stderr_log_path(pid_path)
    err_start = 0
    try:
        # Appended, never truncated: two starts racing for one host:port share the path,
        # and the loser's truncation wiped the serving daemon's log. The failure tail reads
        # from where this start began.
        err_fh: Any = _open_append(err_path)   # closed below, after the spawn
        err_start = os.fstat(err_fh.fileno()).st_size
    except OSError as exc:
        err(f"warning: cannot write the daemon log {err_path}: {exc}")
        err_fh, err_path = subprocess.DEVNULL, None
    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": err_fh,
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
    try:
        proc = subprocess.Popen(args, **kwargs)
    finally:
        if err_path is not None:
            err_fh.close()      # the child holds its own handle
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
    refusal: tuple[int, str] | None = None
    announced = restarted = False
    ceiling = build_until = 0.0
    while True:
        if time.monotonic() >= deadline:
            # An older capture builds its missing indexes before the daemon answers, which
            # can outlast any --timeout; stopping the daemon then only restarts the build
            # next time. Wait it out, then give the daemon a fresh --timeout, so one that
            # wedges after the build still fails in bounded time.
            # A child that died meanwhile is caught by the poll below the probe.
            names, built = _index_build(err_path, err_start)
            if names is None or (built and restarted):
                break
            if built:
                restarted = True
                deadline = time.monotonic() + max(wait_s, 0.0)
            elif not announced:
                announced = True
                ceiling = cli_daemonctl.INDEX_BUILD_CEILING_S
                build_until = time.monotonic() + ceiling
                err(f"mcuscoped is building index {names} on an older capture (one time); "
                    f"waiting. Ctrl-C leaves it building (pid {proc.pid})")
            elif time.monotonic() >= build_until:
                # Not stopped: that would only restart the build on the next start.
                die(f"mcuscoped is still building index {names} after {ceiling:g}s; "
                    f"left running (pid {proc.pid})", 1)
        # A guard refusal here is not the pre-spawn one: the daemon this command just
        # started is up and this CLI holds no token for it, which is a success it cannot
        # report as a failure without leaving a running daemon behind an exit 1 (SPEC 4).
        body, refusal = _status_or_refusal(s, timeout=0.5)
        if body is not None or refusal is not None:
            break
        if proc.poll() is not None:      # it died; no point waiting out the deadline
            break
        time.sleep(0.1)
    if body is None and refusal is None:
        _abandon_daemon(proc, pid_path, s, wait_s, err_path, err_start)
    # "Something mcuscoped answers here" is not "the daemon I spawned is up". Two starts
    # racing for one host:port leave the loser's child dead on the port conflict while the
    # winner answers, and the loser then reported success with a dead pid. A URL answering
    # for a different process is a failure of *this* start: nothing is written, nothing is
    # removed, and the pid named is the one that actually holds the port.
    # A refusal carries no `pid`, so that check is the body's alone.
    if body is not None:
        # On Windows proc.pid is the venv launcher shim, reported as the daemon's `ppid`.
        serving = _serving_pids(body)
        if serving and proc.pid not in serving:
            die(f"another daemon is already serving at {s.url} "
                f"(pid {body.get('pid')})", 1)
    if proc.poll() is not None:
        die(f"mcuscoped exited with status {proc.poll()} although {s.url} answers; "
            "something else is serving that port", 1)
    if refusal is not None:
        err(f"note: the daemon requires a token (HTTP {refusal[0]}: {refusal[1]}); pass "
            "--token or set MCUSCOPE_TOKEN for later commands")
    ui_url = _ui_url(s)
    # The serving process is the one to act on; under a Windows venv launcher the spawned
    # pid is the launcher, which a `taskkill` would hit instead.
    pid = (_status_pid(body, "pid") if body is not None else None) or proc.pid
    if s.json_out:
        res: dict[str, Any] = {"ok": True, "pid": pid, "ui_url": ui_url}
        if pid != proc.pid:
            res["launcher_pid"] = proc.pid
        out_json(res)
    else:
        which = f"pid {pid}" + (f"; launcher {proc.pid}" if pid != proc.pid else "")
        print(f"started mcuscoped ({which}); web UI: {ui_url}")
    if open_ui:
        import webbrowser

        webbrowser.open(ui_url)


@daemon_app.command("restart")
def daemon_restart(
    ctx: typer.Context,
    config: str | None = CONFIG_OPTION,
    sim: bool = SIM_OPTION,
    wait_s: float = START_TIMEOUT_OPTION,
    open_ui: bool = OPEN_OPTION,
) -> None:
    """Stop the daemon if it is running, then start it again: its config file and sim port
    are kept unless the options here override them."""
    s = settings_of(ctx)
    if open_ui and s.json_out:
        die("--open cannot be combined with --json", 1)   # before anything is stopped
    from .config import default_config_path

    body = _status_body(s, timeout=1.0)
    # Same daemon again: its config file and sim port are carried unless overridden, or a
    # restart after `start -c x --sim` came back on the default capture with no sim port.
    # Not the default path, which a daemon started without one reports: forwarding it would
    # refuse a restart that should come back on defaults.
    carried = body.get("config_path") if body is not None else None
    config = _named_config(config)   # refused before anything is stopped
    if config is None and carried and carried != str(default_config_path()):
        # The daemon reports the path absolute (resolved at its startup), so the check
        # here is on the file it runs on, from any directory. Only a 0.4.0 daemon reports
        # the relative path it was given, resolved here against this CLI's cwd.
        config = _named_config(carried)
    if body is None:
        err(f"no daemon running at {s.url}; starting one")
    else:
        if not sim:
            ports = Client(s).probe("GET", "/ports") or {}
            sim = any(str(pt.get("device", "")).startswith("sim://")
                      for pt in ports.get("ports", []) if isinstance(pt, dict))
        _stop_daemon(s, restarting=True)
    _start_daemon(ctx, config, sim, wait_s, open_ui)


@daemon_app.command("stop")
def daemon_stop(ctx: typer.Context) -> None:
    """Stop the local mcuscoped daemon, however it was started."""
    _stop_daemon(settings_of(ctx))


def _stop_daemon(s: Settings, restarting: bool = False) -> None:
    pid_path = _pid_file(s)
    if not os.path.exists(pid_path):
        # No record - a daemon started some other way, or one whose data dir was
        # unwritable when it tried to claim one. It still answers POST /shutdown,
        # so ask /status who is serving instead of refusing to stop a live daemon.
        body = _status_body(s)
        if body is None:
            die(f"no daemon is running at {s.url}; nothing to stop", 1)
        _stop_running_daemon(s, body, restarting=restarting)
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
            # there: a daemon still starting up. Removing the record of a live daemon is
            # how one becomes unstoppable, so keep it and report what was found.
            die(f"no usable /status from {s.url}, but pid {pid} is still running; "
                f"left its record {pid_path} in place", 1)
        with contextlib.suppress(OSError):
            os.remove(pid_path)
        die(f"no daemon responding at {s.url}; removed stale pid file (was pid {pid})", 1)
    _stop_running_daemon(s, body, pid_path, pid, restarting=restarting)


@daemon_app.command("status")
def daemon_status(ctx: typer.Context) -> None:
    """Report whether the daemon is reachable."""
    s = settings_of(ctx)
    body = _status_body(s)   # anything that is not mcuscoped counts as not running (exit 3)
    if body is None:
        if s.json_out:
            out_json({"running": False})
        else:
            print("not running" + start_hint(s.url))
        raise typer.Exit(3)
    if s.json_out:
        out_json({"running": True, "version": body["version"], "uptime_s": body["uptime_s"]})
    else:
        print(f"running: mcuscoped {body['version']} up {body['uptime_s']:.0f}s")


config_app = typer.Typer(help="The daemon's config file.")
app.add_typer(config_app, name="config")


@config_app.command("path")
def config_path(ctx: typer.Context) -> None:
    """Print where mcuscoped reads config.toml from by default."""
    from .config import default_config_path

    path = str(default_config_path())
    if settings_of(ctx).json_out:
        out_json({"path": path})
    else:
        print(path)


# -- ai-guide -------------------------------------------------------------------------

AI_GUIDE = """\
mcu: hardware debug bridge CLI (talks to the mcuscoped daemon over 127.0.0.1)

WHAT IT IS
  mcuscoped owns the serial link to an MCU running the "monitor" firmware and logs
  every line to SQLite. `mcu` is a thin client. Prefer --json for machine parsing.
  Who is on the other end: mcu cmd ping (OK monitor 1 <board name>), mcu cmd info
  (uptime, can=N buses, firmware tokens). gpio/adc/spi names (led, vbat, imu below) are
  the firmware's own; they are examples, not a list.

EXIT CODES (contract)
  0 success / match    1 error (bus ERR, HTTP error, bad usage, a daemon that stopped answering)
  2 timeout the board or the wait reported     3 daemon unreachable
  A closed or full stdout/stderr never changes the code; unwritable output (a full disk, a
  stdout closed at start) turns 0 into 1.

PITFALLS (read these first)
  - Writes need -p when more than one port is attached (cmd, send, break, sysrq, can tx,
    wait/assert --send): refused, exit 1, listing the aliases. Reads without -p (lines,
    tail, wait, log export, can dump) span EVERY port; their text rows then carry [port]
    when more than one board is attached or has stored rows (not can dump's; tail -f
    also from the first row of a board attached during it). A detached board's history
    stays readable with -p.
  - An unknown -p is refused (exit 1, "no such port"), on reads too; an empty -p is
    refused by every command (exit 1).
  - `send` writes a raw line with no seq; the monitor ignores it. Use `cmd` (or
    wait/assert --send) for monitor commands; `send` is for other consoles and bootloaders.
  - `wait` sees only lines that arrive after it starts: `mcu mark X; mcu wait --match X`
    times out. To know a port is up, read `mcu ports --json` (ports[].connected) first.
  - wait/assert judge only lines the board sent: their own --send, earlier tx rows, markers
    and sys notices are neither matched nor counted unless --chan names their channel
    (--chan cmd, marker or sys). A silent board plus a `mcu mark` is still "empty".
  - A --send the monitor refuses (ERR) is exit 1 on `wait` (the ERR on stderr) and a
    FAILED verdict on `assert`; a --send with no response fails the assert too. Its
    --forbid lines then print "not judged": nothing was.
  - A verdict over a window that held no lines is "empty", exit 1 (a --forbid over nothing
    proves nothing); --allow-empty accepts it. A retrospective assert with no --session or
    --last-ms judges the whole capture.
  - `lines` returns the newest 100 by default and --limit counts raw rows before
    --changes/--names filter. For a whole run use `log export` (every row by default).
  - mark and send take a text starting with '-' as it is (mcu mark "-pwm duty 50");
    elsewhere put `--` before a positional argument that starts with '-'.
  - Prompts (purge, session delete --data) are refused unless stdin is a terminal: pass -y.
  - Numbers are ASCII decimal: other scripts' digits, `_` and `+` are refused (exit 1).
  - Text output shows line boundaries inside a line (\\x0b, \\u2028) escaped everywhere,
    so one row stays one line; on a terminal other control bytes too (\\x1b, \\x07),
    colour (SGR) kept. --json (and --csv) carry the bytes as captured.

GLOBAL OPTIONS (any position; before `--`)
  --json            one JSON object per command; tail, log export and can dump print JSONL
                    (one object per row, -f or not), a fatal error as the last line
  -p, --port ALIAS  choose a port (see PITFALLS)
  --url URL         daemon base URL (or env MCUSCOPE_URL); default http://127.0.0.1:8558
  --token TOKEN     access token for a remote daemon (or env MCUSCOPE_TOKEN)
  --version         client version and interpreter (honours --json)
  A daemon older than this mcu is refused (exit 1, naming both versions): upgrade it with
  mcu daemon restart. A dev build must match exactly. The `mcu daemon` commands work
  against any version.

HEALTH
  mcu status                      daemon + port health; each port shows its state
                                  (connected / disconnected (REASON) / DEGRADED: N write
                                  failures since HH:MM:SS: RX flows, writes do not) and
                                  target=<name> from the monitor's ping at connect
  disconnect_reason (--json, and in brackets above):
    connecting    no open attempt has resolved yet; wait one retry interval
    no_device     board powered off or unplugged: fix power/cable
    open_failed   present but will not open (another process holds it, permissions), or
                  for socket:// the far end is down; retried every few seconds on its own
                  (POST /ports/<alias>/reconnect only skips the wait)
    read_error    the link dropped mid-session; the daemon is retrying on its own
    manual        closed by POST /ports/<alias>/disconnect; resume with .../reconnect
  Waiting for a (re)connect: check `mcu ports --json` first; only if still disconnected,
    mcu wait --chan sys --match "port board connected" --timeout 60000
  mcu ports                       list attached ports (says so when there are none)
  mcu devices                     host serial devices: device, description, vid:pid, serial
  mcu attach socket://127.0.0.1:9900 --alias board [--baud N] [--eol none|lf|crlf]
                                  --alias names it (default: the device's basename, or
                                  "board" for a URL; an existing alias is retargeted, with
                                  a note); --baud default 115200; --eol what the port
                                  appends to every line it sends (default lf)
  mcu attach --serial 0672FF3 --alias board
                                  by USB serial number (4th column of `mcu devices`),
                                  re-resolved on every open; a device or --serial, not both
  mcu detach board                (an alias never contains '/')

THE CORE LOOP (send, wait, query)
  mcu cmd "i2c rd 48 2"           send a monitor command, print its data ("ok" when it has
                                  none); ERR -> stderr, exit 1; no response -> exit 2
  mcu cmd ... --timeout 500 --retry-ms 500   --timeout: response wait in ms; --retry-ms
                                  keeps retrying `ERR 6 busy` for that long
  mcu send "boot 0"               write one raw line, no seq, no response wait (see PITFALLS)
  mcu wait --match "^!can" --timeout 2000        block until a line matches; exit 2 on
                                  timeout (the message names the pattern, how long it
                                  waited and, after --send, how many sends went out);
                                  exit 3 if the daemon stops during the wait; a daemon at
                                  its subscriber cap ("too many subscribers") is exit 1:
                                  it is running, so retry rather than restart it. A daemon
                                  that accepts the wait but never answers is exit 1, not 2
  mcu wait --send "can tx 300 AABB" --match "301 AABB"   send then wait for the reply
  --raw                           with wait/assert --send: write the line verbatim instead
                                  of as a monitor command (no seq, no response matching)
  mcu wait --send "" --repeat-ms 50 --match "=>" --timeout 30000
      resend the line every 50 ms until it matches, to catch a bootloader's autoboot window
      start it BEFORE powering the target: writes to a disconnected port are counted, not fatal
  mcu lines --last-ms 5000 --chan event --match "1A3"    query the capture (the workhorse)
  mcu tail -f --chan debug        follow live output; exit 3 if the daemon stops under it,
                                  exit 1 at the subscriber cap ("too many subscribers")
  mcu mark "starting test"        drop an annotation into the log
  --eol none|lf|crlf              line ending for one send (cmd/send/wait/assert); the
                                  port's own setting applies when omitted. `--eol none`
                                  appends nothing, which is how a bare control character
                                  is sent: mcu send --eol none $'\\x03'   (Ctrl-C, bash)
                                  PowerShell: mcu send --eol none ([char]3)
                                  To a monitor, follow it with `mcu send ""` (a bare line
                                  end), or the next `cmd` starts with that byte and times out
  mcu break --ms 250              serial break (line held low), 1..2000 ms
  mcu sysrq b                     break, then one ASCII character with no terminator: Linux
                                  magic SysRq (b reboot, t tasks, w blocked tasks). Needs
                                  the target's kernel sysrq enabled and its console on
                                  this UART; one character only.
  `cmd` and `--send` take the monitor's own grammar, not the `mcu` sugar: a CAN frame is
  `can tx ID DATA [x][r]` (x = extended id, r = RTR), on bus 2 `can2 tx ...`. `--ext` is
  sugar only: `mcu can tx C0103 B400 --ext` sends `can tx C0103 B400 x`.

READING THE CAPTURE (lines, tail and log export)
  Windows: --last-ms N (0 to 10^15, back from the daemon's clock), --session NAME, and
    wall-clock bounds
    --from HH:MM[:SS[.mmm]] --to HH:MM[:SS[.mmm]]   today, local time; give the date for
    another day (2026-09-01T19:53:35); --from after --to is refused. Bounds intersect.
  Size: `lines` gives the newest --limit (default 100; --limit 0 returns no rows, only
    `truncated`); `log export` gives EVERY row (--limit N = the newest N). tail takes -n.
    A stderr note says when more rows exist.
  Polling: mcu lines --since-id N --limit 500   the next 500 rows ABOVE id N; when the note
    (or --json "truncated") says more exist, call again with the newest id returned
  Order: text is oldest first; `lines --json` is newest first (--order asc|desc overrides
    either); the JSONL of tail, log export and can dump is oldest first
  Filters: --chan debug|event|cmd|resp|sys|marker, --match REGEX (matches the raw line)
  mcu log export --csv -o run.csv   the window as CSV (id,ts,port,dir,chan,seq,raw);
    --csv refuses --json, --limit and --decode. -o FILE writes the file and prints a count
  Decoding plot samples (the readable timeline for a test run):
    --decode        render !ps/!p samples as named fields from the firmware's !pd definition:
                    "s0 state=CHARGING vbat=25.54V io=robot|relay|bat" (enum labels, bit-lane
                    names, units resolved; the !pd rows themselves are hidden)
    --changes       print a stream's sample only when a rendered field changed (implies --decode)
    --names a,b     render only these fields or lanes (implies --decode)
  mcu log export --session run-3 --decode --changes      the whole run as state transitions
  mcu lines --from 19:53:35 --to 19:54:00 --decode --names state,vbat
  mcu tail -f --decode --changes                         live, only when something changes
  mcu lines --match "^!e"         firmware error notices: "!e plot 3 badarg def" means the
                                  monitor rejected plot stream 3; the stream never appears.
                                  "!e event p overflow": !p lines over 255 bytes are being
                                  cut at a space (trailing pairs lost). Later cut lines
                                  carry no notice; "!e event p overflow cut=<n>" ends the
                                  run with its count, at a !p that fits, a cut of another
                                  type, or 1 s with no cut. A cut keeping nothing past the
                                  type or a marker's @<tick> is not sent at all; a !p whose
                                  first pair does not fit arrives as "!p <tick>".
                                  "!e can bus <n> dropped": a frame for a bus above the
                                  build's MON_CAN_BUSES was dropped (sent once per init)
  Every --json row carries the decoded text in "decoded" (and in "raw") when decoding.

VERDICTS (one pass/fail answer instead of a log to read)
  `wait` asks "did this line appear?"; `assert` asks "did this run pass?".
  Exit 0 = pass, 1 = fail (or empty). Several conditions at once, negative ones included.
  mcu assert --session boot-test --expect "CALIB DONE" --forbid "ERR|retry" --json
                                  judge a stored run after the fact
  mcu assert --send "selftest" --expect "SELFTEST OK" --forbid "PANIC" --timeout 5000
                                  live: send, then judge the window that follows
  mcu assert --last-ms 10000 --forbid "ERR"     judge the last 10 s
  mcu assert --expect "BOOT OK" --forbid "ERR" --min-window 10000 --timeout 20000
                                  boot within 20 s AND stay clean for at least 10 s
  Live windows close as soon as every --expect is met; --min-window holds one open so
  --forbid covers it. With no --expect the whole window is used. --chan, --raw, --eol and
  --allow-empty work as in PITFALLS and wait.

SESSIONS (name a run, then query just that run)
  The daemon records one session per run of its own ("auto-<timestamp>"); naming one
  carves your run out of that, and it survives a daemon restart.
  mcu session start boot-test [--note "..."]     everything captured from now belongs to it
  mcu session stop                close it (starting another also closes the current one)
  mcu session list [--limit N]    recent runs with their line counts ("auto" vs "named")
  --session boot-test             scopes lines, log export, plot export, can dump, assert
  mcu session export boot-test -o run.db         archive it as a standalone capture DB
  mcu session export boot-test --bundle -o run.zip   the .db plus decoded lines, plot and
                                  CAN CSVs and a manifest
  mcu session delete boot-test --data -y         drop the label, and its lines with --data

DELETING CAPTURE (not recoverable; the count is always shown first)
  mcu purge (--session S | --before-days N | --id-from A --id-to B | --all) [--dry-run] [-y]
      --dry-run only reports the count; -y (--yes) skips the prompt

PLOTS (numeric channels the firmware emits as `!p <tick> name=value`)
  mcu plot channels [--active 60] channels with unit, last value, age, count (-p: one board;
                                  --active S: only those seen in the last S seconds)
  mcu plot export --last-ms 10000 --names vbat,temp -o run.csv [--wide]
      -p PORT scopes it to one board; --wide gives one row per sample tick; --from/--to,
      --session bound it; --decode (enum labels, bit lanes as <channel>.<lane> columns),
      --changes (a row only when a value moved), --deadband vbat=0.05 (a smaller move is none)
  mcu plotjuggler on [host:port] [--save]   mirror points to PlotJuggler's UDP Server; off
                                  stops, no args shows state; alias pj

BUS SUGAR (all wrap `cmd`)
  mcu can tx 1A3 DEADBEEF [--ext] [--rtr 4] [--bus 2] [--retry-ms 500]
  mcu can dump --id 100 -f        decoded CAN frames, live; --bus N one controller; -n N
                                  newest frames (-n 0 -f: follow only); polls failing for
                                  30 s end it: exit 3 unreachable, 1 if it kept erroring
  mcu can dump -i 100 -i 200 --from 19:53 --to 19:54 --csv -o frames.csv
                                  -i/--id repeatable; --csv streams every matching frame
                                  (no -n, no follow); --last-ms, --session bound it too;
                                  --to is refused with -f (--follow)
  mcu can stat / mcu can filter all           both take --bus N
  mcu i2c scan / mcu i2c rd 48 2 --reg 00 (wrrd) / mcu i2c wr 50 0011AA
  mcu spi xfer imu 00FF / mcu gpio set led 1 / mcu gpio get led / mcu adc read vbat

TYPICAL AGENT PATTERN
  1. mcu ports --json                         (which boards, and are they connected?)
  2. mcu -p board cmd "..." --json            (act; check "status": ok|err|timeout)
  3. mcu -p board wait --send "..." --match "..." --json   (send-and-wait for the effect)
  4. mcu -p board lines --last-ms N --json    (inspect what happened)
  5. mcu -p board assert --last-ms N --expect ... --forbid ...   (pass/fail on an exit code)

TIMING-CRITICAL WORK (anything faster than about 1 Hz)
  Every `mcu` call is a new process (about 200 ms). The daemon's REST API is the same
  thing without the start-up; two primitives:
    POST http://127.0.0.1:8558/send   {"port": "board", "line": "...", "eol": "none|lf|crlf"}
    GET  http://127.0.0.1:8558/lines?since_id=N&order=asc&limit=1000
         rows with id > N, oldest first; set N to the last id returned and repeat at once
         while "truncated" is true, so no row is skipped
  Check `mcu wait --repeat-ms` first: it runs a send-until-match loop inside the daemon.

DAEMON CONTROL
  mcu daemon status                  exit 0 running, 3 when nothing answers
  mcu daemon start [--sim] [-c/--config PATH] [-t/--timeout S] [--open]
                                     prints the serving pid ("pid N; launcher M" under a
                                     Windows venv launcher: act on N);
                                     exit 1 "daemon already running" if one answers, so
                                     check status first; --sim: in-process simulator;
                                     --config: a missing file is refused, exit 1,
                                     "no such config file: <path>"; --timeout: readiness wait (env
                                     MCUSCOPE_START_TIMEOUT), a daemon that never answers
                                     is stopped and its stderr tail shown; one building an
                                     older capture's indexes (a one-time stderr note) is
                                     waited for, then given a fresh --timeout; past 600 s
                                     of building, exit 1 "still building index", left
                                     running; --open: browser
  mcu daemon stop                    POST /shutdown; a pid is signalled only when a local
                                     pid record names the one /status reports, never for
                                     a remote daemon
  mcu daemon restart [start options] stop if running, then start on the same config and
                                     sim port unless overridden
  A daemon refusing with 401/403/429 is running: exit 1 naming it, no spawn. One that
  `start` spawned and that then refuses is started:
  exit 0 with a note that it wants a token
  mcu config path                    where the default config.toml lives
  env MCUSCOPE_DATA_DIR | MCUSCOPE_CONFIG_DIR | MCUSCOPE_CACHE_DIR name those directories
  mcu --install-completion / --show-completion   shell completion (only right after `mcu`)
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


def _split_global_opts(argv: list[str]) -> tuple[list[str], list[str]]:
    """cli_argv.split_global_opts bound to this module's `app`."""
    return cli_argv.split_global_opts(app, argv)


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
    guard_stdout()
    code = _dispatch(argv)
    # A command whose stdout could not be written has not done what it reported.
    if code == 0 and output_failed():
        code = 1
    # The interpreter flushes stdout during shutdown, and a closed pipe there prints
    # "Exception ignored ... BrokenPipeError" and exits 120 over whatever we returned.
    # Flushing here, where it can be handled, keeps the exit-code contract intact.
    # The command's code stands: a failing `mcu assert | head -1` stays 1.
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        _silence_stdout()
    except OSError as exc:
        _stdout_unwritable(exc)      # a no-op when the guard already reported it
        return code or 1
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


def _mapped_exit(code: int) -> int:
    """Map a command's own exit code onto SPEC 4's 0/1/2/3."""
    # typer turns a Ctrl-C raised inside a command into Exit(130); SPEC 4 maps an
    # interrupt to 1. A -f follow catches its own and has already exited 0.
    if code == 130:
        return _dispatch_error("interrupted", 1)
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
    reset_output_state()
    if _stdio.stdout_was_closed():
        # The devnull repair would otherwise report every command a success (SPEC 4).
        _stdout_unwritable(OSError(errno.EBADF, "stdout was closed when mcu started"))
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
        # click returns a command's Exit code here rather than raising it, so both this
        # and the arm below go through _mapped_exit.
        return _mapped_exit(rv if isinstance(rv, int) else 0)
    except EXIT_EXCEPTIONS as exc:
        return _mapped_exit(int(getattr(exc, "exit_code", 0) or 0))
    except USAGE_ERRORS as exc:
        try:
            exc.show()
        except OSError:
            _silence_stderr()        # a closed or full stderr must not own the exit code (120)
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
            if output_failed():
                return 1             # our own stdout refused a write; the guard reported it
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
    return _stdio.console_entry(main, "mcu", reports_closed_stdout=True)


if __name__ == "__main__":
    raise SystemExit(console_entry())
