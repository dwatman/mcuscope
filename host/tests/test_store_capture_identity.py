"""The capture file is judged by identity while the daemon runs (RES-2, REVIEW class N1):
a replaced or deleted `db_path`, or a failed tick check, stops capture and leaves no WAL
that a later open would replay into another file."""

from __future__ import annotations

import os
import sqlite3
import time

import pytest

from mcuscope import store as store_mod
from mcuscope.lockfile import LockLost
from mcuscope.store import Store, StoreError

# What these tests do to the open capture, Windows refuses outright (see the last test).
posix_only = pytest.mark.skipif(
    os.name == "nt", reason="Windows opens the capture without FILE_SHARE_DELETE, so "
    "replacing or deleting it while open is refused and the hazard cannot arise")


async def _add(store: Store, n: int, tag: str) -> None:
    futs = [await store.submit_line(ts=time.time(), port="b", dir="rx", chan="debug",
                                    seq=None, raw=f"{tag} {i} " + "x" * 100)
            for i in range(n)]
    for fut in futs:
        await fut


def _snapshot(src: str, dst: str) -> None:
    a, b = sqlite3.connect(src), sqlite3.connect(dst)
    a.backup(b)
    a.close()
    b.close()


@posix_only
@pytest.mark.parametrize("readers", ["cached", "reopen"])
@pytest.mark.parametrize("order", ["before_checkpoint", "after_checkpoint"])
async def test_a_restored_copy_survives_the_daemon_that_was_writing_over_it(
    tmp_path, order, readers
) -> None:
    # Both mechanisms that pair this capture's -wal with the restored file: the writer's
    # close skipping its checkpoint because its file moved, and a read connection that
    # opened the restored file by path and checkpointed the old WAL into it on close.
    db = str(tmp_path / "capture.db")
    store = Store(db)
    await store.start()
    await _add(store, 300, "old")
    store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    _snapshot(db, str(tmp_path / "copy.db"))
    await _add(store, 300, "new")
    await store.count_lines_safe()             # a cached read connection on the live file
    if order == "after_checkpoint":
        store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    os.replace(tmp_path / "copy.db", db)
    if readers == "reopen":
        store._close_read_conns()
        with pytest.raises(StoreError, match="was replaced by another file"):
            await store.count_lines_safe()     # no reopen by path, before any tick has run
    await _add(store, 50, "after")             # still lands in the old file, by handle
    await store.sweep_tick(1)
    assert store.capture_error is not None and "was replaced by another file" in \
        store.capture_error
    assert not store.writer_alive
    await store.stop()
    c = sqlite3.connect(db)
    try:
        assert c.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert c.execute("SELECT COUNT(*) FROM lines").fetchone()[0] == 300
    finally:
        c.close()


@posix_only
async def test_a_deleted_capture_stops_capture_and_refuses_later_writes(tmp_path) -> None:
    db = tmp_path / "capture.db"
    store = Store(str(db))
    await store.start()
    try:
        await _add(store, 10, "x")
        assert await store.sweep_tick(1) == 0 and store.capture_error is None  # control
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(str(db) + suffix):
                os.remove(str(db) + suffix)
        await store.sweep_tick(1)
        assert "was deleted while the daemon was writing it" in store.capture_error
        assert not store.writer_alive
        before = store.write_errors
        with pytest.raises(StoreError):
            await store.add_line(ts=time.time(), port="b", dir="rx", chan="debug", seq=None,
                                 raw="lost")
        assert store.write_errors == before + 1
        # /status keeps answering: these two read the closed connection.
        assert store.active_session() is None and store.content_bytes() == 0
        # Any other read names the cause rather than a closed connection.
        with pytest.raises(StoreError, match="was deleted while the daemon was writing"):
            store.query_lines(limit=1)
    finally:
        await store.stop()


async def test_a_failing_tick_check_stops_capture_with_its_message(tmp_path) -> None:
    store = Store(str(tmp_path / "capture.db"))
    calls = []
    store.add_tick_check(lambda: calls.append(1))   # registered before start, as the server does
    await store.start()
    try:
        await store.sweep_tick(1)
        assert calls == [1] and store.capture_error is None and store.writer_alive

        def lost() -> None:
            raise LockLost("capture lock file /x/capture.db.lock was removed")

        store.add_tick_check(lost)
        await store.sweep_tick(1)
        assert store.capture_error == "capture lock file /x/capture.db.lock was removed"
        assert not store.writer_alive
        await store.sweep_tick(1)        # a failed capture runs no more checks
        assert calls == [1, 1]
    finally:
        await store.stop()


async def test_an_in_memory_capture_still_runs_tick_checks(tmp_path) -> None:
    store = Store(":memory:")
    calls = []
    store.add_tick_check(lambda: calls.append(1))
    await store.start()
    try:
        await store.sweep_tick(1)
        assert calls == [1] and store.capture_error is None
    finally:
        await store.stop()


@posix_only
async def test_every_path_opener_refuses_a_replaced_capture(tmp_path) -> None:
    # Read connections, streamed exports and the session export's ATTACH all open the
    # capture by name; each would read (and on close checkpoint into) the foreign file.
    db = str(tmp_path / "capture.db")
    store = Store(db)
    await store.start()
    try:
        await _add(store, 5, "x")
        session = await store.start_session("s")
        _snapshot(db, str(tmp_path / "copy.db"))
        os.replace(tmp_path / "copy.db", db)
        with pytest.raises(StoreError, match="was replaced by another file"):
            list(store.iter_lines_export())
        with pytest.raises(StoreError, match="was replaced by another file"):
            list(store.iter_plot_export(names=["t"]))
        with pytest.raises(StoreError, match="was replaced by another file"):
            store.export_session_db(str(tmp_path / "out.db"), id_from=session["start_id"],
                                    id_to=None, session=session)
    finally:
        await store.stop()


async def test_a_failed_capture_fails_what_was_queued_and_drops_its_readers(tmp_path) -> None:
    store = Store(str(tmp_path / "capture.db"))
    await store.start()
    try:
        await store.count_lines_safe()
        assert store._read_conns                  # control: a cached reader exists
        futs = [store.submit_line_nowait(ts=time.time(), port="b", dir="rx", chan="debug",
                                         seq=None, raw=f"q{i}") for i in range(5)]
        await store._fail_capture("capture gone")
        for fut in futs:
            assert isinstance(fut.exception(), StoreError)
        assert store.write_errors == 5
        assert not store._read_conns
        with pytest.raises(StoreError, match="capture gone"):
            await store.count_lines_safe()        # no reopen by path either
    finally:
        await store.stop()


async def test_a_capture_that_cannot_be_checked_stops_capture_with_the_cause(
    tmp_path, monkeypatch
) -> None:
    db = str(tmp_path / "capture.db")
    store = Store(db)
    await store.start()
    stat = os.stat

    def refused(path, *a, **kw):
        if os.fspath(path) == db:
            raise PermissionError(13, "Permission denied", path)
        return stat(path, *a, **kw)

    try:
        monkeypatch.setattr(store_mod.os, "stat", refused)
        await store.sweep_tick(1)
        assert store.capture_error == (f"capture {db} could not be checked ([Errno 13] "
                                       f"Permission denied: {db!r}); capture stopped")
    finally:
        monkeypatch.undo()
        await store.stop()


@pytest.mark.skipif(os.name != "nt", reason="the refusal is Windows' share mode")
async def test_windows_refuses_to_replace_or_delete_an_open_capture(tmp_path) -> None:
    db = str(tmp_path / "capture.db")
    store = Store(db)
    await store.start()
    try:
        await _add(store, 5, "x")
        _snapshot(db, str(tmp_path / "copy.db"))
        with pytest.raises(PermissionError):
            os.replace(tmp_path / "copy.db", db)
        with pytest.raises(PermissionError):
            os.remove(db)
        assert await store.sweep_tick(1) == 0 and store.capture_error is None
    finally:
        await store.stop()
