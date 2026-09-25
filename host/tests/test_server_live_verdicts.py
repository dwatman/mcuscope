"""/wait and /assert verdicts (SPEC 3.4) and the port a request names or defaults to: a call's
own send is not its match, a failed send fails the assert, a window of no lines is `empty`,
an unknown port is refused, and live matching has a pool and a deadline of its own."""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from mcuscope import server as server_mod
from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import create_app
from mcuscope.store import MATCH_WORKERS, Store, match_executor
from tests.support import UNOPENABLE, Stack, on_loop, stack_client

OWN_SEND = r"^>\d+ ping$"   # the stored tx row of `ping`, and nothing the sim answers


def _http(stack: Stack) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=20.0)


@pytest.fixture
def c(tmp_path):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db")),
    )
    app = create_app(config, config_path=tmp_path / "config.toml")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        yield client


def _add(c: TestClient, port: str, chan: str = "debug", raw: str = "row") -> None:
    store = c.app.state.store
    asyncio.run_coroutine_threadsafe(
        store.add_line(ts=time.time(), port=port, dir="rx", chan=chan, seq=None, raw=raw),
        c.app.state.ports._loop,
    ).result(5)


# -- CLI-1: a call's own send is not its match ----------------------------------------------


def test_a_wait_does_not_match_its_own_command(stack: Stack) -> None:
    with _http(stack) as h:
        mine = h.post("/wait", json={"send": "ping", "match": OWN_SEND, "timeout_ms": 400}).json()
        assert mine["status"] == "timeout", mine
        assert mine["cmd_result"]["status"] == "ok", "positive control: the send happened"
        named = h.post("/wait", json={
            "send": "ping", "match": OWN_SEND, "timeout_ms": 1500, "chan": "cmd",
        }).json()
        assert named["status"] == "match" and named["line"]["dir"] == "tx", named


def test_a_live_assert_does_not_expect_its_own_command(stack: Stack) -> None:
    with _http(stack) as h:
        r = h.post("/assert", json={"send": "ping", "expect": [OWN_SEND], "timeout_ms": 400})
        body = r.json()
        assert body["status"] == "fail" and body["checked_lines"] >= 1, body


# -- CLI-2: an assert reports its send, and a refused send fails it --------------------------


def test_an_assert_whose_send_is_refused_fails_and_says_why(stack: Stack) -> None:
    with _http(stack) as h:
        ok = h.post("/assert", json={"send": "ping", "forbid": ["PANIC"], "timeout_ms": 300}).json()
        assert ok["status"] == "pass" and ok["reason"] is None, ok        # positive control
        assert ok["cmd_result"]["status"] == "ok"
        bad = h.post("/assert", json={
            "send": "reset", "forbid": ["PANIC"], "timeout_ms": 1500,
        }).json()
        assert bad["status"] == "fail", bad
        assert bad["cmd_result"]["status"] == "err"
        assert bad["reason"].startswith("send answered ERR 1 badcmd"), bad["reason"]
        assert bad["checked_lines"] == 0, "the verdict was decided; the window must not run"


def test_an_assert_whose_send_timed_out_fails_and_says_why(make_stack) -> None:
    stack = make_stack(["--drop-response", "2"])   # 1 is the connect-time ping
    with _http(stack) as h:
        body = h.post("/assert", json={
            "send": "ping", "forbid": ["PANIC"], "timeout_ms": 500,
        }).json()
    assert body["status"] == "fail" and body["cmd_result"]["status"] == "timeout", body
    assert body["reason"] == "send got no response in 500 ms"


def test_a_retrospective_or_raw_assert_carries_no_command_result(c) -> None:
    _add(c, "board")
    body = c.post("/assert", json={"forbid": ["PANIC"]}).json()
    assert body["cmd_result"] is None and body["reason"] is None and body["status"] == "pass"


# -- CLI-3: a verdict over no lines, and an unknown port -------------------------------------


def test_a_retrospective_verdict_over_no_lines_is_empty(c) -> None:
    _add(c, "board", chan="debug")
    scope = {"forbid": ["PANIC"], "port": "board", "chan": "resp"}
    empty = c.post("/assert", json=scope).json()
    assert empty["status"] == "empty" and empty["checked_lines"] == 0, empty
    assert empty["reason"] == "no lines were checked in the window"
    allowed = c.post("/assert", json={**scope, "allow_empty": True}).json()
    assert allowed["status"] == "pass" and allowed["reason"] is None
    judged = c.post("/assert", json={**scope, "chan": "debug"}).json()
    assert judged["status"] == "pass" and judged["checked_lines"] == 1   # positive control


def test_a_live_verdict_over_no_lines_is_empty(c) -> None:
    body = c.post("/assert", json={"forbid": ["PANIC"], "timeout_ms": 200}).json()
    assert body["status"] == "empty" and body["checked_lines"] == 0, body
    fine = c.post("/assert", json={"forbid": ["PANIC"], "timeout_ms": 200, "allow_empty": True})
    assert fine.json()["status"] == "pass"


READS = [
    ("GET", "/lines", {}), ("GET", "/lines/export", {}), ("GET", "/can/frames", {}),
    ("GET", "/plot/channels", {}), ("GET", "/plot/series", {"name": "v"}),
    ("GET", "/plot/export", {"names": "v"}),
]


def test_reads_lists_every_route_with_a_port_query(c) -> None:
    spec = c.app.openapi()
    derived = {
        (method.upper(), path)
        for path, ops in spec["paths"].items() for method, op in ops.items()
        if any(q["in"] == "query" and q["name"] == "port" for q in op.get("parameters", []))
    }
    assert {(method, path) for method, path, _ in READS} == derived


@pytest.mark.parametrize("method, path, extra", READS)
def test_a_read_scoped_to_an_unknown_port_is_refused(c, method, path, extra) -> None:
    _add(c, "gone")   # a detached board's history
    known = c.request(method, path, params={**extra, "port": "gone"})
    assert known.status_code != 400 or "no such port" not in known.text, known.text
    r = c.request(method, path, params={**extra, "port": "nosuch"})
    assert r.status_code == 400 and r.json() == {"error": "no such port: nosuch"}


def test_a_verdict_or_marker_for_an_unknown_port_is_refused(c) -> None:
    _add(c, "gone")
    assert c.post("/assert", json={"forbid": ["x"], "port": "gone"}).status_code == 200
    r = c.post("/assert", json={"forbid": ["x"], "port": "nosuch"})
    assert r.status_code == 400 and r.json() == {"error": "no such port: nosuch"}
    assert c.post("/marker", json={"text": "m", "port": "gone"}).status_code == 200
    r = c.post("/marker", json={"text": "m", "port": "nosuch"})
    assert r.status_code == 400 and r.json() == {"error": "no such port: nosuch"}


# -- CLI-18: a write defaults only to the sole attached port ---------------------------------


WRITES = [
    ("/send", {"line": "x"}), ("/cmd", {"cmd": "ping"}), ("/break", {"ms": 5}),
    ("/wait", {"match": "x", "send": "ping", "timeout_ms": 100}),
    ("/assert", {"forbid": ["x"], "send": "ping", "timeout_ms": 100}),
]


def test_writes_lists_every_post_with_a_port_field(c) -> None:
    spec = c.app.openapi()
    schemas = spec["components"]["schemas"]
    derived = set()
    for path, ops in spec["paths"].items():
        body = ops.get("post", {}).get("requestBody", {})
        ref = body.get("content", {}).get("application/json", {}).get("schema", {})
        if "port" in schemas.get(ref.get("$ref", "").rsplit("/", 1)[-1], {}).get("properties", {}):
            derived.add(path)
    # A marker need not name a port (SPEC 3.5); its unknown-port refusal is tested above.
    assert {path for path, _ in WRITES} == derived - {"/marker"}


@pytest.mark.parametrize("path, body", WRITES)
def test_an_unnamed_write_with_two_ports_attached_is_refused(stack: Stack, path, body) -> None:
    with _http(stack) as h:
        assert h.post("/ports", json={"alias": "spare", "device": UNOPENABLE}).status_code == 200
        try:
            r = h.post(path, json=body)   # "spare" never connects, "board" is up
            assert r.status_code == 400
            assert r.json() == {"error": "port is ambiguous; specify one of: board, spare"}
            named = h.post(path, json={**body, "port": stack.alias})
            assert named.status_code == 200 or "ambiguous" not in named.text, named.text
        finally:
            h.delete("/ports/spare")


def test_the_sole_attached_port_is_the_default_even_when_down(c) -> None:
    assert c.post("/send", json={"line": "x"}).json() == {"error": "no ports attached"}
    assert c.post("/ports", json={"alias": "solo", "device": UNOPENABLE}).status_code == 200
    try:
        r = c.post("/send", json={"line": "x"})
        assert r.status_code == 400 and "solo" in r.json()["error"], r.text
        assert "ambiguous" not in r.text
    finally:
        c.delete("/ports/solo")


def test_the_daemon_port_scopes_to_the_daemons_own_rows(c) -> None:
    """`port=""` names the daemon's own rows (SPEC 3.5), not every port."""
    _add(c, "board", raw="board row")
    on_loop(c, c.app.state.store.add_line(
        ts=time.time(), port="board", dir="rx", chan="event", seq=None, raw="!p 1 v=1",
        plot=[(1, None, "v", 1.0)]))
    own = c.get("/lines", params={"port": ""}).json()["lines"]
    assert own and {r["port"] for r in own} == {""}, own
    assert "board" in {r["port"] for r in c.get("/lines").json()["lines"]}   # control
    assert c.get("/plot/channels", params={"port": ""}).json()["channels"] == []
    assert [ch["name"] for ch in c.get("/plot/channels").json()["channels"]] == ["v"]
    sys_rows = c.post("/assert", json={"port": "", "chan": "sys", "forbid": ["x^"]}).json()
    assert sys_rows["checked_lines"] == sum(r["chan"] == "sys" for r in own), sys_rows


# -- Class 84/85: rows the host wrote (tx, markers, sys) are not judged ----------------------


def _post_later(stack: Stack, path: str, body: dict, delay: float = 0.3) -> threading.Timer:
    t = threading.Timer(delay, lambda: httpx.post(stack.base_url + path, json=body, timeout=5))
    t.start()
    return t


def _add_later_as(c: TestClient, dir: str, chan: str, raw: str) -> threading.Timer:
    store = c.app.state.store
    t = threading.Timer(0.2, lambda: on_loop(c, store.add_line(
        ts=time.time(), port="board", dir=dir, chan=chan, seq=None, raw=raw)))
    t.start()
    return t


def test_a_live_wait_does_not_match_a_marker_the_host_wrote(stack: Stack) -> None:
    mark = {"port": stack.alias, "text": "HOST-MARK"}
    wait = {"port": stack.alias, "match": "HOST-MARK", "timeout_ms": 1200}
    with _http(stack) as h:
        t = _post_later(stack, "/marker", mark)
        body = h.post("/wait", json=wait).json()
        t.join()
        assert body["status"] == "timeout", body
        t = _post_later(stack, "/marker", mark)
        named = h.post("/wait", json={**wait, "chan": "marker"}).json()
        t.join()
    assert named["status"] == "match" and named["line"]["dir"] == "-", named


def test_a_live_wait_does_not_match_a_sys_row(c) -> None:
    wait = {"match": "port board lost", "timeout_ms": 800}
    t = _add_later_as(c, "-", "sys", "port board lost")
    assert c.post("/wait", json=wait).json()["status"] == "timeout"
    t.join()
    t = _add_later_as(c, "-", "sys", "port board lost")
    named = c.post("/wait", json={**wait, "chan": "sys"}).json()
    t.join()
    assert named["status"] == "match" and named["line"]["chan"] == "sys", named


def test_a_live_wait_named_to_a_channel_skips_the_others(c) -> None:
    wait = {"match": "hello", "timeout_ms": 800, "chan": "resp"}
    t = _add_later_as(c, "rx", "debug", "hello")
    assert c.post("/wait", json=wait).json()["status"] == "timeout"
    t.join()
    t = _add_later_as(c, "rx", "resp", "hello")
    named = c.post("/wait", json=wait).json()
    t.join()
    assert named["status"] == "match" and named["line"]["chan"] == "resp", named


def test_a_retrospective_assert_does_not_pass_on_the_hosts_own_rows(stack: Stack) -> None:
    with _http(stack) as h:
        assert h.post("/marker", json={"port": stack.alias, "text": "HOST-MARK"}).status_code == 200
        sent = h.post("/cmd", json={"port": stack.alias, "cmd": "selftestx go"}).json()
        assert sent["status"] == "err", sent   # the reply names `selftestx`, not `selftestx go`
        for pattern, chan, dir in (("HOST-MARK", "marker", "-"), ("selftestx go", "cmd", "tx")):
            scope = {"port": stack.alias, "expect": [pattern], "last_ms": 10_000}
            body = h.post("/assert", json=scope).json()
            assert body["status"] == "fail" and body["expect"][0]["line"] is None, body
            named = h.post("/assert", json={**scope, "chan": chan}).json()
            assert named["status"] == "pass", named                        # positive control
            assert named["expect"][0]["line"]["dir"] == dir, named


def test_a_silent_port_with_a_marker_and_a_sys_row_is_still_empty(c) -> None:
    on_loop(c, c.app.state.store.add_line(
        ts=time.time(), port="quiet", dir="-", chan="sys", seq=None, raw="port quiet lost"))
    assert c.post("/marker", json={"port": "quiet", "text": "step 1"}).status_code == 200
    scope = {"port": "quiet", "forbid": ["PANIC"]}
    body = c.post("/assert", json=scope).json()
    assert body["status"] == "empty" and body["checked_lines"] == 0, body
    for chan in ("marker", "sys"):
        named = c.post("/assert", json={**scope, "chan": chan}).json()
        assert named["status"] == "pass" and named["checked_lines"] == 1, named
    _add(c, "quiet", raw="the target speaks")
    spoke = c.post("/assert", json=scope).json()
    assert spoke["status"] == "pass" and spoke["checked_lines"] == 1, spoke


def test_a_live_window_holding_only_a_marker_is_empty(c) -> None:
    live = {"forbid": ["PANIC"], "timeout_ms": 600}
    t = _add_later_as(c, "-", "marker", "step 2")
    body = c.post("/assert", json=live).json()
    t.join()
    assert body["status"] == "empty" and body["checked_lines"] == 0, body
    t = _add_later_as(c, "rx", "debug", "the target speaks")
    spoke = c.post("/assert", json=live).json()
    t.join()
    assert spoke["status"] == "pass" and spoke["checked_lines"] == 1, spoke


# -- PERF-4 / API-2: live matching has its own pool, bounded by the window ------------------


def test_a_live_wait_matches_while_the_shared_match_pool_is_busy(stack: Stack) -> None:
    release = threading.Event()
    busy = [match_executor().submit(release.wait, 20) for _ in range(MATCH_WORKERS)]
    try:
        with _http(stack) as h:
            r = h.post("/wait", json={"send": "ping", "match": "OK monitor", "timeout_ms": 2000})
            assert r.json()["status"] == "match", r.json()
    finally:
        release.set()
        for f in busy:
            f.result(5)


@pytest.mark.parametrize("path, body, scan", [
    ("/wait", {"send": "ping", "match": "OK", "timeout_ms": 300}, "_search_batch"),
    ("/assert", {"send": "ping", "forbid": ["PANIC"], "timeout_ms": 300}, "_scan_batch"),
])
def test_a_live_scan_is_cut_at_the_window_and_its_rows_reported(
    stack: Stack, monkeypatch, path, body, scan
) -> None:
    finish, entered, returned = threading.Event(), threading.Event(), threading.Event()

    def stuck(*_args):
        entered.set()
        finish.wait(20)
        returned.set()
        return None if scan == "_search_batch" else []

    monkeypatch.setattr(server_mod, scan, stuck)
    try:
        with _http(stack) as h:
            res = h.post(path, json=body).json()
        assert entered.is_set(), "positive control: a scan was started"
        assert not returned.is_set(), "the response waited for the scan past its window"
        assert res["dropped"] >= 1, f"the unjudged rows were not reported ({res})"
        assert res["status"] in ("timeout", "empty"), res
    finally:
        finish.set()


def test_a_wait_that_lost_rows_says_so_instead_of_reporting_timeout(stack, monkeypatch) -> None:
    """A wait whose feed shed rows has not seen the window it reports on.

    The regex runs in an executor, so the writer keeps broadcasting during that await; a
    burst past the subscriber queue drops the oldest, which can be the line being waited
    for. Driven before the fix: the needle was broadcast, 48 rows were shed, and /wait
    answered a clean {"status": "timeout"} that `mcu wait` turns into exit 2. A false
    negative on an assertion API is worse than a slow one.
    """
    import threading

    store = stack.app.state.store
    original = Store.subscribe
    # A 4-row queue stands in for the 2000-row one overrun during a slow match; the defect
    # is the silence, not the size.
    monkeypatch.setattr(Store, "subscribe", lambda self, pf=None, maxsize=4: original(self, pf, 4))

    def flood() -> None:
        time.sleep(0.3)
        for i in range(50):
            store._broadcast({"id": 900_000 + i, "port": stack.alias, "dir": "rx",
                              "chan": "debug",
                              "raw": "NEEDLE" if i == 0 else f"noise{i}"})

    threading.Thread(target=flood, daemon=True).start()
    res = httpx.post(stack.base_url + "/wait",
                     json={"match": "NEEDLE", "timeout_ms": 1500}, timeout=20).json()
    assert res["dropped"] > 0, (
        "rows were shed and the wait reported nothing: a timeout from this run is "
        f"indistinguishable from a real negative ({res})"
    )


# -- serial link ----------------------------------------------------------------------


def test_wait_with_send_still_matches_when_the_send_used_the_whole_window(
    make_stack,
) -> None:
    """/wait reported a timeout without ever looking at a match already in its queue.

    `send` is given the same timeout as the whole wait, so a command whose response never
    comes (here: --drop-response) burned the entire window; the loop then saw remaining
    <= 0 and broke immediately. The sim's 10 Hz CAN heartbeat has been queueing the whole
    time, so a correct implementation drains and evaluates it before giving up.
    """
    import httpx

    stack = make_stack(["--drop-response", "2"])   # 1 is the connect-time ping
    r = httpx.post(
        f"{stack.base_url}/wait",
        json={"match": "!can", "send": "ping", "timeout_ms": 1500, "port": stack.alias},
        timeout=15.0,
    )
    assert r.status_code == 200
    body = r.json()
    # The send itself timed out, which is the precondition this test needs to hold.
    assert body["cmd_result"] is not None
    assert body["cmd_result"]["status"] == "timeout"
    assert body["status"] == "match", body
    assert "!can" in body["line"]["raw"]


def test_assert_with_send_still_judges_lines_the_send_used_the_whole_window_for(
    make_stack,
) -> None:
    """The same defect as /wait above, in the loop nobody had shared with it.

    /wait was fixed by draining before giving up; /assert kept `if remaining <= 0: break`
    ahead of its drain, so it answered "fail" with checked_lines 0 and the matching line
    still sitting in the queue. Both endpoints run one CaptureWatch now, so the drain rule
    holds for whichever of them the next change touches.
    """
    import httpx

    stack = make_stack()
    port = stack.app.state.ports._ports[stack.alias]

    # Answered just as the window ends: a send that times out now fails the assert outright
    # (SPEC 3.4), so the window-consuming send has to be one that succeeds.
    async def answered_at_the_deadline(cmd, timeout_ms, eol=None):
        await asyncio.sleep(timeout_ms / 1000.0)
        return {"status": "ok", "seq": 0, "data": "", "latency_ms": float(timeout_ms),
                "line_id": None}

    port.send_command = answered_at_the_deadline
    r = httpx.post(
        f"{stack.base_url}/assert",
        json={
            "expect": ["!can"],
            "send": "ping",
            "timeout_ms": 1500,
            "port": stack.alias,
        },
        timeout=15.0,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["cmd_result"]["latency_ms"] == 1500.0, "precondition: the send took the window"
    assert body["checked_lines"] > 0, body
    assert body["status"] == "pass", body
    assert body["expect"][0]["matched"] is True


# -- owed by the coverage disposition ---------------------------------------------------


def test_wait_and_assert_accept_send_mode_raw(stack: Stack) -> None:
    # `raw` writes the line verbatim, so it needs the `>SEQ CMD` wire form. Zero coverage
    # on both routes until now, on the one field whose other value silently sends a
    # different thing.
    with stack_client(stack) as c:
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


def _add_later(c: TestClient, raw: str, delay: float = 0.2) -> threading.Timer:
    store = c.app.state.store
    t = threading.Timer(delay, lambda: on_loop(c, store.add_line(
        ts=time.time(), port="board", dir="rx", chan="debug", seq=None, raw=raw)))
    t.start()
    return t


@pytest.fixture
def stall(monkeypatch):
    """_scan_batch that parks the scans of the patterns in `stall.patterns` past the window."""
    release = threading.Event()
    real = server_mod._scan_batch

    def scan(pats, texts):
        if {p.pattern for p in pats} & scan.patterns:
            release.wait(10)
            return []
        return real(pats, texts)

    scan.patterns = set()
    monkeypatch.setattr(server_mod, "_scan_batch", scan)
    monkeypatch.setattr(server_mod, "LIVE_SCAN_GRACE_S", 0.1)
    yield scan
    release.set()


# -- finding 2: a forbid hit is decided before the expect scan can be cut ---------------------


def test_a_forbid_hit_survives_a_cut_expect_scan(c, stall) -> None:
    stall.patterns = {"NEVER"}
    timer = _add_later(c, "OK boot")
    res = c.post("/assert", json={"forbid": ["OK"], "expect": ["NEVER"], "timeout_ms": 1500}).json()
    timer.join()
    assert res["status"] == "fail" and res["reason"] is None, res
    assert res["forbid"][0]["matched"] and res["forbid"][0]["line"]["raw"] == "OK boot", res
    assert res["checked_lines"] == 1 and res["dropped"] == 0, res


def test_a_cut_expect_scan_after_a_clean_forbid_scan_counts_the_batch_unjudged(c, stall) -> None:
    stall.patterns = {"NEVER"}
    timer = _add_later(c, "fine")
    res = c.post("/assert", json={"forbid": ["PANIC"], "expect": ["NEVER"],
                                  "timeout_ms": 400}).json()
    timer.join()
    assert res["checked_lines"] == 0 and res["dropped"] == 1, res
    assert not res["forbid"][0]["matched"]
    assert res["status"] == "empty", res


# -- finding 3: lines dropped unjudged are not the empty window allow_empty accepts ------------


@pytest.mark.parametrize("allow_empty", [False, True])
def test_a_window_whose_lines_were_all_cut_is_empty_and_says_why(c, stall, allow_empty) -> None:
    stall.patterns = {"PANIC"}
    timer = _add_later(c, "ordinary")
    res = c.post("/assert", json={"forbid": ["PANIC"], "timeout_ms": 400,
                                  "allow_empty": allow_empty}).json()
    timer.join()
    assert res["status"] == "empty", res
    assert res["reason"] == "no lines were judged: 1 dropped unjudged", res
    assert res["checked_lines"] == 0 and res["dropped"] == 1


def test_a_quiet_window_still_passes_under_allow_empty(c, stall) -> None:
    stall.patterns = {"PANIC"}   # armed, but no line arrives to be scanned
    res = c.post("/assert", json={"forbid": ["PANIC"], "timeout_ms": 200,
                                  "allow_empty": True}).json()
    assert res["status"] == "pass" and res["reason"] is None, res
    assert res["dropped"] == 0
    quiet = c.post("/assert", json={"forbid": ["PANIC"], "timeout_ms": 200}).json()
    assert quiet["reason"] == "no lines were checked in the window", quiet
