"""tools/mcu_sim.py, the source checkout's back-compat shim, re-exports mcuscope.sim.

The rest of the suite imports `mcuscope.sim` so it collects from an sdist, which ships no
tools/; this is the one test of the shim, skipped where it is absent.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mcuscope import sim
from tests.support import CHILD_TEXT, child_env

SHIM = Path(__file__).resolve().parents[2] / "tools" / "mcu_sim.py"
pytestmark = pytest.mark.skipif(not SHIM.exists(), reason="tools/ is not in this tree (sdist)")


def test_the_shim_re_exports_the_package_simulator():
    import mcu_sim  # found through conftest's tools/ path entry

    assert Path(mcu_sim.__file__).resolve() == SHIM
    for name in ("main", "build_parser", "spawn", "_format_typed_sample", "_process_incoming"):
        assert getattr(mcu_sim, name) is getattr(sim, name), name


def test_the_shim_runs_as_a_script():
    r = subprocess.run(
        [sys.executable, str(SHIM), "--help"], capture_output=True, env=child_env(), **CHILD_TEXT
    )
    assert r.returncode == 0 and "--pty" in r.stdout, r.stderr
