"""--decode hides only the `!pd` lines it learned (SPEC 4 `--decode`)."""

from __future__ import annotations

from mcuscope.cli_output import LineDecoder

PD = "!pd 0 vbat:u2*0.01:V"


def test_a_token_that_merely_starts_with_pd_is_shown() -> None:
    dec = LineDecoder()
    assert dec.decode("!pdo 5V 3A", "b") == "!pdo 5V 3A"
    assert dec.decode("!psx 1 2", "b") == "!psx 1 2"


def test_a_definition_the_grammar_rejects_is_shown() -> None:
    assert LineDecoder().decode("!pd", "b") == "!pd"
    assert LineDecoder().decode("!pd 0 nonsense", "b") == "!pd 0 nonsense"


def test_a_valid_definition_is_learned_and_hidden() -> None:
    """Positive control: the drop still happens for the line it is for."""
    dec = LineDecoder()
    assert dec.decode(PD, "b") is None
    assert dec.decode("!ps 0 10 09FA", "b") == "s0 vbat=25.54V"
