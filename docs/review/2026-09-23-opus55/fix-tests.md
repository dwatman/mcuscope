# Fix batch: tests

Revert-verification ran in a scratch worktree of HEAD 4e618e6 (`~/tt-data/mcuscope-2026-09-23/fix-tests/wt`, now removed) with this batch's test files copied in and `PYTHONPATH` on the worktree, so every child imported the worktree's package.
Driver: `~/tt-data/mcuscope-2026-09-23/fix-tests/mutants.py`; one log per mutant beside it (`mut_*.log`).

## HEALTH-10: a crashed child fails the test

- `host/tests/support.py:54-56,73`: `child_env` records the child's final `MCUSCOPE_DATA_DIR` in `CHILD_DATA_DIRS`.
  Every `mcu`/`mcuscoped` spawn builds its env through `child_env` (health's "checked and fine"), so every one is covered, the 22 from the AST scan included.
- `host/tests/conftest.py:59-84`: autouse `_no_child_crashed` scans those dirs at teardown for `*-crash.log` and fails the test with the log text.
  It deletes the logs it reports, so one crash is not blamed on later tests.
  A new marker `child_crash_expected` (registered in `pytest_configure`) opts out.
- Only the crash log is checked, not "Traceback" on stderr: every console entry writes the log on an uncaught exception, and the run helpers that see stderr live in files other batches may be editing.
- Revert-verify:
  - Mutant C09 (`cli.py:2580` `if pid is None:` to `if False:`), `test_cli.py -k corrupt`: HEAD helpers pass (4 passed); new helpers fail (1 error, "a spawned child crashed", `TypeError: '<=' not supported`).
  - C09 with the `CHILD_DATA_DIRS.add` line removed: passes again, so the registry line is what catches it.
  - Marker removed from `test_a_child_crash_log_lands_in_the_childs_data_dir_override`: that test errors. The marker is needed.
- No false alarms: with the check live, every file that spawns a child passes in the clean worktree.
  Files: `test_cli` (167), `test_cli_ux`, `test_break`, `test_fixdiff2_cli`, `test_pidfile`, `test_prerelease_cli_fixes`, `test_review_r2_cli`, `test_scaffold`, `test_sim_tcp`, `test_stdio`, `test_dirs_override`, `test_rulings_cli_closed_pipe`, `test_sweep_cli_closed_output`.
  The daemon batch's new `test_daemon_hangup_and_race_reports.py` passes too, in the shared tree.

## HEALTH-12: the inert WS backpressure test

- `host/tests/test_e2e.py`: `test_ws_backpressure_drop_oldest` deleted.
  The shed path is pinned by `test_hardening.py::test_a_slow_subscriber_is_told_it_missed_rows`, which asserts `ws_dropped` and `take_dropped` counts through `store._broadcast`.
- `test_e2e.py` went from 62 s (health sweep, load 10-15) to 15.4 s here.
- Revert-verify: not applicable (a deletion).
  I did not re-run a mutant against the hardening test: its assertions on the drop counts are direct.

## HEALTH-13: the 1.2M-point export test

- `host/tests/test_plot_export_decode.py:300-349`: `test_a_selection_past_the_old_row_cap_streams` now sets `store._EXPORT_CHUNK` and `store._EXPORT_PAGE` to 7.
  It fills 700 lines of three points (`a`, `b`, `c`) and asserts the exact `(name, value)` sequence, not a count.
  That is 300 fetches, and a page of 7 ends mid-line, which is the boundary the store batch's PERF-2 paging has to get right.
- Time: 78.6 s (sweep) to 0.2 s.
- Revert-verify (store.py in the worktree):
  - `break` after the first fetchmany batch: fails.
  - The same `break`, with the test's `_EXPORT_CHUNK` patch removed: passes. The patch is what makes the test sensitive.
  - `LIMIT 1000` appended to the export SQL: fails.
- `host/pyproject.toml:89-90`: the comment now says the slowest test measured 7 s at load ~10 (2026-09-23).
  That is `test_cli.py::test_daemon_start_timeout_does_not_orphan_the_child` at 6.9 s in the health sweep, the slowest once the three tests fixed here are gone.
  The same test took 6.7 s in this batch's runs at load ~5, and was again the slowest.

## HEALTH-14: sim CAN traffic in the bundle sessions

- `host/tests/test_session_bundle.py:93-96`: `recorded()` sends `can filter none` and `can2 filter none` through `/cmd` and asserts each is ok before the session opens.
  All the standing traffic is covered, not only the 0x100 heartbeat: without `--demo`, the sim also emits bus 1's `CAN_BUS` ids and bus 2's `CAN_BUS2` ids.
- Revert-verify (test side, the flake being timing):
  - A 0.4 s sleep inside the session: passes.
  - The same sleep with the silencing removed: fails.

## HEALTH-21: guide flags matched as tokens

- `host/tests/test_cli_contract.py:222-227`: a flag counts only as a whole token, `(?<![\w-])flag(?![\w-])`.
- Revert-verify:
  - Against HEAD's guide: fails and lists exactly `purge -y`, `session delete -y`, `daemon start/restart -c` and `-t`.
  - The same guide with ` -c -t -y` added: passes.
- The shared tree still fails on those three: the CLI batch's guide edit has not landed. Expected; the test was not weakened.

## HEALTH-22: pty read

- `host/tests/test_sim_pty.py:34`: `ser.read(ser.in_waiting or 1)`.
- `test_pty_ping_round_trip`: 10.15 s (sweep) to 0.05 s. A timing fix, so no mutant; the file passes (3 passed).

## HEALTH-23: exact answers

- `host/tests/test_e2e.py:162-163`: a `/cmd` on a held port asserts status 400 and the body `{"error": "port board is not connected"}`.
  - Revert-verify: changing the `PortError` text in `serial_link.py` fails it.
- `host/tests/test_sessions.py:233-249`: the `/send` there could never store anything, since `_mk_app` has no port, so the "before" row did not exist.
  - It is now a `/marker` "before the run", asserted outside the session and, as a positive control, present in the capture.
  - Revert-verify: `"id_from": 0` in `_resolve_window`'s session scope (`server.py:2782`) fails at the new assertion.
    The HEAD version also failed under this mutant, at a later assertion, so this is a direct pin rather than a new kill.

## HEALTH-27 (durations): the eol 422 cases

- `host/tests/test_eol.py:253-275`: the 30 `test_an_unknown_request_eol_is_422` cases share one module-scoped port-less `TestClient` (`portless`), not a sim stack each.
  The fixture calls `isolate_user_dirs` itself, as `test_timeline`'s module stack does.
- It also asserts that every part of the error names `eol`: a body invalid for another reason would 422 whatever the eol.
- Time: `test_eol.py` 72 s (sweep) to 10 s.
- Revert-verify:
  - `Eol` accepting `"cr"` (`server.py:202`): 5 cases fail.
  - `/cmd` body changed to `{}`, test side: the 6 `/cmd` cases fail on the new assertion (the HEAD test would pass them).

## HEALTH-26: docs

- `host/tests/conftest.py:1-6`: `mcu_sim` is the checkout's shim over `mcuscope.sim`.
- `docs/ARCHITECTURE.md`:
  - `:31`: `query_plot_channels` is the plain GROUP BY form, kept for the tests to compare against.
  - `:111-112`: `request`, `download` and `stream_text` go through `_daemon_errors`; `probe` does not.
  - `:137`: `test_break.py` spawns the TCP listener once.
- `CLAUDE.md:58-60`: the listener set names `test_break.py`, and one line says `child_env` feeds the crash check and names the marker.
  The "~4 min" suite figure is left: the only per-file timings I have were taken at load 10-15.
- Coordinator request (daemon batch): `docs/ARCHITECTURE.md:68-70`, the startup order now keys the startup and crash logs by record ownership, and describes the signal handlers (SIGTERM, and SIGBREAK on Windows, release the record; SIGHUP is raised on as SIGTERM).
  Worded from `daemon.py:328-380,457-460` in the shared tree.

## Existing tests edited (outside my file list)

- `test_dirs_override.py::test_a_child_crash_log_lands_in_the_childs_data_dir_override`: `@pytest.mark.child_crash_expected`, and `import pytest`.
- `test_rulings_cli_closed_pipe.py::test_a_crash_with_a_closed_stderr_is_still_logged_and_exits_1`: the marker.
- `test_sweep_cli_closed_output.py::test_a_crash_notice_into_a_full_stderr_is_logged_and_exits_1`: the marker.
- All three crash a child on purpose and assert the log; without the marker the new check fails them.

## SPEC edits

None.

## Changelog

None user-visible; test-suite only. If wanted: "Tests: a crashed `mcu`/`mcuscoped` child now fails its test; the suite's three slowest tests are down from about 110 s to under 1 s."

## Not done

- `cli_client.py:3-5` still claims `probe` routes through `_daemon_errors` (HEALTH-26). It belongs to the CLI batch.
  Change: "every request policy - request, download, stream_text - routes through it; `probe` does not, since for `mcu daemon` any transport failure means not running".
  If CLI-10 changes `probe`, `docs/ARCHITECTURE.md:111-112` has to follow.
- Seen in the shared tree while running my files, caused by other batches' in-flight source edits, not by these test changes (both pass in the clean worktree):
  - `test_cli_contract.py::test_wait_repeat_survives_a_daemon_without_the_send_counters` and `::test_assert_still_takes_a_zero_timeout`: `TypeError: Client.request() got an unexpected keyword argument 'timeout_code'` at `cli_client.py:135` (CLI batch).
  - `test_e2e.py::test_malformed_seq_response_resolves_fast`: `sqlite3.IntegrityError: NOT NULL constraint failed: lines.ts` (likely the link batch's `sent_ts` work).

## Doubts

- The crash check only sees dirs handed out during the test. A child that crashes after its test ends (a daemon killed late, a module-scoped fixture's child) is missed. I judged a sweep of every dir ever handed out not worth its per-test cost; nothing in the suite spawns a child from a wider-scoped fixture today.
- `test_a_selection_past_the_old_row_cap_streams` patches `_EXPORT_CHUNK` and `_EXPORT_PAGE` by name. If the store batch pages the plot export on a new constant, the test still passes but stops being sensitive. A rename errors (monkeypatch raises), which is loud.
- HEALTH-12 was deleted rather than made to engage. The real-stack property (ingest does not stall behind a slow `/ws` reader) now rests on `_broadcast` using `put_nowait`, pinned only at the store level.
