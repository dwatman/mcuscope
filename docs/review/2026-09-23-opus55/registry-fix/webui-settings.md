# Fix batch webui-settings

HEAD at start: `3f10153c7558104570a90817148b91e1ffb6f5db`.

Tally: 18 batch items plus 5 handed in by other batches.
22 are fixed.
The reload badge needed no change: it was already done.

## Findings

Each line gives the id, then what it came to, then the test that fails once the fix is reverted.
Revert-verification ran on a private copy with `~/tt-data/mcuscope-2026-09-25/fix-webui-settings/revert.py`; its output is `revert.out` next to it.

- R12-1: fixed. When `/devices` fails, the Ports section now says `could not list devices: <why>`.
  - Fails on revert: `settings_ports_refusals` "R12-1".
- R23-2: fixed. A download that is fetched (the token path, and the bundle) is held by path, in a `fetching` map, until it is saved. A re-rendered row joins that hold (`joinFetch`). It is not a nav hold: no note shows and no timer runs.
  - Fails on revert: `settings_export_hold` "a row re-rendered while its fetch is out..." fails on each of three reverts (no join on the `.db`, no join on the bundle, releasing only the clicked button).
  - I dropped a redundant `fetching.has(path)` guard from the click handler, because no test could catch its removal.
- R61-1: fixed. The token is rendered once when the dialog opens. After the load it is rendered again only while the token section is clean, in both the answered and the unreachable branch.
  - Fails on revert: `settings_loading_hold` "R61-1 ..." fails on each of three reverts.
- R71-1 / D-17 A: fixed. The dialog now uses config.py's bounds: `retention_days >= 1`, `min_sessions >= 0`, cap 0 or at least 1 MiB (any whole MB is), and every upper bound below 2^63. The check is `v < 2 ** 63`, which is exact for a double, where `<= 2**63 - 1` is not.
  - The wording changed to "... a whole number ..., 1 or more" and similar.
  - `MAX_DB_BYTES` is no longer imported.
  - Fails on revert: `settings_storage_cap` catches all four bound reverts. The "loaded 5000 retention, edit sessions, save" test is there too.
- R73-1: fixed. A `cfgGen` counter means only the newest `GET /config` issued writes `cfg` and the badge. This covers both `refreshConfig` and the read in `saveAttachedPortToConfig`.
  - Fails on revert: `settings_config_gen` (new), one test per site.
- R73-2: fixed. The marker box is cleared only if no `input` event has arrived since the send (`markerEdits`).
  - Fails on revert: `cmdbar_marker_answer` "R73-2".
- O-70a: fixed. Only an untouched "+ port" row is dropped. A saved port whose alias was cleared is refused with `port "<alias>": the alias is empty; use remove to delete a port`. A new row that has been typed into but has no alias is refused with `a new port row has no alias`. The dirty snapshot follows the same rule.
  - Fails on revert: `settings_ports_refusals` fails on each of three reverts.
- R27-8: fixed. The stub `<select>` now behaves like a browser's:
  - a value that no option carries reads `""`;
  - a change to the options selects the first again;
  - it falls back to the last option marked `selected`, then the first;
  - an `<option>` with no value reads its text.
  - The stub parses `index.html` for every `<select id>` and its static options.
  - The `CLAUDE.md` sentence is updated.
  - Revert check: `populateCmdPort` with `opts.push(cur)` dropped and `sel.value = cur` fails `cmdbar_detached_pick` with the new stub and passes with the old one.
  - `dom_stub.test.mjs` (new) pins the rules. I checked the same sequence in headless Chromium (`select_check.py`): `['a','b','','a','','y','9600']`, identical.
- R27-9: fixed. `FakeEl.closest` walks `parentNode` and matches the selector. A new `lineCell(row)` helper builds a real `.ln` hit element, and `settings_dirty` nests its fields inside a `.cfg-sec` section.
  - Fails on revert: M4 (`.lnX`) fails plots_hover_tick, plots_tick_reset, digital_tick_reset and plots_pause_edge. M5 (`.cfg-secX`) fails settings_dirty.
- R27-22: fixed. `installExportDaemon(env, sessions, channels, ports)` accepts arrays or getters. `export_paused_window` and `plots_pause_edge` pass in the ports and plot channels they feed. `export_paused_window` also gains an all-ports pane export and a positive control for `no such port`.
  - Fails on revert: M2 (`port=zz`) fails both.
- R27-23: fixed. `test_webui.py::test_every_class_the_modules_select_is_declared` checks the selector classes against `index.html`, the modules' own `className` literals, and uPlot's vendor source. It has a built-in negative control that renames `side-body`.
- N-JS-1: fixed. `MatchBudgetExceeded` is now named in the double's "Not mirrored" list.
- N-JS-2: fixed. The `/devices` stub in `statusbar_session_dialog` now lists `/dev/ttyUSB3` and `COM4`.
- N-JS-3: fixed. `test_webui.py` has two new tests:
  - `test_the_js_config_doubles_answer_as_the_daemon_does`: the 409 text equals `config.CONFLICT_MESSAGE`; both doubles use the same revision predicate; the daemon does not check a missing revision and refuses a stale one.
  - `test_the_js_lines_clamp_is_the_daemons`: `api.js LINES_LIMIT_MAX` and `SERVER_CLAMP` equal what `/lines` actually serves for `limit` 5x the clamp.
  - Fails on revert: all three mutations fail.
- N-JS-4: fixed. `statusbar_logic` and `plots_seed_grammar` (two sites) now assert the `console.error` message and the error.
  - Fails on revert: each of the three message mutations fails.
- R75-1: fixed.
  - `settings_loading_hold` FIELDS now comes from `index.html`'s `cfg*` inputs minus `cfgToken`. Fails on revert: dropping `cfgPjDest` from `DAEMON_FIELDS`.
  - `test_the_export_double_declares_the_routes_parameters` compares DECLARED with `app.openapi()`. Fails on revert: removing `limit`.
- R75-2: fixed. `NON_SPACE_WS` is now every JS `\s` member plus U+001C-001F and U+0085, minus the space, and the comment says so.
  - Fails on revert: a `markerTick` that splits on NBSP fails. The old list lacked NBSP.
- Reload badge (owner ruling): already done, no change. `statusbar.js` compares against `<meta name="mcuscope-version">`, which the daemon stamps. `statusbar_reload_stamp.test.mjs` and `test_webui.py::test_the_served_page_carries_the_daemon_version` pin it. The first `/status` is used only when the page is unstamped, i.e. not served by a daemon.

Handed in by other batches:

- R72-1 call sites (webui-panes, plus the orchestrator's `api.js` addition): fixed.
  - The `statusbar.js` poll now passes `{ background: true }`.
  - In `api.js`, the six requests at the old `:287 :358 :366 :411 :492 :554` spread `BG`.
  - The `setAuthFailed` text is now `access token required: click "token needed"`.
  - `api_token_background.test.mjs` (new) answers 401 to each of the seven requests in turn, with the prompt budget re-armed first. Its positive control is a foreground 401 on the same fake, which does prompt.
  - Fails on revert: removing the flag at any one of the seven sites fails its own case.
- `.db` preflight tests (webui-panes): moved. `settings_revision` (11, 12) and `settings_export_hold` (2, 3, 7, 8, 9, 12, 13) now answer `GET /sessions/2/export?check=1&wait=1`: `{ok:true}` or 400 `no such session: 2`. The `state.js` side had already landed; all pass.
- D-9, cmdbar half (daemon-process): fixed. `submitCmd` and `submitMarker` strip U+0020 only (`stripSpaces`).
  - `cmdbar_space_trim.test.mjs` (new) covers tab, NBSP, U+3000 and VT, plus a tab-only command. Fails on revert: `.trim()` at either site.
- D-8 grammar in the export double (daemon-api, webui-panes): fixed. The double now mirrors UrlUInt, UrlInt, UrlFloat and UrlBool, in the daemon's order and wording. The lax `pyInt`, `trimWs` and `BOOLS` are gone.
  - `test_export_guard_double_agrees_with_the_daemon` passes.
  - Fails on revert: a lax bool fails it, and so does a uint that accepts `-`.
- `cloneNode` drops `dataset` (webui-panes): fixed. The clone now copies `dataset` and the attributes (and an option's value).
  - Fails on revert: `dom_stub.test.mjs`.

## CHANGELOG

- Web UI: a failed device listing in Settings > Ports now says `could not list devices: ...` instead of showing an empty list.
- Web UI: Settings > Storage now accepts every value the config loader accepts (retention above 3650 days, more than 1000 kept sessions, caps up to 2^63 bytes). A file holding such a value no longer blocks every Storage save.
- Web UI: clearing the alias of a saved port and saving is refused, naming the port. Use remove to delete one. Previously the port was deleted silently.
- Web UI: a token typed into Settings while the dialog was still loading is no longer overwritten when the load lands.
- Web UI: the command bar and the marker box trim spaces only. A leading or trailing tab or other whitespace is sent as typed (D-9).
- Web UI: a 401 on the status poll or on a stream backfill or seed no longer opens a token prompt over what is being typed. The `token needed` badge shows instead.
- Web UI: a re-rendered session row no longer starts a second fetched download while one is in flight, and a marker label retyped identically before the ack is kept.

## Needs another batch

- daemon-api or daemon-process (D-9, marker half): `server.py:336 _stripped` uses `str.strip` for `MarkerBody.text`, so the daemon still strips tab and Unicode whitespace from a marker.
  - This contradicts D-9 A ("U+0020 only, host and firmware"). The UI now sends `\thello`, and the daemon stores `hello`.
  - Change: `v.strip(" ")` for the marker text. Check the other `_stripped` users before changing the shared helper.
- webui-panes (`state.js`): `MAX_DB_BYTES` (`:176`) has no user left. Delete it, and drop `ConfigStorageBody.max_db_bytes` from the "Mirrors ..." comment above it.
- Unowned JS tests that still hand-roll `closest` (R27-9 class): `terminal_paused_hover:45` and `plots_host_step:62, :138`. They pass, but ignore the selector. Change: `elementFromPoint = () => lineCell(row)` (the new stub export).
- Unowned: 21 other callers of `installExportDaemon` still run without port and channel guards. Opt in by passing getters as `export_paused_window` does.
- Orchestrator: `rm -rf ~/tt-data/mcuscope-2026-09-25/fix-webui-settings/rv` is due once revert-verification is no longer wanted. It holds a 26 MB copy of `host/mcuscope` and `host/tests`, and `revert.py` needs it. I did not run it: global rules require confirmation for a recursive delete.

## Files outside the batch list, edited

I made these edits because this batch's harness or behaviour change broke tests that no batch owns.

- `api.js`: the orchestrator assigned it mid-task.
- `cmdbar_eol`, `cmdbar_sole`: the stub now supplies `index.html`'s port-default option, so I dropped their hand-made copy (and `cmdbar_eol`'s `browserSelect` wrapper).
- `settings_ports_baud`: an input's value is a string, as in a browser.
- `settings_ports_eol`: the new storage refusal wording. Its `min_sessions` case uses `-1`, since `1001` is now valid.
- `settings_storage_cap`: rewritten for the D-17 bounds, and it now carries R71-1's tests.
- `cmdbar_marker_answer`: typing now emits `input`, and it carries R73-2's tests.
- New test files: `api_token_background`, `cmdbar_space_trim`, `dom_stub`, `settings_config_gen`, `settings_ports_refusals`.

## Needs Windows

- Nothing in this batch is platform-specific.
- The node suite and `test_webui.py` were run on Linux only.

## Needs a browser

- R72-1: with a token configured, let the status poll hit a 401 while typing in the marker box. The badge should show, no prompt should open, and a click on the badge should prompt.
- O-70a and R12-1 wording in the real Settings dialog: I checked the text in the stub, but not the layout.

## Verification

- `node --test` over every file in `host/tests/webui_js/`, one at a time: 192 files, all `# fail 0`.
- `uv run python -m pytest tests/test_webui.py tests/test_webui_js.py::test_export_guard_double_agrees_with_the_daemon`: 20 passed.
- `ruff check tests/test_webui.py`: clean.
- Every new test was run alone with `--test-name-pattern`. `settings_config_gen`'s attach test needed a fix to pass alone and is fixed.
- The revert list above: 44 mutations. Each fails its test, apart from one first miss (the O-70a snapshot filter), which I pinned and re-verified.
- There is no em or en dash in the touched files (`grep -P "[\x{2013}\x{2014}]"`).

Not verified:

- The whole-suite `test_webui_js.py::test_webui_js_suite`, beyond one accidental early run. That run showed `state_plot_tick` failing, which is not my file and was passing by the final serial run. The serial per-file run replaced the whole-suite run.
- Retention or session values above 2^53 lose precision in JSON on the way through the dialog. This is not addressed, and no user sets such values.

## The two questions

1. Least confident: that the stub's `<select>` rules match a browser. The stub is now the standard every JS test is held to.
   - I re-drove the rules in headless Chromium (Playwright 1.62.0, `select_check.py`). The result matched on all seven cases, including the "blank pick, then an option appended" case.
   - Second: R61-1's "clean" check depends on the open-time render having set the token's clean state. I pinned this with a control that leaves junk in the field before opening.
2. Not thought about: the sibling paths of each change.
   - The daemon's marker strip (above) is the other side of D-9.
   - Other callers of the double and other hand-rolled `closest` sites are listed above.
   - `saveAttachedPortToConfig` was a second writer of the badge, and it is fixed under R73-1.
   - `intField` (`state.js`) still `.trim()`s numeric fields. This is harmless, and outside D-9's scope (commands and markers).

## Surprises

- The `state.js` `.db` preflight change had landed by the time I ran, so 9 tests in my files were failing until I moved them.
- The stub select change broke four unowned tests. The leg's estimate, that only `statusbar_session_dialog` would break, assumed a stub without static options.
