# Changelog

All notable changes to MCUscope are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project intends to adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once published.

## [Unreleased]

Phases 0-7 of the implementation plan are complete (see `docs/IMPLEMENTATION_PLAN.md` for the live tracker); nothing has been published to PyPI yet.

### Added

- `mcuscoped` daemon: owns the serial link, timestamps and stores every line in SQLite, serves a REST + WebSocket API on `127.0.0.1:8765`.
- `mcu` CLI: the primary human and AI interface over that API, with `--json` output and a stable exit-code contract (0 success/match, 1 error, 2 timeout, 3 daemon unreachable).
- Web UI: multi-pane terminal, port setup, decoded CAN view, realtime analog plots, and a combined digital/enum panel (logic-analyser bit traces plus FPGA-style state bands) sharing one time base and cursor.
- Portable C firmware monitor module (`firmware/monitor/`) implementing the command/event protocol, with host-compiled tests and an integration guide.
- Hardware-free simulator (`tools/mcu_sim.py`) and a manual web UI smoke harness (`tools/webui_smoke.py`), so the full stack runs and is tested with no board attached.
- A security hardening pass over the daemon and web UI (input validation, bounded regex matching to avoid ReDoS, review-driven fixes).
- LAN access with an optional access token (`mcuscoped --token`, env `MCUSCOPED_TOKEN`): non-loopback clients must present it via `Authorization: Bearer`, `X-Auth-Token`, or `?token=` on the WebSocket; loopback clients stay friction-free. `mcu --token` / env `MCUSCOPE_TOKEN` on the client side; the web UI prompts and remembers the token. The token is runtime-only and deliberately not a config-file key.
- Config write-back API and web UI settings page (SPEC 3.3.1): the whole setup (bind address, storage, saved ports) is editable from the browser starting from an empty config, while the TOML file stays hand-editable (tomlkit round-trip preserves comments). Saves are atomic read-modify-write; `retention_days` applies live, bind/db changes report `restart_required`; config edits from the network require a token. `mcuscoped --config PATH` / env `MCUSCOPED_CONFIG` selects an alternate config file.
- Reliability hardening from a full-codebase review: the SQLite writer survives commit failures instead of deadlocking ingestion, bounded RX/write queues with drop-oldest accounting, pending commands fail fast on port disconnect, WebSocket subscriber leak fixed, `/wait` evaluates line bursts in batches so matches are not lost under load, heavy reads moved off the event loop, and one bad autoconnect port no longer aborts startup.
- Input bounds on device-controlled integers (response seq, CAN ids per 11/29-bit ranges) and on client timeouts; outgoing lines reject embedded newlines and non-ASCII instead of mangling them silently.
- CLI: `mcu daemon status` handles non-mcuscoped servers per the exit-code contract, corrupt pid files are cleaned up, Windows device paths derive sane aliases.
- Web UI: fixed the analog-chart color picker, added WebSocket reconnect backoff and channel/lane caps.

[Unreleased]: https://github.com/dwatman/mcuscope/commits/main
