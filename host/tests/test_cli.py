"""CLI tests: drive the `mcu` entry point as a subprocess against a live daemon.

Uses `python -m mcuscope.cli` (equivalent to the installed `mcu` console script) with
MCUSCOPE_URL pointed at the per-test stack, so the real exit-code contract and --json
output shapes are exercised end to end. Cross-platform.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
import typer

from mcuscope.cli import Client, Settings
from tests.support import Stack

MCU = [sys.executable, "-m", "mcuscope.cli"]


def run_mcu(
    stack: Stack | None,
    *args: str,
    url: str | None = None,
    timeout: float = 20.0,
    stdin: str = "",
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MCUSCOPE_URL"] = url if url is not None else (stack.base_url if stack else "")
    return subprocess.run(
        [*MCU, *args], capture_output=True, text=True, env=env, timeout=timeout, input=stdin
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


def test_lines_reports_truncation_on_stderr(stack: Stack) -> None:
    # A capped query used to look like a complete one in human output: only --json ever
    # showed "truncated", so "the error never happened" could be read off a short window.
    for i in range(3):
        run_mcu(stack, "mark", f"trunc-{i}")
    r = run_mcu(stack, "lines", "--chan", "marker", "--limit", "1")
    assert r.returncode == 0
    assert len(r.stdout.strip().splitlines()) == 1     # stdout stays pure rows
    assert "truncated" in r.stderr

    # ... and --json still prints exactly one object, with the flag inside it.
    j = run_mcu(stack, "--json", "lines", "--chan", "marker", "--limit", "1")
    assert json.loads(j.stdout)["truncated"] is True


def test_die_emits_a_json_error_object(stack: Stack) -> None:
    # With --json, a failure used to print nothing on stdout at all.
    r = run_mcu(None, "--json", "status", url="http://127.0.0.1:1")
    assert r.returncode == 3
    obj = json.loads(r.stdout)
    assert obj["exit_code"] == 3 and "unreachable" in obj["error"]
    assert r.stderr.strip()          # the human message still goes to stderr


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
    out_lines: list[str] = []
    got_frame = threading.Event()

    def drain() -> None:
        for line in proc.stdout:           # type: ignore[union-attr]
            out_lines.append(line)
            got_frame.set()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        # Wait for the first heartbeat frame (the sim emits at 10 Hz) rather than sleeping a
        # fixed 1.5s. That sleep had to cover python startup plus the subscription, not just
        # the 100ms frame interval, and a loaded runner can spend longer than it on startup
        # alone and see nothing at all.
        assert got_frame.wait(20.0), "no CAN frame arrived from the 10 Hz heartbeat"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        reader.join(timeout=2)
    lines = [json.loads(line) for line in out_lines if line.strip()]
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


def test_plot_export_json_wraps_the_csv(make_stack: Callable[..., Stack]) -> None:
    # SPEC 4: with --json a command prints exactly one JSON object and no prose. This one
    # used to print raw CSV to stdout, which no --json consumer can parse.
    stack = make_stack(["--plot"])
    _wait_plot_names(stack, {"tri"})
    r = run_mcu(stack, "plot", "export", "--names", "tri", "--json")
    assert r.returncode == 0, r.stderr
    obj = json.loads(r.stdout)             # exactly one object, or this raises
    assert obj["names"] == "tri" and obj["format"] == "long"
    assert obj["csv"].startswith("ts,")
    assert obj["rows"] == max(obj["csv"].count("\n") - 1, 0)


def test_plot_export_json_to_file_reports_the_file(make_stack: Callable[..., Stack], tmp_path):
    # With -o the CSV goes to the file, but --json used to print nothing at all.
    stack = make_stack(["--plot"])
    _wait_plot_names(stack, {"tri"})
    dest = tmp_path / "plot.csv"
    r = run_mcu(stack, "plot", "export", "--names", "tri", "--json", "-o", str(dest))
    assert r.returncode == 0, r.stderr
    obj = json.loads(r.stdout)
    assert obj["file"] == str(dest)
    assert obj["bytes"] == dest.stat().st_size > 0
    assert obj["rows"] == len(dest.read_text(encoding="utf-8").splitlines()) - 1


def test_plot_export_unwritable_path_exit1(make_stack: Callable[..., Stack], tmp_path) -> None:
    stack = make_stack(["--plot"])
    _wait_plot_names(stack, {"tri"})
    bad = tmp_path / "no-such-dir" / "out.csv"
    r = run_mcu(stack, "plot", "export", "--names", "tri", "-o", str(bad))
    assert r.returncode == 1
    assert "cannot write" in r.stderr
    assert "Traceback" not in r.stderr


def test_plot_export_streams_the_response(monkeypatch, capsys) -> None:
    """The CSV body is consumed chunk by chunk, never materialised whole.

    `/plot/export` is the one endpoint that can answer with a very large body; it used to
    be read via `resp.text`. Both unstreamed doors are nailed shut here: `httpx.request`
    and `Response.text`.
    """
    from mcuscope import cli

    chunks = ["ts,tick_ms,sid,name,value\n", "1.0,10,0,tri,1.5\n", "1.1,20,0,tri,2.5\n"]

    class _Resp:
        status_code = 200

        def iter_text(self):
            yield from chunks

        @property
        def text(self):
            raise AssertionError("body was read whole instead of streamed")

    class _Stream:
        def __enter__(self):
            return _Resp()

        def __exit__(self, *exc):
            return False

    def _no_request(*a, **kw):
        raise AssertionError("plot export used an unstreamed request")

    monkeypatch.setattr(cli.httpx, "stream", lambda *a, **kw: _Stream())
    monkeypatch.setattr(cli.httpx, "request", _no_request)
    rc = cli.main(["plot", "export", "--names", "tri", "--url", "http://127.0.0.1:1"])
    assert rc == 0
    assert capsys.readouterr().out == "".join(chunks)


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


_PIDDIR_ENV_SKIP = pytest.mark.skipif(
    os.name == "nt",
    reason="platformdirs resolves the Windows data dir via the shell API, not env vars",
)


def _spawn_env(data_home: str, url: str) -> dict[str, str]:
    env = os.environ.copy()
    env["XDG_DATA_HOME"] = data_home
    env["MCUSCOPE_URL"] = url
    return env


def _daemon_config(tmp_path, name: str) -> str:
    cfg = tmp_path / f"{name}.toml"
    cfg.write_text(
        f'[storage]\ndb_path = "{(tmp_path / (name + ".db")).as_posix()}"\n', encoding="utf-8"
    )
    return str(cfg)


def _answers(url: str) -> bool:
    try:
        return httpx.get(url + "/status", timeout=1.0).status_code == 200
    except httpx.HTTPError:
        return False


@_PIDDIR_ENV_SKIP
def test_daemon_start_timeout_does_not_orphan_the_child(tmp_path) -> None:
    """A readiness timeout must not leave a daemon running that nothing can stop.

    The old code deleted the pid file and returned, so a merely slow daemon came up a few
    seconds later with `daemon status` reporting it running and `daemon stop` answering
    "no pid file". --timeout 0.05 makes the race certain to be lost: nothing starts a
    uvicorn app in 50ms, and daemon_start honours the value exactly. It did not always
    lose while a 0.5s floor sat in daemon_start, which an idle machine could beat.
    """
    from tests.support import free_port

    data_home = str(tmp_path / "data")
    url = f"http://127.0.0.1:{free_port()}"
    r = subprocess.run(
        [*MCU, "daemon", "start", "-c", _daemon_config(tmp_path, "orphan"), "--timeout", "0.05"],
        capture_output=True, text=True, timeout=60, env=_spawn_env(data_home, url),
    )
    assert r.returncode == 1
    assert "did not come up" in r.stderr
    assert "Traceback" not in r.stderr
    # Whatever was spawned is gone, and stays gone: nothing answers at that URL.
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        assert not _answers(url), f"orphaned daemon still running at {url}: {r.stderr}"
        time.sleep(0.25)
    pid_dir = os.path.join(data_home, "mcuscope")
    left = [f for f in os.listdir(pid_dir) if f.endswith(".pid")] if os.path.isdir(pid_dir) else []
    assert left == []   # the child was stopped, so its pid record is gone with it


@_PIDDIR_ENV_SKIP
def test_daemon_start_pid_file_is_keyed_by_host_port(tmp_path) -> None:
    """One shared pid file meant a second daemon clobbered the first one's record.

    Spawns a real daemon, then checks that `daemon stop` aimed at a different URL does not
    claim it, and that the correctly aimed one does.
    """
    from tests.support import free_port

    data_home = str(tmp_path / "data")
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    other = f"http://127.0.0.1:{free_port()}"
    started = subprocess.run(
        [*MCU, "daemon", "start", "-c", _daemon_config(tmp_path, "keyed")],
        capture_output=True, text=True, timeout=90, env=_spawn_env(data_home, url),
    )
    try:
        assert started.returncode == 0, started.stderr
        pid_files = sorted(os.listdir(os.path.join(data_home, "mcuscope")))
        assert f"mcuscoped-127.0.0.1-{port}.pid" in pid_files

        # A stop aimed elsewhere must not correlate this daemon's pid with that URL.
        miss = subprocess.run(
            [*MCU, "daemon", "stop"], capture_output=True, text=True, timeout=30,
            env=_spawn_env(data_home, other),
        )
        assert miss.returncode == 1
        assert "no pid file" in miss.stderr
        assert _answers(url), "a stop aimed at another URL killed this daemon"
    finally:
        stopped = subprocess.run(
            [*MCU, "daemon", "stop"], capture_output=True, text=True, timeout=30,
            env=_spawn_env(data_home, url),
        )
    assert stopped.returncode == 0, stopped.stderr
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and _answers(url):
        time.sleep(0.2)
    assert not _answers(url)


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


def test_session_list_shows_the_date(stack: Stack) -> None:
    # Time of day alone made yesterday's run and today's indistinguishable in a listing.
    run_mcu(stack, "session", "start", "dated-run")
    r = run_mcu(stack, "session", "list")
    assert r.returncode == 0
    row = next(line for line in r.stdout.splitlines() if "dated-run" in line)
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", row), row
    run_mcu(stack, "session", "stop")


def test_session_stop_without_one_exits_1(stack: Stack) -> None:
    run_mcu(stack, "session", "stop")          # ensure nothing is running
    r = run_mcu(stack, "session", "stop")
    assert r.returncode == 1
    assert "no session" in r.stderr


def test_session_export_writes_a_capture_file(stack: Stack, tmp_path) -> None:
    run_mcu(stack, "session", "start", "archive-me")
    run_mcu(stack, "mark", "inside the archived run")
    run_mcu(stack, "session", "stop")

    out = tmp_path / "run.db"
    r = run_mcu(stack, "session", "export", "archive-me", "-o", str(out))
    assert r.returncode == 0, r.stderr
    assert out.stat().st_size > 0

    conn = sqlite3.connect(str(out))
    raws = [row[0] for row in conn.execute("SELECT raw FROM lines")]
    conn.close()
    assert "inside the archived run" in raws


def test_session_delete_with_data(stack: Stack) -> None:
    run_mcu(stack, "session", "start", "junk-run")
    run_mcu(stack, "mark", "junk payload")
    run_mcu(stack, "session", "stop")

    r = run_mcu(stack, "session", "delete", "junk-run", "--data", "--yes")
    assert r.returncode == 0, r.stderr
    left = run_mcu(stack, "lines", "--limit", "500", "--json")
    assert "junk payload" not in [x["raw"] for x in json.loads(left.stdout)["lines"]]


# -- assert ---------------------------------------------------------------------------


def test_assert_retrospective_pass_and_fail(stack: Stack) -> None:
    run_mcu(stack, "session", "start", "verdict-run")
    run_mcu(stack, "mark", "BOOT OK")
    run_mcu(stack, "mark", "CALIB DONE")
    run_mcu(stack, "session", "stop")

    ok = run_mcu(stack, "assert", "--session", "verdict-run",
                 "--expect", "BOOT OK", "--forbid", "ERR")
    assert ok.returncode == 0, ok.stderr
    assert "PASS" in ok.stdout

    bad = run_mcu(stack, "assert", "--session", "verdict-run", "--expect", "NEVER PRINTED")
    assert bad.returncode == 1
    assert "FAILED" in bad.stderr


def test_assert_json_verdict(stack: Stack) -> None:
    run_mcu(stack, "mark", "READY 1")
    r = run_mcu(stack, "assert", "--expect", "READY 1", "--last-ms", "60000", "--json")
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout)
    assert body["status"] == "pass"
    assert body["expect"][0]["line"]["raw"] == "READY 1"


def test_assert_live_window_with_send(stack: Stack) -> None:
    # The live form: send something, then judge what comes back within the window.
    r = run_mcu(stack, "assert", "--send", "ping", "--expect", "monitor", "--timeout", "3000")
    assert r.returncode == 0, r.stderr + r.stdout

    miss = run_mcu(stack, "assert", "--expect", "NOTHING EMITS THIS", "--timeout", "600")
    assert miss.returncode == 1


def test_assert_needs_a_pattern(stack: Stack) -> None:
    r = run_mcu(stack, "assert")
    assert r.returncode == 1
    assert "expect" in r.stderr


# -- purge ----------------------------------------------------------------------------


def test_purge_dry_run_then_delete(stack: Stack) -> None:
    run_mcu(stack, "session", "start", "purge-me")
    run_mcu(stack, "mark", "delete this line")
    run_mcu(stack, "session", "stop")

    dry = run_mcu(stack, "purge", "--session", "purge-me", "--dry-run")
    assert dry.returncode == 0
    assert "would delete" in dry.stdout
    still = run_mcu(stack, "lines", "--limit", "500", "--json")
    assert "delete this line" in [x["raw"] for x in json.loads(still.stdout)["lines"]]

    done = run_mcu(stack, "purge", "--session", "purge-me", "--yes")
    assert done.returncode == 0, done.stderr
    gone = run_mcu(stack, "lines", "--limit", "500", "--json")
    assert "delete this line" not in [x["raw"] for x in json.loads(gone.stdout)["lines"]]


def test_purge_requires_one_selector(stack: Stack) -> None:
    r = run_mcu(stack, "purge", "--yes")
    assert r.returncode == 1
    assert "exactly one" in r.stderr


@pytest.mark.parametrize("answer", ["n\n", ""])   # declined, and stdin closed
def test_purge_without_yes_asks_and_deletes_nothing_when_refused(
    stack: Stack, answer: str
) -> None:
    # Declining a destructive prompt is a normal outcome: it must print a plain message
    # (typer's own abort path renders a traceback at whoever answered "n") and leave the
    # capture alone, with a non-zero exit so a script never reads "cancelled" as "done".
    run_mcu(stack, "mark", "must survive a refused purge")
    r = run_mcu(stack, "purge", "--all", stdin=answer)
    assert r.returncode == 1
    assert "cancelled" in r.stderr
    assert "Traceback" not in r.stderr and "Abort" not in r.stderr
    left = run_mcu(stack, "lines", "--limit", "500", "--json")
    assert "must survive a refused purge" in [x["raw"] for x in json.loads(left.stdout)["lines"]]


def test_purge_prompt_never_lands_on_stdout(stack: Stack) -> None:
    # The confirmation is a message to a human. On stdout it is a prose fragment in the
    # middle of a --json consumer's parse, so it goes to stderr and stdout carries only
    # the one JSON object SPEC 4 promises.
    run_mcu(stack, "mark", "prompt-routing")
    r = run_mcu(stack, "--json", "purge", "--all", stdin="n\n")
    assert r.returncode == 1
    assert "delete" in r.stderr and "[y/N]" in r.stderr
    obj = json.loads(r.stdout)
    assert obj == {"error": "cancelled", "exit_code": 1}


def test_session_delete_prompt_never_lands_on_stdout(stack: Stack) -> None:
    run_mcu(stack, "session", "start", "prompt-run")
    run_mcu(stack, "mark", "prompt payload")
    run_mcu(stack, "session", "stop")
    r = run_mcu(stack, "--json", "session", "delete", "prompt-run", "--data", stdin="n\n")
    assert r.returncode == 1
    assert "[y/N]" in r.stderr
    assert json.loads(r.stdout) == {"error": "cancelled", "exit_code": 1}
    # Declining left the session alone.
    names = [s["name"] for s in json.loads(run_mcu(stack, "--json", "session", "list").stdout)[
        "sessions"
    ]]
    assert "prompt-run" in names


# -- ports / attach / detach ----------------------------------------------------------


def test_ports_lists_the_attached_port(stack: Stack) -> None:
    r = run_mcu(stack, "ports")
    assert r.returncode == 0
    assert stack.alias in r.stdout and "connected" in r.stdout

    obj = json.loads(run_mcu(stack, "--json", "ports").stdout)
    assert [pt["alias"] for pt in obj["ports"]] == [stack.alias]


def test_attach_then_detach_round_trip(stack: Stack) -> None:
    # A socket:// URL with nothing listening: attach records the port and hands it to the
    # reconnect loop rather than failing, so the round trip needs no second simulator.
    from tests.support import free_port

    device = f"socket://127.0.0.1:{free_port()}"
    att = run_mcu(stack, "attach", device, "--alias", "spare")
    assert att.returncode == 0
    assert "attached spare" in att.stdout
    assert "spare" in run_mcu(stack, "ports").stdout

    det = run_mcu(stack, "detach", "spare")
    assert det.returncode == 0
    assert "detached spare" in det.stdout
    assert "spare" not in run_mcu(stack, "ports").stdout


def test_attach_derives_an_alias_and_detach_of_nothing_exits_1(stack: Stack) -> None:
    from tests.support import free_port

    obj = json.loads(
        run_mcu(stack, "--json", "attach", f"socket://127.0.0.1:{free_port()}").stdout
    )
    assert obj["port"]["alias"] == "board"   # _derive_alias default for a URL device

    r = run_mcu(stack, "detach", "never-attached")
    assert r.returncode == 1


# -- send / mark ----------------------------------------------------------------------


def test_send_writes_a_raw_line_into_the_capture(stack: Stack) -> None:
    r = run_mcu(stack, "send", ">9001 ping")
    assert r.returncode == 0
    assert r.stdout.strip() == "ok"

    rows = json.loads(run_mcu(stack, "lines", "--limit", "500", "--json").stdout)["lines"]
    sent = [x for x in rows if x["raw"] == ">9001 ping"]
    assert sent and sent[0]["dir"] == "tx"


def test_mark_reports_its_line_id(stack: Stack) -> None:
    r = run_mcu(stack, "mark", "phase two begins")
    assert r.returncode == 0
    assert r.stdout.startswith("marker ")

    obj = json.loads(run_mcu(stack, "--json", "mark", "and again").stdout)
    rows = json.loads(run_mcu(stack, "lines", "--limit", "500", "--json").stdout)["lines"]
    hit = [x for x in rows if x["id"] == obj["line_id"]]
    assert hit and hit[0]["chan"] == "marker" and hit[0]["raw"] == "and again"


# -- tail (including -f) --------------------------------------------------------------


def follow_mcu(
    stack: Stack, *args: str, expect: str, poke: Callable[[], None] | None = None,
    timeout: float = 20.0,
) -> list[str]:
    """Run a never-terminating `mcu` follow command until `expect` appears, then stop it.

    A reader thread drains stdout so the child cannot block on a full pipe, and `poke` runs
    once the stream is open, which is what makes the expected line arrive after (not
    before) the follow started.
    """
    env = os.environ.copy()
    env["MCUSCOPE_URL"] = stack.base_url
    proc = subprocess.Popen(
        [*MCU, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    seen: list[str] = []
    found = threading.Event()

    def drain() -> None:
        for line in proc.stdout:           # type: ignore[union-attr]
            seen.append(line.rstrip("\n"))
            if expect in line:
                found.set()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        if poke is not None:
            # Poke until the follower reports it, rather than once after a fixed 0.5s guess
            # at how long `mcu ... -f` needs to start python and subscribe. That guess holds
            # on an idle machine and silently loses the line on a loaded CI runner, where the
            # single poke lands before the subscription exists. Every poke is a `mcu mark`,
            # so repeating it only adds another marker line.
            deadline = time.monotonic() + timeout
            while not found.is_set() and time.monotonic() < deadline:
                poke()
                found.wait(0.5)
            assert found.is_set(), f"{expect!r} never appeared; saw {seen}"
        else:
            assert found.wait(timeout), f"{expect!r} never appeared; saw {seen}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        reader.join(timeout=2)
    return seen


def test_tail_prints_recent_lines_oldest_first(stack: Stack) -> None:
    run_mcu(stack, "mark", "tail-marker-one")
    run_mcu(stack, "mark", "tail-marker-two")
    r = run_mcu(stack, "tail", "-n", "200", "--chan", "marker")
    assert r.returncode == 0
    assert r.stdout.index("tail-marker-one") < r.stdout.index("tail-marker-two")


def test_tail_json_emits_one_object_per_line(stack: Stack) -> None:
    run_mcu(stack, "mark", "tail-json-marker")
    r = run_mcu(stack, "--json", "tail", "-n", "5", "--chan", "marker")
    rows = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
    assert rows and all(row["chan"] == "marker" for row in rows)


def test_tail_follow_streams_new_lines(stack: Stack) -> None:
    seen = follow_mcu(
        stack, "tail", "-n", "1", "-f", "--chan", "marker",
        expect="follow-me-now",
        poke=lambda: run_mcu(stack, "mark", "follow-me-now"),
    )
    assert any("follow-me-now" in line for line in seen)


def test_tail_follow_json_streams_objects(stack: Stack) -> None:
    seen = follow_mcu(
        stack, "--json", "tail", "-n", "1", "-f", "--match", "follow-json-marker",
        expect="follow-json-marker",
        poke=lambda: run_mcu(stack, "mark", "follow-json-marker"),
    )
    rows = [json.loads(line) for line in seen if line.startswith("{")]
    assert any(row["raw"] == "follow-json-marker" for row in rows)


# -- log export -----------------------------------------------------------------------


def test_log_export_to_stdout_and_file(stack: Stack, tmp_path) -> None:
    run_mcu(stack, "mark", "export-me-please")

    to_stdout = run_mcu(stack, "log", "export", "--chan", "marker", "--limit", "500")
    assert to_stdout.returncode == 0
    assert "export-me-please" in to_stdout.stdout

    dest = tmp_path / "capture.log"
    to_file = run_mcu(
        stack, "log", "export", "--chan", "marker", "--limit", "500", "-o", str(dest)
    )
    assert to_file.returncode == 0
    assert "wrote" in to_file.stdout
    assert "export-me-please" in dest.read_text(encoding="utf-8")


def test_log_export_json_is_jsonl(stack: Stack, tmp_path) -> None:
    run_mcu(stack, "mark", "jsonl-export-marker")
    dest = tmp_path / "capture.jsonl"
    run_mcu(
        stack, "--json", "log", "export", "--chan", "marker", "--limit", "500", "-o", str(dest)
    )
    rows = [json.loads(line) for line in dest.read_text(encoding="utf-8").splitlines() if line]
    assert any(row["raw"] == "jsonl-export-marker" for row in rows)


def test_log_export_json_to_file_emits_one_object(stack: Stack, tmp_path) -> None:
    # -o with --json used to print 0 bytes, so a consumer parsing stdout got nothing.
    run_mcu(stack, "mark", "json-result-object")
    dest = tmp_path / "capture.jsonl"
    r = run_mcu(
        stack, "--json", "log", "export", "--chan", "marker", "--limit", "500", "-o", str(dest)
    )
    assert r.returncode == 0, r.stderr
    obj = json.loads(r.stdout)
    assert obj["file"] == str(dest)
    assert obj["lines"] == len(dest.read_text(encoding="utf-8").splitlines())
    assert obj["bytes"] == dest.stat().st_size


def test_log_export_unwritable_path_exit1(stack: Stack, tmp_path) -> None:
    bad = tmp_path / "no-such-dir" / "capture.log"
    r = run_mcu(stack, "log", "export", "--limit", "5", "-o", str(bad))
    assert r.returncode == 1
    assert "cannot write" in r.stderr
    assert "Traceback" not in r.stderr


def test_log_export_match_narrows_the_dump(stack: Stack) -> None:
    run_mcu(stack, "mark", "keep-this-one")
    run_mcu(stack, "mark", "drop-that-one")
    r = run_mcu(stack, "log", "export", "--match", "keep-this-one", "--limit", "500")
    assert "keep-this-one" in r.stdout and "drop-that-one" not in r.stdout


# -- bus sugar: can / spi / gpio / adc ------------------------------------------------


def test_can_tx_and_echo(stack: Stack) -> None:
    # The simulator echoes a transmitted frame back with id+1 after 20 ms (SPEC 7), so a
    # successful tx is observable in the decoded CAN view rather than only in the ok.
    tx = run_mcu(stack, "can", "tx", "123", "DEADBEEF")
    assert tx.returncode == 0

    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        dump = run_mcu(stack, "--json", "can", "dump", "--id", "124", "-n", "5")
        frames = [json.loads(line) for line in dump.stdout.splitlines() if line.strip()]
        if any(fr["data_hex"].upper() == "DEADBEEF" for fr in frames):
            return
        time.sleep(0.2)
    raise AssertionError("echoed frame 0x124 never arrived")


def test_can_tx_rtr_and_ext_flags(stack: Stack) -> None:
    assert run_mcu(stack, "can", "tx", "1ABCDEF", "--ext", "11").returncode == 0
    assert run_mcu(stack, "can", "tx", "200", "--rtr", "4").returncode == 0


def test_can_stat_counts_the_transmit(stack: Stack) -> None:
    before = run_mcu(stack, "can", "stat").stdout
    assert "tx=" in before and "state=" in before
    run_mcu(stack, "can", "tx", "321", "01")
    after = run_mcu(stack, "can", "stat").stdout

    def tx_of(text: str) -> int:
        return int(dict(tok.split("=", 1) for tok in text.split() if "=" in tok)["tx"])

    assert tx_of(after) == tx_of(before) + 1


def test_can_filter_variants(stack: Stack) -> None:
    for args in (["all"], ["none"], ["100", "700"]):
        r = run_mcu(stack, "can", "filter", *args)
        assert r.returncode == 0, r.stderr
    run_mcu(stack, "can", "filter", "all")     # leave the sim receiving again

    bad = run_mcu(stack, "can", "filter")
    assert bad.returncode == 1
    assert "badarg" in bad.stderr


def test_spi_xfer_echoes_inverted(stack: Stack) -> None:
    r = run_mcu(stack, "spi", "xfer", "imu", "AABB")
    assert r.returncode == 0
    assert r.stdout.strip().upper() == "5544"     # the sim inverts each byte (SPEC 7)

    bad = run_mcu(stack, "spi", "xfer", "nosuchcs", "AA")
    assert bad.returncode == 1
    assert "badarg" in bad.stderr


def test_gpio_set_then_get(stack: Stack) -> None:
    assert run_mcu(stack, "gpio", "set", "led", "1").returncode == 0
    assert run_mcu(stack, "gpio", "get", "led").stdout.strip() == "1"
    assert run_mcu(stack, "gpio", "set", "led", "0").returncode == 0
    assert run_mcu(stack, "gpio", "get", "led").stdout.strip() == "0"

    bad = run_mcu(stack, "gpio", "set", "led", "2")
    assert bad.returncode == 1


def test_adc_read(stack: Stack) -> None:
    r = run_mcu(stack, "adc", "read", "vbat")
    assert r.returncode == 0
    fields = dict(tok.split("=", 1) for tok in r.stdout.split() if "=" in tok)
    assert int(fields["raw"]) > 0 and 3000 < int(fields["mv"]) < 3600

    obj = json.loads(run_mcu(stack, "--json", "adc", "read", "vbat").stdout)
    assert obj["status"] == "ok" and obj["data"].startswith("raw=")

    bad = run_mcu(stack, "adc", "read", "nosuchchannel")
    assert bad.returncode == 1
    assert "badarg" in bad.stderr
