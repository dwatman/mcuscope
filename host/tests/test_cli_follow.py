"""The `-f` follows' failure modes: a daemon that stalls is exit 1, as on every REST command
(SPEC 4), one that is not there stays exit 3, and the subscriber cap is exit 1."""

from __future__ import annotations

import asyncio
import functools
import gc
import json
import logging
import socket
import sys
import threading
import time

import httpx
import pytest
import typer
import websockets

from mcuscope import cli, cli_client, store
from mcuscope.cli_client import Client, Settings
from tests.support import UNREACHABLE, Stack


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


# -- FC-5: the follow's frame cap is raised, not removed ---------------------------------

def test_the_follow_frame_cap_is_a_number_not_unbounded(monkeypatch, capsys) -> None:
    """max_size=None buffers whatever a wrong service on the port sends."""
    import websockets

    seen: dict = {}

    def connect(url, **kw):
        seen.update(kw)
        raise websockets.exceptions.InvalidURI(url, "stop here")

    monkeypatch.setattr(websockets, "connect", connect)
    cli.main([*UNREACHABLE, "tail", "-f", "-n", "0"])
    capsys.readouterr()
    assert seen.get("max_size") == 16 * 1024 * 1024, seen
    # 500 rows of up to 4 KB is about 2 MB: the cap has headroom and is still a cap.
    assert seen["max_size"] > 500 * 4096


# -- TQ-F2 half B: _follow_ws must consume the recv it was handed -------------------------


def test_follow_ws_consumes_its_pending_recv_when_the_staged_drain_raises(
    monkeypatch, caplog
) -> None:
    """`mcu tail -f | head` ends in the staged drain, with a recv still in flight.

    Half A of this fix (_stage_backfill) has a test; the half that runs on the ordinary
    follow path did not, and could be deleted with the suite green. Left unawaited, the
    recv resolves with the socket teardown's ConnectionClosed and asyncio reports "Task
    exception was never retrieved" when the task is collected.
    """
    import asyncio

    import websockets

    from mcuscope import cli

    class _StagingWs:
        """Hands out one frame, then blocks; the close resolves the recv with an error."""

        def __init__(self) -> None:
            self._closed = asyncio.Event()
            self._sent = False

        async def recv(self) -> str:
            if not self._sent:
                self._sent = True
                return json.dumps([{"id": 1, "ts": 0.0, "port": "b", "dir": "rx",
                                    "chan": "debug", "seq": None, "raw": "staged"}])
            await self._closed.wait()
            raise websockets.exceptions.ConnectionClosedOK(None, None)

        async def __aenter__(self) -> _StagingWs:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            # A real close awaits its handshake, so an orphaned recv resolves here rather
            # than being cancelled by asyncio.run's shutdown (which files no report).
            self._closed.set()
            await asyncio.sleep(0.05)
            return False

    class _ClosedStdout:
        def write(self, text: str) -> int:
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self) -> None:
            raise BrokenPipeError(32, "Broken pipe")

        def fileno(self) -> int:
            raise OSError("no fd")

    def slow_snapshot() -> int:
        # Slower than the first frame, so the frame is *staged* and a second recv is in
        # flight when the snapshot returns: that pair is the only window where the drain
        # can raise with a recv still owned by the caller.
        time.sleep(0.2)
        return 0

    monkeypatch.setattr(websockets, "connect", lambda *a, **kw: _StagingWs())
    monkeypatch.setattr(sys, "stdout", _ClosedStdout())
    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)

    code = None
    with caplog.at_level(logging.ERROR, logger="asyncio"):
        try:
            cli._follow_ws(s, None, None, backfill=slow_snapshot)
        except typer.Exit as exc:
            code = exc.exit_code
        # Not pytest.raises: its ExceptionInfo keeps the traceback, and with it the frame
        # holding the recv, alive - so an orphaned task is never collected and files no
        # report. Task.__del__ is what reports, so the reference has to be gone first.
        gc.collect()
        before = caplog.text

        # The positive control: an orphaned recv failure does reach this caplog.
        async def orphan() -> None:
            async def fail() -> None:
                raise websockets.exceptions.ConnectionClosedOK(None, None)

            task = asyncio.ensure_future(fail())
            await asyncio.sleep(0.05)
            del task

        asyncio.run(orphan())
        gc.collect()
    assert code == 0, "the closed pipe must end the follow with exit 0"
    assert "never retrieved" not in before
    assert "never retrieved" in caplog.text[len(before):], caplog.text


@pytest.fixture
def capped(stack: Stack, monkeypatch) -> Stack:
    monkeypatch.setattr(store, "MAX_SUBSCRIBERS", 0)   # read at subscribe time
    return stack


def test_tail_follow_at_the_subscriber_cap_is_exit_1_naming_the_cap(capped, capsys) -> None:
    rc = cli.main(["tail", "-n", "0", "-f", "--url", capped.base_url])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "too many subscribers" in err and "subscriber cap" in err, err
    assert "stream closed by daemon" not in err


def test_tail_follow_cap_in_json_mode_is_one_error_object(capped, capsys) -> None:
    rc = cli.main(["--json", "tail", "-n", "0", "-f", "--url", capped.base_url])
    out = capsys.readouterr().out
    assert rc == 1
    obj = json.loads(out.strip().splitlines()[-1])
    assert obj["exit_code"] == 1 and "too many subscribers" in obj["error"]


def test_wait_at_the_same_cap_answers_the_same_code_and_words(capped, capsys) -> None:
    """The sibling surface the ruling aligns with: both name "too many subscribers"."""
    rc = cli.main(["wait", "--match", "never", "--timeout", "500", "--url", capped.base_url])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "too many subscribers" in err


def test_tail_follow_refused_at_shutdown_stays_exit_3(stack: Stack, capsys) -> None:
    """Close 1001: the daemon is going away, which is "unreachable", not the cap."""
    stack.app.state.store._subscribers_closed = True
    rc = cli.main(["tail", "-n", "0", "-f", "--url", stack.base_url])
    err = capsys.readouterr().err
    assert rc == 3, err
    assert "stream closed by daemon" in err
    assert "too many subscribers" not in err


def test_a_follow_frame_over_one_mib_is_printed_not_closed_as_unreachable(capsys) -> None:
    """The daemon coalesces up to 500 rows of up to 4 KB into one frame."""
    from websockets.asyncio.server import serve

    rows = [{"id": i, "ts": 1.0, "chan": "debug", "raw": "y" * 4000} for i in range(1, 501)]
    frame = json.dumps(rows)
    assert len(frame) > 2**20
    ready, port = threading.Event(), []

    async def handler(ws):
        await ws.send(frame)
        await ws.close(1001)

    async def server():
        async with serve(handler, "127.0.0.1", 0, max_size=None) as srv:
            port.append(srv.sockets[0].getsockname()[1])
            ready.set()
            await asyncio.sleep(30)

    threading.Thread(target=lambda: asyncio.run(server()), daemon=True).start()
    assert ready.wait(10)
    s = cli_client.Settings(url=f"http://127.0.0.1:{port[0]}", json_out=False, port=None)
    with pytest.raises(cli.typer.Exit) as exit_info:
        cli._follow_ws(s, None, None)
    out = capsys.readouterr()
    assert out.out.count(" debug| yyyy") == 500, out.err
    assert exit_info.value.exit_code == 3   # the close 1001 that followed, as SPEC 4 says
