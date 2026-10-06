"""The CSV formula guard holds for readers splitting on `;` or TAB, not only `,` (SPEC 3.4)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from mcuscope.server import _csv_cell
from tests.support import mk_app, on_loop


@pytest.mark.parametrize(("raw", "cell"), [
    ("temp ok;=1+41", "temp ok;'=1+41"),
    ("a\t+1", "a\t'+1"),
    ("a;@x;-2", "a;'@x;'-2"),
    ("a;\t=1", "a;'\t'=1"),        # a TAB after `;` is itself a cell start char
    ("=2+40", "'=2+40"),            # the leading guard is unchanged
    ("a;b c", "a;b c"),             # nothing to guard
    ("a=b;c+d", "a=b;c+d"),         # formula chars not at a cell start
])
def test_a_formula_after_a_separator_is_guarded(raw, cell) -> None:
    assert _csv_cell(raw) == cell


def test_a_line_break_starts_a_guarded_cell_and_is_quoted() -> None:
    assert _csv_cell("x\n=1") == "\"x\n'=1\""


def test_an_unguarded_vocabulary_stays_verbatim() -> None:
    assert _csv_cell("-;=x", formula_guard=False) == "-;=x"


def test_the_export_guards_raw_after_a_semicolon(tmp_path) -> None:
    with TestClient(mk_app(tmp_path), base_url="http://127.0.0.1") as c:
        on_loop(c, c.app.state.store.add_line(
            ts=time.time(), port="", dir="rx", chan="debug", seq=None, raw="temp ok;=1+41"))
        body = c.get("/lines/export", params={"format": "csv", "chan": "debug"}).text
    assert body.splitlines()[1].endswith(",temp ok;'=1+41"), body
