"""Session exports and the served UI page (SPEC 3.4, 9.1): an abandon after the copy closed is
still a cancellation, `wait=1` waits for an export slot instead of a 503, and index.html
carries the serving version."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import threading

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Mount

from mcuscope import __version__
from mcuscope import server as server_mod
from mcuscope.server import EXPORT_QUEUE_MAX, EXPORT_WORKERS
from mcuscope.store import Store
from tests.test_e2e import poll
from tests.test_server_exports import (
    _app,
    _BlockedBuild,
    _client,
    _on_loop,
    _session_id,
    _temp_copies,
)


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

        _on_loop(c, cancel_mid_member(), timeout=20)
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


def test_a_waiter_that_disconnects_claims_no_slot_and_leaves_no_file(tmp_path, blocked) -> None:
    admitted = EXPORT_WORKERS + EXPORT_QUEUE_MAX
    with _client(_app(tmp_path)) as c:
        sid = _session_id(c)
        threads, results = _fill_the_pool(c, sid, blocked)
        files_before = _temp_copies(tmp_path)

        async def leave_while_waiting() -> None:
            transport = httpx.ASGITransport(app=c.app, client=("127.0.0.1", 1))
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as ac:
                task = asyncio.ensure_future(ac.get(f"/sessions/{sid}/export?wait=1"))
                await asyncio.sleep(0.3)
                assert not task.done(), "positive control: the request was waiting"
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        _on_loop(c, leave_while_waiting())
        assert c.app.state.export_builds == admitted
        assert _temp_copies(tmp_path) == files_before
        blocked.release.set()
        for t in threads:
            t.join(10)
        assert results == [200] * admitted
        assert poll(lambda: c.app.state.export_builds == 0, 5), "the gone waiter took a slot"
        assert _temp_copies(tmp_path) == []
        assert len(blocked.paths) == admitted, "the gone waiter still ran a build"


def test_wait_is_a_declared_parameter_and_other_names_still_refused(tmp_path, blocked) -> None:
    blocked.release.set()
    with _client(_app(tmp_path)) as c:
        sid = _session_id(c)
        assert c.get(f"/sessions/{sid}/export", params={"wait": 1}).status_code == 200
        r = c.get(f"/sessions/{sid}/export", params={"wiat": 1})
        assert r.status_code == 422 and "wiat: unknown query parameter" in r.text, r.text


# -- chrome F1: the page carries the serving version ------------------------------------------


def test_the_served_page_carries_the_daemon_version(tmp_path) -> None:
    with _client(_app(tmp_path)) as c:
        r = c.get("/ui/")
        assert r.status_code == 200
        assert f'<meta name="mcuscope-version" content="{__version__}">' in r.text
        assert "__MCUSCOPE_VERSION__" not in r.text
        assert r.headers["content-type"].startswith("text/html")
        assert r.headers["cache-control"] == "no-cache"
        assert c.get("/ui/index.html").text == r.text
        again = c.get("/ui/", headers={"if-none-match": r.headers["etag"]})
        assert again.status_code == 304 and again.headers["etag"] == r.headers["etag"]
        # Other files keep StaticFiles' own response.
        js = c.get("/ui/app.js")
        assert js.status_code == 200 and "text/javascript" in js.headers["content-type"]


def test_a_new_version_changes_the_pages_etag_though_the_file_did_not(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / "index.html").write_text(
        '<meta name="mcuscope-version" content="__MCUSCOPE_VERSION__">', encoding="utf-8")
    app = Starlette(routes=[Mount("/ui", server_mod._NoCacheStatic(directory=tmp_path, html=True))])
    with TestClient(app) as c:
        old = c.get("/ui/")
        assert 'content="' + __version__ + '"' in old.text
        server_mod._stamped_index.cache_clear()
        monkeypatch.setattr(server_mod, "__version__", "9.9.9")
        new = c.get("/ui/", headers={"if-none-match": old.headers["etag"]})
        assert new.status_code == 200, "a cached page of the old version was revalidated"
        assert 'content="9.9.9"' in new.text
    server_mod._stamped_index.cache_clear()
