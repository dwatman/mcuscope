"""tools/webui_smoke.py runs its checks only against its own server, and cleans up after itself.

The checks send commands and attach and detach a port, so reaching a daemon that already
held the port (the user's, on 8558) would act on the user's board.
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import socket
import tempfile
import threading
from pathlib import Path

import pytest

SMOKE = Path(__file__).resolve().parents[2] / "tools" / "webui_smoke.py"
pytestmark = pytest.mark.skipif(not SMOKE.exists(), reason="tools/ is not in this tree (sdist)")


def _harness():
    spec = importlib.util.spec_from_file_location("webui_smoke", SMOKE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Foreign:
    """A plain HTTP server answering /status as a daemon of another process would."""

    def __init__(self, pid: int) -> None:
        self.requests: list[str] = []
        body = json.dumps({"pid": pid, "ports": [{"alias": "board", "connected": True}]})
        seen = self.requests

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):   # noqa: N802 (http.server's name)
                seen.append(f"GET {self.path}")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode())

            do_POST = do_DELETE = do_GET   # noqa: N815

            def log_message(self, *args):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _live_thread() -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    t = threading.Thread(target=stop.wait, daemon=True)
    t.start()
    return t, stop


def test_readiness_refuses_a_server_of_another_process():
    smoke = _harness()
    foreign = _Foreign(pid=os.getpid() + 1)
    thread, stop = _live_thread()
    try:
        with pytest.raises(RuntimeError, match=r"is answered by pid \d+, not this harness"):
            smoke._wait_ready(foreign.base, thread, timeout=3.0)
        assert foreign.requests == ["GET /status"]
    finally:
        stop.set()
        foreign.close()


def test_readiness_accepts_its_own_process():
    # Positive control: the same server answering with this pid is taken as ready.
    smoke = _harness()
    own = _Foreign(pid=os.getpid())
    thread, stop = _live_thread()
    try:
        smoke._wait_ready(own.base, thread, timeout=3.0)
    finally:
        stop.set()
        own.close()


def test_readiness_refuses_when_its_own_server_thread_died():
    smoke = _harness()
    foreign = _Foreign(pid=os.getpid())
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    try:
        with pytest.raises(RuntimeError, match=rf"own server on {foreign.base} is not running"):
            smoke._wait_ready(foreign.base, dead, timeout=3.0)
        assert foreign.requests == []
    finally:
        foreign.close()


def test_a_taken_port_is_refused_before_any_check(capsys):
    smoke = _harness()
    foreign = _Foreign(pid=os.getpid() + 1)
    try:
        assert smoke.main(["--port", str(foreign.port), "--no-wait"]) == 1
        assert f"port {foreign.port} is not free" in capsys.readouterr().out
        assert foreign.requests == []
    finally:
        foreign.close()


def test_the_default_port_is_a_free_one_and_the_temp_dir_is_removed(monkeypatch, tmp_path):
    made: list[str] = []
    real_mkdtemp = tempfile.mkdtemp

    def mkdtemp(*args, **kw):
        made.append(real_mkdtemp(*args, dir=tmp_path, **kw))
        return made[-1]

    smoke = _harness()
    monkeypatch.setattr(smoke.tempfile, "mkdtemp", mkdtemp)
    # 8558 held (by this test, or by a daemon already there): the old default would refuse.
    try:
        held = socket.create_server(("127.0.0.1", 8558))
    except OSError:
        held = None
    try:
        assert smoke.main(["--no-wait"]) == 0
    finally:
        if held:
            held.close()
    assert len(made) == 1 and Path(made[0]).parent == tmp_path
    assert not Path(made[0]).exists()
