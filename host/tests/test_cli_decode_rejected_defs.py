"""--decode hides only the `!pd` lines it learned (SPEC 4 `--decode`)."""

from __future__ import annotations

import json

import pytest
import typer

from mcuscope import cli
from mcuscope.cli_client import Settings
from mcuscope.cli_output import LineDecoder
from tests.support import DEAD
from tests.test_cli import _ScriptedWS

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


# -- F-12: a follow learns a hidden !pd under its own port ----------------------------------


def test_follow_learns_a_filtered_out_redefinition_under_its_port(monkeypatch,
                                                                  capsys) -> None:
    import websockets

    frames = [
        json.dumps([{"id": 1, "ts": 1.0, "port": "a", "chan": "event", "raw": "!pd 7 amps:u1"}]),
        json.dumps([{"id": 2, "ts": 2.0, "port": "a", "chan": "event", "raw": "!ps 7 1 05"}]),
    ]
    monkeypatch.setattr(websockets, "connect", lambda url, **kw: _ScriptedWS(frames),
                        raising=False)
    dec = LineDecoder()
    dec.prime(["!pd 7 volts:u1"], "a")
    s = Settings(url=DEAD, json_out=False, port=None)
    with pytest.raises(typer.Exit):
        cli._follow_ws(s, None, "^!ps", dec=dec)   # --match hides the !pd row
    out = capsys.readouterr().out
    assert "s7 amps=5" in out, out
