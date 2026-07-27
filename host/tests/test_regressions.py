"""Regression tests for defects found in the July 2026 review pass.

Each test names the defect it pins. They live together rather than scattered through the
topical suites because every one of them documents a behaviour that looked correct and
was not, and the shared context is worth more than the filing.
"""

from __future__ import annotations

import asyncio
import sqlite3

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
