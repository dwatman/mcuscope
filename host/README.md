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

<!-- Absolute URL on purpose: PyPI does not resolve repo-relative image paths, and it only
     renders once the repository is public. -->
![MCUscope web UI](https://raw.githubusercontent.com/dwatman/mcuscope/main/docs/img/webui.png)

## Install

Requires Python 3.11 or newer.

```bash
uv tool install mcuscope        # or: pipx install mcuscope
```

This exposes three console scripts on your PATH: `mcuscoped` (the daemon), `mcu` (the
CLI), and `mcu-sim` (the hardware-free simulator).

To reach a real serial port, one OS-specific step:

- **Linux**: your user must be in the `dialout` group: `sudo usermod -aG dialout $USER`,
  then log out and back in. Without it, opening `/dev/ttyACM0` fails with permission denied.
- **Windows 10/11**: most USB-serial adapters and ST-Link VCPs work with the in-box
  driver; some need the vendor driver (CP210x, CH340, FTDI).

Neither is needed for the quickstart below, which runs with no hardware attached.

## Quickstart

No hardware needed to try it:

```bash
mcuscoped --sim --open            # daemon + built-in simulator; opens the web UI
```

The web UI at `http://127.0.0.1:8765/ui/` shows the live terminal, CAN table, and
realtime plots. The Plots panel also renders a Digital/Enum view (logic-analyser bit
traces and labelled enum/state bands) sharing the same time base and cursor as the
analog charts.

With real hardware, start the daemon first (it owns the port and captures everything),
then attach the port - from the UI's **+ Attach** dialog, or the CLI:

```bash
mcuscoped                                              # serves the API + web UI on :8765
# in another terminal (or use `mcu daemon start` to background the daemon):
mcu devices                                            # find the port name
mcu attach /dev/ttyACM0 --baud 115200 --alias board    # Linux
mcu attach COM7 --baud 115200 --alias board            # Windows

mcu status                        # daemon + port health
mcu cmd ping                      # -> monitor 1 <project>  (port-layer name, not the alias)
mcu cmd 'i2c scan'                # -> 48 50
mcu tail -f                       # follow live capture
```

Every command takes `--json` for a single machine-readable object and returns meaningful
exit codes (**0** success/match, **1** error or bad usage, **2** timeout, **3** daemon
unreachable). Run `mcu ai-guide` for a compact, agent-oriented cheat sheet.

The simulator also runs standalone (`mcu-sim`, prints e.g. `socket://127.0.0.1:9900`);
attach it like any device: `mcu attach socket://127.0.0.1:9900 --alias sim`.

## Documentation

Full quickstart, configuration reference, protocol/API specification, and firmware
integration guide are in the [project repository](https://github.com/dwatman/mcuscope).

## License

MIT
