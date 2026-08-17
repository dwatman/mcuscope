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
import time
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
    # is one cached request a day. It is one key to turn off; MCUSCOPE_UPDATE_CHECK=0|1
    # overrides this key either way and needs no config file at all.
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
        # utf-8-sig, not utf-8: a byte-order mark makes tomllib fail with "Invalid
        # statement (at line 1, column 1)", which names neither the cause nor the fix.
        # Rare on Linux, but on Windows it is what the ordinary tools produce - PowerShell's
        # `Out-File -Encoding utf8` always writes one - so hand-editing the config the
        # obvious way there left the daemon refusing to start over an invisible character.
        data = tomllib.loads(cfg_path.read_text(encoding="utf-8-sig"))
        return _from_dict(data)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{cfg_path}: invalid TOML: {exc}") from exc
    except (TypeError, ValueError, AttributeError) as exc:
        raise ConfigError(f"{cfg_path}: invalid value: {exc}") from exc


def _as_bool(table: dict, key: str, default: bool, where: str) -> bool:
    """Read a boolean key, refusing to coerce a non-bool.

    TOML has real booleans, so anything else here is a hand-edited mistake - and plain
    bool() turns the most likely one, `check = "false"`, into True: the opposite of what
    was written, silently. Warn and keep the default instead.
    """
    value = table.get(key, default)
    if isinstance(value, bool):
        return value
    log.warning("config: [%s] %s must be true or false, not %r; using %r",
                where, key, value, default)
    return default


_INT_MAX = 2**63 - 1   # what SQLite will hold; an upper bound nobody reaches by hand


def _as_int(
    table: dict, key: str, default: int, where: str, lo: int, hi: int, strict: bool = True
) -> int:
    """Read an integer key, refusing to coerce a non-int and bounding the range.

    The same argument as _as_bool, from the other side: bare int() coerces where TOML has
    a real type. `port = true` became port **1** (bool is an int in Python) and
    `port = 8765.7` silently truncated, both without a word. Out of range is the likelier
    mistake - a typo'd `port = 99999999` was taken as written and failed much later, from
    inside the bind, with an error naming neither the config nor the key.
    """
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        # A wrong *type* fails the load, which is what `port = "abc"` already did through
        # int() and the ConfigError wrapper: the daemon refuses to start and names the key.
        # Out of range below is different - there the default is a sane answer to fall back
        # on, and for a retention setting it is the conservative one.
        # `strict=False` is for a per-item value inside a loop, where failing the load would
        # charge one bad entry to every port (registry class 16): warn, keep the default,
        # and leave the rest of the file working.
        if not strict:
            log.warning("config: [%s] %s must be a whole number, not %r; using %r",
                        where, key, value, default)
            return default
        # ValueError, not ConfigError: load_config's wrapper turns it into a ConfigError
        # that names the file, which is the whole point of the friendly message.
        raise ValueError(f"[{where}] {key} must be a whole number, not {value!r}")
    if not lo <= value <= hi:
        log.warning("config: [%s] %s must be %d..%d, not %r; using %r",
                    where, key, lo, hi, value, default)
        return default
    return value


def _as_str(table: dict, key: str, default: str | None, where: str, strict: bool = True):
    """Read a string key, refusing to coerce a non-string.

    The third side of _as_int and _as_bool, and the one they left open: every string key was
    read bare, so `db_path = 5` loaded fine and then died inside resolve_db_path with an
    AttributeError naming neither the file nor the key - the exact failure _as_int exists to
    prevent. `strict=False` warns and keeps the default for a per-item value inside a loop,
    so one bad entry is not charged to every port (class 16).
    """
    value = table.get(key, default)
    if value is None or isinstance(value, str):
        return value
    if not strict:
        log.warning("config: [%s] %s must be text, not %r; using %r", where, key, value, default)
        return default
    raise ValueError(f"[{where}] {key} must be text, not {value!r}")


MIN_DB_CAP_BYTES = 1 << 20   # 1 MiB; server.py imports this so one floor governs both paths


def _as_cap(table: dict, key: str, default: int) -> int:
    """max_db_bytes: 0 means no cap, anything else must clear the floor."""
    value = _as_int(table, key, default, "storage", 0, _INT_MAX)
    if value and value < MIN_DB_CAP_BYTES:
        log.warning("config: [storage] %s must be 0 (no cap) or at least %d bytes, not %r; "
                    "using %r", key, MIN_DB_CAP_BYTES, value, default)
        return default
    return value


def _check_shape(data: dict) -> None:
    """Reject a wrong-shaped section before any key is read.

    The per-key helpers below all assume a table (or, for ports, a list of them), so a
    hand-edited `server = 3` or `ports = "oops"` failed with "'int' object has no
    attribute 'get'", naming neither the key nor the fix. The write path's _table()
    already says it properly; the load path says the same thing here.
    """
    for name in ("server", "storage", "update"):
        section = data.get(name)
        if section is not None and not isinstance(section, dict):
            raise ConfigError(f"config key [{name}] is not a table; fix the file by hand")
    ports = data.get("ports")
    if ports is not None and (
        not isinstance(ports, list) or not all(isinstance(p, dict) for p in ports)
    ):
        raise ConfigError(
            "config key [[ports]] is not an array of tables; fix the file by hand"
        )


def _from_dict(data: dict) -> Config:
    _check_shape(data)
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
        host=_as_str(server_d, "host", ServerConfig.host, "server"),
        port=_as_int(server_d, "port", ServerConfig.port, "server", 1, 65535),
    )
    storage = StorageConfig(
        db_path=_as_str(storage_d, "db_path", StorageConfig.db_path, "storage"),
        # Bounded below because the sweep computes `now - retention_days * 86400`, so a zero
        # or negative value puts the cutoff in the future and the first sweep deletes the
        # entire capture. The write-back API already bounds this (ge=1); a hand-edited file
        # is exactly the path that never sees that validation. The upper bounds here are
        # deliberately far out of reach: falling back to a default is the safe answer for a
        # port, which then fails loudly, but for a retention window it would silently delete
        # data the value was written to keep.
        retention_days=_as_int(storage_d, "retention_days", StorageConfig.retention_days,
                               "storage", 1, _INT_MAX),
        # 0 (no cap) or at least MIN_DB_CAP_BYTES, the same rule PUT /config/storage
        # enforces: the trim targets 90% of the cap, so a hand-edited `max_db_bytes = 1000`
        # empties the capture on the first sweep. Same argument as retention_days above.
        max_db_bytes=_as_cap(storage_d, "max_db_bytes", StorageConfig.max_db_bytes),
        min_sessions=_as_int(storage_d, "min_sessions", StorageConfig.min_sessions,
                             "storage", 0, _INT_MAX),
        auto_session=_as_bool(storage_d, "auto_session", StorageConfig.auto_session, "storage"),
    )
    update = UpdateConfig(check=_as_bool(update_d, "check", UpdateConfig.check, "update"))
    ports: list[PortConfig] = []
    for i, entry in enumerate(ports_d):
        alias = entry.get("alias")
        if not alias:
            # A port without an alias is unusable; say so instead of vanishing it.
            log.warning("config: [[ports]] entry %d has no alias, skipping it", i + 1)
            continue
        if not isinstance(alias, str) or not ALIAS_RE.fullmatch(alias):
            # Not str(alias): the grammar check passed on the coercion while the raw value was
            # stored, so `alias = 123` attached a port under a key no string lookup reaches.
            log.warning("config: port alias %r is invalid, skipping it", alias)
            continue
        # Coerced before the guard below, not inside the constructor after it: a non-string
        # device is truthy, so it passed the guard and was then nulled, leaving exactly the
        # unusable port the guard exists to reject.
        device = _as_str(entry, "device", None, f"ports.{alias}", strict=False)
        serial_number = _as_str(entry, "serial_number", None, f"ports.{alias}", strict=False)
        if not device and not serial_number:
            # Without either, the reader thread would retry forever on nothing.
            log.warning(
                "config: port %r has neither device nor serial_number, skipping it", alias
            )
            continue
        ports.append(
            PortConfig(
                alias=alias,
                device=device,
                serial_number=serial_number,
                # The same two helpers as the sections above. `autoconnect = "false"` is
                # the very string _as_bool was written for, and bare bool() read it as
                # True: the port then opened itself on every start, which is the setting's
                # exact opposite. `baud = true` became **1 baud**, a port that can never
                # talk to anything. Not strict, because one bad entry must not take the
                # whole file down (class 16).
                baud=_as_int(entry, "baud", PortConfig.baud, f"ports.{alias}", 1, _INT_MAX,
                             strict=False),
                autoconnect=_as_bool(entry, "autoconnect", PortConfig.autoconnect,
                                     f"ports.{alias}"),
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
        # utf-8-sig for the same reason as load_config; _write_doc then writes the file
        # back without the BOM, which is what TOML wants anyway.
        return tomlkit.parse(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # tomlkit raises its own parse error hierarchy
        raise ConfigError(f"{path}: cannot rewrite invalid TOML: {exc}") from exc


def replace_atomic(src: str | Path, dst: str | Path, attempts: int = 10) -> None:
    """`os.replace(src, dst)`, retrying the sharing violations only Windows produces.

    POSIX rename(2) does not care who has either file open, so this is one call there and
    the loop never runs. Windows fails the replace outright while any other process holds
    a handle without FILE_SHARE_DELETE: WinError 5 when the destination is open (an
    on-access antivirus scan, the Search indexer, an editor looking at config.toml) and
    WinError 32 when the source is. Those handles are usually gone within a few tens of
    milliseconds, so a bounded retry turns a spurious failure into a normal write. A
    handle that is genuinely held - the user editing the file in Notepad - still fails,
    with the real error, which is the honest answer.
    """
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.02 * (attempt + 1))   # 0.9 s in total across the 10 attempts


def _write_doc(path: Path, doc: tomlkit.TOMLDocument) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    # newline="" so the LF tomlkit emits is written verbatim. The default translates it to
    # CRLF on Windows, so a single settings save from the web UI rewrote every line of a
    # hand-edited config file.
    tmp.write_text(tomlkit.dumps(doc), encoding="utf-8", newline="")
    replace_atomic(tmp, path)


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
