"""Sweeps and purges against another process's write lock and a stop mid-sweep (REVIEW
classes 94 and 103): rows already deleted are counted and recorded, and a held lock is a
lock episode, not an error per tick."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time

import pytest

from mcuscope import store as store_mod
from mcuscope.store import _RETENTION_TICKS, CaptureLocked, Store

CHUNK = 50


@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setattr(store_mod, "_RETENTION_CHUNK", CHUNK)


async def _started(tmp_path, name: str = "c.db") -> tuple[Store, sqlite3.Connection]:
    db = str(tmp_path / name)
    store = Store(db)
    await store.start(retention_days=7, max_db_bytes=0)
    await store._initial_sweep_task   # done before any row exists, so it cannot race
    return store, sqlite3.connect(db, isolation_level=None)


async def _fill(store: Store, n: int, ts: float) -> None:
    futs = [store.submit_line_nowait(ts=ts, port="t", dir="rx", chan="debug", seq=None,
                                     raw=f"{i} " + "x" * 200) for i in range(n)]
    for f in futs:
        await f


def _lock_after_first_chunk(monkeypatch, store: Store, name: str, other, then=None) -> list:
    """Wrap chunk method `name`: after the first chunk commits, `other` takes the write
    lock (or `then` runs)."""
    real, calls = getattr(store, name), []

    def wrapped(*args, **kwargs):
        n = real(*args, **kwargs)
        if not calls:
            (then or (lambda: other.execute("BEGIN IMMEDIATE")))()
        calls.append(n)
        return n

    monkeypatch.setattr(store, name, wrapped)
    return calls


def _sys(store: Store) -> list[str]:
    return [r["raw"] for r in store.query_lines(chans=["sys"], limit=100, order="asc")[0]]


def _errors(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


async def test_a_size_sweep_cut_short_by_a_lock_counts_records_and_joins_the_episode(
    tmp_path, monkeypatch, caplog, small_chunks
) -> None:
    store, other = await _started(tmp_path)
    try:
        await _fill(store, 1000, time.time())
        cap = store.content_bytes() // 2
        store.set_max_db_bytes(cap)
        calls = _lock_after_first_chunk(monkeypatch, store, "_delete_oldest_chunk", other)
        with caplog.at_level(logging.WARNING, logger="mcuscope.store"):
            await store.sweep_tick(1)
            assert calls == [CHUNK]
            assert store.lines_trimmed == CHUNK          # counted though the sweep failed
            assert not store._conn.in_transaction      # the refused chunk rolled back
            assert store.db_locked_since is not None
            assert _errors(caplog) == []
            other.execute("ROLLBACK")
            await store.drain_writes()
        rows = _sys(store)
        row = (f"storage: trimmed {CHUNK} oldest lines to stay under the {cap} byte cap; "
               "the sweep stopped early: database is locked")
        assert row in rows
        assert any(r.getMessage() == row for r in caplog.records)
        assert any(r.startswith("storage: capture database locked by another process for")
                   for r in rows)
        assert store.db_locked_since is None
    finally:
        other.close()
        await store.stop()


async def test_a_stop_mid_size_sweep_records_what_it_had_trimmed(
    tmp_path, monkeypatch, small_chunks
) -> None:
    store, other = await _started(tmp_path)
    try:
        await _fill(store, 1000, time.time())
        store.set_max_db_bytes(store.content_bytes() // 2)
        task = asyncio.create_task(store._sweep_size_reported())
        _lock_after_first_chunk(monkeypatch, store, "_delete_oldest_chunk", other,
                                then=task.cancel)
        with pytest.raises(asyncio.CancelledError):
            await task
        await store.drain_writes()
        assert store.lines_trimmed == CHUNK
        assert [r for r in _sys(store) if r.startswith("storage: trimmed")] == [
            f"storage: trimmed {CHUNK} oldest lines to stay under the "
            f"{store._max_db_bytes} byte cap; the sweep stopped early: the daemon stopped"]
    finally:
        other.close()
        await store.stop()


async def test_an_age_sweep_cut_short_by_a_lock_records_its_count_and_retries_next_tick(
    tmp_path, monkeypatch, caplog, small_chunks
) -> None:
    store, other = await _started(tmp_path)
    try:
        await _fill(store, 4 * CHUNK, time.time() - 30 * 86400)
        calls = _lock_after_first_chunk(monkeypatch, store, "_delete_expired_chunk", other)
        with caplog.at_level(logging.WARNING, logger="mcuscope.store"):
            await store.sweep_tick(_RETENTION_TICKS)
            assert calls == [CHUNK] and store.lines_expired == CHUNK
            assert store.db_locked_since is not None and _errors(caplog) == []
            other.execute("ROLLBACK")
            await store.drain_writes()
            # Not an hourly tick: the age sweep the lock put off runs anyway.
            await store.sweep_tick(1)
            await store.drain_writes()
        assert store.lines_expired == 4 * CHUNK
        expired = [r for r in _sys(store) if r.startswith("storage: expired")]
        assert len(expired) == 2
        assert expired[0].startswith(f"storage: expired {CHUNK} lines older than ")
        assert expired[0].endswith("; the sweep stopped early: database is locked")
        assert expired[1].startswith(f"storage: expired {3 * CHUNK} lines older than ")
        assert "stopped early" not in expired[1]
    finally:
        other.close()
        await store.stop()


async def test_a_lock_episode_a_sweep_opened_is_closed_by_a_tick_with_nothing_to_write(
    tmp_path, caplog
) -> None:
    store, other = await _started(tmp_path)
    try:
        await _fill(store, 1000, time.time())
        store.set_max_db_bytes(store.content_bytes() // 2)
        other.execute("BEGIN IMMEDIATE")
        with caplog.at_level(logging.WARNING, logger="mcuscope.store"):
            await store.sweep_tick(1)
            assert store.db_locked_since is not None and store.lines_trimmed == 0
            await store.sweep_tick(2)                  # still held: quiet, no error
            assert store.db_locked_since is not None and _errors(caplog) == []
            other.execute("ROLLBACK")
            store.set_max_db_bytes(0)                  # nothing left to write
            await store.sweep_tick(3)
            assert store.db_locked_since is None
            await store.drain_writes()
        locked = [r for r in _sys(store) if "capture database locked by another" in r]
        assert len(locked) == 1
        assert sum("locked by another process; writes are held" in r.getMessage()
                   for r in caplog.records) == 1
    finally:
        other.close()
        await store.stop()


async def test_the_episode_is_left_to_the_writer_when_its_row_cannot_be_queued(tmp_path) -> None:
    store, other = await _started(tmp_path)
    try:
        other.execute("BEGIN IMMEDIATE")
        futs = [store.submit_line_nowait(ts=time.time(), port="t", dir="rx", chan="debug",
                                         seq=None, raw="first")]
        while store.db_locked_since is None:
            await asyncio.sleep(0.01)
        while True:
            try:
                futs.append(store.submit_line_nowait(ts=time.time(), port="t", dir="rx",
                                                     chan="debug", seq=None, raw="x"))
            except asyncio.QueueFull:
                break
        other.execute("ROLLBACK")
        store._close_lock_episode()
        assert store.db_locked_since is not None       # kept for the writer to close
        for f in futs:
            await f
        await store.drain_writes()
        assert store.db_locked_since is None
        assert len([r for r in _sys(store) if "capture database locked by another" in r]) == 1
    finally:
        other.close()
        await store.stop()


async def test_a_sweep_row_that_cannot_be_queued_is_folded_into_the_next(
    tmp_path, monkeypatch
) -> None:
    store, other = await _started(tmp_path)
    try:
        await _fill(store, 1000, time.time())
        cap = store.content_bytes() // 2
        store.set_max_db_bytes(cap)
        real = store.submit_line_nowait

        def full(**kwargs):
            raise asyncio.QueueFull

        monkeypatch.setattr(store, "submit_line_nowait", full)
        trimmed = await store._sweep_size_reported()
        assert trimmed > 0 and store._trim_unannounced == trimmed
        monkeypatch.setattr(store, "submit_line_nowait", real)
        store.set_max_db_bytes(0)
        assert await store._sweep_size_reported() == 0
        assert [r for r in _sys(store) if r.startswith("storage: trimmed")] == [
            f"storage: trimmed {trimmed} oldest lines to stay under the {cap} byte cap"]
    finally:
        other.close()
        await store.stop()


async def test_a_startup_age_sweep_refused_by_a_lock_runs_on_the_first_tick(
    tmp_path, caplog
) -> None:
    store, other = await _started(tmp_path)
    await _fill(store, 20, time.time() - 30 * 86400)
    await store.stop()
    other.close()
    store = Store(str(tmp_path / "c.db"))
    with caplog.at_level(logging.WARNING, logger="mcuscope.store"):
        await store.start(retention_days=7, max_db_bytes=0)
        other = sqlite3.connect(str(tmp_path / "c.db"), isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")           # before the startup sweep runs
            await store._initial_sweep_task
            assert store.db_locked_since is not None and store.lines_expired == 0
            assert _errors(caplog) == []
            other.execute("ROLLBACK")
            await store.sweep_tick(1)
            assert store.lines_expired == 20
        finally:
            other.close()
            await store.stop()


async def test_a_folded_trim_row_names_every_cap_its_lines_were_trimmed_under(
    tmp_path, monkeypatch
) -> None:
    """A count carried into a sweep under a cap changed live keeps the cap it was trimmed
    under: the row names both, not only the latest."""
    store, other = await _started(tmp_path)
    try:
        await _fill(store, 1000, time.time())
        first = store.content_bytes() // 2
        store.set_max_db_bytes(first)
        real = store.submit_line_nowait

        def full(**kwargs):
            raise asyncio.QueueFull

        monkeypatch.setattr(store, "submit_line_nowait", full)
        carried = await store._sweep_size_reported()
        assert carried > 0 and store._trim_unannounced == carried
        monkeypatch.setattr(store, "submit_line_nowait", real)
        second = first // 2
        store.set_max_db_bytes(second)
        more = await store._sweep_size_reported()
        assert more > 0
        assert [r for r in _sys(store) if r.startswith("storage: trimmed")] == [
            f"storage: trimmed {carried + more} oldest lines to stay under the {first} and "
            f"{second} byte caps"]
    finally:
        other.close()
        await store.stop()


async def test_a_sweep_row_that_cannot_be_queued_at_stop_is_still_logged(
    tmp_path, monkeypatch, caplog
) -> None:
    """At stop there is no next sweep to fold a pending count into: the log keeps it."""
    store, other = await _started(tmp_path)
    try:
        store._trim_unannounced, store._trim_caps = 1234, [1000]

        def full(**kwargs):
            raise asyncio.QueueFull

        monkeypatch.setattr(store, "submit_line_nowait", full)
        with caplog.at_level(logging.WARNING, logger="mcuscope.store"):
            fut = store._sweep_row("_trim_unannounced", store._trim_row,
                                   asyncio.CancelledError())
        assert fut is None and store._trim_unannounced == 1234
        assert [r.getMessage() for r in caplog.records] == [
            "storage: trimmed 1234 oldest lines to stay under the 1000 byte cap; the sweep "
            "stopped early: the daemon stopped; not recorded in the capture: write queue full"]
        monkeypatch.undo()
    finally:
        other.close()
        await store.stop()


async def test_a_purge_cut_short_names_what_it_had_deleted(
    tmp_path, monkeypatch, caplog, small_chunks
) -> None:
    store, other = await _started(tmp_path)
    try:
        await _fill(store, 4 * CHUNK, time.time())
        _lock_after_first_chunk(monkeypatch, store, "_delete_range_chunk", other)
        with caplog.at_level(logging.WARNING, logger="mcuscope.store"), pytest.raises(
            CaptureLocked, match=f"^purge stopped after deleting {CHUNK} lines: the capture "
                                 "is locked by another process$"
        ):
            await store.delete_range(1, store.max_id())
        assert not store._conn.in_transaction
        assert store.db_locked_since is not None   # the lock episode, as for a sweep
        assert any(r.getMessage() == f"storage: purge stopped after deleting {CHUNK} lines: "
                   "the capture is locked by another process" for r in caplog.records)
    finally:
        other.execute("ROLLBACK")
        other.close()
        await store.stop()


async def test_a_purge_stopped_by_a_cancel_logs_its_count_and_stays_cancelled(
    tmp_path, monkeypatch, caplog, small_chunks
) -> None:
    store, other = await _started(tmp_path)
    try:
        await _fill(store, 4 * CHUNK, time.time())
        task = asyncio.create_task(store.delete_range(1, store.max_id()))
        _lock_after_first_chunk(monkeypatch, store, "_delete_range_chunk", other,
                                then=task.cancel)
        with caplog.at_level(logging.WARNING, logger="mcuscope.store"), pytest.raises(
                asyncio.CancelledError):
            await task
        assert any(r.getMessage() == f"storage: purge stopped after deleting {CHUNK} lines: "
                   "the daemon stopped" for r in caplog.records)
    finally:
        other.close()
        await store.stop()
