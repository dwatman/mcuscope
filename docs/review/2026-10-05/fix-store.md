# Fix batch: store

Revert-verification: `~/tt-data/mcuscope-2026-10-05/fix-store/mutate.py` applies each mutation to the live file, runs the named tests, restores (`mutate.txt` holds the run). Every row below says CAUGHT unless stated.
Two branches no mutation could catch were deleted as redundant: the `_next_id` restore after a busy insert (`_insert_batch` raises before moving it) and a `capture_error` guard in `_run_tick_checks` (`_check_path` already raises it).

## RES-1: another process's write lock
- `store.py:1385` `_insert_when_unlocked`: on `SQLITE_BUSY`/`LOCKED` (`_is_busy`, `:411`) roll back, keep the batch, back off 10 ms doubling to 0.5 s, retry; never row by row. Awaits only with no transaction open. Cancelled while waiting: the batch's futures fail and are counted.
- `:921` writer `PRAGMA busy_timeout=5` after start (start keeps the 5 s default).
- `db_locked_since` set on the first refusal, logged once; `_lock_cleared_notice` (`:1423`) logs and inserts the sys row `storage: capture database locked by another process for <s> s from <HH:MM:SS>; writes were held meanwhile` in the batch that gets through.
- Test: `tests/test_store_write_lock.py` (external `BEGIN IMMEDIATE`; pins busy_timeout 5, no row-by-row log, one log line, one sys row, dense ids, the loop runs while held). Mutations: retry, notice, log-once, busy_timeout, cancel path.

## RES-2: capture replaced or deleted
- `_open_writer` (`:954`) records `(st_dev, st_ino)` (`_file_identity`, `:974`; None for `:memory:` or `st_ino == 0`).
- `_check_path` (`:818`) runs on each tick, off the loop on `match_executor` (`_run_tick_checks`, `:843`), and before every by-path open: `_open_read_conn`, `_open_export_conn`, the session export's ATTACH.
- `_fail_capture` (`:857`): sets `capture_error`, cancels the writer, fails what is queued, `PRAGMA wal_checkpoint(TRUNCATE)` on the old handle, closes the writer, swaps in `_FailedConn` (`:358`, every use raises `StoreError(capture_error)`), closes the read connections. `active_session()` answers None and `content_bytes()` 0 once failed, so `/status` keeps answering.
- **Mechanism, driven** (`fix-store/res2_probe.py`, both replace orders x readers cached/reopened, original against fixed store): all four original runs damage the restored file, all four fixed runs leave it intact (300 of 300 rows, integrity ok, WAL 0 bytes).
  - Writer: SQLite skips its checkpoint-on-close once the main file has moved, so the old `-wal` stays at the path and the next open replays it into the restored file (after-checkpoint order: `malformed`; before-checkpoint: 6500 foreign rows).
  - Readers: a read connection opened by path after the swap checkpoints the old WAL into the new file when it closes (the original's "reopen" runs left no WAL and 6500 rows).
  - Detection alone did not fix it: the first fixed version (close without TRUNCATE) still damaged all four. The TRUNCATE before any close is what fixes it.
- Smoke: isolated `--sim` daemon on 18801, capture deleted: after the tick, `/status` showed `capture_error` and `writer_alive=false`, `write_errors` counting the refused lines, and SIGTERM stopped it cleanly.
- Tests: `tests/test_store_capture_identity.py` (both orders x both reader modes, delete, every by-path opener, queued writes failed, readers dropped, `_FailedConn` message). Mutations: tick check, TRUNCATE, each of the three opener checks, writer cancel, `_fail_queued`, reader close, stand-in, both `/status` guards.

## add_tick_check
- `:813` `Store.add_tick_check(fn)`, callable before `start()` (the server lifespan registers `lock.verify` first). Any exception becomes `capture_error` (its message) through `_fail_capture`; later ticks run nothing.
- Test: `test_a_failing_tick_check_stops_capture_with_its_message` (raises `LockLost`), plus a `:memory:` case. Mutation: hook loop.

## RES-4: `last_ms` window ceiling
- `_window_anchor` (`:2208`): one clock or bound read per window; `_window_floor` uses it, and `_window_terms` (`:2298`) adds `+ts <= anchor + WINDOW_TS_SLACK_S`.
  - The `+` keeps the term off `idx_lines_ts`; the existing plan tests (`test_store_lines_plan.py`, `last_ms` bound shapes) pass.
  - The anchor is the ts of the newest line at or below `id_to`, an export's freeze included, else now.
- `count_lines` keeps an all-covering `id_to` when `floor_ts` is given (`:2471`), so a count and its rows share one anchor.
- Late-rows sys row reworded for both directions.
- Tests: `tests/test_store_window_ceiling.py` (now-anchored and /assert-frozen windows exclude rows an hour ahead; a bound-anchored ceiling; frozen count equals its rows). Mutations: ceiling, anchor at bound, count keeps id_to.

## RES-6: age expiry counted and announced
- `_sweep_retention_locked` adds to `Store.lines_expired` (`:3632`). `_sweep_retention_reported` (`:3634`, used by the startup sweep and the hourly tick) logs and writes `storage: expired <n> lines older than <local time>`.
- Test: `tests/test_store_retention_floor.py::test_the_running_session_ages_out_and_the_expiry_is_announced`. Mutations: counter, sys row.

## SOAK-1: the floor protects only ended sessions
- `retention_span()` (`:3337`): `(MIN(start_id), MAX(end_id))` of the newest `min_sessions` ended sessions. `retention_floor_id()` kept as `span[0]`.
- Age delete keeps `id BETWEEN lo AND hi` only. The size trim (`_delete_oldest_chunk`, `:3355`) spends below the span, then above it, before the forced pass. The `since` walk shortcut requires the new span to cover the last one.
- `config.py:94-106` comments restated.
- Tests: `test_store_retention_floor.py` (running session ages out, unsessioned lines after the last ended one age out, the size trim spends the running session first, a span that lost its top walks again). Mutations: ended-only, span top, size above span, since needs top.

## RES-7: unclean previous stop
- `_unclean_stop_notice` (`:1049`): when the newest `daemon start`/`daemon stop` sys row is a start, `start()` queues `previous run ended without a clean stop after line N (<local time>); lines in flight were lost` ahead of this run's rows. Constants `DAEMON_START_ROW`/`DAEMON_STOP_ROW` (`:224`).
- Smoke: kill -9 of an isolated daemon, then a restart, wrote the row (`line 244`) just before `daemon start`.
- Tests: `tests/test_store_start_checks.py` (a start with no stop, a clean stop, a first run, a row quoting the words). Mutations: notice, start-only.

## RES-8: unreadable capture
- `start()` turns a non-operational `sqlite3.DatabaseError` from `_open_capture` into `CaptureUnreadable` (`:345`, a `StoreError`): `capture PATH is unreadable (msg): move it aside or set storage.db_path`, raised `from None`. The connection is closed.
- `OperationalError` (full, locked, read-only, unopenable) passes through unchanged: it subclasses DatabaseError, and "move it aside" would blame a good file.
- Tests: garbage and interior-page files, a failure after open closing the connection, and a directory as `db_path` staying an `OperationalError`. Mutations: refusal, close, operational pass-through.
- Not the whole fix: uvicorn still logs the lifespan traceback (see Not done).

## RES-9: WAL pinned by another reader
- `_check_wal` (`:3695`), each tick: `PRAGMA wal_checkpoint(PASSIVE)`. Two consecutive ticks with `done < frames` and the WAL past `_JOURNAL_SIZE_LIMIT` log once and write `storage: another process is holding a read open on the capture; the WAL is N MB and cannot be checkpointed until that read ends`. Re-arms when a checkpoint drains it.
- Test: `tests/test_store_wal_pinned.py` (limit patched to 32 KB; control without a reader; once per episode; re-arm). Mutations: two ticks, once, rearm, stuck test.

## SEC-6: capture file and parent dir
- `_open_writer`: parent via `dirs.make_private_dirs` (`:959`); on POSIX a new DB is created 0600 with `O_EXCL` before `connect` (`:964`), so `-wal`/`-shm` follow. An existing file or dir keeps its mode.
- Smoke: daemon run showed dir 700, `capture.db`/`-wal`/`-shm`/`.lock` 600.
- Tests: `test_a_new_capture_and_its_new_dirs_are_owner_only`, `test_an_existing_capture_keeps_its_mode`. Mutations: file mode, dir mode.

## OP-7: `-p X` reads include port `''`
- `query_lines`: two index-ordered arms (port X, port `''`), each LIMITed, merged by `WHERE id IN (... UNION ALL ...) ORDER BY id`. No temp b-tree: a single `port IN` sorted the whole board.
- `count_lines`: `port IN (?, '')`. The subscriber feed (`:1772`) does the same.
- `own_rows=False` keeps the port exact, on `query_lines`, `count_lines` and `subscribe`. `_port_rows` is at `:190`.
- Verdict scopes: the coordinator ruled that `/wait` and `/assert` (live and retrospective) stay exact. `server.py:2669` (`_verdict_rows` adds `own_rows: False`) and `:2711` (CaptureWatch subscribes with `own_rows=False`); see Doubts.
- Tests: `tests/test_store_port_rows.py` (reads, counts, export, limit after merge, feed, exact verdict scope); `tests/test_server_verdict_own_rows.py` (a live `/wait -p X --chan marker` ignores a portless marker, with a positive control); plan tests in `test_store_lines_plan.py` cover both arms. Mutations: union arm, count IN, feed, both switches, both server lines.

## OP-2 (summary side)
- `_keep_recent_adhoc` (`:171`) keeps the `p.ADHOC_NAMES_MAX` most recent (by `last_line_id`) ad-hoc names per port. It runs on a new ad-hoc name in `_note_plot` (`:2813`) and after every rebuild merge (`:2942`), so the bound holds across a restart, where the decoder admits 256 more.
- Typed-stream names and other ports are not counted.
- Test: `tests/test_store_plot_summary_cap.py` (276 names, restart, 256 new names: only the 256 newest stay; typed names kept; points still stored; a forced rebuild agrees). Mutations: live trim, rebuild trim, ad-hoc only.

## Existing tests edited
- `test_store_size_cap.py`, the two floor tests: `stop_session()` before the sweep, since only an ended session is protected now. The newest-line check reads `port="t", dir="rx"`, because the end marker is now newest.
- `test_store_retention_walk.py`: `test_a_protected_expired_run_is_walked_once_not_every_sweep` ends its session; `test_a_risen_floor_walks_everything_again` ends `new-run` (SOAK-1).
- `test_store_lines_plan.py::test_the_age_sweep_does_not_read_the_table_when_nothing_has_expired`: ends the session, passes `retention_span()` (the chunk takes a span); comment updated.
- `test_store_plot_summary.py::test_deletes_subtract_instead_of_rescanning`: aux ids filtered to `port == "aux"` (OP-7 adds port `''` rows).
- `test_assert.py::test_sweep_tick_survives_a_failing_sweep` and `::test_sweep_tick_runs_the_age_sweep_only_when_the_hour_divides`: await `_initial_sweep_task` first. The tick now awaits its off-loop checks, which let the startup sweep call the patched method too.
- `test_server_scope.py` `FloorClock`: keys on `_window_anchor`, the clock read's new home.
- `test_assert.py` (2 lambdas), `test_server_live_verdicts.py` (1): `subscribe` doubles pass `**kw` through (`own_rows`).

## SPEC edits
- 3.2 item 5: the floor covers ended sessions only; the running session ages out; the expiry sys row and `lines_expired`.
- 3.2 item 6: other-process write lock, pinned WAL, file identity and what a failed capture does; start checks (unreadable file, unclean stop row, POSIX modes).
- 3.4 window paragraph: the `last_ms` ceiling and its anchor; the late-rows sys row text.
- 3.4 "Automatic sessions": the open session is not protected until it ends (it said the floor protected it). That paragraph sits under the sessions route; I edited it because SOAK-1 made it false.

## Guide wording (for the cli batch)
- `-p X` on reads (`lines`, `tail`, `log export`, `--json` streams) also returns the daemon's own rows (port `''`: markers made without `-p`, session boundaries, daemon notices). `wait` and `assert` judge only the named port's rows, so mark with `-p` when a verdict should see the marker.
- `status`: `lines_expired` counts lines removed by age retention. `db_locked_since` is set while another program holds a write lock on the capture (close it). `capture_error` set means capture has stopped (the file was replaced or deleted, or the capture lock was lost): restart the daemon.
- Retention: `min_sessions` protects the newest N *ended* sessions. A daemon left running is one session, and its lines older than `retention_days` expire.

## Changelog
- Another program holding a write lock on the capture no longer freezes the daemon. Writes are held and retried, `/status` shows `db_locked_since`, and a sys row records the episode.
- A capture file replaced or deleted under a running daemon stops capture with `capture_error` on `/status`, and the file now at that path is no longer corrupted by the old WAL.
- `last_ms` windows exclude rows stamped ahead of the window's end, so a backwards clock step no longer pulls pre-step lines in.
- Age retention is counted (`lines_expired`) and recorded in the capture.
- A daemon left running now ages out its own old lines: `min_sessions` protects only ended sessions.
- A previous run that ended without a clean stop is recorded at the next start.
- A corrupt capture fails the start with its path and a remedy.
- Another reader pinning the WAL past 64 MB is announced once per episode.
- New captures and their new parent directories are owner-only on POSIX.
- `-p X` reads include the daemon's own rows; verdicts do not.
- The plot channel list keeps the 256 most recent ad-hoc names per port.

## Not done
- **server.py, outside my files, done at the coordinator's request:** the two verdict-scope lines above. A server owner should review them.
- **server.py lifespan, RES-8 traceback:** starlette formats the lifespan exception as a traceback, which uvicorn logs; the startup log's reason line is the last line, now the clean message.
  - To drop the traceback: around `store.start(...)`, `except CaptureUnreadable as exc: log.error("%s", exc); raise SystemExit(3)`. Not tried; check uvicorn's handling of SystemExit in lifespan first.
- **server.py, optional:** map `StoreError` from a failed capture (`store.capture_error is not None`) to 503 instead of the generic 500. Reads after a failure now say the cause, but each one logs a traceback.
- **server.py:** use `store.DAEMON_START_ROW`/`DAEMON_STOP_ROW` for the lifecycle rows (`server.py:547`, `:609` at time of reading). `_unclean_stop_notice` matches the literal text.
- **SPEC 3.5 and 4 (OP-7):** "A `port=` filter on a read also returns the daemon's own rows (port `""`); `/wait` and `/assert` judge only the named port's rows." Owned by server (3.4 routes, the `port=` paragraph near `SPEC.md:832`) and cli (4).

## Doubts
- **Verdict scope:** class 84 says a verdict with `chan` judges every row of that channel "whoever wrote it". The coordinator ruled that port `''` rows stay out of a `-p` verdict. I implemented that, so `wait -p X --chan marker` does not see a marker made without `-p`, which is the AGENTUX-2 example. The guide line above tells agents to mark with `-p`.
- **WAL TRUNCATE:** it can report busy if another process holds a read open at the moment of failure. That is logged with a "move the -wal aside" instruction, untested.
- **Not verified on Windows:** the identity check (`st_ino` from `os.stat`), sharing violations on replace (resilience called this out), and the modes path, which is POSIX only.
- **Python below 3.11:** `_is_busy`'s message fallback never runs on the 3.13 venv.
- **Tick timing:** checks run once a minute on `match_executor`, which a long regex scan can delay. Up to a minute of capture lands in the old file before detection; the probe shows that data is lost with the old inode, as before.
- **Plot summary rebuild:** it still issues one query per name in the capture, so the cap bounds memory and responses, not the cost of a rebuild over a historically flooded capture.
- **Not run:** the whole suite. I ran all `test_store_*` files plus every file mentioning the touched surfaces (`fix-store/run3.txt`): 2080 passed, 2 failed.
  - Both failures were my new identity test assuming one executor worker held the cached reader. Another worker opened a fresh connection and was correctly refused.
  - That read was dropped from the test, which then passed alone and in random order; the RES-2 mutations were re-run (`mutate2.txt`), all caught.
  - The other 2080 were not re-run after that one-line test change.

## Scratch
`~/tt-data/mcuscope-2026-10-05/fix-store/`:

- `mutate.py`, `res2_probe.py`: worth keeping in `mcuscope-tools`.
- `store.py.orig`, `config.py.orig`, `SPEC.md.before`, `server.py.before`, `store.py.good`, `server.py.good`: copies.
- `run*.txt`, `mutate.txt`, `mutate2.txt`, `diff.txt`, `plan.py`.
- `smoke/`: config, logs, and `cap/` with a capture DB and lock.

No process left running.
