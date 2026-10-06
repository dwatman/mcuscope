# fix-server2

## 1. Verdict scope (OP-7)
- Every verdict read routes through `_verdict_rows` (`server.py` ~2659: live and retrospective `/assert` query and count) or `CaptureWatch` (~2711: live `/wait` and `/assert`). `/wait` has no retrospective path. Other store reads (`/lines`, plot, export, `_with_names`) are not verdicts.
- Revert-verify: dropping `own_rows: False` in `_verdict_rows` fails `test_server_live_verdicts.py::test_a_silent_port_with_a_marker_and_a_sys_row_is_still_empty`; dropping it in the CaptureWatch subscribe fails `test_server_verdict_own_rows.py::test_a_live_wait_on_a_port_ignores_a_portless_marker`. Both pass restored (42 passed).

## 2. RES-8 traceback: NOT done, needs daemon.py
- Tried `except CaptureUnreadable: log.error; raise SystemExit(3)` around `store.start`, driven with a random-bytes DB in an isolated daemon (port 18920): uvicorn's lifespan handler logs any BaseException incl. SystemExit as a traceback, so the output is unchanged (rc 3 either way). Reverted.
- Exact change: in `daemon.py` (`_serve`, where `_FirstError` is attached to `uvicorn.error`), add a `logging.Filter` that clears `record.exc_info`/`exc_text` when the exc is `CaptureUnreadable`, and print its message instead. Or probe the capture before `uvicorn.run`.

## 3. StoreError after capture failure
- `server.py` new `@app.exception_handler(StoreError)` (before `_register_routes`): 503 `{"error": cause}` without logging when `store.capture_error` is set, otherwise delegates to `_unhandled_error` (logged 500).
- Test: `tests/test_server_capture_failed.py` (503 with no "unhandled error" log; healthy capture stays 500). Mutation `if True:` fails the first test.

## 4. Lifecycle rows
- `server.py` uses `DAEMON_START_ROW` / `DAEMON_STOP_ROW` (imported from `.store`). Not mutation-tested: the constants equal the old literals, and `test_store_start_checks.py` plus `test_assert.py` pass.

## 5. SPEC 3.4
- Added after the "An empty `port=`" paragraph: the `port=` own-rows sentence, and a line that a failed capture makes store reads answer 503 with the cause.

## Existing tests edited
- None.

## Changelog
- Reads after a capture failure answer 503 with the cause, no traceback logged per request.

## Verification
- ruff clean; ran test_server_capture_failed, test_server_status_and_listing, test_assert, test_server_live_verdicts, test_store_start_checks: 97 passed.

## Doubts
- Handler also catches StoreErrors raised in routes that did not catch them while capture_error is set; the 503 body is str(exc), not capture_error itself.
- Scratch: ~/tt-data/mcuscope-2026-10-05/fix-server2/ (server.py.orig, server.py.good, run/ with bad.db). No process left running.

## Orchestrator follow-up: RES-8 traceback

- `daemon.py` `_ShortCaptureError`: a filter on `uvicorn.error`, installed and removed beside `_FirstError` in `_serve`. Starlette hands uvicorn the lifespan traceback as message text, so the record is recognised by its last line (`mcuscope.store.CaptureUnreadable: ...`) and rewritten to the message alone.
- Test `test_daemon_startlog.py::test_a_corrupt_capture_logs_its_message_without_a_traceback`, with a positive control (a RuntimeError still logs its traceback on the same stream); `test_the_error_capture_is_removed_after_serving` now also checks the filter is removed.
- Revert-verified: the filter disabled, and the match forced on every record, each fail the test.
