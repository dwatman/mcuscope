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
from mcuscope.config import Config, PortConfig
from mcuscope.lockfile import CaptureLock
from mcuscope.server import create_app
from tests.support import UNOPENABLE, free_port


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


def test_a_startup_failure_after_the_claim_leaves_no_pid_record(tmp_path, monkeypatch) -> None:
    """Everything after the pid claim runs inside the try, so a failure there still
    reaches the finally: a stranded record would have `mcu daemon stop` signal whatever
    process later recycles the pid, and a stranded lock would need clearing by hand."""
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app: str(tmp_path / "data"))
    monkeypatch.setattr(
        daemon_mod, "create_app", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(RuntimeError):
        daemon_mod.main(["-c", str(tmp_path / "absent.toml"), "--port", str(free_port())])

    assert not list((tmp_path / "data").glob("*.pid")), "a pid record outlived the daemon"
    released = CaptureLock(str(tmp_path / "data" / "capture.db"))
    released.acquire(timeout=0)   # raises LockError if the capture was left claimed
    released.release()


async def test_the_sim_demo_binds_nothing_and_still_captures(tmp_path) -> None:
    """`--sim` reaches the simulator through a link, not a loopback socket.

    It used to open an ephemeral listener and connect to itself, which is where the
    healthy-while-dead failure came from: a listener left bound with no thread behind it
    keeps completing handshakes, so the daemon reconnects to a corpse and reports the port
    healthy. There is nothing to bind now, so that failure mode is gone rather than
    guarded - `spawn()` keeps its own test of the invariant, for standalone `mcu-sim`.

    The configured board beside the demo port is the other half: the opener is handed to
    every port the daemon builds, and answering unconditionally served a real board's
    alias out of the simulator - fabricated data under a real name.
    """
    config = Config()
    config.storage.db_path = str(tmp_path / "capture.db")
    config.ports.append(PortConfig(alias="board", device=UNOPENABLE, autoconnect=True))
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
        board_ever_connected = False
        for _ in range(200):
            ports = {p["alias"]: p for p in (await client.get("/status")).json()["ports"]}
            port = ports["sim"]
            board_ever_connected |= ports["board"]["connected"]
            if port["connected"] and port["lines_rx"]:
                break
            await asyncio.sleep(0.02)
        assert port["connected"] and port["lines_rx"], "the demo port never captured"
        assert not board_ever_connected, "the configured board was served out of the simulator"
        assert ports["board"]["connected"] is False
        assert ports["board"]["lines_rx"] == 0, "the board's capture came from the simulator"


def test_a_lingering_time_wait_socket_is_not_a_port_conflict() -> None:
    """The probe must answer the same question uvicorn's bind will, not a stricter one.

    asyncio sets SO_REUSEADDR for uvicorn on POSIX, so uvicorn binds happily over the
    TIME-WAIT sockets a just-stopped daemon leaves behind. The probe did a plain bind, so for
    roughly a minute after a clean stop it refused to start and said "another mcuscoped or
    another service is listening there" while nothing was listening at all.
    """
    from mcuscope.daemon import _port_conflict

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = srv.accept()
    # Close the server side first, which is what leaves TIME-WAIT on the listening port.
    conn.close()
    srv.close()
    client.close()
    try:
        # The wildcard, which is what the daemon binds and what actually fails: a plain
        # bind of 0.0.0.0:P is refused while 127.0.0.1:P sits in TIME-WAIT, though binding
        # 127.0.0.1:P itself would have succeeded. The real report came from `--host 0.0.0.0`.
        assert _port_conflict("0.0.0.0", port) is None, (
            "a TIME-WAIT socket was reported as another process listening"
        )
    finally:
        client.close()


def test_a_live_listener_is_still_a_port_conflict() -> None:
    # The other half: SO_REUSEADDR must not blind the probe to a real one.
    from mcuscope.daemon import _port_conflict

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)
    try:
        msg = _port_conflict("127.0.0.1", port)
        assert msg is not None and "already in use" in msg
    finally:
        srv.close()
