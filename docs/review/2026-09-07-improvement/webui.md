# Web UI and simulator leg (diff e573e93..6f1441d)

Run in-session (the agent launch was classifier-blocked twice). Mutations ran in a worktree at 6f1441d (`/tmp/review-2026-09-07/mut_webui.py`); the sim was driven with `drive_sim.py`.

## Findings

| id | sev | where | claim | verified |
|---|---|---|---|---|
| W1 | MED (usability) | terminal.js `loadHistory` (`if (!step.rows.length) return;`) and the scroll handler | A page the pane's filter empties prepends nothing and the scroll offset stays at 0, so no further scroll event fires; the walk stalls until the user scrolls down and up again. With a narrow regex every top hit may serve an empty page. | Read: the fetch is only triggered by a scroll event and the empty page moves nothing. |
| W2 | LOW | terminal.js `historyIdTo`, `loadHistory` | A pane cleared with "clear" (view only) refills with the cleared lines on the next top hit: the history walk has no floor at `pane.clearId`. | Read: `historyIdTo` floors at id 1 and the request carries no `since_id`. |
| W3 | LOW (test quality, class 28) | tests/webui_js/terminal_delta_mark.test.mjs "a render refills it" | Removing the per-render refill in `render` does not fail the test. | Mutation survived. |
| W4 | LOW (test quality) | tests/webui_js/can_age_tick.test.mjs "an idle tick updates the age cells without rebuilding" | Making the idle tick call `renderCan()` does not fail the test. | Mutation survived. |
| W5 | LOW (test quality) | tests/test_sim.py marker test | The 15 s period is unpinned: a 1.5 s period passes. | Mutation survived. |
| W6 | nit | plots.js `onSelect` `if (!(max > min)) return;` | The inverted case cannot happen (uPlot's x scale is not inverted); the guard is the NaN guard, and the test named "inverted" pins nothing. | Mutation survived. |
| W7 | LOW (perf, noted by the author) | terminal.js `updateShown` | With a pattern set every render walks the source ring (5000 rows) for the total; at 30 renders/s per paused pane on scroll. | Read. |

Ruled out: `<mark>` is built from text nodes, no HTML path (class 34/XSS). The copy gesture is on the pane's scroller, the zoom reset on the chart canvas: no collision. `fmtDelta` at row 0 gets `undefined` and prints `+0.000s`. `chartZoom` is dropped by `setTimeMode`, `setChartPaused(false)` and stays through a legend rebuild (`buildUplot` reads it via `xRangeFor`). `currentData` with a zoom keeps `lo < hi` for any range. `_due_beats` caps at `PERIODIC_MAX_BURST` and re-anchors (class 36). `--flap` returns at the loop top before a send, and the listener's `finally` closes the socket.

Sim drive: `mcu-sim --tcp-port 9931 --flap 2`: two sessions of 44 whole lines each ended by EOF at 2.0 s, the last chunk newline-terminated, reconnect served a fresh simulator (`sim alive n=1` again). Marker: `!m @15002 sim marker 1` after 15.0 s on a plain sim.

## Sweep verdicts

- Class 6 (2 producers): `onSelect` rejects a non-finite range through `!(max > min)`; `currentData` slices existing gated arrays (complies).
- Class 11 (1 codec): the sim's marker goes through `p.format_marker` and `parse_marker` accepts it (complies, pinned in test_sim.py).
- Class 16 (3 loops): `loadHistory` drops rows without a numeric id one at a time; `ageCan` skips a missing cell; `poll_events` marker loop is bounded (complies).
- Class 23 (3 writers of a held view): `buildUplot` on a legend toggle re-slices the frozen snapshot (complies); `setTimeMode` drops the zoom but keeps the pause (complies); the history prepend writes `pane.rows` of a paused pane by design and the scroll offset moves with it (complies, pinned).
- Class 25 (2 groups): the new pane template carries `tabindex`/`role`; `syncTimeSeg`, `setCmdMode`, `setView` set `aria-checked` on every member (complies).
- Class 26 (1 held view): history rows come from the capture, not the ring, and are dropped on rebuild rather than re-derived (complies).
- Class 27 (2 doubles): the DOM stub does not clamp `scrollTop` to the extent, so the two-render sequence in `loadHistory` is only shape-tested (exempt, manual); the fake `/lines` in the history tests answers the real envelope (complies).
- Class 34 (0 sites): no `JSON.parse`, `= {}` or wire-keyed subscript in the JS diff.
- Class 36 (1 site): the marker beat uses `_due_beats` (complies).

## Mutation verification

| mutation | failed |
|---|---|
| history: short page does not end the walk | yes |
| history: next bound below kept rows, not the served page | yes |
| history: no divider at the budget | yes |
| history: idTo ignores a gap oldest row | yes |
| history: scroll offset not moved | yes |
| history: port/chan not sent | yes |
| history: refused match not retried | yes |
| history: busy never released | yes |
| history: rebuild keeps pages | yes |
| history: rows not re-filtered | yes |
| delta: sign wrong | yes |
| delta: prev is the DOM window's first row | yes |
| mark: never marks | yes |
| mark: not charged to the budget | yes |
| mark: render does not refill | no (W3) |
| readout: total is rows only | yes |
| zoom: select does not pause | yes |
| zoom: currentData ignores the zoom | yes |
| zoom: inverted selection accepted | no (W6) |
| zoom: survives another mode | yes |
| zoom: resume keeps it | yes |
| zoom: right margin dropped | yes |
| can: interval 500 | yes |
| can: idle tick rebuilds | no (W4) |
| sim: flap deadline ignored | yes |
| sim: marker period 1.5 | no (W5) |
| sim: marker without tick | yes |

## The two questions

Q1, least confident: uPlot's cursor sync replays mouse events to sibling charts, so a drag on one chart may fire `setSelect` on every synced chart and pause them all; the stub has no uPlot, so this is manual. Second: the browser's `scrollTop` clamp between the two renders in `loadHistory`.
Q2, the gap: the round tested paging against a fake `/lines`; nothing drove the real endpoint with `order=desc&id_to&chan&match` from the browser's parameter shape, and `chan` is appended once per channel while the daemon may read one value. Checked after: the daemon's `/lines` takes `chan` as a repeated query parameter (the backfill already sends it that way).
