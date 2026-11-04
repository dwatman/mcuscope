"""The `/ws` live feed (SPEC 3.4): a disconnect releases its subscriber, the idle keepalive,
backpressure, and a dead pump closing the socket."""

from __future__ import annotations

import asyncio
import time

import pytest

from tests.support import add_row, mk_app, on_loop

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


def test_rows_queued_before_a_capture_change_go_out_ahead_of_its_token(tmp_path, monkeypatch):
    # A purge freeing the highest id mints a new capture (SPEC 3.4) while rows of the old one
    # can still be queued for a subscriber (a slow reader, or the pump simply not run yet).
    # The pump put the token at the head of its frame, ahead of those rows: the web UI reset
    # on it, re-seeded from the purged capture, and then merged the queued rows behind the
    # token as the new capture's, so a line the purge had just deleted came back on screen.
    from fastapi.testclient import TestClient

    from mcuscope import server as server_mod

    monkeypatch.setattr(server_mod, "WS_KEEPALIVE_S", 0.1)
    app = mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        store = app.state.store
        for i in range(3):
            on_loop(c, add_row(store, 1000.0 + i, f"r{i}"))
        with c.websocket_connect("/ws", headers={"host": "127.0.0.1"}) as ws:
            before = ws.receive_json()[0]["capture"]

            async def race() -> None:
                # One loop step, so the pump cannot run in between: line 3 reaches the queue,
                # the purge frees id 3, and the next line is captured (the daemon's id
                # sequence never moves down, so it takes id 4).
                store._broadcast({"id": 3, "port": "board", "raw": "old"})
                store._delete_lines("SELECT id FROM lines WHERE id >= ?", (3,))
                store._broadcast({"id": 4, "port": "board", "raw": "new"})

            on_loop(c, race())
            after = store.capture_id
            assert after != before
            got: list = []
            deadline = time.monotonic() + 5.0
            while not any(r.get("raw") == "new" for r in got) and time.monotonic() < deadline:
                got += ws.receive_json()
            assert got == [
                {"id": 3, "port": "board", "raw": "old"},
                {"capture": after},
                {"id": 4, "port": "board", "raw": "new"},
            ], got
            # Named once: the in-band change is the frame's token, not followed by another.
            assert ws.receive_json() == []


def test_a_capture_change_shed_from_a_full_queue_still_reaches_the_client(tmp_path, monkeypatch):
    # The change sits in the subscriber's queue, so a slow reader can have it shed like a row.
    # Every row still queued then belongs to the new capture, and the client must still be
    # told: a page never told keeps its old watermark and drops the new capture for good.
    from fastapi.testclient import TestClient

    from mcuscope import server as server_mod

    monkeypatch.setattr(server_mod, "WS_KEEPALIVE_S", 0.1)
    app = mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        store = app.state.store
        on_loop(c, add_row(store, 1000.0, "r0"))
        with c.websocket_connect("/ws", headers={"host": "127.0.0.1"}) as ws:
            before = ws.receive_json()[0]["capture"]
            (q,) = store._json_subs

            async def flood() -> None:
                store._delete_lines("SELECT id FROM lines WHERE id >= ?", (1,))
                for i in range(q.maxsize):   # the change, then a queue's worth of rows
                    store._broadcast({"id": 2 + i, "port": "board", "raw": f"n{i}"})

            on_loop(c, flood())
            after = store.capture_id
            assert after != before
            got: list = []
            deadline = time.monotonic() + 5.0
            while not any(r.get("raw") == f"n{q.maxsize - 1}" for r in got) \
                    and time.monotonic() < deadline:
                got += ws.receive_json()
            assert got[0] == {"capture": after}, got[:3]
            assert sum("capture" in r for r in got) == 1
            assert not any("gap" in r for r in got), "the shed change is not a missed line"


def test_capture_frame_places_each_change_where_it_was_queued() -> None:
    from mcuscope.server import _capture_frame
    from mcuscope.store import CaptureMark

    row = '{"id":1}'
    # First frame: the head capture is named first; a change to the same capture is no news.
    out, head, sent = _capture_frame([row, CaptureMark("a"), row], "a", None)
    assert out == ['{"capture":"a"}', row, row] and (head, sent) == ("a", "a")
    # A change mid-frame stays between its rows, and the next frame starts in it.
    out, head, sent = _capture_frame([row, CaptureMark("b"), row], "a", "a")
    assert out == [row, '{"capture":"b"}', row] and (head, sent) == ("b", "b")
    # A keepalive names nothing once the capture is sent.
    assert _capture_frame([], "b", "b") == ([], "b", "b")
