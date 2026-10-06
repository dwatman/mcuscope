# Fix-diff review: server

Scope: `git diff 5eaaeeb` of `server.py`, `daemon.py`, `lockfile.py`, `pidfile.py`, `_stdio.py`, `dirs.py`, the SPEC 3.1/3.4 route edits, and the new and edited tests of the server, server2 and daemon batches.
Revert-verification: `~/tt-data/mcuscope-2026-10-05/fixdiff-server/mutate.py` over `muts1.json`, 24 mutations, results in `mut1.log`; every file restored byte-identical (md5 compare, `git diff --stat` unchanged).

## FD-SERVER-1 HIGH CONFIRMED: after a capture failure the CLI reports a version mismatch instead of the 503's cause
- `server.py:654-661` `_store_error`, and its delegation to `_unhandled_error` (`:644`).
- Failure: a handler registered for `StoreError` runs in Starlette's ExceptionMiddleware, inside `_VersionHeader` and `_FrameDenial`, yet adds `_NO_FRAMING` and `identity` itself (copied from `_unhandled_error`, which is right only when ServerErrorMiddleware sends it).
  - Every 503 (and the healthy-capture 500 it delegates) carries `x-mcuscope-version`, `x-frame-options` and the CSP twice.
  - httpx joins the duplicate: `X-Mcuscope-Version: 0.5.0, 0.5.0`, and `check_daemon_version` refuses it.
- Repro, real daemon (`live_drive.py`, port 18962): unlink `cap.db.lock`; 60 s later `/status` has `capture_error` and `writer_alive: false` (the wiring works); then `mcu assert --forbid X` exits 1 with `error: daemon at http://127.0.0.1:18962 is mcuscope 0.5.0, 0.5.0, this mcu needs >= 0.5.0`.
  - In process (`probe_503.py`): `/lines`, `/sessions`, `/plot/channels`, `/lines/export`, `/assert`, `/ports` all 503 with the cause and `x-frame-options: ['DENY', 'DENY']`.
- An agent reading that message upgrades `mcu` rather than restarting the daemon, the one fix `capture_error` names.
- Also changed by the handler: a healthy-capture `StoreError` is no longer re-raised by ServerErrorMiddleware, so uvicorn's own traceback and a TestClient's default `raise_server_exceptions` no longer fire for it; `_unhandled_error`'s comment ("Sent by ServerErrorMiddleware, outside every app middleware") is now false on this path.
- Test gap: `test_server_capture_failed.py` raises from a synthetic `/_boom` route with `capture_error` set by hand (class 63) and asserts no header, so it cannot see this.
- Fix: `_store_error` returns its 503 without adding headers; for the healthy case `log.exception(...)` and a bare 500 `JSONResponse` (or re-raise so ServerErrorMiddleware handles it as before).
  - Test: a real failure (unlink the lock, `_run_tick_checks()`), then a real read, asserting status, body and exactly one version header; and `check_daemon_version` accepts its headers.
- Class 87 (the inverse: a handler inside the middleware duplicating what the middleware adds). Worth a registry note under 87.

## FD-SERVER-2 MEDIUM CONFIRMED: the 64 KiB body cap is bypassed by `Content-Length` plus `Transfer-Encoding: chunked`
- `server.py:996-1003` `_BodyLimit.__call__`: a present `Content-Length` is judged and the app is called directly ("the server enforces the declared length").
- h11 0.16 (the only HTTP parser installed) frames a request carrying both headers as chunked and ignores the length, so the chunked cap never runs.
- Repro (`probe_te_cl.py`, uvicorn on 18961): `POST /marker` with `Content-Length: 5`, `Transfer-Encoding: chunked` and a 1,000,026-byte chunked JSON body answers `200 {"line_id":3}`; the same body without the `Content-Length` line answers 413.
- SPEC 3.4's 413 line ("refused on its Content-Length before any of it is read, or once a chunked body passes the cap") is false for this request.
- Fix: test `transfer-encoding` first: any chunked request takes the counted path whatever `Content-Length` says (or refuse the pair with 400). Add the pair to `test_server_request_limits.py` over real uvicorn.
- Class N5 (SEC-4 reopened).

## FD-SERVER-3 MEDIUM CONFIRMED: the daemon's capture-lock wiring has no test
- `daemon.py:526` `app.state.capture_lock = lock`.
- Mutation `daemon-state-lock` (line replaced by `pass`) passes `test_lockfile_identity`, `test_daemon_startlog`, `test_server_status_and_listing`, `test_capture_lock`, `test_daemon_startup` (70 passed).
- Every lock-check test sets `app.state.capture_lock` itself, so dropping the one line that connects RES-10's runtime half in production goes unnoticed.
- Fix: a `daemon.main` test (stub `_serve` to capture the app) asserting `app.state.capture_lock` is the acquired lock; or drive `main` far enough that the lifespan registers `lock.verify`.

## FD-SERVER-4 LOW CONFIRMED: `_stdio._write_report` 0700 dir has no test
- `_stdio.py:374`. Mutation `stdio-dirs` (back to `os.makedirs`) passes `test_lockfile_identity`, `test_daemon_startlog`, `test_stdio` (35 passed).
- The daemon batch report lists the site as fixed but tests only the lock and pid paths.
- Fix: one `@posix` test that `write_startup_log` into a missing `MCUSCOPE_DATA_DIR` creates it 0700.

## FD-SERVER-5 LOW CONFIRMED (by reading): class N7 still open in the daemon batch's own files
- `config.py:606` `_write_doc`: `path.parent.mkdir(parents=True, exist_ok=True)` and `tmp.write_bytes` create the config dir and `config.toml` with the default mode (0755/0644 under umask 022); that file can carry `server.token`.
- `update_check.py:215`: cache dir by plain `mkdir`.
- `pidfile.py:181`, `:330`: `os.open(..., O_CREAT | O_EXCL ...)` with the default 0o777 mode, so the pid record is 0644 (inside a 0700 data dir only when that dir was created by this version).
- SEC-6's ruling named the data dir, DB and lock; the class sweep ("every `open`/`os.open` with `O_CREAT`, `makedirs`, `mkstemp`") reaches these too.
- Fix: `make_private_dirs` for both dirs, `0o600` on the config temp and pid opens.

## FD-SERVER-6 LOW SUSPECTED (Windows): the identity retry in `acquire` is unbounded and its compare unproven on Windows
- `lockfile.py:146-150`: when `_is_current` is false the loop closes and reopens with no deadline check and no sleep.
- Any persistent mismatch between `os.fstat(fd)` and `os.stat(path)` spins the daemon start at full CPU forever, and `verify()` would stop the capture on the first tick.
- On POSIX the two agree (driven by `test_lockfile_identity`). On Windows CPython fills `st_dev`/`st_ino` for `stat` and `fstat` by different calls (by-name fast path versus by-handle); nothing here has run it.
  - `test_capture_lock.py` on Windows would show a mismatch only as a hang.
- Fix: bound the identity retry by the same deadline (raise `LockError` or a distinct error), and add "a daemon starts and `/status` stays `capture_error: null` past one tick" to the Windows checklist.

## FD-SERVER-7 LOW CONFIRMED: an expect-only assert whose expects all matched is `incomplete` when rows were shed
- `server.py:3394`: the condition exempts only a matched forbid; a met expect is just as decided (shed rows cannot un-see it), and with no forbids there is nothing left to judge.
- Repro (`probe/test_probe_expect_only.py`, the batch's `shedding` fixture): `{"expect": ["HELLO"]}` answers `incomplete`, `3 lines were dropped unjudged; retry, or set allow_dropped`, with `expect[0].matched: true`.
- Matches SPEC 3.4's literal text and the OP-9 wording, so this is a ruling question rather than a code slip.
- Question (recommended first): treat a verdict as decided when every expect matched and there are no forbids, alongside a matched forbid / keep `incomplete` for any shed window.

## FD-SERVER-8 LOW SUSPECTED: WebSocket frames are still buffered up to uvicorn's 16 MiB
- `server.py:2506`: `/ws` reads and discards client frames; neither `create_app` nor `daemon._serve` sets `ws_max_size`, so each frame is assembled up to 16 MiB first.
- Same class as SEC-4 (N5), on the one input path the body cap does not cover. Confirm by sending a 10 MB text frame and watching RSS.
- Fix: pass `ws_max_size=MAX_BODY_BYTES` (or smaller) to uvicorn.

## FD-SERVER-9 NIT CONFIRMED: `make_private_dirs` swallows a file at the target path
- `dirs.py:44-49`: for `path` that is an existing file, `mkdir` raises `FileExistsError`, which is swallowed, so it returns silently where `os.makedirs(exist_ok=True)` raised. The caller then fails on its own open, so only the error text changes.
- `test_make_private_dirs_is_idempotent_on_a_file_parent` drives a file *parent* (which does raise), and its name says the opposite of what it asserts.
- Fix: catch `FileExistsError` only when `os.path.isdir(d)` afterwards; rename the test.

## FD-SERVER-10 NIT CONFIRMED: the 422 cut counts the repr, not the value
- `server.py:637-639`: `"A" * 5000` is reported `... (5002 characters)`; the test pins 5002.
- Fix: count `len(value)` for a str, or say "(N characters quoted)".

## Checked and fine
- OP-7, class 84: every verdict read routes through `_verdict_rows` (retrospective `/assert` query and count share one `scope`) or `CaptureWatch` (live `/wait` and `/assert`); `/wait` has no retrospective path. Mutations `own-verdict_rows` and `own-capturewatch` each caught.
- OP-9 `incomplete`: mutations dropping the forbid condition and the `allow_dropped` opt-in each caught; reason text unique to the path and pinned.
- `send_failed`: mutation `if False` caught; ok and raw sends are positive controls.
- 503 mapping: both directions of the `capture_error` branch caught; real reads after a real lock loss all answer 503 with the cause and log no "unhandled error" (`probe_503.py`).
- SEC-4: length cap, chunked cap and chunked skip mutations caught; 403 precedes 413.
- Capture lock: `realpath`, `_is_current`, `verify`, lock mode 0600, `make_private_dirs` in `acquire`, dir mode 0700, pid-file dir each caught; `verify` is a no-op when not held (`--ignore-capture-lock`, `:memory:`).
- Live end to end: a deleted lock file sets `capture_error` within one 60 s tick and `writer_alive` false (`live/drive.out`).
- MODULES-2 `:memory:` guard caught.
- SEC-6 on an existing dir: never chmod'd (test pins 0755 kept; an existing 0640 lock file kept). Windows branch is plain `os.makedirs`, by reading.
- RES-8 `_ShortCaptureError`: filter off, filter on every record, and filter not removed each caught; the test has a RuntimeError positive control on the same stream.
- Baseline: the 14 target test files, 257 passed (`baseline.log`); the store-batch failures the server batch reported are gone.

## Not covered
- Windows: the `stat`/`fstat` compare, `realpath` on mapped and SUBST drives, the chunked path.
- OP-9 with a real shed (the tests stub `dropped_total`); SEC-7 and SEC-3 beyond reading their tests (the server batch's own 21-mutation run covers them).
- The 413 over a `0.0.0.0` bind with a token.

## The two questions
1. Least confident: FD-SERVER-6, the Windows identity compare. It is reasoned, not driven, and its failure mode is a silent startup hang; it belongs on the owed Windows checklist with the MODULES-2 Windows half.
2. Not thought about: the headers a handler adds depend on which middleware layer sends it (FD-SERVER-1), and the parser's framing rules decide what a body cap sees (FD-SERVER-2). Both came from looking at what the fix's code touches (the CLI that reads the 503, the HTTP parser in front of the middleware), not at the finding.

Scratch: `~/tt-data/mcuscope-2026-10-05/fixdiff-server/` (628 KB; probes, `mutate.py`, `muts1.json`, `mut1.log`, `live/` with its capture). No process left running.
