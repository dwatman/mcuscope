# Fix batch: Windows break review (windows-fixdiff-break.md F1 to F8)

Base: 89a6af3ec4b19ca798ab384a5f2005936d88ddf2. Not committed. The start-race files (`cli.py`, `cli_daemonctl.py`, `test_cli_daemon_stop_scope.py`, class 89) were not touched.

## Per finding

### F4: Win32 error read (`host/mcuscope/link.py:211`)
- `_win32_break` builds its own `ctypes.WinDLL("kernel32", use_last_error=True)` (`:223`), as `pidfile.py` and `cli_daemonctl.py` do, and sets `argtypes = [HANDLE]` on both calls (`:224`).
  - pyserial's `serial/win32.py:23` binds `WinDLL('kernel32')` without `use_last_error`, so its functions cannot be used with `get_last_error`.
- Errors are `ctypes.WinError(ctypes.get_last_error())`, formatted with `str()`, so the message carries the code: `SetCommBreak failed ([WinError 5] Access is denied.)`. The set's message is still built before the `finally` runs the clear.
- The `is_open` check is deleted (F2 option "delete"): pyserial nulls `_port_handle` on close (`serialwin32.py:243`), and SetCommBreak on a None handle fails like a dead one. The daemon cannot reach it anyway.

### F6: short write (`host/mcuscope/link.py:163`)
- The daemon does rely on it: the reader's `finally` calls `link.cancel_write()` on a disconnect (`serial_link.py:634`) while a `/send` may be blocked in `write`.
  - Win32: the aborted write returns its short count ("canceled IO is no error"). POSIX: `cancel_write` makes `write` `break` and return `length - len(d)`. Neither raises, so `_write_bytes` reported success.
- Fix: `SerialLink.write` raises `SerialException("write cut short (n of N bytes reported written)")` on any count other than `len(data)`; `_write_bytes` turns that into `port <alias> write failed: ...` and counts it in the write streak.
  - All pyserial backends the allowlist permits return `len(data)` on a whole write (`serialposix`, `serialwin32`, `protocol_socket`, `rfc2217`).
- Class 90 (`docs/REVIEW.md:1004`) now lists every call the daemon makes, what `serialwin32.py` ignores in each, and a verdict. One line records the unmeasured part (whether an unplug alone aborts a pending write); the count check covers it either way.
- `docs/ARCHITECTURE.md:65`: one line for the short-count rule.

### F1, F2, F3: tests (`host/tests/test_serial_link_break_errors.py:55-156`)
- Fixture `win32` fakes the boundary only: a `serial.serialwin32` module whose `Serial` is a plain class (no pyserial `close`, so no fake handle reaches pyserial), `ctypes.WinDLL`/`get_last_error`/`WinError` (patched `raising=False`), `link.sys.platform`, and `link.time.sleep`. The real `SerialLink.send_break` -> `_is_win32_serial` -> `_win32_break` runs on Linux.
  - The fake kernel32 updates the saved error only when built with `use_last_error=True`, and a clear overwrites it, so a wrong error read shows as the wrong code.
- Tests (all run on Linux):
  - landing: events are exactly set(handle), sleep(0.25), clear(handle); `argtypes` pinned.
  - failures `(0,0)`, `(0,1)`, `(1,0)`: exact message text with the code; the line is cleared in every case, and held only after a successful set.
  - interrupted hold (`KeyboardInterrupt` in the sleep): still cleared.
- Windows-only (`:140`): real `serialwin32.Serial()` is recognised by `_is_win32_serial` and has `_port_handle is None`; real kernel32 on a None handle and on a regular file's handle raises `SetCommBreak failed ([WinError N` with N non-zero.
- `host/tests/test_link.py:69`: a real pyserial port on a pty; a whole write returns, then after `cancel_write` a write raises `write cut short (0 of 100 ...)`. POSIX only.

### F5: symlink skip (`host/tests/support.py:556`)
- `symlink_or_skip(link, target)` replaces `_link` in `test_cli_export_files.py` (5 calls) and the try/skip in `test_daemon_config_path.py:130`.
  - It skips on `NotImplementedError` or an `OSError` whose `winerror` is 1314, and re-raises everything else. No platform check: `winerror` exists only on Windows.
- `host/tests/test_support.py` (new): 1314 and `NotImplementedError` skip with their own reason text; winerror 5, `FileExistsError` and POSIX `EPERM` are re-raised as the same object.

### F7, F8: docs
- Class 88 (`docs/REVIEW.md:991`): sweep is `\.symlink_to\(|os\.symlink\(|os\.link\(|hardlink_to\(|mklink`, 4 call sites today, and names the helper rule.
- `registry-triage/windows.md:53`: now `port <alias> break failed: <reason>`, and the parenthetical says `link._win32_break` raises, not pyserial.

## Revert verification

Scratch copy of the working tree at `~/tt-data/mcuscope-2026-09-26/fixbatch-break-rv/` (tar of `host` and `tools`, run with the repo venv's Python; checked it imports the scratch `mcuscope` and `tests.support`). Driver `mutate.py` checks every anchor before writing, restores from `.orig`, and asserts the restore. Tests: the five changed test files, `-p no:randomly -x`. Baseline: 61 passed, 1 skipped.

| Mutant | Result | Caught by |
|---|---|---|
| W1 `time.sleep` removed | caught | landing |
| W2 wrong handle | caught | landing |
| W3 `use_last_error` dropped | caught | failures `(0,0)` |
| W4 set error via bare `WinError()` (the pre-fix read) | caught | failures |
| W5 `argtypes` not set | caught | landing |
| W6 clear moved out of `finally` | caught | failures `(0,0)` |
| W7 set result unchecked | caught | failures |
| W8 clear result unchecked | caught | failures `(1,0)` |
| W9 Win32 dispatch removed (through pyserial) | caught | landing |
| W10 `_is_win32_serial` always False | caught | landing |
| W11 set error read after the clear | caught | landing, `(0,1)` |
| X1 write count unchecked | caught | `test_link` pty test |
| Y1 skip on any `OSError` | caught (after a test fix, below) | `test_support` |
| Y2 `NotImplementedError` not skipped | caught | `test_support` |
| Y3 1314 re-raised | caught | `test_support` |

- Deleted branch: `is_open` check in `_win32_break` (no test needed; nothing to revert).
- Y1 first survived: `pytest.raises(FileExistsError)` let the helper's `Skipped` (a `BaseException`) through, so the case skipped instead of failing. The test now catches `BaseException` and checks the type.

## Other checks
- `ruff check .` clean. No U+2013/U+2014 in any changed file (Python count).
- Each changed test file alone, random order: break_errors 8 passed 1 skipped; link 6; support 5; export_files 33; config_path 9.
- Files with real pyserial writes through `SerialLink`, each alone: `test_sim_tcp.py` 12, `test_sim_pty.py` 3, `test_break.py` 19, `test_serial_link_tx.py` 17, `test_port_health.py` 27, `test_scaffold.py` 10, `test_reconnect.py` 51 + 1 skipped. Whole suite not run (brief).

## Doubts
- The Windows-only test is unrun. It assumes SetCommBreak on a regular file handle and on NULL fails with a non-zero error (expected ERROR_INVALID_FUNCTION and ERROR_INVALID_HANDLE). If Windows CI shows otherwise, drop that half and keep the `_port_handle` pin.
- The fake sets the saved error to 0 on a successful call; real Win32 may leave it unchanged. That makes W11 stricter in the fake than on hardware, not weaker.
- pyserial POSIX quirk: after `cancel_write`, a write whose bytes all went out can still report 0 (the abort is seen before `d` is sliced). Now a false failure instead of a false success, and only on a port that is being closed.
- No owner check was added to the Windows checklist for an unplug during a flow-controlled write: the count check makes the outcome a failure whatever the abort path. Add one if a measurement is still wanted.

## Needs Windows
- The Windows-only test (`test_the_real_win32_port_and_kernel32_fit_the_checked_break`).
- A live break on a connected adapter still answering 200 through the new `WinDLL`.
- Whether an unplug alone aborts a pending write (class 90's unmeasured line).

## Scratch dirs (not deleted)
- `~/tt-data/mcuscope-2026-09-26/fixbatch-break-rv/` (working-tree copy, `mutate.py`, `.orig` files).
- `~/tt-data/mcuscope-2026-09-26/export_files.pre-fix`, `config_path.pre-fix` (pre-edit copies).

## Round 2 (windows-fixdiff2.md F4, F7)
- F4: `host/tests/test_link.py` `test_a_partial_write_raises`, a stub whose `write` returns `len(data) - 1`, asserting `write cut short (9 of 10 bytes reported written)`.
- F7: `docs/SPEC.md:695` `write_failures` causes now read "(timeout, closed handle, cut short)".
- Revert, scratch `~/tt-data/mcuscope-2026-09-26/fixbatch-break-rv2/` (`PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, `test_link.py` alone): baseline 7 passed; M13 (`n == 0 and data`) 1 failed (the new test); count check removed 2 failed. `link.py` restored and compared equal to the repo copy.
- `test_link.py` in random order: 7 passed; ruff clean.
