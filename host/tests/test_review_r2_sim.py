"""End-to-end cover for the round-2 sim/protocol fixes that need the whole stack.

The unit-level halves live in test_protocol.py and test_sim.py; here a non-finite typed
sample is pushed through a live daemon, because the two failures it caused (an
IntegrityError out of the store's insert for NaN, a 500 out of GET /plot/series for Inf)
are only visible past the decoder.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import httpx

from tests.support import Stack

# 7F800000 = +inf, FF800000 = -inf, 7FC00000 = NaN: an uninitialised float, an overflowing
# accumulator and a 0.0/0.0 in ordinary firmware.
NON_FINITE_PATTERNS = ("7F800000", "FF800000", "7FC00000")


def _ingest(stack: Stack, *lines: str) -> None:
    """Feed lines to the daemon's rx path on its own loop, as the serial reader does."""
    ports = stack.app.state.ports
    port = ports._ports[stack.alias]
    for line in lines:
        fut = asyncio.run_coroutine_threadsafe(
            port._store_rx_line(time.time(), line), ports._loop
        )
        fut.result(5.0)


def test_non_finite_typed_sample_is_a_generic_event(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack()
    with httpx.Client(base_url=stack.base_url, timeout=5.0) as c:
        _ingest(stack, "!pd 3 volts:f4 amps:f4")
        for i, pattern in enumerate(NON_FINITE_PATTERNS):
            _ingest(stack, f"!ps 3 {i + 1:X} {pattern},41200000")

        # Not a plot point at all: the whole line is a generic event, as a width mismatch is.
        names = [ch["name"] for ch in c.get("/plot/channels").json()["channels"]]
        assert names == [], f"a non-finite sample was stored as a plot point: {names}"

        # Both channels of the line go, including the finite one beside the bad value.
        for name in ("volts", "amps"):
            r = c.get("/plot/series", params={"name": name})
            assert r.status_code == 200, r.text
            assert r.json()["points"] == []

        # The line itself is kept, so nothing is lost silently.
        rows = c.get("/lines", params={"match": "7F800000"}).json()["lines"]
        assert len(rows) == 1, rows


def test_post_scale_overflow_is_a_generic_event(make_stack: Callable[..., Stack]) -> None:
    """A finite u4 field carried to infinity by its own *scale factor (RG-F13)."""
    stack = make_stack()
    with httpx.Client(base_url=stack.base_url, timeout=5.0) as c:
        _ingest(stack, "!pd 4 big:u4*1e308", "!ps 4 A FFFFFFFF")

        names = [ch["name"] for ch in c.get("/plot/channels").json()["channels"]]
        assert names == [], f"a post-scale infinity was stored: {names}"
        r = c.get("/plot/series", params={"name": "big"})
        assert r.status_code == 200, r.text
        assert r.json()["points"] == []
