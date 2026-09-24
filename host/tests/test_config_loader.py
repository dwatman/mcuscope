"""The config loader (`config.py`, SPEC 3.3): typed values, friendly refusals, and a warning
for every key it does not recognise.

Four typos in one file (`prot`, `[storge]`, `retention_dayz`, `max_db_byte`) loaded clean
with no warning of any kind: the daemon bound 8558 instead of 18605 and wrote the default
capture instead of the configured one. Everything else in the loader is meticulous about
saying what it did, and a misspelling is the likeliest hand edit there is."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcuscope import config as config_mod
from mcuscope import daemon as daemon_mod
from mcuscope import serial_link
from mcuscope.config import MIN_DB_CAP_BYTES, ConfigError, StorageConfig, load_config
from mcuscope.serial_link import SerialPort
from tests.support import free_port
from tests.test_config_api_revision import _app, _client


def _write(tmp_path, body: str) -> str:
    cfg = tmp_path / "config.toml"
    cfg.write_text(body, encoding="utf-8", newline="\n")
    return str(cfg)


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


TYPOS = """
prot = 18605

[server]
hostt = "127.0.0.1"

[storge]
retention_days = 3

[storage]
retention_dayz = 3
max_db_byte = 2000000

[update]
checkk = true

[plotjuggler]
destination = "127.0.0.1:9870"

[[ports]]
alias = "board"
device = "COM7"
baudrate = 115200
"""


def test_every_unrecognised_key_is_named_with_its_section(tmp_path, caplog) -> None:
    path = _write(tmp_path, TYPOS)
    with caplog.at_level(logging.WARNING, logger="mcuscope.config"):
        cfg = load_config(path)
    warned = _warnings(caplog)
    expected = {
        "prot": "the config file",
        "storge": "the config file",
        "hostt": "[server]",
        "retention_dayz": "[storage]",
        "max_db_byte": "[storage]",
        "checkk": "[update]",
        "destination": "[plotjuggler]",
        "baudrate": "[[ports]] 'board'",
    }
    for key, where in expected.items():
        assert any(repr(key) in w and where in w for w in warned), (key, warned)
    # The hint is what turns a warning into a fix.
    assert any("did you mean 'retention_days'?" in w for w in warned), warned
    assert any("did you mean 'baud'?" in w for w in warned), warned
    # Warned, never refused: the write-back path preserves unknown keys deliberately, so a
    # file written by a newer version has to keep loading.
    assert cfg.server.port == 8558, "the typo'd key really is ignored"
    assert cfg.storage.retention_days == 10
    assert [p.alias for p in cfg.ports] == ["board"]


def test_a_fully_populated_correct_config_warns_about_nothing(tmp_path, caplog) -> None:
    """The half that fails when the known-key table goes stale: a key the loader reads but
    the table does not list would be warned about on a correct file."""
    path = _write(
        tmp_path,
        "[server]\n"
        'host = "127.0.0.1"\n'
        "port = 18605\n"
        "[storage]\n"
        'db_path = "/tmp/mcuscope-test.db"\n'
        "retention_days = 3\n"
        f"max_db_bytes = {MIN_DB_CAP_BYTES}\n"
        "min_sessions = 2\n"
        "auto_session = false\n"
        "[update]\n"
        "check = false\n"
        "[plotjuggler]\n"
        "enabled = true\n"
        'dest = "127.0.0.1:9870"\n'
        "[[ports]]\n"
        'alias = "board"\n'
        'device = "COM7"\n'
        'serial_number = "066BFF3"\n'
        "baud = 9600\n"
        "autoconnect = false\n"
        "identify = false\n"
        'eol = "crlf"\n',
    )
    with caplog.at_level(logging.WARNING, logger="mcuscope.config"):
        cfg = load_config(path)
    assert _warnings(caplog) == []
    assert cfg.server.port == 18605 and cfg.ports[0].baud == 9600


def test_a_port_entry_with_no_alias_is_still_placed(tmp_path, caplog) -> None:
    """The entry is named by its position when it has no alias to name it by."""
    path = _write(tmp_path, '[[ports]]\ndevice = "COM7"\nbaudrate = 9600\n')
    with caplog.at_level(logging.WARNING, logger="mcuscope.config"):
        load_config(path)
    assert any("'baudrate'" in w and "[[ports]] 1" in w for w in _warnings(caplog))


def test_the_ignored_token_key_is_not_also_an_unknown_key(tmp_path, caplog) -> None:
    """`server.token` has its own warning pointing at the environment variable; it must not
    collect a second one telling the user it is a typo."""
    path = _write(tmp_path, '[server]\ntoken = "secret"\n')
    with caplog.at_level(logging.WARNING, logger="mcuscope.config"):
        load_config(path)
    warned = _warnings(caplog)
    assert any("MCUSCOPED_TOKEN" in w for w in warned), warned
    assert not any("unknown key" in w for w in warned), warned


def test_a_padded_serial_number_and_device_load_stripped_and_resolve(tmp_path, monkeypatch):
    cfg = tmp_path / "pad.toml"
    cfg.write_text(
        '[[ports]]\nalias = "b"\nserial_number = " 0672FF3 "\n'
        '[[ports]]\nalias = "d"\ndevice = " /dev/ttyACM0 "\n'
        '[[ports]]\nalias = "e"\ndevice = "   "\n',
        encoding="utf-8", newline="\n",
    )
    warnings: list[str] = []
    ports = {p.alias: p for p in load_config(cfg, warnings=warnings).ports}
    assert ports["b"].serial_number == "0672FF3" and ports["b"].device is None
    assert ports["d"].device == "/dev/ttyACM0" and ports["d"].serial_number is None
    assert "e" not in ports
    assert warnings == ["config: port 'e' has neither device nor serial_number, skipping it"]
    monkeypatch.setattr(serial_link, "cached_comports", lambda *a, **k: [
        SimpleNamespace(device="/dev/ttyACM3", serial_number="0672FF3")])
    port = SerialPort(None, None, "b", serial_number=ports["b"].serial_number)
    assert port._resolve_device() == "/dev/ttyACM3"


# -- config loading ----------------------------------------------------------------------


def test_bad_toml_is_a_friendly_error(tmp_path) -> None:
    from mcuscope.config import ConfigError, load_config
    from mcuscope.daemon import main as daemon_main

    cfg = tmp_path / "config.toml"
    cfg.write_text("[server\nport = not-an-int", encoding="utf-8", newline="\n")
    with pytest.raises(ConfigError):
        load_config(cfg)
    # daemon entry point turns it into exit code 1, not a traceback
    assert daemon_main(["-c", str(cfg)]) == 1


def test_bad_config_value_is_a_friendly_error(tmp_path) -> None:
    from mcuscope.config import ConfigError, load_config

    cfg = tmp_path / "config.toml"
    cfg.write_text('[server]\nport = "abc"\n', encoding="utf-8", newline="\n")
    with pytest.raises(ConfigError):
        load_config(cfg)


def test_unusable_port_entries_are_skipped_with_warning(tmp_path, caplog) -> None:
    from mcuscope.config import load_config

    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[[ports]]\n"
        'device = "COM7"\n'          # no alias
        "[[ports]]\n"
        'alias = "empty"\n'          # neither device nor serial_number
        "[[ports]]\n"
        'alias = "good"\n'
        'device = "COM8"\n',
        encoding="utf-8", newline="\n",
    )
    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="mcuscope.config"):
        config = load_config(cfg)
    assert [pc.alias for pc in config.ports] == ["good"]
    assert any("no alias" in r.message for r in caplog.records)
    assert any("neither device nor serial_number" in r.message for r in caplog.records)


def test_token_in_config_file_is_ignored_with_warning(tmp_path, caplog) -> None:
    # SPEC 3.3: the token is runtime-only; a file key is ignored, loudly, so the
    # UI-writable config surface can never grant or revoke authentication.
    import logging as _logging

    from mcuscope.config import load_config

    cfg = tmp_path / "config.toml"
    cfg.write_text('[server]\ntoken = "secret"\nhost = "0.0.0.0"\n', encoding="utf-8", newline="\n")
    with caplog.at_level(_logging.WARNING, logger="mcuscope.config"):
        loaded = load_config(cfg)
    assert loaded.server.token is None
    assert loaded.server.host == "0.0.0.0"
    assert any("MCUSCOPED_TOKEN" in r.message for r in caplog.records)


def test_config_rejects_invalid_alias(tmp_path, caplog) -> None:
    import logging as _logging

    from mcuscope.config import load_config

    cfg = tmp_path / "config.toml"
    cfg.write_text('[[ports]]\nalias = "a/b"\ndevice = "COM7"\n', encoding="utf-8", newline="\n")
    with caplog.at_level(_logging.WARNING, logger="mcuscope.config"):
        config = load_config(cfg)
    assert config.ports == []
    assert any("invalid" in r.message for r in caplog.records)


# -- config ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, -7])
def test_retention_days_is_clamped_to_at_least_one(tmp_path, value: int) -> None:
    """A hand-edited retention_days <= 0 put the cutoff in the future and deleted everything.

    The write-back API bounds this (ge=1); the file loader is the path that never sees
    that validation, and it clamped max_db_bytes and min_sessions but not this one.
    """
    cfg = tmp_path / "config.toml"
    cfg.write_text(f"[storage]\nretention_days = {value}\n", encoding="utf-8", newline="\n")
    assert load_config(str(cfg)).storage.retention_days >= 1


def test_config_integers_are_not_coerced(tmp_path) -> None:
    """The int() half of the `check = "false"` defect, which _as_bool fixed only for bools.

    TOML has real types, so anything else here is a hand-edited mistake, and bare int()
    took each one as written: `port = true` became port **1** (a bool is an int in
    Python), `port = 8558.7` truncated in silence, and a typo'd `port = 99999999` was
    accepted and then failed much later from inside the bind, naming neither the config
    file nor the key.
    """
    cfg = tmp_path / "config.toml"
    # A wrong type fails the load and names the key, the way `port = "abc"` already did.
    for value in ("true", "8558.7", '"9000"'):
        cfg.write_text(f"[server]\nport = {value}\n", encoding="utf-8", newline="\n")
        with pytest.raises(ConfigError, match="whole number"):
            load_config(str(cfg))
    # Out of range falls back to the default instead, with a warning: there is a sane
    # answer to fall back on, and for a retention setting it is the conservative one.
    for value in ("99999999", "0", "-1"):
        cfg.write_text(f"[server]\nport = {value}\n", encoding="utf-8", newline="\n")
        assert load_config(str(cfg)).server.port == 8558, f"port = {value} was taken"
    # A real, in-range integer still lands, so the guard is not simply refusing everything.
    cfg.write_text("[server]\nport = 9000\n", encoding="utf-8", newline="\n")
    assert load_config(str(cfg)).server.port == 9000
    cfg.write_text("[storage]\nmin_sessions = -1\n", encoding="utf-8", newline="\n")
    assert load_config(str(cfg)).storage.min_sessions == StorageConfig.min_sessions


def test_port_entries_are_typed_and_one_bad_entry_stays_local(tmp_path, caplog) -> None:
    """The ports loop kept both coercions after the sections above were fixed.

    `baud = true` became **1 baud**, a port that can never talk: warned about and defaulted.
    `autoconnect = "false"` is the very string `_as_bool` was written for, and a fallback to
    the default True opens the port, the setting's exact opposite: that entry is skipped.
    Either way the neighbour loads: charging one bad entry to the whole file is class 16.
    """
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[[ports]]\nalias = "board"\ndevice = "COM7"\nbaud = true\n'
        '[[ports]]\nalias = "lazy"\ndevice = "COM9"\nautoconnect = "false"\n'
        '[[ports]]\nalias = "good"\ndevice = "COM8"\nbaud = 9600\n',
        encoding="utf-8", newline="\n",
    )
    with caplog.at_level(logging.WARNING, logger="mcuscope.config"):
        ports = load_config(str(cfg)).ports
    assert [(p.alias, p.baud) for p in ports] == [("board", 115200), ("good", 9600)]
    assert sum("ports.board" in r.message for r in caplog.records) == 1
    assert any("'lazy' autoconnect" in r.message and "skipping" in r.message
               for r in caplog.records), "the skipped entry must be named"
    # The neighbour is untouched, which is the half a hard failure would have destroyed.
    assert ports[1].device == "COM8"


def test_config_with_a_utf8_bom_loads(tmp_path) -> None:
    """PowerShell's `Out-File -Encoding utf8` always writes a BOM, and plenty of Windows
    editors do too. The TOML parser rejects one at line 1, column 1 with a message that
    names neither the cause nor the fix, so hand-editing config.toml the obvious way
    on Windows left the daemon refusing to start over an invisible character."""
    import tomlkit

    from mcuscope.config import _read_doc, _write_doc, load_config

    path = tmp_path / "config.toml"
    body = '[server]\nhost = "127.0.0.1"\nport = 8791\n'
    path.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))

    cfg = load_config(path)
    assert cfg.server.port == 8791 and cfg.server.host == "127.0.0.1"

    # The write-back path parses it too, and normalises the BOM away on save.
    doc = _read_doc(path)
    doc["server"]["port"] = 8888
    _write_doc(path, doc)
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert load_config(path).server.port == 8888
    assert tomlkit.parse(path.read_text(encoding="utf-8"))["server"]["port"] == 8888


# -- F4: an unreadable config is a startup failure, not a traceback ---------------------


def test_a_config_path_that_is_a_directory_names_the_file(tmp_path) -> None:
    """`exists()` then `read_text()`: a directory (a typo'd MCUSCOPED_CONFIG, or the path
    of a config dir) raised IsADirectoryError straight out of load_config."""
    d = tmp_path / "config.toml"
    d.mkdir()
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(str(d))
    assert str(d) in str(pytest.raises(ConfigError, load_config, str(d)).value)


@pytest.mark.skipif(sys.platform == "win32", reason="chmod 000 does not deny reads on Windows")
def test_an_unreadable_config_file_names_the_file(tmp_path) -> None:
    """A config owned by root on a shared bench: PermissionError, not a refusal."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("[server]\nport = 9000\n", encoding="utf-8", newline="\n")
    cfg.chmod(0o000)
    if os.access(cfg, os.R_OK):   # running as root: the mode is not enforced
        pytest.skip("root reads a 000 file, so the OSError cannot be provoked this way")
    try:
        with pytest.raises(ConfigError, match="cannot read"):
            load_config(str(cfg))
    finally:
        cfg.chmod(0o600)


# -- RG-F17: the loader's baud ceiling matches the API's --------------------------------


def test_a_baud_the_api_refuses_is_not_loaded(tmp_path, caplog) -> None:
    """The loader took baud=999999999 while ConfigPortEntry refuses it, so the settings
    dialog's ports save 422'd on an entry the daemon had started with."""
    import logging

    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[[ports]]\nalias = "fast"\ndevice = "COM7"\nbaud = 999999999\n'
        '[[ports]]\nalias = "good"\ndevice = "COM8"\nbaud = 9600\n',
        encoding="utf-8", newline="\n",
    )
    with caplog.at_level(logging.WARNING, logger="mcuscope.config"):
        ports = load_config(str(cfg)).ports
    assert [p.alias for p in ports] == ["good"], "the unsaveable port was loaded anyway"
    assert any("skipping" in r.message and "fast" in r.message for r in caplog.records)


# -- A-5: warnings once, and on /status ---------------------------------------------------

TYPO = '[server]\nprot = 18605\n[storge]\ndb_path = "x"\n[storage]\nretention_days = 0\n'


def _unknown(records) -> list[str]:
    return [r.getMessage() for r in records if "unknown key" in r.getMessage()]


def test_warnings_are_logged_once_at_startup_and_served_on_status(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    cfg = tmp_path / "typo.toml"
    cfg.write_text(TYPO, encoding="utf-8", newline="\n")
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app: str(tmp_path / "data"))
    seen: dict = {}

    def fake_serve(app, **kw):
        with _client(app) as c:
            seen["status"] = c.get("/status").json()["config_warnings"]
            for _ in range(2):
                assert c.get("/config").status_code == 200
            ports = {"ports": [{"alias": "b", "device": "/dev/ttyACM0"}]}
            assert c.put("/config/ports", json=ports).status_code == 200
            # The file changes after startup: /status keeps the config it started with.
            cfg.write_text(TYPO + "[updat]\n", encoding="utf-8", newline="\n")
            c.get("/config")
            seen["status_after"] = c.get("/status").json()["config_warnings"]

    monkeypatch.setattr(daemon_mod, "_serve", fake_serve)
    with caplog.at_level(logging.WARNING, logger="mcuscope.config"):
        assert daemon_mod.main(["-c", str(cfg), "--port", str(free_port())]) in (0, None)

    logged = _unknown(caplog.records)
    assert len(logged) == 2, logged   # prot and storge, once each, across 3 reads and a save
    assert any("'prot'" in m and "did you mean 'port'" in m for m in logged), logged
    # Not only unknown keys: every loader warning for that file.
    assert any("retention_days" in m for m in seen["status"]), seen
    assert sorted(seen["status"]) == sorted(
        r.getMessage() for r in caplog.records if r.name == "mcuscope.config"
    )
    assert seen["status_after"] == seen["status"]
    assert not any("updat" in m for m in seen["status_after"])


def test_status_config_warnings_empty_for_a_clean_config(tmp_path: Path) -> None:
    with _client(_app(tmp_path)) as c:
        assert c.get("/status").json()["config_warnings"] == []
    with _client(_app(tmp_path, config_warnings=["config: x"])) as c:
        assert c.get("/status").json()["config_warnings"] == ["config: x"]


def test_load_config_still_logs_without_a_sink(tmp_path: Path, caplog) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text("[server]\nprot = 1\n", encoding="utf-8", newline="\n")
    with caplog.at_level(logging.WARNING, logger="mcuscope.config"):
        config_mod.load_config(cfg)
    assert len(_unknown(caplog.records)) == 1
    caplog.clear()
    sink: list[str] = []
    with caplog.at_level(logging.WARNING, logger="mcuscope.config"):
        config_mod.load_config(cfg, warnings=sink)
        # The sink is per call: a later plain load logs again.
        config_mod.load_config(cfg)
    assert len(sink) == 1 and "prot" in sink[0]
    assert len(_unknown(caplog.records)) == 1
