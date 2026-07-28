"""Reconnect timing: presence-gated polling for a device that comes back (SPEC 3.1).

The reader thread used to sleep out a doubling backoff between open attempts regardless
of why the last one failed, so a replug that finished at t=8 s was not noticed until the
next scheduled attempt. These tests pin the two halves of `_retry_wait`: fast polling
while the device is absent, full backoff while it is present but unopenable.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time

import pytest

from mcuscope import serial_link
from mcuscope.serial_link import BACKOFF_MIN, SerialPort
from mcuscope.store import Store

# `threading.Event.wait(t)` can return marginally before t: on Windows the wait is
# rounded to the system timer tick (~15.6 ms), and CI measured 0.391 s out of a 0.4 s
# wait. The lower bounds below exist to catch the poll cutting an interval short, which
# would return in a quarter of the time, so a slop this size costs them nothing.
TIMER_SLOP = 0.05


class _Info:
    """The two fields of a pyserial ListPortInfo that the port code reads."""

    def __init__(self, device: str, serial_number: str | None = None) -> None:
        self.device = device
        self.serial_number = serial_number


def _port(**kwargs) -> SerialPort:
    # Presence and backoff logic touch neither the store nor the loop.
    return SerialPort(None, None, "board", **kwargs)


def _set_after(delay: float, event: threading.Event) -> threading.Thread:
    def run() -> None:
        time.sleep(delay)
        event.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


# -- presence -------------------------------------------------------------------------


def test_present_by_serial_number(monkeypatch) -> None:
    monkeypatch.setattr(serial_link, "_cached_comports", lambda *a, **k: [_Info("/dev/x", "SN1")])
    assert _port(serial_number="SN1")._device_present()
    assert not _port(serial_number="SN2")._device_present()


def test_remote_transports_are_always_present() -> None:
    # No cheap test exists for socket://; answering True leaves the backoff in charge.
    assert _port(device="socket://127.0.0.1:9900")._device_present()
    assert _port(device="rfc2217://host:1234")._device_present()
    assert _port()._device_present()


@pytest.mark.skipif(os.name == "nt", reason="POSIX device nodes")
def test_present_by_path(tmp_path) -> None:
    dev = tmp_path / "ttyFAKE"
    port = _port(device=str(dev))
    assert not port._device_present()
    dev.write_bytes(b"")
    assert port._device_present()


@pytest.mark.skipif(os.name != "nt", reason="Windows COM enumeration")
def test_present_by_com_name(monkeypatch) -> None:
    monkeypatch.setattr(serial_link, "_cached_comports", lambda *a, **k: [_Info("COM12")])
    assert _port(device="COM12")._device_present()
    assert _port(device=r"\\.\COM12")._device_present()
    assert not _port(device="COM3")._device_present()


# -- retry wait -----------------------------------------------------------------------


def test_retry_wait_returns_early_when_device_reappears(monkeypatch) -> None:
    port = _port(serial_number="SN1")
    back = threading.Event()
    monkeypatch.setattr(
        serial_link,
        "_cached_comports",
        lambda *a, **k: [_Info("/dev/ttyFAKE", "SN1")] if back.is_set() else [],
    )
    thread = _set_after(0.3, back)

    t0 = time.monotonic()
    nxt = port._retry_wait(5.0)
    elapsed = time.monotonic() - t0
    thread.join()

    assert elapsed >= 0.3 - TIMER_SLOP
    assert elapsed < 1.5, f"replug took {elapsed:.2f} s to notice; the poll is not gating"
    assert nxt == BACKOFF_MIN, "a reappearance should restart the backoff"


def test_retry_wait_keeps_backoff_while_device_is_present() -> None:
    # Present but unopenable (busy, permissions): retrying at poll rate would only spin.
    port = _port(device="socket://127.0.0.1:9")
    t0 = time.monotonic()
    nxt = port._retry_wait(0.4)
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.4 - TIMER_SLOP
    assert nxt == 0.8


def test_retry_wait_caps_the_backoff() -> None:
    port = _port(device="socket://127.0.0.1:9")
    assert port._retry_wait(serial_link.BACKOFF_MAX) == serial_link.BACKOFF_MAX


def test_retry_wait_expires_without_the_device(monkeypatch) -> None:
    monkeypatch.setattr(serial_link, "_cached_comports", lambda *a, **k: [])
    port = _port(serial_number="SN1")
    t0 = time.monotonic()
    nxt = port._retry_wait(0.6)
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.6 - TIMER_SLOP, "the poll must not cut the interval short for nothing"
    assert nxt == 1.2


def test_retry_wait_stops_promptly_while_polling(monkeypatch) -> None:
    # Detach must not wait out a long interval just because the device is missing.
    monkeypatch.setattr(serial_link, "_cached_comports", lambda *a, **k: [])
    port = _port(serial_number="SN1")
    thread = _set_after(0.1, port._stop)

    t0 = time.monotonic()
    nxt = port._retry_wait(30.0)
    elapsed = time.monotonic() - t0
    thread.join()

    assert nxt is None
    assert elapsed < 1.0, f"stop took {elapsed:.2f} s to take effect"


# -- comports cache -------------------------------------------------------------------


def test_comports_scan_is_shared(monkeypatch) -> None:
    scans = []
    monkeypatch.setattr(serial_link, "_comports_cache", (0.0, []))
    monkeypatch.setattr(serial_link.list_ports, "comports", lambda: scans.append(1) or [])

    serial_link._cached_comports()
    serial_link._cached_comports()
    assert len(scans) == 1, "concurrent pollers must share one enumeration"

    # An empty result still populates the cache: a machine with no ports at all is the
    # case that polls hardest, and rescanning it every time is the cost being avoided.
    serial_link._cached_comports(max_age=0)
    assert len(scans) == 2


# -- failure notices ------------------------------------------------------------------
#
# The retry loop runs for as long as the device stays away, so an unplugged board left
# overnight used to write thousands of identical "open failed" rows into the capture -
# through the terminal panes, the exports and every `mcu lines` the user ran to find out
# what had happened. One row per reason per episode, one row on the way back up.


def _sys_rows(store: Store) -> list[str]:
    rows, _more = store.query_lines(chans=["sys"], limit=1000, order="asc")
    return [r["raw"] for r in rows]


async def _settle(port: SerialPort) -> None:
    """Await the fire-and-forget sys-row writes the callbacks spawned."""
    await asyncio.gather(*list(port._bg_tasks))


def test_repeated_open_failures_record_one_row(tmp_path) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "c.db"))
        await store.start()
        try:
            port = SerialPort(store, asyncio.get_running_loop(), "board")
            for _ in range(200):
                port._on_error("open /dev/ttyACM0 failed: [Errno 2] no such file", True)
            await _settle(port)
            rows = _sys_rows(store)
            assert rows == ["port board: open /dev/ttyACM0 failed: [Errno 2] no such file"]

            # The reconnect says so once, and reports the retries as a count rather than
            # as 200 rows nobody will read.
            port._on_connect("/dev/ttyACM0")
            await _settle(port)
            rows = _sys_rows(store)
            assert rows[-1] == "port board connected: /dev/ttyACM0 (after 200 failed attempts)"
        finally:
            await store.stop()

    asyncio.run(run())


def test_distinct_reasons_still_reported_but_bounded(tmp_path) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "d.db"))
        await store.start()
        try:
            port = SerialPort(store, asyncio.get_running_loop(), "board")
            # A changed reason is news (the node came back, but permissions are wrong),
            # so it is recorded - up to a cap, because a reason that changes every attempt
            # would otherwise be the same flood by another route.
            for i in range(serial_link.MAX_ERR_NOTICES + 5):
                port._on_error(f"open failed: reason {i}", True)
            await _settle(port)
            rows = _sys_rows(store)
            assert len(rows) == serial_link.MAX_ERR_NOTICES
        finally:
            await store.stop()

    asyncio.run(run())


def test_each_episode_reports_its_reason_again(tmp_path) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "e.db"))
        await store.start()
        try:
            port = SerialPort(store, asyncio.get_running_loop(), "board")
            msg = "read error: device reports readiness to read but returned no data"
            port._on_error(msg)
            port._on_connect("/dev/ttyACM0")
            port._on_disconnect()
            port._on_error(msg)          # a second unplug is a second thing worth knowing
            await _settle(port)
            rows = _sys_rows(store)
            assert rows.count(f"port board: {msg}") == 2
            assert "port board disconnected" in rows
            # A connect with no failed attempts behind it stays exactly as it was.
            assert "port board connected: /dev/ttyACM0" in rows
        finally:
            await store.stop()

    asyncio.run(run())
