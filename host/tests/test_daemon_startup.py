"""Tests for mcuscoped's pre-startup checks and the in-process simulator.

Both cover failures that are invisible from inside the daemon: an address conflict the
probe missed and reported only from inside uvicorn.run() (after the pid claim), and a
sim listener left bound with no thread behind it, which answers connect() and nothing
else.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from mcuscope import daemon as daemon_mod
from mcuscope import sim as mcu_sim
from mcuscope.config import Config
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


def test_sim_listener_does_not_outlive_its_serving_thread(monkeypatch) -> None:
    """A listener left bound with no thread behind it keeps completing handshakes from
    the kernel backlog: the daemon reconnects, reports the port healthy, and exchanges
    nothing. The standalone serve_tcp() path always closed it; this one did not."""
    monkeypatch.setattr(mcu_sim, "serve_listener", lambda *a, **kw: None)
    config = Config()

    shutdown = daemon_mod._start_sim(config)
    try:
        device = config.ports[-1].device
        assert device is not None and device.startswith("socket://127.0.0.1:")
        port = int(device.rsplit(":", 1)[1])

        refused = False
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1.0).close()
            except ConnectionRefusedError:
                refused = True
                break
            except OSError:
                # Not proof of anything: an unaccepted connection fills the backlog and
                # the next SYN is dropped rather than refused, which times out here.
                pass
            time.sleep(0.05)
        assert refused, f"127.0.0.1:{port} still accepts connections with no sim behind it"
    finally:
        shutdown()


def test_sim_listener_serves_while_its_thread_lives() -> None:
    """The other half of the above: a healthy in-process sim does answer."""
    config = Config()
    shutdown = daemon_mod._start_sim(config)
    try:
        device = config.ports[-1].device
        assert device is not None
        port = int(device.rsplit(":", 1)[1])
        with socket.create_connection(("127.0.0.1", port), timeout=5.0) as conn:
            conn.sendall(b">1 ping\n")
            conn.settimeout(5.0)
            buf = bytearray()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and b"<1 " not in buf:
                buf.extend(conn.recv(4096))
        assert b"<1 OK monitor 1 sim" in buf
    finally:
        shutdown()
    assert not any(t.name == "mcu-sim" and t.is_alive() for t in threading.enumerate()
                   if not t.daemon)
