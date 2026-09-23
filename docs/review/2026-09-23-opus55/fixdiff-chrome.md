# Fix-diff leg: chrome half

HEAD: `b994076c88c875ad43c30789cda6039d147b7abe` (diff `6e4f6f7..HEAD`).

Scratch, drive scripts and the mutation runner are under `~/tt-data/mcuscope-2026-09-24/fixdiff/`:

- `chrome-copy/` holds the mutation copy.
- `browser/drive*.py` and `browser/probe7.py` are the Chromium and daemon drives.
- `mutate.py` with `muts1.json` and `muts2.json` is the mutation runner.

## Findings

### F1. The reload badge misses an upgrade when the tab comes Back into the UI (medium)
- `statusbar.js:98-100`: `pageVersion` is the first `/status` version, so a new document running cached old modules adopts the new daemon's version as its own.
- Scenario:
  - A 0.5.0 tab navigates away (a typed URL or a link in the same tab).
  - The daemon is upgraded to 0.6.0.
  - The user presses Back.
  - Chromium builds a new document (`back_forward`, not bfcache) from the cached 0.5.0 HTML and modules.
  - The first poll reads 0.6.0 and the badge never shows, while the old build runs against the new API.
- Driven in `browser/drive4.py` (Chromium, a scratch package whose `__version__` and a `BUILD` marker change between two daemon runs):
  - The control tab, left open, showed `badgeShown: True`.
  - The tab that went Back showed `build: 'old', badgeShown: False, ver: '0.6.0'`.
  - `drive5.py`: clicking the badge does load the new build.
- Fix: stamp the serving version into the page at serve time (for example `<meta name="mcuscope-version">`, templated by the server) and compare `/status` against that.
  - Cached HTML then carries the version of the build it belongs to.
  - This also closes the fix-chrome doubt about a failed first poll.

### F2. A session `.db` export refused 503 by the new export pool is silent (medium)
- `state.js:355-366` (`preflight`) and `settings.js:395-404`: a session `.db` export goes out as an `<a download>` navigation.
  - Its preflight checks only the sessions list, so the server's new `503 too many session exports in progress` (`server.py` `_run_export`) never reaches the page.
- Scenario:
  - Four session builds are in flight: `EXPORT_WORKERS + EXPORT_QUEUE_MAX`, and a cancelled download's build keeps its slot.
  - The user clicks export on a fifth session.
  - Chromium cancels the download with nothing shown. The code's own comment says a navigation saves the error body under the export's name, which would be JSON in a file named `.db`.
- Driven: a scratch daemon with `EXPORT_QUEUE_MAX = -2` (every build refused). `drive6b.py` recorded `downloads: [('auto-...db', 'canceled')]`, no `actionErr` chip and an empty `cfgSessionsErr`. The positive control, the unpatched daemon, saved a SQLite file.
  - `page.route` cannot stand in for this, because Playwright does not route download navigations.
- Fix (owner should pick):
  - (a) The server makes a navigation wait for a slot instead of answering 503. Recommended: a browser download cannot show a refusal.
  - (b) An admission probe in the preflight (still racy).
  - (c) Take the session `.db` down the fetch path. This buffers the whole file in the tab.

### F3. A staging overflow during the backfill leaves an unmarked hole and deletes a staged shed notice (medium-low)
- `api.js:730`: the staging trim exempts capture tokens but not the new `{gap}` notices. The rows it drops are reported only by `console.warn` (`api.js:811`).
  - So there is no divider and no chart or lane break. This is the WEBUI-3 rule applied on one path only, and the "never drop silently" convention.
- Scenario: a busy link (above 5512 rows while the first `/lines` fetch is in flight) drops the oldest staged rows.
  - The terminal joins row 3 to row 516, and every chart draws its last level across the hole.
  - A daemon `{gap}` notice staged before them is deleted with them.
- Driven: a scratch test in the `api_ws_gap` harness (parked backfill of ids 1-3, then `{gap: 5}` and live ids 4-6003).
  - The buffer ids ran `[2, 3, 516, 517]` with no gap row.
- Fix: in `stageRow`'s trim, keep shed notices and fold the dropped count into a `{gap: n}` notice left at the trim point. `drainStaging`/`markShed` then mark the hole like any shed.

### F4. Stale help on the command bar's port select (low)
- `index.html:120`: the `#cmdPort` title still says auto resolves to "the sole attached or sole connected one", "or (auto) when that is ambiguous".
  - CLI-18 removed the connected clause. With several ports attached, the bar now refuses under auto. This is class 58.
- Scenario: a user with two ports, one of them disconnected, reads the tooltip and expects auto to reach the connected board, then gets "pick a port".
- Reasoned: grep. No test reads the title.
- Fix: "The bracketed entry is auto: the sole attached port. With several attached, pick one; the bar will not send under (auto)."

### F5. `userText` hand-lists invisible characters and misses a set that renders blank (low)
- `state.js:144`: `INVISIBLE_RE` covers the bidi controls. It misses these default-ignorable characters:
  - U+00AD, U+034F, U+115F-1160, U+17B4-17B5, U+180B-180F.
  - U+2060-2065, including WORD JOINER.
  - U+206A-206F, U+3164, U+FFA0, U+FFF0-FFF8, U+1BCA0-1BCA3, U+1D173-1D17A, U+E0000-E0FFF.
  - This is class 75: a hand-kept list standing in for an enumeration.
- Scenario: sessions `run` and `run⁠` (or `runㅤ`) look identical in the export dialog and in the settings list, so the user exports or deletes the wrong one. The ruling says "zero-width controls".
- Driven: `node` enumeration of `\p{Default_Ignorable_Code_Point}` against `INVISIBLE_RE`.
- Fix:
  - Use `/[\p{Default_Ignorable_Code_Point}\p{Bidi_Control}]/gu` (Chrome 64+, Firefox 78+), possibly keeping U+FE00-FE0F so emoji names stay readable.
  - Switch `charCodeAt` to `codePointAt`, so a tag character prints as `<U+E0041>`.
  - Add a test that enumerates the whole property.

### F6. `settings_user_text.test.mjs:55` passes only after the test before it (low)
- The ports-dropdown test reads `cfgPortsBody` rows that exist only because test 1 opened the dialog.
- Driven: run alone with `--test-name-pattern`, it fails with `TypeError: Cannot read properties of undefined (reading '_fields')`. Every other test in the 14 slice files with new tests passes alone.
- Fix: open the dialog inside that test, through a shared helper.

### F7. Changed branches no test covers (low)
Each of these survived a hand revert against its test files (`mutate.py`).

- `api.js:254` `pushRow(g, chartsCleared)` changed to `pushRow(g, false)`.
  - Clear-all nulls the anchors (`terminal.js:840`). A staged notice under the clear would make the divider's ts the relative zero, which is the row the clear covers.
  - Test: park the backfill, stage `{gap}` plus rows, clear all, release, and assert `state.anchorTs` is still null.
- `cmdbar.js:183`: the `cmdGen++` on the "pick a port" refusal.
  - Without it, an earlier in-flight `/cmd` answer overwrites the refusal.
  - Test: hold a `/cmd`, refuse a line, then resolve the held command.
- `cmdbar.js:177`: a whitespace-only raw line.
  - SPEC says raw mode sends the line as typed; `"   "` is untested.
- `api.js:233`: `Number.isInteger(row.gap) && row.gap > 0`.
  - `{gap: 0}` and `{gap: "5"}` are untested. `"5"` would concatenate into `pendingGap`.
  - Test both, or delete the guard as redundant with the daemon contract.

## Doubts verified

- WEBUI-6, the name still rendering reversed: resolved.
  - Chromium (`drive1.py`): the chip reads `■ ⁨<U+202E>gpj.exe-…⁩`, is one row (29 px), computes `text-overflow: ellipsis`, and has scrollWidth 576 against clientWidth 170.
  - The screenshot is `browser/header.png`.
- WEBUI-7, the first version seen: gives a wrong answer in a realistic sequence (F1).
  - The failed-first-poll variant needs a page loaded while the daemon is down, which only a cache load produces, so it has the same root cause.
- CLI-18, stale `knownAliases`: refuted as harmful.
  - A `port: null` `/send` or `/cmd` with two ports attached gets 400 `port is ambiguous; specify one of: sim, dead` (`probe7.py`).
  - The strip shows it. The only cost is that the input is cleared; the line stays in the history.
- Characters against `unicode-bidi` CSS: the characters are the right choice.
  - `title`, `<option>` text and `confirm()` are reached by no author CSS, and all of them carry session names.

## Needs a human in a browser

- Firefox (Linux and Windows): start a session with a 60-character name and check that the header chip is one row ending in an ellipsis (`text-overflow` on a `<button>`).
- Firefox: open the UI, navigate the tab elsewhere, upgrade and restart the daemon, press Back, and check whether "daemon updated: reload" shows (bfcache or cache load).
- Firefox: run a scratch daemon with `EXPORT_QUEUE_MAX = -2`, click a session's export in Settings, and check whether a `.db` holding JSON is saved.
- Windows (Chrome and Firefox): in the session delete confirm and the export dialog's session list, check that U+2068/U+2069 show as nothing, not boxes.
- Chrome: upgrade the daemon, restart the browser with session restore, and check whether the restored UI tab shows the badge.

## Checked, nothing found

- `api.js` WS gap:
  - `pendingGap` is reset on re-arm and on a capture reset.
  - The backfill-covered guard holds, and a notice ends its drain segment.
  - A pane's clear cut hides the divider (clearId), and the high-rate path refills on rebuild.
  - 8 mutations killed.
- `api.js` pane queue block trim: 2 mutations killed.
- Export dialog against the server's new 400s:
  - The streamed exports' preflight shows `no such port`, the `since_id` floor and unknown-parameter refusals inline.
  - The bundle is on the fetch path.
  - `test_export_guard_double_agrees_with_the_daemon` passes when run alone.
- `cmdbar.js` CLI-18 and HEALTH-9:
  - Chromium with two ports: the strip reads `$   hello error pick a port: 2 are attached` and the input keeps `'  hello'`.
  - The M46 detached pick and the connected-only mutation were killed.
- Reload badge click: loads the new build in Chromium (`drive5.py`).
- `state.js` tokenizer and ticks:
  - The marker mutations were killed.
  - The terminator branches were killed by `plots_event_tokens`.
  - Stored `raw` keeps a lone trailing CR (`serial_link.py:808`), so the JS strip mirrors Python's `split_tokens(normalize_line(raw))`.
- `userText` call sites: 15 sites wrapped.
  - `pt.target` is exempt: the serial decode is `ascii`/`replace`, so it cannot carry U+202E.
  - Option values stay raw devices.
- Class 34: no new name-keyed store; `portConnected` removed cleanly.
- Class 59: `#reloadBadge` (`inline-flex`) is covered by the global `[hidden] { display: none !important; }`.
- Classes 61, 62 and 72: no new `.value` writer from a re-render, no new select, no `focus()` in the diff.
- Class 46: `row.gap` and `pt.description` tolerate absence.
- Deleted symbols (`syncWindowButtons`, the `TITLE_MAX` export, `portConnected`, `watermarks()`) have no remaining references in webui, tests or docs.
- Cross-site guard: the UI's same-origin download navigations and `location.reload()` pass (drives 5 and 6b).
- All 19 slice test files pass run singly. Per-test isolation covered 14 files; the only failure is F6.
