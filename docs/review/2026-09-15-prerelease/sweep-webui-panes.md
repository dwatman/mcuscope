# Registry sweeps: web UI panes (classes 70, 72, 73, 74, 76, 77)

Files swept whole: `plots.js`, `digital.js`, `terminal.js`, `can.js`, `pane.js`, `exportrange.js`, `timewindow.js`, `chrome.js` (all under `host/mcuscope/webui/`).
Sites are cited at the pre-fix line numbers.

Result: 8 violations, all fixed, each with a break-it test and revert-verified (14 of 14 mutants killed).
Final run: `uv run python -m pytest tests/test_webui_js.py -q`: 2 passed; `node --test` over `tests/webui_js`: 758 pass, 0 fail; ruff clean.

New tests, all under `host/tests/webui_js/`:

- `sweep_panes_digital.test.mjs` (SD 1-4)
- `sweep_panes_plots.test.mjs` (SP 1-2)
- `sweep_panes_terminal.test.mjs` (ST 1-2)
- `sweep_panes_tick.test.mjs` (SK 1-3)

## Class 70: status codes and close codes

Command: `grep -n "status_code ==\|status ===\|\.code ==" <files>`: **0 sites**.
Widened, because a client can map every failure without naming a status: `grep -n "api(\|await \|catch" <files>` found one request site.

| Site | Verdict |
|---|---|
| `terminal.js:503-509` history page: any failure of a request carrying `match` retries once without it | exempt because the fallback is right for every cause. The rows are re-filtered locally, so after a transient failure the cost is one extra request and a sparser page, never a wrong row. For this query shape `/lines` sends 400 only for the pattern (`_check_match`, `MatchBudgetExceeded`, `server.py:1667-1678`). |

No WebSocket close-code branch lives in these files (they are in `api.js`).

## Class 72: focus and caret moves

Command: `grep -n "\.focus()" <files>`: **4 sites**.
Widened to `\.focus(\|\.select()\|scrollIntoView\|scrollBy\|scrollTo(\|scrollTop =\|showPicker\|\.click()\|\.blur()\|setSelectionRange\|showModal`: **13 code sites** (plus one comment line, `chrome.js:81`).

| Site | Caller | Verdict |
|---|---|---|
| `plots.js:505` `titleEl.focus()` in rename `finish` | Enter/Escape keydown, **and the input's blur** | **violates**: a blur is focus leaving for where the user clicked, and refocusing inside the blur handler cancels that move in Chromium. The next keystrokes go to the title span, whose Enter reopens the rename. Fixed. |
| `plots.js:514-515` `input.focus()`, `select()` | `startRename` via title click / span-button key | complies |
| `plots.js:1293` `scrollBy` | `plotFold` click | complies |
| `terminal.js:209` `scrollTop = 1e9` | `render` while autoscroll | exempt because it is the live pane following its own tail; focus does not move |
| `terminal.js:530` `scrollTop +=` | history page landing (after a user's top hit) | exempt because it keeps the rows in view steady under the prepend; focus does not move |
| `terminal.js:681` `matchInput.focus()` | `.match-clear` click | complies |
| `terminal.js:855` `scrollIntoView` | `filterPaneTo` via `can.js` id click/keydown only (`app.js:29` wires it) | complies |
| `chrome.js:85-86` `showPicker()`, `click()` | swatch click/keydown | complies |
| `chrome.js:212-213` `next.click()`, `next.focus()` | radiogroup keydown | complies |
| `chrome.js:228` `btn.click()` | dialog Enter keydown | exempt because it presses a button and moves no focus |

Fix: `finish(commit, refocus)`, so only Enter and Escape return focus to the title.

## Class 73: awaited results written into a replaced view

Command: `grep -n "await " <files>`: **3 sites** in 2 functions.
Widened to `.then(` (1 site) and to every export of these files that another module calls after its own await (enumerated from the importers' `import` lines): `plotSeed`, `noteDaemonNow`, `setKnownPorts`, `canIngest`/`plotIngest`, and the export `build` closures.

| Site | Verdict |
|---|---|
| `terminal.js:477-491` `loadHistory` | complies: the loop ends when a page reports a generation mismatch |
| `terminal.js:494-537` `loadHistoryPage` (awaits at 504, 508) | complies. `historyGen` is taken before the first await and checked after both, before any write. Clear, clear-all, rebuild (and so resume, filter and port change) and the capture reset (`api.js:174`) all call `resetHistory`. A regex typed during the page lands inside the 200 ms debounce, whose rebuild then drops it. |
| `terminal.js:715` clipboard `.then` | exempt because it toggles a class on the clicked element only |
| `plots.js:379` `plotSeed` (called after awaits in `api.js:340-371`) | complies: `api.js` re-checks `wsGen` and `plotSeedGen()` after its awaits, and `seedTargetHasData` refuses a non-empty target |
| `can.js:115` `noteDaemonNow` (after the `/status` await) | complies: whole-value, forward-only anchor; see class 77 for the clock itself |
| `terminal.js:616` `setKnownPorts` (after the `/status` await) | complies: replaces the whole alias list |
| `canIngest`, `plotIngest` from the backfill | complies: staged and generation-checked in `api.js` (not these files) |
| export `build` closures: `plots.js:1250`, `digital.js:421`, `can.js:638`, `terminal.js:583` | exempt because they run after the dialog's await but write no view state. Names, ports and shown windows are captured at open; the modal dialog blocks a user-driven filter change. See hand-off H1 for a capture reset during the dialog. |

## Class 74: a shown limit against the figure it is enforced on

Command (registry sweep names other files, so for these): `grep -n "MAX_[A-Z]*\b\|_CAP\b\|limit \|(limit\|max " <files>`, kept where the value reaches text: **9 sites**.
Widened by hand-reading every user-facing string that names a span or a count.
That added 3 sites: the window button titles, the zoom chip, and the history divider count.

| Site | Verdict |
|---|---|
| `can.js:318` `(limit 256)` vs `canRows.size` | complies (paused: the snapshot of the same set) |
| `terminal.js:545` `pattern too long (max 200 chars)` | **violates**: counted in UTF-16 code units, while the daemon it mirrors counts code points (`server.py:2735`). A 150-emoji pattern was refused. Fixed. |
| `terminal.js:424` `SLOW_MSG` "over 250 ms" vs `regexBudget` per episode | complies |
| `terminal.js:632` `n / 5` panes vs `panes.length`, `:631` | complies |
| `digital.js:295` `(limit 64 reached)` vs `digitalLanes.size` | complies |
| `plots.js:819` `(limit 64 reached)` vs `plotChannelMeta.size` (analog only, as SPEC 9.2) | complies |
| `plots.js:564`, `digital.js:64`, `can.js:142` console warnings | exempt because the console is not UI; same figures |
| `chrome.js:124` "Show the last 5s" vs `spanFor(timeMode, secs)` | complies (tick base: 5000 ms of MCU clock) |
| `timewindow.js:172` zoom chip vs `z.max - z.min` in the mode's units | complies |
| `pane.js:149` history budget divider `gap: N lines not loaded` | **violates**: N was `rows[0].id - 1`, counting lines at or below the pane's clear point, which it never loads. Fixed (`floor`). Known ceiling: a retention-trimmed capture still overstates, since the oldest id is not known. |
| `pane.js:95-99` hint "no older lines to load" | complies (a spent budget also shows the divider) |

## Class 76: view caches and rebuild keys

Command: `grep -n "Version\b\|View\b\|needsRebuild\|!==.*prev" <files>`: **24 lines**.
20 are the CAN view key (`can.js:29-657`, one site). 4 are false positives: `plots.js:202` DataView, comments `plots.js:1305` and `can.js:682`, `terminal.js:855` scrollIntoView.
Widened to `dirty\|stamp\|cached\|=== last\|!== last\|textContent !==` and by reading every early return on a compare: **19 memo sites**.

| Site | Inputs the build reads | Verdict |
|---|---|---|
| `can.js:311-313` `canView` {version, filter, layout} | row set (version), filter, collapsed set (layout), `canCapWarned` (bumps version with it), counts | complies |
| `can.js:459-494` `updateCanRow` cell memo | ext, rtr, dlc, hex, mask, count, period, age, age class | complies |
| `can.js:686` `canDirty \|\| canLit` timer | frames, lit bytes | complies |
| `can.js:260` `collapsedMem` | refused write only | complies |
| `plots.js:1032` rebuild predicate {uplot, series count, theme} | names, theme; unit (`addSample:594`), shown count (toggle/solo), colour (picker) rebuild explicitly; isInt read live by closures | complies |
| `plots.js:1037` `chart.dirty` for `setData` | window, zoom, paused/frozen, time mode (`terminal.js:781`), samples | complies |
| `plots.js:1260` `lastHoverX` | hovered x, recomputed each tick (time mode and epochs included) | complies |
| `plots.js:453` `multiPort` | ports of charts and lanes; new chart and lane read it at build | complies |
| `plots.js:777`, `:1282` text compares | the text itself | complies |
| `digital.js:463` lane repaint key {dirty, _sizedirty, size} | **shared right edge** and **theme** (grid colour) missing | **violates**: a lane whose stream went quiet never redrew as a sibling stream moved the edge, contrary to SPEC 9.2 "the lanes scroll". A theme toggle never reached a lane with nothing new. Fixed. |
| `digital.js:87,469` `pendingVal` readout cache | newest value; not fed while paused | **violates**: resume showed the pause-time value (a lane born paused showed nothing) beside a waveform that had moved on. Fixed. |
| `digital.js:138` `valText` | the text | complies |
| `digital.js:285` `digitalShown` | collapse state (collapse button keeps it) | complies |
| `terminal.js:79` `tsCol` {mode, ch} | mode, stamp length | complies (grows by design) |
| `terminal.js:618` `setKnownPorts` `same` | alias list; port-tag column crossing 1 re-renders | complies |
| `terminal.js:248-269` `shiftWindow` element reuse by row identity | row, prev row (same object after a trim), time mode, regex, tag column, estimates (earlier-only anchors) | complies: every other input change takes the rebuild path |
| `terminal.js:180` `viewH` | pane height | exempt because the height changes these callers miss (a toolbar wrapping when a pane is added or closed, about one row) sit inside the 8-row overscan (144 px). Reasoned from the arithmetic, not driven. |
| `terminal.js:160,229` hint and empty-state text compares | the text | complies |
| `chrome.js:41` `paletteSlots` | name | complies |

Fixes: `redrawDigital` keys each lane on `drawnEdge` and `theme`, stamped when it paints. `setDigitalPaused(false)` reloads `pendingVal` from each ring's newest vertex.

## Class 77: monotonic fix-ups on clocks and counters

Command: `grep -n "lastTick\|+ 1e-4\|Math.max(.*tick" <files>`: **9 lines** (`plots.js:426,537,538,542,543,545`, `digital.js:102,103`, `timewindow.js:278`).
Widened to `Math.max(\|maxId\|lastTs\|lastHost\|prevTs\|1e-4\|1e-6\|\.sort(\|splice(\|findIndex\|> .*\.ts` and by reading the tick column, the hover, the export edges and every id watermark: **33 sites**.

| Site | Verdict |
|---|---|
| `timewindow.js:266-289` `continueTick` (the landed D-3 continuation) | complies. Re-driven through SK 1-2 besides TC/R. Ceilings as filed: a reset within 100 ms of boot; a lone slow stream whose post-reset tick passes its previous one. |
| `timewindow.js:278` `Math.max(0, host gap)` | complies (a host step back counts 0) |
| `timewindow.js:279-281` sorted epoch insert, cap | complies |
| `timewindow.js:226` `noteTickAnchor` skip needs `ts >= prev.ts` | complies |
| `timewindow.js:298-299` `estimateTick` wrap (terminal column) | complies: the board's clock wraps |
| `plots.js:1119-1123` `xForRow` tick estimate + `tickOffsetAt(row.ts)` | **violates**, two ways. (1) A boot line read in the same chunk as the first post-reset sample shares its host time and took the new offset on top of an estimate already continued from the old tick: one uptime to the right. (2) An estimate crossing 2^32 was wrapped to a small tick. Fixed: `estimateTickX` takes the anchor's offset, unwrapped. |
| `plots.js:536-540` gap point `+ 1e-4` | complies |
| `plots.js:543` tick nudge after `continueTick` | complies |
| `plots.js:542` host nudge | **owner should pick** (O1): host receive time is the daemon's `time.time()` |
| `digital.js:74` max drawn tick within one sample | complies |
| `digital.js:76` null vertex at `prev.x` | complies |
| `digital.js:90` capped-sample fallback | complies |
| `digital.js:93` `digitalLast.host` max | **owner should pick** (O1) |
| `digital.js:94` `digitalLast.tick` max | complies across a reset (continued). Across two ports it is not a restart; see O3. |
| `digital.js:102` host nudge | **owner should pick** (O1) |
| `digital.js:103` tick nudge | complies |
| `plots.js:1217-1229` `chartShownWindow` tick edges | complies (continued ticks) |
| `digital.js:372-398` `digitalShownWindow`, `hostAtTick` | complies |
| `terminal.js:567-574` `exportPane` min/max ts and min id | complies (min/max, not first/last) |
| `can.js:617-622` `canShownWindow` | complies (min over rows; `id_to` bounds the top) |
| `exportrange.js:65` `SHOWN_EDGE_S` | complies |
| `terminal.js:518` `oldestServedId` min | complies |
| `plots.js:267,411-418` `seedMaxId` id watermark | complies: `clearAllCharts` clears it and the capture reset calls that |
| `plots.js:1195` `frozenMaxId` | complies |
| `digital.js:125,804` `digitalFrozenId` | complies (`api.js:165` zeroes `maxId` before `clearAllDigital`) |
| `can.js:541,658` `canFrozenId` | complies |
| `terminal.js:301,687,814` `frozenId`, `clearId` | complies (`api.js:173` zeroes them on a capture reset) |
| `can.js:152` CAN period `dt >= 0` | complies (a step back is not measured) |
| `can.js:121` age anchor forward-only | **owner should pick** (O1) |
| `can.js:117` `noteDaemonNow` forward-only | **owner should pick** (O1) |
| `can.js:133-137` eviction by least `lastTs` | **owner should pick** (O1) |
| `terminal.js:40-48` tick column: raw tick, `anchorTick` zero | open owner question from `rulings-webui-panes.md` (O2) |
| `timewindow.js:184-186` `fmtDelta` | complies (signed) |

CAN periodicity and age use host `ts` only; no device timestamp feeds them.

## Revert-verification

Script `~/tt-data/sweep-webui-panes/revert.py` (and the second batch in `revert2.out`).
Each mutant was applied to a copy-backed file and its test file run; the file was then restored from the copy, md5 checked.
The first batch was re-run after the last `plots.js` edit.

| Mutant | Result | Killed by |
|---|---|---|
| M1 edge not in the lane key | killed | SD 1 |
| M2 theme not in the lane key | killed | SD 2 |
| M3 edge stamp not stored | killed | SD 2 (idle tick repaints) |
| M4 theme stamp not stored | killed | SD 2 |
| M5 resume keeps the stale readout | killed | SD 3, SD 4 |
| M6 blur refocuses the title | killed | SP 1 |
| M7 Enter does not refocus | killed | SP 2 |
| M8 Escape does not refocus | killed | SP 2 |
| M9 pattern length in code units | killed | ST 1 |
| M10 terminal passes no floor | killed | ST 2 |
| M11 plan ignores the floor | killed | ST 2 |
| M12 drawn estimate offset at the row's host time | killed | SK 1 |
| M13 drawn estimate wrapped | killed | SK 2 |
| M14 column estimate unwrapped | killed | SK 3 |

Before each fix, each test failed on the value it names: SD 1 no label drawn, SD 3 `IDLE`, SD 4 empty, SP 1 one focus call, ST 1 refused, ST 2 `gap: 5800`, SK 1 `IDLE`, SK 2 `RUN`.

## Docs

- CHANGELOG `[Unreleased]` Fixed: three lines (quiet lanes, theme and resume readout; rename focus; pattern cap and divider count).
- The hover fix completes the unreleased D-3 line, which already promises it.
- SPEC unchanged: 9.2 already states the corrected behaviour ("the lanes scroll while the signal is constant"; "a hovered terminal line take[s] the same offset").

## Hand-offs (other agent's files)

- H1 `exportdlg.js`: a dialog left open across a capture reset still sends the old capture's `id_to` and shown window after its session-list await. A capture-generation check there would drop or refresh them (class 73).
- `api.js`: nothing new. The seed generation check and `resetHistory` on reset were verified.

## Broken tests

None. `settings_ports_bound.test.mjs` failed once mid-sweep while `settings.js` was being edited by the other batch; it passed on the final run.

## Manual checks (browser)

- [ ] Two streams on one port, one stopped: its lanes keep scrolling with the other's.
- [ ] Pause, toggle the theme: lane gridlines recolour.
- [ ] Pause, let an enum change, stop the stream, resume: the gutter reads the new label.
- [ ] Rename a chart, click the command input without pressing Enter: typing lands in the command input (Chromium and Firefox).
- [ ] 64 lanes live: CPU is acceptable now that every lane repaints each tick while the edge moves.
- [ ] Tick base, board reset: hover the boot banner line; the cursor sits at the first post-reset sample.

## Owner decisions

- **O1, a daemon wall-clock step back** (row `ts` is `time.time()`: NTP step, manual change).
  - Now:
    - Host-base charts and lanes nudge every later sample 0.1 ms past the pre-step edge, so the trace glues and the live edge stalls until clear-all.
    - The CAN age anchor ignores older readings, so every age includes the step and periodic ids read dead until reload or a capture reset.
    - CAN eviction by least `lastTs` drops the live ids first.
  - Options:
    - (a) Keep, and document beside A-12's inexact lower bound (SPEC 9.1/9.2).
    - (b) Treat a host step back beyond a slack as a restart. Charts and lanes break and continue by locally measured elapsed time. CAN re-anchors from a `/status` `now` behind its anchor by more than the status deadline, and evicts by arrival order.
- **O2 (still open from the rulings round)**: the terminal tick column shows the raw tick after a reset, so it reads lower than the chart cursor tag. Change it, or keep it as the board's own clock?
- **O3, tick base with two boards**: the lanes share one right edge, the largest drawn tick across ports.
  - The lower-uptime board's lanes sit off screen; charts are unaffected (each has its own edge).
  - Options: (a) keep and document; (b) a per-port lane edge; (c) under the tick base show lanes of one port at a time.

## The two questions

1. **Least confident, rechecked.**
   - The class 77 continuation was filed closed by mutation. I re-drove the hover through a lane whose values sit far apart, so the cursor's snap cannot hide a wrong x, and that found both `xForRow` defects (SK 1-2).
   - The lane-key fix could re-clobber a cursor readout on every tick. Rechecked: `redrawTick` re-applies the cursor whenever a lane drew, and `digital_repaint.test.mjs` stays green.
   - Still reasoned rather than driven: the `viewH` exemption (overscan arithmetic), the blur-refocus cancellation (Chromium focus-change semantics; manual check listed), and the repaint cost of 64 lanes.
2. **Not yet checked.**
   - A tick-carrying line that is not a plot sample (`!m @tick`, `!can`), read after a reset but before the first post-reset plot sample, hovers at its raw tick: epochs open only from chart and lane samples. Bounded to one plot period; ceiling, not fixed.
   - Two boards under the tick base (O3).
   - A daemon wall-clock step back across host mode and the CAN table (O1).
   - The export dialog across a capture reset (H1).
