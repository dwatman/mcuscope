# Fix batch: daemon

## RES-10 + MODULES-1
- `lockfile.py`: `CaptureLock.path = realpath(db_path) + ".lock"`; `acquire` reopens and retries when `fstat(fd)` and `stat(path)` differ in `(st_dev, st_ino)` after `flock`; new `verify()` raises new `LockLost` (RuntimeError) when the file was removed or replaced, no-op when not held (e.g. `--ignore-capture-lock`). Docstring says hard links are uncovered.
- `daemon.py`: sets `app.state.capture_lock = lock` after `create_app` (see Not done).
- Tests: `host/tests/test_lockfile_identity.py` (symlink spelling refused, swapped-file retry, verify on unlink and on replace).
- Revert-verify: reverting the `_is_current` check, the `realpath`, and the `verify` body each fails a test.

## MODULES-2
- `daemon.py`: no `CaptureLock` for `:memory:`. `resolve_db_path` never returns `""` (it maps to the default path), so only `:memory:` is tested.
- Test: `test_a_memory_capture_takes_no_lock`; fails when the guard is reverted.

## SEC-6 (POSIX, at creation)
- `dirs.py`: new `make_private_dirs(path)` creates each missing directory 0700 (makedirs mode= would hit only the leaf); existing dirs untouched. Used by `pidfile.py` (`pid_file_path`), `_stdio.py` (`_write_report`), `lockfile.py`. Lock file created 0600, existing file never chmod'd.
- Tests: `test_lock_file_is_0600_and_new_dirs_0700_but_existing_modes_are_kept`, `test_pid_file_path_creates_the_data_dir_0700`. Revert of lock mode and of dir mode each fails.
- Store batch: use `dirs.make_private_dirs` for the `db_path` parent (`store.py:747`) instead of `os.makedirs`.

## Existing tests edited
None. `tests/test_capture_lock.py` still passes (28 passed with the new file).

## SPEC edits
None.

## Changelog
- The capture lock follows symlinks and relative spellings (one lock per real file) and is re-checked while running.
- Data dir, crash/startup log dir and lock file are created owner-only on POSIX.

## Not done
- `server.py` lifespan, after `Store(...)`: `lock = getattr(app.state, "capture_lock", None)`; `if lock is not None: store.add_tick_check(lock.verify)`. The store is built inside the server lifespan, so the daemon cannot register it. `verify()` raises `LockLost`; the store's tick check should treat any exception as the failure (set `capture_error`).
- `store.py` db parent: see SEC-6.

## Doubts
- The tick-check contract (raise on failure) is my assumption; the store batch may expect a bool.
- Windows: `os.stat` vs `os.fstat` inode compare is untested there.
- Ruff clean; only `test_lockfile_identity.py` and `test_capture_lock.py` were run.
