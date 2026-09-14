# Fix batch: web UI chrome (pre-release round)

Files changed: `webui/settings.js`, `statusbar.js`, `cmdbar.js`, `state.js`, `exportdlg.js`, `app.js`, `index.html`, `style.css`; `tests/test_webui.py`.
Tests: 4 new files `tests/webui_js/prerelease_chrome_{settings,statusbar,export,static}.test.mjs` (30 runs), one new test in `app_resizer_keys.test.mjs`, E-10 assertions rewritten in `statusbar_logic.test.mjs` and `settings_ports_eol.test.mjs`.

Revert verification: `~/tt-data/prerelease-2026-09-15/fixE/revert.py` undoes one changed branch at a time (copy, mutate, run, restore from the copy, `cmp`), 41 branches.
Raw results in `revert_results.json` there.
40 of 41 fail their test when undone; the 41st is an equivalence (see E-7).

Gates:

- Node, every file for the touched modules plus the new ones: 319 tests, 316 pass, 3 fail.
  - The 3 are in `exportdlg.test.mjs` (shown mode) and come from the panes batch's uncommitted `exportrange.js` change: they fail with my `exportdlg.js` undone, and pass on a HEAD copy of the web UI tree carrying only my files (621 tests there, 2 failures that are missing `host/tests` fixtures outside the copied tree).
- `uv run python -m pytest tests/test_webui.py`: 11 passed. `ruff check tests/test_webui.py`: clean.

## Fixed

- **E-1** (cap re-rounded on an unrelated Storage save).
  - Change: `renderStorage` keeps `capShown = {mb, bytes}`. `saveStorage` sends `capShown.bytes` while the field still reads `capShown.mb`, else `MB * n`. After an accepted PUT, `capShown` becomes what was sent.
  - Tests: 1500000, 1600000, 1048577 and 2^42-1 each round-trip; an edit to 3 sends 3 MiB; 0 sends 0.
  - Also tested: after a save whose re-read failed, typing the old rendered 1 sends 1 MiB, not the stale 1500000.
  - Revert: both branches fail (loaded bytes: 1 test; post-save update: 1 test).
- **E-2** (failed re-read after a save renders the stale config as clean).
  - Change: new `renderSaved(section, render, err)`. On a null `refreshConfig` it keeps the fields as typed, marks the section clean (what is shown is what was saved) and writes `saved; could not re-read the config`. Used by the Server, Storage, Updates and Ports saves.
  - `saveAttachedPortToConfig` now reports only a failed GET or PUT; the re-read after the PUT goes through `refreshConfig` and cannot flash a failure.
  - Tests: Ports keeps 57600 and the next save sends 57600. Server and Updates show the note. A refused PUT still shows its refusal and stays `Save *`. The attach save reports nothing when only the re-read fails, and still reports a refused PUT.
  - Revert: fallback 3 fail, markClean 1, attach path 1.
- **E-4** (a poll focuses the command input).
  - Change: `setCmdMode` focuses only when `remember` (the user's pick).
  - Tests: `OK monitor` arriving on a poll flips to cmd with zero `focus()` calls. A second port making auto ambiguous flips to raw with none. `setCmdMode("cmd", true)` still focuses.
  - Revert: 2 fail.
- **E-5** (overlapping fills duplicate options).
  - Change: a generation token on `exportdlg.fillSessions`, `statusbar.populateDevices` (returns false when superseded, and `openAttach` then does not call `showModal`), and also on `settings.renderSessions` and `openSettings` (same shape, same file).
  - `exportNow` now awaits the newest `sessionsReady`: a fill superseded by `reset range` returns early with the select empty, and awaiting only the old promise would have exported the whole capture.
  - Tests: reset range with the fills released in either order gives `["4","7"]`. Export pressed before a reset waits and sends session 7. Two Attach clicks, released oldest-first and newest-first, give one device list and one `showModal`. Two gear clicks give one `showModal`. A Storage save during the open's session fill lists each session once, and a superseded fill failing late writes no error.
  - Revert: every guard fails its test (export gen 2, export await 1, devices gen 2, openAttach 2, openGen 1, sessions gen 1, sessions error 1).
- **E-6** (Settings and Attach open nothing against a stalled daemon).
  - Change: `openSettings` passes `AbortSignal.timeout(STATUS_TIMEOUT_MS)` (4 s, now exported from `statusbar.js`) to `/config` and `/devices`, so a stall opens the read-only dialog. `populateDevices` does the same, and a timeout reads `could not list devices: no reply from daemon`.
  - Tests: fetches that never answer unless aborted, with `AbortSignal.timeout` replaced by a controller the test aborts. Nothing opens before the deadline; after it Settings is open read-only with Ports Save disabled, and Attach is open offering the simulator and custom entries. Each deadline is checked to be under 5 s.
  - Revert: settings deadline 1 fails, devices deadline 1, wording 1.
- **E-7** (offline, the command bar keeps a resolved port).
  - Change: `cmdbar.setCmdOffline(on)`, called from both branches of `pollStatus`. While offline the auto entry reads `(offline)` and the input placeholder `daemon unreachable`.
  - The input is deliberately left enabled: one slow poll disabling it would blur a line being typed (the E-4 hazard again).
  - Tests: online `(sim)`, offline `(offline)` plus placeholder and input still enabled, offline twice, back online `(sim)`. Also offline with no port ever known.
  - Revert: set 2 fail, clear 2, label 2, placeholder 2.
  - The `if (on === daemonOffline) return` early exit is EQUIVALENT on the stub (0 fail). It keeps a failed poll every 5 s from rebuilding the port `<select>`, which in a browser closes a dropdown the user has open.
- **E-9** (refused session `.db` export saved, not reported).
  - Change: `streamable` excludes `/sessions/`.
  - Test: `/sessions/2/export` answering 400 is fetched once, returns `session export failed: no such session: 2`, and creates no anchor.
  - Revert: 1 fails.
- **E-10** (file size compared with the cap).
  - Change: `dbFigures(s)` in `statusbar.js`. The bar warning is `db <content> / <cap>`, its title adds `<size> on disk.`, and the chip hover is `db <content> / <cap> (<size> on disk)`. The Settings hint shows content, with the title adding `; <size> on disk`.
  - Tests: the existing size tests now use content different from size, including the finding's shape (5.0 MB on disk, 1020 kB content, 1.0 MB cap): the bar reads `db 1020 kB / 1.0 MB`.
  - Revert: settings figure 1 fails, settings title 1, statusbar content 1, hover 1, bar title 1.
- **E-11** (accessibility attributes).
  - Change: `role="alert"` on all 9 `.inline-err` slots; `role="status"` on `#cmdResult`; `aria-label` on `#cmdInput` ("Command") and `#markerInput` ("Marker text").
  - `app.js` sets `aria-valuemin` (260) and `aria-valuemax` (workspace less the terminal column, never below the current value) beside `aria-valuenow`.
  - Tests: static scan of every `inline-err` tag, the status role and both names. Resizer range at 1600 px is 260..1274 and holds the value; at 0 px before layout the default 360 stays inside the range.
  - Revert: alert on one slot 1 fails, status 1, name 1, valuemin 1, valuemax 1, max floor 1.
- **E-12** (collapse does nothing in the narrow layout and persists).
  - Change: inside `@media (max-width: 860px)`, `#collapseBtn, #popoutBtn { display: none; }`. Expand is included because the `1fr !important` column ignores `--side-w` in the same way.
  - Test: the rule is inside the narrow block and nowhere outside it.
  - Revert: removing it fails, and moving the collapse hide out of the media query fails.
- **Class 61, `settings.js:195`** (the PlotJuggler answer overwrote a dest typed during the PUT).
  - Change: the answer's `dest` is written only while the field still reads what was sent.
  - Tests: an edit typed during a held PUT survives. A blank, untouched field still shows the daemon's kept destination.
  - Revert: 1 fails.
- **D-11, CSS half** (marker text shrinks to an ellipsis).
  - Change: `.ln.marker .divider` gets `min-width: 0`; new `.ln.marker .divider-text { min-width: 0; overflow: hidden; text-overflow: ellipsis; }`.
  - It only takes effect once `terminal.js` wraps the text in that span. See "Not done / owed": CSS alone cannot put an ellipsis on the divider's anonymous flex item.
  - Test: static checks of both rules. Revert: each fails.
- **F-8** (hand-kept id list).
  - Change: `test_webui.py` derives every `$("id")` from `webui/*.js` by regex, subtracts ids the JS assigns itself (`.id = "..."`, today `digitalCount`) and compares against every `id="..."` in `index.html`. It asserts the scan finds more than 100 ids, so a broken regex cannot pass empty.
  - Revert: `$("zzNotInIndex")` appended to a copy of `exportdlg.js` fails naming it (the old list would have passed). Breaking the regex fails the more-than-100 guard.
- **F-17** (portless `-` untested).
  - Kept the branch: `-` is `seedPort`'s and `plotIngest`'s key for a channel or row with no port (an older daemon's `/plot/channels`).
  - Test: `-` sends no `port`, `sim` and `-x` are sent. Revert (J29): 1 fails.
- **F-18** (`/can/frames` as streamable untested).
  - Test: `/can/frames?...`, `/lines/export`, `/plot/export` navigate with zero fetches and the anchor carries the path. Revert (J38): 1 fails.

## Manual verify (browser only)

- [ ] E-4: type in the marker box while a board first answers `OK monitor`; the caret stays in the marker box and Enter adds a marker.
- [ ] E-6: `kill -STOP` the daemon; the gear opens Settings read-only about 4 s later, `+ Attach` opens with "no reply from daemon"; `kill -CONT` recovers.
- [ ] E-7: stop the daemon; the port select's auto entry reads `(offline)`, an empty command input shows `daemon unreachable`, and an open port dropdown is not closed by the 5 s polls.
- [ ] E-10: with a cap set and lines trimmed, the bar shows content against the cap, and the hovers show the on-disk size.
- [ ] E-11: with a screen reader, a bad baud in Attach is announced; a command result is announced; the resizer reports a sane range.
- [ ] E-12: under 860 px, the `»` and `expand` buttons are gone; widening again brings them back and the sidebar state is unchanged.
- [ ] D-11 (after the `terminal.js` change below): a 240-character marker ends in an ellipsis inside the pane, with the dashed rules shrinking to nothing first.
- [ ] E-5: double-click `+ Attach` and the gear against a slow daemon; one dialog, one list.

## Not done / owed

- **`terminal.js`** (panes batch, D-11). Wrap the divider text in a span so the CSS above applies. At the current `div.textContent = chan === "gap" ? row.raw : "marker: " + ...` write:
  ```js
  const text = document.createElement("span");
  text.className = "divider-text";
  text.textContent = chan === "gap" ? row.raw : "marker: " + row.raw.replace(/^!m\s+(@\d+\s+)?/, "");
  div.appendChild(text);
  ```
  Contradiction in the split: triage assigned D-11's CSS as CSS-only, but `text-overflow` applies to a block container and the text is an anonymous flex item of `.divider`, which no selector reaches.
- **`exportrange.js` or `exportdlg.test.mjs`** (panes batch): the 3 shown-mode failures in `exportdlg.test.mjs` against the tree as it stands.
- **SPEC 9.1**:
  - "Capture size": the chip hover shows the capture content against the cap (the enforced figure, 3.4) with the file size on disk. The Settings storage hint shows content, with the on-disk size in its title. Once the cap has trimmed lines, content against the cap shows in the bar as a warning.
  - Settings bullet "the cap's hint carries the current capture size": "the current capture content".
  - Command box `auto` bullet: add "or `(offline)` while `/status` is failing".
  - Settings "Against an unreachable daemon the dialog opens read-only": add "including one that has not answered within 4 s". Same sentence for the attach dialog, which opens with an empty device list and the reason.
  - Settings: "a save whose follow-up read of the config fails keeps the fields as typed and says `saved; could not re-read the config`".
  - Narrow layout (where SPEC describes it, if anywhere): the sidebar hide and expand buttons are not offered under 860 px.
  - A size cap that is not a whole MiB is shown rounded and saved back unchanged unless the field is edited.
- **CHANGELOG [Unreleased], Fixed**: E-1, E-2, E-4, E-5, E-6, E-7, E-9, E-10, E-11, E-12 and the PlotJuggler dest overwrite, one line each.
- **REVIEW.md class candidates** from leg E, now confirmed by a fix:
  - "a field rendered in coarser units than stored is saved back rounded" (E-1).
  - "a background refresh moves focus" (E-4).
  - "clear before an await, fill after it" (E-5; sweep: every `textContent = ""` or `= []` before an `await` in a function that later appends).
  - "a limit shown beside a figure it is not measured against" (E-10).
  - "a hand-kept list standing in for a mechanical enumeration" (F-8).

## The two questions

1. Least confident: E-7's shape.
   - The finding offered `setKnownPorts([])` or an `(offline)` label. I chose the label plus placeholder and left the input and Marker enabled.
   - Disabling on one failed poll would blur typing, and `setKnownPorts([])` would re-render every pane and say "attach a port" while ports may well be attached.
   - The prompt glyph and the remembered mode are unchanged offline. If the owner reads the finding as "the bar must look disabled", this is short of it.
   - Rechecked: `pollStatus` has exactly two exits that reach the bar, and both now call `setCmdOffline`. A render fault in the success branch leaves the last state, which matches the existing "keep the last good paint".
   - Second: D-11 CSS cannot be confirmed without a browser. Headless Firefox (snap) wrote no screenshot here, so the claim that an anonymous flex item gets no ellipsis rests on the CSS spec, not an observation.
2. What should have been checked:
   - A real browser for every item in "Manual verify".
   - Whether `AbortSignal.timeout` exists in every browser the owner supports (Chrome 103, Firefox 100, Safari 16; `cmdbar.js` already relies on it).
   - The sibling of E-5 in `plots.js`/`api.js` seeds (panes files, not swept here).
   - Whether any other `await api(` in `settings.js` still renders stale state after a stall: `renderPj` and `renderUpdateNow` have no deadline. They fill after `showModal`, so the dialog opens, but their fields stay at the last render until the daemon answers.
