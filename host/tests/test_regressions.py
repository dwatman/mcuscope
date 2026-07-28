"""Regression tests for defects found in the July 2026 review pass.

Each test names the defect it pins. They live together rather than scattered through the
topical suites because every one of them documents a behaviour that looked correct and
was not, and the shared context is worth more than the filing.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time

import mcu_sim
import pytest

from mcuscope import protocol as p
from mcuscope.cli import _hoist_global_opts as hoist
from mcuscope.config import load_config
from mcuscope.store import Store

# -- protocol -------------------------------------------------------------------------


def test_line_limit_is_255_content_bytes() -> None:
    """is_oversized capped content at 254, refusing a line SPEC 2.1 and the firmware allow."""
    assert not p.is_oversized("x" * 255)
    assert p.is_oversized("x" * 256)


def test_format_can_event_rejects_ids_parse_would_refuse() -> None:
    """format and parse must accept the same id set, or a producer emits undecodable lines."""
    with pytest.raises(p.ProtocolError):
        p.format_can_event(p.CanFrame(can_id=0x800, data=b"\xaa", tick_ms=1))
    with pytest.raises(p.ProtocolError):
        p.format_can_event(p.CanFrame(can_id=0x2000_0000, data=b"\xaa", ext=True, tick_ms=1))
    # The maximal legal ids still format, and round-trip through the parser.
    for frame in (
        p.CanFrame(can_id=0x7FF, data=b"\xaa", tick_ms=1),
        p.CanFrame(can_id=0x1FFF_FFFF, data=b"\xaa", ext=True, tick_ms=1),
    ):
        assert p.parse_can_event(p.format_can_event(frame)) is not None


@pytest.mark.parametrize(
    "raw",
    [
        "!can ² - 100 -",      # superscript two: isdigit() is True, int() raises
        "!can 1 r 100 ²",
        "!can 1 - 800 AA",          # id out of range for a standard frame
        "!can 1 x 20000000 AA",     # id out of range for an extended frame
    ],
)
def test_parse_can_event_returns_none_never_raises(raw: str) -> None:
    """SPEC 3.5: a malformed !can line is stored as a generic event, so this returns None."""
    assert p.parse_can_event(raw) is None


def test_parse_plot_adhoc_returns_none_for_non_ascii_digit() -> None:
    assert p.parse_plot_adhoc("!p ² a=1") is None


# -- store ----------------------------------------------------------------------------


def test_created_capture_has_incremental_autovacuum(tmp_path) -> None:
    """PRAGMA auto_vacuum must precede journal_mode=WAL, or it silently stays 0.

    With it at 0 every `PRAGMA incremental_vacuum` in the codebase is a no-op and a
    size-capped capture never hands freed pages back: one trimmed to zero rows still
    occupied 90 MiB on disk.
    """
    db = tmp_path / "cap.db"

    async def run() -> None:
        store = Store(str(db))
        await store.start()
        await store.stop()

    asyncio.run(run())
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2   # 2 == INCREMENTAL
    finally:
        conn.close()


def test_plot_points_has_line_id_index(tmp_path) -> None:
    """Without it the FK cascade full-scans plot_points on every retention chunk.

    Measured before the fix: one 5000-row chunk against 200k points took 97 s and blocked
    the event loop; with the index, 0.03 s.
    """
    db = tmp_path / "cap.db"

    async def run() -> None:
        store = Store(str(db))
        await store.start()
        await store.stop()

    asyncio.run(run())
    conn = sqlite3.connect(db)
    try:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='plot_points'"
        )}
        assert "idx_plot_line" in idx
    finally:
        conn.close()


def test_line_ids_are_not_reused_after_the_table_empties(tmp_path) -> None:
    """A session's id span must never come to describe a later run's lines.

    Sessions survive retention and `purge --all`, so restarting the id sequence at 1 made
    `session show run-alpha` return run-beta's traffic, and export/purge act on it.
    """
    db = tmp_path / "cap.db"

    async def first() -> tuple[int, int]:
        store = Store(str(db))
        await store.start()
        await store.start_session("run-alpha")
        for i in range(5):
            await store.add_line(ts=1.0, port="a", dir="rx", chan="debug", seq=None,
                                 raw=f"alpha {i}")
        await store.stop_session()
        high = store.max_id()
        # What `purge --all` does: delete every line, leaving the session rows behind.
        await store.delete_range(1, high)
        await store.stop()
        return high, 0

    async def second() -> int:
        store = Store(str(db))
        await store.start()
        row = await store.add_line(ts=2.0, port="a", dir="rx", chan="debug", seq=None,
                                   raw="beta 0")
        await store.stop()
        return row["id"]

    high, _ = asyncio.run(first())
    assert asyncio.run(second()) > high


def test_delete_session_does_not_fall_back_to_a_name_match(tmp_path) -> None:
    """get_session is id-only: resolve_session's name fallback deleted the wrong run.

    A session *named* "99" answered a lookup for id 99, so the route deleted a different
    session's lines and left the label behind pointing at the deleted range.
    """
    db = tmp_path / "cap.db"

    async def run() -> None:
        store = Store(str(db))
        await store.start()
        try:
            sess = await store.start_session("99")
            assert store.get_session(sess["id"]) is not None
            missing = 99 if sess["id"] != 99 else 98
            assert store.get_session(missing) is None          # id-only: no match
            assert store.resolve_session(str(missing)) is not None  # name fallback still works
        finally:
            await store.stop()

    asyncio.run(run())


def test_plot_series_can_be_scoped_to_one_port(tmp_path) -> None:
    """Channel names are unique only within a port, so two boards' data merged silently.

    Reproduced before the fix: boardA declaring `temp:s2*0.1:C` and boardB declaring
    `temp:s2*10:mV` produced ONE series containing both boards' samples, with
    non-monotonic ticks and boardA's Celsius values reported in boardB's unit.
    plot_points has no port column, but every row joins to its line, which does.
    """
    db = tmp_path / "cap.db"

    async def run() -> None:
        store = Store(str(db))
        await store.start()
        try:
            for port, value in (("boardA", 10.0), ("boardB", 50.0), ("boardA", 11.0)):
                await store.add_line(
                    ts=time.time(), port=port, dir="rx", chan="event", seq=None,
                    raw="!ps 0 64 00FF",
                    plot=[{"sid": "0", "name": "temp", "value": value, "tick_ms": 100}],
                )
            everything = await store.query_plot_series_safe(name="temp")
            just_a = await store.query_plot_series_safe(name="temp", port="boardA")
            just_b = await store.query_plot_series_safe(name="temp", port="boardB")
            assert len(everything) == 3          # unfiltered stays as it was
            assert [r["value"] for r in just_a] == [10.0, 11.0]
            assert [r["value"] for r in just_b] == [50.0]

            # The channel listing reports which port a channel's newest sample came from,
            # so a collision is at least visible, and can be narrowed.
            chans = await store.query_plot_channels_safe()
            assert [c["name"] for c in chans] == ["temp"]
            assert chans[0]["port"] == "boardA"   # newest sample
            a_only = await store.query_plot_channels_safe(port="boardB")
            assert a_only[0]["count"] == 1
        finally:
            await store.stop()

    asyncio.run(run())


# -- config ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, -7])
def test_retention_days_is_clamped_to_at_least_one(tmp_path, value: int) -> None:
    """A hand-edited retention_days <= 0 put the cutoff in the future and deleted everything.

    The write-back API bounds this (ge=1); the file loader is the path that never sees
    that validation, and it clamped max_db_bytes and min_sessions but not this one.
    """
    cfg = tmp_path / "config.toml"
    cfg.write_text(f"[storage]\nretention_days = {value}\n", encoding="utf-8")
    assert load_config(str(cfg)).storage.retention_days >= 1


# -- regex denial of service ----------------------------------------------------------

# 17 characters, no nested +/*, and exponential. Chosen deliberately: MAX_MATCH_LEN=200 is
# no defence (7 characters suffice), and a static "nested quantifier" screen would pass
# this one while flagging the harmless textbook examples. Only a real timeout works.
POISON_PATTERN = r"(?:a{1,3}){2,40}b"


def _poison_app(tmp_path):
    from mcuscope.config import Config, ServerConfig, StorageConfig
    from mcuscope.server import create_app

    return create_app(Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db"), retention_days=7),
        ports=[],
    ))


def test_catastrophic_pattern_is_refused_and_does_not_freeze_the_process(tmp_path) -> None:
    """A user regex must not be able to stop the daemon.

    Running match queries on a dedicated pool was never containment: CPython's `re` holds
    the GIL for the whole of a backtrack, so one short pattern froze the entire process
    (measured: a 10 ms heartbeat got 1 tick in 2.4 s), with no recovery but a restart. The
    daemon supports LAN exposure, so that is a remote DoS. Matching now runs on the `regex`
    engine, which releases the GIL and can be interrupted by a timeout.

    This asserts both halves: the request is refused quickly, AND an independent thread
    keeps running while it happens.
    """
    from fastapi.testclient import TestClient

    app = _poison_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert c.post("/marker", json={"text": "a" * 60}).status_code == 200

        ticks = [0]
        stop = threading.Event()

        def heartbeat() -> None:
            while not stop.is_set():
                ticks[0] += 1
                time.sleep(0.01)

        beat = threading.Thread(target=heartbeat, daemon=True)
        beat.start()
        started = time.monotonic()
        resp = c.get("/lines", params={"match": POISON_PATTERN, "limit": 10})
        elapsed = time.monotonic() - started
        stop.set()
        beat.join(timeout=2)

    assert resp.status_code == 400
    assert "budget" in resp.json()["error"]
    assert elapsed < 5.0, f"took {elapsed:.1f}s; the per-call timeout did not fire"
    # The GIL half. Under stdlib `re` this was ~1 tick regardless of how long it ran.
    assert ticks[0] >= elapsed * 20, (
        f"only {ticks[0]} heartbeat ticks in {elapsed:.2f}s: the matcher held the GIL"
    )


def test_catastrophic_pattern_refused_on_retrospective_assert(tmp_path) -> None:
    """400, never 500, and never a hang.

    Exit 2 from `mcu wait` already means "pattern valid, nothing matched in the window",
    so a killed pattern must be a 400 (CLI exit 1) rather than a timeout result. `mcu
    assert` never exits 2 at all, which is the other reason this cannot be a timeout.
    """
    from fastapi.testclient import TestClient

    app = _poison_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert c.post("/marker", json={"text": "a" * 60}).status_code == 200
        started = time.monotonic()
        resp = c.post("/assert", json={"expect": [POISON_PATTERN], "timeout_ms": 0})
        elapsed = time.monotonic() - started

    assert resp.status_code == 400, resp.text
    assert "budget" in resp.json()["error"]
    assert elapsed < 10.0


def test_live_window_matchers_are_budgeted(tmp_path) -> None:
    """The /wait and /assert live paths match through _search_batch / _scan_batch.

    Driven directly: a live window only runs the pattern when rows actually arrive during
    it, so an HTTP-level test of `since="now"` returns a plain timeout without ever
    exercising the matcher. These are the functions that see hostile input.
    """
    import mcuscope.server as server_mod
    from mcuscope.store import MatchBudgetExceeded

    pattern = server_mod.regex.compile(POISON_PATTERN)
    texts = ["a" * 60] * 5

    started = time.monotonic()
    with pytest.raises(MatchBudgetExceeded):
        server_mod._search_batch(pattern, texts)
    assert time.monotonic() - started < 5.0

    started = time.monotonic()
    with pytest.raises(MatchBudgetExceeded):
        server_mod._scan_batch([pattern], texts)
    assert time.monotonic() - started < 5.0

    # An honest pattern over the same texts still returns a result, not an exception.
    assert server_mod._search_batch(server_mod.regex.compile("a{10}"), texts) == 0


def test_ordinary_patterns_are_unaffected(tmp_path) -> None:
    """The budget must not be reachable by honest use."""
    from fastapi.testclient import TestClient

    app = _poison_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.post("/marker", json={"text": "hello world"})
        c.post("/marker", json={"text": "goodbye"})
        rows = c.get("/lines", params={"match": "hell.", "limit": 10}).json()["lines"]
        assert any("hello world" in r["raw"] for r in rows)
        # An invalid pattern is a 400 with a readable message, not an opaque 500.
        bad = c.get("/lines", params={"match": "((("})
        assert bad.status_code == 400
        assert "bad match regex" in bad.json()["error"]


# -- cli argv hoisting ----------------------------------------------------------------


def test_hoisting_leaves_a_global_looking_option_value_alone() -> None:
    """`mcu lines --match -p --limit 5` meant the regex "-p", and returned wrong data.

    Hoisting ran before parsing, so it treated the value of --match as the global port
    option and consumed --limit as that option's value, leaving the regex as "5". Exit 0,
    no warning, wrong answer.
    """
    assert hoist(["lines", "--match", "-p", "--limit", "5"]) == \
        ["lines", "--match", "-p", "--limit", "5"]
    assert hoist(["wait", "--match", "ERR", "--send", "-p"]) == \
        ["wait", "--match", "ERR", "--send", "-p"]
    assert hoist(["session", "start", "--note", "--json", "run1"]) == \
        ["session", "start", "--note", "--json", "run1"]


def test_hoisting_still_moves_real_global_options() -> None:
    assert hoist(["i2c", "rd", "48", "2", "--json"]) == ["--json", "i2c", "rd", "48", "2"]
    assert hoist(["lines", "--port", "sim", "--limit", "5"]) == \
        ["--port", "sim", "lines", "--limit", "5"]
    assert hoist(["tail", "-f", "--json"]) == ["--json", "tail", "-f"]


def test_hoisting_handles_the_attached_short_form() -> None:
    """`mcu lines -psim` was a usage error while `mcu -psim lines` worked."""
    assert hoist(["lines", "-psim", "--limit", "1"]) == ["-psim", "lines", "--limit", "1"]


def test_hoisting_respects_end_of_options() -> None:
    assert hoist(["send", "--", "-p test marker"]) == ["send", "--", "-p test marker"]


def test_hoisting_resolves_the_subcommand_past_a_leading_global_value() -> None:
    """A leading `-p board` made the subcommand walk stop, disabling the guard above.

    _value_taking_opts skipped tokens starting with "-" but not the *value* that follows a
    global option, so it looked up a command named "board", gave up, and fell back to the
    root group's options. Every protection for subcommand option values was then off:
    `mcu -p board lines --match -p --limit 5` became --port=--limit with the regex "5".
    """
    assert hoist(["-p", "board", "lines", "--match", "-p", "--limit", "5"]) == \
        ["-p", "board", "lines", "--match", "-p", "--limit", "5"]
    assert hoist(["--url", "http://x", "lines", "--match", "-p"]) == \
        ["--url", "http://x", "lines", "--match", "-p"]
    assert hoist(["-p", "board", "lines", "--match", "--json"]) == \
        ["-p", "board", "lines", "--match", "--json"]
    # The global still hoists when it really is one, from after the subcommand.
    assert hoist(["-p", "board", "lines", "--limit", "5", "--json"]) == \
        ["-p", "board", "--json", "lines", "--limit", "5"]


# -- simulator ------------------------------------------------------------------------


def _fresh_sim() -> mcu_sim.Simulator:
    return mcu_sim.Simulator(mcu_sim.build_parser().parse_args([]))


@pytest.mark.parametrize(
    ("cmd", "ext"),
    [(">1 can tx 7FF DEAD", False), (">1 can tx 1FFFFFFF DEAD x", True)],
)
def test_can_tx_at_the_top_of_the_id_range_does_not_raise(cmd: str, ext: bool) -> None:
    """`can tx 7FF` answered OK and then killed the simulator thread for good.

    The echo frame is id+1, and 0x7FF + 1 is out of range for a standard frame, so
    format_can_event raised from inside poll_events. That escaped the serving loop while
    the listening socket stayed open, so the daemon reconnected into a backlog nobody was
    accepting from and reported a healthy port that never produced another byte.
    """
    sim = _fresh_sim()
    assert sim.handle_line(cmd) == ["<1 OK"]
    # The echo is due 20 ms later; poll until it lands rather than sleeping a fixed time.
    deadline = time.monotonic() + 2.0
    echoed: list[str] = []
    while time.monotonic() < deadline and not echoed:
        echoed = [ln for ln in sim.poll_events() if ln.startswith("!can")]
        time.sleep(0.005)
    assert echoed, "the echo never arrived"
    # It wrapped inside its own range instead of overflowing out of it, so every frame it
    # produced is one the parser accepts.
    top = p.CAN_ID_MAX_EXT if ext else p.CAN_ID_MAX_STD
    for ln in echoed:
        frame = p.parse_can_event(ln)
        assert frame is not None, ln
        assert frame.can_id <= top


def test_can_filter_rejects_a_trailing_flags_token() -> None:
    """SPEC 2.4: `can filter 100 700 r` must be refused, not answered OK and ignored."""
    sim = _fresh_sim()
    assert sim.handle_line(">1 can filter 100 700") == ["<1 OK"]
    for bad in (">2 can filter 100 700 r", ">2 can filter 100 700 x"):
        resp = p.parse_response(sim.handle_line(bad)[0])
        assert not resp.ok, bad
        assert resp.err_name == "badarg"


# -- windows text and path handling ---------------------------------------------------


def test_config_write_back_keeps_lf_endings(tmp_path) -> None:
    """The one text write in the package without newline=, so it wrote CRLF on Windows.

    A single settings save from the web UI rewrote every line of a hand-edited LF config.
    """
    import tomlkit

    from mcuscope.config import _write_doc

    path = tmp_path / "config.toml"
    path.write_bytes(b'[server]\nhost = "127.0.0.1"\nport = 8765\n')
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    doc["server"]["port"] = 8888
    _write_doc(path, doc)
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.count(b"\n") == 3


def test_export_filename_avoids_windows_reserved_device_names() -> None:
    """`CON.db` / `COM1.db` cannot be created on Windows even with the extension."""
    from mcuscope.server import _safe_download_stem

    for name in ("com1", "CON", "aux", "LPT9", "nul"):
        stem = _safe_download_stem(name)
        assert stem.split(".")[0].upper() not in {
            "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
    # Trailing dots and spaces are silently stripped by Windows, so they must not be the
    # only thing left, and an ordinary name is untouched.
    assert _safe_download_stem("...") == "session"
    assert _safe_download_stem("") == "session"
    assert _safe_download_stem("run-42") == "run-42"


def test_db_path_comparison_is_case_and_separator_insensitive_on_windows() -> None:
    """UI settings reported restart_required for a path that named the file already open."""
    import os

    from mcuscope.server import _same_path

    assert _same_path("/data/capture.db", "/data/capture.db")
    assert not _same_path("/data/a.db", "/data/b.db")
    if os.name == "nt":
        assert _same_path(r"C:\data\capture.db", r"c:\data\capture.db")
        assert _same_path(r"C:\data\capture.db", "C:/data/capture.db")
        assert _same_path(r"C:\data\.\capture.db", r"C:\data\capture.db")


def test_static_js_is_served_as_javascript_whatever_the_registry_says() -> None:
    """A registry .js -> text/plain mapping blanked the whole UI: app.js is a module."""
    import mimetypes

    from mcuscope.server import _pin_static_mimetypes

    mimetypes.add_type("text/plain", ".js")   # simulate the hostile registry entry
    try:
        _pin_static_mimetypes()
        assert "javascript" in (mimetypes.guess_type("app.js")[0] or "")
        assert mimetypes.guess_type("style.css")[0] == "text/css"
    finally:
        _pin_static_mimetypes()


# -- protocol strictness --------------------------------------------------------------


@pytest.mark.parametrize("token", ["+5", "1_0", "\u0665", " 5", "5.0", "0x5", "", "-1"])
def test_parse_seq_token_is_strict_ascii_decimal(token: str) -> None:
    """Bare int() accepted signs, digit grouping and non-ASCII digits off the wire."""
    with pytest.raises(p.ProtocolError):
        p.parse_seq_token(token)


# " 5" is absent: the line parsers split on whitespace, so it never reaches them as a token.
@pytest.mark.parametrize("token", ["+5", "1_0", "\u0665", "5.0", "0x5"])
def test_wire_lines_reject_loose_seq_tokens(token: str) -> None:
    """A garbled `<+17 OK` would otherwise resolve the pending command for seq 17."""
    with pytest.raises(p.ProtocolError):
        p.parse_response(f"<{token} OK")
    with pytest.raises(p.ProtocolError):
        p.parse_command(f">{token} ping")


def test_response_seq_extraction_is_strict_too() -> None:
    """The fast path that pops a pending entry must agree with the full parser."""
    from mcuscope.serial_link import _response_seq

    assert _response_seq("<17 OK") == 17
    for bad in ("<+17 OK", "<1_7 OK", "<\u0665 OK"):
        assert _response_seq(bad) is None


def test_marker_tick_requires_ascii_digits() -> None:
    """\\d also matches non-ASCII decimal digits, which the rest of the stack never sees."""
    assert p.parse_marker("!m @55 hello").tick_ms == 55
    assert p.parse_marker("!m @\u0665\u0665 hello").tick_ms is None


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

    stack = make_stack(["--drop-response", "1"])
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


def test_cancelling_a_command_does_not_leak_its_pending_entry(tmp_path) -> None:
    """Only TimeoutError popped the seq, so every cancelled /cmd leaked one entry.

    CancelledError is a BaseException (client disconnect, Ctrl-C, uvicorn cancelling the
    handler), so it escaped the cleanup and the entry survived until the next disconnect.
    The sim is told to swallow the first response, so the command is reliably still
    in flight when it is cancelled.
    """
    from mcuscope.serial_link import SerialPort

    async def run() -> None:
        stop = threading.Event()
        sock = mcu_sim.open_tcp_listener(0)
        tcp_port = sock.getsockname()[1]
        args = mcu_sim.build_parser().parse_args(["--drop-response", "1"])
        thread = threading.Thread(
            target=mcu_sim.serve_listener, args=(args, sock, stop), daemon=True
        )
        thread.start()
        store = Store(str(tmp_path / "pending.db"))
        await store.start()
        port = SerialPort(
            store, asyncio.get_running_loop(), "board",
            device=f"socket://127.0.0.1:{tcp_port}",
        )
        port.start()
        try:
            deadline = time.monotonic() + 5.0
            while not port.connected and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert port.connected, "the port never connected to the simulator"

            task = asyncio.ensure_future(port.send_command("ping", 10_000))
            # The response to this one is swallowed, so it is certainly still pending.
            deadline = time.monotonic() + 5.0
            while not port._pending and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert port._pending, "the command never registered a pending entry"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert port._pending == {}
        finally:
            await port.stop()
            await store.stop()
            stop.set()
            thread.join(timeout=2.0)
            sock.close()

    asyncio.run(run())
