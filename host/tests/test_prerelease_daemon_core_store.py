"""Pre-release round, daemon core: store survivors F-10, F-11 and F-30, driven on the store."""

from __future__ import annotations

import asyncio

from tests.test_daemon_r2026_09_12_store import T0, _add, _started


def test_a_bare_carriage_return_is_folded_too(tmp_path) -> None:
    """F-10: every existing case also held a `\\n`, so the `\\r` half of the guard was inert."""

    async def run() -> None:
        store = await _started(tmp_path / "cr.db")
        try:
            row = await _add(store, T0, "a\rb")
            assert row["raw"] == "a b"
            stored, _ = store.query_lines(limit=1)
            assert stored[0]["raw"] == "a b"
        finally:
            await store.stop()

    asyncio.run(run())


def test_the_ceiling_walk_names_the_highest_id_not_the_newest_ts(tmp_path) -> None:
    """F-11: the walk branch runs only when the newest row is past the cutoff, which the
    clock-step fixture never had, so the `ORDER BY ts DESC` form (id 3 here) passed."""

    async def run() -> None:
        store = await _started(tmp_path / "walk.db")
        try:
            for i in range(5):
                await _add(store, T0 + i, f"before{i}")
            # the clock steps back
            after = [(await _add(store, T0 - 100 + i, f"after{i}"))["id"] for i in range(3)]
            await _add(store, T0 + 50, "newest")   # past the cutoff: forces the walk
            assert store._window_id_ceiling(T0 + 2) == after[-1]
            rows, _ = store.query_lines(until_ts=T0 + 2, limit=100, order="asc")
            assert [r["raw"] for r in rows] == [
                "before0", "before1", "before2", "after0", "after1", "after2",
            ]
        finally:
            await store.stop()

    asyncio.run(run())


def test_plot_ports_rebuilds_after_a_delete_on_its_own(tmp_path) -> None:
    """F-30: `/plot/channels` always rebuilt through `query_plot_channels_safe` first, so a
    stale summary in `plot_ports_safe` alone was never observed."""

    async def run() -> None:
        store = await _started(tmp_path / "ports.db")
        try:
            ids = {}
            for port in ("A", "B"):
                row = await store.add_line(
                    ts=T0, port=port, dir="rx", chan="event", seq=None, raw="!p v=1",
                    plot=[(1, None, "v", 1.0)],
                )
                ids[port] = row["id"]
            assert await store.plot_ports_safe() == ["A", "B"]
            await store.delete_range(ids["B"], ids["B"])
            assert await store.plot_ports_safe() == ["A"], "a purged board is still listed"
        finally:
            await store.stop()

    asyncio.run(run())
