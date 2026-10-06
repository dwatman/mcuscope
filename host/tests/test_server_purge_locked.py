"""A purge refused by another process's write lock (SPEC 3.4): a 503 naming the lines it
had already deleted, the lock episode opened as for a sweep, and no traceback logged."""

from __future__ import annotations

import logging
import sqlite3
import time

from fastapi.testclient import TestClient

from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import create_app
from mcuscope.store import Store


def _purge_failing_on_second_chunk(tmp_path, monkeypatch, exc: Exception):
    config = Config(server=ServerConfig(host="127.0.0.1", port=0),
                    storage=StorageConfig(db_path=str(tmp_path / "cap.db"), auto_session=False))
    app = create_app(config, config_path=tmp_path / "config.toml")
    real = Store._delete_range_chunk
    calls = {"n": 0}

    def chunk(self, a, b, limit):
        calls["n"] += 1
        if calls["n"] == 2:
            raise exc
        return real(self, a, b, 3)   # 3 rows per chunk

    monkeypatch.setattr(Store, "_delete_range_chunk", chunk)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000),
                    raise_server_exceptions=False) as c:
        for i in range(10):
            c.post("/marker", json={"text": f"m{i}"})
        deadline = time.monotonic() + 5
        while len(c.get("/lines?limit=100").json()["lines"]) < 10:
            assert time.monotonic() < deadline, "markers never stored"
            time.sleep(0.02)
        r = c.post("/purge", json={"all": True})
        return r, c.get("/status").json()


def test_a_purge_refused_by_a_lock_is_a_503_and_opens_the_episode(
    tmp_path, monkeypatch, caplog
) -> None:
    caplog.set_level(logging.WARNING)
    r, status = _purge_failing_on_second_chunk(
        tmp_path, monkeypatch, sqlite3.OperationalError("database is locked"))
    assert r.status_code == 503, r.text
    assert r.json() == {"error": "purge stopped after deleting 3 lines: the capture is "
                                 "locked by another process"}
    assert status["db_locked_since"] is not None
    assert not [rec for rec in caplog.records if rec.exc_info], "a traceback was logged"


def test_a_purge_failing_otherwise_is_still_a_500(tmp_path, monkeypatch, caplog) -> None:
    """Positive control: only the lock is a 503; another fault stays a logged 500."""
    caplog.set_level(logging.WARNING)
    r, status = _purge_failing_on_second_chunk(
        tmp_path, monkeypatch, sqlite3.OperationalError("disk I/O error"))
    assert r.status_code == 500, r.text
    assert "purge stopped after deleting 3 lines: disk I/O error" in r.json()["error"]
    assert status["db_locked_since"] is None
    assert [rec for rec in caplog.records if rec.exc_info]
