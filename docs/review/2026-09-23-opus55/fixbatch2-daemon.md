# Fix batch 2: daemon slice (2026-09-24)

HEAD: `b889e2b8fb848866054da6ba09cd2e651d9f69c7` (working tree also carries the other agents' uncommitted edits).
Scratch: `~/tt-data/mcuscope-2026-09-24/fixbatch2-daemon/` (`host/` private copy, `mutate.py`, `logs/mutants.log`, `logs/files.txt`).
Revert-verify: `mutate.py` applies each mutant to the copy (imports asserted to resolve there via `PYTHONPATH`), runs the named file, restores. All 15 KILLED.

## Per finding

### 1. Medium: `wait=1` waiter and build ignore a client disconnect
- `server.py:2869` `_client_left`: awaits `request.receive()` until `http.disconnect`. `_run_export` runs it as a task beside admission and the build, cancelled in `finally`.
- `server.py:2879` `_admit_and_build`: a waiter parks on `asyncio.wait((freed, gone))`, returns 499 `client disconnected` if gone. The waiter counter is `app.state.export_waiters` (`server.py:471`).
- Waiter cap `EXPORT_WAITERS_MAX = EXPORT_WORKERS * 4` (`server.py:2717`). Past it: 503 `too many session exports waiting for a slot; try again shortly`, `wait=1` or not.
- Build phase: races `built` against `gone`. On disconnect it cancels the pool future (a queued build never starts), calls `job.abandon()` and returns 499. The cancelled-handler path now cancels `built` explicitly (`server.py:2916`), because `asyncio.wait` does not cancel it the way `await` did.
- Tests: `tests/test_fixdiff2_daemon_exports.py`. Four of them run over real uvicorn (`stack`) with httpx client timeouts: waiter leaves (no build, no wait left on `export_freed`, no temp file); admitted build abandoned (`on_open` raises, no file); queued build never starts; cap 503. One in-process test covers a queued build whose handler is cancelled.
- Removed `test_a_waiter_that_disconnects_claims_no_slot_and_leaves_no_file` (in-process cancel) from `test_server_fixdiff_exports.py`.
- Mutants 1a-1h KILLED (ignore gone while waiting, no return after gone, no cap, build ignores gone, gone build not cancelled, cancelled handler leaves queued build, `freed` not cancelled, waiters not decremented).

### 2. Low: `_top_ts` never falls after a purge
- `store.py:1678` in `_delete_lines` (every lines delete: `delete_range`, `delete_before_ts`, retention, size cap): after a non-empty delete, `_top_ts = MAX(ts)` (index seek).
- No extra episode-close code. An open episode closes at the next in-order batch with its true pre-purge count.
- Test: `tests/test_fixdiff2_daemon_store.py::test_a_purge_of_the_rows_ahead_ends_the_late_count[range|before_ts]`. After the purge, in-order rows give only `back in time order; 1 committed`, and a 30 s inversion still opens a new episode.
- Mutant 2 KILLED.

### 3. Low: CTRL_CLOSE hold shorter than the graceful wait
- `daemon.py:132` `CONSOLE_CLOSE_GRACEFUL_S = 3`. `daemon.py:306` `_serve` sets `_stdio.console_close_hook`, which lowers `server.config.timeout_graceful_shutdown` to 3 s. uvicorn reads it at shutdown (checked in 0.52 and in the 0.35 floor).
- `_stdio.py:41,93`: the ctrl handler calls the hook on CTRL_CLOSE before `interrupt_main`. `CONSOLE_CLOSE_HOLD_S = 4.5` is now named (`_stdio.py:44`). Ctrl-C and Break keep 5 s.
- Deviation from the brief: `_stdio.py` was edited, not only `daemon.py`. Only `_stdio`'s handler can tell a close from a Ctrl-C (both arrive as SIGINT).
- Tests: `tests/test_fixdiff2_daemon_console.py`. On a fake kernel32: close runs hook, then SIGINT, then the 4.5 s hold; Ctrl-C and Break do not run the hook. A fake `Server` checks that `_serve`'s hook turns 5 into 3, and that 3 leaves at least 1.5 s of the hold for the finaliser.
- Mutants 3a and 3b KILLED.

### 4. Port column, server half
- `server.py:3422`: `len({attached aliases} | set(stored_ports())) > 1`.
- Test: `tests/test_port_column_stored.py::test_the_column_rule_counts_attached_and_stored_ports_as_one_set` (renamed; the helper now takes alias lists). It adds `["b"], ["a"]` (column) and `["a"], ["a"]` (no column). The existing live `test_a_single_board_capture_has_no_port_column` still passes.
- Mutant 4 (old rule) KILLED.

### 5. Nit: loader keeps unstripped device and serial
- `config.py:469-473`: both are stored stripped, and blank becomes `None`, as PUT does. The blank check now reads the stripped values.
- Test: `tests/test_fixdiff2_daemon_config.py`. A padded serial loads as `0672FF3` and `SerialPort._resolve_device` matches it against a faked enumeration. A padded device is stripped. An all-blank device is skipped with the warning.
- Mutants 5a and 5b KILLED.

### 6. Nit: pydantic upper bound
- `pyproject.toml:45` `"pydantic>=2.0.2,<3"`. Not re-run on the floor venv (the lower bound is unchanged).

### 7. Nit: stale sim comment
- `sim.py:280`: now says `x` makes the filter pass extended frames only (a plain one standard only), and `r` is refused because no RTR match exists.

### 8. Nit: two branches no test distinguished
- `_scan_plot_summary` `own` guard: DELETED (`store.py:2370`, unconditional BEGIN/rollback). No caller holds a transaction open: the offload read connection never does, and the in-memory writer inserts and commits in one loop step. Without the guard, a caller inside a transaction would get a BEGIN error instead of a scan over its uncommitted rows. `test_store_plot_summary`, `test_store_plot_reads` and `test_store_fixdiff_reads` pass.
- `_check_stamp_order` done-callback: TESTED. `test_fixdiff2_daemon_store.py::test_a_notice_in_a_failed_commit_logs_no_unretrieved_exception`: the commit fails with a notice in the batch, then gc, then no "never retrieved" reaches the loop's exception handler. Positive control: a bare failed future does reach it.
- Mutant 8b KILLED.

## Test runs
- 60 related files (`server|store|config|console|stdio|daemon|e2e|export|port_column|sim_error`, no `test_cli_*`), each run alone: all pass except one ordering race in my new exports test. Fixed: it now waits on `server_state.tasks` before releasing. That file was rerun alone 4 times and passes.
- `uv run python -m ruff check .` clean.
- Whole suite not run.

## SPEC wording (not edited)
- 3.1 (`SPEC.md:341`), dependency list: add `pydantic`.
  - "... `tomlkit`, `regex`, `pydantic`."
  - Sub-bullet: "`pydantic` is fastapi's own, named to pin it to v2 (`>=2.0.2,<3`): the server uses v2 names."
- 3.4 (`SPEC.md:866`): replace the sentence with: "With `?wait=1` (on `/export` and `/bundle`) the request waits for a slot instead of the 503, with at most 8 waiting (4 per export worker); past that it is refused 503 `too many session exports waiting for a slot`. A client that disconnects while waiting starts no build, and one that disconnects during its build abandons it: the build stops and its temp file is removed."
- 3.4 port column (`SPEC.md:778`): "more than one port is attached or has stored rows" becomes "the attached ports and the ports with stored rows together number more than one".
- 3.2 (`SPEC.md:470`): "... within the roughly 5 s Windows allows: on a console close the wait for in-flight requests is capped at 3 s (5 s otherwise), so the stop completes inside it."
- 2.4 needs no change.

## CHANGELOG lines
- A session export or bundle whose client disconnects (`wait=1` waiter, queued, or building) no longer runs or keeps building a full copy. `wait=1` waiters are capped at 8, and one past the cap is refused 503.
- After a purge removes rows stamped ahead of the clock, new rows are no longer counted as committing late, and a real inversion is announced again.
- Closing the daemon's console window on Windows caps the in-flight request wait at 3 s, so the graceful stop (session close, `daemon stop` row, pid record) completes before Windows ends the process.
- The daemon's text export and bundle `lines.txt` show `[port]` when a detached board's history and a different attached board are both present.
- A config port's `device` and `serial_number` are loaded stripped, so a padded serial number matches its board.
- pydantic is bounded `<3`.

## Needs Windows
- Close the console window of a foreground `mcuscoped` while a `wait=1` export waits and a large `/lines/export` streams. Check that the capture then holds a `daemon stop` row, the session is closed, and the pid record is removed. This checks the 3 s wait plus the finaliser fitting in 4.5 s.
- `test_fixdiff2_daemon_console.py` and `test_fixdiff2_daemon_exports.py` on the Windows leg.

## Doubts
- `_stdio.py` was edited (see finding 3). It is outside the listed files, but no other agent owns it.
- A bundle holds `store._sweep_lock` across its whole `_run_export`, including the `wait=1` wait. Consequences:
  - Only one bundle parks as a counted waiter; later bundles queue on the lock, where neither the cap nor the disconnect watch reaches them.
  - While a bundle waits for a slot, retention and purge are blocked too.
  - A gone client queued on the lock is caught once it reaches `_run_export` (at worst it is admitted, then abandoned at `on_open`).
  - This predates the batch. Owner should pick whether to move the wait ahead of the lock.
- The 499 status goes to a closed socket and nobody reads it; any code would do.
- The web UI's `wait=1` download now gets a 503 past 8 waiters. It is not handled on the UI side (not my files).
- 3 s plus finaliser within 4.5 s is reasoned, not measured. The finaliser's 2 s `_bg_tasks` wait per port could exceed the remainder with a wedged store.
