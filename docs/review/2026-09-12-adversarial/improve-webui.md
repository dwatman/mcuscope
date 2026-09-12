# Web UI improvement leg, 2026-09-12 (HEAD c15b7c6)

Open sweep for usability, CPU efficiency and function across `host/mcuscope/webui/` and `host/mcuscope/sim.py`.
Read-only on the main tree; scratch under `/tmp/rev-2026-09-12/improve-webui/`.

Stacks measured: `mcuscoped --sim --config <throwaway> --port 18605 --plot` (sim default rate), and a second daemon on 18606 attached to `mcu-sim --plot --flood 2000 --tcp-port 19900` for the high-rate leg.
Browser absent throughout: every figure comes from node 22 driving the same endpoints and the same DOM-free modules the page does.

Note on the launch line: `mcuscoped` has no `--plot`; argparse abbreviation resolved it to `--plotjuggler`, so the measured daemon was streaming UDP to 127.0.0.1:9870. That affects daemon CPU, not any payload below. See P15.

## (a) Measurements

### Polling and payloads, browser absent

Every periodic HTTP poll the page makes, found by reading `app.js`, `api.js`, `statusbar.js`, `plots.js`, `can.js`, `terminal.js`, `digital.js`:

| timer | interval | endpoint | idles when hidden |
|---|---|---|---|
| `app.js:120` | 5 s | `GET /status` | yes |
| `app.js:121` | 1 s | none (local uptime tick) | yes |
| `api.js:99` | 1 s | none (local lines/s window) | yes |
| `plots.js:1031` | 200 ms | none (canvas repaint) | yes |
| `can.js:406` | 1 s | none (age cells) | yes, and while the CAN view is hidden |

`GET /status` is the only steady-state request. `/plot/channels`, `/plot/series` and `/lines` fire on page load and on a WS reconnect only; the per-poll `/plot/channels` scan the 2026-09-07 round removed has not come back.

`/status` over six polls at the 5 s interval:

| figure | value |
|---|---|
| response size | 1020-1021 B (avg 1021) |
| flattened fields | 28 |
| fields that differ between consecutive polls | 4 of 28 |
| which ones | `uptime_s`, `db_size_bytes`, `db_content_bytes`, `ports` |
| bytes/s from status polling | 204 B/s |

Grows with the capture? `/status` is O(ports), `/plot/channels` is O(distinct channels) (1932 B for 9 channels). Neither is O(rows). No polled endpoint grows with the capture.

WebSocket, the only unbounded consumer:

| | sim default | flood 2000 lines/s |
|---|---|---|
| window | 20 s | 20 s |
| rows | 2054 | 42062 |
| rows/s | 102.7 | 2104 |
| frames/s | 20.2 | 76.3 |
| rows per frame | 5.1 | 27.6 |
| bytes/s | 12 250 | 290 112 |
| bytes per row | 119.3 | 137.9 |
| largest frame | - | 12 280 B / 89 rows |
| median inter-frame gap | - | 10.2 ms (p99 44.4) |

Row shape on the wire is `{id, ts, port, dir, chan, seq, raw}`; the six non-`raw` fields are about 90 B of a 138 B row. `dir` is `"rx"` and `seq` is `null` on essentially every captured line. See "considered and not proposed".

Channel mix at the sim's default rate, 20 s:

| prefix | rows | chan |
|---|---|---|
| `!ps` | 1200 | event |
| `!p` | 400 | event |
| `!can` | 370 | event |
| `!can2` | 60 | event |
| `!pd` | 12 | event |
| `sim alive` | 10 | debug |
| `!m` | 1 | marker |

Page-load cost, one full boot sequence (`/lines` backfill, `!pd` seed scan, `/plot/channels`, one `/plot/series` per channel):

| request | bytes | notes |
|---|---|---|
| `/lines?order=desc&limit=200` | 23 874 | fixed, 200 rows |
| `/lines?match=^!pd &limit=64&id_to=` | 8 276 | definition seed |
| `/plot/channels` | 1 932 | 9 channels |
| `/plot/series` x 9 | 399 094 | 5400 points total, 74 B/point |
| **total** | **433 176** | 211 ms wall |

`/plot/series` is 92% of the page-load bytes. A point is `{"line_id":20649,"ts":1789189895.529565,"tick_ms":201108,"value":0.6277}` = 74 B for 4 numbers. At the `SEED_CHANNELS = 32` cap with a 20 Hz stream and the default 30 s window, the seed would be about 1.4 MB.

Export sizes, whole capture of 686k rows (10 min of flood):

| path | bytes |
|---|---|
| `/lines/export` (text) | 40 454 249 |
| `/lines/export?format=csv` | 54 585 127 |
| `/lines/export?format=jsonl` | 102 007 210 |

`state.js downloadPath` does `await r.blob()`, so the whole response is held in tab memory before it reaches disk. SPEC 9.2 states there is deliberately no row cap.

### Render cost

DOM-free modules, node 22, at the sizes SPEC 9.2 caps them to (100k-point ring, 32 channels, 64 lanes):

| case | us/op |
|---|---|
| `timewindow.visibleRange` over 100k vertices | 0.080 |
| `timewindow.firstAtOrAfter` over 100k | 0.035 |
| `timewindow.timeWindow` (builds two closures) | 0.057 |
| `timewindow.fmtTime` host (`new Date` + pad) | 0.294 |
| `timewindow.fmtTime` rel | 0.159 |
| `timewindow.fmtTime` tick | 0.040 |
| `timewindow.fmtDelta` | 0.136 |
| `state.nearestX` over 100k | 0.055 |
| `pane.planHistoryPage` (200-row page) | 0.631 |
| `exportrange.params` | 0.087 |
| `exportrange.validate` | 0.031 |

Derived per-frame totals: 96 surfaces (32 charts + 64 lanes) x `visibleRange` = 7.7 us per frame; 32 charts x `nearestX` = 1.8 us. At the 200 ms redraw tick both are noise. The projection layer costs nothing worth optimising.

Per-row ingest, the path `api.js routeLiveRow` runs for every arriving row, driven with 2060 rows of the sim's own measured mix and the three `!pd` definitions primed:

| path | us/row |
|---|---|
| `state.pushBuffer` | 0.32 |
| `can.canIngest` | 1.30 |
| `plots.plotIngest` | 3.81 |
| `state.lineTick`, cold | 3.43 |
| `state.lineTick`, memoized (5000-row buffer) | 0.027 |

Total ingest is about 5.4 us/row, so 10.8 ms/s of main-thread time at the 2000 lines/s guard threshold: roughly 1% of one core. The memoization on `lineTick` is worth 127x and is doing its job. The high-rate guard fires correctly at the flood rate (measured 2104 lines/s against `HIGH_RATE_ON = 2000`).

DOM-bound per-frame paths, read rather than measured (the stub cannot lay out a canvas):

| path | DOM writes per frame | rebuilds a tree it did not change? |
|---|---|---|
| `terminal.render` (virtualized) | one screenful (~40 rows) worst case; `shiftWindow` reduces the live case to the rows that entered plus the rows that left | no |
| `terminal.updateShown` | 1 text node, but walks the 5000-row ring per render per pane while a pattern is set | no (known, declined 2026-09-07) |
| `can.renderCan` | per-cell diffed against `r.last`; only cells whose text moved are written | no |
| `can.ageCan` (1 Hz) | age cell only | no |
| `plots.redrawPlots` | `setData` per dirty chart; all `clientWidth` reads hoisted above the writes | no, except `buildUplot` destroys and recreates the chart on a theme change, a series-count change, and a committed colour change |
| `digital.redrawDigital` | one canvas repaint per dirty lane; `clientWidth` reads hoisted | no |
| `statusbar.renderPorts` | whole chip row, but gated behind a `portsSig` string comparison | no |
| `plots.renderChans` | whole legend, on every new channel | yes, but only when a channel appears |

No per-frame path rebuilds a node tree the frame did not change. This part of the UI is in good shape; the findings below are function and usability, not CPU.

## (b) Proposals, ranked by value per line of diff

### P1. A drag zoom applies to one chart, so the stacked charts stop sharing their x axis (M)

**Observation.** `plots.js onSelect` writes `chart.zoom`, and `xRangeFor(chart)` / `chartZoom(chart)` / `currentData(chart)` read that chart's own field. Zoom the temperature spike on stream 0 and the current trace on stream 1, plus every digital lane, stay on the 30 s tail window: the one thing stacked charts exist for, reading two signals against one time axis, is exactly what a zoom breaks. `digital.js` never imports `charts` and projects with `timeWindow(state.timeMode, currentWindowSec(), digitalRightEdge(), w)`, so the linked cursor lands at the right *time* on a lane but at a different *pixel* from the chart it was dragged on. SPEC 9.2 promises "a shared, synchronized x axis".

**Change.** In `plots.js`, replace the per-chart `chart.zoom` with one module-level `let plotZoom = null` (same `{mode, min, max}` shape). Export a getter. `onSelect` sets it, pauses every chart surface and the digital panel (call `pauseAll(true)` from `freeze.js`, which already governs all three), and marks every chart dirty plus `markDigitalDirty()`. The `dblclick` handler already bound per canvas clears it, and `digital.js` gets the same handler on `#digitalWrap`. `terminal.js setTimeMode` already nulls `chart.zoom` per chart: change that to one `clearZoom()` call. In `digital.js`, `drawDigitalLane` and `setDigitalCursorAt` both build their window with `timeWindow(...)`: give `timewindow.js` a new `windowFor(zoom, timeMode, windowSec, edge, width)` that returns the zoom range when one stands in the active mode and the right-anchored tail otherwise, and call it from all four sites (`plots.xRangeFor`, `plots.currentData`, `digital.drawDigitalLane`, `digital.setDigitalCursorAt`).

**Test.** DOM-free: `host/tests/webui_js/timewindow.test.mjs` gains cases for `windowFor` - a zoom in the active mode wins, a zoom recorded in another mode is ignored, no zoom falls back to `[edge - span, edge]`, a zero-width zoom is rejected. Manual (browser checklist): that uPlot's own double-click reset does not fight the shared hook, and that a drag on one chart under `cursor.sync` does not leave a stale selection rectangle on its siblings.

**SPEC.** Section 9.2, the bullet "The visible range is set by the window selector, and there is no drag zoom" - already false at HEAD (see P14) and must become "a drag on any chart's x axis zooms every chart and the digital lanes to that range and pauses them; double-click anywhere restores the window selector's range".

### P2. The CAN table does not show which byte moved (S)

**Observation.** `can.js updateCanRow` already computes the payload diff (`if (flags || L.hex !== e.hex)`) and then throws it away, writing the whole payload as one text node via `fmtCanData`. Highlighting the bytes that changed is the single feature every CAN tool has and the reason the latest-per-id view exists: "I pressed the button, which byte moved". At the sim's 10 Hz heartbeat the payload is a rolling counter, so the effect is immediately visible in the demo too.

**Change.** In `can.js`, add and export `changedBytes(prevHex, hex)` returning a boolean array (length = byte count; all false when the lengths differ or `prevHex` is empty). In `updateCanRow`, when the payload moved, rebuild `r.data` as one `<span class="byte">` per byte instead of a text node, adding `chg` to the spans `changedBytes` flags. Store `L.hexPrev` alongside `L.hex`. Clear the `chg` class on the next tick that finds the payload unchanged, so the highlight fades on a quiet id rather than sticking. `style.css` gets `.can .byte.chg { background: <accent>; transition: background .4s }`.

**Test.** DOM-free: new `host/tests/webui_js/can_bytediff.test.mjs` drives `changedBytes` - equal payloads flag nothing, one byte differing flags exactly that index, a dlc change flags nothing (lengths differ), an empty previous payload flags nothing, an `rtr` row has no payload at all.

**SPEC.** Section 9.1, CAN panel columns bullet: add "bytes that moved since the previous frame for that id are highlighted briefly".

### P3. The port chip never shows which board is behind the port (S)

**Observation.** `/status` carries `target` per port (measured: `"target":"sim"`), and `statusbar.js pollStatus` reads it only into `state.portTarget` to pick the command bar's send mode. The chip shows alias and device. This is item 5 of the 2026-09-01 bench feedback verbatim - "the ST-LINK was moved between the two boards several times, the alias stayed `st-link-v3pwr` while the board behind it changed" - which was built daemon-side and in `mcu status` and never reached the browser.

**Change.** In `statusbar.js renderPorts`, after the `.alias` span append a `<span class="meta target">` carrying `pt.target` when it is non-null, and add `pt.target || ""` to the `portsSig` array. The signature is the trap: without it the chip is only repainted when some other field moves, so replugging the ST-LINK onto the other board would leave the old name on screen indefinitely. Add `target` to the chip's `data-tip` as "monitor reports: <name>" when null is the interesting case (a port that has not answered `OK monitor`).

**Test.** DOM-free-ish: `host/tests/webui_js/statusbar_logic.test.mjs` already drives `renderPorts` through the stub. Add a case that changes only `target` between two renders and asserts the chip text changed (this fails against a `portsSig` that omits it), and one that renders a null `target` and asserts no target span.

**SPEC.** Section 9.1, the port chip paragraph: add `target` to the list of what the chip shows.

### P4. Isolating one channel or lane takes one click per channel (S)

**Observation.** `plots.js renderChans` wires the name span to a plain toggle, and `digital.js` does the same for its lane gutter. A stream declaring 12 channels needs 11 clicks to look at one, and 11 more to get back. SPEC 9.2 caps the UI at 64 analog channels and 64 lanes, so the worst case is 63 clicks.

**Change.** In `plots.js renderChans`, in the `toggle` closure, read the event: with `altKey` (or `shiftKey`) set, solo instead of toggle - if this channel is the only one shown, show all; otherwise show only this one. Recompute `chart.show` for every name, then `buildUplot(chart)` once (the count crosses the single-trace y-axis boundary, so an in-place `setSeries` is not enough). Keyboard: `makeSpanButton` passes the activating event through, so `Shift+Enter` reaches the same branch. Same three lines in `digital.js`'s lane name handler, followed by `markDigitalDirty()`. Update both `title` strings to say so.

**Test.** DOM-free: extract the decision as `soloShow(names, showMap, name) -> Map` into `plots.js` and export it; new `host/tests/webui_js/plots_solo.test.mjs` drives it - soloing from all-shown leaves one, soloing the already-sole channel restores all, soloing a hidden channel shows only it.

**SPEC.** Section 9.2, the "channel checkboxes" bullet: one clause that alt-click (and Shift+Enter) solos.

### P5. The attach dialog silently drops `eol` and `serial_number` (S)

**Observation.** `POST /ports` takes `PortAttach{alias, device, serial_number, baud, eol}`. `statusbar.js submitAttach` posts `{alias, device, baud}`. A CRLF board attached from the browser therefore always lands on `lf`, and the only fix is to open Settings, find the row, and set it there - the same path the 2026-09-04 round found rewriting a hand-set `crlf` back to `lf` (that round's HIGH). This is that class on the other door.

**Change.** In `index.html`'s attach dialog, add an eol `<select>` (`lf`, `crlf`, `none`) defaulting to `lf` and a `serial_number` text input beside the device row, both with `<label for=>`. In `statusbar.js submitAttach`, include `eol` always and `serial_number` when non-empty; pass both to `saveAttachedPortToConfig` so the "save to config" checkbox writes the same values it just attached with.

**Test.** DOM-free-ish: extend `host/tests/webui_js/settings_ports_bound.test.mjs`'s sibling for the attach path (or add `statusbar_attach_eol.test.mjs`) with a fake `api` capturing the POST body - assert `eol` is present, assert a blank serial number is omitted rather than sent as `""`.

**SPEC.** Section 9.1, the attach dialog bullet list: add the eol and serial-number fields.

### P6. Every export is buffered whole in tab memory before it reaches disk (S)

**Observation.** Measured: `/lines/export?format=jsonl` over a 686k-row capture is 102 MB; the text form is 40 MB. `state.js downloadPath` does `await authFetch(...)` then `await r.blob()` then `saveBlob`, so the browser holds the entire response in the tab before the save dialog appears, with no progress and nothing to cancel. SPEC 9.2 is explicit that the daemon will not cap a row count, so this grows without bound. The comment at `downloadPath` explains the choice: a plain `<a>` navigation would put a configured token in the URL and the server log.

**Change.** In `state.js downloadPath`, branch on `getToken()`. With no token set (the loopback default, and the only case the comment's argument is about), build `<a href=path download=fallbackName>` and click it: the browser streams straight to disk, shows its own progress, and the `Content-Disposition` filename applies natively. With a token set, keep the existing fetch-to-blob path unchanged. Keep the error reporting for the blob branch; the navigation branch cannot report a daemon 400 inline, so gate it further on the caller (the four `*/export` paths, not `/sessions/{id}/bundle`) or accept the browser's own error page - state whichever in the comment.

**Test.** DOM-free: `downloadPath` already takes its dependencies from module scope; assert through the stub that with no token an anchor with `download` is created and no `fetch` happens, and that with a token set `fetch` is called and no anchor is created.

**SPEC.** None.

### P7. The simulator's terminal is 99.5% machine output (S, `sim.py`)

**Observation.** Measured over 20 s of `mcuscoped --sim`: 2042 `event` rows, 10 `debug` rows, 1 `marker`. The terminal column is the largest thing on the page, and in the first minute of the zero-hardware demo it is a solid wall of `!ps 0 1B59 04,09FA,0F` and `!can 46900 - 100 000001D5` with `sim alive n=42` twice a minute. The channel colour coding, the `resp`/`err` styling, the per-pane regex filter and the channel checkboxes all have nothing to act on unless the user types a command, so the demo does not demonstrate them.

**Change.** In `sim.py Simulator.poll_events`, replace the bare `sim alive n=N` beat with a short narrated sequence driven off the same enum state machine the plot stream already runs (`state = (tick // 1000) % 3`): emit a debug line on each transition (`state: IDLE -> ARMED`), a periodic reading line (`vbat=24.98V iout=1.24A temp=41C`) at 0.5 Hz, and roughly once a minute one warning-shaped line and one `ERR` response line so the `resp`/`err` colouring appears without a command. Keep the total added rate under 2 lines/s so the measured payload figures barely move, and keep every added line plain debug text (no new wire syntax).

**Test.** `host/tests/` already drives the sim core; assert that a 5 s poll sequence produces at least one `debug` row whose text is not `!`-prefixed, and that the added lines do not parse as any protocol event (`protocol.parse_event` returns nothing for them).

**SPEC.** Section 7 describes the simulator's behaviour; add the narration to its list. No wire-protocol change.

### P8. Eight of the simulator's nine plot channels declare no unit (S, `sim.py`)

**Observation.** Measured from `/plot/channels`: only `tri` carries `unit: "V"` and `scale: 0.01`. `ramp` is raw u2 counts to 65535, `ftest` an f4, and the two ad-hoc channels carry nothing. The UI's unit handling (legend suffix, cursor readout unit, the `.unit` span in `renderChans`) is exercised by exactly one trace in the demo, and the scale path by one.

**Change.** In `sim.py _poll_plot`, extend the definitions: `!pd 0 tri:s2*0.01:V ramp:u2*0.1:mA ftest:f4:degC`, and make the ad-hoc `!p` line emit a third channel with a physically meaningful name. Pick values whose magnitudes stay far apart so the independent-y-scale behaviour the chart relies on is still demonstrated. Check the existing tests first: the `!pd` strings are asserted in several places (`test_sim*.py`, the plot-grammar JS tests) and every one must move with them.

**Test.** The existing sim and plot-grammar tests, updated; add one asserting `/plot/channels` returns a non-null `unit` for more than one channel.

**SPEC.** None.

### P9. Clicking a CAN row does not narrow the terminal to that id (M)

**Observation.** The workflow that motivates the CAN table - "0x321 looks wrong, show me its raw frames" - currently means reading the id off the table and hand-typing `!can[0-9]? +[0-9]+ +\S+ +321 ` into a pane's regex box. The pane filter and the CAN row model both exist; nothing connects them.

**Change.** In `can.js buildCanTable`, make the id cell a span-button (the `makeSpanButton` helper `digital.js` exports is already shared) whose activation calls a new hook. Wire the hook in `app.js` to a `terminal.js` export `filterPaneTo(pattern)` that sets the last pane's `matchInput.value`, calls `applyRegex` and `rebuild`, and scrolls that pane into view. Build the pattern in `can.js` from the row: `^!can<bus-suffix> \\d+ \\S+ <ID> ` with the bus suffix empty for bus 1, matching `parseCanEvent`'s own grammar so the two cannot drift. Add a second button on the row that clears it again.

**Test.** DOM-free: export `canFilterPattern(entry)` from `can.js` and drive it in `can_logic.test.mjs` - bus 1 produces no digit, bus 2 produces `!can2`, an extended id is zero-padded to 8 the way `fmtCanId` does, and the produced pattern matched against a real `!can` line from the same entry is a hit while the neighbouring id is not.

**SPEC.** Section 9.1, CAN panel: one clause that clicking an id filters a terminal pane to that id's frames.

### P10. A window change is one click per chart plus one for the lanes (S)

**Observation.** `chrome.js buildWindowButtons` is shared, but each chart holds its own `chart.window` and the digital panel its own `digitalWindow`. With three streams plus the ad-hoc chart plus the lanes, moving from 30 s to 5 s is five clicks, and a half-done change leaves panels showing different spans under a cursor that claims to be shared.

**Change.** In `chrome.js buildWindowButtons`, pass the click event to `onSelect(secs, event)`. In `plots.js buildChartDom` and `digital.js buildDigitalHead`, when `event.shiftKey` is set, apply the span to every chart and the digital panel instead of just this one, then repaint the "on" state on every window group (export a `syncWindowButtons(secs)` from `chrome.js` that walks `.plot-win` and re-toggles). Say so in the buttons' `title`.

**Test.** DOM-free: `chrome.test.mjs` already covers `buildWindowButtons`; add a case asserting the event reaches `onSelect` and that a shift-click and a plain click are distinguishable to the callback.

**SPEC.** Section 9.2, "selectable time window (5 s, 30 s, 5 min)": add that a shift-click applies it to every chart and the lanes.

### P11. Every chart is 150 px tall with no way to make one bigger (M)

**Observation.** `plots.js buildUplot` hard-codes `height: 150` and `redrawPlots`/`resizePlots` repeat it. The "expand" control widens the sidebar, which helps the x resolution and does nothing for the y, and reading a waveform is a y-axis job. With four streams the sidebar is a 600 px scroller of equally cramped strips, and there is no way to give the one chart under investigation more room.

**Change.** Add `chart.height` defaulting to 150. Put a drag handle on the bottom edge of `.plot-canvas` (the `pointerdown`/`pointermove`/`pointercapture` pattern in `app.js`'s `#resizer` is the model, clamped to 80..600) that writes `chart.height` and calls `scheduleResizeRedraw()`. Replace all three hard-coded `150`s with `chart.height`. Persist the per-chart height in `localStorage` keyed by `chart.key`, validated on read like `chrome.js` validates the colour store, so a hand-edited value cannot produce a zero-height canvas.

**Test.** DOM-free: export `clampChartHeight(px)` and test the bounds and the non-numeric/NaN rejection. The drag itself is manual (the stub cannot lay out a canvas, per CLAUDE.md).

**SPEC.** Section 9.2, the UI plot panel bullet: note that a chart's height is adjustable and remembered per browser.

### P12. The CAN table is the one live panel pause-all does not stop (M)

**Observation.** `registerSurface` is called by `terminal.js` (panes), `plots.js` (charts) and `digital.js`; `can.js` registers nothing, and `openCanExport` passes `watermark: null` with the comment "the CAN table is not a freeze surface". So "pause all" freezes three panels and leaves the fourth running, and a payload updating at 10 Hz cannot be read at all. SPEC 9.1 defines pause-all over "panes, charts, the digital panel", so this is a deliberate omission, but the export dialog's whole design is that a paused surface exports its frozen window, and CAN is the one panel that cannot.

**Change.** In `can.js`, keep a `canFrozen` snapshot of the `canRows` map (structured-cloned entries, as `terminal.js` snapshots `frozenRows`) plus `canFrozenId = state.maxId` at the moment of pause; `renderCan`/`ageCan` read `canFrozen` while paused. Register the surface with `isLive: () => !canPaused`, `setPaused`, `watermark: () => canPaused ? canFrozenId : null`. Add a pause button to the CAN sub-head mirroring the digital head's. `openCanExport` then passes the real watermark and a `shownLastMs` so the "shown window" export mode becomes available there too.

**Test.** DOM-free-ish: `can_logic.test.mjs` gains a freeze case - ingest, pause, ingest more, assert the rendered model is the snapshot and the watermark is the id at pause; `freeze.test.mjs` gains the fourth surface to `anyLive`/`pauseAll`.

**SPEC.** Section 9.1: add the CAN panel to the pause-all surface list and to the export dialog's per-panel table (its shown-window mode).

### P13. The chip says a port is connected but not whether it is saying anything (S)

**Observation.** The status bar carries one global lines/s (`api.js renderRate`). With two boards attached, nothing on screen says which of them is producing it, and the bench memory records that a silent board is normal for this hardware, so "0" is not by itself a fault. `/status` carries `lines_rx` per port and is polled every 5 s, which is all the arithmetic needs.

**Change.** In `statusbar.js`, keep the previous poll's `{alias: lines_rx}` map and its timestamp; in `renderPorts`, append a `<span class="meta rate">` reading `<N>/s` computed from the delta over the elapsed poll interval (blank on the first poll, and blank rather than negative when the counter goes backwards after a daemon restart). Do not put the derived rate in `portsSig` - it moves on every poll and would defeat the signature; instead write it into an existing cached element, the way `can.js updateCanAge` writes the age cell without rebuilding the row.

**Test.** DOM-free: export `portRate(prevRx, rx, dtSeconds)` and drive it - first poll gives null, a counter reset gives null not a negative, a zero dt gives null, a normal delta rounds as expected.

**SPEC.** Section 9.1, the port chip paragraph: add the per-port rate.

### P14. SPEC 9.2 still says the charts have no drag zoom (S, doc only)

**Observation.** SPEC 9.2 reads "The visible range is set by the window selector, and there is no drag zoom: the charts are right-anchored on live data, so pausing and exporting the frozen window as CSV is the path to a closer look." The 2026-09-07 improvement round built drag zoom (`plots.js onSelect`, `chartZoom`, `clearZoom`, the per-canvas `dblclick`) and the sentence was never retired. Per CLAUDE.md, when code and SPEC disagree SPEC wins, so this reads as an instruction to delete a shipped feature.

**Change.** Rewrite that bullet in `docs/SPEC.md` section 9.2 to describe what exists: a drag on a chart's x axis selects a range, which pauses the surface and replaces the follow-tail window until a double-click clears it; the window selector sets the range otherwise. Land it with P1 if P1 is taken, on its own if not.

**Test.** None (documentation).

**SPEC.** Section 9.2, the bullet quoted above.

### P15. `mcuscoped --plot` silently means `--plotjuggler` (S)

**Observation.** `mcuscoped` has no `--plot`; `mcu-sim` does. argparse's default prefix matching resolved `--plot` to `--plotjuggler` on the measurement stack for this review, so the daemon started streaming UDP to 127.0.0.1:9870 with no message saying so. The brief for this leg asked for `--plot` expecting "with plots", which is the natural reading given the sibling tool has that exact flag. A user copying that line gets a UDP stream they did not ask for and no indication of it.

**Change.** In `daemon.py`'s parser, log one line at INFO when PlotJuggler streaming is enabled, naming the destination and the flag that enabled it, so the effect is at least visible in the startup output beside the "web UI: ..." line. Do not disable abbreviation globally: other prefixes are in muscle memory and in the docs. If the owner would rather remove the ambiguity, adding an explicit `--plot` that errors with "did you mean --plotjuggler, or mcu-sim --plot?" is the smaller-surprise alternative and the same size.

**Test.** A CLI test asserting the startup output names the PlotJuggler destination when the flag is given and does not when it is not.

**SPEC.** Section 3.7 if the explicit-error variant is chosen; none for the log line.

## (c) Considered and not proposed

- **Trim `dir` and `seq` from the WebSocket row.** Measured 22 of 138 bytes per row (16%) are `"dir":"rx","seq":null`. It is a SPEC 3.4 wire change with the CLI as a second consumer, for 16% of a stream that is already only 290 kB/s at the guard threshold.
- **A leaner `/plot/series` point shape.** 74 B per point, 92% of the page-load bytes. Parallel arrays would cut it by about half, but it is a SPEC 9.2 wire change shared with `mcu plot` and the seed is a once-per-load cost of 400 kB.
- **Cache `updateShown`'s ring walk.** Declined by the author and again by the 2026-09-07 round; measured at 0.027 us per row memoized and about 100 us per pane per render cold, which does not amount to a new argument.
- **Web-worker or off-thread ingest.** Total client ingest is 5.4 us/row, about 1% of a core at 2000 lines/s. Nothing to move.
- **Expose `POST /purge` in Settings.** Genuinely unexposed, and `dry_run` was designed for a confirmation dialog, but the sessions list already offers per-run delete and the automatic session covers the whole daemon run, so the space-reclaim path exists. Low value per line.
- **Terminal find-and-jump (highlight a match and scroll to it, keeping context) instead of filter-only.** A real gap, but it is a scroll-position search over a virtualized list plus a paged capture walk: L-sized, and it overlaps the "marker list with click-to-jump" item already on the deferred list.
- **`fillSessions()` is not awaited before the export dialog opens**, so an Export click inside the first tens of milliseconds sends no session ref and exports the open one. Real but unreachable in practice on loopback; folding the await in is S if someone is in the file anyway.
- **Persist a pane's paused state across reload.** The shared buffer is re-backfilled from scratch on load, so there is no frozen content to restore; the flag alone would come back paused over a live view.
- **Per-lane export in the digital panel.** `exportDigital` exports every shown lane; hiding the others first is one click each, and P4 makes that one click total.
- **Hard-coded `socket://127.0.0.1:9900` in the attach dialog's device list.** Wrong for `mcu-sim --tcp-port 0`, right for every default invocation; not worth a lookup.
- **`buildUplot` destroy-and-recreate on a committed colour change.** Already deliberate and already deferred to the picker's `change` event rather than `input`, so a drag does not thrash it.
- Everything on the **Phase P2 backlog** and its "Deferred review findings" subsection: global keyboard shortcuts, the marker list with click-to-jump, command autocomplete, markers on the charts, sim state across TCP reconnects, settable `can stat`.
- Everything on the **2026-09-07 "Decisions and not built"** lists (W7, D4, D6, the unhoisted completion flags, `--flap` on the in-process sim, history pages not surviving a rebuild, the uncached `shown / total`).

## Carried open, not run by this leg

The browser checklist owed since 2026-09-07 needs a real browser and was not run here. It stands unchanged, plus the two manual items P1 and P11 add:

- Drag zoom and double-click reset, and whether uPlot's own reset fights the hook.
- Single-series y axis width in the sidebar.
- Scroll-to-top history paging without a jump, with a filter and after a clear.
- Double-click copy flash.
- Tab focus on panes; radios read by a screen reader.
- The 2026-09-04 items: write-fail badge, disconnect-reason gloss, eol select offline.
- New with P1: a drag on one chart under `cursor.sync` must not leave a selection rectangle on its siblings.
- New with P11: the chart-height drag handle.
