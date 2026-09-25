"""SQLite capture storage (SPEC 3.5).

One writer connection, touched only from the event-loop thread. Writes go through a
single asyncio writer task draining a queue, so the ingestion path never blocks on
disk and row ids/broadcasts are assigned at one serialization point. Bounded reads run
on the loop; heavy ones go through `_offload` to per-worker read connections.

The serial reader threads never touch SQLite: they hand bytes to the loop via
`loop.call_soon_threadsafe`, and the loop-side consumer queues each line with
`submit_line_nowait` (falling back to `submit_line` when the queue is full).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

# Third-party `regex`, not stdlib `re`, for every USER-supplied pattern: it releases the
# GIL while matching and supports a real timeout, neither of which `re` does. Internal
# patterns elsewhere in the package stay on `re`. See _make_regexp.
import regex

from . import protocol as p

SCHEMA = """
CREATE TABLE IF NOT EXISTS lines(
  id     INTEGER PRIMARY KEY,
  ts     REAL    NOT NULL,
  port   TEXT    NOT NULL,
  dir    TEXT    NOT NULL CHECK(dir IN ('rx','tx','-')),
  chan   TEXT    NOT NULL CHECK(chan IN ('debug','cmd','resp','event','marker','sys')),
  seq    INTEGER,
  raw    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lines_ts ON lines(ts);
-- Every /lines query orders by id, never by ts, so the channel index must carry id as its
-- second column: with (chan, ts) the planner picked the index and then sorted the whole
-- matching set into a temp b-tree. Measured on a 3M-row capture: `--chan debug` went from
-- 810 ms to 0.2 ms, `--port X --chan Y` from 420 ms to 0.2 ms, with no query regressing
-- and no extra space. The superseded index is dropped after the new one exists, so an
-- interrupted upgrade never leaves the table with neither. (idx_lines_ts stays: the
-- retention sweep selects by ts.)
CREATE INDEX IF NOT EXISTS idx_lines_chan_id ON lines(chan, id);
DROP INDEX IF EXISTS idx_lines_chan_ts;
-- `port` alone had no index, so `/lines?port=` with no `chan` planned as a full scan of
-- the table btree - and it runs inline on the event loop, because query_lines_safe only
-- offloads a query carrying `match`. The cost is invisible on a busy port, where the
-- LIMIT fills from the newest rows, and paid in full on a *quiet* one, which is the
-- normal case: a board that is silent when idle still gets polled. Measured at 1M rows,
-- no ANALYZE: busy 0.3 ms, quiet 80 ms, absent 81 ms, linear in table size. With this
-- index all three are under 0.3 ms, and 200k inserts stayed within noise (1.31 s against
-- 1.43 s), because one more integer-keyed index on an append-only table is nearly free.
CREATE INDEX IF NOT EXISTS idx_lines_port_id ON lines(port, id);
-- `port` with `chan`: neither index above serves both a quiet port with a busy channel
-- and a busy port with a rare one (either choice walked millions of rows on the loop at
-- 6M lines). This one seeks both columns; query_lines and count_lines name it with
-- INDEXED BY, since with a `chan IN (...)` list the planner otherwise takes the port index.
CREATE INDEX IF NOT EXISTS idx_lines_port_chan_id ON lines(port, chan, id);
-- The host's own rows (`dir` tx and '-', a small minority). No index carries `dir`, so
-- count_lines counts a verdict's rx-only window as every row less these: 0.9 s against
-- 47 ms for a whole 6M-line capture with the `dir` term read off the table.
CREATE INDEX IF NOT EXISTS idx_lines_host ON lines(id) WHERE dir <> 'rx';

CREATE TABLE IF NOT EXISTS can_frames(
  line_id INTEGER PRIMARY KEY REFERENCES lines(id) ON DELETE CASCADE,
  tick_ms INTEGER,
  bus     INTEGER NOT NULL DEFAULT 1,
  can_id  INTEGER NOT NULL,
  ext     INTEGER NOT NULL DEFAULT 0,
  rtr     INTEGER NOT NULL DEFAULT 0,
  dlc     INTEGER NOT NULL,
  data    BLOB
);
CREATE INDEX IF NOT EXISTS idx_can_id_line ON can_frames(can_id, line_id);

CREATE TABLE IF NOT EXISTS plot_points(
  line_id INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
  tick_ms INTEGER,
  sid     TEXT,                -- NULL for ad-hoc !p points
  name    TEXT NOT NULL,
  value   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plot_name_line ON plot_points(name, line_id);
-- The FK cascade from lines deletes by line_id alone, which (name, line_id) cannot serve:
-- without this index every retention chunk full-scans plot_points, so the cost is
-- O(chunk x plot_points). Measured on a 60k-line capture, one chunk against 200k points
-- took 97 s and blocked the event loop; with this index, 0.03 s.
CREATE INDEX IF NOT EXISTS idx_plot_line ON plot_points(line_id);

-- A session is a named span of the one capture timeline, stored as an id range rather
-- than a column on every line: nothing is written per row, existing captures need no
-- migration, and scoping a query to a session rides the primary key for free. The cost
-- is that sessions cannot overlap or nest - starting one closes the previous.
-- AUTOINCREMENT, so an id is never handed out twice: a plain rowid frees the newest id
-- when its row is deleted, which the daemon does on every quiet run (an empty automatic
-- session is dropped on close), and a client holding that id then addressed a different
-- run - including `DELETE /sessions/1?data=true`. Same rule as lines.id.
CREATE TABLE IF NOT EXISTS sessions(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT    NOT NULL,
  note       TEXT    NOT NULL DEFAULT '',
  started_ts REAL    NOT NULL,
  ended_ts   REAL,                       -- NULL while the session is running
  start_id   INTEGER NOT NULL,           -- first lines.id in the session (inclusive)
  end_id     INTEGER,                    -- last lines.id (inclusive); NULL while running
  auto       INTEGER NOT NULL DEFAULT 0  -- opened by the daemon, not named by anyone
);
CREATE INDEX IF NOT EXISTS idx_sessions_name ON sessions(name, id);
-- `active_session` asks for the newest row with `ended_ts IS NULL`, and it runs on the
-- event loop from GET /status and four other handlers. Without an index the predicate is
-- not sargable, so the LIMIT short-circuits only while a session is running and the quiet
-- case (none active) reads every row - and `sessions` is never trimmed by retention.
-- Partial, so it holds at most one row and costs nothing to maintain.
CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(id) WHERE ended_ts IS NULL;

-- Small key/value side table. Its only key so far is `capture`: an opaque token
-- identifying this id space, handed to every client so none of them has to guess from id
-- arithmetic whether the rows it holds still belong to the stream it is reading (SPEC 3.4).
CREATE TABLE IF NOT EXISTS meta(
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# (index, table) for every index SCHEMA creates; start() names the ones an older capture
# lacks before building them.
_SCHEMA_INDEXES = re.findall(r"CREATE INDEX IF NOT EXISTS (\w+) ON (\w+)", SCHEMA)
# The start and end of that build in the daemon's log; `mcu daemon start` matches them.
INDEX_BUILD_NOTICE = "building index"
INDEX_BUILT_NOTICE = "built index"

# Columns added after the first release, applied to an existing capture with ALTER TABLE.
# `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so a schema
# change needs this list as well as the definition above.
_MIGRATIONS = (
    ("sessions", "auto", "ALTER TABLE sessions ADD COLUMN auto INTEGER NOT NULL DEFAULT 0"),
    # SPEC 2.4 bus digit; rows from before multi-bus support were all bus 1.
    ("can_frames", "bus", "ALTER TABLE can_frames ADD COLUMN bus INTEGER NOT NULL DEFAULT 1"),
)

def _mint_capture_id() -> str:
    """A fresh capture identity. Opaque to clients: only equality is ever tested."""
    return uuid.uuid4().hex


def _lines_index(port: str | None, chans: list[str] | None) -> str:
    """The FROM-clause index hint a `lines` read filtered by `port` and `chan` needs.

    Without it, a `chan IN (...)` list sends the planner to idx_lines_port_id, which walks
    every row of a busy port for a rare channel (4.4 s at 6M lines); a single channel
    already gets the covering index, so the hint changes no plan there.
    """
    return " INDEXED BY idx_lines_port_chan_id" if port is not None and chans else ""


def _in_schema(conn: sqlite3.Connection, name: str) -> bool:
    """Whether the capture already has a table or index of this name."""
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name=?", (name,)
    ).fetchone() is not None


_EXPORT_CHUNK = 10_000     # rows per page of a streamed plot export
_EXPORT_PAGE = 1000        # rows per page when a streaming export pages on the id cursor
_RETENTION_CHUNK = 5_000   # rows deleted per retention DELETE, committed one chunk at a time
# Pause between delete chunks. sleep(0) only yielded one loop iteration, which the writer
# used to take a single batch off the queue; 5 ms lets it drain what a chunk delayed.
_CHUNK_YIELD_S = 0.005
_VACUUM_PAGES = 2_000      # most pages one _reclaim_pages call hands back
_VACUUM_STEP_PAGES = 64    # pages per incremental_vacuum statement inside that call
_RECLAIM_BUDGET_S = 0.02   # a _reclaim_pages call starts no new step past this
_RECLAIM_MIN_PAGES = 256   # freelist below this is not worth a reclaim (1 MB at 4 kB pages)
_JOURNAL_SIZE_LIMIT = 64 * 1024 * 1024   # bytes the -wal file is truncated to after a checkpoint

# How far below a window's time floor its id floor is sought (see _window_id_floor). `ts` is
# stamped before the line queues (the port's rx queue, RX_QUEUE_MAX, then the write queue,
# _WRITE_QUEUE_MAX), so a row can commit after a later-stamped one: at the writer's ~15k
# lines/s both queues full drain in about 1.4 s per port. The window is exact while that
# inversion, or a backwards clock step, stays under this; past it the writer announces the
# episode in a sys row (_check_stamp_order). The cost is reading up to this
# many seconds of rows below the floor when the window holds fewer than `limit` rows
# (0.2 ms per 1000 rows at 6M lines, so at most about 30 ms at the writer's rate).
WINDOW_TS_SLACK_S = 10.0


def fold_breaks(raw: str) -> str:
    """`raw` as stored: CR and LF folded to a space, so one row is one line."""
    if "\n" in raw or "\r" in raw:
        raw = raw.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return raw


def _reclaim_pages(conn: sqlite3.Connection) -> None:
    """Hand freed pages back to the filesystem, bounded, and actually stepping.

    `conn.execute("PRAGMA incremental_vacuum")` reclaims exactly ONE page. The pragma
    yields a row per page freed, and sqlite3 only steps the statement as rows are
    consumed, so an unconsumed execute() advances it once and stops. Measured on a
    20.5 MB capture with 5012 free pages: execute() alone took the freelist to 5011 and
    left the file byte-identical; fetching took it to 0 and the file to 12 kB. The size
    cap was therefore trimming rows correctly and returning ~0.02% of the space, with
    nothing on any surface saying so - the same request-versus-result shape as the
    auto_vacuum defect this mechanism was built to fix.

    executescript, not execute().fetchall(), because on Python 3.11 the pragma yields no
    rows at all: fetchall() gets an empty list, the statement is stepped once and one page
    comes back, which is the very defect above wearing the fetch. executescript steps it
    to completion on every supported version (measured 3.11/3.12/3.13: 4454 -> 2454).

    Bounded per call because every caller runs on the event loop: an unbounded reclaim is
    O(freelist), and a capture that has plateaued has a large one. The bound is in time as
    well as pages: the cost per page is not steady (2000 pages took 16 ms on a small
    capture and up to 1.2 s after a 1.5M-line trim of a 6M-line one), so the pages go in
    small steps and no step starts once `_RECLAIM_BUDGET_S` has passed. Because a call
    is bounded, `sweep_tick` calls this whenever the freelist is large, not only after a
    trim, so a backlog drains over successive ticks.
    """
    deadline = time.perf_counter() + _RECLAIM_BUDGET_S
    left = _VACUUM_PAGES
    while left > 0:
        step = min(_VACUUM_STEP_PAGES, left)
        conn.executescript(f"PRAGMA incremental_vacuum({step});")
        conn.commit()
        left -= step
        if time.perf_counter() >= deadline:
            return
_WRITE_QUEUE_MAX = 10_000  # bound the write queue so a stalled writer cannot eat RAM forever
_SIZE_CHECK_S = 60         # seconds between size-cap checks (see _retention_loop)
_RETENTION_TICKS = 60      # size-cap ticks per age sweep, i.e. hourly
MAX_SUBSCRIBERS = 256      # cap fan-out queues so connect/disconnect churn cannot eat RAM
# The CLI maps a 503 to exit 3 by the `daemon is shutting down` prefix; keep it.
SUBSCRIBERS_CLOSED_MSG = "daemon is shutting down; no new watch can start"

# Rows one commit may absorb. The writer runs on the event loop by design (that residency
# is what keeps broadcasts after commit, the Python-owned id sequence uninterleaved, and
# retention/vacuum tasks out of an open writer transaction), so its per-iteration cost is
# loop latency for everything else. Without a cap that cost scales with however full the
# queue happens to be, up to _WRITE_QUEUE_MAX; with it the insert half is bounded by
# construction. A backlog is not delayed, only split across successive iterations.
_MAX_BATCH_ROWS = 1_000

# Warn when one commit exceeds this. The cap above bounds the insert half only: a commit
# can still spike when SQLite checkpoints the WAL (wal_autocheckpoint, default 1000 pages,
# fsyncs), and that depends on WAL backlog rather than on batch size. Contended or slow
# media (antivirus-scanned Windows disks, SD cards) is where that tail shows up, so make it
# observable instead of theoretical.
_SLOW_COMMIT_S = 0.1

# Commit coalescing. Every commit rewrites the hot tail page of each index, so a commit per
# small batch wrote the WAL at about 20x the captured bytes. Above this ingest rate the
# writer holds a commit until `_COMMIT_INTERVAL_S` after the previous one; below it, and
# whenever a full batch is already waiting, it commits at once. A hold that collected
# nothing means the writes come from a caller awaiting each row in turn, which a hold only
# slows, so holds then stop for `_HOLD_BACKOFF_S`.
# The rate is an exponential average over `_RATE_TAU_S`, not one commit's lines over the
# gap since the last: two commits a few ms apart (an awaited row beside a 50 lines/s
# stream, or a 15.6 ms Windows monotonic tick reading 0) measured thousands of lines/s.
_COALESCE_RATE = 200.0      # lines/s
_RATE_TAU_S = 0.5
_COMMIT_INTERVAL_S = 0.1
_HOLD_BACKOFF_S = 1.0


def _commit_hold(rate: float, since_commit: float, queued: int) -> float:
    """Seconds the writer waits before its next batch (see _COALESCE_RATE)."""
    if rate <= _COALESCE_RATE or queued >= _MAX_BATCH_ROWS:
        return 0.0
    return max(0.0, _COMMIT_INTERVAL_S - since_commit)

# SQLite's largest INTEGER, standing in for the upper bound of a session still running.
# COALESCE(end_id, this) keeps that bound a constant the planner can seek to; `end_id IS
# NULL OR id <= end_id` gave it a lower bound only, so counting a session's lines scanned
# to the end of the table (1M lines, 50 sessions: 2060 ms against 88 ms).
_MAX_LINE_ID = 9223372036854775807

# `GET /sessions` (see Store.list_sessions). Module level so a test can EXPLAIN the exact
# statement the daemon runs: the counts it returns are pinned already, the plan is not.
SESSION_LIST_SQL = (
    "SELECT s.id, s.name, s.note, s.started_ts, s.ended_ts, s.start_id, s.end_id, "
    "  s.auto, "
    "  (SELECT COUNT(*) FROM lines l WHERE l.id >= s.start_id "
    f"     AND l.id <= COALESCE(s.end_id, {_MAX_LINE_ID})) AS lines "
    "FROM sessions s ORDER BY s.id DESC LIMIT ?"
)

log = logging.getLogger(__name__)


class StoreError(RuntimeError):
    """A write could not be persisted (insert or commit failure)."""


class MatchBudgetExceeded(StoreError):
    """A user-supplied regex hit its time budget and was stopped (see _make_regexp).

    Surfaced to the client as a 400: the fault is in the submitted pattern. It must not
    become a CLI exit 2, which for `mcu wait` already means "pattern valid, nothing
    matched in the window" - conflating the two would corrupt scripted flows.
    """


@dataclass
class _WriteReq:
    row: dict[str, Any]
    can: dict[str, Any] | None
    plot: list[p.PlotPoint] | None
    future: asyncio.Future


class _PlotStat:
    """One (port, name) entry of the plot channel summary (see Store._plot_summary)."""

    __slots__ = ("sid", "last_value", "last_tick", "last_ts", "last_line_id", "count")

    def __init__(self, sid, last_value, last_tick, last_ts, last_line_id, count) -> None:
        self.sid = sid
        self.last_value = last_value
        self.last_tick = last_tick
        self.last_ts = last_ts
        self.last_line_id = last_line_id
        self.count = count


@dataclass
class _Drain:
    """A barrier in the write queue: the writer resolves it once the lines ahead of it have
    been inserted and have their ids.

    Not a write, so it never reaches the database and is never counted as a lost line.
    `start_session` is what needs it (see `drain_writes`).
    """

    future: asyncio.Future


def _resolve_drain(item: _Drain) -> None:
    if not item.future.done():
        item.future.set_result(None)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Bring a pre-existing capture up to the current schema. Idempotent, safe on a new file."""
    for table, column, ddl in _MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if cols and column not in cols:
            conn.execute(ddl)
    _rebuild_sessions_for_autoincrement(conn)


def _rebuild_sessions_for_autoincrement(conn: sqlite3.Connection) -> None:
    """Give an existing capture's `sessions.id` the AUTOINCREMENT it was created without.

    AUTOINCREMENT cannot be added by ALTER TABLE, so the table is rebuilt once, ids and
    all; `sqlite_sequence` then carries the high-water mark across daemon runs, which an
    in-memory counter could not. Runs after the column migrations above, so the copy sees
    every column. See the SCHEMA comment for what reuse cost.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sessions'"
    ).fetchone()
    if row is None or "AUTOINCREMENT" in str(row[0]).upper():
        return
    cols = "id, name, note, started_ts, ended_ts, start_id, end_id, auto"
    # One transaction, and no executescript: a rebuild that is not atomic loses every session
    # row if the process dies mid-way, and silently, because the next open recreates an empty
    # `sessions` from SCHEMA and the AUTOINCREMENT guard above then reports the work done.
    # `executescript` cannot be used inside it - it commits any pending transaction first -
    # so the new table is built under its own name from SCHEMA's own text (one source of
    # truth) and renamed into place once the copy is in.
    create = _schema_statement("CREATE TABLE IF NOT EXISTS sessions(")
    # Every index on the table, or the rebuild silently drops the ones it forgets: DROP
    # TABLE takes them with it and only what is recreated here comes back.
    indexes = [
        _schema_statement("CREATE INDEX IF NOT EXISTS idx_sessions_name"),
        _schema_statement("CREATE INDEX IF NOT EXISTS idx_sessions_active"),
    ]
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(create.replace("IF NOT EXISTS sessions(", "sessions_autoinc(", 1))
        conn.execute(
            f"INSERT INTO sessions_autoinc({cols}) SELECT {cols} FROM sessions"
        )
        # The indexes go with the old table, so they must be dropped before the rename or
        # `CREATE INDEX IF NOT EXISTS` quietly skips rebuilding them under the wanted name.
        conn.execute("DROP INDEX IF EXISTS idx_sessions_name")
        conn.execute("DROP INDEX IF EXISTS idx_sessions_active")
        conn.execute("DROP TABLE sessions")
        conn.execute("ALTER TABLE sessions_autoinc RENAME TO sessions")
        for index in indexes:
            conn.execute(index)
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def _schema_statement(marker: str) -> str:
    """The one SCHEMA statement containing `marker`, so a rebuild cannot drift from it.

    Line comments are stripped before splitting: SCHEMA's own column comments contain
    semicolons ("-- last lines.id (inclusive); NULL while running"), and splitting on `;`
    with them in place cuts a statement in half.
    """
    bare = "\n".join(line.split("--")[0] for line in SCHEMA.splitlines())
    for stmt in bare.split(";"):
        if marker in stmt:
            return stmt.strip()
    raise RuntimeError(f"SCHEMA has no statement containing {marker!r}")


MATCH_WORKERS = 4          # size of the dedicated regex pool (see match_executor)
# Ceiling on one `search()` call. This is what actually kills a catastrophic pattern, so it
# is small: no honest single-line match on a <=255-byte protocol line comes near it.
MATCH_TIMEOUT_S = 0.25
# Ceiling on all matching for one query. Deliberately generous: a legitimate scan across a
# multi-million-line capture is seconds of work at microseconds per row, and this must not
# be what stops it.
MATCH_BUDGET_S = 30.0
# Every user pattern compiles with these flags: `\d \w \s \b` read as ASCII, as the web
# UI's JavaScript reads them, so a pane and the daemon match the same lines (SPEC 3.4).
USER_REGEX_FLAGS = regex.ASCII
# Largest expanded size a user pattern may compile to (see repeat_expansion). `regex`
# expands a counted repeat into copies at compile time, so a 24-character nested repeat
# compiles for a second and a 45-character one exhausts memory; this keeps `\d{65535}`.
MAX_REPEAT_EXPANSION = 100_000

_COUNT = re.compile(r"\{(\d*)(,?)(\d*)\}")
_VERBOSE_OR_V1 = re.compile(r"\(\?[a-zA-Z0-9-]*[xV]")


class PatternTooLarge(ValueError):
    """A user pattern whose counted repeats expand past MAX_REPEAT_EXPANSION."""


def _repeat_factor(m: re.Match[str]) -> int:
    """Copies `regex` compiles for a `{m}`, `{m,n}`, `{m,}` or `{,n}` quantifier: the
    minimum, plus one for the loop when the upper bound is open (measured, 2026.7)."""
    lo = int(m[1] or 0)
    exact = not m[2]
    return max(lo, 1) if exact else lo + 1


def repeat_expansion(pattern: str) -> int:
    """An upper bound on the size `regex` expands `pattern` to at compile time.

    Each atom counts 1, a sequence or alternation sums its parts, and a quantifier
    multiplies the item it follows (`+` by 2, `*` and `?` by 1). Siblings add, so twenty
    `\\d{65535}` count 1.3M, not 65535: they cost the memory they add up to.
    Verbose mode (a comment can hide a paren) and V1 (nested sets) are not scanned: for
    those the bound is the pattern length times the product of every count, which is
    always at least the true size.
    """
    if _VERBOSE_OR_V1.search(pattern):
        bound = len(pattern) * 2 ** pattern.count("+")
        for m in _COUNT.finditer(pattern):
            bound *= _repeat_factor(m)
        return bound
    stack = [[0, 0]]         # per open group: [size so far, size of the last item]
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        factor = None
        if c == "\\":
            # `\p{L}`, `\N{name}`, `\g<name>`: the bracketed part is one atom.
            close = {"{": "}", "<": ">"}.get(pattern[i + 2 : i + 3])
            if close and pattern[i + 1 : i + 2] in tuple("pPNxugkL"):
                end = pattern.find(close, i + 3)
                i = n if end < 0 else end + 1
            else:
                i += 2
            item = 1
        elif c == "[":
            i += 1
            if pattern[i : i + 1] == "^":
                i += 1
            if pattern[i : i + 1] == "]":
                i += 1
            while i < n and pattern[i] != "]":
                if pattern[i] == "\\":
                    i += 1
                elif pattern.startswith("[:", i):
                    end = pattern.find(":]", i + 2)
                    i = i if end < 0 else end + 1
                i += 1
            i += 1
            item = 1
        elif pattern.startswith("(?#", i):
            i += 3
            while i < n and pattern[i] != ")":
                i += 2 if pattern[i] == "\\" else 1
            i += 1
            continue
        elif c == "(":
            stack.append([0, 0])
            i += 1
            continue
        elif c == ")":
            if len(stack) > 1:
                item = stack.pop()[0]
            else:
                item = 1     # unbalanced: compile refuses it, count it as a literal
            i += 1
        elif c == "|":
            stack[-1][1] = 0
            i += 1
            continue
        elif c in "*?+":
            factor = 2 if c == "+" else 1
            i += 1
        elif c == "{" and (m := _COUNT.match(pattern, i)) and (m[1] or m[2]):
            factor = _repeat_factor(m)
            i = m.end()
        else:
            item = 1
            i += 1
        top = stack[-1]
        if factor is None:
            top[0] += item
            top[1] = item
        else:
            # A lazy or possessive suffix is part of this quantifier, not another one.
            if pattern[i : i + 1] in ("?", "+"):
                i += 1
            top[0] += top[1] * (factor - 1)
            top[1] *= factor
    while len(stack) > 1:
        size = stack.pop()[0]
        stack[-1][0] += size
    return stack[0][0]


def compile_user_regex(pattern: str) -> regex.Pattern[str]:
    """Compile a user-supplied pattern: bounded, with USER_REGEX_FLAGS.

    Blocking (up to tens of ms at the bound): callers on the loop run it in a thread.
    Raises PatternTooLarge, or `regex.error` for a pattern that does not compile.
    """
    size = repeat_expansion(pattern)
    if size > MAX_REPEAT_EXPANSION:
        raise PatternTooLarge(
            f"repeats expand to {size} (max {MAX_REPEAT_EXPANSION})"
        )
    return regex.compile(pattern, USER_REGEX_FLAGS)

_match_pool: ThreadPoolExecutor | None = None
_match_pool_lock = threading.Lock()


def match_executor() -> ThreadPoolExecutor:
    """The bounded thread pool that runs every user-supplied regex.

    All regex work the API accepts (`match=` on /lines and /wait, the /assert patterns) is
    user text, and the heavy analytical reads (`_offload`) share the pool. Keeping them
    off the default executor is the point: a burst of slow scans there would stall every
    other piece of thread work the daemon does. (The serial reader join, the one wait that
    must never queue, has its own pool in `serial_link`.)

    Process-wide and never explicitly shut down: the daemon owns it for its lifetime, and
    the threads are idle between queries. A query still running at interpreter exit delays
    exit until it finishes (ThreadPoolExecutor joins its workers via atexit).
    """
    global _match_pool
    with _match_pool_lock:
        if _match_pool is None:
            _match_pool = ThreadPoolExecutor(
                max_workers=MATCH_WORKERS, thread_name_prefix="mcu-match"
            )
        return _match_pool


def _make_regexp(budget_s: float = MATCH_BUDGET_S):
    """A cached-pattern, time-budgeted REGEXP implementation for SQLite (`raw REGEXP ?`).

    User-supplied patterns run here, so this has to survive a hostile one. Running match
    queries off the event loop is NOT sufficient containment on its own: CPython's `re`
    holds the GIL for the whole of a backtrack, so `(a+)+$` against a 40-character line
    freezes the entire process, not just a pool worker (measured: a 10 ms heartbeat got 1
    tick in 2.4 s). `MAX_MATCH_LEN` does not help either, since 7 characters suffice.

    The third-party `regex` engine fixes both halves: it releases the GIL while matching
    (the same heartbeat kept every tick), and its `timeout=` genuinely interrupts a
    backtrack in progress. Two limits, because either alone has a hole - a per-call
    timeout is unbounded across millions of rows, and a whole-query budget alone would let
    one row eat all of it.

    The budget starts at the first call rather than at construction, so a closure can be
    armed when the connection is set up and still measure only the query it serves. Each
    closure carries its own `timed_out` flag: SQLite reports the raised TimeoutError to the
    caller as a generic OperationalError, and the flag is what tells a real budget stop
    from an unrelated SQL error. `window_spent` says which limit it was, because the two
    need different remedies (see _budget_error).
    """
    cache: dict[str, regex.Pattern[str]] = {}
    deadline: list[float | None] = [None]

    def regexp(pattern: str, value: str | None) -> bool:
        if value is None:
            return False
        pat = cache.get(pattern)
        if pat is None:
            pat = compile_user_regex(pattern)
            cache[pattern] = pat
        if deadline[0] is None:
            deadline[0] = time.monotonic() + budget_s
        remaining = deadline[0] - time.monotonic()
        if remaining <= 0:
            regexp.timed_out = regexp.window_spent = True
            raise TimeoutError("match budget exceeded")
        try:
            return pat.search(value, timeout=min(MATCH_TIMEOUT_S, remaining)) is not None
        except TimeoutError:
            regexp.timed_out = True
            regexp.window_spent = remaining < MATCH_TIMEOUT_S
            raise

    regexp.timed_out = False
    regexp.window_spent = False
    return regexp


def _budget_error(rx: Callable[..., bool]) -> MatchBudgetExceeded:
    """The refusal for a match query `rx` stopped, worded for the limit that stopped it."""
    if rx.window_spent:
        return MatchBudgetExceeded(
            f"match scan used its {MATCH_BUDGET_S:.0f} s budget before covering the window: "
            "the window is too large, narrow it (session, last_ms, since_id)"
        )
    return MatchBudgetExceeded(
        "match pattern exceeded the matching time budget; simplify the regex"
    )


class Store:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._queue: asyncio.Queue[_WriteReq | _Drain | None] | None = None
        self._writer_task: asyncio.Task | None = None
        self._retention_task: asyncio.Task | None = None
        self._initial_sweep_task: asyncio.Task | None = None
        self._retention_days = 10
        self._max_db_bytes = 0   # 0 disables the size cap (SPEC 3.3)
        self._min_sessions = 0   # sessions kept regardless of age (0 disables the floor)
        # (cutoff, floor_id) of the last age sweep that ran to the end: below both, nothing
        # is left to delete, so the next sweep starts its walk at that cutoff.
        self._last_age_sweep: tuple[float, int | None] | None = None
        self.lines_trimmed = 0   # lines dropped by the size cap, reported on /status
        # Writes the writer task could not persist, reported on /status (SPEC 3.4). The
        # serial layer counts a line as received before handing it here, so a failed write
        # is a line counted and lost; without this counter the only trace was a log line
        # while every health surface stayed green.
        self.write_errors = 0
        self._subscribers: dict[asyncio.Queue, str | None] = {}
        # Set by stop_subscribers: no fan-out and no new subscriber after the sentinel.
        self._subscribers_closed = False
        # The same moment as an awaitable, for a watcher busy elsewhere (a send) to race.
        self._subscribers_stopped = asyncio.Event()
        # Rows shed from a slow subscriber's queue: per queue, so the pump can announce the
        # gap in-band, and a lifetime total for /status, because a feed that is losing rows
        # while every other field reads healthy is the shape class 12 exists for.
        self._sub_dropped: dict[asyncio.Queue, int] = {}
        # Subscribers that take each row as its JSON text (the /ws pumps): the text is
        # produced once per row here and shared, not once per subscriber per frame.
        self._json_subs: set[asyncio.Queue] = set()
        self.ws_dropped = 0
        # Next `lines.id` to hand out. The daemon owns this sequence (see _insert_batch);
        # it is seeded from the file at start() and only ever moves up (_resync_next_id).
        self._next_id = 1
        # Commit coalescing state (see _commit_hold): the averaged rate as of the last commit.
        self._ingest_rate = 0.0
        self._last_commit = 0.0
        self._hold_off_until = 0.0
        # Stamp order (see _check_stamp_order): the newest `ts` stored, and the current
        # episode of rows committing past the window slack behind it.
        self._top_ts = 0.0
        self._late_rows = 0
        self._late_worst = 0.0
        self._capture_id = ""
        # Serialises the retention/size sweeps against each other. Both compute how much to
        # delete up front and then delete in yielding chunks, so two overlapping sweeps each
        # trim a target the other has already met: measured on a 200k-row capture, one sweep
        # correctly dropped 159k rows and two gathered dropped all 200k. The startup sweep
        # can still be running when the 60 s tick fires, so the overlap is routine.
        # `delete_range` takes it too: a purge frees space the sweep already counted.
        self._sweep_lock = asyncio.Lock()
        # Serialises the session-mutating methods (start_session, stop_session) against each
        # other. `stop_session` suspends at the end marker's `add_line` before `end_id` is
        # written, so two overlapping starts both saw the same active session, both closed
        # it and both opened one: sessions cannot overlap or nest (see the SCHEMA comment),
        # and the extra open row was then never closed. `delete_session` needs no lock - it
        # is synchronous and contains no await, so it cannot interleave with either.
        self._session_lock = asyncio.Lock()
        # Per-(port, name) plot channel summary, served by query_plot_channels_safe instead
        # of a GROUP BY over plot_points on every poll. Only the loop thread mutates it: the
        # writer after each committed batch, a delete by subtracting what it removed
        # (_forget_plot_points). Start, and a delete it cannot subtract, mark it dirty, and
        # the next read rebuilds it off the loop from SQL (_rebuild_plot_summary).
        self._plot_summary: dict[tuple[str, str], _PlotStat] = {}
        self._plot_dirty = True
        self._plot_lock = asyncio.Lock()
        # One cached read connection per match_executor worker (see _read_conn). The set
        # exists so stop() can close them all; `_read_epoch` retires the cached handles.
        self._read_local = threading.local()
        self._read_conns: set[sqlite3.Connection] = set()
        self._read_conns_lock = threading.Lock()
        self._read_epoch = 0

    # -- lifecycle --------------------------------------------------------------------

    def set_retention_days(self, days: int) -> None:
        """Live-apply a retention change (SPEC 3.3.1); picked up on the next sweep."""
        self._retention_days = days

    async def start(
        self, retention_days: int = 10, max_db_bytes: int = 0, min_sessions: int = 0
    ) -> None:
        self._retention_days = retention_days
        self._max_db_bytes = max(0, int(max_db_bytes))
        self._min_sessions = max(0, int(min_sessions))
        parent = os.path.dirname(self._db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        # No `regexp` function is registered here on purpose: `_make_regexp` arms its budget
        # on the first call and never re-arms, so a long-lived closure is a trap. Every match
        # query builds its own (query_lines_safe inline, _query_lines_on on a worker);
        # a direct `query_lines(match=...)` against this connection fails loudly with
        # "no such function: REGEXP" rather than raising TimeoutError 30 s in.
        # Incremental auto-vacuum lets a size-capped capture hand freed pages back to the
        # filesystem after a trim, instead of the file sitting at its high-water mark. It
        # can only be chosen before the database header is materialised, so this applies to
        # captures this daemon creates; an older one keeps its setting and simply plateaus.
        # This MUST precede journal_mode=WAL: setting the journal mode writes the header,
        # after which auto_vacuum silently stays 0 and every PRAGMA incremental_vacuum
        # below becomes a no-op (a trimmed capture then never gives space back).
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        # Read it back for the same reason as journal_mode below: the pragma reports its
        # refusal in the result set rather than raising, and on a pre-existing capture it
        # silently stays 0.
        av_row = conn.execute("PRAGMA auto_vacuum").fetchone()
        auto_vacuum = int(av_row[0]) if av_row else 0
        if auto_vacuum != 2 and self._db_path not in (":memory:", ""):
            log.warning(
                "capture %s has auto_vacuum=%d, not INCREMENTAL: freed pages are never "
                "handed back to the filesystem, so the file plateaus at its high-water "
                "mark until someone runs VACUUM on it by hand",
                self._db_path, auto_vacuum,
            )
        # This PRAGMA reports a refusal in its result set rather than raising: a filesystem
        # with no shared-memory support answers 'delete' and the whole batched-commit design
        # silently degrades to a journal per commit. Read it back and say so.
        mode_row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        mode = (mode_row[0] if mode_row else "") or ""
        if str(mode).lower() != "wal" and self._db_path not in (":memory:", ""):
            log.warning(
                "capture %s is in journal mode %r, not WAL: commits are slower and "
                "readers cannot run concurrently with the writer",
                self._db_path, mode,
            )
        # NORMAL is crash-safe under WAL (a crash can lose the last commit, never corrupt the
        # DB) and skips the per-commit fsync that FULL forces - the right tradeoff for a
        # high-rate capture tool that batches its commits.
        conn.execute("PRAGMA synchronous=NORMAL")
        # The writer ran on SQLite's 2 MB default against a capture that reaches hundreds
        # of MB, re-reading index pages per batch: 64 MB of cache is worth 4% on its own
        # (36,495 -> 38,126 rows/s at the flood rate). Read connections get their own
        # (-8000, see _open_read_conn). wal_autocheckpoint is deliberately left alone:
        # raising it improves the mean and worsens the _SLOW_COMMIT_S tail.
        conn.execute("PRAGMA cache_size=-65536")
        # Bounds the -wal file once a checkpoint passes it: without it the file stays at
        # the high-water mark a long read left behind, until restart.
        conn.execute(f"PRAGMA journal_size_limit={_JOURNAL_SIZE_LIMIT}")
        conn.execute("PRAGMA foreign_keys=ON")
        # An older capture builds the indexes it lacks here, before the daemon answers.
        # `mcu daemon start` keys its readiness wait on these two notices.
        building = [name for name, table in _SCHEMA_INDEXES
                    if _in_schema(conn, table) and not _in_schema(conn, name)]
        if building:
            log.warning("capture %s: %s %s once (about 2.5 s per million lines)",
                        self._db_path, INDEX_BUILD_NOTICE, ", ".join(building))
            t0 = time.monotonic()
        conn.executescript(SCHEMA)
        _apply_migrations(conn)
        conn.commit()
        if building:
            log.warning("capture %s: %s %s in %.1f s", self._db_path, INDEX_BUILT_NOTICE,
                        ", ".join(building), time.monotonic() - t0)
        self._conn = conn
        # Seed past any id a stored session still refers to, not just past the live rows.
        # Sessions record a start_id/end_id span and are never deleted by retention or by
        # `purge --all`, so an emptied `lines` table would restart the sequence at 1 and the
        # next run's lines would fall inside an old session's range: `session show run-alpha`
        # then returned run-beta's traffic, and `session export`/`purge --session` acted on
        # it. Ids must never be reused while anything still points at them.
        self._next_id = max(self._max_id_sql(self._conn), self._max_session_ref_id()) + 1
        # A clock stepped back across a restart leaves stored rows ahead of the new ones.
        self._top_ts = conn.execute("SELECT MAX(ts) FROM lines").fetchone()[0] or 0.0
        # The capture identity outlives the daemon process: a restart against the same file
        # continues the same id space, so a client that kept its rows across the reconnect
        # must NOT be told to throw them away. A capture created here (a fresh file, or one
        # deleted and recreated) gets a new token, which is exactly what a client needs to
        # see. An older capture predating this table gets one on first open.
        row = conn.execute("SELECT value FROM meta WHERE key = 'capture'").fetchone()
        if row is None:
            self._capture_id = _mint_capture_id()
            conn.execute("INSERT INTO meta(key, value) VALUES('capture', ?)",
                         (self._capture_id,))
            conn.commit()
        else:
            self._capture_id = str(row["value"])
        self._close_crashed_auto_session()
        self._queue = asyncio.Queue(maxsize=_WRITE_QUEUE_MAX)
        self._writer_task = asyncio.create_task(self._writer())
        self._writer_task.add_done_callback(self._writer_exited)
        # The initial sweep runs in the background: a large expired backlog must not
        # hold up daemon startup (the chunked sweep yields the loop between chunks).
        self._initial_sweep_task = asyncio.create_task(self._initial_sweep())
        self._retention_task = asyncio.create_task(self._retention_loop())

    def _close_crashed_auto_session(self) -> None:
        """Close an automatic session a crashed run left open, where that run ended.

        Its end is the capture's newest row (id and `ts`), read before this run writes
        anything, so neither the restart's time nor its rows land in the dead run. No end
        marker: one written now would carry the restart's time. Dropped when it holds no
        device traffic, as stop_session drops one.
        """
        assert self._conn is not None
        session = self.active_session()
        if session is None or not session["auto"]:
            return
        last = self._conn.execute(
            "SELECT id, ts FROM lines ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last is not None and last[0] >= session["start_id"]:
            end_id, ended_ts = int(last[0]), float(last[1])
        else:
            end_id, ended_ts = session["start_id"] - 1, session["started_ts"]
        self._conn.execute(
            "UPDATE sessions SET ended_ts = ?, end_id = ? WHERE id = ?",
            (ended_ts, end_id, session["id"]),
        )
        self._conn.commit()
        if not self._captured_traffic({**session, "end_id": end_id}):
            self.delete_session(session["id"])
        log.info("closed session %s, left open by a run that did not stop cleanly",
                 session["name"])

    async def _initial_sweep(self) -> None:
        try:
            await self._sweep_retention_async()
            await self._sweep_size_reported()
        except Exception as exc:
            log.error("startup retention sweep failed: %s", exc)

    async def stop(self) -> None:
        for task_attr in ("_initial_sweep_task", "_retention_task"):
            task = getattr(self, task_attr, None)
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._retention_task = None
        if self._queue is not None and self._writer_task is not None:
            # Bounded shutdown: a full queue behind a wedged writer means the flush can
            # never complete (a blocking put would hang here forever), and the join gets
            # a timeout for the same reason. Cancelling loses queued writes, but only in
            # a state where they were never going to land anyway.
            try:
                self._queue.put_nowait(None)  # sentinel: flush and exit
            except asyncio.QueueFull:
                log.error("store writer queue full at shutdown; cancelling writer")
                self._writer_task.cancel()
            done, _pending = await asyncio.wait({self._writer_task}, timeout=5.0)
            if not done:
                log.error("store writer did not exit within 5 s; cancelling")
                self._writer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._writer_task
            self._fail_queued("store stopped")
        self.stop_subscribers()
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._close_read_conns()

    def stop_subscribers(self) -> None:
        """Push a `None` sentinel into every subscriber queue: the capture is closing.

        Public because the daemon calls it from uvicorn's exit hook: `stop()` runs in the
        lifespan finaliser, which uvicorn reaches only after its graceful wait has already
        cancelled every parked handler, too late for the sentinel to reach anyone.

        A handler parked on `q.get()` otherwise sits there until uvicorn's graceful
        shutdown cancels it, and the client is answered with a generic 500 after the
        5 s cap (`/wait`, `/assert`, `mcu tail`). Drop-oldest to make room, as the
        fan-out does: at shutdown the sentinel matters more than one more row.

        Closes the feed too. The capture keeps committing for the graceful wait, and a
        fan-out after the sentinel sheds it from a full queue; a subscriber arriving after
        this call would never be sent one.
        """
        self._subscribers_closed = True
        self._subscribers_stopped.set()
        for q in self._subscribers:
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                    # Counted like the fan-out's shed, so /ws still announces the gap.
                    self._sub_dropped[q] = self._sub_dropped.get(q, 0) + 1
                    self.ws_dropped += 1
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(None)

    def _close_read_conns(self) -> None:
        with self._read_conns_lock:
            conns, self._read_conns = self._read_conns, set()
            self._read_epoch += 1
        for c in conns:
            with contextlib.suppress(Exception):
                c.close()

    def _writer_exited(self, task: asyncio.Task) -> None:
        """Fail what is queued when the writer dies of an unexpected exception.

        stop() drains the queue itself after its join, and a cancelled writer is that path
        or a caller taking the task down; both leave the queue to stop(). Any other exit
        would strand every queued future, and `_store_rx_batch` awaits exactly those.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        log.error("store writer died: %s", exc)
        self._fail_queued("store writer exited")

    def _fail_queued(self, reason: str) -> None:
        """Resolve the futures of anything the writer never got to.

        A cancelled writer (queue full at shutdown, or the 5 s deadline) leaves requests in
        the queue whose futures nobody completes, and `SerialPort._store_rx_batch` awaits
        exactly those - so the awaiter hangs until the loop closes and dies pending. Failing
        them turns that into an ordinary StoreError the caller already handles.
        """
        if self._queue is None:
            return
        pending = 0
        while True:
            try:
                req = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if req is None:
                continue
            if isinstance(req, _Drain):
                # A barrier is not a write: resolve it rather than failing it, or the
                # counter would report a lost line that never existed. "Everything ahead
                # of me is done" is true once the queue has been abandoned.
                _resolve_drain(req)
                continue
            if not req.future.done():
                self._fail_write(req, StoreError(reason))
                pending += 1
        if pending:
            log.warning("store: failed %d queued write(s): %s", pending, reason)

    def _fail_write(self, item: _WriteReq | None, exc: Exception) -> None:
        """Resolve one queued write as failed, counting it on the way.

        Every path that loses a line goes through here so `write_errors` is the count of
        lines the capture was handed and did not store, whatever the reason (insert,
        commit, a shutdown that left the queue undrained, or a refusal before the line was
        ever queued). `item` is None for that last case, where there is no future to
        resolve and the caller raises instead.
        """
        self.write_errors += 1
        if item is not None and not item.future.done():
            item.future.set_exception(exc)

    # -- write path -------------------------------------------------------------------

    @property
    def writer_alive(self) -> bool:
        """Whether the single writer task is running, so `/status` can report it.

        False before start() and once the task has exited for any reason. A writer that
        died is total loss of capture, and no other field on the health surface moves.
        """
        return self._writer_task is not None and not self._writer_task.done()

    async def _writer(self) -> None:
        """Drain the queue in batches: one fsync-bounded commit covers every line that was

        already waiting, instead of a commit (and fsync) per line. Each caller still gets
        its own inserted row id back via its future, and each broadcast happens after the
        single commit so subscribers never see a row that a crash could roll back.

        A batch is capped at `_MAX_BATCH_ROWS`: insert plus commit run synchronously on the
        event loop, so an uncapped batch made that stall scale with queue depth. A larger
        backlog is not held back, it is committed over successive iterations of this loop.
        """
        assert self._queue is not None
        while True:
            req = await self._queue.get()
            if req is None:
                return
            if isinstance(req, _Drain):
                _resolve_drain(req)   # nothing is in flight: the last batch is committed
                continue
            now = time.monotonic()
            hold = 0.0 if now < self._hold_off_until else _commit_hold(
                self._ingest_rate, now - self._last_commit, self._queue.qsize() + 1
            )
            if hold:
                try:
                    await asyncio.sleep(hold)
                except asyncio.CancelledError:
                    # `req` is off the queue, so _fail_queued cannot reach it.
                    self._fail_write(req, StoreError("store writer exited"))
                    raise
                if self._queue.empty():
                    self._hold_off_until = time.monotonic() + _HOLD_BACKOFF_S
            batch = [req]
            stop = False
            drain: _Drain | None = None
            while len(batch) < _MAX_BATCH_ROWS:  # absorb what is queued, up to the cap
                try:
                    nxt = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if nxt is None:
                    stop = True  # sentinel: flush this batch, then exit
                    break
                if isinstance(nxt, _Drain):
                    # Stop absorbing at the barrier: it is resolved once THIS batch has its
                    # ids, and lines queued behind it must not be pulled in ahead of that,
                    # or `_next_id` would already count them.
                    drain = nxt
                    break
                batch.append(nxt)
            notice = self._check_stamp_order(batch)
            if notice is not None:
                batch.append(notice)
            assert self._conn is not None
            try:
                try:
                    self._insert_batch(batch)
                    results = [(item, item.row, None) for item in batch]
                except Exception as exc:
                    # A single bad row aborts the whole executemany, so redo the batch one
                    # row at a time to isolate it: the others must still land. This is also
                    # the self-heal for a stale id sequence (see _insert_batch).
                    log.warning("batched insert failed (%s); retrying row by row", exc)
                    with contextlib.suppress(Exception):
                        self._conn.rollback()
                    try:
                        results = self._insert_individually(batch)
                    except Exception as exc2:
                        # The row-by-row fallback itself can fail (its resync reads MAX(id)
                        # from SQL, so a connection-level error reaches here).
                        # Letting it escape kills the writer task: this batch's futures would
                        # never resolve, every later submit_line would hang, and the queue
                        # would fill to _WRITE_QUEUE_MAX and block the serial consumer for
                        # good. Fail this batch's callers instead and keep draining.
                        log.error("row-by-row insert failed: %s", exc2)
                        for item in batch:
                            self._fail_write(item, StoreError(f"insert failed: {exc2}"))
                        if stop:
                            return
                        continue
                try:
                    t0 = time.perf_counter()
                    self._conn.commit()  # single durability point for the whole batch
                    elapsed = time.perf_counter() - t0
                    now = time.monotonic()
                    self._ingest_rate = (
                        self._ingest_rate * math.exp(-(now - self._last_commit) / _RATE_TAU_S)
                        + len(batch) / _RATE_TAU_S
                    )
                    self._last_commit = now
                    if elapsed >= _SLOW_COMMIT_S:
                        log.warning(
                            "slow capture commit: %.0f ms for %d rows",
                            elapsed * 1000, len(batch),
                        )
                except Exception as exc:
                    # A commit failure (disk full, I/O error) must not kill the writer:
                    # fail this batch's callers, roll back, and keep draining the queue.
                    log.error("batch commit failed: %s", exc)
                    with contextlib.suppress(Exception):
                        self._conn.rollback()
                    # The rolled-back ids stay spent (a harmless gap). The plot summary is
                    # untouched: it is fed only after a commit (below).
                    for item, _row, item_exc in results:
                        self._fail_write(
                            item,
                            item_exc if item_exc is not None
                            else StoreError(f"commit failed: {exc}"),
                        )
                    if stop:
                        return
                    continue
                rows = []
                for item, row, exc in results:
                    if exc is not None:
                        self._fail_write(item, exc)
                        continue
                    if not item.future.done():
                        item.future.set_result(row)
                    rows.append(row)
                    if item.plot:
                        self._note_plot(row, item.plot)
                self._broadcast_batch(rows)
                if stop:
                    return
            except Exception:
                # Anything not guarded above (a broadcast raising, say) ends the task, and
                # this batch is already off the queue, so `_writer_exited` cannot reach it:
                # its futures are only resolvable here.
                for item in batch:
                    if not item.future.done():
                        self._fail_write(item, StoreError("store writer exited"))
                raise
            finally:
                # Every exit from this batch releases the barrier, failures included: a
                # waiter that outlived the rows it was waiting for must not hang.
                if drain is not None:
                    _resolve_drain(drain)

    def _check_stamp_order(self, batch: list[_WriteReq]) -> _WriteReq | None:
        """Count rows committing more than WINDOW_TS_SLACK_S behind the newest `ts` stored.

        Past the slack a `since_ts`/`last_ms` window can miss rows (see _window_id_floor),
        so an episode of such rows is announced in the capture: a sys row when the first
        commits, and one with the count when a batch commits without any. Returns the sys
        row to append to this batch, or None.
        """
        top, late, worst, low = self._top_ts, 0, 0.0, math.inf
        for item in batch:
            ts = item.row["ts"]
            low = min(low, ts)
            if ts > top:
                top = ts
            elif top - ts > WINDOW_TS_SLACK_S:
                late += 1
                worst = max(worst, top - ts)
        self._top_ts = top
        if self._last_age_sweep is not None and low < self._last_age_sweep[0]:
            # A row older than the last age sweep's cutoff (a clock stepped back, or a
            # caller's own ts): the next sweep must walk from the start to find it.
            self._last_age_sweep = None
        if late:
            starts = not self._late_rows
            self._late_rows += late
            self._late_worst = max(self._late_worst, worst)
            if not starts:
                return None
            raw = (f"storage: rows are committing up to {worst:.1f} s behind newer "
                   f"timestamps, past the {WINDOW_TS_SLACK_S:g} s window slack; since_ts "
                   "and last_ms windows that start among them can miss rows")
        elif self._late_rows:
            raw = (f"storage: rows are back in time order; {self._late_rows} committed up "
                   f"to {self._late_worst:.1f} s behind newer timestamps")
            self._late_rows, self._late_worst = 0, 0.0
        else:
            return None
        req = self._write_req(ts=time.time(), port="", dir="-", chan="sys", seq=None,
                              raw=raw)
        # Nobody awaits it; retrieve a failure so asyncio does not log it as unhandled.
        req.future.add_done_callback(lambda f: f.cancelled() or f.exception())
        return req

    def _insert_batch(self, batch: list[_WriteReq]) -> None:
        """Insert a whole batch as one statement per table, filling in each row's id.

        The daemon is the sole writer of this database (SPEC 3.5), so it owns the `lines`
        id sequence and takes the next id in Python rather than reading `lastrowid` back
        per row. That is what makes the batch expressible as three `executemany` calls
        instead of one `execute` per line (plus one per can/plot child) - the largest
        single cost of capture once commits were batched.

        If the sequence is ever wrong - another process wrote to the same file - the
        primary-key collision surfaces as an exception here and the caller falls back to
        `_insert_individually`, which resyncs the counter first.
        """
        assert self._conn is not None
        first = self._next_id
        line_rows = []
        can_rows = []
        plot_rows = []
        for i, item in enumerate(batch):
            line_id = first + i
            r = item.row
            r["id"] = line_id
            line_rows.append(
                (line_id, r["ts"], r["port"], r["dir"], r["chan"], r["seq"], r["raw"])
            )
            for tick_ms, sid, name, value in item.plot or ():
                plot_rows.append((line_id, tick_ms, sid, name, value))
            can = item.can
            if can is not None:
                can_rows.append(
                    (line_id, can["tick_ms"], can["bus"], can["can_id"], int(can["ext"]),
                     int(can["rtr"]), can["dlc"], can["data"])
                )
        self._conn.executemany(
            "INSERT INTO lines(id, ts, port, dir, chan, seq, raw) VALUES(?,?,?,?,?,?,?)",
            line_rows,
        )
        if plot_rows:
            self._conn.executemany(
                "INSERT INTO plot_points(line_id, tick_ms, sid, name, value) VALUES(?,?,?,?,?)",
                plot_rows,
            )
        if can_rows:
            self._conn.executemany(
                "INSERT INTO can_frames(line_id, tick_ms, bus, can_id, ext, rtr, dlc, data) "
                "VALUES(?,?,?,?,?,?,?,?)",
                can_rows,
            )
        self._next_id = first + len(batch)

    def _insert_individually(
        self, batch: list[_WriteReq]
    ) -> list[tuple[_WriteReq, dict[str, Any] | None, Exception | None]]:
        """Fallback for a batch that would not go in as one statement: one row at a time.

        Each row is inserted on its own so a single bad one (a CHECK violation, a duplicate
        id) fails alone. Ids still come from the daemon's sequence, resynced first so a
        foreign row cannot collide again.
        """
        self._resync_next_id()
        results: list[tuple[_WriteReq, dict[str, Any] | None, Exception | None]] = []
        for item in batch:
            try:
                row = self._insert(self._next_id, item.row, item.can, item.plot)
                self._next_id += 1
                results.append((item, row, None))
            except Exception as exc:  # one bad insert must not lose the others
                log.warning("line insert failed: %s", exc)
                results.append((item, None, exc))
        return results

    def _resync_next_id(self) -> None:
        """Move the id sequence past any row another writer put in the file. Never down.

        SQLite's MAX(id) drops when the newest rows are deleted, and ids above it may
        already be in clients' hands (`max_id()` answers from this sequence) or inside a
        session's span; handing them out again put new lines where clients and sessions
        expected the old ones. Sessions take their ids from this sequence, so it is past
        theirs already (start() seeds it past them).
        """
        assert self._conn is not None
        self._next_id = max(self._next_id, self._max_id_sql(self._conn) + 1)

    def _insert(
        self,
        line_id: int,
        row: dict[str, Any],
        can: dict[str, Any] | None,
        plot: list[p.PlotPoint] | None = None,
    ) -> dict[str, Any]:
        """Insert one line (+ optional can/plot rows) under `line_id`.

        If a can/plot child insert fails, the freshly inserted line row is deleted
        again so the batch commit cannot persist an orphan line.
        """
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO lines(id, ts, port, dir, chan, seq, raw) VALUES(?,?,?,?,?,?,?)",
            (line_id, row["ts"], row["port"], row["dir"], row["chan"], row["seq"], row["raw"]),
        )
        try:
            self._insert_children(line_id, can, plot)
        except Exception:
            with contextlib.suppress(Exception):
                self._conn.execute("DELETE FROM lines WHERE id = ?", (line_id,))
            raise
        row["id"] = line_id
        return row

    def _insert_children(
        self,
        line_id: int,
        can: dict[str, Any] | None,
        plot: list[p.PlotPoint] | None,
    ) -> None:
        assert self._conn is not None
        if plot:
            self._conn.executemany(
                "INSERT INTO plot_points(line_id, tick_ms, sid, name, value) VALUES(?,?,?,?,?)",
                [(line_id, *pt) for pt in plot],
            )
        if can is not None:
            self._conn.execute(
                "INSERT INTO can_frames(line_id, tick_ms, bus, can_id, ext, rtr, dlc, data) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    line_id,
                    can["tick_ms"],
                    can["bus"],
                    can["can_id"],
                    int(can["ext"]),
                    int(can["rtr"]),
                    can["dlc"],
                    can["data"],
                ),
            )

    def _write_req(
        self,
        *,
        ts: float,
        port: str,
        dir: str,
        chan: str,
        seq: int | None,
        raw: str,
        can: dict[str, Any] | None = None,
        plot: list[p.PlotPoint] | None = None,
    ) -> _WriteReq:
        assert self._queue is not None
        if not self.writer_alive:
            # A dead writer never drains the queue, so the future below would never
            # resolve: every caller (including the lifespan's own shutdown rows, awaited
            # under a suppress that cannot catch a hang) would block forever. Fail fast
            # instead, so the failure is visible and shutdown still completes. The refusal
            # is counted like any other lost line: `write_errors` is what /status offers as
            # "lines handed to the capture and not stored", and a dead writer is the state
            # it exists to reveal.
            exc = StoreError("store writer is not running")
            self._fail_write(None, exc)
            raise exc
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        # One stored row is one line, and every write path converges here (serial rx,
        # POST /marker, the tx echo of a send). A `raw` carrying CR or LF renders as two
        # lines in a text or csv export and is counted as two, so the row count and the
        # file disagree about how much was captured. Folded to a space rather than
        # dropped, so the text either side stays separated.
        raw = fold_breaks(raw)
        # `id` is filled in by the writer; it leads so the row serializes in schema order.
        row = {"id": None, "ts": ts, "port": port, "dir": dir, "chan": chan,
               "seq": seq, "raw": raw}
        return _WriteReq(row=row, can=can, plot=plot, future=fut)

    def submit_line_nowait(self, **kwargs: Any) -> asyncio.Future:
        """Queue a line without suspending; raises `asyncio.QueueFull` when there is no room.

        The ingest fast path: a coroutine per line cost a wakeup and a frame for a put that
        never suspends while the queue has room. Callers fall back to `submit_line` on
        QueueFull, which is where the backpressure lives.
        """
        req = self._write_req(**kwargs)
        assert self._queue is not None
        self._queue.put_nowait(req)
        return req.future

    async def submit_line(self, **kwargs: Any) -> asyncio.Future:
        """Queue a line for the writer and return the future carrying its stored row.

        This is `add_line` without the await, so a caller holding a whole burst can queue
        every line before yielding. That is what lets the writer batch them: awaiting each
        row before queueing the next leaves the writer's queue with one item at a time, so
        the batching loop in `_writer` degenerates into a commit (and a loop wakeup) per
        line, which at a few thousand lines a second dominates the cost of capture.

        Only a full queue suspends (`put_nowait` first), so a burst that fits is queued
        without an intervening loop iteration. Keyword-only: ts, port, dir, chan, seq, raw,
        and optional can/plot (see `_write_req`).
        """
        req = self._write_req(**kwargs)
        assert self._queue is not None
        try:
            self._queue.put_nowait(req)
        except asyncio.QueueFull:
            await self._queue.put(req)
        return req.future

    async def add_line(self, **kwargs: Any) -> dict[str, Any]:
        """Enqueue a line and return the stored row (with its id): `submit_line` + await."""
        return await (await self.submit_line(**kwargs))

    async def drain_writes(self) -> None:
        """Wait until every line queued before this call has been written and given its id.

        `submit_line` returns before the writer has seen the line, so up to
        `_WRITE_QUEUE_MAX` lines can be waiting for an id at any moment. Anything reading
        `_next_id` as "the id the next captured line will take" needs those assigned first;
        `start_session` is the caller.

        A no-op with no writer running: nothing can be pending then, and the barrier would
        never be resolved.
        """
        if self._queue is None or not self.writer_alive:
            return
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._queue.put(_Drain(future=fut))
        await fut

    # -- WebSocket fan-out ------------------------------------------------------------

    def subscribe(
        self, port_filter: str | None = None, maxsize: int = 2000, as_json: bool = False
    ) -> asyncio.Queue:
        """A queue fed every committed row: dicts, or with `as_json` each row's JSON text."""
        if self._subscribers_closed:
            raise StoreError(SUBSCRIBERS_CLOSED_MSG)
        if len(self._subscribers) >= MAX_SUBSCRIBERS:
            raise StoreError(f"too many subscribers (max {MAX_SUBSCRIBERS})")
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers[q] = port_filter
        self._sub_dropped[q] = 0
        if as_json:
            self._json_subs.add(q)
        return q

    @property
    def subscribers_closed(self) -> bool:
        """True once stop_subscribers has run: `subscribe` refuses for shutdown, not the cap."""
        return self._subscribers_closed

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.pop(q, None)
        self._sub_dropped.pop(q, None)
        self._json_subs.discard(q)

    def take_dropped(self, q: asyncio.Queue) -> int:
        """Rows dropped for this subscriber since the last call, and reset.

        The feed sheds the oldest row rather than blocking the writer, which is right, but
        it did so silently: a subscriber that stopped reading for 60 s lost 36.7% of the
        span while `connected` stayed true and `rx_dropped` stayed 0, and the web UI builds
        its plots from this stream, so the chart simply had holes. The count is handed to
        the pump so the gap is announced in-band; an id gap alone cannot be inferred by the
        client, because `port=` filtering makes gaps legitimate.
        """
        n = self._sub_dropped.get(q, 0)
        if n:
            self._sub_dropped[q] = 0
        return n

    def _broadcast(self, row: dict[str, Any]) -> None:
        self._broadcast_batch([row])

    def _broadcast_batch(self, rows: list[dict[str, Any]]) -> None:
        """Fan one committed batch out to every subscriber, walking the subscribers once.

        The row dicts are shared by every queue they land in (and by the caller's future):
        subscribers read them and must never mutate them.
        """
        if not self._subscribers or not rows or self._subscribers_closed:
            return
        texts: list[str] | None = None
        for q, port_filter in self._subscribers.items():  # no awaits below: no copy needed
            as_json = q in self._json_subs
            if as_json and texts is None:
                texts = [json.dumps(r, separators=(",", ":")) for r in rows]
            for i, row in enumerate(rows):
                if port_filter is not None and row["port"] != port_filter:
                    continue
                item: Any = texts[i] if as_json else row
                if q.full():  # slow consumer: drop the oldest, never block the writer
                    try:
                        q.get_nowait()
                        self._sub_dropped[q] = self._sub_dropped.get(q, 0) + 1
                        self.ws_dropped += 1
                    except asyncio.QueueEmpty:
                        pass
                try:
                    q.put_nowait(item)
                except asyncio.QueueFull:
                    pass

    # -- sessions ---------------------------------------------------------------------
    #
    # The session table is written directly on the loop connection rather than through the
    # write queue. That is safe because the writer's insert-and-commit block contains no
    # await, so it can never be interleaved with these calls; and it is necessary because
    # a session's boundary marker has to be able to see the row it belongs to.

    _SESSION_COLS = "id, name, note, started_ts, ended_ts, start_id, end_id, auto"

    @staticmethod
    def _session_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = dict(row)
        out["auto"] = bool(out.get("auto"))   # SQLite has no bool; clients get a real one
        return out

    def active_session(self) -> dict[str, Any] | None:
        """The running session (the one with no end), or None."""
        assert self._conn is not None
        row = self._conn.execute(
            f"SELECT {self._SESSION_COLS} FROM sessions WHERE ended_ts IS NULL "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return self._session_dict(row)

    def _max_session_ref_id(self) -> int:
        """The highest `lines.id` any stored session still refers to (0 if none do)."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT MAX(MAX(COALESCE(start_id, 0), COALESCE(end_id, 0))) FROM sessions"
        ).fetchone()
        return int(row[0] or 0)

    def get_session(self, session_id: int) -> dict[str, Any] | None:
        """Look up a session by id alone, with no fallback to a name match.

        Callers that act destructively on the row they get back must use this rather than
        resolve_session(): the name fallback means a lookup for a missing id can return a
        session merely *named* that number, which is not what "delete session 99" asks for.
        """
        assert self._conn is not None
        row = self._conn.execute(
            f"SELECT {self._SESSION_COLS} FROM sessions WHERE id = ?", (int(session_id),)
        ).fetchone()
        return self._session_dict(row)

    def resolve_session(self, ref: str) -> dict[str, Any] | None:
        """Look up a session by numeric id or by name (the newest match wins)."""
        assert self._conn is not None
        # is_decimal_token(), not isdigit() or isdecimal(). isdigit() is true for e.g. "²",
        # which int() rejects with ValueError - so a session named "²" crashed the lookup
        # instead of falling through to the name branch that would have found it. isdecimal()
        # has both halves of that same problem: it is true for other scripts' digits, which
        # int() silently converts (a session named "٣" resolved to session id 3), and it
        # bounds the length not at all, so a 5000-digit ref raised past CPython's conversion
        # limit - a 500 with a traceback on /sessions/{ref}/export and on every endpoint
        # taking session=.
        if p.is_decimal_token(ref):
            row = self._conn.execute(
                f"SELECT {self._SESSION_COLS} FROM sessions WHERE id = ?", (int(ref),)
            ).fetchone()
            if row:
                return self._session_dict(row)
        row = self._conn.execute(
            f"SELECT {self._SESSION_COLS} FROM sessions WHERE name = ? ORDER BY id DESC LIMIT 1",
            (ref,),
        ).fetchone()
        return self._session_dict(row)

    def list_sessions(
        self, limit: int = 50, conn: sqlite3.Connection | None = None
    ) -> list[dict[str, Any]]:
        """Recent sessions, newest first, each with the number of lines still stored.

        The count is computed rather than remembered because retention (and the size cap)
        remove a session's lines out from under it; a finished run that has aged out reads
        as 0 lines instead of claiming rows that are gone.

        Both ends of each count must be a plain comparison against `l.id`, so the planner
        seeks to `end_id` instead of scanning to the end of the table (see _MAX_LINE_ID);
        this runs once per listed session, and `limit` reaches 1000 - hence
        `list_sessions_safe`, which is what a request handler should call.
        """
        c = conn if conn is not None else self._conn
        assert c is not None
        limit = max(0, min(int(limit), 1000))
        rows = c.execute(SESSION_LIST_SQL, (limit,)).fetchall()
        return [self._session_dict(r) for r in rows]

    async def list_sessions_safe(self, limit: int = 50) -> list[dict[str, Any]]:
        """list_sessions, off the loop (see _offload).

        Sargable at both ends, and still O(lines in the span): the count steps every id in
        each session's range, 135 ms for one session over 1M lines, times up to `limit`
        sessions, from a GET the web UI issues on a timer.
        """
        return await self._offload(self.list_sessions, limit=limit)

    async def start_session(
        self, name: str, note: str = "", auto: bool = False
    ) -> dict[str, Any]:
        """Open a session, closing any running one first. Returns the new session.

        Closing one opens the new one at its `end_id + 1`, so the two abut: the lines
        committed while the end marker was written belong to the new run, not to neither.
        With none running, `start_id` is the id the next stored line will take, after the
        write queue is drained (`_next_id` only counts lines the writer has given ids, and
        a queued backlog would otherwise land inside the new session).

        `auto` marks a session the daemon opened for its own run rather than one someone
        named. The two are stored identically and both count towards the retention floor;
        the flag exists so the UI can keep offering "start a run" while one is open, and so
        an empty one can be dropped on close (see `stop_session`).

        Runs under `_session_lock`, so a second start cannot enter while this one is
        suspended writing the previous session's end marker.
        """
        async with self._session_lock:
            closed = await self._stop_session_locked()
            if closed is not None:
                return await self._open_session_locked(name, note, auto, closed["end_id"] + 1)
            await self.drain_writes()
            return await self._open_session_locked(name, note, auto, self._next_id)

    async def _open_session_locked(
        self, name: str, note: str, auto: bool, start_id: int
    ) -> dict[str, Any]:
        """Insert the session row and its start marker; `_session_lock` is held."""
        assert self._conn is not None
        cur = self._conn.execute(
            "INSERT INTO sessions(name, note, started_ts, start_id, auto) VALUES(?,?,?,?,?)",
            (name, note, time.time(), start_id, int(bool(auto))),
        )
        self._conn.commit()
        session_id = cur.lastrowid
        # A marker, not a sys row: the UI draws markers as a full-width divider, which
        # is exactly how a run boundary should read in the terminal.
        await self.add_line(
            ts=time.time(), port="", dir="-", chan="marker", seq=None,
            raw=f"session start: {name}" + (f" ({note})" if note else ""),
        )
        return self.resolve_session(str(session_id)) or {}

    async def stop_session(self, reopen_auto: str | None = None) -> dict[str, Any] | None:
        """Close the running session, if any, and return it. Idempotent.

        An automatic session that captured no device traffic is dropped rather than kept:
        a daemon started and stopped without a board attached is not a run, and a list
        full of those would bury the ones that are. Its lines stay in the capture; only
        the label goes.

        `reopen_auto` names an automatic session to open at the closed one's `end_id + 1`
        under the same lock hold, so the two abut as they do in `start_session`.
        """
        async with self._session_lock:
            closed = await self._stop_session_locked()
            if closed is not None and reopen_auto is not None:
                await self._open_session_locked(reopen_auto, "", True, closed["end_id"] + 1)
            return closed

    async def _stop_session_locked(self) -> dict[str, Any] | None:
        """stop_session's body, with `_session_lock` already held by the caller."""
        assert self._conn is not None
        session = self.active_session()
        if session is None:
            return None
        # Write the closing marker first so it falls inside the session it closes.
        row = await self.add_line(
            ts=time.time(), port="", dir="-", chan="marker", seq=None,
            raw=f"session end: {session['name']}",
        )
        self._conn.execute(
            "UPDATE sessions SET ended_ts = ?, end_id = ? WHERE id = ?",
            (time.time(), row["id"], session["id"]),
        )
        self._conn.commit()
        closed = self.resolve_session(str(session["id"]))
        if closed is not None and closed["auto"] and not self._captured_traffic(closed):
            self.delete_session(closed["id"])
        return closed

    def _captured_traffic(self, session: dict[str, Any]) -> bool:
        """Did this session record anything from a device?

        Sys rows, and the markers the daemon or a client wrote (`dir` '-': its own
        start/stop rows, the session's own boundaries, `POST /marker`), are the host
        talking to itself and do not make a run. A firmware `!m` marker is not: it
        arrives on `dir` 'rx' like any other device line, and for a board whose only
        instrumentation is markers it may be the sole traffic of a real run.
        """
        assert self._conn is not None
        start_id, end_id = self.session_span(session)
        row = self._conn.execute(
            "SELECT 1 FROM lines WHERE id >= ? AND id <= ? "
            "AND (chan IN ('debug','resp','event') OR (chan = 'marker' AND dir = 'rx')) "
            "LIMIT 1",
            (start_id, end_id),
        ).fetchone()
        return row is not None

    def delete_session(self, session_id: int) -> bool:
        """Forget a session label. The captured lines themselves are untouched."""
        assert self._conn is not None
        cur = self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def export_session_db(
        self, dest_path: str, *, id_from: int, id_to: int | None, session: dict[str, Any],
        on_open: Callable[[sqlite3.Connection], None] | None = None,
    ) -> int:
        """Copy one session's span into a standalone capture database. Returns line count.

        Blocking: call from a worker thread. The copy runs on its own connection with the
        live capture ATTACHed read-only, so it never touches the loop connection (which
        sqlite3 would refuse from another thread anyway), and it is a plain
        `INSERT ... SELECT` per table rather than a row-at-a-time transfer.

        `id_to` is None for a session that is still running; the span then ends at the
        last line captured when the copy starts.

        The result is a normal MCUscope capture file, not a bespoke archive format: point
        `mcuscoped --config` at it (or open it with any SQLite tool) and every query works
        unchanged. The session row is carried across with its ids intact, so `--session`
        still scopes correctly inside the copy.

        `on_open` receives the copy's connection before any SQL, so a caller that abandons
        the export can stop it from another thread (the copy then raises). A progress
        handler does it; an `interrupt()` made between two statements is lost.
        """
        conn = sqlite3.connect(dest_path)
        try:
            if on_open is not None:
                on_open(conn)
            conn.executescript(SCHEMA)
            # Plain path, not a `file:...?mode=ro` URI: URI filenames in ATTACH depend on a
            # connection flag and on platform-specific path escaping, which is exactly the
            # kind of thing that works on Linux and breaks on Windows. The live capture is
            # only ever read here (every statement below writes to main), and WAL lets this
            # reader run without blocking the daemon's writer.
            conn.execute("ATTACH DATABASE ? AS src", (self._db_path,))
            try:
                # A running session has no end_id yet, so its upper bound is "everything
                # captured so far". Read that through `src` on this connection: max_id()
                # goes through the loop-thread connection, and this method runs on a
                # worker thread, so calling it here raised sqlite3.ProgrammingError and
                # every export of an *open* session - which includes the automatic one
                # the daemon always has running - answered 400. The existing tests all
                # stopped the session first, so the whole branch was uncovered.
                hi = id_to
                if hi is None:
                    hi = conn.execute("SELECT MAX(id) FROM src.lines").fetchone()[0] or 0
                cur = conn.execute(
                    "INSERT INTO lines SELECT id, ts, port, dir, chan, seq, raw FROM src.lines "
                    "WHERE id >= ? AND id <= ?",
                    (id_from, hi),
                )
                copied = cur.rowcount
                conn.execute(
                    "INSERT INTO can_frames(line_id, tick_ms, bus, can_id, ext, rtr, dlc, data) "
                    "SELECT line_id, tick_ms, bus, can_id, ext, rtr, dlc, data "
                    "FROM src.can_frames WHERE line_id >= ? AND line_id <= ?",
                    (id_from, hi),
                )
                conn.execute(
                    "INSERT INTO plot_points SELECT line_id, tick_ms, sid, name, value "
                    "FROM src.plot_points WHERE line_id >= ? AND line_id <= ?",
                    (id_from, hi),
                )
                conn.execute(
                    "INSERT INTO sessions"
                    "(id, name, note, started_ts, ended_ts, start_id, end_id, auto) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (
                        session["id"], session["name"], session["note"], session["started_ts"],
                        session["ended_ts"], session["start_id"], session["end_id"],
                        int(bool(session.get("auto"))),
                    ),
                )
                conn.commit()
            finally:
                # DETACH inside an open transaction raises, which would mask whatever the
                # INSERT above failed with. Roll back first, suppressed: there is nothing
                # to roll back on the success path.
                with contextlib.suppress(sqlite3.Error):
                    conn.rollback()
                conn.execute("DETACH DATABASE src")
            return max(0, copied)
        finally:
            conn.close()

    # -- reads ------------------------------------------------------------------------

    @property
    def capture_id(self) -> str:
        """Identity of this id space (SPEC 3.4). Changes only when ids can be reused."""
        return self._capture_id

    def _new_capture(self) -> None:
        assert self._conn is not None
        self._capture_id = _mint_capture_id()
        self._conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('capture', ?)",
                           (self._capture_id,))
        self._conn.commit()
        log.warning("storage: the highest line id was deleted; capture identity is now %s",
                    self._capture_id)

    def _delete_lines(self, select_ids: str, params: tuple[Any, ...]) -> int:
        """Delete the lines `select_ids` selects (one chunk), commit, return the count.

        Every lines delete goes through here, because deleting the highest id changes what
        a client's ids mean: its dedup watermark then discards the new capture as
        duplicates. So that case, and only that case, mints a new capture identity, which
        every client reads as "drop what you hold and re-seed". Trimming the oldest end -
        retention, the size cap - leaves the maximum alone and is not a reset.

        The cascaded plot points are taken off the plot summary here, from an aggregate
        over the chunk's own points, so a delete costs O(chunk) rather than a rebuild.
        """
        assert self._conn is not None
        max_before = self._max_id_sql(self._conn)
        gone = self._plot_points_in(select_ids, params)
        cur = self._conn.execute(f"DELETE FROM lines WHERE id IN ({select_ids})", params)
        self._conn.commit()
        if cur.rowcount:
            self._forget_plot_points(gone)
            if self._max_id_sql(self._conn) < max_before:
                self._new_capture()
            # The newest rows may have gone (a purge after a clock step back); a stale
            # `_top_ts` would count every in-order row after it as late.
            self._top_ts = self._conn.execute("SELECT MAX(ts) FROM lines").fetchone()[0] or 0.0
        return cur.rowcount

    def _plot_points_in(
        self, select_ids: str, params: tuple[Any, ...]
    ) -> list[tuple[str, str, int, int]] | None:
        """(port, name, count, newest line_id) of the plot points on the selected lines.

        None when the summary is being rebuilt or is due for one: the delete then only
        marks it dirty.
        """
        assert self._conn is not None
        if self._plot_dirty or self._plot_lock.locked():
            return None
        return self._conn.execute(
            "SELECT li.port, pp.name, COUNT(*), MAX(pp.line_id) "
            "FROM plot_points pp CROSS JOIN lines li ON li.id = pp.line_id "
            f"WHERE pp.line_id IN ({select_ids}) GROUP BY li.port, pp.name",
            params,
        ).fetchall()

    def _forget_plot_points(self, gone: list[tuple[str, str, int, int]] | None) -> None:
        """Take deleted plot points off the summary (see _plot_points_in)."""
        if gone is None:
            self._plot_dirty = True
            return
        for port, name, count, newest in gone:
            stat = self._plot_summary.get((port, name))
            if stat is None:
                continue
            stat.count -= count
            if stat.count <= 0:
                del self._plot_summary[(port, name)]
            elif newest >= stat.last_line_id:
                # The channel's latest sample went and older ones remain (a purge of a
                # middle range): which is now latest takes a scan.
                self._plot_dirty = True

    def max_id(self, conn: sqlite3.Connection | None = None) -> int:
        """The newest line id. Answered from the writer's own sequence while it runs.

        The writer allocates every id (see _insert_batch) and commits with no await in
        between, so at any point the loop can observe, `_next_id - 1` is the highest id
        committed. Deleting the top of the capture is the one case the two differ, and
        `_delete_lines` reads SQL for exactly that comparison.
        """
        if conn is None and self.writer_alive:
            return self._next_id - 1
        return self._max_id_sql(conn if conn is not None else self._conn)

    @staticmethod
    def _max_id_sql(c: sqlite3.Connection | None) -> int:
        assert c is not None
        row = c.execute("SELECT MAX(id) AS m FROM lines").fetchone()
        return row["m"] or 0

    def session_span(
        self, session: Any, conn: sqlite3.Connection | None = None
    ) -> tuple[int, int]:
        """A session's inclusive id bounds. One still running ends at the newest line.

        `export_session_db` deliberately does not use this: it runs on a worker thread
        against an ATTACHed database, where this connection is unusable and the bound has
        to come from `src.lines` (see the comment there).
        """
        end_id = session["end_id"]
        return session["start_id"], self.max_id(conn) if end_id is None else end_id

    def _window_floor(
        self, last_ms: float, id_to: int | None, conn: sqlite3.Connection | None = None
    ) -> float:
        """The ts a `last_ms` window is measured back from.

        Normally now. But when an upper id bound is in force - a paused surface exporting
        what it shows, or a session that has ended - "the last 30 seconds" means the 30
        seconds ending at that bound, not the 30 ending at this request. Intersecting a
        frozen id range with a now-anchored window returns almost nothing: a chart paused
        40 s ago asked for rows [3,4,5,6] and got [6].

        One primary-key seek (`SEARCH lines USING INTEGER PRIMARY KEY (rowid<?)`), so it
        costs nothing on the hot path. A bound below every stored id leaves the window
        empty either way, since no row satisfies the id filter.
        """
        anchor = None if id_to is None else self.newest_ts_at_or_below(id_to, conn)
        return (time.time() if anchor is None else anchor) - last_ms / 1000.0

    def newest_ts_at_or_below(
        self, id_to: int, conn: sqlite3.Connection | None = None
    ) -> float | None:
        """The `ts` of the highest-id line at or below `id_to`, or None. One primary-key seek."""
        c = conn if conn is not None else self._conn
        assert c is not None
        row = c.execute(
            "SELECT ts FROM lines WHERE id <= ? ORDER BY id DESC LIMIT 1", (id_to,)
        ).fetchone()
        return row[0] if row else None

    def _window_terms(
        self,
        *,
        id_from: int | None = None,
        id_to: int | None = None,
        port: str | None = None,
        chans: list[str] | None = None,
        dir: str | None = None,
        last_ms: float | None = None,
        since_ts: float | None = None,
        until_ts: float | None = None,
        floor_ts: float | None = None,
        conn: sqlite3.Connection | None = None,
        id_col: str = "id",
        port_col: str = "port",
        ts_col: str = "ts",
    ) -> tuple[list[str], list[Any]]:
        """The id/port/chan/time predicates every read over a capture window shares.

        Parameterised by column because the same window is expressed against `lines`
        (`id`, `port`, `ts`) and against a join (`cf.line_id`, `l.port`, `l.ts`). Five
        reads share it, and share with it that `last_ms` is anchored through `_window_floor`
        rather than at `now`: forgetting that is silent, since the query still runs and just
        returns almost nothing whenever an upper id bound is in force.

        `floor_ts` is a `last_ms` window already resolved to its inclusive floor, for a
        caller whose `id_to` is its own freeze rather than a bound the request gave.

        With `id_to` given, `until_ts` adds no id ceiling: the caller folds the ceiling into
        `id_to` once per request (`id_ceiling_safe`), because it is an index walk the length
        of the window and a paged export would otherwise repeat it on every page.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if id_from is not None:
            clauses.append(f"{id_col} >= ?")
            params.append(id_from)
        if id_to is not None:
            clauses.append(f"{id_col} <= ?")
            params.append(id_to)
        if port is not None:
            # `is not None`: "" is the daemon's own port (SPEC 3.5), not "every port".
            clauses.append(f"{port_col} = ?")
            params.append(port)
        if dir is not None:
            clauses.append("dir = ?")
            params.append(dir)
        if chans:
            # A single channel stays `= ?` rather than a one-element IN, so the plan for
            # the common case is exactly what it was.
            if len(chans) == 1:
                clauses.append("chan = ?")
                params.append(chans[0])
            else:
                clauses.append(f"chan IN ({','.join('?' * len(chans))})")
                params.extend(chans)
        if last_ms is not None:
            floor_ts = self._window_floor(last_ms, id_to, conn)
        if floor_ts is not None:
            clauses.append(f"{ts_col} >= ?")
            params.append(floor_ts)
            clauses.append(f"{id_col} >= ?")
            params.append(self._window_id_floor(floor_ts, conn))
        if since_ts is not None:
            # Strict, as `since_ts` is on /lines: the same rows for the same value.
            clauses.append(f"{ts_col} > ?")
            params.append(since_ts)
            clauses.append(f"{id_col} >= ?")
            params.append(self._window_id_floor(since_ts, conn))
        if until_ts is not None:
            # Paired id ceiling for the same reason `last_ms` gets an id floor: `ts <= ?`
            # under `ORDER BY id DESC` is not sargable, so a window ending in the past
            # reads the table btree back from the newest row before it finds one.
            clauses.append(f"{ts_col} <= ?")
            params.append(until_ts)
            if id_to is None:
                clauses.append(f"{id_col} <= ?")
                params.append(self._window_id_ceiling(until_ts, conn))
        return clauses, params

    def _window_id_ceiling(self, until_ts: float, conn: sqlite3.Connection | None = None) -> int:
        """The highest id an `until_ts` window can contain, as a bound an index can seek to.

        MAX(id), not the id of the newest `ts`: those differ the moment `ts` stops rising
        with `id`, and the difference is silent loss on the widest window a caller can ask
        for. One backwards clock step (NTP, a resumed laptop) put the newest-`ts` row at
        id 50 of 61, so an `until_ts` above every stored `ts` - which reads as "no upper
        bound" - dropped the last 11 rows.

        What being right costs, measured at 300k rows:

        - The newest row is inside the window - which is what an `until_ts` meaning "no
          upper bound" looks like - two primary-key lookups, 0.01 ms.
        - Nothing at or below the cutoff: the covering-index seek finds nothing, 0.01 ms.
        - A window ending inside the capture: a covering-index walk of that window's
          entries, 23 ms for half of 300k rows. Index-only, so no `raw` blob and no table
          btree, and it only runs when `until_ts` is given (no polling path passes one).
          The old form was a single seek, and could name an id below rows the window still
          holds. `INDEXED BY` is load-bearing: without it the planner takes MAX(id) as a
          backwards rowid walk, which reads the table btree from the newest row down and
          is 48 ms on an *empty* window.

        With nothing at or below the cutoff, 0: the window is empty, and saying so as a
        bound keeps the empty case off the table btree.
        """
        c = conn if conn is not None else self._conn
        assert c is not None
        newest = c.execute("SELECT id, ts FROM lines ORDER BY id DESC LIMIT 1").fetchone()
        if newest is None:
            return 0
        if newest[1] <= until_ts:
            return int(newest[0])
        row = c.execute(
            "SELECT MAX(id) FROM (SELECT id FROM lines INDEXED BY idx_lines_ts WHERE ts <= ?)",
            (until_ts,),
        ).fetchone()
        return int(row[0]) if row is not None and row[0] is not None else 0

    def _window_id_floor(self, floor_ts: float, conn: sqlite3.Connection | None = None) -> int:
        """A lower id bound for a time-floored window, as a bound an index can seek to.

        `ts >= ?` alone is not enough: `/lines` orders by id, so the planner reads the
        table btree backwards and only stops early when the window actually holds
        `limit+1` rows. A *quiet* window therefore reads the whole table on the event
        loop - 46 ms at 300k rows, 0.6 ms once the window is busy, the same busy/quiet
        asymmetry that idx_lines_port_id was added for. One `idx_lines_ts` step resolves
        the cutoff to an id, and every window read then rides a primary-key range.

        `ts` is not monotonic in id (rows queue after they are stamped), so the bound is
        one past the newest row stamped `WINDOW_TS_SLACK_S` before the floor, and the
        caller keeps its exact `ts` term. While no row is stamped that much later than a
        higher-id row, every lower id was stamped before the floor, so the bound drops
        nothing the window holds. The price is reading up to the slack's worth of older
        rows when the window holds fewer than `limit`.

        With nothing that old, 1: no row can be excluded.
        """
        c = conn if conn is not None else self._conn
        assert c is not None
        row = c.execute(
            "SELECT id FROM lines WHERE ts < ? ORDER BY ts DESC LIMIT 1",
            (floor_ts - WINDOW_TS_SLACK_S,),
        ).fetchone()
        return int(row[0]) + 1 if row is not None else 1

    def query_lines(
        self,
        *,
        port: str | None = None,
        chans: list[str] | None = None,
        dir: str | None = None,
        match: str | None = None,
        since_id: int | None = None,
        since_ts: float | None = None,
        until_ts: float | None = None,
        last_ms: int | None = None,
        floor_ts: float | None = None,
        id_from: int | None = None,
        id_to: int | None = None,
        limit: int = 100,
        order: str = "desc",
        conn: sqlite3.Connection | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Query stored lines. `id_from`/`id_to` are inclusive bounds (session scoping)."""
        c = conn if conn is not None else self._conn
        assert c is not None
        limit = max(0, min(int(limit), 1000))
        clauses, params = self._window_terms(
            id_from=id_from, id_to=id_to, port=port, chans=chans, dir=dir, last_ms=last_ms,
            since_ts=since_ts, until_ts=until_ts, floor_ts=floor_ts, conn=conn,
        )
        if match:
            clauses.append("raw REGEXP ?")
            params.append(match)
        if since_id is not None:
            clauses.append("id > ?")
            params.append(since_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        order_sql = "DESC" if order == "desc" else "ASC"
        sql = (
            f"SELECT id, ts, port, dir, chan, seq, raw FROM lines{_lines_index(port, chans)} "
            f"{where} ORDER BY id {order_sql} LIMIT ?"
        )
        rows = c.execute(sql, (*params, limit + 1)).fetchall()
        truncated = len(rows) > limit
        return [dict(r) for r in rows[:limit]], truncated

    def count_lines(
        self,
        *,
        port: str | None = None,
        chans: list[str] | None = None,
        dir: str | None = None,
        id_from: int | None = None,
        id_to: int | None = None,
        last_ms: float | None = None,
        floor_ts: float | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """Count stored lines in a window. No `match` here: counting is match-free by design.

        Used to report how many lines an assertion looked at, and what a purge is about to
        remove. Deliberately excludes a regex filter so it stays a bounded index count
        rather than a full-table regex scan.

        Bounded is not the same as cheap: an id range spanning the whole capture forces the
        table btree and reads every raw blob, so a bound that constrains nothing is dropped
        here rather than left for each caller to remember.
        """
        c = conn if conn is not None else self._conn
        assert c is not None
        if last_ms is None:
            # `id >= 1` / `id <= max_id` select every row but force the count off the
            # covering index onto the table btree (3M rows: 230 ms against 26 ms). Only
            # safe to drop when no `last_ms` is in play, since `id_to` also anchors the
            # window floor - see `_window_floor`.
            if id_from is not None and id_from <= 1:
                id_from = None
            if id_to is not None and id_to >= self.max_id(c):
                id_to = None
        clauses, params = self._window_terms(
            id_from=id_from, id_to=id_to, port=port, chans=chans,
            dir=None if dir == "rx" else dir, last_ms=last_ms, floor_ts=floor_ts, conn=conn,
        )
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT COUNT(*) FROM lines{_lines_index(port, chans)} {where}"
        if dir == "rx":
            # Every row less the host's (see idx_lines_host), in one statement so both
            # counts read one snapshot of a capture the writer is still appending to.
            host = " AND ".join([*clauses, "dir <> 'rx'"])
            sql = (f"SELECT ({sql}) - (SELECT COUNT(*) FROM lines INDEXED BY idx_lines_host "
                   f"WHERE {host})")
            params = params * 2
        return int(c.execute(sql, params).fetchone()[0])

    def has_port_rows(self, port: str, conn: sqlite3.Connection | None = None) -> bool:
        """Whether any stored line carries `port`. One idx_lines_port_id seek."""
        c = conn if conn is not None else self._conn
        assert c is not None
        return c.execute(
            "SELECT 1 FROM lines WHERE port = ? LIMIT 1", (port,)
        ).fetchone() is not None

    def stored_ports(
        self, conn: sqlite3.Connection | None = None, include_daemon: bool = False
    ) -> list[str]:
        """Every port with a stored line, sorted: one idx_lines_port_id seek per port. The
        daemon-level port "" (SPEC 3.5) is not a board, so it is left out unless
        `include_daemon` (the plot summary rebuild must account for every line)."""
        c = conn if conn is not None else self._conn
        assert c is not None
        floor = "" if include_daemon else "WHERE port > ''"
        return [r[0] for r in c.execute(
            f"WITH RECURSIVE p(port) AS (SELECT MIN(port) FROM lines {floor} UNION ALL "
            "SELECT (SELECT MIN(port) FROM lines WHERE port > p.port) FROM p "
            "WHERE p.port IS NOT NULL) SELECT port FROM p WHERE port IS NOT NULL"
        )]

    def _read_on_private_conn(self, reader: Callable[..., Any], **kwargs: Any) -> Any:
        """Run one read on this worker thread's cached read connection."""
        return self._on_read_conn(lambda conn: reader(conn=conn, **kwargs))

    def _on_read_conn(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        """Call `fn` with this thread's cached read connection, once more on a fresh one if
        `stop()` closed the handle between the epoch check and the query (the two are not
        atomic). Every worker-side read goes through here."""
        try:
            return fn(self._read_conn())
        except sqlite3.ProgrammingError:
            if getattr(self._read_local, "epoch", None) == self._read_epoch:
                raise
            return fn(self._read_conn())

    def _read_conn(self) -> sqlite3.Connection:
        """This thread's cached read connection, opened on first use and kept until stop().

        One per match_executor worker (threading.local), so an offloaded read no longer
        pays a connect, the schema parse and a cold page cache per call. `stop()` closes
        them all from the loop thread (hence check_same_thread=False) and bumps the epoch,
        so a worker holding a closed handle reopens rather than failing.
        """
        local = self._read_local
        conn = getattr(local, "conn", None)
        if conn is not None and local.epoch == self._read_epoch:
            return conn
        conn = self._open_read_conn()
        with self._read_conns_lock:
            local.conn, local.epoch = conn, self._read_epoch
            self._read_conns.add(conn)
        return conn

    async def _offload(self, reader: Callable[..., Any], **kwargs: Any) -> Any:
        """Run an analytical read off the event loop, against its own read connection.

        Every heavy read shares one policy, so it is stated here rather than in five
        near-identical wrappers: the work goes to **match_executor, never the default
        pool**, because the default pool is what joins the serial reader thread on detach
        and shutdown and must never queue behind an analytics scan. WAL lets these readers
        run concurrently with the writer.

        An in-memory database cannot be reopened from another thread, so it runs inline.
        `query_lines_safe` does not come through here: it offloads only when a `match` is
        present and has to translate the regex budget, which is its own contract.
        """
        if self._db_path in (":memory:", ""):
            return reader(**kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            match_executor(), functools.partial(self._read_on_private_conn, reader, **kwargs)
        )

    async def id_ceiling_safe(self, until_ts: float) -> int:
        """_window_id_ceiling, off the loop: an index walk the length of the window."""
        return await self._offload(self._window_id_ceiling, until_ts=until_ts)

    async def count_lines_safe(self, **kwargs: Any) -> int:
        """count_lines, off the loop (see _offload).

        The counts that report what a purge would remove and how many lines an assertion
        looked at are whole-capture reads: 44 ms at 1M rows, 230 ms at 3M for the dry run.
        """
        return await self._offload(self.count_lines, **kwargs)

    def _open_read_conn(self) -> sqlite3.Connection:
        """Open a read connection to the same DB file (WAL allows concurrent readers).

        No `regexp` is registered, as on the loop connection (see start()): the connection
        is cached, and a closure armed here would spend its budget for good. Every match
        query registers its own (`_query_lines_on`).
        """
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA cache_size=-8000")   # 8 MB of page cache per reader
        return conn

    def _query_lines_threadsafe(self, **kwargs: Any) -> tuple[list[dict[str, Any]], bool]:
        return self._on_read_conn(lambda conn: self._query_lines_on(conn, **kwargs))

    def _query_lines_on(
        self, conn: sqlite3.Connection, **kwargs: Any
    ) -> tuple[list[dict[str, Any]], bool]:
        # Arm a fresh budget for THIS query. The connection is cached across queries, and
        # the budget has to be per query, not per connection, or a long-lived connection
        # would carry an already-spent deadline into the next request.
        rx = _make_regexp()
        conn.create_function("regexp", 2, rx, deterministic=True)
        try:
            return self.query_lines(conn=conn, **kwargs)
        except sqlite3.OperationalError:
            # SQLite reports the closure's TimeoutError as a generic OperationalError, so
            # the closure's own flag is what distinguishes a budget stop from a real SQL
            # error. Never return partial rows here: the result is all-or-error.
            if rx.timed_out:
                raise _budget_error(rx) from None
            raise

    async def query_lines_safe(self, **kwargs: Any) -> tuple[list[dict[str, Any]], bool]:
        """query_lines, but run a match-bearing query off the event loop.

        Match queries execute on the dedicated match_executor against a private read
        connection, so a slow pattern ties up one of its workers while ingestion,
        detach/shutdown joins and other clients keep running. That separation only holds
        because the pattern runs on the `regex` engine, which releases the GIL and honours
        a timeout (see _make_regexp); with stdlib `re` the pool was decoration. Match-free
        queries are cheap and bounded (limit <= 1000), so they run inline on the loop,
        except with `until_ts`: `ts <= ?` is not sargable under `ORDER BY id`, and rows out
        of id order after a clock step are read past one by one.
        Falls back to inline for an in-memory DB, which cannot be reopened from another
        thread - that path still gets a budget, just on the loop connection.
        """
        offloadable = (
            bool(kwargs.get("match")) or kwargs.get("until_ts") is not None
        ) and self._db_path not in (":memory:", "")
        if not offloadable:
            if kwargs.get("match") and self._conn is not None:
                rx = _make_regexp()   # re-arm: a per-connection deadline would be stale
                self._conn.create_function("regexp", 2, rx, deterministic=True)
                try:
                    return self.query_lines(**kwargs)
                except sqlite3.OperationalError:
                    if rx.timed_out:
                        raise _budget_error(rx) from None
                    raise
            return self.query_lines(**kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            match_executor(), functools.partial(self._query_lines_threadsafe, **kwargs)
        )

    async def query_can_frames_safe(self, **kwargs: Any) -> tuple[list[dict[str, Any]], bool]:
        """query_can_frames, off the loop (see _offload).

        A JOIN against `lines` with filters (`port`, `last_ms`) that no index fully covers,
        so on a large capture it is the heaviest read the API serves. Measured on a 3M-line
        capture it blocked the loop for ~0.3 s per call, which at high ingest rates backs up
        thousands of lines behind a UI that polls CAN.
        """
        return await self._offload(self.query_can_frames, **kwargs)

    async def can_frames_page_safe(self, **kwargs: Any) -> tuple[list[dict[str, Any]], bool, int]:
        """can_frames_page, off the loop (see _offload)."""
        return await self._offload(self.can_frames_page, **kwargs)

    def can_frames_page(
        self, *, conn: sqlite3.Connection | None = None, **kwargs: Any
    ) -> tuple[list[dict[str, Any]], bool, int]:
        """query_can_frames (newest first) plus `next_since_id`: the highest line id the
        answer covers, for a follow to resume from (SPEC 3.4 /can/frames).

        A follow on a port that sends no frames never sees a frame id to advance on, and
        re-read every frame above its first watermark on each poll. The newest line id is
        read in the same snapshot as the frames, so no frame committed between the two
        reads can fall below the watermark unseen.
        """
        conn = conn if conn is not None else self._conn
        assert conn is not None
        assert kwargs.get("order", "desc") == "desc", "an ascending page covers less"
        own = not conn.in_transaction   # the loop connection may be mid-write: join that
        if own:
            conn.execute("BEGIN")
        try:
            rows, truncated = self.query_can_frames(conn=conn, **kwargs)
            top = conn.execute("SELECT MAX(id) FROM lines").fetchone()[0] or 0
        finally:
            if own:
                conn.execute("COMMIT")
        if kwargs.get("id_to") is not None:
            top = min(top, kwargs["id_to"])
        # Never backwards: ids past SQL's MAX(id) may already be in the client's hands
        # after a purge of the newest rows.
        return rows, truncated, max(top, kwargs.get("since_id") or 0)

    def query_can_frames(
        self,
        *,
        port: str | None = None,
        bus: int | None = None,
        can_id: int | None = None,
        can_ids: list[int] | None = None,
        last_ms: int | None = None,
        since_ts: float | None = None,
        until_ts: float | None = None,
        floor_ts: float | None = None,
        since_id: int | None = None,
        id_from: int | None = None,
        id_to: int | None = None,
        limit: int = 100,
        order: str = "desc",
        conn: sqlite3.Connection | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        conn = conn if conn is not None else self._conn
        assert conn is not None
        limit = max(0, min(int(limit), 1000))
        clauses, params = self._window_terms(
            id_from=id_from, id_to=id_to, port=port, last_ms=last_ms, since_ts=since_ts,
            until_ts=until_ts, floor_ts=floor_ts, conn=conn,
            id_col="cf.line_id", port_col="l.port", ts_col="l.ts",
        )
        ids = list(can_ids) if can_ids else ([can_id] if can_id is not None else [])
        # A single id stays `= ?` rather than a one-element IN, so the plan for the common
        # case is exactly what it was (as `chans` does in _window_terms).
        if len(ids) == 1:
            clauses.append("cf.can_id = ?")
            params.append(ids[0])
        elif ids:
            # `+` de-optimises the term: without it the planner drives from idx_can_id_line
            # and throws away the ORDER BY cf.line_id index order, sorting every match
            # through a temp b-tree before LIMIT can apply. Measured at 300k frames, 133 ms
            # that way against 2.21 ms; the paged CSV export re-issues the statement per
            # page, so it was 47.0 s against 3.85 s at 1M lines. The single-id branch above
            # never regressed.
            clauses.append(f"+cf.can_id IN ({','.join('?' * len(ids))})")
            params.extend(ids)
        if bus is not None:
            clauses.append("cf.bus = ?")
            params.append(bus)
        if since_id is not None:
            clauses.append("cf.line_id > ?")
            params.append(since_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        # CROSS JOIN, which in SQLite means "do not reorder", not a cartesian product.
        # `lines` has no index on `port`, so a filter that lands on `l` looks selective to
        # the planner and it drives the loop from `lines` instead - which also throws away
        # the `ORDER BY cf.line_id DESC` index order, forcing every matching frame through a
        # temp b-tree before LIMIT can apply. Measured at 1M lines, `?port=`: 131 ms that
        # way against 0.4 ms driving from `can_frames`. Pinning the order costs nothing on
        # the other filters (identical plans) because `cf.line_id` is the primary key, so
        # the outer loop is a backwards key scan and LIMIT stops it early.
        sql = (
            "SELECT cf.line_id, l.ts, l.port, cf.tick_ms, cf.bus, cf.can_id, cf.ext, cf.rtr, "
            "cf.dlc, cf.data "
            "FROM can_frames cf CROSS JOIN lines l ON l.id = cf.line_id "
            f"{where} ORDER BY cf.line_id {'ASC' if order == 'asc' else 'DESC'} LIMIT ?"
        )
        rows = conn.execute(sql, (*params, limit + 1)).fetchall()
        truncated = len(rows) > limit
        rows = rows[:limit]
        out: list[dict[str, Any]] = []
        for r in rows:
            data = r["data"] or b""
            out.append(
                {
                    "line_id": r["line_id"],
                    "ts": r["ts"],
                    "port": r["port"],   # which board: a bus id is unique only per port
                    "tick_ms": r["tick_ms"],
                    "bus": r["bus"],
                    "can_id": r["can_id"],
                    "ext": bool(r["ext"]),
                    "rtr": bool(r["rtr"]),
                    "dlc": r["dlc"],
                    "data_hex": p.bytes_to_hex(data),
                }
            )
        return out, truncated

    # -- plot reads (SPEC 9.2) --------------------------------------------------------

    async def query_plot_channels_safe(self, port: str | None = None) -> list[dict[str, Any]]:
        """One row per channel name, served from the writer's summary: sid, point count,
        and the newest sample with the port it came from; `port=` narrows to one board,
        since a name is unique only within a port (SPEC 2.5, 9.2).

        A GROUP BY over plot_points would scan the whole table, and the web UI polls this
        every second. The summary is exact between deletes; after one it is rebuilt from
        SQL off the loop, once, on the next read. The plain GROUP BY form lives on as the
        tests' oracle (tests/test_store_plot_summary.py).
        """
        await self._settled_plot_summary()
        return self._plot_channels_from_summary(port)

    async def plot_ports_safe(self) -> list[str]:
        """Every port holding stored plot points, sorted; from the same summary."""
        await self._settled_plot_summary()
        return sorted({row_port for row_port, _name in self._plot_summary})

    async def _settled_plot_summary(self) -> None:
        """Rebuild if dirty, and wait out a rebuild already in flight: until its scan lands
        the summary holds only the rows written since it began (a false "no such channel")."""
        if self._plot_dirty or self._plot_lock.locked():
            await self._rebuild_plot_summary()

    def _note_plot(self, row: dict[str, Any], plot: list[p.PlotPoint]) -> None:
        """Fold one committed line's plot points into the summary (writer task only)."""
        line_id, ts, port = row["id"], row["ts"], row["port"]
        summary = self._plot_summary
        for tick_ms, sid, name, value in plot:
            stat = summary.get((port, name))
            if stat is None:
                summary[(port, name)] = _PlotStat(sid, value, tick_ms, ts, line_id, 1)
            else:
                stat.count += 1
                if line_id >= stat.last_line_id:
                    stat.sid, stat.last_value, stat.last_tick = sid, value, tick_ms
                    stat.last_ts, stat.last_line_id = ts, line_id

    def _plot_channels_from_summary(self, port: str | None) -> list[dict[str, Any]]:
        """The endpoint's rows, merged across ports unless `port` narrows to one."""
        merged: dict[str, dict[str, Any]] = {}
        for (row_port, name), stat in self._plot_summary.items():
            if port is not None and row_port != port:
                continue
            cur = merged.get(name)
            if cur is None:
                merged[name] = {
                    "name": name, "sid": stat.sid, "last_value": stat.last_value,
                    "last_tick": stat.last_tick, "last_ts": stat.last_ts, "port": row_port,
                    "last_line_id": stat.last_line_id, "count": stat.count,
                }
                continue
            cur["count"] += stat.count
            if stat.last_line_id > cur["last_line_id"]:
                cur.update(
                    sid=stat.sid, last_value=stat.last_value, last_tick=stat.last_tick,
                    last_ts=stat.last_ts, port=row_port, last_line_id=stat.last_line_id,
                )
        return [merged[name] for name in sorted(merged)]

    def _scan_plot_summary(
        self, conn: sqlite3.Connection | None = None, high: int = 0
    ) -> dict[tuple[str, str], _PlotStat]:
        """The summary rebuilt from SQL, over lines with id <= `high` (the rebuild path).

        Joining every point to its line for the port was the whole cost (15 s at 4.4M
        points), so only the ports with the fewest lines are joined, driven from their own
        lines; the busiest port's counts are the per-name totals, which the covering
        (name, line_id) index gives without touching `lines`, minus theirs. 2.4 s there.

        The statements share one read transaction: a delete committed between two of them
        mixed snapshots, giving wrong counts or a vanished newest point (a TypeError).
        """
        c = conn if conn is not None else self._conn
        assert c is not None
        c.execute("BEGIN")   # no caller holds a transaction open (the writer commits in-step)
        try:
            return self._scan_plot_rows(c, high)
        finally:
            c.rollback()

    def _scan_plot_rows(
        self, c: sqlite3.Connection, high: int
    ) -> dict[tuple[str, str], _PlotStat]:
        """_scan_plot_summary's statements, on a connection already in a read snapshot."""
        totals = {
            name: count for name, count in c.execute(
                "SELECT name, COUNT(*) FROM plot_points INDEXED BY idx_plot_name_line "
                "WHERE line_id <= ? GROUP BY name", (high,)
            )
        }
        if not totals:
            return {}
        ports = self.stored_ports(c, include_daemon=True)
        sizes = {
            port: c.execute(
                "SELECT COUNT(*) FROM lines WHERE port = ? AND id <= ?", (port, high)
            ).fetchone()[0]
            for port in ports
        }
        busiest = max(sizes, key=sizes.__getitem__)
        newest: dict[tuple[str, str], tuple[int, int]] = {}   # key -> (line_id, count)
        for port in ports:
            if port == busiest:
                continue
            for name, line_id, count in c.execute(
                "SELECT pp.name, MAX(pp.line_id), COUNT(*) "
                "FROM lines li INDEXED BY idx_lines_port_id "
                "CROSS JOIN plot_points pp ON pp.line_id = li.id "
                "WHERE li.port = ? AND li.id <= ? GROUP BY pp.name", (port, high)
            ):
                newest[(port, name)] = (line_id, count)
                totals[name] -= count
        for name, count in totals.items():
            if count <= 0:
                continue
            # Its newest point on the busiest port: normally the name's newest point.
            row = c.execute(
                "SELECT pp.line_id FROM plot_points pp INDEXED BY idx_plot_name_line "
                "CROSS JOIN lines l ON l.id = pp.line_id "
                "WHERE pp.name = ? AND pp.line_id <= ? AND l.port = ? "
                "ORDER BY pp.line_id DESC LIMIT 1", (name, high, busiest)
            ).fetchone()
            newest[(busiest, name)] = (row[0], count)
        out: dict[tuple[str, str], _PlotStat] = {}
        for (port, name), (line_id, count) in newest.items():
            sid, value, tick, ts = c.execute(
                "SELECT pp.sid, pp.value, pp.tick_ms, l.ts FROM plot_points pp "
                "INDEXED BY idx_plot_name_line CROSS JOIN lines l ON l.id = pp.line_id "
                # The line's last point of that name, as _note_plot keeps.
                "WHERE pp.name = ? AND pp.line_id = ? ORDER BY pp.rowid DESC LIMIT 1",
                (name, line_id),
            ).fetchone()
            out[(port, name)] = _PlotStat(sid, value, tick, ts, line_id, count)
        return out

    async def _rebuild_plot_summary(self) -> None:
        """Rebuild the summary from SQL without stopping the writer.

        Everything with id <= `high` is committed when the scan is launched, and the writer
        keeps folding newer lines into a fresh dict meanwhile; the two are disjoint by id,
        so the merge is exact. A delete during the scan re-dirties the summary, and the
        next read scans again: at most one chunk stale, never rebuilt in a loop.
        """
        async with self._plot_lock:
            if not self._plot_dirty:
                return
            self._plot_dirty = False
            high = self._next_id - 1
            self._plot_summary = live = {}
            try:
                scanned = await self._offload(self._scan_plot_summary, high=high)
            except BaseException:
                self._plot_dirty = True   # or the half-built summary stands until a delete
                raise
            for key, stat in live.items():
                base = scanned.get(key)
                if base is not None:
                    stat.count += base.count
                scanned[key] = stat
            self._plot_summary = scanned

    def query_plot_series(
        self,
        *,
        name: str,
        port: str | None = None,
        last_ms: int | None = None,
        since_id: int | None = None,
        id_from: int | None = None,
        id_to: int | None = None,
        limit: int = 10000,
        decimate: int = 1,
        conn: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """History for one channel, chronological (ascending line_id).

        `decimate` > 1 reduces a long window with **min/max** decimation: the matched
        points are cut into buckets of N (counting back from the newest) and each bucket
        contributes its lowest and highest sample. Keeping every Nth point instead is
        cheaper to write but aliases - a spike that falls between two kept samples vanishes
        entirely, which is exactly the event someone opens a plot to find. Min/max keeps the
        envelope, so a transient still shows up as a spike, just with less detail around it.
        A bucket therefore yields up to 2 points, so the reduction is about N/2, not N.

        `limit` caps the points considered (newest kept) before decimation.
        """
        c = conn if conn is not None else self._conn
        assert c is not None
        limit = max(0, min(int(limit), 100000))
        decimate = max(1, int(decimate))
        # plot_points carries no port column, but every row joins to its line, which does
        # - the same route query_can_frames already takes. Without the port term, two
        # boards declaring the same channel name interleaved into one series, with
        # non-monotonic ticks and one board's samples reported in the other's unit.
        window, wparams = self._window_terms(
            id_from=id_from, id_to=id_to, port=port, last_ms=last_ms, conn=conn,
            id_col="pp.line_id", port_col="l.port", ts_col="l.ts",
        )
        clauses = ["pp.name = ?", *window]
        params: list[Any] = [name, *wparams]
        if since_id is not None:
            clauses.append("pp.line_id > ?")
            params.append(since_id)
        where = " AND ".join(clauses)
        # The newest `limit` points first, walking idx_plot_name_line back from the top:
        # numbering the whole matching set before applying the cap cost 8.8 s against
        # 17 ms for a channel with 1.2M points. CROSS JOIN keeps plot_points outermost.
        newest = (
            "SELECT pp.line_id, l.ts, pp.tick_ms, pp.value "
            "FROM plot_points pp CROSS JOIN lines l ON l.id = pp.line_id "
            f"WHERE {where} ORDER BY pp.line_id DESC LIMIT ?"
        )
        if decimate == 1:
            rows = c.execute(newest, (*params, limit)).fetchall()
            return [dict(r) for r in reversed(rows)]
        # Rank each bucket's points by value in both directions; rank 1 in either is the
        # bucket's min or max. The rn tie-break makes the choice deterministic when several
        # samples share the extreme value, and collapses to one row when min and max are
        # the same sample. rn counts from the newest, so the buckets keep recent data whole.
        sql = (
            "SELECT line_id, ts, tick_ms, value FROM ("
            "  SELECT line_id, ts, tick_ms, value, rn,"
            "         ROW_NUMBER() OVER (PARTITION BY (rn - 1) / ? ORDER BY value, rn) AS lo,"
            "         ROW_NUMBER() OVER (PARTITION BY (rn - 1) / ? ORDER BY value DESC, rn) AS hi"
            "  FROM (SELECT line_id, ts, tick_ms, value,"
            "               ROW_NUMBER() OVER (ORDER BY line_id DESC) AS rn"
            f"        FROM ({newest}))"
            ") WHERE lo = 1 OR hi = 1 ORDER BY line_id"
        )
        rows = c.execute(sql, (decimate, decimate, *params, limit)).fetchall()
        return [dict(r) for r in rows]

    async def query_plot_series_safe(self, **kwargs: Any) -> list[dict[str, Any]]:
        """query_plot_series, off the loop (see _offload).

        The window-function scan can touch up to 100k rows of the matching set.
        """
        return await self._offload(self.query_plot_series, **kwargs)

    def plot_streams(
        self, *, id_from: int, id_to: int, conn: sqlite3.Connection | None = None,
    ) -> list[tuple[str, str | None, list[str]]]:
        """Each (port, stream) in an id window with its channel names, in definition order.

        Per port, since a sid is unique only within one (SPEC 2.5). Insertion order (rowid)
        is declaration order: a sample is flattened into points in the order its `!pd`
        names them, bit lanes included. A port's ad-hoc `!p` points have no sid and come
        after its streams as one group.
        """
        c = conn if conn is not None else self._conn
        assert c is not None
        rows = c.execute(
            "SELECT l.port AS port, pp.sid AS sid, pp.name AS name, MIN(pp.rowid) AS first_row "
            "FROM plot_points pp JOIN lines l ON l.id = pp.line_id "
            "WHERE pp.line_id BETWEEN ? AND ? GROUP BY l.port, pp.sid, pp.name "
            "ORDER BY l.port, pp.sid IS NULL, pp.sid, first_row",
            (id_from, id_to),
        ).fetchall()
        out: dict[tuple[str, str | None], list[str]] = {}
        for r in rows:
            out.setdefault((r["port"], r["sid"]), []).append(r["name"])
        return [(port, sid, names) for (port, sid), names in out.items()]

    async def plot_streams_safe(
        self, **kwargs: Any
    ) -> list[tuple[str, str | None, list[str]]]:
        """plot_streams, off the loop (see _offload). The GROUP BY scans the window."""
        return await self._offload(self.plot_streams, **kwargs)

    def _export_where(
        self, names: list[str], last_ms: int | None,
        id_from: int | None = None, id_to: int | None = None,
        conn: sqlite3.Connection | None = None, port: str | None = None,
        until_ts: float | None = None, since_ts: float | None = None,
        floor_ts: float | None = None,
    ) -> tuple[str, list[Any]]:
        # `conn` is threaded through rather than defaulted to self._conn: iter_plot_export
        # streams on a private connection off the loop, and a sqlite3 connection may not be
        # used from another thread.
        placeholders = ",".join("?" * len(names))
        window, wparams = self._window_terms(
            id_from=id_from, id_to=id_to, last_ms=last_ms, since_ts=since_ts,
            until_ts=until_ts, floor_ts=floor_ts, conn=conn,
            id_col="pp.line_id", ts_col="l.ts", port=port, port_col="l.port",
        )
        clauses = [f"pp.name IN ({placeholders})", *window]
        return " AND ".join(clauses), [*names, *wparams]

    def export_sids(
        self, *, names: list[str], last_ms: int | None = None,
        id_from: int | None = None, id_to: int | None = None,
        conn: sqlite3.Connection | None = None, port: str | None = None,
        until_ts: float | None = None, since_ts: float | None = None,
        floor_ts: float | None = None,
    ) -> list[Any]:
        """Distinct sids among the export rows (to reject a multi-stream wide export).

        The result is tiny, but the DISTINCT still scans every matching row, so callers
        on the event loop should prefer `export_sids_safe`.
        """
        c = conn if conn is not None else self._conn
        assert c is not None
        if not names:
            return []
        where, params = self._export_where(
            names, last_ms, id_from, id_to, conn, port, until_ts, since_ts, floor_ts
        )
        # CROSS JOIN: driven from idx_plot_name_line whatever the planner guesses of a
        # `port` filter, which otherwise walks every line of the port (REVIEW class 20).
        sql = (
            "SELECT DISTINCT pp.sid FROM plot_points pp CROSS JOIN lines l ON l.id = pp.line_id "
            f"WHERE {where}"
        )
        return [r["sid"] for r in c.execute(sql, params).fetchall()]

    async def export_sids_safe(self, **kwargs: Any) -> list[Any]:
        """export_sids, off the loop (see _offload). A DISTINCT scan over plot_points."""
        return await self._offload(self.export_sids, **kwargs)

    def first_export_line_id(
        self,
        *,
        names: list[str],
        last_ms: int | None = None,
        id_from: int | None = None,
        id_to: int | None = None,
        conn: sqlite3.Connection | None = None,
        port: str | None = None,
        until_ts: float | None = None,
        since_ts: float | None = None,
        floor_ts: float | None = None,
    ) -> int | None:
        """The line_id of the first row `iter_plot_export` would yield, or None if none.

        The line that anchors `decode` to the `!pd` definitions in force at the window's
        start.
        """
        if not names:
            return None
        conn = conn if conn is not None else self._conn
        assert conn is not None
        window, wparams = self._window_terms(
            id_from=id_from, id_to=id_to, last_ms=last_ms, since_ts=since_ts,
            until_ts=until_ts, floor_ts=floor_ts, conn=conn,
            id_col="pp.line_id", ts_col="l.ts", port=port, port_col="l.port",
        )
        # One LIMIT 1 seek per name along idx_plot_name_line, then the least: `name IN (..)
        # ORDER BY line_id LIMIT 1` sorted every match of two or more names first.
        wanted = list(dict.fromkeys(names))
        where = " AND ".join(["pp.name = n.name", *window])
        sql = (
            f"WITH n(name) AS (VALUES {','.join(['(?)'] * len(wanted))}) "
            "SELECT MIN((SELECT pp.line_id FROM plot_points pp "
            f"CROSS JOIN lines l ON l.id = pp.line_id WHERE {where} "
            "ORDER BY pp.line_id LIMIT 1)) FROM n"
        )
        first = conn.execute(sql, (*wanted, *wparams)).fetchone()[0]
        return None if first is None else int(first)

    async def first_export_line_id_safe(self, **kwargs: Any) -> int | None:
        """first_export_line_id, off the loop (see _offload): it seeks over plot_points."""
        return await self._offload(self.first_export_line_id, **kwargs)

    def iter_plot_export(
        self,
        *,
        names: list[str],
        last_ms: int | None = None,
        id_from: int | None = None,
        id_to: int | None = None,
        port: str | None = None,
        until_ts: float | None = None,
        since_ts: float | None = None,
        floor_ts: float | None = None,
    ):
        """Yield long-format export rows, ordered by (line_id, name), a page at a time.

        Opens its own read connection (WAL allows concurrent readers), so a million-row
        export never materializes in one list nor blocks the event loop - StreamingResponse
        consumes this generator in a worker thread. The connection allows cross-thread use
        because that pool calls `next()` serially. There is no row cap: a cap can only
        truncate a response whose headers have already gone out, which is
        byte-indistinguishable from a complete CSV.

        Each page is fetched whole before any row is yielded, so no statement (and no read
        snapshot) stays open while the client is slow or gone: a cursor held across yields
        pinned the WAL, which then grew until restart. Pages walk idx_plot_line, already in
        line order, and sort names within a line here: `ORDER BY line_id, name` sorted the
        whole selection before the first byte, spilling to the system temp dir. The window
        is resolved once, and its top frozen at the newest point, so a page does not
        re-evaluate a relative bound (class 44) nor run past the export's start.

        An in-memory DB cannot be reopened, so it falls back to the loop connection, which
        sqlite3 refuses to use from another thread: that generator must therefore be drained
        on the event loop. `open_plot_export` is what a request handler calls, and it is
        what applies that rule.
        """
        if not names:
            return
        private = self._db_path not in (":memory:", "")
        conn = self._open_export_conn() if private else self._conn
        assert conn is not None
        try:
            where, params = self._export_where(
                names, last_ms, id_from, id_to, conn, port, until_ts, since_ts, floor_ts
            )
            top = conn.execute("SELECT MAX(line_id) FROM plot_points").fetchone()[0] or 0
            sql = (
                "SELECT pp.line_id, l.ts, l.port, pp.tick_ms, pp.sid, pp.name, pp.value "
                "FROM plot_points pp INDEXED BY idx_plot_line "
                "CROSS JOIN lines l ON l.id = pp.line_id "
                f"WHERE {where} AND pp.line_id > ? AND pp.line_id <= ? "
                "ORDER BY pp.line_id LIMIT ?"
            )
            after, limit = 0, _EXPORT_CHUNK
            while True:
                rows = conn.execute(sql, (*params, after, top, limit)).fetchall()
                if not rows:
                    return
                if len(rows) == limit:
                    # The last line may go on past the page: it is fetched whole next time.
                    last = rows[-1]["line_id"]
                    rows = [r for r in rows if r["line_id"] != last]
                    if not rows:
                        limit *= 2   # one line filled the page
                        continue
                rows.sort(key=lambda r: (r["line_id"], r["name"]))
                for r in rows:
                    yield dict(r)
                after = rows[-1]["line_id"]
        finally:
            if private:
                conn.close()

    async def open_plot_export(self, **kwargs: Any):
        """The export rows for a streaming response: the generator, or a list in memory.

        An in-memory capture has no private connection to stream from, so `iter_plot_export`
        falls back to the loop connection - and StreamingResponse advances the generator on
        a worker thread, where sqlite3 raises ProgrammingError before a single row is
        yielded. The rows are materialized here on the loop instead; an in-memory capture is
        a test/demo configuration, so holding the selection in memory is acceptable. The
        file-backed path streams exactly as before.
        """
        if self._db_path in (":memory:", ""):
            return list(self.iter_plot_export(**kwargs))
        return self.iter_plot_export(**kwargs)

    def _iter_export_pages(
        self, page: Callable[[sqlite3.Connection, int | None], list[dict[str, Any]]],
        key: str, start_id: int | None = None,
    ):
        """Yield every row of a windowed read, ascending, a page at a time.

        The connection rule is `iter_plot_export`'s: a private read connection for a
        file-backed capture, the loop connection for an in-memory one (which cannot be
        reopened, so that generator must be drained on the loop - see `open_lines_export`).
        Paging on the id cursor rather than one unbounded cursor keeps each statement
        inside the 1000-row clamp the underlying reads apply anyway.
        """
        private = self._db_path not in (":memory:", "")
        conn = self._open_read_conn() if private else self._conn
        assert conn is not None
        try:
            since_id = start_id
            while True:
                rows = page(conn, since_id)
                if not rows:
                    return
                yield from rows
                since_id = rows[-1][key]
        finally:
            if private:
                conn.close()

    def iter_lines_export(self, **filters: Any):
        """Every matching line, ascending by id, for a streaming export (no limit)."""
        start_id = filters.pop("since_id", None)
        # _query_lines_on, not query_lines: it re-arms the match budget per page, so a long
        # export is not stopped by a deadline armed when the connection was opened.
        return self._iter_export_pages(
            lambda conn, since_id: self._query_lines_on(
                conn, since_id=since_id, limit=_EXPORT_PAGE, order="asc", **filters
            )[0],
            "id", start_id,
        )

    def iter_can_export(self, **filters: Any):
        """Every matching CAN frame, ascending by line id, for a streaming export."""
        start_id = filters.pop("since_id", None)
        return self._iter_export_pages(
            lambda conn, since_id: self.query_can_frames(
                conn=conn, since_id=since_id, limit=_EXPORT_PAGE, order="asc", **filters
            )[0],
            "line_id", start_id,
        )

    async def open_lines_export(self, **kwargs: Any):
        """The lines for a streaming response; see `open_plot_export` for the memory rule."""
        if self._db_path in (":memory:", ""):
            return list(self.iter_lines_export(**kwargs))
        return self.iter_lines_export(**kwargs)

    async def open_can_export(self, **kwargs: Any):
        """The CAN frames for a streaming response; see `open_plot_export`."""
        if self._db_path in (":memory:", ""):
            return list(self.iter_can_export(**kwargs))
        return self.iter_can_export(**kwargs)

    def _open_export_conn(self) -> sqlite3.Connection:
        """A private read connection for streaming export.

        `check_same_thread=False` is safe here: StreamingResponse's threadpool advances the
        generator one `next()` at a time, so the connection is never touched concurrently.
        """
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # -- retention --------------------------------------------------------------------

    def set_max_db_bytes(self, limit: int) -> None:
        """Live-apply a size cap (SPEC 3.3.1); 0 disables it. Picked up on the next check."""
        self._max_db_bytes = max(0, int(limit))

    def max_db_bytes(self) -> int:
        """The size cap in force. /status reports this, not the configured value: they are
        set together today, but a health surface must show what is applied."""
        return self._max_db_bytes

    def set_min_sessions(self, count: int) -> None:
        """Live-apply the session retention floor (SPEC 3.3.1); 0 disables it."""
        self._min_sessions = max(0, int(count))

    def retention_floor_id(self) -> int | None:
        """Lowest line id protected from age expiry, or None when nothing is protected.

        Age alone is a poor measure of what is worth keeping: a board captured over a quiet
        fortnight would otherwise lose its only recorded run to the calendar. The newest
        `min_sessions` sessions are therefore kept whatever their age, so old data survives
        while there is little of it and only expires once newer runs have accumulated.

        With fewer than `min_sessions` sessions recorded, every session is protected. Lines
        captured while no session was running are not protected by this floor - only the
        span from the oldest protected session onwards is.
        """
        assert self._conn is not None
        if self._min_sessions <= 0:
            return None
        row = self._conn.execute(
            "SELECT start_id FROM sessions ORDER BY id DESC LIMIT 1 OFFSET ?",
            (self._min_sessions - 1,),
        ).fetchone()
        if row is not None:
            return row["start_id"]
        # Fewer sessions than the floor: protect all of them, from the oldest onwards.
        row = self._conn.execute("SELECT MIN(start_id) AS m FROM sessions").fetchone()
        return row["m"]

    def _delete_oldest_chunk(self, limit: int, floor_id: int | None = None) -> int:
        """Delete up to `limit` of the oldest lines by id and commit (FK cascades children).

        `floor_id` keeps protected sessions out of the delete (see retention_floor_id).
        """
        guard = "" if floor_id is None else " WHERE id < ?"
        params: tuple[Any, ...] = (limit,) if floor_id is None else (floor_id, limit)
        return self._delete_lines(f"SELECT id FROM lines{guard} ORDER BY id LIMIT ?", params)

    def _delete_range_chunk(self, id_from: int, id_to: int, limit: int) -> int:
        return self._delete_lines(
            "SELECT id FROM lines WHERE id >= ? AND id <= ? ORDER BY id LIMIT ?",
            (id_from, id_to, limit),
        )

    async def delete_range(self, id_from: int, id_to: int) -> int:
        """Delete an explicit id range, in loop-yielding chunks. Returns lines removed.

        This is the deliberate counterpart to retention: retention only ever truncates the
        oldest end of the capture, whereas a purge removes exactly the span asked for, hole
        in the middle and all. Children cascade via the foreign keys, and freed pages are
        handed back where the database was created with incremental auto-vacuum.

        Held under `_sweep_lock` for the whole chunk loop, exactly as the sweeps are: a
        size sweep computes its `want` from the content size up front and then trims all of
        it, even though the purge yielding between chunks had already freed that space -
        measured at ~5000 rows removed beyond the cap's target. Serialising every bulk
        delete lets each one compute against a capture that is not moving under it.
        """
        if id_to < id_from:
            return 0
        return await self._delete_chunks(
            lambda: self._delete_range_chunk(id_from, id_to, _RETENTION_CHUNK)
        )

    async def delete_before_ts(self, before_ts: float, *, max_id: int | None = None) -> int:
        """Delete every line stamped before `before_ts` (`purge before_ts`), in chunks.

        By `ts`, not as an id range: `ts` is not monotonic in id (rows queue after they
        are stamped, and the wall clock can step), so the id of the newest row before the
        cutoff also covered newer rows below it and missed older ones above it.

        `max_id` (the span's highest id) keeps rows committed after the span was read out
        of the delete: `before_ts` may lie ahead of now, so they can be stamped before it.
        """
        floor = None if max_id is None else max_id + 1
        return await self._delete_chunks(
            lambda: self._delete_expired_chunk(before_ts, _RETENTION_CHUNK, floor)
        )

    def before_ts_span(
        self, before_ts: float, conn: sqlite3.Connection | None = None
    ) -> tuple[int, int | None, int | None]:
        """(count, lowest id, highest id) of the lines `delete_before_ts` would delete.

        A covering walk of idx_lines_ts over those rows: use `before_ts_span_safe` from
        the loop.
        """
        c = conn if conn is not None else self._conn
        assert c is not None
        row = c.execute(
            "SELECT COUNT(*), MIN(id), MAX(id) FROM lines INDEXED BY idx_lines_ts "
            "WHERE ts < ?",
            (before_ts,),
        ).fetchone()
        return int(row[0]), row[1], row[2]

    async def before_ts_span_safe(self, before_ts: float) -> tuple[int, int | None, int | None]:
        """before_ts_span, off the loop (see _offload)."""
        return await self._offload(self.before_ts_span, before_ts=before_ts)

    async def _delete_chunks(self, chunk: Callable[[], int]) -> int:
        """Run `chunk` until it deletes nothing, yielding between chunks, then reclaim."""
        async with self._sweep_lock:
            total = 0
            while True:
                n = chunk()
                if n == 0:
                    break
                total += n
                await asyncio.sleep(_CHUNK_YIELD_S)   # let the writer drain between chunks
            if total:
                assert self._conn is not None
                with contextlib.suppress(Exception):
                    _reclaim_pages(self._conn)
            return total

    def _estimated_rows(self) -> int:
        """The number of rows in `lines`.

        This was once MAX(id) - MIN(id) + 1, on the reasoning that ids are dense because
        only the oldest are ever deleted. `delete_range` breaks that assumption by design
        ("a hole in the middle and all"), and the error is not benign: an inflated count
        deflates bytes_per_row, which inflates `want`, which is then clamped against the
        same inflated count. Measured after one mid-capture purge, a 4 MiB cap deleted all
        100k remaining rows instead of the ~37k needed. A COUNT(*) rides idx_lines_ts and
        only runs while the daemon is already over its cap, so the scan is worth its cost.
        """
        assert self._conn is not None
        return int(self._conn.execute("SELECT COUNT(*) FROM lines").fetchone()[0])

    def content_bytes(self) -> int:
        """Bytes of live content: allocated pages minus the freelist.

        This, not the file size, is what the size cap is measured against. SQLite does not
        hand space back to the filesystem on DELETE; it keeps the pages on a freelist and
        reuses them. A cap applied to the file size would therefore still read "too big"
        after a trim and keep deleting until the capture was empty. Free pages are exactly
        the space the next lines will occupy, so excluding them makes the cap converge and
        the file plateau. The WAL is left out deliberately: the auto-checkpoint bounds it
        while no reader pins an old snapshot (streamed exports release theirs between
        pages), and `journal_size_limit` truncates it after, so it is overhead, not growth.
        """
        assert self._conn is not None
        page_size = self._conn.execute("PRAGMA page_size").fetchone()[0]
        page_count = self._conn.execute("PRAGMA page_count").fetchone()[0]
        freelist = self._conn.execute("PRAGMA freelist_count").fetchone()[0]
        return max(0, (page_count - freelist)) * page_size

    async def _trim_oldest(self, want: int, floor_id: int | None) -> int:
        """Delete up to `want` of the oldest lines, in loop-yielding chunks."""
        dropped = 0
        while dropped < want:
            n = self._delete_oldest_chunk(min(_RETENTION_CHUNK, want - dropped), floor_id)
            if n == 0:
                break
            dropped += n
            await asyncio.sleep(_CHUNK_YIELD_S)   # let the writer drain between chunks
        return dropped

    async def _sweep_size_async(self) -> int:
        """Trim the oldest lines until live content fits under the size cap.

        The target is 90% of the cap: without that headroom the next check would trim
        again immediately and the daemon would spend its life deleting a few rows at a
        time. Only ever removes the oldest lines, so a capture is truncated at its start,
        never sampled or holed in the middle.

        The session floor is honoured where it can be, so a protected run is the last thing
        to go. It cannot be honoured absolutely, though: if the protected sessions alone
        exceed the cap, refusing to trim them would quietly turn the cap into no cap at all
        and let the disk fill. So a second pass ignores the floor and says so loudly - a
        configured size cap is a hard bound, and the alternative is a silent one.
        """
        async with self._sweep_lock:
            return await self._sweep_size_locked()

    async def _sweep_size_reported(self) -> int:
        """The size sweep plus its sys row: every trim, startup's included, is recorded in
        the capture itself (SPEC 3.2), not only in the daemon's log."""
        trimmed = await self._sweep_size_async()
        if trimmed:
            await self.add_line(
                ts=time.time(), port="", dir="-", chan="sys", seq=None,
                raw=f"storage: trimmed {trimmed} oldest lines "
                    f"to stay under the {self._max_db_bytes} byte cap",
            )
        return trimmed

    async def _sweep_size_locked(self) -> int:
        cap = self._max_db_bytes
        if not cap:
            return 0
        used = self.content_bytes()
        if used <= cap:
            return 0
        rows = self._estimated_rows()
        if rows <= 0:
            return 0
        bytes_per_row = max(1.0, used / rows)
        excess = used - int(cap * 0.9)
        want = min(rows, max(1, int(excess / bytes_per_row)))
        floor_id = self.retention_floor_id()
        dropped = await self._trim_oldest(want, floor_id)
        if dropped < want and floor_id is not None:
            forced = await self._trim_oldest(want - dropped, None)
            if forced:
                dropped += forced
                log.warning(
                    "storage: the %d protected session(s) alone exceed the %d byte cap; "
                    "trimmed %d of their lines to keep the cap a real bound",
                    self._min_sessions, cap, forced,
                )
        if dropped:
            self.lines_trimmed += dropped
            # Return the freed pages to the filesystem where the database was created with
            # incremental auto-vacuum (see start()); a no-op on one that was not, where the
            # file simply plateaus at its high-water mark instead.
            with contextlib.suppress(Exception):
                _reclaim_pages(self._conn)
            log.warning(
                "storage: trimmed %d oldest lines to stay under the %d byte cap "
                "(live content was %d bytes)", dropped, cap, used
            )
        return dropped

    def _delete_expired_chunk(
        self, cutoff: float, limit: int, floor_id: int | None, since: float | None = None
    ) -> int:
        """Delete up to `limit` expired lines and commit. `DELETE ... LIMIT` needs a compile

        option the stdlib build lacks, so the bounded delete is expressed as a subselect.
        The FK cascade drops each line's can_frames/plot_points rows. `floor_id` keeps ids
        at and above it: the newest sessions however old they are (see retention_floor_id),
        or the rows a purge committed after its span (delete_before_ts).

        `ORDER BY ts`, not `ORDER BY id`: ordering by id made the planner prefer the table
        btree over idx_lines_ts and read every `raw` blob, and the LIMIT only cuts that
        short when rows really are expired. Nothing expired is the steady state of a
        capture inside its retention window, and that case scanned the whole table on the
        event loop every sweep (45 ms at 300k rows, linear from there). On the ts index the
        expired range is simply empty. Age is `ts`, so this selects by `ts` alone: `ts` is
        not monotonic in id, and an id bound would keep old rows and take new ones.

        `since` bounds the walk below (see _sweep_retention_locked): with a floor, every
        protected expired row would otherwise be read and rejected on each sweep.
        """
        terms, params = ["ts < ?"], [cutoff]
        if since is not None:
            terms.append("ts >= ?")
            params.append(since)
        if floor_id is not None:
            terms.append("id < ?")
            params.append(floor_id)
        return self._delete_lines(
            f"SELECT id FROM lines WHERE {' AND '.join(terms)} ORDER BY ts LIMIT ?",
            (*params, limit),
        )

    async def _sweep_retention_async(self) -> int:
        """Chunked retention that yields the loop between chunks so ingestion keeps draining.

        A large one-shot DELETE would hold the write lock and stall the writer task; each
        chunk commits and then a short sleep lets the writer run its own batch.
        The session floor is absolute here: age expiry never touches a protected run, so a
        quiet fortnight cannot cost you the only capture you have.
        """
        async with self._sweep_lock:
            return await self._sweep_retention_locked()

    async def _sweep_retention_locked(self) -> int:
        cutoff = time.time() - self._retention_days * 86400
        floor_id = self.retention_floor_id()
        # The last full sweep deleted everything older than its cutoff and below its floor,
        # so while the floor has not risen (None, no floor, is the highest) the walk starts
        # at that cutoff: rows the floor protects are otherwise read and rejected hourly,
        # on the loop. A risen floor unprotects rows of any age, so it walks everything, as
        # does a row the writer committed below that cutoff since (_check_stamp_order).
        since, start = None, self._last_age_sweep
        if start is not None:
            last_cutoff, last_floor = start
            if last_floor is None or (floor_id is not None and floor_id <= last_floor):
                since = last_cutoff
        total = 0
        while True:
            n = self._delete_expired_chunk(cutoff, _RETENTION_CHUNK, floor_id, since)
            total += n
            if n < _RETENTION_CHUNK:
                if self._last_age_sweep is start:   # else an old row landed mid-sweep
                    self._last_age_sweep = (cutoff, floor_id)
                return total
            await asyncio.sleep(_CHUNK_YIELD_S)

    async def _retention_loop(self) -> None:
        """Periodic maintenance: the size cap on a short tick, the age sweep hourly.

        The two run on different clocks because they answer to different things. Age
        retention only changes as the wall clock advances, so hourly is plenty. The size
        cap has to react to the capture rate, which can be four orders of magnitude apart
        between a quiet board and a saturated link, so it is checked every minute - three
        pragma reads, cheap enough to run when nothing is close to the cap.
        """
        ticks = 0
        while True:
            await asyncio.sleep(_SIZE_CHECK_S)
            ticks += 1
            await self.sweep_tick(ticks)

    def _reclaim_backlog(self) -> None:
        """Hand back one bounded slice of the freelist when there is a backlog worth it."""
        assert self._conn is not None
        freelist = self._conn.execute("PRAGMA freelist_count").fetchone()[0]
        if freelist >= _RECLAIM_MIN_PAGES:
            _reclaim_pages(self._conn)

    async def sweep_tick(self, tick: int = _RETENTION_TICKS) -> int:
        """One maintenance tick: the size cap, its sys row, and the age sweep when due.

        Separated from the sleeping so it is drivable. The sweeps themselves were already
        testable, but everything wrapped around them - the hourly cadence, the guard that
        keeps a failed sweep from killing the daemon, and the sys row a user actually sees
        - could only be reached by leaving a daemon running for a minute, so nothing
        covered them. `tick` counts size checks; the age sweep runs when it divides.

        Returns the number of lines trimmed by the size cap.
        """
        trimmed = 0
        try:
            trimmed = await self._sweep_size_reported()
            if tick % _RETENTION_TICKS == 0:
                await self._sweep_retention_async()
            # Every delete path leaves pages on the freelist, and only `_VACUUM_PAGES` of
            # them are handed back per call, so the drain has to be driven by the tick
            # rather than by whether this tick happened to trim anything.
            self._reclaim_backlog()
        except Exception as exc:  # a sweep failure must not kill the daemon
            log.error("retention sweep failed: %s", exc)
        return trimmed

    def db_size_bytes(self) -> int:
        """Bytes the capture occupies on disk: the database plus its write-ahead log.

        Under WAL the `-wal` sidecar holds committed data that has not been checkpointed
        back yet, and it can be a large share of the total during a fast capture. Counting
        only the main file would under-report what the capture is actually using.

        This is disk usage, and it is NOT what the size cap is measured against: that is
        content_bytes(), which excludes the freelist. `/status` reports both, because
        reporting this one alone beside db_max_bytes made a cap that was working correctly
        read as broken (24 MB against a 2 MiB cap while the enforced number sat at 2.0 MB).
        """
        total = 0
        for path in (self._db_path, self._db_path + "-wal"):
            try:
                total += os.path.getsize(path)
            except OSError:
                pass   # main file not created yet, or no WAL right now
        return total
