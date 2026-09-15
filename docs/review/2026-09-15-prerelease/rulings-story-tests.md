# Owner rulings: self-contained web UI tests, and the seed window

## Method

- Every test in `host/tests/webui_js/*.test.mjs` (96 files, 731 tests) was run alone with an anchored `--test-name-pattern`, and each file in order.
- An import hook logged each assert's arguments per test; tests whose asserts saw different or fewer values alone were read for vacuous passes.
- Drivers and before/after runs: `~/tt-data/story-tests/`.

## Findings

- 99 tests in 35 files failed alone.
- Others passed alone only vacuously, or were setup steps written as tests: `plots_finite` (rejections for a stream never defined), `digital_zoom` (`notEqual` against NaN), `plots_zoom`, `plots_theme_rebuild` (`deepEqual([], [])`), `statusbar_logic`, `api_pane_queue`, `settings_dirty`, `plots_export_button`, `statusbar_proto`, `statusbar_session_dialog`, and two assert-less "open the dialog" tests.
- Reverse case: `rulings_panes_window` "a shift-click on the lanes' selector is the same group state" passed its first assert in order without the lanes click, because the previous test had set the group span.

## Fixes

- Each dependent test builds its own state through a per-file setup helper; the full table (file, test, depended on, fix) is below.
- Removed: the two "open the dialog" tests (`settings_sessions_bundle`, `settings_storage_cap`); their setup is `openFresh()`.
- Replaced: `api_db_reset_misfire` fetch-count and buffer asserts are relative to the test's start; `export_paused_window`'s "still paused from the previous test" became the setup helper's own asserts.
- None needed a web UI module restructured.
- The settings doubles (`settings_*`, `prerelease_chrome_settings`, `statusbar_attach_eol`) still model an older daemon without `revision` or `config_warnings`; no test there is about those fields.

## Seed window follows the group span

- `api.js` `seedLastMs` adds `groupWindow() * 1000` instead of `PLOT_WINDOW_DEFAULT * 1000`, so the re-seed after a capture reset covers the span the rebuilt charts show.
- Test: `api_seed_group_window.test.mjs` (own file: the group span is module state). Page-load seed `last_ms` 40000 (30 s plus 10 s idle); after shift-click 5 min and a capture reset, 310000, and the rebuilt chart shows 300 s.
- Revert: both lines reverted, the test failed `expected: 310000, actual: 40000`; restored from the copy.

## Final runs

- Each test alone plus each file in order: 97 files, 730 tests, 0 failures either way, no assert-less tests.
- `node --test`: 730 pass.

## Dependent tests and fixes

| file | test | depended on | fix |
| --- | --- | --- | --- |
| api_backfill.test.mjs | rows arriving after the drain route straight through | test 1's socket, drained staging and rows 1-2 in the pane | `drained(ids)` helper: fresh pane, buffer and watermark, new connection whose rejected backfill drains `ids` |
| api_backfill.test.mjs | a row already covered by the watermark is dropped | rows 1-3 from tests 1-2 | `drained([1, 2, 3])` |
| api_backfill.test.mjs | a malformed row does not cost the rest of the frame | rows 1-3 and the open socket | `drained([1, 2, 3])` |
| api_backfill.test.mjs | a new capture token drops the stale watermark and re-seeds | adopted "cap-one" and old rows held (alone the "old rows must not survive" asserts were vacuous) | `drained([1, 2])`; the file tracks the adopted token so a later fresh connection is not a reset |
| api_capture_reset_stage.test.mjs | the re-seed after a reset is not raced by the live rows in the token's own frame | test 1's socket, adopted "cap-a", watermark 2 | `page(rows)` helper: new connection adopting the file's current token and merging a-1, a-2; new tokens from a counter |
| api_capture_reset_stage.test.mjs | a reset landing mid-staging is not sorted across | an adopted token (alone "cap-c" was adopted as first, so no wipe) | `await page([])` first; token from the counter |
| api_db_reset_misfire.test.mjs | a row the snapshot already carried is a duplicate, not a capture reset | test 1's socket, seeded snapshot, `linesFetches === 1` | `seeded()` helper: fresh backfill of the snapshot plus adopted token; fetch count asserted relative to the test's start |
| api_db_reset_misfire.test.mjs | ids restarting low is NOT a reset on its own | seeded snapshot (watermark 10) | `seeded()` |
| api_db_reset_misfire.test.mjs | a new capture token wipes and re-seeds, even with every id higher than those held | row "live 11" from the previous test (alone the "must not survive" assert was vacuous), adopted token | `seeded()`, then feed "live 11" and assert it landed before the token |
| api_db_reset_misfire.test.mjs | a reset on a silent target is caught with nothing but a keepalive | an adopted token (alone "cap-c" was the first token, adopted) | `seeded()`; token from the counter |
| api_high_rate_pending.test.mjs | an ordinary rate counts every matching row for a paused pane | the setup test's socket | `stream()` helper: fresh panes, buffer, ids, connection, then a quiet rate tick clearing any latched shed |
| api_high_rate_pending.test.mjs | the shed stops counting, and the release recovers the count | 10 rows counted by the previous test | `stream()`, then the previous test's tick and 10-row feed as asserted setup |
| api_high_rate_pending.test.mjs | a filter change recounts the backlog against the new filter | 3010 rows pending from the shed test | `stream()`, feed 3010, assert pending 3010 |
| api_pane_queue.test.mjs | a live pane keeps at most VIEW_MAX queued rows, newest first out | the setup test's socket | `stream()` helper (fresh panes, buffer, ids, connection) |
| api_pane_queue.test.mjs | a paused pane counts rows without retaining any | the burst from the previous test | `burst()`: `stream()` plus one VIEW_MAX+120 feed |
| api_pane_queue.test.mjs | a pane whose filter excludes the rows gets neither | the burst (passed alone but vacuously: no rows fed at all) | `burst()`, asserting the burst reached the live pane |
| api_pane_queue.test.mjs | the flush trims the rendered set to VIEW_MAX and refreshes the jump button | the unflushed burst | `burst()` |
| api_pane_queue.test.mjs | a further burst still cannot grow a paused pane's retention | the flushed burst | `burst()` then the flush wait |
| api_plot_seed.test.mjs | the seed is bounded: capped channels, capped points, no decimation | test 1's page load and its `seen` requests | `freshPage()` helper: watermark 0, buffer, charts, lanes and `seen` cleared, connect and settle |
| api_plot_seed.test.mjs | the seed does not double-count what the backfill replays | the seeded chart from test 1 | `freshPage()` |
| api_plot_seed.test.mjs | a reconnect does not seed again on top of the history it already holds | the seeded page from test 1 | `freshPage()` |
| settings_dirty.test.mjs | an edit marks its section, and typing the saved value back clears it | init and open from test 1 | `fresh()` (reset CONFIG retention, puts, failPut, confirms, reopen) |
| settings_dirty.test.mjs | saving one section leaves another's unsaved edit marked | init, open | `fresh()` |
| settings_dirty.test.mjs | a failed save keeps the section marked | init, open | `fresh()` |
| settings_dirty.test.mjs | removing a port row is an unsaved edit; an untouched new row is not | init, open | `fresh()` |
| settings_dirty.test.mjs | the x with unsaved edits asks, names the sections, and stays open when declined | the Ports edit (removed row) left by the previous test | `fresh()` plus `editPortsAndToken()` builds both edits |
| settings_dirty.test.mjs | Escape with unsaved edits asks too, and accepting closes | open dialog with Ports and token edits from the two tests before | `fresh()` plus `editPortsAndToken()` |
| settings_dirty.test.mjs | reopening re-renders from the config, so nothing is unsaved and closing does not ask | passed alone but vacuously: nothing had been edited | builds edits, discards them via the x, then reopens (one added assert that the close happened) |
| settings_dirty.test.mjs | Enter saves the section the field is in, and nothing from a section without a Save | init | module-level init, `fresh()` |
| settings_dirty.test.mjs | a saved token is confirmed in the note style, not the error slot | init, open | `fresh()` |
| settings_offline.test.mjs | daemon down: an edit to a daemon section is not an unsaved change, a token edit is | offline open from test 1 | `openWith(true)` |
| settings_offline.test.mjs | daemon back: the next open is editable again | init; offline typing in cfgHost from test 2 | opens offline, types 0.0.0.0, closes, then `openWith(false)` |
| settings_offline.test.mjs | daemon down after a good load: the stale config does not make it editable | the good load from test 3 | opens with the daemon up (asserted), closes, then opens down |
| settings_pj.test.mjs | a change applies live, and both fields show the daemon's answer | init, open | `fresh()` (reset daemon double, failNextPut, echoAs, reopen, clear puts) |
| settings_pj.test.mjs | a typed dest is sent as typed | init, open | `fresh()` |
| settings_pj.test.mjs | a refused change re-syncs the checkbox and keeps the typed dest | init, open | `fresh()` |
| settings_pj.test.mjs | save as default applies, then writes the config with the applied values | init, open | `fresh()` |
| settings_pj.test.mjs | save as default saves nothing when the apply is refused | init, open, and the ticked box from the previous test | `fresh()`, ticks the box itself |
| settings_ports_baud.test.mjs | a {cleared, zero, negative, not a number} baud is refused by name and saves nothing (4) | init, open; in order the error slot also still held the previous refusal | `openFresh()` per test (render clears cfgPortsErr) |
| settings_ports_baud.test.mjs | a baud above the daemon's bound is refused here, not by a 422 | init, open | `openFresh()` |
| settings_ports_baud.test.mjs | the bound itself still saves | init, open | `openFresh()` |
| settings_ports_baud.test.mjs | a valid baud still saves, and carries the typed value | init, open | `openFresh()` |
| settings_ports_eol.test.mjs | a save sends every row's eol, so a picked value lands and an untouched one is kept | init, open | `openFresh()` (resets the /status double too) |
| settings_ports_eol.test.mjs | storage: the cap hint carries the current content, file size and trimmed lines go in its title | the open from test 1 with the first /status figures | `openFresh()` |
| settings_ports_eol.test.mjs | the update line says a check has not run, without guessing at an env var | the render of an earlier open | clears cfgUpdateNow, then `openFresh()` |
| settings_ports_eol.test.mjs | the empty sessions row names the control that exists | the render of an earlier open | `openFresh()` |
| settings_ports_eol.test.mjs | each refusal names the field by its label | init, open | `openFresh()` |
| settings_ports_identify.test.mjs | a save sends identify per row, off where unticked | init, open | `openFresh()` |
| settings_sessions_bundle.test.mjs | open the dialog (no asserts) | setup step disguised as a test | removed; folded into `openFresh()` |
| settings_sessions_bundle.test.mjs | a session row offers export, bundle and delete, in that order | the "open the dialog" test | `openFresh()` |
| settings_sessions_bundle.test.mjs | the bundle button downloads the zip endpoint, not the db export | the "open the dialog" test | `openFresh()` |
| settings_storage_cap.test.mjs | open the dialog (no asserts) | setup step disguised as a test | removed; folded into `openFresh()` |
| settings_storage_cap.test.mjs | a size cap above the daemon's bound is refused by name, and saves nothing | the "open the dialog" test | `openFresh()` |
| settings_storage_cap.test.mjs | the bound itself saves | the "open the dialog" test | `openFresh()` |
| theme_storage.test.mjs | toggling the theme still applies it when the write is refused | `initTheme()` (the click listener) and the light theme from test 1 | `boot()` runs initTheme once per module (a second call would add a second toggle listener), and the test sets data-theme light itself |
| plots_finite.test.mjs | a well-formed stream decodes and scales as declared | first in file (empty models) | `fresh()` (clearAllCharts + clearAllDigital) first |
| plots_finite.test.mjs | a non-finite f4 sample is dropped, and takes no partial row with it | stream 0 def and baseline sample from test 1 (FAIL alone) | `stream0()`: fresh, define stream 0, one sample, precondition assert |
| plots_finite.test.mjs | a finite sample that a large scale factor overflows is dropped after scaling | accumulated charts (only the finite sweep grew) | `fresh()` |
| plots_finite.test.mjs | a mid-stream overflow leaves the earlier points intact | stream 2 def from the test above; alone `!ps 2` was dropped as unknown stream | fresh, defines stream 2 with its first sample; added assert that s2 holds both finite points |
| plots_finite.test.mjs | a non-finite literal cannot enter a definition or an ad-hoc point | no ad-hoc chart yet | `fresh()`, so `charts.has("p1\|adhoc") === false` is guaranteed, not inherited |
| plots_finite.test.mjs | the plot value grammar accepts and rejects the same shapes the daemon does | ad-hoc chart from the test above | `fresh()` |
| plots_finite.test.mjs | an out-of-range tick never reaches the x array | stream 0 def (FAIL alone) | `stream0()` |
| plots_finite.test.mjs | a malformed field or arity is rejected outright | stream 0 def; alone every line was rejected as unknown stream, so vacuous | `stream0()` |
| plots_finite.test.mjs | integer fields decode with the declared width and sign | accumulated charts | `fresh()` |
| plots_finite.test.mjs | a scale on an enum or bits channel invalidates the definition | accumulated lanes | `fresh()` |
| plots_finite.test.mjs | a channel type that reaches Object.prototype is rejected | finite sweep over other tests' charts; alone nothing to sweep | fresh, plus a legal `!pd 8 a:u1` after the loop asserted to decode, so the rejection discriminates |
| plots_finite.test.mjs | clearAllCharts empties the model | charts from earlier tests (alone clears an empty model, vacuous) | `stream0()` first |
| plots_finite.test.mjs | an ad-hoc tick past the daemon's decimal digit cap is rejected | lanes left by earlier tests in the sweep | clearAllCharts replaced by `fresh()` |
| plots_paused_freeze.test.mjs | pausing snapshots the samples the freeze covers | shared module lets frozenDraw/frozenEdge | `pausedChart()` returns them |
| plots_paused_freeze.test.mjs | the frozen view survives the whole ring rotating past the freeze | test 1's paused chart and snapshot (FAIL alone) | `pausedChart()` + `rotate()` (with its precondition asserts) |
| plots_paused_freeze.test.mjs | a channel first seen while paused draws nothing into the frozen view | paused, rotated chart (FAIL alone) | `pausedChart()` + `rotate()` |
| plots_paused_freeze.test.mjs | resuming drops the snapshot and returns to the live arrays | paused, rotated chart (FAIL alone) | `pausedChart()` + `rotate()` |
| plots_seed_paused.test.mjs | a seed arriving under 'pause all' does not un-freeze anything | nothing: its run_before FAIL came from api.js being mid-edit during the sweep (seedLastMs threw, seed caught it); passes alone 24/24 | unchanged |
| plots_seed_paused.test.mjs | a seed never fills a surface that already holds samples | chart seeded by test 1 (FAIL alone) | clears, `pauseAll(true)` as in order, seeds stream 0 into the empty chart, asserts 3 points, then the original second seed |
| plots_zoom.test.mjs | a selection pauses every surface and stores one range in the active mode's units | first in file | `fixture({ zoom: false })`: setZoom(null), pauseAll(false), clear, feed(100) |
| plots_zoom.test.mjs | the zoom reaches the chart that was NOT dragged | zoom from test 1 (FAIL alone) | `fixture()` (drag 1020.8..1030.7 made) |
| plots_zoom.test.mjs | currentData ships the zoomed range with a one-sample margin on each side | zoom from test 1 (FAIL alone) | `fixture()` |
| plots_zoom.test.mjs | an empty or non-finite selection is ignored | zoom from test 1; alone null before and after, vacuous | `fixture()`, plus assert the zoom stands before the selections |
| plots_zoom.test.mjs | the zoom is dropped in another time mode and on resume | zoom and 100 samples (FAIL alone) | `fixture()` |
| plots_zoom.test.mjs | a time-mode change drops the range without resuming a frozen UI | charts from test 1 (FAIL alone) | `fixture({ zoom: false })` |
| plots_zoom.test.mjs | double-clicking the digital lanes clears the zoom and resumes every surface | charts (FAIL alone) | `fixture({ zoom: false })` |
| plots_zoom.test.mjs | a zoom is not overwritten by samples that keep arriving while paused | charts (FAIL alone) | `fixture({ zoom: false })` |
| plots_export_button.test.mjs | a chart with no channel shown disables its export button and says why | both channels shown at start | `setShown([true, true])` |
| plots_export_button.test.mjs | and the click that gets through anyway exports nothing | both channels hidden by test 1 (FAIL alone) | `setShown([false, false])` + disabled precondition |
| plots_export_button.test.mjs | showing a channel again re-enables the button | both hidden; alone the click hid channel a while b stayed shown, vacuous | `setShown([false, false])` first |
| plots_export_button.test.mjs | a digital panel with no lane shown disables its export button | all lanes shown | `showAllLanes()` first |
| plots_export_button.test.mjs | nothing this panel built would be refused by the daemon | URLs built by earlier tests (FAIL alone) | builds its own: chart and digital, changes off and on with decode unticked, asserting each built a URL, then the original asserts |
| plots_theme_rebuild.test.mjs | an unchanged theme rebuilds nothing | charts from test 1; alone `deepEqual([], [])`, vacuous | builds two laid-out charts and asserts both have a uPlot first |
| digital_edge.test.mjs | the seed and the live stream interleaving out of order cannot move the edge backwards | lane with edge 1004 from test 1 (FAIL alone) | `constantSignal()` |
| digital_edge.test.mjs | pause pins the edge at the newest sample; clear-all forgets it | same (FAIL alone) | `constantSignal()` |
| digital_edge.test.mjs | a constant signal still advances the right edge with every sample | first in file | uses `constantSignal()` (same lines) |
| digital_paused_freeze.test.mjs | pausing snapshots the vertices the freeze covers | module lets frozenB0/frozenEdge | `pausedLanes()`: resume, clearAllDigital, n = 0, feed(10), pause, snapshot |
| digital_paused_freeze.test.mjs | the frozen view survives the whole ring rotating past the freeze | pause from test 1 (FAIL alone) | `pausedLanes()` + `rotate()` |
| digital_paused_freeze.test.mjs | readouts and cursor scrub read the frozen data, not the rotated ring | paused, rotated, redrawn lanes (FAIL alone) | `pausedLanes()` + `rotate()` + redrawDigital() |
| digital_paused_freeze.test.mjs | a lane born while paused draws nothing into the frozen view | paused lanes (FAIL alone) | `pausedLanes()` + `rotate()` |
| digital_paused_freeze.test.mjs | resuming drops the snapshots and returns to the live rings | paused, rotated lanes (FAIL alone) | `pausedLanes()` + `rotate()` |
| digital_repaint.test.mjs | an idle tick does not clobber the cursor readout with the live value | first user of lane st | `enumLane()` (same lines, plus clearAllDigital) |
| digital_repaint.test.mjs | a repainting lane still tracks the live value while a cursor is up | lane st from test 2 (FAIL alone) | `enumLane()` |
| digital_repaint.test.mjs | leaving the panel returns every readout to the live edge | lane st plus test 3's IDLE sample at 2004 (FAIL alone) | `enumLane()` + that sample and a redraw |
| digital_zoom.test.mjs | a zoom the charts are frozen on is the window the lanes draw too | none | uses `zoomAndPause()` (same lines) |
| digital_zoom.test.mjs | a zoom recorded in another time mode does not move the lanes | cursor left at 240 px by test 2; alone px was NaN, notEqual vacuous | `zoomAndPause()`, measures px itself in host mode with a 240 px precondition |
| digital_zoom.test.mjs | a live panel ignores the zoom, so it cannot draw a frozen window while scrolling | zoom and pause from test 2 (FAIL alone) | `zoomAndPause()` |
| can_freeze_surface.test.mjs | the pause button and the paused tag follow the state, whoever set it | rows ingested by the previous test (an empty table is not live, so pause-all skipped it) | clears the table and ingests a frame first |
| can_logic.test.mjs | an empty table exports nothing at all | initCan() from the CSV test (canExport listener) | snapshotExport calls a new initCanOnce(); every initCan() in the file goes through it, so listeners are bound once |
| can_logic.test.mjs | a collapsed group is still exported, and bus is always a CSV column | same | same |
| cmdbar_eol.test.mjs | a pick is explicit, carried on the body, and beats the port's value | ports a/b, both connected, from the previous test | new twoPorts() helper (also used by the test that set them up) |
| cmdbar_eol.test.mjs | picking the default again clears the override, without clearing site data | same | twoPorts() at the start |
| cmdbar_mode.test.mjs | OK monitor arriving on a status poll flips a never-picked port to cmd | known ports ["sbc"] and raw start from test 1 | sets sbc known and unanswered, asserts raw, then flips |
| cmdbar_sole.test.mjs | a marker that lands is acknowledged in the strip | a port attached by the previous test | managed(["mcu"], ["mcu"]) first |
| exportdlg.test.mjs | no URL this dialog built would be refused by the daemon | URLs and a navigation built by every earlier test | builds its own: live chart with changes+deadband, paused chart Shown, CAN frames; original asserts kept |
| export_paused_window.test.mjs | the watermark still bounds a range that is not the shown window | chart paused and driven past the ring by test 1 | pausedPastRing() helper (test 1's setup and its asserts; ticks climb file-wide) |
| export_paused_window.test.mjs | a live chart sends no id_to at all | same | pausedPastRing() first |
| export_paused_window.test.mjs | nothing these panels exported would be refused by the daemon | URLs built by every earlier test | exports down each road itself (paused chart Shown/Session, live chart, lanes paused/live, paused panes) |
| statusbar_proto.test.mjs | the same alias follows OK monitor once the port answers | status and refreshStatus from test 1 | attachBuiltins() helper, called by all three tests |
| statusbar_proto.test.mjs | and it posts /send, like any other port that has not answered OK monitor | same (passed alone, but without the /status maps it was not testing the shadowing) | attachBuiltins() first |
| statusbar_session_dialog.test.mjs | an empty or whitespace name is refused in the dialog and sends nothing | dialog opened by test 1 | openFreshDialog() helper (session null, refreshStatus, posts cleared, open) |
| statusbar_session_dialog.test.mjs | a daemon refusal stays in the dialog, not in the status bar strip | refreshStatus from test 1; strip hidden by test 3's success (stub starts it visible) | openFreshDialog(), then flash and dismiss so the strip starts hidden, asserted |
| statusbar_session_dialog.test.mjs | an unreachable daemon is reported in the dialog, which stays open with the typing | open dialog from earlier tests | openFreshDialog() |
| statusbar_session_dialog.test.mjs | reopening clears the last refusal; Escape and Cancel close without starting | a refusal left by earlier tests (vacuous alone: sesErr already empty) | makes its own refusal ("Name is required", asserted), cancels, then reopens |
| statusbar_logic.test.mjs | a failure flashes the chip and stays readable in the strip until dismissed | daemon title set by earlier polls (alone '' made the doesNotMatch vacuous) | polls a base status first and asserts the normal title |
| terminal_history.test.mjs | a rebuild drops the capture pages and restarts the walk from the buffer | state.maxId 1000 from earlier tests (freeze point) | sets state.maxId = 1000 |
| terminal_paused_freeze.test.mjs | pausing records the row the pane is frozen at | nothing, but it set up the story | livePane([1,2,3]) helper |
| terminal_paused_freeze.test.mjs | a WS reconnect's backfill does not un-freeze the pane | pane frozen at 3 by test 1 | livePane([1,2,3]) and pause |
| terminal_paused_freeze.test.mjs | the frozen pane still re-filters when its filter changes | frozen at 3 with rows 4..6 backfilled by test 2 | frozenAt3WithNewer() helper |
| terminal_paused_freeze.test.mjs | rebuild leaves a frozen pane's 'N new' count alone | same | frozenAt3WithNewer() |
| terminal_paused_freeze.test.mjs | resuming folds in everything that arrived while frozen | same, and pending 3 from test 4 | frozenAt3WithNewer() (pending 3), asserts the pane starts frozen |
| terminal_paused_freeze.test.mjs | the frozen pane survives the shared buffer rotating past its freeze | live pane over rows 1..6 from test 5 | livePane([1..6]) first |
| terminal_paused_freeze.test.mjs | resuming drops the snapshot and returns the pane to the live buffer | frozen snapshot and rotated buffer from test 6 | builds both, asserts a snapshot exists before resuming |
| rulings_panes_window.test.mjs | a shift-click on the lanes' selector is the same group state | reverse case: group span already 5 from test 4, so the first assert held without the lanes click | shift-clicks 300 on a chart first |
