"""Another process's long read pins the WAL past `journal_size_limit` (RES-9, REVIEW class
N13): the tick checkpoints passively and announces it once per episode."""

from __future__ import annotations

import sqlite3
import time

from mcuscope import store as store_mod
from mcuscope.store import Store

PINNED = "storage: another process is holding a read open on the capture; the WAL is "


async def _fill(store: Store, n: int) -> None:
    futs = [await store.submit_line(ts=time.time(), port="b", dir="rx", chan="debug",
                                    seq=None, raw=f"{i} " + "x" * 200) for i in range(n)]
    for fut in futs:
        await fut


def _notices(store: Store) -> list[str]:
    return [r["raw"] for r in store.query_lines(chans=["sys"], limit=100)[0]
            if r["raw"].startswith(PINNED)]


async def test_a_pinned_wal_is_announced_once_per_episode(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store_mod, "_JOURNAL_SIZE_LIMIT", 32 * 1024)
    db = str(tmp_path / "c.db")
    store = Store(db)
    await store.start()
    reader = sqlite3.connect(db, isolation_level=None)
    try:
        # Control: a WAL past the limit that a checkpoint can drain is not announced.
        await _fill(store, 500)
        for _ in range(3):
            await store.sweep_tick(1)
        assert _notices(store) == []

        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM lines").fetchone()   # pins this snapshot
        await _fill(store, 500)
        await store.sweep_tick(1)
        assert _notices(store) == [], "announced on the first stuck tick"
        await store.sweep_tick(1)
        notices = _notices(store)
        assert len(notices) == 1 and notices[0].endswith(
            "MB and cannot be checkpointed until that read ends")
        await _fill(store, 100)
        await store.sweep_tick(1)
        assert len(_notices(store)) == 1, "announced again within one episode"

        reader.execute("COMMIT")
        await store.sweep_tick(1)          # drains: the episode ends and re-arms
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM lines").fetchone()
        await _fill(store, 500)
        await store.sweep_tick(1)
        await store.sweep_tick(1)
        assert len(_notices(store)) == 2
    finally:
        reader.close()
        await store.stop()
