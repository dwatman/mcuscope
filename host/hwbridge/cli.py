"""The mcu command-line client (thin HTTP client of hwbridged).

Phase 0 provides a minimal argparse stub so `mcu --help` works and the console script
resolves. The full typer-based CLI (SPEC section 4) is built in phase 3.
"""

from __future__ import annotations

import argparse

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcu",
        description="Command-line client for the hwbridge hardware debug bridge.",
    )
    parser.add_argument("--version", action="version", version=f"mcu {__version__}")
    parser.add_argument(
        "--url",
        metavar="URL",
        help="Daemon base URL (default: env HWBRIDGE_URL or http://127.0.0.1:8765).",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Phase 0 stub: parsing succeeds but the CLI commands are not implemented yet.
    print("mcu: not implemented yet (phase 0 scaffold).")
    _ = args
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
