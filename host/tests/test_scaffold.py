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
from tests.support import CHILD_TEXT, UNREACHABLE, child_env, paths, recorder


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


CONSOLE_SCRIPTS = ["mcu", "mcuscoped", "mcu-sim"]


def test_console_scripts_names_every_declared_script() -> None:
    import pathlib

    import tomlkit

    pyproject = pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = set(tomlkit.parse(pyproject.read_text(encoding="utf-8"))["project"]["scripts"])
    assert len(declared) >= 3, declared
    assert set(CONSOLE_SCRIPTS) == declared


@pytest.mark.parametrize("name", CONSOLE_SCRIPTS)
def test_console_scripts_run(name: str) -> None:
    """Run the generated .exe/shim itself, not `python -m`.

    The suite starts the daemon in process, and test_cli.py's `_mcu_command` runs the `mcu`
    wrapper only where one is installed, else `python -m mcuscope.cli`. The wrapper is a
    distinct code path, and it matters most on Windows, where every startup bug in the
    changelog (a pythonw base interpreter, null std streams, no console) originates in the
    wrapper's choice of interpreter, and a regression there would ship with a green suite.
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
        env=child_env(MCUSCOPE_UPDATE_CHECK="0"),
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


# -- improvement 1: httpx's own CLI is not dragged in ----------------------------------


CHILD = """
import sys
from mcuscope import cli      # the console script's first import, as it is in production
import httpx

body = {"version": "0.4.0", "uptime_s": 1.0, "db_path": "x", "ports": []}
transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
cli.Client.open = lambda self: httpx.Client(transport=transport)
rc = cli.main(["status", "--url", "http://127.0.0.1:1"])
loaded = [m for m in sys.modules if sys.modules[m] is not None]
print("rc", rc, "httpx" in sys.modules, "rich" in sys.modules, "httpx._main" in loaded)
"""


def test_a_command_does_not_import_rich_through_httpx() -> None:
    """In a child process: the pytest process has already imported httpx (tests.support),
    where the sentinel `mcuscope.cli_client` sets at import time cannot get in first.
    """
    r = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[-4:] == ["0", "True", "False", "False"], r.stdout
    assert "mcuscoped 0.4.0" in r.stdout, "the run went through httpx for real"


def test_the_sentinel_leaves_httpx_working(monkeypatch, capsys) -> None:
    """A sentinel placed on the wrong name is silent; only a real request catches that."""
    import httpx as http_mod

    seen = recorder(monkeypatch, lines={"lines": [], "truncated": False})
    assert http_mod.Client is not None
    rc = cli.main(["lines", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert paths(seen) == ["/lines"]
