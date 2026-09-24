"""`GET /sessions/{ref}/bundle`: one run as a zip (SPEC 3.4).

Lines are fed into the running daemon's port rather than taken from the simulator, for the
reason test_plot_export_decode.py gives: every assertion here is about a particular set of
streams and frames, which a free-running sim cannot be asked for.
"""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx

from mcuscope import server
from mcuscope import store as store_mod
from mcuscope.config import resolve_db_path
from tests.support import Stack, on_loop, stack_client

DEF = "!pd 3 mode:u1:=0=IDLE,1=ARMED,2=RUN volts:s2*0.01:V io:u1:/led,irq"
CAN = "!can 100 - 123 DEADBEEF"


def feed(stack: Stack, *lines: str) -> None:
    """Store `lines` as received on the stack's port, in order, before returning."""
    ports = stack.app.state.ports
    port = ports._ports[stack.alias]
    for line in lines:
        asyncio.run_coroutine_threadsafe(
            port._store_rx_line(time.time(), line), ports._loop
        ).result(10)


def sample(tick: int, mode: int, volts: int, io: int) -> str:
    return f"!ps 3 {tick:X} {mode:02X},{volts & 0xFFFF:04X},{io:02X}"


def db_dir(stack: Stack) -> Path:
    return Path(resolve_db_path(stack.app.state.config)).parent


def hold_temp_file_body(monkeypatch) -> threading.Event:
    """Park every `_TempFileResponse` before its first body message until the event is set.

    The temp file is unlinked once the body is sent, and a small one is sent in the same
    tick as the headers, so a client listing the directory after the status line races
    the unlink.
    """
    gate = threading.Event()
    real = server._TempFileResponse.__call__

    async def held(self, scope, receive, send):
        async def gated(message):
            if message["type"] == "http.response.body":
                await asyncio.to_thread(gate.wait, 10)
            await send(message)

        await real(self, scope, receive, gated)

    monkeypatch.setattr(server._TempFileResponse, "__call__", held)
    return gate


def temp_files(stack: Stack) -> list[Path]:
    return sorted(db_dir(stack).glob("mcuscope-*"))


def wait_no_temp_files(stack: Stack, timeout: float = 5.0) -> list[Path]:
    # The unlink runs server-side after the last body byte, so the client sees the end of
    # the download first. Poll rather than sleep, and return what is left.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        left = temp_files(stack)
        if not left:
            return left
        time.sleep(0.02)
    return temp_files(stack)


def recorded(stack: Stack, name: str = "run/1", *, with_can: bool = True) -> tuple[Stack, int]:
    """Record one closed session carrying a typed stream, ad-hoc points and (optionally) CAN."""
    with stack_client(stack) as c:
        # The sim's standing CAN traffic would land in any session open past its period.
        for bus in ("can", "can2"):
            r = c.post("/cmd", json={"cmd": f"{bus} filter none"}).json()
            assert r["status"] == "ok", r
        c.post("/marker", json={"text": "before the run"})
        sid = c.post("/sessions", json={"name": name}).json()["session"]["id"]
        feed(stack, DEF, sample(1, 0, 100, 1), sample(2, 2, 250, 2), "!p 3 adhoc=1.5")
        if with_can:
            feed(stack, CAN)
        c.post("/sessions/stop")
        c.post("/marker", json={"text": "after the run"})
    return stack, sid


def bundle(stack: Stack, sid: int) -> tuple[httpx.Response, zipfile.ZipFile]:
    with stack_client(stack) as c:
        r = c.get(f"/sessions/{sid}/bundle")
    assert r.status_code == 200, r.text
    return r, zipfile.ZipFile(io.BytesIO(r.content))


def session_raws(stack: Stack, sid: int) -> list[str]:
    with stack_client(stack) as c:
        rows = c.get("/lines", params={"session": str(sid), "limit": 10000, "order": "asc"})
    return [r["raw"] for r in rows.json()["lines"]]


# -- refusal ---------------------------------------------------------------------------


def test_unknown_session_is_refused_in_the_export_endpoints_words(stack: Stack) -> None:
    with stack_client(stack) as c:
        exp = c.get("/sessions/nope/export")
        bun = c.get("/sessions/nope/bundle")
    assert bun.status_code == exp.status_code
    assert bun.json()["error"] == exp.json()["error"] == "no such session: nope"


# -- contents --------------------------------------------------------------------------


def test_bundle_contents_and_manifest(make_stack: Callable[..., Stack]) -> None:
    stack, sid = recorded(make_stack())
    r, zf = bundle(stack, sid)

    assert r.headers["content-type"] == "application/zip"
    disp = r.headers["content-disposition"]
    assert "run_1_bundle_" in disp and disp.endswith('.zip"'), disp
    assert zf.testzip() is None, "a truncated or corrupt member"

    names = zf.namelist()
    assert names == [
        "capture.db", "lines.txt", "plot_board_3.csv", "plot_board_adhoc.csv", "can.csv",
        "manifest.json",
    ]
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["files"] == names, "the manifest lists exactly what the zip holds"
    assert manifest["session"] == "run/1" and manifest["id"] == sid
    assert manifest["from_ts"] is not None and manifest["to_ts"] is not None
    with stack_client(stack) as c:
        assert manifest["daemon_version"] == c.get("/status").json()["version"]


def test_capture_db_holds_exactly_the_session(make_stack: Callable[..., Stack], tmp_path) -> None:
    stack, sid = recorded(make_stack())
    _r, zf = bundle(stack, sid)
    out = tmp_path / "capture.db"
    out.write_bytes(zf.read("capture.db"))

    conn = sqlite3.connect(str(out))
    conn.row_factory = sqlite3.Row
    raws = [r["raw"] for r in conn.execute("SELECT raw FROM lines ORDER BY id")]
    conn.close()
    assert raws == session_raws(stack, sid)
    assert "before the run" not in raws and "after the run" not in raws


def test_lines_txt_covers_every_line_of_the_session(make_stack: Callable[..., Stack]) -> None:
    stack, sid = recorded(make_stack())
    _r, zf = bundle(stack, sid)
    text = zf.read("lines.txt").decode()
    lines = text.splitlines()
    assert len(lines) == len(session_raws(stack, sid))
    # Undecoded: the definition and the samples are there as captured, not as values.
    assert any(DEF in ln for ln in lines)
    assert any(sample(1, 0, 100, 1) in ln for ln in lines)
    assert not any("mode=" in ln or "IDLE " in ln for ln in lines)


def test_a_wide_decoded_csv_per_stream_and_a_long_one_for_adhoc(
    make_stack: Callable[..., Stack],
) -> None:
    stack, sid = recorded(make_stack())
    _r, zf = bundle(stack, sid)

    rows = zf.read("plot_board_3.csv").decode().splitlines()
    # Every channel of the stream, in definition order, with the decoded lane labels.
    assert rows[0] == "ts,tick_ms,mode,volts,io.led,io.irq"
    # Lane values are the stored floats, as /plot/export?decode=1 renders them.
    assert rows[1].split(",")[2:] == ["IDLE", "1.0", "1", "0"]
    assert rows[2].split(",")[2:] == ["RUN", "2.5", "0", "1"]
    # Only this stream's points: `changes=0`, so every sample is a row.
    assert len(rows) == 3

    adhoc = zf.read("plot_board_adhoc.csv").decode().splitlines()
    assert adhoc[0] == "ts,tick_ms,sid,name,value"
    assert adhoc[1].endswith(",3,,adhoc,1.5"), adhoc[1]
    assert len(adhoc) == 2


def test_can_csv_only_when_the_session_carried_frames(
    make_stack: Callable[..., Stack],
) -> None:
    quiet, quiet_sid = recorded(make_stack(), with_can=False)
    _r, zf = bundle(quiet, quiet_sid)
    assert "can.csv" not in zf.namelist()
    assert json.loads(zf.read("manifest.json"))["files"] == zf.namelist()

    noisy, noisy_sid = recorded(make_stack())
    _r2, zf2 = bundle(noisy, noisy_sid)
    can = zf2.read("can.csv").decode().splitlines()
    assert can[0].startswith("id,ts,")
    assert len(can) == 2 and "DEADBEEF" in can[1].upper()


# -- the temp file ---------------------------------------------------------------------


def test_the_bundle_leaves_no_temp_file_behind(make_stack: Callable[..., Stack]) -> None:
    stack, sid = recorded(make_stack())
    _r, _zf = bundle(stack, sid)
    # Both the zip and the session db copy it is built from, which lives only inside build().
    assert wait_no_temp_files(stack) == []


def test_a_disconnected_bundle_download_removes_the_temp_file(
    make_stack: Callable[..., Stack], monkeypatch
) -> None:
    stack, sid = recorded(make_stack())
    gate = hold_temp_file_body(monkeypatch)
    with stack_client(stack) as c, c.stream("GET", f"/sessions/{sid}/bundle") as r:
        assert r.status_code == 200
        during = temp_files(stack)
        gate.set()
        next(r.iter_bytes())          # one chunk, then close the connection unread
    assert len(during) == 1, during
    assert wait_no_temp_files(stack) == []


# -- repeatability ---------------------------------------------------------------------


def test_two_bundles_of_one_session_hold_the_same_files(
    make_stack: Callable[..., Stack],
) -> None:
    # A second export must not pick up rows that arrived after the session closed, nor
    # re-order the members: the run is over, so the bundle is fixed.
    stack, sid = recorded(make_stack())
    _r1, first = bundle(stack, sid)
    feed(stack, sample(9, 1, 300, 3), CAN)
    _r2, second = bundle(stack, sid)

    assert first.namelist() == second.namelist()
    assert [i.file_size for i in first.infolist()] == [i.file_size for i in second.infolist()]
    for name in first.namelist():
        if name != "capture.db":       # sqlite page layout is not the contract; its rows are
            assert first.read(name) == second.read(name), name


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
    with stack_client(stack) as c:
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
    with stack_client(stack) as c:
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
    with stack_client(stack) as c:
        sid = c.post("/sessions", json={"name": "two-streams"}).json()["session"]["id"]
        feed(stack, DEF, two, sample(1, 0, 100, 1), "!ps 4 2 0BB8")
        c.post("/sessions/stop")
    _r, zf = bundle(stack, sid)
    members = _members(zf)
    assert "plot_board_3.csv" in members and "plot_board_4.csv" in members, list(members)
    # Stream 3's sample carries volts=1.0, stream 4's carries 30.0. Neither file may hold
    # the other's row: the columns would be right and the values would be another board's.
    assert len(members["plot_board_3.csv"]) == 1 and len(members["plot_board_4.csv"]) == 1
    # Compare value cells, not the row text: the wall-clock ts column can contain "1.0".
    three = members["plot_board_3.csv"][0].split(",")[2:]
    four = members["plot_board_4.csv"][0].split(",")[2:]
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
    with stack_client(stack) as c:
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
    with stack_client(stack) as c:
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
        with stack_client(stack) as c:
            purge_done.append(c.post("/purge", json={"id_from": lo, "id_to": hi}))

    monkeypatch.setattr(store_mod.Store, "export_session_db", held)
    t = threading.Thread(target=purge, daemon=True)
    t.start()
    with stack_client(stack) as c:
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


# -- A-4: the bundle re-reads its session under the lock -----------------------------


def _hold_sweep_lock(stack: Stack) -> None:
    on_loop(stack, stack.app.state.store._sweep_lock.acquire())


def _bundle_in_thread(stack: Stack, ref: str, out: list) -> threading.Thread:
    def go() -> None:
        with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
            out.append(c.get(f"/sessions/{ref}/bundle"))

    t = threading.Thread(target=go, daemon=True)
    t.start()
    lock = stack.app.state.store._sweep_lock
    deadline = time.monotonic() + 10
    while not lock._waiters:
        assert time.monotonic() < deadline, "the bundle never queued on the lock"
        time.sleep(0.01)
    return t


def test_a_bundle_queued_behind_its_sessions_deletion_is_refused(
    make_stack: Callable[..., Stack],
) -> None:
    stack = make_stack()
    store = stack.app.state.store
    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        sid = c.post("/sessions", json={"name": "racer"}).json()["session"]["id"]
        c.post("/marker", json={"text": "inside"})
        c.post("/sessions/stop")
    _hold_sweep_lock(stack)
    out: list = []
    t = _bundle_in_thread(stack, str(sid), out)

    async def delete_then_release() -> None:
        store.delete_session(sid)
        store._sweep_lock.release()

    on_loop(stack, delete_then_release())
    t.join(30)
    assert out, "the bundle never returned"
    assert out[0].status_code == 400, out[0].text[:200]
    assert out[0].json()["error"] == f"no such session: {sid}"


def test_an_open_sessions_bundle_takes_its_span_after_the_wait(
    make_stack: Callable[..., Stack],
) -> None:
    stack = make_stack()
    store = stack.app.state.store
    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        sid = c.post("/sessions", json={"name": "waiting"}).json()["session"]["id"]
    _hold_sweep_lock(stack)
    out: list = []
    t = _bundle_in_thread(stack, str(sid), out)

    async def mark_then_release() -> int:
        row = await store.add_line(ts=time.time(), port="", dir="-", chan="marker",
                                   seq=None, raw="landed during the wait")
        store._sweep_lock.release()
        return row["id"]

    marker_id = on_loop(stack, mark_then_release())
    t.join(30)
    assert out and out[0].status_code == 200, out
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(out[0].content)).read("manifest.json"))
    assert manifest["to_id"] >= marker_id, (manifest, marker_id)
