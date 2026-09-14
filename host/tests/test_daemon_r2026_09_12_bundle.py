"""`GET /sessions/{ref}/bundle` after the 2026-09-12 round: D2, survivors V18 and V19, and
the purge that could land inside a build.

Shares `test_session_bundle`'s helpers: these need a real daemon, because every assertion
is about what the members agree on while the capture moves under them.
"""

from __future__ import annotations

import io
import json
import sqlite3
import threading
import time
import zipfile
from collections.abc import Callable

from mcuscope import store as store_mod
from tests.support import Stack
from tests.test_session_bundle import (
    CAN,
    DEF,
    bundle,
    client,
    db_dir,
    feed,
    recorded,
    sample,
    wait_no_temp_files,
)


def _members(zf: zipfile.ZipFile) -> dict[str, list[str]]:
    """Every text member as its list of data rows (header dropped for the CSVs)."""
    out: dict[str, list[str]] = {}
    for name in zf.namelist():
        if name == "capture.db" or name == "manifest.json":
            continue
        rows = zf.read(name).decode().splitlines()
        out[name] = rows[1:] if name.endswith(".csv") else rows
    return out


def _db_counts(blob: bytes, tmp_path) -> dict[str, int]:
    path = tmp_path / "member.db"
    path.write_bytes(blob)
    conn = sqlite3.connect(str(path))
    try:
        return {
            t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
            for t in ("lines", "can_frames", "plot_points")
        }
    finally:
        conn.close()


# -- D2: one frozen span for every member ----------------------------------------------


def test_an_open_sessions_members_all_cover_the_same_span(
    make_stack: Callable[..., Stack], tmp_path
) -> None:
    """`capture.db` covered a wider id span than every other member of the same zip.

    `hi` is frozen on the loop and every other member uses it; `export_session_db` was
    handed `session["end_id"]`, which is None while the session runs, and re-resolved it to
    MAX(id) inside the worker thread seconds later. The drift is the build time, so it
    scales with the session - measured 354 lines in `capture.db` against 310 in
    `lines.txt` from a bundle that took under a second.
    """
    stack = make_stack()
    with client(stack) as c:
        sid = c.post("/sessions", json={"name": "open-run"}).json()["session"]["id"]
        feed(stack, DEF, sample(1, 0, 100, 1), sample(2, 2, 250, 2), CAN)

        # Lines keep arriving while the zip is built, exactly as they do on a live bench.
        stop = threading.Event()

        def noise() -> None:
            i = 0
            while not stop.is_set():
                feed(stack, sample(10 + i, 1, 300 + i, 1))
                i += 1

        t = threading.Thread(target=noise, daemon=True)
        t.start()
        try:
            r = c.get(f"/sessions/{sid}/bundle")
        finally:
            stop.set()
            t.join(10)
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    manifest = json.loads(zf.read("manifest.json"))
    members = _members(zf)
    counts = _db_counts(zf.read("capture.db"), tmp_path)

    assert counts["lines"] == len(members["lines.txt"]), (
        "capture.db and lines.txt must cover one span, not two"
    )
    assert counts["can_frames"] == len(members["can.csv"])
    assert manifest["from_id"] and manifest["to_id"], manifest
    assert manifest["to_id"] - manifest["from_id"] + 1 >= counts["lines"]

    # And the span is in the manifest, which is otherwise the one thing the zip does not
    # record about an open session (its `end_id` is still null).
    path = tmp_path / "member.db"
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("SELECT start_id, end_id FROM sessions").fetchone()
    finally:
        conn.close()
    assert row[0] == manifest["from_id"]
    assert row[1] is None, "the session is still open in the copy, as it is in the capture"


def test_a_closed_sessions_manifest_names_its_span(make_stack: Callable[..., Stack]) -> None:
    stack, sid = recorded(make_stack())
    _r, zf = bundle(stack, sid)
    manifest = json.loads(zf.read("manifest.json"))
    with client(stack) as c:
        session = c.get("/sessions", params={"name": "run/1"}).json()["sessions"][0]
    assert manifest["from_id"] == session["start_id"]
    assert manifest["to_id"] == session["end_id"]


# -- V18: a member holds its own stream's rows only ------------------------------------


def test_a_name_two_streams_declare_lands_in_one_member_each(
    make_stack: Callable[..., Stack],
) -> None:
    """Survivor V18. `_stream_rows` exists because the plot export is port-unscoped, so a
    name another stream also uses would otherwise land in this file too. No bundle test
    declared the same channel name in two streams, so the filter was revertible in silence.
    """
    stack = make_stack()
    two = "!pd 4 volts:s2*0.01:V"
    with client(stack) as c:
        sid = c.post("/sessions", json={"name": "two-streams"}).json()["session"]["id"]
        feed(stack, DEF, two, sample(1, 0, 100, 1), "!ps 4 2 0BB8")
        c.post("/sessions/stop")
    _r, zf = bundle(stack, sid)
    members = _members(zf)
    assert "plot_3.csv" in members and "plot_4.csv" in members, list(members)
    # Stream 3's sample carries volts=1.0, stream 4's carries 30.0. Neither file may hold
    # the other's row: the columns would be right and the values would be another board's.
    assert len(members["plot_3.csv"]) == 1 and len(members["plot_4.csv"]) == 1
    # Compare value cells, not the row text: the wall-clock ts column can contain "1.0".
    three = members["plot_3.csv"][0].split(",")[2:]
    four = members["plot_4.csv"][0].split(",")[2:]
    assert "1.0" in three and "30.0" not in three, three
    assert "30.0" in four and "1.0" not in four, four


# -- V19: a failed build leaves nothing behind -----------------------------------------


def test_a_failed_build_removes_both_temp_files(
    make_stack: Callable[..., Stack], monkeypatch
) -> None:
    """Survivor V19, and the coverage gap beside it. The two temp-file tests drive success
    and a client disconnect; the build-failure path had neither a test nor coverage, and it
    is the path that writes a zip and a session db and then has to remove both.
    """
    stack, sid = recorded(make_stack())

    def boom(*_a, **_kw):
        raise RuntimeError("disk went away")

    monkeypatch.setattr(store_mod.Store, "export_session_db", boom)
    with client(stack) as c:
        r = c.get(f"/sessions/{sid}/bundle")
    assert r.status_code == 400
    assert "export failed" in r.json()["error"]
    assert wait_no_temp_files(stack) == [], "a bundle or session temp file was left behind"
    assert not list(db_dir(stack).glob("mcuscope-bundle-*"))
    assert not list(db_dir(stack).glob("mcuscope-session-*"))


# -- a purge cannot land inside a build ------------------------------------------------


def test_a_purge_of_the_span_waits_for_a_bundle_in_progress(
    make_stack: Callable[..., Stack], tmp_path, monkeypatch
) -> None:
    """The members are drained at different moments, the last of them seconds into the
    build, and nothing held `_sweep_lock` across that: a purge or a retention sweep could
    delete rows out of `[lo, hi]` mid-build, leaving the members disagreeing with each
    other and with the manifest - silently, since each one is individually well formed.
    """
    stack, sid = recorded(make_stack())
    with client(stack) as c:
        before = c.get("/lines", params={"session": str(sid), "limit": 1000}).json()["lines"]
    assert before, "the session must hold lines for the purge to race"
    lo, hi = before[-1]["id"], before[0]["id"]

    # Hold the build open until the purge is queued behind it, so the purge is certain to
    # arrive inside the build. Where the bundle does not hold the lock the purge never
    # queues, runs at once, and the row counts below fail.
    real_export = store_mod.Store.export_session_db
    started = threading.Event()
    lock = stack.app.state.store._sweep_lock

    def held(self, *a, **kw):
        started.set()
        deadline = time.monotonic() + 5
        while not lock._waiters and time.monotonic() < deadline:
            time.sleep(0.01)
        return real_export(self, *a, **kw)

    purge_done: list = []

    def purge() -> None:
        started.wait(10)
        with client(stack) as c:
            purge_done.append(c.post("/purge", json={"id_from": lo, "id_to": hi}))

    monkeypatch.setattr(store_mod.Store, "export_session_db", held)
    t = threading.Thread(target=purge, daemon=True)
    t.start()
    with client(stack) as c:
        r = c.get(f"/sessions/{sid}/bundle")
    t.join(30)

    assert r.status_code == 200, r.text
    assert purge_done, "the purge never returned"
    assert purge_done[0].status_code == 200, purge_done[0].text
    assert purge_done[0].json()["deleted"] > 0, "the purge must really have run"

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    members = _members(zf)
    counts = _db_counts(zf.read("capture.db"), tmp_path)
    assert counts["lines"] == len(before), "the zip lost rows the purge took mid-build"
    assert len(members["lines.txt"]) == len(before)


