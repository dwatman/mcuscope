"""What `mcuscoped` hands the app and uvicorn: the held capture lock, whose re-check the
lifespan registers, and the WebSocket frame bound (SPEC 3.2, 3.4)."""

from __future__ import annotations

import pytest

from mcuscope import daemon as daemon_mod
from mcuscope.lockfile import CaptureLock, LockError
from mcuscope.server import MAX_BODY_BYTES
from tests.support import free_port


@pytest.fixture
def serve_seen(tmp_path, monkeypatch):
    """Run daemon.main() up to uvicorn; `check(app, kw)` runs where it would serve."""
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app: str(tmp_path / "data"))
    monkeypatch.setattr("mcuscope._stdio._report_key", "")
    cfg = tmp_path / "empty-config.toml"
    cfg.write_bytes(b"")
    seen: list = []

    def run(check) -> int:
        monkeypatch.setattr(daemon_mod, "_serve", lambda app, **kw: seen.append(check(app, kw)))
        rc = daemon_mod.main(["-c", str(cfg), "--port", str(free_port())])
        assert seen, "main() never reached uvicorn"
        return rc

    run.db = str(tmp_path / "data" / "capture.db")   # type: ignore[attr-defined]
    return run


def test_the_app_holds_the_lock_the_daemon_acquired(serve_seen) -> None:
    def check(app, kw):
        lock = app.state.capture_lock
        assert isinstance(lock, CaptureLock) and lock._fd is not None, "not the held lock"
        # Another claim on the same capture is refused: this is the lock that guards it.
        with pytest.raises(LockError):
            CaptureLock(serve_seen.db).acquire(timeout=0)

    assert serve_seen(check) == 0


def test_uvicorn_bounds_a_websocket_frame_to_the_body_cap(serve_seen) -> None:
    def check(app, kw):
        assert kw.get("ws_max_size") == MAX_BODY_BYTES, kw

    assert serve_seen(check) == 0
