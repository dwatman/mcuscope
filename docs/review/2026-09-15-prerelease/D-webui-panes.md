# Leg D: web UI data panes (pre-release, v0.4.0..fdd30a2)

Scope: `plots.js`, `digital.js`, `timewindow.js`, `can.js`, `terminal.js`, `pane.js`, `exportrange.js`, and the code they touch (`freeze.js`, `chrome.js`, `api.js resetForDbReset`, `store.py _window_floor`).
Repros live in `~/tt-data/prerelease-2026-09-15/D/` (each `node --test <file>`; they import the real modules and `host/tests/webui_js/dom_stub.mjs` by absolute path).
HTTP repros ran against `mcuscoped --sim --config ~/tt-data/prerelease-2026-09-15/D/daemon.toml` on port 18614 (own db), stopped by PID afterwards.
Stub limits apply throughout: no layout (`clientWidth` 0 unless forced), and its `<select>` keeps any value.

## Findings

### D-1 MED - a history page lands on a pane that was cleared, resumed or reset while it was in flight
- Where: `terminal.js:491-530` (`loadHistoryPage` applies its result after `await api(...)` with no check that the pane is still the one it asked for); writers racing it: `.clear` handler `terminal.js:667-673`, clear-all `terminal.js:796-800`, `setAutoscroll(pane, true)` `terminal.js:296-299`, `api.js:169-175` capture reset.
- Defect: `pane.rows.unshift(...)`, `historyNext`, `historyDone` and `historyLoaded` are written from a request issued against a pane state that no longer exists.
- Scenarios:
  - Scroll a paused pane to its top, click `clear` before the page returns: the cleared pane shows 200 lines, every one at or below its `clearId` (pane.js promises "a cleared pane must not refill with what it cleared").
  - Scroll to top, then `↓ latest`: a live pane gets capture rows prepended above its buffer rows across an unmarked hole (ids 9000 then 11001, no gap divider), and `historyLoaded` 200 on a live pane.
  - A capture reset during the fetch prepends rows from the old capture into the new one.
- DRIVEN: `history_race.test.mjs`, output `after clear: rows 200 ids <= clearId 200 first 8801` and `after resume: autoscroll true rows 1200 first 8801 hole [ 9000, 11001 ] gap rows 0 historyLoaded 200`.
- Class 37 (async read-modify-write spanning an await), web UI instance.
- Fix: stamp the pane with a generation bumped by clear, rebuild, resume and reset; `loadHistoryPage` drops a result whose generation moved.

### D-2 MED - a paused surface's "shown window" export is anchored on the wrong edge, so it can export nothing of what is shown
- Where: `plots.js:1212` (`shownLastMs: chart.window * 1000`), `digital.js:375`, `terminal.js:559-563`, `exportrange.js:57-60`; the daemon measures `last_ms` back from the ts of the `id_to` row (`store.py:1469-1477`).
- Defect: each surface draws a window ending at its own newest sample or row, but sends only a duration plus `id_to = state.maxId`, whose row can be much later (another channel, another port, a `sys` line).
- Scenarios:
  - A stream stops (board crash, disconnect), a `sys` line or marker follows, the chart is paused: the chart shows its last 5 s, `shown` exports the 5 s before the later line, i.e. a header only.
  - A pane filtered to `sys` holding 4 rows over 20 s, paused after one later marker: `shown` exports 1 of the 4.
  - While a drag zoom stands the chart shows the zoom range, yet `shown` exports `chart.window` seconds.
- DRIVEN (HTTP, sim daemon): stopped `sim` at id 1508, marker at 1510.
  - `/plot/export?names=ftest&port=sim&format=long&last_ms=5000&id_to=1510` -> 1 line (header); the same with `id_to=1508` -> 101 lines.
  - `/lines/export?chan=sys&format=text&last_ms=20107&id_to=1510` -> 1 row of the pane's 4.
- Class 23 (export face: "an export button that ignores its surface's freeze"); the `_window_floor` fix moved the anchor to `id_to`, which is still not the surface's edge.
- Fix: `shown` sends the drawn window as `since_ts`/`until_ts` (host time of the frozen or zoomed `xmin`/`xmax`, and first/last row ts for a pane) alongside `id_to`, instead of `last_ms`.

### D-3 MED - tick mode after an MCU reset or 2^32 wrap collapses every later sample onto the old tick, and the lanes' live edge stops
- Where: `plots.js:533` (`tx = chart.lastTick + 1e-4`), `digital.js:84` (same nudge), `digital.js:57` (`digitalLast.tick` only ever grows).
- Defect: the monotonic nudge meant for repeated ticks turns a backward jump into 1e-4 ms steps glued to the pre-reset tick.
- Scenario: 10 s at 100 Hz on ticks 1,000,000.., board resets, 10 s more on ticks 0..: in tick mode the post-reset 10 s is drawn as 0.1 ms at the right edge, and `digitalRightEdge()` stays at 1009990 so the lanes stop scrolling while live; a terminal-row hover (real small tick) falls outside every chart window.
- DRIVEN: `plots_probe.test.mjs` test 1, `post-reset first/last 1009990.0001 1009990.0999999465 post-reset span ms 0.100 (real 9990)` and `lane b0 ... edge 1009990`.
- New class candidate: a monotonic fix-up applied to a clock that legitimately restarts. `estimateTick` already treats a reboot as a new anchor; the charts and lanes do not.
- Fix: on a backward tick jump beyond a slack, offset later ticks per chart and lane so the axis continues by the host-time gap (or break the trace); see Decisions.

### D-4 MED - the pause-all label is not recomputed when a member is born live or destroyed, so the button does the opposite of its label
- Where: `plots.js:422-438` (`ensureChart` calls `freezeChanged` only when `bornPaused()`), `digital.js:150-198` (`addDigitalLane`), `can.js:127-131` (first row makes `isLive` true), `can.js:619-636` (`clearAllCan` makes it false); none recompute the label.
- Scenarios:
  - Pause the pane (scroll up) and the chart by hand, label "resume all"; the first CAN frame or a new enum lane arrives live; label still "resume all"; clicking runs `pauseAll(anyLive()=true)`, which pauses.
  - With CAN the only live surface, CAN `clear` leaves "pause all" over `anyLive() == false`; clicking runs `pauseAll(false)` and resumes every paused pane and chart.
- DRIVEN: `label_probe.test.mjs`, `after first CAN frame: label "resume all" anyLive true`, `after first lane: label "resume all" digital live true`, `after CAN clear: label pause all anyLive false`.
- Class 25 ("every create and destroy recomputes whatever renders it").
- Fix: call `freezeChanged()` from `ensureChart`, `addDigitalLane`, `canIngest` on insert, `clearAllCan`, `clearAllCharts` and `clearAllDigital` (or render the label from a 1 s tick).

### D-5 MED - CAN ages read fresh after a page load onto a board that went silent
- Where: `can.js:94-105` (`canNow` anchors on the newest row ts seen, then adds browser-elapsed time).
- Defect: with no traffic since the capture's newest line, the anchor is that line, so a frame 10 minutes old reads as 0 ms old.
- Scenario: a 10 Hz id stopped 600 s ago, reload the page: the backfill ingests its frames, age reads `1ms`, class `age-fresh`; before the reload it was `age-dead`.
- DRIVEN: `can_probe.test.mjs` test 1, `age text 1ms class age-fresh periodic true`.
- Class 12 (healthy-while-dead surface).
- Fix: anchor on a daemon clock reading taken at load (a `now` in `/status` or the backfill envelope), falling back to the newest row only while none is known.

### D-6 MED - clicking a CAN id filters the pane to nothing for wire forms the table itself decodes
- Where: `can.js:194-198` (`canFilterPattern`).
- Defect: the regex mirror is narrower than `parseCanEvent`: upper-case id only (SPEC 1 allows either case), bus 1 only as `!can` (SPEC 2.5 makes `!can1` a synonym), single spaces only (the parser splits on whitespace runs); and it does not separate standard from extended ids.
- Scenario: a board printing `!can 12 - 7df 0201` gets a `7DF` row; the click shows an empty pane. A standard 0x7DF row's click also matches `!can 12 x 7DF 0201` (a different row).
- DRIVEN: `can_pattern.test.mjs`: own pattern matches `false` for the lowercase, `!can1` and double-space forms; the std pattern matches the ext frame `true`.
- Class 19 (two engines validating one thing).
- Fix: case-insensitive id, `!can1?` for bus 1, `\s+` separators, and a flags clause (`[^\sx]*` vs `\S*x\S*`) that splits std from ext.

### D-7 MED - a remote frame wipes the byte-change mask, so an RTR-polled id never lights a byte
- Where: `can.js:142-146` (`e.moved = 0` whenever either payload is empty, and `e.hex` becomes `""` after an RTR).
- Defect: SPEC 9.1 says a remote frame lights nothing; the code also discards changes already accumulated since the last repaint and leaves the next data frame with no baseline.
- Scenario: data `AABB`, paint, data `AACC` (byte 1 moved), RTR, data `AACC`, paint: nothing lit. With a requester and responder on one bus (data, RTR, data, RTR) no byte of that id ever lights.
- DRIVEN: `can_probe.test.mjs` test 2, `lit bytes after data,changed,rtr,data: 0`.
- Class 56 (baseline reset between phases).
- Fix: keep the last data payload as the diff baseline separately from the displayed row; an RTR neither diffs nor clears `moved`.

### D-8 LOW - collapsing a CAN group divider does nothing while the table is paused
- Where: `can.js:242-251` (`toggleCollapsed` bumps `canRowsVersion`) against `can.js:283-284` (a paused table keys its view on `canFrozenVersion`).
- Defect: the id filter was moved into the view key for exactly this reason (comment at 281); the collapse was not.
- Scenario: two groups, pause, click a divider: rows stay, caret stays, the stored set flips; resume later collapses it (or a second click un-flips storage while the view never moved).
- DRIVEN: `can_collapse_paused.test.mjs`, `live rows before 2 after click 1 caret "▸"` vs `paused rows before 2 after click 2 caret "▾" stored ["a CAN1"]`.
- New class candidate: a view cache key omitting a user-controlled input (one of two siblings fixed).
- Fix: key `canView` on the collapsed set too, or track a separate `canLayoutVersion` bumped by collapse.

### D-9 LOW - the digital panel paused before its first lane moves its watermark and snapshot forward with that lane
- Where: `digital.js:100-103` and `:118` (`anchorDigitalFreeze` after the push, overwriting `digitalFrozenId`).
- Defect: SPEC 9.1 says a surface records the id it had ingested when it froze; the watermark jumps to the first later sample, and the snapshot includes a post-pause vertex. A chart born paused (sibling) keeps an empty snapshot instead.
- Scenario: pause at `maxId` 100, first enum sample at id 500: watermark reads 500, `lane.frozen.vs.length` 1, still paused.
- DRIVEN: `label_probe.test.mjs` test 2, `watermark at pause 100 watermark now 500 frozen snapshot vertices 1 still paused true`.
- Class 23.
- Fix: keep the pause-time `digitalFrozenId`; anchor only the right edge on the first sample (or snapshot empty, as `ensureChart` does). See Decisions.

### D-10 LOW - a single-trace chart's y-axis unit label keeps the old unit after a redefinition
- Where: `plots.js:544-547` (unit change calls `renderChans` only) against the rebuild predicate `plots.js:1019-1021` (series count and theme only); the label is read at build, `plots.js:935-941`.
- Scenario: `!pd 0 v:u2:mV`, samples, then `!pd 0 v:u2:V`: chip reads `V`, axis still `mV`.
- DRIVEN (canvas width forced to 300 on the stub): `yaxis_unit.test.mjs`, `after redefinition: axis label mV chip [ 'V' ]`.
- New class candidate: a rebuild predicate omitting an input the build reads.
- Fix: `buildUplot(chart)` when a unit changes on a chart with one shown trace (or add units to the predicate).

### D-11 LOW - marker and gap rows carry no tooltip, so a long marker is clipped with no way to read it
- Where: `terminal.js:88-101` (only the `.msg` branch sets `title`); `.ln.marker .divider` has no `min-width: 0` or ellipsis (`style.css:219`).
- Scenario: a 240-character `!m` marker: debug row title 240 chars, marker row title empty; double-click copies the raw line with its `!m @tick` prefix.
- DRIVEN for the title (`marker_title.test.mjs`, `marker row title length 0 | debug row title length 240`); the clipping itself needs a real browser.
- No registry class (the one-line row rule applied to one of three row kinds).
- Fix: set `div.title` to the displayed marker text, and give the divider text `min-width: 0; overflow: hidden; text-overflow: ellipsis`.

### D-12 LOW - `termState` pane configs from localStorage are not type-checked
- Where: `terminal.js:770-777` into `pane.js:18-19` and `terminal.js:651-652`.
- Scenario: `{"panes":[{"port":7,"channels":"resp","regex":{"a":1}}]}` loads a pane on port `7`, channels `r,e,s,p` (no real channel), regex `/[object Object]/`, and `persistState` writes it back, so a reload never recovers.
- DRIVEN: `termstate.test.mjs`, `port 7 channels ["r","e","s","p"] regexSrc {"a":1} regex /[object Object]/ persisted ...`.
- Class 34 (type-check clause for localStorage values).
- Fix: accept `port` only as a string, `channels` only as an array filtered to `ALL_CHANS`, `regex` only as a string.

## Sweeps

### Class 6 - non-finite values reaching chart arrays
Command: `grep -n "xsHost\.push\|xsTick\.push\|vs\.push\|ys\.get(.*)\.push\|ys\.set(\|\.fill(null)\|uplot\.setData\|new uPlot(" plots.js digital.js`: 10 sites.
- `digital.js:86` x and value push: complies (x gated at `:52`; values are decoded integers, 0/1 bits, or seed values gated at `plots.js:314`).
- `plots.js:536`, `:537` x push: complies (gated at `:530`; the nudge adds a finite 1e-4).
- `plots.js:560` y push: complies (live: `parsePlotValue`, `decodePlotField` f4 gate, post-scale gate `:239`; seed: `:314`).
- `plots.js:568`, `:586-587`, `:1004`: exempt, null is uPlot's gap value.
- `plots.js:957`, `:1025`: consumers of `currentData`, which only slices the gated arrays or the snapshot copies of them: complies.

### Class 23 - a rebuild path un-freezes a paused surface
Command: `grep -n 'registerSurface("' webui/*.js`: 4 surfaces; writers of each surface's contents listed and ruled.
- charts (`plots.js:1192`): `addSample` complies (snapshot); `redrawPlots`/`buildUplot`/`renderChans` solo and toggle/theme rebuild/time-mode/zoom complies (`chartDrawData`); seed complies (through `addSample`); `clearAllCharts` complies (latch); export violates (D-2).
- digital (`digital.js:729`): `digitalIngest` violates when paused with no frozen edge (D-9), otherwise complies; redraw, `valueAt`, cursor snap, readouts comply (`laneDrawData`, `pendingVal` not written while paused); colour, window, `clearAllDigital` comply; export violates (D-2).
- can (`can.js:532`): `canIngest`/eviction comply (snapshot); `renderCan`/`ageCan`/filter comply; collapse is refused rather than un-frozen (D-8); `clearAllCan` complies; export snapshot and `canShownLastMs` comply (`canFrozenNow` is at or past the `id_to` row ts).
- panes (`terminal.js:316`): `flush` complies; `rebuild` complies (`frozenRows`); `countPending` complies; `setKnownPorts`/`setTimeMode` render comply; `resetForDbReset` complies (freeze zeroed with the rows); `loadHistoryPage` violates (D-1, async writer past a reset); export violates (D-2).

### Class 25 - group state reaching only existing members
Group operations: `grep -n "pauseAll(" webui/*.js` (3 calls: `terminal.js:791`, `plots.js:877`, `plots.js:894`), clear-all `terminal.js:793`, shift-click window `chrome.js:118`, unfilter `terminal.js:822`.
- Create sites: `addPane` complies (born paused, label recomputed); `ensureChart` complies on state, violates on label (D-4); `addDigitalLane` state is the panel's single flag, complies, label violates (D-4); `canIngest` new row state complies (single flag), label violates (D-4).
- Destroy sites: `closePane` complies; clear-all complies (`updateShared` at `terminal.js:803`); `clearAllCan` from its own button and `resetForDbReset` violate (D-4); CAN eviction exempt (the row set stays non-empty).
- Shift-click window: a chart built afterwards takes `PLOT_WINDOW_DEFAULT` (`plots.js:428`); ruled under Decisions, since SPEC 9.2 words it as an action.
- Unfilter: exempt, it targets the panes a click filtered, not a group state.

### Class 26 - frozen view re-derived from a rotated ring
Frozen views: 4 (plus the lanes' edge).
- `chart.frozen` (`plots.js:1174`): complies, copies at pause.
- `lane.frozen` and `digitalFrozen` (`digital.js:107-115`): complies.
- `canFrozen` (`can.js:508`): complies, entry copies.
- `pane.frozenRows` (`terminal.js:305`): complies; history rows live only in `pane.rows` and a paused rebuild drops them, as `pane.js` documents.

### Class 32 - function tested as pure that mutates module state
Commands: `grep -n "^let \|^const X = new Map|Set|\[\]"` over the 7 files (60 module-level mutables), and every exported helper of the DOM-free modules.
- Exported pure helpers (`timewindow.js` 17, `pane.js` 7, `exportrange.js` 5 of 7, `can.js` `changedBytes`/`canFilterPattern`/`canPeriodic`/`canAgeClass`): complies, none writes module state; `setZoom`, `loadRange`, `saveRange` are exempt (stateful by name); `noteTickAnchor` mutates only its argument.
- Order check: node 22 has no test shuffle, so each of the 284 tests in the 36 leg-D test files was run alone (`isolate.py`, output `isolate.out`).
  35 fail alone in 13 files: they are story-style and rely on an earlier test's state, e.g. `plots_finite` "an out-of-range tick never reaches the x array", whose three negatives pass vacuously alone because no `!pd` exists.
  No live defect (file order is fixed); see Decisions.

### Class 34 - wire-named key on a prototype-bearing store
Command: `grep -n "JSON.parse\|= {}\|= Object" <7 files>`: 5 sites, plus every `x[name]` lookup.
- `plots.js:28` `PLOT_TYPES`: complies (null prototype).
- `can.js:237` collapsed set: complies (array, strings filtered, `Set`).
- `exportrange.js:31`: complies (`validate` type-checks every field).
- `terminal.js:772` `termState`: violates the type-check clause (D-12).
- `can.js:320` `COL_TITLES`: exempt, static keys.
- Lookups: `plotTitles[chart.key]` complies (`parseTitles` null prototype); `TAG[chan]` exempt (chan is the daemon's CHECK-constrained set or client-side `gap`); `TIME_AXIS_LABELS[state.timeMode]` complies (validated against `TIME_MODES`); `LANE_KINDS[lane.kind]` exempt (only `enum`/`bits` are routed there); all other name-keyed stores are `Map`s (`plotDefs`, `seedMaxId`, `charts`, `plotChannelMeta`, chart `ys`/`unit`/`show`/`isInt`, `digitalLanes`, `laneGroups`, `canRows`, `canFrozen`, `tickAnchors`).

### Class 36 - periodic catch-up loop without a burst cap
Command: `grep -n "while\s*(" <7 files>`: 4 sites (`timewindow.js:94`, `:103`, `:208`, `digital.js:608`), all binary searches over a bounded array: exempt, no schedule variable. The three timers (`plots.js:1270`, `can.js:653`, `terminal.js:238`) are `setInterval` with no catch-up.

### Class 51 - boundary fetch whose empty result cannot re-fire
Command: `grep -n "scrollTop\b.*<\|IntersectionObserver\|atTop" webui/*.js`: 2 sites.
- `terminal.js:684`: exempt, not a fetch.
- `terminal.js:689` `loadHistory`: complies, an empty filtered page walks on below the served page under `HISTORY_HOPS` (5), and a short page ends the walk. Separate defect on the same path: D-1.

### Class 56 - change marker diffed against the last paint
Command: `grep -n "moved\|changes\|_last\b\|prev" webui/*.js`: 105 lines across `webui/*.js`, 59 in the leg-D files (the rest belong to other legs' files).
Baselines in leg-D files:
- `can.js:143` per-frame `moved` accumulation: complies (per item at ingest), except the RTR reset at `:145` which violates (D-7).
- `can.js:434`, `:454` mask consumed per paint: exempt, the indicator is defined per repaint interval (SPEC 9.1).
- `can.js:519` cleared on resume: complies (SPEC 9.1).
- `terminal.js:34-35`, `timewindow.js:184-186` delta: exempt, per displayed row (SPEC 9.1).
- `timewindow.js:225-227` tick anchor thinning: exempt, not a change marker.
- `pane.js:52-53` ts column width: exempt, a layout high-water mark.
- The remaining lines are comments or unrelated identifiers (`preventDefault`, `prev` aliases in `setKnownPorts` and `filterPaneTo`): exempt.

### Class 59 - display rule defeating hidden
Command: `grep -n "hidden\]" webui/style.css`: 1 site, the global `[hidden] { display: none !important; }` (`style.css:40`): complies. `display:` with `!important`: only `style.css:428` (`none`): complies. `grep -n "style.display" webui/*.js`: 5 sites, all in `settings.js`/`statusbar.js` (not leg-D files) and none in the 7 files: complies for this leg.

### Class 61 - re-render rewriting a field being typed in
Command: `grep -n "\.value = " webui/*.js`: 52 sites; 6 in leg-D files.
- `terminal.js:589` `o.value`: exempt, a new option.
- `terminal.js:593` `sel.value = cur`: complies, runs on pane creation and from the status poll only when the alias set changed, writing the pane's own model value into a select.
- `terminal.js:651`: complies, pane creation.
- `terminal.js:660`: complies, the clear button the user pressed.
- `terminal.js:842` `setPaneRegex`: complies, a CAN id click or unfilter (the class's named exception); unfilter restores only where `regexSrc` still equals the clicked pattern, and `applyRegex` updates `regexSrc` per keystroke.
- `plots.js:494`: complies, the rename input on open.
- The CAN id filter box has no value writer.

## Decisions for the owner

- Tick mode across an MCU reset or wrap (D-3): continue the axis by the host-time gap, or break the trace at the reset. Either is defensible; today it is neither.
- Shift-click window span: an action on the charts that exist, or a group state a chart built later (new stream, clear-all) inherits. SPEC 9.2 words it as an action; class 25 would call it a group state.
- The digital panel paused before any lane (D-9): re-anchor on the first sample (current, shows one vertex) or stay empty like a chart born paused. SPEC 9.1 "a frozen surface keeps showing what it froze" favours empty.
- `shown` while a drag zoom stands (D-2): export the zoom range, or keep `shown` meaning the window selector's span.
- Story-style JS tests (class 32 order check): 35 of 284 tests are meaningful only in file order. Accept, or require each test to build its own state.

## The two questions

1. Least confident:
   - D-1's reach in practice: the race window is one `/lines` request, milliseconds on a small local capture, longer with `match=` over a large one. The defect is driven; the frequency is not measured.
   - D-2's pane half assumes a filtered pane whose last row is well before `frozenId`; driven with `chan=sys`, not re-driven through the real dialog (the dialog is `exportdlg.js`, outside this leg).
   - D-8, D-10 and D-11 were driven on the stub; the visible effect (caret, axis label, clipping) needs a real browser to confirm.
2. Not yet checked:
   - The same generation gap as D-1 in the other awaited writers these surfaces share: the plot history seed (`api.js`) against clear-all and a capture reset while it is in flight.
   - D-3's sibling in the terminal: `lineTick` stamps and the chart hover after a reset (only reasoned: the hover falls outside the window).
   - Whether the CAN table's MAX_CAN_IDS eviction churn (more than 256 live ids re-evicting each frame, period never established) is acceptable; not driven.
