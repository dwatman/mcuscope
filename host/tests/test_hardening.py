"""Regression tests for daemon-core hardening: writer resilience, bounded queues,
integer bounds on device-controlled tokens, and outgoing-line validation.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
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
from mcuscope.store import _MAX_BATCH_ROWS, _SLOW_COMMIT_S, Store, StoreError

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


def test_writer_splits_a_backlog_across_capped_commits(tmp_path) -> None:
    # Insert and commit run on the event loop by design, so one commit absorbs at most
    # _MAX_BATCH_ROWS queued rows: the stall is bounded by construction rather than by how
    # full the queue happens to be. A bigger backlog is split, never dropped or delayed.
    async def run() -> None:
        store = Store(str(tmp_path / "cap_batch.db"))
        await store.start()
        try:
            sizes: list[int] = []
            real_insert = store._insert_batch

            def spy(batch):
                sizes.append(len(batch))
                return real_insert(batch)

            store._insert_batch = spy
            total = _MAX_BATCH_ROWS + 250
            # submit_line only enqueues (the queue is well under _WRITE_QUEUE_MAX here), so
            # the whole backlog is waiting before the writer task gets the loop back.
            futs = [
                await store.submit_line(
                    ts=time.time(), port="t", dir="rx", chan="debug", seq=None, raw=f"line {i}"
                )
                for i in range(total)
            ]
            rows = [await f for f in futs]

            assert len(sizes) > 1, f"expected more than one commit, got {sizes}"
            assert max(sizes) <= _MAX_BATCH_ROWS
            assert sum(sizes) == total
            # Every future resolved, with the ids contiguous and in submission order.
            assert [r["id"] for r in rows] == list(range(rows[0]["id"], rows[0]["id"] + total))
            # ...and every one of them is on disk (query_lines caps its limit at 1000).
            stored = store._conn.execute("SELECT raw FROM lines ORDER BY id").fetchall()
            assert [r[0] for r in stored] == [f"line {i}" for i in range(total)]
        finally:
            await store.stop()

    asyncio.run(run())


class _SlowCommit:
    """Connection proxy whose commit() blocks, like a WAL checkpoint on contended media."""

    def __init__(self, conn: sqlite3.Connection, delay: float) -> None:
        self._conn = conn
        self._delay = delay

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def commit(self) -> None:
        time.sleep(self._delay)
        self._conn.commit()


def test_slow_commit_is_logged(tmp_path, caplog) -> None:
    # The batch cap bounds the insert half only; a checkpoint fsync can still stall the
    # loop. Make that tail observable, naming the duration and the row count.
    import logging as _logging

    async def run() -> None:
        store = Store(str(tmp_path / "slow.db"))
        await store.start()
        try:
            store._conn = _SlowCommit(store._conn, _SLOW_COMMIT_S * 2)
            with caplog.at_level(_logging.WARNING, logger="mcuscope.store"):
                await _add_sys(store, "slow one")
        finally:
            store._conn = store._conn._conn
            await store.stop()

    asyncio.run(run())
    warnings = [r.message for r in caplog.records if "slow capture commit" in r.message]
    assert warnings, [r.message for r in caplog.records]
    assert "1 rows" in warnings[0]
    ms = float(warnings[0].split(":")[1].strip().split(" ")[0])
    assert ms >= _SLOW_COMMIT_S * 1000


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
    with TestClient(app, base_url="http://127.0.0.1") as c:
        with c.websocket_connect("/ws", headers={"host": "127.0.0.1"}):
            assert len(app.state.store._subscribers) == 1
        deadline = time.monotonic() + 5.0
        while app.state.store._subscribers and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not app.state.store._subscribers


def test_request_body_bounds(tmp_path) -> None:
    from fastapi.testclient import TestClient

    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
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
    with TestClient(app, base_url="http://127.0.0.1") as c:
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
    with TestClient(app, base_url="http://127.0.0.1") as c:
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
        with c.websocket_connect("/ws?token=sesame-open-123", headers={"host": "127.0.0.1"}):
            pass
        try:
            with c.websocket_connect("/ws", headers={"host": "127.0.0.1"}):
                raise AssertionError("unauthenticated WS was accepted")
        except Exception:
            pass  # closed during handshake, as required


def test_loopback_clients_exempt_from_token(tmp_path) -> None:
    from fastapi.testclient import TestClient

    app = _mk_token_app(tmp_path, "sesame-open-123")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as c:
        assert c.get("/status").status_code == 200


def test_no_token_configured_means_open(tmp_path) -> None:
    from fastapi.testclient import TestClient

    app = _mk_token_app(tmp_path, None)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert c.get("/status").status_code == 200


def test_token_static_exemption_is_exact(tmp_path) -> None:
    # Only "/", "/ui" and "/ui/..." are token-exempt; a path that merely starts with
    # the letters "/ui" (a hypothetical future /ui-admin) must still require the token.
    from fastapi.testclient import TestClient

    app = _mk_token_app(tmp_path, "sesame-open-123")
    with TestClient(app, base_url="http://127.0.0.1") as c:
        r = c.get("/uiadmin")
        assert r.status_code == 401


def test_wrong_token_attempts_are_rate_limited(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from mcuscope.server import TOKEN_FAIL_MAX

    app = _mk_token_app(tmp_path, "sesame-open-123")
    with TestClient(app, base_url="http://127.0.0.1") as c:
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
    with TestClient(app, base_url="http://127.0.0.1") as c:
        for _ in range(TOKEN_FAIL_MAX * 2):
            assert c.get("/status").status_code == 401
        r = c.get("/status", headers={"Authorization": "Bearer sesame-open-123"})
        assert r.status_code == 200


def test_correct_token_resets_failure_budget(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from mcuscope.server import TOKEN_FAIL_MAX

    app = _mk_token_app(tmp_path, "sesame-open-123")
    with TestClient(app, base_url="http://127.0.0.1") as c:
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


# -- regex isolation and /ws keepalive -------------------------------------------------


def test_match_executor_is_bounded_and_separate() -> None:
    from mcuscope.store import MATCH_WORKERS, match_executor

    pool = match_executor()
    assert pool is match_executor()          # one process-wide pool, not one per call
    assert pool._max_workers == MATCH_WORKERS
    assert pool._thread_name_prefix == "mcu-match"


async def test_match_queries_run_off_the_default_executor(tmp_path, monkeypatch) -> None:
    # A user regex must not occupy a default-executor worker: that pool is also what
    # run_in_executor(None, ...) uses to join the serial reader thread on detach.
    seen: dict[str, str] = {}
    original = Store._query_lines_threadsafe

    def spy(self, **kwargs):
        seen["thread"] = threading.current_thread().name
        return original(self, **kwargs)

    monkeypatch.setattr(Store, "_query_lines_threadsafe", spy)
    store = Store(str(tmp_path / "cap.db"))
    await store.start()
    try:
        await store.add_line(ts=time.time(), port="p", dir="rx", chan="debug", seq=None, raw="hi")
        rows, _ = await store.query_lines_safe(match="hi")
        assert len(rows) == 1
    finally:
        await store.stop()
    assert seen["thread"].startswith("mcu-match")


async def test_wait_scan_runs_off_the_default_executor(tmp_path, monkeypatch) -> None:
    # Same guarantee for the live path: /wait scans each burst on the match pool.
    from httpx import ASGITransport, AsyncClient

    from mcuscope import server as server_mod

    seen: dict[str, str] = {}
    original = server_mod._search_batch

    def spy(pattern, texts):
        seen["thread"] = threading.current_thread().name
        return original(pattern, texts)

    monkeypatch.setattr(server_mod, "_search_batch", spy)
    app = _mk_app(tmp_path)
    transport = ASGITransport(app=app)
    # Loopback base_url: the same-origin guard requires a Host it can legitimately answer
    # to (an IP literal, localhost, or the configured bind name), so a synthetic "test"
    # hostname is refused before it reaches the route.
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        async with app.router.lifespan_context(app):
            store = app.state.store

            async def feed() -> None:
                await asyncio.sleep(0.05)
                await store.add_line(
                    ts=time.time(), port="", dir="rx", chan="debug", seq=None, raw="marco polo"
                )

            task = asyncio.create_task(feed())
            body = await client.post("/wait", json={"match": "polo", "timeout_ms": 3000})
            await task
    assert body.json()["status"] == "match"
    assert seen["thread"].startswith("mcu-match")


def test_ws_sends_an_idle_keepalive_frame(tmp_path, monkeypatch) -> None:
    # With no rows flowing the daemon must still write periodically, so a client that
    # vanished without a TCP close is reaped instead of holding its queue indefinitely.
    from fastapi.testclient import TestClient

    from mcuscope import server as server_mod

    monkeypatch.setattr(server_mod, "WS_KEEPALIVE_S", 0.1)
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        with c.websocket_connect("/ws", headers={"host": "127.0.0.1"}) as ws:
            assert ws.receive_json() == []     # keepalive: an empty SPEC 3.4 frame
            assert ws.receive_json() == []     # and it repeats, so detection is bounded


# -- lost writes are visible, and counting stays off the loop ---------------------------


def test_failed_write_is_counted(tmp_path) -> None:
    # A write the store cannot persist is a line the serial layer already counted as
    # received: with no counter, the loss showed up nowhere but the log.
    async def run() -> None:
        store = Store(str(tmp_path / "we.db"))
        await store.start()
        try:
            assert store.write_errors == 0
            store._conn = _CommitBoom(store._conn)
            with pytest.raises(StoreError):
                await _add_sys(store, "lost")
            assert store.write_errors == 1
            await _add_sys(store, "kept")     # a later success must not reset the count
            assert store.write_errors == 1
        finally:
            await store.stop()

    asyncio.run(run())


def test_status_reports_write_errors(tmp_path) -> None:
    # SPEC 3.4: /status carries a top-level integer `write_errors`, always present, and it
    # moves when a write is lost. The whole point is that the failure is visible without
    # reading the daemon log.
    from fastapi.testclient import TestClient

    app = _mk_app(tmp_path)
    # raise_server_exceptions=False: the failing write is the point, so the 500 is wanted
    # as a response rather than re-raised into the test.
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as c:
        body = c.get("/status").json()
        assert body["write_errors"] == 0
        assert isinstance(body["write_errors"], int)
        app.state.store._conn = _CommitBoom(app.state.store._conn)
        r = c.post("/marker", json={"text": "boom"})
        assert r.status_code >= 400
        assert c.get("/status").json()["write_errors"] == 1


def test_status_reports_the_applied_size_cap(tmp_path) -> None:
    # /status must show the cap the store is enforcing, not the one config asked for.
    from fastapi.testclient import TestClient

    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        app.state.store.set_max_db_bytes(4096)
        assert c.get("/status").json()["db_max_bytes"] == 4096


def test_session_line_count_is_bounded_at_both_ends(tmp_path) -> None:
    # The per-session COUNT runs on the event loop, once per listed session. A running
    # session's `end_id IS NULL OR id <= end_id` upper bound is not sargable, so the count
    # scanned to the end of the table; COALESCE keeps both ends a seek.
    from mcuscope.store import SESSION_LIST_SQL

    async def run() -> None:
        store = Store(str(tmp_path / "plan.db"))
        await store.start()
        try:
            await store.start_session("run-a")
            await _add_sys(store, "one")
            assert store.list_sessions()[0]["lines"] >= 1
            plan = " ".join(
                str(r[3])
                for r in store._conn.execute(
                    "EXPLAIN QUERY PLAN " + SESSION_LIST_SQL, (50,)
                ).fetchall()
            ).replace(" ", "")
            # Both ends of the range, not just `rowid>?`: an open upper bound is the
            # 2060 ms plan at 1M lines.
            assert "rowid>?" in plan and "rowid<?" in plan, plan
        finally:
            await store.stop()

    asyncio.run(run())


def _captured_plan(store: Store, run) -> str:
    """EXPLAIN the statement the store actually issued, rather than a copy of it.

    A plan test that explains a hand-written query proves nothing about the daemon (this
    round's test-quality leg found exactly that shape), so the statement is taken off the
    connection's trace callback, which reports it with its parameters already substituted.
    """
    seen: list[str] = []
    store._conn.set_trace_callback(seen.append)
    try:
        run()
    finally:
        store._conn.set_trace_callback(None)
    sql = next(s for s in seen if s.lstrip().upper().startswith("SELECT"))
    return " | ".join(str(r[3]) for r in store._conn.execute("EXPLAIN QUERY PLAN " + sql))


def test_can_frames_always_drives_from_the_frame_table(tmp_path) -> None:
    # Class 20. `lines` has no index on `port`, so a filter landing on `l` reads as
    # selective and the planner drives the join from `lines` - which also discards the
    # `ORDER BY cf.line_id DESC` index order and pushes every matching frame through a temp
    # b-tree before LIMIT can apply. Measured at 1M lines over two ports: 131 ms against
    # 0.4 ms. The plan is what is pinned, not the time: the planner picks this without
    # sqlite_stat1 (the store never runs ANALYZE), so a two-row capture reproduces it and a
    # timing test on one would not.
    async def run() -> None:
        store = Store(str(tmp_path / "canplan.db"))
        await store.start()
        try:
            for i, port in enumerate(("A", "B")):
                await await_line(store, port, i)
            cases = {
                "port": {"port": "A"},
                "port+last_ms": {"port": "A", "last_ms": 5000},
                "last_ms": {"last_ms": 5000},
                "since_id": {"since_id": 0},
                "can_id": {"can_id": 0x100},
                "unfiltered": {},
            }
            for label, kwargs in cases.items():
                plan = _captured_plan(store, lambda k=kwargs: store.query_can_frames(**k))
                assert "SCAN l" not in plan, f"{label} drives from lines: {plan}"
                assert "TEMP B-TREE" not in plan, f"{label} sorts before LIMIT: {plan}"
        finally:
            await store.stop()

    asyncio.run(run())


async def await_line(store: Store, port: str, i: int) -> None:
    fut = await store.submit_line(
        ts=time.time(), port=port, dir="rx", chan="event", seq=None, raw=f"!can {i}",
        can={"tick_ms": i, "can_id": 0x100 + i, "ext": False, "rtr": False,
             "dlc": 1, "data": bytes([i])},
    )
    await fut


def test_plot_channels_port_filter_does_not_scan_lines(tmp_path) -> None:
    # Class 20, the other half. The aggregate scans plot_points either way - it counts every
    # point of every channel, which is the endpoint - but `line_id IN (SELECT id FROM lines
    # WHERE port = ?)` also scanned all of `lines` to build the id list, with a bloom filter
    # and a second temp b-tree for the GROUP BY. 190 ms against 138 ms at 1M lines.
    async def run() -> None:
        store = Store(str(tmp_path / "chanplan.db"))
        await store.start()
        try:
            fut = await store.submit_line(
                ts=time.time(), port="A", dir="rx", chan="event", seq=None, raw="!p v 1",
                plot=[{"tick_ms": 1, "sid": None, "name": "v", "value": 1.0}],
            )
            await fut
            plan = _captured_plan(store, lambda: store.query_plot_channels(port="A"))
            assert "SCAN lines" not in plan, plan
            assert "BLOOM" not in plan, plan
            # And the filter still selects: the unfiltered call is the control.
            assert store.query_plot_channels(port="B") == []
            assert [c["name"] for c in store.query_plot_channels(port="A")] == ["v"]
        finally:
            await store.stop()

    asyncio.run(run())


def _count_thread_spy(monkeypatch) -> dict[str, str]:
    seen: dict[str, str] = {}
    original = Store._count_lines_threadsafe

    def spy(self, **kwargs):
        seen["thread"] = threading.current_thread().name
        return original(self, **kwargs)

    monkeypatch.setattr(Store, "_count_lines_threadsafe", spy)
    return seen


async def test_purge_dry_run_counts_off_the_loop(tmp_path, monkeypatch) -> None:
    # The dry-run count reads the whole selected range (44 ms at 1M rows, 230 ms at 3M):
    # it belongs on the match pool with the other whole-capture reads, not on the loop.
    from httpx import ASGITransport, AsyncClient

    seen = _count_thread_spy(monkeypatch)
    app = _mk_app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        async with app.router.lifespan_context(app):
            store = app.state.store
            for i in range(3):
                await store.add_line(
                    ts=time.time(), port="", dir="rx", chan="debug", seq=None, raw=f"l{i}"
                )
            r = await client.post("/purge", json={"all": True, "dry_run": True})
    assert r.json()["deleted"] >= 3   # plus the daemon's own start/session rows
    assert seen["thread"].startswith("mcu-match")


async def test_assert_checked_lines_counts_off_the_loop(tmp_path, monkeypatch) -> None:
    # `mcu assert --expect X` defaults to --timeout 0, so this is the default invocation:
    # the count sits beside match queries that were deliberately offloaded.
    from httpx import ASGITransport, AsyncClient

    seen = _count_thread_spy(monkeypatch)
    app = _mk_app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        async with app.router.lifespan_context(app):
            store = app.state.store
            await store.add_line(
                ts=time.time(), port="", dir="rx", chan="debug", seq=None, raw="marco"
            )
            r = await client.post("/assert", json={"expect": ["marco"], "timeout_ms": 0})
    body = r.json()
    assert body["status"] == "pass" and body["checked_lines"] >= 1
    assert seen["thread"].startswith("mcu-match")


class _NoWal:
    """Connection proxy that answers the WAL pragma the way a filesystem without
    shared-memory support does: with a result row naming another mode, not an exception."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        object.__setattr__(self, "_conn", conn)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value) -> None:
        setattr(self._conn, name, value)

    def execute(self, sql, *args):
        if "journal_mode=WAL" in sql:
            return self._conn.execute("PRAGMA journal_mode=DELETE")
        return self._conn.execute(sql, *args)


def test_journal_mode_is_wal_and_a_refusal_is_reported(tmp_path, caplog) -> None:
    async def run(path: str) -> str:
        store = Store(path)
        await store.start()
        try:
            return str(store._conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        finally:
            await store.stop()

    # The readback nobody was doing: a normal capture really is in WAL.
    assert asyncio.run(run(str(tmp_path / "wal.db"))) == "wal"

    # And a refusal is named, with the mode and the path, rather than silently degrading
    # the batched-commit design to a journal per commit.
    import mcuscope.store as store_mod

    real_connect = sqlite3.connect
    caplog.clear()
    with caplog.at_level("WARNING"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                store_mod.sqlite3, "connect", lambda *a, **kw: _NoWal(real_connect(*a, **kw))
            )
            asyncio.run(run(str(tmp_path / "nowal.db")))
    warned = [r.message for r in caplog.records if "journal mode" in r.message]
    assert warned and "delete" in warned[0] and "nowal.db" in warned[0]
