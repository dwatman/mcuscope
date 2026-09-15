# Fix: fix-diff review of the browser ids fix

Findings: `fixdiff-browser-ids.md`. Scratch, scripts, logs and downloads: `~/tt-data/fd2-ids/` (`revert_verify.py`, `revert.out`, `t_lanes_host_ids.py`, `t_can_ids.py`, `can_board.py`, `run-*.out`, `leg/`).

## Changes per finding

### H1: lanes past a trimmed id index

- `digital.js:127` `noteLaneId` also stores each sample's host time and sets `trimmed` when the index splices (`:142`); the pause snapshot copies both.
- `digital.js:414` `digitalShownWindow`: a stream whose index trimmed past the lower edge makes that side a host time (`fromTs`), the upper side stays the largest last id. A window ending before the trimmed index too goes by time on both sides.
- `digital.js:445` `hostAtTick` maps a tick edge to host time by interpolating between the nearest held samples either side (the port's lane vertices and its index), frozen while paused. With none below, the oldest held sample's time is the edge (nothing earlier is drawn).
- `exportrange.js:59` `params` now sends each side as given: `fromTs`, `toTs`, `sinceId`, `idTo` independently. Chart, pane and CAN callers produce the same URLs as before.
- The `ponytail:` comment is gone; the laneIds comment (`digital.js:47`) states the fallback. The ceiling test became "a lane window reaching past the trimmed index and every vertex exports from the oldest sample drawn" (`since_ts`, no `since_id`).

### Owner ruling (L2): host-base lanes by id

- The index's host times are nudged apart as `addSample` nudges a chart's (`+1e-4` per repeat), so a stream's chart and lanes place a burst alike. `digitalShownWindow` searches ticks or hosts by time base; the tail's upper edge is the newest sample (a burst's nudge passes the raw edge).
- Residual: the port's range is one contiguous id range across its lane streams, each nudged on its own. At a zoom edge inside a burst it adds rows of the slower stream up to that burst's edge, never drops a drawn one (browser: 33 extra within 1.5 ms, 0 missing).

### Owner ruling: CAN table by id

- `can.js:173` rows keep `lastId`; `can.js:619` `canShownWindow` returns `{sinceId}` (oldest shown row's last frame minus one); the freeze watermark is `id_to`.

### M1: `!pd` in the first connect's backfill

- `api.js:360,391,549`: `runBackfill` hands its rows to `seedPlotHistory`, which passes them to `plotSeed`.
- `plots.js:382` `plotSeed(entries, rows)` parses the newest valid event `!pd` per port and sid among the rows (parse only, nothing cached); `seedGroup` (`:416`) prefers it over `plotDefs`.

### M2: filenames of id-bounded exports

- `server.py:2788` `_named_by_ids`: with `since_id`, `from` is the later of the time bound and the first line after `since_id` in the id range, `to` the newest line at or below the frozen `id_to`, never backwards. An empty id range keeps the time-bound name.
- Applied to `/plot/export` (`:1907`), `/lines/export` (`:1728`) and `/can/frames` CSV (`:1777`). `/lines/export` had the gap for any `since_id` without time bounds (CLI `--since-id`); `/can/frames` now that the table sends ids.
- Residual: the lanes' H1 fallback sends `since_ts` and `id_to`, so its name is `<from>-end` ("`id_to` alone does not change the name"). Owner pick: name `to` from `id_to` as well, or accept.

### L1, L3, L4, L5, L6

- L1 `exportdlg.js:136`: paused but nothing inside reads "the shown window holds nothing to export".
- L3 `digital.js:838`: resume drops the index snapshot again.
- L4 `digital.js:105`: the id is noted only when a lane took the point.
- L5: SPEC wording below.
- L6: `digitalIngest`'s `stream` has no default; comment at `digital.js:479` reads "the ids differ per port"; Python cases added (below).
- Not changed: older digital test files call `digitalIngest` without `stream` and without an id, so it is never read there. `plotSeed`'s `rows = []` default serves 15 existing seed tests in other files.

## Tests

- `tests/test_plot_export_since_id.py` (+5): `since_id` with `last_ms` anchored on `id_to`, with `until_ts`, `decode=1` wide header after a redefinition before the cursor (`ob.led`, control `io.led`), plot filename cases (both sides, no `id_to`, tighter `since_ts`, past the last line, empty range, `id_to` alone), `/lines/export` and `/can/frames` names.
- `tests/webui_js/fixdiff_browser_ids.test.mjs` (new, 13): H1 tick base lower cut, both sides cut, index as the upper bracket, host base cut, another port not bracketing, trim while paused for vertices and for the index, host tail burst, untrimmed keeps ids, snapshot trimmed flag, L4 cap, L1 title, M1 newest backfill `!pd` (older valid, invalid, other port, non-event rows).
- `tests/webui_js/seed_backfill_pd_order.test.mjs` (new, 1): the review's probe through `connectWs`, chips `zt, za`.
- Changed to the rulings, not weakened: `fix_browser_ids` (host-base burst test also checks lanes; ceiling test), `exportrange` (+1, mixed sides), `prerelease_fixdiff_webui` FW-3, `prerelease_webui-panes_can` (id assertions, +1 filter hides every row), `prerelease_webui-panes_plots` (lanes window by id, a sample outside 30 s added), `rulings_panes_export` (two lanes assertions by id).
- Selections over 100k ids compare `{n, first, last}`: a failing `deepEqual` diff ran node out of memory (SIGKILL), which is not a failure unique to a path.

## Revert results

`revert_verify.py` (copy, revert, run, restore, hash-check; `revert.out`). api.js lines reverted by reverse replacement, not copy, since another agent edits that file.

| Branch | Caught by |
|---|---|
| S1 no `since_id` keeps the name | plot filename test |
| S2 empty id range keeps the name | 500, `since_id` with `until_ts` |
| S3 `from` tighter of time and line | plot filename test |
| S4 never backwards | plot filename test |
| S5 `to` from the last line | plot filename test |
| S6, S7 lines and CAN named | lines and CAN filename test |
| S8 plot named | plot filename test |
| D1 id only when a lane took it | cap refused stream |
| D2 host nudge | host-base burst straddle (lanes) |
| D3 trimmed flag, D4 snapshot flag, D6 lower cut, D11 fallback return | H1 tests and the ceiling test |
| D5 resume drops index snapshot (L3) | NOT CAUGHT: memory only, nothing reads it while live |
| D7 upper cut | window ending before the index |
| D8 host base needs no tick map | host base cut |
| D9 tail upper edge | host tail burst |
| D10 xs per base | 4 host-base tests |
| D12 vertex brackets, D19 interpolation | H1 tick tests |
| D13 index bracket | index as upper bracket; trim while paused (index) |
| D14 no sample below | ceiling test |
| D15, D16 frozen reads in `hostAtTick` | trim while paused (vertices), (index) |
| D17, D18 port filters in `hostAtTick` | another port |
| E1, E2, E3 params sides | exportrange and panel tests |
| X1 paused title | L1 test |
| C1 `lastId`, C2 CAN by id | FW-3, paused table export |
| C3 no shown row | filter hides every row |
| M1a to M1d seed order | backfill `!pd` test |
| api.js `rows` to `seedPlotHistory`, and to `plotSeed` | `seed_backfill_pd_order` |

## Browser

HTTP 8594, TCP 9941 (bursts) and 9942 (CAN); one Chromium at a time; no console or page errors; every PID confirmed gone.

- `t_lead1_ids.py tick`: all six MATCH (tail, zoom, edge; chart and lanes).
- `t_lead1_ids.py host`: charts MATCH; tail and zoom lanes MATCH; edge lanes 72 extra, 0 missing against its raw-ts expectation, which is the pre-ruling model.
- `t_lanes_host_ids.py host` (same harness, lanes expected by per-stream nudged host): all MATCH except edge lanes, 33 extra, 0 missing, extras within 0.45 ms below and 1.45 ms above the window (the L2 residual).
- `t_can_ids.py`: 3 of 3 MATCH at different points of the 0x300 cycle; the old ts bound would add 2 burst mates each time; filenames carry both stamps. Positive control with `canShownWindow` reverted to ts: 3 of 3 MISMATCH, 2 extra each. Restored and hash-checked.

## Suites

Per the coordinator, single files only after the OOM; the full suite is the coordinator's.

- pytest files: `test_plot_export_since_id` 19, `test_export_lines_can` 28, `test_cli` 167, `test_hardening` 82, `test_prerelease_cli_fixes` 76, `test_plot` 18, `test_review_r2_server` 23, `test_prerelease_fixdiff_py` 40, `test_sweep_cli_closed_output` 20, `test_session_bundle` 9, `test_daemon_r2026_09_12_server` 22, `test_plot_export_decode` 22: all pass.
- JS files, singly: `fixdiff_browser_ids` 13, `fix_browser_ids` 17, `seed_backfill_pd_order` 1, `exportrange` 13, `exportdlg` 22, `prerelease_fixdiff_webui` 18, `prerelease_webui-panes_can` 12, `prerelease_webui-panes_plots` 7, `rulings_panes_export` 10: all pass.
- Before the instruction, one whole `node --test` run: 895 tests, 894 pass. The failure is not mine: `api_ws_backoff.test.mjs` "staging is capped while the backfill runs" (expected 5000, actual 5050), in `api.js` staging, which the other agent owns; its fixture has no rows, so the seed is never called.
- `ruff check .`: clean. `test_webui_js.py` not run (instruction).

## Proposed CHANGELOG

Fixed:

- Web UI: the lanes' "Shown window" export covers the whole window drawn, where a long window at a high sample rate exported only its newest part; past the id index the lower edge goes by time.
- Web UI: the lanes under the host base, and the paused CAN table, export their shown window by line id, so a burst sharing one timestamp is no longer split or widened at the edge.
- Web UI: after a reload whose newest lines hold a stream's `!pd`, its chips and lanes follow that field order.
- Web UI: a paused panel whose window holds nothing says so, instead of telling the user to pause.
- Daemon: `/plot/export`, `/lines/export` and `/can/frames` exports bounded by `since_id` name their file from the lines they cover, not `start-end`.

## Proposed SPEC lines

3.4, replacing the last sentence of the filename paragraph (`SPEC.md:874`):

- "`from`/`to` are the effective bounds (the session span narrowed by `since_ts`/`until_ts`/`last_ms`) as local time `YYYYMMDDTHHMMSS`, or `start`/`end` for an unbounded side; `id_to` alone does not change the name. With `since_id`, `from` is also no earlier than the first line after it, and `to` is the newest line at or below the export's upper bound; an empty id range keeps the name above."

9.1, replacing `SPEC.md:1604-1606`:

- "A chart sends `since_id` one below the first drawn sample's id and `id_to` the last one's, under every time base: the samples of one serial burst share one timestamp."
- "The lanes send the same for the exported port's samples inside the window, and a port with none exports nothing. Where the lanes' id index no longer reaches the window's lower edge, that side is `since_ts` at the edge's host time, exact to one burst."
- "The CAN table sends `since_id` one below its oldest shown row's last frame; its freeze watermark is the upper bound."
- "A terminal pane sends its host-time edges and `since_id` one below its first row's id."

9.2, beside `SPEC.md:1745`: "It takes that order from the newest `!pd` among the rows the page loads with, or else from the stored one before them."
