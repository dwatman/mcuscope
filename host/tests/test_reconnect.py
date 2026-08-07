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
import serial

from mcuscope import serial_link
from mcuscope.link import BurstThenError, FakeLink
from mcuscope.serial_link import BACKOFF_MIN, JOIN_TIMEOUT, PortError, SerialPort
from mcuscope.store import Store
from tests.support import UNOPENABLE, UNOPENABLE_ALT

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
            port = await mgr.attach("t", device=UNOPENABLE)
            port._seq = 41
            await mgr.detach("t")
            again = await mgr.attach("t", device=UNOPENABLE)
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
            port = await mgr.attach("board", device=UNOPENABLE)
            port.lines_rx, port.lines_tx, port.rx_dropped, port._seq = 12, 3, 4, 41
            await mgr.detach("board")

            again = await mgr.attach("board", device=UNOPENABLE_ALT)  # other device
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
                port = await mgr.attach(f"a{i}", device=UNOPENABLE)
                port.lines_rx = i
                await mgr.detach(f"a{i}")
            assert len(mgr._carried) == serial_link.CARRIED_MAX
            assert "a0" not in mgr._carried, "the oldest alias should have been evicted"
            assert f"a{over - 1}" in mgr._carried

            # A surviving alias still carries its own counters, not a neighbour's.
            again = await mgr.attach(f"a{over - 1}", device=UNOPENABLE)
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
            first = await mgr.attach("t", device=UNOPENABLE)

            async def boom(self) -> None:
                raise RuntimeError("store is unhappy")

            monkeypatch.setattr(SerialPort, "prime_plot_defs", boom)
            with pytest.raises(RuntimeError):
                await mgr.attach("t", device=UNOPENABLE)
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


# -- the reader's success path (link.FakeLink) -----------------------------------------
#
# Before the Link seam these could not be written: the only transport a SerialPort could
# obtain was a real one, so every reader-thread test above drives a device that can never
# open, and the burst/drain/post cycle - the hottest code in the module - ran untested.


def _fake_port(script, store, loop):
    """A SerialPort whose transport is an in-memory script, plus the exhaustion event."""
    exhausted = threading.Event()
    links: list[FakeLink] = []

    def opener(device: str, baud: int) -> FakeLink:
        # Only the first link plays the script; a reconnect gets a quiet one, so a
        # replay cannot inflate the counts a test is reading.
        link = FakeLink(
            script if not links else [], device=device,
            exhausted=exhausted, idle_after=True,
        )
        links.append(link)
        return link

    port = SerialPort(store, loop, "board", device="/dev/fake", open_link_fn=opener)
    return port, exhausted, links


async def _drive(script, tmp_path, expect_lines: int):
    """Run the reader over `script` and return the rows the store received."""
    from mcuscope.store import Store

    store = Store(str(tmp_path / "reader.db"))
    await store.start()
    loop = asyncio.get_running_loop()
    port, exhausted, links = _fake_port(script, store, loop)
    port.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if exhausted.is_set() and port.lines_rx >= expect_lines:
                break
            await asyncio.sleep(0.01)
    finally:
        await port.stop()
    rows, _ = store.query_lines(limit=200, order="asc")
    await store.stop()
    return rows, port, links


async def test_reader_timestamps_a_burst_and_splits_its_lines(tmp_path) -> None:
    """One read anchors the burst; the drain brings the rest of it in one hop."""
    rows, port, _ = await _drive([b"one\ntwo\nthree\n"], tmp_path, 3)
    raws = [r["raw"] for r in rows if r["chan"] != "sys"]
    assert raws == ["one", "two", "three"]
    assert port.lines_rx == 3
    # One burst, so all three carry the timestamp of the byte that woke the reader.
    stamps = {r["ts"] for r in rows if r["chan"] != "sys"}
    assert len(stamps) == 1, stamps


async def test_reader_keeps_a_partial_line_until_its_terminator_arrives(tmp_path) -> None:
    """A line split across two bursts is one line, not two fragments."""
    rows, port, _ = await _drive([b"he", b"llo\n"], tmp_path, 1)
    assert [r["raw"] for r in rows if r["chan"] != "sys"] == ["hello"]
    assert port.lines_rx == 1


async def test_reader_posts_a_burst_the_drain_died_inside(tmp_path) -> None:
    """SPEC 3.2's logging half: complete lines already in the buffer must not be lost.

    At EOF a socket:// port reports readable and pyserial raises from inside the drain
    with whole lines sitting in buf. Posting only on success threw that burst away, so a
    response received just before the link dropped was neither delivered nor logged.
    """
    script = [BurstThenError(b"last words\n", serial.SerialException("eof"))]
    rows, port, _ = await _drive(script, tmp_path, 1)
    assert "last words" in [r["raw"] for r in rows]
    assert port.lines_rx == 1


async def test_reader_closes_and_cancels_the_link_it_loses(tmp_path) -> None:
    """The teardown order the write lock exists for: cancel_write, then close."""
    _, _, links = await _drive([b"x\n"], tmp_path, 1)
    assert links, "the reader never opened a link"
    assert links[0].closed
    assert links[0].cancelled_writes >= 1


async def test_reader_reopens_after_the_link_drops(tmp_path) -> None:
    """A read error costs the connection, not the reader thread."""
    from mcuscope.store import Store

    store = Store(str(tmp_path / "reopen.db"))
    await store.start()
    loop = asyncio.get_running_loop()
    opened: list[FakeLink] = []

    def opener(device: str, baud: int) -> FakeLink:
        # First link dies after one line; the second delivers and then stalls quietly.
        script = [b"first\n", serial.SerialException("dropped")] if not opened else [b""]
        link = FakeLink(script, device=device)
        opened.append(link)
        return link

    port = SerialPort(store, loop, "board", device="/dev/fake", open_link_fn=opener)
    port.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(opened) < 2:
            await asyncio.sleep(0.01)
    finally:
        await port.stop()
    await store.stop()
    assert len(opened) >= 2, "the reader did not reopen after the link dropped"


async def test_write_goes_to_the_link_and_fails_once_it_is_gone(tmp_path) -> None:
    from mcuscope.store import Store

    store = Store(str(tmp_path / "write.db"))
    await store.start()
    loop = asyncio.get_running_loop()
    port, exhausted, links = _fake_port([b""], store, loop)
    port.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not links:
            await asyncio.sleep(0.01)
        await port.send_raw("ping")
        assert links[0].written == b"ping\n"
    finally:
        await port.stop()
    with pytest.raises(PortError):
        await port.send_raw("after detach")   # the link is gone
    await store.stop()


# -- the drain strategies are two adapters, not one --------------------------------------


def test_socket_drain_does_not_trust_in_waiting() -> None:
    """socket:// reports in_waiting as a 0/1 readability poll, so a sized read per byte."""
    from mcuscope.link import SerialLink

    class _Sock:
        in_waiting = 1              # what the URL handler always says
        timeout = 0.2

        def __init__(self) -> None:
            self.data = bytearray(b"abcdef")
            self.sized_reads: list[int] = []

        def read(self, n: int) -> bytes:
            self.sized_reads.append(n)
            out, self.data = bytes(self.data[:n]), self.data[n:]
            return out

    ser = _Sock()
    link = SerialLink(ser, "socket://127.0.0.1:9900")
    buf = bytearray()
    link.drain(buf)
    assert bytes(buf) == b"abcdef"
    assert ser.sized_reads[0] > 1, "read was sized from in_waiting, one byte at a time"
    assert ser.timeout == 0.2, "the zero timeout must be put back"


def test_native_drain_reads_exactly_what_is_waiting() -> None:
    from mcuscope.link import SerialLink

    class _Native:
        def __init__(self) -> None:
            self.in_waiting = 4
            self.asked: list[int] = []

        def read(self, n: int) -> bytes:
            self.asked.append(n)
            return b"wxyz"[:n]

    ser = _Native()
    link = SerialLink(ser, "/dev/ttyACM0")
    buf = bytearray()
    link.drain(buf)
    assert bytes(buf) == b"wxyz"
    assert ser.asked == [4]


def test_a_link_that_cannot_cancel_says_so() -> None:
    """The bool is what replaced hasattr at the call site."""
    from mcuscope.link import SerialLink

    class _NoCancel:
        in_waiting = 0

    link = SerialLink(_NoCancel(), "socket://127.0.0.1:1")
    assert link.cancel_read() is False
    assert link.cancel_write() is False


# -- the once-per-episode notices --------------------------------------------------------


def test_episode_notice_reports_once_and_rearms() -> None:
    """The rule five shedding paths used to each re-implement, with the clear far away."""
    from mcuscope.serial_link import _EpisodeNotice

    emitted: list[int] = []
    n = _EpisodeNotice()
    for _ in range(3):
        n.report(lambda: emitted.append(1))
    assert emitted == [1], "an open episode reported more than once"
    assert n.count == 3 and n.triggered

    n.clear()
    assert not n.triggered and n.count == 0
    n.report(lambda: emitted.append(1))
    assert emitted == [1, 1], "a new episode did not report"


def test_retry_wait_policy_without_the_wall_clock() -> None:
    """The backoff decisions, asserted exactly rather than measured against a slop.

    These four were inferred from elapsed time (0.3 to 0.6 s each, against a 0.05 s slop
    that Windows CI measured 0.391 s into). The waiter is a parameter now, so the policy
    is checked directly and only the poll-really-shortens-the-wait case needs a clock.
    """
    waits: list[float] = []

    def never_stops(seconds: float) -> bool:
        waits.append(seconds)
        return False

    def stops(seconds: float) -> bool:
        waits.append(seconds)
        return True

    # A URL transport has nothing to stat, so it always tests as present: one full wait.
    present = _port(device="socket://127.0.0.1:1")
    assert present._retry_wait(0.4, never_stops) == 0.8        # doubles
    assert present._retry_wait(serial_link.BACKOFF_MAX, never_stops) == serial_link.BACKOFF_MAX
    assert waits == [0.4, serial_link.BACKOFF_MAX]             # capped, not 2x
    assert present._retry_wait(0.4, stops) is None             # stop event wins

    # An absent node is polled for in short slices instead of waited out in one go, so a
    # replug is picked up in a fraction of a second rather than a whole backoff.
    absent = _port(device="/definitely/not/a/device")
    waits.clear()
    third_wait_stops = lambda seconds: (waits.append(seconds), len(waits) >= 3)[1]  # noqa: E731
    assert absent._retry_wait(serial_link.BACKOFF_MAX, third_wait_stops) is None
    assert waits == [serial_link.PRESENCE_POLL_S] * 3, waits
