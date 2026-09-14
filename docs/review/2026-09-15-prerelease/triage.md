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

## Decisions for the owner

- E-3: token-less export navigation loses the daemon's refusal (deadband, pane regex): header preflight, client mirrors, or fetch-to-blob.
- E-8: concurrent config edits: revision check (409), merge by alias, or documented last-writer-wins.
- D-2: `shown` while a drag zoom stands: the zoom range or the window selector span.
- D-3: tick mode across an MCU reset: continue by host-time gap, or break the trace.
- D-9: digital panel paused before its first lane: re-anchor on the first sample, or stay empty.
- Shift-click window span: an action on existing charts, or a group state later charts inherit.
- Story-style JS tests (35 of 284 pass only in file order): accept, or make each test self-contained.
- C-3: a detached board's `/plot/channels` definitions: another board's (SPEC 9.2 today), its own stored `!pd`, or null; SPEC 2.5 and 9.2 disagree on channel-name scope.
- C-8: startup log on a failed start: record the failure, or write only after `server.started`.
- Subscriber-cap 503: exit 1 (as fixed here) or a new documented retryable code.
- `mcu daemon start --config typo.toml` starts on defaults: refuse a named config that does not exist?
- A-5: unknown config key warnings reach only stderr: expose on `/status` and Settings?
- Version gate: bump the version first in each release cycle, so a new parameter is gateable.
- `--deadband name=-1` taken as its magnitude: refuse negatives?
- A-12: make the `since_ts`/`last_ms` id floor exact across a backwards clock step (cost as A-1), or keep it documented.
- Bundle holds `_sweep_lock` for the whole build: bound it, or accept.
- Bundle `plot_<sid>.csv` is port-unscoped: `plot_<port>_<sid>.csv`?
- `lines/export?format=csv` leaves captured `raw` open to formula injection (decided earlier; re-listed).
- A-10 across a clock step: the new crossing-window refusal compares `since_ts`/`until_ts` with the session's wall-clock stamps, so after a backwards step inside a session a window holding rows can be refused (loud, reasoned). Keep, or refuse only ts-against-ts pairs.
- `mcu tail -f` at the subscriber cap (WS close 1013) still exits 3, where `wait`/`assert` now exit 1.
- E-7: offline, the command bar reads `(offline)` but its input stays enabled (disabling on one failed poll would blur typing); enough, or should it look disabled?
- Release version: 0.5.0 (the unknown `session=` 400 is an interface change) or 0.4.1.
- PyPI README image: pin to the release tag, or track `main`.
