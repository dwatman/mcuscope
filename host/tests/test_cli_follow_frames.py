"""The `-f` follows: frames that cannot be decoded, the give-up clock, the WS upgrade."""

from __future__ import annotations

import json
import time

import httpx
import pytest
import typer

from mcuscope import cli
from mcuscope.cli_client import Client, Settings
from tests.test_cli import _ScriptedWS, run_mcu_canned


def _settings(port: str | None = None) -> Settings:
    return Settings(url="http://127.0.0.1:1", json_out=False, port=port)


def test_a_non_utf8_binary_frame_is_skipped_not_fatal(monkeypatch, capsys) -> None:
    """HEALTH-15 C04: UnicodeDecodeError is a ValueError, and one bad frame is per-item."""
    import websockets

    good = json.dumps([{"ts": 1.0, "chan": "log", "raw": "kept", "port": "p", "id": 1}])
    monkeypatch.setattr(websockets, "connect",
                        lambda url, **kw: _ScriptedWS([b"\xff\xfe\x90", good]))
    with pytest.raises(typer.Exit) as ei:
        cli._follow_ws(_settings(), None, None)
    out, err = capsys.readouterr()
    assert ei.value.exit_code == 3, err     # the stream's own end, not "malformed frame"
    assert "kept" in out
    assert "skipping bad frame" in err


def test_the_ws_url_quotes_the_port(monkeypatch, capsys) -> None:
    import websockets

    urls: list[str] = []

    def connect(url, **kw):
        urls.append(url)
        return _ScriptedWS([])

    monkeypatch.setattr(websockets, "connect", connect)
    with pytest.raises(typer.Exit):
        cli._follow_ws(_settings("sim&chan=sys"), None, None)
    assert urls == ["ws://127.0.0.1:1/ws?port=sim%26chan%3Dsys"]


@pytest.mark.parametrize("status", [400, 404, 500, 401])
def test_an_http_status_on_the_upgrade_is_exit_1(monkeypatch, capsys, status) -> None:
    """Something answered: the daemon is reachable, as the same refusal over REST is 1."""
    import websockets
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    def refuse(url, **kw):
        raise websockets.exceptions.InvalidStatus(Response(status, "x", Headers(), b""))

    monkeypatch.setattr(websockets, "connect", refuse)
    with pytest.raises(typer.Exit) as ei:
        cli._follow_ws(_settings("sim x"), None, None)
    assert ei.value.exit_code == 1
    assert f"HTTP {status}" in capsys.readouterr().err


def test_no_answer_on_the_upgrade_stays_exit_3(monkeypatch, capsys) -> None:
    """Positive control for the mapping above."""
    import websockets

    def refuse(url, **kw):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(websockets, "connect", refuse)
    with pytest.raises(typer.Exit) as ei:
        cli._follow_ws(_settings(), None, None)
    assert ei.value.exit_code == 3


def test_a_successful_poll_restarts_the_give_up_clock(monkeypatch, capsys) -> None:
    """HEALTH-15 B08: failures separated by an answered poll do not add up to a give-up.

    One failure at t=0.2, answered polls until t=10, then failures for good. Measured from
    the first failure the follow gives up at 30.2 s; from the last episode, at 40 s. The
    test interrupts it at 35 s, which only the fresh clock survives.
    """
    clock = [0.0]

    def sleep(sec: float) -> None:
        clock[0] += sec
        if clock[0] > 35.0:
            raise KeyboardInterrupt

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"capture": "c1"})
        if clock[0] < 0.3 or clock[0] >= 10.0:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json={"frames": []})

    monkeypatch.setattr(time, "sleep", sleep)
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    s = _settings()
    client = Client(s, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(client, "get", lambda path, **kw: {"frames": []})
    with pytest.raises(typer.Exit) as ei:
        cli._dump_follow(client, s, None)
    assert ei.value.exit_code == 0, capsys.readouterr().err


def _two_ports(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/ports":
        return httpx.Response(200, json={"ports": [{"alias": "a"}, {"alias": "b"}]})
    return httpx.Response(200, json={"lines": [], "truncated": False})


def test_a_follow_across_two_boards_names_each_rows_port(monkeypatch, capsys) -> None:
    import websockets

    rows = [{"ts": 1.0, "chan": "debug", "raw": f"from {p}", "port": p, "id": i}
            for i, p in enumerate("ab", 1)]
    monkeypatch.setattr(websockets, "connect", lambda url, **kw: _ScriptedWS([json.dumps(rows)]))
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, _two_ports, "tail", "-f", "-n", "0")
    assert rc == 3                                  # the scripted stream's end
    assert "[a]  debug| from a" in out and "[b]  debug| from b" in out
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, _two_ports, "-p", "a", "tail", "-f")
    assert "[a]" not in out and "from a" in out     # scoped: no column


def test_a_wait_match_across_two_boards_names_its_port(monkeypatch, capsys) -> None:
    line = {"id": 1, "ts": 1.0, "port": "b", "chan": "event", "raw": "!can 1 100 AA"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/wait":
            return httpx.Response(200, json={"status": "match", "line": line})
        return _two_ports(request)

    rc, out, _ = run_mcu_canned(monkeypatch, capsys, handler, "wait", "--match", "!can")
    assert rc == 0 and "[b]  event| !can 1 100 AA" in out


def test_can_dump_follow_ends_at_once_when_stdout_was_closed(monkeypatch) -> None:
    from mcuscope import cli_output

    cli_output.reset_output_state()
    monkeypatch.setattr(cli_output, "_OUT_FAILED", True)
    s = _settings()
    client = Client(s, transport=httpx.MockTransport(
        lambda r: pytest.fail("the follow polled a daemon it can never report")))
    with pytest.raises(typer.Exit) as ei:
        cli._dump_follow(client, s, None)
    assert ei.value.exit_code == 1
