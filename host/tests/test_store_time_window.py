"""Time-bounded reads and `purge before_ts` when `ts` is not monotonic in `id`.

Rows are stamped before they queue for the writer, so a row can take a higher id than one
stamped after it (two ports, or a marker racing a backlogged port). The windows must stay
exact while that inversion is below `WINDOW_TS_SLACK_S`, and a purge by age must select by
`ts`, not by an id range standing in for it.
"""

from __future__ import annotations

import random
import time

from mcuscope.store import WINDOW_TS_SLACK_S, Store

T0 = time.time() - 3600   # inside the default retention, so the startup sweep leaves it


async def _store(tmp_path, stamps: list[float]) -> Store:
    store = Store(str(tmp_path / "window.db"))
    await store.start()
    for i, ts in enumerate(stamps):
        await store.add_line(ts=ts, port="p", dir="rx", chan="debug", seq=None, raw=f"r{i}")
    return store


def _exact(store: Store, keep) -> list[int]:
    rows, _ = store.query_lines(limit=1000, order="asc")
    return [r["id"] for r in rows if keep(r["ts"])]


async def test_a_row_stamped_after_the_floor_but_committed_first_is_kept(tmp_path) -> None:
    # The shape an id floor sought at the first row at or after `cutoff - slack` gets
    # wrong: nothing is stamped in the slack interval, so that seek lands on the late row
    # (id 2) and the early-committed row id 1, inside the window, falls below the bound.
    store = await _store(tmp_path, [T0 - 60, T0 + 0.5, T0])
    try:
        cut = T0 + 0.2
        got = [r["raw"] for r in store.query_lines(since_ts=cut, limit=100, order="asc")[0]]
        assert got == ["r1"], got
        got = [r["raw"] for r in store.query_lines(
            floor_ts=cut, limit=100, order="asc")[0]]
        assert got == ["r1"], got
    finally:
        await store.stop()


async def test_every_cutoff_matches_the_exact_ts_filter_under_inversions(tmp_path) -> None:
    # Two interleaved "ports" whose stamps run up to 1.5 s out of id order, with quiet gaps
    # longer than the slack, checked at every distinct cutoff in and around the data.
    rng = random.Random(7)
    stamps: list[float] = []
    t = T0
    for _ in range(12):
        t += rng.choice((0.3, 2.0, WINDOW_TS_SLACK_S * 3))
        stamps.extend(t + rng.uniform(-1.5, 1.5) for _i in range(rng.randint(1, 15)))
    store = await _store(tmp_path, stamps)
    try:
        pairs = zip(stamps, stamps[1:], strict=False)
        assert any(a > b for a, b in pairs), "no inversion was built"
        cuts = sorted(set(stamps)) + [T0 - 1, max(stamps) + 1]
        for cut in cuts + [c + 1e-4 for c in cuts]:
            got = [r["id"] for r in store.query_lines(since_ts=cut, limit=1000, order="asc")[0]]
            assert got == _exact(store, lambda ts, c=cut: ts > c), cut
            got = [r["id"] for r in store.query_lines(floor_ts=cut, limit=1000, order="asc")[0]]
            assert got == _exact(store, lambda ts, c=cut: ts >= c), cut
            assert store.count_lines(floor_ts=cut) == len(_exact(store, lambda ts, c=cut: ts >= c))
    finally:
        await store.stop()


async def test_the_id_floor_is_a_bound_the_empty_window_can_seek_to(tmp_path) -> None:
    # Past everything by more than the slack, the floor is one past the newest id, so an
    # empty poll reads nothing; inside the slack it drops back to the rows that could be
    # in the window.
    store = await _store(tmp_path, [T0, T0 + 1, T0 + 2])
    try:
        assert store._window_id_floor(T0 + 2 + WINDOW_TS_SLACK_S + 1) == 4
        assert store._window_id_floor(T0 + 2) == 1
        assert store.query_lines(since_ts=T0 + 2, limit=10)[0] == []
    finally:
        await store.stop()


async def test_purge_before_ts_deletes_by_age_not_by_an_id_range(tmp_path) -> None:
    # id 2 is new and sits below old rows; ids 3 and 4 are old and sit above it. Deleting
    # the id range up to the newest old row would take ids 1-4, new id 2 among them.
    cut = T0 + 100
    store = await _store(tmp_path, [T0, cut + 5, T0 + 50, T0 + 60, cut + 1, cut + 2])
    try:
        span = await store.before_ts_span_safe(cut)
        assert span == (3, 1, 4), span
        assert store.before_ts_span(cut) == span
        deleted = await store.delete_before_ts(cut)
        assert deleted == 3 == span[0], "the dry-run count is not what the purge deleted"
        left = [(r["id"], r["raw"]) for r in store.query_lines(limit=100, order="asc")[0]]
        assert left == [(2, "r1"), (5, "r4"), (6, "r5")], left
        assert await store.before_ts_span_safe(cut) == (0, None, None)
        assert await store.delete_before_ts(cut) == 0
    finally:
        await store.stop()


async def test_purge_before_ts_goes_past_one_chunk(tmp_path, monkeypatch) -> None:
    import mcuscope.store as store_mod

    monkeypatch.setattr(store_mod, "_RETENTION_CHUNK", 3)
    store = await _store(tmp_path, [T0 + i for i in range(10)] + [T0 + 1000])
    try:
        assert await store.delete_before_ts(T0 + 500) == 10
        assert [r["raw"] for r in store.query_lines(limit=100)[0]] == ["r10"]
    finally:
        await store.stop()
