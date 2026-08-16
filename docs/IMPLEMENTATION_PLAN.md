# Implementation Plan (for Claude Opus)

Read `docs/SPEC.md` first; it is the authoritative contract.
This file sequences the work into phases with acceptance criteria.
Work strictly in phase order: each phase leaves the repo in a working, tested state.
Do not pull **[P2]** items forward.

House rules for all phases:

- No em dashes or en dashes anywhere: code, comments, docs, commit messages. Use commas, colons, parentheses, or spaced hyphens.
- Python >= 3.11, type hints throughout, `ruff` clean (add a minimal ruff config).
- Firmware C: C99, no dynamic allocation, no HAL/LL includes in core files.
- Keep dependencies to the set listed in SPEC 3.1. Do not add ORM, pydantic beyond what FastAPI needs, or a config framework.
- Every phase ends with its tests passing via `pytest` from `host/`.
- Host code targets BOTH Linux (Ubuntu/Mint) and Windows 10/11.
  - Never use POSIX-only APIs (pty, fork, signals beyond SIGINT/SIGTERM handling, hardcoded /dev or ~/.config paths) outside the simulator's pty mode and the systemd contrib file.
  - Paths come from `platformdirs`.
  - The e2e suite runs against the simulator's TCP mode and must pass on both OSes; pty-specific tests skip cleanly on Windows (`pytest.mark.skipif(os.name == 'nt', ...)`).

---

## Status

Live progress tracker (single source of truth for what is done).
Update the marker when a phase lands: `[x]` done and acceptance verified, `[~]` in progress, `[ ]` not started.
Add a one-line note only when reality diverged from the plan below.

- [x] Phase 0: scaffold
- [x] Phase 1: protocol module + simulator
- [x] Phase 2: daemon
- [x] Phase 3: CLI
- [x] Phase 4: firmware monitor module
- [x] Phase 5: docs and packaging polish
- [x] Phase 6: web UI (terminal, setup, CAN view)
- [x] Phase 7: realtime plotting
- [x] Phase 7 addendum: Digital/Enum panel (logic-analyser bit traces + enum/state bands, sharing the plot time base and cursor) - the two P2 web UI items below, pulled forward and landed with the owner's sign-off.
- [x] Post-plan addendum: config write-back API + web UI settings page (SPEC 3.3.1), owner-requested.
  Full setup from an empty config via the browser; token became runtime-only (MCUSCOPED_TOKEN / --token, never a config key).
- [x] Post-plan addendum: hardening.
  Security and reliability review passes: input validation, bounded regex matching, a WebSocket subscriber leak, a writer that survives commit failures, bounded RX/write queues with drop-oldest accounting.
  Plus LAN access behind a runtime-only access token with per-address brute-force limiting.
- [x] Post-plan addendum: one-command demo.
  `mcuscoped --sim` runs the simulator in-process and autoconnects to it; `--open` launches the browser.
  The simulator moved into the package (`mcuscope.sim`, console script `mcu-sim`); `tools/mcu_sim.py` is now a source-checkout shim.
- [x] Post-plan addendum: capture-throughput pass.
  Sustained ingest went from about 950 lines/s (saturated at 142% CPU) to over 40,000, and `/ws` sends arrays of coalesced rows rather than one frame per line.
- [x] Post-plan addendum: sessions (owner-requested).
  A named id range over the one capture timeline: `session=` on the query and export endpoints, `mcu session start|stop|list`, and a record button in the UI status bar.
- [x] Post-plan addendum: retention rework (owner-requested).
  `retention_days` 7 -> 10, a `storage.min_sessions` floor so the newest N runs never expire by age, and an opt-in `storage.max_db_bytes` size cap measured against live content.
- [x] Post-plan addendum: verdicts and capture management (owner-requested).
  `mcu assert` (retrospective or live; `--expect`/`--forbid`/`--min-window`), `mcu session export` to a standalone capture database, and `mcu purge` / `mcu session delete --data` for deliberate deletion.
- [x] Post-plan addendum: automatic sessions and the single-writer guard.
  `storage.auto_session` records a session per daemon run, so the retention floor protects real runs without anyone naming one by hand.
  `mcuscoped` takes an OS lock on `<db_path>.lock` so two daemons cannot write one capture.
- [x] Post-plan addendum: deferred-item sweep before hardware bring-up.
  An idle keepalive on `/ws`, a dedicated bounded pool for user regexes, CLI coverage for the commands that had none, and a pytest timeout backstop.
- [x] Post-plan addendum: presence-gated reconnect (owner-requested).
  While a device node is absent the reader polls for it at 4 Hz and opens the moment it returns, instead of sleeping out a backoff that had doubled to its cap by the time a replug finished.
  The doubling wait still covers a device that is present but will not open, and `socket://` / `rfc2217://` where no cheap presence test exists.
  Measured on real hardware (STLINK-V3 VCP on `/dev/ttyACM0`), reconnect after the node reappeared: **0.185 s** for a 25 s unplug and **0.345 s** for a 40 s unplug, against a 0.40 s design ceiling (`PRESENCE_POLL_S` + `PRESENCE_SETTLE_S`) and independent of how long the board was out.
  The unplug itself was noticed in 0.021 s and 0.001 s, and the recovered link passed a command round trip.
  For the same two replugs the old schedule would have reconnected 0.26 s and 5.69 s after the node returned (computed from its retry offsets, not measured).
- [x] Post-plan addendum: firmware markers and scientific notation (owner-requested).
  `!m [@<tick>] <text>` lets the MCU annotate the timeline itself, stored on the `marker` channel next to `mcu mark` and the session boundaries, with `monitor_mark()` on the firmware side filling the tick from the port.
  The tick sigil is `@` rather than a bare leading number, so free-form marker text that starts with a digit is never silently reinterpreted as a tick.
  Plot values and `*<scale>` factors now accept scientific notation, which also stops `%g` output from firmware that has float printf being dropped.
- [x] Post-plan addendum: bench-feedback trio (owner-requested, 2026-07-29).
  Reconnect attempts no longer narrate themselves into the capture: one row per reason per disconnected episode, and the reconnect carries the retry count (SPEC 3.2).
  A release check (SPEC 3.6) asks PyPI once a day, cached across restarts and off by one config key or `MCUSCOPE_UPDATE_CHECK=0`.
  Reported by the UI badge and `mcu status`; dismissing the badge hides only that version, so a newer release still shows.
  The status bar's lines/s box is reserved and the high-rate notice moved to its own badge, so the port chips stop jittering with the traffic.

Notes:

- Phase 1: simulator defaults to TCP transport (`socket://`); the SPEC-mentioned pty mode is POSIX-only (`--pty`). This keeps the whole stack testable on Windows.
- Phase 4: firmware C tests run as a host-compiled suite (`firmware/tests/`, gcc) wired into pytest via `host/tests/test_firmware_monitor.py`; the wrapper skips cleanly when no C compiler is on PATH.
  `arm-none-eabi` is a documented compile-only check (`make arm-check`), not required by the suite.
- Phase 6: the terminal is a multi-pane, independently-filtered view (not the single pane-with-filter the SPEC first sketched); SPEC 9.1 was updated to match.
  Relative-time is a shared toolbar control for all panes (SPEC updated), not per-pane.
  Paused panes freeze (stable scrollbar, "N new" counter).
  Static UI assets are served no-cache so browsers pick up updates without a hard refresh.
  No terminal download button: data is already persisted to SQLite and `mcu log export` covers filtered dumps; the specced UI export button is a Phase 7 plot feature.
  Manual smoke: `tools/webui_smoke.py`.
- Phase 7: host-side pipeline (protocol decode of `!p`/`!pd`/`!ps`, `plot_points` store, per-port def cache with restart priming, `/plot/channels`, `/plot/series`, `/plot/export` long+wide CSV, CLI `mcu plot channels`/`export`; tests in `test_plot.py`).
  Serial reader now timestamps each burst at arrival (was up to one READ_TIMEOUT of lines under one coarse time), which matters for host-time plotting.
  Browser panel uses vendored uPlot 1.6.31.
  Deliberate divergences from SPEC 9.2, all in the SPEC now:
  - one **shared time base** (host / MCU-tick / relative, with a common relative zero) drives both the terminal timestamps and the plot x axis, instead of a plot-only host/tick toggle;
  - each channel gets its own auto-ranged y scale (mixed units, y axis undrawn, values in the legend);
  - traces are stepped (hold-last), not interpolated;
  - the cursor is linked across charts and can be driven by hovering a terminal line.
  Manual smoke: `tools/webui_smoke.py` (auto-checks the plot channels).
- Phase 7 addendum: added a Digital/Enum panel below the analog charts, rendering packed-bit channels as stacked square waves and enum/state channels as labelled state bands.
  It shares the analog charts' time base and cursor (per-channel colour, window/pause/csv/collapse controls).
  Simulator emits `state` (enum) and `led/irq/pwm_en` (bits) under `--plot`.
  Manual smoke: `tools/webui_smoke.py` (auto-checks the enum + bit channel classification).
- Sessions are non-overlapping by construction: they are an id range, not a per-row column, so nothing is written per line and existing captures need no migration, at the cost that starting a session closes the running one.
  The `auto` flag added later is the one schema change to `sessions`, applied to existing captures with `ALTER TABLE`.
  (`CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so a migration list in `store.py` carries later columns.)
- `mcu assert` deliberately diverges from the CLI exit-code contract: `1` means the assertion failed, and it never exits `2`.
  A window that closes with an expectation unmet is a verdict, not an inability to reach one. SPEC 4 records this.
- With automatic sessions on, `POST /sessions/stop` reports "no session is running" when only the daemon's own session is open: it belongs to the daemon run rather than the caller, which keeps `session start` / `session stop` a matched pair.
  An automatic session that recorded no device traffic is dropped when it closes.
- The single-writer guard is an OS lock, not a pid file, because the kernel releases it however the process exits: a crash cannot leave one stranded.
  The realistic stuck case is a restart racing its predecessor's shutdown, covered by a short retry; `--ignore-capture-lock` covers a filesystem without working file locks.
  The Windows half (`msvcrt.locking`) is exercised only by the Windows CI matrix, not locally.

---

## Phase 0: scaffold

- `host/pyproject.toml` (package `mcuscope`, console scripts `mcuscoped` and `mcu`, deps per SPEC 3.1, dev deps `pytest`, `pytest-asyncio`, `ruff`).
- Package skeleton: `mcuscope/__init__.py`, `protocol.py`, `store.py`, `serial_link.py`, `server.py`, `daemon.py`, `cli.py` (stubs with docstrings).
- `firmware/monitor/` and `tools/` directories with placeholder files.
- `git init`, sensible `.gitignore` (Python, C objects, `*.db`).

Acceptance: `uv tool install .` (or `pip install -e host/`) succeeds; `mcu --help` and `mcuscoped --help` print stubs; `pytest` collects and passes (zero or trivial tests).

## Phase 1: protocol module + simulator

- `mcuscope/protocol.py`: pure functions/dataclasses, no I/O.
  Line classification (debug/resp/event), command line formatting with seq, response parsing (OK/ERR, error-code table from SPEC 2.3), `!can` event encode/decode (flags token, RTR DLC convention, tick), hex helpers with validation.
- `tools/mcu_sim.py` implementing SPEC section 7 fully, importing `protocol.py` for formatting so sim and daemon share one encoding implementation.
  Runnable standalone: TCP mode by default (prints the listening port), `--pty` on POSIX (prints the slave path), `--help` documents fault flags.
- `host/tests/test_protocol.py` covering every branch of SPEC 2.3 to 2.5, including: seq 65535 wrap, oversized line, CRLF tolerance, RTR frames, extended ids, malformed `!can` returning a "store as generic event" signal rather than raising.

Acceptance: protocol tests pass; running `python tools/mcu_sim.py` and sending `>1 ping` over a TCP connection to it (a 5-line test script) yields `<1 OK monitor 1 sim`, on both Linux and Windows.

## Phase 2: daemon

- `store.py`: SQLite schema per SPEC 3.5, WAL mode, insert path (single writer task consuming an asyncio queue so serial reading never blocks on disk), query helpers for /lines and /can/frames, retention sweep, marker/sys inserts.
- `serial_link.py`: per-port link built on plain pyserial (NOT pyserial-asyncio, per SPEC 3.1).
  - A blocking reader thread pushing bytes into the asyncio loop via `loop.call_soon_threadsafe`.
  - Devices opened with `serial.serial_for_url` so COMx, /dev/tty*, and socket:// all work; optional `serial_number` resolution via `list_ports` at each (re)connect.
  - Line assembly (LF, strip CR, 4 KB safety cap on host side); classification via `protocol.py`.
  - Auto-reconnect with exponential backoff (0.5 s to 5 s), `sys` rows on connect/disconnect.
  - TX path with per-port asyncio lock; seq assignment and in-flight response matching with timeout and late-response tolerance (SPEC 3.2 item 3); clean thread shutdown on detach.
- `server.py` + `daemon.py`: FastAPI app implementing every endpoint in SPEC 3.4 exactly, config loading (SPEC 3.3, paths via `platformdirs`), WS fan-out (per-connection queue, drop-oldest on slow consumer, never block the store path), graceful shutdown.
- `host/contrib/mcuscoped.service` (systemd user unit, Linux convenience only) and `host/contrib/config.example.toml`.
- `host/tests/test_e2e.py` (daemon half): fixture starts sim (TCP mode, ephemeral port) + daemon on an ephemeral port with a temp db.
  Test every endpoint: cmd ok/err/timeout, wait (match, timeout, with send), lines filters (chan, match, last_ms, since_id, limit cap), can frames by id, marker, status, send raw, devices listing, WS receives live rows.
  Also: sim `--drop-response` produces `"status": "timeout"` and a late response is logged but not delivered, and reconnect works after the sim's TCP connection drops and returns.
  One POSIX-only test attaches via `--pty` (skip on Windows).

Acceptance: e2e daemon tests pass on Linux and Windows.
Manual smoke: start sim, start daemon with a config pointing at `socket://127.0.0.1:<port>`, `curl /status` shows the port connected, `curl /cmd` with `i2c scan` returns `48 50`.

## Phase 3: CLI

- `cli.py` with typer, implementing the full table in SPEC section 4.
  - Global flags, exit-code contract (0/1/2/3), `--json` single-object output, human formatting for `tail` and `can dump`, `-f` follow via WS.
  - `mcu daemon start|stop|status`: detached spawn on both OSes (`start_new_session=True` on POSIX, `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` creationflags on Windows; pid file in the platformdirs data dir).
  - `mcu ai-guide` (write the guide text per SPEC 6.1: about 60 lines, agent-oriented, emphasize cmd/wait/lines and `--json`).
- Extend `test_e2e.py`: drive the installed `mcu` entry point as a subprocess against the live fixture.
  Assert exit codes for ok/err/timeout/unreachable and `--json` shape for cmd, wait, lines, can dump, i2c sugar (`--reg` maps to wrrd).

Acceptance: full test suite passes.
`mcu i2c rd 48 2 --json` against the sim returns the fake temperature bytes with exit 0, and stopping the daemon makes any command exit 3 with a one-line stderr message.

## Phase 4: firmware monitor module

- Implement `monitor.h`, `monitor.c`, `monitor_cmds.c` exactly per SPEC 5.2 to 5.4.
  Plus `port_template/monitor_port_template.c` with every shim stubbed (returning `MONITOR_ERR_NOSUP`) and TODO comments referencing the owner's UART circular-buffer driver.
  And weak default shims so unimplemented buses degrade to `ERR 7 nosup`.
- `firmware/tests/`: host-compiled harness (plain makefile, gcc): fake shims backed by arrays, a `feed(line) -> expect(response)` helper.
  Tests mirror the protocol suite: ping, all bus commands, badcmd/badarg/nosup, overflow discard, tokenizer edge cases, registered custom command, event emission draining a fake CAN queue.
  Plus monitor_plot: definition parsing, little-endian struct to big-endian hex including f4 bit patterns and negative s2 values, len mismatch rejection, and the 5 s !pd rebroadcast driven by a fake tick.
  Wire into pytest with a small wrapper that runs `make -C firmware/tests` and the produced binary; skip if no C compiler (and on Windows unless gcc is present).
- `firmware/monitor/INTEGRATION.md`: step-by-step for a bare-metal LL superloop project.
  Covers: files to add, the three port functions against a DMA+IRQ circular-buffer UART driver, and shims for each bus with LL/HAL(bxCAN) example sketches.
  Also the line-atomicity requirement for the application's debug printf, the CAN RX queue pattern (IRQ pushes to a small ring, `mon_can_rx_pop` drains), and the manual smoke checklist against real hardware.

Acceptance: firmware host tests pass via pytest.
`monitor.c`/`monitor_cmds.c` compile with `-Wall -Wextra -Werror` under both gcc (host) and, syntactically, arm-none-eabi flags documented in the makefile (actual cross-compile optional if toolchain absent).

## Phase 5: docs and packaging polish

- Finish `README.md`: quickstart (install, run sim, run daemon, first `mcu` commands), real-hardware setup, config reference pointer.
  OS-specific steps called out for both Linux and Windows (device names, config paths, dialout group vs driver notes).
- `docs/CLAUDE_SNIPPET.md` per SPEC 6.2.
- Verify `uv tool install` from a clean environment; pin minimum versions in pyproject; final ruff pass; ensure no em/en dashes anywhere (`grep -rP '[\x{2013}\x{2014}]' .` must return nothing).

Acceptance: a new user (or agent) can go from clone to talking to the simulator using only README instructions, on either Linux or Windows.

## Phase 6: web UI (terminal, setup, CAN view)

Implement SPEC 9.1 exactly: static `webui/` files served at `/ui`, no build step, no framework, no network fetches.
Status/setup bar with attach dialog fed by `GET /devices`, terminal view (WS live + backfill, filters, autoscroll rules), command box (cmd/raw modes, localStorage history), CAN latest-per-id table, marker button.

Testing: endpoint-level tests for `/devices`, static serving, no-cache headers, and the backfill order contract live in `tests/test_webui.py`.
The non-trivial UI logic (line ring buffer, CAN parse/EWMA) is kept in small pure JS functions.
Manual smoke harness is `tools/webui_smoke.py`: it brings up sim (`--plot --garbage`) + daemon, auto-verifies the backend half of the acceptance list, and prints a per-panel browser checklist.

Acceptance, with the simulator attached:

- the UI shows live debug lines;
- a command typed in the box returns its response inline;
- the CAN table shows the 0x100 heartbeat with a period near 100 ms;
- attach/detach of a second sim works from the dialog;
- the page works with the machine offline.

## Phase 7: realtime plotting

Implement SPEC 9.2:

- vendored uPlot;
- ingest of both plot formats into `plot_points` (ad-hoc `!p`; typed `!pd`/`!ps` with definition cache, scale applied at ingest, and startup cache priming from stored lines);
- `GET /plot/channels`, `GET /plot/series`, `GET /plot/export` (long and wide CSV);
- CLI `mcu plot channels` and `mcu plot export`;
- plot panel with channel checkboxes (units shown), window select, pause, cursor readout.

Add `--plot` and `--plot-late-def` to the simulator (SPEC 7) if not already done.

Extend e2e tests:

- ad-hoc decode (fixed-point, negative values);
- typed decode (negative s2, u4, f4 bit-exact round trip, scale applied);
- `!ps` before any `!pd` stored as generic event and skipped;
- definition arriving late starts decoding from that point (`--plot-late-def`);
- daemon restart recovers definitions from stored lines;
- token width/count mismatch rejected safely;
- series decimation;
- wide CSV rejects mixed sids;
- retention cascade removes plot points.

Acceptance:

- sim with `--plot` shows ad-hoc and typed traces (including the f4 channel) live in the browser;
- `mcu plot export --wide` of the typed stream opens cleanly in a spreadsheet with one row per sample and scaled values;
- a malformed plot line neither crashes ingest nor appears in `plot_points`.

## Phase P2 backlog (do not start without the owner)

In rough priority order: flash+reset integration, pytest HIL fixtures, DBC decoding, MCP wrapper, CAN FD, binary plot streaming, RTT transport.
Design intent for each is in SPEC 10.
DBC decoding also has a full design note at `docs/DBC_DECODING.md` (query-time decode, 1.5 to 2 days, and a case for not building it on speculation).

Web UI enhancements (also owner-gated), for the same sidebar "Plots" section:

- ~~**Digital / logic-analyser traces**~~ and ~~**State-machine state view**~~: both landed as the Digital/Enum panel (see the Phase 7 addendum above); no longer backlog.
- **Markers on the charts** (owner-requested, noted 2026-07-28): draw `marker` rows as vertical annotation lines across every chart, text on hover, sharing the x axis and the linked cursor.
  This is what the optional MCU tick on `!m` was put on the wire for.
  Design intent and the open question (how to place a marker that has no tick when the time base is MCU tick) are in SPEC 9.2.

### Deferred review findings (2026-07-07 full-project review)

Items surfaced by the 2026-07-07 review pass and deliberately deferred.
None are urgent; pick up individually when relevant.
Several have landed since; the checklist below carries the current state, and the CHANGELOG the details.

- [ ] Daemon
  - [x] Plot downsampling (min/max decimation) so long windows render without shipping full-resolution arrays.
  - [x] WebSocket keepalive so idle subscribers with vanished clients are reaped before the next row: `/ws` sends an empty array after 20 s of silence (SPEC 3.4), which surfaces a dead peer as a failing write instead of never.
  - [x] Covering indexes: benchmarked, and **partly rejected** - `lines(port, chan, id)` made port-only queries 3000x slower.
    The existing `lines(chan, ts)` proved harmful (every query orders by id) and was replaced with `lines(chan, id)`, which took `--chan debug` on a 3M-row capture from 810 ms to 0.2 ms.
    `plot_points(name, line_id)` already existed.
  - [x] Dedicated bounded executor for user-regex queries so a slow-pattern burst cannot delay port detach/shutdown joins: `store.match_executor()`, 4 workers, used by `/lines`, `/wait` and `/assert`.
  - [ ] SPEC note: host stores over-length terminated debug lines up to the 4 KB safety cap (SPEC 2.1 vs capture behaviour).
- [ ] Web UI
  - [ ] Global keyboard shortcuts (pause-all, focus filter, focus marker, dismiss result strip).
  - [ ] Marker list with click-to-jump cursor sync between terminal and plots.
  - [ ] Command autocomplete from history and known command verbs.
  - [ ] CSV export of the filtered terminal pane (plots and digital already export).
- [ ] Simulator
  - [ ] Persist sim state (tick, counters, plot defs) across TCP reconnects to mimic a real MCU.
  - [ ] Settable `can stat` bus state and on-demand error-code injection so the full error table gets an e2e path.
- [ ] Tests
  - [x] CLI-level coverage for `send`, `mark`, `attach`/`detach`, `ports`, `tail -f`, `log export`, `spi`, `gpio`, `adc`, `can tx/stat/filter`.
  - [ ] A Windows run of the capture-lock suite has only ever happened in CI; no local Windows verification of `msvcrt.locking`.
  - [x] pytest-timeout so a hung socket fails fast instead of stalling CI: 90 s per test, `thread` method (reaches a stall inside a background reader thread and dumps every stack).
- [ ] Firmware
  - [x] Compile-time assert that the worst-case plot line fits `g_out` so the bound survives limit changes.
  - [ ] INTEGRATION.md note that the SPSC CAN ring example assumes single-core Cortex-M (no `__DMB()`).
  - [ ] `i2c scan`: signal (or document) response truncation when many devices ACK.
- [ ] Docs / release
  - [ ] Web UI screenshots in README (owner will capture; placeholder comment is in place at `docs/img/webui.png`).
