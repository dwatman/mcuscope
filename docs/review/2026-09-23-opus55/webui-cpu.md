# webui-cpu: CPU cost of the web UI

Setup: own daemon on 18760 (`--config` in scratch, `MCUSCOPE_*_DIR` in scratch), Python fake board on `socket://127.0.0.1:18762` with rates set at runtime, headless Chromium 1.62.0 at 1600x1000.
Figures are main-thread `TaskDuration` in ms per second of wall time (so 100 = 10% of one core), from CDP `Performance.getMetrics`, median of 3 windows of 8-10 s; profile figures are inclusive ms/s from `Profiler` at 250 us.
The machine was shared, so absolute numbers drift 20-40% between runs; every saving below is an A/B in the same run (original modules against patched copies served through `page.route`), repeated twice.
Rates: "realistic" = 130 lines/s (20 Hz analog, 20 Hz enum+bits, 50 CAN frames/s over 8 ids, 20 debug lines/s); "high" = 1600 lines/s (800 Hz analog, 100 Hz enum+bits, 400 CAN/s over 30 ids, 200 debug/s).
Scripts, logs and raw JSONL: `~/tt-data/mcuscope-2026-09-23/webui-cpu/` (`measure.py`, `make_patched.py`, `probe_term.py`, `backfill.py`, `idle_probe.py`, `shot.py`; results `run1/run2/ab-*.jsonl`).

Headline: after a few minutes on a bench the terminal's cheap append path is permanently off, and a hidden tab burns more ingest CPU than a visible one. Both fixes are two lines each. Together they halve the realistic-rate cost and cut a hidden 5-pane tab by 93%.

## WEBUI-CPU-1: once a pane holds VIEW_MAX rows, every flush rebuilds the whole visible window

- MEDIUM, CONFIRMED. `host/mcuscope/webui/terminal.js:405` (the trim in `flush`) against `shiftWindow` at `terminal.js:248-257`.
- Failure: when `pane.rows` reaches `VIEW_MAX` (5000), each flush splices the head off. `first` then stays at `5000 - visCount` and `shift` is 0, so the identity check fails on every flush. `render` falls back to `replaceChildren` of the full ~60-row window 30 times a second.
  - At 20 lines/s a pane gets there in about 4 minutes, so on a bench left open this is the steady state, not an edge case.
- Repro: `probe_term.py` watches `vlist` with a MutationObserver for 5 s at 100 lines/s.
  - Before VIEW_MAX: 503 elements created for 503 rows, 0 full rebuilds.
  - At VIEW_MAX: 5520 elements created for about 500 rows, 92 of 92 flushes were full rebuilds.
- Cost at realistic rate, VIEW_MAX reached (`ab-orig*.jsonl` against `ab-patched*.jsonl`, phase `realistic-late`):
  - main-thread task: 247 / 219 ms/s, patched 110 / 131;
  - layout: 68 / 58 ms/s, patched 17 / 20;
  - `flush` inclusive: 111 / 86 ms/s, patched 28 / 26;
  - renderer process CPU (all threads): 461 / 412 ms/s, patched 269 / 312.
- Fix: keep the window indices on the same rows across the trim. With the patch the probe reads 503 elements for 500 rows and 0 rebuilds, with no page errors. The identity check stays as the guard.
  ```js
  if (pane.rows.length > VIEW_MAX) {
    const cut = pane.rows.length - VIEW_MAX;
    pane.rows.splice(0, cut);
    pane.winFirst -= cut; pane.winLast -= cut;
  }
  ```
- Saving: about 75% of the terminal's cost at bench rates (about 80-110 ms/s main thread per live pane at 130 lines/s), for every pane.
  - At 1000 lines/s the saving is smaller (73-85 down to 48-72 ms/s), because most of the window is new rows either way.

## WEBUI-CPU-2: a hidden tab trims each pane's queue one row at a time, so it costs more than a visible one

- MEDIUM, CONFIRMED. `host/mcuscope/webui/api.js:141`.
- Failure: `flush` returns early while `document.hidden`, so each live pane's queue fills to `VIEW_MAX`. From then on every arriving row runs `p.queue.splice(0, 1)` on a 5000-element array, for every pane.
  - This is the O(length)-per-row trim that `state.js` `BUFFER_SLACK` and `PLOT_SLACK` were introduced to remove; this one site was missed.
  - Background tabs are not throttled for WebSocket messages, so this runs at the full line rate.
- Repro: `measure.py term` phase `t-5panes-regex-hidden`, with 5 panes at 1000 lines/s and hidden emulated (see Not covered).
  - Hidden: `routeLiveRow` inclusive 307 ms/s. Visible: 15 ms/s.
- A/B (`dbg1000-5panes-hidden`): 160 / 165 ms/s task, patched 11 / 13.
  - With one pane at the high rate (`high-hidden`): 76 / 73, patched 37 / 25. That figure includes WEBUI-CPU-5.
- Fix: block trim, as `pushBuffer` does: `if (p.queue.length > VIEW_MAX + BUFFER_SLACK) p.queue.splice(0, p.queue.length - VIEW_MAX);`.
  - `BUFFER_SLACK` is already imported. The flush trims to exactly VIEW_MAX anyway.
  - Alternative: queue nothing while hidden and `rebuild` on `visibilitychange`, since the shared buffer holds the same 5000 rows.
- Saving: 93% of a hidden tab's cost with 5 panes, about 50% with one pane.

## WEBUI-CPU-3: charts hand uPlot every sample in the window, and nothing decimates them

- MEDIUM, CONFIRMED. `host/mcuscope/webui/plots.js:1020-1047` (`currentData`).
- Failure: an 800 Hz stream in the default 30 s window is 24,004 points per series. With 3 series, that is 72k `lineTo` calls 5 times a second on a chart 342 px wide.
  - The stepped path builder does not reduce points per pixel.
- Measured (`high`): uPlot `series` inclusive is 104 / 113 ms/s. That is the largest single consumer at high rate, about 40% of script time (run1: 197 ms/s).
- Fix, prototyped in `make_patched.py` (DECIMATE):
  - When the slice holds more than 4 points per pixel, keep, for each pixel column, the first and last index plus each series' min, max and first null.
  - Take the union across series, so the chart still has one x array.
- Measured with the patch: `series` 11 / 7 ms/s, down about 90%. uPlot gets 1082 points instead of 24,004.
  - `chart-orig.png` and `chart-patched.png` look the same for this dense signal.
- Caveats for the implementer:
  - The cursor snaps to kept samples only.
  - Readouts stay true sample values.
  - The shown-window export reads `chartDrawData`, not `u.data`, so it is unaffected.
- At 20 Hz the chart costs 4-5 ms/s; this matters only for fast streams.

## WEBUI-CPU-4: collapsing the sidebar does not stop the charts or the CAN table

- MEDIUM, CONFIRMED. `host/mcuscope/webui/plots.js:1058-1059` and `plots.js:1333`; `host/mcuscope/webui/can.js:570-573`.
- Failure: with `.workspace.collapsed`, each chart's `canvasEl.clientWidth` reads 4, not 0, so the `w <= 0` skip misses. Lanes read 0 and are skipped.
  - `redrawPlots` then `setSize`s the chart to 4 px and keeps drawing the whole window into it.
  - `canVisible()` checks only `data-view`, so the CAN table keeps re-rendering into the zero-width column.
- Repro: `probe_term.py` prints chart widths `[342]` open and `[4]` collapsed.
  - run1: main-thread task is 396 ms/s with the sidebar open and 488 collapsed, so collapsing to save CPU saved nothing.
- A/B (`high-collapsed`): script 107 / 123 ms/s, patched 40 / 35.
- Fix:
  - Skip `redrawTick` while `#workspace` has `collapsed`, next to the `data-view === "can"` skip.
  - Add `&& !collapsed` to `canVisible()`.
  - Reopen already reaches `resizePlots` through the next tick; the CAN table repaints on its next 1 s tick.
- Saving: about 70-90 ms/s at high rate; the chart share of whatever rate is flowing.

## WEBUI-CPU-5: every `!ps` line is decoded twice

- LOW, CONFIRMED (profile). `host/mcuscope/webui/state.js:242` (`computeTick` calls `hooks.plotSampleTick`) and `plots.js:48-54`, then `plots.js:261` (`plotIngest`).
- Failure: `pushBuffer` runs `noteRowTick`, then `lineTick`, then `plotSampleTick`, which runs a full `decodePlotSample`. `plotIngest` then decodes the same line again.
- Measured (run1 `high-hidden`, where the cost is ingest only): `hooks.plotSampleTick` inclusive 17.6 ms/s of the 60.6 ms/s `onmessage` total, about 29%.
- Fix: memoise the last decode. It is a pure function of `(raw, def)`, and the two calls are adjacent for the same row.
  - Prototyped as `decodeOnce` in `make_patched.py`. This keeps the single-decoder design that the `state.js` comment insists on.
- Saving: about 30% of the per-row ingest cost on plot-heavy traffic, visible or hidden.

## WEBUI-CPU-6: the fold cue forces a layout and invalidates style 5 times a second, even when idle

- LOW, CONFIRMED. `host/mcuscope/webui/plots.js:1300-1313` (`syncFoldCue`, called from `redrawTick` every 200 ms).
- Failure:
  - It reads `getBoundingClientRect` for every chart after the draws, which forces a layout.
  - It writes `btn.hidden` and `btn.title` unconditionally. A same-value write still invalidates style: `idle_probe.py` measured 100 same-value writes and 100 style recalcs.
- Measured: an idle page with charts, board silent, does 6.8 style recalcs/s, against 0.8/s on an empty page. `syncFoldCue` costs about 3 ms/s while streaming and about 0.5-1 ms/s idle.
- Fix:
  - Write only on change, as `textContent` already does two lines above.
  - Run the cue from the events that move the fold (chart added, removed or collapsed, resize, scroll) instead of the 5 Hz tick.
- Saving: small. The idle page drops from about 7 to about 5 ms/s main thread.

## WEBUI-CPU-7: with a regex set, every render rescans the whole buffer for the "N / M lines" readout

- LOW, CONFIRMED (profile). `host/mcuscope/webui/terminal.js:146-156` (`updateShown`), called from every `render`, 30 times a second per live pane.
- Measured at 1000 lines/s: `updateShown` inclusive 4.4 ms/s with 1 pane and 14.2 ms/s with 5 panes (`run2.jsonl`, `t-dbg1000-regex`, `t-dbg1000-5panes-regex`).
- Fix: keep the in-scope count incrementally (add on ingest, drop on buffer trim), or recompute it at most once a second.
- Saving: about 3 ms/s per pane with a pattern.

## WEBUI-CPU-8: digital lanes draw every sub-pixel segment, with one `stroke()` and a save/clip/restore per enum segment

- LOW, SUSPECTED (cost measured, fix not prototyped). `host/mcuscope/webui/digital.js:622-670` (`drawBits`, `drawEnum`).
- Measured: `redrawDigital` inclusive is 28-57 ms/s at the high rate, where the bits lane toggles at 100 Hz: about 3000 segments on 230 px, redrawn 5 times a second.
- Fix:
  - Merge consecutive segments narrower than about 1.5 px into one filled "busy" block.
  - In `drawEnum`, batch all rails and crossings into one path per lane instead of one `beginPath`/`stroke` per segment.
- Estimated saving: most of that 28-57 ms/s for fast-toggling lanes. It would be confirmed by the same A/B at `psd=100`.

## Checked and fine

- Idle, empty page: 2-3 ms/s main thread, 0.8 layouts/s; every `setInterval` is gated on `document.hidden` (`run1` `idle-empty`).
- Idle, board silent with charts, lanes and CAN rows: 7-13 ms/s. Lanes do not repaint with the edge unchanged, and the CAN table only ages its cells (`idle_probe.py`, `run1` `idle-populated`).
- Pause-all at the high rate: 62-71 ms/s, all of it ingest. There is no DOM work; layout 0.6 ms/s (`high-paused`, `t-5panes-paused`).
- Hidden tab (emulated): layout and style both go to 0/s. Terminal flush, chart and lane redraws, CAN, rate and status polls all stop (`high-hidden`).
- CAN-only view at the high rate: the chart loop is skipped, 137 ms/s against 397 (`high-view-can`).
- High-rate shed at 3000 lines/s: 28 ms/s, the panes are not fed (`t-flood3000`).
- Page-load backfill (200 rows, `!pd` seed, 32-channel plot seed) and a reconnect backfill after a 12 s outage at 780 lines/s (5000 rows paged): neither is a measurable burst. The first 12 s after load cost the same as the next 12 s of streaming (2.5-2.8 s each) (`backfill.py`, `backfill.jsonl`).
- No CSS animations; no `requestAnimationFrame` loop at steady state (0 rAF/s counted).
- `JSON.parse` per frame, plus the per-row CAN parse: `sock.onmessage` self 3-8 ms/s at 1000-1600 lines/s.
- CAN table: rebuilt only when the row set changes; the 1 s tick rewrites changed cells only.
- The status poll changes the port chips only when their signature changes.

## Not covered

- Real hidden-tab behaviour.
  - Headless Chromium reports every tab visible. Hidden was emulated by overriding `document.hidden`/`visibilityState` and dispatching `visibilitychange`, so the page's own gates ran, but browser timer throttling and rAF suspension did not.
  - A headed run would pop a window on the owner's desktop, so none was made.
- GPU raster. Headless rasterises in software, so the renderer and GPU process figures overstate paint relative to a desktop browser; the main-thread figures do not have that bias.
- Windows, Firefox, a second port, more than 4 lanes, and hover or cursor cost under load.
- The prototyped fixes (`patched/`) were exercised by the A/B runs with no page errors.
  - Only F1 got a correctness probe (identity of DOM rows); no JS test was run.
  - Decimation was eyeballed on one dense signal only; a fast stream with sparse steps was not checked.
