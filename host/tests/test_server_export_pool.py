"""The session-export build pool (SPEC 3.4): waiters and admitted builds notice a client
that left, the number of waiters is capped, and an abandoned or cancelled build ends quietly."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sqlite3
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from mcuscope import server as server_mod
from mcuscope.server import EXPORT_QUEUE_MAX, EXPORT_WAITERS_MAX, EXPORT_WORKERS
from mcuscope.store import Store
from tests.support import Stack, on_loop
from tests.test_e2e import poll
from tests.test_server_exports import _app, _BlockedBuild, _client, _session_id, _temp_copies

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

        on_loop(c, cancel_queued())
        assert poll(lambda: c.app.state.export_builds == EXPORT_WORKERS, 5), "slot kept"
        build.release.set()
        for t in threads:
            t.join(10)
        assert results == [200] * EXPORT_WORKERS
        assert poll(lambda: c.app.state.export_builds == 0, 5)
        assert len(build.paths) == EXPORT_WORKERS, "the cancelled queued build started"


@pytest.fixture
def blocked(monkeypatch):
    fake = _BlockedBuild()
    monkeypatch.setattr(Store, "export_session_db",
                        lambda self, dest_path, **kw: fake(self, dest_path, **kw))
    yield fake
    fake.release.set()   # never leave a pool worker parked for the next test


# -- finding 1: interrupting a closed copy connection -----------------------------------------


def test_abandoning_a_job_whose_copy_connection_closed_does_not_raise(tmp_path) -> None:
    job = server_mod._ExportJob(set(), str(tmp_path / "cap.db"), "k")
    conn = sqlite3.connect(":memory:")
    job.on_open(conn)
    conn.close()   # export_session_db's finally, before the bundle writes its members
    job.abandon()
    assert job._abandoned


def test_a_bundle_cancelled_after_its_copy_logs_no_failure(tmp_path, monkeypatch, caplog) -> None:
    writing = threading.Event()

    def endless():
        while True:
            writing.set()
            yield {"id": 1, "ts": 1.0, "port": "board", "dir": "rx", "chan": "debug",
                   "seq": None, "raw": "x" * 200}

    async def lines_export(self, **_kw):
        return endless()

    monkeypatch.setattr(Store, "open_lines_export", lines_export)
    caplog.set_level(logging.ERROR, logger="mcuscope.server")
    with _client(_app(tmp_path)) as c:
        sid = _session_id(c)

        async def cancel_mid_member() -> None:
            transport = httpx.ASGITransport(app=c.app, client=("127.0.0.1", 1))
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as ac:
                task = asyncio.ensure_future(ac.get(f"/sessions/{sid}/bundle"))
                assert await asyncio.to_thread(writing.wait, 10), "no member was written"
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        on_loop(c, cancel_mid_member(), timeout=20)
        assert poll(lambda: _temp_copies(tmp_path) == [], 10), "the abandoned bundle kept going"
        assert poll(lambda: c.app.state.export_builds == 0, 5)
    failures = [r.getMessage() for r in caplog.records if "failed" in r.getMessage()]
    assert failures == [], failures
    # Positive control: an error on this logger does reach caplog.
    logging.getLogger("mcuscope.server").error("session bundle failed: control")
    assert caplog.records[-1].getMessage() == "session bundle failed: control"


# -- chrome F2: wait=1 waits for a slot -------------------------------------------------------


def _fill_the_pool(c: TestClient, sid: int, blocked) -> tuple[list[threading.Thread], list[int]]:
    admitted = server_mod.EXPORT_WORKERS + server_mod.EXPORT_QUEUE_MAX
    results: list[int] = []
    threads = [
        threading.Thread(target=lambda: results.append(
            c.get(f"/sessions/{sid}/export").status_code))
        for _ in range(admitted)
    ]
    for t in threads:
        t.start()
    blocked.wait_entered(EXPORT_WORKERS)
    assert poll(lambda: c.app.state.export_builds == admitted, 5)
    return threads, results


class _GatedBuild(_BlockedBuild):
    """_BlockedBuild whose builds are let through one at a time."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Semaphore(0)

    def __call__(self, store, dest_path, **_kw) -> int:
        self.paths.append(dest_path)
        self.entered.release()
        assert self.gate.acquire(timeout=20), "the test never let the build through"
        return 0


@pytest.fixture
def gated(monkeypatch):
    fake = _GatedBuild()
    monkeypatch.setattr(Store, "export_session_db",
                        lambda self, dest_path, **kw: fake(self, dest_path, **kw))
    yield fake
    fake.gate.release(64)


def test_wait_parks_a_full_pools_request_until_a_slot_frees(tmp_path, gated, monkeypatch) -> None:
    # No queue, so an admitted build enters at once and admission is observable.
    monkeypatch.setattr(server_mod, "EXPORT_QUEUE_MAX", 0)
    admitted = EXPORT_WORKERS
    with _client(_app(tmp_path)) as c:
        sid = _session_id(c)
        threads, results = _fill_the_pool(c, sid, gated)
        assert c.get(f"/sessions/{sid}/export").status_code == 503   # without wait: unchanged
        waited: list[int] = []
        waiters = [
            threading.Thread(target=lambda path=path: waited.append(
                c.get(f"/sessions/{sid}/{path}", params={"wait": 1}).status_code))
            for path in ("export", "bundle")
        ]
        for t in waiters:
            t.start()
        for t in waiters:
            t.join(0.5)
        assert waited == [], f"a wait=1 request answered while the pool was full: {waited}"
        assert c.app.state.export_builds == admitted
        # One slot frees: one waiter takes it, the other must go back to waiting.
        gated.gate.release()
        assert poll(lambda: len(results) == 1, 5), "the first build never finished"
        assert poll(lambda: len(gated.paths) == admitted + 1, 5), "no waiter was admitted"
        assert c.app.state.export_builds == admitted
        assert c.get("/status").status_code == 200, "the loop stopped serving"
        assert waited == [] and len(gated.paths) == admitted + 1
        gated.gate.release(64)
        for t in threads + waiters:
            t.join(10)
        assert results == [200] * admitted
        assert sorted(waited) == [200, 200], waited
        assert poll(lambda: c.app.state.export_builds == 0, 5)
        assert _temp_copies(tmp_path) == []


def test_wait_is_a_declared_parameter_and_other_names_still_refused(tmp_path, blocked) -> None:
    blocked.release.set()
    with _client(_app(tmp_path)) as c:
        sid = _session_id(c)
        assert c.get(f"/sessions/{sid}/export", params={"wait": 1}).status_code == 200
        r = c.get(f"/sessions/{sid}/export", params={"wiat": 1})
        assert r.status_code == 422 and "wiat: unknown query parameter" in r.text, r.text
