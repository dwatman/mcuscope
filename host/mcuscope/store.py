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
CREATE INDEX IF NOT EXISTS idx_lines_chan_ts ON lines(chan, ts);

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
"""

_LINE_COLS = ("id", "ts", "port", "dir", "chan", "seq", "raw")

_EXPORT_CHUNK = 10_000     # rows fetched per fetchmany() when streaming an export
_RETENTION_CHUNK = 5_000   # rows deleted per retention DELETE, committed one chunk at a time
_WRITE_QUEUE_MAX = 10_000  # bound the write queue so a stalled writer cannot eat RAM forever
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
        self._retention_days = 7
        self._subscribers: dict[asyncio.Queue, str | None] = {}

    # -- lifecycle --------------------------------------------------------------------

    def set_retention_days(self, days: int) -> None:
        """Live-apply a retention change (SPEC 3.3.1); picked up on the next sweep."""
        self._retention_days = days

    async def start(self, retention_days: int = 7) -> None:
        self._retention_days = retention_days
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
        conn.executescript(SCHEMA)
        conn.commit()
        self._conn = conn
        self._queue = asyncio.Queue(maxsize=_WRITE_QUEUE_MAX)
        self._writer_task = asyncio.create_task(self._writer())
        # The initial sweep runs in the background: a large expired backlog must not
        # hold up daemon startup (the chunked sweep yields the loop between chunks).
        self._initial_sweep_task = asyncio.create_task(self._initial_sweep())
        self._retention_task = asyncio.create_task(self._retention_loop())

    async def _initial_sweep(self) -> None:
        try:
            await self._sweep_retention_async()
        except Exception as exc:
            log.error("startup retention sweep failed: %s", exc)

    async def stop(self) -> None:
        for task_attr in ("_initial_sweep_task", "_retention_task"):
            task = getattr(self, task_attr, None)
            if task:
                task.cancel()
                with _suppress_cancel():
                    await task
        self._retention_task = None
        if self._queue is not None and self._writer_task is not None:
            await self._queue.put(None)  # sentinel: flush and exit
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
            results: list[tuple[_WriteReq, dict[str, Any] | None, Exception | None]] = []
            for item in batch:
                try:
                    row = self._insert(item.row, item.can, item.plot)
                    results.append((item, row, None))
                except Exception as exc:  # one bad insert must not lose the others
                    log.warning("line insert failed: %s", exc)
                    results.append((item, None, exc))
            assert self._conn is not None
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

    def _insert(
        self,
        row: dict[str, Any],
        can: dict[str, Any] | None,
        plot: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Insert one line (+ optional can/plot rows). The caller commits the whole batch.

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
        return {"id": line_id, **row}

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

    async def add_line(
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
    ) -> dict[str, Any]:
        """Enqueue a line for the writer and return the stored row (with its id)."""
        assert self._queue is not None
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        row = {"ts": ts, "port": port, "dir": dir, "chan": chan, "seq": seq, "raw": raw}
        await self._queue.put(_WriteReq(row=row, can=can, plot=plot, future=fut))
        return await fut

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
        limit: int = 100,
        order: str = "desc",
        conn: sqlite3.Connection | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        c = conn if conn is not None else self._conn
        assert c is not None
        limit = max(1, min(int(limit), 1000))
        clauses: list[str] = []
        params: list[Any] = []
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
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], bool]:
        assert self._conn is not None
        limit = max(1, min(int(limit), 1000))
        clauses: list[str] = []
        params: list[Any] = []
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
        limit: int = 10000,
        decimate: int = 1,
        conn: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """History for one channel, chronological (ascending line_id).

        `decimate` keeps every Nth point counting back from the newest, so a long window
        stays cheap; `limit` caps the returned points (newest kept).
        """
        c = conn if conn is not None else self._conn
        assert c is not None
        limit = max(1, min(int(limit), 100000))
        decimate = max(1, int(decimate))
        clauses = ["pp.name = ?"]
        params: list[Any] = [name]
        if since_id is not None:
            clauses.append("pp.line_id > ?")
            params.append(since_id)
        if last_ms is not None:
            clauses.append("l.ts >= ?")
            params.append(time.time() - last_ms / 1000.0)
        where = " AND ".join(clauses)
        # ROW_NUMBER from the newest so decimation and the cap both keep recent data.
        sql = (
            "SELECT line_id, ts, tick_ms, value FROM ("
            "  SELECT pp.line_id, l.ts, pp.tick_ms, pp.value, "
            "         ROW_NUMBER() OVER (ORDER BY pp.line_id DESC) AS rn "
            "  FROM plot_points pp JOIN lines l ON l.id = pp.line_id "
            f"  WHERE {where}"
            ") WHERE (rn - 1) % ? = 0 ORDER BY line_id DESC LIMIT ?"
        )
        rows = c.execute(sql, (*params, decimate, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

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

    def _export_where(self, names: list[str], last_ms: int | None) -> tuple[str, list[Any]]:
        placeholders = ",".join("?" * len(names))
        clauses = [f"pp.name IN ({placeholders})"]
        params: list[Any] = list(names)
        if last_ms is not None:
            clauses.append("l.ts >= ?")
            params.append(time.time() - last_ms / 1000.0)
        return " AND ".join(clauses), params

    def export_sids(
        self, *, names: list[str], last_ms: int | None = None,
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
        where, params = self._export_where(names, last_ms)
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
            where, params = self._export_where(names, last_ms)
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

    def _delete_expired_chunk(self, cutoff: float, limit: int) -> int:
        """Delete up to `limit` expired lines and commit. `DELETE ... LIMIT` needs a compile

        option the stdlib build lacks, so the bounded delete is expressed as a subselect.
        The FK cascade drops each line's can_frames/plot_points rows.
        """
        assert self._conn is not None
        cur = self._conn.execute(
            "DELETE FROM lines WHERE id IN "
            "(SELECT id FROM lines WHERE ts < ? ORDER BY id LIMIT ?)",
            (cutoff, limit),
        )
        self._conn.commit()
        return cur.rowcount

    def sweep_retention(self) -> int:
        """Delete every expired line in bounded, per-chunk-committed passes (synchronous)."""
        cutoff = time.time() - self._retention_days * 86400
        total = 0
        while True:
            n = self._delete_expired_chunk(cutoff, _RETENTION_CHUNK)
            total += n
            if n < _RETENTION_CHUNK:
                return total

    async def _sweep_retention_async(self) -> int:
        """Chunked retention that yields the loop between chunks so ingestion keeps draining.

        A large one-shot DELETE would hold the write lock and stall the writer task; each
        chunk commits and then `await asyncio.sleep(0)` lets the writer run its own batch.
        """
        cutoff = time.time() - self._retention_days * 86400
        total = 0
        while True:
            n = self._delete_expired_chunk(cutoff, _RETENTION_CHUNK)
            total += n
            if n < _RETENTION_CHUNK:
                return total
            await asyncio.sleep(0)

    async def _retention_loop(self) -> None:
        while True:
            await asyncio.sleep(3600)
            try:
                await self._sweep_retention_async()
            except Exception as exc:  # a sweep failure must not kill the daemon
                log.error("retention sweep failed: %s", exc)

    def db_size_bytes(self) -> int:
        try:
            return os.path.getsize(self._db_path)
        except OSError:
            return 0


class _suppress_cancel:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is asyncio.CancelledError
