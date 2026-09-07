# Daemon leg: store, server, protocol, serial_link, pjstream (diff e573e93..6f1441d)

Run in-session (the agent launch was classifier-blocked twice). Mutations ran in a worktree at 6f1441d; drives in `/tmp/review-2026-09-07/drive_daemon.py` and `mut_daemon.py`.

## Findings

| id | sev | where | claim | verified |
|---|---|---|---|---|
| D1 | MED (test quality, class 27/29) | tests/test_store_fastpaths.py:96 `slow_scan` | The race test runs the real scan first and blocks afterwards, so the rows written during the block are never inside the scan's window; the `line_id <= high` bound is unpinned. | Mutation "scan ignores high" survived the suite; a drive that blocks before the SQL and writes 5 rows meanwhile gives count 5 with the bound (exact) and would give 10 without it. |
| D2 | LOW (test coverage, class 29) | serial_link.py:906-907 | The `QueueFull` fallback to `submit_line` in `_submit_rx_line` is uncovered: no test drives a port against a full store queue. | Coverage of the diff hunks (below). |
| D3 | LOW (class 40, shutdown only) | store.py `_read_conn` | The epoch check and the use of the cached handle are not atomic against `stop()`: a worker between the two gets a closed connection (`ProgrammingError`, one request fails) and a handle opened after `_close_read_conns` ran is leaked. | Reasoned from the code; a few bytecodes wide, at shutdown only. |
| D4 | LOW | store.py `max_id` | After a delete of the top id the fast path answers `_next_id - 1` while SQL answers lower; every caller uses it as a range bound so nothing breaks, and session end ids come from the marker row, not `max_id`. | Driven: 352 vs 351 after `delete_range(top, top)`. |
| D5 | nit | store.py:766 | `_plot_dirty = True` on a failed commit is inert: `_note_plot` never ran for that batch. | Mutation removing it survived. |
| D6 | ruled out | store.py `_delete_lines` | Every delete dirties the summary, so at the size cap a rebuild follows each sweep. The size tick is 60 s, so at most one scan a minute, off the loop; before the change the scan ran on every poll. | `_SIZE_CHECK_S = 60`. |
| D7 | ruled out | store.py `_scan_plot_summary` | The join back on `(name, line_id)` fans out when one line carries one name twice; the reference SQL does the same. Both parsers refuse duplicate names (`!p 3 v=3 v=4` and `!pd s0 v v` parse to None), so the shape is unreachable from the wire. | Driven with injected tuples: SQL gives two rows, the summary one. |

Ruled out by driving: the cached read connection after a query aborted by the regex budget still sees rows committed afterwards on every worker (counts {50} across 8 queries) and `PRAGMA wal_checkpoint(TRUNCATE)` returns (0, 0, 0) with an empty WAL, so no reader holds a snapshot. The rebuild with rows committed while the scan waits matches SQL exactly. `submit_line_nowait` on a dead writer raises `StoreError` from `_write_req` before any put, as `submit_line` did. `normalize_line` only strips one terminator, so `line.split()` in the serial link and `normalize_line(raw).split()` in the parsers tokenize identically. `json.dumps` in the writer cannot raise on a row: every field is str, int, float or None, and lone surrogates escape under `ensure_ascii`.

## Sweep verdicts

- Class 1 (4 sites): `_rebuild_plot_summary` scans via `_offload` on match_executor (complies); `_plot_channels_from_summary` is O(channels) on the loop (complies); `_close_read_conns` closes at most four handles on the loop (complies); `_broadcast_batch` dumps JSON on the loop where the `/ws` handler did before (complies). `grep "run_in_executor(None"`: one comment line.
- Class 16 (3 loops): `_broadcast_batch` over rows, `_note_plot` over points, `_scan_plot_summary` over SQL rows: the items are internal, none can be bad (complies).
- Class 17 (1 site): `/plot/channels` fields come from committed batches and the SQL rebuild; the port on a merged row is the entry with the highest line id, as the SQL picks (complies, pinned by the summary-equals-SQL tests).
- Class 20 (2 statements): the rebuild scan plans as `SEARCH plot_points USING INDEX idx_plot_line (line_id<?)` plus a temp b-tree for the GROUP BY on a two-port capture with no `sqlite_stat1` (exempt: an aggregate, as `/plot/channels` was); `_max_id_sql` is `MAX(id)` on the rowid (complies).
- Class 21 (4 sites): `time.time()` appears only as a row's `ts` in the new tests (exempt shape).
- Class 27 (3 doubles): `slow_scan` blocks after the real scan (violates: D1); `_WRITE_QUEUE_MAX = 2` is the real queue at a small bound (complies); `NoWritable` mirrors a protocol class whose constructor never names the attribute (complies).
- Class 31 (1 field): `subscribe(as_json)` is read on the fan-out branch (complies).
- Class 36 (0 sites): no loop compares a schedule against now in the diff; the chunk loops are bounded by rows.
- Class 37 (3 sites): `_rebuild_plot_summary` re-checks `_plot_dirty` under `_plot_lock` and the writer's `_note_plot` lands in the fresh dict during the await (complies); `query_plot_channels_safe` reads the flag outside the lock and the rebuild re-checks (complies); `_delete_lines` sets the flag with no await (complies).
- Class 39 (0 sites): no new `create_task`.
- Class 40 (1 group): `_read_local`, `_read_conns`, `_read_epoch`: writes under `_read_conns_lock`; the worker's check-then-use is not atomic against `stop()` (D3).
- Class 44 (2 sites): `/purge` reads `max_id()` twice in one handler with no await between, now from the same sequence (complies); no new paged walk.
- Class 49 (0 sites): the daemon diff writes no files.

## Revert verification (mutations in the worktree)

| mutation | tests | failed |
|---|---|---|
| delete does not dirty the summary | fastpaths | yes |
| count never increments | fastpaths | yes |
| merge drops the scanned count | fastpaths | yes |
| commit failure does not resync `_next_id` | fastpaths | yes |
| commit failure does not dirty | all four files | no (D5, inert) |
| reader never cached | fastpaths | yes |
| stop leaves read conns open | fastpaths | yes |
| json subscribers get dicts | fastpaths | yes |
| max_id always SQL | all four files | no (a performance path; both answers agree while the top stands) |
| port filter ignored in batch fan-out | fastpaths | yes |
| nowait swallows QueueFull | fastpaths | yes |
| uvicorn guard removed | fastpaths | yes |
| feed_tokens learns nothing | protocol | yes |
| summary scan ignores `high` | all four files | no (D1) |

## Coverage of the diff hunks

`pytest test_store_fastpaths test_store_writer test_e2e test_plot test_protocol test_reconnect --cov`: protocol.py and server.py hunks fully covered. store.py 1071-1072, 1075: the `QueueEmpty` and `QueueFull` excepts in drop-oldest (dead by design, unreachable under the `full()` check on the loop). store.py 1960: the re-check inside `_plot_lock` (dead by design without a concurrent rebuild). store.py 2317, 2419: the chunk-loop yields (reached only by a delete larger than one chunk; the loops are pre-existing). serial_link.py 906-907: D2. pjstream.py 156, 161: the no-finite-value return and the reserved-alias rename (pre-existing branches, tuple form only).

## The two questions

Q1, least confident: the claim that insert, commit and `_note_plot` never interleave with a rebuild, which rests on the writer holding no await between them; re-read `_writer` and confirmed the only awaits are the queue get and the drain barrier, both before `_insert_batch`. Second: D3 is reasoned, not driven.
Q2, the gap: the summary's rebuild is exact but its cost model was not measured; nothing in the round timed `_scan_plot_summary` on a capture at the size cap with a busy plot stream, where a 60 s sweep now triggers it. And every `_offload` reader now shares a connection across requests, so a reader that ever left a cursor unexhausted would hold a snapshot for the worker's life; the drive shows none does today, and nothing pins that for the next reader added.
