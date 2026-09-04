"""FastAPI REST + WebSocket app (SPEC 3.4) and app assembly (lifespan wiring).

`create_app(config)` builds the app with a lifespan that starts the Store, attaches
autoconnect ports, and tears everything down on shutdown. Endpoints reach shared state
through `request.app.state` (store, ports, config, start_time).
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import re
import tempfile
import time
from collections.abc import Callable, Iterable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Literal, NamedTuple
from urllib.parse import parse_qs

# Third-party `regex`, not stdlib `re`, for every user-supplied pattern: it releases the
# GIL while matching and honours a timeout. See store._make_regexp.
import regex
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi import Path as PathParam  # aliased: `Path` here is pathlib's
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, pjstream
from . import protocol as p
from .config import (
    MAX_BAUD,
    MIN_DB_CAP_BYTES,
    Config,
    ConfigError,
    PortConfig,
    StorageConfig,
    default_config_path,
    load_config,
    resolve_db_path,
    save_plotjuggler,
    save_ports,
    save_server,
    save_storage,
    save_update,
)
from .link import Link
from .serial_link import PortError, PortManager, cached_comports, validate_device
from .store import (
    MATCH_BUDGET_S,
    MATCH_TIMEOUT_S,
    MatchBudgetExceeded,
    Store,
    StoreError,
    match_executor,
)
from .update_check import UpdateChecker

log = logging.getLogger("mcuscope.server")

# Longest user-supplied regex accepted on /lines and /wait. A first gate only: it is not a
# ReDoS defence, and never was - 7 characters are enough to write a catastrophic pattern.
# The real bound is the per-call timeout and per-query budget in store._make_regexp,
# which the `regex` engine can enforce because it releases the GIL and can be interrupted.
MAX_MATCH_LEN = 200

# Bounds for client-supplied command/wait timeouts. A huge timeout would hold the
# port's command lock (or a fan-out subscriber queue) hostage for its whole duration.
MAX_TIMEOUT_MS = 300_000

# Smallest capture size cap accepted (0 always means "no cap"). A floor keeps a mistyped
# value from trimming a capture to nothing the moment it is saved; the daemon's own
# start/stop and port sys rows alone need more room than a handful of kilobytes.
# Defined in config.py and imported, not restated: the loader applies the same floor to a
# hand-edited file, and two copies of one bound is how they drift apart (class 19).

# Most patterns accepted on one /assert call. Each pattern costs a query (retrospective)
# or a per-line search (live), so the count is bounded like the pattern length is.
MAX_ASSERT_PATTERNS = 16

# One ceiling for both attach paths: the live one had none, which is the wrong way round
# (the saved value is re-read and re-validated, the live one goes straight to the driver).
# Imported from config so the loader, the write-back API and live attach share one value.

# Rows one /plot/export may stream. Refused up front, never silently truncated.
MAX_EXPORT_ROWS = 1_000_000

# Ceilings for every integer parameter that is not clamped (SPEC 3.3.1). A Python int is
# arbitrary precision, so an unbounded one reached either a float conversion or a SQLite
# bind and raised OverflowError there: a 500 with a traceback for the caller's own bad
# input. Ids are the SQLite INTEGER range; the ms ceiling is far past any real window and
# still converts to float.
MAX_LINE_ID = 2**63 - 1
MAX_MS = 10**15
MAX_DECIMATE = 10**9

# How far into the future a purge `before_ts` may sit before it is refused. Small enough
# that only clock skew fits, because "older than T" with T in the future is a full wipe.
PURGE_FUTURE_SKEW_S = 60.0

# Most rows coalesced into one /ws frame. Bounds frame size (and the json.dumps behind
# it) while still collapsing a burst into a single write.
WS_BATCH_MAX = 500

# Idle keepalive interval for /ws. A subscriber whose client vanished without closing the
# TCP connection (LAN drop, laptop sleep, a killed browser) is only detected when a write
# to it fails, and on a quiet capture there may be no write for hours - until then its
# queue, its handler task and its fan-out slot are all still held. Writing an empty frame
# on an idle timer keeps that detection bounded by the network's own timeouts instead of
# by whether the target happens to be talking.
WS_KEEPALIVE_S = 20.0


def _enable_ws_backpressure() -> None:
    """Make `await websocket.send_text(...)` block once the peer stops reading.

    uvicorn's websockets-sansio protocol (what ws="auto" selects whenever websockets is
    installed) writes every frame straight to the asyncio transport, and gates its ASGI
    send on a `writable` Event that nothing ever clears: it defines no pause_writing or
    resume_writing, so the transport's high-water mark calls asyncio.Protocol's no-op.
    A client that stops reading then applies no backpressure at all - the /ws pump drains
    the subscriber queue as fast as rows arrive into an unbounded transport buffer, so
    drop-oldest never engages, no gap is announced and the daemon grows by the whole
    backlog (measured 2026-08-09: 1.34 MB of transport buffer for one stalled client after
    20k rows, with ws_dropped 0 and the queue empty; the keepalive ping does not shed it,
    because transport.close() cannot flush past the stalled peer).

    Wiring the two callbacks to the Event they already gate on restores the intended chain:
    transport over its high-water mark -> ASGI send blocks -> pump stops -> subscriber queue
    fills -> store sheds the oldest and counts it -> gap announced in-band on drain. Memory
    per connection is then the asyncio default 64 KiB write buffer plus the queue. A client
    that keeps up never reaches the high-water mark and is untouched.
    """
    try:
        from uvicorn.protocols.websockets.websockets_sansio_impl import (
            WebSocketsSansIOProtocol as proto,
        )
    except ImportError:   # a build without that impl (wsproto): nothing to wire
        log.warning("uvicorn has no websockets_sansio_impl: WS backpressure shedding is off")
        return
    if "pause_writing" in vars(proto):   # a uvicorn that does its own flow control wins
        return

    def pause_writing(self) -> None:
        self.writable.clear()

    def resume_writing(self) -> None:
        self.writable.set()

    proto.pause_writing = pause_writing
    proto.resume_writing = resume_writing

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


# The line ending appended to an outgoing line. A closed domain like SendMode below, and
# declared for the same reason: an unrecognised value must be a 422, never a silent LF.
# `None` on a request body means "the port's configured default".
Eol = Literal["none", "lf", "crlf"]


class PortAttach(BaseModel):
    alias: str = Field(pattern=_ALIAS_RE)
    device: str | None = None
    serial_number: str | None = None
    baud: int = Field(default=115200, gt=0, le=MAX_BAUD)
    eol: Eol = PortConfig.eol


class SendBody(BaseModel):
    port: str | None = None
    line: str
    eol: Eol | None = None


class CmdBody(BaseModel):
    port: str | None = None
    cmd: str
    timeout_ms: int = Field(default=1000, gt=0, le=MAX_TIMEOUT_MS)
    eol: Eol | None = None


class BreakBody(BaseModel):
    port: str | None = None
    # Bounded both ways: a 0 ms break is not a break, and an unbounded one parks a worker
    # thread (and the port's raw lock) for as long as the caller names. 2 s is far past
    # any receiver's break-detect threshold.
    ms: int = Field(default=250, ge=1, le=2000)


# The two closed domains a request can name. Declared rather than compared, because both
# were compared: `send_mode` was only ever tested `== "raw"`, so any other value silently
# sent as a *command* instead of failing, and `chan` was matched by equality against stored
# rows, so an unknown one simply never matched and the caller burned its whole timeout to be
# told "no match" rather than "no such channel". The primary consumer here is an agent, and
# a plausible negative answer to a typo is worse than an error (SPEC 3.4, and the `chan`
# domain is the one the lines table already CHECKs).
SendMode = Literal["cmd", "raw"]
Chan = Literal["debug", "cmd", "resp", "event", "marker", "sys"]


class WaitBody(BaseModel):
    port: str | None = None
    match: str
    timeout_ms: int = Field(default=2000, gt=0, le=MAX_TIMEOUT_MS)
    send: str | None = None
    send_mode: SendMode = "cmd"
    eol: Eol | None = None   # applies to `send`; None is the port default
    chan: Chan | None = None
    since: str = "now"  # only "now" is defined (SPEC 3.4); anything else is rejected
    # Resend `send` every N ms until the match. Deliberately not a Field bound: an
    # out-of-range value gets a 400 naming the bound it broke (the ceiling is timeout_ms,
    # which a Field cannot see), not a 422 the caller has to decode.
    repeat_ms: int | None = None


class AssertBody(BaseModel):
    port: str | None = None
    expect: list[str] = Field(default_factory=list, max_length=MAX_ASSERT_PATTERNS)
    forbid: list[str] = Field(default_factory=list, max_length=MAX_ASSERT_PATTERNS)
    # 0 means retrospective: judge lines already captured. > 0 opens a live window.
    timeout_ms: int = Field(default=0, ge=0, le=MAX_TIMEOUT_MS)
    # Hold a live window open this long even once every expectation is met, so a forbid
    # is judged over a stated span rather than however long the expects happened to take.
    min_window_ms: int = Field(default=0, ge=0, le=MAX_TIMEOUT_MS)
    send: str | None = None
    send_mode: SendMode = "cmd"
    eol: Eol | None = None   # applies to `send`; None is the port default
    chan: Chan | None = None
    session: str | None = None   # retrospective scope
    last_ms: int | None = Field(default=None, gt=0, le=MAX_MS)


class PurgeBody(BaseModel):
    session: str | None = None
    before_ts: float | None = None
    id_from: int | None = Field(default=None, ge=1, le=MAX_LINE_ID)
    id_to: int | None = Field(default=None, ge=1, le=MAX_LINE_ID)
    all: bool = False
    dry_run: bool = False


class MarkerBody(BaseModel):
    port: str | None = None
    # Bounded like SessionBody.note. Unbounded, a handful of loopback requests could write
    # megabytes each straight into the capture, and the size cap that would eventually
    # reclaim it is opt-in and off by default.
    text: str = Field(min_length=1, max_length=4096)


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
    auto_session: bool = StorageConfig.auto_session


class ConfigUpdateBody(BaseModel):
    check: bool


class PlotJugglerBody(BaseModel):
    """PUT /plotjuggler (runtime, SPEC 3.7): dest omitted means keep the current one."""
    enabled: bool
    dest: str | None = Field(default=None, min_length=1, max_length=255)


class ConfigPlotJugglerBody(BaseModel):
    enabled: bool
    dest: str = Field(min_length=1, max_length=255)


class ConfigPortEntry(BaseModel):
    alias: str = Field(pattern=_ALIAS_RE)
    device: str | None = Field(default=None, max_length=512)
    serial_number: str | None = Field(default=None, max_length=128)
    baud: int = Field(default=115200, gt=0, le=MAX_BAUD)
    autoconnect: bool = True
    identify: bool | None = None   # omitted: keep the saved value for this alias
    eol: Eol | None = None          # same: the settings dialog does not offer it either


class ConfigPortsBody(BaseModel):
    ports: list[ConfigPortEntry] = Field(max_length=64)


def auto_session_name() -> str:
    """Name for a session the daemon opens for its own run.

    Local time, sortable, and free of characters that would need quoting when it is passed
    back as `--session`. The `auto-` prefix keeps these distinguishable at a glance from
    anything a person named.
    """
    return time.strftime("auto-%Y-%m-%d_%H-%M-%S", time.localtime())


# -- app assembly ---------------------------------------------------------------------


def create_app(
    config: Config,
    config_path: str | os.PathLike[str] | None = None,
    shutdown_cb: Callable[[], None] | None = None,
    open_link_fn: Callable[[str, int], Link] | None = None,
) -> FastAPI:
    """Build the app. `shutdown_cb`, when given, makes POST /shutdown live: the real
    daemon passes a callback that ends the process; without one (tests, embedding)
    the endpoint answers with an error instead of killing the host process.

    `open_link_fn` is how every port obtains its transport, defaulting to opening the
    device with pyserial. `mcuscoped --sim` passes the simulator's, so the demo needs no
    loopback socket; the test harness passes one for the same reason."""
    _enable_ws_backpressure()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        store = Store(resolve_db_path(config))
        await store.start(
            config.storage.retention_days,
            config.storage.max_db_bytes,
            config.storage.min_sessions,
        )
        # Everything after store.start() runs under the try, so a failure *during* startup
        # still tears the store down. It used to open immediately before the yield, so an
        # exception in between (a full or read-only disk on the first add_line, an
        # OperationalError from start_session) left the writer task, the retention task and
        # the SQLite connection running with nothing to stop them.
        ports: PortManager | None = None
        checker: UpdateChecker | None = None
        pj: pjstream.PlotJugglerStreamer | None = None
        try:
            # PlotJuggler stream (SPEC 3.7), created before the manager so every port
            # it builds carries it. Enabling resolves the dest, so it runs off-loop
            # (a dead resolver must not stall startup) and a bad configured dest only
            # logs: the capture does not depend on the viewer.
            pj = pjstream.PlotJugglerStreamer(dest=config.plotjuggler.dest)
            app.state.pj = pj
            if config.plotjuggler.enabled:
                try:
                    await asyncio.to_thread(pj.configure, True)
                except (ValueError, OSError) as exc:
                    log.warning("plotjuggler: cannot enable for %r: %s",
                                config.plotjuggler.dest, exc)
            ports = PortManager(store, loop, open_link_fn=open_link_fn, pj=pj)
            app.state.store = store
            app.state.ports = ports
            app.state.config = config
            app.state.config_path = (
                Path(config_path) if config_path else default_config_path()
            )
            # Serializes read-modify-write config saves (SPEC 3.3.1).
            app.state.config_write_lock = asyncio.Lock()
            # Serializes stop + automatic reopen: the store's lock covers neither the
            # "is it mine to stop?" check nor the session opened after the stop.
            app.state.session_stop_lock = asyncio.Lock()
            app.state.shutdown_cb = shutdown_cb
            app.state.start_time = time.time()
            # Release check (SPEC 3.6): one call here, then one per `GET /status`; see
            # update_check for why there is no timer.
            checker = UpdateChecker(enabled=config.update.check)
            app.state.update_checker = checker
            checker.maybe_check()
            await store.add_line(
                ts=time.time(), port="", dir="-", chan="sys", seq=None, raw="daemon start"
            )
            open_session = store.active_session()
            if open_session is not None and not open_session["auto"]:
                # A named run outlives the daemon process: a restart mid-bench (config
                # change, upgrade) used to close it silently and file everything after
                # under an automatic session. Auto sessions are per daemon run and are
                # not resumed; start_session below closes a stale one.
                log.info("resuming session %s", open_session["name"])
                await store.add_line(
                    ts=time.time(), port="", dir="-", chan="sys", seq=None,
                    raw=f"resuming session: {open_session['name']}",
                )
            elif config.storage.auto_session:
                await store.start_session(auto_session_name(), auto=True)
            elif open_session is not None:
                await store.stop_session()   # a crashed run's automatic session
            for pc in config.ports:
                if pc.autoconnect:
                    try:
                        await ports.attach(
                            pc.alias, pc.device, pc.baud, pc.serial_number,
                            pc.identify, pc.eol,
                        )
                    except PortError as exc:
                        # One bad config entry must not abort startup: log it, record a
                        # sys row, and keep serving with the remaining ports.
                        log.error("autoconnect %s failed: %s", pc.alias, exc)
                        await store.add_line(
                            ts=time.time(), port="", dir="-", chan="sys", seq=None,
                            raw=f"autoconnect {pc.alias} failed: {exc}",
                        )
            yield
        finally:
            # Every step here is suppressed, because each one is the thing standing between
            # a failure in the step before it and store.stop(). An exception out of a port
            # detach used to skip the store shutdown entirely, leaking the writer task, the
            # retention task and the connection, and losing the daemon-stop row.
            if checker is not None:
                with suppress(Exception):
                    await checker.aclose()
            if pj is not None:
                with suppress(Exception):
                    pj.close()
            if ports is not None:
                with suppress(Exception):
                    await ports.stop_all()
            # Close the run before the daemon-stop row, so a session spans exactly the
            # time the daemon was up rather than trailing past its own shutdown notice.
            # Only an automatic session belongs to this daemon run; a named one stays open
            # and is resumed by the next start (see startup above).
            with suppress(Exception):
                active = store.active_session()
                if active is not None and active["auto"]:
                    await store.stop_session()
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
        # A sentence naming the field, not `str(exc.errors())`, which is a Python repr of a
        # list of dicts: it does contain the field name, buried in `'loc': ('body', 'chan')`
        # among quoting an agent then has to parse. CLAUDE.md names an agent as this API's
        # primary consumer, and it reads this string to decide what to fix.
        parts = []
        for err in exc.errors():
            where = ".".join(str(x) for x in err.get("loc", ())[1:]) or "request"
            msg = err.get("msg", "invalid")
            got = err.get("input")
            parts.append(f"{where}: {msg}" + (f" (got {got!r})" if got is not None else ""))
        detail = "; ".join(parts) or "invalid request"
        return JSONResponse(status_code=422, content={"error": detail})

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception):
        # SPEC 3.4: every error is a {"error": msg} JSON envelope, never a bare 500 page.
        # Log the traceback server-side; the client sees only the message.
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"error": str(exc)})

    _register_routes(app)
    _mount_webui(app)
    app.add_middleware(_SameOriginGuard, bind_host=config.server.host)
    app.add_middleware(_TokenGuard, token=config.server.token)
    return app


# Names that always denote this machine. Anything else must be an IP literal (which cannot
# be DNS-rebound) or the exact name the daemon was configured to bind.
_ALWAYS_ALLOWED_HOSTS = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


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


def _hostname_of(host: bytes) -> str | None:
    """The hostname part of a Host header, lowercased, port and IPv6 brackets removed."""
    try:
        h = host.decode("latin-1").strip().lower()
    except UnicodeDecodeError:
        return None
    if not h:
        return None
    if h.startswith("["):                    # [::1]:8558 -> ::1
        end = h.find("]")
        return None if end < 0 else h[1:end]
    return h.split(":", 1)[0]                # 127.0.0.1:8558 -> 127.0.0.1


def _host_allowed(host: bytes, bind_host: str) -> bool:
    """True if the Host header names an address this daemon may legitimately answer to.

    This is the actual DNS-rebinding defence. Comparing Origin to Host cannot provide one:
    in a real rebinding attack the page's origin *is* the attacker's hostname and the Host
    header carries that same hostname, so the two match and the request passes. Worse, the
    browser runs on the operator's machine, so the connection is from 127.0.0.1 and is also
    exempt from the token guard and the config-write denial.

    Rebinding needs a DNS name, because the attack is making a name resolve to a new
    address. An IP literal cannot be rebound, so literals are accepted, along with
    `localhost` and whatever name the daemon was configured to bind. Everything else -
    `evil.example` pointed at 127.0.0.1 - is refused before it reaches any route.
    """
    name = _hostname_of(host)
    if name is None:
        return False
    if name in _ALWAYS_ALLOWED_HOSTS:
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return name == bind_host.strip().lower()
    return True


class _SameOriginGuard:
    """Refuse cross-origin browser requests (CSRF, cross-site WebSocket, DNS rebinding).

    The REST/WS API is unauthenticated by design for the localhost workflow (SPEC 3.4), so a
    web page the operator merely visits could otherwise drive the hardware or read the capture
    stream via the browser. Browsers attach an `Origin` header to such cross-site fetches and
    to every WebSocket handshake; we reject any request whose Origin does not match its own
    Host. Non-browser clients (the `mcu` CLI, curl) send no Origin and are unaffected, and
    same-origin UI use - loopback or the LAN address the page was served from - always passes.
    A DNS-rebinding page keeps its original Origin, which no longer matches the rebound Host.

    What it does not cover: a browser sends no Origin on a no-cors subresource load
    (`<img src>`, `<script src>`, `<iframe>`, `<link>`), so any page the operator visits can
    still *trigger* a GET here, though it cannot read the opaque response. Blind cross-site
    triggering of a GET is inherent to browsers; the bound on it is that GET endpoints stay
    cheap and side-effect free, not this guard.
    """

    def __init__(self, app, bind_host: str = "127.0.0.1") -> None:
        self.app = app
        self.bind_host = bind_host

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers") or [])
            host = headers.get(b"host", b"")
            # Host is checked on every request, not just those carrying an Origin: a
            # rebound page's same-origin GETs, and its script/img loads, send no Origin
            # at all, so an Origin-gated check would wave them straight through.
            if not _host_allowed(host, self.bind_host):
                await self._deny(scope, send)
                return
            origin = headers.get(b"origin")
            if origin is not None and not _origin_matches_host(origin, host):
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
        """Drop records whose window and lockout have both expired (bounds the table).

        Expiry alone does not actually bound it: an attacker spraying from many source
        addresses keeps every record live, so nothing is ever eligible and the table grows
        past TOKEN_FAIL_TABLE_MAX regardless. When that happens, evict the oldest records
        as well - losing a lockout early is far better than unbounded memory, and the
        evicted client simply starts its window again.
        """
        expired = [
            host for host, (count, start, locked) in self._fails.items()
            if now - start > TOKEN_FAIL_WINDOW_S and now >= locked
        ]
        for host in expired:
            del self._fails[host]
        if len(self._fails) >= TOKEN_FAIL_TABLE_MAX:
            oldest = sorted(self._fails.items(), key=lambda kv: kv[1][1])
            for host, _rec in oldest[: len(self._fails) - TOKEN_FAIL_TABLE_MAX + 1]:
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


def _pin_static_mimetypes() -> None:
    """Force the Content-Type of the assets the UI is served from.

    StaticFiles takes its Content-Type from `mimetypes`, which on Windows reads
    HKEY_CLASSES_ROOT\\<ext>\\Content Type and lets a registry entry *override* the
    built-in table. index.html loads app.js as `type="module"`, and browsers enforce a
    strict MIME check on module scripts, so a machine where some installer set .js to
    text/plain served the whole UI as a blank page with only a console error.
    add_type(strict=True) wins over the registry entry.
    """
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/css", ".css")


def _mount_webui(app: FastAPI) -> None:
    """Serve the static web UI (SPEC 9.1) at /ui and redirect / to it."""
    _pin_static_mimetypes()
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


def _update_status(request: Request) -> dict | None:
    """The release-check block for /status, starting a check first if one is owed (see
    update_check)."""
    checker = request.app.state.update_checker
    checker.maybe_check()
    return checker.status()


def _same_path(a: str, b: str) -> bool:
    """Whether two path strings name the same file, by the rules of this filesystem.

    A plain string compare called `C:\\data\\capture.db`, `c:\\data\\capture.db` and
    `C:/data/capture.db` three different files, so retyping the same db path in the UI
    settings page reported restart_required for a daemon already on that file. normcase
    folds case and separators on Windows and is a no-op on POSIX.
    """
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


# Reserved Windows device names: unusable as a filename even with an extension, so a
# session called `com1` produced a download the browser could not save.
_WIN_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def _safe_download_stem(name: str) -> str:
    """Sanitize a session name into a filename stem safe on every supported OS."""
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    # Windows silently strips trailing dots and spaces, which can empty the name outright.
    stem = stem.rstrip(". ")
    if not stem:
        return "session"
    if stem.split(".")[0].upper() in _WIN_RESERVED:
        stem = "_" + stem
    return stem


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
            # The serving process itself. The pid file can name a launcher shim instead
            # (Windows venv launchers spawn the interpreter as a child), so this is the
            # pid `mcu daemon stop` must target when it has to fall back to a hard kill.
            "pid": os.getpid(),
            "uptime_s": time.time() - request.app.state.start_time,
            "db_path": resolve_db_path(cfg),
            "db_size_bytes": store.db_size_bytes(),
            # Both numbers, because db_size_bytes (file + WAL) is not the one the cap is
            # measured against, and reporting it alone beside db_max_bytes made a cap that
            # was enforcing correctly read as broken: 24 MB against a 2 MiB cap, while the
            # enforced figure sat at 2.0 MB the whole time. db_content_bytes excludes the
            # freelist, which is what the trim converges on.
            "db_content_bytes": store.content_bytes(),
            # The cap the store is enforcing, not the one config asked for: they are set
            # together today, but a health surface must report what is applied.
            "db_max_bytes": store.max_db_bytes(),
            "lines_trimmed": store.lines_trimmed,
            # Lines the capture was handed and could not store. Non-zero means received
            # lines were lost, which no other field on this response reveals.
            "write_errors": store.write_errors,
            # False means the single store writer has exited: nothing is being captured at
            # all, and every further write fails fast. No other field here moves for it.
            "writer_alive": store.writer_alive,
            # Rows shed from slow WebSocket subscribers over this daemon's life. Separate
            # from rx_dropped, which is the capture side: this one means a *client* missed
            # rows the capture holds, so the capture is intact and a re-fetch recovers them.
            "ws_dropped": store.ws_dropped,
            # Identity of the capture's id space (SPEC 3.4): a client compares it to decide
            # whether the ids it holds still name the same rows.
            "capture": store.capture_id,
            "session": store.active_session(),
            # Starts a release check if one is owed (SPEC 3.6); it runs detached, so this
            # response carries the previous answer, not this request's. That is why the
            # field is null on a fresh daemon's first status and populated on the next.
            "update": _update_status(request),
            # Running state of the UDP plot stream (SPEC 3.7), which the saved config
            # may disagree with: the runtime toggle does not write the file.
            "plotjuggler": {
                "enabled": request.app.state.pj.enabled,
                "dest": request.app.state.pj.dest,
            },
            "ports": [pt.status() for pt in ports.list()],
        }

    @app.post("/shutdown")
    async def shutdown(request: Request):
        # The graceful stop channel for `mcu daemon stop`. It exists because Windows has
        # no graceful signal that crosses console boundaries: os.kill maps everything
        # except the two console ctrl events onto TerminateProcess, and
        # GenerateConsoleCtrlEvent only reaches processes on the caller's console, which
        # a detached daemon never is. POSIX uses it too so both platforms stop the same
        # way, with SIGTERM as the fallback for an older daemon.
        client = request.client
        if client is None or client.host not in _LOOPBACK_CLIENTS:
            return JSONResponse(
                status_code=403,
                content={"error": "shutdown is a local operation; run mcu on the "
                         "daemon's machine"},
            )
        cb = request.app.state.shutdown_cb
        if cb is None:
            # No callback wired (tests, or the app embedded elsewhere): refusing beats
            # killing a process that only happens to host this app.
            return _bad_request("this server does not accept shutdown requests")
        # After the response is sent, not during the handler: uvicorn's graceful
        # shutdown would let the in-flight response finish anyway, but a short delay
        # makes the reply's delivery independent of that grace window.
        asyncio.get_running_loop().call_later(0.2, cb)
        return {"ok": True}

    @app.get("/ports")
    async def get_ports(request: Request) -> dict[str, Any]:
        return {"ports": [pt.status() for pt in _ports(request).list()]}

    @app.post("/ports")
    async def attach_port(request: Request, body: PortAttach):
        # Held to the config-write bar (SPEC 3.4): a device string can name a network
        # destination (socket://, rfc2217://), so a tokenless network client could
        # point the daemon's serial traffic at a host of its choosing.
        if denied := _config_write_denied(request):
            return denied
        if not body.device and not body.serial_number:
            return _bad_request("attach requires device or serial_number")
        try:
            pt = await _ports(request).attach(
                body.alias, body.device, body.baud, body.serial_number, eol=body.eol
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
            pt = await ports.attach(alias, pt.device, pt.baud, pt.serial_number,
                                    pt.identify, pt.eol)
        except PortError as exc:
            return _bad_request(str(exc))
        return {"port": pt.status()}

    @app.post("/ports/{alias}/disconnect")
    async def disconnect_port(request: Request, alias: str):
        # Close and stop retrying, but keep the attachment: reconnect above resumes it.
        ports = _ports(request)
        if not await ports.hold(alias):
            return _bad_request(f"no such port: {alias}")
        return {"port": ports.get(alias).status()}

    @app.get("/devices")
    async def devices() -> dict[str, Any]:
        # Off the loop: enumerating ports is a sysfs walk on Linux but a setupapi query on
        # Windows, where a machine carrying Bluetooth virtual COM ports takes far longer
        # than a request should ever hold the loop. Blocking here stalls every WebSocket
        # feed and every serial callback for the duration, so it goes to a thread and
        # shares serial_link's short scan cache with the reader threads.
        return {"devices": await asyncio.to_thread(_enumerate_devices)}

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
        # Off the loop with load_config: the config directory can be a network mount,
        # where one stat blocks for the mount's timeout (class 1).
        exists = await asyncio.to_thread(path.exists)
        running: Config = request.app.state.config
        restart_required = (
            saved.server.host != running.server.host
            or saved.server.port != running.server.port
            or not _same_path(resolve_db_path(saved), resolve_db_path(running))
        )
        return {
            "path": str(path),
            "exists": exists,
            "server": {"host": saved.server.host, "port": saved.server.port},
            "storage": {
                "db_path": saved.storage.db_path,
                "retention_days": saved.storage.retention_days,
                "max_db_bytes": saved.storage.max_db_bytes,
                "min_sessions": saved.storage.min_sessions,
                "auto_session": saved.storage.auto_session,
            },
            "update": {"check": saved.update.check},
            "plotjuggler": {
                "enabled": saved.plotjuggler.enabled,
                "dest": saved.plotjuggler.dest,
            },
            "ports": [
                {
                    "alias": pc.alias,
                    "device": pc.device,
                    "serial_number": pc.serial_number,
                    "baud": pc.baud,
                    "eol": pc.eol,
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
                    body.max_db_bytes, body.min_sessions, body.auto_session,
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
        running.storage.auto_session = body.auto_session
        # Turning it on mid-run starts covering the capture now rather than at the next
        # restart; turning it off leaves the current automatic session to close normally,
        # since ending it early would fragment the run for no benefit.
        if body.auto_session and store.active_session() is None:
            await store.start_session(auto_session_name(), auto=True)
        saved_view = Config(storage=StorageConfig(db_path=db_path))
        restart = not _same_path(resolve_db_path(saved_view), resolve_db_path(running))
        return {"ok": True, "restart_required": restart}

    @app.put("/config/update")
    async def put_config_update(request: Request, body: ConfigUpdateBody):
        if denied := _config_write_denied(request):
            return denied
        try:
            async with request.app.state.config_write_lock:
                await asyncio.to_thread(save_update, _cfg_path(request), body.check)
        except (ConfigError, OSError) as exc:
            return _save_error(exc)
        running: Config = request.app.state.config
        running.update.check = body.check
        # Applies live: turning it on checks on the cache's normal schedule (so enabling it
        # twice in a day still makes one request), turning it off stops the next request.
        request.app.state.update_checker.set_enabled(body.check)
        return {"ok": True, "restart_required": False}

    @app.put("/config/plotjuggler")
    async def put_config_plotjuggler(request: Request, body: ConfigPlotJugglerBody):
        if denied := _config_write_denied(request):
            return denied
        try:
            pjstream.parse_dest(body.dest)   # at least as strict as the loader (3.3.1)
        except ValueError as exc:
            return _bad_request(str(exc))
        try:
            async with request.app.state.config_write_lock:
                await asyncio.to_thread(
                    save_plotjuggler, _cfg_path(request), body.enabled, body.dest.strip()
                )
        except (ConfigError, OSError) as exc:
            return _save_error(exc)
        # Saves the file only: the running stream is PUT /plotjuggler's job (SPEC 3.7),
        # so "save as default" and "apply now" stay two deliberate acts.
        return {"ok": True, "restart_required": False}

    @app.get("/plotjuggler")
    async def get_plotjuggler(request: Request) -> dict[str, Any]:
        pj: pjstream.PlotJugglerStreamer = request.app.state.pj
        return {"enabled": pj.enabled, "dest": pj.dest}

    @app.put("/plotjuggler")
    async def put_plotjuggler(request: Request, body: PlotJugglerBody):
        # Held to the config-write bar (SPEC 3.7): this names the address capture data
        # is sent to, so a tokenless non-loopback client may not redirect it.
        if denied := _config_write_denied(request):
            return denied
        pj: pjstream.PlotJugglerStreamer = request.app.state.pj
        try:
            # The lock serializes concurrent toggles (configure is not thread-safe
            # against itself): without it two racing PUTs can pair one request's dest
            # with the other's resolved address, and every surface then reports a
            # destination the datagrams do not go to.
            async with request.app.state.config_write_lock:
                await asyncio.to_thread(pj.configure, body.enabled, body.dest)
        except (ValueError, OSError) as exc:
            # ValueError is a malformed dest; OSError a dest whose host will not resolve.
            # Both are this request's fault and leave the previous state in force.
            return _bad_request(str(exc))
        return {"enabled": pj.enabled, "dest": pj.dest}

    @app.put("/config/ports")
    async def put_config_ports(request: Request, body: ConfigPortsBody):
        if denied := _config_write_denied(request):
            return denied
        seen: set[str] = set()
        entries: list[PortConfig] = []
        # `identify` is config-file only (the settings dialog does not offer it), so a save
        # that omits it must not flip a hand-written `identify = false` back to the default.
        saved = await asyncio.to_thread(load_config, _cfg_path(request))
        saved_identify = {pc.alias: pc.identify for pc in saved.ports}
        saved_eol = {pc.alias: pc.eol for pc in saved.ports}
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
                    eol=(
                        entry.eol if entry.eol is not None
                        else saved_eol.get(entry.alias, PortConfig.eol)
                    ),
                    autoconnect=entry.autoconnect,
                    identify=(
                        entry.identify if entry.identify is not None
                        else saved_identify.get(entry.alias, True)
                    ),
                )
            )
        try:
            async with request.app.state.config_write_lock:
                await asyncio.to_thread(save_ports, _cfg_path(request), entries)
        except (ConfigError, OSError) as exc:
            return _save_error(exc)
        return {"ok": True, "restart_required": False}

    # -- sessions (named spans of the capture timeline) ---------------------------------

    def _session_range(request: Request, ref: str | None) -> SessionRange:
        """Resolve a `session=` query value into inclusive id bounds.

        An unknown reference yields a range that matches nothing rather than silently
        widening to the whole capture: a typo in a session name must not hand back every
        line ever stored as if it were that run.
        """
        return _session_range_for(_store(request), ref)

    def _upper_bound(session_end: int | None, id_to: int | None) -> int | None:
        """The effective inclusive upper line id: the tighter of a session's end and id_to.

        `id_to` is what a paused surface sends to fetch or export exactly what it shows.
        It is inclusive, where `since_id` is an exclusive cursor - a freeze is "up to and
        including what I show", a cursor is "after what I have" - and the asymmetry is
        documented in SPEC rather than smoothed away.
        """
        bounds = [b for b in (session_end, id_to) if b is not None]
        return min(bounds) if bounds else None

    @app.get("/sessions")
    async def list_sessions(
        request: Request,
        limit: int = Query(default=50, ge=0),  # noqa: B008
        name: str | None = None,
    ) -> dict[str, Any]:
        store = _store(request)
        if name is not None:
            # One indexed lookup (idx_sessions_name), so a client resolving a reference does
            # not have to page the list and hope the session is on the page it asked for.
            # `name` is a session ref, resolved like `session=` everywhere else: a numeric
            # id first, then the newest session of that name.
            session = store.resolve_session(name)
            sessions = []
            if session is not None:
                lo, hi = store.session_span(session)
                lines = await store.count_lines_safe(id_from=lo, id_to=hi)
                sessions = [dict(session, lines=lines)]
            return {"sessions": sessions, "active": store.active_session()}
        # Off the loop: the per-session count steps every id in that session's range, so the
        # cost follows the capture and `limit` reaches 1000.
        return {
            "sessions": await store.list_sessions_safe(limit),
            "active": store.active_session(),
        }

    @app.post("/sessions")
    async def start_session(request: Request, body: SessionBody):
        return {"session": await _store(request).start_session(body.name.strip(), body.note)}

    @app.post("/sessions/stop")
    async def stop_session(request: Request):
        # An automatic session is not something the caller started, so it is not theirs to
        # stop: it belongs to the daemon run and closes with it. Reporting "no session is
        # running" keeps `session start` / `session stop` a matched pair.
        store = _store(request)
        async with request.app.state.session_stop_lock:
            active = store.active_session()
            if active is not None and active["auto"]:
                return _bad_request("no session is running")
            # The verdict is stop_session's own result, not the read above it: two
            # concurrent stops both pass a pre-check, and the loser must not get a
            # success envelope carrying a null session.
            session = await store.stop_session()
            if session is None:
                return _bad_request("no session is running")
            if request.app.state.config.storage.auto_session:
                # Reopen an automatic session so the capture after the named run is still
                # covered by one, and the retention floor keeps protecting it.
                await store.start_session(auto_session_name(), auto=True)
        return {"session": session}

    @app.delete("/sessions/{session_id}")
    async def delete_session(
        request: Request,
        session_id: int = PathParam(ge=1, le=MAX_LINE_ID),  # noqa: B008
        data: bool = False,
    ):
        """Delete a session. `data=true` also deletes the lines it covers.

        The label and the capture are separable on purpose: forgetting a mislabelled run
        should not destroy what was recorded, and deleting a run's data is destructive
        enough to deserve saying so explicitly.
        """
        store = _store(request)
        # Look up by id only. resolve_session() falls back to a *name* match, so a request
        # for an id that does not exist could land on a session merely *named* that number
        # and then delete a different session's lines (the route deletes by the raw path
        # id, so the label was left behind pointing at the deleted range). The path param
        # is typed int, so the contract here is "address by id".
        session = store.get_session(session_id)
        if session is None:
            return _bad_request(f"no such session: {session_id}")
        deleted = 0
        if data:
            end_id = session["end_id"] if session["end_id"] is not None else store.max_id()
            deleted = await store.delete_range(session["start_id"], end_id)
        store.delete_session(session_id)
        if store.active_session() is None and request.app.state.config.storage.auto_session:
            # The deleted session was the running one. Reopen as POST /sessions/stop does, or
            # the run carries on with no active session and no retention floor protecting it.
            await store.start_session(auto_session_name(), auto=True)
        return {"ok": True, "lines_deleted": deleted}

    @app.get("/sessions/{ref}/export")
    async def export_session(request: Request, ref: str):
        """Download one session as a standalone capture database (SPEC 3.4).

        Built into a temp file beside the capture database on a worker thread, streamed,
        then removed. The copy is a normal capture file, so the archive of a run is
        queryable with the same tools as the live capture rather than being a dead format.

        Beside the capture, not in the system temp dir (SPEC 3.4): the copy is as large as
        the session, `/tmp` is RAM on many Linux installs, and a world-writable directory
        is the wrong place for a file this process is about to create and open.
        """
        store = _store(request)
        session = store.resolve_session(ref)
        if session is None:
            return _bad_request(f"no such session: {ref}")
        def build() -> str:
            # Every filesystem call on the worker thread, the temp file's included: the
            # capture directory can be a network mount (class 1).
            fd, tmp_path = tempfile.mkstemp(
                prefix="mcuscope-session-", suffix=".db", dir=_export_tmp_dir(request)
            )
            # The descriptor is closed but the file kept: sqlite3.connect opens a
            # zero-length file as an empty database, so nothing needs the name to be free,
            # and unlinking it first left a known unclaimed path for anything watching the
            # directory.
            os.close(fd)
            try:
                store.export_session_db(
                    tmp_path,
                    id_from=session["start_id"],
                    id_to=session["end_id"],
                    session=session,
                )
            except BaseException:
                with suppress(OSError):
                    os.unlink(tmp_path)
                raise
            return tmp_path

        try:
            tmp_path = await asyncio.to_thread(build)
        except Exception as exc:
            log.error("session export failed: %s", exc)
            return _bad_request(f"export failed: {exc}")
        safe = _safe_download_stem(session["name"])
        return _TempFileResponse(
            tmp_path,
            media_type="application/vnd.sqlite3",
            filename=f"{safe}.db",
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
            lo, hi = store.session_span(session)
        elif body.before_ts is not None:
            # "Older than T" with T in the future selects the whole capture, including the
            # running session, which is what `all` is for. A minute of slack covers clock
            # skew between a client and the daemon.
            if body.before_ts > time.time() + PURGE_FUTURE_SKEW_S:
                return _bad_request(
                    "before_ts is in the future; use all: true to delete the whole capture"
                )
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
            # Off the loop: this counts the whole selected range. count_lines drops a
            # bound that spans the capture itself, so the range goes across as selected.
            n = await store.count_lines_safe(id_from=lo, id_to=hi)
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
            await port.send_raw(body.line, body.eol)
        except PortError as exc:
            return _bad_request(str(exc))
        return {"ok": True}

    @app.post("/break")
    async def send_break(request: Request, body: BreakBody):
        try:
            port = _ports(request).resolve(body.port)
        except PortError as exc:
            return _bad_request(str(exc))
        try:
            await port.send_break(body.ms)
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
            return await port.send_command(body.cmd, body.timeout_ms, body.eol)
        except PortError as exc:
            return _bad_request(str(exc))

    @app.get("/lines")
    async def lines(
        request: Request,
        port: str | None = None,
        chan: list[Chan] | None = Query(default=None),  # noqa: B008 - FastAPI query param
        match: str | None = None,
        since_id: int | None = Query(default=None, le=MAX_LINE_ID),  # noqa: B008
        since_ts: float | None = None,
        last_ms: int | None = Query(default=None, le=MAX_MS),  # noqa: B008
        session: str | None = None,
        id_to: int | None = Query(default=None, ge=1, le=MAX_LINE_ID),  # noqa: B008
        limit: int = Query(default=100, ge=0),  # noqa: B008 - 0 is the no-backfill probe
        order: Literal["desc", "asc"] = "desc",
    ) -> dict[str, Any]:
        if match is not None and len(match) > MAX_MATCH_LEN:
            return _bad_request(f"match regex too long (max {MAX_MATCH_LEN} chars)")
        if match is not None:
            # Validate up front. Compiling lazily inside the SQLite REGEXP callback made a
            # bad pattern surface as an opaque 500, unlike /wait which already says 400.
            try:
                regex.compile(match)
            except regex.error as exc:
                return _bad_request(f"bad match regex: {exc}")
        span = _session_range(request, session)
        id_from, id_to = span.id_from, _upper_bound(span.id_to, id_to)
        try:
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
        except MatchBudgetExceeded as exc:
            return _bad_request(str(exc))
        return {"lines": rows, "truncated": truncated}

    @app.get("/can/frames")
    async def can_frames(
        request: Request,
        port: str | None = None,
        bus: int | None = Query(default=None, ge=p.CAN_BUS_MIN, le=p.CAN_BUS_MAX),  # noqa: B008
        id: str | None = None,
        last_ms: int | None = Query(default=None, le=MAX_MS),  # noqa: B008
        since_id: int | None = Query(default=None, le=MAX_LINE_ID),  # noqa: B008
        session: str | None = None,
        id_to: int | None = Query(default=None, ge=1, le=MAX_LINE_ID),  # noqa: B008
        limit: int = Query(default=100, ge=0),  # noqa: B008 - 0 is the no-backfill probe
    ):
        can_id = None
        if id is not None:
            try:
                can_id = p.parse_hex_int(id)
            except p.ProtocolError:
                return _bad_request(f"bad can id: {id}")
            if can_id > p.CAN_ID_MAX_EXT:
                return _bad_request(f"can id out of range: {id}")
        span = _session_range(request, session)
        id_from, id_to = span.id_from, _upper_bound(span.id_to, id_to)
        rows, truncated = await _store(request).query_can_frames_safe(
            port=port, bus=bus, can_id=can_id, last_ms=last_ms, since_id=since_id,
            id_from=id_from, id_to=id_to, limit=limit,
        )
        return {"frames": rows, "truncated": truncated}

    @app.get("/plot/channels")
    async def plot_channels(request: Request, port: str | None = None) -> dict[str, Any]:
        store = _store(request)
        meta = _ports(request).plot_channel_meta()
        out = []
        # `port` narrows to one board. Channel names are unique only within a port, so
        # two boards declaring "temp" otherwise merge into one channel carrying both
        # boards' samples under whichever unit was declared last (SPEC 9.2).
        for ch in await store.query_plot_channels_safe(port=port):
            m = meta.get(ch["name"], {})
            out.append(
                {
                    "name": ch["name"],
                    "port": ch.get("port"),
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
        port: str | None = None,
        last_ms: int | None = Query(default=None, le=MAX_MS),  # noqa: B008
        since_id: int | None = Query(default=None, le=MAX_LINE_ID),  # noqa: B008
        session: str | None = None,
        id_to: int | None = Query(default=None, ge=1, le=MAX_LINE_ID),  # noqa: B008
        limit: int = Query(default=10000, ge=0),  # noqa: B008
        decimate: int = Query(default=1, le=MAX_DECIMATE),  # noqa: B008
    ) -> dict[str, Any]:
        span = _session_range(request, session)
        id_from, id_to = span.id_from, _upper_bound(span.id_to, id_to)
        points = await _store(request).query_plot_series_safe(
            name=name, port=port, last_ms=last_ms, since_id=since_id,
            id_from=id_from, id_to=id_to, limit=limit, decimate=decimate,
        )
        return {"name": name, "port": port, "points": points}

    @app.get("/plot/export")
    async def plot_export(
        request: Request,
        names: str,
        last_ms: int | None = Query(default=None, le=MAX_MS),  # noqa: B008
        session: str | None = None,
        id_to: int | None = Query(default=None, ge=1, le=MAX_LINE_ID),  # noqa: B008
        format: str = "long",
        port: str | None = None,
    ):
        # `port` scopes to one board: channel names are unique only within a port (SPEC
        # 9.2), so two boards declaring the same name otherwise interleave in one column.
        name_list = [n for n in names.split(",") if n]
        if not name_list:
            return _bad_request("names is required")
        if format not in ("long", "wide"):
            return _bad_request("format must be 'long' or 'wide'")
        store = _store(request)
        span = _session_range(request, session)
        id_from, id_to = span.id_from, _upper_bound(span.id_to, id_to)
        if id_to is None:
            # One window for all three store calls below: the capture keeps growing, so
            # the count would guard a smaller set than the CSV then streams.
            id_to = store.max_id()
        if format == "wide":
            sids = await store.export_sids_safe(
                names=name_list, last_ms=last_ms, id_from=id_from, id_to=id_to, port=port
            )
            if len(sids) > 1:
                return _bad_request("wide export requires all channels to share one stream")
        # Refuse an over-large selection rather than truncating it: the response streams,
        # so by the time the row cap bites the headers are long gone and a short CSV is
        # byte-indistinguishable from a complete one.
        n = await store.count_plot_export_safe(
            names=name_list, last_ms=last_ms, id_from=id_from, id_to=id_to, port=port
        )
        if n == 0:
            # An empty selection is either a mistyped channel or a window with no points,
            # and a 26-byte header-only CSV at exit 0 cannot tell them apart. Refuse only
            # when *no* requested name exists at all: one dead name among several must
            # still export the others. Checked here alone, so the scan costs nothing on
            # any path that selected rows.
            known = {ch["name"] for ch in await store.query_plot_channels_safe(port=port)}
            unknown = [n_ for n_ in name_list if n_ not in known]
            if len(unknown) == len(name_list):
                return _bad_request(
                    "no such plot channel: " + ", ".join(unknown) + "; see /plot/channels"
                )
        if n > MAX_EXPORT_ROWS:
            return _bad_request(
                f"selection is {n} rows, over the {MAX_EXPORT_ROWS} export limit; "
                "narrow it with session, last_ms or id_to"
            )
        # open_plot_export, not iter_plot_export: an in-memory capture has no private read
        # connection, so its generator must be drained on the loop (see store.py).
        rows = await store.open_plot_export(
            names=name_list, last_ms=last_ms, id_from=id_from, id_to=id_to, port=port
        )
        stream = _csv_wide(rows, name_list) if format == "wide" else _csv_long(rows)
        return StreamingResponse(
            stream,
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="plot.csv"'},
        )

    @app.post("/wait")
    async def wait(request: Request, body: WaitBody):
        # 400, not 408 or 500: the fault is the submitted pattern, and the CLI must map it
        # to exit 1. Exit 2 already means "pattern valid, nothing matched in the window",
        # and conflating a killed pattern with a real timeout would corrupt scripted flows.
        try:
            return await _do_wait(request, body)
        except MatchBudgetExceeded as exc:
            return _bad_request(str(exc))

    @app.post("/assert")
    async def assert_(request: Request, body: AssertBody):
        try:
            return await _do_assert(request, body)
        except MatchBudgetExceeded as exc:
            return _bad_request(str(exc))

    @app.post("/marker")
    async def marker(request: Request, body: MarkerBody):
        # /marker is the only path whose `port` reaches store.add_line without passing
        # through PortManager.resolve(), so the alias grammar is enforced here instead.
        # Unchecked, the max_length on `text` is defeated through the field beside it: the
        # port is stored verbatim on the row, so a 100k-char or control-byte port writes
        # exactly the garbage into the capture that bounding `text` exists to keep out.
        # Empty or omitted stays legal, a marker need not name a port (SPEC 3.5).
        if body.port and not re.fullmatch(_ALIAS_RE, body.port):
            return _bad_request(f"invalid port: {body.port[:64]!r}")
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
        if port is not None and websocket.app.state.ports.get(port) is None:
            # Live-only surface: no row can ever carry an unattached alias, so the client
            # would sit on a healthy socket forever. The read endpoints are exempt - a
            # detached port's lines are still in the capture.
            await websocket.close(code=1008, reason=f"no such port: {port}")
            return
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
            sent_capture: str | None = None
            while True:
                rows: list[Any] = []
                try:
                    rows.append(await asyncio.wait_for(q.get(), timeout=WS_KEEPALIVE_S))
                except asyncio.TimeoutError:  # not builtin TimeoutError on 3.10
                    # Idle keepalive (see WS_KEEPALIVE_S). An empty array is a well-formed
                    # frame under SPEC 3.4 - every client already loops over the rows - so
                    # no client needs to know this is a probe, and a vanished peer surfaces
                    # here as a failing send rather than never.
                    pass
                else:
                    # Coalesce whatever else is already queued into one frame (SPEC 3.4:
                    # each message is an array). A frame - and a json.dumps, and a TCP
                    # write - per row is what an attached subscriber costs at high line
                    # rates; every client renders on a timer anyway, so the coalescing is
                    # free on their side.
                    while len(rows) < WS_BATCH_MAX:
                        try:
                            rows.append(q.get_nowait())
                        except asyncio.QueueEmpty:
                            break
                    # A gap object at the head of the frame if rows were shed for this
                    # subscriber since the last one. In-band because an id gap cannot be
                    # inferred: `port=` filtering makes gaps legitimate. Clients that do not
                    # know the object skip it, having no "id", which is what they already do
                    # with anything unrecognised.
                    dropped = store.take_dropped(q)
                    if dropped:
                        rows.insert(0, {"gap": dropped})
                # The capture identity, ahead of everything else in the frame: on the first
                # frame so a client can compare it against what it held before the socket
                # opened, and again whenever it changes under a live connection (a purge
                # that took the highest id). A client reads a change as "the ids you hold
                # name nothing now" and re-seeds. Keepalives carry it too, so a silent
                # target still tells a reconnected client within WS_KEEPALIVE_S.
                if store.capture_id != sent_capture:
                    sent_capture = store.capture_id
                    rows.insert(0, {"capture": sent_capture})
                await websocket.send_text(json.dumps(rows, separators=(",", ":")))

        async def watch() -> None:
            try:
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
            except (WebSocketDisconnect, RuntimeError):
                return

        pump_task = asyncio.create_task(pump())
        watch_task = asyncio.create_task(watch())
        try:
            # FIRST_COMPLETED: either half ending ends the connection. Waiting on the
            # receive loop alone left a socket that had lost its pump open and looking
            # healthy while delivering nothing for the rest of its life.
            await asyncio.wait({pump_task, watch_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (pump_task, watch_task):
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
            store.unsubscribe(q)
            with suppress(Exception):
                await websocket.close()


def _match_timeout(deadline: float) -> float:
    """Timeout for one search() call: the per-call ceiling, or what is left of the budget.

    A per-call ceiling alone is unbounded across millions of rows; a whole-query budget
    alone lets a single row spend all of it. Both are needed.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise MatchBudgetExceeded(
            "match pattern exceeded the matching time budget; simplify the regex"
        )
    return min(MATCH_TIMEOUT_S, remaining)


def _search_batch(pattern, texts: list[str]) -> int | None:
    """Return the index of the first text matching `pattern`, or None.

    Called off the event loop so a slow pattern cannot stall it; batched so a burst of
    lines costs one executor hop, not one per row (per-row hops fall behind at high
    line rates, the subscriber queue then drops oldest, and a real match can be lost).

    The deadline is computed here rather than passed in so the signature stays (pattern,
    texts). Raises MatchBudgetExceeded rather than skipping the batch: silently dropping
    lines a hostile pattern was too slow to test would fabricate a "no match" verdict.
    """
    deadline = time.monotonic() + MATCH_BUDGET_S
    for i, text in enumerate(texts):
        try:
            hit = pattern.search(text, timeout=_match_timeout(deadline))
        except TimeoutError:
            # regex raises the builtin TimeoutError when a single search is interrupted.
            # Translate it here, or it escapes the executor and becomes an opaque 500.
            raise MatchBudgetExceeded(
                "match pattern exceeded the matching time budget; simplify the regex"
            ) from None
        if hit is not None:
            return i
    return None


class CaptureWatch:
    """A live view of the rows committed after the watch opened, for /wait and /assert.

    Both endpoints watch the same feed and differ only in the verdict they reach, so the
    ordering rules that make the watch correct live here rather than in each handler:
    the watermark is read before subscribing, a burst is drained past the deadline, the
    shed-row count follows the subscriber, and the subscription is always released.
    """

    def __init__(
        self,
        store: Store,
        *,
        port: str | None = None,
        chan: str | None = None,
        maxsize: int = 2000,
    ) -> None:
        self._store = store
        self._port = port
        self._chan = chan
        self._maxsize = maxsize   # only the drop-accounting tests pass a small one
        self._q: asyncio.Queue[dict[str, Any]] | None = None
        self._start_id = 0
        self._dropped = 0

    def open(self) -> None:
        """Subscribe. Raises StoreError, which both callers answer with a 503."""
        # Read the watermark BEFORE subscribing: subscribe can only enqueue newer ids, so a
        # line committed between the two calls is still delivered. The other order could
        # enqueue a row and then read a max_id that already covers it, dropping a real match.
        self._start_id = self._store.max_id()
        self._q = self._store.subscribe(self._port, maxsize=self._maxsize)

    def close(self) -> None:
        if self._q is not None:
            self._store.unsubscribe(self._q)
            self._q = None

    def dropped_total(self) -> int:
        """Rows the feed shed for this subscriber, collecting anything shed since the last take.

        A scan is an await, so the writer keeps broadcasting during it and a burst past the
        queue can drop the very line being watched for. Reporting the count is what stops a
        hole in the window from reading as a clean "no match" or a clean "pass" (class 12).

        One property, not two: the earlier pair had a bare `dropped_so_far` reading only what
        the last batch had taken, and `/wait`'s match return used it, so rows shed during the
        scan that found the match went unreported.
        """
        if self._q is not None:
            self._dropped += self._store.take_dropped(self._q)
        return self._dropped

    async def next_batch(self, remaining: float) -> list[dict[str, Any]] | None:
        """One wake-up's worth of candidate rows, or None if nothing arrived at all.

        Waits up to `remaining` seconds for a first row, then drains whatever else is
        already queued so a whole burst costs one executor hop. **The drain runs even when
        `remaining <= 0`**: `send` is given the same timeout as the whole window, so a
        command that consumes all of it leaves the deadline expired with a match already
        sitting in the queue, and answering "timeout" there is exit 2 on a run that
        actually matched.

        An empty list is not None: rows arrived but none cleared the watermark and channel
        filter, so the window is still live and the caller keeps waiting.
        """
        q = self._q
        if q is None:
            raise RuntimeError("CaptureWatch.next_batch before open()")
        rows: list[dict[str, Any]] = []
        if remaining > 0:
            with suppress(asyncio.TimeoutError):  # not builtin TimeoutError on 3.10
                rows.append(await asyncio.wait_for(q.get(), timeout=remaining))
        while True:
            try:
                rows.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
        self._dropped += self._store.take_dropped(q)
        if not rows:
            return None
        return [
            r for r in rows
            if r["id"] > self._start_id and (self._chan is None or r["chan"] == self._chan)
        ]


class _SendTally:
    """Writes attempted by one /wait, counted for the response.

    Mutable and shared with the repeat task, which is the only writer; the handler reads
    it after cancelling that task, or (on the match path) while it is still running, where
    a slightly stale count is preferable to holding a lock across the match scan.
    """

    __slots__ = ("sends", "failures")

    def __init__(self) -> None:
        self.sends = 0
        self.failures = 0


async def _repeat_send(
    ports: PortManager, alias: str, line: str, period_s: float, tally: _SendTally,
    eol: str | None = None,
) -> None:
    """Write `line` now, then every `period_s`, until cancelled.

    A failed write is counted, not fatal: the caller is typically racing a bootloader's
    autoboot window and starts the wait *before* powering the target, so "port is not
    connected" is the expected state for the first few ticks (SPEC 3.4).

    Only the first write that succeeds is stored as a tx row: at 20 Hz for 30 s the rest
    would bury the capture the wait exists to read.

    The port is looked up by alias every tick, not captured once: an attach (which is what
    `POST /ports/{alias}/reconnect` performs) replaces the SerialPort object, and a
    captured one would go on failing against a dead handle for the rest of the window.
    """
    loop = asyncio.get_running_loop()
    next_at = loop.time()
    while True:
        port = ports.get(alias)
        try:
            if port is None:   # detached mid-wait
                raise PortError(f"no such port: {alias}")
            await port.send_raw(line, eol, log=tally.sends == 0)
        except Exception as exc:
            # Not PortError alone: send_raw reaches store.add_line, whose StoreError is a
            # plain RuntimeError. A write that fails is this tick's failure, not the loop's
            # end. Logged once per task, so a dead writer does not fill the log at 100 Hz.
            if tally.failures == 0:
                log.warning("repeat send on %s failed: %s", alias, exc)
            tally.failures += 1
        else:
            tally.sends += 1
        # Re-anchor rather than backfill (class 36): a write that blocked out several
        # periods must not be followed by that many writes back to back.
        next_at = max(next_at + period_s, loop.time())
        await asyncio.sleep(next_at - loop.time())


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
    if body.eol is not None and body.send is None:
        return _bad_request("eol applies to send; set send too")
    if body.repeat_ms is not None:
        refusal = p.repeat_refusal(
            body.repeat_ms, body.timeout_ms,
            has_send=body.send is not None, raw=body.send_mode == "raw",
        )
        if refusal is not None:
            return _bad_request(refusal)
    if len(body.match) > MAX_MATCH_LEN:
        return _bad_request(f"match regex too long (max {MAX_MATCH_LEN} chars)")
    try:
        pattern = regex.compile(body.match)
    except regex.error as exc:
        return _bad_request(f"bad match regex: {exc}")

    watch = CaptureWatch(store, port=port_filter, chan=body.chan)
    try:
        watch.open()
    except StoreError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    started = loop.time()
    repeater: asyncio.Task | None = None
    tally = _SendTally()
    try:
        cmd_result = None
        if body.send is not None and port_obj is not None:
            if body.repeat_ms is not None:
                # A body the wire encoder refuses (embedded newline, non-ASCII, too long)
                # is a 400 on the non-repeat path, and must not become a full-window
                # timeout with every tick counted as a failed write.
                try:
                    port_obj._encode_wire(body.send, body.eol or port_obj.eol)
                except PortError as exc:
                    return _bad_request(str(exc))
                # Off the handler: a write that blocks for the port's whole write timeout
                # must not delay the match this call exists to catch.
                repeater = asyncio.create_task(
                    _repeat_send(
                        ports, port_obj.alias, body.send, body.repeat_ms / 1000.0, tally,
                        body.eol,
                    )
                )
            else:
                try:
                    if body.send_mode == "raw":
                        await port_obj.send_raw(body.send, body.eol)
                    else:
                        cmd_result = await port_obj.send_command(
                            body.send, body.timeout_ms, body.eol
                        )
                except PortError as exc:
                    return _bad_request(str(exc))
                tally.sends = 1

        deadline = started + body.timeout_ms / 1000.0
        while True:
            remaining = deadline - loop.time()
            candidates = await watch.next_batch(remaining)
            if candidates:
                idx = await loop.run_in_executor(
                    match_executor(), _search_batch, pattern, [r["raw"] for r in candidates]
                )
                if idx is not None:
                    return {
                        "status": "match",
                        "line": candidates[idx],
                        "waited_ms": (loop.time() - started) * 1000.0,
                        "cmd_result": cmd_result,
                        "dropped": watch.dropped_total(),
                        "sends": tally.sends,
                        "send_failures": tally.failures,
                    }
            # Window spent, or the blocking get timed out with nothing to show for it.
            if remaining <= 0 or candidates is None:
                break
        return {
            "status": "timeout",
            "line": None,
            "waited_ms": (loop.time() - started) * 1000.0,
            "cmd_result": cmd_result,
            "dropped": watch.dropped_total(),
            "sends": tally.sends,
            "send_failures": tally.failures,
        }
    finally:
        # Consumed on every exit, the exceptional ones included (class 39): a client
        # disconnect cancels this handler, and a repeater left running would keep writing
        # to the port long after the response it belonged to.
        try:
            if repeater is not None:
                repeater.cancel()
                # A repeater that died of anything but cancellation must not re-raise over
                # the response this handler already built, nor skip the close below.
                with suppress(Exception, asyncio.CancelledError):
                    await repeater
        finally:
            watch.close()


def _unlink_later(path: str) -> None:
    """Remove a streamed temp file once the response is done with it."""
    with suppress(OSError):
        os.unlink(path)


def _export_tmp_dir(request: Request) -> str | None:
    """Directory for an export's temp copy: the one holding the capture database.

    None (the system temp dir) only when that directory does not exist, which in practice
    means an in-memory capture.
    """
    db_path = resolve_db_path(request.app.state.config)
    if db_path in (":memory:", ""):
        return None
    parent = Path(db_path).parent
    return str(parent) if parent.is_dir() else None


class _TempFileResponse(FileResponse):
    """FileResponse that removes its file when the response ends, sent or not.

    A `BackgroundTask` runs only after the body has been sent, so a client disconnecting
    mid-download (an ordinary cancelled browser download) left the copy on disk forever.

    Load-bearing assumption (class 24): the ASGI server must not implement the
    `http.response.pathsend` extension, or starlette hands it the path and returns
    before the file is sent, and the finally would delete it first. uvicorn does not
    implement pathsend; re-check on any server swap.
    On Windows an unlink can lose to a still-open handle (suppressed OSError) and the
    copy then sits beside the capture until the next export; owed to the Windows leg.
    """

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            _unlink_later(str(self.path))


def _scan_batch(patterns: list[Any], texts: list[str]) -> list[tuple[int, int]]:
    """Every (pattern index, text index) first hit in this batch, evaluated off the loop.

    One executor hop per burst per direction, like `_search_batch`: an assertion carries
    several patterns, and a hop per pattern per line would fall behind a fast capture and
    lose rows to the subscriber queue's drop-oldest.
    """
    hits: list[tuple[int, int]] = []
    deadline = time.monotonic() + MATCH_BUDGET_S   # one budget across all patterns x texts
    for pi, pattern in enumerate(patterns):
        for ti, text in enumerate(texts):
            try:
                hit = pattern.search(text, timeout=_match_timeout(deadline))
            except TimeoutError:
                raise MatchBudgetExceeded(
                    "match pattern exceeded the matching time budget; simplify the regex"
                ) from None
            if hit is not None:
                hits.append((pi, ti))
                break
    return hits


def _compile_patterns(patterns: list[str]) -> tuple[list[Any] | None, str]:
    out = []
    for pat in patterns:
        if len(pat) > MAX_MATCH_LEN:
            return None, f"regex too long (max {MAX_MATCH_LEN} chars): {pat[:40]}..."
        try:
            out.append(regex.compile(pat))
        except regex.error as exc:
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
    exactly the span the expectations needed. `min_window_ms` decouples the two: it holds
    the window open for a stated span even after every expectation is met, which is what
    "boot, then watch for errors for ten more seconds" needs - without it the forbid
    verdict silently covers only the two seconds the boot happened to take. Any forbidden
    match still fails immediately: there is no reason to keep waiting once the verdict is
    decided.
    """
    store = _store(request)
    if not body.expect and not body.forbid:
        return _bad_request("at least one expect or forbid pattern is required")
    # SPEC 3.4 bounds the total per call, not each list: `max_length` on the two fields
    # separately let 16 + 16 through, and each pattern costs a query or a scan.
    if len(body.expect) + len(body.forbid) > MAX_ASSERT_PATTERNS:
        return _bad_request(
            f"at most {MAX_ASSERT_PATTERNS} expect and forbid patterns in total"
        )
    if body.min_window_ms:
        if body.timeout_ms == 0:
            return _bad_request("min_window_ms needs a live window (set timeout_ms too)")
        if body.min_window_ms > body.timeout_ms:
            return _bad_request("min_window_ms cannot exceed timeout_ms")
    # The mirror of the guard above, in both directions: a field that only one of the two
    # modes reads is refused by the other, or the scope judged is not the scope asked for and
    # the verdict still reads authoritative.
    if body.timeout_ms > 0:
        if body.session is not None:
            return _bad_request("session needs a retrospective window (leave timeout_ms at 0)")
        if body.last_ms is not None:
            return _bad_request("last_ms needs a retrospective window (leave timeout_ms at 0)")
    elif body.send is not None:
        # Only the live branch sends, so a retrospective assert was quietly judging a board
        # that had never been given the command it was being judged on.
        return _bad_request("send needs a live window (set timeout_ms too)")
    if body.eol is not None and body.send is None:
        return _bad_request("eol applies to send; set send too")
    expect_pats, err_msg = _compile_patterns(body.expect)
    if expect_pats is None:
        return _bad_request(err_msg)
    forbid_pats, err_msg = _compile_patterns(body.forbid)
    if forbid_pats is None:
        return _bad_request(err_msg)

    expect_hits: list[dict[str, Any] | None] = [None] * len(body.expect)
    forbid_hits: list[dict[str, Any] | None] = [None] * len(body.forbid)

    def verdict(checked: int, elapsed_ms: float, dropped: int = 0) -> dict[str, Any]:
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
            # Rows the feed shed while a scan ran: a forbid that "did not match" over a
            # window with holes in it has not been judged over that window (class 12).
            "dropped": dropped,
        }

    if body.timeout_ms == 0:
        # Retrospective: one bounded query per pattern rather than pulling the window into
        # memory and scanning it here. Each is `raw REGEXP ?` over an id range, offloaded
        # by query_lines_safe, and stops at the first hit.
        span = _session_range_for(store, body.session)
        if span.unknown:
            return _bad_request(f"no such session: {body.session}")
        id_from, id_to = span.id_from, span.id_to
        if body.last_ms is not None and id_to is None:
            # One window for every pattern and the count: each query would otherwise
            # re-anchor at its own now, and the verdict spans them all.
            id_to = store.max_id()
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
        # Off the loop like the match queries above: this is the default invocation of
        # `mcu assert`, and counting how many lines were looked at must not undo the
        # containment that looking at them was given.
        checked = await store.count_lines_safe(
            port=body.port, chans=scope["chans"], id_from=id_from, id_to=id_to,
            last_ms=body.last_ms,
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
    watch = CaptureWatch(store, port=port_filter, chan=body.chan)
    try:
        watch.open()
    except StoreError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    started = loop.time()
    checked = 0
    try:
        if body.send is not None and port_obj is not None:
            try:
                if body.send_mode == "raw":
                    await port_obj.send_raw(body.send, body.eol)
                else:
                    await port_obj.send_command(body.send, body.timeout_ms, body.eol)
            except PortError as exc:
                return _bad_request(str(exc))

        deadline = started + body.timeout_ms / 1000.0
        min_deadline = started + body.min_window_ms / 1000.0
        while True:
            now = loop.time()
            # Every expectation met: the window has served its purpose, unless a minimum
            # was asked for and has not elapsed - then keep watching, which is the whole
            # point of min_window_ms (the forbids are still being judged).
            expects_met = bool(body.expect) and all(h is not None for h in expect_hits)
            if expects_met and now >= min_deadline:
                break
            # Wake at the minimum's end rather than the timeout's, so a quiet window that
            # has already satisfied its expectations returns then instead of hanging on
            # for the full timeout waiting for a row that may never come.
            remaining = (min(deadline, min_deadline) if expects_met else deadline) - now
            # Judge the batch even once the window is spent: `send` gets the same timeout
            # as the whole window, so a command that consumes it leaves a match already
            # queued. One watch for both loops, because only /wait had this.
            candidates = await watch.next_batch(remaining)
            if candidates:
                checked += len(candidates)
                texts = [r["raw"] for r in candidates]
                if forbid_pats:
                    hits = await loop.run_in_executor(
                        match_executor(), _scan_batch, forbid_pats, texts
                    )
                    for pi, ti in hits:
                        if forbid_hits[pi] is None:
                            forbid_hits[pi] = candidates[ti]
                    if any(h is not None for h in forbid_hits):
                        break   # the verdict is decided; waiting longer cannot change it
                pending = [i for i, h in enumerate(expect_hits) if h is None]
                if pending:
                    hits = await loop.run_in_executor(
                        match_executor(), _scan_batch, [expect_pats[i] for i in pending], texts
                    )
                    for pi, ti in hits:
                        expect_hits[pending[pi]] = candidates[ti]
            # One post-deadline drain has now happened, so stop. Inside the window a None
            # batch is the minimum elapsing rather than the end, so re-evaluate instead.
            if remaining <= 0:
                break
        return verdict(checked, (loop.time() - started) * 1000.0, watch.dropped_total())
    finally:
        watch.close()


class SessionRange(NamedTuple):
    """Inclusive id bounds for a `session=` reference, and whether it resolved at all.

    An unresolved ref carries the empty range (1, 0), so consumers that use the bounds
    directly (/lines family) match nothing instead of widening to the whole capture;
    consumers with a stricter contract (/assert) check `unknown` and refuse with a 400.
    """

    id_from: int | None
    id_to: int | None
    unknown: bool = False


_NO_SESSION = SessionRange(None, None)
# An unknown ref matches nothing rather than silently widening to the whole capture: a
# typo in a session name must not hand back every line ever stored as if it were that run.
_UNKNOWN_SESSION = SessionRange(1, 0, unknown=True)


def _session_range_for(store: Store, ref: str | None) -> SessionRange:
    """`_session_range` without a Request."""
    if ref is None:
        return _NO_SESSION
    session = store.resolve_session(ref)
    if session is None:
        return _UNKNOWN_SESSION
    return SessionRange(session["start_id"], session["end_id"])


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


def _enumerate_devices() -> list[dict[str, Any]]:
    """The /devices payload. Blocking: call from a worker thread."""
    by_id = _by_id_map()
    out = []
    for info in cached_comports():
        vid_pid = None
        if info.vid is not None and info.pid is not None:
            vid_pid = f"{info.vid:04X}:{info.pid:04X}"
        out.append(
            {
                "device": info.device,
                # Only resolve when there is a map to look up in. Off Linux there never
                # is, and realpath("COM7") is a pointless filesystem round trip that
                # answers with the port name glued onto the current directory.
                "by_id": by_id.get(os.path.realpath(info.device)) if by_id else None,
                "description": info.description or "",
                "vid_pid": vid_pid,
                "serial_number": info.serial_number,
            }
        )
    return out


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
