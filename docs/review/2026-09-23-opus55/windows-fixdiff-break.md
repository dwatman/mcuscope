# Fix-diff review: Windows break (1b3c97b), symlink skip (00de8c8), docs (89a6af3)

HEAD reviewed: 89a6af3ec4b19ca798ab384a5f2005936d88ddf2. Review only: no tracked file edited.

## Findings

### F1 LOW: the Win32 break tests never run on Linux, and their fixture state runs pyserial's close on a fake handle
- `host/tests/test_serial_link_break_errors.py:57` sets `ser.is_open, ser._port_handle = True, 1234` on a real `serialwin32.Serial` and never resets them.
  - When the object is collected, `io.IOBase.__del__` calls `close()`, which goes into `_close()` because `is_open` is True.
  - Today that stops at `self._orgTimeouts` (an AttributeError) before `CloseHandle(1234)`. CI run 36149423798 (windows-latest, py3.13) logs 3 `PytestUnraisableExceptionWarning`s, one per test.
  - If `_orgTimeouts` ever exists (a pyserial change, or a test that sets it), `CloseHandle(1234)` would close whatever real handle 0x4D0 is in the pytest process. Windows ignores the low 2 bits of a handle value.
- `:61` `win32_only` skips all three tests off Windows, so Linux CI covers none of `_win32_break`.
  - `from serial import win32` cannot be imported on Linux (no `ctypes.WinDLL`). A fake `serial.win32` in `sys.modules` fixes that, plus `ctypes.WinError` patched with `raising=False` and `link.sys` patched to a namespace with `platform="win32"`. With those, the real `serialwin32` imports and both the fix and the mutant run on Linux. The scratch harness below does this.
- Fix:
  - Use `monkeypatch.setattr(ser, "is_open", True)` and `monkeypatch.setattr(ser, "_port_handle", 1234)`. The undo list keeps `ser` alive and closes it with `is_open=False`. In scratch this took the run from 4 warnings to 1 (the unrelated starlette deprecation).
  - Replace the `skipif` with that fake-module fixture so the tests run on both OSes. Keep one Windows-only case that uses the real `serial.win32`.
- Fixed: the Win32 tests run on every OS against a fake pyserial port and fake kernel32; no fake handle reaches pyserial. See `windows-fixbatch-break.md`.

### F2 LOW: three changed branches in `_win32_break` have no test (revert-verified, table below)
- `host/mcuscope/link.py:223`: deleting `time.sleep(seconds)` passes every repo test. A 0 ms break would answer 200.
- `link.py:219`: passing the wrong handle (`handle = 0`) passes, because the test lambdas ignore `h`.
- `link.py:217-218`: deleting the `is_open` check passes.
  - Without that check, `_port_handle` would be None, so SetCommBreak fails and the call still raises "SetCommBreak failed", a 400.
  - The daemon cannot reach it anyway: `_close_link_locked` sets `_link=None` under the same `_write_lock` the break holds.
- Also untested: an exception during the sleep still clears the line, and the set-fails/clear-succeeds pair `(0, 1)`.
- Fix: record `h` and the sleep argument in the doubles and assert them; add `(0, 1)` and an interrupted-sleep case. For the `is_open` check, pick one: keep it and add a test, or delete it and let `_port_handle` None fail through SetCommBreak. Keeping it mirrors pyserial's `PortNotOpenError`.
- Fixed: the fake kernel32 records handle, sleep and order; `(0, 1)` and an interrupted hold added. The `is_open` check is deleted: a closed port's `_port_handle` None fails SetCommBreak. See `windows-fixbatch-break.md`.

### F3 LOW: the test does not pin that `_port_handle` exists in the installed pyserial
- `link.py:219` reads the private `ser._port_handle`. `pyproject.toml` has `pyserial>=3.5` with no upper bound. Every 3.x release sets it in `__init__`.
- The test assigns `_port_handle` itself (`:57`), so a pyserial that renamed the attribute still passes the test. In production the rename raises AttributeError, which is not in `_BREAK_ERRORS`, so `/break` answers 500 on every Windows port.
- Fix: assert `serialwin32.Serial()._port_handle is None` before overwriting it in the test (this is class 63, a fixture in a state the producer cannot reach).
- Fixed: a Windows-only test asserts `serialwin32.Serial()._port_handle is None` and drives the real kernel32 on a non-COM handle. See `windows-fixbatch-break.md`.

### F4 NIT: `GetLastError` is read through pyserial's `WinDLL`, which does not use `use_last_error`
- `link.py:222,228` `ctypes.WinError()` calls `kernel32.GetLastError` after Python code has run since the failing call. ctypes documents `use_last_error=True` plus `ctypes.get_last_error()` as the reliable form.
- Only the text of the error changes: the 400 decision rests on the BOOL result, which is read correctly (`BOOL` restype, `HANDLE` = `c_void_p` argtypes in `serial/win32.py:126-128,164-166`, correct width on 64-bit).
- The set failure's `WinError()` is built before the `finally` runs ClearCommBreak, so ClearCommBreak does not clobber it. pyserial's own `in_waiting`/`write` use the same pattern.
- Fix, if wanted: a module-level `ctypes.WinDLL("kernel32", use_last_error=True)` with its own `SetCommBreak`/`ClearCommBreak` prototypes, and `ctypes.WinError(ctypes.get_last_error())`. Tests would then patch that seam instead of `serial.win32`.
- Fixed: `_win32_break` uses its own `WinDLL("kernel32", use_last_error=True)` and `WinError(get_last_error())`. See `windows-fixbatch-break.md`.

### F5 LOW: `_link` (and `test_daemon_config_path.py:132`) turn any symlink OSError into a skip on every OS
- `host/tests/test_cli_export_files.py:154` `except (OSError, NotImplementedError): pytest.skip(...)`.
- Scenario: on Linux, a later edit creates the link path twice (FileExistsError), or tmp is full. All 10 symlink tests skip, CI stays green, and the link-safety behaviour goes untested with no failure.
  - Driven in scratch: `symlink_to` raising `FileExistsError` gives 10 skipped, 0 failed.
- Fix: skip only on the privilege refusal (`sys.platform == "win32" and getattr(exc, "winerror", None) == 1314`) and re-raise otherwise. Tighten class 88's sweep text ("a try/skip on `OSError`") to match.
- Fixed: both sites use `support.symlink_or_skip`, which skips only on WinError 1314 or `NotImplementedError`; `test_support.py` drives each refusal. See `windows-fixbatch-break.md`.

### F6 LOW: class 90's list of results pyserial ignores is incomplete for the methods the daemon calls
- `docs/REVIEW.md:1007` names only `_update_break_state`, `_update_rts_state`, `_update_dtr_state`, `reset_input_buffer` and `reset_output_buffer`.
- The daemon also calls `write`, `read`, `close`, `cancel_read`/`cancel_write` and `open` (through `serial_for_url`), which ignore these results:
  - `write`: `GetOverlappedResult` (`serialwin32.py:321`).
  - `read`: `ResetEvent`.
  - `open`/`_reconfigure_port`: `SetupComm`, `GetCommTimeouts`, `PurgeComm`, `SetCommTimeouts`, `SetCommMask`, `GetCommState`.
  - `_close`: `CloseHandle`.
  - `_cancel_overlapped_io`: `CancelIoEx`.
- The one with a possible false success is `write`: a pending write completed with `ERROR_OPERATION_ABORTED` returns `n.value` without raising ("canceled IO is no error"), and `SerialPort._write_bytes` ignores the count.
  - Whether a USB unplug completes a pending write that way is unmeasured.
  - A zero `n` from a failed `GetOverlappedResult` otherwise raises `SerialTimeoutException`, with a misleading "Write timeout" reason, but no false success.
- Fix: list those calls in the class 90 entry with a verdict for each, and add an owner check to the Windows checklist: unplug during a write blocked by flow control, then read the `/send` result.
- Fixed: the reader's own `cancel_write` produces the short count, so `SerialLink.write` now raises on one; class 90 lists every ignored call with a verdict. See `windows-fixbatch-break.md`.

### F7 NIT: class 88's count and sweep regex do not reconcile
- `docs/REVIEW.md:995` says "7 sites". The sweep grep returns 8 lines today, and 5 of them are calls.
  - The others are the `_link` docstring (`:149`) and two test names containing `symlink_to` (`test_cli_export_files.py:333`, `test_serial_link_devices.py:27`).
- Fix: sweep `\.symlink_to\(|os\.symlink\(|...` and state "5 call sites".
- Fixed: sweep tightened to call syntax, 4 call sites. See `windows-fixbatch-break.md`.

### F8 NIT: the R18-4 checklist line quotes the wrong error text
- `docs/review/2026-09-23-opus55/registry-triage/windows.md:53` says "400 `port X: break failed`". The code and SPEC say `port <alias> break failed: <reason>` (`serial_link.py:1180`, `SPEC.md:758`).
- Fixed. See `windows-fixbatch-break.md`.

### Checked, no finding
- Thread safety: `_break_locked` holds `_write_lock` across the whole break, and both closes of a published link (`_close_link_locked`, `serial_link.py:1046`) take that lock. The close at `:597` happens before `_link` is published. A cached handle value cannot be closed and reused mid-break.
- Always cleared: the `finally` clears after a failed set and after an exception during the sleep (tested in scratch). pyserial's own `send_break` has no `try/finally`, so this is stricter than before.
- Duration: same as pyserial's `SerialBase.send_break`: an `is_open` check, set, `time.sleep(duration)`, clear.
- Error mapping: every raise is `SerialException` (`PortNotOpenError` is a subclass), which is in `_BREAK_ERRORS`, so it answers 400 `port <alias> break failed: ...`. That matches `SPEC.md:758`.
- Non-Windows: `_is_win32_serial` returns before any Windows import, so `link.py` imports on Linux and the POSIX path is byte-identical (`uv run python -m pytest tests/test_serial_link_break_errors.py tests/test_cli_export_files.py`: 36 passed, 3 skipped).
- `rfc2217://` is not a `serialwin32.Serial`, and `socket://` returns earlier, so neither is rerouted.
- Docs: the ARCHITECTURE line and the SPEC sentence match the code. The AI guide has no break change and needs none, because the CLI surface is unchanged. No em or en dashes in the three commits (a Python count over `git show` output gives 0). ruff is clean on the touched files.

## Revert verification

Scratch harness: `~/tt-data/mcuscope-2026-09-26/break-rv/`, made with `git archive HEAD host tools` and run with the repo venv's Python from `break-rv/host` (checked: it imports the scratch `mcuscope`).
- Windows is emulated on Linux (the F1 fixture), and the repo's 3 Win32 tests run unmodified except that the skip is removed.
- B1 to B5 are the extra break-it tests: handle and duration, interrupted sleep, not open, set fails with clear ok.
- The mutation driver is `../mutate.py`. It checks every anchor before writing, and `link.py` was restored from `../link.py.orig` and diffed.

| Mutant | Repo tests (R) | Scratch B tests | Verdict |
|---|---|---|---|
| M1 dispatch removed (goes through pyserial `send_break`) | caught (2 of 3) | caught | covered |
| M2 SetCommBreak result unchecked | caught `[0-0]` | caught | covered |
| M3 ClearCommBreak moved out of `finally` | caught `[0-0]` | caught | covered |
| M4 ClearCommBreak result unchecked | caught `[1-0]` | - | covered |
| M5 `time.sleep(seconds)` removed | **passes** | caught (B1, B3) | F2 |
| M6 `is_open` check removed | **passes** | caught (B4) | F2 (test or delete) |
| M7 wrong handle passed | **passes** | caught (B1) | F2 |
| M8 Win32 path returns False | caught (lands test) | caught | covered |
| S1 `_link` skip reverted, emulated refusal (EPERM) | 11 failed (10 symlink tests + the POSIX FIFO test) | - | skip branch covered |
| S2 `_link` as committed, emulated refusal (EPERM) | 10 skipped, 1 failed (FIFO test at `:337`, POSIX only, skipped on Windows by `mkfifo`) | - | as intended |
| S3 `_link` as committed, `FileExistsError` | 10 skipped, 0 failed | - | F5: a real failure is hidden |

The symlink mutants ran as `RVSYM=eperm|eexist python -m pytest -p rvsym tests/test_cli_export_files.py`, with `break-rv/host/rvsym.py` patching `Path.symlink_to`.

## Not covered
- Nothing was run on Windows. The GetLastError clobber in F4 is from the ctypes documentation, not measured.
- F6's `ERROR_OPERATION_ABORTED` write path on a real unplug is unmeasured.
- "A live break on the connected adapter still answers 200" (windows.md:79) is the owner's claim; I did not re-check it.
- The REVIEW_LOG wording and the class 89 start-race parts of 89a6af3 were not reviewed.
- Scratch dirs created, not deleted: `~/tt-data/mcuscope-2026-09-26/break-rv/` (harness, mutants, `rvsym.py`, `.orig` copies) and `~/tt-data/mcuscope-2026-09-26/ci/` (the Windows CI job log `win313.log`).
