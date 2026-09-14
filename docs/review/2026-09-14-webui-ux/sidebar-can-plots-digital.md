# Web UI sidebar review, 2026-09-14: CAN table, analog plots, digital panel

Read-only UX and data-viz review of the right sidebar at HEAD 2149806.
Scope: the CAN / Plots / Both switch, expand and collapse, the CAN latest-per-id table, the uPlot strip charts, the digital/enum panel, and the four-way time base coupling with the terminal.
Nothing in the tree was edited.
Findings are ranked by value to the user; effort is S (under an hour), M (a few hours), L (a day or more).

Sources read: `webui/index.html`, `style.css`, `can.js`, `plots.js`, `digital.js`, `timewindow.js`, `freeze.js`, `exportrange.js`, `exportdlg.js`, `chrome.js`, `app.js`, `state.js`, `docs/SPEC.md` sections 9.1 and 9.2, `docs/img/webui.png`, `host/mcuscope/sim.py`, and the 2026-09-07 and 2026-09-12 review legs.

## Findings

### F1. At the default sidebar width the CAN table scrolls sideways and `age` is the column that falls off (S)

Where: `index.html:43` (`--side-w: 360px`), `can.js:272` (six columns), `style.css:218-222` (`.can-wrap { overflow: auto }`, `td { padding: 5px 10px }`).

Today: the six columns are id (up to 8 hex digits plus an `ext`/`rtr` flag chip), dlc, data (23 characters for an 8-byte payload), count, ms, age.
That is roughly 45 monospace characters at 11.5 px plus 120 px of cell padding, about 435 px of content in a 360 px pane, so `.can-wrap` scrolls horizontally and the rightmost column is off-screen until the user drags the divider.
The screenshot in `docs/img/webui.png` was taken with a hand-widened sidebar, so the default is not what the documentation shows.

Why it hurts: `age` is the column that says a bus went quiet, which is the single most important thing the panel reports, and it is the one the default layout hides.
Horizontal scrolling inside a vertically scrolling table is also the worst way to read a wide row.

Change: tighten `table.can td/th` padding to `5px 6px`, drop `count` to a `title` on the row (or to a narrower right-aligned cell), and verify in a browser at exactly 360 px.
If a column still has to go, `count` is the one nobody watches live.

### F2. Every board without a CAN bus loses 45 percent of the sidebar to an empty table (S)

Where: `index.html:66` (`data-view="both"` is the default), `style.css:209` (`height: var(--can-h, 45%)`), `can.js:246-251` (the empty state).

Today: on first load the sidebar splits 45/55 between CAN and Plots, and a board that emits no `!can` events shows "No CAN frames seen yet" occupying nearly half the panel, permanently, until the user notices the horizontal divider or the view switch.

Why it hurts: this is the default experience for the majority of targets, and it halves the height available to the charts, which F9 and the queued chart-height item say is already the scarce dimension.

Change: in `app.js`, when `canRows.size === 0` collapse the CAN section to its `.sub-head` only (set `--can-h` to the head height) and release it to the stored or default 45 percent on the first ingested frame.
One `renderCan()` already runs on the first frame, so it is a class toggle on `.sidebar` plus one rule in `style.css`.

### F3. A drag zoom leaves no visible state and no visible way out (S to signal, M to do fully)

Where: `plots.js:710-723` (`onSelect`), `plots.js:727-732` (`clearZoom`), `chrome.js:82-103` (the window buttons keep their own `.on`), `digital.js:510-515`.

Today: dragging across a chart zooms every chart and every lane and pauses all four surfaces.
The window selector keeps `30s` lit while the drawn span is, say, 1.2 s; nothing names the zoomed range; and the only exits are a double-click (undocumented anywhere in the UI) or resuming a chart, which drops the zoom globally but leaves the other panels paused at their frozen edges.

Why it hurts: the head now lies about what the chart shows, which is the same defect class the code already guards against for the export button and the shift-click window sync.
A user who drags by accident sees four panels freeze and has no affordance to undo it.

Change: while `getZoom()` stands, clear the `.on` state on every window group and insert a chip in each `.plot-head` and in `#digitalHead` reading the zoom span with a `x` that calls `clearZoom()` plus `pauseAll(false)`.
`syncWindowButtons` already walks every group, so the deselect is one call.

### F4. The byte-change highlight compares against the last render, not the previous frame (S)

Where: `can.js:375-384` (`updateCanRow` diffs `L.hex`, the last rendered payload), `can.js:553-557` (the render tick is 1 Hz), `can.js:148-154` (`changedBytes`).

Today: `canIngest` overwrites `e.hex` on every frame, and the diff is taken only when the 1 Hz tick renders.
For the simulator's 10 Hz heartbeat that is a diff across ten frames; for a 100 Hz id nearly every byte will have moved at some point in the second, so the whole payload lights up and the highlight says nothing.

Why it hurts: SPEC 9.1 says "the bytes that moved since the previous frame for that id are highlighted", and this is a diff over the last second instead.
The feature exists to answer "which byte moved when I pressed the button", and at any rate above a few Hz it stops answering it.

Change: in `canIngest`, keep `e.prevHex = e.hex` before the assignment and OR a per-frame change mask into `e.moved` (cleared by `updateCanRow` when it paints).
That makes the highlight "bytes that moved in any frame since the last paint", which is honest and useful; then either say that in SPEC 9.1 or store only the newest frame's mask to match the wording literally.
Flagged as a SPEC contradiction below either way.

### F5. Staleness is a fixed 3 s against a period the row has already measured, and the colour salience is inverted (S)

Where: `can.js:16` (`CAN_STALE_S = 3`), `can.js:400-401`, `style.css:246` (`.age-fresh { color: var(--good) } .age-stale { color: var(--text-faint) }`).

Today: an id that arrives once a minute is permanently green-then-grey on a three second cycle, and a 1 kHz id that has dropped 2000 consecutive frames still reads fresh green.
A bus that died five minutes ago is rendered in the dimmest colour on the panel.

Why it hurts: the row already holds an EWMA of its own inter-arrival period, which is exactly the right yardstick and is being ignored.
And the palette makes "everything normal" the loudest state and "this id stopped" the quietest, which is backwards for a panel you watch out of the corner of your eye.

Change: make the threshold `max(3, 5 * period/1000)` seconds when `e.period` is known, and re-map the colours: fresh takes `--text` (no colour at all), stale takes `--warn`, and past ten times the period takes `--crit`.
Two lines in `updateCanAge` and two rules in `style.css`.

### F6. Two boards' identical stream ids and channel names merge silently into one chart and one lane (S to surface, M to fix)

Where: `plots.js:253` (`key = "s" + sample.sid`, no port), `plots.js:409` (`ensureChart(key, sid)`), `digital.js:21` (`digitalLanes` keyed by name alone), against `plots.js:246` (`plotDefs` correctly keyed `port|sid`) and `can.js:103` (the CAN table correctly keys `port|bus|id`).

Today: attach two boards that both declare `!pd 0 temp:s2*0.01:C` and their samples land in the same chart under one channel called `temp`, interleaved and monotonically nudged so the trace is a zigzag between two boards.
The same happens to a digital lane called `state`.
Nothing on screen names a port.

Why it hurts: the CAN table gets this right, prints the port in its group divider in the port's own colour, and SPEC 9.1 explicitly calls out the same uniqueness rule for CAN ids.
The plots panel makes the identical collision invisible, and a zigzag trace reads as a hardware fault rather than as two boards.

Change (cheap): record the contributing port(s) per chart and per lane at ingest, and append them to the `.ptitle` and to the lane gutter when more than one port has contributed.
Change (right): key `charts` and `digitalLanes` by `port|sid|name` the way `plotDefs` and `canRows` already are, and colour by that key.
SPEC 9.2 already says a future revision should key channels by (port, name) throughout, so the cheap version is the honest interim and the right version is the one SPEC anticipates.

### F7. The digital panel has no time axis of its own (M)

Where: `digital.js:395-475` (the lanes draw a waveform and nothing else), `index.html:94-97` (`#digitalWrap` holds only lanes plus the cursor), `style.css:264-270`.

Today: the lanes carry no ruler, no gridlines and no tick labels.
The only time reference is the analog chart above, which can be collapsed, and the `#dCursor` tag which appears only while hovering.
A stream that is digital-only (the simulator's `!pd 1` and `!pd 2` with the ad-hoc chart hidden) gives lanes with no time reference at all: 5 s and 5 m look identical apart from the highlighted window button.

Why it hurts: SPEC 9.2 asks these to read as logic-analyser lanes, and a logic analyser without a time base is a picture of a square wave.
Measuring a pulse width, the thing lanes are for, is impossible.

Change: add a fixed-height ruler row at the bottom of `#digitalWrap`, drawn from the same `laneWindow(...)` projection the lanes use, with four or five ticks labelled by `fmtTime(state, t)`.
Add faint vertical gridlines at the same x positions inside `drawBits` and `drawEnum`.
Both take the window object that is already passed in, so no new projection code.

### F8. The colour palette is allocated per panel, so unrelated signals in stacked panels share a colour (S)

Where: `plots.js:573` (`colorFor(name, i)` with `i` the index within that chart), `digital.js:145` (`colorFor(name, digitalLanes.size)`), `chrome.js:12-13,38`.

Today: the first channel of every chart is `#46c8d8`, and so is the first digital lane.
With the ad-hoc chart, a stream chart and the lanes on screen, three unrelated signals are the same teal.

Why it hurts: the panels are stacked under one synchronized cursor and one x axis, which is a visual promise that they are read together; identical colours across them read as "the same signal".
The colour store in `chrome.js` is already global and keyed by name, so the palette index is the only thing that is not.

Change: replace the caller-supplied index with a counter inside `chrome.js` that assigns the next palette slot the first time a name is seen and remembers it.
`colorFor(name)` then needs no index and both call sites shrink.

### F9. Every chart carries two legends, in a 150 px canvas (M)

Where: `plots.js:551-558` and `renderChans` (the `.plot-chans` strip above the canvas: swatch, name, unit), `plots.js:788` (`legend: { live: true }`, uPlot's own legend below the canvas with the same swatches and names plus the value).

Today: as visible in `docs/img/webui.png`, a two-channel chart spends about 20 px on a clickable name strip and about 20 px on a live value strip, both showing the same two names and swatches, around a 150 px plot.
A twelve-channel stream wraps the top strip onto three or four rows.

Why it hurts: in a 360 px sidebar the chrome-to-data ratio is roughly one to two, and the duplication makes neither strip obviously the interactive one.

Change: turn uPlot's own legend off and paint the live value into the existing `.plot-chans` chip after the unit, from `setCursor`, keeping the `min-width: 9ch` tabular-nums treatment that `style.css:298` already applies.
One strip, clickable, with the value on it.

### F10. The chart legend's time readout is labelled "Value:" (S)

Where: `plots.js:746` (`const series = [{ value: fmtPlotX }]`, no `label`).

Today: uPlot falls back to its default x-series label, so the cursor readout reads `Value: 16:37:03.863` (see the screenshot) whatever the active time base is.

Why it hurts: the one place the reader looks to confirm which of the four time bases is in force calls it "Value".

Change: set `label` on series[0] from `state.timeMode` (`host` / `tick (ms)` / `rel (s)`), the same strings `terminal.js:699` already builds for `#plotXLabel`.
Export that map from `timewindow.js` so the two cannot drift.

### F11. The single-trace y axis carries no unit (S)

Where: `plots.js:770-776` (the y axis appears only with exactly one trace shown, and sets `values` but no `label`).

Today: solo a channel and you get a numeric y axis with no unit, even though the channel declared one and `chart.unit` holds it.

Why it hurts: soloing a channel is precisely the moment the user wants to read an absolute value off the axis, and the unit is one property away.

Change: add `label: chart.unit.get(shown[0]) || undefined` to that axis object.

### F12. The CAN column headers are unglossed jargon and one of them mixes units (S)

Where: `can.js:272` (`["id", "dlc", "data", "count", "ms", "age"]`), `can.js:182-187` (`fmtCanPeriod`).

Today: no `th` has a `title`.
`ms` is an EWMA of the inter-arrival period, which nothing says; `count` is messages since the table was reset, not the DLC or a sequence number.
`fmtCanPeriod` prints a bare number below 10 s and `"1.5s"` above it, so a cell reading `1.5s` sits under a header reading `ms`.

Why it hurts: `ms` next to `age` invites reading it as a second timestamp, and a unit suffix appearing in only some cells of a unit-named column is a small but real misread.

Change: add a `title` per header (`dlc`: bytes in the payload; `count`: frames seen since reset; `ms`: estimated period, EWMA of inter-arrival; `age`: since the last frame).
Rename the header to `period` and let `fmtCanPeriod` suffix every value.

### F13. The two best gestures in the panel are undiscoverable (S)

Where: `plots.js:786` (drag zoom), `plots.js:795` and `digital.js:510` (double-click reset), `plots.js:915` (`paneMouseMove`, hovering a terminal line drives every chart cursor).

Today: alt-click solo, shift-click window and the CAN id click all announce themselves in a `title`.
Drag-to-zoom, double-click-to-reset and hover-a-log-line-to-move-the-cursor announce themselves nowhere at all.
The third is the feature that most distinguishes this tool from a plotting library and no user will ever find it.

Why it hurts: an undiscovered feature has the value of a missing one, and F3 shows the zoom is also unexplained once triggered.

Change: put a dim hint span in the Plots `.sub-head` beside `#plotXLabel` reading "drag to zoom, dbl-click resets, hover a log line to scrub", with the full sentence in its `title`.
One element, no new behaviour.

### F14. The CAN export leaves the `ids` field live and inert when `snapshot` is picked (S)

Where: `can.js:502-519` (the `ids` option has no `enabledBy`), `exportdlg.js:95-96` (`enabledBy` names a checkbox field), `can.js:514` (`if (v.format === "snapshot") { exportCan(); return null; }`).

Today: choose `snapshot` and the "CAN ids" text field stays enabled and prefilled while `exportCan()` writes the whole table regardless.
The `What` label with choices `history` / `snapshot` also does not say that one comes from the daemon's capture and the other is what is on screen.

Why it hurts: this is the enabled-and-inert control the codebase already refuses elsewhere, in `syncExportBtn` (`plots.js:632-638`) and `syncDigitalExportBtn`, both citing the same rule.

Change: extend `enabledBy` in `exportdlg.js` to accept `{field, equals}` so a select value can gate a field, then gate `ids` on `format === "history"`.
Relabel `What` to `Source` with the two choices spelled out ("frame history (capture)" / "table snapshot (on screen)").

### F15. Sidebar width and the expand state are forgotten on every reload (S)

Where: `app.js:311-317` (`popoutBtn` writes an inline `--side-w`), `app.js:331-341` (the resizer writes it too), `app.js:362-365` (`--can-h`).

Today: pane layouts persist (SPEC 9.1 says so and `terminal.js` does it), but the sidebar width, the expand toggle and the CAN/Plots split are inline styles that die with the page.
A user who works expanded re-expands after every reload.

Why it hurts: it is the first adjustment anyone makes and the only layout state that is not remembered, so it reads as an oversight rather than a choice.

Change: write all three into `localStorage` on `pointerup` and on the expand click, validated on read the way `chrome.js` validates the colour store and `exportrange.js` validates its range, and re-apply at boot before the first `resizePlots()`.

### F16. A collapsed chart's head says only "stream 0" (S)

Where: `plots.js:517-529` (the collapse toggle and the `.ptitle`).

Today: collapse a chart and all that is left is `▸ stream 0` with its window buttons, pause and export.
Nothing says what is in it, so collapsing three charts to find one means expanding them again.

Why it hurts: collapse exists so several streams fit in the panel, and it removes exactly the information needed to choose between them.

Change: when `chart.collapsed`, append the shown channel names (truncated, with a `title` carrying the full list) to the title; restore the bare title on expand.
`chart.names` and `chart.show` are both to hand in the click handler.

### F17. There is no way to find an id in a table that holds up to 256 of them (M)

Where: `can.js:17` (`MAX_CAN_IDS = 256`), `index.html:79` (the head has unfilter, pause, export, Reset and no filter box).

Today: on a busy bus the table is a 256-row scroller sorted by port, bus and id, in a pane that shows roughly a dozen rows.
Finding `0x321` means scrolling and reading.

Why it hurts: the group collapse helps when there are several buses and does nothing within one; the panel's whole value is "look at this id" and there is no way to get to it.

Change: add a small filter input to `.sub-head` that substring-matches the formatted id (and, later, a name) and hides non-matching rows on rebuild.
`canRowsVersion` already drives the rebuild, so bumping it on input is the whole wiring.

### F18. `Reset` is the only capitalised control in the sidebar, and it means what `clear` means elsewhere (S)

Where: `index.html:79` (`>Reset<` beside lowercase `unfilter`, `pause`, `export`), against `index.html:47-48` (`pause all`, `clear all`) and `index.html:341` (the pane's `clear`).

Today: the same view-only, database-safe action is called `clear` on a pane, `clear all` on the shared toolbar and `Reset` on the CAN table, and only the last is capitalised.

Why it hurts: `Reset` in an instrument UI implies something stronger than clearing a view, and the capitalisation makes it look like a primary action.
This is the one place the otherwise very consistent lowercase-control language breaks.

Change: rename it to `clear` and match the case.
SPEC 9.1 says "Reset clears the table"; update that clause in the same commit.

### F19. The `delta` time base quietly does not apply to the charts (S, low)

Where: `index.html:50-54` (the segment's title reads "Timestamp base for panes and plots"), `plots.js:666-673` (`xAxisValues` has no `delta` branch and falls through to host), `timewindow.js:94-100` (same), `terminal.js:699` (`#plotXLabel` maps `delta` to `"x: host"`).

Today: three of the four buttons drive both the panes and the charts; the fourth drives only the panes.
`#plotXLabel` is honest about it, but that label is only visible in the Plots and Both views and never explains why.

Why it hurts: a control described as governing both surfaces silently governs one, and the reason (a per-row delta has no meaning as an axis) is sound but unstated.

Change: put the reason in the `delta` button's own `title` ("gap to the line above; the charts stay on host time"), and give `#plotXLabel` the text `x: host (delta is terminal only)` in that mode.

## Contradictions between SPEC section 9 and the code

- **SPEC 9.1, CAN panel:** "The bytes that moved since the previous frame for that id are highlighted."
  The code diffs against the last rendered payload on a 1 Hz tick (`can.js:375`, `can.js:553`), so above about 2 Hz it is a diff across many frames.
  Reported as F4; either the code or the sentence has to move.
- **SPEC 9.2, plot panel:** "one chart per stream (sid)" read against "Channel names are unique only within a port" and "the `port` field on `/plot/channels` is what makes the collision visible."
  The UI keys charts on sid alone and lanes on name alone (`plots.js:253`, `digital.js:21`) and shows no port anywhere in either panel, so nothing in the browser makes the collision visible.
  Reported as F6.
- **SPEC 9.1, CAN panel:** "Reset clears the table."
  Everything else with the same semantics is called `clear`.
  Reported as F18; a wording change, not a defect.

No other divergence found: pause-all, the per-surface `id_to` watermark, the shown-window export mode, the bus dividers and tints, the collapsed-set persistence, the drag-zoom-pauses-everything rule, the stepped paths, the undrawn y axis, the 64-channel and 64-lane caps with the count text, and the seeding rules all match section 9 as written.

## Already good, keep it

- **The freeze discipline.** Every surface snapshots what it froze and records a line-id watermark that every export mode carries as `id_to` (`freeze.js`, `can.js:414-446`, `plots.js:985-1016`, `digital.js:634-661`).
  Snapshot rather than index, so a ring that rotates past the freeze cannot blank a paused view.
  This is the strongest part of the design and it is invisible when it works, which is the point.
- **The CAN age clock is the daemon's, not the browser's** (`can.js:87-93`), anchored on the newest row of any channel so a quiet bus on a chatty link still ages.
  Watching a remote daemon across a LAN is a first-class case and this is the reason it reads correctly.
- **Bus grouping.** The divider naming the port in the port's own colour, the per-bus background tint so a group stays identifiable when scrolled past its divider, bus 1 left untinted because it is unmarked on the wire, and the collapsed set persisted by label.
  Deliberate, and it degrades to a plain table with one group.
- **The enum lanes as an FPGA bus envelope** with X-crossings and a hard-clipped centred label (`digital.js:445-475`).
  It is the correct idiom, it is legible at the sizes in the screenshot, and it is visibly not a generic charting default.
- **Stepped, hold-last paths with per-channel auto y scales** (`plots.js:745`, `plots.js:739-742`).
  Right for irregular MCU signals, and the tradeoff is documented in the code where someone would otherwise "fix" it.
- **Controls that refuse to lie.** The export button disables itself and says why when nothing is shown (`plots.js:632-638`, `digital.js:312-318`); `syncWindowButtons` repaints every group after a shift-click so no head shows a span it is not drawing.
- **Keyboard parity.** `makeSpanButton` turns every swatch and name into a real button with `aria-pressed`, and alt-click solo has a `Shift+Enter` equivalent; the view switch and time base are proper radiogroups with `aria-checked`.
- **Empty states that name the wire syntax** ("`!p` / `!pd` / `!ps` events stream live into strip charts here").
  Exactly the right thing to tell a firmware author staring at a blank panel.
- **The visual language.** Uppercase letterspaced micro-headers, monospace for all data, one accent colour used sparingly, an eight-slot categorical palette that holds in both themes, tabular-nums with a fixed min-width so live readouts do not shuffle, and `prefers-reduced-motion` honoured.
  It reads as an instrument, not as a template.

## Already queued elsewhere, not re-raised

- Per-chart height with a drag handle (2026-09-12 P11): `plots.js:778`, `plots.js:855` and `plots.js:865` still hard-code `height: 150`.
  F2 and F9 both make this worse and both are cheaper, so they are worth doing first.
- The whole 2026-09-12 browser checklist, including the CAN pause and byte-diff items, the drag-zoom and double-click items, and the lower-case CAN id filter question (`manual-verify.md`, Panels batch item 12).
- Markers drawn on the charts, the marker list with click-to-jump, and terminal find-and-jump: Phase P2 backlog.
