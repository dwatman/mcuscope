| id | file:line | change | killed-by |
|---|---|---|---|
| P01 | server.py:301 | `if not v:` -> `if False:` | tests/test_sessions.py::test_a_blank_session_name_is_refused_not_stored_empty (3 params) |
| P02 | server.py:1564 | `if not math.isfinite(body.before_ts):` -> `if False:` | tests/test_assert.py::test_purge_refuses_a_non_finite_before_ts_and_deletes_nothing[Infinity] |
| P03 | server.py:1441 | `async with store._sweep_lock:` -> `if True:` | tests/test_daemon_r2026_09_12_bundle.py::test_a_purge_of_the_span_waits_for_a_bundle_in_progress |
| P04 | server.py:1492 | `store.export_session_db(db_path, id_from=lo, id_to=hi, se...` -> `store.export_session_db(db_path, id_from=lo, id...` | tests/test_daemon_r2026_09_12_bundle.py::test_an_open_sessions_members_all_cover_the_same_span |
| P05 | server.py:1511 | `"to_id": hi,` -> `"to_id": session["end_id"],` | tests/test_daemon_r2026_09_12_bundle.py::test_an_open_sessions_members_all_cover_the_same_span |
| P06 | server.py:1732 | `if not element:` -> `if False:` | tests/test_export_lines_can.py::test_can_id_list_refuses_an_empty_or_unparsable_element |
| P07 | server.py:1777 | `m = (own if own is not None else merged).get(ch["name"], {})` -> `m = merged.get(ch["name"], {})` | tests/test_decode_per_port.py::test_channels_label_each_attached_board_from_its_own_definition |
| P08 | server.py:1798 | `return {"channels": out, "ports": await store.plot_ports_...` -> `return {"channels": out}` | tests/test_decode_per_port.py::test_channels_label_each_attached_board_from_its_own_definition |
| P09 | server.py:1874 | `if unknown:` -> `if len(unknown) == len(name_list):` | tests/test_review_r2_server.py::test_plot_export_refuses_one_unknown_name_among_several |
| P10 | server.py:2937 | `return bool(kinds) and all(k in ("enum", "bit") for k in ...` -> `return bool(kinds) and any(k in ("enum", "bit")...` | tests/test_plot_export_decode.py::test_deadband_is_accepted_where_another_stream_declares_the_name_numeric |
| P11 | server.py:2937 | `return bool(kinds) and all(k in ("enum", "bit") for k in ...` -> `return bool(kinds) and all(k in ("enum",) for k...` | tests/test_plot_export_decode.py::test_deadband_on_a_decoded_bit_lane_is_refused |
| P12 | server.py:2183 | `raise CaptureStopped(_SHUTDOWN_MSG)` -> `rows = [r for r in rows if r is not None]` | tests/test_daemon_r2026_09_12_server.py::test_an_assert_cut_short_by_shutdown_is_a_503 |
| P13 | server.py:2000 | `if None in rows:` -> `if False:` | SURVIVED (full suite) |
| P14 | server.py:1926 | `return JSONResponse(status_code=503, content={"error": _S...` -> `return JSONResponse(status_code=500, content={"...` | tests/test_daemon_r2026_09_12_server.py::test_a_wait_cut_short_by_shutdown_is_a_503_not_a_timeout |
| P15 | server.py:1935 | `return JSONResponse(status_code=503, content={"error": _S...` -> `return JSONResponse(status_code=500, content={"...` | tests/test_daemon_r2026_09_12_server.py::test_an_assert_cut_short_by_shutdown_is_a_503 |
| P16 | server.py:2639 | `raise StarletteHTTPException(400, f"no such session: {ref}")` -> `return SessionRange(1, 0)` | tests/test_assert.py::test_unknown_session_is_an_error_not_a_pass |
| P17 | server.py:2665 | `if value is not None and not math.isfinite(value):` -> `if False:` | tests/test_webui_js.py::test_export_guard_double_agrees_with_the_daemon |
| P18 | server.py:2693 | `except (OverflowError, OSError, ValueError):   # past the...` -> `except (ValueError,):   # past the platform's t...` | tests/test_webui_js.py::test_export_guard_double_agrees_with_the_daemon |
| P19 | server.py:2774 | `{_csv_cell(r['raw'], formula_guard=False)}` -> `{_csv_cell(r['raw'])}` | tests/test_daemon_r2026_09_12_server.py::test_csv_export_does_not_rewrite_a_captured_line |
| P20 | server.py:2773 | `{_csv_cell(r['dir'], formula_guard=False)}` -> `{_csv_cell(r['dir'])}` | tests/test_daemon_r2026_09_12_server.py::test_csv_export_does_not_rewrite_a_captured_line |
| P21 | server.py:2880 | `ts, tick, port, values = r["ts"], r["tick_ms"], r["port"]...` -> `ts, tick, port, values = r["ts"], r["tick_ms"],...` | tests/test_decode_per_port.py::test_wide_decode_and_changes_are_per_board |
| P22 | server.py:3026 | `key = (row["port"], row["sid"], row["name"])` -> `key = (None, row["sid"], row["name"])` | tests/test_decode_per_port.py::test_long_changes_keep_one_baseline_per_board |
| P23 | server.py:2903 | `if not sep:` -> `if False:` | tests/test_plot_export_decode.py::test_deadband_without_an_equals_names_the_syntax_not_the_channel |
| P24 | server.py:2913 | `if not value.isascii() or not math.isfinite(band):` -> `if not math.isfinite(band):` | tests/test_plot_export_decode.py::test_deadband_value_must_be_a_finite_ascii_quantity |
| P25 | server.py:2913 | `if not value.isascii() or not math.isfinite(band):` -> `if not value.isascii():` | tests/test_webui_js.py::test_export_guard_double_agrees_with_the_daemon |
| P26 | server.py:3008 | `yield _render(row, dmaps.get(row["port"], {}))` -> `yield _render(row, next(iter(dmaps.values()), {}))` | tests/test_decode_per_port.py::test_a_redefinition_on_one_board_does_not_reach_the_other |
| P27 | server.py:3055 | `if r["id"] < first_id:` -> `if r["id"] <= first_id:` | EQUIVALENT: first_id is a sample row's id, never a !pd row's |
| P28 | server.py:3062 | `primed.reverse()   # newest first` -> `pass   # newest first` | tests/test_plot_export_decode.py::test_a_name_another_stream_declared_does_not_relabel_this_row |
| P29 | server.py:3086 | `labels.setdefault(n, entry[0])` -> `labels[n] = entry[0]` | SURVIVED (full suite) |
| P30 | server.py:2747 | `lows.append(store._window_floor(last_ms, id_to))` -> `lows.append(store._window_floor(last_ms, None))` | tests/test_daemon_r2026_09_12_server.py::test_last_ms_in_a_filename_is_anchored_where_the_rows_are |
| P31 | store.py:988 | `if "\n" in raw or "\r" in raw:` -> `if "\n" in raw:` | SURVIVED (full suite) |
| P32 | store.py:989 | `raw = raw.replace("\r\n", " ").replace("\r", " ")` -> `raw = raw.replace("\r", " ")` | tests/test_daemon_r2026_09_12_server.py::test_a_multi_line_marker_is_stored_as_one_line |
| P33 | store.py:1581 | `"SELECT MAX(id) FROM (SELECT id FROM lines INDEXED BY idx...` -> `"SELECT id FROM lines WHERE ts <= ? ORDER BY ts...` | SURVIVED (full suite) |
| P34 | store.py:1909 | `clauses.append(f"+cf.can_id IN ({','.join('?' * len(ids))...` -> `clauses.append(f"cf.can_id IN ({','.join('?' * ...` | tests/test_daemon_r2026_09_12_store.py::test_can_id_list_keeps_driving_from_the_frame_table |
| P35 | store.py:631 | `if q.full():` -> `with contextlib.suppress(asyncio.QueueFull):` | tests/test_daemon_r2026_09_12_store.py::test_stop_wakes_every_subscriber_with_a_sentinel |
| P36 | store.py:2671 | `if freelist >= _RECLAIM_MIN_PAGES:` -> `if False:` | tests/test_daemon_r2026_09_12_store.py::test_the_age_sweep_path_reclaims_too |
| P37 | store.py:2701 | `self._reclaim_backlog()` -> `pass` | tests/test_daemon_r2026_09_12_store.py::test_the_age_sweep_path_reclaims_too |
| P38 | store.py:2018 | `if self._plot_dirty:` -> `if False:` | SURVIVED (full suite) |
| P39 | store.py:546 | `conn.execute("PRAGMA cache_size=-65536")` -> `pass` | tests/test_daemon_r2026_09_12_store.py::test_the_writer_connection_gets_a_page_cache |
| P40 | cli.py:307 | `return re.sub(r"[^A-Za-z0-9_.-]", "-", base)[:32].lstrip(...` -> `return base` | tests/test_cli_r2026_09_12.py::test_attach_derives_an_alias_inside_the_grammar_from_any_serial |
| P41 | cli.py:357 | `if device and serial:` -> `if False:` | tests/test_cli_r2026_09_12.py::test_attach_refuses_a_device_and_a_serial_together |
| P42 | cli.py:359 | `if not device and not serial:` -> `if False:` | tests/test_cli_r2026_09_12.py::test_attach_refuses_neither |
| P43 | cli.py:628 | `if is_newer(CLOCK_BOUND_MIN_VERSION, version):` -> `if False:` | tests/test_cli_r2026_09_12.py::test_plot_export_decode_is_refused_against_a_daemon_that_drops_it |
| P44 | cli.py:645 | `if since_ts is not None or until_ts is not None:` -> `if since_ts is not None:` | tests/test_cli_r2026_09_12.py::test_clock_bounds_are_allowed_against_a_current_daemon[argv0] |
| P45 | cli.py:676 | `for r in reversed(rows):   # newest first` -> `for r in rows:   # newest first` | tests/test_timeline.py::test_decode_primes_from_a_definition_outside_the_window (the full run's first failure was a flake, F-5) |
| P46 | cli.py:677 | `dec.prime([r["raw"]], r.get("port"))` -> `dec.prime([r["raw"]], None)` | tests/test_decode_per_port.py::test_cli_priming_reaches_a_board_behind_many_rebroadcasts_of_another |
| P47 | cli.py:701 | `if dec is not None and baseline is not None:` -> `if False:` | tests/test_timeline.py::test_tail_snapshot_hands_its_changes_baseline_to_the_follow |
| P48 | cli.py:1165 | `if send_cmd is not None and repeat_ms is None and "sends"...` -> `if send_cmd is not None and "sends" in res:` | tests/test_cli_r2026_09_12.py::test_the_wait_timeout_line_has_no_send_counts_when_they_do_not_apply[repeat] |
| P49 | cli.py:1343 | `if out_file.lower().endswith(".db"):` -> `if out_file.endswith(".db"):` | tests/test_cli_r2026_09_12.py::test_bundle_refuses_a_db_name_whatever_its_case[run.Db] |
| P50 | cli.py:1395 | `if out_file == "-":` -> `if False:` | tests/test_cli_r2026_09_12.py::test_every_export_refuses_the_stdout_token[argv0] |
| P51 | cli.py:1512 | `_stdout_untranslated()` -> `pass` | tests/test_cli_r2026_09_12.py::test_a_streamed_export_to_stdout_is_not_newline_translated[argv2] |
| P52 | cli.py:1598 | `else "--changes" if changes else "--names")` -> `else "--names" if changes else "--names")` | tests/test_cli_export.py::test_log_export_csv_refuses_the_paged_options[extra2] |
| P53 | cli.py:1783 | `if to is not None and follow:` -> `if False:` | tests/test_cli_r2026_09_12.py::test_can_dump_refuses_an_upper_bound_with_follow |
| P54 | cli.py:1790 | `if csv:` -> `if False:` | tests/test_cli_r2026_09_12.py::test_can_dump_csv_is_refused_against_a_daemon_that_drops_format |
| P55 | cli.py:1796 | `if session:` -> `if False:` | tests/test_cli_r2026_09_12.py::test_can_dump_forwards_the_session |
| P56 | cli.py:2110 | `if decode or changes or deadband:` -> `if decode:` | EQUIVALENT: --changes needs --decode and --deadband needs --changes, refused before the gate |
| P57 | cli.py:2217 | `if named and not os.path.exists(named):` -> `if False:` | tests/test_daemon_startup.py::test_daemon_start_warns_that_the_named_config_is_missing[option] |
| P58 | cli.py:2063 | `f"last={_fmt_value(last) if isinstance(last, float) else ...` -> `f"last={last}{unit}  "` | tests/test_cli_r2026_09_12.py::test_plot_channels_renders_the_last_value_readably |
| P59 | cli.py:147 | `trim = f"  trimmed={trimmed}" if trimmed else ""` -> `trim = ""` | tests/test_cli_r2026_09_12.py::test_status_reports_trimmed_lines_when_there_are_some |
| P60 | cli_client.py:165 | `if resp.status_code == 503:` -> `if False:` | tests/test_cli_r2026_09_12.py::test_a_503_from_the_daemon_is_unreachable_not_an_error[argv1] |
| P61 | cli_output.py:296 | `return self._pds.setdefault(port, p.PlotDecoder())` -> `return self._pds.setdefault(None, p.PlotDecoder())` | tests/test_decode_per_port.py::test_cli_priming_covers_more_boards_than_the_old_cap |
| P62 | cli_output.py:321 | `self._last.get((port, key)) == rendered` -> `self._last.get((None, key)) == rendered` | tests/test_timeline.py::test_line_decoder_keeps_two_boards_streams_apart |
| P63 | config.py:332 | `_check_unknown(data)` -> `pass` | tests/test_daemon_r2026_09_12_config.py::test_every_unrecognised_key_is_named_with_its_section |
| P64 | config.py:306 | `_warn_unknown(entry, _KNOWN_PORT_KEYS, where)` -> `pass` | tests/test_daemon_r2026_09_12_config.py::test_a_port_entry_with_no_alias_is_still_placed |
| P65 | daemon.py:277 | `store.stop_subscribers()` -> `pass` | tests/test_daemon_r2026_09_12_server.py::test_an_assert_cut_short_by_shutdown_is_a_503 |
| P66 | daemon.py:289 | `if not server.started:` -> `if False:` | SURVIVED (full suite) |
| P67 | daemon.py:192 | `cfg_line = (f"config: {cfg_file}" if cfg_file.exists()` -> `cfg_line = (f"config: {cfg_file}" if True` | tests/test_daemon_startup.py::test_startup_says_a_named_config_was_not_found |
| P68 | daemon.py:399 | `if config.plotjuggler.enabled:` -> `if False:` | tests/test_daemon_startup.py::test_startup_names_the_plotjuggler_destination |
| P69 | protocol.py:985 | `elif chan.kind != "bits" and chan.name == name:` -> `elif chan.name == name:` | SURVIVED (full suite) |
| P70 | sim.py:439 | `if self.args.demo and key not in DEMO_CAN_IDS:` -> `if False:` | tests/test_sim.py::test_demo_keeps_one_can_id_per_table_feature |
| P71 | serial_link.py:1401 | `return {alias: port.plot_decoder.channel_meta() for alias...` -> `return {}` | tests/test_decode_per_port.py::test_channels_label_each_attached_board_from_its_own_definition |
| P72 | server.py:1565 | `return _bad_request("before_ts must be a finite number")` -> `return _bad_request("bad before_ts")` | tests/test_assert.py::test_purge_refuses_a_non_finite_before_ts_and_deletes_nothing[NaN] |
| P73 | server.py:3091 | `if scan.learn(raw):` -> `if False:` | SURVIVED (full suite) |
| P74 | cli.py:717 | `dec.decode(defs[di]["raw"], defs[di].get("port"))` -> `dec.decode(defs[di]["raw"], None)` | tests/test_timeline.py::test_decode_uses_the_definition_in_force_at_each_point_of_the_window |
| P75 | cli.py:1017 | `dec.decode(row["raw"], row.get("port"))   # learn it even...` -> `dec.decode(row["raw"], None)   # learn it even ...` | SURVIVED (full suite) |
| P76 | server.py:1665 | `return {"lines": rows, "truncated": truncated}` -> `return {"lines": rows, "truncated": False}` | tests/test_server_scope.py::test_limit_zero_is_still_the_no_backfill_probe |
| J01 | webui/can.js:480 | `e.gaps >= CAN_PERIODIC_GAPS` -> `e.gaps >= 0` | periodic needs three steady gaps; irregular gaps never qualify [can_age_tick.test.mjs] |
| J02 | webui/can.js:480 | `&& e.jitter <= CAN_JITTER_MAX * e.period` -> `&& true` | periodic needs three steady gaps; irregular gaps never qualify [can_age_tick.test.mjs] |
| J03 | webui/can.js:487 | `if (!periodic \|\| periodMs == null) return "age-fresh";` -> `if (periodMs == null) return "age-fresh";` | an id with no period, or an irregular one, is never coloured [can_age_tick.test.mjs] |
| J04 | webui/can.js:489 | `Math.max(2 * CAN_STALE_MIN_S, 10 * p)` -> `Math.max(CAN_STALE_MIN_S, 10 * p)` | a 1 kHz id is held to the 250 ms and 500 ms delivery-jitter floor, not a 1 s one [can_age_tick.test.mjs] |
| J05 | webui/can.js:490 | `Math.max(CAN_STALE_MIN_S, 5 * p)` -> `5 * p` | a 1 kHz id is held to the 250 ms and 500 ms delivery-jitter floor, not a 1 s one [can_age_tick.test.mjs] |
| J06 | webui/can.js:135 | `Math.abs(dt - e.period)` -> `(dt - e.period)` | the rendered age cell takes the period's class as the tick ages it [can_age_tick.test.mjs] |
| J07 | webui/can.js:142 | `if (e.hex && f.hex && e.hex.length === f.hex.length) {` -> `if (e.hex && f.hex) {` | a dlc change lights nothing, and the new shape diffs from its own first frame [can_bytediff.test.mjs] |
| J08 | webui/can.js:434 | `if (!canPaused) e.moved = 0;` -> `return;` | a row revealed by the filter lights nothing that moved while it was hidden [can_bytediff.test.mjs] |
| J09 | webui/can.js:453 | `if (mask) canLit = true;` -> `if (mask) canLit = true;` | a paused table keeps the highlight it froze with, through frames, ticks and a rebuild [can_bytediff.test.mjs] |
| J10 | webui/can.js:519 | `for (const e of canRows.values()) e.moved = 0;` -> `;` | resuming does not light what moved while the table was frozen [can_bytediff.test.mjs] |
| J11 | webui/can.js:48 | `function canModel() { return canPaused && canFrozen ? can...` -> `function canModel() { return canRows; }` | a paused table keeps the highlight it froze with, through frames, ticks and a rebuild [can_bytediff.test.mjs] |
| J12 | webui/can.js:97 | `if (canPaused && canFrozenNow != null) return canFrozenNow;` -> `(removed)` | SURVIVED (node suite) |
| J13 | webui/can.js:626 | `canFrozen = new Map();` -> `canFrozenVersion = canRowsVersion;` | a cleared paused table stays folded while live frames arrive behind it [can_head.test.mjs] |
| J14 | webui/can.js:535 | `isLive: () => canRows.size > 0 && !canPaused,` -> `isLive: () => !canPaused,` | an empty table is not live, so it cannot hold pause-all in the paused state [can_freeze_surface.test.mjs] |
| J15 | webui/can.js:600 | `watermark: canPaused ? canFrozenId : null,` -> `watermark: null,` | SURVIVED (node suite) |
| J16 | webui/can.js:591 | `(canFrozenNow - Math.min(...seen))` -> `(canFrozenNow - Math.max(...seen))` | SURVIVED (node suite) |
| J17 | webui/can.js:284 | `\|\| canView.filter !== canFilter` -> `(removed)` | a paused table keeps the highlight it froze with, through frames, ticks and a rebuild [can_bytediff.test.mjs] |
| J18 | webui/can.js:311 | `.toUpperCase().replace(/^0X/, "");` -> `.toUpperCase();` | the filter input narrows the table by id substring, 0x prefix and case ignored [can_head.test.mjs] |
| J19 | webui/can.js:195 | `const bus = e.bus === 1 ? "" : String(e.bus);` -> `const bus = String(e.bus);` | the filter pattern is built in parseCanEvent's own grammar [can_logic.test.mjs] |
| J20 | webui/can.js:196 | `const id = fmtCanId(e).replace(/^0+(?=.)/, "");` -> `const id = fmtCanId(e);` | the filter pattern is built in parseCanEvent's own grammar [can_logic.test.mjs] |
| J21 | webui/can.js:170 | `if (!prevHex \|\| !hex \|\| prevHex.length !== hex.length) re...` -> `if (!prevHex \|\| !hex) return flags;` | a length change flags nothing: it is a different message, not a moved byte [can_bytediff.test.mjs] |
| J22 | webui/can.js:208 | `if (sec < 0.9995) return` -> `if (sec < 1) return` | SURVIVED (node suite) |
| J23 | webui/exportdlg.js:131 | `renderMode = !ok && range.mode === "shown" ? "session" : ...` -> `if (!ok && range.mode === "shown") range.mode =...` | a remembered shown range survives a panel that cannot offer it [exportdlg.test.mjs] |
| J24 | webui/exportdlg.js:115 | `values[by.field] !== by.equals` -> `!values[by.field]` | the ids field is disabled under snapshot and back under history; Source names both [can_head.test.mjs] |
| J25 | webui/exportdlg.js:226 | `if (btn.disabled) return;` -> `btn.disabled = true;` | Enter in an option exports once, and a second press while it runs does not [exportdlg.test.mjs] |
| J26 | webui/exportdlg.js:232 | `await sessionsReady;` -> `(removed)` | Export pressed before the session list lands still carries the remembered session [exportdlg.test.mjs] |
| J27 | webui/exportdlg.js:254 | `if (err) { $("expErr").textContent = err; return; }` -> `if (err) { $("expErr").textContent = err; }` | a daemon refusal stays in the dialog, with the range that produced it [exportdlg.test.mjs] |
| J28 | webui/exportdlg.js:162 | `if (!have && range.session != null) {` -> `if (false) {` | a remembered session that is gone says so before falling back [exportdlg.test.mjs] |
| J29 | webui/exportdlg.js:186 | `if (port !== "-") p.set("port", port);` -> `p.set("port", port);` | SURVIVED (node suite) |
| J30 | webui/exportdlg.js:191 | `p.set("decode", "1");   // changes=1 without` -> `// changes=1 without` | changes only always carries decode, whatever the decode box says [plots_export_button.test.mjs] |
| J31 | webui/exportdlg.js:192 | `if (v.deadband.trim()) p.set(` -> `p.set(` | EQUIVALENT: _parse_deadband skips an empty item |
| J32 | webui/exportdlg.js:241 | `if (inverted(effective)) {` -> `if (inverted(range)) {` | EQUIVALENT: inverted() reads clock mode only; renderMode differs from range.mode only for shown |
| J33 | webui/api.js:247 | `if (!body.truncated \|\| typeof last !== "number" \|\| last <...` -> `break;` | every board's definition is seeded, paged over the lookback and bounded by the window [api_plot_def_seed_ports.test.mjs] |
| J34 | webui/api.js:316 | `if (ports.size < 2) return channels;` -> `if (ports.size < 1) return channels;` | a fresh page seeds the charts and lanes from stored plot history [api_plot_seed.test.mjs] |
| J35 | webui/api.js:314 | `new Set([...listed, ...channels.map((c) => c.port)]` -> `new Set([...channels.map((c) => c.port)]` | a name two boards share seeds each board's history, the shadowed one included [api_plot_seed_ports.test.mjs] |
| J36 | webui/api.js:168 | `tickAnchors.clear();   // their ids` -> `// their ids` | a new capture token wipes and re-seeds, even with every id higher than those held [api_db_reset_misfire.test.mjs] |
| J37 | webui/state.js:330 | `if (!authToken && !tokenGaveUp && streamable(path)) {` -> `if (!authToken && streamable(path)) {` | after a cancelled token prompt a streaming export is fetched, not navigated (F3) [state_logic.test.mjs] |
| J38 | webui/state.js:312 | `return p.endsWith("/export") \|\| p === "/can/frames";` -> `return p.endsWith("/export");` | SURVIVED (node suite) |
| J39 | webui/timewindow.js:52 | `return z && z.mode === timeMode && z.max > z.min ? z : null;` -> `return z && z.mode === timeMode ? z : null;` | a zero-width or inverted zoom is rejected rather than dividing by zero [timewindow.test.mjs] |
| J40 | webui/timewindow.js:226 | `ts - prev.ts < ANCHOR_MIN_GAP_S` -> `ts - prev.ts < 1e9` | SURVIVED (node suite) |
| J41 | webui/timewindow.js:229 | `if (list.length > ANCHOR_CAP) list.splice(0, list.length ...` -> `(removed)` | SURVIVED (node suite) |
| J42 | webui/timewindow.js:240 | `return ((t % TICK_WRAP) + TICK_WRAP) % TICK_WRAP;` -> `return t;` | the estimate wraps at 2^32 as the tick does [terminal_tick_estimate.test.mjs] |
| J43 | webui/timewindow.js:82 | `const x0 = Math.max(0, win.toPx(xs[i]));` -> `const x0 = win.toPx(xs[i]);` | a level that began before the window still reaches the left edge [digital_clear_segments.test.mjs] |
| J44 | webui/terminal.js:44 | `if (row.chan === "gap") return "-";` -> `(removed)` | SURVIVED (node suite) |
| J45 | webui/terminal.js:108 | `if (pane.port === "all" && state.knownAliases.length > 1) {` -> `if (pane.port === "all") {` | the port tag column appears with a second port and goes when it leaves [terminal_empty_state.test.mjs] |
| J46 | webui/terminal.js:826 | `const restore = pane.regexSrc === pane.canFilter;` -> `const restore = true;` | unfilter leaves a pattern the user edited after the click [terminal_filter_pane.test.mjs] |
| J47 | webui/terminal.js:835 | `if (pane.canFilter === null \|\| pane.regexSrc !== pane.can...` -> `pane.canFilterPrev = pane.regexSrc;` | two clicks in a row still unfilter back to the user's pattern [terminal_filter_pane.test.mjs] |
| J48 | webui/terminal.js:573 | `for (const ch of pane.channels) p.append("chan", ch);` -> `p.set("chan", [...pane.channels].join(","));` | the pane's own three filters are what the download is filtered by [export_paused_window.test.mjs] |
| J49 | webui/terminal.js:607 | `if ((prev.length > 1) !== (aliases.length > 1)) panes.for...` -> `(removed)` | the port tag column appears with a second port and goes when it leaves [terminal_empty_state.test.mjs] |
| J50 | webui/terminal.js:219 | `if (row.id <= pane.clearId \|\| row.id > top) continue;` -> `if (row.id <= pane.clearId) continue;` | SURVIVED (node suite) |
| J51 | webui/pane.js:53 | `if (prev && prev.mode === mode && text.length <= prev.ch)...` -> `if (prev && text.length <= prev.ch) return prev;` | an unchanged width is the same object, and a new time base starts from its own stamps [terminal_ts_width.test.mjs] |
| J52 | webui/pane.js:65 | `if (cleared) return say(` -> `if (false) return say(` | the copy names each cause in one short line, and a non-empty pane has none [terminal_empty_state.test.mjs] |
| J53 | webui/layout.js:25 | `v.canCap >= CAN_CAP_MIN &&` -> `(removed)` | each field is validated on its own, so one bad value does not cost the rest [layout.test.mjs] |
| J54 | webui/layout.js:31 | `Math.min(w, wsWidth - TERMINAL_MIN)` -> `Math.min(w, wsWidth)` | a dragged width is saved on release and ends the expanded state [app_layout.test.mjs] |
| J55 | webui/plots.js:860 | `return chart.paused ? zoomFor(getZoom(), state.timeMode) ...` -> `return zoomFor(getZoom(), state.timeMode);` | SURVIVED (node suite) |
| J56 | webui/plots.js:1182 | `if (!paused) clearZoom();` -> `(removed)` | the zoom is dropped in another time mode and on resume [plots_zoom.test.mjs] |
| J57 | webui/plots.js:471 | `if (t && t !== defaultTitle(chart)) plotTitles[chart.key]...` -> `if (t) plotTitles[chart.key] = t;` | a chart title is renamed per browser; empty or whitespace restores the default [plots_chrome.test.mjs] |
| J58 | webui/plots.js:546 | `if (chart.unit.get(name) !== unit) { chart.unit.set(name,...` -> `(removed)` | a stream redeclared with different fields changes only that port's chart [plots_ports.test.mjs] |
| J59 | webui/plots.js:451 | `const multi = ports.size > 1;` -> `const multi = ports.size > 2;` | the port is named only once a second port has contributed [plots_ports.test.mjs] |
| J60 | webui/plots.js:1109 | `return t != null ? t : estimateTick(tickAnchors, row);` -> `return t;` | SURVIVED (node suite) |
| J61 | webui/plots.js:398 | `function seedPort(channel) { return typeof channel.port =...` -> `function seedPort(channel) { return channel.por...` | a duplicate (line_id, name) keeps the y array aligned with the shared x array [plots_seed_grammar.test.mjs] |
| J62 | webui/digital.js:296 | `return windowFor(digitalPaused ? getZoom() : null,` -> `return windowFor(getZoom(),` | SURVIVED (node suite) |
| J63 | webui/digital.js:382 | `const port = ports.length > 1 && ports.includes(v.port) ?...` -> `const port = ports[0];` | lanes from two ports export one port at a time, named by the Port choice [plots_ports.test.mjs] |
| J64 | webui/digital.js:354 | `digitalExportBtn.disabled = n === 0;` -> `digitalExportBtn.disabled = false;` | a digital panel with no lane shown disables its export button [plots_export_button.test.mjs] |
| J65 | webui/digital.js:241 | `if (l.name !== lane.name) continue;` -> `if (l !== lane) continue;` | SURVIVED (node suite) |
| J66 | webui/digital.js:717 | `leaveZoom();` -> `(removed)` | resuming the lanes alone drops the zoom, as resuming a chart does [plots_zoom_chip.test.mjs] |
| J67 | webui/digital.js:762 | `laneGroups.clear();` -> `(removed)` | the port is named only once a second port has contributed [plots_ports.test.mjs] |
| J68 | webui/settings.js:71 | `if (readOnly && s.sec !== "cfgSecToken") return false;` -> `(removed)` | daemon down after a good load: the stale config does not make it editable [settings_offline.test.mjs] |
| J69 | webui/statusbar.js:252 | `if (rx < prev) return null;` -> `(removed)` | the chip says whether the port is actually saying anything [statusbar_logic.test.mjs] |
| J70 | webui/statusbar.js:548 | `if (dev.includes("://")) return "board";` -> `(removed)` | deriveAlias matches cli._derive_alias [statusbar_session_dialog.test.mjs] |
| J71 | webui/chrome.js:219 | `t.tagName === "TEXTAREA" \|\| t.tagName === "BUTTON" \|\|` -> `t.tagName === "TEXTAREA" \|\|` | Enter in a textarea, on a button or a link, or mid-composition submits nothing [chrome_radios.test.mjs] |
| J72 | webui/chrome.js:233 | `const sole = shown.length === 1 && shown[0] === name;` -> `const sole = false;` | soloing the channel that is already alone shows them all again [plots_solo.test.mjs] |
| J73 | webui/cmdbar.js:80 | `const live = state.knownAliases.filter((a) => state.portC...` -> `const live = state.knownAliases;` | under auto a sole connected port among several is what the bar aims at [cmdbar_sole.test.mjs] |
| J74 | webui/cmdbar.js:243 | `if ($("markerBtn").disabled) return;` -> `(removed)` | with no port attached the input and the marker button are off, the marker text is not [cmdbar_sole.test.mjs] |
| J75 | webui/chrome.js:122 | `const hit = e && e.shiftKey ? [...windowGroups.values()] ...` -> `const hit = [group];` | a shift-click applies the span to every selector, and repaints them all [chrome.test.mjs] |
| J76 | webui/settings.js:469 | `.filter((tr) => tr._fields.aliasInput.value.trim())` -> `(removed)` | removing a port row is an unsaved edit; an untouched new row is not [settings_dirty.test.mjs] |
| J77 | webui/statusbar.js:203 | `if (btn.disabled) return;   // one start in flight` -> `// one start in flight` | a held Enter while the start is in flight posts once [statusbar_session_dialog.test.mjs] |
| J78 | webui/exportdlg.js:202 | `$("expTitle").textContent = TITLES[ctx.kind] \|\| "Export";` -> `$("expTitle").textContent = "Export";` | the heading names what the panel exports [exportdlg.test.mjs] |
| J79 | webui/terminal.js:844 | `clearTimeout(pane.regexTimer);   // the typed-input debounce` -> `// the typed-input debounce` | the pending debounce cannot re-apply the value the user typed before [terminal_filter_pane.test.mjs] |
