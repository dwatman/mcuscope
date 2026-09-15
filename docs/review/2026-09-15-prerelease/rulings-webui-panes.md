# Owner rulings: web UI panes (D-2, D-3, D-9, shift-click window)

New tests, all under `host/tests/webui_js/`:

- `rulings_panes_tickclock.test.mjs` (TC): the DOM-free clock, 12 tests.
- `rulings_panes_reset.test.mjs` (R): charts, lanes, draw and hover across a reset, 9 tests.
- `rulings_panes_export.test.mjs` (X): D-2 and D-9, 9 tests.
- `rulings_panes_window.test.mjs` (WG): the shift-click span, 5 tests.

No existing test needed an edit.
`uv run python -m pytest tests/test_webui_js.py -q`: 2 passed (the whole node suite, 0 failures).

## D-3: tick axis across a reset or wrap

Design, and one deviation from the brief:

- Detection is per chart and per lane, in each member's own samples (one stream, so tick order holds).
- The offset is not per member but per port: an epoch `{host, offset}` in `tickClocks`, owned by `digital.js` and shared with `plots.js`.
- Why: with per-member offsets, a chart or lane born after the reset has no previous x and draws at raw ticks.
  - A lane like that sits off screen, because every lane shares one right edge.
  - A board flashed with a new enum channel is the realistic case.
  - A terminal-row hover would also need a different x for every chart.
- Offset: the first sample after the jump lands at the previous sample's x plus the host time elapsed (a host step back counts as 0).
- A sibling that sees the same reset reuses the epoch if it starts after that sibling's previous sample and at most 100 ms past its own sample.
- A member born in the same serial read just before the sibling that saw the reset adopts the epoch on its next sample, with a break.
- Slack: `TICK_JUMP_SLACK_MS = 100`.
  - A producer reorders ticks by milliseconds, while a reset takes the clock back by the board's uptime.
  - A board resetting within 100 ms of boot stays glued by the nudge (known ceiling).
- Epochs are capped at 1000 per port.
- `clearAllDigital` clears the clocks (clear-all and capture reset both call it): the axis starts over at raw ticks.

Changes:

- `timewindow.js:232-289`: `TICK_JUMP_SLACK_MS`, `newTickClocks`, `tickOffsetAt`, `continueTick`.
- `plots.js:532-541`: `addSample` maps the tick through `continueTick` and pushes a gap point (every channel null) on a restart. `plots.js:426` adds `prevTick`.
- `plots.js:1121-1123`: `xForRow` (terminal-row hover) adds `tickOffsetAt(port, row.ts)` to the line's tick or estimate.
- `digital.js:40`: `tickClocks`.
- `digital.js:72-97`: per-lane mapping; a null vertex at the last pre-reset sample; the shared edge takes the drawn tick (with a fallback when every lane of the sample is capped); vertex push moved to `pushVertex` (`:100`).
- `digital.js:557-563` (`drawBits` lifts the pen at a null vertex, no edge into it) and `:575` (`drawEnum` skips it).
- `digital.js:807`: the clear.

Tests: TC 1-12 (repeat and in-slack step, reset, 2^32 wrap, two resets with hover by host time, host step back, sibling sharing, born after reset, other port untouched, same-read adoption, no re-adoption on a second reset, host-ordered insert, cap, clear); R 1-9 (chart gap and continuity, repeated-tick nudge, lane null vertex and moving edge, bits pen and enum bus drawn on a recording canvas, terminal hover before/after the reset and for a tickless line, reset while paused then resume, stream born after the reset, clear-all after a reset, capped-lane edge).

## D-2: shown window under a drag zoom

- `plots.js:1213-1229` `chartShownWindow`:
  - Host base, zoom standing on the chart: the zoom's `min`/`max`.
  - Tick base: the host times of the first sample at or after `z.min` and the last at or before `z.max` (as the unzoomed path maps its edges); null when no sample falls inside, so the mode is not offered.
- `digital.js:372-397` `digitalShownWindow`: the same for the lanes; tick edges are interpolated between vertices by `hostAtTick`, clamped to the frozen edge.
- A zoom left by a window button falls back to the span; a double-click resumes, so there is no shown window until the next pause, which exports the span.
- Tests: X 1-7 (host chart and lanes; tick chart with a sample on the right edge; tick lanes interpolated; zoom past the edge; zoom with no sample; zoom on the continued axis after a reset; leave by button, double-click, pause again).

## D-9: panel paused before its first lane

- `digital.js:168`: a lane born while paused always gets an empty snapshot; the re-anchor in `digitalIngest` is gone, so `digitalFrozenId` keeps its pause (or clear) value.
- `digital.js:638-642` `digitalRightEdge` and `:373` `digitalShownWindow`: while paused, only the frozen edge counts; none means nothing drawn, no ruler, no shown window.
- Tests: X 8 (pause at id 100, lane at 500: watermark 100, empty snapshot, null edge, empty readout, shown not offered, resume shows both vertices), X 9 (clear-all while paused).

## Shift-click window span as group state

- `chrome.js:92-96,126`: `groupSecs`, set only by a shift-click (on any chart or on the lanes); `groupWindow()` exported.
- `plots.js:428`: a new chart takes `groupWindow()` instead of `PLOT_WINDOW_DEFAULT`.
- Survives clear-all and a capture reset (module state neither touches), matching the pause-all latch and the lanes' own window, which a capture reset also keeps. A page reload returns to 30 s.
- Tests: WG 1-5 (default before any shift-click; plain click not inherited; new stream inherits and lights its button; shift-click, pause, clear-all, new stream; shift-click on the lanes).

## Revert-verify

Script `~/tt-data/rulings-webui-panes/revert.py`: each mutant was applied to a copy-backed file, the four rulings test files were run, and the file was restored from the copy (md5 checked). Raw output: `revert.out`.
40 of 41 mutants killed.

| Mutant | Result | Killed by |
|---|---|---|
| T1 slack dropped (`tick < prev.tick`) | killed | TC 1, R 2 |
| T2 host-gap clamp removed | killed | TC 5 |
| T3 sibling epoch never shared | killed | TC 6, R 1, R 6 |
| T3b share tolerance removed | killed | TC 6 |
| T4 `e !== not` guard removed | killed | 5 tests |
| T5 same-read adoption removed | killed | TC 8 |
| T5b adoption without break | killed | TC 8 |
| T6 new member ignores epochs | killed | TC 7, R 7 |
| T7 epoch cap removed | killed | TC 11 |
| T8 unsorted insert | killed | TC 10 |
| T9 `tickOffsetAt` takes newest epoch regardless of host | killed | 4 tests incl. R 5 |
| P1 chart gap point removed | killed | R 1, R 6 |
| P2 chart x back to raw tick | killed | 4 tests |
| P3 chart `prevTick` never stored | killed | R 1, R 6 |
| P4 hover offset removed | killed | R 5 |
| P6 host zoom ignored (chart) | killed | X 1 |
| P7 tick zoom min ignored | killed | X 2, X 5, X 6 |
| P8 tick zoom max ignored | killed | X 2, X 5, X 6 |
| P9 right-edge sample excluded (`>=`) | killed | X 2 |
| P10 empty tick zoom returns a window | killed | X 5 |
| P11 new chart back to 30 s | killed | WG 3-5 |
| C1 plain click sets the group span | killed | WG 2, 3, 5 |
| C2 shift-click does not set it | killed | WG 3-5 |
| Dg1 lanes' edge back to raw tick | killed | 4 tests |
| Dg2 lane null vertex removed | killed | R 3, R 4, R 6 |
| Dg3 lane x back to raw tick | killed | R 3, R 7 |
| Dg4 null vertex at the post-reset x | killed | R 3 |
| Dg5 bits pen not lifted | killed | R 4 |
| Dg5b bits edge drawn into the gap | killed | R 4 |
| Dg6 enum bus drawn for the gap | killed | R 4 |
| Dg7 capped-sample fallback removed | killed | R 9 |
| Dg8 D-9: lane snapshot only with a frozen edge | killed | X 8, X 9 |
| Dg8b D-9: re-anchor restored | killed | X 8, X 9 |
| Dg9 D-9: edge falls through to live | killed | X 8, X 9 |
| Dg10 D-9: shown window falls through to live | killed | X 8 |
| Dg11 host zoom ignored (lanes) | killed | X 1 |
| Dg12 tick zoom ignored (lanes) | killed | X 3 |
| Dg13 edge clamp in `hostAtTick` removed | killed | X 4 |
| Dg14 `digitalPaused ?` guard on the lanes' zoom | SURVIVED | equivalent, see below |
| Dg15 clock clear removed | killed | R 8 and 3 more |
| Dg16 lane `prevTick` never stored | killed | 4 tests |

Dg14 is equivalent for the same reason as F-24.
The zoom is set only in `onSelect`, which pauses everything next, and the digital panel resumes only through `setDigitalPaused`, which leaves the zoom.
The guard stays so the export picks the same window `laneWindow` draws.
A clock clear duplicated in `clearAllCharts` was removed rather than kept as an untestable twin: clear-all always calls both.

## Manual checks (browser)

- D-3, tick base, a board reset by hand mid-stream:
  - [ ] The chart trace shows a break at the reset (no stepped join) and continues to the right.
  - [ ] The lanes break and the ruler keeps scrolling.
  - [ ] A hover over a post-reset terminal line lands the cursor on post-reset samples.
  - [ ] The chart cursor at the gap point reads `--`.
- D-3: the terminal's tick column still shows the raw tick, so after a reset it reads lower than the cursor tag on the charts. Not changed here; say whether it should.
- D-2:
  - [ ] Drag a zoom and export "shown window" (host and tick base): the CSV spans the zoom.
  - [ ] Press a window button, export again: the CSV spans the selector's window.
- D-9:
  - [ ] Pause all before any enum or bits stream exists, start one: the Digital head appears, the lanes stay blank with no ruler.
  - [ ] Resume: the lanes fill.
- Shift-click:
  - [ ] Shift-click 5m, clear all, start a new stream: its chart comes up with 5m lit.

## SPEC and CHANGELOG

- SPEC 9.1, export dialog "shown window": adds the zoom range and the lanes' interpolated tick edges.
- SPEC 9.2:
  - Shift-click reworded to a group state that a later chart inherits; a plain click sets its own panel only.
  - Time base: the reset/wrap rule (100 ms slack, host-gap continuation, break, shared by later members and the hover, dropped by clear-all).
  - Digital panel: D-9 behaviour.
- CHANGELOG `[Unreleased]`:
  - Added: the shift-click line extended.
  - Fixed: the shown-window line extended with the zoom; new lines for D-3 and D-9.

## Hand-off (outside my files)

- `api.js:303` sizes the history seed from `PLOT_WINDOW_DEFAULT`, so after a shift-click to 5m the seed after a capture reset still asks for 30 s plus the channel's idle time.
- Using `groupWindow()` from `chrome.js` there would match, if wanted.
