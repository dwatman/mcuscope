# Fix batch: web UI panes

Test files (new): `prerelease_webui-panes_terminal.test.mjs` (T), `_can.test.mjs` (C), `_plots.test.mjs` (P), `_timewindow.test.mjs` (W), all under `host/tests/webui_js/`.
Existing owned tests edited: `can_bytediff` (pinned the D-7 defect), `can_logic` (pinned the old pattern prefix), `export_paused_window` (pinned `last_ms`; E-3 test added), `digital_zoom` (vacuous live-panel test made non-vacuous).

Revert-verify: `~/tt-data/prerelease-2026-09-15/fix-webui-panes/revert.py` re-introduced each defect in a copy-backed product file, ran the tests in a mirror with the exportdlg.js hand-off applied, restored from the copy (md5 checked after); raw output in `revert.out`.
45 mutants: 44 killed, 1 survived (F-24, equivalent, below).
Mirror run with the exportdlg.js hand-off, owned plus exportdlg and exportrange tests: 355 pass, 1 fail (exportrange.test.mjs, listed under "Not done").

## Fixed

| Finding | Change | Test | Revert-verify |
|---|---|---|---|
| D-1 | `pane.historyGen` (pane.js), bumped in `resetHistory`, which clear, clear-all and rebuild (so resume and filter changes) call; `loadHistoryPage` returns before any write when it moved | T: clear, resume, filter change, clear-all, resetHistory mid-flight; control page lands | gen check removed: 5 fail; bump removed: 5 fail |
| D-2 | `exportrange.params` takes `shown: {fromTs, toTs}` and sends `since_ts = fromTs - 1e-6` (exclusive bound) and `until_ts`, no `last_ms`; pane: min/max row ts; chart: frozen snapshot's last host x minus `chart.window`; lanes: frozen edge minus panel window; CAN: oldest row's last frame to `canFrozenNow`. Zoom ignored (owner decision kept) | W params; P chart (stream stopped, later marker) and lanes; C table; `export_paused_window` chart and pane | edge epsilon, last_ms form, pane edge, chart live-array, chart span units, lanes live edge, CAN until: all killed |
| D-4 | `freezeChanged()` from `ensureChart` (born live), `clearAllCharts`, `addDigitalLane`, `clearAllDigital`, `canIngest` first row, `clearAllCan` | P label test; C label test | each of the 6 calls removed: killed |
| D-5 | `noteDaemonNow(ts)` exported from can.js: finite only, forward only against the extrapolated clock; rows still anchor when no reading comes | C: silent board ages dead; missing/NaN reading before any row; stale reading does not pull back | body removed, finite check removed, forward-only removed: all killed |
| D-6 | `canFilterPattern`: `!can1?` for bus 1, `\s+` separators, flags `(?:-|r+)` vs `[xr]*x[xr]*`, `[aA]` class per hex letter (no inline flags: JS and `regex` differ) | C: 20-line corpus, every row's pattern selects exactly the lines the real decoder files under it | can1, whitespace, flags, case: each killed |
| D-7 | `e.base` holds the last data payload; an RTR neither diffs nor resets `moved` | C: RTR-polled id, requester/responder, length change after RTR; `can_bytediff` updated | skip removed, diff against `e.hex`: killed |
| D-8 | `canLayoutVersion` bumped by collapse, part of the view key | C: collapse and expand while paused | key clause removed: killed |
| D-10 | `buildUplot` when a unit changes on a chart with one shown trace | P: axis label mV then V | removed: killed |
| D-11 (title) | marker and gap dividers set `title` to their text | T | removed: killed |
| D-11 (span, orchestrator hand-off) | the divider text sits in `<span class="divider-text">` inside `.divider`, for the chrome batch's committed ellipsis rule; title kept | T: children are exactly one `.divider-text` holding the text | text written on the divider directly: killed |
| D-12 | `paneCfgFromStorage` (pane.js) type-checks port, channels (filtered to `ALL_CHANS`), regex; `loadState` maps through it | T: bad, mixed and null entries, and what is persisted back | map, port, channels, regex: each killed |
| E-3 sibling | export `match` gated on `pane.regex` | `export_paused_window`: invalid and over-long pattern send no `match` | regexSrc gate: killed |
| E-13 comment | `exportrange.js` defaultRange comment: no session param is the whole capture; the dialog preselects the open run | none (comment) | n/a |
| F-13 (J15) | test only | C export | killed |
| F-14 (J16) | test only (now `since_ts`) | C export | killed |
| F-15 (J12) | test only | C frozen ages across a repaint after the anchor moved | killed |
| F-16 (J22) | test only, through the period column (deterministic, no clock) | C | killed |
| F-19 (J40), F-20 (J41) | test only | W | killed |
| F-21 (J44), F-22 (J50) | test only; F-22 uses the capture-reset state (frozenId 0, no snapshot, paused) | T | killed |
| F-23 (J55) | test only, reachable path: drag zoom, CAN resumed by hand ends the latch, a new chart is born live under the zoom | P | killed |
| F-25 (J60) | test only: terminal hover on a tickless line in tick mode moves the lane readout to the estimate | P | killed |
| F-26 (J65) | test only: swatch commit recolours the other port's lane of that name, not another name | P | killed |
| F-24 (J62) | no change; `digital_zoom` live-panel test now asserts the zoom stands going in and is gone after resume | `digital_zoom` | SURVIVED: equivalent. `digitalPaused` goes false only in `setDigitalPaused`, which leaves the zoom first; `setZoom(z)` is only in `onSelect`, followed by `pauseAll(true)`. The guard stays as belt and braces |

Behaviour change to note in docs: a paused pane holding a single row now offers the shown window (a zero span used to mean none).

## Manual verify

- D-8: two CAN groups, pause, click a divider: rows collapse and the caret flips; click again restores.
- D-10: a board redefining `!pd 0 v:u2:mV` to `:V` on a solo'd trace: the y axis label changes with the chip.
- D-11: hover a 240-character marker row: the tooltip shows the whole marker (the ellipsis is the chrome batch's CSS).
- D-2 (after the exportdlg.js hand-off): pause a chart on a stopped `sim` stream after a later marker, export "shown window": the CSV holds the shown samples. The URL form was driven against a real daemon on a copy of leg D's capture (port 18921): chart edges 100 rows (old `last_ms` form 0); pane edges 4 of 4 sys rows, 3 without the 1e-6 edge.
- D-5 (after the statusbar.js hand-off): reload onto a board silent for minutes: CAN ages read the silence within one status poll.

## Not done / owed

- `host/mcuscope/webui/exportdlg.js` (chrome batch), required for D-2, else the shown mode is never offered:
  - `:128` `const ok = ctx.watermark != null && ctx.shown != null;`
  - `:249` `params(effective, { watermark: ctx.watermark, shown: ctx.shown })`
  - `:19` comment `{kind, watermark, shown, options, build}`
- `host/mcuscope/webui/statusbar.js` (chrome batch), D-5: in `pollStatus` after `setKnownPorts(...)`, `noteDaemonNow(s.now);` with `import { noteDaemonNow } from "./can.js";`. The daemon batch's `/status` already carries `"now": time.time()` (server.py:907).
- `host/mcuscope/webui/api.js` (unowned), D-1 capture reset: in `resetForDbReset`'s pane loop add `resetHistory(p);` (export added to terminal.js; add it to the `./terminal.js` import). Without it a page in flight across a reset still lands, and `historyNext` keeps an old-capture id.
- `host/tests/webui_js/exportrange.test.mjs`: "shown mode is the panel's own window" and the two loops passing `shownLastMs: 5000` move to `shown: { fromTs, toTs }`; assert `since_ts` = `String(fromTs - 1e-6)`, `until_ts`, no `last_ms`, and no bound for `shown: null`.
- `host/tests/webui_js/exportdlg.test.mjs`: DONE by this batch (the orchestrator allowed it).
  - "the shown window survives a ring trim" now asserts `until_ts` at the frozen edge, `since_ts` one window plus 1e-6 below it, and no `last_ms`.
  - Its session half and "a remembered shown range" now assert neither `last_ms` nor `since_ts`.
  - Correction to the hand-off: tests 1, 11 and 12 fail in-tree because `exportdlg.js` still reads `ctx.shownLastMs`, so no panel is ever offered the shown mode. Test 1 needs no test change at all. All three pass only once the `exportdlg.js` lines above land (verified in the mirror: exportdlg.test.mjs 22 of 22).
- `host/tests/webui_js/prerelease_chrome_export.test.mjs:39`: `shownLastMs: null` to `shown: null` (cosmetic, passes either way).
- Until the exportdlg.js hand-off lands, in-tree red: 5 owned tests (`export_paused_window` chart and pane, C export, P chart and lanes export), the 3 exportdlg.test.mjs shown-mode tests, and exportrange.test.mjs "shown mode is the panel's own window". With the hand-off applied (mirror): only that exportrange test stays red.
- Docs batch:
  - SPEC 9.1 export dialog (`:1571`): "The panel's shown window (`last_ms`)" becomes the drawn window's host-time edges as `since_ts`/`until_ts`.
  - SPEC 9.1 CAN (`:1537`): add that a data frame after a remote frame is diffed against the last data frame.
  - SPEC 9.1 CAN (`:1545`): the id-click pattern takes either id case, `!can1`, and whitespace runs, and tells standard from extended.
  - CHANGELOG: D-1, D-2, D-4 to D-8, D-10 to D-12, E-3 sibling.
- `style.css` D-11 divider ellipsis: landed by the chrome batch; terminal.js now emits the `.divider-text` span it targets.

## The two questions

1. Least confident, rechecked:
   - The exclusive `since_ts` edge and float formatting between JS `String` and the daemon's parse: re-driven over HTTP (above), the edge is needed and sufficient.
   - D-6 in the daemon's engine (history paging and export send the pattern as `match`): 5 patterns against 15 lines under Python `regex`, 0 disagreements with JS.
   - D-5's field: read from server.py and from a live `/status`; epoch seconds, float.
   - Not rechecked: every hand-off above is unapplied, so D-2 and D-5 do nothing in the product until they land; the visual items need a browser.
2. What we should have checked:
   - Working outward from D-1: the capture reset never called `resetHistory` at all, so besides the in-flight page it left `historyNext` stale (owed to api.js above). The plot history seed against clear-all (leg D's open item) is api.js and was not examined.
   - Working outward from D-7: a repaint between the RTR and the next data frame consumes `moved` while the cell shows "remote"; that follows SPEC's per-repaint rule and was left.
   - F-24's mutant is equivalent; the same reasoning should be applied before writing a test for any other zoom guard.
