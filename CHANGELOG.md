# Changelog

All notable changes to MCUscope are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the major version is 0, the interfaces in `docs/SPEC.md` (wire protocol, REST API, CLI exit codes) may still change between minor releases.

## [Unreleased]

### Added

- `mcuscoped` always writes a pid file and a `mcuscoped-startup.log` (URL, pid, interpreter report, stop instructions) in the data directory, so `mcu daemon stop` works however the daemon was started - previously only `mcu daemon start` wrote the pid record, and a daemon launched as `mcuscoped` was invisible to it.
- `--version` flags the windowless-interpreter case explicitly (`[windowless: no console - output and Ctrl-C unavailable]`).
- Install docs: on Windows, pin a real interpreter with `uv tool install mcuscope --python 3.12` when PATH is led by a vendored runtime (KiCad, GIMP, Blender).
- `POST /shutdown` (loopback only): a graceful stop over REST, now the primary channel of `mcu daemon stop`. `GET /status` reports the daemon's `pid`, so a fallback kill targets the serving process rather than a Windows launcher shim.
- Update notice: the daemon asks PyPI once a day (cached across restarts) whether a newer MCUscope exists, and the web UI shows a badge naming it. Dismissing snoozes on a ladder (a day, a week, a month, then permanently for that version) rather than silencing it for good on the first click. Off with `[update] check = false`, the Settings dialog, or `MCUSCOPE_UPDATE_CHECK=0`.

### Fixed

- The simulator died permanently on `can tx 7FF` (and `can tx 1FFFFFFF x`). The echo frame is id+1, which at the top of the range is out of range, so formatting it raised from inside the event pump and unwound the serving thread - while the listening socket stayed open, so the daemon reconnected into a backlog nobody was accepting from and reported a healthy port that never produced another byte. The echo id now wraps within its own range, and a client session can no longer take the listener down with it.
- `mcu -p board lines --match -p ...`: a global option before the subcommand stopped argv hoisting from resolving that subcommand, which disabled the guard protecting subcommand option values. `--port` could silently become the next option (`--port=--limit`), or the command failed with a confusing "unexpected extra argument".
- `mcu wait --send ...` could report a timeout without examining a single captured line: the send is given the same timeout as the whole wait, so a slow command consumed the window and the loop exited before draining a queue that may already hold the match. Exit 2 on a run that actually matched.
- A cancelled `/cmd` (client disconnect, Ctrl-C) leaked its pending-sequence entry, because `CancelledError` is a `BaseException` and escaped the cleanup that `TimeoutError` triggered.
- Events are dispatched on their whole first token rather than a prefix, so a future `!candy`/`!power` line is no longer forced through the CAN or plot decoder and logged as a bogus decode failure.
- Sequence numbers are parsed strictly (ASCII decimal only): bare `int()` accepted `+17`, `1_7` and non-ASCII digits, so a garbled response could resolve the pending command for seq 17. The plot, enum and marker-tick grammars likewise use `[0-9]` rather than `\d`.
- SPEC 2.4: the simulator refused to reject `can filter <id> <mask> r`, answering `OK` to a filter it could not honour.
- A startup failure between opening the store and serving left the writer task, the retention task and the SQLite connection running with nothing to stop them.
- `PortManager` kept one carried-counter entry per alias ever attached, with nothing to prune it.
- Web UI: a large `*<scale>` factor could carry a finite sample to `Infinity`, and uPlot's auto-range then returned `[NaN, NaN]`, silently erasing every series on that chart.
- Web UI: the shared tick anchor was set from an unbounded value, so one corrupt line could shift every terminal timestamp and chart x-axis for the rest of the session.
- Web UI: two overlapping stream reconnects could drop the rows captured across the gap entirely, because the staging buffer was a single global shared by every socket. Staging is now per-connection.
- Web UI: after a capture-database reset the terminal stayed empty until new traffic arrived, and a failed backfill was completely silent while the UI still looked live.
- Web UI: the colour picker never opened in Firefox, which cannot drive a detached `<input type=color>`.
- Windows: saving settings from the web UI rewrote the whole `config.toml` with CRLF endings, the one text write in the package that did not pin `newline=`.
- Windows: a serial port could be closed while a write was still in flight in the driver, and the reader thread's handle was left held if its join timed out (blocking a re-attach of the same COM port, which Windows opens exclusively).
- Windows: `.js` and `.css` content types are pinned rather than read from the registry, where a stale `HKEY_CLASSES_ROOT` entry would make the browser refuse `app.js` as a module script and leave the whole UI blank.
- Windows: session-export filenames avoid the reserved device names (`CON`, `COM1`, ...), which cannot be saved even with an extension; and database paths are compared case- and separator-insensitively, so re-entering the same path no longer reports a spurious restart requirement.
- Windows: the simulator's listener uses `SO_EXCLUSIVEADDRUSE`, since `SO_REUSEADDR` there permits binding an address that is already actively listening - a second `mcu-sim` started silently and was never connected to.
- A `config.toml` saved with a UTF-8 byte-order mark is now read normally, and a save writes it back without one. `tomllib` rejects a BOM with "Invalid statement (at line 1, column 1)", naming neither the cause nor the fix - and on Windows a BOM is what the ordinary tools produce (PowerShell's `Out-File -Encoding utf8` always writes one), so hand-editing the config the obvious way there stopped the daemon starting over an invisible character.
- Windows: `mcuscoped` now refuses a port that is already being listened on, instead of binding it anyway. uvicorn sets `SO_REUSEADDR` unconditionally, which on Windows (unlike POSIX) permits that bind, so a second daemon - or a first one on a port some other service held - started, printed its web UI URL and was never reachable.
- Windows: settings saves, the `daemon start` pid record and the update-check cache retry the atomic file replace, which fails there whenever another process holds a transient handle on either file (an on-access virus scan or the Search indexer is enough). POSIX `rename(2)` never fails this way, so a save that always worked on Linux could be lost on Windows.
- Windows: `mcu daemon start` no longer exits with a traceback if the pid file cannot be written; the daemon records itself on startup anyway, so it warns and carries on rather than breaking the exit-code contract with a live daemon already spawned.
- `GET /devices` enumerates serial ports on a worker thread. That call is a cheap sysfs walk on Linux but a setupapi query on Windows, where it held the event loop - freezing every WebSocket feed and every other request - for as long as the scan took.
- Exporting a session that is still running answered `400`. With no `end_id` yet, the copy resolved its upper bound through the event loop's SQLite connection from the worker thread it runs on, which sqlite3 refuses; every existing test stopped the session first, so the branch was never exercised. This affected the automatic session the daemon always has open, on every platform.
- Windows: `mcu devices` could die with a `UnicodeEncodeError` when redirected to a file or pipe, breaking the exit-code contract, because a redirected stdout falls back to the locale encoding.
- Importing `mcuscope` on Python older than 3.11 now says so, naming the interpreter, instead of failing later with `No module named 'tomllib'`.
- A port that could not be opened wrote a `sys` row per retry, so an unplugged board buried the capture (and the terminal panes) in thousands of identical "open failed" lines. The reason is now recorded once per disconnected episode and the reconnect reports the retries as a count: `port board connected: /dev/ttyACM0 (after 214 failed attempts)`.
- The status bar's lines/s readout appeared and vanished with the traffic, shifting the port chips sideways every second. Its box is now reserved (fixed width, tabular figures) and the "terminal paused" notice moved to its own badge, so the chips hold still.

- Windows: under a GUI-subsystem interpreter (`pythonw.exe`, which uv can select as a tool venv's base via KiCad's vendored runtime), `mcuscoped` ran with no output and could not be stopped with Ctrl-C. The daemon now attaches to the parent's console (`AttachConsole`, falling back to a new one), reattaches the std streams to it, and installs the console control handler that a late attach never gets, so the banner appears in the launching terminal and Ctrl-C shuts down gracefully again.
- Windows: `mcu daemon stop` was never actually graceful - `CTRL_BREAK_EVENT` cannot reach a process on another console, and the detached daemon has none - and its liveness probe (`os.kill(pid, 0)`) could itself disrupt or miss the daemon. Stop now goes through `POST /shutdown`, waits for the process to exit with a real non-signalling probe, and hard-terminates only as a last resort, verifying afterwards that nothing still answers.
- A pid record left behind by a crashed daemon could block the next daemon's claim once the pid was recycled, leaving it unstoppable by `mcu daemon stop`; a claim now only defers to a record naming its own live parent (the `daemon start` launcher). The record is also claimed atomically, written atomically by `daemon start`, and released even when startup fails before the server runs.
- Closing the terminal window on Windows hard-killed an attached daemon before its graceful shutdown could run; the console close event now holds the ~5s grace window open while shutdown proceeds.
- Release workflow: the changelog section is extracted and validated before the PyPI publish, so a forgotten changelog roll no longer burns the version number.

## [0.1.1] - 2026-07-28

### Added

- Firmware markers (SPEC 2.5): `!m [@<tick>] <text>` lets the MCU annotate the timeline itself; a well-formed marker is stored on the `marker` channel alongside `mcu mark` and session boundaries. Firmware calls `monitor_mark("calibration start")`, or just `printf("!m boot done\n")` with no library at all.
- Scientific notation in plot values and `*<scale>` factors (SPEC 2.5), so float `printf("%g")` output such as `1.2e-05` is plotted instead of silently dropped, and `*9.8e-4` reads better than `*0.00098`.
- Simulator: a `mark <text>` command, so the marker path is exercisable end to end with no hardware.
- `mcuscoped --version` and `mcu --version` report which Python interpreter is running, and any startup crash is also written to a `mcuscoped-crash.log` in the data directory, so a failing install can always be diagnosed.

### Fixed

- Windows: `mcuscoped` exited 1 with no output at all when run under a Python whose standard streams are null - notably KiCad's bundled interpreter, which `uv tool install` can select from `PATH`. Null streams are now reattached to the console (`CONOUT$`) at startup, and uvicorn's colour autodetection (the crash site) is bypassed with an explicit `use_colors=False`.
- An automatic session whose only device traffic was a firmware marker is no longer dropped as empty when it closes.

## [0.1.0] - 2026-07-28

First public release.

- `mcuscoped` daemon: owns the serial port, timestamps and stores every line in SQLite, and serves a REST + WebSocket API on `127.0.0.1:8765`. Capture continues with no client attached, and an OS-level lock enforces one daemon per capture database.
- `mcu` CLI: the primary human and AI interface over that API, with `--json` output everywhere and a stable exit-code contract (0 success/match, 1 error, 2 timeout, 3 daemon unreachable).
- `mcu wait` and `mcu assert`: block on a pattern, or judge a whole capture window (multiple `--expect`/`--forbid` conditions, live or retrospective) with a pass/fail exit code, so agents and CI can branch on results instead of reading logs.
- Sessions: name a span of the capture, list, export as a standalone SQLite database, and delete (label alone or with its data). The daemon opens an automatic session per run; retention keeps the newest N sessions regardless of age, with an optional size cap.
- Web UI: multi-pane terminal, port setup, decoded CAN view, realtime analog plots, and a combined digital/enum panel sharing one time base and cursor; settings page edits the full config (bind address, storage, saved ports) with the TOML file staying hand-editable.
- LAN access with an optional access token (`MCUSCOPED_TOKEN` / `--token`), rate-limited against brute force; loopback clients stay friction-free.
- Portable C firmware monitor module (`firmware/monitor/`) implementing the command/event protocol, with host-compiled tests and an integration guide.
- Hardware-free simulator (`mcu-sim`, or in-process via `mcuscoped --sim --open`): fake I2C, SPI, GPIO, ADC and a CAN heartbeat, so the full stack runs and is tested with no board attached.
- Cross-platform: Linux and Windows 10/11, `COMx`, `/dev/tty*` and `socket://host:port` device strings.

[Unreleased]: https://github.com/dwatman/mcuscope/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/dwatman/mcuscope/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/dwatman/mcuscope/releases/tag/v0.1.0
