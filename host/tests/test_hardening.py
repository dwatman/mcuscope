"""Regression tests for daemon-core hardening: writer resilience, bounded queues,
integer bounds on device-controlled tokens, and outgoing-line validation.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from mcuscope import protocol as p
from mcuscope.serial_link import (
    RX_QUEUE_MAX,
    PortError,
    SerialPort,
    _Pending,
    _response_seq,
)
from mcuscope.store import Store, StoreError

# -- store writer resilience -----------------------------------------------------------


class _CommitBoom:
    """Connection proxy whose first commit() raises, like a disk-full error would."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._armed = True

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def commit(self) -> None:
        if self._armed:
            self._armed = False
            raise sqlite3.OperationalError("disk I/O error")
        self._conn.commit()


async def _add_sys(store: Store, raw: str) -> dict:
    return await store.add_line(
        ts=time.time(), port="t", dir="-", chan="sys", seq=None, raw=raw
    )


def test_writer_survives_commit_failure(tmp_path) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "c.db"))
        await store.start()
        try:
            store._conn = _CommitBoom(store._conn)
            with pytest.raises(StoreError):
                await _add_sys(store, "first")
            # The writer must still be alive and serving after the failed commit.
            row = await _add_sys(store, "second")
            assert row["id"] > 0
        finally:
            await store.stop()

    asyncio.run(run())


def test_writer_survives_bad_insert(tmp_path) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "b.db"))
        await store.start()
        try:
            with pytest.raises(sqlite3.IntegrityError):
                # violates the chan CHECK constraint
                await store.add_line(
                    ts=time.time(), port="t", dir="-", chan="nope", seq=None, raw="x"
                )
            row = await _add_sys(store, "still alive")
            assert row["id"] > 0
        finally:
            await store.stop()

    asyncio.run(run())


def test_failed_child_insert_leaves_no_orphan_line(tmp_path) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "o.db"))
        await store.start()
        try:
            bad_can = {"tick_ms": 0, "can_id": None, "ext": False, "rtr": False,
                       "dlc": 0, "data": b""}
            with pytest.raises(sqlite3.IntegrityError):
                await store.add_line(
                    ts=time.time(), port="t", dir="rx", chan="event", seq=None,
                    raw="!can bad", can=bad_can,
                )
            assert store.max_id() == 0  # the line row was rolled back with its child
        finally:
            await store.stop()

    asyncio.run(run())


def test_bad_row_in_a_batch_does_not_lose_its_neighbours(tmp_path) -> None:
    # The writer inserts a whole batch with one executemany per table; a single bad row
    # aborts that statement, so the batch is redone row by row (store._insert_individually)
    # and only the offender fails.
    async def run() -> None:
        store = Store(str(tmp_path / "batch.db"))
        await store.start()
        try:
            good_a = await store.submit_line(
                ts=time.time(), port="t", dir="-", chan="sys", seq=None, raw="a"
            )
            bad = await store.submit_line(
                ts=time.time(), port="t", dir="-", chan="nope", seq=None, raw="b"
            )
            good_b = await store.submit_line(
                ts=time.time(), port="t", dir="-", chan="sys", seq=None, raw="c"
            )
            assert (await good_a)["raw"] == "a"
            with pytest.raises(sqlite3.IntegrityError):
                await bad
            assert (await good_b)["raw"] == "c"
            rows, _ = store.query_lines(limit=10, order="asc")
            assert [r["raw"] for r in rows] == ["a", "c"]
            # Ids stay unique and increasing after the fallback resynced the counter.
            follow = await _add_sys(store, "d")
            assert follow["id"] > rows[-1]["id"]
        finally:
            await store.stop()

    asyncio.run(run())


def test_id_sequence_continues_across_restart(tmp_path) -> None:
    # The writer assigns ids itself, so a reopened store must pick up where the file left
    # off rather than colliding with existing rows.
    path = str(tmp_path / "seq.db")

    async def run() -> None:
        store = Store(path)
        await store.start()
        try:
            first = await _add_sys(store, "before restart")
        finally:
            await store.stop()

        store = Store(path)
        await store.start()
        try:
            second = await _add_sys(store, "after restart")
            assert second["id"] == first["id"] + 1
            rows, _ = store.query_lines(limit=10, order="asc")
            assert [r["raw"] for r in rows] == ["before restart", "after restart"]
        finally:
            await store.stop()

    asyncio.run(run())


def test_batched_children_attach_to_their_own_line(tmp_path) -> None:
    # can/plot children are inserted with the id the writer assigned to their line, not
    # with a lastrowid read back per row; a batch must not cross-link them.
    async def run() -> None:
        store = Store(str(tmp_path / "kids.db"))
        await store.start()
        try:
            futs = []
            for i in range(3):
                futs.append(await store.submit_line(
                    ts=time.time(), port="t", dir="rx", chan="event", seq=None,
                    raw=f"!can {i}",
                    can={"tick_ms": i, "can_id": 0x100 + i, "ext": False, "rtr": False,
                         "dlc": 1, "data": bytes([i])},
                    plot=[{"tick_ms": i, "sid": "0", "name": "v", "value": float(i)}],
                ))
            rows = [await f for f in futs]
            frames, _ = store.query_can_frames(limit=10)
            by_line = {f["line_id"]: f["can_id"] for f in frames}
            assert by_line == {row["id"]: 0x100 + i for i, row in enumerate(rows)}
            points = store.query_plot_series(name="v")
            assert [pt["line_id"] for pt in points] == [row["id"] for row in rows]
        finally:
            await store.stop()

    asyncio.run(run())


# -- size-capped retention (SPEC 3.2) --------------------------------------------------


async def _fill(store: Store, n: int, payload: str) -> None:
    futs = [
        await store.submit_line(
            ts=time.time(), port="t", dir="rx", chan="debug", seq=None,
            raw=f"{i} {payload}",
        )
        for i in range(n)
    ]
    for fut in futs:
        await fut


def test_size_cap_trims_oldest_and_converges(tmp_path) -> None:
    # The cap must remove the OLDEST lines, keep the newest, and settle: SQLite reuses
    # freed pages rather than shrinking, so a cap measured against the file size would
    # still read "too big" after a trim and keep deleting until the capture was empty.
    async def run() -> None:
        store = Store(str(tmp_path / "cap.db"))
        await store.start(retention_days=7, max_db_bytes=0)
        try:
            await _fill(store, 4000, "x" * 200)
            cap = store.content_bytes() // 2
            store.set_max_db_bytes(cap)

            assert await store._sweep_size_async() > 0
            assert store.content_bytes() <= cap
            assert store.lines_trimmed > 0

            rows, _ = store.query_lines(limit=1, order="desc")
            assert rows[0]["raw"].startswith("3999 "), "newest line must survive a trim"
            rows, _ = store.query_lines(limit=1, order="asc")
            assert not rows[0]["raw"].startswith("0 "), "oldest lines should have gone"

            # Converged: an immediate re-check must not keep eating the capture.
            remaining = store.max_id() and len(store.query_lines(limit=1000)[0])
            assert await store._sweep_size_async() == 0
            assert remaining > 0
        finally:
            await store.stop()

    asyncio.run(run())


def test_size_cap_off_by_default_never_trims(tmp_path) -> None:
    # The default must not drop anything: age retention is the only bound unless the
    # owner opts in to a size cap.
    async def run() -> None:
        store = Store(str(tmp_path / "nocap.db"))
        await store.start()
        try:
            await _fill(store, 500, "y" * 200)
            assert await store._sweep_size_async() == 0
            assert store.lines_trimmed == 0
            rows, _ = store.query_lines(limit=1, order="asc")
            assert rows[0]["raw"].startswith("0 ")
        finally:
            await store.stop()

    asyncio.run(run())


def test_db_size_counts_the_wal(tmp_path) -> None:
    # Under WAL a large share of a fast capture sits in the -wal sidecar; reporting only
    # the main file would under-report what the capture is using.
    async def run() -> None:
        path = tmp_path / "wal.db"
        store = Store(str(path))
        await store.start()
        try:
            await _fill(store, 2000, "z" * 200)
            wal = (path.parent / (path.name + "-wal"))
            assert wal.exists() and wal.stat().st_size > 0
            assert store.db_size_bytes() >= path.stat().st_size + wal.stat().st_size
        finally:
            await store.stop()

    asyncio.run(run())


# -- integer bounds on device-controlled tokens ----------------------------------------


def test_response_seq_out_of_range_is_ignored() -> None:
    assert _response_seq("<12 OK") == 12
    assert _response_seq("<99999999999999999999999 OK") is None
    assert _response_seq("<-5 OK") is None
    assert _response_seq("<65536 OK") is None


def test_parse_hex_int_is_bounded() -> None:
    assert p.parse_hex_int("FFFFFFFFFFFFFFFF") == 2**64 - 1
    with pytest.raises(p.ProtocolError):
        p.parse_hex_int("1" + "0" * 16)  # 17 digits


def test_can_event_id_range() -> None:
    assert p.parse_can_event("!can 1 - 7FF 00") is not None
    assert p.parse_can_event("!can 1 - 800 00") is None          # > 11-bit std
    assert p.parse_can_event("!can 1 x 1FFFFFFF 00") is not None
    assert p.parse_can_event("!can 1 x 20000000 00") is None     # > 29-bit ext


def test_can_tx_id_range() -> None:
    with pytest.raises(p.ProtocolError):
        p.parse_can_tx_args(("800", "00"))
    with pytest.raises(p.ProtocolError):
        p.parse_can_tx_args(("20000000", "00", "x"))
    assert p.parse_can_tx_args(("7FF", "00")).can_id == 0x7FF


# -- outgoing-line validation ----------------------------------------------------------


def test_encode_wire_rejects_bad_lines() -> None:
    assert SerialPort._encode_wire("i2c scan") == b"i2c scan\n"
    with pytest.raises(PortError):
        SerialPort._encode_wire("foo\nbar")
    with pytest.raises(PortError):
        SerialPort._encode_wire("temp 23°C")
    with pytest.raises(PortError):
        SerialPort._encode_wire("x" * 300)


# -- port-level failure paths ----------------------------------------------------------


def test_disconnect_fails_pending_promptly(tmp_path) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "d.db"))
        await store.start()
        try:
            port = SerialPort(store, asyncio.get_running_loop(), "board")
            port.connected = True
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            port._pending[5] = _Pending(5, fut, time.time())
            port._on_disconnect()
            with pytest.raises(PortError, match="disconnected"):
                await asyncio.wait_for(fut, timeout=1.0)
            assert not port._pending
        finally:
            await store.stop()

    asyncio.run(run())


def test_rx_queue_overflow_drops_oldest(tmp_path) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "q.db"))
        await store.start()
        try:
            port = SerialPort(store, asyncio.get_running_loop(), "board")
            # No consumer running: flood the loop-side queue past its bound.
            payload = b"".join(b"line %d\n" % i for i in range(RX_QUEUE_MAX + 50))
            port._on_bytes(time.time(), payload)
            assert len(port._rx_lines) == RX_QUEUE_MAX
            assert port.rx_dropped == 50
            # Newest line survived; the oldest 50 were shed.
            newest = f"line {RX_QUEUE_MAX + 49}"
            drained = [line for _ts, line in port._rx_lines]
            assert drained[-1] == newest
            assert "line 0" not in drained
        finally:
            await store.stop()

    asyncio.run(run())


# -- server-level hardening --------------------------------------------------------------


def _mk_app(tmp_path):
    from mcuscope.config import Config, ServerConfig, StorageConfig
    from mcuscope.server import create_app

    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db"), retention_days=7),
        ports=[],
    )
    return create_app(config)


def test_ws_disconnect_releases_subscriber_without_traffic(tmp_path) -> None:
    # A client that disconnects while no rows are flowing must not leak its queue.
    from fastapi.testclient import TestClient

    app = _mk_app(tmp_path)
    with TestClient(app) as c:
        with c.websocket_connect("/ws"):
            assert len(app.state.store._subscribers) == 1
        deadline = time.monotonic() + 5.0
        while app.state.store._subscribers and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not app.state.store._subscribers


def test_request_body_bounds(tmp_path) -> None:
    from fastapi.testclient import TestClient

    app = _mk_app(tmp_path)
    with TestClient(app) as c:
        # timeout_ms must be positive and bounded
        for bad in (0, -5, 10**9):
            r = c.post("/cmd", json={"cmd": "ping", "timeout_ms": bad})
            assert r.status_code == 422, bad
            assert "error" in r.json()
        # alias must be non-empty and sane (empty collides with the daemon port="")
        for alias in ("", " ", "a b", "x" * 40):
            r = c.post("/ports", json={"alias": alias, "device": "COM99"})
            assert r.status_code == 422, alias
        # /wait only supports since="now"
        r = c.post("/wait", json={"match": "x", "timeout_ms": 10, "since": "id:5"})
        assert r.status_code == 400
        # /can/frames: truncated flag present, oversized id rejected
        r = c.get("/can/frames")
        assert r.status_code == 200 and r.json()["truncated"] is False
        r = c.get("/can/frames", params={"id": "FFFFFFFF"})
        assert r.status_code == 400


def test_subscriber_cap() -> None:
    from mcuscope.store import MAX_SUBSCRIBERS

    store = Store(":memory:")
    qs = [store.subscribe() for _ in range(MAX_SUBSCRIBERS)]
    with pytest.raises(StoreError):
        store.subscribe()
    for q in qs:
        store.unsubscribe(q)
    store.subscribe()  # room again after release


def test_bad_autoconnect_port_does_not_abort_startup(tmp_path) -> None:
    # One bad config entry (disallowed device scheme) must not kill the daemon.
    from fastapi.testclient import TestClient

    from mcuscope.config import Config, PortConfig, ServerConfig, StorageConfig
    from mcuscope.server import create_app

    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db"), retention_days=7),
        ports=[
            PortConfig(alias="bad", device="spy://COM1", baud=115200, autoconnect=True),
        ],
    )
    app = create_app(config)
    with TestClient(app) as c:
        r = c.get("/status")
        assert r.status_code == 200
        # the failure is recorded as a sys row
        rows = c.get("/lines", params={"chan": "sys", "limit": 10}).json()["lines"]
        assert any("autoconnect bad failed" in row["raw"] for row in rows)


# -- access token (server.token) ---------------------------------------------------------


def _mk_token_app(tmp_path, token: str | None):
    from mcuscope.config import Config, ServerConfig, StorageConfig
    from mcuscope.server import create_app

    config = Config(
        server=ServerConfig(host="0.0.0.0", port=0, token=token),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db"), retention_days=7),
        ports=[],
    )
    return create_app(config)


def test_token_required_for_non_loopback_clients(tmp_path) -> None:
    # TestClient connections present client host "testclient", i.e. non-loopback.
    from fastapi.testclient import TestClient

    app = _mk_token_app(tmp_path, "sesame-open-123")
    with TestClient(app) as c:
        r = c.get("/status")
        assert r.status_code == 401
        assert r.json() == {"error": "missing or invalid access token"}
        r = c.get("/status", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401
        r = c.get("/status", headers={"Authorization": "Bearer sesame-open-123"})
        assert r.status_code == 200
        r = c.get("/status", headers={"X-Auth-Token": "sesame-open-123"})
        assert r.status_code == 200
        # the static UI is always served so the page can load and prompt
        r = c.get("/", follow_redirects=False)
        assert r.status_code in (200, 307)
        r = c.get("/ui/", follow_redirects=True)
        assert r.status_code == 200
        # WebSocket: query param works, missing token is refused with close 1008
        with c.websocket_connect("/ws?token=sesame-open-123"):
            pass
        try:
            with c.websocket_connect("/ws"):
                raise AssertionError("unauthenticated WS was accepted")
        except Exception:
            pass  # closed during handshake, as required


def test_loopback_clients_exempt_from_token(tmp_path) -> None:
    from fastapi.testclient import TestClient

    app = _mk_token_app(tmp_path, "sesame-open-123")
    with TestClient(app, client=("127.0.0.1", 12345)) as c:
        assert c.get("/status").status_code == 200


def test_no_token_configured_means_open(tmp_path) -> None:
    from fastapi.testclient import TestClient

    app = _mk_token_app(tmp_path, None)
    with TestClient(app) as c:
        assert c.get("/status").status_code == 200


def test_token_static_exemption_is_exact(tmp_path) -> None:
    # Only "/", "/ui" and "/ui/..." are token-exempt; a path that merely starts with
    # the letters "/ui" (a hypothetical future /ui-admin) must still require the token.
    from fastapi.testclient import TestClient

    app = _mk_token_app(tmp_path, "sesame-open-123")
    with TestClient(app) as c:
        r = c.get("/uiadmin")
        assert r.status_code == 401


def test_wrong_token_attempts_are_rate_limited(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from mcuscope.server import TOKEN_FAIL_MAX

    app = _mk_token_app(tmp_path, "sesame-open-123")
    with TestClient(app) as c:
        for _ in range(TOKEN_FAIL_MAX):
            r = c.get("/status", headers={"Authorization": "Bearer wrong"})
            assert r.status_code == 401
        # Locked out: even the correct token is refused (429) until the lockout expires,
        # and no comparison happens while locked.
        r = c.get("/status", headers={"Authorization": "Bearer sesame-open-123"})
        assert r.status_code == 429
        assert "too many failed token attempts" in r.json()["error"]
        assert r.headers.get("retry-after")
        # The static UI stays reachable during a lockout.
        assert c.get("/ui/", follow_redirects=True).status_code == 200


def test_missing_token_does_not_count_toward_lockout(tmp_path) -> None:
    # Requests with NO token are unauthenticated clients (e.g. the UI before its first
    # prompt), not brute-force guesses; they must never lock the address out.
    from fastapi.testclient import TestClient

    from mcuscope.server import TOKEN_FAIL_MAX

    app = _mk_token_app(tmp_path, "sesame-open-123")
    with TestClient(app) as c:
        for _ in range(TOKEN_FAIL_MAX * 2):
            assert c.get("/status").status_code == 401
        r = c.get("/status", headers={"Authorization": "Bearer sesame-open-123"})
        assert r.status_code == 200


def test_correct_token_resets_failure_budget(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from mcuscope.server import TOKEN_FAIL_MAX

    app = _mk_token_app(tmp_path, "sesame-open-123")
    with TestClient(app) as c:
        for _ in range(TOKEN_FAIL_MAX - 1):
            assert c.get("/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = c.get("/status", headers={"Authorization": "Bearer sesame-open-123"})
        assert ok.status_code == 200  # one attempt short of the limit still works
        # The success cleared the slate: a fresh budget applies afterwards.
        for _ in range(TOKEN_FAIL_MAX - 1):
            assert c.get("/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = c.get("/status", headers={"Authorization": "Bearer sesame-open-123"})
        assert ok.status_code == 200


# -- config loading ----------------------------------------------------------------------


def test_bad_toml_is_a_friendly_error(tmp_path) -> None:
    from mcuscope.config import ConfigError, load_config
    from mcuscope.daemon import main as daemon_main

    cfg = tmp_path / "config.toml"
    cfg.write_text("[server\nport = not-an-int", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(cfg)
    # daemon entry point turns it into exit code 1, not a traceback
    assert daemon_main(["-c", str(cfg)]) == 1


def test_bad_config_value_is_a_friendly_error(tmp_path) -> None:
    from mcuscope.config import ConfigError, load_config

    cfg = tmp_path / "config.toml"
    cfg.write_text('[server]\nport = "abc"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(cfg)


def test_unusable_port_entries_are_skipped_with_warning(tmp_path, caplog) -> None:
    from mcuscope.config import load_config

    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[[ports]]\n"
        'device = "COM7"\n'          # no alias
        "[[ports]]\n"
        'alias = "empty"\n'          # neither device nor serial_number
        "[[ports]]\n"
        'alias = "good"\n'
        'device = "COM8"\n',
        encoding="utf-8",
    )
    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="mcuscope.config"):
        config = load_config(cfg)
    assert [pc.alias for pc in config.ports] == ["good"]
    assert any("no alias" in r.message for r in caplog.records)
    assert any("neither device nor serial_number" in r.message for r in caplog.records)


def test_token_in_config_file_is_ignored_with_warning(tmp_path, caplog) -> None:
    # SPEC 3.3: the token is runtime-only; a file key is ignored, loudly, so the
    # UI-writable config surface can never grant or revoke authentication.
    import logging as _logging

    from mcuscope.config import load_config

    cfg = tmp_path / "config.toml"
    cfg.write_text('[server]\ntoken = "secret"\nhost = "0.0.0.0"\n', encoding="utf-8")
    with caplog.at_level(_logging.WARNING, logger="mcuscope.config"):
        loaded = load_config(cfg)
    assert loaded.server.token is None
    assert loaded.server.host == "0.0.0.0"
    assert any("MCUSCOPED_TOKEN" in r.message for r in caplog.records)


# -- re-review fixes -----------------------------------------------------------------


def test_token_guard_handles_non_ascii_credentials() -> None:
    # A hostile non-ASCII Authorization header must be a clean 401, never a
    # TypeError from str-mode hmac.compare_digest. httpx refuses to send such
    # headers, so drive the middleware directly with a raw ASGI scope.
    from mcuscope.server import _TokenGuard

    async def receive() -> dict:
        return {}

    async def inner_app(scope, receive, send) -> None:
        raise AssertionError("request must be denied before reaching the app")

    async def deny_status(header: tuple[bytes, bytes]) -> int:
        sent: list[dict] = []

        async def send(msg) -> None:
            sent.append(msg)

        guard = _TokenGuard(inner_app, token="sesame-open-123")
        scope = {
            "type": "http",
            "path": "/status",
            "client": ("10.0.0.5", 1234),
            "headers": [header],
        }
        await guard(scope, receive, send)
        return sent[0]["status"]

    async def run() -> None:
        assert await deny_status((b"authorization", b"Bearer caf\xe9")) == 401
        assert await deny_status((b"x-auth-token", b"\xe9")) == 401

    asyncio.run(run())


def test_hoist_token_equals_form() -> None:
    from mcuscope.cli import _hoist_global_opts

    assert _hoist_global_opts(["status", "--token=abc"]) == ["--token=abc", "status"]
    assert _hoist_global_opts(["status", "--token", "abc"]) == ["--token", "abc", "status"]


def test_config_rejects_invalid_alias(tmp_path, caplog) -> None:
    import logging as _logging

    from mcuscope.config import load_config

    cfg = tmp_path / "config.toml"
    cfg.write_text('[[ports]]\nalias = "a/b"\ndevice = "COM7"\n', encoding="utf-8")
    with caplog.at_level(_logging.WARNING, logger="mcuscope.config"):
        config = load_config(cfg)
    assert config.ports == []
    assert any("invalid" in r.message for r in caplog.records)
