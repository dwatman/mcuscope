"""mcuscoped entry point: parse args, load config, run the app under uvicorn.

The app itself (endpoints, lifespan, port/store wiring) lives in server.py. This
module is just the process entry: it resolves configuration (SPEC 3.3) and hands the
app to uvicorn, binding to 127.0.0.1 only.
"""

from __future__ import annotations

import argparse

import uvicorn

from . import __version__
from .config import Config, load_config
from .server import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcuscoped",
        description="Host daemon owning serial ports and serving the mcuscope REST/WS API.",
    )
    parser.add_argument("--version", action="version", version=f"mcuscoped {__version__}")
    parser.add_argument(
        "-c",
        "--config",
        metavar="PATH",
        help="Path to config.toml (default: platformdirs user config dir).",
    )
    parser.add_argument("--host", metavar="ADDR", help="Override server.host from config.")
    parser.add_argument(
        "--port", type=int, metavar="PORT", help="Override server.port from config."
    )
    return parser


def _apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    if args.host:
        config.server.host = args.host
    if args.port:
        config.server.port = args.port
    return config


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = _apply_overrides(load_config(args.config), args)
    app = create_app(config)
    uvicorn.run(app, host=config.server.host, port=config.server.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
