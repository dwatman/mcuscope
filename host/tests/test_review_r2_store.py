"""Round-2 review regressions for the store: pragma read-back, lost-line accounting,
the active-session index, and the loop connection carrying no `regexp` function.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time

import pytest

from mcuscope.store import Store, StoreError


async def _add_sys(store: Store, raw: str) -> dict:
    return await store.add_line(
        ts=time.time(), port="t", dir="-", chan="sys", seq=None, raw=raw
    )


def _captured_plan(store: Store, run, keyword: str = "SELECT") -> list[str]:
    """EXPLAIN the statement the store actually issued, not a copy of it.

    Same mechanism as test_hardening's plan checks (the modules cannot import each other
    under pytest's importlib mode): the statement comes off the connection's trace
    callback, with its parameters already substituted.
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


def test_a_line_refused_by_a_dead_writer_counts_as_a_write_error(tmp_path) -> None:
    # RG-F2. submit_line's fast-fail raised before ever reaching _fail_write, so
    # `write_errors` (documented on /status as the count of lines the capture was handed
    # and did not store) read 0 in exactly the state it exists to reveal.
    async def run() -> None:
        store = Store(str(tmp_path / "dead.db"))
        await store.start()
        try:
            await _add_sys(store, "before")
            assert store.write_errors == 0
            store._writer_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await store._writer_task
            assert not store.writer_alive

            for i in range(3):
                with pytest.raises(StoreError, match="writer is not running"):
                    await store.submit_line(
                        ts=time.time(), port="t", dir="-", chan="sys", seq=None,
                        raw=f"lost {i}",
                    )
            assert store.write_errors == 3, "lost lines are not counted"
        finally:
            await store.stop()

    asyncio.run(run())


def test_active_session_does_not_read_every_session_when_none_is_running(tmp_path) -> None:
    # RG-F5, class 20. `ended_ts IS NULL` is not sargable without an index, so the
    # ORDER BY id DESC LIMIT 1 short-circuits only while a session is running; the quiet
    # case read the whole table, on the loop, from GET /status. sessions is never trimmed.
    async def run() -> None:
        store = Store(str(tmp_path / "plan.db"))
        await store.start()
        try:
            for i in range(20):
                await store.start_session(f"run-{i}")
                await _add_sys(store, f"line {i}")
                await store.stop_session()
            assert store.active_session() is None, "the expensive case is the quiet one"
            assert not store._conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_stat1'"
            ).fetchall(), "the store must never ANALYZE; the shipped plan is the statless one"

            rows = _captured_plan(store, store.active_session)
            assert any("idx_sessions_active" in r for r in rows), \
                f"active_session does not use the partial index: {rows}"
            assert not any("TEMP B-TREE" in r for r in rows), rows

            # And it still answers correctly with one running, which is what the partial
            # index has to keep true: a row enters the index only while ended_ts is NULL.
            await store.start_session("live")
            assert store.active_session()["name"] == "live"
            rows = _captured_plan(store, store.active_session)
            assert any("idx_sessions_active" in r for r in rows), rows
        finally:
            await store.stop()

    asyncio.run(run())


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
            await _add_sys(store, "hello world")
            with pytest.raises(sqlite3.OperationalError, match="(?i)regexp"):
                store.query_lines(match="hello")
            # The supported path still matches, off the loop, with its own closure.
            rows, _ = await store.query_lines_safe(match="hello")
            assert [r["raw"] for r in rows] == ["hello world"]
        finally:
            await store.stop()

    asyncio.run(run())
