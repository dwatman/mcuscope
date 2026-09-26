# Fix-diff review 4: round 3 of the start race (uncommitted tree on 89a6af3)

HEAD 89a6af3ec4b19ca798ab384a5f2005936d88ddf2. Target: the "Round 3" section of `windows-fixbatch-race.md` (G1 to G3 of `windows-fixdiff3.md`).
The rewrite is sound: in no case checked can it take the record of a daemon it should not. The Ctrl-C handling is right on every exit.
The gaps are three untested branches and an incomplete residual.

## Findings

### H1 LOW: rewriting a record that names a dead pid is untested
- Where: `cli_daemonctl.py` `_record_own_daemon`. Every rewrite test writes a live pid (`victim`) or no record.
- Scenario: the loser stops its child at once, so when the winner rewrites, the loser's record most likely names a dead pid, with the loser's reap still to come.
  - A change that replaces only a live record (mirroring `_write_pid_record`'s liveness test) leaves the dead record for the reap to remove, which reopens G1 in its likeliest order.
  - Mutant M1 (skip a dead record) survives.
- Fix: parametrize `test_the_winner_takes_the_record_a_racing_loser_wrote_last` over `victim.pid` and a dead pid (the pid of an exited, reaped `Popen`), asserting the note and `read_pid_record == child.pid`.
  - Its trailing `_remove_pid_record(pid_path, victim.pid)` step cannot fail once the assert above it passes. Drop it, or keep it as the dead-pid leg's reap.
- Fixed: `test_the_winner_takes_the_record_a_racing_loser_wrote_last` is parametrized over a live and a dead loser pid; its reap step is kept as the after-leg.

### H2 LOW: nothing pins the absence of the note on a plain start
- Where: the `recorded == pid` short-circuit in `_record_own_daemon`.
- Scenario: without it, every normal start on Linux prints `note: <pid file> named pid N; it now names this start's pid N`, which is the same pid. Mutant M2 survives.
- Fix: add `assert "now names" not in err` to `test_an_answer_with_this_starts_id_is_its_daemon_whatever_the_pids`, where the record already names the child.
- Fixed: `test_an_answer_with_this_starts_id_is_its_daemon_whatever_the_pids` asserts no "now names" note.

### H3 LOW: no test covers the rewrite after a refusal that carries this start's id
- Where: `cli.py` `_await_spawned`. The rewrite runs for a 401/403/429 that carries the id, and in that case `serving` falls back to `proc.pid`.
- Scenario: a CLI without the token races on a LAN URL against a daemon whose token comes from its config. A refactor that ties the rewrite to `body` (to use its `pid`) would leave that winner unrecorded, which is G1 again. Mutant M7 survives.
- Fix: a test using `_start_with(..., _REFUSED_OWN, before_answer=_record(victim.pid))`, asserting exit 0, the token note, the rewrite note and `read_pid_record == child.pid`.
- Fixed: `test_a_refusal_carrying_this_starts_id_takes_the_record_too`.

### H4 LOW: the stated residual misses the reap's read-then-remove
- Where: class 7 (`REVIEW.md:176`), `windows-fixbatch-race.md:255-258` ("cannot undo it in either order"), and `_reap_losing_start` -> `_remove_pid_record` (`cli_daemonctl.py:215`).
- Scenario, with only two starts:
  - the loser's `_remove_pid_record` reads a record naming its own child;
  - the winner's `os.replace` lands;
  - the loser's `os.remove` then deletes the winner's record.
  - Both steps follow the winner's first answer, so their timing is correlated. The documented residual (a later loser's read-then-replace) also needs a third start whose pre-spawn probe saw nothing.
  - The window is microseconds either way, the same class as the 2026-08-10 cell.
- Fix (owner pick; recommended first):
  - Name both straddles in the class 7 residual, and correct "either order" to "either order; only an interleaving inside the reap's read-then-remove can".
  - Or close it: a start that lost does not remove the record at all.
    - Its record then names a dead child, which is stale, and stale records are already safe: `_write_pid_record` and `claim` overwrite one, and `stop` signals only a pid `/status` names.
    - The winner's rewrite replaces it in every order. The cost is a stale `.pid` left after a race that no CLI won.
- Fixed (owner ruling: detect and repair): every "remove only while it names X" (the CLI's `_remove_pid_record`, `release`, and the stale removals by `claim` and `daemon stop`) goes through `pidfile.remove_record_if`. It renames the record aside, reads it there, and deletes it or puts it back without replacing a newer record (Windows rename, POSIX hard link, `O_EXCL` fallback; a failed put-back keeps the file aside and names its pid). The CLI's first write is create-if-absent (`create_record`), which closes the write straddle too. Class 7 residual: the microseconds while a record is aside.

### H5 NIT: the rule "a live record is left alone" now has one exception, which the rule's statements do not mention
- `ARCHITECTURE.md:96`: "a live one is left alone whoever it names" is the pid record rule CLAUDE.md points readers to. Append "(one exception: `daemon start` rewrites it once its answer carries its start id, `cli_daemonctl.py`)".
- `_write_pid_record` docstring: "a record naming a *running* process is never overwritten". Make it "is never overwritten here", since `_record_own_daemon` in the same module does overwrite one.
- Fixed: ARCHITECTURE names the exception and the helper; the `_write_pid_record` docstring says "never overwritten here (the one exception is `_record_own_daemon`)".

### H6 NIT: two stderr lines that contradict each other
- When the first write is refused (a recycled pid in a stale record, or the daemon's claim landing first), the start prints `warning: <pid file> already names a running process; left it in place`. After the id matches, it prints `note: ... it now names this start's pid M`.
- Fix: drop "left it in place" from the warning, or word it "left it in place for now".
- Fixed: the warning now reads `<pid file> names another process; replaced only if this start's daemon answers`, pinned with the later note by `test_a_start_over_a_live_record_warns_once_and_then_takes_it`.

## Checked and sound
- Can the rewrite clobber a record it should not?
  - It runs only after this start's id is echoed, and the record is keyed by host:port. So a second daemon on another URL, or on another data dir, has another file.
  - `localhost` vs `127.0.0.1` key separately, and at most one of them binds.
  - A live pid in the record is not serving this host:port, because the id proves this start's daemon is. It can be:
    - the loser's child, stopped at once;
    - a recycled pid;
    - a hand-started `mcuscoped` that passed the port probe and then loses the bind; on its way out, `release` removes only its own pid.
  - `daemon restart` goes through the same `_start_daemon`. An old daemon that is still exiting gets replaced, and its `release` then skips the record, which is better than before.
  - A hand-started `mcuscoped` that wins makes the CLI start lose, so nothing is rewritten.
- Windows:
  - the record gets `proc.pid`, the launcher, which is the pid `pidfile`'s docstring prefers for the CTRL_BREAK stop;
  - the daemon's own claim (`serving`) is left alone.
  - Under a launcher chain two deep, `stop` matches neither pid. That is the same as the first write before this round, not a regression.
- A daemon whose claim backed off never releases, so a rewritten record goes stale after the daemon exits. That is harmless, and the Windows launcher record already behaves the same way.
- Ctrl-C:
  - inside Popen with no child yet: re-raised, and the CLI prints `interrupted`;
  - at the close, or in the write, the wait, the reap or the abandon: a live child is named and its record kept, and an exited child's record is removed;
  - after `started`, inside `webbrowser.open`: named, exit 1, as SPEC says.
  - The message names `proc.pid`, which is the only pid known before an answer.
  - Pre-existing and left alone: a Ctrl-C between the temp-file write and `replace_atomic` in `_replace_pid_record` leaves `<pid file>.<clipid>.tmp`, because only `OSError` is cleaned up.
- SPEC 4, the AI guide, class 89, `windows.md:91-96` and ARCHITECTURE agree with the code (`DAEMON_STOP_GRACE_S` = 10).
- The `+` lines of `git diff` and the new files contain no U+2013 or U+2014 characters.
- `ruff check .` is clean.

## Revert table
Scratch copy `~/tt-data/mcuscope-2026-09-26/fixdiff4/host`, run by `mutate4.py`: every anchor checked before any write, and the files restored and compared after each mutant.
Run with `PYTHONDONTWRITEBYTECODE=1 -p no:randomly -p no:cacheprovider` over `test_cli_daemon_stop_scope.py`, `test_cli_daemonctl.py` and `test_cli_daemon_start_pid.py`. Baseline: 79 passed, 1 skipped. Log: `mutants4.log`.

| Mutant | Result | Caught by |
|---|---|---|
| M1 `_record_own_daemon` skips a dead record | survives | H1 |
| M2 no `recorded == pid` short-circuit | survives | H2 |
| M3 Ctrl-C keeps an exited child's record | caught | `test_ctrl_c_after_the_child_exited_removes_its_record` |
| M4 Ctrl-C swallowed (return, not raise) | caught | 5 Ctrl-C tests |
| M5 Ctrl-C removes a live child's record | caught | `..._in_the_readiness_wait_...`, `..._while_stopping_a_losing_child_...` |
| M6 serving pid not passed | caught | `test_a_record_naming_the_serving_daemon_itself_is_left` |
| M7 no rewrite after a refusal | survives | H3 |
| M8 Ctrl-C message dropped | caught | 4 Ctrl-C tests |

The first M1 run was "caught", but only because the mutant called `pid_running`, and tests that patch `sys.platform` to `win32` sent it into ctypes. It was rewritten with `os.kill(pid, 0)` and then survived.

## test_cli_ux flake: a test defect, not the product (class 21)
- Test: `test_cli_ux.py::test_lines_order_asc_reverses_the_json_output`. `r3/mutants_r3.log` names it, but no failure output was saved.
- Cause: the sim emits an unsolicited `!m ... sim marker 1` 15 s after the stack starts (`sim.py:476`), filed under chan `marker`. The two order tests assume their three `mcu mark` rows are the only markers. At idle they take 1.5 to 2 s; under load they can pass 15 s.
  - `/marker` answers only after its commit (`store.py` resolves the future after `commit()`), so there is no read-after-write race.
- Reproduced on the scratch copy:
  - Deterministic: a 16 s pause after `default` fails the JSON test (`asc[0]` is `second`, id 14, against `first`, id 10).
  - Under load: with 64 busy processes and `sys.setswitchinterval(1e-4)`, the sibling `test_lines_order_desc_reverses_the_text_output` failed (call 17.4 s; `default[0]` was `'13:08:50.217 marker| second'`). With 32 processes, calls took 9.5 to 14.4 s and all 6 passed. Log: `load64.log`.
- Fix (verified on the scratch copy with the 16 s pause: passes): add `"--match", "^(first|second|third)$"` to every `lines` call in both order tests.
- Registry: class 21 already covers wall-clock thresholds. Add one line to it: "A simulator's own periodic output (the 15 s `!m` marker, the 10 Hz CAN heartbeat) matched by a test's filter is a clock threshold: filter on the test's own text."
  - Sweep: stack tests that filter `chan marker` or `can` and count rows or compare two reads.
  - The other `--chan marker` tests in `test_cli.py` and `test_e2e.py` assert substrings or the order of their own rows, so they are not affected.
- Not class 91: no go-signal is involved.
- Fixed: `OWN_MARKS` (`--match "^(first|second|third)$"`) on every `lines` call in both order tests; no other `test_cli_ux.py` stack test reads the marker. Class 21 has the line. The 16 s pause reproducer on a scratch copy fails both old tests and passes both new ones.

## Not covered
- Windows: the launcher record through the rewrite, and the note naming the launcher. Launcher chains deeper than one.
- The live LAN race was not re-run. The fix agent's 7 of 7 was not repeated.
- `test_cli.py` and the JS suite were not run. I made no edits to tracked files.
- The JSON order test itself was not caught failing under load, only its text sibling. The mechanism is the same.

## Scratch (not deleted)
- `~/tt-data/mcuscope-2026-09-26/fixdiff4/`: `host/`, `tools/` (the tree copy, restored and diffed identical to the repo), `mutate4.py`, `mutants4.log`, `swi.py`, `load64.log`, `test_cli_ux.py.orig`.
