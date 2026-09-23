"""mcuscoped's bind-host check, token sources and network-exposure warnings (SPEC 3.4)."""

from __future__ import annotations

import pytest

from mcuscope.config import Config, ConfigError
from mcuscope.daemon import _apply_overrides, _warn_if_exposed, build_parser


def _args(*argv: str):
    return build_parser().parse_args(list(argv))


@pytest.mark.parametrize("host", ["", " ", "\t", "a b", "local\x00host", "host\x7f"])
def test_a_host_flag_that_is_no_address_is_refused(host: str) -> None:
    with pytest.raises(ConfigError, match="--host must be a host name or address"):
        _apply_overrides(Config(), _args("--host", host))


def test_a_host_flag_is_applied_stripped() -> None:
    assert _apply_overrides(Config(), _args("--host", " 0.0.0.0 ")).server.host == "0.0.0.0"
    assert _apply_overrides(Config(), _args()).server.host == "127.0.0.1"


def test_a_short_token_on_a_network_bind_warns(capsys) -> None:
    _warn_if_exposed("0.0.0.0", "short")
    assert "shorter than 16" in capsys.readouterr().out
    _warn_if_exposed("0.0.0.0", "x" * 16)
    assert capsys.readouterr().out == ""


def test_a_tokenless_network_bind_warns(capsys) -> None:
    _warn_if_exposed("0.0.0.0", None)
    assert "UNAUTHENTICATED" in capsys.readouterr().out
    _warn_if_exposed("127.0.0.1", None)
    assert capsys.readouterr().out == ""


def test_the_env_token_is_applied_and_the_flag_wins(monkeypatch) -> None:
    monkeypatch.setenv("MCUSCOPED_TOKEN", "  from-env-0123456789  ")
    assert _apply_overrides(Config(), _args()).server.token == "from-env-0123456789"
    got = _apply_overrides(Config(), _args("--token", "from-flag-0123456789"))
    assert got.server.token == "from-flag-0123456789"
    monkeypatch.setenv("MCUSCOPED_TOKEN", "   ")
    assert _apply_overrides(Config(), _args()).server.token is None
