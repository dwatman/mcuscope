# Web UI panels-and-dialogs batch (HEAD at start c15b7c6)

Files touched: `host/mcuscope/webui/{can,statusbar,state,cmdbar,exportdlg,exportrange,app,settings}.js`,
`index.html`, `style.css`; `docs/SPEC.md` 9.1; tests `host/tests/webui_js/{exportdlg,export_paused_window,exportrange,can_logic,can_bytediff,can_freeze_surface,cmdbar_eol,cmdbar_sole,state_logic,statusbar_logic,statusbar_proto,statusbar_attach_eol}.test.mjs`,
new helper `host/tests/webui_js/exportdlg_guards.mjs`, `host/tests/test_webui.py`.

## Gates

- `node --test` from `host/tests/webui_js/`: **405 tests, 405 pass, 0 fail, 0 skipped**.
  (`node --test tests/webui_js/` from `host/` fails on this node with `Cannot find module ...tests/webui_js` for the directory argument itself, at HEAD as well as now; `cd tests/webui_js && node --test` is what `tests/test_webui_js.py` runs and is the form used here.)
- `uv run python -m pytest tests/test_webui.py -q`: **10 passed** (8 existing plus the two added below).
- `uv run python -m pytest tests/test_webui_js.py -q`: passed (declared-count floor plus the node run).
- `grep -nP '[\x{2013}\x{2014}]'` over every file above: no matches. No `console.log` in `webui/*.js` or `webui_js/*.mjs`.
- `ruff check` clean on `tests/test_webui.py` (the only Python touched).

## A. Adversarial fixes

- **W2** `state.js`: `cmdModes` is `Object.create(null)`; `state.portEol`, `state.portTarget` and the new
  `state.portConnected` start null-prototyped. `statusbar.js pollStatus` builds all three through an
  `aliasMap` helper wrapping `Object.fromEntries` in `Object.assign(Object.create(null), ...)`.
- **W3** `cmdbar.js targetAlias()` now mirrors `PortManager.resolve()`: the pick, else the sole managed
  alias, else the sole connected alias, else `"auto"`. The connected flag comes from `/status` via
  `state.portConnected` (set in `pollStatus`).
- **W6** new `tests/webui_js/exportdlg_guards.mjs` (`refuse(url)`, `installExportDaemon(env, sessions)`)
  applies the endpoints' own guards: `chan` enum and its repeated form, `id_to >= 0`, `changes` implies
  `decode`, `deadband` implies `changes` and must name an exported channel, the three format sets,
  `until_ts >= since_ts`, CAN id syntax and range. It records every export URL by **both** roads
  (fetch when a token is set, the `<a download>` navigation when not) and both test files assert
  `refusals` is empty. Used by `exportdlg.test.mjs` and `export_paused_window.test.mjs`.
- **W8** `index.html` eol select gains `<option value="">port default</option>`; `cmdbar.js syncCmdEol`
  labels it `port default (<port eol>)` and sets `sel.value = getEol()`, so picking it reaches
  `setEol("")` and removes the stored override.
- **W9** `exportdlg.js doExport` is async: it awaits `downloadPath` and closes only on success; a refusal
  goes into `$("expErr")` with the dialog open. `state.js downloadPath` now **returns** the failure
  message instead of toasting it, and `settings.js` turns it into the chip flash at its two call sites.
- **W10** `applyShownAvailability` no longer rewrites `range.mode`; a module-level `renderMode` carries
  the mode this dialog shows and exports with, and `saveRange` persists the remembered one.
- **W11** `fillSessions` writes `session <id> is no longer in the list; the range moved to the newest run`
  into `$("expErr")` before falling back; `SESSION_LIMIT` raised to 200.
- **W13** covered by a new `state_logic.test.mjs` case (see below); no production change.
- **W14** `exportrange.js` comment reworded (mode falls back whole, bounds per field) and the test renamed
  to "an unreadable mode falls back whole, an unreadable bound falls back on its own".

## B. Improvements

- **P2** `can.js`: `changedBytes(prevHex, hex)` exported; `fillCanData` renders one `<span class="byte">`
  per byte with `chg` on the ones that moved, cleared on the next tick that finds the payload unchanged
  (`L.hilite`). CSS `table.can .byte.chg` in `style.css`.
- **P3** `statusbar.js renderPorts` appends `<span class="meta target">` when `pt.target` is non-null and
  adds `p.target` to `portsSig`; the tip gains `monitor reports: <name>`.
  *Deviation:* the tip is silent when `target` is null rather than saying "no OK monitor response" - most
  ports never answer, and the missing span already says it; this also keeps every existing tip assertion.
- **P5** attach dialog gains `attachSerial` and `attachEol` with `<label for>`; `submitAttach` sends `eol`
  always and `serial_number` only when non-empty, and passes both to `saveAttachedPortToConfig`, which
  writes them into the config entry. `openAttach` resets both fields.
- **P6** `state.js downloadPath` branches: with no token and a streaming path (`*/export` or
  `/can/frames`) it clicks an `<a href download>`; with a token, or for `/sessions/{id}/bundle`, it keeps
  fetch-to-blob. See "design calls" below.
- **P9** `can.js`: `canFilterPattern(entry)` exported; the id span is a span-button (`makeSpanButton` from
  digital.js plus a click handler) calling a `setPaneFilter` hook, wired in `app.js` to terminal.js's
  `filterPaneTo` (which has since landed; the `typeof` guard stays). `#canFilterClear` in the CAN sub-head
  appears with the filter and clears it.
- **P12** `can.js` is a freeze surface: `canFrozen` snapshot plus `canFrozenId`/`canFrozenNow`/
  `canFrozenVersion` on pause, a single `canModel()` seam every reader goes through, `registerSurface("can", …)`,
  a `#canPause` button and `#canPausedTag` in the sub-head, the 1 s tick skipped while paused, and
  `openCanExport` passing the real watermark plus `canShownLastMs()` (oldest frozen row to the freeze).
  Clearing a paused table empties the snapshot without resuming it.
- **P13** `statusbar.js`: `portRate(prev, rx, dtSeconds)` exported (null on the first poll, a counter
  reset, a non-positive dt or a non-finite count), and `renderPortRates` writes `<N>/s` into a cached
  `.rate` span per chip. The rate is deliberately **not** in `portsSig`.
- **fillSessions** the dialog stores the fill promise in `sessionsReady` and `doExport` awaits it, so an
  early Export carries the remembered session. The dialog still opens immediately (see design calls).
- **SPEC 9.1** updated: port chip (`target`, per-port rate), attach dialog fields, CAN byte highlight,
  id-click filter, the CAN table in the pause-all surface list and its shown-window export mode,
  `/sessions?limit=200` and the missing-session notice.

## Revert verification

Each production line was reverted in place (file copied aside with `cp`, restored from the copy after the
run) and the owning test file run with `node --test`. Harness: `/tmp/rev-2026-09-12/revert_check.py`.
**27 of 27 caught**, plus 3 markup reverts driven against `tests/test_webui.py`.

| reverted | test that failed |
|---|---|
| W2 `cmdModes` plain object | state_logic: an alias that shadows Object.prototype is a port like any other |
| W2 `/status` maps plain | statusbar_proto: a port aliased constructor is not the Object constructor |
| W2 `portConnected` plain | statusbar_proto: same |
| W3 sole-connected clause dropped | cmdbar_sole: under auto a sole connected port among several is what the bar aims at |
| W6 a refused URL built (`format=xml` from can.js) | exportdlg: no URL this dialog built would be refused by the daemon (+ the CAN prefill case) |
| W8 default option not labelled | cmdbar_eol: tests 1, 2, 3 |
| W8 select pinned to the port value | cmdbar_eol: tests 1, 4 |
| W9 dialog closes before the download | exportdlg: a daemon refusal stays in the dialog, with the range that produced it |
| W10 remembered mode rewritten | exportdlg: a remembered shown range survives a panel that cannot offer it |
| W11 substitution in silence | exportdlg: a remembered session that is gone says so before falling back |
| W11 limit back to 50 | exportdlg: the sessions list reaches past the newest 50 |
| W13 value guard removed (mutation M17) | state_logic: setCmdModeFor refuses a value that is not a mode, and writes nothing |
| P2 `changedBytes` flags nothing | can_bytediff: tests 1 and 4 |
| P2 highlight never clears | can_bytediff: the moved byte is highlighted, and the highlight clears when it stops |
| P3 `target` out of `portsSig` | statusbar_logic: the chip names the board behind the port, and a new board repaints it |
| P5 `eol` dropped from the body | statusbar_attach_eol: tests 1, 2 |
| P5 blank serial sent as `""` | statusbar_attach_eol: tests 1, 3 |
| P5 config save drops the values | statusbar_attach_eol: save to config writes the values the attach just used |
| P6 no streaming branch | state_logic: a streaming export with no token is a navigation, not a buffered fetch |
| P6 the bundle streams too | state_logic: same |
| P9 bus suffix always empty | can_logic: the filter pattern is built in parseCanEvent's own grammar |
| P9 id click not wired | can_logic: clicking an id hands that pattern to the terminal, and the control clears it |
| P12 `watermark` always null | can_freeze_surface: pause all reaches the table, and resuming it alone makes the button live again |
| P12 paused table renders live rows | can_logic: tests 24, 25 |
| P13 counter reset reads negative | statusbar_logic: the chip says whether the port is actually saying anything |
| P13 rate forced into `portsSig` | statusbar_logic: same |
| `sessionsReady` not awaited | exportdlg: Export pressed before the session list lands still carries the remembered session |
| index.html: `port default` option removed | test_webui: test_cmd_eol_select_offers_the_port_default |
| index.html: `attachSerial` renamed | test_webui: test_index_declares_every_id_the_modules_resolve |
| index.html: `canPause` renamed | test_webui: same |

W14 is comment and test-name wording only: nothing to revert-drive.

## Design calls worth a second opinion

1. **P6 against W9.** P6 wants the no-token export to be an `<a download>` navigation (a 686k-line
   capture is 102 MB buffered in the tab); W9 wants a daemon refusal shown inline in the dialog. The
   navigation cannot report one - the browser saves the 4xx body under the download name. Resolution
   taken: navigation for the four streaming paths with no token (stated in the comment at
   `downloadPath`), fetch-to-blob with a token or for the bundle, and W9's inline reporting live on the
   fetch branch. The dialog's own guards (now pinned by W6's double) are what keeps a refused URL from
   being built in the default configuration. If the owner would rather never risk a silently saved error
   body, invert it: keep fetch-to-blob everywhere and drop P6.
2. **`fillSessions` await.** The considered item asks for it "before the export dialog opens". Awaiting
   before `showModal()` means a daemon that accepts the connection and then stalls produces no dialog at
   all (the class-12 shape). Implemented instead as `sessionsReady`, awaited by `doExport`, which closes
   the same hole without that failure mode.
3. **P9 pattern.** Built as `^!can<bus> \d+ \S+ (?:0[xX])?0*<id> `: the leading-zero and `0x` tolerance is
   beyond P9's literal wording (which says "zero-padded to 8 the way fmtCanId does"), because the table
   shows the padded form while the wire may carry `23`, `023` or `0x23` for the same id. The hex digits
   are the daemon's own upper case, so a device writing lower-case id letters is not matched - listed
   under manual-verify.
4. **P9 clear control.** One `unfilter` button in the CAN sub-head, hidden until an id is clicked, rather
   than a second button on every row (256 rows, one control each).

## CHANGELOG lines (not applied - CHANGELOG.md is owned by another batch)

```
- Web UI: the CAN table highlights the payload bytes that moved since the previous frame for that id, and the highlight clears when the id goes quiet.
- Web UI: clicking a CAN id filters the last terminal pane to that id's raw frames; an `unfilter` control in the panel head clears it.
- Web UI: the CAN table is a pause-all surface, with its own pause button; a frozen table exports the window it shows (`id_to`), including the shown-window range mode.
- Web UI: the port chip names the board behind the port (`target` from `OK monitor`) and shows its lines/s, so a silent board and a moved probe are both visible.
- Web UI: the attach dialog offers a line ending and a serial number, sends both, and "save to config" writes the values the attach used (a CRLF board no longer lands on lf).
- Web UI: the command bar's line-ending select has a "port default" entry again, labelled with the value the port will actually append, so an override can be dropped without clearing site data.
- Web UI: exports with no access token configured stream straight to disk instead of being buffered whole in the tab.
- Web UI: an export refused by the daemon keeps the dialog open with the reason beside the range that produced it, instead of closing and flashing a toast.
- Web UI: the export dialog no longer forgets a remembered `shown` range when opened from a panel that has no frozen window, says so when a remembered session has gone, and lists the newest 200 sessions rather than 50.
- Web UI: the command bar resolves `auto` the way the daemon does, including the sole connected port among several attached.
- Web UI: port aliases that shadow `Object.prototype` (`constructor`, `toString`, `valueOf`) are treated as ordinary ports by the send-mode and line-ending state.
```

## Manual-verify (a browser only), against `mcuscoped --sim` at http://127.0.0.1:8558/ui/

1. CAN panel: watch the sim's 10 Hz heartbeat on id 0x100 and confirm the counter byte lights and the tint fades on a quiet id.
2. CAN panel: click an id and confirm the last terminal pane fills with that id's frames, scrolls into view, and that `unfilter` appears and then clears both the pane and itself.
3. CAN panel: press `pause`, confirm the payloads, counts and ages stop, `paused` shows in the head, `pause all` reads `resume all`, and `resume` catches up.
4. CAN panel paused: open `export` and confirm `Shown window` is selectable and the download covers the frozen span.
5. Port chip: with the ST-LINK attached, confirm the chip shows the target name and a lines/s figure that settles, and that the figure does not make the chip jump or steal focus from the reconnect button every 5 s.
6. Port chip: move the probe to the other board, wait one poll, and confirm the target name changes on screen.
7. Attach dialog: attach a CRLF board with `Save to config`, then check Settings > Ports and the config file both read `crlf`.
8. Command bar: pick a line ending, then pick `port default` again and confirm the select reads `port default (<the port's own>)` and sends nothing extra.
9. Export a large capture with no token set and confirm the browser's own download progress appears immediately (no frozen tab while the body buffers) and the daemon's filename is used.
10. Export with a token set and a bad deadband name, and confirm the message appears inside the dialog with the dialog still open.
11. Export the session bundle from Settings with a token and confirm it still saves as `bundle.zip`.
12. A CAN id whose device writes it in lower-case hex (`!can 1 - 1abc ...`): confirm whether the id-click filter matches, since the pattern uses the daemon's upper case.
13. Tab through the attach dialog and confirm the new serial-number and line-ending fields are announced with their labels.

## Not done

- Nothing in another batch's test files was broken by these changes: the full suite is green.
  `export_paused_window.test.mjs` is shared with the terminal batch (they added the W7 `exportPane` cases
  while this batch was running). Its double was swapped for the guarded one and the module-scope
  `lastUrl` variable became `seen.lastUrl`; their four cases pass unchanged, but a concurrent edit of that
  file that reintroduces a bare `lastUrl` will need the same rename.
- `can.js` now imports `makeSpanButton` from `digital.js` (owned by the other web UI batch, not edited).
  If they move or rename that export, `can.js` needs the same one-line change.
- W2's third map, `state.portConnected`, is new state; nothing else reads it yet.
- Not attempted (out of this batch's list): W1, W5, W7, W12 and P1/P4/P7/P8/P10/P11/P14/P15.
