"""Phase 0 smoke tests: package imports and console-script entry points resolve."""

from __future__ import annotations

import hwbridge
from hwbridge import cli, daemon


def test_version_present() -> None:
    assert isinstance(hwbridge.__version__, str)
    assert hwbridge.__version__


def test_cli_help_parses() -> None:
    parser = cli.build_parser()
    assert parser.prog == "mcu"


def test_daemon_help_parses() -> None:
    parser = daemon.build_parser()
    assert parser.prog == "hwbridged"
