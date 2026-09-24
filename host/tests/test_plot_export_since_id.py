"""`since_id` on /plot/export (SPEC 3.4, 9.2): the exclusive id cursor /lines already takes,
which a paused chart sends because the samples of one serial burst share one timestamp."""

from __future__ import annotations

import csv
import io
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from mcuscope.server import MAX_LINE_ID
from tests.support import mk_app, on_loop
from tests.test_export_lines_can import T0, _add, _can
from tests.test_plot_export_decode import feed


def _plot(client, ts: float, tick: int, name: str = "v", sid: int | None = None) -> int:
    store = client.app.state.store
    raw = f"!p {tick} {name}={tick}"
    row = on_loop(client, store.add_line(ts=ts, port="board", dir="rx", chan="event", seq=None,
                                          raw=raw, plot=[(tick, sid, name, float(tick))]))
    return row["id"]


def _ticks(r) -> list[int]:
    assert r.status_code == 200, r.text
    return [int(row["tick_ms"]) for row in csv.DictReader(io.StringIO(r.text))]


@pytest.fixture
def client(tmp_path):
    with TestClient(mk_app(tmp_path), base_url="http://127.0.0.1") as c:
        # T0 is years behind the lifespan's own rows, so the store announces the stamp
        # inversion in a sys row after the first T0 row: let that be this one, ahead of
        # every id range a test exports.
        _add(c, ts=T0, raw="first row behind the daemon's own stamps")
        yield c


def _export(client, **params):
    return client.get("/plot/export", params={"names": "v", **params})


def test_since_id_splits_a_burst_that_one_timestamp_cannot(client) -> None:
    ids = [_plot(client, T0, 1), _plot(client, T0 + 1, 2), _plot(client, T0 + 1, 3),
           _plot(client, T0 + 1, 4), _plot(client, T0 + 2, 5)]
    # Positive control: the burst (ticks 2..4) is inseparable by time.
    assert _ticks(_export(client, since_ts=T0 + 0.5, until_ts=T0 + 1)) == [2, 3, 4]
    assert _ticks(_export(client, since_id=ids[1], id_to=ids[2])) == [3]
    assert _ticks(_export(client, since_id=ids[0])) == [2, 3, 4, 5], "exclusive, not inclusive"
    assert _ticks(_export(client, since_id=ids[4])) == []


@pytest.mark.parametrize("since_id", [-1, -(2**63), 0])
def test_a_since_id_below_every_row_exports_everything(client, since_id) -> None:
    """As /lines treats it: a cursor before the first row, not a refusal."""
    for tick in (1, 2):
        _plot(client, T0 + tick, tick)
    assert _ticks(_export(client, since_id=since_id)) == [1, 2]


@pytest.mark.parametrize("bad", ["x", "1.5", "", "0x10", str(2**63)])
def test_a_since_id_that_is_not_a_line_id_is_refused_naming_the_field(client, bad) -> None:
    _plot(client, T0, 1)
    r = _export(client, since_id=bad)
    assert r.status_code == 422, r.text
    assert r.json()["error"].startswith("since_id: "), r.json()


def test_since_id_at_the_last_storable_id_is_an_empty_export_not_a_500(client) -> None:
    """`since_id + 1` is past the SQLite integer range; the lower bound must still bind."""
    _plot(client, T0, 1)
    r = _export(client, since_id=MAX_LINE_ID)
    assert _ticks(r) == []
    assert r.text.startswith("ts,"), "a header-only 200"


def test_since_id_at_or_above_id_to_is_an_empty_export(client) -> None:
    ids = [_plot(client, T0 + t, t) for t in (1, 2, 3)]
    assert _ticks(_export(client, since_id=ids[1], id_to=ids[1])) == []
    assert _ticks(_export(client, since_id=ids[2], id_to=ids[0])) == []
    assert _ticks(_export(client, since_id=ids[0], id_to=ids[1])) == [2], "positive control"


def test_since_id_intersects_a_session_whichever_is_tighter(client) -> None:
    before = _plot(client, T0 + 1, 1)
    _plot(client, T0 + 2, 2)
    session = on_loop(client, client.app.state.store.start_session("run-a"))
    inside = [_plot(client, T0 + t, t) for t in (3, 4, 5)]
    # The session starts later than the cursor: the session wins.
    assert _ticks(_export(client, session="run-a", since_id=before)) == [3, 4, 5]
    # The cursor sits inside the session: the cursor wins.
    assert _ticks(_export(client, session="run-a", since_id=inside[0])) == [4, 5]
    assert before + 1 < session["start_id"] <= inside[0], "the fixture must separate the bounds"


def test_since_id_intersects_since_ts_in_both_orders(client) -> None:
    ids = [_plot(client, T0 + t, t) for t in (1, 2, 3, 4)]
    assert _ticks(_export(client, since_id=ids[0], since_ts=T0 + 2.5)) == [3, 4], "ts is tighter"
    assert _ticks(_export(client, since_id=ids[2], since_ts=T0 + 0.5)) == [4], "id is tighter"


def test_a_wide_export_judges_its_one_stream_after_the_since_id(client) -> None:
    """The stream check reads the same scope: rows before the cursor must not count."""
    first = _plot(client, T0 + 1, 1, sid=1)
    _plot(client, T0 + 2, 2, sid=0)
    r = _export(client, format="wide")
    assert r.status_code == 400 and "one stream" in r.json()["error"], "positive control"
    assert _ticks(_export(client, format="wide", since_id=first)) == [2]


def test_since_id_intersects_last_ms_anchored_on_id_to_not_on_the_cursor(client) -> None:
    ids = [_plot(client, T0 + t, t) for t in (1, 2, 3, 4, 5)]
    # last_ms counts back from id_to's row (T0 + 4), so its floor is T0 + 2.5.
    assert _ticks(_export(client, id_to=ids[3], last_ms=1500)) == [3, 4], "positive control"
    assert _ticks(_export(client, since_id=ids[0], id_to=ids[3], last_ms=1500)) == [3, 4]
    assert _ticks(_export(client, since_id=ids[2], id_to=ids[3], last_ms=1500)) == [4], "id tighter"


def test_since_id_intersects_until_ts(client) -> None:
    ids = [_plot(client, T0 + t, t) for t in (1, 2, 3, 4)]
    assert _ticks(_export(client, since_id=ids[0], until_ts=T0 + 3)) == [2, 3]
    assert _ticks(_export(client, since_id=ids[2], until_ts=T0 + 3)) == []
    assert _ticks(_export(client, since_id=ids[1], until_ts=T0 + 4)) == [3, 4], "positive control"


def _stamp(ts: float) -> str:
    return time.strftime("%Y%m%dT%H%M%S", time.localtime(ts))


def _name(r) -> str:
    assert r.status_code == 200, r.text
    return r.headers["content-disposition"].split('filename="')[1].rstrip('"')


def test_an_id_bounded_plot_export_is_named_from_its_first_and_last_lines(client) -> None:
    ids = [_plot(client, T0 + 10 * t, t) for t in (1, 2, 3, 4, 5)]
    name = _name(_export(client, since_id=ids[1], id_to=ids[3]))
    assert name == f"capture_plot_{_stamp(T0 + 30)}-{_stamp(T0 + 40)}.csv"
    # No id_to: the export freezes at the newest line, and that names `to`.
    name = _name(_export(client, since_id=ids[3]))
    assert name == f"capture_plot_{_stamp(T0 + 50)}-{_stamp(T0 + 50)}.csv"
    # A time bound tighter than the first line still names `from`.
    name = _name(_export(client, since_id=ids[0], since_ts=T0 + 25, id_to=ids[3]))
    assert name == f"capture_plot_{_stamp(T0 + 25)}-{_stamp(T0 + 40)}.csv"
    # ...and one past the last line names both sides from `to`, never backwards.
    name = _name(_export(client, since_id=ids[0], since_ts=T0 + 45, id_to=ids[3]))
    assert name == f"capture_plot_{_stamp(T0 + 40)}-{_stamp(T0 + 40)}.csv"
    # An empty id range keeps the time-bound name.
    assert _name(_export(client, since_id=ids[4])) == "capture_plot_start-end.csv"
    assert _name(_export(client, id_to=ids[3])) == "capture_plot_start-end.csv", "id_to alone"


def test_id_bounded_lines_and_can_exports_are_named_from_their_lines(client) -> None:
    rows = [_add(client, ts=T0 + 10 * t, raw=f"line{t}") for t in (1, 2, 3)]
    rows += [_can(client, T0 + 40, 0x100), _can(client, T0 + 50, 0x100)]
    r = client.get("/lines/export", params={"since_id": rows[0]["id"], "id_to": rows[2]["id"]})
    assert _name(r) == f"capture_lines_{_stamp(T0 + 20)}-{_stamp(T0 + 30)}.txt"
    r = client.get("/can/frames", params={"format": "csv", "since_id": rows[2]["id"],
                                          "id_to": rows[3]["id"]})
    assert _name(r) == f"capture_can_{_stamp(T0 + 40)}-{_stamp(T0 + 40)}.csv"
    assert r.text.count("\n") == 2, "the header and one frame"


def test_decode_anchors_at_the_first_row_after_the_since_id(make_stack) -> None:
    """A redefinition between an earlier row and the cursor is the one in force: the wide
    header takes its labels from the definition primed at the export's first row."""
    stack = make_stack()
    feed(stack, "!pd 3 io:u1:/led", "!ps 3 1 01", "!pd 3 ob:u1:/led", "!ps 3 2 01")
    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        cursor = c.get("/lines", params={"match": "^!ps 3 1 "}).json()["lines"][0]["id"]
        whole = c.get("/plot/export", params={"names": "led", "format": "wide", "decode": 1})
        assert whole.text.splitlines()[0] == "ts,tick_ms,io.led", "positive control"
        r = c.get("/plot/export", params={"names": "led", "format": "wide", "decode": 1,
                                          "since_id": cursor})
    assert r.status_code == 200, r.text
    assert r.text.splitlines()[0] == "ts,tick_ms,ob.led"
    assert [line.split(",")[1:] for line in r.text.splitlines()[1:]] == [["2", "1"]]
