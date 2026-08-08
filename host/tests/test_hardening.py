"""Regression tests for daemon-core hardening: writer resilience, bounded queues,
integer bounds on device-controlled tokens, and outgoing-line validation.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

from mcuscope import protocol as p
from mcuscope.serial_link import (
    RX_QUEUE_MAX,
    PortError,
    SerialPort,
    _Pending,
    _response_seq,
)
from mcuscope.store import (
    _MAX_BATCH_ROWS,
    _SLOW_COMMIT_S,
    _VACUUM_PAGES,
    Store,
    StoreError,
    _reclaim_pages,
)

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


async def _fill(store: Store, n: int, payload: str, prefix: str = "") -> None:
    futs = [
        await store.submit_line(
            ts=time.time(), port="t", dir="rx", chan="debug", seq=None,
            raw=f"{prefix}{i} {payload}",
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


def test_size_cap_trims_into_protected_sessions_rather_than_being_ignored(tmp_path, caplog) -> None:
    # The forced trim: when the protected sessions ALONE exceed the cap, the size sweep has
    # a floor it cannot delete below and would otherwise return having freed nothing, on
    # every sweep, forever - a cap that is not a bound, and silent about it. This is the
    # one branch of the sweep the suite never drove.
    async def run() -> None:
        store = Store(str(tmp_path / "forced.db"))
        await store.start(retention_days=7, max_db_bytes=0, min_sessions=5)
        try:
            # One session holding everything, and fewer sessions than the floor, so
            # retention_floor_id protects every line in the capture.
            await store.start_session("the-only-run")
            await _fill(store, 4000, "x" * 200)
            assert store.retention_floor_id() is not None
            cap = store.content_bytes() // 2
            store.set_max_db_bytes(cap)

            with caplog.at_level(logging.WARNING, logger="mcuscope.store"):
                dropped = await store._sweep_size_async()
            assert dropped > 0, "the cap was silently unenforceable inside a protected session"
            # Specifically the forced branch, not an ordinary trim that happened to suffice:
            # deleting protected data is loud on purpose, and this is the evidence of it.
            assert any("protected session(s) alone exceed" in r.message for r in caplog.records)
            assert store.content_bytes() <= cap
            # The newest lines are still the ones kept, protected or not.
            rows, _ = store.query_lines(limit=1, order="desc")
            assert rows[0]["raw"].startswith("3999 ")
            # And it converges rather than eating the rest of the session on the next pass.
            assert await store._sweep_size_async() == 0
        finally:
            await store.stop()

    asyncio.run(run())


def test_size_cap_spends_unprotected_lines_first_and_forces_only_the_remainder(
    tmp_path, caplog
) -> None:
    # The ordering half of the floor, and the arithmetic of the forced pass. The sibling
    # above has nothing unprotected, so it cannot see either: here the cap needs more than
    # the ambient lines can pay, so the first pass spends all of them, stops at the floor,
    # and the forced pass takes only the SHORTFALL out of the protected run. Trimming
    # `want` again there would silently eat a second helping of protected data.
    #
    # (Replaces a test in test_sessions.py that drove this with `want` below the ambient
    # count, where the floor changed nothing and removing it left the test green.)
    async def run() -> None:
        store = Store(str(tmp_path / "ordering.db"))
        await store.start(retention_days=7, max_db_bytes=0, min_sessions=1)
        try:
            await _fill(store, 500, "x" * 200, prefix="ambient")
            await store.start_session("keep-me")
            protected_from = store.max_id()
            await _fill(store, 1500, "x" * 200, prefix="protected")
            assert store.retention_floor_id() is not None

            used = store.content_bytes()
            store.set_max_db_bytes(used // 2)   # more than the 500 ambient lines can pay
            with caplog.at_level(logging.WARNING, logger="mcuscope.store"):
                assert await store._sweep_size_async() > 0

            rows, _ = store.query_lines(limit=1000, order="asc")
            assert not any(r["raw"].startswith("ambient") for r in rows), \
                "unprotected lines must be spent before protected ones"
            # That the cap is a hard bound is the sibling's invariant; this one is about
            # which lines pay for it, so it only asks that the trim moved towards the cap
            # (one pass leaves partly-filled pages behind, which is why it is not `<=`).
            assert store.content_bytes() < used
            assert any("protected session(s) alone exceed" in r.message for r in caplog.records)
            # The shortfall only: over half the protected run survives a cap that asked for
            # roughly a quarter of the capture beyond what the ambient lines covered.
            surviving = store.count_lines(id_from=protected_from)
            assert surviving > 750, f"the forced pass overshot the target: {surviving} of 1500"
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


def test_can_frames_filters_each_select_what_they_name(tmp_path) -> None:
    # Every /can/frames filter but `can_id` shipped with no test at all: the coverage leg
    # listed port, since_id and last_ms together as untested shipped paths. A wrong filter
    # here is invisible from the outside - it returns frames, just not the right ones.
    async def run() -> None:
        store = Store(str(tmp_path / "canfilt.db"))
        await store.start()
        try:
            ids: list[int] = []
            now = time.time()
            for port, can_id, ts in (("boardA", 0x100, now - 3600),
                                     ("boardB", 0x200, now),
                                     ("boardA", 0x300, now)):
                row = await store.add_line(
                    ts=ts, port=port, dir="rx", chan="event", seq=None,
                    raw=f"!can 1 - {can_id:X} AA",
                    can={"tick_ms": 1, "can_id": can_id, "ext": False, "rtr": False,
                         "dlc": 1, "data": b"\xaa"},
                )
                ids.append(row["id"])

            def frames(**kwargs) -> list[int]:
                rows, _ = store.query_can_frames(**kwargs)
                return [f["can_id"] for f in rows]

            assert frames() == [0x300, 0x200, 0x100]              # newest first
            assert frames(port="boardA") == [0x300, 0x100]
            assert frames(port="boardB") == [0x200]
            assert frames(port="nosuchport") == []
            assert frames(since_id=ids[0]) == [0x300, 0x200]      # strictly after that line
            assert frames(since_id=ids[2]) == []
            assert frames(last_ms=60_000) == [0x300, 0x200]       # the hour-old frame is out
            assert frames(can_id=0x300) == [0x300]
            assert frames(id_from=ids[1], id_to=ids[1]) == [0x200]
            # Combined, the filters intersect rather than replace one another.
            assert frames(port="boardA", last_ms=60_000) == [0x300]
            assert frames(port="boardB", since_id=ids[2]) == []
            # And the truncation flag tracks the limit, not the filter.
            rows, truncated = store.query_can_frames(limit=2)
            assert len(rows) == 2 and truncated is True
            rows, truncated = store.query_can_frames(port="boardB", limit=2)
            assert len(rows) == 1 and truncated is False
        finally:
            await store.stop()

    asyncio.run(run())


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
        # The refusal is asserted on its outcome, never inside a `try` an `except` can
        # reach: `AssertionError` IS an `Exception`, so the earlier `try/except Exception`
        # form swallowed its own failure signal and passed with WebSockets dropped from the
        # guard entirely. The close code is asserted too, which pins the branch of _deny
        # that separates "no token" (1008) from "locked out" (1013).
        with pytest.raises(WebSocketDisconnect) as refused:
            with c.websocket_connect("/ws", headers={"host": "127.0.0.1"}):
                pass
        assert refused.value.code == 1008


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


def test_token_failure_table_stays_bounded_under_a_spray() -> None:
    """The bound is the point: expiry alone does not deliver it.

    An attacker spraying from many source addresses keeps every record inside its window,
    so nothing is ever eligible for expiry and the table grows without limit. Driven at the
    guard rather than over HTTP because the defect only appears past a thousand *distinct*
    addresses, which no request-level test would reach.
    """
    from mcuscope.server import TOKEN_FAIL_MAX, TOKEN_FAIL_TABLE_MAX, _TokenGuard

    guard = _TokenGuard(app=None, token="sesame-open-123")
    now = time.monotonic()
    for i in range(TOKEN_FAIL_TABLE_MAX * 3):
        guard._register_failure(f"10.0.{i // 256}.{i % 256}", now)   # all within one window
    assert len(guard._fails) <= TOKEN_FAIL_TABLE_MAX

    # Eviction is oldest-first, so the addresses still being tried are the ones kept.
    assert f"10.0.{(TOKEN_FAIL_TABLE_MAX * 3 - 1) // 256}.{(TOKEN_FAIL_TABLE_MAX * 3 - 1) % 256}" \
        in guard._fails
    # And a live lockout still bites: bounding the table must not cost the guard its job.
    for _ in range(TOKEN_FAIL_MAX):
        guard._register_failure("192.0.2.7", now)
    assert guard._locked_out("192.0.2.7", now)


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
            # The opening frame carries the capture identity and nothing else, because
            # this store is idle: a keepalive is how a silent target tells a reconnected
            # client its id space was replaced.
            first = ws.receive_json()
            assert [k for r in first for k in r] == ["capture"], first
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


def _captured_plan(store: Store, run, keyword: str = "SELECT") -> list[str]:
    """EXPLAIN the statement the store actually issued, rather than a copy of it.

    A plan test that explains a hand-written query proves nothing about the daemon (this
    round's test-quality leg found exactly that shape), so the statement is taken off the
    connection's trace callback, which reports it with its parameters already substituted.

    Returned as the list of plan rows, outer loop first, because the useful assertion is
    "which table does the outer loop read" - and asserting that positively survives SQLite
    rewording its output. Asserting the *absence* of "SCAN l" would pass silently on a
    build that says "SCAN TABLE lines AS l" instead, which is how it read before 3.36.

    The LAST matching statement, not the first: a read carrying `last_ms` resolves its
    window bounds with anchor SELECTs first (`_window_floor`, `_window_id_floor`), and
    explaining one of those pins nothing about the query under test. `keyword` chooses the
    statement kind, so the retention sweep's DELETE can be pinned the same way.
    """
    seen: list[str] = []
    store._conn.set_trace_callback(seen.append)
    try:
        run()
    finally:
        store._conn.set_trace_callback(None)
    matching = [s for s in seen if s.lstrip().upper().startswith(keyword)]
    assert matching, f"the store issued no {keyword} statement: {seen}"
    return [str(r[3]) for r in store._conn.execute("EXPLAIN QUERY PLAN " + matching[-1])]


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
                rows = _captured_plan(store, lambda k=kwargs: store.query_can_frames(**k))
                # The outer loop must read can_frames. Every phrasing SQLite has used names
                # the alias there ("SCAN cf", "SCAN TABLE can_frames AS cf"), and the
                # lines-driven plan names only `l`, so this discriminates on any build.
                assert " cf" in rows[0], f"{label} does not drive from can_frames: {rows}"
                assert not any("TEMP B-TREE" in r for r in rows), \
                    f"{label} sorts every match before LIMIT: {rows}"
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
            rows = _captured_plan(store, lambda: store.query_plot_channels(port="A"))
            # Positive form, as above: `lines` must be reached by primary-key probe, never
            # scanned to build an id list. Both halves of the old plan are named.
            assert any("SEARCH li" in r and "PRIMARY KEY" in r for r in rows), rows
            assert not any("BLOOM" in r for r in rows), rows
            # And the filter still selects: the unfiltered call is the control.
            assert store.query_plot_channels(port="B") == []
            assert [c["name"] for c in store.query_plot_channels(port="A")] == ["v"]
        finally:
            await store.stop()

    asyncio.run(run())


def _count_thread_spy(monkeypatch) -> dict[str, str]:
    # Spies on the read itself rather than on whatever offloads it, so this keeps
    # asserting the property that matters (it did not run on the loop thread) instead of
    # the mechanism that delivers it.
    seen: dict[str, str] = {}
    original = Store.count_lines

    def spy(self, **kwargs):
        seen["thread"] = threading.current_thread().name
        return original(self, **kwargs)

    monkeypatch.setattr(Store, "count_lines", spy)
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


def test_lines_port_filter_seeks_rather_than_scans(tmp_path) -> None:
    # Class 20. `port` had no index of its own, so `/lines?port=` with no `chan` planned as
    # a scan of the whole table btree, and query_lines_safe runs it inline on the event loop
    # because only a `match`-bearing query is offloaded. A busy port hides it (the LIMIT
    # fills from the newest rows); a quiet one pays it in full, which is the case that
    # matters, because a board silent while idle still gets polled. Measured at 1M rows with
    # no ANALYZE: 0.3 ms busy against 80 ms quiet, linear from there.
    #
    # The plan is pinned rather than the time: the planner chooses this with no sqlite_stat1
    # (the store never runs ANALYZE, so that is the shipped condition), which a two-row
    # capture reproduces and a timing test would need bulk data to see. Asserted positively
    # - the index is named in the plan - because asserting the absence of "SCAN" passes on
    # any SQLite that words its output differently.
    async def run() -> None:
        store = Store(str(tmp_path / "portplan.db"))
        await store.start()
        try:
            for i, port in enumerate(("busy", "quiet")):
                fut = await store.submit_line(
                    ts=time.time(), port=port, dir="rx", chan="debug", seq=None, raw=f"l{i}"
                )
                await fut
            assert not store._conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_stat1'"
            ).fetchall(), "the store must never ANALYZE; the shipped plan is the statless one"

            rows = _captured_plan(store, lambda: store.query_lines(port="quiet", limit=200))
            assert any("idx_lines_port_id" in r for r in rows), \
                f"/lines?port= does not seek on the port index: {rows}"
            assert not any("TEMP B-TREE" in r for r in rows), \
                f"/lines?port= sorts every match before LIMIT: {rows}"

            # And the combination, which the first version of this test did not cover and
            # the fix-diff leg caught: with both columns indexed and no stats, the planner
            # took the port index and discarded the chan seek. `chan` is the selective side
            # (one board, many channels), measured at 319 ms against 0.09 ms on the loop.
            rows = _captured_plan(
                store, lambda: store.query_lines(port="quiet", chans=["marker"], limit=200)
            )
            assert any("idx_lines_chan_id" in r for r in rows), \
                f"/lines?port=&chan= does not seek on the chan index: {rows}"

            # count_lines takes the same pair through the same assembler and was the one
            # caller that did not ask for the de-optimisation, so it kept the defect after
            # query_lines was fixed: 95 ms against 0.04 ms at 300k rows on the match pool.
            # The class is closed here by pinning every combination, not just the pair.
            for label, kwargs, wanted in (
                ("port", {"port": "quiet"}, "idx_lines_port_id"),
                ("chan", {"chans": ["marker"]}, "idx_lines_chan_id"),
                ("port+chan", {"port": "quiet", "chans": ["marker"]}, "idx_lines_chan_id"),
                ("last_ms", {"last_ms": 5000}, "idx_lines_ts"),
            ):
                rows = _captured_plan(store, lambda k=kwargs: store.count_lines(**k))
                assert any(wanted in r for r in rows), \
                    f"count_lines {label} does not seek on {wanted}: {rows}"
        finally:
            await store.stop()

    asyncio.run(run())


def test_a_last_ms_window_seeks_by_id_rather_than_reading_the_table(tmp_path) -> None:
    # Class 20. `ts >= ?` alone is not sargable for a query ordered by id: SQLite reads the
    # table btree backwards and stops early only when the window really holds `limit+1`
    # rows, so a QUIET window reads the whole table - 46 ms against 0.6 ms at 300k rows,
    # inline on the event loop, the same busy/quiet asymmetry idx_lines_port_id was added
    # for. Resolving the window's floor to an id gives every reader a primary-key range.
    #
    # Both windows are pinned because only the empty one exposed it, and both are driven
    # through the reader rather than a hand-written query: the anchor SELECT that resolves
    # the floor is part of what is being asserted.
    async def run() -> None:
        store = Store(str(tmp_path / "windowplan.db"))
        await store.start()
        try:
            for i, port in enumerate(("busy", "quiet")):
                fut = await store.submit_line(
                    ts=time.time(), port=port, dir="rx", chan="debug", seq=None, raw=f"l{i}"
                )
                await fut
            assert not store._conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_stat1'"
            ).fetchall(), "the store must never ANALYZE; the shipped plan is the statless one"

            def plan_and_rows(label: str) -> None:
                plan = _captured_plan(
                    store, lambda: store.query_lines(last_ms=60_000, limit=200)
                )
                assert any("PRIMARY KEY" in r and "rowid>" in r for r in plan), \
                    f"/lines?last_ms= ({label}) reads the table rather than a range: {plan}"

            plan_and_rows("busy")
            assert len(store.query_lines(last_ms=60_000, limit=200)[0]) >= 2

            # The empty window is the expensive one, and the bound has to survive having
            # nothing to point at: one past the newest id, not no bound at all.
            store._conn.execute("UPDATE lines SET ts = ts - 999999")
            store._conn.commit()
            plan_and_rows("quiet")
            assert store.query_lines(last_ms=60_000, limit=200)[0] == []
            # And that bound is past the newest id, not `>= 1`: the plan reads the same
            # either way, while a floor of 1 leaves the whole table inside the range.
            assert store._window_id_floor(time.time()) == store.max_id() + 1
        finally:
            await store.stop()

    asyncio.run(run())


def test_the_age_sweep_does_not_read_the_table_when_nothing_has_expired(tmp_path) -> None:
    # Class 20, on the one statement in the store that deletes by age. `ORDER BY id` made
    # the planner take the table btree and read every `raw` blob; the LIMIT cuts that short
    # only when rows really are expired, and nothing expired is the steady state of a
    # capture inside its retention window. That case scanned the whole table on the loop
    # every hourly sweep: 45 ms at 300k rows, ~0.4 s at 1M, uninterruptible.
    #
    # Both variants are pinned - the floored delete carries an extra `id < ?` term and had
    # the same plan - and the DELETE itself is explained, not its subselect.
    async def run() -> None:
        store = Store(str(tmp_path / "sweepplan.db"))
        await store.start()
        try:
            for i in range(2):
                await _add_sys(store, f"ambient {i}")
            await store.start_session("protected")
            await _add_sys(store, "inside the run")
            assert not store._conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_stat1'"
            ).fetchall(), "the store must never ANALYZE; the shipped plan is the statless one"
            cutoff = time.time() - 86400
            floor_id = store.retention_floor_id()

            for label, floor in (("no floor", None), ("floored", floor_id or 1)):
                rows = _captured_plan(
                    store,
                    lambda f=floor: store._delete_expired_chunk(cutoff, 5000, f),
                    keyword="DELETE",
                )
                assert any("idx_lines_ts" in r for r in rows), \
                    f"the age sweep ({label}) does not seek expired rows by ts: {rows}"
                assert not any("TEMP B-TREE" in r for r in rows), \
                    f"the age sweep ({label}) sorts the whole expired set: {rows}"

            # Still deletes oldest-first, and still stops at the floor: the plan is only
            # worth pinning if the delete it belongs to is right.
            store.set_min_sessions(1)
            store._retention_days = 0
            store._conn.execute("UPDATE lines SET ts = ts - 999999")
            store._conn.commit()
            assert await store._sweep_retention_async() > 0
            rows, _ = store.query_lines(limit=1000, order="asc")
            assert rows and min(r["id"] for r in rows) >= store.retention_floor_id()
        finally:
            await store.stop()

    asyncio.run(run())


def test_size_trim_actually_returns_pages_to_the_filesystem(tmp_path) -> None:
    # Class 17. `conn.execute("PRAGMA incremental_vacuum")` reclaims exactly one page: the
    # pragma yields a row per freed page and sqlite3 steps it only as rows are consumed, so
    # an unconsumed execute() advances it once. The cap trimmed rows correctly and handed
    # back ~0.02% of the space, with nothing reporting it - the same request-versus-result
    # shape as the auto_vacuum defect this mechanism exists to fix.
    #
    # Asserted on the freelist, which is what "gave the space back" means, rather than on
    # the pragma being issued: the broken version issued it too.
    async def run() -> None:
        db = tmp_path / "vac.db"
        store = Store(str(db))
        await store.start()
        try:
            assert store._conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2, \
                "the capture must be INCREMENTAL, or this reclaims nothing either way"
            row = "x" * 400
            for i in range(4000):
                fut = await store.submit_line(
                    ts=time.time(), port="p", dir="rx", chan="debug", seq=None,
                    raw=f"{i} {row}",
                )
            await fut
            max_id = store.max_id()
            await store.delete_range(1, max_id)
            free = store._conn.execute("PRAGMA freelist_count").fetchone()[0]
            # Bounded per call, so a large backlog drains over several sweeps; what must not
            # happen is the one-page-per-call behaviour, which leaves nearly all of it.
            assert free < 100, f"the trim left {free} free pages; the vacuum did not step"
        finally:
            await store.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ("/lines?chan=nope&limit=5", "chan"),
        # `order` was `"DESC" if order == "desc" else "ASC"`, so any other value silently
        # returned the rows in the opposite order to the one asked for, which is worse than
        # an empty result: the caller gets data and it is wrong.
        ("/lines?order=bogus&limit=5", "order"),
    ],
)
def test_closed_query_domains_are_refused_too(stack, query, field) -> None:
    """The same defect as the body params below, on the query string (C1, class-wide).

    Found by re-running C1's sweep after claiming it closed: two of four sites had been
    fixed. Every wire-facing string parameter on every handler was then enumerated, and
    these are the only two with a closed documented domain and no declaration.
    """
    r = httpx.get(stack.base_url + query, timeout=15)
    assert r.status_code == 422, f"{field} was accepted: {r.status_code} {r.text[:200]}"
    assert field in r.text, f"the refusal does not name the offending field: {r.text[:200]}"


@pytest.mark.parametrize(
    ("path", "body", "field"),
    [
        ("/wait", {"match": "x", "chan": "nope", "timeout_ms": 100}, "chan"),
        ("/wait", {"match": "x", "send": "ping", "send_mode": "bogus", "timeout_ms": 100},
         "send_mode"),
        ("/assert", {"expect": ["x"], "chan": "nope", "timeout_ms": 100}, "chan"),
        ("/assert", {"expect": ["x"], "send": "ping", "send_mode": "bogus", "timeout_ms": 100},
         "send_mode"),
    ],
)
def test_closed_request_domains_are_refused_not_silently_reinterpreted(
    stack, path, body, field
) -> None:
    """A value outside a closed domain must fail the request, not do something else quietly.

    Found by the coverage leg reading uncovered lines as untested *request parameters*.
    `send_mode` was only ever compared `== "raw"`, so any other value silently sent as a
    command instead; `chan` was matched by equality against stored rows, so an unknown one
    never matched and the caller waited out its whole timeout to be told "no match" rather
    than "no such channel". Both answered 200 with a plausible negative, which for the agent
    that is this API's primary consumer is worse than an error.
    """
    r = httpx.post(stack.base_url + path, json=body, timeout=15)
    assert r.status_code == 422, f"{field} was accepted: {r.status_code} {r.text[:200]}"
    assert field in r.text, f"the refusal does not name the offending field: {r.text[:200]}"


def test_status_reports_the_size_the_cap_is_enforced_against(stack) -> None:
    """A working cap must not read as a broken one.

    /status paired `db_size_bytes` (file + WAL) with `db_max_bytes`, but the trim is
    enforced against live content, which excludes the freelist. Measured during the round:
    db_size_bytes 24130888 beside db_max_bytes 2097152 with lines_trimmed 0, while the
    enforced figure sat at 2.0 MB throughout. Both are reported now, and the one the cap
    uses is named in SPEC.
    """
    body = httpx.get(stack.base_url + "/status", timeout=15).json()
    assert "db_content_bytes" in body, "the enforced size is not reported at all"
    assert isinstance(body["db_content_bytes"], int)
    # Strictly less, not `<=`: the defect this pins is reporting the file size as the
    # enforced one, which makes the two EQUAL and passes `<=`. They are always separated on
    # a real capture - the freelist aside, db_size_bytes counts the -wal sidecar too.
    assert 0 < body["db_content_bytes"] < body["db_size_bytes"]


def test_export_bound_by_id_to_reanchors_its_last_ms_window(tmp_path) -> None:
    """A paused surface exports what it shows, window and all (finding M5).

    `/plot/export` resolved `last_ms` against *now*, so a chart paused on a transient
    exported a window that did not contain it, under a button whose tooltip says "the
    current window". `id_to` is the client's frozen line-id watermark; with one in force the
    window must end there too, because intersecting a frozen id range with a now-anchored
    window returns almost nothing.

    Asserted on the exported rows, not on the request: a test that checks the URL carries
    `id_to` is satisfied by wiring that filters nothing.
    """
    async def run() -> None:
        store = Store(":memory:")  # so the export streams on the loop connection the trace sees
        await store.start()
        try:
            now = time.time()
            # ids 1..10, one second apart, oldest first. id 5 is the "transient".
            for i in range(1, 11):
                fut = await store.submit_line(
                    ts=now - (10 - i), port="p", dir="rx", chan="event", seq=None,
                    raw=f"!p {i} v={i}",
                    plot=[{"tick_ms": i, "sid": None, "name": "v", "value": float(i)}],
                )
            await fut

            def ids(**kw):
                return [r["line_id"] for r in store.iter_plot_export(names=["v"], **kw)]

            # The freeze alone: everything up to and including the bound.
            assert ids(id_to=6) == [1, 2, 3, 4, 5, 6]
            # The freeze plus a window: 3.5 s ending at id 6, so 3..6 and never 7..10. The
            # half second keeps the floor off a row's exact ts, or the elapsed time between
            # seeding and querying would decide whether the boundary row is in (class 21).
            windowed = ids(id_to=6, last_ms=3500)
            assert windowed == [3, 4, 5, 6], windowed
            assert 5 in windowed, "the transient the surface was paused on is not in the export"
            assert not any(i > 6 for i in windowed), "rows past the freeze were exported"
            # Unbounded, the window is still measured from now, so the freeze changes nothing
            # for a live surface.
            assert ids(last_ms=3500) == [7, 8, 9, 10]
            assert ids() == list(range(1, 11))

            # /plot/series takes the same pair, and it is a separate call site of the
            # anchor (the coverage leg found this one uncovered while every other was hit).
            assert [p["line_id"] for p in store.query_plot_series(name="v", last_ms=3500)] \
                == [7, 8, 9, 10]
            assert [p["line_id"] for p in
                    store.query_plot_series(name="v", last_ms=3500, id_to=6)] == [3, 4, 5, 6]

            # Class 20: the bound must extend the existing index seek, not sit beside a
            # scan, and the anchor lookup must be a single primary-key seek. Asserted
            # positively (the index is named), because asserting the absence of "SCAN"
            # passes on any SQLite that words its plan differently.
            seen: list[str] = []
            store._conn.set_trace_callback(seen.append)
            list(store.iter_plot_export(names=["v"], id_to=6, last_ms=3500))
            store._conn.set_trace_callback(None)
            plans = [
                [str(r[3]) for r in store._conn.execute("EXPLAIN QUERY PLAN " + q)]
                for q in seen if q.lstrip().upper().startswith("SELECT")
            ]
            anchor = [p for p in plans if len(p) == 1 and "lines" in p[0]]
            assert anchor and "PRIMARY KEY" in anchor[0][0], f"anchor lookup is not a seek: {plans}"
            export = [p for p in plans if any("idx_plot_name_line" in r for r in p)]
            assert export, f"the export lost its index seek: {plans}"
            assert any("line_id<" in r or "line_id <" in r
                       for p in export for r in p), \
                f"id_to did not become part of the seek: {export}"
        finally:
            await store.stop()

    asyncio.run(run())


def test_a_slow_subscriber_is_told_it_missed_rows(tmp_path) -> None:
    """The feed sheds the oldest row rather than blocking the writer, and said nothing.

    Measured during the round with a raw socket that stopped reading for 60 s: 36.7% of the
    span never arrived, while /status held connected=true, rx_dropped=0, write_errors=0. The
    web UI builds its plots from this stream, so the chart simply had holes. An id gap cannot
    be inferred client-side either, because `port=` filtering makes gaps legitimate.
    """
    async def run() -> None:
        store = Store(":memory:")
        await store.start()
        try:
            q = store.subscribe(maxsize=4)
            for i in range(1, 11):        # 10 rows into a queue that holds 4
                store._broadcast({"id": i, "port": "p", "raw": f"r{i}"})
            assert q.qsize() == 4
            dropped = store.take_dropped(q)
            assert dropped == 6, f"shed 6 rows, reported {dropped}"
            assert store.ws_dropped == 6, "the lifetime total on /status did not move"
            # Taking the count clears it, so the next frame does not re-announce the gap.
            assert store.take_dropped(q) == 0
            # And an unsubscribe does not leave the accounting behind.
            store.unsubscribe(q)
            assert q not in store._sub_dropped
        finally:
            await store.stop()

    asyncio.run(run())


def test_the_page_reclaim_stays_bounded_per_call(tmp_path) -> None:
    """The reclaim is bounded because both callers run on the event loop.

    Its sibling test asserts the freelist drains, which is the defect that was fixed (an
    unfetched `PRAGMA incremental_vacuum` reclaims exactly one page). That test passes just
    as well against an *unbounded* reclaim, which would be O(freelist) on the loop: a
    capture that has plateaued has a large one. This pins the bound itself, per the rule
    that a fix a measurement justified leaves a check on the mechanism rather than on how
    long it took.
    """
    async def run() -> None:
        store = Store(str(tmp_path / "bound.db"))
        await store.start()
        try:
            # Inserted straight onto the connection rather than through the write queue:
            # this pins _reclaim_pages, not the ingest path, and the freelist has to be
            # several times _VACUUM_PAGES for the assertion to tell bounded from unbounded.
            now = time.time()
            store._conn.executemany(
                "INSERT INTO lines(ts, port, chan, dir, raw) VALUES(?,?,?,?,?)",
                [(now, "p", "debug", "rx", "x" * 400) for _ in range(_VACUUM_PAGES * 20)],
            )
            store._conn.execute("DELETE FROM lines")
            store._conn.commit()

            def freelist() -> int:
                return store._conn.execute("PRAGMA freelist_count").fetchone()[0]

            before = freelist()
            assert before > _VACUUM_PAGES * 1.5, \
                f"only {before} free pages; the fixture cannot tell bounded from unbounded"
            _reclaim_pages(store._conn)
            after = freelist()
            assert before - after == _VACUUM_PAGES, \
                f"one call reclaimed {before - after} pages, not {_VACUUM_PAGES}"
            # And it still makes progress across calls, so a backlog drains over sweeps.
            _reclaim_pages(store._conn)
            assert freelist() == after - _VACUUM_PAGES
        finally:
            await store.stop()

    asyncio.run(run())


def test_the_reclaim_does_not_lean_on_execute_stepping_the_pragma(tmp_path) -> None:
    """The reclaim must step the pragma itself, not through a cursor's row consumption.

    On Python 3.11 `PRAGMA incremental_vacuum(N)` yields no rows at all, so
    execute(...).fetchall() steps it exactly once and reclaims one page - the same
    one-page reclaim the fetchall was added to fix, now silent on 3.11 only. Both sibling
    tests above pass on 3.12+ and failed the 3.11 CI legs, so this leg emulates the 3.11
    driver on whatever version runs it: an execute() of the pragma reclaims a single page.
    """
    db = tmp_path / "step.db"

    class OneStepPerExecute(sqlite3.Connection):
        """execute() advances the pragma one page, whatever N says, as sqlite3 3.11 does."""

        def execute(self, sql, *args):
            if "incremental_vacuum" in sql.lower():
                sql = "PRAGMA incremental_vacuum(1)"
            return super().execute(sql, *args)

    conn = sqlite3.connect(db, factory=OneStepPerExecute)
    try:
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")   # before WAL: see test_regressions
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE lines(raw TEXT)")
        conn.executemany(
            "INSERT INTO lines(raw) VALUES(?)", [("x" * 400,) for _ in range(_VACUUM_PAGES * 20)]
        )
        conn.execute("DELETE FROM lines")
        conn.commit()

        def freelist() -> int:
            return conn.execute("PRAGMA freelist_count").fetchone()[0]

        before = freelist()
        assert before > _VACUUM_PAGES * 1.5, \
            f"only {before} free pages; the fixture cannot tell one page from {_VACUUM_PAGES}"
        _reclaim_pages(conn)
        assert before - freelist() == _VACUUM_PAGES, \
            f"one call reclaimed {before - freelist()} pages, not {_VACUUM_PAGES}"
    finally:
        conn.close()


def test_a_wait_that_lost_rows_says_so_instead_of_reporting_timeout(stack, monkeypatch) -> None:
    """A wait whose feed shed rows has not seen the window it reports on.

    The regex runs in an executor, so the writer keeps broadcasting during that await; a
    burst past the subscriber queue drops the oldest, which can be the line being waited
    for. Driven before the fix: the needle was broadcast, 48 rows were shed, and /wait
    answered a clean {"status": "timeout"} that `mcu wait` turns into exit 2. A false
    negative on an assertion API is worse than a slow one.
    """
    import threading

    store = stack.app.state.store
    original = Store.subscribe
    # A 4-row queue stands in for the 2000-row one overrun during a slow match; the defect
    # is the silence, not the size.
    monkeypatch.setattr(Store, "subscribe", lambda self, pf=None, maxsize=4: original(self, pf, 4))

    def flood() -> None:
        time.sleep(0.3)
        for i in range(50):
            store._broadcast({"id": 900_000 + i, "port": stack.alias, "chan": "debug",
                              "raw": "NEEDLE" if i == 0 else f"noise{i}"})

    threading.Thread(target=flood, daemon=True).start()
    res = httpx.post(stack.base_url + "/wait",
                     json={"match": "NEEDLE", "timeout_ms": 1500}, timeout=20).json()
    assert res["dropped"] > 0, (
        "rows were shed and the wait reported nothing: a timeout from this run is "
        f"indistinguishable from a real negative ({res})"
    )


# -- the capture identity (SPEC 3.4) ----------------------------------------------------


def test_capture_id_is_stable_across_a_restart_and_unique_per_capture(tmp_path) -> None:
    # The identity belongs to the database, not to the daemon process. If a restart minted
    # a new one, every reconnecting client would throw away a scrollback it can still use
    # and re-seed - and a genuine reset would be indistinguishable from an ordinary blip.
    async def run() -> tuple[str, str, str]:
        store = Store(str(tmp_path / "a.db"))
        await store.start()
        first = store.capture_id
        await store.stop()

        again = Store(str(tmp_path / "a.db"))
        await again.start()
        second = again.capture_id
        await again.stop()

        other = Store(str(tmp_path / "b.db"))
        await other.start()
        third = other.capture_id
        await other.stop()
        return first, second, third

    first, second, third = asyncio.run(run())
    assert first and second == first, "a restart against the same capture changed its identity"
    assert third != first, "a different capture handed out the same identity"


def test_a_capture_predating_the_meta_table_is_given_an_identity(tmp_path) -> None:
    # The upgrade path every existing capture takes exactly once. Without it the daemon
    # would answer `capture` as an empty string forever, and a client comparing tokens
    # would never see a reset - the failure the token exists to prevent, made permanent.
    async def run() -> str:
        store = Store(str(tmp_path / "old.db"))
        await store.start()
        await _add_sys(store, "captured before the upgrade")
        await store.stop()

        conn = sqlite3.connect(str(tmp_path / "old.db"))
        conn.execute("DROP TABLE meta")     # a capture written by the previous release
        conn.commit()
        conn.close()

        again = Store(str(tmp_path / "old.db"))
        await again.start()
        got = again.capture_id
        assert again.max_id() == 1, "the upgrade cost the capture its rows"
        await again.stop()
        return got

    assert asyncio.run(run()), "an upgraded capture got no identity"


def test_deleting_the_highest_id_mints_a_new_capture(tmp_path) -> None:
    # `lines.id` is a plain rowid: delete the highest one and SQLite hands it out again to
    # the next line captured. From then on the ids a client holds name different rows, and
    # its dedup watermark discards the whole continuation as duplicates. That is the one
    # thing a client cannot infer, so the daemon has to say it.
    async def run() -> None:
        store = Store(str(tmp_path / "p.db"))
        await store.start()
        try:
            for i in range(6):
                await _add_sys(store, f"line {i}")
            top = store.max_id()
            start = store.capture_id

            # Trimming the oldest end - what retention and the size cap do - leaves the
            # maximum alone, so the ids in flight keep their meaning and nothing resets.
            assert await store.delete_range(1, 2) == 2
            assert store.capture_id == start, "trimming the oldest end reset the capture"

            assert await store.delete_range(top, top) == 1
            assert store.capture_id != start, "the highest id was freed with no reset"

            # And the new identity is the one a restart reads back, or a client that
            # reconnects after the purge would be told the pre-purge story.
            after = store.capture_id
        finally:
            await store.stop()

        again = Store(str(tmp_path / "p.db"))
        await again.start()
        assert again.capture_id == after, "the new identity did not survive to the next run"
        await again.stop()

    asyncio.run(run())


def test_status_reports_the_capture_identity(tmp_path) -> None:
    from fastapi.testclient import TestClient

    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        body = c.get("/status").json()
        assert isinstance(body.get("capture"), str) and body["capture"]
