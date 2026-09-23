"""CAN frame rows name the port they came from.

Two boards can both carry bus 1 and id 0x100: without the port, `mcu can dump` could not
say which board a frame came from.
"""

from __future__ import annotations

import time

from mcuscope.store import Store


def _frame(can_id: int) -> dict:
    return {"tick_ms": 1, "bus": 1, "can_id": can_id, "ext": False, "rtr": False,
            "dlc": 1, "data": b"\x01"}


async def test_every_frame_row_carries_its_lines_port(tmp_path) -> None:
    store = Store(str(tmp_path / "can.db"))
    await store.start()
    try:
        for port in ("left", "right", "left"):
            await store.add_line(ts=time.time(), port=port, dir="rx", chan="event", seq=None,
                                 raw="!can 1 100 1 01", can=_frame(0x100))
        rows, _ = store.query_can_frames(limit=10, order="asc")
        assert [r["port"] for r in rows] == ["left", "right", "left"]
        rows, _ = store.query_can_frames(port="right", limit=10)
        assert [r["port"] for r in rows] == ["right"]
        assert [r["port"] for r in store.iter_can_export()] == ["left", "right", "left"]

        seen: list[str] = []
        store._conn.set_trace_callback(seen.append)
        store.query_can_frames(can_id=0x100, limit=10)
        store._conn.set_trace_callback(None)
        plan = [str(r[3]) for r in store._conn.execute("EXPLAIN QUERY PLAN " + seen[-1])]
        assert any("idx_can_id_line" in r for r in plan), plan
        assert any("l USING INTEGER PRIMARY KEY" in r for r in plan), plan
        assert not any("TEMP B-TREE" in r for r in plan), plan
    finally:
        await store.stop()
