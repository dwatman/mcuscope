"""The `lines.id` sequence never moves down, whichever insert path a row takes.

`max_id()` answers from the writer's sequence, so an id above SQL's MAX(id) may already be
in a client's hands (a `/wait` watermark, a `since_id` cursor) or inside a session's span
after the newest rows are deleted. Rewinding past it hid new lines from followers and put
them inside old sessions.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from mcuscope.store import Store, StoreError
from tests.support import add_sys


async def _line(store: Store, raw: str, chan: str = "debug") -> asyncio.Future:
    return await store.submit_line(
        ts=time.time(), port="p", dir="rx", chan=chan, seq=None, raw=raw
    )


class _CommitFailsOnce:
    """The loop connection with one failing commit, as a full disk gives."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self.real, self.armed = real, True

    def commit(self) -> None:
        if self.armed:
            self.armed = False
            raise sqlite3.OperationalError("disk I/O error")
        self.real.commit()

    def __getattr__(self, name):
        return getattr(self.real, name)


async def test_a_failed_commit_after_a_tail_purge_does_not_rewind_the_ids(tmp_path) -> None:
    store = Store(str(tmp_path / "rewind.db"))
    await store.start()
    try:
        for i in range(5):
            await (await _line(store, f"old {i}"))
        top = store.max_id()
        assert await store.delete_range(1, top) == top
        watermark = store.max_id()
        assert watermark == top, "a client that polled here holds this id"

        real = store._conn
        store._conn = _CommitFailsOnce(real)
        lost = await _line(store, "lost to the disk")
        with pytest.raises(StoreError, match="commit failed"):
            await lost
        store._conn = real

        after = await (await _line(store, "AFTER"))
        assert after["id"] > watermark, f"id {after['id']} is at or below {watermark}"
        rows, _ = store.query_lines(since_id=watermark, limit=10)
        assert [r["raw"] for r in rows] == ["AFTER"]
    finally:
        await store.stop()


async def test_the_row_by_row_fallback_keeps_ids_out_of_a_session_span(tmp_path) -> None:
    store = Store(str(tmp_path / "fallback.db"))
    await store.start()
    try:
        await store.start_session("run-alpha")
        for i in range(5):
            await (await _line(store, f"alpha {i}"))
        alpha = await store.stop_session()
        assert await store.delete_range(1, store.max_id()) > 0

        # A CHECK violation in the batch sends it down the row-by-row fallback.
        good = await _line(store, "beta good")
        bad = await _line(store, "beta bad", chan="nope")
        assert (await good)["id"] > alpha["end_id"], "the fallback reused a session's id"
        with pytest.raises(sqlite3.IntegrityError):
            await bad
        rows, _ = store.query_lines(id_from=alpha["start_id"], id_to=alpha["end_id"], limit=100)
        assert rows == [], f"session run-alpha now holds {[r['raw'] for r in rows]}"
        # And the batch path after it continues above the fallback's ids.
        nxt = await (await _line(store, "beta next"))
        assert nxt["id"] > (await good)["id"]
    finally:
        await store.stop()


async def test_the_fallback_after_a_tail_purge_continues_above_the_old_top(tmp_path) -> None:
    store = Store(str(tmp_path / "fallback_top.db"))
    await store.start()
    try:
        for i in range(4):
            await (await _line(store, f"old {i}"))
        top = store.max_id()
        await store.delete_range(2, top)
        good = await _line(store, "good")
        bad = await _line(store, "bad", chan="nope")
        with pytest.raises(sqlite3.IntegrityError):
            await bad
        assert (await good)["id"] == top + 1
        assert store.max_id() == top + 1
    finally:
        await store.stop()


def test_id_sequence_continues_across_restart(tmp_path) -> None:
    # The writer assigns ids itself, so a reopened store must pick up where the file left
    # off rather than colliding with existing rows.
    path = str(tmp_path / "seq.db")

    async def run() -> None:
        store = Store(path)
        await store.start()
        try:
            first = await add_sys(store, "before restart")
        finally:
            await store.stop()

        store = Store(path)
        await store.start()
        try:
            second = await add_sys(store, "after restart")
            assert second["id"] == first["id"] + 1
            rows, _ = store.query_lines(limit=10, order="asc")
            assert [r["raw"] for r in rows] == ["before restart", "after restart"]
        finally:
            await store.stop()

    asyncio.run(run())


def test_line_ids_are_not_reused_after_the_table_empties(tmp_path) -> None:
    """A session's id span must never come to describe a later run's lines.

    Sessions survive retention and `purge --all`, so restarting the id sequence at 1 made
    `session show run-alpha` return run-beta's traffic, and export/purge act on it.
    """
    db = tmp_path / "cap.db"

    async def first() -> tuple[int, int]:
        store = Store(str(db))
        await store.start()
        await store.start_session("run-alpha")
        for i in range(5):
            await store.add_line(ts=1.0, port="a", dir="rx", chan="debug", seq=None,
                                 raw=f"alpha {i}")
        alpha = await store.stop_session()
        # What `purge --all` does: delete every line, leaving the session rows behind.
        await store.delete_range(1, store.max_id())
        assert store.count_lines() == 0
        await store.stop()
        return alpha["end_id"], 0

    async def second() -> int:
        store = Store(str(db))
        await store.start()
        row = await store.add_line(ts=2.0, port="a", dir="rx", chan="debug", seq=None,
                                   raw="beta 0")
        await store.stop()
        return row["id"]

    high, _ = asyncio.run(first())
    assert asyncio.run(second()) > high
