# Fix-diff leg 2: cli slice (2026-09-24)

HEAD: `b889e2b8fb848866054da6ba09cd2e651d9f69c7` (diff `b994076..4462986`; `4462986..HEAD` touches no reviewed file).
Scratch: `~/tt-data/mcuscope-2026-09-24/fixdiff2/cli/`.

- `copy/host` is a private copy (`git archive HEAD`), run with `PYTHONPATH` on the repo venv; each probe printed the imported `mcuscope` path.
- `live/` is a throwaway `--config` daemon on port 18731 with its own `db_path` and `MCUSCOPE_*_DIR`.

## Findings

### 1. HIGH (Windows, reasoned from the docs): every Windows `daemon start` loses its stderr log, and with it the index-build wait
- Where: `cli_daemonctl.py:77-81` (`CreateFileW(FILE_APPEND_DATA | SYNCHRONIZE)`), `cli.py:2621` (`os.fstat(err_fh.fileno())`).
- Defect: the handle carries no `FILE_READ_ATTRIBUTES`, and `os.fstat` needs it, so the fstat raises `PermissionError` [WinError 5].
  - CPython 3.13 `_Py_fstat_noraise` fails if `GetFileInformationByHandleEx(FileBasicInfo)` fails. `NtQueryInformationFile` documents that FileBasicInformation needs FILE_READ_ATTRIBUTES.
- Scenario: `mcu daemon start` on Windows prints `warning: cannot write the daemon log ...: [WinError 5] Access is denied`. The `except OSError` at `cli.py:2622` then sends the daemon's stderr to DEVNULL.
  - No `.err` file, and no failure tail on a failed start.
  - `_index_build` gets `err_path=None`, so an older capture's start is stopped at `--timeout` in the middle of its build. The next start then rebuilds from zero, which is the loop the wait was added to break.
- Test gap: `test_cli_fixdiff_append.py:35` fakes `open_osfhandle` with a POSIX `os.open` fd, so fstat always works. The exact-mask assertion (`:53`) freezes the bug in place.
- Confirmed: reasoned only. I read the CPython 3.13 `fileutils.c` Windows branch and the MS `NtQueryInformationFile` access table. Not driven, since there is no Windows or wine here.
- Fix: add `FILE_READ_ATTRIBUTES` (0x0080) to the access mask. Without `FILE_WRITE_DATA`, appends still hold. Update the test's mask.
  - Windows leg one-liner: `python -c "from mcuscope.cli_daemonctl import _open_append as o; import os; f=o('x.err'); print(os.fstat(f.fileno()).st_size)"`.

### 2. LOW (Windows): a record corroborated only by `ppid` is waited on and then signalled after the daemon has already exited
- Where: `cli_daemonctl.py:324` (`recorded in _serving_pids(body)`), `:326` (`_wait_daemon_gone(s, pid, ...)`, which judges on the pid alone), `:335` (`_signal_daemon_stop(pid)`).
- Defect: `ppid` shows only that the recorded process is the daemon's parent, not that it is a launcher shim that exits with it. The stop waits for that parent to exit, which a non-shim parent never does, and then TerminateProcesses it.
- Scenario: a crashed daemon's record names X, and X is recycled as a `cmd.exe`. From that `cmd.exe` the user runs `python -m mcuscope.daemon` (no redirector). `claim` leaves the live foreign record alone, so it names X, the new daemon's `ppid`.
  - `mcu daemon stop`: `/shutdown` is accepted and the daemon exits. X never exits, and after 10 s X is terminated. The output is `stopped mcuscoped (pid X)`, exit 0.
  - `pidfile.py:22-28`'s "can neither ... kill the innocent process" is false for this case.
- Confirmed: driven, with Windows simulated. `copy/host/tests/test_zz_probe_ppid.py` patches `cli_daemonctl.sys.platform` to win32 against a fake daemon reporting `ppid` = a live `sleep` child. Result: rc 0, `stopped mcuscoped (pid <victim>)`, victim returncode -15.
- The batch's claim that the `/status`-first `_wait_daemon_gone` was "redundant" holds for `pid` corroboration only. No test covers a ppid-corroborated stop.
- Fix: when only `ppid` corroborates, judge "gone" by `/status` going quiet, and signal the shim only while `/status` still answers after the grace. Add the probe as a test.

### 3. LOW: the index-build wait has no ceiling, and its notice match is a bare substring
- Where: `cli.py:2664-2681`, `cli_daemonctl.py:113-117`.
- Defect 1: while the notice is present and the child is alive, `start` waits with no bound, and only Ctrl-C ends it. SPEC 4 documents this, but the brief asks for a bounded wait.
  - A build wedged on I/O (a stalled NFS or USB disk) hangs `mcu daemon start` for an agent indefinitely.
- Defect 2: `"building index" in line` also matches any stderr line that quotes a user path containing it. For an older capture, the auto_vacuum and journal-mode warnings quote `db_path` on every start.
  - Driven: `_index_build` on `capture /srv/building index/cap.db has auto_vacuum=0, ...` returns `('/cap.db has auto_vacuum=0, not INCREMENTAL: ...', False)`.
  - A daemon under such a path that never answers then hangs `start` forever, behind a false "building index" note.
- Owner should pick the ceiling. My recommendation: a cap derived from the capture size, or a flat cap such as 30 min, then abandon as usual.
- Match `": building index "` and `": built index "` (the store's `capture %s: ` prefix) rather than the bare words. The fix can also be anchored on `rpartition(": ")`.
- Nit: the `proc.poll() is not None` clause in the deadline branch (`cli.py:2671`) is redundant, because the loop body polls anyway. A mutation deleting it passed all 5 tests in `test_cli_start_index_build.py`. Delete the clause, or give it a test.

### 4. LOW (Windows, reasoned): `daemon restart` can race an older daemon's capture lock
- Where: `cli_daemonctl.py:324-326`; `cli.py:2758` (`restart` runs stop, then start at once).
- Defect: a pre-0.5.0 Windows daemon reports no `ppid`, so its shim record is no longer corroborated and the stop is judged by `/status` going quiet. Before this diff, the stop waited for the shim to exit.
  - `/status` goes quiet when uvicorn stops listening, before the lifespan finaliser closes the store and releases the `CaptureLock`.
- Scenario: the upgrade path, where a user updates the package and runs `mcu daemon restart` against the still-running older daemon.
  - The new daemon can hit `CaptureLock` (`daemon.py:430-436`) and exit 1, so the restart reports a failed start.
  - The same window already existed for a no-record stop (pre-existing), and now also covers stale records.
- Confirmed: reasoned from the code path, not driven.
- Fix: after a `/status`-judged stop in `restart`, wait (bounded) for the capture lock of the `db_path` from `/status`, when that file is local. Or have `_start_daemon` retry once on a lock refusal.

### 5. NIT: SPEC 4 `daemon` row overstates the start check
- Where: SPEC 4 `daemon` row ("fails ... when `/status` names neither its child as `pid` nor, on Windows, as `ppid`") against `cli.py:2700` (`if serving and ...`).
- Defect: a body with neither field passes (the pre-0.1.2 case). Add "(a daemon reporting neither is accepted)", or drop the `serving and` guard.

### 6. NIT: stale comment
- `cli.py:988`: "judged on the ports attached". It is now attached plus stored (`_stream_port_column`).

## Checked, nothing found

- Linux live stop and start, driven on the throwaway daemon (`live/`):
  - `daemon start` then `stop` gives `stopped mcuscoped (pid N)`, rc 0.
- Index-build wait, live: a 6M-row capture with `idx_lines_port_chan_id` dropped, started with `--timeout 0.5`.
  - It printed the build note once, waited through the 11.6 s build, came up, and rc was 0. Stop rc 0.
  - The store's notices reach the `.err` file (logging's last-resort handler, line-buffered stderr).
- Stale-record corroboration on POSIX: only `pid` corroborates; `_serving_pids` gates `ppid` on win32.
  - `_remove_pid_record` still guards against a newly claimed record.
  - Texts and JSON (`pid: null`) are consistent across the four report branches.
- `daemon start` `ppid` acceptance cannot mistake another daemon: no other daemon's parent can be this start's new child.
- `_stdio.install_console_ctrl_handler`:
  - The idempotent guard sits after the platform check.
  - `keep_ctrl_c_ignored` skips only the flag clear.
  - Our handler registers after the CRT's (installed at interpreter start), so it runs first (LIFO) for CTRL_C, BREAK and CLOSE.
  - `interrupt_main` reaches uvicorn's SIGINT handler.
  - `have_console()` is True off Windows, and the installer returns False there, so the daemon's call is inert on POSIX.
  - A `mcu daemon start` child (DETACHED, no console) skips it.
- `console_entry` `stdout_was_closed()` gate: POSIX-only by construction, since the function is always False on Windows.
- `render.one_line`/`fmt_line` with a non-str `raw`; `assert` check lines; the `AttributeError` added to the follow's per-row guard (counted by `drops.bad`, so not silent).
- `_accepts_tcp`: the default port and IPv6 host parsing match websockets' dial; `TimeoutError` and `asyncio.TimeoutError` are caught ahead of `OSError`.
- `_dump_follow` give-up: exit 1 for a read timeout, 3 for a connect timeout.
- `_port_column`/`_stream_port_column` against the server's `_several_ports`:
  - Same rule (attached or stored > 1, port `""` excluded on both sides).
  - `log export` sends text to the daemon whenever it reports `stored`.
  - `-p` and `--json` suppress the column on both paths.
- `attach`: `/ports` shape guards; the note compares `serial <SN>` and device forms; `serial_link.status()` now reports `serial_number`.
- `conftest._isolate_output_state` resets `_JSON_MODE`; `test_link_fixdiff_console.py` resets `_ctrl_handler_ref` in every test that installs.
- The `AI_GUIDE` hunks and the SPEC 4 hunks (port column, exit-1 stall rule, `can dump -f`, text escaping, attach note) agree with the code, apart from finding 5. README examples are marked as firmware commands.
- Test files run one at a time in the copy, all green:
  - `test_cli_daemon_stop_scope` 14, `test_cli_start_index_build` 5, `test_cli_fixdiff_append` 2, `_attach` 6, `_follow_timeouts` 8, `_prompts` 7, `_rows` 3, `_stdio` 3;
  - `test_link_fixdiff_console` 5, `test_port_column_stored` 11, `test_status_ppid_serial` 3, `test_cli_contract` 27, `test_pidfile` 19, `test_cli_read_scope` 16.
