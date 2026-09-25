"""The export job: its temp copy is removed off the event loop (REVIEW class 1), after a
download and when a handler abandons a finished build; and a copy stopped at any point reports
the same "interrupted"."""

from __future__ import annotations

import asyncio
import os
import sqlite3

import pytest

from mcuscope import server as server_mod
from tests.test_e2e import poll
from tests.test_server_exports import _app, _client, _session_id


@pytest.fixture
def removals(monkeypatch) -> list[tuple[str, bool]]:
    seen: list[tuple[str, bool]] = []
    real = server_mod._remove_export_file

    def recording(path: str, live: set[str]) -> None:
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        real(path, live)
        seen.append((path, on_loop))   # after the removal: a poll on `seen` then sees it done

    monkeypatch.setattr(server_mod, "_remove_export_file", recording)
    return seen


def test_a_downloaded_session_export_is_removed_off_the_loop(tmp_path, removals) -> None:
    with _client(_app(tmp_path)) as c:
        r = c.get(f"/sessions/{_session_id(c)}/export")
        assert r.status_code == 200 and r.content.startswith(b"SQLite format 3")
        assert poll(lambda: removals, 5), "positive control: the copy was removed"
        assert [on_loop for _, on_loop in removals] == [False]
        assert not os.path.exists(removals[0][0])


def test_abandoning_a_finished_build_removes_its_file_off_the_loop(tmp_path, removals) -> None:
    live: set[str] = set()
    job = server_mod._ExportJob(live, str(tmp_path / "cap.db"), "k")
    path = job.run(lambda j: j.mkstemp("session", ".db"))
    assert os.path.exists(path) and removals == [], "positive control: a finished build keeps it"

    async def abandon_on_the_loop() -> None:
        job.abandon()

    asyncio.run(abandon_on_the_loop())
    assert poll(lambda: removals, 5), "the finished build's file was never removed"
    assert removals == [(path, False)]
    assert not os.path.exists(path) and live == set()


def test_an_abandon_landing_while_attach_opens_the_capture_reads_interrupted(tmp_path) -> None:
    with _client(_app(tmp_path)) as c:
        store = c.app.state.store
        session = {"id": 1, "name": "n", "note": "", "started_ts": 0.0, "ended_ts": None,
                   "start_id": 1, "end_id": None, "auto": 0}
        raw: list[str] = []
        for step in range(1, 12):
            job = server_mod._ExportJob(set(), str(tmp_path / "cap.db"), "k")
            state = {"attach": False, "calls": 0}

            def on_open(conn, job=job, state=state, step=step) -> None:
                job.on_open(conn)

                def trace(sql: str) -> None:
                    state["attach"] = sql.lstrip().upper().startswith("ATTACH")

                def stop() -> int:   # the abandon lands `step` VM steps into the ATTACH
                    if state["attach"]:
                        state["calls"] += 1
                        if state["calls"] == step:
                            job.abandon()
                    return int(job._abandoned)

                conn.set_trace_callback(trace)
                conn.set_progress_handler(stop, 1)

            def build(j, on_open=on_open) -> str:
                path = j.mkstemp("session", ".db")
                try:
                    store.export_session_db(path, id_from=1, id_to=None, session=session,
                                            on_open=on_open)
                except sqlite3.OperationalError as exc:
                    raw.append(str(exc))
                    raise
                return path

            with pytest.raises(sqlite3.OperationalError) as err:
                job.run(build)
            assert str(err.value) == "interrupted", (step, raw[-1])
        # Positive control: some of those stops did surface as a failed open.
        assert any(e.startswith("unable to open database") for e in raw), raw
