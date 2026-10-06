"""The plot summary keeps the `ADHOC_NAMES_MAX` most recent names per port, typed (`!ps`)
names included (REVIEW class 95), live and across a rebuild."""

from __future__ import annotations

import time

from mcuscope import protocol as p
from mcuscope.store import Store


async def test_typed_plot_names_are_bounded_per_port_like_ad_hoc_ones(tmp_path) -> None:
    store = Store(str(tmp_path / "plot.db"))
    await store.start()
    try:
        n = p.ADHOC_NAMES_MAX + 40
        futs = [store.submit_line_nowait(
            ts=time.time(), port="A", dir="rx", chan="event", seq=None, raw=f"!ps 1 {i} 1",
            plot=[(i, "1", f"ch{i}", 1.0)]) for i in range(n)]
        futs.append(store.submit_line_nowait(
            ts=time.time(), port="B", dir="rx", chan="event", seq=None, raw="!ps 1 0 1",
            plot=[(0, "1", "other", 1.0)]))
        for f in futs:
            await f
        newest = {f"ch{i}" for i in range(40, n)}
        assert {k[1] for k in store._plot_summary if k[0] == "A"} == newest
        assert {k[1] for k in store._plot_summary if k[0] == "B"} == {"other"}
        store._plot_dirty = True
        names = {c["name"] for c in await store.query_plot_channels_safe("A")}
        assert names == newest
    finally:
        await store.stop()


async def test_a_new_name_under_the_cap_scans_no_other_port(tmp_path, monkeypatch) -> None:
    """Pruning runs only for a port past the cap, so its cost does not grow with the
    number of ports (a scan of every port's names per new name was 8 s of writer CPU at
    64 ports)."""
    from mcuscope import store as store_mod

    calls: list[str | None] = []
    real = store_mod._keep_recent_names
    monkeypatch.setattr(store_mod, "_keep_recent_names",
                        lambda summary, port=None: calls.append(port) or real(summary, port))
    store = Store(str(tmp_path / "plot.db"))
    await store.start()
    try:
        def point(port: str, name: str):
            return store.submit_line_nowait(
                ts=time.time(), port=port, dir="rx", chan="event", seq=None,
                raw=f"!p {name}=1", plot=[(None, "", name, 1.0)])

        futs = [point(f"P{i}", f"n{j}") for i in range(64) for j in range(4)]
        futs += [point("A", f"ch{i}") for i in range(p.ADHOC_NAMES_MAX)]
        for f in futs:
            await f
        assert calls == [], "a name under the cap pruned"
        await point("A", "one-more")
        assert calls == ["A"]
        assert sum(1 for k in store._plot_summary if k[0] == "A") == p.ADHOC_NAMES_MAX
    finally:
        await store.stop()
