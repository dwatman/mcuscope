# Windows checklist

Merged from the registry legs' "Owed" sections and the fix-batch reports' "Needs Windows" lists, deduplicated.
Run after the registry fix batches land, so one Windows pass covers both.

CI coverage (`.github/workflows/ci.yml`): the `test` job runs the whole pytest suite, with node and MinGW (JS and firmware tests), on `windows-latest` for Python 3.10 to 3.13.
The `wheel smoke (windows)` job installs the wheel and runs only `--version`/`--help`.
So `[CI]` below means a named test file already runs on Windows in CI; everything else is manual.

Every manual daemon run uses `-c` on a throwaway TOML with its own `db_path`, a port other than 8558, and `MCUSCOPE_CONFIG_DIR`/`MCUSCOPE_DATA_DIR` in a scratch dir.

## Test files (CI covers them; check the latest Windows run is green)

- [ ] [CI] `test_cli_daemonctl.py` (the append handle's access mask, the ctypes fallback), `test_daemon_console.py` and `test_stdio.py` (console-close hold, ctrl handler), `test_server_export_pool.py` (export waiters), `test_status_ppid_serial.py`, `test_cli_daemon_stop_scope.py`. The fix reports named these by their old `test_*fixdiff*` names.
- [ ] Not covered until a batch acts: the `posix_only` cases in `test_cli_daemon_stop_scope.py` have nothing POSIX-specific (cli-tests drops the marker; then CI covers them).
- [ ] [CI] once the batches land: the new tests for R18-2 (bad `--url` on the WS path), R35-1/R35-2 (closed streams), R22-1, R13-1's corrupt-record cases.
- [ ] Not covered: R15-4, `mcuscoped.exe` serving from an installed wheel. firmware-packaging adds it to the wheel-smoke job; until then run by hand: `mcuscoped.exe --sim -c t.toml --port 18700`, `/status` answers, `mcu.exe --url http://127.0.0.1:18700 daemon stop` exits 0.

## Timing tests on a tick-granular clock (class 21)

- [ ] `pytest tests/test_e2e.py::test_lines_since_ts_excludes_what_predates_it` and `tests/test_assert.py::test_last_ms_window`, 20 runs each, on CPython 3.10, 3.11 and 3.12. Expect 0 failures once tests-daemon's R21-1/R21-2 fixes land.
- [ ] R21-3 to R21-6 fail only on a slow runner: after the fixes, run `test_wait_repeat.py`, `test_serial_link_devices.py`, `test_reconnect.py` 10 times each with the machine loaded (e.g. a parallel build). Expect 0 failures.

## Floor run (class 43)

- [ ] Whole suite on Python 3.10 with every dependency at its declared floor (`uv pip install --resolution lowest-direct -e ".[dev]"` in a fresh venv), including the JS and firmware files. Expect green; CI runs 3.10 with current dependencies only.

## SQLite plans (class 20)

- [ ] Each Windows CPython 3.10 to 3.13 bundles its own SQLite: run `plans_ver.py` (and `probe20.py`) from `~/tt-data/mcuscope-2026-09-24/registry-leg/15-28/` under each (copy the scripts over). Expect the same plans as Linux: `idx_plot_name_line` for one name, no `TEMP B-TREE` after the R20-1 fix.

## Manual daemon lifecycle

- [ ] `python -c "from mcuscope.cli_daemonctl import _open_append as o; import os; f=o('x.err'); print(os.fstat(f.fileno()).st_size)"` prints a size, no `WinError 5`.
- [ ] From a uv, pipx and plain venv install: `mcu daemon start -c t.toml`, then `mcu daemon restart`, then `mcu daemon stop`.
  - Expect start exit 0, the restart with no capture-lock refusal, stop exit 0.
  - Note which pid each line names (R17-4 / D-4): after the D-4 fix, the serving pid and the launcher.
- [ ] Two `mcu daemon start` at once on one host:port from a venv: both daemons' lines stay whole in the `.err`, and the failed start shows its own tail.
- [ ] The index-build wait: `mcu daemon start` on a large capture needing an index build; the CLI reads the `.err` while the daemon appends to it, and prints the build note.
- [ ] From cmd and from PowerShell: foreground `mcuscoped -c t.toml`, then close the window. The capture's sys rows end `daemon stop`, the session is closed, the pid record is gone.
- [ ] The same while a `wait=1` session export waits and a large `/lines/export` streams: all three still hold (the 3 s wait plus the finaliser fit in 4.5 s).
- [ ] Late-attach start (pythonw-based venv): same as above, and the ctrl handler is installed once.
- [ ] `start /b mcuscoped -c t.toml` in cmd, then Ctrl-C in that window: the daemon keeps running; closing the window then writes the `daemon stop` row.
- [ ] `mcu daemon start` then `mcu daemon stop`: unchanged (no console, no handler install).

## Probes owed since earlier rounds

- [ ] Class 3: two listeners on one port with `SO_EXCLUSIVEADDRUSE` (the probe in `test_sim_tcp.py` runs in CI; also start two `mcuscoped` on one port by hand: the second refuses).
- [ ] Class 13: `CreateFileW` handle rights and `fstat` on the append handle (covered by the `_open_append` line above); `replace_atomic` against a file another process holds open (config save while an editor has it); `_TempFileResponse` unlinking a download still open elsewhere (left for the next start's sweep, which removes it).
- [ ] Class 14: the `_stdio.py:203` consoleless residual: `pythonw -m mcuscope.daemon -c t.toml` starts, writes its startup log, and stops on `mcu daemon stop`.
- [ ] Class 35: `mcu-sim --pty` with stderr closed (`mcu-sim --pty 2>&-` from Git Bash, or a Python `Popen(stderr=PIPE)` closed at once) exits 2, not 1, after R35-2; the EINVAL translation (`_stdio.translate_closed_pipe_errors`) under a closed stdout for `mcu tail -n 5 | exit`.
- [ ] Class 66: Ctrl-C and Ctrl-Break in a foreground `mcuscoped` console stop it gracefully (sys row `daemon stop`).
- [ ] R18-4: `POST /break` on a USB-serial adapter unplugged mid-run answers 400 `port X: break failed` (pyserial raises `SerialException` there).
- [ ] R27-14: force the `_open_append` failure (a read-only `.err` path) and read the warning: it names the real error, not `WinError 0`.
