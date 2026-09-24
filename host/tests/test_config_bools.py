"""C7: a per-port boolean that is not a boolean skips the port.

`autoconnect` and `identify` both default to True, so falling back on a typo resolves
`autoconnect = "false"` towards opening the port - the setting's exact opposite, and on a
bench that drives DTR/RTS. The load still survives: one bad entry stays local.
"""

from __future__ import annotations

import logging

import pytest

from mcuscope.config import ConfigError, load_config


def _write(tmp_path, body: str):
    cfg = tmp_path / "config.toml"
    cfg.write_text(body, encoding="utf-8", newline="\n")
    return str(cfg)


@pytest.mark.parametrize("bad", ['autoconnect = "false"', "identify = 1", "autoconnect = 0"])
def test_a_non_bool_port_flag_skips_the_port(tmp_path, caplog, bad) -> None:
    path = _write(
        tmp_path,
        f'[[ports]]\nalias = "board"\ndevice = "COM7"\n{bad}\n'
        '[[ports]]\nalias = "good"\ndevice = "COM8"\n',
    )
    with caplog.at_level(logging.WARNING, logger="mcuscope.config"):
        ports = load_config(path).ports
    assert [p.alias for p in ports] == ["good"], "never loaded at the default True"
    assert any("board" in r.message and "skipping" in r.message for r in caplog.records)


def test_real_booleans_still_load(tmp_path) -> None:
    path = _write(
        tmp_path,
        '[[ports]]\nalias = "board"\ndevice = "COM7"\n'
        "autoconnect = false\nidentify = false\n",
    )
    port = load_config(path).ports[0]
    assert (port.autoconnect, port.identify) == (False, False)


def test_config_refuses_to_coerce_a_quoted_boolean(tmp_path) -> None:
    """bool("false") is True, so a hand-edited `check = "false"` enabled the update check.

    A wrong-typed bool now fails the load naming the key (SPEC 3.3), like _as_int and
    _as_str: warning and defaulting still started the daemon with the setting the typo was
    meant to turn off, which SPEC 3.6 calls the wrong way to be wrong for `update.check`.
    """
    cfg_path = tmp_path / "config.toml"
    # 0 and "" are the discriminating cases: bool() reads both as False, silently turning
    # a malformed entry into a setting nobody chose. `check = "false"` is the likelier
    # typo and coerces the opposite way.
    for body, key in (
        ("[update]\ncheck = 0\n", "check"),
        ('[storage]\nauto_session = ""\n', "auto_session"),
        ('[update]\ncheck = "false"\n', "check"),
    ):
        cfg_path.write_text(body, encoding="utf-8", newline="\n")
        with pytest.raises(ConfigError, match=f"{key} must be true or false"):
            load_config(cfg_path)
    # A real TOML boolean is still honoured, both ways.
    cfg_path.write_text("[update]\ncheck = false\n", encoding="utf-8", newline="\n")
    assert load_config(cfg_path).update.check is False
    cfg_path.write_text("[update]\ncheck = true\n", encoding="utf-8", newline="\n")
    assert load_config(cfg_path).update.check is True
