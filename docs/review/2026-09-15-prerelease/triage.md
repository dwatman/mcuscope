# Pre-release round triage (v0.4.0..fdd30a2)

Leg reports: `A-daemon-api.md`, `B-cli.md`, `C-sim-link.md`, `D-webui-panes.md`, `E-webui-chrome.md`, `F-tests-mutation.md`, `G-release.md`.

## Fix batches (partitioned by file)

- **Daemon core** (`store.py`, `server.py`): A-1, A-2, A-3, A-4, A-6, A-8 and B-16 (grammar only, negatives left), A-9, A-10, A-11, C-1, C-2, stale comment at `server.py:343` (E-13).
- **Link and lifecycle** (`daemon.py`, `serial_link.py`, `sim.py`): A-7, C-4, C-5, C-6, C-7 (`--flap` refuses non-finite, class 64), C-9 bind-failure test.
- **CLI** (`cli.py`, `cli_client.py`, `cli_output.py`): B-1 to B-15, B-17, B-18, G-1 (only the shutdown 503 maps to exit 3, per SPEC 3.4 and 0.4.0).
- **Web UI chrome** (`settings.js`, `statusbar.js`, `cmdbar.js`, `state.js`, `exportdlg.js`, `app.js`, `index.html`, `style.css`): E-1 (round-trip the loaded bytes), E-2, E-4, E-5, E-6, E-7, E-9, E-10, E-11, E-12, D-11 CSS, `settings.js:195` class 61.
- **Web UI panes** (`terminal.js`, `can.js`, `plots.js`, `digital.js`, `pane.js`, `exportrange.js`): D-1, D-2 (anchor only), D-4, D-5, D-6, D-7, D-8, D-10, D-11 title, D-12, E-3 `match` gate at `terminal.js:574`, E-13 comment.
- **Docs** (after code): SPEC, CHANGELOG, README, SCREENSHOTS, AI_GUIDE for G-2 to G-9, A-12 sentence, B-13, E-13, and every behaviour change above.
- Leg F, by owning batch:
  - Daemon core: F-1, F-2, F-5, F-7, F-10, F-11, F-27, F-28, F-29, F-30 (F-9 is C-2).
  - Link and lifecycle: F-3, F-32 (F-31 is C-9).
  - CLI: F-4, F-12.
  - Web UI chrome: F-8, F-17, F-18.
  - Web UI panes: F-13 to F-16, F-19 to F-26.
  - F-6 is C-3 (owner decision).

## Owner rulings (2026-09-15)

To implement:

- Release 0.5.0. Bump `__version__` to the next release as the first commit of each cycle, starting now.
- PyPI README image pinned to the release tag at tagging; add to the release checklist.
- Subscriber cap exits 1 everywhere: `tail -f` on WS close 1013 moves from 3 to 1.
- E-3/E-9: token-less exports preflight (fetch, abort once headers are ok, then navigate) and show any refusal. A session `.db` export checks the session still exists instead of preflighting the copy.
- E-8: `GET /config` returns a revision; `PUT /config/*` sends it back, and a changed file answers 409 with a reopen hint.
- A-5: unknown-key warnings logged once at startup, exposed as `config_warnings` on `/status`, shown in Settings.
- A named config that does not exist is refused (exit 1, `no such config file: <path>`) by `mcuscoped` and `mcu daemon start`; a missing default config still means defaults.
- D-2: `shown` while a drag zoom stands exports the zoom range.
- D-3: tick mode continues across a backward tick jump by the host-time gap, with a break in the line at the reset.
- D-9: a digital panel paused before its first lane stays empty and keeps its pause-time watermark.
- Shift-click window span is a group state: a later chart inherits it. SPEC 9.2 reworded.
- C-3: a detached board's `/plot/channels` definitions come from its own stored `!pd` rows (bounded scan as `prime_plot_defs`), else null fields. SPEC 9.2 fixed to "unique only within a port".
- Negative deadbands refused (`deadband for X must be >= 0`) in daemon, CLI and the JS guard double.
- Bundle plot files named `plot_<port>_<sid>.csv`.
- C-8: a failed start rewrites the startup log as a failure with the exit code.
- The 35 order-dependent JS tests become self-contained.

Kept as is:

- A-12: the lower time bound stays documented as inexact across a backwards clock step.
- A-10: a window that ends before its session starts answers an empty 200.
- The bundle holds `_sweep_lock` for its whole build.
- CSV `raw` stays unguarded against formulas.
- E-7: offline command input stays enabled with `(offline)`.

## Sweep-stage decisions for the owner

Reports: `sweep-daemon.md`, `sweep-cli.md`, `sweep-webui-chrome.md`, `sweep-webui-panes.md`, `sweep-tests.md`.

- WS guard refusals (Host, Origin, token, lockout) happen before `accept`, so every client sees an HTTP 403 handshake, not the close 1008/1013 SPEC 3.1/3.4 promise; the web UI's 1008 token prompt never fires.
- `POST /cmd` parked in `send_command` at shutdown answers 500 after the grace, not the shutdown 503.
- `mcu wait` maps a transport read timeout (wedged daemon) to exit 2, the "nothing matched" code.
- `mcu daemon status/start` read a 401/403/429 from a running daemon as "not running"; `start` then spawns a second daemon.
- `mcu can dump` ignores `truncated`: `-n 5000` shows 1000 silently, and `-f` drops frames past 1000 per poll.
- Dialogs opened up to 4 s after their click (stalled daemon) take focus from wherever the user went.
- A pane clear during a WS backfill: the backfill's rows reappear after the clear.
- A post-action status refresh can share a poll already in flight and show pre-action state for up to 5 s.
- Two section saves inside one PUT's round trip: the second gets a 409 of its own making.
- A daemon wall-clock step back: host-base charts glue and stall, CAN ages read dead, CAN eviction drops live ids.
- The terminal tick column shows the raw tick after a board reset, lower than the chart cursor.
- Tick base with two boards: lanes share one right edge, so the lower-uptime board's lanes sit off screen.
- Firmware refuses µ, control bytes and DEL in a unit; the host accepts them.
