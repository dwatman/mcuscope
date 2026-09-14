# Fix-diff review: web UI (fdd30a2..HEAD, commits 586f445 and 545e990)

Scope: `host/mcuscope/webui`, `host/tests/webui_js`, `host/tests/test_webui*.py`.
Drives: `~/tt-data/prerelease-2026-09-15/fixdiff-js/` (`a_export_stale`, `b_can_shown_filter`, `c_chart_tick_shown`, `e_settings`), importing the real modules and `dom_stub.mjs`.
In-tree JS suite at HEAD: 658 tests, 658 pass.

Counts: HIGH 0, MED 1, LOW 8.

## Findings

### FW-1 MED: a pending Export outlives its dialog, runs against the next one, and holds its button disabled
- Where: `exportdlg.js:227-236` (`doExport`, the `sessionsReady` loop in `exportNow`), `exportdlg.js:214` (the `/sessions` fill has no deadline).
- Defect: `exportNow` reads the module-level `ctx`, `values` and `renderMode` after its await. Cancel closes the dialog but not the pending call, and `expGo.disabled` set by `doExport` is never reset on open. The E-5 loop now also waits for the next dialog's own fill, so the continuation always lands on the newest dialog.
- Scenario: a slow or stalled daemon; press Export while the session list is loading, give up with Cancel, open a CAN or chart export. That dialog's Export is greyed out; when `/sessions` answers it downloads the second panel's export with its defaults and closes the dialog, with no click on it. Against a daemon that never answers `/sessions`, every later export dialog has Export disabled until reload (the E-6 deadline was added to Settings and Attach only).
- DRIVEN: `a_export_stale.test.mjs`: `B open, before any release: expGo.disabled = true`, `built after release: ["B"] dialog open: false`.
- Class 73 (the dialog's context replaced while the export was in flight); E-6 sibling for the missing deadline.
- Fix: in `doExport` capture `const mine = ctx` and return from `exportNow` after the await when `ctx !== mine` or the dialog is closed; reset `expGo.disabled` in `openExportDialog`; pass `AbortSignal.timeout(STATUS_TIMEOUT_MS)` to the `/sessions` fill.

### FW-2 LOW: in tick mode the shown window's host edges are not the samples the chart and lanes draw
- Where: `plots.js:1206-1211` (`chartShownWindow`), `digital.js:363-366` (`digitalShownWindow`).
- Defect: both take the host array's last value minus `window` seconds. In tick mode the chart draws `[lastTick - window*1000, lastTick]` of `xsTick`, which is a different sample set whenever the MCU clock and host time disagree in rate or latency. SPEC 9.1 says "the host-time edges it draws".
- Scenario: an STM32 on HSI running 1 percent slow, 100 Hz stream, 30 s window, tick mode, paused: the chart draws 3001 samples, `shown` exports 2971; the oldest 0.3 s on screen is missing from the CSV. Host mode is exact (one sample short at the left edge where the burst nudge moved `xsHost`).
- DRIVEN (chart): `c_chart_tick_shown.test.mjs`: `HSI tick: drawn 3001, exported 2971, drawn-not-exported 30`; `HSI host: drawn 2971, exported 2971`. Lanes REASONED from `digitalRightEdge` (tick) against `digitalShownWindow` (host).
- Class 23 (export face) and 77 neighbour (a second clock read through the host one).
- Fix: in tick mode take `fromTs` from `xsHost` at the first index whose `xsTick >= lastTick - span` (chart); for the lanes map the tick edge through the frozen edge's own host/tick pair.

### FW-3 LOW: a filtered paused CAN table's shown window starts at a row the table hides
- Where: `can.js:616-621` (`canShownWindow` reads every `canFrozen` entry; the table and `visibleCanIds` use `shownCanRows()`, `can.js:590`).
- Scenario: one `7DF` request an hour ago, `100` at 10 Hz; filter `100`, pause, export shown: the table draws one row whose last frame is 4604.9, the request is `id=100&since_ts=999.999999&until_ts=4604.90`, an hour of 0x100 frames.
- DRIVEN: `b_can_shown_filter.test.mjs`: `drawn rows: 1 id param: 100 since_ts: 999.999999 until_ts: 4604.901709635999`.
- Class 23 (export face: shown is not what the surface draws).
- Fix: compute `fromTs` over `shownCanRows()` instead of `canFrozen.values()`.

### FW-4 LOW: a pane's shown lower edge is a timestamp, and a serial burst shares one timestamp
- Where: `terminal.js:564-574` with `exportrange.js:425`; daemon `store.py:1559` (`ts > since_ts`); `serial_link.py:575` stamps a whole read burst with one `time.time()`.
- Defect: every row of the burst holding the pane's oldest row has the same `ts`, so `since_ts = fromTs - 1e-6` also selects that burst's earlier rows: rows the pane cleared (`clearId`), trimmed at `VIEW_MAX`, or never held because the ring had rotated.
- Scenario: `clear` mid-burst at 115200 baud, a few lines later pause and export shown: the cleared lines of that burst are in the file.
- REASONED from the three sites above (the client half is exactly what `prerelease_webui-panes_timewindow.test.mjs` pins).
- Class 23 (export face).
- Fix: also send `since_id = oldest shown row id - 1` (`/lines/export` takes `since_id`), which is exact for an id-ordered row set.

### FW-5 LOW: two class 61 siblings of the fixed PlotJuggler dest write
- Where: `settings.js:181-190` (`renderPj` writes `cfgPjDest` and `cfgPjEnabled` after `GET /plotjuggler`, which runs after `showModal`), `settings.js:203` (`applyPj` writes `checked` from the answer unconditionally).
- Scenarios:
  - A slow daemon: open Settings, type a destination while the GET is out; the answer replaces it with `127.0.0.1:9870`.
  - Tick enabled, untick while that PUT is out, answers arrive out of order: the box ends ticked over a stream the last request turned off.
- DRIVEN: `e_settings.test.mjs` tests 1 and 2: `dest after late GET: 127.0.0.1:9870`; `checkbox: true`.
- Class 61.
- Fix: write each control only while it still reads what it read (GET) or sent (PUT), as the dest line at `:204` now does.

### FW-6 LOW: a server save whose re-read fails reads clean and "saved" but never raises the restart badge
- Where: `settings.js:529-533` (`renderSaved`), `settings.js:544` (`saveServer` discards the PUT answer); `server.py:1125-1126` answers `{"ok": true, "restart_required": ...}`.
- Scenario: change the bind port, Save, the daemon stops answering `GET /config`: the section shows `saved; could not re-read the config` and no "restart daemon to apply" badge, though the saved change only takes effect on restart.
- REASONED.
- Class 17 neighbour (the result the PUT reported is dropped in favour of a read that did not happen).
- Fix: `setBadge` from the PUT answer's `restart_required` (Server and Storage) before `renderSaved`.

### FW-7 LOW: F-8's mechanical id scan dropped five ids the hand-kept list covered
- Where: `host/tests/test_webui.py:168-178` (`_resolved_ids` matches only literal `$("...")`); `settings.js:77,675` resolve `cfgSecServer`, `cfgSecStorage`, `cfgSecUpdate`, `cfgSecToken`, `cfgSecPorts` through `$(s.sec)`.
- Defect: the old `DIALOG_IDS` named those five; the scan cannot see them, and neither can the DOM stub. A typo in `SECTIONS[].sec` throws in `initSettings` (`null.addEventListener`) and no test fails. `DAEMON_CONTROLS` (`settings.js:93`) and `can.js:297` are the same shape, covered today only because each id also appears literally elsewhere.
- DRIVEN: the old list diffed against the new scan in Python prints `['cfgSecPorts', 'cfgSecServer', 'cfgSecStorage', 'cfgSecToken', 'cfgSecUpdate']`.
- Class 75.
- Fix: also collect the string values of `sec:`/`save:` keys and of the id arrays fed to `$(id)`, or keep those tables in a short explicit list checked beside the scan.

### FW-8 LOW: the plot history seed, D-1's sibling, still lands on charts a clear-all or capture reset emptied
- Where: `api.js:338-370` (`seedPlotHistory` checks `gen !== wsGen`; neither `clearAllCharts` nor `resetForDbReset` moves `wsGen`), `plots.js:405` (`seedTargetHasData` passes on an empty chart).
- Scenario: a capture reset (or clear-all) while the page-load seed's `/plot/series` requests are out: the old capture's history is plotted on the emptied charts, and the reset's own re-seed then skips each chart because it now has data.
- REASONED (leg D listed it as open; the D-1 fix covered pane history only).
- Class 73.
- Fix: a seed generation bumped by `clearAllCharts`/`clearAllDigital`, taken at the start of `seedPlotHistory` and checked before `plotSeed`.

### FW-9 LOW: E-9 moves the session `.db` export from a streamed download to a whole-file blob
- Where: `state.js:310-313` (`streamable` now excludes `/sessions/`), `state.js:337-346` (`r.blob()` then save).
- Defect: the comment calls the file bounded, but with the default `max_db_bytes = 0` (`config.py:72`) a session's `.db` has no size bound. A long run is read whole into browser memory, with no progress shown, before the save prompt appears; before the fix it streamed to disk.
- REASONED.
- Class 55 neighbour (keyed on the path, where the behaviour depends on size).
- Fix: keep navigation for `/sessions/{id}/export` behind a `HEAD` or small-range preflight that surfaces a 4xx, or fold this path into the owner's E-3 decision.

## Manual verify

- [ ] FW-1: with the daemon `kill -STOP`ped, open a pane export, press Export, Cancel, open a chart export: its Export button state, and what downloads after `kill -CONT`.
- [ ] E-6: the attach dialog's "no reply from daemon" wording in Firefox and Safari; a browser whose fetch rejects a timed-out signal as `AbortError` shows the raw message instead.
- [ ] E-11: `#cmdResult` is `hidden` until it gets text; confirm a screen reader announces a result appearing in a region that was hidden a moment before.
- [ ] D-11: a 240-character marker in a narrow pane ends in an ellipsis with the dashed rules gone first, and the row stays 18 px tall.
- [ ] E-12: under 860 px, collapse and expand are gone; a sidebar collapsed before narrowing still shows in the stacked layout.
- [ ] E-7: an open port dropdown during an offline-to-online transition (the select is rebuilt twice, once per transition).
- [ ] D-10: a solo'd trace redefined `mV` to `V` while its chart is paused: the axis label follows the chip (both now read the live definition over frozen data).

## The two questions

1. Least confident, rechecked:
   - FW-2's size. It depends on the clock-rate or latency mismatch, so on most boards it is a few edge samples; the drive uses a 1 percent HSI error, which is inside STM32 HSI tolerance, and host mode was confirmed exact on the same data.
   - D-5 (`noteDaemonNow`). Rechecked against pause (`canNow` returns `canFrozenNow`, untouched by the anchor), reset (`clearAllCan` nulls the anchor, the next poll re-sets it from the daemon's clock), a WS backfill after a stream outage (a backfilled row can move the anchor back by at most its own delivery delay, and the next poll restores it) and a daemon restart (same host clock). No finding. The web UI is served by the daemon it talks to, so "an older daemon without `now`" only arises for a page left open across an upgrade.
   - E-1 against a below-floor hand edit: `load_config` normalises it to 0 before `GET /config`, so the unchanged-bytes round-trip cannot send a value the PUT floor refuses.
2. What should have been checked:
   - For the E-5 and D-1 generation tokens: the export dialog as a replaced view (FW-1) and the plot seed (FW-8), not only the fills and pages named in the findings.
   - For D-2: each surface in each time mode and under each of its own view filters (FW-2, FW-3), and the daemon's burst timestamping against a ts-only lower edge (FW-4).
   - For F-8: diffing the new scan's coverage against the list it replaced (FW-7).
