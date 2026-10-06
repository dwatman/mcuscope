# Fix batch: server3 (fixdiff-server findings)

Scratch: `~/tt-data/mcuscope-2026-10-05/fix2-server/` (originals in `orig/`, `*.new.py` copies, run and revert logs, `probe/ws_probe.py`). No process left running.
Revert-verification: each changed site was reverted to `orig/` or mutated by hand, the pinning file run, and the file restored (cmp).

## FD-SERVER-1 (HIGH): duplicated headers on the 503
- `server.py:654-661` `_store_error`: the 503 adds no headers (the app middleware it runs inside adds them); a healthy-capture `StoreError` is re-raised (`:660`), so ServerErrorMiddleware logs it and `_unhandled_error` answers the 500 exactly as before the server2 batch. `_unhandled_error`'s comment is true again.
- Test: `tests/test_server_response_headers.py`, class 87 for every HTTP response path: 200, 404, 422, 413, 403 guard, unhandled 500, healthy-capture `StoreError` 500, and a real capture failure (lock file unlinked, `_run_tick_checks()`) then `GET /lines`: 503 with `capture_error` as body. Each asserts `x-mcuscope-version`, `x-frame-options` and the CSP exactly once and that `check_daemon_version` accepts the headers.
- Revert (original `server.py`): the 500 test and the real-failure 503 test fail.
- Class 87 sweep: uvicorn's malformed-request 400 and a WS handshake refused before accept stay exempt (sent outside the app); the WS 1009 close (below) is not an HTTP response.

## FD-SERVER-2 (MEDIUM): `Content-Length` plus chunked bypassed the cap
- `server.py:981-1023` `_BodyLimit`: a declared length over the cap is still refused before any body is read (`:1000`); every body is then counted as received and replayed, whatever framing the parser chose. No header decides whether the count runs.
- Tests (`test_server_request_limits.py`, real uvicorn): `test_a_length_beside_chunked_does_not_route_around_the_cap` (413) and positive control `test_a_small_body_beside_both_headers_still_reaches_the_route` (200).
- Revert: the original fails the bypass test; dropping the up-front length check fails `test_a_declared_length_over_the_cap_is_refused_before_any_body_is_sent`.

## FD-SERVER-3 (MEDIUM): capture-lock hand-off untested
- No code change (`daemon.py:526`).
- Test: `tests/test_daemon_serve_wiring.py::test_the_app_holds_the_lock_the_daemon_acquired`. It runs `daemon.main` to `_serve` and asserts that `app.state.capture_lock` is a held `CaptureLock` and that a second claim on the same capture raises `LockError`.
- Revert (line replaced by `pass`): fails.

## FD-SERVER-4 (LOW): `_write_report` 0700 dir untested
- Test: `tests/test_stdio_report_modes.py` (POSIX, umask 022). A startup log into a missing two-level data dir creates both dirs 0700 and the log 0600.
- Revert: `os.makedirs` back fails it, and so does dropping the 0600 opener.

## FD-SERVER-5 (LOW): N7 sites, my half
- `dirs.py:53` new `private_opener` (0600 create). Used by:
  - `pidfile.py:181` (claim) and `:331` (the no-hard-link fallback) take `0o600`.
  - `pidfile.py:304` (`create_record` temp, whose hard link keeps the mode) and `_stdio.py:375` (startup and crash logs) use the opener.
- Tests: `tests/test_pidfile_modes.py`, one per site: claim, `create_record`, and `create_record` with `os.link` failing. Each runs under umask 022.
- Revert: each of the three pidfile sites mutated alone fails its own test.

## FD-SERVER-6 (LOW): unbounded identity retry
- `lockfile.py:151-158`: a mismatch past the deadline raises `OSError("the locked file is not the one at <path> (replaced on every attempt, or the filesystem reports unstable file ids)")`, with a 50 ms sleep between attempts. `daemon.main` already reports an `OSError` as `cannot claim <path>: ...`, exit 1, which `--ignore-capture-lock` does not bypass. A Windows id mismatch is now a refused start instead of a hang.
- Test: `test_lockfile_identity.py::test_acquire_gives_up_when_the_lock_file_identity_never_matches`, runs on every OS.
- Revert: the original hangs (killed by `timeout 30`, rc 124).

## FD-SERVER-7 (LOW): expect-only verdict `incomplete` when rows were shed
- NOT DONE: it needs a ruling, see "Not done".

## FD-SERVER-8 (LOW): WebSocket frames buffered to 16 MiB
- `daemon.py:570`: `ws_max_size=MAX_BODY_BYTES` passed to uvicorn. No client sends anything over `/ws`.
- Test: `test_daemon_serve_wiring.py::test_uvicorn_bounds_a_websocket_frame_to_the_body_cap`. Structural: it checks the kwarg `main` passes to `_serve`.
- Revert: the test fails without the kwarg.
- Real behaviour probe (`probe/ws_probe.py`, uvicorn on 19040, `/ws` of a `mk_app`): a 100-byte frame keeps the socket open; a `MAX_BODY_BYTES + 1` frame is closed with 1009.

## FD-SERVER-9 (NIT): `make_private_dirs` swallowed a file at the path
- `dirs.py:47-50`: `FileExistsError` is swallowed only when a directory is there afterwards. That happens when another process won the mkdir race.
- Tests (`test_lockfile_identity.py`):
  - `test_make_private_dirs_raises_on_a_file_at_the_path`.
  - `test_make_private_dirs_accepts_a_dir_another_process_made_first` (POSIX). Its `os.mkdir` creates the dir, then raises.
  - The misnamed test is renamed `test_make_private_dirs_raises_under_a_file_parent`.
- Revert: the original fails the file test; `if True:` fails the race test.

## FD-SERVER-10 (NIT): 422 cut counted the repr
- `server.py:634-639`: a string counts its own characters, so `"A" * 5000` reads `(5000 characters)`. Any other value counts its repr's characters.
- Tests: `test_a_rejected_value_is_quoted_cut_short` (now 5000), new `test_a_rejected_non_string_counts_the_characters_quoted`.
- Revert: the original fails the first test; widening the `str` branch to lists fails the second.

## Existing tests edited
- `test_server_request_limits.py::test_a_rejected_value_is_quoted_cut_short`: 5002 to 5000 (FD-SERVER-10).
- `test_lockfile_identity.py::test_make_private_dirs_is_idempotent_on_a_file_parent` renamed `..._raises_under_a_file_parent` (body unchanged).

## SPEC edits
- 3.4 error list, 413: "or once the bytes received pass the cap, however the body is framed".
- 3.4 error list, 503: adds "the capture has failed (`capture_error`)".

## Guide wording
- None needed. The CLI's wording for the 503 and the 413 is unchanged; the 503 now reaches it with its cause instead of a version refusal.

## Changelog
- After a capture failure, `mcu` reports the daemon's 503 cause (restart the daemon). Before, it refused the daemon as the wrong version.
- A request with both `Content-Length` and chunked encoding can no longer pass the 64 KiB body cap.
- WebSocket frames from a client are capped at 64 KiB (closed with 1009).
- The pid record and the startup and crash logs are created owner-only (0600) on POSIX.
- A capture lock whose file identity never settles fails the start after 2 s instead of hanging.
- A 422 counts a cut string by its own characters.

## Not done
- **FD-SERVER-7, ruling needed.** Question: should a verdict with no forbids and every expect matched be decided (`pass`) despite `dropped > 0`, alongside a matched forbid? Recommended: yes, because shed rows cannot un-see a match. Otherwise keep `incomplete` for any shed window, as SPEC 3.4 and OP-9 now say.
  - If yes: change `server.py` ~`:3394` to exempt `not forbids and all expects matched`, add a SPEC 3.4 sentence, and add a test beside the forbid-decided one in `test_server_verdict_outcomes.py`.
- FD-SERVER-5, other batches' files:
  - `config.py:606` `_write_doc`: use `dirs.make_private_dirs(path.parent)` and create the temp file with `dirs.private_opener` (or `os.open(..., 0o600)`).
  - `update_check.py:215`: `dirs.make_private_dirs` for the cache dir.
- SPEC 3.2 (not mine), after the "new capture is created 0600" line: "The capture lock, the pid record and the startup and crash logs are created 0600 too."
- FD-SERVER-6, Windows checklist (orchestrator): "a daemon starts, and after one 60 s tick `/status` still shows `capture_error: null` (the `stat`/`fstat` identity compare holds on NTFS)".
- `docs/REVIEW.md` class 87 (orchestrator): add the inverse invariant, "a handler that runs inside the middleware (any `exception_handler` other than `Exception`/500) adds none of its headers". The sweep is `tests/test_server_response_headers.py`.

## Doubts
- Least sure: always counting the body means every request's body is read before the route runs. The bodies are small and the wide run passed (317 tests over e2e, verdicts, scope, validation and limits). But a route that streams its request body would now see it only after it is complete. None does today.
- Not checked:
  - Windows for any of this, in particular `private_opener` there (mode is ignored, so it should be harmless) and the new identity-retry error.
  - The 1009 close through the real `mcuscoped` process. It was only checked on a uvicorn built like `daemon._serve`'s.
