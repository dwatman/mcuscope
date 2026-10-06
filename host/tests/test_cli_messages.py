"""Messages that say what happened and what to do next (SPEC 4; AGENTUX-7, 9, 10)."""

from __future__ import annotations

import json
import time

import httpx
import pytest
import typer

from mcuscope import cli
from mcuscope.cli_client import Client, Settings
from tests.support import versioned
from tests.test_cli import run_mcu_canned


def _json(body):
    return lambda request: httpx.Response(200, json=body)


# -- cmd ------------------------------------------------------------------------------------


def test_a_cmd_timeout_names_the_command_the_port_and_the_next_step(monkeypatch,
                                                                     capsys) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ports":
            return httpx.Response(200, json={"ports": [{"alias": "b"}], "stored": []})
        return httpx.Response(200, json={"status": "timeout"})
    rc, _, err = run_mcu_canned(monkeypatch, capsys, handler,
                                "-p", "b", "cmd", "ping", "--timeout", "250")
    assert rc == 2
    assert err == ("timeout: no response to 'ping' on port b within 250 ms; raise --timeout, "
                   "or check the port with 'mcu status'\n")


# -- sessions and purge -----------------------------------------------------------------------


def test_session_list_limit_0_does_not_claim_there_are_none(monkeypatch, capsys) -> None:
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, _json({"sessions": []}),
                                "session", "list", "--limit", "0")
    assert rc == 0 and out == "--limit 0 lists no sessions\n"
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, _json({"sessions": []}),
                                "session", "list")
    assert out == "no sessions recorded\n"   # control: an empty list still says so


@pytest.mark.parametrize(("argv", "said"), [
    (["session", "delete", "run"],
     "deleted session run; 0 lines deleted (its lines are kept; --data deletes them)\n"),
    (["session", "delete", "run", "--data", "-y"], "deleted session run; 7 lines deleted\n"),
])
def test_session_delete_says_how_many_lines_went(monkeypatch, capsys, argv, said) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            data = request.url.params["data"] == "true"
            return httpx.Response(200, json={"lines_deleted": 7 if data else 0})
        return httpx.Response(200, json={"sessions": [{"id": 3, "name": "run", "lines": 7}]})
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, handler, *argv)
    assert rc == 0 and out == said


def test_a_real_purge_that_finds_nothing_is_not_a_dry_run(monkeypatch, capsys) -> None:
    preview = {"deleted": 0, "id_from": None, "id_to": None, "dry_run": True}
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, _json(preview),
                                "--json", "purge", "--id-from", "1", "--id-to", "1", "-y")
    assert rc == 0 and json.loads(out) == {**preview, "dry_run": False}
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, _json(preview),
                                "--json", "purge", "--id-from", "1", "--id-to", "1",
                                "--dry-run")
    assert json.loads(out)["dry_run"] is True   # control: --dry-run still says so


# -- --names a sample never carried -------------------------------------------------------------


PD = {"id": 1, "ts": 1.0, "port": "b", "dir": "rx", "chan": "event", "seq": None,
      "raw": "!pd 0 vbat:f4"}
PS = {"id": 2, "ts": 1.1, "port": "b", "dir": "rx", "chan": "event", "seq": None,
      "raw": "!ps 0 10 00002041"}


def _rows(rows):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ports":
            return httpx.Response(200, json={"ports": [{"alias": "b"}], "stored": ["b"]})
        return httpx.Response(200, json={"lines": rows[::-1], "truncated": False})
    return handler


@pytest.mark.parametrize("argv", [["lines"], ["log", "export", "--decode"], ["tail"]])
def test_a_name_no_sample_carries_is_warned_about(monkeypatch, capsys, argv) -> None:
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _rows([PD, PS]),
                                  *argv, "--names", "vbat,temp")
    assert rc == 0, err
    assert "vbat=" in out
    assert err.count("warning: --names temp: no sample in the window carries that field") == 1


@pytest.mark.parametrize("argv", [["lines"], ["log", "export", "--decode"], ["tail"]])
def test_names_on_an_empty_window_are_warned_about(monkeypatch, capsys, argv) -> None:
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _rows([]),
                                  *argv, "--names", "vbat,,temp,vbat")
    assert rc == 0, err
    assert err.count("warning: --names vbat,temp: no sample in the window carries "
                     "those fields") == 1, err


def test_names_every_sample_carries_get_no_warning(monkeypatch, capsys) -> None:
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _rows([PD, PS]),
                                  "lines", "--names", "vbat")
    assert rc == 0 and "vbat=" in out and "warning" not in err


# -- plot ----------------------------------------------------------------------------------------


def test_plot_export_json_rows_are_objects_keyed_by_the_header(monkeypatch, capsys) -> None:
    csv = ('ts,tick_ms,sid,name,value\n1.5,10,,8,8.7\n2.0,20,1,state,"IDLE, armed"\n'
           '3.0,30,,8,-5\n')
    canned_text = lambda request: httpx.Response(200, text=csv)  # noqa: E731
    rc, out, err = run_mcu_canned(monkeypatch, capsys, canned_text,
                                  "--json", "plot", "export", "--names", "8,state")
    assert rc == 0, err
    rows = json.loads(out)["rows"]
    assert json.loads(out) == {"names": "8,state", "format": "long", "rows": [
        {"ts": 1.5, "tick_ms": 10, "sid": None, "name": "8", "value": 8.7},
        {"ts": 2.0, "tick_ms": 20, "sid": 1, "name": "state", "value": "IDLE, armed"},
        {"ts": 3.0, "tick_ms": 30, "sid": None, "name": "8", "value": -5},
    ]}
    assert type(rows[0]["tick_ms"]) is int and type(rows[0]["ts"]) is float   # 10 == 10.0
    assert type(rows[2]["value"]) is int                                       # -5 == -5.0


def test_a_plot_export_row_that_does_not_fit_its_header_is_a_daemon_error(monkeypatch,
                                                                         capsys) -> None:
    rc, out, _ = run_mcu_canned(monkeypatch, capsys,
                                lambda request: httpx.Response(200, text="ts,value\n1,2,3\n"),
                                "--json", "plot", "export", "--names", "x")
    assert rc == 1 and json.loads(out)["kind"] == "daemon_error"


def test_a_truncated_channel_list_is_noted(monkeypatch, capsys) -> None:
    ch = {"name": "a", "sid": None, "unit": None, "type": None, "last_value": 1,
          "last_ts": time.time(), "count": 1}
    rc, _, err = run_mcu_canned(monkeypatch, capsys,
                                _json({"channels": [ch], "truncated": True}),
                                "plot", "channels")
    assert rc == 0 and "note: 1 channels listed, the most recently sampled; more exist" in err
    rc, _, err = run_mcu_canned(monkeypatch, capsys,
                                _json({"channels": [ch], "truncated": False}),
                                "plot", "channels")
    assert "note" not in err


# -- can dump -f against a daemon that went away -------------------------------------------------


def test_can_dump_follow_names_an_unreachable_daemon_on_the_first_failed_poll(
    monkeypatch, capsys
) -> None:
    polls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"capture": "A"})
        polls.append(1)
        if len(polls) <= 3:
            raise httpx.ConnectError("connection refused")
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)
    client = Client(s, transport=httpx.MockTransport(versioned(handler)))
    monkeypatch.setattr(client, "get", lambda path, **kw: {"frames": []})
    with pytest.raises(typer.Exit):
        cli._dump_follow(client, s, None)
    err = capsys.readouterr().err.splitlines()
    assert err[0] == ("warning: daemon unreachable at http://127.0.0.1:1: connection refused; "
                      "retrying for 30s")
    assert err[1:] == ["warning: 3 consecutive polls failed"], err


def test_a_malformed_poll_keeps_the_bad_update_wording(monkeypatch, capsys) -> None:
    """Control: only a connect failure is called unreachable."""
    polls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={"capture": "A"})
        polls.append(1)
        if len(polls) == 1:
            return httpx.Response(200, json={"frames": "nope"})
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)
    client = Client(s, transport=httpx.MockTransport(versioned(handler)))
    monkeypatch.setattr(client, "get", lambda path, **kw: {"frames": []})
    with pytest.raises(typer.Exit):
        cli._dump_follow(client, s, None)
    err = capsys.readouterr().err
    assert "skipping bad update" in err and "unreachable" not in err


@pytest.mark.parametrize(("final", "warned"), [(True, True), (False, False)])
def test_a_follows_snapshot_does_not_judge_names_yet(monkeypatch, capsys, final,
                                                     warned) -> None:
    """`tail -f`'s rows are still to come when its snapshot ends."""
    from mcuscope.cli_output import LineDecoder
    from tests.support import canned

    canned(monkeypatch, _rows([PD, PS]))
    s = Settings(url="http://127.0.0.1:1", json_out=False, port="b")
    dec = LineDecoder(names=["temp"])
    cli._tail_snapshot(s, None, None, 5, dec, show_port=False, final=final)
    assert ("--names temp" in capsys.readouterr().err) is warned
