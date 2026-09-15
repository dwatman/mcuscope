# Fix: clear during a backfill (browser-charts defects A and B)

Owner ruling applied: a clear covers everything that reached the page before the click (backfill rows, the plot history seed, live `/ws` rows staged behind the backfill); rows arriving after the click still show.

## What changed (`host/mcuscope/webui/api.js`)

- A: `seedPlotHistory(gen, anchor, cleared)` (api.js:350) takes the chart token from its caller; `runBackfill` passes `clears.charts`, read before its first await (api.js:505, 535).
  - The seed no longer reads `plotSeedGen()` itself, so a clear during `/lines` or the `!pd` seed drops it.
- `clearTokens()` (api.js:490): every surface's clear token in one snapshot, used by `runBackfill` and the staging area.
- B: staging records arrival order against clears.
  - `armStaging` (api.js:683) snapshots the tokens; both staging sites use it (connect api.js:628, capture reset re-seed api.js:207).
  - `noteClears` (api.js:688) runs per staged row (`stageRow`, api.js:673) and once at drain (api.js:722). It records per surface how many staged rows had arrived at its latest clear.
  - `feedStaged(row, i, cut)` (api.js:744): a row below a pane's cut raises that pane's `clearId` to its id, so later rebuilds hide it too. The CAN and chart cuts go down as flags.
  - `routeLiveRow(row, canCleared, chartsCleared)` (api.js:111): skips `canIngest`/`plotIngest` for a covered row, passing `!pd` rows through `isPlotDef` as the backfill does. The pane loop skips `row.id <= p.clearId` (api.js:123), which a live row never is.
  - Rows still reach the shared buffer, as backfill rows do.
- Ids cannot mark the click: the watermark does not move while staging, so a clear's `clearId` sits below every staged row. Arrival order within a capture segment matches id order, so the raised `clearId` never covers a post-click row.
- Covered: clear-all, a single pane's clear, the CAN clear; first connect, reconnect backfill, and the capture reset re-seed.

## Tests

`host/tests/webui_js/clear_staged_backfill.test.mjs`, 13 tests, each passes run alone.

- A: control; clear-all while `/lines`, the `!pd` seed, or `/plot/series` is held (charts and lanes empty).
- B: control (every staged row everywhere); clear-all between two staged frames (panes, rebuilt pane, chart, lane keep only later rows; `!pd` staged before the click still decodes a later `!ps`; buffer holds all).
- B: CAN clear between frames; one pane's clear; clear-all after the last staged row then a live row; panes added mid backfill (one cleared); reconnect backfill; capture reset re-seed, with its no-clear control.
- Full JS suite: 850 pass, 1 fail, `rulings_chrome_settings.test.mjs` E-9 (session-row export double click), outside this path and in files other agents are editing.

## Revert verification

Script `~/tt-data/browser-leg/clear/revert.py`, log `revert.log`; each branch reverted alone, api.js restored from copy and compared.

| Revert | Fails | Unique text |
|---|---|---|
| R1 seed reads its own token | 2 | "landed on charts cleared during /lines", "during the !pd seed" |
| R2 seed gate after `/plot/series` | 3 | "landed on charts cleared during /plot/series" |
| R3 pane `clearId` gate in `routeLiveRow` | 6 | "the drain queued rows staged before the clear into a pane" |
| R4 `clearId` raise in `feedStaged` | 6 | "a rebuild from the buffer brought the cleared rows back" |
| R5 `canCleared` | 1 | "a CAN frame staged before the CAN clear came back" |
| R6 `chartsCleared` | 2 | "the chart holds a sample staged before the clear" |
| R7 `isPlotDef` exemption | 2 | "the cleared staged !pd did not reach the definition cache" |
| R8 `noteClears` at drain | 1 | "a clear after the last staged row let staged rows through" |
| R9 `noteClears` per staged row | 6 | "a CAN frame staged before the CAN clear came back" (with pane failures) |
| R10 `?? 0` for a pane added mid staging | 1 | "a pane added during the backfill lost staged rows" |

## Browser rerun

`clearbf.py` copied to `~/tt-data/browser-leg/clear/` with `WORK` pointed there (the charts leg's run dirs are left intact); HTTP 8592, sim on TCP 9922.

- Fixed: PASS in both modes. Panes hold no id at or below the click, DOM 0 of 60 pre-clear, charts and lanes 0 pre-clear samples, later lines and samples arrive, no console errors.
- Positive control (`ctl.py`, R1 and R3 to R6 reverted, then restored): FAIL in both modes as reported. Response: 321 pre-clear pane rows, DOM 60 of 60, 330 of 485 chart samples. Request: 335 of 488 chart samples, lanes likewise.
- No process left running (8 PIDs in `pids.log` checked).

## Sweep

1. Token snapshotted by a callee after its caller's awaits: only `seedPlotHistory` (fixed). Every other one (`terminal.js` `loadHistoryPage` `historyGen`, `exportdlg.js` `dialogGen`/`fillGen`/`openCapture`, `statusbar.js` `failGen`/`sesGen`, `cmdbar.js` `cmdGen`/`markerGen`, api.js `seedPlotDefs`/`fetchSince` `gen`) is read before its own first await.
2. Clear gate on one path but not a queue beside it: only the staging drain (fixed). Pane `queue`, `pending` and `frozenRows`, history paging (`since_id` floor, `historyGen`), the high-rate rebuild, `canFrozen`, and chart and lane state are all reset or `clearId`-gated by the clear.

## Residuals (not changed)

- Request mode: rows captured between the click and the moment the delayed `/lines` reaches the daemon stay hidden (here 1.5 s, the script's hold). This is `runBackfill`'s existing one-boolean precedent: the ruling counts backfill-delivered rows as covered.
- A capture token staged before the click and applied at drain wipes and re-seeds. New-capture rows staged before the click then come back through the re-seed, because the reset runs after the click. Needs a token, a clear-all and the first backfill in one window. Owner should pick whether to cover it.

## Proposed CHANGELOG (`Fixed`)

- Web UI: a clear-all pressed while the page's backfill is loading no longer brings back the plot history seed on the charts and digital lanes.
- Web UI: a clear (a pane, clear-all, or the CAN table's) pressed while the backfill is loading also covers the live lines that arrived meanwhile; lines arriving after the click still show.

## Proposed SPEC

- 9.1 (line 1524), replace the clause after "never the database;" with: "a clear (a pane, clear-all, or the CAN table's) clicked while the page's backfill is still loading also covers the rows that backfill delivers and the live rows that reached the page while it loaded; rows arriving after the click still show."
- 9.2 (line 1769): "A seed answering after a clear-all is dropped, including a clear-all clicked while the backfill ahead of the seed was loading."
