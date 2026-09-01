"""Port health a bench could not see: writes failing on a "connected" port, and which
board is behind a debugger that moved (SPEC 3.2 write_failures / target)."""

from __future__ import annotations

import contextlib
import json
import re
import time

import httpx
import pytest
import serial

from mcuscope.serial_link import PortError, SerialPort
from tests.support import Stack
from tests.test_cli import run_mcu, run_mcu_canned


def test_write_failures_are_counted_named_and_reset() -> None:
    port = SerialPort(None, None, "board")

    class _Bad:
        def write(self, data: bytes) -> None:
            raise serial.SerialTimeoutException("Write timeout")

    class _Good:
        def write(self, data: bytes) -> None:
            pass

    port._link = _Bad()
    with pytest.raises(PortError, match=r"write failed: Write timeout$"):
        port._write_bytes(b">1 ping\n")
    with pytest.raises(PortError, match=r"2 consecutive write failures since \d\d:\d\d:\d\d\)$"):
        port._write_bytes(b">2 ping\n")
    st = port.status()
    assert st["write_failures"] == 2
    assert st["last_write_error"] == "Write timeout"
    assert st["last_write_error_ts"] <= time.time()
    assert st["write_failing_since"] <= st["last_write_error_ts"]
    assert st["target"] is None

    port._link = _Good()
    port._write_bytes(b">3 ping\n")
    assert port.status()["write_failures"] == 0, "one write that lands ends the streak"
    assert port.status()["last_write_error"] == "Write timeout", "the last error stays on record"
    assert port.status()["write_failing_since"] is None

    port._link = _Bad()
    with pytest.raises(PortError, match=r"Write timeout$"):
        port._write_bytes(b">4 ping\n")
    assert port.status()["write_failures"] == 1, "a new streak counts from one"
    port._on_disconnect()
    assert port.status()["write_failures"] == 0


def test_connect_pings_and_reports_the_target() -> None:
    stack = Stack()
    try:
        deadline = time.monotonic() + 10
        target = None
        while time.monotonic() < deadline and target is None:
            body = httpx.get(f"{stack.base_url}/status", timeout=2).json()
            target = body["ports"][0]["target"]
            time.sleep(0.05)
        assert target == "sim", "the sim answers `monitor 1 sim`; the port must carry the name"
        r = run_mcu(stack, "status")
        assert "connected target=sim" in r.stdout, r.stdout
        rows = httpx.get(f"{stack.base_url}/lines", params={"chan": "sys", "limit": 20},
                         timeout=2).json()["lines"]
        assert any(row["raw"] == f"port {stack.alias} target: monitor 1 sim" for row in rows)
        # The probe is an ordinary command exchange, captured like any other.
        rows = httpx.get(f"{stack.base_url}/lines", params={"chan": "cmd", "limit": 5},
                         timeout=2).json()["lines"]
        assert any(row["raw"].endswith(" ping") for row in rows)
    finally:
        stack.close()


def _status_body(**port_fields) -> dict:
    port = {
        "alias": "stlink", "device": "/dev/ttyACM0", "baud": 115200, "connected": True,
        "held": False, "lines_rx": 10, "lines_tx": 3, "rx_dropped": 0, **port_fields,
    }
    return {"version": "0.0", "uptime_s": 1, "db_path": "x.db", "ports": [port]}


def test_status_shows_a_degraded_port_and_the_target(monkeypatch, capsys) -> None:
    since_ts = time.time() - 3600
    body = _status_body(write_failures=3, last_write_error="Write timeout",
                        last_write_error_ts=time.time(), write_failing_since=since_ts,
                        target="charger")
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, lambda r: httpx.Response(200, json=body),
                                "status")
    assert rc == 0
    assert "DEGRADED: 3 write failures since " in out and "target=charger" in out, out
    since = out.split("since ")[1].split()[0]
    assert since.startswith(time.strftime("%H:%M:%S", time.localtime(since_ts))), \
        "the streak start is shown, not the last failure"
    assert "connected" not in out.split("DEGRADED")[1].splitlines()[0]

    body = _status_body(write_failures=0, target=None)
    _, out, _ = run_mcu_canned(monkeypatch, capsys, lambda r: httpx.Response(200, json=body),
                               "status")
    assert "connected  rx=10" in out and "target=" not in out and "DEGRADED" not in out


def _busy_then_ok(busy_answers: int):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/cmd"
        calls["n"] += 1
        if calls["n"] <= busy_answers:
            return httpx.Response(200, json={
                "status": "err", "seq": calls["n"], "err_code": 6, "err_name": "busy",
                "err_detail": "", "latency_ms": 1.0, "line_id": 1,
            })
        return httpx.Response(200, json={
            "status": "ok", "seq": calls["n"], "data": "sent", "latency_ms": 1.0, "line_id": 2,
        })

    return handler, calls


def test_retry_ms_retries_busy_until_the_deadline(monkeypatch, capsys) -> None:
    handler, calls = _busy_then_ok(2)
    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler,
                                  "can", "tx", "100", "AA", "--retry-ms", "2000")
    assert (rc, out.strip(), calls["n"]) == (0, "sent", 3), err

    handler, calls = _busy_then_ok(2)
    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler, "can", "tx", "100", "AA")
    assert rc == 1 and calls["n"] == 1 and "ERR 6 busy" in err, "no --retry-ms: one attempt"

    handler, calls = _busy_then_ok(10_000)
    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler,
                                  "cmd", "can tx 100 AA", "--retry-ms", "100")
    assert rc == 1 and "ERR 6 busy" in err, "deadline passed: the last answer is reported"

    attempts = {"n": 0}

    def not_busy(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(200, json={
            "status": "err", "seq": 1, "err_code": 2, "err_name": "badarg",
            "err_detail": "", "latency_ms": 1.0, "line_id": 1,
        })

    rc, out, err = run_mcu_canned(monkeypatch, capsys, not_busy, "cmd", "x", "--retry-ms", "500")
    assert rc == 1 and "ERR 2 badarg" in err
    assert attempts["n"] == 1, "only busy is retried; a badarg can never succeed"


def test_plot_channels_shows_age_and_active_filters(monkeypatch, capsys) -> None:
    now = time.time()
    body = {"channels": [
        {"name": "vbat", "sid": "0", "type": "u2", "unit": "V", "last_value": 25.5,
         "last_tick": 1, "last_ts": now - 5, "count": 100},
        {"name": "present", "sid": "1", "type": "u1", "unit": None, "last_value": 0.0,
         "last_tick": 1, "last_ts": now - 4 * 86400, "count": 7},
    ]}
    handler = lambda r: httpx.Response(200, json=body)  # noqa: E731
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, handler, "plot", "channels")
    assert rc == 0 and re.search(r"vbat .*age=\d+s ", out) and "age=4d" in out, out

    _, out, _ = run_mcu_canned(monkeypatch, capsys, handler, "plot", "channels", "--active", "60")
    assert "vbat" in out and "present" not in out

    _, out, _ = run_mcu_canned(monkeypatch, capsys, handler, "plot", "channels", "--active", "1")
    assert out.strip() == "no active plot channels"

    _, out, _ = run_mcu_canned(monkeypatch, capsys, handler,
                               "--json", "plot", "channels", "--active", "60")
    assert [c["name"] for c in json.loads(out)["channels"]] == ["vbat"]


def test_decode_priming_is_bounded(monkeypatch, capsys) -> None:
    """The `!pd` lookup must carry an id floor and the event chan, like the daemon's own
    priming: an unbounded regex over a 1M-row capture with no plot streams runs into
    the store's match budget."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        if params.get("match") == "^!pd ":
            return httpx.Response(200, json={"lines": [], "truncated": False})
        rows = [{"id": 50_000, "ts": 1.0, "port": "b", "dir": "rx", "chan": "debug",
                 "seq": None, "raw": "hi"}]
        return httpx.Response(200, json={"lines": rows, "truncated": False})

    rc, out, _ = run_mcu_canned(monkeypatch, capsys, handler, "lines", "--decode")
    assert rc == 0 and out.strip().endswith("hi")
    prime = [p for p in seen if p.get("match") == "^!pd "]
    assert len(prime) == 1, seen
    assert prime[0]["chan"] == "event"
    assert prime[0]["id_to"] == "50000" and prime[0]["since_id"] == "30000", prime

    seen.clear()
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, handler, "tail", "-n", "5", "--decode")
    prime = [p for p in seen if p.get("match") == "^!pd "]
    assert prime and prime[0]["since_id"] == "30000", "tail resolves the newest id first"


def test_to_before_the_first_row_selects_nothing(monkeypatch, capsys) -> None:
    """The capture's first row is already past --to: no id_to can say "nothing", so the
    CLI must not fall back to id_to=1 and print that row."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        row = {"id": 1, "ts": 1.0, "port": "b", "dir": "-", "chan": "sys", "seq": None,
               "raw": "daemon start"}
        return httpx.Response(200, json={"lines": [row], "truncated": False})

    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler, "lines", "--to", "00:00:00")
    assert rc == 0 and out == "", (out, err)
    assert len(seen) == 1 and seen[0]["order"] == "asc", "only the --to lookup was issued"
    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler,
                                  "--json", "lines", "--to", "00:00:00")
    assert rc == 0 and json.loads(out) == {"lines": [], "truncated": False}


def _canned_lines(rows_by_call):
    """A /lines transport answering successive calls from `rows_by_call`, recording params."""
    seen: list[dict] = []
    answers = list(rows_by_call)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        rows, truncated = answers.pop(0) if answers else ([], False)
        return httpx.Response(200, json={"lines": rows, "truncated": truncated})

    return handler, seen


def _row(i: int, raw: str = "x") -> dict:
    return {"id": i, "ts": 1.0, "port": "b", "dir": "rx", "chan": "debug", "seq": None, "raw": raw}


def test_last_ms_is_fixed_before_paging(monkeypatch, capsys) -> None:
    """Per-page `last_ms` slides the old edge forward by the time earlier pages took; the
    CLI must send one absolute `since_ts` to every page instead."""
    page1 = ([_row(i) for i in range(2000, 1000, -1)], True)
    page2 = ([_row(i) for i in range(1000, 900, -1)], False)
    handler, seen = _canned_lines([page1, page2])
    before = time.time()
    rc, out, _ = run_mcu_canned(monkeypatch, capsys, handler,
                                "lines", "--last-ms", "60000", "--limit", "5000")
    assert rc == 0 and len(out.splitlines()) == 1100
    assert len(seen) == 2 and all("last_ms" not in p for p in seen), seen
    assert seen[0]["since_ts"] == seen[1]["since_ts"]
    assert abs(float(seen[0]["since_ts"]) - (before - 60)) < 5
    assert seen[1]["id_to"] == "1000"


def test_limit_zero_still_reports_truncated(monkeypatch, capsys) -> None:
    handler, seen = _canned_lines([([], True)])
    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler, "--json", "lines", "--limit", "0")
    assert rc == 0 and json.loads(out) == {"lines": [], "truncated": True}
    assert len(seen) == 1 and seen[0]["limit"] == "0"


def test_active_must_be_positive(monkeypatch, capsys) -> None:
    handler = lambda r: httpx.Response(200, json={"channels": []})  # noqa: E731
    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler, "plot", "channels", "--active", "0")
    assert rc == 1 and "must be greater than 0" in err


def test_status_shows_when_the_session_started(monkeypatch, capsys) -> None:
    body = _status_body()
    body["session"] = {"id": 3, "name": "bench", "note": "", "started_ts": 1_700_000_000.0,
                       "ended_ts": None, "start_id": 1, "end_id": None, "auto": False}
    _, out, _ = run_mcu_canned(monkeypatch, capsys, lambda r: httpx.Response(200, json=body),
                               "status")
    assert "session: bench (id 3, running since " in out and "running)" not in out


async def test_identify_can_be_switched_off_per_port() -> None:
    import asyncio

    from mcuscope.serial_link import SerialPort

    loop = asyncio.get_running_loop()
    def identifying(port: SerialPort) -> list:
        return [t for t in port._bg_tasks if t.get_coro().__qualname__.endswith("_identify")]

    quiet = SerialPort(None, loop, "board", identify=False)
    quiet._on_connect("/dev/x")
    assert not identifying(quiet) and quiet.target is None
    for task in list(quiet._bg_tasks):   # the "connected" sys row, with no store to take it
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    class _NoStore:
        async def add_line(self, **kw):
            return {"id": 1}

    loud = SerialPort(_NoStore(), loop, "board")
    loud._write_bytes = lambda data: None
    loud._on_connect("/dev/x")
    assert len(identifying(loud)) == 1, "the default pings"
    for task in list(loud._bg_tasks):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
