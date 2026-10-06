# fix3

## FD-CORE2-1 `O_BINARY` in `write_new_file`
- `config.py` `write_new_file`: flags now include `getattr(os, "O_BINARY", 0)`.
- Test: `test_config_write_binary.py` (sentinel `os.O_BINARY` bit, asserts it reaches `os.open`).
- Revert-verified: flag removed, 1 failed.
- Sweep of other `os.open` with `O_WRONLY`/`O_CREAT` in `host/mcuscope`:
  - Carry `O_BINARY`: `pidfile.py:181`, `pidfile.py:331`, `lockfile.py:164` (`O_RDWR`), `dirs.py:55` (`private_opener`, flags from `io.open`).
  - No flag, nothing written through the fd, so none needed: `store.py:1031` (creates an empty file, closed at once), `cli_output.py:219` and `_stdio.py:406` (`os.devnull` for `dup2`).
  - `update_check.py:218` writes through `write_new_file`, so it is fixed by the same change.

## FD-CORE2-2 `_move_wal_aside`
- `store.py` `_move_wal_aside`: `FileNotFoundError` or a differing identity still renames; any other `OSError` from the main-path stat returns False, so `capture_error` carries the move-aside text.
- Test: `test_store_capture_wal_aside.py::test_an_unreadable_stat_of_the_capture_renames_nothing` (EIO on the main path, based on `probe_aside2.py`).
- Revert-verified: `return False` changed to `pass`, 1 failed.

## FD-CORE2-3 exact mode on replace
- `config.py` `write_new_file`: `os.fchmod(fd, mode)` on the new temp when `like` existed (POSIX only).
- Tests: `test_config_private_files.py` `test_a_replace_keeps_the_exact_mode_whatever_the_umask` (022/0664, 077/0644, 022/0666) and a new file under umask 0 stays 0600.
- Revert-verified: fchmod disabled, 3 failed.

## FD-CORE2-4 status wording and guide
- `cli.py` status prints "then restart the daemon" when the cause contains "aside before restarting"; `AI_GUIDE` gained the move-aside step and the `.stale-<stamp>` files.
- SPEC 4 `mcu status` row notes the "then restart" variant.
- Test: `test_cli_status_capture_health.py::test_a_move_aside_cause_says_then_restart`.
- Revert-verified: condition forced False, 1 failed.

## FD-CORE2-5 lock identity
- Chose: no override. The error now says this filesystem cannot be captured on and that `--ignore-capture-lock` does not apply, and the lockfile docstring says the override does not cover unstable file ids.
- Reason: `verify()` would stop capture on the first tick anyway, and SPEC 3.2 (line 478) scopes the override to a filesystem without locking.
- Existing test `test_lockfile_identity.py` (matches "the locked file is not the one at") still passes. Message text itself not separately pinned.

## FD2-OUTER-1 reserved device names
- `cli_output.py`: `is_reserved_name` (base name before the first dot, trailing spaces stripped, against NUL/CON/PRN/AUX/COM1-9/LPT1-9, `/` or `\` separators); `AtomicOut` treats it as a non-regular target on `os.name == "nt"` only, written in place with no warning.
- I used the name set on every Python version rather than `os.path.isreserved`, so one path is tested everywhere.
- Test: `test_cli_output_reserved_names.py` (helper on any OS; AtomicOut with `os.name` patched to "nt" writes in place, no temp, no warning).
- Revert-verified: `device or` removed, 1 failed.

## Existing tests edited
None.

## Verification
- `pytest -p no:randomly` over `test_cli_status_capture_health`, `test_store_capture_wal_aside`, `test_config_private_files`, `test_config_write_binary`, `test_cli_output_reserved_names`, `test_cli_contract`, `test_lockfile_identity`: 111 passed. Earlier run adding `test_cli_output_atomic_export` and `test_config_api`: 163 passed (before a helper tweak; the final set above reran).
- `ruff check .` clean.
- Mutation copies restored and `cmp`-verified.

## Changelog
- Windows: config saves no longer write CRLF.
- A config replace keeps the file's exact mode under any umask.
- A capture whose stat is unreadable no longer has its WAL moved aside.
- `mcu status` says "then restart" when the WAL must be moved first; the guide documents it and the `.stale-*` files.
- `mcu ... -o NUL` (and other device names) on Windows no longer warns of a partial export.

## Not done
- `AtomicOut` reserved-name branch and CRLF fix are not run on Windows.

## Doubts
- Windows `NUL` stat behaviour and the CRT newline translation are reasoned, not driven.
- `is_reserved_name` does not handle `\\.\` device-namespace paths or `NUL:`.
