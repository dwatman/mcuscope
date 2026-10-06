"""A read narrowed to one board also carries the daemon's own rows, port '' (OP-7): markers,
session boundaries and daemon notices. A verdict's default scope (rx rows) still gets none
of them (REVIEW class 84), and port '' alone stays exact."""

from __future__ import annotations

import time

from mcuscope.store import Store


async def _capture(tmp_path) -> Store:
    store = Store(str(tmp_path / "c.db"))
    await store.start()
    rows = [("sim", "rx", "debug", "board line"), ("", "-", "marker", "NOPORT1"),
            ("other", "rx", "debug", "other line"), ("sim", "-", "marker", "PM1"),
            ("sim", "tx", "cmd", "ping"), ("", "-", "sys", "daemon start")]
    for port, dir_, chan, raw in rows:
        await store.add_line(ts=time.time(), port=port, dir=dir_, chan=chan, seq=None, raw=raw)
    return store


def _raws(rows) -> list[str]:
    return [r["raw"] for r in rows]


async def test_a_board_read_includes_the_daemons_rows_and_no_other_board(tmp_path) -> None:
    store = await _capture(tmp_path)
    try:
        for order in ("desc", "asc"):
            rows, _ = store.query_lines(port="sim", limit=100, order=order)
            want = ["board line", "NOPORT1", "PM1", "ping", "daemon start"]
            assert _raws(rows) == (want if order == "asc" else want[::-1])
        rows, _ = store.query_lines(port="sim", chans=["marker"], limit=100, order="asc")
        assert _raws(rows) == ["NOPORT1", "PM1"]
        assert store.count_lines(port="sim") == 5
        assert store.count_lines(port="sim", chans=["marker"]) == 2
        # The limit is applied after the merge: the newest rows of both ports.
        rows, truncated = store.query_lines(port="sim", limit=2)
        assert _raws(rows) == ["daemon start", "ping"] and truncated
        exported = [r["raw"] for r in store.iter_lines_export(port="sim")]
        assert exported == ["board line", "NOPORT1", "PM1", "ping", "daemon start"]
    finally:
        await store.stop()


async def test_a_verdicts_rx_scope_and_the_daemon_port_stay_exact(tmp_path) -> None:
    store = await _capture(tmp_path)
    try:
        rows, _ = store.query_lines(port="sim", dir="rx", limit=100)
        assert _raws(rows) == ["board line"]
        assert store.count_lines(port="sim", dir="rx") == 1
        rows, _ = store.query_lines(port="sim", dir="tx", limit=100)
        assert _raws(rows) == ["ping"]
        rows, _ = store.query_lines(port="", limit=100, order="asc")
        assert _raws(rows) == ["NOPORT1", "daemon start"]
        assert store.count_lines(port="") == 2
    finally:
        await store.stop()


async def test_a_board_feed_carries_the_daemons_rows(tmp_path) -> None:
    store = await _capture(tmp_path)
    try:
        q = store.subscribe("sim")
        own = store.subscribe("")
        for port, raw in (("other", "o"), ("", "mark"), ("sim", "s")):
            await store.add_line(ts=time.time(), port=port, dir="-", chan="marker", seq=None,
                                 raw=raw)
        got = [q.get_nowait()["raw"] for _ in range(q.qsize())]
        assert got == ["mark", "s"]
        assert [own.get_nowait()["raw"] for _ in range(own.qsize())] == ["mark"]
    finally:
        await store.stop()


async def test_a_verdict_scope_takes_only_the_named_port(tmp_path) -> None:
    # A verdict's `chan` scope (dir None) would otherwise count the daemon's markers and
    # sys rows on a silent board (REVIEW class 84).
    store = await _capture(tmp_path)
    try:
        scope = {"port": "sim", "chans": ["marker"], "dir": None, "own_rows": False}
        rows, _ = store.query_lines(limit=100, **scope)
        assert _raws(rows) == ["PM1"]
        assert store.count_lines(**scope) == 1
        q = store.subscribe("sim", own_rows=False)
        await store.add_line(ts=time.time(), port="", dir="-", chan="marker", seq=None,
                             raw="mark")
        await store.add_line(ts=time.time(), port="sim", dir="-", chan="marker", seq=None,
                             raw="s")
        assert [q.get_nowait()["raw"] for _ in range(q.qsize())] == ["s"]
    finally:
        await store.stop()
