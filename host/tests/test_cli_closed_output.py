"""The CLI with its output closed or full (classes 9, 35, 70): the exit code survives a failed
write, and no crash log is left.

Stream cases run the real console entry in a child: stdout a pipe whose read end is closed, or
`/dev/full`, with the crash-log dir bound to tmp_path. A daemon comes from a canned transport
in the child, or the in-process stack where a WebSocket is needed."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import httpx
import pytest

from tests.support import CHILD_TEXT, Stack, child_env

# ROUTES maps a path to [status, body]; a path not listed answers 404.
# The crash-log dir is pinned twice over: MCUSCOPE_DATA_DIR in the env (which wins, and works
# on Windows), and the platformdirs patch below for the resolution behind it.
CHILD = """
import json, os, sys, platformdirs
DATA = sys.argv[1]                     # bound now: sys.argv is replaced before the CLI runs
platformdirs.user_data_dir = lambda *a, **k: DATA
routes = json.loads(os.environ.get("SWEEP_ROUTES", "{}"))
if routes:
    import httpx
    from mcuscope import __version__, cli_client
    def handler(request):
        status, body = routes.get(request.url.path, [404, {"error": "Not Found"}])
        return httpx.Response(status, json=body,
                              headers={cli_client.VERSION_HEADER: __version__})
    real_open = cli_client.Client.open
    def open_(self):
        self._transport = httpx.MockTransport(handler)
        return real_open(self)
    cli_client.Client.open = open_
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
    env = child_env(MCUSCOPE_URL=url, SWEEP_ROUTES=json.dumps(routes or {}),
                    MCUSCOPE_DATA_DIR=str(data))
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


@pytest.mark.child_crash_expected
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
