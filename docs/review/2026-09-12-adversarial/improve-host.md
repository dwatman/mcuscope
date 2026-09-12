# Host improvement leg, 2026-09-12 (HEAD c15b7c6)

Open sweep for usability, CPU efficiency and function on the daemon and the CLI. Read-only on the main tree; scratch in `/tmp/rev-2026-09-12/improve-host/`.

Environment: Linux, python 3.13.5 in `host/.venv`, throwaway config `cfg/cfg.toml` (port 18604, own `db_path`), `XDG_*` redirected under the scratch dir, `MCUSCOPE_UPDATE_CHECK=0`. `py-spy 0.4.2` installed as a `uv tool` (outside the repo) for the daemon profile; `ptrace_scope=1` refuses attach, so py-spy ran as the parent process.

## (a) Measurements

### CLI start-up, wall clock through the installed console script (best of 5)

| command | ms | note |
|---|---|---|
| `mcu status` | 261 | daemon answers the same request in 1.0 ms |
| `mcu status --json` | 257 | |
| `mcu lines --limit 10` | 258 | |
| `mcu lines --limit 1000` | 263 | |
| `mcu cmd "i2c scan"` | 264 | |
| `mcu session list` | 259 | server side 216 ms on a 2.3M-row capture, 9 ms here |
| `mcu ports` | 256 | |
| `mcu plot channels` | 255 | |
| `mcu devices` | 271 | |
| `mcu --help` | 198 | |
| `mcu ai-guide` | 118 | no daemon contact |
| `mcu --version` | 114 | no daemon contact |

Decomposition (same harness, `python -c` so the console-script trampoline is excluded):

| stage | ms |
|---|---|
| bare interpreter | 16 |
| `import mcuscope.cli` | 79 |
| `main(['status'])` | 257 |
| `main(['status'])` with `sys.modules['httpx._main'] = None` | 214 |
| `main(['lines','--limit','10'])` | 257 |
| the same with `httpx._main` blocked | 210 |

`-X importtime`, cumulative:

| subtree | ms | reached by |
|---|---|---|
| `mcuscope.cli` | 62.7 | every command |
| `httpx` | 65.8 | every command that contacts the daemon |
| `httpx._main` (inside the above) | 36.9 | unconditional: `httpx/__init__.py` does `from ._main import main` and the extras are installed |
| `rich.*` under `httpx._main` | 21.5 self, 55 modules | nothing in `mcu` uses it |
| `typer.rich_utils` | 73.6 | `--help` and error rendering only |

`import httpx` in isolation: 99-121 ms plain, 52-54 ms with `httpx._main` pre-blocked; `rich` and `click` then absent from `sys.modules`.

### Daemon ingest, sim flooding in process (`--plot --flood 20000`)

| run | lines/s | CPU | notes |
|---|---|---|---|
| 60 s, no profiler | 20,115 | 67.4% of one core | 1.21M rows, 166 MB, `ws_dropped` 0, `write_errors` 0 |
| 60 s, under py-spy (200 Hz) | 15,383 | 86.3% | 14,466 samples |

The sim generates the lines in the same process, so the CPU figure covers both sides. Per-thread on-CPU from the sampled run (72 s of thread time total):

| thread | on-CPU | top self-time |
|---|---|---|
| uvicorn (event loop) | 48.1 s (67%) | `_insert_batch` store.py:840 **33.9 s = 70.4%**; `_writer` 1.2 s; `_submit_rx_line` 1.1 s; `submit_line_nowait` 0.7 s; `_write_req` 1.3 s across three lines; `_on_bytes` 0.5 s |
| serial-board (reader) | 16.2 s (22%) | `sim._sanitize` 11.1 s (simulator, not shipped cost); `link.read` 1.6 s; `protocol.is_oversized` 0.7 s |
| match_executor / asyncio default | 4.2 s | idle `_worker` park |

So at 20k lines/s the loop spends ~37 us per line inside the `INSERT`/`commit` and ~15 us per line in Python. Nothing else on the loop is measurable. The fan-out short-circuits with no subscriber attached, so this run does not cover the per-subscriber cost (see gaps).

Insert-cost decomposition, 400k rows through the real `SCHEMA`, batches of 1000, commit per batch:

| variant | rows/s | CPU |
|---|---|---|
| baseline (writer connection as shipped) | 36,495 | 10.84 s |
| `cache_size=-8000` | 36,711 | 10.76 s |
| `cache_size=-65536` | 38,126 | 10.37 s |
| `wal_autocheckpoint=4000` | 37,904 | 10.51 s |
| `cache_size=-65536` + `wal_autocheckpoint=4000` | 46,227 | 8.58 s |
| baseline without `idx_lines_ts` | 55,498 | 7.06 s |

### Query plans and timings, 2.3M lines / 323 MB, no `sqlite_stat1`

| call | ms | plan |
|---|---|---|
| `query_lines` limit 100, no filter | 0.51 | `SCAN lines` (reverse PK, stops at 101) |
| `query_lines chans=[debug]` | 0.38 | `SEARCH lines USING INDEX idx_lines_chan_id` |
| `query_lines port=board` | 0.32 | `SEARCH lines USING INDEX idx_lines_port_id` |
| `query_lines match=...` | 1.68 | offloaded, REGEXP over the LIMIT window |
| `query_lines since_id` (tail poll) | 1.35 | `SEARCH lines USING INTEGER PRIMARY KEY (rowid>?)` |
| `query_lines last_ms=5000` | 0.11 | `COVERING INDEX idx_lines_ts (ts>?)` then PK seek |
| `count_lines`, no filter | 23.4 | PK walk |
| `count_lines chans=[debug]` | 182.8 | covering index walk |
| `query_can_frames` limit 100 | 0.81 | |
| `query_plot_channels_safe`, cold | 48.4 | first read rebuilds the summary from SQL |
| `query_plot_channels_safe`, warm (`GET /plot/channels`, live daemon) | **1.0** | served from the in-memory summary |
| `plot_streams_safe` | 26.9 | |
| **`list_sessions_safe` (1 session)** | **216.3** | correlated `COUNT(*)` per session, O(rows in span) |
| `active_session` | 0.08 | partial index, one row |
| `max_id` (SQL) | 0.01 | |
| `content_bytes` | 0.03 | three pragmas |
| `last_id_before_ts` | 0.04 | `COVERING INDEX idx_lines_ts` |
| `query_plot_series` | 14.6 | `SEARCH pp USING idx_plot_name_line` + window function |

No unindexed scan found. The two `SCAN` entries are both correct (a `LIMIT`-bounded reverse PK read, and a one-row partial index). Alternative forms for the session count were driven and are all slower: forcing `idx_lines_chan_id` 429 ms, `idx_lines_port_id` 409 ms, `idx_lines_ts` 408 ms against 307 ms for the shipped shape.

`GET /status` 1.0 ms, `GET /plot/channels` 1.0 ms, `GET /sessions?limit=50` 9 ms on a 63k-row capture. The web UI's only timer-driven server call is `/status` at 5 s.

### Size cap, driven directly (`Store` on a 129 MB / 933k-row capture, cap 20 MB)

| sweep | trimmed | ms | file | live content | free pages |
|---|---|---|---|---|---|
| before | - | - | 129.1 MB | 129.1 MB | 0 |
| 0 | 796,693 | 9,571 | 120.9 MB | 19.2 MB | ~24,849 |
| 1 | 0 | 0 | 120.9 MB | 19.2 MB | 24,849 |

97 MB sits in the freelist and is never handed back: `_reclaim_pages` is bounded to 2000 pages (8 MB) per call, and `_sweep_size_locked` only calls it `if dropped`.

### Config keys, driven

A config with `prot = 18605`, `[storge]`, `retention_dayz = 3` and `max_db_byte = 2000000` loads clean with **no warning of any kind**: `server.port` 8558, `db_path` `''` (the platformdirs default), `retention_days` 10, `max_db_bytes` 0.

### Robustness drives

| case | observed |
|---|---|
| daemon killed 3 s into `mcu wait --timeout 25000` | CLI returns 5.3 s later with `error: Internal Server Error`, **exit 1**; daemon log carries an uncaught `CancelledError: Task cancelled, timeout graceful shutdown exceeded` from `server.py:2127 next_batch` |
| `mcu lines/log export/plot export --session nosuch` | exit 0, zero rows, no message |
| `mcu assert/purge/session export --session nosuch` | exit 1, `error: no such session: nosuch` |
| `mcu plot export --names sine,nosuchname` | exit 0, 61 rows, the dead name never mentioned |
| `mcu plot export --names nosuchname` | exit 1, `error: no such plot channel: nosuchname; see /plot/channels` |
| update check offline | 5 s timeout, `log.debug` only, 1 h retry, detached: correct |
| port under a new name | `serial_number=` works in config, in `POST /ports` and is reported by `GET /devices`; `mcu attach` has no flag for it |
| `mcu daemon start` failures (port held, broken TOML) | already actionable, unchanged from the 2026-09-07 leg |

## (b) Proposals

Ranked by value to a user or agent per line of diff.

### 1. Stop `import httpx` dragging in httpx's own CLI (rich + click)

- **Measured**: `httpx/__init__.py` unconditionally does `from ._main import main`, which costs 36.9 ms of httpx's 65.8 ms import and loads 55 `rich` modules that `mcu` never touches. End to end: `mcu status` 257 ms -> 214 ms, `mcu lines` 257 ms -> 210 ms. That is 17% off every command in the guide's agent pattern.
- **Change**: in `host/mcuscope/cli_client.py`, at module level (it imports no httpx today, so the cost stays zero for `--help`/`--version`/`ai-guide`), add `sys.modules.setdefault("httpx._main", None)` with a two-line comment saying why: a `None` entry makes `import httpx._main` raise `ImportError`, which is the branch httpx already handles by substituting a stub `main()`. Do the same immediately above `import httpx` in `host/mcuscope/update_check.py` so `mcuscoped` start-up gets it too. Nothing in the tree calls `httpx.main`.
- **Size**: S (about 8 lines including comments).
- **Test that breaks it if done wrong**: in `tests/test_cli_*`, drive `main(['status'])` against a stubbed transport in-process and assert `"rich" not in sys.modules` and `"httpx._main" not in [m for m in sys.modules if sys.modules[m] is not None]`; plus assert `httpx.Client` still constructs and a real request through the stub still works (a sentinel placed on the wrong name silently does nothing, and only the second assertion catches that).
- **Docs**: AI_GUIDE "TIMING-CRITICAL WORK" says "about 200 ms" per call; still true, no edit required. No SPEC change.

### 2. Refuse to be silent about an unrecognised config key

- **Observed**: four typos in one file (`prot`, `[storge]`, `retention_dayz`, `max_db_byte`) produced no warning; the daemon would have bound 8558 instead of 18605 and written the default capture instead of the configured one. Everything else in `config.py` is meticulous - wrong type fails the load naming the key, wrong value warns and defaults - and a misspelled key is the likeliest hand-edit mistake of all.
- **Change**: in `host/mcuscope/config.py`, in `_from_dict` (after `_check_shape`), walk the parsed dict: warn via `log.warning` for any top-level key outside `{"server","storage","update","plotjuggler","ports"}`, and for any key inside those tables outside the set the loader reads. Do the same for each `[[ports]]` entry against its own key set. Use `difflib.get_close_matches` (stdlib) to add `did you mean 'retention_days'?` when there is one candidate. **Warn, never refuse**: the write-back path deliberately preserves unknown keys so a file edited by a newer version still round-trips, and a hard failure would break that. Derive the known-key sets from one module-level tuple per section so a new key cannot be added without appearing here.
- **Size**: S (about 30 lines with the key tables).
- **Test that breaks it if done wrong**: a config with one typo in each of the four sections plus one in a `[[ports]]` entry, asserting each warning names its key and its section; a fully-populated correct config asserting `caplog` is empty (this is the half that fails if the known-key set is stale); and a config with a typo asserting `load_config` still returns defaults rather than raising.
- **Docs**: SPEC 3.3 gains one sentence ("an unrecognised key is warned about and ignored, never refused").

### 3. `mcu wait` timeout says nothing

- **Observed**: `mcu wait --match NEVERMATCH --timeout 1200` prints exactly `timeout` and exits 2. The `--json` form already carries `waited_ms`. An agent that gets `timeout` has to run a second command to learn whether anything arrived at all.
- **Change**: in `host/mcuscope/cli.py`, the `wait` command's non-JSON timeout branch, replace `timeout` with a line built from what is already in hand and in the response: the pattern, the port if one was selected, and `waited_ms` rounded, e.g. `timeout: no line matched '^!can' on port sim in 1200 ms`. When `--send` was used, append `(sent 1, failures 0)` from the existing `sends`/`send_failures` fields. Exit code stays 2. Do not add a wire field, so SPEC 3.4 is untouched.
- **Size**: S (under 10 lines).
- **Test that breaks it if done wrong**: assert the message contains the pattern text and the elapsed figure, and separately assert the exit code is still 2 (a refactor that turns this into `die()` would change it to 1); assert `--json` output is byte-identical to today's.
- **Docs**: no guide change (the guide already says "exit 2 on timeout").

### 4. An unknown `--session` is a silent empty answer on three commands and a refusal on three others

- **Observed**: `lines`, `log export`, `plot export` return exit 0 and zero rows for a mistyped session name; `assert`, `purge` and `session export` refuse with `no such session: X`. The silent half is the dangerous one: "this run captured nothing" and "you typed the name wrong" are the same output.
- **Change**: `host/mcuscope/server.py` already carries the distinction - `SessionRange.unknown` is set by `_session_range_for` and checked only by `/assert`. Add the same guard to the five `_session_range(request, session)` call sites (lines 1624, 1667, 1717, 1778, 1820: `/lines`, `/lines/export`, `/can/frames`, `/plot/series`, `/plot/export`): `if span.unknown: return _bad_request(f"no such session: {session}")`. The CLI needs no change, it already renders a 400 body as `error: ...` with exit 1. Keep `_UNKNOWN_SESSION` as the fallback for any future caller.
- **Size**: S (5 lines plus the SPEC paragraph).
- **Test that breaks it if done wrong**: one test per endpoint asserting HTTP 400 and that the body names the ref, **plus** a test that a session which exists but holds no lines still returns 200 with an empty list. Without the second, a fix that refuses every empty result passes.
- **Docs**: SPEC 3.4 currently documents the empty-range behaviour for the `/lines` family; that paragraph must change to say the endpoints refuse an unresolvable `session=`.

### 5. Freed pages are never handed back, so a capture file only ever grows

- **Measured**: one size-cap sweep trimmed 796,693 rows, live content fell 129.1 MB -> 19.2 MB under a 20 MB cap, and the file went 129.1 MB -> 120.9 MB and then stopped. 24,849 free pages (97 MB) remain and nothing drains them: `_reclaim_pages` is bounded to 2000 pages per call (correctly, it runs on the loop) but `_sweep_size_locked` only calls it `if dropped`, so once the cap is met there is nothing left to trim and the backlog is permanent. Its docstring claims "the retention sweep runs periodically, so a backlog drains over successive ticks", which is false for exactly this reason. Worse, the **age sweep never calls it at all**: `_sweep_retention_locked` has no reclaim, so the default configuration (10-day retention, no size cap) frees pages into the freelist forever.
- **Change**: in `host/mcuscope/store.py`, move reclamation to the one place all three delete paths pass through. In `sweep_tick`, after the size sweep and the age sweep, read `PRAGMA freelist_count` and call `_reclaim_pages(self._conn)` when it exceeds a small threshold, regardless of whether anything was trimmed this tick. Keep the existing `_VACUUM_PAGES` bound, so the per-call cost model (15.8 ms for 8 MB) is unchanged and a backlog drains at 8 MB/minute. The two existing `if dropped:` calls in `_sweep_size_locked` and `delete_range` can then go, or stay as the prompt first step after a large delete. Correct the `_reclaim_pages` docstring.
- **Size**: S (about 15 lines).
- **Test that breaks it if done wrong**: build a capture, delete most of it, then run `sweep_tick` repeatedly with nothing left to trim and assert `PRAGMA page_count` falls on each tick and converges near the live content. Separately assert one tick reclaims **at most** `_VACUUM_PAGES` (a fix that drops the bound would pass the first test and reintroduce an O(freelist) stall on the loop), and assert the age sweep path reclaims too.
- **Docs**: none. SPEC's retention section describes behaviour that this makes true.

### 6. `mcu status` never mentions trimmed lines

- **Observed**: `/status` reports `lines_trimmed`, and the text renderer prints `write_errors`, `rx_dropped`, `writer_alive`, the update badge and the PlotJuggler state, each only when it is news. `lines_trimmed` is the one counter left out, so a capture at its size cap silently deletes the oldest half of the run the agent is about to query.
- **Change**: `host/mcuscope/cli.py` `status()`, beside the existing `errs` line: `trimmed = body.get("lines_trimmed", 0)` and append `  trimmed={trimmed}` when non-zero. Same `.get` treatment for an older daemon.
- **Size**: S (2 lines).
- **Test that breaks it if done wrong**: a stubbed status body with `lines_trimmed` non-zero asserting the figure appears, and one with zero asserting the word `trimmed` does not (the "quiet when zero" half is what a careless fix loses).
- **Docs**: none.

### 7. A daemon that stops during a long poll reports `Internal Server Error`

- **Observed**: killing the daemon 3 s into `mcu wait --timeout 25000` returned `error: Internal Server Error` and exit 1 after 5.3 s (uvicorn's graceful-shutdown cap), with an uncaught `CancelledError` traceback in the daemon log from `next_batch`'s `asyncio.wait_for(q.get(), ...)`. The SPEC 4 contract has an exit code for this (3, daemon unreachable) and the CLI never reaches it, because a cancelled handler becomes uvicorn's generic 500. `mcu assert --timeout`, `tail -f` and `can dump -f` sit on the same machinery.
- **Change**: root-cause it in the store so every client benefits, not just the CLI. `Store.stop()` currently closes the writer and the connections and leaves subscriber queues untouched. Make it push a `None` sentinel into every queue in `self._subscribers` (dropping the oldest to make room, as the fan-out does) before closing the connection. In `server.py`, `_LineWatch.next_batch` treats a `None` item as "the capture is stopping" and raises a dedicated exception; `_do_wait` and `_do_assert` catch it and return `JSONResponse(status_code=503, content={"error": "daemon is shutting down; the wait was cut short"})`. In `cli_client.py`, add 503 to the map that yields exit 3 (it already states the transport-failure map once). The handlers then answer inside the 5 s graceful window instead of being cancelled.
- **Size**: M (about 60 lines across three files).
- **Test that breaks it if done wrong**: start a stack, issue a `/wait` with a long timeout from a thread, call `store.stop()`, and assert the response is 503 with that message and **not** a 200 carrying `status: "timeout"` (the cheap wrong fix wakes the watcher and lets the handler report an ordinary timeout, which reads to an agent as "the board stayed silent"). Second test: the CLI maps that 503 to exit 3. Third: a normal timeout with the daemon alive still returns 200 and exit 2.
- **Docs**: SPEC 3.4 gains the 503 for the long-poll endpoints; SPEC 4 / AI_GUIDE exit-code table needs no change (3 already means unreachable), but the guide's `wait` line is worth one clause.

### 8. `mcu plot channels` prints raw float reprs

- **Observed**: `last=0.14090123772621155` for an `f4` channel, `last=1.0` for a `u1` bit lane, next to `last=16.4 V` for one that happens to round. Seventeen significant figures of a 32-bit float is noise, and the inconsistency reads as a bug in the capture.
- **Change**: `host/mcuscope/cli.py` line 1934 uses `f"last={ch['last_value']}"`. `host/mcuscope/cli_output.py` already has `_fmt_value` (integer when integral, else `%.6g`) written for the `--decode` renderer. Export it and use it here. `--json` output is untouched.
- **Size**: S (3 lines).
- **Test that breaks it if done wrong**: assert `1.0` renders as `1`, `0.14090123772621155` as `0.140901`, and that the `--json` body still carries the full-precision float (a fix applied on the wrong side of the `json_out` branch would pass the first two).
- **Docs**: none.

### 9. `plot export` silently drops a mistyped channel name when another name is valid

- **Observed**: `--names sine,nosuchname` wrote 61 rows and exit 0 with no mention of the dead name; `--names nosuchname` alone refuses correctly. The code says so deliberately: the check runs only when the selection is empty because "the scan costs nothing on any path that selected rows".
- **New argument**: that cost no longer exists. Since the 2026-09-07 round `/plot/channels` is served from the writer's in-memory summary; measured warm at **1.0 ms** on a live daemon, against the 48 ms cold rebuild the old reasoning was written against. The check can now run on every export.
- **Change**: `host/mcuscope/server.py` `plot_export`, hoist the `known`/`unknown` computation out of the `if first_id is None` block and refuse whenever `unknown` is non-empty, with the message it already produces. Keep the message listing every unknown name. Same treatment for `/plot/series` if it takes a name list.
- **Size**: S (about 10 lines, mostly moving existing code).
- **Test that breaks it if done wrong**: `--names <good>,<bad>` returns 400 naming only the bad one; `--names <good>` over a window with no points still returns a header-only CSV and 200 (refusing that would be the over-correction); the all-bad case still returns 400.
- **Docs**: SPEC's `/plot/export` paragraph if it states the current tolerance; AI_GUIDE's plot export lines need no change.

### 10. `mcu attach` cannot attach by serial number

- **Observed**: the brief's "a port that enumerates under a new name" is already solved everywhere except the CLI. `[[ports]] serial_number` works, `POST /ports` accepts `serial_number` (server.py:328, and 973 refuses a body with neither), `SerialPort` resolves it on every open so a replug under a new `/dev/ttyACM*` is picked up, and `GET /devices` reports each device's serial number. `mcu attach` offers only `--baud/--alias/--eol`, so the CLI path for a debugger that moves is "hand-edit config.toml" or "curl". AI_GUIDE never mentions serial numbers at all.
- **Change**: `host/mcuscope/cli.py` `attach`: make the `device` argument optional, add `--serial SN`, refuse with a named error when both or neither are given, and pass `serial_number` in the POST body (the endpoint already validates the rest). Add the serial number to the `attach` help text and one line to AI_GUIDE's attach block pointing at the fourth column of `mcu devices`.
- **Size**: S (about 20 lines).
- **Test that breaks it if done wrong**: `mcu attach --serial X --alias b` posts `serial_number` and no `device`; `mcu attach /dev/x --serial X` refuses naming both flags; `mcu attach` with neither refuses; and the plain device form still posts `device` and no `serial_number`.
- **Docs**: AI_GUIDE attach block; SPEC 4's option table for `attach`.

### 11. `mcu can dump` has no `--session`

- **Observed**: `/can/frames` already takes `session=` (server.py:1695). The CLI exposes `--last-ms`, `--from`, `--to`, `--id`, `--bus` but not `--session`, so "show me this run's CAN traffic" means copying timestamps out of `mcu session list`. Every other read command (`lines`, `tail`, `log export`, `plot export`, `assert`) takes it.
- **Change**: `host/mcuscope/cli.py` `can_dump`: add the same `--session` option the other read commands use and forward it as a query parameter. Proposal 4 gives it the "no such session" refusal for free.
- **Size**: S (about 6 lines).
- **Test that breaks it if done wrong**: assert the request carries `session=`, and assert the frames returned are bounded by that session's id range rather than the whole capture (asserting only that the parameter was sent passes a version that drops it server-side).
- **Docs**: AI_GUIDE SESSIONS block gains the `can dump --session` line; `test_cli_contract.py` requires it.

### 12. Give the writer connection a page cache

- **Measured**: the read connections get `PRAGMA cache_size=-8000`; the writer connection gets none, so it runs on SQLite's 2 MB default against a capture that reaches hundreds of MB. Alone it is worth 4% (36,495 -> 38,126 rows/s). Paired with `wal_autocheckpoint=4000` it is worth 27% (36,495 -> 46,227 rows/s, 10.84 s -> 8.58 s of loop CPU for 400k rows), which at the flood rate is roughly 8 s of the 34 s the loop spends inside `_insert_batch` per minute.
- **Change**: `host/mcuscope/store.py` `start()`, beside the existing pragmas: `PRAGMA cache_size=-65536` on the write connection. The checkpoint half is the one with a tradeoff and should be taken deliberately: a 4000-page autocheckpoint means a larger, rarer WAL flush, so the `_SLOW_COMMIT_S` tail gets worse even as the mean improves, and the `-wal` sidecar (counted by `db_size_bytes`) grows to ~16 MB. Ship the cache bump; treat the checkpoint value as an owner decision with the numbers above.
- **Size**: S (2 lines plus the comment carrying the measurement).
- **Test that breaks it if done wrong**: assert the writer connection reports the configured `cache_size` after `start()`, and that a capture still opens and commits on a path where the pragma is refused. There is no cheap throughput assertion that is not flaky; the pragma read-back is the honest test.
- **Docs**: none.

### 13. Label the `mcu devices` columns

- **Observed**: four unlabelled columns (`device`, `description`, `vid:pid`, `serial_number`, and sometimes a by-id path). Proposal 10 makes the fourth one load-bearing.
- **Change**: `host/mcuscope/cli.py` `devices()`: print one header line with the same widths before the loop. Keep `--json` untouched.
- **Size**: S (2 lines).
- **Test that breaks it if done wrong**: assert the header names `serial`, and assert the no-devices path still prints only `no serial devices found` (a header printed before the empty check is the obvious slip).
- **Docs**: none.

## (c) Considered and not proposed

- **Move the store writer off the event loop.** `_insert_batch` is 70% of loop CPU at 20k lines/s, but ARCHITECTURE states the residency is deliberate (it keeps broadcast-after-commit ordering, the Python-owned id sequence uninterleaved, and retention chunks out of an open writer transaction). An L change against a documented decision, with no user-visible symptom at bench rates.
- **Drop `idx_lines_ts`.** Worth 35% of insert CPU (36,495 -> 55,498 rows/s), but it serves the retention sweep's `DELETE ... WHERE ts < ?` and every `--from`/`--to` and `--last-ms` bound, all of which would become full scans. Deriving the bound by binary search over `id` assumes ts is monotonic in id, which two ports and a clock change break.
- **Make `list_sessions` cheaper.** 216 ms at 2.3M rows is real, but every alternative form was driven and is slower (forced covering indexes 408-429 ms against 307 ms), it is already offloaded to the match executor, and the total work is one pass over the table however many sessions there are. A stored per-session counter would have to be maintained by three delete paths.
- **`count_lines` with a chan filter at 183 ms.** Only reached by `/sessions` bookkeeping and the purge preview, both deliberate one-shot commands, both offloaded.
- **Skip the per-row `json.dumps` for rows a subscriber's `port_filter` excludes.** The texts list is already built once per batch for all JSON subscribers; the waste only appears with two ports and a filtered subscriber, and the flood run had none attached, so there is no measurement behind it.
- **Drop the per-line `asyncio.Future` for rx lines that are not responses.** ~15 us/line of Python is spent on `_write_req` + `create_future` + `_RxPrep`; cutting part of it would save a few percent of loop CPU and would touch the drop-accounting and pending-response settle contract.
- **A `--doctor` subcommand** (bench bug report 1, recommendation 2). `mcuscoped` already writes a keyed startup log and a crash log, and `mcu --version` reports the interpreter; the remaining gap is Windows-specific and belongs with the Windows leg.
- **Auto close/reopen on repeated write timeouts** (bench feedback 2026-09-01, item 4): already on the 2026-09-01 not-built list, and the report itself records that detach/attach did not recover the V3PWR, so a reopen would not have helped.
- **`--deadband` outside `plot export`, DBC decode, `--match` against decoded text**: 2026-09-01 not-built list, no new argument.
- **Markers on the charts, DBC decoding, MCP wrapper, CAN FD, HIL fixtures**: P2 backlog.
- **`mcu --json --show-completion` printing the script**: 2026-09-07 "left" list, no new argument.
- **A hint on `daemon unreachable` when `--url` names a non-default address**: deliberate, and the 2026-09-07 leg drove it.
- **Bench feedback items 1, 2, 3, 5, 6, 7, 8, 9**: all shipped in 9dd2290 and verified present in this drive (`--decode --changes`, `--from/--to`, `target=` in status, `--retry-ms`, `--active`, named sessions surviving restart, uncapped `log export`).

## Gaps in this leg

- The flood profile ran with **no WebSocket subscriber attached**, so `_broadcast_batch` short-circuited and the per-subscriber fan-out cost is unmeasured. A repeat with 4 `/ws` clients at 20k lines/s would settle whether `q.full()`/`put_nowait` per row per subscriber is material.
- cProfile on the uvicorn thread deadlocked the in-process harness (two attempts, one 120 s timeout with no output), so all daemon profiling is py-spy sampling at 200 Hz; self-time inside `_insert_batch` is SQLite's C time and is not broken down further.
- The plot-heavy query plans were taken against a capture with 20,725 plot points; `/plot/series` and the summary rebuild were not explained against a capture with millions.
- The size-cap drive ran `Store` directly rather than through a daemon, so the 9.6 s first sweep was not observed competing with live ingest.
