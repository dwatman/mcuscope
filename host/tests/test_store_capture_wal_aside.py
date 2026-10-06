"""A capture replaced or deleted while a read holds its WAL (FD-STORE-1): the TRUNCATE that
empties the WAL is refused, so the store moves its own -wal and -shm off the path, or says
so in `capture_error` when it cannot. Either way a restart never replays this capture's WAL
into the file now at the path."""

from __future__ import annotations

import asyncio
import errno
import os
import sqlite3
import threading
import time

import pytest

from mcuscope import store as store_mod
from mcuscope.cli import MOVE_ASIDE  # the trigger `mcu status` keys on
from mcuscope.lockfile import LockLost
from mcuscope.store import Store, StoreError

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="Windows opens the capture without FILE_SHARE_DELETE, so the "
    "replace or delete these tests make while it is open is refused outright")



async def _add(store: Store, n: int, tag: str) -> None:
    futs = [await store.submit_line(ts=time.time(), port="b", dir="rx", chan="debug",
                                    seq=None, raw=f"{tag} {i} " + "x" * 100)
            for i in range(n)]
    for fut in futs:
        await fut


async def _restorable(tmp_path) -> tuple[Store, str]:
    """A store with 300 lines in a snapshot at copy.db and 300 more only in its WAL."""
    db = str(tmp_path / "capture.db")
    store = Store(db)
    await store.start()
    await _add(store, 300, "old")
    store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    a, b = sqlite3.connect(db), sqlite3.connect(str(tmp_path / "copy.db"))
    a.backup(b)
    a.close()
    b.close()
    await _add(store, 300, "new")
    return store, db


def _outside_reader(db: str) -> sqlite3.Connection:
    r = sqlite3.connect(db, isolation_level=None)
    r.execute("BEGIN")
    r.execute("SELECT COUNT(*) FROM lines").fetchone()   # a read snapshot on the old file
    return r


def _rows_at(db: str) -> int:
    c = sqlite3.connect(db)
    try:
        assert c.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        return c.execute("SELECT COUNT(*) FROM lines").fetchone()[0]
    finally:
        c.close()


def _stale(tmp_path) -> list[str]:
    return sorted(n for n in os.listdir(tmp_path) if ".stale-" in n)


@pytest.mark.parametrize("reader_open_at_restart", [False, True])
async def test_an_outside_read_open_at_the_replace_does_not_reach_the_restored_file(
    tmp_path, reader_open_at_restart
) -> None:
    store, db = await _restorable(tmp_path)
    r = _outside_reader(db)
    os.replace(tmp_path / "copy.db", db)
    await store.sweep_tick(1)
    assert "was replaced by another file" in store.capture_error
    assert MOVE_ASIDE not in store.capture_error   # moved, so a restart is safe
    assert [n.split(".stale-")[0] for n in _stale(tmp_path)] == ["capture.db-shm",
                                                                 "capture.db-wal"]
    await store.stop()
    if not reader_open_at_restart:
        r.close()
    assert _rows_at(db) == 300
    r.close()
    assert _rows_at(db) == 300


async def test_a_read_that_ends_within_the_wait_lets_the_wal_empty_in_place(tmp_path) -> None:
    store, db = await _restorable(tmp_path)
    r = _outside_reader(db)
    os.replace(tmp_path / "copy.db", db)
    asyncio.get_running_loop().call_later(0.3, r.rollback)
    await store.sweep_tick(1)
    assert store.capture_error is not None
    assert _stale(tmp_path) == [] and os.path.getsize(db + "-wal") == 0
    await store.stop()
    r.close()
    assert _rows_at(db) == 300


async def test_the_plot_summary_rebuild_holding_a_read_is_survived(tmp_path, monkeypatch) -> None:
    store, db = await _restorable(tmp_path)
    in_snapshot, release = threading.Event(), threading.Event()
    scan = Store._scan_plot_rows

    def held_scan(self, c, high):
        out = scan(self, c, high)   # the rebuild's BEGIN snapshot is now open
        in_snapshot.set()
        release.wait(10)
        return out

    monkeypatch.setattr(Store, "_scan_plot_rows", held_scan)
    store._plot_dirty = True
    rebuild = asyncio.create_task(store._rebuild_plot_summary())
    loop = asyncio.get_running_loop()
    assert await loop.run_in_executor(None, in_snapshot.wait, 5)
    os.replace(tmp_path / "copy.db", db)
    await store.sweep_tick(1)
    assert "was replaced by another file" in store.capture_error
    assert len(_stale(tmp_path)) == 2
    release.set()
    with pytest.raises(Exception):   # noqa: B017 - its connection was closed under it
        await rebuild
    await store.stop()
    assert _rows_at(db) == 300


async def test_a_session_export_reading_the_live_file_is_survived(tmp_path) -> None:
    store, db = await _restorable(tmp_path)
    in_snapshot, release = threading.Event(), threading.Event()

    def hold(conn: sqlite3.Connection) -> None:
        def progress() -> int:
            attached = any(row[1] == "src" for row in conn.execute("PRAGMA database_list"))
            if attached and conn.in_transaction and not in_snapshot.is_set():
                in_snapshot.set()
                release.wait(10)
            return 0
        conn.set_progress_handler(progress, 1000)

    session = {"id": 1, "name": "s", "note": "", "started_ts": 0.0, "ended_ts": None,
               "start_id": 1, "end_id": None, "auto": 0}
    loop = asyncio.get_running_loop()
    export = loop.run_in_executor(None, lambda: store.export_session_db(
        str(tmp_path / "out.db"), id_from=1, id_to=None, session=session, on_open=hold))
    assert await loop.run_in_executor(None, in_snapshot.wait, 5)
    os.replace(tmp_path / "copy.db", db)
    await store.sweep_tick(1)
    assert len(_stale(tmp_path)) == 2
    release.set()
    await export
    await store.stop()
    assert _rows_at(db) == 300


@pytest.mark.parametrize("why", ["rename_refused", "identity_unknown"])
async def test_a_wal_that_cannot_be_moved_is_named_in_capture_error(
    tmp_path, monkeypatch, why
) -> None:
    store, db = await _restorable(tmp_path)
    r = _outside_reader(db)
    if why == "rename_refused":
        def refuse(src, dst):
            raise PermissionError(13, "Permission denied", src)
        monkeypatch.setattr(store_mod.os, "rename", refuse)
    else:
        store._side_identity = {"-wal": None, "-shm": None}
    os.replace(tmp_path / "copy.db", db)
    await store.sweep_tick(1)
    assert f"move {db}-wal and {db}-shm aside before restarting" in store.capture_error
    with pytest.raises(StoreError, match=MOVE_ASIDE):
        store._conn.execute("SELECT 1")   # the stand-in carries the full reason
    await store.stop()
    r.close()


async def test_a_foreign_wal_at_the_path_is_left_where_it_is(tmp_path) -> None:
    store, db = await _restorable(tmp_path)
    r = _outside_reader(db)
    os.replace(tmp_path / "copy.db", db)
    (tmp_path / "theirs").write_bytes(b"not this capture's")
    os.replace(tmp_path / "theirs", db + "-wal")   # a restore that brought its own -wal
    await store.sweep_tick(1)
    assert MOVE_ASIDE not in store.capture_error
    assert (tmp_path / "capture.db-wal").read_bytes() == b"not this capture's"
    assert [n.split(".stale-")[0] for n in _stale(tmp_path)] == ["capture.db-shm"]
    await store.stop()
    r.close()


async def test_a_failed_check_on_an_intact_file_keeps_its_wal(tmp_path) -> None:
    store, db = await _restorable(tmp_path)
    r = _outside_reader(db)

    def lost() -> None:
        raise LockLost("capture lock lost")

    store.add_tick_check(lost)
    await store.sweep_tick(1)
    assert store.capture_error.startswith("capture lock lost")
    assert MOVE_ASIDE not in store.capture_error and _stale(tmp_path) == []
    await store.stop()
    r.close()
    assert _rows_at(db) == 600   # the WAL held this file's own commits


async def test_stop_during_the_wait_still_moves_the_wal_aside(tmp_path) -> None:
    store, db = await _restorable(tmp_path)
    r = _outside_reader(db)
    os.replace(tmp_path / "copy.db", db)
    tick = asyncio.create_task(store.sweep_tick(1))
    while store.capture_error is None:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.1)   # inside _empty_wal's retries
    tick.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tick
    assert len(_stale(tmp_path)) == 2
    await store.stop()
    r.close()
    assert _rows_at(db) == 300


async def test_an_unreadable_stat_of_the_capture_renames_nothing(tmp_path, monkeypatch) -> None:
    store, db = await _restorable(tmp_path)
    r = _outside_reader(db)
    real = os.stat

    def eio(path, *a, **k):
        if os.fspath(path) == db:
            raise OSError(errno.EIO, "I/O error", path)
        return real(path, *a, **k)

    monkeypatch.setattr(os, "stat", eio)
    await store.sweep_tick(1)
    monkeypatch.undo()
    assert _stale(tmp_path) == []   # an unknown identity is no proof the file was replaced
    assert MOVE_ASIDE in store.capture_error
    r.close()
    await store.stop()
