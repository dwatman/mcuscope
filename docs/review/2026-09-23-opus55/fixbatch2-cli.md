# Fix batch 2: cli slice (2026-09-24)

HEAD: `b889e2b8fb848866054da6ba09cd2e651d9f69c7` (uncommitted tree).
Revert copy and script: `~/tt-data/mcuscope-2026-09-24/fixbatch2-cli/` (`mutate.py`, output `mutate.out`; the copy imports its own `mcuscope`, checked by printing `__file__`).
Each mutation below was applied alone to the copy and its test file run.

## Findings from fixdiff2-cli.md

### 1. HIGH: Windows append handle cannot be fstat'd
- `cli_daemonctl.py:80-82`: access mask now `FILE_APPEND_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE`; docstring says why.
- Test: `test_cli_fixdiff_append.py::test_the_windows_open_asks_for_append_data_only` asserts `0x0004 | 0x0080 | 0x00100000`.
- Revert M1 (mask without 0x0080): that test fails.

### 2. ppid-only record signalled after the daemon has gone
- `cli_daemonctl.py:340-352`: when the record matches only `/status`'s `ppid`, the stop is judged on `/status` going quiet.
  - If the grace runs out, `/status` is asked again at the moment of signalling. Quiet: stopped, nothing signalled. Answering but not naming the parent: exit 1, `... which is not the process serving it, so no process was signalled`. Still naming it: signalled as before.
  - The own-`pid` path is unchanged.
- Tests, all in the new `tests/test_cli_fixdiff2_stop.py`, run with win32 patched and a sleeper this test started as the parent:
  - `test_the_parent_of_a_daemon_that_shut_down_is_not_signalled` (the reviewer's probe);
  - `test_a_parent_status_still_names_after_the_grace_is_signalled` (positive control, POSIX only);
  - `test_a_parent_status_stops_naming_before_the_signal_is_spared`;
  - `test_a_daemon_gone_by_the_moment_of_signalling_spares_its_parent`.
- Revert M2 (`parent_only = False`, the old behaviour): 3 fail. M3a (quiet re-check not taken as stopped): 1 fails. M3b (uncorroborated parent still signalled): 1 fails.

### 3. Index-build wait: ceiling, anchored match, redundant clause
- `cli_daemonctl.py:108-114`: the notices are matched as whole store lines (`^capture <path>: building index <names> once (about ...)$` and `^capture <path>: built index <names> in <s> s$`), built from the copied constants with `re.escape`. The greedy path takes the last `: building index`.
- `cli_daemonctl.py:114`: `INDEX_BUILD_CEILING_S = 600.0`, counted from when the wait on the build begins (the first `--timeout` expiry with the notice present).
- `cli.py:2669-2693`: past the ceiling, `die("mcuscoped is still building index <names> after 600s; left running (pid N)", 1)`. The process is not stopped and the pid record stays.
- `cli.py:2677`: `proc.poll() is not None` clause deleted. Re-adding it (M8) keeps all 8 tests green, which confirms no test catches its removal. The loop's own poll after the probe covers it.
- Tests in `tests/test_cli_start_index_build.py`:
  - `test_a_build_past_its_ceiling_exits_1_and_is_left_running`: ceiling 30 s injected, exact message, not terminated, record still names 4242, clock at 35 s. It also asserts the default is 600.
  - `test_a_path_quoting_the_notice_words_is_not_a_build`: warnings quoting `/srv/building index/` and `/srv/a: building index b/`; stopped at 5 s, no wait note.
  - `test_the_notices_are_read_under_a_path_quoting_their_words`: real notices under such a path still parse (names, then built).
- Revert M5 (ceiling branch removed): the ceiling test loops forever on the fake clock and pytest-timeout fails it. M6 (build notice back to substring): the path test fails. M7 (built notice back to substring): the parse test fails.

### 4. `daemon restart` racing the old daemon's capture lock
- `cli_daemonctl.py:374-377`: with `restarting`, after a ppid-only stop, wait (bounded by `DAEMON_STOP_GRACE_S`, no signal) for the recorded launcher to exit before returning. A shim exits once its child has, and the child exits only after the lifespan has closed the store and released the lock. A parent that is not a shim never exits, and the start then goes ahead as before.
  - The own-`pid` path already waited on the pid. An uncorroborated or no-record stop keeps today's `/status`-only behaviour, as briefed.
- `quiet` renamed `restarting` (`cli_daemonctl.py`, `cli.py:2767,2777`), since it now does more than silence output. Test fakes updated: `test_cli_ux.py` (2), `test_fixdiff2_cli.py:177`, `test_rulings_cli_config.py:38`.
- Test: `test_cli_fixdiff2_stop.py::test_restart_starts_only_once_the_launcher_has_exited`: a 1.5 s sleeper stands in for the shim, and the fake `_start_daemon` records `pid_running(shim)` at the call, which must be `[False]`.
- Revert M4 (wait removed): that test fails.

### 5. NIT: SPEC 4 `daemon` row: wording for the owner (SPEC not edited)
- Replace `` `start` fails (exit 1, `another daemon is already serving`) when `/status` names neither its child as `pid` nor, on Windows, as `ppid` `` with:
  - `` `start` fails (exit 1, `another daemon is already serving`) when `/status` names neither its child as `pid` nor, on Windows, as `ppid` (a daemon reporting neither field, older than 0.1.2, is accepted) ``
- The same row, for findings 2-4. Replace `` waits for `built index`, then allows one more `--timeout`; `` with:
  - `` waits for `built index`, then allows one more `--timeout`; past 600 s of building it exits 1 with `mcuscoped is still building index <names> after 600s; left running (pid N)` and leaves the daemon running (stopping it would restart the build); the notices are matched only as the store's whole log lines (`capture <path>: building index ...`), so a path quoting the words is not a build; ``
  - Replace `(its `pid`, or on Windows its `ppid`, the venv launcher `start` recorded)` with: `(its `pid`, or on Windows its `ppid`, the venv launcher `start` recorded; a `ppid` match is judged on `/status` going quiet and signalled only if `/status` still names it after the grace, and `restart` waits for that launcher to exit before starting)`.
- SPEC 3.2 line 426 (`mcu daemon start` waits for the build rather than timing out): append `, for up to 600 s of building (SPEC 4)`.

### 6. NIT: stale comment
- `cli.py:993`: now reads `judged on the ports attached or stored, once`.

## Coordinator additions

### U-1, U-6 (fixdiff2-ui-fw.md): `AI_GUIDE` `^!e` entry, `cli.py:2994-3000`
- The entry now reads: a marker with no text past `@<tick>` arrives as the notice alone, and a `!p` whose first pair does not fit arrives as `"!p @<tick>"` then the notice.
  - I wrote `!p @<tick>`, not `!p <tick>`: `test_sim_error_codes.py:87` expects `"!p @7"`.
- New in the entry: `"!e can bus <n> dropped"`, meaning a frame for a bus above the build's `MON_CAN_BUSES` was dropped (sent once per init).
- No test pins this prose. `test_cli_contract.py` passes (27).

### fixdiff2-daemon.md finding 4: port column rule is a union
- `cli.py:890-898` (`_stream_port_column`): the column shows when the union of attached aliases and `stored` names, non-string entries and `""` excluded, has more than one element. Collected as a list and filtered before hashing, so a malformed name cannot raise.
  - `server.py` `_several_ports` is not touched and still counts the two lists separately (the other agent's half).
- Tests in the new `tests/test_cli_fixdiff2_port_column.py`:
  - detached `a` stored plus attached `b` gives a column;
  - `a` both attached and stored (with and without a `""`) gives none;
  - malformed names give no board and no crash.
- Revert P1 (old rule): 3 fail. P2 (`""` counted): 1 fails. P3 (no str filter): 1 fails.

## AI_GUIDE
- `cli.py:3082-3084`: `daemon start` gains `past 600 s of building, exit 1 "still building index", left running`.
- Plus the `!e` entry above.

## CHANGELOG lines (Unreleased; not edited)
- Fixed: `mcu daemon start` on Windows kept no stderr log (`WinError 5` on the new append-only handle), so a failed start showed no tail and an older capture's index build was stopped at `--timeout`.
- Fixed: on Windows, `mcu daemon stop` no longer terminates the parent of a daemon that has already shut down when the pid record names that parent rather than a launcher shim.
- Fixed: `mcu daemon restart` on Windows waits for the old daemon's launcher to exit before starting the new daemon, so the new one no longer hits the old capture lock.
- Changed: `mcu daemon start` waits on an index build for at most 600 s, then exits 1 with `mcuscoped is still building index <names> after 600s; left running (pid N)` and leaves the daemon running.
- Fixed: a `db_path` containing the words `building index` no longer makes `mcu daemon start` wait on a build that is not happening.
- Fixed: `mcu tail -f` and `mcu log export` show the `[port]` column when one board's stored history and a different attached board make two boards (was only when either list alone held two).

## Needs Windows
- `python -c "from mcuscope.cli_daemonctl import _open_append as o; import os; f=o('x.err'); print(os.fstat(f.fileno()).st_size)"` prints a size, no `WinError 5`.
- A real `mcu daemon start` then `mcu daemon restart` in a venv: the restart succeeds with no capture-lock refusal, and `stop` prints `stopped mcuscoped (pid <shim>)`.
- `test_cli_fixdiff2_stop.py` on Windows: the positive control is POSIX-only; the other four and the restart test should pass on real win32.

## Verified
- `uv run python -m ruff check .` in `host/`: clean.
- Run one file at a time, all green:
  - `test_cli_contract` 27, `test_cli_fixdiff2_stop` 5, `test_cli_start_index_build` 8, `test_cli_fixdiff_append` 2, `test_cli_daemon_stop_scope` 14;
  - `test_cli_ux` 27, `test_pidfile` 19, `test_fixdiff2_cli` 18, `test_rulings_cli_config` 12, `test_cli` 167;
  - `test_cli_fixdiff2_port_column` 3, `test_port_column_stored` 11.
- The mutations M1-M8 and P1-P3 above, from `mutate.py`.

## Not verified
- Anything on real Windows (the fstat fix, TerminateProcess of a shim, the lock timing).
- The whole suite was not run, by rule.

## Doubts
- Restart with a ppid-matched parent that is not a shim (the cmd.exe case) now waits the full 10 s grace before starting. That is rare, and the result is the same as before.
- The ceiling is counted from the start of the wait (after the first `--timeout`), not from when the build began. The worst case is therefore `--timeout` + 600 s.
- The anchored match assumes the store's message reaches stderr unprefixed (logging's last-resort handler). If the daemon ever installs a formatter with a prefix, the wait silently falls back to stopping at `--timeout`. `test_the_cli_copies_of_the_notices_match_the_store` checks only the words, not the line shape.
- `ARCHITECTURE.md:133` ("keys its readiness wait on the store's ... notices") is still accurate, but it could add "matched as whole log lines, capped at 600 s". Not edited (not my file).
