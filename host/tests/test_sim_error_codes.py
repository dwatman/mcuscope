"""The simulator answers what the reference firmware answers (SPEC 2.3, 2.4).

Each case is one the firmware and the simulator used to disagree on: the code an agent is
told decides whether it fixes its arguments or its command.
"""

from __future__ import annotations

import mcu_sim
import pytest

from mcuscope import protocol as p
from mcuscope import sim as sim_module


@pytest.fixture
def sim() -> mcu_sim.Simulator:
    return mcu_sim.Simulator(mcu_sim.build_parser().parse_args([]))


def wire(sim: mcu_sim.Simulator, line: str) -> list[str]:
    """The lines the simulator would put on the wire for one command."""
    return sim_module.encode_lines(sim.handle_line(line)).decode("ascii").splitlines()


@pytest.mark.parametrize(
    "cmd",
    ["spi", "spi foo", "adc", "adc get", "can", "can wobble", "can0", "can0 wobble",
     "can3 foo", "can9", "can2 wobble"],
)
def test_unknown_or_missing_subcommand_is_badcmd(sim: mcu_sim.Simulator, cmd: str) -> None:
    assert wire(sim, f">7 {cmd}")[0].startswith("<7 ERR 1 badcmd")


@pytest.mark.parametrize(
    "cmd", ["can0 tx 100 -", "can9 stat", "can0 filter all", "spi xfer imu", "adc read"]
)
def test_known_subcommand_with_bad_arguments_is_badarg(sim: mcu_sim.Simulator, cmd: str) -> None:
    assert wire(sim, f">7 {cmd}")[0].startswith("<7 ERR 2 badarg")


def test_can_tx_refuses_the_event_dash_as_flags(sim: mcu_sim.Simulator) -> None:
    assert wire(sim, ">7 can tx 100 01 -")[0].startswith("<7 ERR 2 badarg")
    assert wire(sim, ">8 can tx 100 01")[0] == "<8 OK"   # positive control


def test_an_error_detail_never_turns_the_code_into_overflow(sim: mcu_sim.Simulator) -> None:
    # A 255-byte line with an unknown name fits the limit; echoing the name as detail
    # would not, and must cost the detail, not the code.
    line = ">57928 " + "F" * 248
    assert len(line) == p.MAX_LINE_BYTES
    assert wire(sim, line) == ["<57928 ERR 1 badcmd"]
    # An OK payload that overflows is still ERR 8 (SPEC 2.3).
    assert sim_module.encode_lines(["<3 OK " + "A" * 300]) == b"<3 ERR 8 overflow\n"


def _encode(line: str) -> list[str]:
    return sim_module.encode_lines([line]).decode("ascii").splitlines()


def test_long_event_is_cut_on_a_token_boundary_with_a_notice() -> None:
    cells = "!p 4000000000" + "".join(f" cell{i:02d}={1000 + i}" for i in range(19))
    assert _encode(cells + " current_ma=123456") == [cells, "!e event p overflow"]
    # Exactly the limit: sent whole, no notice.
    at_limit = "!q " + "y" * 252
    assert len(at_limit) == p.MAX_LINE_BYTES
    assert _encode(at_limit) == [at_limit]
    # The limit falls on a boundary: the byte past it is a space, so every token fits.
    assert _encode(at_limit + " zz") == [at_limit, "!e event q overflow"]
    # A run of spaces before the cut leaves none trailing.
    assert _encode("!q    " + "y" * 300) == ["!q", "!e event q overflow"]
    # No space to cut at: nothing but the notice, with no type to quote.
    assert _encode("!" + "x" * 300) == ["!e event ? overflow"]
    # A first token over 16 characters is not quoted.
    assert _encode("!" + "w" * 20 + " " + "w" * 280) == ["!" + "w" * 20, "!e event ? overflow"]


def test_long_mark_command_is_cut_like_the_firmware(sim: mcu_sim.Simulator) -> None:
    # A 10-digit tick makes the longest `mark` command overflow its `!m @<tick> ` line.
    sim.state.start_ns -= 4_000_000_000 * 1_000_000
    text = "calibration " + "m" * 235
    assert len(f">7 mark {text}") == p.MAX_LINE_BYTES
    assert wire(sim, f">7 mark {text}") == ["<7 OK"]
    out = sim_module.encode_lines(sim.poll_events()).decode("ascii").splitlines()
    markers = [ln for ln in out if ln.startswith(("!m ", "!e "))]
    assert len(markers) == 2 and markers[1] == "!e event m overflow", out
    assert markers[0].endswith(" calibration") and len(markers[0]) <= p.MAX_LINE_BYTES
