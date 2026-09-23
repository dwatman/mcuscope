"""The size cap's trim at startup records its loss in the capture, as the per-minute one does.

Lowering `max_db_bytes` and restarting is the ordinary way to trigger the largest trim a
capture sees, and it went only to the daemon log (SPEC 3.2 asks for a `sys` row).
"""

from __future__ import annotations

import time

from mcuscope.store import Store


async def test_the_startup_trim_writes_its_sys_row(tmp_path) -> None:
    path = str(tmp_path / "cap.db")
    store = Store(path)
    await store.start()
    for i in range(600):
        await store.submit_line(
            ts=time.time(), port="board", dir="rx", chan="debug", seq=None, raw=f"{i} " + "x" * 900
        )
    await store.drain_writes()
    await store.stop()

    store = Store(path)
    await store.start(max_db_bytes=128 * 1024)
    try:
        await store._initial_sweep_task
        assert store.lines_trimmed > 0
        rows, _ = store.query_lines(chans=["sys"], limit=10)
        trims = [r["raw"] for r in rows if r["raw"].startswith("storage: trimmed")]
        assert trims == [
            f"storage: trimmed {store.lines_trimmed} oldest lines "
            f"to stay under the {128 * 1024} byte cap"
        ], trims
    finally:
        await store.stop()


async def test_a_startup_with_nothing_to_trim_writes_no_row(tmp_path) -> None:
    store = Store(str(tmp_path / "small.db"))
    await store.start(max_db_bytes=64 * 1024 * 1024)
    try:
        await store._initial_sweep_task
        assert store.query_lines(chans=["sys"], limit=10)[0] == []
    finally:
        await store.stop()
