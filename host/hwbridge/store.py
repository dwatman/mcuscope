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
"""

_LINE_COLS = ("id", "ts", "port", "dir", "chan", "seq", "raw")


@dataclass
class _WriteReq:
    row: dict[str, Any]
    can: dict[str, Any] | None
    future: asyncio.Future


def _make_regexp():
    """A cached-pattern REGEXP implementation for SQLite (`raw REGEXP ?`)."""
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
                row = self._insert(req.row, req.can)
                if not req.future.done():
                    req.future.set_result(row)
                self._broadcast(row)
            except Exception as exc:  # surface to the awaiting caller
                if not req.future.done():
                    req.future.set_exception(exc)

    def _insert(self, row: dict[str, Any], can: dict[str, Any] | None) -> dict[str, Any]:
        assert self._conn is not None
        cur = self._conn.execute(
            "INSERT INTO lines(ts, port, dir, chan, seq, raw) VALUES(?,?,?,?,?,?)",
            (row["ts"], row["port"], row["dir"], row["chan"], row["seq"], row["raw"]),
        )
        line_id = cur.lastrowid
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
    ) -> dict[str, Any]:
        """Enqueue a line for the writer and return the stored row (with its id)."""
        assert self._queue is not None
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        row = {"ts": ts, "port": port, "dir": dir, "chan": chan, "seq": seq, "raw": raw}
        await self._queue.put(_WriteReq(row=row, can=can, future=fut))
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
    ) -> tuple[list[dict[str, Any]], bool]:
        assert self._conn is not None
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
        rows = self._conn.execute(sql, (*params, limit + 1)).fetchall()
        truncated = len(rows) > limit
        return [dict(r) for r in rows[:limit]], truncated

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
