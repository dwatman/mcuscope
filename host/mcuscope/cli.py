"""The `mcu` command-line client: a thin HTTP client of mcuscoped (SPEC 4).

Exit-code contract (for AI use): 0 success/match, 1 error (bus ERR, HTTP error, bad
usage), 2 timeout, 3 daemon unreachable. With --json, each command prints exactly one
JSON object (streaming commands print one object per line).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import click
import httpx
import typer

from . import __version__, _stdio

# `asyncio`, `websockets` and `platformdirs` are imported where they are used (the follow
# loop and the pid-file helper), not here. They cost about 60 ms of the CLI's ~190 ms
# startup, and every command that is not `tail -f` or `daemon start|stop` pays it for
# nothing - which matters when an agent runs `mcu` dozens of times in a session.

DEFAULT_URL = "http://127.0.0.1:8765"


@dataclass
class Settings:
    url: str
    json_out: bool
    port: str | None
    token: str | None = None

    def headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}


# -- small output / error helpers -----------------------------------------------------


def err(msg: str) -> None:
    print(msg, file=sys.stderr)


# Set once by the global callback. `die()` is called from helpers that have no Settings
# in hand (Client.request, the stream helpers), so the mode is kept here rather than
# threaded through every signature.
_JSON_MODE = False


def set_json_mode(on: bool) -> None:
    global _JSON_MODE
    _JSON_MODE = on


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


def _silence_stdout() -> None:
    """Point stdout at devnull so interpreter shutdown cannot re-raise a broken pipe."""
    with contextlib.suppress(Exception):
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())


def out_json(obj: Any) -> None:
    print(json.dumps(obj))


def emit_stream(text: str) -> None:
    """Print one line of a follow stream, flushed.

    A follow loop writes to a pipe as often as to a terminal (`mcu tail -f --json | jq`,
    or an agent reading the stream), and Python block-buffers a pipe at 8 KB - which makes
    a live follow look like it has hung until enough output piles up.
    """
    print(text, flush=True)


def fmt_ts(ts: float) -> str:
    """Time of day with milliseconds, for per-line output where the date is noise."""
    return time.strftime("%H:%M:%S", time.localtime(ts)) + f".{int(ts * 1000) % 1000:03d}"


def fmt_datetime(ts: float) -> str:
    """Date and time, for listings that span days (sessions, notably).

    A session listing showed only the time, so yesterday's `boot-test` and today's were
    indistinguishable; anything that can list rows from different days uses this.
    """
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def fmt_line(row: dict[str, Any]) -> str:
    return f"{fmt_ts(row['ts'])} {row['chan']:>6}| {row['raw']}"


def fmt_frame(fr: dict[str, Any]) -> str:
    flags = ("x" if fr["ext"] else "") + ("r" if fr["rtr"] else "") or "-"
    return (
        f"{fmt_ts(fr['ts'])}  id={fr['can_id']:X} {flags} "
        f"dlc={fr['dlc']} data={fr['data_hex'] or '-'}"
    )


# -- HTTP client wrapper --------------------------------------------------------------


class Client:
    def __init__(self, s: Settings) -> None:
        self.s = s

    def request(
        self, method: str, path: str, timeout: float = 30.0,
        timeout_code: int = 2, **kw: Any,
    ) -> httpx.Response:
        """Issue a request, mapping transport failures onto the SPEC 4 exit codes.

        `timeout_code` exists for `mcu assert`, which SPEC 4 says never exits 2: a transport
        timeout there has to surface as an error, not as the timeout code.
        """
        try:
            return httpx.request(
                method, self.s.url + path, timeout=timeout, headers=self.s.headers(), **kw
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            die(f"daemon unreachable at {self.s.url}: {exc}", 3)
        except httpx.TimeoutException as exc:
            die(f"request timed out: {exc}", timeout_code)
        except httpx.InvalidURL as exc:
            # Not an httpx.HTTPError subclass, so this used to escape as a raw traceback
            # while every neighbouring bad-URL form was handled.
            die(f"bad daemon url {self.s.url!r}: {exc}", 3)
        except httpx.HTTPError as exc:
            die(f"daemon unreachable at {self.s.url}: {exc}", 3)
        raise AssertionError("unreachable")  # for type-checkers; die() always raises

    def json_or_die(self, resp: httpx.Response) -> Any:
        if resp.status_code >= 400:
            try:
                msg = resp.json().get("error", resp.text)
            except (json.JSONDecodeError, ValueError):
                msg = resp.text
            die(f"error: {msg}", 1)
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            # A proxy, a captive portal, or the wrong port answering 200 with non-JSON.
            # Report it as an error with an exit code, not as a JSONDecodeError traceback.
            die(f"malformed response from {self.s.url}: {exc}", 1)

    def get(self, path: str, **kw: Any) -> Any:
        return self.json_or_die(self.request("GET", path, **kw))

    def post(self, path: str, body: dict[str, Any], **kw: Any) -> Any:
        return self.json_or_die(self.request("POST", path, json=body, **kw))

    def delete(self, path: str, **kw: Any) -> Any:
        return self.json_or_die(self.request("DELETE", path, **kw))

    def download(self, path: str, out_file: str, timeout: float = 300.0, **kw: Any) -> int:
        """Stream a binary response to a file. Returns bytes written.

        Streamed rather than buffered because the thing being downloaded is a database:
        a long run's export can be larger than it is polite to hold in memory twice.
        """
        try:
            with httpx.stream(
                "GET", self.s.url + path, timeout=timeout, headers=self.s.headers(), **kw
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    try:
                        msg = resp.json().get("error", resp.text)
                    except (json.JSONDecodeError, ValueError):
                        msg = resp.text
                    die(f"error: {msg}", 1)
                written = 0
                with open(out_file, "wb") as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
                        written += len(chunk)
                return written
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            die(f"daemon unreachable at {self.s.url}: {exc}", 3)
        except httpx.TimeoutException as exc:
            die(f"request timed out: {exc}", 2)
        except OSError as exc:
            die(f"cannot write {out_file}: {exc}", 1)
        raise AssertionError("unreachable")  # for type-checkers; die() always raises

    def stream_text(
        self, path: str, sink: Callable[[str], None], what: str = "output",
        timeout: float = 300.0, **kw: Any,
    ) -> None:
        """Stream a text response through `sink`, chunk by chunk.

        `/plot/export` is the one endpoint that can answer with a very large body (a long
        run's channel history), so it is consumed incrementally like a session export
        rather than materialised whole with `resp.text`. `what` names the destination in
        the write-error message.
        """
        try:
            with httpx.stream(
                "GET", self.s.url + path, timeout=timeout, headers=self.s.headers(), **kw
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    try:
                        msg = resp.json().get("error", resp.text)
                    except (json.JSONDecodeError, ValueError):
                        msg = resp.text
                    die(f"error: {msg}", 1)
                for chunk in resp.iter_text():
                    sink(chunk)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            die(f"daemon unreachable at {self.s.url}: {exc}", 3)
        except httpx.TimeoutException as exc:
            die(f"request timed out: {exc}", 2)
        except httpx.InvalidURL as exc:
            die(f"bad daemon url {self.s.url!r}: {exc}", 3)
        except httpx.HTTPError as exc:
            die(f"daemon unreachable at {self.s.url}: {exc}", 3)
        except BrokenPipeError:
            raise                        # handled in main(): the reader closed the pipe
        except OSError as exc:
            die(f"cannot write {what}: {exc}", 1)


# Typer vendors its own copy of click (`typer._click`), so a control-flow exception raised
# from inside a typer command is NOT the class of the same name in the installed `click`.
# Catching only one of the two lets the other escape to typer's rich exception hook, which
# answers "n" at a confirmation prompt with a traceback. Both are always caught together.
ABORT_EXCEPTIONS = tuple({click.exceptions.Abort, typer.Abort})
EXIT_EXCEPTIONS = tuple({click.exceptions.Exit, typer.Exit})
USAGE_ERRORS = tuple({click.exceptions.UsageError, typer._click.exceptions.UsageError})


def confirm_or_exit(question: str) -> None:
    """Ask before a destructive action; exit 1 if the answer is no.

    `typer.confirm(abort=True)` raises through typer's rich exception hook, which prints a
    traceback at someone who simply answered "n". Declining is a normal outcome, so it gets
    a plain message - and a non-zero exit, so a script never reads "cancelled" as "done".
    A closed stdin (no tty, no --yes) counts as no.

    The prompt is written to stderr and stdin is read directly, rather than going through
    `typer.confirm`: the prompt is a message to a human, and on stdout it is a prose
    fragment in the middle of a --json consumer's parse. (`typer.confirm(err=True)` is not
    enough - click still writes a space to stdout there, to work around a readline bug.)
    """
    sys.stderr.write(f"{question} [y/N]: ")
    sys.stderr.flush()
    try:
        answer = sys.stdin.readline()
    except (*ABORT_EXCEPTIONS, EOFError, KeyboardInterrupt, OSError, ValueError):
        answer = ""
    if not answer.endswith("\n"):
        sys.stderr.write("\n")           # EOF or ^C left the cursor mid-line
    if answer.strip().lower() not in {"y", "yes"}:
        die("cancelled", 1)


def settings_of(ctx: typer.Context) -> Settings:
    return ctx.obj


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


# -- app + global options -------------------------------------------------------------

app = typer.Typer(
    add_completion=False, no_args_is_help=True, help="mcu: hardware debug bridge CLI."
)


def _version_callback(value: bool) -> None:
    if value:
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
    print(f"mcuscoped {body['version']}  up {body['uptime_s']:.0f}s  db {body['db_path']}")
    sess = body.get("session")
    if sess:
        print(f"  session: {sess['name']} (id {sess['id']}, running)")
    for pt in body["ports"]:
        state = "connected" if pt["connected"] else "disconnected"
        # Only mention drops when there are some; a clean capture should stay quiet.
        dropped = f" dropped={pt['rx_dropped']}" if pt.get("rx_dropped") else ""
        print(
            f"  {pt['alias']:<10} {pt['device']}  @{pt['baud']}  {state}  "
            f"rx={pt['lines_rx']} tx={pt['lines_tx']}{dropped}"
        )


@app.command()
def ports(ctx: typer.Context) -> None:
    """List attached ports."""
    s = settings_of(ctx)
    body = Client(s).get("/ports")
    if s.json_out:
        out_json(body)
        return
    for pt in body["ports"]:
        state = "connected" if pt["connected"] else "disconnected"
        print(f"{pt['alias']:<10} {pt['device']}  @{pt['baud']}  {state}")


@app.command()
def devices(ctx: typer.Context) -> None:
    """List host serial devices (find /dev/ttyACM0, COMx before `mcu attach`)."""
    s = settings_of(ctx)
    body = Client(s).get("/devices")
    if s.json_out:
        out_json(body)
        return
    devs = body["devices"]
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


@app.command()
def attach(
    ctx: typer.Context,
    device: str = typer.Argument(..., help="Device: /dev/ttyACM0, COM7, socket://host:port"),
    baud: int = typer.Option(115200, "--baud"),
    alias: str | None = typer.Option(None, "--alias"),
) -> None:
    """Attach a serial port."""
    s = settings_of(ctx)
    body = {"alias": alias or _derive_alias(device), "device": device, "baud": baud}
    res = Client(s).post("/ports", body)
    if s.json_out:
        out_json(res)
    else:
        print(f"attached {res['port']['alias']} -> {device}")


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


@app.command()
def cmd(
    ctx: typer.Context,
    text: str = typer.Argument(..., help='Command without ">" and seq, e.g. "i2c rd 48 2"'),
    timeout: int = typer.Option(1000, "--timeout", help="Response timeout in ms."),
) -> None:
    """Send a monitor command and print its response."""
    s = settings_of(ctx)
    res = Client(s).post(
        "/cmd", {"port": s.port, "cmd": text, "timeout_ms": timeout}, timeout=timeout / 1000 + 5
    )
    emit_cmd_result(s, res)


@app.command()
def send(ctx: typer.Context, text: str = typer.Argument(...)) -> None:
    """Write one raw line (no response wait)."""
    s = settings_of(ctx)
    res = Client(s).post("/send", {"port": s.port, "line": text})
    if s.json_out:
        out_json(res)
    else:
        print("ok")


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


def _lines_params(
    s: Settings, chan: str | None, match: str | None, last_ms: int | None,
    limit: int, since_id: int | None, session: str | None = None,
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
    return params


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


@app.command()
def lines(
    ctx: typer.Context,
    last_ms: int | None = typer.Option(None, "--last-ms"),
    chan: str | None = typer.Option(None, "--chan"),
    match: str | None = typer.Option(None, "--match"),
    limit: int = typer.Option(100, "--limit"),
    since_id: int | None = typer.Option(None, "--since-id"),
    session: str | None = typer.Option(None, "--session", help="Scope to a session name/id."),
) -> None:
    """Query the capture (the AI workhorse)."""
    s = settings_of(ctx)
    params = _lines_params(s, chan, match, last_ms, limit, since_id, session)
    body = Client(s).get("/lines", params=params)
    if s.json_out:
        out_json(body)   # the "truncated" flag is already in the body
        return
    for row in reversed(body["lines"]):  # oldest first for reading
        print(fmt_line(row))
    note_truncated(body, limit)


@app.command()
def tail(
    ctx: typer.Context,
    n: int = typer.Option(20, "-n", help="Number of recent lines to show first."),
    follow: bool = typer.Option(False, "-f", "--follow", help="Follow live via WebSocket."),
    chan: str | None = typer.Option(None, "--chan"),
    match: str | None = typer.Option(None, "--match"),
) -> None:
    """Show recent lines, optionally following live."""
    s = settings_of(ctx)
    params = _lines_params(s, chan, match, None, n, None)
    body = Client(s).get("/lines", params=params)
    for row in reversed(body["lines"]):  # oldest first for reading
        out_json(row) if s.json_out else print(fmt_line(row))
    note_truncated(body, n)   # stderr, so a JSONL stdout stream stays parseable
    if follow:
        _follow_ws(s, chan, match)


def _follow_ws(s: Settings, chan: str | None, match: str | None) -> None:
    import asyncio

    import websockets

    ws_url = s.url.replace("http", "ws", 1) + "/ws"
    if s.port:
        ws_url += f"?port={s.port}"
    pat = re.compile(match) if match else None

    headers = s.headers()

    async def run() -> None:
        try:
            async with websockets.connect(ws_url, additional_headers=headers or None) as ws:
                while True:
                    # Each frame is an array of rows (SPEC 3.4); a bare object is still
                    # accepted so the CLI works against an older daemon.
                    msg = json.loads(await ws.recv())
                    for row in (msg if isinstance(msg, list) else [msg]):
                        if chan and row["chan"] != chan:
                            continue
                        if pat and not pat.search(row["raw"]):
                            continue
                        emit_stream(json.dumps(row) if s.json_out else fmt_line(row))
        except BrokenPipeError:
            raise                       # handled in main(): the reader closed the pipe, exit 0
        except OSError as exc:
            die(f"daemon unreachable at {s.url}: {exc}", 3)
        except websockets.exceptions.ConnectionClosed:
            # The daemon restarted or shut down under a live follow. That is an ordinary
            # end of stream, not a crash: this used to escape as a 6 KB rich traceback
            # because websockets' exceptions derive from Exception, not OSError.
            die("stream closed by daemon", 3)
        except websockets.exceptions.WebSocketException as exc:
            die(f"websocket error: {exc}", 3)
        except json.JSONDecodeError as exc:
            die(f"malformed frame from daemon: {exc}", 1)
        except KeyError as exc:
            die(f"unexpected row shape from daemon: missing {exc}", 1)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        raise typer.Exit(0) from None


@app.command()
def wait(
    ctx: typer.Context,
    match: str = typer.Option(..., "--match", help="Regex to match against raw lines."),
    timeout: int = typer.Option(2000, "--timeout", help="Timeout in ms."),
    send_cmd: str | None = typer.Option(None, "--send", help="Send this first, then wait."),
    chan: str | None = typer.Option(None, "--chan"),
    raw: bool = typer.Option(False, "--raw", help="Treat --send as a raw line, not a command."),
) -> None:
    """Wait for a line matching a regex, optionally sending first (the AI primitive)."""
    s = settings_of(ctx)
    body: dict[str, Any] = {"port": s.port, "match": match, "timeout_ms": timeout, "chan": chan}
    if send_cmd is not None:
        body["send"] = send_cmd
        body["send_mode"] = "raw" if raw else "cmd"
    res = Client(s).post("/wait", body, timeout=timeout / 1000 + 5)
    if s.json_out:
        out_json(res)
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
        0, "--timeout", help="Live window in ms. Omit to judge already-captured lines."
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
    # timeout_code=1: SPEC 4 states `mcu assert` never exits 2, and a transport timeout
    # (loaded or wedged daemon) was the one path that still could.
    res = Client(s).post("/assert", body, timeout=timeout / 1000 + 30, timeout_code=1)
    if s.json_out:
        out_json(res)
    else:
        for check in res["expect"]:
            if check["matched"]:
                print(f"  ok      expect {check['pattern']!r}: {check['line']['raw']}")
            else:
                err(f"  FAILED  expect {check['pattern']!r}: never seen")
        for check in res["forbid"]:
            if check["matched"]:
                err(f"  FAILED  forbid {check['pattern']!r}: {check['line']['raw']}")
            else:
                print(f"  ok      forbid {check['pattern']!r}: never seen")
        verdict = "PASS" if res["status"] == "pass" else "FAIL"
        print(f"{verdict}  {res['checked_lines']} lines checked in {res['elapsed_ms']:.0f} ms")
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
        print(f"session {res['session']['id']} started: {res['session']['name']}")


@session_app.command("stop")
def session_stop(ctx: typer.Context) -> None:
    """Close the running session."""
    s = settings_of(ctx)
    res = Client(s).post("/sessions/stop", {})
    if s.json_out:
        out_json(res)
    else:
        sess = res["session"]
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
    sessions = body["sessions"]
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
    body = Client(s).get("/sessions", params={"limit": 1000})
    match = None
    for sess in body["sessions"]:
        if str(sess["id"]) == name or sess["name"] == name:
            match = sess
            break
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


@app.command()
def purge(
    ctx: typer.Context,
    session: str | None = typer.Option(None, "--session", help="Delete a session's lines."),
    before_days: float | None = typer.Option(
        None, "--before-days", help="Delete lines older than N days."
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
            print(f"would delete {preview['deleted']} lines "
                  f"(ids {preview['id_from']}-{preview['id_to']})")
        raise typer.Exit(0)
    if preview["deleted"] == 0:
        if s.json_out:
            out_json(preview)
        else:
            print("nothing to delete")
        raise typer.Exit(0)
    if not yes:
        confirm_or_exit(
            f"permanently delete {preview['deleted']} lines "
            f"(ids {preview['id_from']}-{preview['id_to']})?"
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
    chan: str | None = typer.Option(None, "--chan"),
    match: str | None = typer.Option(None, "--match"),
    limit: int = typer.Option(1000, "--limit"),
    session: str | None = typer.Option(None, "--session", help="Scope to a session name/id."),
    out_file: str | None = typer.Option(None, "-o", "--out"),
) -> None:
    """Dump matching lines as JSONL (--json) or text.

    With -o the dump goes to the file and stdout carries only the result: a "wrote N
    lines" note, or with --json the one object SPEC 4 promises (`{"file", "lines"}`),
    where it used to print nothing at all.
    """
    s = settings_of(ctx)
    params = _lines_params(s, chan, match, last_ms, limit, None, session)
    body = Client(s).get("/lines", params=params)
    rows = list(reversed(body["lines"]))
    text = "\n".join(json.dumps(r) if s.json_out else fmt_line(r) for r in rows)
    if out_file:
        payload = text + ("\n" if text else "")
        try:
            # newline="\n" so the export is LF on every platform: the default (None)
            # translates to CRLF on Windows, which both inflates the file past the
            # "bytes" count below and makes the same capture export differently there.
            with open(out_file, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(payload)
        except OSError as exc:
            # An unwritable path is a user error, not a crash: it used to reach the user
            # as a raw FileNotFoundError traceback with no exit-code contract.
            die(f"cannot write {out_file}: {exc}", 1)
        if s.json_out:
            out_json({
                "file": out_file, "lines": len(rows),
                "bytes": len(payload.encode("utf-8")), "truncated": bool(body.get("truncated")),
            })
        else:
            print(f"wrote {len(rows)} lines to {out_file}")
    else:
        print(text)
    note_truncated(body, limit)


# -- bus sugar: can / i2c / spi / gpio / adc ------------------------------------------


def _run_cmd(ctx: typer.Context, text: str, timeout: int = 1000) -> None:
    s = settings_of(ctx)
    res = Client(s).post(
        "/cmd", {"port": s.port, "cmd": text, "timeout_ms": timeout}, timeout=timeout / 1000 + 5
    )
    emit_cmd_result(s, res)


can_app = typer.Typer(help="CAN commands.")
app.add_typer(can_app, name="can")


@can_app.command("tx")
def can_tx(
    ctx: typer.Context,
    can_id: str = typer.Argument(..., metavar="ID"),
    data: str | None = typer.Argument(None, metavar="DATA"),
    ext: bool = typer.Option(False, "--ext", help="29-bit extended id."),
    rtr: int | None = typer.Option(None, "--rtr", help="Send an RTR frame requesting N bytes."),
) -> None:
    """Transmit a CAN frame."""
    parts = ["can", "tx", can_id]
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
    _run_cmd(ctx, " ".join(parts))


@can_app.command("stat")
def can_stat(ctx: typer.Context) -> None:
    """Show CAN counters and state."""
    _run_cmd(ctx, "can stat")


@can_app.command(
    "filter",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def can_filter(ctx: typer.Context) -> None:
    """Set the CAN receive filter (e.g. `can filter all`, `can filter 100 700`)."""
    _run_cmd(ctx, " ".join(["can", "filter", *ctx.args]))


@can_app.command("dump")
def can_dump(
    ctx: typer.Context,
    can_id: str | None = typer.Option(None, "--id"),
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
    if last_ms is not None:
        params["last_ms"] = last_ms
    body = client.get("/can/frames", params=params)
    frames = list(reversed(body["frames"]))
    for fr in frames:
        out_json(fr) if s.json_out else print(fmt_frame(fr))
    if follow:
        _dump_follow(client, s, can_id)


def _dump_follow(client: Client, s: Settings, can_id: str | None) -> None:
    since = 0
    params: dict[str, Any] = {"limit": 1000}
    if s.port:
        params["port"] = s.port
    if can_id:
        params["id"] = can_id
    # prime `since` with the newest frame so we only print new ones
    body = client.get("/can/frames", params={**params, "limit": 1})
    if body["frames"]:
        since = body["frames"][0]["line_id"]
    try:
        while True:
            time.sleep(0.2)
            body = client.get("/can/frames", params={**params, "since_id": since})
            for fr in reversed(body["frames"]):
                since = max(since, fr["line_id"])
                emit_stream(json.dumps(fr) if s.json_out else fmt_frame(fr))
    except KeyboardInterrupt:
        raise typer.Exit(0) from None


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
def plot_channels(ctx: typer.Context) -> None:
    """List discovered plot channels (name, stream, unit, last value, point count)."""
    s = settings_of(ctx)
    body = Client(s).get("/plot/channels")
    channels = body["channels"]
    if s.json_out:
        out_json(body)
        return
    if not channels:
        print("no plot channels captured yet")
        return
    for ch in channels:
        sid = f"s{ch['sid']}" if ch["sid"] is not None else "adhoc"
        unit = f" {ch['unit']}" if ch.get("unit") else ""
        typ = ch.get("type") or "-"
        print(
            f"{ch['name']:<16} {sid:<6} {typ:<3} "
            f"last={ch['last_value']}{unit}  n={ch['count']}"
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

        try:
            client.stream_text("/plot/export", to_file, what=out_file, params=params)
        finally:
            fh.close()
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


def _host_port(s: Settings) -> tuple[str, int]:
    parsed = urlsplit(s.url)
    return parsed.hostname or "127.0.0.1", parsed.port or 8765


def _pid_file(s: Settings) -> str:
    """Path of the pid record for the daemon at `s.url` (see pidfile.py)."""
    from .pidfile import pid_file_path

    return pid_file_path(*_host_port(s))


def _legacy_pid_file() -> str:
    from .pidfile import legacy_pid_file

    return legacy_pid_file()


def _start_timeout_default() -> float:
    """Readiness wait for `daemon start`, overridable from the environment.

    Three seconds was optimistic: opening a multi-gigabyte capture, or a first run on a
    cold or network filesystem, can take longer, and the old code called that a failure.
    """
    raw = os.environ.get("MCUSCOPE_START_TIMEOUT")
    if raw:
        with contextlib.suppress(ValueError):
            return max(float(raw), 0.5)
    return 20.0


DAEMON_START_TIMEOUT_S = _start_timeout_default()
# How long `daemon stop` waits for the daemon to exit after a clean stop request
# (POST /shutdown, or the SIGTERM fallback on POSIX). Graceful shutdown itself is
# capped at 5s of in-flight requests (daemon.GRACEFUL_SHUTDOWN_S) plus the store flush.
DAEMON_STOP_GRACE_S = 10.0

_STATUS_BODY_KEYS = {"version", "uptime_s", "ports"}


def _is_status_body(body: Any) -> bool:
    """True if `body` looks like a genuine mcuscoped /status response."""
    return isinstance(body, dict) and _STATUS_BODY_KEYS <= body.keys()


def _status_body(s: Settings, timeout: float = 2.0) -> dict[str, Any] | None:
    """The daemon's /status body, or None if nothing at `s.url` is mcuscoped.

    A reachable URL that answers with something else (a stray service, a proxy, a stale
    process on the port) counts as "not running" rather than crashing on non-JSON or on
    missing keys. Shared by every `mcu daemon` subcommand so they agree on what "running"
    means.
    """
    try:
        body = httpx.get(s.url + "/status", timeout=timeout, headers=s.headers()).json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        return None
    return body if _is_status_body(body) else None


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
        with contextlib.suppress(OSError):
            os.remove(pid_path)
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
        with contextlib.suppress(OSError):
            os.remove(pid_path)
        die(f"mcuscoped did not come up at {s.url} within {wait_s:g}s; stopped it "
            f"(raise --timeout if it just needs longer)", 1)
    # Could not be stopped: keep the pid record so it stays addressable, and say so.
    die(f"mcuscoped did not come up at {s.url} within {wait_s:g}s and could not be "
        f"stopped; it is still running as pid {proc.pid} (pid file {pid_path})", 1)


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
    pid_path = _pid_file(s)
    # Atomically: a plain open("w") truncates first, and a concurrent `daemon stop`
    # reading at that instant would see an empty file, call it corrupt and delete it.
    tmp_path = pid_path + ".tmp"
    from .config import replace_atomic

    try:
        with open(tmp_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(str(proc.pid))
        replace_atomic(tmp_path, pid_path)
    except OSError as exc:
        # The daemon was already spawned above, so this must not become a traceback: that
        # would break the SPEC 4 exit-code contract *and* leave a running daemon behind.
        # It also is not fatal - the daemon claims its own record for the same host:port
        # on startup (pidfile.claim), which is what `daemon stop` reads - so say so and
        # carry on to the readiness wait rather than killing a healthy daemon.
        err(f"warning: could not write the pid file {pid_path}: {exc}")
        with contextlib.suppress(OSError):
            os.remove(tmp_path)   # do not leave the half-written .tmp lying beside it
    # Honour --timeout as given (clamped only against negatives). A 0.5s floor used to sit
    # here, which silently overrode the documented "Seconds to wait" for any smaller value
    # and turned "wait 0.05s" into a race the daemon could win on an idle machine.
    deadline = time.monotonic() + max(wait_s, 0.0)
    up = False
    while time.monotonic() < deadline:
        if _status_body(s, timeout=0.5) is not None:
            up = True
            break
        if proc.poll() is not None:      # it died; no point waiting out the deadline
            break
        time.sleep(0.1)
    if not up:
        _abandon_daemon(proc, pid_path, s, wait_s)
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
        # Fall back to the pre-keying path so a daemon started by an older `mcu` (which
        # wrote one shared file) is still stoppable after the upgrade.
        legacy = _legacy_pid_file()
        if os.path.exists(legacy):
            pid_path = legacy
        else:
            die("no pid file; daemon not started by this CLI", 1)
    try:
        with open(pid_path, encoding="utf-8") as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        # The remove itself can hit the same permission/lock problem; a traceback
        # here would violate the exit-code contract.
        with contextlib.suppress(OSError):
            os.remove(pid_path)
        die(f"pid file {pid_path} was unreadable or corrupt", 1)
    # Only act on a pid that a live mcuscoped is answering for. A pid file left behind
    # by a crashed daemon eventually names an unrelated, recycled process, and killing
    # that would be a nasty surprise; a stale file is simply removed instead.
    body = _status_body(s)
    if body is None:
        with contextlib.suppress(OSError):
            os.remove(pid_path)
        die(f"no daemon responding at {s.url}; removed stale pid file (was pid {pid})", 1)
    # The pid file can name a launcher shim rather than the daemon itself (Windows venv
    # launchers spawn the interpreter as a child, and `daemon start` recorded the pid it
    # spawned). /status reports the serving process, which is what a fallback kill must
    # target: terminating the shim can leave the real daemon running. Older daemons
    # (pre 0.1.2) do not report it; then the recorded pid is all there is.
    status_pid = body.get("pid")
    real_pid = status_pid if isinstance(status_pid, int) and status_pid > 0 else pid
    if not (_request_shutdown(s) and _wait_pid_gone(real_pid, DAEMON_STOP_GRACE_S)):
        # No POST /shutdown (older daemon), or it accepted and then failed to exit.
        try:
            _signal_daemon_stop(real_pid)
        except (ProcessLookupError, OSError) as exc:
            with contextlib.suppress(OSError):
                os.remove(pid_path)
            die(f"could not stop pid {real_pid}: {exc}", 1)
        if not _wait_pid_gone(real_pid, DAEMON_STOP_GRACE_S):
            die(f"pid {real_pid} did not exit within {DAEMON_STOP_GRACE_S:g}s", 1)
    # The daemon removes its own record when it owns one; this covers the launcher-pid
    # record it refused to clobber, tolerating whichever of us got there first.
    with contextlib.suppress(OSError):
        os.remove(pid_path)
    # Belt and braces for the shim case: if something still answers, the recorded pid
    # was not the daemon and the kill did not propagate. Say so rather than lie.
    if _status_body(s, timeout=1.0) is not None:
        die(f"a process is still answering at {s.url} after pid {real_pid} exited; "
            "the daemon runs under a different pid - stop it from the process list", 1)
    if s.json_out:
        out_json({"ok": True, "pid": real_pid})
    else:
        print(f"stopped mcuscoped (pid {real_pid})")


def _request_shutdown(s: Settings) -> bool:
    """True if the daemon accepted POST /shutdown (graceful stop on every platform).

    The endpoint exists because Windows has no graceful *signal* that crosses console
    boundaries (see _signal_daemon_stop); a REST call reaches the daemon no matter how
    it was launched. Absent on pre-0.1.2 daemons, which answer with an error envelope.
    """
    try:
        body = httpx.post(s.url + "/shutdown", timeout=2.0, headers=s.headers()).json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        return False
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

    On POSIX, SIGTERM is itself graceful: uvicorn handles it, the lifespan runs, so
    ports stop, the automatic session is closed with its ended_ts/end_id, the
    "daemon stop" sys row is written and the store writer is flushed.

    On Windows there is no graceful equivalent to send. os.kill maps every signal
    except the two console ctrl events onto TerminateProcess, and those events
    (GenerateConsoleCtrlEvent) only reach processes attached to the caller's console -
    which a daemon never is: `daemon start` detaches it from any console, and a
    directly-run mcuscoped is not a process-group leader, so CTRL_BREAK_EVENT either
    fails outright or is delivered to nobody. The old CTRL_BREAK path here never
    actually worked; POST /shutdown is the graceful Windows stop, and this hard
    TerminateProcess is the last resort behind it.
    """
    os.kill(pid, signal.SIGTERM)


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
  --url URL         daemon base URL (or env MCUSCOPE_URL); default http://127.0.0.1:8765
  --token TOKEN     access token for a remote daemon (or env MCUSCOPE_TOKEN)

HEALTH
  mcu status                      daemon + port health
  mcu ports                       list attached ports
  mcu devices                     list host serial devices (find /dev/ttyACM0, COMx)
  mcu attach socket://127.0.0.1:9900 --alias board
  mcu detach board

THE CORE LOOP (send, wait, query)
  mcu cmd "i2c rd 48 2"           send a command, print response data; ERR -> stderr, exit 1
  mcu wait --match "^!can" --timeout 2000        block until a line matches; exit 2 on timeout
  mcu wait --send "can tx 300 AABB" --match "301 AABB"   send then wait for the reply
  mcu lines --last-ms 5000 --chan event --match "1A3"    query the capture (the workhorse)
  mcu tail -f --chan debug        follow live output
  mcu mark "starting test"        drop an annotation into the log

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

BUS SUGAR (all wrap `cmd`)
  mcu can tx 1A3 DEADBEEF [--ext] [--rtr 4]
  mcu can dump --id 100 -f        decoded CAN frames, live
  mcu can stat / mcu can filter all
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
  5. mcu assert --last-ms N --expect ... --forbid ...   (decide pass/fail on an exit code)

DAEMON CONTROL
  mcu daemon start | stop | status
  mcu daemon start --timeout 60      wait longer for a big capture to open (env
                                     MCUSCOPE_START_TIMEOUT); on failure the spawned
                                     daemon is stopped, never left orphaned
"""


@app.command("ai-guide")
def ai_guide() -> None:
    """Print a compact usage guide written for an AI agent."""
    print(AI_GUIDE)


# -- entry point ----------------------------------------------------------------------


_GLOBAL_FLAGS = {"--json"}
_GLOBAL_VALUE_OPTS = {"--port", "-p", "--url", "--token"}


def _value_taking_opts(argv: list[str]) -> set[str]:
    """Option strings of the targeted subcommand that consume a following value.

    Hoisting runs before any parsing, so without this it cannot tell a global option from
    a subcommand option's *value*. `mcu lines --match -p --limit 5` meant the regex `-p`,
    but `-p` was hoisted as the port alias and stole `--limit` as its value, leaving the
    regex as `5` - a silent wrong answer with exit 0. Resolving the subcommand up front
    tells us which tokens are values and must be left alone. Best effort: any failure
    falls back to an empty set, i.e. the previous behaviour.
    """
    try:
        node = typer.main.get_command(app)
        skip_value = False
        for tok in argv:
            # The value of a *global* option is not the subcommand name. Without this the
            # walk stopped at the first such value (`mcu -p board lines ...` looked up a
            # command called "board"), fell back to the root group's options, and the
            # guard below stopped protecting subcommand option values - reintroducing
            # exactly the bug this function exists to prevent.
            if skip_value:
                skip_value = False
                continue
            if tok == "--":
                break
            if tok in _GLOBAL_VALUE_OPTS:
                skip_value = True
                continue
            if tok.startswith("-"):
                continue
            # Duck-typed on purpose: typer vendors its own copy of click, so the group it
            # builds is not an instance of the `click.Group` imported here and an
            # isinstance() check silently never descends into the subcommand.
            subs = getattr(node, "commands", None)
            if not subs:
                break
            sub = subs.get(tok)
            if sub is None:
                break
            node = sub
        opts: set[str] = set()
        for prm in getattr(node, "params", []):
            if getattr(prm, "is_flag", False):
                continue
            for o in list(getattr(prm, "opts", [])) + list(getattr(prm, "secondary_opts", [])):
                if o.startswith("-"):
                    opts.add(o)
        return opts
    except Exception:
        return set()


def _hoist_global_opts(argv: list[str]) -> list[str]:
    """Move global options (--json, --port/-p, --url, --token) to the front.

    Click only accepts group-level options before the subcommand, but the SPEC's
    usage puts them anywhere (e.g. `mcu i2c rd 48 2 --json`). Hoisting them keeps
    both orders working. A bare `--` (end-of-options) stops hoisting: everything from
    that token on is left untouched so a literal "--json" (or similar) can still be
    passed through as a positional argument. A token sitting in the value position of a
    subcommand option is never hoisted (see _value_taking_opts).
    """
    head: list[str] = []
    rest: list[str] = []
    value_opts = _value_taking_opts(argv)
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            rest.extend(argv[i:])
            break
        # The previous token is a subcommand option awaiting a value, so this token is
        # that value however much it looks like a global option.
        if i > 0 and argv[i - 1] in value_opts and argv[i - 1] not in _GLOBAL_VALUE_OPTS:
            rest.append(a)
            i += 1
            continue
        if a in _GLOBAL_FLAGS or a.startswith("--json="):
            head.append(a)
        elif a in _GLOBAL_VALUE_OPTS:
            head.append(a)
            if i + 1 < len(argv):
                i += 1
                head.append(argv[i])
        elif a.startswith(("--port=", "--url=", "--token=")):
            head.append(a)
        elif len(a) > 2 and a.startswith("-p") and not a.startswith("--"):
            head.append(a)   # attached short form, e.g. -psim
        else:
            rest.append(a)
        i += 1
    return head + rest


def _widen_stdout_encoding() -> None:
    """Stop a non-ASCII character from turning into a traceback when stdout is redirected.

    Attached to a console, Python encodes stdout as UTF-8. Redirected to a pipe or file it
    falls back to the locale encoding, which on Windows is cp1252, and `mcu devices` prints
    port descriptions straight from setupapi: a CH340 clone with a CJK brand string made
    `mcu devices > log.txt` die with UnicodeEncodeError and a traceback, breaking the
    SPEC 4 exit-code contract. UTF-8 matches what the export paths already write, and
    errors="replace" means even an undecodable byte degrades to `?` rather than raising.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # already replaced by _stdio, or not a text stream
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _widen_stdout_encoding()
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


def _dispatch(argv: list[str] | None = None) -> int:
    # With standalone_mode=False, click returns a command's `Exit` code as the call's
    # return value (rather than exiting), so capture it. Older clicks raise instead,
    # so the except clauses below cover both behaviors.
    if argv is None:
        argv = sys.argv[1:]
    argv = _hoist_global_opts(argv)
    try:
        rv = app(args=argv, standalone_mode=False)
        return rv if isinstance(rv, int) else 0
    except EXIT_EXCEPTIONS as exc:
        return int(getattr(exc, "exit_code", 0) or 0)
    except USAGE_ERRORS as exc:
        exc.show()
        return 1
    except ABORT_EXCEPTIONS:
        err("aborted")
        return 1
    except BrokenPipeError:
        # `mcu tail | head` closes the pipe early. That is the reader's normal exit, not
        # our failure, so report success - and redirect stdout to devnull first, because
        # the interpreter flushes it during shutdown and would print its own
        # "Exception ignored ... BrokenPipeError" and exit 120 over the top of us.
        _silence_stdout()
        return 0
    except KeyboardInterrupt:
        err("interrupted")
        return 1
    except KeyError as exc:
        # A daemon response missing a key we index directly (version skew, a proxy, the
        # wrong port). Every command indexed body["..."] unguarded, so this reached the
        # user as a rich traceback instead of an exit code.
        err(f"unexpected response from daemon: missing {exc}")
        return 1
    except SystemExit as exc:  # e.g. --help
        code = int(exc.code) if isinstance(exc.code, int) else 0
        if code == 1 and type(sys.stdout).__name__ == "PacifyFlushWrapper":
            # A broken pipe raised *inside* a command never reaches our own handler:
            # click/typer catch EPIPE themselves, swap stdout for a PacifyFlushWrapper and
            # sys.exit(1). `mcu lines | head` is not a failure, so translate it back to 0.
            # Duck-typed on the wrapper because typer vendors its own copy of click.
            return 0
        return code


def console_entry() -> int:
    """Console-script entry: repaired std streams plus a crash-file backstop."""
    return _stdio.console_entry(main, "mcu")


if __name__ == "__main__":
    raise SystemExit(console_entry())
