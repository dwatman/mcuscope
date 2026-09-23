# Fix batch: store (2026-09-23)

Files changed: `host/mcuscope/store.py`, `docs/SPEC.md` (3.2, 3.4 time bounds and the `before_ts` line of `/purge`), ten new test files, and minimal edits to three existing test files (listed below).
Revert-verification: `~/tt-data/mcuscope-2026-09-23/fix-store/mut/mut.py` applies each mutation to a scratch copy of the package and runs the named tests against it (`--import-mode=append`, run from the scratch dir; a probe test confirmed the copy is what gets imported).
The baseline passed, and all 44 mutants were killed; the log is `mut/mut_run1.log`.
Two mutants survived the first run and were resolved: see HEALTH-1 and PERF-5.
At-scale numbers come from a copy of `perf/big.db` (6M lines; the original was not touched), in `fix-store/`.

## Per finding

### CAPTURE-1 (windows and `purge before_ts` when `ts` is out of id order)
- `store.py:1805` `_window_id_floor`: the floor is now one past the newest row stamped `WINDOW_TS_SLACK_S` (10 s, `store.py:183`) before the cutoff, and the exact `ts` term stays.
  - The ruling's literal reading ("seek at `cutoff - slack`", meaning the first row at or after it) is not exact. With nothing stamped inside the slack interval, a lower-id row stamped just after the cutoff still falls below the floor.
  - The newest-row-before form is exact under the stated bound: every lower id was stamped less than the slack after it, so before the cutoff.
  - Pinned by `test_store_time_window.py::test_a_row_stamped_after_the_floor_but_committed_first_is_kept`, which fails on the first-at-or-after seek (mutant `floor-old-seek`).
- Why 10 s: a line waits in its port's rx queue (`RX_QUEUE_MAX`) and then the write queue (`_WRITE_QUEUE_MAX`). Full, those two drain in about 1.4 s per port at the writer's ~15k lines/s. The reviewer saw 1.49 s at worst.
- Cost of the slack on big.db: 0.2 ms per 1000 rows read (1.9 ms for 10 s at 1000 lines/s). It is paid only when the window holds fewer than `limit` rows, so at most about 30 ms at the writer's full rate (`slack.py`).
- `strict` is gone, since the slack makes the boundary comparison irrelevant to the bound.
- `store.py:2785` `delete_before_ts`: chunked delete by `ts` (the retention chunk).
- `store.py:2796` `before_ts_span` / `before_ts_span_safe`: `(count, min id, max id)` of the same rows, so the dry-run count equals the delete.
- `last_id_before_ts` is kept, with its docstring corrected, only because server.py still calls it (see Not done).
- Docstrings that claimed ts rises with id have been fixed: `_window_id_floor`, `_delete_expired_chunk`, `last_id_before_ts`.
- Tests: all of `tests/test_store_time_window.py`, which includes randomised inversions at every cutoff (`since_ts`, `floor_ts`, `count_lines`), the purge span/delete, and multi-chunk purges.
- Mutants killed: `floor-old-seek`, `slack-zero`, `purge-by-id`, `span-by-id`.

### CAPTURE-2 (rollover gap)
- `store.py:1371` `start_session`: when it closes a session, the new one starts at `end_id + 1`. The drain now runs only when nothing was running.
- `store.py:1416` `stop_session(reopen_auto=None)`: optionally reopens an automatic session at `end_id + 1` under the same lock hold, for `POST /sessions/stop` (server half in Not done).
- Tests: `test_store_session_bounds.py::test_a_rollover_leaves_no_line_between_the_two_sessions` and `::test_stop_with_reopen_abuts_the_automatic_session` assert no line is covered by zero or two sessions; `::test_a_start_with_nothing_running_still_excludes_the_queued_backlog`.
- Mutants killed: `rollover-drains`, `reopen-drains`, `reopen-off`.

### CAPTURE-3 + HEALTH-1 (id sequence rewinds)
- `store.py:1041` `_resync_next_id`: `max(_next_id, SQL max + 1)`, never down. A failed commit's ids stay spent.
- `store.py:1020` `_insert_individually` resyncs first, then binds explicit ids from the sequence (`_insert(line_id, ...)`, `store.py:1053`).
- The mutation leg showed the session-ref term in the runtime resync to be redundant (`resync-no-session` survived): sessions take their ids from this sequence. It was deleted; `start()` still seeds past session refs.
- Tests: `test_store_id_sequence.py` (a failed commit after a tail purge; the HEALTH-1 fallback probe; the fallback after a tail purge).
- Mutants killed: `resync-lowers`, `fallback-no-resync`, `insert-sqlite-id`.

### CAPTURE-5 (startup trim writes no sys row)
- `store.py:2893` `_sweep_size_reported` holds the sweep and its sys row, and is used by both `_initial_sweep` (`store.py:696`) and `sweep_tick`.
- Tests: `test_store_startup_trim.py` (the row with the exact count; none when nothing is trimmed).
- Mutant killed: `startup-trim-silent`.

### LIFECYCLE-3 (crashed run's automatic session)
- `store.py:667` `_close_crashed_auto_session`, called in `start()` before the writer runs. It sets `end_id`/`ended_ts` from the newest row and writes no end marker, since one written now would carry the restart's time.
  - It is dropped when it carried no device traffic.
  - A named session is left open, to be resumed.
- Tests: `test_store_session_bounds.py`, the three crash tests.
- Mutants killed: `crash-close-off`, `crash-ts-now`, `crash-keeps-empty`, `crash-closes-named`.
- The lifespan then needs one edit (Not done).

### PERF-1 (`/lines?port=<quiet>&chan=<busy>`)
- `store.py:70` adds `idx_lines_port_chan_id ON lines(port, chan, id)`. `_lines_index` (`store.py:147`) adds `INDEXED BY` whenever `port` and `chan` are both given, in `query_lines` and `count_lines`. The `+port` de-optimisation (`unindexed_port`) is gone.
  - Without the hint, a `chan IN (...)` list plans on `idx_lines_port_id`: busy port with rare channels took 4.4 s at 6M lines. With it, every shape tried is 1 ms or less (`perf1b.log`).
- Build cost: 15.2 s once at 6M lines, at startup, logged as a warning before `executescript(SCHEMA)`.
- Re-explain of every read with the reviewer's `plans.py` on big.db (`plans_new.log`): all inline `/lines` shapes are at most 3.3 ms, including the reviewer's 7.7-14 s port+chan cases (now 0.3-0.7 ms). `count_lines port=board chan=debug` 431 ms (was 3.75 s).
- `has_port_rows` (`store.py:1915`) is one `idx_lines_port_id` seek (server.py already calls it).
- Tests: `test_store_lines_plan.py`, 25 parametrised cases (5 shapes x none/id_to/since_id/last_ms/since_ts, both orders, plus `count_lines`), the single-column plans, result rows, `has_port_rows`, and the older-capture build notice.
- Mutants killed: `hint-off`, `index-drop`, `has-port-rows-any`, `index-log-off`.

### PERF-2 (store half) and PERF-8 (plot export)
- `store.py:2554` `iter_plot_export` pages on `line_id` over `idx_plot_line` (CROSS JOIN pinned).
  - Each page is fetched whole before yielding.
  - A line that runs past the page is held back to the next page (the limit doubles if one line fills a page).
  - Names are sorted within a line in Python.
  - The window is resolved once, and the top is frozen at the newest point at start.
- `PRAGMA journal_size_limit=64 MiB` on the writer connection (`store.py:629`).
- big.db, 3 names: first row in 96 ms (was 11.3 s offline, 30.6 s in the daemon), 3.6M rows in 36.9 s, no temp b-tree in the plan.
- Tests: `test_store_plot_reads.py`:
  - `::test_an_export_holds_no_read_snapshot_between_pages`: a parked generator, then `wal_checkpoint(PASSIVE)` must checkpoint every frame.
  - `::test_export_rows_are_ordered_across_pages`
  - `::test_the_export_walks_line_order_without_sorting_the_selection`
  - `::test_the_wal_is_truncated_after_a_checkpoint`
- The tests batch's `test_plot_export_decode.py::test_a_selection_past_the_old_row_cap_streams` patches `_EXPORT_CHUNK`, which this paging uses. It passes, and fails when the export stops after one page (checked by mutation).
- Mutants killed: `export-cursor-open`, `export-no-holdback`, `export-no-freeze`, `export-no-name-sort`, `export-sorted-sql`, `journal-limit-off`.

### PERF-3 (store half)
- `_make_regexp` records `window_spent`: set when the budget is gone, or when the budget is what shortened the per-call timeout.
- `_budget_error` (`store.py:485`) words each case:
  - budget: `match scan used its 30 s budget before covering the window: the window is too large, narrow it (session, last_ms, since_id)`
  - per call: `match pattern exceeded the matching time budget; simplify the regex`
- Same `MatchBudgetExceeded` type, so the server's 400 mapping is unchanged. Both messages contain "budget", which existing tests grep for.
- Tests: `test_store_match_budget.py` (file and memory captures, both paths).
- Mutants killed: `budget-window-flag`, `budget-call-flag`.

### PERF-5 (plot summary rebuilt after every delete)
- `_delete_lines` (`store.py:1573`) now takes the chunk's id subselect.
  - `_plot_points_in` aggregates the chunk's plot points per (port, name) before the delete.
  - `_forget_plot_points` subtracts them, dropping a key at zero. It marks dirty only when a channel's newest sample went while older ones remain, or when a rebuild is in flight.
- `_scan_plot_summary` (`store.py:2270`) rebuilds cheaply:
  - per-name totals come from the covering `idx_plot_name_line`;
  - only the non-busiest ports are joined, driven from their own lines;
  - the busiest port gets the remainder by subtraction.
  - big.db: 2.4 s quiet, 4.1 s under load, against 15-22 s; the result is identical to the old SQL (`plotproto.py`).
- Tests: `test_store_plot_summary.py`:
  - the rebuild equals the writer's own folding, including a name twice in one line;
  - no whole-table walk of plot_points joins lines, and the busiest port's lines are never walked;
  - deletes subtract with zero scans;
  - deleting a newest point rescans once;
  - a delete during an in-flight rebuild is not lost.
- The last test was added after mutant `summary-subtract-during-rebuild` survived the first run.
- Mutants killed: `summary-always-dirty`, `summary-newest-kept`, `summary-zero-kept`, `summary-subtract-during-rebuild`, `rebuild-first-dup`, `rebuild-joins-busiest`.

### PERF-6 (`/plot/series` numbers the whole history)
- `store.py:2364`: an inner `ORDER BY pp.line_id DESC LIMIT ?` (CROSS JOIN), with decimation numbering only that set.
- big.db: `series tri` 43 ms (was 8.8 s); `decimate=10 limit=100000` 1.35 s (was 7.8 s).
- Tests: `test_store_plot_reads.py::test_series_work_is_bounded_by_the_limit_not_the_history`, which counts VM steps via a progress handler (under 2000 steps for 10 of 3000 points) and compares rows with the pre-fix SQL for four decimate/limit pairs.
- Mutant killed: `series-no-inner-limit`.

### PERF-7 (reclaim stalls)
- `store.py:186` `_reclaim_pages` runs 64-page steps and starts no step past `_RECLAIM_BUDGET_S` (20 ms). `_VACUUM_PAGES` stays the per-call page cap.
- Re-measured after a 1.5M-line trim of big.db (`reclaim_scale.log`):
  - old: median 108 ms, max 795 ms per call;
  - new: median 22 ms, max 39 ms;
  - the same ~11k pages/s of work.
- A redundant freelist-empty early return was deleted (no test could tell it apart).
- Tests: `test_store_reclaim_budget.py`.
- Mutant killed: `reclaim-no-deadline`.

### PERF-9 (commit coalescing)
- `_commit_hold` (`store.py:253`) implements the ruling: above 200 lines/s, a commit waits until 100 ms after the previous one. It never holds when a full batch is waiting, so throughput is not capped.
- The writer (`store.py:859`) adds one thing the ruling did not name: a hold that collected nothing stops holds for 1 s.
  - Without it, a caller awaiting each row in turn (`add_line` in a loop, per-command tx rows) measured as high rate and waited up to 100 ms for rows that never came, every other line.
  - The new store test files took 28 s instead of 5.5 s until this was added.
- Tests: `test_store_writer_commits.py`:
  - the policy table;
  - a 1000 lines/s stream commits at most once per interval (+3);
  - a slow stream is never held;
  - an awaiting caller is held at most once per backoff period.
- Mutants killed: `hold-off`, `backoff-off`, `hold-full-batch`, `hold-any-rate`.

### HEALTH-15 B07, HEALTH-20 SRC-7, HEALTH-25, HEALTH-26 (store comments)
- B07: `test_store_plot_summary.py::test_concurrent_reads_of_a_dirty_summary_share_one_rebuild` (mutant `b07-recheck-off` killed).
- SRC-7: `_open_read_conn` no longer registers REGEXP. Test: `test_store_match_budget.py::test_a_worker_read_connection_carries_no_regexp` (mutant `readconn-regexp` killed).
- HEALTH-25: `query_lines` passes `since_ts` to `_window_terms`, and the restated block is deleted (mutant `since_ts-fold-off` killed).
- HEALTH-26: rewrote the module docstring, the `match_executor` docstring and the `first_export_line_id` docstring. The `start()` REGEXP comment no longer names `_open_read_conn`.

### CLI-5 (store half, from the cli batch): CAN rows name their port
- `query_can_frames` selects `l.port` and returns it as `port` in every row, so it also reaches `/can/frames` JSON and `iter_can_export`. The CSV writer names its columns, so the CSV is unchanged.
- Plan unchanged: `idx_can_id_line` (or the `cf` key) outermost, then `lines` by primary key, no temp b-tree.
- Test: `test_store_can_frames_port.py` covers two ports sharing bus 1 and id 0x100, `port=` filtering, the export rows, and the plan. It fails with the field set to None.
- 249 passed across it, `test_export_lines_can.py`, `test_server_exports.py`, `test_server_scope.py`, `test_e2e.py` and the store files.

## Existing tests edited
- `test_store_fastpaths.py::test_summary_is_not_polluted_by_a_batch_whose_commit_failed`: the id after a failed commit is 3, not 2 (the sequence never moves down).
- `test_store_fastpaths.py::test_query_plot_channels_safe_does_not_scan_between_deletes`: deleting an old point is now 0 scans (was 1), and the count is asserted.
- `test_hardening.py`:
  - imports `WINDOW_TS_SLACK_S`;
  - `test_lines_port_filter_seeks_rather_than_scans` and `test_since_ts_seeks_by_id_rather_than_scanning_the_table`: port+chan expects `idx_lines_port_chan_id`; the anchor SQL is the new seek; the empty-window floor is asserted at `cut + slack + 1`;
  - `test_since_ts_keeps_its_strictly_greater_boundary`: the floor is `<= id of l0`;
  - `test_the_page_reclaim_stays_bounded_per_call` and `test_the_reclaim_does_not_lean_on_execute_stepping_the_pragma`: the time budget is patched off, so they pin the page bound alone;
  - `test_export_bound_by_id_to_reanchors_its_last_ms_window`: the export seek is `idx_plot_line`.
- `test_daemon_r2026_09_12_store.py::test_since_ts_excludes_its_own_instant_where_the_id_floor_cannot`: dropped `strict=True`.
- `test_plot_export_decode.py`: no edit needed (checked, see PERF-2).

## SPEC edits
- 3.2 item 2: above about 200 lines/s commits are held to one per 100 ms, so a row reaches readers up to 100 ms after it arrives.
- 3.2 item 5: the size cap is checked "at startup and once a minute".
- 3.2 item 6: the id sequence only moves up; a failed commit leaves a gap.
- 3.4 `/purge`: `before_ts` selects by time, not as an id range; `id_from`/`id_to` are the lowest and highest id among those rows.
- 3.4 time bounds: the lower bound is exact while no row is stamped more than 10 s after a higher-id row (why, and what happens beyond it).

## Changelog
- `/lines`, `mcu lines` and exports filtered by both port and channel no longer stall the daemon (and drop captured lines) on large captures. The first start on an existing capture builds one index, about 2.5 s per million lines.
- `since_ts` / `last_ms` windows no longer miss lines when two ports or a marker commit out of stamp order.
- `purge before_ts` deletes exactly the lines older than the cutoff, and its dry run counts the same rows.
- Session rollover leaves no lines outside every session.
- A restarted daemon closes a crashed run's automatic session where that run ended.
- Line ids never go backwards after a purge followed by a failed write.
- The size cap's startup trim records a `sys` row.
- An aborted `/plot/export` no longer pins the WAL. The first byte of a whole plot export comes in milliseconds, not tens of seconds, with no temp-file spill.
- `/plot/channels` after a start or a purge answers in a few seconds rather than 20; retention and size trims no longer trigger a rebuild.
- `/plot/series` with no window is 200x faster on long histories.
- The per-minute page reclaim no longer stalls the daemon for up to 2.5 s.
- A regex scan stopped by the window budget says to narrow the window rather than simplify the regex.
- Commits are coalesced above about 200 lines/s, cutting WAL writes.
- `/can/frames` rows carry the `port` each frame came from.

## Not done
- server.py, `POST /purge` `before_ts` branch (currently `lo = 1; last = store.last_id_before_ts(...)` then `count_lines_safe` / `delete_range(lo, hi)`). Replace it with:
  ```python
  n, lo, hi = await store.before_ts_span_safe(body.before_ts)
  if body.dry_run or n == 0:
      return {"deleted": n, "id_from": lo, "id_to": hi, "dry_run": body.dry_run}
  deleted = await store.delete_before_ts(body.before_ts)
  return {"deleted": deleted, "id_from": lo, "id_to": hi, "dry_run": False}
  ```
  Then delete `Store.last_id_before_ts` (store.py:1988).
- server.py, `POST /sessions/stop`: replace `session = await store.stop_session()` plus the following `start_session(auto_session_name(), auto=True)` with
  `session = await store.stop_session(reopen_auto=auto_session_name() if request.app.state.config.storage.auto_session else None)`.
  Until then that path keeps the CAPTURE-2 gap (the drain path).
- server.py lifespan (around :480-495):
  - `elif open_session is not None: await store.stop_session()` is unreachable now, because `Store.start()` closes a crashed automatic session. Delete it.
  - The comment "start_session below closes a stale one" should read "Store.start() has already closed a crashed run's automatic session".
- ARCHITECTURE.md store bullet (tests batch's file):
  - "Any delete marks it dirty and the next read rebuilds it ... `query_plot_channels` stays as the SQL form the rebuild ... compare against" becomes: deletes subtract from the summary; only start, or deleting a channel's newest sample, marks it dirty; `query_plot_channels` is a test-only SQL form.
  - "A failed commit also resyncs `_next_id` from SQL" becomes "only moves `_next_id` up".
- SPEC 3.4 "Automatic sessions" (not in my sections): "A daemon that starts with an *automatic* session left open (a crash) closes it" could add "at its newest row's id and time, with no end marker".
- PERF-3, CLI half (the CLI timeout above the server budget) and PERF-2, server half (closing the generator on disconnect) belong to the cli and server batches; not touched.
- Owner should pick: a reclaim call now hands back about 256 pages instead of 2000, so after a large trim the freelist drains about 8x slower in ticks (77k pages: about 5 h instead of 40 min), at the same work per second.
  - Recommended: accept it, since freed pages are reused by new data anyway.
  - The alternative is several budgeted calls per tick with a loop yield between them.
- Still slow off the loop (not in this batch's rulings): `/can/frames?port=aux` 1.4 s, `/plot/series?name=tri&port=aux` 1.7 s, `first_export_line_id` with `port=aux` 2.2 s, `plot_streams` whole capture 39 s (`plans_new.log`).

## Doubts
- Least sure: that 10 s bounds real inversions.
  - The 1.4 s queue figure assumes the writer's full rate. A slower disk, more ports, or any loop stall over ~10 s (PERF-1/7 were the known ones) breaks exactness silently.
  - Nothing reports an inversion larger than the slack.
- The hold backoff was designed from the test-suite symptom, not measured on a live daemon: no `mcuscoped` was started in this batch.
  - A mixed load (a stream plus awaited `cmd` tx rows) gives the awaited rows up to 100 ms extra latency, which I did not measure.
- The rebuild's subtraction was measured with one dominant port. Two equally busy ports join the smaller one per point (not measured).
- The one-time index build blocks startup before ports attach (15 s at 6M lines, about 2 min at 50M). Not measured on Windows or a slow disk.
- Not run: the whole suite, Windows, and a live daemon.
