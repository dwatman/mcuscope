"""The wrong-token lockout is kept per IPv4 address and per IPv6 /64 (SPEC 3.1)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import TOKEN_FAIL_MAX, _throttle_key, create_app

TOKEN = "sesame-open-123"


def _app(tmp_path):
    config = Config(
        server=ServerConfig(host="::", port=0, token=TOKEN),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db")),
    )
    return create_app(config, config_path=tmp_path / "config.toml")


def _guess(app, host: str) -> int:
    # No lifespan: a refused token never reaches a route, so no store is needed.
    c = TestClient(app, base_url="http://127.0.0.1", client=(host, 40000))
    return c.get("/status", headers={"X-Auth-Token": "wrong"}).status_code


def test_rotating_addresses_inside_one_ipv6_64_share_one_budget(tmp_path) -> None:
    app = _app(tmp_path)
    for i in range(TOKEN_FAIL_MAX):
        assert _guess(app, f"2001:db8:1:2::{i + 1:x}") == 401
    assert _guess(app, "2001:db8:1:2:ffff:ffff:ffff:ffff") == 429
    # Positive control: the next /64 has its own budget.
    assert _guess(app, "2001:db8:1:3::1") == 401


def test_ipv4_addresses_keep_their_own_budgets(tmp_path) -> None:
    app = _app(tmp_path)
    for _ in range(TOKEN_FAIL_MAX):
        _guess(app, "192.0.2.10")
    assert _guess(app, "192.0.2.10") == 429
    assert _guess(app, "192.0.2.11") == 401
    # The IPv4-mapped spelling of a locked address is the same client.
    assert _guess(app, "::ffff:192.0.2.10") == 429


def test_throttle_key_shapes() -> None:
    assert _throttle_key("2001:db8:1:2:aaaa::1") == "2001:db8:1:2::/64"
    assert _throttle_key("fe80::1%eth0") == "fe80::/64"
    assert _throttle_key("10.0.0.5") == "10.0.0.5"
    assert _throttle_key("testclient") == "testclient"   # not an address: kept as given
