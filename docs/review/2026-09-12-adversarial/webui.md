# Web UI leg (diff fd5d63d..c15b7c6)

Scope: `host/mcuscope/webui/*.js`, `index.html`, `style.css`, and the `host/tests/webui_js/*.test.mjs` the diff added or changed.
`host/tests/test_webui.py` is **not** touched by this diff (endpoint-level static serving only); it was read and left out of the mutation round.

What was driven, and where the scripts are (all under `/tmp/rev-2026-09-12/webui/`):

- A throwaway sim daemon: `mcuscoped --sim --config rev.toml --port 18603 --plot`, `db_path = /tmp/rev-2026-09-12/webui/rev.db`. Never the default config or db.
- `drive1.sh` - the export endpoints with the params the dialog builds, `/status` field presence, `/can/frames?format=csv` limit behaviour, `/sessions` ordering.
- `drive_range.mjs` - `exportrange.js` loaded under `dom_stub.mjs` and its `params()` output fetched for real against `/lines/export`, `/plot/export` and `/can/frames`, per range mode, with row counts.
- `drive_lines.mjs` - `terminal.js exportPane()`'s `build()` reproduced verbatim (it is module-private, see W7) and fetched, against the backfill path 73 lines above it for contrast.
- `drive_proto.mjs` - class 34 probe: `statusbar.js pollStatus`'s two new stores and `state.js cmdModes`, driven through the real `cmdbar.js` under the stub with a port aliased `constructor` / `toString`.
- `drive_sole.mjs` - the "sole connected port" rule, two managed ports with one connected, driven through the real `cmdbar.js`.
- `mutate.sh` + `mutations.txt` + `mut_M*.log` - 29 hand mutations applied in the worktree `wt` (created at c15b7c6, removed after the round) and run with `node --test`.

Baseline before mutating: `node --test 'tests/webui_js/*.test.mjs'` = 328 pass, 0 fail.

## Findings

| id | sev | where | claim | verified |
|---|---|---|---|---|
| W1 | **HIGH** | `terminal.js:514` | The pane export comma-joins the channel filter (`p.set("chan", [...pane.channels].join(","))`). `/lines/export` takes `chan` as a **repeated** query parameter (server.py:1648 `list[Chan]`, SPEC 3.4 "`chan` may repeat"), so any pane with 2 to 5 of the 6 channels ticked gets `422 chan.0: Input should be 'debug', 'cmd', ... (got 'debug,event')` and downloads nothing. All 6 ticked omits `chan` and works; exactly 1 ticked happens to work because there is no comma. The backfill path at `terminal.js:441`, which is correct, uses `q.append("chan", ch)` - one of two siblings. The 2026-09-07 round's Q2 answer established the repeated form; the new export path did not carry it. | Driven: `drive_lines.mjs` against the live daemon. 5-of-6 and 2-of-6 -> HTTP 422; 6-of-6 -> 200/35788 rows; 1-of-6 -> 200/35619 rows; the backfill shape -> 200 in every case. Fix: `for (const ch of pane.channels) p.append("chan", ch);` |
| W2 | MED | `state.js:346` (and `statusbar.js:369-370`) | `cmdModes` is a plain `{}` keyed by port alias. `config.py ALIAS_RE` is `^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$`, so `constructor`, `toString` and `valueOf` are legal aliases. `getCmdMode("constructor")` returns `Object.prototype.constructor` (a function, so `??` does not fall through), `setCmdMode` is then called with a function: **neither** mode button lights up or carries `aria-checked="true"`, the timeout box stays shown, and `cmdMode === "raw"` is false so the bar POSTs `/cmd` (seq + wait) to a port that has never answered `OK monitor` - exactly the case the feature exists to prevent. `statusbar.js:369-370` build `state.portEol` / `state.portTarget` with `Object.fromEntries`, also plain objects; their own-property writes mask the inherited ones for every attached alias, so no wrong behaviour was reachable there, but both violate the class-34 invariant and `statusbar.js:202` / `plots.js:26` in the same tree already use `Object.assign(Object.create(null), ...)`. | Driven: `drive_proto.mjs`. Alias `constructor` and alias `toString` both read as mode `cmd` and POST `/cmd`; the control alias `sbc` reads `raw` and POSTs `/send`. Fix: `let cmdModes = Object.create(null);` (and the same for the two `Object.fromEntries` results, e.g. `Object.assign(Object.create(null), Object.fromEntries(...))`). |
| W3 | MED | `cmdbar.js:60` `targetAlias()` vs `serial_link.py:1399-1415` | This same diff added the rule "several attached: a sole **connected** port is not ambiguous" to `PortManager.resolve()`, and changed `index.html:115` to advertise it (`auto = the sole connected port`). `targetAlias()` was not updated: it shortcuts only on `state.knownAliases.length === 1`, and `statusbar.js` feeds `setKnownPorts` **every** managed alias, connected or not. With two managed ports of which one is connected, the daemon sends the command to the connected one while the bar seeds its eol display from the `"auto"` pseudo-alias and its send mode from `getCmdMode("auto")`. | Driven: `drive_sole.mjs`. Ports `mcu` (connected, `eol=crlf`, `target=charger`) and `spare` (disconnected): the bar shows eol `lf` (wrong, the daemon appends `crlf`) and mode `raw`, POSTing `/send` with no seq or wait to a monitor that `resolve()` would have answered on `/cmd`. With `mcu` alone it shows `crlf` / `cmd` / `/cmd`. Fix: record the connected flag in `pollStatus` (it is already in `/status`) and make `targetAlias()` mirror `resolve()`: the pick, else the sole managed alias, else the sole connected alias, else `"auto"`. |
| W4 | MED | `exportrange.js:60`, test `exportrange.test.mjs:108` | `params()` sends `id_to=0` whenever the watermark is 0 (`watermark != null` is true for 0, deliberately). Every export endpoint declares `id_to: int \| None = Query(default=None, ge=1, ...)` (server.py:1655, 1697, 1794), so the request is **refused**, not answered with an empty export. Reachable: pause any panel before a line arrives (`pane.frozenId` starts at 0, `state.maxId` starts at 0), and sharply after a daemon DB reset, where `api.js:171` sets `p.frozenId = 0` on panes that stay paused. The code, the daemon and the test contradict each other: `exportrange.test.mjs:108` pins `id_to=0` with the comment "that exports nothing, which is correct". | Driven: `drive1.sh` and `drive_range.mjs`. `/lines/export?id_to=0`, `/plot/export?...&id_to=0` and `/can/frames?format=csv&id_to=0` all return `422 {"error":"id_to: Input should be greater than or equal to 1 (got '0')"}`; `id_to=1` returns 200. Fix: pick one side. Either relax the daemon to `ge=0` (and keep the test), or have `params()` refuse a zero watermark in the dialog with an inline message ("this panel was paused before it held any line") - do not silently drop the bound, which would export past the freeze. |
| W5 | MED | `plots.js:1001,1008`; `digital.js:299,306` | The plot and digital option lists offer `changes only` and `decode values` as two independent checkboxes, and `build()` emits `changes=1` without `decode` when the user unticks decode. SPEC 9.2 and server.py both require `changes=1` to imply `decode=1`. The combination is one click away from the default (decode is on, changes off) and produces a refusal, not an export. | Driven: `/plot/export?names=ftest&format=long&changes=1` -> `400 {"error":"changes requires decode"}`; with `decode=1` -> 200. Fix: one line in each `build()` - `if (v.changes) { p.set("changes","1"); p.set("decode","1"); }` - or give the `changes` field `enabledBy: "decode"`, which `exportdlg.js` already supports. |
| W6 | MED (test quality, class 27) | `exportdlg.test.mjs:19`, `export_paused_window.test.mjs:21` | The fake `fetch` answers `ok: true, status: 200` to **every** URL with no parameter handling at all, while the endpoints it stands in for validate `chan` against an enum and a repeated-parameter shape, `id_to >= 1`, `changes` implies `decode`, `deadband` implies `changes`, `format` against a fixed set, and `until_ts >= since_ts`. The suite therefore certifies any URL the dialog can build. W1, W4 and W5 are all URLs this double accepted and the daemon refuses. | Compared against `server.py:1644-1810`, then each URL fetched for real (`drive_range.mjs`, `drive_lines.mjs`). Fix: give the double the three endpoints' parameter guards (enum `chan`, `id_to` floor, the `changes`/`decode`/`deadband` implications) and assert a refusal is never built, or drive the real daemon in `test_webui.py` with the URLs the builders produce. |
| W7 | MED (test quality, class 29) | `terminal.js:500-517`; mutations M19, M20 | No test drives `exportPane` at all: it is module-private (`terminal.js` exports neither it nor a seam onto it) and the `.exportpane` button only exists in `index.html`'s `<template>`, which `dom_stub.mjs` cannot clone (`firstElementChild` hands back a bare element). Dropping the channel filter entirely (M19) and dropping the pane's freeze watermark (M20) both leave the whole suite green - and class 23's registry entry names "an export button that ignores its surface's freeze" as a confirmed shape, fixed for `plots.js` and `digital.js` (whose exports **are** exported and covered by `export_paused_window.test.mjs`) and never for the terminal. | Mutations M19 and M20 survived, 0 failing tests each. Fix: `export` `exportPane` from terminal.js and add the `export_paused_window.test.mjs` equivalents for a pane, plus one test asserting the built URL carries `port`, a repeated `chan` and `match`. |
| W8 | LOW | `index.html:118`, `state.js:327-334` | The eol select has no "port default" entry; the settings-dialog control that had one was deleted in the same diff. `setEol("")` still exists in `state.js` but nothing in the UI reaches it, so a single pick in the command bar pins the browser-side override for every port, for every future page load, and the only escape is clearing `localStorage["mcuscope.eol"]`. The pick then survives a later change to the port's own `eol` in the config. | Read, plus `drive_proto.mjs`/`cmdbar_eol.test.mjs` behaviour ("a pick ... beats the port's value"). Fix: add a fourth option `<option value="">port default</option>` and let `syncCmdEol` show the port's value as the option's label when it is selected. |
| W9 | LOW | `exportdlg.js:158-167` | `doExport` closes the dialog before the download runs, and `$("expErr")` is used for exactly one case (the inverted clock range). Every daemon refusal - W1's 422, W4's 422, W5's 400, a bad `deadband` name, a mistyped CAN id - therefore lands as a `hooks.reportError` toast with the dialog gone, so the user cannot see or correct the range and options that produced it. | Read; each refusal body confirmed live (`drive1.sh`, `drive_lines.mjs`, the deadband drive). Fix: `await` the download inside `doExport` and only `closeExport()` on success, writing the error into `$("expErr")` otherwise. |
| W10 | LOW | `exportdlg.js:102` with `exportdlg.js:162` | `applyShownAvailability` silently rewrites a remembered `shown` range to `session` whenever the panel being opened has no frozen window, and the next Export persists that rewrite. Exporting from the CAN table (never a freeze surface) or from any live panel therefore forgets a `shown` choice made on a paused one. | Read; the rewrite is unconditional and `saveRange(range)` runs on every Export. Fix: keep the remembered mode and only override the *rendered* selection, or skip `saveRange` for a mode the dialog itself changed. |
| W11 | LOW | `exportdlg.js:124-127` | When the remembered session is not in the newest 50 that `/sessions?limit=50` returns - or was deleted - `fillSessions` substitutes the open session (or `sessions[0]`) with no notice, and the next Export persists the substitution. A session older than the 50th cannot be reached from this dialog at all. | Read; `/sessions?limit=50` is hard-coded at `exportdlg.js:109`. Fix: when the remembered ref is missing, say so in `$("expErr")` before falling back; consider raising or paging the limit. |
| W12 | LOW | `plots.js:990`, `digital.js:291` | `exportChart` and `exportDigital` `return` silently when nothing is shown, so the `export` button does nothing at all and says nothing. `exportdlg.test.mjs` pins this as intended for a chart ("with no channels there is nothing to export and nothing to ask about"), but a control that is enabled and inert is the shape class 12 is written against. | Read + mutation M26 (caught for plots) / M27 (survived for digital, so the digital side is not even pinned). Fix: disable the button while nothing is shown, or open the dialog and put "no channels are shown" in `$("expErr")`. |
| W13 | LOW (test quality, class 29) | `state.js:359`; mutation M17 | `setCmdModeFor`'s value guard (`if (!MODE_CHOICES.includes(mode)) return;`) is unasserted: removing it leaves the suite green. Its read-side twin at `state.js:350` **is** asserted (M18 caught). One of two siblings again. | Mutation M17 survived, 0 failing. Fix: one assertion that `setCmdModeFor(alias, "bogus")` neither changes `getCmdMode` nor writes `localStorage`. |
| W14 | nit | `exportrange.js:23-25` vs `exportrange.js:27` and `exportrange.test.mjs:36,50-53` | The comment says an unrecognised value "falls back **whole** rather than per field: a half-valid range would export a window the user never chose". The code falls back whole only on `mode`; `num()` nulls `fromTs` and `toTs` individually. The test is named "nothing readable in storage falls back to the default, whole" and then asserts the opposite for the readable half (`toTs` survives a bad `fromTs`). Three statements of the rule, two of them wrong. | Read; mutation M03 confirms the per-field behaviour is what is pinned. Fix: reword the comment and the test name to say the mode falls back whole and the bounds per field. |

## Ruled out, with evidence

- **Unencoded pattern characters in an export URL.** Every `build()` goes through `URLSearchParams`; a pane regex of `temp=(\d+)&|#%2B x` came back as `match=temp%3D%28%5Cd%2B%29%26%7C%23%252B+x` and the daemon answered 200 with the same row count as the backfill path (`drive_lines.mjs`). No `encodeURIComponent` gap.
- **`/can/frames` history export silently capped at `limit=100`.** `can.js` sends no `limit` and the endpoint defaults to 100, but the `format=csv` branch (server.py:1723-1734) goes through `open_can_export` and never reads `limit`. Driven: 421 and 423 rows on two successive calls against a running capture, not 100.
- **The bundle button on an open session.** `GET /sessions/1/bundle` on the open auto-session: HTTP 200, 1.9 MB zip, entries `capture.db, lines.txt, plot_0.csv, plot_1.csv, plot_2.csv, plot_adhoc.csv, can.csv, manifest.json`. A missing session is `400 {"error":"no such session: 999"}`, which `downloadPath` surfaces verbatim.
- **Class 45 on the ports PUT.** GET `/config` returns 7 fields per port; `collectPorts` sends 6 and omits `eol`; server.py:1241-1243 keeps the saved `eol` for an omitted one, and `identify` is now sent explicitly. Compliant via the invariant's second branch. (Unchanged pre-existing hole, out of this diff: renaming an alias in the table loses its `eol`, because `saved_eol.get(newAlias)` misses.)
- **Class 46 on the new wire fields.** `p.eol` -> `|| "lf"`; `p.target` absent -> `undefined` -> `raw`; `pc.identify` absent -> `!== false` -> on (pinned by `settings_ports_identify.test.mjs`); `s.ended_ts` absent -> no session marked open, falls back to `sessions[0]`. All degrade, none throws.
- **The Identify checkbox after reopening settings.** `openSettings` awaits `refreshConfig()` and `renderPortsTable()` rebuilds every row from the fresh `/config`, so an uncommitted tick cannot survive a reopen.
- **The eol select going stale after a reconnect or a config change.** `syncCmdEol` runs on every status poll (`statusbar.js:372`), after `state.portEol` is rebuilt, so it follows the port. Stale only in the W8 sense (a user pick, by design).
- **Send mode reaching ports that appear later (class 25).** `state.portEol` / `state.portTarget` are rebuilt wholesale each poll and `getCmdMode` has a default, so a port added mid-session gets the default rather than nothing. `populateCmdPort` resets a pick that is no longer in the list.
- **Class 26 on the pane export.** `exportPane`'s span comes from `pane.rows`, which a paused pane derives from `pane.frozenRows` (`terminal.js:136,305`), the snapshot taken at pause - not from the rotating shared ring.
- **Class 44.** No paged walk was added; every export is one request, and `shown` mode is offered only when a watermark exists, so `last_ms` never travels without an absolute `id_to` beside it.
- **Class 6.** `num()` gates on `Number.isFinite`; `toEpoch` returns null on `NaN`; `!span` catches a `NaN` span. Nothing new writes into a chart data array.
- **Inverted clock bounds.** `inverted()` refuses them inline and `_check_window` (server.py:2620) would 400 them - the dialog and the daemon agree.
- **The daemon's own inverted-window and equal-bounds handling.** `since_ts=1&until_ts=1` returns 200 with 0 rows (`/lines/export`) and the header alone (`/can/frames?format=csv`), matching SPEC 3.4.
- **`mutations.txt` M01-M16, M18, M21-M26, M28, M29** all caught, so the range rules, the watermark rule, the session preselect, the cancel/export persistence, the eol seed, the per-alias mode memory, the identify round-trip and the bundle path are each pinned by something.

## Sweep verdicts

**Class 34 (name-keyed stores) - 30 sites in the touched files** (`grep -n "JSON.parse\|= {}\|= Object\.\|Object.fromEntries\|new Map(" app.js can.js cmdbar.js digital.js exportdlg.js exportrange.js plots.js settings.js state.js statusbar.js terminal.js`). Added or changed by this diff, 7 of them:

- `state.js:346` `let cmdModes = {}` - **violates** (W2, driven).
- `state.js:347-352` `JSON.parse(localStorage[MODE_KEY])` - values type-checked against `MODE_CHOICES`; **violates** on the container only, it populates `cmdModes`.
- `statusbar.js:369` `state.portEol = Object.fromEntries(...)` - **violates** the letter (W2); no wrong behaviour reachable, own properties mask the inherited ones for every attached alias.
- `statusbar.js:370` `state.portTarget = Object.fromEntries(...)` - **violates**, same.
- `exportrange.js:31` `JSON.parse(localStorage[KEY])` through `validate()` - **complies**: mode checked against `MODES`, session coerced to a string, bounds through `Number.isFinite`.
- `exportdlg.js:15,39` `values = {}` - **exempt**: keys are the caller's hard-coded option names (`format`, `ids`, `decode`, `changes`, `deadband`), never from the wire.
- `exportdlg.js:16,38` `fields = new Map()` - **complies**.

The other 23 sites are untouched by this diff: `can.js:18,163,224`, `digital.js:21`, `state.js:137`, `statusbar.js:202` (already `Object.create(null)`), `plots.js:26` (already `Object.create(null)`), `plots.js:40,55,56,174,297,309,343,372,416,451,510,963`, `cmdbar.js:20`, `terminal.js:713` - all `Map`, null-prototype, or non-wire keys.

**Class 45 (whole-collection PUT) - 5 `put_config_` endpoints** (`grep -n '@app.put("/config' server.py`: `/config/server`, `/config/storage`, `/config/update`, `/config/plotjuggler`, `/config/ports`). Four take a scalar object, **exempt**. `/config/ports` is the collection: GET returns `{alias, device, serial_number, baud, eol, autoconnect, identify}`; `collectPorts` sends all but `eol`; server.py:1241 restores the saved `eol` by alias. **Complies.**

**Class 46 (field a newer daemon added) - 5 new wire reads in this diff**: `p.eol` and `p.target` (statusbar.js:369-370), `pc.identify` (settings.js:359), `s.ended_ts` and `s.lines` (exportdlg.js:117,121). All five **comply** (see the ruled-out list). `settings_ports_identify.test.mjs` carries the canned response without `identify`; the other four have no such test - noted, not filed, since none can throw.

**Class 25 (group state reaching new members) - 3 group states in scope**: per-port send mode, per-port eol display, the remembered export range. All three **comply**: the two per-port stores are rebuilt from the full `/status` port list on every poll and read through a defaulting accessor, so a port added later is covered; the export range is global and reloaded on every dialog open.

**Class 51 (boundary-triggered fetch) - 2 sites in the webui, 0 added by this diff** (`grep -n "scrollTop\b.*<\|IntersectionObserver\|atTop" webui/*.js` -> `terminal.js:621,626`, both pre-existing and the subject of the 2026-09-07 W1). **Exempt** from this leg. `fillSessions` fires on dialog open, not on an edge.

**Class 23 (a frozen surface's export) - 4 export buttons.** `plots.js:995` passes `chart.paused ? chart.frozenMaxId : null` (**complies**, pinned); `digital.js:294` passes `digitalPaused ? digitalFrozenId : null` (**complies**, pinned); `terminal.js:506` passes `pane.autoscroll ? null : pane.frozenId` (**complies** in the code, **unasserted** - W7); `can.js:367` passes `null` (**exempt**: the CAN table is not a freeze surface and registers none).

**Class 26 (frozen view over a rotated ring) - 1 new reader.** `exportPane`'s `pane.rows` (**complies**, see ruled-out).

**Class 16 (one bad item ends the loop) - 9 loops added** (`git diff ... | grep "^+.*for (const\|\.map(\|\.filter("`): `can.js:357` (canRows), `exportdlg.js:33,50,79,124` (caller-declared options and the sessions list), `state.js:350` (localStorage entries, value-checked), `statusbar.js:369,370` (the ports list, already iterated by `renderPorts` on the same line of the same `try`), `terminal.js:501` (pane rows). None can throw on a malformed item; the sessions loop degrades to `undefined (undefined lines)` in a label. **All comply.**

**Class 36 (periodic catch-up loop) - 0 `while` loops added.** **Exempt.**

**Class 44 (relative bound re-evaluated per page) - 0 multi-request walks added.** The diff's only new requests are `GET /sessions?limit=50` and the single-shot downloads. **Exempt.**

**Class 6 (non-finite into chart arrays) - 3 numeric sites added** (`num()`, `Math.round(shownLastMs)`, `Math.max(1, span)`). **All comply** (see ruled-out).

**Class 27 (a double gentler than the real thing) - 6 doubles in the new/changed test files.** `exportdlg.test.mjs:19` and `export_paused_window.test.mjs:21` **violate** (W6). `settings_sessions_bundle.test.mjs:26` **violates** mildly: the response has neither `headers` nor `blob`, so `downloadPath` throws into its own catch and the test still passes on its URL assertion - the bundle download is pinned only as far as the path. `cmdbar_eol.test.mjs:13` and `cmdbar_mode.test.mjs:13` **comply** for the respect under test (the body shape is asserted directly; the real `/cmd` and `/send` accept exactly that body). `settings_ports_identify.test.mjs:22` **complies** for `identify` (the body is asserted; the real endpoint's other guards are not the respect under test). Separately, `dom_stub.mjs` does not honour `disabled`, so a test can `emit("change")` on the disabled `expModeShown` radio where a browser would fire nothing - **exempt**, but it means "shown is disabled" is asserted through the flag, never through the event.

**Class 28 (an assertion the test's own guard swallows) - 0 `try {` blocks in the 7 new/changed `.test.mjs` files.** **Exempt.**

**Class 50 (a race test parking the worker too late) - 0 Event-spin doubles added** (`git diff ... tests/webui_js | grep "is_set()\|while ("` -> 0). **Exempt.**

**Class 29 (the negative is never asserted)** is covered by the mutation list below; three survivors, W7, W12 and W13.

## Mutations

29 applied in the worktree, 25 caught, 4 survivors. Full log in `mutations.txt`, per-mutation output in `mut_M*.log`.

| id | file | mutation | caught |
|---|---|---|---|
| M01 | exportrange.js | `id_to` only in `shown` mode | yes (4) |
| M02 | exportrange.js | `if (watermark)` - a 0 watermark reads as live | yes (1) |
| M03 | exportrange.js | `validate` keeps the stored bounds unchecked | yes (1) |
| M04 | exportrange.js | clock mode sends no bounds | yes (2) |
| M05 | exportrange.js | `inverted()` always false | yes (2) |
| M06 | exportrange.js | `last_ms` not rounded | yes (1) |
| M07 | exportdlg.js | the range is never saved | yes (1) |
| M08 | exportdlg.js | the inverted-range guard dropped | yes (1) |
| M09 | exportdlg.js | `shown` always offered | yes (2) |
| M10 | exportdlg.js | preselect `sessions[0]` rather than the open run | yes (1) |
| M11 | exportdlg.js | the panel's watermark never reaches `params()` | yes (4) |
| M12 | cmdbar.js | seed the eol select from `portTarget`, the wrong field | yes (2) |
| M13 | cmdbar.js | a user's eol pick no longer beats the port's value | yes (1) |
| M14 | cmdbar.js | the sole-port shortcut dropped | yes (2) |
| M15 | cmdbar.js | a mode click is not remembered | yes (1) |
| M16 | state.js | every port defaults to `cmd` (ignore `portTarget`) | yes (3) |
| M17 | state.js | `setCmdModeFor` accepts any value | **no (W13)** |
| M18 | state.js | the stored mode is loaded unchecked | yes (1) |
| M19 | terminal.js | the pane export drops the channel filter | **no (W7)** |
| M20 | terminal.js | the pane export drops the freeze watermark | **no (W7)** |
| M21 | can.js | an empty id list is sent as `id=` | yes (1) |
| M22 | can.js | the table snapshot goes to the daemon | yes (3) |
| M23 | settings.js | a missing `identify` reads as off | yes (2) |
| M24 | settings.js | `identify` dropped from the PUT body | yes (1) |
| M25 | settings.js | the bundle button fetches the db export | yes (1) |
| M26 | plots.js | a chart with nothing shown still opens the dialog | yes (1) |
| M27 | digital.js | a digital panel with nothing shown still opens the dialog | **no (W12)** |
| M28 | plots.js | the chart export ignores its freeze | yes (4) |
| M29 | digital.js | the digital export ignores its freeze | yes (1) |

## Manual-verify (only a browser can settle these)

Each line is runnable against `mcuscoped --sim --plot` at `http://127.0.0.1:8558/ui/`.

1. In a terminal pane untick the `sys` channel, click `export`, press Export, and confirm a file downloads rather than an error toast (this is W1; expect the toast today).
2. Pause a terminal pane immediately after a hard reload, before any line lands, click `export` and press Export; confirm the message shown (W4).
3. Open a chart's `export`, untick `decode values`, tick `changes only`, press Export, and read the toast (W5).
4. With the settings dialog, add a second port that cannot connect, leave one connected, then check the command bar's line-ending select and which mode button is lit (W3).
5. Pick a line ending in the command bar, then look for any way back to "port default" without clearing site data (W8).
6. Pause a chart, export with `Shown window`, then export the CAN table, then reopen the chart's export and check whether `Shown window` is still selected (W10).
7. Delete the session the export dialog last remembered, reopen any export dialog, and check whether anything says the remembered range changed (W11).
8. Hide every channel on a chart and click `export`; confirm the button appears to do nothing (W12).
9. Tab through the export dialog: the three radios are one `name="expRange"` group, `expSession`/`expFrom`/`expTo` toggle `disabled` with the mode, and the dynamic option inputs get ids `expOpt_*` but **no `<label for>`** on the select/text fields (the label is a sibling, not associated) - check a screen reader announces them.
10. Resize the export dialog narrow: `.radio-row` is a flex row with `white-space: nowrap` labels and two `datetime-local` inputs at `flex: 1` - check the clock row does not overflow the dialog.
11. Press Escape on the export dialog and confirm it closes without saving the range (the `cancel` handler preventDefaults then calls `closeExport`).
12. Click Export the instant the dialog opens, before `/sessions` answers, and check which session the download covers (the select is emptied synchronously and filled after the fetch).
13. Confirm the `whole session` button, on a capture whose only session has ended, selects that session rather than the whole capture.
14. Export a plot with `changes only` and a deadband of `vbat=0.5`, then with a name that is not in the selection, and check both outcomes are legible to the user.
15. Bundle a long-running open session from Settings and confirm the browser saves `bundle.zip` with the daemon's own filename from `Content-Disposition`.

## The two questions

**Q1, least confident.** That W3's practical reach is what I claim. The probe built the two-port state by calling `statusbar.js`'s own assignment lines and `setKnownPorts` directly (`drive_sole.mjs`), not by serving a real two-port `/status`, because the sim daemon has one port. Re-drove it after writing the finding: `serial_link.py:1399-1415` is unambiguous that `resolve(None)` answers the sole *connected* port among several, and `statusbar.js:372` feeds `setKnownPorts` the unfiltered `s.ports` map, so the mismatch is in the code and not in the probe. What is **not** driven and is stated as such: two real ports on real hardware with one flapping. Also not driven: everything in the manual-verify list, all of it dialog layout, focus and `disabled` semantics the stub cannot fake.

**Q2, the gap.** The round found the export URL builders by fetching them, and that is the technique that should have been applied to the *other* direction: nothing here checked what the daemon's export **responses** do to the browser - the `Content-Disposition` filename parsing in `filenameFromDisposition` against the new `<session>_<kind>_<from>-<to>.<ext>` naming rule this diff added to SPEC 3.4, and whether a streamed multi-MB export through `r.blob()` behaves (the bundle is 1.9 MB after 30 s of sim; a real run is far larger, and `downloadPath` buffers the whole body in memory before saving). Checked after asking: `export_filename` (server.py:2631) sanitises to `[A-Za-z0-9._-]`, so the `filename*=UTF-8''` branch of `filenameFromDisposition` is dead for these exports and the plain branch handles them; the buffering concern is real and untested, and belongs to the next round. Second gap, narrower: `test_webui.py` was not touched by a diff that added two modules and six element ids to `index.html`, and nothing - Python or JS - asserts that the ids `exportdlg.js` resolves at module load (`exportDlg`, `expOptions`, `expSession`, `expFrom`, `expTo`, `expErr`, `expGo`, `expWhole`, `expMode*`) actually exist in `index.html`; the stub manufactures any missing id on demand, so a typo in either file is invisible to the whole suite.
