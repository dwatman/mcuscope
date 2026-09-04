"""The scope a handler answers over must be the scope it judged (SPEC 3.4).

One request is one window, one page bound is a real bound, and a live-only surface refuses
a scope only a live object can satisfy. Each test drives a defect that reads as a correct
answer: a CSV shorter than the count that let it through, a pattern judged over a window
its neighbour never saw, an empty page claiming more rows follow, a stop that stopped
nothing.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time as _time

import httpx
import pytest
import websockets

from tests.support import Stack


def client(stack: Stack) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=20.0)


def _on_loop(stack: Stack, coro, timeout: float = 10.0):
    return asyncio.run_coroutine_threadsafe(coro, stack.app.state.ports._loop).result(timeout)


class FloorClock:
    """The real `time` module, but `time()` jumps 10 s for each `_window_floor` call.

    Only that caller is advanced: the writer, the sessions table and the retention loop
    read the same module, and a clock that moved under them would decide the test.
    """

    def __init__(self, base: float) -> None:
        self._t = base

    def __getattr__(self, name: str):
        return getattr(_time, name)

    def time(self) -> float:
        if sys._getframe(1).f_code.co_name != "_window_floor":
            return _time.time()
        now = self._t
        self._t += 10.0
        return now


# -- one window per request -----------------------------------------------------------


def test_plot_export_streams_the_window_its_count_guarded(stack: Stack, monkeypatch) -> None:
    # The count is what refuses an over-large selection, so a CSV built over a later window
    # is short in a way no byte of it reveals.
    store = stack.app.state.store
    base = _time.time()
    for age in (20.0, 15.0, 10.0, 5.0, 0.0):
        _on_loop(stack, store.add_line(
            ts=base - age, port=stack.alias, dir="rx", chan="debug", seq=None,
            raw=f"!p scope_t={age}",
            plot=[{"tick_ms": 0, "sid": None, "name": "scope_t", "value": age}],
        ))
    monkeypatch.setattr("mcuscope.store.time", FloorClock(base))
    with client(stack) as c:
        r = c.get("/plot/export", params={"names": "scope_t", "last_ms": 12000})
    assert r.status_code == 200, r.text
    rows = [ln for ln in r.text.splitlines()[1:] if ln]
    # Rows at 10 s, 5 s and 0 s old; the 15 s and 20 s ones are outside a 12 s window.
    assert len(rows) == 3, r.text


def test_a_retrospective_assert_judges_every_pattern_over_one_window(
    stack: Stack, monkeypatch
) -> None:
    store = stack.app.state.store
    base = _time.time()
    _on_loop(stack, store.add_line(
        ts=base - 1.5, port=stack.alias, dir="rx", chan="debug", seq=None, raw="ZZSCOPEWINDOW ok",
    ))
    monkeypatch.setattr("mcuscope.store.time", FloorClock(base))
    with client(stack) as c:
        r = c.post("/assert", json={
            "expect": ["ZZSCOPEWINDOW", "SCOPEWINDOW ok"], "timeout_ms": 0, "last_ms": 2000,
        }).json()
    # Both patterns match the same line, so a differing verdict is the window moving.
    assert [e["matched"] for e in r["expect"]] == [True, True], r
    assert r["status"] == "pass", r


# -- a page bound is a bound ----------------------------------------------------------


@pytest.mark.parametrize("path", ["/lines", "/can/frames", "/sessions"])
def test_a_negative_limit_is_refused(stack: Stack, path: str) -> None:
    with client(stack) as c:
        assert c.get(path, params={"limit": -5}).status_code == 422, path


def test_a_negative_plot_series_limit_is_refused(stack: Stack) -> None:
    with client(stack) as c:
        assert c.get("/plot/series", params={"name": "sine", "limit": -5}).status_code == 422


def test_limit_zero_is_still_the_no_backfill_probe(stack: Stack) -> None:
    # `mcu tail -f` sends it to say "stream from here"; `truncated` is how it learns rows
    # exist behind it. Bounding the parameter must not take that away.
    with client(stack) as c:
        assert c.post("/marker", json={"port": stack.alias, "text": "ZZPROBE"}).status_code == 200
        r = c.get("/lines", params={"limit": 0}).json()
    assert r["lines"] == []
    assert r["truncated"] is True


# -- a field the path never reads -----------------------------------------------------


def test_wait_refuses_an_eol_with_nothing_to_send(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/wait", json={"match": "ZZNEVER", "timeout_ms": 200, "eol": "crlf"})
    assert r.status_code == 400
    assert r.json()["error"] == "eol applies to send; set send too"


def test_assert_refuses_an_eol_with_nothing_to_send(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/assert", json={"expect": ["daemon"], "eol": "crlf"})
    assert r.status_code == 400
    assert r.json()["error"] == "eol applies to send; set send too"


# -- stop is a matched pair -----------------------------------------------------------


def test_two_concurrent_stops_give_exactly_one_success(stack: Stack, monkeypatch) -> None:
    """The loser must be refused, not handed a success envelope with no session in it.

    The first stop is slowed so both requests are certainly inside the handler at once:
    the race is real but its window is one scheduling slot wide, and a test that has to
    win a coin toss to see the defect does not pin it.
    """
    store = stack.app.state.store
    real_stop = store.stop_session
    calls: list[int] = []

    async def slow_first_stop():
        calls.append(1)
        if len(calls) == 1:
            await asyncio.sleep(0.3)
        return await real_stop()

    monkeypatch.setattr(store, "stop_session", slow_first_stop)

    results: list[httpx.Response] = []
    lock = threading.Lock()
    with client(stack) as c:
        assert c.post("/sessions", json={"name": "zzrace"}).status_code == 200

        def stop() -> None:
            r = httpx.post(stack.base_url + "/sessions/stop", timeout=20.0)
            with lock:
                results.append(r)

        first = threading.Thread(target=stop)
        first.start()
        _time.sleep(0.05)
        second = threading.Thread(target=stop)
        second.start()
        for t in (first, second):
            t.join(timeout=20.0)

    codes = sorted(r.status_code for r in results)
    assert codes == [200, 400], [r.text for r in results]
    for r in results:
        if r.status_code == 200:
            # A success envelope carrying no session is the shape this endpoint must never
            # answer with: `session start` / `session stop` is a matched pair.
            assert r.json()["session"] is not None, r.text
            assert r.json()["session"]["name"] == "zzrace", r.text
        else:
            assert r.json()["error"] == "no session is running", r.text


# -- a live-only scope ----------------------------------------------------------------


async def test_ws_refuses_a_port_no_attached_alias_can_satisfy(stack: Stack) -> None:
    base = stack.base_url.replace("http", "ws")
    with pytest.raises(websockets.exceptions.ConnectionClosed) as exc:
        async with websockets.connect(base + "/ws?port=ZZZ_nope") as ws:
            await asyncio.wait_for(ws.recv(), 5.0)
    assert exc.value.rcvd is not None and exc.value.rcvd.code == 1008

    # The attached alias still opens and still carries its rows.
    async with websockets.connect(base + f"/ws?port={stack.alias}") as ws:
        frame = json.loads(await asyncio.wait_for(ws.recv(), 5.0))
    assert isinstance(frame, list) and frame
