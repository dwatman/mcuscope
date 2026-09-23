"""Transport timeouts and request budgets (SPEC 4 exit codes, the daemon's match budget)."""

from __future__ import annotations

import socket
import threading

import httpx
import pytest

from tests.test_cli import run_mcu_canned


def _hang(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


@pytest.mark.parametrize("argv", [
    ["status"], ["cmd", "ping"], ["lines"], ["wait", "--match", "x"],
    ["assert", "--forbid", "x"], ["log", "export"],
])
def test_a_daemon_that_stops_answering_is_exit_1(monkeypatch, capsys, argv) -> None:
    rc, _, err = run_mcu_canned(monkeypatch, capsys, _hang, *argv)
    assert rc == 1, err
    assert "accepted the request but stopped answering" in err


def test_a_daemon_that_never_accepts_stays_exit_3(monkeypatch, capsys) -> None:
    """Positive control: a connect failure is still "unreachable"."""
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route", request=request)

    rc, _, err = run_mcu_canned(monkeypatch, capsys, refuse, "status")
    assert rc == 3 and "unreachable" in err


def test_a_board_timeout_the_daemon_reported_stays_exit_2(monkeypatch, capsys) -> None:
    rc, _, err = run_mcu_canned(
        monkeypatch, capsys, lambda r: httpx.Response(200, json={"status": "timeout"}),
        "cmd", "ping",
    )
    assert rc == 2 and err.strip() == "timeout"


def test_a_real_listener_that_never_answers_is_exit_1(monkeypatch, capsys) -> None:
    """End to end over a socket: accept, then silence (the wedged-daemon shape)."""
    from mcuscope import cli

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    held: list[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        srv.settimeout(0.1)
        while not stop.is_set():
            try:
                held.append(srv.accept()[0])
            except OSError:
                pass

    t = threading.Thread(target=accept, daemon=True)
    t.start()
    monkeypatch.setattr(cli, "READ_TIMEOUT_S", 0.3)
    try:
        rc = cli.main(["lines", "--url", f"http://127.0.0.1:{srv.getsockname()[1]}"])
    finally:
        stop.set()
        t.join()
        for c in held:
            c.close()
        srv.close()
    assert rc == 1
    assert "stopped answering" in capsys.readouterr().err


def _read_timeouts(monkeypatch, capsys, *argv) -> list[float]:
    seen: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={
            "lines": [], "truncated": False, "status": "pass", "expect": [], "forbid": [],
            "checked_lines": 1, "elapsed_ms": 1.0, "ports": []})

    rc, _, err = run_mcu_canned(monkeypatch, capsys, handler, *argv)
    assert rc == 0, err
    return seen


def test_a_match_read_waits_past_the_daemons_match_budget(monkeypatch, capsys) -> None:
    from mcuscope import cli

    with_match = _read_timeouts(monkeypatch, capsys, "lines", "--match", "NEVER")
    plain = _read_timeouts(monkeypatch, capsys, "lines")
    assert min(with_match) > cli.MATCH_BUDGET_S
    assert max(plain) == cli.READ_TIMEOUT_S    # the budget is added only where it applies


def test_a_retrospective_assert_waits_past_each_patterns_budget(monkeypatch, capsys) -> None:
    from mcuscope import cli

    seen = _read_timeouts(monkeypatch, capsys,
                          "assert", "--expect", "a", "--forbid", "b", "--forbid", "c")
    assert seen[-1] > 3 * cli.MATCH_BUDGET_S
    live = _read_timeouts(monkeypatch, capsys, "assert", "--forbid", "b", "--timeout", "2000")
    assert live[-1] == pytest.approx(2.0 + cli.READ_TIMEOUT_S)
