"""Tests for mcuscoped's pre-startup checks and the in-process simulator.

Both cover failures invisible from inside the daemon: an address conflict the probe missed
and reported only from inside uvicorn.run(), after the pid claim; and the demo simulator,
which used to be reached over a loopback listener and is now a link.
"""

from __future__ import annotations

import asyncio
import socket
import threading

import httpx
import pytest

from mcuscope import daemon as daemon_mod
from mcuscope.config import Config
from mcuscope.server import create_app
from tests.support import free_port


def _ipv6_loopback_available() -> bool:
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    except OSError:
        return False
    try:
        s.bind(("::1", 0))
    except OSError:
        return False
    finally:
        s.close()
    return True


def test_port_conflict_probes_every_resolved_address(monkeypatch) -> None:
    """uvicorn binds every address a host name resolves to, so probing only the first
    let a conflict on a later one through into the mid-startup failure - after
    pidfile.claim() - that the probe exists to prevent."""
    if not _ipv6_loopback_available():
        pytest.skip("no IPv6 loopback here, so a two-address host cannot be simulated")
    port = free_port()
    # What a dual-stack `--host <name>` resolves to: IPv6 first, IPv4 second.
    infos = [
        (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("::1", port, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: infos)

    busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        busy.bind(("127.0.0.1", port))
        busy.listen(1)

        msg = daemon_mod._port_conflict("dual-stack.example", port)
        assert msg is not None and "already in use" in msg
    finally:
        busy.close()

    # And with nothing bound on either address the probe stays silent.
    assert daemon_mod._port_conflict("dual-stack.example", port) is None


async def test_the_sim_demo_binds_nothing_and_still_captures(tmp_path) -> None:
    """`--sim` reaches the simulator through a link, not a loopback socket.

    It used to open an ephemeral listener and connect to itself, which is where the
    healthy-while-dead failure came from: a listener left bound with no thread behind it
    keeps completing handshakes, so the daemon reconnects to a corpse and reports the port
    healthy. There is nothing to bind now, so that failure mode is gone rather than
    guarded - `spawn()` keeps its own test of the invariant, for standalone `mcu-sim`.
    """
    config = Config()
    config.storage.db_path = str(tmp_path / "capture.db")
    open_link_fn = daemon_mod._start_sim(config)

    sim_port = next(pc for pc in config.ports if pc.alias == "sim")
    assert sim_port.device == "sim://demo", "the demo went back to a socket"
    assert not any(t.name == "mcu-sim" for t in threading.enumerate()), \
        "the demo started a serving thread"

    app = create_app(config, open_link_fn=open_link_fn)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        for _ in range(200):
            port = next(
                p for p in (await client.get("/status")).json()["ports"] if p["alias"] == "sim"
            )
            if port["connected"] and port["lines_rx"]:
                break
            await asyncio.sleep(0.02)
        assert port["connected"] and port["lines_rx"], "the demo port never captured"
