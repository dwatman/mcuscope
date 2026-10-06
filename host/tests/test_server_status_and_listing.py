"""`/status` carries the store's health fields and the ports' own counters, `/plot/channels`
is bounded with `limit` and says when it cut, and the lifespan hands the capture lock's
re-check to the store (SPEC 3.4)."""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from mcuscope.lockfile import CaptureLock
from mcuscope.server import PLOT_CHANNELS_MAX
from mcuscope.store import Store
from tests.support import Stack, mk_app, on_loop, stack_client


def test_status_reports_the_store_health_fields_as_the_store_holds_them(tmp_path) -> None:
    with TestClient(mk_app(tmp_path), base_url="http://127.0.0.1") as c:
        body = c.get("/status").json()
        assert (body["lines_expired"], body["db_locked_since"], body["capture_error"]) == (
            0, None, None)
        store = c.app.state.store
        store.lines_expired, store.db_locked_since = 7, 1234.5
        store.capture_error = "capture file replaced"
        body = c.get("/status").json()
        assert (body["lines_expired"], body["db_locked_since"], body["capture_error"]) == (
            7, 1234.5, "capture file replaced")


def test_status_passes_each_ports_counters_through(stack: Stack) -> None:
    with stack_client(stack) as c:
        port = c.get("/status").json()["ports"][0]
    assert port["rx_replaced"] == 0 and port["plot_name_refused"] == 0, port


def _plot(c: TestClient, i: int, name: str) -> None:
    on_loop(c, c.app.state.store.add_line(
        ts=time.time(), port="board", dir="rx", chan="event", seq=None,
        raw=f"!p {i} {name}={i}", plot=[(i, None, name, float(i))]))


def test_plot_channels_keeps_the_most_recent_under_a_limit_and_says_so(tmp_path) -> None:
    with TestClient(mk_app(tmp_path), base_url="http://127.0.0.1") as c:
        for i, name in enumerate(["a", "d", "b", "c"]):   # newest: c, then b
            _plot(c, i + 1, name)
        full = c.get("/plot/channels").json()
        assert [ch["name"] for ch in full["channels"]] == ["a", "b", "c", "d"]
        assert full["truncated"] is False
        cut = c.get("/plot/channels", params={"limit": 2}).json()
        assert [ch["name"] for ch in cut["channels"]] == ["b", "c"], cut
        assert cut["truncated"] is True
        exact = c.get("/plot/channels", params={"limit": 4}).json()
        assert exact["truncated"] is False and len(exact["channels"]) == 4
        none = c.get("/plot/channels", params={"limit": 0}).json()
        assert none["channels"] == [] and none["truncated"] is True


def test_plot_channels_limit_is_clamped_and_validated(tmp_path, monkeypatch) -> None:
    import mcuscope.server as server_mod

    monkeypatch.setattr(server_mod, "PLOT_CHANNELS_MAX", 2)
    with TestClient(mk_app(tmp_path), base_url="http://127.0.0.1") as c:
        for i, name in enumerate(["a", "b", "c"]):
            _plot(c, i + 1, name)
        body = c.get("/plot/channels", params={"limit": 10**6}).json()
        assert len(body["channels"]) == 2 and body["truncated"] is True    # clamped
        assert len(c.get("/plot/channels").json()["channels"]) == 2         # the default
        assert c.get("/plot/channels", params={"limit": -1}).status_code == 422
    assert PLOT_CHANNELS_MAX == 1000


class _Lock:
    def verify(self) -> None:
        pass


def test_the_lifespan_registers_the_capture_locks_check(tmp_path, monkeypatch) -> None:
    seen: list = []
    monkeypatch.setattr(Store, "add_tick_check", lambda self, fn: seen.append(fn),
                        raising=False)
    app = mk_app(tmp_path)
    lock = _Lock()
    app.state.capture_lock = lock
    with TestClient(app, base_url="http://127.0.0.1"):
        pass
    assert seen == [lock.verify]


def test_no_lock_registers_no_check(tmp_path, monkeypatch) -> None:
    seen: list = []
    monkeypatch.setattr(Store, "add_tick_check", lambda self, fn: seen.append(fn),
                        raising=False)
    with TestClient(mk_app(tmp_path), base_url="http://127.0.0.1"):
        pass
    assert seen == []


@pytest.mark.skipif(os.name == "nt", reason="an open, locked file cannot be unlinked on Windows")
def test_a_lost_capture_lock_stops_the_capture_and_says_why_on_status(tmp_path) -> None:
    app = mk_app(tmp_path)
    lock = CaptureLock(str(tmp_path / "cap.db"))
    lock.acquire()
    app.state.capture_lock = lock
    try:
        with TestClient(app, base_url="http://127.0.0.1") as c:
            store = c.app.state.store
            assert on_loop(c, store._run_tick_checks()) is True     # positive control
            os.unlink(lock.path)
            assert on_loop(c, store._run_tick_checks()) is False
            body = c.get("/status").json()
            assert body["capture_error"].startswith("capture lock file replaced or removed")
            assert body["writer_alive"] is False
    finally:
        lock.release()
