"""CLI leg of the 2026-09-15 pre-release round (B-1..B-18, F-4, F-12, G-1).

Driven in process against a canned transport, as test_cli_r2026_09_12 is: each case is one
refusal, one message, one exit code or the bytes one write produces. A refusal that must not
reach the network runs against an unreachable url, where exit 1 proves the CLI judged it.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys

import httpx
import pytest
import typer

from mcuscope import cli
from mcuscope.cli_client import Settings
from mcuscope.cli_output import LineDecoder
from tests.test_cli import _ScriptedWS
from tests.test_cli_r2026_09_12 import DEAD, STATUS, UNREACHABLE, canned, paths, recorder

OLD = {**STATUS, "version": "0.3.0"}


def dying_body(*chunks: bytes):
    """A response body that sends `chunks` and then loses the connection."""
    def gen():
        yield from chunks
        raise httpx.ReadError("peer died")
    return gen()


# -- G-1: only the shutdown 503 is "unreachable" -----------------------------------------


@pytest.mark.parametrize("argv", [
    ["wait", "--match", "x"],
    ["assert", "--expect", "x", "--timeout", "1000"],
])
def test_the_subscriber_cap_503_is_exit_1_not_unreachable(monkeypatch, capsys, argv) -> None:
    """A daemon at its subscriber cap is running; exit 3 sends an agent to restart it."""
    msg = "too many subscribers (max 256)"
    canned(monkeypatch, lambda request: httpx.Response(503, json={"error": msg}))
    rc = cli.main(["--json", *argv, *UNREACHABLE])
    out = capsys.readouterr()
    assert rc == 1, out.err
    assert json.loads(out.out) == {"error": f"error: {msg}", "exit_code": 1}


def test_a_503_that_only_mentions_shutdown_is_not_the_shutdown_answer(monkeypatch,
                                                                     capsys) -> None:
    """The prefix is the daemon's own sentence, not a substring anywhere in a proxy page."""
    canned(monkeypatch, lambda request: httpx.Response(
        503, text="upstream says: daemon is shutting down"))
    rc = cli.main(["wait", "--match", "x", *UNREACHABLE])
    assert rc == 1, capsys.readouterr().err


# -- B-1: a session-scoped follow stays in the session -----------------------------------


def test_can_dump_follow_polls_inside_the_session(monkeypatch, capsys) -> None:
    import time

    frames: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={**STATUS, "capture": "A"})
        frames.append(request)
        if "since_id" in request.url.params:
            raise KeyboardInterrupt      # the first live poll is all this needs
        return httpx.Response(200, json={"frames": []})

    canned(monkeypatch, handler)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    rc = cli.main(["can", "dump", "--session", "runA", "-f", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    polls = [r for r in frames if "since_id" in r.url.params]
    assert polls, "the follow never polled"
    assert all(r.url.params.get("session") == "runA" for r in frames), \
        [str(r.url) for r in frames]


# -- B-2 / B-3: the version gate covers -p on plot export, --eol and --repeat-ms ---------


GATED = [
    (["-p", "nosuch", "plot", "export", "--names", "ramp"], "ignores -p ("),
    (["attach", "socket://127.0.0.1:1", "--alias", "e1", "--eol", "crlf"], "ignores --eol ("),
    (["send", "eoltest", "--eol", "none"], "ignores --eol ("),
    (["cmd", "ping", "--eol", "crlf"], "ignores --eol ("),
    (["wait", "--match", "x", "--send", "ping", "--eol", "none"], "ignores --eol ("),
    (["assert", "--expect", "x", "--send", "ping", "--eol", "none"], "ignores --eol ("),
    (["wait", "--match", "ZZZ", "--send", "", "--repeat-ms", "50", "--timeout", "300"],
     "ignores --repeat-ms ("),
]


@pytest.mark.parametrize(("argv", "named"), GATED, ids=lambda v: " ".join(v)
                         if isinstance(v, list) else None)
def test_a_field_an_older_daemon_drops_is_refused(monkeypatch, capsys, tmp_path, argv,
                                                  named) -> None:
    monkeypatch.chdir(tmp_path)
    seen = recorder(monkeypatch, status=OLD)
    rc = cli.main([*argv, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert named in err and "0.3.0" in err and "0.4.0 or newer" in err, err
    assert paths(seen) == ["/status"], "refused before the request it would have dropped"


@pytest.mark.parametrize(("argv", "named"), GATED, ids=lambda v: " ".join(v)
                         if isinstance(v, list) else None)
def test_the_same_fields_reach_a_current_daemon(monkeypatch, capsys, tmp_path, argv,
                                               named) -> None:
    monkeypatch.chdir(tmp_path)
    seen = recorder(monkeypatch, plot_export="ts,name,value\n",
                    ports={"port": {"alias": "e1", "connected": False}},
                    send={"ok": True}, cmd={"status": "ok", "data": ""},
                    wait={"status": "timeout", "waited_ms": 1.0, "sends": 3,
                          "send_failures": 0},
                    **{"assert": {"status": "pass", "checked_lines": 0, "elapsed_ms": 1.0,
                                  "expect": [], "forbid": []}})
    rc = cli.main([*argv, *UNREACHABLE])
    assert rc in (0, 2), capsys.readouterr().err
    assert paths(seen)[0] == "/status" and len(paths(seen)) == 2, paths(seen)


@pytest.mark.parametrize("argv", [
    ["plot", "export", "--names", "ramp"],
    ["attach", "socket://127.0.0.1:1", "--alias", "e1"],
    ["attach", "socket://127.0.0.1:1", "--alias", "e1", "--eol", "lf"],
    ["send", "x"],
])
def test_no_gate_request_without_a_gated_option(monkeypatch, capsys, argv) -> None:
    """LF is what a pre-0.4.0 port appends anyway, so `--eol lf` on attach costs nothing."""
    seen = recorder(monkeypatch, plot_export="ts,name,value\n",
                    ports={"port": {"alias": "e1", "connected": False}}, send={"ok": True})
    rc = cli.main([*argv, *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert "/status" not in paths(seen), paths(seen)


def test_repeat_counts_are_not_invented_when_the_daemon_sends_none(monkeypatch,
                                                                   capsys) -> None:
    """An unversioned daemon passes the gate; a missing count is not "sent 0 times"."""
    canned(monkeypatch, lambda request: httpx.Response(
        200, json={"status": "timeout", "waited_ms": 1.0}))
    rc = cli.main(["wait", "--match", "x", "--send", "", "--repeat-ms", "50",
                   "--timeout", "1000", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert "sent " not in err, err


# -- B-4: a refused export leaves -o alone -------------------------------------------------


REFUSAL = (400, {"error": "bad regex"})


@pytest.mark.parametrize("argv", [
    ["log", "export", "--match", "("],
    ["log", "export", "--match", "(", "--limit", "5"],
    ["log", "export", "--match", "(", "--decode"],
    ["plot", "export", "--names", "nosuch"],
    ["can", "dump", "--csv"],
])
def test_a_refused_export_keeps_the_file_and_the_link(monkeypatch, capsys, tmp_path,
                                                      argv) -> None:
    recorder(monkeypatch, lines_export=REFUSAL, lines=REFUSAL, plot_export=REFUSAL,
             can_frames=REFUSAL)
    target = tmp_path / "target.txt"
    target.write_text("data\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    plain = tmp_path / "plain.txt"
    plain.write_text("keep\n", encoding="utf-8")
    for out in (link, plain):
        rc = cli.main([*argv, "-o", str(out), *UNREACHABLE])
        assert rc == 1, capsys.readouterr().err
    assert link.is_symlink(), "the refusal removed the link"
    assert target.read_text(encoding="utf-8") == "data\n", "the refusal truncated the target"
    assert plain.read_text(encoding="utf-8") == "keep\n"


def test_a_stream_dying_mid_export_removes_a_file_but_not_a_link(monkeypatch, capsys,
                                                                 tmp_path) -> None:
    canned(monkeypatch, lambda request: httpx.Response(
        200, content=dying_body(b"a line\n")))
    target = tmp_path / "target.txt"
    target.write_text("data\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    plain = tmp_path / "plain.txt"
    for out in (link, plain):
        rc = cli.main(["log", "export", "-o", str(out), *UNREACHABLE])
        assert rc == 3, capsys.readouterr().err
    assert link.is_symlink(), "a link is not the partial file"
    assert not plain.exists(), "a short export reads exactly like a whole one"


def test_a_session_download_dying_mid_stream_keeps_a_link(monkeypatch, capsys,
                                                          tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions":
            return httpx.Response(200, json={"sessions": [{"id": 1, "name": "run"}]})
        return httpx.Response(200, content=dying_body(b"SQLite format 3"))

    canned(monkeypatch, handler)
    target = tmp_path / "target.db"
    target.write_bytes(b"old")
    link = tmp_path / "link.db"
    link.symlink_to(target)
    plain = tmp_path / "plain.db"
    for out in (link, plain):
        rc = cli.main(["session", "export", "run", "-o", str(out), *UNREACHABLE])
        assert rc == 3, capsys.readouterr().err
    assert link.is_symlink()
    assert not plain.exists(), "a truncated .db reads like a whole one"


def test_an_empty_accepted_export_still_writes_an_empty_file(monkeypatch, capsys,
                                                            tmp_path) -> None:
    """Opening at the first chunk must not lose the file of a window with nothing in it."""
    recorder(monkeypatch, lines_export="", lines={"lines": [], "truncated": False})
    for extra in ([], ["--limit", "5"]):
        out = tmp_path / f"empty{len(extra)}.txt"
        rc = cli.main(["log", "export", *extra, "-o", str(out), *UNREACHABLE])
        assert rc == 0, capsys.readouterr().err
        assert out.read_bytes() == b""


# -- B-5: the in-band error is its own JSONL line -------------------------------------------


def test_a_mid_row_failure_leaves_every_stdout_line_parseable(monkeypatch, capsys) -> None:
    canned(monkeypatch, lambda request: httpx.Response(
        200, content=dying_body(b'{"id": 1}\n{"id": 2, "se')))
    rc = cli.main(["--json", "log", "export", *UNREACHABLE])
    out = capsys.readouterr().out
    assert rc == 3
    rows = [json.loads(line) for line in out.splitlines()]
    assert rows[0] == {"id": 1}
    assert rows[-1]["exit_code"] == 3 and len(rows) == 2, out


def test_a_body_without_a_final_newline_is_still_written_whole(monkeypatch, capsys) -> None:
    canned(monkeypatch, lambda request: httpx.Response(200, text="one\ntwo"))
    assert cli.main(["log", "export", *UNREACHABLE]) == 0
    assert capsys.readouterr().out == "one\ntwo"


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


# -- B-7: the paged export does not translate stdout ---------------------------------------


@pytest.mark.parametrize("extra", [["--limit", "1"], ["--decode"]])
def test_the_paged_export_to_stdout_is_not_newline_translated(monkeypatch, extra) -> None:
    row = {"id": 5, "ts": 0.0, "port": "a", "chan": "debug", "raw": "hi"}
    recorder(monkeypatch, lines={"lines": [row], "truncated": False})
    buf = io.BytesIO()
    wrapper = io.TextIOWrapper(buf, encoding="utf-8", newline="\r\n")
    monkeypatch.setattr(sys, "stdout", wrapper)
    rc = cli.main(["log", "export", *extra, *UNREACHABLE])
    wrapper.flush()
    assert rc == 0
    assert b"hi" in buf.getvalue() and b"\r\n" not in buf.getvalue(), buf.getvalue()


# -- B-8: a usage error with stderr closed -------------------------------------------------


CLOSED_STDERR_CHILD = """
import sys
from mcuscope import cli
sys.argv[1:] = {argv!r}
sys.exit(cli.console_entry())
"""


@pytest.mark.skipif(sys.platform == "win32", reason="a closed pipe reads EINVAL there")
@pytest.mark.parametrize("argv", [
    ["--json", "lines", "--bogus"],
    ["lines", "--limit", "-1"],
    ["nosuchcmd"],
])
def test_a_usage_error_with_stderr_closed_keeps_exit_1(argv) -> None:
    r_fd, w_fd = os.pipe()
    os.close(r_fd)
    try:
        proc = subprocess.run(
            [sys.executable, "-c", CLOSED_STDERR_CHILD.format(argv=[*argv, "--url", DEAD])],
            stdout=subprocess.PIPE, stderr=w_fd, timeout=60,
        )
    finally:
        os.close(w_fd)
    assert proc.returncode == 1, proc.stdout
    if "--json" in argv:
        assert json.loads(proc.stdout)["exit_code"] == 1


# -- B-9 / B-10: the session export target and path ----------------------------------------


def test_session_export_goes_by_id_whatever_the_name(monkeypatch, capsys, tmp_path) -> None:
    seen = recorder(monkeypatch, sessions={"sessions": [{"id": 7, "name": "run?x=1#3/b"}]},
                    sessions_7_export="SQLite")
    rc = cli.main(["session", "export", "run?x=1#3/b", "-o", str(tmp_path / "s.db"),
                   *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert paths(seen) == ["/sessions", "/sessions/7/export"], paths(seen)
    assert seen[0].url.params["name"] == "run?x=1#3/b"


def test_session_export_of_no_such_session_downloads_nothing(monkeypatch, capsys,
                                                             tmp_path) -> None:
    """A daemon honouring name= answers no row; the page an older one answers is in
    test_prerelease_fixdiff_py (FP-8)."""
    seen = recorder(monkeypatch, sessions={"sessions": []})
    out = tmp_path / "s.db"
    rc = cli.main(["session", "export", "nope", "-o", str(out), *UNREACHABLE])
    assert rc == 1
    assert "no such session: nope" in capsys.readouterr().err
    assert paths(seen) == ["/sessions"] and not out.exists()


@pytest.mark.parametrize("bundle", [[], ["--bundle"]])
@pytest.mark.parametrize("form", ["slash", "existing"])
def test_session_export_refuses_a_directory_target(capsys, tmp_path, bundle, form) -> None:
    """`existing` is the final path: with --bundle, `-o adir` means `adir.zip`."""
    target = tmp_path / "adir"
    if form == "existing":
        (tmp_path / ("adir.zip" if bundle else "adir")).mkdir()
        arg = str(target)
    else:
        arg = str(target) + "/"
    rc = cli.main(["session", "export", "1", *bundle, "-o", arg, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "is a directory" in err, err
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


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


# -- B-14: can dump -o with --json describes the file --------------------------------------


def test_can_dump_to_a_file_with_json_prints_the_summary(monkeypatch, capsys, tmp_path) -> None:
    recorder(monkeypatch, can_frames="ts,can_id\n1,256\n2,257\n")
    out = tmp_path / "cj.csv"
    rc = cli.main(["--json", "can", "dump", "-o", str(out), *UNREACHABLE])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert json.loads(captured.out) == {"file": str(out), "frames": 2,
                                        "bytes": out.stat().st_size}


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
    recorder(monkeypatch, lines={"lines": [], "truncated": False})
    for value in ("0", str(10**15)):
        assert cli.main(["lines", "--last-ms", value, *UNREACHABLE]) == 0, \
            capsys.readouterr().err


# -- B-17: a blank serial --------------------------------------------------------------------


@pytest.mark.parametrize("serial", ["", "   "])
def test_attach_refuses_a_blank_serial(capsys, serial) -> None:
    rc = cli.main(["attach", "--serial", serial, "--alias", "x", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "--serial is blank" in err, err


def test_attach_strips_the_serial_it_posts(monkeypatch, capsys) -> None:
    seen = recorder(monkeypatch, ports={"port": {"alias": "b", "connected": False}})
    assert cli.main(["attach", "--serial", " 0672FF3 ", *UNREACHABLE]) == 0
    body = json.loads(seen[0].content)
    assert body["serial_number"] == "0672FF3" and body["alias"] == "0672FF3", body


# -- B-18: a route an older daemon lacks names the version ----------------------------------


NOT_FOUND = (404, {"error": "Not Found"})


@pytest.mark.parametrize(("argv", "route"), [
    (["log", "export"], "/lines/export"),
    (["session", "export", "1", "--bundle", "-o", "b.zip"], "/sessions/1/bundle"),
    (["break"], "/break"),
])
def test_a_missing_route_names_the_daemon_version(monkeypatch, capsys, tmp_path, argv,
                                                  route) -> None:
    monkeypatch.chdir(tmp_path)
    recorder(monkeypatch, status=OLD, sessions={"sessions": [{"id": 1, "name": "r"}]},
             lines_export=NOT_FOUND, sessions_1_bundle=NOT_FOUND, **{"break": NOT_FOUND})
    rc = cli.main([*argv, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert f"daemon 0.3.0 does not serve {route}; it needs daemon 0.4.0 or newer" in err, err


def test_a_404_from_a_current_daemon_keeps_its_own_message(monkeypatch, capsys) -> None:
    recorder(monkeypatch, lines_export=NOT_FOUND)
    rc = cli.main(["log", "export", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1 and "error: Not Found" in err and "does not serve" not in err, err


# -- F-12: a follow learns a hidden !pd under its own port ----------------------------------


def test_follow_learns_a_filtered_out_redefinition_under_its_port(monkeypatch,
                                                                  capsys) -> None:
    import websockets

    frames = [
        json.dumps([{"id": 1, "ts": 1.0, "port": "a", "chan": "event", "raw": "!pd 7 amps:u1"}]),
        json.dumps([{"id": 2, "ts": 2.0, "port": "a", "chan": "event", "raw": "!ps 7 1 05"}]),
    ]
    monkeypatch.setattr(websockets, "connect", lambda url, **kw: _ScriptedWS(frames),
                        raising=False)
    dec = LineDecoder()
    dec.prime(["!pd 7 volts:u1"], "a")
    s = Settings(url=DEAD, json_out=False, port=None)
    with pytest.raises(typer.Exit):
        cli._follow_ws(s, None, "^!ps", dec=dec)   # --match hides the !pd row
    out = capsys.readouterr().out
    assert "s7 amps=5" in out, out
