"""Sweep follow-ups: `mcu assert --last-ms` bounds, and a FIFO at the pid record path."""

from __future__ import annotations

import os
import sys
import threading

import pytest

from mcuscope import cli
from mcuscope.pidfile import read_pid_record

UNREACHABLE = ["--url", "http://127.0.0.1:1"]


@pytest.mark.parametrize("value", ["0", "-5000", str(10**15 + 1)])
def test_assert_last_ms_out_of_range_is_a_usage_error_before_any_request(value, capsys) -> None:
    rc = cli.main([*UNREACHABLE, "assert", "--forbid", "ERR", "--last-ms", value])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "is not in the range" in err
    assert "unreachable" not in err, "the bound was left to the daemon"


def test_assert_last_ms_in_range_reaches_the_daemon(capsys) -> None:
    """Positive control: the same command with a valid window gets as far as the request."""
    rc = cli.main([*UNREACHABLE, "assert", "--forbid", "ERR", "--last-ms", "1"])
    err = capsys.readouterr().err
    assert rc == 3, err
    assert "unreachable" in err


def _read_with_deadline(path: str) -> list:
    got: list = []
    t = threading.Thread(target=lambda: got.append(read_pid_record(path)), daemon=True)
    t.start()
    t.join(5)
    return got


@pytest.mark.skipif(sys.platform == "win32", reason="mkfifo is POSIX-only")
def test_a_fifo_at_the_pid_path_reads_as_no_record_without_blocking(tmp_path) -> None:
    fifo = tmp_path / "mcuscoped.pid"
    os.mkfifo(fifo)
    assert _read_with_deadline(str(fifo)) == [None], "read_pid_record blocked on a FIFO"


def test_a_regular_pid_record_still_reads(tmp_path) -> None:
    """Positive control for the FIFO guard: the same reader returns a plain record's pid."""
    rec = tmp_path / "mcuscoped.pid"
    rec.write_text("1234\n", encoding="utf-8", newline="")
    assert _read_with_deadline(str(rec)) == [1234]


def test_a_cmd_parked_at_shutdown_answers_the_shutdown_503(make_stack) -> None:
    """`POST /cmd` holds no subscription, so only the stop race can cut it short (class 65)."""
    import time

    import httpx

    from tests.test_prerelease_daemon_core_shutdown import _call_on_loop

    stack = make_stack(["--drop-response", "1000000"])
    store = stack.app.state.store
    port = stack.app.state.ports.get(stack.alias)
    core = stack.sim.links[-1]._source._sim.sim
    deadline = time.monotonic() + 10
    while core.cmd_count < 1 or port._pending:   # the identify ping has come and gone
        assert time.monotonic() < deadline, "identify never finished"
        time.sleep(0.01)
    stack._sim_args.drop_response = core.cmd_count + 1   # swallow the call's own command
    out: list = []

    def go() -> None:
        with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
            out.append(c.post("/cmd", json={"cmd": "ping", "timeout_ms": 20_000}))

    t = threading.Thread(target=go, daemon=True)
    t.start()
    while not port._pending:
        assert time.monotonic() < deadline + 10, "the command never went out"
        time.sleep(0.01)
    _call_on_loop(stack, store.stop_subscribers)
    t.join(4)
    assert out, "the command stayed parked past the stop"
    assert out[0].status_code == 503, out[0].text
    assert out[0].json()["error"] == "daemon is shutting down; the command was cut short"
    assert not port._pending, "the cancelled command is still pending"


def test_a_cmd_that_completes_is_unchanged(stack) -> None:
    """Positive control: the race returns the command's own answer."""
    import httpx

    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        r = c.post("/cmd", json={"cmd": "ping", "timeout_ms": 5_000})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok", r.text


@pytest.mark.parametrize("argv", [
    ["wait", "--match", "READY"],
    ["assert", "--expect", "READY", "--timeout", "100"],
])
def test_a_daemon_that_never_answers_the_verdict_is_exit_1_not_2(monkeypatch, capsys, argv) -> None:
    """Exit 2 on `wait` means "nothing matched"; a transport timeout is not a verdict."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setattr(cli.Client, "open",
                        lambda self: httpx.Client(transport=httpx.MockTransport(handler)))
    rc = cli.main([*UNREACHABLE, *argv])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "stopped answering" in err


def test_wait_that_matches_nothing_is_still_exit_2(monkeypatch, capsys) -> None:
    """Positive control: the daemon's own timeout verdict keeps exit 2."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "timeout", "line": None})

    monkeypatch.setattr(cli.Client, "open",
                        lambda self: httpx.Client(transport=httpx.MockTransport(handler)))
    rc = cli.main([*UNREACHABLE, "wait", "--match", "READY"])
    assert rc == 2, capsys.readouterr().err


class _Spawned(Exception):
    pass


def _answer_status(monkeypatch, code: int, **kw) -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, **kw)

    monkeypatch.setattr(cli.Client, "open",
                        lambda self: httpx.Client(transport=httpx.MockTransport(handler)))

    def fake_popen(args, **kwargs):
        raise _Spawned(args)

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)


@pytest.mark.parametrize("code", [401, 403, 429])
@pytest.mark.parametrize("sub", ["status", "start"])
def test_a_daemon_refusing_the_probe_is_running_not_absent(monkeypatch, capsys, code, sub) -> None:
    _answer_status(monkeypatch, code, json={"error": "guard-sentinel-ZZ"})
    rc = cli.main([*UNREACHABLE, "daemon", sub])   # a _Spawned escaping here is the bug
    err = capsys.readouterr().err
    assert rc == 1, err
    assert f"refused the request (HTTP {code}): guard-sentinel-ZZ" in err


def test_a_stray_service_answering_403_is_still_not_mcuscoped(monkeypatch, capsys) -> None:
    """Positive control: a 403 without the daemon's error envelope is not our daemon."""
    _answer_status(monkeypatch, 403, json={"detail": "forbidden"})
    rc = cli.main([*UNREACHABLE, "daemon", "status"])
    err = capsys.readouterr().err
    assert rc == 3, err
    assert "refused the request" not in err


def _frames_daemon(monkeypatch, total: int, honour_id_to: bool = True) -> list:
    """A /can/frames double with the daemon's 1000-frame cap, newest first."""
    import httpx

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params
        seen.append(dict(q))
        if request.url.path == "/status":
            return httpx.Response(200, json={"capture": "c1"})
        ids = list(range(1, total + 1))
        if "since_id" in q:
            ids = [i for i in ids if i > int(q["since_id"])]
        if honour_id_to and "id_to" in q:
            ids = [i for i in ids if i <= int(q["id_to"])]
        ids.reverse()
        cap = min(int(q.get("limit", 100)), 1000)
        page = [{"line_id": i, "ts": 1.0, "tick_ms": None, "bus": 1, "can_id": 256, "ext": 0,
                 "rtr": 0, "dlc": 1, "data_hex": "00"} for i in ids[:cap]]
        return httpx.Response(200, json={"frames": page, "truncated": len(ids) > cap})

    monkeypatch.setattr(cli.Client, "open",
                        lambda self: httpx.Client(transport=httpx.MockTransport(handler)))
    return seen


def _printed_ids(out: str) -> list:
    import json as _json
    return [_json.loads(line)["line_id"] for line in out.splitlines() if line.strip()]


def test_can_dump_n_pages_past_the_daemon_cap(monkeypatch, capsys) -> None:
    _frames_daemon(monkeypatch, 3000)
    rc = cli.main([*UNREACHABLE, "--json", "can", "dump", "-n", "2500"])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    ids = _printed_ids(cap.out)
    assert ids == list(range(501, 3001)), (len(ids), ids[:3], ids[-3:])
    assert "truncated at 2500 rows" in cap.err


def test_can_dump_n_above_the_window_prints_all_and_no_note(monkeypatch, capsys) -> None:
    _frames_daemon(monkeypatch, 3000)
    rc = cli.main([*UNREACHABLE, "--json", "can", "dump", "-n", "5000"])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    assert _printed_ids(cap.out) == list(range(1, 3001))
    assert "truncated" not in cap.err


def test_can_dump_against_a_daemon_ignoring_id_to_stops_and_notes(monkeypatch, capsys) -> None:
    seen = _frames_daemon(monkeypatch, 3000, honour_id_to=False)
    rc = cli.main([*UNREACHABLE, "--json", "can", "dump", "-n", "5000"])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    assert len(seen) <= 3, f"paged the same page {len(seen)} times"
    assert "truncated at" in cap.err


def _client():
    from mcuscope.cli_client import Settings
    return cli.Client(Settings(url="http://127.0.0.1:1", json_out=False, port=None))


def test_a_follow_poll_pages_every_frame_past_the_watermark(monkeypatch) -> None:
    _frames_daemon(monkeypatch, 3000)
    got = cli._poll_new_frames(_client(), {"limit": 1000}, since=500)
    assert [f["line_id"] for f in got] == list(range(3000, 500, -1))


def test_a_follow_poll_after_a_capture_change_replays_one_page(monkeypatch) -> None:
    _frames_daemon(monkeypatch, 3000)
    got = cli._poll_new_frames(_client(), {"limit": 1000}, since=0)
    assert [f["line_id"] for f in got] == list(range(3000, 2000, -1))


def test_a_follow_poll_against_a_daemon_ignoring_id_to_returns(monkeypatch) -> None:
    _frames_daemon(monkeypatch, 3000, honour_id_to=False)
    got: list = []
    t = threading.Thread(
        target=lambda: got.append(cli._poll_new_frames(_client(), {"limit": 1000}, since=500)),
        daemon=True)
    t.start()
    t.join(5)
    assert got, "the poll paged the same page for ever"
    assert len(got[0]) == 1000


def test_can_dump_ignoring_id_to_prints_no_frame_twice(monkeypatch, capsys) -> None:
    _frames_daemon(monkeypatch, 3000, honour_id_to=False)
    assert cli.main([*UNREACHABLE, "--json", "can", "dump", "-n", "5000"]) == 0
    ids = _printed_ids(capsys.readouterr().out)
    assert len(ids) == len(set(ids)) == 1000
