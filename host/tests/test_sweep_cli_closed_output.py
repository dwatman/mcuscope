"""Closed-output and status-mapping sweep of the CLI (classes 9, 35, 70), 2026-09-15.

Stream cases run the real console entry in a child: stdout a pipe whose read end is closed, or
`/dev/full`, with the crash-log dir bound to tmp_path. A daemon comes from a canned transport
in the child, or the in-process stack where a WebSocket is needed.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading

import httpx
import pytest

from mcuscope import cli, cli_client
from tests.support import CHILD_TEXT, Stack

# ROUTES maps a path to [status, body]; a path not listed answers 404.
CHILD = """
import json, os, sys, platformdirs
DATA = sys.argv[1]                     # bound now: sys.argv is replaced before the CLI runs
platformdirs.user_data_dir = lambda *a, **k: DATA
routes = json.loads(os.environ.get("SWEEP_ROUTES", "{}"))
if routes:
    import httpx
    from mcuscope import cli_client
    def handler(request):
        status, body = routes.get(request.url.path, [404, {"error": "Not Found"}])
        return httpx.Response(status, json=body)
    cli_client.Client.open = lambda self: httpx.Client(transport=httpx.MockTransport(handler))
if os.environ.get("SWEEP_CRASH"):
    from mcuscope import _stdio
    def main():
        raise RuntimeError("sweep-crash-sentinel")
    raise SystemExit(_stdio.console_entry(main, "mcu"))
from mcuscope import cli
sys.argv = ["mcu", *sys.argv[2:]]
raise SystemExit(cli.console_entry())
"""

FULL = "/dev/full"
needs_full = pytest.mark.skipif(not os.path.exists(FULL), reason="needs /dev/full")


def _child(tmp_path, stdout: str, *argv: str, routes=None, url="http://127.0.0.1:1",
           timeout: float = 60, stream: str = "stdout",
           crash: bool = False) -> tuple[int | str, str, list[str]]:
    """(exit code or "hung", the other stream's text, crash-log dir listing) with `stream`
    attached, closed or full."""
    data = tmp_path / f"data-{stream}-{stdout}"
    env = dict(os.environ, MCUSCOPE_URL=url, SWEEP_ROUTES=json.dumps(routes or {}))
    if crash:
        env["SWEEP_CRASH"] = "1"
    fd = None
    if stdout == "closed":
        r, fd = os.pipe()
        os.close(r)
    elif stdout == "full":
        fd = os.open(FULL, os.O_WRONLY)
    streams = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if fd is not None:
        streams[stream] = fd
    try:
        p = subprocess.run([sys.executable, "-c", CHILD, str(data), *argv], env=env,
                           stdin=subprocess.DEVNULL, timeout=timeout, **streams, **CHILD_TEXT)
        rc, err = p.returncode, (p.stderr if stream == "stdout" else p.stdout)
    except subprocess.TimeoutExpired:
        rc, err = "hung", ""
    finally:
        if fd is not None:
            os.close(fd)
    return rc, err, sorted(os.listdir(data)) if data.exists() else []


ASSERT_FAIL = {"/assert": [200, {
    "status": "fail", "checked_lines": 3, "elapsed_ms": 1.0,
    "expect": [{"pattern": "never", "matched": False}], "forbid": [],
}]}
LINES = {"/lines": [200, {"truncated": False, "lines": [
    {"id": i, "ts": 1.0, "chan": "debug", "raw": "sweep row " + "x" * 60}
    for i in range(1000, 0, -1)
]}], "/lines/export": [200, "a short export row"]}


# -- the final flush keeps the command's own code ------------------------------------------


def test_a_failing_assert_with_stdout_closed_still_exits_1(tmp_path) -> None:
    """`mcu assert ... | head -0` read as a pass: the flush's broken pipe returned 0."""
    rc, err, files = _child(tmp_path, "closed", "assert", "--expect", "never",
                            routes=ASSERT_FAIL)
    assert _child(tmp_path, "attached", "assert", "--expect", "never",
                  routes=ASSERT_FAIL)[0] == 1
    assert rc == 1, err
    assert "FAILED  expect 'never'" in err and files == []


def test_daemon_status_not_running_with_stdout_closed_is_still_3(tmp_path) -> None:
    rc, err, files = _child(tmp_path, "closed", "daemon", "status")
    assert rc == 3, err
    assert files == []


@needs_full
def test_daemon_status_not_running_into_a_full_disk_is_3_and_says_so(tmp_path) -> None:
    rc, err, files = _child(tmp_path, "full", "daemon", "status")
    assert rc == 3, err
    assert "cannot write output" in err and files == []


# -- a full stdout mid-command is exit 1, not a crash ----------------------------------------


@needs_full
@pytest.mark.parametrize("argv", [["lines", "--limit", "1000"], ["ai-guide"], ["--help"],
                                  ["log", "export"]], ids=" ".join)
def test_an_output_into_a_full_disk_is_exit_1_without_a_crash_log(tmp_path, argv) -> None:
    """`log export` is small: its stream's own flush is the write that fails."""
    rc, err, files = _child(tmp_path, "full", *argv, routes=LINES)
    assert rc == 1, err
    assert err.count("cannot write output: [Errno 28]") == 1, err
    assert "Traceback" not in err and "Exception ignored" not in err, err
    assert files == [], f"crash log written: {files}"


@needs_full
def test_a_json_error_object_into_a_full_disk_keeps_the_errors_code(tmp_path) -> None:
    """The write failure must not own the code die() chose: 3 stays 3, not 1."""
    rc, err, files = _child(tmp_path, "full", "--json", "status")
    assert rc == 3, err
    assert "daemon unreachable" in err and files == []
    assert err.count("cannot write output") == 1, err   # the guard and out_json both see it


@needs_full
def test_a_small_output_into_a_full_disk_fails_at_the_final_flush_as_1(tmp_path) -> None:
    """Too small to overflow the buffer, so only main()'s own flush meets the full disk."""
    rc, err, files = _child(tmp_path, "full", "config", "path")
    assert rc == 1, err
    assert "cannot write output" in err and files == []


@needs_full
def test_a_crash_notice_into_a_full_stderr_is_logged_and_exits_1(tmp_path) -> None:
    """The positive control for every no-crash-log assertion here, and the notice's own guard:
    unguarded against a full stderr it exited 120."""
    rc, _, files = _child(tmp_path, "full", stream="stderr", crash=True)
    assert rc == 1, rc
    assert files == ["mcu-crash.log"]
    assert "sweep-crash-sentinel" in (tmp_path / "data-stderr-full" / "mcu-crash.log").read_text(
        encoding="utf-8")


@needs_full
@pytest.mark.parametrize("argv, code", [(["status"], 3), (["lines", "--bogus"], 1)],
                         ids=["die", "usage"])
def test_an_error_message_into_a_full_stderr_keeps_its_exit_code(tmp_path, argv, code) -> None:
    """Only a closed pipe was guarded: a full stderr exited 120 with a crash log."""
    rc, out, files = _child(tmp_path, "full", "--json", *argv, stream="stderr")
    assert rc == code, out
    assert f'"exit_code": {code}' in out, out
    assert files == [], f"crash log written: {files}"


# -- a follow notices its reader ------------------------------------------------------------


def test_a_json_can_dump_follow_ends_when_stdout_closes(tmp_path) -> None:
    """The backfill went through out_json, which swallows a closed pipe, so the follow then
    polled into devnull for ever."""
    routes = {"/can/frames": [200, {"truncated": False, "frames": [
        {"line_id": 5, "ts": 1.0, "can_id": 256, "ext": 0, "rtr": 0, "dlc": 1, "data_hex": "AA"}
    ]}], "/status": [200, {"capture": "c"}]}
    rc, err, files = _child(tmp_path, "closed", "--json", "can", "dump", "-n", "1", "-f",
                            routes=routes, timeout=15)
    assert rc == 0, err
    assert files == []


@pytest.fixture(scope="module")
def filled():
    st = Stack()
    with httpx.Client() as c:
        for i in range(300):
            c.post(st.base_url + "/marker", json={"text": f"sweep filler {i} " + "x" * 60})
    yield st
    st.close()


def test_a_json_tail_follow_ends_when_stdout_closes(tmp_path, filled) -> None:
    rc, err, files = _child(tmp_path, "closed", "--json", "tail", "-f", "-n", "3",
                            url=filled.base_url, timeout=15)
    assert rc == 0, err
    assert files == []


@needs_full
def test_a_follow_snapshot_into_a_full_disk_is_exit_1_not_unreachable(tmp_path, filled) -> None:
    rc, err, files = _child(tmp_path, "full", "tail", "-f", "-n", "300",
                            url=filled.base_url, timeout=15)
    assert rc == 1, err
    assert "cannot write output" in err and "daemon unreachable" not in err, err
    assert files == []


# -- status mapping (class 70) ----------------------------------------------------------------


def _mock(monkeypatch, handler) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def record(request):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(cli_client.Client, "open",
                        lambda self: httpx.Client(transport=httpx.MockTransport(record)))
    return seen


def _failing_poll(fail):
    def handler(request):
        if request.url.path == "/can/frames" and "since_id" not in request.url.params:
            return httpx.Response(200, json={"frames": [], "truncated": False})
        if request.url.path == "/status":
            return httpx.Response(200, json={"capture": "c"})
        return fail(request)
    return handler


def test_a_can_follow_on_a_daemon_answering_500_gives_up_as_1_not_unreachable(
    monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(cli, "FOLLOW_GIVE_UP_S", 0.2)
    monkeypatch.setattr(cli, "FOLLOW_POLL_S", 0.01)
    _mock(monkeypatch, _failing_poll(lambda r: httpx.Response(500, json={"error": "boom"})))
    rc = cli.main(["can", "dump", "-n", "0", "-f"])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "kept failing for 0.2s" in err and "unreachable" not in err, err


def test_a_can_follow_whose_polls_cannot_connect_still_gives_up_as_3(monkeypatch, capsys) -> None:
    """The control: a transport failure is the unreachable daemon exit 3 names."""
    monkeypatch.setattr(cli, "FOLLOW_GIVE_UP_S", 0.2)
    monkeypatch.setattr(cli, "FOLLOW_POLL_S", 0.01)

    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    _mock(monkeypatch, _failing_poll(refuse))
    rc = cli.main(["can", "dump", "-n", "0", "-f"])
    err = capsys.readouterr().err
    assert rc == 3, err
    assert "daemon unreachable at" in err and "for 0.2s" in err, err


def test_detach_quotes_the_alias_so_a_query_character_cannot_pick_another_port(
    monkeypatch, capsys,
) -> None:
    seen = _mock(monkeypatch, lambda r: httpx.Response(400, json={"error": "no such port"}))
    rc = cli.main(["detach", "board?x"])
    assert rc == 1
    assert [r.url.raw_path for r in seen] == [b"/ports/board%3Fx"]


def test_detach_refuses_a_slash_before_any_request(monkeypatch, capsys) -> None:
    """A 404 there would be read as a daemon too old for the route."""
    seen = _mock(monkeypatch, lambda r: httpx.Response(404, json={"error": "Not Found"}))
    rc = cli.main(["detach", "a/b"])
    assert rc == 1 and seen == []
    assert "an alias cannot contain '/'" in capsys.readouterr().err


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
