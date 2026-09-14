# Batch B report: CAN section of the sidebar, web UI UX round 2026-09-14

HEAD: a813ef1cfa461daa3301b4cf9abfbde02126637a (uncommitted working tree on top).

Files: `host/mcuscope/webui/can.js`, `exportdlg.js`, `index.html`, `style.css`, `docs/SPEC.md` 9.1, `CHANGELOG.md`, `host/tests/test_webui.py`, and the JS tests below.

## Per id

- **Item 4, S-F4 per-frame byte diff**: done.
  - `canIngest` ORs a per-byte changed mask into `e.moved` on every frame; the paint consumes it. A length change, a remote frame or a first frame resets it.
  - A paused snapshot keeps its mask (stable across rebuilds); resume zeroes the live masks; rows hidden by the filter or a collapsed group consume theirs at each paint.
  - The 1 Hz tick also repaints while any byte is lit, so a highlight clears on a bus that went silent (before, it stuck until another id sent a frame).
- **Item 7, S-F5 staleness**: done, changed from the recommendation.
  - Recommended `max(3, 5 * period)`: under it a 1 kHz id that stopped still reads fresh for 3 s, the finding's own example.
  - Built: stale past `max(1 s, 5 periods)`, `--crit` past `max(2 s, 10 periods)`. No period yet: stale past 3 s, never `--crit` (one frame is no rate to have missed).
  - Colours: fresh `--text`, stale `--warn`, dead `--crit`.
- **Item 10, S-F1 + L-7 fit at 360 px**: done. Cell padding 4px 5px; `count` moved to the row's hover (`N frames since clear`).
  - Verified in the screenshot: no sideways scroll, `age` visible.
- **Owner, 4 bytes a line**: done. A CSS forced break before the fifth byte span (`nth-child(5)::before`), with the data cell `nowrap`, so 8 bytes is always 4 + 4.
  - Deterministic rather than width-driven, so rows keep one height whatever the payload. The same 4 + 4 applies on a widened sidebar.
- **Owner, fit-to-content in Both**: done. `height: var(--can-h, 45%)` became `max-height`, so the section fits its rows and scrolls past the cap.
  - Drag decision: the divider drag sets the cap. Double-click restores 45 percent.
  - Dragging above the content shrinks and scrolls the table; dragging below it does nothing visible, since the section already fits.
  - Effect on plots: they get every pixel the table does not use. No plot code changed; charts are fixed height and do not need a resize for this.
- **S-F2 fold while empty**: done. `renderCan` toggles `can-empty` on the sidebar from the rendered model, so a cleared paused table stays folded.
  - `initCan` renders once, and `index.html` starts folded.
  - The fold hides only `#canWrap` in the Both view; the CAN view still shows the empty-state copy.
  - Found in the empty-daemon screenshot: before `initCan` rendered, a board that never sends a frame never folded.
- **S-F12 + L-8 headers**: done. `ms` is `period`; every `th` has a title; period and age share units (`ms`, then `s`, then `XmYYs`); `fmtCanAge` no longer prints `1000ms` at 0.9995 to 1 s.
- **S-F14 export fields**: done. `enabledBy` accepts `{field, equals}` and choices accept `[value, text]`.
  - `What` is `Source` with `frame history (capture)` / `table snapshot (on screen)`; ids is disabled under snapshot.
  - Latent bug fixed: every option change called `render()`, which rewrites the clock fields from the saved range.
  - So ticking `changes` wiped typed clock bounds. Option changes now only re-gate.
- **S-F17 id filter**: done, changed wiring. A `#canIdFilter` box in the head matches a substring of the displayed hex (case and `0x` ignored).
  - Changed: the filter is part of the view key, not a `canRowsVersion` bump, because a paused table keys on its frozen version and a bump would not rebuild it.
  - The count reads `N of M ids`; no match shows a `no id contains X` row. The filter survives `clear`.
  - Decision: the snapshot export and the history prefill follow the filter, as "what is on screen". SPEC says so.
  - A single remaining group shows as the plain table, the same rule as unfiltered.
- **S-F18 `Reset`**: done. `clear`, id `canClear`, SPEC updated.
- **L-15 CAN part**: done. Names the line grammar with an example, the `x`/`r` flags and `!can2`.
  - Then `firmware/monitor/INTEGRATION.md` and SPEC 2.5 as plain paths: the UI serves no docs, and a GitHub link would not match the installed version.
- **Head crowding**: the head is `CAN`, count, filter, pause, export, clear. It wraps to a second line when `paused` and `unfilter` both show at 360 px.
- **SPEC 9.1**: columns, units, row hover, 4-a-line data, repaint and highlight rule, age colours, filter, `clear`, empty copy, fit and fold with the drag-cap rule, CAN export `Source` and gated ids.
- **CHANGELOG**: the unreleased highlight entry reworded; filter under Added; layout, staleness, fit/fold, clear/empty/export under Changed; the export clock wipe under Fixed.

Totals: 12 ids done (S-F4, S-F5, S-F1 + L-7, owner 4 bytes, owner fit, S-F2, S-F12 + L-8, S-F14, S-F17, S-F18, L-15 CAN, SPEC/CHANGELOG), 2 changed from the recommendation (S-F5 floor, S-F17 wiring), 0 skipped.

## Tests added

Every new test was mutation-checked: the guarded line was broken and the test failed, then restored.

- `host/tests/webui_js/can_bytediff.test.mjs`:
  - A byte that changes and changes back between paints still lights.
  - 100 frames between paints light only the counter byte and a one-frame blip.
  - A lit byte clears on the next tick with no frame at all.
  - A dlc change lights nothing, including a bit set at the old length; a new shape diffs from its own first frame; an empty payload.
  - An rtr frame between two data frames resets the diff.
  - The fifth byte keeps its leading space for the CSS break.
  - A row revealed by the filter lights nothing that moved while hidden.
  - Paused: 20 churning frames, a second paint and a filter rebuild all keep the frozen highlight.
  - Resume lights nothing that moved while frozen; the next frame diffs normally.
- `host/tests/webui_js/can_age_tick.test.mjs`:
  - 1 kHz at the 1 s and 2 s bounds; once a minute at 300 s and 600 s; no period at 3 s and never dead.
  - A rendered 1 kHz row goes `age-dead` at 2.5 s while a no-period row stays fresh.
- `host/tests/webui_js/can_head.test.mjs` (new):
  - Fold at init, on the first row, and again on the `clear` button.
  - A cleared paused table stays folded while live frames arrive, and opens on resume.
  - The filter input: `0x` and case ignored, a padded extended id, numeric order, and the count text.
  - No match shows its row and keeps the section open; a matching id arriving rebuilds under the filter.
  - A filter typed while paused shows frozen rows only; resume applies it to live rows.
  - The filter survives a clear.
  - A collapsed divider counts matches; a single remaining group is plain.
  - The export prefill and snapshot follow the filter.
  - `Source` and choice texts; ids disabled under snapshot and re-enabled.
  - An option change keeps typed clock bounds.
  - Header texts and titles; `clear` lower case and no `Reset`; the static empty state and fold class match what can.js renders.
- Updated for intended changes: `can_logic.test.mjs` (5 columns, `20ms`, row hover count, empty copy) and `test_webui.py` (`canClear`, `canIdFilter` ids).

## Gates

- `uv run python -m ruff check .`: all checks passed.
- `uv run python -m pytest tests/test_webui_js.py tests/test_webui.py -q`: 11 passed.
- `node --test` over `host/tests/webui_js`: 446 pass, 0 fail (421 before, 25 added).
- Full suite not run (the brief reserves it).

## Screenshots

- Throwaway daemon on 8797 with the review-dir db, confirmed from its `config:` and `database:` lines; Firefox and both daemons stopped by PID; .db, .lock, .toml and logs deleted.
- `batch-b-demo.png`: `mcuscoped --sim`, 360 px sidebar, Both view.
  - The CAN section fits its four rows, with no blank space below them.
  - `0000018A` shows `00 00 00 00` over `00 00 00 xx`; `age` is visible, with no sideways scroll.
  - **All four digital lanes (irq, led, pwm_en, state) now fit below the analog chart**; batch A showed only `irq`.
- `batch-b-cap.png`: 30 extra ids from `can tx`.
  - The section stops at the 45 percent cap and scrolls, and the one-shot ids read amber past 3 s.
  - The digital panel is then back to one lane in view, as expected at the cap.
- `batch-b-empty.png`: empty daemon. The CAN section is folded to its head, and the plots empty state sits directly below it.
- `batch-b-demo.png` predates two late CSS/JS changes (head wrap, init render); neither alters that frame.

## Owner decisions needed

- `count` is now hover-only. It was the column the review judged least watched; say if it should come back, for example under the period.
- Staleness floor of 1 s for ids with a known period (the review said 3 s). A link that stalls for over a second turns every fast id amber briefly.
- The filter also narrows the snapshot export and the history ids prefill.
- The divider drag sets a cap, so dragging below a short table does nothing.
- 4 + 4 data lines stay even on a widened sidebar.

## Manual checks owed

- Nothing needing a click or typing was driven:
  - filter typing and focus widening, and the head wrapping with `paused` and `unfilter` shown;
  - header and row hovers, and the export dialog's disabled ids field.
- Divider drag above and below a short table, and double-click back to 45 percent.
- A table with classic (non-overlay) scrollbars, as on Windows: about 17 px less width. An extended remote id (both `EXT` and `RTR` chips) is the widest id cell and may scroll sideways there.
- Light theme amber and red ages, and the highlight fading on a silent bus.
- Seen, out of scope: the Digital / Enum head still clips its window selector (L-1) and shows a live `pause` with no lanes (L-16); the uPlot legend still costs about 50 px (S-F9).
