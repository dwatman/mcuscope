"""A stopped match query names the limit that stopped it.

The per-call timeout stops a catastrophic pattern ("simplify the regex"); the whole-query
budget stops an honest pattern scanning too large a window, where simplifying cannot help
and narrowing the window can. Both were one message.
"""

from __future__ import annotations

import functools
import sqlite3
import time

import pytest

from mcuscope import store as store_mod
from mcuscope.store import MatchBudgetExceeded, Store

CATASTROPHIC = r"(?:a{1,3}){2,40}b"   # backtracks for seconds on a run of `a`


@pytest.fixture(params=["file", "memory"])
async def store(request, tmp_path):
    # A file capture runs match queries on a worker; an in-memory one inline on the loop.
    s = Store(str(tmp_path / "m.db") if request.param == "file" else ":memory:")
    await s.start()
    for _ in range(3):
        await s.add_line(ts=time.time(), port="p", dir="rx", chan="debug", seq=None,
                         raw="a" * 60)
    yield s
    await s.stop()


def _budget(monkeypatch, seconds: float) -> None:
    monkeypatch.setattr(store_mod, "_make_regexp",
                        functools.partial(store_mod._make_regexp, budget_s=seconds))


async def test_a_spent_window_budget_says_to_narrow_the_window(store, monkeypatch) -> None:
    _budget(monkeypatch, 0.0)
    with pytest.raises(MatchBudgetExceeded) as exc:
        await store.query_lines_safe(match="never", limit=5)
    msg = str(exc.value)
    assert "window is too large" in msg and "session, last_ms, since_id" in msg, msg
    assert "simplify" not in msg


async def test_a_call_cut_short_by_the_budget_is_the_windows_fault(store, monkeypatch) -> None:
    # The per-call timeout is min(MATCH_TIMEOUT_S, what is left of the budget): when the
    # budget is what shortened it, the window is still the thing to narrow.
    _budget(monkeypatch, store_mod.MATCH_TIMEOUT_S / 5)
    with pytest.raises(MatchBudgetExceeded, match="window is too large"):
        await store.query_lines_safe(match=CATASTROPHIC, limit=5)


async def test_a_catastrophic_pattern_says_to_simplify_it(store) -> None:
    with pytest.raises(MatchBudgetExceeded) as exc:
        await store.query_lines_safe(match=CATASTROPHIC, limit=5)
    assert "simplify the regex" in str(exc.value)
    assert "window" not in str(exc.value)


async def test_a_worker_read_connection_carries_no_regexp(tmp_path) -> None:
    # A closure cached with the connection spends its budget once and then fails every
    # later match with a bare OperationalError; without one, a misuse fails at once.
    s = Store(str(tmp_path / "noregexp.db"))
    await s.start()
    try:
        with pytest.raises(sqlite3.OperationalError, match="no such function: REGEXP"):
            await s._offload(lambda conn: conn.execute("SELECT 'a' REGEXP 'a'").fetchone())
        rows, _ = await s.query_lines_safe(match="x", limit=5)   # the real path registers its own
        assert rows == []
    finally:
        await s.stop()
