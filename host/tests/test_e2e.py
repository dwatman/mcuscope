"""End-to-end tests for the daemon (SPEC 3.4), driven over real HTTP + sockets.

A fresh sim + daemon stack runs per test in background threads (see support.Stack),
so every endpoint is exercised against a live pyserial `socket://` connection to the
simulator. Cross-platform: no pty, no subprocess.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from collections.abc import Callable

import httpx
import websockets

from tests.support import Stack, free_port

# The `stack` and `make_stack` fixtures live in conftest.py (shared with the CLI suite).


def client(stack: Stack) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=5.0)


def poll(fn: Callable[[], bool], timeout: float = 3.0, interval: float = 0.03) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(interval)
    return False


# -- status / ports / devices ---------------------------------------------------------


def test_status(stack: Stack) -> None:
    with client(stack) as c:
        body = c.get("/status").json()
    assert body["version"]
    assert body["uptime_s"] >= 0
    assert body["ports"][0]["alias"] == "board"
    assert body["ports"][0]["connected"] is True
    assert "db_size_bytes" in body


def test_status_reports_the_serving_pid(stack: Stack) -> None:
    # `mcu daemon stop` targets this pid when it must fall back to a hard kill: the pid
    # file can name a launcher shim instead of the daemon (Windows venv launchers).
    with client(stack) as c:
        assert c.get("/status").json()["pid"] == os.getpid()


def test_shutdown_refused_without_callback(stack: Stack) -> None:
    # The test stack wires no shutdown callback, exactly like any embedded use of
    # create_app: the endpoint must refuse rather than kill the hosting process
    # (which here would be pytest itself).
    with client(stack) as c:
        r = c.post("/shutdown")
        assert r.status_code == 400
        assert "error" in r.json()
        assert c.get("/status").status_code == 200  # still serving


def test_shutdown_invokes_the_daemon_callback(stack: Stack) -> None:
    # The real daemon passes a callback that raises SIGTERM in-process; a stub proves
    # the accept-and-schedule path without needing a process to kill.
    fired = threading.Event()
    app = stack._server.config.app
    app.state.shutdown_cb = fired.set
    try:
        with client(stack) as c:
            assert c.post("/shutdown").json() == {"ok": True}
        assert fired.wait(2.0)
    finally:
        app.state.shutdown_cb = None


def test_shutdown_refused_from_non_loopback(stack: Stack) -> None:
    # Network clients cannot stop the daemon, token or not: shutdown is a local
    # operator action. ASGITransport fakes the client address; the 403 path reads
    # nothing but request.client, so no lifespan state is needed.
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(
            app=stack._server.config.app, client=("203.0.113.5", 4444)
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.post("/shutdown")

    r = asyncio.run(go())
    assert r.status_code == 403
    assert "error" in r.json()


def test_devices_shape(stack: Stack) -> None:
    with client(stack) as c:
        body = c.get("/devices").json()
    assert "devices" in body
    for d in body["devices"]:
        assert set(d) == {"device", "by_id", "description", "vid_pid", "serial_number"}


def test_ports_attach_detach(stack: Stack) -> None:
    dead = f"socket://127.0.0.1:{free_port()}"  # nothing listening: attaches, won't connect
    with client(stack) as c:
        r = c.post("/ports", json={"alias": "board2", "device": dead, "baud": 9600})
        assert r.status_code == 200
        aliases = {p["alias"] for p in c.get("/ports").json()["ports"]}
        assert aliases == {"board", "board2"}
        # Nothing is listening on that port, so it must attach and stay down. Asserting the
        # negative is what stops the harness quietly serving every device from the simulator.
        entry = next(p for p in c.get("/ports").json()["ports"] if p["alias"] == "board2")
        assert entry["connected"] is False
        assert c.delete("/ports/board2").json() == {"ok": True}
        aliases = {p["alias"] for p in c.get("/ports").json()["ports"]}
        assert aliases == {"board"}
        assert c.delete("/ports/nope").status_code == 400


def test_port_reconnect(stack: Stack) -> None:
    dead = f"socket://127.0.0.1:{free_port()}"  # nothing listening: attaches, won't connect
    with client(stack) as c:
        # Reconnect of a live port re-attaches with the same parameters and keeps working.
        r = c.post(f"/ports/{stack.alias}/reconnect")
        assert r.status_code == 200
        assert r.json()["port"]["alias"] == stack.alias
        assert poll(lambda: c.get("/ports").json()["ports"][0]["connected"])
        # A disconnected port can be told to retry now; parameters are preserved.
        c.post("/ports", json={"alias": "board2", "device": dead, "baud": 9600})
        r = c.post("/ports/board2/reconnect")
        assert r.status_code == 200
        assert r.json()["port"]["device"] == dead
        assert r.json()["port"]["baud"] == 9600
        c.delete("/ports/board2")
        # Unknown alias is a plain 400 envelope.
        assert c.post("/ports/nope/reconnect").status_code == 400


# -- cmd ------------------------------------------------------------------------------


def test_cmd_ok(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/cmd", json={"cmd": "i2c scan"}).json()
    assert r["status"] == "ok"
    assert r["data"] == "48 50"
    assert isinstance(r["line_id"], int)
    assert r["latency_ms"] >= 0


def test_cmd_err(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/cmd", json={"cmd": "gpio get nope"}).json()
    assert r["status"] == "err"
    assert r["err_code"] == 2
    assert r["err_name"] == "badarg"


def test_cmd_timeout_on_dropped_response(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--drop-response", "1"])  # sim swallows the first response
    with client(stack) as c:
        r = c.post("/cmd", json={"cmd": "ping", "timeout_ms": 600}).json()
    assert r["status"] == "timeout"
    assert r["line_id"] is None


def test_late_response_logged_not_delivered(stack: Stack) -> None:
    # A tiny timeout makes the daemon give up before the (real) response arrives; the
    # late response must still be logged as a resp row, just not delivered (SPEC 3.2).
    with client(stack) as c:
        r = c.post("/cmd", json={"cmd": "ping", "timeout_ms": 1}).json()
        seq = r["seq"]

        def resp_logged() -> bool:
            rows = c.get("/lines", params={"chan": "resp", "limit": 200}).json()["lines"]
            return any(row["seq"] == seq for row in rows)

        assert poll(resp_logged), "late response was not logged"
    if r["status"] == "timeout":
        assert r["line_id"] is None  # timed-out command does not deliver the late row


def test_empty_cmd_is_client_error_not_500(stack: Stack) -> None:
    """An empty command is the caller's mistake, so it must not read as a server fault.

    format_command raises ProtocolError, while every sibling validation on the outgoing
    path (_encode_wire, _write_bytes) raises PortError, which the handlers map to 400.
    Unmapped, it reached FastAPI as a 500 and put a traceback in the daemon log for a
    routine typo. Found on the bench against real firmware, 2026-08-01.
    """
    with client(stack) as c:
        for bad in ("", "   ", "\t"):
            r = c.post("/cmd", json={"cmd": bad})
            assert r.status_code == 400, (bad, r.status_code, r.text)
            assert r.json()["error"] == "empty command"

        # The same validation runs for /wait and /assert, the other two send_command
        # callers, and must answer the same way rather than 500 on their own path.
        r = c.post("/wait", json={"match": "x", "timeout_ms": 50, "send": "  "})
        assert r.status_code == 400, r.text
        assert r.json()["error"] == "empty command"
        r = c.post("/assert", json={"expect": ["x"], "timeout_ms": 50, "send": "  "})
        assert r.status_code == 400, r.text
        assert r.json()["error"] == "empty command"

        # Discrimination: the neighbouring rejections were already 400, and a real command
        # still works, so this test fails for the empty-command mapping and nothing else.
        assert c.post("/cmd", json={"cmd": "x" * 300}).status_code == 400
        assert c.post("/cmd", json={"cmd": "ping\nping"}).status_code == 400
        assert c.post("/cmd", json={"cmd": "i2c scan"}).json()["status"] == "ok"


# -- send / marker --------------------------------------------------------------------


def test_send_raw_logged(stack: Stack) -> None:
    with client(stack) as c:
        assert c.post("/send", json={"line": "hello raw"}).json() == {"ok": True}

        def logged() -> bool:
            rows = c.get("/lines", params={"chan": "cmd", "match": "hello raw"}).json()["lines"]
            return len(rows) >= 1

        assert poll(logged)


def test_marker(stack: Stack) -> None:
    with client(stack) as c:
        line_id = c.post("/marker", json={"text": "checkpoint A"}).json()["line_id"]
        rows = c.get("/lines", params={"chan": "marker"}).json()["lines"]
    assert any(row["id"] == line_id and row["raw"] == "checkpoint A" for row in rows)


def test_firmware_marker_is_stored_on_the_marker_channel(stack: Stack) -> None:
    """A well-formed `!m` from the MCU files as a marker row, not a generic event."""
    with client(stack) as c:
        c.post("/cmd", json={"cmd": "mark checkpoint B"})

        def landed() -> bool:
            rows = c.get("/lines", params={"chan": "marker", "match": "checkpoint B"})
            return len(rows.json()["lines"]) >= 1

        assert poll(landed)
        row = c.get("/lines", params={"chan": "marker", "match": "checkpoint B"}).json()["lines"][0]
    # The whole wire line is kept, tick and all; only the display strips the prefix.
    assert re.fullmatch(r"!m @\d+ checkpoint B", row["raw"])
    # Attributed to the port it came from, unlike a host-side marker (port "").
    assert row["port"] == "board" and row["dir"] == "rx"


async def test_marker_ingest_channel_assignment(tmp_path) -> None:
    """Only a well-formed `!m` becomes a marker row; anything else stays a generic event."""
    from mcuscope.serial_link import SerialPort
    from mcuscope.store import Store

    store = Store(str(tmp_path / "cap.db"))
    await store.start(retention_days=7)
    try:
        port = SerialPort(store, asyncio.get_running_loop(), "board")
        cases = {
            "!m @12345 calibration start": "marker",
            "!m boot done": "marker",
            "!m 12 cells balanced": "marker",   # bare number is text, not a tick
            "!m": "event",                      # no text
            "!m @99": "event",                  # tick but no text
            "!m @4294967296 over": "event",     # tick out of range
            "!mystery boom": "event",           # not the !m line type
        }
        for line, expected in cases.items():
            row = await port._store_rx_line(time.time(), line)
            assert row["chan"] == expected, line
            assert row["raw"] == line, "the whole wire line is stored either way"
    finally:
        await store.stop()


# -- lines queries --------------------------------------------------------------------


def test_lines_chan_and_match_filters(stack: Stack) -> None:
    with client(stack) as c:
        assert poll(lambda: len(c.get("/lines", params={"chan": "event"}).json()["lines"]) > 0)
        rows = c.get("/lines", params={"chan": "event"}).json()["lines"]
        assert all(row["chan"] == "event" for row in rows)
        # regex match applied to raw
        matched = c.get("/lines", params={"match": "^!can"}).json()["lines"]
        assert all(row["raw"].startswith("!can") for row in matched)


def test_lines_since_id_and_limit_cap(stack: Stack) -> None:
    with client(stack) as c:
        top = c.get("/lines", params={"limit": 1}).json()["lines"][0]["id"]
        newer = c.get("/lines", params={"since_id": top, "limit": 1000}).json()["lines"]
        assert all(row["id"] > top for row in newer)
        # Over-asking must not error. The cap itself cannot be observed here - this fixture
        # holds tens of lines, so `<= 1000` would hold at any cap - and is pinned against a
        # seeded store in test_hardening.py.
        body = c.get("/lines", params={"limit": 5000}).json()
        assert body["lines"]


def test_lines_last_ms(stack: Stack) -> None:
    with client(stack) as c:
        assert poll(lambda: len(c.get("/lines", params={"last_ms": 100000}).json()["lines"]) > 0)


# -- CAN decode -----------------------------------------------------------------------


def test_can_frames_by_id(stack: Stack) -> None:
    with client(stack) as c:
        # The sim streams an id 0x100 heartbeat at 10 Hz.
        def have_frames() -> bool:
            return len(c.get("/can/frames", params={"id": "0x100"}).json()["frames"]) > 0

        assert poll(have_frames, timeout=3.0)
        frames = c.get("/can/frames", params={"id": "100"}).json()["frames"]
    f = frames[0]
    assert f["can_id"] == 0x100 and f["ext"] is False and f["rtr"] is False
    assert f["dlc"] == 4 and len(f["data_hex"]) == 8


# -- wait -----------------------------------------------------------------------------


def test_wait_match(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/wait", json={"match": "^!can", "timeout_ms": 2000}).json()
    assert r["status"] == "match"
    assert r["line"]["raw"].startswith("!can")


def test_wait_timeout(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/wait", json={"match": "WILLNEVERMATCH_ZZZ", "timeout_ms": 300}).json()
    assert r["status"] == "timeout"
    assert r["line"] is None


def test_wait_with_send(stack: Stack) -> None:
    # Send a CAN frame; the sim echoes it back with id+1 (0x300 -> 0x301) after 20 ms.
    with client(stack) as c:
        r = c.post(
            "/wait",
            json={"send": "can tx 300 AABB", "match": "301 AABB", "timeout_ms": 2000},
        ).json()
    assert r["status"] == "match"
    assert r["cmd_result"]["status"] == "ok"
    assert "301 AABB" in r["line"]["raw"]


# -- WebSocket ------------------------------------------------------------------------


async def test_ws_streams_live_rows(stack: Stack) -> None:
    url = stack.base_url.replace("http", "ws") + "/ws"
    async with websockets.connect(url) as ws:
        raw = await asyncio.wait_for(ws.recv(), 5.0)
    frame = json.loads(raw)
    # SPEC 3.4: every frame is an array, even when it carries only one item.
    assert isinstance(frame, list) and frame
    # The daemon leads with its capture identity, so the client can tell a continuation of
    # the id space it already holds from a replacement of it.
    assert frame[0] == {"capture": frame[0].get("capture")} and frame[0]["capture"]
    rows = [r for r in frame if "id" in r]
    assert rows, "the opening frame carried the capture but no rows"
    assert set(rows[0]) >= {"id", "ts", "port", "dir", "chan", "raw"}


async def test_ws_port_filter(stack: Stack) -> None:
    # /ws?port= restricts the stream to one port's rows (server.py subscribe filter).
    base = stack.base_url.replace("http", "ws")
    async with websockets.connect(base + "/ws?port=board") as ws:
        for _ in range(3):
            for row in json.loads(await asyncio.wait_for(ws.recv(), 5.0)):
                if "id" not in row:
                    continue        # control object (capture identity, gap notice)
                assert row["port"] == "board"
    # A filter that matches no port yields nothing (the daemon still streams "board" rows).
    async with websockets.connect(base + "/ws?port=ZZZ_nope") as ws:
        try:
            await asyncio.wait_for(ws.recv(), 0.8)
            leaked = True
        except TimeoutError:
            leaked = False
    assert not leaked, "port filter leaked rows for an unknown port"


async def test_purging_the_newest_ids_reaches_a_live_subscriber(stack: Stack) -> None:
    # The one reset a client cannot infer. `lines.id` is a plain rowid, so purging the
    # highest id frees it and the next line captured takes it again: ids keep climbing,
    # timestamps keep climbing, and every id the client holds now names a different row.
    # Its watermark then discards the continuation as duplicates for the life of the page.
    url = stack.base_url.replace("http", "ws") + "/ws"
    async with websockets.connect(url) as ws:
        frame = json.loads(await asyncio.wait_for(ws.recv(), 5.0))
        before = frame[0]["capture"]
        assert before

        with client(stack) as c:
            top = c.get("/lines", params={"limit": 1}).json()["lines"][0]["id"]
            res = c.post("/purge", json={"id_from": top, "id_to": top})
            assert res.status_code == 200 and res.json()["deleted"] == 1

        # No reconnect, no restart: the daemon has to volunteer it on the open socket.
        deadline = asyncio.get_running_loop().time() + 10.0
        after = None
        while after is None and asyncio.get_running_loop().time() < deadline:
            for row in json.loads(await asyncio.wait_for(ws.recv(), 5.0)):
                if "capture" in row:
                    after = row["capture"]
        assert after is not None, "the purge never reached the live subscriber"
        assert after != before, "the capture identity did not change when its top id was freed"


async def test_ws_backpressure_drop_oldest(stack: Stack) -> None:
    # A stalled WS subscriber must never block ingestion: the store fan-out drops the
    # oldest queued row (store._broadcast) instead of blocking the writer.
    url = stack.base_url.replace("http", "ws") + "/ws"
    n = 2500  # exceeds the 2000-deep subscriber queue, so drop-oldest must engage
    async with websockets.connect(url, ping_interval=None, max_queue=1):
        # Never read from the socket above; flood the daemon and confirm every write lands.
        with client(stack) as c:
            before = c.get("/lines", params={"limit": 1}).json()["lines"][0]["id"]
            for i in range(n):
                assert c.post("/send", json={"line": f"flood {i}"}).status_code == 200
            top = c.get("/lines", params={"limit": 1}).json()["lines"][0]["id"]
            assert top - before >= n, "ingestion stalled behind the slow WS consumer"
            assert c.get("/status").status_code == 200  # daemon still responsive


# -- reconnect ------------------------------------------------------------------------


def test_garbage_line_ingested(stack: Stack) -> None:
    # Binary/control junk through the raw send path must be stored, not crash the daemon,
    # and the daemon must keep serving commands afterward (SPEC 3.5 robustness).
    with client(stack) as c:
        junk = "\x01\x02\x7f binary junk line"
        assert c.post("/send", json={"line": junk}).json() == {"ok": True}

        def stored() -> bool:
            rows = c.get("/lines", params={"chan": "cmd", "match": "binary junk"}).json()["lines"]
            return len(rows) >= 1

        assert poll(stored), "garbage line was not stored"
        assert c.post("/cmd", json={"cmd": "ping"}).json()["status"] == "ok"


def test_generic_error_returns_json_envelope(tmp_path) -> None:
    # SPEC 3.4: any unhandled error becomes a {"error": msg} envelope, not a bare 500 page.
    from fastapi.testclient import TestClient

    from mcuscope.config import Config, ServerConfig, StorageConfig
    from mcuscope.server import create_app

    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db"), retention_days=7),
        ports=[],
    )
    app = create_app(config)
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as c:

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        app.state.store.query_can_frames = boom  # force an internal error in a route
        r = c.get("/can/frames")
    assert r.status_code == 500
    assert r.json() == {"error": "boom"}


async def test_malformed_seq_response_resolves_fast(tmp_path) -> None:
    # A seq-bearing but malformed response must pop its pending entry and resolve the
    # command immediately (as "err"/unparseable), not stall for the full timeout.
    from mcuscope.serial_link import SerialPort
    from mcuscope.store import Store

    store = Store(str(tmp_path / "cap.db"))
    await store.start(retention_days=7)
    try:
        loop = asyncio.get_running_loop()
        port = SerialPort(store, loop, "board")
        port._write_bytes = lambda data: None  # no real device: skip the actual write

        async def reply_garbage() -> None:
            while not port._pending:
                await asyncio.sleep(0.005)
            seq = next(iter(port._pending))
            await port._store_rx_line(time.time(), f"<{seq} GARBAGE")

        replier = asyncio.create_task(reply_garbage())
        t0 = loop.time()
        result = await port.send_command("ping", timeout_ms=2000)
        await replier
        assert result["status"] == "err"
        assert result["err_detail"] == "unparseable response"
        assert (loop.time() - t0) < 1.0, "malformed response waited out the full timeout"
    finally:
        await store.stop()


def test_reconnect_after_sim_drop(stack: Stack) -> None:
    assert stack.wait_connected(True)
    stack.stop_sim()
    assert stack.wait_connected(False), "daemon did not notice the dropped connection"
    stack.restart_sim()
    assert stack.wait_connected(True), "daemon did not reconnect"
    with client(stack) as c:
        r = c.post("/cmd", json={"cmd": "ping", "timeout_ms": 1500}).json()
    assert r["status"] == "ok"


async def test_ws_announces_rows_it_shed_from_a_slow_subscriber(stack: Stack) -> None:
    """A subscriber that stops reading is told what it missed, in band (SPEC 3.4).

    The feed sheds the oldest row rather than blocking the writer, and used to do so
    silently: a 60 s stall lost 36.7% of the span with every health field green. The gap
    cannot be inferred from an id jump, because `port=` filtering makes those legitimate.
    """
    url = stack.base_url.replace("http", "ws") + "/ws"
    async with websockets.connect(url) as ws:
        await asyncio.wait_for(ws.recv(), 5.0)          # connected, and the feed is live
        # Overrun the subscriber queue without reading it. The sim emits continuously, so
        # the wait is what fills it; the queue holds WS_SUB_MAXSIZE rows.
        store = stack.app.state.store
        q = next(iter(store._subscribers))
        for i in range(q.maxsize + 50):
            store._broadcast({"id": 10_000 + i, "port": stack.alias, "raw": f"flood{i}"})
        frames = []
        for _ in range(4):
            frames.append(json.loads(await asyncio.wait_for(ws.recv(), 5.0)))
        flat = [item for f in frames for item in f]
        gaps = [item for item in flat if "gap" in item]
        assert gaps, f"rows were shed and no frame said so: {flat[:4]}"
        assert gaps[0]["gap"] >= 50, f"the gap under-reports what was shed: {gaps[0]}"



def test_lines_since_ts_excludes_what_predates_it(stack: Stack) -> None:
    """`since_ts` is a documented SPEC 3.4 selector that no test passed a value for.

    Dropping it on the floor left every row in the answer, which reads as a working filter
    right up until someone relies on it to mean "only what arrived after".
    """
    with client(stack) as c:
        assert poll(lambda: len(c.get("/lines", params={"limit": 1000}).json()["lines"]) >= 4)
        rows = c.get("/lines", params={"limit": 1000, "order": "asc"}).json()["lines"]
        cut = rows[len(rows) // 2]["ts"]
        newer = c.get("/lines", params={"since_ts": cut, "limit": 1000}).json()["lines"]
    assert newer, "since_ts excluded the whole capture"
    assert all(row["ts"] > cut for row in newer)
    assert len(newer) < len(rows), "since_ts returned rows it should have excluded"


def test_attach_by_serial_number_without_a_device(stack: Stack) -> None:
    """SPEC 3.3 lists serial_number as an alternative to device, and only device was driven.

    The 400 for "neither given" passes just as well against a rule demanding `device`
    unconditionally, so it never distinguished the two.
    """
    with client(stack) as c:
        r = c.post("/ports", json={"alias": "byserial", "serial_number": "NO-SUCH-SERIAL"})
        assert r.status_code == 200, r.text
        entry = next(p for p in c.get("/ports").json()["ports"] if p["alias"] == "byserial")
        assert entry["connected"] is False, "nothing answers to that serial number"
        assert c.delete("/ports/byserial").json() == {"ok": True}
        # Neither selector is still refused: this test must not be what makes that pass.
        assert c.post("/ports", json={"alias": "nothing"}).status_code == 400


def test_marker_is_attributed_to_the_port_it_names(stack: Stack) -> None:
    with client(stack) as c:
        line_id = c.post("/marker", json={"text": "port-scoped", "port": stack.alias})
        line_id = line_id.json()["line_id"]
        rows = c.get("/lines", params={"chan": "marker", "match": "port-scoped"}).json()["lines"]
    row = next(r for r in rows if r["id"] == line_id)
    assert row["port"] == stack.alias, "the marker lost the port it was filed against"


def test_attach_refuses_an_impossible_baud(stack: Stack) -> None:
    """The live attach had no ceiling while the saved config path did, which is backwards.

    A saved value is re-read and re-validated on every start; the live one goes straight at
    the driver.
    """
    with client(stack) as c:
        r = c.post("/ports", json={
            "alias": "fast", "device": "socket://127.0.0.1:9", "baud": 999_999_999_999,
        })
        assert r.status_code == 422
        assert not any(p["alias"] == "fast" for p in c.get("/ports").json()["ports"])
        # A high but real rate is still accepted, so this is a ceiling and not a ban.
        ok = c.post("/ports", json={
            "alias": "fast", "device": f"socket://127.0.0.1:{free_port()}", "baud": 12_000_000,
        })
        assert ok.status_code == 200
        c.delete("/ports/fast")


def test_a_zero_limit_returns_no_rows_rather_than_one(stack: Stack) -> None:
    """`limit=0` is a real request, not a mistake to be corrected upward.

    `mcu can dump -n 0 -f` asks for no backfill before it starts following. The clamp
    floored at 1, so it got a row it had not asked for and a `truncated` flag alongside it
    saying there was more - which for a caller that wanted nothing is doubly wrong.
    """
    with client(stack) as c:
        assert poll(lambda: len(c.get("/lines", params={"limit": 5}).json()["lines"]) > 0)
        body = c.get("/lines", params={"limit": 0}).json()
        assert body["lines"] == []
        # `truncated` stays true and that is correct: rows do exist beyond the zero
        # returned. It is the row that was wrong, not the flag.
        assert body["truncated"] is True
        frames = c.get("/can/frames", params={"limit": 0}).json()
        assert frames["frames"] == []
        # A normal limit still returns rows, so this is a floor and not a broken query.
        assert c.get("/lines", params={"limit": 5}).json()["lines"]
