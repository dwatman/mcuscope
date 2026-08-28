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


def _start_sim(*extra: str) -> tuple[subprocess.Popen, str]:
    """Start `mcu_sim.py --pty` and return the process and the slave path it printed."""
    proc = subprocess.Popen(
        [sys.executable, str(SIM_SCRIPT), "--pty", *extra],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **CHILD_TEXT,
    )
    assert proc.stdout is not None
    slave_path = proc.stdout.readline().strip()
    if not slave_path:
        err = proc.stderr.read() if proc.stderr else ""
        proc.kill()
        raise AssertionError(f"mcu_sim.py --pty printed no slave path; stderr: {err}")
    return proc, slave_path


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5.0)


def test_pty_slave_is_raw_before_anyone_attaches() -> None:
    """openpty() leaves the slave canonical: the line discipline ate \\x7f out of the sim's
    own output and echoed everything back into its read path. pyserial sets raw when it
    opens, so only the pre-attach window shows it - opened here with plain os.open."""
    import termios

    proc, slave_path = _start_sim()
    fd = None
    try:
        fd = os.open(slave_path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        iflag, oflag, _cflag, lflag, *_ = termios.tcgetattr(fd)
        assert not lflag & termios.ECHO, "the slave echoes the sim's output back at it"
        assert not lflag & termios.ICANON, "the slave is line-disciplined, not raw"
        assert not oflag & termios.OPOST, "output post-processing rewrites the sim's bytes"
        assert not iflag & termios.ICRNL
    finally:
        if fd is not None:
            os.close(fd)
        _stop(proc)


def test_pty_write_gives_up_instead_of_wedging_with_no_reader() -> None:
    """One blocking os.write() parked the serving thread for good once the slave's input
    queue filled with nothing attached: it then read nothing and polled nothing while the
    slave path stayed stat-able, so a daemon attached to a corpse.

    Driven at the write loop rather than through a subprocess because the wedge is only
    visible while nothing reads: attaching a reader releases the blocked write, so a
    round-trip test passes either way."""
    import contextlib
    import pty
    import threading

    from mcuscope import sim as sim_module

    master, slave = pty.openpty()
    result: dict[str, object] = {}

    def run() -> None:
        try:
            # Far more than the slave's queue holds, with nobody draining it.
            result["ok"] = sim_module._pty_write_lines(master, ["x" * 200] * 5000, budget=0.5)
        except BaseException as exc:   # a raise is as bad as a hang: the session dies
            result["exc"] = exc

    try:
        os.set_blocking(master, False)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(10.0)
        assert not worker.is_alive(), "the write wedged with no reader on the slave"
        assert "exc" not in result, f"the write raised instead of giving up: {result.get('exc')!r}"
        assert result["ok"] is False, "a dropped backlog must be reported, not claimed sent"

        # And it recovers: keep draining until a write goes through, bounded by a
        # deadline rather than by one read freeing enough queue in one attempt.
        def drain() -> None:
            with contextlib.suppress(OSError):
                while True:
                    os.read(slave, 65536)

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        deadline = time.monotonic() + 10.0
        sent = False
        while not sent and time.monotonic() < deadline:
            sent = sim_module._pty_write_lines(master, ["<1 OK ping"], budget=2.0)
        assert sent, "the writer never recovered after the slave was drained"
    finally:
        for fd in (master, slave):
            try:
                os.close(fd)
            except OSError:
                pass
