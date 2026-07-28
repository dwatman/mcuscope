# Changelog

All notable changes to MCUscope are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the major version is 0, the interfaces in `docs/SPEC.md` (wire protocol, REST API, CLI exit codes) may still change between minor releases.

## [Unreleased]

## [0.1.1] - 2026-07-28

### Added

- Firmware markers (SPEC 2.5): `!m [@<tick>] <text>` lets the MCU annotate the timeline itself; a well-formed marker is stored on the `marker` channel alongside `mcu mark` and session boundaries. Firmware calls `monitor_mark("calibration start")`, or just `printf("!m boot done\n")` with no library at all.
- Scientific notation in plot values and `*<scale>` factors (SPEC 2.5), so float `printf("%g")` output such as `1.2e-05` is plotted instead of silently dropped, and `*9.8e-4` reads better than `*0.00098`.
- Simulator: a `mark <text>` command, so the marker path is exercisable end to end with no hardware.
- `mcuscoped --version` and `mcu --version` report which Python interpreter is running, and any startup crash is also written to a `mcuscoped-crash.log` in the data directory, so a failing install can always be diagnosed.

### Fixed

- Windows: `mcuscoped` exited 1 with no output at all when run under a Python whose standard streams are null - notably KiCad's bundled interpreter, which `uv tool install` can select from `PATH`. Null streams are now reattached to the console (`CONOUT$`) at startup, and uvicorn's colour autodetection (the crash site) is bypassed with an explicit `use_colors=False`.
- An automatic session whose only device traffic was a firmware marker is no longer dropped as empty when it closes.

## [0.1.0] - 2026-07-28

First public release.

- `mcuscoped` daemon: owns the serial port, timestamps and stores every line in SQLite, and serves a REST + WebSocket API on `127.0.0.1:8765`. Capture continues with no client attached, and an OS-level lock enforces one daemon per capture database.
- `mcu` CLI: the primary human and AI interface over that API, with `--json` output everywhere and a stable exit-code contract (0 success/match, 1 error, 2 timeout, 3 daemon unreachable).
- `mcu wait` and `mcu assert`: block on a pattern, or judge a whole capture window (multiple `--expect`/`--forbid` conditions, live or retrospective) with a pass/fail exit code, so agents and CI can branch on results instead of reading logs.
- Sessions: name a span of the capture, list, export as a standalone SQLite database, and delete (label alone or with its data). The daemon opens an automatic session per run; retention keeps the newest N sessions regardless of age, with an optional size cap.
- Web UI: multi-pane terminal, port setup, decoded CAN view, realtime analog plots, and a combined digital/enum panel sharing one time base and cursor; settings page edits the full config (bind address, storage, saved ports) with the TOML file staying hand-editable.
- LAN access with an optional access token (`MCUSCOPED_TOKEN` / `--token`), rate-limited against brute force; loopback clients stay friction-free.
- Portable C firmware monitor module (`firmware/monitor/`) implementing the command/event protocol, with host-compiled tests and an integration guide.
- Hardware-free simulator (`mcu-sim`, or in-process via `mcuscoped --sim --open`): fake I2C, SPI, GPIO, ADC and a CAN heartbeat, so the full stack runs and is tested with no board attached.
- Cross-platform: Linux and Windows 10/11, `COMx`, `/dev/tty*` and `socket://host:port` device strings.

[Unreleased]: https://github.com/dwatman/mcuscope/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/dwatman/mcuscope/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/dwatman/mcuscope/releases/tag/v0.1.0
