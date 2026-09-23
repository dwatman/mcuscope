"""A follow against a daemon that accepts and then stalls is exit 1, as on every REST command
(SPEC 4); one that is not there stays exit 3."""

from __future__ import annotations

import asyncio
import functools
import socket
import threading
import time

import httpx
import pytest
import typer
import websockets

from mcuscope import cli
from mcuscope.cli_client import Client, Settings


@pytest.fixture
def stalling_listener():
    """A TCP listener that accepts every connection and never answers."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.1)
    held: list[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        while not stop.is_set():
            try:
                held.append(srv.accept()[0])
            except OSError:
                pass

    t = threading.Thread(target=accept, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.getsockname()[1]}"
    stop.set()
    t.join()
    for c in held:
        c.close()
    srv.close()


def _free_port_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


def test_tail_follow_on_a_stalled_handshake_is_exit_1(stalling_listener, monkeypatch,
                                                      capsys) -> None:
    monkeypatch.setattr(websockets, "connect",
                        functools.partial(websockets.connect, open_timeout=0.3))
    rc = cli.main(["--url", stalling_listener, "tail", "-f", "-n", "0"])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "accepted the connection but stopped answering" in err


class _HandshakeTimesOut:
    """websockets.connect whose opening handshake times out, raising asyncio's class (on
    Python 3.10 not an OSError)."""

    def __init__(self, *a, **kw) -> None:
        pass

    async def __aenter__(self):
        raise asyncio.TimeoutError("timed out during opening handshake")

    async def __aexit__(self, *a) -> bool:
        return False


def test_tail_follow_timing_out_with_nothing_listening_stays_exit_3(monkeypatch,
                                                                     capsys) -> None:
    """A blackholed host times out the same way; the TCP probe finds nothing there."""
    monkeypatch.setattr(websockets, "connect", _HandshakeTimesOut)
    rc = cli.main(["--url", _free_port_url(), "tail", "-f", "-n", "0"])
    err = capsys.readouterr().err
    assert rc == 3, err
    assert "daemon unreachable at" in err and "stopped answering" not in err


def test_tail_follow_timing_out_on_a_listening_port_is_exit_1(stalling_listener,
                                                               monkeypatch, capsys) -> None:
    monkeypatch.setattr(websockets, "connect", _HandshakeTimesOut)
    rc = cli.main(["--url", stalling_listener, "tail", "-f", "-n", "0"])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "accepted the connection but stopped answering" in err


@pytest.mark.parametrize("url, addr", [
    ("http://daemon.lan", ("daemon.lan", 80)),        # the port websockets itself dials
    ("https://daemon.lan", ("daemon.lan", 443)),
    ("http://127.0.0.1:8558", ("127.0.0.1", 8558)),
])
def test_the_tcp_probe_dials_what_the_websocket_dialled(monkeypatch, url, addr) -> None:
    dialled: list[tuple] = []

    def connect(address, timeout=None):
        dialled.append(address)
        raise ConnectionRefusedError

    monkeypatch.setattr(socket, "create_connection", connect)
    assert cli._accepts_tcp(url) is False
    assert dialled == [addr]


def _dump_follow_failing_with(monkeypatch, exc_type) -> int:
    clock = [0.0]
    monkeypatch.setattr(time, "sleep", lambda sec: clock.__setitem__(0, clock[0] + sec))
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"capture": "c1"})
        clock[0] += 10.0           # the poll's timeout elapses
        raise exc_type("timed out", request=request)

    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)
    client = Client(s, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(client, "get", lambda path, **kw: {"frames": []})
    with pytest.raises(typer.Exit) as ei:
        cli._dump_follow(client, s, None)
    return ei.value.exit_code


def test_can_dump_follow_on_a_daemon_that_stops_answering_is_exit_1(monkeypatch,
                                                                    capsys) -> None:
    code = _dump_follow_failing_with(monkeypatch, httpx.ReadTimeout)
    err = capsys.readouterr().err
    assert code == 1, err
    assert "accepted the request but stopped answering for 30s" in err


def test_can_dump_follow_that_cannot_connect_stays_exit_3(monkeypatch, capsys) -> None:
    code = _dump_follow_failing_with(monkeypatch, httpx.ConnectTimeout)
    err = capsys.readouterr().err
    assert code == 3, err
    assert "daemon unreachable at http://127.0.0.1:1 for 30s" in err
