# Fix batch: the lost start race (RACE-1 to RACE-8)

HEAD at start: 89a6af3. Not committed. Review: `windows-fixdiff-race.md` (each finding now has a "Fixed:" line).

## Per finding

Round 1 below; where round 2 replaced it (the pid premise, the refusal settle, the first two doubts), see "Round 2".

### RACE-1: stop the losing child at once
- `host/mcuscope/cli_daemonctl.py:284` `_reap_losing_start`: a live child is stopped at once, not waited for. A child that already exited is not signalled, and its tail is shown.
- How the loss is decided (`cli.py:2758-2764`): `/status` names the child neither as `pid` nor, on Windows, as `ppid`.
  - Certain on POSIX: `sys.executable` is exec'd, so the daemon's pid is `proc.pid`.
  - On Windows it is certain only while at most one launcher sits between `proc` and the daemon (a bare interpreter, or the CPython venv launcher that uv also copies). A deeper chain would read this start's own daemon as another's.
  - That gap is not new. HEAD also stopped the real winner in that case, after 10 s; before 2ef4c5f the start reported a false failure. The Windows-only test (RACE-2) asserts the one-level premise on CI.
  - The reviewer's design is right under that premise, so I implemented it. Making it certain for any chain needs a per-start id echoed by `/status` (SPEC 3 change): see Doubts.
- Live on Linux (`smoke_race2.py stop-during-wait 5`, port 18598, throwaway TOML and db): 5 of 5 rounds, loser `...; stopped this start's own process (pid N)`, `daemon stop` exit 0, nothing answering and no daemon process afterwards.

### RACE-2: the Windows launcher
- Decision: rely on the launcher's job object. Both the CPython venv launcher and uv's trampoline put the interpreter in a `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` job, so `TerminateProcess` on the shim closes the job and ends the interpreter.
  - That is from my reading of the launcher source, not observed. I added no `taskkill /T` fallback: no test on any runner I can reach would tell it apart, so it would be an untestable branch.
- `test_cli_daemon_stop_scope.py:566` `test_a_lost_race_stop_reaches_the_interpreter_behind_the_venv_launcher` (Windows, venv only):
  - spawns `sys.executable` with `daemon start`'s creation flags;
  - asserts the interpreter is the launcher's direct child (the RACE-1 premise), then `pid_running` as a positive control;
  - runs the real `_reap_losing_start`, and asserts the interpreter is gone within 5 s and the record removed.
- CI's `test` job runs it on `windows-latest` in the uv venv. Checklist lines added to `registry-triage/windows.md` (CI result, and one run on the Store Python desktop).

### RACE-3: one stop helper
- `cli_daemonctl.py:268` `_stop_child`: terminate, wait `DAEMON_STOP_GRACE_S` (10 s), kill, wait `DAEMON_STOP_GRACE_S`. Returns True once the child has exited. An `OSError` from either call is suppressed, and the helper still waits and escalates.
- `_abandon_daemon` (`:324`) uses it too. Its old 5 s + 5 s became 10 s + 10 s.

### RACE-4: Ctrl-C
- `cli.py:2697-2703`: the post-spawn part moved into `_await_spawned` (`:2706`, body unchanged apart from the RACE-6 lines). A KeyboardInterrupt escaping it prints `this start's own process (pid N) is still running` while the child lives, then re-raises (exit 1, `interrupted`).
- In the reap the terminate is sent before any wait, so a Ctrl-C there leaves a child that is already being stopped, and names it.
- No pre-wait note: the only wait left is a stop's, normally milliseconds.

### RACE-5: stderr tail
- Docs only: SPEC 4, `windows.md` and class 89 now say "lines appended since this start began", which can include the winner's.
- No pid prefix: it would break `_index_build`'s whole-line match and clutter a foreground daemon's stderr.
- The loser that is stopped at once mostly writes nothing, so its tail is usually empty.

### RACE-6: a refusal carries no pid
- `cli_daemonctl.py:265` `REFUSED_START_SETTLE_S = 3.0`; `cli.py:2766-2775`: on a 401/403/429, wait up to 3 s for the child.
  - If it exits, the existing "exited ... although <url> answers" exit fires. That exit now also removes the record naming the child and shows the tail.
  - If it lives on, it is reported as started, as before.
- Class 89's exit list now includes the refusal path and Ctrl-C (`docs/REVIEW.md`), and the invariant covers an escaping Ctrl-C.

### RACE-7: docs
- AI guide (`cli.py` DAEMON CONTROL): reap wording ("stopped at once, or named with could not be stopped"), Ctrl-C, and the refusal settle.
- SPEC 4 `daemon start` row, the same three outcomes:
  - the unstoppable text, with its pid file kept;
  - the Ctrl-C text;
  - the refusal exit text and the 3 s wait;
  - also the kill after 10 s, and the honest tail wording.
- ARCHITECTURE `cli_daemonctl.py` entry: the at-once stop, the one-launcher premise, and `_stop_child`.
- REVIEW class 7: the "winner left with no record" case is now an accepted matrix cell. It is not filled from `/status`, because that pid may be a peer's (class 82).

### RACE-8: tests
- The reap tests were rewritten on `_start_with`, `test_cli_daemon_stop_scope.py:337-404`:
  - the `.err` file is prefilled with a winner line, and every test asserts it is absent;
  - each test records what the pid file named when the probe answered, a positive control for "record gone";
  - the fake records its calls in order.
- New tests (`:406-566`):
  - the child already failed;
  - stopped at once, `calls == [terminate, wait(grace)]`;
  - deaf to terminate, so killed;
  - cannot be stopped: deaf to both, or the OS refusing both;
  - terminate refused but the kill works;
  - Ctrl-C in the stop, in the readiness wait, and after the child exited;
  - refusal: the child fails on the lock, or lives on (`wait(3.0)` asserted);
  - abandon uses the same escalation;
  - a real SIGTERM-ignoring child (POSIX);
  - the Windows launcher test.
- The Popen fakes in `test_cli_daemonctl.py` (`_FakeProc`) and `test_cli.py` (`_Unresponsive`) gained `kill`, which the real Popen has.

## Revert table
Scratch copy of the working tree `host/`, one mutant at a time (`mutate.py`), against `tests/test_cli_daemon_stop_scope.py`, with `PYTHONDONTWRITEBYTECODE=1` and an empty `PYTHONPYCACHEPREFIX`.
The first pass reused a stale `.pyc`: N13 is the same size as the original, and it was restored within the same second. That spread false failures across N14 to N19, so every mutant was rerun clean.

| Mutant | Result | Caught by (examples) |
|---|---|---|
| N1 reap waits 10 s before stopping | caught | `..._stops_a_child_that_is_still_starting_at_once` and 4 more |
| N2 reap signals an exited child | caught | `..._already_failed_shows_only_its_own_lines` |
| N3 unstoppable branch deletes the record | caught | `..._cannot_be_stopped_keeps_its_record` (both params) |
| N4 no "stopped" note | caught | 4 tests, including the real SIGTERM one |
| N5 no kill escalation | caught | deaf-to-terminate, terminate-refused, real SIGTERM, abandon |
| N6 `stop()` not under `suppress(OSError)` | caught | terminate-refused, OS-refuses-both |
| N7 grace literal 5 instead of `DAEMON_STOP_GRACE_S` | caught | at-once, deaf, abandon |
| N8 helper returns True when the child lives | caught | cannot-be-stopped (both) |
| N9 `_abandon_daemon` back to its inline stop | caught | `test_a_start_that_never_answered_is_stopped_like_a_losing_one` |
| N10 reap tail from byte 0 | caught | already-failed (winner line shown) |
| N11 reap keeps its record | caught | 4 tests |
| N12 no settle wait on a refusal | caught | both refusal tests |
| N13 `REFUSED_START_SETTLE_S = 0` | caught | both refusal tests |
| N14 poll exit keeps the record | caught | `..._refused_probe_is_another_daemons_...` |
| N15 poll exit without tail | caught | same |
| N16 poll exit tail from byte 0 | caught | same |
| N17 no Ctrl-C note | caught | Ctrl-C in the readiness wait, in the stop |
| N18 Ctrl-C note without the poll check | caught | `test_ctrl_c_after_the_child_exited_does_not_call_it_running` |
| N19 Ctrl-C swallowed | caught | all three Ctrl-C tests |

Also green on the repo tree, each file alone:
- `test_cli_daemon_stop_scope.py` (34 passed, 1 skipped: the Windows test);
- `test_cli_daemonctl.py` (30), `test_cli_contract.py` (59), `test_status_ppid_serial.py`, `test_cli_daemon_start_pid.py`, `test_cli_start_index_build.py`;
- `test_cli.py -k "abandon or daemon"` (20);
- `ruff check .`

## Doubts
- Ctrl-C leaves a starting child running and names it rather than stopping it (the owner kept this in round 2).
- The abandon path now waits up to 10 s before the kill (was 5 s), so `daemon start --timeout` failing on a SIGTERM-deaf child takes longer.
- `_FakeChild` in `test_cli_daemonctl.py` still has no `kill`. No test drives it into a stop.

## Needs Windows
- The CI `windows-latest` run of `test_a_lost_race_stop_reaches_the_interpreter_behind_the_venv_launcher`: not run (no push from here). A failure there means one of two things. Either the launcher's job does not end the interpreter (then stop the tree, `taskkill /T /F`), or the interpreter is not the launcher's direct child (then the RACE-1 premise fails on CI).
- The desktop Store Python venv: the same test, `-rs`, not skipped (checklist in `registry-triage/windows.md`).
- A live two-start race on Windows with the stop landing while the loser is still starting, which would exercise the terminate branch end to end.

## Scratch (not deleted)
- `/home/daniel/tt-data/mcuscope-2026-09-26/race-fixbatch/`:
  - `host/` and `tools/`: rsync copy of the working tree. `host/**/__pycache__` holds stale mutant bytecode, so do not reuse it without clearing.
  - `mutate.py`: the N1 to N19 runner.
  - `new_block.py`: the spliced test block.
  - `test_cli_daemon_stop_scope.py.orig`: the pre-edit backup.
  - `smoke_race2.py`: the reviewer's script re-pointed at port 18598.
  - `smoke/`: TOML, db, data and config dirs.
- One stray copy went to `/tmp/x` during a debug step and was deleted in the same command.
- No daemon left running: `ps` showed no `mcuscope.daemon` after the smoke and after the final gates.

## Round 2

HEAD still 89a6af3, not committed. Owner rulings: Ctrl-C unchanged, a start id replaces the pid premise, and the fix-diff 2 findings F1, F2, F3, F5 and F6 (`windows-fixdiff2.md`, each with a "Fixed:" line).

### Start id design
- `mcu daemon start` generates `secrets.token_hex(16)` per start (`cli.py:2672`) and hands it to the child as env `MCUSCOPED_START_ID`.
  - Env, not argv: it rides next to `MCUSCOPED_TOKEN`, adds no user-facing daemon option to document, validate or abbreviate, and every launcher (venv shim, uv trampoline, Store Python) passes the environment through unchanged.
- The daemon takes it only as 16 to 64 lowercase hex digits (`daemon.py:411` `_start_id`); anything else is ignored with a startup note, so nothing unchecked reaches a header.
- `server.py` echoes it as `X-Mcuscope-Start-Id` beside `X-Mcuscope-Version`:
  - in the outermost middleware, so guard refusals (401/403/429), 404s and the `/ws` accept carry it;
  - and in `_unhandled_error`, since a 500 is answered outside every middleware (class 87).
- Why a header and not a `/status` field: a refusal has no body to put it in. A header on every answer means the refused path no longer guesses.
  - Unauthenticated exposure is acceptable under SPEC 3.1: a fresh random value per start that nothing accepts as input. It is no more than the version header already discloses.
- The CLI side:
  - `_status_or_refusal` returns the echoed id with the body and refusal (`cli_daemonctl.py:181`).
  - `_await_spawned` decides `answered_id != start_id` means lost (`cli.py:2764`), for any launcher chain.
  - `_serving_pids` is now used only by `daemon stop`.
- The refusal settle is gone entirely.
  - Ruling 2's 10 s had no path left to apply to: an answer without this start's id is never this start's daemon, because the child runs the same install.
- `daemon stop`, `mcu daemon status` and the pid record ignore the id.
  - `stop` has to know which local process to signal, which an id does not name. `pid`/`ppid` matching stays there.
- DAEMON_MIN_VERSION is not raised: an older winner without the header is correctly read as another daemon, and the child is always the same install.
- New exit: a child that exits just after answering with the id fails the start (`mcuscoped exited with status N just after answering at <url>`), removes its record and shows the tail. It replaces the old "although <url> answers; something else is serving" exit.

### Other fixes
- F5: the Ctrl-C handler removes the record naming an exited child. The pid record write moved into `_await_spawned`, so a Ctrl-C during it names the child too.
- F6: the unstoppable message ends with the stderr tail.
- F2: the launcher test skips when the spawned process is the interpreter itself, and no longer asserts the ppid premise.
  - A new Windows-only `ci.yml` step fails the job on `SKIPPED.*launcher` in `pytest-report.txt`.
  - `-rs` prints the skip reason, not the test name. I checked the line format locally (`SKIPPED [1] tests/test_cli_daemon_stop_scope.py:610: the venv launcher is Windows-only`), and no other test's skip reason names the launcher.

### SPEC and docs
- SPEC 3.4: the header, its env source and grammar, and why it is safe unauthenticated.
- SPEC 4 `daemon start` row:
  - the id decision replaces the pid/ppid rule and the "older than 0.1.2 is accepted" clause;
  - the unstoppable tail, the exit just after answering, Ctrl-C removing an exited child's record;
  - a refusal carrying the id is a start;
  - `stop`, `status` and the record do not use the id.
- AI guide: matching wording (the loss rule, and the refusal rule stated on the id).
- ARCHITECTURE: the launcher-chain caveat is replaced by the id rule; `daemon stop` keeps pid matching.
- REVIEW class 89: the invariant and the exit list.
- `windows.md`: the race section and its checklist lines.

### Tests
- `test_cli_daemon_stop_scope.py`:
  - `_start_with` answers `(body, refusal, id)`, where OWN echoes the id the fake spawn found in its env;
  - own id with neither pid nor ppid matching, on win32 and linux: started, no wait, no signal;
  - an answer without the id, even one naming the child's pid, and one with another start's id: lost;
  - a refusal without the id: lost, stopped at once;
  - a refusal with the id: started, no wait;
  - exited just after answering;
  - Ctrl-C after exit removes the record, and Ctrl-C during the record write names the child;
  - the unstoppable tail;
  - the child's env carries a fresh 32-hex id and the token only with `--token`, and two starts differ.
- `test_server_version_header.py`: the id on a LAN 401, the `/ws` accept, a 500 and a 200; absent without an id.
- `test_daemon_start_id.py` (new): the generated form is taken; absent means no note; empty, short, long, uppercase and CR/LF-injected values are refused with the note.
- Spawn fakes in `test_cli_ux.py`, `test_cli_daemonctl.py` (`_Daemon`, `_fake_spawn` plus `_phased`, which now sends the header), `test_cli_start_index_build.py` and `test_cli_daemon_start_pid.py` echo the id from the spawn env.
- Live: `test_cli_ux.py`'s real `mcu --json daemon start` and `test_status_ppid_serial.py`'s shim start both go through the real header.

### Revert table (round 2)
Fresh scratch copy (`r2/host`, no `__pycache__`), `mutate_r2.py` (checks every anchor first, then restores and asserts each restore), `PYTHONDONTWRITEBYTECODE=1`, an empty `PYTHONPYCACHEPREFIX`, `-p no:cacheprovider -p no:randomly`.
Files per mutant: stop_scope, server_version_header, daemon_start_id, cli_daemonctl, cli_ux, status_ppid_serial, cli_start_index_build, cli_daemon_start_pid. Baseline: 124 passed, 1 skipped.

| Mutant | Result | Caught by (examples) |
|---|---|---|
| N1 to N11, N17 to N19 (round 1, re-run after the move) | all caught | as round 1; N2 also by `test_daemon_start_refuses_when_another_daemon_serves_the_url` |
| R1 no id check | caught | 11 tests |
| R2 a missing id counts as ours | caught | 10 tests, including the no-id parameter |
| R3 the child gets a different id | caught | 24 tests, the real starts included |
| R4 token not forwarded | caught | `test_the_child_gets_a_fresh_start_id_and_the_token_through_its_environment[glob0-s3cret]` |
| R5 middleware drops the id | caught | the header test and both real starts |
| R6 500 without the id | caught | `test_the_start_id_rides_every_answer_a_start_can_get` |
| R7 daemon does not pass the id | caught | both real starts (`test_a_daemon_behind_a_launcher_shim_starts_and_stops`, `test_restart_of_a_running_daemon_swaps_the_pid`) |
| R8 any id accepted | caught | `test_any_other_value_is_ignored_and_said` (5 params) |
| R9 a bad id not said | caught | the same (6 params) |
| R10 probe ignores the header | caught | `_phased` restarts, both real starts |
| R11 a refusal drops the id | caught | `test_start_reports_a_daemon_it_spawned_that_answers_with_its_guard` |
| R12 exited Ctrl-C keeps its record | caught | `test_ctrl_c_after_the_child_exited_removes_its_record` |
| R13 unstoppable without tail | caught | both cannot-be-stopped params |
| R14 `(pid None)` clause always printed | caught | `test_a_refusal_without_this_starts_id_is_another_daemon` |
| R15 exited-after-answer not checked | caught | `test_a_child_that_exited_just_after_answering_is_not_reported_started` |
| R16 that exit keeps the record | caught | same |
| R17 record written outside the Ctrl-C handler | caught | `test_ctrl_c_while_writing_the_pid_record_names_the_child` |
| R18 that exit's tail from byte 0 | caught | same as R15 |
| R19 a 10 s wait on every answer (F3's M2) | caught | 13 tests, including both own-id starts and the own-id refusal |

Also green on the repo tree, each file alone:
- the eight files above, plus `test_cli_contract.py` (59), `test_cli_client_version.py` (23) and `test_daemon_startup.py` (29);
- `test_cli.py -k "abandon or daemon or start or status"` (32);
- `ruff check .`

Live on Linux, port 18596, throwaway TOML, db, data and config dirs (`smoke_race2_r2.py`, `live_header.py`):
- 4 `stop-during-wait` rounds and 1 plain race: one start and one loser stopped at once each time, and nothing answering after the stop.
- A single start: the `/status` header equals `MCUSCOPED_START_ID` in the daemon's `/proc/<pid>/environ`, and the stop exits 0.
- `ps` showed no `mcuscope.daemon` afterwards.

### Doubts (round 2)
- The header is visible to any client that can reach the port, a LAN one included. It says "this daemon was started by `mcu daemon start`, instance X". I judged that harmless; say if the owner wants it only on loopback.
- `_await_spawned` now also writes the pid record, so its name undersells it slightly. The docstring says so.
- The `ci.yml` step is untested until the next push.

### Needs Windows (round 2)
- CI: the launcher test runs green and the new verify step passes.
- CI: `test_cli_ux.py`'s real `daemon start` through the uv venv launcher, which proves the id crosses the launcher.
- The desktop checklist line in `windows.md` is unchanged.

### Scratch (round 2, not deleted)
- `/home/daniel/tt-data/mcuscope-2026-09-26/race-fixbatch/r2/`: `host/` and `tools/` (tree copy), `mutate_r2.py`, `mutants_r2.log`.
- `/home/daniel/tt-data/mcuscope-2026-09-26/race-fixbatch/`:
  - `new_block_r2.py`;
  - `*.r2orig` (pre-round-2 copies of five test files);
  - `smoke_race2_r2.py`, `live_header.py`;
  - `smoke-r2/` (TOML, db, data and config dirs).

## Round 3

HEAD still 89a6af3, not committed. Findings G1 to G3 from `windows-fixdiff3.md`, each with a "Fixed:" line there. G4 was left as the reviewer recommended.

### G1: the winner rewrites the pid record (owner ruling)
- `cli_daemonctl.py` `_record_own_daemon`, called in `_await_spawned` after the id check and the "exited just after answering" check:
  - it makes the record name `proc.pid` unless it already names that or the serving daemon's own `pid` (its own claim behind a launcher);
  - a replaced pid gets a stderr note; a missing record is written silently;
  - a write failure is a warning, and the start still succeeds.
- The atomic write moved out of `_write_pid_record` into `_replace_pid_record`, shared by both callers. `_write_pid_record` keeps its live-record refusal.
- Why nothing else can be clobbered:
  - it runs only once the answer carries this start's id, which proves the daemon serving the record's host:port is this start's;
  - every losing start exits before it, and a losing start never writes over a live pid.
- The loser's reap cannot undo it in either order:
  - reap first: the record is gone, and the winner writes it;
  - rewrite first: the record names the winner, and the reap removes only a record naming its own child.
- Round 3 missed two straddles, the reap's read-then-remove and a later loser's read-then-replace. Round 4 closes both.
- Docs:
  - REVIEW class 7: the cell is now closed, with the residual; the false "/shutdown still stops it" text is gone.
  - `windows.md:96`: corrected.
  - SPEC 4 and the AI guide: updated together.
  - ARCHITECTURE: one line.
- Live LAN smoke (`smoke_lan_r3.py`, `http://100.98.9.94:18594`, `--token tokA|tokB --sim`, throwaway TOML, db, data, config and cache dirs):
  - 7 of 7 rounds: one `started`, one loser stopped at once, the record naming the winner, and `daemon stop` with the winner's token exit 0 (`stopped mcuscoped (pid N)`), with no daemon left.
  - 4 rounds printed the rewrite note (the loser's record had landed last).
  - Round 0 showed the other order: the winner warned "already names a running process", and the loser's reap had removed it by the time of the rewrite.

### G2
`answered_id: str | None = None` beside `body` and `refusal`.

### G3
- One handler from the spawn on: Popen and the `err_fh.close()` sit inside the `try`, so no Ctrl-C lands between Popen returning and the handler. `proc is None` (a Ctrl-C inside Popen) re-raises.
- Not closable here: a Ctrl-C inside `Popen.__init__` after the child exists but before Popen returns. That is CPython's window, microseconds while it reads the exec error pipe.

### Tests (all in `test_cli_daemon_stop_scope.py`)
- The loser's record lands last (a live pid), then the winner's rewrite, the note, and the loser's reap leaving the winner's record.
- The loser's reap first: the winner writes, with no note.
- A record naming the serving daemon itself is left.
- A losing start never takes a live record.
- A failed rewrite: a warning and exit 0, with a positive control that the rewrite was attempted.
- Ctrl-C at the post-spawn close names the child; Ctrl-C inside Popen gives `interrupted` and no traceback.
- `_start_with` gained a `before_answer` hook, which changes the record just before the answer.

### Revert table (round 3)
Fresh scratch copy (`r3/host`), `mutate_r3.py`, `PYTHONDONTWRITEBYTECODE=1`, an empty `PYTHONPYCACHEPREFIX`, `-p no:cacheprovider -p no:randomly`.
Files: stop_scope, cli_daemonctl, cli_ux, status_ppid_serial, cli_start_index_build, cli_daemon_start_pid. Baseline: 117 passed, 1 skipped.

| Mutant | Result | Caught by |
|---|---|---|
| G1a no rewrite | caught | `test_the_winner_takes_the_record_a_racing_loser_wrote_last`, `..._after_the_losers_reap_removed_the_record`, `test_a_record_rewrite_that_fails_...` (1) |
| G1b rewrite over the serving daemon's own record | caught | `test_a_record_naming_the_serving_daemon_itself_is_left` |
| G1c keep a live foreign record | caught | `..._a_racing_loser_wrote_last`, `test_a_record_rewrite_that_fails_...` |
| G1d no note | caught | `..._a_racing_loser_wrote_last` |
| G1e note on a missing record | caught | `..._after_the_losers_reap_removed_the_record` |
| G1f a failed rewrite raises | caught | `test_a_record_rewrite_that_fails_is_a_warning_not_a_failed_start` |
| G1g rewrite on a loss too | caught | `test_a_losing_start_never_takes_the_record` and 3 more |
| G1h the first write ignores a live record | caught | `test_daemon_start_refuses_when_another_daemon_serves_the_url`, `test_write_pid_record_refuses_a_record_naming_a_live_process` |
| G2 `answered_id` not initialised | survives, equivalent | unobservable while `_abandon_daemon` never returns; kept for the reader and the type checker |
| G3a close outside the handler | caught | `test_ctrl_c_right_after_the_spawn_names_the_child` |
| G3b no `proc is None` check | caught | `test_ctrl_c_inside_the_spawn_is_an_interrupt_not_a_traceback` |

(1) The G1a run also failed `test_cli_ux.py::test_lines_order_asc_reverses_the_json_output`. It ran while the LAN smoke loaded the machine, it is unrelated to the record, and it passed 3 of 3 alone on the same scratch copy.

Also green on the repo tree, each file alone:
- the six files above, plus `test_cli_contract.py` (59), `test_server_version_header.py` (6) and `test_daemon_start_id.py` (8);
- `ruff check .`
- No dashes in the touched files, and no daemon left running.

### Doubts (round 3)
- The rewrite note names the launcher's pid on Windows (`proc.pid`), while the start line names the serving pid. `stop` accepts either.
- `test_cli.py` was not run this round: another agent is editing it, as briefed.

### Scratch (round 3, not deleted)
- `/home/daniel/tt-data/mcuscope-2026-09-26/race-fixbatch/r3/`: `host/` and `tools/` (tree copy), `mutate_r3.py`, `mutants_r3.log`.
- `/home/daniel/tt-data/mcuscope-2026-09-26/race-fixbatch/`:
  - `smoke_lan_r3.py`;
  - `smoke-r3/` (TOML, db, data, config and cache dirs);
  - `test_cli_daemon_stop_scope.py.r3orig`.

## Round 4

HEAD still 89a6af3, not committed. Findings H1 to H6 and the `test_cli_ux` flake from `windows-fixdiff4.md`, each with a "Fixed:" line there.

### H4: detect and repair the straddles (owner ruling)
- I judged the suggested mechanism right, with three changes:
  - on Windows the put-back is `os.rename`, which never replaces an existing file and works on FAT too, while hard links need NTFS;
  - on POSIX it is `os.link`, with an `O_EXCL` fallback where the filesystem has no hard links;
  - the first write had the same straddle, so it is fixed too.
- `pidfile.remove_record_if(path, pid)`:
  - reads the record, and if it names `pid`, renames it aside (`<pid file>.<ownpid>.aside`) and reads it there;
  - deletes it if it still names `pid`;
  - otherwise puts it back with `_link_new`, which never replaces a newer record. A newer record stands and the aside copy is deleted.
  - A failed put-back keeps the aside file and says `mcuscope: could not put back <pid file>, which names pid N: <error>; it is kept as <aside>`.
- One helper serves every "remove only while it names X" site: the CLI's `_remove_pid_record` (reap, abandon, Ctrl-C, exited-after-answer), the daemon's `release`, `claim`'s stale removal (closing the 2026-08-10 residual), and `daemon stop`'s stale removal.
  - `daemon stop` now says `the stale pid file <path> (was pid N) changed or could not be moved, so it was left as it is` when the helper declines.
- `pidfile.create_record` makes the CLI's first write create-if-absent, with full content and never an empty record. So a record written after `_write_pid_record`'s read (a winner's rewrite) is never replaced.
  - A stale record is taken through `remove_record_if` first.
  - A record already naming the child (its own claim on POSIX) returns True without a warning.
- The winner's rewrite (`_record_own_daemon`) stays a deliberate replace. It needs no matching check: the id proves the daemon is this start's, and every remover now puts back what it did not judge.
- What remains, in class 7: while a record is aside (microseconds), a reader finds none, and a record created in that gap stands in place of the one put back.

### H1 to H3, H5, H6
- H1: the winner test is parametrized over a live and a dead loser pid (`dead_pid()`).
- H2: the whatever-the-pids test asserts no "now names" note.
- H3: a new test, `test_a_refusal_carrying_this_starts_id_takes_the_record_too`.
- H5: ARCHITECTURE's `pidfile.py` entry names the exception and the helper; the `_write_pid_record` docstring says "never overwritten here".
- H6: the first-write warning reads `warning: <pid file> names another process; replaced only if this start's daemon answers`.
- SPEC 4 states the create-if-absent write, the warning, the put-back and `stop`'s new stale message.
  - The AI guide is unchanged: neither message is in it, and its `daemon start` line already says the record names this start after a race.

### test_cli_ux flake (class 21)
- `OWN_MARKS` (`--chan marker --match "^(first|second|third)$" --limit 3`) is on every `lines` call in both order tests.
- No other stack test in the file reads the marker: the rest are `status`, `ports`, `attach`, `send` and `detach`.
- Class 21 gained the "a simulator's own periodic output" line.
- Reproducer on a scratch copy, a 16 s pause after each `default` read: the old tests fail 2 of 2 and the new ones pass 2 of 2.

### Tests
- `test_pidfile.py` (new):
  - only the pid named is removed, and a non-matching call moves nothing, with a positive control;
  - the straddle put-back;
  - the put-back without hard links (POSIX);
  - a newer record beats the put-back;
  - a failed put-back names the pid and keeps the aside file;
  - `create_record` never replaces;
  - the straddles inside `release` and inside `claim`'s stale removal.
- `test_cli_daemon_stop_scope.py`:
  - the reap straddling the rewrite, and a loser's write straddling the rewrite;
  - a stale record is taken, and the child's own claim is not warned about;
  - a live record warns once and is then taken;
  - H1 to H3;
  - `stop` leaving a stale record that changed.
- The existing `test_release_survives_a_record_it_cannot_remove` now fails the rename, which is what a held-open record fails on Windows. The rewrite-failure test fails the rewrite itself, since the first write no longer calls `_replace_pid_record`.

### Revert table (round 4)
Fresh scratch copy (`r4/host`), `mutate_r4.py`, `PYTHONDONTWRITEBYTECODE=1`, an empty `PYTHONPYCACHEPREFIX`, `-p no:cacheprovider -p no:randomly`.
Files: test_pidfile, stop_scope, cli_daemonctl, daemon_process, status_ppid_serial, cli_daemon_start_pid. Baseline: 130 passed, 1 skipped. All 22 caught.

| Mutant | Caught by (examples) |
|---|---|
| W1 remove without the aside | 9 tests, including both straddle put-backs |
| W2 put-back replaces | `test_a_newer_record_beats_the_put_back` |
| W3 failed put-back not said | `test_a_put_back_that_fails_names_the_pid_and_keeps_the_record` |
| W4 aside kept after a removal | `test_remove_record_if_removes_only_the_pid_it_names` |
| W5 aside kept when a newer record stands | `test_a_newer_record_beats_the_put_back` |
| W6 POSIX put-back by rename | the write straddle, newer-beats, `create_record` |
| W7 no fallback without hard links | `test_the_put_back_works_without_hard_links` |
| W8 `create_record` replaces | the write straddle, `test_create_record_never_replaces` |
| W9 `create_record` leaves its tmp | `test_create_record_never_replaces` |
| W10 `release` by read then remove | `test_release_puts_back_a_record_written_after_its_read` |
| W11 `claim`'s stale removal by remove | `test_claim_puts_back_a_record_written_inside_its_stale_removal` |
| W12 the CLI's removal by read then remove | `test_a_loser_reap_straddling_the_winners_rewrite_puts_it_back` |
| W13 the first write replaces | the write straddle |
| W14 a stale record not taken | `test_a_start_takes_a_stale_record` |
| W15 no early return for the child's own record | `test_a_record_the_child_already_claimed_is_not_warned_about` |
| W16 `stop` ignores a declined stale removal | `test_stop_leaves_a_stale_record_that_changed_before_its_removal` |
| W17 warning says "left it in place" | `test_a_start_over_a_live_record_warns_once_and_then_takes_it` |
| W18 the rewrite skips a dead record (H1's M1) | `..._a_racing_loser_wrote_last[False]` |
| W19 no short-circuit for the record naming the child (H2's M2) | the whatever-the-pids test and 2 more |
| W20 no rewrite after a refusal (H3's M7) | `test_a_refusal_carrying_this_starts_id_takes_the_record_too` |
| W21 no pre-check before the aside | `test_remove_record_if_...` (moves count) and 5 more |
| W22 put-back never attempted | 6 tests |

Also green on the repo tree, each file alone:
- the six files above, plus `test_cli_ux.py` (27), `test_cli_start_index_build.py`, `test_cli_contract.py` (59), `test_server_version_header.py`, `test_daemon_start_id.py` and `test_daemon_startup.py`;
- `test_cli.py -k "abandon or daemon or start or status or pid"` (32);
- `ruff check .`
- No dashes in the touched files.

Live LAN re-run (`smoke_lan_r3.py`, `http://100.98.9.94:18594`, throwaway dirs), 7 of 7 OK:
- one start, the loser stopped at once, the record naming the winner, and `daemon stop` exit 0 with nothing left;
- no `.aside` or `.tmp` left in the data dir.
- The first write is now create-if-absent, so the winner usually prints the warning and then the note.

### Doubts (round 4)
- Windows is unverified: `os.rename` failing with `FileExistsError` on an existing target, and the helper's rename aside of a record another process holds open. That returns False and leaves the record, as `os.remove` did before.
- An `.aside` or `.tmp` file left by a crash between steps is not swept. Nothing reads them.

### Scratch (round 4, not deleted)
- `/home/daniel/tt-data/mcuscope-2026-09-26/race-fixbatch/r4/`: `host/` and `tools/` (tree copy), `mutate_r4.py`, `mutants_r4.log`.
- `/home/daniel/tt-data/mcuscope-2026-09-26/race-fixbatch/`: `pidfile.py.r4orig`, `test_cli_ux.py.r4orig`.

## Round 5

HEAD still 89a6af3, not committed. Findings J1 to J7 from `windows-fixdiff5.md`, each with a "Fixed:" line there.

### What changed
- J1: `dirs.retry_sharing(call, *args)` now holds the retry loop that was inside `config.replace_atomic`, which calls it.
  - It retries `PermissionError` only, with the same 10 attempts and 0.9 s.
  - `pidfile` uses it for the aside (`os.replace`) and the Windows put-back (`os.rename`, also used by `create_record`).
  - Class 13's invariant, sweep and a new line say so. The sweep is clean: only a comment and a docstring mention the calls.
- J2: `_link_new`'s no-hard-link fallback closes, then removes `dst` on a failed write, and re-raises.
- J3: the no-replace rule is tested with and without hard links, for both the put-back and `create_record`.
- J4: the Ctrl-C case is closed rather than documented.
  - A `finally` in `remove_record_if` puts the record back when an interrupt lands between the aside and the put-back. It drops the copy if a newer record stands, and never touches a copy kept on purpose after a failed put-back.
  - The redundant "dropped" state is deleted (mutant Y9 survived): the `finally` covers it.
  - The class 7 residual and the docstring list what remains:
    - a reader finds no record;
    - a record created in the gap stands;
    - a remover that runs in the gap skips, so a stale record comes back;
    - a failed put-back is kept aside and said;
    - a process killed outright loses the record, including the daemon's SIGTERM handler re-entered by a second SIGTERM.
- J5: the aside and tmp names carry `secrets.token_hex(4)`.
- J6:
  - SPEC 4: "never replaces a live record, and takes a stale one only while it still names the dead pid", plus the put-back failure message and its two prefixes.
  - ARCHITECTURE: the put-back line.
  - `remove_record_if(..., note_prefix=)`: the daemon's default is `mcuscoped: `, and the CLI's three callers pass `warning: `. An unreadable copy says "names no readable pid".
- J7: a test for `daemon stop`'s "pid still running, no usable /status" branch.
- `windows.md`: three checklist lines (the CI run of the `os.rename` put-back, a held-open record, the J1 retry).

### Tests
- `test_pidfile.py`:
  - the failed put-back, parametrized over a pid and a garbled copy, with the name found by glob;
  - no-replace with and without hard links;
  - a failed write without hard links;
  - the aside riding out a sharing violation;
  - the Windows put-back retrying `PermissionError` but not `FileExistsError` (platform patched to win32);
  - Ctrl-C before judging, inside the put-back, and inside it with a newer record;
  - per-call names.
- `test_cli_daemon_stop_scope.py`: the CLI's failed put-back is a `warning: `; stop keeps a live pid's record with no usable /status.
- Updated: the stale-stop test's wrapper takes `**kw`.

### Revert table (round 5)
Fresh scratch copy (`r5/host`), `mutate_r5.py`, `PYTHONDONTWRITEBYTECODE=1`, an empty `PYTHONPYCACHEPREFIX`, `-p no:cacheprovider -p no:randomly`.
Files: test_pidfile, stop_scope, cli_daemonctl, daemon_process, config_api. Baseline: 175 passed, 1 skipped. Final run: all 17 caught.

| Mutant | Caught by |
|---|---|
| Y1 aside without the retry | `test_the_aside_rides_out_a_sharing_violation` |
| Y2 Windows put-back without the retry | `test_the_windows_put_back_retries_a_sharing_violation_but_not_an_existing_file` |
| Y3 `retry_sharing` retries any `OSError` | same (`FileExistsError` retried) |
| Y4 `replace_atomic` without the retry | `test_config_api.py::test_replace_atomic_rides_out_a_windows_sharing_violation` |
| Y5 fallback keeps an empty record (J2) | `test_a_failed_write_without_hard_links_leaves_no_empty_record` |
| Y6 fallback truncates (X1, J3) | `test_neither_path_replaces_a_newer_record[False]` |
| Y7 no put-back when interrupted before judging | `test_ctrl_c_before_the_aside_is_judged_puts_it_back` |
| Y8 no put-back when interrupted mid-way, or on a newer record | 5 tests |
| Y9 "dropped" state not removing the copy | survived, so the branch was deleted as redundant |
| Y10 copy kept after a removal | `test_remove_record_if_removes_only_the_pid_it_names` and 1 more |
| Y11 a failed put-back deletes the copy | both `test_a_put_back_that_fails_...` params |
| Y12 aside name without the random part | `test_aside_and_tmp_names_differ_per_call` |
| Y13 tmp name without the random part | same |
| Y14 prefix ignored | 3 tests |
| Y15 the CLI passes no prefix | `test_the_clis_failed_put_back_is_a_warning` |
| Y16 "names pid None" | `..._fails_...[garbled-no readable pid]` |
| Y17 `stop` drops a live pid's record (X23, J7) | `test_stop_keeps_the_record_of_a_live_pid_with_no_usable_status` |
| Y18 interrupted put-back keeps the copy beside a newer record | 4 tests |

Also green on the repo tree, each file alone:
- the five files above, plus `test_status_ppid_serial.py`, `test_cli_daemon_start_pid.py`, `test_cli_ux.py` (27), `test_cli_contract.py` (59), `test_daemon_startup.py` and `test_update_check.py` (38);
- `test_cli.py -k "abandon or daemon or start or status or pid or config"` (32);
- `ruff check .`
- No dashes in the touched files, and no daemon left running.

### Doubts (round 5)
- The Windows branches (`os.rename` no-replace, the sharing retry on a real handle) are exercised only with `sys.platform` patched and `os.rename` faked on Linux. The `windows.md` lines cover the real run.
- A second interrupt inside the `finally`, or a hard kill in the gap, can still lose a record. That is listed in the residual.
- `dirs.py` now holds one filesystem helper beside the directory helpers. I put it there because `pidfile` must stay light, and `config` imports `tomlkit`.

### Scratch (round 5, not deleted)
- `/home/daniel/tt-data/mcuscope-2026-09-26/race-fixbatch/r5/`: `host/` and `tools/` (tree copy), `mutate_r5.py`, `mutants_r5.log`.
- `/home/daniel/tt-data/mcuscope-2026-09-26/race-fixbatch/`: `pidfile.py.r5orig`, `dirs.py.r5orig`, `config.py.r5orig`.

## Round 6

HEAD still 89a6af3, not committed. Findings K1 to K8 from `windows-fixdiff6.md`, each with a "Fixed:" line there.

### Changes
- K1: in `remove_record_if`, the aside rename runs inside the `try` after `state = "judging"`, so an interrupt as it returns is put back.
- K2: the no-hard-link fallback's cleanup catches `BaseException`. An interrupted write removes its empty `dst`, and the `finally` then puts the only copy back instead of meeting `FileExistsError` and deleting it.
- K3: a test for the `state != "kept"` guard.
- K4: the `windows.md` J1 line is the reviewer's timed in-process check.
- K5:
  - the retry bound is a constant in `dirs.retry_sharing`;
  - `attempts` is dropped from it and from `replace_atomic`, and the one test that passed it patches the sleep instead;
  - a policy test pins 9 backoff sleeps totalling 0.9 s, the same error re-raised, and no retry of `FileExistsError`.
- K6: the retry stays on every platform, and the docstrings say what POSIX pays (0.9 s on a permanent EACCES/EPERM).
  - Not gated to win32: the existing `replace_atomic` test pins the loop on both platforms, and gating would change `replace_atomic` and leave the loop tested only on Windows.
- K7: the `lexists` check is deleted as redundant.
- K8:
  - the silent retry after a Ctrl-C is in the class 7 residual and the docstring;
  - SPEC 4 gives `names no readable pid`;
  - the J7 test asserts that `victim.wait(0.5)` times out instead of trusting `poll()`.

### Files changed this round
- `host/mcuscope/pidfile.py`, `host/mcuscope/dirs.py`, `host/mcuscope/config.py`;
- `host/tests/test_pidfile.py`, `host/tests/test_cli_daemon_stop_scope.py` (the J7 assertion), `host/tests/test_config_api.py` (the `attempts=2` call);
- `docs/SPEC.md`, `docs/REVIEW.md` (class 7 residual);
- `docs/review/2026-09-23-opus55/registry-triage/windows.md` (the J1 line), `docs/review/2026-09-23-opus55/windows-fixdiff6.md`, this file.
- Pre-round copies: `~/tt-data/mcuscope-2026-09-26/race-fixbatch/{pidfile,dirs,config}.py.r6orig`.

### Revert table (round 6)
Fresh scratch copy (`r6/host`), `mutate_r6.py`, `PYTHONDONTWRITEBYTECODE=1`, an empty `PYTHONPYCACHEPREFIX`, `-p no:cacheprovider -p no:randomly`, over test_pidfile, stop_scope, cli_daemonctl, daemon_process and config_api (baseline 180 passed, 1 skipped). All 8 caught.

| Mutant | Caught by |
|---|---|
| V1 aside rename outside the `try` (K1) | `test_ctrl_c_as_the_aside_rename_returns_puts_it_back` |
| V2 fallback cleanup on `OSError` only (K2) | both `test_ctrl_c_inside_the_fallback_*` |
| V3 the `finally` retries a failed put-back (K3, Z1) | `test_a_failed_put_back_is_not_retried_behind_its_note` |
| V4 no backoff (Z3) | `test_the_sharing_retry_is_bounded_and_backs_off` |
| V5 bound 3 (Z4) | same |
| V6 the last error swallowed (Z5) | same, and `test_replace_atomic_rides_out_a_windows_sharing_violation` |
| V7 `stop` signals the live pid but keeps its record | `test_stop_keeps_the_record_of_a_live_pid_with_no_usable_status` |
| V8 the `finally` never puts back | 8 tests |

Z2 (`lexists`) is moot: the check is deleted. Z6 is moot: `replace_atomic` has no `attempts` any more.

Also green on the repo tree, each file alone: the five files above, plus `test_update_check.py` (38) and `test_cli_contract.py` (59); `ruff check .`; no dashes in the touched files; no daemon running.

### Doubts (round 6)
- A second interrupt inside the `finally`, and a real SIGINT (every interrupt here is raised from a patched call), are still not covered.
- The Windows branches have still run only on Linux.

### Scratch (round 6, not deleted)
- `/home/daniel/tt-data/mcuscope-2026-09-26/race-fixbatch/r6/`: `host/` and `tools/` (tree copy), `mutate_r6.py`, `mutants_r6.log`.
