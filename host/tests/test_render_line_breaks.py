"""render.fmt_line: one row is one line, and the port column (SPEC 3.4 text export, SPEC 4)."""

from __future__ import annotations

import sys

import pytest

from mcuscope.render import fmt_line

# Every boundary str.splitlines() honours. CR and LF never reach a row (the store folds
# them), but a renderer that trusts that stays one refactor away from splitting rows.
BREAKS = [chr(c) for c in range(sys.maxunicode + 1) if len(f"a{chr(c)}b".splitlines()) > 1]
assert "\n" in BREAKS and "\x85" in BREAKS and "\u2029" in BREAKS


@pytest.mark.parametrize("ch", BREAKS, ids=[f"U+{ord(c):04X}" for c in BREAKS])
def test_a_line_boundary_inside_raw_is_shown_not_obeyed(ch: str) -> None:
    row = {"ts": 0.0, "chan": "debug", "raw": f"before{ch}after", "port": "b"}
    text = fmt_line(row)
    assert len(text.splitlines()) == 1, repr(text)
    assert "before" in text and "after" in text
    escaped = f"\\x{ord(ch):02x}" if ord(ch) < 0x100 else f"\\u{ord(ch):04x}"
    assert text.endswith(f"before{escaped}after")


def test_other_text_is_left_as_captured() -> None:
    """Positive control: TAB, ESC and non-ASCII are not line boundaries and pass through."""
    raw = "a\tb\x1b[31mred\x1b[0m é"
    assert fmt_line({"ts": 0.0, "chan": "debug", "raw": raw, "port": "b"}).endswith(raw)


def test_the_port_column_appears_only_when_asked() -> None:
    row = {"ts": 0.0, "chan": "event", "raw": "x", "port": "board2"}
    assert "[board2]" not in fmt_line(row)
    shown = fmt_line(row, show_port=True)
    assert "[board2]  event| x" in shown
