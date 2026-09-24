"""`/status` fields that report the capture's health: the writer, write errors, the size
cap, the capture identity and the daemon clock (SPEC 3.4)."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from tests.support import CommitBoom, mk_app


def test_status_reports_write_errors(tmp_path) -> None:
    # SPEC 3.4: /status carries a top-level integer `write_errors`, always present, and it
    # moves when a write is lost. The whole point is that the failure is visible without
    # reading the daemon log.
    from fastapi.testclient import TestClient

    app = mk_app(tmp_path)
    # raise_server_exceptions=False: the failing write is the point, so the 500 is wanted
    # as a response rather than re-raised into the test.
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as c:
        body = c.get("/status").json()
        assert body["write_errors"] == 0
        assert isinstance(body["write_errors"], int)
        app.state.store._conn = CommitBoom(app.state.store._conn)
        r = c.post("/marker", json={"text": "boom"})
        assert r.status_code >= 400
        assert c.get("/status").json()["write_errors"] == 1


def test_status_reports_the_applied_size_cap(tmp_path) -> None:
    # /status must show the cap the store is enforcing, not the one config asked for.
    from fastapi.testclient import TestClient

    app = mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        app.state.store.set_max_db_bytes(4096)
        assert c.get("/status").json()["db_max_bytes"] == 4096


def test_status_reports_the_size_the_cap_is_enforced_against(stack) -> None:
    """A working cap must not read as a broken one.

    /status paired `db_size_bytes` (file + WAL) with `db_max_bytes`, but the trim is
    enforced against live content, which excludes the freelist. Measured during the round:
    db_size_bytes 24130888 beside db_max_bytes 2097152 with lines_trimmed 0, while the
    enforced figure sat at 2.0 MB throughout. Both are reported now, and the one the cap
    uses is named in SPEC.
    """
    body = httpx.get(stack.base_url + "/status", timeout=15).json()
    assert "db_content_bytes" in body, "the enforced size is not reported at all"
    assert isinstance(body["db_content_bytes"], int)
    # Strictly less, not `<=`: the defect this pins is reporting the file size as the
    # enforced one, which makes the two EQUAL and passes `<=`. They are always separated on
    # a real capture - the freelist aside, db_size_bytes counts the -wal sidecar too.
    assert 0 < body["db_content_bytes"] < body["db_size_bytes"]


def test_status_reports_the_capture_identity(tmp_path) -> None:
    from fastapi.testclient import TestClient

    app = mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        body = c.get("/status").json()
        assert isinstance(body.get("capture"), str) and body["capture"]


# -- /status now -----------------------------------------------------------------------


def test_status_carries_the_daemon_clock(client) -> None:
    before = time.time()
    now = client.get("/status").json()["now"]
    assert isinstance(now, float) and before <= now <= time.time()


async def test_status_reports_a_dead_writer(stack) -> None:
    """`/status` moved no field when capture had stopped entirely; writer_alive does."""
    import httpx

    assert httpx.get(f"{stack.base_url}/status", timeout=5.0).json()["writer_alive"] is True
    store = stack.app.state.store
    task = store._writer_task
    task.get_loop().call_soon_threadsafe(task.cancel)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if httpx.get(f"{stack.base_url}/status", timeout=5.0).json()["writer_alive"] is False:
            return
        await asyncio.sleep(0.05)
    pytest.fail("/status still reported a live writer after the writer task was killed")
