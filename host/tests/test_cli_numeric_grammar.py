"""Numbers on the `mcu` command line and in its environment are ASCII decimal (SPEC 4).

click's INT and FLOAT are bare int()/float(), which take other scripts' digits, `_`
grouping, `+` and padding and rewrite them: `mcu i2c rd 48 ٣` sent `i2c rd 48 3`.
"""

from __future__ import annotations

import json

import pytest
import typer

from mcuscope import cli, cli_daemonctl
from mcuscope.cli_output import (
    AsciiFloat,
    AsciiFloatRange,
    AsciiInt,
    AsciiIntRange,
    click_types,
)
from tests.support import UNREACHABLE, recorder

ARABIC_3 = "٣"


@pytest.mark.parametrize("value", [ARABIC_3, "1_0", "+3", " 3", "3 ", "0x3"])
def test_an_i2c_count_that_is_not_ascii_decimal_sends_nothing(monkeypatch, capsys,
                                                              value) -> None:
    seen = recorder(monkeypatch)
    rc = cli.main([*UNREACHABLE, "i2c", "rd", "48", value])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "is not an ASCII decimal integer" in err, err
    assert seen == [], [r.url.path for r in seen]


def test_an_ascii_count_reaches_the_board_as_typed(monkeypatch, capsys) -> None:
    """Positive control for the refusals above."""
    seen = recorder(monkeypatch, cmd={"status": "ok", "data": ""})
    rc = cli.main([*UNREACHABLE, "i2c", "rd", "48", "10"])
    assert rc == 0, capsys.readouterr().err
    assert json.loads(seen[-1].content)["cmd"] == "i2c rd 48 10"


@pytest.mark.parametrize("argv", [
    ["lines", "--limit", ARABIC_3],
    ["tail", "-n", "1_0"],
    ["can", "tx", "100", "--rtr", ARABIC_3],
    ["cmd", "ping", "--timeout", ARABIC_3 * 4],
])
def test_other_numeric_options_refuse_non_ascii_digits(monkeypatch, capsys, argv) -> None:
    seen = recorder(monkeypatch)
    rc = cli.main([*UNREACHABLE, *argv])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "is not an ASCII decimal integer" in err, err
    assert seen == []


@pytest.mark.parametrize("value", [ARABIC_3, "1_0", "nan", "inf", "1e999", "0x1"])
def test_a_float_option_refuses_what_float_would_rewrite(monkeypatch, capsys, value) -> None:
    seen = recorder(monkeypatch)
    rc = cli.main([*UNREACHABLE, "plot", "channels", "--active", value])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "is not an ASCII decimal finite number" in err, err
    assert seen == []


@pytest.mark.parametrize("value", ["1.5", "2", ".5", "1e1", "1.5E-1"])
def test_a_float_option_takes_ascii_decimal(monkeypatch, capsys, value) -> None:
    recorder(monkeypatch, plot_channels={"channels": []})
    rc = cli.main([*UNREACHABLE, "plot", "channels", "--active", value])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert "no active plot channels" in out.out   # --active was read (applied client-side)


def test_a_range_still_applies_after_the_grammar(capsys) -> None:
    rc = cli.main([*UNREACHABLE, "lines", "--limit", "-1"])
    err = capsys.readouterr().err
    assert rc == 1 and "-1 is not in the range x>=0" in err, err


def test_every_numeric_parameter_takes_the_ascii_grammar() -> None:
    """The root group converts the built tree, so an option added later is covered too."""
    root = typer.main.get_command(cli.app)
    found: list[str] = []
    todo = [(root, "mcu")]
    while todo:
        cmd, path = todo.pop()
        for prm in cmd.params:
            # By class, not name: typer versions name the same types differently.
            if isinstance(prm.type, (click_types.IntParamType, click_types.FloatParamType)):
                assert isinstance(prm.type, (AsciiInt, AsciiIntRange, AsciiFloat,
                                             AsciiFloatRange)), (path, prm.name)
                found.append(f"{path} {prm.name}")
        todo.extend((sub, f"{path} {name}") for name, sub in getattr(cmd, "commands", {}).items())
    assert len(found) >= 33, found   # the sweep's count; the walk itself must have run


# -- MCUSCOPE_START_TIMEOUT ------------------------------------------------------------------


@pytest.mark.parametrize("value", ["abc", ARABIC_3, "1_0", "nan", " 5"])
def test_an_unreadable_start_timeout_warns_and_uses_the_default(monkeypatch, capsys,
                                                                value) -> None:
    monkeypatch.setenv("MCUSCOPE_START_TIMEOUT", value)
    assert cli_daemonctl._start_timeout_default() == 20.0
    err = capsys.readouterr().err
    assert "MCUSCOPE_START_TIMEOUT" in err and "using 20" in err, err


@pytest.mark.parametrize("value, want", [("2.5", 2.5), ("60", 60.0), ("0.1", 0.5)])
def test_a_readable_start_timeout_is_used_without_a_warning(monkeypatch, capsys, value,
                                                            want) -> None:
    monkeypatch.setenv("MCUSCOPE_START_TIMEOUT", value)
    assert cli_daemonctl._start_timeout_default() == want
    assert capsys.readouterr().err == ""
