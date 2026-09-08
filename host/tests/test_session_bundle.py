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
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx

from mcuscope.config import resolve_db_path
from tests.support import Stack

DEF = "!pd 3 mode:u1:=0=IDLE,1=ARMED,2=RUN volts:s2*0.01:V io:u1:/led,irq"
CAN = "!can 100 - 123 DEADBEEF"


def client(stack: Stack) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=30.0)


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
    with client(stack) as c:
        c.post("/marker", json={"text": "before the run"})
        sid = c.post("/sessions", json={"name": name}).json()["session"]["id"]
        feed(stack, DEF, sample(1, 0, 100, 1), sample(2, 2, 250, 2), "!p 3 adhoc=1.5")
        if with_can:
            feed(stack, CAN)
        c.post("/sessions/stop")
        c.post("/marker", json={"text": "after the run"})
    return stack, sid


def bundle(stack: Stack, sid: int) -> tuple[httpx.Response, zipfile.ZipFile]:
    with client(stack) as c:
        r = c.get(f"/sessions/{sid}/bundle")
    assert r.status_code == 200, r.text
    return r, zipfile.ZipFile(io.BytesIO(r.content))


def session_raws(stack: Stack, sid: int) -> list[str]:
    with client(stack) as c:
        rows = c.get("/lines", params={"session": str(sid), "limit": 10000, "order": "asc"})
    return [r["raw"] for r in rows.json()["lines"]]


# -- refusal ---------------------------------------------------------------------------


def test_unknown_session_is_refused_in_the_export_endpoints_words(stack: Stack) -> None:
    with client(stack) as c:
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
        "capture.db", "lines.txt", "plot_3.csv", "plot_adhoc.csv", "can.csv", "manifest.json",
    ]
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["files"] == names, "the manifest lists exactly what the zip holds"
    assert manifest["session"] == "run/1" and manifest["id"] == sid
    assert manifest["from_ts"] is not None and manifest["to_ts"] is not None
    with client(stack) as c:
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

    rows = zf.read("plot_3.csv").decode().splitlines()
    # Every channel of the stream, in definition order, with the decoded lane labels.
    assert rows[0] == "ts,tick_ms,mode,volts,io.led,io.irq"
    # Lane values are the stored floats, as /plot/export?decode=1 renders them.
    assert rows[1].split(",")[2:] == ["IDLE", "1.0", "1.0", "0.0"]
    assert rows[2].split(",")[2:] == ["RUN", "2.5", "0.0", "1.0"]
    # Only this stream's points: `changes=0`, so every sample is a row.
    assert len(rows) == 3

    adhoc = zf.read("plot_adhoc.csv").decode().splitlines()
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
    make_stack: Callable[..., Stack],
) -> None:
    stack, sid = recorded(make_stack())
    with client(stack) as c, c.stream("GET", f"/sessions/{sid}/bundle") as r:
        assert r.status_code == 200
        during = temp_files(stack)
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
