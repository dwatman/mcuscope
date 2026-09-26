# Windows checklist

Merged from the registry legs' "Owed" sections and the fix-batch reports' "Needs Windows" lists, deduplicated.
Run after the registry fix batches land, so one Windows pass covers both.

CI coverage (`.github/workflows/ci.yml`): the `test` job runs the whole pytest suite, with node and MinGW (JS and firmware tests), on `windows-latest` for Python 3.10 to 3.13.
The `wheel smoke (windows)` job installs the wheel and runs only `--version`/`--help`.
So `[CI]` below means a named test file already runs on Windows in CI; everything else is manual.

Every manual daemon run uses `-c` on a throwaway TOML with its own `db_path`, a port other than 8558, and `MCUSCOPE_CONFIG_DIR`/`MCUSCOPE_DATA_DIR` in a scratch dir.

## Test files (CI covers them; check the latest Windows run is green)

- [x] [CI] `test_cli_daemonctl.py` (the append handle's access mask, the ctypes fallback), `test_daemon_console.py` and `test_stdio.py` (console-close hold, ctrl handler), `test_server_export_pool.py` (export waiters), `test_status_ppid_serial.py`, `test_cli_daemon_stop_scope.py`. The fix reports named these by their old `test_*fixdiff*` names.
- [x] Not covered until a batch acts: the `posix_only` cases in `test_cli_daemon_stop_scope.py` have nothing POSIX-specific (cli-tests drops the marker; then CI covers them).
- [x] [CI] once the batches land: the new tests for R18-2 (bad `--url` on the WS path), R35-1/R35-2 (closed streams), R22-1, R13-1's corrupt-record cases.
- [x] Not covered: R15-4, `mcuscoped.exe` serving from an installed wheel. firmware-packaging adds it to the wheel-smoke job; until then run by hand: `mcuscoped.exe --sim -c t.toml --port 18700`, `/status` answers, `mcu.exe --url http://127.0.0.1:18700 daemon stop` exits 0.

## Timing tests on a tick-granular clock (class 21)

- [x] `pytest tests/test_e2e.py::test_lines_since_ts_excludes_what_predates_it` and `tests/test_assert.py::test_last_ms_window`, 20 runs each, on CPython 3.10, 3.11 and 3.12. Expect 0 failures once tests-daemon's R21-1/R21-2 fixes land.
- [x] R21-3 to R21-6 fail only on a slow runner: after the fixes, run `test_wait_repeat.py`, `test_serial_link_devices.py`, `test_reconnect.py` 10 times each with the machine loaded (e.g. a parallel build). Expect 0 failures.

## Floor run (class 43)

- [x] Whole suite on Python 3.10 with every dependency at its declared floor (`uv pip install --resolution lowest-direct -e ".[dev]"` in a fresh venv), including the JS and firmware files. Expect green; CI runs 3.10 with current dependencies only.

## SQLite plans (class 20)

- [x] Each Windows CPython 3.10 to 3.13 bundles its own SQLite: run `plans_ver.py` (and `probe20.py`) from `~/tt-data/mcuscope-2026-09-24/registry-leg/15-28/` under each (copy the scripts over). Expect the same plans as Linux: `idx_plot_name_line` for one name, no `TEMP B-TREE` after the R20-1 fix.

## Manual daemon lifecycle

- [x] `python -c "from mcuscope.cli_daemonctl import _open_append as o; import os; f=o('x.err'); print(os.fstat(f.fileno()).st_size)"` prints a size, no `WinError 5`.
- [x] From a uv, pipx and plain venv install: `mcu daemon start -c t.toml`, then `mcu daemon restart`, then `mcu daemon stop`.
  - Expect start exit 0, the restart with no capture-lock refusal, stop exit 0.
  - Note which pid each line names (R17-4 / D-4): after the D-4 fix, the serving pid and the launcher.
- [x] Two `mcu daemon start` at once on one host:port from a venv: both daemons' lines stay whole in the `.err`, and the failed start shows its own tail.
- [x] The index-build wait: `mcu daemon start` on a large capture needing an index build; the CLI reads the `.err` while the daemon appends to it, and prints the build note.
- [x] From cmd and from PowerShell: foreground `mcuscoped -c t.toml`, then close the window. The capture's sys rows end `daemon stop`, the session is closed, the pid record is gone.
- [x] The same while a `wait=1` session export waits and a large `/lines/export` streams: all three still hold (the 3 s wait plus the finaliser fit in 4.5 s).
- [x] Late-attach start (pythonw-based venv): same as above, and the ctrl handler is installed once.
- [x] `start /b mcuscoped -c t.toml` in cmd, then Ctrl-C in that window: the daemon keeps running; closing the window then writes the `daemon stop` row.
- [x] `mcu daemon start` then `mcu daemon stop`: unchanged (no console, no handler install).

## Probes owed since earlier rounds

- [x] Class 3: two listeners on one port with `SO_EXCLUSIVEADDRUSE` (the probe in `test_sim_tcp.py` runs in CI; also start two `mcuscoped` on one port by hand: the second refuses).
- [x] Class 13: `CreateFileW` handle rights and `fstat` on the append handle (covered by the `_open_append` line above); `replace_atomic` against a file another process holds open (config save while an editor has it); `_TempFileResponse` unlinking a download still open elsewhere (left for the next start's sweep, which removes it).
- [x] Class 14: the `_stdio.py:203` consoleless residual: `pythonw -m mcuscope.daemon -c t.toml` starts, writes its startup log, and stops on `mcu daemon stop`.
- [x] Class 35: `mcu-sim --pty` with stderr closed (`mcu-sim --pty 2>&-` from Git Bash, or a Python `Popen(stderr=PIPE)` closed at once) exits 2, not 1, after R35-2; the EINVAL translation (`_stdio.translate_closed_pipe_errors`) under a closed stdout for `mcu tail -n 5 | exit`.
- [x] Class 66: Ctrl-C and Ctrl-Break in a foreground `mcuscoped` console stop it gracefully (sys row `daemon stop`).
- [x] R18-4: `POST /break` on a USB-serial adapter unplugged mid-run answers 400 `port <alias> break failed: <reason>` (`link._win32_break` raises where pyserial ignores the Win32 results).
- [x] R27-14: force the `_open_append` failure (a read-only `.err` path) and read the warning: it names the real error, not `WinError 0`.

## Results, 2026-09-25 (Windows 11 desktop, HEAD 453dcfe)

Every box above is ticked.
Console scenarios were driven through a real `conhost.exe` window: WM_CLOSE to the console window for a close, `GenerateConsoleCtrlEvent` for Ctrl-C and Ctrl-Break.
No C compiler on this machine, so the firmware files skipped locally (CI's MinGW runs them).

- Whole suite, 3.12: 2864 passed, 10 failed, all in `test_cli_export_files.py`: `symlink_to` raises WinError 1314 on an account without admin or Developer Mode. CI runs as admin, so it never saw this. Fixed: those tests skip where the OS refuses a symlink, as `test_daemon_config_path.py` already does.
- CI run 36136620633 at 453dcfe: all four Windows legs and the Windows wheel smoke job green. The `posix_only` marker is already gone from `test_cli_daemon_stop_scope.py`.
- Floor run, 3.10 at `--resolution lowest-direct` (fastapi 0.115.7, starlette 0.44.0, uvicorn 0.35.0, httpx 0.27.0, typer 0.26.0, platformdirs 4.0.0, websockets 14.0, pytest 8.0.0), whole suite with the symlink fix: 2864 passed, 78 skipped, 0 failed.
- Class 21: the two R21-1/R21-2 tests, 20 runs each on 3.10, 3.11 and 3.12: 0 failures. `test_wait_repeat.py`, `test_serial_link_devices.py` and `test_reconnect.py`, 10 runs each with the floor suite running alongside: 0 failures.
- Class 20: `plans_ver.py`/`probe20.py` are not on this machine; ran every plan-pinning test file instead (9 files, 167 tests) on 3.10 (SQLite 3.40.1), 3.11 and 3.13 (3.53.1), plus 3.12 in the whole suite: all pass.
- `_open_append`: `fstat` reads the size, no WinError 5. R27-14: a read-only `.err` gives `warning: cannot write the daemon log ...: [WinError 5] Access is denied.`, and the start still succeeds.
- start/restart/stop from a uv venv (3.12), a plain venv (Store Python 3.10) and pipx (3.13): all exit 0, and no capture-lock refusal on restart. `start` names the serving pid and the launcher. `stop` names the recorded pid, which is the launcher. The index-build notice also names the launcher.
- R15-4: `mcuscoped.exe --sim` from the installed wheel serves `/status`, and `mcu.exe daemon stop` exits 0.
- Index-build wait: 2M-line capture with all five `lines` indexes dropped, `--timeout 1.5` and `2.5`. The CLI printed the build note it read from the `.err` the daemon was appending to, then `started`, exit 0.
- Console close (cmd and PowerShell, uv and plain venv, and the pythonw late-attach path), Ctrl-C and Ctrl-Break (cmd and PowerShell): each writes `daemon stop`, ends the session and removes the pid record, within 0.5 s.
- Close with two session builds, a `wait=1` waiter and a slow `/lines/export` in flight (2M-line capture): `daemon stop` is written, the session is closed and the pid record is gone. The three session requests get a bare 500 and the daemon's stderr shows uvicorn's `Cancel 4 running task(s), timeout graceful shutdown exceeded` with four `CancelledError` tracebacks. The lines stream is cut.
- `start /b` then Ctrl-C: the daemon keeps serving. Closing the window then writes `daemon stop`.
- Class 3: a second `mcuscoped` on the same port with a different `db_path` refuses: `127.0.0.1:18700 is already in use`, exit 1.
- Class 13: a config save while another process holds `config.toml` open. A 0.3 s hold succeeds on the retry. A 5 s hold gives 500 `config save failed: [WinError 5]` after 0.98 s and leaves no temp file. A session-export temp copy held open when its response ends stays on disk and is removed by the next start's sweep.
- Class 14: `pythonw -m mcuscope.daemon` starts, writes its startup log and stops on `mcu daemon stop`.
- Class 35: `mcu-sim --pty` exits 2 with stderr open, closed (`2>&-`) or a closed `PIPE`. `mcu tail -n 5` and `-n 5000` into a closed pipe (bash and cmd) exit 0, no traceback.
- R18-4: a USB-serial adapter on COM7 (`identify = false`), `POST /break` every 0.5 s, and the owner pulled it mid-run. The reader thread saw the unplug first (`read error: ClearCommError failed ... Access is denied`, then `disconnected`), so the next break answered 400 `port usb is not connected` rather than `break failed`. That is still an honest 400: no 500 and no false success. A break racing the reader's detection (the `SerialException` path) was not hit at 0.5 s spacing. The port reconnected on its own 17 s later (`after 5 failed attempts`).
  Second unplug, on COM7 held directly by a script with no daemon: once the unplug was seen, pyserial's `send_break` returned normally on the dead handle. `SetCommBreak` and `ClearCommBreak` both returned 0 with error 5, but pyserial ignores both results, so a break landing before the reader noticed the unplug answered 200, a false success. Fixed: `SerialLink.send_break` issues the break itself on Windows with both calls checked (`link._win32_break`) and always clears the line. The tests are in `test_serial_link_break_errors.py` and fail against a mutant that goes through pyserial. A live break on the connected adapter still answers 200.

### New defect (fixed): a losing concurrent start leaves its child alive, and the child can take over

Two `mcu daemon start` at once, from the plain venv: 4 of 4 rounds.
The losing CLI reports `another daemon is already serving at ... (pid W)` and exits 1 at `cli.py:2745`.
It does not wait for its own child. That child is still inside `CaptureLock.acquire`'s 2 s retry (and Store Python starts slowly, so it can reach the lock late).
If the winner is stopped inside that window, the loser's child gets the lock and the port and serves: a daemon the user was told did not start.
`mcu daemon stop` then exits 1 (`did not stop on a shutdown request ... names pid L, which is not the process serving it`), because /status still answers from the new process.
With a 5 s pause before the stop, the loser's child fails on the lock as intended. Its refusal lands whole in the `.err`, but only after the CLI has exited, so the failed start never shows its own tail.
Suggested fix: before that `die`, wait for `proc` to exit, bounded by the lock timeout plus startup slack. Name its pid if it outlives the wait.
The same code path runs on Linux; the window there is the same 2 s retry.
Fixed by `_reap_losing_start` (`cli_daemonctl.py`): the losing start stops its child at once and removes the pid record only while it names the child (fix batch `windows-fixbatch-race.md`).
It shows the lines appended to the shared stderr file since it began, which can include the winner's.
The loss is decided on the start id the daemon echoes (`X-Mcuscope-Start-Id`), so it holds for any launcher chain.
Tests: `test_cli_daemon_stop_scope.py`, `test_a_lost_race_*`, the start-id, refusal and Ctrl-C cases.
The live re-drive above (4 of 4, the winner stopped cleanly) ran the first version, whose loser exited on the lock by itself, so no terminate ran on Windows.
A loser's record landing last left the winner unrecorded, and off loopback `daemon stop` could not stop it; the winning CLI now rewrites the record to name its own child (class 7).
- [x] [CI] `test_a_lost_race_stop_reaches_the_interpreter_behind_the_venv_launcher` passes on `windows-latest`: terminating the venv launcher ends the interpreter. A skip fails the job (`ci.yml`). Passed on 3.10-3.13, run 36227639520.
- [ ] On the desktop (Store Python venv): `uv run python -m pytest tests/test_cli_daemon_stop_scope.py -k venv_launcher -rs` passes, not skipped.
- [x] [CI] `test_pidfile.py` on `windows-latest`: `test_neither_path_replaces_a_newer_record[True]`, `test_create_record_never_replaces` and the straddle tests pass, not skipped. It is the only run of the `os.rename` no-replace put-back. Passed on 3.10-3.13, run 36227639520.
- [ ] A held-open record: with a daemon running (throwaway TOML), hold its pid file open (`python -c "f=open(r'<pid file>'); input()"`) and run `mcu daemon stop`. It exits 0, the record is left in place (the aside rename is refused after the 0.9 s retry), and no traceback or `.aside` file appears.
- [ ] The J1 retry, in one process (sharing modes apply to its own handles too): write a record naming 999999 to a scratch path, open it, close it on `threading.Timer(0.3, f.close)`, and call `pidfile.remove_record_if(p, 999999)`: True after about 0.3 s. Positive control: with a 2 s timer it returns False after about 0.9 s and the record is still there.
