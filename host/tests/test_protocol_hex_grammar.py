"""Hex tokens are bare pairs (SPEC 2.1), and "space" in a marker or command is U+0020 (2.5).

`bytes.fromhex` skips whitespace between pairs, so a tab inside a hex token decoded to fewer
bytes than the token's length promised.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from mcuscope import protocol as p
from mcuscope import sim as mcu_sim
from mcuscope.serial_link import SerialPort
from mcuscope.store import Store


# Even lengths, so the refusal is the grammar's and not the odd-length check's.
@pytest.mark.parametrize("text", ["0a\t0b\t", "0a 0b ", "0a\n0b\n", "\t0a\t0", "0a0\u0660"])
def test_hex_with_whitespace_inside_is_refused(text: str) -> None:
    with pytest.raises(p.ProtocolError, match="hex payload"):
        p.hex_to_bytes(text)


def test_bare_hex_pairs_still_decode() -> None:
    assert p.hex_to_bytes("0A0b") == b"\n\x0b"
    assert p.hex_to_bytes("") == b""


async def _stored(tmp_path, *lines: str) -> tuple[Store, SerialPort, list[dict]]:
    store = Store(str(tmp_path / "cap.db"))
    await store.start()
    port = SerialPort(store, asyncio.get_running_loop(), "board", identify=False)
    await port._store_rx_batch([(time.time(), line) for line in lines])
    rows, _more = store.query_lines(limit=100, order="asc")
    return store, port, rows


async def test_a_plot_sample_with_tabs_in_a_field_is_a_generic_event(tmp_path) -> None:
    """An f4 field of eight characters holding three bytes reached struct.unpack, which
    raised: the reader dropped the line instead of keeping it as a generic event."""
    good = "!ps 0 100 3F800000"
    bad = "!ps 0 101 0a\t0b\t0c"
    store, port, rows = await _stored(tmp_path, "!pd 0 x:f4", good, bad)
    try:
        assert port.rx_dropped == 0
        chans = {r["raw"]: r["chan"] for r in rows}
        assert chans[bad] == "event" and chans[good] == "event", rows
        points = store._conn.execute("SELECT COUNT(*) FROM plot_points").fetchone()[0]
        assert points == 1, "only the clean sample stored a point"
    finally:
        await store.stop()


async def test_a_can_event_with_tabs_in_its_payload_stores_no_frame(tmp_path) -> None:
    store, _port, _rows = await _stored(tmp_path, "!can 100 - 100 DEAD", "!can 101 - 100 DE\tAD\t")
    try:
        frames = store._conn.execute("SELECT tick_ms, dlc FROM can_frames").fetchall()
        assert [tuple(f) for f in frames] == [(100, 2)], "only the clean frame is stored"
    finally:
        await store.stop()


def test_the_simulator_refuses_can_tx_with_tabs_in_the_payload() -> None:
    s = mcu_sim.Simulator(mcu_sim.build_parser().parse_args([]))
    assert s.handle_line(">5 can tx 100 DE\tAD\t") == ["<5 ERR 2 badarg invalid hex payload"]
    assert s.handle_line(">6 can tx 100 DEAD")[0].startswith("<6 OK"), "positive control"


# -- D-9: U+0020 is the only space --------------------------------------------------------


def test_a_marker_whose_text_is_a_control_byte_is_kept() -> None:
    assert p.parse_marker("!m @5 \x1f") == p.Marker(text="\x1f", tick_ms=5)
    assert p.parse_marker("!m @5 \t hi \t") == p.Marker(text="\t hi \t", tick_ms=5)
    assert p.parse_marker("!m @5   ") is None


def test_format_marker_follows_the_same_rule() -> None:
    assert p.parse_marker(p.format_marker("\x1f")) == p.Marker(text="\x1f", tick_ms=None)
    # A tab before the sigil makes it text, both ways.
    assert p.parse_marker(p.format_marker("\t@5 x")) == p.Marker(text="\t@5 x", tick_ms=None)
    with pytest.raises(p.ProtocolError, match="empty"):
        p.format_marker("   ")


def test_a_command_keeps_a_trailing_control_byte() -> None:
    assert p.format_command(7, "ping\x1f") == ">7 ping\x1f"
    assert p.format_command(7, "  ping  ") == ">7 ping"
    with pytest.raises(p.ProtocolError, match="empty command"):
        p.format_command(7, "   ")
