# Fix-diff leg: store (2026-09-24)

HEAD: `b994076c88c875ad43c30789cda6039d147b7abe`

Scope: `host/mcuscope/store.py` hunks of `6e4f6f7..HEAD`, the store test files, the SPEC 3.2/3.4/schema hunks, `fix-store.md` Doubts, the server batch's store edits.
Scratch (scripts, logs, a copy of big.db, a mutable copy of `host/`): `~/tt-data/mcuscope-2026-09-24/fixdiff/store/` and `.../store-copy/`.
Mutants ran from the copy; a probe test asserted `mcuscope.store.__file__` was the copy's.

## Findings

### 1. Medium: commits are held below 200 lines/s
- `store.py:916` measures the rate as `len(batch) / (now - last_commit)`, one commit's instantaneous rate; `store.py:859` holds on it.
- Two commits under 5 ms apart read as over 200 lines/s, so a quiet stream plus any awaited row (a `cmd` tx row, its response, a marker) sets off holds.
- Scenario: a board printing 50 lines/s, an agent issuing `mcu cmd` every 250 ms.
  - Each response row waits for a held commit before `_settle_rx_line` resolves the command.
  - `latency_ms` grows by tens of ms, and a response landing in the last 100 ms of `timeout_ms` can come back as `timeout`.
- SPEC 3.2 item 2 and the PERF-9 ruling both say commits are immediate below about 200 lines/s.
- Windows on Python 3.10 to 3.12: `time.monotonic()` ticks at 15.6 ms, so `now - last_commit` is often 0, which reads as 1000 x batch lines/s. Expect more misfires there (reasoned).
- Driven (`hold_rate.py`, `mixed_latency.py`):
  - 49 lines/s stream alone: 0 held commits.
  - The same stream plus an awaited row every 250 ms: 42 held commits in 4.2 s, awaited row median 53 ms (0.2 ms with no stream).
  - A 159 lines/s stream alone: 17 held commits in 4 s.
- Test gap: `test_store_writer_commits.py:63` only covers a slow stream with nothing else going on.
- Fix:
  - Measure the rate over a window (lines committed in the last second, or an EWMA with a time constant around 0.5 s).
  - Add a mixed-load test: a 50 lines/s stream plus awaited rows, asserting no hold.

### 2. Medium: `mcu daemon start` kills the one-time index build, and every retry starts it again
- `store.py:632` builds `idx_lines_port_chan_id` inside `Store.start()`, before the daemon answers `/status`.
- `cli_daemonctl.py:86`: the readiness wait defaults to 20 s, after which the CLI stops the daemon. The `CREATE INDEX` rolls back, so the next start begins from zero.
- Scenario:
  - The build runs at about 2.5 s per million lines, so any capture above about 8M lines can no longer be started with the plain CLI after the upgrade.
  - That is 2 days at 50 lines/s, against a default `retention_days` of 10.
  - A slower disk lowers the threshold.
- Driven:
  - On a copy of big.db (6M lines) with the index dropped, I ran `mcu daemon start --timeout 6` twice.
  - Both runs printed `did not come up ... within 6s; stopped it`, and both err logs end in `building index idx_lines_port_chan_id once`.
  - The build was lost each time.
- Fix, any one of:
  - The CLI keeps waiting, and says why, while the daemon's err log ends in the build notice.
  - The daemon binds and answers a "starting" status before the build.
  - At least, the refusal message names the index and the `--timeout` it needs.

### 3. Medium: nothing counts or announces a stamp inversion past the 10 s slack
- `store.py:183`, `store.py:1811`: past the slack, a `since_ts`/`last_ms` window silently drops rows whose time is inside it. The same goes for `/wait`, `/assert` and `count_lines`.
- That breaks the "never drop silently" convention, and an `/assert forbid` over such a window can pass.
- Driven (`stall.py`):
  - I fed the real `SerialPort._on_bytes`/`_consume` and the store writer the stamps a reader takes during a 15 s loop stall at 800 lines/s, plus a quiet port's line.
  - Worst inversion: 12.5 s.
  - `since_ts` windows starting in the last 0.2 s of the stall missed three rows: the quiet port's line, the marker posted after the stall, and the port's own `rx queue overflow` sys row.
  - `count_lines(floor_ts)`: 21 against an exact 23.
- When the bound breaks is in "Slack bound" below.
- Fix:
  - The writer keeps the highest committed `ts`.
  - A row that commits more than the slack below it goes into a counter in `/status` and a once-per-episode sys row.
  - Optionally, widen the floor by the largest inversion seen.

### 4. Low: `delete_before_ts` also deletes rows committed after the request
- `store.py:2778` chunks until a chunk finds nothing, with no id ceiling.
- `before_ts` may be up to 60 s ahead of now (SPEC 3.4). Rows captured during the purge that are stamped before it are deleted too, so `deleted` exceeds the dry run and `id_to` understates.
- The replaced id-range code froze the set at the request.
- Driven (`purge_chase.py`, cutoff now+3 s under a live stream): the span reported 2200 rows, ids 1..2200, but 2400 were deleted; the 200 extra were committed after the request, and the capture identity reset.
- Fix: freeze `id <= max_id()` at the request in both `before_ts_span` and the chunk select.

### 5. Low: `_scan_plot_summary` reads without one snapshot
- `store.py:2263`: the totals, the per-port counts and the per-key fetches run as separate autocommit statements on the worker connection. The old form was one statement.
- A delete committed between them mixes two snapshots. The delete does mark the summary dirty (the lock is held), so the next read heals it.
- But the reads waiting on this rebuild get wrong counts, and a delete between the busiest-port lookup and the final fetch leaves `None` at `store.py:2318`/`2321`. The read then fails with a TypeError.
- Driven (`torn_scan.py`, a delete between statement 1 and 2):
  - Deleting aux's lines left `busy/temp` at 45; the true count is 40.
  - Deleting busy's tail left `busy/temp` at 40; the true count is 30.
- Fix: wrap the scan in `BEGIN`/`COMMIT` on the read connection. A WAL reader does not block the writer.

### 6. Low: the crashed-session branch for an empty `lines` table is untested
- `store.py:682`: the `else` branch covers an open automatic session with no rows at or after its start, for example `purge all` followed by a crash.
- Mutant `if True:` survived 208 tests: the session, store, hardening and time-window files.
- Driven (`crash_empty.py`): under the mutant, `Store.start()` raises `TypeError: 'NoneType' object is not subscriptable`, so the daemon cannot start. The real code starts and drops the session.
- Fix: add that scenario as a test.

### 7. Low: SPEC 3.4 `/purge` states the `before_ts` rule twice
- `SPEC.md:846` and `:847` say the same thing, one sentence from each of two batches. Delete one.

### 8. Low: the SPEC schema does not list the new index
- `SPEC.md:985-987` lists the `lines` indexes without `idx_lines_port_chan_id`. SPEC is the contract.
- Add `CREATE INDEX idx_lines_port_chan_id ON lines(port, chan, id);   -- port with chan`.

### 9. Low: an export abandon can miss its copy
- `store.py:1505` hands the connection over before any SQL, and `abandon()` calls `interrupt()`.
- An interrupt made while no statement is running is a no-op. An abandon between `on_open` and `executescript`, or between two INSERTs, therefore lets the copy run to the end.
  - The files are still removed, but a slot of the 2-build pool stays busy for the whole copy.
- Driven: with sqlite 3.47.1, `interrupt()` followed by a 200k-row INSERT ran to completion.
- Fix: set a progress handler in `on_open` that returns the abandoned flag, so it applies to every statement.

### 10. Latent: the writer holds a dequeued request across its hold
- `store.py:863`: `req` is out of the queue during `await asyncio.sleep(hold)`.
- If the task is cancelled there, `_fail_queued` cannot see it, and the batch handler catches only `Exception`, so its future is never resolved.
- Driven (`hold_cancel.py`): after a cancel during a hold plus `_fail_queued`, the future is still pending.
- Not reachable from `stop()` today: a hold starts with under 1000 rows queued, and the 5 s deadline does not fall inside a 100 ms hold.
- Fix: fail `req` on `CancelledError`, then re-raise.

### 11. Nit: no test pins the 10 s slack
- Mutant `WINDOW_TS_SLACK_S = 3.0` survives the time-window, hardening, daemon store and lines-plan files: the tests' inversions are 1.5 s and they scale with the constant.
- Add an inversion of 9.5 s that must be kept.

### 12. Nit: branches no test distinguishes (all mutants survived 208 tests)
- `store.py:933`, the resync after a failed commit.
  - Its comment says it only guards against another writer.
  - Test it with a foreign row inserted before the failure, or delete it.
- `store.py:1613`, the empty-summary fast path in `_plot_points_in`.
  - The aggregate returns `[]` there anyway; it is a performance path only.
- `store.py:631`, the `_in_schema(conn, "lines")` guard.
  - Without it, every fresh capture logs the build notice.
  - Extend `test_an_older_capture_gets_the_index...` to assert no notice on a new file.

### 13. Nit: stale or overstated text
- `store.py:899` says the fallback "re-reads max_id()". It reads `_max_id_sql` via `_resync_next_id`.
- `ARCHITECTURE.md:32` leaves out the other dirty case: a delete during an in-flight rebuild.
- `test_daemon_r2026_09_12_store.py:178`: the floor is now 1 for any capture younger than the slack, so the assertion and its message describe nothing.
  - The query assertion below it still pins `ts > ?`: mutant `since-ts-inclusive` was caught.
- `test_store_plot_reads.py::test_the_wal_is_truncated_after_a_checkpoint` reads the pragma only. Rename it, or grow the WAL and assert the file size after a checkpoint.

## Doubts verified

- "10 s bounds real inversions": holds as a doubt. It is right at the stated premise and breaks outside it (see "Slack bound"). Driven with a 12.5 s inversion that lost 3 rows (finding 3).
- "Hold backoff not measured live; mixed load up to 100 ms": holds, and it is wider than stated.
  - Below the threshold, awaited rows wait a median 53 ms at 49 lines/s (finding 1).
  - The backoff never engages because the stream fills every hold.
- "Rebuild with two equally busy ports not measured": holds, but it is not a regression.
  - `two_ports.py`, 1M lines x 3 points: dominant port 0.62 s, balanced 2.74 s, old SQL 6.66 s. Counts are equal to the old SQL's.
- "Index build blocks startup; not measured on Windows or a slow disk": holds. The CLI's 20 s wait turns it into a start-kill loop (finding 2). Windows still not measured.
- Server batch, `on_open`: holds as described (called before any SQL). The interrupt can be lost between statements (finding 9).
- Server batch, `last_id_before_ts` deleted: holds. No reference remains in `host/`, SPEC or ARCHITECTURE.
- The CAPTURE-1 ruling against the implementation: the store agent's pushback is right.
  - The ruling's literal seek, the first row at or after `cutoff - slack`, drops id 2 in the 3-row case of `test_a_row_stamped_after_the_floor_but_committed_first_is_kept`.
  - The newest-row-before form is exact under the bound: every lower id was stamped less than the slack after that row, so before the cutoff.

## Slack bound

- The inversion between two rows is the difference in their stamp-to-id delays.
  - Loop-stamped rows (sys, marker, tx, session markers) and quiet ports wait only for the write queue.
  - A backlogged port's line also waits in its rx deque (`RX_QUEUE_MAX` 10000, oldest shed) and drains `RX_BATCH_MAX` 1000 per writer commit.
- So the worst inversion is about min(age of the backlog, 10000 / that port's drain rate).
  - The 1.4 s figure is the full-rate case: a writer at about 15k lines/s with one saturated port. Each further saturated port adds about 0.7 s, so fifteen would be needed at full rate.
  - The commit hold adds at most 0.1 s, and is skipped once 1000 rows are queued.
- What breaks it:
  - A loop stall longer than 10 s while a port sends under 1000 lines/s but more than 1000 lines in the stall. Driven: 15 s at 800 lines/s gave 12.5 s.
  - At 2000 lines/s the deque sheds down to 5 s of lines, so a 12 s stall inverted by only 5.1 s (driven). A longer stall at a faster rate is capped by the deque.
  - A writer that drains a saturated port below 1000 lines/s: a 1000-row commit taking over 1 s, sustained.
    - `synchronous=NORMAL` in WAL does not fsync per commit, so this needs a disk slow even at checkpoints.
    - Examples: a network share, an SD card, antivirus scanning the `-wal` on Windows.
    - Not measured; no Windows or slow-disk run.
  - A backwards clock step over 10 s. A Windows workgroup PC syncs w32time about weekly and steps large offsets, so a fast RTC gets stepped back (reasoned from the documented defaults, not measured).
- The SPEC 3.4 parenthetical "(at most about 1.4 s per port at the writer's full rate)" reads as a bound. It should name these conditions and the counter from finding 3.

## Checked, nothing found

- `_window_id_floor` is exact under the bound, and every window read routes through `_window_terms`: `query_lines`, `count_lines`, CAN frames, plot series, plot export, `first_export_line_id`. `_window_id_ceiling` is exact.
- Purge span and delete select the same rows on a fixed data set. The chunk subselect is evaluated twice per chunk, with the same plan and the same rows.
- Rollover and `stop_session(reopen_auto)` abut, under `_session_lock`. No await falls inside a writer transaction (the hold precedes the inserts).
- The id sequence never moves down. `_insert_batch` advances `_next_id` only on success, and the fallback binds explicit ids after a resync.
- `_close_crashed_auto_session`: a named session is left open, and the end id and time come from the newest row, with no marker.
- Plot export paging: no statement is open across a yield; the holdback, the page doubling, the frozen top and the name sort are correct.
- PERF-6 series: the inner LIMIT matches the old SQL (test).
- PERF-1 plans:
  - On big.db I also ran the shapes the batch did not list: a busy port with busy channel lists, and the UI's list without `event`.
  - Each took 0.7 to 1.8 ms with `INDEXED BY` (the IN-early-out keeps the temp b-tree bounded by LIMIT).
  - `count_lines` took 0.44 to 1.1 s at 6M lines.
- Reclaim step budget, the startup trim's sys row, the budget wording, no REGEXP on cached read connections, the CAN `port` field.
- Windows:
  - No new text writes, sockets or paths in the diff.
  - The `export_session_db` connection closes before the caller unlinks.
  - The tests that depend on stamp order use explicit stamps, and the timing tests scale with elapsed time, so 15.6 ms timers do not break them.
- Every file in the slice passes run alone from the copy: `test_store_*.py` (13 files), `test_daemon_r2026_09_12_store.py`, and `test_timeline.py` (25.9 s, no longer timing out).
