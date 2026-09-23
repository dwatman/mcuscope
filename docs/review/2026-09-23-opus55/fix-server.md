# Fix batch: server (2026-09-23)

Files: `host/mcuscope/server.py`, plus the `Store.last_id_before_ts` deletion in `store.py` (the store batch had finished and asked for it).
Scratch: `~/tt-data/mcuscope-2026-09-23/fix-server/` (`revert.py` is the mutation driver, `revert.out` its log, `hostcopy/` the tree it mutates so the shared tree never held a mutant).
Line numbers are `server.py` as it stands now.

Revert method: each mutation in `revert.py` is applied to the copy, the named selection runs, and the file is restored. "caught" means the selection failed under the mutant.

## Findings

### API-1 (server half): export builds get their own pool, 503 past the queue
- `EXPORT_WORKERS = 2`, `EXPORT_QUEUE_MAX = 2` (`:2676`); `_pool()` gives one lazily created process-wide pool per name; `_run_export` (`:2789`) admits, submits and counts a slot until the build itself returns (not the handler).
- Refusal: 503 `too many session exports in progress; try again shortly` (the CLI maps a non-shutdown 503 to exit 1).
- Test: `test_server_exports.py::test_exports_past_the_queue_are_refused_and_builds_run_off_the_default_pool` (asserts the build ran on an `mcu-export` thread), `::test_a_cancelled_export_removes_its_copy_when_the_build_returns` (the slot is held while a cancelled build runs).
- Revert: R10 (no admission), R10b (slot released by the handler), R11 (another pool) all caught.

### API-2: Sec-Fetch-Site refusal, and the live match hop bounded by the window
- `_SameOriginGuard` (`:693`) refuses `Sec-Fetch-Site: cross-site|same-site` (case-folded) unless `_navigates_to_ui` (`:647`): `Sec-Fetch-Mode: navigate` to `/`, `/ui` or `/ui/...`. `_is_ui_path` is shared with the token guard.
- `_live_scan` (`:2345`) wraps every live scan in `wait_for(remaining window + LIVE_SCAN_GRACE_S=1 s)`. On expiry the batch is unjudged: `/wait` answers `timeout` and `/assert` its verdict, each with those rows added to `dropped`, and the loop ends. Rows count toward `checked_lines` only once judged.
- Tests: `test_server_guards.py` (API routes 403 for both sites and a mixed-case value; `same-origin`, `none` and no header served; navigation to the UI allowed, to an API route refused; a UI path loaded as a subresource refused; a cross-site WS refused with a control), and `test_server_live_verdicts.py::test_a_live_scan_is_cut_at_the_window_and_its_rows_reported[/wait|/assert]` (structural: the response arrives while the stuck scan has not returned).
- Revert: R07, R07b, R07c, R07d, R24, R24b, R24c caught.

### API-3: first-page regex trip is a 400
- `_pull_first` (`:2281`) fetches the first row on a worker thread before the `StreamingResponse` is built; `/lines/export` maps `MatchBudgetExceeded` from `open_lines_export` or that pull to 400.
- Test: `test_server_exports.py::test_a_regex_that_trips_on_the_first_page_is_a_400` (positive control: `a+!` over the same row is 200).
- Revert: R16 caught.

### API-4: unknown body fields and undeclared query params are a 422
- `_Body` (`:201`), the base of all 16 body models: `ConfigDict(extra="forbid", strict=True)`. The message names the field (`sesion: Extra inputs are not permitted`).
- `_refuse_undeclared_query` (`:371`) is an app-level dependency. It compares the query against `route.dependant.query_params` and raises 422 `name: unknown query parameter`. It skips WebSocket scopes (the guard's `token`) and the `/` redirect; `/ui` is a mount and never reaches it.
- Client sweep (class 53), every param and body key the bundled clients send, checked against the route declarations:
  - `cli.py` `_lines_params` (port, chan, match, last_ms, since_id, session, since_ts, until_ts, id_to, limit): all declared on `/lines`. `/lines/export` pops `limit` first (`cli.py:1875`).
  - `/can/frames` (port, session, id, bus, since_ts, until_ts, format, limit, since_id, id_to) and `/plot/export` (last_ms, since_ts, until_ts, session, port, decode, changes, deadband, names, format): all declared.
  - Bodies of `/ports`, `/send`, `/break`, `/cmd`, `/marker`, `/wait`, `/assert` (plus the new `allow_empty`), `/purge`, `/sessions` and `/plotjuggler`: all declared.
  - `webui/*.js` (`exportrange.js`, `terminal.js` `/lines` and export params, `api.js` seed, backfill and plot series, `can.js`, `exportdlg.js`, `settings.js` PUT bodies, `statusbar.js` attach body, `cmdbar.js`): all declared.
  - `settings.js` round-trips GET /config ports into PUT /config/ports; those keys are exactly `ConfigPortEntry`'s.
  - Integer fields go through `intField` (`Number.isInteger`), so strict mode receives JSON ints.
- Tests: `test_server_request_validation.py`: the `/assert` typo against a `session` control, `/wait` `timout_ms`, a nested port entry field, `/lines?chans=&zzz=`, a query param beside a path param, and WS `?token=` plus `/?from=` kept.
- Revert: R01, R03, R03b, R03c caught.

### API-5: `since_id` lower bound
- `MIN_LINE_ID = -(2**63)` (`:118`), `ge=` on all five `since_id` params. The existing `test_plot_export_since_id` case `-(2**63)` stays legal.
- Test: `test_a_since_id_below_the_sqlite_range_is_a_422` on all five routes, with the floor itself not a 422.
- Revert: R04 (all five) caught.

### API-6: shared device/serial length
- `MAX_DEVICE_LEN = 512`, `MAX_SERIAL_LEN = 128` (`:108`), used by `PortAttach` and `ConfigPortEntry`.
- Test: `test_attach_and_saved_ports_share_one_length_bound[device|serial_number]` (limit+1 is 422 on both routes, the limit is not).
- Revert: R05 caught.

### API-8: control characters in saved ports
- `_control_char_field` (`:990`) refuses a char below 0x20 in `device` or `serial_number`: 400 `port <alias>: invalid <field>` on PUT /config/ports, and `invalid <field>` on POST /ports (the same class-19 check on the sibling path).
- Test: `test_a_control_character_in_a_saved_port_is_refused[serial_number|device]` (ESC, NUL, TAB; positive control).
- Revert: R06a, R06b, R06c (device half dropped) caught.

### API-9: strict body types
- `_Body` `strict=True`. `{"ms": true}`, `"5"` and `5.0` are 422. `{"all": "yes"}`, `1`, `dry_run: "true"` and `0` are 422. A JSON int still fills `before_ts`.
- Tests: `test_a_break_length_must_be_an_integer`, `test_a_purge_flag_must_be_a_boolean`, `test_a_json_integer_still_fills_a_float_field`, `test_a_valid_break_length_reaches_the_handler`.
- Revert: R02 caught.

### API-10: CSV `raw` formula guard
- `_csv_lines` (`:3317`) guards `raw`; `dir` stays unguarded. The `_csv_cell` docstring is updated.
- Test: `test_server_exports.py::test_the_csv_raw_cell_is_formula_guarded_and_jsonl_is_faithful`.
- Revert: R18 caught.

### API-11: non-Bearer Authorization falls through
- `_TokenGuard._provided_token` (`:794`): another scheme no longer returns None, so `X-Auth-Token` is read.
- Test: `test_server_guards.py::test_a_basic_auth_header_falls_through_to_the_token_header` (Basic alone is 401, Basic plus the right X-Auth-Token is 200, a wrong Bearer still decides).
- Revert: R08 caught.

### WEBUI-1: frame denial
- `_FrameDenial` (`:872`) adds `X-Frame-Options: DENY` and `Content-Security-Policy: frame-ancestors 'none'` to every HTTP response start. It is the outermost middleware, so guard refusals carry the headers too.
- Tests: `test_every_response_forbids_framing` (`/ui/`, a JS asset, `/status`, a 404) and `test_a_guard_refusal_forbids_framing_too` (403 Host, 401 token).
- Revert: R09 (removed) and R09b (registered inside the guards) caught.

### CLI-1: own tx rows are not a match
- `CaptureWatch.next_batch` (`:2494`): with `chan` unset, `dir == "tx"` rows are skipped; with `chan` set, only that channel matches (so `chan=cmd` matches tx).
- Tests: `test_a_wait_does_not_match_its_own_command` (`chan=cmd` control matches the tx row) and `test_a_live_assert_does_not_expect_its_own_command`.
- Revert: R19 caught.

### CLI-2: `/assert` reports its send
- The live branch keeps `cmd_result` (`:3026`). If it is not `ok`, it returns at once with status `fail`, `checked_lines: 0`, and `reason` from `_send_failure` (`:3090`): `send answered ERR <code> <name> <detail>` or `send got no response in <timeout_ms> ms`.
- Every verdict now carries `reason` (null normally) and `cmd_result` (null for raw or retrospective).
- Tests: `test_an_assert_whose_send_is_refused_fails_and_says_why` (the sim's `reset` is ERR 1 badcmd, with a `ping` control, and asserts no window ran), `test_an_assert_whose_send_timed_out_fails_and_says_why` (`--drop-response 2`), `test_a_send_that_timed_out_is_named_as_such` and `test_a_retrospective_or_raw_assert_carries_no_command_result`.
- Revert: R20a (result discarded), R20b (no early return), R20c (send result not judged) caught.

### CLI-3: unknown port refused, empty verdict
- `_unknown_port` (`:999`) passes a port that is attached or that `Store.has_port_rows` knows; anything else is 400 `no such port: X`.
  - Applied on `/lines`, `/lines/export`, `/can/frames`, `/plot/channels`, `/plot/series`, `/plot/export`, retrospective `/assert` and `/marker`.
  - Live `/wait`/`/assert` and writes already require an attached port. `/ws` is unchanged.
- The verdict (`:2943`) is `empty` with reason `no lines were checked in the window` when `checked == 0` and not `allow_empty`; `allow_empty: true` restores pass/fail. A failed send takes precedence (fail).
- `Store.has_port_rows` landed from the store batch and is called synchronously (one index seek).
- Tests: `test_a_read_scoped_to_an_unknown_port_is_refused` (the six reads, with a detached board's history as control), `test_a_verdict_or_marker_for_an_unknown_port_is_refused`, `test_a_retrospective_verdict_over_no_lines_is_empty` and `test_a_live_verdict_over_no_lines_is_empty`.
- Revert: R21a to R21g caught (empty, allow_empty, the helper, the history lookup, retrospective assert, marker, `/plot/series` singly).

### CLI-18: default port only when exactly one is attached
- The daemon did the defaulting, in `PortManager.resolve` (serial_link.py, sole-connected fallback). `_resolve_port` (`:1012`) now serves all five write callers; `PortManager.resolve` is deleted (follow-up below).
  - The named port is used as given; one attached port is the default whatever its state; zero is `no ports attached`.
  - Several give 400 `port is ambiguous; specify one of: board, spare`. The prefix is kept, and the CLI batch already keys on `one of:`.
- Tests: `test_an_unnamed_write_with_two_ports_attached_is_refused` (`/send`, `/cmd`, `/break`, `/wait` send, `/assert` send; the second port is a never-connecting device) and `test_the_sole_attached_port_is_the_default_even_when_down`.
- Revert: R22 (back to `ports.resolve`) caught.

### PERF-2 (server half): close the stream source
- `_ClosingStream` (`:2254`) closes the store generator in a `finally` after the response ends however it ends. Used by `/plot/export`, `/lines/export` and `/can/frames?format=csv`.
- Two extra closes I first wrote were redundant under revert and deleted: `body_iterator.aclose()` (R17e survived), and closing the rendering chain as well as the source (R17f survived). So was the bundle-member close in the build's `finally` (refcounting already closes them when the build frame dies).
- Test: `test_server_exports.py::test_an_export_abandoned_after_its_headers_closes_its_source[plot|lines|can]`: a real uvicorn stack, a raw socket reads the status line (the UI preflight's shape) and closes, and an endless spy source must see its `finally`.
- Revert: R17 (plot), R17b (lines), R17c (can), R17d (no source close) caught.

### PERF-3: budget message passthrough
- Checked: every store-side trip reaches the client as `str(exc)` (`/lines`, `/lines/export`, bundle, plot defs, `/wait`, `/assert` handlers). Nothing maps the text.
- The live scans' own `simplify the regex` text is raised only for a per-call timeout or a whole-batch budget on one short live batch, both pattern faults. No change.

### PERF-4: live matching pool
- `_live_scan` runs on `_pool("live-match", LIVE_MATCH_WORKERS=4)`, not `store.match_executor`.
- Tests: `test_a_live_wait_matches_while_the_shared_match_pool_is_busy` (all `MATCH_WORKERS` held) and the existing `test_hardening.py::test_wait_scan_runs_off_the_default_executor` (thread name updated).
- Revert: R23 caught.

### LIFECYCLE-2: temp copies
- `_ExportJob` (`:2732`) names files `mcuscope-{session|bundle}-<key>-*`, where `<key>` is the first 8 hex digits of the sha256 of the normalised absolute db path. It lists them in `app.state.export_files` and removes them whichever of build-finish and handler-cancel comes second (a lock orders the two).
- `_TempFileResponse` discards its path from the set. Its docstring no longer claims the next export removes an orphan.
- The lifespan finaliser removes every listed file and its `-journal` (`:528`). Startup sweeps this key's names only (`_sweep_export_orphans`, `:2719`, called at `:471`).
- Tests: `test_a_temp_copy_is_named_for_its_capture_and_gone_after_download`, `test_a_cancelled_export_removes_its_copy_when_the_build_returns`, `test_a_stop_during_a_build_removes_its_copy`, `test_an_abandoned_job_removes_its_files_whichever_side_ends_last[both orders]`, and `test_startup_sweeps_only_this_captures_leftovers` (another key, the old unkeyed name, a dashed near-miss and `notes.txt` all survive).
- Revert: R12a, R12b, R12c, R13, R14, R14b (key-blind sweep), R15 caught.
- Interrupting the copy on cancel: done in the follow-up below.

### LIFECYCLE-4 (caller)
- The reconnect route passes `require_existing=True` (`:1167`).
- Test: `test_server_lifespan.py::test_a_detach_during_a_reconnect_is_not_undone` (the prime is parked, DELETE runs, the prime is released, and reconnect is 400 `no such port: r` with `/ports` empty).
- Revert: R25 caught.

### SRC-6 (PUT side, and the config-warnings half the daemon batch passed over)
- PUT /config/plotjuggler with `enabled: true` also runs `pjstream._resolve` off-loop (`:1351`), the runtime's own unicast and resolve check. `ValueError`/`OSError` become 400.
- Lifespan: `config_warnings` is built before the PlotJuggler block, and an enable failure appends `plotjuggler: cannot enable for '<dest>': <exc>` (`:451`).
- Tests: `test_saving_an_enabled_stream_the_runtime_would_refuse_is_refused` (file unchanged after the refusal), `test_a_startup_that_cannot_enable_the_stream_says_so_in_status` (the loader warning is kept first) and `test_a_startup_that_enables_the_stream_warns_nothing`.
- Revert: R26, R27 caught.

### PUT /config/server uses `config.check_host` (daemon batch item)
- `:1272`: `host {exc}` 400.
- Tests: `test_a_saved_host_is_held_to_the_loaders_check` (spaces, `a b`, DEL, NUL) and `test_a_saved_host_is_stored_stripped`.
- Revert: R28 caught.

### HEALTH-15 S03
- Test: `test_server_lifespan.py::test_a_raising_shutdown_step_still_records_the_stop[stop_all|aclose|close]`: the last sys row is `daemon stop`, with `daemon start` as the positive control.
- Revert: R29 (stop_all unsuppressed) caught.

### Store batch items (CAPTURE-1, CAPTURE-2, LIFECYCLE-3 callers)
- `POST /purge before_ts` (`:1741`): `before_ts_span_safe` for the dry run and reported span, then `delete_before_ts`. `Store.last_id_before_ts` is deleted (no caller left).
  - Test: `test_server_purge_and_sessions.py::test_a_time_purge_takes_the_old_rows_whatever_their_ids`, a row with a lower id and a newer ts survives.
  - Revert: R30 (id-range delete) caught.
- `POST /sessions/stop` (`:1492`): `stop_session(reopen_auto=...)`.
  - Test: `test_the_reopened_automatic_session_abuts_the_named_one`, with `Store.start_session` patched to land a row first.
  - Revert: R31 caught.
- Lifespan: the unreachable `elif open_session is not None: stop_session()` branch is deleted and its comment corrected. No revert: this is a deletion; `Store.start()` owns the behaviour.
- `/can/frames` JSON passes rows through unchanged. `port` is in the store's row already, and the `_csv_can` docstring is updated (still no CSV port column).
  - Test: `test_can_frames_name_their_board`.

### Web UI export guard double (coordinator item, after CLI-3 broke it)
- `tests/webui_js/exportdlg_guards.mjs` now mirrors the daemon's order:
  - Undeclared query parameters are refused first (`DECLARED` per route, `name: unknown query parameter`).
  - `since_id`'s `-2^63` floor.
  - An unknown `port` (`known.ports`, null skips) after the time-window check on all three routes, as server.py orders `_check_window(...) or _unknown_port(...)`.
- `test_webui_js.py::test_export_guard_double_agrees_with_the_daemon`: `known.ports` is read from the daemon's own rows. Seven GUARD_URLS were added to reach the new clauses and pin their order (port after window, before session and CAN ids; unknown params before type errors).
- Verified: `-k export_guard_double` passes. Mutating the double caught all five cases: plot, lines and can port order, the declared-params check, and the since_id floor.
- The ten JS files that install the double (`installExportDaemon`) pass one by one under `node --test`, so no UI export URL carries a parameter the daemon now refuses. `installExportDaemon` passes no ports, so the port guard is skipped there.
- The real web UI has no client-side mirror of these refusals (`grep "no such plot channel\|no such port" webui/*.js` finds none); nothing to change there.

### Stale comments (HEALTH-26)
- `server.py:96-108` (the orphaned `MIN_DB_CAP_BYTES` and `MAX_BAUD` blocks) are deleted.
- `:320-321` (`max_db_bytes` floor) now says the handler checks it.

## Existing tests edited

- `test_assert.py` `_flood`: synthetic rows get `"dir": "rx"`, since a real row always has one (class 63). `test_live_window_closes_early_without_a_minimum`: `fail` becomes `empty` (0 lines).
- `test_hardening.py`:
  - `test_wait_scan_runs_off_the_default_executor`: the thread prefix becomes `mcu-live-match`.
  - The flood at the NEEDLE /wait test gets `"dir": "rx"`.
  - `test_marker_port_is_bounded_like_the_alias_grammar`: seeds a row for `board-1.a`, which must now be a known port.
- `test_export_lines_can.py::test_export_has_no_row_limit`: `limit` dropped from the export call, plus an assertion that it is now a 422.
- `test_daemon_r2026_09_12_server.py`:
  - `test_csv_export_does_not_rewrite_a_captured_line`: expects the apostrophe per the API-10 ruling; the quoting and `dir` checks stay.
  - `test_a_non_finite_time_bound_is_refused_by_name`: sends `names` only to `/plot/export`.
- `test_wait_repeat.py`: four requests that match their own tx row gain `chan: "cmd"` / `--chan cmd` (CLI-1). The CLI batch also edited this file; my edits were exact-anchor.
- `test_review_r2_server.py`:
  - `test_a_disconnected_download_still_removes_the_temp_copy` passes `live=set()`.
  - `test_export_tmp_dir_for_a_memory_capture_is_the_system_temp` calls `_export_dir(path)` (renamed from `_export_tmp_dir(request)`).
- `test_daemon_r2026_09_12_server.py`: the L1 `PortManager.resolve` block deleted with the method (follow-up).
- `test_webui_js.py`: GUARD_URLS extended and `known.ports` added (above); `webui_js/exportdlg_guards.mjs` updated to match.
- `test_regressions.py::test_assert_with_send_still_judges_lines_the_send_used_the_whole_window_for`: under CLI-2 a timed-out send fails at once. To keep testing the post-deadline drain, the port's `send_command` is patched to answer `ok` exactly at the deadline, and the precondition is read from `cmd_result`.

Tests-batch files needing a change: none found. `test_e2e.py`, `test_sessions.py`, `test_session_bundle.py`, `test_plot_export_decode.py`, `test_eol.py`, `test_cli_contract.py` and `test_sim_pty.py` were not run; the first six pass as they stand.

## SPEC edits

- 3.1:
  - The same-origin guard now covers no-cors loads via `Sec-Fetch-Site`, with the navigate-to-UI exception.
  - Framing headers on every response.
  - A non-Bearer `Authorization` falls through.
  - The live-match pool; `/lines/export` first-page 400.
- 3.3.1:
  - Port `device`/`serial_number` length bounds are shared with `POST /ports`; control chars below 0x20 are 400.
  - `since_id` lower bound.
  - `PUT /config/plotjuggler` enabled-dest check.
- 3.4 intro: 422 for unknown fields and params (with the `/ws` and static exemption); strict body types; the 503 export cause.
- 3.4 `/status`: `config_warnings` includes the PlotJuggler enable failure; `rx_dropped` gains the link batch's "cut off mid-line" cause.
- 3.4 `/ports`: length bounds and control chars; a reconnect racing a detach is 400.
- 3.4 `/send`: the write-default rule (only one attached, else `port is ambiguous; specify one of: ...`).
- 3.4 `/can/frames`: `port` in the JSON row.
- 3.4 `/lines/export`: jsonl is the faithful format, csv `raw` is guarded; first page before headers.
- 3.4: new paragraph on the unknown `port=` 400 across reads, retrospective `/assert` and `/marker`.
- 3.4 `/wait`: tx rows excluded unless `chan` names them; the 1 s-past-window scan cut is counted in `dropped`.
- 3.4 `/assert`: signature gains `allow_empty`. The send-failure fail, the `empty` status, `reason` and `cmd_result` are in the returns shape.
- 3.4 `/marker`: the port must be known.
- 3.4 `/purge`: `before_ts` selects by `ts`; the reported id span.
- 3.4 automatic sessions: a crashed auto session closes at its newest row with no end marker (store batch's wording).
- 3.4 session export and bundle: the export pool and 503, the keyed temp names, removal on cancel and stop, and the startup sweep.

## Changelog

- Security: requests a browser marks `Sec-Fetch-Site: cross-site` or `same-site` are refused (403), except a navigation to the UI; every response forbids framing.
- API: unknown body fields and query parameters are a 422 naming them; body types are strict.
- API: `port=` naming a board neither attached nor in the capture is a 400 `no such port`, on reads, retrospective `/assert` and `/marker`.
- API: a write without `port` is refused whenever more than one port is attached.
- `/assert`: a window that checked no lines answers `empty` (`allow_empty: true` to accept it); the response carries `cmd_result` and `reason`; a `send` answered with ERR or a timeout fails it.
- `/wait` and `/assert` no longer match their own outgoing command unless `chan` is `cmd`; live matching has its own pool and answers near its deadline.
- `/lines/export?format=csv` guards `raw` against spreadsheet formulas (jsonl stays faithful); a regex over budget on the first page is a 400.
- Session export and bundle: at most 2 builds plus 2 queued (503 beyond); temp copies are removed on cancel, on stop, and at the next start.
- Streamed exports release their database snapshot when the client disconnects (the WAL could grow without bound).
- `since_id` below -2^63 is a 422; `POST /ports` bounds `device`/`serial_number` length; control characters in saved ports are refused.
- `Authorization` with a non-Bearer scheme no longer hides `X-Auth-Token`.
- `PUT /config/plotjuggler` refuses an enabled destination the stream would refuse; a startup that cannot enable it reports that in `config_warnings`.
- `/purge before_ts` deletes by timestamp rather than an id range; `/can/frames` rows carry `port`.

## Follow-up, after the other batches finished

### LIFECYCLE-2: a cancelled export stops its copy
- `store.py:1480` `Store.export_session_db` takes `on_open`, called with the copy's connection before any SQL runs.
- `_ExportJob.on_open` (`server.py:2757`) keeps that connection, or raises `_ExportAbandoned` if the request is already gone.
  - `abandon()` (`:2810`) calls `conn.interrupt()` (thread-safe in sqlite3) on a build still running. The copy raises, and `run()`'s failure path removes the files.
  - Both builds pass the hook (`:1556` export, `:1664` bundle).
  - The bundle also checks `job.checkpoint()` before each 64 kB chunk it writes into the zip (`:1672`): one member can take as long as the copy, and the interrupt does not reach it.
- Tests in `test_server_exports.py`:
  - `test_a_cancelled_export_interrupts_its_copy` (real `export_session_db` over 3000 rows, crawling via a progress handler; the copy must end `interrupted`, with no temp file left).
  - `test_a_stop_interrupts_a_copy_in_flight` (the bundle's copy, stopped by the lifespan exit).
  - `test_an_export_opened_after_its_abandon_never_starts`.
  - `test_a_cancelled_bundle_stops_while_writing_a_member` (an endless `lines.txt` member; its files must go).
- Revert: I1 (no interrupt), I2 (hook not called in store.py), I3 (export without the hook), I4 (bundle without the hook), I6 (an open after the abandon accepted) and I7 (no chunk checkpoint) are all caught.
- Deleted as redundant: a job registry on `app.state` that the lifespan finaliser abandoned. The handler's cancellation abandons first (the graceful-shutdown cap cancels handlers before the lifespan ends), and removing it was not caught by any test.

### CLI-18: `PortManager.resolve` deleted
- There are no callers in `mcuscope/` (the CLI and web UI send `port` or omit it; the daemon's `_resolve_port` decides).
- Deleted `serial_link.py` `PortManager.resolve`, and the "L1: a sole connected port is not ambiguous" block in `test_daemon_r2026_09_12_server.py`: 4 tests, the `_Attached`/`_manager` helpers, and the now-unused import. They pinned the old sole-connected rule.
- Two comments that named it (`server.py` `/marker`, `test_hardening.py`) now name `_resolve_port`.
- The new rule stays pinned by `test_server_live_verdicts.py` (R22 caught).

### Verified after the follow-up
- These pass, each run as a single file: `test_server_exports`, `test_server_live_verdicts`, `test_session_bundle`, `test_sessions`, `test_review_r2_server`, `test_daemon_r2026_09_12_server`, `test_daemon_r2026_09_12_bundle`, `test_hardening`, `test_cli_ux`, `test_e2e`, `test_store_writer`.
- `ruff check .` is clean over `host/`.

## Not done

- `test_timeline.py::test_lines_limit_above_the_cap_is_honoured` times out in its `_add_lines` helper (1200 sequential `store.add_line` awaits exceed 60 s). That path does not touch server.py; it looks like the store's coalesced commits (PERF-9) under sequential awaits. For the store or tests batch.
- Not mine, seen failing mid-round (other batches' files in flux, not re-run after): `test_sweep_daemon_classes` (store writer `_hold_off_until`), `test_sweep_followups` (CLI message). `test_cli.py` einval had one flaky failure; it passes alone.

## Doubts

- `/marker` now requires a known port. The ruling says "every route taking port", and the existing hardening test had a never-seen alias accepted. Least sure this is wanted: an agent labelling a board before attaching it now gets a 400.
- CLI-2: a failed send ends the window at once with `checked_lines: 0`. If the owner wants the window judged anyway (a `forbid` seen after an ERR), that is the one-line R20b revert.
- `_live_scan`'s 1 s grace is a judgement. A scan cut there reports `timeout`, or an `empty` verdict, with `dropped > 0` rather than a new status. The CLI warns on `dropped`, but an `empty` with `dropped > 0` could read as a quiet board.
- `_refuse_undeclared_query` relies on `route.dependant.query_params`, a FastAPI internal attribute (present from the 0.115 floor to 0.141). Not checked on the floor version.
- Not checked: Windows (the temp-file unlink with an open handle, and the sweep), and a real browser's Sec-Fetch headers (the tests send them by hand).
