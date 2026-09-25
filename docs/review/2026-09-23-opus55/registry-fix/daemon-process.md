# Fix batch daemon-process (registry leg, 2026-09-25)

Base HEAD `3f10153`. Other batches edited the tree at the same time; my hunks are only in the files below.

Files changed: `host/mcuscope/{_stdio,daemon,protocol,serial_link,sim,update_check}.py`, `host/tests/{conftest,test_daemon_startup,test_update_check,test_protocol_tokenizer,test_render_line_breaks}.py`, `docs/SPEC.md` (2.5, 7), `docs/ARCHITECTURE.md` (`_stdio.py`, `update_check.py`).
New tests: `test_daemon_closed_streams.py`, `test_serial_link_break_errors.py`, `test_protocol_hex_grammar.py`, `test_sim_flood_stall.py`.

## Findings

Revert-verify: `~/tt-data/mcuscope-2026-09-25/fix-daemon-process/mutate.py` mutates a private copy, runs the named tests, restores. All 31 mutations CAUGHT. The tests named below are the ones that fail.

- R1-3 fixed: `httpx.AsyncClient` built with `asyncio.to_thread`. Fails: `test_update_check.py::test_the_client_is_built_off_the_event_loop`.
- R17-2 fixed: the banner is now `PlotJuggler: --plotjuggler asks to stream plot points to <dest>; /status plotjuggler says whether it is on`. Fails: `test_daemon_startup.py::test_startup_does_not_claim_the_plotjuggler_stream_is_on`.
- R18-3 fixed: `OverflowError` added to `_load_cache`'s tuple. Fails: `test_update_check.py::test_an_out_of_range_cache_timestamp_does_not_stop_the_daemon`, which also checks that the checker constructs and that the `create_app` lifespan starts over the cache.
- R18-4 fixed: module-level `_BREAK_ERRORS`, with `termios.error` included where `termios` imports. Fails: `test_serial_link_break_errors.py::test_a_failing_break_is_a_400_naming_the_port[termios]`.
- R29-2 fixed: the same file drives `SerialException` and `OSError`. A bare `raise` in place of the mapping fails all three params.
- R22-1 fixed: `_HEX_PAIRS_RE` fullmatch before `bytes.fromhex`, in both `hex_to_bytes` and `_decode_field`.
  - Fails: `test_protocol_hex_grammar.py`. Driven: `!ps` with tabs is stored as an event with `rx_dropped` 0, `!can` with tabs stores no frame, and the sim answers `ERR 2 badarg invalid hex payload`.
- R22-6 fixed (D-9 A, host half): `parse_marker`, `format_command` and `format_marker` (both its blank check and its sigil check) trim and judge on U+0020 only.
  - Fails: `test_protocol_hex_grammar.py` marker and command tests; each of the four sites was mutated separately.
  - `format_marker` was not in the brief. It had to change too, or `format_marker("\x1f")` would refuse text that `parse_marker` accepts.
- R27-10 fixed: `mock_transport` answers 404 off the literal `https://pypi.org/pypi/mcuscope/json`, and a new test pins GET, the URL and `Accept`. With `PYPI_URL` mutated, `test_unwritable_cache_dir_does_not_break_the_check` and others fail.
- R28-3 fixed: `match="boom"` plus a spy asserting `steps == ["claim", "create_app"]`. Both `raise RuntimeError("boom")` and a bare `raise RuntimeError` inserted before `pidfile.claim` fail it.
- R32-1 fixed: `_isolate_output_state` in conftest resets `_stdio.console_close_hook`. The probe is `test_daemon_startup.py::test_no_console_close_hook_is_left_from_an_earlier_test`. Run with `-p no:randomly` after `test_daemon_startlog.py`, it fails when the reset is removed.
- R35-1 and O-42 fixed: `_stdio._emit` sits behind `_note` (stderr) and the new `_say` (stdout). It swallows `OSError` and dup2s devnull over the fd, now closing the devnull fd (the old `_note` leaked one per failure). Every `print` in `daemon.py` goes through them.
  - Fails: `test_daemon_closed_streams.py`, covering these cases:
    - a closed stderr during an `--ignore-capture-lock` start;
    - a closed stdout during the banner (four lines);
    - a refusal returning 1 with no crash log, with a positive control that a crash does land in that dir;
    - a real dead pipe repointed at devnull with no fd leak.
  - `test_the_daemon_and_simulator_print_nothing_bare` (an AST walk) catches reverting any single site.
- R35-2 fixed: every sim stderr print goes through `_note`. I also moved the two stdout lines (`socket://...`, the pty path) to `_say`: same class, same file.
  - Fails: `test_a_closed_stderr_does_not_end_the_simulator_listener`, plus the AST guard for the other sites.
- R36-1 fixed (D-14 A): a backlog past `FLOOD_MAX_BURST` sets `next_flood = now` before the burst. Fails: `test_sim_flood_stall.py`, three tests:
  - stall: at most cap + rate in the next second, and at least 95% of the rate;
  - no stall: the rate is met;
  - a hiccup under the cap is still caught up: a threshold mutated to 1000 fails it.
  - At 1,000,000/s the output stays at the old 500,000/s ceiling (driven).
- R75-2 fixed (Python half): `NON_SPACE_WS` is now every `str.isspace()` code point except space, CR and LF (106 cases, was 30), and `BREAKS` is every code point `splitlines()` splits on. Both are derived at import, with the comment corrected. Nothing to revert-verify: it is a test-data change, and all cases pass.
- R78-4 fixed: `sim.SERVE_THREAD_NAME` is used by `spawn()` and the test, with the positive control `test_the_serving_path_is_seen_by_the_thread_name_check`. Renaming the thread fails it.

Single files run green after the change: the 8 files above plus `test_protocol.py`, `test_protocol_strict.py`, `test_sim.py`, `test_stdio.py`, `test_capture_lock.py`, `test_daemon_startlog.py`, `test_daemon_console.py`, `test_sim_tcp.py`, `test_sim_pty.py`, `test_break.py`, `test_serial_link_*.py`, `test_config_api.py` and `test_scaffold.py`. Ruff is clean on every touched file.
Red files:
- `test_e2e.py`: 1 failure, caused by this batch (see "Needs another batch").
- `test_plotjuggler.py`: 4 failures on a `/status plotjuggler.target` key and `line_id`. They come from another batch's `pjstream.py`/`server.py` work, not this one.

## CHANGELOG

- **Upgrade:** a command sent by `/cmd`, `/wait`/`/assert` `send` and `mcu cmd` is trimmed of spaces (U+0020) only.
  - A tab or control byte reaches the board as typed.
  - A trailing CR or LF is refused (400) instead of trimmed.
- **Upgrade:** firmware `!m` marker text keeps surrounding tabs and control bytes; only spaces are trimmed (SPEC 2.5).
- Hex tokens with whitespace inside (`DE\tAD`) are malformed: such a `!can` or `!ps` line is kept as a generic event instead of being decoded short (`!can`) or dropped (`!ps`), and `mcu-sim` answers `can tx` with one `badarg`.
- `POST /break` answers 400 `port X break failed: ...` when the tty fails the break, instead of a 500.
- `mcuscoped` starts even when the release-check cache holds an out-of-range timestamp.
- `mcuscoped` and `mcu-sim` no longer die, or crash-log a refusal, when stdout or stderr is closed.
- The `--plotjuggler` startup line says the stream was asked for and points at `/status` for whether it is on.
- `mcu-sim --flood` resumes at its rate after a long stall instead of replaying the backlog at 500,000 lines/s.

## Needs another batch

- tests-daemon (`test_e2e.py:229`): `test_empty_cmd_is_client_error_not_500` sends `"\t"` expecting 400 `empty command`. Under D-9 A a tab is a command byte, so the daemon now sends it (200, the sim answers badcmd).
  - Change: loop over `("", "   ")` only, and add `r = c.post("/cmd", json={"cmd": "\t"})`, asserting 200 and `r.json()["status"] == "err"` (the tab reached the board).
- firmware-packaging: the firmware half of D-9 (space only, not tab, as blank).
- The batch owning `webui/cmdbar.js`: `submitCmd` and `submitMarker` use JS `.trim()`, which strips tab, VT, FF, NBSP and Unicode spaces.
  - That is a third whitespace set against D-9's U+0020. Change: `.replace(/^ +| +$/g, "")`, and blank = empty after it.
- cli (`cli_output._to_devnull`, line 164): `os.dup2(os.open(devnull), fd)` leaks the devnull fd on each call. Change: close the opened fd after `dup2`, as `_stdio._emit` now does.
- daemon-api (SPEC 3.4 `/break`, optional): add "a transport that fails the break is a 400 `port <alias> break failed: ...`".

## Needs Windows

- The `except ImportError` branch of `_BREAK_ERRORS` (no `termios`), and the termios test param is skipped there.
- `_emit`'s dup2 on a Windows closed pipe goes through `_PipeErrorStream` (EINVAL respelled as BrokenPipeError, still an OSError) and should behave the same. Not driven; `test_a_dead_pipe_is_repointed_at_devnull_without_leaking_an_fd` is skipped on win32.

## Needs a browser

Nothing.

## Cleanup owed

The private copy `~/tt-data/mcuscope-2026-09-25/fix-daemon-process/copy/` (11 MB: an rsync of `host/`, `tools/` and `firmware/` without `.venv`, no databases) is still on disk.
A recursive delete needs the owner's confirmation. `mutate.py` beside it is rerunnable after a fresh rsync.

## The two questions

1. **Least confident, rechecked.**
   - R36-1 at rates above the burst ceiling, where every pass now re-anchors.
     - Drove 1,000,000/s over 10 ms passes: 500,000 lines/s, the same ceiling as before, so the high-rate load path is unchanged.
   - R1-3 moves the client build onto the default executor (`asyncio.to_thread`), which session exports also use.
     - The check is a background task, so a queued build delays only the check. I judged this acceptable and did not measure it.
   - R22-6 changes behaviour that another batch's test pinned (the `"\t"` command, above).
     - Grepped every `format_command` and `parse_marker` test for tab or control-byte input. `test_e2e.py:229` is the only one.
2. **What we had not thought about.**
   - The sibling surfaces of D-9:
     - `format_marker` had the same `str.strip` (fixed here);
     - the web UI command and marker boxes trim with JS's set (handed over);
     - `cli_output._to_devnull` has the fd leak the old `_note` had (handed over).
   - Other `termios.error` sources in pyserial (`tcflush`, `tcdrain`): the daemon calls neither on the write path, and the open path catches `Exception`. No other site is affected.
   - `mcu-sim`'s stdout device line had the same closed-stream failure as the daemon banner. Fixed in this batch.
