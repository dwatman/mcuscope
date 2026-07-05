"""Run the firmware monitor's host-compiled C test suite from pytest.

This shells out to `make -C firmware/tests run`, which builds monitor.c/monitor_cmds.c
plus the fake shims and the test driver (gcc, -Wall -Wextra -Werror) and executes the
binary. The C driver returns non-zero if any check fails. Skipped cleanly when no C
compiler or make is available (e.g. a bare Windows box without a toolchain), so the
Python suite still passes there.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FW_TESTS = REPO_ROOT / "firmware" / "tests"


def _have_toolchain() -> bool:
    if shutil.which("make") is None:
        return False
    return any(shutil.which(cc) is not None for cc in ("cc", "gcc", "clang"))


@pytest.mark.skipif(not _have_toolchain(), reason="no C compiler / make on PATH")
def test_firmware_monitor_c_suite() -> None:
    # Pick an available compiler for CC so we do not rely on a `cc` symlink existing.
    cc = next((c for c in ("cc", "gcc", "clang") if shutil.which(c)), "cc")
    proc = subprocess.run(
        ["make", "-C", str(FW_TESTS), "run", f"CC={cc}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.fail(f"firmware monitor C tests failed:\n{proc.stdout}\n{proc.stderr}")
