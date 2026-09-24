"""Store session lookups: a ref is an id or a name, never a fallback between them, and
session reads stay bounded (SPEC 3.4)."""

from __future__ import annotations

import asyncio

from mcuscope.store import Store
from tests.support import add_sys


def test_session_line_count_is_bounded_at_both_ends(tmp_path) -> None:
    # The per-session COUNT runs on the event loop, once per listed session. A running
    # session's `end_id IS NULL OR id <= end_id` upper bound is not sargable, so the count
    # scanned to the end of the table; COALESCE keeps both ends a seek.
    from mcuscope.store import SESSION_LIST_SQL

    async def run() -> None:
        store = Store(str(tmp_path / "plan.db"))
        await store.start()
        try:
            await store.start_session("run-a")
            await add_sys(store, "one")
            assert store.list_sessions()[0]["lines"] >= 1
            plan = " ".join(
                str(r[3])
                for r in store._conn.execute(
                    "EXPLAIN QUERY PLAN " + SESSION_LIST_SQL, (50,)
                ).fetchall()
            ).replace(" ", "")
            # Both ends of the range, not just `rowid>?`: an open upper bound is the
            # 2060 ms plan at 1M lines.
            assert "rowid>?" in plan and "rowid<?" in plan, plan
        finally:
            await store.stop()

    asyncio.run(run())


def test_delete_session_does_not_fall_back_to_a_name_match(tmp_path) -> None:
    """get_session is id-only: resolve_session's name fallback deleted the wrong run.

    A session *named* "99" answered a lookup for id 99, so the route deleted a different
    session's lines and left the label behind pointing at the deleted range.
    """
    db = tmp_path / "cap.db"

    async def run() -> None:
        store = Store(str(db))
        await store.start()
        try:
            sess = await store.start_session("99")
            assert store.get_session(sess["id"]) is not None
            missing = 99 if sess["id"] != 99 else 98
            assert store.get_session(missing) is None          # id-only: no match
            assert store.resolve_session(str(missing)) is not None  # name fallback still works
        finally:
            await store.stop()

    asyncio.run(run())


def test_session_ref_is_an_ascii_decimal_or_a_name(tmp_path) -> None:
    """resolve_session's id branch gated on isdecimal(), which fails both ways.

    A 5000-digit ref reached `int()` and raised past CPython's conversion limit: an
    unhandled 500 with a traceback on `GET /sessions/{ref}/export` and on every endpoint
    taking `session=`. And a session *named* with another script's digit resolved to the
    id that digit converts to, which is the wrong-session bug this branch already carries a
    comment about.
    """
    async def run() -> None:
        store = Store(str(tmp_path / "sess.db"))
        await store.start()
        try:
            # Three sessions first, so the id the digit converts to (3) exists and belongs
            # to someone else - otherwise the id branch falls through to the name branch on
            # its own and the assertion below passes either way.
            for name in ("one", "two", "three"):
                await store.start_session(name)
            named = await store.start_session("٣")     # Arabic-Indic three
            assert named["id"] == 4
            assert store.resolve_session("9" * 5000) is None    # no raise: not an id token
            assert store.resolve_session("٣")["id"] == 4, "resolved to session id 3 instead"
            # And the ordinary spellings still resolve, by id and by name.
            assert store.resolve_session("3")["name"] == "three"
            assert store.resolve_session("one")["id"] == 1
        finally:
            await store.stop()

    asyncio.run(run())


def _captured_plan(store: Store, run, keyword: str = "SELECT") -> list[str]:
    """EXPLAIN the statement the store actually issued, not a copy of it.

    Same mechanism as support.captured_plan: the statement comes off the connection's trace
    callback, with its parameters already substituted.
    """
    seen: list[str] = []
    store._conn.set_trace_callback(seen.append)
    try:
        run()
    finally:
        store._conn.set_trace_callback(None)
    matching = [s for s in seen if s.lstrip().upper().startswith(keyword)]
    assert matching, f"the store issued no {keyword} statement: {seen}"
    return [str(r[3]) for r in store._conn.execute("EXPLAIN QUERY PLAN " + matching[-1])]


def test_active_session_does_not_read_every_session_when_none_is_running(tmp_path) -> None:
    # RG-F5, class 20. `ended_ts IS NULL` is not sargable without an index, so the
    # ORDER BY id DESC LIMIT 1 short-circuits only while a session is running; the quiet
    # case read the whole table, on the loop, from GET /status. sessions is never trimmed.
    async def run() -> None:
        store = Store(str(tmp_path / "plan.db"))
        await store.start()
        try:
            for i in range(20):
                await store.start_session(f"run-{i}")
                await add_sys(store, f"line {i}")
                await store.stop_session()
            assert store.active_session() is None, "the expensive case is the quiet one"
            assert not store._conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_stat1'"
            ).fetchall(), "the store must never ANALYZE; the shipped plan is the statless one"

            rows = _captured_plan(store, store.active_session)
            assert any("idx_sessions_active" in r for r in rows), \
                f"active_session does not use the partial index: {rows}"
            assert not any("TEMP B-TREE" in r for r in rows), rows

            # And it still answers correctly with one running, which is what the partial
            # index has to keep true: a row enters the index only while ended_ts is NULL.
            await store.start_session("live")
            assert store.active_session()["name"] == "live"
            rows = _captured_plan(store, store.active_session)
            assert any("idx_sessions_active" in r for r in rows), rows
        finally:
            await store.stop()

    asyncio.run(run())
