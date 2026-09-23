# Health: code and test-suite accretion (2026-09-23)

HEAD 6e4f6f7. Scratch: `~/tt-data/mcuscope-2026-09-23/health/`. Detailed helper reports sit beside it:
- `src-py.md`: Python source.
- `js.md`: web UI source, 82 JS mutants, and the run-alone check of 888 JS tests.
- `mutation.md`: 83 Python mutants, with the survivor probe tests in `mut/probes/test_probe_survivors.py`.

Counts: HIGH 0, MEDIUM 14, LOW 16.

## HEALTH-1 MEDIUM CONFIRMED: the row-by-row fallback reuses line ids that a session still spans

- Where: `host/mcuscope/store.py:916` (`_insert_individually`).
- Failure: when a batch insert raises, each row goes through `_insert`, and SQLite assigns ids as `max(rowid)+1`. This ignores `_max_session_ref_id()`, the floor `start()` applies at `store.py:564`.
  - After `purge --all`, the next row that goes through the fallback gets id 1, inside an old session's span. `session show <old>` then returns the new traffic.
  - This is the id-reuse incident `start()` was fixed for, reached by a second path.
  - The resync after the fallback protects only later batches.
- Repro: `mut/probes/id_reuse_fallback.py`, re-run against the repo package. Session run-alpha spans ids 1..7. `delete_range`, then one good row and one CHECK-violating row. Output: `beta row {'id': 1, ...}`.
- Fix: give the fallback explicit ids from `_next_id`, as `_insert_batch` does. Pin it with the probe.
- Class: 38 (a re-run missing the discipline the first run has).

## HEALTH-2 MEDIUM CONFIRMED: `--decode` drops every line whose text starts with `!pd`

- Where: `cli_output.py:372` and `cli.py:1085`. Both use `raw.startswith("!pd")`, where ingest dispatches on the whole first token (`serial_link.py:887-898`).
- Failure: a firmware event `!pdo 5V 3A` disappears from `mcu lines/tail [-f]/log export --decode` with exit 0. The capture holds the line. A malformed `!pd` is dropped too.
  - This is arguably HIGH (a silent wrong answer), but only for firmware that emits such tokens.
- Repro: `src/p1_linedecoder_prefix.py`. On the sim daemon, `mcu mark '!pdo 5V 3A'` is shown by `mcu lines`, and `mcu lines --decode` prints nothing.
- Fix: dispatch on `raw.split()[0]`, and return the raw line when `learn()` rejects it.
- Class: 22, 19.

## HEALTH-3 MEDIUM CONFIRMED: `mcu -p X plot channels` ignores `-p`

- Where: `cli.py:2220` does not forward `port`.
- Failure: with two boards declaring the same names, the rows merge across boards. The counts are summed and each row is labelled with the last writer.
- Repro: see `src-py.md` SRC-2. `-p sim` gave count 984; `GET /plot/channels?port=sim` gave 656.
- Fix: send `params={"port": s.port}`. Update AI_GUIDE and SPEC 4.
- Class: 31, 57.

## HEALTH-4 MEDIUM CONFIRMED: `server.host = ""` in the config file binds every interface

- Where: `config.py:383` does not check the value. `daemon.py:103-106` (`--host ""`) and `server.py:1136-1138` (PUT) both refuse it.
- Failure: `ss -ltnp` showed `0.0.0.0` and `[::]`, the UI URL was printed as `http://:18751/ui/`, and GET /config returned a host the settings dialog cannot save.
- Repro: `src/run2/daemon.toml`.
- Fix: one host check shared by the three paths. In the loader, warn and keep 127.0.0.1.
- Class: 19.

## HEALTH-5 MEDIUM CONFIRMED: the config loader accepts a device that PUT /config/ports refuses, and every later ports save then fails

- Where: `config.py:433-481` never calls `validate_device`. `server.py:1270-1273` refuses the whole PUT body.
- Failure: `device = "spy:///dev/ttyUSB0"` loads with no warning. PUT /config/ports then returns 400 for the unchanged list and for any edit, so the settings dialog cannot save a port.
- Repro: `src/p3_config_roundtrip.py`.
- Fix: validate in the loader and skip the entry with a warning, as `config.py:446-451` does for baud.
- Class: 19.

## HEALTH-6 MEDIUM CONFIRMED: `mcu sysrq é` sends the break, then refuses the character

- Where: `cli.py:521`. `isprintable()` is true for `é`. The `/break` POST precedes `/send`, which refuses non-ASCII.
- Failure: exit 1 "line must be 7-bit ASCII", but `port sim: break 250 ms` is already in the capture, which arms SysRq on a Linux console.
- Fix: `char.isascii() and char.isprintable()` before any request.
- Class: 22.

## HEALTH-7 MEDIUM CONFIRMED: a pane export filters with a different regex dialect from the pane

- Where: `webui/terminal.js:550` filters with a JS `RegExp`. `terminal.js:591` sends the same source to `/lines/export`, where the `regex` module runs it and nothing re-filters the result.
- Failure: with `\Aboot` the pane shows `Aboot` and the export holds `boot ok`. With `ERR\Z` the pane is empty and the export holds `x ERR`.
- Repro: `js/export_dialect.sh`, `js/rx_dialect.mjs`.
- Fix: refuse the constructs the two engines read differently (`\A \Z \z [[: {,n}`) in `applyRegex`. Pin with a two-engine fixture.
- Class: 19.

## HEALTH-8 MEDIUM CONFIRMED: the CAN frame-history export mixes boards

- Where: `webui/can.js:640-644` never sets `port`, and the CSV has no port column.
- Failure: two boards' frames for one id interleave in the file with nothing marking which board sent which. `digital.js:476` already offers a Port choice for exactly this.
- Repro: `js/can_ports.mjs`.
- Fix: a Port select as in exportDigital, and always set `port`.
- Class: 57.

## HEALTH-9 MEDIUM CONFIRMED: raw mode trims the typed line and cannot send an empty one

- Where: `webui/cmdbar.js:179` does `input.value.trim()` then `if (!text) return` for both modes, while the prompt title (`cmdbar.js:133`) says "the line is written as typed".
- Failure: `    print(x)` loses its REPL indentation, and Enter on an empty line (to wake a prompt) sends nothing. `mcu send` does not trim.
- Repro: `js/raw_trim.mjs`.
- Fix: trim only in cmd mode.

## HEALTH-10 MEDIUM CONFIRMED: a CLI test passes when the command crashes, and 22 others can

- Where: `tests/test_cli.py:996` (`test_daemon_stop_corrupt_pidfile_exit1`).
- Failure: with the refusal at `cli.py:2580` deleted, `mcu daemon stop` raises a TypeError. The crash handler exits 1, and the rich traceback's source excerpt contains the deleted `die()` literal, so `"corrupt"` and `"left it in place"` are both found in stderr. The test passes.
- An AST scan found 22 subprocess tests that assert a stderr substring and never assert that no traceback appeared.
- Repro: mutant C09 in `mutation.md`; the traceback is in `mut/probe_home`.
- Fix: in conftest, fail any test whose child data dir holds an `mcu-crash.log`, or assert `"Traceback" not in stderr` in the `_run` helpers.
- Class: 78.

## HEALTH-11 MEDIUM CONFIRMED: the daemon's token and exposure handling has no tests

- Where: `daemon.py:105` (`--host ""` refusal), `:121` (`MCUSCOPED_TOKEN` applied), `:137` and `:147` (the tokenless and short-token exposure warnings).
- Failure: mutants E01-E04 each survive every test file that touches the daemon. With E04, a daemon started with the env token runs without a token, and nothing fails.
- Repro: `mutation.md` E01-E04. The ready tests `test_e01..e04` in `mut/probes/test_probe_survivors.py` each fail under their mutant.
- Fix: move those four tests into the suite.

## HEALTH-12 MEDIUM CONFIRMED: the WS backpressure test never engages drop-oldest, and costs 24 s

- Where: `tests/test_e2e.py:481` (`test_ws_backpressure_drop_oldest`), the second slowest test in the suite.
- Failure: the comment says 2500 sends exceed the 2000-deep queue "so drop-oldest must engage". Replaying the scenario shows it never does: the websockets client's kernel buffers and the server-side pump drain the subscriber queue, and `store.ws_dropped` stays 0. The test is really "2500 /send calls succeed".
  - The drop path has a real test, `test_hardening.py:1895`, which calls `_broadcast` directly.
- Repro: `probe/ws_bp.py 2500` prints `ws_dropped 0`.
- Fix: delete the test, or park the pump and assert `ws_dropped > 0` together with the id arithmetic, which is the positive control.
- Class: 78.

## HEALTH-13 MEDIUM CONFIRMED: one test takes 25-79 s against a 90 s per-test timeout, and the comment says the slowest is 14 s

- Where: `tests/test_plot_export_decode.py:299` (`test_a_selection_past_the_old_row_cap_streams`) inserts 1.2 M plot points. `host/pyproject.toml` says "the slowest test is ~14 s, so 90 s only ever fires on something genuinely wedged".
- Failure: measured 78.6 s during the per-file sweep (load ~15 from parallel agents), 41.6 s at load ~9 and 24.8 s at load ~4. The insert alone takes 11 s. On a loaded CI runner this is a timeout flake, and it is about a third of the suite's serial time.
- Fix: prove "no cap" structurally. For example, shrink the export page size under monkeypatch and assert that an N-row selection streams N rows across several pages. Otherwise mark the test slow and correct the pyproject comment.

## HEALTH-14 MEDIUM CONFIRMED: `test_can_csv_only_when_the_session_carried_frames` fails under load

- Where: `tests/test_session_bundle.py:199`, via `recorded(..., with_can=False)` at `:90`.
- Failure: the sim's 10 Hz CAN heartbeat (`sim.py:422`, filter "all" by default) is always on, so the "quiet" session gets `!can` frames whenever more than 100 ms pass between session start and stop. It failed in the sweep (`assert (3 == 2)`) and passed five times alone.
- Repro: `probe/bundle_hb.py` stretches the gap to 250 ms. The quiet session's bundle then holds `can.csv` with 0x100 heartbeat rows.
- Fix: silence the sim's CAN in that fixture (`can filter` none, or a sim arg), or assert on the fed frame's id rather than the row count.
- Class: 21, 63.

## HEALTH-15 LOW CONFIRMED: Python guards that no test pins

- Each of these mutants survives every related file. `mutation.md` has the probe tests, each failing under its mutant.
  - C04 `cli.py:1063`: a non-UTF-8 binary WS frame is never driven.
  - C06 `cli.py:1797`: the paged `log export -o` partial-file discard.
  - D01 `cli_daemonctl.py:280`: the "still answering after stop" check.
  - S03 `server.py:485`: a raising lifespan shutdown step.
  - L03 `serial_link.py:789`: the oversized notice re-arming.
  - L06 `serial_link.py:1192`: the `_pending.pop` when the tx row fails.
  - B07 `store.py:2141`: the under-lock re-check.
  - C11 `cli.py:2470` and B08 `cli.py:2046`: tests suggested, not probed.
- Three tests do not depend on the code they name: `test_typed_f4_refuses_non_finite` (see HEALTH-16), `test_a_disconnect_during_a_command_leaves_no_unretrieved_future` (`_fail_pending` already empties `_pending`), and `test_a_summary_read_during_a_rebuild_waits_for_it` (asserts the value only).

## HEALTH-16 LOW CONFIRMED: the f4 non-finite gate is dead code on both sides

- Where: `protocol.py:865` and `webui/plots.js:208`.
- Deleting either survives every test, including the f4 infinity and NaN fixture cases. The reason: f4 can only be an analog channel (`_ENUM_TYPES`/`_BITS_TYPES` exclude it), and the post-scale check (`protocol.py:910`, `plots.js:240`) rejects the same values.
- Fix: delete both gates, or keep them and say which check is load-bearing. The `plots.js:310-314` comment is wrong for the same reason.

## HEALTH-17 LOW CONFIRMED: web UI guards that no test pins

- `js.md` JS-8 covers M46, M29, M71, M62, M32, M43 and M53.
  - M46 `cmdbar.js:57`: when the picked port detaches, the next command silently goes to the remaining board.
  - M29 `api.js:190`: a paused pane shows the new capture after a reset.
  - M71 `plots.js:1360`: after a reset, samples below the old seed ids are not charted.
  - M62 `plots.js:249`: a raw-sent `!p` (chan cmd) is charted.
- Probes are in `js/probe_m*.mjs`.

## HEALTH-18 LOW CONFIRMED: the freeze registry's `watermark()` feeds only tests

- Where: `webui/freeze.js:21-31,70`. Every surface must register a watermark, but each export computes its own bound (`can.js:632`, `digital.js:485`, `plots.js:1270`, `terminal.js:577`).
- Mutants M81/M82 (registered watermark returns null) survive. M79/M80 are killed only through `watermarks()`.
- Fix: have `openExportDialog` read the registry, or delete the hook and point the two tests at the export URL.

## HEALTH-19 LOW CONFIRMED: event lines split on different whitespace in the daemon and the browser

- Where: Python `str.split()` treats `\x1c`-`\x1f` as separators, and JS `/\s+/` (`plots.js:82,171,217`, `can.js:60`, `state.js:228`) does not. Those bytes survive the rx decode at `serial_link.py:778`.
- Failure: the daemon stores `!p\x1f100 a=1` and `!can\x1f100 ...` as points and frames, while the live UI drops them until a reload.
- Related JS-vs-Python drifts, all confirmed in `js.md`:
  - JS-5: the `lineTick` hand mirror at `state.js:228-233` takes a rejected `!can 4000000000 zz ...` as the sticky tick anchor.
  - JS-6: marker tick parsing after a tab.
  - JS-7: a duplicated enum value is labelled first-wins in the browser and last-wins in the CLI and export.
- Fix: use one tokenizer rule on both sides and add these cases to `plot_grammar_cases.json`.
- Class: 19.

## HEALTH-20 LOW CONFIRMED: smaller Python drifts

- SRC-6: PUT /config/plotjuggler saves an enabled multicast dest that the runtime refuses. After a restart the stream is off, and nothing reports it in `config_warnings`.
- SRC-7 (latent): `store.py:1846` caches a REGEXP closure on read connections that never re-arms. Probe `src/p6_read_conn_regexp.py`.
- SRC-8: `/cmd` strips the trailing CR/LF that `/send` refuses. The comment at `serial_link.py:1106` claims both refuse it.
- Details: `src-py.md`.

## HEALTH-21 LOW CONFIRMED: `test_ai_guide_names_every_flag` passes `-c`, `-t`, `-y` as substrings

- Where: `tests/test_cli_contract.py:221` checks `opt in cli.AI_GUIDE`.
- Failure: `-c`/`-t` (daemon start/restart) and `-y` (purge, session delete) are never written as tokens in the guide. They pass because they appear inside other words. The contract CLAUDE.md relies on is therefore unchecked for short flags.
- Repro: `probe/guide_substr.py` prints `['-c', '-t', '-y']`.
- Fix: match with `(?<![\w-])flag(?![\w-])`, then add the three flags to the guide.
- Class: 58.

## HEALTH-22 LOW CONFIRMED: `test_pty_ping_round_trip` takes 10 s for 0.3 s of work

- Where: `tests/test_sim_pty.py:28` `_read_line_matching`. `ser.read(4096)` with `timeout = remaining` blocks until 4096 bytes arrive or the 5 s deadline passes, and it runs twice.
- Measured: 10.08 s twice. The same steps done directly take 0.07 + 0.02 + 0.18 s (`probe/pty_t.py`).
- Fix: `ser.read(ser.in_waiting or 1)` or `readline()`.

## HEALTH-23 LOW CONFIRMED: assertions wider than the behaviour

- `test_e2e.py:162` accepts `/cmd` on a held port as `(200, 400, 409, 503)`, and `test_sessions.py:233` accepts `/send` as `(200, 400)`. The answer is a deterministic 400 "port board is not connected" (`probe/held_cmd.py`), and SPEC 3.4 line 695 says 400.
- Fix: assert 400 and the message.
- Class: 29.

## HEALTH-24 LOW CONFIRMED: test-only production code

- Keep the grammar's raw entry points in `protocol.py` (`parse_can_event`, `parse_plot_def`, `parse_plot_adhoc`, `decode_plot_sample`, `PlotDecoder.points`). They faithfully front the `*_tokens` forms, and `test_store_fastpaths.py:390` pins their equivalence.
- Delete these, which are called only by tests:
  - `cli._value_taking_opts` and `cli._hoist_global_opts` (`cli.py:2892-2901`), plus `cli_argv.hoist_global_opts`. Tests can call `split_global_opts`.
  - `store._broadcast` (`store.py:1106`).
  - `webui/chrome.js:171` `syncWindowButtons` and `layout.js` `TITLE_MAX`.
- `test_plot.py` checks ingest through `store.query_plot_channels`, a SQL form production never serves (the endpoint reads the writer's summary). A summary-only defect therefore passes it; point it at `query_plot_channels_safe`.

## HEALTH-25 LOW CONFIRMED: behaviour-preserving simplifications

- `store.py:1696-1707` restates `_window_terms`' `since_ts` branch. The fold was applied to a scratch copy, and 4 named tests pass (listed in `src-py.md` SRC-10). With the branch removed outright, 2 of them fail.
- `server.py:2122-2145` `_search_batch` is `_scan_batch` with one pattern. SUSPECTED only, not run.

## HEALTH-26 LOW CONFIRMED: comments and docs that no longer match the code

- `docs/ARCHITECTURE.md:31`: `query_plot_channels` is not what "the rebuild" uses. The rebuild is `_scan_plot_summary`, and `query_plot_channels` is test-only.
- `docs/ARCHITECTURE.md:109` and `cli_client.py:3-5`: "every request policy (request, probe, ...) routes through `_daemon_errors`". `probe`/`probe_status` (`cli_client.py:159-170`) do not.
- `docs/ARCHITECTURE.md:130-133`: the socket/TCP listener set is `test_sim_tcp.py` and `test_sim_pty.py`, but `test_break.py:89` also spawns the TCP listener (deliberately, for the socket break no-op).
- `host/pyproject.toml` "slowest test is ~14 s": see HEALTH-13.
- `host/tests/conftest.py:3-5` calls `mcu_sim` "a development tool, not part of the mcuscope package". It is now a shim over `mcuscope.sim`, and `support.py` imports `mcuscope.sim` directly.
- `protocol.py:864,909` point at `plots.js:195/227`; the lines are now 208/240. Name the function instead.
- Stale comments at:
  - `serial_link.py:1-8,567-570,960-967`
  - `store.py:3-10,359-370,2325-2330`
  - `server.py:96-108,320-321`
  - `state.js:133` (no main.js exists)
  - `app.js:5-6,15-16,29` (an always-true `typeof` guard)
  - `can.js:59`
- Details: `src-py.md` SRC-9 and `js.md` JS-11.

## HEALTH-27 LOW: where the suite's time goes, and the organisation

- Durations, from a serial per-file sweep of 74 files: 2041 passed, 1 failed (HEALTH-14), 1 skipped, 964 s under load ~10-15.
  - `test_cli.py` takes 130 s over 167 tests. Each spawns `mcu`, which costs about 0.65 s per spawn, 0.40 s of it importing `mcuscope.cli` (typer and protocol).
  - `test_eol.py` spends 34 s building 54 stacks for a parametrized pydantic 422 check (`:253`). A module-scoped stack, or a TestClient without the sim, would take a few seconds.
  - `test_e2e.py` spends 17.5 s in setup across 34 stacks.
- Organisation: 31 of 75 Python files (11.4k of 30.6k lines) and about 30 JS files are named after review rounds.
  - JS mutants M24, M26, M34, M44, M50, M51, M54, M67, M69, M70 and M75 are killed only by a round-named file, never by the module's topical file. Python mutants K01, P03 and A05/A06 were missed on the first pass for the same reason.
  - Concretely, someone who changes `cmdbar.js` or `pidfile.py` and runs the topical file misses those pins.
  - Moving tests by module under test (not by round) fixes that. Merging the copies of `client()` (17 files) and `_mk_app` (6) buys little on its own; do it as part of the move.

## Checked and fine

- ARCHITECTURE.md symbol claims: every named symbol exists in the stated module (scripted grep of 34 names). `_EpisodeNotice` has exactly five instances (`serial_link.py:340-374`). The daemon startup order matches `daemon.py:362-440`.
- CLAUDE.md sim facts: I2C 0x48/0x50, 10 Hz CAN heartbeat on 0x100 (`sim.py:88,422`).
- Child spawns: every `mcu`/`mcuscoped` spawn uses `child_env()` or a `_spawn_env` built on it. The spawns without env run only `sys.executable -c` over pure code, `make`, or node.
- JS order dependence: all 888 tests in multi-test files pass when run alone, and the checker flags a planted dependent test.
- DOM stub `<select>` writes: every written value is an option present at the time of the write.
- Shared grammar fixtures (plot grammar, CSV cell) kill all 12 grammar mutants on both sides.
- Duplicated constants between the CLI and daemon agree (timeouts, window, match length, lookback, alias regex).
- `repeat_refusal` is a single implementation. The paged and streamed `log export` render byte-identically.
- Mutation totals: Python 66 of 83 killed, JS 72 of 82 killed. Every survivor is listed above or in the helper reports.

## Not covered

- The whole-suite wall time ("~4 min" in CLAUDE.md): the whole suite was not run, by the brief. The per-file sweep ran under heavy parallel load.
- Windows-only branches and behaviour. The browser-only drawing.
- `_stdio.py`, `pidfile.py`, `lockfile.py` and `update_check.py` were skimmed for drift, and only mutated.
- The survivor probe tests were not run under the repo's conftest.
