# Implementation Plan (for Claude Opus)

Read `docs/SPEC.md` first; it is the authoritative contract. This file sequences the
work into phases with acceptance criteria. Work strictly in phase order: each phase
leaves the repo in a working, tested state. Do not pull **[P2]** items forward.

House rules for all phases:

- No em dashes or en dashes anywhere: code, comments, docs, commit messages. Use
  commas, colons, parentheses, or spaced hyphens.
- Python >= 3.11, type hints throughout, `ruff` clean (add a minimal ruff config).
- Firmware C: C99, no dynamic allocation, no HAL/LL includes in core files.
- Keep dependencies to the set listed in SPEC 3.1. Do not add ORM, pydantic beyond
  what FastAPI needs, or a config framework.
- Every phase ends with its tests passing via `pytest` from `host/`.
- Deployment target is Linux; pty-based tests skip cleanly on Windows
  (`pytest.mark.skipif(os.name == 'nt', ...)`).

---

## Phase 0: scaffold

- `host/pyproject.toml` (package `hwbridge`, console scripts `hwbridged` and `mcu`,
  deps per SPEC 3.1, dev deps `pytest`, `pytest-asyncio`, `ruff`).
- Package skeleton: `hwbridge/__init__.py`, `protocol.py`, `store.py`,
  `serial_link.py`, `server.py`, `daemon.py`, `cli.py` (stubs with docstrings).
- `firmware/monitor/` and `tools/` directories with placeholder files.
- `git init`, sensible `.gitignore` (Python, C objects, `*.db`).

Acceptance: `uv tool install .` (or `pip install -e host/`) succeeds; `mcu --help`
and `hwbridged --help` print stubs; `pytest` collects and passes (zero or trivial
tests).

## Phase 1: protocol module + simulator

- `hwbridge/protocol.py`: pure functions/dataclasses, no I/O. Line classification
  (debug/resp/event), command line formatting with seq, response parsing (OK/ERR,
  error-code table from SPEC 2.3), `!can` event encode/decode (flags token, RTR DLC
  convention, tick), hex helpers with validation.
- `tools/mcu_sim.py` implementing SPEC section 7 fully, importing `protocol.py` for
  formatting so sim and daemon share one encoding implementation. Runnable
  standalone: prints its pty path, `--help` documents fault flags.
- `host/tests/test_protocol.py` covering every branch of SPEC 2.3 to 2.5, including:
  seq 65535 wrap, oversized line, CRLF tolerance, RTR frames, extended ids, malformed
  `!can` returning a "store as generic event" signal rather than raising.

Acceptance: protocol tests pass; running `python tools/mcu_sim.py` and manually
echoing `>1 ping` into the pty (e.g. with `screen` or a 5-line test script) yields
`<1 OK monitor 1 sim`.

## Phase 2: daemon

- `store.py`: SQLite schema per SPEC 3.5, WAL mode, insert path (single writer
  task consuming an asyncio queue so serial reading never blocks on disk), query
  helpers for /lines and /can/frames, retention sweep, marker/sys inserts.
- `serial_link.py`: per-port task using pyserial-asyncio; line assembly (LF, strip
  CR, 4 KB safety cap on host side); classification via `protocol.py`; auto-reconnect
  with exponential backoff (0.5 s to 10 s), `sys` rows on connect/disconnect; TX
  path with per-port asyncio lock; seq assignment and in-flight response matching
  with timeout and late-response tolerance (SPEC 3.2 item 3).
- `server.py` + `daemon.py`: FastAPI app implementing every endpoint in SPEC 3.4
  exactly, config loading (SPEC 3.3), WS fan-out (per-connection queue, drop-oldest
  on slow consumer, never block the store path), graceful shutdown.
- `contrib/hwbridged.service` (systemd user unit) and `contrib/config.example.toml`.
- `host/tests/test_e2e.py` (daemon half): fixture starts sim + daemon on an ephemeral
  port with a temp db; test every endpoint: cmd ok/err/timeout, wait (match, timeout,
  with send), lines filters (chan, match, last_ms, since_id, limit cap), can frames
  by id, marker, status, send raw, WS receives live rows, sim `--drop-response`
  produces `"status": "timeout"` and a late response is logged but not delivered.

Acceptance: e2e daemon tests pass; manual smoke: start sim, start daemon with a
config pointing at the sim pty, `curl /status` shows the port connected, `curl /cmd`
with `i2c scan` returns `48 50`.

## Phase 3: CLI

- `cli.py` with typer, implementing the full table in SPEC section 4: global flags,
  exit-code contract (0/1/2/3), `--json` single-object output, human formatting for
  `tail` and `can dump`, `-f` follow via WS, `mcu daemon start|stop|status`, and
  `mcu ai-guide` (write the guide text per SPEC 6.1: about 60 lines, agent-oriented,
  emphasize cmd/wait/lines and `--json`).
- Extend `test_e2e.py`: drive the installed `mcu` entry point as a subprocess against
  the live fixture; assert exit codes for ok/err/timeout/unreachable and `--json`
  shape for cmd, wait, lines, can dump, i2c sugar (`--reg` maps to wrrd).

Acceptance: full test suite passes; `mcu i2c rd 48 2 --json` against the sim returns
the fake temperature bytes with exit 0; stopping the daemon makes any command exit 3
with a one-line stderr message.

## Phase 4: firmware monitor module

- Implement `monitor.h`, `monitor.c`, `monitor_cmds.c` exactly per SPEC 5.2 to 5.4,
  plus `port_template/monitor_port_template.c` with every shim stubbed (returning
  `MONITOR_ERR_NOSUP`) and TODO comments referencing the owner's UART circular-buffer
  driver, and weak default shims so unimplemented buses degrade to `ERR 7 nosup`.
- `firmware/tests/`: host-compiled harness (plain makefile, gcc): fake shims backed
  by arrays, a `feed(line) -> expect(response)` helper, tests mirroring the protocol
  suite (ping, all bus commands, badcmd/badarg/nosup, overflow discard, tokenizer
  edge cases, registered custom command, event emission draining a fake CAN queue,
  and monitor_plot: definition parsing, little-endian struct to big-endian hex
  including f4 bit patterns and negative s2 values, len mismatch rejection, and the
  2 s !pd rebroadcast driven by a fake tick).
  Wire into pytest with a small wrapper that runs `make -C firmware/tests` and the
  produced binary; skip if no C compiler (and on Windows unless gcc is present).
- `firmware/monitor/INTEGRATION.md`: step-by-step for a bare-metal LL superloop
  project: files to add, the three port functions against a DMA+IRQ circular-buffer
  UART driver, shims for each bus with LL/HAL(bxCAN) example sketches, the
  line-atomicity requirement for the application's debug printf, CAN RX queue pattern
  (IRQ pushes to a small ring, `mon_can_rx_pop` drains), and the manual smoke
  checklist against real hardware.

Acceptance: firmware host tests pass via pytest; `monitor.c`/`monitor_cmds.c` compile
with `-Wall -Wextra -Werror` under both gcc (host) and, syntactically, arm-none-eabi
flags documented in the makefile (actual cross-compile optional if toolchain absent).

## Phase 5: docs and packaging polish

- Finish `README.md`: quickstart (install, run sim, run daemon, first `mcu` commands),
  real-hardware setup, config reference pointer.
- `docs/CLAUDE_SNIPPET.md` per SPEC 6.2.
- Verify `uv tool install` from a clean environment; pin minimum versions in
  pyproject; final ruff pass; ensure no em/en dashes anywhere (`grep -rP '[\x{2013}\x{2014}]' .`
  must return nothing).

Acceptance: a new user (or agent) can go from clone to talking to the simulator using
only README instructions.

## Phase 6: web UI (terminal, setup, CAN view)

Implement SPEC 9.1 exactly: static `webui/` files served at `/ui`, no build step, no
framework, no network fetches. Status/setup bar with attach dialog fed by
`GET /devices`, terminal view (WS live + backfill, filters, autoscroll rules),
command box (cmd/raw modes, localStorage history), CAN latest-per-id table, marker
button.

Testing: endpoint-level tests for `/devices` and static serving; UI logic that is
non-trivial (line ring buffer, CAN table EWMA period) should live in small pure JS
functions exercised by a Node-free check if practical, otherwise document a manual
smoke script: run sim with `--plot --garbage`, open UI, verify each panel behaves.

Acceptance: with the simulator attached, the UI shows live debug lines, a command
typed in the box returns its response inline, the CAN table shows the 0x100 heartbeat
with a period near 100 ms, attach/detach of a second sim works from the dialog, and
the page works with the machine offline.

## Phase 7: realtime plotting

Implement SPEC 9.2: vendored uPlot, ingest of both plot formats into `plot_points`
(ad-hoc `!p`; typed `!pd`/`!ps` with definition cache, scale applied at ingest, and
startup cache priming from stored lines), `GET /plot/channels`, `GET /plot/series`,
`GET /plot/export` (long and wide CSV), CLI `mcu plot channels` and
`mcu plot export`, plot panel with channel checkboxes (units shown), window select,
pause, cursor readout. Add `--plot` and `--plot-late-def` to the simulator (SPEC 7)
if not already done.

Extend e2e tests: ad-hoc decode (fixed-point, negative values); typed decode
(negative s2, u4, f4 bit-exact round trip, scale applied); `!ps` before any `!pd`
stored as generic event and skipped; definition arriving late starts decoding from
that point (`--plot-late-def`); daemon restart recovers definitions from stored
lines; token width/count mismatch rejected safely; series decimation; wide CSV
rejects mixed sids; retention cascade removes plot points.

Acceptance: sim with `--plot` shows ad-hoc and typed traces (including the f4
channel) live in the browser; `mcu plot export --wide` of the typed stream opens
cleanly in a spreadsheet with one row per sample and scaled values; a malformed
plot line neither crashes ingest nor appears in `plot_points`.

## Phase P2 backlog (do not start without the owner)

In rough priority order: flash+reset integration, pytest HIL fixtures, DBC decoding,
MCP wrapper, CAN FD, binary plot streaming, RTT transport. Design intent for each is
in SPEC 10.
