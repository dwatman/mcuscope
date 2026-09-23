# Fix-diff leg: panes half of the web UI

HEAD: `b994076c88c875ad43c30789cda6039d147b7abe`

Scope: the `6e4f6f7..HEAD` hunks of `plots.js`, `digital.js`, `timewindow.js`, `terminal.js`, `pane.js`, `can.js`, `freeze.js` and the `webui_js` tests that exercise them.
Scratch, probes and the mutation runner: `~/tt-data/mcuscope-2026-09-24/fixdiff/panes/` (the mutable copy is `../panes-copy/`).

## Findings

### P-1 (high): a saved hidden sidebar stops every chart, lane and CAN redraw below 860 px, with no control to undo it

- Where: `plots.js:1420` `plotsShown()` and `can.js:576` `canVisible()` treat `#workspace.collapsed` as "not on screen".
- Defect: under `@media (max-width: 860px)` (`style.css:438`) the workspace is one column that ignores `.collapsed`, so the sidebar is on screen while both gates say it is not.
- Failure scenario:
  - The user hides the sidebar on a wide window (persisted as `layout.hidden`).
  - They later open the UI in a narrower window (half a laptop screen).
  - The Plots and CAN sections are visible and never draw.
  - The same media query hides `#collapseBtn` and `.reopen`, so nothing on the page undoes it.
- Confirmed: driven in headless Chromium against `mcuscoped --sim`, viewport 800 px with `mcuscope.layout = {hidden: true}` (`narrow.py`).
  - Sidebar measured 800x290 px on screen, with 0 uPlot instances.
  - The CAN table stayed "No CAN frames yet" over 7 s while frames flowed.
  - Both buttons had `display: none`.
  - Control, same width not hidden: 1 chart built and the CAN table updating.
- Suggested fix: gate on whether the sidebar has a box, e.g. `sidebar.clientWidth > 0`, in one shared helper used by both modules.
  - `redrawPlots` already reads `clientWidth` per chart on that tick, so the extra read forces no additional layout.
  - Add a test that collapses with the narrow layout in force.

### P-2 (medium): decimation columns are sized over the left margin sample, so a stream resuming after a silence draws in coarse blocks

- Where: `timewindow.js:138` takes `x0 = xs[lo]`, and `plots.js:1104` makes `xs[lo]` the sample before the window, however old it is.
- Defect: when the window's left edge falls inside a silence, the column width is the whole silence plus the window divided by the pixels.
  - The visible part then gets a handful of columns.
- Failure scenario: a stream at 1 kHz is silent for 10 min (a board reset, a bursty logger) and then runs for 30 s in a 30 s window on a 342 px chart.
  - uPlot gets 43 points, with kept samples up to 21 px apart inside the window.
  - A 0.1 Hz sine draws as a coarse min/max staircase.
  - Same on the right in a zoom, where `hi` includes the sample after `z.max`.
- Confirmed: driven through `plotIngest` and `currentData(chart, 342)` in the node stub.
  - Result: `points=43`, `maxGapInWindow_s=1.841`, i.e. 21 px.
  - Same result from `decimateColumns` directly, `far_edge.mjs`: 68 kept, 15 px.
  - Control, continuous stream: 745 kept, 1.0 px.
- Suggested fix: size the columns from the window, not the slice.
  - Pass the window's `[xmin, xmax]` to `decimateColumns`.
  - Clamp `col()` so the margin samples take their own edge column.
  - Add a test with a far `lo - 1` sample.

### P-3 (medium): the quarter-scale fix misses a finite span whose padded range overflows, so the trace draws flat at the bottom

- Where: `plots.js:1127` `fitDrawSpan` scales only when `mx - mn` is not finite.
- Defect: uPlot's default y range pads by 10% of the span (100% of the value for a constant), and for values that large the pad overflows.
- Failure scenario: an ad-hoc `!p 1 v=1.7e308` alternating with `v=0`, a constant `1.7e308`, or any `*scale` carrying a value past about 1.6e308.
  - The span is finite, so the series is not scaled.
  - The y max becomes `Infinity`.
  - The trace is a flat line on the bottom edge.
- Confirmed: driven in headless Chromium with the vendored uPlot and the app's `{auto: true}` scale (`yr.html`).
  - `[0, 1.7e308]`: y max `Infinity`, trace on 8 pixel rows (flat).
  - Constant `1.7e308`: same.
  - `[0, 1e308]`: max `1.1e308`, drawn normally over 83 rows.
  - Also node `uPlot.rangeNum(0, 1.7e308, 0.1, true)` returns `[0, Infinity]` (`rangenum.cjs`).
- Suggested fix: scale when `Math.max(Math.abs(mn), Math.abs(mx)) > Number.MAX_VALUE / 4`.
  - The quarter value then pads to at most about 9e307, whether padded by span or by value.
  - Add the `[0, 1.7e308]` and the constant cases to `plots_y_axis.test.mjs`.

### P-4 (low): the soloed y axis readback is tested only against the stub, where uPlot draws no y ticks at those magnitudes

- Where: `plots.js:1030` (`drawnValue` in the axis `values`) and `plots_y_axis.test.mjs` "read back at its own values".
  - The test calls `axis.values()` directly with a hand-picked split.
- Defect: in Chromium uPlot generates no y splits at all for values of 1e100 and up.
  - That includes the scaled `+/-4.25e307` case this path exists for.
  - The branch never runs in a browser, and the test certifies a label nobody can see.
  - Splits still appear at 1e30.
- Confirmed: driven in Chromium (`yr.html`, axis with a `values` function): `_splits` is `null` for 1e100, 1e300, `+/-4.25e307` and `[0, 4.25e307]`.
- Suggested fix: pick one.
  - Give the soloed axis its own `splits` so huge ranges get ticks.
  - Or drop the axis half and SPEC 9.2's "and the axis", keeping the chip readback that does work.

### P-5 (low, CPU): `!p` and `!can` rows are now decoded twice per row

- Where: the new `hooks.adhocTick` (`plots.js:55`) and `hooks.canTick` (`can.js:91`), called from `state.js` `computeTick` for every event row.
- Defect: `plotIngest` and `canIngest` then run the full parse again on the same raw text.
  - Before the round, `computeTick` only split the line and read one token.
  - WEBUI-CPU-5's `decodeOnce` removed exactly this for `!ps` only.
- Failure scenario: a busy CAN bus or a fast ad-hoc stream pays two full decodes per row, one more than at `6e4f6f7`.
- Confirmed: reasoned from the code (`state.js:248-252` against the `6e4f6f7` `computeTick`).
- Suggested fix: memoise the last parse per decoder as `decodeOnce` does (raw text key), or cache the parse on the row object.

### P-6 (low): four changed branches no test catches

Mutations were applied in the copy and every JS test file importing `plots.js` or `digital.js` was run (62 files, one at a time). Each of these stayed green:

- `plots.js:638` `breakChart`'s `chart.dirty = true`.
  - Probably redundant: a trailing null point 0.1 ms past the newest sample changes nothing drawn.
- `digital.js:135` `breakLanes`'s `lane.dirty = true`.
- `plots.js:634` `breakChart`'s `lastHost === null` guard.
  - Without it, a chart that exists with no sample (`addSample` returned on a non-finite x) gets a point at x = 1e-4.
  - That point then becomes the far margin sample of P-2.
- `plots.js:1444` the `plotsShown()` gate on `visibilitychange`.
  - Without it, a tab refocus redraws into a hidden sidebar once.
- Suggested fix: pin the guard and the gate with a test each, and delete the dirty marks or show what they change.

### P-7 (low, fix-panes doubt 2 confirmed): a gap divider as a pane's oldest row stops scroll-to-top paging

- Where: `pane.js:177` returns null when `rows[0].chan === "gap"`.
- Defect: the new shed divider says rows are missing that the capture holds and paging would fetch, yet it ends the walk.
- Failure scenario: a pane filtered to a rare channel is rebuilt (a filter change, a resume) after a shed notice.
  - With no matching row older than the divider, the divider is `rows[0]` and scrolling up loads nothing.
  - The same happens after a clear followed by a shed.
- Confirmed: reasoned from `historyIdTo`, and from `rebuild` letting `gap` rows through every filter.
- Suggested fix: bound the page by the first non-gap row, since paging is what fills the hole a divider names.
  - This also applies to the reconnect divider.

## Doubts verified

- WEBUI-3 "every chart and lane of that port": verified, the current behaviour is right.
  - `server.py:2218` emits `{"gap": n}` per subscriber, with no port.
  - The page's socket spans all ports, so the shed rows can belong to any port and breaking every chart and lane is the only faithful reading.
  - SPEC 3.4 already says "every chart trace and digital lane"; the triage wording "of that port" cannot be implemented without a daemon change.
- Shed divider blocking history paging: confirmed, see P-7. It is not only the rare "exactly the oldest row" case: a rebuilt sparse pane hits it.
- Regex dialect on non-ASCII: verified.
  - Serial rows are decoded `ascii`/`replace` (`serial_link.py:808`), so only markers and session names carry non-ASCII.
  - The ASCII controls `\x1c`-`\x1f` read the same for `\s \w \S` in `regex` and in JS (driven, both engines).
  - One more face in marker text: JS without the `u` flag counts an astral character as two for `.` and `{n}`, where `regex` counts one code point.
- Chart edge-label drop relying on the final bbox: driven in Chromium on the sim's live chart, plain and with a soloed y axis (`bbox.left` 25 and 60 px).
  - Every label that was drawn fits inside `[0, u.width]`.
  - Not driven on a clock-jump capture.
- CPU items verified only structurally: WEBUI-CPU-4 now measured in Chromium against the sim with CDP `TaskDuration`.
  - 0.51 s per 10 s with the sidebar open, 0.30 s collapsed.
  - The rest were not re-measured.

## Needs a human in a browser

- Firefox: `mcuscoped --sim --open`, set the window to 30 s: the decimated sine chart and the digital lanes look the same as in Chromium, with no block artefacts on steps.
- Windows at 125% scaling: chart x-axis labels at both ends are whole (none cut at the canvas edge) in host and tick modes.
- After P-1 is fixed: narrow the window below 860 px with the sidebar hidden: the charts and the CAN table draw.

## Checked, nothing found

- `terminal.js` flush trim: the `winFirst`/`winLast` shift keeps the identity check as the guard, and a trim past the window rebuilds.
- `terminal.js` `scopedCount`: key inputs (head, clear point, port and channels through `resetHistory`) and the pause/resume source switch carry the count correctly.
- `pane.js` `regexDialectIssue`: escapes, sets, `{,` and classes, cross-checked against the ASCII engine differences above.
- Decimation extremes: min, max, first null, and each column's first and last sample survive (the extremes and live edge were revert-verified again here).
- Gap breaks: `breakChart` and `breakLanes` write the live arrays only, so frozen snapshots do not move (classes 23 and 26).
  - After a clear-all there is nothing to break.
  - Ad-hoc hold keeps a break until the channel's next value.
- Hide and show: charts stay dirty while collapsed and `redrawPlots` re-derives on the width change.
  - The CAN table re-renders on the next tick, and the fold cue follows the ResizeObserver.
- Clear tokens (class 80): no new queue in this slice.
  - `lastDecode`, `pane.scope` and `chart.xLabels` are caches, keyed or reset with their inputs.
- `mergeNarrow` and the one-path lane drawing: breaks stay unmerged, a lone narrow segment stays, and busy blocks skip labels.
- Ruler and chart x-axis tick fitting: the loop terminates, and ruler overlaps are skipped on measured widths.
- `can.js` port-scoped export and space-only id filter pattern (the pattern passes `regexDialectIssue`); `freeze.js` watermark removal.
- Class 76 (rebuild keys): `decodeOnce` (raw plus def object), decimation (width marks dirty), `drawScale` (recomputed per `currentData`).
- Tests: all 27 slice files pass whole, and every test in them passes run alone with `--test-name-pattern` (`run_alone.py`).
  - 11 other mutations were caught, among them the collapse gates, rename fold sync, typed-stream hold, scope clear key, axis edge drop and the decimation picks.
- Browser APIs used (`findLast`, `ResizeObserver`, `measureText`, the regex `s` flag) exist in current Firefox and Chrome.
