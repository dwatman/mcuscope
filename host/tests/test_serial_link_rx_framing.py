"""SerialPort's receive framing and its drop accounting: the cap, CR stripping, and the
lines shed at disconnect and detach (SPEC 2.1, SPEC 3.2 rx_dropped)."""

from __future__ import annotations

import asyncio
import time

from mcuscope import serial_link
from mcuscope.link import SourceLink
from mcuscope.serial_link import RX_SAFETY_CAP, SerialPort
from mcuscope.store import Store


def _sys_rows(store: Store) -> list[str]:
    rows, _more = store.query_lines(chans=["sys"], limit=1000, order="asc")
    return [r["raw"] for r in rows]


async def _settle(port: SerialPort) -> None:
    await asyncio.gather(*list(port._bg_tasks))


def _queued(port: SerialPort) -> list[str]:
    return [line for _ts, line in port._rx_lines]


def _feed_chunked(port: SerialPort, data: bytes, size: int = 512) -> None:
    for i in range(0, len(data), size):
        port._on_bytes(time.time(), data[i:i + size])


async def _port(tmp_path, name: str = "rx.db") -> tuple[Store, SerialPort]:
    store = Store(str(tmp_path / name))
    await store.start()
    return store, SerialPort(store, asyncio.get_running_loop(), "board", identify=False)


async def test_an_oversized_line_in_many_reads_stores_no_tail_and_counts_once(tmp_path) -> None:
    store, port = await _port(tmp_path)
    try:
        # The LF of the long line lands mid-read, with a good line behind it in that read.
        _feed_chunked(port, b"x" * 20000 + b"\nafter\n")
        assert _queued(port) == ["after"]
        assert port.rx_dropped == 1
        await _settle(port)
        assert [r for r in _sys_rows(store) if "discarding to its end" in r] == [
            # The cap trips on the first 512-byte read past it: 9 reads, 4608 bytes.
            f"port board: dropped an unterminated line after 4608 bytes, "
            f"over the {RX_SAFETY_CAP} byte cap; discarding to its end"
        ]
    finally:
        await store.stop()


async def test_the_discarded_lines_lf_as_the_first_byte_of_a_read(tmp_path) -> None:
    store, port = await _port(tmp_path)
    try:
        port._on_bytes(time.time(), b"y" * (RX_SAFETY_CAP + 1))
        port._on_bytes(time.time(), b"y" * 100)
        port._on_bytes(time.time(), b"\n<1 OK\n")
        assert _queued(port) == ["<1 OK"]
        assert port.rx_dropped == 1
    finally:
        await store.stop()


async def test_back_to_back_oversized_lines_report_once_until_a_clean_line(tmp_path) -> None:
    store, port = await _port(tmp_path)
    try:
        long_line = b"z" * (RX_SAFETY_CAP + 600) + b"\n"
        _feed_chunked(port, long_line * 3)
        assert _queued(port) == []
        assert port.rx_dropped == 3
        await _settle(port)
        assert sum("discarding to its end" in r for r in _sys_rows(store)) == 1
        _feed_chunked(port, b"clean\n" + long_line)
        assert _queued(port) == ["clean"]
        await _settle(port)
        assert sum("discarding to its end" in r for r in _sys_rows(store)) == 2
    finally:
        await store.stop()


async def test_a_disconnect_ends_the_discard(tmp_path) -> None:
    """The rest of the long line died with the link: the first line after reconnect is
    a fresh line, not the tail being discarded."""
    store, port = await _port(tmp_path)
    try:
        port._on_connect("/dev/x")
        port._on_bytes(time.time(), b"w" * (RX_SAFETY_CAP + 1))
        port._on_disconnect()
        port._on_connect("/dev/x")
        port._on_bytes(time.time(), b"fresh\n")
        assert _queued(port) == ["fresh"]
        assert port.rx_dropped == 1
    finally:
        await store.stop()


async def test_only_one_trailing_cr_is_stripped(tmp_path) -> None:
    store, port = await _port(tmp_path)
    try:
        port._on_bytes(time.time(), b"a\r\r\nb\r\nc\n\r\n")
        assert _queued(port) == ["a\r", "b", "c", ""]
    finally:
        await store.stop()


async def test_a_partial_line_at_disconnect_is_counted_and_named(tmp_path) -> None:
    store, port = await _port(tmp_path)
    try:
        port._on_connect("/dev/x")
        port._on_bytes(time.time(), b"whole\nPARTIAL-0-")
        port._on_disconnect()
        assert port.rx_dropped == 1
        # Positive control: a clean disconnect keeps the plain row and counts nothing.
        port._on_connect("/dev/x")
        port._on_bytes(time.time(), b"NEW\n")
        port._on_disconnect()
        assert port.rx_dropped == 1
        assert _queued(port) == ["whole", "NEW"]
        await _settle(port)
        rows = [r for r in _sys_rows(store) if "disconnected" in r]
        assert rows == [
            "port board disconnected (dropped a 10-byte partial line)",
            "port board disconnected",
        ]
    finally:
        await store.stop()


class _Once:
    """A source that emits `data` once, then nothing."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    def feed(self, data: bytes) -> bytes:
        return b""

    def poll(self) -> bytes:
        data, self.data = self.data, b""
        return data


async def test_a_partial_line_at_detach_is_counted_and_named(tmp_path) -> None:
    """The reader's disconnect callback runs during stop(), when its row is withheld, so
    the partial must reach stop()'s own row."""
    store = Store(str(tmp_path / "detach.db"))
    await store.start()
    try:
        port = SerialPort(store, asyncio.get_running_loop(), "board", device="sim://x",
                          identify=False,
                          open_link_fn=lambda dev, baud: SourceLink(_Once(b"whole\nPART")))
        port.start()
        deadline = time.monotonic() + 5.0
        while port.lines_rx < 1 or not port._rx_bytes:
            assert time.monotonic() < deadline, "the burst never arrived"
            await asyncio.sleep(0.01)
        await port.stop()
        assert port.rx_dropped == 1
        assert "port board: dropped a 4-byte partial line at detach" in _sys_rows(store)
    finally:
        await store.stop()


class _StalledStore:
    """A store whose write queue is full and never drains; sys rows are recorded."""

    def __init__(self) -> None:
        self.sys: list[str] = []

    def submit_line_nowait(self, **kw):
        raise asyncio.QueueFull

    async def submit_line(self, **kw):
        await asyncio.Event().wait()

    async def add_line(self, **kw):
        self.sys.append(kw["raw"])
        return {"id": 0, **kw}


async def test_lines_in_a_consumer_batch_cancelled_by_detach_are_counted() -> None:
    n = 500
    store = _StalledStore()
    data = b"".join(b"line %d\n" % i for i in range(n))
    port = SerialPort(store, asyncio.get_running_loop(), "board", device="sim://x",
                      identify=False, open_link_fn=lambda dev, baud: SourceLink(_Once(data)))
    port.start()
    deadline = time.monotonic() + 5.0
    while port.lines_rx < 1 or port._rx_lines:
        assert time.monotonic() < deadline, "the consumer never took the batch"
        await asyncio.sleep(0.01)
    await port.stop()
    assert port.rx_dropped == n
    assert f"port board: dropped {n} received lines not yet stored at detach" in store.sys


async def test_a_second_oversized_episode_is_reported_again(tmp_path) -> None:
    store, port = await _port(tmp_path)
    try:
        huge = b"x" * (serial_link.RX_SAFETY_CAP + 1)
        port._on_bytes(time.time(), huge + b"\n")
        port._on_bytes(time.time(), b"clean\n")          # ends the episode
        port._on_bytes(time.time(), huge + b"\n")
        await _settle(port)
        assert sum("received line longer than" in r for r in _sys_rows(store)) == 2
    finally:
        await store.stop()
