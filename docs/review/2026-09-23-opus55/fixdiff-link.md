# Fix-diff leg: link, protocol, daemon, sim, config, pjstream

HEAD: `b994076c88c875ad43c30789cda6039d147b7abe`

Scratch copy and probes: `~/tt-data/mcuscope-2026-09-24/fixdiff/` (`link-copy/` is the mutated tree).
A probe confirmed the copy was the tree being imported.

## Findings

### F1. Windows: closing a foreground daemon's console still kills it with no graceful stop (medium, reasoned)

- `daemon.py:349-376` (LIFECYCLE-6) gives POSIX SIGHUP a graceful stop.
  The Windows counterpart is closing the console window, and it did not get the same fix.
- `_stdio.py:109`: when a console exists at startup (`mcuscoped` typed in cmd or PowerShell), `_ensure_console` returns early and `install_console_ctrl_handler` is never installed.
- In that case the CRT maps CTRL_CLOSE_EVENT to SIGBREAK. It sets Python's flag and returns, and Windows ends the process as soon as the handler returns. `_stdio.py:76` says this itself.
- Failure scenario: the user closes the cmd window running `mcuscoped`.
  - There is no `daemon stop` row, the session stays open until the next start, and the pid record stays behind.
  - On Linux, closing the terminal now does all of these cleanly.
- Confirmed: reasoned from the Win32 contract and the code. There is no Windows machine here. On Linux the POSIX half was driven with a real pty close, not just a bare SIGHUP (`probe_pty_hup.py`), and ended with exit -15, the record removed, and rows `['daemon start', 'daemon stop']`.
- Fix: on Windows, have the daemon always install the CTRL_CLOSE hold (the 4.5 s `interrupt_main` + sleep branch), not only after a late attach. Then run the Windows leg to check it.

### F2. A reconnect still overwrites an alias that was re-attached while it primed (low, driven)

- `serial_link.py:1398` checks only that the alias still exists (`require_existing and not replacing`). It does not check that the alias still points to the same port object the route read at `server.py:1162`.
- Failure scenario: a reconnect of `r` is priming, and during the prime `POST /ports` re-attaches `r` to a new device (attach replaces, so no detach is needed).
  - The reconnect takes the lock last and puts `r` back on the old device.
  - `hold()` has the same problem: a disconnect that lands during the prime is undone.
- Confirmed: driven (`probe_reattach.py`). Detaching and re-attaching `r` to `/dev/new-device` during the reconnect's prime printed `reconnect succeeded; alias r now on /dev/old-device`.
- Fix: pass the expected object instead of a bool (for example `replaces: SerialPort | None`). Under the lock, raise `PortError` when `self._ports.get(alias) is not replaces`, and add a test for the re-attach race.

### F3. HEALTH-5 half-done: the loader still loads ports that `PUT /config/ports` refuses (low, driven)

- `config.py:461` now adds `validate_device`. The PUT has four more refusals the loader does not apply:
  - `device` longer than 512 characters (422);
  - `serial_number` longer than 128 (422);
  - a character below 0x20 in either field (400);
  - a duplicate alias (400).
- Failure scenario: a hand-edited config with any of these loads without a warning. The settings dialog then cannot save any port, which is exactly the symptom HEALTH-5 fixed for schemes.
- Confirmed: driven (`probe_saveback.py`, `probe_saveback2.py`). All five cases loaded with 0 warnings, and GET-then-PUT answered:
  - `422 ports.0.serial_number: ... at most 128`;
  - `422 ports.0.device: ... at most 512`;
  - `400 port odd: invalid device`;
  - `400 port odd: invalid serial_number`;
  - `400 duplicate alias: odd`.
- Fix: warn and skip in the loader using the same constants and checks the PUT model uses (share them, class 19). Extend `test_the_loaded_ports_save_back` with these four cases.

### F4. CAPTURE-6's slice `batch[i:]` has no test (test gap, driven)

- `serial_link.py:876`: `test_lines_in_a_consumer_batch_cancelled_by_detach_are_counted` (`test_serial_link_rx_framing.py:185`) only cancels at index 0, where `batch[i:] == batch`.
- Mutant `extendleft(reversed(batch))` survives: all 9 tests in the file pass.
  - That mutant counts lines the store already took as dropped, and the detach row names them as "not yet stored".
- Confirmed: with a store that accepts 10 of 500 lines and then blocks (`probe_midbatch.py`):
  - the real code gives `rx_dropped 490`;
  - the mutant gives `rx_dropped 500` and a row reading `dropped 500 received lines not yet stored`.
- Fix: make the test's `_StalledStore` accept the first K `submit_line_nowait` calls, then assert `rx_dropped == n - K` and the matching row text.

### Nits

- `serial_link.py:1-8`: the docstring says `_link` is thread-shared "under `_write_lock`". The reader publishes it without the lock (`:588`), and `stop()` reads it without the lock (`:409`). Say that only writes, close and health are under the lock.
- `tests/test_config_api.py:440`: the docstring still names `PortManager.resolve`, which was deleted this round. This is the only remaining mention outside the review docs, and no code, JS or test caller is left.
- `serial_link.py:458`: the stop row says "at detach" for `hold()` and for a reconnect's replace too (fix-link doubt 4). Suggest "at stop", or pass the cause in.

## Doubts verified

### fix-link.md

- **Pool size (2 x MAX_PORTS, cancelled writes hold workers): holds, but the impact is bounded.**
  - Driven with `probe_pool.py`: 80 sends against a port whose writes stall 0.5 s, each cancelled after 20 ms, left the pool held by that port.
  - A healthy port's send still completed in 0.35 s. Cancelled work that has not started is dropped from the queue, so the wait is at most one WRITE_TIMEOUT.
  - In the server, cancellation comes only from shutdown (`_until_stopped`) and the `/wait --repeat` repeater's end, one write each.
- **Detach-partial test drives only one order: holds, and the other order is safe by construction.**
  - `stop()` sets `_stop` as its first statement, and `_on_disconnect` takes nothing once `_stop` is set, so `stop()` always owns the partial.
  - A disconnect that ran before `stop()` began filed its own row.
- **`lines_rx` and `rx_dropped` both count a cancelled line: holds.** SPEC defines `lines_rx` nowhere (only its lines/s use at SPEC 9), so this is no contract breach. It matches `_drop_rx_line`.
- **"at detach" wording for hold: holds.** See the nits.
- **Not checked on Windows: partly closed.**
  - The link changes have no platform branch. The pool, the stamp under the lock, and the framing are the same on both.
  - The tx stamp tests use non-strict `<=`, so they survive the coarse `time.time()` of Python < 3.13 on Windows.
  - Not driven on a real COM port.

### fix-daemon.md

- **LIFECYCLE-5, the other bind order: holds (reasoned).**
  - When the record holder loses the bind, it writes the shared log (`failed to start`), and the winner logs under its own pid.
  - No live daemon's log is overwritten, but the shared name then describes a dead start (the class 7 residual).
- **Parent-pid exemption on Windows: holds as reasoned, not driven.**
  - `mcu daemon start` spawns `sys.executable -m mcuscope.daemon` (`cli.py:2558`). In a venv that is the `Scripts\python.exe` redirector, whose child is the interpreter, so `getppid()` is the recorded pid.
  - This agrees with `pidfile.py`'s docstring. A runtime whose venv `python.exe` is the real interpreter records the daemon's own pid instead, which `getpid()` covers.
- **The test failure was another batch's in-flight edit: refuted as a current concern.** All slice test files pass at HEAD (see below).

## Checked, nothing found

- Tests: every slice test file, run one at a time in the copy (runs logged in `slice_runs.log`), all pass.
  - New files: rx_framing 9, tx 6, attach 1, rx_tokens 9, hangup/race 5, config loader 4, protocol_tokenizer 30, sim_error_codes 20.
  - Existing files: reconnect 51 (1 skip), port_health 27, protocol 223, sim 58, sim_pty 3, eol 62, e2e 39, plotjuggler 46, plot_grammar_fixture 84, pane_regex_dialect 82.
  - `ruff check` is clean on the slice.
- `PortManager.resolve` has no remaining caller in `host/mcuscope`, `webui/*.js`, `host/tests` (py and mjs) or `tools/`.
- Tokenizer consumers after `dd39ee1` all use U+0020 runs only:
  - `serial_link` (`_response_seq`, event dispatch, `_identify`);
  - every `protocol` parser;
  - sim command parsing (`parse_command` uses `split_tokens`);
  - the web UI's `splitTokens` for `plots.js`, `can.js` and `state.js`.
  - No `str.split()` and no JS `/\s/` split of a received line remains.
  - `markerTick` agrees with `parse_marker` on runs of spaces, tabs and a missing text.
- CAPTURE-4 discard at buffer boundaries: an LF as the first byte of a read, an LF mid-read with the next line behind it, back-to-back long lines, and a disconnect mid-discard.
  - The latch is not cleared by the discard's own LF.
  - The CR is counted toward the cap the same way in the terminated and unterminated paths.
- CAPTURE-7: the partial is taken exactly once whatever the order of `_on_disconnect` and `stop()`.
  - `_carried` is snapshotted after `stop()`, so the new counts carry over to a re-attach.
- API-1: no `asyncio.to_thread` is left in `serial_link`.
  - The pending entry is registered before the write, and `sent_ts` is replaced after it. Latency is measured after the wait, so a response that lands before the write returns is still timed from the write.
  - At shutdown, handlers awaiting a write keep the loop open, or are cancelled first (in which case the executor callback returns early on a closed loop).
- The reconnect route passes `require_existing=True` (`server.py:1168`), and the missing-alias path maps to a 400.
- SIGHUP:
  - The handler only raises SIGTERM (class 66).
  - An ignored SIGHUP is left alone.
  - The uvicorn floor (0.35) replays the captured signal, so the exit is -SIGTERM.
  - The SIGHUP handler is registered only where `SIGHUP` exists, so on Windows it does not apply.
  - The tests are marked `posix_only`.
- `_report_key` (LIFECYCLE-5):
  - `set_report_key` only sets a variable, so the first, shared key set before `claim` writes nothing.
  - An unreadable data dir falls back to the shared key.
- `check_host`: the loader, `--host` and `PUT /config/server` (`server.py:1274`) share it. A wildcard host is accepted stripped, and non-printable characters are refused.
- The pjstream per-value filter deletion is safe:
  - its only caller is `serial_link.py:936`, fed by `points_from_tokens`;
  - the ad-hoc path refuses non-finite values;
  - enum and bits channels cannot be `f4` or carry a `*scale` (`_ENUM_TYPES` and `_BITS_TYPES` are integer-only).
- Sim `_cut_event` matches monitor.c `event_end` on:
  - a space at the byte past the limit;
  - no space;
  - a space at index 1;
  - trailing space runs;
  - a first token over 16 characters.
  - The `can`/`spi`/`adc` badcmd-before-badarg order matches SPEC 2.4. `_can` has one caller, which gates the subcommand.
- The SPEC hunks for 2.1 (tokens), 2.3 (event cut), 2.4 (can bus and `-` flags), 2.5 (non-finite point), 3.1 (startup log key), 3.2 item 7 (SIGHUP), 3.3 (host and device loader rules) and 3.4 (`rx_dropped` causes) match the code.
  - F3 is the exception: 3.3's skipped-entry list is complete for the scheme rule only.
- The ARCHITECTURE hunks for the pools, the daemon startup order and the signals match the code.
- `test_eol`'s `portless` module fixture isolates user dirs before conftest's per-test patch, and its second assertion pins that each 422 is for the eol alone.
- `test_e2e`'s held-port `/cmd` now asserts the exact 400 text, and the dropped `test_ws_backpressure_drop_oldest` is covered elsewhere per the triage (HEALTH-12).
