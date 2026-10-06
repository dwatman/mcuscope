"""After a capture failure every read answers 503 with the cause and logs no traceback;
a StoreError with a healthy capture stays the logged 500."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.testclient import TestClient

from mcuscope.store import StoreError
from tests.support import mk_app


def _client(tmp_path) -> TestClient:
    app = mk_app(tmp_path)
    r = APIRouter()

    @r.get("/_boom")
    async def boom():
        raise StoreError("capture failed: file replaced")

    app.include_router(r)
    return TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)


def test_a_store_error_after_a_capture_failure_is_a_503_with_the_cause(tmp_path, caplog) -> None:
    with _client(tmp_path) as c:
        c.app.state.store.capture_error = "file replaced"
        with caplog.at_level(logging.ERROR, logger="mcuscope.server"):
            r = c.get("/_boom")
    assert (r.status_code, r.json()) == (503, {"error": "capture failed: file replaced"})
    assert "unhandled error" not in caplog.text


def test_a_store_error_with_a_healthy_capture_is_still_the_logged_500(tmp_path, caplog) -> None:
    with _client(tmp_path) as c:
        with caplog.at_level(logging.ERROR, logger="mcuscope.server"):
            r = c.get("/_boom")
    assert r.status_code == 500 and "unhandled error" in caplog.text
