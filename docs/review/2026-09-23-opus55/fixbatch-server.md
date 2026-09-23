# Fix batch: server

HEAD: `b994076c88c875ad43c30789cda6039d147b7abe` (the tree also held other batches' uncommitted work).

Scratch: `~/tt-data/mcuscope-2026-09-24/fixbatch-server/`.
- `copy/` is the private mutation tree, driven by `mutate.py`. Results are in `mutate.log` and `mutate2.log`.
- `floorcopy/` plus the `pyd202/` and `pyd1/` venvs hold the pydantic floor check.
- `second-opinion.md` holds the second opinion.

Every mutant ran from `copy/host` with `PYTHONPATH` set to it; the copy's `server.py` import path was printed to confirm it.

## Per finding

### Server 1: a cancelled bundle becomes a 400 and a false error log
- Change: `server.py:2827` wraps `interrupt()` in `suppress(sqlite3.ProgrammingError)`. After the copy, its connection is closed, and `checkpoint()` stops the build.
- Tests:
  - `test_server_fixdiff_exports.py:46`: abandon on a closed connection does not raise.
  - `test_server_fixdiff_exports.py:55`: a bundle cancelled while writing a member logs no `failed` line. It carries a positive control on the same logger.
- Revert (suppress removed): caught.
- The report's second half, re-raising the `CancelledError` if `abandon()` raises, is not done.
  - Nothing else in `abandon()` can raise: `_remove_all` suppresses `OSError`.
  - A test could only reach that wrapper by injecting a fault.

### Server 2: a forbid hit is lost when the expect scan is cut
- Change: `server.py:3115-3140`. The forbid scan's hits are applied, and a hit breaks, before the expect scan starts. A cut in either scan marks the batch unjudged.
- Tests:
  - `test_server_fixdiff_verdicts.py:63`: with the forbid hit and the expect stalled, the answer is `fail` with `forbid[0].matched`, `checked_lines: 1`, `dropped: 0`.
  - `:73`: a clean forbid scan followed by a cut expect scan gives `dropped: 1`, `checked_lines: 0`.
- Revert:
  - Pre-fix order (the break removed): caught.
  - The `checked += len(candidates)` on the hit removed: caught.
- The reviewer's M4 line (`forbid_scan is not _UNJUDGED`) no longer exists.

### Server 3: a cut scan answers `pass` under `allow_empty`
- Decision: `checked == 0` with `dropped > 0` is `empty` whatever `allow_empty` says. The reason is `no lines were judged: N dropped unjudged`.
  - Keyed on `dropped`, not only on the scan cut, so feed sheds count too: a shed line is just as unjudged.
- Change: `server.py:2999`, a new branch before the `allow_empty` one.
- Tests:
  - `test_server_fixdiff_verdicts.py:88`: both `allow_empty` values give `empty` with the new reason.
  - `:99` is the control: a quiet window still passes under `allow_empty`, and without it keeps `no lines were checked in the window`.
- Revert (branch removed): caught.
- `/wait`'s slow-pattern timeout on every retry is unchanged; whether a cut should name the pattern is left to the owner.

### Server 4: `purge before_ts` deletes beyond the span it reports
- Change: `server.py:1766` calls `delete_before_ts(body.before_ts, max_id=hi_id)`. The store batch's `max_id` had landed by then.
  - `or n == 0` is now load-bearing: `hi_id` is None there and would unbound the delete. A comment says so.
- Tests:
  - `test_server_fixdiff_verdicts.py:112`: a row committed between the count and the delete, stamped before the cutoff, survives, and `deleted` equals the count.
  - `:137`: the same with nothing counted, so the answer is `deleted: 0` and the late row survives.
- Revert:
  - `max_id` dropped: caught.
  - `or n == 0` dropped (the reviewer's M6): caught.

### Server 5: SPEC 3.1 claims a `Sec-Fetch-Site` header these loads do not carry
- No code change. The SPEC wording is below.

### Server 6: no pydantic floor
- Change: `host/pyproject.toml:43-44` adds `"pydantic>=2.0.2"`, with a one-line note.
- No upper bound: the file's own policy caps only pre-1.0 dependencies.
- 2.0.2 is the lowest version verified. The venv is `pyd202`: Python 3.10.20, pydantic 2.0.2, fastapi 0.125.0.
  - Run from `floorcopy`, one file at a time: `test_server_fixdiff_exports` 7, `test_server_fixdiff_verdicts` 7, `test_config_loader_host_device` 4, `test_server_request_validation` 24 and `test_config_api` 30, all passed.
- `uv pip install --dry-run -e floorcopy/host pydantic==1.10.26` is now unsatisfiable (`pyd1`).
- `host/uv.lock` is untracked. It already shows the new specifier because another `uv run` re-locked it.

### Server 7: a 500 carries no framing headers
- No code change. Only `daemon.py` could wrap the app outside Starlette's `ServerErrorMiddleware`, and it is outside my files. The SPEC wording is below.

### Server 8: untested and redundant branches
- M3: `test_server_fixdiff_verdicts.py:159`. `enabled: false` with a multicast dest is 200, and `enabled: true` is 400. Mutant `if body.enabled:` changed to `if True:`: caught.
- M5: `_FrameDenial`'s non-http early return is deleted (`server.py:889`). `test_server_guards.py` passes, 24 tests.
- M6: now tested, see Server 4.

### Server 9: SPEC text nits
- Wording below.

### Chrome F1, server side: stamp the version into the page
- Change: `server.py:912` `_NoCacheStatic.file_response`. For `index.html` it serves the body with `__MCUSCOPE_VERSION__` replaced by `__version__`.
  - The ETag is a sha256 of the stamped body, and a matching `If-None-Match` gets a 304.
  - There is no `Last-Modified`, so a version bump over an unchanged file is never answered 304.
  - `Cache-Control: no-cache` is kept. Other files are unchanged.
- `server.py:925`: `_stamped_index` is an `lru_cache` keyed on `(path, mtime_ns, size)`.
- Tests:
  - `test_server_fixdiff_exports.py:209`: the served `/ui/` and `/ui/index.html` carry `content="<version>"` and no placeholder; the 304 on the ETag works; `app.js` is untouched.
  - `:225`: over an unchanged file, a changed version answers 200 to the old ETag.
- Revert:
  - Stamping off: caught.
  - ETag from the file stat: caught.
  - 304 branch off: caught.
- Also checked: passes on the starlette 0.44.0 floor venv (`fixdiff/floor-venv`, with `PYTHONPATH` set to `floorcopy`), with `test_webui.py` 12 passed. HEAD is 200 with no body. A cross-site navigate to `/ui/` is still 200.

### Chrome F2, server side: `wait=1` on `/export` and `/bundle`
- Change:
  - `server.py:1543` and `:1587` declare `wait: bool = False`, which the undeclared-param guard reads. It is passed through `_build_bundle`, `:1617`.
  - `_run_export`, `:2836-2860`: with `wait`, a full pool loops on `app.state.export_freed` (`:471`), an `asyncio.Event` that `dec()` sets.
    - There is no await between the re-check and the claim.
    - Nothing exists before admission, so a disconnect while waiting leaves nothing behind.
- Tests:
  - `test_server_fixdiff_exports.py:132`, with the queue patched to 0 so an admitted build enters at once:
    - a full pool 503s without `wait`;
    - an `export` waiter and a `bundle` waiter park;
    - freeing one slot admits exactly one of them, and the other waits again while the loop keeps serving;
    - all end 200 and leave no temp files.
  - `:168`: a waiter cancelled while it waits claims no slot, starts no build and leaves no file.
  - `:197`: `wait` is accepted, and `wiat` is still a 422.
- Revert:
  - `wait` ignored: caught.
  - No `set()` on release: caught (pytest-timeout).
  - No `clear()` before a re-wait: caught (pytest-timeout, the loop spins).
  - Export or bundle not passing `wait`: both caught.

### Link F3: the loader keeps ports `PUT /config/ports` refuses
- The shared rule now lives in one place, `config.py:298-311`: `MAX_DEVICE_LEN`, `MAX_SERIAL_LEN` and `control_char_field(device, serial_number)`.
  - `server.py` imports them (`:58`). The local constants and `_control_char_field` are deleted.
  - Both attach paths call the shared check (`:1148`, `:1423`). `mcuscope.server.MAX_DEVICE_LEN` still resolves, so `test_server_request_validation.py` is unchanged.
- Loader (`config.py:450-520`) warns and skips:
  - a device longer than 512 or a serial_number longer than 128;
  - a control character in either field;
  - a repeated alias, keeping the later entry;
  - a fifth case the report did not list: a blank device with no serial (`"   "`). The PUT strips it and refuses it (class 60), so the guard now judges stripped values.
- Test: `test_config_loader_host_device.py:47-89` extends `test_the_loaded_ports_save_back`.
  - It adds the length, control-character, blank and duplicate cases, each with its own warning text.
  - An at-limit entry is kept, which pins that the bounds are no tighter than the PUT's.
  - GET then PUT answers 200.
- Revert:
  - Each skip removed (length, control character, blank strip, duplicate): all caught.
  - `>` changed to `>=`: caught.
  - Keeping both duplicates: caught.

## Other test runs (shared tree, one file at a time, after the edits)
- Passed: test_assert 39, config_ports_eol 3, cli_send_verdicts 14, config_api 30, config_bools 4, daemon_r2026_09_12_bundle 5, fixdiff2_daemon 5, eol 62, plotjuggler 46, review_r2_server 23, hardening 82, server_purge_and_sessions 3, review_r2_config 20, server_lifespan 12, rulings_daemon_config 28, server_guards 24, server_live_verdicts 23, sessions 39, server_exports 16, cli_ux 27, server_request_validation 24, daemon_r2026_09_12_config 4, e2e 39, webui 12, session_bundle 9.
- test_regressions: one failure, `test_line_ids_are_not_reused_after_the_table_empties`. It touches only `Store`, and it passes in my copy with HEAD's `store.py`, so it comes from the store batch (see Not done).
- `uv run python -m ruff check .`: clean.

## SPEC wording
- 3.1, replace "Such a load carries `Sec-Fetch-Site`, so a request with ... is refused 403 too." with:
  - "A browser sends `Sec-Fetch-Site` only to a potentially trustworthy origin (HTTPS or loopback), so a loopback request with `Sec-Fetch-Site: cross-site` or `same-site` (another port on this host is same-site) is refused 403 too. A plain-HTTP load of a non-loopback address (the LAN address of a `0.0.0.0` bind) carries no `Sec-Fetch-*` header, so the guard does nothing there: the token is the bound, and without one such a page can still trigger a GET."
- 3.1, replace "Every HTTP response carries `X-Frame-Options: DENY` and ..." with:
  - "Every response a route or guard produces carries `X-Frame-Options: DENY` and `Content-Security-Policy: frame-ancestors 'none'` (the plain-text 500 of an unhandled error does not, and has nothing to frame)."
- 3.3.1, the undeclared-parameter rule:
  - "`/ws` (whose `?token=` the token guard reads), the root redirect `/` and the static UI are exempt."
- 3.4 `/assert`, after "... `allow_empty: true` judges it as `pass`/`fail` like any other.", add:
  - "A window that checked no lines while `dropped` is non-zero stays `empty` whatever `allow_empty` says, with the reason `no lines were judged: N dropped unjudged`: lines came and nothing judged them, which is not a quiet window."
- 3.4 `/purge`: the two `before_ts` sentences become one:
  - "`before_ts` selects by time, not as an id range: every line stamped before it, wherever its id falls (ids follow commit order, which can differ from stamp order, see the time bounds below). `id_from`/`id_to` report the lowest and highest id among them, and the delete takes no row above that `id_to`, so a line committed during the purge is not deleted outside the span reported."
- 3.4 `GET /sessions/{id|name}/export`, after the pool/503 sentence:
  - "With `?wait=1` (on `/export` and `/bundle`) the request waits for a slot instead of the 503, which is how the web UI's download link uses it, since a browser download cannot show a refusal; a client that leaves while waiting starts no build."
- 9.1, "Technology constraints", append:
  - "`index.html` is served with the daemon's version in `<meta name="mcuscope-version">` (its `__MCUSCOPE_VERSION__` placeholder) and an ETag of the stamped page, so a page restored from cache knows which build it is."
- `docs/ARCHITECTURE.md:43` (not my file): "... 2 workers, 2 queued, 503 beyond (or a wait, with `wait=1`)."

## Proposed CHANGELOG lines
- **Upgrade:** a live `/assert` window whose lines were all dropped unjudged (shed, or a scan cut at the deadline) is `empty` with the reason `no lines were judged: N dropped unjudged`, even with `allow_empty` (`--allow-empty`), where it passed.
- A live `/assert` whose expect scan runs out of time keeps a forbid match already found, and fails on it.
- `purge before_ts` no longer deletes lines committed while it runs; the delete takes exactly the id span it reports.
- The daemon config loader warns about and skips a port the settings dialog could not save back: a device over 512 or serial number over 128 characters, a control character in either, a blank device with no serial, or a repeated alias (the later entry is kept).
- A session export or bundle cancelled while the daemon stops no longer logs a false `export failed` error.
- `GET /sessions/{ref}/export` and `/bundle` accept `wait=1`, which waits for an export slot instead of a 503; the web UI's download links use it.
- The web UI page carries the daemon version it was served by, so a page restored from the browser cache after an upgrade shows the reload notice.
- `pydantic>=2.0.2` is now declared: an environment holding pydantic 1.x no longer installs a daemon that cannot start.

## Not done
- `tests/test_regressions.py::test_line_ids_are_not_reused_after_the_table_empties` fails against the current `store.py`. It passes with HEAD's `store.py` in the copy, so the store batch owns it.
- Server 1's second half (re-raise if `abandon()` raises): skipped, reason given above.
- Server 7 code fix (a wrapper in `daemon.py`): not my file; SPEC wording instead.
- The CLI does not use `wait=1`; it can show a 503. Not asked for.
- `docs/ARCHITECTURE.md:43`: wording above.
- `test_config_api.py:440` docstring nit (link report): not in my brief.

## Doubts
- Duplicate alias, keep-first or keep-last:
  - I first kept the first entry, which matches the PUT naming the second occurrence.
  - The second opinion (opus-medium; `fable-low` is barred by the owner's CLAUDE.md) argued for keeping the last: attach replaces, so the last entry is the board that ran, and the dialog's next save would delete it from the file.
  - I switched to keep-last. The owner can flip it.
- A `wait=1` bundle holds `store._sweep_lock` while it waits for a slot.
  - The second opinion: no deadlock, because slot holders are pool threads that never take the lock. A delayed size sweep only overshoots the cap until it runs; it cannot lose data.
  - The alternative, admission before the lock, needs a split admit/run with release on every early return. Not done.
- The waiter wake is wake-all, not FIFO, so under sustained load a later request can overtake an earlier waiter (starvation is possible, not a deadlock).
- `dropped` also counts shed rows of other channels: the subscriber filters by port only, and `chan` is filtered after. So a window with only off-channel sheds and no judged line is `empty` rather than an `allow_empty` pass. This errs toward "retry".
- `_stamped_index` reads the file on the loop, once per change of a small package file. I accepted this against class 1 rather than make the read async.
