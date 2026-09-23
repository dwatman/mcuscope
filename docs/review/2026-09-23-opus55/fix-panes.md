# Fix batch: panes

Files: `webui/terminal.js`, `plots.js`, `digital.js`, `can.js`, `api.js`, `pane.js`, `timewindow.js` (`exportrange.js` untouched).
Scratch, harnesses and A/B pages: `~/tt-data/mcuscope-2026-09-23/fix-panes/`.
Revert-verification: `revert_verify.py` applies one mutation at a time to the file, runs the named test file, and restores; 72 + 16 mutations, results in `rv1.txt` and below.
Every "reverted" below means the named mutation made the named test fail, unless it says MISSED.

## WEBUI-CPU-1: append path survives the VIEW_MAX trim

- `terminal.js:421` `flush`: the trim moves `winFirst`/`winLast` by the rows cut, so `shiftWindow` still finds its rows; the identity check stays the guard.
- Test: `webui_js/terminal_flush_append.test.mjs` (elements kept by identity across a flush at the cap; a flush larger than the window rebuilds).
- Reverted: caught.

## WEBUI-CPU-2: block trim of the pane queue

- `api.js:146`: trims past `VIEW_MAX + BUFFER_SLACK`, back to `VIEW_MAX`.
- Test: `api_pane_queue.test.mjs` (existing, edited; see below): the queue holds `VIEW_MAX + BUFFER_SLACK` untrimmed, one more row trims to `VIEW_MAX` from the oldest.
- Reverted: caught.

## WEBUI-CPU-3: min/max-per-pixel decimation

- `timewindow.js:136` `decimateColumns`: above 4 samples/px, per pixel column its first and last sample and, per series, lowest, highest and first null; the union keeps one x array.
- `plots.js:1095` `currentData(chart, width)`: both uPlot call sites pass the width; a width change (`redrawPlots`, `resizePlots`) marks the chart dirty so it re-decimates.
- Readouts and the cursor read `u.data`, which holds only real samples; exports read the store.
- Tests: `plots_decimate.test.mjs` (threshold; dense sine keeps extremes and newest; sparse-step stream keeps the step's first sample, a one-sample spike and a dip; a break survives; a flat signal keeps the live edge; the level held across empty columns is the burst's last sample; chart integration and chip value; re-decimation on redraw-resize and `resizePlots`).
- Reverted: each pick (min, max, null, first/last, last only), the call, and both dirty marks: all caught.
- Measured in headless Chromium, 2 series at 800 Hz, 30 s, 342 px (`ab/ab2.py`, awaiting uPlot's microtask commit, raster forced): 24000 points and 13-28 ms per redraw before, 765 points and 5.8-7.2 ms after. Renders compared by eye (`ab/combo2.png`): steps, spikes and dips identical.

## WEBUI-CPU-4: collapsed sidebar draws nothing

- `plots.js:1429` `plotsShown()` gates the 200 ms tick and the visibility handler on `#workspace.collapsed` as well as the CAN view.
- `can.js:581` `canVisible()` honours the collapse.
- Test: `plots_collapsed_sidebar.test.mjs` (no chart built while collapsed, drawn on the next tick after reopening; CAN table not re-rendered while collapsed, re-rendered after).
- Reverted: both caught.

## WEBUI-CPU-5: each !ps decoded once

- `plots.js:72` `decodeOnce`: the last decode, keyed by raw text and definition object; `hooks.plotSampleTick` and `plotIngest` both use it.
- Test: `plots_decode_once.test.mjs` (counts `getFloat32` calls: 1 per live row; a redefinition between identical lines decodes afresh).
- Reverted: always-decode and key-without-def both caught. The test first failed on my own change (plotIngest still called the decoder directly), which is how that was found.

## WEBUI-CPU-6: fold cue off the redraw tick

- `plots.js:1403` `syncFoldCue` writes `hidden`/`title` only on change; removed from `redrawTick`; runs on scroll, on a `ResizeObserver` over `plotsScroll`, `plotCharts`, `digitalHead`, `digitalWrap`, and on a chart rename (its tooltip names charts).
- Test: `plots_fold_cue.test.mjs` (the tick forces no layout; a RO callback updates the cue; unchanged cue writes nothing; rename updates the tooltip).
- Reverted: all four caught.

## WEBUI-CPU-7: regex count carried forward

- `terminal.js:160` `scopedCount`: per-pane running count, extended over rows appended since; recounts when the source's first row or the clear point changes; `resetHistory` drops it (every filter change and row replacement goes through it).
- Test: `terminal_regex_count.test.mjs` (a counting getter proves old rows are not re-read; equal to a full recount across a buffer trim, a clear, a channel change, a pause, a raised clear point without rebuild, and a scroll render of a paused cleared pane).
- Reverted: the carry-forward, `resetHistory` drop, head key, clear-point key: all caught.
  - A `src` identity key and an `n > length` key were MISSED and are deleted as redundant (a snapshot with the same first row is the buffer up to the freeze; the source never shrinks with the same head).

## WEBUI-CPU-8: lanes merge sub-pixel segments, one path per lane

- `timewindow.js:92` `mergeNarrow`: runs of 2+ non-null segments under 1.5 px become one busy block.
- `digital.js:645` `drawBits`, `:675` `drawEnum`: busy blocks filled between both levels; enum rails and crossings in one path, clip only for labelled segments.
- Test: `digital_lane_draw.test.mjs` (counting 2d context: bits and enum lanes at 3000 segments on 230 px stay under 60 `lineTo`, 2 strokes; a slow lane keeps every edge and label; `mergeNarrow` cases incl. a lone narrow segment and a break).
- Reverted: bits merge, enum merge, null exclusion, lone-segment rule: all caught.
- Measured before/after in headless Chromium (`ab/ab.py`: bits and enum lanes at 100 Hz, 30 s, 230 px, HEAD modules against HEAD plus the new `digital.js`/`timewindow.js`): 6.65 to 0.49 ms per redraw issuing draws, 63.4 to 0.75 ms with the raster forced (software raster overstates the second).

## WEBUI-2: ad-hoc channels hold

- `plots.js:619` `addSample`: in an ad-hoc chart an absent channel pushes its previous value; a null (a break, or before its first value) stays null. Typed streams keep the gap.
- Test: `plots_adhoc_hold.test.mjs` (alternate lines; a break stays until the channel's next value; the seed holds the same way; typed stream keeps null).
- Reverted: caught.

## WEBUI-3: shed rows marked on every surface

- `api.js` `handleWsRow`/`markShed` (`:248`): a `{gap: n}` accumulates; before the next routed row whose id leaves a hole, a divider `gap: N lines shed by the live stream` goes to the buffer and every pane, and `breakCharts()` (`plots.js:650`) and `breakLanes()` (`digital.js:130`) break every chart and lane.
  - The page subscribes to all ports and the notice names none, so "every chart and lane of that port" is every chart and lane.
  - A notice whose next row follows the watermark (the backfill fetched the rows) marks nothing.
  - Staged notices end a staging segment, like capture tokens, so the sort cannot move them; the pending count resets per connection (`armStaging`).
- Latent sibling: the reconnect backfill's own divider (`gap: N lines not loaded`) now breaks the charts and lanes too; its comment claimed a window with no samples draws as a gap, which a stepped hold does not.
- `pane.js` `gapRow(oldest, gap, why)` carries the wording.
- Test: `api_ws_gap.test.mjs` (divider placement and chart/lane nulls; no-hole notice; staged notice position; backfill-covered notice; notice from a replaced socket; reconnect backfill divider breaks charts and lanes).
- Reverted: all nine branches caught.

## WEBUI-4: long tick labels

- `timewindow.js:219` `fitAxisTicks(anchors, win, width, labelPx)`: fewer ticks while the widest label plus 8 px does not fit the spacing.
- Chart axis (`plots.js:888`): labels measured in uPlot's own axis font (7 px a character where nothing measures); a label that would run off the canvas is `null` (uPlot skips it), placed from the splits call's own range.
- Ruler (`digital.js:576`): fitted ticks at 6.1 px a character, and a label overlapping the one before it (the end clamp) is left out.
- Test: `timewindow_axis_fit.test.mjs` (11-digit ticks overlap unfitted and not fitted; short labels unchanged; ruler draws no overlapping label and labels every fitted tick; chart splits fitted; edge label dropped, middle kept).
- Reverted: fit loop, chart fit, chart edge drop, ruler fit, ruler skip: all caught.
- Browser check: host-time and 10-digit tick axes rendered old/new (`ab/combo2.png`, `ab/combo3.png`); labels identical where they fit. A first version dropped `09:16:40` at the left edge (fixed-width estimate too wide); caught by eye, fixed by measuring.

## WEBUI-5: huge finite values

- `plots.js:1133` `fitDrawSpan`: a series whose finite span overflows is handed to uPlot at a quarter scale (exact); `drawnValue` divides it back for the chips and the soloed y axis.
- Chosen over a range function or a custom `distr`: in Chromium a range function alone drew half the trace, and `distr: 100` blanked every normal y axis label (`yrange.py`).
- Test: `plots_y_axis.test.mjs` (drawn span finite, chip reads -1.7e308, axis reads back, ordinary chart unscaled).
- Reverted: scale, chip, axis: all caught.

## WEBUI-6 (axis half): long unit cut

- `plots.js:1079` `axisUnitLabel`: at most 22 code points with an ellipsis; the chip keeps it whole.
- Test: `plots_y_axis.test.mjs`. Reverted: caught.

## HEALTH-7: one regex dialect for pane and export

- `pane.js:61` `regexDialectIssue`: refuses every letter escape but `\b \B \d \D \s \S \w \W \f \n \r \t \v`, a short `\x`/`\u`, `\1`-`\9`, a POSIX `[:` in a set, a set opening with `]` or `^]`, and `{,`. Found by a two-engine probe over all 52 letter escapes and 40 constructs (`rx_probe.py`/`rx_probe.mjs`).
- `terminal.js` `applyRegex` refuses with `<construct> is read differently by the export filter...`, and compiles with flag `s` so `.` matches an embedded CR as Python's does.
- Fixture `tests/regex_dialect_cases.json` (49 accepted patterns with expected matches over 22 lines; 33 refused with the daemon's reading), driven by `webui_js/pane_regex_dialect.test.mjs` and `tests/test_pane_regex_dialect.py` (the daemon's `store._make_regexp`). The browser half also proves each refusal reads differently (backreferences excepted, refused with the octal forms).
- Reverted: each scanner rule and the `s` flag caught; an initial `\01` refusal failed the "refused for nothing" test and was dropped.

## HEALTH-8: CAN history export per port

- `can.js:645` `openCanExport`: ports from the shown rows then the attached aliases; a `Port` select (enabled with the history source) when there are two; `port` always set when one is known; the shown window is the chosen port's.
- Test: `can_export_port.test.mjs`. Reverted: all six branches caught.

## HEALTH-16 (JS) / FIRMWARE-4: non-finite drops the point

- `plots.js:258`: the one finiteness check, after the scale, `continue`s; `decodePlotField` returns non-finite floats as is; a sample with nothing finite left is `null`, as the firmware batch's `protocol.py` has it (read from its diff).
- Tests: `plots_finite.test.mjs` (edited), `plot_grammar.test.mjs` (shared fixture, unchanged, passes).
- Reverted: per-point check and empty-sample rule both caught.

## HEALTH-17: M29, M62, M71

- Tests: `api_capture_reset_paused_pane.test.mjs` (M29), `plots_ingest_scope.test.mjs` (M62, M71), from the health probes, each with a positive control.
- Reverted (M29 `frozenId = 0`, M62 the event gate, M71 `seedMaxId.clear()`): all three caught.

## JS-7: duplicate enum value

- `digital.js:195` `enumLabel` uses `findLast`, last-wins as the CLI and export.
- Test: `digital_enum_label.test.mjs`. Reverted: caught.

## Tokenizer

- `splitTokens` (drop one terminator, split on U+0020 runs) in all three plot parsers, the sid lookups and `can.js` `parseCanEvent`; now one implementation in `state.js` (follow-up 2).
- Tick hooks the chrome batch's `state.js` reads: `hooks.adhocTick` (`plots.js:55`), `hooks.canTick` (`can.js:95`, `parseCanEvent` now returns `tick`).
- `canFilterPattern` uses ` +` and a trailing space; the marker divider strip (`terminal.js`) splits on spaces and takes the tick word only with text after it.
- Tests: `plots_event_tokens.test.mjs`, `terminal_marker_divider.test.mjs`, `prerelease_webui-panes_can.test.mjs` (edited). Reverted: all caught.

## Stale comments

- `can.js:59` (tokenizer), `plots.js` seed gate (was 310-314: now says a JSON null is what it guards), `api.js` backfill-divider comment ("the plots need no equivalent" was wrong), `terminal.js` history-paging dialect comment.

## Existing tests edited

- `api_pane_queue.test.mjs`: "a live pane keeps at most VIEW_MAX queued rows" now drives the block trim; the setup check in "a pane whose filter excludes the rows" and the bound in "a further burst" allow the slack. Behaviour changed by WEBUI-CPU-2.
- `plots_chrome.test.mjs`: the fold-cue test fires a scroll instead of the 200 ms tick (WEBUI-CPU-6).
- `plots_finite.test.mjs`: "a non-finite f4 value is dropped, and the rest of its sample still lands" (owner ruling FIRMWARE-4).
- `prerelease_webui-panes_can.test.mjs`: the tab-separated corpus line no longer decodes (tokenizer ruling).

## SPEC edits

- 3.4 gap paragraph: the UI sentence only: it marks the hole (divider, chart and lane break) rather than showing the stream from the gap onward.
- 9.1 terminal: regex dialect refusal (two sub-bullets); the two divider wordings for rows the page never received.
- 9.1 CAN: id-click pattern "runs of spaces"; frame history is one port's, with a `Port` choice.
- 9.2 charts: y-axis unit cut to 22 characters; ad-hoc hold; breaks; quarter-scale drawing of an overflowing span; decimation to about 4 samples per pixel; tick count fitted to label width, overlapping or edge labels left out.
- 9.2 lanes: sub-1.5 px changes drawn as a block; a repeated enum value takes its last label.

## Changelog

- Web UI: terminal panes keep appending instead of redrawing their window once they hold 5000 lines, and a hidden tab no longer trims each pane's queue per row.
- Web UI: fast plot streams are drawn min/max per pixel (about 4x cheaper per redraw at 800 Hz), and digital lanes draw fast toggling as a block (about 10x cheaper).
- Web UI: a hidden sidebar stops the chart and CAN redraws; the "N below" cue and the "N / M lines" count no longer run per frame.
- Web UI: ad-hoc channels printed on separate `!p` lines draw as held steps instead of nothing.
- Web UI: rows the live stream shed show as a divider in the panes and a break in every chart and lane; a reconnect gap breaks the charts too.
- Web UI: a pane regex the export filter would read differently (`\A`, `\Z`, `\p{..}`, POSIX classes, `{,n}`, backreferences) is refused with the reason, and `.` matches as the daemon's does.
- Web UI: the CAN frame-history export is one board's, with a Port choice when there are several.
- Web UI: a NaN or infinite float drops that point only; the rest of the sample is charted.
- Web UI: plot and CAN lines split on spaces only, as the daemon does; a repeated enum value reads its last label, as the CLI does.
- Web UI: long tick labels no longer overlap or clip; huge values (near 1e308) draw; a long unit is cut on the y axis.

## Not done

- `splitTokens` duplication: done in the follow-ups below.
- `hooks.adhocTick`/`hooks.canTick` exist only once `plots.js`/`can.js` load; any test importing `state.js` and `terminal.js` without `can.js` sees no `!can` tick (that is what `terminal_logic.test.mjs` and `terminal_tick_estimate.test.mjs` hit mid-round; both pass now, edited by another batch).

## Doubts

- WEBUI-3 "every chart and lane of that port": the notice names no port and the page's socket spans all ports, so I break every chart and lane. If the orchestrator meant a per-port break, the daemon would have to name the port.
- A WS shed divider that becomes a pane's oldest row stops scroll-to-top history loading (`historyIdTo` treats any `gap` row as the end), although the shed rows are in the capture. Rare (the divider must be exactly the oldest row); not changed.
- The regex refusal covers what the probe found on ASCII text plus U+FFFD. `\d \w \s \b` still differ on non-ASCII text, which reaches the capture only through REST markers and session names; not refused.
- The chart edge-label drop relies on uPlot calling `splits` with the final bbox; checked on two renders only, not on the report's clock-jump capture.
- WEBUI-CPU-1, 2, 4, 5, 6, 7 are verified structurally (counts, identity), not re-measured in a browser; the CPU report's A/B covers the same patches.

## Verification run

- Every JS test file naming one of my modules or `app.js` (104 files), one `node --test` at a time after the last code change but the line wraps: all pass (`run2.txt`); the 11 files covering the wrapped lines rerun after: all pass.
- `uv run python -m pytest tests/test_pane_regex_dialect.py tests/test_plot_grammar_fixture.py`: 166 passed; ruff clean on the new Python test.
- Not run: the whole JS or pytest suite, Firefox, Windows, a real daemon in a browser (the A/B pages load the modules directly with no daemon).

## Follow-ups (orchestrator, after the chrome batch)

1. HEALTH-18 cleanup.
   - Dropped the `watermark:` key the registry no longer reads from `registerSurface` in `can.js`, `digital.js`, `plots.js`, `terminal.js`, and their `minWatermark` imports.
   - Deleted `freeze.js` `minWatermark` and its test in `freeze.test.mjs` (existing test removed with the function).
   - Verified: `grep` finds no `minWatermark` and no `watermark: () =>` left under `webui/` or the tests; `freeze.test.mjs`, `can_freeze_surface`, `rulings_panes_export` and `module_load_order` pass. Nothing to revert-verify: the keys were unread.
2. One tokenizer.
   - `state.js` `splitTokens` (exported) replaces `firstToken`, and both callers use it: `lineTick`'s `!ps` retry test, `computeTick`'s tag, `markerTick`'s words.
   - `plots.js` and `can.js` import it; the copy in `plots.js` and the inline one in `can.js` are gone.
   - `plots_event_tokens.test.mjs` imports it from `state.js`.
   - Reverted: the terminator drop, the space-only split, `can.js` back to `/\s+/`: all caught.
3. Fixture `points` key.
   - `plot_grammar.test.mjs` asserts the names the decoded sample carries, in order, where a case lists `points`.
   - A second test fails if no case lists it, so the assertion cannot quietly check nothing.
   - Reverted: pushing points in reverse order is caught with the assertion and passes without it. Keeping a NaN point is caught either way, through `decodes`.
4. Marker tick against `protocol.parse_marker`.
   - `markerTick` now requires the line to open with `!m `, as `parse_marker` partitions at the first space, and reads its tick token with `splitTokens`.
   - The browser already agreed on `!m \t@5 hi` (no tick). The new test is `state_marker_tick.test.mjs`, over the Python side's whitespace set (tab, VT, FF, 0x1C-0x1F). It checks the tick and the terminal divider, with a positive control, a leading-space line (no `!m` head) and a line terminator.
   - Reverted: the head check (back to the first token) and the tick split (back to `/\s+/`): both caught.
   - Switching `markerTick` from `split(" ").filter(Boolean)` to `splitTokens` is not observable, since only the last word could carry a terminator. That switch is the one-implementation change, not a behaviour change.
- Run one file at a time: `state_marker_tick`, `plot_grammar`, `plots_event_tokens`, `freeze`, `state_line_tick`, `state_decoder_tick`, `can_logic`, `can_freeze_surface`, `rulings_panes_export`, `prerelease_webui-panes_can`, `module_load_order`, `smoke`, `terminal_marker_divider`: all pass. `pytest tests/test_plot_grammar_fixture.py`: 84 passed.
- Files touched beyond my batch: `state.js`, `freeze.js`, `freeze.test.mjs`, `plot_grammar.test.mjs` (existing tests edited: `freeze.test.mjs` lost the `minWatermark` test; `plot_grammar.test.mjs` gained the `points` assertion and its guard).
