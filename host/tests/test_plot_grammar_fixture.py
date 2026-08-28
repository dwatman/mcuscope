"""The daemon half of the shared plot-grammar fixture (plot_grammar_cases.json).

`protocol.py` and `webui/plots.js` decode one grammar (SPEC 2.5) from two hand-written
mirrors, in seven places: value grammar, name grammar, enum labels, bit lanes, channel spec,
definition uniqueness, sample decode. Every drift found so far has the same shape and the
same symptom: the browser charts a stream the daemon stored as a generic event, so the panel
works until the page is reloaded and `mcu plot` shows nothing.

`csv_cell_cases.json` closed that class for the CSV cell. This file is the same treatment for
the plot grammar: one case list, asserted on both sides, so a rule changed on one side fails
the other. `tests/webui_js/plot_grammar.test.mjs` runs the identical file in node.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from mcuscope.protocol import decode_plot_sample, parse_plot_adhoc, parse_plot_def

CASES = json.loads(
    (pathlib.Path(__file__).parent / "plot_grammar_cases.json").read_text(encoding="utf-8")
)


def test_fixture_is_populated() -> None:
    """A missing or emptied file must fail here, not pass as "every case agreed"."""
    assert len(CASES["def"]) >= 20
    assert len(CASES["adhoc"]) >= 10
    assert len(CASES["sample"]) >= 15
    # Both answers exercised in every section, or a parser that refuses everything passes.
    for section, key in (("def", "valid"), ("adhoc", "valid"), ("sample", "decodes")):
        answers = {c[key] for c in CASES[section]}
        assert answers == {True, False}, section


@pytest.mark.parametrize("case", CASES["def"], ids=lambda c: c["line"])
def test_plot_def_cases(case: dict) -> None:
    assert (parse_plot_def(case["line"]) is not None) is case["valid"], case["why"]


@pytest.mark.parametrize("case", CASES["adhoc"], ids=lambda c: c["line"])
def test_plot_adhoc_cases(case: dict) -> None:
    assert (parse_plot_adhoc(case["line"]) is not None) is case["valid"], case["why"]


@pytest.mark.parametrize("case", CASES["sample"], ids=lambda c: f"{c['def']} | {c['line']}")
def test_plot_sample_cases(case: dict) -> None:
    definition = parse_plot_def(case["def"])
    assert definition is not None, f"the fixture's own definition must parse: {case['def']}"
    assert (decode_plot_sample(case["line"], definition) is not None) is case["decodes"], case[
        "why"
    ]
