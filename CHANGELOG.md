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

[Unreleased]: https://github.com/dwatman/mcuscope/commits/main
