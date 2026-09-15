# Fix-diff review 2: web UI chrome (9ad910c..5489d6e)

Scope: `host/mcuscope/webui/{api,settings,statusbar,cmdbar,state,exportdlg,chrome}.js`, `index.html`, `style.css`, the round's chrome tests, `host/tests/test_webui*.py`, and the SPEC 3.x / 9.1 and CHANGELOG hunks that describe them.
Probes: `~/tt-data/prerelease-2026-09-15/fixdiff2-chrome/` (`p1_attach_btn`, `p2_settings_loading`), importing the real modules and `dom_stub.mjs` by absolute path.
In-tree JS suite at HEAD: 790 tests, 790 pass. Every test file of this leg also passes run alone, and every test in the round's own new files passes under `--test-name-pattern` alone.

Counts: HIGH 0, MED 1, LOW 6.

Ruled out (kept as negative results, one line each):

- A capture reset during a backfill leaving old-capture rows in the reset panes: unreachable. The capture token is staged while a backfill runs (`stageRow`) and applied only from `drainStaging`, which the backfill's own `.then` calls after it has finished.
- A save queued behind a refused save sending a stale revision: `putConfigNow` reads the module `revision` at execution time, not at queue time, and a 409 leaves it unchanged, so the queued save is refused too. That is the intended answer, not a defect.
- A status poll started before an action satisfying `fresh`: `statusInFlight` is the `.finally()` promise, so the chained `next()` runs after it is nulled and always starts a new poll; `followups_status_refresh.test.mjs` drives both legs.
- A clear between two backfill pages leaving `clearId` below rows already painted: `fetchSince` walks `id_to` backwards from the first page's newest row, so later pages are strictly older; the advance runs after the row loop and `panes.forEach(rebuild)` repaints. (The remaining sliver is FD2-6.)
- `openSettings`/`openAttach` clicked again while their dialog is open: both are `showModal()`, so the backdrop makes a second click on the gear or `+ Attach` unreachable. The `hasAttribute("open")` guard is belt and braces; the stub's `showModal` does set `open`, so the branch is at least exercised.
- Anything left referencing the removed token prompt: no `1008` or `handleWsAuthClose` remains under `webui/`. `getToken` is still used by `wsUrl`, `promptForToken` only by the 401 path in `state.js`.
- `clearFailureSince`: sound in both orders. The dismiss button deliberately does not bump `failGen`, and an action that started before a dismissed failure clearing an already empty strip is a no-op.
- `groupWindow()` against a chart's own span: every chart is built with `window: groupWindow()` (`plots.js:428`) and `clearAllCharts` destroys them all, so the seed span and the drawn span cannot disagree.
- Class 59 on `#cfgWarnings`: `.cfg-warn` sets no `display`, so the `hidden` attribute holds. Class 46: `renderWarnings` tolerates an absent `config_warnings` through `Array.isArray`.
- Class 78 across the round's new chrome tests: every `deepEqual(x, [])`, `doesNotMatch` and `equal(x, false)` listed by the sweep has a positive control in its own test or a named sibling in the same file.

## Findings

### FD2-1 MED: the Settings dialog now opens with every field editable, and a late `/config` overwrites what was typed into one

- Where: `settings.js:687-706` (`openSettings` shows the dialog, then `setReadOnly(true)`), `settings.js:80` (`DAEMON_CONTROLS` is six buttons), `settings.js:565` (`renderAll` on the answer).
- Defect: the round moved the open ahead of the awaits so a slow daemon cannot steal focus. `setReadOnly(true)` disables only `cfgServerSave`, `cfgStorageSave`, `cfgUpdateSave`, `cfgPjSave`, `cfgPortsSave` and `cfgPortAdd`; every input is live from the moment the dialog appears. When `/config` lands up to 4 s later, `renderAll()` writes the file's values into those inputs and `markClean` follows, so `closeSettings` does not warn either. Before this round the dialog did not exist until the answer, so there was nothing to type into: the field is new ground the round opened.
- Failure: against a `kill -STOP`ped daemon, click the gear, type `0.0.0.0` into Bind host, `kill -CONT`. The field reads `127.0.0.1` again, the section reads saved, and nothing says the edit was dropped.
- DRIVEN: `p2_settings_loading.test.mjs`: `cfgHost.disabled: false cfgServerSave.disabled: true`, `cfgHost after /config landed: "127.0.0.1"`.
- Class 61, the round's own; `renderPj` and `renderStorage` got the "write only while the control still reads what it read" discipline in this same diff and the loading state did not.
- Fix: in the loading state disable the fields as well as the buttons (one `readonly` pass over the section fieldsets), or render a field only where its current value still equals the placeholder the loading state wrote.

### FD2-2 LOW: the loading Settings dialog moves focus to the token field up to 4 s after the click

- Where: `settings.js:713` (`$("cfgToken").focus()` in the `!loaded` branch, now reached after the dialog is already open).
- Defect: the same continuation the round deferred the open out of still calls `focus()`. It used to run on a dialog appearing for the first time, where taking focus was the point; it now runs on a dialog the user has had in front of them, and possibly typing into, for up to 4 s.
- Failure: with the daemon stopped, open Settings, start typing a bind host. When the 4 s deadline expires the caret jumps to the access-token box and the rest of the host goes there.
- DRIVEN: `p2_settings_loading.test.mjs`: `focus() calls after the unreachable answer: [ 'cfgToken' ]`, with `cfgPath` already showing the read-only text.
- Class 72 (`focus()` from anything that is not the user's own action; a 4 s-deferred continuation is what E-4 established is not one).
- Fix: focus the token box only when nothing inside the dialog holds focus (`dlg.contains(document.activeElement)` is false, or `activeElement` is `body`).

### FD2-3 LOW: Attach reads enabled while a reopened dialog's device list loads, and clicking it says nothing

- Where: `statusbar.js:707-709` (`submitAttach`'s `finally { btn.disabled = false; }`), `statusbar.js:570-572` (`openAttach` holds the button), `statusbar.js:678` (the `devicesLoading` guard).
- Defect: an attach submitted from an earlier opening re-enables `dlgAttach` in its `finally`, with no generation check, so it unholds the button the reopen is deliberately holding. The `devicesLoading` guard then refuses the click correctly, but silently: no `dlgErr` text, nothing to read.
- Failure: with the daemon stalled on `POST /ports`, attach, Cancel, click `+ Attach` again. While the list still says `loading devices...` the Attach button is live-looking; pressing it does nothing at all.
- DRIVEN: `p1_attach_btn.test.mjs`: `after the old attach settled: dlgAttach.disabled = false`, `devSel options: [ 'loading devices...' ]`, `posts after the click: 1 dlgErr: ""` (no second POST, no message).
- Class 73 sibling: the generation check the round added to `submitAttach`'s two writes was not extended to the `finally`.
- Fix: in `submitAttach`, `finally { if (gen === attachGen) btn.disabled = devicesLoading; }`; `followups_dialogs.test.mjs:147` is the test to extend, since it already builds the state and only asserts the POST.

### FD2-4 LOW: the 409 text tells the user to reload twice, in two different words

- Where: `settings.js:589` (`e.message += "; reopen Settings to load the current file"`), SPEC 3.3.1 (`config file changed since it was read; reload it and try again`).
- Defect: the daemon's own sentence already carries the instruction. The line shown is `config file changed since it was read; reload it and try again; reopen Settings to load the current file`.
- Failure: a 409 in Settings reads as two conflicting instructions on one line.
- REASONED from the two strings; `rulings_chrome_settings.test.mjs:124` asserts the concatenation, so the test pins the doubling rather than catching it.
- Class 58 neighbour (in-product help naming an action twice).
- Fix: shorten the daemon's sentence to the fact (`config file changed since it was read`) and leave the instruction to the client, which knows it is a dialog; or drop the client's suffix.

### FD2-5 LOW: `preflight` interpolates the session reference into a query string without encoding it

- Where: `state.js:339` (`fetch(db ? \`/sessions?name=${db[1]}\` : path, ...)`), with `SESSION_DB = /^\/sessions\/([^/]+)\/export$/`.
- Defect: the regex captures any non-`/` run, and the capture goes into the query string raw. `downloadPath` is a general helper; every other reference in this file is built through `encodeURIComponent`.
- Failure: latent today, since `sessionRow` only ever passes `sess.id` and that is numeric. A caller passing a name (the daemon resolves `name` as id-then-name, so it would otherwise work) with `&`, `#` or `+` in it queries the wrong session or none.
- REASONED.
- Class 53 neighbour (a bound sent as a query parameter without the encoding the parameter needs).
- Fix: `encodeURIComponent(db[1])`, and take the reference from the `/sessions/{ref}/export` path it was built from.

### FD2-6 LOW: the backfill clear snapshot assumes an upper bound the first request does not actually freeze

- Where: `api.js:479-482` (the `paneClears` / `canClearGen` / `plotSeedGen` snapshot and its comment), `api.js:525-526`, `api.js:536-538`.
- Defect: the comment states "A clear clicked while this runs covers every row it is about to deliver, all captured before the click". The first page is *issued* before any clear can land, but the daemon freezes `id_to` when it processes the request, which can be after the click. Rows captured in that window are then hidden by `clearId = Math.max(clearId, state.maxId)` and skipped entirely by `canIngest`/`plotIngest`, which take one boolean for the whole backfill rather than a per-row bound.
- Failure: a clear clicked inside one request round trip of a reconnect backfill drops the handful of rows captured in that window from the CAN table and the charts for good, with no divider; the panes hide them behind `clearId`. Very narrow, and strictly better than the pre-fix behaviour it replaces.
- REASONED from `fetchSince` (pages walk `id_to` backwards, so only the first page can hold post-click rows) and the daemon's `id_to` freeze.
- Class 67 neighbour (an internal freeze read as a bound it is not), and a stated invariant that is a claim.
- Fix: record `state.maxId` at the moment of the clear rather than after the backfill, or keep the boolean and note the window in the comment instead of asserting it away.

### FD2-7 LOW: the round's SPEC 3.1 rewrite contradicts the test that pins the code, and the CLI still handles both

- Where: SPEC 3.1 as rewritten (`the handshake is refused with HTTP 403 (a browser reports it only as close 1006)`), `server.py:772` and `server.py:634` (`_deny` sends `websocket.close(code=1008)` on a websocket scope), `test_hardening.py:741,752` (`assert refused.value.code == 1008`), `test_cli.py:2286-2293` (the CLI keeps a `close_1008` branch beside `http_403`).
- Defect: removing `handleWsAuthClose` from `api.js` is right only because uvicorn puts a pre-accept ASGI close on the wire as HTTP 403, so a browser sees 1006. Nothing in the tree pins that: the one test that exercises the path uses Starlette's `TestClient`, which surfaces the ASGI close and therefore asserts 1008. Its comment goes further and says `_deny` "separates 'no token' (1008) from 'locked out' (1013)", which `_deny` does not do at all.
- Failure: bounded. If any serving path did surface 1008 to a browser, the page would no longer prompt from the WS, but the 5 s `/status` poll still 401s and prompts. The real cost is a SPEC sentence, a test comment and a CLI branch that now say three different things about one refusal.
- REASONED; not drivable in this leg without starting a daemon.
- Class 15 (the shipped artefact against the stand-in that tests it) and class 70.
- Fix: pin the wire status with a real uvicorn handshake (one test asserting the 403 on `/ws` with no token), correct the `test_hardening.py` comment to say the TestClient sees the ASGI close, and either keep or retire the CLI's 1008 branch deliberately.

## Hunks read

- `api.js` 12 of 12. `settings.js` 45 of 45. `statusbar.js` 33 of 33. `cmdbar.js` 6 of 6. `state.js` 14 of 14. `exportdlg.js` 5 of 5. `chrome.js` 2 of 2. `index.html` 1 of 1. `style.css` 1 of 1.
- `docs/SPEC.md`: 20 of 33, being every 3.1, 3.3.1, 3.4 and 9.1 hunk plus the 9.2 window-button and export hunks. The 13 not read are 2.5 grammar, 3.2 startup log, 3.5 CLI table and bundle naming, which belong to other legs.
- `CHANGELOG.md`: all 11 hunks read; the chrome lines checked line by line against the code.
- `test_webui.py` 1 of 1 (a comment only). `test_webui_js.py` 2 of 2.
- Tests: all 37 files of the leg's name list, plus `followups_backfill_clear.test.mjs` because `api.js` is in scope. `exportdlg_guards.mjs` read as the double behind the new clause derivation.
- Every behaviour change found in the chrome sources has both its SPEC 9.1 sentence and its CHANGELOG line, and they agree with the code, with one exception: the WS handshake refusal changed in SPEC 3.1 with no CHANGELOG line at all (FD2-7).

## Manual-verify additions

- [ ] FD2-1: `kill -STOP` the daemon, open Settings, type into Bind host and into Keep newest sessions, `kill -CONT`: what survives, and whether Close then warns about unsaved changes.
- [ ] FD2-2: the same, but let the 4 s deadline expire while typing: where the caret ends up.
- [ ] FD2-3: daemon stalled on `POST /ports`, attach, Cancel, `+ Attach`: whether the Attach button looks pressable while the list still says `loading devices...`, and whether pressing it says anything.
- [ ] `devSel` carries `autofocus` (`index.html:140`) and now opens holding only the `loading devices...` option. Confirm what `showModal` focuses, that type-ahead into it before the real list lands does nothing surprising, and that replacing the options fires no `change` (so the alias default is not re-derived from a device the user never picked). The stub's `<select>` keeps any value and cannot show any of this.
- [ ] The Settings and Attach dialogs are `showModal()`, so a second click on the gear or `+ Attach` is unreachable behind the backdrop. Confirm that in a real browser, since the `openGen` / `devicesGen` guards are now the only thing standing behind an assumption the stub cannot check.
- [ ] FD2-7: with a token set and a wrong one in the browser, watch the `/ws` handshake in DevTools: it must be an HTTP 403 reported as close 1006, and the page must recover through the `/status` 401 prompt within one reconnect backoff.

## The two questions

1. **Least confident, rechecked.** FD2-7's premise: that a browser can never see close 1008 for an auth refusal, which is what makes the `handleWsAuthClose` deletion safe. `_deny` sends 1008 in the ASGI scope and the only test of it asserts 1008, so the 403 in SPEC rests entirely on uvicorn translating a pre-accept close. I could not drive it in this leg (no daemon, no browser), and I have filed it rather than counted it done.
   Rechecked and cleared instead: the `refreshStatus(fresh)` chain, by reading which promise `statusInFlight` actually holds (the `.finally()` one, so the chained poll always starts after it is nulled) and by re-running `followups_status_refresh.test.mjs` alone; and the `putChain` revision question, by confirming `revision` is read inside `putConfigNow` rather than captured at queue time.
2. **What should have been checked.** The "open at once, fill later" change was reviewed as a focus fix and not as a new editable surface. Everything the loading dialog leaves live is unreviewed ground: FD2-1 (the fields), FD2-2 (the deferred focus), FD2-3 (a button another generation's `finally` unholds). The same question applies to the export dialog, whose `openExportDialog` resets `expGo.disabled` but not the session select's own loading state.
   Second gap: the attach dialog's `devSel` under real `<select>` semantics. The round replaced its contents with a placeholder and back, and every assertion about it runs on a stub select that keeps any value, so the drop-a-value-no-option-carries case is untested on the one control the round changed the option set of.
