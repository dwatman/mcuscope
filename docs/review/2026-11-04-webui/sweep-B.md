# Registry leg B: web UI sweep, classes 56 57 58 59 61 62 70 71 72 73

Tree: `7df68db`. Scope: `host/mcuscope/webui/*.js`, `index.html`, `style.css` (vendor/ excluded). Read-only; the two probes cited below live in `scratchpad/probe/` (node --test against the shipped modules and the suite's own `dom_stub.mjs`).

Totals: 2 violations (class 70: 1, class 71: 1). Every other class: 0.

## Class 56

Command: `grep -n "moved\|changes\|_last\b\|prev" host/mcuscope/webui/*.js` : **137 sites**.
Tighter: `grep -nE "\.(moved|base)\b|_last\b|prevTick|share_changes|_changed\(|changedBytes" ...webui/*.js` : 16 sites (all inside the 137).

- 17 `e.preventDefault()` hits, exempt because "prev" matches preventDefault, no baseline: app.js:147, 195; chrome.js:207, 225; cmdbar.js:298, 299, 300, 308; digital.js:289; exportdlg.js:307; plots.js:552, 553, 766, 817; settings.js:852; statusbar.js:743, 748.
- 50 comment-only lines, exempt because prose, no baseline: api.js:55, 167, 207, 532; can.js:23, 27, 175, 202, 203, 214, 297, 478, 483, 543, 567; chrome.js:20, 23, 64, 182; digital.js:98, 560; exportdlg.js:54, 131; pane.js:143; plots.js:625, 651, 945, 1428, 1482; settings.js:58, 137, 250, 817; state.js:32, 45; statusbar.js:355, 387; terminal.js:35, 232, 254, 321, 414, 434, 531, 608, 707; timewindow.js:258, 294, 341, 342.
- 70 code lines:

| Site(s) | Verdict |
|---|---|
| can.js:158, 178, 180, 205, 208, 209 (`e.base`/`e.moved`, `changedBytes`) | complies: `base` is written per data frame at ingest (can.js:182, in `canIngest`), diffed per frame, `moved` accumulates per-frame diffs; ingest order is id order (api.js:117 live, :604 backfill oldest-first). RTR frames neither diff nor reset. |
| can.js:484, 497, 504 (paint consumes `moved`) | complies: the mask is an accumulation of per-item diffs; consuming it at paint is the documented display, not the baseline. Hidden rows consume too (484). |
| can.js:569 (resume zeroes `moved`) | complies: deliberate (comment 567); baseline `base` is still per item. |
| digital.js:89, 90, 91, 97, 139, 140, 141; plots.js:466, 578, 579 (`prevTick`) | complies: per-lane / per-chart previous sample's tick continuation, written per sample at ingest (digital.js:91, plots.js:579). |
| timewindow.js:303, 304, 305, 344, 351, 352, 354, 356, 365, 366 | complies: previous anchor / previous sample of the same port, per item. |
| timewindow.js:261, 262; terminal.js:36, 37, 90, 97 (`fmtDelta`, `prev`) | exempt: per displayed row (SPEC 9.1 terminal delta); callers pass `pane.rows[i - 1]` (terminal.js:239, 309). |
| statusbar.js:278, 279, 284, 285, 286, 288, 293, 299, 302, 303, 400 (`prevRx`) | exempt: lines/s rate defined per poll interval. |
| api.js:744, 745, 746 | exempt: merges adjacent shed notices in the staging trim, not a change marker. |
| api.js:60 | exempt: `shownRate` paint cache for the rate readout (per interval figure). |
| app.js:222 | exempt: comment on a setInterval line. |
| exportdlg.js:195 | exempt: error text string. |
| exportdlg.js:207, 209, 220, 221, 222 | complies: `changes=1` is passed to the daemon; the baseline is server-side. |
| pane.js:29 | exempt: `pendingDirty` flag. |
| pane.js:144, 145 | exempt: column-width memo, not a change marker. |
| plots.js:769, 801 | exempt: comments on code lines (colour preview, legend re-render). |
| settings.js:836 | exempt: confirm text. |
| terminal.js:709, 710, 716 | exempt: alias-set compare for DOM rebuild, not a change marker. |
| terminal.js:861 | exempt: comment. |
| terminal.js:958, 960 | exempt: CAN-filter stash restore. |

Violations: 0.

## Class 57

Command: `grep -n "new Map(\|Object.create(null)" host/mcuscope/webui/*.js` : **52 sites**.

| Site | Key | Verdict |
|---|---|---|
| api.js:534, 769, 770 | pane objects | complies (not wire-keyed) |
| can.js:26 canRows | `port|bus|x/s id` (can.js:141) | complies |
| can.js:374 cells | same row key | complies |
| can.js:558, 704 canFrozen | copy/reset of canRows | complies |
| can.js:663 byPort | port | complies |
| chrome.js:26 savedColors, 43 paletteSlots | channel/lane name | exempt: colour store keyed by name by decision (registry) |
| chrome.js:94 windowGroups | selector object | complies |
| chrome.js:237 soloShow | names of one surface; digital.js:327 passes lane keys (`port|name`) | complies |
| digital.js:26 digitalLanes, 27 laneGroups | `port|name`, `port|group` | complies |
| digital.js:57 laneIds | stream = chart key (carries port) | complies |
| digital.js:327 | lane keys | complies |
| digital.js:502 wins | port | complies |
| exportdlg.js:21, 58 fields | dialog field name | complies (not wire-keyed) |
| freeze.js:12 surfaces | surface object | complies |
| layout.js:69 titles | chart key | complies |
| plots.js:31 PLOT_TYPES | type grammar constant | complies |
| plots.js:45 plotDefs | `port|sid` | complies |
| plots.js:79 seedMaxId, 80 charts | chart key (`port|sid`/`port|adhoc`) | complies |
| plots.js:203 byName | within one definition | complies |
| plots.js:331, 341 | line_id (global), names within one line | complies |
| plots.js:375 seedDef | entries of one chart-key group (one port) | complies |
| plots.js:406 fresh | `port|sid` | complies |
| plots.js:412 groups | chart key via `seedPort` | complies |
| plots.js:467, 595, 754, 1361 | per-chart maps of that chart's names | complies |
| plots.js:681 plotChannelMeta | `chart.key|name` (plots.js:669) | complies |
| plots.js:909 xLabels | tick values | complies |
| settings.js:74 cleanAs, 338 holds, 349 fetching | section id / path | complies |
| state.js:144 portEol, 145 portTarget | alias | complies |
| state.js:187 portColorCache, 489 cmdModes | alias | complies |
| statusbar.js:277, 294, 331 rateCells/next | alias | complies |
| statusbar.js:309 DISCONNECT_WHY | constant | complies |
| statusbar.js:519 aliasMap | alias | complies |
| terminal.js:65 snapshotAnchors | port | complies |
| timewindow.js:280, 323 | port (timewindow.js:4, 47 comments) | complies |

Newest-N priming scan: `seedPlotDefs` (api.js:286) pages its whole bounded window (`since_id`..`id_to`, follows `truncated`): complies. `seedChannelList` (api.js:361) asks per port once two are listed: complies.
Violations: 0.

## Class 58

The registry sweep is a description plus `c58b.py` (owner's machine, not available here). Run as: token extraction over webui (`MCUSCOPED?_[A-Z_]+`, `mcuscoped --x`, `mcu-sim --x`, `mcu <cmd> [<sub>] [--flag]`, backticked/bare `section.key`, `--flag` in prose), diffed against `config._KNOWN_KEYS`, the `environ` reads, `daemon.py` argparse flags and the typer tree in `cli.py`. **25 named tokens** (CSS custom properties `--side-w` etc. and JS index expressions excluded):

| Site | Token | Verdict |
|---|---|---|
| can.js:73 | `mcu can` | complies (cli.py:2157) |
| pane.js:160 | `mcuscoped --sim` | complies (daemon.py) |
| plots.js:171 | `mcu plot` | complies (cli.py:2550) |
| state.js:8 | `mcuscoped --token`, `MCUSCOPED_TOKEN` | complies (daemon.py flag; environ read) |
| state.js:217, 258 | `mcu mark` | complies (cli.py:633) |
| statusbar.js:24 | `--token`, `MCUSCOPED_TOKEN`, `server.token` | complies: the comment says config.py ignores `server.token`, which is true (it is in `_KNOWN_KEYS` only to be warned/ignored at load) |
| statusbar.js:26 (DAEMON_TITLE), index.html:18 | `--host`, `MCUSCOPED_TOKEN` | complies |
| statusbar.js:100, index.html:7 | `__MCUSCOPE_VERSION__` | complies (server.py:1178 substitutes it) |
| statusbar.js:133, settings.js:314, 315, 317 | `update.available/latest/checked_at` | exempt: `/status` JSON fields, not config keys |
| statusbar.js:153, 415 | `mcu status` | complies (cli.py:201) |
| settings.js:149, 150, 163, 164, 165, 168, 169, 289 | `server.host/port`, `storage.*`, `update.check` | complies (all in `_KNOWN_KEYS`) |
| index.html:36 | `mcu daemon stop` | complies (cli.py:2994) |
| index.html:255 | `MCUSCOPE_UPDATE_CHECK` | complies (update_check.py:59) |
| index.html:290 | `--token`, `MCUSCOPED_TOKEN` | complies |
| can.js:602 `server.py`, plots.js:349 `server._csv_wide` | code references | exempt: file/function names, not keys |

Violations: 0.

## Class 59

Commands:
- `grep -n "hidden\]" style.css` : **2** (style.css:39 comment, :40 `[hidden] { display: none !important; }`). Complies: only the global rule.
- `display: <not none> !important` : **0**. (style.css:450 is `display: none !important`, complies.)
- Inline `style.display` on an element also toggled with `hidden` (`grep -n "style.display" *.js`): **5** sites: settings.js:544, 546 (port-row devCustom), statusbar.js:666 (devCustom), 669 (bindRow), 673 (baudCustom). Each element is toggled only through `style.display` (index.html:145, 148, 159 start `style="display:none"`); none is ever given `hidden` (grep). Complies.

Violations: 0.

## Class 61

Command: `grep -n "\.value = " host/mcuscope/webui/*.js` : **53 sites**.

| Site(s) | Writer, callers | Verdict |
|---|---|---|
| cmdbar.js:62, exportdlg.js:84, 178, 186, settings.js:505, 511, 518, state.js:454, statusbar.js:643, 649, 653, terminal.js:699 (12) | option `.value` on a freshly built `<option>` | exempt: not a field |
| chrome.js:69 | colour picker input created per click | complies |
| cmdbar.js:66 | `populateCmdPort`; from init and `setKnownPorts` (only when the alias set changed) and `setCmdOffline` (only on flip); writes back the select's own current value | complies |
| cmdbar.js:121 | `syncCmdEol` from every `/status` poll (statusbar.js:526); writes `getEol()`, which the select's own change handler set (cmdbar.js:302), so it equals what the control holds | complies |
| cmdbar.js:199, 221 | `submitCmd`, Enter | complies (user action) |
| cmdbar.js:252, 259, 260 | history keys | complies (user action) |
| cmdbar.js:278 | marker ack after await; only when `markerEdits` unchanged | complies |
| exportdlg.js:93 | `buildOptions`, dialog open only | complies |
| exportdlg.js:106, 107 | `render()` from open and `reset range` click only; option/mode changes call `paint()`/`gateOptions()` | complies |
| exportdlg.js:126 | `optionChanged` (change handler) writes only fields still in `derived`; any `input` on a field removes it (exportdlg.js:95) | complies (a derived default the user asked to follow) |
| exportdlg.js:180, 198 | `fillSessions` (open, reset); select emptied first so nothing can be picked meanwhile; `fillGen` | complies |
| plots.js:537 | `startRename`, click | complies |
| settings.js:130 | `renderToken`: click-time open, load only when token section clean (`tokenTyped`), `applyToken` click | complies |
| settings.js:149, 150, 163, 164, 167, 168 | `renderServer`/`renderStorage`: open (fields disabled until loaded, `setReadOnly`), and `saveSection` only when the section's fields still equal what was sent | complies |
| settings.js:236 | `renderPj` (open) only if dest unchanged since the GET left | complies |
| settings.js:253 | `applyPj` (change) only if dest unchanged since the PUT left | complies |
| settings.js:521, 532, 543, 552, 559, 570 | `addPortRow`: table render (open/save) or `+ port` click | complies |
| statusbar.js:218, 219 | `openSessionDialog`, click | complies |
| statusbar.js:575, 576, 577 | `openAttach`, click | complies |
| statusbar.js:614 | `syncAlias`, from devSel change / devCustom input / device fill; skipped once the user typed an alias (`aliasTyped`) | complies |
| terminal.js:703 | `populatePortSelect` from `setKnownPorts`, only when the alias set changed; writes `pane.port` (the select's own value) | complies |
| terminal.js:762 | pane build | complies |
| terminal.js:771 | clear-x click | complies |
| terminal.js:974 | `setPaneRegex` from `filterPaneTo`: a CAN id click (registry exemption) or its unfilter, which restores only a pane whose box still holds the click's pattern | complies |

Violations: 0.

## Class 62

Command: `grep -n "createElement(\"option\")\|fillEolOptions" host/mcuscope/webui/*.js` : **21 sites** (5 are imports/definition/export lines: cmdbar.js:1, settings.js:7, state.js:451, 511, statusbar.js:1; exempt, no select).

| Select | Fill | Writers of `.value` reachable during init | Verdict |
|---|---|---|---|
| `#cmdPort` | cmdbar.js:61 | cmdbar.js:66 after the loop | complies |
| `#cmdEol` | cmdbar.js:290 `fillEolOptions` | `syncCmdEol` via `populateCmdPort` at :291, after; the `/status` poll's call (statusbar.js:526) starts at app.js:219, after `initCmdBar` at :210 | complies |
| export option selects | exportdlg.js:83 | exportdlg.js:93 after the loop | complies |
| `#expSession` | exportdlg.js:177, 185 | :180, :198 after their fills | complies |
| settings device select | settings.js:504, 510, 517 | :521 after | complies |
| settings eol select | settings.js:569 | :570 after | complies |
| `#attachEol` | statusbar.js:734 at module evaluation | :577 in `openAttach` (click) | complies |
| `#devSel` | statusbar.js:627, 642, 648, 652 | no JS writer of its `.value` (reads only, :613, :661, :677) | complies |
| `state.js:453` | `fillEolOptions` body | n/a | complies |
| pane port select | terminal.js:698 | :703 after; the change handler only reads | complies |

Violations: 0.

## Class 70

Command: `grep -n "status_code ==\|status ===\|\.code ==" host/mcuscope/webui/*.js` : **4 sites**. Also-sweep (`!r.ok` / `r.ok` range checks, `.status` reads, WS close codes): 6 more, 10 in all.

Server emitters (server.py): 401 only at :965 (`TokenGuard._deny`, missing/wrong token); 429 at :983 (token lockout); WS 1008/1013 pre-accept (:960, :978), which a browser sees as 1006.

| Site | Verdict |
|---|---|
| cmdbar.js:229, 231 `r.status === "ok"/"err"` | exempt: the `/cmd` body field, not an HTTP status (keys on the body) |
| state.js:96 `r.status === 401` -> prompt | complies: 401 has one server cause (token guard) |
| state.js:103 `=== 401` -> badge | complies: same |
| state.js:104 `r.ok` hides badge | complies: any success proves the token |
| state.js:119-121 `!r.ok` throws body `error` | complies by itself (keys on the body text; `err.status` carried, but no caller reads it: grep finds none) |
| state.js:380, 423 `!r.ok` -> `refusalText` | complies: body `error` |
| api.js:712 `onclose` | complies: does not key on `ev.code` (comment :718 records that the guard refusal is 1006) |
| **statusbar.js:494-505 `pollStatus` catch, fed by state.js:119** | **violates** (V70-1, below) |
| settings.js:32-40 / :812-818 `refreshConfig` null -> "daemon unreachable" | violates, same finding (V70-1, second site) |

### V70-1: every `/status` refusal is painted "daemon unreachable"

- `statusbar.js:496` catches every throw of `api("GET", "/status", ..., {background: true})`, and `api` throws for every non-2xx (`state.js:119`). The catch maps all of them to one meaning: `setDaemonOnline(false)` ("daemon unreachable", red dot), `setCmdOffline(true)` (command input placeholder "daemon unreachable", port select "(offline)"), port chips and db size cleared.
- The server answers `/status` with 401 (token guard, `server.py:965`) and 429 (lockout, `server.py:983`) from a healthy daemon; `/` and the static UI are exempt from the guard (`_is_ui_path`), so the page loads and polls.
- Sequence: `mcuscoped --host 0.0.0.0 --token X`; open the page from another machine with no stored token. Every 5 s poll gets 401; the chip reads "daemon unreachable" with a red dot and the command bar says "daemon unreachable", while the daemon is up and answering. Since R72-1 the poll no longer prompts, so this state persists until the user clicks the badge. With a stored wrong token the guard answers 429 for the lockout minute: same "unreachable" paint, and the badge is not raised by a 429 (`state.js:103`).
- Second site: `openSettings` (settings.js:812-818) shows "daemon unreachable: settings are read-only" for any `/config` failure, so a 429 lockout (or a cancelled 401 prompt) reads as a dead daemon.
- Driven: `scratchpad/probe/p70.test.mjs` (fetch answers 401 to `/status`, shipped `statusbar.js`): `daemonVer: "daemon unreachable"`, `dot crit`, `badge hidden: false`, `cmd placeholder: "daemon unreachable"`.
- Refutation tried: the token badge does show on 401 (mitigates, but sits beside a red "unreachable" chip, contradictory); no test pins 401/429 on `/status` to anything but "unreachable" (`statusbar_logic.test.mjs:103` uses a network failure; `api_token_background.test.mjs` asserts only prompt/badge). The earlier class 70 sweep (2026-09-15 `sweep-webui-chrome.md`) ruled the `===` branches only, not this catch. Survives.
- Fix shape: key the catch on `e.status` (present since state.js:121): 401 "token needed", 429 "locked out, retry in a minute", other HTTP status "daemon error HTTP n"; only a fetch/abort failure is "unreachable".

## Class 71

Commands:
- `grep -n "Math.round\|MiB\|/ 1000\|\* 1000" settings.js` : **4 lines** (158 comment, 166 `capShown`, 327 `fmtWhen` display only, 752 comment).
- `grep -nE "\.value = |\.checked = " host/mcuscope/webui/*.js` : **66 lines** (the 53 of class 61 plus 13 `.checked =`: exportdlg.js:70, 112, 113, 114; settings.js:169, 235, 252, 264, 289, 575, 582; statusbar.js:578, 579).

Rulings for every control a later save reads back:

| Control | Rendered from | Save reads | Verdict |
|---|---|---|---|
| cfgHost | `cfg.server.host` (loader-stripped, config.py:71) | `.trim()` | complies |
| cfgPort | int | `intField` | complies |
| cfgDbPath | `db_path` | `.trim()`; `resolve_db_path` strips too (config.py:160) | complies |
| **cfgRetention, cfgMinSessions** | JSON number (a double) | `intField` | **violates** (V71-1) |
| **cfgMaxDb** | `capShown {mb, bytes}` (166): an unedited MB sends `capShown.bytes` back | bytes is itself a double | **violates** (V71-1); the MiB rounding itself complies |
| cfgAutoSession, cfgUpdateCheck | booleans | `.checked` | complies |
| cfgPjEnabled / cfgPjDest | GET `/plotjuggler` | applied on change, `.trim()` | complies |
| cfgToken | browser storage | `.trim()` | complies |
| port rows: alias, device, serial, baud, eol, autoconnect, identify | GET `/config` ports (loader strips device/serial, config.py:487-490; eol always reported; baud > 0) | `.trim()`, `intField`, `.value`, `.checked` | complies |
| export clock bounds | `toLocalInput` (whole seconds) | `toEpoch` | complies: `fromTs/toTs` are only ever set from these inputs (exportdlg.js:276-278) |
| export options, attach and session dialogs | reset/derived on open, not loaded from saved state | | complies |
| radios exportdlg.js:112-114, statusbar.js:578-579, exportdlg.js:70 | mode / reset | | complies |

### V71-1: a Storage integer above 2**53 is rendered and saved back as a double

- settings.js:164, 167/166, 168 render `retention_days`, `max_db_bytes`, `min_sessions` from `JSON.parse`d numbers. config.py accepts each up to `_INT_MAX = 2**63 - 1` (config.py:252-255), and the D-17/R71-1 fix (settings.js:740-743, `storageInt < 2 ** 63`) says "any value the file holds saves".
- Sequence: config file holds `retention_days = 9223372036854775807` (loader-accepted). Open Settings: the field reads `9223372036854776000` (2**63). Change only "auto session" and Save: refused with "Retention must be a whole number of days, 1 or more" and nothing is saved. Same for `min_sessions`, and for `max_db_bytes` (capShown.bytes = 2**63, refused "Size cap must be a whole number of MB"). Any file value in roughly [2**63 - 512, 2**63 - 1] blocks every Storage save; a value in (2**53, 2**63 - 512) saves back silently changed (e.g. 9007199254740993 becomes 9007199254740992).
- Driven: `scratchpad/probe/p71.test.mjs` (wire JSON with the exact 2**63 - 1, shipped settings.js): `retention field: 9223372036854776000`, `err: "Retention must be a whole number of days, 1 or more"`, `puts: 0`.
- Refutation tried: `settings_storage_cap.test.mjs` pins only TYPED values (2**63 refused, 2**63 - 1024 saves) and loads values of 5000/2000; no test loads a value above 2**53. Survives, but LOW severity: it needs a hand-edited file with an absurd value (the registry's own "an upper bound nobody reaches by hand").
- Fix shape: keep each loaded value as the string the field shows and send the loaded number unchanged when the field still reads it (as capShown does for MB), or clamp the documented maximum to 2**53 - 1.

## Class 72

Command: `grep -n "\.focus()" host/mcuscope/webui/*.js` : **8 sites**. Also-sweep (`\.focus(\|showModal\|\.select()\|autofocus\|window.prompt` over js + index.html): **20 lines**.

| Site | Reached from | Verdict |
|---|---|---|
| app.js:90, 96 | collapse/reopen click | complies |
| chrome.js:211 | roving-radio keydown | complies |
| cmdbar.js:134 | `setCmdMode(.., remember=true)` only from the mode-button click (:293); the poll path (:99) passes no `remember` | complies (pinned by `cmdbar_mode` / `cmdbar_status_sync` tests) |
| exportdlg.js:245 (showModal), 247 comment, 248 | `openExportDialog`, from export clicks only (can.js:665 via openCanExport, digital.js:409, plots.js:725, terminal.js:786), before any await | complies |
| plots.js:549 | `finish(.., refocus)`: true only from Enter/Escape keydown | complies |
| plots.js:558, 559 | `startRename`, title click / span-button | complies |
| settings.js:795 | `openSettings`, click, before the awaits | complies |
| state.js:12, 67 | comments | exempt |
| state.js:56 `window.prompt` | `promptForToken`: badge click (state.js:78) or a foreground `authFetch` 401. All poll and stream requests pass `background` (api.js BG, statusbar.js:495). One non-user caller: `initSettings`' restart-badge prime `refreshConfig()` (settings.js:868) | exempt: runs once at page load, before any input; already ruled so in 2026-09-23 `fixdiff-webui-fw.md:73` |
| statusbar.js:190 | `showDlg` from `openSessionDialog` (session button click) and `openAttach` (click, before its await) | complies |
| statusbar.js:222 `.select()` | `openSessionDialog` | complies |
| terminal.js:775 | clear-x click | complies |
| index.html:144, 194, 226 `autofocus` | inside dialogs opened only by clicks | complies |

Violations: 0.

## Class 73

Command: `grep -n "await " host/mcuscope/webui/*.js` : **66 sites**. Also-sweep `grep -n "\.then(\|new Promise"` : **5 sites**.

Functions (each await site belongs to one):

| Function (sites) | Token taken before first await | Verdict |
|---|---|---|
| api.js `seedPlotDefs` (292) | `wsGen` per page; writes only `!pd` cache, which no clear drops by design | complies |
| api.js `seedChannelList` (363, 370) | writes no view; caller re-checks | complies |
| api.js `seedPlotHistory` (395, 405, 416) | `wsGen` + `cleared` = runBackfill's `clears.charts` from before its first await (api.js:543) | complies |
| api.js `fetchSince` (497) | `wsGen` per page | complies |
| api.js `runBackfill` (561, 565, 574, 583) | `clearTokens()` at :543 before any await; panes, CAN, charts compared after | complies |
| cmdbar.js `submitCmd` (207, 227) | `cmdGen` | complies |
| cmdbar.js `submitMarker` (275) | `cmdGen`, `markerGen`, `markerEdits` | complies |
| exportdlg.js `fillSessions` (169) | `fillGen` | complies |
| exportdlg.js `doExport`/`exportNow` (264, 269, 297) | `dialogGen`, `openCapture`, `ctx` identity, `wanted()` | complies |
| settings.js `refreshConfig` (35) | `cfgGen` | complies |
| settings.js `loadDevices` (47) | returns only; caller `openSettings` checks `openGen` | complies |
| settings.js `renderDbNow` (184) | `dbNowGen` | complies |
| settings.js `renderPj` (233), `applyPj` (249, 263), `savePjDefault` (275, 276) | `pjGen` plus control-unchanged checks; `openGen` | complies |
| settings.js `renderUpdateNow` (307) | `updateNowGen` | complies |
| settings.js download handler (417) | writes only path-keyed holds | complies |
| settings.js `deleteSession` (458, 459) | `sessionsGen`; `renderSessions` re-fills with its own gen | complies |
| settings.js `renderSessions` (475) | `sessionsGen` | complies |
| settings.js `putConfigNow` (693), `saveSection` (711, 713), save* (736, 764, 773, 782) | `openGen`, `sent` snapshot | complies |
| settings.js `openSettings` (805) | `openGen` | complies |
| settings.js `saveAttachedPortToConfig` (879, 888, 894) | `cfgGen` for the badge; writes no field | complies |
| state.js `authFetch` (91, 101), `api` (116, 118), `refusalText` (129), `preflight` (379, 380), `downloadPath` (411, 422, 423, 424) | primitives, no view; `wanted()` gate | complies |
| statusbar.js `toggleSession` (204), `startSession` (234) | `failGen` / `sesGen` | complies |
| statusbar.js `pollStatus` (495) | one poll in flight (`statusInFlight`); a stale-poll-after-action was ruled a stated design choice in 2026-09-15 | complies |
| statusbar.js `reconnectPort`/`holdPort`/`detachPort` (537, 548, 559) | `failGen` | complies |
| statusbar.js `openAttach` (582 comment, 587), `populateDevices` (632), `submitAttach` (711) | `devicesGen`, `attachGen` | complies |
| terminal.js `loadHistory` (553), `loadHistoryPage` (570, 574) | `historyGen` read at page start; a hop starts only after the previous page returned false with no gen change, and every rows-replacing path calls `resetHistory` (api.js:201, terminal.js:423, 783, 930) | complies |
| `.then`: api.js:229, 689 | `drainStaging(gen)` checks `staging.gen` | complies |
| `.then`: settings.js:683 | `putChain`; `putConfigNow` checks `openGen` | complies |
| `.then`: statusbar.js:482 | shared poll chaining | complies |
| `.then`: terminal.js:811 | clipboard ack class on the clicked line | complies |

Violations: 0.
