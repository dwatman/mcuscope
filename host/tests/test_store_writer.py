"""The store writer dying of something nobody guarded, and what happens to the queue.

`_fail_queued` existed but was wired only into `stop()`, so a writer that died on its own
left every queued future pending: `SerialPort._store_rx_batch` awaits exactly those, and
they resolve only when the loop closes.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import time

import pytest

from mcuscope.store import _MAX_BATCH_ROWS, _SLOW_COMMIT_S, Store, StoreError, _WriteReq
from tests.support import T0_FIXED, CommitBoom, add_row, add_sys, started_store


async def _submit(store: Store, raw: str, plot=None):
    return await store.submit_line(
        ts=time.time(), port="t", dir="rx", chan="debug", seq=None, raw=raw, plot=plot
    )


async def test_a_writer_that_dies_fails_what_is_still_queued(
    tmp_path, monkeypatch, caplog
) -> None:
    """One row per batch, so the writer dies with the rest of the queue behind it."""
    monkeypatch.setattr("mcuscope.store._MAX_BATCH_ROWS", 1)
    store = Store(str(tmp_path / "died.db"))
    await store.start()
    try:
        def boom(rows) -> None:     # outside the insert/commit guards: it ends the task
            raise RuntimeError("broadcast exploded")

        monkeypatch.setattr(store, "_broadcast_batch", boom)
        # No await between the puts (the queue is not full, so put does not yield), so all
        # three are queued before the writer wakes and takes the first one alone.
        futures = [await _submit(store, f"line {i}") for i in range(3)]
        for fut in futures[1:]:
            with pytest.raises(StoreError, match="store writer exited"):
                await asyncio.wait_for(fut, 2)
        assert not store.writer_alive
        # Positive control for the clean-stop test's absence check below.
        died = [r for r in caplog.records if "store writer died" in r.getMessage()]
        assert len(died) == 1, died
    finally:
        await store.stop()


async def test_a_writer_that_dies_mid_batch_fails_that_batch(tmp_path, monkeypatch) -> None:
    """The rows already taken off the queue are reachable from nowhere else."""
    store = Store(str(tmp_path / "batch.db"))
    await store.start()
    try:
        def boom(row, plot) -> None:
            raise RuntimeError("summary exploded")

        # The per-row step after the future resolves: a broadcast raise no longer reaches
        # an unresolved row, since the whole batch is fanned out after every future is set.
        monkeypatch.setattr(store, "_note_plot", boom)
        futures = [await _submit(store, f"line {i}", plot=[(1, None, "v", 1.0)])
                   for i in range(3)]
        # The first row is stored and resolved before the summary step raises; the rest of
        # the batch is what the writer would otherwise take down with it.
        assert (await asyncio.wait_for(futures[0], 2))["raw"] == "line 0"
        for fut in futures[1:]:
            with pytest.raises(StoreError, match="store writer exited"):
                await asyncio.wait_for(fut, 2)
    finally:
        await store.stop()


async def test_a_clean_stop_does_not_report_a_dead_writer(tmp_path, caplog) -> None:
    """The sentinel exit is not a death: stop() owns the queue on that path."""
    store = Store(str(tmp_path / "clean.db"))
    await store.start()
    await asyncio.wait_for(_submit(store, "kept"), 2)
    await store.stop()
    assert not [r for r in caplog.records if "store writer died" in r.getMessage()]


def test_writer_survives_commit_failure(tmp_path) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "c.db"))
        await store.start()
        try:
            store._conn = CommitBoom(store._conn)
            with pytest.raises(StoreError):
                await add_sys(store, "first")
            # The writer must still be alive and serving after the failed commit.
            row = await add_sys(store, "second")
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
            row = await add_sys(store, "still alive")
            assert row["id"] > 0
        finally:
            await store.stop()

    asyncio.run(run())


def test_failed_child_insert_leaves_no_orphan_line(tmp_path) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "o.db"))
        await store.start()
        try:
            bad_can = {"tick_ms": 0, "bus": 1, "can_id": None, "ext": False, "rtr": False,
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
            follow = await add_sys(store, "d")
            assert follow["id"] > rows[-1]["id"]
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
                    can={"tick_ms": i, "bus": 1, "can_id": 0x100 + i, "ext": False, "rtr": False,
                         "dlc": 1, "data": bytes([i])},
                    plot=[(i, "0", "v", float(i))],
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
                await add_sys(store, "slow one")
        finally:
            store._conn = store._conn._conn
            await store.stop()

    asyncio.run(run())
    warnings = [r.message for r in caplog.records if "slow capture commit" in r.message]
    assert warnings, [r.message for r in caplog.records]
    assert "1 rows" in warnings[0]
    ms = float(warnings[0].split(":")[1].strip().split(" ")[0])
    assert ms >= _SLOW_COMMIT_S * 1000


def test_a_bare_carriage_return_is_folded_too(tmp_path) -> None:
    """F-10: every existing case also held a `\\n`, so the `\\r` half of the guard was inert."""

    async def run() -> None:
        store = await started_store(tmp_path / "cr.db")
        try:
            row = await add_row(store, T0_FIXED, "a\rb")
            assert row["raw"] == "a b"
            stored, _ = store.query_lines(limit=1)
            assert stored[0]["raw"] == "a b"
        finally:
            await store.stop()

    asyncio.run(run())


def test_store_stop_fails_queued_writes_instead_of_stranding_them() -> None:
    """A cancelled writer left queued futures unresolved, and _store_rx_batch awaits them:
    the awaiter hung until the loop closed and died pending."""
    from mcuscope.store import StoreError

    async def run() -> None:
        store = Store(":memory:")
        await store.start()
        # Cancel the writer out from under the queue, then queue a write nobody will drain.
        store._writer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await store._writer_task
        pending = asyncio.get_running_loop().create_future()
        store._queue.put_nowait(
            _WriteReq(row={"raw": "x"}, can=None, plot=None, future=pending)
        )
        await store.stop()
        assert pending.done()
        with pytest.raises(StoreError):
            pending.result()

    asyncio.run(run())


# -- August 2026 round ----------------------------------------------------------------


def test_a_dead_store_writer_fails_writes_instead_of_hanging(tmp_path) -> None:
    """A writer that exits left submit_line awaiting a future nobody would ever resolve.

    The lifespan's shutdown awaits add_line under `suppress(Exception)`, which cannot catch
    a hang: the daemon needed SIGKILL, and the pid record and capture lock leaked with it.
    """
    from mcuscope.store import StoreError

    async def run() -> None:
        store = Store(str(tmp_path / "dead.db"))
        await store.start()
        assert store.writer_alive
        store._writer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await store._writer_task
        assert not store.writer_alive

        with pytest.raises(StoreError):
            await asyncio.wait_for(
                store.add_line(ts=time.time(), port="A", dir="rx", chan="debug",
                               seq=None, raw="after"),
                timeout=5.0,
            )
        # The lifespan's own shutdown sequence, in order, must still complete. Only the
        # refusal is suppressed: a step that hangs raises TimeoutError and fails the test.
        with contextlib.suppress(StoreError):
            await asyncio.wait_for(store.stop_session(), timeout=5.0)
        with contextlib.suppress(StoreError):
            await asyncio.wait_for(
                store.add_line(ts=time.time(), port="", dir="-", chan="sys",
                               seq=None, raw="daemon stop"),
                timeout=5.0,
            )
        await asyncio.wait_for(store.stop(), timeout=5.0)

    asyncio.run(run())


def test_a_line_refused_by_a_dead_writer_counts_as_a_write_error(tmp_path) -> None:
    # RG-F2. submit_line's fast-fail raised before ever reaching _fail_write, so
    # `write_errors` (documented on /status as the count of lines the capture was handed
    # and did not store) read 0 in exactly the state it exists to reveal.
    async def run() -> None:
        store = Store(str(tmp_path / "dead.db"))
        await store.start()
        try:
            await add_sys(store, "before")
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
