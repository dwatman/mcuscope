"""Review round 2, batch A2: the server.py fixes and the tests they were owed.

Everything here drives the real HTTP surface of the shared sim+daemon stack, except the
two cases a live client cannot produce (a mid-download disconnect, an unwritable config
path), which drive the object or the app state directly.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import httpx
import pytest

from mcuscope import server
from mcuscope.config import resolve_db_path
from tests.support import Stack

BIG = str(10**400)   # arbitrary precision: what an unbounded int param used to swallow


def client(stack: Stack) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=30.0)


def db_dir(stack: Stack) -> Path:
    return Path(resolve_db_path(stack.app.state.config)).parent


def temp_exports(stack: Stack) -> list[Path]:
    return sorted(db_dir(stack).glob("mcuscope-session-*"))


def wait_no_temp_exports(stack: Stack, timeout: float = 5.0) -> list[Path]:
    # The unlink runs server-side after the last body byte, so the client can observe the
    # end of the download first. Poll rather than sleep, and return what is left.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        left = temp_exports(stack)
        if not left:
            return left
        time.sleep(0.02)
    return temp_exports(stack)


# -- CD1: an out-of-range integer parameter is a refusal, never a 500 ------------------


def test_out_of_range_integer_params_are_refused_not_a_500(stack: Stack) -> None:
    # Each of these reached either a float conversion or a SQLite bind and raised
    # OverflowError there: a 500 plus a full traceback in the daemon log, for input the
    # daemon should refuse in one line. 422 is FastAPI's validation answer.
    with client(stack) as c:
        probes = [
            c.get("/lines", params={"last_ms": BIG}),
            c.get("/lines", params={"since_id": BIG}),
            c.get("/can/frames", params={"since_id": BIG}),
            c.get("/plot/series", params={"name": "x", "decimate": BIG}),
            c.get("/plot/series", params={"name": "x", "last_ms": BIG}),
            c.get("/plot/export", params={"names": "x", "last_ms": BIG}),
            c.post("/purge", json={"id_from": 1, "id_to": int(BIG)}),
        ]
        # Every remaining int parameter, enumerated from the handler signatures and the
        # body models rather than from the reported seven.
        probes += [
            c.get("/lines", params={"id_to": BIG}),
            c.get("/can/frames", params={"last_ms": BIG, "id_to": BIG}),
            c.get("/plot/series", params={"name": "x", "since_id": BIG, "id_to": BIG}),
            c.get("/plot/export", params={"names": "x", "id_to": BIG}),
            c.post("/purge", json={"id_from": int(BIG)}),
            c.post("/assert", json={"expect": ["x"], "last_ms": int(BIG)}),
            c.request("DELETE", f"/sessions/{BIG}"),
        ]
    for r in probes:
        assert r.status_code == 422, (str(r.request.url), r.text)


def test_a_limit_past_the_ceiling_is_still_clamped_not_refused(stack: Stack) -> None:
    # SPEC 3.3.1 clamps `limit` rather than refusing it, so the bounds added for CD1 must
    # not have turned the clamped parameters into refusals.
    with client(stack) as c:
        assert c.get("/lines", params={"limit": 999999}).status_code == 200
        assert c.get("/lines", params={"limit": 0}).json()["lines"] == []


# -- measurement F1 (REST half): a purge cutoff in the future ---------------------------


def test_purge_before_ts_in_the_future_is_refused(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/purge", json={"before_ts": time.time() + 3600, "dry_run": True})
    assert r.status_code == 400
    body = r.json()["error"]
    assert "future" in body
    assert "all" in body   # the message must point at the deliberate full wipe


def test_purge_before_ts_inside_the_skew_slack_is_accepted(stack: Stack) -> None:
    # The other side of the same boundary: a client clock a few seconds ahead of the
    # daemon must not have its retention purge refused.
    with client(stack) as c:
        r = c.post("/purge", json={"before_ts": time.time() + 5, "dry_run": True})
    assert r.status_code == 200
    assert r.json()["dry_run"] is True


# -- CD3: the session export's temp copy ------------------------------------------------


def test_session_export_builds_its_temp_copy_beside_the_capture(stack: Stack) -> None:
    with client(stack) as c:
        sid = c.post("/sessions", json={"name": "tmp-loc"}).json()["session"]["id"]
        with c.stream("GET", f"/sessions/{sid}/export") as r:
            assert r.status_code == 200
            # Mid-stream: the copy exists, and it is on the capture's own filesystem.
            during = temp_exports(stack)
            r.read()
    assert len(during) == 1
    assert wait_no_temp_exports(stack) == []


def test_session_export_leaves_no_temp_file_behind(stack: Stack) -> None:
    with client(stack) as c:
        sid = c.post("/sessions", json={"name": "tmp-clean"}).json()["session"]["id"]
        r = c.get(f"/sessions/{sid}/export")
    assert r.status_code == 200 and r.content[:6] == b"SQLite"
    assert wait_no_temp_exports(stack) == []


def test_a_disconnected_download_still_removes_the_temp_copy(tmp_path) -> None:
    # A BackgroundTask runs only after the body is sent, so this was the leak: a send that
    # raises stands in for the client that closed the connection mid-download.
    tmp = tmp_path / "mcuscope-session-x.db"
    tmp.write_bytes(b"SQLite format 3\x00")
    resp = server._TempFileResponse(str(tmp), media_type="application/vnd.sqlite3")
    scope = {"type": "http", "method": "GET", "headers": []}

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        raise ConnectionResetError("client went away")

    async def go():
        await resp(scope, receive, send)

    with pytest.raises(ConnectionResetError):
        asyncio.run(go())
    assert not tmp.exists()


# -- CD4: the same-origin guard says what it does not cover -----------------------------


def test_the_same_origin_guard_documents_the_no_cors_gap() -> None:
    # Wording, not behaviour: a reader must not conclude a GET endpoint is unreachable
    # cross-site. If the caveat goes, the claim overreaches again.
    doc = server._SameOriginGuard.__doc__ or ""
    assert "no-cors" in doc


# -- measurement F4: /plot/export and an unknown channel name ---------------------------


def _a_plot_channel(stack: Stack, tries: int = 200) -> str:
    with client(stack) as c:
        for _ in range(tries):
            chans = c.get("/plot/channels").json()["channels"]
            if chans:
                return chans[0]["name"]
            time.sleep(0.05)
    raise AssertionError("the simulator produced no plot channels")


def test_plot_export_refuses_when_no_requested_name_exists(make_stack) -> None:
    stack = make_stack(["--plot"])
    _a_plot_channel(stack)   # the capture has channels; these two are simply not among them
    with client(stack) as c:
        r = c.get("/plot/export", params={"names": "nosuchchan,alsomissing"})
    assert r.status_code == 400
    assert "nosuchchan" in r.json()["error"]
    assert "alsomissing" in r.json()["error"]


def test_plot_export_still_exports_when_one_name_of_several_is_unknown(make_stack) -> None:
    stack = make_stack(["--plot"])
    name = _a_plot_channel(stack)
    with client(stack) as c:
        r = c.get("/plot/export", params={"names": f"{name},nosuchchan"})
    assert r.status_code == 200
    assert r.text.splitlines()[0].startswith("ts,")


# -- F12 (routed from batch C1): resolving a session name server-side -------------------


def _make_sessions(stack: Stack, n: int) -> None:
    with client(stack) as c:
        for i in range(n):
            assert c.post("/sessions", json={"name": f"s{i}"}).status_code == 200


def test_sessions_name_filter_finds_a_session_past_the_default_page(stack: Stack) -> None:
    _make_sessions(stack, 55)
    with client(stack) as c:
        page = c.get("/sessions").json()["sessions"]
        assert "s0" not in {s["name"] for s in page}, "s0 must be off the default page"
        found = c.get("/sessions", params={"name": "s0"}).json()["sessions"]
    assert [s["name"] for s in found] == ["s0"]
    assert isinstance(found[0]["lines"], int)   # the field `session delete` prints


def test_sessions_name_filter_answers_empty_for_an_unknown_name(stack: Stack) -> None:
    with client(stack) as c:
        body = c.get("/sessions", params={"name": "no-such-session"}).json()
    assert body["sessions"] == []
    assert body["active"] is not None


def test_the_session_name_lookup_uses_the_name_index(stack: Stack) -> None:
    # The filter is only worth having if it is one seek: assert the plan, not the latency.
    conn = sqlite3.connect(resolve_db_path(stack.app.state.config))
    try:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT id FROM sessions WHERE name = ? ORDER BY id DESC LIMIT 1", ("s0",)
        ).fetchall()
    finally:
        conn.close()
    assert any("idx_sessions_name" in str(row) for row in plan), plan


def test_cli_session_delete_resolves_a_name_past_the_default_page(stack: Stack) -> None:
    from tests.test_cli import run_mcu

    _make_sessions(stack, 55)
    r = run_mcu(stack, "session", "delete", "s0")
    assert r.returncode == 0, r.stderr
    assert "s0" in r.stdout
    with client(stack) as c:
        assert c.get("/sessions", params={"name": "s0"}).json()["sessions"] == []


def test_cli_session_delete_keeps_its_error_text_for_a_missing_name(stack: Stack) -> None:
    from tests.test_cli import run_mcu

    r = run_mcu(stack, "session", "delete", "no-such-session")
    assert r.returncode == 1
    assert "no such session: no-such-session" in r.stderr


# -- owed by the coverage disposition ---------------------------------------------------


def test_wait_and_assert_accept_send_mode_raw(stack: Stack) -> None:
    # `raw` writes the line verbatim, so it needs the `>SEQ CMD` wire form. Zero coverage
    # on both routes until now, on the one field whose other value silently sends a
    # different thing.
    with client(stack) as c:
        r = c.post("/wait", json={
            "send": ">7 i2c scan", "send_mode": "raw", "match": "^<7 OK", "timeout_ms": 3000,
        })
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "match"
        r = c.post("/assert", json={
            "send": ">8 i2c scan", "send_mode": "raw", "expect": ["^<8 OK"],
            "timeout_ms": 3000,
        })
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "pass"


def test_a_catastrophic_match_pattern_is_stopped_by_the_budget(stack: Stack) -> None:
    # The whole job of the budget is to stop a stall, and nothing in the suite tripped it.
    with client(stack) as c:
        assert c.post("/marker", json={"text": "a" * 400}).status_code == 200
        r = c.get("/lines", params={"match": "(a|a)+b", "limit": 100})
    assert r.status_code == 400
    assert "budget" in r.json()["error"]


# The /shutdown 403 for a non-loopback client is already driven by
# test_e2e.test_shutdown_refused_from_non_loopback (the ASGITransport fake-client pattern
# the disposition asked for); nothing is owed here.


@pytest.mark.parametrize(
    "route,body",
    [
        ("/config/server", {"host": "127.0.0.1", "port": 8558}),
        ("/config/storage", {"db_path": "", "retention_days": 7}),
        ("/config/update", {"check": False}),
        ("/config/plotjuggler", {"enabled": False, "dest": "127.0.0.1:9870"}),
    ],
)
def test_a_config_write_failure_is_a_500_naming_the_failure(
    stack: Stack, route: str, body: dict
) -> None:
    # Four identical untested `except (ConfigError, OSError)` arms. A config path *under a
    # file* cannot be written on any platform, so the save raises OSError inside the
    # worker thread and must come back as the envelope, not a traceback.
    saved = stack.app.state.config_path
    stack.app.state.config_path = Path(resolve_db_path(stack.app.state.config)) / "config.toml"
    try:
        with client(stack) as c:
            r = c.put(route, json=body)
    finally:
        stack.app.state.config_path = saved
    assert r.status_code == 500
    assert "config save failed" in r.json()["error"]


def test_export_tmp_dir_for_a_memory_capture_is_the_system_temp() -> None:
    # Path(":memory:").parent is ".", the daemon's CWD, which for a detached daemon is
    # wherever the launcher was; an in-memory capture must fall back to the system temp.
    from types import SimpleNamespace

    from mcuscope.config import Config, StorageConfig

    def req(db_path: str):
        cfg = Config(storage=StorageConfig(db_path=db_path))
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))

    assert server._export_tmp_dir(req(":memory:")) is None
    got = server._export_tmp_dir(req("relative.db"))
    assert got == "."   # a relative capture really lives in the CWD, so that is correct
