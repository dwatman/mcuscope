"""Improvement 2: an unrecognised config key is warned about, never silently ignored.

Four typos in one file (`prot`, `[storge]`, `retention_dayz`, `max_db_byte`) loaded clean
with no warning of any kind: the daemon bound 8558 instead of 18605 and wrote the default
capture instead of the configured one. Everything else in the loader is meticulous about
saying what it did, and a misspelling is the likeliest hand edit there is.
"""

from __future__ import annotations

import logging

from mcuscope.config import MIN_DB_CAP_BYTES, load_config


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
