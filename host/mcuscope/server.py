"""FastAPI REST + WebSocket app (SPEC 3.4) and app assembly (lifespan wiring).

`create_app(config)` builds the app with a lifespan that starts the Store, attaches
autoconnect ports, and tears everything down on shutdown. Endpoints reach shared state
through `request.app.state` (store, ports, config, start_time).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Iterable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from serial.tools import list_ports
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from . import protocol as p
from .config import (
    Config,
    ConfigError,
    PortConfig,
    StorageConfig,
    default_config_path,
    load_config,
    resolve_db_path,
    save_ports,
    save_server,
    save_storage,
)
from .serial_link import PortError, PortManager, validate_device
from .store import Store, StoreError

log = logging.getLogger("mcuscope.server")

# Longest user-supplied regex accepted on /lines and /wait. A short bound rejects the most
# obvious oversized patterns; it is NOT a full ReDoS defence (a short catastrophic-backtracking
# pattern can still burn CPU on the loop thread - see SPEC 3.4 hardening notes).
MAX_MATCH_LEN = 200

# Bounds for client-supplied command/wait timeouts. A huge timeout would hold the
# port's command lock (or a fan-out subscriber queue) hostage for its whole duration.
MAX_TIMEOUT_MS = 300_000

# Smallest capture size cap accepted (0 always means "no cap"). A floor keeps a mistyped
# value from trimming a capture to nothing the moment it is saved; the daemon's own
# start/stop and port sys rows alone need more room than a handful of kilobytes.
MIN_DB_CAP_BYTES = 1 << 20   # 1 MiB

# Most patterns accepted on one /assert call. Each pattern costs a query (retrospective)
# or a per-line search (live), so the count is bounded like the pattern length is.
MAX_ASSERT_PATTERNS = 16

# Most rows coalesced into one /ws frame. Bounds frame size (and the json.dumps behind
# it) while still collapsing a burst into a single write.
WS_BATCH_MAX = 500

# Failed-token rate limiting (see _TokenGuard): after TOKEN_FAIL_MAX wrong tokens from one
# client address within TOKEN_FAIL_WINDOW_S, further attempts from that address are refused
# for TOKEN_LOCKOUT_S without even comparing, throttling online brute force to a rate at
# which any realistic token is unguessable. The web UI prompts at most 3 times, so a
# legitimate typo never comes near the limit.
TOKEN_FAIL_MAX = 10
TOKEN_FAIL_WINDOW_S = 60.0
TOKEN_LOCKOUT_S = 60.0
TOKEN_FAIL_TABLE_MAX = 1024  # prune stale per-address records past this many entries

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


class AssertBody(BaseModel):
    port: str | None = None
    expect: list[str] = Field(default_factory=list, max_length=MAX_ASSERT_PATTERNS)
    forbid: list[str] = Field(default_factory=list, max_length=MAX_ASSERT_PATTERNS)
    # 0 means retrospective: judge lines already captured. > 0 opens a live window.
    timeout_ms: int = Field(default=0, ge=0, le=MAX_TIMEOUT_MS)
    send: str | None = None
    send_mode: str = "cmd"
    chan: str | None = None
    session: str | None = None   # retrospective scope
    last_ms: int | None = Field(default=None, gt=0)


class PurgeBody(BaseModel):
    session: str | None = None
    before_ts: float | None = None
    id_from: int | None = Field(default=None, ge=1)
    id_to: int | None = Field(default=None, ge=1)
    all: bool = False
    dry_run: bool = False


class MarkerBody(BaseModel):
    port: str | None = None
    text: str


class SessionBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    note: str = Field(default="", max_length=1024)


class ConfigServerBody(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)


class ConfigStorageBody(BaseModel):
    db_path: str = Field(default="", max_length=1024)
    retention_days: int = Field(ge=1, le=3650)
    # 0 disables the size cap. The floor above 0 exists so a mistyped cap (say 5000)
    # cannot silently trim a capture down to nothing the moment it is saved.
    max_db_bytes: int = Field(default=0, ge=0, le=1 << 42)
    min_sessions: int = Field(default=StorageConfig.min_sessions, ge=0, le=1000)


class ConfigPortEntry(BaseModel):
    alias: str = Field(pattern=_ALIAS_RE)
    device: str | None = Field(default=None, max_length=512)
    serial_number: str | None = Field(default=None, max_length=128)
    baud: int = Field(default=115200, gt=0, le=100_000_000)
    autoconnect: bool = True


class ConfigPortsBody(BaseModel):
    ports: list[ConfigPortEntry] = Field(max_length=64)


# -- app assembly ---------------------------------------------------------------------


def create_app(config: Config, config_path: str | os.PathLike[str] | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        store = Store(resolve_db_path(config))
        await store.start(
            config.storage.retention_days,
            config.storage.max_db_bytes,
            config.storage.min_sessions,
        )
        ports = PortManager(store, loop)
        app.state.store = store
        app.state.ports = ports
        app.state.config = config
        app.state.config_path = Path(config_path) if config_path else default_config_path()
        # Serializes read-modify-write config saves (SPEC 3.3.1).
        app.state.config_write_lock = asyncio.Lock()
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
            with suppress(Exception):
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

    Wrong tokens are rate limited per client address (TOKEN_FAIL_* above): past the
    failure budget, requests from that address get a 429 (WS: close 1013) for the
    lockout period without the token even being compared. Attempts during a lockout do
    not extend it, so a web UI stuck retrying a stale token recovers on its own once
    the user fixes the token.
    """

    def __init__(self, app, token: str | None = None) -> None:
        self.app = app
        self.token = token
        # Compare as bytes: str compare_digest raises TypeError on non-ASCII input,
        # which a hostile header could trigger on every request.
        self._token_bytes = token.encode("utf-8") if token is not None else None
        # client address -> [failure count, window start, locked-until] (monotonic clock).
        # Only touched from the event loop thread, so no locking is needed.
        self._fails: dict[str, list[float]] = {}

    def _locked_out(self, host: str, now: float) -> bool:
        rec = self._fails.get(host)
        return rec is not None and now < rec[2]

    def _register_failure(self, host: str, now: float) -> None:
        if len(self._fails) >= TOKEN_FAIL_TABLE_MAX:
            self._prune(now)
        count, start, locked = self._fails.get(host) or [0, now, 0.0]
        if now - start > TOKEN_FAIL_WINDOW_S:
            count, start = 0, now
        count += 1
        if count >= TOKEN_FAIL_MAX:
            locked = now + TOKEN_LOCKOUT_S
        self._fails[host] = [count, start, locked]

    def _prune(self, now: float) -> None:
        """Drop records whose window and lockout have both expired (bounds the table)."""
        expired = [
            host for host, (count, start, locked) in self._fails.items()
            if now - start > TOKEN_FAIL_WINDOW_S and now >= locked
        ]
        for host in expired:
            del self._fails[host]

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
            # Exactly the UI mount and the root redirect: a prefix match on "/ui" would
            # also exempt any future route that merely starts with those letters.
            static_ok = scope["type"] == "http" and (
                path == "/" or path == "/ui" or path.startswith("/ui/")
            )
            if client_host not in _LOOPBACK_CLIENTS and not static_ok:
                now = time.monotonic()
                if self._locked_out(client_host, now):
                    await self._deny_rate_limited(scope, send)
                    return
                provided = self._provided_token(scope)
                if provided is None or not hmac.compare_digest(
                    provided.encode("utf-8"), self._token_bytes
                ):
                    # A missing token is a client without credentials, not a guess; only
                    # wrong tokens count toward the brute-force budget.
                    if provided is not None:
                        self._register_failure(client_host, now)
                    await self._deny(scope, send)
                    return
                self._fails.pop(client_host, None)  # correct token: clear the slate
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

    async def _deny_rate_limited(self, scope, send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1013})  # try again later
            return
        body = b'{"error":"too many failed token attempts; try again later"}'
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"retry-after", str(int(TOKEN_LOCKOUT_S)).encode()),
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
            "db_max_bytes": cfg.storage.max_db_bytes,
            "lines_trimmed": store.lines_trimmed,
            "session": store.active_session(),
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

    @app.post("/ports/{alias}/reconnect")
    async def reconnect_port(request: Request, alias: str):
        # Re-attach with the port's own parameters: tears down the old reader thread and
        # starts a fresh one, so a port sitting in max reconnect backoff (or one whose
        # reader wedged) retries immediately. A no-op-shaped action for connected ports.
        ports = _ports(request)
        pt = ports.get(alias)
        if pt is None:
            return _bad_request(f"no such port: {alias}")
        try:
            pt = await ports.attach(alias, pt.device, pt.baud, pt.serial_number)
        except PortError as exc:
            return _bad_request(str(exc))
        return {"port": pt.status()}

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

    # -- config write-back (SPEC 3.3.1) ------------------------------------------------

    def _cfg_path(request: Request) -> Path:
        return request.app.state.config_path

    def _config_write_denied(request: Request) -> JSONResponse | None:
        # Config write includes the bind address and a file path, so it is held to a
        # higher bar than the rest of the API: non-loopback clients may edit config
        # only when a token is set (the token itself was already checked by the
        # middleware). Loopback is always allowed.
        client = request.client
        if client is not None and client.host in _LOOPBACK_CLIENTS:
            return None
        cfg: Config = request.app.state.config
        if cfg.server.token is not None:
            return None
        return JSONResponse(
            status_code=403,
            content={
                "error": "config editing from the network requires an access token; "
                "restart mcuscoped with MCUSCOPED_TOKEN set"
            },
        )

    def _save_error(exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"error": f"config save failed: {exc}"})

    @app.get("/config")
    async def get_config(request: Request) -> Any:
        path = _cfg_path(request)
        try:
            saved = await asyncio.to_thread(load_config, path)
        except ConfigError as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})
        running: Config = request.app.state.config
        restart_required = (
            saved.server.host != running.server.host
            or saved.server.port != running.server.port
            or resolve_db_path(saved) != resolve_db_path(running)
        )
        return {
            "path": str(path),
            "exists": path.exists(),
            "server": {"host": saved.server.host, "port": saved.server.port},
            "storage": {
                "db_path": saved.storage.db_path,
                "retention_days": saved.storage.retention_days,
                "max_db_bytes": saved.storage.max_db_bytes,
                "min_sessions": saved.storage.min_sessions,
            },
            "ports": [
                {
                    "alias": pc.alias,
                    "device": pc.device,
                    "serial_number": pc.serial_number,
                    "baud": pc.baud,
                    "autoconnect": pc.autoconnect,
                }
                for pc in saved.ports
            ],
            "token_set": running.server.token is not None,
            "restart_required": restart_required,
        }

    @app.put("/config/server")
    async def put_config_server(request: Request, body: ConfigServerBody):
        if denied := _config_write_denied(request):
            return denied
        host = body.host.strip()
        if not host or any(c.isspace() or ord(c) < 0x20 for c in host):
            return _bad_request("invalid host")
        try:
            async with request.app.state.config_write_lock:
                await asyncio.to_thread(save_server, _cfg_path(request), host, body.port)
        except (ConfigError, OSError) as exc:
            return _save_error(exc)
        running: Config = request.app.state.config
        restart = host != running.server.host or body.port != running.server.port
        return {"ok": True, "restart_required": restart}

    @app.put("/config/storage")
    async def put_config_storage(request: Request, body: ConfigStorageBody):
        if denied := _config_write_denied(request):
            return denied
        db_path = body.db_path.strip()
        if any(ord(c) < 0x20 for c in db_path):
            return _bad_request("invalid db_path")
        if body.max_db_bytes and body.max_db_bytes < MIN_DB_CAP_BYTES:
            return _bad_request(
                f"max_db_bytes must be 0 (no cap) or at least {MIN_DB_CAP_BYTES} bytes"
            )
        try:
            async with request.app.state.config_write_lock:
                await asyncio.to_thread(
                    save_storage, _cfg_path(request), db_path, body.retention_days,
                    body.max_db_bytes, body.min_sessions,
                )
        except (ConfigError, OSError) as exc:
            return _save_error(exc)
        running: Config = request.app.state.config
        # Everything but db_path applies live; db_path only on restart.
        store = _store(request)
        store.set_retention_days(body.retention_days)
        store.set_max_db_bytes(body.max_db_bytes)
        store.set_min_sessions(body.min_sessions)
        running.storage.retention_days = body.retention_days
        running.storage.max_db_bytes = body.max_db_bytes
        running.storage.min_sessions = body.min_sessions
        saved_view = Config(storage=StorageConfig(db_path=db_path))
        restart = resolve_db_path(saved_view) != resolve_db_path(running)
        return {"ok": True, "restart_required": restart}

    @app.put("/config/ports")
    async def put_config_ports(request: Request, body: ConfigPortsBody):
        if denied := _config_write_denied(request):
            return denied
        seen: set[str] = set()
        entries: list[PortConfig] = []
        for entry in body.ports:
            if entry.alias in seen:
                return _bad_request(f"duplicate alias: {entry.alias}")
            seen.add(entry.alias)
            device = (entry.device or "").strip() or None
            serial_number = (entry.serial_number or "").strip() or None
            if not device and not serial_number:
                return _bad_request(f"port {entry.alias}: device or serial_number required")
            try:
                validate_device(device)
            except PortError as exc:
                return _bad_request(f"port {entry.alias}: {exc}")
            entries.append(
                PortConfig(
                    alias=entry.alias,
                    device=device,
                    serial_number=serial_number,
                    baud=entry.baud,
                    autoconnect=entry.autoconnect,
                )
            )
        try:
            async with request.app.state.config_write_lock:
                await asyncio.to_thread(save_ports, _cfg_path(request), entries)
        except (ConfigError, OSError) as exc:
            return _save_error(exc)
        return {"ok": True, "restart_required": False}

    # -- sessions (named spans of the capture timeline) ---------------------------------

    def _session_range(request: Request, ref: str | None) -> tuple[int | None, int | None]:
        """Resolve a `session=` query value into inclusive id bounds.

        An unknown reference yields a range that matches nothing rather than silently
        widening to the whole capture: a typo in a session name must not hand back every
        line ever stored as if it were that run.
        """
        return _session_range_for(_store(request), ref)

    @app.get("/sessions")
    async def list_sessions(request: Request, limit: int = 50) -> dict[str, Any]:
        return {
            "sessions": _store(request).list_sessions(limit),
            "active": _store(request).active_session(),
        }

    @app.post("/sessions")
    async def start_session(request: Request, body: SessionBody):
        return {"session": await _store(request).start_session(body.name.strip(), body.note)}

    @app.post("/sessions/stop")
    async def stop_session(request: Request):
        session = await _store(request).stop_session()
        if session is None:
            return _bad_request("no session is running")
        return {"session": session}

    @app.delete("/sessions/{session_id}")
    async def delete_session(request: Request, session_id: int, data: bool = False):
        """Delete a session. `data=true` also deletes the lines it covers.

        The label and the capture are separable on purpose: forgetting a mislabelled run
        should not destroy what was recorded, and deleting a run's data is destructive
        enough to deserve saying so explicitly.
        """
        store = _store(request)
        session = store.resolve_session(str(session_id))
        if session is None:
            return _bad_request(f"no such session: {session_id}")
        deleted = 0
        if data:
            end_id = session["end_id"] if session["end_id"] is not None else store.max_id()
            deleted = await store.delete_range(session["start_id"], end_id)
        store.delete_session(session_id)
        return {"ok": True, "lines_deleted": deleted}

    @app.get("/sessions/{ref}/export")
    async def export_session(request: Request, ref: str):
        """Download one session as a standalone capture database (SPEC 3.4).

        Built into a temp file on a worker thread, streamed, then removed. The copy is a
        normal capture file, so the archive of a run is queryable with the same tools as
        the live capture rather than being a dead format.
        """
        store = _store(request)
        session = store.resolve_session(ref)
        if session is None:
            return _bad_request(f"no such session: {ref}")
        fd, tmp_path = tempfile.mkstemp(prefix="mcuscope-session-", suffix=".db")
        os.close(fd)
        os.unlink(tmp_path)   # sqlite3.connect creates it; an existing empty file is not a DB
        try:
            await asyncio.to_thread(
                store.export_session_db,
                tmp_path,
                id_from=session["start_id"],
                id_to=session["end_id"],
                session=session,
            )
        except Exception as exc:
            with suppress(OSError):
                os.unlink(tmp_path)
            log.error("session export failed: %s", exc)
            return _bad_request(f"export failed: {exc}")
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", session["name"]) or "session"
        return FileResponse(
            tmp_path,
            media_type="application/vnd.sqlite3",
            filename=f"{safe}.db",
            background=BackgroundTask(_unlink_later, tmp_path),
        )

    @app.post("/purge")
    async def purge(request: Request, body: PurgeBody):
        """Delete captured lines by session, time, or id range (SPEC 3.4).

        Exactly one selector is required. `dry_run` reports what would go without touching
        anything, which is what makes this safe to offer at all: a purge is not recoverable,
        so the count is available before the delete rather than only after it.
        """
        store = _store(request)
        selectors = [
            body.session is not None,
            body.before_ts is not None,
            body.id_from is not None or body.id_to is not None,
            body.all,
        ]
        if sum(selectors) != 1:
            return _bad_request(
                "exactly one of session, before_ts, id_from/id_to, all is required"
            )
        if body.session is not None:
            session = store.resolve_session(body.session)
            if session is None:
                return _bad_request(f"no such session: {body.session}")
            lo = session["start_id"]
            hi = session["end_id"] if session["end_id"] is not None else store.max_id()
        elif body.before_ts is not None:
            lo = 1
            last = store.last_id_before_ts(body.before_ts)
            if last is None:
                return {"deleted": 0, "id_from": None, "id_to": None, "dry_run": body.dry_run}
            hi = last
        elif body.all:
            lo, hi = 1, store.max_id()
        else:
            lo = body.id_from if body.id_from is not None else 1
            hi = body.id_to if body.id_to is not None else store.max_id()
        if hi < lo:
            return {"deleted": 0, "id_from": lo, "id_to": hi, "dry_run": body.dry_run}
        if body.dry_run:
            n = store.count_lines(id_from=lo, id_to=hi)
            return {"deleted": n, "id_from": lo, "id_to": hi, "dry_run": True}
        deleted = await store.delete_range(lo, hi)
        log.warning("storage: purged %d lines (ids %d-%d) on request", deleted, lo, hi)
        return {"deleted": deleted, "id_from": lo, "id_to": hi, "dry_run": False}

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
        session: str | None = None,
        limit: int = 100,
        order: str = "desc",
    ) -> dict[str, Any]:
        if match is not None and len(match) > MAX_MATCH_LEN:
            return _bad_request(f"match regex too long (max {MAX_MATCH_LEN} chars)")
        id_from, id_to = _session_range(request, session)
        rows, truncated = await _store(request).query_lines_safe(
            port=port,
            chans=chan,
            match=match,
            since_id=since_id,
            since_ts=since_ts,
            last_ms=last_ms,
            id_from=id_from,
            id_to=id_to,
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
        session: str | None = None,
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
        id_from, id_to = _session_range(request, session)
        rows, truncated = _store(request).query_can_frames(
            port=port, can_id=can_id, last_ms=last_ms, since_id=since_id,
            id_from=id_from, id_to=id_to, limit=limit,
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
        session: str | None = None,
        limit: int = 10000,
        decimate: int = 1,
    ) -> dict[str, Any]:
        id_from, id_to = _session_range(request, session)
        points = await _store(request).query_plot_series_safe(
            name=name, last_ms=last_ms, since_id=since_id, id_from=id_from, id_to=id_to,
            limit=limit, decimate=decimate,
        )
        return {"name": name, "points": points}

    @app.get("/plot/export")
    async def plot_export(
        request: Request,
        names: str,
        last_ms: int | None = None,
        session: str | None = None,
        format: str = "long",
    ):
        name_list = [n for n in names.split(",") if n]
        if not name_list:
            return _bad_request("names is required")
        if format not in ("long", "wide"):
            return _bad_request("format must be 'long' or 'wide'")
        store = _store(request)
        id_from, id_to = _session_range(request, session)
        if format == "wide":
            sids = await store.export_sids_safe(
                names=name_list, last_ms=last_ms, id_from=id_from, id_to=id_to
            )
            if len(sids) > 1:
                return _bad_request("wide export requires all channels to share one stream")
        rows = store.iter_plot_export(
            names=name_list, last_ms=last_ms, id_from=id_from, id_to=id_to
        )
        stream = _csv_wide(rows, name_list) if format == "wide" else _csv_long(rows)
        return StreamingResponse(
            stream,
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="plot.csv"'},
        )

    @app.post("/wait")
    async def wait(request: Request, body: WaitBody):
        return await _do_wait(request, body)

    @app.post("/assert")
    async def assert_(request: Request, body: AssertBody):
        return await _do_assert(request, body)

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
                rows = [await q.get()]
                # Coalesce whatever else is already queued into one frame (SPEC 3.4: each
                # message is an array). A frame - and a json.dumps, and a TCP write - per
                # row is what an attached subscriber costs at high line rates; every client
                # renders on a timer anyway, so the coalescing is free on their side.
                while len(rows) < WS_BATCH_MAX:
                    try:
                        rows.append(q.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                await websocket.send_text(json.dumps(rows, separators=(",", ":")))

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
            with suppress(asyncio.CancelledError, Exception):
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

    # Read the watermark BEFORE subscribing: subscribe can only enqueue newer ids, so a
    # line committed between the two calls is still delivered. The other order could
    # enqueue a row and then read a max_id that already covers it, dropping a real match.
    start_id = store.max_id()
    try:
        q = store.subscribe(port_filter)
    except StoreError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    started = loop.time()
    try:
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


def _unlink_later(path: str) -> None:
    """Remove a streamed temp file once the response has been sent."""
    with suppress(OSError):
        os.unlink(path)


def _scan_batch(patterns: list[Any], texts: list[str]) -> list[tuple[int, int]]:
    """Every (pattern index, text index) first hit in this batch, evaluated off the loop.

    One executor hop per burst per direction, like `_search_batch`: an assertion carries
    several patterns, and a hop per pattern per line would fall behind a fast capture and
    lose rows to the subscriber queue's drop-oldest.
    """
    hits: list[tuple[int, int]] = []
    for pi, pattern in enumerate(patterns):
        for ti, text in enumerate(texts):
            if pattern.search(text) is not None:
                hits.append((pi, ti))
                break
    return hits


def _compile_patterns(patterns: list[str]) -> tuple[list[Any] | None, str]:
    out = []
    for pat in patterns:
        if len(pat) > MAX_MATCH_LEN:
            return None, f"regex too long (max {MAX_MATCH_LEN} chars): {pat[:40]}..."
        try:
            out.append(re.compile(pat))
        except re.error as exc:
            return None, f"bad regex {pat!r}: {exc}"
    return out, ""


async def _do_assert(request: Request, body: AssertBody) -> Any:
    """Judge a capture window against expected and forbidden patterns (SPEC 3.4).

    Two modes, one verdict shape. With `timeout_ms = 0` the assertion is retrospective:
    already-stored lines are judged, so a run that has finished (or a named session from
    last week) can be checked without having watched it happen. With `timeout_ms > 0` it
    is live: the window opens now, optionally sends something first, and closes as soon as
    every expectation is met - or when the timeout expires.

    Forbidden patterns are judged over whatever window actually elapsed. Absence cannot be
    proven early, so an assertion with no expectations always runs its window to the end;
    one with expectations ends when they are met, and the forbid verdict then covers
    exactly the span the expectations needed. Any forbidden match fails immediately: there
    is no reason to keep waiting once the verdict is decided.
    """
    store = _store(request)
    if not body.expect and not body.forbid:
        return _bad_request("at least one expect or forbid pattern is required")
    expect_pats, err_msg = _compile_patterns(body.expect)
    if expect_pats is None:
        return _bad_request(err_msg)
    forbid_pats, err_msg = _compile_patterns(body.forbid)
    if forbid_pats is None:
        return _bad_request(err_msg)

    expect_hits: list[dict[str, Any] | None] = [None] * len(body.expect)
    forbid_hits: list[dict[str, Any] | None] = [None] * len(body.forbid)

    def verdict(checked: int, elapsed_ms: float) -> dict[str, Any]:
        ok = all(h is not None for h in expect_hits) and all(h is None for h in forbid_hits)
        return {
            "status": "pass" if ok else "fail",
            "expect": [
                {"pattern": pat, "matched": hit is not None, "line": hit}
                for pat, hit in zip(body.expect, expect_hits, strict=True)
            ],
            "forbid": [
                {"pattern": pat, "matched": hit is not None, "line": hit}
                for pat, hit in zip(body.forbid, forbid_hits, strict=True)
            ],
            "checked_lines": checked,
            "elapsed_ms": elapsed_ms,
        }

    if body.timeout_ms == 0:
        # Retrospective: one bounded query per pattern rather than pulling the window into
        # memory and scanning it here. Each is `raw REGEXP ?` over an id range, offloaded
        # by query_lines_safe, and stops at the first hit.
        id_from, id_to = _session_range_for(store, body.session)
        if body.session is not None and id_from == 1 and id_to == 0:
            return _bad_request(f"no such session: {body.session}")
        scope = {
            "port": body.port,
            "chans": [body.chan] if body.chan else None,
            "id_from": id_from,
            "id_to": id_to,
            "last_ms": body.last_ms,
        }
        started = time.monotonic()
        for i, pat in enumerate(body.expect):
            rows, _ = await store.query_lines_safe(match=pat, limit=1, order="asc", **scope)
            expect_hits[i] = rows[0] if rows else None
        for i, pat in enumerate(body.forbid):
            rows, _ = await store.query_lines_safe(match=pat, limit=1, order="asc", **scope)
            forbid_hits[i] = rows[0] if rows else None
        checked = store.count_lines(
            port=body.port, chan=body.chan, id_from=id_from, id_to=id_to, last_ms=body.last_ms
        )
        return verdict(checked, (time.monotonic() - started) * 1000.0)

    # Live: same subscribe-before-watermark ordering as /wait, so a line committed between
    # the two calls is judged rather than missed.
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
    start_id = store.max_id()
    try:
        q = store.subscribe(port_filter)
    except StoreError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    started = loop.time()
    checked = 0
    try:
        if body.send is not None and port_obj is not None:
            try:
                if body.send_mode == "raw":
                    await port_obj.send_raw(body.send)
                else:
                    await port_obj.send_command(body.send, body.timeout_ms)
            except PortError as exc:
                return _bad_request(str(exc))

        deadline = started + body.timeout_ms / 1000.0
        while True:
            if body.expect and all(h is not None for h in expect_hits):
                break   # every expectation met: the window has served its purpose
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                row = await asyncio.wait_for(q.get(), timeout=remaining)
            except TimeoutError:
                break
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
            checked += len(candidates)
            texts = [r["raw"] for r in candidates]
            if forbid_pats:
                hits = await loop.run_in_executor(None, _scan_batch, forbid_pats, texts)
                for pi, ti in hits:
                    if forbid_hits[pi] is None:
                        forbid_hits[pi] = candidates[ti]
                if any(h is not None for h in forbid_hits):
                    break   # the verdict is decided; waiting longer cannot change it
            pending = [i for i, h in enumerate(expect_hits) if h is None]
            if pending:
                hits = await loop.run_in_executor(
                    None, _scan_batch, [expect_pats[i] for i in pending], texts
                )
                for pi, ti in hits:
                    expect_hits[pending[pi]] = candidates[ti]
        return verdict(checked, (loop.time() - started) * 1000.0)
    finally:
        store.unsubscribe(q)


def _session_range_for(store: Store, ref: str | None) -> tuple[int | None, int | None]:
    """`_session_range` without a Request: an unknown ref yields an empty range."""
    if ref is None:
        return None, None
    session = store.resolve_session(ref)
    if session is None:
        return 1, 0
    return session["start_id"], session["end_id"]


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
