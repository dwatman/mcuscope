"""Pre-release sweep, daemon side: the instances classes 65 and 68 found.

- The shutdown sentinel shed a row from a full queue without counting it (class 65 site).
- A `/wait` or live `/assert` parked in its send never read the sentinel, so the call ran
  into uvicorn's graceful cancel (class 65).
- A plot summary read during a rebuild saw only the rows since the rebuild began, so
  `/plot/export` refused a channel that has points (class 68 widening).
"""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest

from mcuscope.store import Store
from tests.support import Stack
from tests.test_prerelease_daemon_core_shutdown import _await_subscriber, _call_on_loop, _row


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


async def _slow_rebuild(store: Store):
    """Start a rebuild whose SQL scan blocks on a worker until the returned event is set."""
    real_scan = store._scan_plot_summary
    release = threading.Event()

    def slow_scan(conn=None, high=0):
        release.wait(10)
        return real_scan(conn=conn, high=high)

    store._scan_plot_summary = slow_scan
    store._plot_dirty = True
    rebuild = asyncio.create_task(store.query_plot_channels_safe())
    await asyncio.sleep(0.05)   # the scan is now blocked on the worker
    return rebuild, release


@pytest.mark.parametrize("reader", ["query_plot_channels_safe", "plot_ports_safe"])
def test_a_summary_read_during_a_rebuild_waits_for_it(tmp_path, reader) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "rebuild.db"))
        await store.start()
        try:
            await store.add_line(ts=time.time(), port="A", dir="rx", chan="event", seq=None,
                                 raw="!p 1 v=1", plot=[(1, None, "v", 1.0)])
            rebuild, release = await _slow_rebuild(store)
            concurrent = asyncio.create_task(getattr(store, reader)())
            await asyncio.sleep(0.05)
            release.set()
            got = await concurrent
            await rebuild
            if reader == "plot_ports_safe":
                assert got == ["A"], f"read the half-built summary: {got}"
            else:
                assert [c["name"] for c in got] == ["v"], f"read the half-built summary: {got}"
        finally:
            await store.stop()

    asyncio.run(run())


def test_a_failed_rebuild_scan_leaves_the_summary_dirty(tmp_path) -> None:
    """The rebuild cleared the flag before its scan: a scan that raised left the summary
    holding only the rows written since, and nothing rebuilt it until the next delete."""

    async def run() -> None:
        store = Store(str(tmp_path / "scanfail.db"))
        await store.start()
        try:
            await store.add_line(ts=time.time(), port="A", dir="rx", chan="event", seq=None,
                                 raw="!p 1 v=1", plot=[(1, None, "v", 1.0)])
            real_scan = store._scan_plot_summary

            def failing_scan(conn=None, high=0):
                raise OSError("disk I/O error ZZ-scan")

            store._scan_plot_summary = failing_scan
            store._plot_dirty = True
            with pytest.raises(OSError, match="ZZ-scan"):
                await store.query_plot_channels_safe()
            store._scan_plot_summary = real_scan
            got = await store.query_plot_channels_safe()
            assert [c["name"] for c in got] == ["v"], f"the half-built summary stood: {got}"
        finally:
            await store.stop()

    asyncio.run(run())
