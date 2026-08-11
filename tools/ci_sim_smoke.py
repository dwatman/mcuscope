#!/usr/bin/env python3
"""CI smoke test for the documented standalone simulator invocation.

Phase 1 of docs/IMPLEMENTATION_PLAN.md is accepted on this exact behaviour: running
`python tools/mcu_sim.py` and sending `>1 ping` over a TCP connection to it yields
`<1 OK monitor 1 sim`, on both Linux and Windows. Nothing in the pytest suite covers
it, because the tests drive the simulator core in process rather than the shim script
and its TCP listener.

Stdlib only, one code path for both OSes, and every wait has a deadline so a CI job
can fail rather than hang. The child is killed on every exit path.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import threading
import time

# Seconds. Generous for a loaded CI runner, still far below any job timeout.
STARTUP_TIMEOUT = 30.0
REPLY_TIMEOUT = 15.0

SOCKET_RE = re.compile(r"^socket://(\d+\.\d+\.\d+\.\d+):(\d+)\s*$")


def _fail(msg: str) -> None:
    print(f"SIM SMOKE FAILED: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _read_banner(proc: subprocess.Popen[str], timeout: float) -> str:
    """Return the simulator's first stdout line, or "" if it never arrives in time."""
    box: list[str] = []

    def pump() -> None:
        line = proc.stdout.readline() if proc.stdout else ""
        box.append(line)

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    t.join(timeout)
    return box[0] if box else ""


def _ping(host: str, port: int, deadline: float) -> str:
    """Send `>1 ping` and return the first `<1 ...` response line."""
    with socket.create_connection((host, port), timeout=max(1.0, deadline - time.monotonic())) as s:
        s.sendall(b">1 ping\n")
        buf = b""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail(f"no `<1` response within {REPLY_TIMEOUT:.0f}s (got {buf!r})")
            s.settimeout(remaining)
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                _fail(f"no `<1` response within {REPLY_TIMEOUT:.0f}s (got {buf!r})")
            if not chunk:
                _fail(f"simulator closed the connection (got {buf!r})")
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").rstrip("\r")
                # The simulator also emits unsolicited traffic (the 10 Hz CAN heartbeat,
                # debug lines), so scan past anything that is not this seq's response.
                if line.startswith("<1 "):
                    return line


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    sim = os.path.join(here, "mcu_sim.py")
    if not os.path.isfile(sim):
        _fail(f"{sim} does not exist")

    proc = subprocess.Popen(
        [sys.executable, sim],
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        bufsize=1,
        cwd=os.path.dirname(here),
    )
    try:
        banner = _read_banner(proc, STARTUP_TIMEOUT)
        if not banner:
            _fail(f"simulator printed no socket:// line within {STARTUP_TIMEOUT:.0f}s")
        m = SOCKET_RE.match(banner.strip())
        if not m:
            _fail(f"unexpected first stdout line from the simulator: {banner!r}")
        host, port = m.group(1), int(m.group(2))
        print(f"simulator listening on socket://{host}:{port}")

        line = _ping(host, port, time.monotonic() + REPLY_TIMEOUT)
        print("sent  >1 ping")
        print(f"got   {line}")
        if not line.startswith("<1 OK monitor"):
            _fail(f"expected a line starting with `<1 OK monitor`, got {line!r}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        if proc.stdout is not None:
            proc.stdout.close()

    print("OK: sim TCP smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
