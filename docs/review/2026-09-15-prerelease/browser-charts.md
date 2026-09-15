# Browser leg: charts

Headless Chromium 151 (playwright 1.62.0), standalone `mcu-sim` boards over `socket://`, a fresh daemon and capture per item.
Scripts and logs in `~/tt-data/browser-leg/charts/`.

- PASS D-2: chart `stream 0`, a `sine=` filtered pane and the lanes, each paused after a later marker on a stopped stream.
  - Chart CSV: 601 ticks, identical to the frozen samples in the 30 s window (the page held 275 older ones), values match.
  - Pane jsonl: 876 ids, identical to the pane's rows. Lanes: every lane's samples and transitions match the page in the window.
  - `id_to` is the marker-time freeze everywhere. Script: `d2.py`.
- PASS Shown window under zoom: a real mouse drag on `.u-over` set `getZoom()` and paused every surface.
  - Host base: chart CSV (179) and lanes CSV equal the frozen samples inside the zoom; after `30s`, CSV (600) equals the selector's window, freeze kept.
  - Tick base: CSV (209) equals the samples whose tick is inside the zoom, edges sent as their host times; after `30s`, CSV (601) spans the last 30000 ms of ticks. Script: `zoom.py`.
- PASS Digital pause: pause all with no lanes, then the board started.
  - 4 lanes built with empty snapshots over buffered samples, `digitalRightEdge()` null, `#dRulerLabel` empty, ruler and lane canvases hold no pixels, watermark kept at the pause-time id.
  - Resume all: lanes draw their samples, ruler reads `x: host`, canvases inked. Script: `dpause.py`.
- PASS FW-4: pane cleared on `--flood` boards at 250, 1500 and 1800 lines/s, paused, shown window exported as jsonl.
  - No id at or below `clearId` and no cleared flood sequence number in the file; file ids equal the pane's rows.
  - The mid-burst edge (`since_id`) was not exercised: 40 clears at 1800 lines/s never left a cleared row sharing a timestamp with the first kept row, since a `/ws` frame carries a whole read burst. Script: `fw4.py`.
- FAIL Clear-all during a backfill: `page.route` held the first `/lines` backfill, clear all was pressed, then the backfill was released.
  - Pre-clear samples come back on every chart and lane, and the panes show pre-clear lines (defects A and B). Later lines and samples do arrive.
  - Failed on both runs of both modes, so not timing. Script: `clearbf.py response|request`.

The checklist says the clear-all item is not runnable with the sim; `page.route` makes it runnable.
No console errors or page errors in any run (the only warnings are Chromium's `getImageData` readback notices from `dpause.py`).

## Defects

### A. The plot history seed lands on charts cleared during the backfill

- Repro: 12 s of `mcu-sim --plot` history. Load the page with `/lines` held, press clear all, release.
- Observed: each chart holds about 250 samples, and each lane a matching share, from before the page load. That is the seed.
  - Both modes: the `/lines` request held unsent, or sent with its response held.
- Expected: SPEC 9.2 says "A seed answering after a clear-all is dropped", and SPEC 9.1 says a clear during the backfill covers what it delivers.
- Cause: `seedPlotHistory` takes `const cleared = plotSeedGen()` (api.js:344) only when it starts, after `runBackfill` has awaited `/lines` and `seedPlotDefs`. A clear during those awaits moves the token before the snapshot, so `plotSeed` runs. The `chartClears` snapshot in `runBackfill` (api.js:495) is not used for the seed.

### B. Live rows staged behind the backfill come back after the clear

This happens in response mode only: the daemon answered before the clear and the answer was slow to reach the page.

- Repro: as in A, but let `/lines` reach the daemon at once and hold only its response in the browser for about 4.5 s.
- Observed in panes: 321 to 333 rows whose ids are at or below the daemon's newest id at the click.
  - They are the lines that arrived over `/ws` while the backfill was out.
  - Scrolled to the top, the pane DOM shows them (60 of 60 rows).
- Observed on charts and lanes: about 66 samples from between the page load and the click.
- Cause: after the loop, `runBackfill` raises `clearId` only as far as the backfill's newest id (api.js:553-555). `drainStaging` then feeds the staged rows through `routeLiveRow` (api.js:109-112), which has no clear gate for panes, charts or lanes.
- SPEC 9.1 (line 1524) promises only that the clear covers "the rows that backfill delivers", and staged `/ws` rows fall outside that wording. This is either a defect or a SPEC gap: owner should pick.
