# Fix-diff review 5: round 4 of the start race (uncommitted tree on 89a6af3)

HEAD 89a6af3ec4b19ca798ab384a5f2005936d88ddf2. Target: the "Round 4" section of `windows-fixbatch-race.md` (`remove_record_if`, `create_record`, every caller, the `test_cli_ux` fix, docs).
The mechanism is sound. No ordering of two processes found loses, duplicates or misnames a record. What is left: two failure paths the round added, a lost Windows retry, and an incomplete residual.

## Findings

### J1 MEDIUM: the new Windows renames skip `replace_atomic`'s sharing-violation retry (class 13)
- Where: `pidfile.py:253` (`os.replace(path, aside)`) and `pidfile.py:295` (`os.rename`, used by the put-back and by `create_record`).
- Class 13's invariant says replace/rename goes through `replace_atomic`, and its sweep now flags both lines. No exemption is recorded.
- Scenario, a regression: the CLI's first write used `_replace_pid_record`, which retries WinError 5/32 for 0.9 s. It now calls `create_record`, which makes one `os.rename(tmp, path)`.
  - If an on-access scan holds the just-closed `.tmp`, the start prints `warning: could not write the pid file`. The winner's rewrite (still `replace_atomic`) repairs the record, so what is left is a spurious warning, plus a `.tmp` if the `finally` remove also meets the scan.
- Scenario, new path: the same transient handle on a put-back loses the record it exists to save. Stderr shows `could not put back`, and the record stays aside.
- The aside itself fails exactly where `os.remove` failed before, so it is no regression. A retry would still stop a concurrent reader's handle (every `read_pid_record` opens without `FILE_SHARE_DELETE`) from leaving a record behind.
- Fix:
  - Retry `PermissionError` only (never `FileExistsError`) around the three Windows calls, with `replace_atomic`'s bound.
  - Add one line to class 13: a non-replacing rename cannot use `replace_atomic` and has its own retry.
- Fixed: `dirs.retry_sharing` now holds `replace_atomic`'s loop (which calls it) and retries `PermissionError` only. `remove_record_if`'s aside and the Windows put-back (`os.rename`, also `create_record`'s) go through it. Class 13 has the line, and its invariant and sweep name the helper.

### J2 LOW: without hard links, a failed write leaves an empty record for good
- Where: the `_link_new` fallback, `pidfile.py:301-308`, and the `create_record` docstring ("Never leaves an empty or partial record").
- Scenario: a data dir with no hard links (vfat, some network mounts) meets a full disk. `os.open(O_EXCL)` succeeds, then `os.write` raises.
  - `dst` stays empty. The loser's reap cannot remove it, because it names no pid.
  - Every later `claim` then pays `CLAIM_SETTLE_S`, and `daemon stop` with no daemon exits 1 with `unreadable or corrupt`. That empty record is the state `claim` removes itself from on the same failure (`pidfile.py:220-227`).
- Verified with `probe_fallback_enospc.py` (os.link raises EPERM, os.write raises ENOSPC): `create_record` raised, and the record was left at 0 bytes, reading as None.
- Fix: in the fallback, close and then `os.remove(dst)` on a failed write, as `claim` does. Word the docstring "briefly empty without hard links". Test with the probe's two patches.
- Fixed: the fallback closes, then removes `dst` on a failed write and re-raises; the docstring says "empty only briefly on a filesystem without hard links". Test: `test_a_failed_write_without_hard_links_leaves_no_empty_record` (EPERM link, ENOSPC write).

### J3 LOW: the fallback's no-replace rule is untested
- Mutant X1 (`O_EXCL` changed to `O_TRUNC` in the fallback) survives all six files. `test_the_put_back_works_without_hard_links` has no newer record at the destination.
- Scenario: on a filesystem with no hard links, a put-back or a first write replaces a newer record, which reopens the straddle the round closed.
- Fix: parametrize `test_a_newer_record_beats_the_put_back` and `test_create_record_never_replaces` over hard links and no hard links (the existing `no_links` patch).
- Fixed: `test_neither_path_replaces_a_newer_record` runs with and without hard links, for both the put-back and `create_record`; X1 (Y6) fails its no-link leg.

### J4 LOW: the stated residual misses three outcomes
- Where: class 7 (`REVIEW.md:178`) and the `remove_record_if` docstring (`pidfile.py:246-247`).
- Outcome 1: a remover that runs while the record is aside finds none and skips, so the put-back restores a record its owner meant to delete.
  - Example: start A's stale removal puts aside the record B just created, and meanwhile B's reap of its dead child runs.
  - The result is a stale record, which every reader tolerates. If the pid is recycled, the next `claim` goes unrecorded until a start's rewrite.
- Outcome 2: a failed put-back loses the record. It is announced (`could not put back`), and the file is kept aside.
- Outcome 3: a signal or Ctrl-C between the aside and the put-back loses the record without any message and leaves `.aside` behind.
  - The daemon's SIGTERM handler calls `release` again, and that call finds no record.
- Fix: name all three in the class 7 residual and in the docstring, one clause each.
- Fixed: closed the cheap one. A `finally` puts the record back when Ctrl-C lands between the aside and the put-back, or dropping the copy if a newer record stands (tests: before judging, inside the put-back, inside with a newer record). The class 7 residual and the docstring now list what remains: a reader finds none; a record created then stands; a remover that runs then skips, so a stale record comes back; a failed put-back is kept aside and said; a process killed outright loses it.

### J5 NIT: aside and tmp names are unique per process, not per call
- Where: `pidfile.py:251` and `:276`.
- Scenario: a crash between `os.link` and `os.remove(src)` (put-back or `create_record`) leaves the leftover name hard-linked to the live record. A later process with the recycled pid then goes wrong in two ways:
  - Its `os.replace(path, aside)` is a no-op, because rename(2) between two links to one inode reports success. Verified in `fs/`. `remove_record_if` then deletes the leftover, returns True, and the record is still there. Through `release`, the daemon leaves its own record behind.
  - Its `open(tmp, "w")` truncates the live record through the leftover link.
- Fix: add a per-call random suffix (`secrets.token_hex(4)`) to both names.
- Fixed: `secrets.token_hex(4)` in both the aside and the tmp names; `test_aside_and_tmp_names_differ_per_call`.

### J6 NIT: SPEC and message wording
- `SPEC.md:1226`: "a start's first write never replaces a record" is wrong for a stale one, which is removed and then written.
  - Suggest: "never replaces a live record, and takes a stale one only while it still names the dead pid".
- The put-back failure message (`could not put back <pid file>, which names pid N ...; it is kept as <aside>`) appears in neither SPEC 4 nor ARCHITECTURE.
- Its `mcuscope:` prefix is wrong when the daemon prints it (the daemon's notes say `mcuscoped:`).
- It prints `names pid None` when the aside copy is unreadable.
- Fixed: SPEC now says "never replaces a live record, and takes a stale one only while it still names the dead pid". SPEC 4 and ARCHITECTURE carry the put-back failure message. It is prefixed `mcuscoped: ` by the daemon and `warning: ` by `mcu` (a `note_prefix` parameter), and an unreadable copy says "names no readable pid".

### J7 LOW, pre-existing: `stop`'s "pid still running, no /status" branch has no test
- Where: `cli.py:2874-2879`.
- Mutant X23 (the branch disabled, so the record of a live, still-starting daemon is removed) survives the six files. `grep -rn "usable" host/tests` finds no test for `no usable /status`.
- Fix: in `test_cli_daemon_stop_scope.py`, a record naming `victim.pid` and `_status_body` returning None. Assert exit 1, the message, and that the record is kept.
- Fixed: `test_stop_keeps_the_record_of_a_live_pid_with_no_usable_status` (the message, exit 1, the record kept, nothing signalled); X23 (Y17) fails it.

## Checked and sound
- Two starts, winner rewrite, loser reap, in every order: reap before or after the rewrite, a straddling reap (put back), and a straddling first write (`create_record` refuses). The record ends naming the winner. No duplicate is possible, because every put-back is non-replacing.
- `daemon stop`'s tidy and the daemon's own `release` for the same pid: whichever puts the record aside first removes it, and the other's rename gets ENOENT and returns False.
- `restart`: the new start's create or the new daemon's claim can land inside the old daemon's release. It is put back, or it wins through `O_EXCL`, and the content is the same.
- Readers while a record is aside:
  - With content judged removable, the record is being deleted anyway, so a reader missing it only sees the result early. The one new case is the straddle (microseconds).
  - `daemon stop` then takes the no-record branch. `claim` creates its own record, which beats the put-back: the same pid on POSIX, the worker instead of the launcher on Windows, and `stop` accepts either.
  - `mcu status` and `daemon status` do not read the record. `_report_key` gets the unsuffixed key.
- Windows:
  - `os.rename` onto an existing file raises `FileExistsError` (WinError 183, EEXIST). `os.replace` works on FAT and exFAT.
  - A claimer's open fd blocks the aside rename, which is safer than POSIX. The daemon closes its record in `claim`, so it never holds it open at `release`.
- The daemon's release never removes another's record: the aside copy is deleted only when it names `os.getpid()`.
- The AI guide was right to stay unchanged. No option or exit code changed: `stop`'s new stale message replaces an exit-1 message on the same path. The guide's "names this start, even after a race" matches the code, and `test_cli_contract.py` checks options only.
- `test_cli_ux` fix: with a 16 s pause after the first read, the old tests fail 2 of 2 and the new ones pass (`test_cli_ux_pause_{old,new}.py` in scratch).
- `ruff check .` is clean. No U+2013 or U+2014 in the `+` lines of `git diff`.

## Revert table
Scratch copy `~/tt-data/mcuscope-2026-09-26/fixdiff5/host`, run by `mutate5.py`, which checks every anchor before any write and restores and compares after each mutant.
Run with `PYTHONDONTWRITEBYTECODE=1 -p no:randomly -p no:cacheprovider` over test_pidfile, stop_scope, cli_daemonctl, cli_daemon_start_pid, daemon_process and status_ppid_serial. Baseline: 130 passed, 1 skipped. Log: `mutants5.log`.
Round 4's own W1 to W22 were not repeated.

| Mutant | Result | Caught by / finding |
|---|---|---|
| X1 fallback create truncates | survives | J3 |
| X2 `remove_record_if` refuses pid None | caught | `test_a_record_that_is_not_utf8_is_malformed_and_claim_replaces_it` |
| X3 aside name without the pid | caught | `test_a_put_back_that_fails_...` (the name only; uniqueness is not tested, J5) |
| X4 failed put-back deletes the aside copy | caught | `test_a_put_back_that_fails_...` |
| X6 `_write_pid_record` ignores `create_record`'s False | caught | `test_a_losing_start_write_straddling_...` |
| X12 aside deleted whenever readable | caught | 7 tests, including every straddle |
| X18 fallback leaves its source | caught | `test_the_put_back_works_without_hard_links` |
| X22 failed put-back returns True | caught | `test_a_put_back_that_fails_...` |
| X23 `stop` drops a live pid's record (pre-existing) | survives | J7 |

The `_link_new` Windows branch (`os.rename`) cannot be mutated usefully on Linux, since POSIX rename replaces anyway. Only the Windows CI leg runs it, through `test_create_record_never_replaces`, `test_a_newer_record_beats_the_put_back` and the straddle tests.

## Windows: missing from `windows.md` (nothing added)
- A CI line saying that `test_pidfile.py`'s put-back and `create_record` tests ran on `windows-latest`, not skipped: that is the only run of the `os.rename` branch.
- A record held open by another handle (a reader, or a scanner): `release` and `remove_record_if` return False and leave the record. A put-back onto a delete-pending name gives `PermissionError`, not `FileExistsError`.
- `create_record` under an on-access scan (J1): a spurious `could not write the pid file`.
- A data dir on FAT or exFAT (a USB stick): the aside, the put-back and the first write.
- The live LAN race through the venv launcher, with no `.aside` or `.tmp` left in the data dir.
- Fixed: `registry-triage/windows.md` has the CI line for `test_pidfile.py`'s `os.rename` put-back and `create_record` tests, a held-open record, and the J1 retry. FAT and the live LAN race through the launcher were not added, per the brief's list.

## Not covered
- Windows, all of it: the claims above about WinError mappings and sharing modes are reasoned from CPython and Win32 semantics, not run.
- A real filesystem without hard links: the fallback was driven only by patching `os.link`.
- A live multi-process race: not re-run. `test_cli.py`, the whole suite and the JS suite were not run.
- Repo tree, each file alone: test_pidfile (31), stop_scope (53 passed, 1 skipped), cli_daemonctl (30), daemon_process (9), cli_ux (27), all passing.

## Scratch (not deleted)
- `~/tt-data/mcuscope-2026-09-26/fixdiff5/`:
  - `host/` and `tools/`: the tree copy. `host/tests/` also holds the added `test_cli_ux_pause_new.py` and `test_cli_ux_pause_old.py`.
  - `mutate5.py`, `mutants5.log`, `probe_fallback_enospc.py`, `test_cli_ux.py.orig`;
  - `fs/` (the rename probe) and `tmppaleir19/` (the probe's empty record).
