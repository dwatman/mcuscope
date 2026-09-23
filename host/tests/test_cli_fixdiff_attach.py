"""`mcu attach`: a malformed `/ports` probe is not a crash, and the retarget note names a
serial binding as one."""

from __future__ import annotations

import httpx
import pytest

from tests.test_cli import run_mcu_canned


def _ports_then_ok(listed):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=listed)
        return httpx.Response(200, json={"port": {"alias": "board", "connected": False}})

    return handler


@pytest.mark.parametrize("listed", [
    [{"alias": "board", "device": "/dev/ttyACM0"}],     # a list, not an object
    {"ports": 5},                                        # `ports` not a list
])
def test_a_malformed_ports_probe_still_attaches(monkeypatch, capsys, listed) -> None:
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _ports_then_ok(listed),
                                  "attach", "/dev/ttyACM1", "--alias", "board")
    assert rc == 0, err
    assert "note:" not in err


def _attach(monkeypatch, capsys, before: dict, *args: str) -> str:
    rc, _, err = run_mcu_canned(monkeypatch, capsys,
                                _ports_then_ok({"ports": [{"alias": "board", **before}]}),
                                "attach", *args, "--alias", "board")
    assert rc == 0, err
    return err


_CONNECTED_BY_SERIAL = {"device": "/dev/ttyACM0", "serial_number": "0672FF3"}
_WAITING_FOR_SERIAL = {"device": "0672FF3", "serial_number": "0672FF3"}


def test_rebinding_a_serial_port_to_its_resolved_device_is_noted(monkeypatch, capsys) -> None:
    """The port stops following the serial although the device string is the same."""
    err = _attach(monkeypatch, capsys, _CONNECTED_BY_SERIAL, "/dev/ttyACM0")
    assert "note: board was attached to serial 0672FF3; it now names /dev/ttyACM0" in err


def test_an_unconnected_serial_port_is_named_as_a_serial(monkeypatch, capsys) -> None:
    err = _attach(monkeypatch, capsys, _WAITING_FOR_SERIAL, "--serial", "11AA22")
    assert "note: board was attached to serial 0672FF3; it now names serial 11AA22" in err


@pytest.mark.parametrize("before", [_CONNECTED_BY_SERIAL, _WAITING_FOR_SERIAL])
def test_reattaching_the_same_serial_is_quiet(monkeypatch, capsys, before) -> None:
    assert "note:" not in _attach(monkeypatch, capsys, before, "--serial", "0672FF3")
