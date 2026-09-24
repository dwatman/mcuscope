"""CAN frame rows name the port they came from.

Two boards can both carry bus 1 and id 0x100: without the port, `mcu can dump` could not
say which board a frame came from.
"""

from __future__ import annotations

import asyncio
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


def test_can_frames_filters_each_select_what_they_name(tmp_path) -> None:
    # Every /can/frames filter but `can_id` shipped with no test at all: the coverage leg
    # listed port, since_id and last_ms together as untested shipped paths. A wrong filter
    # here is invisible from the outside - it returns frames, just not the right ones.
    async def run() -> None:
        store = Store(str(tmp_path / "canfilt.db"))
        await store.start()
        try:
            ids: list[int] = []
            now = time.time()
            for port, can_id, ts in (("boardA", 0x100, now - 3600),
                                     ("boardB", 0x200, now),
                                     ("boardA", 0x300, now)):
                row = await store.add_line(
                    ts=ts, port=port, dir="rx", chan="event", seq=None,
                    raw=f"!can 1 - {can_id:X} AA",
                    can={"tick_ms": 1, "bus": 1, "can_id": can_id, "ext": False, "rtr": False,
                         "dlc": 1, "data": b"\xaa"},
                )
                ids.append(row["id"])

            def frames(**kwargs) -> list[int]:
                rows, _ = store.query_can_frames(**kwargs)
                return [f["can_id"] for f in rows]

            assert frames() == [0x300, 0x200, 0x100]              # newest first
            assert frames(port="boardA") == [0x300, 0x100]
            assert frames(port="boardB") == [0x200]
            assert frames(port="nosuchport") == []
            assert frames(since_id=ids[0]) == [0x300, 0x200]      # strictly after that line
            assert frames(since_id=ids[2]) == []
            assert frames(last_ms=60_000) == [0x300, 0x200]       # the hour-old frame is out
            assert frames(can_id=0x300) == [0x300]
            assert frames(id_from=ids[1], id_to=ids[1]) == [0x200]
            # Combined, the filters intersect rather than replace one another.
            assert frames(port="boardA", last_ms=60_000) == [0x300]
            assert frames(port="boardB", since_id=ids[2]) == []
            # And the truncation flag tracks the limit, not the filter.
            rows, truncated = store.query_can_frames(limit=2)
            assert len(rows) == 2 and truncated is True
            rows, truncated = store.query_can_frames(port="boardB", limit=2)
            assert len(rows) == 1 and truncated is False
        finally:
            await store.stop()

    asyncio.run(run())
