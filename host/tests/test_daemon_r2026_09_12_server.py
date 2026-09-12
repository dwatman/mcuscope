"""Server fixes from the 2026-09-12 adversarial round: W4, C1, D3, D4, L1, improvements 4 and 7.

The app-level tests share `test_export_lines_can`'s fixture: rows carry explicit
timestamps, because every assertion is about which side of a bound a row falls on or what
the response says about the window it covers.
"""

from __future__ import annotations

import signal
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from mcuscope.serial_link import PortError, PortManager
from tests.test_export_lines_can import T0, _add, _mk_app, _on_loop


@pytest.fixture
def client(tmp_path):
    with TestClient(_mk_app(tmp_path), base_url="http://127.0.0.1") as c:
        yield c


def _plot(client, ts: float, name: str = "v", value: float = 1.0):
    """One stored line carrying one ad-hoc plot point, so `names=` has something to name."""
    store = client.app.state.store
    return _on_loop(
        client,
        store.add_line(ts=ts, port="board", dir="rx", chan="event", seq=None,
                       raw=f"!p {name}={value}", plot=[(1, None, name, value)]),
    )


def _disposition(r: httpx.Response) -> str:
    return r.headers["content-disposition"]


def _stamps(filename: str) -> tuple[str, str]:
    """The `<from>-<to>` pair out of `<session>_<kind>_<from>-<to>.<ext>`."""
    stem = filename.rsplit(".", 1)[0]
    lo, _, hi = stem.rpartition("-")
    return lo.rsplit("_", 1)[-1], hi


# -- W4: a freeze taken before any line arrived is an empty window, not a refusal -------


def test_id_to_zero_is_an_empty_window_on_every_export(client) -> None:
    """A panel paused before its first line sends `id_to=0` (SPEC 3.4 never floors it).

    `ge=1` turned that into a 422 the browser could only report as a failed download, on
    the one path where the honest answer is "nothing was shown, so nothing is exported".
    """
    _add(client, ts=T0, raw="line0")
    _plot(client, T0)
    cases = (
        ("/lines", {"id_to": 0}),
        ("/lines/export", {"id_to": 0}),
        ("/can/frames", {"id_to": 0, "format": "csv"}),
        ("/plot/series", {"id_to": 0, "name": "v"}),
        ("/plot/export", {"id_to": 0, "names": "v"}),
    )
    for path, params in cases:
        r = client.get(path, params=params)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"
    assert client.get("/lines", params={"id_to": 0}).json()["lines"] == []
    assert client.get("/lines/export", params={"id_to": 0}).text == ""
    assert client.get("/can/frames", params={"id_to": 0}).json()["frames"] == []
    assert client.get("/plot/series", params={"id_to": 0, "name": "v"}).json()["points"] == []
    # The csv exports still carry their header, so the file parses (SPEC 3.4).
    can_csv = client.get("/can/frames", params={"id_to": 0, "format": "csv"}).text
    assert can_csv.splitlines() == ["id,ts,tick_ms,bus,can_id,ext,rtr,dlc,data"]
    assert client.get("/lines", params={"id_to": -1}).status_code == 422, \
        "a negative bound is still an arithmetic slip"


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


# -- D3: the filename names the window the rows came from ------------------------------


def test_last_ms_in_a_filename_is_anchored_where_the_rows_are(client) -> None:
    """The `from` stamp was `now - last_ms` while the rows came from the id bound.

    On an ended session that made `from` 835.6 s *later* than `to`, and later than every
    row in the file: a name that contradicts its own contents.
    """
    rows = [_add(client, ts=T0 + i, raw=f"line{i}") for i in range(5)]
    frozen = rows[-1]["id"]

    r = client.get("/lines/export", params={
        "id_to": frozen, "last_ms": 2000, "until_ts": T0 + 4, "format": "jsonl",
    })
    assert r.status_code == 200
    lo, hi = _stamps(_disposition(r))
    assert lo <= hi, f"the window runs backwards: {lo}-{hi}"
    assert lo == time.strftime("%Y%m%dT%H%M%S", time.localtime(T0 + 2)), lo
    assert hi == time.strftime("%Y%m%dT%H%M%S", time.localtime(T0 + 4)), hi
    body = [line for line in r.text.splitlines() if line]
    assert body, "an empty window would prove nothing about the stamps"
    stamps = [float(line.split('"ts": ')[1].split(",")[0]) for line in body]
    assert min(stamps) >= T0 + 2 and max(stamps) <= T0 + 4, stamps


# -- D4: `raw` is faithful in every format ---------------------------------------------


HOSTILE = ("-45.2 leading minus", "=SUM(A1)", "+1 and more", "@here", "\tstarts with a tab")


def test_csv_export_does_not_rewrite_a_captured_line(client) -> None:
    """The spreadsheet-formula guard was written for `!pd` channel names and sids, not for
    console text: it prefixed an apostrophe to any `raw` starting with `-`, `+`, `=`, `@`
    or a control character, and a consumer cannot tell that apostrophe from one the device
    sent. The same window exported three different ways.
    """
    for raw in HOSTILE:
        _add(client, ts=T0, raw=raw)
    body = client.get("/lines/export", params={"format": "csv", "chan": "debug"}).text
    cells = [line.rsplit(",", 1)[-1] for line in body.splitlines()[1:]]
    unquoted = [c[1:-1].replace('""', '"') if c.startswith('"') else c for c in cells]
    assert unquoted == list(HOSTILE), body
    assert "'" not in body, "no apostrophe the device did not send"
    # And the quoting that keeps the file parseable is still there.
    _add(client, ts=T0, raw='has, a comma and "quotes"')
    last = client.get(
        "/lines/export", params={"format": "csv", "chan": "debug"}
    ).text.splitlines()[-1]
    assert last.endswith('"has, a comma and ""quotes"""'), last
    # Same class, same fix: `dir` is `rx`, `tx` or `-`, and the guard was rewriting every
    # sys and marker row's `-` into `'-`, so the csv column disagreed with the JSON one.
    whole = client.get("/lines/export", params={"format": "csv"}).text
    dirs = {line.split(",")[3] for line in whole.splitlines()[1:]}
    assert dirs <= {"rx", "tx", "-"}, dirs


def test_a_channel_name_is_still_guarded(client) -> None:
    """The exemption is for `raw` alone: a device-declared name still cannot execute."""
    from mcuscope.server import _csv_cell

    assert _csv_cell("=cmd(1)") == "'=cmd(1)"
    assert _csv_cell("=cmd(1)", formula_guard=False) == "=cmd(1)"


# -- improvement 4: an unresolvable session= is refused, an empty one is not ------------


def test_a_session_that_holds_no_lines_still_answers_empty(client) -> None:
    """Without this the obvious over-correction - refusing every empty result - passes."""
    _plot(client, T0)   # `names=v` has to name a real channel (improvement 9)
    _on_loop(client, client.app.state.store.start_session("quiet"))
    _on_loop(client, client.app.state.store.stop_session())
    for path, params in (
        ("/lines", {"session": "quiet"}),
        ("/lines/export", {"session": "quiet"}),
        ("/can/frames", {"session": "quiet"}),
        ("/plot/series", {"session": "quiet", "name": "v"}),
        ("/plot/export", {"session": "quiet", "names": "v"}),
    ):
        r = client.get(path, params=params)
        assert r.status_code == 200, f"{path}: {r.text}"


def test_an_unknown_session_is_refused_by_name_on_every_read(client) -> None:
    _add(client, ts=T0, raw="line0")
    _plot(client, T0)
    for path, params in (
        ("/lines", {"session": "typo"}),
        ("/lines/export", {"session": "typo"}),
        ("/can/frames", {"session": "typo"}),
        ("/plot/series", {"session": "typo", "name": "v"}),
        ("/plot/export", {"session": "typo", "names": "v"}),
    ):
        r = client.get(path, params=params)
        assert r.status_code == 400, f"{path}: {r.status_code}"
        assert r.json()["error"] == "no such session: typo", path


# -- improvement 7: a long poll cut short by shutdown says so --------------------------


def _wait_in_thread(base_url: str, timeout_ms: int, out: list) -> threading.Thread:
    def go() -> None:
        with httpx.Client(base_url=base_url, timeout=30.0) as c:
            out.append(c.post("/wait", json={"match": "never-arrives",
                                             "timeout_ms": timeout_ms}))

    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


def test_a_wait_cut_short_by_shutdown_is_a_503_not_a_timeout(stack) -> None:
    """The cheap wrong fix wakes the watcher and lets the handler report an ordinary
    timeout, which reads to an agent as "the board stayed silent" - a verdict over a
    window that was never run. Before the fix the handler was cancelled by uvicorn and the
    client saw `Internal Server Error` with exit 1, where SPEC 4 has exit 3 for this.
    """
    out: list = []
    t = _wait_in_thread(stack.base_url, 25_000, out)
    time.sleep(0.5)
    # Through uvicorn's exit hook, the path both `mcu daemon stop` and SIGTERM take: the
    # store's own stop() runs in the lifespan finaliser, which uvicorn reaches only after
    # its graceful wait has cancelled the parked handler into a 500 (fix-diff F1).
    loop = stack.app.state.ports._loop
    loop.call_soon_threadsafe(stack._server.handle_exit, signal.SIGTERM, None)
    t.join(15)
    assert out, "the wait never returned"
    r = out[0]
    assert r.status_code == 503, f"{r.status_code} {r.text}"
    assert r.json()["error"] == "daemon is shutting down; the wait was cut short"


def test_an_ordinary_timeout_is_still_a_200(stack) -> None:
    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        r = c.post("/wait", json={"match": "never-arrives", "timeout_ms": 150})
    assert r.status_code == 200
    assert r.json()["status"] == "timeout"


def test_an_assert_cut_short_by_shutdown_is_a_503(stack) -> None:
    out: list = []

    def go() -> None:
        with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
            out.append(c.post("/assert", json={"expect": ["never-arrives"],
                                               "timeout_ms": 25_000}))

    t = threading.Thread(target=go, daemon=True)
    t.start()
    time.sleep(0.5)
    # Through uvicorn's exit hook, the path both `mcu daemon stop` and SIGTERM take: the
    # store's own stop() runs in the lifespan finaliser, which uvicorn reaches only after
    # its graceful wait has cancelled the parked handler into a 500 (fix-diff F1).
    loop = stack.app.state.ports._loop
    loop.call_soon_threadsafe(stack._server.handle_exit, signal.SIGTERM, None)
    t.join(15)
    assert out and out[0].status_code == 503, out
    assert out[0].json()["error"] == "daemon is shutting down; the wait was cut short"


# -- L1: a sole connected port is not ambiguous ----------------------------------------


class _Attached:
    """A port slot as `PortManager.resolve` reads it: an alias and a connected flag."""

    def __init__(self, alias: str, connected: bool) -> None:
        self.alias = alias
        self.connected = connected


def _manager(*ports: _Attached) -> PortManager:
    pm = PortManager(store=None, loop=None)
    pm._ports = {p.alias: p for p in ports}
    return pm


def test_several_attached_with_one_connected_resolves_to_it() -> None:
    """Survivor L1: the whole resolution was unpinned. `test_cli_ux.py:395` attaches a
    second port that *is* connected, so it drives the unchanged two-live path.
    """
    pm = _manager(_Attached("retrying", False), _Attached("live", True))
    assert pm.resolve(None).alias == "live"
    assert pm.resolve("retrying").alias == "retrying", "naming one still wins"


def test_two_connected_ports_stay_ambiguous() -> None:
    pm = _manager(_Attached("a", True), _Attached("b", True))
    with pytest.raises(PortError, match="ambiguous"):
        pm.resolve(None)


def test_no_connected_port_among_several_stays_ambiguous() -> None:
    """Not "pick the first": with nothing connected there is no evidence either way, and
    guessing would send a command to a board the caller did not name."""
    pm = _manager(_Attached("a", False), _Attached("b", False))
    with pytest.raises(PortError, match="ambiguous"):
        pm.resolve(None)


def test_a_sole_attached_port_needs_no_connection() -> None:
    pm = _manager(_Attached("only", False))
    assert pm.resolve(None).alias == "only"
