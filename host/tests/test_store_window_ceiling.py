"""A `last_ms` window has a ceiling as well as a floor (RES-4, REVIEW class 77): rows stamped
ahead of the window's anchor (a clock stepped back since) are not "the last N ms"."""

from __future__ import annotations

import time

import pytest

from mcuscope.store import WINDOW_TS_SLACK_S, Store


async def _rows(tmp_path) -> tuple[Store, int]:
    store = Store(str(tmp_path / "c.db"))
    await store.start()
    now = time.time()
    for i in range(3):   # captured before a 1 h step back: stamped an hour ahead of now
        await store.add_line(ts=now + 3600 + i, port="b", dir="rx", chan="debug", seq=None,
                             raw=f"pre-step {i}")
    last_pre = store.max_id()
    await store.add_line(ts=now + WINDOW_TS_SLACK_S / 2, port="b", dir="rx", chan="debug",
                         seq=None, raw="within slack")
    for i in range(3):
        await store.add_line(ts=now - i * 0.1, port="b", dir="rx", chan="debug", seq=None,
                             raw=f"post-step {i}")
    return store, last_pre


def _raws(rows) -> set[str]:
    return {r["raw"] for r in rows}


async def test_a_now_anchored_window_excludes_rows_stamped_ahead(tmp_path) -> None:
    store, _ = await _rows(tmp_path)
    try:
        rows, _ = store.query_lines(last_ms=3000, dir="rx", limit=100)
        assert _raws(rows) == {"within slack", "post-step 0", "post-step 1", "post-step 2"}
        assert store.count_lines(last_ms=3000, dir="rx") == 4
        # As /assert resolves it: the floor once, then id_to frozen at the newest line.
        floor = store._window_floor(3000, None)
        frozen = {"floor_ts": floor, "ceil_ts": store._window_anchor(None),
                  "id_to": store.max_id()}
        rows, _ = store.query_lines(limit=100, **frozen)
        assert not any(r.startswith("pre-step") for r in _raws(rows))
        assert store.count_lines(**frozen) == len(rows)
    finally:
        await store.stop()


async def test_a_window_anchored_at_a_bound_takes_its_ceiling_from_that_bound(tmp_path) -> None:
    # An ended session or a paused export: the window ends at its bound's newest line, so a
    # lower-id row stamped ahead of that line is outside it, however far behind now it is.
    store = Store(str(tmp_path / "c.db"))
    await store.start()
    try:
        now = time.time()
        for raw, ts in (("ahead of the bound", now - 20), ("bound -1", now - 50),
                        ("bound", now - 49)):
            await store.add_line(ts=ts, port="b", dir="rx", chan="debug", seq=None, raw=raw)
        rows, _ = store.query_lines(last_ms=1500, id_to=store.max_id(), dir="rx", limit=100)
        assert _raws(rows) == {"bound -1", "bound"}
    finally:
        await store.stop()


async def test_a_frozen_count_and_its_rows_share_one_ceiling(tmp_path) -> None:
    # count_lines drops an id bound that constrains nothing, but under a time window the
    # bound is also the ceiling's anchor: dropping it counted a row the query excluded.
    store = Store(str(tmp_path / "c.db"))
    await store.start()
    try:
        now = time.time()
        await store.add_line(ts=now - 1, port="b", dir="rx", chan="debug", seq=None,
                             raw="ahead of the newest line")
        store._late_rows = 1   # an episode already announced: no notice row lands above
        newest = await store.add_line(ts=now - 12, port="b", dir="rx", chan="debug",
                                      seq=None, raw="newest line")   # id floor keeps it
        assert newest["id"] == store.max_id()   # the bound count_lines would drop
        frozen = {"floor_ts": now - 3, "id_to": newest["id"], "dir": "rx",
                  "ceil_ts": store._window_anchor(newest["id"])}
        rows, _ = store.query_lines(limit=100, **frozen)
        assert rows == []
        assert store.count_lines(**frozen) == 0
    finally:
        await store.stop()


_READERS = {
    "lines": lambda s, w: s.query_lines(limit=100, **w)[0],
    "count": lambda s, w: s.count_lines(**w),
    "can": lambda s, w: s.query_can_frames(**w)[0],
    "plot_first": lambda s, w: s.first_export_line_id(names=["v"], **w),
    "plot_sids": lambda s, w: s.export_sids(names=["v"], **w),
    "plot_export": lambda s, w: list(s.iter_plot_export(names=["v"], **w)),
}


@pytest.mark.parametrize("reader", sorted(_READERS))
async def test_a_frozen_window_keeps_the_ceiling_its_floor_was_measured_from(
    tmp_path, reader
) -> None:
    # Nothing arrived since a clock step back, so the freeze lands on a pre-step row
    # stamped ahead of now: the ceiling must stay at the anchor the floor came from.
    store = Store(str(tmp_path / "c.db"))
    await store.start()
    try:
        now = time.time()
        for i in range(5):
            await store.add_line(
                ts=now + 3600 + i, port="b", dir="rx", chan="event", seq=None, raw=f"pre {i}",
                plot=[(0, None, "v", 1.0)],
                can={"tick_ms": 0, "bus": 0, "can_id": 1, "ext": False, "rtr": False,
                     "dlc": 0, "data": ""})
        anchor = store._window_anchor(None)
        frozen = {"floor_ts": anchor - 60.0, "id_to": store.max_id()}
        # Control: a ceiling derived from the freeze lets them in, so the store refuses to
        # derive one itself.
        assert _READERS[reader](store, {**frozen, "ceil_ts": store._window_anchor(frozen["id_to"])})
        with pytest.raises(ValueError, match="floor_ts needs the ceil_ts"):
            _READERS[reader](store, frozen)
        assert not _READERS[reader](store, {**frozen, "ceil_ts": anchor})
    finally:
        await store.stop()
