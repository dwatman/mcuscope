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
    The ingest path splits each rx line once and uses the `*_tokens` entry points (`parse_can_event_tokens`, `feed_tokens`, `points_from_tokens`); the `raw` forms normalise, split and delegate to them.
    Plot points travel as `PlotPoint` tuples `(tick_ms, sid, name, value)`, in `plot_points` column order.
    A `!ps` sample names its stream by a sid carried inside the line, so a caller given only `decode_plot_sample(raw, definition)` would have to reimplement the grammar to find that sid before it could look the definition up.
- **`store.py`** - SQLite capture (WAL, FK cascade).
  - A **single async writer task** drains a queue and is the only writer.
    It allocates `lines.id` itself so a whole batch goes in with one `executemany`, and callers await a future to get the inserted row back.
  - The writer stays on the event loop deliberately, which keeps retention chunks and `incremental_vacuum` out of an open writer transaction.
    The cost is bounded by capping the rows one commit absorbs, and a commit past `_SLOW_COMMIT_S` warns.
  - A failed write increments `write_errors`, which `/status` reports, because a silent write failure was invisible on every surface.
    A failed commit leaves its ids spent (a gap), never rewound: ids above SQL's `MAX(id)` may already be in clients' hands after a purge of the newest rows.
    The row-by-row fallback resyncs `_next_id` from SQL, only ever upwards.
    `max_id()` answers from that sequence while the writer runs, so the resync must read `_max_id_sql`, never `max_id()`.
  - WebSocket subscribers are fed by fan-out with drop-oldest, one walk of the subscribers per committed batch.
    A `/ws` subscriber takes each row as its JSON text (`subscribe(as_json=True)`), serialised once per row for every such subscriber; the pump joins the texts into the frame.
    Row dicts are shared between the futures and every subscriber queue, and are read-only from then on.
  - `submit_line_nowait` is the ingest fast path (a plain `put_nowait`); `submit_line` is the awaiting form the callers fall back to on `QueueFull`.
  - `/plot/channels` is served from a per-(port, name) summary the writer maintains after each committed batch, not from a GROUP BY over `plot_points`.
    A delete subtracts its points from the summary; a start, a delete that takes a channel's newest sample while older ones remain, or a delete while a rebuild is in flight marks it dirty.
    The next read then rebuilds it from SQL off the loop (`_scan_plot_summary`), merging what the writer landed during the scan.
    The plain GROUP BY form lives only in the tests, as their oracle (`test_store_plot_summary.query_plot_channels`).
  - Schema: `lines`, `can_frames` and `sessions` (SPEC 3.5) plus `plot_points` (SPEC 9.2).
    Later columns arrive through `_MIGRATIONS`, since `CREATE TABLE IF NOT EXISTS` cannot alter an existing table.
  - The writer announces rows committing more than `WINDOW_TS_SLACK_S` behind the newest stored `ts` with a `sys` row per episode (at its start, and at its end with a count): past the slack, time windows can miss rows.
  - `stored_ports()` skips along `idx_lines_port_id` (one seek per port, the daemon's port `""` excluded); `/ports` `stored` and the text export's port-column rule read it on the loop, like `has_port_rows`.
  - Retention is age-based with a `min_sessions` floor, plus an opt-in size cap measured against live content rather than file size.
    - The hourly age sweep starts its walk at the last full sweep's cutoff while the floor has not risen (`_last_age_sweep`): otherwise every protected expired row is read and rejected each hour, on the loop.
      The writer drops that record when it commits a row stamped below it (a clock stepped back), and a sweep that saw it dropped mid-walk records nothing.
  - Thread work runs on private pools, so nothing that must answer promptly queues behind analytics or on the *default* executor.
    Every `asyncio.to_thread` shares the default one; config writes and streamed exports still use it.
    - `match_executor()` runs the history regexes (`/lines`, retrospective `/assert`), the `/can/frames` join, the row counts and every other `_offload` read: the heaviest reads the API serves.
    - Live `/wait` and `/assert` matching has its own pool in `server.py` (`_live_scan`), so a live match is not reported late behind a history scan.
    - `serial_link.py` joins reader threads on `_join_pool` (detach and shutdown wait on it) and runs device writes (`/send`, `/cmd`, `/break`) on `_write_pool`.
    - Session export and bundle builds run on their own bounded pool in `server.py`: 2 workers, 2 queued, 503 beyond (or a wait, with `wait=1`).
      An abandoned build is stopped by a progress handler on the copy's connection (`_ExportJob.on_open`), not `interrupt()`, which is lost between two statements; `checkpoint()` stops the bundle's later members.
      A bundle claims its slot (`_admit`) before it takes `_sweep_lock`, so retention never queues behind a bundle that is itself queued.
  - Each `match_executor` worker keeps one read connection per store (`_read_conn`, a `threading.local`), opened `check_same_thread=False` so `stop()` can close it from the loop; the regex budget is still re-armed per query.
  - **User patterns compile with the third-party `regex` module, never stdlib `re`.**
    - `re` holds the GIL for a whole backtrack: a 7-character pattern froze the process and the pool was decoration.
    - `regex` releases the GIL and honours `timeout=`, which `_make_regexp` turns into a per-call ceiling plus a per-query budget.
      Exceeding either raises `MatchBudgetExceeded` and the API answers 400, never a timeout result (which the CLI would report as exit 2).
    - `compile_user_regex` is the one compile, and callers on the loop run it in a thread.
      It refuses a pattern whose counted repeats expand past `MAX_REPEAT_EXPANSION` (`regex` expands them at compile time: 24 characters took a second, 45 exhausted memory), then compiles with `USER_REGEX_FLAGS`, the ASCII classes the web UI's JavaScript reads.
      `repeat_expansion` scans rather than parses, so verbose and V1 patterns (where a comment or a nested set can hide a paren) get a coarser upper bound instead.
    - Internal patterns stay on `re`.
- **`link.py`** - the transport itself: `Link`, `open_link()`, and the two real adapters.
  - `in_waiting` is a true byte count on a native port but a 0/1 readability poll on `socket://`, so the drain strategy differs by transport and `SerialLink` picks it once at open.
    (A sized read on a socket fetched one byte per syscall: 0.2 MB/s against 600.)
  - Holds the URL-scheme allowlist, and `cancel_read`/`cancel_write`, which the URL handlers do not implement and now say so with a bool rather than a suppressed AttributeError.
  - On Windows `SerialLink.send_break` sets and clears the break itself: pyserial discards the `SetCommBreak`/`ClearCommBreak` results, and on an unplugged USB adapter both fail, so its `send_break` reported a break that never left the host.
  - `SerialLink.write` raises on a short count: pyserial returns one, not an error, for a write cut off by `cancel_write` (which the reader sends on a disconnect) or by aborted Win32 I/O.
  - `SerialPort` accepts the opener, so `SourceLink` can drive the reader's success path in-process; before that, every reader test drove a device that could never open.
- **`serial_link.py`** - `SerialPort` (reader thread, reconnect backoff, seq/pending machinery) and `PortManager`.
  The transport lives in `link.py`; what stays here is the retry policy, the counters and the sys rows.
  - On command timeout the pending entry is popped, so a late response is **logged but not delivered** (SPEC 3.2).
    A response landing in the same loop cycle as the deadline is delivered on 3.10 and reported as a timeout on 3.11+ (asyncio.wait_for changed the tie), so a boundary test must accept either outcome, as test_e2e's timeout_ms=1 case does.
  - Reconnect is automatic and its backoff presence-gated (`_retry_wait`).
    - An absent device node is cheap to test for, so it is polled at `PRESENCE_POLL_S` and opened the moment it returns (sub-second replug).
    A device present but unopenable keeps the doubling wait.
  - `cached_comports()` gives port enumeration a short shared TTL, so N polling reader threads do not each pay for a setupapi/sysfs scan.
    `/devices` shares it too, from a worker thread, because a setupapi scan is far too slow to run on the event loop.
  - `_EpisodeNotice`: five conditions that shed data report once per episode rather than once per occurrence.
  - A line that fails to store costs that line only: batching them into one comprehension once let a single malformed line discard the rest of the burst.
  - Counters carry across detach and reattach in `_carried`, keyed by alias, because the alias is the port slot.
- **`server.py`** - `create_app(config)` builds the FastAPI app.
  - The lifespan starts the store, opens the automatic session (or resumes a named one left open by the previous run), attaches autoconnect ports and records daemon start/stop system rows.
  - Implements every SPEC 3.4 endpoint plus `/ws`; exceptions become an `{"error": msg}` envelope.
  - `_VersionHeader`, the outermost middleware, stamps `X-Mcuscope-Version` on every response and WebSocket accept; an unhandled error's 500 is sent outside all middleware, so `_unhandled_error` adds that header and the framing denial itself.
  - Every int, float and bool query or path parameter is a `Url*` type, whose grammar runs on the raw text before pydantic's lax parse; a new one takes one too, as `Annotated[UrlUInt, Query(...)]` (FastAPI drops the validator from `x: UrlUInt = Query(...)`), which a route-walk test enforces.
  - `/ws` frames are arrays of rows, and an empty one is the idle keepalive (`WS_KEEPALIVE_S`) that makes a vanished client surface as a failing write rather than a queue held until the next row.
- **`lockfile.py`** - the single-writer guard on a capture (SPEC 3.2): an OS lock (`fcntl.flock` / `msvcrt.locking`) on `<db_path>.lock`, taken by `mcuscoped` before anything opens the database.
  - A lock rather than a pid file, so a crashed daemon leaves nothing stranded.
  - The Windows half only runs in CI.
- **`daemon.py`** - the `mcuscoped` entry point.
  Startup order: load config, apply `--host/--port` overrides, take the capture lock, probe for a port conflict, record the pid, key the startup and crash logs, install the signal handlers, hold the console close (Windows), wire the `/shutdown` callback, `uvicorn.run`.
  The logs are keyed by record ownership: `host-port`, plus our pid when another live process holds the record.
  SIGTERM (and SIGBREAK on Windows) releases the record; SIGHUP is raised on as SIGTERM.
  Closing the console window on Windows arrives as SIGINT via the ctrl handler.
  - The port probe runs on both platforms and covers every resolved address: Windows needs `SO_EXCLUSIVEADDRUSE` to refuse the bind at all, and POSIX needs it early.
    uvicorn's own `EADDRINUSE` arrives *after* `pidfile.claim()`, so the failing daemon would take the running one's pid record with it.
- **`pidfile.py`** - the `<host>-<port>.pid` record `mcu daemon stop` uses to find and stop a daemon it did not start.
  - Advisory, not a lock (`lockfile.py` is the lock): a stale record is overwritten, and a live one is left alone whoever it names (one exception: `daemon start` rewrites it once its answer carries its start id, `cli_daemonctl.py`).
  - Every "remove only while it names X" goes through `remove_record_if`: the record is renamed aside, read there, and put back if it names someone else, since a read then a remove deletes a record written between them.
    The put-back never replaces a newer record (a rename on Windows, a hard link on POSIX), also runs when a Ctrl-C lands mid-way, and when it fails keeps the copy aside with a `could not put back` note.
  - It may not defer to the port probe, which closes long before either daemon binds: two daemons on one port could otherwise trade the record and leave the survivor unrecorded.
- **`_stdio.py`** - repairs std streams for hostile launch environments (pythonw, some Windows launchers).
  - Replaces streams handed over as `None`, attaches a console where there is one, and widens the stdout encoding so a redirected stream cannot die on a character outside the console code page.
  - Wraps each console script so a crash lands in a file instead of vanishing.
    That crash log is the deliberate trace for a genuine bug, so it must not be replaced by a blanket handler upstream.
  - Its warnings go to stderr, so `mcu --json` stays parseable when a stream needed repairing.
  - `_note` (stderr) and `_say` (stdout) are how `mcuscoped` and `mcu-sim` print: a closed stream drops the message and never owns the exit code (class 35).
- **`config.py`** - TOML config via `tomlkit` + platformdirs. A missing file is fine.
  - A relative `storage.db_path` resolves against the file's directory (`Config.base_dir`, set by `read_config`), so `resolve_db_path` is always absolute and a restart from elsewhere finds the same capture.
  - `save_ports` updates each `[[ports]]` table in place by alias, so keys this version does not model survive a settings save.
  - The storage bounds are one set of constants the loader and `PUT /config/storage` share: a value the file holds can always be sent back.
- **`dirs.py`** - the one resolver for the data, config and cache dirs: `MCUSCOPE_*_DIR` if set, else platformdirs. Stdlib-only at import and platformdirs lazy, so `_stdio`'s crash path can use it without risking an exception.
- **`pjstream.py`** - the PlotJuggler UDP fan-out (SPEC 3.7): one JSON datagram per decoded plot line, sent from `SerialPort`'s ingest path.
  - `send` is fire-and-forget on a non-blocking socket and swallows every `OSError`: it sits on the capture path, and a viewer must never cost a row or stall the loop.
  - `send` (loop) and `configure` (worker thread) share one attribute, an immutable `(socket, sockaddr)` pair swapped whole, so a torn read cannot pair a socket with the wrong address; a replaced socket is retired for one swap before it is closed, so an in-flight send cannot land on a reused fd.
  - `configure` resolves on enable/retarget (not per datagram) and commits no state until resolution succeeds, so a refused change leaves the old state whole. Concurrent `configure` calls are the caller's problem: the daemon serializes them on its config write lock.
  - `status()` is the one shape every surface reports, with `target` (where datagrams go, read from that same pair) beside `dest` (what was asked for).
- **`update_check.py`** - the release check (SPEC 3.6): one PyPI request a day at most, cached under `user_cache_dir` so restarts do not re-ask.
  Reported through `/status.update` to both the UI badge and `mcu status`.
  - No polling task: `maybe_check()` runs at startup and on every `/status`, and the cache decides whether that becomes a request.
    The rate limit thus lives in one place, not split between a timer and a cache.
  - Never raises into the loop, never blocks startup, never writes to the capture.
    The HTTP client is built off the loop: its constructor loads the CA bundle.
  - Off via `[update] check = false`; `MCUSCOPE_UPDATE_CHECK=0|1` overrides the config file either way.
    **conftest sets that env var**, so no test ever hits the network (a stubbed `httpx.MockTransport` covers the real path).
- **`cli.py`** - the `mcu` typer app: the commands, the `-f` follow loops, and `main()`/`_dispatch()`/`console_entry()`.
  Four single-reason modules sit beside it (next four bullets).
  - **Exit-code contract (SPEC 4): 0 success/match, 1 error or bad usage, 2 a timeout the daemon reported, 3 daemon unreachable.**
    A daemon that accepted a request and stopped answering is `1`, not `2`: `2` on `cmd` means the board did not answer.
    `mcu assert` is the documented exception: `1` means the assertion failed (or checked no lines), and it never exits `2`.
  - Global options (`--json`, `--port/-p`, `--url`, `--token`) work in any position (`mcu i2c rd 48 2 --json`): `main()` rewrites argv through `cli_argv` before click parses anything.
  - Two typer traps.
    In non-standalone mode the `Exit` code comes back as the call's **return value**, not an exception, so `main()` must return it.
    And typer vendors its own click, so `typer.Abort` is not `click.exceptions.Abort`.
    Catch both (`ABORT_EXCEPTIONS` and friends), or control-flow exceptions escape to typer's rich handler and print a traceback at the user.
  - User patterns compile with `regex` here too, since a pattern the daemon accepts must not crash the client; the `tail -f` compile carries the daemon's ASCII flags and 200-character cap, duplicated rather than imported.
  - Numbers on the command line are ASCII decimal: the root group (`cli_output.AsciiNumbersGroup`) swaps every click INT/FLOAT type in the built tree for one with `protocol.int_arg`'s grammar.
    A `typer.Option(min=, max=)` keeps its range, and an option added later cannot miss the grammar.
- **`cli_output.py`** - everything the CLI writes (human text, `--json` objects, stderr diagnostics) and the SPEC 4 exit discipline around writing it.
  - Holds `die()` and the module-level `--json` mode it reads (set once by the global callback, kept here so helpers with no `Settings` in hand report correctly).
  - Also `out_json`/`emit_stream`, the row/frame formatters and the confirmation prompt.
  - Closed-pipe silencing on both std streams: a stream that raises `BrokenPipeError` is pointed at devnull.
    An undeliverable message must not change the exit code.
    And bytes stranded in the buffer would make the interpreter's shutdown flush raise, ending the process with 120 over whatever the command returned.
- **`cli_client.py`** - `Settings`, the `Client` request wrapper, and the SPEC 4 map from transport failures to exit codes.
  The map (`_daemon_errors`) is stated once and `request`, `download` and `stream_text` route through it.
  `probe` does not: for the `mcu daemon` commands any transport failure means "not running".
  - `open()` installs a response hook refusing a daemon older than the CLI (`X-Mcuscope-Version`), so a dropped parameter can never pass silently; the follow checks its handshake the same way.
    `probe` clears the hook: `mcu daemon stop` must reach the older daemon an upgrade replaces.
    Tests that replace `open` wholesale skip the check; `tests/support.py`'s stubs keep the real `open` and send the header.
- **`cli_argv.py`** - global-option hoisting: argv is rewritten up front, because click only accepts group-level options ahead of the subcommand.
  - The targeted subcommand is resolved first to learn which of its options consume a following value.
    A token that is really an option's value is then never hoisted (`mcu lines --match -p ...` means the regex `-p`); when that resolution fails, nothing is hoisted at all.
  - Takes the typer app as an argument rather than importing it, which keeps it free of an import cycle with `cli.py`.
- **`cli_daemonctl.py`** - the machinery behind `mcu daemon start|stop|status`.
  Decides whether a daemon is running, keeps the pid record's client side (write, tidy, abandon a daemon that never came up), and stops a daemon however it was started.
  A start that loses the race for its URL stops its own child at once (`_reap_losing_start`): that child is still inside the capture lock's 2 s retry, and left alone it took the port as soon as the winner stopped.
  - Ours or lost is decided on the random id `start` hands its child (`MCUSCOPED_START_ID`), which the daemon echoes as `X-Mcuscope-Start-Id` on every response, guard refusals included. Pids cannot decide it: a launcher chain (venv shim, uv trampoline) puts unknown processes between the child and the daemon.
  - `daemon stop` still matches pids (`pid`, and on Windows `ppid`), because it must know which local process to signal.
  - A start answered with its id rewrites the pid record to name its own child (`_record_own_daemon`): a racing loser's record can land last, and its reap would leave the winner unrecorded.
  - `_stop_child` is the one terminate, grace (`DAEMON_STOP_GRACE_S`), kill sequence, shared with `_abandon_daemon`. On Windows terminating the launcher ends the interpreter through the launcher's kill-on-close job.
  The commands themselves stay in `cli.py`; the daemon's own side of the pid record lives in `pidfile.py`.
  `daemon start` keys its readiness wait on the store's `building index`/`built index` notices in the daemon's stderr file, matched as whole log lines and capped at 600 s, duplicated as literals (a test holds them equal) because importing `store.py` is heavy.

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
  `test_break.py` spawns the listener once, for the no-op break on a socket port.
  Dead-`socket://` attaches in the e2e/CLI/security suites need no listener at all and stay as they are: they test the failure path.
- **`UNOPENABLE`** (a name that resolves to no device) stays the transport for tests about `PortManager` bookkeeping.
  - There no bytes are wanted and presence-gating should fail immediately on both platforms.
