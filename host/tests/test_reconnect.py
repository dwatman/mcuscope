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
from concurrent.futures import ThreadPoolExecutor

import pytest

from mcuscope import serial_link
from mcuscope.serial_link import BACKOFF_MIN, JOIN_TIMEOUT, SerialPort
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
    monkeypatch.setattr(serial_link, "cached_comports", lambda *a, **k: [_Info("/dev/x", "SN1")])
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
    monkeypatch.setattr(serial_link, "cached_comports", lambda *a, **k: [_Info("COM12")])
    assert _port(device="COM12")._device_present()
    assert _port(device=r"\\.\COM12")._device_present()
    assert not _port(device="COM3")._device_present()


# -- retry wait -----------------------------------------------------------------------


def test_retry_wait_returns_early_when_device_reappears(monkeypatch) -> None:
    port = _port(serial_number="SN1")
    back = threading.Event()
    monkeypatch.setattr(
        serial_link,
        "cached_comports",
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
    monkeypatch.setattr(serial_link, "cached_comports", lambda *a, **k: [])
    port = _port(serial_number="SN1")
    t0 = time.monotonic()
    nxt = port._retry_wait(0.6)
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.6 - TIMER_SLOP, "the poll must not cut the interval short for nothing"
    assert nxt == 1.2


def test_retry_wait_stops_promptly_while_polling(monkeypatch) -> None:
    # Detach must not wait out a long interval just because the device is missing.
    monkeypatch.setattr(serial_link, "cached_comports", lambda *a, **k: [])
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

    serial_link.cached_comports()
    serial_link.cached_comports()
    assert len(scans) == 1, "concurrent pollers must share one enumeration"

    # An empty result still populates the cache: a machine with no ports at all is the
    # case that polls hardest, and rescanning it every time is the cost being avoided.
    serial_link.cached_comports(max_age=0)
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


# -- rx burst resilience --------------------------------------------------------------
#
# `_store_rx_batch` submitted a whole burst in one list comprehension, so a single line
# that raised abandoned every line after it - up to RX_BATCH_MAX of them - leaving nothing
# behind but a log warning. Measured against a real store: a 9-line burst carrying one
# malformed line stored the four before it and silently lost the four after it.

_GOOD = [f"line {i}" for i in range(4)]
_MORE = [f"line {i}" for i in range(4, 8)]


async def _run_batch(store: Store, lines: list[str]) -> SerialPort:
    port = SerialPort(store, asyncio.get_running_loop(), "board")
    await port._store_rx_batch([(time.time(), line) for line in lines])
    await _settle(port)
    return port


def _raws(store: Store, chans: list[str]) -> list[str]:
    rows, _more = store.query_lines(chans=chans, limit=100, order="asc")
    return [r["raw"] for r in rows]


def test_one_raising_line_does_not_abandon_the_burst(tmp_path, monkeypatch) -> None:
    async def run() -> None:
        store = Store(str(tmp_path / "batch.db"))
        await store.start()
        try:
            real = serial_link.p.classify

            def classify(line: str):
                if line == "poison":
                    raise ValueError("parser fault")
                return real(line)

            monkeypatch.setattr(serial_link.p, "classify", classify)
            port = await _run_batch(store, [*_GOOD, "poison", *_MORE])
            # Every good line either side of it, not just the ones before it.
            assert _raws(store, ["debug"]) == _GOOD + _MORE
            # And the one that did go is accounted for the way every other drop is.
            assert port.rx_dropped == 1
            assert any("could not be stored" in row for row in _sys_rows(store))
        finally:
            await store.stop()

    asyncio.run(run())


def test_an_overlong_seq_token_costs_no_lines_at_all(tmp_path) -> None:
    """The real poison: a numeric token past CPython's int() digit limit.

    The parsers now gate the length, so the line is merely an unmatched response and is
    stored like any other. Nothing in the burst is lost, and nothing is counted as dropped.
    """
    async def run() -> None:
        store = Store(str(tmp_path / "huge.db"))
        await store.start()
        try:
            poison = "<" + "9" * 5000 + " OK"
            lines = [*_GOOD, poison, *_MORE]
            port = await _run_batch(store, lines)
            assert _raws(store, ["debug", "resp"]) == lines
            assert port.rx_dropped == 0
        finally:
            await store.stop()

    asyncio.run(run())


def test_an_oversized_terminated_line_is_dropped_and_counted(tmp_path) -> None:
    """The cap used to bound only an unterminated buffer, so a line that did arrive with
    its LF was stored whole however long it was."""

    async def run() -> None:
        store = Store(str(tmp_path / "cap.db"))
        await store.start()
        try:
            port = SerialPort(store, asyncio.get_running_loop(), "board")
            huge = b"x" * (serial_link.RX_SAFETY_CAP + 1)
            port._on_bytes(time.time(), b"before\n" + huge + b"\nafter\n")
            assert [line for _ts, line in port._rx_lines] == ["before", "after"]
            assert port.rx_dropped == 1
            await _settle(port)
            assert any("byte cap" in row for row in _sys_rows(store))
        finally:
            await store.stop()

    asyncio.run(run())


# -- reader survival and attach atomicity ---------------------------------------------


def test_reader_survives_a_failing_device_lookup(tmp_path, monkeypatch) -> None:
    """Port enumeration ran outside the retry path, so a failure there killed the reader
    thread: the port then read exactly like one still retrying, and never retried again."""

    async def run() -> None:
        store = Store(str(tmp_path / "lookup.db"))
        await store.start()
        try:
            def boom(*args, **kwargs):
                raise OSError("enumeration failed")

            monkeypatch.setattr(serial_link, "cached_comports", boom)
            port = SerialPort(store, asyncio.get_running_loop(), "board", serial_number="SN1")
            port.start()
            await asyncio.sleep(0.3)
            assert port._thread.is_alive()
            await port.stop()
            assert any("device lookup failed" in row for row in _sys_rows(store))
        finally:
            await store.stop()

    asyncio.run(run())


def test_reattach_continues_the_command_seq() -> None:
    """An explicit re-attach restarted at seq 1 while the automatic reconnect kept
    counting, so a late response to a pre-detach command could resolve a new one."""

    async def run() -> None:
        store = Store(":memory:")
        await store.start()
        mgr = serial_link.PortManager(store, asyncio.get_running_loop())
        try:
            port = await mgr.attach("t", device="socket://127.0.0.1:1")
            port._seq = 41
            await mgr.detach("t")
            again = await mgr.attach("t", device="socket://127.0.0.1:1")
            assert again._seq == 41  # the next command is 42, as it would be after a replug
            await mgr.detach("t")
        finally:
            await store.stop()

    asyncio.run(run())


def test_carried_counters_follow_the_alias_not_the_device() -> None:
    """Carried counters are keyed by alias alone, deliberately: the alias is the port slot,
    every captured line is stored under `port=alias`, so a device swap continues the same
    capture under the same name. Windows also re-enumerates one board as a different COMx
    after a replug, so keying on the device string would zero the counters across exactly
    the reattaches the carry exists for."""

    async def run() -> None:
        store = Store(":memory:")
        await store.start()
        mgr = serial_link.PortManager(store, asyncio.get_running_loop())
        try:
            port = await mgr.attach("board", device="socket://127.0.0.1:1")
            port.lines_rx, port.lines_tx, port.rx_dropped, port._seq = 12, 3, 4, 41
            await mgr.detach("board")

            again = await mgr.attach("board", device="socket://127.0.0.1:2")  # other device
            assert again.device != port.device
            assert (again.lines_rx, again.lines_tx, again.rx_dropped, again._seq) == (12, 3, 4, 41)
            await mgr.detach("board")
        finally:
            await store.stop()

    asyncio.run(run())


def test_carried_counters_are_bounded_and_evict_the_oldest() -> None:
    """Nothing else prunes this table, so a client looping attach/detach over fresh aliases
    grew it without limit. The eviction order is what makes the bound usable: the aliases
    most recently detached are the ones about to be re-attached."""

    async def run() -> None:
        store = Store(":memory:")
        await store.start()
        mgr = serial_link.PortManager(store, asyncio.get_running_loop())
        try:
            over = serial_link.CARRIED_MAX + 20
            for i in range(over):
                port = await mgr.attach(f"a{i}", device="socket://127.0.0.1:1")
                port.lines_rx = i
                await mgr.detach(f"a{i}")
            assert len(mgr._carried) == serial_link.CARRIED_MAX
            assert "a0" not in mgr._carried, "the oldest alias should have been evicted"
            assert f"a{over - 1}" in mgr._carried

            # A surviving alias still carries its own counters, not a neighbour's.
            again = await mgr.attach(f"a{over - 1}", device="socket://127.0.0.1:1")
            assert again.lines_rx == over - 1
            await mgr.detach(f"a{over - 1}")
        finally:
            await store.stop()

    asyncio.run(run())


def test_a_failed_reattach_leaves_the_running_port_alone(monkeypatch) -> None:
    """attach() primed the new port's plot defs after detaching the old one, so a store
    failure there returned 500 *and* left the alias attached to nothing."""

    async def run() -> None:
        store = Store(":memory:")
        await store.start()
        mgr = serial_link.PortManager(store, asyncio.get_running_loop())
        try:
            first = await mgr.attach("t", device="socket://127.0.0.1:1")

            async def boom(self) -> None:
                raise RuntimeError("store is unhappy")

            monkeypatch.setattr(SerialPort, "prime_plot_defs", boom)
            with pytest.raises(RuntimeError):
                await mgr.attach("t", device="socket://127.0.0.1:1")
            assert mgr.get("t") is first
            await mgr.detach("t")
        finally:
            await store.stop()

    asyncio.run(run())


# -- teardown must not queue behind unrelated thread work -----------------------------


def test_reader_join_does_not_queue_behind_the_default_executor(tmp_path) -> None:
    """Detach and shutdown wait on the reader join, so it gets a pool nothing else uses.

    The join used to run on the *default* executor, reserved for it by convention only.
    `asyncio.to_thread` is `run_in_executor(None, ...)`, so every ordinary use of that
    idiom shared the pool and a slow one (a session export, a device enumeration) could
    make a detach queue behind it while /status still read healthy.

    The default pool is starved to a single occupied worker here rather than merely
    loaded, so the assertion cannot pass on spare capacity: with the join back on the
    default executor this times out, it does not merely slow down.
    """

    async def run() -> None:
        loop = asyncio.get_running_loop()
        release = threading.Event()
        occupied = threading.Event()
        pool = ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(pool)

        def hog() -> None:
            occupied.set()
            release.wait(30)

        hogged = loop.run_in_executor(None, hog)
        assert occupied.wait(5), "the default pool's only worker never started"

        store = Store(str(tmp_path / "j.db"))
        await store.start()
        # A device that cannot be opened still gets a real reader thread, which is all
        # stop() has to join; no sim or hardware is needed to exercise the wait.
        port = SerialPort(store, loop, "board", device="/dev/mcuscope-nonexistent")
        port.start()
        try:
            await asyncio.wait_for(port.stop(), timeout=JOIN_TIMEOUT + 3.0)
        finally:
            release.set()
            await hogged
            await store.stop()
            pool.shutdown(wait=True)

    asyncio.run(run())
