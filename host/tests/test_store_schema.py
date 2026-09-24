"""The capture's schema and connections: pragmas and their read-back, indexes, migrations
of an older capture, and the capture identity."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time

import pytest

from mcuscope.store import Store
from tests.support import T0_FIXED, add_row, add_sys, run_coro


def _open_pragma(path: str, name: str):
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(f"PRAGMA {name}").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _indexes(path: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {
            str(r[0]) for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sessions'"
            )
        }
    finally:
        conn.close()


def test_a_pre_existing_capture_says_so_when_auto_vacuum_did_not_take(tmp_path, caplog) -> None:
    # RG-F1. `PRAGMA auto_vacuum=INCREMENTAL` only takes before the header is materialised,
    # so on a capture that already exists it stays 0 and every incremental_vacuum is a
    # no-op, with nothing on any surface saying so.
    path = str(tmp_path / "legacy.db")
    seed = sqlite3.connect(path)
    seed.execute("PRAGMA auto_vacuum=NONE")
    seed.execute("PRAGMA journal_mode=WAL")
    seed.execute("CREATE TABLE marker(x INTEGER)")   # materialise the header
    seed.commit()
    seed.close()
    assert _open_pragma(path, "auto_vacuum") == 0

    async def run() -> None:
        store = Store(path)
        with caplog.at_level(logging.WARNING, logger="mcuscope.store"):
            await store.start()
        await store.stop()

    asyncio.run(run())
    assert _open_pragma(path, "auto_vacuum") == 0, "the pragma cannot have taken here"
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    hit = [m for m in warnings if "auto_vacuum" in m]
    assert hit, f"no warning names auto_vacuum: {warnings}"
    # The warning has to name the consequence, not just the value, or nobody acts on it.
    assert "VACUUM" in hit[0] and "plateaus" in hit[0], hit[0]


def test_a_fresh_capture_gets_incremental_auto_vacuum_and_stays_quiet(tmp_path, caplog) -> None:
    # The other half: the warning must not fire on the normal path, or it is noise.
    path = str(tmp_path / "fresh.db")

    async def run() -> None:
        store = Store(path)
        with caplog.at_level(logging.WARNING, logger="mcuscope.store"):
            await store.start()
        await store.stop()

    asyncio.run(run())
    assert _open_pragma(path, "auto_vacuum") == 2
    assert not [r for r in caplog.records if "auto_vacuum" in r.getMessage()]






def test_an_existing_capture_gains_the_active_session_index_on_open(tmp_path) -> None:
    # RG-F5 migration half. Captures created before the index exists must gain it, and a
    # second open must not trip over it.
    path = str(tmp_path / "migrate.db")

    async def open_once() -> None:
        store = Store(path)
        await store.start()
        await store.stop()

    asyncio.run(open_once())
    conn = sqlite3.connect(path)
    conn.execute("DROP INDEX idx_sessions_active")
    conn.commit()
    conn.close()
    assert "idx_sessions_active" not in _indexes(path)

    asyncio.run(open_once())
    assert "idx_sessions_active" in _indexes(path)
    asyncio.run(open_once())          # idempotent
    assert "idx_sessions_active" in _indexes(path)


def test_a_capture_predating_the_bus_column_is_migrated(tmp_path) -> None:
    # can_frames gained `bus` with multi-bus support (SPEC 3.5). An existing capture has the
    # table without it, so CREATE TABLE IF NOT EXISTS leaves it alone and the column must come
    # by ALTER TABLE; its rows were all bus 1, which the default says.
    path = str(tmp_path / "legacy_can.db")
    seed = sqlite3.connect(path)
    seed.executescript(
        """
        CREATE TABLE lines(
          id INTEGER PRIMARY KEY, ts REAL NOT NULL, port TEXT NOT NULL,
          dir TEXT NOT NULL, chan TEXT NOT NULL, seq INTEGER, raw TEXT NOT NULL);
        CREATE TABLE can_frames(
          line_id INTEGER PRIMARY KEY REFERENCES lines(id) ON DELETE CASCADE,
          tick_ms INTEGER, can_id INTEGER NOT NULL, ext INTEGER NOT NULL DEFAULT 0,
          rtr INTEGER NOT NULL DEFAULT 0, dlc INTEGER NOT NULL, data BLOB);
        """
    )
    # Timestamps are now, or retention removes the rows before the query sees them.
    seed.execute(
        "INSERT INTO lines(id, ts, port, dir, chan, seq, raw) VALUES(1, ?, 'old', 'rx', 'event',"
        " NULL, '!can 5 - 100 AA')", (time.time(),),
    )
    seed.execute(
        "INSERT INTO can_frames(line_id, tick_ms, can_id, dlc, data) VALUES(1, 5, 256, 1, X'AA')"
    )
    seed.commit()
    seed.close()

    async def run() -> None:
        store = Store(path)
        await store.start()
        try:
            rows, _ = store.query_can_frames(limit=10)
            assert [(r["can_id"], r["bus"]) for r in rows] == [(0x100, 1)]
            await store.add_line(
                ts=time.time(), port="new", dir="rx", chan="event", seq=None,
                raw="!can2 6 - 610 BB",
                can={"tick_ms": 6, "bus": 2, "can_id": 0x610, "ext": False, "rtr": False,
                     "dlc": 1, "data": b"\xbb"},
            )
            rows, _ = store.query_can_frames(bus=2, limit=10)
            assert [(r["can_id"], r["bus"]) for r in rows] == [(0x610, 2)]
            rows, _ = store.query_can_frames(bus=1, limit=10)
            assert [r["can_id"] for r in rows] == [0x100]
        finally:
            await store.stop()

    asyncio.run(run())


def test_the_autoincrement_rebuild_keeps_every_session_index(tmp_path) -> None:
    # The rebuild DROPs the sessions table, which takes its indexes with it: only what the
    # rebuild recreates comes back, so a capture old enough to need it must not come out of
    # the migration one index short.
    path = str(tmp_path / "legacy_sessions.db")
    seed = sqlite3.connect(path)
    seed.executescript(
        "CREATE TABLE sessions("
        " id INTEGER PRIMARY KEY, name TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',"
        " started_ts REAL NOT NULL, ended_ts REAL, start_id INTEGER NOT NULL,"
        " end_id INTEGER, auto INTEGER NOT NULL DEFAULT 0);"
        "INSERT INTO sessions(id, name, started_ts, ended_ts, start_id, end_id)"
        " VALUES(1, 'old', 1.0, 2.0, 1, 2);"
    )
    seed.commit()
    seed.close()

    async def run() -> None:
        store = Store(path)
        await store.start()
        try:
            assert store.active_session() is None
            assert store.resolve_session("old")["id"] == 1, "the rebuild lost the row"
        finally:
            await store.stop()

    asyncio.run(run())
    names = _indexes(path)
    assert {"idx_sessions_name", "idx_sessions_active"} <= names, names
    conn = sqlite3.connect(path)
    try:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='sessions'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert "AUTOINCREMENT" in sql.upper(), "the rebuild did not run, so it proves nothing"


def test_the_loop_connection_carries_no_regexp_function(tmp_path) -> None:
    # CD7. The closure registered at start() was dead: every live match path builds its own
    # (its budget arms on the first call and never re-arms, so a long-lived one silently
    # expires). A direct match query on the loop connection must fail loudly now rather
    # than work once and then raise TimeoutError 30 s later.
    async def run() -> None:
        store = Store(str(tmp_path / "regexp.db"))
        await store.start()
        try:
            await add_sys(store, "hello world")
            with pytest.raises(sqlite3.OperationalError, match="(?i)regexp"):
                store.query_lines(match="hello")
            # The supported path still matches, off the loop, with its own closure.
            rows, _ = await store.query_lines_safe(match="hello")
            assert [r["raw"] for r in rows] == ["hello world"]
        finally:
            await store.stop()

    asyncio.run(run())


# -- improvement 12: the writer's page cache -------------------------------------------


def test_the_writer_connection_gets_a_page_cache(tmp_path) -> None:
    """64 MB, against SQLite's 2 MB default, on a capture that reaches hundreds of MB.

    The pragma read-back is the honest test: there is no throughput assertion that is not
    flaky. A capture must still open and commit if the value were ever refused, which the
    write below covers.
    """

    async def run() -> None:
        store = Store(str(tmp_path / "cache.db"))
        await store.start()
        try:
            assert store._conn.execute("PRAGMA cache_size").fetchone()[0] == -65536
            row = await add_row(store, T0_FIXED, "still commits")
            assert row["id"] == 1
        finally:
            await store.stop()

    run_coro(run)


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
        await add_sys(store, "captured before the upgrade")
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
                await add_sys(store, f"line {i}")
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


# -- store ----------------------------------------------------------------------------


def test_created_capture_has_incremental_autovacuum(tmp_path) -> None:
    """PRAGMA auto_vacuum must precede journal_mode=WAL, or it silently stays 0.

    With it at 0 every `PRAGMA incremental_vacuum` in the codebase is a no-op and a
    size-capped capture never hands freed pages back: one trimmed to zero rows still
    occupied 90 MiB on disk.
    """
    db = tmp_path / "cap.db"

    async def run() -> None:
        store = Store(str(db))
        await store.start()
        await store.stop()

    asyncio.run(run())
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2   # 2 == INCREMENTAL
    finally:
        conn.close()


def test_plot_points_has_line_id_index(tmp_path) -> None:
    """Without it the FK cascade full-scans plot_points on every retention chunk.

    Measured before the fix: one 5000-row chunk against 200k points took 97 s and blocked
    the event loop; with the index, 0.03 s.
    """
    db = tmp_path / "cap.db"

    async def run() -> None:
        store = Store(str(db))
        await store.start()
        await store.stop()

    asyncio.run(run())
    conn = sqlite3.connect(db)
    try:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='plot_points'"
        )}
        assert "idx_plot_line" in idx
    finally:
        conn.close()
