"""mcuscoped entry point: parse args, load config, run the app under uvicorn.

The app itself (endpoints, lifespan, port/store wiring) lives in server.py. This
module is just the process entry: it resolves configuration (SPEC 3.3) and hands the
app to uvicorn. The default bind is 127.0.0.1; non-loopback binds are supported for
LAN use and should set an access token via MCUSCOPED_TOKEN or --token (a loud
warning is printed otherwise; the token is runtime-only, never a config key).
"""

from __future__ import annotations

import argparse
import os

import uvicorn

from . import __version__
from .config import Config, ConfigError, load_config
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
        help="Path to config.toml (env MCUSCOPED_CONFIG; "
        "default: platformdirs user config dir).",
    )
    parser.add_argument("--host", metavar="ADDR", help="Override server.host from config.")
    parser.add_argument(
        "--port", type=int, metavar="PORT", help="Override server.port from config."
    )
    parser.add_argument(
        "--token",
        metavar="TOKEN",
        help="Require this access token from non-loopback clients. Prefer the "
        "MCUSCOPED_TOKEN environment variable (not visible in the process list); "
        "the token is runtime-only and never read from the config file.",
    )
    return parser


def _apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    if args.host:
        config.server.host = args.host
    if args.port:
        config.server.port = args.port
    env_token = os.environ.get("MCUSCOPED_TOKEN", "").strip()
    if env_token:
        config.server.token = env_token
    if args.token:
        config.server.token = args.token
    return config


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1"})


def _warn_if_exposed(host: str, token: str | None) -> None:
    """Warn loudly when binding a non-loopback address without a token (SPEC 3.4)."""
    if host in _LOOPBACK_HOSTS:
        return
    if token is None:
        print(
            f"WARNING: binding {host} exposes the UNAUTHENTICATED mcuscope API to the network. "
            "Anyone who can reach this address can read captured data and drive the target. "
            "Set MCUSCOPED_TOKEN (or --token) to require an access token from network "
            "clients; the same-origin guard blocks browsers but not direct clients. "
            "Config editing over the API is disabled for network clients until a "
            "token is set.",
            flush=True,
        )
    elif len(token) < 16:
        print(
            "WARNING: the access token is shorter than 16 characters; use a longer "
            "random token for network exposure.",
            flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg_path = args.config or os.environ.get("MCUSCOPED_CONFIG") or None
    try:
        config = _apply_overrides(load_config(cfg_path), args)
    except ConfigError as exc:
        print(f"mcuscoped: {exc}", flush=True)
        return 1
    _warn_if_exposed(config.server.host, config.server.token)
    app = create_app(config, config_path=cfg_path)
    uvicorn.run(app, host=config.server.host, port=config.server.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
