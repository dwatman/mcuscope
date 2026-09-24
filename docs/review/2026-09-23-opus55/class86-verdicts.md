# Helper contract sweep: verdicts

HEAD 5266f924a31ba5de465e085bd5c8722b692e5439, range 6e4f6f7..HEAD.
Python: 55 helpers, 170 sites. JS: 18 helpers, 58 sites. Total: 73 helpers, 228 sites.
Verdict totals: 1 instance, 22 other, 205 complies.

## Instances

- `_stdio.py:421  instance  console_entry (shared by mcu, mcuscoped and mcu-sim) skips the "started with stdout set to None" warning whenever stdout_was_closed(), on the grounds that "mcu reports it itself".`
  - Only `mcu` reports it (cli.py:3196). For `mcuscoped >&-` or `mcu-sim >&-` on POSIX, stdout now goes to devnull with no word on stderr, and exit 0.
  - Lost this way: mcuscoped's `web UI: <url>` line, and mcu-sim's `socket://127.0.0.1:<port>` line (the one line a caller reads to connect).
  - Probe: `console_entry(main, "mcu-sim")` with fd 1 closed prints nothing and returns 0 at HEAD. The 6e4f6f7 copy of _stdio.py prints the WARNING plus the interpreter report.
  - Scratch copies of the old files are in this directory (`old_*.py`).

## Python (host/mcuscope)

### _stdio.install_console_ctrl_handler (signature changed; now idempotent, first install wins)
- daemon.py:406  complies  _hold_console_close passes keep_ctrl_c_ignored=True. Only a console present at startup (`start /b`) reaches it as the first install. A consoleless start has already installed via _ensure_console, and the early return is harmless there.
- _stdio.py:130  complies  _ensure_console runs in repair_std_streams at startup, so it is always the first install and clears the inherited ignore flag as before.

### _stdio.stdout_was_closed (new)
- cli.py:3196  complies  _dispatch marks output failed, so mcu exits non-zero with "cannot write output".
- _stdio.py:192  other  a comment naming the function, not a call.
- _stdio.py:421  instance  see Instances: the suppression covers mcuscoped and mcu-sim, which never report.

### cli._get_rows (new)
- cli.py:639  complies  only lengthens the read timeout when `match` is set; the body and paging are unchanged.
- cli.py:672  complies  same, for /lines and /can/frames.
- cli.py:711  complies  same.

### cli._iter_pages_asc (signature line flagged; contract unchanged, ceiling moved into _pin_ceiling)
- cli.py:810  complies  _make_decoder has no until_ts, so no ceiling. Its `^!pd ` match now gets the match timeout.
- cli.py:847  complies  same, with the id_to bound kept.
- cli.py:1952  complies  log_export paged path; the until_ts ceiling is pinned exactly as before.

### cli._pin_ceiling (new, extracted from _iter_pages_asc)
- cli.py:635  complies  _fetch_after walks asc from since_id. The pinned id_to is min(existing, ceiling), and until_ts still rides on each page.
- cli.py:709  complies  the original caller; identical logic.

### cli._port_column (new)
- cli.py:965  complies  `lines` judges on its own rows. Port "" is excluded as not a board, and rows from one board plus daemon rows get no column by design.
- cli.py:993  complies  `tail -f` stream judgement via _stream_port_column (attached plus stored).
- cli.py:1018  complies  _tail_snapshot with show_port None judges on the snapshot rows.
- cli.py:1388  complies  `wait` prints one matched row; the stream rule, and False with -p or --json.

### cli._stop_daemon (signature changed: quiet -> restarting)
- cli.py:2767  complies  daemon_restart passes restarting=True: no report, then waits for a ppid-only parent to exit.
- cli.py:2774  complies  daemon_stop takes the default and reports.

### cli._stream_port_column (new)
- cli.py:877  complies  _port_column's stream branch takes [0].
- cli.py:1925  complies  log_export uses both halves: csv and --json give (False, True), which routes to the daemon export.

### cli._tail_snapshot (signature changed: show_port)
- cli.py:991  complies  non-follow tail; None judges the column on the rows.
- cli.py:1001  complies  follow backfill passes the follow's own show_port, so snapshot and stream agree.

### cli.render (new, nested in log_export)
- server.py:1909  other  the local `render` unpacked from _LINES_EXPORT (_jsonl_lines or _csv_lines), not cli's nested render.
- cli.py:1967  complies  -o file path; JSON or fmt_line with the stream-judged show_port.
- cli.py:1990  complies  stdout path, same.

### cli_client._daemon_errors (signature changed: timeout_code removed; transport timeout now exit 1)
- cli_client.py:134  complies  request. Long polls size their client timeout past the daemon's window (/wait and /cmd +5 s, /assert budget +30 s), so a transport timeout is a stuck daemon, as the new exit 1 says.
- cli_client.py:247  complies  download (session export). Exit 1 for a stalled daemon; no caller relied on 2.
- cli_client.py:283  complies  stream_text (plot export), same.

### cli_client.Client.request (signature changed: timeout_code removed)
- cli_client.py:136  other  httpx.Client.request inside Client.request.
- cli_client.py:160  other  httpx.Client.request inside probe_status.
- cli_client.py:228  complies  get passes **kw, including _get_rows' timeout. No caller in the package still passes timeout_code (grep).
- cli_client.py:231  complies  post, same.
- cli_client.py:234  complies  put, same.
- cli_client.py:237  complies  delete, same.

### cli_daemonctl._serving_pids (new)
- cli_daemonctl.py:339  complies  a recorded pid is used only when /status corroborates it (pid, or ppid on Windows).
- cli_daemonctl.py:350  complies  re-check before signalling a ppid-only parent.
- cli.py:2712  complies  _start_daemon: Windows proc.pid is the shim, which the daemon reports as ppid. Empty for an old daemon, so no false refusal.

### cli_daemonctl._status_pid (new)
- cli_daemonctl.py:313  complies  positive non-bool int or None; None is discarded by the caller.
- cli_daemonctl.py:315  complies  same, for ppid (Windows only).
- cli_daemonctl.py:340  complies  parent_only compares against the daemon's own pid.

### cli_daemonctl._stderr_lines (new)
- cli_daemonctl.py:122  complies  _index_build matches whole lines from `start`; [] when unreadable means "no build seen".
- cli_daemonctl.py:133  complies  _stderr_tail gives "" on [].

### cli_daemonctl._stderr_tail (signature changed: start)
- cli_daemonctl.py:276  complies  passes start=err_start, so only this start's output is quoted.
- cli_daemonctl.py:292  complies  same.

### cli_daemonctl._stop_running_daemon (signature changed: body, restarting)
- cli.py:2786  complies  the no-record branch passes body and restarting. It never signals, by design (stop signals no unproven pid).
- cli.py:2813  complies  the record branch passes pid_path and the recorded pid; it is removed only while it still names that pid.

### cli_output._isatty (new)
- cli_output.py:71  complies  err_write escapes controls only on a tty stderr.
- cli_output.py:226  complies  _GuardedStdout samples tty-ness once at construction.
- cli_output.py:539  complies  _stdin_is_interactive: a None stdin gives AttributeError, caught, False, as the old inline try did.

### cli_output.cmd_err_text (new)
- cli_output.py:556  complies  emit_cmd_result: identical text to the old inline form.
- cli.py:1393  complies  wait's refused --send; uses .get, so a partial dict cannot raise.
- cli.py:1504  complies  assert's failed send, status err only.

### cli_output.note_truncated (signature changed: default fallback, beyond)
- cli.py:969  complies  `lines` newest-first: the new default ('mcu log export') replaces the wrong "use --since-id".
- cli.py:972  complies  --since-id path passes beyond="newer" and its own continuation hint.
- cli.py:1038  complies  _tail_snapshot relies on the new default, which is what it used to pass explicitly; skipped for -n 0.
- cli.py:1993  complies  log_export --limit passes its own fallback.
- cli.py:2151  complies  can dump passes opt and fallback; the "rows" wording is generic.

### cli_output.visible (new)
- cli_output.py:72  complies  stderr on a tty.
- cli_output.py:220  other  docstring text in _GuardedStdout.
- cli_output.py:231  complies  stdout on a tty and not --json; returns len(s) so callers see the full count.

### config.check_host (new)
- config.py:407  complies  the loader warns and falls back to the default host on ValueError.
- daemon.py:106  complies  --host raises ConfigError with the reason.
- server.py:1294  complies  PUT /config/server returns 400 and saves the stripped host.

### config.control_char_field (new)
- config.py:488  complies  loader skips the port with a warning.
- server.py:1157  complies  attach refuses 400.
- server.py:1432  complies  PUT /config/ports refuses 400. The check runs on the unstripped values, a superset of the stored ones.

### render.fmt_line (signature changed: show_port; raw now escapes line boundaries)
- server.py:3416  complies  _text_lines export; the escaping keeps one row per line, which is what the export wants.
- cli.py:967  complies  `lines` text.
- cli.py:1034  complies  tail snapshot.
- cli.py:1237  complies  follow loop; a row missing `port` under show_port raises KeyError, which the drop counter already catches.
- cli.py:1388  complies  wait's matched row (daemon rows carry port).
- cli.py:1956  complies  log_export's nested render.

### render.one_line (new)
- render.py:34  complies  fmt_line.
- cli.py:1509  complies  assert's ok-expect line.
- cli.py:1515  complies  assert's failed-forbid line.

### serial_link.PortManager._detach_locked (signature changed: cause)
- serial_link.py:1417  complies  attach replacing an alias passes "re-attach"; counters are carried after stop().
- serial_link.py:1432  complies  detach takes the default "detach".

### serial_link.SerialPort._take_partial (new)
- serial_link.py:449  complies  stop counts it in rx_dropped and the sys row. An overlong discard is counted when it starts, so resetting _rx_discarding loses no count.
- serial_link.py:723  complies  _on_disconnect counts it; skipped once stopping, so stop() takes it instead (no double count).

### server._client_gone (new)
- server.py:2898  complies  a waiter whose client left returns 499; both callers pass a JSONResponse through.
- server.py:2922  complies  a build outrun by the disconnect; the job is abandoned first.

### server._export_dir (new)
- server.py:2760  complies  None (in-memory capture) returns before listing.
- server.py:2808  complies  None means the system temp dir, documented for in-memory captures only.

### server._is_ui_path (new, extracted from _SameOriginGuard)
- server.py:658  complies  _navigates_to_ui, the new second user; exact paths.
- server.py:816  complies  identical predicate to the pre-round inline test.

### server._live_scan (new)
- server.py:2667  complies  _do_wait handles _UNJUDGED (counted in dropped, then timeout). The scan's budget error still propagates to the route's handler.
- server.py:3170  complies  _do_assert forbid scan handles _UNJUDGED.
- server.py:3185  complies  _do_assert expect scan handles _UNJUDGED. It over-counts the already-judged forbid rows as dropped, which is conservative.

### server._pool (new)
- server.py:2389  complies  "live-match" pool, separate from export.
- server.py:2910  complies  "export" pool.

### server._ExportJob._remove_all (new)
- server.py:2828  complies  a failed build removes its files and re-raises.
- server.py:2833  complies  finished after abandon: files removed and nobody serves them.
- server.py:2840  complies  abandon after finish removes them; before finish, the build's own failure path does.

### server._remove_export_file (new)
- server.py:534  complies  lifespan clears whatever is still live.
- server.py:2818  complies  _ExportJob.remove.
- server.py:2948  complies  _TempFileResponse finaliser. Double removal is suppressed.

### server._resolve_port (new; several attached with one connected is now ambiguous, where PortManager.resolve picked the connected one)
- server.py:1799  complies  /send; the refusal names the aliases, which is the documented SPEC 4 intent.
- server.py:1811  complies  /break, same.
- server.py:1823  complies  /cmd, same.
- server.py:2170  other  a comment in /marker naming it; marker uses _unknown_port.
- server.py:2599  complies  _do_wait resolves only with port or send; a live wait needs an attached port.
- server.py:3120  complies  _do_assert live branch, same.

### server._run_export (new)
- server.py:1581  complies  export_session: a JSONResponse (503 or 499) is passed through, and build errors are caught as Exception.
- server.py:1718  complies  _build_bundle, same.

### server._several_ports (new)
- server.py:1632  complies  bundle lines.txt: the "" rows are not a board. Judged over the whole capture, not the session span, so the column may appear when unneeded, never missing.
- server.py:1907  complies  lines_export only without port=.

### server._text_lines (signature changed: show_port)
- server.py:1631  complies  bundle.
- server.py:1907  complies  text export.

### server._unknown_port (new)
- server.py:1853  complies  /lines.
- server.py:1889  complies  /lines/export.
- server.py:1934  complies  /can/frames.
- server.py:1976  complies  /plot/channels (plot rows live on lines, so has_port_rows covers them).
- server.py:2034  complies  /plot/series.
- server.py:2071  complies  /plot/export.
- server.py:2177  complies  /marker maps "" to None first.
- server.py:3080  complies  retrospective /assert.

### server._ExportJob.abandon (new)
- server.py:2917  complies  handler cancelled: a queued build is cancelled, and a running one stops at its progress handler or checkpoint.
- server.py:2921  complies  client gone first, same.

### server.can_frames (signature changed; route handler, no Python caller)
- store.py:74  other  SQL `CREATE TABLE ... can_frames(`.
- store.py:84  other  SQL `CREATE INDEX ... ON can_frames(`.
- store.py:1089  other  SQL `INSERT INTO can_frames(`.
- store.py:1168  other  SQL `INSERT INTO can_frames(`.
- store.py:1607  other  SQL `INSERT INTO can_frames(`.

### server._ExportJob.checkpoint (new)
- server.py:1695  complies  bundle member loop stops between chunks once abandoned.
- server.py:2841  other  comment text in abandon().

### server._ExportJob.mkstemp (new)
- server.py:1570  complies  session export; the file is registered in live and in the job.
- server.py:1679  complies  bundle zip.
- server.py:1680  complies  bundle db copy, removed in the build's finally.
- server.py:2806  other  tempfile.mkstemp.

### server._ExportJob.remove (new)
- sim.py:1172  other  os.remove.
- sim.py:1181  other  os.remove.
- cli_output.py:179  other  os.remove.
- server.py:1714  complies  bundle's db copy; a later _remove_all repeats it harmlessly.
- server.py:2822  complies  _remove_all.
- cli_daemonctl.py:227  other  os.remove.
- cli_daemonctl.py:256  other  os.remove.
- cli.py:2811  other  os.remove.
- pidfile.py:215  other  os.remove.
- pidfile.py:236  other  os.remove.
- pidfile.py:249  other  os.remove.

### server._do_assert.verdict (signature changed: cmd_result)
- server.py:3110  complies  retrospective has no send (refused earlier), so cmd_result stays None.
- server.py:3145  complies  failed send is judged "fail" with the send's reason.
- server.py:3199  complies  cmd_result is None (raw send) or ok here; non-ok returned earlier.

### store._budget_error (new)
- store.py:2116  complies  raised only when rx.timed_out; window_spent is set by _make_regexp.
- store.py:2144  complies  inline path, same guard.

### store._delete_chunks (new, extracted from delete_range)
- store.py:2874  complies  delete_range: same lock, loop, yield and reclaim as the pre-round body.
- store.py:2889  complies  delete_before_ts: floor max_id+1 means ids <= max_id, as _delete_expired_chunk's floor_id reads.

### store._delete_lines (signature changed: full SQL -> id subselect run twice)
- store.py:2850  complies  _delete_oldest_chunk passes a deterministic `ORDER BY id LIMIT` subselect.
- store.py:2853  complies  _delete_range_chunk, same.
- store.py:3059  complies  _delete_expired_chunk `ORDER BY ts LIMIT`. Both evaluations run on the loop connection with no commit or await between, so they select the same ids.

### store._lines_index (new)
- store.py:1949  complies  query_lines; the hint applies only with port and chans, and _window_terms then emits both terms. The index is in SCHEMA, run at every open.
- store.py:1994  complies  count_lines, same.

### store._open_session_locked (new)
- store.py:1468  complies  opens abutting the closed session.
- store.py:1470  complies  opens at _next_id after the drain.
- store.py:1505  complies  stop_session's reopen_auto.

### store._sweep_size_reported (new)
- store.py:728  complies  startup sweep now also writes the sys row.
- store.py:3120  complies  tick sweep, same row as before.

### store._window_id_floor (signature changed: strict removed; slack-based bound)
- store.py:1832  complies  floor_ts window keeps its exact ts term; the lower bound drops nothing while ts inversion stays under WINDOW_TS_SLACK_S (announced past that).
- store.py:1838  complies  since_ts, same bound with the strict ts term.

### store._window_terms (signature changed: unindexed_port removed)
- store.py:1936  complies  query_lines: the old unindexed_port user now takes _lines_index.
- store.py:1988  complies  count_lines, same.
- store.py:2183  complies  query_can_frames never passed unindexed_port. Its comment at store.py:2195 still says "as `+port` does in _window_terms", which no longer exists (stale comment).
- store.py:2490  complies  query_plot_series never passed it.
- store.py:2576  complies  _export_where never passed it.

### store.export_session_db (signature changed: on_open)
- server.py:1571  complies  session export passes job.on_open; id_to None resolves to MAX(id) as before.
- server.py:1686  complies  bundle passes the frozen `hi` and on_open.

### store.stored_ports (new; excludes port "" unless include_daemon)
- store.py:2388  complies  _scan_plot_rows passes include_daemon=True (the fix for the triggering instance).
- server.py:1145  complies  GET /ports "stored" lists boards; "" is not one (SPEC 3.5).
- server.py:3422  complies  _several_ports counts boards; matches the CLI's own `- {""}`.

## JS (host/mcuscope/webui)

### plots.js adhocOnce (new)
- plots.js:56  complies  hooks.adhocTick reads .tick only.
- plots.js:279  complies  plotIngest. The cached sample is never mutated (routePoints only reads points).

### plots.js breakCharts (new)
- api.js:256  complies  markShed. breakChart skips charts with no sample.
- api.js:588  complies  reconnect backfill divider, same.

### digital.js breakLanes (new)
- api.js:257  complies  markShed. The null vertex ends the held level, and the next sample restarts, since digitalIngest compares against the vs tail.
- api.js:588  complies  backfill divider, same.

### can.js canOnce (new)
- can.js:92  complies  hooks.canTick reads .tick.
- can.js:138  complies  canIngest reads the frame and never mutates it.

### can.js canShownWindow (signature changed: port)
- can.js:654  complies  per-port window for the history export.
- can.js:661  complies  port null keeps the old all-rows meaning (the fallback when no port row is shown).

### can.js canVisible (signature changed: exported; now also requires sidebar width)
- app.js:199  complies  visibilitychange repaint skipped while the sidebar is hidden; the 1 s timer repaints on reopen.
- can.js:723  complies  timer skips a hidden sidebar; canDirty survives until shown.

### plots.js currentData (signature changed: width; decimates and scales, sets chart.drawScale)
- plots.js:1060  complies  buildUplot passes w; drawScale matches the data given to uPlot.
- plots.js:1177  complies  redrawPlots passes w. These are the only webui callers; readers of u.data values go through drawnValue.

### plots.js decodeOnce (new)
- plots.js:52  complies  hooks.plotSampleTick reads .tick. The cache key is (raw, def), so a redefinition (new def object) re-decodes.
- plots.js:277  complies  plotIngest, same key. The cached sample is read and never mutated.

### plots.js drawnValue (new)
- plots.js:827  complies  chip readouts from u.data.
- plots.js:1039  complies  solo y-axis labels from scaled splits.

### api.js feedPanes (new)
- api.js:115  complies  routeLiveRow.
- api.js:258  complies  markShed's gap row. Skipped while highRate, but pushRow put it in the buffer, and the high-rate exit rebuilds panes from there.

### timewindow.js fitAxisTicks (new)
- digital.js:537  complies  ruler; the window carries xmin and xmax.
- plots.js:891  complies  x-axis splits.

### pane.js gapRow (signature changed: why, lo)
- api.js:254  complies  shed notice: hole [row.id-n, row.id-1] matches lo.
- api.js:588  complies  backfill divider below rows[0].
- pane.js:163  complies  narrowGap; every gap row now has lo.
- pane.js:206  complies  planHistoryPage divider, lo = floor+1.

### api.js isShedNotice (new)
- api.js:737  complies  stageRow merge of adjacent notices (two calls on the line; undefined prev is false).
- api.js:742  complies  trim never drops a notice.
- api.js:837  complies  drainStaging splits segments at notices.

### timewindow.js mergeNarrow (new)
- digital.js:648  complies  drawBits handles busy segments.
- digital.js:677  complies  drawEnum handles busy segments.

### plots.js plotsShown (new)
- plots.js:1460  complies  redraw timer.
- plots.js:1465  complies  visibilitychange redraw.

### freeze.js registerSurface (signature changed: watermark dropped)
- can.js:575  complies  isLive and setPaused only; the watermark key was removed in the same round.
- digital.js:890  complies  same.
- plots.js:1345  complies  same.
- terminal.js:332  complies  same. Nothing calls the removed watermarks() or minWatermark() (grep).

### state.js splitTokens (new)
- can.js:59  complies  parseCanEvent, positional tokens with runs of spaces collapsed, as protocol.split_tokens.
- plots.js:50  complies  sid lookup.
- plots.js:102  complies  parsePlotAdhoc.
- plots.js:191  complies  parsePlotDef.
- plots.js:231  complies  decodePlotSample.
- plots.js:275  complies  plotIngest sid.
- state.js:229  complies  computeTick tag.
- state.js:250  complies  lineTick miss-cache test.
- state.js:263  complies  markerTick; a tab stays inside the token, as the comment requires.

### state.js userText (new)
- exportdlg.js:171  complies  option label only; the value is the session id.
- settings.js:363  complies  textContent.
- settings.js:364  complies  title.
- settings.js:432  complies  confirm text.
- settings.js:480  complies  option label; matching uses opt.value (raw device).
- statusbar.js:177  complies  button text.
- statusbar.js:179  complies  title.
- statusbar.js:184  complies  title.
- statusbar.js:350  complies  tooltip.
- statusbar.js:351  complies  tooltip.
- statusbar.js:382  complies  chip meta textContent.
- statusbar.js:644  complies  option label; the value stays the raw device.
