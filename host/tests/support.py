"""In-process test harness: a simulator behind the port, and a daemon on a real HTTP port.

The daemon is uvicorn in a background thread, because the CLI suite drives the installed
`mcu` binary against it and that needs a socket. The *serial* side does not: the port opens
a `link.SourceLink` whose far end is the simulator core, so there is no listener, no
ephemeral serial port and no accept loop between the reader thread and the sim.

`test_sim_tcp.py` keeps the real listener under test, deliberately - see its header.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

import httpx
import serial
import uvicorn

from mcuscope import cli, cli_client
from mcuscope import daemon as daemon_mod
from mcuscope import sim as mcu_sim
from mcuscope.config import Config, PortConfig, ServerConfig, StorageConfig
from mcuscope.link import SourceLink, open_link
from mcuscope.server import create_app
from mcuscope.store import Store

# A device that can never be opened and never performs a network operation, for tests that
# exercise PortManager bookkeeping (carried counters, seq, attach failure) rather than any
# transport. `socket://127.0.0.1:1` used to serve this, and it is fast only where the kernel
# refuses the connection: Linux does, Windows drops the SYN, so every reader thread sat in a
# blocking connect and every detach paid the full 2 s join. At CARRIED_MAX + 20 = 276 cycles
# that hung the Windows CI job outright while the same test took 0.15 s on Linux. A name that
# resolves to no device fails presence-gating immediately on both platforms instead.
UNOPENABLE = "mcuscope-no-such-device"
UNOPENABLE_ALT = "mcuscope-no-such-device-2"

# Text-mode keywords for every subprocess a test reads output from. Never bare `text=True`:
# that decodes with the platform default, which on Windows is the ANSI code page, and one
# non-ASCII byte - a rich help box rule, a vendor string, a path - then raises
# UnicodeDecodeError inside the harness and fails the test for the encoding of its own
# reader. Every child here writes UTF-8 (cli/daemon widen stdout to it explicitly), and
# errors="replace" absorbs a sequence a closed pipe cut mid-character.
CHILD_TEXT = {"encoding": "utf-8", "errors": "replace"}

# Config and cache home for every child: conftest's monkeypatch does not reach a subprocess
# (class 33), and a child left on the user's dirs reads their update cache and config, or
# writes its crash log beside their capture. The XDG variables do that on Linux only, since
# platformdirs reads the Windows shell API; MCUSCOPE_*_DIR moves the child on both.
_CHILD_HOME = tempfile.TemporaryDirectory(prefix="mcuscope-child-home-")

# Every data dir child_env has handed out since the last test ended: conftest fails a test
# whose child left a crash log in one.
CHILD_DATA_DIRS: set[str] = set()


def child_env(data_home: str | None = None, **extra: str) -> dict[str, str]:
    """os.environ for a child whose data, config and cache dirs are not the user's.

    `data_home` is an XDG *home*, so the child's data dir is `<data_home>/mcuscope`,
    which is where the MCUSCOPE_DATA_DIR override points too.
    """
    env = os.environ.copy()
    env["XDG_DATA_HOME"] = data_home or os.path.join(_CHILD_HOME.name, "data")
    env["XDG_CONFIG_HOME"] = os.path.join(_CHILD_HOME.name, "config")
    env["XDG_CACHE_HOME"] = os.path.join(_CHILD_HOME.name, "cache")
    env["MCUSCOPE_DATA_DIR"] = os.path.join(env["XDG_DATA_HOME"], "mcuscope")
    env["MCUSCOPE_CONFIG_DIR"] = os.path.join(env["XDG_CONFIG_HOME"], "mcuscope")
    env["MCUSCOPE_CACHE_DIR"] = os.path.join(env["XDG_CACHE_HOME"], "mcuscope")
    env.update(extra)
    CHILD_DATA_DIRS.add(env["MCUSCOPE_DATA_DIR"])
    return env


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class SpyLink(SourceLink):
    """A SourceLink that records what the reader did to it, and can pretend to cancel.

    Only the teardown-order test needs any of this, and none of it belongs in the shipped
    class. `cancellable=True` models a native port: pyserial's URL handlers cannot cancel,
    so the default answers False like the transport SourceLink otherwise stands in for.
    """

    def __init__(self, *args, cancellable: bool = False, **kw) -> None:
        super().__init__(*args, **kw)
        self._cancellable = cancellable
        self.cancelled_reads = 0
        self.cancelled_writes = 0

    def cancel_read(self) -> bool:
        self.cancelled_reads += 1
        return self._cancellable

    def cancel_write(self) -> bool:
        self.cancelled_writes += 1
        return self._cancellable


class Scripted:
    """A source playing a fixed list, one entry per poll, for driving the reader loop.

    `bytes` is a burst (the first byte answers the read, the rest is what the drain
    appends), `b""` is a read timeout, an `Exception` is raised from the read, and a
    `BurstThenError` delivers bytes and then fails during the drain.

    What happens once the script runs out is `idle_after`: False reports the link gone,
    which sends the reader round to reopen; True keeps answering read timeouts, holding one
    connection open so a test can assert against it. Without the second mode a script simply
    replays on every reconnect and a test counting lines counts them repeatedly.

    `replies` answers a written command, for the few tests that need a round trip without a
    whole simulator behind the port.
    """

    def __init__(
        self,
        script=(),
        exhausted: threading.Event | None = None,
        idle_after: bool = False,
    ) -> None:
        self.script = list(script)
        self.exhausted = exhausted
        self.idle_after = idle_after
        self.fed: list[bytes] = []
        self.replies: dict[bytes, bytes] = {}

    def feed(self, data: bytes) -> bytes:
        # Class 27: gentler than the sim it stands in for. The sim buffers writes and parses
        # at the newline, so a command split across two writes still gets a reply; this
        # dispatches on the exact write() payload, so a chunked write would silently match
        # nothing and return no reply at all. Bounded by no test writing a command in
        # chunks - a test that needs to must give this double the same line assembly first.
        self.fed.append(data)
        return self.replies.get(data, b"")

    def poll(self) -> object:
        if not self.script:
            if self.exhausted is not None:
                self.exhausted.set()
            if self.idle_after:
                return b""      # a read timeout; SourceLink does the waiting
            raise serial.SerialException("script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class SimEndpoint:
    """The far end of the harness's serial port: a simulator per open, and an unplug switch.

    One simulator per link, matching `_serve_socket_client`, so a reconnect finds a far end
    that restarted clean. `stop()` is the listener going away: the live link's next read
    fails and no further open succeeds until `start()`.
    """

    def __init__(self, args) -> None:
        self.args = args
        self.up = True
        self.links: list[SourceLink] = []
        # Every byte written to any link this endpoint opened, in order. The smallest thing
        # that answers "what actually went on the wire?": the sim parses at the newline and
        # then discards the framing, so nothing downstream of feed() can tell `lf` from
        # `crlf` from `none`, which is exactly what the eol tests must assert.
        self.written: list[bytes] = []
        # Break durations in seconds, in order. A break leaves no bytes behind, so without
        # this a break that never reached the transport passes every test.
        self.breaks: list[float] = []

    def open(self, device: str, baud: int):
        # Only a sim device gets the simulator. Answering for every device made the suite's
        # dead-`socket://` ports - "attaches, never connects", asserted in the e2e and CLI
        # attach tests - connect to a simulator instead, so those tests stopped exercising
        # the path they document while still passing.
        if not device.startswith("sim://"):
            return open_link(device, baud)
        if not self.up:
            raise serial.SerialException("simulator is not listening")
        source = _Unpluggable(self.args, self)
        link = SourceLink(source, device=device, on_break=source.on_break)
        self.links.append(link)
        return link

    def stop(self) -> None:
        self.up = False

    def start(self) -> None:
        self.up = True


class _Unpluggable:
    """A SimSource that fails once the endpoint is down, the way a dropped socket does."""

    def __init__(self, args, endpoint: SimEndpoint) -> None:
        self._sim = mcu_sim.SimSource(args)
        self._endpoint = endpoint

    def feed(self, data: bytes) -> bytes:
        self._check()
        self._endpoint.written.append(data)
        return self._sim.feed(data)

    def poll(self) -> bytes:
        self._check()
        return self._sim.poll()

    def on_break(self, seconds: float) -> None:
        self._check()
        self._endpoint.breaks.append(seconds)

    def _check(self) -> None:
        if not self._endpoint.up:
            raise serial.SerialException("simulator went away")


class Stack:
    """A running sim + daemon pair. Use `.base_url` for HTTP, `.close()` to tear down."""

    def __init__(self, sim_args: list[str] | None = None, alias: str = "board") -> None:
        self.alias = alias
        self._sim_args = mcu_sim.build_parser().parse_args(sim_args or [])
        self.sim = SimEndpoint(self._sim_args)

        # --- daemon (uvicorn in a thread) ---
        self._tmpdir = tempfile.mkdtemp(prefix="mcuscope-test-")
        db_path = os.path.join(self._tmpdir, "capture.db")
        self.http_port = free_port()
        config = Config(
            server=ServerConfig(host="127.0.0.1", port=self.http_port),
            storage=StorageConfig(db_path=db_path, retention_days=7),
            ports=[
                PortConfig(
                    alias=alias,
                    device="sim://board",
                    baud=115200,
                    autoconnect=True,
                )
            ],
        )
        # An explicit config_path, or the config-write endpoints (and anything driving
        # them, e.g. `mcu pj on --save`) would land in the developer's real
        # platformdirs config file (registry class 33).
        self.config_path = os.path.join(self._tmpdir, "config.toml")
        app = create_app(config, config_path=self.config_path, open_link_fn=self.sim.open)
        self.app = app   # tests that must reach the live store/ports go through here
        uconfig = uvicorn.Config(
            app, host="127.0.0.1", port=self.http_port, log_level="warning"
        )
        self._server = daemon_mod.Server(uconfig)
        self._server_thread = threading.Thread(target=self._server.run, daemon=True)
        self._server_thread.start()
        self.base_url = f"http://127.0.0.1:{self.http_port}"
        self._wait_ready()

    def _wait_ready(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if getattr(self._server, "started", False):
                try:
                    r = httpx.get(f"{self.base_url}/status", timeout=1.0)
                    if r.status_code == 200:
                        ports = r.json()["ports"]
                        if ports and ports[0]["connected"]:
                            return
                except httpx.HTTPError:
                    pass
            time.sleep(0.02)
        raise RuntimeError("stack did not become ready (daemon/sim not connected)")

    def wait_connected(self, connected: bool, timeout: float = 8.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                ports = httpx.get(f"{self.base_url}/status", timeout=1.0).json()["ports"]
                if ports and ports[0]["connected"] == connected:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        return False

    def stop_sim(self) -> None:
        """Drop the simulator so the daemon sees its serial connection break."""
        self.sim.stop()

    def restart_sim(self) -> None:
        """Bring the simulator back so the daemon's next open succeeds."""
        self.sim.start()

    def close(self) -> None:
        self._server.should_exit = True
        self._server_thread.join(timeout=8.0)
        self.sim.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)


def stack_client(stack: Stack, follow: bool = False) -> httpx.Client:
    """An HTTP client on `stack`'s daemon."""
    return httpx.Client(base_url=stack.base_url, timeout=30.0, follow_redirects=follow)


def on_loop(target, coro, timeout: float = 10.0):
    """Run `coro` on the daemon loop of a TestClient or a Stack, and wait for its result."""
    return asyncio.run_coroutine_threadsafe(coro, target.app.state.ports._loop).result(timeout)


def mk_app(tmp_path, **storage):
    """A daemon app on a fresh capture `tmp_path / "cap.db"`, loopback-bound, with no ports."""
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db"), **storage),
    )
    return create_app(config, config_path=tmp_path / "config.toml")


# `cli.main` in process against a canned daemon. A refusal that must not reach the network
# runs against UNREACHABLE, where exit 1 proves the CLI judged the request itself.
DEAD = "http://127.0.0.1:1"
UNREACHABLE = ["--url", DEAD]

STATUS = {"version": "0.4.0", "uptime_s": 1.0, "db_path": "/tmp/x.db", "ports": []}


def canned(monkeypatch, handler):
    """Point every request this invocation makes at `handler`."""
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(cli.Client, "open", lambda self: httpx.Client(transport=transport))


def recorder(monkeypatch, status=None, **bodies):
    """Canned responses by path, recording every request. Returns the request list.

    `bodies` maps a path with its slashes as underscores (`lines_export`) to a JSON body
    or an (status_code, body) pair; anything unmatched answers `{}`.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/status":
            return httpx.Response(200, json=status or STATUS)
        body = bodies.get(request.url.path.strip("/").replace("/", "_"), {})
        if isinstance(body, tuple):
            return httpx.Response(body[0], json=body[1])
        if isinstance(body, str):
            return httpx.Response(200, text=body)
        return httpx.Response(200, json=body)

    canned(monkeypatch, handler)
    return seen


def paths(seen) -> list[str]:
    return [r.url.path for r in seen]


T0_FIXED = 1_700_000_000.0


def run_coro(coro_fn) -> None:
    asyncio.run(coro_fn())


async def started_store(path) -> Store:
    """A store whose age retention cannot reach T0: the startup sweep runs concurrently
    with the test's own writes, and a decade-old `ts` is expired by the default."""
    store = Store(str(path))
    await store.start(retention_days=36_500)
    return store


async def add_row(store: Store, ts: float, raw: str, port: str = "board") -> dict:
    return await store.add_line(ts=ts, port=port, dir="rx", chan="debug", seq=None, raw=raw)


def record_params(monkeypatch, handler) -> list:
    """Route every request through `handler`, recording (path, params) per request."""
    seen: list = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        return handler(request)

    monkeypatch.setattr(cli.Client, "open",
                        lambda self: httpx.Client(transport=httpx.MockTransport(wrapped)))
    return seen


async def add_row_p(store: Store, ts: float, raw: str, chan: str = "debug") -> dict:
    return await store.add_line(ts=ts, port="p", dir="rx", chan=chan, seq=None, raw=raw)


# -- store writer resilience -----------------------------------------------------------


class CommitBoom:
    """Connection proxy whose first commit() raises, like a disk-full error would."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._armed = True

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def commit(self) -> None:
        if self._armed:
            self._armed = False
            raise sqlite3.OperationalError("disk I/O error")
        self._conn.commit()


async def add_sys(store: Store, raw: str) -> dict:
    return await store.add_line(
        ts=time.time(), port="t", dir="-", chan="sys", seq=None, raw=raw
    )


def captured_plan(store: Store, run, keyword: str = "SELECT") -> list[str]:
    """EXPLAIN the statement the store actually issued, rather than a copy of it.

    A plan test that explains a hand-written query proves nothing about the daemon (this
    round's test-quality leg found exactly that shape), so the statement is taken off the
    connection's trace callback, which reports it with its parameters already substituted.

    Returned as the list of plan rows, outer loop first, because the useful assertion is
    "which table does the outer loop read" - and asserting that positively survives SQLite
    rewording its output. Asserting the *absence* of "SCAN l" would pass silently on a
    build that says "SCAN TABLE lines AS l" instead, which is how it read before 3.36.

    The LAST matching statement, not the first: a read carrying `last_ms` resolves its
    window bounds with anchor SELECTs first (`_window_floor`, `_window_id_floor`), and
    explaining one of those pins nothing about the query under test. `keyword` chooses the
    statement kind, so the retention sweep's DELETE can be pinned the same way.
    """
    seen: list[str] = []
    store._conn.set_trace_callback(seen.append)
    try:
        run()
    finally:
        store._conn.set_trace_callback(None)
    matching = [s for s in seen if s.lstrip().upper().startswith(keyword)]
    assert matching, f"the store issued no {keyword} statement: {seen}"
    return [str(r[3]) for r in store._conn.execute("EXPLAIN QUERY PLAN " + matching[-1])]


async def until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


def dead_pid() -> int:
    """The pid of a process that has certainly exited (and been reaped)."""
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    return proc.pid


# -- F12 (routed from batch C1): resolving a session name server-side -------------------


def make_sessions(stack: Stack, n: int) -> None:
    with stack_client(stack) as c:
        for i in range(n):
            assert c.post("/sessions", json={"name": f"s{i}"}).status_code == 200


# -- status mapping (class 70) ----------------------------------------------------------------


def record_requests(monkeypatch, handler) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def record(request):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(cli_client.Client, "open",
                        lambda self: httpx.Client(transport=httpx.MockTransport(record)))
    return seen
