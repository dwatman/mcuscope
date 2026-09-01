# Changelog

All notable changes to MCUscope are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the major version is 0, the interfaces in `docs/SPEC.md` (wire protocol, REST API, CLI exit codes) may still change between minor releases.

## [Unreleased]

### Changed

- A named session survives a daemon restart: shutdown closes only the automatic session, and a daemon starting with a named one open resumes it. A restart mid-run used to close it silently and file the rest under an auto session.
- Flash and reset are dropped from the P2 backlog: the agent drives the vendor tools directly.
- Default daemon port is now **8558** (was 8765, which AnkiConnect and other tools also default to). A saved `server.port` in config.toml keeps its value; only the default moves. Clients follow `MCUSCOPE_URL` or `--url` as before.

### Added

- `mcu lines`, `mcu tail` and `mcu log export` page past the `/lines` 1000-row cap, so any `--limit` is honoured; `log export` writes every matching row by default. Raising `--limit` used to change nothing above 1000.
- `--decode` on `lines`, `tail` and `log export` renders plot samples as named fields from the stream's `!pd` (`s0 state=CHARGING vbat=25.54V io=relay|bat`); `--changes` prints a sample only when a field changed; `--names` picks the fields.
- `--from HH:MM:SS` / `--to HH:MM:SS` wall-clock bounds on `lines` and `log export` (`YYYY-MM-DDTHH:MM:SS` for another day; `--from` after `--to` is refused).
- `log export` without `--limit` streams the window page by page instead of holding it in memory.
- Per-port `identify = false` in config.toml skips the connect-time ping for firmware that is not a monitor.
- `mcu status` shows when the running session started.
- `--retry-ms` on `cmd` and `can tx` retries `ERR 6 busy` until the deadline.
- `mcu plot channels` shows the age of each channel's last sample; `--active S` hides channels not seen in S seconds.
- Port status carries `write_failures`, `last_write_error`, `last_write_error_ts` and `target`; `mcu status` shows a port whose writes fail as `DEGRADED` with the streak, and a failed `mcu cmd` names it.
- The daemon pings once on every connect and reports the monitor's name as the port's `target` (an ST-LINK moved between boards keeps its alias).
- Firmware: `monitor_plot()` emits `!e plot <sid> badarg def|body|len|full` once when it rejects a stream (`!e plot ? badarg sid` for a bad sid or NULL body), which used to be invisible; channel and bit-lane names share one namespace per stream (documented).
- `mcu ai-guide` states that `cmd` and `--send` take the monitor grammar (`can tx ID DATA x`), not the `mcu` sugar.
- `POST /ports/{alias}/disconnect` closes a port and stops retrying while keeping the attachment; `reconnect` resumes it. The web UI's port dot is the switch (green: disconnect, red: reconnect). Held state is in memory only.
- Port status carries `resolved_device` (a by-id path resolved to its `/dev/ttyACM*`) and `description`; the port chip and `mcu status` show them.
- Attach dialog "Bind to this device" box: attach by the stable by-id path instead of the port name. Unticked, the port name is attached as picked (it used to be swapped for the by-id path silently).
- Digital cursor shows the time under it.

### Fixed

- Digital lanes froze at the first sample when every field held a constant value: the window's right edge followed the newest transition rather than the newest sample.
- Port chips no longer show the full by-id path, which wrapped the header buttons onto a second line.

## [0.3.0] - 2026-08-31

### Added

- Multi-bus CAN (SPEC 2.4): a digit on the family token selects the controller, `can2 tx ...`, `can2 filter ...`, `can2 stat`, with frames from bus 2 to 9 arriving as `!can2` to `!can9`.
  - `mcu can tx/dump/stat/filter` take `--bus N`; `/can/frames` rows carry a `bus` column (old rows read as bus 1 by migration).
  - The web UI CAN table groups by port and bus with collapsible dividers; the simulator exposes a second bus (`info` answers `can=2`) so the feature is testable with no hardware.
  - The firmware monitor's `mon_can_*` shims gain a bus field; a single-bus port needs no change.
- PlotJuggler streaming (SPEC 3.7): decoded plot points mirror to PlotJuggler's stock UDP Server source as one JSON datagram per line, fire-and-forget from the ingest path so a viewer can never cost a capture row.
  - `mcuscoped --plotjuggler [host:port]` (or `--pj`), a `[plotjuggler]` config section, live toggle via `mcu plotjuggler on|off` / `mcu pj` and `PUT /plotjuggler`, and a settings section in the web UI.
  - Destination grammar is strict: ASCII-digit ports, `[addr]:port` for IPv6, multicast/unspecified/broadcast refused; non-finite samples are dropped rather than emitted as JSON PlotJuggler rejects.
- `mcu status` reports an available release.
  - The check (SPEC 3.6) previously reached only the web UI badge, so nobody driving the CLI - an agent, or any headless bench - ever learned a newer version existed.
- `GET /sessions?name=` filters by name; `mcu` resolves session names through it.
- A cross-language plot-grammar fixture (68 cases) drives `protocol.py` and `plots.js` from one case list, and a CSV-cell fixture does the same for the daemon's export and the web UI's.

### Security

- `POST /ports` is held to the config-write bar (token required off loopback), like `PUT /config/*` and `PUT /plotjuggler`.
  - A device string can name a network destination (`socket://`, `rfc2217://`), so on a tokenless non-loopback bind a network client could point a port at a host of its choosing. Detach and reconnect stay open; loopback clients are unaffected.

### Changed

- Python 3.10 is now supported (the floor was 3.11).
  - Config reading moved from stdlib `tomllib` to `tomlkit`, which the write-back path already used; `LineClass` no longer needs `enum.StrEnum`.
  - websockets 17 requires 3.11, so the 3.10 CI leg runs websockets 16; both majors are supported.
- Dependency floors now name the oldest versions that work: typer 0.26 (`mcu` failed to import below it), uvicorn 0.35 (WS backpressure shedding was silently off below it), fastapi 0.115.7; dev: pytest-asyncio 0.23.5, starlette 0.44.
- The release check is driven by demand rather than by a polling task: one check at daemon startup, and one per `GET /status` when a check is due.
  - The daily cache was always the real rate limit, so the timer decided nothing the cache did not.
- Dismissing the web UI's update badge now hides that version only; a newer release shows it again.
  - It replaces a day/week/month/permanent snooze ladder whose stored rung index needed guarding against corruption.
- `MCUSCOPE_UPDATE_CHECK=0|1` overrides `[update] check` in both directions, as SPEC 3.6 said; the code had ANDed them, so a config `false` could not be lifted.
- The daemon's attach-time `!pd` scan uses the same 20000-id lookback as the web UI (SPEC 2.5 names the shared bound).
- Duplicate channel or lane names in one `!p`/`!pd` line are malformed on the daemon and in the browser alike (SPEC 2.5); two writers for one name misaligned chart arrays.
- SPEC 2.4 pins `can stat`: counters cumulative since init, state current rather than latched.
- `cli.py` split into `cli_output`, `cli_client`, `cli_argv` and `cli_daemonctl`; the `mcu` entry point and exit-code contract are unchanged.
- Firmware C sources indent with tabs.

### Fixed

- A stalled WebSocket client buffered the whole capture in the daemon instead of being shed: uvicorn's websockets-sansio protocol gates send on a writable Event that nothing cleared. The two callbacks are wired to it; shed engages in seconds and per-connection memory is bounded.
- `mcu tail -f` subscribes before its snapshot, so the overlap is staged and deduplicated instead of lost; the web UI pages its reconnect backfill past the 1000-row clamp and draws a divider for what it left behind.
- `GET /lines?since_ts=` planned as a full reverse scan on the event-loop connection; it takes the id-anchor treatment its `last_ms` sibling had.
- Firmware monitor: three ASan-confirmed overreads closed (`emit_ok`, `cmd_info`, `drain_can`); `emit_err` clamps codes to the wire table; `emit_can_event` masks the id to the flag width; `monitor_mark` refuses a tick-sigil forgery and returns `int`.
- Daemon and store: unbounded integer query/body params are 422s instead of 500s; `/purge` refuses a future `before_ts`; the session-export temp copy lives beside the capture and is removed on disconnect; `write_errors` counts the fast-fail path; `active_session` runs on a partial index; `delete_range` joins the sweep lock and sessions serialize under a store lock.
- Serial link: the detach handle-close joins the pool; a shutdown-window attach is a 400; a `serial_number` attach reports the device it opened; a `/cmd` cancelled mid-write no longer leaks its pending entry.
- Config and startup: wrong-typed boolean keys refuse the load (SPEC 3.3); a port whose baud the API would refuse is skipped; a corrupt capture-lock record no longer crashes the refusal that names the holder; `--host ""` is refused; startup refusals go to stderr.
- CLI: `daemon start` no longer clobbers a live daemon's pid record or reports success with a dead child; a closed stderr no longer turns errors into exit 0; every `--json` error path emits one JSON object even with stdout a closed pipe; `purge --before-days` refuses values below 1 (a negative silently meant `--all`); non-ASCII tokens, WS binary frames and unbounded `--timeout` map to exit codes instead of tracebacks.
- Simulator and protocol: non-finite `f4` samples and post-scale overflows decode to generic events instead of raising out of the store; the sim sheds a slow reader instead of dropping it; the host tokenizer matches `monitor.c` byte for byte; `can.js` CSV quoting matches the daemon's export (leading tab/CR formula guard).
- Web UI: seeded `/plot/series` duplicates no longer misalign a chart's y array; the paused analog chart snapshots at freeze like the digital panel; a lane named `toString` no longer draws in the previous lane's colour; the hidden digital panel keeps its repaint request; `/status` polls coalesce; a capture reset re-seeds the way the first connect does.
- PlotJuggler streamer state is one immutable (socket, address) pair swapped whole, so a concurrent reconfigure cannot raise into the ingest path or pair a reported destination with another request's address.

## [0.2.0] - 2026-08-09

### Added

- `GET /status` reports `write_errors`, and the web UI port chip flags it. A capture write that failed was invisible on every surface: lines counted as received, nothing stored, everything green.
- `GET /status` reports `writer_alive`; `mcu status` and the web UI announce a stopped capture writer loudly. A dead or wedged writer previously read healthy everywhere and made shutdown hang forever.

### Removed

- The pre-release unkeyed `mcuscoped.pid` fallback in `mcu daemon stop`. Every released version writes the host-port-keyed record, so the fallback could only match a pre-0.1.0 development install.

### Changed

- With `--json`, a destructive command refuses to prompt on a non-interactive stdin instead of blocking on it. `echo y | mcu --json purge --all` now fails; pass `-y`.
- Web UI CPU use under load cut sharply.
  - The terminal appends new rows instead of rebuilding its window every frame, and digital readouts and the cursor batch their DOM writes.
  - Tables and chips repaint only on change, and timers idle when the tab is hidden.
- The simulator enforces the firmware monitor's limits, so behaviour certified against `--sim` matches a real board.
  - 12 tokens per command, 255-byte lines, oversized responses answered `ERR 8 overflow`.
- `mcu attach` reports "(connecting; see 'mcu status')" instead of implying the link is already live; `mcuscoped --port` is validated 1..65535 up front.
- `mcuscoped` always writes a pid file and a `mcuscoped-startup.log` (URL, pid, interpreter report, stop instructions) in the data directory, so `mcu daemon stop` works however the daemon was started.
  - Previously only `mcu daemon start` wrote the pid record, and a daemon launched as `mcuscoped` was invisible to it.
- `--version` flags the windowless-interpreter case explicitly (`[windowless: no console - output and Ctrl-C unavailable]`).
- Install docs: on Windows, pin a real interpreter with `uv tool install mcuscope --python 3.12` when PATH is led by a vendored runtime (KiCad, GIMP, Blender).
- `POST /shutdown` (loopback only): a graceful stop over REST, now the primary channel of `mcu daemon stop`.
  - `GET /status` reports the daemon's `pid`, so a fallback kill targets the serving process rather than a Windows launcher shim.
- Update notice: the daemon asks PyPI once a day (cached across restarts) whether a newer MCUscope exists, and the web UI shows a badge naming it.
  - Dismissing snoozes on a ladder (a day, a week, a month, then permanently for that version) rather than silencing it for good on the first click.
  - Off with `[update] check = false`, the Settings dialog, or `MCUSCOPE_UPDATE_CHECK=0`.

### Fixed

- On Windows, piping `mcu` into a closed reader (`mcu tail -f | head`) produced a crash log and a traceback instead of the clean exit 0 POSIX gets.
  - Windows reports a closed pipe as `OSError(EINVAL)`, which no handler recognised.
- `mcu can dump -f` went silent forever after `mcu purge --all` or a database recreate; it now notices the capture change and re-seeds.
- A rejected WebSocket token exited 3 ("daemon unreachable") where the same failure over REST exits 1.
- Detaching a port erased the drop count of lines lost in that same detach; the counters now survive reattach.
- `mcu plot export -o` left an empty file behind when the daemon refused the request.
- A `/cmd` cancelled mid-write (client disconnect) leaked its pending-response entry until the next disconnect.
- A slow startup and a token-guarded daemon's 401 both read as "no answer" inside the 2 s status timeout.
  - A pid record is now removed only when its pid is dead, and stop falls back to `POST /shutdown` when there is no record to read at all.
- `mcu daemon start` deleted a pid record naming a different daemon, and `mcuscoped` took over a live one. Two daemons on one port could trade the record and leave the survivor unrecorded.
- One malformed line discarded the rest of its receive batch, up to 1000 lines, counted nowhere.
  - A number above CPython's 4300-digit limit raised past the protocol error handlers; six parsers now gate token length, and an oversized terminated line is dropped and counted like an unterminated one.
- The simulator's listener could outlive its serving thread after a transient accept error, so the daemon reconnected to a socket nothing was reading and reported the port connected.
- `GET /sessions` counted each session's lines with an open-ended scan: 2.06 s at 1M lines, 19.2 s at 500 sessions, on the event loop. Now 88 ms and 67 ms.
- `POST /purge --dry-run` and `POST /assert` counted rows on the event loop, the latter undoing the containment of the regex work beside it.
- `GET /can/frames` with `port=` or `last_ms=` drove its join from the line table and sorted every matching frame before applying the limit: 131 ms against 0.4 ms at 1M lines.
  - `GET /plot/channels?port=` scanned the line table a second time to build an id list, 190 ms against 138 ms.
- `POST /cmd` answered 500, with a full traceback in the daemon log, for an empty or whitespace-only command instead of 400. `POST /wait` and `POST /assert` shared the path.
- A session reference of more than 4300 digits answered 500 with a traceback, on `GET /sessions/{ref}/export` and on every endpoint taking `session=`.
  - The id branch of the lookup reached `int()` past CPython's conversion limit.
  - A session *named* with another script's digit also resolved to the id that digit converts to, returning a different session's lines.
- The CAN RTR length digit was accepted in any script, on both the receive and the `can tx` path, so `!can 1 r 100 ٣` decoded into a stored CAN frame instead of being kept as a generic event.
  - The simulator accepted the same tokens for its command arguments.
- Web UI: a typed-stream definition carrying an out-of-range enum value built a chart in the browser that the daemon had rejected outright.
  - The panel showed a stream `mcu plot` and `/plot/series` had never decoded.
- Web UI: the CAN sidebar showed frames whose id is out of range for their own flags, which the daemon drops from `can_frames`.
  - The table disagreed with `GET /can/frames` and `mcu can` about the same line.
- Web UI: a freshly loaded page could not decode the typed `!ps` samples in its own backfill, so the typed and digital charts came up empty while the ad-hoc chart was full.
  - A sample is undecodable until its `!pd` definition has been seen, and the definitions rebroadcast less often than the backfill window is wide, so whether the charts drew anything was luck.
  - The definitions are now fetched and applied before the backfill replays.
- The store writer commits at most 1000 rows at a time, cutting worst-case event-loop occupancy from 92 ms to 8-11 ms, and warns on a commit over 100 ms.
- `journal_mode=WAL` reports refusal in its result set rather than raising, so a capture silently running in rollback-journal mode now warns.
- `mcu tail -f --match` compiled the pattern with stdlib `re` while the daemon uses `regex`, so a pattern the daemon accepted printed one matching line and then crashed the client.
  - The client now also carries the daemon's per-match timeout: without it a catastrophic pattern hung the follow with no error and no working Ctrl-C.
- `mcu-sim --pty` retried a dead pty master ten times a second forever instead of exiting.
- A malformed `--url`, an unsupported scheme, a non-numeric port and a null `uptime_s` from a stray responder produced tracebacks instead of exit codes.
- `--json` emitted prose for `ai-guide` and `--version`, nothing at all for a usage error, and a bare newline for a `log export` that matched nothing.
- Web UI: a terminal rebuild left rows in the pane queue that it had already folded into the view, so a backfill landing mid-stream duplicated lines.
- `mcuscoped` output could die with `UnicodeEncodeError` when redirected to a file on a Windows console code page, losing the startup diagnostic it was trying to print.
- `mcuscoped`'s port probe checked only the first resolved address, so a conflict on any other address of a multi-homed host slipped past it.
- `mcu session export` left a truncated file at the destination when the transfer failed.
- `db_max_bytes` in `GET /status` reported the configured size cap rather than the one in force.
- `mcu devices` on Linux listed 32 phantom `/dev/ttyS*` ports, burying the one real adapter.
  - A port is now hidden only when the kernel itself reports `PORT_UNKNOWN` for it.
    - A real on-chip UART (a Raspberry Pi mini-UART, an ARM SoC's `ttyS1`, a `ttyAMA0`) is still listed, and a USB adapter is never judged at all.
  - pyserial means to hide these already, but its check went stale when Linux 6.7 moved the devices onto the `serial-base` bus.
- Web UI: a failed backfill froze the whole stream.
  - The error path referenced an unimported name, so it raised, and the staging area it should have drained was never released.
    - Every later row was queued into it instead of rendered, while the stream pill stayed green and the rate readout kept counting.
- A second `mcuscoped` on a port already in use deleted the running daemon's pid record on its way out, leaving the first daemon running but unstoppable by `mcu daemon stop`.
  - The port probe now runs on Linux too, before anything is claimed; it was Windows-only, and POSIX only learns of the collision from inside uvicorn, after the pid record is taken.
- `mcu --json` could emit a stream-repair warning on stdout, ahead of the JSON object, breaking any parsing consumer.
  - It goes to stderr now, and no longer claims to have "reattached to the console" on Linux, where it never does.
- Web UI: a paused terminal pane retained every matching row just to count it.
  - A backgrounded tab throttles the flush to about once a minute, so a fast capture held tens of thousands of rows, past the point the shared buffer had evicted them.
- Config integers were read with bare `int()`, the other half of the `bool()` defect below.
  - `port = true` became port **1** (a bool is an int in Python), `port = 8558.7` truncated in silence.
  - A typo'd `port = 99999999` was taken as written and failed much later from inside the bind, naming neither the file nor the key.
  - A wrong type now fails the load with a message naming the key, and an out-of-range value warns and keeps the default.
- A `[[ports]]` entry's `autoconnect = "false"` was read as **true**, so the port opened itself on every start - the exact opposite of the setting - and `baud = true` became **1 baud**.
  - Both are now refused with a warning that names the port, and one bad entry no longer affects its neighbours.
- Web UI: every numeric settings field was read with `parseInt`, which takes the leading digits and stops, so `1e9` in the port box passed the 1-65535 check and saved port **1**.
  - A field that is not a whole number is now rejected outright.
- `[update] check` and `[storage] auto_session` were read with `bool()`, so a hand-edited `check = "false"` enabled the update check and `check = 0` disabled it.
  - A non-boolean is now refused with a warning and the default kept.
- The update check re-asked PyPI on every restart when upstream had only pre-releases: the cache it wrote for that case was rejected by its own loader, voiding the once-a-day guarantee.
- A reader thread that outlived detach could raise `RuntimeError: Event loop is closed` and leak the device handle it had just opened; a `socket://` open blocks longer than the shutdown join allows.
- Line counters carried across a detach and re-attach covered `lines_rx` and `rx_dropped` but not `lines_tx`, so `mcu port reconnect` reset the transmit count to zero.
- Port enumeration was cached for less time than the reconnect poll interval, so the cache never hit and every poll paid for a full scan.
- A store shutdown that cancelled its writer left queued writes with futures nobody resolved; the awaiting task hung until the loop closed.
- Plot channel export ran on the default thread pool, where it could queue ahead of the reader-thread joins that detach and shutdown depend on.
- `mcu daemon stop` waited out its full grace period and then failed after a shutdown that had worked, when the daemon was left unreaped as a zombie by the script that spawned it.
- Web UI: cancelling a colour picker leaked a focusable hidden input into the page, one per cancel.
- The simulator died permanently on `can tx 7FF` (and `can tx 1FFFFFFF x`).
  - The echo frame is id+1, which at the top of the range is out of range, so formatting it raised from inside the event pump and unwound the serving thread.
    - The listening socket stayed open, so the daemon reconnected into a backlog nobody was accepting from and reported a healthy port that never produced another byte.
  - The echo id now wraps within its own range, and a client session can no longer take the listener down with it.
- `mcu -p board lines --match -p ...`: a global option before the subcommand stopped argv hoisting from resolving that subcommand, which disabled the guard protecting subcommand option values.
  - `--port` could silently become the next option (`--port=--limit`), or the command failed with a confusing "unexpected extra argument".
- `mcu wait --send ...` could report a timeout without examining a single captured line.
  - The send is given the same timeout as the whole wait, so a slow command consumed the window and the loop exited before draining a queue that may already hold the match.
  - Exit 2 on a run that actually matched.
- A cancelled `/cmd` (client disconnect, Ctrl-C) leaked its pending-sequence entry, because `CancelledError` is a `BaseException` and escaped the cleanup that `TimeoutError` triggered.
- Events are dispatched on their whole first token rather than a prefix, so a future `!candy`/`!power` line is no longer forced through the CAN or plot decoder and logged as a bogus decode failure.
- Sequence numbers are parsed strictly (ASCII decimal only): bare `int()` accepted `+17`, `1_7` and non-ASCII digits, so a garbled response could resolve the pending command for seq 17.
  - The plot, enum and marker-tick grammars likewise use `[0-9]` rather than `\d`.
- SPEC 2.4: the simulator refused to reject `can filter <id> <mask> r`, answering `OK` to a filter it could not honour.
- A startup failure between opening the store and serving left the writer task, the retention task and the SQLite connection running with nothing to stop them.
- `PortManager` kept one carried-counter entry per alias ever attached, with nothing to prune it.
- Web UI: a large `*<scale>` factor could carry a finite sample to `Infinity`, and uPlot's auto-range then returned `[NaN, NaN]`, silently erasing every series on that chart.
- Web UI: the shared tick anchor was set from an unbounded value, so one corrupt line could shift every terminal timestamp and chart x-axis for the rest of the session.
- Web UI: two overlapping stream reconnects could drop the rows captured across the gap entirely, because the staging buffer was a single global shared by every socket. Staging is now per-connection.
- Web UI: after a capture-database reset the terminal stayed empty until new traffic arrived, and a failed backfill was completely silent while the UI still looked live.
- Web UI: the colour picker never opened in Firefox, which cannot drive a detached `<input type=color>`.
- Windows: saving settings from the web UI rewrote the whole `config.toml` with CRLF endings, the one text write in the package that did not pin `newline=`.
- Windows: a serial port could be closed while a write was still in flight in the driver.
  - The reader thread's handle was left held if its join timed out, blocking a re-attach of the same COM port (which Windows opens exclusively).
- Windows: `.js` and `.css` content types are pinned rather than read from the registry.
  - A stale `HKEY_CLASSES_ROOT` entry would make the browser refuse `app.js` as a module script and leave the whole UI blank.
- Windows: session-export filenames avoid the reserved device names (`CON`, `COM1`, ...), which cannot be saved even with an extension.
- Windows: database paths are compared case- and separator-insensitively, so re-entering the same path no longer reports a spurious restart requirement.
- Windows: the simulator's listener uses `SO_EXCLUSIVEADDRUSE`, since `SO_REUSEADDR` there permits binding an address that is already actively listening.
  - A second `mcu-sim` started silently and was never connected to.
- A `config.toml` saved with a UTF-8 byte-order mark is now read normally, and a save writes it back without one.
  - `tomllib` rejects a BOM with "Invalid statement (at line 1, column 1)", naming neither the cause nor the fix.
    - On Windows a BOM is what the ordinary tools produce (PowerShell's `Out-File -Encoding utf8` always writes one).
    - Hand-editing the config the obvious way there stopped the daemon starting over an invisible character.
- Windows: `mcuscoped` now refuses a port that is already being listened on, instead of binding it anyway.
  - uvicorn sets `SO_REUSEADDR` unconditionally, which on Windows (unlike POSIX) permits that bind.
    - A second daemon, or a first one on a port some other service held, started, printed its web UI URL and was never reachable.
- Windows: settings saves, the `daemon start` pid record and the update-check cache retry the atomic file replace.
  - The replace fails there whenever another process holds a transient handle on either file (an on-access virus scan or the Search indexer is enough).
  - POSIX `rename(2)` never fails this way, so a save that always worked on Linux could be lost on Windows.
- Windows: `mcu daemon start` no longer exits with a traceback if the pid file cannot be written.
  - The daemon records itself on startup anyway, so it warns and carries on rather than breaking the exit-code contract with a live daemon already spawned.
- `GET /devices` enumerates serial ports on a worker thread.
  - That call is a cheap sysfs walk on Linux but a setupapi query on Windows, where it held the event loop - freezing every WebSocket feed and every other request - for as long as the scan took.
- Exporting a session that is still running answered `400`.
  - With no `end_id` yet, the copy resolved its upper bound through the event loop's SQLite connection from the worker thread it runs on, which sqlite3 refuses.
    - Every existing test stopped the session first, so the branch was never exercised.
  - This affected the automatic session the daemon always has open, on every platform.
- Windows: `mcu devices` could die with a `UnicodeEncodeError` when redirected to a file or pipe, breaking the exit-code contract, because a redirected stdout falls back to the locale encoding.
- Importing `mcuscope` on Python older than 3.11 now says so, naming the interpreter, instead of failing later with `No module named 'tomllib'`.
- A port that could not be opened wrote a `sys` row per retry, so an unplugged board buried the capture (and the terminal panes) in thousands of identical "open failed" lines.
  - The reason is now recorded once per disconnected episode and the reconnect reports the retries as a count: `port board connected: /dev/ttyACM0 (after 214 failed attempts)`.
- The status bar's lines/s readout appeared and vanished with the traffic, shifting the port chips sideways every second.
  - Its box is now reserved (fixed width, tabular figures) and the "terminal paused" notice moved to its own badge, so the chips hold still.
- Windows: under a GUI-subsystem interpreter (`pythonw.exe`), `mcuscoped` ran with no output and could not be stopped with Ctrl-C.
  - uv can select `pythonw.exe` as a tool venv's base via KiCad's vendored runtime.
  - The daemon now attaches to the parent's console (`AttachConsole`, falling back to a new one), reattaches the std streams to it, and installs the console control handler that a late attach never gets.
    - The banner appears in the launching terminal and Ctrl-C shuts down gracefully again.
- Windows: `mcu daemon stop` was never actually graceful, and its liveness probe (`os.kill(pid, 0)`) could itself disrupt or miss the daemon.
  - `CTRL_BREAK_EVENT` cannot reach a process on another console, and the detached daemon has none.
  - Stop now goes through `POST /shutdown`, waits for the process to exit with a real non-signalling probe, and hard-terminates only as a last resort, verifying afterwards that nothing still answers.
- A pid record left behind by a crashed daemon could block the next daemon's claim once the pid was recycled, leaving it unstoppable by `mcu daemon stop`.
  - A claim now only defers to a record naming its own live parent (the `daemon start` launcher).
  - The record is also claimed atomically, written atomically by `daemon start`, and released even when startup fails before the server runs.
- Closing the terminal window on Windows hard-killed an attached daemon before its graceful shutdown could run; the console close event now holds the ~5s grace window open while shutdown proceeds.
- Release workflow: the changelog section is extracted and validated before the PyPI publish, so a forgotten changelog roll no longer burns the version number.

## [0.1.1] - 2026-07-28

### Added

- Firmware markers (SPEC 2.5): `!m [@<tick>] <text>` lets the MCU annotate the timeline itself; a well-formed marker is stored on the `marker` channel alongside `mcu mark` and session boundaries.
  - Firmware calls `monitor_mark("calibration start")`, or just `printf("!m boot done\n")` with no library at all.
- Scientific notation in plot values and `*<scale>` factors (SPEC 2.5).
  - Float `printf("%g")` output such as `1.2e-05` is plotted instead of silently dropped, and `*9.8e-4` reads better than `*0.00098`.
- Simulator: a `mark <text>` command, so the marker path is exercisable end to end with no hardware.
- `mcuscoped --version` and `mcu --version` report which Python interpreter is running.
- Any startup crash is also written to a `mcuscoped-crash.log` in the data directory, so a failing install can always be diagnosed.

### Fixed

- Windows: `mcuscoped` exited 1 with no output at all when run under a Python whose standard streams are null - notably KiCad's bundled interpreter, which `uv tool install` can select from `PATH`.
  - Null streams are now reattached to the console (`CONOUT$`) at startup, and uvicorn's colour autodetection (the crash site) is bypassed with an explicit `use_colors=False`.
- An automatic session whose only device traffic was a firmware marker is no longer dropped as empty when it closes.

## [0.1.0] - 2026-07-28

First public release.

- `mcuscoped` daemon: owns the serial port, timestamps and stores every line in SQLite, and serves a REST + WebSocket API on `127.0.0.1:8558`.
  - Capture continues with no client attached, and an OS-level lock enforces one daemon per capture database.
- `mcu` CLI: the primary human and AI interface over that API, with `--json` output everywhere and a stable exit-code contract (0 success/match, 1 error, 2 timeout, 3 daemon unreachable).
- `mcu wait` and `mcu assert`: block on a pattern, or judge a whole capture window with a pass/fail exit code, so agents and CI can branch on results instead of reading logs.
  - Multiple `--expect`/`--forbid` conditions, live or retrospective.
- Sessions: name a span of the capture, list, export as a standalone SQLite database, and delete (label alone or with its data).
  - The daemon opens an automatic session per run; retention keeps the newest N sessions regardless of age, with an optional size cap.
- Web UI: multi-pane terminal, port setup, decoded CAN view, realtime analog plots, and a combined digital/enum panel sharing one time base and cursor.
  - Settings page edits the full config (bind address, storage, saved ports) with the TOML file staying hand-editable.
- LAN access with an optional access token (`MCUSCOPED_TOKEN` / `--token`), rate-limited against brute force; loopback clients stay friction-free.
- Portable C firmware monitor module (`firmware/monitor/`) implementing the command/event protocol, with host-compiled tests and an integration guide.
- Hardware-free simulator (`mcu-sim`, or in-process via `mcuscoped --sim --open`): fake I2C, SPI, GPIO, ADC and a CAN heartbeat, so the full stack runs and is tested with no board attached.
- Cross-platform: Linux and Windows 10/11, `COMx`, `/dev/tty*` and `socket://host:port` device strings.

[Unreleased]: https://github.com/dwatman/mcuscope/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/dwatman/mcuscope/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/dwatman/mcuscope/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/dwatman/mcuscope/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/dwatman/mcuscope/releases/tag/v0.1.0
