# Test sweep 1 (class 75): per-site verdicts

Companion to `sweep-tests.md`. Line numbers are as enumerated at the start of the sweep, before the fixes moved them.

## Sweep 1, Python: 140 sites

| site | kind | name | verdict |
|---|---|---|---|
| support.py:46 | assign | `CHILD_TEXT` n=2 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_assert.py:864 | parametrize | `test_purge_refuses_a_non_finite_before_ts_and_deletes_nothing` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_break.py:56 | parametrize | `test_break_ms_out_of_range_is_422` n=4 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_break.py:143 | parametrize | `test_cli_break_ms_out_of_range_is_bad_usage` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli.py:1767 | assign | `_STATUS_STUB_BODY` n=4 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_cli.py:312 | parametrize | `test_windows_einval_from_the_final_flush_is_success` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli.py:327 | parametrize | `test_an_einval_escaping_a_command_is_a_real_failure` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli.py:355 | parametrize | `test_windows_einval_from_a_follow_write_is_success` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli.py:372 | parametrize | `test_windows_einval_inside_a_follow_ends_it_as_a_closed_pipe` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli.py:401 | parametrize | `test_windows_einval_while_rich_renders_help_is_success` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli.py:421 | parametrize | `test_a_non_finite_start_timeout_from_the_environment_is_ignored` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli.py:1166 | parametrize | `test_purge_without_yes_asks_and_deletes_nothing_when_refused` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli.py:1698 | parametrize | `test_a_bus_outside_1_to_9_is_refused_before_anything_is_sent` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim (inner loop covers all 4 derived `--bus` commands) |
| test_cli.py:1897 | parametrize | `test_daemon_stop_asks_status_before_giving_up_on_a_corrupt_record` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli.py:2321 | parametrize | `test_a_field_of_the_wrong_type_is_an_exit_code_not_a_traceback` n=6 | violates, fixed: LIST_FIELDS guard derives every `_list_field` key from cli.py; `forbid` case added in test_review_r2_cli |
| test_cli.py:2463 | parametrize | `test_status_shows_write_errors_only_when_non_zero` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli.py:2482 | parametrize | `test_status_reports_an_available_release` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli_contract.py:22 | assign | `UNREACHABLE` n=2 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_cli_contract.py:174 | assign | `GUIDE_EXEMPT` n=2 | complies: an exemption set on a mechanical enumeration; stale-exemption check added |
| test_cli_contract.py:123 | parametrize | `test_negative_limit_is_bad_usage` n=4 | violates, fixed: implicit every-`--limit`/`-n` list was 4 of 5; `log export --limit -1` added |
| test_cli_contract.py:140 | parametrize | `test_a_zero_timeout_is_bad_usage_where_the_daemon_needs_one` n=2 | exempt because it names where the daemon needs one: assert's 0 is retrospective by design, daemon start/restart take seconds |
| test_cli_contract.py:160 | parametrize | `test_min_window_is_bounded_by_the_client` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli_contract.py:228 | parametrize | `test_sysrq_refuses_a_non_printable_character` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli_contract.py:266 | parametrize | `test_eol_without_send_is_refused_client_side` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli_export.py:24 | assign | `UNREACHABLE` n=2 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_cli_export.py:60 | parametrize | `test_inverted_clock_bounds_are_refused_without_a_daemon` n=4 | violates, fixed: WINDOWED, guard derives every `--from` command from the click tree (4 = 4 today) |
| test_cli_export.py:107 | parametrize | `test_log_export_csv_refuses_the_paged_options` n=4 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli_r2026_09_12.py:23 | assign | `UNREACHABLE` n=2 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_cli_r2026_09_12.py:25 | assign | `STATUS` n=4 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_cli_r2026_09_12.py:63 | assign | `BOUNDED` n=5 | violates, fixed: BOUNDED, implicit every-`--from` list, guarded with EXPORTS (5 = 4 commands today) |
| test_cli_r2026_09_12.py:345 | assign | `CHANNELS` n=1 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_cli_r2026_09_12.py:373 | assign | `ATTACHED` n=1 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_cli_r2026_09_12.py:123 | parametrize | `test_bundle_refuses_a_db_name_whatever_its_case` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli_r2026_09_12.py:134 | parametrize | `test_every_export_refuses_the_stdout_token` n=5 | violates, fixed: EXPORTS, guard derives every `-o` command (4 = 4 today) |
| test_cli_r2026_09_12.py:193 | parametrize | `test_a_streamed_export_to_stdout_is_not_newline_translated` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli_r2026_09_12.py:292 | parametrize | `test_the_wait_timeout_line_has_no_send_counts_when_they_do_not_apply` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli_r2026_09_12.py:333 | parametrize | `test_status_stays_quiet_about_trimming_when_there_is_none` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli_r2026_09_12.py:458 | parametrize | `test_a_503_from_the_daemon_is_unreachable_not_an_error` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_cli_ux.py:68 | assign | `stderr_lines` n=0 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) (class attribute of a fake Popen) |
| test_cli_ux.py:70 | assign | `spawned` n=0 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) (class attribute of a fake Popen) |
| test_config_bools.py:23 | parametrize | `test_a_non_bool_port_flag_skips_the_port` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_config_ports_eol.py:18 | assign | `DIALOG_BODY` n=1 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_daemon_r2026_09_12_server.py:133 | assign | `HOSTILE` n=5 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_daemon_r2026_09_12_server.py:321 | parametrize | `test_a_non_finite_time_bound_is_refused_by_name` n=4 | exempt because no every-claim; derived since_ts/until_ts routes 4 = listed 4 |
| test_daemon_r2026_09_12_server.py:322 | parametrize | `test_a_non_finite_time_bound_is_refused_by_name` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_daemon_startup.py:227 | parametrize | `test_daemon_start_refuses_a_named_config_that_is_missing` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_eol.py:63 | parametrize | `test_loader_warns_and_defaults_on_a_bad_eol` n=5 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_eol.py:227 | parametrize | `test_an_unknown_request_eol_is_422` n=4 | violates, fixed: EOL_BODIES was 4 of 5 top-level `eol` bodies (POST /ports had one bad value); guard derives from the OpenAPI schema |
| test_eol.py:233 | parametrize | `test_an_unknown_request_eol_is_422` n=6 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_eol.py:261 | parametrize | `test_cli_refuses_an_unknown_eol` n=5 | exempt because no every-claim; derived `--eol` commands 5 = listed 5 |
| test_firmware_monitor.py:30 | assign | `SAN_FLAGS` n=2 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) (mirrors firmware/tests/Makefile flags for a link probe) |
| test_hardening.py:1657 | parametrize | `test_closed_query_domains_are_refused_too` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_hardening.py:1679 | parametrize | `test_closed_request_domains_are_refused_not_silently_reinterpreted` n=4 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_plotjuggler.py:82 | assign | `POINTS` n=2 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_plotjuggler.py:41 | parametrize | `test_parse_dest_refuses` n=18 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_plotjuggler.py:202 | parametrize | `test_non_unicast_dest_refused` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_port_health.py:594 | parametrize | `test_send_refuses_a_terminator_in_the_body` n=4 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_cli_fixes.py:27 | assign | `OLD` n=2 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_prerelease_cli_fixes.py:93 | assign | `GATED` n=7 | complies: names its options; every require_daemon/`gated` flag in cli.py is now guarded (test_prerelease_fixdiff_py) |
| test_prerelease_cli_fixes.py:165 | assign | `REFUSAL` n=2 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_prerelease_cli_fixes.py:467 | assign | `NOT_FOUND` n=2 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_prerelease_cli_fixes.py:41 | parametrize | `test_the_subscriber_cap_503_is_exit_1_not_unreachable` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_cli_fixes.py:135 | parametrize | `test_no_gate_request_without_a_gated_option` n=4 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_cli_fixes.py:168 | parametrize | `test_a_refused_export_keeps_the_file_and_the_link` n=5 | violates, fixed: implicit every-export list lacked `session export`; added (driven: /sessions/1/export refused, -o kept) |
| test_prerelease_cli_fixes.py:263 | parametrize | `test_a_clock_at_the_calendar_limit_is_bad_usage` n=4 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_cli_fixes.py:280 | parametrize | `test_the_paged_export_to_stdout_is_not_newline_translated` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_cli_fixes.py:305 | parametrize | `test_a_usage_error_with_stderr_closed_keeps_exit_1` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_cli_fixes.py:350 | parametrize | `test_session_export_refuses_a_directory_target` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_cli_fixes.py:351 | parametrize | `test_session_export_refuses_a_directory_target` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_cli_fixes.py:377 | parametrize | `test_the_truncation_note_names_only_the_commands_own_options` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_cli_fixes.py:382 | parametrize | `test_the_truncation_note_names_only_the_commands_own_options` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_cli_fixes.py:398 | parametrize | `test_a_usage_error_with_clock_bounds_costs_no_request` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_cli_fixes.py:428 | parametrize | `test_last_ms_out_of_range_is_bad_usage` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_cli_fixes.py:429 | parametrize | `test_last_ms_out_of_range_is_bad_usage` n=4 | violates, not fixed (product gap): derived `--last-ms` commands include `assert`, which has no client bound; reported |
| test_prerelease_cli_fixes.py:449 | parametrize | `test_attach_refuses_a_blank_serial` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_cli_fixes.py:470 | parametrize | `test_a_missing_route_names_the_daemon_version` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_daemon_core_plot.py:70 | parametrize | `test_a_deadband_value_outside_the_grammar_is_refused` n=8 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_daemon_core_plot.py:103 | parametrize | `test_a_name_listed_twice_is_refused` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_daemon_core_shutdown.py:23 | assign | `_HOST` n=1 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_prerelease_daemon_core_shutdown.py:144 | parametrize | `test_a_match_queued_ahead_of_the_sentinel_is_still_judged` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_daemon_core_windows.py:157 | parametrize | `test_a_negative_last_ms_is_refused` n=5 | violates, not fixed (product gap): derived last_ms routes include POST /assert, which refuses -60000 but also 0 (GETs take 0); reported |
| test_prerelease_daemon_core_windows.py:181 | parametrize | `test_a_window_crossing_its_session_is_answered_and_names_no_backwards_file` n=4 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_fixdiff_py.py:26 | assign | `OLD` n=2 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_prerelease_fixdiff_py.py:100 | assign | `LINE_FORMS` n=5 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_prerelease_fixdiff_py.py:230 | assign | `GATES` n=5 | violates, fixed: GATES claimed every gated option and lacked `--changes`/`--deadband`; added, guard derives gated flags from cli.py |
| test_prerelease_fixdiff_py.py:377 | assign | `PAGED_TO_FORMS` n=3 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_prerelease_fixdiff_py.py:110 | parametrize | `test_session_with_last_ms_on_the_cli_is_the_daemons_tail` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_fixdiff_py.py:269 | parametrize | `test_a_dead_stream_through_a_symlink_removes_the_file_it_resolves_to` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_fixdiff_py.py:308 | parametrize | `test_a_page_without_the_name_falls_back_to_the_quoted_path` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_link_fixes.py:71 | assign | `_SERVE_KW` n=? | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_prerelease_link_fixes.py:117 | assign | `NOT_DECIMAL` n=9 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_link_fixes.py:120 | parametrize | `test_sim_integer_flags_refuse_what_int_would_take` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_link_fixes.py:129 | parametrize | `test_sim_integer_flags_refuse_out_of_range` n=4 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_link_fixes.py:142 | parametrize | `test_flap_refuses_a_value_that_is_not_a_finite_duration` n=9 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_link_fixes.py:159 | parametrize | `test_daemon_port_flag_is_on_the_grammar_and_bounded` n=7 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_prerelease_link_fixes.py:292 | parametrize | `test_a_reattach_keeps_the_last_write_error` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_protocol.py:444 | assign | `PLOT_BODIES_REJECTED` n=19 | violates, fixed: had drifted from test_monitor.c (5 unit bodies missing); now read from that file, unit-charset divergence named |
| test_protocol.py:450 | assign | `PLOT_BODIES_ACCEPTED` n=5 | violates, fixed: as above |
| test_protocol.py:687 | assign | `_LOOSE_DECIMALS` n=7 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_protocol.py:690 | assign | `_DECIMAL_POSITIONS` n=10 | violates, fixed: guard proves every int()-calling function in protocol.py is reached; `!can id` and `can tx id` positions added |
| test_protocol.py:849 | assign | `_FUZZ_PREFIXES` n=11 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_protocol.py:31 | parametrize | `test_classify` n=5 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_protocol.py:196 | parametrize | `test_can_flags_round_trip` n=4 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_protocol.py:255 | parametrize | `test_malformed_can_event_returns_none` n=12 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_protocol.py:298 | parametrize | `test_a_non_bus_event_name_is_not_a_frame` n=4 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_protocol.py:305 | parametrize | `test_format_can_event_refuses_a_bus_it_cannot_name` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_protocol.py:754 | parametrize | `test_format_can_event_rejects_frames_parse_would_refuse` n=5 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_protocol.py:769 | parametrize | `test_response_formatters_range_check_the_seq` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_protocol.py:1048 | parametrize | `test_typed_f4_refuses_non_finite` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_reconnect.py:285 | assign | `_GOOD` n=? | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_reconnect.py:286 | assign | `_MORE` n=? | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_regressions.py:51 | parametrize | `test_parse_can_event_returns_none_never_raises` n=5 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_regressions.py:268 | parametrize | `test_retention_days_is_clamped_to_at_least_one` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_regressions.py:518 | parametrize | `test_can_tx_at_the_top_of_the_id_range_does_not_raise` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_regressions.py:830 | parametrize | `test_parse_seq_token_is_strict_ascii_decimal` n=8 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_regressions.py:838 | parametrize | `test_wire_lines_reject_loose_seq_tokens` n=5 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_review_r2_cli.py:201 | parametrize | `test_purge_before_days_refuses_a_non_positive_age` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_review_r2_cli.py:322 | parametrize | `test_ms_timeout_out_of_range_is_a_usage_refusal` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_review_r2_cli.py:341 | parametrize | `test_a_wrongly_typed_daemon_field_is_reported_not_a_traceback` n=5 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_review_r2_config.py:77 | parametrize | `test_every_unusable_started_formats_as_unknown` n=8 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_review_r2_server.py:302 | parametrize | `test_a_config_write_failure_is_a_500_naming_the_failure` n=4 | violates, fixed: was 4 of 5 `_save_error` routes (/config/ports missing); now parametrized over the guarded BODIES |
| test_review_r2_sim.py:21 | assign | `NON_FINITE_PATTERNS` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_rulings_cli_closed_pipe.py:61 | assign | `CASES` n=6 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) (one case per output path) |
| test_rulings_cli_closed_pipe.py:69 | assign | `USAGE` n=? | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_rulings_cli_closed_pipe.py:70 | assign | `TEXT` n=? | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_rulings_cli_closed_pipe.py:97 | parametrize | `test_the_repair_warning_on_a_closed_stderr_does_not_own_the_exit` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_rulings_cli_config.py:18 | assign | `spawned` n=0 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) (class attribute of a fake Popen) |
| test_rulings_daemon_config.py:28 | assign | `BODIES` n=5 | violates, fixed: BODIES, guard derives every PUT /config/* route (5 = 5 today) |
| test_scaffold.py:60 | parametrize | `test_console_scripts_run` n=3 | violates, fixed: CONSOLE_SCRIPTS, guard reads [project.scripts] from pyproject.toml |
| test_server_scope.py:98 | parametrize | `test_a_negative_limit_is_refused` n=3 | complies: the 4th derived limit route, /plot/series, is driven in the same file (needs `name`) |
| test_sessions.py:362 | parametrize | `test_session_name_bounds` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_sessions.py:369 | parametrize | `test_a_blank_session_name_is_refused_not_stored_empty` n=3 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_sim.py:545 | parametrize | `test_listener_survives_a_transient_accept_error` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_sim.py:615 | parametrize | `test_listener_stops_when_the_socket_is_gone` n=2 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_sim_tcp.py:30 | assign | `_ADDR_IN_USE` n=? | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_store_fastpaths.py:379 | parametrize | `test_token_entry_points_agree_with_the_raw_ones` n=9 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_timeline.py:140 | assign | `SAMPLES` n=4 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) |
| test_update_check.py:48 | parametrize | `test_is_newer` n=14 | exempt because the list is input examples for one behaviour, not an every-X claim |
| test_webui_js.py:45 | assign | `pytestmark` n=2 | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) (pytest marks) |
| test_webui_js.py:59 | assign | `GUARD_URLS` n=197 | violates, fixed: every refusal literal of exportdlg_guards.mjs must come back for some URL (25 of 25 today) |

## Sweep 1, JS: 241 sites (48 carry a coverage word nearby)

| site | text | verdict |
|---|---|---|
| api_plot_def_seed_ports.test.mjs:13 | `const DEFS = [{ id: 1, ts: 1, port: "p1", chan: "event", raw: "!pd 0 alpha:u1" }];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_plot_def_seed_ports.test.mjs:17 | `const SEED = [` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_plot_def_seed_ports.test.mjs:23 | `const defQueries = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_backfill.test.mjs:28 | `const errors = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_pane_queue.test.mjs:35 | `const rows = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_high_rate_pending.test.mjs:37 | `const rows = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_capture_reset_stage.test.mjs:27 | `let served = [];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| api_plot_seed_ports_fallback.test.mjs:11 | `const seen = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_plot_seed_ports_fallback.test.mjs:32 | `const errors = [];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| api_seed_group_window.test.mjs:14 | `const series = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_ws_backoff.test.mjs:26 | `const timers = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_plot_seed_ports.test.mjs:12 | `const SERIES = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_plot_seed_ports.test.mjs:17 | `const seen = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_backfill_paging.test.mjs:22 | `let queries = [];          // every /lines?since_id= request, in order` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| api_backfill_paging.test.mjs:31 | `const ids = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| app_resizer_keys.test.mjs:69 | `const focused = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_plot_def_seed.test.mjs:22 | `const SEED_ROWS = [` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_plot_def_seed.test.mjs:28 | `const DEF_ROWS = [` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_plot_def_seed.test.mjs:32 | `const seen = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_plot_def_seed.test.mjs:66 | `const names = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_plot_def_seed.test.mjs:68 | `for (const want of ["tri", "ramp", "ftest"]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| can_age_tick.test.mjs:41 | `const cells = [...wrap.querySelectorAll("td")].map((c) => c.textContent);` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_plot_seed.test.mjs:26 | `const CHANNELS = [` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_plot_seed.test.mjs:44 | `const SERIES = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_plot_seed.test.mjs:61 | `const SEED_ROWS = [` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_plot_seed.test.mjs:65 | `const DEF_ROWS = [` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_plot_seed.test.mjs:70 | `const seen = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| api_plot_seed.test.mjs:158 | `for (const want of ["tri", "state", "led", "sine", "old_temp"]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| cmdbar_mode.test.mjs:13 | `const posts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| cmdbar_bounds.test.mjs:17 | `const posts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| cmdbar_bounds.test.mjs:40 | `const seen = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| can_head.test.mjs:276 | `for (const id of ["canIdFilter", "canClear"]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| can_logic.test.mjs:41 | `const e = [...canRows.values()][0];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| can_logic.test.mjs:54 | `const rows = [...canRows.values()];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| can_logic.test.mjs:64 | `const bad = [` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| can_logic.test.mjs:175 | `for (const bad of ["!can0 100 - 1 DE", "!can22 100 - 1 DE", "!canx 100 - 1 DE", "!can 100 ` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| can_logic.test.mjs:388 | `const asked = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| chrome_radios.test.mjs:14 | `const log = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| digital_clear_segments.test.mjs:45 | `const log = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| digital_clear_segments.test.mjs:46 | `for (const k of ["moveTo", "lineTo", "fillRect"]) g[k] = (...a) => log.push([k, ...a]);` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| cmdbar_sole.test.mjs:15 | `const posts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| chrome.test.mjs:32 | `const firstEight = ["chart_a", "lane_a", "chart2_a", "lane_b", "n5", "n6", "n7", "n8"].map` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| chrome.test.mjs:44 | `for (const [name, slot] of [["toString", 0], ["constructor", 1], ["valueOf", 2],` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| chrome.test.mjs:72 | `const picked = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| chrome.test.mjs:91 | `const seen = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| chrome.test.mjs:102 | `const seen = [];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| chrome.test.mjs:162 | `const calls = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| chrome.test.mjs:165 | `const seen = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| cmdbar_eol.test.mjs:14 | `const posts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| cmdbar_eol.test.mjs:48 | `for (const a of ["auto", "board", "a", "b"]) setCmdModeFor(a, "cmd");   // bodies under te` | exempt because it is a fixture (a body, URL, flag set or data the test feeds in) (ports to set a mode on); the every-eol claim beside it is fixed: derived from protocol.py EOL_BYTES |
| dom_stub.mjs:172 | `emit(type, ev = {}) { for (const fn of [...(this.handlers.get(type) \|\| [])]) fn(ev); }` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| dom_stub.mjs:205 | `const doc = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| dom_stub.mjs:222 | `emit(type, ev = {}) { for (const fn of [...(this.handlers.get(type) \|\| [])]) fn(ev); },` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| dom_stub.mjs:226 | `const localStorage = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| dom_stub.mjs:238 | `const intervals = [];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| dom_stub.mjs:239 | `const frames = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| dom_stub.mjs:240 | `const sockets = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| dom_stub.mjs:241 | `const blobs = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| dom_stub.mjs:289 | `const syncSubs = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| dom_stub.mjs:304 | `const els = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| freeze.test.mjs:38 | `for (const s of [a, b, c]) {` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| freeze.test.mjs:43 | `for (const s of [a, b, c]) assert.equal(s.live, true);` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| digital_zoom.test.mjs:31 | `const vertices = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| digital_zoom.test.mjs:38 | `const lane = [...digitalLanes.values()][0];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| layout.test.mjs:14 | `for (const raw of [null, "", "{nope", "42", "null", "[]", '"wide"']) {` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| layout.test.mjs:22 | `for (const sideW of [-1, 0, null, 1e999]) {` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| layout.test.mjs:25 | `for (const canCap of [0, 4.9, 95.1, 100, -5]) {` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| layout.test.mjs:54 | `for (const t of ["", "   ", "\t\n", null, undefined]) assert.equal(cleanTitle(t), null, JS` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| layout.test.mjs:70 | `const items = [{ name: "a", top: 0 }, { name: "b", top: 470 }, { name: "c", top: 480 }, { ` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| exportdlg_guards.mjs:13 | `const CHANS = ["debug", "cmd", "resp", "event", "marker", "sys"];` | complies: CHANS/BOOLS/SPEC mirror the daemon and are contract-tested against it (test_webui_js) |
| exportdlg_guards.mjs:14 | `const BOOLS = ["0", "off", "f", "false", "n", "no", "1", "on", "t", "true", "y", "yes"];` | complies, as above |
| exportdlg_guards.mjs:60 | `const errs = [];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| exportdlg_guards.mjs:91 | `const LINES_SPEC = [` | complies, as above |
| exportdlg_guards.mjs:96 | `const CAN_SPEC = [` | complies, as above |
| exportdlg_guards.mjs:101 | `const PLOT_SPEC = [` | complies, as above |
| exportdlg_guards.mjs:117 | `for (const [field, v] of [["since_ts", s], ["until_ts", u]]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| export_paused_window.test.mjs:202 | `for (const src of ["[", "x".repeat(201)]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| export_paused_window.test.mjs:216 | `for (const mode of ["Shown", "Session"]) await pressExport(() => exportChart(chart), mode)` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| plots_seed_grammar.test.mjs:103 | `const errors = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_seed_grammar.test.mjs:131 | `const errors = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| exportrange.test.mjs:33 | `for (const poison of [` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| exportrange.test.mjs:88 | `for (const range of [` | exempt because the modes list drives every value of MODES (session, clock, shown) plus variants; loop is examples |
| exportdlg.test.mjs:14 | `const SESSIONS = [` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| exportdlg.test.mjs:385 | `const focused = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| exportdlg.test.mjs:386 | `for (const id of ["expModeSession", "expModeClock", "expModeShown"]) env.byId(id).focus = ` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_channel_cap.test.mjs:19 | `const warned = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_zoom.test.mjs:39 | `const calls = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_export_button.test.mjs:100 | `const lanes = [...digitalLanes.values()];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_export_button.test.mjs:112 | `for (const [open, what] of [[() => exportChart(chart), "chart"], [exportDigital, "digital"` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_export_button.test.mjs:157 | `for (const open of [() => exportChart(chart), exportDigital]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_export_button.test.mjs:158 | `for (const changes of [false, true]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_export.test.mjs:11 | `const SESSIONS = [` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_export.test.mjs:15 | `const held = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_export.test.mjs:17 | `let fetches = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_export.test.mjs:35 | `const built = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_export.test.mjs:46 | `for (const order of ["older first", "newer first"]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_export.test.mjs:95 | `const created = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_export.test.mjs:107 | `for (const path of ["/can/frames?since_ts=1&format=csv", "/lines/export?format=jsonl",` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| plots_seed_paused.test.mjs:16 | `const CHANNELS = [` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_seed_paused.test.mjs:22 | `const POINTS = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_seed_paused.test.mjs:33 | `const BACKFILL = [{ id: 9, ts: 1000.5, port: "p1", chan: "debug", raw: "boot" }];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_theme_rebuild.test.mjs:66 | `const before = [...charts.values()].map((c) => c.uplot);` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_settings.test.mjs:27 | `const puts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_settings.test.mjs:30 | `const held = [];                 // [{path, release(body)}] for paths in `hold`` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_settings.test.mjs:32 | `let sessions = [{ id: 1, name: "a", started_ts: 1, ended_ts: 2, lines: 3, auto: false }];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_settings.test.mjs:73 | `const reported = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_settings.test.mjs:88 | `for (const bytes of [1500000, 1600000, 1048577, 2 ** 42 - 1]) {` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| prerelease_chrome_settings.test.mjs:213 | `const armed = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_chrome.test.mjs:165 | `const loud = [...css.matchAll(/display:\s*([\w-]+)\s*!important/g)].map((m) => m[1]);` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| plots_chrome.test.mjs:173 | `for (const want of ["!p &lt;tick&gt; &lt;name&gt;=&lt;value&gt;", "!p 1234 temp=21.5", "!p` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_chrome.test.mjs:183 | `for (const want of ["white-space: nowrap", "overflow: hidden", "text-overflow: ellipsis"])` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_chrome.test.mjs:223 | `const texts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_static.test.mjs:22 | `const slots = [...html.matchAll(/<[a-z]+\b[^>]*class="[^"]*\binline-err\b[^"]*"[^>]*>/g)].` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| prerelease_chrome_static.test.mjs:30 | `for (const id of ["cmdInput", "markerInput"]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_static.test.mjs:55 | `for (const id of ["#collapseBtn", "#popoutBtn"]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_finite.test.mjs:35 | `const out = [];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| plots_finite.test.mjs:45 | `const out = [];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| plots_finite.test.mjs:50 | `for (const v of [...allNumbers(), ...allLaneNumbers()]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_finite.test.mjs:150 | `const good = ["0", "-0", "12", "-12", "1.25", "-1.25", "1e3", "1E3", "1e+3", "1e-3", "1.5e` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_finite.test.mjs:155 | `const bad = ["", "+1", ".5", "1.", "1e", "e5", "0x10", "1,5", "nan", "NaN", "inf", "Infini` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_finite.test.mjs:225 | `for (const t of ["toString", "constructor", "hasOwnProperty", "__proto__", "valueOf"]) {` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| prerelease_webui-panes_can.test.mjs:99 | `const CORPUS = [` | exempt because the corpus is SPEC 2.5 wire forms with no enumerable table; each row key comes from the real decoder |
| prerelease_webui-panes_can.test.mjs:110 | `const keys = [...C.canRows.keys()];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_solo.test.mjs:21 | `const names = ["a", "b", "c"];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| plots_solo.test.mjs:27 | `const names = ["a", "b", "c"];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| plots_solo.test.mjs:34 | `const names = ["a", "b", "c"];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_solo.test.mjs:42 | `const names = ["a", "b"];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| plots_solo.test.mjs:88 | `const names = [...digitalLanes.keys()];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_ports_baud.test.mjs:16 | `const CONFIG = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_ports_baud.test.mjs:25 | `const puts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_ports_baud.test.mjs:55 | `for (const [label, value] of [["cleared", ""], ["zero", "0"],` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_webui-panes_terminal.test.mjs:29 | `const waiting = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_webui-panes_terminal.test.mjs:36 | `const lines = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_webui-panes_terminal.test.mjs:179 | `for (const d of [marker, gap]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| state_eol.test.mjs:55 | `for (const bad of ["cr", "LF", "\r\n", "lf ", null, undefined, 1, {}]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| rulings_panes_tickclock.test.mjs:13 | `const out = [];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| rulings_panes_reset.test.mjs:38 | `const rows = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| rulings_panes_reset.test.mjs:88 | `const calls = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| rulings_panes_reset.test.mjs:156 | `const frozenTicks = [...c.frozen.xsTick];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_statusbar.test.mjs:14 | `const heldDevices = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_statusbar.test.mjs:56 | `const focused = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_statusbar.test.mjs:68 | `const focused = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_chrome_statusbar.test.mjs:153 | `const armed = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_ports_identify.test.mjs:10 | `const CONFIG = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_ports_identify.test.mjs:21 | `const puts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_ports_bound.test.mjs:12 | `const CONFIG = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_ports_bound.test.mjs:20 | `const puts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_fixdiff_webui.test.mjs:41 | `const held = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_fixdiff_webui.test.mjs:129 | `const closes = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_fixdiff_webui.test.mjs:138 | `const built = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_fixdiff_webui.test.mjs:165 | `const built = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_fixdiff_webui.test.mjs:208 | `const armed = [];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| prerelease_fixdiff_webui.test.mjs:215 | `const built = [];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| prerelease_fixdiff_webui.test.mjs:244 | `const rows = [];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| prerelease_fixdiff_webui.test.mjs:350 | `const CONFIG = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_fixdiff_webui.test.mjs:373 | `const held = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_fixdiff_webui.test.mjs:432 | `for (const changed of [false, true]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_fixdiff_webui.test.mjs:454 | `for (const [section, field, value, save] of [["Server", "cfgPort", "8600", "cfgServerSave"` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| prerelease_fixdiff_webui.test.mjs:461 | `for (const restart of [false, true]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_ports_eol.test.mjs:10 | `const CONFIG = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_ports_eol.test.mjs:20 | `const puts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_ports_eol.test.mjs:22 | `let sessions = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| statusbar_attach_eol.test.mjs:11 | `const requests = [];        // [method, path, body]` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| rulings_chrome_settings.test.mjs:34 | `const puts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| rulings_chrome_settings.test.mjs:35 | `const names = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| rulings_chrome_settings.test.mjs:36 | `const navigations = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| rulings_chrome_settings.test.mjs:37 | `const reported = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| rulings_chrome_settings.test.mjs:117 | `for (const e of ["cfgServerErr", "cfgStorageErr", "cfgUpdateErr", "cfgPortsErr", "cfgPjErr` | violates, fixed: E-8 saves derived from index.html cfg*Save ids |
| rulings_chrome_settings.test.mjs:194 | `const W = ["config: unknown key 'prot' in [server], ignored; did you mean 'port'?",` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_offline.test.mjs:9 | `const CONFIG = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_offline.test.mjs:17 | `const puts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_offline.test.mjs:33 | `const DAEMON_CONTROLS = ["cfgServerSave", "cfgStorageSave", "cfgUpdateSave", "cfgPjSave", ` | violates, fixed: DAEMON_CONTROLS derived from index.html cfg*Save/Add ids |
| state_plot_tick.test.mjs:38 | `const cases = [` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| state_plot_tick.test.mjs:95 | `for (const bad of [undefined, null, "abc", NaN, Infinity]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_dirty.test.mjs:12 | `const CONFIG = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_dirty.test.mjs:20 | `const puts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_dirty.test.mjs:38 | `let confirms = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_dirty.test.mjs:75 | `for (const id of ["cfgServerSave", "cfgStorageSave", "cfgUpdateSave", "cfgTokenSave", "cfg` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_storage_cap.test.mjs:13 | `const CONFIG = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_storage_cap.test.mjs:22 | `const puts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| rulings_chrome_export.test.mjs:11 | `const SESSIONS = [{ id: 7, name: "run-b", lines: 5, started_ts: 3000, ended_ts: null, auto` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| rulings_chrome_export.test.mjs:13 | `let fetches = [];         // [url, opt]` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| rulings_chrome_export.test.mjs:43 | `const navigations = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| rulings_chrome_export.test.mjs:105 | `const armed = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_pj.test.mjs:14 | `const CONFIG = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_pj.test.mjs:31 | `const puts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_sessions_bundle.test.mjs:10 | `const CONFIG = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_sessions_bundle.test.mjs:19 | `const SESSIONS = {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| settings_sessions_bundle.test.mjs:25 | `const gets = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| terminal_filter_pane.test.mjs:16 | `for (const [i, raw] of ["!can 10 - 100 0102", "!can 11 - 321 0304", "!can2 12 - 321 05"].e` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| statusbar_proto.test.mjs:16 | `const posts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| statusbar_proto.test.mjs:53 | `for (const alias of ["constructor", "toString"]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| state_logic.test.mjs:127 | `const xs = [0, 1, 2, 10];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| state_logic.test.mjs:149 | `const created = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| state_logic.test.mjs:196 | `const created = [];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| state_logic.test.mjs:237 | `const seen = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| state_logic.test.mjs:286 | `const seen = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| state_logic.test.mjs:330 | `for (const alias of ["constructor", "toString", "valueOf"]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| state_logic.test.mjs:344 | `for (const bogus of ["bogus", "", null, undefined, "CMD", 1, {}]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| state_logic.test.mjs:353 | `const created = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| terminal_paused_freeze.test.mjs:15 | `let served = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| terminal_ts_width.test.mjs:19 | `const out = [];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| terminal_history.test.mjs:16 | `let queries = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| terminal_history.test.mjs:32 | `const ids = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| terminal_history.test.mjs:185 | `const errs = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| smoke.test.mjs:20 | `const ORDER = ["timewindow.js", "pane.js", "freeze.js", "layout.js", "chrome.js", "state.j` | complies: ORDER is checked against readdirSync(webui) |
| smoke.test.mjs:37 | `const expect = {` | violates, fixed: `expect` covered 14 of 18 modules; 4 added and its keys asserted equal to ORDER |
| statusbar_session_dialog.test.mjs:11 | `let posts = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| statusbar_session_dialog.test.mjs:63 | `for (const name of ["", "   \t"]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| statusbar_session_dialog.test.mjs:163 | `const cases = [` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| theme_a11y_static.test.mjs:18 | `const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| theme_a11y_static.test.mjs:27 | `for (const theme of ["light", "dark"]) {` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| theme_a11y_static.test.mjs:30 | `for (const bg of ["bg", "panel", "panel-2"]) {` | exempt because 'surface' is a design role: the three background vars are every one in style.css today; not derivable from border/text vars by name |
| theme_a11y_static.test.mjs:42 | `const base = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| theme_a11y_static.test.mjs:49 | `for (const bg of ["bg", "panel", "panel-2"]) {` | exempt, as above |
| theme_a11y_static.test.mjs:50 | `for (const [label, surface] of [[bg, v[bg]], [`accent-soft over ${bg}`, over(v["accent-sof` | exempt, as above |
| theme_a11y_static.test.mjs:72 | `const dialogs = [...html.matchAll(/<dialog id="(\w+)"([^>]*)>/g)];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| theme_a11y_static.test.mjs:79 | `const described = [...html.matchAll(/aria-describedby="(\w+)"/g)].map((m) => m[1]);` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| theme_a11y_static.test.mjs:85 | `for (const id of ["devSel", "sesName", "cfgHost"]) assert.match(html, new RegExp(`id="${id` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| theme_a11y_static.test.mjs:91 | `for (const id of ["cfgServerSave", "cfgStorageSave", "cfgUpdateSave", "cfgTokenSave", "cfg` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| terminal_empty_state.test.mjs:47 | `const all = [{ ...base, ports: 0 }, base, { ...base, cleared: true }, { ...base, total: 5,` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| terminal_toolbar_static.test.mjs:39 | `const buttons = [...group[1].matchAll(/class="iconbtn (\w+)"/g)].map((m) => m[1]);` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| terminal_tick_estimate.test.mjs:75 | `let served = [];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| timewindow.test.mjs:26 | `for (const bad of [0, NaN, undefined]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| timewindow.test.mjs:44 | `for (const mode of ["host", "tick"]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| timewindow.test.mjs:46 | `for (const t of [5000, 4999.5, 4985, 4970.001]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| timewindow.test.mjs:79 | `const xs = [0, 10, 20, 30, 40, 50, 60];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| timewindow.test.mjs:92 | `const xs = [0, 1, 2, 3];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| timewindow.test.mjs:99 | `const xs = [0, 10, 20, 30, 40, 50];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| timewindow.test.mjs:140 | `for (const bad of [{ mode: "host", min: 5, max: 5 }, { mode: "host", min: 9, max: 4 },` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| timewindow.test.mjs:169 | `for (const [span, max] of [[0.9, 3], [7, 2], [3000, 5], [86400 * 3, 4]]) {` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| terminal_logic.test.mjs:255 | `const before = [...pane.vlist.children];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| terminal_logic.test.mjs:265 | `const after = [...pane.vlist.children];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| terminal_logic.test.mjs:287 | `const before = [...pane.vlist.children];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| terminal_logic.test.mjs:291 | `const after = [...pane.vlist.children];` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| terminal_logic.test.mjs:333 | `const shown = [...pane.vlist.children];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| statusbar_logic.test.mjs:19 | `let posted = [];      // [path, method] of every non-GET request` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| statusbar_logic.test.mjs:20 | `let devices = [];     // GET /devices answer` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |
| statusbar_logic.test.mjs:314 | `for (const bad of ["javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,x", "", n` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| statusbar_logic.test.mjs:347 | `for (const stored of ["9.9.8", "", "{not json", "null"]) {` | exempt because no coverage word at the site, its 3 lines above or its test title: fixture or input examples |
| statusbar_logic.test.mjs:534 | `const errors = [];` | exempt because the coverage word describes behaviour, not the list (fixture or input examples) |

