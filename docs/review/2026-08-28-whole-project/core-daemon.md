# Review round 2, module-reading leg: core daemon

HEAD checked: `fd76735 POST /ports held to the config-write bar` (as expected).

Modules read end to end: `host/mcuscope/server.py`, `host/mcuscope/store.py`, `host/mcuscope/serial_link.py`, `host/mcuscope/pjstream.py`, `host/mcuscope/_stdio.py`.
Read against `docs/SPEC.md` sections 2 (wire protocol), 3.1-3.7 (REST/WS API, storage, retention), 9.2, and the cross-platform mandate in `CLAUDE.md`.
Probe scripts were written to `/tmp` only; nothing in the repo was touched.

Counts: 2 MED, 1 MED-LOW, 4 LOW. No HIGH.

---

## 1. MED (CONFIRMED). An out-of-range integer query parameter is a 500 with a logged traceback, on seven endpoints

**Files:** `server.py:1332-1372` (`/lines`), `1374-1399` (`/can/frames`), `1431-1449` (`/plot/series`), `1451-1495` (`/plot/export`), `1263-1307` (`/purge`); lands in `store.py:1257` (`_window_floor`), `store.py:1217` (`_delete_lines`) and every `conn.execute` that binds one of these values.

**Invariant broken.** The codebase's own stated rule is that caller input is a 400, not a 500: `serial_link.py:958-963` records exactly this defect being fixed for `format_command` ("the caller got a 500 for its own bad input, and the daemon log got a traceback for a routine typo"). `server.py:1346-1354` applies the rule to `match=` on `/lines`. The integer parameters were left unbounded.

`limit` is clamped everywhere (SPEC 628-635 requires clamping, and the code matches, including `truncated` staying true at `limit=0`). `timeout_ms`, `min_window_ms`, `baud`, `id_from`/`id_to` on `PurgeBody` all carry pydantic bounds. `last_ms`, `since_id`, `decimate` and `PurgeBody.id_to` carry none, so a Python arbitrary-precision int reaches either `last_ms / 1000.0` (OverflowError: int too large to convert to float) or a SQLite bind (OverflowError: Python int too large to convert to SQLite INTEGER).

**Probe** (`/tmp/probe_ints.py`, `uv run python` from `host/`, TestClient with `raise_server_exceptions=False`, `BIG = 10**400`):

```
GET  /lines?last_ms=BIG            -> 500 {"error":"int too large to convert to float"}
GET  /lines?since_id=BIG           -> 500 {"error":"Python int too large to convert to SQLite INTEGER"}
GET  /can/frames?since_id=BIG      -> 500 {"error":"Python int too large to convert to SQLite INTEGER"}
GET  /plot/series?name=x&decimate=BIG -> 500 {"error":"Python int too large to convert to SQLite INTEGER"}
GET  /plot/series?name=x&last_ms=BIG  -> 500 {"error":"int too large to convert to float"}
GET  /plot/export?names=x&last_ms=BIG -> 500 {"error":"int too large to convert to float"}
POST /purge {"id_from":1,"id_to":BIG} -> 500 {"error":"Python int too large to convert to SQLite INTEGER"}
GET  /lines?limit=0                -> 200 {"lines":[],"truncated":true}   (SPEC 633-635: correct)
GET  /lines?since_ts=1e400         -> 200 {"lines":[],"truncated":false}  (inf, harmless)
```

**Failure scenario.** Each one routes through `@app.exception_handler(Exception)` at `server.py:423-428`, which calls `log.exception(...)`. So a single repeated GET writes an unbounded stream of full tracebacks into the daemon log for input the daemon should have refused in one line. Reachable without a token from loopback, and reachable cross-site from any page the operator visits (see finding 4: a no-Origin `<img src="http://127.0.0.1:8765/lines?last_ms=1e400...">` passes both guards). The JSON envelope contract of SPEC 3.4 is technically kept, but an agent reading `{"error":"int too large to convert to float"}` cannot tell which field it got wrong.

`/purge` is the worst of the set because it is a destructive endpoint: the caller gets a 500 and cannot tell from the response whether the chunked delete had already removed anything. (In the probe the first chunk raised before deleting, but that is timing, not a guarantee: `delete_range` at `store.py:1985-1991` accumulates `total` across chunks and the raise discards it.)

**Suggested shape of fix:** pydantic bounds on the query params, the same way `PurgeBody.id_from` already has `ge=1`. One `le=` per field, no new machinery.

---

## 2. MED (CONFIRMED mechanism). The detach path's device-handle close still runs on the shared default executor, which is the invariant `_join_pool` was created for

**File:** `serial_link.py:348` (`await asyncio.to_thread(self._close_link_locked, link)`), against the module comment at `serial_link.py:36-42`.

**Invariant broken.** That comment states the rule explicitly: "Joining a reader thread must never queue behind unrelated work, because detach and shutdown both wait on it", and says the private `_join_pool` exists because "`asyncio.to_thread` is `run_in_executor(None, ...)`, so every ordinary use of the obvious stdlib idiom silently shares the pool". Only the `thread.join` at line 333 was moved to `_join_pool`. The handle close on the *same* `stop()` path, three lines later, is still `asyncio.to_thread`, so it is back on the default pool the rule was written to keep it off.

**Who else is on that pool.** `_write_bytes` (`serial_link.py:944` and `970`, blocking for up to `link.WRITE_TIMEOUT = 2.0 s` when the target deasserts flow control; bounded per port by `_raw_lock` and `_cmd_lock` at one each, so up to `2 * MAX_PORTS = 64` concurrent), `export_session_db` (`server.py:1243`, unbounded duration: it copies a whole session's lines, can_frames and plot_points), `_enumerate_devices` (`server.py:927`, a setupapi query on Windows), `load_config` and every `save_*` (`server.py:960, 1008, 1028, 1059, 1079, 1142`), and `pj.configure` (`server.py:340, 1106`, whose `getaddrinfo` can park for a resolver timeout).

**Probe** (`/tmp/probe_pool.py`):

```
default executor max_workers = 12 (cpu_count = 8)
detach handle-close waited 1.80s behind unrelated work
```

**Failure scenario.** `stop()` reaches line 348 only on the path where the reader thread outlived its 2 s join, and the comment at `serial_link.py:339-341` says why it must not be delayed: "Windows serial handles are exclusive, so a re-attach of the same COM port would otherwise fail with ERROR_ACCESS_DENIED." With the default pool saturated (a large `GET /sessions/{ref}/export` plus a handful of stalled writes is enough on an 8-core box, 12 workers), that close is queued for however long the unrelated work takes, and `POST /ports/{alias}/reconnect` on Windows fails against a handle the daemon still holds. Worse on a 2-core CI runner, where the pool is 6.

The fix is one word: use `_join_pool` for the close as well, since the whole point of that pool is that nothing else can reach it.

Secondary, same pool: `POST /cmd` latency is not bounded by anything on the write path once a session export is running, because `_write_bytes` queues behind it. The event loop stays responsive, so no health surface moves.

---

## 3. MED-LOW (SUSPECTED). `GET /sessions/{ref}/export` has no size bound and leaks its temp file when the client disconnects

**File:** `server.py:1227-1261`, with `store.export_session_db` at `store.py:1112-1186` and `_unlink_later` at `server.py:1813-1816`.

**Invariant broken.** `/plot/export` refuses an over-large selection up front (`MAX_EXPORT_ROWS`, `server.py:1477-1484`), and SPEC 729 states that rule. The session export, which copies strictly more (lines plus can_frames plus plot_points plus indexes), has no equivalent count or byte guard at all.

**Failure scenario A (size).** A session spanning a multi-gigabyte capture is copied whole into `tempfile.mkstemp()`, i.e. `TMPDIR` or `/tmp`. On the many Linux distributions where `/tmp` is tmpfs, that is RAM. There is no dry-run count and no refusal, so the first indication is an ENOSPC out of SQLite (answered as a 400 by `server.py:1250-1254`, having already consumed the space) or the OOM killer.

**Failure scenario B (leak).** `server.py:1256-1261` hands cleanup to `BackgroundTask(_unlink_later, tmp_path)`. Starlette runs a response's background task only after the body has been sent; if the client disconnects mid-download the send raises and the background never runs, so the temp copy stays on disk for the machine's lifetime. A browser cancelling a large download is the ordinary way to hit this.

**Failure scenario C (small, Linux).** `server.py:1239-1241` does `mkstemp` then `os.close(fd)` then `os.unlink(tmp_path)`, and only afterwards does a worker thread `sqlite3.connect(tmp_path)`. Between the unlink and the connect the name is unclaimed and known to anything that watched `/tmp`, so on a shared machine the export can be redirected through a symlink planted at that path. `mkstemp`'s randomness protects the *creation*, not the window after the deliberate unlink.

Marked SUSPECTED because I did not drive a disconnect or a multi-gigabyte export; the code path is unambiguous from reading.

---

## 4. LOW-MED (SUSPECTED). `_SameOriginGuard` does not see cross-site subresource GETs, which is broader than its docstring and SPEC 324 claim

**File:** `server.py:495-525`, SPEC line 324 ("That blocks cross-site CSRF, cross-site WebSocket capture exfiltration, and DNS rebinding").

**Invariant.** The guard denies when `origin is not None and not _origin_matches_host(...)`. Browsers attach `Origin` to `fetch`/XHR, to WebSocket handshakes, and to cross-site form POSTs. They do **not** attach it to no-cors subresource loads: `<img src>`, `<script src>`, `<iframe>`, `<link>`, `<video>`. Such a GET to `http://127.0.0.1:8765/...` therefore arrives with no Origin, a Host of `127.0.0.1:8765` (an IP literal, so `_host_allowed` returns True at `server.py:492`), and a client address of `127.0.0.1`, which `_TokenGuard` exempts at `server.py:647`.

**Failure scenario.** Any page the operator visits can trigger every GET endpoint on the daemon. It cannot read the responses (opaque), so this is not exfiltration and not integrity loss. It is unauthenticated remote work: `GET /sessions/1/export` (finding 3), `GET /plot/export` (a full `plot_points` scan under a 1M-row ceiling), `GET /lines?match=<slow pattern>` (a 30 s `MATCH_BUDGET_S` per request against a 4-worker `match_executor`), and `GET /lines?last_ms=<huge>` (finding 1, one traceback per request).

The mechanism is inherent to browsers and I am not proposing a redesign. The finding is that the guard's docstring and SPEC 324 both read as "cross-site requests are refused", full stop, and a reader will assume a GET endpoint is unreachable cross-site when it is not. Either the wording gains the caveat, or the expensive GETs gain a bound (finding 3's is the one that matters).

---

## 5. LOW (SUSPECTED). `pjstream._resolve` refuses multicast and the unspecified address but not broadcast

**File:** `pjstream.py:61-73`.

The docstring states the rule as "Refuses a non-unicast result: multicast or the unspecified address widens the audience beyond the named recipient, which is the case the config-write bar on this destination exists to exclude (SPEC 3.7)". `255.255.255.255` and a directed broadcast such as `192.168.1.255` are not unicast and widen the audience in exactly the stated way, and both have `is_multicast == False` and `is_unspecified == False`, so both pass.

Not exploitable as written, because the socket is created without `SO_BROADCAST` and the `sendto` fails with EACCES, swallowed by `send`'s bare `except OSError`. So the practical outcome is a destination that every surface reports as enabled while no datagram ever leaves, rather than a leak. Worth closing with `ip.is_multicast or ip.is_unspecified or ip == IPv4Address("255.255.255.255")`, or by dropping the claim to what is enforced.

---

## 6. LOW (SUSPECTED). `PortManager.attach` primes before both the port cap and the store's liveness check

**File:** `serial_link.py:1094-1124`, with `prime_plot_defs` at `845-863`.

Two consequences of `await port.prime_plot_defs()` sitting before `async with self._lock`:

- The `MAX_PORTS` cap at line 1115 does not bound the priming work. Each attach queues a `^!pd ` REGEXP scan carrying the full `MATCH_BUDGET_S` (30 s) onto the 4-worker `match_executor`, and N concurrent `POST /ports` or `POST /ports/{alias}/reconnect` each get one before any of them is refused for exceeding the cap. `PLOT_DEF_LOOKBACK` bounds each scan to 20000 ids, so the per-scan work is small; the count is what is unbounded.
- `prime_plot_defs` calls `self._store.max_id()`, which is `assert self._conn is not None` (`store.py:1223-1227`). An attach that starts after `store.stop()` has nulled the connection raises AssertionError rather than PortError, so `POST /ports` answers 500 instead of the `_bad_request` the handler at `server.py:890-895` is written to produce. Under `python -O` the assert is stripped and it becomes an AttributeError on None instead. Shutdown-window only.

The comment at lines 1099-1106 gives good reasons for priming before the lock; the observation is only that the two checks it deliberately steps in front of are now unguarded on that path.

---

## 7. LOW (CONFIRMED by reading). The `regexp` closure registered on the loop connection at `Store.start()` carries a one-shot budget and is now dead code

**File:** `store.py:445`, against `_make_regexp` at `store.py:339-383`.

`_make_regexp` arms `deadline[0]` on its **first** call and never re-arms it. That is correct for its intended use (one closure per query), and every live path honours it: `query_lines_safe` re-registers a fresh closure for the inline match path (`store.py:1548-1550`), `_query_lines_threadsafe` re-registers on its private connection (`1518-1519`), and `_offload` uses `_open_read_conn`, which builds one closure per connection and closes it (`1503-1511`, `1449-1455`).

So the closure registered at `start()` is only ever reached if some future caller invokes `query_lines(match=...)` against `self._conn` without going through `query_lines_safe`. When that happens the failure is delayed and silent: the first such query works, and every one issued more than 30 s later raises `TimeoutError` out of the SQLite callback, surfacing as a generic `sqlite3.OperationalError` with no `timed_out` flag anyone is holding, so it does not even map to `MatchBudgetExceeded`. It reads as a database error rather than a budget stop.

Either drop the registration (the three live paths all supply their own), or make it obviously inert. As written it is a trap that only fires for the next person to add a match query.

---

## Things checked and found correct

Recorded so the next round does not re-derive them.

- **SPEC vs code on `/lines` limit.** SPEC 633-635 requires clamping to 0..1000 with out-of-range values brought into range rather than refused, and `truncated` staying true for a non-empty window at `limit=0`. `store.query_lines` (`1372`, `1406-1408`) matches exactly, including the `limit=-5 -> 0` case. Probed.
- **Session mutation on the loop connection outside the write queue.** The comment at `store.py:918-923` claims safety because the writer's insert-and-commit block contains no await. Verified: `_writer` (`store.py:626-685`) has no await between `_insert_batch` and `commit()`, and `start_session`/`_stop_session_locked` have no await between their own `execute` and `commit`. `start_session` drains the queue before sampling `_next_id`, and the writer stops absorbing at a `_Drain` barrier (`605-624`) so lines queued behind it cannot take ids ahead of it.
- **Reader-thread / loop shared state in `SerialPort`.** The only cross-thread fields are `_stop` (a `threading.Event`), `_link` (written unlocked at `489`, otherwise under `_write_lock`), and the `_post` bridge. The unlocked write at 489 is ordered behind the `_stop` re-check at 485, and `stop()` sets `_stop` before its join, so the "reader assigns a handle nobody will close" window is genuinely closed. Every counter (`lines_rx`, `lines_tx`, `rx_dropped`, `connected`, `_seq`, `_rx_bytes`, `_rx_lines`) is touched only on the loop.
- **`asyncio.Queue.get` under `wait_for` in the `/ws` pump** (`server.py:1553`): CPython's `Queue.get` re-wakes the next getter on cancellation, so the keepalive timeout cannot swallow a queued row.
- **`_do_wait` and `_do_assert` loop termination**, including the forbid-only and `min_window_ms` branches. Both terminate; the post-deadline drain runs exactly once.
- **`iter_plot_export` cross-thread SQLite use.** `_open_export_conn` sets `check_same_thread=False` and Starlette advances the generator one `next()` at a time, so the private connection is never touched concurrently, and the in-memory case is materialised on the loop by `open_plot_export` before it can reach a worker thread.
- **`_stdio.py` Windows text-file rule.** `_write_report` (`322-330`) opens with an explicit `newline=""`, per the cross-platform mandate. `console_entry` orders `widen_stdout_encoding` before `translate_closed_pipe_errors`, so the `_PipeErrorStream` wrapper never hides a stream that still needed reconfiguring.
- **`_TokenGuard` brute-force accounting.** Only a wrong token counts toward the budget, a missing one does not, and a non-Bearer `Authorization` header short-circuits to None so it can neither bypass the comparison nor pollute the counter.
