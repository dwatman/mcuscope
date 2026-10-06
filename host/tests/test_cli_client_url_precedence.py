"""Where every `mcu` command finds the daemon: --url, MCUSCOPE_URL, the config's [server],
then the default (SPEC 4, ruling OP-6)."""

from __future__ import annotations

import os
import subprocess

import pytest

from mcuscope import cli
from mcuscope.cli_client import DEFAULT_URL, resolve_url
from tests.support import free_port


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """An empty config dir as the default one, with no URL or config in the environment."""
    d = tmp_path / "cfg"
    d.mkdir()
    monkeypatch.setenv("MCUSCOPE_CONFIG_DIR", str(d))
    monkeypatch.delenv("MCUSCOPE_URL", raising=False)
    monkeypatch.delenv("MCUSCOPED_CONFIG", raising=False)
    return d


def _server(path, host: str, port: int) -> None:
    path.write_text(f'[server]\nhost = "{host}"\nport = {port}\n', encoding="utf-8")


def test_no_config_is_the_default(config_dir) -> None:
    assert resolve_url(None) == (DEFAULT_URL, "default", None)


def test_the_default_configs_server_is_used(config_dir) -> None:
    _server(config_dir / "config.toml", "127.0.0.2", 18911)
    assert resolve_url(None) == ("http://127.0.0.2:18911", "config", "127.0.0.2")


@pytest.mark.parametrize(("host", "url"), [
    ("0.0.0.0", "http://127.0.0.1:18911"),
    ("::", "http://[::1]:18911"),
    ("::1", "http://[::1]:18911"),
    ("bench.local", "http://bench.local:18911"),
])
def test_a_wildcard_bind_is_reached_on_loopback(config_dir, host, url) -> None:
    _server(config_dir / "config.toml", host, 18911)
    assert resolve_url(None) == (url, "config", host)


def test_mcuscoped_config_names_the_file(config_dir, tmp_path, monkeypatch) -> None:
    _server(config_dir / "config.toml", "127.0.0.1", 18911)
    other = tmp_path / "other.toml"
    _server(other, "127.0.0.1", 18912)
    monkeypatch.setenv("MCUSCOPED_CONFIG", str(other))
    assert resolve_url(None)[0] == "http://127.0.0.1:18912"
    # An explicit path (daemon start --config) wins over the environment's.
    third = tmp_path / "third.toml"
    _server(third, "127.0.0.1", 18913)
    assert resolve_url(None, str(third))[0] == "http://127.0.0.1:18913"


def test_env_beats_the_config_and_the_flag_beats_both(config_dir, monkeypatch) -> None:
    _server(config_dir / "config.toml", "127.0.0.1", 18911)
    monkeypatch.setenv("MCUSCOPE_URL", "http://127.0.0.1:18914/")
    assert resolve_url(None) == ("http://127.0.0.1:18914", "env", None)
    assert resolve_url("http://127.0.0.1:18915") == ("http://127.0.0.1:18915", "flag", None)


def test_an_unreadable_config_warns_and_falls_back(config_dir, capsys) -> None:
    (config_dir / "config.toml").write_text("[server\n", encoding="utf-8")
    assert resolve_url(None) == (DEFAULT_URL, "default", None)
    err = capsys.readouterr().err
    assert "warning:" in err and "invalid TOML" in err and DEFAULT_URL in err


def test_a_command_uses_the_configs_address(config_dir, capsys) -> None:
    port = free_port()
    _server(config_dir / "config.toml", "0.0.0.0", port)
    assert cli.main(["status"]) == 3
    err = capsys.readouterr().err
    assert f"unreachable at http://127.0.0.1:{port}" in err
    # The config's address is one `mcu daemon start` would serve, so the hint is given.
    assert "mcu daemon start" in err


def test_local_commands_do_not_read_the_config(config_dir, capsys) -> None:
    (config_dir / "config.toml").write_text("[server\n", encoding="utf-8")
    assert cli.main(["ai-guide"]) == 0
    assert "warning" not in capsys.readouterr().err


# -- mcu daemon start ---------------------------------------------------------------------


class _Spawned(Exception):
    pass


def _start_args(monkeypatch, argv: list[str]) -> list[str]:
    """The mcuscoped argv `mcu daemon start` would spawn, captured before anything runs."""
    seen: list[list[str]] = []

    def popen(args, **kw):
        seen.append(list(args))
        raise _Spawned

    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(_Spawned):
        cli.main(["daemon", "start", *argv])
    return seen[0]


def test_start_on_the_configs_address_passes_no_host(config_dir, monkeypatch) -> None:
    port = free_port()
    _server(config_dir / "config.toml", "0.0.0.0", port)
    args = _start_args(monkeypatch, [])
    assert "--host" not in args and "--port" not in args, args   # the config's bind stands


def test_start_config_names_the_address_it_serves(config_dir, tmp_path, monkeypatch) -> None:
    named = tmp_path / "bench.toml"
    port = free_port()
    _server(named, "0.0.0.0", port)
    args = _start_args(monkeypatch, ["--config", str(named)])
    assert args[-2:] == ["--config", str(named)] and "--host" not in args, args


def test_start_with_an_explicit_url_binds_it(config_dir, monkeypatch) -> None:
    _server(config_dir / "config.toml", "0.0.0.0", free_port())
    port = free_port()
    args = _start_args(monkeypatch, ["--url", f"http://127.0.0.1:{port}"])
    i = args.index("--host")
    assert args[i:i + 4] == ["--host", "127.0.0.1", "--port", str(port)], args
    monkeypatch.setenv("MCUSCOPE_URL", f"http://127.0.0.1:{port}")
    args = _start_args(monkeypatch, [])
    assert "--host" in args, args


def test_the_pid_record_is_keyed_by_the_bind_host(config_dir) -> None:
    from mcuscope.cli_client import Settings
    from mcuscope.cli_daemonctl import _pid_file

    s = Settings(url="http://127.0.0.1:18916", json_out=False, port=None,
                 url_from="config", bind_host="0.0.0.0")
    assert os.path.basename(_pid_file(s)) == "mcuscoped-0.0.0.0-18916.pid"
    plain = Settings(url="http://127.0.0.1:18916", json_out=False, port=None)
    assert os.path.basename(_pid_file(plain)) == "mcuscoped-127.0.0.1-18916.pid"


def test_a_start_off_the_default_address_says_how_to_reach_it(
    config_dir, tmp_path, monkeypatch, capsys
) -> None:
    named = tmp_path / "bench.toml"
    port = free_port()
    _server(named, "127.0.0.1", port)
    monkeypatch.setattr(cli, "_await_spawned", lambda *a, **kw: None)

    class _Proc:
        pid = 4242

        def poll(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: _Proc())
    assert cli.main(["daemon", "start", "--config", str(named)]) == 0
    err = capsys.readouterr().err
    assert f"this daemon serves http://127.0.0.1:{port}" in err
    assert f"MCUSCOPED_CONFIG={named}" in err


def test_no_note_when_later_commands_find_it_anyway(config_dir, monkeypatch, capsys) -> None:
    _server(config_dir / "config.toml", "127.0.0.1", free_port())
    monkeypatch.setattr(cli, "_await_spawned", lambda *a, **kw: None)

    class _Proc:
        pid = 4242

        def poll(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: _Proc())
    named = str(config_dir / "config.toml")
    assert cli.main(["daemon", "start", "--config", named]) == 0
    assert "this daemon serves" not in capsys.readouterr().err


@pytest.mark.parametrize("named", [True, False])
def test_a_start_on_an_unreadable_config_refuses_before_probing(config_dir, tmp_path,
                                                                monkeypatch, capsys,
                                                                named) -> None:
    """Falling back to the default address could find another daemon there and report
    "already running" instead of the config's error."""
    import httpx

    from tests.support import STATUS, canned

    bad = (tmp_path / "bad.toml") if named else (config_dir / "config.toml")
    bad.write_text("[server\n", encoding="utf-8")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=STATUS)

    canned(monkeypatch, handler)
    rc = cli.main(["daemon", "start", *(["--config", str(bad)] if named else [])])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "invalid TOML" in err and "already running" not in err, err
    assert seen == [], "refused before any daemon was asked"
