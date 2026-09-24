"""The `/ws` live feed (SPEC 3.4): a disconnect releases its subscriber, the idle keepalive,
backpressure, and a dead pump closing the socket."""

from __future__ import annotations

import asyncio
import time

import pytest

from tests.support import mk_app

# -- server-level hardening --------------------------------------------------------------


def test_ws_disconnect_releases_subscriber_without_traffic(tmp_path) -> None:
    # A client that disconnects while no rows are flowing must not leak its queue.
    from fastapi.testclient import TestClient

    app = mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        with c.websocket_connect("/ws", headers={"host": "127.0.0.1"}):
            assert len(app.state.store._subscribers) == 1
        deadline = time.monotonic() + 5.0
        while app.state.store._subscribers and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not app.state.store._subscribers


def test_ws_sends_an_idle_keepalive_frame(tmp_path, monkeypatch) -> None:
    # With no rows flowing the daemon must still write periodically, so a client that
    # vanished without a TCP close is reaped instead of holding its queue indefinitely.
    from fastapi.testclient import TestClient

    from mcuscope import server as server_mod

    monkeypatch.setattr(server_mod, "WS_KEEPALIVE_S", 0.1)
    app = mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        with c.websocket_connect("/ws", headers={"host": "127.0.0.1"}) as ws:
            # The opening frame carries the capture identity and nothing else, because
            # this store is idle: a keepalive is how a silent target tells a reconnected
            # client its id space was replaced.
            first = ws.receive_json()
            assert [k for r in first for k in r] == ["capture"], first
            assert ws.receive_json() == []     # keepalive: an empty SPEC 3.4 frame
            assert ws.receive_json() == []     # and it repeats, so detection is bounded


def test_ws_backpressure_callbacks_are_wired() -> None:
    """The shed path only engages if `await websocket.send_text()` can actually block.

    uvicorn's websockets-sansio protocol writes every frame straight to the asyncio
    transport and gates its ASGI send on a `writable` Event it never clears, having no
    pause_writing of its own. Measured before the fix: one client that stopped reading at
    5018 lines/s held 1.34 MB of transport buffer after 20k rows with ws_dropped 0, the
    queue empty, and the keepalive close at t+40 s unable to flush it away.
    `_enable_ws_backpressure` wires the transport's flow-control callbacks to that Event.

    Pinned here: the callbacks are installed and drive `writable`, and uvicorn's send
    still gates on that Event (a source canary; the first version of this test drove the
    real protocol via __new__ and broke on the next uvicorn release over an unrelated
    internal attribute). The real-stack proof is the flood probe in REVIEW_LOG 2026-08-09.
    """
    import inspect
    from types import SimpleNamespace

    from uvicorn.protocols.websockets import websockets_sansio_impl as impl

    from mcuscope.server import _enable_ws_backpressure

    _enable_ws_backpressure()
    proto = impl.WebSocketsSansIOProtocol
    assert "pause_writing" in vars(proto), "no flow control on uvicorn's ws protocol"

    if proto.pause_writing.__module__ == "mcuscope.server":
        ns = SimpleNamespace(writable=asyncio.Event())
        ns.writable.set()
        proto.pause_writing(ns)
        assert not ns.writable.is_set(), "pause_writing did not clear the send gate"
        proto.resume_writing(ns)
        assert ns.writable.is_set(), "resume_writing did not reopen the send gate"
        assert "self.writable.wait()" in inspect.getsource(impl), \
            "uvicorn's send no longer gates on writable: re-verify the shed path"


async def test_a_dead_ws_pump_closes_the_socket(stack, monkeypatch) -> None:
    """The receive loop kept the socket open and apparently healthy after the pump died,
    so a client sat on a live connection that would never deliver another row."""
    import websockets

    store = stack.app.state.store

    def boom(q):
        raise RuntimeError("pump is dead")

    url = stack.base_url.replace("http", "ws") + "/ws"
    async with websockets.connect(url) as ws:
        await asyncio.wait_for(ws.recv(), 5.0)
        monkeypatch.setattr(store, "take_dropped", boom)   # raises on the next row
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            while True:
                await asyncio.wait_for(ws.recv(), 10.0)
