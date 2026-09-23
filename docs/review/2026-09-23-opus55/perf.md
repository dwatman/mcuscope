# perf: performance and scale

HEAD `6e4f6f7`, Linux, python 3.13.
Capture: 6.0M lines, 48 sessions over 12 days, two ports (`board` busy, `aux` one line per 7 h), 3.6M plot points (10 channels), 1.2M CAN frames, 1.1 GB.
Built through `Store.submit_line` with a fake `time.time`, with the plot and CAN children decoded as `SerialPort._submit_rx_line` decodes them (`~/tt-data/mcuscope-2026-09-23/perf/gen.py`).
Live runs: `mcuscoped --config` on a copy of that capture, `mcu-sim --plot --flood 3000` on 18701 (about 3100 lines/s stored).
Ingest lag is measured by `probe.py`: `/ws` arrival time minus row `ts`, plus `/status` latency and `rx_dropped`, per second.
Load average was 17-20 on 8 cores (other agents), so absolute times are high; the plans and the scaling are the evidence.
Scratch, with every script and log: `~/tt-data/mcuscope-2026-09-23/perf/` (`big.db` kept for reuse).

## PERF-1. HIGH, CONFIRMED. `/lines?port=<quiet>&chan=...` scans the whole channel index on the event loop, and the stall drops captured lines

- Where: `host/mcuscope/store.py:1688` (`unindexed_port=bool(chans)`), `_window_terms` at `store.py:1549`; the query runs inline because `query_lines_safe` offloads only `match`/`until_ts`.
- Failure: the `+port` de-optimisation forces `idx_lines_chan_id`. With `port` naming a quiet board (or a mistyped one) and `chan` naming a busy channel, SQLite walks every entry of that channel and reads each table row to test `port`.
  - Plan: `SEARCH lines USING INDEX idx_lines_chan_id (chan=?)`, with `+port = 'aux'` as a row filter.
  - At 6M lines: `port=aux&chan=debug` 7.7 s, `port=nosuch&chan=debug` 8.5 s, `port=aux&chan=debug&chan=event` 14.0 s, five channels 12.6 s, `id_to=<mid>` plus port and chan 3.9 s. Linear in the channel's row count.
  - The same query with `INDEXED BY idx_lines_port_id` takes 1 ms.
- The loop is frozen for the whole query. Received lines queue in the loop's callback list, then `_on_bytes` trims the rx deque to `RX_QUEUE_MAX` (10,000; `serial_link.py:790`) and sheds the oldest.
- Repro (live daemon, flood 3000 lines/s): `GET /lines?port=aux&chan=debug&chan=event&limit=100` answered in 12.2 s.
  - `/ws` delivered 0 rows for 12 s, and one `/status` call took 12,211 ms.
  - `rx_dropped` went 0 to **27,861**: those lines are lost from the capture. Log: `probe_lines.log`.
- Real surfaces that send this shape:
  - A web UI pane filtered to one board with any channel unticked (hiding `event` to hide `!ps`/`!can` noise is the obvious case), scrolled to the top. `loadHistoryPage` then sends `port=` plus five `chan=`, up to `HISTORY_HOPS` = 5 pages.
  - `mcu lines -p <port> --chan <c>`.
- The same reversal off the loop (slow, no ingest loss):
  - `count_lines` with port+chan (the retrospective `/assert` count): 3.75 s.
  - `/can/frames?port=aux` (the CROSS JOIN pins `SCAN cf`): 2.35 s.
  - `/plot/series?name=tri&port=aux`: 2.5 s.
  - `first_export_line_id` with `port=aux`: 1.6 s.
- Fix: stop picking one index for both directions.
  - A covering `lines(port, chan, id)` index seeks both columns, and the IN-list early-out keeps `ORDER BY id DESC LIMIT` cheap for several channels.
  - Or offload every port+chan read.
  - Per class 20's own note, re-explain every `lines` statement after adding an index, and pin quiet-port plus busy-channel in a plan test (only the busy-port case is pinned today).
- Class: 20 (the fix for one direction is the defect in the other) and 1 (SQLite scan on the loop).

## PERF-2. HIGH, CONFIRMED. An aborted `/plot/export` pins a read snapshot forever, so the WAL grows without bound

- Where: `store.py:2388-2394`. `iter_plot_export` holds one cursor open across every `yield`.
  - Served via `server.py:1950` (`/plot/export`) and `server.py:1491` (session bundle).
  - When the client goes away mid-stream the generator is never closed, so its connection keeps its read transaction.
- Consequence: no checkpoint can pass that snapshot, and every frame the writer commits is appended to `-wal`.
  - The `busy`/`log`/`checkpointed` counters from `PRAGMA wal_checkpoint(PASSIVE)` show `checkpointed` frozen while `log` climbs.
- Repro 1 (`walstall.py`): read 200 KB of `/plot/export?names=tri,ramp,ftest`, stall, close the socket.
  - WAL 168 MB to 747 MB during the 110 s stall.
  - It kept growing after the close: 1.83 GB five minutes later, about 5.6 MB/s at 3000 lines/s.
  - `checkpointed` stayed at 7142 throughout. No TCP connection to the daemon remained.
- Repro 2: read 200 KB, then close immediately. `log` 14,049 to 54,601 frames over 40 s, `checkpointed` stuck at 141.
- Repro 3 is the web UI's own path: `preflight()` in `webui/state.js` fetches every streamable export and aborts the body once the headers say 200.
  - Doing exactly that (read the headers, close) on a whole-range `/plot/export?names=tri,ramp` pinned it: `log` 18,158 to 45,474 frames, `checkpointed` stuck at 4459.
  - So every whole-capture or long-session plot export started from the web UI leaks a snapshot. Only a daemon restart releases it.
- Not bounded by anything the user can set:
  - `content_bytes` excludes the WAL. Its docstring (`store.py:2595`) says the auto-checkpoint bounds the WAL; this path breaks that.
  - The size cap therefore never sees it, and the disk fills at the capture's WAL rate (about 480 GB/day at 3000 lines/s).
- Also: nothing sets `journal_size_limit`, so after any long read (a 30 s regex scan, the 20 s summary rebuild) `-wal` stays at its high-water mark until restart. 168 MB was seen before this test; `db_size_bytes` stays inflated by it.
- Controls:
  - `/lines/export` and `/can/frames?format=csv` page with `fetchall` per page and release their snapshot between pages.
  - 5 aborted `/lines/export` streams left the checkpoint whole and added 1 fd.
- Fix:
  - Page `iter_plot_export` on `line_id` like `_iter_export_pages`, so no statement stays open across a yield.
  - Close the generator on disconnect, for example `StreamingResponse(..., background=...)` or a `try/finally` wrapper that calls `.close()`.
  - Set `PRAGMA journal_size_limit`.
- Class: new ("a streamed response holding a database cursor across yields, never closed when the client leaves").

## PERF-3. MEDIUM, CONFIRMED. Regex reads over a large capture are refused as "simplify the regex", and the CLI times out first

- Where: `MATCH_BUDGET_S = 30.0` (`store.py:350`), applied per query; the CLI request timeout is 30 s (`cli_client.py:131`).
- Failure: a literal pattern that matches nothing scans the whole window.
  - At 6M lines, `match=NEVERMATCHES`, with or without `chan=debug`, raised `MatchBudgetExceeded` at 30.0 s (`plans2.log`).
  - The 400 tells the user to "simplify the regex", which cannot help.
  - The comment beside the constant says the budget "must not be what stops" a legitimate multi-million-line scan.
- CLI:
  - `mcu lines --match NEVERMATCHES --limit 5`: exit 2 `request timed out` at 30.3 s, which reads as a match timeout.
  - `mcu assert --forbid NEVERMATCHES` (retrospective, the default): exit 1 `request timed out` at 30.5 s. The `mcu assert` docs give exit 1 as "the assertion failed".
  - A retrospective assert over a days-long capture is not usable at all.
- The server keeps scanning after the client leaves, each pattern up to 30 s in turn, holding a `match_executor` worker (feeds PERF-4).
- Fix:
  - Make the per-query budget proportional to rows or pages scanned, or answer with a distinct "window too large" error that suggests `--session`/`--last-ms`.
  - Keep "simplify the regex" for the per-call `MATCH_TIMEOUT_S` hit.
  - Give the CLI a timeout longer than the server budget.
- Class: 70 (one refusal for two causes).

## PERF-4. MEDIUM, CONFIRMED. Live `/wait` and `/assert` matching queue behind analytics on the 4-worker `match_executor`

- Where: `server.py:2412` (`/wait`) and the live `/assert` loop both `run_in_executor(match_executor(), ...)` with no deadline.
  - `Store._offload` puts every heavy read on the same 4 workers: plot series, the plot summary rebuild, session counts, CAN, `count_lines`.
- Failure: a `/wait` whose target line arrives at once cannot report it until a worker frees up.
- Repro (`starve.py`, flood lines match `flood line` about 3000 times/s):
  - Baseline `/wait {"match":"flood line","timeout_ms":5000}` returned match with `waited_ms=14`.
  - During 4 concurrent `/plot/series?name=tri`: `waited_ms=7988`, past its own 5000 ms timeout.
  - During 4 concurrent `/lines?match=NEVERMATCHES`: `waited_ms=28971`.
  - `mcu wait --match "flood line" --timeout 2000` under the second load: exit 1 `request timed out` after 7.6 s, although matching lines were arriving continuously.
- Four web UI panes with a regex filter paging history, or one agent running PERF-3's assert, are enough to break `mcu wait` for another agent.
- Fix: run live-window matching on its own small pool (it scans short batches), or inline; `regex` with `timeout=` releases the GIL. Or give analytics a separate pool from latency-critical matching.
- Class: 1 (the reserved-pool lesson, repeated on `match_executor`).

## PERF-5. MEDIUM, CONFIRMED. The plot channel summary is rebuilt from the whole `plot_points` table after every start and every delete

- Where: `_plot_dirty = True` at construction (`store.py:481`) and on every `_delete_lines` (`store.py:1441`), meaning retention (hourly), each size-cap trim (every minute when over the cap) and purge.
  - `_scan_plot_summary` then reads everything.
- Measured at 3.6M points:
  - `_scan_plot_summary` 22.0 s.
  - First `GET /plot/channels` after a daemon start: 20.4 s. After a purge: 18.7 s (later calls 0.0 s).
- The plan takes `idx_plot_line (line_id<?)` plus a table lookup per point and a temp b-tree `GROUP BY`. The unfiltered SQL aggregate over the covering index is 1.6 s.
- Who waits on it:
  - The web UI's `seedChannelList` on every page load (the charts stay empty).
  - Every `/plot/export`, which checks `known` through the summary before streaming. A whole export's first byte came at 30.6 s.
  - `mcu plot channels`, whose 30 s CLI timeout this reaches at roughly 1.5x this capture.
- It also holds a `match_executor` worker (PERF-4).
- Fix: on delete, decrement the summary from an aggregate over the deleted id range (O(chunk)), and rebuild only when a channel's newest sample was deleted. Or, at least, `INDEXED BY idx_plot_name_line` for the rebuild.
- Class: 20 (whole-table work on a request path).

## PERF-6. MEDIUM, CONFIRMED. `/plot/series` with no window computes `ROW_NUMBER` over the channel's entire history

- Where: `store.py:2204`. The window function numbers every matching point before `rn <= limit` applies.
- Measured at 1.2M points for `tri`:
  - default `limit=10000` 8.8 s; with `port=board` 10.0 s; `decimate=10&limit=100000` 7.8 s.
  - `rpm`, with 240k points: 1.1 s.
  - The web UI seed, bounded by `last_ms` plus `id_to`, is 78 ms, so only unbounded REST callers pay.
- A plain inner `ORDER BY pp.line_id DESC LIMIT 10000` returns the same rows in 17 ms (checked on the same file).
- Four concurrent calls starve `/wait` (PERF-4).
- Fix: apply the limit with an inner `ORDER BY ... DESC LIMIT ?` subquery, then decimate the result.
- Class: 20.

## PERF-7. MEDIUM, CONFIRMED (the offline reclaim timing); daemon attribution partly inferred. Per-minute page reclaim stalls the loop 1-2.5 s for about an hour after a large trim

- Where: `_reclaim_pages` (`store.py:152`) via `_reclaim_backlog` (`store.py:2731`) on every tick while the freelist is at least 256 pages.
  - Its docstring gives 15.8 ms per 2000 pages. That is bounded in pages moved, not in time.
- Measured on the same capture after retention (3.1M lines) and a size-cap trim (1.5M lines), freelist 172,867 pages:
  - Offline, one `_reclaim_pages` call took 42 ms to 1,214 ms. Several consecutive calls were near 1 s at freelist 130-155k; later calls were about 100 ms (`vac_ret.log`).
  - In the live daemon, stalls came at the per-minute ticks: 1.0 s, 2.5 s and 1.9 s of `/status` latency with no trim in between.
  - At 2000 pages a tick, that freelist drains for about 86 minutes.
- At 3000 lines/s nothing was dropped. Above about 4000-10,000 lines/s a 2.5 s stall exceeds `RX_QUEUE_MAX` (PERF-1's mechanism).
- Fix: run the reclaim under a time budget (a smaller `incremental_vacuum(N)` repeated until a few ms elapse), and re-measure the docstring at scale.
- Class: 1.

## PERF-8. LOW, CONFIRMED. A whole plot export sorts the full selection before its first byte, spilling to the system temp dir

- Where: `iter_plot_export`'s `ORDER BY pp.line_id, pp.name` with `name IN (...)`. Plan: `SEARCH pp USING INDEX idx_plot_name_line (name=?) ... USE TEMP B-TREE FOR ORDER BY`.
- Measured, 3 names over 6M lines:
  - First row 11.3 s offline; the daemon's first byte came at 30.6 s, behind PERF-5. Total 59 s, 143 MB.
  - A deleted SQLite temp file of 126 MB was open in the daemon while it ran. It lives in the system temp dir, which SPEC 3.4 keeps exports out of because `/tmp` is RAM on many installs.
- Fix: drive from `idx_plot_line` over the id range (already in `line_id` order), and order the names within one line in Python.

## PERF-9. LOW, CONFIRMED. WAL write amplification: about 20x the captured bytes

- At 3000 lines/s (about 0.55 MB/s of row data) the WAL took about 1380 frames/s, 5.6 MB/s: the `log` counter rose 13.8k per 10 s while a snapshot was pinned.
- Each small commit rewrites every hot index tail page (lines 3 indexes, plot 2, CAN 1). A day at that rate is about 480 GB of WAL writes plus the checkpoint copy.
- Fix: a minimum commit interval (for example 100-250 ms) when the queue is not full, trading a little `/ws` latency for far fewer rewritten pages.

## Checked and fine

- Writer throughput versus DB size: flood 15,000 lines/s for 20 s on an empty DB and on the 5M-line DB. Both stored about 14.5k lines/s with 0 `rx_dropped` and lag under 0.2 s (`probe_fresh.log`, `probe_live.log`).
- Startup on the 6M-line DB: 1.1-2.3 s to a first `/status`.
- Startup retention sweep deleting 3.1M expired lines under 3000 lines/s ingest: max lag 0.52 s, `/status` at most 263 ms, 0 dropped (`probe_ret.log`).
- `POST /purge` of 1M lines under ingest: 31 s, max lag 0.52 s, 0 dropped (`probe_purge.log`).
- Size-cap trim of 1.49M lines: 0 dropped, but stalls of up to 2.5 s (PERF-7).
- Inline `/lines` plans, all index seeks at 0.1-9 ms:
  - no filter, `port` alone (busy, quiet, absent), `chan` alone (busy, rare, absent), multi-`chan` IN;
  - `last_ms`, `since_ts`, `since_id`, `id_to`;
  - busy port plus rare chan.
- CAN: `/can/frames` with no filter, busy port, `bus`, single id, absent id, `last_ms`, session, and the CSV page all take 0.1-11 ms. The multi-id IN list and an absent bus are 0.3 s off the loop.
- `count_lines` for purge dry-run, session and `last_ms`: 20-83 ms. `_estimated_rows` is 112 ms on the loop, once a minute and only while over the cap.
- `learn_stored_plot_defs` (the `^!pd` lookback): 110 ms. The web UI `seedPlotDefs` and `/plot/series` seed are bounded by id and are fast.
- `/status` stays at single-digit ms under load, apart from the stalls above.
- Session export (`.db` copy) of a 125k-line session: 1.3 s. Bundle: 11.4 s. `/lines/export`: 2.2 s. CAN CSV: 0.3 s.
- Paged exports (`/lines/export`, CAN CSV) release their snapshot between pages, and an aborted one leaves the WAL checkpointable.
- `list_sessions`: 1.4 s at 6M lines, offloaded. It is O(lines in the listed sessions), as its docstring states, and is used only by the CLI and the export dialog.
- `_window_id_ceiling` (`until_ts`): 1.1-1.3 s off the loop, as documented.

## Not covered

- Windows. The reclaim and checkpoint timings in PERF-7 and PERF-9 are likely worse on slow or antivirus-scanned disks. PERF-2's growth is platform-independent.
- Captures past 6M lines: most times above are linear in it, so a 50M-line capture multiplies them by about 8.
- Browser-side web UI behaviour with the big capture (render cost, memory). Only the requests it sends were driven.
- `/ws` fan-out with many subscribers at high rate, and PlotJuggler UDP at rate.
- Unbounded growth of `_plot_summary` keys from firmware inventing ad-hoc `!p` names (not checked whether the protocol bounds names).
