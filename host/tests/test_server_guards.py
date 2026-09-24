"""The request guards in front of every route (SPEC 3.1): the fetch-metadata refusal of a
cross-site load, framing denial, and the token header forms."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
import uvicorn
import websockets
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mcuscope import daemon as daemon_mod
from mcuscope import server
from mcuscope import server as server_mod
from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import create_app
from tests.support import free_port

TOKEN = "sesame-open-123"


def _app(tmp_path, token: str | None = None):
    config = Config(
        server=ServerConfig(host="0.0.0.0" if token else "127.0.0.1", port=0, token=token),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db")),
    )
    return create_app(config, config_path=tmp_path / "config.toml")


@pytest.fixture
def local(tmp_path):
    with TestClient(_app(tmp_path), base_url="http://127.0.0.1",
                    client=("127.0.0.1", 50000)) as c:
        yield c


def _fetch(site: str | None, mode: str | None = None) -> dict[str, str]:
    h = {}
    if site is not None:
        h["Sec-Fetch-Site"] = site
    if mode is not None:
        h["Sec-Fetch-Mode"] = mode
    return h


# -- API-2: a cross-site or same-site load is refused ---------------------------------------


@pytest.mark.parametrize("site", ["cross-site", "same-site", "Cross-Site"])
@pytest.mark.parametrize("path", ["/status", "/lines", "/sessions/1/export"])
def test_a_load_from_another_site_is_refused(local, site, path) -> None:
    r = local.get(path, headers=_fetch(site, "no-cors"))
    assert r.status_code == 403 and r.json() == {"error": "cross-origin request refused"}


@pytest.mark.parametrize("site", [None, "same-origin", "none"])
def test_the_ui_a_typed_url_and_the_cli_are_served(local, site) -> None:
    assert local.get("/status", headers=_fetch(site, "cors")).status_code == 200


@pytest.mark.parametrize("path", ["/", "/ui/", "/ui/app.js"])
def test_a_link_from_another_site_may_still_open_the_ui(local, path) -> None:
    r = local.get(path, headers=_fetch("cross-site", "navigate"), follow_redirects=False)
    assert r.status_code in (200, 307), r.text


def test_a_navigation_from_another_site_to_an_api_route_is_refused(local) -> None:
    r = local.get("/sessions/1/export", headers=_fetch("cross-site", "navigate"))
    assert r.status_code == 403


def test_a_ui_path_loaded_as_a_subresource_is_refused(local) -> None:
    assert local.get("/ui/", headers=_fetch("same-site", "no-cors")).status_code == 403


def test_a_cross_site_websocket_is_refused(local, monkeypatch) -> None:
    from starlette.websockets import WebSocketDisconnect

    from mcuscope import server as server_mod

    monkeypatch.setattr(server_mod, "WS_KEEPALIVE_S", 0.2)   # the close waits one keepalive

    headers = {"host": "127.0.0.1", **_fetch("cross-site", "websocket")}
    with pytest.raises(WebSocketDisconnect):
        with local.websocket_connect("/ws", headers=headers):
            pass
    with local.websocket_connect("/ws", headers={"host": "127.0.0.1"}) as ws:  # control
        assert ws.receive_text()


# -- WEBUI-1: no response may be framed ------------------------------------------------------


def _unframable(r) -> bool:
    return (
        r.headers.get("x-frame-options") == "DENY"
        and r.headers.get("content-security-policy") == "frame-ancestors 'none'"
    )


@pytest.mark.parametrize("path", ["/ui/", "/ui/app.js", "/status", "/no-such-route"])
def test_every_response_forbids_framing(local, path) -> None:
    assert _unframable(local.get(path)), local.get(path).headers


def test_a_guard_refusal_forbids_framing_too(local, tmp_path) -> None:
    refused = local.get("/status", headers={"host": "evil.test"})
    assert refused.status_code == 403 and _unframable(refused)
    (tmp_path / "t").mkdir()
    with TestClient(_app(tmp_path / "t", TOKEN), base_url="http://127.0.0.1") as lan:
        denied = lan.get("/status")
        assert denied.status_code == 401 and _unframable(denied)


# -- API-11: a foreign Authorization scheme does not hide X-Auth-Token ----------------------


def test_a_basic_auth_header_falls_through_to_the_token_header(tmp_path) -> None:
    (tmp_path / "t").mkdir()
    with TestClient(_app(tmp_path / "t", TOKEN), base_url="http://127.0.0.1") as lan:
        basic = {"Authorization": "Basic dXNlcjpwdw=="}
        assert lan.get("/status", headers=basic).status_code == 401
        both = lan.get("/status", headers={**basic, "X-Auth-Token": TOKEN})
        assert both.status_code == 200
        # Bearer still decides when it is the scheme, even beside a correct X-Auth-Token.
        wrong = lan.get("/status", headers={"Authorization": "Bearer nope", "X-Auth-Token": TOKEN})
        assert wrong.status_code == 401


# -- FD2-7: the WS handshake refusal on the wire -----------------------------------------

TOKEN = "sesame-open-123"


@pytest.fixture
def token_ws_url(tmp_path, monkeypatch) -> str:
    """A real uvicorn server with a token set, reached as a non-loopback client would be.

    The handshake refusal is a pre-accept ASGI close, which only uvicorn turns into the
    HTTP status SPEC 3.1 names; Starlette's TestClient surfaces the close code instead.
    Every client here connects from 127.0.0.1, so the loopback exemption is lifted rather
    than the test binding a routable address.
    """
    monkeypatch.setattr(server_mod, "_LOOPBACK_CLIENTS", frozenset())
    port = free_port()
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=port, token=TOKEN),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db"), retention_days=7),
        ports=[],
    )
    app = create_app(config, config_path=tmp_path / "config.toml")
    server = daemon_mod.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not getattr(server, "started", False):
        time.sleep(0.02)
    assert getattr(server, "started", False), "the token server never started"
    try:
        yield f"ws://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=8.0)


async def test_a_ws_handshake_without_the_token_is_refused_with_http_403(token_ws_url) -> None:
    for url, why in ((f"{token_ws_url}/ws", "no token"),
                     (f"{token_ws_url}/ws?token=wrong", "a wrong token")):
        with pytest.raises(websockets.exceptions.InvalidStatus) as refused:
            async with websockets.connect(url):
                pass
        # SPEC 3.1: the handshake itself is refused, which is what lets the page fall back
        # to the /status 401 prompt (a browser can only report this as close 1006).
        assert refused.value.response.status_code == 403, why
    # Positive control: the same handshake carrying the token is answered 101, so the 403
    # above is the refusal and not this endpoint's answer to every client.
    async with websockets.connect(f"{token_ws_url}/ws?token={TOKEN}") as ws:
        assert ws.response.status_code == 101


# -- access token (server.token) ---------------------------------------------------------


def _mk_token_app(tmp_path, token: str | None):
    from mcuscope.config import Config, ServerConfig, StorageConfig
    from mcuscope.server import create_app

    config = Config(
        server=ServerConfig(host="0.0.0.0", port=0, token=token),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db"), retention_days=7),
        ports=[],
    )
    return create_app(config)


def test_token_required_for_non_loopback_clients(tmp_path) -> None:
    # TestClient connections present client host "testclient", i.e. non-loopback.
    from fastapi.testclient import TestClient

    app = _mk_token_app(tmp_path, "sesame-open-123")
    with TestClient(app, base_url="http://127.0.0.1") as c:
        r = c.get("/status")
        assert r.status_code == 401
        assert r.json() == {"error": "missing or invalid access token"}
        r = c.get("/status", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401
        r = c.get("/status", headers={"Authorization": "Bearer sesame-open-123"})
        assert r.status_code == 200
        r = c.get("/status", headers={"X-Auth-Token": "sesame-open-123"})
        assert r.status_code == 200
        # the static UI is always served so the page can load and prompt
        r = c.get("/", follow_redirects=False)
        assert r.status_code in (200, 307)
        r = c.get("/ui/", follow_redirects=True)
        assert r.status_code == 200
        # WebSocket: query param works, missing token is refused with close 1008
        with c.websocket_connect("/ws?token=sesame-open-123", headers={"host": "127.0.0.1"}):
            pass
        # The refusal is asserted on its outcome, never inside a `try` an `except` can
        # reach: `AssertionError` IS an `Exception`, so the earlier `try/except Exception`
        # form swallowed its own failure signal and passed with WebSockets dropped from the
        # guard entirely. 1008 is what the TestClient sees: `_deny` sends a pre-accept ASGI
        # close, which a real uvicorn handshake puts on the wire as HTTP 403 instead (SPEC
        # 3.1, pinned in test_a_ws_handshake_without_the_token_is_refused_with_http_403).
        # The lockout refusal is a different method, `_deny_rate_limited` (close 1013 here,
        # the same 403 over uvicorn).
        with pytest.raises(WebSocketDisconnect) as refused:
            with c.websocket_connect("/ws", headers={"host": "127.0.0.1"}):
                pass
        assert refused.value.code == 1008


def test_loopback_clients_exempt_from_token(tmp_path) -> None:
    from fastapi.testclient import TestClient

    app = _mk_token_app(tmp_path, "sesame-open-123")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as c:
        assert c.get("/status").status_code == 200


def test_no_token_configured_means_open(tmp_path) -> None:
    from fastapi.testclient import TestClient

    app = _mk_token_app(tmp_path, None)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert c.get("/status").status_code == 200


def test_token_static_exemption_is_exact(tmp_path) -> None:
    # Only "/", "/ui" and "/ui/..." are token-exempt; a path that merely starts with
    # the letters "/ui" (a hypothetical future /ui-admin) must still require the token.
    from fastapi.testclient import TestClient

    app = _mk_token_app(tmp_path, "sesame-open-123")
    with TestClient(app, base_url="http://127.0.0.1") as c:
        r = c.get("/uiadmin")
        assert r.status_code == 401


def test_wrong_token_attempts_are_rate_limited(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from mcuscope.server import TOKEN_FAIL_MAX

    app = _mk_token_app(tmp_path, "sesame-open-123")
    with TestClient(app, base_url="http://127.0.0.1") as c:
        for _ in range(TOKEN_FAIL_MAX):
            r = c.get("/status", headers={"Authorization": "Bearer wrong"})
            assert r.status_code == 401
        # Locked out: even the correct token is refused (429) until the lockout expires,
        # and no comparison happens while locked.
        r = c.get("/status", headers={"Authorization": "Bearer sesame-open-123"})
        assert r.status_code == 429
        assert "too many failed token attempts" in r.json()["error"]
        assert r.headers.get("retry-after")
        # The static UI stays reachable during a lockout.
        assert c.get("/ui/", follow_redirects=True).status_code == 200


def test_token_failure_table_stays_bounded_under_a_spray() -> None:
    """The bound is the point: expiry alone does not deliver it.

    An attacker spraying from many source addresses keeps every record inside its window,
    so nothing is ever eligible for expiry and the table grows without limit. Driven at the
    guard rather than over HTTP because the defect only appears past a thousand *distinct*
    addresses, which no request-level test would reach.
    """
    from mcuscope.server import TOKEN_FAIL_MAX, TOKEN_FAIL_TABLE_MAX, _TokenGuard

    guard = _TokenGuard(app=None, token="sesame-open-123")
    now = time.monotonic()
    for i in range(TOKEN_FAIL_TABLE_MAX * 3):
        guard._register_failure(f"10.0.{i // 256}.{i % 256}", now)   # all within one window
    assert len(guard._fails) <= TOKEN_FAIL_TABLE_MAX

    # Eviction is oldest-first, so the addresses still being tried are the ones kept.
    assert f"10.0.{(TOKEN_FAIL_TABLE_MAX * 3 - 1) // 256}.{(TOKEN_FAIL_TABLE_MAX * 3 - 1) % 256}" \
        in guard._fails
    # And a live lockout still bites: bounding the table must not cost the guard its job.
    for _ in range(TOKEN_FAIL_MAX):
        guard._register_failure("192.0.2.7", now)
    assert guard._locked_out("192.0.2.7", now)


def test_missing_token_does_not_count_toward_lockout(tmp_path) -> None:
    # Requests with NO token are unauthenticated clients (e.g. the UI before its first
    # prompt), not brute-force guesses; they must never lock the address out.
    from fastapi.testclient import TestClient

    from mcuscope.server import TOKEN_FAIL_MAX

    app = _mk_token_app(tmp_path, "sesame-open-123")
    with TestClient(app, base_url="http://127.0.0.1") as c:
        for _ in range(TOKEN_FAIL_MAX * 2):
            assert c.get("/status").status_code == 401
        r = c.get("/status", headers={"Authorization": "Bearer sesame-open-123"})
        assert r.status_code == 200


def test_correct_token_resets_failure_budget(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from mcuscope.server import TOKEN_FAIL_MAX

    app = _mk_token_app(tmp_path, "sesame-open-123")
    with TestClient(app, base_url="http://127.0.0.1") as c:
        for _ in range(TOKEN_FAIL_MAX - 1):
            assert c.get("/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = c.get("/status", headers={"Authorization": "Bearer sesame-open-123"})
        assert ok.status_code == 200  # one attempt short of the limit still works
        # The success cleared the slate: a fresh budget applies afterwards.
        for _ in range(TOKEN_FAIL_MAX - 1):
            assert c.get("/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = c.get("/status", headers={"Authorization": "Bearer sesame-open-123"})
        assert ok.status_code == 200


# -- re-review fixes -----------------------------------------------------------------


def test_token_guard_handles_non_ascii_credentials() -> None:
    # A hostile non-ASCII Authorization header must be a clean 401, never a
    # TypeError from str-mode hmac.compare_digest. httpx refuses to send such
    # headers, so drive the middleware directly with a raw ASGI scope.
    from mcuscope.server import _TokenGuard

    async def receive() -> dict:
        return {}

    async def inner_app(scope, receive, send) -> None:
        raise AssertionError("request must be denied before reaching the app")

    async def deny_status(header: tuple[bytes, bytes]) -> int:
        sent: list[dict] = []

        async def send(msg) -> None:
            sent.append(msg)

        guard = _TokenGuard(inner_app, token="sesame-open-123")
        scope = {
            "type": "http",
            "path": "/status",
            "client": ("10.0.0.5", 1234),
            "headers": [header],
        }
        await guard(scope, receive, send)
        return sent[0]["status"]

    async def run() -> None:
        assert await deny_status((b"authorization", b"Bearer caf\xe9")) == 401
        assert await deny_status((b"x-auth-token", b"\xe9")) == 401

    asyncio.run(run())


# -- CD4: the same-origin guard says what it does not cover -----------------------------


def test_the_same_origin_guard_documents_the_no_cors_gap() -> None:
    # Wording, not behaviour: a reader must not conclude a GET endpoint is unreachable
    # cross-site. If the caveat goes, the claim overreaches again.
    doc = server._SameOriginGuard.__doc__ or ""
    assert "no-cors" in doc
