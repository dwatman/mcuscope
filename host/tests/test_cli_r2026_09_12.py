"""CLI leg of the 2026-09-12 adversarial round (C3..C10) and the host improvement leg.

Every test here drives `cli.main` in process against a canned transport: each case is
about one refusal, one message or the bytes one write produces, and the exit-code
contract is the same either way. The refusals that must not reach the network run against
an unreachable url, where exit 1 proves the CLI judged the request itself.
"""

from __future__ import annotations

import builtins
import io
import json
import subprocess
import sys

import httpx
import pytest

from mcuscope import cli

DEAD = "http://127.0.0.1:1"
UNREACHABLE = ["--url", DEAD]

STATUS = {"version": "0.4.0", "uptime_s": 1.0, "db_path": "/tmp/x.db", "ports": []}


def canned(monkeypatch, handler):
    """Point every request this invocation makes at `handler`."""
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(cli.Client, "open", lambda self: httpx.Client(transport=transport))


def recorder(monkeypatch, status=None, **bodies):
    """Canned responses by path, recording every request. Returns the request list.

    `bodies` maps a path with its slashes as underscores (`lines_export`) to a JSON body
    or an (status_code, body) pair; anything unmatched answers `{}`.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/status":
            return httpx.Response(200, json=status or STATUS)
        body = bodies.get(request.url.path.strip("/").replace("/", "_"), {})
        if isinstance(body, tuple):
            return httpx.Response(body[0], json=body[1])
        if isinstance(body, str):
            return httpx.Response(200, text=body)
        return httpx.Response(200, json=body)

    canned(monkeypatch, handler)
    return seen


def paths(seen) -> list[str]:
    return [r.url.path for r in seen]


# -- C3: --from/--to against a daemon that would silently drop them ---------------------

BOUNDED = [
    ["lines", "--to", "23:59"],
    ["lines", "--from", "00:01"],
    ["log", "export", "--to", "23:59"],
    ["plot", "export", "--names", "vbat", "--to", "23:59"],
    ["can", "dump", "--to", "23:59"],
]


@pytest.mark.parametrize("argv", BOUNDED)
def test_clock_bounds_are_refused_against_a_daemon_that_ignores_them(monkeypatch, capsys,
                                                                     argv) -> None:
    """A pre-0.4.0 daemon drops the undeclared parameter and answers the whole capture."""
    seen = recorder(monkeypatch, status={**STATUS, "version": "0.3.0"})
    rc = cli.main([*argv, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "0.3.0" in err and "0.4.0 or newer" in err, err
    assert "--from/--to" in err, err
    assert paths(seen) == ["/status"], "refused before the query it would have answered"


@pytest.mark.parametrize("argv", BOUNDED)
def test_clock_bounds_are_allowed_against_a_current_daemon(monkeypatch, capsys, argv) -> None:
    seen = recorder(monkeypatch, lines={"lines": [], "truncated": False},
                    lines_export="", plot_export="ts,name,value\n",
                    can_frames={"frames": []})
    rc = cli.main([*argv, *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert paths(seen).count("/status") == 1, "one extra request, not one per page"


def test_an_unparsable_daemon_version_is_not_refused(monkeypatch, capsys) -> None:
    """A dev build must not be locked out on a string nobody can order."""
    recorder(monkeypatch, status={**STATUS, "version": "0.5.0.dev3+g1234"},
             lines={"lines": [], "truncated": False})
    rc = cli.main(["lines", "--to", "23:59", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err


def test_a_query_without_clock_bounds_makes_no_extra_request(monkeypatch, capsys) -> None:
    """The version check is on that path only: every other command keeps its one call."""
    seen = recorder(monkeypatch, lines={"lines": [], "truncated": False})
    rc = cli.main(["lines", "--last-ms", "5000", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert paths(seen) == ["/lines"], paths(seen)


def test_inverted_bounds_are_still_refused_before_the_version_check(monkeypatch,
                                                                    capsys) -> None:
    seen = recorder(monkeypatch, status={**STATUS, "version": "0.3.0"})
    rc = cli.main(["lines", "--from", "19:00", "--to", "18:00", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1 and "is after --to" in err, err
    assert seen == [], "a usage error costs no request"


# -- C4 / C9: the session export output path -------------------------------------------


@pytest.mark.parametrize("name", ["run.db", "run.DB", "run.Db"])
def test_bundle_refuses_a_db_name_whatever_its_case(capsys, tmp_path, name) -> None:
    """On Windows run.DB and run.db are one file, so a case-sensitive guard is none."""
    rc = cli.main(["session", "export", "1", "--bundle", "-o", str(tmp_path / name),
                   *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "not a .db" in err, err
    assert not (tmp_path / name).exists()


@pytest.mark.parametrize("argv", [
    ["session", "export", "1", "--bundle"],
    ["session", "export", "1"],
    ["log", "export"],
    ["plot", "export", "--names", "vbat"],
    ["can", "dump"],
])
def test_every_export_refuses_the_stdout_token(capsys, tmp_path, monkeypatch, argv) -> None:
    """`-o -` opened a file called "-" (and "-.zip" from the bundle branch), on all of them.

    One message, so the answer does not depend on which export was asked for, and it says
    where stdout actually is.
    """
    monkeypatch.chdir(tmp_path)
    rc = cli.main([*argv, "-o", "-", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "error: -o - is not stdout; omit -o for stdout, or give a file path" in err, err
    assert list(tmp_path.iterdir()) == [], "and no file named - is left behind"


# -- C6 / C10: line endings, on the file and on stdout ----------------------------------


def open_spy(monkeypatch) -> list[tuple[str, object]]:
    """Record (path, newline=) for every open, so the kwarg is asserted on Linux too."""
    calls: list[tuple[str, object]] = []
    real = builtins.open

    def spy(file, mode="r", *args, **kw):
        calls.append((str(file), kw.get("newline", "MISSING")))
        return real(file, mode, *args, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    return calls


def test_the_streamed_export_file_is_opened_untranslated(monkeypatch, tmp_path,
                                                         capsys) -> None:
    """newline="" or the body stops reaching the file byte for byte on Windows."""
    recorder(monkeypatch, lines_export="a,b\nc,d\n")
    out = tmp_path / "run.csv"
    calls = open_spy(monkeypatch)
    rc = cli.main(["log", "export", "--csv", "-o", str(out), *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert (str(out), "") in calls, calls


def test_the_paged_export_file_is_opened_lf_only(monkeypatch, tmp_path, capsys) -> None:
    """The sibling path writes its own rows, so it pins LF rather than no translation."""
    recorder(monkeypatch, lines={"lines": [{"id": 1, "ts": 0.0, "chan": "debug",
                                            "raw": "hi"}], "truncated": False})
    out = tmp_path / "run.txt"
    calls = open_spy(monkeypatch)
    rc = cli.main(["log", "export", "--limit", "5", "-o", str(out), *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert (str(out), "\n") in calls, calls


@pytest.mark.parametrize("argv", [
    ["log", "export", "--csv"],
    ["can", "dump", "--csv"],
    ["plot", "export", "--names", "vbat"],
])
def test_a_streamed_export_to_stdout_is_not_newline_translated(monkeypatch, argv) -> None:
    """`> run.csv` and `-o run.csv` must write the same bytes (CRLF on Windows, else LF).

    Driven through a TextIOWrapper that translates to CRLF: on Linux the platform default
    hides the defect, and this makes the arm that never pinned `newline=` fail anywhere.
    """
    recorder(monkeypatch, lines_export="a,b\nc,d\n", can_frames="ts,id\n1,100\n",
             plot_export="ts,name,value\n1,vbat,2\n")
    buf = io.BytesIO()
    wrapper = io.TextIOWrapper(buf, encoding="utf-8", newline="\r\n")
    monkeypatch.setattr(sys, "stdout", wrapper)
    rc = cli.main([*argv, *UNREACHABLE])
    wrapper.flush()
    assert rc == 0
    assert b"\r\n" not in buf.getvalue(), buf.getvalue()
    assert b"\n" in buf.getvalue()


# -- C7: a refusal from /lines/export reaches the user ---------------------------------


def test_a_400_from_the_export_endpoint_is_an_error_with_exit_1(monkeypatch,
                                                                capsys) -> None:
    """An unknown session is refused daemon-side; the CLI must not swallow the body."""
    recorder(monkeypatch, lines_export=(400, {"error": "no such session: nope"}))
    rc = cli.main(["log", "export", "--csv", "--session", "nope", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "error: no such session: nope" in err, err


# -- improvement 1: httpx's own CLI is not dragged in ----------------------------------


CHILD = """
import sys
from mcuscope import cli      # the console script's first import, as it is in production
import httpx

body = {"version": "0.4.0", "uptime_s": 1.0, "db_path": "x", "ports": []}
transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
cli.Client.open = lambda self: httpx.Client(transport=transport)
rc = cli.main(["status", "--url", "http://127.0.0.1:1"])
loaded = [m for m in sys.modules if sys.modules[m] is not None]
print("rc", rc, "httpx" in sys.modules, "rich" in sys.modules, "httpx._main" in loaded)
"""


def test_a_command_does_not_import_rich_through_httpx() -> None:
    """In a child process: the pytest process has already imported httpx (tests.support),
    where the sentinel `mcuscope.cli_client` sets at import time cannot get in first.
    """
    r = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[-4:] == ["0", "True", "False", "False"], r.stdout
    assert "mcuscoped 0.4.0" in r.stdout, "the run went through httpx for real"


def test_the_sentinel_leaves_httpx_working(monkeypatch, capsys) -> None:
    """A sentinel placed on the wrong name is silent; only a real request catches that."""
    import httpx as http_mod

    seen = recorder(monkeypatch, lines={"lines": [], "truncated": False})
    assert http_mod.Client is not None
    rc = cli.main(["lines", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert paths(seen) == ["/lines"]


# -- improvement 3: what the wait timed out on -----------------------------------------


def test_the_wait_timeout_line_names_the_pattern_the_port_and_the_wait(monkeypatch,
                                                                       capsys) -> None:
    recorder(monkeypatch, wait={"status": "timeout", "waited_ms": 1200.4})
    rc = cli.main(["-p", "sim", "wait", "--match", "^NEVER", "--timeout", "1200",
                   *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 2, "a timeout stays exit 2; die() would have made it 1"
    assert "^NEVER" in err and "sim" in err and "1200" in err, err


def test_the_wait_timeout_json_is_the_body_and_nothing_else(monkeypatch, capsys) -> None:
    body = {"status": "timeout", "waited_ms": 1200.4}
    recorder(monkeypatch, wait=body)
    rc = cli.main(["--json", "wait", "--match", "^NEVER", *UNREACHABLE])
    out = capsys.readouterr()
    assert rc == 2
    assert json.loads(out.out) == body
    assert "timeout: no line matched" not in out.err, "the prose form is text mode only"


# -- improvement 6: trimmed lines in `mcu status` --------------------------------------


def test_status_reports_trimmed_lines_when_there_are_some(monkeypatch, capsys) -> None:
    recorder(monkeypatch, status={**STATUS, "lines_trimmed": 796693})
    assert cli.main(["status", *UNREACHABLE]) == 0
    assert "trimmed=796693" in capsys.readouterr().out


@pytest.mark.parametrize("body", [{"lines_trimmed": 0}, {}])
def test_status_stays_quiet_about_trimming_when_there_is_none(monkeypatch, capsys,
                                                              body) -> None:
    """Quiet when zero, and `.get` for a daemon too old to send the counter."""
    recorder(monkeypatch, status={**STATUS, **body})
    assert cli.main(["status", *UNREACHABLE]) == 0
    assert "trimmed" not in capsys.readouterr().out


# -- improvement 8: plot channel values ------------------------------------------------


CHANNELS = {"channels": [
    {"name": "vbat", "sid": 0, "type": "f4", "unit": "V", "last_value": 0.14090123772621155,
     "last_ts": None, "count": 3},
    {"name": "lane", "sid": 0, "type": "u1", "unit": None, "last_value": 1.0,
     "last_ts": None, "count": 3},
]}


def test_plot_channels_renders_the_last_value_readably(monkeypatch, capsys) -> None:
    recorder(monkeypatch, plot_channels=CHANNELS)
    assert cli.main(["plot", "channels", *UNREACHABLE]) == 0
    out = capsys.readouterr().out
    assert "last=0.140901 V" in out, out
    assert "last=1 " in out, out
    assert "0.14090123772621155" not in out


def test_plot_channels_json_keeps_the_full_precision(monkeypatch, capsys) -> None:
    """The fix belongs on the text side of the --json branch, not before it."""
    recorder(monkeypatch, plot_channels=CHANNELS)
    assert cli.main(["--json", "plot", "channels", *UNREACHABLE]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["channels"][0]["last_value"] == 0.14090123772621155


# -- improvement 10: attach by serial number -------------------------------------------


ATTACHED = {"port": {"alias": "b", "connected": False}}


def test_attach_by_serial_posts_the_serial_number_alone(monkeypatch, capsys) -> None:
    seen = recorder(monkeypatch, ports=ATTACHED)
    rc = cli.main(["attach", "--serial", "0672FF3", "--alias", "b", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    body = json.loads(seen[0].content)
    assert body["serial_number"] == "0672FF3"
    assert "device" not in body, body


def test_attach_by_device_still_posts_the_device_alone(monkeypatch, capsys) -> None:
    seen = recorder(monkeypatch, ports=ATTACHED)
    rc = cli.main(["attach", "/dev/ttyACM0", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    body = json.loads(seen[0].content)
    assert body["device"] == "/dev/ttyACM0"
    assert "serial_number" not in body, body


def test_attach_refuses_a_device_and_a_serial_together(capsys) -> None:
    rc = cli.main(["attach", "/dev/ttyACM0", "--serial", "X", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "--serial" in err and "both" in err, err


def test_attach_refuses_neither(capsys) -> None:
    rc = cli.main(["attach", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "--serial" in err and "mcu devices" in err, err


# -- improvement 11 and the -f bound: `can dump` ---------------------------------------


def test_can_dump_forwards_the_session(monkeypatch, capsys) -> None:
    seen = recorder(monkeypatch, can_frames={"frames": []})
    rc = cli.main(["can", "dump", "--session", "run-3", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert dict(seen[0].url.params)["session"] == "run-3", seen[0].url


def test_can_dump_refuses_an_upper_bound_with_follow(capsys) -> None:
    """The follow polls live frames and cannot honour --to, so it ran past it for ever."""
    rc = cli.main(["can", "dump", "--to", "23:59", "-f", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "--to" in err and "-f" in err, err


def test_can_dump_still_follows_with_a_lower_bound(monkeypatch, capsys) -> None:
    """--from bounds the backfill only, and everything live is after it by definition."""
    recorder(monkeypatch, can_frames={"frames": []})
    monkeypatch.setattr(cli, "_dump_follow", lambda *a, **kw: None)
    rc = cli.main(["can", "dump", "--from", "00:01", "-f", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err


# -- improvement 13: `mcu devices` columns ---------------------------------------------


def test_devices_labels_its_columns(monkeypatch, capsys) -> None:
    recorder(monkeypatch, devices={"devices": [
        {"device": "/dev/ttyACM0", "description": "STLINK-V3", "vid_pid": "0483:374f",
         "serial_number": "0672FF3"},
    ]})
    assert cli.main(["devices", *UNREACHABLE]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].split() == ["device", "description", "vid:pid", "serial"], out
    assert "0672FF3" in out


def test_devices_with_none_attached_prints_only_its_message(monkeypatch, capsys) -> None:
    """A header over "no serial devices found" reads as a table that failed to load."""
    recorder(monkeypatch, devices={"devices": []})
    assert cli.main(["devices", *UNREACHABLE]) == 0
    assert capsys.readouterr().out == "no serial devices found\n"


# -- improvement 7 (CLI half): a daemon shutting down under a long poll -----------------


@pytest.mark.parametrize("argv", [
    ["wait", "--match", "x"],
    ["assert", "--expect", "x", "--timeout", "1000"],
])
def test_a_503_from_the_daemon_is_unreachable_not_an_error(monkeypatch, capsys,
                                                           argv) -> None:
    """SPEC 4 codes "the daemon is not there" 3, and a shutdown mid-wait is that."""
    msg = "daemon is shutting down; the wait was cut short"
    canned(monkeypatch, lambda request: httpx.Response(503, json={"error": msg}))
    rc = cli.main([*argv, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 3, err
    assert msg in err, err


def test_an_ordinary_400_is_still_exit_1(monkeypatch, capsys) -> None:
    """Only 503 moves; a bad request is still the user's error."""
    canned(monkeypatch, lambda request: httpx.Response(400, json={"error": "bad regex"}))
    rc = cli.main(["wait", "--match", "(", *UNREACHABLE])
    assert rc == 1, capsys.readouterr().err


# -- fix-diff F2: the rest of the 0.4.0 export surface is version-gated like --from/--to --


def test_plot_export_decode_is_refused_against_a_daemon_that_drops_it(monkeypatch,
                                                                      capsys) -> None:
    """A pre-0.4.0 `/plot/export` declares no `decode`, so FastAPI drops it and the export
    comes back raw at exit 0 (class 53)."""
    seen = recorder(monkeypatch, status={**STATUS, "version": "0.3.0"})
    rc = cli.main(["plot", "export", "--names", "ramp", "--decode", "-o", "x.csv",
                   *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1 and "--decode/--changes/--deadband" in err and "0.3.0" in err, err
    assert paths(seen) == ["/status"], paths(seen)


def test_can_dump_csv_is_refused_against_a_daemon_that_drops_format(monkeypatch,
                                                                    capsys) -> None:
    """Without `format` the old daemon answers JSON, which landed inside the .csv file."""
    seen = recorder(monkeypatch, status={**STATUS, "version": "0.3.0"})
    rc = cli.main(["can", "dump", "--csv", "-o", "x.csv", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1 and "--csv" in err and "0.3.0" in err, err
    assert paths(seen) == ["/status"], paths(seen)


# -- fix-diff F5: an alias derived from a serial number stays inside the grammar --------


def test_attach_derives_an_alias_inside_the_grammar_from_any_serial(monkeypatch,
                                                                    capsys) -> None:
    """`AB:CD` used to be refused naming `alias`, an option the user never typed."""
    seen = recorder(monkeypatch, ports=ATTACHED)
    rc = cli.main(["attach", "--serial", "AB:CD", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert json.loads(seen[0].content)["alias"] == "AB-CD"
