# Fix batch: web UI fix-diff findings (FW-1 to FW-9)

Files changed: `webui/exportdlg.js`, `exportrange.js`, `plots.js`, `digital.js`, `can.js`, `terminal.js`, `settings.js`, `state.js`, `statusbar.js`, `api.js`; `tests/test_webui.py`; `tests/webui_js/prerelease_chrome_export.test.mjs`; SPEC 9.1 and 9.2.
New test file: `tests/webui_js/prerelease_fixdiff_webui.test.mjs` (18 tests).

Revert verification: `~/tt-data/prerelease-2026-09-15/fix-fixdiff-webui/revert.py` undoes one changed branch at a time (every anchor checked before any write; copy, mutate, run, restore from the copy, `cmp`).
Raw results in `revert.out` and `revert_results.json` there.
32 mutants: 31 killed, 1 equivalent (FW-4, below).

Gates:

- `cd host/tests/webui_js && node --test`: 675 tests, 675 pass (658 before, less the retired E-9 test, plus 18 new).
- `uv run python -m pytest tests/test_webui.py`: 12 passed. `ruff check tests/test_webui.py`: clean.

## Fixed

- **FW-1** (a pending Export outlives its dialog).
  - Change: `dialogGen`, bumped by `closeExport` (Cancel, the x and Escape all go through it). `exportNow(gen)` returns after the session-list await and after the download await when it moved.
  - `openExportDialog` re-enables `expGo`. `doExport` re-enables only while `ctx` is still its own, so an ended Export cannot release the next dialog's pending button.
  - The `/sessions` fill passes `AbortSignal.timeout(STATUS_TIMEOUT_MS)`. On failure the select offers `whole capture` and `expErr` reads `could not list sessions: no reply from daemon` (or the error message).
  - `STATUS_TIMEOUT_MS` moved to `state.js` (statusbar.js imports and still re-exports it), so exportdlg.js does not pull in statusbar.js and its import graph.
  - Tests: each of the three closes, then the next panel's dialog, with both lists held: B's Export starts enabled, A's list answering builds nothing, leaves B open and B's button held; B then exports once. Cancel with no next dialog builds nothing. A refused download answering after Cancel writes nothing into B, leaves B open and B's pending button disabled. A list that never answers: nothing before the deadline, one 4000 ms deadline, then whole capture, the reason, Export usable.
  - Revert: list gen check 4 fail, download gen check 1, close bump 5, open re-enable 10, ctx-owned finally 1, deadline 6, reason 1, wording 1.
- **FW-2** (tick mode's shown window).
  - Change, chart: under tick, `fromTs` is `xsHost` at the first index whose `xsTick >= lastTick - span`. Host and rel modes are unchanged.
  - Change, lanes: a lane stores transitions only, so no per-sample host time exists at the left edge. Under tick it is interpolated between the lanes' nearest vertices either side of the edge (the right one may be the frozen edge itself). With no vertex before the edge, the first vertex is the first sample drawn.
  - Tests: the finding's HSI case (1 percent slow, 100 Hz, 30 s): the chart's export selects exactly the 3001 drawn samples. Lanes over the same clock with two 1 s latency steps placed so only the tight vertices bracket the edge: the export selects exactly the samples inside the tick window. Lanes shorter than the window start at their first sample.
  - Revert: chart branch 1, lanes branch 2, interpolation 1, no-vertex-before 1, nearest after 1, nearest before 1.
- **FW-3** (CAN window under the id filter).
  - Change: `canShownWindow` takes `lastTs` over `shownCanRows()`.
  - Test: the finding's 7DF-an-hour-ago case: `since_ts` is the shown 0x100 row's last frame less 1e-6. Revert: 1 fails.
- **FW-4** (burst-shared edge timestamp).
  - Change: `exportPane` adds `sinceId` (oldest held row's id minus 1) to `shown`. `exportrange.params` sends it as `since_id` in shown mode only, so it stays the single builder. `/lines/export` declares `since_id` (SPEC 3.4, server.py) and the store treats it as exclusive.
  - Test: rows 40, a gap row, 41 and 45 sharing a ts: `since_id=39`, `since_ts`/`until_ts` unchanged, no guard-double refusal; clock mode on the same pane sends no `since_id`.
  - Revert: params line 1 fails, pane field 1. EQUIVALENT: "min id" against "first row's id" (0 fail), since pane rows are in id order.
- **FW-5** (class 61 in the PlotJuggler section).
  - Change: `renderPj` records both controls before the GET and writes each only while it still reads that. `applyPj` writes `checked` only while it still reads what was sent.
  - Also the refusal path's re-sync GET, a third site of the same shape: it writes `checked` only while unchanged.
  - Tests: a dest typed and the box ticked during a held GET both survive; an untouched open shows the daemon's state. Two PUTs answered out of order leave the box unticked. A refused PUT's re-sync sets an untouched box and leaves one changed while the GET is out.
  - Revert: GET enabled 1, GET dest 1, PUT enabled 1, refusal re-sync 1.
- **FW-6** (restart badge after a failed re-read).
  - Change: `renderSaved(s, render, err, answer)` raises the badge from `answer.restart_required` when the re-read fails. Server and Storage pass their PUT answer; Updates and Ports always answer `false` (server.py), so they pass none.
  - Tests, Server (port) and Storage (db path): with `GET /config` failing, `restart_required: true` raises the badge and `false` leaves it hidden, both with the `saved; could not re-read the config` note.
  - Revert: the setBadge line 2 fail, server call 1, storage call 1.
- **FW-7** (id scan lost five ids).
  - Change: `_ids_in(text)` also collects `sec:`/`save:` string values, ids in an array (inline or named) iterated into `$(id)`, and the values of a `$({...}[key])` table.
  - Test: `test_the_id_scan_follows_ids_reached_through_a_variable` drives each form on a synthetic snippet, and asserts `cfgSecServer`, `cfgSecStorage`, `cfgSecUpdate`, `cfgSecToken`, `cfgSecPorts` are in the real scan.
  - Revert: each of the four collectors fails the test (1 each).
- **FW-8** (plot seed after clear-all).
  - Change: `plots.js` `seedGen`, bumped by `clearAllCharts` (terminal clear-all and the capture reset both call it), read as `plotSeedGen()`. `seedPlotHistory` takes it at the start and drops the page when it moved.
  - Tests: a clear-all while the page-load `/plot/series` is held leaves the charts empty. A capture token arriving during the held seed does not reset until it lands, and the reset's own seed then plots the new capture.
  - Revert: the check 1 fails, the bump 1.
- **FW-9** (session `.db` export buffered whole).
  - Change: `streamable` no longer excludes `/sessions/`; the bundle stays a fetch as before E-9.
  - Test: the F-18 navigation test now includes `/sessions/2/export` (no fetch, anchor carries the path). The E-9 test pinned the reverted behaviour and is removed.
  - Revert: restoring the exclusion fails 1.

SPEC: 9.1 export dialog (list deadline, tick edges, pane `since_id`, close ends a pending Export, CAN filter), Settings (badge from the save), 9.2 seeding (dropped after clear-all).

## Manual verify (browser only)

- [ ] FW-1: `kill -STOP` the daemon; pane export, Export, Cancel, open a chart export. Its Export is enabled; about 4 s later the list shows `whole capture` and `could not list sessions: no reply from daemon`. After `kill -CONT` nothing downloads from the cancelled dialog.
- [ ] FW-1: Firefox and Safari report the list timeout as `TimeoutError` (a browser raising `AbortError` shows the raw message, as E-6).
- [ ] FW-9: with no token, Settings > sessions > export on a large run shows the browser's own download progress at once.
- [ ] FW-2: a real board under the tick base, chart and lanes paused, shown-window CSV: first and last rows match the drawn left and right edges.
- [ ] FW-4: `clear` a pane mid-burst at 115200, pause, export shown: none of the cleared lines are in the file.

## Not done / owed

- FW-9: how a navigated session `.db` download reports a refusal (a deleted session saves the error body as `.db`) stays the owner's decision, with E-3.
- FW-2, host mode: `addSample` nudges colliding host times up by 1e-4 s, so a chart's `since_ts` can sit just above the true ts of its first sample (the finding's "one sample short"). Not ruled on; the chart keeps no per-sample id to fix it with.
- FW-2, lanes from several ports under the tick base: `digitalLast`/`digitalFrozen` take per-field maxima across ports, and the bracket search reads every lane's ticks, so two boards' clocks mix. That is the drawing's existing behaviour, not something this fix introduced.
- CHANGELOG [Unreleased], Fixed: FW-1 to FW-8 one line each; FW-9 as the E-9 revert.
- REVIEW.md: FW-4 as a class 23 sweep item ("a ts lower edge where the daemon stamps a burst with one ts: add the id"); FW-7 as class 75's sweep ("diff the new scan against the list it replaced").

## The two questions

1. Least confident, rechecked:
   - FW-8's capture-reset half. The finding assumed a reset can land while the seed is out. It cannot on one socket: `onmessage` stages every row, capture tokens included, until `runBackfill` (seed included) resolves, so the reset runs after the seed has landed. A test pins that ordering. The generation still moves on a reset, since the reset calls `clearAllCharts`.
   - FW-2 lanes: interpolation is exact only while host time is linear in tick between the two bracketing vertices. A lane that toggles rarely near the edge gives a wide bracket, and latency jitter inside it moves the edge by up to that jitter. The test places steps outside the tight bracket and inside the loose ones to show the nearest pair is used.
   - FW-1's ownership rule: `ctx` identity assumes each panel passes a fresh options object per open. All four callers build one inline.
2. What should have been checked:
   - A browser for every Manual verify item.
   - Whether `/plot/export` and `/can/frames` exports hit FW-4's burst edge as well. Charts keep no row ids, and the CAN table's rows are latest-per-id, so `since_id` does not map onto either. They were left on `since_ts`.
