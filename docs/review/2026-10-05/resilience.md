# Resilience leg, 2026-10-05

HEAD 5eaaeeb (0.5.0), Linux. Probes and raw outputs: `~/tt-data/mcuscope-2026-10-05/resilience/` (`p_*.py`, `out_*.txt`, harness `h.py`).
Disk-full cases ran under `unshare -r -m` on a size-limited tmpfs (real ENOSPC, no sudo). Clock cases ran the daemon through `clockd.py`, which offsets `time.time()` by a number read from a file.

## RES-1 HIGH CONFIRMED: another process holding a write transaction freezes the whole daemon

- Where: `host/mcuscope/store.py:748` opens the writer connection with sqlite3's default 5 s busy timeout; the writer runs on the event loop (`store.py:1074`), and on failure `_insert_individually` (`store.py:1250`) retries every row, each paying the same wait with no yield.
- Failure: a DB Browser left with an unsaved edit (any `BEGIN IMMEDIATE` on the capture) blocks the loop for as long as it is held.
  - `/status`, `/ws`, `/cmd` and the web UI stop answering; SIGTERM is not acted on (the first run needed SIGKILL after 8 s).
  - The serial stream is shed while frozen: after a ~90 s hold, `rx_dropped=38743`, `write_errors=17`. Both are counted and announced, so no silent loss, but the daemon is hung.
- Repro: `p_dbproc.py write 500 12` (second connection `BEGIN IMMEDIATE; INSERT INTO meta ...`): `/status` timed out at 90 s, latency sampler max 32.8 s plus one 60 s timeout; recovery within 3 s of `ROLLBACK`.
- Fix: give the loop connection `timeout=0` (or a few ms of `busy_timeout`). Treat `SQLITE_BUSY` as "keep the batch, back off, retry" instead of the row-by-row fallback, which multiplies the wait. Announce "capture database locked by another process" once per episode (sys row when it clears, a `/status` field while it lasts).
- Class: 1 (SQLite work on the loop). The writer's documented loop exemption assumes each statement is bounded; a busy wait is not.

## RES-2 HIGH CONFIRMED: capture file replaced or deleted under a running daemon goes undetected; a replace ends in a corrupt file

- Where: `store.py:739` `start()` records no identity for the file; nothing re-checks `db_path` afterwards. `db_size_bytes` (`store.py:3316`) swallows the `OSError`.
- Replace (restore an older copy over the live file with a rename): capture continues into the old inode with nothing reported.
  - After the daemon stops, its `-wal`/`-shm` stay beside the restored file, and the next open replays the old database's WAL into it.
  - `PRAGMA integrity_check` then fails (`Tree 3 page 45: btreeInitPage() returns error code 11`), 3 of 3 runs. The restored file is destroyed.
- Delete (`rm capture.db*`): capture continues into the unlinked inode; `/status` shows `db_size_bytes=0` beside `db_content_bytes=245760`, `writer_alive=true`, no warning. Everything captured is gone at stop.
- Repro: `p_unlink.py replace` and `p_unlink.py rm`.
- Fix: record `(st_dev, st_ino)` of `db_path` at start and compare on every `sweep_tick` (cheap stat, off the loop).
  - On mismatch or ENOENT: stop writing, close the writer and every read connection so no WAL is left paired with a foreign main file, and fail loudly (log error, sys row impossible, a `/status` flag such as `writer_alive=false` with a reason).
- Class: none; candidate new class "a file held open by handle while its path is re-resolved by others" (the read connections open by path, the writer by handle).

## RES-3 MEDIUM CONFIRMED: a disk-full hole leaves no record in the capture, and the log takes one line per lost line

- Where: `host/mcuscope/serial_link.py:898` `_drop_rx_line`.
  - The once-per-episode sys row is spawned at the start of the episode, through the store that is failing, so it is lost too (`write_errors` is `rx_dropped + 1`).
  - `_unstorable.clear()` (`serial_link.py:978`) re-arms silently when storage returns. The `log.warning` runs per line, not per episode.
- Failure: 16 MB tmpfs filled for 9 s at 2000 lines/s: 18516 lines lost (id gap 6136 to 24654).
  - After recovery the capture's sys rows are only `daemon start`, `connected`, `target`. A later export or `mcu lines` shows a silent hole, and the counters that recorded it reset at the next daemon start.
  - The log got 18516 identical warnings in 9 s. With `db_path` on a separate volume, that is about 2000 lines/s into the data dir's `.err` file.
- Repro: `unshare -r -m .venv/bin/python p_diskfull.py 16m 2000`.
- Fix: write the episode's sys row when it closes, with count and span ("port board: 18516 rx lines could not be stored between HH:MM:SS and HH:MM:SS: <reason>"), as `_check_stamp_order` does for late rows. Log the first occurrence and the closing count only.
- Class: none; candidate new class "an episode notice written through the path whose failure it reports".

## RES-4 MEDIUM CONFIRMED: after a backwards clock step, `last_ms` windows include pre-step lines for about 10 s, then exclude them

- Where: `store.py:1916` `_window_floor` gives a now-anchored `last_ms` window a floor and no ceiling; `store.py:2047` `_window_id_floor` excludes the pre-step rows only once a post-step row is `WINDOW_TS_SLACK_S` old.
- Failure: 1 h step back, 4 s of pre-step capture.
  - At t+1 s and t+5 s, `/lines?last_ms=3000` returned 233 and 308 rows, 180 of them stamped before the step (the whole pre-step capture).
  - `/assert {"expect": ["^flood line 5 payload"], "last_ms": 3000}` answered `pass` on line id 16, captured before the step.
  - At t+13 s the same query excluded every pre-step row and the assert failed. With a real 1 h step, up to an hour of old lines qualify in that window.
- The sys row announcing the step says windows "can miss rows"; the first ~10 s include extra rows instead.
- Repro: `p_clock.py back`.
- Fix: a now-anchored `last_ms` window also takes `ts <= now + small skew`. "The last N ms" then never contains rows stamped ahead of the clock, which is exact rather than slack-dependent. Reword the sys row to cover both directions.
- Class: near 77 (a clock that goes backwards handled as a repeat), host side.

## RES-5 MEDIUM CONFIRMED: a text export killed mid-run is left in place looking complete

- Where: `host/mcuscope/cli.py:1815` `_OutFile` writes the target path directly; the removal guard (`cli.py:1905`) runs only on exceptions.
  - The CLI installs no SIGTERM handler, so `timeout 60 mcu log export -o x.txt` (what an agent writes), a closed terminal, or SIGKILL all skip it.
- Failure: 1,000,007-line export, killed at 1.5 s. SIGTERM left 102,351 lines and SIGKILL 112,443, each a plausible text capture.
- Also: a failing export onto an existing file (ENOSPC) leaves nothing, so the earlier good export at that path is lost (`p_cliexport.py full`, "overwrite existing").
- `session export` killed mid-download leaves a partial `.db` too, but it opens as `database disk image is malformed`, so it does not look complete.
- Repro: `p_cliexport.py kill`, `p_cliexport.py sesskill`.
- Fix: stream into a sibling temp name and `os.replace` it onto the target only on completion. This covers every non-completion (signal, power loss) and keeps a previous file intact. Apply it to `Client.download` and `plot export` too.
- Class: 49 (partial on non-completion); the sweep should list signal termination as a non-completion.

## RES-6 MEDIUM CONFIRMED: age retention deletes silently, so a forward clock step can empty the capture with no trace

- Where: `store.py:3247` `_sweep_retention_locked` deletes with no sys row, no log line and no counter. The size cap, by contrast, writes a sys row and counts `lines_trimmed`.
- Failure: daemon restarted with the clock 11 days ahead and `min_sessions = 0`: every row of the previous run (148) was gone at startup. No sys row, no log line; only the capture-identity rotation was logged.
  - With the default `min_sessions = 5`, session rows survived and only the pre-session `daemon start` row went.
  - Triggers: an RTC-less board host booting with a stale clock and then stepping, a manual date change, or a VM resumed with a skewed clock.
- Repro: `p_clock.py fwd 0`, `p_clock.py fwd 5`.
- Fix: count age-expired lines on `/status` and write one sys row per sweep that deleted anything ("storage: expired N lines older than <cutoff>").
  - Optionally refuse to expire rows stamped newer than the last clean stop when the clock moved past `retention_days` across a restart.
- Class: none (the "every shed counted or announced" convention).

## RES-7 LOW CONFIRMED: an unclean daemon end leaves no record in the capture

- Where: `store.py:846` `_close_crashed_auto_session` logs at info only; a named session left open is resumed with a `resuming session` marker and no note of the crash.
- Failure: kill -9 at 3000 lines/s. The capture reads `daemon start` with no preceding `daemon stop`, and nothing says lines in flight were lost (12,246 received, 11,900 stored; the exact count is unknowable).
- Repro: `p_kill9.py auto`, `p_kill9.py named`.
- Fix: at start, when the previous run did not stop cleanly (open session, or the newest sys row is not `daemon stop`), write a sys row "previous run ended without a clean stop after line N (HH:MM:SS); lines in flight were lost".

## RES-8 LOW CONFIRMED: a corrupt capture at startup fails with a raw traceback that names neither the file nor a remedy

- Where: `store.py:762` (first PRAGMA) raises `sqlite3.DatabaseError` through the lifespan (`server.py:479`).
- Failure: garbage file, half-truncated file, bad header magic and a zeroed interior page all exit 3 (good: nothing is opened for writing).
  - `mcu daemon start` prints the traceback tail ending `sqlite3.DatabaseError: file is not a database`.
  - The interior-page case first logs six `batched insert failed ... retrying row by row` lines before the traceback.
- Repro: `p_corrupt.py`.
- Fix: catch `sqlite3.DatabaseError` in `Store.start` and exit with "capture <path> is unreadable (<msg>): move it aside or set storage.db_path".

## RES-9 LOW CONFIRMED: an external long read transaction grows the WAL without notice, outside the size cap

- Failure: the sqlite3 CLI or a browser holding `BEGIN; SELECT ...` grew the `-wal` from 2.6 MB to 28 MB in 21 s at 2000 lines/s. Nothing was announced, and it stayed at 28 MB after release (under `journal_size_limit`).
- `content_bytes` excludes the WAL by design (`store.py:3108`), so `max_db_bytes` does not bound disk use while a reader pins it.
- Repro: `p_dbproc.py read 2000 21`.
- Fix: on the minute tick, run `PRAGMA wal_checkpoint(PASSIVE)`. When it repeatedly cannot reach the end of the log and the WAL passes `journal_size_limit`, announce "another reader is holding the capture open; the WAL is N MB".

## RES-10 LOW CONFIRMED: deleting `<db>.lock` lets a second daemon write the same capture

- Where: `host/mcuscope/lockfile.py` locks whatever inode the path names at acquire time and never re-checks.
- Failure: after `rm unlink.db.lock`, a second `mcuscoped` on another port started on the same `db_path`. Both wrote, with `UNIQUE constraint failed: lines.id` retried row by row, and the second daemon's auto session overlapped the first's rows. Integrity held and no rows were lost.
- Repro: `p_unlink.py lock`.
- Fix: after `flock`, compare `fstat(fd)` with `stat(path)` (retry on mismatch). Re-check on the sweep tick and log loudly if the lock file was removed. POSIX only; Windows cannot delete an open lock file.

## Checked and fine

- Disk full (`p_diskfull.py`): the writer survives; `write_errors` and `rx_dropped` count every line; `writer_alive` stays true; `mcu status` prints `write_errors=` and `dropped=`; capture resumes the moment space returns; `integrity_check` ok; ids not reused.
- Writes during disk full: `POST /marker`, `/sessions` and `/cmd` answer 500 `{"error": "commit failed: database or disk is full"}` at once, and session state is unchanged.
- Daemon start on a full volume: refuses (`database or disk is full`, exit 3); nothing half-written.
- Export into a full target (`p_cliexport.py full`): `log export`, `--csv`, `session export`, `--bundle` all exit 1 with `cannot write <path>: [Errno 28]`, and the partial file is removed.
- Session export whose temp copy fills the capture volume (`p_exportfill.py`): 400 `export failed: database or disk is full` in 0.6 s, temp copy removed, no capture line lost at 500 lines/s.
- kill -9 of the daemon mid-burst (`p_kill9.py`): `quick_check` ok; restart in 0.6 to 0.8 s past the stale pid record and lock; the crashed auto session is closed at its last row; a named session is resumed.
- kill -9 during a session export build (`p_orphan.py`): the temp copy and its journal are left, and the next start sweeps them.
- Upgrade 0.4.0 to HEAD to 0.4.0 on one capture (`p_updown.py`): capture id, sessions and row counts preserved; HEAD builds `idx_lines_port_chan_id` and `idx_lines_host`; 0.4.0 runs on the migrated file, keeps writing and closes sessions.
- HEAD CLI against 0.4.0: `status`, `lines` and `--json status` exit 1 with the version refusal; HEAD `daemon status` and `daemon stop` reach and stop the 0.4.0 daemon (`p_stopold.py`).
- 0.4.0 CLI against HEAD: `status`, `lines`, `tail`, `session list`, `cmd ping`, `mark` and `log export --last-ms` all work.
  - A route walk of both versions (`routes.py`) shows no query or body parameter removed or renamed, only additions, so an old client loses no filter silently.
- Backwards clock step: the late-rows sys row lands at the first late batch. Session membership is by ids, so `started_ts > ended_ts` only affects file names, which `_resolve_window` clamps. `purge before_ts` selects by `ts` as specified.
- Corrupt capture variants never start and never write (exit 3).

## Not covered

- Web UI time axis under clock steps: no browser run. SPEC 9.2 behaviour has node tests in `timewindow.js`; a forward step reads as a gap by construction.
- The hourly age sweep across an in-run clock step (only the startup sweep was driven), and `_last_age_sweep` after a step back.
- Disk full during a retention sweep or `incremental_vacuum`; the export-fill case above 500 lines/s.
- Deleting only `-wal`/`-shm` under a running daemon; RLIMIT_FSIZE (tmpfs ENOSPC used instead).
- Windows: replace or delete of an open capture (sharing violations change RES-2 entirely), antivirus or backup agents holding the file (likely the commonest real trigger of RES-1, unmeasured).
- Real suspend and resume.

## The two questions

1. Least confident: RES-2's mechanism.
   - The corruption after a replace is driven 3 of 3, but which connection leaves the `-wal` behind (the writer by handle, or read connections opened by path after the swap) is not established. The fix should be checked against both.
   - RES-4 was driven with one step size and 4 s of pre-step data; the 10 s flip point is read from `WINDOW_TS_SLACK_S`, not measured at other rates.
   - RES-1 was measured at one flood rate.
2. What we had not thought about: the external-process cases.
   - Every surface assumes the daemon is the only thing touching the capture, and nothing checks it: the lock file, the path, write locks and read snapshots (RES-1, 2, 9, 10).
   - The same "notice written through the failing path" shape (RES-3) is worth sweeping across the other four `_EpisodeNotice` users and the store's own sys rows.

## Scratch and side effects

- `~/tt-data/mcuscope-2026-10-05/resilience/`, 516 MB:
  - `db/` 187 MB (synthetic 1M-line `big.db`)
  - `out/` 294 MB (exports)
  - `v040/` 35 MB (0.4.0 venv)
  - `logs/`, `env/`, `cfg/`, probe scripts
- mcuscope 0.4.0 ignores `MCUSCOPE_DATA_DIR`, so its runs wrote three small files into the real data dir; they are left for the owner to remove:
  - `~/.local/share/mcuscope/mcuscoped-127.0.0.1-18622-startup.log`
  - `~/.local/share/mcuscope/mcuscoped-127.0.0.1-18638.err`
  - `~/.local/share/mcuscope/mcuscoped-127.0.0.1-18638-startup.log`
  - No capture data went there: every run used a scratch `db_path`.
- All processes started were stopped (checked with `ps`); the tmpfs mounts lived only in their namespaces.
