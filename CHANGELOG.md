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
- One-command demo: `mcuscoped --sim` runs the bundled simulator in-process and autoconnects to it; `--open` launches the web UI in the browser, and the daemon always prints the UI URL at startup. The simulator moved into the package (`mcuscope.sim`, console script `mcu-sim`; `tools/mcu_sim.py` remains as a source-checkout shim).
- Brute-force protection for the access token: wrong-token attempts are rate limited per client address (10 failures / 60 s window locks the address out for 60 s with HTTP 429 / WS close 1013); missing-token requests never count, and a correct token clears the record.
- `POST /ports/{alias}/reconnect` plus a reconnect button on disconnected port chips in the UI, to skip the reconnect backoff after replugging a device.
- Web UI efficiency pass: all timers idle while the tab is hidden (with an immediate refresh on return), the CAN table updates cells in place instead of rebuilding its DOM every tick, the shared plot cursor re-projects only when something moved, terminal hover hit-testing is coalesced to one per frame, and the CAN row set is capped at 256 ids with least-recently-seen eviction.
- Web UI usability: Settings gains an access-token field (recovers from a cancelled token prompt without a reload), the CAN table gets CSV export, `/cmd` requests carry a client-side abort timeout so the result strip can never hang on a dead daemon, and the marker/timeout controls wrap instead of disappearing on narrow screens.
- `mcu daemon start` forwards `--config`/`--sim` to the daemon and passes the CLI token to it via the environment.
- Fixes from a review pass: the token guard's static-file exemption now matches only `/ui` paths exactly, `/wait` no longer misses a line landing exactly at subscribe time, detaching a port mid-command returns a clean error envelope instead of an internal cancellation, store shutdown is time-bounded even with a wedged writer, and sys-row tasks can no longer be spawned after port teardown.
- Capture-throughput pass, measured against a line blaster on loopback. Peak sustained ingest went from about 950 lines/s (at 142% CPU, i.e. saturated) to over 40,000 lines/s, and 500 lines/s dropped from 69% CPU to about 7%:
  - The reader thread no longer reads `socket://` ports one byte per syscall. pyserial implements `in_waiting` as a 0/1 readability poll on its URL handlers, so the sized drain read a single byte at a time and posted a loop callback per two bytes; those transports now drain with a zero timeout instead (0.2 MB/s -> 600 MB/s in isolation).
  - Received lines are stored a burst at a time rather than one await per line, which restored the store writer's batching: a burst is now one commit instead of one commit (and one event-loop wakeup) per line.
  - The writer inserts each batch with one `executemany` per table, assigning `lines.id` itself; a batch that fails falls back to row-at-a-time inserts so a single bad row still cannot take its neighbours down.
  - Line assembly splits each burst in one pass instead of slicing lines off the front of the buffer (which was quadratic in burst size), and the loop-side rx queue is a deque rather than an `asyncio.Queue`.
- `GET /ws` now sends a **JSON array** of rows per message, coalescing queued rows into one frame (SPEC 3.4). Clients must iterate the array; `mcu tail -f` and the web UI still accept a bare object so they work against an older daemon. At 10,000 lines/s an attached subscriber costs 5% CPU instead of 25%.
- `mcu` starts about 25% faster: `asyncio`, `websockets` and `platformdirs` are imported by the two commands that need them instead of on every invocation.
- `mcu daemon stop` no longer signals a pid that no live daemon answers for, so a pid file left by a crashed daemon cannot kill an unrelated recycled process; the stale file is removed and reported instead.
- The web UI trims its shared line buffer in blocks instead of shifting one row per line, and the firmware monitor now proves at compile time that a worst-case `!ps` plot line fits its output buffer.
- `rx_dropped` (lines shed because storage could not keep up) is now reported on `GET /status`, in `mcu status`, and as a warning on the port chip in the web UI. It was counted but never surfaced, so a capture with holes looked clean.
- `mcu tail -f` and `mcu can dump -f` flush each line, so piping a follow stream (`mcu tail -f --json | jq`, or an agent reading it) shows output as it arrives instead of stalling until Python's 8 KB pipe buffer fills.

[Unreleased]: https://github.com/dwatman/mcuscope/commits/main
