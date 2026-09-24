"""`mcu-sim`'s command-line flags: integers on the ASCII-decimal grammar and bounded, and
`--flap` a finite duration (SPEC 7)."""

from __future__ import annotations

import pytest

from mcuscope import sim as mcu_sim

# -- C-7: integer and float argv on the wire grammar ------------------------------------


NOT_DECIMAL = ["١٨٦٢٣", " 5", "5 ", "+5", "1_0", "0x10", "5.0", "", "1" * 21]


@pytest.mark.parametrize("flag", ["--tcp-port", "--drop-response", "--flood"])
@pytest.mark.parametrize("value", NOT_DECIMAL)
def test_sim_integer_flags_refuse_what_int_would_take(flag, value, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        mcu_sim.build_parser().parse_args([f"{flag}={value}"])
    assert exc.value.code == 2
    assert f"argument {flag}: not a decimal integer" in capsys.readouterr().err


@pytest.mark.parametrize(("flag", "value", "bound"), [
    ("--tcp-port", "-1", "must be 0..65535, got -1"),
    ("--tcp-port", "65536", "must be 0..65535, got 65536"),
    ("--drop-response", "-1", "must be >= 0, got -1"),
    ("--flood", "-5", "must be >= 0, got -5"),
])
def test_sim_integer_flags_refuse_out_of_range(flag, value, bound, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        mcu_sim.build_parser().parse_args([f"{flag}={value}"])
    assert exc.value.code == 2
    assert f"argument {flag}: {bound}" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "1e999", "-1", "1_0", " 1", "٥", "+1"])
def test_flap_refuses_a_value_that_is_not_a_finite_duration(value, capsys) -> None:
    """`nan > 0` is False, so `--flap nan` switched flapping off without a word."""
    with pytest.raises(SystemExit) as exc:
        mcu_sim.build_parser().parse_args([f"--flap={value}"])
    assert exc.value.code == 2
    assert "argument --flap: must be a finite number of seconds >= 0" in capsys.readouterr().err


def test_sim_flags_still_take_their_documented_values() -> None:
    args = mcu_sim.build_parser().parse_args(
        ["--tcp-port", "0", "--drop-response", "2", "--flood", "20000", "--flap", "0.5"]
    )
    assert (args.tcp_port, args.drop_response, args.flood, args.flap) == (0, 2, 20000, 0.5)
    assert mcu_sim.build_parser().parse_args(["--tcp-port", "65535", "--flap", "0"]).flap == 0.0
