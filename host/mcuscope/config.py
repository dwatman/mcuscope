"""Daemon configuration (SPEC 3.3): dataclasses and TOML loading.

Paths come from platformdirs so the same code resolves sensible locations on Linux
(`~/.config`, `~/.local/share`) and Windows (`%APPDATA%`). This is plain stdlib
`tomllib` plus small dataclasses, not a config framework.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import platformdirs

APP_NAME = "mcuscope"

log = logging.getLogger(__name__)


class ConfigError(Exception):
    """config.toml exists but could not be parsed or has an invalid value."""


# Port aliases must be usable as filter values and path segments, and must never be
# empty (alias "" collides with the daemon-level port="" convention, SPEC 3.5). The
# same pattern is enforced on the HTTP attach body in server.py.
ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    # Optional shared-secret for clients connecting from non-loopback addresses.
    # Loopback clients are always exempt (the local machine is the trust boundary,
    # SPEC 3.4); when unset, non-loopback binds serve unauthenticated (warned loudly).
    token: str | None = None


@dataclass
class StorageConfig:
    db_path: str = ""            # empty means <user_data_dir>/capture.db
    retention_days: int = 7


@dataclass
class PortConfig:
    alias: str
    device: str | None = None
    serial_number: str | None = None
    baud: int = 115200
    autoconnect: bool = True


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    ports: list[PortConfig] = field(default_factory=list)


def default_config_path() -> Path:
    """Location of config.toml (SPEC 3.3), cross-platform via platformdirs."""
    return Path(platformdirs.user_config_dir(APP_NAME)) / "config.toml"


def resolve_db_path(config: Config) -> str:
    """Resolve the capture database path, applying the platformdirs default."""
    raw = config.storage.db_path.strip()
    if raw:
        return os.path.expanduser(raw)
    return str(Path(platformdirs.user_data_dir(APP_NAME)) / "capture.db")


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load config from `path` (or the platformdirs default). Missing file is OK.

    Every key is optional; an absent file yields defaults with no ports.
    """
    cfg_path = Path(path) if path is not None else default_config_path()
    if not cfg_path.exists():
        return Config()
    try:
        with open(cfg_path, "rb") as fh:
            data = tomllib.load(fh)
        return _from_dict(data)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{cfg_path}: invalid TOML: {exc}") from exc
    except (TypeError, ValueError, AttributeError) as exc:
        raise ConfigError(f"{cfg_path}: invalid value: {exc}") from exc


def _from_dict(data: dict) -> Config:
    server_d = data.get("server", {}) or {}
    storage_d = data.get("storage", {}) or {}
    ports_d = data.get("ports", []) or []
    token = server_d.get("token")
    if token is not None:
        token = str(token).strip() or None
    server = ServerConfig(
        host=server_d.get("host", ServerConfig.host),
        port=int(server_d.get("port", ServerConfig.port)),
        token=token,
    )
    storage = StorageConfig(
        db_path=storage_d.get("db_path", StorageConfig.db_path),
        retention_days=int(storage_d.get("retention_days", StorageConfig.retention_days)),
    )
    ports: list[PortConfig] = []
    for i, entry in enumerate(ports_d):
        alias = entry.get("alias")
        if not alias:
            # A port without an alias is unusable; say so instead of vanishing it.
            log.warning("config: [[ports]] entry %d has no alias, skipping it", i + 1)
            continue
        if not ALIAS_RE.fullmatch(str(alias)):
            log.warning("config: port alias %r is invalid, skipping it", alias)
            continue
        if not entry.get("device") and not entry.get("serial_number"):
            # Without either, the reader thread would retry forever on nothing.
            log.warning(
                "config: port %r has neither device nor serial_number, skipping it", alias
            )
            continue
        ports.append(
            PortConfig(
                alias=alias,
                device=entry.get("device"),
                serial_number=entry.get("serial_number"),
                baud=int(entry.get("baud", PortConfig.baud)),
                autoconnect=bool(entry.get("autoconnect", PortConfig.autoconnect)),
            )
        )
    return Config(server=server, storage=storage, ports=ports)
