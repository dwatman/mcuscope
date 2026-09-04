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
from tests.support import UNOPENABLE, Stack
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


# -- disconnect_reason (SPEC 3.4) -----------------------------------------------------
#
# Driven through the real reader thread: the branch that picks between no_device and
# open_failed is a presence test taken inside the reader at the moment the open fails, so
# a test calling _on_error directly would assert nothing about which branch a real
# failure takes.


async def _reason_port(tmp_path, name, present: bool | None = None, **kwargs):
    """A started SerialPort over a real Store, plus a waiter for its status reason.

    `present` pins the presence test. On Windows a device is present when COM enumeration
    lists it, never when a file exists, so a test that needs "present but busy" cannot
    rely on a temp file the way Linux would let it.
    """
    import asyncio

    from mcuscope.store import Store

    store = Store(str(tmp_path / f"{name}.db"))
    await store.start()
    port = SerialPort(store, asyncio.get_running_loop(), "board", identify=False, **kwargs)
    if present is not None:
        port._device_present = lambda: present

    async def wait_reason(want, timeout=5.0):
        deadline = time.monotonic() + timeout
        seen = None
        while time.monotonic() < deadline:
            seen = port.status()["disconnect_reason"]
            if seen == want:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"disconnect_reason stayed {seen!r}, wanted {want!r}")

    port.start()
    return store, port, wait_reason


async def _await_connect(port, timeout=5.0) -> None:
    import asyncio

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not port.connected:
        await asyncio.sleep(0.01)
    assert port.connected, "the port never connected"


def _quiet_link(device: str):
    from mcuscope.link import SourceLink
    from tests.support import Scripted

    return SourceLink(Scripted([], idle_after=True), device=device)


async def test_reason_is_no_device_when_the_serial_number_resolves_to_nothing(tmp_path) -> None:
    """The dev-is-None branch: nothing to open, so no open was attempted."""
    store, port, wait_reason = await _reason_port(
        tmp_path, "sn", serial_number="mcuscope-no-such-serial"
    )
    try:
        await wait_reason("no_device")
    finally:
        await port.stop()
    rows, _ = store.query_lines(limit=50, order="asc")
    await store.stop()
    assert any("no device for serial_number mcuscope-no-such-serial" in r["raw"] for r in rows), \
        "the sys row must name the branch taken: resolution, not a failed open"


async def test_reason_is_no_device_when_the_open_fails_on_an_absent_node(tmp_path) -> None:
    """The device string is there, the node is not: an open failure caused by absence."""
    def opener(device: str, baud: int):
        raise OSError("[Errno 2] could not open port: no such file or directory")

    store, port, wait_reason = await _reason_port(
        tmp_path, "absent", device=str(tmp_path / "never-created"), open_link_fn=opener
    )
    try:
        await wait_reason("no_device")
    finally:
        await port.stop()
    rows, _ = store.query_lines(limit=50, order="asc")
    await store.stop()
    assert any("could not open port" in r["raw"] for r in rows), \
        "an open WAS attempted here, unlike the serial_number case"


async def test_reason_is_open_failed_when_the_device_is_there_but_busy(tmp_path) -> None:
    """Present and unopenable is a different problem from absent, and must not read as one."""
    node = tmp_path / "ttyFAKE"
    node.write_bytes(b"")

    def opener(device: str, baud: int):
        raise OSError("[Errno 16] device or resource busy")

    store, port, wait_reason = await _reason_port(
        tmp_path, "busy", device=str(node), open_link_fn=opener, present=True
    )
    try:
        await wait_reason("open_failed")
    finally:
        await port.stop()
    await store.stop()


def _drop_then_gate():
    """An opener that plays one dying link, then blocks every retry on a gate."""
    import threading

    from mcuscope.link import SourceLink
    from tests.support import Scripted

    gate = threading.Event()
    calls: list[str] = []

    def opener(device: str, baud: int):
        calls.append(device)
        if len(calls) == 1:
            return SourceLink(
                Scripted([b"alive\n", serial.SerialException("dropped")]), device=device
            )
        # Held here so the reason under test cannot be overwritten by the next attempt
        # before the assertion reads it.
        gate.wait(5.0)
        raise OSError("[Errno 2] gone")

    return opener, gate


async def test_reason_is_read_error_when_the_link_drops_mid_session(tmp_path) -> None:
    node = tmp_path / "ttyFAKE"
    node.write_bytes(b"")
    opener, gate = _drop_then_gate()
    store, port, wait_reason = await _reason_port(
        tmp_path, "drop", device=str(node), open_link_fn=opener
    )
    try:
        await wait_reason("read_error")
        assert port.status()["held"] is False, "a link that dropped was not held"
    finally:
        gate.set()
        await port.stop()
    await store.stop()


async def test_a_read_error_becomes_no_device_once_the_node_has_gone(tmp_path) -> None:
    """The reason is the current one, not the first one (SPEC 3.4: not latched)."""
    node = tmp_path / "ttyFAKE"
    node.write_bytes(b"")
    opener, gate = _drop_then_gate()
    store, port, wait_reason = await _reason_port(
        tmp_path, "unplug", device=str(node), open_link_fn=opener
    )
    try:
        await wait_reason("read_error")
        node.unlink()          # the board is now unplugged, not merely dropped
        gate.set()
        await wait_reason("no_device")
    finally:
        gate.set()
        await port.stop()
    await store.stop()


async def test_a_connect_clears_the_reason_of_the_episode_before_it(tmp_path) -> None:
    import threading

    node = tmp_path / "ttyFAKE"
    node.write_bytes(b"")
    allow = threading.Event()

    def opener(device: str, baud: int):
        if not allow.is_set():
            raise OSError("[Errno 16] device or resource busy")
        return _quiet_link(device)

    store, port, wait_reason = await _reason_port(
        tmp_path, "clear", device=str(node), open_link_fn=opener, present=True
    )
    try:
        await wait_reason("open_failed")
        allow.set()
        await _await_connect(port)
        assert port.status()["disconnect_reason"] is None, \
            "a connected port has no reason to report"
        assert port.disconnect_reason is None, "the stored reason is cleared, not just masked"
    finally:
        await port.stop()
    await store.stop()


async def test_manual_survives_a_reader_callback_that_lands_after_hold(tmp_path) -> None:
    """hold() and the reader race: the reason says who closed the port, not who noticed."""
    node = tmp_path / "ttyFAKE"
    node.write_bytes(b"")
    store, port, _ = await _reason_port(
        tmp_path, "manual", device=str(node),
        open_link_fn=lambda device, baud: _quiet_link(device),
    )
    try:
        await _await_connect(port)
        await port.hold()
        assert port.status()["disconnect_reason"] == "manual"
        # A callback the reader posted before the stop event reached it, delivered now.
        port._on_error("read error: dropped", False, "read_error")
        assert port.status()["disconnect_reason"] == "manual", \
            "a late read_error must not rewrite a port the operator closed"
        assert port.status()["held"] is True
    finally:
        await port.stop()
    await store.stop()


def test_status_names_why_a_disconnected_port_is_down(monkeypatch, capsys) -> None:
    for reason in ("no_device", "open_failed", "read_error"):
        body = _status_body(connected=False, disconnect_reason=reason)
        rc, out, _ = run_mcu_canned(monkeypatch, capsys,
                                    lambda r, b=body: httpx.Response(200, json=b), "status")
        assert rc == 0
        assert f"disconnected ({reason})" in out, out

    # Held wins: "on request" is more use to a reader than the bare word.
    body = _status_body(connected=False, held=True, disconnect_reason="manual")
    _, out, _ = run_mcu_canned(monkeypatch, capsys,
                               lambda r: httpx.Response(200, json=body), "status")
    assert "held (disconnected on request)" in out and "(manual)" not in out, out

    # An older daemon, or a port down before any attempt: no brackets, and never "(None)".
    body = _status_body(connected=False)
    _, out, _ = run_mcu_canned(monkeypatch, capsys,
                               lambda r: httpx.Response(200, json=body), "status")
    assert re.search(r"disconnected(?!\s*\()", out), out
    assert "None" not in out, out


def test_a_port_reports_connecting_before_its_first_open_attempt() -> None:
    """null is reserved for "connected", and POST /ports answers before the open lands."""
    stack = Stack()
    try:
        with httpx.Client(base_url=stack.base_url, timeout=5.0) as c:
            r = c.post("/ports", json={"alias": "dead", "device": UNOPENABLE, "baud": 115200})
            assert r.status_code == 200, r.text
            port = r.json()["port"]
            assert port["connected"] is False
            assert port["disconnect_reason"] == "connecting", port

            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                got = [p for p in c.get("/status").json()["ports"] if p["alias"] == "dead"][0]
                if got["disconnect_reason"] == "no_device":
                    break
                time.sleep(0.05)
            assert got["disconnect_reason"] == "no_device", got
    finally:
        stack.close()


def test_encode_wire_refuses_an_unknown_line_ending() -> None:
    """Every caller validates first, so "cr" reaching here is a bug, not user input."""
    with pytest.raises(PortError, match="unknown line ending"):
        SerialPort._encode_wire("x", "cr")


def test_status_masks_the_reason_only_while_connected() -> None:
    """The stored reason stays put; status() is what hides it (SPEC 3.4: null if up)."""
    port = SerialPort(None, None, "board")
    port.disconnect_reason = "manual"
    port.connected = True
    assert port.status()["disconnect_reason"] is None
    port.connected = False
    assert port.status()["disconnect_reason"] == "manual"


# -- what /send may put on the wire (SPEC 3.4: no CR or LF in the body) --------------


@pytest.mark.parametrize("line", ["x\r", "x\n", "\r", "\n"])
def test_send_refuses_a_terminator_in_the_body(line) -> None:
    """Stripping it made /send write something other than what was asked for, and a body
    that was nothing but a terminator wrote zero bytes and answered ok."""
    stack = Stack()
    try:
        with httpx.Client(base_url=stack.base_url, timeout=5.0) as c:
            before = len(stack.sim.written)
            r = c.post("/send", json={"line": line, "eol": "none"})
            assert r.status_code == 400, r.text
            assert "embedded newlines" in r.json()["error"], r.text
            assert len(stack.sim.written) == before, "a refused send still wrote"
    finally:
        stack.close()
