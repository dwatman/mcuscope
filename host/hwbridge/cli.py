"""The `mcu` command-line client: a thin HTTP client of hwbridged (SPEC 4).

Exit-code contract (for AI use): 0 success/match, 1 error (bus ERR, HTTP error, bad
usage), 2 timeout, 3 daemon unreachable. With --json, each command prints exactly one
JSON object (streaming commands print one object per line).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

import click
import httpx
import platformdirs
import typer
import websockets

DEFAULT_URL = "http://127.0.0.1:8765"
APP_NAME = "hwbridge"


@dataclass
class Settings:
    url: str
    json_out: bool
    port: str | None


# -- small output / error helpers -----------------------------------------------------


def err(msg: str) -> None:
    print(msg, file=sys.stderr)


def die(msg: str, code: int) -> None:
    err(msg)
    raise typer.Exit(code)


def out_json(obj: Any) -> None:
    print(json.dumps(obj))


def fmt_ts(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts)) + f".{int(ts * 1000) % 1000:03d}"


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

    def request(self, method: str, path: str, timeout: float = 30.0, **kw: Any) -> httpx.Response:
        try:
            return httpx.request(method, self.s.url + path, timeout=timeout, **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            die(f"daemon unreachable at {self.s.url}: {exc}", 3)
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
        return resp.json()

    def get(self, path: str, **kw: Any) -> Any:
        return self.json_or_die(self.request("GET", path, **kw))

    def post(self, path: str, body: dict[str, Any], **kw: Any) -> Any:
        return self.json_or_die(self.request("POST", path, json=body, **kw))

    def delete(self, path: str, **kw: Any) -> Any:
        return self.json_or_die(self.request("DELETE", path, **kw))


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


@app.callback()
def _global(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    port: str | None = typer.Option(None, "--port", "-p", help="Port alias (default: sole port)."),
    url: str | None = typer.Option(None, "--url", help="Daemon base URL (or env HWBRIDGE_URL)."),
) -> None:
    resolved = url or os.environ.get("HWBRIDGE_URL") or DEFAULT_URL
    ctx.obj = Settings(url=resolved.rstrip("/"), json_out=json_out, port=port)


# -- status / ports -------------------------------------------------------------------


@app.command()
def status(ctx: typer.Context) -> None:
    """Daemon and port health."""
    s = settings_of(ctx)
    body = Client(s).get("/status")
    if s.json_out:
        out_json(body)
        return
    print(f"hwbridged {body['version']}  up {body['uptime_s']:.0f}s  db {body['db_path']}")
    for pt in body["ports"]:
        state = "connected" if pt["connected"] else "disconnected"
        print(
            f"  {pt['alias']:<10} {pt['device']}  @{pt['baud']}  {state}  "
            f"rx={pt['lines_rx']} tx={pt['lines_tx']}"
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


def _derive_alias(device: str) -> str:
    if "://" in device:
        return "board"
    return os.path.basename(device.rstrip("/")) or "board"


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
    limit: int, since_id: int | None,
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
    return params


@app.command()
def lines(
    ctx: typer.Context,
    last_ms: int | None = typer.Option(None, "--last-ms"),
    chan: str | None = typer.Option(None, "--chan"),
    match: str | None = typer.Option(None, "--match"),
    limit: int = typer.Option(100, "--limit"),
    since_id: int | None = typer.Option(None, "--since-id"),
) -> None:
    """Query the capture (the AI workhorse)."""
    s = settings_of(ctx)
    params = _lines_params(s, chan, match, last_ms, limit, since_id)
    body = Client(s).get("/lines", params=params)
    if s.json_out:
        out_json(body)
        return
    for row in reversed(body["lines"]):  # oldest first for reading
        print(fmt_line(row))


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
    if follow:
        _follow_ws(s, chan, match)


def _follow_ws(s: Settings, chan: str | None, match: str | None) -> None:
    ws_url = s.url.replace("http", "ws", 1) + "/ws"
    if s.port:
        ws_url += f"?port={s.port}"
    pat = re.compile(match) if match else None

    async def run() -> None:
        try:
            async with websockets.connect(ws_url) as ws:
                while True:
                    row = json.loads(await ws.recv())
                    if chan and row["chan"] != chan:
                        continue
                    if pat and not pat.search(row["raw"]):
                        continue
                    out_json(row) if s.json_out else print(fmt_line(row))
        except OSError as exc:
            die(f"daemon unreachable at {s.url}: {exc}", 3)

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


log_app = typer.Typer(help="Export captured lines.")
app.add_typer(log_app, name="log")


@log_app.command("export")
def log_export(
    ctx: typer.Context,
    last_ms: int | None = typer.Option(None, "--last-ms"),
    chan: str | None = typer.Option(None, "--chan"),
    match: str | None = typer.Option(None, "--match"),
    limit: int = typer.Option(1000, "--limit"),
    out_file: str | None = typer.Option(None, "-o", "--out"),
) -> None:
    """Dump matching lines as JSONL (--json) or text."""
    s = settings_of(ctx)
    params = _lines_params(s, chan, match, last_ms, limit, None)
    body = Client(s).get("/lines", params=params)
    rows = list(reversed(body["lines"]))
    text = "\n".join(json.dumps(r) if s.json_out else fmt_line(r) for r in rows)
    if out_file:
        with open(out_file, "w", encoding="utf-8") as fh:
            fh.write(text + ("\n" if text else ""))
        if not s.json_out:
            print(f"wrote {len(rows)} lines to {out_file}")
    else:
        print(text)


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
    if s.json_out and not follow:
        out_json(body)
    else:
        for fr in frames:
            print(fmt_frame(fr))
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
                out_json(fr) if s.json_out else print(fmt_frame(fr))
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


# -- daemon control -------------------------------------------------------------------


daemon_app = typer.Typer(help="Start/stop/check the local hwbridged daemon.")
app.add_typer(daemon_app, name="daemon")


def _pid_file() -> str:
    data_dir = platformdirs.user_data_dir(APP_NAME)
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "hwbridged.pid")


@daemon_app.command("start")
def daemon_start(ctx: typer.Context) -> None:
    """Spawn hwbridged as a detached background process (cross-platform)."""
    s = settings_of(ctx)
    # already running?
    try:
        httpx.get(s.url + "/status", timeout=1.0)
        die("daemon already running", 1)
    except httpx.HTTPError:
        pass
    args = [sys.executable, "-m", "hwbridge.daemon"]
    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        )
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, **kwargs)
    with open(_pid_file(), "w", encoding="utf-8") as fh:
        fh.write(str(proc.pid))
    if s.json_out:
        out_json({"ok": True, "pid": proc.pid})
    else:
        print(f"started hwbridged (pid {proc.pid})")


@daemon_app.command("stop")
def daemon_stop(ctx: typer.Context) -> None:
    """Stop the hwbridged process started by `daemon start`."""
    s = settings_of(ctx)
    pid_path = _pid_file()
    if not os.path.exists(pid_path):
        die("no pid file; daemon not started by this CLI", 1)
    with open(pid_path, encoding="utf-8") as fh:
        pid = int(fh.read().strip())
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError) as exc:
        os.remove(pid_path)
        die(f"could not stop pid {pid}: {exc}", 1)
    os.remove(pid_path)
    if s.json_out:
        out_json({"ok": True, "pid": pid})
    else:
        print(f"stopped hwbridged (pid {pid})")


@daemon_app.command("status")
def daemon_status(ctx: typer.Context) -> None:
    """Report whether the daemon is reachable."""
    s = settings_of(ctx)
    try:
        body = httpx.get(s.url + "/status", timeout=2.0).json()
    except httpx.HTTPError:
        if s.json_out:
            out_json({"running": False})
        else:
            print("not running")
        raise typer.Exit(3) from None
    if s.json_out:
        out_json({"running": True, "version": body["version"], "uptime_s": body["uptime_s"]})
    else:
        print(f"running: hwbridged {body['version']} up {body['uptime_s']:.0f}s")


# -- ai-guide -------------------------------------------------------------------------

AI_GUIDE = """\
mcu: hardware debug bridge CLI (talks to the hwbridged daemon over 127.0.0.1)

WHAT IT IS
  hwbridged owns the serial link to an MCU running the "monitor" firmware and logs
  every line to SQLite. `mcu` is a thin client. Prefer --json for machine parsing.

EXIT CODES (contract)
  0 success / match    1 error (bus ERR, HTTP error, bad usage)
  2 timeout            3 daemon unreachable

GLOBAL OPTIONS
  --json            one JSON object per command (streaming cmds: one per line)
  -p, --port ALIAS  choose a port (default: the only attached port)
  --url URL         daemon base URL (or env HWBRIDGE_URL); default http://127.0.0.1:8765

HEALTH
  mcu status                      daemon + port health
  mcu ports                       list attached ports
  mcu attach socket://127.0.0.1:9900 --alias board
  mcu detach board

THE CORE LOOP (send, wait, query)
  mcu cmd "i2c rd 48 2"           send a command, print response data; ERR -> stderr, exit 1
  mcu wait --match "^!can" --timeout 2000        block until a line matches; exit 2 on timeout
  mcu wait --send "can tx 300 AABB" --match "301 AABB"   send then wait for the reply
  mcu lines --last-ms 5000 --chan event --match "1A3"    query the capture (the workhorse)
  mcu tail -f --chan debug        follow live output
  mcu mark "starting test"        drop an annotation into the log

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

DAEMON CONTROL
  mcu daemon start | stop | status
"""


@app.command("ai-guide")
def ai_guide() -> None:
    """Print a compact usage guide written for an AI agent."""
    print(AI_GUIDE)


# -- entry point ----------------------------------------------------------------------


_GLOBAL_FLAGS = {"--json"}
_GLOBAL_VALUE_OPTS = {"--port", "-p", "--url"}


def _hoist_global_opts(argv: list[str]) -> list[str]:
    """Move global options (--json, --port/-p, --url) to the front.

    Click only accepts group-level options before the subcommand, but the SPEC's
    usage puts them anywhere (e.g. `mcu i2c rd 48 2 --json`). Hoisting them keeps
    both orders working. None of the subcommands define these option names, so this
    is unambiguous.
    """
    head: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in _GLOBAL_FLAGS or a.startswith("--json="):
            head.append(a)
        elif a in _GLOBAL_VALUE_OPTS:
            head.append(a)
            if i + 1 < len(argv):
                i += 1
                head.append(argv[i])
        elif a.startswith("--port=") or a.startswith("--url="):
            head.append(a)
        else:
            rest.append(a)
        i += 1
    return head + rest


def main(argv: list[str] | None = None) -> int:
    # With standalone_mode=False, click returns a command's `Exit` code as the call's
    # return value (rather than exiting), so capture it. Older clicks raise instead,
    # so the except clauses below cover both behaviors.
    if argv is None:
        argv = sys.argv[1:]
    argv = _hoist_global_opts(argv)
    try:
        rv = app(args=argv, standalone_mode=False)
        return rv if isinstance(rv, int) else 0
    except (typer.Exit, click.exceptions.Exit) as exc:
        return int(getattr(exc, "exit_code", 0) or 0)
    except click.exceptions.UsageError as exc:
        exc.show()
        return 1
    except click.exceptions.Abort:
        err("aborted")
        return 1
    except SystemExit as exc:  # e.g. --help
        return int(exc.code) if isinstance(exc.code, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
