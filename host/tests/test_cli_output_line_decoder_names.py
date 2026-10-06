"""The CLI's LineDecoder replays stored rows: the daemon's ingest name cap is not its to apply."""

from __future__ import annotations

from mcuscope import protocol as p
from mcuscope.cli_output import LineDecoder


def test_a_history_past_the_ingest_cap_still_decodes() -> None:
    dec = LineDecoder(names=None, changes=False)
    n = p.ADHOC_NAMES_MAX + 44
    out = [dec.decode(f"!p 1 n{i}={i}", port="board") for i in range(n)]
    assert out[-1] == f"p:n{n - 1} n{n - 1}={n - 1}"
    assert all(o is not None and o.startswith("p:") for o in out)
