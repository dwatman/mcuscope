"""Run the firmware monitor's host-compiled C test suite from pytest.

This shells out to `make -C firmware/tests run`, which builds monitor.c/monitor_cmds.c
plus the fake shims and the test driver (gcc, -Wall -Wextra -Werror) and executes the
binary, and then to `make asan` for the same suite under AddressSanitizer + UBSan;
`make families` does the same for each MON_NO_<FAMILY> build. The C driver returns
non-zero if any check fails. Skipped cleanly when no C compiler or make is
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


def _assert_all_checks_ran(
    proc: subprocess.CompletedProcess[str], what: str, runs: int = 1
) -> None:
    """Fail unless each of `runs` C drivers printed a summary saying every check passed.

    Make's exit code alone is not enough: a `make` that decides the binary is up to date, a
    driver that exits before reaching main's checks, or an empty suite all exit 0. Mirrors
    the count guard in test_webui_js.py.
    """
    out = f"{proc.stdout}\n{proc.stderr}"
    if proc.returncode != 0:
        pytest.fail(f"{what} failed:\n{out}")
    matches = _SUMMARY_RE.findall(proc.stdout)
    if len(matches) != runs:
        pytest.fail(f"{what}: {len(matches)} of {runs} '<n>/<n> checks passed' summaries:\n{out}")
    for passed, total in matches:
        if int(total) == 0 or passed != total:
            pytest.fail(f"{what}: {passed}/{total} checks passed:\n{out}")


@needs_toolchain
def test_firmware_monitor_c_suite() -> None:
    _assert_all_checks_ran(_make("run"), "firmware monitor C tests")


def _skip_without_sanitizers(tmp_path: Path) -> None:
    probe = tmp_path / "probe.c"
    probe.write_text("int main(void) { return 0; }\n", newline="")
    linkable = subprocess.run(
        [CC, *SAN_FLAGS, str(probe), "-o", str(tmp_path / "probe")],
        capture_output=True,
    )
    if linkable.returncode != 0:
        pytest.skip(f"{CC} cannot link a sanitized build (MinGW-w64 ships no ASan runtime)")


@needs_toolchain
def test_firmware_monitor_c_suite_under_sanitizers(tmp_path: Path) -> None:
    # The parser is fed untrusted UART bytes, so an out-of-bounds read here is a defect on
    # the target rather than a test artifact - and two of them read adjacent memory without
    # faulting, so the plain -O2 build saw nothing (firmware/tests/Makefile records both).
    _skip_without_sanitizers(tmp_path)
    _assert_all_checks_ran(_make("asan"), "firmware monitor C tests under ASan/UBSan")


# MON_NO_CAN, _I2C, _SPI, _GPIO, _ADC, and all five together.
FAMILY_BUILDS = 6


@needs_toolchain
def test_firmware_family_flags() -> None:
    _assert_all_checks_ran(_make("families"), "MON_NO_<FAMILY> builds", runs=FAMILY_BUILDS)


@needs_toolchain
def test_firmware_family_flags_under_sanitizers(tmp_path: Path) -> None:
    _skip_without_sanitizers(tmp_path)
    _assert_all_checks_ran(
        _make("families-asan"), "MON_NO_<FAMILY> builds under ASan/UBSan", runs=FAMILY_BUILDS
    )


def _compile_eventf_call(tmp_path: Path, call: str) -> subprocess.CompletedProcess[str]:
    src = tmp_path / "eventf.c"
    src.write_text(
        '#include "monitor/monitor.h"\n'
        f"void f(unsigned int tick) {{ {call}; }}\n",
        newline="",
    )
    return subprocess.run(
        [CC, "-std=c99", "-fsyntax-only", "-Wformat", "-Werror",
         f"-I{REPO_ROOT / 'firmware'}", str(src)],
        capture_output=True,
        **CHILD_TEXT,
    )


@needs_toolchain
def test_monitor_eventf_arguments_are_format_checked(tmp_path: Path) -> None:
    # An integer passed to %s is a HardFault on target; MON_PRINTF makes it a build error.
    bad = _compile_eventf_call(tmp_path, 'monitor_eventf("p %s", tick)')
    assert bad.returncode != 0 and "format" in bad.stderr, bad.stderr
    # Positive control: the same call, correctly cast, compiles under the same flags.
    good = _compile_eventf_call(tmp_path, 'monitor_eventf("p %lu", (unsigned long)tick)')
    assert good.returncode == 0, good.stderr
