"""A stopped match query names the limit that stopped it.

The per-call timeout stops a catastrophic pattern ("simplify the regex"); the whole-query
budget stops an honest pattern scanning too large a window, where simplifying cannot help
and narrowing the window can. Both were one message.
"""

from __future__ import annotations

import functools
import sqlite3
import threading
import time

import pytest

from mcuscope import store as store_mod
from mcuscope.config import StorageConfig
from mcuscope.store import MatchBudgetExceeded, Store
from tests.support import Stack, on_loop, stack_client

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


# -- regex denial of service ----------------------------------------------------------

# 17 characters, no nested +/*, and exponential. Chosen deliberately: MAX_MATCH_LEN=200 is
# no defence (7 characters suffice), and a static "nested quantifier" screen would pass
# this one while flagging the harmless textbook examples. Only a real timeout works.
POISON_PATTERN = r"(?:a{1,3}){2,40}b"


def _poison_app(tmp_path):
    from mcuscope.config import Config, ServerConfig
    from mcuscope.server import create_app

    return create_app(Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db"), retention_days=7),
        ports=[],
    ))


def test_catastrophic_pattern_is_refused_and_does_not_freeze_the_process(tmp_path) -> None:
    """A user regex must not be able to stop the daemon.

    Running match queries on a dedicated pool was never containment: CPython's `re` holds
    the GIL for the whole of a backtrack, so one short pattern froze the entire process
    (measured: a 10 ms heartbeat got 1 tick in 2.4 s), with no recovery but a restart. The
    daemon supports LAN exposure, so that is a remote DoS. Matching now runs on the `regex`
    engine, which releases the GIL and can be interrupted by a timeout.

    This asserts both halves: the request is refused quickly, AND an independent thread
    keeps running while it happens.
    """
    from fastapi.testclient import TestClient

    app = _poison_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert c.post("/marker", json={"text": "a" * 60}).status_code == 200

        ticks = [0]
        stop = threading.Event()

        def heartbeat() -> None:
            while not stop.is_set():
                ticks[0] += 1
                time.sleep(0.01)

        beat = threading.Thread(target=heartbeat, daemon=True)
        beat.start()
        started = time.monotonic()
        resp = c.get("/lines", params={"match": POISON_PATTERN, "limit": 10})
        elapsed = time.monotonic() - started
        stop.set()
        beat.join(timeout=2)

    assert resp.status_code == 400
    assert "budget" in resp.json()["error"]
    assert elapsed < 5.0, f"took {elapsed:.1f}s; the per-call timeout did not fire"
    # The GIL half. Under stdlib `re` this was ~1 tick regardless of how long it ran.
    assert ticks[0] >= elapsed * 20, (
        f"only {ticks[0]} heartbeat ticks in {elapsed:.2f}s: the matcher held the GIL"
    )


def test_catastrophic_pattern_refused_on_retrospective_assert(tmp_path) -> None:
    """400, never 500, and never a hang.

    Exit 2 from `mcu wait` already means "pattern valid, nothing matched in the window",
    so a killed pattern must be a 400 (CLI exit 1) rather than a timeout result. `mcu
    assert` never exits 2 at all, which is the other reason this cannot be a timeout.
    """
    from fastapi.testclient import TestClient

    app = _poison_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        on_loop(c, c.app.state.store.add_line(
            ts=time.time(), port="", dir="rx", chan="debug", seq=None, raw="a" * 60))
        started = time.monotonic()
        resp = c.post("/assert", json={"expect": [POISON_PATTERN], "timeout_ms": 0})
        elapsed = time.monotonic() - started

    assert resp.status_code == 400, resp.text
    assert "budget" in resp.json()["error"]
    assert elapsed < 10.0


def test_live_window_matchers_are_budgeted(tmp_path) -> None:
    """The /wait and /assert live paths match through _search_batch / _scan_batch.

    Driven directly: a live window only runs the pattern when rows actually arrive during
    it, so an HTTP-level test of `since="now"` returns a plain timeout without ever
    exercising the matcher. These are the functions that see hostile input.
    """
    import mcuscope.server as server_mod
    from mcuscope.store import MatchBudgetExceeded

    pattern = server_mod.regex.compile(POISON_PATTERN)
    texts = ["a" * 60] * 5

    started = time.monotonic()
    with pytest.raises(MatchBudgetExceeded):
        server_mod._search_batch(pattern, texts)
    assert time.monotonic() - started < 5.0

    started = time.monotonic()
    with pytest.raises(MatchBudgetExceeded):
        server_mod._scan_batch([pattern], texts)
    assert time.monotonic() - started < 5.0

    # An honest pattern over the same texts still returns a result, not an exception.
    assert server_mod._search_batch(server_mod.regex.compile("a{10}"), texts) == 0


def test_ordinary_patterns_are_unaffected(tmp_path) -> None:
    """The budget must not be reachable by honest use."""
    from fastapi.testclient import TestClient

    app = _poison_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.post("/marker", json={"text": "hello world"})
        c.post("/marker", json={"text": "goodbye"})
        rows = c.get("/lines", params={"match": "hell.", "limit": 10}).json()["lines"]
        assert any("hello world" in r["raw"] for r in rows)
        # An invalid pattern is a 400 with a readable message, not an opaque 500.
        bad = c.get("/lines", params={"match": "((("})
        assert bad.status_code == 400
        assert "bad match regex" in bad.json()["error"]


def test_a_catastrophic_match_pattern_is_stopped_by_the_budget(stack: Stack) -> None:
    # The whole job of the budget is to stop a stall, and nothing in the suite tripped it.
    with stack_client(stack) as c:
        assert c.post("/marker", json={"text": "a" * 400}).status_code == 200
        r = c.get("/lines", params={"match": "(a|a)+b", "limit": 100})
    assert r.status_code == 400
    assert "budget" in r.json()["error"]
