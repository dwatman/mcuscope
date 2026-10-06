# Fix batch store4, 2026-10-05

Files: `host/mcuscope/store.py`, store tests, SPEC 3.2 and the 3.4 purge paragraph.
All seven violations are fixed, plus the coordinator's fastpaths race.
Revert-verification: `~/tt-data/mcuscope-2026-10-05/fix4-store/mutate.py` applies 22 mutations one at a time, each against its pinning test file. All 22 are CAUGHT (log: `mutate.log` beside it), and store.py was restored and confirmed byte-identical with `cmp`.

## Per finding

**V94-1** (size sweep cut short)
- `_trim_oldest` counts each committed chunk into `lines_trimmed` and `_trim_unannounced`.
- `_sweep_size_reported` writes the sys row on every exit through `_sweep_row`. On a failure or cancel the row ends `; the sweep stopped early: <reason>` (`database is locked`, or `the daemon stopped`).
- `_sweep_row` queues without suspending, so it is safe on the cancel path. On `QueueFull` or a dead writer it keeps the count pending, and the next sweep's row folds it in. It logs the row at warning level.
- `_trim_cap` records the cap the lines were trimmed under, so a carried row names that cap and not the current one.
- Pinned by `tests/test_store_sweep_lock.py`:
  - `test_a_size_sweep_cut_short_by_a_lock_counts_records_and_joins_the_episode`
  - `test_a_stop_mid_size_sweep_records_what_it_had_trimmed`
  - `test_a_sweep_row_that_cannot_be_queued_is_folded_into_the_next`
- Mutations M1, M2, M14, M14b, M15 and M16 were all caught.

**V94-2** (age sweep cut short)
- `_sweep_retention_locked` counts per chunk into `lines_expired` and `_expire_unannounced`; the `finally` is gone.
- `_sweep_retention_reported` announces on every exit, as the size sweep does (`_expiry_row`).
- Pinned by `test_an_age_sweep_cut_short_by_a_lock_records_its_count_and_retries_next_tick`. M3 and M4 were caught.

**V94-3** (purge cut short)
- `_delete_chunks` rolls back the refused chunk and logs `storage: purge stopped after deleting <n> lines: <reason>`.
- It raises `StoreError` with the same text, which the server's catch-all answers as 500 `{"error": ...}`.
- A cancel is logged and stays a `CancelledError`.
- Pinned by `test_a_purge_cut_short_names_what_it_had_deleted` and `test_a_purge_stopped_by_a_cancel_logs_its_count_and_stays_cancelled`. M11, M12 and M13 were caught.

**V103-1** (lock seen by the sweeps)
- `sweep_tick` and `_initial_sweep` pass a busy error to `_sweep_refused`. It rolls back the failed statement's open transaction and opens the lock episode through `_lock_held`, which is the writer's code factored out: one log line and `db_locked_since`. No `log.error` follows.
- An age sweep that was due is kept in `_age_sweep_due` and runs on the next tick. A refused startup sweep runs on the first tick.
- `_close_lock_episode` runs at the start of each tick while an episode is open:
  - It tries `BEGIN IMMEDIATE`, which waits at most the writer's 5 ms on the loop (class 1 holds). If that works it rolls back and queues the existing clearing row.
  - This closes an episode on a quiet board where the writer never commits again.
  - It skips when the queue is full: the row resets `db_locked_since`, so a row that could not be queued would lose the episode, and the writer closes it instead.
- Pinned by:
  - `test_a_lock_episode_a_sweep_opened_is_closed_by_a_tick_with_nothing_to_write`
  - `test_the_episode_is_left_to_the_writer_when_its_row_cannot_be_queued`
  - `test_a_startup_age_sweep_refused_by_a_lock_runs_on_the_first_tick`
  - the size and age tests above, which check there is no error log, `db_locked_since`, and `not in_transaction`
- M5 through M10b were caught.

**V92-3** (identity taken after the connect)
- `_open_writer` stats the path before the connect and again after `_setup_writer`. If they differ it closes the connection and raises `CaptureUnreadable("capture <path> was replaced while it was being opened: start the daemon again")`, which the daemon's short-error filter prints without a traceback.
- When the file does not exist before the connect (no POSIX pre-create) there is nothing to compare.
- Pinned by `tests/test_store_open_identity.py`. M17 was caught.

**V95-1** (typed names in the plot summary)
- `_keep_recent_adhoc` is renamed `_keep_recent_names`. It keeps the 256 most recent names per port, typed and ad-hoc alike.
- `_note_plot` prunes on every new key, not only ad-hoc ones. The rebuild uses the same function.
- The ingest half was already in place: `serial_link.py:993-996` counts every point name of `!p`/`!pd`/`!ps` against `ADHOC_NAMES_MAX`, counted in `plot_name_refused` and announced. No link change is needed.
- Pinned by `tests/test_store_plot_summary_name_cap.py`, which checks the live summary and a rebuild. M18 and M19 were caught.

**V104-2** (floor without its ceiling)
- `_window_terms` raises `ValueError("floor_ts needs the ceil_ts it was measured from")` when it gets `floor_ts` without `ceil_ts` and no `last_ms`. I used a `ValueError` rather than an `assert` so it survives `-O`.
- Production callers (`server._resolve_window`) pass both. The existing tests did not; see below.
- Pinned by `tests/test_store_window_terms_anchor.py`. M20 was caught.

**Coordinator item: fastpaths race**
- `test_batch_fanout_serialises_once_and_filters_per_row` and `test_batch_fanout_drop_oldest_counts_per_row` now stamp rows `time.time()` instead of `1.0`, so the startup age sweep (cutoff now minus 10 days) cannot reach them. Assertions are unchanged.
- I ran them 5 times with `-p randomly` and all passed. I could not reproduce the original failure on 3.13 (8 runs of the old version passed); the fix holds by construction.

## Existing tests edited

- `tests/test_store_fastpaths.py`, the two fanout tests: `ts=1.0` became `ts=time.time()` (the race above).
- `tests/test_store_plot_summary_cap.py::test_the_summary_keeps_the_newest_adhoc_names_across_a_restart`: it pinned typed names as exempt from the cap.
  - Now 256 names in total, with the 5 typed ones included.
  - After a run of 256 new ad-hoc names, only those remain.
- These tests passed `floor_ts` without `ceil_ts`. Each now passes the ceiling the removed fallback derived (`store._window_anchor(<same bound>)`), which is what `_resolve_window` does:
  - `tests/test_store_stamp_order.py::test_an_inversion_inside_the_slack_keeps_every_row_and_says_nothing`
  - `tests/test_store_time_window.py::test_a_row_stamped_after_the_floor_but_committed_first_is_kept`
  - `tests/test_store_time_window.py::test_every_cutoff_matches_the_exact_ts_filter_under_inversions`
  - `tests/test_store_window_ceiling.py`: `test_a_now_anchored_window_excludes_rows_stamped_ahead`, `test_a_frozen_count_and_its_rows_share_one_ceiling`, and `test_a_frozen_window_keeps_the_ceiling_its_floor_was_measured_from` (6 readers).
    - In the last one, the control "the freeze alone lets them in" now passes the freeze-derived ceiling explicitly, and also asserts that the freeze alone is refused.

## SPEC edits

- 3.2 retention: both sweeps count per committed chunk. A sweep cut short records what it deleted (`; the sweep stopped early: <reason>`), and a row that cannot be queued folds into the next.
- 3.2 "Another process on the capture", write-lock bullet: the sweeps join the lock episode. A refused sweep is skipped without an error, a put-off age sweep runs at the next minute's check, and a check that finds the lock gone closes the episode.
- 3.2 identity bullet: a file replaced while the daemon opens it refuses the start.
- 3.4 `POST /purge`: a purge cut short answers 500 `purge stopped after deleting <n> lines: <reason>`, and the deleted chunks stay deleted.

## Guide wording

For `AI_GUIDE`, if the cli batch covers storage rows: "A `storage: trimmed`/`expired` row ending `the sweep stopped early: <reason>` counts the lines a sweep deleted before another process's lock or a stop cut it short. A failed `mcu purge` names how many lines it had already deleted."

## Changelog

- Retention and size sweeps cut short by another process's write lock, or by a stop, now count and record the lines they had already deleted.
- A lock held by another process no longer logs `retention sweep failed` every minute. The sweep joins the lock episode (`db_locked_since`, one sys row when it clears), and a put-off age sweep runs on the next check.
- A purge that fails partway reports how many lines it had already deleted.
- A capture file replaced while the daemon is opening it now refuses the start.
- The plot channel summary bounds typed (`!pd`/`!ps`) names per port as it does ad-hoc ones.

## Not done

- SPEC 3.4 `/status` field text (around line 736) still says `plot_name_refused` counts "the `!p` lines refused past the ad-hoc name cap". SPEC 2.5 and the decoder count `!ps` samples too.
  - Owner: server or link batch.
  - Change: "the plot lines (`!p`, `!ps`) refused past the per-port plot name cap (2.5)".

## Verification

- `uv run python -m ruff check .` is clean.
- 60 files (every `tests/test_store_*.py` plus the server, e2e, flow, assert and cli files that mention purge, retention, `floor_ts` or plot channels), random order: 945 passed, 1 skipped.
- `tests/test_store_sweep_lock.py` passed 3 more times with `-p randomly`.
- Not run: the whole suite (per the brief), and the Python 3.10 floor.
- No daemon was started.

## Doubts

- `_close_lock_episode` takes the write lock on a tick while an episode is open, including one the writer opened, so it can close the writer's episode before the writer's own retry does.
  - I reasoned that this is single-row and race-free on the loop (the reset and the enqueue are synchronous), but I did not test it against a writer mid-backoff.
- V104-2 broke 11 existing test cases, against the sweep's statement that "nothing reaches it today". Production does not reach it, but tests used `floor_ts` alone as a convenience.
  - If the owner prefers the fallback for direct store callers, the alternative is to make floor and ceiling one argument.
- I did not check the server-level 500 for a partial purge end to end. It relies on `_unhandled_error` formatting `str(exc)` and on `capture_error` being `None`.
