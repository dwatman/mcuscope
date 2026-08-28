# Fix batch A1 (store.py): RG-F1, RG-F2, RG-F5, CD7

HEAD at start: `0b5eed9` (verified with `git log -1 --oneline`). Nothing committed.

Files touched:
- `host/mcuscope/store.py`
- `host/tests/test_review_r2_store.py` (new, 7 tests)
- `docs/SPEC.md` 3.5 sessions index list (2 added lines, surgical)
- `host/tests/test_hardening.py` (2 lines, forced by CD7 - see below; flagged because it is outside my stated file list)

## RG-F1 auto_vacuum read-back

`start()` now reads `PRAGMA auto_vacuum` back after applying it and warns when it is not 2, mirroring the `journal_mode` read-back three lines down (same `:memory:`/"" exemption). The message names the consequence: freed pages are never handed back, the file plateaus at its high-water mark until someone runs VACUUM by hand. No automatic VACUUM.

## RG-F2 fast-fail counted

`submit_line`'s "store writer is not running" refusal now calls `_fail_write(None, exc)` before raising. `_fail_write`'s `item` parameter became `_WriteReq | None`: passing None counts the loss with no future to resolve, which avoids creating a throwaway future whose exception nothing retrieves (the class-39 shape). The docstring's invariant now holds and its wording covers the refused-before-queued case.

## RG-F5 active_session partial index

`CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(id) WHERE ended_ts IS NULL;` added to SCHEMA (with a why comment) and to SPEC 3.5.

Migration: `conn.executescript(SCHEMA)` runs on every open, so `IF NOT EXISTS` is the in-place migration path for existing captures, exactly as the two earlier index migrations rely on. One trap found and closed: `_rebuild_sessions_for_autoincrement` DROPs the `sessions` table, which takes its indexes with it, and it recreated only `idx_sessions_name`. It now drops and recreates both from `_schema_statement`, so a legacy capture does not come out of the AUTOINCREMENT rebuild one index short. Tested.

Plan, no active session, 20 closed sessions, no `sqlite_stat1`: `SCAN sessions USING INDEX idx_sessions_active` (the partial index holds at most one row), no temp b-tree. Was `SCAN sessions`.

## CD7 dead regexp closure

The `conn.create_function("regexp", ...)` at `Store.start()` is deleted, replaced by a comment saying why the loop connection deliberately has none and that the three live paths build their own. A direct `query_lines(match=...)` on the loop connection now raises `sqlite3.OperationalError: no such function: REGEXP` instead of a delayed `TimeoutError`.

Consequence: one existing test *was* such a direct caller. `test_hardening.py::test_since_ts_seeks_by_id_rather_than_scanning_the_table` drives `store.query_lines(since_ts=..., match="l")` on the traced loop connection deliberately (the plan must come off that connection). Two lines added there: import `_make_regexp` and register it on `store._conn` inside the test, with a comment. No production caller exists.

Match tests run after the change, all green:
- `tests/test_e2e.py -k match` (2 passed)
- `tests/test_hardening.py -k "match or budget"` (3 passed)

## Revert-verification (mutation -> outcome)

| Fix | Mutation | With fix | Reverted |
|---|---|---|---|
| RG-F1 | delete the read-back+warning block from `start()` | 7 passed | `test_a_pre_existing_capture_says_so_when_auto_vacuum_did_not_take` FAILS |
| RG-F1 (noise half) | force `av_row = None` so it warns always | 7 passed | `test_a_fresh_capture_gets_incremental_auto_vacuum_and_stays_quiet` FAILS |
| RG-F2 | restore the bare `raise StoreError(...)` | 7 passed | `test_a_line_refused_by_a_dead_writer_counts_as_a_write_error` FAILS (write_errors 0) |
| RG-F5 | remove the index from SCHEMA and the rebuild | 7 passed | 3 FAIL: plan check, migration, rebuild |
| RG-F5 (rebuild half only) | index in SCHEMA, dropped from the rebuild list | 7 passed | `test_the_autoincrement_rebuild_keeps_every_session_index` FAILS |
| CD7 | re-add `create_function("regexp", ...)` in `start()` | 7 passed | `test_the_loop_connection_carries_no_regexp_function` FAILS |

## Gates run (from `host/`)

- `uv run python -m pytest tests/test_review_r2_store.py tests/test_sessions.py tests/test_hardening.py -q` -> 119 passed
- `uv run python -m pytest tests/test_e2e.py -k match -q` -> 2 passed
- `uv run python -m pytest tests/test_hardening.py -k "match or budget" -q` -> 3 passed
- `uv run python -m ruff check mcuscope/store.py tests/test_review_r2_store.py tests/test_hardening.py` -> clean
- No em/en dashes in any touched file (grep -P clean)

## Not mine, for the orchestrator

`tests/test_capture_lock.py` has 2 failures in the shared worktree: `test_a_second_daemon_on_one_capture_is_refused_before_it_serves` and `test_the_override_downgrades_the_refusal_to_a_warning` both assert the refusal text on **stdout**, and the concurrent C2 batch (F11) moved daemon startup refusals to `sys.stderr` in `daemon.py`. Confirmed by reading the working-tree diff of `daemon.py`; unrelated to store.py, and reproduces with my changes reverted in spirit (no store path is involved). That batch owns updating those two assertions.
