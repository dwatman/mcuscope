# What your firmware needs to send

MCUscope works with firmware that already prints text over a UART, with no changes.
Two optional steps add plots and then two-way commands.
Each step builds on the one before, and you can stop at any of them.

| Level | Change to your firmware | What you get |
|---|---|---|
| 1. Plain text | none | timestamped capture, live terminal with filters, regex search, sessions, `mcu wait`, `mcu assert`, export |
| 2. Plot and marker lines | one `printf` format | realtime plots, plot export, PlotJuggler streaming, markers on the timeline |
| 3. Monitor module | add three C files and a small port file | commands and responses, CAN/I2C/SPI/GPIO/ADC access, the CAN table, typed plot streams, digital and enum lanes |

## 1. Plain text

Keep your existing `printf` and change nothing:

```c
printf("boot ok, vbat=3.72V\n");
```

Every line is timestamped and stored as debug output.
The daemon sends a `ping` line when it connects, and a firmware with its own text console will see it.
Set `identify = false` on the port to stop that (see [Configuration](CONFIGURATION.md#reattach-a-board-at-every-start)).

One rule: a debug line must not begin with `<` or `!`.
Those first characters mark protocol lines (`<` a response, `!` an event).

## 2. Plots and markers from `printf`

### Plot lines

A line in this format becomes a point on a live chart:

```c
printf("!p %lu temp=%d.%02d rpm=%d\n", tick_ms, whole, frac, rpm);
```

The format is `!p <tick> <name>=<value> ...`.

- `<tick>` is any millisecond counter.
- Each `<name>` becomes a plot channel.
  - A name is up to 16 characters: letters, digits, `_` and `.`, not starting with a digit.
- Each `<value>` is an integer (`1200`), a fixed-point number (`-12.34`) or scientific notation (`1.2e-05`).

Printing the fraction as a second integer, as above, avoids float `printf`.
If your firmware does have float `printf`, `%g` output is accepted as it is.

### Markers

A marker annotates the timeline and draws as a full-width divider in the terminal:

```c
printf("!m @%lu calibration start\n", tick_ms);
printf("!m boot done\n");                        // no timebase: leave the tick out
```

The tick is optional and carries a literal `@`, so marker text that starts with a number (`!m 12 cells balanced`) keeps its first word.
`mcu mark 'text'` and the marker box in the web UI add markers from the host side.

## 3. The monitor module

Levels 1 and 2 are one-way: the firmware talks and the host listens.
The monitor module lets the host ask the firmware for something.

What it adds:

- **Commands and responses**: `mcu cmd 'i2c rd 48 2'` returns the reply, with sequence numbers, error codes and timeouts.
- **Bus access**: built-in `can`, `i2c`, `spi`, `gpio` and `adc` commands, calling drivers you supply.
- **CAN frames** in the web UI's CAN table, from up to nine controllers.
- **Typed plot streams**: packed binary samples that carry real floats with no float `printf`, and declare their units and scaling once.
- **Digital and enum lanes**: logic-analyser style bit traces and labelled state bands on the plot time base.
- **Your own commands**: register a handler and call it with `mcu cmd`.

What it costs:

- Three portable C99 files (`monitor.h`, `monitor.c`, `monitor_cmds.c`), with no dynamic allocation and no HAL dependency.
- A port file you write, with three callbacks: UART read, UART write and a millisecond tick.
- About 4.4 KB of flash and 1 KB of RAM on a Cortex-M0+ at `-Os`. The [footprint table](../firmware/monitor/INTEGRATION.md#7-footprint) has the details.

Your existing `printf` output keeps working alongside it on the same UART.

The files are in [`firmware/monitor/`](../firmware/monitor/) in the repository.
They are not part of the PyPI package: copy the directory into your project.
The [integration guide](../firmware/monitor/INTEGRATION.md) walks through it step by step.

## Protocol reference

[SPEC.md](SPEC.md) section 2 defines every line format, and section 5 the monitor module's contract.
