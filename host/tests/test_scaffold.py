"""Phase 0 smoke tests: package imports and console-script entry points resolve."""

from __future__ import annotations

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
