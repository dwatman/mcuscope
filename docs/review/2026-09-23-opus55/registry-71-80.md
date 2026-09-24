# Registry leg, classes 71-80 (2026-09-24)

HEAD `f31ecd9` (checked with `git rev-parse HEAD` before starting).
Scratch, probes and raw sweep outputs: `~/tt-data/mcuscope-2026-09-24/registry-leg/71-80/`.
Status: 71-77, 79 and 80 complete; 78 complete for Python, the 179-site JS residue owed (not ruled after the orchestrator's budget stop).

## Findings

- **R71-1 LOW** `host/mcuscope/webui/settings.js:707,714,719` (with `config.py:424-431` and `server.py:335-338`).
  - A hand-edited value the loader accepts but PUT /config/storage refuses blocks every later Storage save, including one that only edits another field.
  - The loader bounds `retention_days` and `min_sessions` at 2^63-1 and `max_db_bytes` at 2^63-1; the dialog and ConfigStorageBody bound them at 3650, 1000 and 2^42.
  - Driven (`p71.test.mjs`): `retention_days = 5000` renders "5000" and an edit of "Keep newest sessions" is refused "Retention must be 1-3650 days"; `min_sessions = 5000` likewise; `max_db_bytes = 2^42+1` renders 4194304 MB (the field's own maximum) and the save sends 4398046511105, which the daemon answers 422.
  - Belongs to class 19 (two engines validating one thing) more than 71: the loaded value round-trips exactly, it is the bounds that disagree.
- **R72-1 LOW** `host/mcuscope/webui/state.js:56` (`promptForToken` from `authFetch`, `state.js:81`).
  - A 401 on any background request (the 5 s `/status` poll, a history page) opens `window.prompt`, which takes focus and the keystrokes being typed.
  - Scenario: the daemon restarts with a token while the user types a marker; the poll's 401 opens the prompt, the rest of the marker lands in it, and Enter stores that text as the token (`setToken`), spending one of three prompts.
  - Reasoned only (code read; `window.prompt` cannot be driven under the DOM stub). Nothing reaches the target, so the harm is a lost marker and a bogus stored token. The prompt may be the only way to ask; owner should pick between exempting it and deferring the prompt to the next user action.
- **R73-1 LOW** `host/mcuscope/webui/settings.js:28-35` (`refreshConfig`), also `settings.js:833-834` (`saveAttachedPortToConfig`).
  - `/config` answers write `cfg` and the restart badge with no generation, so an older answer landing last overwrites a newer one.
  - Driven (`p73.test.mjs`): the init-time prime GET held, the dialog opened on a file needing a restart (badge shown), then the prime's answer (no restart) released: the badge hid and stays hidden until the next `/config` read.
  - Window is narrow on a local daemon (the prime answers in ms).
- **R73-2 LOW** `host/mcuscope/webui/cmdbar.js:272` (`submitMarker`).
  - The ack clears the marker input by value (`input.value === sent`), not by generation: a label retyped identically while the first send is out is cleared by the first ack and never sent.
  - Reasoned only. The ABA case settings.js `pjGen` documents; LOW.
- **R76-1 LOW** `host/mcuscope/webui/terminal.js:258-280` (`shiftWindow`) with `terminal.js:43-49` (`fmtTs` tick branch).
  - The append-only render reuses line elements keyed on row identity, but a tick-mode timestamp also reads `state.anchorTick`, which the first ticked row after clear-all sets.
  - Driven (`p76.test.mjs`): after clear-all, a tickless line shows `~2000` (the estimate against zero 0); the next line carries tick 3000 and becomes the zero; the flush keeps `~2000` beside `0`; any full render rewrites the first line to `~-1000`. The column shows two zeros until a full render.
  - Rel mode is unaffected (its zero is set by the very first row); the port-tag column already forces a full render on change (`terminal.js:661`).
- **R77-1 LOW, owner should pick** `host/mcuscope/webui/plots.js:573`, `digital.js:118,149`, `digital.js:109` (live edge).
  - The host-time nudge treats a backward wall-clock step as a repeat: every sample until the clock passes the old high-water is drawn at `last + 1e-4` s, and the lanes' live edge (`digitalLast.host`) stops for the length of the step.
  - The daemon stamps rows with `time.time()` unclamped and announces such rows as late (`store._check_stamp_order`), so the host clock does legitimately go backwards; the registry entry names only MCU resets and wraps.
  - Reasoned only (code read). Owner should pick whether host-clock steps are in scope for class 77.
- **R78-1 LOW** `host/tests/test_session_bundle.py:404`: `glob("mcuscope-session-*")` can never match on the bundle route.
  - The bundle build names both temps `mcuscope-bundle-*` (`server.py:1679-1680`); only `/sessions/{id}/export` makes `mcuscope-session-*` (`server.py:1570`).
  - Driven by the batch reader (probe listing the dir inside the failing build: two `mcuscope-bundle-*` files); prefixes re-checked by grep here.
- **R78-2 LOW** `host/tests/test_reconnect.py:1314`: `assert not port._pending` can never fail.
  - The test runs `_fail_pending` inside the write, and `_fail_pending` clears `_pending` itself (`serial_link.py:739`).
  - Driven by the batch reader: with the four `_pending.pop(seq, None)` cleanups removed from `send_command` in a private copy, the test still passes. `_fail_pending`'s clear re-checked here.
- **R78-3 LOW** `host/tests/test_serial_link_attach.py:550`: the `unhandled` recorder (asyncio exception handler after `gc.collect()`) has no positive control. Live today (the batch reader made `_store_sys` re-raise in a private copy and the test failed); the gap is the missing control.
- **R78-4 LOW** `host/tests/test_daemon_startup.py:104`: matches the thread name `mcu-sim`, set only at `sim.py:1044` and pinned by no test; a rename would make the absence check vacuous. Reasoned only.
- **R78-5 LOW** `host/tests/test_cli_contract.py:121`: "no partial file" after a daemon death cannot fail.
  - `--limit 0` is not a paged export (`paged = bool(limit or ...)`), so `log export` takes the streamed `/lines/export` path, dies on the unreachable URL (rc 3) and never calls the patched `_iter_pages_asc`, so `run.txt` is never opened.
  - Driven by the batch reader: with `_OutFile.discard` mutated to skip the removal, rc is 3 and the file is absent. `test_cli_export.py:346` (`test_a_refused_export_leaves_no_partial_file`) drives the removal after the file is open, so the guard is covered; a daemon death on the paged path specifically is not.
- **R78-6 LOW** `host/tests/test_config_api.py:287`: `update_checker.enabled is False` cannot fail: `conftest.py:19` sets `MCUSCOPE_UPDATE_CHECK=0` and `update_check.resolve_enabled` lets the environment win. Driven by the batch reader; env precedence re-read here (`update_check.py:125-128`).
- **R78-7 LOW (latent)**: 10 absence checks live today but with no positive control, each driven live by the batch reader: `test_store_lines_plan.py:44, 356` (`sqlite_stat1` probe; same query unruled at `:415, 464, 570`, `test_store_sessions.py:125`), `test_sim_pty.py:130-133` (openpty slave flags never shown set before the sim clears them), `test_capture_lock.py:163` (`os.open` spy), `test_store_writer.py:77` ("store writer died" in caplog), `test_config_api.py:95` (`[[ports]]` literal), `test_sessions.py:82` (reasoned). Fixes per site in `verdicts_py2.md`.
- **R75-1 LOW (latent)** hand-kept "every X" lists that are complete today but not derived:
  - `tests/test_server_live_verdicts.py:123` READS and `:152` WRITES: derived from OpenAPI (6 query-`port` GETs) and from the `_resolve_port` callers (5): both complete.
  - `tests/webui_js/settings_loading_hold.test.mjs:67` FIELDS ("every daemon-owned field"): matches `index.html`'s 11 `cfg*` inputs minus the token.
  - `tests/webui_js/exportdlg_guards.mjs:109` DECLARED ("Every query parameter each route declares"): equal to OpenAPI for all three routes, same order.
  - Inline parametrize lists the registry sweep does not reach: `test_server_export_windows.py:276` (4 of 4 `since_ts` routes), `test_server_scope.py:90` (3 of 4 `limit` routes, the fourth in a sibling), `test_sim_flags.py:16` (3 of 3 `int_arg` flags).
  - Confirmed by derivation scripts `derive75.py`, `derive75b.py`, `derive75c.py`; nothing is missing now, a new endpoint or field would not be caught.
- **R75-2 LOW (latent)** stdlib-derived sets hand-kept:
  - `tests/test_render_line_breaks.py:11` BREAKS claims "Every boundary str.splitlines() honours": equal to the derived set today (checked over all code points).
  - `tests/test_protocol_tokenizer.py:14` and `tests/webui_js/state_marker_tick.test.mjs:15` NON_SPACE_WS claim "Bytes str.split() treats as whitespace": it is neither the bytes set nor the str set (str.split also splits on U+0085, U+00A0, U+1680, U+2000-200A, U+2028/9, U+202F, U+205F, U+3000).
  - Driven: `parse_can_event`, `parse_response`, `PlotDecoder.learn/points` and `parse_marker` refuse all 27 non-space whitespace code points, and JS `lineTick` refuses all 27 JS `\s` members plus U+001C-001F and U+0085. No live defect.

## Class 71: a field rendered in coarser units than stored, saved back rounded

Sweep: `grep -n "Math.round\|MiB\|/ 1000\|\* 1000" host/mcuscope/webui/settings.js`: 3 sites.

- `settings.js:150` comment: exempt, not code.
- `settings.js:158` cap rendered in whole MiB: complies. `capShown` keeps the loaded bytes and `saveStorage` (`:717`) sends them back while the field still reads the rendered MB. Driven: a 1.5 MiB cap renders "2" and an unrelated save sends 1572864.
- `settings.js:319` `fmtWhen` ts to locale string: exempt, display only, no control.

Sweep imprecision: the grep sees only settings.js and only those four spellings. A field can also be transformed by `.trim()`, a default (`|| 115200`) or a normalisation (`isEol ? : DEFAULT_EOL`), and other dialogs render stored values into controls.
Improved: `grep -nE "\.value = |\.checked = " host/mcuscope/webui/*.js`, then rule each control that a later save reads back. Run over the settings, attach and export dialogs:

- `cfgHost`, `cfgPort`, `cfgRetention`, `cfgMinSessions`, `cfgAutoSession`, `cfgUpdateCheck`: rendered raw, sent raw or through `intField` (integers only); complies.
- `cfgDbPath` `|| ""` and `.trim()` on save: complies, `resolve_db_path` strips too (`config.py:154`), so a padded path resolves the same.
- Port rows (`settings.js:502-573`): alias, device and serial trimmed (the loader strips them, `config.py:469-472`); baud `|| 115200` (the loader never yields 0); eol normalised to `lf` (the loader already reads a bad value as lf); `identify` omitted keeps the saved value; complies.
- `cfgPjDest` trimmed on save: complies, the daemon echoes its own value back.
- Export dialog `expFrom`/`expTo` (`exportdlg.js:39-44,261-262`): rendered to whole seconds, but the stored range only ever comes from these fields (`toEpoch`), so it is whole seconds already; complies.
- `app.js:159`, `layout.js:31,49` round layout sizes: exempt, not a field rendered from a stored value and saved back.

Adjacent: R71-1 (loader and PUT bounds disagree).

## Class 72: a background refresh that moves focus

Sweep: `grep -n "\.focus()" host/mcuscope/webui/*.js`: 8 sites.

- `app.js:76` collapse button click: complies.
- `app.js:82` reopen button click: complies.
- `cmdbar.js:133` `if (remember)`: complies; `remember` is true only from the mode buttons' click (`:287`); the status-poll path `:98` passes false.
- `exportdlg.js:232`: complies; `openExportDialog` runs synchronously from the four export buttons (`can.js:656`, `terminal.js:612`, `digital.js:497`, `plots.js:1378`), before any await.
- `chrome.js:205` `rovingRadios` keydown: complies.
- `terminal.js:718` match-clear click: complies.
- `plots.js:540` `refocus` true only from the rename input's Enter/Escape keydown: complies.
- `plots.js:549` `startRename`, from the title click and its span button (`:689-690`): complies.

Sweep imprecision: focus also moves through `showModal()`, `.select()`, `autofocus` and `window.prompt`.
Improved: `grep -n "\.focus(\|showModal\|\.select()\|autofocus\|window.prompt" host/mcuscope/webui/*.js host/mcuscope/webui/index.html`: 7 further sites.

- `exportdlg.js:229` showModal: complies (as :232).
- `statusbar.js:190` `showDlg`, callers `:221` (session dialog from `toggleSession`, a click) and `:583` (attach dialog, opened before its await): complies.
- `statusbar.js:222` `.select()` in `openSessionDialog`: complies.
- `plots.js:550` `.select()` in `startRename`: complies.
- `settings.js:755` showModal before the awaits: complies.
- `index.html:143,193,225` autofocus: complies, they act only through the showModal calls above.
- `state.js:56` `window.prompt` from any 401: violates, R72-1.

## Class 73: an awaited result written into a view replaced while it was in flight

Sweep: `grep -n "await " host/mcuscope/webui/*.js`: 67 sites; each function read.

- `cmdbar.js:202, 222`: complies, `report` checks `gen === cmdGen`.
- `cmdbar.js:270`: violates in the ABA case, R73-2; the strip write checks `current()`.
- `exportdlg.js:153` `fillSessions`: complies, `fillGen`.
- `exportdlg.js:248`: complies, `ctx === mine`.
- `exportdlg.js:253, 281`: complies, `dialogGen` and `openCapture` after each await.
- `state.js:75, 85, 97, 99, 110` (`authFetch`, `api`, `refusalText`): exempt, transport helpers that write no view.
- `state.js:366, 367, 369` `preflight`: exempt, writes no view.
- `state.js:402, 413, 414, 415` `downloadPath`: complies, `wanted()` is the caller's token.
- `api.js:287` `seedPlotDefs`: complies, `wsGen` per page; the definitions are deliberately not clear-gated.
- `api.js:358, 365` `seedChannelList`: exempt, returns its answer; its caller checks.
- `api.js:390, 400, 411` `seedPlotHistory`: complies, `wsGen` and the caller's `cleared` token taken before the first await (`api.js:542`).
- `api.js:492` `fetchSince`: complies, `wsGen` per page.
- `api.js:554, 558, 567, 576` `runBackfill`: complies, `wsGen` after each await, clear tokens snapshotted before the first. A capture reset mid-backfill cannot start a second backfill on the same `wsGen`: the capture token is staged and handled in the drain after this backfill resolves.
- `statusbar.js:204` `toggleSession`, `536, 547, 558` port actions: complies, `failGen` via `clearFailureSince`, then `refreshStatus(true)`.
- `statusbar.js:234` `startSession`: complies, `sesGen`; the unconditional `closeDlg` is documented as deliberate.
- `statusbar.js:494` `pollStatus`: complies, single-flight (`statusInFlight`).
- `statusbar.js:581` comment: exempt.
- `statusbar.js:586` `openAttach`: complies, `populateDevices` returns false for a superseded fill.
- `statusbar.js:631` `populateDevices`: complies, `devicesGen`.
- `statusbar.js:710` `submitAttach`: complies, `attachGen`.
- `terminal.js:506, 525, 529` `loadHistory`/`loadHistoryPage`: complies, `historyGen`; every path that replaces `pane.rows` calls `resetHistory` (`api.js:196`, `terminal.js:377, 726, 854`); the flush trim at `:421` runs only on live panes, and history loads only on paused ones.
- `settings.js:30` `refreshConfig`: violates, R73-1.
- `settings.js:40` `loadDevices`: exempt, returns its answer; `openSettings` checks `openGen`.
- `settings.js:176` `renderDbNow`: complies, `dbNowGen`.
- `settings.js:225` `renderPj`: complies, `pjGen`.
- `settings.js:241` `applyPj` success path: complies with a note: no `pjGen` check, but it writes only a control still reading what this request sent, and every later apply resends the whole state.
- `settings.js:255` `applyPj` refusal path: complies, `pjGen`.
- `settings.js:267, 268` `savePjDefault`: complies, `openGen`.
- `settings.js:299` `renderUpdateNow`: complies, `updateNowGen`.
- `settings.js:400` export button: complies, holds keyed by path.
- `settings.js:435, 436` `deleteSession`: complies, `sessionsGen`.
- `settings.js:452` `renderSessions`: complies, `sessionsGen`.
- `settings.js:656` `putConfigNow`: complies, `openGen`.
- `settings.js:674, 676` `saveSection`: complies, `openGen`.
- `settings.js:699, 724, 733, 742`: complies, wrappers over `saveSection`.
- `settings.js:764` `openSettings`: complies, `openGen`.
- `settings.js:833, 842, 848` `saveAttachedPortToConfig`: `:833` violates as part of R73-1 (badge from an unordered GET); `:842` complies (a stale revision is refused 409, as SPEC 3.3.1 wants); `:848` as R73-1.

Sweep imprecision: `.then(` chains are async without the keyword.
Improved: add `grep -n "\.then(\|new Promise" host/mcuscope/webui/*.js`: 5 sites.

- `api.js:224, 681` `.then(() => drainStaging(gen))`: complies, `drainStaging` checks `staging.gen`.
- `terminal.js:753` clipboard flash: complies, line elements are never recycled for another row.
- `settings.js:646` `putChain`: complies, serialisation only.
- `statusbar.js:482`: complies, re-enters `refreshStatus`.

## Class 74: a limit shown beside a figure it is not measured against

Sweep: `grep -n "max\|cap\|limit" host/mcuscope/webui/statusbar.js host/mcuscope/webui/settings.js host/mcuscope/cli.py`: 174 sites. Bucketed mechanically (`s74_class.txt`), then the candidates read.

- 59 exempt, noise: the only match is inside "capture"/"Capture": `statusbar.js:185, 245, 404, 410, 429, 434`; `cli.py:170, 203, 456, 809, 953, 1098, 1100, 1225, 1428, 1450, 1457, 1532, 1542, 1597, 1601, 1603, 1664, 1669, 1709, 1713, 1715, 1726, 1767, 1955, 1965, 2102, 2165, 2166, 2171, 2199, 2244, 2245, 2248, 2251, 2252, 2276, 2426, 2435, 2591, 2676, 2691, 2754, 2890, 2898, 2956, 3026, 3030, 3087, 3194`; `settings.js:382, 414, 419, 432`.
- 14 exempt, noise: `max(`/`Math.max(`/an option's `max=` bound: `statusbar.js:13`; `cli.py:523, 755, 762, 812, 1432, 1438, 1939, 2143, 2256, 2520, 2526, 2669, 2686`.
- Display sites, each pairing the cap with the enforced figure:
  - `statusbar.js:85, 87` daemon chip title: content / cap, file size only as "on disk": complies.
  - `statusbar.js:251, 258, 259, 263` status-bar db warning: content / cap: complies.
  - `settings.js:159, 180, 186` cap field and its hint "now <content>": complies.
  - `settings.js:715` refusal names the field's own range: complies.
  - `cli.py:973, 976, 1997, 1998` `note_truncated`: complies, names the count that came back (`cli_output.py:498-501`).
  - `cli.py:1306-1308` subscriber cap message: complies, no figure.
- Everything else is exempt, not a displayed limit:
  - Comments and docstrings: `statusbar.js:69, 246, 247`; `settings.js:147, 148, 150, 167, 168, 179, 711, 788`; `cli.py:101, 160, 628, 633, 664, 668, 673, 811, 1252, 1253, 1286, 1295, 1643, 1909, 2104, 2272, 2275`.
  - Request parameters and paging arithmetic: `cli.py:582, 588, 592, 627, 629, 632, 642, 648, 662, 675, 682, 701, 713, 747, 806, 958, 961, 1255, 1572, 1918, 1921, 1935, 1946, 1950, 1951, 2182, 2192`; `settings.js:452`.
  - Option declarations and help text: `cli.py:935, 936, 940, 1568, 1892, 1893, 2029, 2087`; guide text `cli.py:2891, 2896, 2947, 2958, 2980, 2981, 2983, 2989, 3028, 3073`.
  - Cap bookkeeping, no display: `settings.js:152, 157, 158, 169, 712, 713, 714, 717, 722, 725`.

Sweep imprecision: "cap" matches "capture" (59 of 174), and the limit displays in `can.js`, `digital.js`, `plots.js`, `terminal.js` and `cli_output.py` are outside the files.
Improved: `grep -nwiE "cap|limit|max_[a-z_]+|MAX_[A-Z_]+" host/mcuscope/webui/*.js host/mcuscope/cli*.py | grep -iE "textContent|title|print|err\(|die\(|echo"`. Run; the extra display sites:

- `terminal.js:669` panes `n / MAX_PANES`: complies.
- `can.js:332` `(limit MAX_CAN_IDS)` beside the id count the cap is enforced on: complies; the latch resets on clear (`can.js:690`).
- `digital.js:346` lanes, `plots.js:868` channels: complies, same shape, latches reset at `digital.js:933`, `plots.js:1489`.
- `cli_output.py:500-501`: complies (as `note_truncated` above).

## Class 75: a hand-kept list standing in for a mechanical enumeration

Sweep: `grep -n "^[A-Z_]* = \[\|^[A-Z_]* = (" host/tests/*.py`: 37 sites; `grep -nE "^(export )?const [A-Z_0-9]+ = (\[|new Set\(\[|Object\.freeze\(\[)" host/tests/webui_js/*.mjs`: 30 sites.

Python:

- Derived against the source, complies: `test_cli_export.py:60` WINDOWED, `test_cli_export_files.py:34` EXPORTS, `test_cli.py:2323` LIST_FIELDS, `test_scaffold.py:60` CONSOLE_SCRIPTS, `test_eol.py:222` EOL_BODIES, `test_cli_version_gate.py:18` BOUNDED, `:176` GATED (flag set derived by `test_the_gate_lists_name_every_option_the_cli_gates`; commands per flag checked now: `--eol` 5 of 5, `--repeat-ms` 1 of 1), `:267` GATES, `test_webui_js.py:59` GUARD_URLS (every refusal clause of the double derived and reached), `test_protocol.py:705` _DECIMAL_POSITIONS (reach guard at `:795-806`).
- Hand-kept "every", complete today: `test_server_live_verdicts.py:123` READS, `:152` WRITES: R75-1.
- Hand-kept stdlib set: `test_protocol_tokenizer.py:14` NON_SPACE_WS, `test_render_line_breaks.py:11` BREAKS: R75-2.
- Fixtures, no coverage claim, exempt: `support.py:334`, `test_cli_contract.py:26`, `test_cli_export.py:24` (UNREACHABLE argv); `test_cli_closed_pipe.py:71` USAGE (usage-path sample); `test_cli_export_files.py:152`, `test_cli_version_gate.py:238` (canned responses); `test_flow_cli_windows.py:86, 214` (paging-path forms); `test_plotjuggler.py:90`, `test_timeline.py:144`, `test_store_plot_reads.py:48` (data); `test_protocol.py:702, 912`, `test_sim_flags.py:13`, `test_server_exports.py:448`, `test_plot.py:409` (hostile-input samples); `test_reconnect.py:285, 286`; `test_firmware_monitor.py:31` (compiler flags); `test_cli_start_index_build.py:20, 151` (notice text); `test_store_lines_plan.py:51, 58` (plan shapes; until_ts and session bounds covered at `:482`).

JS:

- Derived, complies: `settings_offline.test.mjs:34` DAEMON_CONTROLS, `smoke.test.mjs:20` ORDER.
- Hand-kept "every", complete today: `settings_loading_hold.test.mjs:67` FIELDS: R75-1. `exportdlg_guards.mjs:14` CHANS (equal to `server.py:256` Chan), `:15` BOOLS, `:92, 97, 102` *_SPEC: the double's grammar, pinned only by the sampled GUARD_URLS; noted under R75-1 with DECLARED (`:109`, not a sweep hit: `const X = {`).
- Hand-kept stdlib set: `state_marker_tick.test.mjs:15` NON_SPACE_WS: R75-2.
- Fixtures, exempt: `api_backfill_clear:22`, `api_plot_def_seed_ports:13, 17`, `api_plot_def_seed:22, 28`, `can_table:99`, `exportdlg_session_fill:10`, `exportdlg_preflight_busy:10`, `exportdlg:14`, `state_download_preflight:11`, `settings_revision:195`, `state_download_navigate:9`, `clear_staged_backfill:27, 32, 158`, `plots_seed_paused:16, 33`, `api_plot_seed:26, 61, 65`; `plots_seed_order:34` PALETTE (a copy of `chrome.js:12` PLOT_COLORS whose drift fails loudly).

Sweep imprecision: it misses dict constants (`DECLARED = {`, `CASES = {`), names with digits, and inline `@pytest.mark.parametrize` lists (179 sites).
Improved: `grep -nE "^[A-Z_0-9]+ = [\[\(\{]|^const [A-Z_0-9]+ = [\[\{]" host/tests/*.py host/tests/webui_js/*.mjs` plus `grep -nE "parametrize\([^)]*\[\s*\(?\s*\[?\"(--|/|-[a-z])" host/tests/*.py` for flag and path lists (14 sites, ruled: 11 samples with no "every" claim; `test_server_export_windows.py:276`, `test_server_scope.py:90`, `test_sim_flags.py:16` complete and listed under R75-1). The other 165 parametrize sites were not ruled.

## Class 76: a view cache or rebuild key missing an input the view reads

Sweep: `grep -n "Version\b\|View\b\|needsRebuild\|!==.*prev" host/mcuscope/webui/*.js`: 30 sites.

- Noise, exempt: `app.js:33, 42` (`setView`, the layout toggle); `statusbar.js:100, 102, 103, 115, 140` (page and update versions); `terminal.js:894` (`scrollIntoView`); `plots.js:223` (`DataView`); `can.js:720` comment.
- `can.js:29, 30, 32, 48, 152, 161, 291, 298, 313, 323, 325, 326, 327, 340, 426, 538, 540, 552, 689, 695`: one memoised view. The key is `{version (rows or frozen), filter, layout (collapse)}`; `buildCanTable` also reads `multi` (derived from the entries), `portColor` (stable per port), and `canCapWarned` (bumps `canRowsVersion` when set, `:152-154`): complies.

Sweep imprecision: it misses the digital lane repaint predicate, the chart rebuild predicate (the bit site) and the terminal's append-only reuse.
Improved: `grep -nE "Version\b|View\b|needsRebuild|!==.*prev|drawn[A-Z]\w*|sizeChanged|const need\b|shiftWindow|\.dirty\b" host/mcuscope/webui/*.js`. Ruled:

- `digital.js:548-549` lane repaint key `{dirty, _sizedirty, width x dpr, drawnEdge, theme}`: complies. Window, time mode and zoom mark `_sizedirty` (`digital.js:391`, `terminal.js:821`, `plots.js:975, 985`); show and colour mark `dirty`; the ruler redraws on any lane draw or width change.
- `plots.js:1167-1170` chart rebuild predicate `{uplot, series count, theme}`: complies. Axis, range and value formatters are closures reading live state; a unit change on a single shown trace, the single-trace boundary and a colour change rebuild explicitly (`plots.js:632, 788, 800, 763`).
- `terminal.js:258` `shiftWindow`, keyed on row identity: violates, R76-1 (`state.anchorTick`). Its other inputs: time mode (full render on change), port-tag column (full render, `:661`), regex (rebuild), `tickAnchors` (earlier anchors only, stable for a rendered row).
- `terminal.js:245` `renderEmpty` text compare: complies.

## Class 77: a monotonic fix-up applied to a clock that legitimately restarts

Sweep: `grep -n "lastTick\|+ 1e-4\|Math.max(.*tick" host/mcuscope/webui/*.js host/mcuscope/*.py`: 11 sites.

- `timewindow.js:226` widest label: exempt, noise.
- `timewindow.js:355` `continueTick` new epoch: complies, it is the restart handler; the host gap is clamped at 0.
- `digital.js:118` host nudge: host half is R77-1.
- `digital.js:119` tick nudge: complies, the tick is already continued by `continueTick` (`digital.js:86`) with a null break vertex on restart.
- `digital.js:149` `noteLaneId`: ticks continued, complies; host nudge part of R77-1.
- `plots.js:457` initial state: exempt.
- `plots.js:573` host nudge: R77-1.
- `plots.js:574, 576` tick nudge: complies, after `continueTick` (`:569`) with `breakChart` on restart.
- `plots.js:642, 643` `breakChart` gap point: complies, a deliberate one-point break.

Sweep imprecision: misses the live-edge maxima and the Python time checks.
Improved: `grep -nE "(<=|<|>=|>) *[a-zA-Z_.]*(last|prev|Last|Prev)[a-zA-Z_.]*(ts|tick|Tick|host|Host|Ts)\b|max\([^)]*(ts|tick)[^)]*\)" host/mcuscope/webui/*.js host/mcuscope/*.py`: extra sites `timewindow.js:303` (anchor thinning keeps a reset, complies), `digital.js:109, 110` (live edge: tick complies, host R77-1), `api.js:793` (id floor, exempt), `cli.py:755` (window floor, exempt), `store.py:1026` (late-row accounting: announces a backward host step, complies), `server.py:3321` (window bound, exempt).

## Class 78: a negative assertion whose observation point never receives the thing

Python complete, JS residue owed. Method, following the 2026-09-15 sweep (`docs/review/2026-09-15-prerelease/sweep-tests.md`):

- Registry greps: `tests/*.py` 673 sites (`s78py.txt`), `tests/webui_js/*.mjs` 655 sites (`s78mjs.txt`).
- Mechanical pass (`c78py.py`, `c78js.mjs`, output `c78py_out.txt`, `c78js_out.txt`): a site whose test holds a positive assertion on the same observed expression, or on the same root variable, is ruled complies without reading; a sibling test in the same file asserting the same expression positively is ruled complies (sibling control).
  - Python: 311 same-expression, 126 same-root, 50 sibling; 186 read by hand (150 no control found, 21 no negative node, 12 `.exists()` forms, 3 module-level).
  - JS: 175 same-expression, 183 same-root, 118 sibling; 179 to read (170 no control found, 9 outside a `test()` body).
- Python residue, 186 sites read by two opus-medium readers, per-site lists in scratch `verdicts_py1.md` (93: 70 complies, 19 exempt, 4 violate: R78-1..4) and `verdicts_py2.md` (93: 47 complies, 34 exempt, 12 violate: R78-5..7).
- JS residue, 179 sites (`batch_js1.txt`, `batch_js2.txt`): not ruled, owed. A reader could not be started (subagent limit), then the orchestrator stopped new agents.

Monkeypatch targets against `from .mod import name` bindings (`fromimport.py`): 404 patch sites, 154 targets, 10 bound by name elsewhere; all comply.

- `serial_link.cached_comports` x13 (bound in server): every test drives serial_link's own `SerialPort`/`port_identity`; the server-side tests patch `server_mod.cached_comports` (`test_serial_link_devices.py:69, 99`).
- `pidfile.pid_file_path`, `pid_running`, `read_pid_record`: every by-name import is function-local (`cli.py:2792`, `cli_daemonctl.py:42, 221, 239, 424`), resolved at call time.
- `config.replace_atomic` (bound in cli_daemonctl, update_check): the test drives `config.save_update` and asserts `seen` non-empty.
- `cli_daemonctl._request_shutdown`, `_status_body` (bound in cli): the test drives `cli_daemonctl._stop_running_daemon`.
- `cli_client.DEFAULT_URL`: patched on both modules (`test_cli_ux.py:27-28`).
- `cli_output._silence_stdout`: the driven path is cli_output's own global.
- uvicorn `WebSocketsSansIOProtocol`: imported inside `_enable_ws_backpressure` (`server.py:164`), and the test asserts the warning it causes.
- 27 attribute-of-attribute or instance roots (`cli.subprocess`, `Store`, `store_mod.sqlite3`, ...): exempt, patched on the shared object.
- `platformdirs.user_*_dir` string patches: `dirs.user_dir` imports platformdirs at call time; conftest clears `MCUSCOPE_*_DIR` (`conftest.py:37-45`).

Child spawns (AST over `subprocess.run/Popen`, `check_*`, `mcu_sim.spawn`, `spawns.py`, `spawns2.py`): 63 sites.

- 36 comply, env from `child_env`: 15 direct (`test_break.py:165`; `test_cli.py:580, 988, 2418, 2541, 2548, 2558`; `test_cli_closed_pipe.py:141`; `test_cli_closed_stdio.py:24`; `test_daemon_process.py:111`; `test_scaffold.py:94`; `test_sim_pty.py:49, 95`; `test_sim_tcp.py:284`; `test_stdio.py:342`), 3 via `_spawn_env` (`test_cli.py:873, 883, 891`), 18 via a local `env = child_env(...)` (`test_cli.py:67, 82, 523, 832, 855, 967, 1301, 1889, 1919, 2713`; `test_cli_closed_output.py:69`; `test_cli_closed_pipe.py:46, 58`; `test_cli_daemonctl.py:471`; `test_cli_ux.py:322`; `test_dirs_override.py:130`; `test_pidfile.py:146`; `test_port_column_stored.py:132`).
- 27 exempt, no console entry and no user dir resolved: `mcu_sim.spawn` threads (`test_break.py:85`; `test_sim_tcp.py:59, 75, 98, 109, 115, 127, 236, 324`); node, make and cc (`test_webui_js.py:34, 309, 343`; `test_firmware_monitor.py:49, 84, 125`); python children with no mcuscope (`support.py:471`; `test_cli.py:204`; `test_cli_daemon_stop_scope.py:77, 198, 431`; `test_daemon_process.py:32, 56`; `test_pidfile.py:89, 283`); `test_capture_lock.py:84` (explicit-path CaptureLock); `test_cli_ux.py:469` and `test_scaffold.py:149` (in-child `cli.main`, no crash handler, reads no user dir).

## Class 79: a module cycle that loads in only one import order

Sweep: `node --test tests/webui_js/module_load_order.test.mjs` (from `host/`): 20 tests, 20 pass: the plausibility guard, 18 modules (every `webui/*.js`, enumerated by `readdirSync`) each imported first in a fresh process, and a positive control that the probe reports a module throwing at import (`:57`). Complies.

## Class 80: a clear gate on one delivery path, with a queue fed beside it

Tokens: `clearGen` per pane (`terminal.js:724, 852`, `api.js:194`), `canClearGen` (`can.js:683-687`), `plotSeedGen` (`plots.js:1472-1475`, also bumped by `clearAllDigital`, `digital.js:920`).
Sweep: `grep -n "staging\|queue\|pending\|frozen" host/mcuscope/webui/*.js`: 192 sites, bucketed by the structure each names (`s80.txt`), every structure then traced against the three tokens.

- `staging`, 13 sites (`api.js:628, 666, 700, 709, 724, 759, 787, 789, 821, 822, 825, 826, 849`): complies. `noteClears` records per-surface cuts and id floors; the drain gates each staged row by its arrival number; a dropped area leaves floors the next backfill reads.
- `pendingGap`, 6 sites (`api.js:233, 238, 246, 251, 252, 758`): complies. The daemon puts the notice at the head of a frame with rows behind it (`server.py:2246-2248`), handled in one synchronous pass; a staged notice's divider takes the id below its successor row, whose pane cut `feedStaged` has already raised; `armStaging` zeroes it.
- `pane.queue`, 10 sites (`api.js:145, 146, 195`; `terminal.js:386, 412, 416, 417, 418, 725, 853`): complies, emptied by the pane clear, clear-all and the capture reset.
- `pane.pending`, 9 sites (`pane.js:29`; `api.js:137, 138`; `terminal.js:180, 309, 384, 385, 410, 411`): complies, zeroed by each clear; `countPending` filters by `clearId`.
- `pane.frozenRows`/`frozenId`, 13 sites (`pane.js:36, 37`; `terminal.js:161, 162, 231, 232, 313, 317, 321, 363, 369, 371, 614`): complies, `rebuild` and `renderEmpty` filter the snapshot by `clearId`; the capture reset drops it.
- `canFrozen`, 1 site (`can.js:122`) plus `can.js:45-48`: complies, `clearAllCan` empties the snapshot.
- Digital snapshots, 9 sites (`digital.js:167, 170, 181, 219, 439, 468, 473, 873, 876`) and the related `other` lines `digital.js:54, 97, 144, 213, 628, 759`: complies, `clearAllDigital` drops lanes, `laneIds` and `digitalFrozen`.
- `chart.frozen`, 5 sites (`plots.js:459, 1099, 1328, 1334, 1381`): complies, `clearAllCharts` destroys the charts.
- `pendingVal`, 5 sites (`digital.js:101, 555, 556, 874, 908`): complies, per lane, dropped with the lanes.
- Exempt, pointer and UI state rather than rows: `pendingCursorX/ClientX` 14 sites (`digital.js:727, 729, 810, 811, 814, 815, 823, 824, 832, 833, 854, 855, 930, 931`); `pane.js:27, 28` (model fields, ruled with queue and pending); `can.js:723` (paused ticker); `cmdbar.js:168, 218` (result strip); `exportdlg.js:217`; `settings.js:647`; `plots.js:1229, 1247, 1315`; `api.js:638, 654` (backoff timers); `terminal.js:315` (resume rebuild).
- Exempt, comments: 88 sites (`s80.txt`, bucket "comment").

Stub drives run, single files from `host/`, all pass: `clear_staged_backfill` 23, `api_backfill_clear` 4, `api_backfill_clear_tokens` 4, `api_seed_clear_reset` 2, `digital_clear_seed_gen` 2, `plots_clear_seed_gen` 1.
Owed: the registry's browser drive (`page.route` holding `/lines`, clear, release) was not run in this leg.

Sweep imprecision: the view feeders `buffer` (the shared ring), history pages and seeds are not matched.
Improved: add `buffer|history|seed` to the pattern. Ruled: the shared `buffer` is filtered by `clearId` on every rebuild; history pages carry `since_id=clearId` and `historyGen`; the plot history seed checks `plotSeedGen` taken before the backfill's first await; `seedPlotDefs` is deliberately not gated (definitions survive clears, `api.js:198-204`).

## The two questions

1. What am I least confident about?
   - The class 78 mechanical rulings (963 sites ruled complies without reading). A positive on the same root can observe a different field, and several matches are value assertions rather than absences. Rechecked by sampling 12 Python and 10 JS same-root sites: every one was a real control or an exempt value assertion, none a masked violation; a 22-site sample does not prove the other 941.
   - The 179 JS residue sites are not ruled at all (owed).
   - R72-1 and R77-1 are reasoned, not driven (`window.prompt` and a wall-clock step cannot be driven under the DOM stub); R73-2 and R78-4 are reasoned too.
   - Class 74's 101 candidate lines were ruled in groups by reading, not one by one.
2. What should we have checked and have not?
   - Class 80's browser drive (`page.route` holding `/lines`, clear, release) and class 72 in a real browser: owed.
   - The 165 inline `@pytest.mark.parametrize` lists that the class 75 grep cannot reach; only the 14 flag and path lists were ruled.
   - Beyond class 77's scope: what a backward host-clock step does to the daemon's `since_ts`/`last_ms` windows. The store announces late rows, but no leg checked the query side. Candidate for the class 83 leg.
   - Windows: nothing in 71-80 is platform-gated except the child-spawn XDG caveat recorded on 2026-09-15 (Windows ignores XDG; `child_env` also sets the `MCUSCOPE_*_DIR` overrides, which do apply there).
