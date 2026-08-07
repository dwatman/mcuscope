# Architecture

Per-module notes for `host/mcuscope/`, covering the design constraints that are not obvious from the code. Each module's own docstring is the finer-grained reference; `docs/SPEC.md` is the contract and wins over both.

Request flow: `mcu` CLI (httpx) -> REST/WS on 127.0.0.1 -> daemon -> serial link -> UART -> MCU. Only the daemon touches the port, so there is no "port busy", and capture continues with no client attached.

## Modules

- **`protocol.py`** - pure, no I/O, and the shared source of truth for both daemon and
  simulator: keep it that way, and fully unit-tested. Encodes/decodes the line protocol
  (`>SEQ CMD`, `<SEQ OK/ERR`, `!` events, anything else debug), 7-bit ASCII, LF-terminated,
  255 bytes max. Holds the error-code table, seq wrap (`next_seq`, 1-65535, never 0) and
  CAN frame parse/format; malformed CAN events return `None` rather than raising. Every
  `int()` over a wire token is length-gated first, because a number above CPython's
  4300-digit limit raises a bare `ValueError` that no `ProtocolError` handler catches.
  `PlotDecoder` is the one exception to "no state": it holds the typed-stream `!pd` cache,
  because a `!ps` sample names its stream by a sid carried *inside* the line, and a caller
  given only `decode_plot_sample(raw, definition)` has to reimplement the grammar to find
  that sid before it can look the definition up. Feed it whole lines.
- **`store.py`** - SQLite capture (WAL, FK cascade). A **single async writer task**
  drains a queue and is the only writer; it allocates `lines.id` itself so a whole batch
  goes in with one `executemany`, and callers await a future to get the inserted row
  back. It stays on the event loop deliberately, which is what keeps retention chunks and
  `incremental_vacuum` out of an open writer transaction; the cost is bounded by capping
  the rows one commit absorbs, and a commit past `_SLOW_COMMIT_S` warns. A write that
  fails increments `write_errors`, which `/status` reports, because a silent write failure
  was invisible on every surface. WebSocket subscribers are fed by fan-out with
  drop-oldest. Schema is `lines`, `can_frames` and `sessions` (SPEC 3.5) plus
  `plot_points` (SPEC 9.2); later columns arrive through `_MIGRATIONS`, since
  `CREATE TABLE IF NOT EXISTS` cannot alter an existing table. Retention is age-based with
  a `min_sessions` floor, plus an opt-in size cap measured against live content rather
  than file size. `match_executor()` runs every user-supplied regex (`/lines`, `/wait`,
  `/assert`), the `/can/frames` join and the row counts, the heaviest reads the API
  serves; the point is keeping them off the *default* executor, which joins the serial
  reader thread on detach and shutdown and must never queue behind analytics. **User
  patterns compile with the third-party `regex` module, never stdlib `re`** - `re` holds
  the GIL for a whole backtrack, so a 7-character pattern froze the process and the pool
  was decoration. `regex` releases the GIL and honours `timeout=`, which `_make_regexp`
  turns into a per-call ceiling plus a per-query budget; exceeding either raises
  `MatchBudgetExceeded` and the API answers 400, never a timeout result (which the CLI
  would report as exit 2). Internal patterns stay on `re`.
- **`link.py`** - the transport itself: `Link`, `open_link()`, and the two real adapters.
  `in_waiting` is a true byte count on a native port but a 0/1 readability poll on
  `socket://` (a sized read there fetched one byte per syscall, 0.2 MB/s against 600),
  so the drain strategy differs by transport and `SerialLink` picks it once at open.
  Also holds the URL-scheme allowlist and `cancel_read`/`cancel_write`, which the URL
  handlers do not implement and now say so with a bool rather than a suppressed
  AttributeError. `SerialPort` accepts the opener, so `FakeLink` can drive the reader's
  success path in-process; before that the only reachable transport was a real one, and
  every reader test drove a device that could never open.
- **`serial_link.py`** - `SerialPort` (reader thread, reconnect backoff, seq/pending
  machinery) and `PortManager`. On command timeout the pending entry is popped, so a late
  response is **logged but not delivered** (SPEC 3.2). Reconnect is automatic and its
  backoff presence-gated (`_retry_wait`): an absent device node is cheap to test for, so it
  is polled at `PRESENCE_POLL_S` and opened the moment it returns (sub-second replug),
  while a device present but unopenable keeps the doubling wait. `cached_comports()`
  gives port enumeration a short shared TTL, so N polling reader threads do not each pay
  for a setupapi/sysfs scan; `/devices` shares it too, from a worker thread, because a
  setupapi scan is far too slow to run on the event loop. The transport itself lives in
  `link.py`; what stays here is the retry policy, the counters and the sys rows.
  `_EpisodeNotice` carries the last of those: five conditions that shed data report once
  per episode rather than once per occurrence, and each used to be a bare bool set beside
  its report and cleared a hundred lines away. A line that
  fails to store costs that line only: batching them into one comprehension once let a
  single malformed line discard the rest of the burst. Counters carry across detach and
  reattach in `_carried`, keyed by alias, because the alias is the port slot.
- **`server.py`** - `create_app(config)` builds the FastAPI app; its lifespan starts the
  store, opens the automatic session, attaches autoconnect ports and records daemon
  start/stop system rows. Implements every SPEC 3.4 endpoint plus `/ws`; exceptions become
  an `{"error": msg}` envelope. `/ws` frames are arrays of rows, and an empty one is the
  idle keepalive (`WS_KEEPALIVE_S`) that makes a vanished client surface as a failing write
  rather than a queue held until the next row.
- **`lockfile.py`** - the single-writer guard on a capture (SPEC 3.2): an OS lock
  (`fcntl.flock` / `msvcrt.locking`) on `<db_path>.lock`, taken by `mcuscoped` before
  anything opens the database. A lock rather than a pid file, so a crashed daemon leaves
  nothing stranded. The Windows half only runs in CI.
- **`daemon.py`** - `mcuscoped` entry point: load config, apply `--host/--port` overrides,
  take the capture lock, probe for a port conflict, record the pid, install the signal
  handler that releases that record, wire the `/shutdown` callback, `uvicorn.run`. The port
  probe runs on both platforms and covers every resolved address: Windows needs
  `SO_EXCLUSIVEADDRUSE` to refuse the bind at all, and POSIX needs it early, because
  uvicorn's own `EADDRINUSE` arrives *after* `pidfile.claim()` and the failing daemon would
  take the running one's pid record with it.
- **`pidfile.py`** - the `<host>-<port>.pid` record `mcu daemon stop` uses to find and stop
  a daemon it did not start. Advisory, not a lock (`lockfile.py` is the lock): a stale
  record is overwritten, and a live one is left alone whoever it names. It may not defer to
  the port probe, which closes long before either daemon binds, so two daemons on one port
  could otherwise trade the record and leave the survivor unrecorded.
- **`_stdio.py`** - repairs std streams that an interpreter handed over as `None` (pythonw,
  some Windows launchers), attaches a console where there is one, widens the stdout encoding
  so a redirected stream cannot die on a character outside the console code page, and wraps
  each console script so a crash lands in a file instead of vanishing. That crash log is the
  deliberate trace for a genuine bug, so it must not be replaced by a blanket handler
  upstream. Its warnings go to stderr, so `mcu --json` stays parseable when a stream needed
  repairing.
- **`config.py`** - TOML config via `tomllib` + platformdirs. A missing file is fine.
- **`update_check.py`** - the release check (SPEC 3.6): one PyPI request a day at most,
  cached under `user_cache_dir` so restarts do not re-ask, reported only through
  `/status.update` and the UI badge. Never raises into the loop, never blocks startup, and
  never writes to the capture. Off via `[update] check = false` or
  `MCUSCOPE_UPDATE_CHECK=0`; **conftest sets that env var**, so no test ever hits the
  network (a stubbed `httpx.MockTransport` covers the real path).
- **`cli.py`** - the `mcu` typer app. **Exit-code contract (SPEC 4): 0 success/match, 1
  error or bad usage, 2 timeout, 3 daemon unreachable.** `mcu assert` is the documented
  exception: `1` means the assertion failed, and it never exits `2`. Global options
  (`--json`, `--port/-p`, `--url`, `--token`) are hoisted to the front of argv in `main()`
  so they work in any position (`mcu i2c rd 48 2 --json`). Two typer traps: in
  non-standalone mode the `Exit` code comes back as the call's **return value**, not an
  exception (`main()` must return it); and typer vendors its own click, so `typer.Abort`
  is not `click.exceptions.Abort` - catch both (`ABORT_EXCEPTIONS` and friends) or
  control-flow exceptions escape to typer's rich handler and print a traceback at the user.
  User patterns compile with `regex` here too, since a pattern the daemon accepts must not
  crash the client.
