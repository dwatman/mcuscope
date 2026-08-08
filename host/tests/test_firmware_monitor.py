"""Run the firmware monitor's host-compiled C test suite from pytest.

This shells out to `make -C firmware/tests run`, which builds monitor.c/monitor_cmds.c
plus the fake shims and the test driver (gcc, -Wall -Wextra -Werror) and executes the
binary, and then to `make asan` for the same suite under AddressSanitizer + UBSan. The C
driver returns non-zero if any check fails. Skipped cleanly when no C compiler or make is
available (e.g. a bare Windows box without a toolchain), so the Python suite still passes
there.

`make arm-check` is deliberately not run here: it is the only enforcement of SPEC 5.1's
freestanding rules but needs arm-none-eabi-gcc, which no developer box is required to have,
so a pytest case for it would skip on nearly every machine and prove nothing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FW_TESTS = REPO_ROOT / "firmware" / "tests"

# Same flags firmware/tests/Makefile's asan target uses, for the can-we-even-link probe.
SAN_FLAGS = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]


def _cc() -> str | None:
    """An available C compiler, or None. Not `cc` unconditionally: the symlink may not exist."""
    if shutil.which("make") is None:
        return None
    return next((c for c in ("cc", "gcc", "clang") if shutil.which(c)), None)


CC = _cc()
needs_toolchain = pytest.mark.skipif(CC is None, reason="no C compiler / make on PATH")


def _make(target: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-C", str(FW_TESTS), target, f"CC={CC}"],
        capture_output=True,
        text=True,
    )


@needs_toolchain
def test_firmware_monitor_c_suite() -> None:
    proc = _make("run")
    if proc.returncode != 0:
        pytest.fail(f"firmware monitor C tests failed:\n{proc.stdout}\n{proc.stderr}")


@needs_toolchain
def test_firmware_monitor_c_suite_under_sanitizers(tmp_path: Path) -> None:
    # The parser is fed untrusted UART bytes, so an out-of-bounds read here is a defect on
    # the target rather than a test artifact - and two of them read adjacent memory without
    # faulting, so the plain -O2 build saw nothing (firmware/tests/Makefile records both).
    probe = tmp_path / "probe.c"
    probe.write_text("int main(void) { return 0; }\n", newline="")
    linkable = subprocess.run(
        [CC, *SAN_FLAGS, str(probe), "-o", str(tmp_path / "probe")],
        capture_output=True,
    )
    if linkable.returncode != 0:
        pytest.skip(f"{CC} cannot link a sanitized build (MinGW-w64 ships no ASan runtime)")

    proc = _make("asan")
    if proc.returncode != 0:
        pytest.fail(
            f"firmware monitor C tests failed under ASan/UBSan:\n{proc.stdout}\n{proc.stderr}"
        )
