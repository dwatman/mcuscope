"""Another process holding the capture's write lock (RES-1, REVIEW classes 1 and N13): the
writer keeps its batch and retries, the loop stays free, and the episode is recorded."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time

import pytest

from mcuscope.store import Store


async def test_a_held_write_lock_holds_the_batch_and_records_the_episode(tmp_path, caplog) -> None:
    db = str(tmp_path / "capture.db")
    store = Store(db)
    await store.start()
    other = sqlite3.connect(db, isolation_level=None)
    try:
        # The bound on any one wait the writer makes on the loop.
        assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5
        other.execute("BEGIN IMMEDIATE")
        with caplog.at_level(logging.WARNING, logger="mcuscope.store"):
            futs = [await store.submit_line(ts=time.time(), port="b", dir="rx",
                                            chan="debug", seq=None, raw=f"held {i}")
                    for i in range(50)]
            ticks = 0
            while store.db_locked_since is None and ticks < 500:
                await asyncio.sleep(0.01)
                ticks += 1
            assert store.db_locked_since is not None
            # The loop is free while the lock is held: these sleeps return.
            for _ in range(5):
                await asyncio.sleep(0.02)
            assert not any(f.done() for f in futs), "a row committed through a held lock"
            other.execute("ROLLBACK")
            rows = [await f for f in futs]
        assert [r["raw"] for r in rows] == [f"held {i}" for i in range(50)]
        assert store.write_errors == 0 and store.db_locked_since is None
        assert not any("row by row" in r.getMessage() for r in caplog.records)
        locked = [r.getMessage() for r in caplog.records if "locked by another process" in
                  r.getMessage()]
        assert len(locked) == 1, locked     # logged once per episode, not per retry
        sys_rows = [r["raw"] for r in store.query_lines(chans=["sys"], limit=10)[0]]
        notice = [r for r in sys_rows if "capture database locked by another process for" in r]
        assert len(notice) == 1 and "writes were held meanwhile" in notice[0]
        # Ids stayed dense: the failed attempts handed none out.
        assert [r["id"] for r in rows] == list(range(rows[0]["id"], rows[0]["id"] + 50))
    finally:
        other.close()
        await store.stop()


async def test_a_writer_stopped_while_locked_fails_the_held_batch(tmp_path) -> None:
    db = str(tmp_path / "capture.db")
    store = Store(db)
    await store.start()
    other = sqlite3.connect(db, isolation_level=None)
    try:
        other.execute("BEGIN IMMEDIATE")
        fut = await store.submit_line(ts=time.time(), port="b", dir="rx", chan="debug",
                                      seq=None, raw="never")
        while store.db_locked_since is None:
            await asyncio.sleep(0.01)
        store._writer_task.cancel()
        await asyncio.gather(store._writer_task, return_exceptions=True)
        assert fut.done() and fut.exception() is not None
        assert store.write_errors == 1
    finally:
        other.execute("ROLLBACK")
        other.close()
        await store.stop()


async def test_a_refused_sweep_chunk_does_not_pin_the_writer_once_the_lock_moves_on(
    tmp_path
) -> None:
    # A sweep chunk refused by the same lock leaves the writer inside a transaction. Once
    # the other process commits, retrying in that stale snapshot is refused for good, so
    # every retry has to start from a rolled-back connection.
    db = str(tmp_path / "capture.db")
    store = Store(db)
    await store.start()
    await store.add_line(ts=time.time(), port="b", dir="rx", chan="debug", seq=None,
                         raw="first")
    other = sqlite3.connect(db, isolation_level=None)
    try:
        other.execute("BEGIN IMMEDIATE")
        fut = await store.submit_line(ts=time.time(), port="b", dir="rx", chan="debug",
                                      seq=None, raw="held")
        while store.db_locked_since is None:
            await asyncio.sleep(0.01)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store._delete_lines("SELECT id FROM lines ORDER BY id LIMIT ?", (1,))
        assert store._conn.in_transaction    # the state the rollback has to clear
        other.execute("INSERT INTO meta(key, value) VALUES('other', 'x')")
        other.execute("COMMIT")
        row = await asyncio.wait_for(fut, 5)
        assert row["raw"] == "held" and store.db_locked_since is None
    finally:
        other.close()
        await store.stop()
