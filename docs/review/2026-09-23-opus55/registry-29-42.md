# Registry leg, classes 29-42 (HEAD f31ecd9)

HEAD verified `f31ecd995ee2ed193d8d60637b76620ddc880be3` before sweeping.
Scratch, probes and sweep scripts: `~/tt-data/mcuscope-2026-09-24/registry-leg/29-42/`.
Mutations ran only in a private copy there (`mut/host`), each restored and `cmp`-checked against `git show HEAD:`.

Status: classes 29-42 complete.

## Findings

### R35-1 LOW `host/mcuscope/daemon.py:442` - a warning that cannot be delivered stops the daemon it was warning about
- `mcuscoped --ignore-capture-lock` over a held capture prints its WARNING with a bare `print(..., file=sys.stderr)`.
- With stderr a closed pipe the print raises BrokenPipeError out of `main()`, so `console_entry` writes `mcuscoped-crash.log` and the process exits 1: the daemon the user asked to start anyway does not start.
- The same bare print sits on every daemon refusal (`daemon.py:391, 427, 440, 453, 466`); those keep exit 1 only because an uncaught exception also exits 1, and each writes a spurious crash log.
- `_stdio._note` is the guarded helper for exactly this (suppress, then dup2 devnull over the fd); daemon.py does not use it.
- Driven: `probe35daemon.py` holds the lock and spawns the real `mcuscoped` with an isolated TOML on port 18598. Stderr attached: running after 4 s. Stderr a closed pipe: rc 1 at once, crash log `BrokenPipeError` at `daemon.py:442`. `probe35.py` shows the refusal case (rc 1 both ways, crash log only when closed).

### R35-2 LOW `host/mcuscope/sim.py:813` (and 797, 922, 1103, 1155, 1157, 1184) - the simulator's error-reporting prints end the process they report on
- The listener's "client session failed" handler prints to stderr unguarded. With stderr closed the print raises out of the `except`, ends `serve_listener`, and `mcu-sim` exits via the crash handler instead of accepting the next client.
- `sim.py:922` (a truncation notice inside a session) raises into that same handler, so the first oversized line kills the listener the same way.
- `sim.py:1103` (Windows, `--pty` refusal) returns 2; with stderr closed it exits 1 instead.
- `sim.py:1184` is a warning on the `--pty --symlink` start path: a closed stderr aborts the start.
- Driven in process (`probe35sim.py`), with a stderr whose writes raise BrokenPipeError and `_serve_socket_client` forced to fail. Control (stderr attached): the listener is alive after two failed sessions. Closed: it died with `BrokenPipeError`.
- The registry's earlier exemption ("none of which own an exit code, the process keeps serving") does not hold: the print is what stops it serving.

### R39-1 LOW `host/mcuscope/server.py:2408-2418` `_until_stopped`, and `server.py:2920-2933` `_admit_and_build` - a raced future that already holds an exception is never retrieved on the cancellation exit
- `_until_stopped`'s `finally` cancels and awaits `work` only `if not work.done()`.
  - If the handler is cancelled in the same loop tick that `work` finished with an exception, `work` is done and skipped.
  - Its exception is never retrieved: asyncio logs "Task exception was never retrieved" at GC.
- Reached by `/wait` and `/assert` with `send` (`watch.until_stopped(port_obj.send_command(...))`, `server.py:2662-2665`, `3147-3150`) when the handler is cancelled, e.g. by uvicorn's graceful-shutdown cap.
- `_admit_and_build` has the same shape: in its `except asyncio.CancelledError`, `built.cancel()` is a no-op on a build that already failed, and the wrapped future's exception is never retrieved.
- Driven for `_until_stopped` (`probe39.py`):
  - Positive control (no cancel): 0 reports.
  - `work` resolved with RuntimeError and the outer cancelled in the same tick: 1 report, "Task exception was never retrieved | RuntimeError(...)".
- `_admit_and_build`: reasoned only (same asyncio mechanism).

### R36-1 LOW `host/mcuscope/sim.py:498-512` `_poll_flood` - capped per pass but never re-anchored, so a stall is backfilled
- `owed` is capped at FLOOD_MAX_BURST (5000) per pass, and the schedule advances only by what was emitted, so the whole backlog is still paid back.
- Driven (`probe36.py`): `--flood 20000`, a 1 h stall, 10 ms passes. Result: 15000 consecutive passes at the cap, 75.0 M lines, 150 s at 500,000 lines/s against 20,000 requested.
- Registry contradiction, reported rather than resolved:
  - Class 36 says `FLOOD_MAX_BURST` "was added for exactly this invariant".
  - The invariant also requires a re-anchor past a large stall. Flood's own comment chooses the catch-up ("bounds the rate at which a stalled sim catches up, not the configured rate itself").
  - Either flood is exempt (its backlog is load the operator asked for), or it violates. I read it as a violation: the lines are synthetic and the 25x overshoot is exactly the post-suspend burst the class exists to stop. Owner should pick.

### R29-1 LOW `host/mcuscope/server.py:2175-2176` - the /marker port-grammar guard is revertible in silence
- The only test (`test_server_request_validation.py:263-266`) asserts status 400 and `"port" in error`.
- With the grammar check removed, every one of its bad ports still gets 400 from `_unknown_port` ("no such port: ..."), which also contains "port".
- Driven: the check mutated to `if False:` in the private copy; `tests/test_server_request_validation.py` 35 passed.
- Either the guard is redundant with `_unknown_port` (it only differs for a stored row carrying an ungrammatical port, i.e. a legacy capture) or it needs a test on text unique to it (`invalid port:`).

### R29-2 LOW `host/mcuscope/serial_link.py:1170-1171` - a transport failure during `/break` has no test
- `_break_locked` maps SerialException/OSError from `send_break` to `PortError("... break failed ...")` (a 400). No test raises from `send_break`.
- Driven: the mapping replaced by a bare `raise` in the private copy. `test_break.py` (19), `test_link.py` (5) and `test_serial_link_tx.py` (17) all passed.

### R29-3 LOW `host/mcuscope/cli.py:1317`, `cli.py:1323` - two follow refusal arms with no assertion
- The `websocket error:` arm (exit 3) is reached by `test_cli_follow.py:161-170` (InvalidURI), which asserts neither exit code nor text. No test reaches the `unexpected row shape ... missing` arm (exit 1).
- Driven: both exit codes swapped (3->1, 1->3) in the private copy. `test_cli_follow.py`, `test_cli_follow_frames.py`, `test_cli_transport_timeouts.py`, `test_cli_output_rows.py` and `test_cli_closed_pipe.py` all passed.
- Not run against `test_cli.py`, the heavy file. Its WS tests (`test_cli.py:2297-2310`) cover close codes and InvalidStatus only, by reading.

### R31-1 LOW `host/mcuscope/server.py:2600-2621` (`_do_wait`) and `server.py:3041-3042` (`_do_assert`) - `send_mode` without `send` is accepted and ignored, while `eol` without `send` is refused
- Both endpoints refuse `eol` with no `send` ("eol applies to send; set send too").
- `send_mode` is the same kind of send modifier. Without `send` it answers 200 and is never read.
- Driven (`probe31.py`, sim stack):
  - `/wait` with `send_mode:"raw"` and no `send`: 200 timeout. With `eol:"lf"` instead: 400.
  - `/assert` with `send_mode:"raw"` and no `send`: 200 pass. With `eol:"lf"` instead: 400.
- It cannot change the judged scope, since nothing is sent either way. The finding is the sibling asymmetry the class names.
- Reading `"send_mode" in body.model_fields_set` would let the handlers refuse it like `eol`.

### R34-1 LOW `host/mcuscope/webui/chrome.js:25-33` `loadColors` - a saved colour is type-checked, not grammar-checked
- Values re-read from localStorage pass if `typeof === "string"`. A hand-edited `"garbage"` is returned by `colorFor` as a stroke, and canvas ignores an invalid `strokeStyle`.
- That lane or series then draws in whatever colour the context last held: class 34's original symptom, reached through the value instead of the key.
- The writer only ever stores `<input type=color>` output (`#rrggbb`), so a `^#[0-9a-f]{6}$` check on load costs nothing.
- Reasoned only; the DOM stub cannot show a canvas.

### R32-1 LOW `host/mcuscope/_stdio.py:41` `console_close_hook` - module state written outside its owner with no per-test reset
- `daemon._serve` assigns `_stdio.console_close_hook` (`daemon.py:306`). The AST sweep missed it because the write is cross-module.
- In-process `_serve` calls (`test_daemon_startlog.py:42, 60, 79, 88`, `test_daemon_process.py:215`) leave it pointing at a finished server's config. Conftest has no reset and no stated exemption.
- No test observed to depend on it: the two readers set it themselves (`test_daemon_console.py:66, 99`). The four affected files pass under seeds 11, 22 and 33 each (`seeds32.txt`).
- An exemption line or a conftest reset closes it.

## Verdict lists

### Class 29, the negative is never asserted

Sweep, mechanical (`sweep29.py`, AST):
- Every refusal message in `mcuscope/*.py` passed to `_bad_request`, `die`, `PortError`, `StoreError`, `ConfigError`, `MatchBudgetExceeded`, `CaptureStopped` or `LockError`, or in a 4xx/5xx `JSONResponse` `error`. That is **245 sites**.
- Each message's literal was searched for in `tests/*.py` and `tests/webui_js/*.mjs`.
- 146 sites matched by their longest literal, and 52 carry no literal (a variable message).
- `sweep29b.py` then took the 99 unmatched and searched for any test string literal (>= 10 chars) inside the message, which cleared 80.
- The last 19 were ruled by hand: grep for shorter fragments, reading the test, and a mutation where a test looked weak.

The registry's own named guards:
- Opener dispatch `connected` negative: 11 assertions (`grep -rnE 'connected"?\]? (is|==) False|"connected": False' tests`). Complies.
- Capture lock: 5 `raises(LockError)`. Complies.
- `PRAGMA foreign_keys`: read back and child table asserted directly, `test_plot.py:324, 338`. Complies.

Full per-site list: see "Appendix A" at the end of this file (245 rows, every one ruled).

Sweep imprecision: the registry's "grep for an assertion of that value" misses a shared-wording assertion.
- R29-1 passed the grep ("port" is asserted) while the guard was untested.
- Improved sweep: take each refusal's text unique to it (not shared with the next refusal on the same path) and grep for that. Where a test exists, mutate the guard and run the test's file.

### Class 30, a wrapper that trusts an external runner's exit code

Sweep: `ast33.py` over `tests/*.py`, every `subprocess.run/Popen/call/check_output`: **54 sites**. Those running a test runner or compiler:
- `test_firmware_monitor.py:49` (`make run|asan|families|families-asan`): **complies**. `_assert_all_checks_ran` parses `<n>/<n> checks passed`, requires `runs` summaries, and fails on a total of 0.
- `test_firmware_monitor.py:84` (sanitizer link probe): **exempt**, a capability probe.
- `test_firmware_monitor.py:125` (`-fsyntax-only`): **exempt**, a compile check that asserts stderr content and has a positive control.
- `test_webui_js.py:343` (`node --test`): **complies**. It parses both `# pass N` and `ℹ pass N`, and fails when pass is below the declared `test(` count.
- `test_webui_js.py:309` (`node -e`): **exempt**, not a runner; stdout is parsed and compared per URL.
- `test_webui_js.py:34` (`node --version`): **exempt**, a version probe.
- The other 48 sites drive the shipped artifact or a bare helper, not a runner (listed under class 33). **Exempt.**

CI (`.github/workflows/ci.yml`): node pinned to 22 (`:61`).
- `:119` `make asan` is exit-code-only, but on Linux the pytest sanitizer tests run the same target with the count guard. **Complies as a pair.**
- Note: the "Verify firmware C tests actually ran" step (`:92-97`) exempts sanitizer skips on every OS, while its comment says Windows only. A Linux runner that lost libasan would skip the counted run silently, leaving only the exit-code `make asan` (which would then fail to link, so it is not silent). Not a violation.

### Class 31, a field the model accepts and the path never reads

Sweep A: `ast31.py`, request models in `server.py`, **18 models** (`_Body` base plus 17), and every field read in its handler.
- `PortAttach` (5), `SendBody` (3), `CmdBody` (4), `BreakBody` (2), `PurgeBody` (6), `MarkerBody` (2), `SessionBody` (2), `ConfigServerBody` (3), `ConfigStorageBody` (6), `ConfigUpdateBody` (2), `PlotJugglerBody` (2), `ConfigPlotJugglerBody` (3), `ConfigPortsBody` (2): **comply**. Each field is read in its single-branch handler.
- `ConfigPortEntry` (7): **complies**, read per entry in `put_config_ports`.
- `WaitBody` (9): read in `_do_wait`; `repeat_ms` refused without `send` or raw mode (`protocol.repeat_refusal`); `eol` refused without `send`; `since` refused unless "now".
  - `send_mode` without `send`: **VIOLATES**, R31-1.
- `AssertBody` (12): `min_window_ms`, `session`, `last_ms` and `send` each refused on the mode that ignores them (`server.py:3024-3040`); `allow_empty` is read in `verdict()` on both modes; `port` and `chan` are read on both branches.
  - `send_mode` without `send`: **VIOLATES**, R31-1.

Sweep B: `ast31b.py`, every route's parameters (**37 endpoints, 80 parameters**), a word-boundary read test in the handler body: **0 unused**.
- `/lines` (11), `/lines/export` (10), `/can/frames` (11), `/plot/series` (8), `/plot/export` (12), `/sessions` (2), `/sessions/{id}` (2), both exports (2 each), `/ws` (1): **comply**.
- The per-parameter "changes the result" test was not re-run for every parameter; it is owed.

### Class 32, a function tested as pure that mutates module state

Sweep A: `ast32.py`, every module-level name a function rebinds (`global`) or mutates (`.add/.update/[k]=`...): **8 names**.
- `cli_output._JSON_MODE`, `cli_output._OUT_FAILED`, `_stdio._repaired_at_start`: **comply**, reset per test in `conftest.py:135-137` (`_isolate_output_state`).
- `_stdio._report_key`: **complies**, restored by `conftest.py:107-121` (`_isolate_report_key`).
- `_stdio._ctrl_handler_ref`: **exempt**, written once by the installer; the tests that drive it monkeypatch it (`test_daemon_console.py:39, 45, 65`, `test_stdio.py:295`).
- `serial_link._comports_cache`: **exempt**, a sub-second TTL cache. Tests patch it with `monkeypatch.setattr` (`test_reconnect.py:154, 171`) or patch `cached_comports` itself.
- `server._pools`, `store._match_pool`: **exempt**, process-wide lazy pools, never reset.

Sweep A2 (cross-module writes the AST misses): `grep -rnE "^\s*(_stdio|cli_output|...)\.[A-Za-z_]+\s*=" mcuscope`: 3 hits.
- `_stdio.console_close_hook` (`daemon.py:306`): **VIOLATES**, R32-1.
- `config.ports` (`daemon.py:172`) and `sim.rx_overflow` (`sim.py:703`): **exempt**, instance attributes, not module state.
- Process-wide replacements: `sys.stdout = _GuardedStdout(...)` (`cli_output.py:256`) and `setattr(sys, ...)` (`_stdio.py:172, 313`). **Exempt**: pytest's capture manager restores sys streams around every test.

Sweep B, random ordering: pytest-randomly is installed, so every run is shuffled. Several seeds over the whole suite is owed, because the brief forbids whole suites. Seeds 11, 22 and 33 over the four files that touch this state pass: `test_daemon_console.py` 5, `test_daemon_startlog.py` 5, `test_cli_argv.py` 10, `test_stdio.py` 22.

Sweep imprecision: the registry's AST instruction finds only same-module writes. Add a cross-module grep for `<module>.<name> =` and `monkeypatch`-less `setattr(<module>, ...)`, which is how R32-1 was found.

### Class 33, a test that runs the real entry point inherits the user's real environment

Sweep A: `grep -rn "platformdirs\." mcuscope` returns 1 executable call, `dirs.py:23` (`getattr(platformdirs, f"user_{kind}_dir")`) behind `dirs.user_dir`. Its callers: `config.py:149, 157`, `pidfile.py:61`, `_stdio.py:340`, `update_check.py:97`.
- `conftest.py:34-45` patches all three `user_*_dir` functions and deletes `MCUSCOPE_{DATA,CONFIG,CACHE}_DIR`. **Complies.**

Sweep B: `ast33.py`, **54 spawn sites** in `tests/`:
- `env=child_env(...)`, directly or via a wrapper that calls it (`run_mcu`, `_spawn_env`, the env built beside the call): **comply**.
  - `test_break.py:165`; `test_cli.py:67, 82, 523, 580, 832, 855, 873, 883, 891, 967, 988, 1301, 1889, 1919, 2418, 2541, 2548, 2558, 2713`.
  - `test_cli_closed_output.py:69`; `test_cli_closed_pipe.py:46, 58, 141`; `test_cli_closed_stdio.py:24`; `test_cli_daemonctl.py:471`; `test_cli_ux.py:322`.
  - `test_daemon_process.py:111`; `test_dirs_override.py:130`; `test_pidfile.py:146`; `test_port_column_stored.py:132`.
  - `test_scaffold.py:94`; `test_sim_pty.py:49, 95`; `test_sim_tcp.py:284`; `test_stdio.py:342`.
- `test_capture_lock.py:84`: **exempt**. The child locks an explicit `tmp_path` db and resolves no user dir.
- Bare `python -c` children that import nothing from the package: **exempt**. `support.py:471`, `test_cli.py:204`, `test_cli_daemon_stop_scope.py:77, 198, 431`, `test_daemon_process.py:32, 56`, `test_pidfile.py:89, 283`.
- `test_cli_ux.py:469` (`cli.main` for `--help`, `--version`, `ai-guide`) and `test_scaffold.py:149` (`cli.main(["status"])` over a MockTransport): **exempt**. Neither path resolves a user dir, which was checked by grep: `cli*.py` resolve dirs only in `cli_daemonctl._pid_file` and `default_config_path`. Neither goes through `console_entry`, so no crash-log path.
- `test_webui_js.py:34, 309, 343` and `test_firmware_monitor.py:49, 84, 125`: **exempt**, node, make and cc.

Sweep C, in-process `cli.main([... "daemon", "start"|"restart" ...])`, which spawns through the package's own `subprocess.Popen`: **41 call sites** in 7 files. Each file was checked for a stub.
- `test_cli_daemonctl.py` (`spawn`/`_fake_spawn`/local `popen`), `test_cli_ux.py` (`fake_spawn`), `test_cli_daemon_stop_scope.py` (local `spawn`/lambdas, `_start_daemon`), `test_cli_start_index_build.py` (`spawn`) and `test_daemon_startup.py:246`: **comply**, `Popen` replaced.
- `test_status_ppid_serial.py:76`: **complies**. It really spawns, after `monkeypatch.setenv` of every `child_env` variable.
- `test_cli.py:438, 787` go through `run_mcu` (child_env). **Comply.**

Sweep imprecision: the registry sweep lists `Popen|subprocess` in tests. It misses the in-process `cli.main(["daemon", "start"])` route, where the spawn is in the package. Sweep C is the addition.

### Class 34, a wire-named key on a prototype-bearing object store

Sweep, the registry command `grep -n "JSON.parse\|= {}\|= Object\|localStorage" mcuscope/webui/*.js`: **50 lines**. Plus every bracket read by a non-literal key (`grep -noE "[A-Za-z_$][A-Za-z0-9_$.]*\[[A-Za-z_$][A-Za-z0-9_$.]*\]"`, index-like names filtered): **43 reads**. Plus object-literal tables (`(const|let|var) X = {`): **33**.

Wire-keyed stores:
- `chrome.js:25` `savedColors`: null-proto, and the loader checks `typeof string`. Keys **comply**; values **VIOLATE** (R34-1).
- `plots.js:30` `PLOT_TYPES`, `statusbar.js:309` `DISCONNECT_WHY`, `state.js:126` `portTarget`, `statusbar.js:517-521` `aliasMap` (`portEol`, `portTarget`), `state.js:480` `cmdModes`: **comply**, `Object.create(null)`. `cmdModes` values are checked against `MODE_CHOICES`.
- `plots.js:494` `plotTitles` via `layout.js:59` `parseTitles`: **complies**, null-proto, `typeof string` plus `cleanTitle`.
- `terminal.js:120` `TAG[chan]`: **exempt**. `chan` is the daemon's closed domain (the lines table CHECK; `Chan` Literal, `server.py:256`), and a miss falls back to `chan`.
- `digital.js:556, 635, 770` `LANE_KINDS[lane.kind]`: **exempt**, `kind` is set by the decoder to "bits" or "enum".
- `exportdlg.js:20` `values[f.name]`, `exportdlg.js:219` `TITLES[ctx.kind]`, `chrome.js:197` `ARROW_STEP[e.key]`, `terminal.js:808` and `digital.js:578` `TIME_AXIS_LABELS[state.timeMode]`, `plots.js:1009` `scales[skey]`, `statusbar.js:519` `p[field]`: **exempt**, keys from code, not the wire.
- `plots.js:341` `values[name]`: a comment naming the daemon's `_csv_wide`; the store is `row.points`, a `Map`. **Complies.**

localStorage reads, values:
- `layout.js:17` `parseLayout` (finite and range per field), `exportrange.js:31` `validate`, `cmdbar.js:22` (array, string filter, cap), `can.js:279` (array, string filter), `terminal.js:827` with `pane.js:96` `paneCfgFromStorage` (per field), `theme.js:16` (enum), `state.js:17` token (string), `state.js:453` EOL (`isEol`), `state.js:482` `cmdModes` (enum), `statusbar.js:117` dismissed version (compared with `===`): **comply**.
- The rest of the grep's 50 lines are writes (`setItem`), comments, or `api.js:689` (a WS frame, routed by field into Maps). **Exempt.**

Sweep imprecision: the registry grep misses object literals with content (`const TAG = {...}`) and `Object.fromEntries`. The table grep above is the addition.

### Class 35, an error-reporting write failing hijacks the exit code the error owned

Sweep: every stderr write and every `print(` in `mcuscope/*.py`. **75 `print(` lines** (comments excluded), plus `sys.stderr.write` at `cli_output.py:73`.

Error-path writes:
- `cli_output.py:64-77` `err_write` (the route of `err()`, `die()`, and every CLI stderr message): **complies**. It suppresses OSError and repoints stderr at devnull.
- `cli_output.py:259-272` `out_json` (the `die()` JSON half): **complies**. BrokenPipe silences stdout; other OSError goes to `_stdout_unwritable`.
- `_stdio.py:398-411` `_note`: **complies**.
- `daemon.py:442` (WARNING, then continue): **VIOLATES**, R35-1, driven.
- `daemon.py:427, 440, 453, 466` (refusal, then `return 1`): **VIOLATE**, R35-1. Exit 1 is kept only because the uncaught BrokenPipeError also exits 1, and a crash log is written. Driven for `:427`.
- `daemon.py:391` (warning, then continue; embedder off the main thread only): **VIOLATES**, R35-1 shape, reasoned.
- `sim.py:797, 813, 922, 1155, 1157`: **VIOLATE**, R35-2, driven for `:813`.
- `sim.py:1103` (Windows `--pty` refusal, return 2): **VIOLATES**, R35-2, reasoned (Windows-only branch).
- `sim.py:1184` (symlink warning on the `--pty` start path): **VIOLATES**, R35-2, reasoned.

Not error-path writes:
- The CLI stdout prints (`cli.py` 45 sites, `cli_daemonctl.py:388-393`, `cli_output.py:283, 549`) go through `_GuardedStdout` (`cli_output.py:255-256`). **Exempt** for this class.
- The daemon's stdout notices (`daemon.py:142, 152, 431, 493, 498`) and the sim's device-string prints (`sim.py:1056, 1119`): **exempt** for this class. They are not error-path writes, but see "What should we have checked": `daemon.py:431` crashes a daemon whose stdout is a closed pipe.

### Class 36, a periodic catch-up loop without a burst cap

Sweep: the registry's `while <schedule> vs now` grep finds none of the real sites, because the loops are per-call helpers. Used instead: `grep -rnE "while .*(now|monotonic|time\(\)|next_|due)|if now >=|_due_beats|MAX_BURST" mcuscope` plus `setInterval`. That gives **9 schedules in the sim, 1 in the daemon, 5 JS timers**.
- `sim.py:429` heartbeat, `:447` CAN bus per id, `:476` marker, `:532` reading, `:538` fault, `:566` plot samples: **comply**. All go through `_due_beats` (`sim.py:126-137`): cap `PERIODIC_MAX_BURST = 4`, and a re-anchor to `now + period` when the cap bites.
- `sim.py:505` `_poll_flood`: **VIOLATES**, R36-1 (cap without re-anchor).
- `sim.py:465` pending echoes and `sim.py:559` plot-def rebroadcast: **exempt**. One-shot due times, and the rebroadcast checks elapsed since the last send.
- `server.py:2560` `_repeat_send`: **complies** (`server.py:2596`), `next_at = max(next_at + period_s, loop.time())` re-anchors.
- `store.py:3111` `_retention_loop`, `update_check._due`, `serial_link._retry_wait`: **exempt**, sleep-and-check loops that emit nothing per missed beat.
- `app.js:194`, `can.js:722`, `api.js:100`, `terminal.js:254`, `plots.js:1456`: **exempt**, `setInterval`, which browsers do not backfill.
- Firmware monitor: `grep -nE "while *\(|period|next_" firmware/monitor/*.c` has no schedule loop. **Exempt.**

Improved sweep: `grep -rnE "next_[a-z_]*\s*(\+=|=.*\+)|_due_beats\(|max\(next_|owed" mcuscope`, since schedule variables are advanced in straight-line helpers, not in `while` conditions.

### Class 37, an async read-modify-write spanning an await without a lock

Sweep: `ast37.py`, every `async def` in `store.py`, `serial_link.py`, `server.py` and `update_check.py`, with its awaits, locks and shared-state writes. That is **130 sites** (59 store and link, 71 server and update_check). Per-site list: "Appendix B" at the end of this file.

Rulings needing more than a lock name:
- `delete_session` (`server.py:1520-1549`): **complies, with a residual**.
  - It reads the session and fixes `end_id = max_id()`, awaits `delete_range`, deletes the label, then reopens an automatic session if none is active. It does not take `session_stop_lock`, which its sibling `POST /sessions/stop` holds for the same check-and-reopen.
  - Two concurrent `DELETE ...?data=true` of the running session can both pass the read. Both answer `{"ok": true}` (the second for a label the first already removed; `delete_session`'s bool is ignored), and both can call `start_session(auto)`.
  - The second auto start closes the first and opens another. The first is empty, and `_stop_session_locked` drops an empty auto session, so no stray row survives.
  - Rows committed during the delete above the stale `end_id` stay in the capture unlabelled. That is kept data, not lost data.
  - Reasoned, not driven. Reported for the owner: taking `session_stop_lock` here would make it match its sibling.
- `POST /purge` (`server.py:1734-1792`): **complies**. Spans are read and then deleted under `_sweep_lock`; a concurrent delete can only shrink what the range finds.

### Class 38, a reset re-run missing the discipline the first run has

Sweep: every caller of each web UI initialization routine (`grep -nE "\bNAME\(" webui/*.js`, excluding the definition), and each daemon attach path.
- `runBackfill`: `api.js:679` (first connect, in `onopen`) and `api.js:222` (`resetForDbReset`). Line-by-line diff:
  - Both run `armStaging(gen)`, then `runBackfill(gen)`, `.catch`, `.then(drainStaging(gen))`, with `gen = wsGen`.
  - The reset additionally zeroes every watermark and floor the first run starts from (`maxId`, `canFloor`, `chartFloor`, anchors, `tickAnchors`, per-pane `clearId`/`clearGen`/`frozenId`/`frozenRows`/history).
  - A token met while staging re-arms staging per row (`feedStaged`, `api.js:848`).
  - **Complies.**
- `seedPlotDefs` (`api.js:567`), `seedPlotHistory` (`:576`), `seedChannelList` (`:390`), `seedLastMs` (`:403`), `plotSeed` (`:419`): single callers inside `runBackfill`, so they inherit its two callers' discipline. **Comply.**
- `connectWs`: `app.js:190`, `api.js:862` (backoff) and `api.js:873` (`reconnectStream`). All three go through the same function, which closes the previous socket and bumps `wsGen` first. **Complies.**
- `resetHistory` (4 callers) and `rebuild` (8 callers): resets and views, bounded by `clearId`/`frozenId`. **Comply.**
- Daemon `PortManager.attach` callers: startup autoconnect (`server.py:502`), `POST /ports` (`:1160`), reconnect (`:1188`, `replaces=`). One routine: `prime_plot_defs`, then the checks under `_lock`, the carried counters, and `start()`.
  - `POST /ports` also drops `detached_meta[alias]`. The reconnect's port is attached, so it has none; startup has none yet. **Complies.**

### Class 39, a raced task orphaned by the exceptional exit

Sweep: `grep -rn "create_task\|ensure_future" mcuscope`, **18 lines, 17 create sites** (`serial_link.py:656` is a docstring).
- `cli.py:1130, 1132, 1140` (`_stage_backfill`): **complies**.
  - Every return consumes `task` (`await task` / `task.result()`), and the `except BaseException` cancels and awaits `recv`.
  - The one exit that leaves `task` unconsumed is the outer task's own cancellation. There the to_thread task is still pending and is cancelled by `asyncio.run`'s shutdown; a cancelled task files no report.
- `server.py:2273-2274` (pump/watch under FIRST_COMPLETED): **complies**, `finally` cancels and awaits both under `suppress(CancelledError, Exception)`.
- `server.py:2408-2409` (`_until_stopped`): **VIOLATES**, R39-1, driven.
- `server.py:2872` (`gone`): **complies**. `_client_left` only returns normally, so it holds no exception to orphan.
- `server.py:2901` (`freed`, an Event wait): **complies**, it cannot fail.
- `server.py:2653` (repeater): **complies**, `finally` cancels and awaits it with `suppress(Exception, CancelledError)`.
- The wrapped build future in `_admit_and_build` (`server.py:2920-2933`, not a create_task but raced the same way): **VIOLATES**, R39-1, reasoned.
- Not raced, so **exempt**:
  - `serial_link.py:395` (consumer; consumed in `stop()`).
  - `serial_link.py:666, 689` (`_bg_tasks`: strong refs, waited and cancelled in `stop()`).
  - `store.py:693, 697, 698` (awaited in `stop()`).
  - `update_check.py:246` (detached singleton with a blanket handler).

### Class 40, multi-attribute state shared between the loop and a worker thread, torn on read

Sweep: `grep -rn "to_thread\|threading.Thread\|run_in_executor\|ThreadPoolExecutor" mcuscope`, **43 lines**. Executable hops and threads, ruled:
- `server.py:450, 1406` `pj.configure`: **complies**.
  - `_target` is one immutable `(socket, sockaddr)` swapped in one store; `send` reads it once. The mutating endpoint holds `config_write_lock`.
  - Residual: `dest` and `_target` are two stores, and `GET /plotjuggler` reads both unlocked. `probe40.py` hammered a thread flipping between two dests against a reader: 0 torn pairs in 400,000 reads with `sys.setswitchinterval(1e-6)`. No call or backward jump sits between the two stores, so CPython does not switch there.
- `server.py:475, 533` (export orphan sweep and removal), `:1246, 1299, 1321, 1352, 1378, 1421, 1459` (config reads and saves), `:1373` (`pjstream._resolve`), `:1209` (`_enumerate_devices`), `:2324` (`next(it)` of a store generator): **comply**. They return values or write files, and each config save holds `config_write_lock`.
- `server.py:2389` live-match pool (`_search_batch`, `_assert_scan`): **complies**, pure over its arguments.
- Export pool, `_ExportJob.run` / `abandon` (`server.py:2781-2852`): **complies**. `_abandoned`/`_finished` are under `self._lock`; `live` is a set mutated one call at a time.
- `serial_link.py:396` reader thread: **complies**. It writes only `self._link` (a single attribute) and posts everything else through `call_soon_threadsafe`.
- `serial_link.py:421, 437` (`join`, `_close_link_locked`), `:1151, 1211` (`_write_bytes`), `:1180` (`_break_locked`): **comply**. `_write_lock` is held, and `_write_health` is one immutable `_WriteHealth` swapped whole.
- `store.py:2086, 2164` (match pool, private read connection per thread): **complies**, read-only.
- `update_check.py:284, 291` `_save_cache`: **complies**. The fields are assigned on the loop before the hop; the thread writes the file only.
- `cli.py:1130` (single-process CLI): **exempt** for this class.
- `sim.py:1044` in-process sim thread with `link.SourceLink` (`link.py:238`): **complies**, `_lock` on read, drain, write and break.
- `daemon.py:518` `threading.Timer(webbrowser.open)`: **exempt**, touches no daemon state.
- The remaining lines are imports, pool constructors and comments. **Exempt.**

### Class 41, callee-filled memory read beyond what the contract obliges

Sweep: every output parameter in `firmware/monitor/monitor.h` (port struct, handler typedef, bus shims), each call site (`grep -nE "uart_read|mon_can_rx_pop|mon_can_stat\(|mon_i2c_xfer\(|mon_spi_xfer\(|mon_gpio_get\(|mon_adc_read\(|mon_info_extra\(|\.fn\(" monitor.c monitor_cmds.c`: **19 lines, 11 call sites** plus 7 weak defaults), and `port_template/monitor_port_template.c`.
- `uart_read(buf, max)` at `monitor.c:1173`: **complies**. The return is clamped to `sizeof g_stage` (`:1177-1179`), and only `[0, len)` is read.
- Handler `resp` at `monitor_cmds.c:537, 545, 551` (built-in and registered): **complies**.
  - The contract states NUL termination (`monitor.h:95-98`).
  - The caller also pre-terminates (`g_resp[0] = '\0'`, `monitor.c:1111`) and bounds the read (`emit_ok`, `mon_put_strn(..., sizeof g_resp)`, `monitor.c:311`).
- `mon_can_rx_pop(f)` at `monitor.c:989`: **complies**.
  - The frame is zeroed before every pop (`:988`); `bus` 0 maps to 1, and >MON_CAN_BUSES is dropped and announced.
  - `dlc` is clamped to 8 before `data[]` is read (`monitor.c:971, 975`); `id` is masked to the declared width.
- `mon_can_stat(rx, tx, err, state)` at `monitor_cmds.c:231`: **complies**.
  - All four are pre-initialised (`0`, `"active"`), and a NULL `state` is replaced.
  - Nit, not a violation: `state` is read as a C string by type. The header says only "may be left untouched", not "a static NUL-terminated string", and the template names the three literals.
- `mon_i2c_xfer(rd)` at `monitor_cmds.c:317, 345`: **complies**. The contract says "fill all rd_len" (`monitor.h:150-152`), and `read_into_resp` zeroes `n` bytes first (`monitor_cmds.c:94`). `:271` (probe) and `:299` (write-only) pass no read buffer.
- `mon_spi_xfer(rx)` at `monitor_cmds.c:372`: **complies**, same contract and the same zeroing.
- `mon_gpio_get(level)` at `monitor_cmds.c:407`: **complies**, pre-initialised `false`.
- `mon_adc_read(raw, mv)` at `monitor_cmds.c:428`: **complies**, pre-initialised `0` and `INT32_MIN`.
- `mon_info_extra(buf, max)` at `monitor_cmds.c:128`: **complies**. The contract says NUL-terminate within max; the caller passes `max - 1`, terminates the last byte itself, and pre-sets `extra[0]`.
- Weak defaults (`monitor_cmds.c:560-602`) and the template's stubs: **exempt**. They return NOSUP or false without writing, and every caller above tolerates that.
- `monitor_plot(def, tick, data, len)`: **exempt**. `data` is caller-provided input, not callee-filled; `len` is checked against the parsed definition.
- `mon_hex_decode` / `mon_parse_*` out-params: **exempt**. They are monitor-internal helpers, not a third-party contract, and every caller checks the return first.
- Python callback protocols (`link.SourceLink` over a source's `poll`/`feed`): **exempt**. There is no uninitialised memory in Python; a short or odd return is buffered as bytes (`link.py:243-275`).

Verified: `tests/test_firmware_monitor.py` 5 passed (C suite, sanitizer build, family flags and their ASan builds, eventf format check).

### Class 42, an exception handler naming a class the floor version does not raise

Sweep: `grep -nE 'except .*TimeoutError|suppress\(.*TimeoutError' mcuscope/*.py tests/*.py`, **15 sites**.
- `serial_link.py:1236`, `server.py:2219, 2394, 2523`: **comply**. Each is around `asyncio.wait_for` and names `asyncio.TimeoutError`.
- `cli.py:1284`: **complies**. It names both classes, around the websockets open.
- `cli.py:1062`, `store.py:493`, `server.py:2361, 2974`: **comply**. They wrap `regex`'s `timeout=`, which raises the builtin TimeoutError.
- `sim.py:787` (`socket.accept`), `tests/test_plotjuggler.py:417`, `tests/test_sim_tcp.py:43, 198, 247`, `tests/test_sim.py:534`: **comply**. `socket.timeout` has been an alias of TimeoutError since 3.10.
- Every `asyncio.wait_for` in the package (`serial_link.py:1235`, `server.py:2218, 2391, 2524`) is covered above.
- The `asyncio.wait_for` calls in tests have no handler, or expect a different exception. **Exempt.**

Floor interpreter: a scratch copy of HEAD (`git archive`) with a 3.10.20 venv and `--resolution lowest-direct`. These 8 timeout-heavy files were run singly, all passed:
- `test_assert` 39, `test_wait_repeat` 20, `test_server_live_verdicts` 39, `test_serial_link_tx` 17.
- `test_cli_transport_timeouts` 11, `test_server_ws` 4, `test_cli_follow` 15, `test_store_match_budget` 12.

The full suite on 3.10 is owed: the brief forbids whole suites.

## What stays owed

- Class 32: the whole suite under several random seeds.
- Class 42: the whole suite on the 3.10 floor.
- Class 31: the per-parameter "changes the result" tests.
- R29-3 mutation against `test_cli.py`.
- Windows: `sim.py:1103` (R35-2) and the EINVAL translation (`_stdio.translate_closed_pipe_errors`) under R35-1/R35-2's closed-stream shape.
- No class in this range needs a real browser, except confirming R34-1's drawn symptom.

## The two questions

1. What am I least confident about here?
   - R39-1 for `_admit_and_build` is reasoned from the mechanism `probe39.py` drove on `_until_stopped`, not driven itself.
   - R36-1's severity rests on reading `--flood` as a live signal; the registry text supports both readings, which is why it is flagged for the owner.
   - The class 37 `delete_session` residual is reasoned only.
2. What should we have checked that we have not thought about?
   - Closed **stdout** at daemon start, found while driving class 35.
     - `mcuscoped` whose stdout is a closed pipe dies at `daemon.py:431` (`print(files, flush=True)`): rc 120, `mcuscoped-crash.log`, never starts (`probe35stdout.py`).
     - The recent "closed stdout warned for every console script" work covers a stdout that is None at start, not a pipe whose reader is gone.
     - The same bare prints follow at `daemon.py:493, 498, 142, 152`.
     - Not an error-path write, so outside class 35. It belongs with whichever class owns "closed stream at start" (class 12/13 territory), for the owner to file.

## Appendix A: class 29, every refusal site (sweep29.py, 245)

- `mcuscope/cli.py:135` die 'token must be ASCII (--token, or MCUSCOPE_TOKEN)': complies: asserted via '-p/--port is empty'
- `mcuscope/cli.py:139` die '-p/--port is empty: name a port alias, or leave -p out to span every p': complies: asserted via '-p/--port is empty'
- `mcuscope/cli.py:382` die 'error: give a device or --serial, not both': complies: asserted via 'mcu devices'
- `mcuscope/cli.py:384` die "error: give a device, or --serial SN (see 'mcu devices')": complies: asserted via 'mcu devices'
- `mcuscope/cli.py:432` die ": an alias cannot contain '/'": complies: message text found in tests
- `mcuscope/cli.py:508` die 'error: send does not read stdin; give the line itself': complies: asserted via 'does not read stdin'
- `mcuscope/cli.py:552` die 'sysrq takes exactly one character, got': complies: asserted via 'exactly one'
- `mcuscope/cli.py:557` die 'sysrq takes a printable ASCII character, got': complies: asserted via 'printable ASCII'
- `mcuscope/cli.py:1364` die 'error: --eol applies to --send; give --send too': complies: asserted via '--eol applies to --send; give --send too'
- `mcuscope/cli.py:1467` die 'at least one --expect or --forbid is required': complies: asserted via '--min-window'
- `mcuscope/cli.py:1476` die 'error: --eol applies to --send; give --send too': complies: asserted via '--eol applies to --send; give --send too'
- `mcuscope/cli.py:1611` die 'is a directory; give a file path': complies: asserted via 'is a directory'
- `mcuscope/cli.py:1621` die 'is a directory; give a file path': complies: asserted via 'is a directory'
- `mcuscope/cli.py:1627` die 'no such session:': complies: message text found in tests
- `mcuscope/cli.py:1647` die 'no such session:': complies: message text found in tests
- `mcuscope/cli.py:1687` die 'error: -o - is not stdout; omit -o for stdout, or give a file path': complies: message text found in tests
- `mcuscope/cli.py:1723` die 'exactly one of --session, --before-days, --id-from/--id-to, --all is r': complies: asserted via '--before-days'
- `mcuscope/cli.py:1727` die '--before-days must be greater than 0 (use --all to delete everything)': complies: asserted via '--before-days'
- `mcuscope/cli.py:1729` die 'is after --id-to': complies: message text found in tests
- `mcuscope/cli.py:1917` die '--csv and --json are two output formats; pick one': complies: asserted via 'two output formats'
- `mcuscope/cli.py:1923` die '--csv exports the whole window; it does not take': complies: asserted via 'csv export'
- `mcuscope/cli.py:2113` die '--csv does not follow': complies: message text found in tests
- `mcuscope/cli.py:2118` die 'error: --to cannot be combined with -f; a follow has no end': complies: asserted via 'two output formats'
- `mcuscope/cli.py:2121` die '--csv and --json are two output formats; pick one': complies: asserted via 'two output formats'
- `mcuscope/cli.py:2313` die 'error:': complies: message text found in tests
- `mcuscope/cli.py:2481` die 'error: changes requires decode': complies: asserted via 'changes requires decode'
- `mcuscope/cli.py:2483` die 'error: deadband requires changes': complies: asserted via '--deadband'
- `mcuscope/cli.py:2573` die 'no such config file:': complies: message text found in tests
- `mcuscope/cli.py:2599` die '--open cannot be combined with --json': complies: message text found in tests
- `mcuscope/cli.py:2611` die 'daemon already running': complies: asserted via 'already running'
- `mcuscope/cli.py:2721` die 'answers; something else is serving that port': complies: asserted via 'MCUSCOPE_TOKEN'
- `mcuscope/cli.py:2749` die '--open cannot be combined with --json': complies: message text found in tests
- `mcuscope/cli.py:2816` die '; removed stale pid file (was pid': complies: asserted via 'removed stale pid file'
- `mcuscope/cli.py:273` die '--save needs on or off: there is no state change to save': complies: asserted via '--save needs on or off'
- `mcuscope/cli.py:277` die "expected 'on' or 'off', got": complies: asserted via '/config/plotjuggler'
- `mcuscope/cli.py:378` die "error: --serial is blank; give the serial number from 'mcu devices'": complies: asserted via '--serial is blank'
- `mcuscope/cli.py:1063` die '--match pattern too slow (over': complies: asserted via 'match pattern'
- `mcuscope/cli.py:1193` die 'bad --match pattern:': complies: asserted via 'match pattern'
- `mcuscope/cli.py:1358` die 'error: --repeat-ms needs --send': complies: asserted via '--repeat-ms'
- `mcuscope/cli.py:1360` die 'error: --repeat-ms must be between': complies: message text found in tests
- `mcuscope/cli.py:1471` die 'error: --min-window needs a live window (give --timeout too)': complies: asserted via '--min-window'
- `mcuscope/cli.py:1473` die 'error: --min-window cannot exceed --timeout': complies: asserted via '--eol applies to --send; give --send too'
- `mcuscope/cli.py:1616` die '--bundle writes a zip, not a .db': complies: message text found in tests
- `mcuscope/cli.py:1877` die 'cannot write': complies: message text found in tests
- `mcuscope/cli.py:2718` die 'another daemon is already serving at': complies: message text found in tests
- `mcuscope/cli.py:2789` die 'no daemon is running at': complies: message text found in tests
- `mcuscope/cli.py:2806` die 'was unreadable or corrupt, and no daemon is responding at': complies: asserted via 'left it in place'
- `mcuscope/cli.py:2812` die 'is still running; left its record': complies: asserted via 'removed stale pid file'
- `mcuscope/cli.py:1290` die 'daemon unreachable at': complies: message text found in tests
- `mcuscope/cli.py:1292` die 'daemon unreachable at': complies: message text found in tests
- `mcuscope/cli.py:1310` die 'stream closed by daemon': complies: message text found in tests
- `mcuscope/cli.py:1315` die 'websocket refused by daemon: HTTP': complies: asserted via 'HTTP {status}' (test_cli_follow_frames.py:65)
- `mcuscope/cli.py:1317` die 'websocket error:': VIOLATES R29-3
- `mcuscope/cli.py:1321` die 'malformed frame from daemon:': complies: asserted via 'malformed frame' (test_cli_follow_frames.py)
- `mcuscope/cli.py:1323` die 'unexpected row shape from daemon: missing': VIOLATES R29-3
- `mcuscope/cli.py:1980` die 'cannot write': complies: message text found in tests
- `mcuscope/cli.py:1288` die 'accepted the connection but stopped answering:': complies: asserted via 'accepted the connection but stopped answering'
- `mcuscope/cli.py:1304` die 'stream refused by daemon:': complies: asserted via 'not authorised'
- `mcuscope/cli.py:1308` die "error: too many subscribers (the daemon's subscriber cap is reached); ": complies: asserted via 'stream closed by daemon'
- `mcuscope/cli.py:2695` die 'mcuscoped is still building index': complies: message text found in tests
- `mcuscope/cli.py:2237` die 'daemon unreachable at': complies: message text found in tests
- `mcuscope/cli.py:2231` die 'accepted the request but stopped answering for': complies: message text found in tests
- `mcuscope/cli.py:2235` die 'error: the daemon at': complies: message text found in tests
- `mcuscope/cli_argv.py:136` die 'needs a value': complies: message text found in tests
- `mcuscope/cli_client.py:83` die 'bad daemon url': complies: message text found in tests
- `mcuscope/cli_client.py:214` die 'error:': complies: message text found in tests
- `mcuscope/cli_client.py:97` die 'daemon unreachable at': complies: message text found in tests
- `mcuscope/cli_client.py:101` die 'accepted the request but stopped answering:': complies: asserted via 'accepted the request but stopped answering'
- `mcuscope/cli_client.py:107` die 'daemon unreachable at': complies: message text found in tests
- `mcuscope/cli_client.py:113` die 'cannot send request to': complies: message text found in tests
- `mcuscope/cli_client.py:187` die '(it would be dropped silently); it needs daemon': complies: asserted via 'needs daemon' (test_cli_version_gate.py)
- `mcuscope/cli_client.py:199` die 'error:': complies: message text found in tests
- `mcuscope/cli_client.py:204` die '; it needs daemon': complies: message text found in tests
- `mcuscope/cli_client.py:225` die 'malformed response from': complies: asserted via 'malformed response' (test_cli.py, test_e2e.py)
- `mcuscope/cli_client.py:262` die 'cannot write': complies: message text found in tests
- `mcuscope/cli_client.py:294` die 'cannot write': complies: message text found in tests
- `mcuscope/cli_daemonctl.py:294` die 's and could not be stopped; it is still running as pid': complies: asserted via 'did not come up'
- `mcuscope/cli_daemonctl.py:209` die 'refused the request (HTTP': complies: message text found in tests
- `mcuscope/cli_daemonctl.py:275` die 'mcuscoped exited with status': complies: asserted via 'exited with status' (test_cli_ux.py)
- `mcuscope/cli_daemonctl.py:290` die 's; stopped it (raise --timeout if it just needs longer)': complies: asserted via 'did not come up'
- `mcuscope/cli_daemonctl.py:381` die '; the daemon runs under a different pid - stop it from the process lis': complies: asserted via 'still answering'
- `mcuscope/cli_daemonctl.py:47` die 'cannot use the daemon pid file:': complies: asserted via 'unreachable'
- `mcuscope/cli_daemonctl.py:357` die ', so no process was signalled; stop it where it runs': complies: asserted via 'no process was signalled'
- `mcuscope/cli_daemonctl.py:367` die 'did not exit within': complies: asserted via 'DAEMON_STOP_GRACE_S'
- `mcuscope/cli_daemonctl.py:365` die 'could not stop pid': complies: asserted via 'DAEMON_STOP_GRACE_S'
- `mcuscope/cli_output.py:123` die 'unexpected response from daemon:': complies: message text found in tests
- `mcuscope/cli_output.py:130` die 'unexpected response from daemon:': complies: message text found in tests
- `mcuscope/cli_output.py:147` die 'unexpected response from daemon:': complies: message text found in tests
- `mcuscope/cli_output.py:524` die 'refusing to prompt for confirmation: stdin is not a terminal; pass -y ': complies: asserted via 'stdin is not a terminal; pass -y'
- `mcuscope/cli_output.py:534` die 'cancelled': complies: message text found in tests
- `mcuscope/config.py:386` ConfigError 'config key [[ports]] is not an array of tables; fix the file by hand': complies: asserted via 'fix the file by hand'
- `mcuscope/config.py:617` ConfigError '] is not a table; fix the file by hand': complies: asserted via 'fix the file by hand'
- `mcuscope/config.py:190` ConfigError ': invalid TOML:': complies: asserted via 'invalid TOML'
- `mcuscope/config.py:195` ConfigError ': cannot read:': complies: asserted via 'cannot read'
- `mcuscope/config.py:197` ConfigError ': invalid value:': complies: message text found in tests
- `mcuscope/config.py:381` ConfigError '] is not a table; fix the file by hand': complies: asserted via 'fix the file by hand'
- `mcuscope/config.py:559` ConfigError ': cannot read:': complies: asserted via 'cannot read'
- `mcuscope/config.py:569` ConfigError ': cannot rewrite invalid TOML:': complies: asserted via 'invalid TOML'
- `mcuscope/daemon.py:420` ConfigError 'no such config file:': complies: message text found in tests
- `mcuscope/daemon.py:108` ConfigError '--host': complies: message text found in tests
- `mcuscope/daemon.py:118` ConfigError '--plotjuggler:': complies: asserted via '--plotjuggler'
- `mcuscope/lockfile.py:134` LockError '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/serial_link.py:193` PortError '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/serial_link.py:464` PortError 'detached': complies: message text found in tests
- `mcuscope/serial_link.py:733` PortError 'disconnected': complies: message text found in tests
- `mcuscope/serial_link.py:1113` PortError 'line must not contain embedded newlines': complies: asserted via 'embedded newlines'
- `mcuscope/serial_link.py:1115` PortError 'line exceeds': complies: asserted via 'limit' (test_security.py:186)
- `mcuscope/serial_link.py:1120` PortError 'unknown line ending:': complies: asserted via 'unknown line ending'
- `mcuscope/serial_link.py:1381` PortError 'too many ports attached (max': complies, type only: pytest.raises(PortError) + alias absent (test_reconnect.py:1216)
- `mcuscope/serial_link.py:1383` PortError 'detached': complies: message text found in tests
- `mcuscope/serial_link.py:1127` PortError 'line must be 7-bit ASCII': complies: asserted via 'ASCII' (test_eol.py:287)
- `mcuscope/serial_link.py:1171` PortError 'break failed:': VIOLATES R29-2 (mutation-driven)
- `mcuscope/serial_link.py:1402` PortError 'detached': complies: message text found in tests
- `mcuscope/serial_link.py:1413` PortError 'too many ports attached (max': complies, type only: same test as :1381
- `mcuscope/serial_link.py:1080` PortError 'is not connected': complies: message text found in tests
- `mcuscope/serial_link.py:1095` PortError 'write failed:': complies: message text found in tests
- `mcuscope/serial_link.py:1167` PortError 'is not connected': complies: message text found in tests
- `mcuscope/serial_link.py:1169` PortError 'transport cannot send a break': complies: asserted via 'cannot send a break'
- `mcuscope/serial_link.py:1202` PortError '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/serial_link.py:1407` PortError 'no such port:': complies: message text found in tests
- `mcuscope/serial_link.py:1409` PortError 'was re-attached during the reconnect': complies: message text found in tests
- `mcuscope/serial_link.py:1411` PortError 'was disconnected during the reconnect': complies: message text found in tests
- `mcuscope/serial_link.py:966` PortError 'response received but storing it failed:': complies, type only: pytest.raises(PortError) (test_reconnect.py:1132)
- `mcuscope/server.py:1010` JSONResponse '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1023` _bad_request 'no such port:': complies: message text found in tests
- `mcuscope/server.py:1042` PortError 'port is ambiguous; specify one of:': complies: message text found in tests
- `mcuscope/server.py:2886` JSONResponse 'client disconnected': complies: asserted via 'disconnect'
- `mcuscope/server.py:572` JSONResponse '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:579` JSONResponse '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1041` PortError 'no ports attached': complies: message text found in tests
- `mcuscope/server.py:1227` JSONResponse 'config editing from the network requires an access token; restart mcus': complies: asserted via 'MCUSCOPED_TOKEN'
- `mcuscope/server.py:1238` JSONResponse 'config save failed:': complies: message text found in tests
- `mcuscope/server.py:2340` MatchBudgetExceeded 'match pattern exceeded the matching time budget; simplify the regex': complies: asserted via 'match pattern'
- `mcuscope/server.py:2419` CaptureStopped '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:2615` _bad_request 'only since="now" is supported': complies: asserted via 'eol applies to send; set send too'
- `mcuscope/server.py:2617` _bad_request 'eol applies to send; set send too': complies: message text found in tests
- `mcuscope/server.py:2626` _bad_request 'match regex too long (max': complies: message text found in tests
- `mcuscope/server.py:3017` _bad_request 'at least one expect or forbid pattern is required': complies, status only: {} -> 400 (test_assert.py:187)
- `mcuscope/server.py:3021` _bad_request 'expect and forbid patterns in total': complies: asserted via 'total' (test_assert.py:861)
- `mcuscope/server.py:3042` _bad_request 'eol applies to send; set send too': complies: message text found in tests
- `mcuscope/server.py:3045` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:3048` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:3345` _bad_request 'match regex too long (max': complies: message text found in tests
- `mcuscope/server.py:3361` _bad_request 'until_ts is before since_ts': complies: message text found in tests
- `mcuscope/server.py:1035` PortError 'no such port:': complies: message text found in tests
- `mcuscope/server.py:1123` JSONResponse "shutdown is a local operation; run mcu on the daemon's machine": complies: asserted via 'shutdown is a local operation'
- `mcuscope/server.py:1132` _bad_request 'this server does not accept shutdown requests': complies: 400 plus still serving (test_e2e.py:57-65)
- `mcuscope/server.py:1156` _bad_request 'attach requires device or serial_number': complies: asserted via 'serial_number'
- `mcuscope/server.py:1158` _bad_request 'invalid': complies: message text found in tests
- `mcuscope/server.py:1174` _bad_request 'no such port:': complies: message text found in tests
- `mcuscope/server.py:1185` _bad_request 'no such port:': complies: message text found in tests
- `mcuscope/server.py:1199` _bad_request 'no such port:': complies: message text found in tests
- `mcuscope/server.py:1237` JSONResponse '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1314` _bad_request 'invalid db_path': complies: asserted via 'max_db_byte'
- `mcuscope/server.py:1316` _bad_request 'max_db_bytes must be 0 (no cap) or at least': complies: asserted via 'max_db_byte'
- `mcuscope/server.py:1539` _bad_request 'no such session:': complies: message text found in tests
- `mcuscope/server.py:1566` _bad_request 'no such session:': complies: message text found in tests
- `mcuscope/server.py:1608` _bad_request 'no such session:': complies: message text found in tests
- `mcuscope/server.py:1749` _bad_request 'exactly one of session, before_ts, id_from/id_to, all is required': complies: asserted via 'exactly one'
- `mcuscope/server.py:1886` _bad_request "format must be 'text', 'jsonl' or 'csv'": complies: message text found in tests
- `mcuscope/server.py:1933` _bad_request "format must be 'json' or 'csv'": complies: message text found in tests
- `mcuscope/server.py:2064` _bad_request 'names is required': complies: message text found in tests
- `mcuscope/server.py:2068` _bad_request 'names lists': complies: message text found in tests
- `mcuscope/server.py:2070` _bad_request "format must be 'long' or 'wide'": complies: message text found in tests
- `mcuscope/server.py:2075` _bad_request 'changes requires decode': complies: message text found in tests
- `mcuscope/server.py:2077` _bad_request 'deadband requires changes': complies: message text found in tests
- `mcuscope/server.py:2106` _bad_request 'no such plot channel:': complies: message text found in tests
- `mcuscope/server.py:2176` _bad_request 'invalid port:': VIOLATES R29-1 (mutation-driven)
- `mcuscope/server.py:2520` CaptureStopped '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:2624` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:2630` _bad_request 'bad match regex:': complies: asserted via 'bad match regex'
- `mcuscope/server.py:2636` JSONResponse '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:2895` JSONResponse '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:2897` JSONResponse '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:3026` _bad_request 'min_window_ms needs a live window (set timeout_ms too)': complies: asserted via 'live window'
- `mcuscope/server.py:3028` _bad_request 'min_window_ms cannot exceed timeout_ms': complies: asserted via 'min_window_ms'
- `mcuscope/server.py:3034` _bad_request 'session needs a retrospective window (leave timeout_ms at 0)': complies: asserted via 'timeout_ms'
- `mcuscope/server.py:3036` _bad_request 'last_ms needs a retrospective window (leave timeout_ms at 0)': complies: asserted via 'live window'
- `mcuscope/server.py:3040` _bad_request 'send needs a live window (set timeout_ms too)': complies: asserted via 'eol applies to send; set send too'
- `mcuscope/server.py:3138` JSONResponse '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:3349` _bad_request 'bad match regex:': complies: asserted via 'bad match regex'
- `mcuscope/server.py:3359` _bad_request 'must be a finite number': complies: message text found in tests
- `mcuscope/server.py:1164` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1191` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1248` JSONResponse '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1296` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1303` _save_error '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1326` _save_error '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1356` _save_error '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1375` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1383` _save_error '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1410` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1426` _bad_request 'duplicate alias:': complies: asserted via 'duplicate alias' (test_config_api.py)
- `mcuscope/server.py:1431` _bad_request ': device or serial_number required': complies: asserted via 'serial_number'
- `mcuscope/server.py:1435` _bad_request ': invalid': complies: message text found in tests
- `mcuscope/server.py:1463` _save_error '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1507` _bad_request 'no session is running': complies: message text found in tests
- `mcuscope/server.py:1516` _bad_request 'no session is running': complies: message text found in tests
- `mcuscope/server.py:1584` _bad_request 'export failed:': complies: message text found in tests
- `mcuscope/server.py:1620` _bad_request 'no such session:': complies: message text found in tests
- `mcuscope/server.py:1671` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1721` _bad_request 'export failed:': complies: message text found in tests
- `mcuscope/server.py:1755` _bad_request 'no such session:': complies: message text found in tests
- `mcuscope/server.py:1801` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1805` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1813` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1817` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1825` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1832` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1834` JSONResponse '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1865` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1904` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:2081` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:2099` _bad_request 'wide export requires all channels to share one stream': complies: asserted via 'one stream'
- `mcuscope/server.py:2127` _bad_request 'renders as a label (an enum or a decoded bit lane)': complies: message text found in tests
- `mcuscope/server.py:2154` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:2156` JSONResponse '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:2163` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:2165` JSONResponse '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:2364` MatchBudgetExceeded 'match pattern exceeded the matching time budget; simplify the regex': complies: asserted via 'match pattern'
- `mcuscope/server.py:2539` CaptureStopped '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:2583` PortError 'no such port:': complies: message text found in tests
- `mcuscope/server.py:2612` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:3133` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1439` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1760` _bad_request 'before_ts must be a finite number': complies: message text found in tests
- `mcuscope/server.py:1765` _bad_request 'before_ts is in the future; use all: true to delete the whole capture': complies: asserted via 'whole capture'
- `mcuscope/server.py:1941` _bad_request 'empty can id in list': complies: message text found in tests
- `mcuscope/server.py:1947` _bad_request 'can id out of range:': complies: message text found in tests
- `mcuscope/server.py:2121` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:2975` MatchBudgetExceeded 'match pattern exceeded the matching time budget; simplify the regex': complies: asserted via 'match pattern'
- `mcuscope/server.py:3153` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:1945` _bad_request 'bad can id:': complies: message text found in tests
- `mcuscope/server.py:2650` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/server.py:2668` _bad_request '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/store.py:510` MatchBudgetExceeded 'match pattern exceeded the matching time budget; simplify the regex': complies: asserted via 'match pattern'
- `mcuscope/store.py:506` MatchBudgetExceeded 's budget before covering the window: the window is too large, narrow i': complies: asserted via 'session, last_ms, since_id'
- `mcuscope/store.py:1207` StoreError 'store writer is not running': complies: message text found in tests
- `mcuscope/store.py:1284` StoreError '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/store.py:1286` StoreError 'too many subscribers (max': complies: message text found in tests
- `mcuscope/store.py:843` StoreError '<no literal>': exempt: message is a variable (str(exc) or a helper's text) whose origin is an enumerated raise site
- `mcuscope/store.py:900` StoreError 'store writer exited': complies: message text found in tests
- `mcuscope/store.py:1003` StoreError 'store writer exited': complies: message text found in tests
- `mcuscope/store.py:979` StoreError 'commit failed:': complies: asserted via 'commit failed'
- `mcuscope/store.py:948` StoreError 'insert failed:': complies (reasoned from the test name only, not read): test_writer_survives_bad_insert (test_store_writer.py:97)

## Appendix B: class 37, every async def (ast37.py, 130)

- `mcuscope/store.py:593` start (awaits=0 locks=[] writes=['self._capture_id', 'self._conn', ): exempt: no await (awaits=0), runs once before any caller
- `mcuscope/store.py:729` _initial_sweep (awaits=2 locks=[] writes=[]): complies: both sweeps take _sweep_lock inside
- `mcuscope/store.py:736` stop (awaits=3 locks=[] writes=['self._conn', 'self._retention_tas): complies: stops tasks it owns, then the writer; no read-then-act on shared state
- `mcuscope/store.py:872` _writer (awaits=2 locks=[] writes=['self._hold_off_until', 'self._ing): complies: sole writer task; the hold/rate fields are its own
- `mcuscope/store.py:1235` submit_line (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:1256` add_line (awaits=2 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:1260` drain_writes (awaits=2 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:1441` list_sessions_safe (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:1450` start_session (awaits=5 locks=['self._session_lock'] writes=[]): complies: _session_lock; drain barrier before the _next_id sample
- `mcuscope/store.py:1476` _open_session_locked (awaits=1 locks=[] writes=[]): complies: caller holds _session_lock
- `mcuscope/store.py:1495` stop_session (awaits=3 locks=['self._session_lock'] writes=[]): complies: _session_lock
- `mcuscope/store.py:1512` _stop_session_locked (awaits=1 locks=[] writes=[]): complies: caller holds _session_lock
- `mcuscope/store.py:2070` _offload (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:2090` id_ceiling_safe (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:2094` count_lines_safe (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:2135` query_lines_safe (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:2168` query_can_frames_safe (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:2314` query_plot_channels_safe (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:2324` plot_ports_safe (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:2329` _settled_plot_summary (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:2447` _rebuild_plot_summary (awaits=2 locks=['self._plot_lock'] writes=['scanned[]', 'sel): complies: _plot_lock
- `mcuscope/store.py:2544` query_plot_series_safe (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:2575` plot_streams_safe (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:2625` export_sids_safe (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:2659` first_export_line_id_safe (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:2734` open_plot_export (awaits=0 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:2797` open_lines_export (awaits=0 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:2803` open_can_export (awaits=0 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:2874` delete_range (awaits=1 locks=[] writes=[]): complies: _delete_chunks holds _sweep_lock for the whole loop
- `mcuscope/store.py:2894` delete_before_ts (awaits=1 locks=[] writes=[]): complies: same (_delete_chunks)
- `mcuscope/store.py:2926` before_ts_span_safe (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/store.py:2930` _delete_chunks (awaits=2 locks=['self._sweep_lock'] writes=[]): complies: _sweep_lock
- `mcuscope/store.py:2978` _trim_oldest (awaits=1 locks=[] writes=[]): complies: only caller is _sweep_size_locked (under _sweep_lock)
- `mcuscope/store.py:2989` _sweep_size_async (awaits=2 locks=['self._sweep_lock'] writes=[]): complies: _sweep_lock
- `mcuscope/store.py:3006` _sweep_size_reported (awaits=2 locks=[] writes=[]): complies: sweep under _sweep_lock, then one append
- `mcuscope/store.py:3018` _sweep_size_locked (awaits=2 locks=[] writes=['self.lines_trimmed']): complies: callers hold _sweep_lock; want computed and applied under it
- `mcuscope/store.py:3079` _sweep_retention_async (awaits=2 locks=['self._sweep_lock'] writes=[]): complies: _sweep_lock
- `mcuscope/store.py:3090` _sweep_retention_locked (awaits=1 locks=[] writes=[]): complies: caller holds _sweep_lock; a stale floor_id only protects more
- `mcuscope/store.py:3101` _retention_loop (awaits=2 locks=[] writes=[]): complies: delegates to sweep_tick
- `mcuscope/store.py:3123` sweep_tick (awaits=2 locks=[] writes=[]): complies: both sweeps lock inside
- `mcuscope/serial_link.py:85` learn_stored_plot_defs (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/serial_link.py:401` hold (awaits=1 locks=[] writes=['self.disconnect_reason', 'self.he): complies: called under PortManager._lock (PortManager.hold)
- `mcuscope/serial_link.py:408` stop (awaits=5 locks=[] writes=['self._rx_lines.clear()', 'self.rx): complies: stop event set first; every later read is of state the stopped reader no longer writes
- `mcuscope/serial_link.py:693` _identify (awaits=1 locks=[] writes=['self.target']): complies: a reconnect needs a retry wait (>= BACKOFF_MIN) between the ping's answer and its continuation, so a stale name cannot be written over the new connect's None
- `mcuscope/serial_link.py:835` _consume (awaits=2 locks=[] writes=['self._queue_overflow.clear()', 's): complies: sole consumer task
- `mcuscope/serial_link.py:857` _store_rx_batch (awaits=2 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/serial_link.py:904` _submit_rx_line (awaits=1 locks=[] writes=['self.lines_rx']): complies: counter increment, no read-then-act
- `mcuscope/serial_link.py:955` _settle_rx_line (awaits=1 locks=[] writes=['self._pending.pop()', 'self._unst): complies: pops its own seq's pending entry
- `mcuscope/serial_link.py:976` _store_rx_line (awaits=2 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/serial_link.py:998` prime_plot_defs (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/serial_link.py:1012` _store_sys (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/serial_link.py:1129` send_raw (awaits=3 locks=['self._raw_lock'] writes=['self.lines_tx']): complies: _raw_lock
- `mcuscope/serial_link.py:1173` send_break (awaits=3 locks=['self._raw_lock'] writes=[]): complies: _raw_lock
- `mcuscope/serial_link.py:1183` send_command (awaits=4 locks=['self._cmd_lock'] writes=['self._pending', '): complies: _cmd_lock; seq/pending under it
- `mcuscope/serial_link.py:1357` attach (awaits=3 locks=['self._lock'] writes=['port._seq', 'port._wr): complies: re-checks _closed and `replaces` inside _lock after the unlocked prime
- `mcuscope/serial_link.py:1430` detach (awaits=2 locks=['self._lock'] writes=[]): complies: _lock
- `mcuscope/serial_link.py:1434` hold (awaits=2 locks=['self._lock'] writes=[]): complies: called under PortManager._lock (PortManager.hold)
- `mcuscope/serial_link.py:1442` _detach_locked (awaits=1 locks=[] writes=['self._carried', 'self._carried.po): complies: caller holds _lock
- `mcuscope/serial_link.py:1474` stop_all (awaits=1 locks=[] writes=['self._closed']): complies: sets _closed first; attach re-checks it under _lock
- `mcuscope/server.py:2317` _pull_first (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:2381` _live_scan (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:2402` _until_stopped (awaits=2 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:2560` _repeat_send (awaits=2 locks=[] writes=['tally.failures', 'tally.sends']): complies: tally is per-call, owned by one handler
- `mcuscope/server.py:2600` _do_wait (awaits=5 locks=[] writes=['tally.sends']): complies: tally per call
- `mcuscope/server.py:2859` _run_export (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:2879` _client_left (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:2889` _admit_and_build (awaits=2 locks=[] writes=['state.export_builds', 'state.expo): complies: no await between the capacity check and the claim (commented at the site)
- `mcuscope/server.py:2996` _do_assert (awaits=9 locks=[] writes=['expect_hits[]', 'forbid_hits[]', ): complies: hit lists are per call
- `mcuscope/server.py:3256` _resolve_window (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:3303` _named_by_ids (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:3719` _plot_export_defs (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:422` lifespan (awaits=14 locks=[] writes=['app.state.config', 'app.state.co): exempt: startup/shutdown, runs before/after every handler
- `mcuscope/server.py:556` _http_error (awaits=0 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:560` _validation_error (awaits=0 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:575` _unhandled_error (awaits=0 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:685` __call__ (awaits=4 locks=[] writes=[]): complies: _locked_out check and _register_failure with no await between
- `mcuscope/server.py:705` _deny (awaits=3 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:812` __call__ (awaits=3 locks=[] writes=['self._fails.pop()']): complies: _locked_out check and _register_failure with no await between
- `mcuscope/server.py:835` _deny (awaits=3 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:853` _deny_rate_limited (awaits=3 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:889` __call__ (awaits=2 locks=[] writes=[]): complies: _locked_out check and _register_failure with no await between
- `mcuscope/server.py:908` get_response (awaits=1 locks=[] writes=['response.headers[]']): exempt: per-response headers
- `mcuscope/server.py:954` _root (awaits=0 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1052` status (awaits=0 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1114` shutdown (awaits=0 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1140` get_ports (awaits=0 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1149` attach_port (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1171` detach_port (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1178` reconnect_port (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1195` disconnect_port (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1203` devices (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1241` get_config (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1290` put_config_server (awaits=2 locks=['request.app.state.config_write_lock'] write): complies: config_write_lock held across the read-modify-write save
- `mcuscope/server.py:1309` put_config_storage (awaits=3 locks=['request.app.state.config_write_lock'] write): complies: config_write_lock held across the read-modify-write save
- `mcuscope/server.py:1347` put_config_update (awaits=2 locks=['request.app.state.config_write_lock'] write): complies: config_write_lock held across the read-modify-write save
- `mcuscope/server.py:1365` put_config_plotjuggler (awaits=3 locks=['request.app.state.config_write_lock'] write): complies: config_write_lock held across the read-modify-write save
- `mcuscope/server.py:1389` get_plotjuggler (awaits=0 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1394` put_plotjuggler (awaits=2 locks=['request.app.state.config_write_lock'] write): complies: config_write_lock held across the read-modify-write save
- `mcuscope/server.py:1414` put_config_ports (awaits=3 locks=['request.app.state.config_write_lock'] write): complies: config_write_lock held across the read-modify-write save
- `mcuscope/server.py:1469` list_sessions (awaits=2 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1495` start_session (awaits=1 locks=[] writes=[]): complies: delegates to store.start_session (_session_lock)
- `mcuscope/server.py:1499` stop_session (awaits=2 locks=['request.app.state.session_stop_lock'] write): complies: session_stop_lock across the check, the stop and the reopen
- `mcuscope/server.py:1520` delete_session (awaits=2 locks=[] writes=[]): complies with residual, see note: reads session/end_id, awaits delete_range, then deletes the label and may reopen auto without session_stop_lock
- `mcuscope/server.py:1552` export_session (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1596` bundle_session (awaits=2 locks=['store._sweep_lock'] writes=[]): complies: holds store._sweep_lock across the build, so no bulk delete moves the span
- `mcuscope/server.py:1625` _build_bundle (awaits=8 locks=[] writes=['stems[]']): exempt: per-call locals
- `mcuscope/server.py:1734` purge (awaits=4 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1797` send (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1809` send_break (awaits=1 locks=[] writes=[]): complies: _raw_lock
- `mcuscope/server.py:1821` cmd (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1837` lines (awaits=2 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1869` lines_export (awaits=4 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1918` can_frames (awaits=4 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:1974` plot_channels (awaits=3 locks=[] writes=['by_port[]', 'detached_meta[]']): exempt: per-call locals
- `mcuscope/server.py:2023` plot_series (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:2045` plot_export (awaits=7 locks=[] writes=['win.scope[]']): exempt: per-call window
- `mcuscope/server.py:2147` wait (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:2159` assert_ (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:2168` marker (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:2190` ws (awaits=10 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:2302` __call__ (awaits=1 locks=[] writes=[]): complies: _locked_out check and _register_failure with no await between
- `mcuscope/server.py:2499` until_stopped (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:2503` next_batch (awaits=1 locks=[] writes=['self._dropped', 'self._stopped']): complies: per-watch fields, one watch per handler
- `mcuscope/server.py:2954` __call__ (awaits=1 locks=[] writes=[]): complies: _locked_out check and _register_failure with no await between
- `mcuscope/server.py:891` send_unframed (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:2212` pump (awaits=2 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/server.py:2264` watch (awaits=1 locks=[] writes=[]): exempt: writes no shared state (read-only, single append, or delegates to a locked method)
- `mcuscope/update_check.py:248` _check_and_hold (awaits=1 locks=[] writes=['self._retry_after']): complies: _retry_after is written only by this task; maybe_check refuses a second task while one runs
- `mcuscope/update_check.py:252` aclose (awaits=1 locks=[] writes=['self._task']): exempt: shutdown only
- `mcuscope/update_check.py:261` check_once (awaits=4 locks=['httpx.AsyncClient(timeout=HTTP_TIMEOUT_S, f): complies: fields written after the await, no read-then-act
