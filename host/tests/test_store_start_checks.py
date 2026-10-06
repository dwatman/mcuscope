"""What `Store.start` checks of the capture it opens: an unclean previous stop is recorded
(RES-7), an unreadable file is refused by name (RES-8), and new files are owner-only on
POSIX (SEC-6)."""

from __future__ import annotations

import os
import sqlite3
import stat
import time

import pytest

from mcuscope.store import DAEMON_START_ROW, DAEMON_STOP_ROW, CaptureUnreadable, Store

UNCLEAN = "previous run ended without a clean stop after line "


async def _run(db: str, rows: list[str]) -> None:
    store = Store(db)
    await store.start()
    try:
        for raw in rows:
            await store.add_line(ts=time.time(), port="", dir="-", chan="sys", seq=None,
                                 raw=raw)
    finally:
        await store.stop()


async def _sys_rows(db: str) -> list[str]:
    store = Store(db)
    await store.start()
    try:
        await store.drain_writes()
        return [r["raw"] for r in store.query_lines(chans=["sys"], limit=100, order="asc")[0]]
    finally:
        await store.stop()


async def test_a_run_that_never_wrote_daemon_stop_is_recorded(tmp_path) -> None:
    db = str(tmp_path / "c.db")
    await _run(db, [DAEMON_START_ROW, "work"])
    rows = await _sys_rows(db)
    notice = [r for r in rows if r.startswith(UNCLEAN)]
    assert len(notice) == 1 and notice[0].endswith("; lines in flight were lost")
    assert notice[0].startswith(UNCLEAN + "2 (")   # the newest line the dead run left


async def test_a_clean_stop_and_a_first_run_record_nothing(tmp_path) -> None:
    clean = str(tmp_path / "clean.db")
    await _run(clean, [DAEMON_START_ROW, "work", DAEMON_STOP_ROW])
    assert not any(r.startswith(UNCLEAN) for r in await _sys_rows(clean))
    fresh = str(tmp_path / "fresh.db")
    assert not any(r.startswith(UNCLEAN) for r in await _sys_rows(fresh))
    # Rows that merely mention the words are not the lifecycle rows.
    quoted = str(tmp_path / "quoted.db")
    await _run(quoted, [DAEMON_START_ROW, DAEMON_STOP_ROW, f"note: {DAEMON_START_ROW}"])
    assert not any(r.startswith(UNCLEAN) for r in await _sys_rows(quoted))


def _corrupt_interior(path: str) -> None:
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE lines(id INTEGER PRIMARY KEY, ts REAL, port TEXT, dir TEXT, "
              "chan TEXT, seq INTEGER, raw TEXT)")
    c.executemany("INSERT INTO lines(ts, port, dir, chan, seq, raw) VALUES(1,'b','rx',"
                  "'debug',NULL,?)", [("x" * 200,)] * 2000)
    c.commit()
    c.close()
    with open(path, "r+b") as f:
        f.seek(4096 * 3)
        f.write(b"\xff" * 4096)


@pytest.mark.parametrize("kind", ["garbage", "interior_page"])
async def test_an_unreadable_capture_is_refused_by_name_with_the_remedy(tmp_path, kind) -> None:
    db = tmp_path / "bad.db"
    if kind == "garbage":
        db.write_bytes(os.urandom(8192))
    else:
        _corrupt_interior(str(db))
    before = db.read_bytes()
    store = Store(str(db))
    with pytest.raises(CaptureUnreadable) as info:
        await store.start()
    msg = str(info.value)
    assert msg.startswith(f"capture {db} is unreadable (")
    assert msg.endswith("): move it aside or set storage.db_path")
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert store._conn is None and store._writer_task is None
    # Past the 100-byte header, which opening in WAL mode rewrites (as it always has).
    assert db.read_bytes()[100:] == before[100:], "a refused capture's content was written"


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
async def test_a_new_capture_and_its_new_dirs_are_owner_only(tmp_path) -> None:
    os.chmod(tmp_path, 0o755)
    db = tmp_path / "a" / "b" / "c.db"
    store = Store(str(db))
    old_umask = os.umask(0o022)   # a 077 umask would give these modes by itself
    try:
        await store.start()
    finally:
        os.umask(old_umask)
    try:
        await store.add_line(ts=time.time(), port="", dir="-", chan="sys", seq=None, raw="x")
        for p in (db, db.with_name("c.db-wal"), db.with_name("c.db-shm")):
            assert stat.S_IMODE(p.stat().st_mode) == 0o600, p
        for d in (tmp_path / "a", tmp_path / "a" / "b"):
            assert stat.S_IMODE(d.stat().st_mode) == 0o700, d
        assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o755   # existing: untouched
    finally:
        await store.stop()


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
async def test_an_existing_capture_keeps_its_mode(tmp_path) -> None:
    db = tmp_path / "c.db"
    sqlite3.connect(db).close()
    os.chmod(db, 0o640)
    store = Store(str(db))
    await store.start()
    await store.stop()
    assert stat.S_IMODE(db.stat().st_mode) == 0o640


async def test_a_capture_failing_after_open_closes_its_connection(tmp_path, monkeypatch) -> None:
    # A corrupt page the setup never reads fails on the first read of the existing rows.
    db = tmp_path / "c.db"
    opened = []

    def bad(self) -> int:
        opened.append(self._conn)
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(Store, "_max_session_ref_id", bad)
    store = Store(str(db))
    with pytest.raises(CaptureUnreadable, match="malformed"):
        await store.start()
    assert store._conn is None
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


async def test_a_capture_that_cannot_be_opened_is_not_called_unreadable(tmp_path) -> None:
    # OperationalError (full, locked, read-only, unopenable) is a DatabaseError too, and
    # "move it aside" would send the owner after a file that is not at fault.
    path = tmp_path / "a-directory.db"
    path.mkdir()
    store = Store(str(path))
    with pytest.raises(sqlite3.OperationalError):
        await store.start()
    assert store._conn is None


async def test_a_code_fault_at_start_is_not_blamed_on_the_file(tmp_path, monkeypatch) -> None:
    def fault() -> None:
        raise sqlite3.IntegrityError("CHECK constraint failed: a migration bug")

    store = Store(str(tmp_path / "c.db"))
    monkeypatch.setattr(store, "_open_capture", fault)
    with pytest.raises(sqlite3.IntegrityError, match="a migration bug"):
        await store.start()
