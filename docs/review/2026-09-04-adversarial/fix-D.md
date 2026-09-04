# Fix batch D - web UI (JS)

HEAD at start: `7a1120f`. Not committed.

## What changed

**D1 (W2) - `write_failures` on the port chip.** `host/mcuscope/webui/statusbar.js`.
`p.write_failures || 0` added to `portsSig`; a connected port with write failures now takes the `crit` dot alongside the store-wide `writeErrors`/`writerDead`; a `.meta.drop` badge reading "N write fail(s)" carries `last_write_error` as its title, mirroring the `rx_dropped` block above it.
`docs/SPEC.md` 9.1 chip line names `write_failures` (and `last_write_error` in the hover).

**D2 (W3) - the CAN divider's storage write.** `host/mcuscope/webui/can.js`.
The bare `localStorage.setItem` in `toggleCollapsed` is wrapped.
A bare `try/catch` alone does **not** fix the reported symptom: `renderCan` re-reads the store through `loadCollapsed` on every rebuild, so a refused write means the click still does nothing visible. `toggleCollapsed` therefore keeps the set in `collapsedMem` when the write is refused, and `loadCollapsed` prefers it; a successful write clears it, keeping the stored value the single source of truth on the normal path. This is the behaviour the finding describes ("applies for this page and is simply not remembered") and what its test idea asserts.

**D3 (W4) - `timeMode` range check.** `host/mcuscope/webui/terminal.js`.
`TIME_MODES = ["host", "tick", "rel"]` beside `VIEW_MAX`/`MAX_PANES`; `loadState` uses `TIME_MODES.includes`. The `st.rel === true` migration arm is unchanged.

**D4 (W5) - the line ending in the offline branch.** `host/mcuscope/webui/settings.js`.
`renderEol()` added beside `renderToken()` in `openSettings`' `if (!cfg)` branch.

**D5 (W6) - seed ingest guards.** `host/mcuscope/webui/plots.js`.
The group body of `plotSeed` is extracted to `seedGroup(key, group)` (its `continue` becomes a `return`), the group loop wraps that call in `try/catch`, and the `mergeSeedSeries` row loop guards per row and reports the last error once via `console.error`. Extraction rather than a nested block because the group body already had a `continue`.

**D6 (W7) - `disconnect_reason` gloss.** `host/mcuscope/webui/statusbar.js`.
`DISCONNECT_WHY`, null-prototyped (the key comes off the wire), maps the four tokens; `|| pt.disconnect_reason` keeps an unknown value visible.

**D7 - test-quality gaps.**

1. `tests/webui_js/statusbar_logic.test.mjs`: "a deterministic render fault is logged once, not on every poll" - two refreshes over a fault, `console.error` counted. Uses a fresh module instance (`import(... + "?once")`) because the latch is module state and the existing fault test spends it.
2. `tests/webui_js/terminal_logic.test.mjs`: "a flush across a VIEW_MAX trim must not leave the old rows on screen" - `VIEW_MAX` rows, five more flushed in, asserts the rendered window equals `rows.slice(winFirst, winLast)`. The trim keeps the row count, so the index bookkeeping alone says "nothing moved".
3. `tests/webui_js/plots_channel_cap.test.mjs` (new): 69 distinct ad-hoc names through `plotIngest`; exactly 64 channels, the samples still land, the warning fires once, and the panel shows "(limit 64 reached)". Its own file so no earlier test spends part of the cap.
4. `tests/webui_js/cmdbar_bounds.test.mjs`: "re-submitting the same command stores one entry" - asserts the stored array and that ArrowUp twice reaches a different command.

Other new/changed tests: `plots_seed_grammar.test.mjs` (two poisoned-lane cases for D5), `can_logic.test.mjs` (D2), `settings_eol_offline.test.mjs` (new, D4), `terminal_timemode.test.mjs` (new, D3), `statusbar_logic.test.mjs` (D1, D6; the three existing `disconnect_reason` tip assertions updated to the glossed text, plus an unknown-token case).

## Revert verification

Each fix reverted by hand in a copy of the file, the test run, the file restored. All ten mutations were caught.

| # | mutation | test file | result |
| --- | --- | --- | --- |
| D1 | drop `write_failures` from the sig, the dot and the badge | `statusbar_logic.test.mjs` | not ok 18 |
| D2 | bare `setItem`, no `collapsedMem` | `can_logic.test.mjs` | not ok 21 |
| D3 | `TIME_MODES.includes` -> `typeof === "string"` | `terminal_timemode.test.mjs` | not ok 1 |
| D4 | remove `renderEol()` from the `!cfg` branch | `settings_eol_offline.test.mjs` | not ok 1 |
| D5 | remove both `try/catch` | `plots_seed_grammar.test.mjs` | not ok 6, not ok 7 |
| D6 | tip back to the raw token | `statusbar_logic.test.mjs` | not ok 5 |
| D7.1 | `renderFaultLogged` latch -> bare `console.error` | `statusbar_logic.test.mjs` | not ok 19 |
| D7.2 | delete the identity loop in `shiftWindow` | `terminal_logic.test.mjs` | not ok 16 |
| D7.3 | `MAX_CHANNELS` 64 -> 640000 | `plots_channel_cap.test.mjs` | not ok 1 |
| D7.4 | duplicate check -> `if (true)` | `cmdbar_bounds.test.mjs` | not ok 4 |

`cd host && uv run python -m pytest tests/test_webui_js.py -q` passes (263 node tests).

## Not done

- W1 is server-side and out of this batch by instruction; `settings.js collectPorts` untouched.
- `index.html` needed no change: the write-fail badge reuses the existing `.meta.drop` styling.
