# Fix batch: web UI chrome (fix-diff round 2)

FD2-1 to FD2-6 from `fixdiff2-webui-chrome.md` and PD-1, PD-2, PD-6 from `fixdiff2-webui-panes.md` (all three in `api.js`).
Base HEAD 5489d6e. Source copies for the reverts: `~/tt-data/prerelease-2026-09-15/fix-fixdiff2-chrome/`.

JS suite after the batch: 808 tests, 807 pass, 1 fail - `sweep_chrome_settings.test.mjs:127`, which pins the text FD2-4 removes and is outside this batch's file list (see "Not done").
Every test file this batch touched passes run alone, and every test in them passes alone under `--test-name-pattern`.

## Per item

### FD2-1: the loading Settings dialog held every field it owns

- `settings.js:94-108`: `DAEMON_FIELDS` beside `DAEMON_CONTROLS` (the ten inputs and checkboxes the daemon's saves write), disabled by `setReadOnly` with the six buttons, plus a pass over the rendered port rows (`input`, `select`, `button` under `cfgPortsBody`).
  The token section is deliberately not in it: it is browser-side, and the read-only state exists to let it be used.
- SPEC 9.1: the "loading..." sentence now says the fields it writes are disabled too, with one clause saying why; the unreachable-daemon sentence says the same.
- Test `fixdiff2_chrome_settings.test.mjs`, two tests: every field disabled while `/config` is held with `cfgToken` still free, the answer then writing the file's value over what the stub let through and marking the section clean, and the positive control of the same typing after the answer reading `Save *`. The second test holds a reopen's port row (`baudInput`, `eolSel`) and shows the answer re-rendering them free.
- Revert: field loop removed, test 1 fails on `cfgHost was editable while the file was still loading`; port-row loop removed, test 2 fails on `a port row was editable while the file was loading`.

### FD2-2: the deferred failure branch no longer moves focus

- `settings.js:724`: `if (!dlg.contains(document.activeElement)) $("cfgToken").focus();`.
- `dom_stub.mjs:73,183-188,211-216`: `FakeEl.contains`, `focus()`/`blur()` tracking, and `document.activeElement` defaulting to `body`. Nothing in the tree read either before, so the addition is additive.
- Test `fixdiff2_chrome_settings.test.mjs`: with the user on the dialog's Close button the unreachable answer records no `focus()` on the token box and leaves `activeElement` alone; the positive control (focus outside the dialog) shows the same answer focusing it.
- Revert: guard removed, the first test fails on `the late answer pulled the caret to the token box`.

### FD2-3: `submitAttach`'s `finally` is generation-checked

- `statusbar.js:713`: `if (gen === attachGen) btn.disabled = devicesLoading;`.
- Test: `followups_dialogs.test.mjs` test 3 extended, as the report suggested - the button is asserted held after the earlier opening's attach settles, and the positive control presses Attach once the reopened list lands and sees the second POST.
- Revert: `btn.disabled = false`, the test fails on `the old attach re-enabled the button the reopen is deliberately holding`.

### FD2-4: the doubled reload instruction dropped

- `settings.js:596-605`: the 409 suffix is gone and `putConfigNow`'s now-empty try/catch with it; one comment says the daemon's sentence carries the instruction.
- SPEC 9.1: `A 409 shows the daemon's error as it is written (it already says to reload)`.
- Test: `rulings_chrome_settings.test.mjs` no longer holds a `HINT` constant; its three 409 assertions are exact equality against the daemon's own text.
- Revert: suffix re-appended, three tests in that file fail.

### FD2-5: the session reference is encoded into the preflight query

- `state.js:341-344`: `encodeURIComponent(db[1])`.
- Test `fixdiff2_chrome_export.test.mjs`: a session named `run&a#b` produces exactly `/sessions?name=run%26a%23b` and still navigates to the export path; a numeric id is unchanged, and a session deleted meanwhile is reported as `no such session: run&a#b`.
- Revert: the raw interpolation fails on `the name ended the parameter early: the daemon was asked about another session`.

### FD2-6: the backfill clear comment states the window instead of denying it

- `api.js:487-491`: the comment now says the boolean covers the whole backfill, that the daemon freezes the first page's `id_to` when it processes the request (which can be after the click), and that later pages walk backwards so only the first page can hold post-click rows.
- Comment only, as decided; no behaviour and no test.

### PD-1: a cleared backfill still hands over the stream definitions

- `api.js:476-480` `isPlotDef(row)`, used at `:548` as `if (!chartsCleared || isPlotDef(row)) plotIngest(row);`, with the reason at the call site (the first branch of `plotIngest` primes a cache no clear drops, and `seedPlotDefs` only fetches from below the window).
- Test `fixdiff2_chrome_backfill.test.mjs`: clear-all during the backfill leaves `charts.size` 0 for the backfill's own samples, then two live `!ps` rows on the stream announced inside that backfill chart. The control test above it drives the no-clear case.
  Each test takes its own port: `plotDefs` is cached for the life of the module, so a definition an earlier test taught it would answer for the next one and the file would pass with the fix reverted.
- Revert: gate restored to `if (!chartsCleared)`, the test fails on `the stream's definition went with the cleared rows: every later !ps is undecodable`.

### PD-2: the post-backfill pass walks the live pane array

- `api.js:553-555`: `for (const p of panes) if (p.clearGen !== (paneClears.get(p) ?? 0)) ...`, with the snapshot still taken before the fetch for the panes that existed.
- Test: a pane created while the backfill is out and then cleared stays empty; a second pane created in the same window and not cleared takes the rows (positive control).
- Revert: iterating `paneClears` again fails on `the clear on a pane born mid-backfill was lost`.

### PD-6: `resetForDbReset` bumps every pane's `clearGen`

- `api.js:174-176`: `p.clearId = 0; p.clearGen += 1;` with one line on why (a backfill across a reset must read all three surfaces as cleared, not two of three).
- Test: a capture reset moves every pane's `clearGen` as it moves `canClearGen()` and `plotSeedGen()`; the first capture id seen is not a reset (positive control).
- Revert: the bump removed, the test fails on `pane 0: a reset left its clear token behind`.

## CHANGELOG lines

- Web UI: the Settings dialog holds its fields, not just its Save buttons, until the config file has loaded, so a slow daemon cannot overwrite what was typed into one.
- Web UI: a Settings dialog that opened before an unreachable daemon answered no longer moves the caret to the access-token box while the user is typing in it.
- Web UI: Attach stays held while a reopened attach dialog loads its device list, even when an attach from an earlier opening finishes meanwhile.
- Web UI: a refused config save shows the daemon's message once, instead of following it with a second reload instruction in other words.
- Web UI: a session export checks the session by an encoded name, so a name carrying `&` or `#` is no longer read as another session.
- Web UI: clear-all pressed during a backfill no longer discards the stream definitions it carried, which left every later plot sample on those streams undecodable.
- Web UI: a terminal pane created while a backfill is out and then cleared no longer refills with the rows it cleared.

## Not done

- `sweep_chrome_settings.test.mjs:127` (`assert.match(env.byId("cfgServerErr").textContent, /reopen Settings/);`) pins FD2-4's removed suffix and is not in this batch's file list. It is the one JS failure in the tree. The edit is to assert the daemon's own text instead, e.g. `assert.equal(env.byId("cfgServerErr").textContent, "config file changed since it was read; reload it and try again");`.
- `CHANGELOG.md:145` still says a 409 comes "with a hint to reopen Settings" (FD2-4). Owned by the orchestrator.
- FD2-7 belongs to the daemon batch, and PD-3, PD-4, PD-5, PD-7 to the panes batch.
- FD2-2 changes what a real browser does in a way the stub cannot show: with the fields now disabled (FD2-1), `showModal` autofocus lands on some control inside the dialog, so the unreachable branch will rarely focus the token box at all. One manual-verify line is owed (below).

## Manual-verify additions

- [ ] FD2-1/FD2-2 together: `kill -STOP` the daemon, click the gear, and check what `showModal` focuses now that the fields are disabled; then let the 4 s deadline expire and confirm the token box is reachable (by Tab, if it is no longer focused automatically).
- [ ] FD2-1: the same stalled daemon, then `kill -CONT`: the fields fill from the file, no section reads `Save *`, and Close asks nothing.
- [ ] FD2-3: daemon stalled on `POST /ports`, attach, Cancel, `+ Attach`: Attach reads disabled while the list says `loading devices...`, and becomes pressable when the list lands.

## The two questions

1. **Least confident, rechecked.** That PD-1's test can fail with the fix reverted. It could not at first: `plotDefs` is never dropped, so the definition cached by the control test above satisfied the reverted gate and the file passed. I found it by running the revert rather than trusting the probe (which was reported run alone), and gave each test its own port; the revert then failed as it should. The same trap applies to any future test in that file.
   Still reasoned rather than driven: FD2-6 (comment only, and the daemon-side `id_to` freeze is not drivable from the JS stub), and what a real browser focuses in the Settings dialog once the fields are disabled, which is the manual-verify line above.
2. **What should we have checked that we have not thought about.** The loading state was fixed for the fields and left alone for the *rendered* rows: the port rows are covered here, but the sessions section keeps the previous open's rows on screen during a reopen, and their Export and Delete buttons are live the whole time. Delete is a real request against a real session, not a lost edit, so it is not FD2-1's shape, but nobody has asked whether it should be pressable while the dialog says "loading...". The export dialog has the same unreviewed surface: `openExportDialog` resets `expGo.disabled` but not its session select's loading state (already noted by the fix-diff report).
   Second: `readOnly` now gates both the dirty marking and the disabled flags, so any future control added to a daemon section has to be added to `DAEMON_FIELDS` by hand or it silently stays live. Nothing derives the list from `index.html`, where `rulings_chrome_settings.test.mjs` does exactly that for the Save buttons.
