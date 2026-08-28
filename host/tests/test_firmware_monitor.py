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

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support import CHILD_TEXT

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


_SUMMARY_RE = re.compile(r"^(\d+)/(\d+) checks passed$", re.MULTILINE)


def _make(target: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-C", str(FW_TESTS), target, f"CC={CC}"],
        capture_output=True,
        **CHILD_TEXT,
    )


def _assert_all_checks_ran(proc: subprocess.CompletedProcess[str], what: str) -> None:
    """Fail unless the C driver printed a summary saying every check passed.

    Make's exit code alone is not enough: a `make` that decides the binary is up to date, a
    driver that exits before reaching main's checks, or an empty suite all exit 0. Mirrors
    the count guard in test_webui_js.py.
    """
    out = f"{proc.stdout}\n{proc.stderr}"
    if proc.returncode != 0:
        pytest.fail(f"{what} failed:\n{out}")
    match = _SUMMARY_RE.search(proc.stdout)
    if match is None:
        pytest.fail(f"{what}: no '<n>/<n> checks passed' summary in output:\n{out}")
    passed, total = int(match.group(1)), int(match.group(2))
    if total == 0 or passed != total:
        pytest.fail(f"{what}: {passed}/{total} checks passed:\n{out}")


@needs_toolchain
def test_firmware_monitor_c_suite() -> None:
    _assert_all_checks_ran(_make("run"), "firmware monitor C tests")


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

    _assert_all_checks_ran(_make("asan"), "firmware monitor C tests under ASan/UBSan")
