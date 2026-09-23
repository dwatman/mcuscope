"""The config loader refuses a bind host and a device the write-back API refuses."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mcuscope.config import load_config
from mcuscope.server import create_app


def _load(tmp_path: Path, text: str):
    cfg = tmp_path / "config.toml"
    cfg.write_text(text, encoding="utf-8", newline="\n")
    warnings: list[str] = []
    return load_config(cfg, warnings=warnings), warnings


def test_an_empty_host_keeps_loopback_and_says_so(tmp_path: Path) -> None:
    for raw in ('""', '"   "', '"a b"', '"lo\\u0000"'):
        config, warnings = _load(tmp_path, f"[server]\nhost = {raw}\n")
        assert config.server.host == "127.0.0.1", raw
        assert any("[server] host must be a host name or address" in w for w in warnings), (
            raw, warnings)


def test_a_wildcard_host_is_still_a_host(tmp_path: Path) -> None:
    config, warnings = _load(tmp_path, '[server]\nhost = " 0.0.0.0 "\n')
    assert config.server.host == "0.0.0.0"
    assert warnings == []


def test_a_device_the_api_refuses_is_not_loaded(tmp_path: Path) -> None:
    config, warnings = _load(
        tmp_path,
        '[[ports]]\nalias = "spy"\ndevice = "spy:///dev/ttyUSB0"\n'
        '[[ports]]\nalias = "opts"\ndevice = "/dev/ttyUSB0?logging=debug"\n'
        '[[ports]]\nalias = "good"\ndevice = "socket://127.0.0.1:9900"\n',
    )
    assert [p.alias for p in config.ports] == ["good"]
    assert any("'spy'" in w and "scheme not allowed: spy://" in w for w in warnings), warnings
    assert any("'opts'" in w and "query options" in w for w in warnings), warnings


def test_the_loaded_ports_save_back(tmp_path: Path) -> None:
    """The settings dialog PUTs GET /config's list back; one refused device refused it all."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[storage]\ndb_path = "{(tmp_path / "cap.db").as_posix()}"\n'
        '[[ports]]\nalias = "spy"\ndevice = "spy:///dev/ttyUSB0"\nautoconnect = false\n'
        '[[ports]]\nalias = "good"\ndevice = "/dev/ttyACM0"\nautoconnect = false\n',
        encoding="utf-8", newline="\n",
    )
    app = create_app(load_config(cfg, warnings=[]), config_path=cfg)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
        body = c.get("/config").json()
        assert [p["alias"] for p in body["ports"]] == ["good"]
        r = c.put("/config/ports", json={"ports": body["ports"], "revision": body["revision"]})
        assert r.status_code == 200, r.text
