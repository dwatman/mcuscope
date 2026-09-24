"""One window per export request (SPEC 3.4): the `until_ts` ceiling, `last_ms`, `id_to`,
session crossings, and the stamps in an export's filename.

App-level tests use the in-process app on a file-backed capture (the `client` fixture), so
the offloaded reads really leave the loop."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from mcuscope import store as store_mod
from mcuscope.store import Store
from tests.support import on_loop
from tests.test_export_lines_can import T0, _add, _can


def _plot_line(client, ts: float, value: float, port: str = "board") -> dict:
    store = client.app.state.store
    return on_loop(client, store.add_line(
        ts=ts, port=port, dir="rx", chan="event", seq=None, raw=f"!p v={value}",
        plot=[(1, None, "v", value)],
    ))


def _stamp(ts: float) -> str:
    return time.strftime("%Y%m%dT%H%M%S", time.localtime(ts))


# -- A-1: the until_ts ceiling is resolved once per request --------------------------


def test_every_page_of_an_export_reuses_one_until_ts_ceiling(client, monkeypatch) -> None:
    """The ceiling is an index walk the length of the window, and every 1000-row page
    re-derived it: 292.7 s against 11.3 s for the same bytes at 1M rows."""
    for i in range(7):
        _plot_line(client, T0 + i, float(i))
        _can(client, T0 + i, 0x100)
    monkeypatch.setattr(store_mod, "_EXPORT_PAGE", 2)
    calls: list[float] = []
    real = Store._window_id_ceiling

    def spy(self, until_ts, conn=None):
        calls.append(until_ts)
        return real(self, until_ts, conn)

    monkeypatch.setattr(Store, "_window_id_ceiling", spy)
    until = T0 + 4.5   # inside the capture: the walk, not the newest-row fast path
    cases = (
        ("/lines/export", {"format": "jsonl"}, 10),
        ("/can/frames", {"format": "csv"}, 5),
        ("/plot/export", {"names": "v", "format": "wide"}, 5),
        ("/lines", {"limit": 1000}, None),
    )
    for path, params, rows in cases:
        calls.clear()
        r = client.get(path, params={"until_ts": until, **params})
        assert r.status_code == 200, f"{path}: {r.text}"
        assert len(calls) == 1, f"{path} derived the ceiling {len(calls)} times"
        if rows is not None:
            body = [ln for ln in r.text.splitlines() if ln and not ln.startswith(("id,", "ts,"))]
            assert len(body) == rows, f"{path}: {r.text}"




def test_the_folded_ceiling_keeps_every_row_after_a_clock_step(client) -> None:
    """The fold must not undo D6: an `until_ts` above every stored ts is the whole capture."""
    for i in range(3):
        _add(client, ts=T0 + i, raw=f"before{i}")
    for i in range(2):
        _add(client, ts=T0 - 100 + i, raw=f"after{i}")
    wide = client.get("/lines/export", params={"until_ts": T0 + 1e6, "format": "jsonl"})
    assert [json.loads(ln)["raw"] for ln in wide.text.splitlines()] == [
        "before0", "before1", "before2", "after0", "after1",
    ]
    early = client.get("/lines", params={"until_ts": T0 - 100, "order": "asc"}).json()
    assert [x["raw"] for x in early["lines"]] == ["after0"]


# -- A-2: an internal freeze is not the caller's bound -------------------------------


def test_last_ms_on_a_quiet_capture_counts_back_from_now_on_every_export(client) -> None:
    """A board quiet for an hour exported that hour-old tail as "the last minute"."""
    old = time.time() - 3600
    for i in range(3):
        _add(client, ts=old + i, raw=f"old{i}")
        _plot_line(client, old + i, float(i))
        _can(client, old + i, 0x100)
    before = time.time()
    cases = (
        # `chan`: the daemon's own session-start row is recent.
        ("/lines/export", {"format": "csv", "chan": "debug"}, "id,ts,port,dir,chan,seq,raw"),
        ("/can/frames", {"format": "csv"}, "id,ts,tick_ms,bus,can_id,ext,rtr,dlc,data"),
        ("/plot/export", {"names": "v"}, "ts,tick_ms,sid,name,value"),
    )
    for path, params, header in cases:
        r = client.get(path, params={"last_ms": 60000, **params})
        assert r.status_code == 200, f"{path}: {r.text}"
        assert r.text.splitlines() == [header], f"{path} exported hour-old rows: {r.text}"
        lo, hi = _stamps(_disposition(r))
        assert hi == "end", _disposition(r)
        assert _stamp(before - 61) <= lo <= _stamp(time.time()), (path, lo)
    verdict = client.post("/assert", json={
        "forbid": ["old1"], "timeout_ms": 0, "last_ms": 60000, "chan": "debug",
    }).json()
    assert verdict["status"] == "empty" and verdict["checked_lines"] == 0, verdict


# -- A-9: a negative last_ms is refused, zero is a window ----------------------------


@pytest.mark.parametrize("path, extra", [
    ("/lines", {}), ("/lines/export", {}), ("/can/frames", {}),
    ("/plot/series", {"name": "v"}), ("/plot/export", {"names": "v"}),
])
def test_a_negative_last_ms_is_refused(client, path, extra) -> None:
    _plot_line(client, time.time(), 1.0)
    r = client.get(path, params={"last_ms": -60000, **extra})
    assert r.status_code == 422, f"{path}: {r.status_code} {r.text}"
    assert "last_ms" in r.text
    # A paused surface whose shown span rounds to 0 ms still gets its window.
    assert client.get(path, params={"last_ms": 0, **extra}).status_code == 200, path


# -- A-10: a window whose from is after its to ---------------------------------------


def _ended_session(client, name: str = "run") -> dict:
    store = client.app.state.store
    on_loop(client, store.start_session(name))
    _plot_line(client, time.time(), 1.0)
    _can(client, time.time(), 0x100)
    return on_loop(client, store.stop_session())


@pytest.mark.parametrize("path, extra", [
    ("/lines", {}), ("/lines/export", {}), ("/can/frames", {"format": "csv"}),
    ("/plot/export", {"names": "v"}),
])
def test_a_window_crossing_its_session_is_answered_and_names_no_backwards_file(
    client, path, extra
) -> None:
    """Session stamps and a `last_ms` floor are not row bounds, so crossing them is no
    refusal; an export's `<from>-<to>` then takes both sides from the upper bound."""
    session = _ended_session(client)
    start, end = session["started_ts"], session["ended_ts"]
    until = time.time() - 120
    crossings = (
        ({"session": "run", "until_ts": start - 1}, start - 1),
        ({"session": "run", "since_ts": end + 1}, end),
        ({"until_ts": until, "last_ms": 60000}, until),
    )
    for params, upper in crossings:
        r = client.get(path, params={**params, **extra})
        assert r.status_code == 200, f"{path} {params}: {r.status_code} {r.text}"
        if path != "/lines":
            assert _stamps(_disposition(r)) == (_stamp(upper), _stamp(upper)), (path, params)


def _plot(client, ts: float, name: str = "v", value: float = 1.0):
    """One stored line carrying one ad-hoc plot point, so `names=` has something to name."""
    store = client.app.state.store
    return on_loop(
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


# -- improvement 4: an unresolvable session= is refused, an empty one is not ------------


def test_a_session_that_holds_no_lines_still_answers_empty(client) -> None:
    """Without this the obvious over-correction - refusing every empty result - passes."""
    _plot(client, T0)   # `names=v` has to name a real channel (improvement 9)
    on_loop(client, client.app.state.store.start_session("quiet"))
    on_loop(client, client.app.state.store.stop_session())
    answers = {}
    for path, params in (
        ("/lines", {"session": "quiet"}),
        ("/lines/export", {"session": "quiet"}),
        ("/can/frames", {"session": "quiet"}),
        ("/plot/series", {"session": "quiet", "name": "v"}),
        ("/plot/export", {"session": "quiet", "names": "v"}),
    ):
        r = client.get(path, params=params)
        assert r.status_code == 200, f"{path}: {r.text}"
        answers[path] = r
    # Empty, not merely 200: the `v` line stored before the session must not leak in. The
    # session's own boundary markers are its only lines.
    assert {r["dir"] for r in answers["/lines"].json()["lines"]} == {"-"}
    assert "!p v=" not in answers["/lines/export"].text
    assert answers["/can/frames"].json()["frames"] == []
    assert answers["/plot/series"].json()["points"] == []
    assert answers["/plot/export"].text.splitlines() == ["ts,tick_ms,sid,name,value"]


# -- non-finite and out-of-range time bounds ----------------------------------------------


@pytest.mark.parametrize("path", ["/lines", "/lines/export", "/can/frames", "/plot/export"])
@pytest.mark.parametrize(("field", "value"), [("since_ts", "nan"), ("until_ts", "-inf")])
def test_a_non_finite_time_bound_is_refused_by_name(client, path, field, value) -> None:
    _add(client, ts=T0, raw="line0")
    _plot(client, T0)
    names = {"names": "v"} if path == "/plot/export" else {}   # undeclared elsewhere: a 422
    r = client.get(path, params={field: value, **names})
    assert r.status_code == 400, f"{path}: {r.status_code} {r.text[:80]}"
    assert r.json()["error"] == f"{field} must be a finite number"


def test_a_bound_past_the_platform_clock_still_exports_and_names_the_side(client) -> None:
    _add(client, ts=T0, raw="line0")
    r = client.get("/lines/export", params={"until_ts": "1e300"})
    assert r.status_code == 200, r.text
    assert _disposition(r).endswith('_start-out-of-range.txt"'), _disposition(r)


def test_export_bound_by_id_to_reanchors_its_last_ms_window(tmp_path) -> None:
    """A paused surface exports what it shows, window and all (finding M5).

    `/plot/export` resolved `last_ms` against *now*, so a chart paused on a transient
    exported a window that did not contain it, under a button whose tooltip says "the
    current window". `id_to` is the client's frozen line-id watermark; with one in force the
    window must end there too, because intersecting a frozen id range with a now-anchored
    window returns almost nothing.

    Asserted on the exported rows, not on the request: a test that checks the URL carries
    `id_to` is satisfied by wiring that filters nothing.
    """
    async def run() -> None:
        store = Store(":memory:")  # so the export streams on the loop connection the trace sees
        await store.start()
        try:
            now = time.time()
            # ids 1..10, one second apart, oldest first. id 5 is the "transient".
            for i in range(1, 11):
                fut = await store.submit_line(
                    ts=now - (10 - i), port="p", dir="rx", chan="event", seq=None,
                    raw=f"!p {i} v={i}",
                    plot=[(i, None, "v", float(i))],
                )
            await fut

            def ids(**kw):
                return [r["line_id"] for r in store.iter_plot_export(names=["v"], **kw)]

            # The freeze alone: everything up to and including the bound.
            assert ids(id_to=6) == [1, 2, 3, 4, 5, 6]
            # The freeze plus a window: 3.5 s ending at id 6, so 3..6 and never 7..10. The
            # half second keeps the floor off a row's exact ts, or the elapsed time between
            # seeding and querying would decide whether the boundary row is in (class 21).
            windowed = ids(id_to=6, last_ms=3500)
            assert windowed == [3, 4, 5, 6], windowed
            assert 5 in windowed, "the transient the surface was paused on is not in the export"
            assert not any(i > 6 for i in windowed), "rows past the freeze were exported"
            # Unbounded, the window is still measured from now, so the freeze changes nothing
            # for a live surface.
            assert ids(last_ms=3500) == [7, 8, 9, 10]
            assert ids() == list(range(1, 11))

            # /plot/series takes the same pair, and it is a separate call site of the
            # anchor (the coverage leg found this one uncovered while every other was hit).
            assert [p["line_id"] for p in store.query_plot_series(name="v", last_ms=3500)] \
                == [7, 8, 9, 10]
            assert [p["line_id"] for p in
                    store.query_plot_series(name="v", last_ms=3500, id_to=6)] == [3, 4, 5, 6]

            # Class 20: the bound must extend the existing index seek, not sit beside a
            # scan, and the anchor lookup must be a single primary-key seek. Asserted
            # positively (the index is named), because asserting the absence of "SCAN"
            # passes on any SQLite that words its plan differently.
            seen: list[str] = []
            store._conn.set_trace_callback(seen.append)
            list(store.iter_plot_export(names=["v"], id_to=6, last_ms=3500))
            store._conn.set_trace_callback(None)
            plans = [
                [str(r[3]) for r in store._conn.execute("EXPLAIN QUERY PLAN " + q)]
                for q in seen if q.lstrip().upper().startswith("SELECT")
            ]
            anchor = [p for p in plans if len(p) == 1 and "lines" in p[0]]
            assert anchor and "PRIMARY KEY" in anchor[0][0], f"anchor lookup is not a seek: {plans}"
            export = [p for p in plans if any("idx_plot_line" in r for r in p)]
            assert export, f"the export lost its index seek: {plans}"
            assert any("line_id<" in r or "line_id <" in r
                       for p in export for r in p), \
                f"id_to did not become part of the seek: {export}"
        finally:
            await store.stop()

    asyncio.run(run())
