"""hwbridged entry point: config load, wiring, lifecycle.

Phase 0 provides a minimal argparse stub so `hwbridged --help` works and the console
script resolves. The real daemon is built in phase 2.
"""

from __future__ import annotations

import argparse

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hwbridged",
        description="Host daemon owning serial ports and serving the hwbridge REST/WS API.",
    )
    parser.add_argument("--version", action="version", version=f"hwbridged {__version__}")
    parser.add_argument(
        "-c",
        "--config",
        metavar="PATH",
        help="Path to config.toml (default: ~/.config/hwbridge/config.toml).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Phase 0 stub: parsing succeeds but the daemon is not implemented yet.
    print("hwbridged: not implemented yet (phase 0 scaffold).")
    _ = args
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
