# Registry leg, classes 43-56 (2026-09-24)

HEAD at start: `f31ecd9` (matched the brief).
HEAD moved mid-run to `41cff9a` ("registry leg brief, classes 57-70 verdict lists"): docs only, no source change, so every verdict below holds for both.
Scratch: `~/tt-data/mcuscope-2026-09-24/registry-leg/43-56/` (probes, floor venv, logs, v0.3.0/v0.4.0 daemon copies).

Tally: 14 classes swept; 13 findings (0 HIGH, 2 MEDIUM, 11 LOW); 9 driven, 4 reasoned.

## Findings

- **R53-1 MEDIUM** `host/mcuscope/cli.py:2126`, `:2137`
  - `mcu can dump --last-ms N` now sends `since_ts` (converted by `_absolute_window`, commit 0fc920f, unreleased); `/can/frames` declares `since_ts` only from 0.4.0, and `can dump` gates only `--from/--to` and `--csv`.
  - 0.4.0's CLI sent `last_ms` (declared since 0.1.0), so this is a regression in unreleased code against 0.1.x-0.3.x daemons.
  - Driven: HEAD `mcu --url <v0.3.0 --sim daemon> --json can dump --last-ms 1000 -n 1000` returned 280 frames (the whole capture) at exit 0; the daemon's own `/can/frames?last_ms=1000` returned 21.
  - Same command, ungated: `--bus` (declared from 0.3.0) against 0.1.x/0.2.x shows every bus (reasoned).
- **R53-3 MEDIUM** `host/mcuscope/cli.py:1477-1496`, `host/mcuscope/cli_client.py:35`
  - SPEC 4 `mcu assert`: "a window that held no such line answers status empty, exit 1". The daemon implements that only at HEAD (0.5.0), but `DAEMON_MIN_VERSION` is 0.4.0 and `mcu assert` gates only `--eol`.
  - Driven: HEAD `mcu -p typo assert --forbid ERR --last-ms 60000` against a v0.4.0 `--sim` daemon printed `PASS  0 lines checked`, exit 0.
  - `allow_empty` is a HEAD-only body field, so the `DAEMON_MIN_VERSION` comment ("declaring every ... body field the CLI sends") is stale.
  - Fix options: judge `checked_lines == 0` with `status == "pass"` and no `--allow-empty` as empty client-side, or gate `assert` on 0.5.0.
  - This is class 53's mirror for a behaviour rather than a field: the client relies on a guarantee only a newer peer provides. It is not caught by the class 53 sweep as written (see class 53 below).
- **R44-1 LOW** `host/mcuscope/cli.py:751`
  - `_absolute_window` anchors `--last-ms` (no `--session`) on the client's `time.time()`. The daemon's own comment at `server.py:1066` says a client measuring against its own clock is off by the skew. With `--url` to a daemon whose clock differs, the window shifts by the skew.
  - Affects `mcu lines`, `mcu log export`, `mcu can dump`.
  - Driven: `probe_r44_skew.py` (in scratch), client clock +300 s. `mcu lines --last-ms 60000` returned 0 rows where the daemon's `/lines?last_ms=60000` returned 5.
  - Fix: anchor on `/status` `now`.
- **R45-1 LOW** `host/mcuscope/config.py:661-684` (`save_ports`)
  - `PUT /config/ports` rebuilds every `[[ports]]` table from `PortConfig`, so a per-port key the model does not carry is dropped on any UI ports save.
  - SPEC 3.3 (SPEC.md:524) says the write-back preserves unknown keys "so a file written by a newer version still round-trips". The SPEC.md:562 exception names only comments.
  - Driven: a config with `future_port_key` in a port and `future_server_key` in `[server]`. `save_ports(load_config(p).ports)` dropped the port key; `save_server` kept the server key.
- **R43-1 LOW** `host/pyproject.toml` dev `ruff>=0.5`
  - ruff 0.5.0 through 0.12.12 report 5 `UP038` errors (`cli.py:751`, `cli.py:1406`, `cli_daemonctl.py:177`, `lockfile.py:75`, `tests/test_plotjuggler.py:409`). 0.13.0 is the first clean version.
  - Driven: `ruff check . --no-cache` at 0.5.0, 0.8.0, 0.10.0, 0.12.0, 0.12.4, 0.12.8, 0.12.12 (fail), 0.13.0, 0.14.0 (pass).
- **R43-2 LOW** `host/tests/test_cli_sessions.py:74` against `httpx>=0.27`
  - `test_a_page_without_the_name_falls_back_to_the_quoted_path[bundle0/1]` asserts `name=run+1...`; httpx 0.27.x encodes the space as `%20`. The daemon decodes both, so the product is unaffected: the test pins the transport's encoding.
  - Driven: fails on httpx 0.27.0 and 0.27.2, passes on 0.28.0.
- **R43-3 LOW** `host/pyproject.toml` `regex>=2024.0`
  - `regex` refuses `\z` before 2026.2.19 (bisected: 2026.1.15 refuses, 2026.2.19 accepts). `tests/test_pane_regex_dialect.py::test_a_refused_pattern_reads_here_as_the_fixture_records[ERR\\z]` fails at the floor (2024.4.16).
  - Product effect at the floor: a `\z` pattern is a loud 400 rather than accepted; the web UI already refuses it.
  - Driven (compile probe per version, then the test file at 2026.2.19: 82 passed).
- **R48-1 LOW** `firmware/monitor/monitor_cmds.c:101-111` (`cmd_ping`), same shape at `:223-249` (`cmd_can_stat` `state`)
  - A variable-length payload (port `name`, shim `state`) is bounded by `resp_max` (256, the local buffer), not `MON_OK_PAYLOAD_MAX`.
  - Driven: `c48/probe_ping.c` (host gcc, fake shims). A 236-239 char name answers `<1 OK monitor 1 ...` at seq 1 and `<65535 ERR 8 overflow` at seq 65535.
  - Latent: `monitor.h:82` documents `name` as a "short project id". `can_stat` is reasoned by symmetry.
- **R51-1 LOW** `host/mcuscope/webui/terminal.js:494-512` (`loadHistory`)
  - After `HISTORY_HOPS` (5) pages that the pane's filter empties, the walk neither advances nor ends. `historyDone` stays false and the hint reads "scroll to the top for older lines" while the view is at the top, so the pane stalls until the user scrolls away and back.
  - It is reachable when the daemon refuses `match` (budget exceeded or dialect), because `terminal.js:524-530` then refetches unfiltered and filters locally.
  - The registry's sweep text accepts "under a hop cap", so whether this residual complies is an owner call.
  - Reasoned only (no browser drive).
- **R53-2 LOW** `host/mcuscope/cli.py:747`, `:813`, `:849`
  - `id_to` (declared from 0.2.0) is sent ungated by the `--session --last-ms` anchor lookup and by the `--decode` definition priming.
  - Against 0.1.x the anchor becomes the newest row, so the window slides past the session end and returns empty at exit 0. The 0.1.x daemon itself also answered this combination empty. Priming uses the newest definitions, which mislabel only after a `!pd` redefinition.
  - Reasoned.
- **R54-1 LOW** `host/mcuscope/cli.py:2127-2141` vs `:2182-2190` (and the `",".join(can_id)` at `:2133` and `:2158`)
  - The CAN filter set (`port`, `session`, `id`, `bus`) is built twice, in `can_dump` and `_dump_follow`, with no shared helper. It is identical today. Latent, reasoned.
- **R54-2 LOW** `host/mcuscope/webui/terminal.js:518-522` vs `:621-628`
  - The pane filter set (`port`, repeated `chan`, `match` gated on `pane.regex`) is built twice, in history paging and the pane export. This is the class's own bit pair: it was made consistent but never shared. Latent, reasoned.

## Class 43: declared dependency floor

- Method: copied the tree to scratch (`src/`) and ran `uv venv --python 3.10 low && uv pip install --resolution lowest-direct -e '.[dev]'` in scratch, not `/tmp`.
  - Resolved: pyserial 3.5, fastapi 0.115.7, pydantic 2.0.2, uvicorn 0.35.0, typer 0.26.0, httpx 0.27.0, platformdirs 4.0.0, websockets 14.0, tomlkit 0.12.0, regex 2024.4.16, pytest 8.0.0, pytest-asyncio 0.23.5, starlette 0.44.0, pytest-timeout 2.3.1, pytest-cov 5.0.0, pytest-randomly 3.15.0, ruff 0.5.0.
- **Contradiction, reported rather than worked around.** The registry says "then the full suite there"; the brief says "No whole test suites: run single test files only".
  - Ran every `tests/test_*.py` one file at a time, sequentially, in the floor venv (`run-floor.sh`). That is 117 of 119 files: 2625 passed, 1 skipped, 3 failed (R43-2, R43-3).
  - Skipped: `test_webui_js.py` (node, no Python floor involved) and `test_firmware_monitor.py` (gcc).
- Ruff at the floor: see R43-1. The sweep text omits lint; lint is a CI gate (`ci.yml:81`).
- pytest-cov 5.0 with `--cov` on one file: works.
- Per floor:
  - Every runtime floor imports and passes: typer 0.26 (`import mcuscope.cli` OK), uvicorn 0.35, fastapi 0.115.7, pydantic 2.0.2, httpx 0.27 (product), websockets 14, tomlkit 0.12, platformdirs 4.0, pyserial 3.5.
  - Violate: `regex` (R43-3), `ruff` (R43-1), `httpx` (test only, R43-2).
  - Exempt: `hatchling` (build-system, no floor claimed).
- `except ImportError` sweep: 1 site. Also grepped `ModuleNotFoundError|importlib|__import__`, which found 2 more that are comments only.
  - `server.py:166` complies: it logs a warning before returning.
  - Driven at uvicorn 0.35.0: `_enable_ws_backpressure()` wired `pause_writing`, with no warning.
  - `update_check.py:47` and `cli_client.py:32` set `sys.modules["httpx._main"] = None`. They rely on httpx's own `except ImportError` around `from ._main import main`. Verified present at httpx 0.27.0 (`httpx/__init__.py:47-49`) and at the dev venv's version. Complies.
- Sweep imprecision: the command uses `/tmp/low`; the brief forbids `/tmp`. Improved: `uv venv --python 3.10 <scratch>/low && VIRTUAL_ENV=<scratch>/low uv pip install --resolution lowest-direct -e '.[dev]'`, then the suite, then `<scratch>/low/bin/ruff check .`.

## Class 44: relative bound re-evaluated per page

Method: grep `id_to|since_id|LINES_PAGE` over `cli*.py` and `webui/*.js` (60 lines), then read every multi-request loop and the server handlers calling `_resolve_window`. Walk sites: 17.

- `cli.py:632` `_fetch_after`: carries `since_ts` (absolute) and `until_ts`/`id_to`; `last_ms` never sent (every `_lines_params` caller passes None). Complies.
- `cli.py:661` `_fetch_newest` (lines, tail snapshot, log export `--limit`, can dump `-n`): same. Complies.
- `cli.py:711` `_iter_pages_asc` (log export, decode priming): `since_id` walk under a fixed `id_to`. Complies.
- `cli.py:2271` `_poll_new_frames`: `since_id` fixed per poll, `id_to` walks down. Complies.
- `cli.py:691` `_pin_ceiling`: one request, before the walk. Complies.
- `cli.py:724` `_absolute_window`: fixed once, before the walk, but on the client's clock. Violates (R44-1).
- `cli.py:2448` plot export: one request carrying `last_ms`; the server twin below covers it. Complies.
- `webui/api.js:281` `seedPlotDefs`: `since_id` walk under a fixed `id_to`. Complies.
- `webui/api.js:486` `fetchSince`: fixed `since_id` watermark, `id_to` walks. Complies.
- `webui/api.js:387` `seedPlotHistory`: several requests with `last_ms`, each pinned by `id_to=anchor.id`, so the daemon anchors every one at one row. Complies.
- `webui/terminal.js:496/515` history paging: `id_to` walks, `since_id` fixed at `clearId`, no time bound. Complies.
- Server `_resolve_window` callers resolve `last_ms` to `floor_ts` once: `/lines` (`server.py:1858`), `/lines/export` (`:1894`, freeze), `/can/frames` (`:1950`), `/plot/export` (`:2086`, count then stream share `win.scope`), retrospective `/assert` (`:3097`, one window for every pattern). All comply.
- `/plot/series` (`server.py:2039`): one store call, one SQL. Complies.
- Sweep imprecision: add "a now taken from the client's clock, even once, where the daemon's clock anchors the bound", which is R44-1's shape.

## Class 45: whole-collection PUT drops unrendered fields

Method: `grep put_config_|@app.put` in `server.py` (6 PUT handlers), the client savers in `settings.js` and `cli.py`, and the file-level write-back each handler calls.

- `PUT /config/ports`: GET returns 7 fields per port. `collectPorts` (`settings.js:604`) sends all 7; `device`/`serial_number` are omitted only when empty, matching GET's null. `eol`/`identify` omitted means keep saved. Complies at the wire.
  - `save_ports` drops unknown per-port TOML keys: violates (R45-1).
- `PUT /config/server`: `saveServer` sends host and port, which is every GET field. Complies.
- `PUT /config/storage`: `saveStorage` sends all 5 GET fields. Complies.
- `PUT /config/update`: sends `check`. Complies.
- `PUT /config/plotjuggler`: `savePjDefault` (`settings.js:268`) and `cli.py:280` send both fields. Complies.
- `PUT /plotjuggler`: runtime, not a collection; an omitted `dest` means keep (`settings.js:241`, `cli.py:278`). Complies.
- Sweep imprecision: the sweep stops at the wire. Extend it to the save function behind each PUT: every key the file can hold under the collection, not only every key the model has.

## Class 46: CLI reads a young field without tolerating its absence

- Method: AST over `cli*.py` for every `x["key"]` load (135 sites) plus every `_field`/`_list_field` call (22).
  - Each key was checked against the v0.1.0 daemon sources (`git show v0.1.0:host/mcuscope/*.py`, the CLI excluded), with the origin of each non-trivial key spot-read in v0.1.0 (`serial_link.py:751-756` port snapshot, `server.py:1422-1435` assert verdict, `store.py:680` session `lines`, `server.py:1073-1084` channels).
  - The supported floor is 0.1.0: the CLI talks to older daemons and gates per option (`require_daemon`).
- Key absent from v0.1.0 (21):
  - `cli.py:185` `upd["latest"]`: only after `upd.get("available")`, and both came in one block. Complies.
  - `cli.py:197` `pj["dest"]`: only after `pj.get("enabled")`, same block. Complies.
  - `cli.py:227` `pt["write_failing_since"]` and `:228` `pt["write_failures"]`: guarded by `.get`. Complies.
  - `cli.py:234` `pt["target"]`: guarded by `.get`. Complies.
  - `cli.py:282` and `:287`-`:289` `body["enabled"]`/`body["dest"]`: response of `PUT /plotjuggler` (0.3.0 route); an older daemon 404s into `Client.fail`. Complies.
  - `cli.py:701` `params["until_ts"]`: local dict. Exempt.
  - `cli.py:1381` and `:1501` `res["dropped"]`: guarded by `.get`. Complies.
  - `cli.py:1388` and `:1411` `res["sends"]`: guarded by `"sends" in res`. Complies.
  - `cli.py:1228` `row["gap"]`: guarded by `"gap" in row`. Complies.
  - `cli_output.py:331` (`y`, `mo`, `d`) and `:335` (`mi`, `s`): regex group dict, local. Exempt.
- Key present in v0.1.0 (114): all comply. Local dicts (`params`, `page_params`, the `g` regex groups) are exempt.
  - `cli.py` lines: 1024, 213, 221, 867, 969, 1028, 1399, 1631, 1749, 2195, 2436, 807, 837, 975, 1022, 1391, 1511, 1517, 1522, 1583, 1952, 2150, 165, 166, 311, 679, 816, 967, 1392, 1529, 1581, 1671, 2155, 2283, 2429, 2832 (x2), 165, 192 (x2), 204, 206 (x2), 207 (x2), 251 (x2), 311, 423, 576, 855, 856, 963, 1508, 1526, 1549 (x2), 1561 (x2), 1562 (x2), 1583, 1585 (x2), 1586, 1669 (x2), 1675 (x2), 1757, 1764, 1952, 2429, 2430, 2432, 2438, 2440, 2834 (x2), 193, 408, 975, 1110, 1527, 1585, 1654, 1747, 2256, 397, 747, 1233, 1234, 1512, 1515, 1518, 1521, 1528, 1654, 1232, 1236, 1513, 1519.
  - Other files: `cli_client.py:211`, `cli_daemonctl.py:177` (x2), `cli_daemonctl.py:192`, `cli_output.py:473` (x2), `:474` (x2), `:335`, `:468` (x2), `:336`.
- `_field`/`_list_field` (22): `update`, `session`, `plotjuggler` at `cli.py:178/188/195` are `optional=True`. Every other key (`ports`, `devices`, `port`, `lines`, `sessions`, `expect`, `forbid`, `line`, `session` on a POST answer, `channels`, `frames`) is a v0.1.0 field. Complies.
- Web UI: exempt, because it is served by the daemon it talks to.
- Sweep imprecision: the `["<field>"]` grep misses `_field(body, "<field>")` without `optional=True`, which dies with exit 1 on an absent young field. Add `grep -n '_field(' cli*.py`.

## Class 47: live-only surface accepts an unknown scope

Method: AST over every route in `server.py` (38 routes) for a `port` query argument, a body model with `port`, or an `{alias}` path. 16 handlers take a port scope.

- Live-only or write, refused through `ports.get`/`_resolve_port`: all comply.
  - `DELETE /ports/{alias}` (`detach` answers 400).
  - `/ports/{alias}/reconnect` and `/ports/{alias}/disconnect`.
  - `/send`, `/break`, `/cmd`.
  - `/wait` (`server.py:2609`).
  - live `/assert` (`:3130`).
  - `/ws` (`:2192`, close 1008).
- Retrospective, refused through `_unknown_port`: all comply.
  - `/lines`, `/lines/export`, `/can/frames`.
  - `/plot/channels`, `/plot/series`, `/plot/export`.
  - retrospective `/assert` (`:3090`).
- `/marker`: a write, but refused through `_unknown_port` (attached or stored). Exempt: SPEC 3.5 (SPEC.md:853) rules "as a read's port must". It writes the capture, not a port.
- Remaining 22 routes take no port scope.
  - `POST /ports` creates an alias, and `PUT /config/server`'s `port` is a TCP port.
  - `PUT /config/ports` holds config aliases.
  - `/status`, `/ports`, `/devices`, `/config`, `/plotjuggler`, `/sessions*`, `/purge`, `/shutdown` and `/` mention "port" only in text.
  - All exempt.

## Class 48: length budget against the local buffer

Method: `grep -n resp_max firmware/monitor/*.c firmware/monitor/*.h` (48 lines), read every arithmetic use and every `mon_buf_init(&b, resp, resp_max)` writer.

- `monitor_cmds.c:90` `read_into_resp`: clamps to `MON_OK_PAYLOAD_MAX + 1`. Complies.
- `monitor_cmds.c:266-272` `cmd_i2c_scan`: clamps to `MON_OK_PAYLOAD_MAX + 1`. Complies.
- `cmd_ping` (`:106`): variable `name`. Violates (R48-1).
- `cmd_can_stat` (`:239`): variable shim `state`. Violates (R48-1, reasoned).
- `cmd_info` (`:119`): `up=` plus `can=` (under 25 bytes) plus `extra` (at most 63). Bounded at 88, below 245. Complies.
- `cmd_gpio_get` (`:412`, 1 byte) and `cmd_adc_read` (`:433`, at most 30 bytes): bounded. Comply.
- `(void)resp_max` handlers (`cmd_nosup`, `can_tx`, `can_filter`, `i2c_wr`, `gpio_set`), signatures, and `monitor_dispatch` pass-through (`:531-551`): no arithmetic. Exempt.
- `monitor.c:311` `emit_ok`: refuses whole on `b.over` (the wire check itself). `monitor.c:672` is an event body, not a response. Exempt.
- Sweep imprecision: the `resp_max` arithmetic grep cannot see a writer that trusts `mon_buf` to bound it. Add `grep -n 'mon_buf_init(&b, resp, resp_max)' firmware/monitor/*.c` and bound each payload against `MON_OK_PAYLOAD_MAX`.

## Class 49: streamed export left partial

Method: `grep -n 'open(' cli*.py` plus `_OutFile(`, `remove_partial`, `.download(`. 6 writer sites.

- `cli_client.py:254` `Client.download`: `started` is set after the open succeeds, and the `finally` removes the file unless `ok`. Complies.
- `cli.py:1803` `_OutFile`: opened at the first write, `discard` removes only a file it opened.
  - Used by `_stream_export` (`cli.py:1862`, with `ok` and a `finally`). Complies.
  - Used by paged log export (`cli.py:1967`, same guard; a `typer.Exit` from a dead daemon lands in the `finally`). Complies.
- `cli.py:1633` session export/bundle calls `download`. Complies.
- `cli_daemonctl.py:251` pid-record temp: atomic replace, and removed on OSError. Exempt: not a streamed remote resource.
- `cli_daemonctl.py:60-87` stderr log append handles. Exempt: logs, not exports.

## Class 50: race test parks the worker after the step

Method: `grep -n -B3 -A6 "while not .*is_set()" host/tests/*.py`. 8 sites.

- `test_assert.py:790` (needle producer), `test_cli_transport_timeouts.py:57` and `test_cli_follow.py:37` (accept loops), `test_wait_repeat.py:47` (marker poster), `test_session_bundle.py:307` (noise feeder), `test_store_match_budget.py:121` (heartbeat): exempt, a stop flag on the thread's own loop.
- `test_cli.py:1323`: a poll for a result, not a double. Exempt.
- `test_store_fastpaths.py:101` `slow_scan`: spins, then calls `real_scan`. Complies.
- Supplementary sweep, because the grep misses other parkers: every wrapped real in tests (`real_\w* = |orig\w* = |original = |_real = `, 40 sites), then the 9 that block or signal.
  - Before the real call, all comply: `test_server_lifespan.py:120` (`slow_prime`), `test_store_plot_summary.py:262` (`release.wait` then real), `test_config_api_revision.py:152` (sleep then real), `test_session_bundle.py:427` (spins on `lock._waiters` then real), `test_server_scope.py:139` (sleep then real).
  - Not parkers: `test_serial_link_attach.py:376` counts only (its parker `slow_prime` precedes), and `test_assert.py:720`, `test_server_live_verdicts.py:348` (queue size) and `test_store_plot_summary.py:310` (raising double).
- Sweep imprecision: a spin on something other than an Event (`while not lock._waiters`) and an `Event.wait()` parker are both missed. Use the wrapped-real grep above.

## Class 51: boundary fetch whose empty result cannot re-fire

Method: `grep -n "scrollTop\b.*<\|IntersectionObserver\|atTop" webui/*.js`, plus every `addEventListener("scroll"` and every GET site (25) for another edge trigger.

- `terminal.js:744` `loadHistory` on scroll top: walks on under `HISTORY_HOPS`, and past the cap the walk neither advances nor ends. Violates (R51-1).
- `terminal.js:739` `atBottom`: autoscroll only, no fetch. Exempt.
- `plots.js:1449` scroll handler `syncFoldCue`: no fetch. Exempt.
- No other GET is edge-triggered.
- Owed: a headless-browser drive of R51-1 (a pane whose `match` the daemon refuses, a narrow pattern, scroll to top).

## Class 52: per-daemon artefact not keyed like its record

Method: the registry's `grep -n user_data_dir` matches only a comment (`config.py:90`). Swept instead with `grep -n 'user_dir(' *.py` (5 sites) and every name derived from them.

- `pidfile.py:61` pid record `mcuscoped-<host>-<port>.pid`: keyed.
- `cli_daemonctl.py:249` its temp `<record>.<pid>.tmp`: keyed.
- `cli_daemonctl.py:51` `.err` stderr log, derived from the record: keyed.
- `_stdio.py:386` and `:393` startup/crash logs: keyed by `set_report_key` from `daemon.py:474`.
  - A crash before that line (config load) writes an unkeyed `mcuscoped-crash.log`. Exempt: no host:port or record exists yet to key it like.
- `config.py:157` `capture.db`: per config. Exempt.
  - Its `.lock` (`lockfile.py:115`) and export temps (`server.py:2814`, keyed by `_export_key` of the capture) are per capture. Exempt.
- `update_check.py:97` update cache: cache dir, shared by design. Exempt.
- `config.py:149` config.toml: config dir, per config. Exempt.
- `mcu`/`mcu-sim` crash logs: foreground, not per-daemon. Exempt.
- Sweep imprecision: replace `grep -n "user_data_dir"` with `grep -n 'user_dir(' host/mcuscope/*.py`, plus the names built from `pid_file_path`.

## Class 53: bound sent as a parameter the peer may not declare

- Method: AST over `cli*.py` for `params[...] =`, `body[...] =`, and dict literals passed to `get`/`post`/`put`/`delete`/`stream_text` (107 key sites), plus the `/ws` URL (`cli.py:1186`) and the IfExp `params=` at `cli.py:2414` (2 more). 109 sites in all.
  - Each `(route, key)` was checked against AST route maps of v0.1.0, v0.1.1, v0.2.0, v0.3.0, v0.4.0 and HEAD (`routes.py`, `routes.json`).
- Floor: 0.1.0. The CLI supports older daemons through per-option gates (`require_daemon`), so a key younger than 0.1.0 must be gated, refused, or harmless.
- Declared by every daemon since 0.1.0: comply.
  - `/send` port/line, `/marker`, `/lines` (except `until_ts`/`id_to`), `/sessions` (POST, limit, `DELETE data`), `/purge`, `/cmd` port/cmd/timeout_ms, `/wait` (except eol/repeat_ms), `/assert` (except eol/allow_empty).
  - `/can/frames` port/session/id/limit/since_id, `/plot/export` names/format/last_ms/session, `/plot/channels` port, `/ws` port.
- Younger keys and routes:
  - `PUT /plotjuggler` and `PUT /config/plotjuggler` (0.3.0 routes): 404 into `Client.fail`, "does not serve". Refused, complies.
  - `POST /break`, `GET /lines/export`, `GET /sessions/{ref}/bundle` (0.4.0 routes): refused (404). Complies.
  - `eol` on `POST /ports` (`cli.py:392`), `/send` (`:512`), `/wait` (`:1373`), `/assert` (`:1493`), `/cmd` (`:2010`), and `/wait` `repeat_ms` (`:1373`): gated. Complies.
    - `cli.py:561` sends `eol: "none"` for `break --then` without a gate. It is only reached after `/break` (0.4.0 route) answered, so the peer is 0.4.0 or newer. Complies.
  - `until_ts`/`since_ts` via `--from/--to` (`_clock_bounds`, `cli.py:785`): gated. Complies.
  - `/plot/export` `port`/`decode`/`changes`/`deadband` (`cli.py:2485`): gated. Complies.
  - `/can/frames` `format=csv` (`cli.py:2124`): gated. Complies.
  - `/can/frames` `since_ts` from `--last-ms` (`cli.py:2137`): not gated. Violates (R53-1, driven).
  - `/can/frames` `bus` (0.3.0, `cli.py:2135`, `:2190`): not gated, and a narrowing filter. Violates (R53-1, reasoned).
  - `/lines` `until_ts` in `_pin_ceiling` (`cli.py:700`): only with `--to`, which is gated. Complies.
  - `/lines` and `/can/frames` `id_to` (0.2.0) in page walks (`cli.py:687`, `:2291`): an ignoring daemon is detected (`_newest_id > id_to`), the walk stops, and `truncated` is announced. Complies.
  - `/lines` `id_to` at `cli.py:747`, `:813`, `:849`: not gated. Violates (R53-2, reasoned).
  - `/sessions?name=` (0.3.0, `cli.py:743`, `:1623`, `:1644`): `_match_session` re-checks by exact id or name. Complies.
  - `/assert` `allow_empty` (HEAD only, `cli.py:1482`): harmless alone, since an older daemon passes an empty window anyway. Exempt. The missing empty verdict without the flag is R53-3.
- Web UI (`webui/*.js`: 28 `p.set`/`q.set`/`q.append`/`URLSearchParams` builders, 5 string-built queries, 12 POST/PUT/DELETE bodies): exempt, because it is served by the daemon it talks to.
- Sweep imprecision:
  - Say that the floor is 0.1.0 for gating purposes, not `DAEMON_MIN_VERSION`.
  - Include f-string and IfExp query builders.
  - Add behaviours a CLI contract relies on (R53-3), not only parameters.

## Class 54: wire shape built by hand at two sibling sites

Method: for each SPEC 3.4 and 9.2 query parameter (port, chan, match, since_id, since_ts, until_ts, last_ms, id_to, limit, order, bus, id, format, session, data, names, decode, changes, deadband, decimate, name, wait), grep the builders in `cli*.py` and `webui/*.js` (187 grep lines, including signature and prose noise). Ruling per parameter:

- `chan`, `match`, `port` in the web UI pane filter: two JS builders, `terminal.js:518-522` and `:621-628`. Violates (R54-2).
  - `api.js:287`'s constant `^!pd ` via `encodeURIComponent` is a fixed literal. Exempt.
- CAN `id`, `port`, `session`, `bus` in the CLI: two Python builders, `cli.py:2127-2141` and `:2182-2190`, with the id joined at `:2133` and `:2158`. Violates (R54-1).
  - The JS pair `can.js:675` sends user text. The form is the same comma list (placeholder `100,7DF`). Complies.
- `since_ts`/`until_ts`: Python `cli.py:606`, `:2137-2139`, `:2493-2495` all set floats from one computation (`_clock_bounds`/`_absolute_window`). JS `exportrange.js:65-69` is one helper. Same epoch form on both sides. Complies.
- `names`: `cli.py:2489` (user comma string) and `exportdlg.js:199` (`join(",")`), one per side, same form. Complies.
- `decode`/`changes` (`"1"` on both sides), `deadband` (user string on both), `data` (`true`/`false` on both), `format` (enum): complies.
- `port` elsewhere, `session`, `since_id`, `id_to`, `limit`, `order`, `last_ms`, `name`, the `/ws` `port`: scalar values passed verbatim to the transport's encoder. There is no client-side shape to drift, so they comply.
  - Python sites: `cli.py:594`, `:2129`, `:2184`, `:2414`, `:2499`, `:1186`, `:602`, `:650`, `:721`, `:2278`, `:610`, `:687`, `:747`, `:2291`, `:592`, `:642`, `:675`, `:701`, `:713`, `:1572`, `:2182`, `:2192`, `:639`, `:604`, `:2131`, `:2186`, `:2497`, `:2491`, `:1623`, `:1644`.
  - JS sites: `api.js:288`, `:366`, `:401-409`, `:478-479`, `:554`, `exportrange.js:63`, `:70`, `:73`, `terminal.js:518-520`, `exportdlg.js:153`, `settings.js:435`, `:452`, `state.js:365`, `can.js:674`, `exportdlg.js:201`.
- `decimate`, `wait`: no client builder (0 sites).
- Sweep imprecision: "two builders on one side are a violation" reads literally on every scalar passthrough. Restrict it to parameters with a client-side shape (list encoding, joined ids, time format) and to a filter set rebuilt in two places.

## Class 55: refusal keyed on a kind name

Method: `grep -nE "kind ==|type ==|kind !=|type !=|\.kind\b|\.type\b|\["kind"\]|\["type"\]|kind ===|type ===|kind !==|type !=="` over `server.py`, `render.py`, `cli_output.py`, `protocol.py` and `webui/*.js` (58 lines). A second grep for kind literals (`"enum"|"bit"|"bits"|"analog"`, 22 more) found the refusal itself.

- `server.py:3623` `_renders_as_label` (the deadband refusal): keys on `enum` and `bit`, exactly the kinds `_decode_map` (`:3635-3640`) gives labels, i.e. `num=None`. Complies.
- `protocol.py:896-1049` (bits/enum decode, `declared_kinds`, `channel_meta`), `cli_output.py:430/434`, `plots.js:241-246`: the branch is the kind itself. Complies.
- `plots.js:297` routes to digital lanes on `enum` or `bits`, every kind that renders as a lane. Complies.
- `plots.js:357/368/371`: `bit` groups lanes of a bits channel. Complies.
- `plots.js:665`: the integer check keys on `type`, the property itself. Complies.
- `digital.js:209/211`: `isBit` means has lanes. Complies.
- `digital.js:556/635/770`: `LANE_KINDS` has `bits` and `enum`, the only kinds `plots.js:297` routes there. Complies.
- `protocol.py:373/375` (response OK/ERR), `server.py:382/656/686/706/805/813/816/836/854/892/2268/2881` (ASGI scope and message types), `exportdlg.js:62/64/74/94` (form field types), `exportdlg.js:219/281` (export kind name), `digital.js:727/728` (event types), and the `chrome.js:67` and `settings.js:381/413/418/535/552/559/565` DOM assignments: not channel kinds. Exempt.
- `render.py`: 0 sites.
- Sweep imprecision: add membership tests (`in ("enum", "bit")`) and dispatch tables keyed by kind (`LANE_KINDS[...]`). ASGI `scope["type"]` and DOM `.type` are noise.

## Class 56: change marker diffed against the last paint

Method: `grep -n "moved\|changes\|_last\b\|prev" webui/*.js cli*.py server.py` returned 226 lines. Every line is bucketed in `c56-ruled.txt` (scratch).

- CAN byte mask, 18 lines (`can.js:23,158,175,178,180,202,203,205,208,209,214,471,476,477,490,497,560,562`): diffed per frame at ingest against `e.base`, the previous frame of that id, accumulated into `moved`, and consumed per paint. Complies.
- CLI `LineDecoder` `--changes`, 22 lines (`cli_output.py:372-417`; `cli.py:790,801,804,821,831,839,841,963,988,993,1033,1957`): per `(port, key)` at each decoded sample. One decoder per output; `share_changes` carries the baseline from the snapshot into the follow, and one `_decode_pages` spans every page. Complies.
- Server `/plot/export` changes, 18 lines (`server.py:2057-2077,2136,2138,3516-3537,3698-3708`): per `(port, name)` and `(port, sid, name)` per row, in one generator per export. Complies.
- `digital.js:91` transition reduction, per sample at ingest: complies.
- Exempt:
  - Status-bar rate (11 lines, per poll interval).
  - Terminal delta column (8 lines, per displayed row, SPEC 9.1).
  - Tick continuity (23 lines).
  - Shed-notice merge (3), layout and DOM diff caches (7), `canFilterPrev` (2), cmd `historyPrev` (1).
  - Option wiring and help text (27), `preview` identifiers (9).
- Noise: `preventDefault` (13 lines), "removed" (15), prose and comments (46).
- Sweep imprecision: about 85% of the output is noise. Tighter: `grep -nE "\.(moved|base)\b|_last\b|prevTick|share_changes|_changed\(|changedBytes" host/mcuscope/webui/*.js host/mcuscope/cli*.py host/mcuscope/server.py`.

## Other observations

- `docs/REVIEW.md` layout: the "A round is these legs" list (`REVIEW.md:561-631`) sits inside class 43's entry, between its Sweep line and class 44. It reads as part of class 43.
- `cli.py:587-600`: `_lines_params`'s `last_ms` argument is dead. Every caller passes None since `_absolute_window` took over, and nothing tests the branch. This is a candidate for the coverage leg (delete it).

## Owed

- Windows: the class 43 floor run on Windows (not run). Classes 44-56 have no Windows-specific leg.
- Browser: an R51-1 drive in a headless browser.
- Not driven, reasoned only: R48-1 for `can_stat`, R51-1, R53-1 for `--bus`, R53-2, R54-1, R54-2.

## The two questions

1. **Least confident, and rechecked.**
   - Class 46's age test ("the key string exists somewhere in the v0.1.0 daemon") could pass a key that 0.1.0 sent in a different response. I spot-read the origin of every non-trivial key (port snapshot, assert verdict, sessions, channels, purge, CAN frames), and each was in the response the CLI reads.
   - R53-1 and R53-3 could have been probe artefacts. I re-drove both against real v0.3.0 and v0.4.0 daemons (`git archive` of the tag, `--sim`, a throwaway config on ports 18631/18641, stopped by PID), not against canned responses.
   - The class 43 per-file run ran each file once in random order. Order-dependent flakiness at the floor was not probed.
2. **What we have not thought about.**
   - Contracts that exist only in the daemon. The CLI's refusals and verdicts increasingly rely on daemon-side checks added at HEAD: the empty verdict, `_unknown_port` on retrospective reads, the undeclared-query 422. Against a 0.4.0 daemon, which `DAEMON_MIN_VERSION` still admits, each one is silently absent, and R53-3 is the driven instance.
   - Nothing sweeps "every client-side contract the SPEC states, against the oldest admitted daemon". A registry extension to class 53, or a new class, would cover it.
   - The same outward look found R45-1: the class 45 sweep compares wire fields, and the loss was one layer further down, in the file write-back.
