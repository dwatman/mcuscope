# Fix-diff review 2: web UI panes (9ad910c..HEAD, HEAD 5489d6e)

Scope: `webui/terminal.js`, `can.js`, `plots.js`, `digital.js`, `pane.js`, `timewindow.js`; the round's pane tests; SPEC 9.1/9.2 and the CHANGELOG lines that describe pane behaviour.
`api.js runBackfill` was read as the consumer of `clearGen` / `canClearGen()` / `plotSeedGen()`.
Probes: `~/tt-data/prerelease-2026-09-15/fixdiff2-panes/` (`a_clear_backfill`, `b_break_mode`, `runalone.mjs`, and `webui-nogate/` - a copy of the webui tree with the chart gate removed, for the pre-fix comparison).
Run-alone: 25 pane test files, 187 tests, each run on its own with `--test-name-pattern`; all pass.

Counts: HIGH 1, MED 1, LOW 5.

Ruled out, kept for the record:

- A capture reset landing while a backfill is in flight: not reachable. `staging` is armed before every `runBackfill` call and the capture token is a staged row, so `noteCapture` -> `resetForDbReset` can only run from `drainStaging`, after that backfill resolved; `feedStaged` re-stages everything behind the token.
- A null vertex can never be a lane's newest: `digitalIngest` always pushes the sample's own value straight after it, so `setDigitalPaused(false)`'s `pendingVal = vs.at(-1)` cannot blank a resumed readout.
- A cursor parked inside the reset gap: `valueAt` returns `""` for a null vertex, not `"null"`.
- `rulings_panes_reset.test.mjs:115` `deepEqual(drops, [])` is not vacuous: probe b1 shows a real low bit level does emit `lineTo(x, 26)` in that lane geometry.
- A history seed cannot open a false epoch on a chart that already holds samples: `seedTargetHasData` refuses a non-empty target, so a seeded member's `prevTick` is null.
- A clear landing between two backfill pages: every page is fetched before `canCleared` / `chartsCleared` are read, and every row of the walk predates the first fetch, so the all-or-nothing gate is right.
- `canIngest` caches no cross-row state (it builds and re-keys rows only), so the CAN gate has no sibling of PD-1.

## PD-1 HIGH: a clear-all during the backfill drops the stream definitions inside it, and every later sample is undecodable

- Where: `api.js:526,532` (`chartsCleared` gating `plotIngest`), against `plots.js:251-255` and `api.js:237-244`.
- Defect: the generation gate was put on `plotIngest`, which is not only the view writer. Its first branch caches `!pd` definitions into `plotDefs`, a cache neither `clearAllCharts` nor a capture reset drops (deliberately: `api.js:181-183`). `seedPlotDefs` fetches only definitions at or **below** the window's oldest row, because the window's own `!pd` rows "arrive with it" (`api.js:239`) through this very loop. Skipping the loop therefore loses every definition announced inside the backfill window.
- Failure: reconnect after an outage the board rebooted in, so the gap carries the board's `!pd` announcements; the terminal floods; the user presses clear-all while the paged walk is still running. From then on every live `!ps` on those streams decodes to nothing: no chart, no digital lane, no warning, until the board is reset again. The same on a page load whose backfill is slow.
- DRIVEN: `a_clear_backfill.test.mjs` test 2, run alone: after clear-all during the backfill, two live `!ps 0` rows leave `charts.size` at 0, where the control test (no clear) has a chart and two points. Against `webui-nogate/api.js` (the gate removed) the same live rows decode, so the round's fix is what introduced this, not a pre-existing hole.
- Class 73 (the fix's own class), new shape: a generation gate placed on a call that also primes a cache the clear does not drop.
- Fix: gate the plotting, not the decoding. In the row loop, let a definition through regardless: `if (!chartsCleared || (row.chan === "event" && typeof row.raw === "string" && row.raw.startsWith("!pd"))) plotIngest(row);`. The same question is owed on any future gate over `plotIngest`.

## PD-2 MED: a pane added while the backfill is out and then cleared refills with what it cleared

- Where: `api.js:481` (`const paneClears = new Map(panes.map(...))`) and `:535`.
- Defect: the map is a snapshot of the members that existed when the backfill started. A pane created during the backfill is not in it, so its clear is never noticed and its `clearId` is never raised past the delivered rows.
- Failure: a slow daemon (or a long reconnect walk); press `+ pane`, then clear on the new pane. When the backfill lands, the new pane fills with the rows captured before the click, while every pane that already existed correctly stays empty.
- DRIVEN: `a_clear_backfill.test.mjs` test 3: the pane's rows are `[10, 11, 12]` where `[]` is expected.
- Class 25 (a group state that only reaches the members that already existed).
- Fix: iterate the live array at the end and default the absent snapshot to the birth value: `for (const p of panes) if (p.clearGen !== (paneClears.get(p) ?? 0)) p.clearId = Math.max(p.clearId, state.maxId);`.

## PD-3 LOW: SPEC files the reset break under the tick base; the code breaks in every time base

- Where: `docs/SPEC.md` 9.2 "Under the tick base ... Charts and lanes break their line there" against `plots.js:534-540` and `digital.js:76`.
- Defect: the gap point and the lane's null vertex go into the shared arrays, so the host-base and relative-base views break at the reset too. The contract sentence sits inside the tick-base bullet and says nothing about them.
- Failure: a reader (or the next fix) takes SPEC at its word and treats a host-base break as a defect, or removes it.
- DRIVEN: `b_break_mode.test.mjs` test 2: in `timeMode "host"`, `currentData` returns 9 x values for 8 samples with a null among the y values, and the lane's `vs` carries the null.
- Class: contract (SPEC 9.2). The behaviour itself looks right: the board did reboot.
- Fix: one clause, either "Charts and lanes break their line there, in every time base" or the sentence moved out of the tick-base bullet.

## PD-4 LOW: the history budget divider can read "gap: 0 lines not loaded"

- Where: `pane.js:150`.
- Defect: `rows[0].id - 1 - floor` is 0 when the spent page reaches exactly the first line past the clear point. `loadHistoryPage` sends `since_id = clearId`, so the count can never go negative, but it can be zero.
- Failure: a cleared pane paged to the top; the last page serves exactly `HISTORY_PAGE` rows ending one above the clear point and spends the 5000-row budget. The pane shows a divider claiming nothing was not loaded.
- DRIVEN: `planHistoryPage({served: 200, truncated: false, loaded: HISTORY_MAX - 200, oldestServedId: 1001, floor: 1000})` returns `gap: 0 lines not loaded`, `done: true`.
- Class 74 (a limit shown beside a figure it is not measured against).
- Fix: `if (spent && !exhausted && rows.length && rows[0].id - 1 > floor)`.

## PD-5 LOW: the digital lanes are covered by the backfill clear only through `plotSeedGen`

- Where: `api.js:483,526,532`; `digital.js:809` (`clearAllDigital` bumps nothing of its own).
- Defect: the lanes are fed by `plotIngest` -> `routePoints` -> `digitalIngest`, so they are protected only because `clearAllDigital` is never called without `clearAllCharts` beside it (`api.js:185-186`, `terminal.js:820-821`). Nothing states or asserts that, and a digital-only clear would refill the lanes silently.
- REASONED (both call sites enumerated by grep; no third exists).
- Class 25 / 73.
- Fix: bump `seedGen` from `clearAllDigital` as well (it is already the token both consumers read), or name the coupling in one line at `api.js:483`.

## PD-6 LOW: `resetForDbReset` bumps the CAN and chart generations but not the panes'

- Where: `api.js:163-177` against `:481`.
- Defect: a capture reset moves `canClearGen()` and `plotSeedGen()` (through `clearAllCan` / `clearAllCharts`) but leaves `p.clearGen` alone, so the three surfaces would disagree if a backfill were ever in flight across it. Today none can be (see the ruled-out list), but that invariant is unstated and load-bearing, and it is exactly the invariant the round's own staging comments exist to keep.
- REASONED (read `drainStaging` and `feedStaged`; `staging` is armed before both `runBackfill` call sites).
- Class 73.
- Fix: `p.clearGen += 1` in the reset loop for symmetry, or one line at `api.js:170` recording why the panes need no token here.

## PD-7 LOW: two `deepEqual(seen.refusals, [])` with no cited positive control

- Where: `rulings_panes_export.test.mjs:84`, `export_paused_window.test.mjs:229`.
- Defect: neither file ever shows `installExportDaemon`'s `refuse()` firing, so a double that stopped checking would read as "nothing was refused". `exportdlg.test.mjs:431` does prove the guard fires (it filters a deliberately bad session out of the list), but neither site cites it.
- REASONED.
- Class 78.
- Fix: cite `exportdlg.test.mjs:431` in a comment, or press one export the guard refuses and assert it was recorded.

## Hunks read

Source: `terminal.js` 4/4, `can.js` 1/1, `plots.js` 6/6, `digital.js` 16/16, `pane.js` 2/2, `timewindow.js` 1/1.
Docs: `SPEC.md` 27 hunks, of which the 9.1 export-dialog, 9.2 time-base, 9.2 digital-panel, 9.2 shift-click and 9.1 clear-view hunks were read against the code; the 20 hunks covering 2.5, 3.1, 3.3, 3.4, 4 and the bundle belong to other legs and were skimmed only. `CHANGELOG.md` 8 hunks, pane lines read, CLI and daemon lines skimmed.
Tests, all hunks read: `can_age_tick` 1, `can_freeze_surface` 1, `can_logic` 6, `digital_edge` 2, `digital_paused_freeze` 5, `digital_repaint` 3, `digital_zoom` 3, `export_paused_window` 4, `followups_backfill_clear` 1, `plots_export_button` 4, `plots_finite` 13, `plots_paused_freeze` 3, `plots_seed_paused` 2, `plots_theme_rebuild` 1, `plots_zoom` 8, `rulings_panes_export` 1, `rulings_panes_reset` 1, `rulings_panes_tickclock` 1, `rulings_panes_window` 1, `sweep_panes_digital` 1, `sweep_panes_plots` 1, `sweep_panes_terminal` 1, `sweep_panes_tick` 1, `terminal_history` 1, `terminal_paused_freeze` 7.
`dom_stub.mjs` did not change in the range.

## Manual-verify additions

Not already in `manual-verify.md`:

- [ ] Tick reset, chart cursor: park the cursor on the gap point; every channel reads `--`, and the y axis does not jump. uPlot's stepped path builder (`uPlot.paths.stepped({align: 1})` with `spanGaps: false`) is what renders the break, and the stub has no uPlot.
- [ ] Host base, board reset mid-stream: the trace and the lanes break there too. Confirm the break is wanted before PD-3's SPEC wording is chosen.
- [ ] 64 lanes live with one stream quiet: CPU with the new per-lane `drawnEdge` key, which now repaints every visible lane on every 5 Hz tick while the edge moves.
- [ ] Two boards on one port under the tick base (SPEC 9.2's documented limit): the lower-uptime board's lanes sit off screen, the charts are unaffected.
- [ ] A host wall clock stepped back (`timedatectl set-time` or an NTP step) with charts, lanes and the CAN table live: SPEC 9.2 now documents the glued edge and the stale CAN ages; confirm that is what happens.
- [ ] A 2^32 tick wrap: a firmware seeding its tick near 0xFFFFF000 crosses it in seconds; the axis continues and the trace breaks once.
- [ ] After PD-1 is fixed: `kill -STOP` the daemon, reload, clear-all while the backfill hangs, `kill -CONT`; the board's streams still chart.

## The two questions

1. **Least confident, rechecked.** How reachable PD-1 is. The click window on a local first connect is milliseconds, which would make it a curiosity. I re-read the reconnect path instead of reasoning from the page load: `fetchSince` pages `BACKFILL_MAX` rows over an outage-sized gap, several round trips, and clear-all right after a reconnect flood is the natural user action. I also re-drove it against `webui-nogate/` to be sure the fix caused it rather than exposing something older. Still reasoned rather than driven: PD-5's "no third call site" (grep, not execution), PD-6's staging invariant (read, not raced), and whether the host-base break of PD-3 is wanted.
2. **What should have been checked.** The gate was placed on the ingest call, and nobody asked what else that call does besides fill the view. `plotIngest` primes `plotDefs` (PD-1); `canIngest` was checked for the same shape and has none; `pushBuffer` is correctly left ungated. The other gap is the snapshot shape: `paneClears` is a Map over the members that existed, which is registry class 25 in a new place, and neither the rulings round nor the sweep ran class 25 over the round's own diff. Worth one pass over every `new Map(xs.map(...))` and `[...xs]` taken before an await in `api.js` and `exportdlg.js`.
