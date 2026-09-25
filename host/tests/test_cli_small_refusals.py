"""Argument handling and messages an agent trips on (SPEC 4 table)."""

from __future__ import annotations

import json

import httpx
import pytest

from mcuscope import cli
from tests.test_cli import run_mcu_canned


def _recorder(answer: dict | None = None):
    calls: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, request.url.path, body))
        return httpx.Response(200, json=answer if answer is not None else {"ok": True})
    return handler, calls


# -- a text that starts with '-' ---------------------------------------------------------


def test_a_marker_starting_with_dash_p_is_the_text_not_a_port(monkeypatch, capsys) -> None:
    handler, calls = _recorder({"line_id": 5})
    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler, "mark", "-pwm duty 50")
    assert rc == 0, err
    assert calls == [("POST", "/marker", {"port": None, "text": "-pwm duty 50"})]


def test_the_attached_port_form_is_still_hoisted() -> None:
    """Positive control: an alias-shaped `-pNAME` is the global option."""
    assert cli._split_global_opts(["lines", "-psim"]) == (["-psim"], ["lines"])
    assert cli._split_global_opts(["mark", "-pwm duty 50"]) == ([], ["mark", "-pwm duty 50"])


def test_send_takes_a_dash_leading_line_and_refuses_a_lone_dash(monkeypatch, capsys) -> None:
    handler, calls = _recorder()
    rc, *_ = run_mcu_canned(monkeypatch, capsys, handler, "send", "-x")
    assert rc == 0 and calls[-1][2]["line"] == "-x"
    calls.clear()
    rc, _, err = run_mcu_canned(monkeypatch, capsys, handler, "send", "-")
    assert rc == 1 and "does not read stdin" in err
    assert calls == []


# -- refused before any request ----------------------------------------------------------


def test_sysrq_refuses_a_non_ascii_character_before_the_break(monkeypatch, capsys) -> None:
    handler, calls = _recorder()
    rc, _, err = run_mcu_canned(monkeypatch, capsys, handler, "sysrq", "é")
    assert rc == 1 and "printable ASCII" in err
    assert calls == [], "the break went out before the refusal"
    rc, *_ = run_mcu_canned(monkeypatch, capsys, handler, "sysrq", "b")
    assert rc == 0 and [c[1] for c in calls] == ["/break", "/send"]   # positive control


def test_purge_refuses_an_inverted_id_range(monkeypatch, capsys) -> None:
    handler, calls = _recorder()
    rc, _, err = run_mcu_canned(monkeypatch, capsys, handler,
                                "purge", "--id-from", "500", "--id-to", "100", "--dry-run")
    assert rc == 1 and "--id-from 500 is after --id-to 100" in err
    assert calls == []


# -- output wording -----------------------------------------------------------------------


def test_status_says_when_no_port_is_attached(monkeypatch, capsys) -> None:
    body = {"version": "9", "uptime_s": 1, "db_path": "x", "ports": []}
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, lambda r: httpx.Response(200, json=body),
                                "status")
    assert rc == 0 and "no ports attached" in out


def test_a_command_with_no_data_prints_ok(monkeypatch, capsys) -> None:
    rc, out, _ = run_mcu_canned(
        monkeypatch, capsys, lambda r: httpx.Response(200, json={"status": "ok", "data": ""}),
        "cmd", "gpio set led 1")
    assert rc == 0 and out == "ok\n"
    rc, out, _ = run_mcu_canned(
        monkeypatch, capsys, lambda r: httpx.Response(200, json={"status": "ok", "data": "1"}),
        "cmd", "gpio get led")
    assert out == "1\n"


def test_an_unknown_plot_channel_points_at_the_command(monkeypatch, capsys) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={
            "error": "no such plot channel: vbat; see /plot/channels"})

    rc, _, err = run_mcu_canned(monkeypatch, capsys, handler, "plot", "export", "--names", "vbat")
    assert rc == 1
    assert "no such plot channel: vbat; see 'mcu plot channels'" in err


def test_attach_over_an_existing_alias_says_it_moved(monkeypatch, capsys) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"ports": [
                {"alias": "board", "device": "/dev/ttyACM0"}]})
        return httpx.Response(200, json={"port": {"alias": "board", "connected": False}})

    rc, _, err = run_mcu_canned(monkeypatch, capsys, handler,
                                "attach", "/dev/ttyACM1", "--alias", "board")
    assert rc == 0
    assert "note: board was attached to /dev/ttyACM0; it now names /dev/ttyACM1" in err
    rc, _, err = run_mcu_canned(monkeypatch, capsys, handler,
                                "attach", "/dev/ttyACM1", "--alias", "other")
    assert rc == 0 and "note:" not in err      # positive control: a new alias is quiet
    rc, _, err = run_mcu_canned(monkeypatch, capsys, handler,
                                "attach", "/dev/ttyACM0", "--alias", "board")
    assert rc == 0 and "note:" not in err      # the same target again moved nothing


def test_stop_with_nothing_running_does_not_imply_a_daemon(monkeypatch, capsys,
                                                           tmp_path) -> None:
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", str(tmp_path))
    rc = cli.main(["--url", "http://127.0.0.1:1", "daemon", "stop"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no daemon is running at http://127.0.0.1:1; nothing to stop" in err


def test_an_ambiguous_port_names_the_aliases_once_and_the_option(monkeypatch, capsys) -> None:
    """The daemon lists the aliases itself; the CLI adds only the option."""
    for daemon_msg, expect in (
        ("port is ambiguous; specify one of: a, b",
         "error: port is ambiguous; specify one of: a, b (with -p)"),
    ):
        def handler(request: httpx.Request, msg=daemon_msg) -> httpx.Response:
            if request.url.path == "/ports":
                return httpx.Response(200, json={"ports": [{"alias": "a"}, {"alias": "b"}]})
            return httpx.Response(400, json={"error": msg})

        rc, _, err = run_mcu_canned(monkeypatch, capsys, handler, "send", "x")
        assert rc == 1 and err.strip() == expect


# -- F6 / measurement F1: --before-days is an age, never a wipe -------------------------


@pytest.mark.parametrize("value", ["-1", "0", "-0.5"])
def test_purge_before_days_refuses_a_non_positive_age(monkeypatch, capsys, value: str) -> None:
    """A negative age puts before_ts in the future, which selects the whole capture."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"deleted": 0, "id_from": None, "id_to": None})

    rc, out, err = run_mcu_canned(
        monkeypatch, capsys, handler, "purge", "--before-days", value, "--dry-run"
    )
    assert rc == 1
    assert "--before-days must be greater than 0" in err
    assert seen == [], "the purge was sent to the daemon anyway"


# -- measurement F3: no None-None id range in the purge preview -------------------------


def test_purge_dry_run_omits_the_id_range_when_nothing_matched(monkeypatch, capsys) -> None:
    rc, out, _ = run_mcu_canned(
        monkeypatch, capsys,
        lambda request: httpx.Response(200, json={"deleted": 0, "id_from": None, "id_to": None}),
        "purge", "--before-days", "999", "--dry-run",
    )
    assert rc == 0
    assert out.strip() == "would delete 0 lines"


def test_purge_dry_run_keeps_the_id_range_when_there_is_one(monkeypatch, capsys) -> None:
    rc, out, _ = run_mcu_canned(
        monkeypatch, capsys,
        lambda request: httpx.Response(200, json={"deleted": 3, "id_from": 1, "id_to": 9}),
        "purge", "--before-days", "1", "--dry-run",
    )
    assert rc == 0 and out.strip() == "would delete 3 lines (ids 1-9)"
