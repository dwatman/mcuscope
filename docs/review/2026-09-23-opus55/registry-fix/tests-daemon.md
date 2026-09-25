# Fix batch tests-daemon (registry leg, 2026-09-25)

HEAD at start: `3f10153`. Test files only; no source file edited.
Revert-verify: `~/tt-data/mcuscope-2026-09-25/fix-tests-daemon/mutate.py` copies the tree, applies one mutant per copy, runs the named test, and reports CAUGHT or MISSED. The output is in `mutants.log` there: 26 of 26 caught.

## Findings

- R21-1 fixed. `test_e2e.py::test_lines_since_ts_excludes_what_predates_it` polls until a row is stamped strictly after `cut`, and asserts that no row at or before `cut` comes back (id sets, not a length that the growing capture can outrun).
  - Emulated 15.625 ms clock (`-p quantclock`, with an in-test assert that the clock is quantised): the old test failed 10 times in 21 runs, the fixed test 0 times in 20.
  - Mutant `since_ts` dropped in `/lines`: caught.
- R21-2 fixed. `test_assert.py::test_last_ms_window` uses a 1000 ms window and a spin past `old_ts + 1 s`, and the call that must see `recent` goes first.
  - Emulated clock plus a 50 ms sleep before each call: fixed passes, the old shape fails (`narrow` is `empty`).
- R21-3 fixed. `test_wait_repeat.py::test_an_unsendable_body_is_refused_before_the_first_write` spies `send_raw`: no call for the repeated request, one call for the plain one (positive control). The wall-clock bound is gone.
  - Mutant: pre-check `_encode_wire` in `/wait` repeat removed. Caught.
- R21-4 fixed: the `max(gaps) < 0.2` bound is dropped. The existing `>= 5` / `<= 25` write counts carry the claim. No branch to revert.
- R21-5 fixed. `test_serial_link_devices.py::test_devices_enumeration_does_not_stall_the_event_loop` holds the fake scan on an Event, which is set only after `/status` has answered. `/devices` must then answer 200.
  - Mutant: `_enumerate_devices()` called on the loop. Caught (ReadTimeout).
- R21-6 fixed, 3 sites. The alternative is now 60 s and the budget 10 s:
  - `test_e2e.py::test_malformed_seq_response_resolves_fast` (timeout_ms 60000).
  - `test_reconnect.py::test_retry_wait_returns_early_when_device_reappears` (`_retry_wait(60.0)`).
  - `test_reconnect.py::test_retry_wait_stops_promptly_while_polling` (`_retry_wait(60.0)`).
  - Mutants for the three sites were all caught: the response is not popped, `if False:` replaces the presence check, and `time.sleep` replaces the stop wait.
- R27-6 fixed: new `test_devices_names_the_by_id_link_of_the_resolved_device` (the realpath is faked, so it runs on every OS). Mutant `"by_id": None`: caught.
- R27-11 fixed: `_Sock` records the timeout of every sized read, and the test asserts that all of them were 0. Mutant `ser.timeout = 0` -> `pass`: caught.
  - Not done: moving the three `link.py` doubles to a `test_link_*.py` file. That file is outside my list, and the brief only says "consider".
- R27-12 fixed: `FloorClock.advances`, and both users assert `>= 1`. Mutant: `_window_floor` renamed to `_window_floor2` at the definition and every caller. Both tests caught, on `advances == 0`.
- R27-13 fixed: `OneStepPerExecute.cursor()` returns a cursor whose `execute` steps once. Mutant: `_reclaim_pages` via `conn.cursor().execute(...).fetchall()`. Caught (32 pages, not 2000).
- R28-1 fixed. `test_store_writer.py::test_a_dead_store_writer_fails_writes_instead_of_hanging` now suppresses `StoreError` only. Mutant: `stop_session` awaits a never-set Event when the writer is dead. Caught in 5 s (TimeoutError).
- R75-1 (Python half) fixed. Each hand list is now asserted equal to a derived one:
  - READS: OpenAPI query `port`.
  - WRITES: POST bodies with `port`, minus `/marker`, since SPEC 3.5 lets a marker omit the port.
  - `TIME_BOUND_ROUTES`: OpenAPI `since_ts` / `until_ts`, a new constant in `test_server_export_windows.py`.
  - The `limit` routes: OpenAPI `limit`; `/plot/series` keeps its own test.
  - `INT_FLAGS`: argparse actions typed `int_arg` or `int`.
  - Five mutants were caught: an entry dropped from each list, and `--flap` retyped as `int_arg`.
  - The `_resolve_port` callers were not scanned from source: `/wait` and `/assert` call it from a helper, and `/marker` names it only in a comment. OpenAPI is the derivation used instead.
- R78-1 fixed. `test_session_bundle.py::test_a_failed_build_removes_both_temp_files`:
  - Lists `mcuscope-bundle-*` during the build, as a positive control that expects `.db` and `.zip`.
  - The dead `mcuscope-session-*` glob is gone; the `mcuscope-bundle-*` glob was already there.
  - Mutant: the temp prefix changed to `bndl`. Caught.
- R78-2 fixed. `_fail_futures_only` sets the exception without clearing `_pending`, and the two future tests use it.
  - This differs from the brief's "record only": a stub that sets no exception would take away what those tests exist to drive (an unretrieved exception).
  - Each of the four `_pending.pop(seq, None)` in `send_command` was mutated to `pass` on its own: 4 of 4 caught.
- R78-3 fixed. The body moved to a helper `_unhandled_after_a_sys_row_on_a_stopped_store(reraise)`, plus a new sibling `test_the_orphan_recorder_sees_a_sys_task_that_raises`, in which `_store_sys` lets the StoreError out.
  - Mutant: the exception handler is never installed. Caught.
- R78-6 fixed. After startup the test stubs `maybe_check` (no network) and runs `monkeypatch.delenv("MCUSCOPE_UPDATE_CHECK")`, then `set_enabled(True)`.
  - It asserts True, then False after PUT false, then True after PUT true.
  - Mutant: the PUT handler skips `set_enabled`. Caught.
- R78-7 (non-store sites) fixed:
  - `test_sim_pty.py`: a bare `pty.openpty()` slave shows ECHO/ICANON/OPOST/ICRNL set.
  - `test_capture_lock.py`: the spy's `opened` list holds the lock path. Mutant: the spy does not record. Caught.
  - `test_store_writer.py`: the dying-writer test asserts one "store writer died" record. Mutant: the log line removed. Caught.
  - `test_config_api.py`: `[[ports]]` is in the text after the first save. This is a pure positive control; tomlkit writes the header, so there was no mutant.
- FA-7 fixed: `boom` records its run, and the test asserts it ran. Mutant: `trimmed = 0` in place of the size sweep. Caught.
- From daemon-process / daemon-api "Needs another batch", D-9: `test_e2e.py::test_empty_cmd_is_client_error_not_500` now loops over `""` and `"   "` only. A tab-only `/cmd` asserts 200 with `status == "err"`.
  - Mutant: `strip(" \t")` in `format_command`. Caught.
  - No other item in daemon-api.md, daemon-process.md, cli.md or firmware-packaging.md names tests-daemon or one of my files.

## CHANGELOG

None: test-only.

## Needs another batch

- None required. Optional, for whoever owns a `test_link_*.py`: move `test_socket_drain_does_not_trust_in_waiting`, `test_native_drain_reads_exactly_what_is_waiting` and `test_a_link_that_cannot_cancel_says_so` out of `test_reconnect.py` (R27-11 note).

## Needs Windows

- R21-1 / R21-2 were verified under the emulated 15.625 ms clock only, not on Windows CPython 3.10.
- `test_sim_pty.py` is POSIX-only, and the new by-id test does not touch the filesystem.

## Needs a browser

- None.

## The two questions

1. Least confident: that the R21-1 poll can always find a later row. It relies on the stack sim producing lines continuously.
   - Re-driven: 20 of 20 quantised runs passed, and the default-clock single-file run passed.
   - Also that the plugin actually loaded. An in-test assert (`time.time() % 0.015625 == 0`) proved it for both the old and the new runs.
2. Unchecked gap: other test sites wrapping an awaited step in `suppress(Exception)` (R28-1's shape).
   - Grepped `host/tests/*.py`: the only other hit is teardown in `test_cli.py:1950` (`server_close`), not an awaited step. No sibling.

## Verification commands

- Every touched file was run on its own with `uv run python -m pytest -q tests/<file>.py`, in random order: all green. Ruff is clean on all 16.
- The baseline before the edits had 1 failure: the D-9 tab case above.

## Scratch left for the orchestrator to delete

These recursive deletes need the owner's confirmation:

- `~/tt-data/mcuscope-2026-09-25/fix-tests-daemon/base/`: the 17 MB tree copy the mutants start from.
- `~/tt-data/mcuscope-2026-09-25/fix-tests-daemon/new21/`: a 17 MB copy with the quantclock and delay probes.

Kept to rerun: `mutate.py`, `mutants.log`, `plug/quantclock.py`.
