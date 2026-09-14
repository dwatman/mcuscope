# Leg E: web UI chrome and wire (pre-release, v0.4.0..fdd30a2)

Stack: `mcuscoped --sim --config ~/tt-data/prerelease-2026-09-15/E/cfg.toml` on 127.0.0.1:18615, own db, stopped by PID after the leg.
Captured responses: `resp_status.json`, `resp_ports.json`, `resp_devices.json`, `resp_config.json`, `resp_plotjuggler.json`, `resp_sessions_limit_200.json`, `resp_plot_channels.json` in that directory.
Repros: `e1..e10_*.test.mjs` there, each `node --test <file>`; the ones marked "real daemon" need the stack running.

Every field the chrome files read from `/status`, `/ports`, `/devices`, `/config`, `/plotjuggler`, `/sessions`, `/plot/channels`, `/cmd`, `/send`, `/marker` is present in the real response and named in SPEC 3.3.1 / 3.4.
Every `$("id")` in `webui/*.js` resolves to an id in `index.html` or one the JS creates (0 missing); every class queried in the pane template exists.

## Findings

### E-1 MED: an unrelated Storage save rewrites a size cap that is not a whole MiB
- Where: `host/mcuscope/webui/settings.js:143-144` (render in rounded MB), `settings.js:545-550` (save as `MB * n`).
- Defect: the cap is rendered as `Math.max(1, Math.round(bytes / MiB))` and every Storage save sends that back as bytes, so the saved cap changes without being edited.
- Scenario: config `max_db_bytes = 1500000` (hand edit, SPEC allows any value >= 1048576) -> open Settings, change only retention to 11, Save -> `/config` now holds `max_db_bytes: 1048576`, the live cap drops 30% and the next sweep trims (my scratch capture lost 43428 lines to it). `1600000` goes up to 2097152 instead.
  - The dirty tracker compares the MB text, so the section never showed the cap as edited.
- DRIVEN, real daemon: `e1_storage_cap_roundtrip.test.mjs` -> `expected 1500000, actual 1048576`.
- Class: 45 (a whole-section PUT drops what it does not render faithfully); new class candidate: "a field rendered in coarser units than stored is saved back rounded".
- Fix: remember the rendered MB and the loaded bytes; send the loaded bytes when the field still reads what was rendered.

### E-2 LOW: a save whose follow-up GET /config fails re-renders the pre-save config as clean
- Where: `settings.js:526-527`, `561-562`, `577-578`, `594-595` (`await refreshConfig(); render*()`), `refreshConfig` at `settings.js:28-36`.
- Defect: `refreshConfig` returns null on failure but keeps the stale `cfg`; every save handler ignores the null and re-renders that stale `cfg`, marking the section clean.
- Scenario: edit port `keep` baud 921600 -> 57600, Save; the PUT lands, the daemon goes away before the refresh -> daemon file has 57600, dialog shows 921600 with a plain `Save` and no error; the next Ports save writes 921600 back.
  - Same site shape in `saveAttachedPortToConfig` (`settings.js:677`): a failed refresh after a successful PUT flashes "save to config failed".
- DRIVEN, real daemon for the PUT, refresh made to fail: `e2_ports_save_refresh_fail.test.mjs` -> `daemon saved baud 57600 | dialog shows 921600 | save button: Save | err: ""`.
- Class: 12 (fetch helper returning something the caller renders as current).
- Fix: on a null refresh, leave the fields as typed and say "saved; could not re-read the config".

### E-3 MED: the export dialog closes as a success on a URL the daemon refuses
- Where: `host/mcuscope/webui/state.js:330-336` (token-less navigation), `exportdlg.js:253-255`, `exportdlg.js:192` (deadband passed through), `terminal.js:574` (pane regex passed through).
- Defect: the navigation branch cannot see a refusal and relies on "the dialog's own guards" (comment at `state.js:324-326`), but the free-text `deadband` field and the pane's `match` have no client guard; the refusal reason is never shown and the dialog closes.
- Scenario, three inputs, each answered 400 by the real daemon:
  - Plot export, changes on, deadband `ftest:0.5` -> `/plot/export?...&deadband=ftest%3A0.5` -> `400 {"error":"deadband needs name=value: ftest:0.5"}`, no Content-Disposition; dialog closed, `expErr` empty.
  - Deadband `nosuch=1` -> `400 deadband names no exported channel`.
  - A pane regex valid in JS but not in Python `regex`: `[^]`, `[]`, `\cJ` -> `400 bad match regex`. `terminal.js:497` (history paging) already retries without `match` on exactly this refusal; the export sibling at `:574` sends it regardless, gated on `regexSrc` where history gates on the compiled `regex` (class 54 divergence).
- DRIVEN: `e3_export_deadband_nav.test.mjs` (real exportdlg.js, real daemon) -> `navigations: [/plot/export?...deadband=ftest%3A0.5] | dialog open: false | expErr: ""`, then `daemon answers 400`. Regex cases driven by curl. Whether a browser then saves the JSON as `plot.csv` or shows a failed download varies by browser and needs a real one; either way the reason is lost.
- Class: 27/55 shape (a refusal path keyed on the wrong property: "guards exist" rather than "every refusable input is guarded"); see Decisions.
- Fix: preflight with `authFetch` and abort the body once headers show `ok`, then navigate; or mirror `_parse_deadband` and drop `match` the way history does.

### E-4 MED: a /status poll moves keyboard focus into the command input
- Where: `host/mcuscope/webui/cmdbar.js:125` (`setCmdMode` focuses `cmdInput`), reached from `syncCmdMode` (`cmdbar.js:91`) on every poll (`statusbar.js:487`) and every port-list change (`terminal.js:608`).
- Defect: a mode change the user did not make (a board first answering `OK monitor`, or a second port making `auto` ambiguous) calls `focus()` from a background poll.
- Scenario: user typing `reset done` in the marker box or a pane regex; the board answers `OK monitor` -> the next poll flips raw to cmd and focuses `cmdInput`; the rest of the typing and its Enter land there and are sent to the target as a command.
- DRIVEN for the call (`e4_poll_focus.test.mjs`, real `/status` shape): `prompt after poll 2: > | focus() calls during poll: ["cmdInput"]`. The keystroke consequence is REASONED and needs a real browser (a modal dialog makes the bar inert, so only non-dialog fields are exposed). The two-port trigger is REASONED from `autoAlias`.
- Class: new class candidate "a background refresh moves focus" (neighbour of 61).
- Fix: focus only when `remember` is true (the user clicked cmd/raw).

### E-5 LOW: overlapping list fills duplicate options
- Where: `exportdlg.js:138-158` (`fillSessions`), `statusbar.js:563-589` (`populateDevices`); both clear before the await and append after it.
- Scenario: export dialog opened, `reset range` pressed before `/sessions` answers -> the session select lists every session twice. `+ Attach` clicked twice before `/devices` answers -> simulator and `custom...` each listed twice (then `showModal` runs twice).
- DRIVEN (held fetches, real response bodies): `e5_overlapping_fills.test.mjs` -> `session options: [auto-... (open), auto-... (open)]`, `device options: [socket://127.0.0.1:9900, custom, socket://127.0.0.1:9900, custom]`.
- Class: new class candidate "clear before an await, fill after it" (a generation check is the fix).
- Fix: a per-fill generation token; only the newest fill writes.

### E-6 LOW: Settings and Attach open nothing against a stalled daemon
- Where: `settings.js:607-610` (awaits `/config` and `/devices` before `showModal`), `statusbar.js:536-538` (awaits `/devices` before `showDlg`); neither has a deadline.
- Defect: SPEC 9.1 says "against an unreachable daemon the dialog opens read-only"; that holds for a refused connection only. The export dialog was changed for exactly this in 2026-09-12 (`sessionsReady`), its siblings were not.
- Scenario: daemon accepts and never answers (blocked loop; driven with SIGSTOP) -> clicking the gear or `+ Attach` does nothing for as long as it lasts, including the token field the offline mode exists for.
- DRIVEN, real daemon under `kill -STOP`: `e6_stalled_daemon_dialogs.test.mjs` -> `settings dialog open: false | attach dialog open: false` after 6 s.
- Class: 12.
- Fix: open first and fill after, or race the fetches against the 4 s status deadline.

### E-7 LOW: offline, the chips go but the command bar keeps a resolved port
- Where: `statusbar.js:455-463` clears chips and session but not `state.knownAliases` / `portConnected` / `portTarget`.
- Scenario: daemon unreachable -> chips empty, `daemon unreachable`, while the port select still reads `(sim)`, the input and Marker stay enabled and the prompt stays `>`.
- DRIVEN: `e7_offline_cmdbar.test.mjs` -> `offline: {"chips":[],"daemon":"daemon unreachable","auto":["(sim)","sim"],"inputDisabled":false,...}`.
- Class: 12. Sending still fails visibly in the strip, hence LOW.
- Fix: on the offline branch call `setKnownPorts` with nothing known, or label `auto` as `(offline)`.

### E-8 LOW: Ports Save deletes a port saved elsewhere while the dialog was open
- Where: `settings.js:586-601`; `PUT /config/ports` (`server.py:1223`) replaces the list with no revision check.
- Scenario: Settings open in tab A; tab B attaches with "save to config" (alias `tabB`); tab A edits a baud and saves -> `/config` ports `["keep"]`, `tabB` gone, no error.
  - SPEC 3.3.1 promises that hand edits made while the daemon runs survive a save; a `[[ports]]` entry hand-added while the dialog is open does not.
- DRIVEN, real daemon: `e8_ports_lost_update.test.mjs` -> `aliases after tab A's save: ["keep"] | err: ""`.
- Class: 45 neighbour; new class candidate "a whole-collection PUT from a stale read". See Decisions.

### E-9 LOW: a refused session `.db` export is saved, not reported
- Where: `state.js:310-313` `streamable` keys on the path ending in `/export`, which catches `/sessions/{id}/export`, a bounded temp-file response with its own refusals (`no such session`, `export failed: <exc>`).
- Scenario: Sessions list rendered; the run is deleted in another tab (or the export hits disk full); click `export` -> navigation to `/sessions/2/export` -> `400 {"error":"no such session: 2"}`, `reportError` never called.
- DRIVEN, real daemon: `e9_session_export_nav.test.mjs` -> `navigated to /sessions/2/export -> 400 ... | reported: []`.
- Class: 55 (keyed on a name, `/export`, where the behaviour keys on "unbounded stream").
- Fix: exclude `/sessions/` from `streamable`, as the bundle already is.

### E-10 MED: the status bar and the Settings hint compare file size with the cap
- Where: `statusbar.js:75-77`, `statusbar.js:224-226`, `settings.js:160`: all show `db_size_bytes` beside `db_max_bytes`.
- Defect: SPEC 3.4 says `db_content_bytes` is the figure the cap is enforced against, "and comparing the wrong one makes a working cap read as broken"; the UI does exactly that. Pre-existing in v0.4.0, both sites touched in this diff.
- Scenario (real `/status`): `size 5469632, content 1044480, cap 1048576, trimmed 47922` -> bar warning `db 5.2 MB / 1.0 MB`, Settings hint `0 = no cap; now 5.2 MB` beside a 1 MB cap: the cap is working and reads five times over.
- DRIVEN, real daemon: `e10_cap_figure.test.mjs`.
- Class: 17 neighbour (reported figure is not the enforced one); new class candidate "a limit shown beside a figure it is not measured against".
- Fix: show `db_content_bytes` against the cap (keep the file size in the hover).

### E-11 LOW: accessibility attributes missing on status and error surfaces
- Where: `index.html:176`, `197`, `330` and every `cfg*Err` (inline errors, no `role="alert"`/`aria-live`); `index.html:113` `#cmdResult` (no `role="status"`); `index.html:66` `#resizer` (`role="separator"` focusable with `aria-valuenow` in px but no `aria-valuemin`/`aria-valuemax`, so the implied 0..100 range is exceeded); `index.html:121`, `127` (`#cmdInput`, `#markerInput` named by placeholder only).
- Scenario: a screen reader user presses Attach with a bad baud: the refusal is written to `#dlgErr` and not announced; the command result strip is silent likewise.
- REASONED from markup (grep); AT output needs a real browser.
- Class: none registered.
- Fix: `role="alert"` on the inline error slots, `role="status"` on `#cmdResult`, `aria-valuemin/max` on the resizer, `aria-label` on the two inputs.

### E-12 LOW: the sidebar collapse button does nothing in the narrow layout, and persists
- Where: `style.css:427-428` (`grid-template-columns: 1fr !important`, `.reopen { display: none !important }`), `app.js:72-77`.
- Scenario: window under 860 px -> click `»` -> the `!important` single column keeps the sidebar on screen, `reopenBtn.focus()` targets a `display:none` button, and `layout.hidden = true` is saved, so widening the window later shows the sidebar collapsed.
- REASONED; needs a real browser.
- Class: 59 neighbour (a display rule defeating a state class).
- Fix: hide `#collapseBtn` in the narrow query, or honour `.collapsed` there.

### E-13 LOW: SPEC and a comment say "no session param means the open session"; the daemon exports the whole capture
- Where: `docs/SPEC.md:1574` ("sends no bound and so means the open session"), `host/mcuscope/webui/exportrange.js:11-12`; `server.py:2632-2635` returns no bound for `ref is None`.
- Scenario: `GET /lines/export?format=text` with the auto session open -> `filename="capture_lines_start-end.txt"`, unscoped.
  - Behaviour is still right because `fillSessions` preselects the open run explicitly; the SPEC sentence and the comment are wrong, not the code.
- DRIVEN (curl). Also a stale comment at `server.py:343` ("the settings dialog does not offer eol"; it does, `settings.js:421-429`).
- Class: none; documentation.
- Fix: reword SPEC 9.1 and the comment: reset returns to the open session by preselecting it.

## Sweeps

### Class 12, healthy-while-dead (catch near `await api(`)
Command: `grep -n "await api(\|api(\"" app.js api.js chrome.js cmdbar.js exportdlg.js settings.js state.js statusbar.js layout.js`. 35 sites.

- `cmdbar.js:185` /send, `:205` /cmd: comply (error in the strip; both carry an AbortSignal).
- `cmdbar.js:248` /marker: complies (error in the strip); no deadline, nothing rendered as pending.
- `exportdlg.js:143` /sessions: complies ("whole capture", which is what no param exports).
- `statusbar.js:181` stop, `:208` start, `:496` reconnect, `:506` disconnect, `:516` detach, `:639` attach: comply (flash or inline error).
- `statusbar.js:454` /status: complies for chips, session, db size; violates for the command bar (E-7).
- `statusbar.js:568` /devices: complies on refusal; violates on a stall (E-6).
- `api.js:241`, `:311`, `:319`, `:360`, `:441`, `:482`: comply (seed and backfill failures are non-fatal and logged; backfill failure reported via `reportError`).
- `settings.js:30` refreshConfig: violates (E-2), and on a stall (E-6).
- `settings.js:40` /devices: complies (empty list; saved device shows as custom with its path).
- `settings.js:159` and `:244` /status hints: comply (hint reduced, nothing stale).
- `settings.js:178` GET /plotjuggler: complies (inline error beside the controls).
- `settings.js:191`, `:203` PUT/GET /plotjuggler: comply (checkbox re-synced, error shown).
- `settings.js:215` PUT /config/plotjuggler: complies.
- `settings.js:319` DELETE session, `:334` /sessions: comply (inline error).
- `settings.js:525`, `:557`, `:576`, `:593` section PUTs: comply for the PUT; violate via the refresh after it (E-2).
- `settings.js:667`, `:676`, `:677` save to config: violates at `:677` (failure reported after a successful PUT, E-2).

Kill probe: daemon SIGSTOPped (E-6) and unreachable (E-7); `streamWarn` and `daemon unreachable` both changed state.

### Class 34, wire-named keys
Command: `grep -n "JSON.parse\|= {}\|= Object" host/mcuscope/webui/*.js`. 21 sites.

- `can.js:237` collapsed set: complies (Set, string-filtered).
- `can.js:320` `Object.keys(COL_TITLES)`: exempt, constant keys.
- `chrome.js:24`, `:26` colour store: complies (null prototype, string values checked).
- `cmdbar.js:23` history: complies (array, strings only, capped).
- `freeze.js:71` watermarks: exempt, keys are the four registered surface constants.
- `exportdlg.js:20`, `:50` `values`: exempt, keys are caller-declared option names.
- `api.js:597` WS frame: exempt, not a store.
- `pane.js:15`: exempt, default parameter.
- `layout.js:20` layout: complies (each field type- and range-checked).
- `layout.js:60`, `:62` titles: complies (null prototype, strings cleaned).
- `exportrange.js:31` range: complies (`validate`).
- `exportrange.js:50`: exempt, URLSearchParams.
- `terminal.js:772` termState: complies (not a name-keyed store; `timeMode` checked by `includes`).
- `plots.js:28` PLOT_TYPES: complies (null prototype).
- `statusbar.js:275` DISCONNECT_WHY, `:483` portConnected: comply (null prototype).
- `state.js:408`, `:410` cmdModes: complies (null prototype, values checked).

Wire-keyed reads in the leg's files: `state.portEol/portTarget/portConnected` (`cmdbar.js:80`, `:111`, `state.js:417`), `DISCONNECT_WHY[...]` (`statusbar.js:319`), `savedColors[name]` (`chrome.js:35`, `:45`), `cmdModes[alias]` (`state.js:417`, `:422`): all null-prototype, comply. `aliasMap` (`statusbar.js:477`): `Object.fromEntries` then assign onto a null-prototype target, complies.

### Class 45, whole-collection PUT
Command: `grep -n "put_config_" host/mcuscope/server.py`. 5 endpoints, 6 client senders.

- `/config/server`: GET `{host, port}`, client sends both: complies.
- `/config/storage`: GET 5 fields, client sends 5: fields comply; value of `max_db_bytes` violates (E-1).
- `/config/update`: `{check}`: complies.
- `/config/plotjuggler` (`settings.js:215`): `{enabled, dest}` both sent, from the runtime state by design: complies.
- `/config/ports` from `savePorts` (`settings.js:485-512`): 7 GET fields, all sent (serial blank = null = GET): complies; stale-read loss is E-8.
- `/config/ports` from `saveAttachedPortToConfig` (`settings.js:665-682`): existing entries round-trip whole; the new entry omits `identify`, which the server keeps per alias: complies.

### Class 46, field younger than the peer (web UI)
The page is served by the daemon it talks to, so skew exists only for a tab left open across a daemon change of version.
Command: `git diff v0.4.0..HEAD -- docs/SPEC.md | grep '^+'` for response fields; one new field the leg's JS reads, `/plot/channels.ports`.

- `api.js:313`: complies (`Array.isArray(list && list.ports) ? ... : []`).
- `api.js:319` `?port=` on an older daemon that ignores it returns every channel per port, duplicating seeds: exempt, only reachable across a downgrade with the tab open (REASONED).

### Class 54, one builder per parameter
Command: `grep -n "<name>" host/mcuscope/webui/*.js` for the parameters the leg's files touch.

- `session`, `since_ts`, `until_ts`, `last_ms`, `id_to` (exports): one builder, `exportrange.params`: complies.
- `names`, `port`, `format`, `decode`, `changes`, `deadband` (`/plot/export`): one builder, `plotExportPath`: complies.
- `/lines` `since_id`/`order`/`limit`/`id_to`: `api.js:242`, `api.js:427-428`, `api.js:482`, `terminal.js:493-494`; four sites, same integer form: violates the letter (no helper), no drift today.
- `match`: `api.js:241` (encodeURIComponent), `terminal.js:497` (gated on `pane.regex`), `terminal.js:574` (gated on `pane.regexSrc`): violates, the two pane sites disagree on when to send it (feeds E-3).
- `chan`: `terminal.js:496`, `:573`, same repeated form: violates the letter, no drift today.
- `/sessions?limit`: `exportdlg.js:143`, `settings.js:334`, same form: complies.
- `/plot/series` and `/plot/channels?port`: one site each (`api.js:350-358`, `:319`): complies.

### Class 55, refusal keyed on a name
Command: `grep -n "kind ==\|type ==\|\.kind\b\|\.type\b\|kind ===\|type ===\|kind !==\|type !==" <leg files>`. 15 lines.

- `exportdlg.js:56`, `:68`, `:88` (`f.type`): comply, the branch keys on the declared control type itself.
- `exportdlg.js:202`, `:253` (`ctx.kind`): exempt, title and filename, no refusal.
- `chrome.js:66`, `exportdlg.js:58`, `settings.js:289`, `:297`, `:303`, `:416`, `:433`, `:440`, `:446`: exempt, assignments.
- Outside the grep, same shape: `state.js:310-313` `streamable` keyed on the `/export` suffix: violates (E-9).

### Class 59, display rule against hidden
Command: `grep -n "hidden\]" style.css`; `grep -n "display:[^;]*!important" style.css`; `grep -n "style.display\|style.cssText" *.js`; `grep -n 'style="[^"]*display' index.html`.

- `style.css:40` global `[hidden] { display: none !important; }`: the only `[hidden]` rule: complies.
- `style.css:428` `display: none !important`: complies (none). No `display: <not none> !important` exists.
- Inline display: `statusbar.js:597`, `:600`, `:604`, `settings.js:402`, `:404`, `index.html:141`, `:144`, `:155`: none of these elements is toggled with `hidden`: comply.
- `chrome.js:68` cssText: no display: complies.

### Class 61, re-render rewriting a typed field
Command: `grep -n "\.value = " host/mcuscope/webui/*.js`. 52 sites.

- Option construction (`o.value = ...`), not a user field: `cmdbar.js:51`, `exportdlg.js:73`, `:147`, `:155`, `terminal.js:589`, `statusbar.js:575`, `:581`, `:585`, `state.js:373`, `settings.js:363`, `:369`, `:376`: exempt.
- Written on open or reset only: `exportdlg.js:82`, `:95`, `:96`, `statusbar.js:193`, `:194`, `:530`, `:531`, `:532`, `settings.js:111`, `:130`, `:131`, `:141`, `:142`, `:143`, `:145`, `:180`, `:379`, `:390`, `:401`, `:410`, `:417`, `:428`, `plots.js:494`, `chrome.js:67`, `terminal.js:651`: comply (`settings.js:180` lands after `showModal`, a millisecond window).
- User-requested writes: `cmdbar.js:175`, `:199`, `:230`, `:237`, `:238`, `:249`, `terminal.js:660`, `:842` (CAN id click), `statusbar.js:558` (alias prefill, stops once typed): comply.
- Select values synced after a fill, not typed: `cmdbar.js:55`, `terminal.js:593`, `exportdlg.js:149`, `:167`: comply.
- `cmdbar.js:114` `cmdEol.value = getEol()` on every poll: complies (equals the value the change handler already stored).
- `settings.js:195` `cfgPjDest.value = st.dest` from the checkbox's change handler: violates, LOW, REASONED: toggle `enabled` while dest resolves slowly, edit dest during the PUT, the answer overwrites the edit.
- Not a `.value` write but the same hazard from a poll: focus moved (E-4).

### Class 62, value before options
Command: `grep -n 'createElement("option")\|fillEolOptions' host/mcuscope/webui/*.js`. 13 fill sites over 8 selects.

- `#cmdEol`: fill `cmdbar.js:261`, writers `:114` after it: complies (fixed last round).
- `#cmdPort`: fill `cmdbar.js:50`, writer `:55` after: complies.
- `#attachEol`: fill `statusbar.js:658` at init, writer `:532` on open: complies.
- `#devSel`: fill `statusbar.js:574-587`, no explicit writer: complies.
- Settings row eol: fill `settings.js:427`, writer `:428`: complies.
- Settings row device: fill `settings.js:362-378`, writer `:379`: complies.
- `#expSession`: fill `exportdlg.js:146`/`:154`, writers `:149`/`:167` after: complies (duplication is E-5, not order).
- Export option selects: fill `exportdlg.js:72`, writer `:82` after: complies.
- Pane port select: fill `terminal.js:588`, writer `:593` after: complies.

## Decisions for the owner
- Token-less exports as navigation (E-3, E-9). The 2026-09-12 resolution assumed the dialog guards every refusable input; deadband, pane regex and a vanished session are not guarded. Options: a header-only preflight fetch before navigating (keeps streaming, one extra request), client-side mirrors of the deadband and regex grammars, or fetch-to-blob everywhere.
- Concurrent config edits (E-8). Options: a revision on `GET /config` checked by each PUT (409 on mismatch), merge-by-alias on the server, or accept last-writer-wins and say so in SPEC 3.3.1.
- Size cap editing (E-1): keep MB with round-trip preservation, or allow decimal MB, or edit in bytes.

## The two questions
1. Least confident: E-4's consequence. The `focus()` call from a poll is driven; that keystrokes then land in the command input and Enter sends them is the browser's documented behaviour but was not observed in one. Rechecked the trigger by re-reading `getCmdMode` and `autoAlias`; the two-port trigger remains REASONED. Second: E-3's browser outcome (saved error body against a failed download) differs per browser; the lost refusal reason and closed dialog are driven.
2. Not yet checked: every layout, focus and download claim in a real browser (E-3, E-4, E-11, E-12), and the Windows leg. Working outward from the export dialog found the pane regex sibling (history already falls back, export does not) and the session `.db` export sharing the navigation branch; working outward from the cap hint found the status bar comparing the same wrong figure. Not covered by this leg: the `/ws` feed and backfill paths in `api.js` beyond the seed-list change, and the CLI side of classes 46 and 54.
