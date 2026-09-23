"""Commit coalescing: under load at most one commit per interval, at low rates no wait.

Each commit rewrites the hot tail page of every index, so a commit per small batch wrote
the WAL at about 20x the captured bytes (owner ruling: above about 200 lines/s, at most
one commit per 100 ms).
"""

from __future__ import annotations

import asyncio
import time

from mcuscope import store as store_mod
from mcuscope.store import Store, _commit_hold


def test_the_hold_policy() -> None:
    interval, rate = store_mod._COMMIT_INTERVAL_S, store_mod._COALESCE_RATE
    assert _commit_hold(rate, 0.0, 1) == 0.0, "at the threshold rate a commit is immediate"
    assert abs(_commit_hold(rate * 5, 0.03, 1) - (interval - 0.03)) < 1e-9
    assert _commit_hold(rate * 5, interval + 0.01, 1) == 0.0
    assert _commit_hold(rate * 5, 0.0, store_mod._MAX_BATCH_ROWS) == 0.0, \
        "a full batch waiting is a backlog, and holding it caps throughput"


async def _counted(store: Store, run) -> tuple[int, float]:
    commits: list[str] = []
    store._conn.set_trace_callback(
        lambda s: commits.append(s) if s.strip().upper() == "COMMIT" else None
    )
    t0 = time.monotonic()
    try:
        await run()
        await store.drain_writes()
    finally:
        store._conn.set_trace_callback(None)
    return len(commits), time.monotonic() - t0


def _line(store: Store, raw: str) -> asyncio.Future:
    return store.submit_line_nowait(ts=time.time(), port="p", dir="rx", chan="debug",
                                    seq=None, raw=raw)


async def test_a_fast_stream_commits_at_most_once_per_interval(tmp_path) -> None:
    store = Store(str(tmp_path / "stream.db"))
    await store.start()
    try:
        async def stream() -> None:
            for burst in range(30):   # 20 lines every 20 ms: 1000 lines/s
                for i in range(20):
                    _line(store, f"{burst}.{i}")
                await asyncio.sleep(0.02)

        commits, elapsed = await _counted(store, stream)
        assert store.count_lines() == 600
        # Two bursts commit before the rate is known, and the last one after the stream.
        assert commits <= elapsed / store_mod._COMMIT_INTERVAL_S + 3, (commits, elapsed)
    finally:
        await store.stop()


async def test_a_slow_stream_is_never_held(tmp_path, monkeypatch) -> None:
    holds: list[float] = []
    real = store_mod._commit_hold
    monkeypatch.setattr(store_mod, "_commit_hold",
                        lambda *a: holds.append(real(*a)) or holds[-1])
    store = Store(str(tmp_path / "slow.db"))
    await store.start()
    try:
        for i in range(8):
            await _line(store, f"slow {i}")
            await asyncio.sleep(0.05)
        assert holds and not any(holds), holds
    finally:
        await store.stop()


async def test_a_caller_awaiting_each_row_is_not_held_for_rows_that_never_come(
    tmp_path, monkeypatch
) -> None:
    held: list[float] = []
    real = store_mod._commit_hold
    monkeypatch.setattr(store_mod, "_commit_hold",
                        lambda *a: (h := real(*a)) and held.append(h) or h)
    store = Store(str(tmp_path / "awaiter.db"))
    await store.start()
    try:
        t0 = time.monotonic()
        for i in range(60):
            await store.add_line(ts=time.time(), port="", dir="-", chan="marker", seq=None,
                                 raw=f"m{i}")
        elapsed = time.monotonic() - t0
        assert len(held) <= 1 + elapsed / store_mod._HOLD_BACKOFF_S, (len(held), elapsed)
    finally:
        await store.stop()
