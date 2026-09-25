# Batch cli-tests

Test doubles and assertions in the CLI suite that let a broken CLI pass. No source file is edited; a fix that needs one is reported, not made.
Each fix is revert-verified with the mutant the finding names (group A/B1/B2 notes in `registry-15-28.md` class 27 give the exact mutants).

## Files

- Tests you may edit: `test_cli.py`, `test_cli_contract.py`, `test_cli_attach.py`, `test_cli_daemon_stop_scope.py`, `test_cli_daemonctl.py`, `test_cli_version_gate.py`, `test_cli_ux.py`.
- No SPEC or AI_GUIDE section.

## Findings

### R27-4 MEDIUM (FB1-1): "still answering after stop" tested only on the pid-None path
- Checked: test_cli_daemon_stop_scope.py:142-153 stubs `_wait_daemon_gone` to ignore its pid and `_status_body` to answer, with no pid; the check at cli_daemonctl.py:380 exists for the corroborated-pid case.
- Fix: a case with body pid 4242, `_wait_daemon_gone` returning `pid == 4242`, `_status_body` still answering: exit 1, `still answering`.
- Revert-verify: `if pid is None and _status_body(...)` fails it.

### R27-5 MEDIUM (FB2-1): the gated request's fields are recorded but never asserted
- Checked: test_cli_version_gate.py:203-217 asserts only paths.
- Fix: after the path check, read the gated request's JSON body or params and assert the typed value (`eol`, `repeat_ms`, `port`). Do not rely on the recorder's 200 answers (a real daemon would refuse some).
- Revert-verify: the leg's mutant (eol None, no repeat_ms, no port) fails 5 cases.

### R27-14 LOW (FB1-2): Windows ctypes double ignores `use_last_error` and `restype`
- Checked: test_cli_daemonctl.py:183-210.
- Fix: the fake `get_last_error` answers 5 only when `WinDLL` was built with `use_last_error=True`; `CreateFileW` returns a raw `-1` unless `restype is wintypes.HANDLE`.
- Revert-verify: M1 (`use_last_error=False`) and M2 (no `restype`) each fail.

### FB1-3 LOW: "the spawned daemon is dealt with" not asserted
- Checked: test_cli_daemonctl.py:365 asserts text both outcomes share.
- Fix: `_FakeProc` records `terminate`/`wait`; assert both ran and the "stopped it" wording.
- Revert-verify: M3 (`_abandon_daemon` skips terminate) fails.

### R27-16 LOW (FA-3): `_StoppableDaemon` treats any POST as a shutdown
- Checked: test_cli.py:1776.
- Fix: `do_POST` stops only on `/shutdown`, 404 otherwise; `do_GET` answers `/status` only.
- Revert-verify: `probe("POST", "/shutdownX")` fails the four cases.

### R27-17 / R63-1 LOW (FA-4): detach miss canned as 404, daemon answers 400
- Checked: test_cli_attach.py:129-130; `DELETE /ports/<unknown>` is 400 (server.py:1174).
- Fix: canned status 400.
- Revert-verify: `cli_client.fail` rewriting a 400's message fails it.

### R27-18 LOW (FA-5): "older daemon" wait test passes a gate a real older daemon fails
- Checked: test_cli_contract.py:244 answers `/status` with the wait body (no version).
- Fix: `/status` answers an unorderable version (`0.4.0.dev1+local`), the only real route to a missing `sends`; the docstring says so.

### R27-19 LOW (FA-2): Ctrl-C delivered as an in-coroutine KeyboardInterrupt
- Checked: test_cli.py:2592 `_ScriptedWS` raises `KeyboardInterrupt` from `recv()`; on 3.11+ a real SIGINT cancels the main task.
- Fix: a `recv` that blocks, and `_thread.interrupt_main()` from a timer thread (what `asyncio.Runner` turns into cancellation), expecting exit 0.
- Revert-verify: moving the `KeyboardInterrupt` arm inside `run()` fails it (exit 1 `interrupted`).

### R27-20 LOW (FA-6): `_DeadPipe.fileno()` answers the runner's fd 1
- Checked: test_cli.py:409-410.
- Fix: `fileno()` returns an fd the test opened on `os.devnull`, closed at teardown.
- Verify: `pytest -s` over `-k rich_renders` keeps the session summary.

### R27-21 / R78-5 LOW (FA-1): the paged-export death test never takes the paged path
- Checked: test_cli_contract.py:118-121 uses `--limit 0`, which is the streamed path; the patched `_iter_pages_asc` is never called.
- Fix: delete it; `test_cli_read_scope.py::test_a_paged_export_that_dies_mid_walk_leaves_no_file` covers the guard (the leg drove that).

### FB2-2 LOW: `probes == [] or cmd == "restart"` after the loop is always true
- Checked: test_cli_ux.py:183.
- Fix: assert `probes == []` inside the loop for `start`.
- Revert-verify: a `_status_body` call at the top of `daemon_start` fails it.

### Also (from `fixbatch-cli.md` "Needs Windows", not a registry finding)
- The `posix_only` stop tests in `test_cli_daemon_stop_scope.py` have nothing POSIX-specific: drop the marker so CI's Windows job runs them. Keep it on any case that does rely on POSIX (say which).
