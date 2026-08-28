"""PlotJuggler UDP streaming tests (SPEC 3.7).

Four layers: pjstream unit tests (datagram format and every refusal), config
load/save, the REST pair (runtime vs saved, and the write-protection bar), and
live-stack tests proving sim plot lines arrive as datagrams and the CLI drives it
end to end. Resolver failures are driven by monkeypatching, never by hoping the
tester's network answers NXDOMAIN (a hijacking resolver would pass a broken build).
"""

from __future__ import annotations

import json
import re
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
    # IPv6 literals take the standard bracket form, brackets stripped for getaddrinfo.
    assert parse_dest("[::1]:9870") == ("::1", 9870)
    assert parse_dest("[2001:db8::1]:80") == ("2001:db8::1", 80)


@pytest.mark.parametrize("bad", [
    "nocolon", ":9870", "host:", "host:abc", "host:0", "host:70000", "", "host:98.7",
    # int() alone would take all three of these; the wire grammar must not.
    "host:+9870", "host:9_870", "host:٩٨٧٠",
    # a bare IPv6 literal must not donate its last group as the port
    "2001:db8::1", "::1",
    # junk hosts a later resolve would otherwise report confusingly or not at all
    "a b:9870", "host\tx:9870", "ho\x00st:9870", "[]:9870", "[nope!]:9870",
])
def test_parse_dest_refuses(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_dest(bad)


def test_parse_dest_refusal_messages() -> None:
    # Each refusal path owns its wording; a shared exception type alone cannot tell
    # the bracket-guidance branch from the host-grammar branch below it.
    with pytest.raises(ValueError, match=re.escape("bracketed, like [2001:db8::1]:9870")):
        parse_dest("2001:db8::1")
    with pytest.raises(ValueError, match="port must be a number"):
        parse_dest("host:9_870")
    with pytest.raises(ValueError, match="not a hostname or address"):
        parse_dest("a b:9870")


# -- streamer -------------------------------------------------------------------------


def _udp_receiver() -> tuple[socket.socket, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(5.0)
    return sock, f"127.0.0.1:{sock.getsockname()[1]}"


def _streamer(dest: str) -> PlotJugglerStreamer:
    pj = PlotJugglerStreamer(dest=dest)
    pj.configure(True)
    return pj


POINTS = [
    {"tick_ms": 12345, "sid": "0", "name": "temp", "value": 25.1},
    {"tick_ms": 12345, "sid": "0", "name": "gpio.led", "value": 1.0},
]


def test_send_datagram_format() -> None:
    sock, dest = _udp_receiver()
    try:
        pj = _streamer(dest)
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


def test_reserved_alias_is_renamed() -> None:
    # The alias grammar admits `ts` and `tick`; the timestamp keys must survive them.
    sock, dest = _udp_receiver()
    try:
        pj = _streamer(dest)
        pj.send("ts", 5.0, POINTS)
        msg = json.loads(sock.recv(65535).decode())
        assert msg["ts"] == 5.0 and "ts_" in msg
        pj.send("tick", 6.0, POINTS)
        msg = json.loads(sock.recv(65535).decode())
        assert msg["tick"] == 12.345 and "tick_" in msg
        pj.close()
    finally:
        sock.close()


def test_non_finite_values_dropped_not_emitted() -> None:
    # A bare Infinity/NaN token is not JSON and would cost the receiver the whole
    # datagram; the bad value goes, the rest of the line survives.
    sock, dest = _udp_receiver()
    try:
        pj = _streamer(dest)
        pj.send("board", 1.0, [
            {"tick_ms": 100, "sid": "0", "name": "ok", "value": 1.5},
            {"tick_ms": 100, "sid": "0", "name": "inf", "value": float("inf")},
            {"tick_ms": 100, "sid": "0", "name": "nan", "value": float("nan")},
        ])
        msg = json.loads(sock.recv(65535).decode())   # parseable at all = the point
        assert msg["board"] == {"ok": 1.5}
        # every value non-finite: nothing to plot, nothing sent
        pj.send("board", 2.0, [{"tick_ms": 100, "sid": "0", "name": "inf",
                                "value": float("-inf")}])
        sock.settimeout(0.3)
        with pytest.raises(TimeoutError):
            sock.recv(65535)
        pj.close()
    finally:
        sock.close()


def test_disabled_streamer_sends_nothing() -> None:
    sock, dest = _udp_receiver()
    try:
        pj = PlotJugglerStreamer(dest=dest)
        pj.send("board", 1.0, POINTS)
        pj.configure(True, dest)
        pj.configure(False)
        pj.send("board", 2.0, POINTS)
        sock.settimeout(0.3)
        with pytest.raises(TimeoutError):
            sock.recv(65535)
        pj.close()
    finally:
        sock.close()


def test_dest_changed_while_disabled_wins_on_enable() -> None:
    old_sock, old_dest = _udp_receiver()
    new_sock, new_dest = _udp_receiver()
    try:
        pj = _streamer(old_dest)
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


def test_bad_configure_keeps_previous_state(monkeypatch: pytest.MonkeyPatch) -> None:
    sock, dest = _udp_receiver()
    try:
        pj = _streamer(dest)
        with pytest.raises(ValueError):
            pj.configure(True, "not-a-dest")
        with pytest.raises(ValueError):
            pj.configure(True, "239.1.1.1:9870")   # multicast: refused as non-unicast
        real = socket.getaddrinfo
        monkeypatch.setattr(pjstream.socket, "getaddrinfo",
                            lambda *a, **k: (_ for _ in ()).throw(socket.gaierror(-2)))
        with pytest.raises(OSError):
            pj.configure(True, "example.invalid:9870")
        monkeypatch.setattr(pjstream.socket, "getaddrinfo", real)
        # All three refusals left the old destination live.
        assert pj.enabled is True and pj.dest == dest
        pj.send("board", 3.0, POINTS)
        assert json.loads(sock.recv(65535).decode())["ts"] == 3.0
        pj.close()
    finally:
        sock.close()


@pytest.mark.parametrize("bad", ["0.0.0.0:9870", "239.255.0.1:9870", "255.255.255.255:9870"])
def test_non_unicast_dest_refused(bad: str) -> None:
    pj = PlotJugglerStreamer()
    with pytest.raises(ValueError):
        pj.configure(True, bad)
    assert pj.enabled is False


def test_send_swallows_socket_errors() -> None:
    sock, dest = _udp_receiver()
    try:
        pj = _streamer(dest)
        pj._target[0].close()   # yank the socket out from under it
        pj.send("board", 1.0, POINTS)   # must not raise: capture path calls this
    finally:
        sock.close()


def test_retired_socket_closes_one_swap_late() -> None:
    # The replaced socket must survive exactly one swap (an in-flight send may hold
    # it) and be closed by the next; close() reaps whatever is left of both slots.
    pj = _streamer("127.0.0.1:9001")
    first = pj._target[0]
    pj.configure(True, "127.0.0.1:9002")
    assert pj._retired is not None and pj._retired[0] is first
    assert first.fileno() != -1, "retired socket must stay open for one swap"
    second = pj._target[0]
    pj.configure(True, "127.0.0.1:9003")
    assert first.fileno() == -1, "second swap must close the first socket"
    assert pj._retired[0] is second and second.fileno() != -1
    third = pj._target[0]
    pj.close()
    assert second.fileno() == -1 and third.fileno() == -1 and pj._target is None


def test_close_then_send_is_noop() -> None:
    pj = _streamer("127.0.0.1:9870")
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


def _mk_app(tmp_path: Path, **plotjuggler: object):
    config = Config(storage=StorageConfig(db_path=str(tmp_path / "cap.db")))
    for key, value in plotjuggler.items():
        setattr(config.plotjuggler, key, value)
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


def test_rest_bad_dest_is_400_and_state_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _mk_app(tmp_path)
    with _loopback(app) as c:
        # grammar and unicast refusals need no resolver; the last leg simulates one down
        for bad in ("no-port", "host:0", "239.1.1.1:9870"):
            r = c.put("/plotjuggler", json={"enabled": True, "dest": bad})
            assert r.status_code == 400, bad
            assert c.get("/plotjuggler").json()["enabled"] is False
        monkeypatch.setattr(
            pjstream, "_resolve",
            lambda dest: (_ for _ in ()).throw(socket.gaierror(-2, "resolver down")),
        )
        r = c.put("/plotjuggler", json={"enabled": True, "dest": "resolves.not:9870"})
        assert r.status_code == 400
        assert c.get("/plotjuggler").json()["enabled"] is False
        # empty string is not "keep the current one"; that is spelled by omission
        r = c.put("/plotjuggler", json={"enabled": True, "dest": ""})
        assert r.status_code == 422
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


def test_startup_with_dead_resolver_serves_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # [plotjuggler] enabled=true with a dest that will not resolve must not kill (or
    # hang) the daemon: the capture does not depend on the viewer.
    monkeypatch.setattr(
        pjstream, "_resolve",
        lambda dest: (_ for _ in ()).throw(socket.gaierror(-2, "resolver down")),
    )
    app = _mk_app(tmp_path, enabled=True, dest="viewer.lan:9870")
    with _loopback(app) as c:
        body = c.get("/plotjuggler").json()
        assert body == {"enabled": False, "dest": "viewer.lan:9870"}


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


def test_cli_plotjuggler_and_alias(
    make_stack: Callable[..., Stack], capsys: pytest.CaptureFixture
) -> None:
    stack = make_stack()
    url = ["--url", stack.base_url]
    assert cli.main(["plotjuggler", "on", "127.0.0.1:9555", *url]) == 0
    assert "plotjuggler: on  dest 127.0.0.1:9555" in capsys.readouterr().out
    assert cli.main(["pj", *url]) == 0                     # alias, show-state form
    assert "plotjuggler: on" in capsys.readouterr().out
    # the new status line: present while streaming, silent when off
    assert cli.main(["status", *url]) == 0
    assert "plotjuggler: streaming to 127.0.0.1:9555" in capsys.readouterr().out
    assert cli.main(["pj", "off", *url]) == 0
    capsys.readouterr()
    assert cli.main(["status", *url]) == 0
    assert "plotjuggler" not in capsys.readouterr().out
    # each refusal asserted by its own words, not just a shared exit code
    assert cli.main(["plotjuggler", "sideways", *url]) == 1
    assert "expected 'on' or 'off'" in capsys.readouterr().err
    assert cli.main(["plotjuggler", "--save", *url]) == 1
    assert "--save needs on or off" in capsys.readouterr().err
    assert cli.main(["plotjuggler", "on", "bad-dest", *url]) == 1
    assert "host:port" in capsys.readouterr().err   # the daemon's 400, relayed


def test_cli_save_persists_to_config(
    make_stack: Callable[..., Stack], capsys: pytest.CaptureFixture
) -> None:
    stack = make_stack()
    url = ["--url", stack.base_url]
    assert cli.main(["pj", "on", "127.0.0.1:9666", "--save", *url]) == 0
    assert "(saved to config)" in capsys.readouterr().out
    saved = load_config(stack.config_path).plotjuggler
    assert (saved.enabled, saved.dest) == (True, "127.0.0.1:9666")
    assert cli.main(["pj", "off", "--save", *url]) == 0
    saved = load_config(stack.config_path).plotjuggler
    assert (saved.enabled, saved.dest) == (False, "127.0.0.1:9666")


def test_cli_json_is_one_object(
    make_stack: Callable[..., Stack], capsys: pytest.CaptureFixture
) -> None:
    stack = make_stack()
    url = ["--url", stack.base_url]
    assert cli.main(["--json", "pj", "on", "127.0.0.1:9777", "--save", *url]) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {"enabled": True, "dest": "127.0.0.1:9777"}
    # the error path too: stdout carries exactly one object, code in-band
    assert cli.main(["--json", "pj", "on", "bad-dest", *url]) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["exit_code"] == 1 and "host:port" in body["error"]


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
