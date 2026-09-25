"""Every response names the serving daemon's version in `X-Mcuscope-Version` (SPEC 3.4): each
route, a 404, a 422, an unhandled 500, a streamed export and the WebSocket accept."""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount

from mcuscope import __version__
from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import VERSION_HEADER, create_app


@pytest.fixture
def c(tmp_path):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db")),
    )
    app = create_app(config, config_path=tmp_path / "config.toml")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000),
                    raise_server_exceptions=False) as client:
        yield client


def _fill(path: str) -> str:
    # A reference nothing names: no route below acts on a real session, port or file.
    return re.sub(r"\{[^}]+\}", "999999", path)


def test_every_route_answers_with_the_version(c) -> None:
    missing, statuses = [], set()
    for route in c.app.routes:
        if isinstance(route, APIWebSocketRoute):
            continue
        if isinstance(route, Mount):
            calls = [("GET", route.path + "/index.html")]
        elif isinstance(route, APIRoute):
            # POST /shutdown answers 400 in tests (no callback); an empty body is a 422
            # everywhere else, and DELETE of a reference nothing names deletes nothing.
            calls = [(m, _fill(route.path)) for m in sorted(route.methods)]
        else:
            calls = [("GET", route.path)]
        for method, path in calls:
            kw = {"json": {}} if method in ("POST", "PUT") else {}
            r = c.request(method, path, follow_redirects=False, **kw)
            statuses.add(r.status_code)
            if r.headers.get(VERSION_HEADER) != __version__:
                missing.append((method, path, r.status_code))
    assert missing == []
    assert {200, 422} <= statuses, statuses   # the walk reached handlers, not only refusals


def test_refusals_and_streams_carry_it(c) -> None:
    for r in (
        c.get("/no/such/path"),                                   # 404
        c.get("/lines", params={"limit": "1_0"}),                 # 422
        c.get("/lines", params={"match": "("}),                   # 400
        c.get("/lines/export"),                                   # streamed 200
        c.get("/lines", headers={"Origin": "http://evil.example"}),   # a guard's 403
    ):
        assert r.headers.get(VERSION_HEADER) == __version__, (r.request.url, r.status_code)
    assert {c.get("/no/such/path").status_code, c.get("/lines/export").status_code} == {404, 200}


def test_an_unhandled_error_carries_it(c, monkeypatch) -> None:
    def boom(**_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(c.app.state.store, "query_lines", boom)
    r = c.get("/lines")
    assert r.status_code == 500 and r.json() == {"error": "boom"}
    assert r.headers.get(VERSION_HEADER) == __version__
    assert r.headers.get("x-frame-options") == "DENY"


def test_the_websocket_accept_carries_it(c) -> None:
    with c.websocket_connect("/ws", headers={"host": "127.0.0.1"}) as ws:
        headers = {k.decode().lower(): v.decode() for k, v in ws.extra_headers or []}
    assert headers.get(VERSION_HEADER.lower()) == __version__
