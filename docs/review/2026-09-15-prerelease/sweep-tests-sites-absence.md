# Test sweep 2 (unproven absence): per-site verdicts

Companion to `sweep-tests.md`. Line numbers are as enumerated at the start of the sweep, before the fixes moved them.

## Sweep 2, Python: 759 sites

Hand-ruled sites:

| site | test | verdict |
|---|---|---|
| test_capture_lock.py:161 | test_a_refused_acquire_does_not_leak_its_descriptor | complies: lockfile.py opens with `os.open`, the patched module attribute |
| test_cli.py:163 | test_a_pipe_closed_inside_a_command_is_success | complies: console_entry re-raises after the crash log, so a crash's Traceback reaches the child's stderr |
| test_cli.py:175 | test_a_pipe_closed_before_the_final_flush_is_success | complies, as above |
| test_cli.py:175 | test_a_pipe_closed_before_the_final_flush_is_success | complies, as above |
| test_cli.py:184 | test_a_pipe_closed_during_help_is_success | complies, as above |
| test_cli.py:854 | test_daemon_start_timeout_does_not_orphan_the_child | violates, fixed: pid dir now `_child_data_dir(data_home)`, the child's own resolution, not a hand join |
| test_cli.py:1538 | test_stage_backfill_consumes_its_recv_when_the_snapshot_raises | violates, fixed: positive control added: a recv orphaned the same way does reach `reports`; M1 red |
| test_cli_contract.py:222 | test_wait_repeat_survives_a_daemon_without_the_send_counters | exempt because in process an escaping exception fails the call itself; 'unexpected response' positive at test_cli LIST_FIELDS |
| test_cli_contract.py:222 | test_wait_repeat_survives_a_daemon_without_the_send_counters | exempt because in process an escaping exception fails the call itself; 'unexpected response' positive at test_cli LIST_FIELDS |
| test_cli_ux.py:118 | test_a_failed_start_shows_the_tail_of_the_daemons_stderr | complies: the CLI writes the record at the patched `_pid_file`; mutation M5 (keep it) turns it red |
| test_cli_ux.py:294 | test_restart_with_no_daemon_running_just_starts_one | complies: restart calls cli's own `_stop_daemon`; the same seam records at test_rulings_cli_config:163 |
| test_daemon_r2026_09_12_bundle.py:177 | test_a_failed_build_removes_both_temp_files | complies: helper imported from test_session_bundle, whose :231 sees the temp file while held |
| test_daemon_r2026_09_12_bundle.py:178 | test_a_failed_build_removes_both_temp_files | complies, as above |
| test_daemon_startup.py:78 | test_a_startup_failure_after_the_claim_leaves_no_pid_record | complies: pidfile resolves platformdirs at call time; mutation M6 (no release) turns it red |
| test_hardening.py:1378 | test_lines_port_filter_seeks_rather_than_scans | exempt because it is a precondition (no ANALYZE), not a product effect |
| test_hardening.py:1590 | test_the_age_sweep_does_not_read_the_table_when_nothing_has_expired | exempt, as above |
| test_pidfile.py:45 | test_claim_writes_own_pid_and_release_removes_it | complies: the path claim() returned and just read |
| test_pidfile.py:243 | test_claim_removes_the_record_when_the_pid_write_fails | complies: claim() builds its path with pid_file_path |
| test_regressions.py:718 | test_replace_atomic_survives_a_real_open_handle_on_windows | complies: the test created the source it asserts moved |
| test_regressions.py:1564 | test_an_attach_racing_stop_all_does_not_start_an_orphan_port | violates, fixed: sibling positive control added (an unraced attach calls the patched start); M4 red |
| test_review_r2_cli.py:399 | test_daemon_stop_reports_removing_a_stale_pid_record | complies: the child's own 'removed stale pid file' on stderr proves it looked at that path |
| test_review_r2_cli.py:478 | test_follow_ws_consumes_its_pending_recv_when_the_staged_drain_raises | violates, fixed: positive control added: an orphaned recv failure does reach this caplog; M2 red |
| test_review_r2_config.py:105 | test_the_config_writer_does_not_use_a_fixed_temp_sibling | violates, fixed: glob `*.tmp` now tied to the observed temp name (`seen[0].endswith('.tmp')`) |
| test_review_r2_config.py:127 | test_the_update_cache_writer_does_not_use_a_fixed_temp_sibling | violates, fixed: as above |
| test_review_r2_server.py:157 | test_a_disconnected_download_still_removes_the_temp_copy | complies: the test created the file it asserts removed |
| test_review_r2_store.py:146 | test_active_session_does_not_read_every_session_when_none_is_running | exempt, as above |
| test_rulings_cli_closed_pipe.py:77 | test_a_closed_stderr_keeps_the_exit_code_and_writes_no_crash_log | complies: positive control at :105 (a real crash lands `mcu-crash.log` in the same dir), fixed before this sweep |
| test_rulings_cli_closed_pipe.py:83 | test_a_closed_stderr_still_emits_the_json_object | complies, as above |
| test_rulings_cli_closed_pipe.py:91 | test_a_closed_stdout_keeps_the_exit_code_and_the_message | complies, as above |
| test_rulings_cli_closed_pipe.py:103 | test_the_repair_warning_on_a_closed_stderr_does_not_own_the_exit | complies, as above |
| test_rulings_cli_config.py:74 | test_a_missing_relative_config_is_refused_as_its_resolved_path | complies: the patched `_status_body` is on the start path (the successful starts in the file need it) |
| test_rulings_cli_config.py:75 | test_a_missing_relative_config_is_refused_as_its_resolved_path | complies: the CLI writes the record only after the spawn, at the patched path; `_Daemon.spawned == []` is the stronger half |
| test_rulings_cli_config.py:130 | test_a_missing_default_config_still_starts_on_defaults | exempt because it is a precondition; with conftest missing, the real config exists on this machine and it would fail |
| test_rulings_cli_config.py:156 | test_restart_of_a_daemon_on_the_missing_default_config_is_not_refused | exempt, as above |
| test_rulings_daemon_config.py:268 | test_a_named_config_that_does_not_exist_is_refused | complies: the same `run_main` redirect as the M6-verified tests; pidfile reads the patched attribute |
| test_rulings_daemon_plot.py:134 | test_an_attached_board_does_not_rescan_the_store | violates, fixed: positive control added: the spy sees the rescan a detached board needs; M3 red |
| test_rulings_daemon_startlog.py:101 | test_a_corrupt_capture_through_main | complies, as above (M6) |
| test_stdio.py:117 | test_console_entry_does_not_log_normal_exits | complies: same `_crash_dir` patch and name as the positive at :93 |
| test_stdio.py:152 | test_console_entry_widens_a_redirected_stdout | complies, as above (report key restored per test by conftest) |
| test_update_check.py:111 | test_check_once_records_and_caches | complies: the same `mock_transport(calls=)` records at :234 |
| test_update_check.py:215 | test_config_disabled_never_requests | complies, as above |

Mechanically ruled sites, by rule:

249 sites: complies: the same test asserts a positive on the same observed object.

```
test_assert.py:68 test_assert.py:79 test_assert.py:287 test_assert.py:302 test_assert.py:316
test_assert.py:346 test_assert.py:346 test_assert.py:368 test_assert.py:395 test_assert.py:395
test_assert.py:429 test_assert.py:669 test_cli.py:150 test_cli.py:265 test_cli.py:285
test_cli.py:291 test_cli.py:659 test_cli.py:845 test_cli.py:905 test_cli.py:1040
test_cli.py:1177 test_cli.py:1177 test_cli.py:1189 test_cli.py:1205 test_cli.py:1242
test_cli.py:1568 test_cli.py:1619 test_cli.py:1626 test_cli.py:1678 test_cli.py:1870
test_cli.py:1922 test_cli.py:2092 test_cli.py:2094 test_cli.py:2410 test_cli.py:2417
test_cli.py:2425 test_cli.py:2479 test_cli.py:2503 test_cli.py:2516 test_cli.py:2550
test_cli.py:2762 test_cli_contract.py:60 test_cli_contract.py:84 test_cli_export.py:81
test_cli_export.py:161 test_cli_export.py:320 test_cli_r2026_09_12.py:212
test_cli_r2026_09_12.py:311 test_cli_r2026_09_12.py:359 test_cli_r2026_09_12.py:382
test_cli_r2026_09_12.py:391 test_cli_ux.py:46 test_cli_ux.py:115 test_cli_ux.py:116
test_cli_ux.py:127 test_cli_ux.py:127 test_cli_ux.py:152 test_cli_ux.py:158 test_cli_ux.py:216
test_cli_ux.py:256 test_cli_ux.py:339 test_config_api.py:78 test_config_api.py:107
test_config_api.py:109 test_config_api.py:110 test_config_api.py:111 test_config_api.py:177
test_config_api.py:277 test_config_api.py:404 test_config_api.py:408 test_config_api.py:555
test_config_ports_eol.py:49 test_config_ports_eol.py:45 test_daemon_r2026_09_12_bundle.py:152
test_daemon_r2026_09_12_bundle.py:153 test_daemon_r2026_09_12_server.py:70
test_daemon_r2026_09_12_server.py:71 test_daemon_r2026_09_12_server.py:72
test_daemon_r2026_09_12_server.py:73 test_daemon_r2026_09_12_server.py:94
test_daemon_r2026_09_12_server.py:94 test_daemon_r2026_09_12_server.py:192
test_daemon_r2026_09_12_server.py:193 test_daemon_r2026_09_12_server.py:194
test_daemon_r2026_09_12_store.py:153 test_daemon_startup.py:194 test_daemon_startup.py:194
test_daemon_startup.py:219 test_daemon_startup.py:250 test_e2e.py:159 test_e2e.py:173
test_e2e.py:360 test_e2e.py:360 test_e2e.py:379 test_e2e.py:695 test_export_lines_can.py:113
test_export_lines_can.py:114 test_hardening.py:317 test_hardening.py:563 test_hardening.py:596
test_hardening.py:618 test_hardening.py:686 test_hardening.py:688 test_hardening.py:694
test_hardening.py:699 test_hardening.py:1436 test_hardening.py:1455 test_hardening.py:1485
test_hardening.py:1566 test_hardening.py:1863 test_hardening.py:1895 test_pidfile.py:178
test_pidfile.py:275 test_pidfile.py:276 test_pidfile.py:277 test_pidfile.py:324
test_plot.py:156 test_plot.py:355 test_plot.py:406 test_plot_export_decode.py:457
test_plot_export_decode.py:457 test_plotjuggler.py:432 test_port_health.py:101
test_port_health.py:106 test_port_health.py:106 test_port_health.py:169 test_port_health.py:225
test_port_health.py:283 test_port_health.py:543 test_port_health.py:550 test_port_health.py:561
test_prerelease_cli_fixes.py:274 test_prerelease_cli_fixes.py:290
test_prerelease_cli_fixes.py:490 test_prerelease_daemon_core_shutdown.py:83
test_prerelease_daemon_core_shutdown.py:104 test_prerelease_daemon_core_windows.py:104
test_prerelease_fixdiff_py.py:135 test_prerelease_fixdiff_py.py:170
test_prerelease_fixdiff_py.py:213 test_prerelease_fixdiff_py.py:421
test_prerelease_link_fixes.py:57 test_prerelease_link_fixes.py:370 test_protocol.py:76
test_protocol.py:79 test_protocol.py:80 test_protocol.py:170 test_protocol.py:175
test_protocol.py:220 test_protocol.py:220 test_protocol.py:244 test_protocol.py:327
test_protocol.py:327 test_protocol.py:332 test_protocol.py:901 test_protocol.py:924
test_protocol.py:957 test_protocol.py:958 test_protocol.py:1007 test_reconnect.py:64
test_reconnect.py:78 test_reconnect.py:88 test_reconnect.py:458 test_reconnect.py:765
test_reconnect.py:1191 test_reconnect.py:1327 test_reconnect.py:1351 test_regressions.py:33
test_regressions.py:561 test_regressions.py:584 test_regressions.py:971
test_regressions.py:1028 test_regressions.py:1111 test_regressions.py:1137
test_regressions.py:1149 test_regressions.py:1227 test_regressions.py:1228
test_regressions.py:1229 test_regressions.py:1231 test_regressions.py:1232
test_regressions.py:1297 test_review_r2_cli.py:44 test_review_r2_cli.py:136
test_review_r2_cli.py:255 test_review_r2_cli.py:335 test_review_r2_cli.py:362
test_review_r2_config.py:141 test_review_r2_config.py:151 test_review_r2_serial.py:193
test_review_r2_server.py:87 test_review_r2_sim.py:50 test_review_r2_sim.py:67
test_review_r2_store.py:118 test_review_r2_store.py:182 test_rulings_cli_closed_pipe.py:92
test_rulings_cli_closed_pipe.py:113 test_rulings_cli_config.py:168
test_rulings_cli_follow.py:26 test_rulings_cli_follow.py:52 test_rulings_daemon_config.py:60
test_rulings_daemon_config.py:60 test_rulings_daemon_config.py:122
test_rulings_daemon_config.py:223 test_rulings_daemon_config.py:266
test_rulings_daemon_config.py:307 test_rulings_daemon_plot.py:187
test_rulings_daemon_plot.py:187 test_rulings_daemon_startlog.py:100 test_scaffold.py:92
test_security.py:224 test_server_scope.py:115 test_session_bundle.py:163
test_session_bundle.py:163 test_sessions.py:62 test_sessions.py:62 test_sessions.py:246
test_sessions.py:506 test_sessions.py:541 test_sessions.py:579 test_sessions.py:643
test_sessions.py:815 test_sim.py:69 test_sim.py:102 test_sim.py:107 test_sim.py:114
test_sim.py:130 test_sim.py:144 test_sim.py:153 test_sim.py:158 test_sim.py:185 test_sim.py:266
test_sim.py:295 test_sim.py:300 test_sim.py:452 test_sim.py:759 test_sim.py:765 test_sim.py:781
test_source_link.py:38 test_source_link.py:123 test_stdio.py:68 test_stdio.py:78
test_store_fastpaths.py:248 test_timeline.py:66 test_timeline.py:323 test_update_check.py:110
test_update_check.py:274 test_update_check.py:276 test_update_check.py:286
test_wait_repeat.py:177
```

237 sites: exempt because an exit or status code equal to 0 is a success assertion, not an absence.

```
test_assert.py:305 test_assert.py:645 test_assert.py:839 test_break.py:136 test_break.py:151
test_break.py:157 test_break.py:163 test_cli.py:103 test_cli.py:162 test_cli.py:174
test_cli.py:183 test_cli.py:352 test_cli.py:454 test_cli.py:478 test_cli.py:498 test_cli.py:568
test_cli.py:575 test_cli.py:585 test_cli.py:620 test_cli.py:632 test_cli.py:645 test_cli.py:706
test_cli.py:725 test_cli.py:734 test_cli.py:751 test_cli.py:901 test_cli.py:884 test_cli.py:943
test_cli.py:1028 test_cli.py:1032 test_cli.py:1033 test_cli.py:1034 test_cli.py:1037
test_cli.py:1043 test_cli.py:1049 test_cli.py:1057 test_cli.py:1077 test_cli.py:1092
test_cli.py:1108 test_cli.py:1119 test_cli.py:1128 test_cli.py:1149 test_cli.py:1155
test_cli.py:1221 test_cli.py:1235 test_cli.py:1240 test_cli.py:1253 test_cli.py:1275
test_cli.py:1285 test_cli.py:1351 test_cli.py:1478 test_cli.py:1578 test_cli.py:1585
test_cli.py:1607 test_cli.py:1636 test_cli.py:1649 test_cli.py:1650 test_cli.py:1661
test_cli.py:1684 test_cli.py:1685 test_cli.py:1686 test_cli.py:1687 test_cli.py:1693
test_cli.py:1722 test_cli.py:1732 test_cli.py:1741 test_cli.py:1743 test_cli.py:1752
test_cli.py:1888 test_cli.py:1920 test_cli.py:2129 test_cli.py:2358 test_cli.py:2394
test_cli.py:2399 test_cli.py:2475 test_cli.py:2496 test_cli.py:2508 test_cli.py:2521
test_cli.py:2529 test_cli.py:2576 test_cli.py:2728 test_cli_contract.py:157
test_cli_contract.py:238 test_cli_export.py:79 test_cli_export.py:92 test_cli_export.py:128
test_cli_export.py:139 test_cli_export.py:152 test_cli_export.py:160 test_cli_export.py:185
test_cli_export.py:195 test_cli_export.py:205 test_cli_export.py:248 test_cli_export.py:259
test_cli_export.py:274 test_cli_export.py:291 test_cli_export.py:295 test_cli_export.py:301
test_cli_export.py:306 test_cli_export.py:316 test_cli_export.py:319 test_cli_r2026_09_12.py:91
test_cli_r2026_09_12.py:100 test_cli_r2026_09_12.py:107 test_cli_r2026_09_12.py:178
test_cli_r2026_09_12.py:189 test_cli_r2026_09_12.py:211 test_cli_r2026_09_12.py:252
test_cli_r2026_09_12.py:264 test_cli_r2026_09_12.py:329 test_cli_r2026_09_12.py:338
test_cli_r2026_09_12.py:355 test_cli_r2026_09_12.py:365 test_cli_r2026_09_12.py:379
test_cli_r2026_09_12.py:388 test_cli_r2026_09_12.py:414 test_cli_r2026_09_12.py:431
test_cli_r2026_09_12.py:442 test_cli_r2026_09_12.py:451 test_cli_r2026_09_12.py:517
test_cli_ux.py:150 test_cli_ux.py:155 test_cli_ux.py:161 test_cli_ux.py:207 test_cli_ux.py:214
test_cli_ux.py:234 test_cli_ux.py:269 test_cli_ux.py:293 test_cli_ux.py:335 test_cli_ux.py:322
test_cli_ux.py:325 test_cli_ux.py:346 test_cli_ux.py:348 test_cli_ux.py:360 test_cli_ux.py:367
test_cli_ux.py:402 test_cli_ux.py:408 test_cli_ux.py:420 test_cli_ux.py:423 test_cli_ux.py:434
test_cli_ux.py:444 test_cli_ux.py:446 test_cli_ux.py:451 test_cli_ux.py:465
test_config_api.py:152 test_config_api.py:164 test_config_api.py:482
test_daemon_r2026_09_12_store.py:130 test_daemon_r2026_09_12_store.py:226
test_daemon_r2026_09_12_store.py:220 test_daemon_r2026_09_12_store.py:272
test_daemon_startup.py:124 test_decode_per_port.py:204 test_eol.py:279 test_eol.py:285
test_eol.py:291 test_hardening.py:110 test_hardening.py:321 test_hardening.py:357
test_hardening.py:454 test_hardening.py:455 test_hardening.py:1081 test_hardening.py:1105
test_hardening.py:1892 test_plot.py:152 test_plot.py:353 test_plotjuggler.py:422
test_plotjuggler.py:424 test_plotjuggler.py:427 test_plotjuggler.py:429 test_plotjuggler.py:431
test_plotjuggler.py:447 test_plotjuggler.py:451 test_plotjuggler.py:461 test_port_health.py:45
test_port_health.py:54 test_port_health.py:96 test_port_health.py:166 test_port_health.py:195
test_port_health.py:220 test_port_health.py:220 test_port_health.py:228 test_port_health.py:257
test_port_health.py:267 test_port_health.py:536 test_prerelease_cli_fixes.py:83
test_prerelease_cli_fixes.py:146 test_prerelease_cli_fixes.py:236
test_prerelease_cli_fixes.py:256 test_prerelease_cli_fixes.py:289
test_prerelease_cli_fixes.py:333 test_prerelease_cli_fixes.py:389
test_prerelease_cli_fixes.py:420 test_prerelease_cli_fixes.py:442
test_prerelease_cli_fixes.py:459 test_prerelease_daemon_core_windows.py:151
test_prerelease_fixdiff_py.py:71 test_prerelease_fixdiff_py.py:140
test_prerelease_fixdiff_py.py:177 test_prerelease_fixdiff_py.py:210
test_prerelease_fixdiff_py.py:255 test_prerelease_fixdiff_py.py:323
test_prerelease_fixdiff_py.py:336 test_prerelease_fixdiff_py.py:393 test_reconnect.py:340
test_regressions.py:439 test_regressions.py:1147 test_review_r2_cli.py:227
test_review_r2_cli.py:237 test_review_r2_cli.py:477 test_review_r2_server.py:253
test_review_r2_store.py:73 test_review_r2_store.py:82 test_review_r2_store.py:114
test_rulings_cli_config.py:107 test_rulings_cli_config.py:118 test_rulings_cli_config.py:125
test_rulings_cli_config.py:131 test_rulings_cli_config.py:161 test_scaffold.py:85
test_sessions.py:220 test_sim.py:459 test_sim.py:797 test_stdio.py:150
test_store_fastpaths.py:198 test_store_fastpaths.py:263 test_store_fastpaths.py:359
test_store_fastpaths.py:450 test_timeline.py:57 test_timeline.py:97 test_timeline.py:154
test_wait_repeat.py:134 test_wait_repeat.py:174 test_wait_repeat.py:265 test_wait_repeat.py:278
test_webui.py:135 test_webui_js.py:293
```

160 sites: complies: the direct return value or state of the object under test (nothing between the product and the assert).

```
test_assert.py:369 test_assert.py:542 test_break.py:73 test_break.py:113 test_break.py:131
test_capture_lock.py:44 test_capture_lock.py:206 test_cli.py:276 test_cli.py:416
test_cli.py:849 test_cli.py:1672 test_cli.py:1985 test_cli.py:2362 test_cli.py:2411
test_cli_contract.py:202 test_config_api.py:87 test_config_api.py:279 test_config_api.py:280
test_daemon_r2026_09_12_bundle.py:176 test_daemon_r2026_09_12_config.py:129
test_daemon_r2026_09_12_store.py:101 test_daemon_startup.py:104 test_daemon_startup.py:122
test_daemon_startup.py:123 test_e2e.py:119 test_e2e.py:377 test_e2e.py:650 test_e2e.py:676
test_e2e.py:700 test_eol.py:194 test_export_lines_can.py:83 test_export_lines_can.py:220
test_hardening.py:391 test_hardening.py:541 test_hardening.py:977 test_hardening.py:1067
test_hardening.py:1068 test_hardening.py:1244 test_hardening.py:1795 test_hardening.py:2137
test_link.py:30 test_link.py:31 test_link.py:38 test_link.py:39 test_pidfile.py:36
test_pidfile.py:290 test_pidfile.py:338 test_plot.py:185 test_plot.py:272 test_plot.py:299
test_plotjuggler.py:207 test_plotjuggler.py:240 test_plotjuggler.py:335 test_plotjuggler.py:328
test_plotjuggler.py:483 test_port_health.py:297 test_port_health.py:456
test_prerelease_fixdiff_py.py:150 test_prerelease_link_fixes.py:156 test_protocol.py:227
test_protocol.py:227 test_protocol.py:736 test_protocol.py:977 test_reconnect.py:746
test_reconnect.py:747 test_reconnect.py:960 test_reconnect.py:1097 test_reconnect.py:1312
test_reconnect.py:1313 test_reconnect.py:1328 test_reconnect.py:1353 test_regressions.py:594
test_regressions.py:612 test_regressions.py:662 test_regressions.py:922
test_regressions.py:1087 test_regressions.py:1563 test_review_r2_config.py:205
test_review_r2_serial.py:126 test_review_r2_serial.py:196 test_review_r2_server.py:127
test_review_r2_server.py:135 test_review_r2_server.py:222 test_review_r2_server.py:256
test_review_r2_sim.py:44 test_review_r2_sim.py:64 test_security.py:93 test_security.py:94
test_security.py:110 test_security.py:111 test_security.py:112 test_security.py:162
test_security.py:225 test_session_bundle.py:175 test_session_bundle.py:204
test_session_bundle.py:221 test_session_bundle.py:235 test_sessions.py:90 test_sessions.py:164
test_sessions.py:270 test_sessions.py:312 test_sessions.py:330 test_sessions.py:343
test_sessions.py:376 test_sessions.py:417 test_sessions.py:424 test_sessions.py:440
test_sessions.py:620 test_sessions.py:848 test_sim.py:115 test_sim.py:119 test_sim.py:123
test_sim.py:131 test_sim.py:132 test_sim.py:154 test_sim.py:176 test_sim.py:193 test_sim.py:289
test_sim.py:407 test_sim.py:460 test_sim.py:629 test_sim.py:697 test_sim.py:719 test_sim.py:762
test_sim.py:785 test_sim.py:876 test_sim.py:912 test_sim_pty.py:130 test_sim_pty.py:131
test_sim_pty.py:132 test_sim_pty.py:133 test_sim_pty.py:169 test_sim_pty.py:170
test_sim_pty.py:171 test_sim_tcp.py:102 test_sim_tcp.py:228 test_sim_tcp.py:265
test_source_link.py:145 test_source_link.py:146 test_stdio.py:40 test_stdio.py:195
test_store_fastpaths.py:236 test_store_fastpaths.py:264 test_store_fastpaths.py:415
test_store_writer.py:40 test_update_check.py:87 test_update_check.py:137
test_update_check.py:173 test_update_check.py:167 test_update_check.py:183
test_update_check.py:194 test_update_check.py:209 test_update_check.py:210
test_update_check.py:260 test_update_check.py:306 test_update_check.py:349
test_wait_repeat.py:203 test_wait_repeat.py:410 test_webui.py:218 test_webui_js.py:299
```

21 sites: complies: the captured output of the call under test, which the file's sibling positives read.

```
test_cli.py:1094 test_cli.py:1157 test_cli.py:2094 test_cli.py:2509 test_cli.py:2676
test_cli_r2026_09_12.py:321 test_cli_r2026_09_12.py:339 test_cli_ux.py:53 test_cli_ux.py:351
test_cli_ux.py:352 test_cli_ux.py:424 test_daemon_r2026_09_12_server.py:148
test_export_lines_can.py:81 test_export_lines_can.py:188 test_export_lines_can.py:189
test_prerelease_cli_fixes.py:159 test_prerelease_fixdiff_py.py:151 test_review_r2_server.py:231
test_rulings_cli_config.py:133 test_rulings_cli_config.py:162 test_sim.py:220
```

29 sites: complies: the path the product writes (an export opens -o itself, a config save replaces onto config.toml), which the file's success tests show receiving.

```
test_cli.py:2376 test_cli.py:2426 test_cli.py:2450 test_cli_contract.py:117
test_cli_export.py:93 test_cli_export.py:329 test_cli_export.py:340 test_cli_r2026_09_12.py:131
test_cli_r2026_09_12.py:152 test_cli_r2026_09_12.py:494 test_cli_r2026_09_12.py:506
test_cli_ux.py:117 test_config_api.py:88 test_config_api.py:228 test_config_api.py:293
test_plotjuggler.py:307 test_plotjuggler.py:341 test_prerelease_cli_fixes.py:206
test_prerelease_cli_fixes.py:226 test_prerelease_cli_fixes.py:237
test_prerelease_cli_fixes.py:347 test_prerelease_cli_fixes.py:364
test_prerelease_fixdiff_py.py:224 test_prerelease_fixdiff_py.py:224
test_prerelease_fixdiff_py.py:287 test_prerelease_fixdiff_py.py:348
test_rulings_daemon_config.py:143 test_rulings_daemon_config.py:267
test_rulings_daemon_config.py:294
```

11 sites: complies: the recorder fixture that sibling tests in the file read positively.

```
test_cli_r2026_09_12.py:117 test_cli_ux.py:179 test_cli_ux.py:277
test_prerelease_cli_fixes.py:147 test_review_r2_cli.py:112 test_review_r2_cli.py:215
test_rulings_cli_config.py:62 test_rulings_cli_config.py:140 test_rulings_cli_config.py:149
test_rulings_daemon_config.py:218 test_rulings_daemon_startlog.py:45
```

3 sites: complies: the logger this file's positive tests read from the same caplog.

```
test_daemon_r2026_09_12_config.py:109 test_review_r2_store.py:102 test_store_writer.py:74
```

8 sites: complies: beside a positive on the same captured plan (index named); the spelling itself is now controlled by test_the_plan_words_the_negative_assertions_rely_on.

```
test_daemon_r2026_09_12_store.py:71 test_daemon_r2026_09_12_store.py:150 test_hardening.py:1207
test_hardening.py:1242 test_hardening.py:1385 test_hardening.py:1518 test_hardening.py:1604
test_review_r2_store.py:153
```

## Sweep 2, JS: 487 sites

Hand-ruled sites:

| site | verdict |
|---|---|
| api_backfill.test.mjs:69 | complies: the same file asserts a positive on the same observed object |
| api_backfill.test.mjs:84 | complies: the same file asserts a positive on the same observed object |
| api_backfill.test.mjs:117 | complies: the same file asserts a positive on the same observed object |
| api_backfill.test.mjs:121 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| api_backfill_paging.test.mjs:84 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| api_backfill_paging.test.mjs:96 | complies: the same file asserts a positive on the same observed object |
| api_backfill_paging.test.mjs:99 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| api_backfill_paging.test.mjs:118 | complies: the same file asserts a positive on the same observed object |
| api_backfill_paging.test.mjs:140 | complies: the same file asserts a positive on the same observed object |
| api_backfill_paging.test.mjs:150 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| api_backfill_paging.test.mjs:163 | complies: the same file asserts a positive on the same observed object |
| api_backfill_paging.test.mjs:168 | complies: the same file asserts a positive on the same observed object |
| api_backfill_paging.test.mjs:169 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| api_capture_reset_stage.test.mjs:95 | complies: the same file asserts a positive on the same observed object |
| api_capture_reset_stage.test.mjs:117 | complies: the same file asserts a positive on the same observed object |
| api_capture_reset_stage.test.mjs:118 | complies: the same file asserts a positive on the same observed object |
| api_db_reset_misfire.test.mjs:132 | complies: the same file asserts a positive on the same observed object |
| api_high_rate_pending.test.mjs:63 | complies: the same file asserts a positive on the same observed object |
| api_high_rate_pending.test.mjs:90 | complies: the same file asserts a positive on the same observed object |
| api_pane_queue.test.mjs:65 | complies: the same file asserts a positive on the same observed object |
| api_pane_queue.test.mjs:76 | complies: the same file asserts a positive on the same observed object |
| api_pane_queue.test.mjs:81 | complies: the same file asserts a positive on the same observed object |
| api_pane_queue.test.mjs:90 | complies: the same file asserts a positive on the same observed object |
| api_pane_queue.test.mjs:91 | complies: the same file asserts a positive on the same observed object |
| api_pane_queue.test.mjs:97 | complies: the same file asserts a positive on the same observed object |
| api_pane_queue.test.mjs:100 | complies: the same file asserts a positive on the same observed object |
| api_pane_queue.test.mjs:102 | complies: the same file asserts a positive on the same observed object |
| api_pane_queue.test.mjs:110 | complies: the same file asserts a positive on the same observed object |
| api_plot_seed.test.mjs:173 | complies: the same file asserts a positive on the same observed object |
| api_plot_seed_ports.test.mjs:49 | complies: the same `seen` is read positively in the test (/plot/series requests) |
| app_layout.test.mjs:26 | complies: the same file asserts a positive on the same observed object |
| app_layout.test.mjs:34 | complies: the same file asserts a positive on the same observed object |
| app_layout.test.mjs:36 | complies: the same file asserts a positive on the same observed object |
| app_layout_corrupt.test.mjs:15 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| app_layout_corrupt.test.mjs:16 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| app_resizer_keys.test.mjs:62 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| app_resizer_keys.test.mjs:63 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| app_resizer_keys.test.mjs:64 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| can_age_tick.test.mjs:42 | violates, fixed: the count is never in a cell's text (only the row's hover), so this could not fail; now observes `tr.title`, with a renderCan positive; J3 red |
| can_age_tick.test.mjs:80 | complies: the same file asserts a positive on the same observed object |
| can_age_tick.test.mjs:97 | complies: the same file asserts a positive on the same observed object |
| can_freeze_surface.test.mjs:30 | complies: the same file asserts a positive on the same observed object |
| can_freeze_surface.test.mjs:31 | complies: the same file asserts a positive on the same observed object |
| can_freeze_surface.test.mjs:40 | complies: the same file asserts a positive on the same observed object |
| can_freeze_surface.test.mjs:49 | complies: the same file asserts a positive on the same observed object |
| can_freeze_surface.test.mjs:59 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| can_head.test.mjs:41 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| can_head.test.mjs:43 | complies: the same file asserts a positive on the same observed object |
| can_head.test.mjs:57 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| can_head.test.mjs:95 | complies: the tag is shown hidden at can_freeze_surface:63 |
| can_head.test.mjs:145 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| can_head.test.mjs:224 | complies: the same file asserts a positive on the same observed object |
| can_head.test.mjs:230 | complies: the same file asserts a positive on the same observed object |
| can_head.test.mjs:261 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| can_head.test.mjs:275 | complies: the same file asserts a positive on the same observed object |
| can_logic.test.mjs:91 | complies: the same file asserts a positive on the same observed object |
| can_logic.test.mjs:131 | complies: the same file asserts a positive on the same observed object |
| can_logic.test.mjs:211 | complies: the same storage key is read positively in the same file |
| can_logic.test.mjs:228 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| can_logic.test.mjs:343 | complies: the same storage key is read positively in the same file |
| can_logic.test.mjs:363 | complies: the same file asserts a positive on the same observed object |
| can_logic.test.mjs:364 | complies: the same file asserts a positive on the same observed object |
| can_logic.test.mjs:365 | complies: the same file asserts a positive on the same observed object |
| can_logic.test.mjs:369 | complies: the same file asserts a positive on the same observed object |
| can_logic.test.mjs:370 | complies: the same file asserts a positive on the same observed object |
| can_logic.test.mjs:378 | complies: the same file asserts a positive on the same observed object |
| can_logic.test.mjs:395 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| can_logic.test.mjs:399 | complies: the same file asserts a positive on the same observed object |
| chrome.test.mjs:61 | complies: the same file asserts a positive on the same observed object |
| chrome.test.mjs:131 | complies: the same file asserts a positive on the same observed object |
| chrome.test.mjs:143 | complies: the same file asserts a positive on the same observed object |
| chrome.test.mjs:155 | complies: the same file asserts a positive on the same observed object |
| chrome_radios.test.mjs:48 | complies: the same file asserts a positive on the same observed object |
| chrome_radios.test.mjs:87 | complies: the same file asserts a positive on the same observed object |
| chrome_radios.test.mjs:97 | complies: the same file asserts a positive on the same observed object |
| chrome_radios.test.mjs:105 | complies: the same file asserts a positive on the same observed object |
| chrome_radios.test.mjs:134 | complies: the same file asserts a positive on the same observed object |
| chrome_radios.test.mjs:140 | complies: the same file asserts a positive on the same observed object |
| chrome_radios.test.mjs:146 | complies: the same file asserts a positive on the same observed object |
| chrome_radios.test.mjs:148 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| chrome_radios.test.mjs:149 | complies: the same file asserts a positive on the same observed object |
| cmdbar_eol.test.mjs:77 | complies: the same file asserts a positive on the same observed object |
| cmdbar_eol.test.mjs:124 | complies: the same file asserts a positive on the same observed object |
| cmdbar_eol.test.mjs:125 | complies: the same storage key is read positively in the same file |
| cmdbar_eol.test.mjs:129 | complies: the same file asserts a positive on the same observed object |
| cmdbar_sole.test.mjs:119 | complies: the same file asserts a positive on the same observed object |
| cmdbar_sole.test.mjs:137 | complies: the same file asserts a positive on the same observed object |
| cmdbar_sole.test.mjs:145 | complies: the same file asserts a positive on the same observed object |
| cmdbar_sole.test.mjs:149 | complies: the same file asserts a positive on the same observed object |
| cmdbar_sole.test.mjs:151 | complies: the same file asserts a positive on the same observed object |
| cmdbar_sole.test.mjs:152 | complies: the same file asserts a positive on the same observed object |
| cmdbar_sole.test.mjs:161 | complies: the same file asserts a positive on the same observed object |
| cmdbar_sole.test.mjs:164 | complies: the test wrote the value it asserts cleared |
| cmdbar_sole.test.mjs:180 | complies: the same file asserts a positive on the same observed object |
| digital_clear_segments.test.mjs:60 | complies: the same file asserts a positive on the same observed object |
| digital_edge.test.mjs:51 | complies: the same file asserts a positive on the same observed object |
| digital_edge.test.mjs:65 | complies: the same file asserts a positive on the same observed object |
| digital_edge.test.mjs:71 | complies: the same file asserts a positive on the same observed object |
| digital_paused_freeze.test.mjs:112 | complies: the same file asserts a positive on the same observed object |
| digital_paused_freeze.test.mjs:121 | complies: the same file asserts a positive on the same observed object |
| digital_repaint.test.mjs:56 | complies: the same file asserts a positive on the same observed object |
| digital_zoom.test.mjs:44 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| digital_zoom.test.mjs:92 | complies: the same file asserts a positive on the same observed object |
| export_paused_window.test.mjs:101 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| export_paused_window.test.mjs:110 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| export_paused_window.test.mjs:118 | complies: the same file asserts a positive on the same observed object |
| export_paused_window.test.mjs:122 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| export_paused_window.test.mjs:142 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| export_paused_window.test.mjs:172 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| export_paused_window.test.mjs:188 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| export_paused_window.test.mjs:197 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| export_paused_window.test.mjs:198 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| export_paused_window.test.mjs:205 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| export_paused_window.test.mjs:229 | complies: the same file asserts a positive on the same observed object |
| exportdlg.test.mjs:79 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| exportdlg.test.mjs:80 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| exportdlg.test.mjs:94 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| exportdlg.test.mjs:115 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| exportdlg.test.mjs:116 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| exportdlg.test.mjs:126 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| exportdlg.test.mjs:140 | complies: the same file asserts a positive on the same observed object |
| exportdlg.test.mjs:141 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| exportdlg.test.mjs:162 | complies: the same storage key is read positively in the same file |
| exportdlg.test.mjs:214 | complies: the same file asserts a positive on the same observed object |
| exportdlg.test.mjs:224 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| exportdlg.test.mjs:234 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| exportdlg.test.mjs:254 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| exportdlg.test.mjs:260 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| exportdlg.test.mjs:284 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| exportdlg.test.mjs:308 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| exportdlg.test.mjs:354 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| exportdlg.test.mjs:361 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| exportdlg.test.mjs:380 | complies: the same file asserts a positive on the same observed object |
| exportdlg.test.mjs:405 | complies: expGo.disabled is shown true while waiting at rulings_chrome_export:110 |
| exportdlg.test.mjs:431 | complies: the same file asserts a positive on the same observed object |
| exportdlg.test.mjs:446 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| exportrange.test.mjs:47 | complies: the same file asserts a positive on the same observed object |
| exportrange.test.mjs:59 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| exportrange.test.mjs:62 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| exportrange.test.mjs:71 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| exportrange.test.mjs:72 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| exportrange.test.mjs:81 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| exportrange.test.mjs:83 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| exportrange.test.mjs:105 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| freeze.test.mjs:40 | complies: the same file asserts a positive on the same observed object |
| freeze.test.mjs:53 | complies: the same file asserts a positive on the same observed object |
| freeze.test.mjs:81 | complies: the same file asserts a positive on the same observed object |
| freeze.test.mjs:125 | complies: the same file asserts a positive on the same observed object |
| freeze.test.mjs:130 | complies: the same file asserts a positive on the same observed object |
| freeze.test.mjs:139 | complies: the same file asserts a positive on the same observed object |
| freeze.test.mjs:140 | complies: the same file asserts a positive on the same observed object |
| freeze.test.mjs:148 | complies: the same file asserts a positive on the same observed object |
| freeze.test.mjs:149 | complies: the same file asserts a positive on the same observed object |
| freeze.test.mjs:157 | complies: the same file asserts a positive on the same observed object |
| freeze.test.mjs:165 | complies: the same file asserts a positive on the same observed object |
| freeze.test.mjs:168 | complies: the same file asserts a positive on the same observed object |
| layout.test.mjs:23 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| layout.test.mjs:26 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| layout.test.mjs:37 | complies: the same file asserts a positive on the same observed object |
| layout.test.mjs:54 | complies: the same file asserts a positive on the same observed object |
| layout.test.mjs:60 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| layout.test.mjs:61 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| layout.test.mjs:66 | complies: the same file asserts a positive on the same observed object |
| plots_channel_cap.test.mjs:34 | complies: the same file asserts a positive on the same observed object |
| plots_chrome.test.mjs:44 | complies: the same file asserts a positive on the same observed object |
| plots_chrome.test.mjs:85 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| plots_chrome.test.mjs:96 | complies: the same file asserts a positive on the same observed object |
| plots_chrome.test.mjs:118 | complies: the same storage key is read positively in the same file |
| plots_chrome.test.mjs:124 | complies: the same storage key is read positively in the same file |
| plots_chrome.test.mjs:144 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| plots_chrome.test.mjs:148 | complies: the same file asserts a positive on the same observed object |
| plots_chrome.test.mjs:150 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| plots_chrome.test.mjs:151 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| plots_chrome.test.mjs:155 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| plots_chrome.test.mjs:166 | exempt because it is a static scan of style.css |
| plots_chrome.test.mjs:176 | complies: the same file asserts a positive on the same observed object |
| plots_chrome.test.mjs:205 | complies: the same file asserts a positive on the same observed object |
| plots_export_button.test.mjs:72 | complies: the same file asserts a positive on the same observed object |
| plots_export_button.test.mjs:86 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| plots_export_button.test.mjs:92 | complies: the same file asserts a positive on the same observed object |
| plots_export_button.test.mjs:99 | complies: the same file asserts a positive on the same observed object |
| plots_export_button.test.mjs:104 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| plots_export_button.test.mjs:107 | complies: the same file asserts a positive on the same observed object |
| plots_export_button.test.mjs:136 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| plots_export_button.test.mjs:137 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| plots_export_button.test.mjs:148 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| plots_export_button.test.mjs:172 | complies: the same file asserts a positive on the same observed object |
| plots_finite.test.mjs:107 | complies: the same file asserts a positive on the same observed object |
| plots_finite.test.mjs:135 | complies: the same file asserts a positive on the same observed object |
| plots_finite.test.mjs:139 | complies: the same file asserts a positive on the same observed object |
| plots_finite.test.mjs:216 | complies: the same file asserts a positive on the same observed object |
| plots_finite.test.mjs:217 | complies: the same file asserts a positive on the same observed object |
| plots_finite.test.mjs:229 | complies: the same file asserts a positive on the same observed object |
| plots_finite.test.mjs:241 | complies: the same file asserts a positive on the same observed object |
| plots_finite.test.mjs:242 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| plots_finite.test.mjs:259 | complies: the same file asserts a positive on the same observed object |
| plots_finite.test.mjs:270 | complies: the same file asserts a positive on the same observed object |
| plots_finite.test.mjs:283 | complies: the same file asserts a positive on the same observed object |
| plots_finite.test.mjs:302 | complies: the same file asserts a positive on the same observed object |
| plots_finite.test.mjs:334 | complies: the same file asserts a positive on the same observed object |
| plots_finite.test.mjs:353 | complies: the same file asserts a positive on the same observed object |
| plots_finite.test.mjs:354 | complies: the same file asserts a positive on the same observed object |
| plots_finite.test.mjs:358 | complies: the same file asserts a positive on the same observed object |
| plots_paused_freeze.test.mjs:93 | complies: the same file asserts a positive on the same observed object |
| plots_ports.test.mjs:81 | complies: the same file asserts a positive on the same observed object |
| plots_ports.test.mjs:84 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| plots_ports.test.mjs:85 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| plots_ports.test.mjs:167 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| plots_seed_grammar.test.mjs:67 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| plots_seed_grammar.test.mjs:77 | complies: the same file asserts a positive on the same observed object |
| plots_seed_paused.test.mjs:69 | complies: the same file asserts a positive on the same observed object |
| plots_seed_paused.test.mjs:74 | complies: the same file asserts a positive on the same observed object |
| plots_seed_paused.test.mjs:77 | complies: the same file asserts a positive on the same observed object |
| plots_solo.test.mjs:94 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| plots_zoom.test.mjs:65 | complies: the same file asserts a positive on the same observed object |
| plots_zoom.test.mjs:120 | complies: the same file asserts a positive on the same observed object |
| plots_zoom.test.mjs:134 | complies: the same file asserts a positive on the same observed object |
| plots_zoom.test.mjs:146 | complies: the same file asserts a positive on the same observed object |
| plots_zoom.test.mjs:147 | complies: the same file asserts a positive on the same observed object |
| plots_zoom.test.mjs:148 | complies: the same file asserts a positive on the same observed object |
| plots_zoom.test.mjs:149 | complies: the same file asserts a positive on the same observed object |
| plots_zoom_chip.test.mjs:63 | complies: the same file asserts a positive on the same observed object |
| plots_zoom_chip.test.mjs:65 | complies: the same file asserts a positive on the same observed object |
| plots_zoom_chip.test.mjs:74 | complies: the same file asserts a positive on the same observed object |
| plots_zoom_chip.test.mjs:86 | complies: the same file asserts a positive on the same observed object |
| plots_zoom_chip.test.mjs:88 | complies: the same file asserts a positive on the same observed object |
| plots_zoom_chip.test.mjs:90 | complies: the same file asserts a positive on the same observed object |
| plots_zoom_chip.test.mjs:103 | complies: the same file asserts a positive on the same observed object |
| plots_zoom_chip.test.mjs:111 | complies: the same file asserts a positive on the same observed object |
| plots_zoom_chip.test.mjs:120 | complies: the same file asserts a positive on the same observed object |
| plots_zoom_chip.test.mjs:128 | complies: the same file asserts a positive on the same observed object |
| prerelease_chrome_export.test.mjs:75 | complies: the same file asserts a positive on the same observed object |
| prerelease_chrome_export.test.mjs:87 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| prerelease_chrome_settings.test.mjs:114 | complies: the same file asserts a positive on the same observed object |
| prerelease_chrome_settings.test.mjs:200 | complies: the same file asserts a positive on the same observed object |
| prerelease_chrome_settings.test.mjs:220 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| prerelease_chrome_settings.test.mjs:285 | violates, fixed: positive control test added (a failed current fill writes the slot); J2 and J2b red |
| prerelease_chrome_static.test.mjs:25 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| prerelease_chrome_static.test.mjs:60 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| prerelease_chrome_statusbar.test.mjs:64 | complies: the same file asserts a positive on the same observed object |
| prerelease_chrome_statusbar.test.mjs:74 | complies: the same file asserts a positive on the same observed object |
| prerelease_chrome_statusbar.test.mjs:91 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| prerelease_chrome_statusbar.test.mjs:160 | complies: the same file asserts a positive on the same observed object |
| prerelease_fixdiff_webui.test.mjs:106 | complies: the same file asserts a positive on the same observed object |
| prerelease_fixdiff_webui.test.mjs:147 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| prerelease_fixdiff_webui.test.mjs:152 | complies: the same file asserts a positive on the same observed object |
| prerelease_fixdiff_webui.test.mjs:159 | complies: the same file asserts a positive on the same observed object |
| prerelease_fixdiff_webui.test.mjs:174 | complies: the same file asserts a positive on the same observed object |
| prerelease_fixdiff_webui.test.mjs:196 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| prerelease_fixdiff_webui.test.mjs:203 | complies: the same file asserts a positive on the same observed object |
| prerelease_fixdiff_webui.test.mjs:220 | complies: the same file asserts a positive on the same observed object |
| prerelease_fixdiff_webui.test.mjs:230 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| prerelease_fixdiff_webui.test.mjs:333 | complies: the same file asserts a positive on the same observed object |
| prerelease_fixdiff_webui.test.mjs:340 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| prerelease_fixdiff_webui.test.mjs:425 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| prerelease_fixdiff_webui.test.mjs:449 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| prerelease_handoff_reset.test.mjs:37 | complies: the same file asserts a positive on the same observed object |
| prerelease_handoff_reset.test.mjs:38 | complies: the same file asserts a positive on the same observed object |
| prerelease_handoff_reset.test.mjs:39 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_can.test.mjs:52 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_can.test.mjs:91 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_can.test.mjs:227 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| prerelease_webui-panes_can.test.mjs:228 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_plots.test.mjs:90 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| prerelease_webui-panes_plots.test.mjs:91 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_plots.test.mjs:138 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_plots.test.mjs:142 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_terminal.test.mjs:89 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_terminal.test.mjs:90 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_terminal.test.mjs:91 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_terminal.test.mjs:100 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_terminal.test.mjs:101 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_terminal.test.mjs:110 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_terminal.test.mjs:111 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_terminal.test.mjs:121 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_terminal.test.mjs:130 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_terminal.test.mjs:140 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_terminal.test.mjs:141 | complies: the same file asserts a positive on the same observed object |
| prerelease_webui-panes_timewindow.test.mjs:35 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| prerelease_webui-panes_timewindow.test.mjs:37 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| rulings_chrome_export.test.mjs:77 | complies: the same file asserts a positive on the same observed object |
| rulings_chrome_export.test.mjs:78 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| rulings_chrome_export.test.mjs:86 | complies: the same file asserts a positive on the same observed object |
| rulings_chrome_export.test.mjs:98 | complies: the same file asserts a positive on the same observed object |
| rulings_chrome_export.test.mjs:119 | complies: the same file asserts a positive on the same observed object |
| rulings_chrome_export.test.mjs:129 | complies: the same file asserts a positive on the same observed object |
| rulings_chrome_export.test.mjs:130 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| rulings_chrome_export.test.mjs:171 | complies: the same file asserts a positive on the same observed object |
| rulings_chrome_settings.test.mjs:118 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| rulings_chrome_settings.test.mjs:139 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| rulings_chrome_settings.test.mjs:175 | complies: the same file asserts a positive on the same observed object |
| rulings_chrome_settings.test.mjs:176 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| rulings_chrome_settings.test.mjs:183 | complies: the same file asserts a positive on the same observed object |
| rulings_chrome_settings.test.mjs:189 | complies: the same file asserts a positive on the same observed object |
| rulings_chrome_settings.test.mjs:210 | complies: the same file asserts a positive on the same observed object |
| rulings_chrome_settings.test.mjs:211 | complies: the same file asserts a positive on the same observed object |
| rulings_chrome_settings.test.mjs:216 | complies: the same file asserts a positive on the same observed object |
| rulings_chrome_settings.test.mjs:226 | complies: the same file asserts a positive on the same observed object |
| rulings_chrome_settings.test.mjs:233 | complies: the same file asserts a positive on the same observed object |
| rulings_chrome_settings.test.mjs:246 | complies: the same file asserts a positive on the same observed object |
| rulings_chrome_settings.test.mjs:260 | complies: the same file asserts a positive on the same observed object |
| rulings_panes_export.test.mjs:32 | complies: expModeShown.disabled is shown true at exportdlg:73 |
| rulings_panes_export.test.mjs:84 | complies: the same file asserts a positive on the same observed object |
| rulings_panes_export.test.mjs:127 | complies: the same file asserts a positive on the same observed object |
| rulings_panes_export.test.mjs:153 | complies: the same file asserts a positive on the same observed object |
| rulings_panes_export.test.mjs:161 | complies: the same file asserts a positive on the same observed object |
| rulings_panes_export.test.mjs:162 | complies: the same file asserts a positive on the same observed object |
| rulings_panes_export.test.mjs:183 | complies: the same file asserts a positive on the same observed object |
| rulings_panes_export.test.mjs:184 | complies: the same file asserts a positive on the same observed object |
| rulings_panes_export.test.mjs:190 | complies: the same file asserts a positive on the same observed object |
| rulings_panes_export.test.mjs:191 | complies: the same file asserts a positive on the same observed object |
| rulings_panes_export.test.mjs:205 | complies: the same file asserts a positive on the same observed object |
| rulings_panes_export.test.mjs:206 | complies: the same file asserts a positive on the same observed object |
| rulings_panes_reset.test.mjs:50 | complies: the same file asserts a positive on the same observed object |
| rulings_panes_reset.test.mjs:115 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| rulings_panes_tickclock.test.mjs:23 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| rulings_panes_tickclock.test.mjs:71 | complies: the same file asserts a positive on the same observed object |
| settings_dirty.test.mjs:74 | complies: the same file asserts a positive on the same observed object |
| settings_dirty.test.mjs:88 | complies: the same file asserts a positive on the same observed object |
| settings_dirty.test.mjs:123 | complies: the same file asserts a positive on the same observed object |
| settings_dirty.test.mjs:130 | complies: the same file asserts a positive on the same observed object |
| settings_dirty.test.mjs:162 | complies: the same file asserts a positive on the same observed object |
| settings_dirty.test.mjs:170 | complies: the same file asserts a positive on the same observed object |
| settings_dirty.test.mjs:172 | complies: the same file asserts a positive on the same observed object |
| settings_dirty.test.mjs:176 | complies: the same file asserts a positive on the same observed object |
| settings_dirty.test.mjs:177 | complies: the same file asserts a positive on the same observed object |
| settings_dirty.test.mjs:191 | complies: the same file asserts a positive on the same observed object |
| settings_dirty.test.mjs:199 | complies: the same file asserts a positive on the same observed object |
| settings_offline.test.mjs:61 | complies: the same property flips true on DAEMON_CONTROLS in the same test |
| settings_offline.test.mjs:69 | complies: the same file asserts a positive on the same observed object |
| settings_offline.test.mjs:80 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| settings_offline.test.mjs:88 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| settings_offline.test.mjs:90 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| settings_offline.test.mjs:98 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| settings_offline.test.mjs:107 | complies: the same file asserts a positive on the same observed object |
| settings_offline.test.mjs:110 | complies: the same file asserts a positive on the same observed object |
| settings_pj.test.mjs:86 | complies: the same file asserts a positive on the same observed object |
| settings_pj.test.mjs:107 | complies: the same file asserts a positive on the same observed object |
| settings_pj.test.mjs:109 | complies: the same file asserts a positive on the same observed object |
| settings_pj.test.mjs:133 | complies: the same file asserts a positive on the same observed object |
| settings_pj.test.mjs:135 | complies: `puts` read positively at :101 and :118 |
| settings_pj.test.mjs:159 | complies, as above |
| settings_ports_baud.test.mjs:62 | complies: the same file asserts a positive on the same observed object |
| settings_ports_baud.test.mjs:75 | complies: the same file asserts a positive on the same observed object |
| settings_ports_eol.test.mjs:110 | complies: the same file asserts a positive on the same observed object |
| settings_sessions_bundle.test.mjs:61 | complies: the same file asserts a positive on the same observed object |
| settings_storage_cap.test.mjs:50 | complies: the same file asserts a positive on the same observed object |
| state_eol.test.mjs:27 | complies: the same file asserts a positive on the same observed object |
| state_eol.test.mjs:28 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| state_eol.test.mjs:49 | complies: the same file asserts a positive on the same observed object |
| state_eol.test.mjs:50 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| state_eol.test.mjs:51 | complies: the URL the product requested; the capture helper nulls it first and asserts it was filled |
| state_eol.test.mjs:59 | complies: the same file asserts a positive on the same observed object |
| state_eol.test.mjs:60 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| state_eol.test.mjs:68 | complies: the same file asserts a positive on the same observed object |
| state_eol.test.mjs:69 | complies: the same file asserts a positive on the same observed object |
| state_eol.test.mjs:81 | complies: the same file asserts a positive on the same observed object |
| state_logic.test.mjs:70 | complies: the same file asserts a positive on the same observed object |
| state_logic.test.mjs:71 | complies: the same file asserts a positive on the same observed object |
| state_logic.test.mjs:106 | complies: the same file asserts a positive on the same observed object |
| state_logic.test.mjs:209 | complies: the same file asserts a positive on the same observed object |
| state_logic.test.mjs:232 | complies: the same file asserts a positive on the same observed object |
| state_logic.test.mjs:246 | complies: the same storage key is read positively in the same file |
| state_logic.test.mjs:248 | complies: the same file asserts a positive on the same observed object |
| state_logic.test.mjs:258 | complies: the same file asserts a positive on the same observed object |
| state_logic.test.mjs:267 | complies: the same file asserts a positive on the same observed object |
| state_logic.test.mjs:269 | complies: the same file asserts a positive on the same observed object |
| state_logic.test.mjs:277 | complies: the same file asserts a positive on the same observed object |
| state_logic.test.mjs:321 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| state_logic.test.mjs:368 | complies: the same file asserts a positive on the same observed object |
| state_logic.test.mjs:371 | complies: the same file asserts a positive on the same observed object |
| state_plot_tick.test.mjs:67 | complies: the same file asserts a positive on the same observed object |
| state_plot_tick.test.mjs:68 | complies: the same file asserts a positive on the same observed object |
| state_plot_tick.test.mjs:73 | complies: the same file asserts a positive on the same observed object |
| state_plot_tick.test.mjs:81 | complies: the same file asserts a positive on the same observed object |
| state_plot_tick.test.mjs:82 | complies: the same file asserts a positive on the same observed object |
| statusbar_attach_eol.test.mjs:85 | complies: the attach helper wrote the serial the assertion sees cleared |
| statusbar_logic.test.mjs:54 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:55 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:56 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:57 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:83 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:85 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:90 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:109 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:117 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| statusbar_logic.test.mjs:121 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| statusbar_logic.test.mjs:123 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:124 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:153 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:187 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| statusbar_logic.test.mjs:198 | violates, fixed: it followed an unticked attach (stub checkbox starts false); the check now also follows a ticked one; J1 red |
| statusbar_logic.test.mjs:284 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:308 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| statusbar_logic.test.mjs:327 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| statusbar_logic.test.mjs:339 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| statusbar_logic.test.mjs:351 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:352 | complies: the same storage key is read positively in the same file |
| statusbar_logic.test.mjs:363 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:367 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:391 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:408 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:410 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| statusbar_logic.test.mjs:418 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:433 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:438 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:450 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:463 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:464 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:525 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:565 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:570 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:572 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:594 | complies: the same file asserts a positive on the same observed object |
| statusbar_logic.test.mjs:610 | complies: the same file asserts a positive on the same observed object |
| statusbar_proto.test.mjs:56 | complies: the same file asserts a positive on the same observed object |
| statusbar_proto.test.mjs:60 | complies: the same file asserts a positive on the same observed object |
| statusbar_proto.test.mjs:61 | complies: the same file asserts a positive on the same observed object |
| statusbar_proto.test.mjs:62 | complies: the same file asserts a positive on the same observed object |
| statusbar_proto.test.mjs:65 | complies: the same file asserts a positive on the same observed object |
| statusbar_proto.test.mjs:66 | complies: the same file asserts a positive on the same observed object |
| statusbar_proto.test.mjs:67 | complies: the same file asserts a positive on the same observed object |
| statusbar_session_dialog.test.mjs:55 | complies: the same file asserts a positive on the same observed object |
| statusbar_session_dialog.test.mjs:57 | complies: a prefilled note is what it guards, written through the same element |
| statusbar_session_dialog.test.mjs:58 | complies: `posts` read positively from :116 |
| statusbar_session_dialog.test.mjs:69 | complies: the same file asserts a positive on the same observed object |
| statusbar_session_dialog.test.mjs:79 | complies: the same file asserts a positive on the same observed object |
| statusbar_session_dialog.test.mjs:84 | complies: the same file asserts a positive on the same observed object |
| statusbar_session_dialog.test.mjs:101 | complies: startSession sets disabled before its await and clears it in finally, so dropping the clear turns it red |
| statusbar_session_dialog.test.mjs:139 | complies: the id exists in index.html and product JS, and the file asserts the same id in another state |
| statusbar_session_dialog.test.mjs:142 | complies: the same file asserts a positive on the same observed object |
| statusbar_session_dialog.test.mjs:145 | complies: the same file asserts a positive on the same observed object |
| statusbar_session_dialog.test.mjs:146 | complies: the same file asserts a positive on the same observed object |
| statusbar_session_dialog.test.mjs:156 | complies: the same file asserts a positive on the same observed object |
| terminal_delta_mark.test.mjs:18 | complies: the same file asserts a positive on the same observed object |
| terminal_delta_mark.test.mjs:74 | complies: the same file asserts a positive on the same observed object |
| terminal_delta_mark.test.mjs:83 | complies: the same file asserts a positive on the same observed object |
| terminal_delta_mark.test.mjs:106 | complies: the same file asserts a positive on the same observed object |
| terminal_empty_state.test.mjs:87 | complies: the same file asserts a positive on the same observed object |
| terminal_empty_state.test.mjs:113 | complies: the same file asserts a positive on the same observed object |
| terminal_filter_pane.test.mjs:33 | complies: the same file asserts a positive on the same observed object |
| terminal_filter_pane.test.mjs:39 | complies: the same file asserts a positive on the same observed object |
| terminal_filter_pane.test.mjs:40 | complies: the same file asserts a positive on the same observed object |
| terminal_filter_pane.test.mjs:43 | complies: the same file asserts a positive on the same observed object |
| terminal_filter_pane.test.mjs:44 | complies: the same file asserts a positive on the same observed object |
| terminal_filter_pane.test.mjs:49 | complies: the same file asserts a positive on the same observed object |
| terminal_filter_pane.test.mjs:53 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:56 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:60 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:63 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:66 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:68 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:73 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:80 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:95 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:97 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:125 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:126 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:131 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:133 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:135 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:147 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:165 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:191 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:213 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:214 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:234 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:246 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:247 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:262 | complies: the same file asserts a positive on the same observed object |
| terminal_history.test.mjs:266 | complies: the same file asserts a positive on the same observed object |
| terminal_logic.test.mjs:71 | complies: the same file asserts a positive on the same observed object |
| terminal_logic.test.mjs:79 | complies: the same file asserts a positive on the same observed object |
| terminal_logic.test.mjs:190 | complies: the same file asserts a positive on the same observed object |
| terminal_logic.test.mjs:203 | complies: the same file asserts a positive on the same observed object |
| terminal_logic.test.mjs:206 | complies: the same file asserts a positive on the same observed object |
| terminal_logic.test.mjs:211 | complies: the same file asserts a positive on the same observed object |
| terminal_logic.test.mjs:223 | complies: the same file asserts a positive on the same observed object |
| terminal_logic.test.mjs:237 | complies: the same file asserts a positive on the same observed object |
| terminal_logic.test.mjs:308 | complies: the same file asserts a positive on the same observed object |
| terminal_paused_freeze.test.mjs:94 | complies: the same file asserts a positive on the same observed object |
| terminal_paused_freeze.test.mjs:130 | complies: the same file asserts a positive on the same observed object |
| terminal_toolbar_static.test.mjs:41 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| theme_a11y_static.test.mjs:87 | complies: the direct return value or DOM state the product wrote (the stub keeps any written value) |
| timewindow.test.mjs:39 | complies: the same file asserts a positive on the same observed object |
| timewindow.test.mjs:150 | complies: the same file asserts a positive on the same observed object |
| timewindow.test.mjs:154 | complies: the same file asserts a positive on the same observed object |

