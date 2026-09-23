"""The daemon half of tests/regex_dialect_cases.json (webui_js/pane_regex_dialect.test.mjs is the
browser half): every pattern a terminal pane accepts matches, in the daemon's own matcher (the one
/lines and /lines/export filter with), exactly the lines the fixture lists, as JavaScript does.
"""

import json
from pathlib import Path

import pytest

from mcuscope.store import _make_regexp

CASES = json.loads((Path(__file__).parent / "regex_dialect_cases.json").read_text("utf-8"))


@pytest.mark.parametrize("case", CASES["same"], ids=lambda c: c["pattern"])
def test_an_accepted_pattern_matches_what_the_pane_matches(case):
    rx = _make_regexp()
    assert [rx(case["pattern"], line) for line in CASES["lines"]] == case["expect"]


@pytest.mark.parametrize("case", CASES["refused"], ids=lambda c: c["pattern"])
def test_a_refused_pattern_reads_here_as_the_fixture_records(case):
    # The browser half shows each of these readings differs from JavaScript's, so no pattern
    # is refused for nothing; this pins the daemon side of that comparison.
    rx = _make_regexp()
    try:
        got = [rx(case["pattern"], line) for line in CASES["lines"]]
    except Exception:
        got = "refused"
    assert got == case["daemon"]
