"""Fix-round diff review, Python (FP-1..FP-8): crossings of session stamps answered rather
than refused, the CLI's `--last-ms` anchor and paged `--to` walks, and its session export,
version gate and partial-file removal.

Daemon cases use `test_export_lines_can`'s file-backed app; CLI cases run in process, over
that app forwarded through a MockTransport or over a canned transport.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from mcuscope import cli
from mcuscope.cli_output import remove_partial
from mcuscope.store import Store
from tests.test_cli_r2026_09_12 import STATUS, UNREACHABLE, canned, paths, recorder
from tests.test_export_lines_can import _can, _mk_app

OLD = {**STATUS, "version": "0.3.0"}


def _on_loop(tc, coro):
    return asyncio.run_coroutine_threadsafe(coro, tc.app.state.ports._loop).result(10)


@pytest.fixture
def tc(tmp_path):
    with TestClient(_mk_app(tmp_path), base_url="http://127.0.0.1") as c:
        yield c


def _add(tc, ts: float, raw: str = "x") -> dict:
    return _on_loop(tc, tc.app.state.store.add_line(
        ts=ts, port="board", dir="rx", chan="debug", seq=None, raw=raw))


def _ended_session(tc, name: str = "old") -> dict:
    """A session over three lines with real stamps, ended just before the request."""
    store = tc.app.state.store
    _on_loop(tc, store.start_session(name, ""))
    for i in range(3):
        _add(tc, time.time(), f"line{i}")
    session = _on_loop(tc, store.stop_session())
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
    _on_loop(tc, store.start_session("old", ""))
    for raw, value in (("early", 1.0), ("late", 2.0)):
        if raw == "late":
            time.sleep(0.15)
        _add(tc, time.time(), raw)
        _can(tc, time.time(), 0x100 + int(value))
        _on_loop(tc, store.add_line(ts=time.time(), port="board", dir="rx", chan="event",
                                    seq=None, raw=f"!p v={value}", plot=[(1, None, "v", value)]))
    if not running:
        _on_loop(tc, store.stop_session())
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
    _on_loop(tc, purge())
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
    session = _on_loop(tc, store.start_session("run", ""))
    row = _add(tc, stamped, "burst")
    assert row["id"] > session["start_id"] and stamped < session["started_ts"]
    window = {"session": "run", "until_ts": stamped}
    r = tc.get("/lines", params=window)
    assert r.status_code == 200, r.text
    assert [x["raw"] for x in r.json()["lines"]] == ["burst"]
    r = tc.get("/lines/export", params={**window, "format": "text"})
    assert r.status_code == 200, r.text
    assert "burst" in r.text


# -- FP-5: -o NAME beside a NAME/ directory writes NAME.zip -----------------------------


def test_bundle_beside_a_directory_of_the_same_name_writes_the_zip(monkeypatch, capsys,
                                                                    tmp_path) -> None:
    (tmp_path / "run-3").mkdir()
    seen = recorder(monkeypatch, sessions={"sessions": [{"id": 1, "name": "run-3"}]},
                    sessions_1_bundle="PK")
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["session", "export", "run-3", "--bundle", "-o", "run-3", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert paths(seen) == ["/sessions", "/sessions/1/bundle"], paths(seen)
    assert (tmp_path / "run-3.zip").read_bytes() == b"PK"
    assert (tmp_path / "run-3").is_dir() and not list((tmp_path / "run-3").iterdir())


def test_a_bundle_target_ending_in_a_separator_is_refused_before_the_suffix(
    monkeypatch, capsys, tmp_path
) -> None:
    seen = recorder(monkeypatch)
    rc = cli.main(["session", "export", "1", "--bundle", "-o", str(tmp_path / "x") + os.sep,
                   *UNREACHABLE])
    assert rc == 1
    assert "is a directory" in capsys.readouterr().err
    assert seen == [] and list(tmp_path.iterdir()) == []


# -- FP-6: one GET /status per command ---------------------------------------------------


GATES = [
    (["plot", "export", "--names", "v", "--from", "10:00", "--decode", "-o", "p.csv"],
     "ignores --from/--to/--decode ("),
    (["-p", "board", "plot", "export", "--names", "v", "--from", "10:00", "-o", "q.csv"],
     "ignores --from/--to/-p ("),
    (["can", "dump", "--csv", "--from", "10:00", "-o", "c.csv"], "ignores --from/--to/--csv ("),
    (["can", "dump", "--csv", "-o", "c.csv"], "ignores --csv ("),
    (["plot", "export", "--names", "v", "--decode", "-o", "p.csv"], "ignores --decode ("),
    (["plot", "export", "--names", "v", "--decode", "--changes", "--deadband", "v=1", "-o",
      "p.csv"], "ignores --decode/--changes/--deadband ("),
]


def test_the_gate_lists_name_every_option_the_cli_gates() -> None:
    """Every flag literal cli.py hands the version gate is named by some gate test's message."""
    import ast

    from tests.test_cli_r2026_09_12 import BOUNDED
    from tests.test_prerelease_cli_fixes import GATED

    with open(cli.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    derived = set()
    for node in ast.walk(tree):
        call = getattr(getattr(node, "func", None), "attr", None) or getattr(
            getattr(node, "func", None), "id", None)
        on_gated = getattr(getattr(getattr(node, "func", None), "value", None), "id", None)
        if call in ("require_daemon", "_clock_bounds") or on_gated == "gated":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                        and sub.value.startswith("-"):
                    derived.update(sub.value.split("/"))
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "gated"
                                                for t in node.targets):
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                        and sub.value.startswith("-"):
                    derived.add(sub.value)
    assert len(derived) >= 8, derived
    named = " ".join(msg for _, msg in [*GATES, *GATED]) + (" --from/--to" if BOUNDED else "")
    missing = sorted(flag for flag in derived if flag not in named)
    assert not missing, f"gated in cli.py, named by no gate test: {missing}"


@pytest.mark.parametrize(("argv", "named"), GATES, ids=lambda v: " ".join(v)
                         if isinstance(v, list) else None)
def test_every_gated_option_is_judged_by_one_status_request(monkeypatch, capsys, tmp_path,
                                                            argv, named) -> None:
    monkeypatch.chdir(tmp_path)
    seen = recorder(monkeypatch, status=OLD)
    rc = cli.main([*argv, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert named in err, err
    assert paths(seen) == ["/status"], paths(seen)
    seen = recorder(monkeypatch, plot_export="ts,name,value\n", can_frames="id\n",
                    plot_channels={"channels": []})
    rc = cli.main([*argv, *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert paths(seen).count("/status") == 1, paths(seen)


# -- FP-7: a failed export through a symlink leaves no partial bytes behind it -----------


def _dying(*chunks: bytes):
    def gen():
        yield from chunks
        raise httpx.ReadError("peer died")
    return gen()


@pytest.mark.parametrize("argv", [["log", "export", "--csv"], ["session", "export", "run"]])
def test_a_dead_stream_through_a_symlink_removes_the_file_it_resolves_to(
    monkeypatch, capsys, tmp_path, argv
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions":
            return httpx.Response(200, json={"sessions": [{"id": 1, "name": "run"}]})
        return httpx.Response(200, content=_dying(b"id,ts\n1,2\n"))
    canned(monkeypatch, handler)
    target = tmp_path / "target.csv"
    target.write_text("yesterday's complete export\n", encoding="utf-8")
    hop = tmp_path / "hop.csv"
    hop.symlink_to(target)
    link = tmp_path / "latest.csv"
    link.symlink_to(hop)                       # a chain resolves to the same regular file
    rc = cli.main([*argv, "-o", str(link), *UNREACHABLE])
    assert rc == 3, capsys.readouterr().err
    assert link.is_symlink() and hop.is_symlink(), "a link is not the partial file"
    assert not target.exists(), "the partial bytes read like a whole export"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO")
def test_a_symlink_to_a_fifo_keeps_both(tmp_path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    link = tmp_path / "link"
    link.symlink_to(fifo)
    remove_partial(str(link))
    remove_partial(str(fifo))
    assert link.is_symlink() and fifo.exists()
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "gone")
    remove_partial(str(dangling))              # nothing to remove, and no error
    assert dangling.is_symlink()


# -- FP-8: a daemon ignoring ?name= still exports a session past its page ----------------


@pytest.mark.parametrize("bundle", [[], ["--bundle"]])
def test_a_page_without_the_name_falls_back_to_the_quoted_path(monkeypatch, capsys, tmp_path,
                                                                bundle) -> None:
    name = "run 1?#x"
    page = {"sessions": [{"id": i, "name": f"s{i}"} for i in range(60, 10, -1)]}
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/sessions":
            return httpx.Response(200, json=page)
        return httpx.Response(200, content=b"SQLite")
    canned(monkeypatch, handler)
    out = tmp_path / ("s.zip" if bundle else "s.db")
    rc = cli.main(["session", "export", name, *bundle, "-o", str(out), *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    kind = "bundle" if bundle else "export"
    assert [r.url.raw_path for r in seen] == [
        b"/sessions?name=run+1%3F%23x", f"/sessions/run%201%3F%23x/{kind}".encode()
    ], [r.url.raw_path for r in seen]
    assert out.read_bytes() == b"SQLite"


def test_a_page_holding_the_name_exports_that_row_by_id(monkeypatch, capsys, tmp_path) -> None:
    """The newest row is never taken for the name: only an exact name or id matches."""
    page = {"sessions": [{"id": 9, "name": "old-run-2"}, {"id": 3, "name": "old-run"}]}
    seen = recorder(monkeypatch, sessions=page, sessions_3_export="SQLite")
    rc = cli.main(["session", "export", "old-run", "-o", str(tmp_path / "s.db"), *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert paths(seen) == ["/sessions", "/sessions/3/export"], paths(seen)


def test_a_fallback_to_a_session_the_daemon_lacks_downloads_nothing(monkeypatch, capsys,
                                                                    tmp_path) -> None:
    seen = recorder(monkeypatch, sessions={"sessions": [{"id": 1, "name": "other"}]},
                    sessions_nope_export=(400, {"error": "no such session: nope"}))
    out = tmp_path / "s.db"
    rc = cli.main(["session", "export", "nope", "-o", str(out), *UNREACHABLE])
    assert rc == 1
    assert "no such session: nope" in capsys.readouterr().err
    assert paths(seen) == ["/sessions", "/sessions/nope/export"] and not out.exists()


# -- FP-2: a paged --to walk derives the until_ts ceiling once ---------------------------


def _fill(tc, stamps) -> list[dict]:
    async def run():
        return [await tc.app.state.store.add_line(
            ts=ts, port="board", dir="rx", chan="debug", seq=None, raw=f"r{i}")
            for i, ts in enumerate(stamps)]
    return _on_loop(tc, run())


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
