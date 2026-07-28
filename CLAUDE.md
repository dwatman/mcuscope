# CLAUDE.md

## What this is

MCUscope is a hardware debug bridge. A Python daemon (`mcuscoped`) owns the serial
port to an embedded target (STM32 or any MCU), timestamps and stores every line into
SQLite, and serves a local REST + WebSocket API on `127.0.0.1:8765`. The `mcu` CLI is a
thin client over that API and is the **primary interface for both the human and the AI
agent**. A hardware-free simulator (`tools/mcu_sim.py`) lets the entire stack run and be
tested with no board attached.

Two authoritative documents govern the work:

- **`docs/SPEC.md`** is the design contract (wire protocol, REST/WS API, DB schema,
  firmware monitor contract). When code and SPEC disagree, SPEC wins. Change SPEC
  deliberately, not to paper over an implementation shortcut.
- **`docs/IMPLEMENTATION_PLAN.md`** is a phased plan with per-phase acceptance criteria.
  Work strictly in phase order; leave each phase working and tested. Do **not** pull
  items from the "Phase P2 backlog" forward without the owner asking.

Current phase status lives in the "Status" tracker at the top of
`docs/IMPLEMENTATION_PLAN.md` (single source of truth); update it there when a phase
lands, not here.

## Commands

All host development happens from the `host/` directory. Python 3.11+ is required,
a uv-managed 3.12 virtualenv lives at `host/.venv`.
uv venvs have no `pip` - use `uv pip install`.

```bash
cd host
uv pip install -e '.[dev]'          # first-time setup into .venv

# Run tests (invoke the venv interpreter directly; on Windows use .venv/Scripts/python.exe)
.venv/bin/python -m pytest                      # full suite (~332 tests, ~2 min)
.venv/bin/python -m pytest tests/test_e2e.py::test_status   # a single test
.venv/bin/python -m pytest -k can               # tests matching a name

# Lint (must be clean; ruff config lives in pyproject.toml, line length 100)
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff check --fix .

# Run the simulator (defaults to a TCP listener printing socket://127.0.0.1:9900)
python tools/mcu_sim.py

# Run the daemon and CLI (installed as console scripts)
mcuscoped --port 8765
mcu status
mcu cmd 'i2c scan'
```

On POSIX the interpreter is `.venv/bin/python`. Tests are cross-platform and run without
any hardware or subprocess daemon by default (the e2e/CLI suites spin up sim+daemon in
background threads on ephemeral ports - see `host/tests/support.py`).

## Cross-platform mandate (non-negotiable)

Everything must work identically on **Linux and Windows 10/11**. This constrains real
design choices, so keep it in mind:

- Use plain **pyserial**, never `pyserial-asyncio` (removed on purpose: unreliable on
  Windows). The serial layer is one blocking reader thread per port, bridged into the
  asyncio loop with `loop.call_soon_threadsafe`.
- Device strings go through `serial.serial_for_url` so `COMx`, `/dev/tty*`, and
  `socket://host:port` all work.
- All filesystem paths come from **platformdirs** (config dir, data dir, pid file). Never
  hard-code `/etc`, `~/.config`, or `%APPDATA%`.
- The simulator's default transport is **TCP**; its `--pty` mode is POSIX-only and
  refuses to run on Windows. Prefer TCP (`socket://`) everywhere, including tests.

## Architecture

Request flow: `mcu` CLI (httpx) -> REST/WS on 127.0.0.1 -> daemon -> serial link ->
UART -> MCU. Only the daemon touches the port; there is no "port busy", and capture
continues even with no client attached.

Host package `host/mcuscope/` (see each module's docstring):

- **`protocol.py`** - pure, no I/O, and the shared source of truth for both daemon and
  simulator: keep it that way, and fully unit-tested. Encodes/decodes the line protocol
  (`>SEQ CMD`, `<SEQ OK/ERR`, `!` events, anything else debug), 7-bit ASCII, LF-terminated,
  255 bytes max. Holds the error-code table, seq wrap (`next_seq`, 1-65535, never 0) and
  CAN frame parse/format; malformed CAN events return `None` rather than raising.
- **`store.py`** - SQLite capture (WAL, FK cascade). A **single async writer task**
  drains a queue and is the only writer; it allocates `lines.id` itself so a whole batch
  goes in with one `executemany`, and callers await a future to get the inserted row
  back. WebSocket subscribers are fed by fan-out with drop-oldest. Schema is `lines`,
  `can_frames` and `sessions` (SPEC 3.5) plus `plot_points` (SPEC 9.2); later columns
  arrive through `_MIGRATIONS`, since `CREATE TABLE IF NOT EXISTS` cannot alter an
  existing table. Retention is age-based with a `min_sessions` floor, plus an opt-in size
  cap measured against live content rather than file size. `match_executor()` runs every
  user-supplied regex (`/lines`, `/wait`, `/assert`) and the `/can/frames` join, the
  heaviest read the API serves; the point is keeping them off the *default* executor,
  which joins the serial reader thread on detach and shutdown and must never queue behind
  analytics. **User patterns compile with the third-party `regex` module, never stdlib
  `re`** - `re` holds the GIL for a whole backtrack, so a 7-character pattern froze the
  process and the pool was decoration. `regex` releases the GIL and honours `timeout=`,
  which `_make_regexp` turns into a per-call ceiling plus a per-query budget; exceeding
  either raises `MatchBudgetExceeded` and the API answers 400, never a timeout result
  (which the CLI would report as exit 2). Internal patterns stay on `re`.
- **`serial_link.py`** - `SerialPort` (reader thread, reconnect backoff, seq/pending
  machinery) and `PortManager`. On command timeout the pending entry is popped, so a late
  response is **logged but not delivered** (SPEC 3.2). Reconnect is automatic and its
  backoff presence-gated (`_retry_wait`): an absent device node is cheap to test for, so it
  is polled at `PRESENCE_POLL_S` and opened the moment it returns (sub-second replug),
  while a device present but unopenable keeps the doubling wait. `_cached_comports()`
  gives port enumeration a short shared TTL, so N polling reader threads do not each pay
  for a setupapi/sysfs scan. `_make_drain` splits by transport: `in_waiting` is a real byte
  count only on native ports, so `socket://` drains with a zero timeout instead (pyserial's
  URL handlers implement `in_waiting` as a 0/1 readability poll, which made the sized read
  fetch one byte per syscall).
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
- **`daemon.py`** - `mcuscoped` entry point: load config, apply `--host/--port`
  overrides, take the capture lock, `uvicorn.run`.
- **`config.py`** - TOML config via `tomllib` + platformdirs. A missing file is fine.
- **`cli.py`** - the `mcu` typer app. **Exit-code contract (SPEC 4): 0 success/match, 1
  error or bad usage, 2 timeout, 3 daemon unreachable.** `mcu assert` is the documented
  exception: `1` means the assertion failed, and it never exits `2`. Global options
  (`--json`, `--port/-p`, `--url`, `--token`) are hoisted to the front of argv in `main()`
  so they work in any position (`mcu i2c rd 48 2 --json`). Two typer traps: in
  non-standalone mode the `Exit` code comes back as the call's **return value**, not an
  exception (`main()` must return it); and typer vendors its own click, so `typer.Abort`
  is not `click.exceptions.Abort` - catch both (`ABORT_EXCEPTIONS` and friends) or
  control-flow exceptions escape to typer's rich handler and print a traceback at the user.

`mcuscope/sim.py` is a standalone, I/O-free-core simulator speaking the full protocol
(fake I2C 0x48 temp / 0x50 EEPROM, SPI echo, GPIO, ADC, a 10 Hz CAN heartbeat on id
0x100). It ships in the package (console script `mcu-sim`; `mcuscoped --sim` runs it
in-process as the zero-hardware demo). `tools/mcu_sim.py` is a back-compat shim for source
checkouts, imported by tests via `sys.path` injection in `host/tests/conftest.py`.

`firmware/monitor/` holds the portable C monitor module (SPEC section 5): `monitor.h`,
`monitor.c`, `monitor_cmds.c`, a port-shim template and `INTEGRATION.md`. Host-compiled
tests live in `firmware/tests/` (gcc), wired into pytest via
`host/tests/test_firmware_monitor.py`, which skips cleanly with no C compiler present.

## Conventions

- No em dashes (U+2014) or en dashes (U+2013) anywhere: code, comments, docstrings, docs, commit messages.
- Keep phases in a working state, with the test suite and ruff green, before moving on.
- Minimise dependencies; add one only when it clearly earns its place (`regex` did, for its `timeout=`).
- Refreshing `docs/img/webui.png` has real traps (a headless capture comes out empty): follow `docs/SCREENSHOTS.md`.
