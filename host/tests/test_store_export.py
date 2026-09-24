"""Store-side session exports: an in-memory capture, and a failing export surfacing its own
error (SPEC 3.4)."""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from mcuscope.store import Store


def test_an_in_memory_capture_can_still_be_exported(tmp_path) -> None:
    # `iter_plot_export` falls back to the loop connection for an in-memory capture, and
    # StreamingResponse advances the generator on a worker thread - where sqlite3 refuses a
    # connection opened with check_same_thread=True and raises ProgrammingError before a
    # single row is yielded. The docstring claimed the fallback worked, and the response had
    # already sent its 200 and its headers by then, so `/plot/export` answered with a
    # truncated body rather than an error.
    #
    # Consumed on a worker thread here, which is the only place the defect exists: drained
    # on the loop, both versions pass.
    async def run() -> None:
        for db_path in (":memory:", str(tmp_path / "export.db")):
            store = Store(db_path)
            await store.start()
            try:
                for i in range(1, 4):
                    fut = await store.submit_line(
                        ts=time.time(), port="p", dir="rx", chan="event", seq=None,
                        raw=f"!p {i} v={i}",
                        plot=[(i, None, "v", float(i))],
                    )
                await fut

                rows = await store.open_plot_export(names=["v"])
                out = await asyncio.to_thread(list, rows)
                assert [r["line_id"] for r in out] == [1, 2, 3], f"{db_path}: {out}"
                assert [r["value"] for r in out] == [1.0, 2.0, 3.0]
            finally:
                await store.stop()

    asyncio.run(run())


def test_export_failure_surfaces_its_own_error(tmp_path) -> None:
    """The finally ran DETACH inside the open transaction, which raises and replaced the
    real insert error with "cannot DETACH database within transaction"."""

    async def run() -> str:
        store = Store(str(tmp_path / "src.db"))
        await store.start()
        row = await store.add_line(ts=time.time(), port="A", dir="rx", chan="debug",
                                   seq=None, raw="hello")
        await store.stop()
        return row["id"]

    line_id = asyncio.run(run())
    bad_session = {
        "id": 1, "name": object(),   # unbindable: fails the sessions INSERT mid-transaction
        "note": None, "started_ts": 0.0, "ended_ts": None,
        "start_id": line_id, "end_id": line_id, "auto": 0,
    }
    store = Store(str(tmp_path / "src.db"))
    # Named by the message, not the class: the masking DETACH error is a sqlite3.Error too.
    with pytest.raises(sqlite3.Error, match="binding parameter"):
        store.export_session_db(
            str(tmp_path / "out.db"), id_from=line_id, id_to=line_id, session=bad_session
        )
