"""Daemon configuration (SPEC 3.3): dataclasses, TOML loading, and write-back.

Paths come from platformdirs so the same code resolves sensible locations on Linux
(`~/.config`, `~/.local/share`) and Windows (`%APPDATA%`). Reading uses stdlib
`tomllib`; the write-back API (SPEC 3.3.1) uses tomlkit so comments, ordering, and
unknown keys in a hand-edited file survive UI edits. Saves are read-modify-write
with an atomic replace.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import platformdirs
import tomlkit

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
    # Runtime-only (SPEC 3.1): set via --token or MCUSCOPED_TOKEN, never loaded from
    # the config file, so the UI-writable config surface cannot touch authentication.
    # Loopback clients are always exempt; when unset, non-loopback binds serve
    # unauthenticated (warned loudly).
    token: str | None = None


@dataclass
class StorageConfig:
    db_path: str = ""            # empty means <user_data_dir>/capture.db
    # Ten days rather than a week so two successive weekends are always covered: work
    # paused on a Friday is still there when it resumes the Monday after next.
    retention_days: int = 10
    # Never expire the lines belonging to the newest N sessions, however old they get.
    # Age alone is a poor measure of what is worth keeping: a board captured over a quiet
    # fortnight would otherwise lose its only recorded run to the calendar. 0 disables the
    # floor (pure age-based retention).
    min_sessions: int = 5
    # Open a session automatically for each daemon run, so "the newest N sessions" means
    # "the newest N runs" without anyone having to remember to name one. The normal way to
    # use MCUscope - daemon up, agent issuing commands - names no sessions at all, which
    # would leave the floor above protecting nothing.
    auto_session: bool = True
    # Cap on live capture content, in bytes. 0 (the default) means no cap: a capture is
    # bounded by retention_days alone, so nothing is ever dropped for size unless the
    # owner opts in. When set, the oldest lines are trimmed to stay under it.
    max_db_bytes: int = 0


@dataclass
class UpdateConfig:
    # Ask PyPI, at most once a day, whether a newer release exists, and show it in the web
    # UI (SPEC 3.6). On by default: an out-of-date debug tool is a real cost and the check
    # is one cached request a day. It is one key to turn off, and the environment veto
    # (MCUSCOPE_UPDATE_CHECK=0) works without a config file at all.
    check: bool = True


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
    update: UpdateConfig = field(default_factory=UpdateConfig)
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
    update_d = data.get("update", {}) or {}
    ports_d = data.get("ports", []) or []
    if server_d.get("token") is not None:
        # The token is runtime-only (SPEC 3.3): a file key would let the UI-writable
        # config surface grant or revoke authentication. Ignore it, loudly.
        log.warning(
            "config: server.token in the config file is ignored; "
            "set the MCUSCOPED_TOKEN environment variable (or --token) instead"
        )
    server = ServerConfig(
        host=server_d.get("host", ServerConfig.host),
        port=int(server_d.get("port", ServerConfig.port)),
    )
    storage = StorageConfig(
        db_path=storage_d.get("db_path", StorageConfig.db_path),
        # Clamped like its neighbours: the sweep computes `now - retention_days * 86400`, so
        # a zero or negative value puts the cutoff in the future and the first sweep deletes
        # the entire capture. The write-back API already bounds this (ge=1); a hand-edited
        # file is exactly the path that never sees that validation.
        retention_days=max(1, int(storage_d.get("retention_days", StorageConfig.retention_days))),
        max_db_bytes=max(0, int(storage_d.get("max_db_bytes", StorageConfig.max_db_bytes))),
        min_sessions=max(0, int(storage_d.get("min_sessions", StorageConfig.min_sessions))),
        auto_session=bool(storage_d.get("auto_session", StorageConfig.auto_session)),
    )
    update = UpdateConfig(check=bool(update_d.get("check", UpdateConfig.check)))
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
    return Config(server=server, storage=storage, update=update, ports=ports)


# -- write-back (SPEC 3.3.1) -----------------------------------------------------------
#
# Each save re-parses the current file with tomlkit, changes only the affected keys,
# and writes atomically, so hand edits (including ones made while the daemon runs)
# survive. Replacing the ports list rewrites the whole [[ports]] array-of-tables.


def _read_doc(path: Path) -> tomlkit.TOMLDocument:
    if not path.exists():
        return tomlkit.document()
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except Exception as exc:  # tomlkit raises its own parse error hierarchy
        raise ConfigError(f"{path}: cannot rewrite invalid TOML: {exc}") from exc


def _write_doc(path: Path, doc: tomlkit.TOMLDocument) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    # newline="" so the LF tomlkit emits is written verbatim. The default translates it to
    # CRLF on Windows, so a single settings save from the web UI rewrote every line of a
    # hand-edited config file.
    tmp.write_text(tomlkit.dumps(doc), encoding="utf-8", newline="")
    os.replace(tmp, path)


def _table(doc: tomlkit.TOMLDocument, name: str):
    if name not in doc:
        doc[name] = tomlkit.table()
    section = doc[name]
    if not isinstance(section, dict):
        # e.g. a hand-edited `server = 3`; refuse rather than raise a bare TypeError.
        raise ConfigError(f"config key [{name}] is not a table; fix the file by hand")
    return section


def save_server(path: Path, host: str, port: int) -> None:
    doc = _read_doc(path)
    section = _table(doc, "server")
    section["host"] = host
    section["port"] = port
    _write_doc(path, doc)


def save_storage(
    path: Path, db_path: str, retention_days: int,
    max_db_bytes: int = 0, min_sessions: int = StorageConfig.min_sessions,
    auto_session: bool = StorageConfig.auto_session,
) -> None:
    doc = _read_doc(path)
    section = _table(doc, "storage")
    section["db_path"] = db_path
    section["retention_days"] = retention_days
    section["max_db_bytes"] = max_db_bytes
    section["min_sessions"] = min_sessions
    section["auto_session"] = auto_session
    _write_doc(path, doc)


def save_update(path: Path, check: bool) -> None:
    doc = _read_doc(path)
    section = _table(doc, "update")
    section["check"] = check
    _write_doc(path, doc)


def save_ports(path: Path, ports: list[PortConfig]) -> None:
    doc = _read_doc(path)
    aot = tomlkit.aot()
    for pc in ports:
        entry = tomlkit.table()
        entry["alias"] = pc.alias
        if pc.device:
            entry["device"] = pc.device
        if pc.serial_number:
            entry["serial_number"] = pc.serial_number
        entry["baud"] = pc.baud
        entry["autoconnect"] = pc.autoconnect
        aot.append(entry)
    if ports:
        doc["ports"] = aot
    elif "ports" in doc:
        # An empty array-of-tables renders as nothing; drop the key entirely.
        del doc["ports"]
    _write_doc(path, doc)
