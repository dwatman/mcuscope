"""SPEC 2.1: only U+0020 separates tokens, in every line protocol.py parses.

The plot lines are pinned in plot_grammar_cases.json, shared with the web UI. These are the
other parsers that split a line: `!can`, responses and the marker tick.
"""

from __future__ import annotations

import sys

import pytest

from mcuscope import protocol as p

# Every code point str.split() treats as whitespace and the wire grammar does not: all but
# U+0020, and CR and LF, which end the line before a parser sees it.
NON_SPACE_WS = [chr(c) for c in range(sys.maxunicode + 1)
                if chr(c).isspace() and chr(c) not in " \r\n"]
assert "\t" in NON_SPACE_WS and "\x1f" in NON_SPACE_WS and "\u3000" in NON_SPACE_WS


@pytest.mark.parametrize("ws", NON_SPACE_WS, ids=[f"U+{ord(c):04X}" for c in NON_SPACE_WS])
def test_can_event_does_not_split_on_other_whitespace(ws: str) -> None:
    assert p.parse_can_event(f"!can{ws}100 - 123 AA") is None
    assert p.parse_can_event(f"!can 100 -{ws}123 AA") is None
    # Positive control: the same line with a space decodes.
    assert p.parse_can_event("!can 100 - 123 AA") is not None


def test_can_event_collapses_a_run_of_spaces() -> None:
    frame = p.parse_can_event("!can  100   -  123 AA")
    assert frame is not None and frame.can_id == 0x123 and frame.tick_ms == 100


@pytest.mark.parametrize("ws", NON_SPACE_WS, ids=[f"U+{ord(c):04X}" for c in NON_SPACE_WS])
def test_response_does_not_split_on_other_whitespace(ws: str) -> None:
    with pytest.raises(p.ProtocolError, match="neither OK nor ERR"):
        p.parse_response(f"<5 OK{ws}data")
    assert p.parse_response("<5 OK  a   b").data == "a b"


@pytest.mark.parametrize("ws", NON_SPACE_WS, ids=[f"U+{ord(c):04X}" for c in NON_SPACE_WS])
def test_plot_decoder_entry_points_split_on_spaces_only(ws: str) -> None:
    d = p.PlotDecoder()
    assert d.learn(f"!pd 0 a:u1{ws}b:u1") is False
    assert d.learn("!pd 0 a:u1 b:u1") is True   # positive control
    assert d.feed(f"!ps 0 1{ws}01,02") is None
    assert d.points(f"!p 10 a=1{ws}b=2") is None
    assert d.feed("!ps  0 1  01,02") is not None
    assert d.points("!p 10  a=1   b=2") == [(10, None, "a", 1.0), (10, None, "b", 2.0)]


def test_can_tx_flags_refuse_the_event_dash() -> None:
    with pytest.raises(p.ProtocolError, match="any of x, r"):
        p.parse_can_tx_args(["100", "01", "-"])
    assert p.parse_can_tx_args(["100", "01", "x"]).ext is True


@pytest.mark.parametrize("ws", NON_SPACE_WS, ids=[f"U+{ord(c):04X}" for c in NON_SPACE_WS])
def test_marker_tick_is_a_space_delimited_token(ws: str) -> None:
    marker = p.parse_marker(f"!m {ws}@5 hi")
    assert marker is not None and marker.tick_ms is None
    # Positive control: spaces around the tick still read as a tick.
    assert p.parse_marker("!m   @5   hi") == p.Marker(text="hi", tick_ms=5)
