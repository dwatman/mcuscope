# MCUscope

[![CI](https://github.com/dwatman/mcuscope/actions/workflows/ci.yml/badge.svg)](https://github.com/dwatman/mcuscope/actions/workflows/ci.yml)

**MCUscope** is a hardware debug bridge that lets both humans and AI agents (Claude Code)
interact with STM32 (or any) microcontrollers over a serial link: send CAN/I2C/SPI/GPIO/ADC
commands, stream and query debug output, plot realtime data, and run
send-and-wait-for-response interactions with timeouts.

Installed as the `mcuscope` package, it provides the `mcuscoped` daemon and the `mcu` CLI.

## Architecture

```
MCU firmware "monitor" module          PC (Linux or Windows 10/11)
+--------------------------+   UART   +-------------------------+      +- mcu CLI (human + AI)
| cmd parser, CAN / I2C /  +----------+ mcuscoped daemon:       +------+- web UI: terminal, setup,
| SPI / GPIO / ADC proxies |          | owns serial port,       | REST |  CAN view, realtime plots
| + normal debug printf    |          | timestamps all traffic  | + WS +- pytest HIL tests (later)
| + !p plot data points    |          | into SQLite, serves UI  |      +- MCP wrapper (later)
+--------------------------+          +-------------------------+
```

<!-- TODO: drop a web UI screenshot here once one exists, e.g.:
     ![MCUscope web UI](docs/img/webui.png)
     Suggested shot: terminal + CAN view + realtime plots panel in one frame. -->

Key ideas:

- The daemon (`mcuscoped`) is the **sole owner of the serial port**. Everyone else
  (the `mcu` CLI, a live tail, tests, Claude) is a client over a local REST/WebSocket
  API. No more "port busy", and the log exists even when no client is attached.
- The wire protocol is **line-oriented text** sharing the UART with normal debug
  prints. Machine traffic is tagged with leading characters (`>` `<` `!`); everything
  else is treated as debug output. Sequence numbers correlate commands with responses.
- All traffic is timestamped and stored in **SQLite**, so "the last 20 CAN frames with
  id 0x1A3" or "debug lines matching X in the past 2 seconds" are cheap queries.
- The **primary AI interface is the `mcu` CLI** with a `--json` flag and meaningful
  exit codes. It works identically for the human and the agent.

## Quickstart (no hardware required)

The MCU simulator speaks the full protocol over a TCP socket, so you can run the entire
stack with nothing plugged in. Everything below works identically on Linux and Windows
10/11; where a command differs, both forms are given.

### 1. Install

Python 3.11+ is required. The simplest install puts the two console scripts,
`mcuscoped` (the daemon) and `mcu` (the CLI), on your PATH in an isolated environment:

```bash
uv tool install ./host          # or from PyPI once published: uv tool install mcuscope
# pipx works too:
pipx install ./host             # or: pipx install mcuscope
```

Works identically on Linux and Windows 10/11. For a development setup (editable install
with test/lint deps), see [Development](#development) below.

### 2. Start the simulator

In one terminal, from the repo root:

```bash
python tools/mcu_sim.py
# prints the address to attach to, e.g.:  socket://127.0.0.1:9900
```

Add `--plot` to stream plot data, `--garbage` to inject junk, or `--tcp-port 0` for an
ephemeral port (the chosen port is printed). On POSIX you can use `--pty` to expose a
real `/dev/pts/*` device instead of a socket.

### 3. Start the daemon

Point the daemon at the simulator with a small config file. Create `sim.toml`:

```toml
[[ports]]
alias = "sim"
device = "socket://127.0.0.1:9900"
autoconnect = true
```

Then, in a second terminal:

```bash
mcuscoped -c sim.toml            # serves the API on 127.0.0.1:8765
```

Or run it in the background and check it with the CLI:

```bash
mcu daemon start                 # spawns a detached mcuscoped using your default config
mcu daemon status
```

`mcu daemon start` reads the config from the platform config dir (see
[Configuration](#configuration)); use `mcuscoped -c <file>` when you want an explicit
config such as the `sim.toml` above.

### 4. Talk to it

```bash
mcu status                       # daemon + port health; "sim ... connected"
mcu cmd ping                     # -> monitor 1 sim
mcu cmd 'i2c scan'               # -> 48 50
mcu cmd 'i2c rd 48 2'            # read 2 bytes from the fake temperature sensor
mcu can dump -n 5                # recent decoded CAN frames (10 Hz heartbeat on id 0x100)
mcu tail -f                      # follow live capture (Ctrl-C to stop)
mcu i2c rd 48 2 --json           # machine-readable output for scripting/agents
```

Every command takes `--json` for a single machine-readable object, and returns
meaningful exit codes: **0** success/match, **1** error or bad usage, **2** timeout,
**3** daemon unreachable. Run `mcu ai-guide` for a compact, agent-oriented cheat sheet.

## Real hardware

Replace the simulator with a real serial device. Flash the firmware monitor module (see
`firmware/monitor/INTEGRATION.md`) onto your MCU, connect its debug UART to the PC, then
attach the port.

Device strings and per-OS notes:

- **Linux**: `/dev/ttyACM0`, `/dev/ttyUSB0`, or the stable
  `/dev/serial/by-id/usb-...`. Your user must be in the `dialout` group to open the
  port (`sudo usermod -aG dialout $USER`, then log out and back in).
- **Windows 10/11**: `COM7` (find it in Device Manager). Most USB-serial adapters and
  ST-Link VCPs work with the in-box driver; some need the vendor driver (CP210x, CH340,
  FTDI).

Attach at runtime, or persist the port in the config file:

```bash
mcu attach /dev/ttyACM0 --baud 115200 --alias board     # Linux
mcu attach COM7 --baud 115200 --alias board             # Windows
mcu status
```

Because the daemon reconnects automatically, unplugging and replugging the device
resumes capture with no restart. Prefer `serial_number` in the config (below) for a port
that stays identified across reboots and re-enumeration on both OSes.

## Configuration

Config is optional; an absent file yields defaults with no ports. It lives at
`platformdirs.user_config_dir("mcuscope")/config.toml`:

- **Linux**: `~/.config/mcuscope/config.toml`
- **Windows**: `%APPDATA%\mcuscope\config.toml`

The default capture database lives under `platformdirs.user_data_dir("mcuscope")`. All
keys are optional, and a missing file is fine: start `mcuscoped` with no config, open
the web UI, and set everything up from the settings page (it writes this file, and the
file stays hand-editable; comments survive UI edits). `mcuscoped --config PATH` (or env
`MCUSCOPED_CONFIG`) selects an alternate config file:

```toml
[server]
host = "127.0.0.1"      # bind "0.0.0.0" to reach the daemon across the LAN
port = 8765

[storage]
db_path = ""            # default: <user_data_dir>/mcuscope/capture.db
retention_days = 7

[[ports]]
alias = "board"                          # name used by clients
device = "/dev/serial/by-id/usb-STM..."  # or COM7, /dev/ttyACM0, socket://127.0.0.1:9900
# serial_number = "066BFF3..."           # alternative to device: resolve via USB serial
                                         # number at each (re)connect, stable on both OSes
baud = 115200
autoconnect = true
```

`mcuscoped --host` / `--port` / `--token` override `[server]` at launch; `mcu --url` (or
the `MCUSCOPE_URL` env var) points the CLI at a non-default daemon address.

### LAN access

Bind `host = "0.0.0.0"` (or run `mcuscoped --host 0.0.0.0`) to reach the daemon from
other machines on your network. Set an access token when you do. The token is
runtime-only, never stored in the config file (so the UI-editable config can never
change authentication):

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"   # generate one
MCUSCOPED_TOKEN=that-token mcuscoped                           # preferred: env var
mcuscoped --token that-token                                   # or the flag
```

With a token set, every non-loopback client must present it (`mcu --token ...` or the
`MCUSCOPE_TOKEN` env var; the web UI prompts for it and remembers it). Local clients on
the daemon machine never need the token. Without a token, a non-loopback bind serves
the API unauthenticated to anyone on the network - the daemon warns loudly at startup,
and config editing over the API stays loopback-only until a token is set.

## Repository layout

```
docs/SPEC.md                 Full system specification (protocol, API, schema, firmware contract)
docs/IMPLEMENTATION_PLAN.md  Phased plan with acceptance criteria
docs/CLAUDE_SNIPPET.md       Paste-in block that tells Claude Code the bridge exists
host/                        Python package: mcuscoped daemon + mcu CLI (+ tests)
firmware/monitor/            Portable C monitor module + port shim template + INTEGRATION.md
tools/mcu_sim.py             MCU simulator for hardware-free development and tests
```

## Development

For an editable install with the test and lint dependencies:

```bash
cd host
uv venv                             # creates .venv (or: python -m venv .venv)
uv pip install -e '.[dev]'          # uv venvs have no pip; use `uv pip`
```

See `CLAUDE.md` for the full developer workflow (test commands, lint, cross-platform
rules). The test suite runs the whole stack against the simulator with no hardware:

```bash
.venv/bin/python -m pytest          # POSIX; on Windows: .venv\Scripts\python.exe -m pytest
.venv/bin/python -m ruff check .
```

## Status

Phases 0-7 complete: protocol + simulator, daemon (capture + REST/WS API), `mcu` CLI,
portable firmware monitor module, docs/packaging, web UI (terminal, setup, CAN view), and
realtime plotting. Remaining work is the Phase P2 backlog (flash+reset, HIL fixtures, DBC
decoding, MCP wrapper, and more). See `docs/IMPLEMENTATION_PLAN.md` for the live tracker.
