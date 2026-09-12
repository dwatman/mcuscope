# Batch: web UI charts and terminal (HEAD c15b7c6)

Files touched: `host/mcuscope/webui/{timewindow,chrome,plots,digital,terminal}.js`, `docs/SPEC.md` 9.2.
`freeze.js` needed no change (`pauseAll` already governs all three surfaces).
Tests: `timewindow.test.mjs`, `chrome.test.mjs`, `export_paused_window.test.mjs`, `plots_zoom.test.mjs`, and new `plots_solo.test.mjs`, `plots_export_button.test.mjs`, `digital_zoom.test.mjs`, `terminal_filter_pane.test.mjs`.

## What changed, per item

### W1 (HIGH) - the pane export comma-joined `chan`
`terminal.js exportPane().build()`: `for (const ch of pane.channels) p.append("chan", ch)`, the shape the backfill path 60 lines above already uses.

### W7 - `exportPane` was unreachable from a test
`terminal.js` now exports `exportPane`. `export_paused_window.test.mjs` gained four pane cases (freeze watermark as `id_to`, a live pane sending none, the three filters in the URL with a repeated `chan`, an unfiltered pane sending neither `chan` nor `match`).

### W5 - `changes` without `decode` is a 400, not an export
`plots.js exportChart` and `digital.js exportDigital`: `changes` now carries `enabledBy: "decode"` (the dialog disables it while decode is off, as `deadband` already follows `changes`) **and** `build()` sets `decode=1` whenever `changes` is set, so no URL the dialog can build is refused.

### W12 - an export button that was enabled and inert
`plots.js syncExportBtn(chart)` (called from `renderChans`, the channel toggle and the solo path) and `digital.js syncDigitalExportBtn()` (called from `buildDigitalHead`, `addDigitalLane`, the lane toggle and `clearAllDigital`) disable the button and put the reason in its `title` while nothing is shown. Both buttons carry `class="exportbtn"` so a test can find them; the early `return` in the two export functions stays as the belt, since the DOM stub does not honour `disabled`.

### P1 - one shared zoom for every chart and the lanes
- `timewindow.js`: `windowOf()` helper, `windowFor(zoom, timeMode, windowSec, edge, width)`, `zoomFor(zoom, timeMode)`, and the single zoom store (`getZoom` / `setZoom`).
- `plots.js`: `chart.zoom` is gone. `onSelect` writes the shared range, calls `pauseAll(true)` and marks every chart plus the lanes dirty; `chartZoom(chart)` is the shared range gated on that chart being frozen on it; `xRangeFor` and `currentData` go through it; `clearZoom()` drops the range and repaints without touching the freeze; the per-canvas `dblclick` clears it and calls `pauseAll(false)`; `setChartPaused(_, false)` clears it (resuming follows the tail again, everywhere).
- `digital.js`: `laneWindow(winSec, edge, w)` feeds the lane draw, `setDigitalCursorAt` and `digitalHoverAt` (the fifth site P1 lists four of - the hover inverse has to agree or the cursor lands off the waveform). `#digitalWrap` gets the same `dblclick`.
- `terminal.js setTimeMode`: one `clearZoom()` call in place of the per-chart null.

**Deviation from P1's letter, deliberate:** the zoom store lives in `timewindow.js`, not `plots.js`. `digital.js` must read it, and `plots.js` imports `digital.js`, so a getter on `plots.js` would have made the two modules cyclic - which also flips the order the freeze surfaces register in, depending on which module a test imports first. `windowFor` still takes the zoom as an argument exactly as specified, and the store is DOM-free, so P1's tests land in `timewindow.test.mjs` as asked.

### P4 - alt-click solos
`soloShow(names, showMap, name)` lives in `chrome.js` (the shared chart chrome, already imported by both panels, for the same no-cycle reason) and is **re-exported from `plots.js`**, so `import { soloShow } from "plots.js"` works as P4 specifies. `plots.js renderChans`'s toggle and `digital.js`'s `toggleLane` take the event and solo on `altKey || shiftKey`; `makeSpanButton` already forwards the activating event, so Shift+Enter is the same branch. `digital.js applyLaneShow(lane, on)` is the one writer for a lane's shown state, so the solo path and the plain toggle cannot drift. Both `title` strings say so.

### P10 - shift-click sets the window everywhere
`chrome.js buildWindowButtons` passes the event to `onSelect(secs, event)` and handles the shift-click itself: it drives every selector's own `onSelect` and calls the new `syncWindowButtons(secs)`, so neither panel needs a branch and neither has to know the other has a selector. `dropWindowButtons(win)` (called from `clearAllCharts`) drops a destroyed chart's selector, or its closure would keep writing the window onto a chart that is no longer drawn. Button titles say what shift-click does.

### `filterPaneTo(pattern)` for the other batch
Exported from `terminal.js`. Sets the last pane's `matchInput.value`, calls `applyRegex`, cancels the typing debounce, `rebuild`s, persists, and scrolls the pane into view (`pane.el.scrollIntoView?.()`, so the DOM stub - which has no such method - is not an obstacle). An empty or missing pattern clears the filter; an uncompilable pattern is refused inline by `applyRegex` rather than thrown at the caller.

### P14 - SPEC 9.2
Rewrote the "there is no drag zoom" bullet to describe the shared zoom, its units, and the double-click reset. Added one clause each to the channel-checkbox bullet (alt-click solo, shift-click window) and to the export-button bullet (disabled while nothing is shown). The `deadband` sentence was not touched.

## Revert verification

Each mutation applied to a copy of the tree, the named test file run, then restored. 23 of 23 caught.

| mutation | file | test file | result |
|---|---|---|---|
| pane export comma-joins `chan` | terminal.js | export_paused_window | fail 1 (the three filters case) |
| pane export drops the freeze watermark | terminal.js | export_paused_window | fail 1 |
| chart export drops the forced `decode` | plots.js | plots_export_button | fail 2 (incl. the daemon double's refusal list) |
| digital export drops the forced `decode` | digital.js | plots_export_button | fail 2 |
| `changes` no longer follows `decode` | plots.js | plots_export_button | fail 1 |
| chart export button never disables | plots.js | plots_export_button | fail 1 |
| digital export button never disables | digital.js | plots_export_button | fail 1 |
| a drag pauses only the chart dragged | plots.js | plots_zoom | fail 3 |
| the zoom survives a resume | plots.js | plots_zoom | fail 1 |
| a zoom from another time mode is reused | timewindow.js | timewindow | fail 2 |
| a zero-width zoom is accepted | timewindow.js | timewindow | fail 1 |
| the lanes ignore the shared zoom | digital.js | digital_zoom | fail 1 |
| the lanes' double-click does nothing | digital.js | plots_zoom | fail 1 |
| `soloShow` toggles instead of soloing | chrome.js | plots_solo | fail 7 |
| soloing the sole channel does not restore the rest | chrome.js | plots_solo | fail 3 |
| the legend ignores alt-click | plots.js | plots_solo | fail 1 |
| the lane gutter ignores alt-click | digital.js | plots_solo | fail 1 |
| shift-click reaches only the clicked panel | chrome.js | chrome | fail 1 |
| the other heads are not repainted | chrome.js | chrome | fail 1 |
| an unknown span clears every button | chrome.js | chrome | fail 1 |
| a destroyed chart keeps its selector | chrome.js | chrome | fail 1 |
| `filterPaneTo` types without applying | terminal.js | terminal_filter_pane | fail 3 |
| `filterPaneTo` leaves the debounce armed | terminal.js | terminal_filter_pane | fail 1 |

Scripts: `/tmp/rev-2026-09-12/mutate.py` (against a HEAD tree with only this batch's files overlaid) and `/tmp/rev-2026-09-12/mutate_live.py` (the five export-dialog ones, which need the other batch's `exportdlg_guards.mjs`).

## Gates

- `node --test 'tests/webui_js/*.test.mjs'` from `host/`: **405 tests, 405 pass, 0 fail** (the count moves as the other batches land; this batch adds 38 of them). A HEAD tree with only this batch's files overlaid ran 366/366, so nothing here depends on another batch's edit except `plots_export_button.test.mjs`, which uses their `exportdlg_guards.mjs` double.
- `uv run python -m pytest tests/test_webui_js.py`: 1 passed.
- `grep -nP '[\x{2013}\x{2014}]'` over the six production files, the eight test files and `docs/SPEC.md`: empty.
- `grep -n 'console\.log'` over the same: empty.

Note: `node --test tests/webui_js/` (a bare directory) is not a valid invocation on node 22 - it resolves the path as a module and exits 1. The glob form above is what the pytest wrapper uses.

## CHANGELOG lines (not applied - CHANGELOG.md is another batch's file)

```
- Web UI: a drag on any chart's x axis now zooms every chart and the digital lanes to that range and pauses them; double-click anywhere restores the window selector's range.
- Web UI: alt-click (or Shift+Enter) on a channel or lane name shows only that one, and shows them all again when it is already the only one.
- Web UI: shift-click a window button to set that span on every chart and the digital lanes at once.
- Web UI: fixed a terminal pane export sending its channel filter as one comma-joined value, which the daemon refused with 422 for any pane with two to five channels ticked.
- Web UI: a plot or digital export with "changes only" now always sends decode, which the daemon requires; the checkbox follows the decode box in the dialog.
- Web UI: the chart and digital export buttons are disabled, saying why, while the panel shows no channel or lane, instead of doing nothing when clicked.
```

## CSS the other batch needs to add

None. The two export buttons gained the class `exportbtn` purely as a test handle, and `button:disabled` already styles them; add a rule only if the disabled look needs to differ from the browser default.

## Manual-verify (browser only, against `mcuscoped --sim`)

1. Drag on one chart's x axis: every chart and every digital lane must show the dragged range, and all panels plus the terminal must go paused together.
2. Under `cursor.sync`, the drag must not leave a stale selection rectangle on the sibling charts.
3. uPlot's own double-click reset must not fight the shared hook: one double-click on a chart returns every panel to the window selector's range and resumes.
4. Double-click on the digital lanes must do the same as a double-click on a chart.
5. With a zoom standing, the linked cursor must land on the same x on a chart and on a lane (the pixel agreement the stub cannot lay out).
6. Shift-click a window button: every chart head and the digital head must repaint their own "on" state, not just the one clicked.
7. Alt-click a channel name, then alt-click it again: one trace, then all of them, with the y axis appearing and disappearing at the single-trace boundary.
8. Hide every channel on a chart and hover the export button: it must be visibly disabled and the tooltip must say why (same for the digital head with no lane shown).

## Not done

- Nothing outside this batch's files is broken by these changes: the only failures seen in the working tree during the run were in `can.js` / `statusbar.js` tests owned by other batches, and they were green again by the final run.
- `export_paused_window.test.mjs` was also edited by the other web UI batch while this batch was in it (they moved the fake daemon to `exportdlg_guards.mjs` and adapted the pane cases this batch added to their `<a download>` capture). The merged file is green; no work was lost, but the file now has two owners.
- `plots_export_button.test.mjs` covers both the chart and the digital export button in one file rather than splitting by panel prefix: the two share the dialog harness and the W5 assertion is the same for both.
- P1's "export a getter from plots.js" and P4's "soloShow into plots.js" were placed in `timewindow.js` and `chrome.js` respectively, to avoid a `plots.js` <-> `digital.js` import cycle (see the P1 and P4 entries above). `soloShow` is re-exported from `plots.js` so the specified import still works.
