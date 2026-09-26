# Fix-diff review 6: round 5 of the start race (uncommitted tree on 89a6af3)

HEAD 89a6af3ec4b19ca798ab384a5f2005936d88ddf2. Target: the "Round 5" section of `windows-fixbatch-race.md` (J1 to J7), isolated by diffing against the `.r5orig` copies.
The J1 move is behaviour-neutral for `replace_atomic`, and the `finally` cannot double-restore or clobber. Two interrupt windows are still open, and one guard and the retry policy are untested.

## Findings

### K1 LOW: a Ctrl-C landing as the aside rename returns still loses the record silently
- Where: `pidfile.py:258-262`. The rename runs before `state = "judging"` and before the `try`, so the `finally` does not cover it.
- Scenario: SIGINT arrives during the rename syscall. CPython raises it at the eval-breaker check after the builtin returns, inside `retry_sharing`, with the rename done.
  - This is the J4 outcome the round set out to close: the record is gone, `.aside` is left, and nothing is said.
- Probe P1 (`retry_sharing` patched to rename, then raise `KeyboardInterrupt`): record absent, `rec.pid.<pid>.<hex>.aside` left.
- Fix: set `state = "judging"` first and move the rename into the `try`, keeping its `except OSError: return False`. The name is random per call, so `lexists(aside)` is False when the rename did not happen, and the `finally` does nothing.
  - Verified on scratch: P1 restores the record. test_pidfile, stop_scope and cli_daemonctl give 126 passed, 1 skipped.
  - Test: the P1 patch, asserting the record reads DEAD and `_asides(path) == []`.
- Fixed: `state = "judging"` is set first and the rename runs inside the `try` (still `return False` on `OSError`). Test: `test_ctrl_c_as_the_aside_rename_returns_puts_it_back` (P1's patch).

### K2 LOW: a Ctrl-C inside the no-hard-link fallback leaves an empty record, and a put-back also deletes the only copy
- Where: `pidfile.py:324-336`. The J2 cleanup catches `OSError` only, so `KeyboardInterrupt` between the `O_EXCL` create and `os.write` leaves `dst` empty.
- Scenario, put-back (probe P2): `state` is `"putting"`, so the `finally` retries `_link_new`. The retry meets the empty `dst` (`FileExistsError`) and removes the aside.
  - The record is lost, and the empty one it leaves is the J2 state: `stop` exits 1 with `unreadable or corrupt`, and every `claim` pays the settle.
- Scenario, `create_record` (probe P3): an empty record is left for good.
- The round's "inside the put-back" tests patch `_link_new` as a whole, so the interrupt never lands inside it.
- Fix: `except BaseException:` on the cleanup, which removes `dst` and re-raises anyway.
  - Verified on scratch with K1's fix: P2 leaves WINNER in place, P3 leaves no record and no tmp, and the same 126 pass.
  - Test: `no_links` plus an `os.write` that raises `KeyboardInterrupt` once, run for both callers.
- Fixed: the fallback cleanup is `except BaseException`, so an interrupted write removes the empty `dst` and re-raises; the `finally`'s retry then finds no record and puts the only copy back. Tests (POSIX, `no_links` plus a write interrupted once): `create_record` leaves nothing, and the put-back keeps WINNER with no aside.

### K3 LOW: the `state != "kept"` guard is untested
- Where: `pidfile.py:285`. Mutant Z1 drops the guard and survives all five files.
- Scenario, with the guard gone: the `finally` retries a put-back that failed. On a transient error the record comes back, while stderr has already said `it is kept as <aside>`.
- Fix: a test whose `_link_new` raises `PermissionError` once and then works. Assert the note, the kept aside, and no record at `path`.
- Fixed: `test_a_failed_put_back_is_not_retried_behind_its_note` (`_link_new` fails once, then works): the note, one call, no record, the aside kept. Z1 (V3) fails it.

### K4 LOW: the `windows.md` line for the J1 retry cannot fail
- Where: the last line added to `registry-triage/windows.md`.
- The 0.3 s holder starts before `mcu daemon stop`. The CLI start-up, `POST /shutdown` and the daemon's shutdown all come before `release`, so the handle is usually gone first. The record is then removed with or without the retry.
- Fix: replace it with an in-process check, which is deterministic on Windows because sharing modes apply to every handle, including the process's own. Write a record naming 999999, open it, and close it on `threading.Timer(0.3, f.close)`.
  - Expected: `pidfile.remove_record_if(p, 999999)` returns True after about 0.3 s.
  - Positive control: with a 2 s timer it returns False after about 0.9 s.
- Fixed: the `windows.md` line is now the timed in-process check (a 0.3 s timer gives True after about 0.3 s; with a 2 s timer, False after about 0.9 s and the record kept).

### K5 NIT: the retry policy is unpinned
- Mutants Z3 (no backoff), Z4 (default bound 3) and Z6 (`replace_atomic` ignoring `attempts`) all survive.
- The gap existed for `replace_atomic` before round 5. The same bound now also sets how long daemon exit and `daemon stop` can wait.
- Fix: one test on `dirs.retry_sharing`, as probe P5 does it: sleeps patched, 9 sleeps totalling 0.9 s, then the same `PermissionError`, and no sleep for `FileExistsError`.
  - `replace_atomic`'s `attempts` then serves only its own test, so drop it.
- Fixed: `test_the_sharing_retry_is_bounded_and_backs_off`: 9 sleeps of 0.02 n totalling 0.9 s, the same error re-raised, and no sleep for `FileExistsError`. The bound is a local constant; the `attempts` parameters of `retry_sharing` and `replace_atomic` are dropped, and its test patches the sleep instead.

### K6 NIT: the `retry_sharing` docstring is wrong about POSIX
- Where: `dirs.py:34`, "POSIX never raises it here, so the loop does not run there".
- EACCES and EPERM are `PermissionError`. Probe P4, a read-only data dir: `remove_record_if` returns False after 0.90 s. It used to return at once.
- The only cost is the delay. Fix the wording ("on POSIX only a permanent permission error raises it, and costs the 0.9 s").
  - Do not gate the loop on win32: that would change `replace_atomic`.
- Fixed: the retry stays on every platform, and the docstrings now say that on POSIX only a permanent EACCES/EPERM raises it, at a cost of 0.9 s. It is not gated to win32: `test_replace_atomic_rides_out_a_windows_sharing_violation` pins the loop on both platforms, so gating would leave it covered only by the Windows leg and would change `replace_atomic`.

### K7 NIT: the `lexists(aside)` check is redundant
- Mutant Z2 survives: `_link_new` on a missing aside raises `FileNotFoundError`, and the `suppress(OSError)` absorbs it.
- This still holds with K1's fix.
- Fix: delete the check, or keep it as a guard that only saves a syscall and say so.
- Fixed: `lexists` deleted. With the rename inside the `try`, a missing aside makes the put-back raise `FileNotFoundError`, which the `suppress` absorbs (comment says so).

### K8 NIT: docs, and the J7 test
- Class 7 residual and the `remove_record_if` docstring say "a failed put-back keeps the copy aside and says so". A put-back the `finally` retries after a Ctrl-C fails silently, since `suppress(OSError)` wraps it. Add "(silently after a Ctrl-C)".
- SPEC 4 gives only `which names pid N`. The code also prints `which names no readable pid`.
- `test_stop_keeps_the_record_of_a_live_pid_with_no_usable_status`: `victim.poll() is None` holds while the fixture's reaper thread sits in `wait()`, because `Popen.poll` returns None when it cannot take the waitpid lock.
  - Use `with pytest.raises(subprocess.TimeoutExpired): victim.wait(0.5)`.
- Fixed: class 7 and the docstring add "silently when it is the retry after a Ctrl-C"; SPEC 4 adds `names no readable pid`; the J7 test asserts `victim.wait(0.5)` times out (mutant V7, stop signalling the pid, fails it).

## Checked and sound
- `replace_atomic` has no behaviour change.
  - The loop moved verbatim, with the same bound, backoff and last exception re-raised.
  - Probe P5: 9 sleeps totalling 0.9 s, then the original `PermissionError`, and 0 sleeps for `FileExistsError`.
  - `os.replace` is looked up per call, so tests that patch `os.replace` still apply.
- The `finally` cannot double-restore.
  - A successful put-back consumes the aside, so `lexists` is False.
  - If `_link_new`'s own `remove(src)` fails, the retry meets the restored record (`FileExistsError`) and removes the aside.
  - The `"ours"` state only removes.
- The `finally` never restores after a successful delete. An interrupt before `state = "ours"` puts the record back, and the caller gets `KeyboardInterrupt`, not True.
- The `finally` never clobbers. Every restore goes through `_link_new`, which does not replace.
  - After `FileExistsError`, the second attempt succeeds only if the newer record has gone meanwhile. That is the same as a later put-back.
- No new aside or tmp leftovers: every path that leaves one did so before (a kill, a failed remove under a Windows scan). The random names mean a leftover is no longer reused, and leftovers only ever came from crashes.
- The fallback cleanup removes only a `dst` it created with `O_EXCL`. A claimer takes an empty record only after `CLAIM_SETTLE_S`, so a write would have to take 0.25 s to fail.
- Import weight: `import mcuscope.pidfile` loads `mcuscope`, `.dirs`, `.pidfile` and `.protocol`; tomlkit, typer and httpx do not load. `dirs`'s new imports are already loaded by `protocol`.
- `note_prefix`: the daemon's default is `mcuscoped: `, and all three CLI callers (`cli.py:2880`, `cli_daemonctl.py:226,247`) pass `warning: `. Both are asserted with full text.
- `windows.md`: the CI line and the held-open line are actionable (`stop` exits 0 and leaves the record; checked against `_stop_running_daemon`).
- `ruff check` is clean on the touched files. There are no U+2013 or U+2014 in the `+` lines of `git diff` over docs, host/mcuscope and host/tests.

## Revert table
Scratch copy `~/tt-data/mcuscope-2026-09-26/fixdiff6/host`, driven by `mutate6.py` (checks every anchor before writing, then restores and compares). Run with `PYTHONDONTWRITEBYTECODE=1 -p no:randomly -p no:cacheprovider` over test_pidfile, stop_scope, cli_daemonctl, config_api and daemon_process.
Baseline: 175 passed, 1 skipped. Round 5's Y1 to Y18 were not repeated.

| Mutant | Result | Caught by / finding |
|---|---|---|
| Z1 `finally` retries a failed put-back (no `kept` guard) | survives | K3 |
| Z2 `finally` without `lexists` | survives | K7 (redundant) |
| Z3 `retry_sharing` without backoff | survives | K5 |
| Z4 `retry_sharing` default bound 3 | survives | K5 |
| Z5 `retry_sharing` swallows the last `PermissionError` | caught | `test_replace_atomic_rides_out_a_windows_sharing_violation` |
| Z6 `replace_atomic` ignores `attempts` | survives | K5 |
| Z7 fallback keeps the empty `dst` | caught | `test_a_failed_write_without_hard_links_leaves_no_empty_record` |
| Z8 `"ours"` keeps the aside copy | caught | `test_remove_record_if_removes_only_the_pid_it_names` and 1 more |

Probes (`probe6.py`, run against the repo tree through the editable install, files under `probe-dirs/`):

| Probe | Result |
|---|---|
| P1 interrupt after the aside rename | record absent, `.aside` left (K1) |
| P2 interrupt in the fallback put-back | record empty (size 0), aside deleted (K2) |
| P3 interrupt in `create_record`'s fallback | empty record left (K2) |
| P4 read-only data dir | False after 0.90 s (K6) |
| P5 `retry_sharing` policy | 9 sleeps, 0.9 s, same exception; `FileExistsError` 0 sleeps |

With K1 and K2's fixes applied to the scratch copy, P1 to P3 come out correct. The copy was then restored and compared with `cmp`.

## Not covered
- Windows: the sharing retry on a real handle, `os.rename`, and K4's replacement check.
- Real SIGINT delivery: every interrupt was simulated by raising `KeyboardInterrupt` from a patched call. P1's reachability is reasoned from CPython 3.12+'s check after a builtin call, not observed.
- A real filesystem without hard links (the fallback ran only with `os.link` patched).
- The whole suite, `test_cli.py`, and the JS suite.

## Scratch (not deleted)
- `~/tt-data/mcuscope-2026-09-26/fixdiff6/`:
  - `host/` and `tools/`: the tree copy, restored to match the repo;
  - `mutate6.py` and `mutants6.log`;
  - `probe6.py`;
  - `pidfile.py.orig`: the pre-fix copy used for the restore;
  - `probe-dirs/`: the `p6-*` dirs holding the probes' records.
