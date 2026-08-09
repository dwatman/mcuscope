"""SQLite capture storage (SPEC 3.5).

One SQLite connection, touched only from the event-loop thread. Writes go through a
single asyncio writer task draining a queue, so the ingestion path never blocks on
disk and row ids/broadcasts are assigned at one serialization point. Reads (the query
helpers) run synchronously on the loop; they are small (limit capped at 1000).

The serial reader threads never touch SQLite: they hand bytes to the loop via
`loop.call_soon_threadsafe`, and the loop-side code calls `add_line`, which enqueues a
write and awaits the assigned row.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
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

CREATE TABLE IF NOT EXISTS can_frames(
  line_id INTEGER PRIMARY KEY REFERENCES lines(id) ON DELETE CASCADE,
  tick_ms INTEGER,
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

-- Small key/value side table. Its only key so far is `capture`: an opaque token
-- identifying this id space, handed to every client so none of them has to guess from id
-- arithmetic whether the rows it holds still belong to the stream it is reading (SPEC 3.4).
CREATE TABLE IF NOT EXISTS meta(
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# Columns added after the first release, applied to an existing capture with ALTER TABLE.
# `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so a schema
# change needs this list as well as the definition above.
_MIGRATIONS = (
    ("sessions", "auto", "ALTER TABLE sessions ADD COLUMN auto INTEGER NOT NULL DEFAULT 0"),
)

def _mint_capture_id() -> str:
    """A fresh capture identity. Opaque to clients: only equality is ever tested."""
    return uuid.uuid4().hex


_EXPORT_CHUNK = 10_000     # rows fetched per fetchmany() when streaming an export
_RETENTION_CHUNK = 5_000   # rows deleted per retention DELETE, committed one chunk at a time
_VACUUM_PAGES = 2_000      # pages reclaimed per incremental_vacuum call (see _reclaim_pages)


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

    Bounded per call because both callers run on the event loop: an unbounded reclaim is
    O(freelist), and a capture that has plateaued has a large one. 2000 pages is 8 MB at
    the 4 kB page size, measured at 15.8 ms, against 55 ms to drain 7518 pages at once.
    The retention sweep runs periodically, so a backlog drains over successive ticks.
    """
    conn.executescript(f"PRAGMA incremental_vacuum({_VACUUM_PAGES});")
    conn.commit()
_WRITE_QUEUE_MAX = 10_000  # bound the write queue so a stalled writer cannot eat RAM forever
_SIZE_CHECK_S = 60         # seconds between size-cap checks (see _retention_loop)
_RETENTION_TICKS = 60      # size-cap ticks per age sweep, i.e. hourly
MAX_SUBSCRIBERS = 256      # cap fan-out queues so connect/disconnect churn cannot eat RAM

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
    plot: list[dict[str, Any]] | None
    future: asyncio.Future


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
    index = _schema_statement("CREATE INDEX IF NOT EXISTS idx_sessions_name")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(create.replace("IF NOT EXISTS sessions(", "sessions_autoinc(", 1))
        conn.execute(
            f"INSERT INTO sessions_autoinc({cols}) SELECT {cols} FROM sessions"
        )
        # The index goes with the old table, so it must be dropped before the rename or
        # `CREATE INDEX IF NOT EXISTS` quietly skips rebuilding it under the wanted name.
        conn.execute("DROP INDEX IF EXISTS idx_sessions_name")
        conn.execute("DROP TABLE sessions")
        conn.execute("ALTER TABLE sessions_autoinc RENAME TO sessions")
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

_match_pool: ThreadPoolExecutor | None = None
_match_pool_lock = threading.Lock()


def match_executor() -> ThreadPoolExecutor:
    """The bounded thread pool that runs every user-supplied regex.

    All regex work the API accepts (`match=` on /lines and /wait, the /assert patterns) is
    user text, and the stdlib `re` engine cannot be interrupted mid-backtrack, so a
    catastrophic pattern owns its worker until it finishes. What matters is where that
    worker comes from: on the default executor a burst of slow patterns would stall every
    other piece of thread work the daemon does. Giving regex work its own pool of
    MATCH_WORKERS confines the damage to other regex work. (The serial reader join, the
    one wait that must never queue, has its own pool again in `serial_link`.)

    Process-wide and never explicitly shut down: the daemon owns it for its lifetime, and
    the threads are idle between queries. A pattern still running at interpreter exit will
    delay exit (ThreadPoolExecutor joins its workers via atexit); bounding the pool caps
    how many such threads can exist, it does not make `re` interruptible.
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
    from an unrelated SQL error.
    """
    cache: dict[str, regex.Pattern[str]] = {}
    deadline: list[float | None] = [None]

    def regexp(pattern: str, value: str | None) -> bool:
        if value is None:
            return False
        pat = cache.get(pattern)
        if pat is None:
            pat = regex.compile(pattern)
            cache[pattern] = pat
        if deadline[0] is None:
            deadline[0] = time.monotonic() + budget_s
        remaining = deadline[0] - time.monotonic()
        if remaining <= 0:
            regexp.timed_out = True
            raise TimeoutError("match budget exceeded")
        try:
            return pat.search(value, timeout=min(MATCH_TIMEOUT_S, remaining)) is not None
        except TimeoutError:
            regexp.timed_out = True
            raise

    regexp.timed_out = False
    return regexp


class Store:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._queue: asyncio.Queue[_WriteReq | None] | None = None
        self._writer_task: asyncio.Task | None = None
        self._retention_task: asyncio.Task | None = None
        self._initial_sweep_task: asyncio.Task | None = None
        self._retention_days = 10
        self._max_db_bytes = 0   # 0 disables the size cap (SPEC 3.3)
        self._min_sessions = 0   # sessions kept regardless of age (0 disables the floor)
        self.lines_trimmed = 0   # lines dropped by the size cap, reported on /status
        # Writes the writer task could not persist, reported on /status (SPEC 3.4). The
        # serial layer counts a line as received before handing it here, so a failed write
        # is a line counted and lost; without this counter the only trace was a log line
        # while every health surface stayed green.
        self.write_errors = 0
        self._subscribers: dict[asyncio.Queue, str | None] = {}
        # Rows shed from a slow subscriber's queue: per queue, so the pump can announce the
        # gap in-band, and a lifetime total for /status, because a feed that is losing rows
        # while every other field reads healthy is the shape class 12 exists for.
        self._sub_dropped: dict[asyncio.Queue, int] = {}
        self.ws_dropped = 0
        # Next `lines.id` to hand out. The daemon owns this sequence (see _insert_batch);
        # it is seeded from the file at start() and resynced if a batch ever fails.
        self._next_id = 1
        self._capture_id = ""
        # Serialises the retention/size sweeps against each other. Both compute how much to
        # delete up front and then delete in yielding chunks, so two overlapping sweeps each
        # trim a target the other has already met: measured on a 200k-row capture, one sweep
        # correctly dropped 159k rows and two gathered dropped all 200k. The startup sweep
        # can still be running when the 60 s tick fires, so the overlap is routine.
        self._sweep_lock = asyncio.Lock()

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
        conn.create_function("regexp", 2, _make_regexp(), deterministic=True)
        # Incremental auto-vacuum lets a size-capped capture hand freed pages back to the
        # filesystem after a trim, instead of the file sitting at its high-water mark. It
        # can only be chosen before the database header is materialised, so this applies to
        # captures this daemon creates; an older one keeps its setting and simply plateaus.
        # This MUST precede journal_mode=WAL: setting the journal mode writes the header,
        # after which auto_vacuum silently stays 0 and every PRAGMA incremental_vacuum
        # below becomes a no-op (a trimmed capture then never gives space back).
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
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
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        _apply_migrations(conn)
        conn.commit()
        self._conn = conn
        # Seed past any id a stored session still refers to, not just past the live rows.
        # Sessions record a start_id/end_id span and are never deleted by retention or by
        # `purge --all`, so an emptied `lines` table would restart the sequence at 1 and the
        # next run's lines would fall inside an old session's range: `session show run-alpha`
        # then returned run-beta's traffic, and `session export`/`purge --session` acted on
        # it. Ids must never be reused while anything still points at them.
        self._next_id = max(self.max_id(), self._max_session_ref_id()) + 1
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
        self._queue = asyncio.Queue(maxsize=_WRITE_QUEUE_MAX)
        self._writer_task = asyncio.create_task(self._writer())
        # The initial sweep runs in the background: a large expired backlog must not
        # hold up daemon startup (the chunked sweep yields the loop between chunks).
        self._initial_sweep_task = asyncio.create_task(self._initial_sweep())
        self._retention_task = asyncio.create_task(self._retention_loop())

    async def _initial_sweep(self) -> None:
        try:
            await self._sweep_retention_async()
            await self._sweep_size_async()
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
        if self._conn is not None:
            self._conn.close()
            self._conn = None

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
            if not req.future.done():
                self._fail_write(req, StoreError(reason))
                pending += 1
        if pending:
            log.warning("store: failed %d queued write(s): %s", pending, reason)

    def _fail_write(self, item: _WriteReq, exc: Exception) -> None:
        """Resolve one queued write as failed, counting it on the way.

        Every path that loses a line goes through here so `write_errors` is the count of
        lines the capture was handed and did not store, whatever the reason (insert,
        commit, or a shutdown that left the queue undrained).
        """
        self.write_errors += 1
        if not item.future.done():
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
            batch = [req]
            stop = False
            while len(batch) < _MAX_BATCH_ROWS:  # absorb what is queued, up to the cap
                try:
                    nxt = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if nxt is None:
                    stop = True  # sentinel: flush this batch, then exit
                    break
                batch.append(nxt)
            assert self._conn is not None
            try:
                self._insert_batch(batch)
                results = [(item, item.row, None) for item in batch]
            except Exception as exc:
                # A single bad row aborts the whole executemany, so redo the batch one row
                # at a time to isolate it: the others must still land. This is also the
                # self-heal for a stale id sequence (see _insert_batch).
                log.warning("batched insert failed (%s); retrying row by row", exc)
                with contextlib.suppress(Exception):
                    self._conn.rollback()
                try:
                    results = self._insert_individually(batch)
                except Exception as exc2:
                    # The row-by-row fallback itself can fail (it re-reads max_id() to
                    # resync the sequence, so a connection-level error reaches here). Letting
                    # it escape kills the writer task: this batch's futures would never
                    # resolve, every later submit_line would hang, and the queue would fill
                    # to _WRITE_QUEUE_MAX and block the serial consumer for good. Fail this
                    # batch's callers instead and keep draining.
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
                if elapsed >= _SLOW_COMMIT_S:
                    log.warning(
                        "slow capture commit: %.0f ms for %d rows", elapsed * 1000, len(batch)
                    )
            except Exception as exc:
                # A commit failure (disk full, I/O error) must not kill the writer:
                # fail this batch's callers, roll back, and keep draining the queue.
                log.error("batch commit failed: %s", exc)
                with contextlib.suppress(Exception):
                    self._conn.rollback()
                for item, _row, item_exc in results:
                    self._fail_write(
                        item,
                        item_exc if item_exc is not None
                        else StoreError(f"commit failed: {exc}"),
                    )
                if stop:
                    return
                continue
            for item, row, exc in results:
                if exc is not None:
                    self._fail_write(item, exc)
                    continue
                if not item.future.done():
                    item.future.set_result(row)
                self._broadcast(row)
            if stop:
                return

    def _insert_batch(self, batch: list[_WriteReq]) -> None:
        """Insert a whole batch as one statement per table, filling in each row's id.

        The daemon is the sole writer of this database (SPEC 3.5), so it owns the `lines`
        id sequence and takes the next id in Python rather than reading `lastrowid` back
        per row. That is what makes the batch expressible as three `executemany` calls
        instead of one `execute` per line (plus one per can/plot child) - the largest
        single cost of capture once commits were batched.

        If the sequence is ever wrong - another process wrote to the same file - the
        primary-key collision surfaces as an exception here and the caller falls back to
        `_insert_individually`, which lets SQLite assign ids and resyncs the counter.
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
            for pt in item.plot or ():
                plot_rows.append((line_id, pt["tick_ms"], pt["sid"], pt["name"], pt["value"]))
            can = item.can
            if can is not None:
                can_rows.append(
                    (line_id, can["tick_ms"], can["can_id"], int(can["ext"]),
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
                "INSERT INTO can_frames(line_id, tick_ms, can_id, ext, rtr, dlc, data) "
                "VALUES(?,?,?,?,?,?,?)",
                can_rows,
            )
        self._next_id = first + len(batch)

    def _insert_individually(
        self, batch: list[_WriteReq]
    ) -> list[tuple[_WriteReq, dict[str, Any] | None, Exception | None]]:
        """Fallback for a batch that would not go in as one statement: one row at a time.

        Each row is inserted on its own so a single bad one (a CHECK violation, a duplicate
        id) fails alone. Ids come from SQLite here, so the counter is resynced afterwards.
        """
        results: list[tuple[_WriteReq, dict[str, Any] | None, Exception | None]] = []
        for item in batch:
            try:
                results.append((item, self._insert(item.row, item.can, item.plot), None))
            except Exception as exc:  # one bad insert must not lose the others
                log.warning("line insert failed: %s", exc)
                results.append((item, None, exc))
        self._next_id = max(self.max_id(), self._max_session_ref_id()) + 1
        return results

    def _insert(
        self,
        row: dict[str, Any],
        can: dict[str, Any] | None,
        plot: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Insert one line (+ optional can/plot rows), letting SQLite assign the id.

        If a can/plot child insert fails, the freshly inserted line row is deleted
        again so the batch commit cannot persist an orphan line.
        """
        assert self._conn is not None
        cur = self._conn.execute(
            "INSERT INTO lines(ts, port, dir, chan, seq, raw) VALUES(?,?,?,?,?,?)",
            (row["ts"], row["port"], row["dir"], row["chan"], row["seq"], row["raw"]),
        )
        line_id = cur.lastrowid
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
        line_id: int | None,
        can: dict[str, Any] | None,
        plot: list[dict[str, Any]] | None,
    ) -> None:
        assert self._conn is not None
        if plot:
            self._conn.executemany(
                "INSERT INTO plot_points(line_id, tick_ms, sid, name, value) VALUES(?,?,?,?,?)",
                [(line_id, pt["tick_ms"], pt["sid"], pt["name"], pt["value"]) for pt in plot],
            )
        if can is not None:
            self._conn.execute(
                "INSERT INTO can_frames(line_id, tick_ms, can_id, ext, rtr, dlc, data) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    line_id,
                    can["tick_ms"],
                    can["can_id"],
                    int(can["ext"]),
                    int(can["rtr"]),
                    can["dlc"],
                    can["data"],
                ),
            )

    async def submit_line(
        self,
        *,
        ts: float,
        port: str,
        dir: str,
        chan: str,
        seq: int | None,
        raw: str,
        can: dict[str, Any] | None = None,
        plot: list[dict[str, Any]] | None = None,
    ) -> asyncio.Future:
        """Queue a line for the writer and return the future carrying its stored row.

        This is `add_line` without the await, so a caller holding a whole burst can queue
        every line before yielding. That is what lets the writer batch them: awaiting each
        row before queueing the next leaves the writer's queue with one item at a time, so
        the batching loop in `_writer` degenerates into a commit (and a loop wakeup) per
        line, which at a few thousand lines a second dominates the cost of capture.

        `put` only suspends when the queue is full, so a burst that fits is queued without
        an intervening loop iteration.
        """
        assert self._queue is not None
        if not self.writer_alive:
            # A dead writer never drains the queue, so the future below would never
            # resolve: every caller (including the lifespan's own shutdown rows, awaited
            # under a suppress that cannot catch a hang) would block forever. Fail fast
            # instead, so the failure is visible and shutdown still completes.
            raise StoreError("store writer is not running")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        # `id` is filled in by the writer; it leads so the row serializes in schema order.
        row = {"id": None, "ts": ts, "port": port, "dir": dir, "chan": chan,
               "seq": seq, "raw": raw}
        await self._queue.put(_WriteReq(row=row, can=can, plot=plot, future=fut))
        return fut

    async def add_line(self, **kwargs: Any) -> dict[str, Any]:
        """Enqueue a line and return the stored row (with its id): `submit_line` + await."""
        return await (await self.submit_line(**kwargs))

    # -- WebSocket fan-out ------------------------------------------------------------

    def subscribe(self, port_filter: str | None = None, maxsize: int = 2000) -> asyncio.Queue:
        if len(self._subscribers) >= MAX_SUBSCRIBERS:
            raise StoreError(f"too many subscribers (max {MAX_SUBSCRIBERS})")
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers[q] = port_filter
        self._sub_dropped[q] = 0
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.pop(q, None)
        self._sub_dropped.pop(q, None)

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
        if not self._subscribers:   # the common case: nothing attached, no list to build
            return
        for q, port_filter in self._subscribers.items():  # no awaits below: no copy needed
            if port_filter is not None and row["port"] != port_filter:
                continue
            if q.full():  # slow consumer: drop the oldest, never block the writer
                try:
                    q.get_nowait()
                    self._sub_dropped[q] = self._sub_dropped.get(q, 0) + 1
                    self.ws_dropped += 1
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(row)
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

        `start_id` is the id the next stored line will take, so everything captured from
        here on belongs to the session, including the boundary marker written below.

        `auto` marks a session the daemon opened for its own run rather than one someone
        named. The two are stored identically and both count towards the retention floor;
        the flag exists so the UI can keep offering "start a run" while one is open, and so
        an empty one can be dropped on close (see `stop_session`).
        """
        assert self._conn is not None
        await self.stop_session()
        start_id = self._next_id
        cur = self._conn.execute(
            "INSERT INTO sessions(name, note, started_ts, start_id, auto) VALUES(?,?,?,?,?)",
            (name, note, time.time(), start_id, int(bool(auto))),
        )
        self._conn.commit()
        session_id = cur.lastrowid
        # A marker, not a sys row: the UI draws markers as a full-width divider, which is
        # exactly how a run boundary should read in the terminal.
        await self.add_line(
            ts=time.time(), port="", dir="-", chan="marker", seq=None,
            raw=f"session start: {name}" + (f" ({note})" if note else ""),
        )
        return self.resolve_session(str(session_id)) or {}

    async def stop_session(self) -> dict[str, Any] | None:
        """Close the running session, if any, and return it. Idempotent.

        An automatic session that captured no device traffic is dropped rather than kept:
        a daemon started and stopped without a board attached is not a run, and a list
        full of those would bury the ones that are. Its lines stay in the capture; only
        the label goes.
        """
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
            "AND (chan IN ('debug','cmd','resp','event') OR (chan = 'marker' AND dir = 'rx')) "
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
        self, dest_path: str, *, id_from: int, id_to: int | None, session: dict[str, Any]
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
        """
        conn = sqlite3.connect(dest_path)
        try:
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
                    "INSERT INTO can_frames SELECT line_id, tick_ms, can_id, ext, rtr, dlc, data "
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

    def _delete_lines(self, sql: str, params: tuple[Any, ...]) -> int:
        """Run one chunked `DELETE FROM lines`, commit, and return the rows removed.

        Every lines delete goes through here, because `lines.id` is a plain rowid: deleting
        the highest id frees it, and the next line captured takes it again. From then on the
        ids a client holds no longer name the rows it thinks they do, and its dedup
        watermark discards the new capture as duplicates. So that case, and only that case,
        mints a new capture identity, which every client reads as "drop what you hold and
        re-seed". Trimming the oldest end - retention, the size cap - leaves the maximum
        alone and is not a reset.
        """
        assert self._conn is not None
        max_before = self.max_id()
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        if cur.rowcount and self.max_id() < max_before:
            self._new_capture()
        return cur.rowcount

    def max_id(self, conn: sqlite3.Connection | None = None) -> int:
        c = conn if conn is not None else self._conn
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
        if id_to is None:
            return time.time() - last_ms / 1000.0
        c = conn if conn is not None else self._conn
        assert c is not None
        row = c.execute(
            "SELECT ts FROM lines WHERE id <= ? ORDER BY id DESC LIMIT 1", (id_to,)
        ).fetchone()
        anchor = row[0] if row else time.time()
        return anchor - last_ms / 1000.0

    def _window_terms(
        self,
        *,
        id_from: int | None = None,
        id_to: int | None = None,
        port: str | None = None,
        chans: list[str] | None = None,
        last_ms: float | None = None,
        conn: sqlite3.Connection | None = None,
        id_col: str = "id",
        port_col: str = "port",
        ts_col: str = "ts",
        unindexed_port: bool = False,
    ) -> tuple[list[str], list[Any]]:
        """The id/port/chan/last_ms predicates every read over a capture window shares.

        Parameterised by column because the same window is expressed against `lines`
        (`id`, `port`, `ts`) and against a join (`cf.line_id`, `l.port`, `l.ts`). Five
        reads share it, and share with it that `last_ms` is anchored through `_window_floor`
        rather than at `now`: forgetting that is silent, since the query still runs and just
        returns almost nothing whenever an upper id bound is in force.

        `unindexed_port` applies the `+port` de-optimisation, which every read combining
        `port` with `chan` needs - `query_lines` records the measurement.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if id_from is not None:
            clauses.append(f"{id_col} >= ?")
            params.append(id_from)
        if id_to is not None:
            clauses.append(f"{id_col} <= ?")
            params.append(id_to)
        if port:
            clauses.append(f"+{port_col} = ?" if unindexed_port else f"{port_col} = ?")
            params.append(port)
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
            clauses.append(f"{ts_col} >= ?")
            params.append(floor_ts)
            clauses.append(f"{id_col} >= ?")
            params.append(self._window_id_floor(floor_ts, conn))
        return clauses, params

    def _window_id_floor(self, floor_ts: float, conn: sqlite3.Connection | None = None) -> int:
        """The lowest id a `last_ms` window can contain, as a bound an index can seek to.

        `ts >= ?` alone is not enough: `/lines` orders by id, so the planner reads the
        table btree backwards and only stops early when the window actually holds
        `limit+1` rows. A *quiet* window therefore reads the whole table on the event
        loop - 46 ms at 300k rows, 0.6 ms once the window is busy, the same busy/quiet
        asymmetry that idx_lines_port_id was added for. One `idx_lines_ts` step resolves
        the cutoff to an id, and every window read then rides a primary-key range.

        Assumes `ts` rises with `id`, which holds because the host stamps every line at
        receive time on the single writer. The `ts` term stays in the query regardless,
        so the window is still filtered by time, not by the id alone.

        With nothing inside the window, one past the newest id: the window is empty, and
        saying so as a bound is what keeps the empty case off the table btree.
        """
        c = conn if conn is not None else self._conn
        assert c is not None
        row = c.execute(
            "SELECT id FROM lines WHERE ts >= ? ORDER BY ts LIMIT 1", (floor_ts,)
        ).fetchone()
        return int(row[0]) if row is not None else self.max_id(c) + 1

    def query_lines(
        self,
        *,
        port: str | None = None,
        chans: list[str] | None = None,
        match: str | None = None,
        since_id: int | None = None,
        since_ts: float | None = None,
        last_ms: int | None = None,
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
        # `+port` where a chan filter is present too, to keep the planner off
        # idx_lines_port_id for this term. `chan` is the selective one (a capture is
        # usually one board and many channels), but with both columns indexed and no
        # sqlite_stat1 the planner cannot know that, picks the port index and discards
        # the chan seek. Measured at 1M lines, one port, 3 marker rows: 319 ms against
        # 0.09 ms, and query_lines_safe runs this inline on the loop. The unary + is
        # SQLite's documented way to make a term unusable by an index; it changes no
        # result, only the plan.
        clauses, params = self._window_terms(
            id_from=id_from, id_to=id_to, port=port, chans=chans, last_ms=last_ms,
            conn=conn, unindexed_port=bool(chans),
        )
        if match:
            clauses.append("raw REGEXP ?")
            params.append(match)
        if since_id is not None:
            clauses.append("id > ?")
            params.append(since_id)
        if since_ts is not None:
            clauses.append("ts > ?")
            params.append(since_ts)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        order_sql = "DESC" if order == "desc" else "ASC"
        sql = f"SELECT id, ts, port, dir, chan, seq, raw FROM lines {where} ORDER BY id {order_sql} LIMIT ?"  # noqa: E501
        rows = c.execute(sql, (*params, limit + 1)).fetchall()
        truncated = len(rows) > limit
        return [dict(r) for r in rows[:limit]], truncated

    def count_lines(
        self,
        *,
        port: str | None = None,
        chans: list[str] | None = None,
        id_from: int | None = None,
        id_to: int | None = None,
        last_ms: float | None = None,
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
            id_from=id_from, id_to=id_to, port=port, chans=chans, last_ms=last_ms, conn=conn,
            unindexed_port=bool(chans),
        )
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        row = c.execute(f"SELECT COUNT(*) AS n FROM lines {where}", params).fetchone()
        return int(row["n"])

    def _read_on_private_conn(self, reader: Callable[..., Any], **kwargs: Any) -> Any:
        """Run one read on a private connection, opened and closed around it."""
        conn = self._open_read_conn()
        try:
            return reader(conn=conn, **kwargs)
        finally:
            conn.close()

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

    async def count_lines_safe(self, **kwargs: Any) -> int:
        """count_lines, off the loop (see _offload).

        The counts that report what a purge would remove and how many lines an assertion
        looked at are whole-capture reads: 44 ms at 1M rows, 230 ms at 3M for the dry run.
        """
        return await self._offload(self.count_lines, **kwargs)

    def last_id_before_ts(self, ts: float, conn: sqlite3.Connection | None = None) -> int | None:
        """Highest line id older than `ts`, so a time-based purge becomes an id range.

        `MAX(id) WHERE ts < ?` had to read every row below the cutoff (272 ms at 300k with
        a mid-capture cutoff, and the same again when nothing was old enough), on the loop.
        The newest row below the cutoff carries that id, and idx_lines_ts reaches it in one
        seek: 0.07 ms, and O(log n) rather than O(cutoff), so this stays inline.

        Both spellings assume `ts` rises with `id`, as does the id range the caller then
        deletes; the host stamps `ts` at receive time on the single writer.
        """
        c = conn if conn is not None else self._conn
        assert c is not None
        row = c.execute(
            "SELECT id AS m FROM lines WHERE ts < ? ORDER BY ts DESC LIMIT 1", (ts,)
        ).fetchone()
        return row["m"] if row is not None else None

    def _open_read_conn(self) -> sqlite3.Connection:
        """Open a private read connection to the same DB file (WAL allows concurrent readers).

        Used to run a match query on a worker thread without sharing the loop-thread connection.
        """
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.create_function("regexp", 2, _make_regexp(), deterministic=True)
        return conn

    def _query_lines_threadsafe(self, **kwargs: Any) -> tuple[list[dict[str, Any]], bool]:
        conn = self._open_read_conn()
        # Arm a fresh budget for THIS query. _open_read_conn registers a closure too, but
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
                raise MatchBudgetExceeded(
                    "match pattern exceeded the matching time budget; simplify the regex"
                ) from None
            raise
        finally:
            conn.close()

    async def query_lines_safe(self, **kwargs: Any) -> tuple[list[dict[str, Any]], bool]:
        """query_lines, but run a match-bearing query off the event loop.

        Match queries execute on the dedicated match_executor against a private read
        connection, so a slow pattern ties up one of its workers while ingestion,
        detach/shutdown joins and other clients keep running. That separation only holds
        because the pattern runs on the `regex` engine, which releases the GIL and honours
        a timeout (see _make_regexp); with stdlib `re` the pool was decoration. Match-free
        queries are cheap and bounded (limit <= 1000), so they run inline on the loop.
        Falls back to inline for an in-memory DB, which cannot be reopened from another
        thread - that path still gets a budget, just on the loop connection.
        """
        offloadable = bool(kwargs.get("match")) and self._db_path not in (":memory:", "")
        if not offloadable:
            if kwargs.get("match") and self._conn is not None:
                rx = _make_regexp()   # re-arm: a per-connection deadline would be stale
                self._conn.create_function("regexp", 2, rx, deterministic=True)
                try:
                    return self.query_lines(**kwargs)
                except sqlite3.OperationalError:
                    if rx.timed_out:
                        raise MatchBudgetExceeded(
                            "match pattern exceeded the matching time budget; "
                            "simplify the regex"
                        ) from None
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

    def query_can_frames(
        self,
        *,
        port: str | None = None,
        can_id: int | None = None,
        last_ms: int | None = None,
        since_id: int | None = None,
        id_from: int | None = None,
        id_to: int | None = None,
        limit: int = 100,
        conn: sqlite3.Connection | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        conn = conn if conn is not None else self._conn
        assert conn is not None
        limit = max(0, min(int(limit), 1000))
        clauses, params = self._window_terms(
            id_from=id_from, id_to=id_to, port=port, last_ms=last_ms, conn=conn,
            id_col="cf.line_id", port_col="l.port", ts_col="l.ts",
        )
        if can_id is not None:
            clauses.append("cf.can_id = ?")
            params.append(can_id)
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
            "SELECT cf.line_id, l.ts, cf.tick_ms, cf.can_id, cf.ext, cf.rtr, cf.dlc, cf.data "
            "FROM can_frames cf CROSS JOIN lines l ON l.id = cf.line_id "
            f"{where} ORDER BY cf.line_id DESC LIMIT ?"
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
                    "tick_ms": r["tick_ms"],
                    "can_id": r["can_id"],
                    "ext": bool(r["ext"]),
                    "rtr": bool(r["rtr"]),
                    "dlc": r["dlc"],
                    "data_hex": p.bytes_to_hex(data),
                }
            )
        return out, truncated

    # -- plot reads (SPEC 9.2) --------------------------------------------------------

    def query_plot_channels(
        self, conn: sqlite3.Connection | None = None, port: str | None = None
    ) -> list[dict[str, Any]]:
        """One row per distinct channel name: sid, point count, and its latest sample.

        Units/scale/type are not stored here; the server merges those in from its live
        `!pd` definition cache. Channels are keyed by name alone (SPEC 2.5). A single
        GROUP BY pass computes MAX(line_id) + COUNT(*) per name, then joins back to fetch
        the latest sample, instead of a correlated subquery scan per row.

        Each row also reports the `port` its newest sample came from, and `port=` filters
        to one board. Name alone is not unique across ports: two boards declaring `temp`
        produced a single merged channel whose unit and scale came from whichever declared
        last, with both boards' samples in it. The filter is the way to tell them apart.
        """
        c = conn if conn is not None else self._conn
        assert c is not None
        inner_from = ""
        params: list[Any] = []
        if port:
            # A join, not `line_id IN (SELECT id FROM lines WHERE port = ?)`: the IN form
            # made the planner scan all of `lines` to build the id list, plus a bloom filter
            # and a second temp b-tree for the GROUP BY. Measured at 1M lines: 190 ms that
            # way, 138 ms as a join, against 33 ms unfiltered. The join keeps the covering
            # index on plot_points and pays one primary-key probe per point.
            # CROSS JOIN, to pin the drive order the same way /can/frames does. Once
            # `lines` gained idx_lines_port_id (for /lines?port=), `li.port = ?` read as
            # selective and the planner drove the join from `lines`, probing plot_points
            # per line and adding a temp b-tree for the GROUP BY: 208 ms against 90 ms at
            # 500k points. A whole-table aggregate wants the covering index scanned once,
            # which is what pinning the order preserves.
            inner_from = (
                "CROSS JOIN lines li ON li.id = plot_points.line_id WHERE li.port = ? "
            )
            params.append(port)
        # The aggregate scans plot_points whichever way it is written, because it counts
        # every point of every channel; that is the endpoint, not a class 20 defect. It runs
        # off the loop for exactly that reason (query_plot_channels_safe).
        sql = (
            "SELECT pp.name, pp.sid, pp.value AS last_value, pp.tick_ms AS last_tick, "
            "       l.ts AS last_ts, l.port AS port, "
            "       pp.line_id AS last_line_id, g.count AS count "
            "FROM (SELECT name, MAX(line_id) AS mx, COUNT(*) AS count "
            f"      FROM plot_points {inner_from}GROUP BY name) g "
            "JOIN plot_points pp ON pp.name = g.name AND pp.line_id = g.mx "
            "JOIN lines l ON l.id = pp.line_id "
            "ORDER BY pp.name"
        )
        return [dict(r) for r in c.execute(sql, params).fetchall()]

    async def query_plot_channels_safe(self, port: str | None = None) -> list[dict[str, Any]]:
        """query_plot_channels, off the loop (see _offload).

        The aggregate scans the whole plot_points table.
        """
        return await self._offload(self.query_plot_channels, port=port)

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
        # ROW_NUMBER from the newest so the cap and the buckets both keep recent data.
        windowed = (
            "SELECT pp.line_id, l.ts, pp.tick_ms, pp.value, "
            "       ROW_NUMBER() OVER (ORDER BY pp.line_id DESC) AS rn "
            "FROM plot_points pp JOIN lines l ON l.id = pp.line_id "
            f"WHERE {where}"
        )
        if decimate == 1:
            sql = f"SELECT line_id, ts, tick_ms, value FROM ({windowed}) WHERE rn <= ?"
            rows = c.execute(sql, (*params, limit)).fetchall()
            return [dict(r) for r in reversed(rows)]
        # Rank each bucket's points by value in both directions; rank 1 in either is the
        # bucket's min or max. The rn tie-break makes the choice deterministic when several
        # samples share the extreme value, and collapses to one row when min and max are
        # the same sample.
        sql = (
            "SELECT line_id, ts, tick_ms, value FROM ("
            "  SELECT line_id, ts, tick_ms, value, rn,"
            "         ROW_NUMBER() OVER (PARTITION BY (rn - 1) / ? ORDER BY value, rn) AS lo,"
            "         ROW_NUMBER() OVER (PARTITION BY (rn - 1) / ? ORDER BY value DESC, rn) AS hi"
            f"  FROM ({windowed}) WHERE rn <= ?"
            ") WHERE lo = 1 OR hi = 1 ORDER BY line_id"
        )
        rows = c.execute(sql, (decimate, decimate, *params, limit)).fetchall()
        return [dict(r) for r in rows]

    async def query_plot_series_safe(self, **kwargs: Any) -> list[dict[str, Any]]:
        """query_plot_series, off the loop (see _offload).

        The window-function scan can touch up to 100k rows of the matching set.
        """
        return await self._offload(self.query_plot_series, **kwargs)

    def _export_where(
        self, names: list[str], last_ms: int | None,
        id_from: int | None = None, id_to: int | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> tuple[str, list[Any]]:
        # `conn` is threaded through rather than defaulted to self._conn: iter_plot_export
        # streams on a private connection off the loop, and a sqlite3 connection may not be
        # used from another thread.
        placeholders = ",".join("?" * len(names))
        window, wparams = self._window_terms(
            id_from=id_from, id_to=id_to, last_ms=last_ms, conn=conn,
            id_col="pp.line_id", ts_col="l.ts",
        )
        clauses = [f"pp.name IN ({placeholders})", *window]
        return " AND ".join(clauses), [*names, *wparams]

    def export_sids(
        self, *, names: list[str], last_ms: int | None = None,
        id_from: int | None = None, id_to: int | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> list[Any]:
        """Distinct sids among the export rows (to reject a multi-stream wide export).

        The result is tiny, but the DISTINCT still scans every matching row, so callers
        on the event loop should prefer `export_sids_safe`.
        """
        c = conn if conn is not None else self._conn
        assert c is not None
        if not names:
            return []
        where, params = self._export_where(names, last_ms, id_from, id_to, conn)
        sql = (
            "SELECT DISTINCT pp.sid FROM plot_points pp JOIN lines l ON l.id = pp.line_id "
            f"WHERE {where}"
        )
        return [r["sid"] for r in c.execute(sql, params).fetchall()]

    async def export_sids_safe(self, **kwargs: Any) -> list[Any]:
        """export_sids, off the loop (see _offload). A DISTINCT scan over plot_points."""
        return await self._offload(self.export_sids, **kwargs)

    def count_plot_export(
        self,
        *,
        names: list[str],
        last_ms: int | None = None,
        id_from: int | None = None,
        id_to: int | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """Rows `iter_plot_export` would yield, so the caller can refuse before streaming.

        A StreamingResponse has already sent its headers by the time the row cap bites, so
        truncation cannot be signalled in band and a short CSV is byte-indistinguishable
        from a complete one. Counting first is what lets an over-large selection be a clear
        400 instead.
        """
        if not names:
            return 0
        conn = conn if conn is not None else self._conn
        assert conn is not None
        where, params = self._export_where(names, last_ms, id_from, id_to, conn)
        sql = ("SELECT COUNT(*) FROM plot_points pp JOIN lines l ON l.id = pp.line_id "
               f"WHERE {where}")
        return int(conn.execute(sql, params).fetchone()[0])

    async def count_plot_export_safe(self, **kwargs: Any) -> int:
        """count_plot_export, off the loop (see _offload): it counts the whole selection."""
        return await self._offload(self.count_plot_export, **kwargs)

    def iter_plot_export(
        self,
        *,
        names: list[str],
        last_ms: int | None = None,
        id_from: int | None = None,
        id_to: int | None = None,
        cap: int = 1_000_000,
    ):
        """Yield long-format export rows, ordered by (line_id, name), streamed in chunks.

        Opens its own read connection (WAL allows concurrent readers) and pulls rows with
        fetchmany, so a million-row export never materializes in one list nor blocks the
        event loop - StreamingResponse consumes this generator in a worker thread. The
        connection allows cross-thread use because that pool calls `next()` serially. Falls
        back to the loop connection for an in-memory DB, which cannot be reopened.
        """
        if not names:
            return
        private = self._db_path not in (":memory:", "")
        conn = self._open_export_conn() if private else self._conn
        assert conn is not None
        try:
            where, params = self._export_where(names, last_ms, id_from, id_to, conn)
            sql = (
                "SELECT pp.line_id, l.ts, pp.tick_ms, pp.sid, pp.name, pp.value "
                "FROM plot_points pp JOIN lines l ON l.id = pp.line_id "
                f"WHERE {where} ORDER BY pp.line_id, pp.name LIMIT ?"
            )
            cur = conn.execute(sql, (*params, cap))
            while True:
                batch = cur.fetchmany(_EXPORT_CHUNK)
                if not batch:
                    break
                for r in batch:
                    yield dict(r)
        finally:
            if private:
                conn.close()

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
        return self._delete_lines(
            f"DELETE FROM lines WHERE id IN (SELECT id FROM lines{guard} ORDER BY id LIMIT ?)",
            params,
        )

    def _delete_range_chunk(self, id_from: int, id_to: int, limit: int) -> int:
        return self._delete_lines(
            "DELETE FROM lines WHERE id IN "
            "(SELECT id FROM lines WHERE id >= ? AND id <= ? ORDER BY id LIMIT ?)",
            (id_from, id_to, limit),
        )

    async def delete_range(self, id_from: int, id_to: int) -> int:
        """Delete an explicit id range, in loop-yielding chunks. Returns lines removed.

        This is the deliberate counterpart to retention: retention only ever truncates the
        oldest end of the capture, whereas a purge removes exactly the span asked for, hole
        in the middle and all. Children cascade via the foreign keys, and freed pages are
        handed back where the database was created with incremental auto-vacuum.
        """
        if id_to < id_from:
            return 0
        total = 0
        while True:
            n = self._delete_range_chunk(id_from, id_to, _RETENTION_CHUNK)
            if n == 0:
                break
            total += n
            await asyncio.sleep(0)   # let the writer drain between chunks
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
        the file plateau. The WAL is left out deliberately: SQLite's auto-checkpoint bounds
        it, so it is fixed overhead rather than growth.
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
            await asyncio.sleep(0)   # let the writer drain between chunks
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

    def _delete_expired_chunk(self, cutoff: float, limit: int, floor_id: int | None) -> int:
        """Delete up to `limit` expired lines and commit. `DELETE ... LIMIT` needs a compile

        option the stdlib build lacks, so the bounded delete is expressed as a subselect.
        The FK cascade drops each line's can_frames/plot_points rows. `floor_id` keeps the
        newest sessions out of the delete however old they are (see retention_floor_id).

        `ORDER BY ts`, not `ORDER BY id`: ordering by id made the planner prefer the table
        btree over idx_lines_ts and read every `raw` blob, and the LIMIT only cuts that
        short when rows really are expired. Nothing expired is the steady state of a
        capture inside its retention window, and that case scanned the whole table on the
        event loop every sweep (45 ms at 300k rows, linear from there). On the ts index the
        expired range is simply empty. Both orders delete oldest-first, because the host
        stamps `ts` at receive time on the single writer.
        """
        guard = "" if floor_id is None else " AND id < ?"
        params: tuple[Any, ...] = (
            (cutoff, limit) if floor_id is None else (cutoff, floor_id, limit)
        )
        return self._delete_lines(
            "DELETE FROM lines WHERE id IN "
            f"(SELECT id FROM lines WHERE ts < ?{guard} ORDER BY ts LIMIT ?)",
            params,
        )

    async def _sweep_retention_async(self) -> int:
        """Chunked retention that yields the loop between chunks so ingestion keeps draining.

        A large one-shot DELETE would hold the write lock and stall the writer task; each
        chunk commits and then `await asyncio.sleep(0)` lets the writer run its own batch.
        The session floor is absolute here: age expiry never touches a protected run, so a
        quiet fortnight cannot cost you the only capture you have.
        """
        async with self._sweep_lock:
            return await self._sweep_retention_locked()

    async def _sweep_retention_locked(self) -> int:
        cutoff = time.time() - self._retention_days * 86400
        floor_id = self.retention_floor_id()
        total = 0
        while True:
            n = self._delete_expired_chunk(cutoff, _RETENTION_CHUNK, floor_id)
            total += n
            if n < _RETENTION_CHUNK:
                return total
            await asyncio.sleep(0)

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
            trimmed = await self._sweep_size_async()
            if trimmed:
                # A sys row puts the loss in the capture itself, where anyone reading
                # the log will see it, rather than only in the daemon's stderr.
                await self.add_line(
                    ts=time.time(), port="", dir="-", chan="sys", seq=None,
                    raw=f"storage: trimmed {trimmed} oldest lines "
                        f"to stay under the {self._max_db_bytes} byte cap",
                )
            if tick % _RETENTION_TICKS == 0:
                await self._sweep_retention_async()
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
