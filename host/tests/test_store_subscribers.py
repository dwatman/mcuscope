"""The store's live-row subscribers: the cap, the shed count a slow one is told, and the
stop sentinel (SPEC 3.4)."""

from __future__ import annotations

import asyncio
import json

import pytest

from mcuscope.store import CaptureMark, Store, StoreError
from tests.support import add_row, run_coro, started_store

# -- improvement 7: a subscriber parked on the queue is woken when the capture closes ----


def test_stop_wakes_every_subscriber_with_a_sentinel(tmp_path) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "sentinel.db"))
        await store.start()
        empty = store.subscribe()
        full = store.subscribe(maxsize=1)
        full.put_nowait({"id": 1})            # drop-oldest has to make room for the sentinel
        await store.stop()
        assert empty.get_nowait() is None
        assert full.get_nowait() is None, "a full queue must still receive the sentinel"

    run_coro(run)


def test_subscriber_cap() -> None:
    from mcuscope.store import MAX_SUBSCRIBERS

    store = Store(":memory:")
    qs = [store.subscribe() for _ in range(MAX_SUBSCRIBERS)]
    with pytest.raises(StoreError):
        store.subscribe()
    for q in qs:
        store.unsubscribe(q)
    store.subscribe()  # room again after release


    # else: uvicorn grew its own flow control and _enable_ws_backpressure deferred to it,
    # which is the guard's designed outcome.


def test_a_slow_subscriber_is_told_it_missed_rows(tmp_path) -> None:
    """The feed sheds the oldest row rather than blocking the writer, and said nothing.

    Measured during the round with a raw socket that stopped reading for 60 s: 36.7% of the
    span never arrived, while /status held connected=true, rx_dropped=0, write_errors=0. The
    web UI builds its plots from this stream, so the chart simply had holes. An id gap cannot
    be inferred client-side either, because `port=` filtering makes gaps legitimate.
    """
    async def run() -> None:
        store = Store(":memory:")
        await store.start()
        try:
            q = store.subscribe(maxsize=4)
            for i in range(1, 11):        # 10 rows into a queue that holds 4
                store._broadcast({"id": i, "port": "p", "raw": f"r{i}"})
            assert q.qsize() == 4
            dropped = store.take_dropped(q)
            assert dropped == 6, f"shed 6 rows, reported {dropped}"
            assert store.ws_dropped == 6, "the lifetime total on /status did not move"
            # Taking the count clears it, so the next frame does not re-announce the gap.
            assert store.take_dropped(q) == 0
            # And an unsubscribe does not leave the accounting behind.
            store.unsubscribe(q)
            assert q not in store._sub_dropped
        finally:
            await store.stop()

    asyncio.run(run())


# -- a capture change is in-band: the rows queued before it stay ahead of it ---------------


def test_a_capture_change_is_queued_between_the_old_rows_and_the_new(tmp_path) -> None:
    """A purge that frees the highest id changes the capture identity (SPEC 3.4).

    Rows already queued for a /ws subscriber belong to the capture before it, and the pump
    used to put the new token at the head of the frame, ahead of them: a client resetting on
    it then took those rows as the new capture's, and showed a line the purge had deleted.
    The change has to sit in the queue at the point it happened, between the old rows and
    the new.
    """
    async def run() -> None:
        store = await started_store(tmp_path / "mark.db")
        try:
            for i in range(3):
                await add_row(store, 1000.0 + i, f"r{i}")
            before = store.capture_id
            q = store.subscribe(as_json=True)
            plain = store.subscribe()   # a dict subscriber (/wait, /assert) is not told
            store._broadcast({"id": 3, "port": "board", "raw": "old"})
            store._delete_lines("SELECT id FROM lines WHERE id >= ?", (3,))
            assert store.capture_id != before
            store._broadcast({"id": 4, "port": "board", "raw": "new"})
            items = [q.get_nowait() for _ in range(q.qsize())]
            assert [json.loads(x) for x in items] == [
                {"id": 3, "port": "board", "raw": "old"},
                {"capture": store.capture_id},
                {"id": 4, "port": "board", "raw": "new"},
            ]
            assert isinstance(items[1], CaptureMark) and items[1].capture == store.capture_id
            assert [r["raw"] for r in (plain.get_nowait(), plain.get_nowait())] == ["old", "new"]
            assert plain.empty()
        finally:
            await store.stop()

    run_coro(run)


def test_a_shed_capture_change_is_handed_to_the_pump_and_not_counted(tmp_path) -> None:
    # A full queue sheds its oldest item. A change shed that way leaves only rows of the new
    # capture behind it, so the pump is told to name it ahead of all of them; it is not a
    # line, so it must not swell the gap notice either.
    async def run() -> None:
        store = await started_store(tmp_path / "shed.db")
        try:
            await add_row(store, 1000.0, "r0")
            q = store.subscribe(maxsize=2, as_json=True)
            assert store.take_shed_capture(q) is None
            store._delete_lines("SELECT id FROM lines WHERE id >= ?", (1,))
            for i in range(3):   # the change, then three rows into a queue of two
                store._broadcast({"id": 2 + i, "port": "board", "raw": f"n{i}"})
            assert store.take_dropped(q) == 1, "only the one shed row is a missed line"
            assert store.take_shed_capture(q) == store.capture_id
            assert store.take_shed_capture(q) is None, "taking it clears it"
            assert [json.loads(q.get_nowait())["raw"] for _ in range(2)] == ["n1", "n2"]
        finally:
            await store.stop()

    run_coro(run)
