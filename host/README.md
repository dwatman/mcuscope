# MCUscope

**MCUscope** is a hardware debug bridge for embedded targets. It lets both humans and AI
agents (such as Claude Code) talk to an STM32 (or any) microcontroller over a serial link:
send CAN/I2C/SPI/GPIO/ADC commands, stream and query timestamped debug output, and plot
realtime data in the browser.

A single daemon (`mcuscoped`) owns the serial port, timestamps every line into SQLite, and
serves a local REST + WebSocket API and a web UI on `127.0.0.1:8765`. The `mcu` CLI is a
thin client over that API and is the primary interface for both the human and the agent.

This package (`mcuscope`) is the host side. The portable C firmware "monitor" module that
runs on the target, a hardware-free simulator, and the full specification live in the
[project repository](https://github.com/dwatman/mcuscope).

## Install

Requires Python 3.11+.

```bash
uv tool install mcuscope        # or: pipx install mcuscope
```

This exposes two console scripts on your PATH: `mcuscoped` (the daemon) and `mcu` (the CLI).

## Quickstart

```bash
# 1. Attach a serial port and start the daemon (it owns the port and captures everything)
mcu attach /dev/ttyACM0 --baud 115200 --alias board   # Linux
mcu attach COM7 --baud 115200 --alias board           # Windows
mcuscoped                                              # serves the API + web UI on :8765

# 2. Talk to it
mcu status                        # daemon + port health
mcu cmd ping                      # -> monitor 1 <alias>
mcu cmd 'i2c scan'                # -> 48 50
mcu tail -f                       # follow live capture
```

Open `http://127.0.0.1:8765/ui` for the web UI: live terminal, CAN table, and realtime
plots.

Every command takes `--json` for a single machine-readable object and returns meaningful
exit codes (**0** success/match, **1** error or bad usage, **2** timeout, **3** daemon
unreachable). Run `mcu ai-guide` for a compact, agent-oriented cheat sheet.

## No hardware? Use the simulator

The [project repository](https://github.com/dwatman/mcuscope) ships a full simulator
(`tools/mcu_sim.py`) that speaks the entire protocol over a TCP socket, so you can run the
whole stack with nothing plugged in. From a checkout:

```bash
python tools/mcu_sim.py                       # prints e.g. socket://127.0.0.1:9900
mcu attach socket://127.0.0.1:9900 --alias sim
mcuscoped
```

## Documentation

Full quickstart, configuration reference, protocol/API specification, and firmware
integration guide are in the [project repository](https://github.com/dwatman/mcuscope).

## License

MIT
