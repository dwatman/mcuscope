# Fix batch: web UI panes

HEAD: `b994076c88c875ad43c30789cda6039d147b7abe` (uncommitted tree; the chrome batch was editing its files concurrently)

Scratch, revert runner (`revert.py` + `m_p*.json`) and Chromium drives (`drive.py`, `cpu.py`, `ulp.html`): `~/tt-data/mcuscope-2026-09-24/fixbatch-panes/`.
Every revert below was run with `revert.py`: copy the source, apply the revert, run the named test file with `--test-name-pattern`, restore from the copy.

## Per finding

### P-1 (high): visibility gates follow the real layout

- Change: `plots.js:1441` `plotsShown()` and `can.js:588` `canVisible()` test `sidebar.clientWidth > 0` in place of `#workspace.collapsed`.
  - Not a shared helper: the one shared home would be `state.js` (not mine), and the gate is one expression.
- Tests: `plots_collapsed_sidebar.test.mjs` rewritten around three layouts (shown, collapsed wide = width 0, collapsed narrow = class set, width 800).
  - New: "below 860 px a saved hidden sidebar is on screen, and its charts draw"; "below 860 px a saved hidden sidebar still re-renders the CAN table".
  - `can_bytediff`, `can_age_tick`, `plots_fold_cue`: setup gives the sidebar a width.
  - Without it `plots_fold_cue` "the redraw tick does not run the cue" goes vacuous: its mutant (tick calls `syncFoldCue`) survived without the width and is caught with it.
- Revert: both gates back to the class check fail the two narrow tests; each gate with the width term dropped fails the collapsed-wide tests.
- Chromium, real page against `mcuscoped --sim` (`drive.py`):
  - 800 px, saved hidden: sidebar 800 px wide, 1 uPlot, CAN table moving.
  - 1400 px, saved hidden: `sidebar.clientWidth` 0, 0 uPlots, CAN idle, reopen tab `display: flex`.
  - 1400 px shown: 360 px, 1 uPlot, CAN moving.
- Narrow users are never stuck: below 860 px the sidebar is always on screen; widening past 860 px brings back the reopen tab. No `style.css`, `index.html` or `chrome.js` edit needed.
- CPU: the plot tick now reads `sidebar.clientWidth` while collapsed (5 Hz). A/B in Chromium, collapsed, s of task time per 10 s: old 0.66 / 0.77, new 0.73 / 0.65. Within noise.

### P-2 (medium): decimation columns divide the window

- Change: `timewindow.js:139` `decimateColumns(xs, ys, lo, hi, width, xmin = xs[lo], xmax = xs[hi - 1])` sizes columns over `[xmin, xmax]`.
  - `plots.js:1119,1129` `currentData` passes xRangeFor's window (`z.min/z.max`, else `xmax - span / xmax`).
  - The report's `col()` clamp was added, did not change any result, and was removed: input is sorted and each side has one margin sample, so each already gets a column of its own.
- Tests (`plots_decimate.test.mjs`):
  - "columns divide the window, not a margin sample far outside it" (direct, left and zoom-right margins).
  - "a stream resuming after a silence draws at full resolution through currentData".
  - "a zoom whose right edge falls in a silence draws at full resolution".
  - Each asserts kept samples at most 2 px apart inside the window.
- Revert: `timewindow.js` back to slice sizing fails the first test.
  - `plots.js` passing no window fails the second, passing no `winMax` fails the third, passing no `winMin` fails the second.
- CPU: no per-row work added. A window the data does not fill now gets one column per screen pixel instead of the slice spread over all of them, so fewer points.

### P-3 (medium): scale on magnitude, not on an overflowing span

- Change: `plots.js:1146` `fitDrawSpan` scales when `Math.max(-mn, mx) > Number.MAX_VALUE / 4`.
  - That expression is the largest magnitude; it is `-Infinity` for an all-null series, which stays unscaled.
- Test: `plots_y_axis.test.mjs` "every series near the double limit gets a finite padded y range, and reads back its own values".
  - It loads the vendored uPlot in a `vm` context and runs its own `rangeNum(min, max, 0.1, true)` on the drawn data.
  - Cases: `[0, 1.7e308]`, constant `+/-1.7e308`, `+/-1.7e308`, `[0, MAX]`, constant `MAX`, `[0, MAX/4]`, `+/-MAX/4`, constant `MAX/2`.
- Revert: the old condition fails it.
  - Mutants also caught: threshold `MAX/2` (the `MAX/2` constant overflows), positive side only (`-1.7e308` constant), and a threshold of `1e3` ("an ordinary chart is not rescaled").
- Chromium, real page, fed through the page's own `plots.js`:
  - `[0, 1.7e308]`: y `[0, 4.7e307]`, trace spread 75 of 83 px.
  - `+/-1.7e308`: y `[-5.1e307, 5.1e307]`, spread 69 px.
  - Constant `1.7e308`: y `[0, 8.5e307]`, drawn mid-height.
  - Chips read `1.7e+308`.

### P-4 (low): the soloed y axis ticks at every magnitude

- Chosen: the axis gets its own steps. The alternative (drop the axis readback and SPEC's "and the axis") was not taken.
- Change: `plots.js:1084` `Y_INCRS`: uPlot's 1, 2, 2.5, 5 steps from 1e-32 to 5e307 (uPlot's own stop at 5e32).
  - `plots.js:1034` puts them on the soloed axis.
  - `plots.js:1040`: a tick whose readback is past the double limit gets a null label, which uPlot skips. Before, those ticks were labelled `Infinity`.
- Why steps and not `splits`: with no step wide enough, uPlot's `findIncr` returns space 0 and never calls `splits`.
  - Its `numIntDigits` goes through int32 bit ops, so no digit cap blocks huge values.
- Tests (`plots_y_axis.test.mjs`):
  - New "the soloed y axis has tick steps for every magnitude a series can reach": a step within range/10..range/2 for ranges 1e-30 to 1.02e308, all finite.
  - "read back at its own values" now also asserts `[-7.5e307, 2.5e307]` reads `[null, "1e+308"]`.
- Revert:
  - No `incrs`, steps stopping at 1e32, and steps to 1e308 (overflow) each fail the new test.
  - Keeping the `Infinity` label and dropping `drawnValue` from the axis each fail the readback test.
- Chromium, real page:
  - Ticks at 1e50: `0, 5e+49, 1e+50`. At 1e300: `0, 5e+299, 1e+300`.
  - `[0, 1.7e308]`: `0, 8e+307, 1.6e+308`.
  - `+/-1.7e308`: `-, -1e+308, 0, 1e+308, -`.
  - Before: no ticks past about 1e33.
- Checked for an infinite loop in uPlot's split walk (`val + incr == val` at a tiny span): `[1e20, 1e20 + 1 ulp]` gets range `[0, 2e20]` from uPlot's soft zero, 3 splits, no hang (`ulp.html`).
- CPU: `Y_INCRS` is built once per module. uPlot registers 1360 steps in a Map once per soloed chart build, and `findIncr` binary-searches them per axis pass.

### P-5 (low, CPU): one parse per `!p` and `!can` row

- Change: `plots.js:71` `adhocOnce(raw)` and `can.js:99` `canOnce(raw)` keep the last parse keyed by the raw text, as `decodeOnce` does.
  - Used by `hooks.adhocTick` and `plotIngest` (`plots.js:56,279`), and by `hooks.canTick` and `canIngest` (`can.js:92,138`).
  - Both parsers are pure functions of the raw text, and their results are only read.
  - No `state.js` edit needed.
- Tests:
  - `plots_decode_once.test.mjs` "a live !p row is parsed once for its tick and its points together" (counts `parseFloat`).
  - New `can_decode_once.test.mjs` (counts `parseInt`).
  - Both also check that new text is parsed afresh.
- Revert: each of the four call sites back to the raw parser, and each memo ignoring its key, fail.
- CPU: per row, one string compare in place of a second full parse.

### P-6 (low): the four uncaught branches

- `breakChart`'s `chart.dirty = true`: deleted (`plots.js:640`).
  - A trailing null point changes nothing uPlot draws: the stepped path ends at the last sample either way, and the chips skip nulls back to it.
  - The next sample marks the chart dirty.
- `breakLanes`' `lane.dirty = true` (`digital.js:135`): kept, now tested. It is not redundant.
  - A lane quieter than its sibling repaints only through this flag: the shared right edge does not move on a break.
  - Without it, the level stays drawn to the edge.
  - Test: `digital_repaint.test.mjs` "a break repaints a quiet lane whose level no longer reaches the shared edge". Revert fails it.
- `breakChart`'s `lastHost === null` guard: kept, now tested.
  - Test: `plots_adhoc_hold.test.mjs` "a break on a chart with no sample yet adds nothing" (chart built by a `ts: NaN` row). Revert fails it.
- The `plotsShown()` gate on `visibilitychange`: kept, now tested.
  - Test: `plots_collapsed_sidebar.test.mjs` "a tab returning to visible repaints the charts only when the sidebar is on screen". Revert fails it.

### P-7 (low): paging past a leading divider

- Change: `pane.js:176` `historyIdTo` bounds the page by the first non-gap row (`rows.find`).
  - It returns null only with no line at all, or when that line is at the floor.
- Tests (`terminal_history.test.mjs`):
  - "historyIdTo asks for the page" now asserts a shed divider ahead of row 500 gives 499.
  - The same test asserts null for dividers alone, and for a divider ahead of the capture's first line.
  - The old assertion "a divider already says the rest is not loaded" encoded the defect and is replaced.
  - New fetch-path test "a pane whose oldest row is a shed divider still pages below its oldest line": `id_to=989`, the page lands ahead of the divider, and the divider stays.
- Revert: the old `rows[0]`/gap check fails the fetch test.
  - Using `rows[0]` without the gap check fails the arithmetic test, and dropping the no-line guard throws.
- The HISTORY_MAX divider is unaffected: it comes with `historyDone`.

## Runs

- Each of the 18 new or changed tests passes alone (`--test-name-pattern`).
- All 151 `webui_js` test files pass, run one at a time (104 import a module of mine, 47 do not).
- `pytest tests/test_plot_grammar_fixture.py tests/test_security.py`: 103 passed.
- The sim daemon ran on a throwaway config (port 8693, own `db_path` and `MCUSCOPE_*_DIR`) and was killed by PID. The Playwright browsers were closed; no profile dirs were left.

## Edits needed in files I do not own

- `app.js:199-200` (optional, the CAN half of P-6's refocus gate): the tab-refocus repaint renders the CAN table into a hidden sidebar once per refocus.
  - Export `canVisible` from `can.js` (mine, not done: an export with no caller until `app.js` uses it).
  - Then replace the two lines with `if (canVisible() && canRows.size) renderCan();`.
- None needed for P-1 in `style.css`, `index.html` or `chrome.js`.

## SPEC wording (9.2 / 9.1)

- Line 1862: "A series whose values reach past a quarter of the double limit is drawn at a quarter scale, so its padded y range stays finite; the chips and the axis still read the samples."
- Line 1860, append: "The axis ticks at any magnitude; a tick past the double limit read back is left unlabelled."
- Line 1605 area, add: "Scrolling to the top of a pane whose oldest row is a divider loads the lines below its oldest line, which is what fills the hole the divider names."
- Line 1556 area, add: "Below 860 px the sidebar stacks under the terminal and is always shown; a saved hide applies again in a wider window."

## Proposed CHANGELOG lines

- Amend line 414: "Web UI: long tick labels no longer overlap or clip, values near 1e308 draw across the chart (a constant or a 0 to 1.7e308 series too), a soloed y axis ticks at any magnitude, and a long unit is cut on the y axis."
- Amend line 410, append: "; scrolling to the top of a pane pages past a divider."
- P-1, P-2 and P-5 fix regressions in unreleased work (line 45's hidden-sidebar gate, the decimation, the tick hooks), so they get no lines of their own.

## Not done

- No `app.js` refocus edit (see above).
- P-2 not driven in a browser: the node tests drive `currentData` and `decimateColumns`, and the drawing is uPlot's.

## Doubts

- P-7: after paging past a shed divider, the divider sits below rows it says were shed.
  - It is still true of the live stream, and the rows are now shown above it.
  - Dropping the divider once its hole is filled would need an id range on the gap row. I did not do it.
- P-1: the gate reads layout on the tick, so it relies on a collapsed wide sidebar measuring exactly 0.
  - Chromium measures 0 because `.sidebar` has no padding. A future padding or border rule would read a few px and draw into the hidden column.
- P-4: steps below 1e-32 are still absent, as in uPlot's default, so a soloed series spanning under about 1e-31 gets no ticks. This is unchanged.

## Needs a human in a browser

- Firefox, 800 px window with the sidebar hidden at a wide width: the charts and CAN table draw; widen past 860 px, and the reopen tab shows.
- Solo a channel fed `!p` values of 0 and 1.7e308 (e.g. `mcu` sim with an ad-hoc stream): the y axis labels (`8e+307`, `1.6e+308`) fit the 46 px gutter uncut.
