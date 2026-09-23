"""The `[port]` column for a detached board's history (SPEC 3.4 `/lines/export`, SPEC 4).

Text spanning every port carries `[port]` when the attached ports and those with stored rows
together name more than one. Judged on the attached ports alone, a board detached before the
export left its rows unattributed among the other board's, in the daemon's text, the CLI's and
a bundle's.
"""

from __future__ import annotations

import asyncio
import io
import re
import subprocess
import threading
import time
import zipfile
from types import SimpleNamespace

import httpx
import pytest

from mcuscope import cli, server
from mcuscope.cli_client import Settings
from mcuscope.store import Store
from tests.support import CHILD_TEXT, Stack, child_env
from tests.test_cli import MCU, run_mcu, run_mcu_canned

COLUMN = re.compile(r"^\d\d:\d\d:\d\d\.\d{3} \[", re.M)   # a text row with the column


def _on_loop(stack: Stack, coro, timeout: float = 10.0):
    return asyncio.run_coroutine_threadsafe(coro, stack.app.state.ports._loop).result(timeout)


def _store_detached_rows(stack: Stack) -> None:
    """Rows from `b2`, a board that is no longer attached."""
    store = stack.app.state.store
    for i in range(3):
        _on_loop(stack, store.add_line(ts=time.time(), port="b2", dir="rx", chan="debug",
                                       seq=None, raw=f"from b2 {i}"))


# -- the store -------------------------------------------------------------------------------


async def test_stored_ports_seeks_along_the_port_index(tmp_path) -> None:
    store = Store(str(tmp_path / "p.db"))
    await store.start()
    try:
        assert store.stored_ports() == []
        for port in ("busy",) * 20 + ("quiet", "", "aux"):
            await store.add_line(ts=time.time(), port=port, dir="rx", chan="debug", seq=None,
                                 raw="x")
        seen: list[str] = []
        store._conn.set_trace_callback(seen.append)
        try:
            assert store.stored_ports() == ["aux", "busy", "quiet"]   # "" is the daemon's
        finally:
            store._conn.set_trace_callback(None)
        plan = [str(r[3]) for r in store._conn.execute("EXPLAIN QUERY PLAN " + seen[-1])]
        on_lines = [step for step in plan if " lines " in f" {step} "]
        assert len(on_lines) == 2, plan
        assert all(s.startswith("SEARCH lines USING COVERING INDEX idx_lines_port_id")
                   for s in on_lines), plan
    finally:
        await store.stop()


# -- the daemon ------------------------------------------------------------------------------


def _several(attached: list[str], stored: list[str]) -> bool:
    ports = [SimpleNamespace(alias=a) for a in attached]
    state = SimpleNamespace(ports=SimpleNamespace(list=lambda: ports),
                            store=SimpleNamespace(stored_ports=lambda: stored))
    return server._several_ports(SimpleNamespace(app=SimpleNamespace(state=state)))


def test_the_column_rule_counts_attached_and_stored_ports_as_one_set() -> None:
    assert _several(["a", "b"], [])             # two quiet boards, nothing stored yet
    assert _several(["a"], ["a", "b"])          # a detached board's history
    assert _several([], ["a", "b"])
    assert _several(["b"], ["a"])               # a detached with history, b new and quiet
    assert not _several(["a"], ["a"])           # one board, attached and stored
    assert not _several(["a"], [])
    assert not _several([], [])


def test_a_detached_boards_history_carries_the_port_in_the_daemons_text(stack: Stack) -> None:
    _store_detached_rows(stack)
    with httpx.Client(base_url=stack.base_url, timeout=20.0) as c:
        listed = c.get("/ports").json()
        assert [p["alias"] for p in listed["ports"]] == ["board"]
        assert listed["stored"] == ["b2", "board"]
        text = c.get("/lines/export", params={"format": "text"}).text
        assert re.search(r"^\S+ \[b2\]  debug\| from b2 0$", text, re.M), text
        assert re.search(r"^\S+ \[board\] ", text, re.M), text
        scoped = c.get("/lines/export", params={"format": "text", "port": "b2"}).text
        assert "from b2 0" in scoped and not COLUMN.search(scoped), scoped
        sid = c.get("/status").json()["session"]["id"]
        r = c.get(f"/sessions/{sid}/bundle")
        assert r.status_code == 200, r.text
        lines_txt = zipfile.ZipFile(io.BytesIO(r.content)).read("lines.txt").decode()
        assert re.search(r"^\S+ \[b2\]  debug\| from b2 0$", lines_txt, re.M), lines_txt


def test_a_single_board_capture_has_no_port_column(stack: Stack) -> None:
    """Positive control: one port attached and stored, so no column anywhere."""
    with httpx.Client(base_url=stack.base_url, timeout=20.0) as c:
        c.post("/marker", json={"text": "a row to export"})
        assert c.get("/ports").json()["stored"] == ["board"]
        text = c.get("/lines/export", params={"format": "text"}).text
        assert "a row to export" in text and not COLUMN.search(text), text
    r = run_mcu(stack, "log", "export")
    assert r.returncode == 0, r.stderr
    assert "a row to export" in r.stdout and not COLUMN.search(r.stdout), r.stdout


# -- the CLI, end to end ---------------------------------------------------------------------


def test_a_detached_boards_history_carries_the_port_in_every_cli_text_read(
    stack: Stack, tmp_path,
) -> None:
    _store_detached_rows(stack)
    r = run_mcu(stack, "log", "export")
    assert r.returncode == 0, r.stderr
    assert re.search(r"^\S+ \[b2\]  debug\| from b2 0$", r.stdout, re.M), r.stdout
    out = tmp_path / "run.txt"
    r = run_mcu(stack, "log", "export", "-o", str(out))
    assert r.returncode == 0, r.stderr
    assert re.search(rb"^\S+ \[b2\]  debug\| from b2 0$", out.read_bytes(), re.M)

    # tail -f's backfill: the stream is judged before the snapshot prints.
    env = child_env(MCUSCOPE_URL=stack.base_url)
    proc = subprocess.Popen([*MCU, "tail", "-n", "500", "-f"], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env, **CHILD_TEXT)
    seen: list[str] = []

    def read() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            seen.append(line)
            if "from b2 0" in line:
                return

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(30)
    proc.kill()
    proc.wait(10)
    hit = [line for line in seen if "from b2 0" in line]
    assert hit and re.match(r"^\S+ \[b2\]  debug\| from b2 0$", hit[0]), seen[-5:]


# -- the CLI against canned daemons ----------------------------------------------------------


def _ports_body(monkeypatch, body) -> bool:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    monkeypatch.setattr(cli.Client, "open", lambda self: httpx.Client(transport=transport))
    return cli._port_column(Settings(url="http://127.0.0.1:1", json_out=False, port=None))


def test_the_daemons_own_rows_are_not_a_second_board(monkeypatch, capsys) -> None:
    """A finished result judged on its rows: port "" (daemon start, markers) is no board."""
    rows = [{"id": i, "ts": 1.0, "port": port, "dir": "-", "chan": "sys", "seq": None,
             "raw": f"row {i}"} for i, port in ((1, ""), (2, "a"))]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"lines": rows[::-1], "truncated": False})

    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler, "lines")
    assert rc == 0, err
    assert "row 1" in out and "row 2" in out and not COLUMN.search(out), out
    rows[0]["port"] = "b"      # positive control: two boards
    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler, "lines")
    assert rc == 0 and "[b]" in out and "[a]" in out, out


def test_a_stream_is_judged_on_the_attached_and_the_stored_ports(monkeypatch) -> None:
    assert _ports_body(monkeypatch, {"ports": [{"alias": "a"}], "stored": ["a", "b"]})
    assert _ports_body(monkeypatch, {"ports": [{"alias": "a"}, {"alias": "b"}], "stored": []})
    assert not _ports_body(monkeypatch, {"ports": [{"alias": "a"}], "stored": ["a"]})


@pytest.mark.parametrize("body", [
    {"ports": [{"alias": "a"}]},                   # a daemon older than `stored`
    {"ports": [{"alias": "a"}], "stored": 5},      # malformed
    ["not", "an", "object"],
])
def test_a_ports_answer_without_a_usable_stored_is_judged_on_the_attached(
    monkeypatch, body,
) -> None:
    assert not _ports_body(monkeypatch, body)


def test_a_multi_board_text_export_streams_the_daemons_rendering(monkeypatch, capsys) -> None:
    """A daemon reporting `stored` renders the column itself, so the export is not paged."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/ports":
            return httpx.Response(200, json={"ports": [{"alias": "a"}], "stored": ["a", "b"]})
        if request.url.path == "/lines/export":
            return httpx.Response(200, text="daemon rendered [b]\n")
        return httpx.Response(200, json={"lines": [], "truncated": False})

    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler, "log", "export")
    assert rc == 0, err
    assert out == "daemon rendered [b]\n"
    assert "/lines" not in paths
