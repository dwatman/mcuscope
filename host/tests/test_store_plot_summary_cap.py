"""The plot channel summary keeps the `ADHOC_NAMES_MAX` most recent ad-hoc names per port
(OP-2, REVIEW class N4). The decoder's cap starts empty on every daemon run, so this is the
bound across restarts; typed-stream names and other ports are not counted against it."""

from __future__ import annotations

import time

import pytest

from mcuscope import protocol as p
from mcuscope.store import Store

CAP = p.ADHOC_NAMES_MAX


async def _plot(store: Store, port: str, names: list[str], sid: str | None = None) -> None:
    for name in names:
        await store.add_line(ts=time.time(), port=port, dir="rx", chan="event", seq=None,
                             raw=f"!p 0 {name}=1", plot=[(0, sid, name, 1.0)])


async def _names(store: Store, port: str) -> set[str]:
    return {c["name"] for c in await store.query_plot_channels_safe(port)}


async def test_the_summary_keeps_the_newest_adhoc_names_across_a_restart(tmp_path) -> None:
    db = str(tmp_path / "c.db")
    store = Store(db)
    await store.start()
    try:
        await _plot(store, "b", [f"run1_{i}" for i in range(CAP + 20)])
        await _plot(store, "b", [f"typed_{i}" for i in range(5)], sid="s1")
        await _plot(store, "other", ["elsewhere"])
        names = await _names(store, "b")
        # Typed names count against the same per-port bound (REVIEW class 95).
        assert len(names) == CAP and {f"typed_{i}" for i in range(5)} <= names
        assert "run1_0" not in names and f"run1_{CAP + 19}" in names
    finally:
        await store.stop()

    store = Store(db)          # a new run: the decoder admits CAP new names again
    await store.start()
    try:
        rebuilt = await _names(store, "b")
        assert rebuilt == names, "the rebuild disagrees with the live summary"
        await _plot(store, "b", [f"run2_{i}" for i in range(CAP)])
        names = await _names(store, "b")
        assert names == {f"run2_{i}" for i in range(CAP)}   # the typed names were older
        assert await _names(store, "other") == {"elsewhere"}
        # The points behind a dropped name are still stored and readable by name.
        assert store._conn.execute(
            "SELECT COUNT(*) FROM plot_points WHERE name = 'run1_0'").fetchone()[0] == 1
        store._plot_dirty = True    # what a delete does: the next read rebuilds from SQL
        assert await _names(store, "b") == names
    finally:
        await store.stop()


async def _counts(store: Store, port: str) -> dict[str, int]:
    return {c["name"]: c["count"] for c in await store.query_plot_channels_safe(port)}


@pytest.mark.parametrize("dropped_by", ["live", "restart"])
async def test_a_dropped_name_that_returns_counts_its_older_points(tmp_path, dropped_by) -> None:
    db = str(tmp_path / "c.db")
    store = Store(db)
    await store.start()
    try:
        await _counts(store, "b")                 # the startup rebuild, before any points
        names = [f"a{i}" for i in range(CAP)]
        await _plot(store, "b", names)
        await _plot(store, "b", names)
        await _plot(store, "b", ["new"])          # drops a0, the least recent
        assert "a0" not in await _counts(store, "b")
        if dropped_by == "restart":
            await store.stop()
            store = Store(db)
            await store.start()
            assert "a0" not in await _counts(store, "b")   # the rebuild drops it too
        await _plot(store, "b", ["a0"])
        live = await _counts(store, "b")
        assert live["a0"] == 3
        store._plot_dirty = True
        assert await _counts(store, "b") == live
    finally:
        await store.stop()
