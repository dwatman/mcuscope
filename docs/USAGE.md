# Using MCUscope

This guide assumes MCUscope is [installed](INSTALL.md).
Most examples run against the built-in simulator (`mcu daemon start --sim`), so you can try them with no board.

Contents:

- [The daemon](#the-daemon)
- [Ports](#ports)
- [The web UI](#the-web-ui)
- [Reading the capture](#reading-the-capture)
- [Talking to the board](#talking-to-the-board)
- [CAN](#can)
- [Plots](#plots)
- [Sessions](#sessions)
- [Waiting and verdicts](#waiting-and-verdicts)
- [Scripting](#scripting)
- [The simulator](#the-simulator)
- [Full reference](#full-reference)

## The daemon

`mcuscoped` owns the serial ports and stores everything they send.
The web UI and the `mcu` command are both clients of it, so start it first.

```bash
mcu daemon start          # run in the background; returns when it is ready
mcu daemon status
mcu daemon restart
mcu daemon stop
```

`mcu daemon start` prints the web UI address, `http://127.0.0.1:8558/ui/` by default.
Add `--open` to open it in your browser, and `--sim` to attach the simulator.
To run the daemon in the foreground instead (for its log output, or under a service manager), run `mcuscoped`: Ctrl+C stops it, and it takes the same `--open` and `--sim`.

Capture continues while no browser or terminal is connected.
`mcu status` shows the daemon, the running session, and each port with its receive and transmit counts.

## Ports

### Attach

In the web UI, click **+ Attach**.
The dialog lists the serial devices it found, with their descriptions.
Pick one, set the baud rate and give it an alias.

From the shell:

```bash
mcu devices                                             # list serial devices on this machine
mcu attach /dev/ttyACM0 --baud 115200 --alias board     # Linux
mcu attach COM7 --baud 115200 --alias board             # Windows
mcu ports                                               # what is attached right now
mcu detach board
```

- `--baud` defaults to 115200.
- `--alias` is the name you use for the port afterwards. It defaults to the device's base name.
- `--serial SN` attaches by USB serial number (the fourth column of `mcu devices`) instead of by device name.
- `--eol none|lf|crlf` sets the line ending added to everything sent to the port (default `lf`).

### Keeping a port across restarts

A port attached from the shell stays until you detach it or the daemon stops.
To have it reattach at every daemon start, tick **Save to config** in the attach dialog, or add it to the [config file](CONFIGURATION.md#reattach-a-board-at-every-start).

Device names can change when a board is replugged.
Two things stay stable:

- On Linux, the `/dev/serial/by-id/usb-...` path.
- On both systems, the USB serial number (`--serial`, or `serial_number` in the config).

### Unplugging

The daemon reconnects by itself, retrying with a short backoff, so unplugging and replugging a board resumes capture with no restart.
A disconnected port's chip in the web UI has a reconnect button that skips the wait.

### More than one port

Several ports can be attached at once.
Commands that write to a board (`cmd`, `send`, `wait --send`, the bus commands) then need `-p <alias>`:

```bash
mcu -p board cmd ping
```

Commands that read span every port unless you pass `-p`.

## The web UI

Everything can be driven from the browser at `http://127.0.0.1:8558/ui/`.

- **Top bar**: one chip per port with its connection state, **+ Attach**, the session button and the settings gear.
- **Terminal**: live scrollback, with a filter per pane for port, channel and regex.
  - Panes can be paused, cleared, exported, and placed side by side (**+ pane**).
  - A command box sends monitor commands or raw lines, and a marker box drops an annotation.
- **CAN**: a table with one row per id showing the latest frame, its period and its age, grouped by port and bus. It exports to CSV.
- **Plots**: realtime charts for plot channels, and a digital/enum panel for bit traces and state bands.
  - Both share one time axis and cursor. Each chart exports to CSV.
- **Time and view controls**: the **host / tick / rel / delta** switch sets the timestamp base for panes and plots, and **CAN / Plots / Both** picks what the right side shows.
- **Settings** (gear icon): server address, storage and retention, update check, PlotJuggler, recorded sessions, access token, saved ports.
  - It writes the normal config file, which stays hand-editable.

## Reading the capture

Every line is stored with a timestamp, its port and a channel.
The channels are `debug` (your firmware's own output), `cmd`, `resp`, `event`, `marker` and `sys` (the daemon's notices).

```bash
mcu tail -f                                   # follow live
mcu tail -n 50 --chan debug                   # the last 50 debug lines
mcu lines --last-ms 2000                      # everything in the last two seconds
mcu lines --chan debug --match 'state:'       # filter by channel and regex
mcu lines --from 19:53:00 --to 19:54:30       # a clock-time window today
```

A filtered line looks like this:

```
23:56:22.911  debug| state: ARMED -> RUN
```

- `--match` takes a regular expression, on `lines`, `tail`, `wait`, `assert` and `log export`.
- A time window is `--last-ms N`, or `--from` and `--to` (`HH:MM[:SS[.mmm]]` today, or an ISO date-time), or `--session NAME`.
- `mcu lines` returns 100 rows unless you raise `--limit`, and says so when it cut the result.

### Decoded samples

Plot samples are stored in their wire form (`!ps <stream> <tick> <values>`).
`--decode` shows them as named values, and `--changes` keeps only the samples where a value changed:

```bash
mcu lines --last-ms 5000 --decode --changes
```

```
23:57:25.912  event| s1 state=IDLE
23:57:25.912  event| s2 gpio=led|pwm_en
```

`s1` and `s2` are stream numbers.
That turns a long capture into a list of state transitions.
`--names state,gpio` limits it to the fields you name.

### Export

```bash
mcu log export --last-ms 60000 -o run.log     # text
mcu log export --session boot-test --csv -o run.csv
mcu --json log export --last-ms 60000         # JSONL, one object per line
```

`log export` takes the same filters as `lines` and writes every matching row.

## Talking to the board

These need the [monitor module](FIRMWARE.md#3-the-monitor-module) in your firmware, except `send`, `mark`, `break` and `sysrq`.

```bash
mcu cmd ping                  # -> monitor 1 sim
mcu cmd 'i2c scan'            # -> 48 50
mcu cmd info                  # -> up=46146 can=2 rst=por fw=sim-0.1
```

`mcu cmd` sends one monitor command and prints the response.
It exits 1 on an error reply (`ERR 1 badcmd unknown bogus`) and 2 when no reply arrives within `--timeout` (1000 ms by default).

Each bus has its own subcommands, which build the command for you:

```bash
mcu i2c scan
mcu i2c rd 48 2                   # read 2 bytes from address 0x48
mcu i2c rd 50 4 --reg 00          # read 4 bytes starting at register 0x00
mcu i2c wr 50 00AABB
mcu spi xfer flash 9F000000       # full-duplex transfer on the chip-select named "flash"
mcu gpio set led 1
mcu gpio get led
mcu adc read vbat                 # -> raw=4106 mv=3309
```

Chip-select, pin and channel names are whatever your firmware's port file defines.
Addresses and data are hexadecimal.

Other things to send:

```bash
mcu send 'custom line'            # one raw line, with no wait for a response
mcu mark 'starting rail test'     # an annotation in the capture
mcu break                         # a serial break (hold TX low for 250 ms)
mcu sysrq t                       # Linux magic SysRq on a serial console: break, then one key
```

Use `send` for firmware with its own text console.
A monitor ignores a raw line, so use `cmd` for monitor commands.

## CAN

CAN needs the monitor module, with your CAN driver wired to it.

```bash
mcu can tx 123 AABB               # send id 0x123, data AA BB
mcu can tx 18A 0102 --ext         # 29-bit id
mcu can tx --bus 2 610 AABB       # second controller (bus 1 is the default)
mcu can stat                      # -> rx=614 tx=1 err=0 state=active
mcu can dump -n 20                # the 20 newest received frames
mcu can dump --id 100 -f          # follow one id live
mcu can dump --csv --last-ms 60000 -o can.csv
mcu can filter 100 700            # software receive filter: id 0x100, mask 0x700
mcu can filter all
```

Received frames look like this:

```
23:56:28.415  id=100 - dlc=4 data=00000217
23:56:28.415  bus=2 id=610 - dlc=4 data=000000A0
```

The id is hex.
The field after it is the flags: `x` extended, `r` remote, `-` neither.
Decoding to named signals from a DBC file is not supported yet.

## Plots

Plot channels come from `!p` lines or from typed streams; [What your firmware needs to send](FIRMWARE.md) covers both.

```bash
mcu plot channels                                         # every channel, with last value and age
mcu plot export --names tri,ramp --last-ms 60000 -o run.csv
mcu plot export --names tri,ramp --session boot-test --wide -o run.csv
```

The default CSV has one row per value.
`--wide` writes one row per sample with a column per channel.
`--decode` writes enum labels instead of numbers, and one column per bit lane.

### PlotJuggler

The daemon can mirror plot points live to [PlotJuggler](https://github.com/PlotJuggler/PlotJuggler) over UDP:

```bash
mcu pj on                         # stream to 127.0.0.1:9870
mcu pj on 192.168.1.20:9870       # or to another machine
mcu pj                            # show the current state
mcu pj off
```

`mcu pj` is short for `mcu plotjuggler`.
`--save` also writes the setting to the config file, and the same switch is in the web UI settings.

In PlotJuggler, add a "UDP Server" data source on that port with the JSON message protocol, and enable its message-timestamp option with the field `ts`.

## Sessions

A session names a span of the capture, so one run can be pulled back out of a long log.

```bash
mcu session start boot-test --note 'cold start, 3V3 rail'
# ... run the test ...
mcu session stop
mcu session list
mcu lines --session boot-test
mcu plot export --session boot-test --names tri -o run.csv
```

Starting and stopping a session adds marker lines to the capture, so the boundaries show in the terminal too.
The session button in the web UI does the same.

The daemon also records a session for each of its own runs, named `auto-<timestamp>`.
Naming a run replaces the automatic one, and a fresh automatic one starts when you stop.
An automatic session that captured nothing from a board is dropped from the list.

### Archive a run

```bash
mcu session export boot-test -o boot-test.db          # a standalone capture database
mcu session export boot-test --bundle -o run.zip      # database, lines as text, plot and CAN CSVs, manifest
```

The `.db` file has the same schema as the live capture.

### Delete data

Old data expires by age ([retention](CONFIGURATION.md#keep-captures-longer-or-cap-the-disk-they-use)).
To remove something sooner:

```bash
mcu purge --session junk-run --dry-run        # show how many lines would go
mcu purge --session junk-run --yes            # delete them
mcu purge --before-days 2 --yes               # or everything older than two days
mcu session delete junk-run                   # remove the label, keep the lines
```

A purge cannot be undone.
It also takes `--id-from` and `--id-to` for a range of line ids, or `--all`.
The web UI lists sessions under Settings with the same export and delete actions.

## Waiting and verdicts

`mcu wait` blocks until a line matches, or a timeout passes:

```bash
mcu wait --match 'state: .* -> RUN' --timeout 15000         # wait for the board to say something
mcu wait --match 'monitor 1' --send ping --timeout 2000     # send a command, then wait for a line
```

It prints the matching line and exits 0, or exits 2 on a timeout.

`mcu assert` judges a whole window and gives one pass or fail answer:

```bash
mcu assert --session boot-test --expect 'vbat=' --forbid 'PANIC'      # a stored run
mcu assert --last-ms 10000 --expect 'vbat=' --forbid 'PANIC'          # the last ten seconds
mcu assert --timeout 3000 --expect 'vbat=' --forbid 'PANIC'           # the next three seconds
mcu assert --send ping --expect 'monitor' --forbid 'PANIC' --timeout 2000
```

```
  ok      expect 'vbat=': vbat=25.21V iout=1.43A temp=41C
  ok      forbid 'PANIC': never seen
PASS  47 lines checked in 573 ms
```

- Every `--expect` must match at least once and no `--forbid` may match. Both can be repeated.
- Exit 0 is a pass and 1 is a fail.
- A window with no lines at all fails as `EMPTY`, because a `--forbid` over nothing proves nothing. `--allow-empty` accepts it.
- Both commands judge only lines the board sent. Your own command text, markers and daemon notices never match.

## Scripting

Every command takes `--json` and then prints machine-readable output:

```bash
mcu --json cmd 'i2c rd 48 2'
```

```json
{"status": "ok", "seq": 23, "data": "0643", "latency_ms": 5.3, "line_id": 9813}
```

Commands that return rows (`tail`, `log export`, `can dump`) print one JSON object per row.

Exit codes are the same for every command:

| Code | Meaning |
|---|---|
| 0 | success, a match, or a passing verdict |
| 1 | an error, bad usage, or a failing verdict |
| 2 | a timeout: the board did not answer, or a `wait` did not match |
| 3 | the daemon is not reachable |

`mcu` finds the daemon at `127.0.0.1:8558` unless told otherwise.
`--url` or `MCUSCOPE_URL` point it at another one; see [How mcu finds the daemon](CONFIGURATION.md#how-mcu-finds-the-daemon).

An AI agent uses the same commands.
`mcu ai-guide` prints a reference written for agents, and [CLAUDE_SNIPPET.md](CLAUDE_SNIPPET.md) is a block to paste into a project's `CLAUDE.md`.

## The simulator

`mcu daemon start --sim` runs a simulated MCU inside the daemon and attaches it as port `sim`.
It speaks the full protocol: debug lines, a CAN heartbeat, plot streams, and answers to `ping`, `i2c`, `spi`, `gpio` and `adc` commands.

The simulator also runs on its own, as a TCP listener that attaches like any device:

```bash
mcu-sim                                           # prints socket://127.0.0.1:9900
mcu attach socket://127.0.0.1:9900 --alias sim2
```

## Full reference

- `mcu --help` and `mcu <command> --help` list every option.
- [SPEC.md](SPEC.md) section 4 specifies the CLI, section 3 the REST and WebSocket API, and section 2 the wire protocol.
