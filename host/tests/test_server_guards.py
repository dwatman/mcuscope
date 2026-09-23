"""The request guards in front of every route (SPEC 3.1): the fetch-metadata refusal of a
cross-site load, framing denial, and the token header forms."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import create_app

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
