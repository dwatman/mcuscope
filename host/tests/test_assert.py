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


def test_export_by_name_and_unknown_ref(tmp_path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.post("/sessions", json={"name": "by-name"})
        _lines(c, "payload")
        c.post("/sessions/stop")
        assert c.get("/sessions/by-name/export").status_code == 200
        assert c.get("/sessions/nope/export").status_code == 400
