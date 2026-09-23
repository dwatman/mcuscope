"""The config loader stores a port's device and serial number stripped, as PUT /config/ports
does: a padded serial number never matched the one the USB descriptor reports."""

from __future__ import annotations

from types import SimpleNamespace

from mcuscope import serial_link
from mcuscope.config import load_config
from mcuscope.serial_link import SerialPort


def test_a_padded_serial_number_and_device_load_stripped_and_resolve(tmp_path, monkeypatch):
    cfg = tmp_path / "pad.toml"
    cfg.write_text(
        '[[ports]]\nalias = "b"\nserial_number = " 0672FF3 "\n'
        '[[ports]]\nalias = "d"\ndevice = " /dev/ttyACM0 "\n'
        '[[ports]]\nalias = "e"\ndevice = "   "\n',
        encoding="utf-8", newline="\n",
    )
    warnings: list[str] = []
    ports = {p.alias: p for p in load_config(cfg, warnings=warnings).ports}
    assert ports["b"].serial_number == "0672FF3" and ports["b"].device is None
    assert ports["d"].device == "/dev/ttyACM0" and ports["d"].serial_number is None
    assert "e" not in ports
    assert warnings == ["config: port 'e' has neither device nor serial_number, skipping it"]
    monkeypatch.setattr(serial_link, "cached_comports", lambda *a, **k: [
        SimpleNamespace(device="/dev/ttyACM3", serial_number="0672FF3")])
    port = SerialPort(None, None, "b", serial_number=ports["b"].serial_number)
    assert port._resolve_device() == "/dev/ttyACM3"
