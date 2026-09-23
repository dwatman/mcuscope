# Fix batch: store (2026-09-24)

HEAD: `b994076c88c875ad43c30789cda6039d147b7abe` (uncommitted work on top).

Scratch: `~/tt-data/mcuscope-2026-09-24/fixbatch-store/`.

- `copy/host/` is the private mutant copy. A probe test (`tests/test_zz_probe.py`) asserts that `mcuscope.store` is imported from the copy.
- `mutants.py` and `mutants.log` hold the revert-verification.
- `abandon_probe.py` drives finding 9.
- `second-opinion-index.md` is the second opinion on finding 2.

## Per finding

### 1. Commits held below 200 lines/s
- `store.py:262`: `_RATE_TAU_S = 0.5`. `store.py:952-956`: the rate is an EWMA, `rate * exp(-dt/tau) + n/tau`, instead of `n / dt`.
  - Two commits a few ms apart now add `1/tau` = 2 lines/s, not 1000 x n.
  - A Windows 15.6 ms tick reading `dt = 0` is harmless.
- Test: `test_store_fixdiff_writer.py::test_awaited_rows_beside_a_slow_stream_are_never_held` (50 lines/s stream plus an awaited row every 200 ms, over 50 hold decisions, none may hold).
- Revert: KILLED.
- `test_store_writer_commits.py::test_a_fast_stream_commits_at_most_once_per_interval`: the slack grew from +3 to +9 commits.
  - The average needs 6 bursts (about 0.1 s at 1000 lines/s) to pass 200 lines/s.
  - The bound still fails with no holds at all (30 commits against 15.5).

### 2. `mcu daemon start` kills the one-time index build
- Design: keep the build blocking in `Store.start()`, and make the CLI wait it out. The daemon then answers only once it is ready, and nothing is captured before the build ends, so nothing is lost.
- Rejected: building in the background after the daemon answers.
  - `CREATE INDEX` holds SQLite's one write lock for the whole build.
  - The writer runs on the loop with the default 5 s busy timeout, so every batch would freeze the daemon for 5 s and then count as `write_errors`. The second opinion drove `database is locked` against a concurrent build.
  - Pausing the writer instead stalls every awaited row for the whole build.
  - `INDEXED BY` on a missing index is a hard error, and dropping the hint brings back the 4.4 s loop walks.
- Rejected: answering a "starting" `/status` before the build. It needs a not-ready refusal on every handler, in server.py and daemon.py, for one upgrade.
- Resumable or short: not possible. SQLite has no incremental `CREATE INDEX`, and sorter threads and cache size change nothing (2.50 to 2.54 s per 2M rows).
- Store side, done:
  - `store.py:137-140`: `_SCHEMA_INDEXES` (parsed from SCHEMA), `INDEX_BUILD_NOTICE = "building index"`, `INDEX_BUILT_NOTICE = "built index"`.
  - `store.py:650-662`: the notice names every SCHEMA index missing on an existing table, not only `idx_lines_port_chan_id`. An old capture also lacks `idx_lines_port_id`, `idx_lines_chan_id` and `idx_plot_line`, and the CLI would kill those builds too.
  - A completion notice follows at WARNING. Nothing configures the root logger, so INFO never reaches the err log.
- Test: `test_store_lines_plan.py::test_an_older_capture_gets_the_index_and_says_it_is_building_it` now:
  - drops two indexes;
  - asserts both names in the start notice;
  - asserts the completion notice;
  - asserts nothing is logged on a new file.
- Revert: all three mutants KILLED (gate on one index only, completion at INFO, table guard removed).
- CLI edit needed: see "Edits in files I do not own".
- Second opinion: an opus-high agent, not fable-low. The global CLAUDE.md says not to use fable-low while Opus outclasses Fable.
  - It agreed with this design and my objection to building in the background.
  - It added the "every missing index" gate and the WARNING level, both taken.

### 3. Stamp inversion past the 10 s slack, silent
- Form chosen: a sys row per episode, in the capture itself. No `/status` counter, so no server.py edit is needed; the end row carries the count.
- `store.py:1007-1044`, `_check_stamp_order`: the writer keeps the newest `ts` stored (`_top_ts`). A row more than `WINDOW_TS_SLACK_S` below it is late. The sys row is appended to the same batch (`store.py:918-920`), so it commits with the rows it describes.
  - The first late row writes `storage: rows are committing up to X s behind newer timestamps, past the 10 s window slack; since_ts and last_ms windows that start among them can miss rows`.
  - The first batch with no late row writes `storage: rows are back in time order; N committed up to X s behind newer timestamps`.
- `store.py:673`: `_top_ts` is seeded from `MAX(ts)` at start, so a clock stepped back across a restart is caught (one index seek).
- Tests in `test_store_fixdiff_writer.py`:
  - `test_rows_committing_past_the_slack_are_announced_with_a_count`: the start and end rows, their wording, the count, and their positions among the rows.
  - `test_a_capture_restarted_behind_its_newest_stamp_announces_it`.
- Revert: KILLED on four mutants (no check, no end row, a row on every late batch, `_top_ts` not seeded).
- Cost: one Python pass over the batch's `ts`, about 0.1 us per row.
- The widened floor (the report's "optionally") was not done.
  - Announcing meets the rule.
  - A permanent widening after one stall would cost every later window read, and it resets on restart anyway.

### 4. `delete_before_ts` also takes rows committed after the request
- `store.py:2868-2881`: `delete_before_ts(before_ts, *, max_id=None)`. With `max_id`, the chunk select adds `id < max_id + 1` (the existing `floor_id` guard). With None it behaves as before.
- Test: `test_store_fixdiff_reads.py::test_a_purge_keeps_rows_committed_after_its_span` (cutoff now+30 s; 3 rows committed between span and delete survive; with no `max_id` the old behaviour deletes them).
- Revert: KILLED (bound ignored, and bound off by one).

### 5. `_scan_plot_summary` reads without one snapshot
- `store.py:2349-2364`: the statements run inside `BEGIN` ... `rollback()` on the scan's connection. It opens its own transaction only when none is open. The body moved to `_scan_plot_rows`.
- Test: `test_store_fixdiff_reads.py::test_a_summary_scan_reads_one_snapshot`.
  - A delete commits before the scan's second statement, once for aux's lines and once for busy's tail.
  - It asserts the counts and newest line ids of the scan's snapshot, and that the snapshot is released.
- Revert: KILLED (no BEGIN; snapshot left open).

### 6. Crashed session over an emptied `lines`, untested
- Test: `test_store_fixdiff_reads.py::test_a_crashed_auto_session_over_an_emptied_capture_is_dropped`.
- Revert (`if True:`): KILLED.

### 7. SPEC 3.4 states the `before_ts` rule twice
- SPEC text below. SPEC not edited.

### 8. SPEC schema lacks `idx_lines_port_chan_id`
- SPEC text below.

### 9. An export abandon can miss its copy
- The fix belongs in `server.py` (`_ExportJob.on_open`); the exact edit is below.
- `store.py:1573-1576`: the `on_open` docstring now says a progress handler stops the copy, because an `interrupt()` between statements is lost.
- Driven in the copy (`abandon_probe.py`: abandon inside `on_open`, before any SQL, 50k rows):
  - without the edit: `copy ran to the end: 50000 lines`;
  - with it: `copy stopped: OperationalError interrupted`.
- No test added: the test belongs in a server test file.

### 10. A cancel during a hold strands the dequeued request
- `store.py:891-897`: on `CancelledError` in the hold sleep, `req` is failed through `_fail_write` (so it is counted), and the cancel is re-raised.
- Test: `test_store_fixdiff_writer.py::test_a_writer_cancelled_during_a_hold_fails_the_row_it_took`.
- Revert: KILLED.

### 11. No test pins the 10 s slack
- Test: `test_store_fixdiff_writer.py::test_an_inversion_inside_the_slack_keeps_every_row_and_says_nothing`.
  - A 9.5 s inversion keeps the row in `since_ts` and `count_lines(floor_ts=)`.
  - No sys row is written.
- Revert (`WINDOW_TS_SLACK_S = 3.0`): KILLED.

### 12. Branches no test distinguishes
- The resync after a failed commit (`store.py:933` then) is deleted as redundant.
  - A foreign row collides in the next batch's `executemany`, and the row-by-row fallback resyncs then.
  - "Never moves down" is still pinned by `test_store_id_sequence.py::test_a_failed_commit_after_a_tail_purge_does_not_rewind_the_ids`.
- The empty-summary fast path in `_plot_points_in` is deleted as redundant: the aggregate returns `[]` for a capture without plot points.
- The `_in_schema(conn, table)` guard is now pinned by the new-file assertion in finding 2's test. Revert KILLED.

### 13. Stale or overstated text
- `store.py:936`: the comment now says the fallback's resync reads MAX(id) from SQL.
- `ARCHITECTURE.md:32`: not mine; the edit is below.
- `test_daemon_r2026_09_12_store.py::test_since_ts_excludes_its_own_instant_where_the_id_floor_cannot`:
  - An older row (T0-20) comes first, so the floor is 2 and the boundary row (id 3) sits above it. The assertion is no longer trivially 1.
  - `later-first` moved to T0+5 to stay inside the slack.
  - Mutant `since-ts-inclusive`: KILLED.
- `test_store_plot_reads.py`: renamed to `test_the_wal_size_limit_is_set`, which is all it checks.

### Tests changed because of finding 3 (the tests were right, their expectations assumed no sys rows)
- `test_store_time_window.py::test_purge_before_ts_deletes_by_age_not_by_an_id_range`: the stamps were rescaled to 6 s inversions (cut T0+10), with the same shape and assertions.
- `test_daemon_r2026_09_12_store.py::test_until_ts_wider_than_the_capture_keeps_every_row_after_a_clock_step`: the ceilings are compared with the returned row ids, since the 100 s step back now writes a sys row that takes an id.

### Tests outside my files that fail (all from finding 3's sys rows)
All three feed stamps decades behind the rows the store or lifespan stamps with `time.time()` (session markers, daemon start rows). That is a real inversion, so a sys row now takes an id. Each edit below was applied in the copy and passes.

- `test_regressions.py::test_line_ids_are_not_reused_after_the_table_empties` (reported by the server batch). The test's proxy is wrong, not store.py.
  - Its rows are stamped `ts=1.0` against the markers' now, so the end-of-episode sys row lands right after the end marker (id 9, session span 1..8).
  - `high = store.max_id()` is then 9, not the session's end, and the restart correctly seeds at `max(MAX(id), session refs) + 1` = 9.
  - The invariant it pins is that no session span is reused, and that still holds. HEAD already reuses an id past a session's end after `purge all` and a restart, with the capture identity reminted at the purge. Driven with HEAD's store.py (`head_reuse.py`): `session end 3, row after it 4, first id after restart 4`.
  - Edit (replace the four lines from `await store.stop_session()` to `return high, 0`):
    ```python
            alpha = await store.stop_session()
            # What `purge --all` does: delete every line, leaving the session rows behind.
            await store.delete_range(1, store.max_id())
            assert store.count_lines() == 0
            await store.stop()
            return alpha["end_id"], 0
    ```
  - Revert check in the copy: the mutant seed `self._next_id = self._max_id_sql(self._conn) + 1` fails the edited test.
- `test_prerelease_daemon_core_store.py::test_the_ceiling_walk_names_the_highest_id_not_the_newest_ts`:
  - Replace the `for i in range(3):` loop with `after = [(await _add(store, T0 - 100 + i, f"after{i}"))["id"] for i in range(3)]`.
  - Replace the assertion with `assert store._window_id_ceiling(T0 + 2) == after[-1]`.
- `test_plot_export_since_id.py`, 2 tests (the exports are named from their first line, and the sys row sat inside the id range). In the `client` fixture, before `yield c`:
  ```python
          # T0 is years behind the lifespan's own rows, so the store announces the stamp
          # inversion in a sys row after the first T0 row: let that be this one, ahead of
          # every id range a test exports.
          _add(c, ts=T0, raw="first row behind the daemon's own stamps")
  ```

## Edits in files I do not own

- **server.py (server batch), finding 4**, in `/purge`:
  - Pass the span's top: `deleted = await store.delete_before_ts(body.before_ts, max_id=hi_id)`.
  - When `n == 0` (`hi_id` is None), skip the delete. `max_id=None` means "no bound", which is the old chasing behaviour.
- **server.py (server batch), finding 9**, in `_ExportJob.on_open`, after `self._conn = conn`, add:
  ```python
  conn.set_progress_handler(lambda: self._abandoned, 1000)
  ```
  - Test: abandon inside `on_open` before any SQL, then assert the build raises and the temp files are gone.
- **cli.py / cli_daemonctl.py (CLI batch), finding 2**, in the `daemon start` readiness loop (`cli.py` about 2613-2622):
  - When the deadline passes and `proc.poll() is None`, read the err file from `err_start`.
  - If it holds a line containing `building index` with no later line containing `built index`, do not abandon:
    - print once to stderr: `mcuscoped is building index <names> on an older capture (one time); waiting. Ctrl-C leaves it building; mcu daemon stop abandons it`;
    - keep polling.
  - Once the `built index` line appears, restart the normal `--timeout` countdown, so a daemon that wedges later still fails in bounded time.
  - Match on those substrings from `err_start`, never "the log ends in the notice": other warnings (auto_vacuum, journal mode) can follow it.
  - Do not import `store.py` into the CLI (it is heavy). Duplicate the two literals in `cli_daemonctl.py`, plus a test asserting they equal `store.INDEX_BUILD_NOTICE` / `store.INDEX_BUILT_NOTICE`.
  - Under `--json` the notice goes to stderr only.
  - Driven test: a fake child whose err file shows the build notice past the deadline is not terminated, and one past the deadline without it is.
- **docs/ARCHITECTURE.md**:
  - Line 25: replace the sentence with `A failed commit leaves its ids spent (a gap), never rewound: ids above SQL's MAX(id) may already be in clients' hands after a purge of the newest rows. The row-by-row fallback resyncs \`_next_id\` from SQL, only ever upwards.`
  - Line 32: `A delete subtracts its points from the summary; a start, a delete that takes a channel's newest sample while older ones remain, or a delete while a rebuild is in flight marks it dirty.`
  - Add under store.py: `The writer announces rows committing more than WINDOW_TS_SLACK_S behind the newest stored ts with a sys row per episode (start, and end with a count): past the slack, time windows can miss rows.`

## SPEC wording (not edited)

- **3.2 item 2** (line 423): `Above about 200 lines/s (averaged over about half a second) commits are held to one per 100 ms, since each commit rewrites every index's newest page; a row then reaches readers up to 100 ms after it arrives.`
- **3.4 `/purge`** (finding 7): delete line 849 (`\`before_ts\` selects the rows stamped before it by their \`ts\`, not an id range: ...`) and keep line 850. Then add after it:
  - `A \`before_ts\` purge deletes only the rows its span counted: a line committed while the purge runs is kept even when stamped before \`before_ts\`, so \`deleted\` equals the dry run's count.`
- **Time bounds** (finding 3), replacing lines 928-929:
  - `\`ts\` is stamped on arrival and the id at commit, after the line has queued, so ids and times can disagree. The lower bound holds while that disagreement stays under 10 s.`
  - `At the writer's full rate a saturated port queues about 1.4 s of lines. The 10 s bound breaks only in these cases: a loop stall over 10 s while a port keeps sending, a writer draining a port below 1000 lines/s for a sustained period (a slow disk), or a backwards clock step over 10 s.`
  - `Beyond it, a row whose time is inside a \`since_ts\`/\`last_ms\` window can fall outside it. Such rows are announced in the capture:`
    - `a \`sys\` row \`storage: rows are committing up to <s> s behind newer timestamps, past the 10 s window slack; ...\` when the first one commits;`
    - `and \`storage: rows are back in time order; <n> committed up to <s> s behind newer timestamps\` once a batch commits without one.`
- **Schema** (finding 8), after line 987: `CREATE INDEX idx_lines_port_chan_id ON lines(port, chan, id);   -- port with chan`.
- **Startup** (finding 2), a sentence in the lifecycle or 3.2 text:
  - `Opening an older capture builds any index it lacks before the daemon answers (about 2.5 s per million lines), logged as \`building index <names>\` and \`built index <names> in <s> s\`.`
  - `\`mcu daemon start\` waits for the build rather than timing out.` Add this line only once the CLI edit lands.

## Proposed CHANGELOG lines

- Commit coalescing measures the ingest rate over about half a second, so a slow stream plus `mcu cmd` traffic is no longer held up to 100 ms per row.
- Rows committing more than 10 s out of time order (a long loop stall, a slow disk, a backwards clock step) are announced in the capture with a `sys` row, and the count follows when order returns.
- `purge before_ts` no longer deletes lines captured while the purge runs (needs the server edit).
- A session export abandoned before its first statement stops instead of running to the end (needs the server edit).
- The plot channel summary rebuild reads one snapshot, so a purge during it can no longer give wrong counts or fail the read.
- The daemon logs which indexes it is building on an older capture, and when it has finished.

## Verified, and how

- The 16 mutants in `mutants.py`, run from the copy with the probe test: all KILLED (`mutants.log`).
- Each owned test file run alone from the real tree: all pass (list below).
- `uv run python -m ruff check .` in `host/`: clean.
- Finding 9's server edit: `abandon_probe.py` against the copy, with and without the edit.
- Every Python test file in `host/tests/` run alone against the shared tree (`perfile.log`, 118 files; the JS and firmware wrappers were skipped). All pass except the three files in "Tests outside my files that fail", whose edits pass in the copy.
  - The shared tree also holds other batches' uncommitted work, so this is not a clean run of my diff alone.

## Not done

- server.py, the CLI and ARCHITECTURE.md edits above (not my files).
- SPEC and CHANGELOG (not to be edited by me).
- No live `mcu daemon start` against a large capture, since the CLI edit is not in.
- The widened window floor (finding 3's optional part).

## Doubts

- Finding 3 checks the stamps before the insert. After a failed commit, `_top_ts` keeps the lost rows' stamps.
  - Those rows were stamped by the same clock moments earlier, so a false "late" needs them to be 10 s ahead of what follows.
  - A lost episode row does not re-announce.
- Owner should pick: finding 3's sys rows take ids, so any test that mixes a fixed epoch with rows the store stamps now gets an extra row.
  - Three external files broke this way, plus two of mine, and future tests will hit the same trap.
  - The alternative is a `/status` counter (`late_rows`) instead of the rows. It causes no test churn, but needs a server.py field and a SPEC 3.4 `/status` line, and it loses where in the capture the inversion happened.
  - I kept the sys rows because they sit beside the rows they describe.
- Finding 1: the averaged rate needs about 0.1 s of a 1000 lines/s burst before holds start, so a short burst commits a few more times than before. That is by design, and it is small.
