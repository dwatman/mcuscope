# Triage, 2026-09-23 round

Reports: `capture.md`, `lifecycle.md`, `api.md`, `cli.md`, `firmware.md`, `perf.md`, `webui.md`, `webui-cpu.md`, `health.md`.
Orchestrator re-checked against the code: CAPTURE-1, CLI-1, LIFECYCLE-1, WEBUI-1, FIRMWARE-1, PERF-2, WEBUI-CPU-2, HEALTH-2, HEALTH-6, CLI-12, API-5.

Merged: CAPTURE-3 with HEALTH-1 (id sequence), FIRMWARE-4 with HEALTH-16 (f4 non-finite), FIRMWARE-9 with HEALTH-19 (tokenizer).
API-10 reopened by the owner 2026-09-23 (see rulings). HEALTH-24's `store._broadcast` deletion is dropped: `test_hardening.py` drives the drop path through it (HEALTH-12).

## Owner rulings (2026-09-23)

- Firmware footprint: build the own bounded formatter replacing internal `snprintf` (FIRMWARE-15, with 16 and 18), and opt-in per-family build flags answering `ERR 7 nosup` (FIRMWARE-17).
- FIRMWARE-2: an over-long event is cut at its last space and an overflow notice is emitted; no silent loss.
- FIRMWARE-4: a non-finite value drops that point only, host and browser alike.
- FIRMWARE-8: `x` in a CAN filter passes only extended frames, a plain filter only standard ones.
- CLI-3: an unknown `-p` is refused; a verdict that checked 0 lines (live or retrospective) reports `empty`, exit 1; `--allow-empty` opts back into a pass.
- CLI-10: a daemon that accepts but never answers is exit 1 on every command.
- CLI-18: a write without `-p` is refused whenever more than one port is attached, whatever their state; reads still span all ports.
- API-7: on a TTY, human output keeps SGR colour sequences and shows every other control byte visibly; `--json`, files and pipes stay faithful.
- PERF-9: commits coalesce only under load (above about 200 lines/s, at most one per 100 ms).
- CLI-4: `--since-id` returns the next N rows above the id; the guide's polling recipe uses `order=asc`.
- HEALTH-27: tests move to per-module files in a separate commit after this round's fixes.
- API-10: the CSV `raw` cell gets the same formula guard as channel names (OWASP CSV injection); jsonl stays the faithful format. Reverses the 2026-09-15 "kept".
- PERF-7: reclaim about 256 pages per call (worst call 39 ms); a large freelist drains about 8x slower.
- CLI-15: a WS upgrade answered 502/504 stays exit 3 (a gateway with nothing behind it); other statuses exit 1.
- Session names and other user text show bidi and zero-width controls as `<U+XXXX>` (orchestrator, owner's no-silent-errors preference).
- pjstream's unreachable per-value filter is deleted; the per-point drop is pinned end to end (orchestrator).
- Vendored monitors (charger-test, charger_control, relay_control in `~/Syncthing/auto-charger/`): re-vendor after the firmware batch; their CAN shims set every field, so FIRMWARE-1 does not bite them.

## Orchestrator rulings

- CAPTURE-1: the id floor seeks at `cutoff - slack` and keeps the exact `ts` term; `purge before_ts` deletes by `ts` in chunks. The slack bounds the stamp-to-commit delay and is stated in SPEC 3.4.
- CLI-1: `/wait` and live `/assert` never match `dir=tx` rows unless the caller's `chan` names the tx channel.
- CLI-2: `/assert --send` returns `cmd_result`; a send answered `err` or `timeout` fails the verdict; `wait` text mode prints the ERR and exits 1.
- LIFECYCLE-1: a pid is signalled only when a local pid record for that host:port names it; otherwise `/shutdown` alone, judged by `/status` going quiet.
- LIFECYCLE-3: a stale automatic session is closed in `Store.start()` at its last row's `ts`, before `daemon start` is written.
- FIRMWARE-5: the firmware's reading (unknown subcommand is `ERR 1 badcmd`); the sim follows, SPEC 2.4 says so.
- Tokenizer (FIRMWARE-9, HEALTH-19): both sides split tokens on runs of U+0020 only; any other whitespace is a token byte.
- WEBUI-3: a `{gap}` becomes a divider in the terminal and a null break in every chart and lane of that port.

## Fix batches (partitioned by file)

| Batch | Files | Findings |
|---|---|---|
| store | `store.py` | CAPTURE-1, 2, 3 + HEALTH-1, 5, LIFECYCLE-3, PERF-1, 2 (paging), 3 (message), 5, 6, 7, 8, 9, HEALTH-15 B07, HEALTH-20 SRC-7, HEALTH-25 fold, store comments |
| link | `serial_link.py`, `link.py` | CAPTURE-4, 6, 7, 8, API-1 (write pool, `sent_ts`), LIFECYCLE-4 (`attach(require_existing=)`), HEALTH-15 L03 L06, SRC-8 comment |
| server | `server.py` | API-1 (export pool), 2, 3, 10, 4, 5, 6, 8, 9, 11, WEBUI-1, CLI-1, CLI-2, CLI-3 (server), CLI-18 (server default), PERF-2 (close on disconnect), PERF-4, LIFECYCLE-2, LIFECYCLE-4 (caller), SRC-6 PUT, HEALTH-15 S03 |
| daemon | `daemon.py`, `config.py`, `lockfile.py` | LIFECYCLE-5, 6, HEALTH-4, 5, 11, SRC-6 warnings |
| cli | `cli.py`, `cli_client.py`, `cli_output.py`, `cli_argv.py`, `cli_daemonctl.py`, `_stdio.py`, `render.py`, `pidfile.py` | CLI-1..18 client halves and guide, API-7, CAPTURE-9, LIFECYCLE-1, HEALTH-2, 3, 6, HEALTH-15 C04 C06 C11 B08 D01, HEALTH-24 argv hoist |
| firmware | `firmware/`, `sim.py`, `protocol.py`, `pjstream.py`, `tools/` | FIRMWARE-1..18, HEALTH-16 (Python), tokenizer (Python) |
| panes | `terminal.js`, `plots.js`, `digital.js`, `can.js`, `api.js`, `pane.js`, `exportrange.js`, `timewindow.js` | WEBUI-CPU-1..8, WEBUI-2, 3, 4, 5, 6 (axis label), HEALTH-7, 8, 16 (JS), 17 (M29 M62 M71), JS-7, tokenizer (JS), FIRMWARE-4 (JS) |
| chrome | `state.js`, `statusbar.js`, `cmdbar.js`, `settings.js`, `exportdlg.js`, `chrome.js`, `freeze.js`, `layout.js`, `app.js`, `theme.js`, `index.html`, `style.css` | WEBUI-6 (chips), 7, HEALTH-9, 17 (M46), 18, 24 (JS), JS-5, JS-6, tokenizer (`lineTick`), CLI-18 (UI port pick), stale comments |
| tests | existing test infra and docs: `conftest.py`, `support.py`, `test_e2e.py`, `test_plot_export_decode.py`, `test_session_bundle.py`, `test_sim_pty.py`, `test_sessions.py`, `test_cli_contract.py`, `test_eol.py`, `pyproject.toml`, `docs/ARCHITECTURE.md`, `CLAUDE.md` | HEALTH-10, 12, 13, 14, 21, 22, 23, 26 |

Shared documents: SPEC by section (store 3.2/3.4 storage and windows; server 3.1, 3.3, 3.4 routes; cli 4; firmware 2, 3.7, 5; panes and chrome 9), re-read and retry on a failed anchor.
