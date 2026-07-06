"""Daemon configuration (SPEC 3.3): dataclasses and TOML loading.

Paths come from platformdirs so the same code resolves sensible locations on Linux
(`~/.config`, `~/.local/share`) and Windows (`%APPDATA%`). This is plain stdlib
`tomllib` plus small dataclasses, not a config framework.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import platformdirs

APP_NAME = "mcuscope"


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765


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
    with open(cfg_path, "rb") as fh:
        data = tomllib.load(fh)
    return _from_dict(data)


def _from_dict(data: dict) -> Config:
    server_d = data.get("server", {}) or {}
    storage_d = data.get("storage", {}) or {}
    ports_d = data.get("ports", []) or []
    server = ServerConfig(
        host=server_d.get("host", ServerConfig.host),
        port=int(server_d.get("port", ServerConfig.port)),
    )
    storage = StorageConfig(
        db_path=storage_d.get("db_path", StorageConfig.db_path),
        retention_days=int(storage_d.get("retention_days", StorageConfig.retention_days)),
    )
    ports: list[PortConfig] = []
    for entry in ports_d:
        alias = entry.get("alias")
        if not alias:
            continue  # a port without an alias is unusable; skip it
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
