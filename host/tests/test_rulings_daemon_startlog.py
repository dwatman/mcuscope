"""Owner ruling 2026-09-15, C-8: a start that never serves rewrites the startup log as a
failure, with the exit code and a one-line reason."""

from __future__ import annotations

import re
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from starlette.applications import Starlette

from mcuscope import _stdio, pidfile
from mcuscope import daemon as daemon_mod
from tests.support import free_port


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app: str(tmp_path / "data"))
    monkeypatch.setattr(_stdio, "_report_key", "")
    return tmp_path / "data"


def _log(data_dir: Path) -> str:
    return (data_dir / "mcuscoped-startup.log").read_text(encoding="utf-8")


def _failing_app(exc: BaseException) -> Starlette:
    @asynccontextmanager
    async def lifespan(app):
        raise exc
        yield

    return Starlette(lifespan=lifespan)


def test_a_lifespan_failure_rewrites_the_log_with_the_exception_line(data_dir) -> None:
    _stdio.write_startup_log("mcuscoped", "mcuscoped 0.5.0 started, pid 1\n")
    with pytest.raises(SystemExit) as exc:
        daemon_mod._serve(_failing_app(RuntimeError("store would not open")),
                          host="127.0.0.1", port=free_port(), log_level="warning")
    assert exc.value.code == 3
    text = _log(data_dir)
    assert "started, pid" not in text, text
    lines = text.splitlines()
    assert lines[0].startswith("mcuscoped ") and lines[0].endswith(", exit 3"), lines
    assert "failed to start" in lines[0]
    assert lines[1] == "reason: RuntimeError: store would not open", lines


def test_a_bind_failure_names_the_bind_error(data_dir) -> None:
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    port = busy.getsockname()[1]
    try:
        with pytest.raises(SystemExit) as exc:
            daemon_mod._serve(Starlette(), host="127.0.0.1", port=port, log_level="warning")
    finally:
        busy.close()
    assert exc.value.code == 3
    reason = _log(data_dir).splitlines()[1]
    # CPython renders an OSError carrying `winerror` as "[WinError %d] %s", and the socket
    # layer sets it on every Windows socket error, so the bracket form is platform-shaped.
    assert re.match(r"reason: \[(Errno|WinError) \d+\] ", reason), reason
    # The port is what makes the line actionable, and it is the half no rendering changes.
    assert str(port) in reason, reason


def test_a_start_that_served_leaves_the_started_log(data_dir, monkeypatch) -> None:
    class OneTick(daemon_mod.Server):
        async def main_loop(self) -> None:
            return   # started, then straight to shutdown

    monkeypatch.setattr(daemon_mod, "Server", OneTick)
    _stdio.write_startup_log("mcuscoped", "mcuscoped 0.5.0 started, pid 1\n")
    daemon_mod._serve(Starlette(), host="127.0.0.1", port=free_port(), log_level="warning")
    assert _log(data_dir) == "mcuscoped 0.5.0 started, pid 1\n"


def test_the_error_capture_is_removed_after_serving(data_dir) -> None:
    import logging

    before = list(logging.getLogger("uvicorn.error").handlers)
    with pytest.raises(SystemExit):
        daemon_mod._serve(_failing_app(RuntimeError("x")),
                          host="127.0.0.1", port=free_port(), log_level="warning")
    assert logging.getLogger("uvicorn.error").handlers == before


def test_a_corrupt_capture_through_main(tmp_path, data_dir, monkeypatch) -> None:
    """The finding's scenario end to end: a db_path that is not a database."""
    claims: list[str | None] = []
    real_claim = pidfile.claim

    def spy_claim(host: str, port: int) -> str | None:
        path = real_claim(host, port)
        claims.append(path)
        return path

    monkeypatch.setattr(pidfile, "claim", spy_claim)
    db = tmp_path / "corrupt.db"
    db.write_bytes(b"not a database, " * 512)
    cfg = tmp_path / "corrupt.toml"
    cfg.write_text(f'[storage]\ndb_path = "{db.as_posix()}"\n', encoding="utf-8", newline="\n")
    port = free_port()
    with pytest.raises(SystemExit) as exc:
        daemon_mod.main(["-c", str(cfg), "--port", str(port)])
    assert exc.value.code == 3
    text = (data_dir / f"mcuscoped-127.0.0.1-{port}-startup.log").read_text(encoding="utf-8")
    assert "failed to start" in text and ", exit 3\n" in text, text
    assert "started, pid" not in text and "database" in text.splitlines()[1], text
    # The claim is the positive control for the glob below: this path does reach it, so an
    # empty data dir means the record was released and not that it was never written.
    assert len(claims) == 1 and claims[0] and claims[0].endswith(".pid"), claims
    assert not list(data_dir.glob("*.pid")), "the pid record outlived a failed start"
