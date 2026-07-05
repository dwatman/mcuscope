# mcu-interface

A hardware debug bridge that lets both humans and AI agents (Claude Code) interact with
STM32 (or any) microcontrollers over a serial link: send CAN/I2C/SPI/GPIO/ADC commands,
stream and query debug output, and run send-and-wait-for-response interactions with
timeouts.

## Architecture

```
MCU firmware "monitor" module          PC (Linux Mint)
+--------------------------+   UART   +-------------------------+      +- mcu CLI (human + AI)
| cmd parser, CAN / I2C /  +----------+ hwbridged daemon:       +------+- web UI: terminal, setup,
| SPI / GPIO / ADC proxies |          | owns serial port,       | REST |  CAN view, realtime plots
| + normal debug printf    |          | timestamps all traffic  | + WS +- pytest HIL tests (later)
| + !p plot data points    |          | into SQLite, serves UI  |      +- MCP wrapper (later)
+--------------------------+          +-------------------------+
```

Key ideas:

- The daemon (`hwbridged`) is the **sole owner of the serial port**. Everyone else
  (the `mcu` CLI, a live tail, tests, Claude) is a client over a local REST/WebSocket
  API. No more "port busy", and the log exists even when no client is attached.
- The wire protocol is **line-oriented text** sharing the UART with normal debug
  prints. Machine traffic is tagged with leading characters (`>` `<` `!`); everything
  else is treated as debug output. Sequence numbers correlate commands with responses.
- All traffic is timestamped and stored in **SQLite**, so "the last 20 CAN frames with
  id 0x1A3" or "debug lines matching X in the past 2 seconds" are cheap queries.
- The **primary AI interface is the `mcu` CLI** with a `--json` flag and meaningful
  exit codes. It works identically for the human and the agent.

## Repository layout

```
docs/SPEC.md                 Full system specification (protocol, API, schema, firmware contract)
docs/IMPLEMENTATION_PLAN.md  Phased plan with acceptance criteria (for implementation by Opus)
host/                        Python package: hwbridged daemon + mcu CLI
firmware/                    Portable C monitor module + port shim template
tools/                       MCU simulator (pty-based) for hardware-free development and tests
```

## Status

Design complete (see docs/). Implementation not yet started.
