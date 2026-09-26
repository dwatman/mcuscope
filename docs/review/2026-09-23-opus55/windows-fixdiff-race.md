# Fix-diff review: the lost start race (2ef4c5f, docs in 89a6af3)

HEAD reviewed: 89a6af3.
Scope: `_reap_losing_start` (`host/mcuscope/cli_daemonctl.py:262-296`), its call in `cli.py:2743-2749`, the three tests in `test_cli_daemon_stop_scope.py:331-389`, and the SPEC 4 / AI guide / ARCHITECTURE / class 89 text.

## Findings

### RACE-1 MEDIUM: the 10 s wait keeps the takeover window open; the child still takes over, serves, and is then killed as "still starting"
- Where: `cli_daemonctl.py:280-292`.
- Scenario, reproduced live on Linux 7 of 7 rounds (`smoke_race.py stop-during-wait`, `smoke_race2.py`):
  - Two `mcu daemon start` at once; `mcu daemon stop` as soon as `/status` answers, while the loser's CLI is inside its wait.
  - The stop exits 0. The loser's child then takes the lock and the port: `/status` names the loser's child from t=1.4 s until about t=11 s.
  - The loser's CLI then reports `stopped this start's own process (pid N), still starting after 10s`, which is false: the child was serving for about 9.5 s.
  - During that time a `daemon start` gets `daemon already running`, and with ports configured the child opens them.
- The fix turns a permanent takeover into one of up to 10 s. The takeover itself is the class 89 defect.
- Fix: terminate the child as soon as the loss is decided, then wait. Before `pidfile.claim` the daemon has SIGTERM at SIG_DFL, so it dies cleanly. The lock refusal text is lost, but the message already names the winner.
  If the wait is kept, re-probe `/status` during it and word the note from what answers.
- Fixed: the reap stops a live child at once (`_reap_losing_start`); live on Linux 5 of 5 `stop-during-wait` rounds, nothing answered after the stop. See `windows-fixbatch-race.md`.

### RACE-2 MEDIUM: the Windows terminate path was never driven live
- Where: `cli_daemonctl.py:283-292`.
- On a Windows venv `proc` is the launcher shim, so `terminate()` is TerminateProcess on the shim and `wait()` returns once the shim is gone.
  Whether the real `python.exe` dies too depends on the launcher's job object (kill-on-close). The code assumes that and never checks it.
- The live re-drive in `registry-triage/windows.md` says the loser "printed its lock refusal". So the child exited by itself, and this branch never ran.
- Failure if the grandchild survives: the loser prints "stopped", removes the record (it names the shim), and the real daemon takes over with no record. That is the original defect, with a false message on top.
- Fix: on Windows, drive it with `LOSER_EXIT_WAIT_S` patched to 0.5 in a scratch copy (or RACE-1's terminate-at-once), then check the process list for the grandchild.
  If it survives, stop the tree (for example `taskkill /T /F /PID <shim>`).
- Fixed: relies on the launcher's kill-on-close job, pinned by a Windows-only CI test through the real venv launcher (not yet run; a CI skip fails the job).

### RACE-3 LOW: no kill escalation, and the 5 s post-terminate wait is shorter than a graceful stop
- Where: `cli_daemonctl.py:285-290`, compared with `_abandon_daemon` at `:316-325`.
- Scenario (probe P1, a real child with SIGTERM ignored): the start exits 1 with `could not be stopped`, and the child is still alive afterwards. `_abandon_daemon` would have sent SIGKILL.
- Once the child is serving (RACE-1), SIGTERM runs uvicorn's graceful shutdown: `GRACEFUL_SHUTDOWN_S` = 5, plus the lifespan flush.
  So `wait(timeout=5)` can expire on a daemon that is in fact stopping, and the start reports "could not be stopped". `DAEMON_STOP_GRACE_S` is 10 for this reason.
- Fix: one shared terminate, wait, kill, wait helper used by both `_abandon_daemon` and `_reap_losing_start`.
- Fixed: `_stop_child`, `DAEMON_STOP_GRACE_S` after the terminate and after the kill, used by both.

### RACE-4 LOW: Ctrl-C during the silent wait leaves the child running, and no pid is named
- Where: `cli_daemonctl.py:281`, reached from `cli.py:2747`.
- Nothing is printed before the wait, so the start hangs for up to 10 s with no output, which invites Ctrl-C.
- Probe P3: `main` maps the KeyboardInterrupt to `interrupted`, exit 1. The child is left alive and its record stays, but the message does not name its pid. This is class 89's own invariant.
- Fix: print a one-line note naming the pid before waiting. Catch KeyboardInterrupt in the reap, terminate the child, then re-raise. RACE-1's fix removes most of the wait anyway.
- Fixed: Ctrl-C anywhere after the spawn names a still-running child (the terminate is already sent in the reap); no pre-wait note, since the wait is now only a stop's.

### RACE-5 LOW: the tail shown is not the child's own
- Where: `cli_daemonctl.py:296`.
- The `.err` file is shared by every start for the host:port. Everything after `err_start` includes the winner's lines and any third start's. Waiting 10 s widens that span.
- Probe P2: the winner wrote 22 lines around the loser's refusal. The 10-line tail showed 10 winner lines and dropped the refusal.
- Several docs overclaim this:
  - `windows.md` "shows the child's own stderr tail"
  - SPEC 4 "shows the child's stderr lines"
  - class 89 prose.
- Fix: word them as "this start's lines of the shared file", or have the daemon prefix its stderr lines with its pid and filter on that.
- Fixed: docs worded as the lines appended since this start began. No pid prefix: it would break the whole-line `building index` match and clutter a foreground daemon's stderr.

### RACE-6 LOW (pre-existing sibling, class 89): the token-refusal readiness path reports success for a losing child
- Where: `cli.py:2753-2767`.
- Scenario (probe P4): the winner answers 401/403/429 to this CLI. For example, the two starts used different `--token` values.
  The loser prints `started mcuscoped (pid <own child>)`, exits 0, and does not reap. Its child is still in the lock retry and fails there, or takes over later.
- The class 89 sweep entry (`REVIEW.md:1001`) lists `_start_daemon`'s exits and omits this path and the KeyboardInterrupt path.
- Fix: add both to the class 89 exit list.
  On a refusal, give `proc` the lock-retry span (about 3 s). If it exits, fail with its tail, since the answer came from someone else.
- Fixed: round 2 decides on a start id the daemon echoes on every response, refusals included, so no settle is needed; the refusal path and Ctrl-C are in the class 89 exit list.

### RACE-7 NIT: the docs claim more than the code does
- AI guide, `cli.py:3131-3132`: "never left to take over". The unstoppable branch and Ctrl-C both leave it, and RACE-1 shows it takes over during the wait.
- SPEC 4, `SPEC.md:1223`, says "waits up to 10 s ... stopping it". It omits:
  - the extra 5 s after the stop;
  - the unstoppable outcome: exit 1, `could not be stopped`, pid file kept.
- The residual "winner left with no pid record when the loser's record got in first" appears only in `windows.md`. Seen live on Linux too, in the `sequential` round 1 (`no local pid record`). It is a class 7 matrix cell and belongs there.
- Fixed: AI guide and SPEC 4 reworded (unstoppable, Ctrl-C and refusal outcomes stated); the residual is a class 7 accepted cell.

### RACE-8 LOW: three changed branches have no test (see the table)
- Untested:
  - the `suppress(OSError)` around `terminate` (M8);
  - the 10 s value that SPEC promises (M9): the test asserts `waits == [LOSER_EXIT_WAIT_S]`, so any value passes;
  - `start=err_start` in the reap (M10): the test's `.err` file is fresh, so 0 equals `err_start`.
- Fix:
  - prefill the `.err` file with a line and assert that line is absent from the output;
  - assert `waits == [10.0, 5]` in the "still starting" test;
  - drive `terminate` raising `PermissionError`: assert the "could not be stopped" text and that the record is kept (probe P5 shows the path works).
- NIT: tests 1 and 2 assert the record is gone but never that it existed first. Test 3 is the only positive control for that setup.
- Fixed: all three plus the NIT (each test records what the pid file named at the probe); every changed branch now caught, 19 mutants.

Checked and sound: `wait` and `terminate` act on this process's own `Popen`, so pid reuse cannot redirect them before the reap (class 82). The record is removed only while it names the child: M2 is caught by `test_cli_daemonctl.py::test_daemon_start_refuses_when_another_daemon_serves_the_url`. The exit code is 1 on every branch. There are no em or en dashes in the touched files.

## Revert verification

Scratch copy of HEAD `host/`, mutants applied one at a time by `mutate.py`, run against `tests/test_cli_daemon_stop_scope.py`. The survivors were re-run with `tests/test_cli_daemonctl.py` added.

| Mutant | Result | Caught by |
|---|---|---|
| M1 skip the reap | caught | all three `test_a_lost_race_*` (checked without `-x`) |
| M2 remove the record unconditionally | caught | `test_cli_daemonctl.py::test_daemon_start_refuses_when_another_daemon_serves_the_url` |
| M3 keep the record after exit | caught | `test_a_lost_race_waits_for_its_child...` |
| M4 unstoppable branch deletes the record | caught | `test_a_lost_race_child_that_cannot_be_stopped...` |
| M5 drop the "stopped" note | caught | `test_a_lost_race_stops_a_child...` |
| M6 drop the stderr tail | caught | `test_a_lost_race_waits_for_its_child...` |
| M7 no `terminate` | caught | `test_a_lost_race_stops_a_child...` |
| M8 `terminate` not under `suppress(OSError)` | **survives** | none |
| M9 `LOSER_EXIT_WAIT_S = 0` | **survives** | none |
| M10 tail read from byte 0 | **survives** | none |
| M11 tail dropped from the `die` message | caught | `test_a_lost_race_waits_for_its_child...` |
| M12 terminate at once, no wait | caught | `test_a_lost_race_waits_for_its_child...` (this is RACE-1's suggested design, so that test would change with it) |

Also run, all green: `test_cli_daemon_stop_scope.py` (24), `test_cli_daemonctl.py` and `test_cli_contract.py` (89), and ruff on the three touched files.

## Not covered
- Windows: nothing was run there. RACE-2 is the gap that matters.
- A live race with serial ports configured. RACE-1's claim that the takeover opens them is inferred from daemon startup, not observed.
- Launcher chains other than the plain venv shim (Store Python without a venv, uv trampolines), where `ppid` might not equal `proc.pid` and the real winner would now kill its own daemon after 10 s. Before the fix it was left running.
- The whole suite and the JS suite (not run, by brief).

## Scratch
- `/home/daniel/tt-data/mcuscope-2026-09-26/race-fixdiff/` holds:
  - a `git archive` copy of `host/` and `tools/`;
  - `mutate.py`;
  - `host/tests/test_scratch_race_probe.py` (probes P1 to P5);
  - `smoke_race.py` and `smoke_race2.py`;
  - `smoke/` (throwaway TOML, db, data, config and cache dirs, port 18599).
- No daemon is left running. The process list was checked after each round.
