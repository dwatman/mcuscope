"""The TCP transport, deliberately: the listener, and one whole-stack run over socket://.

Encodes the phase 1 acceptance - send `>1 ping` over a TCP connection to the sim and get
back `<1 OK monitor 1 sim` - and, since the harness moved to an in-process link, the only
end-to-end exercise of pyserial's `socket://` handler and `SerialLink`'s socket drain.
Both remain production paths for a user attaching a remote port, so they keep a test that
uses them for real rather than a stand-in. Runs on both Linux and Windows.
"""

from __future__ import annotations

import asyncio
import socket
import time

import httpx
import mcu_sim
import pytest

from mcuscope.config import Config, PortConfig, ServerConfig, StorageConfig
from mcuscope.link import SerialLink, validate_device
from mcuscope.server import create_app


def _read_line_matching(conn: socket.socket, prefix: str, timeout: float = 2.0) -> str:
    """Read newline-terminated lines until one starts with `prefix`, or time out."""
    conn.settimeout(timeout)
    buf = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = conn.recv(4096)
        except TimeoutError:
            break
        if not chunk:
            break
        buf.extend(chunk)
        while b"\n" in buf:
            raw, _, remainder = buf.partition(b"\n")
            del buf[:]
            buf.extend(remainder)
            line = raw.decode("ascii", "replace")
            if line.startswith(prefix):
                return line
    raise AssertionError(f"no line starting with {prefix!r} received")


def test_tcp_ping_round_trip() -> None:
    sim = mcu_sim.spawn()
    port = sim.port
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as conn:
            conn.sendall(b">1 ping\n")
            assert _read_line_matching(conn, "<1 ") == "<1 OK monitor 1 sim"
            # A second command on the same connection also works.
            conn.sendall(b">2 i2c scan\n")
            assert _read_line_matching(conn, "<2 ") == "<2 OK 48 50"
    finally:
        sim.stop()


def test_tcp_reconnect_serves_next_client() -> None:
    # The listener serves one client at a time and accepts a fresh one after a drop,
    # which is what the phase 2 reconnect test relies on.
    sim = mcu_sim.spawn()
    port = sim.port
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as conn:
            conn.sendall(b">1 ping\n")
            assert _read_line_matching(conn, "<1 ") == "<1 OK monitor 1 sim"
        # First connection closed; a new one must be accepted and served.
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as conn2:
            conn2.sendall(b">1 ping\n")
            assert _read_line_matching(conn2, "<1 ") == "<1 OK monitor 1 sim"
    finally:
        sim.stop()


def test_spawn_closes_the_listener_when_its_serving_thread_ends() -> None:
    """A bound listener with no thread behind it is worse than a closed one.

    The kernel keeps completing handshakes out of the backlog, so a client connects, sees
    a healthy port and never exchanges a byte. Only the daemon's copy of the embedding
    dance closed the socket in a finally; the harness the reconnect test runs on did not.
    Reaches for the stop event directly to model the thread ending on its own, which is
    the case the finally exists for - `stop()` would close the socket itself.
    """
    sim = mcu_sim.spawn()
    port = sim.port
    sim._stop.set()
    sim._thread.join(timeout=3.0)
    assert not sim._thread.is_alive(), "the serving thread did not end"
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1.0).close()


def test_spawn_stop_is_idempotent() -> None:
    """close() and stop_sim() can both run in one teardown."""
    sim = mcu_sim.spawn()
    sim.stop()
    sim.stop()


def test_spawn_reports_a_device_string_a_port_can_use() -> None:
    sim = mcu_sim.spawn()
    try:
        assert sim.device == f"socket://127.0.0.1:{sim.port}"
        validate_device(sim.device)      # and it survives the scheme allowlist
    finally:
        sim.stop()


# -- the socket:// link, end to end ------------------------------------------------------


async def test_a_port_captures_over_a_real_socket_connection(tmp_path) -> None:
    """The one whole-stack test still on TCP, and the reason it is here.

    Every other stack test reaches the simulator through `link.SourceLink`, in process.
    That leaves two production paths with no end-to-end cover: pyserial's `socket://` URL
    handler, and `SerialLink`'s socket drain branch - the one that sets `timeout = 0`
    because `in_waiting` on that handler is a 0/1 readability poll rather than a byte
    count, worth 0.2 MB/s against 600. Both are what a user attaching a remote port gets,
    so one test drives them for real: a spawned listener, pyserial, the reader thread, the
    store, and a command round trip.
    """
    sim = mcu_sim.spawn()
    try:
        config = Config(
            server=ServerConfig(host="127.0.0.1", port=0),
            storage=StorageConfig(db_path=str(tmp_path / "capture.db"), retention_days=7),
            ports=[PortConfig(alias="tcp", device=sim.device, baud=115200, autoconnect=True)],
        )
        app = create_app(config)     # the default opener: serial_for_url, no injection
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
        ):
            for _ in range(200):
                port = (await client.get("/status")).json()["ports"][0]
                if port["connected"] and port["lines_rx"]:
                    break
                await asyncio.sleep(0.02)
            assert port["connected"], "the port never connected over socket://"

            live = app.state.ports.get("tcp")._link
            assert isinstance(live, SerialLink), f"not the real transport: {type(live)}"
            assert live._socket_drain, "the socket drain branch was not selected"

            resp = await client.post("/cmd", json={"cmd": "ping", "timeout_ms": 4000})
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"] == "monitor 1 sim"
    finally:
        sim.stop()
