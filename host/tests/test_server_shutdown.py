"""The subscriber feed and parked calls at shutdown (SPEC 3.4): a call cut short answers 503,
and a row committed around the sentinel is delivered or counted.

`stop_subscribers` is driven on the loop in the same callback as the rows it races, so each
ordering is fixed rather than hoped for."""

from __future__ import annotations

import asyncio
import json
import signal
import threading
import time

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

from mcuscope.store import Store, StoreError
from tests.support import Stack
from tests.test_export_lines_can import T0

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


# -- improvement 7: a long poll cut short by shutdown says so --------------------------


def _await_stack_subscriber(stack, before: int) -> None:
    """Block until the handler has subscribed: a sentinel fired earlier tests the refusal
    of a late subscriber, not the parked watch these tests are about."""
    store = stack.app.state.store
    deadline = time.monotonic() + 10
    while len(store._subscribers) <= before:
        assert time.monotonic() < deadline, "the handler never subscribed"
        time.sleep(0.01)


def _wait_in_thread(base_url: str, timeout_ms: int, out: list) -> threading.Thread:
    def go() -> None:
        with httpx.Client(base_url=base_url, timeout=30.0) as c:
            out.append(c.post("/wait", json={"match": "never-arrives",
                                             "timeout_ms": timeout_ms}))

    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


def test_a_wait_cut_short_by_shutdown_is_a_503_not_a_timeout(stack) -> None:
    """The cheap wrong fix wakes the watcher and lets the handler report an ordinary
    timeout, which reads to an agent as "the board stayed silent" - a verdict over a
    window that was never run. Before the fix the handler was cancelled by uvicorn and the
    client saw `Internal Server Error` with exit 1, where SPEC 4 has exit 3 for this.
    """
    out: list = []
    before = len(stack.app.state.store._subscribers)
    t = _wait_in_thread(stack.base_url, 25_000, out)
    _await_stack_subscriber(stack, before)
    # Through uvicorn's exit hook, the path both `mcu daemon stop` and SIGTERM take: the
    # store's own stop() runs in the lifespan finaliser, which uvicorn reaches only after
    # its graceful wait has cancelled the parked handler into a 500 (fix-diff F1).
    loop = stack.app.state.ports._loop
    loop.call_soon_threadsafe(stack._server.handle_exit, signal.SIGTERM, None)
    t.join(15)
    assert out, "the wait never returned"
    r = out[0]
    assert r.status_code == 503, f"{r.status_code} {r.text}"
    assert r.json()["error"] == "daemon is shutting down; the wait was cut short"


def test_an_ordinary_timeout_is_still_a_200(stack) -> None:
    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        r = c.post("/wait", json={"match": "never-arrives", "timeout_ms": 150})
    assert r.status_code == 200
    assert r.json()["status"] == "timeout"


def test_an_assert_cut_short_by_shutdown_is_a_503(stack) -> None:
    out: list = []

    def go() -> None:
        with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
            out.append(c.post("/assert", json={"expect": ["never-arrives"],
                                               "timeout_ms": 25_000}))

    before = len(stack.app.state.store._subscribers)
    t = threading.Thread(target=go, daemon=True)
    t.start()
    _await_stack_subscriber(stack, before)
    # Through uvicorn's exit hook, the path both `mcu daemon stop` and SIGTERM take: the
    # store's own stop() runs in the lifespan finaliser, which uvicorn reaches only after
    # its graceful wait has cancelled the parked handler into a 500 (fix-diff F1).
    loop = stack.app.state.ports._loop
    loop.call_soon_threadsafe(stack._server.handle_exit, signal.SIGTERM, None)
    t.join(15)
    assert out and out[0].status_code == 503, out
    assert out[0].json()["error"] == "daemon is shutting down; the wait was cut short"


def test_the_row_the_sentinel_sheds_is_counted_as_dropped(tmp_path) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "shed.db"))
        await store.start()
        try:
            q = store.subscribe(maxsize=2)
            store._broadcast_batch([_row(1, "a"), _row(2, "b")])
            assert store.take_dropped(q) == 0
            store.stop_subscribers()
            items = [q.get_nowait() for _ in range(q.qsize())]
            assert [i and i["raw"] for i in items] == ["b", None], items
            assert store.take_dropped(q) == 1, "the shed row left no gap to announce"
            assert store.ws_dropped == 1, "the shed row is missing from the /status total"
        finally:
            await store.stop()

    asyncio.run(run())


def _sim_core(stack: Stack):
    return stack.sim.links[-1]._source._sim.sim


@pytest.mark.parametrize("mode", ["cmd", "raw"])
@pytest.mark.parametrize("path, body", [
    ("/wait", {"match": "never-ZZ65", "timeout_ms": 20_000, "send": "ping"}),
    ("/assert", {"expect": ["never-ZZ65"], "timeout_ms": 20_000, "send": "ping"}),
])
def test_a_call_parked_in_its_send_answers_503_at_the_sentinel(
    make_stack, path, body, mode
) -> None:
    stack = make_stack(["--drop-response", "1000000"])
    store = stack.app.state.store
    port = stack.app.state.ports.get(stack.alias)
    core = _sim_core(stack)
    deadline = time.monotonic() + 10
    while core.cmd_count < 1 or port._pending:   # the identify ping has come and gone
        assert time.monotonic() < deadline, "identify never finished"
        time.sleep(0.01)
    loop = stack.app.state.ports._loop
    if mode == "cmd":
        stack._sim_args.drop_response = core.cmd_count + 1   # swallow the call's own command
        parked = lambda: bool(port._pending)  # noqa: E731
    else:
        # A raw write parks behind the port's raw lock, as behind a flow-controlled write.
        asyncio.run_coroutine_threadsafe(port._raw_lock.acquire(), loop).result(5)
        parked = lambda: bool(port._raw_lock._waiters)  # noqa: E731
    body = {**body, "send_mode": mode}

    before = len(store._subscribers)
    out: list[httpx.Response] = []

    def go() -> None:
        with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
            out.append(c.post(path, json=body))

    t = threading.Thread(target=go, daemon=True)
    t.start()
    _await_subscriber(store, before)
    while not parked():   # the handler is now inside its send
        assert time.monotonic() < deadline + 10, "the send never started"
        time.sleep(0.01)
    _call_on_loop(stack, store.stop_subscribers)
    t.join(4)   # inside uvicorn's 5 s graceful wait
    if mode == "raw":
        _call_on_loop(stack, port._raw_lock.release)
    assert out, "the call stayed parked in its send past the sentinel"
    assert out[0].status_code == 503, out[0].text
    assert out[0].json()["error"] == "daemon is shutting down; the wait was cut short"
    assert not parked(), "the cancelled send is still parked"


@pytest.mark.parametrize("path, body", [
    ("/wait", {"match": ".", "timeout_ms": 5_000, "send": "ping"}),
    ("/assert", {"expect": ["."], "timeout_ms": 5_000, "send": "ping"}),
])
def test_a_send_that_completes_leaves_no_stop_waiter_behind(stack: Stack, path, body) -> None:
    """Each call races its send against the stop event; the losing wait must be cancelled,
    or every call with a send leaves a pending task parked on the event until shutdown."""
    store = stack.app.state.store
    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        r = c.post(path, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("match", "pass"), r.text
    deadline = time.monotonic() + 5
    while store._subscribers_stopped._waiters:
        assert time.monotonic() < deadline, "the stop waiter outlived the call"
        time.sleep(0.01)


def test_a_cmd_parked_at_shutdown_answers_the_shutdown_503(make_stack) -> None:
    """`POST /cmd` holds no subscription, so only the stop race can cut it short (class 65)."""
    import time

    import httpx

    stack = make_stack(["--drop-response", "1000000"])
    store = stack.app.state.store
    port = stack.app.state.ports.get(stack.alias)
    core = stack.sim.links[-1]._source._sim.sim
    deadline = time.monotonic() + 10
    while core.cmd_count < 1 or port._pending:   # the identify ping has come and gone
        assert time.monotonic() < deadline, "identify never finished"
        time.sleep(0.01)
    stack._sim_args.drop_response = core.cmd_count + 1   # swallow the call's own command
    out: list = []

    def go() -> None:
        with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
            out.append(c.post("/cmd", json={"cmd": "ping", "timeout_ms": 20_000}))

    t = threading.Thread(target=go, daemon=True)
    t.start()
    while not port._pending:
        assert time.monotonic() < deadline + 10, "the command never went out"
        time.sleep(0.01)
    _call_on_loop(stack, store.stop_subscribers)
    t.join(4)
    assert out, "the command stayed parked past the stop"
    assert out[0].status_code == 503, out[0].text
    assert out[0].json()["error"] == "daemon is shutting down; the command was cut short"
    assert not port._pending, "the cancelled command is still pending"


def test_a_cmd_that_completes_is_unchanged(stack) -> None:
    """Positive control: the race returns the command's own answer."""
    import httpx

    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        r = c.post("/cmd", json={"cmd": "ping", "timeout_ms": 5_000})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok", r.text
