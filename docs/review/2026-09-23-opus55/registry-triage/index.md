# Registry leg triage, index (HEAD 91d3649)

Every finding of `registry-1-14.md` .. `registry-71-80.md`, checked against the code at HEAD 91d3649 (source unchanged since the sweep's f31ecd9 apart from R13-1, R58-1/2).
Severity is the registry's unless marked `*` (changed here). No finding was overstated enough to rate down; see the batch file for each check.

- Verdicts: `fix`, `owner-pick` (a batch implements it once `decisions.md` is answered; the recommended option is the default), `not-a-defect`, `fixed` (already committed).
- A finding whose files span two batches names both; each batch file says which half it owns.
- `O-*` rows are observations the legs recorded outside their class ranges.

| id | sev | file(s) | verdict | batch |
|---|---|---|---|---|
| R1-1 | HIGH | server.py:3347,2628,2990 | owner-pick (D-1) | daemon-api |
| R1-2 | LOW | server.py:2954,2846 | fix | daemon-api |
| R1-3 | LOW | update_check.py:268 | fix | daemon-process |
| R12-1 | LOW | webui/settings.js:38,764 | fix | webui-settings |
| R13-1 | MEDIUM | pidfile.py:143 | fixed (705f368) | - |
| R15-1 | MEDIUM | tools/webui_smoke.py:176 | fix | firmware-packaging |
| R15-2 | LOW | tests importing mcu_sim (5 files), sdist | owner-pick (D-2) | firmware-packaging |
| R15-3 | LOW | release.yml:111, ci.yml:149 | fix | firmware-packaging |
| R15-4 | LOW | ci.yml wheel-smoke (windows) | fix | firmware-packaging |
| R15-5 | LOW | ci.yml, firmware/tests/Makefile, port_template | fix | firmware-packaging |
| R15-6 | LOW | host/contrib/config.example.toml | fix | firmware-packaging |
| R15-7 | LOW | tools/webui_smoke.py:162 | fix | firmware-packaging |
| R15-8 | LOW | ci.yml:103 | fix | firmware-packaging |
| R15-9 | LOW | tests/test_scaffold.py:78 | fix | firmware-packaging |
| R16-1 | MEDIUM | cli_output.py:429 | fix | cli |
| R16-2 | LOW | cli.py:2280, cli_output.py:113 | fix | cli |
| R16-3 | LOW | cli.py:714 | fix | cli |
| R16-4 | LOW | monitor_cmds.c:270 | fix | firmware-packaging |
| R17-1 | MEDIUM | config.py:156 | owner-pick (D-3) | daemon-api |
| R17-2 | LOW | daemon.py:494 | fix | daemon-process |
| R17-3 | LOW | server.py:1108,1391,1411, pjstream.py:74 | fix | daemon-api |
| R17-4 | LOW | cli.py:2728 | owner-pick (D-4) | cli |
| R18-1 | LOW | cli.py:1191 | fix | cli |
| R18-2 | LOW | cli.py:1254 | fix | cli |
| R18-3 | MEDIUM | update_check.py:186 | fix | daemon-process |
| R18-4 | LOW | serial_link.py:1168 | fix | daemon-process |
| O-1 | MEDIUM | pidfile.py:143 (= R13-1) | fixed (705f368) | - |
| R19-1 | LOW | webui/state.js:261, terminal.js:99 | fix | webui-panes |
| R19-2 | LOW | store.py:453, server.py, cli.py:1191, pane.js:57, terminal.js:587 | owner-pick (D-5) | daemon-api + cli + webui-panes |
| R19-3 | LOW | server.py:307 (MarkerBody) | owner-pick (D-6) | daemon-api |
| R20-1 | MEDIUM | store.py:2619,2654 | fix | daemon-api |
| R20-2 | MEDIUM | store.py:2178, server.py:1963, cli.py:2181 | owner-pick (D-7) | daemon-api + cli |
| R20-3 | MEDIUM | store.py:3071 | fix | daemon-api |
| R20-4 | LOW | test_sessions.py:903, test_store_lines_plan.py:500 | fix | daemon-api |
| R20-5 | LOW | store.py:2264, test_store_lines_plan.py:306, REVIEW.md class 20 | fix | daemon-api + firmware-packaging (REVIEW.md) |
| R21-1 | MEDIUM | test_e2e.py:606 | fix | tests-daemon |
| R21-2 | LOW | test_assert.py:127 | fix | tests-daemon |
| R21-3 | LOW | test_wait_repeat.py:326 | fix | tests-daemon |
| R21-4 | LOW | test_wait_repeat.py:369 | fix | tests-daemon |
| R21-5 | LOW | test_serial_link_devices.py:80 | fix | tests-daemon |
| R21-6 | LOW | test_reconnect.py:110,146, test_e2e.py:544 | fix | tests-daemon |
| R22-1 | MEDIUM | protocol.py:165,861 | fix | daemon-process |
| R22-2 | LOW | cli.py (33 numeric params) | fix | cli |
| R22-3 | LOW | cli_daemonctl.py:149 | fix | cli |
| R22-4 | LOW | server.py (35 query/path params) | owner-pick (D-8) | daemon-api |
| R22-5 | LOW | webui/state.js:198 | fix | webui-panes |
| R22-6 | LOW | protocol.py:297,1121, monitor.c monitor_mark | owner-pick (D-9) | daemon-process + firmware-packaging |
| R23-1 | LOW | webui/terminal.js:307 | fix | webui-panes |
| R23-2 | LOW | webui/settings.js:393 | fix | webui-settings |
| R25-1 | MEDIUM | webui/terminal.js:768,848 | owner-pick (D-10) | webui-panes |
| R25-2 | LOW | webui/chrome.js:146, SPEC 9.2 | owner-pick (D-11) | webui-panes |
| R25-3 | MEDIUM | cli.py:997 | fix | cli |
| R25-4 | LOW | webui/terminal.js:783,838, can.js:443 | fix | webui-panes |
| R25-5 | LOW | webui/index.html:53 | fix | webui-panes |
| R26-1 | LOW | webui/terminal.js:47, timewindow.js:275 | owner-pick (D-12) | webui-panes |
| R27-1 | MEDIUM | firmware/tests/fake_shims.c:244 | fix | firmware-packaging |
| R27-2 | LOW | firmware/tests/fake_shims.c:222 | fix | firmware-packaging |
| R27-3 | MEDIUM | monitor_cmds.c:187 | owner-pick (D-13) | firmware-packaging |
| R27-4 | MEDIUM | test_cli_daemon_stop_scope.py:142 | fix | cli-tests |
| R27-5 | MEDIUM | test_cli_version_gate.py:203 | fix | cli-tests |
| R27-6 | MEDIUM | test_serial_link_devices.py:101 | fix | tests-daemon |
| R27-7 | MEDIUM | test_sessions.py:766 | fix | daemon-api |
| R27-8 | MEDIUM | cmdbar_detached_pick.test.mjs:30, dom_stub.mjs:90 | fix | webui-settings |
| R27-9 | MEDIUM | dom_stub.mjs:182 + 5 JS tests | fix | webui-settings |
| R27-10 | LOW | test_update_check.py:29, test_config_api.py:347 | fix | daemon-process |
| R27-11 | LOW | test_reconnect.py:696 | fix | tests-daemon |
| R27-12 | LOW | test_server_scope.py:25 | fix | tests-daemon |
| R27-13 | LOW | test_store_reclaim_budget.py:221 | fix | tests-daemon |
| R27-14 | LOW | test_cli_daemonctl.py:183 | fix | cli-tests |
| R27-15 | LOW | test_cli_read_scope.py:67, cli.py:882 (= FB2-3) | fix | cli |
| R27-16 | LOW | test_cli.py:1776 | fix | cli-tests |
| R27-17 | LOW | test_cli_attach.py:129 (= R63-1) | fix | cli-tests |
| R27-18 | LOW | test_cli_contract.py:244 | fix | cli-tests |
| R27-19 | LOW | test_cli.py:2592 | fix | cli-tests |
| R27-20 | LOW | test_cli.py:409 | fix | cli-tests |
| R27-21 | LOW | test_cli_contract.py:118 (= R78-5) | fix | cli-tests |
| R27-22 | LOW | exportdlg_guards.mjs:226 | fix | webui-settings |
| R27-23 | LOW | dom_stub.mjs:153,222, test_webui.py | fix | webui-settings |
| FA-7 | LOW | test_assert.py:644 | fix | tests-daemon |
| FB1-3 | LOW | test_cli_daemonctl.py:365 | fix | cli-tests |
| FB2-2 | LOW | test_cli_ux.py:183 | fix | cli-tests |
| N-JS-1 | LOW | exportdlg_guards.mjs:5 | fix | webui-settings |
| N-JS-2 | LOW | statusbar_session_dialog.test.mjs:173 | fix | webui-settings |
| N-JS-3 | LOW | settings_revision, settings_late_answers, api_backfill_paging tests | fix | webui-settings |
| N-JS-4 | LOW | statusbar_logic:549, plots_seed_grammar:123,148 | fix | webui-settings |
| R28-1 | MEDIUM | test_store_writer.py:341 | fix | tests-daemon |
| R28-2 | LOW | test_pane_regex_dialect.py:29 | fix | webui-panes |
| R28-3 | LOW | test_daemon_startup.py:75 | fix | daemon-process |
| R28-4 | LOW | test_store_fastpaths.py:218 | fix | daemon-api |
| R29-1 | LOW | test_server_request_validation.py:263 | fix | daemon-api |
| R29-2 | LOW | serial_link.py:1170 (no test) | fix | daemon-process |
| R29-3 | LOW | cli.py:1317,1323, test_cli_follow.py:161 | fix | cli |
| R31-1 | LOW | server.py:2600,3041 | fix | daemon-api |
| R32-1 | LOW | _stdio.py:41, conftest.py | fix | daemon-process |
| R34-1 | LOW | webui/chrome.js:25 | fix | webui-panes |
| R35-1 | LOW | daemon.py:442 (and 391,427,440,453,466) | fix | daemon-process |
| R35-2 | LOW | sim.py:813 (and 797,922,1103,1155,1157,1184) | fix | daemon-process |
| R36-1 | LOW | sim.py:498 | owner-pick (D-14) | daemon-process |
| R39-1 | LOW | server.py:2408,2920 | fix | daemon-api |
| O-42 | LOW | daemon.py:431 (and 142,152,493,498) closed stdout | fix | daemon-process |
| R43-1 | LOW | pyproject.toml ruff floor | fix | firmware-packaging |
| R43-2 | LOW | test_cli_sessions.py:74 | fix | firmware-packaging |
| R43-3 | LOW | pyproject.toml regex floor | fix | firmware-packaging |
| R44-1 | LOW | cli.py:751 | fix | cli |
| R45-1 | LOW | config.py:661 | fix | daemon-api |
| R48-1 | LOW | monitor_cmds.c:101,223 | fix | firmware-packaging |
| R51-1 | LOW | webui/terminal.js:494 | owner-pick (D-15) | webui-panes |
| R53-1 | MEDIUM | cli.py:2126 | fix | cli |
| R53-2 | LOW | cli.py:747,813,849 | fix | cli |
| R53-3 | MEDIUM | cli.py:1477, cli_client.py:35 | owner-pick (D-16) | cli |
| R54-1 | LOW | cli.py:2127,2182 | fix | cli |
| R54-2 | LOW | webui/terminal.js:518,621 | fix | webui-panes |
| O-56a | LOW | cli.py:587 dead `last_ms` in `_lines_params` | fix | cli |
| O-56b | LOW | docs/REVIEW.md:561 legs list inside class 43 | fix | firmware-packaging |
| R57-1 | LOW | webui/can.js:626 | fix | webui-panes |
| R58-1 | LOW | serial_link.py:893 | fixed (152c76e) | - |
| R58-2 | LOW | serial_link.py:1344 | fixed (152c76e) | - |
| R61-1 | LOW | webui/settings.js:122 | fix | webui-settings |
| R63-1 | LOW | test_cli_attach.py:130 (= R27-17) | fix | cli-tests |
| R63-2 | LOW | test_cli_send_verdicts.py:31, cli.py assert render | fix | cli |
| R63-3 | LOW | test_cli_read_scope.py:155 | fix | cli |
| O-70a | LOW | webui/settings.js:609 cleared alias drops a port | fix | webui-settings |
| O-70b | - | serial_link.py:89 priming floor is global ids | not-a-defect | - |
| R71-1 | LOW | settings.js:707, config.py:424, server.py:335 | owner-pick (D-17) | daemon-api + webui-settings |
| R72-1 | LOW | webui/state.js:56 | owner-pick (D-18) | webui-panes |
| R73-1 | LOW | webui/settings.js:28 | fix | webui-settings |
| R73-2 | LOW | webui/cmdbar.js:272 | fix | webui-settings |
| R75-1 | LOW | 6 Python + 2 JS hand-kept lists | fix | tests-daemon + webui-settings |
| R75-2 | LOW | test_protocol_tokenizer.py:14, test_render_line_breaks.py:11, state_marker_tick.test.mjs:15 | fix | daemon-process + webui-settings |
| R76-1 | LOW | webui/terminal.js:258 | fix | webui-panes |
| R77-1 | LOW | webui/plots.js:573, digital.js:109,118 | owner-pick (D-19) | webui-panes |
| R78-1 | LOW | test_session_bundle.py:404 | fix | tests-daemon |
| R78-2 | LOW | test_reconnect.py:1314 | fix | tests-daemon |
| R78-3 | LOW | test_serial_link_attach.py:550 | fix | tests-daemon |
| R78-4 | LOW | test_daemon_startup.py:104, sim.py:1044 | fix | daemon-process |
| R78-5 | LOW | test_cli_contract.py:121 (= R27-21) | fix | cli-tests |
| R78-6 | LOW | test_config_api.py:287 | fix | tests-daemon |
| R78-7 | LOW | 10 absence checks (store sites + others) | fix | daemon-api + tests-daemon |

## Counts

- 138 rows, 135 distinct defects: R63-1/R27-17, R78-5/R27-21 and O-1/R13-1 are one defect each, listed under both ids.
- By row: fix 114, owner-pick 19, not-a-defect 1, fixed 4 (R13-1, O-1, R58-1, R58-2).
- needs-windows-only: none. Every finding has a Linux-testable fix; what still needs a Windows run is in `windows.md`.

## Batches

| batch | findings | owns |
|---|---|---|
| daemon-api | 20 | server.py, store.py, config.py, pjstream.py |
| daemon-process | 16 | daemon.py, sim.py, _stdio.py, update_check.py, serial_link.py, link.py, protocol.py |
| tests-daemon | 18 | existing daemon-side test files only |
| cli | 21 | cli.py, cli_output.py, cli_client.py, cli_daemonctl.py |
| cli-tests | 11 (13 rows) | existing CLI test doubles |
| firmware-packaging | 20 | firmware/, .github/, tools/, pyproject.toml, docs/REVIEW.md |
| webui-panes | 17 | terminal.js, pane.js, state.js, can.js, chrome.js, plots.js, digital.js, timewindow.js, exportdlg.js, index.html |
| webui-settings | 17 | settings.js, cmdbar.js, dom_stub.mjs, exportdlg_guards.mjs, the existing JS tests they fix, test_webui.py |

Counts include the halves of split findings. The owned test files are listed in each batch file.

## Not findings, still owed by the leg

- Class 78's 179-site JS residue is not ruled (`registry-71-80.md`).
- Class 43 floor run over the whole suite, with the JS and firmware files too (the leg ran Python files one at a time).
- Class 32 whole suite under several seeds; class 31 per-parameter "changes the result" tests (`registry-29-42.md` "What stays owed").
- Every leg's "Registry sweep precision" notes: the improved sweep commands go into `docs/REVIEW.md` (firmware-packaging owns that file).
