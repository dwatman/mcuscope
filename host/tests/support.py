"""In-process test harness: a simulator behind the port, and a daemon on a real HTTP port.

The daemon is uvicorn in a background thread, because the CLI suite drives the installed
`mcu` binary against it and that needs a socket. The *serial* side does not: the port opens
a `link.SourceLink` whose far end is the simulator core, so there is no listener, no
ephemeral serial port and no accept loop between the reader thread and the sim.

`test_sim_tcp.py` keeps the real listener under test, deliberately - see its header.
"""

from __future__ import annotations

import os
import shutil
import socket
import tempfile
import threading
import time

import httpx
import serial
import uvicorn

from mcuscope import sim as mcu_sim
from mcuscope.config import Config, PortConfig, ServerConfig, StorageConfig
from mcuscope.link import SourceLink, open_link
from mcuscope.server import create_app

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

    def open(self, device: str, baud: int):
        # Only a sim device gets the simulator. Answering for every device made the suite's
        # dead-`socket://` ports - "attaches, never connects", asserted in the e2e and CLI
        # attach tests - connect to a simulator instead, so those tests stopped exercising
        # the path they document while still passing.
        if not device.startswith("sim://"):
            return open_link(device, baud)
        if not self.up:
            raise serial.SerialException("simulator is not listening")
        link = SourceLink(_Unpluggable(self.args, self), device=device)
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
        self._server = uvicorn.Server(uconfig)
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
