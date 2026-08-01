"""In-process test harness: a simulator + daemon stack on ephemeral ports.

Both the simulator (TCP transport) and the daemon (uvicorn) run in background threads
inside the test process, so the whole stack is exercised over real sockets and real
pyserial `socket://` connections, cross-platform. No subprocesses are needed here; the
phase 3 CLI tests drive the installed `mcu` binary against this same live daemon.
"""

from __future__ import annotations

import os
import shutil
import socket
import tempfile
import threading
import time

import httpx
import mcu_sim
import uvicorn

from mcuscope.config import Config, PortConfig, ServerConfig, StorageConfig
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


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Stack:
    """A running sim + daemon pair. Use `.base_url` for HTTP, `.close()` to tear down."""

    def __init__(self, sim_args: list[str] | None = None, alias: str = "board") -> None:
        self.alias = alias
        # --- simulator (TCP) ---
        self._sim_stop = threading.Event()
        self._sim_sock = mcu_sim.open_tcp_listener(0)
        self.sim_port = self._sim_sock.getsockname()[1]
        self._sim_args = mcu_sim.build_parser().parse_args(sim_args or [])
        self._sim_thread = threading.Thread(
            target=mcu_sim.serve_listener,
            args=(self._sim_args, self._sim_sock, self._sim_stop),
            daemon=True,
        )
        self._sim_thread.start()

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
                    device=f"socket://127.0.0.1:{self.sim_port}",
                    baud=115200,
                    autoconnect=True,
                )
            ],
        )
        app = create_app(config)
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
        self._sim_stop.set()
        self._sim_sock.close()
        self._sim_thread.join(timeout=2.0)

    def restart_sim(self) -> None:
        """Bring the simulator back on the same port so the daemon can reconnect."""
        self._sim_stop = threading.Event()
        self._sim_sock = mcu_sim.open_tcp_listener(self.sim_port)
        self._sim_thread = threading.Thread(
            target=mcu_sim.serve_listener,
            args=(self._sim_args, self._sim_sock, self._sim_stop),
            daemon=True,
        )
        self._sim_thread.start()

    def close(self) -> None:
        self._server.should_exit = True
        self._server_thread.join(timeout=8.0)
        self._sim_stop.set()
        try:
            self._sim_sock.close()
        except OSError:
            pass
        shutil.rmtree(self._tmpdir, ignore_errors=True)
