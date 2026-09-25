"""What the API refuses before a handler runs (SPEC 3.3.1, 3.4): unknown body fields and query
parameters, lax types, and integers or strings outside the bounds the handlers rely on."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import MAX_DEVICE_LEN, MAX_SERIAL_LEN, MIN_LINE_ID, create_app
from tests.support import UNOPENABLE, Stack, mk_app, stack_client


@pytest.fixture
def c(tmp_path):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db")),
    )
    app = create_app(config, config_path=tmp_path / "config.toml")
    # Loopback, so the config-write bar does not answer first.
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        yield client


def _error(r) -> str:
    return r.json()["error"]


# -- API-4: unknown fields and parameters ------------------------------------------------


def test_a_misspelled_assert_scope_is_refused_by_name(c) -> None:
    right = c.post("/assert", json={"forbid": ["x"], "session": "nope"})
    assert right.status_code == 400 and "no such session" in _error(right)   # positive control
    typo = c.post("/assert", json={"forbid": ["x"], "sesion": "nope"})
    assert typo.status_code == 422, typo.text
    assert "sesion" in _error(typo) and "Extra inputs" in _error(typo)


def test_a_misspelled_wait_timeout_is_refused_by_name(c) -> None:
    r = c.post("/wait", json={"match": "x", "timout_ms": 50})
    assert r.status_code == 422 and "timout_ms" in _error(r)


def test_an_unknown_field_inside_a_port_entry_is_refused(c) -> None:
    r = c.put("/config/ports", json={"ports": [{"alias": "a", "device": "/dev/x", "bogus": 1}]})
    assert r.status_code == 422 and "ports.0.bogus" in _error(r)


def test_an_undeclared_query_parameter_is_refused_by_name(c) -> None:
    assert c.get("/lines", params={"chan": "sys"}).status_code == 200   # positive control
    r = c.get("/lines", params={"chans": "sys", "zzz": "1"})
    assert r.status_code == 422
    assert _error(r) == "chans: unknown query parameter; zzz: unknown query parameter"


def test_an_undeclared_query_parameter_is_refused_beside_a_path_parameter(c) -> None:
    r = c.delete("/sessions/1", params={"date": "true"})
    assert r.status_code == 422 and "date: unknown query parameter" in _error(r)


def test_the_websocket_and_the_root_redirect_keep_their_extra_parameters(c, monkeypatch) -> None:
    from mcuscope import server as server_mod

    monkeypatch.setattr(server_mod, "WS_KEEPALIVE_S", 0.2)   # the close waits one keepalive
    # `token` is read by the token guard, never declared on the route.
    with c.websocket_connect("/ws?token=abc", headers={"host": "127.0.0.1"}) as ws:
        assert '"capture"' in ws.receive_text()
    r = c.get("/?from=bookmark", follow_redirects=False)
    assert r.status_code == 307


# -- API-9: strict types -------------------------------------------------------------------


@pytest.mark.parametrize("body", [
    {"ms": True}, {"ms": "5"}, {"ms": 5.0},
])
def test_a_break_length_must_be_an_integer(c, body) -> None:
    r = c.post("/break", json=body)
    assert r.status_code == 422 and "ms" in _error(r)


def test_a_valid_break_length_reaches_the_handler(c) -> None:
    r = c.post("/break", json={"ms": 5})
    assert r.status_code == 400 and "no ports attached" in _error(r)


@pytest.mark.parametrize("body", [
    {"all": "yes"}, {"all": 1}, {"all": True, "dry_run": "true"}, {"all": True, "dry_run": 0},
])
def test_a_purge_flag_must_be_a_boolean(c, body) -> None:
    r = c.post("/purge", json=body)
    assert r.status_code == 422, r.text


def test_a_json_integer_still_fills_a_float_field(c) -> None:
    r = c.post("/purge", json={"before_ts": 1, "dry_run": True})
    assert r.status_code == 200 and r.json()["dry_run"] is True


# -- API-5: since_id lower bound ------------------------------------------------------------


@pytest.mark.parametrize("path, extra", [
    ("/lines", {}), ("/lines/export", {}), ("/can/frames", {}),
    ("/plot/series", {"name": "v"}), ("/plot/export", {"names": "v"}),
])
def test_a_since_id_below_the_sqlite_range_is_a_422(c, path, extra) -> None:
    below = c.get(path, params={**extra, "since_id": MIN_LINE_ID - 1})
    assert below.status_code == 422 and "since_id" in _error(below)
    floor = c.get(path, params={**extra, "since_id": MIN_LINE_ID})
    assert floor.status_code != 422, floor.text   # the floor itself is a cursor


# -- API-6 / API-8: attach and saved-port strings --------------------------------------------


@pytest.mark.parametrize(
    "field, size", [("device", MAX_DEVICE_LEN), ("serial_number", MAX_SERIAL_LEN)]
)
def test_attach_and_saved_ports_share_one_length_bound(c, field, size) -> None:
    other = {"serial_number": "SN"} if field == "device" else {"device": UNOPENABLE}
    for value, want in (("x" * (size + 1), 422), ("x" * size, None)):
        attach = c.post("/ports", json={"alias": "big", field: value, **other})
        save = c.put("/config/ports", json={"ports": [{"alias": "big", field: value, **other}]})
        if want == 422:
            assert attach.status_code == 422 and field in _error(attach)
            assert save.status_code == 422 and field in _error(save)
        else:
            assert attach.status_code != 422 and save.status_code != 422
    c.delete("/ports/big")


@pytest.mark.parametrize("field", ["serial_number", "device"])
def test_a_control_character_in_a_saved_port_is_refused(c, field) -> None:
    base = {"alias": "a", "device": UNOPENABLE, "serial_number": "SN1"}
    ok = c.put("/config/ports", json={"ports": [base]})
    assert ok.status_code == 200, ok.text                       # positive control
    for bad in ("x\x1by", "x\x00y", "x\ty"):
        r = c.put("/config/ports", json={"ports": [{**base, field: bad}]})
        assert r.status_code == 400 and _error(r) == f"port a: invalid {field}", r.text
        attach = c.post("/ports", json={**base, "alias": "b", field: bad})
        assert attach.status_code == 400 and _error(attach) == f"invalid {field}"


# -- C1: one stored row is one line ----------------------------------------------------


def test_a_multi_line_marker_is_stored_as_one_line(client) -> None:
    """C1's root cause. `mcu mark "$(printf 'a\\nb')"` stored a row whose `raw` held a
    newline, so `log export` counted it as two lines while `--limit` counted one: the same
    window, the same command, two answers.
    """
    r = client.post("/marker", json={"text": "multi\nline\r\nmarker\rtail"})
    assert r.status_code == 200
    line_id = r.json()["line_id"]
    rows = client.get("/lines", params={"limit": 10}).json()["lines"]
    row = next(x for x in rows if x["id"] == line_id)
    assert "\n" not in row["raw"] and "\r" not in row["raw"], row["raw"]
    assert row["raw"] == "multi line marker tail", "folded to a space, not run together"
    stored = client.get("/lines", params={"limit": 1000}).json()["lines"]
    text = client.get("/lines/export", params={"format": "text"}).text
    assert len(text.splitlines()) == len(stored), "one stored row is one exported line"
    assert text.count("multi line marker tail") == 1
    csv = client.get("/lines/export", params={"format": "csv"}).text
    assert len(csv.splitlines()) == len(stored) + 1, "header plus one line per row"


def test_request_body_bounds(tmp_path) -> None:
    from fastapi.testclient import TestClient

    app = mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        # timeout_ms must be positive and bounded
        for bad in (0, -5, 10**9):
            r = c.post("/cmd", json={"cmd": "ping", "timeout_ms": bad})
            assert r.status_code == 422, bad
            assert "error" in r.json()
        # alias must be non-empty and sane (empty collides with the daemon port="")
        for alias in ("", " ", "a b", "x" * 40):
            r = c.post("/ports", json={"alias": alias, "device": "COM99"})
            assert r.status_code == 422, alias
        # /wait only supports since="now"
        r = c.post("/wait", json={"match": "x", "timeout_ms": 10, "since": "id:5"})
        assert r.status_code == 400
        # /can/frames: truncated flag present, oversized id rejected
        r = c.get("/can/frames")
        assert r.status_code == 200 and r.json()["truncated"] is False
        r = c.get("/can/frames", params={"id": "FFFFFFFF"})
        assert r.status_code == 400


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ("/lines?chan=nope&limit=5", "chan"),
        # `order` was `"DESC" if order == "desc" else "ASC"`, so any other value silently
        # returned the rows in the opposite order to the one asked for, which is worse than
        # an empty result: the caller gets data and it is wrong.
        ("/lines?order=bogus&limit=5", "order"),
    ],
)
def test_closed_query_domains_are_refused_too(stack, query, field) -> None:
    """The same defect as the body params below, on the query string (C1, class-wide).

    Found by re-running C1's sweep after claiming it closed: two of four sites had been
    fixed. Every wire-facing string parameter on every handler was then enumerated, and
    these are the only two with a closed documented domain and no declaration.
    """
    r = httpx.get(stack.base_url + query, timeout=15)
    assert r.status_code == 422, f"{field} was accepted: {r.status_code} {r.text[:200]}"
    assert field in r.text, f"the refusal does not name the offending field: {r.text[:200]}"


@pytest.mark.parametrize(
    ("path", "body", "field"),
    [
        ("/wait", {"match": "x", "chan": "nope", "timeout_ms": 100}, "chan"),
        ("/wait", {"match": "x", "send": "ping", "send_mode": "bogus", "timeout_ms": 100},
         "send_mode"),
        ("/assert", {"expect": ["x"], "chan": "nope", "timeout_ms": 100}, "chan"),
        ("/assert", {"expect": ["x"], "send": "ping", "send_mode": "bogus", "timeout_ms": 100},
         "send_mode"),
    ],
)
def test_closed_request_domains_are_refused_not_silently_reinterpreted(
    stack, path, body, field
) -> None:
    """A value outside a closed domain must fail the request, not do something else quietly.

    Found by the coverage leg reading uncovered lines as untested *request parameters*.
    `send_mode` was only ever compared `== "raw"`, so any other value silently sent as a
    command instead; `chan` was matched by equality against stored rows, so an unknown one
    never matched and the caller waited out its whole timeout to be told "no match" rather
    than "no such channel". Both answered 200 with a plausible negative, which for the agent
    that is this API's primary consumer is worse than an error.
    """
    r = httpx.post(stack.base_url + path, json=body, timeout=15)
    assert r.status_code == 422, f"{field} was accepted: {r.status_code} {r.text[:200]}"
    assert field in r.text, f"the refusal does not name the offending field: {r.text[:200]}"


def test_marker_port_is_bounded_like_the_alias_grammar(tmp_path) -> None:
    # /marker is the only endpoint whose `port` reaches store.add_line without going
    # through _resolve_port(), and the field was unvalidated: a 100k-char port and a
    # port carrying NUL/control bytes both stored verbatim with a 200, defeating the
    # max_length on `text` through the field beside it. /send with the same port 400s.
    from fastapi.testclient import TestClient

    app = mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        # A port must also be one the capture knows (CLI-3); this one has history.
        asyncio.run_coroutine_threadsafe(c.app.state.store.add_line(
            ts=time.time(), port="board-1.a", dir="rx", chan="debug", seq=None, raw="seed",
        ), c.app.state.ports._loop).result(5)
        before = len(c.get("/lines", params={"limit": 1000}).json()["lines"])
        for bad in ("p" * 100_000, "p" * 33, "a\x00b", "a\x01b", "a\nb", "-lead"):
            r = c.post("/marker", json={"text": "marked", "port": bad})
            assert r.status_code == 400, bad
            # The grammar guard's own text: `_unknown_port` answers "no such port" instead.
            assert r.json()["error"].startswith("invalid port: "), bad
        # Nothing reached the capture: every attempt was refused before the write.
        rows = c.get("/lines", params={"limit": 1000}).json()["lines"]
        assert len(rows) == before
        assert not [r for r in rows if r["raw"] == "marked"]

        # A well-formed alias and an absent port are both still accepted, and a marker
        # need not name a port at all (SPEC 3.5).
        assert c.post("/marker", json={"text": "named", "port": "board-1.a"}).status_code == 200
        assert c.post("/marker", json={"text": "unnamed"}).status_code == 200
        assert c.post("/marker", json={"text": "empty", "port": ""}).status_code == 200
        rows = c.get("/lines", params={"limit": 1000}).json()["lines"]
        assert len(rows) == before + 3


BIG = str(10**400)   # arbitrary precision: what an unbounded int param used to swallow


# -- CD1: an out-of-range integer parameter is a refusal, never a 500 ------------------


def test_out_of_range_integer_params_are_refused_not_a_500(stack: Stack) -> None:
    # Each of these reached either a float conversion or a SQLite bind and raised
    # OverflowError there: a 500 plus a full traceback in the daemon log, for input the
    # daemon should refuse in one line. 422 is FastAPI's validation answer.
    with stack_client(stack) as c:
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
    with stack_client(stack) as c:
        assert c.get("/lines", params={"limit": 999999}).status_code == 200
        assert c.get("/lines", params={"limit": 0}).json()["lines"] == []


def test_marker_text_is_stripped_and_a_blank_one_refused(client) -> None:
    def markers() -> list[str]:
        rows = client.get("/lines", params={"chan": "marker", "order": "asc"}).json()["lines"]
        return [row["raw"] for row in rows]

    before = markers()
    # Blank as stored: spaces, and line breaks the store folds to spaces.
    for blank in ("   ", " \n ", "\r\n"):
        r = client.post("/marker", json={"text": blank})
        assert r.status_code == 422 and "must not be blank" in r.text, repr(blank)
    assert markers() == before
    # Only U+0020 is space (SPEC 2.5): a tab is text, kept and not stripped.
    for text in ("  x  ", "\tx\t", "\t"):
        assert client.post("/marker", json={"text": text}).status_code == 200, repr(text)
    assert markers() == [*before, "x", "\tx\t", "\t"]


def test_session_names_still_strip_every_whitespace(client) -> None:
    r = client.post("/sessions", json={"name": "\tbench\n"})
    assert r.status_code == 200, r.text
    assert r.json()["session"]["name"] == "bench"
    assert client.post("/sessions", json={"name": "\t\n"}).status_code == 422


def test_send_mode_without_send_is_refused_on_wait_and_assert(c) -> None:
    refusal = "send_mode applies to send; set send too"
    for path, body in (
        ("/wait", {"match": "x", "timeout_ms": 20}),
        ("/assert", {"expect": ["x"], "timeout_ms": 20}),
    ):
        for mode in ("raw", "cmd"):   # the default, sent explicitly, is still a field ignored
            r = c.post(path, json={**body, "send_mode": mode})
            assert r.status_code == 400 and _error(r) == refusal, (path, mode)
        # eol keeps its own refusal, so the two paths stay distinguishable.
        r = c.post(path, json={**body, "eol": "crlf"})
        assert r.status_code == 400 and _error(r) == "eol applies to send; set send too"
        # Positive control: the same request without the field is judged normally.
        assert c.post(path, json=body).status_code == 200, path
    # With send, send_mode is read: the refusal is about the port, not send_mode.
    r = c.post("/wait", json={"match": "x", "timeout_ms": 20, "send": "ping",
                              "send_mode": "raw"})
    assert r.status_code == 400 and "send_mode" not in _error(r)


def test_query_and_path_params_hold_their_grammar(c) -> None:
    for name in ("a", "b", "c"):
        assert c.post("/sessions", json={"name": name}).status_code == 200
    c.post("/sessions/stop")

    def ids() -> set[int]:
        return {s["id"] for s in c.get("/sessions").json()["sessions"]}

    assert {2, 3} <= ids()
    for method, url, param in (
        ("DELETE", "/sessions/+2", "session_id"),
        ("DELETE", "/sessions/%202%20", "session_id"),
        ("DELETE", "/sessions/3?data=yes", "data"),
        ("GET", "/lines?limit=1_0", "limit"),
        ("GET", "/lines?since_id=%2B1", "since_id"),
        ("GET", "/lines?since_ts=1_0", "since_ts"),
        ("GET", "/lines?until_ts=%2B5", "until_ts"),
        ("GET", "/lines?since_ts=%C4%B1nf", "since_ts"),   # U+0131, folds to i unless ASCII
        ("GET", "/lines?until_ts=%C4%B0nf", "until_ts"),   # U+0130
        ("GET", "/can/frames?bus=%D9%A3", "bus"),        # U+0663
        ("GET", "/plot/series?name=x&decimate=2.0", "decimate"),
        ("GET", "/plot/export?names=x&decode=on", "decode"),
        ("GET", "/sessions/1/export?wait=True", "wait"),
    ):
        r = c.request(method, url)
        assert r.status_code == 422, url
        assert _error(r).startswith(f"{param}: must be "), (url, _error(r))
    assert {2, 3} <= ids(), "a refused delete deleted"
    # A non-finite float passes the grammar to SPEC 3.4's own 400, which names the field.
    for q in ("since_ts=1e999", "since_ts=nan", "since_ts=-Infinity"):
        r = c.get(f"/lines?{q}")
        assert r.status_code == 400 and _error(r) == "since_ts must be a finite number", q
    # Positive controls: the grammar's own forms still work.
    assert c.get("/lines?limit=10&since_id=-5&since_ts=1.5e3&until_ts=.5e10").status_code == 200
    assert c.get("/plot/series?name=x&decimate=-1").status_code == 200   # floors at 1
    assert c.delete("/sessions/3?data=0").status_code == 200
    assert c.delete("/sessions/2").status_code == 200
    assert not {2, 3} & ids()


def test_every_numeric_and_bool_url_param_carries_its_grammar(c) -> None:
    # Enumerated from the routes, not listed: a new parameter without a Url* type, or one
    # written `x: UrlUInt = Query(...)` (FastAPI drops the validator there), fails here.
    def params(dep):
        yield from dep.query_params
        yield from dep.path_params
        for sub in dep.dependencies:
            yield from params(sub)

    seen, bare = 0, []
    for route in c.app.routes:
        dep = getattr(route, "dependant", None)
        for f in params(dep) if dep else ():
            shape = repr(f.field_info.annotation) + repr(f.field_info.metadata)
            if any(t in shape for t in ("int", "float", "bool")):
                seen += 1
                if "_url_grammar" not in shape:
                    bare.append(f"{route.path} {f.name}")
    assert seen >= 36, seen   # positive control: the walk reaches the parameters
    assert bare == []
