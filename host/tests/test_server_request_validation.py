"""What the API refuses before a handler runs (SPEC 3.3.1, 3.4): unknown body fields and query
parameters, lax types, and integers or strings outside the bounds the handlers rely on."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import MAX_DEVICE_LEN, MAX_SERIAL_LEN, MIN_LINE_ID, create_app
from tests.support import UNOPENABLE


@pytest.fixture
def c(tmp_path):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db")),
    )
    app = create_app(config, config_path=tmp_path / "config.toml")
    # Loopback, so the config-write bar does not answer first.
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        yield client


def _error(r) -> str:
    return r.json()["error"]


# -- API-4: unknown fields and parameters ------------------------------------------------


def test_a_misspelled_assert_scope_is_refused_by_name(c) -> None:
    right = c.post("/assert", json={"forbid": ["x"], "session": "nope"})
    assert right.status_code == 400 and "no such session" in _error(right)   # positive control
    typo = c.post("/assert", json={"forbid": ["x"], "sesion": "nope"})
    assert typo.status_code == 422, typo.text
    assert "sesion" in _error(typo) and "Extra inputs" in _error(typo)


def test_a_misspelled_wait_timeout_is_refused_by_name(c) -> None:
    r = c.post("/wait", json={"match": "x", "timout_ms": 50})
    assert r.status_code == 422 and "timout_ms" in _error(r)


def test_an_unknown_field_inside_a_port_entry_is_refused(c) -> None:
    r = c.put("/config/ports", json={"ports": [{"alias": "a", "device": "/dev/x", "bogus": 1}]})
    assert r.status_code == 422 and "ports.0.bogus" in _error(r)


def test_an_undeclared_query_parameter_is_refused_by_name(c) -> None:
    assert c.get("/lines", params={"chan": "sys"}).status_code == 200   # positive control
    r = c.get("/lines", params={"chans": "sys", "zzz": "1"})
    assert r.status_code == 422
    assert _error(r) == "chans: unknown query parameter; zzz: unknown query parameter"


def test_an_undeclared_query_parameter_is_refused_beside_a_path_parameter(c) -> None:
    r = c.delete("/sessions/1", params={"date": "true"})
    assert r.status_code == 422 and "date: unknown query parameter" in _error(r)


def test_the_websocket_and_the_root_redirect_keep_their_extra_parameters(c, monkeypatch) -> None:
    from mcuscope import server as server_mod

    monkeypatch.setattr(server_mod, "WS_KEEPALIVE_S", 0.2)   # the close waits one keepalive
    # `token` is read by the token guard, never declared on the route.
    with c.websocket_connect("/ws?token=abc", headers={"host": "127.0.0.1"}) as ws:
        assert '"capture"' in ws.receive_text()
    r = c.get("/?from=bookmark", follow_redirects=False)
    assert r.status_code == 307


# -- API-9: strict types -------------------------------------------------------------------


@pytest.mark.parametrize("body", [
    {"ms": True}, {"ms": "5"}, {"ms": 5.0},
])
def test_a_break_length_must_be_an_integer(c, body) -> None:
    r = c.post("/break", json=body)
    assert r.status_code == 422 and "ms" in _error(r)


def test_a_valid_break_length_reaches_the_handler(c) -> None:
    r = c.post("/break", json={"ms": 5})
    assert r.status_code == 400 and "no ports attached" in _error(r)


@pytest.mark.parametrize("body", [
    {"all": "yes"}, {"all": 1}, {"all": True, "dry_run": "true"}, {"all": True, "dry_run": 0},
])
def test_a_purge_flag_must_be_a_boolean(c, body) -> None:
    r = c.post("/purge", json=body)
    assert r.status_code == 422, r.text


def test_a_json_integer_still_fills_a_float_field(c) -> None:
    r = c.post("/purge", json={"before_ts": 1, "dry_run": True})
    assert r.status_code == 200 and r.json()["dry_run"] is True


# -- API-5: since_id lower bound ------------------------------------------------------------


@pytest.mark.parametrize("path, extra", [
    ("/lines", {}), ("/lines/export", {}), ("/can/frames", {}),
    ("/plot/series", {"name": "v"}), ("/plot/export", {"names": "v"}),
])
def test_a_since_id_below_the_sqlite_range_is_a_422(c, path, extra) -> None:
    below = c.get(path, params={**extra, "since_id": MIN_LINE_ID - 1})
    assert below.status_code == 422 and "since_id" in _error(below)
    floor = c.get(path, params={**extra, "since_id": MIN_LINE_ID})
    assert floor.status_code != 422, floor.text   # the floor itself is a cursor


# -- API-6 / API-8: attach and saved-port strings --------------------------------------------


@pytest.mark.parametrize(
    "field, size", [("device", MAX_DEVICE_LEN), ("serial_number", MAX_SERIAL_LEN)]
)
def test_attach_and_saved_ports_share_one_length_bound(c, field, size) -> None:
    other = {"serial_number": "SN"} if field == "device" else {"device": UNOPENABLE}
    for value, want in (("x" * (size + 1), 422), ("x" * size, None)):
        attach = c.post("/ports", json={"alias": "big", field: value, **other})
        save = c.put("/config/ports", json={"ports": [{"alias": "big", field: value, **other}]})
        if want == 422:
            assert attach.status_code == 422 and field in _error(attach)
            assert save.status_code == 422 and field in _error(save)
        else:
            assert attach.status_code != 422 and save.status_code != 422
    c.delete("/ports/big")


@pytest.mark.parametrize("field", ["serial_number", "device"])
def test_a_control_character_in_a_saved_port_is_refused(c, field) -> None:
    base = {"alias": "a", "device": UNOPENABLE, "serial_number": "SN1"}
    ok = c.put("/config/ports", json={"ports": [base]})
    assert ok.status_code == 200, ok.text                       # positive control
    for bad in ("x\x1by", "x\x00y", "x\ty"):
        r = c.put("/config/ports", json={"ports": [{**base, field: bad}]})
        assert r.status_code == 400 and _error(r) == f"port a: invalid {field}", r.text
        attach = c.post("/ports", json={**base, "alias": "b", field: bad})
        assert attach.status_code == 400 and _error(attach) == f"invalid {field}"
