# Batch daemon-process

Owner-pick items use the recommended option in `decisions.md` unless the owner answered otherwise.

## Files

- Source: `host/mcuscope/daemon.py`, `sim.py`, `_stdio.py`, `update_check.py`, `serial_link.py`, `link.py`, `protocol.py`.
- Tests you may edit: `test_daemon_startup.py`, `conftest.py`, `test_update_check.py`, `test_protocol_tokenizer.py`, `test_render_line_breaks.py`.
- New tests go in new files (e.g. `test_serial_link_break_errors.py`, `test_daemon_closed_streams.py`, `test_sim_flood_stall.py`, `test_protocol_hex_grammar.py`).
  Do not edit `test_break.py`, `test_sim*.py`, `test_serial_link_tx.py` (firmware-packaging owns their imports) or `test_config_api.py` (tests-daemon).
- Docs: SPEC 2 (2.1, 2.5), 3.1, 3.2, 3.6, 7; the matching `docs/ARCHITECTURE.md` module sections.

## Findings

### R1-3 LOW: `httpx.AsyncClient` built on the loop per release check
- Checked: update_check.py:268 constructs the client (SSL context, CA bundle) inside `check_once` on the loop.
- Fix: build it off the loop (`await asyncio.to_thread(httpx.AsyncClient, ...)`), or build one SSL context once off the loop and pass it.
- Test: `httpx.AsyncClient` replaced by a recorder; `asyncio.get_running_loop()` raises in the constructing thread.

### R17-2 LOW: banner says the PlotJuggler stream is on before it is
- Checked: daemon.py:494-502 prints `streaming plot points to <dest>` from the requested config; the lifespan may then fail to enable it.
- Fix: word the line as the request: `PlotJuggler: --plotjuggler asks to stream plot points to <dest>; /status plotjuggler says whether it is on`.
- Test: `daemon.main([... "--plotjuggler", "nosuch.invalid:9870"])` with `_serve` stubbed; stdout does not claim `streaming plot points to`.

### R18-3 MEDIUM: an out-of-range cached timestamp stops the daemon starting
- Checked: `_load_cache` (update_check.py:186) catches `(OSError, ValueError, KeyError, TypeError)`; `float()` of a 400-digit int raises `OverflowError` (verified here), inside the lifespan, whatever `[update] check` says.
- Fix: add `OverflowError` (or `ArithmeticError`) to the tuple.
- Test: `update.json` `{"latest": "0.1.0", "checked_at": 1` + 400 zeros + `}` under the test cache dir; `UpdateChecker(...)` constructs with `checked_at` None; `create_app` lifespan starts.

### R18-4 LOW: a POSIX break failure escapes as a portless 500
- Checked: `termios.error` MRO is `(error, Exception)` here; `_break_locked` (serial_link.py:1168) maps only `(SerialException, OSError)`.
- Fix: map `termios.error` too (a module-level tuple, `termios` imported only where it exists), so `/break` answers 400 `port X break failed: ...`.
- Test (skipped on win32): a link whose `send_break` raises `termios.error(5, "Input/output error")`; `POST /break` answers 400 naming the port and `break failed`.

### R29-2 LOW: no test drives a transport failure during `/break`
- Same file as R18-4's test: `send_break` raising `serial.SerialException` and `OSError`, each answering 400 `break failed`.
- Revert-verify: replace the mapping with a bare `raise`; the tests fail.

### R22-1 MEDIUM: `bytes.fromhex` accepts whitespace inside hex tokens
- Checked: `hex_to_bytes` (protocol.py:165) and `_decode_field` (:861) call `bytes.fromhex`; `bytes.fromhex('0a\t0b')` is `b'\n\x0b'` (verified). An rx `!ps` whose f4 field carries tabs raises `struct.error` and the reader drops the line; `!can ... DE\tAD\t` stores dlc 2; the sim answers OK to `can tx 100 DE\tAD\t`.
- Fix: both accept only `[0-9A-Fa-f]` pairs (a fullmatch before decoding); `_decode_field` then never hands a short buffer to `struct`.
- Tests: `hex_to_bytes("0a\t0b")` raises `ProtocolError`; the tab-padded `!ps` line is stored as a generic event (`rx_dropped` stays 0); the `!can` line stores no `can_frames` row; `Simulator.handle_line("can tx 100 DE\tAD\t")` answers `ERR 2 badarg`.
- SPEC 2.1 already says "hex pairs with no separators"; no text change.

### R22-6 LOW, owner-pick D-9 (host half): three whitespace sets for "non-space"
- Checked: `parse_marker` uses `str.strip` (protocol.py:1121); `format_command` uses `cmd.strip()` (:297); the firmware treats space and tab as blank.
- Fix (D-9 option A, U+0020 only): `parse_marker` trims and judges blankness on U+0020 only; `format_command` strips U+0020 only (CR and LF are still refused by `_check_no_break`). The firmware half is firmware-packaging's.
- Test: `parse_marker("!m @5 \x1f")` returns text `\x1f`; `format_command(7, "ping\x1f")` keeps the `\x1f`.
- SPEC 2.5: define "non-space" and "surrounding whitespace" as U+0020.

### R27-10 LOW: no test pins the release-check URL
- Checked: `mock_transport` (test_update_check.py:29-37) and the handlers at :164, :165, :257 answer every URL and method.
- Fix: the transport answers 404 off `PYPI_URL`, and one test asserts method `GET`, URL `PYPI_URL` and the `Accept` header. `test_config_api.py:347` (tests-daemon's file) needs nothing once this pins the URL.
- Revert-verify: `PYPI_URL = "https://pypi.org/project/mcuscope/"` fails the file.

### R28-3 LOW: `pytest.raises(RuntimeError)` satisfied before the claim
- Checked: test_daemon_startup.py:75.
- Fix: `match="boom"`, plus a spy showing `pidfile.claim` ran before `create_app` raised.
- Revert-verify: `raise RuntimeError` inserted before `pidfile.claim` in `daemon.main` fails it.

### R32-1 LOW: `_stdio.console_close_hook` left set across tests
- Checked: `daemon._serve` assigns it (daemon.py:306); in-process `_serve` tests leave it pointing at a finished server; conftest has no reset.
- Fix: an autouse conftest fixture `monkeypatch.setattr(_stdio, "console_close_hook", None)`.
- Check: `-p no:randomly` order of `test_daemon_startlog.py` then a probe asserting the hook is None at test start.

### R35-1 LOW, with O-42: a closed stderr or stdout at start stops the daemon
- Checked: bare `print` at daemon.py:391, 427, 431, 440, 442, 453, 466, 493, 498 and 142, 152. `_stdio._note` guards stderr only.
- Fix: every daemon message goes through `_stdio._note`, plus a stdout sibling in `_stdio` (suppress `OSError`, dup2 devnull over the fd), so a closed stream drops the message and the exit code stays the refusal's (1) or the start goes on.
- Tests (in process, `_serve` stubbed): with `sys.stderr` raising `BrokenPipeError` and a held capture lock plus `--ignore-capture-lock`, `main` reaches `_serve`; with `sys.stdout` raising, the start banner does not stop it; a refusal returns 1 and writes no crash log.

### R35-2 LOW: the simulator's error prints end the listener
- Checked: sim.py:797, 813, 922, 1103, 1155, 1157, 1184 print to stderr unguarded.
- Fix: every site goes through `_stdio._note` (sim.py already imports from the package), so a closed stderr drops the message instead of ending the listener.
- Test: in process, stderr raising `BrokenPipeError` and `_serve_socket_client` forced to fail twice; the listener still accepts a third client.

### R36-1 LOW, owner-pick D-14: `--flood` backfills a stall at 25x the asked rate
- Checked: `_poll_flood` (sim.py:498-512) caps each pass at `FLOOD_MAX_BURST` but advances the schedule only by what it emitted.
- Fix (D-14 option A): when the backlog passes `FLOOD_MAX_BURST`, re-anchor the schedule at now (drop the backlog).
- Test: `--flood 20000`, a 1 h stall, 10 ms passes: lines emitted in the next second stay under the cap plus 20000.
- SPEC 7: say a stalled flood resumes at its rate.

### R75-2 LOW (Python half): stdlib-derived sets kept by hand
- Checked: `NON_SPACE_WS` (test_protocol_tokenizer.py:14) is described as "bytes str.split() treats as whitespace" and is neither that set nor the str set; `BREAKS` (test_render_line_breaks.py:11) is right today.
- Fix: derive both from the stdlib over all code points (or the byte range the tokenizer sees) and correct the comment.
- The JS copy in `state_marker_tick.test.mjs` is webui-settings'.

### R78-4 LOW: absence check keyed on an unpinned thread name
- Checked: test_daemon_startup.py:104 looks for thread `mcu-sim`, named only at sim.py:1044.
- Fix: a named constant in `sim.py` used by the thread and the test, plus a positive control where the serving path does start the thread and the name is seen.

### O-42 LOW: `mcuscoped` dies when stdout is a closed pipe
- `print(files, flush=True)` at daemon.py:431 raises (exit 120, crash log). Fixed and tested with R35-1.
