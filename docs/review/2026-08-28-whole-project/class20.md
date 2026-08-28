# Class 20 sweep: non-sargable bound on a hot query

Repo `/home/daniel/git/mcuscope`, HEAD `fd76735`.
Sweep as written in `docs/REVIEW.md` "### 20": `EXPLAIN QUERY PLAN` every statement reachable from a `server.py` handler.

## Probe conditions

Two probe databases built by `/tmp/claude-1000/review-r2/probe20.py`, `probe20b.py`, `probe20c.py`; raw plans in `run.log`, `run2.log`.

- `sqlite_stat1 present: 0`, `distinct ports: 2`, SQLite 3.45.1 (the shipped condition; the store never runs `ANALYZE`).
- Four rows over two ports, one CAN child and one plot child each, plus one session. A two-row capture reproduces the plan choice, per the registry.
- Every statement is taken off the connection's trace callback (the same mechanism as `_captured_plan` in `test_hardening.py`), not hand-copied, so the plan is the daemon's own statement with parameters substituted. `iter_plot_export` streams on a private connection, so that connection is traced instead. `export_session_db` runs on its own connection with the capture `ATTACH`ed, so `sqlite3.connect` is wrapped for that call.

## Site count

`store.py` contains **47 distinct SQL statement sites** (AST enumeration of every SQL string literal and f-string, docstrings excluded). `server.py` contains no SQL of its own; every statement the daemon issues comes from `store.py`.

Of the 47: **37 are reachable from a `server.py` handler**; 10 are startup-only or background-sweep-only and are marked as such below (they are still ruled, because they run on the event loop).

Verdicts: **1 violates (confirmed)**, 36 complies, 10 exempt.

## Verdict list: `lines` reads

| # | Site | Verdict |
|---|------|---------|
| 1 | store.py:1405 `query_lines` SELECT | complies |
| 2 | store.py:1446 `count_lines` SELECT COUNT | complies |
| 3 | store.py:1261 `_window_floor` | complies |
| 4 | store.py:1350 `_window_id_floor` | complies |
| 5 | store.py:1226 `max_id` | complies |
| 6 | store.py:1499 `last_id_before_ts` | complies |

Probed combinations, outer plan row:

`query_lines` (store.py:1405), 11 combinations:

- unfiltered: `SCAN lines`. Complies: `ORDER BY id LIMIT ?` on the primary key, no temp b-tree, so the walk stops after `limit+1` rows.
- `order=desc`: `SCAN lines`. Same, walked backwards.
- `port`: `SEARCH lines USING INDEX idx_lines_port_id (port=?)`
- `chan`: `SEARCH lines USING INDEX idx_lines_chan_id (chan=?)`
- `port+chan`: `SEARCH lines USING INDEX idx_lines_chan_id (chan=?)` (the `+port` de-optimisation holding)
- `last_ms`: anchor `SEARCH lines USING COVERING INDEX idx_lines_ts (ts>?)`, then `SEARCH lines USING INTEGER PRIMARY KEY (rowid>?)`
- `port+last_ms`: anchor, then `SEARCH lines USING INDEX idx_lines_port_id (port=? AND id>?)`
- `since_id`: `SEARCH lines USING INTEGER PRIMARY KEY (rowid>?)`
- `since_ts`: anchor, then `SEARCH lines USING INTEGER PRIMARY KEY (rowid>?)`
- `id_from+id_to`: `SEARCH lines USING INTEGER PRIMARY KEY (rowid>? AND rowid<?)`
- `match`: `SCAN lines`. **Exempt because** no index can serve `raw REGEXP ?`, and `query_lines_safe` (store.py:1547) offloads exactly the match-bearing query to `match_executor` on a private connection, so the scan is not on the loop. The in-memory fallback keeps it on the loop but re-arms the `regex` timeout budget.

`count_lines` (store.py:1446), 7 combinations:

- unfiltered: `SCAN lines USING COVERING INDEX idx_lines_ts`. **Exempt because** it is a whole-table aggregate, the registry's named exempt shape; it rides the covering index rather than the table btree.
- `port`: `SEARCH lines USING COVERING INDEX idx_lines_port_id (port=?)`
- `chan`: `SEARCH lines USING COVERING INDEX idx_lines_chan_id (chan=?)`
- `port+chan`: `SEARCH lines USING INDEX idx_lines_chan_id (chan=?)`
- `last_ms`: `SEARCH lines USING COVERING INDEX idx_lines_ts (ts>?)`
- `port+last_ms`: `SEARCH lines USING INDEX idx_lines_port_id (port=? AND id>?)`
- `id_from=1, id_to=4`: emitted as `WHERE id <= 4` and planned `SEARCH lines USING INTEGER PRIMARY KEY (rowid<?)`. The `id >= 1` bound is dropped deliberately (store.py:1435) because a bound that constrains nothing forces the count off the covering index.

## Verdict list: CAN reads

| # | Site | Verdict |
|---|------|---------|
| 7 | store.py:1611 `query_can_frames` | complies |

8 combinations, all driving from `can_frames` with no temp b-tree:

- unfiltered / `port`: `SCAN cf` then `SEARCH l USING INTEGER PRIMARY KEY (rowid=?)`
- `can_id` / `port+can_id`: `SEARCH cf USING INDEX idx_can_id_line (can_id=?)`
- `last_ms` / `port+last_ms`: anchor on `idx_lines_ts`, then `SEARCH cf USING INTEGER PRIMARY KEY (rowid>?)`
- `since_id`: `SEARCH cf USING INTEGER PRIMARY KEY (rowid>?)`
- `id range`: `SEARCH cf USING INTEGER PRIMARY KEY (rowid>? AND rowid<?)`

The `SCAN cf` on the `?port=` case is a backwards primary-key walk under `LIMIT`, which is the shape the `CROSS JOIN` was added to guarantee. Complies.

## Verdict list: plot reads

| # | Site | Verdict |
|---|------|---------|
| 8 | store.py:1676 `query_plot_channels` | exempt (whole-table aggregate) |
| 9 | store.py:1739 `query_plot_series` windowed subquery | complies |
| 10 | store.py:1745 `query_plot_series` decimate=1 outer | complies |
| 11 | store.py:1753 `query_plot_series` decimate>1 outer | exempt (window-function sort) |
| 12 | store.py:1802 `export_sids` | complies |
| 13 | store.py:1832 `count_plot_export` | complies |
| 14 | store.py:1869 `iter_plot_export` | complies |

- `query_plot_channels` unfiltered: `SCAN plot_points USING COVERING INDEX idx_plot_name_line` + `USE TEMP B-TREE FOR ORDER BY`. **Exempt because** it counts every point of every channel; the b-tree sorts the aggregate result (one row per channel), not the input. Runs off the loop via `query_plot_channels_safe`.
- `query_plot_channels port`: adds `SEARCH li USING INTEGER PRIMARY KEY (rowid=?)`, no bloom filter. The filter makes it no worse, which is the registry's test for the exempt shape. Complies.
- `query_plot_series`, 8 combinations (name only, `port`, `last_ms`, `port+last_ms`, `since_id`, id range, `decimate`, `port+decimate`): every one drives `SEARCH pp USING INDEX idx_plot_name_line (name=? [AND line_id>? [AND line_id<?]])` then `SEARCH l USING INTEGER PRIMARY KEY (rowid=?)`. The `port` filter does **not** flip the drive order onto `lines` despite `idx_lines_port_id` existing, because `pp.name = ?` is an equality on the leading index column. Complies. The `decimate>1` form adds three `USE TEMP B-TREE FOR ORDER BY` rows: inherent to the min/max window functions, and it runs off the loop via `query_plot_series_safe`.
- Export family (`export_sids`, `count_plot_export`, `iter_plot_export`) takes no `port`; probed with `names`, `names+last_ms`, `names+id range`. All drive from `pp` on `idx_plot_name_line` and probe `l` by primary key. `export_sids` adds `USE TEMP B-TREE FOR DISTINCT` (inherent to `SELECT DISTINCT`, result is a handful of sids), and all three run off the loop. Complies.

## Verdict list: sessions

| # | Site | Verdict |
|---|------|---------|
| 15 | store.py:939 `active_session` | **VIOLATES (confirmed)** |
| 16 | store.py:948 `_max_session_ref_id` | exempt (whole-table aggregate) |
| 17 | store.py:961 `get_session` | complies |
| 18 | store.py:978 `resolve_session` by id | complies |
| 19 | store.py:983 `resolve_session` by name | complies |
| 20 | store.py:190 `SESSION_LIST_SQL` / `list_sessions` | complies |
| 21 | store.py:1098 `_captured_traffic` | complies |
| 22 | store.py:1041 `INSERT INTO sessions` | exempt (INSERT VALUES) |
| 23 | store.py:1077 `UPDATE sessions SET ended_ts` | complies |
| 24 | store.py:1108 `DELETE FROM sessions WHERE id = ?` | complies |

### FINDING 1 (violates, confirmed): `active_session` scans the whole `sessions` table on the loop

`host/mcuscope/store.py:939`

```
SELECT ... FROM sessions WHERE ended_ts IS NULL ORDER BY id DESC LIMIT 1
```

Probe output (no `sqlite_stat1`):

```
### active_session
  SQL: SELECT id, name, note, started_ts, ended_ts, start_id, end_id, auto
       FROM sessions WHERE ended_ts IS NULL ORDER BY id DESC LIMIT 1
    PLAN: SCAN sessions
```

Confirmed at scale, 5000 sessions, none active, no `sqlite_stat1`:

```
sessions: 5000
active_session plan: SCAN sessions
active_session (none active, 5000 rows): 1.860 ms/call
```

`ended_ts IS NULL` has no index (`idx_sessions_name` is `(name, id)`), so the predicate is not sargable and the only bound is `ORDER BY id DESC LIMIT 1`. That short-circuits on the first row **only while a session is running**. With none running the plan reads every session row, linear in the table, and `sessions` is never trimmed by retention while the daemon opens one automatic session per run.

It is the exact busy/quiet asymmetry `idx_lines_port_id` and the `last_ms` id-floor were added for, one table over: the cheap case hides it and the quiet case pays in full.

On the loop, five handler call sites, and the two that expect `None` are precisely the always-full-scan case:

- `server.py:837` GET `/status` (polled by the web UI on a timer)
- `server.py:1047` POST, `if body.auto_session and store.active_session() is None`
- `server.py:1176`, `server.py:1189` session endpoints
- `server.py:1221` `if store.active_session() is None and ... auto_session`

Also `store.py:1068` `_stop_session_locked`.

Severity: low today (1.86 ms at 5000 sessions), but the shape and the growth are the registry's, and there is no pinned plan test.

### The rest of the sessions statements

- `_max_session_ref_id` (store.py:948): `SEARCH sessions` for `SELECT MAX(MAX(COALESCE(start_id,0), COALESCE(end_id,0))) FROM sessions`. **Exempt because** it is a whole-table aggregate over an expression no index can serve; the registry names that shape exempt. Measured 3.92 ms/call at 5000 sessions, but it runs only at `start()` (store.py:480) and on the row-by-row fallback after a failed batch insert (store.py:757), not per request.
- `get_session` / `resolve_session` by id: `SEARCH sessions USING INTEGER PRIMARY KEY (rowid=?)`.
- `resolve_session` by name: `SEARCH sessions USING INDEX idx_sessions_name (name=?)`.
- `list_sessions`: `SCAN s` + `CORRELATED SCALAR SUBQUERY 1` -> `SEARCH l USING INTEGER PRIMARY KEY (rowid>? AND rowid<?)`. The `COALESCE(s.end_id, _MAX_LINE_ID)` fix holding: both ends bounded. The outer `SCAN s` is bounded by `ORDER BY s.id DESC LIMIT ?` (limit <= 1000) and runs off the loop.
- `_captured_traffic`: `MULTI-INDEX OR` over `SEARCH lines USING INDEX idx_lines_chan_id (chan=? AND id>? AND id<?)` legs. Bounded seeks on both ends.
- `UPDATE sessions` / `DELETE FROM sessions`: `SEARCH sessions USING INTEGER PRIMARY KEY (rowid=?)`.

## Verdict list: writes and deletes

| # | Site | Verdict |
|---|------|---------|
| 25 | store.py:726 `INSERT INTO lines(id, ...)` batch | exempt (INSERT VALUES) |
| 26 | store.py:731 `INSERT INTO plot_points` batch | exempt (INSERT VALUES) |
| 27 | store.py:736 `INSERT INTO can_frames` batch | exempt (INSERT VALUES) |
| 28 | store.py:773 `INSERT INTO lines` single | exempt (INSERT VALUES) |
| 29 | store.py:781 `DELETE FROM lines WHERE id = ?` | complies |
| 30 | store.py:795 `INSERT INTO plot_points` | exempt (INSERT VALUES) |
| 31 | store.py:800 `INSERT INTO can_frames` | exempt (INSERT VALUES) |
| 32 | store.py:1963 `_delete_range_chunk` | complies |
| 33 | store.py:1957 `_delete_oldest_chunk` | complies (background sweep) |
| 34 | store.py:2114 `_delete_expired_chunk` | complies (background sweep) |
| 35 | store.py:2010 `_estimated_rows` | exempt (whole-table aggregate, background sweep) |

- `_delete_range_chunk` (the handler-reachable one, `DELETE /lines` / purge): `SEARCH lines USING INTEGER PRIMARY KEY (rowid=?)` + `LIST SUBQUERY 1` -> `SEARCH lines USING INTEGER PRIMARY KEY (rowid>? AND rowid<?)`, then the FK cascade `SEARCH plot_points USING COVERING INDEX idx_plot_line (line_id=?)` and `SEARCH can_frames USING INTEGER PRIMARY KEY (rowid=?)`. Bounded at both ends and chunked.
- `_delete_oldest_chunk` with no floor: the subquery is `SCAN lines` under `ORDER BY id LIMIT ?`, a forward primary-key walk that stops at the chunk size. Complies.
- `_delete_expired_chunk`: `SEARCH lines USING COVERING INDEX idx_lines_ts (ts<?)`. Complies (pinned).

## Verdict list: session export and meta

| # | Site | Verdict |
|---|------|---------|
| 36 | store.py:1149 `SELECT MAX(id) FROM src.lines` | complies |
| 37 | store.py:1151 `INSERT INTO lines SELECT ... FROM src.lines` | complies |
| 38 | store.py:1157 `INSERT INTO can_frames SELECT ...` | complies |
| 39 | store.py:1162 `INSERT INTO plot_points SELECT ...` | complies |
| 40 | store.py:1167 `INSERT INTO sessions(...) VALUES` | exempt (INSERT VALUES) |
| 41 | store.py:486 `SELECT value FROM meta` | exempt (startup only, PK probe) |
| 42 | store.py:489 `INSERT INTO meta` | exempt (startup only) |
| 43 | store.py:1198 `INSERT OR REPLACE INTO meta` | exempt (startup only) |
| 44 | store.py:256 `SELECT sql FROM sqlite_master` | exempt (migration, startup only) |
| 45 | store.py:273 `INSERT INTO sessions_autoinc SELECT` | exempt (migration, startup only) |
| 46 | store.py:1940 `retention_floor_id` OFFSET query | complies (background sweep) |
| 47 | store.py:1946 `SELECT MIN(start_id) FROM sessions` | exempt (whole-table aggregate, background sweep) |

- `export_session_db`, on its own connection with the capture ATTACHed and run in a worker thread: `SEARCH src.lines USING INTEGER PRIMARY KEY (rowid>? AND rowid<?)`, `SEARCH src.can_frames USING INTEGER PRIMARY KEY (rowid>? AND rowid<?)`, `SEARCH src.plot_points USING INDEX idx_plot_line (line_id>? AND line_id<?)`. All three bounded.
- `retention_floor_id`: `SCAN sessions` for `ORDER BY id DESC LIMIT 1 OFFSET ?`. The walk is reverse primary key and stops after `min_sessions` rows, so it is bounded by the configured floor, not by the table. Complies.

## Trap check: index changes regress siblings

`idx_lines_port_id (port, id)` and `idx_lines_chan_id (chan, id)` are the two indexes that make `lines` plans interdependent. Every statement over `lines` was re-explained above, including the combinations rather than the motivating case alone:

- `query_lines`: port, chan, port+chan, last_ms, port+last_ms, since_id, since_ts, id range, match, unfiltered, order=desc.
- `count_lines`: the same set minus match.
- `query_can_frames` and `query_plot_series` both reach `lines` through a join and were probed with and without `port`; neither flips its drive order onto `lines`.
- Export family reaches `lines` through a join with no port filter; drives from `plot_points`.

No sibling regression found.

## Plan-pinning coverage in `host/tests/test_hardening.py`

Pinned today:

- `test_can_frames_always_drives_from_the_frame_table` (line 1176): `query_can_frames` port, port+last_ms, last_ms, since_id, can_id, unfiltered. Six combinations, asserted positively (`" cf" in rows[0]`) plus no temp b-tree.
- `test_plot_channels_port_filter_does_not_scan_lines` (line 1221): `query_plot_channels(port=)`, positive `SEARCH li ... PRIMARY KEY`, no bloom filter.
- `test_session_line_count_is_bounded_at_both_ends` (line 1120): `list_sessions`, `rowid>?` and `rowid<?`.
- `test_lines_port_filter_seeks_rather_than_scans` (line 1353): `query_lines` port and port+chan; `count_lines` port, chan, port+chan, last_ms.
- `test_a_last_ms_window_seeks_by_id_rather_than_reading_the_table` (line 1414): `query_lines(last_ms=)`, busy and empty window.
- `test_since_ts_seeks_by_id_rather_than_scanning_the_table` (line 1462) and `test_since_ts_keeps_its_strictly_greater_boundary` (line 1532): `query_lines(since_ts=)`.
- `test_the_age_sweep_does_not_read_the_table_when_nothing_has_expired` (line 1566): `_delete_expired_chunk`.

Statements with **no pinned plan**:

1. `active_session` (store.py:939) - the finding above.
2. `query_plot_series` (store.py:1739/1745/1753), all eight combinations, including the `port` filter that could flip the drive order onto `lines` exactly as `query_can_frames` did. This is the one unpinned statement carrying the shape the registry says regresses silently: a plain `JOIN` (not `CROSS JOIN`) with a `l.port` filter over a table that now has `idx_lines_port_id`.
3. `export_sids` (store.py:1802), `count_plot_export` (store.py:1832), `iter_plot_export` (store.py:1869) - the same `plot_points JOIN lines` shape with an `l.ts` filter.
4. `_delete_range_chunk` (store.py:1963) and `_delete_oldest_chunk` (store.py:1957).
5. `query_lines` / `count_lines` with an explicit `id_from`/`id_to` range, and `query_lines(since_id=)`.
6. `_captured_traffic` (store.py:1098), `retention_floor_id` (store.py:1940), `resolve_session` by name (store.py:983).
7. `export_session_db`'s three `INSERT ... SELECT` statements (store.py:1151/1157/1162).

Nothing on that list is a violation as the tree stands; each is a plan that the next index change could regress unnoticed. Item 2 is the one worth pinning first, because it is the shape that has already bitten twice on the sibling tables.
