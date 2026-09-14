# Spec review, round 2: c5bed6f (and origin/main...HEAD)

Resolved and verified:

- The unknown-session 400 (tests pass, callers unchanged).
- `plotExportPath` (same parameter order).
- Formula-guard wording (server.py:2704, can.js:543).
- Ramp wording (sim.py:615).
- Light accent contrast (theme test).
- The guard double's contract test.
- The `mcu wait` guide and SPEC 4 row.
- Bullet splits: a word-diff of every hunk in SPEC, CHANGELOG and REVIEW.md found no dropped fact.

Targeted pytest runs (73 tests) and node runs (37 tests) pass.

## (a) Missing or partial

- S-F6 "Fix (right): key `charts` and `digitalLanes` by `port|sid|name`" (2026-09-14-webui-ux/SUMMARY.md:40). The per-port seed restores the other board's points but not its definition.
  - `/plot/channels?port=B` fills `type/unit/scale/kind/labels` from `plot_channel_meta()`, which merges every port by name, last one wins (serial_link.py:1388-1397, server.py:1767-1785).
  - `seedDef` (plots.js:350) then seeds B's history under A's unit or enum labels.
  - Verified by reading.
- SPEC.md:1644: the seed "restores each board's history". It does not do this for a detached board whose names are all shadowed by an attached board.
  - That board is in neither the unfiltered list (store.py:1997, newest port per name) nor `/status`, so `ports.size < 2` returns early (api.js:317).
  - Verified by reading.
- 2026-09-15 spec.md:14: "the owner should pick one" (the marker button, the CAN staleness floor).
  - REVIEW_LOG records the implementer's choice ("deliberate, reasons in batch-b-report.md"), not an owner decision.

## (b) Scope creep

- None in c5bed6f.
- improve-host.md:157 said "no guide change", but CLAUDE.md requires the AI_GUIDE edit, so the guide change is correct.

## (c) Implemented but looks wrong

- Init-order regression from dropping the static `#cmdEol` options.
  - `initCmdBar` calls `populateCmdPort()` (cmdbar.js:254), whose `syncCmdEol()` (cmdbar.js:62, 111) sets `sel.value = getEol()` before `fillEolOptions` runs (cmdbar.js:266).
  - With a saved `crlf`, the value matches no option. The options appended next re-select the first entry, so the bar shows "(LF)" while sends append CRLF.
  - This lasts until a `/status` render calls `syncCmdEol` again (statusbar.js:486), and never happens while the daemon is down.
  - `cmdbar_eol.test.mjs` misses it because it fills first and appends the default entry last, which is the reverse of index.html's order.
  - Verified by reading against the HTML select insertion rule.
  - Fix: fill before `populateCmdPort()`.
- `wait --send` failure count: on the single-send path a write error is a 400 (server.py:2308), so `failures` is always 0 on a timeout.
  - The new test fixture's `sends:1, send_failures:1` is a state the daemon cannot produce.
  - Harmless, but the suffix implies a count that can never be non-zero.
- Nit: SPEC.md 9.1 "The arrow keys move and select the checked button" reads as moving the checked button. The original said the tab stop is "moved and selected with the arrow keys".
