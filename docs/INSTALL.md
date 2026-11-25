# Installing MCUscope

## What you need

- Python 3.10 or newer.
- Linux or Windows 10/11. CI runs the test suite on both; macOS is untested.
- For a real board: a UART on the MCU that your PC can reach, and its baud rate.
  - A USB-serial adapter or a debug probe's virtual COM port (ST-Link VCP, for example) both work.

The [hardware-free demo](../README.md#quick-start) needs only Python.

## Install

MCUscope installs as an isolated command-line tool, with uv or with pipx:

```bash
uv tool install mcuscope        # or: pipx install mcuscope
```

That puts three commands on your PATH:

| Command | What it is |
|---|---|
| `mcuscoped` | the daemon: owns the serial port, stores the capture, serves the web UI |
| `mcu` | the command-line client |
| `mcu-sim` | a simulated MCU, for trying things with no board |

If `mcuscoped` is not found, run `uv tool update-shell` (or `pipx ensurepath`) and open a new terminal.

No uv or pipx yet?
Install [uv](https://docs.astral.sh/uv/getting-started/installation/) (or [pipx](https://pipx.pypa.io/stable/)).
uv can also install Python itself, which helps when `python -V` reports something older than 3.10 or there is no `python` at all:

```bash
uv python install 3.13
```

## Upgrade and uninstall

```bash
uv tool upgrade mcuscope        # or: pipx upgrade mcuscope
uv tool uninstall mcuscope      # or: pipx uninstall mcuscope
```

`mcu status` and the web UI say when a newer release exists.
After an upgrade, restart a running daemon: `mcu daemon restart`.

Uninstalling leaves your config file and capture database in place.
[Configuration](CONFIGURATION.md#the-config-file) says where they are.

## Serial port access

- **Linux**: your user must be in the `dialout` group, or opening `/dev/ttyACM0` fails with "permission denied".
  - Run `sudo usermod -aG dialout $USER`, then log out and back in.
- **Windows**: most USB-serial adapters and ST-Link virtual COM ports work with the in-box driver.
  - Some adapters need the vendor driver (CP210x, CH340, FTDI).

## Windows notes

Run the examples in these docs from PowerShell.
They quote arguments with single quotes (`mcu cmd 'i2c scan'`), which PowerShell understands and `cmd.exe` does not.

`uv tool install` builds the tool around whichever `python` it finds first.
On a machine with KiCad, GIMP or Blender that can be a vendored interpreter unsuited to a console daemon.
If `mcu daemon start` fails or the daemon prints an interpreter warning, pin a real Python:

```powershell
uv tool install mcuscope --python 3.13 --force
```

## From source

See [Development](DEVELOPMENT.md) for an editable install with the test and lint dependencies.
