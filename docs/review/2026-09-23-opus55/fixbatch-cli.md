# Fix batch: cli (fix-diff leg, 2026-09-24)

HEAD: `b994076c88c875ad43c30789cda6039d147b7abe` (uncommitted work on top; other batches' edits are in the same tree).
Scratch: `~/tt-data/mcuscope-2026-09-24/fixbatch-cli/`.

- `host/` is a private copy; `mut.py` is the revert harness, with its results in `mut.log`.
- `runs.log` holds the per-file test runs; `live/` holds the live daemon drives.
- `second-opinion-port-column.md` is the second opinion on finding 3.

## Question (blocks finding 3)

Recommended: the server and store batch adds a port column to `/lines/export?format=text` and a `stored` port list to `GET /ports`, and the CLI then judges streams on attached plus stored ports. The alternative is a CLI-only fallback.

- Details are under finding 3.

## Per finding

### 1 (HIGH) `daemon stop` signals a stale record's pid
- `cli_daemonctl.py:298`: `_stop_running_daemon(s, body, pid_path, recorded, quiet)` signals or waits on `recorded` only when `/status` corroborates it.
  - Corroborated means `recorded in _serving_pids(body)` (`:270`): `pid`, plus `ppid` on win32 only.
  - Otherwise the stop is `POST /shutdown`, judged by `/status` going quiet. A stale record is still tidied through `_remove_pid_record`.
- New texts:
  - refused shutdown: `its pid record <path> names pid N, which is not the process serving it, so no process was signalled`;
  - success: `stopped mcuscoped at URL (its pid record named pid N, not the serving process: asked it to shut down, signalled nothing)`.
- `cli.py:2743,2770`: both call sites pass `body`. `_serving_pid` is replaced by `_serving_pids`.
- `_wait_daemon_gone` is unchanged. Rewriting it to judge by `/status` first was redundant once only a corroborated pid reaches it: no test caught its revert, so I restored the original.
- `pidfile.py:22-28`: the docstring promise is corrected. `AI_GUIDE` `daemon stop` line (`cli.py:3034`).
- Tests (`test_cli_daemon_stop_scope.py`):
  - a stale record naming a live victim (a `python -c sleep` child): the shutdown is accepted, the victim survives, the record is removed;
  - the same with the shutdown refused: exit 1 and the victim survives;
  - a dead-pid record plus a 0.4 s slow shutdown: exit 0 (the second face);
  - the `_serving_pids` platform gate;
  - the existing record-names-the-pid test remains the positive control.
- Repro before and after: the reviewer's `test_zz_probe_stop.py`, run in my copy.
  - Before: victim `-15`, dead-pid face exit 1.
  - After: victim alive (`None`); the dead-pid face passes at the real 10 s grace. Its 1.5 s shutdown exceeds the fixture's 1.0 s grace, so it cannot pass under the fixture.
- Live, on a `--sim` daemon with a throwaway config and data dir (`live/`):
  - start then stop gives `stopped mcuscoped (pid N)`;
  - with the record overwritten to name a live `sleep 300`, stop exits 0 with the new text, the `sleep` stays alive, the daemon is gone and the record is removed.
- Revert: 6 mutations, all KILLED (corroboration, both texts, stale-record tidy, win32 gate, ppid read).

### 2 Follow siblings at exit 3
- `cli.py:1261`: `except (TimeoutError, asyncio.TimeoutError)` sits ahead of `OSError`.
  - Exit 1 `accepted the connection but stopped answering` when `_accepts_tcp` (`:1134`) opens TCP to the same host:port (default 80/443, as websockets dials).
  - Otherwise exit 3 unreachable.
- `cli.py:2205`: in the `can dump -f` give-up, a `TimeoutException` other than `ConnectTimeout` exits 1 with `accepted the request but stopped answering for 30s`.
- Tests: `test_cli_fixdiff_follow_timeouts.py`:
  - a real accept-and-stall listener (`open_timeout` shortened);
  - a fake handshake timeout raising `asyncio.TimeoutError`, against both a free port (3) and a listening one (1);
  - the probe's dialled address (3 URL forms);
  - `can dump -f` with `ReadTimeout` (1) and with `ConnectTimeout` (3).
- The file passes on the 3.10.20 / websockets 14.0 floor venv.
- Revert: 6 mutations KILLED on 3.13. Narrowing the arm to `TimeoutError` alone is KILLED on the 3.10 floor; on 3.13 the two are one class.

### 3 `[port]` missing for detached-board history: NOT DONE (question above)
- A CLI-only fix is either lossy or costly:
  - "always the column" puts `[sim]` on every single-board row and pages every text export (3000 requests per 3M rows);
  - judging on rows as they stream leaves early rows unattributed.
- Second opinion (opus-high, `second-opinion-port-column.md`): the root cause is the daemon's text export, which has no port column.
  - Proposed rule: without `-p`, text carries `[port]` when more than one port is attached or has stored rows.
  - Needs `store.stored_ports()` (index seeks, plan checked), `GET /ports` `stored`, and `_text_lines(rows, show)`.
  - CLI side: the `_port_column` stream branch uses `attached > 1 or stored > 1`, and `log_export` drops the paged-path gate.
- My view: agree.
- CLI-only fallback if the server change is refused:
  - `tail -f` decides after the backfill snapshot;
  - `log export` without `-p` always shows the column, rendered client-side from the daemon's `jsonl` stream.

### 4 README examples always fail
- `README.md:222,251`: `reset`/`BOOT OK` replaced by `selftest`/`SELFTEST OK` (the guide's example), marked as "a command your firmware adds". The sim still refuses it; that is the stated premise.

### 5 `attach` crash on a non-object `/ports`
- `cli.py:391-393`: `listed` must be a dict and `ports` a list.
- Test: `test_cli_fixdiff_attach.py` (a JSON list, and `{"ports": 5}`). Revert: 2 mutations KILLED.

### 6 Non-string `raw` ends `tail -f`
- `render.py:26`: new `one_line(raw)` = `str(raw).translate(_BREAKS)`, used by `fmt_line`, so a null `raw` prints `None` as on 6e4f6f7.
- `cli.py:1219`: the per-row follow guard also catches `AttributeError`: under `--decode`, `row["raw"].startswith` raised it past the guard.
- Tests: `test_cli_fixdiff_rows.py` (null `raw` with and without `--decode`). Revert: 2 mutations KILLED.

### 7 (Windows) `.err` append in the spawned daemon
- `cli_daemonctl.py:58` `_open_append`:
  - POSIX: `open(path, "ab")`;
  - win32: `CreateFileW(FILE_APPEND_DATA|SYNCHRONIZE, share rwd, OPEN_ALWAYS)`, then `msvcrt.open_osfhandle`.
- `cli.py:2606`: `err_start` comes from `fstat().st_size`, since `tell()` on an append-only handle is not needed.
- Tests: `test_cli_fixdiff_append.py` (fake kernel32 and msvcrt: the access mask, `OPEN_ALWAYS`, the share flags; an invalid handle falls back to DEVNULL with the warning).
- Revert: 4 mutations KILLED, including `err_start = 0` against the existing append test.
- The real inheritance is under "Needs Windows".

### 8 Declining at a real prompt untested
- Tests: `test_cli_fixdiff_prompts.py`, with a tty-patched stdin answering `n`, EOF or `yess`:
  - purge and `session delete --data` each give `cancelled`, exit 1, stdout empty;
  - purge sends only the dry-run request; session delete sends no DELETE.
  - Positive control: `y` on session delete.
- `test_cli.py:1157` renamed to `test_purge_without_yes_on_a_piped_stdin_deletes_nothing`.
- Revert: the `if False:` mutation and a `startswith("y")` mutation are both KILLED.

### 9 (docs) "pipes and files carry the captured bytes"
- `AI_GUIDE` (`cli.py:2846`): line boundaries are escaped in text output everywhere, other controls only on a terminal, and `--json`/`--csv` carry the bytes as captured.
- README, `host/README.md` and `CLAUDE_SNIPPET.md` make no such claim, so they are unchanged. SPEC wording is below.

### 10 (Windows) venv `daemon start` reports failure
- `cli.py:2670`: success when `proc.pid in _serving_pids(body)`, that is `/status` `pid`, or on win32 `ppid`.
- Depends on `/status` reporting `ppid` (`server.py`, not mine; see "Not done").
- Tests (`test_cli_daemon_stop_scope.py`, `sys.platform` patched):
  - win32 with `ppid` equal to the shim: exit 0;
  - still refused on win32 with no `ppid`, on win32 with another `ppid`, and on linux with `ppid` equal to the shim.
- Revert: KILLED.

### 11 Repair warning on a POSIX `>&-`
- `_stdio.py:414`: no warning when `stdout_was_closed()`, since `mcu` reports `cannot write output ... closed when mcu started` itself.
- Tests: `test_cli_fixdiff_stdio.py` (`>&-` gives no WARNING; positive control `<&-` still warns).
- Revert: 2 mutations KILLED, run with `PYTHONPATH` on the copy (the child otherwise imports the editable install).

### 12 Attach retarget note
- `cli.py:396-403`: `was` is `serial <SN>` when the row reports `serial_number`, else its `device`; `now` is `serial <SN>` for `--serial`. The note prints when they differ.
- Tests: `test_cli_fixdiff_attach.py`:
  - a rebind from serial to the resolved device is noted;
  - an unconnected serial port is named as a serial;
  - the same serial re-attached (connected or not) is quiet.
- Revert: 2 mutations KILLED.
- Depends on `GET /ports` rows carrying `serial_number` (`serial_link.py` `status()`, not mine).
  - Live today it is absent, and a same-serial re-attach prints `note: b2 was attached to ZZ99; it now names serial ZZ99`.
  - That was already false before this change (`... now names ZZ99`).

### 13 `assert` check lines print `raw` unrendered
- `cli.py:1490,1496`: `one_line(...)`.
- Test: `test_cli_fixdiff_rows.py` (U+2028 in the expect line, VT in the forbid line). Revert: 2 mutations KILLED.

### 14 Stop test drives an impossible state
- `test_cli_daemon_stop_scope.py`: D01 now calls `_stop_running_daemon(s, {"pid": 4242})`, with no record. Test-only.

### 15 `_JSON_MODE` not reset
- `conftest.py:136`: reset in `_isolate_output_state`.
- Test: `test_cli_fixdiff_stdio.py::test_the_isolation_fixture_clears_a_json_mode_main_left_set`.
  - It runs `main(["--json", "ai-guide"])`, asserts the mode leaked, calls the fixture via `__wrapped__`, and asserts it is cleared. No test order is involved.
- Revert: KILLED.

### Firmware F4
- `AI_GUIDE` (`cli.py:2951`): `"!e event p overflow": a !p line over 255 bytes was cut at a space (its trailing pairs lost); with nothing left past its header, the notice arrives alone`.

### Link F1 follow-up
- `_stdio.py:54`: `install_console_ctrl_handler(keep_ctrl_c_ignored=False)`.
  - It is idempotent (`:70`, after the platform check).
  - It calls `SetConsoleCtrlHandler(None, False)` only when `not keep_ctrl_c_ignored` (`:95`).
- `daemon.py:401`: `_hold_console_close` calls it with `keep_ctrl_c_ignored=True`. The private ref guard is dropped; `have_console()` is kept.
- `test_link_fixdiff_console.py`:
  - the daemon test expects `install {'keep_ctrl_c_ignored': True}`;
  - the late-attach daemon case is replaced by installer tests on a fake kernel32: a second install keeps the first thunk and registers once; `keep_ctrl_c_ignored` never clears the flag; the positive control shows a plain install still clears it.
- Revert: 5 mutations KILLED (idempotency, flag always cleared, flag never cleared, daemon without the flag, console guard).

## Verification run
- Each file run alone in the shared tree (`runs.log`): 41 files, all green except `test_regressions.py::test_line_ids_are_not_reused_after_the_table_empties` (`9 > 9`).
  - It passes in my copy, whose `store.py` predates the store batch's current edits. Not mine; route it to the store batch.
- `test_cli_daemon_stop_scope.py`, `test_pidfile.py`, `test_cli_contract.py`, `test_cli_fixdiff_attach.py` and `test_cli_fixdiff_follow_timeouts.py` were re-run after the last edits: green.
- `uv run python -m ruff check .` in `host/`: clean.

## SPEC 4 wording (SPEC not edited)
- `daemon` row: replace "`stop` asks `POST /shutdown` and signals a pid only when a local pid record for that host:port names it, so a daemon on another machine (a remote `--url`, a tunnelled port) is never signalled: with no record, success is `/status` going quiet;" with:
  - "`stop` asks `POST /shutdown` and signals a pid only when the local pid record for that host:port names the process `/status` reports (its `pid`, or on Windows its `ppid`, the venv launcher `start` recorded), so neither a daemon on another machine (a remote `--url`, a tunnelled port) nor a process that inherited a stale record's pid is signalled; otherwise success is `/status` going quiet;"
- `daemon` row, after "`start` prints the web UI URL": "; `start` succeeds only when `/status` names its child as `pid` (or, on Windows, as `ppid`)".
- `attach` row, append: "; re-pointing an existing alias at another target prints `note: <alias> was attached to <old>; it now names <new>` on stderr, a serial binding written `serial <SN>`".
- SPEC:1137: "Text output shows each line boundary inside a row (CR, LF, VT, FF, FS, GS, RS, NEL, U+2028, U+2029) as `\xNN`/`\uNNNN` on every sink, so one row is one line; on a terminal it also shows every other C0 or C1 control byte as `\xNN` and keeps SGR colour sequences (`ESC [ ... m`), stderr included; `--json` and `--csv` carry the captured bytes."
- SPEC:1127 (exit-code paragraph): "A daemon that accepts a request or a WebSocket connection but never answers ... is exit `1` on every command, `tail -f` and `can dump -f` included, ...".
- SPEC 3.4 (for the server batch):
  - the `/lines/export` `text` sentence adds "each line boundary inside `raw` shown as `\xNN`/`\uNNNN`";
  - `GET /status` gains `ppid`;
  - `GET /ports` rows gain `serial_number` (null for a device attach).

## Proposed CHANGELOG lines
- Fixed: `mcu daemon stop` no longer signals a process named by a stale pid record; a recorded pid is signalled only when `/status` reports it as the serving daemon.
- Fixed: `mcu tail -f` and `mcu can dump -f` against a daemon that accepts and then stops answering exit 1, not 3 (and not a traceback on Python 3.10).
- Fixed: `mcu attach` no longer crashes on a malformed `/ports` answer; its retarget note names a serial binding and notes a rebind from serial to device.
- Fixed: a row whose `raw` is not a string no longer ends `mcu tail -f`; `mcu assert` check lines keep device text on one line.
- Fixed: `mcu` run with stdout closed reports only `cannot write output`, without a "no console" warning.
- Fixed (Windows): the daemon's stderr file is appended to by the spawned daemon as well; `mcu daemon start` from a venv reports success (with a daemon reporting `ppid`).
- Fixed (Windows): the console-close hold keeps an inherited ignore-Ctrl-C (`start /b`) and is installed at most once.
- Docs: README agent examples use a firmware command; the guide names `!e event p overflow` and says text output escapes line boundaries everywhere.

## Not done
- Finding 3: see the question.
- `server.py` `/status` must add `"ppid": os.getppid()` for findings 10 and 1 (Windows shim).
  - Until then, `mcu daemon start` from a Windows venv still exits 1.
  - Until then, a shim record is uncorroborated: the stop is graceful `/shutdown` only, with no hard-kill fallback.
- `serial_link.py` `status()` must add `"serial_number": self.serial_number` for finding 12 to take effect.
- SPEC and CHANGELOG are not edited (wording above).

## Needs Windows
- F7: two concurrent `mcu daemon start` on one host:port from a venv; both daemons' lines stay whole in the `.err`, and a failed start still shows its own tail.
- F10: `mcu daemon start` then `mcu daemon stop` from a uv, pipx or venv install, once `/status` has `ppid`. Expect start exit 0 and `stopped mcuscoped (pid <shim>)`.
- F1: the `posix_only` stop tests in `test_cli_daemon_stop_scope.py` have nothing POSIX-specific; run them there.
- Link F1: `start /b mcuscoped`, then Ctrl-C does not stop it; closing the window writes the `daemon stop` row.

## Doubts
- The second opinion ran on opus-high, not fable-low: the owner's CLAUDE.md says not to use fable-low while Opus outclasses it.
- Finding 2 adds a TCP probe (up to 2 s, only on a handshake timeout).
  - The report's fix (straight to 1) would call a blackholed remote host "accepted the connection".
- Finding 11 also silences the warning for `mcuscoped`/`mcu-sim` with stdout closed. Their stdout carries only the banner.
- A pre-0.1.2 daemon (no `pid` in `/status`, no `/shutdown`) can no longer be stopped through its record.
- Finding 15's test reaches the fixture through `__wrapped__`, a pytest implementation detail (present in 9.1.1 and in the older `functools.wraps` form).
- Latent, not in scope:
  - `mcu cmd` prints a response's `data` without line-boundary escaping (`emit_cmd_result`);
  - a `tail -f`/`wait` that starts with the port column off never shows it for a board attached mid-stream (second opinion);
  - the web UI's "all ports" pane text export has no `[port]` with two boards attached (second opinion, read from `terminal.js:594-621`, not driven).
- My first scripted edit for finding 7 wrote `cli_daemonctl.py` before a `cli.py` anchor failed. It was caught, and `cli.py` was applied separately; both files were checked.
