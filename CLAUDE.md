# CLAUDE.md

## What this is

MCUscope is a hardware debug bridge.
A Python daemon (`mcuscoped`) owns the serial port to an embedded target (STM32 or any MCU), timestamps and stores every line into SQLite, and serves a local REST + WebSocket API on `127.0.0.1:8765`.
The `mcu` CLI is a thin client over that API and is the **primary interface for both the human and the AI agent**.
A hardware-free simulator (`mcuscope.sim`, console script `mcu-sim`) lets the entire stack run and be tested with no board attached.

Two authoritative documents govern the work:

- **`docs/SPEC.md`** is the design contract (wire protocol, REST/WS API, DB schema, firmware monitor contract). When code and SPEC disagree, SPEC wins. Change SPEC deliberately, not to paper over an implementation shortcut.
- **`docs/IMPLEMENTATION_PLAN.md`** is a phased plan with per-phase acceptance criteria. Work strictly in phase order; leave each phase working and tested. Do **not** pull items from the "Phase P2 backlog" forward without the owner asking.

`docs/REVIEW.md` is the review runbook: a registry of confirmed defect classes with the sweep that finds each new instance, the legs a round runs, and its exit criterion.
Review from it, and add a class whenever a round confirms one.

Current phase status lives in the "Status" tracker at the top of `docs/IMPLEMENTATION_PLAN.md` (single source of truth); update it there when a phase lands, not here.

## Commands

All host development happens from the `host/` directory.
Python 3.11+ is required, a uv-managed 3.12 virtualenv lives at `host/.venv`. uv venvs have no `pip` - use `uv pip install`.

`uv run python` resolves the venv interpreter on both OSes, so these are literal on Linux and Windows alike.
(The direct paths differ: `.venv/bin/python` against `.venv\Scripts\python.exe`.)

```bash
cd host
uv venv --python 3.12               # first-time; a bare `uv venv` may pick a <3.11 python
uv pip install -e '.[dev]'          # first-time setup into .venv

# Run tests
uv run python -m pytest                                # full suite (~4 min)
uv run python -m pytest tests/test_e2e.py::test_status # a single test
uv run python -m pytest -k can                         # tests matching a name
uv run python -m pytest tests/test_webui_js.py         # web UI JS only (needs node 18+)

# Lint (must be clean; ruff config lives in pyproject.toml, line length 100)
uv run python -m ruff check .
uv run python -m ruff check --fix .

# Run the simulator alone (TCP listener, prints socket://127.0.0.1:9900)
mcu-sim

# Run the daemon and CLI (installed as console scripts)
mcuscoped --port 8765            # add --sim for the zero-hardware demo
mcu status
mcu cmd 'i2c scan'
```

Tests are cross-platform and need no hardware and no subprocess daemon by default: the e2e/CLI suites spin up sim+daemon in background threads, see `host/tests/support.py`.
The port they drive opens a `link.SourceLink` onto the simulator core in process, so there is no serial listener; `socket://` and the TCP listener keep a deliberate set of their own (`test_sim_tcp.py`, `test_sim_pty.py`).
`docs/ARCHITECTURE.md` "What the tests attach to" says which tier uses which and why.

## Cross-platform mandate (non-negotiable)

Everything must work identically on **Linux and Windows 10/11**, which constrains real design choices:

- Use plain **pyserial**, never `pyserial-asyncio` (removed on purpose: unreliable on Windows). The serial layer is one blocking reader thread per port, bridged into the asyncio loop with `loop.call_soon_threadsafe`.
- Device strings go through `serial.serial_for_url`, so `COMx`, `/dev/tty*` and `socket://host:port` all work.
- All filesystem paths come from **platformdirs** (config dir, data dir, pid file). Never hard-code `/etc`, `~/.config` or `%APPDATA%`.
- The simulator's default transport is **TCP**; `--pty` is POSIX-only and refuses to run on Windows. Prefer TCP (`socket://`) everywhere, including tests.
- Text files written for the user (exports, pid files) need an explicit `newline=`, or Windows turns `\n` into CRLF and byte counts stop matching the file on disk.

## Architecture

Request flow: `mcu` CLI (httpx) -> REST/WS on 127.0.0.1 -> daemon -> serial link -> UART -> MCU.
Only the daemon touches the port; there is no "port busy", and capture continues even with no client attached.

`docs/ARCHITECTURE.md` covers each module of `host/mcuscope/` and the design constraints that are not obvious from the code: the single writer and why it stays on the loop, the `regex` mandate for user patterns, the presence-gated reconnect, the pid record's rules, and the CLI's exit-code contract.
Read it before changing any of them.

`mcuscope/sim.py` is a standalone, I/O-free-core simulator speaking the full protocol (fake I2C 0x48 temp / 0x50 EEPROM, SPI echo, GPIO, ADC, a 10 Hz CAN heartbeat on id 0x100).
It ships in the package (console script `mcu-sim`; `mcuscoped --sim` runs it in-process as the zero-hardware demo).
`tools/mcu_sim.py` is a back-compat shim for source checkouts, imported by tests via `sys.path` injection in `host/tests/conftest.py`.

`firmware/monitor/` holds the portable C monitor module (SPEC section 5): `monitor.h`, `monitor.c`, `monitor_cmds.c`, a port-shim template and `INTEGRATION.md`.
Host-compiled tests live in `firmware/tests/` (gcc), wired into pytest via `host/tests/test_firmware_monitor.py`, which skips cleanly with no C compiler present.

The web UI JavaScript is tested the same way: `host/tests/test_webui_js.py` shells out to `node --test` over `host/tests/webui_js/`, skipping cleanly without node 18+.
No npm packages; the DOM is a stub in `dom_stub.mjs`.
The stub cannot fake a laid-out canvas (`clientWidth` is always 0), so anything reached only through one is out of its range: put that logic in a DOM-free module and test it there, as `timewindow.js` does for the time-to-pixel projection.
What remains manual-verify against the simulator is the drawing itself, the uPlot glue and the settings dialog.

## Conventions

- No em dashes (U+2014) or en dashes (U+2013) anywhere: code, comments, docstrings, docs, commit messages.
- Keep phases in a working state, with the test suite and ruff green, before moving on.
- Minimise dependencies; add one only when it clearly earns its place (`regex` did, for its `timeout=`).
- Refreshing `docs/img/webui.png` has real traps (a headless capture comes out empty): follow `docs/SCREENSHOTS.md`.
