"""`/assert` and `/purge`: one verdict over a capture window, and deliberate deletion.

`wait` answers "did this line appear?"; `assert` answers "did this run pass?". The tests
here pin the parts that make that a usable verdict: negative conditions, the scope the
verdict covers, and that a failure names the line that caused it.
"""

from __future__ import annotations

import sqlite3
import threading
import time

from fastapi.testclient import TestClient

from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import create_app


def _mk_app(tmp_path):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "assert.db")),
    )
    return create_app(config, config_path=tmp_path / "config.toml")


def _lines(c: TestClient, *texts: str) -> None:
    for text in texts:
        c.post("/marker", json={"text": text})


def _after(ts: float) -> float:
    """Block until the wall clock reads strictly later than `ts`, and return that reading.

    time.time() has a 15.625 ms granularity on Windows, so consecutive calls routinely
    return the identical float and a `time.sleep(0.01)` can fail to advance it at all.
    Every ts boundary in the store is exclusive (`WHERE ts < ?`), so a test that wants a
    line on a given side of a cut has to wait for a strictly greater reading rather than
    assume one happened.
    """
    while True:
        now = time.time()
        if now > ts:
            return now
        time.sleep(0.002)


def _named(c: TestClient) -> list[dict]:
    """Sessions someone actually named, ignoring the daemon's automatic one."""
    return [s for s in c.get("/sessions").json()["sessions"] if not s["auto"]]


# -- retrospective ---------------------------------------------------------------------


def test_pass_when_every_expect_matched_and_no_forbid(tmp_path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        _lines(c, "BOOT OK", "CALIB DONE", "idle")
        body = c.post("/assert", json={
            "expect": ["BOOT OK", "CALIB DONE"], "forbid": ["ERR|retry"],
        }).json()
        assert body["status"] == "pass"
        assert [e["matched"] for e in body["expect"]] == [True, True]
        assert body["expect"][0]["line"]["raw"] == "BOOT OK"
        assert body["forbid"][0]["matched"] is False
        assert body["checked_lines"] >= 3


def test_missing_expect_fails_and_says_which(tmp_path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        _lines(c, "BOOT OK")
        body = c.post("/assert", json={"expect": ["BOOT OK", "CALIB DONE"]}).json()
        assert body["status"] == "fail"
        assert body["expect"][0]["matched"] is True
        assert body["expect"][1]["matched"] is False
        assert body["expect"][1]["line"] is None


def test_forbidden_line_fails_and_is_named(tmp_path) -> None:
    # A failure that does not point at the offending line is not much use to an agent.
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        _lines(c, "BOOT OK", "ERR i2c nak", "done")
        body = c.post("/assert", json={"expect": ["BOOT OK"], "forbid": ["ERR"]}).json()
        assert body["status"] == "fail"
        assert body["expect"][0]["matched"] is True
        assert body["forbid"][0]["line"]["raw"] == "ERR i2c nak"


def test_verdict_is_scoped_to_the_session(tmp_path) -> None:
    # The whole point of a retrospective assert: judge one run, not the whole capture.
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        _lines(c, "ERR from an earlier run")
        c.post("/sessions", json={"name": "run-1"})
        _lines(c, "BOOT OK")
        c.post("/sessions/stop")
        _lines(c, "ERR from a later run")

        scoped = c.post("/assert", json={
            "expect": ["BOOT OK"], "forbid": ["ERR"], "session": "run-1",
        }).json()
        assert scoped["status"] == "pass", "errors outside the run must not fail it"

        unscoped = c.post("/assert", json={"expect": ["BOOT OK"], "forbid": ["ERR"]}).json()
        assert unscoped["status"] == "fail"


def test_running_session_is_open_ended(tmp_path) -> None:
    # Checking a run mid-flight is the natural agent move; an unfinished session has no
    # end_id, so its scope has to stay open at the top rather than matching nothing.
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.post("/sessions", json={"name": "in-progress"})
        _lines(c, "STEP 1 OK")
        body = c.post("/assert", json={
            "expect": ["STEP 1 OK"], "forbid": ["ERR"], "session": "in-progress",
        }).json()
        assert body["status"] == "pass"
        assert body["checked_lines"] > 0


def test_last_ms_window(tmp_path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        _lines(c, "recent")
        body = c.post("/assert", json={"expect": ["recent"], "last_ms": 60_000}).json()
        assert body["status"] == "pass"


def test_unknown_session_is_an_error_not_a_pass(tmp_path) -> None:
    # An empty scope would vacuously satisfy every forbid; a typo must not read as a pass.
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        r = c.post("/assert", json={"forbid": ["ERR"], "session": "typo"})
        assert r.status_code == 400
        assert "no such session" in r.json()["error"]


def test_bad_input_rejected(tmp_path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert c.post("/assert", json={}).status_code == 400          # nothing to judge
        assert c.post("/assert", json={"expect": ["("]}).status_code == 400   # bad regex
        assert c.post("/assert", json={"expect": ["x" * 500]}).status_code == 400
        many = c.post("/assert", json={"expect": [f"p{i}" for i in range(20)]})
        assert many.status_code == 422


def test_min_window_needs_a_live_window_it_fits_in(tmp_path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        retro = c.post("/assert", json={"forbid": ["ERR"], "min_window_ms": 500})
        assert retro.status_code == 400 and "live window" in retro.json()["error"]
        toobig = c.post("/assert", json={
            "forbid": ["ERR"], "timeout_ms": 200, "min_window_ms": 500,
        })
        assert toobig.status_code == 400 and "exceed" in toobig.json()["error"]


# -- live windows ----------------------------------------------------------------------


def test_live_window_closes_early_without_a_minimum(tmp_path) -> None:
    # The default: an assertion whose expectation is already satisfiable returns at once
    # rather than sitting out its timeout.
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.post("/marker", json={"text": "seed"})   # ensure the store is live
        started = time.monotonic()
        body = c.post("/assert", json={
            "expect": ["never appears"], "timeout_ms": 400,
        }).json()
        assert body["status"] == "fail"
        assert 0.3 < time.monotonic() - started < 3.0, "should use its window, then stop"


def test_min_window_holds_the_window_open(tmp_path) -> None:
    # The reason the option exists: without it, "boot then stay clean" would judge the
    # forbid over only the milliseconds the boot took.
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        def emit_later() -> None:
            time.sleep(0.15)
            c.post("/marker", json={"text": "BOOT OK"})

        t = threading.Thread(target=emit_later)
        started = time.monotonic()
        t.start()
        body = c.post("/assert", json={
            "expect": ["BOOT OK"], "forbid": ["PANIC"],
            "min_window_ms": 900, "timeout_ms": 5000,
        }).json()
        elapsed = time.monotonic() - started
        t.join()
        assert body["status"] == "pass"
        assert elapsed >= 0.85, f"the minimum window was cut short ({elapsed:.2f}s)"
        assert elapsed < 4.0, "it should end at the minimum, not run to the timeout"


def test_forbidden_line_ends_a_minimum_window_immediately(tmp_path) -> None:
    # A minimum window is about proving absence, not about delaying a decided failure.
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        def emit_later() -> None:
            time.sleep(0.15)
            c.post("/marker", json={"text": "PANIC now"})

        t = threading.Thread(target=emit_later)
        started = time.monotonic()
        t.start()
        body = c.post("/assert", json={
            "forbid": ["PANIC"], "min_window_ms": 3000, "timeout_ms": 3000,
        }).json()
        elapsed = time.monotonic() - started
        t.join()
        assert body["status"] == "fail"
        assert elapsed < 2.0, f"a decided failure should not wait out the window ({elapsed:.2f}s)"


# -- purge -----------------------------------------------------------------------------


def test_dry_run_reports_without_deleting(tmp_path) -> None:
    # A purge is not recoverable, so the count has to be available before the delete.
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        _lines(c, "a", "b", "c")
        before = len(c.get("/lines", params={"limit": 200}).json()["lines"])
        body = c.post("/purge", json={"all": True, "dry_run": True}).json()
        assert body["deleted"] == before and body["dry_run"] is True
        assert len(c.get("/lines", params={"limit": 200}).json()["lines"]) == before

        done = c.post("/purge", json={"all": True}).json()
        assert done["deleted"] == before
        assert c.get("/lines", params={"limit": 200}).json()["lines"] == []


def test_purge_by_session_leaves_the_rest(tmp_path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        _lines(c, "keep me before")
        c.post("/sessions", json={"name": "junk"})
        _lines(c, "big useless capture")
        c.post("/sessions/stop")
        _lines(c, "keep me after")

        body = c.post("/purge", json={"session": "junk"}).json()
        assert body["deleted"] > 0
        raws = [r["raw"] for r in c.get("/lines", params={"limit": 200}).json()["lines"]]
        assert "big useless capture" not in raws
        assert "keep me before" in raws and "keep me after" in raws
        # The label survives its data and honestly reads as empty.
        assert _named(c)[0]["lines"] == 0


def test_purge_by_id_range(tmp_path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        ids = [c.post("/marker", json={"text": f"line {i}"}).json()["line_id"] for i in range(5)]
        body = c.post("/purge", json={"id_from": ids[1], "id_to": ids[3]}).json()
        assert body["deleted"] == 3
        left = [r["raw"] for r in c.get("/lines", params={"limit": 200}).json()["lines"]]
        assert "line 0" in left and "line 4" in left
        assert "line 2" not in left


def test_purge_before_ts_deletes_only_what_predates_it(tmp_path) -> None:
    """`mcu purge --before N` is the one destructive selector the suite never drove, so
    both of its own branches - a cut-off with nothing behind it, and a real cut - were
    shipped unexercised."""
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        _lines(c, "old one", "old two")
        # Pin the cut to the stored data, not to a bare time.time(): on a 15.625 ms clock
        # `cut` landed in the same tick as "old two", which `ts < cut` then spared, and the
        # test failed 4 runs in 8 on Windows.
        old_ts = max(r["ts"] for r in c.get("/lines", params={"limit": 200}).json()["lines"])
        cut = _after(old_ts)               # strictly newer than every line so far
        _after(cut)                        # and "new one" strictly newer than the cut
        _lines(c, "new one")

        # Nothing predates the epoch: the early return, not a purge of everything.
        empty = c.post("/purge", json={"before_ts": 0.0}).json()
        assert empty == {"deleted": 0, "id_from": None, "id_to": None, "dry_run": False}

        # The range starts at 1, so it takes the daemon's own start rows with it; what
        # matters is that the dry run predicts the delete exactly and the cut lands right.
        dry = c.post("/purge", json={"before_ts": cut, "dry_run": True}).json()
        assert dry["id_from"] == 1 and dry["deleted"] >= 2 and dry["dry_run"] is True

        done = c.post("/purge", json={"before_ts": cut}).json()
        assert done["deleted"] == dry["deleted"]
        raws = [r["raw"] for r in c.get("/lines", params={"limit": 200}).json()["lines"]]
        assert "new one" in raws and "old one" not in raws and "old two" not in raws


def test_purge_needs_exactly_one_selector(tmp_path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert c.post("/purge", json={}).status_code == 400
        assert c.post("/purge", json={"all": True, "id_from": 1}).status_code == 400
        assert c.post("/purge", json={"session": "nope"}).status_code == 400


def test_delete_session_with_data(tmp_path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        started = c.post("/sessions", json={"name": "throwaway"}).json()["session"]
        _lines(c, "inside")
        c.post("/sessions/stop")
        _lines(c, "outside")

        body = c.delete(f"/sessions/{started['id']}", params={"data": "true"}).json()
        assert body["lines_deleted"] > 0
        raws = [r["raw"] for r in c.get("/lines", params={"limit": 200}).json()["lines"]]
        assert "inside" not in raws and "outside" in raws
        assert _named(c) == []


# -- session export --------------------------------------------------------------------


def test_session_export_is_a_normal_capture_file(tmp_path) -> None:
    # The export's value is that it is not a bespoke archive format: the same queries work.
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        _lines(c, "before the run")
        started = c.post("/sessions", json={"name": "run/1", "note": "archive me"}).json()
        _lines(c, "inside the run")
        c.post("/sessions/stop")
        _lines(c, "after the run")

        resp = c.get(f"/sessions/{started['session']['id']}/export")
        assert resp.status_code == 200
        assert "run_1.db" in resp.headers["content-disposition"], "name sanitized for a filename"
        out = tmp_path / "run.db"
        out.write_bytes(resp.content)

        conn = sqlite3.connect(str(out))
        conn.row_factory = sqlite3.Row
        raws = [r["raw"] for r in conn.execute("SELECT raw FROM lines ORDER BY id")]
        assert "inside the run" in raws
        assert "before the run" not in raws and "after the run" not in raws
        sess = conn.execute("SELECT * FROM sessions").fetchall()
        assert len(sess) == 1 and sess[0]["name"] == "run/1"
        # Ids are preserved, so the copied session row still scopes its own lines.
        lo, hi = sess[0]["start_id"], sess[0]["end_id"]
        n = conn.execute(
            "SELECT COUNT(*) FROM lines WHERE id >= ? AND id <= ?", (lo, hi)
        ).fetchone()[0]
        assert n == len(raws)
        conn.close()


def test_export_of_a_running_session(tmp_path) -> None:
    """A session with no end_id yet exports everything captured so far.

    Every other export test stopped the session first, so the `id_to is None` branch was
    never exercised: it resolved the upper bound through the loop-thread connection from
    the worker thread the copy runs on, and sqlite3 refused it. Exporting the session in
    progress - including the automatic one the daemon always has open - answered 400.
    """
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        _lines(c, "before the run")
        started = c.post("/sessions", json={"name": "still-going"}).json()
        _lines(c, "during the run")

        resp = c.get(f"/sessions/{started['session']['id']}/export")
        assert resp.status_code == 200, resp.text
        out = tmp_path / "live.db"
        out.write_bytes(resp.content)

        conn = sqlite3.connect(str(out))
        conn.row_factory = sqlite3.Row
        raws = [r["raw"] for r in conn.execute("SELECT raw FROM lines ORDER BY id")]
        assert "during the run" in raws and "before the run" not in raws
        sess = conn.execute("SELECT * FROM sessions").fetchall()
        # The copy records an unfinished run as unfinished rather than inventing an end.
        assert len(sess) == 1 and sess[0]["end_id"] is None
        conn.close()

        # And the run is still open afterwards: the export only ever reads.
        (live,) = _named(c)
        assert live["id"] == started["session"]["id"] and live["ended_ts"] is None


def test_export_by_name_and_unknown_ref(tmp_path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.post("/sessions", json={"name": "by-name"})
        _lines(c, "payload")
        c.post("/sessions/stop")
        assert c.get("/sessions/by-name/export").status_code == 200
        assert c.get("/sessions/nope/export").status_code == 400


# -- CaptureWatch ----------------------------------------------------------------------
#
# /wait and /assert share one watch, so its ordering rules are pinned here once against a
# bare Store rather than twice through a daemon. Each of these was previously reachable
# only by driving HTTP against a sim, which is why the drain rule held for one endpoint
# and not the other.


def _watch_store(tmp_path, name):
    from mcuscope.store import Store

    return Store(str(tmp_path / name))


async def _sys(store, raw, chan="sys"):
    return await store.add_line(
        ts=time.time(), port="t", dir="-", chan=chan, seq=None, raw=raw
    )


def test_watch_ignores_rows_committed_before_it_opened(tmp_path) -> None:
    """The watermark is read before subscribing, so only newer ids are candidates."""
    import asyncio

    from mcuscope.server import CaptureWatch

    async def run() -> None:
        store = _watch_store(tmp_path, "wm.db")
        await store.start()
        try:
            await _sys(store, "before")
            watch = CaptureWatch(store)
            watch.open()
            try:
                await _sys(store, "after")
                batch = await watch.next_batch(0.5)
                assert [r["raw"] for r in batch] == ["after"]
            finally:
                watch.close()
        finally:
            await store.stop()

    asyncio.run(run())


def test_watch_drains_a_queued_burst_after_the_deadline_has_passed(tmp_path) -> None:
    """The rule /assert was missing: a spent window still evaluates what is already queued.

    `send` is given the same timeout as the whole window, so a command that consumes it
    leaves the deadline expired with the match sitting in the queue. next_batch is called
    with a non-positive remaining and must still return the rows.
    """
    import asyncio

    from mcuscope.server import CaptureWatch

    async def run() -> None:
        store = _watch_store(tmp_path, "drain.db")
        await store.start()
        try:
            watch = CaptureWatch(store)
            watch.open()
            try:
                await _sys(store, "queued while the send ran")
                batch = await watch.next_batch(-1.0)   # window already spent
                assert [r["raw"] for r in batch] == ["queued while the send ran"]
            finally:
                watch.close()
        finally:
            await store.stop()

    asyncio.run(run())


def test_watch_separates_an_empty_window_from_a_filtered_one(tmp_path) -> None:
    """None means nothing arrived; [] means rows arrived and none matched the filter.

    Collapsing the two would end the window early on the first row of another channel.
    """
    import asyncio

    from mcuscope.server import CaptureWatch

    async def run() -> None:
        store = _watch_store(tmp_path, "filt.db")
        await store.start()
        try:
            watch = CaptureWatch(store, chan="rx")
            watch.open()
            try:
                assert await watch.next_batch(0.05) is None      # quiet window
                await _sys(store, "noise", chan="sys")
                assert await watch.next_batch(0.5) == []         # arrived, filtered out
            finally:
                watch.close()
        finally:
            await store.stop()

    asyncio.run(run())


def test_watch_counts_the_rows_the_feed_shed(tmp_path) -> None:
    """A hole in the window must be reported, or a forbid reads as judged when it is not."""
    import asyncio

    from mcuscope.server import CaptureWatch

    async def run() -> None:
        store = _watch_store(tmp_path, "drop.db")
        await store.start()
        try:
            watch = CaptureWatch(store, maxsize=2)
            watch.open()
            try:
                for i in range(5):
                    await _sys(store, f"line {i}")
                batch = await watch.next_batch(0.5)
                assert len(batch) == 2          # the queue only ever held two
                assert watch.sync_dropped() == 3
            finally:
                watch.close()
        finally:
            await store.stop()

    asyncio.run(run())


def test_watch_close_releases_the_subscription(tmp_path) -> None:
    """The subscriber list must not grow by one per /wait."""
    import asyncio

    from mcuscope.server import CaptureWatch

    async def run() -> None:
        store = _watch_store(tmp_path, "unsub.db")
        await store.start()
        try:
            before = len(store._subscribers)
            watch = CaptureWatch(store)
            watch.open()
            assert len(store._subscribers) == before + 1
            watch.close()
            assert len(store._subscribers) == before
            watch.close()   # idempotent: the finally in each handler may double up
            assert len(store._subscribers) == before
        finally:
            await store.stop()

    asyncio.run(run())
