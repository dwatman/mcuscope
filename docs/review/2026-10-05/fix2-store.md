# Fix batch: store2 (fixdiff-store findings)

Revert-verification: `~/tt-data/mcuscope-2026-10-05/fix2-store/mutate.py` checks every anchor first, then applies each mutation to the live `store.py`, runs the named test file, and restores it. `mutate.txt` holds the final run: 24 of 24 CAUGHT, file restored (`cmp` against `store.py.mine`).
Broader run (`run1.txt`): all 30 `test_store_*` files plus `test_assert`, `test_server_scope`, `test_plot`, `test_server_exports`, `test_server_export_job`, `test_server_live_verdicts`, `test_server_capture_failed`, `test_config_example` and `test_config_loader`: 420 passed, 1 skipped (the Windows-only case). `test_cli_contract` and `test_sessions`: 102 passed. ruff is clean on my files.

## FD-STORE-1: emptying the WAL with a read open
- `store.py:874` `_fail_capture`: the TRUNCATE now runs in `_empty_wal` (`:915`), up to `_WAL_EMPTY_TRIES` (`:222`, 20 tries 50 ms apart, 5 ms busy each). It awaits between tries, so it never blocks the loop.
  - If the TRUNCATE is still refused, `_move_wal_aside` (`:930`) renames this capture's `-wal` and `-shm` to `<name>.stale-<YYYYmmdd-HHMMSS>`.
  - It renames only when the main file at the path is no longer ours (replaced or deleted), and only side files whose `(st_dev, st_ino)` match the ones recorded at start (`:1041`).
    - When a failed tick check leaves the file intact (lock lost), the `-wal` holds that file's own commits, so it stays.
    - A `-wal` that a restore brought with its file is foreign, so it stays too.
  - If it cannot rename them (rename refused, or no identity recorded while a side file exists), `capture_error` and the `_FailedConn` reason end with `; its WAL could not be emptied: move <db>-wal and <db>-shm aside before restarting, or the restart replays it into the file now at that path`.
  - All of this sits in a `finally`, so a `stop()` that cancels the tick during the wait still moves the files aside and swaps in the stand-in.
- Tests: `tests/test_store_capture_wal_aside.py` (POSIX only), 10 cases:
  - an outside reader open at the replace, with the reader closed or still open at the "restart" (300 of 300 rows, integrity ok)
  - a reader that ends within the wait, so the WAL is emptied in place and nothing goes stale
  - **the plot summary rebuild** holding its `BEGIN` snapshot
  - **a session export** holding its read on the ATTACHed live file
  - the rename refused, and the identity unknown: both announced in `capture_error` and the stand-in
  - a foreign `-wal` left in place
  - a lock-lost failure on an intact file keeping its WAL (600 rows)
  - a stop during the wait
- The two in-daemon reader tests show those readers do refuse the TRUNCATE, which the reviewer suspected but had not driven: both assert that the aside files exist.
- Revert-verify: caught for each of rename, retry, finally, intact-file guard, foreign-side check, unknown identity, rename refused, announcement and stand-in reason.
- A `_db_identity is None` guard I first wrote was redundant (with no identity, `_file_identity()` is None as well, so the next check returns), and I deleted it.

## FD-STORE-2: Windows sharing mode
- `tests/test_store_capture_identity.py`: a `posix_only` marker on the 4 parametrized replace cases, the delete test and the every-opener test. The reason given: "Windows opens the capture without FILE_SHARE_DELETE, so replacing or deleting it while open is refused and the hazard cannot arise".
- New `test_windows_refuses_to_replace_or_delete_an_open_capture` (runs only on `nt`): asserts `PermissionError` for both, and that the capture stays healthy afterwards. **Not run** (no Windows here).
- The new `test_store_capture_wal_aside.py` is skipped on Windows at module level, with the same reason.

## FD-STORE-3: frozen `last_ms` ceiling (store half)
- The store half is done. `ceil_ts` is accepted next to `floor_ts` by `_window_terms` and by every reader that takes `floor_ts`: `query_lines`, `count_lines`, `query_can_frames`, `_export_where`, `export_sids`, `first_export_line_id` and `iter_plot_export`. Given with `floor_ts`, it replaces the anchor re-derived from `id_to` (`store.py:2367`).
- Test: `tests/test_store_window_ceiling.py::test_a_frozen_window_keeps_the_ceiling_its_floor_was_measured_from`, parametrized over all 6 public readers.
  - Its control: the freeze alone lets the pre-step rows in.
  - With `ceil_ts`, nothing gets in.
- Revert-verify: the anchor line and each of the 7 forwards were mutated to `None` one at a time, and all were caught.
- **The server half is not done** (see Not done). Until it lands, the verdict and export paths keep the bug, and the SPEC 3.4 sentence the reviewer quotes still overclaims for them.

## FD-STORE-4: an evicted plot name that returns
- `_keep_recent_adhoc` (`store.py:171`) returns the keys it drops, and the store accumulates them in `_plot_evicted` (`:817`), both live (`:2895`) and from the rebuild (`:3024`).
- When a dropped key comes back, `_note_plot` sets `_plot_dirty` (`:2892`), so the next read rebuilds with the true count.
  - Until that read, deletes only mark the summary dirty, so the `_forget_plot_points` underflow cannot happen.
- Test: `tests/test_store_plot_summary_cap.py::test_a_dropped_name_that_returns_counts_its_older_points[live|restart]`: count 3, and the summary equals a forced rebuild.
- Revert-verify: the dirty flag, the live accumulation and the rebuild accumulation were each caught.
- `ponytail:` comment: the set grows with the ad-hoc names a capture has ever held; the rebuild's own scan holds as many at once.

## FD-STORE-5: the RES-1 rollback, tested
- No code change.
- Test: `tests/test_store_write_lock.py::test_a_refused_sweep_chunk_does_not_pin_the_writer_once_the_lock_moves_on`.
  - It calls `_delete_lines` during the lock and asserts the writer is left `in_transaction` (positive control).
  - Then the other process commits a write, and the held line must land within 5 s.
- Revert-verify: replacing the rollback with `pass` fails it (a 5 s timeout).

## FD-STORE-6: "newest N sessions" wording (partly done)
- `config.py:99` (the `auto_session` comment) and SPEC 3.4 "Automatic sessions" (`SPEC.md:978`) now say "the newest N ended sessions" / "finished runs".
- The rest is in files I do not own (see Not done). The prompt named `webui/settings.js` for the hint, but the hint text lives in `webui/index.html:240`, and settings.js has no hint text, so I left both alone.

## FD-STORE-7: what the floor protects
- SPEC 3.2 item 5 (`SPEC.md:456`) now reads: the floor keeps everything from the oldest protected session's first line to the newest ended one's last, lines captured between sessions included; lines before and after that span are not protected.
- No code change: the span behaviour is kept, as the reviewer's first option.

## NITs
- FD-STORE-8: `store.py:971` now uses `type(exc) is not sqlite3.DatabaseError`, so Integrity, Programming and other errors pass through unchanged.
  - Test: `test_store_start_checks.py::test_a_code_fault_at_start_is_not_blamed_on_the_file`. Caught.
- FD-STORE-9: `_check_path` (`store.py:849`) wraps any other `OSError` as `capture <path> could not be checked (<err>); capture stopped`. It still stops on the first tick: it cannot prove the file is ours.
  - Test: `test_store_capture_identity.py::test_a_capture_that_cannot_be_checked_stops_capture_with_the_cause`, with `os.stat` patched to EACCES. Caught.
- FD-STORE-10:
  - The `0 <=` guard is deleted (`store.py:3797`). `test_store_wal_pinned.py` passes.
  - `test_a_new_capture_and_its_new_dirs_are_owner_only` now sets `os.umask(0o022)` around `start()`. Removing the 0600 create now fails it under this machine's umask.

## Existing tests edited
- `test_store_capture_identity.py`: the `posix_only` marker on three tests (FD-STORE-2), plus two new tests.
- `test_store_start_checks.py::test_a_new_capture_and_its_new_dirs_are_owner_only`: the umask is pinned to 022 around `start()` (FD-STORE-10).
- `test_store_write_lock.py`, `test_store_plot_summary_cap.py`, `test_store_window_ceiling.py`: new tests appended, and `import pytest` added. No existing assertion changed.

## SPEC edits
- 3.2 item 5: what the floor protects, including lines between sessions (FD-STORE-7).
- 3.2 item 6: the WAL is emptied with up to about 1 s of waiting for reads; otherwise it is renamed `.stale-<stamp>`, or `capture_error` names the files to move aside (FD-STORE-1).
- 3.4 "Automatic sessions": "the newest N ended sessions ... finished daemon runs" (FD-STORE-6).

## Guide wording (for the cli batch)
- `capture_error`: when it ends `move <path>-wal and <path>-shm aside before restarting`, do that before `mcu daemon restart`; otherwise a restart is safe.
- Files named `<capture>-wal.stale-<stamp>` / `-shm.stale-<stamp>` beside the capture are a replaced capture's leftovers, safe to delete.
- `mcu status` currently prints `CAPTURE STOPPED: <capture_error>; restart the daemon (mcu daemon restart)` (`cli.py:216`). With the new suffix, the line reads "...move X aside before restarting; restart the daemon", which is acceptable. The cli batch may want "then restart" when the reason contains "aside".

## Changelog
- A capture replaced or deleted while a read is open (another program, a plot rebuild or a session export) no longer corrupts the file put at its path.
  - The daemon waits up to about a second for the read to end, then moves its own `-wal`/`-shm` aside.
  - If it cannot move them, `capture_error` says to move them before restarting.
- A plot channel that drops out of the 256-name summary and later returns reports its full point count.
- A capture whose path cannot be checked (permission, I/O error) stops capture with a message naming the cause.

## Not done
- **FD-STORE-3, server half** (`server.py` `_resolve_window`, ~3608): take `anchor = store._window_anchor(bound)` once, before the until-fold and the freeze. Set `floor_ts = anchor - last_ms / 1000` and add `"ceil_ts": anchor if last_ms is not None else None` to `scope`.
  - The scope is spread into every store reader, all of which now accept `ceil_ts`.
  - The retrospective `/assert` (`server.py` ~3430, which unpacks `win.scope` by key into its own scope dict at ~3436) must forward `ceil_ts` too: add it to the `(win.scope[k] for k in ...)` tuple and to that dict.
  - Test to add with it: the reviewer's `probe_ceiling.py` through `/assert` and an export (5 rows stamped now+3600, nothing after, `last_ms=60000`, expect 0).
- **FD-STORE-6, outside my files:** say "ended" and that the running session ages out.
  - `docs/SPEC.md:535` (3.3 config example): `min_sessions = 5        # newest N ended sessions never expire by age; the running one does (0 = age only)`.
  - `README.md:303`: the same comment.
  - `host/contrib/config.example.toml:22`: "The newest N ended sessions are never expired by age...; the running session ages out like any other".
  - `host/mcuscope/webui/index.html:240` hint: `never deleted by age once ended`.
  - Question: should the webui batch own the `index.html` hint (recommended), or was settings.js meant to carry it?
- **FD-STORE-2 Windows test:** written, not run. The Windows leg should run it.

## Doubts
- **Least sure:** that renaming `-shm` is safe while another program still holds the old one mapped.
  - The outside reader keeps working on the renamed inodes (POSIX), and a new opener gets fresh files.
  - I did not test an outside *writer* (it cannot be one, since capture held the write side), nor NFS/SMB rename semantics.
- **The post-swap race:** a read connection opened by path between `_check_path` and `connect` would pair the restored file with the old WAL. If its close then happens after a rename (rather than after a successful TRUNCATE), it could checkpoint the renamed WAL into the restored file.
  - The window is microseconds; not driven.
  - `SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE` on read connections (Python 3.12+) would close it; the project still supports 3.10.
- **A refused sweep chunk** (`_delete_lines`) still leaves the writer in a transaction until the next insert rolls it back. With no traffic, the writer then sits on a stale read snapshot, which could make RES-9's pinned-WAL notice blame "another process".
  - Not driven. A `rollback()` on failure inside `_delete_lines` would fix it at the root, and the insert-side rollback stays as the catch-all.
- **Not thought to check:** a capture on a filesystem with `st_ino == 0`. There the replace is undetectable anyway, and the aside path announces rather than renames.

## Scratch
`~/tt-data/mcuscope-2026-10-05/fix2-store/`:

- `mutate.py`, worth keeping in `mcuscope-tools`.
- `mutate.txt` and `run1.txt`.
- Pre-edit copies: `store.py`, `config.py`, `SPEC.md`, `settings.js` (unchanged), and the five store test files.
- `store.py.mine` (post-edit), `defs.txt`.

No daemon was started; no process left running.
