# Fix-diff leg (leg 7), review round 2

Scope: `git diff fd76735` (wave 1 commit 0b5eed9 plus the uncommitted wave 2), 60 files, 5451 diff lines, plus the four untracked new test files that `git diff` does not show (`tests/test_review_r2_{config,serial,server,store}.py`).
Read-only: no edits, no commits.

## Mechanical checks

- **Dash check**: `grep -P '\xe2\x80\x94|\xe2\x80\x93'` over the whole diff -> **0 matches**. Clean.
- **Ruff**: `uv run python -m ruff check .` -> "All checks passed!" (rc 0).
- **Suite**: `uv run python -m pytest -q` -> **1111 passed, 1 skipped**, 347 s, rc 0.

## Findings

### 1. MED - `/send`'s 12-token cap is undocumented and contradicts SPEC's escape-hatch clause

`host/mcuscope/serial_link.py:944-950` (new).
`_encode_wire` now refuses any line over 12 space-separated tokens. It is reached by two callers: `send_command` (a `>SEQ ...` line, where the cap is exactly right, SPEC 2.3/5.4) and `send_raw`, which is `POST /send`.
SPEC 618-620 says of `/send`: "Write one raw line ... with no seq management ... **This is the escape hatch for non-monitor firmware.**" SPEC 79 scopes the 12-token rule to "a command line". The SPEC diff in this round does not add a /send token cap anywhere.

CONFIRMED (probe from `host/`):

```
line = "set output voltage to 12 volts and current to 3 amps now please"   # 13 tokens
SerialPort._encode_wire(line) -> PortError: line has 13 tokens, over the 12 cap
```

So a plain-English line to a non-monitor target is now a 400 on the one endpoint documented as having no monitor grammar. The fixer followed ruling SP-L7 verbatim ("_encode_wire enforces the 12-token cap locally... /send answers 400"), so this is a defect in the ruling, not in the implementation - but CLAUDE.md's "SPEC wins" applies: either SPEC 3.4's `/send` paragraph gains the cap, or the cap moves to the command path only (`format_command` already covers `/cmd`; the raw path would keep the length/newline/ASCII checks).
Registry: class 19 shape (two engines validating one thing) and a SPEC-drift instance.

### 2. MED-LOW - `_export_tmp_dir` writes into the daemon's CWD for an in-memory or relative capture

`host/mcuscope/server.py:1885-1892` (new). Docstring: "None (the system temp dir) **only when that directory does not exist**, which in practice means an in-memory capture."

CONFIRMED (probe from `host/`):

```
db_path=":memory:"    -> _export_tmp_dir = '.'   (cwd, not None)
db_path="relative.db" -> _export_tmp_dir = '.'
```

`Path(":memory:").parent` is `Path('.')`, and `.` is always a directory, so the None branch is unreachable for the case the docstring names. With `storage.db_path = ":memory:"` a session export mkstemps a copy the size of the session into the daemon's working directory. For a detached daemon (`mcu daemon start`, `start_new_session` / `DETACHED_PROCESS`) that is whatever directory the launcher happened to be in - on Windows just as much as on Linux, and possibly read-only or a mount the operator does not expect. A relative `db_path` lands in the same place, which is correct there (that is where the DB is).
Fix shape: treat `":memory:"`/`""` explicitly, as the three other sites in `store.py` already do (`self._db_path not in (":memory:", "")`).

### 3. MED-LOW - two new API refusals that no ruling asked for

Both are documented in the SPEC diff, so they are deliberate, but neither appears in `triage.md`:

- `host/mcuscope/server.py:1532-1542`: `/plot/export` now answers 400 when the selection is empty and *no* requested name exists. Formerly 200 with a header-only CSV.
- `host/mcuscope/server.py:1333-1339` + `PURGE_FUTURE_SKEW_S`: `/purge` now answers 400 for a `before_ts` more than 60 s in the future. Ruling F6 covered only the CLI's `--before-days > 0`.

Flagged per the brief ("flag any hunk that does something the ruling did not ask for"). Both are behaviour changes visible to any existing client; they need the orchestrator's sign-off rather than a silent ride on the round.

### 4. LOW - CLAUDE.md/README interpreter bump exceeds (and one line contradicts) the ruling

`CLAUDE.md:27,33`, `README.md:113,377` change 3.12 -> 3.13. `triage.md:68` (spec-drift ruling) says explicitly: "CLAUDE.md venv-version note: **local drift, no repo change**". `triage.md:170` (batch G scope) lists "CLAUDE.md venv version line" as in scope. The triage contradicts itself and the fixer followed the batch line.
The README changes were in neither line: `uv tool install mcuscope --python 3.13 --force` is a user-facing recommendation now pinned to whatever this one machine's venv happens to be (it is 3.13 here: `host/.venv/lib/python3.13`). Suggest reverting the two README lines at least.

### 5. LOW - `daemon start --help` no longer shows the concrete timeout default

`host/mcuscope/cli_daemonctl.py:68` (`DAEMON_START_TIMEOUT_S = _start_timeout_default`, the function) plus `cli.py:1440`.
CONFIRMED: `mcu daemon start --help` renders `[default: (dynamic)]` where it used to render the number. Click handles the callable correctly (per-invocation read works, which is the F14 fix), so this is cosmetic - but the documented "Seconds to wait" no longer tells the user what the wait is. One `show_default="20 unless MCUSCOPE_START_TIMEOUT is set"` would restore it.

### 6. LOW - pid-suffixed temp siblings no longer self-heal

`host/mcuscope/config.py:410`, `host/mcuscope/update_check.py:203`, `host/mcuscope/cli_daemonctl.py:515`.
The F10 fix is right (a fixed `.tmp` let two processes write each other's bytes), but the old fixed name was self-limiting: a killed write left one file that the next write overwrote. `<name>.<pid>.tmp` accumulates one file per crashed write in the config dir, the cache dir and the data dir, and nothing sweeps them. Windows makes it slightly likelier: `replace_atomic` retries against a sharing violation and gives up leaving the temp behind.
Not worth its own sweep, but worth a `glob("*.tmp")` cleanup in `_write_doc`/`_save_cache`, or a note.

### 7. LOW (SUSPECTED) - `_TempFileResponse` rests on uvicorn not implementing `http.response.pathsend`

`host/mcuscope/server.py:1897-1908` (new).
Starlette 1.3.1's `FileResponse._handle_simple` has a `send_pathsend` branch: when the ASGI server advertises the `http.response.pathsend` extension, starlette hands the *path* to the server and returns immediately, and the server opens and sends the file afterwards. The `finally: _unlink_later(...)` would then delete the export before a byte of it left the process.
Verified the shipped server does not take that branch: `grep -rn pathsend` over the installed uvicorn -> no hits. So this is correct today. It is a one-line comment's worth of load-bearing assumption, and it is exactly the kind of "a fix that rests on one runtime's behaviour" the registry already names (class 24).
Windows half: an `os.unlink` while any handle is still open raises `PermissionError`, which `_unlink_later`'s `suppress(OSError)` swallows - and after this round's change the leaked copy sits in the user's **data directory** beside the capture rather than in the system temp dir, so nothing ever reaps it. Also unverified on Windows.

### 8. LOW - `_release_pid_on_terminating_signal` prints once per signal off the main thread

`host/mcuscope/daemon.py:266-273`. The `try/except ValueError` is inside the `for sig in sigs` loop, so an embedder calling `main()` off the main thread gets the same warning 2-3 times (SIGINT/SIGTERM, plus SIGBREAK on Windows). Cosmetic; a flag or a single message after the loop would do.

### 9. LOW - one new POSIX test is timing-dependent in its recovery half

`host/tests/test_sim_pty.py:118-186`, `test_pty_write_gives_up_instead_of_wedging_with_no_reader`.
The wedge half is solid. The recovery half reads the slave exactly once (`os.read(slave, 65536)`) in a joined thread, then asserts the next `_pty_write_lines(..., budget=2.0)` returns True. It passes here, but it depends on that single read freeing enough of the pty queue within 2 s. Correctly gated (`pytestmark = skipif(os.name != "posix")`), and the `import termios` / `import pty` are inside the guarded module, so Windows skips the whole file.

### 10. Open (not a defect) - F9 was not implemented and the contradiction was reported

`host/mcuscope/cli_argv.py` carries only the F8 (`is_value`) change. `fix-c1.md:100-109` reports that ruling F9 (`-p` hoisted only as exact `-p` + value or `-p<digits>`) cannot be implemented without re-breaking the deliberately-tested `-psim` attached alias, and declines to change it. That is the behaviour the brief asks a fixer for. Consequence still live: `mcu lines -pulse` is hoisted to the group and errors there rather than at the subcommand. Needs an orchestrator decision, not a fix.

## Verified good (probed, no finding)

- **Class 35 / R35, closed stdout.** Through the real entry point: `mcu --json status --url http://127.0.0.1:9 >&-` exits **3** (the `_stdio` startup repair points a `None` stdout at devnull, so the guarded `out_json` write never sees EBADF). A direct `os.close(1)` after startup exits 120 with a rich traceback, but that path is unreachable through the console script. `out_json` mirrors `err_write` exactly (both catch `BrokenPipeError` only), as ruled.
- **Termios/pty guards.** `import tty` and `os.set_blocking` in `sim.serve_pty` sit *after* the `if os.name == "nt": return 2` early return; `_pty_write_lines` is module-level but imports nothing POSIX-only. No new `termios` import at module scope anywhere.
- **New text writes carry `newline=`** (class 2): `_write_pid_record` `newline=""`, `_write_doc` `newline=""` (unchanged), every new test write `newline="\n"`.
- **Windows gating of new tests**: the two XDG_DATA_HOME cases carry the existing `_PIDDIR_ENV_SKIP` (`os.name == "nt"`), the unreadable-config case carries `skipif(sys.platform == "win32")`, the pty module is skipped wholesale. The `config-path-under-a-file` case raises `OSError` on both platforms (`IsADirectoryError` / `NotADirectoryError`). No new test depends on DNS, locale, timezone or the home directory; the two subprocess helpers copy `os.environ` and override `XDG_DATA_HOME` and `MCUSCOPE_URL`.
- **CD7 (regexp removed from the loop connection)**: `REGEXP` appears in exactly one SQL site (`store.py:1422`, `query_lines`), reachable only through the three paths that register their own (`store.py:1546/1555/1586`). One direct caller exists and it is the new negative test.
- **`emit_ok`'s `%.*s` bound** is correct: `emit_ok` has exactly one caller (`monitor.c:940`, passing `g_resp`), so the precision `sizeof g_resp` matches the buffer it bounds.
- **C harness asserts are live**: `firmware/tests/Makefile` never defines `NDEBUG` (plain `-O2 -g` / `-O1 -g -fsanitize=...`), so the new `fake_feed`/`fake_uart_write`/`fake_can_push` bounds asserts actually fire.
- **JS test gate is dynamic**: `test_webui_js.py:54` globs `*.test.mjs` and asserts `pass >= declared`, so the five new `.test.mjs` files are counted without an edit (matching the SD4 SPEC change that dropped the hardcoded 27).
- **Class 17 instances in new code** report results, not requests: `note_truncated` now names the rows returned, the `auto_vacuum` read-back reports the pragma's answer, `resolved_device` reports the device actually opened.
- `decimate` needs no `ge` bound: `store.py:1758` clamps with `max(1, int(decimate))`.
- `ipaddress` cross-version comparison in the new broadcast guard is safe (`IPv6Address == IPv4Address` is False, not a raise).

## Files reviewed

Hunk by hunk, new code read in place:

- `host/mcuscope/`: serial_link.py, store.py, server.py, sim.py, protocol.py, cli.py, cli_argv.py, cli_client.py, cli_daemonctl.py, cli_output.py, config.py, lockfile.py, daemon.py, update_check.py, pjstream.py
- `host/mcuscope/webui/`: api.js, cmdbar.js, digital.js, freeze.js, plots.js, settings.js, state.js, statusbar.js, terminal.js
- `firmware/`: monitor/monitor.c, monitor/monitor.h, monitor/monitor_cmds.c, monitor/port_template/monitor_port_template.c, tests/fake_shims.c
- `host/tests/`: conftest.py, test_capture_lock.py, test_firmware_monitor.py, test_hardening.py, test_regressions.py, test_protocol.py, test_sim.py, test_sim_pty.py, test_plotjuggler.py, test_plot_grammar_fixture.py, test_review_r2_cli.py, test_review_r2_sim.py, test_review_r2_config.py, test_review_r2_serial.py (structure + the risky cases), test_review_r2_server.py (structure + the config-write and sessions cases), test_review_r2_store.py
- `host/tests/webui_js/`: dom_stub.mjs, settings_pj.test.mjs
- Docs/config: docs/SPEC.md (full diff), docs/ARCHITECTURE.md, README.md, CLAUDE.md, host/pyproject.toml

## Deliberately not reviewed line by line

- `firmware/tests/test_monitor.c` (+371) and `firmware/monitor/INTEGRATION.md`: checked that all 11 new C cases are registered in `main()` and that `monitor_mark`'s tick-sigil and empty-text refusals are driven (`test_monitor.c:431-457, 1067-1086`); the assertions themselves were left to the firmware leg, which owns them and whose ASan run is green here.
- The five new `webui_js/*.test.mjs` files (`cmdbar_bounds`, `digital_repaint`, `plot_grammar`, `plots_seed_grammar`, `settings_storage_cap`, `settings_ports_baud`, `statusbar_logic`, `freeze`): the JS half of the round is the webui leg's, and the pytest wrapper's count gate proves they run and pass. Only `settings_pj.test.mjs` (the TQ-F3 tautology fix) and `dom_stub.mjs` were read.
- `host/tests/plot_grammar_cases.json` (+82) as data: the Python half asserts >= 20/10/15 cases with both answers present in each section, and both engines consume the same file, which is the property that matters.
- `host/mcuscope/webui/` drawing/uPlot paths that the DOM stub cannot reach - out of range here as it is everywhere (CLAUDE.md), left to the browser leg.
