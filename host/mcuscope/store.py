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
import functools
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
        self._retention_days = 7
        self._subscribers: dict[asyncio.Queue, str | None] = {}

    # -- lifecycle --------------------------------------------------------------------

    async def start(self, retention_days: int = 7) -> None:
        self._retention_days = retention_days
        parent = os.path.dirname(self._db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.create_function("regexp", 2, _make_regexp(), deterministic=True)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        conn.commit()
        self._conn = conn
        self._queue = asyncio.Queue()
        self._writer_task = asyncio.create_task(self._writer())
        self.sweep_retention()
        self._retention_task = asyncio.create_task(self._retention_loop())

    async def stop(self) -> None:
        if self._retention_task:
            self._retention_task.cancel()
            with _suppress_cancel():
                await self._retention_task
        if self._queue is not None and self._writer_task is not None:
            await self._queue.put(None)  # sentinel: flush and exit
            await self._writer_task
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- write path -------------------------------------------------------------------

    async def _writer(self) -> None:
        assert self._queue is not None
        while True:
            req = await self._queue.get()
            if req is None:
                return
            try:
                row = self._insert(req.row, req.can, req.plot)
                if not req.future.done():
                    req.future.set_result(row)
                self._broadcast(row)
            except Exception as exc:  # surface to the awaiting caller
                if not req.future.done():
                    req.future.set_exception(exc)

    def _insert(
        self,
        row: dict[str, Any],
        can: dict[str, Any] | None,
        plot: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        assert self._conn is not None
        cur = self._conn.execute(
            "INSERT INTO lines(ts, port, dir, chan, seq, raw) VALUES(?,?,?,?,?,?)",
            (row["ts"], row["port"], row["dir"], row["chan"], row["seq"], row["raw"]),
        )
        line_id = cur.lastrowid
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
        self._conn.commit()
        return {"id": line_id, **row}

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
    ) -> list[dict[str, Any]]:
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
        rows = self._conn.execute(sql, (*params, limit)).fetchall()
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
        return out

    # -- plot reads (SPEC 9.2) --------------------------------------------------------

    def query_plot_channels(self) -> list[dict[str, Any]]:
        """One row per distinct channel name: sid, point count, and its latest sample.

        Units/scale/type are not stored here; the server merges those in from its live
        `!pd` definition cache. Channels are keyed by name alone (SPEC 2.5).
        """
        assert self._conn is not None
        sql = (
            "SELECT pp.name, pp.sid, pp.value AS last_value, pp.tick_ms AS last_tick, "
            "       l.ts AS last_ts, pp.line_id AS last_line_id, "
            "       (SELECT COUNT(*) FROM plot_points c WHERE c.name = pp.name) AS count "
            "FROM plot_points pp JOIN lines l ON l.id = pp.line_id "
            "WHERE pp.line_id = (SELECT MAX(m.line_id) FROM plot_points m WHERE m.name = pp.name) "
            "ORDER BY pp.name"
        )
        return [dict(r) for r in self._conn.execute(sql).fetchall()]

    def query_plot_series(
        self,
        *,
        name: str,
        last_ms: int | None = None,
        since_id: int | None = None,
        limit: int = 10000,
        decimate: int = 1,
    ) -> list[dict[str, Any]]:
        """History for one channel, chronological (ascending line_id).

        `decimate` keeps every Nth point counting back from the newest, so a long window
        stays cheap; `limit` caps the returned points (newest kept).
        """
        assert self._conn is not None
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
        rows = self._conn.execute(sql, (*params, decimate, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def query_plot_export(
        self,
        *,
        names: list[str],
        last_ms: int | None = None,
        cap: int = 1_000_000,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Long-format rows for the requested channels, ordered by (line_id, name).

        Returns the rows plus a `truncated` flag; the server pivots these into wide CSV
        when asked. `cap` bounds memory on a huge window.
        """
        assert self._conn is not None
        if not names:
            return [], False
        placeholders = ",".join("?" * len(names))
        clauses = [f"pp.name IN ({placeholders})"]
        params: list[Any] = list(names)
        if last_ms is not None:
            clauses.append("l.ts >= ?")
            params.append(time.time() - last_ms / 1000.0)
        where = " AND ".join(clauses)
        sql = (
            "SELECT pp.line_id, l.ts, pp.tick_ms, pp.sid, pp.name, pp.value "
            "FROM plot_points pp JOIN lines l ON l.id = pp.line_id "
            f"WHERE {where} ORDER BY pp.line_id, pp.name LIMIT ?"
        )
        rows = self._conn.execute(sql, (*params, cap + 1)).fetchall()
        truncated = len(rows) > cap
        return [dict(r) for r in rows[:cap]], truncated

    # -- retention --------------------------------------------------------------------

    def sweep_retention(self) -> int:
        assert self._conn is not None
        cutoff = time.time() - self._retention_days * 86400
        cur = self._conn.execute("DELETE FROM lines WHERE ts < ?", (cutoff,))
        self._conn.commit()
        return cur.rowcount

    async def _retention_loop(self) -> None:
        while True:
            await asyncio.sleep(3600)
            try:
                self.sweep_retention()
            except Exception:
                pass  # a sweep failure must not kill the daemon

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
