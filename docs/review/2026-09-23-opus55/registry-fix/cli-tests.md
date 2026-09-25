# Fix batch cli-tests (2026-09-25)

HEAD at start: `3f10153`. Tests only; no source, SPEC or AI_GUIDE edits.
Mutants were run in a private copy (`~/tt-data/mcuscope-2026-09-25/fix-cli-tests/copy/host`, with `PYTHONPATH` set so child `mcu` processes import it too) by `mutant.sh` in the same dir.

## Findings

| id | result | test that fails with the fix reverted (mutant) |
|---|---|---|
| R27-4 | fixed: parametrized over no record / corroborated pid 4242; `_wait_daemon_gone` answers `pid == recorded` | `test_cli_daemon_stop_scope.py::test_a_daemon_still_answering_after_the_stop_is_reported[4242-pid 4242]` (`if pid is None and _status_body(...)`: DID NOT RAISE Exit) |
| R27-5 | fixed: moved to `test_cli_contract.py::test_a_typed_field_reaches_the_daemon`, asserts `eol` / `repeat_ms` / `port` in the POST body or GET params, never the exit code | 7 separate mutants (attach, send, cmd, wait and assert `eol` -> None; `repeat_ms` dropped; plot export `port` dropped), each fails exactly its own case |
| R27-14 | fixed: fake `get_last_error` answers 5 only after `WinDLL(use_last_error=True)`; `CreateFileW` returns raw -1 unless `restype is wintypes.HANDLE` | `test_cli_daemonctl.py::test_a_failed_windows_open_falls_back_to_no_log` (M1 `use_last_error=False`: `[Errno 0] no error`; M2 no `restype`: no warning at all) |
| FB1-3 | fixed: `_FakeProc` records `terminate`/`wait`; asserts `["terminate", "wait"]` and `; stopped it` | `test_cli_daemonctl.py::test_start_whose_daemon_never_answers_is_still_a_failed_start` (M3 `proc.terminate()` -> `pass`: `[['wait']]`) |
| R27-16 | fixed: `_StoppableDaemon` answers GET `/status` and POST `/shutdown` only, 404 otherwise | the 4 cases `test_daemon_stop_falls_back_to_the_api_when_no_record_exists` and `test_daemon_stop_asks_status_before_giving_up_on_a_corrupt_record[3]` (`probe("POST", "/shutdownX")`); control run without the mutant passes |
| R27-17 / R63-1 | fixed: both detach cases canned as 400 | `test_cli_attach.py::test_a_real_miss_still_reports_the_daemons_no_such_port` (`fail()` rewriting a 400's message) |
| R27-18 | fixed, adapted to D-16: the gate is now the response header, so the unorderable `0.4.0.dev1+local` goes in `X-Mcuscope-Version`, not in `/status`; docstring says so | `test_cli_contract.py::test_wait_repeat_survives_a_daemon_without_the_send_counters` fails on dropping the `"sends" in res` guard, and on an orderable old header (`0.4.0`), both rc 1 |
| R27-19 | fixed: `recv` blocks in 50 ms sleeps, `_thread.interrupt_main()` from a thread once blocked, driven through `cli.main(["tail", "-n", "0", "-f"])`; expects exit 0, no `interrupted`, the row printed | `test_cli.py::test_ctrl_c_ends_a_follow_with_success` (arm moved inside `run()`: `interrupted`, 1 == 0), killed on 3.10, 3.11 and 3.13 |
| R27-20 | fixed: `_DeadPipe.fileno()` is an fd on `os.devnull`, closed by an autouse fixture | not a branch; `pytest -s -k rich_renders` printed no session summary before, prints `2 passed` after |
| R27-21 / R78-5 | fixed: `test_log_export_removes_a_partial_file_when_the_daemon_dies` deleted; `test_cli_read_scope.py::test_a_paged_export_that_dies_mid_walk_leaves_no_file` exists and covers the guard | n/a (deletion) |
| FB2-2 | fixed: `probes == []` asserted inside the loop for `start` | `test_cli_ux.py::test_open_with_json_is_refused_before_anything_is_spawned` (a `_status_body` call at the top of `daemon_start`) |
| posix_only (fixbatch-cli F1) | all 6 markers and the `posix_only` definition dropped: every case signals a Python child through `os.kill`, which is TerminateProcess on Windows, and none needs a POSIX API | Windows run owed (below) |

## Items from registry-fix "Needs another batch"

- `test_cli.py`: local `_ScriptedWS` replaced by `from tests.support import ScriptedWS as _ScriptedWS`; the 5 MockTransport handlers named in cli.md plus 2 more (`test_can_dump_follow_stops_on_an_error_no_retry_can_fix`, `test_a_write_failing_mid_stream_is_an_exit_code_not_a_traceback`) wrapped in `versioned`.
  - `_FakeWs` gained a current handshake (`response`).
  - `run_mcu_canned` now uses `support.canned`, so the version check runs; it used to patch `open` to a bare `httpx.Client`.
- `test_cli_daemon_stop_scope.py`: expects `started mcuscoped (pid 4242; launcher 999997)`.
- `test_cli_version_gate.py`: deleted. Where its cases went:
  - `test_the_same_fields_reach_a_current_daemon` -> `test_cli_contract.py::test_a_typed_field_reaches_the_daemon` (R27-5).
  - `test_a_404_from_a_current_daemon_keeps_its_own_message` -> `test_cli_contract.py::test_a_404_keeps_the_daemons_own_message`.
  - `test_inverted_bounds_are_still_refused_before_the_version_check`: not moved; `test_cli_export.py::test_inverted_clock_bounds_are_refused_without_a_daemon` already covers every windowed command.
  - `test_an_unparsable_daemon_version_is_not_refused`: covered by `test_cli_client_version.py::test_a_current_newer_or_unorderable_daemon_is_let_through`.
  - Everything else tested the removed `/status` gate or the `does not serve` route rewrite, both gone.
- `test_cli_contract.py`: `_canned` and `test_log_export_keeps_a_file_it_could_not_open` go through `support.canned` (real `open`).
- `test_cli_daemonctl.py`: `_answer_status` goes through `support.canned`.

## Unowned test files edited

- `test_cli_export_files.py`: imported `BOUNDED` from the deleted file for a list-completeness check; now checks `EXPORTS`/`-o` only. `test_cli_export.py::test_the_window_list_is_every_command_taking_from` is the `--from` half.
- `test_cli_follow_frames.py`: `test_a_successful_poll_restarts_the_give_up_clock` handler wrapped in `versioned`. Line 149's handler fails the test if polled at all, so it was left as is.
- `test_cli_small_refusals.py`: the older-daemon ambiguous-port pair dropped (the CLI no longer has that wording).
- `test_port_health.py`: `_canned_lines` answers `/status` with `{"now": time.time()}` and records `/lines` only.
- `test_status_ppid_serial.py`: reads the shim pid after `; launcher ` and asserts `(pid <daemon>; launcher <shim>)`.
- `test_cli_output_rows.py`, `test_cli_decode_rejected_defs.py`: fixed by the `test_cli.py` import, not edited.

## Verification

- Every file above, run alone in random order: all pass (test_cli 167, contract 60, attach 18, stop_scope 21, daemonctl 30, ux 27, export_files 33, follow_frames 11, small_refusals 16, port_health 27, status_ppid_serial 3).
- `ruff check` clean on all touched files.
- Sweep: 44 test files that drive the CLI (outside the tests-daemon batch and webui JS) run one file per pytest process. The only failures were in `test_plotjuggler.py` (4, the `target` key), which daemon-api.md already hands over.

## CHANGELOG

None: test-only batch.

## Needs another batch

None needed for this batch's findings. Q2 below lists class 27 instances for the orchestrator.

## Needs Windows

- The 6 stop tests in `test_cli_daemon_stop_scope.py` that were `posix_only` now run on Windows CI: `test_no_record_and_a_refused_shutdown_signals_nothing`, `test_no_record_and_an_accepted_shutdown_is_judged_by_status`, `test_a_record_naming_the_pid_still_lets_the_signal_fallback_work`, `test_a_stale_record_naming_a_live_unrelated_pid_is_not_signalled`, `test_a_stale_record_and_a_refused_shutdown_signals_nothing`, `test_a_parent_status_still_names_after_the_grace_is_signalled`. Reasoned, not driven: under a venv, `victim.pid` is the redirector, and TerminateProcess on it is still what `victim.wait()` observes.
- R27-14's double follows the ctypes docs (default `c_int` restype turns INVALID_HANDLE_VALUE into -1). Never compared with a real kernel32.

## Needs a browser

None.

## Scratch left for the owner to delete

Not removed: a recursive delete needs the owner's confirmation. Under `~/tt-data/mcuscope-2026-09-25/fix-cli-tests/`:

- `copy/` (the private host+firmware copy used for mutants);
- `venv3.10/`, `venv3.11/` (for the Ctrl-C check);
- `*.orig` and `test_cli_version_gate.py.deleted` (pre-edit backups; the gate file is still in HEAD);
- `base.txt`, `sweep.log`, `sweep_files.txt`, `probe_test.py`.

`mutant.sh` is worth keeping: it syncs the repo tree into the copy, applies one exact replacement, runs pytest and restores.

## The two questions

1. Least confident: that the R27-19 test drives what a real Ctrl-C does.
   - Rechecked: it passes 15 of 15 runs on 3.13, and the moved-arm mutant fails on 3.10, 3.11 and 3.13 (scratch venvs).
   - The first version slept 30 s inside `recv`. The loop saw the interrupt only when that select returned, so the test passed after 30 s. It now sleeps in 50 ms slices, and the docstring says why.
   - Still reasoned, not driven: a real SIGINT from a terminal on Windows.
2. What we have not checked: other doubles that skip the D-16 version check by patching `Client.open` to a bare `httpx.Client`. They still pass, so they were outside this batch's remit. Each should use `support.canned`/`record_requests` or wrap in `versioned`:
   - `test_cli_closed_output.py:34`, `test_cli_send_verdicts.py:252,267`, `test_cli_can_dump.py:218`, `test_port_column_stored.py:157`, `test_scaffold.py:138`;
   - in this batch's own file, `test_cli.py:720` `_FakeHttp`. It replaces httpx to assert that `plot export` streams, so it was left alone. It would need a MockTransport and a header to keep both checks.
   - Registry candidate (class 27): "a test double that replaces the client seam also skips the checks the seam runs".
