"""Phase 0 smoke tests: package imports and console-script entry points resolve."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig

import pytest

import mcuscope
from mcuscope import cli, daemon


def test_version_present() -> None:
    assert isinstance(mcuscope.__version__, str)
    assert mcuscope.__version__


def test_cli_app_present() -> None:
    # The CLI is a typer app (built out in phase 3); the entry point stays callable.
    assert cli.app is not None
    assert callable(cli.main)


def test_daemon_help_parses() -> None:
    parser = daemon.build_parser()
    assert parser.prog == "mcuscoped"


def _console_script(name: str) -> str | None:
    """Locate an installed console script, preferring this interpreter's own bin dir."""
    exe = name + (".exe" if os.name == "nt" else "")
    scripts = sysconfig.get_path("scripts")
    candidate = os.path.join(scripts, exe)
    if os.path.exists(candidate):
        return candidate
    return shutil.which(name)


@pytest.mark.parametrize("name", ["mcu", "mcuscoped", "mcu-sim"])
def test_console_scripts_run(name: str) -> None:
    """Run the generated .exe/shim itself, not `python -m`.

    The rest of the suite drives the CLI as `python -m mcuscope.cli`, so the console-script
    wrapper - a distinct code path - was never executed anywhere. It matters most on
    Windows, where every startup bug in the changelog (a pythonw base interpreter, null
    std streams, no console) originates in the wrapper's choice of interpreter, and a
    regression there would ship with a green suite.
    """
    script = _console_script(name)
    if script is None:
        pytest.skip(f"{name} is not installed (no editable/wheel install in this env)")
    # mcu-sim has no --version; --help is the universal cheap check that it starts.
    flag = "--help" if name == "mcu-sim" else "--version"
    proc = subprocess.run(
        [script, flag], capture_output=True, text=True, timeout=60,
        # Keep the release check offline even if the ambient env lacks conftest's veto.
        env={**os.environ, "MCUSCOPE_UPDATE_CHECK": "0"},
    )
    assert proc.returncode == 0, f"{script} {flag} exited {proc.returncode}: {proc.stderr}"
    out = proc.stdout + proc.stderr
    if flag == "--version":
        assert mcuscope.__version__ in out, out
    else:
        assert "usage" in out.lower(), out
    # The wrapper must run on a 3.11+ interpreter, or the guard in __init__ would fire.
    assert "requires Python 3.11" not in out
    assert sys.version_info >= (3, 11)
