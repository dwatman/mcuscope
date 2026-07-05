"""Cross-platform TCP transport test for the simulator (SPEC 7, phase 1 acceptance).

Encodes the phase 1 acceptance: send `>1 ping` over a TCP connection to the sim and
get back `<1 OK monitor 1 sim`. Runs on both Linux and Windows.
"""

from __future__ import annotations

import socket
import threading
import time

import mcu_sim


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
    args = mcu_sim.build_parser().parse_args([])
    srv = mcu_sim.open_tcp_listener(0)
    port = srv.getsockname()[1]
    stop = threading.Event()
    thread = threading.Thread(target=mcu_sim.serve_listener, args=(args, srv, stop), daemon=True)
    thread.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as conn:
            conn.sendall(b">1 ping\n")
            assert _read_line_matching(conn, "<1 ") == "<1 OK monitor 1 sim"
            # A second command on the same connection also works.
            conn.sendall(b">2 i2c scan\n")
            assert _read_line_matching(conn, "<2 ") == "<2 OK 48 50"
    finally:
        stop.set()
        srv.close()
        thread.join(timeout=2.0)


def test_tcp_reconnect_serves_next_client() -> None:
    # The listener serves one client at a time and accepts a fresh one after a drop,
    # which is what the phase 2 reconnect test relies on.
    args = mcu_sim.build_parser().parse_args([])
    srv = mcu_sim.open_tcp_listener(0)
    port = srv.getsockname()[1]
    stop = threading.Event()
    thread = threading.Thread(target=mcu_sim.serve_listener, args=(args, srv, stop), daemon=True)
    thread.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as conn:
            conn.sendall(b">1 ping\n")
            assert _read_line_matching(conn, "<1 ") == "<1 OK monitor 1 sim"
        # First connection closed; a new one must be accepted and served.
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as conn2:
            conn2.sendall(b">1 ping\n")
            assert _read_line_matching(conn2, "<1 ") == "<1 OK monitor 1 sim"
    finally:
        stop.set()
        srv.close()
        thread.join(timeout=2.0)
