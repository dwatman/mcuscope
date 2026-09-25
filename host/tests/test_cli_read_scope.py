"""Read commands: port scope and refusals, the port column, and `--since-id` paging (SPEC 4)."""

from __future__ import annotations

import json
import re

import httpx
import pytest
import typer

from mcuscope import cli
from tests.support import UNREACHABLE, recorder
from tests.test_cli import run_mcu_canned


def _row(i: int, port: str = "a", raw: str | None = None) -> dict:
    return {"id": i, "ts": 1.0 + i / 1000, "port": port, "dir": "rx", "chan": "debug",
            "seq": None, "raw": raw or f"row {i}"}


STATUS = {"version": "9.9.9", "uptime_s": 1, "db_path": "x", "ports": []}


# -- an unknown -p refused by the daemon is exit 1 on every read ---------------------------


@pytest.mark.parametrize("argv", [
    ["lines"], ["tail", "-n", "1"], ["log", "export", "--limit", "1"], ["log", "export"],
    ["can", "dump", "-n", "1"], ["plot", "channels"],
])
def test_a_read_naming_no_such_port_is_exit_1(monkeypatch, capsys, argv) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("port") == "nosuch":
            return httpx.Response(400, json={"error": "no such port: nosuch"})
        return httpx.Response(200, json={**STATUS, "lines": [], "frames": [],
                                         "channels": [], "truncated": False})

    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler, "-p", "nosuch", *argv)
    assert rc == 1, err
    assert "no such port: nosuch" in err


# -- the port column --------------------------------------------------------------------


def _lines_handler(rows: list[dict], ports: int = 1):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ports":
            return httpx.Response(200, json={"ports": [{"alias": f"p{i}"} for i in range(ports)]})
        port = request.url.params.get("port")   # the daemon's own -p scope
        page = [r for r in rows if port is None or r["port"] == port]
        return httpx.Response(200, json={"lines": page[::-1], "truncated": False})
    return handler


def test_rows_from_two_boards_carry_the_port(monkeypatch, capsys) -> None:
    rows = [_row(1, "a"), _row(2, "b")]
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, _lines_handler(rows), "lines")
    assert rc == 0
    assert "[a]  debug| row 1" in out and "[b]  debug| row 2" in out


def test_rows_from_one_board_or_under_p_carry_none(monkeypatch, capsys) -> None:
    rows = [_row(1, "a"), _row(2, "a")]
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, _lines_handler(rows), "lines")
    assert rc == 0 and "[a]" not in out and "row 2" in out
    both = [_row(1, "a"), _row(2, "b")]
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, _lines_handler(both), "-p", "a", "lines")
    assert rc == 0 and "[" not in out and "row 1" in out


@pytest.mark.parametrize("ports", [["a"], ["a", "b"]])
def test_a_text_export_is_streamed_as_the_daemon_renders_it(monkeypatch, capsys,
                                                            ports) -> None:
    """The daemon renders `[port]` itself, so neither case pages `/lines` to render it."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/ports":
            return httpx.Response(200, json={"ports": [{"alias": a} for a in ports]})
        if request.url.path == "/lines/export":
            return httpx.Response(200, text="daemon rendered\n")
        return httpx.Response(200, json={"lines": [], "truncated": False})

    rc, out, _ = run_mcu_canned(monkeypatch, capsys, handler, "log", "export")
    assert rc == 0 and out == "daemon rendered\n"
    assert paths == ["/lines/export"], paths


# -- --since-id walks upwards -------------------------------------------------------------


def test_since_id_returns_the_next_rows_above_the_id_across_pages(monkeypatch, capsys) -> None:
    """1500 rows above id 100 exist; `--limit 1200` must return 101..1300, not the newest."""
    asked: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        asked.append(q)
        assert q.get("order") == "asc", q
        lo, n = int(q["since_id"]), int(q["limit"])
        ids = [i for i in range(lo + 1, 1601)][:n]
        return httpx.Response(200, json={"lines": [_row(i) for i in ids],
                                         "truncated": ids[-1] < 1600})

    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler,
                                  "--json", "lines", "--since-id", "100", "--limit", "1200")
    assert rc == 0, err
    body = json.loads(out)
    ids = [r["id"] for r in body["lines"]]
    assert ids == list(range(1300, 100, -1))    # newest first, as `lines --json` always is
    assert body["truncated"] is True
    assert [(q["since_id"], q["limit"]) for q in asked] == [("100", "1000"), ("1100", "200")]


def test_since_id_note_says_newer_rows_follow_and_where(monkeypatch, capsys) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        lo = int(request.url.params["since_id"])
        return httpx.Response(200, json={"lines": [_row(lo + 1), _row(lo + 2)],
                                         "truncated": True})

    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler,
                                  "lines", "--since-id", "7", "--limit", "2")
    assert rc == 0
    assert out.index("row 8") < out.index("row 9")   # text: oldest first
    assert "newer matches exist (raise --limit or call again with --since-id 9)" in err


def test_without_since_id_the_note_no_longer_offers_it(monkeypatch, capsys) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"lines": [_row(2), _row(1)], "truncated": True})

    rc, _, err = run_mcu_canned(monkeypatch, capsys, handler, "lines", "--limit", "2")
    assert rc == 0
    assert "older matches exist (raise --limit or use 'mcu log export' for every row)" in err
    assert "--since-id" not in err


# -- -n 0 is the follow-only form, not a truncated result ---------------------------------


def test_tail_n0_prints_no_truncation_note(monkeypatch, capsys) -> None:
    def truncated(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"lines": [_row(1)], "frames": [], "truncated": True})

    rc, _, err = run_mcu_canned(monkeypatch, capsys, truncated, "tail", "-n", "0")
    assert rc == 0 and "truncated" not in err
    rc, _, err = run_mcu_canned(monkeypatch, capsys, truncated, "can", "dump", "-n", "0")
    assert rc == 0 and "truncated" not in err
    # Positive control: -n 1 over the same answer still notes it.
    rc, _, err = run_mcu_canned(monkeypatch, capsys, truncated, "tail", "-n", "1")
    assert "truncated" in err


# -- plot channels honours -p ------------------------------------------------------------


def test_plot_channels_sends_the_port(monkeypatch, capsys) -> None:
    asked: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(dict(request.url.params))
        return httpx.Response(200, json={"channels": []})

    run_mcu_canned(monkeypatch, capsys, handler, "-p", "sim", "plot", "channels")
    run_mcu_canned(monkeypatch, capsys, handler, "plot", "channels")
    assert asked == [{"port": "sim"}, {}]


# -- a paged export that dies mid-walk leaves no file (HEALTH-15 C06) --------------------


def test_a_paged_export_that_dies_mid_walk_leaves_no_file(monkeypatch, capsys,
                                                         tmp_path) -> None:
    out_file = tmp_path / "log.txt"

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params
        if request.url.path == "/ports":
            return httpx.Response(200, json={"ports": []})
        if "match" in q:                       # the decoder's `!pd` prime query
            return httpx.Response(200, json={"lines": [], "truncated": False})
        if q.get("since_id") == "1":           # the second page of the walk
            return httpx.Response(500, json={"error": "daemon fell over"})
        return httpx.Response(200, json={"lines": [_row(1)], "truncated": True})

    rc, _, err = run_mcu_canned(monkeypatch, capsys, handler,
                                "log", "export", "--decode", "-o", str(out_file))
    assert rc == 1, err
    assert "daemon fell over" in err
    assert not out_file.exists(), "a partial export was left behind"


# -- B-6: the calendar's ends are usage errors ----------------------------------------------


@pytest.mark.parametrize("argv", [
    ["lines", "--from", "9999-12-31T23:59"],
    ["lines", "--from", "0001-01-01T00:00"],
    ["lines", "--to", "0001-01-01T00:00"],
    ["can", "dump", "-n", "1", "--from", "9999-12-31T23:59"],
])
def test_a_clock_at_the_calendar_limit_is_bad_usage(capsys, argv) -> None:
    rc = cli.main(["--json", *argv, *UNREACHABLE])
    out = capsys.readouterr()
    assert rc == 1, out.err
    assert json.loads(out.out)["exit_code"] == 1
    assert "expected HH:MM" in out.err and "Traceback" not in out.err


# -- B-11: the truncation note names options its command has --------------------------------


def _flags_of(*path: str) -> set[str]:
    command = typer.main.get_command(cli.app)
    for name in path:
        command = command.commands[name]
    return {o for prm in command.params for o in getattr(prm, "opts", [])}


@pytest.mark.parametrize(("argv", "path"), [
    (["tail", "-n", "2"], ("tail",)),
    (["log", "export", "--limit", "2"], ("log", "export")),
    (["lines", "--limit", "2"], ("lines",)),
])
@pytest.mark.parametrize("rows", [2, 1])
def test_the_truncation_note_names_only_the_commands_own_options(monkeypatch, capsys, argv,
                                                                  path, rows) -> None:
    body = [{"id": i, "ts": 0.0, "chan": "debug", "raw": "x"} for i in range(rows, 0, -1)]
    recorder(monkeypatch, lines={"lines": body, "truncated": True})
    rc = cli.main([*argv, *UNREACHABLE])
    note = [ln for ln in capsys.readouterr().err.splitlines() if "truncated" in ln]
    assert rc == 0 and note, note
    named = set(re.findall(r"(?<![\w-])(--?[a-z][\w-]*)", note[0].split("(", 1)[1]))
    assert named or "'mcu log export'" in note[0], note
    assert named <= _flags_of(*path), (note, named - _flags_of(*path))


# -- B-12: usage refusals before the version request ----------------------------------------


@pytest.mark.parametrize(("argv", "msg"), [
    (["plot", "export", "--names", "ramp", "--changes", "--from", "10:00"],
     "changes requires decode"),
    (["plot", "export", "--names", "ramp", "--deadband", "r=1", "--to", "23:00"],
     "deadband requires changes"),
    (["log", "export", "--csv", "--limit", "5", "--from", "10:00"], "does not take --limit"),
])
def test_a_usage_error_with_clock_bounds_costs_no_request(capsys, argv, msg) -> None:
    rc = cli.main([*argv, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert msg in err, err


# -- B-15: --last-ms bounds ------------------------------------------------------------------


@pytest.mark.parametrize("value", ["-5000", str(10**15 + 1)])
@pytest.mark.parametrize("argv", [
    ["lines"], ["log", "export"], ["can", "dump"], ["plot", "export", "--names", "ramp"],
])
def test_last_ms_out_of_range_is_bad_usage(capsys, argv, value) -> None:
    rc = cli.main([*argv, "--last-ms", value, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "--last-ms" in err, err


def test_last_ms_at_its_bounds_is_accepted(monkeypatch, capsys) -> None:
    recorder(monkeypatch, status={**STATUS, "now": 1.0e9},
             lines={"lines": [], "truncated": False})
    for value in ("0", str(10**15)):
        assert cli.main(["lines", "--last-ms", value, *UNREACHABLE]) == 0, \
            capsys.readouterr().err


# -- measurement F2: the truncation note reports what came back -------------------------


def test_truncation_note_reports_the_returned_rows_not_the_request(capsys) -> None:
    """The daemon caps /lines at 1000 below any bigger request.

    Naming the request read as "your limit did this" and offered a remedy ("raise
    --limit") that is inert above the cap.
    """
    from mcuscope.cli_output import note_truncated

    body = {"lines": [{"id": i} for i in range(1000)], "truncated": True}
    note_truncated(body, 20000)
    err = capsys.readouterr().err
    assert "truncated at 1000 rows" in err
    assert "raise --limit" not in err
    assert "use 'mcu log export' for every row" in err


def test_truncation_note_still_offers_a_bigger_limit_when_the_user_capped_it(capsys) -> None:
    from mcuscope.cli_output import note_truncated

    body = {"lines": [{"id": i} for i in range(5)], "truncated": True}
    note_truncated(body, 5)
    err = capsys.readouterr().err
    assert "truncated at 5 rows" in err and "raise --limit" in err


# -- R16-3: an ascending walk continues past a malformed last row --------------------------


def _walk_handler(asked: list[dict], first: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        if request.url.path == "/ports":
            return httpx.Response(200, json={"ports": []})
        if "match" in q:                       # the decoder's `!pd` prime query
            return httpx.Response(200, json={"lines": [], "truncated": False})
        asked.append(q)
        if q.get("since_id", "0") == "0":
            return httpx.Response(200, json={"lines": first, "truncated": True})
        return httpx.Response(200, json={"lines": [_row(3), _row(4)], "truncated": False})
    return handler


def test_an_export_walk_continues_from_the_highest_integer_id(monkeypatch, capsys,
                                                              tmp_path) -> None:
    out_file = tmp_path / "log.txt"
    asked: list[dict] = []
    first = [_row(1), {**_row(2), "id": "2"}]
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _walk_handler(asked, first),
                                  "log", "export", "--decode", "--limit", "0",
                                  "-o", str(out_file))
    assert rc == 0, err
    assert [q.get("since_id") for q in asked] == [None, "1"], asked
    assert "row 4" in out_file.read_text()


def test_a_truncated_page_with_no_row_id_ends_the_export_as_a_failure(monkeypatch, capsys,
                                                                     tmp_path) -> None:
    out_file = tmp_path / "log.txt"
    first = [{**_row(1), "id": None}, {**_row(2), "id": "2"}]
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _walk_handler([], first),
                                  "log", "export", "--decode", "--limit", "0",
                                  "-o", str(out_file))
    assert rc == 1, err
    assert "a truncated page after id 0 carries no row id to continue from" in err
    assert not out_file.exists()


def test_since_id_walk_continues_from_the_highest_integer_id(monkeypatch, capsys) -> None:
    asked: list[dict] = []
    first = [_row(1), {**_row(2), "id": "2"}]
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _walk_handler(asked, first),
                                  "lines", "--since-id", "0", "--limit", "10")
    assert rc == 0, err
    assert [q.get("since_id") for q in asked] == ["0", "1"], asked
    assert "row 4" in out


# -- R44-1: --last-ms counts back from the daemon's clock -----------------------------------


@pytest.mark.parametrize("argv", [["lines"], ["can", "dump", "-n", "1"]])
def test_last_ms_is_anchored_on_the_daemons_now(monkeypatch, capsys, argv) -> None:
    import math
    import time

    now = time.time() + 300.0   # the daemon's clock runs 5 minutes ahead of this one
    asked: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={**STATUS, "now": now})
        asked.append(dict(request.url.params))
        return httpx.Response(200, json={"lines": [], "frames": [], "truncated": False})

    rc, _, err = run_mcu_canned(monkeypatch, capsys, handler, *argv, "--last-ms", "1000")
    assert rc == 0, err
    assert float(asked[-1]["since_ts"]) == math.nextafter(now - 1.0, -math.inf), asked
