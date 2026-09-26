# Flake: `test_ctrl_c_ends_a_follow_with_success` prints nothing

Diagnosed at HEAD 89a6af3 plus the uncommitted working tree.
Failure: `tests/test_cli.py:2600`, `assert "before-the-interrupt" in out`, out == "", rc 0, no "interrupted" (whole-suite2.log).

## Root cause

A race inside the test itself, not leaked state.
The test's `blocked` event says "recv() is parked", but that is also true while the follow is still staging the backfill, before any row is printed.

- `_follow_ws` runs `_stage_backfill` (`host/mcuscope/cli.py:1128`): the snapshot (GET /lines) goes to a worker thread, and frames arriving meanwhile are staged in a list.
- `_BlockingWS`'s first recv returns the row at once (staged), and the second recv sets `blocked` straight away.
- If the snapshot thread has not finished by then, the interrupter thread calls `interrupt_main()` during staging.
  asyncio.Runner's SIGINT handler cancels the main task inside `_stage_backfill`, whose `except BaseException` re-raises, so the staged list is never handled.
- `asyncio.run` then raises KeyboardInterrupt, `_follow_ws` turns it into exit 0, and the snapshot (`-n 0`) prints nothing: rc 0, empty out, clean err, exactly as logged.

The test passes normally only because the snapshot's ~0.3 ms of work runs in one GIL hold as soon as the executor thread starts, before the main thread reaches recv1.
Traced order in a passing run: snapshot start 0.00 ms, snapshot end 0.3, recv1 0.4, `_stage_backfill` returns 0.45, `blocked` 0.5, `interrupt_main` 0.55.
Anything that makes the snapshot thread lose the GIL before it finishes opens the window: the failing run's likely trigger is a >5 ms stall (OS preemption on a loaded machine, a GC pause), but that last step is inferred, not reproduced.

## Evidence

Scratch tests in `~/tt-data/mcuscope-2026-09-26/flake/`, run from `host/` with `uv run python -m pytest -p no:randomly -p tests.conftest --rootdir=. <file>`.

| Condition | Result |
|---|---|
| Unmodified test, `sys.setswitchinterval(1e-4)` or `1e-5` (`test_flake_switch.py`) | 20/20 fail, same assertion at test_cli.py:2600 |
| Unmodified test, default 5 ms interval | 10/10 pass |
| Copy with /lines handler sleeping 0.2 s (`test_flake_repro.py`) | 5/5 fail |
| Copy with `time.sleep(0)` in the handler (one GIL release) | 4/5 fail |
| Either copy, interrupter waiting for the snapshot first | 10/10 pass |
| Unmodified test alone | 5/5 pass |
| `test_cli.py` alone, `--randomly-seed` 1..5 | all pass (167 each) |
| 1 or 4 busy Python threads alive; 16 spinners on 8 cores; 3M-object heap | 30/30, 30/30, 40/40 pass |

Deterministic reproducer: `test_flake_switch.py::test_orig_switch[0-0.0001]`.
It calls the real test function unchanged, with the switch interval shortened.

Not leaked module state: no seed of `test_cli.py` fails, and none of the candidates listed in the brief (`_JSON_MODE`, `_OUT_FAILED`, `_stdio`, capsys) is on the path.
A leak would print something wrong, not drop a staged row with rc 0.

## Predates the diff

Yes.
The diff does not touch `tail`, `_follow_ws`, `_stage_backfill`, `emit_stream` or the test; the staging code dates from 568741d/f530536 (2026-08-11).
On a clean worktree of HEAD (`~/tt-data/mcuscope-2026-09-26/flake-wt`), the switch-interval and slow-snapshot reproducers fail identically (HEAD line 2597, same assertion) and the waiting controls pass.

## Proposed fix (test only)

Interrupt once the row has been printed and recv is parked, not merely once recv is parked:

```python
printed = threading.Event()
real_emit = cli.emit_stream
monkeypatch.setattr(cli, "emit_stream", lambda text: (real_emit(text), printed.set()))

def interrupt() -> None:
    if blocked.wait(10) and printed.wait(10):
        _thread.interrupt_main()
```

`from mcuscope import cli` in the test; `_follow_ws` calls `emit_stream` through the `cli` module global, so the patch reaches it.
Checked in `test_flake_fixed.py`: 20/20 pass across 0 s and 0.2 s snapshot delays and the 5 ms and 1e-5 s switch intervals, the conditions where the original fails every time.
The rc 0 and no-"interrupted" assertions are unchanged, so the test still guards against the in-coroutine interrupt arm its docstring describes (mutation-checked, see Fix).

No product change proposed.
Ctrl-C during the snapshot drops frames staged but not yet printed; the user asked to stop and the rows are in the DB, so nothing is lost silently.

## Registry class

None of 21 (wall-clock granularity) or 32 (module state) fits; 50 (a race test that parks the worker after the step it claims to race) is the nearest sibling.
Added as class 91 in `docs/REVIEW.md`: a test's go-signal set at a point an earlier phase also reaches.

## Fix

Applied to `host/tests/test_cli.py::test_ctrl_c_ends_a_follow_with_success`, test only.
The interrupter waits for `blocked` and then for `printed`, set by a wrapper around `cli.emit_stream`.
If `printed` times out it interrupts anyway, so a follow that drops the row fails at the row assertion within 10 s, not after the double's 30 s budget with a misleading message.
The "writes no wakeup fd" comment stays: during `asyncio.run` the wakeup fd is -1, and an `interrupt_main()` at 100 ms under `asyncio.sleep(3)` raised only at 3004 ms (`flake/wakeup.py`).

Verification (scratch files in `~/tt-data/mcuscope-2026-09-26/flake/`):

- `test_flake_verify.py` calls the edited real test 20 times each under switch interval 1e-4, 1e-5, and a 0.2 s slow snapshot: 60/60 pass.
- Positive control: the same wrapper against the unfixed test in the HEAD worktree: 60/60 fail.
- Mutations of `cli.py` in a scratch copy (`flake/mut/`, `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`); the unmutated copy passes:

| Mutation | Result |
|---|---|
| No `except KeyboardInterrupt` around `asyncio.run` | fails, `rc == 0` assert, err "interrupted" |
| That arm moved inside the coroutine (the docstring's regression) | fails, same assert |
| Staged frames never handled | fails in 11 s, `before-the-interrupt in ''` |

- `ruff check tests/test_cli.py` clean; `tests/test_cli.py` whole file after the edit: 167 passed.

### Class 91 sweep

Python: 67 `.wait(` sites in `host/tests/*.py`.
Test-owned releases (`release`, `gate`, `finish`, `stop`, `start` barriers) are exempt: the test sets them.
Signals set in a double or callback, each with the callers that reach its `set()`:

| Site | Signal | Verdict |
|---|---|---|
| test_cli.py:555 `got_frame` | any stdout line of the child | ok: later asserts only need one line, and check every id |
| test_cli.py:1308 `found` | line containing the expected text | ok |
| test_cli.py `blocked` | recv() parked, also during staging | **instance, fixed** |
| test_cli_follow.py:329 `ready` | server listening, port recorded | ok |
| test_daemon_closed_streams.py:114 `third` | third session; the listener serves one client at a time, so both failures printed first | ok |
| test_e2e.py:72 `fired` | shutdown callback | ok: the assertion itself, not a go-signal |
| test_reconnect.py:177 `scanning` | the slow thread's scan only | ok |
| test_reconnect.py:520, test_serial_link_attach.py:473, test_serial_link_tx.py:65 `occupied` | inside the only pool worker | ok |
| test_reconnect.py:1049 `storing` | consumer inside the store with the first burst; a split burst would fail the `== 50` assert loudly | ok |
| test_reconnect.py:1310 `failed` | after the loop ran `fail_pending` | ok |
| test_serial_link_attach.py:37, :102 `entered` | the prime of the one attach in flight; the device never opens, so no other prime | ok |
| test_serial_link_devices.py:69 `scanning` | `server.cached_comports` has one caller, `/devices` (server.py:3953) | ok |
| test_serial_link_tx.py:91 `taken`, :467 `held` | inside the write lock | ok |
| test_serial_link_tx.py:191 `_SlowText` entered | first `str(exc)`, at serial_link.py:1096, between the health load (1093) and store | ok |
| test_server_exports.py:219 `started` | copy's INSERTs running (`in_transaction`) | ok |
| test_server_exports.py:434 `started` | first yield of the export generator | ok |
| test_server_lifespan.py:123 `parked` | reconnect's prime; the device never opens | ok |
| test_server_raced_futures.py:88 `entered`, :106 `posted` | the one build; done callback after wrap_future's | ok |
| test_session_bundle.py:435 `started` | entry to `export_session_db`, the bundle's only call | ok |
| test_source_link.py:108 `in_poll`, :120 `wrote` | reader thread's poll; writer after `write()` | ok |
| test_store_fastpaths.py:109 `asyncio.sleep(0.05)` as the go-signal | `high` is fixed before the first await (`async with` an uncontended lock) | ok |

JS (`host/tests/webui_js`, read, not run beyond one probe of `clear_staged_backfill.test.mjs` in the scratch copy):

- `clear_staged_backfill.test.mjs` `until(...)` over `seen`: ok, `blank()` resets `seen` and all 26 tests call it, directly or via `seedPage`/`stagedPage`/`droppedPage`.
- Fetch gates in `api_backfill_clear`, `api_backfill_clear_tokens`, `cmdbar_refusal_inflight`: ok, each parks only its named route and asserts the request is in flight (`assert.ok(release, ...)`) before acting.
- Gates in `api_ws_gap`, `api_staging_overflow`, `api_backfill_paging`, `api_capture_reset_stage`, `api_ws_backoff`, `statusbar_logic`, `statusbar_session_dialog`: ok, each parks one route, and node runs the test on one thread, so a fixed tick count is deterministic, not a race window.

No other instance found.
