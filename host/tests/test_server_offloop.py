"""Work the daemon keeps off the event loop and off the default executor: match queries,
counts and time-bounded reads (SPEC 3.4)."""

from __future__ import annotations

import asyncio
import threading
import time

from mcuscope.store import Store
from tests.support import mk_app, on_loop
from tests.test_export_lines_can import T0, _add

# -- regex isolation and /ws keepalive -------------------------------------------------


def test_match_executor_is_bounded_and_separate() -> None:
    from mcuscope.store import MATCH_WORKERS, match_executor

    pool = match_executor()
    assert pool is match_executor()          # one process-wide pool, not one per call
    assert pool._max_workers == MATCH_WORKERS
    assert pool._thread_name_prefix == "mcu-match"


async def test_match_queries_run_off_the_default_executor(tmp_path, monkeypatch) -> None:
    # A user regex must not occupy a default-executor worker: that pool is also what
    # run_in_executor(None, ...) uses to join the serial reader thread on detach.
    seen: dict[str, str] = {}
    original = Store._query_lines_threadsafe

    def spy(self, **kwargs):
        seen["thread"] = threading.current_thread().name
        return original(self, **kwargs)

    monkeypatch.setattr(Store, "_query_lines_threadsafe", spy)
    store = Store(str(tmp_path / "cap.db"))
    await store.start()
    try:
        await store.add_line(ts=time.time(), port="p", dir="rx", chan="debug", seq=None, raw="hi")
        rows, _ = await store.query_lines_safe(match="hi")
        assert len(rows) == 1
    finally:
        await store.stop()
    assert seen["thread"].startswith("mcu-match")


async def test_wait_scan_runs_off_the_default_executor(tmp_path, monkeypatch) -> None:
    # Same guarantee for the live path: /wait scans each burst on the match pool.
    from httpx import ASGITransport, AsyncClient

    from mcuscope import server as server_mod

    seen: dict[str, str] = {}
    original = server_mod._search_batch

    def spy(pattern, texts):
        seen["thread"] = threading.current_thread().name
        return original(pattern, texts)

    monkeypatch.setattr(server_mod, "_search_batch", spy)
    app = mk_app(tmp_path)
    transport = ASGITransport(app=app)
    # Loopback base_url: the same-origin guard requires a Host it can legitimately answer
    # to (an IP literal, localhost, or the configured bind name), so a synthetic "test"
    # hostname is refused before it reaches the route.
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        async with app.router.lifespan_context(app):
            store = app.state.store

            async def feed() -> None:
                await asyncio.sleep(0.05)
                await store.add_line(
                    ts=time.time(), port="", dir="rx", chan="debug", seq=None, raw="marco polo"
                )

            task = asyncio.create_task(feed())
            body = await client.post("/wait", json={"match": "polo", "timeout_ms": 3000})
            await task
    assert body.json()["status"] == "match"
    assert seen["thread"].startswith("mcu-live-match")   # live scans have their own pool


def _count_thread_spy(monkeypatch) -> dict[str, str]:
    # Spies on the read itself rather than on whatever offloads it, so this keeps
    # asserting the property that matters (it did not run on the loop thread) instead of
    # the mechanism that delivers it.
    seen: dict[str, str] = {}
    original = Store.count_lines

    def spy(self, **kwargs):
        seen["thread"] = threading.current_thread().name
        return original(self, **kwargs)

    monkeypatch.setattr(Store, "count_lines", spy)
    return seen


async def test_purge_dry_run_counts_off_the_loop(tmp_path, monkeypatch) -> None:
    # The dry-run count reads the whole selected range (44 ms at 1M rows, 230 ms at 3M):
    # it belongs on the match pool with the other whole-capture reads, not on the loop.
    from httpx import ASGITransport, AsyncClient

    seen = _count_thread_spy(monkeypatch)
    app = mk_app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        async with app.router.lifespan_context(app):
            store = app.state.store
            for i in range(3):
                await store.add_line(
                    ts=time.time(), port="", dir="rx", chan="debug", seq=None, raw=f"l{i}"
                )
            r = await client.post("/purge", json={"all": True, "dry_run": True})
    assert r.json()["deleted"] >= 3   # plus the daemon's own start/session rows
    assert seen["thread"].startswith("mcu-match")


async def test_assert_checked_lines_counts_off_the_loop(tmp_path, monkeypatch) -> None:
    # `mcu assert --expect X` defaults to --timeout 0, so this is the default invocation:
    # the count sits beside match queries that were deliberately offloaded.
    from httpx import ASGITransport, AsyncClient

    seen = _count_thread_spy(monkeypatch)
    app = mk_app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        async with app.router.lifespan_context(app):
            store = app.state.store
            await store.add_line(
                ts=time.time(), port="", dir="rx", chan="debug", seq=None, raw="marco"
            )
            r = await client.post("/assert", json={"expect": ["marco"], "timeout_ms": 0})
    body = r.json()
    assert body["status"] == "pass" and body["checked_lines"] >= 1
    assert seen["thread"].startswith("mcu-match")


def test_lines_with_until_ts_runs_off_the_loop(client, monkeypatch) -> None:
    _add(client, ts=T0, raw="line0")

    async def ident() -> int:
        return threading.get_ident()

    loop_thread = on_loop(client, ident())
    threads: list[int] = []
    real = Store.query_lines

    def spy(self, **kw):
        threads.append(threading.get_ident())
        return real(self, **kw)

    monkeypatch.setattr(Store, "query_lines", spy)
    r = client.get("/lines", params={"until_ts": T0 + 1})
    assert [x["raw"] for x in r.json()["lines"]] == ["line0"]
    assert threads and loop_thread not in threads, "the until_ts read ran on the loop"
    threads.clear()
    client.get("/lines")
    assert threads == [loop_thread], "a plain poll stays inline"
