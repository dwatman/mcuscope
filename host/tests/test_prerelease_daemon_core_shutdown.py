"""Pre-release round, daemon core: the subscriber feed at shutdown (C-1, C-2, A-6, F-27).

`stop_subscribers` is driven on the loop in the same callback as the rows it races, so each
ordering is fixed rather than hoped for.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mcuscope.store import Store, StoreError
from tests.support import Stack
from tests.test_export_lines_can import T0, _mk_app

_HOST = {"host": "127.0.0.1"}   # the TestClient's default Host fails the allowlist


def _row(row_id: int, raw: str, port: str = "board") -> dict:
    return {"id": row_id, "ts": T0, "port": port, "dir": "rx", "chan": "debug",
            "seq": None, "raw": raw}


def _call_on_loop(target, fn) -> None:
    """Run the synchronous `fn` on the daemon loop, as one callback, and wait for it."""

    async def run() -> None:
        fn()

    asyncio.run_coroutine_threadsafe(run(), target.app.state.ports._loop).result(10)


def _await_subscriber(store: Store, before: int) -> None:
    deadline = time.monotonic() + 10
    while len(store._subscribers) <= before:
        assert time.monotonic() < deadline, "the handler never subscribed"
        time.sleep(0.01)


@pytest.fixture
def client(tmp_path):
    with TestClient(_mk_app(tmp_path), base_url="http://127.0.0.1") as c:
        yield c


# -- C-1 and C-2: the store side --------------------------------------------------------


def test_the_sentinel_survives_rows_committed_after_it(tmp_path) -> None:
    """The capture commits for up to the graceful wait after the sentinel, and drop-oldest
    shed the sentinel from a full queue: that `/wait` then parked into uvicorn's 500."""

    async def run() -> None:
        store = Store(str(tmp_path / "closed.db"))
        await store.start()
        try:
            q = store.subscribe(maxsize=2)
            store._broadcast_batch([_row(1, "a"), _row(2, "b")])
            store.stop_subscribers()
            store._broadcast_batch([_row(i, "late") for i in range(3, 8)])
            items = [q.get_nowait() for _ in range(q.qsize())]
            assert None in items, f"the sentinel was shed: {items}"
            assert all(i is None or i["raw"] != "late" for i in items), items
        finally:
            await store.stop()

    asyncio.run(run())


def test_a_subscriber_after_the_sentinel_is_refused_as_shutdown(tmp_path) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "late.db"))
        await store.start()
        try:
            store.subscribe()
            assert not store.subscribers_closed
            store.stop_subscribers()
            assert store.subscribers_closed
            with pytest.raises(StoreError, match="^daemon is shutting down"):
                store.subscribe()
        finally:
            await store.stop()

    asyncio.run(run())


# -- C-2: the endpoints answer a late subscriber ----------------------------------------


def test_wait_and_assert_arriving_after_the_sentinel_answer_503(client) -> None:
    _call_on_loop(client, client.app.state.store.stop_subscribers)
    wait = client.post("/wait", json={"match": "x", "timeout_ms": 20_000})
    live = client.post("/assert", json={"expect": ["x"], "timeout_ms": 20_000})
    for r in (wait, live):
        assert r.status_code == 503, r.text
        assert r.json()["error"].startswith("daemon is shutting down"), r.text
        assert "too many subscribers" not in r.text


def test_a_websocket_after_the_sentinel_closes_as_going_away(client) -> None:
    _call_on_loop(client, client.app.state.store.stop_subscribers)
    with client.websocket_connect("/ws", headers=_HOST) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 1001, exc.value.code
    assert exc.value.reason.startswith("daemon is shutting down"), exc.value.reason


# -- A-6 and F-27: rows queued ahead of the sentinel ------------------------------------


def test_the_websocket_sends_the_rows_ahead_of_the_sentinel_then_closes(client) -> None:
    store = client.app.state.store
    before = len(store._subscribers)
    with client.websocket_connect("/ws", headers=_HOST) as ws:
        _await_subscriber(store, before)
        _call_on_loop(client, lambda: (
            store._broadcast_batch([_row(10**6, "ZZ-last-words")]), store.stop_subscribers()
        ))
        frame = json.loads(ws.receive_text())
        assert any(r.get("raw") == "ZZ-last-words" for r in frame), frame
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 1000, "a clean close, not a pump that died on the sentinel"


def _post_in_thread(stack: Stack, path: str, body: dict, out: list) -> threading.Thread:
    def go() -> None:
        with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
            out.append(c.post(path, json=body))

    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


@pytest.mark.parametrize("path, body", [
    ("/wait", {"match": "ZZ-READY-A6", "timeout_ms": 20_000}),
    ("/assert", {"expect": ["ZZ-READY-A6"], "timeout_ms": 20_000}),
])
def test_a_match_queued_ahead_of_the_sentinel_is_still_judged(stack: Stack, path, body) -> None:
    """The whole drained batch was discarded when the sentinel was anywhere in it, so a
    match that arrived just before SIGTERM answered 503 "cut short" instead."""
    store = stack.app.state.store
    before = len(store._subscribers)
    out: list = []
    t = _post_in_thread(stack, path, body, out)
    _await_subscriber(store, before)
    _call_on_loop(stack, lambda: (
        store._broadcast_batch([_row(10**9, "ZZ-READY-A6", port=stack.alias)]),
        store.stop_subscribers(),
    ))
    t.join(15)
    assert out, "the call never returned"
    assert out[0].status_code == 200, out[0].text
    verdict = out[0].json()
    assert verdict["status"] in ("match", "pass"), verdict
