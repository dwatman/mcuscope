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
from tests.support import UNREACHABLE, VERSION_HEADERS, ScriptedWS, Stack, canned, versioned


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
    rc = cli.main([*UNREACHABLE, "tail", "-f", "-n", "0"])
    err = capsys.readouterr().err
    assert rc == 3 and "websocket error:" in err, err   # the WebSocketException arm
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
            self.response = ScriptedWS([]).response   # a current daemon's handshake

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

    def versioned_handshake(connection, request, response):
        for key, value in VERSION_HEADERS.items():
            response.headers[key] = value   # a current daemon's handshake

    async def server():
        async with serve(handler, "127.0.0.1", 0, max_size=None,
                         process_response=versioned_handshake) as srv:
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


# -- the follows' own arms ------------------------------------------------------------------


def _row(i: int, port: str = "sim", raw: str | None = None) -> dict:
    return {"id": i, "ts": 1.0, "port": port, "dir": "rx", "chan": "debug", "seq": None,
            "raw": raw or f"row {i}"}


def _tail_follow(monkeypatch, capsys, frames: list, *argv: str, ports=None, lines=None):
    """`mcu tail -f` over a canned REST side and a scripted /ws; (rc, out, err)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ports":
            return httpx.Response(200, json=ports or {"ports": [], "stored": []})
        return httpx.Response(200, json={"lines": lines or [], "truncated": False})

    canned(monkeypatch, handler)
    monkeypatch.setattr(websockets, "connect",
                        lambda *a, **kw: ScriptedWS([json.dumps(f) for f in frames]))
    rc = cli.main([*UNREACHABLE, "tail", "-f", "-n", "0", *argv])
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_a_snapshot_row_missing_a_key_ends_the_follow_naming_it(monkeypatch, capsys) -> None:
    """The follow's KeyError arm: the snapshot formats rows outside the per-row guard."""

    canned_lines = {"lines": [{"id": 1, "ts": 1.0, "raw": "no chan"}], "truncated": False}
    canned(monkeypatch, lambda request: httpx.Response(200, json=canned_lines))
    monkeypatch.setattr(websockets, "connect", lambda *a, **kw: ScriptedWS([]))
    rc = cli.main([*UNREACHABLE, "tail", "-f", "-n", "1"])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "unexpected row shape from daemon: missing 'chan'" in err


def test_a_board_attached_during_a_follow_turns_the_port_column_on(monkeypatch,
                                                                    capsys) -> None:
    """R25-3: judged once at start, board2's rows printed bare beside sim's."""
    frames = [[_row(1, "sim")], [_row(2, "board2")], [_row(3, "sim")]]
    rc, out, err = _tail_follow(monkeypatch, capsys, frames,
                                ports={"ports": [{"alias": "sim"}], "stored": []})
    assert rc == 3, err   # the scripted close
    lines = out.splitlines()
    assert "[" not in lines[0] and "row 1" in lines[0], out   # one board so far: bare
    assert "[board2]" in lines[1] and "row 2" in lines[1], out
    assert "[sim]" in lines[2] and "row 3" in lines[2], out


def test_the_daemons_own_rows_do_not_turn_the_port_column_on(monkeypatch, capsys) -> None:
    """Port "" is the daemon's rows (SPEC 3.5), not a second board."""
    frames = [[_row(1, "")], [_row(2, "sim")]]
    rc, out, err = _tail_follow(monkeypatch, capsys, frames,
                                ports={"ports": [{"alias": "sim"}], "stored": []})
    assert "[" not in out and "row 2" in out, out


def test_a_follow_under_p_prints_no_port_column(monkeypatch, capsys) -> None:
    frames = [[_row(1, "sim")], [_row(2, "board2")]]
    rc, out, err = _tail_follow(monkeypatch, capsys, frames, "-p", "sim",
                                ports={"ports": [{"alias": "sim"}], "stored": []})
    assert "row 2" in out and "[" not in out, out


def test_follow_match_digit_class_is_ascii_as_in_the_daemon(monkeypatch, capsys) -> None:
    """R19-2 (D-5): `\\d` does not match U+0663; an ASCII digit still does."""
    frames = [[_row(1, raw="reading \u0663")], [_row(2, raw="reading 3")]]
    rc, out, err = _tail_follow(monkeypatch, capsys, frames, "--match", r"\d")
    assert "reading 3" in out, out + err
    assert "\u0663" not in out, out


def test_follow_match_over_the_daemons_length_cap_is_refused(monkeypatch, capsys) -> None:
    """R18-1: 400-deep nesting raised RecursionError out of regex.compile."""
    rc, out, err = _tail_follow(monkeypatch, capsys, [], "--match", "(" * 400 + ")" * 400)
    assert rc == 1, err
    assert "bad --match pattern: too long (max 200 chars)" in err
    # Positive control: 200 characters is the daemon's own limit and compiles here.
    rc, out, err = _tail_follow(monkeypatch, capsys, [[_row(1)]], "--match",
                                "(" * 98 + "row " + ")" * 98)
    assert "row 1" in out, err


def test_follow_match_over_the_daemons_repeat_budget_is_refused(monkeypatch, capsys) -> None:
    """X-cli-1: 24 characters expanding to 1,010,100 compiled here while `lines` refused it."""
    connects: list = []

    def connect(*a, **kw):
        connects.append(a)
        return ScriptedWS([json.dumps([_row(1)])])

    canned(monkeypatch, lambda request: httpx.Response(200, json={"lines": []}))
    monkeypatch.setattr(websockets, "connect", connect)
    rc = cli.main([*UNREACHABLE, "tail", "-f", "-n", "0", "--match",
                   "(?:(?:a{100}){100}){100}"])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "bad --match pattern: too large: repeats expand to 1010100" in err
    assert connects == []
    # Positive control: one level less is inside the budget and follows.
    rc = cli.main([*UNREACHABLE, "tail", "-f", "-n", "0", "--match", "(?:(?:row ){100}){100}|row"])
    out = capsys.readouterr()
    assert "row 1" in out.out, out.err
    assert len(connects) == 1


@pytest.mark.parametrize("flag", ["(?u)", "(?L)", "(?au)"])
def test_follow_match_with_an_inline_flag_against_ascii_is_refused(monkeypatch, capsys,
                                                                     flag) -> None:
    """The daemon's 400 for these; a ValueError from `regex` was once a traceback here."""
    rc, out, err = _tail_follow(monkeypatch, capsys, [[_row(1)]], "--match", flag + "row")
    assert rc == 1, err
    assert "bad --match pattern:" in err and "user patterns always compile as ASCII" in err
    assert "row 1" not in out


def test_follow_match_nested_too_deeply_is_refused_not_a_crash(monkeypatch, capsys) -> None:
    import regex

    def deep(*a, **kw):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(regex, "compile", deep)
    rc, out, err = _tail_follow(monkeypatch, capsys, [], "--match", "((x))")
    assert rc == 1, err
    assert "bad --match pattern: nested too deeply" in err


@pytest.mark.parametrize("url", ["http://127.0.0.1:99999", "http://"])
def test_a_bad_url_on_the_websocket_path_is_exit_3(monkeypatch, capsys, url) -> None:
    """R18-2: websockets' ValueError read as "malformed frame from daemon", exit 1."""
    rc = cli.main(["--url", url, "tail", "-f", "-n", "0"])
    err = capsys.readouterr().err
    assert rc == 3, err
    assert "bad daemon url" in err and "malformed frame" not in err, err


def test_a_bad_url_on_the_can_follow_is_exit_3(capsys) -> None:
    rc = cli.main(["--url", "http://127.0.0.1:99999", "can", "dump", "-f", "-n", "0"])
    err = capsys.readouterr().err
    assert rc == 3, err


# -- can dump -f: per-poll and per-frame guards, and next_since_id ---------------------------


def _frame(i: int) -> dict:
    return {"line_id": i, "ts": 1.0, "bus": 1, "can_id": 0x100, "ext": False, "rtr": False,
            "dlc": 1, "data_hex": "AA"}


def _scripted_dump_follow(monkeypatch, polls: list) -> tuple[list[dict], int]:
    """Run _dump_follow over scripted /can/frames answers; (the polls' params, exit code).

    Each entry of `polls` is one answer body; the follow ends with a Ctrl-C after them.
    """
    monkeypatch.setattr(time, "sleep", lambda sec: None)
    asked: list[dict] = []
    answers = list(polls)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"capture": "c1"})
        asked.append(dict(request.url.params))
        if not answers:
            raise KeyboardInterrupt
        return httpx.Response(200, json=answers.pop(0))

    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)
    client = Client(s, transport=httpx.MockTransport(versioned(handler)))
    monkeypatch.setattr(client, "get", lambda path, **kw: {"frames": []})
    with pytest.raises(typer.Exit) as ei:
        cli._dump_follow(client, s, None)
    return asked, ei.value.exit_code


def test_a_malformed_poll_or_frame_costs_that_item_not_the_follow(monkeypatch,
                                                                  capsys) -> None:
    """R16-2: `_list_field`'s die() sat outside both guards and ended `can dump -f`."""
    polls = [
        {"frames": [_frame(5), {"line_id": 4}], "truncated": False},
        {"frames": [_frame(7), "junk"], "truncated": False},
        {"frames": "not a list"},
        {"frames": [_frame(9)], "truncated": False},
    ]
    asked, code = _scripted_dump_follow(monkeypatch, polls)
    out, err = capsys.readouterr()
    assert code == 0, err
    printed = [ln for ln in out.splitlines() if "id=100" in ln]
    assert len(printed) == 3, out   # frames 5, 7 and 9
    assert "skipping bad frame" in err and "skipped 2 frames" in err, err
    assert "skipping bad update: 'frames' is not a list" in err, err
    assert len(asked) == 5, asked   # the follow kept polling past every bad item


def test_the_follow_advances_on_next_since_id_with_no_frames(monkeypatch, capsys) -> None:
    """R20-2 (D-7): a quiet port's follow re-read every frame above id 0 on every poll."""
    polls = [{"frames": [], "truncated": False, "next_since_id": 500},
             {"frames": [], "truncated": False, "next_since_id": 400}]
    asked, _ = _scripted_dump_follow(monkeypatch, polls)
    capsys.readouterr()
    assert [q["since_id"] for q in asked] == ["0", "500", "500"], asked   # never backwards


def test_the_follow_without_next_since_id_keeps_its_watermark(monkeypatch, capsys) -> None:
    polls = [{"frames": [_frame(3)], "truncated": False}, {"frames": [], "truncated": False}]
    asked, _ = _scripted_dump_follow(monkeypatch, polls)
    capsys.readouterr()
    assert [q["since_id"] for q in asked] == ["0", "3", "3"], asked


def test_can_dump_and_its_follow_send_every_filter(monkeypatch, capsys) -> None:
    """R54-1: the filters are built once, for the backfill and the follow's polls alike."""
    asked: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"capture": "c1"})
        asked.append(dict(request.url.params))
        if len(asked) > 3:
            raise KeyboardInterrupt   # the backfill, the follow's priming read, one poll
        return httpx.Response(200, json={"frames": [], "truncated": False})

    monkeypatch.setattr(time, "sleep", lambda sec: None)
    canned(monkeypatch, handler)
    rc = cli.main([*UNREACHABLE, "-p", "b", "can", "dump", "-n", "1", "--bus", "2",
                   "-i", "100", "-i", "200", "--session", "run", "-f"])
    assert rc == 0, capsys.readouterr().err
    want = {"port": "b", "bus": "2", "id": "100,200", "session": "run"}
    assert len(asked) == 4, asked
    for q in asked:
        assert want.items() <= q.items(), q
