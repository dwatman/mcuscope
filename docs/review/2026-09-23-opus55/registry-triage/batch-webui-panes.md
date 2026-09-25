# Batch webui-panes

Web UI view logic: panes, charts, lanes, CAN table, chrome.
Owner-pick items use the recommended option in `decisions.md` unless the owner answered otherwise.
Run JS test files one at a time (`node --test <file>`); never two whole JS suites at once.

## Files

- Source: `host/mcuscope/webui/terminal.js`, `pane.js`, `state.js`, `can.js`, `chrome.js`, `plots.js`, `digital.js`, `timewindow.js`, `exportdlg.js`, `index.html`.
- Tests you may edit: `test_pane_regex_dialect.py`, `tests/regex_dialect_cases.json`, `tests/webui_js/can_export_port.test.mjs`, plus new `.test.mjs` files.
  Do not edit `dom_stub.mjs`, `exportdlg_guards.mjs` or the JS tests webui-settings fixes (`plots_pause_edge`, `plots_hover_tick`, `plots_tick_reset`, `digital_tick_reset`, `settings_dirty`, `cmdbar_detached_pick`, `statusbar_*`, `export_paused_window`, `state_marker_tick`, `settings_loading_hold`).
- Docs: SPEC 9 (all of it); the web UI section of `docs/ARCHITECTURE.md`.

## Findings

### R19-1 LOW: host markers decoded as firmware `!m` lines
- Checked: `computeTick` sends every `chan === "marker"` row to `markerTick` (state.js:243, :261); the divider strips `!m @N` (terminal.js:99). The daemon parses `!m` only on rx (serial_link.py:939); `/marker` rows are `dir "-"`.
- Fix: both gate on `row.dir === "rx"`.
- Test: `lineTick({chan: "marker", dir: "-", raw: "!m @4000000000 hello"})` is null and the divider keeps the text whole; an rx `!m @5 x` still reads 5.

### R19-2 LOW, owner-pick D-5 (UI half)
- Checked: `SAME_LETTER_ESCAPES` (pane.js:57) treats `\d \w \s \b` as identical in both engines; that holds for ASCII subjects only.
- Fix (D-5 option A): with the daemon on ASCII classes (daemon-api), the pane rewrites `\s`/`\S` to the ASCII set (`[\t-\r ]`, inside and outside classes) before `new RegExp`. `.` against an astral character stays a documented residual.
- Test: `regex_dialect_cases.json` gains non-ASCII lines (`٣`, `é`, U+0085, U+FEFF) under "same"; `test_pane_regex_dialect.py` and the JS side agree on them.
- Depends on the daemon half landing first (the Python side of the fixture test runs `_make_regexp`).

### R22-5 LOW: `intField` accepts hex, binary, octal, exponent and `1000.0`
- Checked: state.js:195-200 uses `Number()`.
- Fix: accept `^-?[0-9]+$` after trim, then `Number`.
- Test: `0x3E8`, `0b1111101000`, `1e3`, `1000.0`, `+1000` give NaN; ` 1000 ` gives 1000.

### R23-1 LOW: a pane paused mid-flush later grows while paused
- Checked: `setAutoscroll(pane, false)` (terminal.js:316) freezes at `state.maxId`, which includes rows still in `pane.queue`; the flush then drops the queue (:412) and the next rebuild adds them.
- Fix: freeze at the newest row the pane drew, so queued rows count as "N new".
- Test: rows 1-10 drawn, 11-13 queued, pause, `rebuild()`: rows stay 1-10, pending 3.

### R25-1 MEDIUM, owner-pick D-10: a pane added after clear-all shows every cleared line
- Checked: clear-all sets each pane's `clearId` (terminal.js:852); `addPane` (:768) starts at 0 and rebuilds from the buffer.
- Fix (D-10 option A): clear-all records a watermark in state; a new pane starts its `clearId` there.
- Test: 50 rows, clear-all, add a pane: it holds 0 rows; a later row appears in it.

### R25-2 LOW, owner-pick D-11: a live chart's selector shows the zoom chip
- Checked: `paintWindowGroup` (chrome.js:150-156) paints every selector from the global `zoomText`; F-23 pins a chart born live under a zoom following its tail.
- Fix (D-11 option A): a selector whose surface is not frozen on the zoom lights its own span and hides the chip; SPEC 9.2 reads "every window selector of a surface frozen on it shows a chip".
- Test (new file): the F-23 scenario, then the new chart's selector has the chip hidden and one span lit.

### R25-4 LOW: CAN unfilter misses cloned panes and outlives closed ones
- Checked: `+ pane` (terminal.js:838-839) clones `regexSrc` but not `canFilter`; `closePane` (:783-795) leaves `canFilterClear` shown.
- Fix: a clone of a CAN-filtered pane copies `canFilter`/`canFilterPrev`; `closePane` hides the button when no pane is CAN-filtered (terminal.js exports the check; can.js owns the button).
- Test: clone then unfilter restores both panes; closing the only filtered pane hides the button.

### R25-5 LOW: group buttons name fewer members than they govern
- Checked: index.html:53-54.
- Fix: titles name panes, charts, lanes and the CAN table (pause all) and panes, charts and lanes plus the time re-zero (clear all).

### R26-1 LOW, owner-pick D-12: a paused pane's tick estimates decay to `~-`
- Checked: `fmtTs` (terminal.js:47) re-derives `~N` from `tickAnchors`, capped at `ANCHOR_CAP` and rotating.
- Fix (D-12 option A): at pause, snapshot the anchor lists of the ports the frozen rows use; while paused, estimates read the snapshot.
- Test: pane paused on `!can 7000` plus a debug line reads `~250`; after 10001 later anchors it still reads `~250`.

### R28-2 LOW: the dialect test counts any exception as the daemon's refusal
- Checked: test_pane_regex_dialect.py:29 `except Exception`.
- Fix: `except regex.error`.
- Revert-verify: `_make_regexp` raising `KeyError` fails it.

### R34-1 LOW: a saved colour is type-checked, not grammar-checked
- Checked: `loadColors` (chrome.js:25-33).
- Fix: keep only `/^#[0-9a-f]{6}$/i` values.
- Test: stored `"garbage"` falls back to the default colour.

### R51-1 LOW, owner-pick D-15: history walk stalls at the hop cap
- Checked: `loadHistory` (terminal.js:494-512) stops after `HISTORY_HOPS` empty pages with `historyDone` false while the view is at the top, where no scroll event can fire.
- Fix (D-15 option A): the hint becomes a control, "no match in the last N lines; search older", that runs the walk again.
- Test: a filter emptying 5 pages shows the control; clicking it fetches the sixth.

### R54-2 LOW: pane filter params built twice
- Checked: terminal.js:518-522 and :621-628.
- Fix: one helper both call. Existing tests cover both.

### R57-1 LOW: CAN export prefills every port's ids for one port's export
- Checked: `visibleCanIds` (can.js:626-628) is the union over ports; the export is `port=chosen(v)`.
- Fix: ids per port, and the ids field follows the Port select until edited (a derived default in `exportdlg.js`).
- Test (`can_export_port.test.mjs`): p1 shows 100, p2 shows 200; default export prefills `100`; switching Port to p2 prefills `200`.

### R72-1 LOW, owner-pick D-18: a background 401 opens `window.prompt`
- Checked: `promptForToken` (state.js:56) from `authFetch` on any request, including the 5 s poll.
- Fix (D-18 option A): a 401 from a background request marks auth needed on the daemon chip; the prompt opens on the next user action or a click on the chip.
- Test: a 401 on the poll path calls no prompt and shows the chip; a user-triggered request then prompts.

### R76-1 LOW: tick stamps drawn before the anchor tick are kept after it
- Checked: `shiftWindow` (terminal.js:258-280) reuses line elements; `fmtTs` tick mode reads `state.anchorTick`, set by the first ticked row after clear-all.
- Fix: a change of `state.anchorTick` forces a full render of tick-mode panes, as the port-tag column does (terminal.js:661).
- Test: after clear-all a tickless line then a tick-3000 line: the first reads `~-1000` without a manual render.

### R77-1 LOW, owner-pick D-19: a backward host-clock step draws as a repeat
- Checked: plots.js:573 and digital.js:118, :149 nudge any non-increasing host x to `last + 1e-4`; digital.js:109 keeps the live edge at the old high-water.
- Fix (D-19 option A): a backward step larger than 1 s breaks the series (as a tick restart does) and re-bases the host high-water; smaller steps keep the nudge.
- Test: host time steps back 3600 s: the chart breaks and later points draw at their own host x; the lanes' live edge follows.

## Browser leg failures (added 2026-09-25; evidence in `../browser/`)

This batch also owns `host/mcuscope/webui/style.css`.

- F-1: a chart with one channel shown cuts y-axis labels wider than about 31 px (`10000`, `8e+307`); `plots.js:1034` fixes that axis at 46 px. Size the axis to its labels.
- F-2: after a channel or regex filter change, a paused pane's first scroll to the top loads nothing; `terminal.js:389` sets a flag that swallows the next real scroll.
- F-3: the port tag in Digital/Enum lane labels is cut (`ben…` on `pwm_en`); `style.css:317` lets the tag shrink first. The lane name should shrink first.
- F-4: the export dialog's Clock row overflows the dialog at every window width; `style.css:395`, `:461-464`.
- Unit-test what the DOM stub can reach; for pure layout, rerun the check with `~/tt-data/mcuscope-tools/browser/` (its README) and say which script passed.

## Added by owner rulings 2026-09-25

- `.db` session download: pre-check a free export slot with a fetch (the daemon-api batch adds or extends the preflight) and show a queue-full refusal in the UI instead of a failed navigation.
