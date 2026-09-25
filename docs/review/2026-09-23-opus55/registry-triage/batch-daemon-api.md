# Batch daemon-api

Read `docs/REVIEW.md` "Fix batches" and the class headings before starting (daemon loop and paging rules live there).
Owner-pick items use the recommended option in `decisions.md` unless the owner answered otherwise.

## Files

- Source: `host/mcuscope/server.py`, `store.py`, `config.py`, `pjstream.py`.
- Tests you may edit: `test_server_request_validation.py`, `test_store_lines_plan.py`, `test_sessions.py`, `test_store_sessions.py`, `test_plot.py`, `test_store_plot_summary.py`, `test_store_fastpaths.py`.
- New tests go in new files (for example `test_server_regex_budget.py`, `test_config_writeback.py`, `test_store_retention_walk.py`).
  Do not edit `test_config_api.py`, `test_server_scope.py`, `test_store_writer.py` (tests-daemon owns them).
- Docs: SPEC 3.3, 3.4, 3.5, 3.7; the `server.py`, `store.py`, `config.py`, `pjstream.py` sections of `docs/ARCHITECTURE.md`.

## Findings

### R1-1 HIGH, owner-pick D-1: user regexes compile on the event loop, unbounded
- Checked: `_check_match` (server.py:3347), `_do_wait` (:2628), `_compile_patterns` (:2990) call `regex.compile` inline after only `MAX_MATCH_LEN`. Re-timed here: `(?:(?:a{100}){100}){100}` compiles in 0.38 s under a 1.5 GB cap.
- Fix (D-1 option A): one helper that bounds the expanded size before compiling.
  - Scan the pattern for counted quantifiers (`{m}`, `{m,n}`, `{m,}`) and group nesting; refuse when the product of counts along any nesting path exceeds a named limit (100,000 keeps `\d{65535}` legal).
  - 400 text names the rule, e.g. `match regex too large: nested repeats expand to N (max 100000)`.
  - Then compile off the loop (`asyncio.to_thread` or the read pool) in all three sites.
- Test that fails on revert: `/lines?match=(?:(?:a{100}){100}){100}`, `/wait` and `/assert` with the same pattern answer 400 with that text; `\d{65535}` is accepted (positive control); a recorder on `regex.compile` finds no running loop in its thread.
- SPEC 3.4: state the expansion bound beside the 200-character cap.

### R1-2 LOW: finished export unlinked on the loop
- Checked: `_TempFileResponse.__call__` finally (server.py:2958) and `_ExportJob.abandon()` after a finished build (:2849) call `os.unlink` on the loop.
- Fix: `await asyncio.to_thread(_remove_export_file, ...)` in the finally; `abandon()` hands a finished job's removal to a worker.
- Test: recorder replacing `_remove_export_file` asserts `asyncio.get_running_loop()` raises in its thread after a session export download.

### R17-1 MEDIUM, owner-pick D-3: relative `db_path` reported as typed and re-resolved by restart
- Checked: `resolve_db_path` returns `os.path.expanduser(raw)` (config.py:156); `/status` `db_path` and the banner print it.
- Fix (D-3 option A): a relative `storage.db_path` resolves against the config file's directory (mcuscoped has no db-path flag); `resolve_db_path` always returns an absolute path.
- Test: config `db_path = "rel.db"` in dir A, loaded with CWD B: `resolve_db_path` is `A/rel.db`; `/status` `db_path` is absolute.
- SPEC 3.3: say how a relative `db_path` resolves.

### R17-3 LOW: PlotJuggler reports the requested dest, not where datagrams go
- Checked: server.py:1108, :1391, :1411 report `pj.dest`; the sockaddr from `_resolve` (pjstream.py:74) is only logged.
- Fix: the streamer keeps the resolved target; `/status` `plotjuggler`, `GET` and `PUT /plotjuggler` add `target` (`"[::1]:9870"`, null while disabled).
- Test: `getaddrinfo` patched to answer `::1`; `configure(True, "localhost:9870")`; `GET /plotjuggler` has `dest` localhost and `target` `[::1]:9870`.
- SPEC 3.7 and the `/status` field list in SPEC 3.4.

### R19-2 LOW, owner-pick D-5 (daemon half): the pane's JS dialect and the daemon's Unicode classes disagree
- Checked: `_make_regexp` (store.py:453) compiles with `regex` defaults, Unicode-aware for `\d \w \s \b`.
- Fix (D-5 option A): one flags constant for every user pattern, `regex.ASCII`, used by `_make_regexp` and the three server compile sites. The CLI follow uses the same flags (cli batch); the pane side and the fixture are webui-panes'.
- Test: `/lines?match=\d&chan=marker` no longer returns a marker `reading ٣`; an ASCII digit still matches.
- SPEC 3.4: the match dialect is ASCII for `\d \w \s \b`.

### R19-3 LOW, owner-pick D-6: host marker text rule differs per entry point
- Checked: `MarkerBody.text` (server.py:307) is min_length 1 with no strip; `SessionBody.name` strips; the web UI trims.
- Fix (D-6 option A): a validator on `MarkerBody.text` strips with `str.strip` and refuses a blank text, as `SessionBody` does.
- Test: `POST /marker {"text": "   "}` is refused; `"  x  "` is stored as `x`.
- SPEC 3.4 `/marker`.

### R20-1 MEDIUM: two-name plot export sorts every match before LIMIT 1
- Checked by explain here: `first_export_line_id` with `names=[temp, volt]` and `port` plans `SEARCH l USING INDEX idx_lines_port_id ... USE TEMP B-TREE FOR ORDER BY`; one name uses `idx_plot_name_line`.
- Fix: drive from `plot_points` for any number of names (`plot_points pp CROSS JOIN lines l`, or a per-name `UNION ALL` of `LIMIT 1` seeks), in `first_export_line_id` and `export_sids`. Explain both with 1, 2 and 5 names, with and without `port`.
- Test: a plan test on the statement the daemon issues (`captured_plan`) with two names plus `port`: no `TEMP B-TREE`, driven by `idx_plot_name_line`. Also for `export_sids`.

### R20-2 MEDIUM, owner-pick D-7 (daemon half): `can dump -f` on a quiet port rescans from its stale watermark
- Checked: `query_can_frames` (store.py:2178) walks `cf.rowid > since` with `l.port` filtered per row; a follow that never sees a frame keeps `since_id=0`.
- Fix (D-7 option A): `/can/frames` answers `next_since_id`, the highest line id the answer covers, read in the same snapshot as the frames (the snapshot's `MAX(id)` when not truncated). The CLI half advances on it.
- Test: frames on port A only; `port=B&since_id=0` answers `frames: []` and `next_since_id` = newest line id; the next query from it steps a small, fixed number of VM steps (progress-handler count) whatever the frame count.
- SPEC 3.4 `/can/frames`.

### R20-3 MEDIUM: hourly age sweep walks every protected expired row on the loop
- Checked: `_delete_expired_chunk` (store.py:3071) selects `ts < ? AND id < ? ORDER BY ts`; with the floor protecting the expired range the ts index walk rejects every row.
- Fix: remember the last age sweep's `(cutoff, floor_id)`. While `floor_id` has not risen, bound the walk below by the previous cutoff (`ts >= prev_cutoff AND ts < cutoff`): everything older and below the floor was already deleted. A risen floor, or the first sweep, walks the whole range once. `delete_before_ts` (purge) keeps the unbounded walk.
- Test: a capture with many expired rows inside a protected session; the second sweep's VM step count (progress handler) is a small fraction of the protected row count. Second case: the floor rises and the newly unprotected expired rows are deleted.

### R20-4 LOW: two plan tests explain a hand-written copy
- Checked: `test_sessions.py:906` and `test_store_lines_plan.py:503`.
- Fix: capture the issued statements (extend `captured_plan` to return every statement, or pick the one needed) and explain those.
- Revert-verify: change the real statement at store.py:1415 to a non-indexed order; the fixed test fails.

### R20-5 LOW: a plan pin on a method no handler runs
- Checked: `Store.query_plot_channels` (store.py:2264) has no caller in `mcuscope/`; tests use it as the summary's oracle.
- Fix: delete the plan pin (`test_store_lines_plan.py:306-331`; the daemon path is pinned by `test_store_plot_summary.py:84-103`); move `query_plot_channels` out of `store.py` into `test_store_plot_summary.py` as the oracle and import it in `test_plot.py` and `test_store_fastpaths.py`.
- The `docs/REVIEW.md` class 20 example text is firmware-packaging's.

### R22-4 LOW, owner-pick D-8: query and path params use lax parsing
- Checked: `DELETE /sessions/+2` deletes session 2 (driven by the leg); request bodies are strict (server.py:210), query/path params are not.
- Fix (D-8 option A): `Annotated` types for every int, float and bool query/path param (sweep H list, `registry-15-28.md` class 22): ints ASCII digits (a leading `-` only where negatives mean something), floats ASCII decimal or exponent and finite, bools `true`, `false`, `1`, `0`.
- Test: `DELETE /sessions/+2`, `/lines?limit=1_0`, `DELETE /sessions/3?data=yes` answer 422 and delete nothing; `DELETE /sessions/2` still works.
- SPEC 3.4: one line on the parameter grammar.

### R27-7 MEDIUM (FD-2): the atomicity test fails before the destructive step
- Checked: `FailingCopy` (test_sessions.py:766) raises on `INSERT INTO SESSIONS_AUTOINC`, before `DROP TABLE sessions` (store.py:394).
- Fix: parametrize the fault over the insert and `ALTER TABLE SESSIONS_AUTOINC RENAME`.
- Revert-verify: `conn.commit(); conn.execute("BEGIN IMMEDIATE")` after the DROP fails the rename case.

### R28-4 LOW: asserts inside a thread target
- Checked: test_store_fastpaths.py:231-232.
- Fix: collect results in the thread, assert on the main thread.

### R29-1 LOW: `/marker` port-grammar guard revertible in silence
- Checked: test_server_request_validation.py:263-266 asserts `"port" in error`, which `_unknown_port` also satisfies.
- Fix: assert text unique to the guard (`invalid port:`).
- Revert-verify: guard to `if False:` fails the test.

### R31-1 LOW: `send_mode` without `send` accepted and ignored
- Checked: `_do_wait` (server.py:2614) and `_do_assert` (:3041) refuse `eol` without `send`, not `send_mode`.
- Fix: refuse `"send_mode" in body.model_fields_set and body.send is None` with `send_mode applies to send; set send too`.
- Test: `/wait` and `/assert` with `send_mode: "raw"` and no `send` answer 400 with that text.
- SPEC 3.4 `/wait` and `/assert`.

### R39-1 LOW: a raced future's exception never retrieved on cancellation
- Checked: `_until_stopped` (server.py:2413) skips a done `work`; `_admit_and_build` (:2926) cancels an already failed `built`.
- Fix: on the exceptional exit, call `.exception()` on a done, not-cancelled future in both.
- Test: `work` resolves with RuntimeError in the same tick the outer task is cancelled; after `gc.collect()` the loop's exception handler recorded nothing (positive control: without the cancel, the RuntimeError is raised to the caller).

### R45-1 LOW: `save_ports` drops per-port keys the model does not carry
- Checked: `save_ports` (config.py:661) builds a fresh array of tables.
- Fix: update each existing `[[ports]]` table in place, matched by alias, keeping unknown keys and comments; append new ones, drop removed ones.
- Test: a config with `future_port_key` under a port; `save_ports(p, load_config(p).ports)` keeps it; SPEC 3.3's promise is already written.

### R71-1 LOW, owner-pick D-17 (daemon half): loader and API bound storage fields differently
- Checked: loader bounds (config.py:424-431) are `_INT_MAX`; `ConfigStorageBody` (server.py:335-338) bounds 3650 days, 1000 sessions, 2^42 bytes.
- Fix (D-17 option A): one set of named bounds in `config.py`, used by the loader and by `ConfigStorageBody`; the dialog half mirrors them (webui-settings).
- Test: a file with `retention_days = 5000`; `PUT /config/storage` sending it back with another field changed answers 200.
- SPEC 3.3: the bounds.

### R78-7 LOW (store sites): absence checks with no positive control
- Sites: `test_store_lines_plan.py:44, 356` (and the same query at `:415, :464, :570`), `test_store_sessions.py:125`, `test_sessions.py:82`.
- Fix per site as `~/tt-data/mcuscope-2026-09-24/registry-leg/71-80/verdicts_py2.md` says: e.g. show `sqlite_stat1` appearing in `sqlite_master` on the same connection after an `ANALYZE` control, and show the closed range's `query_lines` returning the session's own rows.
- The other R78-7 sites are tests-daemon's.

## Added by owner rulings 2026-09-25 (see `decisions.md`, last sections)

- Bundle export: acquire the export slot before `store._sweep_lock`; retention must not wait on a queued bundle. Test: retention runs while a bundle waits for a slot.
- Copy interrupted exactly at open: report the same "interrupted" outcome as other cut-offs.
- SPEC 3.4: `last_ms` with an upper bound counts back from the newest line by id at or below it; say "by id".
- Server halves (coordinate through your report; webui batches implement the UI):
  - a way for the web UI to pre-check a free export slot before a `.db` navigation (extend the existing download preflight if it fits), answering the refusal the navigation would get;
  - the version of the daemon serving the page, readable by the page (for example in `index.html` as served, or a response header), for the reload badge.
