"""The CLI and the daemon agree on a window: `--session` with `--last-ms`, and paged `--to`
walks, driven through `cli.main` against an in-process app (SPEC 3.4, SPEC 4)."""

from __future__ import annotations

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from mcuscope import cli
from mcuscope.store import Store
from tests.support import UNREACHABLE, canned, mk_app, on_loop
from tests.test_export_lines_can import _can


@pytest.fixture
def tc(tmp_path):
    with TestClient(mk_app(tmp_path), base_url="http://127.0.0.1") as c:
        yield c


def _add(tc, ts: float, raw: str = "x") -> dict:
    return on_loop(tc, tc.app.state.store.add_line(
        ts=ts, port="board", dir="rx", chan="debug", seq=None, raw=raw))


def _ended_session(tc, name: str = "old") -> dict:
    """A session over three lines with real stamps, ended just before the request."""
    store = tc.app.state.store
    on_loop(tc, store.start_session(name, ""))
    for i in range(3):
        _add(tc, time.time(), f"line{i}")
    session = on_loop(tc, store.stop_session())
    time.sleep(0.05)           # so a 10 ms `last_ms` counted back from now starts after it
    return session


def _forward(tc, monkeypatch) -> None:
    """Every CLI request of this invocation goes to the in-process app."""
    def handler(request: httpx.Request) -> httpx.Response:
        r = tc.request(request.method, request.url.path, params=request.url.params,
                       content=request.content, headers={"content-type": "application/json"})
        return httpx.Response(r.status_code, headers=r.headers, content=r.content)
    canned(monkeypatch, handler)


# -- FP-1: --session with --last-ms is the session's tail on every command --------------


def _cli_json(capsys, argv) -> list[dict]:
    """Rows a `--json` command printed: one object with `lines`/`frames`, or JSONL."""
    rc = cli.main([*UNREACHABLE, "--json", *argv])
    out = capsys.readouterr()
    assert rc == 0, out.err
    text = out.out.strip()
    if not text:
        return []
    if text.startswith("{\"lines\""):
        return json.loads(text)["lines"][::-1]          # newest first on the wire
    return [json.loads(ln) for ln in text.splitlines()]


def _raws(rows) -> list[str]:
    return [r["raw"] for r in rows]


def _tail_session(tc, running: bool = False) -> None:
    """`early`, then 150 ms later `late`, each with a CAN frame and a plot sample."""
    store = tc.app.state.store
    on_loop(tc, store.start_session("old", ""))
    for raw, value in (("early", 1.0), ("late", 2.0)):
        if raw == "late":
            time.sleep(0.15)
        _add(tc, time.time(), raw)
        _can(tc, time.time(), 0x100 + int(value))
        on_loop(tc, store.add_line(ts=time.time(), port="board", dir="rx", chan="event",
                                    seq=None, raw=f"!p v={value}", plot=[(1, None, "v", value)]))
    if not running:
        on_loop(tc, store.stop_session())
    time.sleep(0.2)            # a window counted back from now holds none of it


LINE_FORMS = [
    ["lines", "--limit", "100"],
    ["lines", "--limit", "7"],                       # pages of 5: the id_to walk
    ["log", "export"],                               # /lines/export
    ["log", "export", "--limit", "100"],             # paged /lines
    ["log", "export", "--names", "v"],               # the ascending walk
]


@pytest.mark.parametrize("form", LINE_FORMS, ids=" ".join)
@pytest.mark.parametrize("last_ms", [100, 0])
def test_session_with_last_ms_on_the_cli_is_the_daemons_tail(tc, monkeypatch, capsys, form,
                                                             last_ms) -> None:
    """SPEC 3.4: an ended session is an upper bound, so `last_ms` counts back from its
    newest line; 0 keeps that line, as the daemon's inclusive floor does."""
    _tail_session(tc)
    _forward(tc, monkeypatch)
    monkeypatch.setattr(cli, "LINES_PAGE", 5)
    window = {"session": "old", "last_ms": last_ms}
    truth = tc.get("/lines", params={**window, "order": "asc", "limit": 1000}).json()["lines"]
    assert "late" in _raws(truth) and "early" not in _raws(truth) or last_ms == 0, truth
    got = _cli_json(capsys, [*form, "--session", "old", "--last-ms", str(last_ms)])
    if "--names" in form:
        # Decoding renders only samples; the ids still say which rows the window held.
        assert {r["id"] for r in got} <= {r["id"] for r in truth}, got
        assert got or last_ms == 0, "the tail holds a sample"
    else:
        assert [r["id"] for r in got] == [r["id"] for r in truth]


def test_can_dump_and_plot_export_agree_on_the_sessions_tail(tc, monkeypatch, capsys) -> None:
    _tail_session(tc)
    _forward(tc, monkeypatch)
    bounds = ["--session", "old", "--last-ms", "100"]
    lines = _raws(_cli_json(capsys, ["lines", *bounds]))
    assert "late" in lines and "early" not in lines, lines
    frames = _cli_json(capsys, ["can", "dump", *bounds])
    assert [f["can_id"] for f in frames] == [0x102], frames
    rc = cli.main([*UNREACHABLE, "plot", "export", "--names", "v", *bounds])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert [ln.rsplit(",", 1)[-1] for ln in out.out.splitlines()[1:]] == ["2.0"], out.out


@pytest.mark.parametrize("form", LINE_FORMS[:3], ids=" ".join)
def test_a_running_sessions_last_ms_still_counts_back_from_now(tc, monkeypatch, capsys,
                                                               form) -> None:
    _tail_session(tc, running=True)
    _forward(tc, monkeypatch)
    truth = tc.get("/lines", params={"session": "old", "last_ms": 100}).json()["lines"]
    assert truth == []
    assert _cli_json(capsys, [*form, "--session", "old", "--last-ms", "100"]) == []


# -- FP-3: a purged session's last_ms window is empty, not "after the session" ---------


def test_a_purged_sessions_last_ms_window_is_an_empty_answer(tc) -> None:
    """The floor falls back to now when no row is at or below the session's end."""
    _ended_session(tc)
    store = tc.app.state.store

    async def purge():
        store._conn.execute("DELETE FROM lines")
        store._conn.commit()
    on_loop(tc, purge())
    window = {"session": "old", "last_ms": 10}
    for path, extra, field in (("/lines", {}, "lines"), ("/can/frames", {}, "frames")):
        r = tc.get(path, params={**window, **extra})
        assert r.status_code == 200, f"{path}: {r.text}"
        assert r.json()[field] == [], path
    for path, extra in (("/lines/export", {"format": "jsonl"}),
                        ("/can/frames", {"format": "csv"})):
        r = tc.get(path, params={**window, **extra})
        assert r.status_code == 200, f"{path}: {r.text}"
    r = tc.post("/assert", json={"forbid": ["x"], **window})
    assert r.status_code == 200, r.text
    assert r.json()["checked_lines"] == 0, r.text


# -- FP-4: a burst stamped before its session started is still in the window ----------


def test_a_row_stamped_before_its_session_start_is_answered_by_until_ts(tc) -> None:
    """The reader stamps a burst before the loop ingests it, so an id inside the session
    can carry a ts before `started_ts`."""
    store = tc.app.state.store
    # A whole second back, not just "before the next call": time.time() is 15 ms coarse on
    # Windows, where the stamp tied with started_ts and the premise went untested.
    stamped = time.time() - 1.0
    session = on_loop(tc, store.start_session("run", ""))
    row = _add(tc, stamped, "burst")
    assert row["id"] > session["start_id"] and stamped < session["started_ts"]
    window = {"session": "run", "until_ts": stamped}
    r = tc.get("/lines", params=window)
    assert r.status_code == 200, r.text
    assert [x["raw"] for x in r.json()["lines"]] == ["burst"]
    r = tc.get("/lines/export", params={**window, "format": "text"})
    assert r.status_code == 200, r.text
    assert "burst" in r.text


# -- FP-2: a paged --to walk derives the until_ts ceiling once ---------------------------


def _fill(tc, stamps) -> list[dict]:
    async def run():
        return [await tc.app.state.store.add_line(
            ts=ts, port="board", dir="rx", chan="debug", seq=None, raw=f"r{i}")
            for i, ts in enumerate(stamps)]
    return on_loop(tc, run())


def _count_ceiling_walks(monkeypatch) -> list[float]:
    calls: list[float] = []
    real = Store._window_id_ceiling

    def spy(self, until_ts, conn=None):
        calls.append(until_ts)
        return real(self, until_ts, conn)
    monkeypatch.setattr(Store, "_window_id_ceiling", spy)
    return calls


def _clock(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


PAGED_TO_FORMS = [
    ["log", "export", "--names", "r"],     # the ascending walk (since_id pages)
    ["log", "export", "--limit", "40"],    # the descending walk (id_to pages)
    ["lines", "--limit", "40"],
]


@pytest.mark.parametrize("form", PAGED_TO_FORMS, ids=" ".join)
def test_a_paged_to_walk_runs_the_ceiling_walk_once(tc, monkeypatch, capsys, form) -> None:
    base = float(int(time.time()) - 1000)
    _fill(tc, [base + i for i in range(60)])
    _forward(tc, monkeypatch)
    monkeypatch.setattr(cli, "LINES_PAGE", 5)
    calls = _count_ceiling_walks(monkeypatch)
    until = base + 45                           # inside the capture: the walk, not the fast path
    rc = cli.main([*UNREACHABLE, "--json", *form, "--to", _clock(until)])
    assert rc == 0, capsys.readouterr().err
    assert calls == [until], f"{len(calls)} ceiling walks for one window"


def test_the_ceiling_walk_is_skipped_only_when_the_bound_row_is_inside_until_ts(tc,
                                                                               monkeypatch) -> None:
    base = time.time() - 1000
    rows = _fill(tc, [base, base + 1, base + 50, base + 2, base + 60])  # a step back at row 4
    calls = _count_ceiling_walks(monkeypatch)
    until = base + 5
    for id_to, walks in ((rows[3]["id"], 0), (rows[1]["id"], 0), (rows[4]["id"], 1),
                         (rows[2]["id"], 1), (0, 0)):
        calls.clear()
        r = tc.get("/lines", params={"until_ts": until, "id_to": id_to, "order": "asc"})
        assert r.status_code == 200, r.text
        assert len(calls) == walks, (id_to, calls)
        want = [x["id"] for x in rows[:4] if x["ts"] <= until and x["id"] <= id_to]
        assert [x["id"] for x in r.json()["lines"] if x["raw"].startswith("r")] == want


def test_a_to_before_every_row_still_asks_the_daemon(tc, monkeypatch, capsys) -> None:
    """An empty ceiling probe still sends the page: the daemon's refusals come from it."""
    _fill(tc, [time.time()])
    _forward(tc, monkeypatch)
    to = ["--to", _clock(time.time() - 5000)]
    rc = cli.main([*UNREACHABLE, "log", "export", "--names", "r", "--session", "nope", *to])
    assert rc == 1
    assert "no such session: nope" in capsys.readouterr().err
    assert _cli_json(capsys, ["log", "export", "--names", "r", *to]) == []


@pytest.mark.parametrize("form", PAGED_TO_FORMS, ids=" ".join)
def test_a_paged_to_walk_matches_one_request_across_a_clock_step(tc, monkeypatch, capsys,
                                                                 form) -> None:
    """Rows below the ceiling stamped after --to (a backwards step) stay out of every page."""
    base = float(int(time.time()) - 1000)
    stamps = [base + i for i in range(20)] + [base + 3 + i / 10 for i in range(20)]
    stamps += [base + 30 + i for i in range(5)]
    _fill(tc, stamps)
    _forward(tc, monkeypatch)
    monkeypatch.setattr(cli, "LINES_PAGE", 5)
    until = base + 10
    truth = tc.get("/lines", params={"until_ts": until, "order": "asc", "limit": 1000})
    want = [x["id"] for x in truth.json()["lines"]]
    assert len(want) == 11 + 20, want
    got = [r["id"] for r in _cli_json(capsys, [*form, "--to", _clock(until)])]
    assert got == want
