# Fix batch server4, 2026-10-06

Scope: `host/mcuscope/server.py`, its tests, SPEC 3.1 and 3.4 routes.
Revert-verification: `~/tt-data/mcuscope-2026-10-05/fix4-server/revert.py` mutates one branch at a time, runs the named test file, restores (`cmp` against `server.py.fixed` confirmed the restore); logs `revert.log`, `revert2.log` beside it.
All 28 mutations below are caught.

## V92-1 export temp copies keyed and placed by file identity

- `server.py:1220` `_db_realpath` (`realpath`, `:memory:` as is); lifespan `:540` computes `app.state.db_realpath` once off the loop; export key, orphan sweep and `_ExportJob` (`:3269`) all take it instead of the spelled path.
- Test: `tests/test_server_db_identity.py` `test_a_retargeted_symlink_does_not_sweep_the_other_captures_copies`, `test_a_respelled_capture_sweeps_its_own_leftovers` (a real export left in place by stubbing `_remove_export_file`).
- Revert: key, sweep dir and job dir each mutated back to `resolve_db_path(...)`: all caught.

## V92-2 restart_required compares files, not spellings

- `server.py:1226` `_same_path` now compares `normcase(realpath)` of both sides (blocking, run via `asyncio.to_thread`); GET `/config` `:1499` and PUT `/config/storage` `:1597` compare the saved path against `app.state.db_realpath`, the file opened at start, so a link retargeted since start does report a restart.
- Test: `test_restart_required_compares_files_not_spellings` (symlink alias, link to another file, retarget after start, running via the link).
- Revert: `_same_path` back to `normpath`, and GET/PUT running side back to `resolve_db_path(running)`: all caught. The existing `test_db_path_comparison_is_case_and_separator_insensitive_on_windows` still passes unchanged.

## V95-2 token failure table eviction is announced

- `server.py:895-903`: `_prune` logs one warning per eviction episode (`token guard: N addresses with failed tokens; evicting the oldest, whose lockouts end early`); the episode ends at the first prune that needs no forced eviction. Log only, no `/status` counter (the guard is ASGI middleware with no handle on app state; the sweep's fix offered "and/or").
- Test: `tests/test_server_token_guard_eviction.py` (no line before the bound, exactly one across ~1000 evictions, a second after all records expire and a new spray).
- Revert: drop the reset, drop the once-guard, drop the log: all caught.

## V96-1 a 422 is bounded whatever the body

- `server.py:995-1013` `_ERRORS_MAX = 5`, `_excerpt` (whole up to 80 characters, else `<80>... (N characters)`), `_error_list` (`; and N more`).
- `_validation_error` `:637`/`:646` cuts `where` (an unknown field's key) and caps the list; the undeclared-query-parameter 422 `:442` does the same.
- Test: `tests/test_server_error_excerpts.py` `test_a_422_lists_five_errors_and_counts_the_rest_with_unknown_keys_cut` (3001 unknown keys, 53 KB body), `test_an_unknown_key_named_in_a_422_is_cut`, `test_unknown_query_parameters_are_cut_and_counted`.
- Revert: `where`, body count, query cut, query count each reverted: all caught.

## V96-2 no client string echoed whole; WS close reason within 123 bytes

- Every listed site now formats through `_excerpt`: `:1268`, `:1280`, `:1423`, `:1434`, `:1448` (port); `:1824`, `:1871`, `:1879`, `:2026`, `:3655`, `:3712` (session ref); also `bad can id` `:2217` and `names lists X twice` `:2353`, same class, not in the sweep's list.
- `_ws_reason` `:1015` cuts a close reason to 123 UTF-8 bytes on a character boundary, ending `...`; used on the 1008 refusal `:2481`.
- Test: `test_server_error_excerpts.py`: one parametrized case per HTTP site, `test_a_session_deleted_under_a_bundle_is_named_cut` (the bundle re-read path, via a 128-character session name), `test_a_ws_close_reason_fits_the_close_frame` (ASCII: excerpt alone; three-byte characters: byte cut), positive controls for short values.
- Revert: each site and both WS steps reverted: all caught.
- Deliberately not changed (would be redundant, no test could catch it): `can id out of range` (element is at most 18 characters after `parse_hex_int`), the 1001 close reason (a store constant), `/send`'s repeat `no such port` at `:2874` (an alias that was attached), `DELETE /sessions/{id}` (int path).

## Existing tests edited

- `tests/test_server_raced_futures.py` `test_an_export_build_that_failed_as_its_handler_was_cancelled_is_retrieved`: its fake `app.state` gains `db_realpath` (`_build_admitted` reads it instead of `config`); the now-unused `config` field and `Config`/`StorageConfig` import dropped. Passes.

Files run unchanged and passing: `test_config_api.py` (40), `test_server_exports.py` (24), `test_server_guards.py` (35), `test_server_ws.py` (4), `test_server_request_validation.py` (40), `test_session_bundle.py` (16), `test_config_api_revision.py` (16), and every other `test_server_*.py`, `test_sessions.py`, `test_e2e.py`, `test_export_lines_can.py`, `test_assert.py` (log `existing2.log`).

## SPEC edits

- 3.1 token guard: the failure table caps at 1024 addresses, evicts oldest, logs one warning per episode.
- 3.4 errors: a 422 lists at most five errors then `; and N more`; a client value in any error is echoed whole up to 80 characters, else cut with `... (N characters)`.
- 3.4 `/ws`: the 1008 reason is cut to 123 bytes, ending `...`.
- 3.4 `/sessions/{ref}/export`: temp copy directory and `<key>` come from the resolved (symlink-followed) capture path.

## Guide wording

None needed: the guide does not quote these messages. Optional for `AI_GUIDE` errors: "A value the daemon names in an error is cut past 80 characters, `... (N characters)`; a 422 lists five errors, then `and N more`."

## Changelog

- Error messages cut any client-supplied value past 80 characters and list at most five validation errors, so an oversized request is not echoed back.
- `/ws?port=` with a long alias closes 1008 with a reason that fits the close frame, instead of failing the handshake handler.
- `restart_required` treats a symlink or other spelling of the running capture as the same file, and a link retargeted since start as a different one.
- Export temp copies are keyed and placed by the capture's resolved path, so two daemons sharing a path spelling no longer sweep each other's copies.
- The daemon logs a warning when a spray of failed-token addresses forces the token guard to evict live lockouts.

## Not done

- SPEC 3.3/3.3.1 (not owned): "`restart_required: true` whenever a saved value differs from the running one" could add "a db path differs when it resolves to another file than the one opened at start". Optional.

## Doubts

- V92-2 reads the saved path's `realpath` at request time against the start-time realpath: a saved path whose parent directory is a not-yet-mounted network share resolves lexically and may report a restart that a later mount would not. Not tested.
- Windows: the symlink tests skip without the privilege; `normcase(realpath)` on Windows was not run.
- The token guard log is announced but not counted on `/status`; if the owner wants a counter, the guard needs a handle on app state.
