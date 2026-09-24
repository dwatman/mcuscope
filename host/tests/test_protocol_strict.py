"""Protocol parsers and encoders at their bounds: ASCII-decimal tokens, id ranges and the
line limit (SPEC 2.1 to 2.5)."""

from __future__ import annotations

import pytest

from mcuscope import protocol as p
from mcuscope import sim as sim_module
from mcuscope.serial_link import PortError, SerialPort, _response_seq

# -- integer bounds on device-controlled tokens ----------------------------------------


def test_response_seq_out_of_range_is_ignored() -> None:
    assert _response_seq("<12 OK") == 12
    assert _response_seq("<99999999999999999999999 OK") is None
    assert _response_seq("<-5 OK") is None
    assert _response_seq("<65536 OK") is None


def test_parse_hex_int_is_bounded() -> None:
    assert p.parse_hex_int("FFFFFFFFFFFFFFFF") == 2**64 - 1
    with pytest.raises(p.ProtocolError):
        p.parse_hex_int("1" + "0" * 16)  # 17 digits


def test_can_event_id_range() -> None:
    assert p.parse_can_event("!can 1 - 7FF 00") is not None
    assert p.parse_can_event("!can 1 - 800 00") is None          # > 11-bit std
    assert p.parse_can_event("!can 1 x 1FFFFFFF 00") is not None
    assert p.parse_can_event("!can 1 x 20000000 00") is None     # > 29-bit ext


def test_can_tx_id_range() -> None:
    with pytest.raises(p.ProtocolError):
        p.parse_can_tx_args(("800", "00"))
    with pytest.raises(p.ProtocolError):
        p.parse_can_tx_args(("20000000", "00", "x"))
    assert p.parse_can_tx_args(("7FF", "00")).can_id == 0x7FF


# -- outgoing-line validation ----------------------------------------------------------


def test_encode_wire_rejects_bad_lines() -> None:
    assert SerialPort._encode_wire("i2c scan") == b"i2c scan\n"
    with pytest.raises(PortError):
        SerialPort._encode_wire("foo\nbar")
    with pytest.raises(PortError):
        SerialPort._encode_wire("temp 23°C")
    with pytest.raises(PortError):
        SerialPort._encode_wire("x" * 300)


# -- protocol -------------------------------------------------------------------------


def test_line_limit_is_255_content_bytes() -> None:
    """is_oversized capped content at 254, refusing a line SPEC 2.1 and the firmware allow."""
    assert not p.is_oversized("x" * 255)
    assert p.is_oversized("x" * 256)


def test_format_can_event_rejects_ids_parse_would_refuse() -> None:
    """format and parse must accept the same id set, or a producer emits undecodable lines."""
    with pytest.raises(p.ProtocolError):
        p.format_can_event(p.CanFrame(can_id=0x800, data=b"\xaa", tick_ms=1))
    with pytest.raises(p.ProtocolError):
        p.format_can_event(p.CanFrame(can_id=0x2000_0000, data=b"\xaa", ext=True, tick_ms=1))
    # The maximal legal ids still format, and round-trip through the parser.
    for frame in (
        p.CanFrame(can_id=0x7FF, data=b"\xaa", tick_ms=1),
        p.CanFrame(can_id=0x1FFF_FFFF, data=b"\xaa", ext=True, tick_ms=1),
    ):
        assert p.parse_can_event(p.format_can_event(frame)) is not None


@pytest.mark.parametrize(
    "raw",
    [
        "!can ² - 100 -",      # superscript two: isdigit() is True, int() raises
        "!can 1 r 100 ²",
        # Arabic-Indic three, which the superscript above does not cover: isdecimal() is
        # True for it *and* int() converts it to 3, so the RTR dlc digit accepted it and a
        # garbled line decoded into a can_frames row instead of staying a generic event.
        "!can 1 r 100 ٣",
        "!can 1 - 800 AA",          # id out of range for a standard frame
        "!can 1 x 20000000 AA",     # id out of range for an extended frame
    ],
)
def test_parse_can_event_returns_none_never_raises(raw: str) -> None:
    """SPEC 3.5: a malformed !can line is stored as a generic event, so this returns None."""
    assert p.parse_can_event(raw) is None


def test_decimal_tokens_are_ascii_on_every_can_and_sim_path() -> None:
    """The same token class as the seq/tick/sid fixes, at the three sites they missed.

    `'٣'.isdecimal()` is True and `int('٣')` is 3, so every check written as isdecimal()
    accepts a digit no SPEC grammar allows and no firmware would emit.
    """
    with pytest.raises(p.ProtocolError):
        p.parse_can_tx_args(["100", "٣", "r"])       # host-side `can tx`, from user text
    with pytest.raises(p.ProtocolError):
        sim_module._parse_dec("٣", 0, 10)                   # simulator command arguments
    # The ASCII spelling of each still works, so the check discriminates.
    assert p.parse_can_tx_args(["100", "3", "r"]).dlc == 3
    assert sim_module._parse_dec("3", 0, 10) == 3


def test_parse_plot_adhoc_returns_none_for_non_ascii_digit() -> None:
    assert p.parse_plot_adhoc("!p ² a=1") is None


# -- protocol strictness --------------------------------------------------------------


@pytest.mark.parametrize("token", ["+5", "1_0", "\u0665", " 5", "5.0", "0x5", "", "-1"])
def test_parse_seq_token_is_strict_ascii_decimal(token: str) -> None:
    """Bare int() accepted signs, digit grouping and non-ASCII digits off the wire."""
    with pytest.raises(p.ProtocolError):
        p.parse_seq_token(token)


# " 5" is absent: the line parsers split on whitespace, so it never reaches them as a token.
@pytest.mark.parametrize("token", ["+5", "1_0", "\u0665", "5.0", "0x5"])
def test_wire_lines_reject_loose_seq_tokens(token: str) -> None:
    """A garbled `<+17 OK` would otherwise resolve the pending command for seq 17."""
    with pytest.raises(p.ProtocolError):
        p.parse_response(f"<{token} OK")
    with pytest.raises(p.ProtocolError):
        p.parse_command(f">{token} ping")


def test_response_seq_extraction_is_strict_too() -> None:
    """The fast path that pops a pending entry must agree with the full parser."""
    from mcuscope.serial_link import _response_seq

    assert _response_seq("<17 OK") == 17
    for bad in ("<+17 OK", "<1_7 OK", "<\u0665 OK"):
        assert _response_seq(bad) is None


def test_marker_tick_requires_ascii_digits() -> None:
    """\\d also matches non-ASCII decimal digits, which the rest of the stack never sees."""
    assert p.parse_marker("!m @55 hello").tick_ms == 55
    assert p.parse_marker("!m @\u0665\u0665 hello").tick_ms is None
