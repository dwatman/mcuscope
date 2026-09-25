"""`--decode` over a sample whose non-finite point the protocol dropped (SPEC 2.5)."""

from __future__ import annotations

import json

import httpx
import websockets

from mcuscope.cli_output import LineDecoder
from tests.support import UNREACHABLE, ScriptedWS, canned

PD = "!pd 0 a:f4 b:f4"
INF_B = "!ps 0 2 3F800000,7F800000"   # a = 1.0, b = +inf (dropped)


def test_a_dropped_point_renders_as_a_dash() -> None:
    dec = LineDecoder()
    assert dec.decode(PD) is None
    assert dec.decode(INF_B) == "s0 a=1 b=-"
    assert dec.decode("!ps 0 3 7F800000,3F800000") == "s0 a=- b=1"
    assert dec.decode("!ps 0 4 40000000,40400000") == "s0 a=2 b=3"   # positive control


def test_a_dropped_point_does_not_end_a_decoding_follow(monkeypatch, capsys) -> None:
    """The `next()` walk raised StopIteration, a RuntimeError out of the follow's guard."""
    from mcuscope import cli

    def row(i: int, raw: str) -> dict:
        return {"id": i, "ts": 1.0, "port": "sim", "dir": "rx", "chan": "event", "seq": None,
                "raw": raw}

    canned(monkeypatch, lambda request: httpx.Response(200, json={
        "lines": [], "truncated": False, "ports": []}))
    frames = [[row(1, PD)], [row(2, INF_B)], [row(3, "after the sample")]]
    monkeypatch.setattr(websockets, "connect",
                        lambda *a, **kw: ScriptedWS([json.dumps(f) for f in frames]))
    rc = cli.main([*UNREACHABLE, "tail", "-f", "-n", "0", "--decode"])
    out, err = capsys.readouterr()
    assert rc == 3, err   # the scripted close, not a crash
    assert "s0 a=1 b=-" in out, out + err
    assert "after the sample" in out, out + err
