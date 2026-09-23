"""Session exports under a real server (SPEC 3.4): uvicorn never cancels a handler whose TCP
client left, so a `wait=1` waiter and an admitted build must notice the disconnect themselves;
and the number of waiters is capped."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sqlite3
import threading

import httpx
import pytest

from mcuscope import server as server_mod
from mcuscope.server import EXPORT_QUEUE_MAX, EXPORT_WAITERS_MAX, EXPORT_WORKERS
from mcuscope.store import Store
from tests.support import Stack
from tests.test_e2e import poll
from tests.test_server_exports import _app, _client, _on_loop, _session_id

ADMITTED = EXPORT_WORKERS + EXPORT_QUEUE_MAX


class _Build:
    """Stands in for Store.export_session_db: parks until released, then opens its copy
    through `on_open` as the real export does, which is where an abandon stops it."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = threading.Semaphore(0)
        self.paths: list[str] = []
        self.stopped: list[str] = []   # builds that found their job abandoned
        self.finished: list[str] = []

    def __call__(self, store, dest_path, on_open=None, **_kw) -> int:
        self.paths.append(dest_path)
        self.entered.release()
        assert self.release.wait(20), "the test never released the build"
        conn = sqlite3.connect(":memory:")
        try:
            on_open(conn)
        except server_mod._ExportAbandoned:
            self.stopped.append(dest_path)
            raise
        finally:
            conn.close()
        self.finished.append(dest_path)
        return 0


@pytest.fixture
def build(monkeypatch):
    fake = _Build()
    monkeypatch.setattr(Store, "export_session_db",
                        lambda self, dest_path, **kw: fake(self, dest_path, **kw))
    yield fake
    fake.release.set()


def _copies(stack: Stack) -> list[str]:
    folder = os.path.dirname(server_mod.resolve_db_path(stack.app.state.config))
    return sorted(n for n in os.listdir(folder) if n.startswith("mcuscope-"))


def _sid(stack: Stack) -> int:
    return httpx.get(f"{stack.base_url}/status", timeout=5).json()["session"]["id"]


def _get(stack: Stack, path: str, out: list, timeout: float = 30.0, **params) -> threading.Thread:
    """GET on its own connection in a thread; a timeout closes that connection."""
    def run() -> None:
        try:
            out.append(httpx.get(stack.base_url + path, params=params, timeout=timeout))
        except httpx.TimeoutException:
            out.append("gave up")
    t = threading.Thread(target=run)
    t.start()
    return t


def _fill(stack: Stack, build: _Build, sid: int) -> tuple[list[threading.Thread], list]:
    results: list = []
    threads = [_get(stack, f"/sessions/{sid}/export", results) for _ in range(ADMITTED)]
    for _ in range(EXPORT_WORKERS):
        assert build.entered.acquire(timeout=10), "the pool never started a build"
    assert poll(lambda: stack.app.state.export_builds == ADMITTED, 5)
    return threads, results


def test_a_waiter_whose_client_disconnects_runs_no_build(stack: Stack, build: _Build) -> None:
    sid = _sid(stack)
    threads, results = _fill(stack, build, sid)
    before = _copies(stack)
    left: list = []
    waiter = _get(stack, f"/sessions/{sid}/export", left, timeout=1.0, wait=1)
    assert poll(lambda: stack.app.state.export_waiters == 1, 5), "the request never parked"
    waiter.join(10)
    assert left == ["gave up"]
    # The pool is still full, so only noticing the disconnect can free the waiter's place.
    assert poll(lambda: stack.app.state.export_waiters == 0, 5), "the gone waiter still waits"
    assert not stack.app.state.export_freed._waiters, "the gone waiter left a wait behind"
    build.release.set()
    for t in threads:
        t.join(10)
    assert [r.status_code for r in results] == [200] * ADMITTED
    assert poll(lambda: stack.app.state.export_builds == 0, 5)
    assert len(build.paths) == ADMITTED, "the gone waiter ran a build"
    assert len(before) == EXPORT_WORKERS
    assert poll(lambda: _copies(stack) == [], 5)


def test_an_admitted_build_whose_client_disconnects_is_abandoned(
    stack: Stack, build: _Build
) -> None:
    sid = _sid(stack)
    left: list = []
    t = _get(stack, f"/sessions/{sid}/export", left, timeout=1.0)
    assert build.entered.acquire(timeout=10)
    t.join(10)
    assert left == ["gave up"]
    # The build is still parked, so only the handler noticing the disconnect ends its task.
    assert poll(lambda: not stack._server.server_state.tasks, 5), "the handler never returned"
    build.release.set()
    assert poll(lambda: stack.app.state.export_builds == 0, 5)
    assert build.stopped == build.paths and build.finished == [], "the full build still ran"
    assert _copies(stack) == []
    # Positive control: a client that stays gets its copy from the same fake.
    r = httpx.get(f"{stack.base_url}/sessions/{sid}/export", timeout=10)
    assert r.status_code == 200 and len(build.finished) == 1
    assert poll(lambda: _copies(stack) == [], 5)


def test_waiters_past_the_cap_are_refused(stack: Stack, build: _Build) -> None:
    sid = _sid(stack)
    threads, results = _fill(stack, build, sid)
    waited: list = []
    threads += [_get(stack, f"/sessions/{sid}/export", waited, wait=1)
                for _ in range(EXPORT_WAITERS_MAX)]
    assert poll(lambda: stack.app.state.export_waiters == EXPORT_WAITERS_MAX, 5)
    r = httpx.get(f"{stack.base_url}/sessions/{sid}/export", params={"wait": 1}, timeout=5)
    assert r.status_code == 503 and "waiting for a slot" in r.json()["error"], r.text
    assert waited == [], "a parked waiter was answered while the pool was full"
    build.release.set()
    for t in threads:
        t.join(20)
    assert [r.status_code for r in results + waited] == [200] * (ADMITTED + EXPORT_WAITERS_MAX)
    assert poll(lambda: stack.app.state.export_builds == 0, 5)
    assert poll(lambda: _copies(stack) == [], 5)


def test_a_queued_build_whose_client_disconnects_never_starts(stack: Stack, build: _Build) -> None:
    sid = _sid(stack)
    results: list = []
    threads = [_get(stack, f"/sessions/{sid}/export", results) for _ in range(EXPORT_WORKERS)]
    for _ in range(EXPORT_WORKERS):
        assert build.entered.acquire(timeout=10)
    left: list = []
    queued = _get(stack, f"/sessions/{sid}/export", left, timeout=1.0)
    assert poll(lambda: stack.app.state.export_builds == EXPORT_WORKERS + 1, 5)
    queued.join(10)
    assert left == ["gave up"]
    # Both workers are still busy, so only a cancelled queue entry frees its slot now.
    assert poll(lambda: stack.app.state.export_builds == EXPORT_WORKERS, 5)
    build.release.set()
    for t in threads:
        t.join(10)
    assert [r.status_code for r in results] == [200] * EXPORT_WORKERS
    assert poll(lambda: stack.app.state.export_builds == 0, 5)
    assert len(build.paths) == EXPORT_WORKERS, "the queued build started after its client left"
    assert poll(lambda: _copies(stack) == [], 5)


def test_a_queued_build_whose_handler_is_cancelled_never_starts(tmp_path, build: _Build) -> None:
    """The graceful-shutdown cap cancels handlers; a build still queued must not start."""
    with _client(_app(tmp_path)) as c:
        sid = _session_id(c)
        results: list[int] = []
        threads = [threading.Thread(target=lambda: results.append(
            c.get(f"/sessions/{sid}/export").status_code)) for _ in range(EXPORT_WORKERS)]
        for t in threads:
            t.start()
        for _ in range(EXPORT_WORKERS):
            assert build.entered.acquire(timeout=10)

        async def cancel_queued() -> None:
            transport = httpx.ASGITransport(app=c.app, client=("127.0.0.1", 1))
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as ac:
                task = asyncio.ensure_future(ac.get(f"/sessions/{sid}/export"))
                for _ in range(100):
                    if c.app.state.export_builds == EXPORT_WORKERS + 1:
                        break
                    await asyncio.sleep(0.02)
                assert c.app.state.export_builds == EXPORT_WORKERS + 1, "never queued"
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        _on_loop(c, cancel_queued())
        assert poll(lambda: c.app.state.export_builds == EXPORT_WORKERS, 5), "slot kept"
        build.release.set()
        for t in threads:
            t.join(10)
        assert results == [200] * EXPORT_WORKERS
        assert poll(lambda: c.app.state.export_builds == 0, 5)
        assert len(build.paths) == EXPORT_WORKERS, "the cancelled queued build started"
