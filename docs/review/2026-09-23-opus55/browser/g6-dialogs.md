# Browser checks: g6-dialogs

Headless Chromium (Playwright 1.62.0) against `mcuscoped` 0.5.0 at 91d3649, throwaway config and `MCUSCOPE_*_DIR` per item, port 8811. Scripts: `~/tt-data/mcuscope-tools/browser/`; outputs and screenshots: `~/tt-data/mcuscope-2026-09-25/browser/<group>/<item>/`. Run 2026-09-25, final pass `run_all.py`.

- passed: a11y (proxy): dialogs are named by their titles; attach fields have labels and hints, serial number and line ending included (what a screen reader says needs the owner) (`g6-dialogs/a11y`)
  - dialog names [['attachDlg', 'Attach serial port'], ['sessionDlg', 'Start a session'], ['settingsDlg', 'Settings'], ['exportDlg', 'Export']]; attach fields [id, label, hint] [['devSel', 'Device', 'not listed? pick custom... and type the path'], ['baudSel', 'Baud', None], ['attachSerial', 'Serial number', 'finds the board by its USB serial number, whatever port it lands on'], ['attachEol', 'Line ending', 'appended to every line sent to this port'], ['aliasInput', 'Alias', 'its name here and in the CLI (mcu -p <alias>)'], ['saveToConfig', 'Save to config', 'also adds it to Settings > Ports']]; attach-aria.txt
- passed: adv W1: sys unticked, pane export downloads a file (no error) (`g6-dialogs/adversarial`)
  - url session=1&chan=debug&chan=cmd&chan=resp&chan=event&chan=marker&format=text; HTTP errors []
- passed: adv W5: unticking `decode values` disables `changes only` (and the deadband) (`g6-dialogs/adversarial`)
  - disabled (True, True)
- passed: adv W10: Shown window chosen on a paused chart is still selected after a CAN export in between (`g6-dialogs/adversarial`)
  - CAN dialog showed expModeSession; chart dialog reopened with Shown True
- passed: adv W11: after the remembered session is deleted, reopening export says the range moved (`g6-dialogs/adversarial`)
  - inline 'session 2 is no longer in the list; the range moved to the newest run'
- passed: adv A9: the three range radios are one group; session/from/to follow the mode; every option field has an associated label (`g6-dialogs/adversarial`)
  - radio names ['expRange', 'expRange', 'expRange']; disabled [session, from, to] per mode {'Clock': [True, False, False], 'Session': [False, True, True]}; option labels [['expOpt_format', 'Format'], ['expOpt_decode', 'decode values (enum labels, bit lanes)'], ['expOpt_changes', 'changes only'], ['expOpt_deadband', 'Deadband']]
- FAILED: adv A10: the export dialog's clock row stays inside the dialog (1600 px and 420 px windows) (`g6-dialogs/adversarial`)
  - {1600: {'dialogRight': 1030, 'toRight': 1165, 'bodyScrollW': 594, 'bodyClientW': 458}, 420: {'dialogRight': 403, 'toRight': 612, 'bodyScrollW': 594, 'bodyClientW': 384}}; export-clock-*.png
- passed: adv A11: Escape closes the export dialog without saving the range (Clock picked, not exported) (`g6-dialogs/adversarial`)
  - closed True; stored range unchanged True
- passed: adv A12: Export clicked before /sessions answers exports the remembered session (`g6-dialogs/adversarial`)
  - remembered 3; url session=3&format=text
- passed: attach: device select focused on open; the alias follows the device; Enter attaches, a held Enter attaches once (`g6-dialogs/attach`)
  - focus 'devSel'; alias per listed device {'socket://127.0.0.1:9900': 'board'}; custom path /dev/ttyFAKE9 -> 'ttyFAKE9'; POST /ports sent 1; ports now ['sim', 'held']
- passed: attach: a CRLF board attached with Save to config reads crlf in Settings > Ports and in the config file (`g6-dialogs/attachcrlf`)
  - Settings row 'crlf'; config file ['crlf']; live /ports ['crlf']
- passed: adv A15 / panels 11: bundling an open session from Settings saves a zip under the daemon's Content-Disposition name, with and without a stored token (`g6-dialogs/bundle`)
  - [saved name, first bytes, url] {'no token': ('longrun_bundle_20260925T103123-end.zip', b'PK', '2f13bfee-1b8f-4976-8eca-a3cc87c681ed'), 'token stored': ('longrun_bundle_20260925T103123-end.zip', b'PK', '51800749-2e7f-4cb0-bec1-42f9c7aa7cbc')} (`bundle.zip` is only the fallback name, settings.js:415)
- passed: adv W8 / panels 8: picking the bracketed port default again reads `(LF)` and sends no eol (`g6-dialogs/eoldefault`)
  - select ['', '(LF)']; bodies ['{"port":null,"cmd":"ping","timeout_ms":1000,"eol":"crlf"}', '{"port":null,"cmd":"ping","timeout_ms":1000}']; stored override None
- passed: export: heading names the panel; focus on the range choice in force; `reset range`; Enter exports (`g6-dialogs/exportdlg`)
  - titles and focus {'pane': ('Export terminal lines', 'expModeSession'), 'chart': ('Export plot data', 'expModeSession'), 'lanes': ('Export plot data', 'expModeSession'), 'can': ('Export CAN frames', 'expModeSession')}; Enter in the dialog downloaded 'capture_lines_20260925T103015-20260925T103035.txt'; reopened (True, 'expModeClock'); after reset (True, '1')
- passed: keyboard: one Tab stop per segmented control and window selector; arrows move and select; the zoom chip is the stop while zoomed (`g6-dialogs/keyboard`)
  - tab stops per radiogroup [['timeSeg', 1], ['sideSeg', 1], ['plot-win', 1], ['plot-win', 1], ['modeToggle', 1]]; ArrowRight on host -> ['tick', 'true', 'tick']; stops while zoomed [['zoom on'], ['zoom on']]
- passed: keyboard: cmd / raw by arrows keeps focus on the group, not the command input (`g6-dialogs/keyboard`)
  - after ArrowRight: focused 'raw' in the group True, selected 'raw'
- passed: keyboard: the divider is a Tab stop, Left/Right resize it with the accent highlight, a reload keeps the width (`g6-dialogs/keyboard`)
  - reached by Tab True; width 360 -> 400 after two Left, 400 after reload; focused colours ['rgb(10, 109, 125)', 'rgb(26, 33, 41)', 'none', '#0a6d7d']; resizer-focus.png
- passed: keyboard: hiding the sidebar with Enter puts focus on the reopen tab; Enter reopens and focus returns (`g6-dialogs/keyboard`)
  - after hide 'reopenBtn'; after reopen 'collapseBtn'
- passed: settings: daemon stopped: a read-only banner, every daemon Save disabled, the token field saveable (no focus move, per the 2026-09-15 FD2-2 fix) (`g6-dialogs/offline`)
  - banner 'daemon unreachable: settings are read-only; the access token still works'; saves disabled [True, True, True, True, True]; token field enabled True; focus ''; token Save ['Save *', True, False]; stored 'abc123'
- passed: panels 5 (fake monitor for the ST-LINK): the chip shows the target and a lines/s figure; no jump, focus kept across polls (`g6-dialogs/portchip`)
  - chip over 8 s [('probe \u2068socket://127.0.0.1:9863\u2069 boardA ×', 373), ('probe \u2068socket://127.0.0.1:9863\u2069 boardA 5/s ×', 373), ('probe \u2068socket://127.0.0.1:9863\u2069 boardA 5/s ×', 373), ('probe \u2068socket://127.0.0.1:9863\u2069 boardA 5/s ×', 373)]; focus after two polls 'Detach probe'
- passed: panels 6 (fake monitor): the probe moved to another board: the target name changes on screen within a poll (`g6-dialogs/portchip`)
  - after the swap 'probe \u2068socket://127.0.0.1:9863\u2069 boardB 5/s ×'; portchip-swapped.png
- passed: session: the button opens the dialog with the name selected; Enter in Note adds a newline; Enter in Name starts (`g6-dialogs/session`)
  - name focused/selection [True, 0, 20, 20]; after Enter in note: posts 0, note 'line one\nline two'; POSTs 1; closed True
- passed: session: the note shows as the session row's hover (`g6-dialogs/session`)
  - rows [['\u2068bench-run\u2069  recording9/25/202', '', ['\u2068line one\nline two\u2069', 'download this run as a standalone capture database']]]
- passed: settings: editing Bind host shows `Save *`; typing it back clears the mark (`g6-dialogs/settings`)
  - edited ['Save *', True, False]; typed back ['Save', False, False]
- passed: settings: Escape asks, naming the section; Cancel keeps the edit (`g6-dialogs/settings`)
  - confirm text ['Close Settings and discard unsaved changes to Server?']; dialog open with the edit True
- passed: settings: removing a port row marks Ports; a port's EOL changed and saved is there on reopen (`g6-dialogs/settings`)
  - rows 1; after remove ['Save *', True, False]; discard confirm ['Close Settings and discard unsaved changes to Ports?']; reopened EOL 'crlf'; config file eol ['crlf']
- passed: settings: Enter in Retention saves Storage only; the PlotJuggler disclosure opens by keyboard and mouse (`g6-dialogs/settings`)
  - PUTs ['storage']; storage ['Save', False, False]; server still dirty ['Save *', True, False]; disclosure Enter True, click False, click True
- passed: panels 10 / adv A14: with a token stored, a deadband naming a channel not exported, and a malformed one, each show legibly in the open dialog (`g6-dialogs/tokendeadband`)
  - vbat=0.5 -> ('plot export failed: deadband names no exported channel: vbat=0.5', True); tri:0.5 -> ('plot export failed: deadband needs name=value: tri:0.5', True)
- passed: adv W3: with a second port that never connects, the bar names no default port and refuses to send under (auto) (CLI-18: several attached, pick one) (`g6-dialogs/w3`)
  - one port [auto label, eol label, mode] ['(mcu)', '(CRLF)', 'cmd']; two ports ['(auto)', '(LF)', 'raw']; requests on Enter []; result strip '$ pingERRORpick a port: 2 are attached'
- passed: adv W4: a pane paused before any line: export is not refused (the daemon takes id_to=0) and saves a file (`g6-dialogs/w4`)
  - shown window (True, 'the shown window holds nothing to export'); result ('download', 'id_to=0&format=text'); HTTP errors []
- passed: adv A13: with the only session ended, `reset range` selects that session rather than the whole capture (`g6-dialogs/wholesession`)
  - [True, [['1', '\u2068only\u2069 (111 lines)']], '1']

## Defect: the export dialog's Clock row runs past the dialog at every width

- Failed twice (the 10:14 probe at eight widths and the final pass): each `datetime-local` input is 242 px, the `to` field ends 135 px past the dialog's right edge at 1600 px, and the body scrolls sideways (scrollWidth 594 against 458).
- Smallest repro: `mcuscoped --sim`, a pane's `export`, pick Clock; the `to` field is cut at the dialog edge (`export-clock-1600.png`).
- Suspected: `host/mcuscope/webui/style.css:395` with `:461-464`: `.dlg-body` is a grid whose `1fr` track grows to the `.radio-row`'s min-content, so the inputs' `flex: 1; min-width: 0` never gets to shrink them.
- Chromium only; Firefox's `datetime-local` has another intrinsic width.

## Checklist lines the code has moved past (passed against current behaviour)

- 2026-09-12 adversarial 3 (W5 toast): `changes only` is now disabled while `decode` is off.
- Adversarial 4 (W3): with two attached ports the bar refuses under `(auto)`, the CLI-18 ruling, instead of mirroring the daemon's sole-connected rule.
- Adversarial 8 (W12): the export button is disabled with a reason, not inert.
- Adversarial 15 and panels 11: the download takes the daemon's `Content-Disposition` name (`longrun_bundle_<from>-end.zip`); `bundle.zip` is only the fallback.
- 2026-09-14 Settings offline "token field focused": the 2026-09-15 FD2-2 fix dropped the focus move.
