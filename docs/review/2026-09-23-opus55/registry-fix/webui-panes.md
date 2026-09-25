# Fix batch webui-panes (registry leg, 2026-09-25)

HEAD at start `3f10153`; tree shared with the other batches, nothing committed.
Scratch: `~/tt-data/mcuscope-2026-09-25/fix-webui-panes/` (`revert_verify.py`, `regen_fixture.py`, `shots_dialogs.py`, browser screenshots).

Tally: 22 of 22 items fixed. R72-1 is fixed in state.js but its call sites are handed over. R77-1 (owner picked A) and the `.db` preflight landed in a second pass.

## Findings

Every "fails with the fix reverted" line is from `revert_verify.py`: 64 mutations in a private copy, each run against its test file unmutated (pass) and mutated (fail); all 64 caught.

- R19-1 fixed. `state_host_marker.test.mjs` fails with either gate reverted (tick, divider).
  - Deviation: the gate is `row.dir === "-"` (host row), not `row.dir === "rx"`. Identical on every row the daemon writes (a marker row is `rx` or `-`), but `=== "rx"` breaks the rows without `dir` in 8 existing test files (`makeRow` has none; `state_marker_tick` is webui-settings').
- R19-2 fixed (UI half; the daemon half, `USER_REGEX_FLAGS = regex.ASCII`, is in the tree from daemon-api). `pane.js asciiSpaces` rewrites `\s`/`\S` inside and outside classes before `new RegExp`.
  - Fixture: 8 lines added (`reading ٣`, `café`, U+0085, U+FEFF, U+00A0, U+2028, a lone U+00A0, a lone U+FEFF) and 11 `same` patterns (`[\s]`, `[^\s]`, `[\S]`, `^\S+$`, `[b]\s`, ...); every expect row regenerated from `store._make_regexp` and checked to extend the old one.
  - `pane_regex_dialect.test.mjs` fails with the rewrite removed and with each of its 5 branches broken; `test_pane_regex_dialect.py` 93 passed.
  - `.` against an astral character stays the documented residual (SPEC 9.1).
- R22-5 fixed. `state_int_field.test.mjs`.
- R23-1 fixed: a pause freezes at the newest row the pane drew (else below its queue, else `maxId`), which also covers rows the high-rate guard never fed. `terminal_pause_queue.test.mjs`.
- R25-1 fixed (D-10 A): clear-all records `{id, captureGen}`; `addPane` starts there within the same capture. `terminal_clear_all_birth.test.mjs` (record, use, capture guard).
- R25-2 fixed (D-11 A): `buildWindowButtons(..., onZoom)`; charts pass `() => chart.paused`, lanes `() => digitalPaused`; `setChartPaused` repaints. `plots_zoom_live_chip.test.mjs`.
  - A lanes repaint in `setDigitalPaused` was deleted: no test could catch it, because a zoom needs a chart and every chart's pause repaints all selectors.
- R25-4 fixed: `+ pane` copies `canFilter`/`canFilterPrev`; `closePane` calls `can.js showCanUnfilter(anyFiltered)`. `terminal_can_unfilter.test.mjs`.
- R25-5 fixed (tooltips only, no test: no branch).
- R26-1 fixed (D-12 A): pause snapshots the anchor lists of the frozen rows' ports (`frozenAnchors`, keyed by `captureGen`); history pages add theirs to it uncapped (`noteTickAnchor` gained `cap`). `terminal_paused_anchors.test.mjs` (read, capture guard, paged anchors, resume, cap).
  - Sibling found and fixed: hovering such a line put the chart cursor through the live store, so the cursor vanished while the stamp read `~N`. The line element carries the anchors its stamp read (`__anchors`), `plots.js xForRow` uses them. `terminal_paused_hover.test.mjs`.
- R28-2 fixed: `except regex.error`. With `_make_regexp` swapped for one raising `KeyError` in a scratch copy: the fixed test fails all 33 refused cases, the old `except Exception` passes 10 of them.
- R34-1 fixed. `chrome_color_grammar.test.mjs`.
- R51-1 fixed (D-15 A): `historyMiss` counts lines read since a page last landed a row; the hint reads `no match in the last N lines` and a `search older` footer button walks on. `terminal_history_stall.test.mjs` (count, reset on rows, reset with history, button, hint, click).
- R54-2 fixed: `paneFilterParams` builds both. `export_paused_window.test.mjs` fails with its `chan` loop removed.
- R57-1 fixed: `visibleCanIds(port)`; an option `value` may be a function of the other fields, followed until the user edits that field (`exportdlg.js optionChanged`). `can_export_port.test.mjs` new test (per port, follow, edited).
- R72-1 fixed in state.js (D-18 A), effective only once the call sites below pass the flag.
  - `api(..., { background: true })`: a 401 opens no prompt and shows a `token needed` badge (`#tokenBadge`, beside the daemon chip); a click on it re-arms and prompts; any non-401 answer hides it. `state_token_background.test.mjs` (background, badge, click re-arms).
  - Nothing in this batch's files makes a background request, so the poll still prompts until statusbar.js changes.
- R76-1 fixed: `render` drops the append path when `state.anchorTick` moved since the pane last drew (`pane.tickZero`). `terminal_tick_zero.test.mjs`, which also shows the append path still runs with the zero unchanged.
- R77-1 fixed (owner: A). `timewindow.js continueHost`: one host clock (beside `tickClocks` in digital.js, cleared with it) holds epochs `{id, offset, x}`.
  - A row newer than any seen whose ts is more than 1 s behind the newest drawn x opens an epoch; rows from its id on draw at `ts + offset`.
  - `routePoints` converts every sample once, so charts, lanes and the tick epochs (keyed by host time) all read the continued x.
  - Each chart and lane breaks when its epoch changes. The hover maps `row.ts` through the epoch for `row.id`, and so does the tick estimate's anchor. The lanes' time-bounded export maps drawn x back to ts (`hostTsAt`).
  - A late older row or a non-finite ts never becomes the edge; the list is capped at 1000.
  - `plots_host_step.test.mjs` fails with each of 12 mutations: routed, epoch, threshold, newest only, top newest, NaN guard, cap, chart break, lane break, hover host, hover estimate, export ts, clear.
  - While building it, `null + 0` let a `null` ts reach the x arrays (`state_plot_tick` caught it): `continueHost` returns a non-finite ts untouched, so the members' class-6 gate still drops it.
  - SPEC 9.2's residual line now describes the restart; host labels after a step read the pre-step clock's continuation until clear-all or a reload.
  - Browser: `g5_plots.py chips zoom linked clear gutter` 12 passed (Chromium, no step driven: that needs a clock change, `browser.md`).
- F-1 fixed: `yAxisSize` measures the labels (floor 46 px, converges past the second cycle). `plots_y_axis_size.test.mjs` (size, converge, CSS px, floor). Browser: `g1_owed.py solo solo_sweep` all 6 passed (were 3 failures).
- F-2 fixed: `render` drops `selfScroll` when the offset is where the last scroll event left it (`pane.seenTop`), which also covers api.js's capture-reset path. `terminal_self_scroll.test.mjs` (drop, seen offset) with a clamping `scrollTop` double.
  - Browser: `g3_terminal.py firstscroll` 3 of 3 passed (were 2 failures); new `g3_terminal.py clampfilter` (added to the tools dir) passed: a filter change that clamps a paused pane's offset leaves it paused and pages nothing, and the next top hit pages.
- F-3 fixed: `.dlane .gut .pt { flex: 0 0 auto }`. Browser: `g5_plots.py gutter` passed; screenshot shows `bench pwm_...`.
- F-4 fixed: `.dlg-body` and `.field` get `grid-template-columns: minmax(0, 1fr)`. Browser: `g6_dialogs.py adversarial` A10 passed at 1600 and 420 px; `shots_dialogs.py`: settings, attach, session bodies have no horizontal overflow at 1600 and 420 px.
- `.db` preflight (owner ruling) fixed: `state.js preflight` asks `GET /sessions/{ref}/export?check=1&wait=1` (daemon-api's) and shows its refusal (a deleted session, a full queue) instead of navigating.
  - Moved: `state_download_preflight` (E-9 tests), `state_preflight_session_ref` (rewritten: one check of the export path itself; a 400 and a 503 each reported and not navigated), `state_download_wait`. SPEC 9.1's sessions line updated.
  - Mutations caught: no check (GETs the copy), no `wait=1`, a refusal ignored.
  - `settings_revision` (2) and `settings_export_hold` (7) fail until webui-settings moves them, as agreed.

## Files outside the batch list, edited

- `tests/webui_js/state_logic.test.mjs:319`: asserted `intField("1e9") === 1e9`, the grammar R22-5 removes; now asserts NaN.
- `tests/webui_js/terminal_logic.test.mjs:81`: asserted `selfScroll` true after a rebuild that moved nothing, the F-2 defect itself; now asserts false.
- Both are owned by no batch; one line each.

## CHANGELOG

- Fixed: a pane added after "clear all" no longer shows the lines it cleared.
- Fixed: a paused pane no longer takes in lines that arrived just before the pause; they count as new.
- Fixed: a paused pane's `~N` tick estimates, and the chart cursor on hovering one, survive a long pause.
- Fixed: under the tick base, a line stamped before the first ticked line after "clear all" is redrawn against the new zero.
- Fixed: after a filter change, a paused pane's first scroll to the top loads older lines.
- Fixed: a pane history search that finds nothing in 1000 lines says so and offers "search older", instead of asking for a scroll the view cannot make.
- Fixed: a marker from `mcu mark` or the web UI whose text starts `!m @N` is shown as typed and no longer sets the tick zero.
- Changed: a pane regex's `\s` matches ASCII whitespace only, as the daemon's export and history filters read it.
- Fixed: a chart born live while a drag zoom stands shows its own window span, not the zoom chip.
- Fixed: CAN "unfilter" also restores a pane added as a copy of a CAN-filtered one, and goes away when no filtered pane is left.
- Fixed: the CAN history export prefills the chosen port's ids and follows the Port choice until edited.
- Fixed: number fields in Settings and the attach dialog refuse hex, binary, exponent and `1000.0` forms.
- Fixed: a soloed chart's y axis is wide enough for labels such as `10000` and `8e+307`.
- Fixed: the Digital / Enum gutter keeps a lane's port tag whole and shortens the lane name instead.
- Fixed: the export dialog's Clock row fits inside the dialog.
- Fixed: a hand-edited saved colour that is not `#rrggbb` is ignored.
- Added (once the poll passes `background`): a 401 on a background request shows a `token needed` badge instead of opening a prompt over what is being typed.
- Fixed: a host wall clock stepping back over 1 s breaks the charts and lanes and continues them, instead of stacking every later sample at the pre-step edge and stopping the lanes' live edge.
- Fixed: a session `.db` download refused for a full export queue says so, instead of a failed download.

## Needs another batch

- webui-settings, `statusbar.js:494`: the poll passes `{ background: true }`: `api("GET", "/status", undefined, ac ? ac.signal : undefined, { background: true })`. This is the R72-1 defect path.
- No owner, `api.js`: the same flag on the requests no user action makes: `:287`, `:358`, `:366`, `:411`, `:492`, `:554`.
  - `setAuthFailed` (`:31`) says `access token required (reload to retry)`; the badge now offers the retry, so `access token required: click "token needed"` reads true.
- webui-settings, `dom_stub.mjs` `cloneNode` drops `dataset`, so a template-cloned pane's channel buttons toggle channel `undefined`.
  - `terminal_createpane` and every test flattening `paneTpl` cannot click a channel; `terminal_self_scroll` drives the port select instead for that reason.
- webui-settings or daemon-api: `test_webui_js.py::test_export_guard_double_agrees_with_the_daemon` fails: `exportdlg_guards.mjs` still words the pydantic errors that daemon-api's D-8 grammar (`must be ASCII digits`, ...) replaced. Not caused here.

## Needs Windows

- Nothing platform-specific in this batch.

## Needs a browser

- Firefox: F-2 relies on reading `scrollTop` after a render returning the clamped offset (driven in Chromium only); F-4 (Firefox's `datetime-local` has another intrinsic width, per the leg report).
- Eyes: the `search older` button and `token needed` badge styling; R25-5 tooltips.
- `registry-triage/browser.md` R19-1, R25-1, R25-2 lines are scriptable now; R72-1 needs the statusbar change first.
- R77-1 with a real clock step (a VM or a spare machine, `browser.md`): charts break and continue, the lanes' edge keeps moving, a hovered line after the step lands on its sample.

## The two questions

1. Least confident, rechecked:
   - The F-2 clamp case (a filter change that clamps must stay swallowed): only the stub test covered it, so it was re-driven in Chromium (`g3_terminal.py clampfilter`, passed). Firefox not driven.
   - R77-1: the design was proven only in the stub, where every consumer of host x was listed by grep (`x.host`, `xsHost`, `row.ts`, `tickOffsetAt`, `estimateTickX`) and each got a mutation. That grep found the lanes' time-bounded export, which needed the inverse mapping (`hostTsAt`). A real clock step has not been driven.
   - R72-1: the fix reads as done but the poll does not pass the flag; confirmed by grep (`statusbar.js:494`), hence "effective once the call sites pass it" above.
   - Revert-verification first showed 5 misses (`\S` in and out of a class, class tracking, the lanes repaint, the anchor cap); the fixture and the cap test were extended until all were caught, and the lanes repaint was deleted as unreachable.
2. Not thought about until now:
   - The hover cursor reads the same anchors as the stamp (fixed, above), found by listing every consumer of `tickAnchors`.
   - The `.dlg-body`/`.field` change reaches every dialog: settings, attach, session re-shot at two widths, `g6_dialogs.py settings attach session keyboard w4` passed.
   - R77-1's conversion also reached the class-6 gate (a `null` ts became 0), caught by the existing `state_plot_tick`; the member gates now see the raw non-finite value again.
   - The capture reset (api.js) keeps a paused pane paused with its old snapshot and clear-all point: both are keyed by `captureGen`, and a test drives each.

## Also

- A per-file run on the final tree: every JS file passes except `settings_revision` and `settings_export_hold` (above). Between runs webui-settings changed `dom_stub.mjs` (a `<select>` now drops a value no option carries); `terminal_self_scroll` now sets `knownAliases` so its port choice is a real option.
- `test_webui_js.py` was run once. It runs the whole JS suite, which the brief rules out; every other run was one file at a time.
- The scratch `browser/` dir (6.4 MB of screenshots and logs, capture databases removed) is left for a recursive delete that needs the owner's confirmation.
