"""PlotJuggler UDP streaming tests (SPEC 3.7).

Four layers: pjstream unit tests (datagram format and every refusal), config
load/save, the REST pair (runtime vs saved, and the write-protection bar), and one
live-stack test proving sim plot lines arrive as datagrams end to end.
"""

from __future__ import annotations

import json
import socket
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from mcuscope import cli, pjstream
from mcuscope.config import Config, ConfigError, StorageConfig, load_config, save_plotjuggler
from mcuscope.daemon import _apply_overrides, build_parser
from mcuscope.pjstream import PlotJugglerStreamer, parse_dest
from mcuscope.server import create_app
from tests.support import Stack

# -- parse_dest -----------------------------------------------------------------------


def test_parse_dest_accepts_host_port() -> None:
    assert parse_dest("127.0.0.1:9870") == ("127.0.0.1", 9870)
    assert parse_dest(" example.com:1 ") == ("example.com", 1)


@pytest.mark.parametrize("bad", [
    "nocolon", ":9870", "host:", "host:abc", "host:0", "host:70000", "", "host:98.7",
])
def test_parse_dest_refuses(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_dest(bad)


# -- streamer -------------------------------------------------------------------------


def _udp_receiver() -> tuple[socket.socket, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(5.0)
    return sock, f"127.0.0.1:{sock.getsockname()[1]}"


POINTS = [
    {"tick_ms": 12345, "sid": "0", "name": "temp", "value": 25.1},
    {"tick_ms": 12345, "sid": "0", "name": "gpio.led", "value": 1.0},
]


def test_send_datagram_format() -> None:
    sock, dest = _udp_receiver()
    try:
        pj = PlotJugglerStreamer(enabled=True, dest=dest)
        pj.send("board", 1756270000.125, POINTS)
        msg = json.loads(sock.recv(65535).decode())
        # One datagram per line: ts primary, tick secondary, channels under the alias.
        assert msg == {
            "ts": 1756270000.125,
            "tick": 12.345,
            "board": {"temp": 25.1, "gpio.led": 1.0},
        }
        pj.close()
    finally:
        sock.close()


def test_disabled_streamer_sends_nothing() -> None:
    sock, dest = _udp_receiver()
    try:
        pj = PlotJugglerStreamer(enabled=False, dest=dest)
        pj.send("board", 1.0, POINTS)
        pj.configure(True, dest)
        pj.configure(False)
        pj.send("board", 2.0, POINTS)
        sock.settimeout(0.3)
        with pytest.raises(TimeoutError):
            sock.recv(65535)
    finally:
        sock.close()


def test_bad_configure_keeps_previous_state() -> None:
    sock, dest = _udp_receiver()
    try:
        pj = PlotJugglerStreamer(enabled=True, dest=dest)
        with pytest.raises(ValueError):
            pj.configure(True, "not-a-dest")
        with pytest.raises(OSError):
            # Numeric-looking but impossible: getaddrinfo fails without touching DNS.
            pj.configure(True, "256.256.256.256:9870")
        # Both refusals left the old destination live.
        pj.send("board", 3.0, POINTS)
        assert json.loads(sock.recv(65535).decode())["ts"] == 3.0
        pj.close()
    finally:
        sock.close()


def test_dest_changed_while_disabled_wins_on_enable() -> None:
    old_sock, old_dest = _udp_receiver()
    new_sock, new_dest = _udp_receiver()
    try:
        pj = PlotJugglerStreamer(enabled=True, dest=old_dest)
        pj.configure(False, new_dest)   # retarget while off...
        pj.configure(True)              # ...then a bare enable
        pj.send("board", 4.0, POINTS)
        # the datagram lands where the reported dest says, not at the stale resolution
        assert json.loads(new_sock.recv(65535).decode())["ts"] == 4.0
        old_sock.settimeout(0.3)
        with pytest.raises(TimeoutError):
            old_sock.recv(65535)
        pj.close()
    finally:
        old_sock.close()
        new_sock.close()


def test_constructor_with_dead_dest_disables_not_raises() -> None:
    pj = PlotJugglerStreamer(enabled=True, dest="256.256.256.256:9870")
    assert pj.enabled is False
    pj.send("board", 1.0, POINTS)   # and stays a no-op


def test_send_swallows_socket_errors() -> None:
    sock, dest = _udp_receiver()
    try:
        pj = PlotJugglerStreamer(enabled=True, dest=dest)
        pj._sock.close()   # yank the socket out from under it
        pj.send("board", 1.0, POINTS)   # must not raise: capture path calls this
    finally:
        sock.close()


def test_close_then_send_is_noop() -> None:
    pj = PlotJugglerStreamer(enabled=True, dest="127.0.0.1:9870")
    pj.close()
    assert pj.enabled is False
    pj.send("board", 1.0, POINTS)


# -- config load/save -----------------------------------------------------------------


def test_config_roundtrip(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("# header comment\n[server]\nport = 8765\n", encoding="utf-8")
    save_plotjuggler(cfg, True, "10.0.0.5:4000")
    loaded = load_config(cfg)
    assert loaded.plotjuggler.enabled is True
    assert loaded.plotjuggler.dest == "10.0.0.5:4000"
    # read-modify-write: hand content elsewhere survives
    text = cfg.read_text(encoding="utf-8")
    assert "# header comment" in text and "port = 8765" in text


def test_config_malformed_dest_warns_and_defaults(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('[plotjuggler]\nenabled = true\ndest = "no-port-here"\n', encoding="utf-8")
    loaded = load_config(cfg)
    # right type, bad value: fall back, keep the enable
    assert loaded.plotjuggler.dest == pjstream.DEFAULT_DEST
    assert loaded.plotjuggler.enabled is True


def test_config_wrong_type_dest_fails_load(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("[plotjuggler]\ndest = 9870\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(cfg)


def test_config_section_not_a_table_fails_load(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("plotjuggler = 3\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(cfg)


# -- REST -----------------------------------------------------------------------------


def _mk_app(tmp_path: Path):
    config = Config(storage=StorageConfig(db_path=str(tmp_path / "cap.db")))
    return create_app(config, config_path=tmp_path / "config.toml")


def _loopback(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1))


def test_rest_runtime_and_saved_are_separate(tmp_path: Path) -> None:
    app = _mk_app(tmp_path)
    with _loopback(app) as c:
        assert c.get("/plotjuggler").json() == {
            "enabled": False, "dest": pjstream.DEFAULT_DEST,
        }
        # runtime on: /status reports it, the file does not exist
        r = c.put("/plotjuggler", json={"enabled": True, "dest": "127.0.0.1:9333"})
        assert r.status_code == 200
        assert r.json() == {"enabled": True, "dest": "127.0.0.1:9333"}
        assert c.get("/status").json()["plotjuggler"]["enabled"] is True
        assert not (tmp_path / "config.toml").exists()
        # save: the file changes, the runtime endpoint answer does not
        r = c.put("/config/plotjuggler", json={"enabled": False, "dest": "127.0.0.1:9444"})
        assert r.json() == {"ok": True, "restart_required": False}
        saved = load_config(tmp_path / "config.toml").plotjuggler
        assert (saved.enabled, saved.dest) == (False, "127.0.0.1:9444")
        assert c.get("/plotjuggler").json() == {"enabled": True, "dest": "127.0.0.1:9333"}
        assert c.get("/config").json()["plotjuggler"] == {
            "enabled": False, "dest": "127.0.0.1:9444",
        }


def test_rest_bad_dest_is_400_and_state_holds(tmp_path: Path) -> None:
    app = _mk_app(tmp_path)
    with _loopback(app) as c:
        for bad in ("no-port", "host:0", "256.256.256.256:9870"):
            r = c.put("/plotjuggler", json={"enabled": True, "dest": bad})
            assert r.status_code == 400, bad
            assert c.get("/plotjuggler").json()["enabled"] is False
        r = c.put("/config/plotjuggler", json={"enabled": True, "dest": "no-port"})
        assert r.status_code == 400
        assert not (tmp_path / "config.toml").exists()


def test_rest_dest_omitted_keeps_previous(tmp_path: Path) -> None:
    app = _mk_app(tmp_path)
    with _loopback(app) as c:
        c.put("/plotjuggler", json={"enabled": True, "dest": "127.0.0.1:9333"})
        r = c.put("/plotjuggler", json={"enabled": False})
        assert r.json() == {"enabled": False, "dest": "127.0.0.1:9333"}
        r = c.put("/plotjuggler", json={"enabled": True})
        assert r.json() == {"enabled": True, "dest": "127.0.0.1:9333"}


def test_rest_put_is_denied_from_network_without_token(tmp_path: Path) -> None:
    app = _mk_app(tmp_path)
    # TestClient's default client host is "testclient", i.e. non-loopback.
    with TestClient(app) as c:
        r = c.put(
            "/plotjuggler",
            json={"enabled": True, "dest": "127.0.0.1:9333"},
            headers={"host": "127.0.0.1"},
        )
        assert r.status_code == 403
        r = c.put(
            "/config/plotjuggler",
            json={"enabled": True, "dest": "127.0.0.1:9333"},
            headers={"host": "127.0.0.1"},
        )
        assert r.status_code == 403


# -- end to end against the sim -------------------------------------------------------


def test_sim_plot_lines_arrive_as_datagrams(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--plot"])
    sock, dest = _udp_receiver()
    try:
        with httpx.Client(base_url=stack.base_url, timeout=5.0) as c:
            r = c.put("/plotjuggler", json={"enabled": True, "dest": dest})
            assert r.status_code == 200
            msg = json.loads(sock.recv(65535).decode())
            assert isinstance(msg["ts"], float)
            assert isinstance(msg["tick"], float)
            chans = msg["board"]
            assert chans and all(isinstance(v, (int, float)) for v in chans.values())
            # off means off: after the drain settles, nothing more arrives
            c.put("/plotjuggler", json={"enabled": False})
            sock.settimeout(0.3)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    sock.recv(65535)   # residue from before the toggle
                except TimeoutError:
                    break
            else:
                pytest.fail("datagrams kept arriving after disable")
    finally:
        sock.close()


def test_cli_plotjuggler_and_alias(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack()
    url = ["--url", stack.base_url]
    assert cli.main(["plotjuggler", "on", "127.0.0.1:9555", *url]) == 0
    assert cli.main(["pj", *url]) == 0                     # alias, show-state form
    assert cli.main(["pj", "off", *url]) == 0
    assert cli.main(["plotjuggler", "sideways", *url]) == 1  # not on/off
    assert cli.main(["plotjuggler", "--save", *url]) == 1    # nothing to save
    assert cli.main(["plotjuggler", "on", "bad-dest", *url]) == 1  # daemon's 400


# -- daemon flag ----------------------------------------------------------------------


def test_daemon_flag_overrides_config() -> None:
    parser = build_parser()
    cfg = _apply_overrides(Config(), parser.parse_args(["--plotjuggler", "1.2.3.4:9000"]))
    assert (cfg.plotjuggler.enabled, cfg.plotjuggler.dest) == (True, "1.2.3.4:9000")
    # bare flag: enable, keep the configured dest
    cfg = Config()
    cfg.plotjuggler.dest = "10.0.0.9:1234"
    cfg = _apply_overrides(cfg, parser.parse_args(["--pj"]))
    assert (cfg.plotjuggler.enabled, cfg.plotjuggler.dest) == (True, "10.0.0.9:1234")
    # no flag: config decides
    assert _apply_overrides(Config(), parser.parse_args([])).plotjuggler.enabled is False
    with pytest.raises(ConfigError):
        _apply_overrides(Config(), parser.parse_args(["--plotjuggler", "nonsense"]))
