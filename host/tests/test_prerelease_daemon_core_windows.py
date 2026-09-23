"""Pre-release round, daemon core: one window per request (A-1, A-2, A-9, A-10), the bundle
that waited behind its own deletion (A-4), and `/status` `now`.

App-level tests use `test_export_lines_can`'s file-backed app, so the offloaded reads really
leave the loop; the bundle race needs the threaded stack.
"""

from __future__ import annotations

import asyncio
import io
import json
import threading
import time
import zipfile
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from mcuscope import store as store_mod
from mcuscope.store import Store
from tests.support import Stack
from tests.test_daemon_r2026_09_12_server import _disposition, _stamps
from tests.test_export_lines_can import T0, _add, _can, _mk_app


def _on_loop(target, coro):
    """Run `coro` on the daemon loop of a TestClient or a Stack."""
    return asyncio.run_coroutine_threadsafe(coro, target.app.state.ports._loop).result(10)


@pytest.fixture
def client(tmp_path):
    with TestClient(_mk_app(tmp_path), base_url="http://127.0.0.1") as c:
        yield c


def _plot_line(client, ts: float, value: float, port: str = "board") -> dict:
    store = client.app.state.store
    return _on_loop(client, store.add_line(
        ts=ts, port=port, dir="rx", chan="event", seq=None, raw=f"!p v={value}",
        plot=[(1, None, "v", value)],
    ))


def _stamp(ts: float) -> str:
    return time.strftime("%Y%m%dT%H%M%S", time.localtime(ts))


# -- A-1: the until_ts ceiling is resolved once per request --------------------------


def test_every_page_of_an_export_reuses_one_until_ts_ceiling(client, monkeypatch) -> None:
    """The ceiling is an index walk the length of the window, and every 1000-row page
    re-derived it: 292.7 s against 11.3 s for the same bytes at 1M rows."""
    for i in range(7):
        _plot_line(client, T0 + i, float(i))
        _can(client, T0 + i, 0x100)
    monkeypatch.setattr(store_mod, "_EXPORT_PAGE", 2)
    calls: list[float] = []
    real = Store._window_id_ceiling

    def spy(self, until_ts, conn=None):
        calls.append(until_ts)
        return real(self, until_ts, conn)

    monkeypatch.setattr(Store, "_window_id_ceiling", spy)
    until = T0 + 4.5   # inside the capture: the walk, not the newest-row fast path
    cases = (
        ("/lines/export", {"format": "jsonl"}, 10),
        ("/can/frames", {"format": "csv"}, 5),
        ("/plot/export", {"names": "v", "format": "wide"}, 5),
        ("/lines", {"limit": 1000}, None),
    )
    for path, params, rows in cases:
        calls.clear()
        r = client.get(path, params={"until_ts": until, **params})
        assert r.status_code == 200, f"{path}: {r.text}"
        assert len(calls) == 1, f"{path} derived the ceiling {len(calls)} times"
        if rows is not None:
            body = [ln for ln in r.text.splitlines() if ln and not ln.startswith(("id,", "ts,"))]
            assert len(body) == rows, f"{path}: {r.text}"


def test_lines_with_until_ts_runs_off_the_loop(client, monkeypatch) -> None:
    _add(client, ts=T0, raw="line0")

    async def ident() -> int:
        return threading.get_ident()

    loop_thread = _on_loop(client, ident())
    threads: list[int] = []
    real = Store.query_lines

    def spy(self, **kw):
        threads.append(threading.get_ident())
        return real(self, **kw)

    monkeypatch.setattr(Store, "query_lines", spy)
    r = client.get("/lines", params={"until_ts": T0 + 1})
    assert [x["raw"] for x in r.json()["lines"]] == ["line0"]
    assert threads and loop_thread not in threads, "the until_ts read ran on the loop"
    threads.clear()
    client.get("/lines")
    assert threads == [loop_thread], "a plain poll stays inline"


def test_the_folded_ceiling_keeps_every_row_after_a_clock_step(client) -> None:
    """The fold must not undo D6: an `until_ts` above every stored ts is the whole capture."""
    for i in range(3):
        _add(client, ts=T0 + i, raw=f"before{i}")
    for i in range(2):
        _add(client, ts=T0 - 100 + i, raw=f"after{i}")
    wide = client.get("/lines/export", params={"until_ts": T0 + 1e6, "format": "jsonl"})
    assert [json.loads(ln)["raw"] for ln in wide.text.splitlines()] == [
        "before0", "before1", "before2", "after0", "after1",
    ]
    early = client.get("/lines", params={"until_ts": T0 - 100, "order": "asc"}).json()
    assert [x["raw"] for x in early["lines"]] == ["after0"]


# -- A-2: an internal freeze is not the caller's bound -------------------------------


def test_last_ms_on_a_quiet_capture_counts_back_from_now_on_every_export(client) -> None:
    """A board quiet for an hour exported that hour-old tail as "the last minute"."""
    old = time.time() - 3600
    for i in range(3):
        _add(client, ts=old + i, raw=f"old{i}")
        _plot_line(client, old + i, float(i))
        _can(client, old + i, 0x100)
    before = time.time()
    cases = (
        # `chan`: the daemon's own session-start row is recent.
        ("/lines/export", {"format": "csv", "chan": "debug"}, "id,ts,port,dir,chan,seq,raw"),
        ("/can/frames", {"format": "csv"}, "id,ts,tick_ms,bus,can_id,ext,rtr,dlc,data"),
        ("/plot/export", {"names": "v"}, "ts,tick_ms,sid,name,value"),
    )
    for path, params, header in cases:
        r = client.get(path, params={"last_ms": 60000, **params})
        assert r.status_code == 200, f"{path}: {r.text}"
        assert r.text.splitlines() == [header], f"{path} exported hour-old rows: {r.text}"
        lo, hi = _stamps(_disposition(r))
        assert hi == "end", _disposition(r)
        assert _stamp(before - 61) <= lo <= _stamp(time.time()), (path, lo)
    verdict = client.post("/assert", json={
        "forbid": ["old1"], "timeout_ms": 0, "last_ms": 60000, "chan": "debug",
    }).json()
    assert verdict["status"] == "empty" and verdict["checked_lines"] == 0, verdict


# -- A-9: a negative last_ms is refused, zero is a window ----------------------------


@pytest.mark.parametrize("path, extra", [
    ("/lines", {}), ("/lines/export", {}), ("/can/frames", {}),
    ("/plot/series", {"name": "v"}), ("/plot/export", {"names": "v"}),
])
def test_a_negative_last_ms_is_refused(client, path, extra) -> None:
    _plot_line(client, time.time(), 1.0)
    r = client.get(path, params={"last_ms": -60000, **extra})
    assert r.status_code == 422, f"{path}: {r.status_code} {r.text}"
    assert "last_ms" in r.text
    # A paused surface whose shown span rounds to 0 ms still gets its window.
    assert client.get(path, params={"last_ms": 0, **extra}).status_code == 200, path


# -- A-10: a window whose from is after its to ---------------------------------------


def _ended_session(client, name: str = "run") -> dict:
    store = client.app.state.store
    _on_loop(client, store.start_session(name))
    _plot_line(client, time.time(), 1.0)
    _can(client, time.time(), 0x100)
    return _on_loop(client, store.stop_session())


@pytest.mark.parametrize("path, extra", [
    ("/lines", {}), ("/lines/export", {}), ("/can/frames", {"format": "csv"}),
    ("/plot/export", {"names": "v"}),
])
def test_a_window_crossing_its_session_is_answered_and_names_no_backwards_file(
    client, path, extra
) -> None:
    """Session stamps and a `last_ms` floor are not row bounds, so crossing them is no
    refusal; an export's `<from>-<to>` then takes both sides from the upper bound."""
    session = _ended_session(client)
    start, end = session["started_ts"], session["ended_ts"]
    until = time.time() - 120
    crossings = (
        ({"session": "run", "until_ts": start - 1}, start - 1),
        ({"session": "run", "since_ts": end + 1}, end),
        ({"until_ts": until, "last_ms": 60000}, until),
    )
    for params, upper in crossings:
        r = client.get(path, params={**params, **extra})
        assert r.status_code == 200, f"{path} {params}: {r.status_code} {r.text}"
        if path != "/lines":
            assert _stamps(_disposition(r)) == (_stamp(upper), _stamp(upper)), (path, params)


# -- /status now -----------------------------------------------------------------------


def test_status_carries_the_daemon_clock(client) -> None:
    before = time.time()
    now = client.get("/status").json()["now"]
    assert isinstance(now, float) and before <= now <= time.time()


# -- A-4: the bundle re-reads its session under the lock -----------------------------


def _hold_sweep_lock(stack: Stack) -> None:
    _on_loop(stack, stack.app.state.store._sweep_lock.acquire())


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

    _on_loop(stack, delete_then_release())
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

    marker_id = _on_loop(stack, mark_then_release())
    t.join(30)
    assert out and out[0].status_code == 200, out
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(out[0].content)).read("manifest.json"))
    assert manifest["to_id"] >= marker_id, (manifest, marker_id)
