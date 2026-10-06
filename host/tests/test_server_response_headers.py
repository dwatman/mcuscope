"""Every HTTP response path carries the version and framing headers exactly once (SPEC 3.1,
3.4): a path answered inside the app middleware must not add them again, and one answered
outside it (ServerErrorMiddleware's 500) must add them itself."""

from __future__ import annotations

import os

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from mcuscope.cli_client import check_daemon_version
from mcuscope.lockfile import CaptureLock
from mcuscope.server import MAX_BODY_BYTES, VERSION_HEADER
from mcuscope.store import StoreError
from tests.support import mk_app, on_loop

_ONCE = (VERSION_HEADER.lower(), "x-frame-options", "content-security-policy")


def _assert_once(r) -> None:
    for name in _ONCE:
        assert len(r.headers.get_list(name)) == 1, (r.status_code, name, r.headers.raw)
    check_daemon_version("http://127.0.0.1", r.headers)   # exits on a joined duplicate


@pytest.fixture
def c(tmp_path):
    app = mk_app(tmp_path)
    r = APIRouter()

    @r.get("/_boom")
    async def boom():
        raise RuntimeError("boom")

    @r.get("/_store_boom")
    async def store_boom():
        raise StoreError("store boom")

    app.include_router(r)
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
        yield client


def test_every_response_path_carries_each_header_once(c) -> None:
    cases = {
        200: c.get("/status"),
        404: c.get("/no/such/route"),
        422: c.post("/marker", json={"text": 5}),
        413: c.post("/marker", content=b"x" * (MAX_BODY_BYTES + 1)),
        403: c.post("/marker", json={"text": "x"}, headers={"Origin": "http://evil.example"}),
    }
    for status, r in cases.items():
        assert r.status_code == status, (status, r.text)
        _assert_once(r)


def test_an_unhandled_error_and_a_healthy_store_error_carry_each_header_once(c) -> None:
    for path, text in (("/_boom", "boom"), ("/_store_boom", "store boom")):
        r = c.get(path)
        assert (r.status_code, r.json()) == (500, {"error": text})
        _assert_once(r)


@pytest.mark.skipif(os.name == "nt", reason="an open, locked file cannot be unlinked on Windows")
def test_a_read_after_a_real_capture_failure_is_a_503_the_cli_accepts(tmp_path) -> None:
    app = mk_app(tmp_path)
    lock = CaptureLock(str(tmp_path / "cap.db"))
    lock.acquire()
    app.state.capture_lock = lock
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            store = client.app.state.store
            os.unlink(lock.path)
            assert on_loop(client, store._run_tick_checks()) is False
            r = client.get("/lines")
            assert r.status_code == 503, r.text
            assert r.json()["error"] == store.capture_error, r.text
            _assert_once(r)
    finally:
        lock.release()
