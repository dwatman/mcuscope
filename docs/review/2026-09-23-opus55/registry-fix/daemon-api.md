# Fix batch daemon-api

Base `3f10153`. Files: `server.py`, `store.py`, `config.py`, `pjstream.py`, SPEC 3.3/3.4/3.7, ARCHITECTURE (those modules), the batch's test files and eleven new ones.
Revert-verification: `~/tt-data/mcuscope-2026-09-25/fix-daemon-api/mutants.py` applies each mutant to a private copy of `host/` and runs its tests; 68 mutants, 68 killed (the full list is its `M` table; three survivors of the first run are resolved below).

## Findings

- **R1-1 HIGH** fixed (D-1 A).
  - `store.compile_user_regex` is the one compile for user patterns: `repeat_expansion` bounds the expanded size (`MAX_REPEAT_EXPANSION` 100,000), then `regex.compile(p, USER_REGEX_FLAGS)`; `/lines`, `/lines/export`, `/wait`, `/assert` and `_make_regexp` all use it, the three server sites via `asyncio.to_thread`.
  - Refusal: `match regex too large: repeats expand to N (max 100000)` (`/assert`: `regex too large '<pat>': repeats expand to ...`).
  - Deviation from the batch text, kept to the ruling: siblings add, not only the product along one nesting path. Measured: twenty `\d{65535}` (180 chars) compile 393 ms and 360 MB, and 12 x `(?:a{300}){300}` 375 ms and 300 MB, both of which a per-path rule accepts. `\d{65535}` alone stays legal (65,535).
  - Counts follow what `regex` expands (measured): `{m}` m copies, `{m,n}`/`{m,}` m+1, `+` 2, `*`/`?` 1. Verbose and V1 patterns get a coarse upper bound (length x product of counts): a verbose comment can hide a paren from a scanner, and `a#(?x:(?:(?:a{100}) #)\n{100}){100})` fooled both a verbose-blind and a verbose-everywhere scan.
  - Test: `test_server_regex_budget.py` (nested refused on all four routes with the text, siblings, verbose smuggle, `\d{65535}` accepted on all four, off-loop recorder, 27 expansion cases). Mutants: 17, one per scanner branch and site.
- **R19-2 LOW** fixed (D-5 A, daemon half): `USER_REGEX_FLAGS = regex.ASCII` for every user compile. Test: `test_ascii_classes_skip_a_non_ascii_digit_in_a_marker` (`/lines` and retrospective `/assert`), and the recorder asserts ASCII on the live `/wait`/`/assert` copies. Mutant `unicode classes` killed. `test_pane_regex_dialect.py` still passes.
- **R1-2 LOW** fixed: the download's `finally` unlinks via `asyncio.to_thread`; `abandon()` of a finished job hands removal to a one-worker `export-cleanup` pool. Tests: `test_server_export_job.py` (two recorders on `_remove_export_file`). Mutants killed.
- **R17-1 MEDIUM** fixed (D-3 A): `Config.base_dir` (set by `read_config` to the file's directory); `resolve_db_path` joins and returns an absolute path; `PUT /config/storage`'s restart check resolves the saved value against the same directory. Test: `test_config_db_path.py` (file in A, CWD B; relative `--config`; `/status` absolute; unchanged save needs no restart, a changed one does). Two mutants killed; a third (base_dir for a missing file) survived and that branch was deleted as redundant (a missing file has no `db_path`).
- **R17-3 LOW** fixed: `PlotJugglerStreamer.target` (resolved `addr:port`, `[v6]:port`, null while off) and `status()`, the one shape `/status`, `GET` and `PUT /plotjuggler` answer. Test: `test_pjstream_target.py`. Mutants killed.
- **R19-3 LOW** fixed (D-6 A): `MarkerBody.text` and `SessionBody.name` share `_stripped` (an `AfterValidator`). Test: `test_marker_text_is_stripped_and_a_blank_one_refused`. Mutant killed.
- **R20-1 MEDIUM** fixed: `first_export_line_id` is one `LIMIT 1` seek per distinct name (`WITH n(name) AS (VALUES ..) SELECT MIN((correlated seek))`, no compound-select limit); `export_sids` is `CROSS JOIN`-pinned. Explained with 1, 2 and 5 names, with and without `port`: every plan drives `SEARCH pp USING (COVERING) INDEX idx_plot_name_line (name=?)`, no `FOR ORDER BY` sort (`export_sids` keeps its tiny `TEMP B-TREE FOR DISTINCT`). Test: `test_plot_export_anchor_and_sids_seek_each_name_rather_than_sort` (plans plus rows). Both mutants killed.
- **R20-2 MEDIUM** fixed (D-7 A, daemon half): `Store.can_frames_page` reads frames and `MAX(id)` in one read transaction; `/can/frames` answers `next_since_id` (capped by `id_to`, never below `since_id`). Tests: `test_store_can_watermark.py` (quiet port: the next poll costs the same VM steps at 20 and 400 frames, positive control that the stale watermark walks every frame; a frame committed between the two reads stays above the watermark; endpoint shape). Five mutants killed.
- **R20-3 MEDIUM** fixed: `_last_age_sweep = (cutoff, floor)`; while the floor has not risen the walk is `ts >= last_cutoff`. Beyond the batch text: the writer drops the record when it commits a row stamped below that cutoff (a clock step back, or a caller's own `ts`), and a sweep that saw it dropped mid-walk records nothing; without that, six existing tests lost rows they expected deleted. Tests: `test_store_retention_walk.py` (second sweep under 200 VM steps against 2000 protected rows, first over 2000; risen floor; late old row; mid-sweep reset). Five mutants killed.
- **R20-4 LOW** fixed: `test_the_session_name_lookup_uses_the_name_index` explains the statement `resolve_session` issues; the `since_ts` anchor explains what `_window_id_floor` issues, and now asserts a `SEARCH ... idx_lines_ts (ts<?)` with no temp b-tree (the old `idx_lines_ts in plan` passed on both mutants below). Mutants `anchor ordered by id` (the batch's revert), `anchor not indexed`, `name lookup not indexed` killed.
- **R20-5 LOW** fixed: `Store.query_plot_channels` deleted; its GROUP BY lives in `test_store_plot_summary.query_plot_channels(store, port)`, imported by `test_plot.py`, `test_store_fastpaths.py`, `test_store_lines_plan.py`; the plan pin on it deleted. No branch to revert.
- **R22-4 LOW** fixed (D-8 A): `UrlUInt`, `UrlInt` (`since_id`, `decimate`), `UrlFloat`, `UrlBool` on all 36 int/float/bool query and path params (35 of sweep H plus the new `check`). The grammar runs before pydantic's lax parse; error text `<name>: must be ...` (a `PydanticCustomError`, not "Value error, ..."). Non-finite words pass to SPEC 3.4's existing 400 `since_ts must be a finite number`. Found while fixing: FastAPI drops the validator from `x: UrlUInt = Query(...)` (non-optional), so those six are `Annotated[UrlUInt, Query(...)] = d`, and `test_every_numeric_and_bool_url_param_carries_its_grammar` walks the routes. Tests: `test_query_and_path_params_hold_their_grammar` (`DELETE /sessions/+2`, `%202%20`, `3?data=yes` 422 and delete nothing; `/sessions/2` deletes). Seven mutants killed.
- **R27-7 MEDIUM** fixed: the fault is parametrized over the copy and the rename, with a `failed` flag proving the fault fired. Mutant `rebuild not atomic` (commit and re-BEGIN after the DROP) fails the rename case.
- **R28-4 LOW** fixed: counts collected in the thread, asserted on the main thread. Mutant (read conn returns count+1) killed.
- **R29-1 LOW** fixed: asserts `invalid port: ` prefix. Mutant (guard off) killed.
- **R31-1 LOW** fixed: `_send_only_fields` refuses `eol` or an explicit `send_mode` without `send` on `/wait` and `/assert`. Test: `test_send_mode_without_send_is_refused_on_wait_and_assert` (both modes, eol keeps its own text, positive control). Mutant killed.
- **R39-1 LOW** half fixed.
  - `_until_stopped`: fixed (`work.exception()` on a done, uncancelled future). Test `test_until_stopped_retrieves_work_that_failed_as_it_was_cancelled`, with a detector positive control. Mutant killed.
  - `_admit_and_build`: not a defect. `Future.cancel()` clears the never-retrieved flag even on a done future (CPython `futures.py` and `_asyncio`), and the handler already calls `built.cancel()` first; the added `.exception()` survived its mutant and was removed. The test stays and now pins that `built.cancel()` (mutant `build not cancelled` killed).
- **R45-1 LOW** fixed: `save_ports` reuses each saved `[[ports]]` table (the last of a duplicated alias, the one the loader read), updates the modelled keys, deletes unset ones and implicit defaults, reorders to the body, appends new, drops removed; a non-AoT `ports` is replaced. Test: `test_config_writeback.py`. Four mutants killed.
- **R71-1 LOW** fixed (D-17 A, daemon half): `RETENTION_DAYS_MAX`, `MIN_SESSIONS_MAX`, `MAX_DB_BYTES_MAX` in `config.py` (2^63-1), used by the loader and `ConfigStorageBody`. Test: `test_config_storage_bounds.py` (file with 5000 days, 5000 sessions, 8 TiB cap saves another field; 2^63 still 422). Mutant killed.
- **R78-7 LOW** (store sites) fixed: `stats_present(conn)` in `test_store_lines_plan.py` replaces the six copies (five there, one in `test_store_sessions.py`), with `test_the_stats_probe_sees_an_analyze` as the control (mutant: probe blind, killed); `test_sessions.py:82` asserts the closed range holds `in a`.
- **Owner ruling, bundle slot before sweep lock**: `_run_export(..., lock=, prepare=)` claims the slot (`_admit`) before the lock, builds the members under it, and gives the slot back if `prepare` refuses or raises. Tests: `test_retention_runs_while_a_bundle_waits_for_a_slot`, `test_a_bundle_refused_after_its_slot_gives_the_slot_back`. Mutants killed.
- **Owner ruling, copy interrupted at open**: `_ExportJob.run` reports an abandoned job's `OperationalError` as `interrupted`. Reproduced first: a stop landing 2 or more VM steps into the ATTACH reads `unable to open database: <path>`. Test: `test_an_abandon_landing_while_attach_opens_the_capture_reads_interrupted` (positive control: some raw errors were the open failure). Mutant killed.
- **Owner ruling, SPEC `last_ms` "by id"**: done.
- **Owner ruling, pre-check a slot**: `GET /sessions/{ref}/export?check=1[&wait=1]` answers `{"ok": true}` or the exact 400/503 the request would get, taking no slot. Test: `test_check_answers_the_refusal_without_building`. Two mutants killed.
- **Owner ruling, page version for the reload badge**: already served. `index.html` is stamped `<meta name="mcuscope-version">` by `_NoCacheStatic.file_response` (tests in `test_webui.py`, `statusbar_reload_stamp.test.mjs`); nothing added beyond the header below.
- **Orchestrator addition, version header**: `_VersionHeader`, outermost, adds `X-Mcuscope-Version: <mcuscope.__version__>` to every `http.response.start` and `websocket.accept`. Starlette sends an unhandled error's 500 from `ServerErrorMiddleware`, outside every app middleware, so `_unhandled_error` adds it (and the framing denial, which that 500 also lacked). Tests: `test_server_version_header.py` (route walk, 404, 422, 400, streamed 200, a guard's 403, a 500, the WS accept). Four mutants killed, including the middleware moved inside the guards.

## CHANGELOG

- A `match` regex whose counted repeats expand past 100,000 is refused with a 400 (`match regex too large: ...`); patterns compile off the event loop.
- **Breaking:** user regexes read `\d \w \s \b` as ASCII, as the web UI does, on every endpoint.
- **Breaking:** a relative `storage.db_path` resolves against the config file's directory, and `/status` reports the absolute path.
- `/status`, `GET` and `PUT /plotjuggler` report `target`, the address datagrams go to.
- **Breaking:** `POST /marker` strips its text and refuses a blank one (422).
- **Breaking:** `/wait` and `/assert` refuse `send_mode` without `send` (400).
- **Breaking:** integer, number and boolean query and path parameters take only their plain forms: `+2`, ` 3 `, `1_0`, `yes`, `on` are 422.
- `/can/frames` answers `next_since_id`; a follow on a port without frames stays cheap.
- `GET /sessions/{ref}/export?check=1` reports whether an export would be refused, without building it.
- Every response carries `X-Mcuscope-Version`.
- Saving ports from the web UI keeps unknown keys and comments inside each `[[ports]]` table.
- `PUT /config/storage` accepts every value the config file may hold (no more 3650-day or 1000-session caps).
- Plot export and bundle decode anchoring, the hourly age sweep under a session floor, and a bundle waiting for a build slot no longer cost a full sort, a walk of every protected row, or a blocked retention sweep.

## Needs another batch

- `tests/test_server_exports.py::test_an_abandoned_job_removes_its_files_whichever_side_ends_last[finished first]` fails: `abandon()` of a finished job now removes on a worker. Replace the final assertion with `assert poll(lambda: not os.path.exists(made[0]) and live == set(), 5)` for that case.
- `tests/test_plotjuggler.py` pins the old shape in four tests: add `"target"` (`None` while disabled, `"127.0.0.1:9333"` / `"127.0.0.1:9777"` when enabled on those dests) at lines 307, 313, 321, 357, in `test_startup_with_dead_resolver_serves_disabled` (`None`) and `test_cli_json_is_one_object` (`"127.0.0.1:9777"`).
- `tests/webui_js/exportdlg_guards.mjs` (the export double) no longer matches the daemon; `test_webui_js.py::test_export_guard_double_agrees_with_the_daemon` lists every mismatch. The double must mirror:
  - `limit`, `last_ms`, `id_to`, `bus`, `session_id`: `^[0-9]{1,20}$` else `<name>: must be ASCII digits (got '<v>')`; `since_id`, `decimate`: `^-?[0-9]{1,20}$` else `must be ASCII digits with an optional -`.
  - `since_ts`, `until_ts`: `^-?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]{1,4})?$` or, case-insensitively, `-?(inf|infinity|nan)` (these still reach the 400 `X must be a finite number`); else `must be an ASCII decimal number`.
  - `decode`, `changes`, `wait`, `data`, `check`: exactly `true|false|1|0`, else `must be true, false, 1 or 0`.
  - The grammar runs before the bounds, so `id_to=-1` now reads `id_to: must be ASCII digits (got '-1')`, not the `ge` message; the double's `BOOLS` list and lax `pyInt` go.
- cli: `mcu can dump -f` advances on `next_since_id` (D-7); the `X-Mcuscope-Version` check (header name exactly `X-Mcuscope-Version`, value `mcuscope.__version__`; refuse when missing or older than `DAEMON_MIN_VERSION`); `mcu wait`/`mcu assert` with `--raw` and no `--send` is accepted and ignored client-side (class 31, the daemon now refuses an explicit `send_mode` without `send`, but the CLI never sends it then); SPEC 4 / `AI_GUIDE` if the CLI surfaces `target` in `mcu plotjuggler`.
- webui-panes: the `.db` preflight in `state.js` (`preflight`) can call `GET /sessions/<ref>/export?check=1&wait=1` instead of `/sessions?name=`: it answers `{"ok": true}` or the refusal JSON the navigation would get (400 `no such session: X`, 503 `too many session exports waiting for a slot; try again shortly`). SPEC 9.1 (the Settings sessions line saying it "checks `GET /sessions?name=<id>`") then changes.
- webui-settings: `settings.js` storage checks (`retention_days > 3650` refused as "Retention must be 1-3650 days", and the 2^42-byte cap bound) should mirror the loader: retention >= 1, min_sessions >= 0, cap 0 or >= 1 MiB, uppers 2^63-1.
- daemon-process (D-9): `test_e2e.py::test_empty_cmd_is_client_error_not_500` now fails for `"\t"`: `format_command` strips U+0020 only, so a tab-only `/cmd` reaches the board (`badcmd`, 200) instead of the 400. The ruling says a tab is a token byte; that test's `\t` case needs updating, or the refusal kept for a command with no non-space byte.
- `test_webui_js.py` and the whole node suite were not run beyond the double test; the JS side of D-5 (pane `\s`) is webui-panes'.
- REVIEW.md candidate class (orchestrator): "a response header added by middleware misses the 500 Starlette sends outside all middleware". Bit: `_FrameDenial` (SPEC 3.1 "every HTTP response") never reached an unhandled error's 500. Sweep: every `add_middleware` that amends `http.response.start`, against `_unhandled_error`.

## Needs Windows

- `test_config_db_path.py` (relative `--config` and `abspath` joins; Windows drive-relative `db_path` like `D:cap.db` resolves against that drive's CWD, not the config dir, untested).
- The ATTACH-open race text (`unable to open database`) and the retention VM-step counts come from Linux SQLite 3.x; `test_an_abandon_landing_while_attach_opens_the_capture_reads_interrupted` asserts the raw open failure occurs at least once, so a SQLite that never produces it fails there rather than passing vacuously.

## Needs a browser

- None directly; the webui halves above own their checks.

## Not verified

- Whole suites were not run (brief). Related files run together: 65 files (`test_server_*`, `test_config_*`, `test_store_*`, `test_session*`, `test_export*`, `test_plot*`, `test_assert`, `test_security`, `test_e2e`, `test_webui`, `test_pane_regex_dialect`, `test_cli_can_dump`, `test_cli_follow`): 1127 passed, 7 failed. Five are the pins listed under "Needs another batch"; one was a race in my own recorder (fixed, 5 clean reruns); one is `test_e2e.py::test_empty_cmd_is_client_error_not_500`, below.
- Retention and `/can/frames` step counts were measured on small synthetic captures, not the 6M-line perf DB.

## Surprises

- `regex` compiles holding the GIL: `(?:\X{300}){300}` (90,300, inside the bound) stalled a 5 ms heartbeat for 261 ms, so compiling in a thread does not shield the loop from the C compile; the bound is what limits it. Other atoms cost 0.1 to 0.65 us per unit (at most ~65 ms at the bound), `\X` about 2.7. If ~0.25 s is too much, weight `\X` or lower the limit (owner call).
- FastAPI silently drops `BeforeValidator` metadata from a non-optional `Annotated` param written `x: T = Query(...)`.
- R39-1's second site was not a defect (`Future.cancel()` disarms the log).
- D-6 (host marker: `str.strip`) and D-9 (firmware and `!m`: U+0020 only) give two whitespace sets for "marker text"; both are rulings as written, flagged in case one set was meant.

## The two questions

1. Least confident: that `repeat_expansion` is an upper bound for every pattern `regex` accepts. Rechecked by driving, not reading: 27 fixed cases, every scanner branch mutated, the verbose smuggle, `\Q` (unsupported, a compile error), fuzzy `{e<=1}`, `(?r)`, `(?f)`, `(?i)` folds, captures, lookarounds and backrefs inside nested repeats, each compared against the real compile time. Residual: a syntax neither enumerated nor verbose/V1 could still mislead the scan; the per-unit cost table above bounds what a miscount can cost.
2. What was not thought about: the sibling surfaces of each change. Found this way: the 500 path missing both headers (fixed here), the FastAPI validator drop (fixed, walk test), a bundle whose `prepare` refuses keeping its slot (fixed, test), the sweep bound versus rows stamped in the past (fixed, writer reset). Left for others: the CLI and web UI mirrors listed under "Needs another batch".
