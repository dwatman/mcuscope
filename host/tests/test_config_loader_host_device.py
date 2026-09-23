"""The config loader refuses a bind host and a device the write-back API refuses."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mcuscope.config import MAX_DEVICE_LEN, MAX_SERIAL_LEN, load_config
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


# Each is refused by PUT /config/ports, so loading it would refuse every later save.
_UNSAVEABLE = {
    "spy": ('device = "spy:///dev/ttyUSB0"', "scheme not allowed: spy://"),
    "longdev": (f'device = "/dev/{"x" * MAX_DEVICE_LEN}"',
                f"device is longer than {MAX_DEVICE_LEN} characters"),
    "longsn": (f'serial_number = "{"7" * (MAX_SERIAL_LEN + 1)}"',
               f"serial_number is longer than {MAX_SERIAL_LEN} characters"),
    "ctldev": ('device = "/dev/tty\\u001b"', "device holds a control character"),
    "ctlsn": ('serial_number = "A1\\tB2"', "serial_number holds a control character"),
    "blank": ('device = "   "', "has neither device nor serial_number"),
}


def test_the_loaded_ports_save_back(tmp_path: Path) -> None:
    """The settings dialog PUTs GET /config's list back; one refused entry refused it all."""
    cfg = tmp_path / "config.toml"
    bad = "".join(
        f'[[ports]]\nalias = "{alias}"\n{line}\nautoconnect = false\n'
        for alias, (line, _) in _UNSAVEABLE.items()
    )
    cfg.write_text(
        f'[storage]\ndb_path = "{(tmp_path / "cap.db").as_posix()}"\n{bad}'
        '[[ports]]\nalias = "good"\ndevice = "/dev/ttyACM0"\nautoconnect = false\n'
        '[[ports]]\nalias = "good"\ndevice = "/dev/ttyACM1"\nautoconnect = false\n'
        # At the limits: kept, which pins the bounds as the PUT's and not tighter.
        f'[[ports]]\nalias = "edge"\ndevice = "/{"x" * (MAX_DEVICE_LEN - 1)}"\n'
        f'serial_number = "{"7" * MAX_SERIAL_LEN}"\nautoconnect = false\n',
        encoding="utf-8", newline="\n",
    )
    warnings: list[str] = []
    config = load_config(cfg, warnings=warnings)
    assert [(p.alias, p.device) for p in config.ports][:1] == [("good", "/dev/ttyACM1")]
    assert [p.alias for p in config.ports] == ["good", "edge"], warnings
    for alias, (_, why) in _UNSAVEABLE.items():
        assert any(f"'{alias}'" in w and why in w for w in warnings), (alias, warnings)
    assert any("'good' is repeated, skipping the earlier entry" in w for w in warnings), warnings
    app = create_app(config, config_path=cfg)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
        body = c.get("/config").json()
        assert [p["alias"] for p in body["ports"]] == ["good", "edge"]
        r = c.put("/config/ports", json={"ports": body["ports"], "revision": body["revision"]})
        assert r.status_code == 200, r.text
