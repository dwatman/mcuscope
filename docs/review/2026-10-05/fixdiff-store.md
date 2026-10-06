# Fix-diff review: store

Scope: `git diff 5eaaeeb -- host/mcuscope/store.py host/mcuscope/config.py`, the SPEC 3.2 and 3.4 store edits, the new and edited tests listed in `fix-store.md`.
Probes and copies: `~/tt-data/mcuscope-2026-10-05/fixdiff-store/` (1.2 GB, almost all of it `big.db`, an indexed copy of the perf capture; `probe/` holds throwaway DBs).
The tree was left as found: every mutation ran on `store.py`, then the file was restored from `store.py.orig` and checked with `cmp`.

Counts: 0 HIGH, 2 MEDIUM, 5 LOW, 3 NIT.

## Findings

### FD-STORE-1 MEDIUM CONFIRMED: RES-2 still corrupts the restored file when a read is open at the moment of failure
- `store.py:880` (`_fail_capture`): `PRAGMA wal_checkpoint(TRUNCATE)` runs on the writer, whose busy timeout is 5 ms.
  Any read open on the old file at that moment refuses it, and the only trace is a log line ("move capture.db-wal aside before the next start").
- `/status` `capture_error` and `mcu status` say "restart the daemon". A restart opens the path, which pairs the stale `-wal` with the restored file and replays it in.
  So in this case following the on-screen instruction is what causes the damage.
- Repro: `probe_res2_busy.py <dir> reader_closes_first|restart_while_reader_open`.
  - Store with 300 rows, then a snapshot copy, then 300 more rows.
  - A reader with `BEGIN; SELECT` open on the old file, then `os.replace(copy, db)` and `sweep_tick`.
  - Result in both variants: the TRUNCATE is refused and the WAL stays at 119512 bytes. The restored file ends with **600** rows instead of 300.
  - Here the copy is from the same lineage, so the integrity check passes. With an unrelated copy the batch's own probe got `malformed`.
- Reachable from inside the daemon, not only from other programs.
  - The plot summary rebuild holds `BEGIN` for seconds on a large capture.
  - A session export reads `src` for the whole copy.
  - Any `/lines` regex scan takes more than 5 ms. The web UI polls every second.
- SPEC 3.2 item 6 ("the WAL is emptied into the old file before every connection closes, so nothing is replayed") overclaims for this case.
- Suggested fix:
  - Keep the real handle after a refused TRUNCATE.
  - Retry the TRUNCATE off the loop with a longer busy timeout, on later ticks and in `stop()`.
  - If it still cannot run, rename the `-wal`/`-shm` now at the path aside. They belong to the old inode, which the identity check has already proved.
  - Failing that, put the instruction into `capture_error` itself so `/status` and `mcu status` carry it.
- Class: 12 (healthy-while-dead surface gives the wrong remedy), N1. This was the "reasoned, not driven" leftover in the batch's own Doubts.

### FD-STORE-2 MEDIUM SUSPECTED (Windows): six RES-2 tests replace or delete an open capture, which Windows refuses
- `tests/test_store_capture_identity.py:50`, `:78`, `:140`: `os.replace(copy, db)` and `os.remove(db)` while the store holds `db` open.
- SQLite's Windows VFS opens with `FILE_SHARE_READ | FILE_SHARE_WRITE` and no `FILE_SHARE_DELETE`, so both calls raise `PermissionError` (sharing violation).
  - The tests carry no `skipif`. CI runs the whole suite on `windows-latest` (`ci.yml:86`).
  - Expected failures: the 4 parametrized replace cases, the delete test and the every-opener test.
- Suggested fix: `skipif(os.name == "nt")`, with the reason that Windows refuses the replace outright, so the hazard cannot arise there.
  Better still, add one Windows case asserting that the replace raises `PermissionError` (the reason the feature is POSIX-only in practice).
- Class: 13, 14. Not run on Windows.

### FD-STORE-3 LOW CONFIRMED: a frozen `last_ms` window takes its floor from now and its ceiling from the newest row
- `server.py:3608` (`_resolve_window`) resolves `floor_ts` with no bound, which anchors it at now. It then freezes `id_to = max_id()`.
- `store.py:2292` (`_window_terms`) recomputes the anchor from that `id_to`, giving the newest row's `ts`, and caps the window there.
- When nothing has arrived since a backwards clock step, the newest row is itself pre-step.
  Its `ts` is ahead of now, so the ceiling admits every pre-step row above the floor.
  The same `last_ms` then answers differently on `/lines` (now-anchored, 0 rows) and on retrospective `/assert` and exports (frozen, 5 rows).
- Repro: `probe_ceiling.py`: 5 rows stamped now+3600 and nothing after. `query_lines(last_ms=60000)` returns 0 rows; the frozen form returns 5, and its count is 5.
- SPEC 3.4's new sentence ("'The last N ms' therefore never holds rows captured before a backwards clock step") is false for the verdict path.
- Suggested fix:
  - In `_resolve_window`, take `anchor = store._window_anchor(bound)` once, before the freeze.
  - Put both `floor_ts` and a `ceil_ts` in the scope, and have `_window_terms` use a given `ceil_ts` instead of re-deriving it.
  - That also makes "`now` fixed once per request" literal.
- Class: 44 (server-side twin), 77.

### FD-STORE-4 LOW CONFIRMED: after OP-2 evicts a name, its live count is wrong if it comes back, and a delete can drop it
- `store.py:2813` (`_note_plot`): an evicted name that reappears is re-added with `count 1`, although its older points are still stored.
- `_forget_plot_points` then subtracts the deleted old points from that small count, hits `<= 0`, and deletes the entry while newer points remain.
- Repro: `probe_op2.py`.
  - 256 names with 2 points each, then one new name (evicts `a0`), then one more `a0` point.
  - Live summary: `count` 1. Forced rebuild: `count` 3.
  - The batch's "a forced rebuild agrees" test misses this because no evicted name returns in it.
- Suggested fix: remember the keys evicted this run (at most 256 per port per run). When one of them comes back, set `_plot_dirty`.
- Class: 17 (reported value), 76.

### FD-STORE-5 LOW CONFIRMED: the rollback in `_insert_when_unlocked` is load-bearing and untested
- `store.py:1403`.
  - Mutation: replace the `rollback()` with `pass`. `test_store_write_lock.py` still passes (2 of 2).
  - It also passes the simple probe where the other process commits a write.
- It matters after a sweep chunk is refused by the same lock: that leaves the writer connection in a transaction (`in_transaction True`).
  Once the other process commits, every retry without the rollback is `SQLITE_BUSY_SNAPSHOT` for good.
- Repro: `probe_rollback.py <dir> sweep`.
  - Original: the held line lands.
  - Mutated: "NOT LANDED in 5 s", `db_locked_since` still set, and the writer had to be cancelled at stop.
- Suggested fix: a test that calls `_delete_lines` (or `sweep_tick` with a cap) during the lock, then has the other process commit a write, then asserts that the held line lands.

### FD-STORE-6 LOW CONFIRMED: four places still describe `min_sessions` as "the newest N sessions"
SOAK-1 changed the meaning to "the newest N *ended* sessions". These still say the old thing:

- `docs/SPEC.md:535` (the config example in SPEC): "newest N sessions never expire by age".
- `README.md:303`: same comment.
- `host/contrib/config.example.toml:22`: "The newest N sessions are never expired by age".
- `host/mcuscope/webui/index.html:240`: label "Keep newest sessions", hint "never deleted by age". This is the in-product help, and a daemon left running is not covered by it.

Fix: say "ended" in each, and mention that the running session ages out. Class: 58, 75.

### FD-STORE-7 LOW CONFIRMED: SPEC says lines outside sessions are unprotected; the span protects those between protected sessions
- SPEC 3.2 item 5 (edited this round): "lines captured while no session was running, and those after the newest ended session, are not protected".
- `retention_span` protects `[MIN(start_id), MAX(end_id)]`, so lines captured between two protected sessions survive.
- Repro: `probe_gap.py` with `auto_session` off, `min_sessions=5`, every row 30 days old.
  - "between A and B (no session running)" survives the sweep.
  - "before any session" and "after B" expire.
- This predates the round (the old `id >= floor` had the same effect), but the edit restated the sentence.
- Fix the SPEC wording ("lines before the oldest protected session, and after the newest ended one"), or decide that the code should change.

### FD-STORE-8 NIT SUSPECTED: RES-8 blames the file for any non-operational `DatabaseError`
- `store.py:907`: `IntegrityError`, `ProgrammingError`, `InterfaceError` and `DataError` all subclass `DatabaseError`. A code or schema bug at start would read "unreadable: move it aside".
- Corruption and not-a-database both raise plain `sqlite3.DatabaseError`, so `type(exc) is sqlite3.DatabaseError` is the exact test.
- No realistic trigger was found; not driven.

### FD-STORE-9 NIT SUSPECTED: a non-ENOENT stat error stops capture for good, with a bare errno as the reason
- `store.py:830`: `_check_path` catches only `FileNotFoundError`.
  - `EACCES` (a parent directory's mode changed) or `EIO` reaches `_run_tick_checks` and fails the capture.
  - `capture_error` then reads `[Errno 13] Permission denied: '...'`, with no "capture stopped".
- Suggest wrapping it as `capture <path> could not be checked (<err>); capture stopped`. Then decide whether a transient error deserves a second tick before stopping.

### FD-STORE-10 NIT CONFIRMED: two branches no test can fail
- `store.py:3710` `0 <= done`: when `done` is -1, `frames` is -1 too, so `done < frames` is already false. Delete the guard, or test a case that needs it.
- `test_a_new_capture_and_its_new_dirs_are_owner_only` passes without the 0600/0700 code under a 077 umask, because the umask already yields those modes. It can only fail with its fix reverted on a 022 runner.
  Setting `os.umask(0o022)` inside the test would make it hold for any runner.

## Checked and fine

- **RES-1, class 1.**
  - Each attempt blocks the loop at most 5 ms (`busy_timeout` pinned by the test). The backoff sleeps 10 ms to 0.5 s with no transaction open.
  - The cleared-lock notice is inserted inside the transaction the batch already holds, so it cannot hit busy and replay the batch row by row.
  - `stop()` during an episode cancels the writer after 5 s, and the held futures are failed and counted.
- **RES-2 openers.** Every by-path `sqlite3.connect`/`ATTACH` of the capture is behind `_check_path`: `_open_writer` (start), `_open_read_conn`, `_open_export_conn` and the session export's ATTACH. This was checked with a grep.
- **The tick-check path.**
  - A raising hook becomes `capture_error` with its message. Later ticks run nothing.
  - `stop()` after a failure works: `_FailedConn.close` is a no-op and the writer task is already done.
- **SOAK-1.**
  - Ended sessions always have `end_id` set, by both closing paths, so `retention_span` never meets a NULL.
  - The `since` shortcut is sound: when the new span covers the old one, rows outside the new span are a subset of rows outside the old one.
  - `config.py` comments are accurate.
- **SOAK-1 on the perf capture** (6M lines, read-only):
  - The `(id < ? OR id > ?) ... ORDER BY ts` delete stays on `idx_lines_ts`, with and without `since`. It takes 3-4 ms per chunk with the span at the bottom, middle or top, and 0 ms when nothing has expired.
  - `_unclean_stop_notice` seeks `idx_lines_chan_id`.
- **OP-7 on the indexed perf copy.**
  - `query_lines(port='board')` asc and desc, with chans, with `since_id` and with a wide `last_ms`: 4-6 ms.
  - `count_lines(port='board')` with own rows costs the same as without (0.62 against 0.63 s).
  - The callers of the changed helpers were rechecked (class 86):
    - `_window_terms` defaults to exact for the CAN and plot readers.
    - The verdict scopes pass `own_rows=False` (`server.py:2680`, `:2722`).
    - `/ws` and the exports take own rows, as ruled.
- **RES-4.**
  - The unary `+` on the ceiling is pinned: removing it fails `test_since_ts_seeks_by_id_rather_than_scanning_the_table` (mutation run).
  - Keeping `id_to` with `floor_ts` costs about 0.25 s more at 6M lines on a full-capture count, off the loop.
- **OP-2 across a restart.** The rebuild trims (`store.py:2942`). During a rebuild, live trimming acts on the `live` dict only, and the merge trims again.
- **Runs.** The new and edited test files, 16 files, `run1.txt`: 197 passed.

## Not covered

- Windows: the identity check (`st_ino` on NTFS, SMB, exFAT), sharing semantics on replace (FD-STORE-2 is reasoned from the SQLite VFS, not run), and the modes path.
- FD-STORE-1 was driven with a reader in the same process but outside the store. The in-daemon reader paths (plot rebuild, session export) were not driven.
- Python below 3.11 (`_is_busy`'s message fallback). Whole suite not run (brief).
- The server-side lines from this batch (`_verdict_rows`, the CaptureWatch subscribe, the live row matcher) are for the server reviewer.
- The daemon's RES-8 traceback handling (`daemon.py:292`) is for the daemon reviewer.

## The two questions

1. **What am I least confident about here?**
   - That FD-STORE-1 also bites through the daemon's own readers. It was driven with an outside reader only.
   - The busy timeout is 5 ms and the plot rebuild holds a read for seconds, so I expect it does, but I did not run that path.
   - FD-STORE-2 is reasoned from the SQLite Windows VFS share mode, not run.
2. **What should we have checked that we have not thought about?**
   - The remedy text on every failure surface. FD-STORE-1 shows `capture_error` and `mcu status` prescribing "restart" in exactly the state where a restart does the damage.
   - Every `capture_error` producer should be ruled for whether a restart is safe.
   - Every time window that is resolved in two places (the server floor, the store ceiling) should be ruled for whether both halves use one anchor, as FD-STORE-3 shows they do not.
