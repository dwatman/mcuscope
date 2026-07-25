"""CLI tests: drive the `mcu` entry point as a subprocess against a live daemon.

Uses `python -m mcuscope.cli` (equivalent to the installed `mcu` console script) with
MCUSCOPE_URL pointed at the per-test stack, so the real exit-code contract and --json
output shapes are exercised end to end. Cross-platform.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import typer

from mcuscope.cli import Client, Settings
from tests.support import Stack

MCU = [sys.executable, "-m", "mcuscope.cli"]


def run_mcu(
    stack: Stack | None, *args: str, url: str | None = None, timeout: float = 20.0
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MCUSCOPE_URL"] = url if url is not None else (stack.base_url if stack else "")
    return subprocess.run(
        [*MCU, *args], capture_output=True, text=True, env=env, timeout=timeout
    )


# -- exit-code contract ---------------------------------------------------------------


def test_cmd_ok_exit0_prints_data(stack: Stack) -> None:
    r = run_mcu(stack, "cmd", "i2c scan")
    assert r.returncode == 0
    assert r.stdout.strip() == "48 50"


def test_cmd_err_exit1_stderr(stack: Stack) -> None:
    r = run_mcu(stack, "cmd", "gpio get nope")
    assert r.returncode == 1
    assert "badarg" in r.stderr


def test_cmd_timeout_exit2(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--drop-response", "1"])
    r = run_mcu(stack, "cmd", "ping", "--timeout", "500")
    assert r.returncode == 2
    assert "timeout" in r.stderr


def test_unreachable_exit3() -> None:
    r = run_mcu(None, "status", url="http://127.0.0.1:1")
    assert r.returncode == 3
    assert r.stderr.strip()  # one-line message on stderr


def test_bad_usage_exit1() -> None:
    r = run_mcu(None, "no-such-command", url="http://127.0.0.1:1")
    assert r.returncode == 1


# -- --json output shapes -------------------------------------------------------------


def test_cmd_json_shape(stack: Stack) -> None:
    r = run_mcu(stack, "--json", "cmd", "i2c scan")
    assert r.returncode == 0
    obj = json.loads(r.stdout)
    assert obj["status"] == "ok" and obj["data"] == "48 50"
    assert isinstance(obj["line_id"], int)


def test_status_json_shape(stack: Stack) -> None:
    obj = json.loads(run_mcu(stack, "--json", "status").stdout)
    assert obj["ports"][0]["connected"] is True
    assert "db_size_bytes" in obj


def test_lines_json_shape(stack: Stack) -> None:
    r = run_mcu(stack, "--json", "lines", "--last-ms", "100000", "--limit", "5")
    obj = json.loads(r.stdout)
    assert "lines" in obj and "truncated" in obj


def test_wait_json_match(stack: Stack) -> None:
    r = run_mcu(stack, "--json", "wait", "--match", "^!can", "--timeout", "2000")
    assert r.returncode == 0
    assert json.loads(r.stdout)["status"] == "match"


def test_wait_timeout_exit2(stack: Stack) -> None:
    r = run_mcu(stack, "wait", "--match", "ZZZ_NEVER", "--timeout", "300")
    assert r.returncode == 2


def test_can_dump_json(stack: Stack) -> None:
    # Backfill (non-follow) --json must emit one JSON object per frame (JSONL), matching
    # the follow-mode wire format instead of a single aggregate body.
    frames: list[dict] = []
    for _ in range(30):
        r = run_mcu(stack, "--json", "can", "dump", "--id", "100", "-n", "5")
        frames = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
        if frames:
            break
        time.sleep(0.1)
    assert frames
    assert frames[0]["can_id"] == 0x100


def test_can_dump_follow_json(stack: Stack) -> None:
    # `can dump -f --json` must be consistent JSONL end to end: no human-format lines
    # mixed into the backfill portion before the live follow portion kicks in.
    env = os.environ.copy()
    env["MCUSCOPE_URL"] = stack.base_url
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [*MCU, "--json", "can", "dump", "--id", "100", "-n", "0", "-f"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        time.sleep(1.5)  # sim emits a 10 Hz CAN heartbeat on id 0x100
    finally:
        proc.terminate()
    out, _ = proc.communicate(timeout=5)
    lines = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert lines
    assert all(fr["can_id"] == 0x100 for fr in lines)


# -- i2c sugar: --reg maps to wrrd ----------------------------------------------------


def test_i2c_rd_trailing_json(stack: Stack) -> None:
    # The SPEC acceptance form puts --json last: `mcu i2c rd 48 2 --json`.
    r = run_mcu(stack, "i2c", "rd", "48", "2", "--json")
    assert r.returncode == 0
    obj = json.loads(r.stdout)
    assert obj["status"] == "ok" and len(obj["data"]) == 4  # two temp bytes


def test_i2c_reg_maps_to_wrrd(stack: Stack) -> None:
    # Write CAFE at EEPROM offset 0x10, then read it back via a register read (--reg).
    assert run_mcu(stack, "i2c", "wr", "50", "10CAFE").returncode == 0
    obj = json.loads(run_mcu(stack, "--json", "i2c", "rd", "50", "2", "--reg", "10").stdout)
    assert obj["status"] == "ok" and obj["data"] == "CAFE"


# -- ai-guide (no daemon needed) ------------------------------------------------------


def test_ai_guide() -> None:
    r = subprocess.run([*MCU, "ai-guide"], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0
    assert "EXIT CODES" in r.stdout
    assert "--json" in r.stdout


# -- plot channels / export (SPEC 9.2) ------------------------------------------------


def _wait_plot_names(stack: Stack, need: set[str], tries: int = 60) -> dict[str, dict]:
    by_name: dict[str, dict] = {}
    for _ in range(tries):
        obj = json.loads(run_mcu(stack, "--json", "plot", "channels").stdout)
        by_name = {ch["name"]: ch for ch in obj["channels"]}
        if need <= set(by_name):
            break
        time.sleep(0.1)
    return by_name


def test_plot_channels_json(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--plot"])
    by_name = _wait_plot_names(stack, {"tri", "sine"})
    assert by_name["tri"]["sid"] == "0" and by_name["tri"]["unit"] == "V"
    assert by_name["sine"]["sid"] is None


def test_plot_export_wide_csv(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--plot"])
    for _ in range(60):
        by_name = {ch["name"]: ch for ch in
                   json.loads(run_mcu(stack, "--json", "plot", "channels").stdout)["channels"]}
        if by_name.get("tri", {}).get("count", 0) >= 10:
            break
        time.sleep(0.1)
    r = run_mcu(stack, "plot", "export", "--names", "tri,ramp,ftest", "--wide")
    assert r.returncode == 0
    lines = r.stdout.strip().splitlines()
    assert lines[0] == "ts,tick_ms,tri,ramp,ftest"
    assert len(lines) >= 2


def test_plot_export_wide_mixed_streams_exit1(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--plot"])
    _wait_plot_names(stack, {"tri", "sine"})
    r = run_mcu(stack, "plot", "export", "--names", "sine,tri", "--wide")
    assert r.returncode == 1
    assert "error" in r.stderr


# -- devices ---------------------------------------------------------------------------


def test_devices_json_passthrough(stack: Stack) -> None:
    # The sim is a socket:// URL so it never shows up in the host's real serial device
    # list; the list may legitimately be empty. Just check the shape passes through raw.
    r = run_mcu(stack, "--json", "devices")
    assert r.returncode == 0
    body = json.loads(r.stdout)
    assert "devices" in body
    for d in body["devices"]:
        assert set(d) == {"device", "by_id", "description", "vid_pid", "serial_number"}


def test_devices_human(stack: Stack) -> None:
    r = run_mcu(stack, "devices")
    assert r.returncode == 0
    body = json.loads(run_mcu(stack, "--json", "devices").stdout)
    if not body["devices"]:
        assert r.stdout.strip() == "no serial devices found"
    else:
        assert r.stdout.strip()
        first = body["devices"][0]
        assert first["device"] in r.stdout


# -- _hoist_global_opts honors `--` -----------------------------------------------------


def test_hoist_respects_end_of_options(stack: Stack) -> None:
    # Before the fix, a literal "--json" after "--" would be hoisted as the global flag,
    # leaving `send`'s required TEXT argument unfilled (usage error, exit 1).
    r = run_mcu(stack, "send", "--", "--json")
    assert r.returncode == 0
    assert r.stdout.strip() == "ok"


# -- Client.request timeout classification (exit 2, not 3) -----------------------------


def test_read_timeout_exit2_not_unreachable() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def _accept_and_stall() -> None:
        conn, _ = srv.accept()
        stop.wait(5)
        conn.close()

    t = threading.Thread(target=_accept_and_stall, daemon=True)
    t.start()
    try:
        s = Settings(url=f"http://127.0.0.1:{port}", json_out=False, port=None)
        with pytest.raises(typer.Exit) as ei:
            Client(s).request("GET", "/status", timeout=0.2)
        assert ei.value.exit_code == 2
    finally:
        stop.set()
        srv.close()
        t.join(timeout=2)


# -- daemon start ------------------------------------------------------------------------


def test_daemon_start_already_running_exit1(stack: Stack) -> None:
    # Also exercises the "looks like a real /status body" verification: the live test
    # stack's daemon genuinely returns version/uptime_s/ports, so this must short-circuit
    # before ever trying to spawn a second mcuscoped process.
    r = run_mcu(stack, "daemon", "start")
    assert r.returncode == 1
    assert "already running" in r.stderr


# NOTE: the "not yet up" polling loop and the --host/--port passthrough to the spawned
# `python -m mcuscope.daemon` process are not covered here: exercising them means
# actually spawning a real mcuscoped subprocess (binding a real port, needing cleanup,
# potentially racy in CI), which the task explicitly asked to skip when not testable
# without spawning real subprocesses.


# -- daemon status / stop ---------------------------------------------------------------


class _JsonHandler(BaseHTTPRequestHandler):
    """A reachable HTTP server that is not mcuscoped: valid JSON, wrong shape."""

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        body = b'{"hello": 1}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _serve_http(handler) -> tuple[HTTPServer, threading.Thread, str]:
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t, f"http://127.0.0.1:{httpd.server_address[1]}"


def test_daemon_status_running_exit0(stack: Stack) -> None:
    r = run_mcu(stack, "daemon", "status")
    assert r.returncode == 0
    assert r.stdout.startswith("running:")


def test_daemon_status_unreachable_exit3() -> None:
    r = run_mcu(None, "daemon", "status", url="http://127.0.0.1:1")
    assert r.returncode == 3
    assert "not running" in r.stdout


def test_daemon_status_non_mcuscoped_json_exit3() -> None:
    # A reachable server returning JSON that is not a /status body: exit 3, no traceback.
    httpd, t, url = _serve_http(_JsonHandler)
    try:
        r = run_mcu(None, "daemon", "status", url=url)
        assert r.returncode == 3
        assert "Traceback" not in r.stderr
        assert "not running" in r.stdout
    finally:
        httpd.shutdown()
        t.join(timeout=2)


def test_daemon_status_non_json_body_exit3() -> None:
    # Default BaseHTTPRequestHandler answers 501 with an HTML body (not JSON).
    httpd, t, url = _serve_http(BaseHTTPRequestHandler)
    try:
        r = run_mcu(None, "daemon", "status", url=url)
        assert r.returncode == 3
        assert "Traceback" not in r.stderr
    finally:
        httpd.shutdown()
        t.join(timeout=2)


_PIDDIR_ENV_SKIP = pytest.mark.skipif(
    os.name == "nt",
    reason="platformdirs resolves the Windows data dir via the shell API, not env vars",
)


def _run_mcu_data_home(data_home: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["XDG_DATA_HOME"] = data_home
    env["MCUSCOPE_URL"] = "http://127.0.0.1:1"
    return subprocess.run(
        [*MCU, *args], capture_output=True, text=True, env=env, timeout=20
    )


@_PIDDIR_ENV_SKIP
def test_daemon_stop_no_pidfile_exit1(tmp_path) -> None:
    r = _run_mcu_data_home(str(tmp_path), "daemon", "stop")
    assert r.returncode == 1
    assert "no pid file" in r.stderr


@_PIDDIR_ENV_SKIP
def test_daemon_stop_corrupt_pidfile_exit1(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    import platformdirs

    data_dir = platformdirs.user_data_dir("mcuscope")
    os.makedirs(data_dir, exist_ok=True)
    pid_path = os.path.join(data_dir, "mcuscoped.pid")
    with open(pid_path, "w", encoding="utf-8") as fh:
        fh.write("not-a-pid")
    r = _run_mcu_data_home(str(tmp_path), "daemon", "stop")
    assert r.returncode == 1
    assert "corrupt" in r.stderr
    assert not os.path.exists(pid_path)  # the bad file was cleaned up


# -- sessions -------------------------------------------------------------------------


def test_session_start_stop_list_and_scoping(stack: Stack) -> None:
    # The whole agent-facing workflow: name a run, do something in it, close it, and get
    # back only that run's lines.
    started = run_mcu(stack, "session", "start", "cli-run", "--note", "from the CLI", "--json")
    assert started.returncode == 0
    session = json.loads(started.stdout)["session"]
    assert session["name"] == "cli-run" and session["ended_ts"] is None

    assert run_mcu(stack, "mark", "inside cli-run").returncode == 0
    assert run_mcu(stack, "session", "stop").returncode == 0
    assert run_mcu(stack, "mark", "after cli-run").returncode == 0

    scoped = run_mcu(stack, "lines", "--session", "cli-run", "--limit", "200", "--json")
    assert scoped.returncode == 0
    raws = [r["raw"] for r in json.loads(scoped.stdout)["lines"]]
    assert "inside cli-run" in raws
    assert "after cli-run" not in raws

    listing = run_mcu(stack, "session", "list", "--json")
    assert listing.returncode == 0
    names = [s["name"] for s in json.loads(listing.stdout)["sessions"]]
    assert "cli-run" in names

    # Human output stays parseable at a glance.
    human = run_mcu(stack, "session", "list")
    assert human.returncode == 0
    assert "cli-run" in human.stdout


def test_session_stop_without_one_exits_1(stack: Stack) -> None:
    run_mcu(stack, "session", "stop")          # ensure nothing is running
    r = run_mcu(stack, "session", "stop")
    assert r.returncode == 1
    assert "no session" in r.stderr
