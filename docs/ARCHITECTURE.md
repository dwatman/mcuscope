# Architecture

Per-module notes for `host/mcuscope/`, covering the design constraints that are not obvious from the code.
Each module's own docstring is the finer-grained reference; `docs/SPEC.md` is the contract and wins over both.

Request flow: `mcu` CLI (httpx) -> REST/WS on 127.0.0.1 -> daemon -> serial link -> UART -> MCU.
Only the daemon touches the port, so there is no "port busy", and capture continues with no client attached.

## Modules

- **`protocol.py`** - pure, no I/O, the shared source of truth for daemon and simulator: keep it that way, and fully unit-tested.
  - Encodes/decodes the line protocol (`>SEQ CMD`, `<SEQ OK/ERR`, `!` events, anything else debug): 7-bit ASCII, LF-terminated, 255 bytes max.
  - Holds the error-code table, seq wrap (`next_seq`, 1-65535, never 0) and CAN frame parse/format; malformed CAN events return `None` rather than raising.
  - Every `int()` over a wire token is length-gated first: a number above CPython's 4300-digit limit raises a bare `ValueError` that no `ProtocolError` handler catches.
  - `PlotDecoder` is the one exception to "no state": it holds the typed-stream `!pd` cache, and must be fed whole lines.
    A `!ps` sample names its stream by a sid carried inside the line, so a caller given only `decode_plot_sample(raw, definition)` would have to reimplement the grammar to find that sid before it could look the definition up.
- **`store.py`** - SQLite capture (WAL, FK cascade).
  - A **single async writer task** drains a queue and is the only writer.
    It allocates `lines.id` itself so a whole batch goes in with one `executemany`, and callers await a future to get the inserted row back.
  - The writer stays on the event loop deliberately, which keeps retention chunks and `incremental_vacuum` out of an open writer transaction.
    The cost is bounded by capping the rows one commit absorbs, and a commit past `_SLOW_COMMIT_S` warns.
  - A failed write increments `write_errors`, which `/status` reports, because a silent write failure was invisible on every surface.
  - WebSocket subscribers are fed by fan-out with drop-oldest.
  - Schema: `lines`, `can_frames` and `sessions` (SPEC 3.5) plus `plot_points` (SPEC 9.2).
    Later columns arrive through `_MIGRATIONS`, since `CREATE TABLE IF NOT EXISTS` cannot alter an existing table.
  - Retention is age-based with a `min_sessions` floor, plus an opt-in size cap measured against live content rather than file size.
  - `match_executor()` runs every user-supplied regex (`/lines`, `/wait`, `/assert`), the `/can/frames` join and the row counts: the heaviest reads the API serves.
    The point is keeping them off the *default* executor, which joins the serial reader thread on detach and shutdown and must never queue behind analytics.
  - **User patterns compile with the third-party `regex` module, never stdlib `re`.**
    - `re` holds the GIL for a whole backtrack: a 7-character pattern froze the process and the pool was decoration.
    - `regex` releases the GIL and honours `timeout=`, which `_make_regexp` turns into a per-call ceiling plus a per-query budget.
      Exceeding either raises `MatchBudgetExceeded` and the API answers 400, never a timeout result (which the CLI would report as exit 2).
    - Internal patterns stay on `re`.
- **`link.py`** - the transport itself: `Link`, `open_link()`, and the two real adapters.
  - `in_waiting` is a true byte count on a native port but a 0/1 readability poll on `socket://`, so the drain strategy differs by transport and `SerialLink` picks it once at open.
    (A sized read on a socket fetched one byte per syscall: 0.2 MB/s against 600.)
  - Holds the URL-scheme allowlist, and `cancel_read`/`cancel_write`, which the URL handlers do not implement and now say so with a bool rather than a suppressed AttributeError.
  - `SerialPort` accepts the opener, so `SourceLink` can drive the reader's success path in-process; before that, every reader test drove a device that could never open.
- **`serial_link.py`** - `SerialPort` (reader thread, reconnect backoff, seq/pending machinery) and `PortManager`.
  The transport lives in `link.py`; what stays here is the retry policy, the counters and the sys rows.
  - On command timeout the pending entry is popped, so a late response is **logged but not delivered** (SPEC 3.2).
  - Reconnect is automatic and its backoff presence-gated (`_retry_wait`).
    - An absent device node is cheap to test for, so it is polled at `PRESENCE_POLL_S` and opened the moment it returns (sub-second replug).
    A device present but unopenable keeps the doubling wait.
  - `cached_comports()` gives port enumeration a short shared TTL, so N polling reader threads do not each pay for a setupapi/sysfs scan.
    `/devices` shares it too, from a worker thread, because a setupapi scan is far too slow to run on the event loop.
  - `_EpisodeNotice`: five conditions that shed data report once per episode rather than once per occurrence.
  - A line that fails to store costs that line only: batching them into one comprehension once let a single malformed line discard the rest of the burst.
  - Counters carry across detach and reattach in `_carried`, keyed by alias, because the alias is the port slot.
- **`server.py`** - `create_app(config)` builds the FastAPI app.
  - The lifespan starts the store, opens the automatic session, attaches autoconnect ports and records daemon start/stop system rows.
  - Implements every SPEC 3.4 endpoint plus `/ws`; exceptions become an `{"error": msg}` envelope.
  - `/ws` frames are arrays of rows, and an empty one is the idle keepalive (`WS_KEEPALIVE_S`) that makes a vanished client surface as a failing write rather than a queue held until the next row.
- **`lockfile.py`** - the single-writer guard on a capture (SPEC 3.2): an OS lock (`fcntl.flock` / `msvcrt.locking`) on `<db_path>.lock`, taken by `mcuscoped` before anything opens the database.
  - A lock rather than a pid file, so a crashed daemon leaves nothing stranded.
  - The Windows half only runs in CI.
- **`daemon.py`** - the `mcuscoped` entry point.
  Startup order: load config, apply `--host/--port` overrides, take the capture lock, probe for a port conflict, record the pid, install the signal handler that releases that record, wire the `/shutdown` callback, `uvicorn.run`.
  - The port probe runs on both platforms and covers every resolved address: Windows needs `SO_EXCLUSIVEADDRUSE` to refuse the bind at all, and POSIX needs it early.
    uvicorn's own `EADDRINUSE` arrives *after* `pidfile.claim()`, so the failing daemon would take the running one's pid record with it.
- **`pidfile.py`** - the `<host>-<port>.pid` record `mcu daemon stop` uses to find and stop a daemon it did not start.
  - Advisory, not a lock (`lockfile.py` is the lock): a stale record is overwritten, and a live one is left alone whoever it names.
  - It may not defer to the port probe, which closes long before either daemon binds: two daemons on one port could otherwise trade the record and leave the survivor unrecorded.
- **`_stdio.py`** - repairs std streams for hostile launch environments (pythonw, some Windows launchers).
  - Replaces streams handed over as `None`, attaches a console where there is one, and widens the stdout encoding so a redirected stream cannot die on a character outside the console code page.
  - Wraps each console script so a crash lands in a file instead of vanishing.
    That crash log is the deliberate trace for a genuine bug, so it must not be replaced by a blanket handler upstream.
  - Its warnings go to stderr, so `mcu --json` stays parseable when a stream needed repairing.
- **`config.py`** - TOML config via `tomlkit` + platformdirs. A missing file is fine.
- **`pjstream.py`** - the PlotJuggler UDP fan-out (SPEC 3.7): one JSON datagram per decoded plot line, sent from `SerialPort`'s ingest path.
  - `send` is fire-and-forget on a non-blocking socket and swallows every `OSError`: it sits on the capture path, and a viewer must never cost a row or stall the loop.
  - `send` (loop) and `configure` (worker thread) share one attribute, an immutable `(socket, sockaddr)` pair swapped whole, so a torn read cannot pair a socket with the wrong address; a replaced socket is retired for one swap before it is closed, so an in-flight send cannot land on a reused fd.
  - `configure` resolves on enable/retarget (not per datagram) and commits no state until resolution succeeds, so a refused change leaves the old state whole. Concurrent `configure` calls are the caller's problem: the daemon serializes them on its config write lock.
- **`update_check.py`** - the release check (SPEC 3.6): one PyPI request a day at most, cached under `user_cache_dir` so restarts do not re-ask.
  Reported through `/status.update` to both the UI badge and `mcu status`.
  - No polling task: `maybe_check()` runs at startup and on every `/status`, and the cache decides whether that becomes a request.
    The rate limit thus lives in one place, not split between a timer and a cache.
  - Never raises into the loop, never blocks startup, never writes to the capture.
  - Off via `[update] check = false`; `MCUSCOPE_UPDATE_CHECK=0|1` overrides the config file either way.
    **conftest sets that env var**, so no test ever hits the network (a stubbed `httpx.MockTransport` covers the real path).
- **`cli.py`** - the `mcu` typer app: the commands, the `-f` follow loops, and `main()`/`_dispatch()`/`console_entry()`.
  Four single-reason modules sit beside it (next four bullets).
  - **Exit-code contract (SPEC 4): 0 success/match, 1 error or bad usage, 2 timeout, 3 daemon unreachable.**
    `mcu assert` is the documented exception: `1` means the assertion failed, and it never exits `2`.
  - Global options (`--json`, `--port/-p`, `--url`, `--token`) work in any position (`mcu i2c rd 48 2 --json`): `main()` rewrites argv through `cli_argv` before click parses anything.
  - Two typer traps.
    In non-standalone mode the `Exit` code comes back as the call's **return value**, not an exception, so `main()` must return it.
    And typer vendors its own click, so `typer.Abort` is not `click.exceptions.Abort`.
    Catch both (`ABORT_EXCEPTIONS` and friends), or control-flow exceptions escape to typer's rich handler and print a traceback at the user.
  - User patterns compile with `regex` here too, since a pattern the daemon accepts must not crash the client.
- **`cli_output.py`** - everything the CLI writes (human text, `--json` objects, stderr diagnostics) and the SPEC 4 exit discipline around writing it.
  - Holds `die()` and the module-level `--json` mode it reads (set once by the global callback, kept here so helpers with no `Settings` in hand report correctly).
  - Also `out_json`/`emit_stream`, the row/frame formatters and the confirmation prompt.
  - Closed-pipe silencing on both std streams: a stream that raises `BrokenPipeError` is pointed at devnull.
    An undeliverable message must not change the exit code.
    And bytes stranded in the buffer would make the interpreter's shutdown flush raise, ending the process with 120 over whatever the command returned.
- **`cli_client.py`** - `Settings`, the `Client` request wrapper, and the SPEC 4 map from transport failures to exit codes.
  The map (`_daemon_errors`) is stated once and every request policy (request, probe, download, stream_text) routes through it.
- **`cli_argv.py`** - global-option hoisting: argv is rewritten up front, because click only accepts group-level options ahead of the subcommand.
  - The targeted subcommand is resolved first to learn which of its options consume a following value.
    A token that is really an option's value is then never hoisted (`mcu lines --match -p ...` means the regex `-p`); when that resolution fails, nothing is hoisted at all.
  - Takes the typer app as an argument rather than importing it, which keeps it free of an import cycle with `cli.py`.
- **`cli_daemonctl.py`** - the machinery behind `mcu daemon start|stop|status`.
  Decides whether a daemon is running, keeps the pid record's client side (write, tidy, abandon a daemon that never came up), and stops a daemon however it was started.
  The commands themselves stay in `cli.py`; the daemon's own side of the pid record lives in `pidfile.py`.

## What the tests attach to

The port a test drives is a design decision with a coverage consequence, so it is written down rather than inferred.

- **Whole-stack tests** (`tests/support.py:Stack`, the `stack` fixture) attach `sim://board` and open a `link.SourceLink` whose far end is the simulator core, in process.
  No listener, no ephemeral serial port, no accept loop.
  `stop_sim`/`restart_sim` unplug and replug that link, which is deterministic where a socket teardown was not.
- **Reader-loop tests** use the same `SourceLink` with a `Scripted` source instead of the simulator, so the burst/drain/post cycle is driven byte by byte, including failures that land mid-drain.
  One Link, two sources: the read/drain contract has one implementation.
- **The CLI suite** spawns the installed `mcu` console script, not `python -m mcuscope.cli`: the prog name and `sys.path` differ between the two.
  Every Windows startup bug the project has had lived in that gap (class 15).
  An uninstalled checkout falls back.
- **`socket://` and the TCP listener** keep a deliberate set.
  `test_sim_tcp.py` covers the listener (one client at a time, close-on-exit, reconnect) with raw sockets.
  It adds one whole-stack run through pyserial so the URL handler and `SerialLink`'s socket-drain branch - both production paths for a remote port - are exercised for real.
  `test_sim_pty.py` covers the POSIX pty transport.
  Dead-`socket://` attaches in the e2e/CLI/security suites need no listener at all and stay as they are: they test the failure path.
- **`UNOPENABLE`** (a name that resolves to no device) stays the transport for tests about `PortManager` bookkeeping.
  - There no bytes are wanted and presence-gating should fail immediately on both platforms.
