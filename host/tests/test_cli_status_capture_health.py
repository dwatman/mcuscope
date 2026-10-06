"""`mcu status` and `mcu ports` show the capture's health fields and each port's loss
counters, only when they say something (SPEC 4)."""

from __future__ import annotations

import httpx
import pytest

from mcuscope import __version__, cli
from tests.test_cli import run_mcu_canned

PORT = {"alias": "b", "device": "sim://b", "baud": 115200, "connected": True,
        "lines_rx": 9, "lines_tx": 1}


def _status(monkeypatch, capsys, argv=("status",), **fields):
    body = {"version": __version__, "uptime_s": 3.0, "db_path": "/x.db",
            "ports": [{**PORT, **fields.pop("port", {})}], **fields}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)
    return run_mcu_canned(monkeypatch, capsys, handler, *argv)


def test_a_clean_capture_shows_none_of_them(monkeypatch, capsys) -> None:
    rc, out, _ = _status(monkeypatch, capsys, lines_expired=0, lines_trimmed=0,
                         capture_error=None, db_locked_since=None, writer_alive=True,
                         port={"rx_dropped": 0, "rx_replaced": 0, "plot_name_refused": 0})
    assert rc == 0
    for word in ("expired=", "trimmed=", "CAPTURE", "dropped=", "rx_replaced=",
                 "plot_name_refused="):
        assert word not in out, out


def test_lines_expired_for_age_are_shown(monkeypatch, capsys) -> None:
    rc, out, _ = _status(monkeypatch, capsys, lines_expired=12, lines_trimmed=3)
    first = out.splitlines()[0]
    assert "trimmed=3" in first and "expired=12" in first, out


def test_a_capture_error_says_capture_stopped_and_why(monkeypatch, capsys) -> None:
    rc, out, _ = _status(monkeypatch, capsys, writer_alive=False,
                         capture_error="capture file /x.db was replaced")
    assert ("  CAPTURE STOPPED: capture file /x.db was replaced; restart the daemon "
            "(mcu daemon restart)") in out
    assert "store writer is not running" not in out   # one stop line, the one with a cause


def test_a_dead_writer_without_a_cause_keeps_its_own_line(monkeypatch, capsys) -> None:
    rc, out, _ = _status(monkeypatch, capsys, writer_alive=False, capture_error=None)
    assert "CAPTURE STOPPED: the store writer is not running" in out


def test_a_held_write_lock_is_shown_with_its_start(monkeypatch, capsys) -> None:
    rc, out, _ = _status(monkeypatch, capsys, db_locked_since=1_700_000_000.0)
    assert "CAPTURE BLOCKED: another process has held a write lock on the capture since " \
        in out and out.rstrip().count("close it") == 1, out


@pytest.mark.parametrize("argv", [("status",), ("ports",)])
def test_port_loss_counters_are_shown_when_non_zero(monkeypatch, capsys, argv) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        port = {**PORT, "rx_dropped": 2, "rx_replaced": 5, "plot_name_refused": 7}
        return httpx.Response(200, json={"version": __version__, "uptime_s": 1.0,
                                         "db_path": "/x.db", "ports": [port], "stored": []})
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, handler, *argv)
    line = next(ln for ln in out.splitlines() if ln.lstrip().startswith("b "))
    assert line.endswith(" dropped=2 rx_replaced=5 plot_name_refused=7"), line


def test_a_move_aside_cause_says_then_restart(monkeypatch, capsys) -> None:
    cause = f"x; move /d-wal and /d-shm {cli.MOVE_ASIDE}"
    rc, out, _ = _status(monkeypatch, capsys, capture_error=cause)
    assert f"{cause}; then restart the daemon (mcu daemon restart)" in out
