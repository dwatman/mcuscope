# Fix batch: chrome

Revert-verification ran in a scratch tree (`~/tt-data/mcuscope-2026-09-23/fix-chrome/tree`): HEAD pane files, my working copies, and the panes-side tick hooks applied.
The runner is `~/tt-data/mcuscope-2026-09-23/fix-chrome/mutate.py`: 28 mutations, all 28 killed, and the files were restored after each one.
All the tests listed below also pass in the shared tree as of the end of this batch.

## Findings

### WEBUI-6 (chips half): session name clipped and bidi-isolated
- `state.js:142` adds `bidiIsolate(s)`, which wraps the text in U+2068/U+2069 (first-strong isolate). Now `userText`; see the follow-up at the end.
  - Used by `statusbar.js:174,176,181` (chip text, running and automatic-run titles), `exportdlg.js:171` (session option) and `settings.js:363,432` (name cell, delete confirm).
- `style.css:74`: `#sessionBtn` gets `max-width: 24ch`, `overflow: hidden`, `text-overflow: ellipsis` and `white-space: nowrap`.
- Tests: `statusbar_session_bidi.test.mjs` (the chip, both titles, and a static CSS check), `settings_sessions_bidi.test.mjs`, and the updated `exportdlg.test.mjs`.
- Revert-verified at each of the 6 call sites, the isolate characters and the CSS rule: all killed.

### WEBUI-7: daemon updated, reload
- `statusbar.js:92,98`: `renderReload` remembers the first `/status` version and shows `#reloadBadge` whenever a later poll reports a different one.
  - Clicking the badge calls `location.reload()` (`statusbar.js`, in `initStatusbar`).
  - The markup is at `index.html:34`, and `style.css:71-72` reuses the restart badge style.
- Test: `statusbar_reload_notice.test.mjs`. It covers the same version twice, a failed poll, a new version, a return to the old version, and the click.
- Revert-verified with three mutations (no render; compare against the last version instead of the first; no reload on click): all killed.

### HEALTH-9: raw mode sends as typed
- `cmdbar.js:177-178`: raw mode keeps the text untrimmed and can send an empty line; cmd mode still trims and still drops a blank line.
  - An empty raw line is not added to the history.
- Test: `cmdbar_raw_line.test.mjs`.
- Revert-verified with four mutations (trim in raw, refuse empty raw, no trim in cmd, empty line into history): all killed.

### HEALTH-17 M46 and CLI-18 (UI): the user picks the port
- `cmdbar.js:87`: `auto` resolves only to the sole attached port. The "sole connected among several" clause is gone, and with it `state.portConnected` (removed from `state.js` and `statusbar.js`).
- `cmdbar.js:181`: while several ports are attached, a cmd or raw line under `auto` is refused in the strip with `pick a port: N are attached`. The typed line stays in the box.
- The M46 guard (`cmdbar.js:57`, which keeps a detached pick) was already there; it is now pinned by `cmdbar_detached_pick.test.mjs`, with one remaining board, with two, and with a positive control.
- Test: `cmdbar_sole.test.mjs` (rewritten, see below).
- Revert-verified: removing the refusal, clearing the line on refusal, letting auto name a port among several, and dropping the M46 guard were all killed.

### HEALTH-18: the freeze registry's watermark hook is deleted
- `freeze.js:18`: `registerSurface` requires only `isLive` and `setPaused`; `watermarks()` is deleted.
  - The registry could not serve the export: a pane or chart exports its own freeze bound, while the registered value was the minimum over every member.
  - The export bound is now read from the export URL (`id_to`) in `can_freeze_surface.test.mjs` and `rulings_panes_export.test.mjs`.
- Revert-verified in the scratch tree only, by mutating the bound in `can.js` and in `digital.js`: both killed.
- The panes half is under "Not done".

### HEALTH-24 (JS half)
- `chrome.js`: `syncWindowButtons` and its test in `chrome.test.mjs` are deleted.
- `layout.js:13`: `TITLE_MAX` is no longer exported. `cleanTitle` still uses it, so the const stays.
- These are deletions, so there was no mutation to run. `smoke.test.mjs` and `module_load_order.test.mjs` pass.

### JS-5, JS-6, tokenizer (`state.js` lineTick)
- `state.js:226` `firstToken` splits on U+0020 only. `state.js:228` `computeTick` sends each tag to its decoder's hook: `!ps` to `plotSampleTick`, `!p` to `adhocTick`, `!can`/`!can1-9` to `canTick`. The range guard applies to each hook's answer.
  - The hand-copied `!can`/`!p` check is gone. The hooks are declared at `state.js:138`, and panes already publishes them (`can.js:95`, `plots.js:55`).
- `state.js:245` `markerTick` follows `protocol.parse_marker`: the tick is the first space-separated word only when that whole word is `@<digits>`, and text must follow it.
- A `!ps` miss is still not cached; every other miss is.
- Tests:
  - `state_line_tick.test.mjs`: dispatch, a tag followed by a tab, `\x1f` or NBSP, the anchor after a refusal, the range guard, the marker cases, and caching.
  - `state_decoder_tick.test.mjs`: the real decoders, including `!can 4000000000 zz 100 -`, `!p 7 a=1\x1fb=2` and accepted lines.
- Revert-verified with seven mutations (the old mirror, a `\s+` tokenizer, no range guard, the old marker regex, no text required after the tick, caching a `!ps` miss, not caching a `!can` miss): all killed.

### Stale comments
- `state.js:132`: `main.js` corrected to `app.js`, and the hooks comment now covers the tick hooks.
- `state.js:118`: "three per-alias maps" corrected.
- `app.js`: deleted the "Build progress" header and the namespace-import rationale. The always-true `typeof terminal.filterPaneTo` guard is replaced by named imports (`app.js:13,24,185`).

## Existing tests edited

- `freeze.test.mjs`: deleted the two watermark tests; added a both-functions-required test; the surface helper no longer registers `watermark`.
- `chrome.test.mjs`: deleted the `syncWindowButtons` test and its import.
- `can_freeze_surface.test.mjs`: `watermarks().can` replaced by `id_to` from the table's history export. The helper asserts that the dialog opened.
- `rulings_panes_export.test.mjs`: `F.watermarks().digital` replaced by `id_to` from a session-mode `exportDigital`. An assertion taken before any lane existed was dropped, because an empty panel opens no dialog.
- `cmdbar_sole.test.mjs`: rewritten for CLI-18.
  - The "sole connected port is the target" tests became "several attached: nothing sent, `pick a port`, line kept", in both modes.
  - The `connected` argument and the connect-state relabel case were removed.
- `cmdbar_eol.test.mjs`:
  - `twoPorts` no longer sets `portConnected`.
  - The two tests that send now pick port `b` first (a write needs a pick).
  - One status-poll value changed so the default label still visibly moves.
- `statusbar_proto.test.mjs`: dropped the three `portConnected` assertions.
- `statusbar_logic.test.mjs`: the session chip text now carries the isolate characters.
- `exportdlg.test.mjs`: the session option labels now carry the isolate characters.
- `state_logic.test.mjs`: added stand-in `canTick`/`adhocTick` hooks, like its existing `plotSampleTick` stand-in.
- `terminal_logic.test.mjs` and `terminal_tick_estimate.test.mjs`: now import `can.js`, which publishes the `!can` tick. Their `!can` anchors had no decoder without it.

## SPEC edits (section 9.1)

- Status bar: the `daemon updated: reload` badge when `/status` reports a version other than the first this page saw.
- Command box: cmd mode trims; raw mode sends the line exactly as typed, empty included.
- Port select:
  - `auto` resolves to the sole attached port, or to none while several are attached.
  - With several attached, the bar refuses a line under `auto` until a port is picked.
  - A detached pick stays picked.
- Session control: the name on the button is clipped to one row, and every session name shown is bidi-isolated.

## Changelog

- Web UI: a "daemon updated: reload" badge appears when the daemon is upgraded under an open page.
- Web UI: raw mode sends the line exactly as typed, including leading spaces and an empty line.
- Web UI: with more than one port attached, the command bar refuses to send under `auto` until a port is picked. It no longer follows the only connected board.
- Web UI: a long session name no longer wraps the header, and a direction-override character in a name no longer reorders the text around it.
- Web UI: a malformed `!can`/`!p` line (for example bad CAN flags) no longer sets the tick anchor. Event and marker ticks split tokens on spaces only, as the daemon does.

## Not done

- Panes batch, HEALTH-18 second half. Four `registerSurface` calls still pass a `watermark` key that nothing reads:
  - `can.js:576`, `digital.js:895`, `plots.js:1308` and `terminal.js:335`: delete the `watermark: ...` key from each.
  - Once those are gone, delete `freeze.js` `minWatermark` and its comment (lines 27-36), the `minWatermark` imports in `plots.js:8` and `terminal.js:7`, and the `minWatermark` test at the end of `freeze.test.mjs`.

## Doubts

- WEBUI-6: the question about the name itself still rendering reversed was answered yes; see the follow-up at the end.
- WEBUI-7 compares against the first version this page saw, not the version that served the page. A tab whose first poll fails, and whose daemon is upgraded before the next poll succeeds, adopts the new version as its own and never shows the badge. Embedding the version in `index.html` would close this, but that needs the server.
- CLI-18 in the bar refuses on `state.knownAliases.length > 1`, which is refreshed by the 5 s status poll. A port attached from another client moments earlier can let one `auto` write through. It goes out with `port: null`, which the server batch's rule then refuses.
- Not checked: how the badge and the clipped chip look in a real browser (the DOM stub has no layout), and whether `unicode-bidi` CSS would be preferred over the U+2068/U+2069 characters for chips.

## Follow-up: invisible characters shown (coordinator ruling on the WEBUI-6 question)

- `state.js`: `bidiIsolate` is replaced by `userText(s)`. It writes U+061C, U+200B-200F, U+202A-202E, U+2066-2069 and U+FEFF out as `<U+XXXX>`, then wraps the result in U+2068/U+2069. `\u202Egpj.exe` now shows as `<U+202E>gpj.exe`.
- Call sites:
  - Session name: chip text, both chip titles, export dialog option, settings list cell, delete confirmation.
  - Session note: tooltip in the settings list.
  - Port chip: the device text, and the description and device string in its hover.
  - Device lists: the device and description in the attach dialog and in the settings ports dropdown. Option values stay the raw device.
  - Not wrapped: port aliases (ASCII grammar) and the `OK monitor` target (the serial decode maps non-ASCII to U+FFFD).
- Tests:
  - `state_user_text.test.mjs`: the spoof case, every listed character, the neighbours of each range left alone, and every occurrence replaced.
  - `statusbar_user_text.test.mjs` (renamed from `statusbar_session_bidi.test.mjs`): chip, titles, port chip, attach dialog, CSS.
  - `settings_user_text.test.mjs` (renamed from `settings_sessions_bidi.test.mjs`): cell, note, confirm, ports dropdown.
- Existing test edited: `statusbar_logic.test.mjs` (port chip test), where the chip text and hover now expect isolated device text.
- Revert-verified: 25 mutations in the scratch tree (`mutate.py`, WEBUI-6 entries), all killed.
  - They cover each of the 15 call sites, dropping the isolation, disabling the escape, dropping each character range, matching only the first occurrence, and widening a range.
- The shared tree passes all 48 test files that touch statusbar, settings, exportdlg or `userText`.
- SPEC 9.1 session control bullet rewritten: the listed characters are shown as `<U+XXXX>` in names, notes, device strings and descriptions, and bidi-isolated.
- Changelog: in the web UI, invisible formatting characters in session names, notes and device text are shown as `<U+XXXX>`, so a direction override cannot disguise a name.
- The `freeze.js` `minWatermark` follow-up stays with the panes batch, as ruled.
