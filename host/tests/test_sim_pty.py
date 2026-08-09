"""POSIX-only pty transport test for the simulator (SPEC 7).

Spawns `python tools/mcu_sim.py --pty` as a real subprocess, reads the pty slave path it
prints on startup, opens it with pyserial, and exercises the same `>1 ping` round trip
as the TCP transport test. `--pty` is refused on Windows by mcu_sim itself (see
serve_pty), so this whole module is skipped there rather than exercised as a failure.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import serial

from tests.support import CHILD_TEXT

REPO_ROOT = Path(__file__).resolve().parents[2]
SIM_SCRIPT = REPO_ROOT / "tools" / "mcu_sim.py"

pytestmark = pytest.mark.skipif(os.name != "posix", reason="pty transport is POSIX-only")


def _read_line_matching(ser: serial.Serial, prefix: str, timeout: float = 5.0) -> str:
    """Read newline-terminated lines until one starts with `prefix`, or time out."""
    buf = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ser.timeout = max(0.0, deadline - time.monotonic())
        chunk = ser.read(4096)
        if chunk:
            buf.extend(chunk)
        while b"\n" in buf:
            raw, _, remainder = buf.partition(b"\n")
            del buf[:]
            buf.extend(remainder)
            line = raw.decode("ascii", "replace")
            if line.startswith(prefix):
                return line
    raise AssertionError(f"no line starting with {prefix!r} received")


def test_pty_ping_round_trip(tmp_path: Path) -> None:
    symlink = tmp_path / "mcu-sim-pty"
    proc = subprocess.Popen(
        [sys.executable, str(SIM_SCRIPT), "--pty", "--symlink", str(symlink)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **CHILD_TEXT,
    )
    ser: serial.Serial | None = None
    try:
        assert proc.stdout is not None
        slave_path = proc.stdout.readline().strip()
        if not slave_path:
            err = proc.stderr.read() if proc.stderr else ""
            raise AssertionError(f"mcu_sim.py --pty printed no slave path; stderr: {err}")

        # Wait for the symlink to appear (created just after the printed path) rather than
        # racing the subprocess; fall back to the printed path if it never shows up.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not symlink.is_symlink():
            time.sleep(0.02)
        device_path = str(symlink) if symlink.is_symlink() else slave_path

        ser = serial.Serial(device_path, baudrate=115200, timeout=1.0)
        ser.write(b">1 ping\n")
        assert _read_line_matching(ser, "<1 ") == "<1 OK monitor 1 sim"

        # A second command on the same connection also works.
        ser.write(b">2 i2c scan\n")
        assert _read_line_matching(ser, "<2 ") == "<2 OK 48 50"
    finally:
        if ser is not None:
            ser.close()
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)
        if symlink.is_symlink() or symlink.exists():
            try:
                symlink.unlink()
            except OSError:
                pass
