"""FastAPI REST + WebSocket app (SPEC 3.4) and app assembly (lifespan wiring).

`create_app(config)` builds the app with a lifespan that starts the Store, attaches
autoconnect ports, and tears everything down on shutdown. Endpoints reach shared state
through `request.app.state` (store, ports, config, start_time).
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from collections.abc import Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from serial.tools import list_ports
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from . import protocol as p
from .config import Config, resolve_db_path
from .serial_link import PortError, PortManager
from .store import Store, StoreError

log = logging.getLogger("mcuscope.server")

# Longest user-supplied regex accepted on /lines and /wait. A short bound rejects the most
# obvious oversized patterns; it is NOT a full ReDoS defence (a short catastrophic-backtracking
# pattern can still burn CPU on the loop thread - see SPEC 3.4 hardening notes).
MAX_MATCH_LEN = 200

# Bounds for client-supplied command/wait timeouts. A huge timeout would hold the
# port's command lock (or a fan-out subscriber queue) hostage for its whole duration.
MAX_TIMEOUT_MS = 300_000

# Same alias grammar as config.ALIAS_RE (see there for the rationale).
_ALIAS_RE = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$"

# -- request bodies -------------------------------------------------------------------


class PortAttach(BaseModel):
    alias: str = Field(pattern=_ALIAS_RE)
    device: str | None = None
    serial_number: str | None = None
    baud: int = Field(default=115200, gt=0)


class SendBody(BaseModel):
    port: str | None = None
    line: str


class CmdBody(BaseModel):
    port: str | None = None
    cmd: str
    timeout_ms: int = Field(default=1000, gt=0, le=MAX_TIMEOUT_MS)


class WaitBody(BaseModel):
    port: str | None = None
    match: str
    timeout_ms: int = Field(default=2000, gt=0, le=MAX_TIMEOUT_MS)
    send: str | None = None
    send_mode: str = "cmd"
    chan: str | None = None
    since: str = "now"  # only "now" is defined (SPEC 3.4); anything else is rejected


class MarkerBody(BaseModel):
    port: str | None = None
    text: str


# -- app assembly ---------------------------------------------------------------------


def create_app(config: Config) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        store = Store(resolve_db_path(config))
        await store.start(config.storage.retention_days)
        ports = PortManager(store, loop)
        app.state.store = store
        app.state.ports = ports
        app.state.config = config
        app.state.start_time = time.time()
        await store.add_line(
            ts=time.time(), port="", dir="-", chan="sys", seq=None, raw="daemon start"
        )
        for pc in config.ports:
            if pc.autoconnect:
                try:
                    await ports.attach(pc.alias, pc.device, pc.baud, pc.serial_number)
                except PortError as exc:
                    # One bad config entry must not abort startup: log it, record a
                    # sys row, and keep serving with the remaining ports.
                    log.error("autoconnect %s failed: %s", pc.alias, exc)
                    await store.add_line(
                        ts=time.time(), port="", dir="-", chan="sys", seq=None,
                        raw=f"autoconnect {pc.alias} failed: {exc}",
                    )
        try:
            yield
        finally:
            await ports.stop_all()
            with _ignore():
                await store.add_line(
                    ts=time.time(), port="", dir="-", chan="sys", seq=None, raw="daemon stop"
                )
            await store.stop()

    app = FastAPI(title="mcuscoped", version=__version__, lifespan=lifespan)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": str(exc.errors())})

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception):
        # SPEC 3.4: every error is a {"error": msg} JSON envelope, never a bare 500 page.
        # Log the traceback server-side; the client sees only the message.
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"error": str(exc)})

    _register_routes(app)
    _mount_webui(app)
    app.add_middleware(_SameOriginGuard)
    app.add_middleware(_TokenGuard, token=config.server.token)
    return app


def _origin_matches_host(origin: bytes, host: bytes) -> bool:
    """True if a browser Origin header names the same host:port as the request's Host."""
    try:
        o = origin.decode("latin-1").strip().lower()
        h = host.decode("latin-1").strip().lower()
    except UnicodeDecodeError:
        return False
    if not o or o == "null":
        return False
    netloc = o.split("://", 1)[1] if "://" in o else o
    return netloc == h


class _SameOriginGuard:
    """Refuse cross-origin browser requests (CSRF, cross-site WebSocket, DNS rebinding).

    The REST/WS API is unauthenticated by design for the localhost workflow (SPEC 3.4), so a
    web page the operator merely visits could otherwise drive the hardware or read the capture
    stream via the browser. Browsers attach an `Origin` header to such cross-site fetches and
    to every WebSocket handshake; we reject any request whose Origin does not match its own
    Host. Non-browser clients (the `mcu` CLI, curl) send no Origin and are unaffected, and
    same-origin UI use - loopback or the LAN address the page was served from - always passes.
    A DNS-rebinding page keeps its original Origin, which no longer matches the rebound Host.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers") or [])
            origin = headers.get(b"origin")
            if origin is not None and not _origin_matches_host(origin, headers.get(b"host", b"")):
                await self._deny(scope, send)
                return
        await self.app(scope, receive, send)

    async def _deny(self, scope, send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})  # policy violation
            return
        body = b'{"error":"cross-origin request refused"}'
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


_LOOPBACK_CLIENTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


class _TokenGuard:
    """Require the configured access token from non-loopback clients (SPEC 3.4).

    Loopback clients are always exempt: the local machine is the existing trust
    boundary, and the `mcu` CLI keeps working with zero friction. Network clients
    must present the token as `Authorization: Bearer <token>`, an `X-Auth-Token`
    header, or (WebSocket only, where browsers cannot set headers) a `?token=`
    query parameter. When no token is configured the guard is inert; daemon startup
    warns loudly about non-loopback binds in that case.

    The static UI (`/` and `/ui/...`) is served without the token so a browser can
    load the page and then prompt for the token when its API calls get 401.
    """

    def __init__(self, app, token: str | None = None) -> None:
        self.app = app
        self.token = token
        # Compare as bytes: str compare_digest raises TypeError on non-ASCII input,
        # which a hostile header could trigger on every request.
        self._token_bytes = token.encode("utf-8") if token is not None else None

    def _provided_token(self, scope) -> str | None:
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization")
        if auth is not None:
            try:
                text = auth.decode("latin-1").strip()
            except UnicodeDecodeError:
                return None
            if text.lower().startswith("bearer "):
                return text[7:].strip()
            return None
        xtok = headers.get(b"x-auth-token")
        if xtok is not None:
            try:
                return xtok.decode("latin-1").strip()
            except UnicodeDecodeError:
                return None
        if scope["type"] == "websocket":
            qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            vals = qs.get("token")
            if vals:
                return vals[0]
        return None

    async def __call__(self, scope, receive, send) -> None:
        if self.token is not None and scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            client_host = client[0] if client else ""
            path = scope.get("path", "")
            static_ok = scope["type"] == "http" and (path == "/" or path.startswith("/ui"))
            if client_host not in _LOOPBACK_CLIENTS and not static_ok:
                provided = self._provided_token(scope)
                if provided is None or not hmac.compare_digest(
                    provided.encode("utf-8"), self._token_bytes
                ):
                    await self._deny(scope, send)
                    return
        await self.app(scope, receive, send)

    async def _deny(self, scope, send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})  # policy violation
            return
        body = b'{"error":"missing or invalid access token"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _NoCacheStatic(StaticFiles):
    """StaticFiles that asks browsers to always revalidate.

    The UI is small and local, and its files change between daemon versions. Without
    this, browsers apply heuristic caching and serve a stale index.html/app.js/style.css
    after an update. `no-cache` forces a conditional request; unchanged files still get a
    cheap 304 via the ETag StaticFiles already sets.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _mount_webui(app: FastAPI) -> None:
    """Serve the static web UI (SPEC 9.1) at /ui and redirect / to it."""
    webui_dir = Path(__file__).parent / "webui"

    @app.get("/", include_in_schema=False)
    async def _root() -> RedirectResponse:
        return RedirectResponse(url="/ui/")

    if webui_dir.is_dir():
        app.mount("/ui", _NoCacheStatic(directory=webui_dir, html=True), name="webui")


def _store(request: Request) -> Store:
    return request.app.state.store


def _ports(request: Request) -> PortManager:
    return request.app.state.ports


def _bad_request(msg: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": msg})


# -- routes ---------------------------------------------------------------------------


def _register_routes(app: FastAPI) -> None:  # noqa: C901 - one function per endpoint
    @app.get("/status")
    async def status(request: Request) -> dict[str, Any]:
        store = _store(request)
        ports = _ports(request)
        cfg: Config = request.app.state.config
        return {
            "version": __version__,
            "uptime_s": time.time() - request.app.state.start_time,
            "db_path": resolve_db_path(cfg),
            "db_size_bytes": store.db_size_bytes(),
            "ports": [pt.status() for pt in ports.list()],
        }

    @app.get("/ports")
    async def get_ports(request: Request) -> dict[str, Any]:
        return {"ports": [pt.status() for pt in _ports(request).list()]}

    @app.post("/ports")
    async def attach_port(request: Request, body: PortAttach):
        if not body.device and not body.serial_number:
            return _bad_request("attach requires device or serial_number")
        try:
            pt = await _ports(request).attach(
                body.alias, body.device, body.baud, body.serial_number
            )
        except PortError as exc:  # rejected device scheme, port cap, etc.
            return _bad_request(str(exc))
        return {"port": pt.status()}

    @app.delete("/ports/{alias}")
    async def detach_port(request: Request, alias: str):
        ok = await _ports(request).detach(alias)
        if not ok:
            return _bad_request(f"no such port: {alias}")
        return {"ok": True}

    @app.get("/devices")
    async def devices() -> dict[str, Any]:
        by_id = _by_id_map()
        out = []
        for info in list_ports.comports():
            vid_pid = None
            if info.vid is not None and info.pid is not None:
                vid_pid = f"{info.vid:04X}:{info.pid:04X}"
            out.append(
                {
                    "device": info.device,
                    "by_id": by_id.get(os.path.realpath(info.device)),
                    "description": info.description or "",
                    "vid_pid": vid_pid,
                    "serial_number": info.serial_number,
                }
            )
        return {"devices": out}

    @app.post("/send")
    async def send(request: Request, body: SendBody):
        try:
            port = _ports(request).resolve(body.port)
        except PortError as exc:
            return _bad_request(str(exc))
        try:
            await port.send_raw(body.line)
        except PortError as exc:
            return _bad_request(str(exc))
        return {"ok": True}

    @app.post("/cmd")
    async def cmd(request: Request, body: CmdBody):
        try:
            port = _ports(request).resolve(body.port)
        except PortError as exc:
            return _bad_request(str(exc))
        try:
            return await port.send_command(body.cmd, body.timeout_ms)
        except PortError as exc:
            return _bad_request(str(exc))

    @app.get("/lines")
    async def lines(
        request: Request,
        port: str | None = None,
        chan: list[str] | None = Query(default=None),  # noqa: B008 - FastAPI query param
        match: str | None = None,
        since_id: int | None = None,
        since_ts: float | None = None,
        last_ms: int | None = None,
        limit: int = 100,
        order: str = "desc",
    ) -> dict[str, Any]:
        if match is not None and len(match) > MAX_MATCH_LEN:
            return _bad_request(f"match regex too long (max {MAX_MATCH_LEN} chars)")
        rows, truncated = await _store(request).query_lines_safe(
            port=port,
            chans=chan,
            match=match,
            since_id=since_id,
            since_ts=since_ts,
            last_ms=last_ms,
            limit=limit,
            order=order,
        )
        return {"lines": rows, "truncated": truncated}

    @app.get("/can/frames")
    async def can_frames(
        request: Request,
        port: str | None = None,
        id: str | None = None,
        last_ms: int | None = None,
        since_id: int | None = None,
        limit: int = 100,
    ):
        can_id = None
        if id is not None:
            try:
                can_id = p.parse_hex_int(id)
            except p.ProtocolError:
                return _bad_request(f"bad can id: {id}")
            if can_id > p.CAN_ID_MAX_EXT:
                return _bad_request(f"can id out of range: {id}")
        rows, truncated = _store(request).query_can_frames(
            port=port, can_id=can_id, last_ms=last_ms, since_id=since_id, limit=limit
        )
        return {"frames": rows, "truncated": truncated}

    @app.get("/plot/channels")
    async def plot_channels(request: Request) -> dict[str, Any]:
        store = _store(request)
        meta = _ports(request).plot_channel_meta()
        out = []
        for ch in await store.query_plot_channels_safe():
            m = meta.get(ch["name"], {})
            out.append(
                {
                    "name": ch["name"],
                    "sid": ch["sid"],
                    "type": m.get("type"),
                    "unit": m.get("unit"),
                    "scale": m.get("scale"),
                    "kind": m.get("kind", "analog"),
                    "labels": m.get("labels"),
                    "group": m.get("group"),
                    "bit": m.get("bit"),
                    "last_value": ch["last_value"],
                    "last_tick": ch["last_tick"],
                    "last_ts": ch["last_ts"],
                    "count": ch["count"],
                }
            )
        return {"channels": out}

    @app.get("/plot/series")
    async def plot_series(
        request: Request,
        name: str,
        last_ms: int | None = None,
        since_id: int | None = None,
        limit: int = 10000,
        decimate: int = 1,
    ) -> dict[str, Any]:
        points = await _store(request).query_plot_series_safe(
            name=name, last_ms=last_ms, since_id=since_id, limit=limit, decimate=decimate
        )
        return {"name": name, "points": points}

    @app.get("/plot/export")
    async def plot_export(
        request: Request,
        names: str,
        last_ms: int | None = None,
        format: str = "long",
    ):
        name_list = [n for n in names.split(",") if n]
        if not name_list:
            return _bad_request("names is required")
        if format not in ("long", "wide"):
            return _bad_request("format must be 'long' or 'wide'")
        store = _store(request)
        if format == "wide":
            sids = await store.export_sids_safe(names=name_list, last_ms=last_ms)
            if len(sids) > 1:
                return _bad_request("wide export requires all channels to share one stream")
        rows = store.iter_plot_export(names=name_list, last_ms=last_ms)
        stream = _csv_wide(rows, name_list) if format == "wide" else _csv_long(rows)
        return StreamingResponse(
            stream,
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="plot.csv"'},
        )

    @app.post("/wait")
    async def wait(request: Request, body: WaitBody):
        return await _do_wait(request, body)

    @app.post("/marker")
    async def marker(request: Request, body: MarkerBody):
        row = await _store(request).add_line(
            ts=time.time(),
            port=body.port or "",
            dir="-",
            chan="marker",
            seq=None,
            raw=body.text,
        )
        return {"line_id": row["id"]}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket, port: str | None = None):
        await websocket.accept()
        store: Store = websocket.app.state.store
        try:
            q = store.subscribe(port)
        except StoreError:
            await websocket.close(code=1013)  # try again later: subscriber cap reached
            return

        # Two concurrent halves: a pump pushing rows out, and a receive loop whose only
        # job is to notice the disconnect. Without the receive loop, a client that goes
        # away while no rows are flowing leaks its queue and handler task until the
        # next broadcast happens to fail (Starlette surfaces disconnects via receive()).
        async def pump() -> None:
            while True:
                row = await q.get()
                await websocket.send_json(row)

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    break
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            pump_task.cancel()
            with _ignore():
                await pump_task
            store.unsubscribe(q)


def _search_batch(pattern, texts: list[str]) -> int | None:
    """Return the index of the first text matching `pattern`, or None.

    Called off the event loop so a slow pattern cannot stall it; batched so a burst of
    lines costs one executor hop, not one per row (per-row hops fall behind at high
    line rates, the subscriber queue then drops oldest, and a real match can be lost).
    """
    for i, text in enumerate(texts):
        if pattern.search(text) is not None:
            return i
    return None


async def _do_wait(request: Request, body: WaitBody) -> dict[str, Any]:
    import re

    store = _store(request)
    ports = _ports(request)
    loop = asyncio.get_running_loop()

    port_obj = None
    port_filter = None
    if body.port is not None or body.send is not None:
        try:
            port_obj = ports.resolve(body.port)
            port_filter = port_obj.alias
        except PortError as exc:
            return _bad_request(str(exc))

    if body.since != "now":
        return _bad_request('only since="now" is supported')
    if len(body.match) > MAX_MATCH_LEN:
        return _bad_request(f"match regex too long (max {MAX_MATCH_LEN} chars)")
    try:
        pattern = re.compile(body.match)
    except re.error as exc:
        return _bad_request(f"bad match regex: {exc}")

    try:
        q = store.subscribe(port_filter)
    except StoreError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    started = loop.time()
    try:
        start_id = store.max_id()
        cmd_result = None
        if body.send is not None and port_obj is not None:
            try:
                if body.send_mode == "raw":
                    await port_obj.send_raw(body.send)
                else:
                    cmd_result = await port_obj.send_command(body.send, body.timeout_ms)
            except PortError as exc:
                return _bad_request(str(exc))

        deadline = started + body.timeout_ms / 1000.0
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                row = await asyncio.wait_for(q.get(), timeout=remaining)
            except TimeoutError:
                break
            # Drain everything already queued so the whole burst is evaluated in one
            # executor hop (see _search_batch).
            batch = [row]
            while True:
                try:
                    batch.append(q.get_nowait())
                except asyncio.QueueEmpty:
                    break
            candidates = [
                r for r in batch
                if r["id"] > start_id and (body.chan is None or r["chan"] == body.chan)
            ]
            if not candidates:
                continue
            idx = await loop.run_in_executor(
                None, _search_batch, pattern, [r["raw"] for r in candidates]
            )
            if idx is not None:
                return {
                    "status": "match",
                    "line": candidates[idx],
                    "waited_ms": (loop.time() - started) * 1000.0,
                    "cmd_result": cmd_result,
                }
        return {
            "status": "timeout",
            "line": None,
            "waited_ms": (loop.time() - started) * 1000.0,
            "cmd_result": cmd_result,
        }
    finally:
        store.unsubscribe(q)


def _fmt_num(value: Any) -> str:
    return "" if value is None else str(value)


def _csv_cell(value: Any) -> str:
    """One RFC-4180 CSV cell, hardened against spreadsheet formula injection.

    Channel names and sids come from device `!pd`/`!p` lines, so a name like `=cmd(...)` or one
    containing a comma/quote/newline must neither execute on open in a spreadsheet nor break out
    of its cell. A leading formula/control char is prefixed with an apostrophe; the cell is
    quoted when it contains a delimiter. (Numeric fields go through `_fmt_num`, so a legitimate
    negative value is never mistaken for a formula.)
    """
    s = "" if value is None else str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        s = "'" + s
    if any(c in s for c in (",", '"', "\n", "\r")):
        s = '"' + s.replace('"', '""') + '"'
    return s


def _csv_long(rows: Iterable[dict[str, Any]]):
    """Yield long CSV: one point per row (ts,tick_ms,sid,name,value)."""
    yield "ts,tick_ms,sid,name,value\n"
    for r in rows:
        yield (
            f"{_fmt_num(r['ts'])},{_fmt_num(r['tick_ms'])},{_csv_cell(r['sid'] or '')},"
            f"{_csv_cell(r['name'])},{_fmt_num(r['value'])}\n"
        )


def _csv_wide(rows: Iterable[dict[str, Any]], names: list[str]):
    """Yield wide CSV: one sample line per row (ts,tick_ms,<name>,...).

    Rows arrive ordered by (line_id, name); points sharing a line_id are one sample.
    """
    yield "ts,tick_ms," + ",".join(_csv_cell(n) for n in names) + "\n"
    cur_id: int | None = None
    ts = tick = None
    values: dict[str, Any] = {}

    def emit() -> str:
        cols = ",".join(_fmt_num(values.get(n)) for n in names)
        return f"{_fmt_num(ts)},{_fmt_num(tick)},{cols}\n"

    for r in rows:
        if r["line_id"] != cur_id:
            if cur_id is not None:
                yield emit()
            cur_id = r["line_id"]
            ts, tick, values = r["ts"], r["tick_ms"], {}
        values[r["name"]] = r["value"]
    if cur_id is not None:
        yield emit()


def _by_id_map() -> dict[str, str]:
    """Map realpath(device) -> /dev/serial/by-id/... symlink, best effort (Linux)."""
    base = "/dev/serial/by-id"
    result: dict[str, str] = {}
    if os.name != "posix" or not os.path.isdir(base):
        return result
    try:
        for name in os.listdir(base):
            link = os.path.join(base, name)
            result[os.path.realpath(link)] = link
    except OSError:
        pass
    return result


class _ignore:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc) -> bool:
        return True
