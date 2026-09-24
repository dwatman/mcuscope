"""Serial device enumeration and identity (`serial_link.py`): what `/devices` lists and
which device a port resolves to (SPEC 3.2)."""

from __future__ import annotations

import asyncio
import os
import threading
import time

import pytest

from mcuscope import serial_link
from mcuscope.link import SourceLink
from mcuscope.serial_link import SerialPort, port_identity
from mcuscope.store import Store
from tests.support import Scripted

# -- port identity for the status readout ---------------------------------------------

class _InfoWithDescription:
    def __init__(self, device: str, description: str | None) -> None:
        self.device = device
        self.description = description


@pytest.mark.skipif(os.name != "posix", reason="by-id symlinks are a POSIX shape")
def test_port_identity_resolves_a_by_id_symlink_to_its_port(tmp_path, monkeypatch) -> None:
    real = tmp_path / "ttyACM0"
    real.write_bytes(b"")
    link = tmp_path / "usb-STMicroelectronics_STLINK-V3PWR_0031-if01"
    link.symlink_to(real)
    monkeypatch.setattr("mcuscope.serial_link.cached_comports",
                        lambda: [_InfoWithDescription(str(real), "STLINK-V3PWR")])
    assert port_identity(str(link)) == (str(real), "STLINK-V3PWR")
    # The port name itself is its own short name.
    assert port_identity(str(real)) == (str(real), "STLINK-V3PWR")
    # Enumeration knows nothing of it: the name still comes back, without a description.
    monkeypatch.setattr("mcuscope.serial_link.cached_comports", lambda: [])
    assert port_identity(str(link)) == (str(real), None)


def test_port_identity_leaves_urls_and_com_names_alone(monkeypatch) -> None:
    monkeypatch.setattr("mcuscope.serial_link.cached_comports",
                        lambda: [_InfoWithDescription("COM7", "USB Serial Device (COM7)")])
    assert port_identity("socket://127.0.0.1:9900") == ("socket://127.0.0.1:9900", None)
    assert port_identity("COM7") == ("COM7", "USB Serial Device (COM7)")
    # A failing enumeration costs the description, not the connection.
    def boom():
        raise OSError("setupapi")
    monkeypatch.setattr("mcuscope.serial_link.cached_comports", boom)
    assert port_identity("COM7") == ("COM7", None)


def test_devices_enumeration_does_not_stall_the_event_loop(stack, monkeypatch) -> None:
    """Enumerating serial ports is a setupapi query on Windows, not a cheap sysfs walk.

    Running it on the loop froze every WebSocket feed and every other request for its
    duration - invisible on Linux, seconds on a Windows box carrying Bluetooth COM ports.
    """
    import httpx

    from mcuscope import server as server_mod

    def slow_scan(*_a, **_k):
        time.sleep(2.0)
        return []

    monkeypatch.setattr(server_mod, "cached_comports", slow_scan)
    started = threading.Event()

    def hit_devices() -> None:
        started.set()
        httpx.get(f"{stack.base_url}/devices", timeout=10.0)

    t = threading.Thread(target=hit_devices, daemon=True)
    t.start()
    started.wait(2.0)
    time.sleep(0.1)                          # make sure the scan is under way
    began = time.monotonic()
    assert httpx.get(f"{stack.base_url}/status", timeout=5.0).status_code == 200
    # The scan sleeps 2.0 s, so a blocked loop answers in no less than ~1.9 s from here;
    # an unblocked one answers in an ordinary request round trip. The budget sits far
    # from both, because a tight one (0.4 s against a 0.6 s scan) failed on a Windows
    # box once the suite's earlier tests had aged the process: an unblocked round trip
    # crept to ~0.45 s. Discrimination comes from the spread, not from a fast machine.
    assert time.monotonic() - began < 1.2, "an in-flight /devices scan blocked the loop"
    t.join(timeout=10.0)


def test_devices_skips_realpath_when_there_is_no_by_id_map(monkeypatch) -> None:
    """`realpath("COM7")` is a pointless filesystem hop answering `<cwd>\\COM7`."""
    from mcuscope import server as server_mod

    class _Info:
        device, description, serial_number = "COM7", "USB Serial", "SN9"
        vid, pid = 0x0483, 0x5740

    monkeypatch.setattr(server_mod, "cached_comports", lambda *a, **k: [_Info()])
    monkeypatch.setattr(server_mod, "_by_id_map", dict)
    monkeypatch.setattr(
        server_mod.os.path, "realpath",
        lambda p: pytest.fail("realpath called with no by-id map to look up in"),
    )
    (dev,) = server_mod._enumerate_devices()
    assert dev["device"] == "COM7" and dev["by_id"] is None
    assert dev["vid_pid"] == "0483:5740" and dev["serial_number"] == "SN9"


def test_absent_8250_ports_are_hidden_but_real_uarts_are_kept(tmp_path, monkeypatch) -> None:
    """`mcu devices` listed 32 phantom /dev/ttyS* on Linux, burying the one real adapter.

    The filter must key on the kernel's own PORT_UNKNOWN verdict, not on the name: ttyS0
    is a real mini-UART on a Raspberry Pi and a real on-chip UART on many ARM SoCs.
    """
    import sys as _sys

    from mcuscope import serial_link

    if _sys.platform != "linux":
        pytest.skip("sysfs serial-core attributes are Linux-only")

    sysfs = tmp_path / "sys" / "class" / "tty"
    real_open = open

    def fake_open(path, *args, **kwargs):
        text = str(path)
        if text.startswith("/sys/class/tty/"):
            return real_open(sysfs / text[len("/sys/class/tty/"):], *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    for name, type_value in (("ttyS0", "0"), ("ttyS1", "4"), ("ttyAMA0", "22")):
        (sysfs / name).mkdir(parents=True)
        (sysfs / name / "type").write_text(type_value + "\n", encoding="utf-8", newline="\n")
    (sysfs / "ttyACM0").mkdir(parents=True)   # USB CDC: no `type` attribute at all

    assert serial_link._is_absent_uart("/dev/ttyS0")        # PORT_UNKNOWN: the phantom
    assert not serial_link._is_absent_uart("/dev/ttyS1")    # 16550A: a real on-chip UART
    assert not serial_link._is_absent_uart("/dev/ttyAMA0")  # PL011 on a Pi
    assert not serial_link._is_absent_uart("/dev/ttyACM0")  # no attribute: always kept
    # A device string that is not a bare tty name must not reach the filesystem check.
    assert not serial_link._is_absent_uart("socket://127.0.0.1:9900")
    assert not serial_link._is_absent_uart("/dev/../etc/passwd")


class _Info:
    """The two fields of a pyserial ListPortInfo that the port code reads."""

    def __init__(self, device: str, serial_number: str | None = None) -> None:
        self.device = device
        self.serial_number = serial_number


def test_serial_number_port_reports_the_device_it_opened(monkeypatch) -> None:
    """`/status` must say which device a serial number landed on, not echo the serial.

    Driven through the reader with a fake enumeration and a scripted link, so the value
    reported is the one `_resolve_device` actually handed to the open. `self.device` must
    stay None: the next reconnect has to re-resolve, since the board can come back on a
    different node.
    """

    async def run() -> None:
        store = Store(":memory:")
        await store.start()
        monkeypatch.setattr(
            serial_link, "cached_comports", lambda: [_Info("/dev/ttyFAKE7", "SN1")]
        )
        port = SerialPort(
            store, asyncio.get_running_loop(), "board", serial_number="SN1",
            open_link_fn=lambda dev, baud: SourceLink(Scripted([], idle_after=True)),
        )
        assert port.status()["device"] == "SN1"   # nothing opened yet
        port.start()
        try:
            for _ in range(100):
                if port.connected:
                    break
                await asyncio.sleep(0.05)
            assert port.connected, "the scripted link never connected"
            assert port.status()["device"] == "/dev/ttyFAKE7"
            assert port.device is None, "device must stay unset so reconnect re-resolves"
        finally:
            await port.stop()
            await store.stop()

    asyncio.run(run())
