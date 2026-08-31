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
from tests.support import CHILD_TEXT


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


def _console_scripts_declared() -> bool:
    """True when the installed mcuscope distribution declares console scripts.

    Then a missing executable is a packaging failure, not an uninstalled checkout: the
    skip below is otherwise indistinguishable from a pass in a CI summary, and this is
    the only test that runs the shipped wrappers at all.
    """
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        dist = distribution("mcuscope")
    except PackageNotFoundError:
        return False
    return any(ep.group == "console_scripts" for ep in dist.entry_points)


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
        if _console_scripts_declared():
            pytest.fail(
                f"mcuscope is installed here and declares the {name} console script, but no "
                f"{name} executable is in {sysconfig.get_path('scripts')} or on PATH"
            )
        pytest.skip(f"{name} is not installed (no editable/wheel install in this env)")
    # mcu-sim has no --version; --help is the universal cheap check that it starts.
    flag = "--help" if name == "mcu-sim" else "--version"
    proc = subprocess.run(
        [script, flag], capture_output=True, **CHILD_TEXT, timeout=60,
        # Keep the release check offline even if the ambient env lacks conftest's veto.
        env={**os.environ, "MCUSCOPE_UPDATE_CHECK": "0"},
    )
    assert proc.returncode == 0, f"{script} {flag} exited {proc.returncode}: {proc.stderr}"
    out = proc.stdout + proc.stderr
    if flag == "--version":
        assert mcuscope.__version__ in out, out
    else:
        assert "usage" in out.lower(), out
    # The wrapper must run on a 3.10+ interpreter, or the guard in __init__ would fire.
    assert "requires Python 3.10" not in out
    assert sys.version_info >= (3, 10)


def test_a_missing_console_script_is_not_silently_skipped(monkeypatch) -> None:
    """The skip above must never hide broken packaging in an installed environment."""
    if not _console_scripts_declared():
        pytest.skip("mcuscope is not installed here, so a missing script is a real skip")
    monkeypatch.setattr(sys.modules[__name__], "_console_script", lambda name: None)

    # Not pytest.raises: a skip is also an exception, so raises(fail.Exception) would let
    # the skip through and this test would go inert exactly like the one it guards.
    outcome: BaseException | None = None
    try:
        test_console_scripts_run("mcu")
    except BaseException as exc:  # noqa: BLE001 - the outcome is the assertion
        outcome = exc
    assert isinstance(outcome, pytest.fail.Exception), (
        f"a missing console script must fail loudly, got {outcome!r}"
    )
