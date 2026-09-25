"""`mcu-sim --flood` after a stall resumes at its rate instead of repaying the backlog (SPEC 7)."""

from __future__ import annotations

from mcuscope import sim as mcu_sim

RATE = 20000


def _lines_in_one_second(stall_s: float) -> int:
    s = mcu_sim.Simulator(mcu_sim.build_parser().parse_args(["--flood", str(RATE)]))
    t0 = s.next_flood
    start = t0 + stall_s
    return sum(len(s._poll_flood(start + k * 0.01)) for k in range(100))


def test_a_long_stall_is_not_backfilled_at_the_burst_cap() -> None:
    emitted = _lines_in_one_second(3600.0)
    # Repaying the hour emitted FLOOD_MAX_BURST every 10 ms pass: 500000 lines this second.
    assert emitted <= mcu_sim.FLOOD_MAX_BURST + RATE, emitted
    assert emitted >= RATE * 0.95, f"the flood did not resume at its rate: {emitted}"


def test_without_a_stall_the_rate_is_met() -> None:
    emitted = _lines_in_one_second(0.0)
    assert RATE * 0.95 <= emitted <= RATE * 1.05, emitted


def test_a_hiccup_under_the_cap_is_still_caught_up() -> None:
    """The re-anchor is for a stall only: 0.1 s late owes 2000 lines, under the cap, and
    they are all emitted on the next pass."""
    s = mcu_sim.Simulator(mcu_sim.build_parser().parse_args(["--flood", str(RATE)]))
    assert len(s._poll_flood(s.next_flood + 0.1)) in (2000, 2001)   # float rounding
