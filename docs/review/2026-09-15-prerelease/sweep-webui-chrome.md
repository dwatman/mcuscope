# Registry sweep: web UI chrome (classes 70, 71, 72, 73, 74, 76)

Files: `state.js`, `settings.js`, `statusbar.js`, `exportdlg.js`, `api.js`, `cmdbar.js`, `app.js`, `index.html`, `style.css` (in `host/mcuscope/webui/`).
Base a259062 plus the concurrent round's uncommitted tree.
Node suite after the fixes: `uv run python -m pytest tests/test_webui_js.py -q` 2 passed (35 new node tests, whole suite green).

Line numbers are the tree after the fixes.

## Class 70: one status code for several causes

Command (registry): `grep -n "status_code ==\|status ===\|\.code ==" <owned js>`: 5 sites.
Widened to `!r.ok` (the same primitive keyed on "any refusal"): 3 more, 8 in all.
Server emitters read from `server.py`: 401 at 778 (token guard only); 409 at 1077 (`ConfigConflict` only, the five `/config/*` saves); WS 1008 at 634 (Host/Origin guard, pre-accept), 772 (token guard, pre-accept), 1966 (`?port=` naming no port, post-accept).

- `state.js:81` `r.status === 401` prompts for a token: complies, 401 has one cause (token middleware).
- `settings.js:585` `e.status === 409` adds the reopen hint: complies, 409 is only `ConfigConflict` (revision mismatch).
- `api.js:622` WS `ev.code === 1008` prompts for a token: violates, owner should pick (O1).
  - Three server causes emit 1008, one of them not auth.
  - Driven: a pre-accept `websocket.close` reaches the client as an HTTP 403 handshake refusal under uvicorn 0.52 (`InvalidStatus 403`, script `~/tt-data/sweep-chrome/ws_probe.py`); all three uvicorn WS implementations do the same.
  - A browser therefore sees close 1006 for the token and Host/Origin refusals: the branch never fires for auth.
  - The only 1008 a browser can receive is `no such port`, which the UI never provokes (no `?port=`), and which the branch would misread as auth.
  - Recovery works today through the `/status` poll's 401 prompt within 5 s.
- `cmdbar.js:219`, `:221` `r.status === "ok"` / `"err"`: exempt because it is the `/cmd` body field, whose values are ok/err/timeout (`serial_link.py:1202-1235`), not an HTTP status.
- `state.js:101` `api()` `!r.ok`: complies, the message is the body's `error`, and `err.status` is carried for keyed callers.
- `state.js:343` preflight `!r.ok`, `state.js:381` token download `!r.ok`: complies, both report `refusalText` (body `error`, else `HTTP <status>`).

Fixes: none (O1). Hand-off: SPEC 3.4 (line 881) and 3.1 (line 358) say the handshake refusal is "close 1008"; a client sees HTTP 403. That text and `server.py` belong to the daemon side.

## Class 71: rendered coarser than stored, saved back

Command (registry): `grep -n "Math.round\|MiB\|/ 1000\|\* 1000" settings.js`: 3 lines.
Widened to every field a whole-section save sends from a rendered value (all of `settings.js`), and to rounding in the other owned files (`Math.floor|ceil|toFixed|1024|1000|intField(`): 30 lines.

Registry hits:

- `settings.js:138` comment: exempt because it is a comment.
- `settings.js:146` `max_db_bytes` shown in whole MiB: complies, `capShown` sends the loaded bytes while the field reads the rendered MB; the reopen race on `capShown` is fixed under class 73.
- `settings.js:307` `fmtWhen` `ts * 1000`: exempt because display only, never saved.

Every field a Settings save sends (widened):

- `cfgHost`, `cfgPort`, `cfgRetention`, `cfgMinSessions`, baud per row: complies, rendered raw and parsed by `intField`/`trim`, exact for any value the loader accepts.
- `cfgDbPath` trimmed on save: complies, `config.py:146` strips `db_path` when resolving it, so the trim changes no meaning.
- `cfgAutoSession` (`!== false`), `cfgUpdateCheck` (`!cfg.update || check !== false`): complies, absent reads as the daemon default and is saved as that default.
- Ports row `eol` (`isEol ? eol : lf`), `identify` (`!== false`), device select/custom, `serial_number` trim: complies, the loader already normalises eol, `identify` absent defaults true, device values round-trip through the custom box.
- PlotJuggler `dest` trimmed: complies, the field shows the daemon's normalised dest.
- `saveAttachedPortToConfig` sends `current.ports` from `GET /config` untouched: complies.

Rounding elsewhere (widened; line numbers from the pre-fix grep file `~/tt-data/sweep-chrome/grep71_74.txt`):

- `exportdlg.js:31-41` clock bounds via `datetime-local`: complies, `index.html:321,323` carry `step="1"`, and stored bounds only ever come from `toEpoch` of that field, so they round-trip (including the DST repeated hour, both directions take the first occurrence).
- `cmdbar.js:167` latency `toFixed(1)`, `:204-210` timeout fallback: exempt because display, or a field not loaded from anything saved.
- `statusbar.js:12-15` uptime, `:64-68` `fmtBytes`, `:263,268` rate, `:634` baud select: exempt because display only, or the attach dialog, which loads no saved value.
- `app.js:164` CAN cap percent rounded to 0.1: exempt because the dragged value is what is saved; nothing renders it back into a control.
- `api.js:43,90,222,303,304,381-392,544`: exempt because constants, rates and query bounds, no field.
- `state.js:179` `intField`, `:317` comment: exempt (parser, comment).

Fixes: none.

## Class 72: a background refresh that moves focus

Command (registry): `grep -n "\.focus()" <owned js>`: 5 sites.
Widened to `select()`, `setSelectionRange`, `scrollIntoView`, `autofocus` (taken by `showModal`), synthetic `.click()`, and to disabling or removing a focused control from a poll: 11 grep lines plus 5 widened sites, 16 in all.

- `exportdlg.js:231` `.focus()` in `openExportDialog`: complies; callers are the four export buttons' click handlers (`terminal.js:691`, `plots.js:660`, `digital.js:350`, `can.js:673`).
- `settings.js:697` `cfgToken.focus()` in `openSettings`: complies nominally (settingsBtn click only); see O2 for the up-to-4 s delay.
- `app.js:81`, `:87`: complies, collapse/reopen click handlers.
- `cmdbar.js:137` `cmdInput.focus()`: complies, only when `remember` (mode button click); the poll path (`syncCmdMode`) passes false.
- `cmdbar.js:243`, `:251` `setSelectionRange`: complies, ArrowUp/Down keydown only, caret in the focused input.
- `statusbar.js:209` `sesName.select()`: complies, sessionBtn click only.
- `index.html:190` `sesName autofocus`: complies, `openSessionDialog` is synchronous from a click.
- `index.html:140` `devSel autofocus`: complies nominally (attachBtn click), but shown after `populateDevices` (up to 4 s): O2.
- `index.html:221` `cfgHost autofocus`: complies nominally, shown after the open's GETs (up to 4 s): O2.
- `state.js:315`, `:377` synthetic anchor `.click()`: exempt because a detached anchor's click moves no focus.
- Widened: `cmdbar.js:70`, `:74` a poll reporting no ports disables `cmdInput` and `markerBtn`, blurring them if focused: exempt because SPEC 9.1 (line 1550) documents the disable, and no document-level key handler exists, so stray keys reach nothing.
- Widened: `statusbar.js:306` chip rebuild on a `portsSig` change removes a focused chip button: exempt because it happens only when that chip's displayed state changed, and focus falls to the body, where no key handler acts.
- Widened: `statusbar.js` `renderUpdateBadge` hiding the badge from a poll: exempt, only when the daemon's update answer changes; nothing typed is redirected.
- Widened: `closeAttach`/`closeDlg` after an await restores focus to the opener: complies after the class 73 fix, which no longer closes a reopened dialog.
- Widened: `cmdbar.js:52` `populateCmdPort` rebuilding `cmdPort`'s options from a poll: exempt, it does not blur the select and runs only when the alias set or offline state changes.

Fixes: none. Owner decision O2.

## Class 73: an awaited result written into a replaced view

Command (registry): `grep -n "await " <owned js>`, then every function read: 65 awaits before the fixes (state 12, settings 27, statusbar 9, exportdlg 4, api 11, cmdbar 3, app 0), in 36 functions.
After the fixes settings has 24 (four save handlers share `saveSection`).

state.js:

- `authFetch:72`, `api:91`, `refusalText:110`, `preflight:336`: complies, read no view state.
- `downloadPath:367`: complies, `wanted()` after each await; the Settings row buttons pass none, exempt because a row download has no cancel control.

settings.js:

- `refreshConfig:28`: complies for `renderSaved`; its caller `openSettings` violated (below).
- `loadDevices:38` / `openSettings:677`: violated, fixed: an overlapping open's config or devices landing late became `cfg`/`devicesCache`; the open now adopts its own answers after its generation check.
- `renderDbNow:159`: violated, fixed: an older `/status` read overwrote the cap hint and warnings; `dbNowGen`, also bumped by the read-only open.
- `renderPj:205`: violated, fixed: the value guard missed two toggles returning the checkbox to its old value (ABA); `pjGen`.
- `applyPj:222`: violated, fixed: a superseded apply's refusal and its re-sync GET wrote over a newer apply; `pjGen`.
  - Echo writes keep only the value guards: no ordering makes a generation check there observable.
- `savePjDefault:250`: violated, fixed: a refusal landing after a reopen was written there; `openGen`.
- `renderUpdateNow:278`: violated, fixed: an older read overwrote "checks are off" after checks were saved off; `updateNowGen`.
- `download` closure `:338`: complies, holds its button; the download itself is user-requested.
- `deleteSession:362`: violated, fixed: a refusal landing after a reopen was written there; `sessionsGen`.
- `renderSessions:377`: complies, `sessionsGen`.
- `putConfig:575`: violated, fixed: a save landing after a reopen adopted its revision, so the reopened dialog's older fields could overwrite that save unrefused; adopts only when `openGen` is unchanged.
- `saveServer:619`, `saveStorage:629`, `saveUpdateCheck:657`, `savePorts:664` (via `saveSection:596`, formerly `renderSaved`): violated, fixed.
  - A reopen during the PUT got the late re-render, error, dirty mark and `capShown`; now nothing is written there.
  - Fields typed during the PUT were overwritten by the re-render and marked clean, so closing dropped them unasked; they are now kept, dirty against what was sent.
- `saveAttachedPortToConfig:746`: complies, no view state; the revision it sends guards the read-modify-write.
- `openSettings:677` itself: complies, `openGen`.

statusbar.js:

- `toggleSession:187`, `startSession:213`, `reconnectPort:517`, `holdPort:528`, `detachPort:539`: violated, fixed.
  - A late success cleared a failure strip written after the action started; `failGen` (bumped by `flashDaemonError`) and `clearFailureSince`.
- `startSession:213` also: violated, fixed: a refusal was written into a session dialog reopened meanwhile; `sesGen`.
  - A late success still closes a reopened dialog, deliberately: the session it would start is now running.
- `pollStatus:469`: complies, one poll in flight, reads no view state (see question 2 for the post-action freshness gap).
- `openAttach:555`, `populateDevices:594`: complies, `devicesGen`.
- `submitAttach:654`: violated, fixed: "save to config" was read after the await from a reopened (reset) dialog, and a late answer closed or wrote into it; read before the await, `attachGen`.

exportdlg.js:

- `fillSessions:145`: complies, `fillGen`.
- `doExport:241`: complies, `ctx === mine`.
- `exportNow:250`: violated, fixed (hand-off from the panes sweep): after a capture reset the open dialog still sent the old capture's `id_to`, window and session.
  - `state.captureGen` (bumped by `api.js resetForDbReset`) is recorded at open; Export refuses inline when it moved.

api.js:

- `seedPlotDefs:237`: complies, `wsGen`; definitions are kept across clears by design.
- `seedChannelList:312`: complies, no view state.
- `seedPlotHistory:340`: complies, `wsGen` and `plotSeedGen`.
- `fetchSince:438`: complies, `wsGen` per page.
- `runBackfill:476`: violates, owner should pick (O3).
  - Driven (`~/tt-data/sweep-chrome/probe_clear_backfill.mjs`): a pane clear and CAN clear during the first-load backfill, then the backfill lands: pane rows `[1, 2]` and one CAN row come back.
  - `wsGen` covers reconnects; nothing covers a user clear.
  - A capture reset during it is serialised by the staging queue (the token is staged until the drain), so that case complies.

cmdbar.js:

- `submitCmd:177`: complies, `cmdGen`.
- `submitMarker:254`: violated, fixed.
  - The input was cleared after the POST, wiping a label typed meanwhile.
  - The ack wrote the strip over a newer command's result or a newer marker's ack.
  - `markerGen` plus the `cmdGen` in force; a marker does not bump `cmdGen`, so a pending command still shows its verdict.

Tests: `sweep_chrome_settings.test.mjs` (17), `sweep_chrome_statusbar.test.mjs` (10), `sweep_chrome_cmdbar.test.mjs` (5), `sweep_chrome_export.test.mjs` (3).
Each holds one request, replaces the view in the order that goes wrong, releases, and asserts on text unique to the path.

## Class 74: a limit shown beside a figure it is not measured against

Command (registry): `grep -n "max\|cap\|limit" statusbar.js settings.js`: 42 lines.
Widened to the other owned JS and `index.html` (`max|cap|limit|MAX`, less CSS `max-width/height`): 157 lines, 199 in all.
Output kept in `~/tt-data/sweep-chrome/grep71_74.txt`.

Displayed limits, each ruled:

- `statusbar.js:238,246,86` `db <content> / <cap>`: complies, content beside `db_max_bytes`, which SPEC 3.4 enforces against `db_content_bytes`; disk size only as the aside.
- `settings.js:168` cap hint `now <content>`: complies, same figure.
- `settings.js:642` `Size cap must be 0-4194304 MB`: complies, `ConfigStorageBody.max_db_bytes le=1<<42`; the 1 MiB floor (`MIN_DB_CAP_BYTES`) is below any whole-MB value above 0.
- `settings.js:635` retention `1-3650`, `index.html:233` min/max: complies, `ConfigStorageBody.retention_days ge=1 le=3650`.
- `settings.js:647` sessions `0-1000`, `index.html:235`: complies, `min_sessions ge=0 le=1000`.
- `settings.js:625` port `1-65535`, `index.html:222`: complies, `ConfigServerBody.port`.
- `settings.js:557`, `statusbar.js:663` baud `1-100000000`: complies, `MAX_BAUD` (`config.py:288`) on `ConfigPortEntry` and `PortAttach`.
- `index.html:190` `sesName maxlength=128`, `:195` `sesNote maxlength=1024`: complies, `SessionBody` 128/1024; the browser counts UTF-16 units, never more than the daemon's code points.
- `index.html:60` `paneCount` "panes open / maximum": complies, both figures are client-side (filled by terminal.js).
- `cmdbar.js:209` timeout above `MAX_TIMEOUT_MS`: exempt because no limit is displayed (the field falls back to 1000 visibly).

All other lines: exempt because not a displayed limit (line numbers from the pre-fix grep file).

- Comments: settings 136,137,139,156,157,607,692; statusbar 60,220-222,379,409; api 11,36,127,134,143-146,170-172,178-179,187-196,208,217,221,234,256,279-298,381-394,411-424,434-435,446,464,472-481,513-514,535,547,604,608,641,648,656-666; app 51,64,163; state 13,140,149-151,165,178,197-198,266,290,317-318.
- Code with no displayed figure: settings 141,146-148,158,171,318,331,336,349,368,608-613,621,624; statusbar 12,76,164,233,238,385,404; cmdbar 2,13,27,32,110,182,205; api 1,7,137,152-168,174,209,212,240,243,292,303-304,340,357,392,396,417,425,429-430,443-444,455,477,485,489,520,545,644,650,697,713; exportdlg 148-149,158; app 68,120,155,162,164,170; state 27,51,122,128,141-143,152,161,201-202,247,263,268,464; index 34,82,87,191,234,273.

Fixes: none.

## Class 76: a view cache or rebuild key missing an input

Command (registry): `grep -n "Version\b\|View\b\|needsRebuild\|!==.*prev" <owned js>`: 4 lines (`app.js:38,47` `setView`, `statusbar.js:102,127` `dismissedVersion`), none a memo: exempt because the name matches, the code is not a cache.
The grep misses every memo in these files, so widened to `Sig`, `shown*`, `cache`/`Cache`, `===`-guarded early returns: 10 memos.

- `statusbar.js:306` `portsSig`: complies.
  - The build reads alias, device, resolved_device, description, baud, connected, held, disconnect_reason, rx_dropped, write_failures, last_write_error, target, known, writeErrors, writerDead: all in the key.
  - The rate cell is deliberately outside it, written by `renderPortRates` through `rateCells`.
- `api.js:59` `renderRate` (`shownRate`, `shownHigh`): complies, the build reads `lineRate` and `highRate` only.
- `api.js:17` `setStreamOnline` early return: complies, the build reads `online` only; `setAuthFailed` writes its own text around it on purpose.
- `api.js:154` `noteCapture` `id === captureId`: complies, key is the capture id.
- `state.js:157` `portColorCache`: complies, key alias; the build reads the alias and a constant palette.
- `state.js:213` `lineTick` `row.__tick`: exempt because the omitted input (the `!pd` definition behind `plotSampleTick`) only gates decodability.
  - The tick token of a sample once decoded does not depend on it, and a null is never cached (`:216`).
- `settings.js:69` `isDirty`/`cleanAs`: complies, the snapshot reads every field its section's save sends (checked against `collectPorts` and each save body).
- `cmdbar.js:100` `syncCmdMode` (`mode !== cmdMode`; auto label `textContent !== label`): complies, `setCmdMode` builds from `mode` only, the label is its own key.
- `cmdbar.js:46` `setCmdOffline` early return: complies; `populateCmdPort` is also driven by `setKnownPorts` when aliases change, so each input it reads has a trigger.
- `statusbar.js:58` `setActionError` / `cmdbar.js:153` `setResultHidden` hidden-state guards: complies, the guarded work (resize redraw) depends only on the hidden flag.

Fixes: none.

## Revert table

Script `~/tt-data/sweep-chrome/mutate.py`: checks every anchor before any write, copies each source, mutates one branch, runs its test file, restores from the copy and byte-compares.
All 41 rows fail; two rows first survived and were fixed by strengthening the tests (noted).

| Branch | Mutation | Fails |
|---|---|---|
| settings open adopts its own devices | cache set inside `loadDevices` | overlapping open (first survived: release order; test fixed) |
| settings open adopts its own cfg | removed | overlapping open |
| `renderDbNow` success gen | removed | cap hint newest; read-only warnings |
| `renderDbNow` catch gen | removed | older failed read |
| read-only open bumps `dbNowGen` | removed | read-only warnings |
| `renderPj` success gen | removed | GET after two toggles |
| `renderPj` catch gen | removed | GET failing after a toggle |
| `applyPj` superseded refusal | removed | refused toggle superseded |
| `applyPj` re-sync gen | removed | re-sync after refusal |
| `savePjDefault` error gen | removed | PJ default refused after reopen |
| `renderUpdateNow` success gen | removed | older read over "checks are off" |
| `renderUpdateNow` catch gen | removed | older failed read |
| `deleteSession` error gen | removed | delete refused after reopen |
| `putConfig` revision only in its dialog | always adopt | save after reopen adopts no revision |
| `saveSection` `onSaved` gen | always | storage cap bytes after reopen |
| `saveSection` no write after reopen | removed | save after reopen (first survived: dirty mark not asserted; test fixed) |
| `saveSection` typed-during-save check | `if (fresh)` | port typed while saving |
| `saveSection` dirty against sent | `markClean` | port typed; re-read failing |
| `saveSection` error gen | removed | server save refused after reopen |
| `flashDaemonError` bumps `failGen` | removed | all 5 strip tests |
| stop / start / reconnect / disconnect / detach clear since | `setActionError("")` (5 rows) | that action's strip test (each) |
| `openSessionDialog` bumps `sesGen` | removed | session start refused after reopen |
| session start error gen | removed | session start refused after reopen |
| `openAttach` bumps `attachGen` | removed | attach after reopen stays open; refused after reopen |
| save-to-config read before await | read after | save to config honoured |
| attach close gen | always close | attach after reopen stays open |
| attach error gen | always write | attach refused after reopen |
| marker keeps typed label | always clear | label typed while out |
| marker ack gen | always show | ack after newer command; older marker |
| marker refusal gen | always show | refusal after newer command |
| `current()` checks `cmdGen` | dropped | ack and refusal after newer command |
| `current()` checks `markerGen` | dropped | older marker's ack |
| marker does not bump `cmdGen` | bumps | pending command keeps its verdict |
| export refuses across capture reset | `if (false)` | reset while waiting; reset before Export |
| `resetForDbReset` bumps `captureGen` | removed | reset while waiting; reset before Export |
| dialog open records its capture | removed | dialog opened after the reset exports |

## SPEC and CHANGELOG

- `docs/SPEC.md` 9.1 export dialog: the inline refusal after a capture reset.
- `CHANGELOG.md` `[Unreleased]` Fixed: the export refusal, and one grouped entry for the late-answer fixes.

## Hand-offs

- Daemon side (SPEC 3.1 line 358, 3.4 line 881, `server.py:634,772`): the pre-accept "close 1008" is an HTTP 403 handshake refusal on the wire; the SPEC text is wrong for every client, and O1 depends on it.
- Panes agent (`terminal.js`, `can.js`): O3 touches the clear semantics (`clearId`, `clearAllCan`) as well as `api.js runBackfill`.

## Broken existing tests

- None in the node suite.
- `tests/test_regressions.py::test_only_the_documented_commands_emit_jsonl` fails in the shared tree; it walks `cli.py`, which another agent is editing, and touches nothing in these files.

## Manual checks (browser)

- [ ] Token-protected daemon: DevTools shows the `/ws` handshake refused with 403 and the socket closing 1006; the page still prompts for the token (via `/status`) within 5 s.
- [ ] `kill -STOP` the daemon, click Settings, click into the command input and type `i2c scan` and Enter during the 4 s: note where focus and the keys land when the read-only dialog appears (O2).
- [ ] Same with the attach dialog: typed keys and Enter after the late `showModal` (O2).
- [ ] Settings with the daemon briefly stopped: Save a section, type into it, `kill -CONT`: the typing stays, the section reads `Save *`.
- [ ] Same, but close (confirm) and reopen Settings before `kill -CONT`: the reopened dialog is untouched, and its own save of that section answers the 409 reopen hint.
- [ ] Marker posted with the daemon stopped, type a second label, `kill -CONT`: the second label stays in the box.
- [ ] Export dialog open on a paused pane, then a capture reset (`mcu db reset` or deleting the database): Export shows the reset refusal; closing and reopening exports.
- [ ] Keyboard focus on a chip's reconnect button when the port reconnects: focus falls to the page (exempt, recorded).

## Owner decisions

- O1, class 70, WS 1008 (`api.js:622`). Owner should pick:
  - (a) Leave the behaviour, correct the comment and SPEC 3.1/3.4 to "handshake refused (HTTP 403, a browser sees 1006)". Recommended: the `/status` 401 prompt already recovers.
  - (b) On a close before `onopen`, probe `/status` through `authFetch`, so the stream prompts by itself.
  - (c) Daemon accepts then closes 1008 with a reason, so browsers and the CLI see it; changes the wire contract.
- O2, class 72, dialogs shown up to 4 s after their click take focus from wherever the user went.
  - In read-only Settings, Enter in `cfgToken` stores the typed text as the access token; in attach, Enter attaches the selected device.
  - (a) Open the dialog at once in a loading state and fill it when answered.
  - (b) After the await, do not open when focus has moved into a text field since the click.
  - (c) Leave (only with a stalled daemon).
- O3, class 73, a clear during a backfill (`api.js runBackfill`): the backfill's rows and CAN frames reappear after the clear.
  - (a) Leave: the rows arrive "after" the click by id.
  - (b) `runBackfill` notes a clear generation and, if it moved, lands rows in the buffer only, raising each pane's `clearId` past them and skipping CAN and plot ingest.
  - (c) Refuse clear while a backfill runs.

## The two questions

1. Least confident: the `saveSection` rewrite, which now carries four save handlers and three paths.
   - Rechecked by driving, not re-reading: every branch has a failing mutation, and the 84 existing settings tests (revision, re-read failure, cap bytes) pass unchanged.
   - The class 70 1006 claim was reasoned from uvicorn source first; now driven at the ASGI/uvicorn level (403 handshake). The browser half (403 surfaces as 1006) rests on the WHATWG WebSocket spec and is a manual check.
   - O3 was reasoned first; now driven with a probe (rows `[1, 2]` return).
   - Not verified: anything on Windows or in a real browser.
2. Not yet checked:
   - `refreshStatus` shares a poll already in flight, so an action's follow-up refresh can return state from before the action (a detached chip stays up to 5 s). It is the same shape as class 73 (a read that predates the change, written after it). Not fixed: the sharing is a stated design choice; owner should pick between a chained fresh poll for post-action callers and leaving it.
   - A marker ack shown over a pending command arms the 5 s auto-hide, whose `hideResult` bumps `cmdGen` and drops that command's verdict if it takes longer. Pre-existing, unchanged by the fix.
   - Two overlapping section saves in one dialog: the second carries the revision the first has not yet answered, so it gets a 409 of its own making (reasoned, not driven) (the daemon changes only the saved section, but the file revision moved). Owner should pick: queue section saves, or leave (needs two saves inside one PUT's round trip).
