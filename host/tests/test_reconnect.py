"""Reconnect timing: presence-gated polling for a device that comes back (SPEC 3.1).

The reader thread used to sleep out a doubling backoff between open attempts regardless
of why the last one failed, so a replug that finished at t=8 s was not noticed until the
next scheduled attempt. These tests pin the two halves of `_retry_wait`: fast polling
while the device is absent, full backoff while it is present but unopenable.
"""

from __future__ import annotations

import asyncio
import gc
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import serial

from mcuscope import serial_link
from mcuscope import sim as mcu_sim
from mcuscope.link import BurstThenError, Link, SourceLink
from mcuscope.serial_link import BACKOFF_MIN, JOIN_TIMEOUT, PortError, SerialPort
from mcuscope.store import Store
from tests.support import UNOPENABLE, UNOPENABLE_ALT, Scripted, SimEndpoint, SpyLink

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


def test_a_slower_scan_does_not_overwrite_a_fresher_one(monkeypatch) -> None:
    """Two reader threads can scan concurrently. The one that started earlier but finished
    later saw the older world, so installing its result would hide a device that just
    appeared for another whole TTL - the replug latency this cache is here to cut."""
    monkeypatch.setattr(serial_link, "_comports_cache", (0.0, []))
    old, new = [_Info("/dev/old")], [_Info("/dev/new")]
    scanning = threading.Event()
    finish = threading.Event()

    def slow_scan():
        scanning.set()
        finish.wait(5)
        return old

    monkeypatch.setattr(serial_link.list_ports, "comports", slow_scan)
    slow = threading.Thread(target=serial_link.cached_comports, daemon=True)
    slow.start()
    assert scanning.wait(5), "the slow scan never started"

    monkeypatch.setattr(serial_link.list_ports, "comports", lambda: new)
    assert serial_link.cached_comports() == new   # started later, finished first
    finish.set()
    slow.join(timeout=5)
    assert serial_link._comports_cache[1] == new, "the older scan overwrote the newer one"


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


# -- the reader's success path (a scripted SourceLink) ---------------------------------
#
# Before the Link seam these could not be written: the only transport a SerialPort could
# obtain was a real one, so every reader-thread test above drives a device that can never
# open, and the burst/drain/post cycle - the hottest code in the module - ran untested.


def _fake_port(script, store, loop, cancellable: bool = False):
    """A SerialPort whose transport is an in-memory script, plus the exhaustion event."""
    exhausted = threading.Event()
    links: list[SpyLink] = []

    def opener(device: str, baud: int) -> SpyLink:
        # Only the first link plays the script; a reconnect gets a quiet one, so a
        # replay cannot inflate the counts a test is reading.
        source = Scripted(
            script if not links else [], exhausted=exhausted, idle_after=True,
        )
        link = SpyLink(source, device=device, cancellable=cancellable)
        links.append(link)
        return link

    port = SerialPort(store, loop, "board", device="/dev/fake", open_link_fn=opener)
    return port, exhausted, links


async def _drive(script, tmp_path, expect_lines: int, cancellable: bool = False):
    """Run the reader over `script` and return the rows the store received."""
    from mcuscope.store import Store

    store = Store(str(tmp_path / "reader.db"))
    await store.start()
    loop = asyncio.get_running_loop()
    port, exhausted, links = _fake_port(script, store, loop, cancellable=cancellable)
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
    """The teardown order the write lock exists for: cancel_write, then close.

    `cancellable` models a native port, which is the only transport that can actually
    cancel: pyserial's URL handlers cannot, and a link that says it can where production
    cannot would let this pass over a path production never takes.
    """
    _, _, links = await _drive([b"x\n"], tmp_path, 1, cancellable=True)
    assert links, "the reader never opened a link"
    assert links[0].closed
    assert links[0].cancelled_writes >= 1
    # And stop() unblocks the read, or a detach pays the full read timeout before the
    # thread even notices the stop event.
    assert links[0].cancelled_reads >= 1


async def test_reader_reopens_after_the_link_drops(tmp_path) -> None:
    """A read error costs the connection, not the reader thread."""
    from mcuscope.store import Store

    store = Store(str(tmp_path / "reopen.db"))
    await store.start()
    loop = asyncio.get_running_loop()
    opened: list[SourceLink] = []

    def opener(device: str, baud: int) -> SourceLink:
        # First link dies after one line; the second delivers and then stalls quietly.
        script = [b"first\n", serial.SerialException("dropped")] if not opened else []
        link = SourceLink(Scripted(script, idle_after=bool(opened)), device=device)
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
        assert links[0]._source.fed == [b"ping\n"]
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
    assert n.triggered

    n.clear()
    assert not n.triggered
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


def test_a_reappeared_device_settles_before_the_open_and_restarts_the_backoff(
    monkeypatch,
) -> None:
    """The node can appear a moment before it is openable (udev rules still landing), so
    the poll that finds it waits out the settle grace instead of racing straight into an
    open that fails - and a reappearance is a fresh situation, so the backoff starts over
    rather than continuing from wherever it had doubled to."""
    present: list[bool] = []
    monkeypatch.setattr(
        serial_link, "cached_comports",
        lambda *a, **k: [_Info("/dev/ttyFAKE", "SN1")] if present else [],
    )
    waits: list[float] = []

    def waiter(seconds: float) -> bool:
        waits.append(seconds)
        present.append(True)   # the device turns up during the first poll slice
        return False

    port = _port(serial_number="SN1")
    assert port._retry_wait(serial_link.BACKOFF_MAX, waiter) == BACKOFF_MIN
    assert waits == [serial_link.PRESENCE_POLL_S, serial_link.PRESENCE_SETTLE_S], waits


# -- teardown: the handle and the callback that outlive stop() -------------------------
#
# stop() can be outlived four ways: the join deadline expires with the reader still in a
# read, an open completes after stop() returned, a callback is posted after the loop
# closed, and a sys row is spawned after the barrier. Each one shows itself as a held
# handle or a task dying pending, and each is Windows-visible only (an exclusive COM
# handle keeps the next attach out), so they are emulated here rather than left to a CI
# leg nobody can run locally.


class _WedgedLink(Link):
    """A link whose read blocks the way a native one does: without holding a Python lock.

    SourceLink cannot stand in here - its read holds the same lock close() takes, so a
    close from the loop thread would politely wait for the read instead of racing it,
    which is the whole situation under test.
    """

    def __init__(self, device: str = "/dev/wedged") -> None:
        self.device = device
        self.closed = False
        self.reading = threading.Event()
        self.release = threading.Event()

    def read(self, n: int) -> bytes:
        self.reading.set()
        self.release.wait(10)
        return b""

    def drain(self, buf: bytearray) -> None:
        pass

    def write(self, data: bytes) -> None:
        pass

    def close(self) -> None:
        self.closed = True


async def test_stop_closes_the_handle_of_a_reader_that_is_not_coming_back(
    tmp_path, monkeypatch
) -> None:
    """Windows serial handles are exclusive, so a handle left held by a wedged reader
    fails the next attach of that COM port with ERROR_ACCESS_DENIED - for good."""
    monkeypatch.setattr(serial_link, "JOIN_TIMEOUT", 0.2)
    links: list[_WedgedLink] = []

    def opener(device: str, baud: int) -> _WedgedLink:
        link = _WedgedLink(device)
        links.append(link)
        return link

    store = Store(str(tmp_path / "wedged.db"))
    await store.start()
    port = SerialPort(
        store, asyncio.get_running_loop(), "board", device="/dev/fake", open_link_fn=opener
    )
    port.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not (links and links[0].reading.is_set()):
            await asyncio.sleep(0.01)
        # Wait for the read itself, not just the open: stopping between the two would
        # take the reader down the late-open path instead of the wedged one.
        assert links and links[0].reading.is_set(), "the reader never reached a read"
        await port.stop()
        assert port._thread.is_alive(), "the reader exited, so this proves nothing"
        assert links[0].closed, "the handle stayed with a thread that is not coming back"
    finally:
        links[0].release.set()
        port._thread.join(timeout=5)
        await store.stop()


async def test_a_link_opened_after_the_join_deadline_is_closed_not_leaked(
    tmp_path, monkeypatch
) -> None:
    """A socket:// connect runs to pyserial's 5 s POLL_TIMEOUT, past the join deadline, so
    stop() read a handle that was still None and closed nothing. Nobody else will."""
    monkeypatch.setattr(serial_link, "JOIN_TIMEOUT", 0.2)
    stopped = threading.Event()
    opened: list[_WedgedLink] = []

    def slow_opener(device: str, baud: int) -> _WedgedLink:
        assert stopped.wait(10), "stop() never returned"
        link = _WedgedLink(device)
        opened.append(link)
        return link

    store = Store(str(tmp_path / "late.db"))
    await store.start()
    port = SerialPort(
        store, asyncio.get_running_loop(), "board", device="/dev/fake",
        open_link_fn=slow_opener,
    )
    port.start()
    await port.stop()
    stopped.set()
    port._thread.join(timeout=5)
    await store.stop()
    assert opened, "the opener never ran"
    assert opened[0].closed, "the late link was left open by the reader that owned it"


def test_a_callback_posted_after_the_loop_closed_is_dropped_not_raised() -> None:
    """That RuntimeError would escape _reader and kill the thread through the excepthook,
    leaving its handle open - and there is nothing left to deliver to anyway."""
    closed = asyncio.new_event_loop()
    closed.close()
    SerialPort(None, closed, "board")._post(lambda: None)

    class _LiveButRefusing:
        def call_soon_threadsafe(self, fn, *args) -> None:
            raise RuntimeError("scheduling refused")

        def is_closed(self) -> bool:
            return False

    # A live loop refusing a callback is a real fault and must not be swallowed with it.
    with pytest.raises(RuntimeError):
        SerialPort(None, _LiveButRefusing(), "board")._post(lambda: None)


async def test_a_late_callback_after_stop_spawns_no_sys_row(tmp_path) -> None:
    """The reader can still post callbacks after stop() gave up waiting for it; a task
    spawned after the barrier has nothing left to await it and dies pending at loop close.
    """
    store = Store(str(tmp_path / "late-row.db"))
    await store.start()
    try:
        port = SerialPort(store, asyncio.get_running_loop(), "board")
        await port.stop()
        port._on_error("read error: the reader was still going")
        assert port._bg_tasks == set(), "a sys row was spawned after the barrier"
    finally:
        await store.stop()


async def test_stop_does_not_wait_out_a_store_that_is_not_answering(tmp_path) -> None:
    """The sys-row barrier is bounded and then cancels, so a wedged store costs the row
    and not the detach - or a Ctrl-C would hang on the way out."""
    store = Store(str(tmp_path / "barrier.db"))
    await store.start()
    try:
        port = SerialPort(store, asyncio.get_running_loop(), "board")
        never = asyncio.Event()

        async def wedged(text: str) -> None:
            await never.wait()

        port._store_sys = wedged
        port._spawn_sys("a row the store will never take")
        assert port._bg_tasks, "nothing was spawned, so the barrier is not being tested"
        task = next(iter(port._bg_tasks))
        await asyncio.wait_for(port.stop(), timeout=10)
        assert task.cancelled(), "stop() left a sys-row task running"
    finally:
        await store.stop()


async def test_a_link_that_raises_from_cancel_still_gets_closed(tmp_path) -> None:
    """Both cancels sit outside a guard: the one in the reader's finally would kill the
    thread and leak the very handle the close beside it was about to take, and the one in
    stop() would come out of a detach as a 500."""

    class _AngryCancel(SpyLink):
        def cancel_read(self) -> bool:
            raise RuntimeError("cancel exploded")

        def cancel_write(self) -> bool:
            raise RuntimeError("cancel exploded")

    store = Store(str(tmp_path / "angry.db"))
    await store.start()
    links: list[_AngryCancel] = []

    def opener(device: str, baud: int) -> _AngryCancel:
        script = [b"x\n", serial.SerialException("dropped")] if not links else []
        link = _AngryCancel(
            Scripted(script, idle_after=bool(links)), device=device
        )
        links.append(link)
        return link

    port = SerialPort(
        store, asyncio.get_running_loop(), "board", device="/dev/fake", open_link_fn=opener
    )
    port.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(links) < 2:
            await asyncio.sleep(0.01)
    finally:
        await port.stop()
        await store.stop()
    assert len(links) >= 2, "the reader died on the raising cancel instead of reopening"
    assert links[0].closed, "the handle leaked with the exception"


# -- what a detach does to lines the store never took ----------------------------------


async def test_a_detach_counts_the_lines_the_store_never_took(tmp_path) -> None:
    """Every other shedding path counts into rx_dropped and files a sys row; this one
    threw away up to RX_QUEUE_MAX received lines with /status still reporting zero.

    Reachable on every `mcu port detach`, every reconnect and every shutdown whenever the
    store is behind - which is the only condition the queue bound exists for.
    """
    store = Store(str(tmp_path / "detach.db"))
    await store.start()
    try:
        loop = asyncio.get_running_loop()
        port = SerialPort(store, loop, "board")
        storing = asyncio.Event()
        held = asyncio.Event()

        async def wedged_store(batch) -> None:
            storing.set()
            await held.wait()

        port._store_rx_batch = wedged_store
        port._consumer_task = loop.create_task(port._consume())

        first = b"".join(f"line {i}\n".encode() for i in range(50))
        port._on_bytes(time.time(), first)
        await asyncio.wait_for(storing.wait(), timeout=5)
        # The consumer is inside the store with the first burst; the second one queues
        # behind it and is what a detach strands.
        port._on_bytes(time.time(), first)
        assert len(port._rx_lines) == 50

        await port.stop()
        assert port.rx_dropped == 50, "the queued lines vanished without being counted"
        assert any("dropped 50 received lines" in row for row in _sys_rows(store))
    finally:
        await store.stop()


async def test_a_partial_line_does_not_survive_the_reconnect(tmp_path) -> None:
    """The fragment left over from the old connection used to be glued onto the first line
    of the new one, corrupting exactly one line per replug - and misclassifying it, not
    merely making it ugly, when that line was a `<seq` response or a `!can` frame."""
    store = Store(str(tmp_path / "partial.db"))
    await store.start()
    opened: list[SourceLink] = []

    def opener(device: str, baud: int) -> SourceLink:
        script = (
            [b"PARTIAL-", serial.SerialException("dropped")] if not opened
            else [b"NEW LINE\n"]
        )
        link = SourceLink(Scripted(script, idle_after=bool(opened)), device=device)
        opened.append(link)
        return link

    port = SerialPort(
        store, asyncio.get_running_loop(), "board", device="/dev/fake", open_link_fn=opener
    )
    port.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and port.lines_rx < 1:
            await asyncio.sleep(0.01)
    finally:
        await port.stop()
    rows, _ = store.query_lines(limit=200, order="asc")
    await store.stop()
    raws = [r["raw"] for r in rows if r["chan"] != "sys"]
    assert "NEW LINE" in raws, raws
    assert not any(raw.startswith("PARTIAL-") for raw in raws), raws


async def test_one_unstorable_line_settles_the_rest_and_resolves_its_command(
    tmp_path,
) -> None:
    """The settle half of the burst, which the submit half's test does not reach: a store
    future that fails used to end the loop, so every later line in the burst lost its
    settle - and any command waiting on a response in it waited out its whole timeout."""
    store = Store(str(tmp_path / "settle.db"))
    await store.start()
    try:
        loop = asyncio.get_running_loop()
        port = SerialPort(store, loop, "board")
        real_submit = store.submit_line

        async def submit(**kw):
            fut = await real_submit(**kw)
            if kw["raw"].startswith("<7 "):
                failed = loop.create_future()
                failed.set_exception(RuntimeError("the row never landed"))
                return failed
            return fut

        store.submit_line = submit
        waiting = loop.create_future()
        port._pending[7] = serial_link._Pending(7, waiting, time.time())

        batch = [*_GOOD, "<7 OK done", *_MORE]
        await port._store_rx_batch([(time.time(), line) for line in batch])
        await _settle(port)

        assert _raws(store, ["debug"]) == _GOOD + _MORE, "the burst stopped at the bad line"
        assert port.rx_dropped == 1
        with pytest.raises(PortError):
            await asyncio.wait_for(waiting, timeout=1)
    finally:
        await store.stop()


async def test_a_write_to_a_broken_handle_is_a_port_error(tmp_path) -> None:
    """The reader can close the handle underneath a write, and pyserial's exception has to
    become a PortError, or send_command's cleanup is skipped and the endpoint answers 500.
    """

    class _Broken(_WedgedLink):
        def write(self, data: bytes) -> None:
            raise serial.SerialException("handle went away")

    port = _port(device="/dev/fake")
    port._link = _Broken()
    with pytest.raises(PortError, match="write failed"):
        port._write_bytes(b"ping\n")


# -- the remaining episode notices -----------------------------------------------------


async def test_the_can_decode_notice_rearms_on_a_frame_that_decodes(tmp_path) -> None:
    """One sys row per episode, not per bad frame - and a frame that decodes ends the
    episode, so the next bout of garbage is news again."""
    store = Store(str(tmp_path / "can.db"))
    await store.start()
    try:
        port = SerialPort(store, asyncio.get_running_loop(), "board")
        for _ in range(3):
            assert port._decode_can("!can not a frame") is None
        assert port._decode_can("!can 100 - 100 DEADBEEF") is not None
        assert port._decode_can("!can still not a frame") is None
        await _settle(port)
        rows = [row for row in _sys_rows(store) if "decode failure" in row]
        assert len(rows) == 2, rows
    finally:
        await store.stop()


async def test_the_overflow_notice_rearms_once_the_queue_drains(tmp_path, monkeypatch) -> None:
    """The clear condition is a drain below half, and it lives in the consumer rather than
    beside the report - so a second overflow after a recovery reports again."""
    monkeypatch.setattr(serial_link, "RX_QUEUE_MAX", 10)
    store = Store(str(tmp_path / "overflow.db"))
    await store.start()
    try:
        loop = asyncio.get_running_loop()
        port = SerialPort(store, loop, "board")
        burst = b"".join(f"line {i}\n".encode() for i in range(14))
        port._on_bytes(time.time(), burst)
        assert port.rx_dropped == 4
        assert port._queue_overflow.triggered

        port._consumer_task = loop.create_task(port._consume())
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and port._queue_overflow.triggered:
            await asyncio.sleep(0.01)
        assert not port._queue_overflow.triggered, "a drained queue never re-armed"

        port._on_bytes(time.time(), burst)
        await port.stop()
        rows = [row for row in _sys_rows(store) if "queue overflow" in row]
        assert len(rows) == 2, rows
    finally:
        await store.stop()


# -- the attach cap and the harness's own opener ---------------------------------------


def test_the_attach_cap_refuses_the_port_past_it(monkeypatch) -> None:
    """A client looping attach over fresh aliases would otherwise exhaust the thread and
    handle budget. The number is configuration; the refusal is the invariant, so a small
    cap tests it at a fraction of the cost."""

    async def run() -> None:
        monkeypatch.setattr(serial_link, "MAX_PORTS", 3)
        store = Store(":memory:")
        await store.start()
        mgr = serial_link.PortManager(store, asyncio.get_running_loop())
        try:
            for i in range(3):
                await mgr.attach(f"p{i}", device=UNOPENABLE)
            with pytest.raises(PortError):
                await mgr.attach("one-too-many", device=UNOPENABLE)
            assert mgr.get("one-too-many") is None
            # Replacing an alias that is already attached adds no port, so the cap must
            # not refuse a reconnect of the last one.
            await mgr.attach("p0", device=UNOPENABLE_ALT)
            for i in range(3):
                await mgr.detach(f"p{i}")
        finally:
            await store.stop()

    asyncio.run(run())


def test_the_harness_simulator_only_answers_for_a_sim_device() -> None:
    """support.SimEndpoint is what nearly every test in the suite attaches to. Answering
    for every device connected the ports that document "attaches, never connects" to a
    simulator, so those tests stopped exercising the path they document while staying
    green - and the shipped `--sim` opener beside it had the identical bug, where it meant
    a real configured board was served out of the simulator."""
    endpoint = SimEndpoint(mcu_sim.build_parser().parse_args([]))
    assert isinstance(endpoint.open("sim://board", 115200), SourceLink)
    with pytest.raises(serial.SerialException):
        endpoint.open(UNOPENABLE, 115200)
    assert len(endpoint.links) == 1, "a real device string was handed a simulator"


# -- abandoned command futures ---------------------------------------------------------


class _NoStore:
    """Stands in for the Store: send_command's tx-row insert, and nothing else."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def add_line(self, **kw):
        if self.fail:
            raise RuntimeError("the row never landed")
        return {"id": 1}


class _Unretrieved:
    """Collects asyncio's "Future exception was never retrieved" reports for this loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.reports: list[str] = []
        self._prev = loop.get_exception_handler()

    def __enter__(self) -> _Unretrieved:
        self.loop.set_exception_handler(lambda loop, ctx: self.reports.append(ctx["message"]))
        return self

    def __exit__(self, *exc) -> bool:
        self.loop.set_exception_handler(self._prev)
        return False

    async def collect(self) -> list[str]:
        # Future.__del__ is what files the report, so the collection has to happen first.
        gc.collect()
        await asyncio.sleep(0)
        return [msg for msg in self.reports if "never retrieved" in msg]


async def test_a_disconnect_during_a_command_leaves_no_unretrieved_future() -> None:
    """A command abandoning its registered future must consume it first.

    _fail_pending set an exception on the pending future while the command's own write
    (or its tx-row insert) was failing off-loop, and send_command then left by its except
    path without ever awaiting that future: asyncio logged one "Future exception was
    never retrieved" per disconnect-during-command when the future was collected. The
    ordering is forced here rather than raced, so both halves of it are deterministic.
    """
    loop = asyncio.get_running_loop()
    for scenario in ("write", "store"):
        port = SerialPort(_NoStore(fail=scenario == "store"), loop, "board")
        failed = threading.Event()

        def fail_pending(port=port, failed=failed) -> None:
            port._fail_pending(PortError("port board disconnected"))
            failed.set()

        def write(data: bytes, scenario=scenario, fail_pending=fail_pending, failed=failed) -> None:
            # Off the loop, exactly where a real write sits when the reader posts the
            # disconnect that fails this command's future.
            loop.call_soon_threadsafe(fail_pending)
            assert failed.wait(10), "the disconnect never landed"
            if scenario == "write":
                raise PortError("port board write failed: handle went away")

        port._write_bytes = write
        with _Unretrieved(loop) as watch:
            with pytest.raises((PortError, RuntimeError)):
                await port.send_command("ping", 5000)
            assert not port._pending
            assert await watch.collect() == [], f"{scenario}: the future was left unretrieved"


async def test_a_cancelled_or_timed_out_command_consumes_its_future() -> None:
    """The other two exits that abandon the future: the response timeout, and a
    cancellation (client disconnect, Ctrl-C) delivered while the response is awaited with
    a disconnect already having set the exception."""
    loop = asyncio.get_running_loop()
    port = SerialPort(_NoStore(), loop, "board")
    port._write_bytes = lambda data: None

    with _Unretrieved(loop) as watch:
        result = await port.send_command("ping", 20)
        assert result["status"] == "timeout"
        assert not port._pending
        assert await watch.collect() == []

        task = loop.create_task(port.send_command("ping", 5000))
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and port.lines_tx < 2:
            await asyncio.sleep(0.01)
        assert port.lines_tx == 2, "the second command never reached its wait"
        pend = next(iter(port._pending.values()))
        # The disconnect lands first and the cancellation second, both before the task
        # runs again: the exception is set on a future the task is then never resumed to
        # retrieve. The other order cancels the future outright and leaves nothing set.
        port._fail_pending(PortError("port board disconnected"))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pend.future.done()
        assert not port._pending
        del pend
        assert await watch.collect() == []


async def test_a_handle_closed_out_from_under_a_wedged_reader_reads_as_not_connected(
    tmp_path, monkeypatch
) -> None:
    """stop() closing the handle has to clear the port reference under the same lock.

    Leaving it set meant every write until the outlived reader's own finally ran reached
    a closed handle and reported "write failed: ...", when the truth is that the port is
    not connected at all - a health surface saying something untrue about why.
    """
    monkeypatch.setattr(serial_link, "JOIN_TIMEOUT", 0.2)

    class _ClosedHandle(_WedgedLink):
        def write(self, data: bytes) -> None:
            if self.closed:
                raise serial.SerialException("the handle is closed")

    links: list[_ClosedHandle] = []

    def opener(device: str, baud: int) -> _ClosedHandle:
        link = _ClosedHandle(device)
        links.append(link)
        return link

    store = Store(str(tmp_path / "closed.db"))
    await store.start()
    port = SerialPort(
        store, asyncio.get_running_loop(), "board", device="/dev/fake", open_link_fn=opener
    )
    port.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not (links and links[0].reading.is_set()):
            await asyncio.sleep(0.01)
        assert links and links[0].reading.is_set(), "the reader never reached a read"
        await port.stop()
        assert port._thread.is_alive(), "the reader exited, so this proves nothing"
        assert port._link is None, "a closed handle was left as the port's link"
        with pytest.raises(PortError, match="not connected"):
            port._write_bytes(b"ping\n")
    finally:
        links[0].release.set()
        port._thread.join(timeout=5)
        await store.stop()


async def test_a_port_with_no_device_says_so(tmp_path) -> None:
    """The sys row said "no device for serial_number None", which is a health surface
    saying something untrue about why it is not connected."""
    store = Store(str(tmp_path / "nodev.db"))
    await store.start()
    try:
        port = SerialPort(store, asyncio.get_running_loop(), "board")
        port.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not _sys_rows(store):
            await asyncio.sleep(0.01)
        await port.stop()
        rows = _sys_rows(store)
        assert rows == ["port board: no device configured"], rows
    finally:
        await store.stop()
