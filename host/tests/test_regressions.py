"""Regression tests for defects found in the July 2026 review pass.

Each test names the defect it pins. They live together rather than scattered through the
topical suites because every one of them documents a behaviour that looked correct and
was not, and the shared context is worth more than the filing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import threading
import time

import mcu_sim
import pytest

from mcuscope import cli as cli_module
from mcuscope import protocol as p
from mcuscope import sim
from mcuscope.cli import _hoist_global_opts as hoist
from mcuscope.config import ConfigError, StorageConfig, load_config
from mcuscope.store import Store, _WriteReq
from tests.support import UNOPENABLE

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
        # Arabic-Indic three, which the superscript above does not cover: isdecimal() is
        # True for it *and* int() converts it to 3, so the RTR dlc digit accepted it and a
        # garbled line decoded into a can_frames row instead of staying a generic event.
        "!can 1 r 100 ٣",
        "!can 1 - 800 AA",          # id out of range for a standard frame
        "!can 1 x 20000000 AA",     # id out of range for an extended frame
    ],
)
def test_parse_can_event_returns_none_never_raises(raw: str) -> None:
    """SPEC 3.5: a malformed !can line is stored as a generic event, so this returns None."""
    assert p.parse_can_event(raw) is None


def test_decimal_tokens_are_ascii_on_every_can_and_sim_path() -> None:
    """The same token class as the seq/tick/sid fixes, at the three sites they missed.

    `'٣'.isdecimal()` is True and `int('٣')` is 3, so every check written as isdecimal()
    accepts a digit no SPEC grammar allows and no firmware would emit.
    """
    with pytest.raises(p.ProtocolError):
        p.parse_can_tx_args(["100", "٣", "r"])       # host-side `can tx`, from user text
    with pytest.raises(p.ProtocolError):
        sim._parse_dec("٣", 0, 10)                   # simulator command arguments
    # The ASCII spelling of each still works, so the check discriminates.
    assert p.parse_can_tx_args(["100", "3", "r"]).dlc == 3
    assert sim._parse_dec("3", 0, 10) == 3


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


def test_session_ref_is_an_ascii_decimal_or_a_name(tmp_path) -> None:
    """resolve_session's id branch gated on isdecimal(), which fails both ways.

    A 5000-digit ref reached `int()` and raised past CPython's conversion limit: an
    unhandled 500 with a traceback on `GET /sessions/{ref}/export` and on every endpoint
    taking `session=`. And a session *named* with another script's digit resolved to the
    id that digit converts to, which is the wrong-session bug this branch already carries a
    comment about.
    """
    async def run() -> None:
        store = Store(str(tmp_path / "sess.db"))
        await store.start()
        try:
            # Three sessions first, so the id the digit converts to (3) exists and belongs
            # to someone else - otherwise the id branch falls through to the name branch on
            # its own and the assertion below passes either way.
            for name in ("one", "two", "three"):
                await store.start_session(name)
            named = await store.start_session("٣")     # Arabic-Indic three
            assert named["id"] == 4
            assert store.resolve_session("9" * 5000) is None    # no raise: not an id token
            assert store.resolve_session("٣")["id"] == 4, "resolved to session id 3 instead"
            # And the ordinary spellings still resolve, by id and by name.
            assert store.resolve_session("3")["name"] == "three"
            assert store.resolve_session("one")["id"] == 1
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
    cfg.write_text(f"[storage]\nretention_days = {value}\n", encoding="utf-8", newline="\n")
    assert load_config(str(cfg)).storage.retention_days >= 1


def test_config_integers_are_not_coerced(tmp_path) -> None:
    """The int() half of the `check = "false"` defect, which _as_bool fixed only for bools.

    TOML has real types, so anything else here is a hand-edited mistake, and bare int()
    took each one as written: `port = true` became port **1** (a bool is an int in
    Python), `port = 8558.7` truncated in silence, and a typo'd `port = 99999999` was
    accepted and then failed much later from inside the bind, naming neither the config
    file nor the key.
    """
    cfg = tmp_path / "config.toml"
    # A wrong type fails the load and names the key, the way `port = "abc"` already did.
    for value in ("true", "8558.7", '"9000"'):
        cfg.write_text(f"[server]\nport = {value}\n", encoding="utf-8", newline="\n")
        with pytest.raises(ConfigError, match="whole number"):
            load_config(str(cfg))
    # Out of range falls back to the default instead, with a warning: there is a sane
    # answer to fall back on, and for a retention setting it is the conservative one.
    for value in ("99999999", "0", "-1"):
        cfg.write_text(f"[server]\nport = {value}\n", encoding="utf-8", newline="\n")
        assert load_config(str(cfg)).server.port == 8558, f"port = {value} was taken"
    # A real, in-range integer still lands, so the guard is not simply refusing everything.
    cfg.write_text("[server]\nport = 9000\n", encoding="utf-8", newline="\n")
    assert load_config(str(cfg)).server.port == 9000
    cfg.write_text("[storage]\nmin_sessions = -1\n", encoding="utf-8", newline="\n")
    assert load_config(str(cfg)).storage.min_sessions == StorageConfig.min_sessions


def test_port_entries_are_typed_and_one_bad_entry_stays_local(tmp_path, caplog) -> None:
    """The ports loop kept both coercions after the sections above were fixed.

    `autoconnect = "false"` is the very string `_as_bool` was written for, 25 lines below
    it, and `bool()` read it as True. `baud = true` became **1 baud**, a port that can
    never talk. Both are warned about and defaulted rather than failing the load: charging
    one bad entry to the whole file is registry class 16.
    """
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[[ports]]\nalias = "board"\ndevice = "COM7"\nbaud = true\nautoconnect = "false"\n'
        '[[ports]]\nalias = "good"\ndevice = "COM8"\nbaud = 9600\n',
        encoding="utf-8", newline="\n",
    )
    with caplog.at_level(logging.WARNING, logger="mcuscope.config"):
        ports = load_config(str(cfg)).ports
    assert [(p.alias, p.baud) for p in ports] == [("board", 115200), ("good", 9600)]
    assert sum("ports.board" in r.message for r in caplog.records) == 2, "both must be named"
    # The neighbour is untouched, which is the half a hard failure would have destroyed.
    assert ports[1].device == "COM8"


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


def test_can_filter_takes_the_x_flag_and_refuses_the_r_flag() -> None:
    """SPEC 2.4: `x` is accepted and passed to the port layer, `r` is refused.

    Both were refused here, which is the *stricter* mistake and so the quiet one: the sim is
    a second implementation of SPEC 5, and a firmware that follows the spec would have been
    judged wrong by the tool meant to model it. The test named only `r` and pinned both.
    """
    sim = _fresh_sim()
    assert sim.handle_line(">1 can filter 100 700") == ["<1 OK"]
    assert sim.handle_line(">2 can filter 100 700 x") == ["<2 OK"]
    assert sim.state.can[1].filter_ext is True
    for bad in (">3 can filter 100 700 r", ">3 can filter 100 700 z", ">3 can filter 1 2 x y"):
        resp = p.parse_response(sim.handle_line(bad)[0])
        assert not resp.ok, bad
        assert resp.err_name == "badarg", bad


# -- windows text and path handling ---------------------------------------------------


def test_config_write_back_keeps_lf_endings(tmp_path) -> None:
    """This write once lacked newline=, so it wrote CRLF on Windows.

    A single settings save from the web UI rewrote every line of a hand-edited LF config.
    Every text write in the package passes newline= now; see registry class 2 in docs/REVIEW.md.
    """
    import tomlkit

    from mcuscope.config import _write_doc

    path = tmp_path / "config.toml"
    path.write_bytes(b'[server]\nhost = "127.0.0.1"\nport = 8558\n')
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
    # Normalisation, not string equality: these hold on both platforms, so the test still
    # fails on Linux if _same_path is ever reduced to `a == b`. Everything below was inside
    # the Windows guard, which left the POSIX run asserting nothing a plain == would miss.
    assert _same_path("/data/./capture.db", "/data/capture.db")
    assert _same_path("/data//capture.db", "/data/capture.db")
    assert _same_path("/data/sub/../capture.db", "/data/capture.db")
    if os.name == "nt":
        # Case folding is correct only where the filesystem is case-insensitive.
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


def test_config_with_a_utf8_bom_loads(tmp_path) -> None:
    """PowerShell's `Out-File -Encoding utf8` always writes a BOM, and plenty of Windows
    editors do too. The TOML parser rejects one at line 1, column 1 with a message that
    names neither the cause nor the fix, so hand-editing config.toml the obvious way
    on Windows left the daemon refusing to start over an invisible character."""
    import tomlkit

    from mcuscope.config import _read_doc, _write_doc, load_config

    path = tmp_path / "config.toml"
    body = '[server]\nhost = "127.0.0.1"\nport = 8791\n'
    path.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))

    cfg = load_config(path)
    assert cfg.server.port == 8791 and cfg.server.host == "127.0.0.1"

    # The write-back path parses it too, and normalises the BOM away on save.
    doc = _read_doc(path)
    doc["server"]["port"] = 8888
    _write_doc(path, doc)
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert load_config(path).server.port == 8888
    assert tomlkit.parse(path.read_text(encoding="utf-8"))["server"]["port"] == 8888


def test_replace_atomic_rides_out_a_windows_sharing_violation(tmp_path, monkeypatch) -> None:
    """os.replace fails on Windows while anyone holds either file open; POSIX never does.

    An on-access virus scan or the Search indexer taking a transient handle on config.toml
    was enough to lose a settings save from the web UI. Simulated here rather than raced,
    so the retry is pinned on both platforms.
    """
    import os as _os

    from mcuscope.config import replace_atomic

    src, dst = tmp_path / "a.tmp", tmp_path / "a"
    dst.write_text("old", encoding="utf-8", newline="\n")
    real_replace = _os.replace
    calls = []

    def flaky(a, b):
        calls.append(1)
        if len(calls) < 3:            # WinError 5: destination held by another process
            raise PermissionError(13, "Access is denied", str(a), 5, str(b))
        real_replace(a, b)

    src.write_text("new", encoding="utf-8", newline="\n")
    monkeypatch.setattr(_os, "replace", flaky)
    replace_atomic(src, dst)
    assert dst.read_text() == "new" and len(calls) == 3

    # A handle that is never released still fails, with the real error rather than a hang.
    src.write_text("newer", encoding="utf-8", newline="\n")
    monkeypatch.setattr(_os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(PermissionError):
        replace_atomic(src, dst, attempts=2)


def test_replace_atomic_survives_a_real_open_handle_on_windows(tmp_path) -> None:
    """The same thing unsimulated: a reader that lets go while the retry is still running."""
    import os as _os

    from mcuscope.config import replace_atomic

    src, dst = tmp_path / "b.tmp", tmp_path / "b"
    dst.write_text("old", encoding="utf-8", newline="\n")
    src.write_text("new", encoding="utf-8", newline="\n")
    holder = open(dst)                       # noqa: SIM115 - closed by the timer below
    threading.Timer(0.15, holder.close).start()
    try:
        replace_atomic(src, dst)             # must not raise on either platform
    finally:
        if not holder.closed:
            holder.close()
    assert dst.read_text() == "new"
    assert not _os.path.exists(src)


def test_devices_enumeration_does_not_stall_the_event_loop(stack, monkeypatch) -> None:
    """Enumerating serial ports is a setupapi query on Windows, not a cheap sysfs walk.

    Running it on the loop froze every WebSocket feed and every other request for its
    duration - invisible on Linux, seconds on a Windows box carrying Bluetooth COM ports.
    """
    import httpx

    from mcuscope import server as server_mod

    def slow_scan(*_a, **_k):
        time.sleep(2.0)
        return []

    monkeypatch.setattr(server_mod, "cached_comports", slow_scan)
    started = threading.Event()

    def hit_devices() -> None:
        started.set()
        httpx.get(f"{stack.base_url}/devices", timeout=10.0)

    t = threading.Thread(target=hit_devices, daemon=True)
    t.start()
    started.wait(2.0)
    time.sleep(0.1)                          # make sure the scan is under way
    began = time.monotonic()
    assert httpx.get(f"{stack.base_url}/status", timeout=5.0).status_code == 200
    # The scan sleeps 2.0 s, so a blocked loop answers in no less than ~1.9 s from here;
    # an unblocked one answers in an ordinary request round trip. The budget sits far
    # from both, because a tight one (0.4 s against a 0.6 s scan) failed on a Windows
    # box once the suite's earlier tests had aged the process: an unblocked round trip
    # crept to ~0.45 s. Discrimination comes from the spread, not from a fast machine.
    assert time.monotonic() - began < 1.2, "an in-flight /devices scan blocked the loop"
    t.join(timeout=10.0)


def test_devices_skips_realpath_when_there_is_no_by_id_map(monkeypatch) -> None:
    """`realpath("COM7")` is a pointless filesystem hop answering `<cwd>\\COM7`."""
    from mcuscope import server as server_mod

    class _Info:
        device, description, serial_number = "COM7", "USB Serial", "SN9"
        vid, pid = 0x0483, 0x5740

    monkeypatch.setattr(server_mod, "cached_comports", lambda *a, **k: [_Info()])
    monkeypatch.setattr(server_mod, "_by_id_map", dict)
    monkeypatch.setattr(
        server_mod.os.path, "realpath",
        lambda p: pytest.fail("realpath called with no by-id map to look up in"),
    )
    (dev,) = server_mod._enumerate_devices()
    assert dev["device"] == "COM7" and dev["by_id"] is None
    assert dev["vid_pid"] == "0483:5740" and dev["serial_number"] == "SN9"


def test_port_already_in_use_is_refused() -> None:
    """uvicorn's unconditional SO_REUSEADDR means Windows lets a second daemon bind a
    port that is already being listened on, so it started, printed its URL and was never
    reached.

    The probe now runs on POSIX too. The kernel does refuse the bind there by itself, but
    only once uvicorn.run() reaches it - which is after pidfile.claim(), so the second
    daemon took over the first's pid record and deleted it on its way out. See
    test_port_conflict_is_detected_on_every_platform.
    """
    import socket

    from mcuscope.daemon import _port_conflict

    held = socket.socket()
    held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    held.bind(("127.0.0.1", 0))
    port = held.getsockname()[1]
    held.listen(5)
    try:
        conflict = _port_conflict("127.0.0.1", port)
        assert conflict is not None and str(port) in conflict
    finally:
        held.close()
    # A free port is never reported, on either platform.
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    free_port = free.getsockname()[1]
    free.close()
    assert _port_conflict("127.0.0.1", free_port) is None


def test_daemon_declines_to_start_on_a_taken_port(tmp_path, monkeypatch, capsys) -> None:
    """The conflict has to end the start, not just be noticed."""
    from mcuscope import daemon as daemon_mod

    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[server]\nhost = "127.0.0.1"\nport = 8558\n\n'
        f'[storage]\ndb_path = {str(tmp_path / "capture.db")!r}\n',
        encoding="utf-8", newline="\n",
    )
    monkeypatch.setattr(daemon_mod, "_port_conflict", lambda h, p: "127.0.0.1:8558 is busy")
    monkeypatch.setattr(
        daemon_mod.uvicorn, "run",
        lambda *a, **k: pytest.fail("uvicorn must not be reached on a port conflict"),
    )
    assert daemon_mod.main(["--config", str(cfg)]) == 1
    assert "is busy" in capsys.readouterr().err   # startup refusals go to stderr


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

    stack = make_stack(["--drop-response", "2"])   # 1 is the connect-time ping
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
    # The send itself got no answer, which is the precondition this test needs to hold:
    # /assert does not report its send, so read it off the capture (a `>2 ping` row with
    # no `<2` response; seq 1 was the connect-time ping).
    rows = httpx.get(f"{stack.base_url}/lines", params={"limit": 50}, timeout=5.0).json()
    raws = [row["raw"] for row in rows["lines"]]
    assert ">2 ping" in raws and not any(raw.startswith("<2 ") for raw in raws), raws
    assert body["checked_lines"] > 0, body
    assert body["status"] == "pass", body
    assert body["expect"][0]["matched"] is True


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
        args = mcu_sim.build_parser().parse_args(["--drop-response", "2"])   # 1: connect ping
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


def test_cancelling_a_command_mid_write_does_not_leak_its_pending_entry(tmp_path) -> None:
    """The to_thread write made registration-to-wait a cancellation window.

    The write used to be synchronous, so no cancel could land between registering the
    pending entry and the guarded response wait; offloading it opened a window guarded
    only for PortError. Seen as a Windows 3.11/3.12 CI failure of the sibling test above,
    where to_thread dispatch is slow enough for the cancel to land inside the write.
    Deterministic here: the write blocks until released, so the cancel always lands in it.
    """
    from mcuscope.serial_link import SerialPort

    async def run() -> None:
        stop = threading.Event()
        sock = mcu_sim.open_tcp_listener(0)
        tcp_port = sock.getsockname()[1]
        args = mcu_sim.build_parser().parse_args([])
        thread = threading.Thread(
            target=mcu_sim.serve_listener, args=(args, sock, stop), daemon=True
        )
        thread.start()
        store = Store(str(tmp_path / "pending_write.db"))
        await store.start()
        port = SerialPort(
            store, asyncio.get_running_loop(), "board",
            device=f"socket://127.0.0.1:{tcp_port}",
        )
        port.start()
        release = threading.Event()
        try:
            deadline = time.monotonic() + 5.0
            while not port.connected and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert port.connected, "the port never connected to the simulator"

            def blocked_write(payload: bytes) -> None:
                release.wait(timeout=5.0)

            port._write_bytes = blocked_write
            task = asyncio.ensure_future(port.send_command("ping", 10_000))
            deadline = time.monotonic() + 5.0
            while 2 not in port._pending and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert 2 in port._pending, "the command never registered a pending entry"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert port._pending == {}
        finally:
            release.set()
            await port.stop()
            await store.stop()
            stop.set()
            thread.join(timeout=2.0)
            sock.close()

    asyncio.run(run())


# -- August 2026 review pass ----------------------------------------------------------


def test_port_conflict_is_detected_on_every_platform() -> None:
    """POSIX skipped the probe, so a failing second daemon deleted the first's pid record.

    uvicorn only reports EADDRINUSE from inside run(), which is after pidfile.claim(): the
    second daemon claimed the record, failed to bind, and removed it on the way out.
    """
    import socket

    from mcuscope.daemon import _port_conflict

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    busy = listener.getsockname()[1]
    try:
        msg = _port_conflict("127.0.0.1", busy)
        assert msg is not None and str(busy) in msg
    finally:
        listener.close()
    # The same port, once released, must read as free (no leaked probe socket either).
    assert _port_conflict("127.0.0.1", busy) is None


def test_claim_keeps_a_record_that_is_already_ours(tmp_path, monkeypatch) -> None:
    """claim() removed and recreated its own record, opening a no-pid-file window."""
    import os

    from mcuscope import pidfile

    path = tmp_path / "mcuscope-127.0.0.1-9.pid"
    monkeypatch.setattr(pidfile, "pid_file_path", lambda h, p: str(path))
    path.write_text(str(os.getpid()), encoding="utf-8", newline="\n")

    # The removal is the defect, not the end state: the recreated file looks identical
    # (and the filesystem may even hand back the same inode), so watch for the unlink
    # itself. `mcu daemon stop` landing in that window reports "no pid file" and exits 1.
    removed: list[str] = []
    real_remove = os.remove
    monkeypatch.setattr(os, "remove", lambda p, *a, **kw: (removed.append(str(p)),
                                                           real_remove(p, *a, **kw))[1])

    claimed = pidfile.claim("127.0.0.1", 9)
    assert claimed == str(path)
    assert path.read_text(encoding="utf-8").strip() == str(os.getpid())
    assert removed == [], "claim() deleted a record that was already ours"


def test_config_refuses_to_coerce_a_quoted_boolean(tmp_path) -> None:
    """bool("false") is True, so a hand-edited `check = "false"` enabled the update check.

    A wrong-typed bool now fails the load naming the key (SPEC 3.3), like _as_int and
    _as_str: warning and defaulting still started the daemon with the setting the typo was
    meant to turn off, which SPEC 3.6 calls the wrong way to be wrong for `update.check`.
    """
    cfg_path = tmp_path / "config.toml"
    # 0 and "" are the discriminating cases: bool() reads both as False, silently turning
    # a malformed entry into a setting nobody chose. `check = "false"` is the likelier
    # typo and coerces the opposite way.
    for body, key in (
        ("[update]\ncheck = 0\n", "check"),
        ('[storage]\nauto_session = ""\n', "auto_session"),
        ('[update]\ncheck = "false"\n', "check"),
    ):
        cfg_path.write_text(body, encoding="utf-8", newline="\n")
        with pytest.raises(ConfigError, match=f"{key} must be true or false"):
            load_config(cfg_path)
    # A real TOML boolean is still honoured, both ways.
    cfg_path.write_text("[update]\ncheck = false\n", encoding="utf-8", newline="\n")
    assert load_config(cfg_path).update.check is False
    cfg_path.write_text("[update]\ncheck = true\n", encoding="utf-8", newline="\n")
    assert load_config(cfg_path).update.check is True


def test_update_cache_timestamp_survives_a_null_latest(tmp_path, monkeypatch) -> None:
    """A pre-release-only PyPI wrote {"latest": null} that the loader refused, so every
    restart re-asked - defeating the once-a-day guarantee (SPEC 3.6)."""
    import json

    from mcuscope.update_check import ENV_ENABLE, UpdateChecker

    # conftest disables the check suite-wide to keep it off the network; the scheduling
    # this test is about only happens when it is enabled. No request is made either way:
    # the point is what the loaded cache says about whether one is owed.
    monkeypatch.delenv(ENV_ENABLE, raising=False)
    path = tmp_path / "update.json"
    stamp = time.time()
    path.write_text(json.dumps({"latest": None, "checked_at": stamp}),
                    encoding="utf-8", newline="\n")

    checker = UpdateChecker(enabled=True, current="0.1.0", path=path)
    assert checker.latest is None
    assert checker.checked_at == pytest.approx(stamp, abs=1.0)
    # The cached timestamp counts, so no check is owed: refusing it made every restart
    # due immediately, which is the defect.
    assert checker._due() is False


def test_stream_repair_warning_goes_to_stderr(capsys, monkeypatch) -> None:
    """It printed on stdout, so `mcu --json` with a closed stderr emitted the warning
    ahead of the JSON object and broke every parsing consumer."""
    from mcuscope import _stdio

    monkeypatch.setattr(_stdio, "repair_std_streams", lambda: (["stderr"], False))
    rc = _stdio.console_entry(lambda: 0, "mcu")
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "WARNING" in captured.err


def test_detach_and_reattach_carries_the_tx_counter() -> None:
    """lines_rx and rx_dropped survived a reconnect; lines_tx silently reset to zero."""
    from mcuscope.serial_link import PortManager

    async def run() -> None:
        store = Store(":memory:")
        await store.start()
        mgr = PortManager(store, asyncio.get_running_loop())
        try:
            port = await mgr.attach("t", device=UNOPENABLE)
            port.lines_rx, port.lines_tx, port.rx_dropped = 100, 5, 2
            await mgr.detach("t")
            again = await mgr.attach("t", device=UNOPENABLE)
            assert (again.lines_rx, again.lines_tx, again.rx_dropped) == (100, 5, 2)
            await mgr.detach("t")
        finally:
            await store.stop()

    asyncio.run(run())


def test_store_stop_fails_queued_writes_instead_of_stranding_them() -> None:
    """A cancelled writer left queued futures unresolved, and _store_rx_batch awaits them:
    the awaiter hung until the loop closed and died pending."""
    from mcuscope.store import StoreError

    async def run() -> None:
        store = Store(":memory:")
        await store.start()
        # Cancel the writer out from under the queue, then queue a write nobody will drain.
        store._writer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await store._writer_task
        pending = asyncio.get_running_loop().create_future()
        store._queue.put_nowait(
            _WriteReq(row={"raw": "x"}, can=None, plot=None, future=pending)
        )
        await store.stop()
        assert pending.done()
        with pytest.raises(StoreError):
            pending.result()

    asyncio.run(run())


def test_absent_8250_ports_are_hidden_but_real_uarts_are_kept(tmp_path, monkeypatch) -> None:
    """`mcu devices` listed 32 phantom /dev/ttyS* on Linux, burying the one real adapter.

    The filter must key on the kernel's own PORT_UNKNOWN verdict, not on the name: ttyS0
    is a real mini-UART on a Raspberry Pi and a real on-chip UART on many ARM SoCs.
    """
    import sys as _sys

    from mcuscope import serial_link

    if _sys.platform != "linux":
        pytest.skip("sysfs serial-core attributes are Linux-only")

    sysfs = tmp_path / "sys" / "class" / "tty"
    real_open = open

    def fake_open(path, *args, **kwargs):
        text = str(path)
        if text.startswith("/sys/class/tty/"):
            return real_open(sysfs / text[len("/sys/class/tty/"):], *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    for name, type_value in (("ttyS0", "0"), ("ttyS1", "4"), ("ttyAMA0", "22")):
        (sysfs / name).mkdir(parents=True)
        (sysfs / name / "type").write_text(type_value + "\n", encoding="utf-8", newline="\n")
    (sysfs / "ttyACM0").mkdir(parents=True)   # USB CDC: no `type` attribute at all

    assert serial_link._is_absent_uart("/dev/ttyS0")        # PORT_UNKNOWN: the phantom
    assert not serial_link._is_absent_uart("/dev/ttyS1")    # 16550A: a real on-chip UART
    assert not serial_link._is_absent_uart("/dev/ttyAMA0")  # PL011 on a Pi
    assert not serial_link._is_absent_uart("/dev/ttyACM0")  # no attribute: always kept
    # A device string that is not a bare tty name must not reach the filesystem check.
    assert not serial_link._is_absent_uart("socket://127.0.0.1:9900")
    assert not serial_link._is_absent_uart("/dev/../etc/passwd")


# -- CLI --json contract (SPEC 4) -----------------------------------------------------


def test_only_the_documented_commands_emit_jsonl() -> None:
    """SPEC 4 exempts named commands from "exactly one JSON object", and missed one.

    `mcu can dump` prints one object per frame, exactly like `mcu tail`, and for the same
    reason: its `-f` form is an unbounded live stream. The exemption sentence was corrected
    for `tail` in the previous round while `can dump` went unlisted, so the sweep read as
    passing with a live instance in it.

    Enumeration is what failed, so the enumeration is pinned here rather than re-read: a
    per-row emitter is `out_json` inside a loop, and a new one fails this test until SPEC
    names it deliberately.
    """
    import ast
    import pathlib

    src = pathlib.Path(cli_module.__file__).read_text(encoding="utf-8")
    emitters = set()
    for fn in ast.walk(ast.parse(src)):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for loop in (n for n in ast.walk(fn) if isinstance(n, ast.For | ast.While)):
            for call in ast.walk(loop):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "out_json"
                ):
                    emitters.add(fn.name)

    # `log_export` writes its JSONL through a shared text branch rather than a loop over
    # out_json, so it is documented in SPEC but not detectable by this shape.
    # `_tail_snapshot` is where `mcu tail` prints its recent-lines snapshot: the follow
    # path opens /ws before fetching it, so the loop lives in a helper the command calls
    # rather than in the command body. Same emitter, same SPEC exemption.
    assert emitters == {"_tail_snapshot", "can_dump"}

    spec = (pathlib.Path(__file__).parents[2] / "docs" / "SPEC.md").read_text(encoding="utf-8")
    for documented in ("`mcu log export`", "`mcu tail`", "`mcu can dump`"):
        assert documented in spec


# -- August 2026 round ----------------------------------------------------------------


def test_a_dead_store_writer_fails_writes_instead_of_hanging(tmp_path) -> None:
    """A writer that exits left submit_line awaiting a future nobody would ever resolve.

    The lifespan's shutdown awaits add_line under `suppress(Exception)`, which cannot catch
    a hang: the daemon needed SIGKILL, and the pid record and capture lock leaked with it.
    """
    from mcuscope.store import StoreError

    async def run() -> None:
        store = Store(str(tmp_path / "dead.db"))
        await store.start()
        assert store.writer_alive
        store._writer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await store._writer_task
        assert not store.writer_alive

        with pytest.raises(StoreError):
            await asyncio.wait_for(
                store.add_line(ts=time.time(), port="A", dir="rx", chan="debug",
                               seq=None, raw="after"),
                timeout=5.0,
            )
        # The lifespan's own shutdown sequence, in order, must still complete.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(store.stop_session(), timeout=5.0)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                store.add_line(ts=time.time(), port="", dir="-", chan="sys",
                               seq=None, raw="daemon stop"),
                timeout=5.0,
            )
        await asyncio.wait_for(store.stop(), timeout=5.0)

    asyncio.run(run())


async def test_status_reports_a_dead_writer(stack) -> None:
    """`/status` moved no field when capture had stopped entirely; writer_alive does."""
    import httpx

    assert httpx.get(f"{stack.base_url}/status", timeout=5.0).json()["writer_alive"] is True
    store = stack.app.state.store
    task = store._writer_task
    task.get_loop().call_soon_threadsafe(task.cancel)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if httpx.get(f"{stack.base_url}/status", timeout=5.0).json()["writer_alive"] is False:
            return
        await asyncio.sleep(0.05)
    pytest.fail("/status still reported a live writer after the writer task was killed")


async def test_detach_carries_the_drops_stop_itself_counted(tmp_path) -> None:
    """The carried snapshot was taken before port.stop(), which is what counts the lines
    stranded in the rx queue: those drops vanished on the next attach, erasing the record
    a flaky link is being reconnected because of."""
    from mcuscope.serial_link import PortManager

    store = Store(str(tmp_path / "carry.db"))
    await store.start()
    pm = PortManager(store, asyncio.get_running_loop())
    try:
        port = await pm.attach("p1", UNOPENABLE)
        port._consumer_task.cancel()   # stands in for "the store is behind"
        with contextlib.suppress(asyncio.CancelledError):
            await port._consumer_task
        port._rx_lines.extend((time.time(), f"line {i}") for i in range(7))
        port.rx_dropped = 3
        await pm.detach("p1")
        assert port.rx_dropped == 10    # 3 already counted + 7 stranded, counted by stop()
        port2 = await pm.attach("p1", UNOPENABLE)
        assert port2.rx_dropped == 10
    finally:
        await pm.stop_all()
        await store.stop()


async def test_a_blocking_write_does_not_freeze_the_event_loop(tmp_path) -> None:
    """pyserial's write blocks for up to WRITE_TIMEOUT when the target asserts flow
    control, and it was called inline from send_raw/send_command: the whole daemon (every
    other port, every request, the WebSocket pumps) stopped for up to 2 s per line."""
    from mcuscope.serial_link import SerialPort

    store = Store(str(tmp_path / "wr.db"))
    await store.start()
    port = SerialPort(store, asyncio.get_running_loop(), "board", device=UNOPENABLE)
    ticks = 0
    progressed: list[bool] = []

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    def blocking_write(data: bytes) -> None:
        before = ticks
        time.sleep(0.1)             # the driver holding the write, as flow control does
        progressed.append(ticks > before)

    port._write_bytes = blocking_write
    tick_task = asyncio.create_task(ticker())
    try:
        await asyncio.wait_for(port.send_raw("ping"), timeout=10.0)
    finally:
        tick_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tick_task
        await store.stop()
    assert progressed == [True], "the loop made no progress while the write was in flight"


async def test_attach_does_not_hold_the_manager_lock_across_the_prime_query(
    tmp_path, monkeypatch
) -> None:
    """prime_plot_defs is a match query carrying the full 30 s budget. Run under the
    manager lock, hostile /lines?match= traffic queued every attach, detach and the
    stop_all that lifespan shutdown depends on."""
    from mcuscope import serial_link

    store = Store(str(tmp_path / "prime.db"))
    await store.start()
    pm = serial_link.PortManager(store, asyncio.get_running_loop())
    gate = asyncio.Event()

    async def slow_prime(self) -> None:
        await gate.wait()

    try:
        await pm.attach("a", UNOPENABLE)
        monkeypatch.setattr(serial_link.SerialPort, "prime_plot_defs", slow_prime)
        attaching = asyncio.create_task(pm.attach("b", UNOPENABLE))
        await asyncio.sleep(0)  # let the attach reach the prime
        assert await asyncio.wait_for(pm.detach("a"), timeout=5.0) is True
        gate.set()
        await asyncio.wait_for(attaching, timeout=5.0)
    finally:
        gate.set()
        await pm.stop_all()
        await store.stop()


async def test_a_stalled_write_does_not_freeze_stop(tmp_path) -> None:
    """stop()'s join-timeout branch closed the handle under _write_lock on the loop thread.

    Since the write moved to a worker (the test above), that lock can be held for a whole
    WRITE_TIMEOUT, so a detach, a reconnect or shutdown landing during a stalled write
    stopped the entire daemon until the driver let go.
    """
    from mcuscope import serial_link

    store = Store(str(tmp_path / "stall.db"))
    await store.start()
    port = serial_link.SerialPort(store, asyncio.get_running_loop(), "board", device=UNOPENABLE)
    closed: list[bool] = []
    by_progress: list[bool] = []
    held = threading.Event()
    release = threading.Event()
    ticks = 0
    target: int | None = None

    class _StuckLink:
        def cancel_read(self) -> bool:
            return False

        def close(self) -> None:
            closed.append(True)

    class _StuckReader:
        """A reader that outlives its join deadline: the branch that closes the handle."""

        def join(self, timeout: float | None = None) -> None:
            nonlocal target
            target = ticks + 200   # loop iterations owed while the close is pending

        def is_alive(self) -> bool:
            return True

    # The write lock is released by loop *progress*, never by the clock: the holder lets go
    # only once the ticker has run 200 more iterations, so a close that blocks the loop
    # never gets its release and falls back to the 5 s backstop, which is the failure.
    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            if target is not None and ticks >= target:
                release.set()
            await asyncio.sleep(0)

    def hold_the_write_lock() -> None:
        with port._write_lock:
            held.set()
            by_progress.append(release.wait(timeout=5.0))

    port._link = _StuckLink()
    port._thread = _StuckReader()
    holder = threading.Thread(target=hold_the_write_lock, daemon=True)
    holder.start()
    assert held.wait(timeout=5.0)
    tick_task = asyncio.create_task(ticker())
    try:
        await port.stop()
    finally:
        release.set()
        holder.join(timeout=5.0)
        tick_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tick_task
        await store.stop()
    assert by_progress == [True], "the loop made no progress while the close waited"
    assert closed == [True], "the handle must still be closed once the lock is free"


async def test_concurrent_raw_sends_are_single_flight_per_port(tmp_path) -> None:
    """send_raw had no serialisation, so N concurrent POST /send against a target that had
    deasserted flow control parked N executor workers inside the write for 2 s each."""
    from mcuscope.serial_link import SerialPort

    store = Store(str(tmp_path / "raw.db"))
    await store.start()
    port = SerialPort(store, asyncio.get_running_loop(), "board", device=UNOPENABLE)
    guard = threading.Lock()
    inflight = 0
    peak = 0

    def slow_write(data: bytes) -> None:
        nonlocal inflight, peak
        with guard:
            inflight += 1
            peak = max(peak, inflight)
        time.sleep(0.05)
        with guard:
            inflight -= 1

    port._write_bytes = slow_write
    try:
        await asyncio.wait_for(
            asyncio.gather(*(port.send_raw(f"ping {i}") for i in range(4))), timeout=20.0
        )
    finally:
        await store.stop()
    assert peak == 1, f"{peak} raw writes were in flight at once"
    assert port.lines_tx == 4


async def test_an_attach_racing_stop_all_does_not_start_an_orphan_port(
    tmp_path, monkeypatch
) -> None:
    """The prime query runs before the manager lock, so stop_all could drain every port
    while an attach sat in it; the attach then started a reader thread and a device handle
    with no owner, against a store that was about to stop."""
    from mcuscope import serial_link

    store = Store(str(tmp_path / "race.db"))
    await store.start()
    pm = serial_link.PortManager(store, asyncio.get_running_loop())
    gate = asyncio.Event()
    started: list[str] = []

    async def slow_prime(self) -> None:
        await gate.wait()

    real_start = serial_link.SerialPort.start

    def counting_start(self) -> None:
        started.append(self.alias)
        real_start(self)

    monkeypatch.setattr(serial_link.SerialPort, "prime_plot_defs", slow_prime)
    monkeypatch.setattr(serial_link.SerialPort, "start", counting_start)
    try:
        attaching = asyncio.create_task(pm.attach("b", UNOPENABLE))
        await asyncio.sleep(0)                       # let the attach reach the prime
        await asyncio.wait_for(pm.stop_all(), timeout=5.0)
        gate.set()
        with pytest.raises(serial_link.PortError):
            await asyncio.wait_for(attaching, timeout=5.0)
    finally:
        gate.set()
        await store.stop()
    assert pm.list() == []
    assert started == [], "a port started after stop_all had drained the manager"


def test_export_failure_surfaces_its_own_error(tmp_path) -> None:
    """The finally ran DETACH inside the open transaction, which raises and replaced the
    real insert error with "cannot DETACH database within transaction"."""

    async def run() -> str:
        store = Store(str(tmp_path / "src.db"))
        await store.start()
        row = await store.add_line(ts=time.time(), port="A", dir="rx", chan="debug",
                                   seq=None, raw="hello")
        await store.stop()
        return row["id"]

    line_id = asyncio.run(run())
    bad_session = {
        "id": 1, "name": object(),   # unbindable: fails the sessions INSERT mid-transaction
        "note": None, "started_ts": 0.0, "ended_ts": None,
        "start_id": line_id, "end_id": line_id, "auto": 0,
    }
    store = Store(str(tmp_path / "src.db"))
    # Named by the message, not the class: the masking DETACH error is a sqlite3.Error too.
    with pytest.raises(sqlite3.Error, match="binding parameter"):
        store.export_session_db(
            str(tmp_path / "out.db"), id_from=line_id, id_to=line_id, session=bad_session
        )


async def test_a_dead_ws_pump_closes_the_socket(stack, monkeypatch) -> None:
    """The receive loop kept the socket open and apparently healthy after the pump died,
    so a client sat on a live connection that would never deliver another row."""
    import websockets

    store = stack.app.state.store

    def boom(q):
        raise RuntimeError("pump is dead")

    url = stack.base_url.replace("http", "ws") + "/ws"
    async with websockets.connect(url) as ws:
        await asyncio.wait_for(ws.recv(), 5.0)
        monkeypatch.setattr(store, "take_dropped", boom)   # raises on the next row
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            while True:
                await asyncio.wait_for(ws.recv(), 10.0)


def test_a_lock_dir_that_cannot_be_written_is_a_startup_failure(tmp_path, monkeypatch,
                                                                capsys) -> None:
    """Only LockError was handled, so a read-only or full data dir left an OSError
    traceback at the user instead of the one-line startup failure every other cause gets."""
    from mcuscope import daemon as daemon_mod
    from mcuscope.lockfile import CaptureLock

    def raise_oserror(self, timeout: float = 2.0) -> None:
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(CaptureLock, "acquire", raise_oserror)
    monkeypatch.setenv("MCUSCOPED_CONFIG", str(tmp_path / "no-such-config.toml"))
    assert daemon_mod.main(["--port", "8558"]) == 1
    assert capsys.readouterr().err.startswith("mcuscoped: cannot claim ")


def test_port_override_is_bounded_like_the_config_key(tmp_path, monkeypatch, capsys) -> None:
    """`--port 99999` bypassed the 1..65535 bound the config file gets and failed much
    later, from inside the bind, naming neither the flag nor the reason."""
    from mcuscope import daemon as daemon_mod

    monkeypatch.setenv("MCUSCOPED_CONFIG", str(tmp_path / "no-such-config.toml"))
    # 0 is the trap: a truthiness guard reads it as "no override" and starts on the
    # config port, refusing nothing.
    for bad in ("99999", "0", "-1"):
        assert daemon_mod.main(["--port", bad]) == 1
        assert "--port must be 1..65535" in capsys.readouterr().err
