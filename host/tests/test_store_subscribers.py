"""The store's live-row subscribers: the cap, the shed count a slow one is told, and the
stop sentinel (SPEC 3.4)."""

from __future__ import annotations

import asyncio

import pytest

from mcuscope.store import Store, StoreError
from tests.support import run_coro

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
