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
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

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

-- A session is a named span of the one capture timeline, stored as an id range rather
-- than a column on every line: nothing is written per row, existing captures need no
-- migration, and scoping a query to a session rides the primary key for free. The cost
-- is that sessions cannot overlap or nest - starting one closes the previous.
CREATE TABLE IF NOT EXISTS sessions(
  id         INTEGER PRIMARY KEY,
  name       TEXT    NOT NULL,
  note       TEXT    NOT NULL DEFAULT '',
  started_ts REAL    NOT NULL,
  ended_ts   REAL,                       -- NULL while the session is running
  start_id   INTEGER NOT NULL,           -- first lines.id in the session (inclusive)
  end_id     INTEGER,                    -- last lines.id (inclusive); NULL while running
  auto       INTEGER NOT NULL DEFAULT 0  -- opened by the daemon, not named by anyone
);
CREATE INDEX IF NOT EXISTS idx_sessions_name ON sessions(name, id);
"""

# Columns added after the first release, applied to an existing capture with ALTER TABLE.
# `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so a schema
# change needs this list as well as the definition above.
_MIGRATIONS = (
    ("sessions", "auto", "ALTER TABLE sessions ADD COLUMN auto INTEGER NOT NULL DEFAULT 0"),
)

_LINE_COLS = ("id", "ts", "port", "dir", "chan", "seq", "raw")

_EXPORT_CHUNK = 10_000     # rows fetched per fetchmany() when streaming an export
_RETENTION_CHUNK = 5_000   # rows deleted per retention DELETE, committed one chunk at a time
_WRITE_QUEUE_MAX = 10_000  # bound the write queue so a stalled writer cannot eat RAM forever
_SIZE_CHECK_S = 60         # seconds between size-cap checks (see _retention_loop)
_RETENTION_TICKS = 60      # size-cap ticks per age sweep, i.e. hourly
MAX_SUBSCRIBERS = 256      # cap fan-out queues so connect/disconnect churn cannot eat RAM

log = logging.getLogger(__name__)


class StoreError(RuntimeError):
    """A write could not be persisted (insert or commit failure)."""


@dataclass
class _WriteReq:
    row: dict[str, Any]
    can: dict[str, Any] | None
    plot: list[dict[str, Any]] | None
    future: asyncio.Future


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Add columns a pre-existing capture predates. Idempotent, and safe on a new file."""
    for table, column, ddl in _MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if cols and column not in cols:
            conn.execute(ddl)


def _make_regexp():
    """A cached-pattern REGEXP implementation for SQLite (`raw REGEXP ?`).

    The stdlib `re` engine cannot be interrupted mid-backtrack, so a catastrophic pattern is
    contained by running match queries off the event loop (query_lines_safe / the /wait
    executor) rather than by a per-row timeout: a slow pattern ties up a worker thread but
    never stalls ingestion, the loop, or other clients. The `MAX_MATCH_LEN` cap in server.py
    bounds pattern size as a first gate.
    """
    cache: dict[str, re.Pattern[str]] = {}

    def regexp(pattern: str, value: str | None) -> bool:
        if value is None:
            return False
        pat = cache.get(pattern)
        if pat is None:
            pat = re.compile(pattern)
            cache[pattern] = pat
        return pat.search(value) is not None

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
        self._subscribers: dict[asyncio.Queue, str | None] = {}
        # Next `lines.id` to hand out. The daemon owns this sequence (see _insert_batch);
        # it is seeded from the file at start() and resynced if a batch ever fails.
        self._next_id = 1

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
        conn.execute("PRAGMA journal_mode=WAL")
        # NORMAL is crash-safe under WAL (a crash can lose the last commit, never corrupt the
        # DB) and skips the per-commit fsync that FULL forces - the right tradeoff for a
        # high-rate capture tool that batches its commits.
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        # Incremental auto-vacuum lets a size-capped capture hand freed pages back to the
        # filesystem after a trim, instead of the file sitting at its high-water mark. It
        # can only be chosen on a database with no tables yet, so this applies to captures
        # this daemon creates; an older one keeps its setting and simply plateaus.
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        conn.executescript(SCHEMA)
        _apply_migrations(conn)
        conn.commit()
        self._conn = conn
        self._next_id = self.max_id() + 1
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
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- write path -------------------------------------------------------------------

    async def _writer(self) -> None:
        """Drain the queue in batches: one fsync-bounded commit covers every line that was

        already waiting, instead of a commit (and fsync) per line. Each caller still gets
        its own inserted row id back via its future, and each broadcast happens after the
        single commit so subscribers never see a row that a crash could roll back.
        """
        assert self._queue is not None
        while True:
            req = await self._queue.get()
            if req is None:
                return
            batch = [req]
            stop = False
            while True:  # absorb everything already queued into this commit
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
                results = self._insert_individually(batch)
            try:
                self._conn.commit()  # single durability point for the whole batch
            except Exception as exc:
                # A commit failure (disk full, I/O error) must not kill the writer:
                # fail this batch's callers, roll back, and keep draining the queue.
                log.error("batch commit failed: %s", exc)
                with contextlib.suppress(Exception):
                    self._conn.rollback()
                for item, _row, item_exc in results:
                    if not item.future.done():
                        item.future.set_exception(
                            item_exc if item_exc is not None
                            else StoreError(f"commit failed: {exc}")
                        )
                if stop:
                    return
                continue
            for item, row, exc in results:
                if exc is not None:
                    if not item.future.done():
                        item.future.set_exception(exc)
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
        self._next_id = self.max_id() + 1
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
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.pop(q, None)

    def _broadcast(self, row: dict[str, Any]) -> None:
        if not self._subscribers:   # the common case: nothing attached, no list to build
            return
        for q, port_filter in list(self._subscribers.items()):
            if port_filter is not None and row["port"] != port_filter:
                continue
            if q.full():  # slow consumer: drop the oldest, never block the writer
                try:
                    q.get_nowait()
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

    def resolve_session(self, ref: str) -> dict[str, Any] | None:
        """Look up a session by numeric id or by name (the newest match wins)."""
        assert self._conn is not None
        if ref.isdigit():
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

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        """Recent sessions, newest first, each with the number of lines still stored.

        The count is computed rather than remembered because retention (and the size cap)
        remove a session's lines out from under it; a finished run that has aged out reads
        as 0 lines instead of claiming rows that are gone.
        """
        assert self._conn is not None
        limit = max(1, min(int(limit), 1000))
        sql = (
            "SELECT s.id, s.name, s.note, s.started_ts, s.ended_ts, s.start_id, s.end_id, "
            "  s.auto, "
            "  (SELECT COUNT(*) FROM lines l WHERE l.id >= s.start_id "
            "     AND (s.end_id IS NULL OR l.id <= s.end_id)) AS lines "
            "FROM sessions s ORDER BY s.id DESC LIMIT ?"
        )
        return [self._session_dict(r) for r in self._conn.execute(sql, (limit,)).fetchall()]

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

        Marker and sys rows are the daemon talking to itself (its own start/stop rows and
        the session's own boundaries), so they do not make a run.
        """
        assert self._conn is not None
        end_id = session["end_id"] if session["end_id"] is not None else self.max_id()
        row = self._conn.execute(
            "SELECT 1 FROM lines WHERE id >= ? AND id <= ? "
            "AND chan IN ('debug','cmd','resp','event') LIMIT 1",
            (session["start_id"], end_id),
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
        live capture ATTACHed read-only, so it never touches the loop connection, and it
        is a plain `INSERT ... SELECT` per table rather than a row-at-a-time transfer.

        The result is a normal MCUscope capture file, not a bespoke archive format: point
        `mcuscoped --config` at it (or open it with any SQLite tool) and every query works
        unchanged. The session row is carried across with its ids intact, so `--session`
        still scopes correctly inside the copy.
        """
        hi = id_to if id_to is not None else self.max_id()
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
                conn.execute("DETACH DATABASE src")
            return max(0, copied)
        finally:
            conn.close()

    # -- reads ------------------------------------------------------------------------

    def max_id(self) -> int:
        assert self._conn is not None
        row = self._conn.execute("SELECT MAX(id) AS m FROM lines").fetchone()
        return row["m"] or 0

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
        limit = max(1, min(int(limit), 1000))
        clauses: list[str] = []
        params: list[Any] = []
        if id_from is not None:
            clauses.append("id >= ?")
            params.append(id_from)
        if id_to is not None:
            clauses.append("id <= ?")
            params.append(id_to)
        if port:
            clauses.append("port = ?")
            params.append(port)
        if chans:
            placeholders = ",".join("?" * len(chans))
            clauses.append(f"chan IN ({placeholders})")
            params.extend(chans)
        if match:
            clauses.append("raw REGEXP ?")
            params.append(match)
        if since_id is not None:
            clauses.append("id > ?")
            params.append(since_id)
        if since_ts is not None:
            clauses.append("ts > ?")
            params.append(since_ts)
        if last_ms is not None:
            clauses.append("ts >= ?")
            params.append(time.time() - last_ms / 1000.0)
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
        chan: str | None = None,
        id_from: int | None = None,
        id_to: int | None = None,
        last_ms: float | None = None,
    ) -> int:
        """Count stored lines in a window. No `match` here: counting is match-free by design.

        Used to report how many lines an assertion looked at, and what a purge is about to
        remove. Deliberately excludes a regex filter so it stays a bounded index count
        rather than a full-table regex scan.
        """
        assert self._conn is not None
        clauses: list[str] = []
        params: list[Any] = []
        if id_from is not None:
            clauses.append("id >= ?")
            params.append(id_from)
        if id_to is not None:
            clauses.append("id <= ?")
            params.append(id_to)
        if port:
            clauses.append("port = ?")
            params.append(port)
        if chan:
            clauses.append("chan = ?")
            params.append(chan)
        if last_ms is not None:
            clauses.append("ts >= ?")
            params.append(time.time() - last_ms / 1000.0)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        row = self._conn.execute(f"SELECT COUNT(*) AS n FROM lines {where}", params).fetchone()
        return int(row["n"])

    def last_id_before_ts(self, ts: float) -> int | None:
        """Highest line id older than `ts`, so a time-based purge becomes an id range."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT MAX(id) AS m FROM lines WHERE ts < ?", (ts,)
        ).fetchone()
        return row["m"]

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
        try:
            return self.query_lines(conn=conn, **kwargs)
        finally:
            conn.close()

    async def query_lines_safe(self, **kwargs: Any) -> tuple[list[dict[str, Any]], bool]:
        """query_lines, but run a match-bearing query off the event loop.

        A user `match` regex cannot be time-bounded with stdlib `re`, so match queries execute
        on the default thread-pool executor against a private read connection - a slow pattern
        ties up a worker but ingestion and other clients keep running regardless. Match-free
        queries are cheap and bounded (limit <= 1000), so they run inline on the loop. Falls
        back to inline for an in-memory DB, which cannot be reopened from another thread.
        """
        offloadable = bool(kwargs.get("match")) and self._db_path not in (":memory:", "")
        if not offloadable:
            return self.query_lines(**kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(self._query_lines_threadsafe, **kwargs)
        )

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
    ) -> tuple[list[dict[str, Any]], bool]:
        assert self._conn is not None
        limit = max(1, min(int(limit), 1000))
        clauses: list[str] = []
        params: list[Any] = []
        if id_from is not None:
            clauses.append("cf.line_id >= ?")
            params.append(id_from)
        if id_to is not None:
            clauses.append("cf.line_id <= ?")
            params.append(id_to)
        if port:
            clauses.append("l.port = ?")
            params.append(port)
        if can_id is not None:
            clauses.append("cf.can_id = ?")
            params.append(can_id)
        if since_id is not None:
            clauses.append("cf.line_id > ?")
            params.append(since_id)
        if last_ms is not None:
            clauses.append("l.ts >= ?")
            params.append(time.time() - last_ms / 1000.0)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT cf.line_id, l.ts, cf.tick_ms, cf.can_id, cf.ext, cf.rtr, cf.dlc, cf.data "
            "FROM can_frames cf JOIN lines l ON l.id = cf.line_id "
            f"{where} ORDER BY cf.line_id DESC LIMIT ?"
        )
        rows = self._conn.execute(sql, (*params, limit + 1)).fetchall()
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
        self, conn: sqlite3.Connection | None = None
    ) -> list[dict[str, Any]]:
        """One row per distinct channel name: sid, point count, and its latest sample.

        Units/scale/type are not stored here; the server merges those in from its live
        `!pd` definition cache. Channels are keyed by name alone (SPEC 2.5). A single
        GROUP BY pass computes MAX(line_id) + COUNT(*) per name, then joins back to fetch
        the latest sample, instead of a correlated subquery scan per row.
        """
        c = conn if conn is not None else self._conn
        assert c is not None
        sql = (
            "SELECT pp.name, pp.sid, pp.value AS last_value, pp.tick_ms AS last_tick, "
            "       l.ts AS last_ts, pp.line_id AS last_line_id, g.count AS count "
            "FROM (SELECT name, MAX(line_id) AS mx, COUNT(*) AS count "
            "      FROM plot_points GROUP BY name) g "
            "JOIN plot_points pp ON pp.name = g.name AND pp.line_id = g.mx "
            "JOIN lines l ON l.id = pp.line_id "
            "ORDER BY pp.name"
        )
        return [dict(r) for r in c.execute(sql).fetchall()]

    def _query_plot_channels_threadsafe(self) -> list[dict[str, Any]]:
        conn = self._open_read_conn()
        try:
            return self.query_plot_channels(conn=conn)
        finally:
            conn.close()

    async def query_plot_channels_safe(self) -> list[dict[str, Any]]:
        """query_plot_channels, run off the event loop against a private read connection.

        The aggregate scans the whole plot_points table, so it is offloaded to a worker
        thread (WAL lets readers run concurrently). Falls back inline for an in-memory DB,
        which cannot be reopened from another thread.
        """
        if self._db_path in (":memory:", ""):
            return self.query_plot_channels()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._query_plot_channels_threadsafe)

    def query_plot_series(
        self,
        *,
        name: str,
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
        limit = max(1, min(int(limit), 100000))
        decimate = max(1, int(decimate))
        clauses = ["pp.name = ?"]
        params: list[Any] = [name]
        if id_from is not None:
            clauses.append("pp.line_id >= ?")
            params.append(id_from)
        if id_to is not None:
            clauses.append("pp.line_id <= ?")
            params.append(id_to)
        if since_id is not None:
            clauses.append("pp.line_id > ?")
            params.append(since_id)
        if last_ms is not None:
            clauses.append("l.ts >= ?")
            params.append(time.time() - last_ms / 1000.0)
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

    def _query_plot_series_threadsafe(self, **kwargs: Any) -> list[dict[str, Any]]:
        conn = self._open_read_conn()
        try:
            return self.query_plot_series(conn=conn, **kwargs)
        finally:
            conn.close()

    async def query_plot_series_safe(self, **kwargs: Any) -> list[dict[str, Any]]:
        """query_plot_series, run off the event loop against a private read connection.

        The window-function scan can touch up to 100k rows of the matching set, so it
        must not stall ingestion. Falls back inline for an in-memory DB.
        """
        if self._db_path in (":memory:", ""):
            return self.query_plot_series(**kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(self._query_plot_series_threadsafe, **kwargs)
        )

    def _export_where(
        self, names: list[str], last_ms: int | None,
        id_from: int | None = None, id_to: int | None = None,
    ) -> tuple[str, list[Any]]:
        placeholders = ",".join("?" * len(names))
        clauses = [f"pp.name IN ({placeholders})"]
        params: list[Any] = list(names)
        if id_from is not None:
            clauses.append("pp.line_id >= ?")
            params.append(id_from)
        if id_to is not None:
            clauses.append("pp.line_id <= ?")
            params.append(id_to)
        if last_ms is not None:
            clauses.append("l.ts >= ?")
            params.append(time.time() - last_ms / 1000.0)
        return " AND ".join(clauses), params

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
        where, params = self._export_where(names, last_ms, id_from, id_to)
        sql = (
            "SELECT DISTINCT pp.sid FROM plot_points pp JOIN lines l ON l.id = pp.line_id "
            f"WHERE {where}"
        )
        return [r["sid"] for r in c.execute(sql, params).fetchall()]

    def _export_sids_threadsafe(self, **kwargs: Any) -> list[Any]:
        conn = self._open_read_conn()
        try:
            return self.export_sids(conn=conn, **kwargs)
        finally:
            conn.close()

    async def export_sids_safe(self, **kwargs: Any) -> list[Any]:
        """export_sids, run off the event loop. Falls back inline for an in-memory DB."""
        if self._db_path in (":memory:", ""):
            return self.export_sids(**kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(self._export_sids_threadsafe, **kwargs)
        )

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
            where, params = self._export_where(names, last_ms, id_from, id_to)
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
        assert self._conn is not None
        guard = "" if floor_id is None else " WHERE id < ?"
        params: tuple[Any, ...] = (limit,) if floor_id is None else (floor_id, limit)
        cur = self._conn.execute(
            f"DELETE FROM lines WHERE id IN (SELECT id FROM lines{guard} ORDER BY id LIMIT ?)",
            params,
        )
        self._conn.commit()
        return cur.rowcount

    def _delete_range_chunk(self, id_from: int, id_to: int, limit: int) -> int:
        assert self._conn is not None
        cur = self._conn.execute(
            "DELETE FROM lines WHERE id IN "
            "(SELECT id FROM lines WHERE id >= ? AND id <= ? ORDER BY id LIMIT ?)",
            (id_from, id_to, limit),
        )
        self._conn.commit()
        return cur.rowcount

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
                self._conn.execute("PRAGMA incremental_vacuum")
                self._conn.commit()
        return total

    def _estimated_rows(self) -> int:
        """Row count estimated from the id range, without a COUNT(*) scan.

        Ids are dense (the writer allocates them consecutively and only the oldest are
        ever deleted), so MIN/MAX - both O(1) on the primary key - are a good estimate,
        and this runs while the daemon is already over its size cap.
        """
        assert self._conn is not None
        row = self._conn.execute("SELECT MIN(id) AS lo, MAX(id) AS hi FROM lines").fetchone()
        if row["lo"] is None:
            return 0
        return row["hi"] - row["lo"] + 1

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
                self._conn.execute("PRAGMA incremental_vacuum")
                self._conn.commit()
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
        """
        assert self._conn is not None
        guard = "" if floor_id is None else " AND id < ?"
        params: tuple[Any, ...] = (
            (cutoff, limit) if floor_id is None else (cutoff, floor_id, limit)
        )
        cur = self._conn.execute(
            "DELETE FROM lines WHERE id IN "
            f"(SELECT id FROM lines WHERE ts < ?{guard} ORDER BY id LIMIT ?)",
            params,
        )
        self._conn.commit()
        return cur.rowcount

    async def _sweep_retention_async(self) -> int:
        """Chunked retention that yields the loop between chunks so ingestion keeps draining.

        A large one-shot DELETE would hold the write lock and stall the writer task; each
        chunk commits and then `await asyncio.sleep(0)` lets the writer run its own batch.
        The session floor is absolute here: age expiry never touches a protected run, so a
        quiet fortnight cannot cost you the only capture you have.
        """
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
                if ticks % _RETENTION_TICKS == 0:
                    await self._sweep_retention_async()
            except Exception as exc:  # a sweep failure must not kill the daemon
                log.error("retention sweep failed: %s", exc)

    def db_size_bytes(self) -> int:
        """Bytes the capture occupies on disk: the database plus its write-ahead log.

        Under WAL the `-wal` sidecar holds committed data that has not been checkpointed
        back yet, and it can be a large share of the total during a fast capture. Counting
        only the main file would under-report what the capture is actually using, which
        matters both for the status display and for the size cap.
        """
        total = 0
        for path in (self._db_path, self._db_path + "-wal"):
            try:
                total += os.path.getsize(path)
            except OSError:
                pass   # main file not created yet, or no WAL right now
        return total
