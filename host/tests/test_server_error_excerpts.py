"""Errors never echo a client value whole (SPEC 3.4): a value is cut to 80 characters with
its length named, a 422 lists at most five errors and counts the rest, and a WebSocket
close reason stays within the frame's 123 bytes."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import create_app

N = 500
LONG = "a" * N
CUT = "a" * 80 + f"... ({N} characters)"
HOST_MSG = f"must be a host name or address, not {'a b' * 60!r}"


@pytest.fixture
def c(tmp_path):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db")),
    )
    app = create_app(config, config_path=tmp_path / "config.toml")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        yield client


# One request per site that formats a client string into its refusal.
SITES = [
    ("unknown_port", "get", f"/lines?port={LONG}", None, f"no such port: {CUT}"),
    ("resolve_port", "post", "/send", {"port": LONG, "line": "x"}, f"no such port: {CUT}"),
    ("detach", "delete", f"/ports/{LONG}", None, f"no such port: {CUT}"),
    ("reconnect", "post", f"/ports/{LONG}/reconnect", None, f"no such port: {CUT}"),
    ("disconnect", "post", f"/ports/{LONG}/disconnect", None, f"no such port: {CUT}"),
    ("export", "get", f"/sessions/{LONG}/export", None, f"no such session: {CUT}"),
    ("bundle", "get", f"/sessions/{LONG}/bundle", None, f"no such session: {CUT}"),
    ("purge", "post", "/purge", {"session": LONG}, f"no such session: {CUT}"),
    ("window", "get", f"/lines?session={LONG}", None, f"no such session: {CUT}"),
    ("session_range", "get", f"/plot/series?name=x&session={LONG}", None,
     f"no such session: {CUT}"),
    ("can_id", "get", f"/can/frames?id={LONG}", None, f"bad can id: {CUT}"),
    ("names_twice", "get", f"/plot/export?names={LONG},{LONG}", None,
     f"names lists {CUT} twice"),
    ("plot_channel", "get", f"/plot/export?names={LONG}", None,
     f"no such plot channel: {CUT}; see /plot/channels"),
    ("deadband_shape", "get", f"/plot/export?names=x&decode=true&changes=true&deadband={LONG}",
     None, f"deadband needs name=value: {CUT}"),
    ("deadband_name", "get",
     f"/plot/export?names=x&decode=true&changes=true&deadband={LONG}=1", None,
     "deadband names no exported channel: " + (LONG + "=1")[:80] + f"... ({N + 2} characters)"),
    ("host", "put", "/config/server", {"host": "a b" * 60, "port": 8558},
     "host " + HOST_MSG[:80] + f"... ({len(HOST_MSG)} characters)"),
    ("device_scheme", "put", "/config/ports",
     {"ports": [{"alias": "b", "device": LONG[:200] + "://x"}]},
     "port b: " + ("device scheme not allowed: " + "a" * 200 + "://")[:80]
     + "... (230 characters)"),
]


@pytest.mark.parametrize(("method", "url", "body", "error"),
                         [s[1:] for s in SITES], ids=[s[0] for s in SITES])
def test_a_long_client_value_is_cut_in_the_refusal(c, method, url, body, error) -> None:
    r = c.request(method.upper(), url, json=body)
    assert r.status_code == 400, r.text
    assert r.json() == {"error": error}


def test_a_session_deleted_under_a_bundle_is_named_cut(c, monkeypatch) -> None:
    # The ref resolved, then the session went before the bundle's lock was taken; a name
    # can be 128 characters, so this ref reaches the message too.
    name = "n" * 128
    assert c.post("/sessions", json={"name": name}).status_code == 200
    monkeypatch.setattr(c.app.state.store, "get_session", lambda sid: None)
    r = c.get(f"/sessions/{name}/bundle")
    assert r.status_code == 400
    assert r.json() == {"error": "no such session: " + "n" * 80 + "... (128 characters)"}


def test_a_short_value_is_echoed_as_typed(c) -> None:
    # Positive control for the cut: an 80-character value is not cut.
    v = "b" * 80
    assert c.delete(f"/ports/{v}").json() == {"error": f"no such port: {v}"}


def test_a_422_lists_five_errors_and_counts_the_rest_with_unknown_keys_cut(c) -> None:
    body = {f"k{i:04d}": 0 for i in range(3000)}
    body["z" * 20000] = 0
    body["text"] = "hi"
    raw = json.dumps(body)
    assert len(raw) < 64 * 1024
    r = c.post("/marker", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err.endswith("; and 2996 more"), err[-80:]
    assert err.count("Extra inputs are not permitted") == 5
    assert len(err) < 2000


def test_an_unknown_key_named_in_a_422_is_cut(c) -> None:
    key = "z" * 20000
    r = c.post("/marker", json={key: 0, "text": "hi"})
    assert r.status_code == 422
    assert r.json()["error"] == (
        "z" * 80 + "... (20000 characters): Extra inputs are not permitted (got 0)"
    )


def test_unknown_query_parameters_are_cut_and_counted(c) -> None:
    names = [f"q{i:02d}" for i in range(20)] + ["x" * 300]
    r = c.get("/status?" + "&".join(f"{n}=1" for n in names))
    assert r.status_code == 422
    assert r.json()["error"] == (
        "; ".join(f"q{i:02d}: unknown query parameter" for i in range(5)) + "; and 16 more"
    )
    r = c.get("/status?" + "x" * 300 + "=1")
    assert r.json()["error"] == "x" * 80 + "... (300 characters): unknown query parameter"


@pytest.mark.parametrize("port", ["p" * 200, "€" * 100], ids=["ascii", "multibyte"])
def test_a_ws_close_reason_fits_the_close_frame(c, port) -> None:
    with pytest.raises(WebSocketDisconnect) as e, c.websocket_connect(
        f"/ws?port={port}", headers={"host": "127.0.0.1"}
    ) as ws:
        ws.receive_text()
    assert e.value.code == 1008
    reason = e.value.reason
    assert len(reason.encode("utf-8")) <= 123
    if port.isascii():   # the excerpt alone fits
        assert reason == "no such port: " + port[:80] + "... (200 characters)"
    else:                # 80 three-byte characters do not: cut on a character boundary
        assert reason == "no such port: " + "\u20ac" * 35 + "..."


def test_a_short_ws_close_reason_is_whole(c) -> None:
    with pytest.raises(WebSocketDisconnect) as e, c.websocket_connect(
        "/ws?port=nope", headers={"host": "127.0.0.1"}
    ) as ws:
        ws.receive_text()
    assert (e.value.code, e.value.reason) == (1008, "no such port: nope")
