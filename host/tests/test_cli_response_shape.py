"""The CLI reads a current daemon's answers as SPEC 3.4 states them (no older-daemon
fallbacks, D-16), and the guards that keep a malformed field from steering a read."""

from __future__ import annotations

import json

import httpx
import pytest

from mcuscope import cli
from tests.support import STATUS, UNREACHABLE, canned, record_requests, recorder
from tests.test_cli_follow import _row, _tail_follow


def test_a_ws_frame_that_is_not_an_array_is_a_bad_frame(monkeypatch, capsys) -> None:
    rc, out, err = _tail_follow(monkeypatch, capsys, [_row(1), [_row(2)]])
    assert "skipping bad frame: frame is not an array of rows" in err, err
    assert "row 1" not in out
    assert "row 2" in out, out   # positive control: the array frame after it prints


@pytest.mark.parametrize("now", [None, True, "1e9"], ids=["absent", "bool", "string"])
def test_last_ms_against_a_status_without_a_numeric_now_is_exit_1(monkeypatch, capsys,
                                                                   now) -> None:
    status = {**STATUS} if now is None else {**STATUS, "now": now}
    seen = recorder(monkeypatch, status=status, lines={"lines": [], "truncated": False})
    rc = cli.main(["lines", "--last-ms", "1000", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "/status 'now' is not a number" in err, err
    assert [r.url.path for r in seen] == ["/status"]   # no read on a made-up anchor


def test_last_ms_counts_back_from_the_daemons_now(monkeypatch, capsys) -> None:
    """Positive control, and the anchor is the daemon's clock, not this host's."""
    seen = recorder(monkeypatch, status={**STATUS, "now": 1000.0},
                    lines={"lines": [], "truncated": False})
    assert cli.main(["lines", "--last-ms", "250", *UNREACHABLE]) == 0, capsys.readouterr().err
    since = [float(r.url.params["since_ts"]) for r in seen if r.url.path == "/lines"]
    assert since and 999.74 < since[0] < 999.76, since


def test_a_session_anchor_whose_ts_is_a_bool_falls_back_to_now(monkeypatch, capsys) -> None:
    """`True` is an int: taken as the anchor, the window would start at the epoch."""
    session = {"id": 3, "name": "run", "end_id": 9}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions":
            return httpx.Response(200, json={"sessions": [session]})
        if request.url.path == "/status":
            return httpx.Response(200, json={**STATUS, "now": 1000.0})
        if "id_to" in request.url.params:
            return httpx.Response(200, json={"lines": [{"id": 9, "ts": True}]})
        return httpx.Response(200, json={"lines": [], "truncated": False})

    seen = record_requests(monkeypatch, handler)
    rc = cli.main(["lines", "--session", "run", "--last-ms", "250", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    since = [float(r.url.params["since_ts"]) for r in seen
             if r.url.path == "/lines" and "since_ts" in r.url.params]
    assert since and 999.74 < since[0] < 999.76, since


@pytest.mark.parametrize("argv, body, missing", [
    (["--send", "ping"], {}, "'sends'"),
    (["--send", "ping", "--repeat-ms", "100"], {"sends": 3}, "'send_failures'"),
])
def test_a_wait_answer_without_the_send_counters_is_exit_1(monkeypatch, capsys, argv, body,
                                                           missing) -> None:
    """SPEC 3.4: both are always present; a missing one is not read as "sent nothing"."""
    recorder(monkeypatch, wait={"status": "timeout", "waited_ms": 1.0, **body})
    rc = cli.main(["wait", "--match", "x", "--timeout", "1000", *argv, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "unexpected response from daemon" in err and missing in err, err


# -- the `[port]` column of a stream (X-cli-5 M12, M12b) -------------------------------------

TWO_BOARDS = {"ports": [{"alias": "a"}, {"alias": "b"}], "stored": ["a", "b"]}
ROWS = {"lines": [{"id": 1, "ts": 1.0, "port": "a", "dir": "rx", "chan": "debug", "seq": None,
                   "raw": "from a"}], "truncated": False}


def test_a_paged_export_with_p_prints_no_port_column(monkeypatch, capsys) -> None:
    recorder(monkeypatch, ports=TWO_BOARDS, lines=ROWS)
    assert cli.main(["-p", "a", "log", "export", "--limit", "5", *UNREACHABLE]) == 0
    out = capsys.readouterr().out
    assert "from a" in out and "[a]" not in out, out
    # Positive control: the same two boards without -p carry the column.
    assert cli.main(["log", "export", "--limit", "5", *UNREACHABLE]) == 0
    assert "[a] " in capsys.readouterr().out


def test_a_json_export_does_not_ask_for_the_boards(monkeypatch, capsys) -> None:
    """The column is text-only, so --json spends no /ports request on it."""
    seen = recorder(monkeypatch, ports=TWO_BOARDS, lines=ROWS)
    assert cli.main(["--json", "log", "export", "--limit", "5", *UNREACHABLE]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[0])["raw"] == "from a"
    assert "/ports" not in [r.url.path for r in seen]
    assert cli.main(["log", "export", "--limit", "5", *UNREACHABLE]) == 0   # positive control
    assert "/ports" in [r.url.path for r in seen]


# -- bool is an int: the id guards (X-cli-5 M2, M24) -----------------------------------------


def test_a_bool_id_is_no_row_id_to_continue_from() -> None:
    assert cli._highest_id([{"id": True}]) is None
    assert cli._highest_id([{"id": True}, {"id": 4}]) == 4


def test_a_bool_next_since_id_is_not_a_watermark(monkeypatch) -> None:
    def answer(mark):
        canned(monkeypatch, lambda request: httpx.Response(
            200, json={"frames": [], "truncated": False, "next_since_id": mark}))
        covered: list[int] = []
        s = cli.Settings(url="http://127.0.0.1:1", json_out=False, port=None)
        cli._poll_new_frames(cli.Client(s), {}, 1, covered)
        return covered

    assert answer(True) == []
    assert answer(5) == [5]   # positive control
