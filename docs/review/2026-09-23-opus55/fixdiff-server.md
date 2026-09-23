# Fix-diff leg: server slice

HEAD: `b994076c88c875ad43c30789cda6039d147b7abe`

Scope: `git diff 6e4f6f7..HEAD` over `host/mcuscope/server.py`, `host/pyproject.toml`, the SPEC 3.1/3.3.1/3.4 API hunks and the listed test files.
Scratch (not in the repo): `~/tt-data/mcuscope-2026-09-24/fixdiff/` (`server-copy/` mutation tree, `mutate.py` driver, `floor-src/` + `floor-venv*/` floor check, `probes/`).
Every probe ran from `server-copy/`, and printed `server.py`'s import path to confirm it.

## Findings

### 1. A cancelled bundle turns its cancellation into a 400 and a false error log
- `server.py:2810`: `abandon()` calls `self._conn.interrupt()` on the copy's connection even after `export_session_db` has closed it, and sqlite3 raises `ProgrammingError: Cannot operate on a closed database.`
- `server.py:2841`: that raise replaces the `CancelledError` in `_run_export`. The handler's `except Exception` then logs `session bundle failed: Cannot operate on a closed database.` and returns 400, so the cancellation is swallowed.
- Scenario: a daemon stop (the 5 s `GRACEFUL_SHUTDOWN_S` cap) lands while a bundle writes its CSV members, which is the long phase after the copy.
  - The session export has the same race, in the short window between the copy's return and `_finished`.
- The files are still removed (`_abandoned` was set before the raise, so `checkpoint()` stops the build), and `test_a_cancelled_bundle_stops_while_writing_a_member` passes over the defect.
- Confirmed (driven): that test's shape plus a log capture gave the result `(400, '{"error":"export failed: Cannot operate on a closed database."}')` and the log line above. `interrupt()` on a closed connection raises on 3.10 and on 3.13.
- Fix:
  - Drop `_conn` when the copy returns, or `suppress(sqlite3.ProgrammingError)` around `interrupt()`.
  - In `_run_export`, re-raise the `CancelledError` even if `abandon()` raises (`try: job.abandon() finally: raise`).
  - Have the test assert no error is logged.

### 2. Live `/assert` loses a decided forbid when the expect scan is cut
- `server.py:3090`: the diff moved the forbid hits' application and the `break` below the expect scan.
  - A forbid hit no longer skips the expect scan (before the diff, it did).
  - If that expect scan passes the window plus 1 s, the whole batch is unjudged, and the forbid hit found in it is thrown away.
- Scenario: forbid `OK`, expect `NEVER`, a slow expect scan. The answer is `status: empty`, `reason: "no lines were checked in the window"`, `forbid[0].matched: false`, although the forbid matched a line.
  - With `allow_empty`, the answer is `fail`, with no forbid shown.
- Confirmed (driven): `_scan_batch` patched so the forbid scan is real and the expect scan stalls.
- Fix: apply the forbid hits and `break` on any before starting the expect scan, as the pre-diff code did. Only the expect scan's cut then marks the batch unjudged.

### 3. A cut scan answers `pass` under `allow_empty`, and `empty` gives a false reason
- `server.py:2971`: `checked == 0` does not tell apart "no lines arrived" from "lines arrived but none were judged" (`unjudged > 0`).
- Scenario: a forbid-only live assert whose scan is cut.
  - Without `allow_empty`, the answer is `empty` with reason `no lines were checked in the window` and `dropped: 5`. Lines did arrive.
  - With `allow_empty: true`, the answer is `pass`, reason null, `checked_lines: 0`, `dropped: 1`: a pass over lines no pattern ever saw.
  - SPEC 3.4 defines `allow_empty` for "a window that held no lines".
- Same mechanism: a slow but legal pattern (30 rows x 0.083 s against a 300 ms `/wait`) answers `timeout`, `dropped: 30` at 1.3 s. Every retry answers the same, because the budget 400 (SPEC 3.1) cannot fire before the grace does.
- Confirmed (driven): stalled `_scan_batch` with and without `allow_empty`, and a `(a|aa)+$` flood on `/wait`.
- Fix:
  - When `unjudged > 0` and `checked == 0`, answer `empty` whatever `allow_empty` says.
  - Give that case its own reason, e.g. `N lines were not judged before the deadline`.
  - Owner call: whether a slow-pattern cut should name the pattern.

### 4. `purge before_ts` reports an id span the delete does not honour
- `server.py:1747`: `id_from`/`id_to` (and the dry run's count) come from `before_ts_span_safe`, but `delete_before_ts` deletes by `ts < before_ts` with no id bound.
- Scenario: rows committed between the count and the delete are deleted too. `before_ts` may be up to 60 s ahead, so this includes live rows arriving during the purge.
  - Driven: `deleted: 3099` against a reported span `1..3081` (3081 ids), with a live feeder at 2 ms.
  - SPEC 3.4 (`SPEC.md:846`) says the span is "the lowest and highest id it took" (class 17).
- Fix: pass `id_to=hi_id` into the delete, so it takes what the preview counted. The alternative is to report the span of what was actually deleted.

### 5. SPEC 3.1 claims `Sec-Fetch-Site` on loads that do not carry it
- `SPEC.md:365` says such a load "carries `Sec-Fetch-Site`".
- Browsers send `Sec-Fetch-*` only to potentially trustworthy URLs (HTTPS, loopback).
- Confirmed (driven, Playwright Chromium 1.62): a page on `127.0.0.1:E` loaded three `<img>`s.
  - `127.0.0.1:T` arrived with `same-site`.
  - `localhost:T` arrived with `cross-site`.
  - `http://100.98.9.94:T` (the LAN IP) arrived with **no** Sec-Fetch headers.
- Consequence: with a `0.0.0.0` bind and no token, a page can still trigger `/sessions/1/export` through the LAN address. With a token, the non-loopback client gets 401 anyway.
- Fix: state the limit in SPEC 3.1: the guard is inert for plain-HTTP non-loopback origins, and the token is the bound there.

### 6. The FastAPI floor admits a pydantic the server cannot import (class 43, latent)
- `host/pyproject.toml`: no `pydantic` requirement. `fastapi>=0.115.7` admits `pydantic>=1.7.4`.
- `server.py` imports v2-only names: `field_validator`, present since d6c14d7, and `ConfigDict`, added this round.
- Scenario: `pip install mcuscope` into an environment holding pydantic 1.x resolves cleanly, then `mcuscoped` dies at import.
- Confirmed (driven): fastapi 0.115.7 with pydantic 1.10.26 gives `ImportError: cannot import name 'field_validator'`. pydantic 2.0.2 passes the six files most exposed to it (next section).
- Fix: add `pydantic>=2.0,<3` to `dependencies`, with a one-line floor note.

### 7. A 500 carries no framing headers (nit)
- `server.py:581`: `_FrameDenial` sits inside Starlette's `ServerErrorMiddleware`, so the 500 that middleware writes has neither header. SPEC 3.1 (`SPEC.md:368`) says "Every HTTP response".
- Confirmed (driven): `has_port_rows` patched to raise, then `GET /plot/channels?port=zz` answers `500 None None`.
- Fix: word SPEC as "every response a route or guard produces", or wrap the app outside Starlette's stack in `daemon.py`. Impact is nil: a plain-text 500 has nothing to click.

### 8. Untested and redundant branches (revert-verified, nits)

Each mutation runs from `mutate.py`, which restores the file afterwards.

- M3, `server.py:1350`: `if body.enabled:` changed to `if True:` **survived** `test_server_lifespan.py`.
  - SPEC's "a dest saved with `enabled: false` is grammar-checked only" (`SPEC.md:600`) is unpinned.
  - Add: `PUT /config/plotjuggler {enabled: false, dest: "239.1.2.3:9870"}` is 200.
- M4, `server.py:3090`: dropping `forbid_scan is not _UNJUDGED` survived. It only costs a second grace period; finding 2's fix removes the line anyway.
- M5, `server.py:874`: removing `_FrameDenial`'s non-http early return survived. The branch is redundant, since WS messages never have type `http.response.start`. Delete it.
- M6, `server.py:1748`: `or n == 0` removed survived. The delete of nothing answers the same, so this is a harmless shortcut.
- Controls: M1 (Sec-Fetch check made http-only) and M2 (navigate exemption ignoring the mode) were both caught.

### 9. SPEC text (nits)
- `SPEC.md:846-847`: two consecutive sentences restate the `before_ts` rule, one from each batch. Keep one.
- `SPEC.md:621`: the undeclared-parameter exemption lists `/ws` and the static UI but not the root redirect `/`, which the code also exempts and the tests pin.

## Doubts verified (fix-server.md)

- `/marker` requires a known port: **holds** as described.
  - Consistent with SPEC 4 ("an unknown `-p` is refused ... on reads and writes alike") and the CLI-3 ruling.
  - Labelling a board before it is attached is now a 400. Flagged for the owner, not settled here.
- CLI-2, a failed send ends the window with `checked_lines: 0`: **holds**. `test_an_assert_whose_send_is_refused_fails_and_says_why` pins it, and it is consistent with the ruling ("fails the verdict"). Whether the window should still run is the owner's call.
- The 1 s grace can read as a quiet board: **holds, and worse than stated**. Under `allow_empty` it is a `pass` over unjudged lines, and a slow pattern answers `timeout` on every retry (finding 3, driven).
- `route.dependant.query_params` on the floor: **refuted as a risk**. The floor run is in the next section.
  - `test_server_request_validation.py` (exact-message 422s, so it cannot pass with the dependency inert) passed there.
  - It also passed on fastapi 0.141.1 / starlette 1.7.0 in the copy's venv.
- Windows, unlink with an open handle:
  - Reasoned, not driven. `_TempFileResponse` removes after FileResponse's `anyio.open_file` context closes. The copy's `sqlite3` connection is closed in `export_session_db`'s `finally` before `run()` removes files.
  - A finaliser unlink that loses to an open handle on Windows is suppressed. The build's own failure path, or the next start's sweep, removes the file.
  - Not verified on Windows.
- Real browser Sec-Fetch headers: driven in Chromium (finding 5).
  - The guard's assumptions hold for loopback: another port of `127.0.0.1` is `same-site`, and `localhost` against `127.0.0.1` is `cross-site`.
  - They fail for a plain-HTTP LAN address.

## Floor check

- Venv: Python 3.10.20, `uv pip install --resolution lowest-direct -e floor-src/host[dev]`.
  - Resolved to fastapi 0.115.7, starlette 0.44.0, uvicorn 0.35.0, httpx 0.27.0, typer 0.26.0, websockets 14.0, regex 2024.4.16, pytest 8.0.0 and pytest-asyncio 0.23.5.
  - pydantic 2.13.5 is transitive, so lowest-direct does not lower it.
- All 17 slice files, run one file at a time: every one passed (337 tests), `test_server_request_validation.py` included (24 passed).
- Second venv at pydantic 2.0.2 (the lowest v2 fastapi 0.115.7 admits): request_validation, guards, assert, live_verdicts, purge_and_sessions and sessions all passed. Strict mode, `extra="forbid"` and the nested `ConfigPortEntry` all validate there.
- pydantic 1.10.26: import fails (finding 6).
- Venvs not deleted: the owner's rule wants a manifest and confirmation for `rm -rf`. The paths are in the reply.

## Checked, nothing found

- `_refuse_undeclared_query`: 38 routes enumerated by AST.
  - Every query parameter is declared on its signature. No handler reads `request.query_params`, and no route declares query parameters through a sub-dependency.
  - WS and `/` are skipped; the `/ui` mount and 404s never reach it.
- Web UI and CLI query builders (`p.set`, `q.append`, the literal `?` URLs), against the declarations: no undeclared name.
- `_unknown_port`:
  - Applied on all six reads, retrospective `/assert` and `/marker`.
  - `/ws` is exempt per the ruling. Live `/wait`/`/assert` and writes already require an attached port.
  - `has_port_rows` plans as `SEARCH lines USING COVERING INDEX idx_lines_port_id (port=?)`: one seek on the loop, like `resolve_session`.
- `_resolve_port`: all five write callers are converted, and no caller of the deleted `PortManager.resolve` remains in `mcuscope/`.
  - The `test_config_api.py:440` docstring (outside this slice) still names it.
- `_Body` strict/forbid: every body model inherits it. The CLI sends int-typed values to the int fields; the web UI's are covered by the fix batch's `intField` sweep.
- `_control_char_field`:
  - Both attach paths share one function (class 19).
  - tomlkit round-trips DEL, C1 and U+2028 through `tomllib`; only ESC (`\e`) fails. So `< 0x20` is the right bound.
- `check_host` on PUT /config/server: judged on the stripped value (class 60).
- PUT /config/plotjuggler: `_resolve` is the runtime's whole check besides socket creation (class 19). Its `except (ValueError, OSError)` matches the sibling handlers (class 18), and IDNA `UnicodeError` is a `ValueError`.
- `_live_scan`:
  - It catches `asyncio.TimeoutError` (class 42).
  - A cut executor future cannot leave an unretrieved exception, because asyncio skips a cancelled destination (class 39).
  - The pool is process-wide and named `mcu-live-match`.
- `CaptureWatch.next_batch`: the conditional expression binds as intended (`dir != "tx"` only when `chan` is unset).
- `_send_failure`: `send_command` returns only `ok`, `err` (always with `err_code`/`err_name`) and `timeout`, so no KeyError is possible.
- `_run_export`:
  - The admission check and the increment have no await between them (class 37).
  - The slot is released from the pool thread via `call_soon_threadsafe`.
  - A queued build cancelled before it starts never runs.
- `_ExportJob`:
  - `on_open`, `abandon` and `run` share the lock.
  - `checkpoint` reads a single flag, and `_paths` is iterated only after `_finished` (class 40).
- `_sweep_export_orphans`:
  - The regex matches `tempfile`'s alphabet `[a-z0-9_]`, and the copy uses the DELETE journal, so `-journal` is the only sidecar.
  - The key goes through `normcase`/`abspath`, so it is stable on Windows. The sweep is off the loop.
- `_ClosingStream`: the store generator opens its connection lazily with `check_same_thread=False`, so closing it on the loop is safe. The list form (`:memory:`) has no `close` and is skipped.
- `_pull_first`: the first page runs off the loop. A `MatchBudgetExceeded` leaves the generator finished, so nothing is left open.
- The lifespan finaliser's file removal is suppressed as a whole, and `config_warnings` now exists before the PlotJuggler block.
- The `/sessions/stop` reopen goes through `stop_session(reopen_auto=)` under the stop lock.
- SPEC 3.1, 3.3.1 and 3.4 hunks against the code: the 422 wording, the 503 export cause, `config_warnings`, the `/ports` bounds and control characters, the reconnect race, the write default, `/can/frames` `port`, the `/lines/export` csv guard and first page, the unknown-port list, `/wait` tx exclusion, the `/assert` shape (`reason`, `cmd_result`, `dropped`, `empty`), `/marker` and the export pool and temp names.
  - All match, except findings 4, 5, 7 and 9.
- `pyproject.toml` diff: a comment-only change to the timeout note.
- Test hunks:
  - `test_assert`, `test_prerelease_daemon_core_windows`: status becomes `empty`.
  - `test_review_r2_server`: renames.
  - `test_server_scope`: the `**kw` pass-through.
  - `test_session_bundle`: the sim CAN filter.
  - `test_sessions`: marker plus a positive control.
  - `test_export_lines_can`: `limit` becomes a 422.
  - `test_wait_repeat`: `chan: cmd`.
  - `test_daemon_r2026_09_12_server`: the API-10 expectation, and `names` only on `/plot/export`.
  - `test_daemon_token_exposure`: in-process parser checks, no platform dependence.
  - `test_plot_export_decode`: `_EXPORT_CHUNK`/`_EXPORT_PAGE` are read at call time, so the patch is live.
  - No hunk can fail on Windows by construction. None was run there.
