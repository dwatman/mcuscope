# Leg A - daemon API (pre-release, v0.4.0..fdd30a2)

Scope: `server.py`, `store.py`, `config.py`, `update_check.py` diffs and the code they touch.
Scratch evidence: `~/tt-data/prerelease-2026-09-15/A/` (`measure.py`, `stall.py`, `floor.py`, `floor2.py`, `plans.py`, `test_probe_a.py`, `probe_quiet.py`, `probe_misc.py`, outputs `*.out`).
Daemons: `mcuscoped --sim --config big.toml` / `typo.toml` on 18611 with scratch db_path, stopped by PID. 1M-row synthetic capture (`build_db.py`, two ports, monotonic ts, no `sqlite_stat1`).

Totals: 1 HIGH, 2 MED, 9 LOW.

## Findings

### A-1 HIGH - `until_ts` id ceiling is an O(rows) index walk, re-run on the loop per `/lines` request and per export page

- Where: `store.py:1580-1583` (`_window_id_ceiling`), reached inline on the loop via `query_lines` (`store.py:1842`, match-free queries are not offloaded) and once per 1000-row page via `_iter_export_pages` (`store.py:2349`) for `/lines/export` and `/can/frames?format=csv`.
- Defect: the 2026-09-12 D6 fix replaced a single seek with `MAX(id)` over every `idx_lines_ts` entry at or below `until_ts`; its cost was measured once per call at 300k rows, never per page or on the loop.
- Failure: 1M rows, `until_ts` just below the newest row (any "to a minute ago" range, web UI clock-mode export, `mcu ... --to`).
  - `GET /lines?limit=1&until_ts=T` 261 ms per request against 2.0 ms for the equivalent `id_to`; a separate-process `/version` poller saw a 403 ms loop stall (baseline 6-7 ms).
  - `GET /lines/export?format=text&until_ts=1780000999` 292.7 s against 11.3 s for `id_to=999000`, same 32,855,895 bytes. At 300k rows 34.6 s against 3.0 s (quadratic).
  - Ceiling alone (`ceiling.py`): 19 / 90 / 161 / 181 ms at 10 / 50 / 90 / 99.9 % of the capture, old seek 0.02-0.07 ms.
- DRIVEN: `measure.py`, `stall.py`, `ceiling.py` against the 18611 daemon; outputs `measure.out`, `stall.out`.
- Class: 1 and 20 (a sargable seek regressed to a walk; the sweep's plan check reads `SEARCH ... COVERING INDEX (ts<?)`, which hides the range length), with class 44's shape (a bound re-derived on every page).
- Fix: resolve `until_ts` to an id ceiling once per request in the handler and pass it down as `id_to` (every page reuses it), and offload `/lines` whenever `until_ts` is given.

### A-2 MED - exports anchor `last_ms` at the newest stored line when the caller gave no upper bound

- Where: `server.py:1692-1696` (`/lines/export`), `1749-1750` (`/can/frames` csv), `1856-1860` (`/plot/export`) freeze `id_to = max_id()`, and `_window_floor` (`store.py:1454`) then anchors at that row's `ts` instead of now.
- Defect: SPEC 3.4 says `last_ms` counts back from the newest line at or below an effective upper bound "from `id_to`, or from a session that has ended", and "with no upper bound it counts back from now"; the internal freeze is read as a caller bound. The diff's `_effective_bounds` change now names the file after the same wrong window.
- Failure: capture whose newest line is 3600 s old (a board that went quiet). `/lines?last_ms=60000` -> 0 rows; `/lines/export?last_ms=60000&format=jsonl` -> 3 rows, all an hour old, `filename="capture_lines_20260915T013907-end.jsonl"` at 02:40 local.
- DRIVEN: `probe_quiet.py`.
- Class: 44 (server-side twin: the freeze taken for one purpose changes another bound's meaning); new class candidate if the owner wants it separate: "an internal freeze read as a caller-supplied bound".
- Fix: when the caller sent no `id_to` and the session is open or absent, resolve `last_ms` against now before freezing (pass the absolute floor down, keeping the `ts >= ?` inclusivity), then freeze `id_to`.

### A-3 MED - a deadband on a label channel first declared inside the window is accepted and does nothing

- Where: `server.py:1892` calls `_renders_as_label` (`server.py:2922-2937`) with `decs` from `_plot_export_defs`, which holds only the definitions primed before `first_id`; the in-window `defs` are never consulted.
- Defect: SPEC 9.2 lists "a channel that renders as a label" as a 400 because the band "would otherwise be accepted and do nothing"; a channel absent from the primed set yields `kinds == []` and passes.
- Failure: feed `!pd 3 volts:s2*0.01:V`, `!ps 3 1 0064`, `!pd 4 mode:u1:=0=IDLE,1=ARMED,2=RUN`, three `!ps 4` samples. `GET /plot/export?names=volts,mode&decode=1&changes=1&deadband=mode=5` -> 200, `mode` rows render `IDLE`, `ARMED`, `RUN`; the same request with `names=mode` alone -> the 400.
- DRIVEN: `test_probe_a.py::test_deadband_on_enum_declared_inside_window` (fails as written).
- Class: new class candidate: "a refusal judged on the state primed before the window, not the window it governs".
- Fix: also learn each in-window `defs` row into a scan decoder per port and judge `declared_kinds` over primed plus in-window declarations.

### A-4 LOW - a bundle that waited on `_sweep_lock` behind the deletion of its own session answers 200 and resurrects the label

- Where: `server.py:1429-1442`: `session`, `lo`, `hi` are resolved before `async with store._sweep_lock`, and never re-checked after it.
- Failure: session `racer` over 1M rows; `DELETE /sessions/1?data=true` then 0.3 s later `GET /sessions/racer/bundle`. The bundle waits 17.7 s, then returns 200 (1754 bytes): `lines.txt` 0 bytes, manifest `{"session": "racer", "id": 1, "from_id": 1, "to_id": 1000000, ...}`, and `capture.db` carries the session row. `GET /sessions?name=racer` afterwards -> `[]`. SPEC 3.4: an unknown reference is a 400.
- DRIVEN: curl race on the 18611 daemon (session row inserted into the scratch db).
- Class: 37.
- Fix: inside the lock, re-read the session by id (`get_session`), 400 if gone, and take `hi` there.

### A-5 LOW - unknown-key config warnings reach only stderr, and repeat on every config read

- Where: `config.py:276-306` logs from `_from_dict`, which runs in `daemon.main`, `GET /config` (`server.py:1065`) and `PUT /config/ports` (`server.py:1231`).
- Defect: SPEC 3.3 promises the key is "warned about by name"; no `/status` or `/config` field carries it, and `mcu daemon start` sends the child's stderr to `.err`, printed only when the start fails (`cli_daemonctl.py:57`, used at 187 and 202).
- Failure: `typo.toml` with `prot`, `[storge]`, `baudrate`: startup plus two `GET /config` printed the three warnings three times on stderr; `/status` has no field for them. Under `mcu daemon start -c typo.toml` a successful start shows none (REASONED from the code; not driven, to keep off the owner's pid records).
- DRIVEN (stderr, repeat) / REASONED (daemon start).
- Class: 12 (a setting that failed to apply, invisible on the surface the user looks at).
- Fix: collect the warnings into `app.state` and expose them on `/status` (and the web UI Settings), logging once at startup rather than per read.

### A-6 LOW - the shutdown sentinel discards rows queued ahead of it

- Where: `server.py:2179-2183` (`CaptureWatch.next_batch` raises when `None` is anywhere in the drained batch) and `server.py:2000-2001` (`/ws` pump returns without sending the rows before it).
- Failure: a watch opened, `READY` committed (row 5 queued to it), then `store.stop_subscribers()`: `next_batch` raises `CaptureStopped`, so a `/wait` whose match arrived just before SIGTERM answers 503 "cut short" instead of `match`. `/ws` drops up to `WS_BATCH_MAX` rows the same way.
- DRIVEN: `probe_misc.py` (function level on a live store).
- Class: 16 (the terminating item takes the good items batched with it).
- Fix: judge or send the rows before the sentinel first; raise or return on the sentinel afterwards.

### A-7 LOW - `stop_subscribers` mutates asyncio queues and futures from a Python signal handler

- Where: `daemon.py:274-278` calls `store.stop_subscribers()` (`store.py:618`) from `handle_exit`, which uvicorn 0.52 installs with `signal.signal` (verified in `Server.capture_signals`).
- Defect: the handler runs between bytecodes of whatever the loop thread is doing, so it can interleave with `_broadcast_batch`'s `q.full()` / `q.get_nowait()` / `q.put_nowait()` on the same queue and call `Future.set_result` outside the loop's control; asyncio requires `call_soon_threadsafe` from signal context.
- Failure: a SIGTERM landing inside a fan-out can drop the sentinel (`QueueFull` suppressed after the broadcast refilled the slot), leaving that `/wait` to uvicorn's 500 the change exists to prevent. Probability is low; the `/shutdown` path calls it on the loop and is safe.
- REASONED.
- Class: new class candidate: "loop-owned state mutated from a signal handler".
- Fix: `loop.call_soon_threadsafe(store.stop_subscribers)` in `handle_exit` (the graceful wait starts on a later loop tick, so ordering holds).

### A-8 LOW - `deadband` values accept `1_0` and padded whitespace

- Where: `server.py:2910-2913`: `float(value)` plus `isascii()` and `isfinite()`.
- Failure: `deadband=volts=1_0` -> 200 with a band of 10; `volts= 5 ` -> 200. SPEC 9.2: "a value that is not a finite ASCII number" is a 400.
- DRIVEN: `test_probe_a.py::test_deadband_value_grammar` (`{'1_0': 200, ' 5 ': 200, '-5': 200, '1e308': 200}`).
- Class: 22.
- Fix: match the SPEC 2.5 value grammar with an ASCII regex before `float()`; decide whether `-5` (taken as 5) is refused too.

### A-9 LOW - negative `last_ms` is accepted on every read and export endpoint

- Where: `Query(default=None, le=MAX_MS)` with no lower bound at `server.py:1638, 1676, 1715, 1805, 1824`; `AssertBody.last_ms` has `gt=0`.
- Failure: `/lines?last_ms=-60000` -> `{"lines":[]}`; `/lines/export?last_ms=-60000&format=csv` -> header-only 200 named `capture_lines_20260915T023336-end.csv`, a `from` 60 s in the future; `/can/frames?last_ms=-5` and `/plot/series?last_ms=-60000` empty 200. The inverted `since_ts`/`until_ts` window is a 400 for exactly this reason. Pre-existing before v0.4.0.
- DRIVEN: `test_probe_a.py::test_negative_and_zero_windows`.
- Class: none registered; the same shape as the inverted-window refusal in `_check_window`.
- Fix: `ge=1` (or `gt=0`) on the five `last_ms` queries, as `/assert` has.

### A-10 LOW - `session=` with `until_ts` before the session starts exports a backwards-named empty file

- Where: `server.py:2732-2750` (`_effective_bounds` takes `max(lows)`, `min(highs)` without checking they cross).
- Failure: session `run` started 02:41:20; `/lines/export?session=run&until_ts=<02:41:19>&format=csv` -> 200 header only, `filename="run_lines_20260915T024120-20260915T024119.csv"`.
- DRIVEN: `probe_misc.py`.
- Class: 17 (the D3 backwards-filename shape, via a contradicting pair the handler does not refuse).
- Fix: refuse a window whose effective `from` exceeds `to` with a 400 naming the pair, as `until_ts is before since_ts` is.

### A-11 LOW - a repeated name in `/plot/export?names=` is exported twice

- Where: `server.py:1837`.
- Failure: `names=volts,volts&format=wide` -> header `ts,tick_ms,volts,volts`, every value duplicated (long format not driven).
- DRIVEN: `test_probe_a.py::test_negative_and_zero_windows`.
- Class: none.
- Fix: refuse a duplicate name (`names lists volts twice`) or dedup preserving order.

### A-12 LOW - SPEC 3.4 contradicts itself on time-bound exactness; the lower bound drops rows across a clock step

- Where: `docs/SPEC.md:851-852`: "Both bounds are exact over the rows, whatever the wall clock did", then "(The lower bound is the weaker half - its derived id floor still assumes `ts` rises with `id` ...)". Code: `store.py:1586` `_window_id_floor`.
- Failure: ids 1-3 at ts 1000-1002, clock steps back, ids 4-6 at 997-999.
  - `query_lines(since_ts=998.5)` -> `[6]`, expected `[1, 2, 3, 6]`.
  - `query_lines(last_ms=4000, id_to=6)` -> `[4, 5, 6]`, expected all six. `until_ts=1001.5` is exact.
- DRIVEN: `floor.py`, `floor2.py`.
- Class: none new (the known D6 remainder); the contradiction is the SPEC text. Whether to fix the floor is under Decisions.
- Fix: SPEC sentence to "The upper bound is exact ..."; see Decisions for the code.

## Sweeps

### Class 1 - blocking work on the loop

- `grep -n "@app\.\(get\|post\|put\|delete\|websocket\)" server.py`: 37 endpoints. `grep -rn "run_in_executor(None" host/mcuscope`: 0 executable lines.
- Changed by the diff (12):
  - `GET /lines`: violates (A-1, `until_ts` walk inline); `_session_range` is one indexed lookup, complies.
  - `GET /lines/export`, `GET /can/frames`: the export page walk is on the stream's worker thread (off loop, cost is A-1); `_effective_bounds` now issues `_window_floor` on the loop, one PK seek (`SEARCH lines USING INTEGER PRIMARY KEY (rowid<?)`), complies.
  - `GET /plot/export`: `known` via `query_plot_channels_safe` (summary), `_plot_export_defs` via `query_lines_safe` with match (offloaded), `_window_floor` seek; complies.
  - `GET /plot/series`, `GET /plot/channels` (`plot_ports_safe` is an in-memory set over the summary): complies.
  - `GET /sessions/{ref}/bundle`: waits on `_sweep_lock` (awaitable), build on `to_thread`; complies.
  - `POST /purge`: `isfinite` only; complies.
  - `POST /sessions`: validator only; complies.
  - `POST /wait`, `POST /assert`, `WS /ws`: sentinel handling in memory; complies.
- Unchanged by the diff (25): `/`, `/status`, `/shutdown`, `/ports` x2, `DELETE /ports/{alias}`, reconnect, disconnect, `/devices`, `/config` (load_config now also warns, still `to_thread`), 4 x `PUT /config/*`, `GET`/`PUT /plotjuggler`, `PUT /config/ports` (same), `GET /sessions` (`count_lines_safe`, offloaded; `count_lines` lost `until_ts`, no caller passes it), `POST /sessions/stop`, `DELETE /sessions/{id}`, `/sessions/{ref}/export`, `/send`, `/break`, `/cmd`, `/marker`: no call into a changed store read; complies per the 2026-09-12 list.
- Store: `sweep_tick._reclaim_backlog` runs `PRAGMA freelist_count` plus a bounded 2000-page `incremental_vacuum` on the loop each tick; complies (bounded, documented 15.8 ms).

### Class 12 - healthy-while-dead

- Probe list for this leg's surfaces (4):
  - Config key warnings: violates (A-5).
  - `PRAGMA cache_size=-65536`: read back -65536, not reported anywhere; exempt (no refusal path, not a health field).
  - `/wait` and `/assert` on shutdown: 503 path complies; the rows-before-sentinel loss is A-6.
  - `/ws` on shutdown: closes on the sentinel instead of sitting on a healthy socket; complies (REASONED).

### Class 16 - one bad item ends the loop

Loops in the diff (15, in 13 entries):

- Can id element loop `server.py:1731`: exempt, a request validation that refuses the whole request by SPEC.
- `_parse_deadband` loop: exempt, same.
- `_export_rows` pending-def loop: complies (`learn` returns False on a malformed `!pd`).
- `_plot_export_defs` page loop: complies.
- `_wide_header` two loops: complies.
- `_csv_wide` loop: complies.
- `_changes_long` loop: complies.
- `plot_channels` loop: complies.
- `_renders_as_label` comprehension: complies.
- `_warn_unknown` / `_check_unknown` loops: complies (non-dict entries skipped per entry; driven with three typos).
- `stop_subscribers` loop: complies (per-queue suppress).
- `/ws` pump sentinel: violates (A-6).
- `CaptureWatch.next_batch` sentinel: violates (A-6).

### Class 17 - reported value is the request

Sites (7):

- Bundle manifest `from_id`/`to_id`: complies (the frozen span every member used); stale session is A-4.
- `_effective_bounds` filename: violates (A-2 wrong anchor, A-10 backwards pair).
- `/plot/channels` `ports`: complies (read from the stored summary).
- `/purge` `deleted`: complies (delete_range result).
- 503 shutdown message: complies.
- `export_filename` `out-of-range`: complies.
- `cache_size`: exempt, not reported.

### Class 18 - unmapped exception types

- Sites in the diff (2):
  - `export_filename` `(OverflowError, OSError, ValueError)` around `time.localtime`: complies (driven 1e300, -1e300, 2**63 raise OverflowError; the other `localtime` sites, `server.py:357` and `cli_output.py:215`, take `now` or a stored row `ts`, exempt).
  - `update_check.py`: `sys.modules.setdefault("httpx._main", None)` relies on httpx's `try: from ._main import main / except ImportError`, present in the installed httpx and in the 0.27 floor; no except tuple changed; complies.

### Class 20 - non-sargable bound

- `plans.py` on a two-port capture with no `sqlite_stat1`, statements taken off the trace callback (10):
  - `SELECT id, ts FROM lines ORDER BY id DESC LIMIT 1`: `SCAN lines` with LIMIT 1 from the rowid end; complies.
  - `SELECT MAX(id) FROM (SELECT id FROM lines INDEXED BY idx_lines_ts WHERE ts <= ?)`: `SEARCH ... COVERING INDEX idx_lines_ts (ts<?)`, a range walk proportional to the window; violates (A-1, measured).
  - `query_lines` page with `until_ts`: `SEARCH lines USING INTEGER PRIMARY KEY (rowid<?)`; complies.
  - `since_ts` floor `SELECT id FROM lines WHERE ts > ? ORDER BY ts LIMIT 1`: covering-index seek; complies.
  - `last_ms` anchor `_window_floor`: PK seek; complies.
  - `last_ms` + `id_to` page: `rowid>? AND rowid<?`; complies.
  - `query_can_frames` with `+cf.can_id IN (...)`: `SEARCH cf USING INTEGER PRIMARY KEY`, `CROSS JOIN` holding; complies.
  - `first_export_line_id` with `port` and `until_ts`: `idx_plot_name_line (name=? AND line_id<?)`; complies.
  - `export_sids` with `port`: index seek plus temp b-tree for DISTINCT; exempt (offloaded aggregate).
  - `iter_plot_export` with `l.port` added, two names: temp b-tree for ORDER BY; exempt (one cursor on a worker thread, unchanged by adding the column; one name has no temp b-tree).

### Class 31 - a field accepted and never read

Models and parameters of changed handlers (59 fields):

- `SessionBody` name, note: complies.
- `PurgeBody` session, before_ts, id_from, id_to, all, dry_run: complies.
- `/lines` 10 params: complies.
- `/lines/export` 10 params: complies (`since_id` does not reach the filename, as SPEC's naming rule only names time bounds).
- `/can/frames` 11 params: complies; `limit` unread on csv, exempt by SPEC.
- `/plot/channels` port: complies.
- `/plot/series` 8 params: complies.
- `/plot/export` 11 params: complies.

### Class 37 - read-await-act without a lock

Sites (7):

- Bundle handler: violates (A-4).
- `plot_channels` (two awaits, read only): complies.
- `plot_ports_safe`: complies (read only).
- `lines_export` / `can_frames` csv / `plot_export` re-resolving the session in `_effective_bounds` after awaits: complies (filename only; rows are scoped by the ids taken before).
- `sweep_tick._reclaim_backlog`: complies (synchronous, no await).
- `delete_range` and the sweeps: under `_sweep_lock`, unchanged; complies.
- `start_session` / `stop_session`: under `_session_lock`, unchanged; complies.

### Class 39 - raced task orphaned

- `grep -n "create_task\|ensure_future" server.py store.py`: 6 sites.
  - `/ws` pump and watch: complies (the new normal return on the sentinel reaches the same `finally` that cancels and awaits both).
  - `_do_wait` repeater: complies (`CaptureStopped` exits through the `finally` that cancels and awaits it).
  - store writer, initial sweep, retention tasks: not raced; exempt.

### Class 44 - relative bound per page

Sites (6):

- `/lines/export`, `/can/frames` csv, `/plot/export`, bundle: `id_to` frozen before the first store call; complies on value.
- `_iter_export_pages`: `until_ts` ceiling re-derived per page; violates on cost (A-1).
- `last_ms` under the internal freeze: violates (A-2).

### Class 47 - live-only scope

- Handlers taking a port (query, body or path), from `grep -n "port: str\|ports.get\|\.resolve(" server.py`: 16.
  - `/lines`, `/lines/export`, `/can/frames`, `/plot/channels`, `/plot/series`, `/plot/export`: exempt (retrospective).
  - `/ws`: complies (`ports.get` refusal).
  - `/wait`, `/assert` live: complies (`ports.resolve` when port or send is given).
  - `/send`, `/break`, `/cmd`: complies (`resolve`).
  - `/marker`: exempt (a marker row may name any alias grammar-valid port, SPEC 3.5).
  - `ports/{alias}` status, reconnect, disconnect: complies (`ports.get`).

### Class 49 - streamed-to-file export left partial

- Daemon side: 30 `/lines/export` downloads aborted after 0.3 s on the 1M capture: fds 15 -> 17 (the read connections), no traceback, WAL steady; complies. Mid-stream failure reaching the client as an incomplete chunked read was ruled out 2026-09-12 and not re-driven.
- CLI, `grep -n 'open(out' cli*.py`: 3 sites.
  - `cli_client.py:216`: complies, `started` set only after the open.
  - `cli.py:1531`: complies, guard armed after the open.
  - `cli.py:1637`: complies, guard armed after the open.

### Class 53 - bound sent the peer may not declare

- `grep 'params\["..."\] =\|p.set(\|p.append(\|q.append('` over `cli*.py` and `webui/*.js`: 51 sites.
  - `webui/*.js` (19): exempt, served by the same daemon.
  - `cli.py` (32): every name is declared by the 0.4.0 daemon. `since_ts`/`until_ts`, `format=csv`, `decode`/`changes`/`deadband` are gated by `_require_export_daemon`; `id_to`, `since_id`, `limit`, `port`, `chan`, `match`, `session`, `id`, `bus`, `last_ms` date from before the floor. Complies.
  - `id_to=0` (newly legal here) against a 0.4.0 daemon is a loud 422; exempt.

### Class 54 - one parameter, two builders

- Same 51 sites.
  - `/can/frames` `port`, `id`, `bus` are built at `cli.py:1795-1801` and `1842-1846`, both from `",".join(can_id)` at the call. Violates the letter (two Python builders), identical forms today, pre-existing since v0.4.0; CLI leg to rule.
  - `/lines` `id_to` and `since_id` at `cli.py:580` and `594` overwrite the dict the builder at 538-554 made: complies.
  - `webui/terminal.js:496` and `569-575` build `/lines` and `/lines/export` separately with the same repeated `chan` form: violates the letter, forms identical; web UI leg.
  - `exportrange.js` single builder: complies.

### Class 57 - per-board state keyed by name

Dicts and sets in the changed `server.py` code (13):

- `_csv_wide.last` (port, name): complies.
- `_csv_wide.values`: exempt (one line, one port).
- `bands`: exempt (keys a requested column).
- `_changes_long.last` (port, sid, name): complies.
- `_export_rows.dmaps`: complies (per port).
- `_plot_export_defs.decs`: complies (per port).
- `_wide_header.scans`: complies (per port).
- `_wide_header.labels`: exempt (a fixed header, SPEC 9.2 rule).
- `_renders_as_label` over all ports: exempt (documented per SPEC 2.5); gap is A-3.
- `plot_channels` `by_port`: complies; `merged` fallback exempt (detached board, SPEC 9.2).
- `/plot/export` `known`: complies (scoped by `port=`).
- `_plot_export_defs` priming: complies (pages the whole lookback, no newest-N).
- Bundle `plot_streams` groups by sid across ports: exempt by SPEC 3.4 ("port-unscoped"); see Decisions.

### Class 60 - constraint judged before normalising

- `grep -n "\.strip()\|\.lower()" server.py`: 11 lines.
  - 300 `SessionBody` validator: complies.
  - 1311 `name.strip()`: complies (already stripped).
  - 1113 `host`: complies (re-checks empty).
  - 1129 `db_path`: complies (max only; empty means default).
  - 1190 `dest`: complies (`parse_dest` strips before validating).
  - 1238, 1239 device, serial_number: complies (empty becomes None, then both-missing refused).
  - 532, 533, 545, 578, 711, 714, 715, 720: exempt (header parsing, no `Field`).

### Class 64 - float accepting inf and nan

- `grep -n "float" server.py` over `Query` and `BaseModel`: 9 sites, plus the deadband value.
  - `since_ts`/`until_ts` on `/lines` (1636-7), `/lines/export` (1674-5), `/can/frames` (1716-7), `/plot/export` (1825-6): complies (`_check_window`).
  - `PurgeBody.before_ts` (276): complies.
  - `_parse_deadband` value: complies on inf/nan; grammar gap is A-8.

## Decisions for the owner

- Fix the `since_ts`/`last_ms` id floor across a backwards clock step (A-12), or keep it documented once the SPEC sentence is corrected. An exact floor has the same cost shape as A-1's ceiling, so it wants the resolve-once design A-1 proposes.
- A bundle holds `_sweep_lock` for its whole build (17.7 s blocked a `DELETE ?data=true` here). On a large session that also holds the size-cap sweep, so the capture can overshoot `max_db_bytes` by the build time times the capture rate. Bound it, or accept as SPEC 3.4 states.
- Bundle `plot_<sid>.csv` is port-unscoped by SPEC 3.4. Two boards both declaring sid 0 with the same channel names interleave in one wide file with no port column and cannot be told apart. SPEC 2.5 makes a stream per port, so `plot_<port>_<sid>.csv` may be the intended reading.
- `lines/export?format=csv` leaves captured `raw` unguarded (SPEC 3.4, decided). Driven: a device line `=HYPERLINK("http://evil","x")` lands as a live formula cell. Listed only because `_csv_cell`'s own docstring names device text as the injection vector.

## The two questions

1. Least confident, rechecked:
   - A-1's magnitude. The first poller shared a process with the client, so its stall figures were noise. Re-driven with a separate-process poller: 403 ms stall per request at 1M rows. The export ratio (292.7 s against 11.3 s) is one run each, on a synthetic capture.
   - A-7 is reasoned only; the race window is a few bytecodes and was not reproduced.
   - A-5's `mcu daemon start` half is reasoned from the code, not driven.
   - Nothing here ran on Windows. The `out-of-range` filename branch (negative `ts` on Windows) and `_TempFileResponse` unlink are owed to the Windows leg.
2. What we had not thought about:
   - The consumers of A-1: the web UI clock-mode export sends `until_ts` (`exportrange.js:56`), `mcu lines --to` pages `/lines` on the loop, and `mcu log export --to` pages `/lines` or `/lines/export`, so all inherit the walk.
   - The new 400 for an unknown session changes what an older CLI or open browser tab sees on `/lines` and `/plot/series`, which used to return an empty 200. The CLI and web UI legs should check that each surfaces the error rather than rendering "no rows".
   - The in-memory export branch still materialises on the loop (known, untested); not re-examined.
   - Nothing else found.
