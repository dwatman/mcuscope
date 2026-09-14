# Batch C report: analog plots, digital/enum panel, sidebar layout

HEAD: ef59a5ed49856d95d46e646a3755390d185618a9 (uncommitted working tree on top).

Files: `webui/plots.js`, `digital.js`, `chrome.js`, `timewindow.js`, `app.js`, `can.js`, `pane.js`, `terminal.js`, `index.html`, `style.css`, new `webui/layout.js`.
Also `mcuscope/sim.py`, `tools/mcu_sim.py`, `docs/SPEC.md` (7, 9.1, 9.2), `CHANGELOG.md`, and the tests below.
`cmdbar.js`, the CAN `.byte` rules and the cmdbar/bytediff tests were edited by the orchestrator in parallel, not here.

## Per id

- **S-F6 (item 6)**: done, the right fix.
  - Charts keyed `port|s<sid>` / `port|adhoc`, lanes `port|name`, packed groups per port; the history seed keys by the channel's `port`.
  - Chart heads and lane gutters name the port (in its colour) once more than one port has contributed; group headers add `on <port>`.
  - A chart export passes `port=`; lanes from two ports offer a `Port` choice and export that port's shown lanes.
  - Also fixed: a redefined stream's new unit now reaches the chip (it kept the first unit).
  - Kept: the colour store stays keyed by name (see owner decisions).
  - Limit, now in SPEC 9.2: unfiltered `/plot/channels` names one port per name, so the seed restores only that port's history for a shared name.
- **S-F3 (item 9)**: done, changed placement.
  - The zoom chip (`1.20 s ×`) is a lit segment inside every window selector rather than a separate head chip, to save width at 360 px; no span button is lit meanwhile.
  - The chip and every double-click go through one exit (clear the zoom, resume all).
  - A window button clicked while zoomed leaves the zoom and keeps the freeze; resuming the lanes alone now drops the zoom, as resuming a chart already did.
- **S-F7**: done. Ruler row under the lanes plus gridlines, from `laneWindow`; `axisTicks` in timewindow.js gives clock-friendly steps shared with the chart x axis.
- **S-F8**: done. `colorFor(name)` hands out palette slots per name on first sight; both call sites lost their index.
- **S-F9 + L-4**: done. uPlot legend off; each chip shows name, value (under the cursor, else the newest drawn) and unit. Measured: the demo chart lost about 50 px.
- **S-F10**: changed. With the legend gone there is no `Value:` label; the cursor line carries a `fmtTime` tag, and `TIME_AXIS_LABELS` feeds `#plotXLabel` and the ruler.
- **S-F11**: done. A soloed channel's axis is labelled with its unit; an empty or whitespace unit gets no label.
- **S-F13**: done. `drag: zoom, dbl-click: reset` in the Plots head, terminal-line hover in its title; hidden until a chart or lane exists.
- **S-F15 + D-F12**: done, one validated key `mcuscope.layout` instead of three: width, expanded, hidden, CAN cap (as a percent).
  - A width from a wider window is clamped; expanded is 60 percent of the current window; restore returns to the dragged width, not 360.
- **S-F16**: done. A collapsed head lists its shown channels (ellipsis, full list in the title).
- **S-F19**: done. The delta button's title, and `x: host (delta is terminal only)` in the Plots head.
- **L-1**: done. Head controls wrap as one group and padding is tighter; the Digital / Enum head puts its controls on a second line at 360 px instead of clipping.
- **L-5**: verified, nothing left. `buildUplot` gives every series its own auto `y<i>` scale; the value sits beside its unit in the chip, and a solo axis names the unit.
- **L-6**: SPEC 2.5 declares no stream name, so the title is click-to-rename per browser, keyed by port and stream, max 32 chars; empty or whitespace restores the default.
- **L-15 plots**: done, then cut to one line by the owner (below).
- **L-16**: done, cause corrected. The head was already `hidden`, but `.plot-head { display: flex }` beat `[hidden]`. Also fixed: "No plot data yet" beside digital-only lanes.
- **Item 3, demo look**: done under `--demo` only.
  - Pinned: `test_poll_events_narrates_with_no_command_typed` needs a `state:` step within 5 s, so the sim core's periods stay for `--plot`.
  - `--demo`: tri 10 s, ftest 24 s, state 6 s steps (narration follows), led 2 s, irq 300 ms every 2.5 s, pwm_en 1 s in 3 s. Signals moved to a pure `_plot_signals(tick, demo)`.
- **Item 4**: not moot. Two charts with the CAN table at its cap push the lanes out of view (`batch-c-ports.png`).
  - The Plots head shows `↓ N below`, naming the widgets and scrolling to the first.
- **Item 5**: SPEC and CHANGELOG (Changed, Added, Fixed) updated.
  - SPEC 7: the `--demo` signals. 9.1: modules, layout persistence, delta, empty states, folded CAN head.
  - SPEC 9.2: port keying, zoom chip, chips, ruler, titles, hint, fold cue.
- **Owner, one-line empty states**: done.
  - Pane (`emptyPaneText` now returns `{text, title}`), CAN body and plots each show one short line; the grammar, the `--sim` route and the doc pointers are the tooltip.
  - `.empty-state` is nowrap with ellipsis and tighter padding.
  - The digital panel has no empty state: its head stays hidden until the first lane.
- **Owner, folded CAN head**: done. It reads `no frames yet` with the grammar tooltip; the id filter, `export` and `clear` hide; pause and the paused tag stay. The CAN view keeps the body line.

Totals: 24 done (3 changed from the recommendation: S-F3 placement, S-F10, L-16 cause), 1 verified with nothing left (L-5), 0 skipped.

## Tests added

All new JS tests were mutation-checked: 29 mutations, each caught by its test, plus one equivalent mutant.

- `webui_js/plots_ports.test.mjs`:
  - Same sid and name on two ports gives two charts and four lanes, with no sample crossing over.
  - The port tag appears only with a second port, on charts, lanes and group headers, and goes after a clear.
  - A port that detaches and re-attaches rejoins the same chart and lane objects.
  - A stream redeclared with new fields and units on one port leaves the other port's chart unchanged; the chip shows the new unit.
  - The seed keeps same-named channels apart; a chart export carries `port=`; the lane export's Port choice picks names; one port offers no choice.
- `webui_js/plots_zoom_chip.test.mjs`:
  - A zoom lights no span and shows the chip on every head, including one built mid-zoom.
  - A window click leaves the zoom and keeps the freeze; the chip on one chart releases every surface.
  - Zoom while paused, then resume all; lanes resumed alone; a chart resumed alone; double-click on the lanes.
- `webui_js/plots_chrome.test.mjs`:
  - Legend off; chip values for a cursor on a gap, off-chart hold-last, and an index past the data.
  - Solo axis unit, and a whitespace unit getting no label.
  - Collapsed names follow hide and new channels. Rename trims and bounds; empty, whitespace or the default leaves nothing stored; a rename is per port.
  - Empty state and hint follow lanes as well as charts; the `.plot-head[hidden]` and one-line `.empty-state` CSS rules; the plots copy against its tooltip.
  - The fold cue counts a 10 px peek as below; the ruler's labels and time-base name.
- `webui_js/layout.test.mjs`:
  - Corrupt JSON and wrong types per field; `canCap` range edges; a width from a wider or a narrower window.
  - Expanded in a small window; titles with prototype keys; the fold threshold.
- `webui_js/app_layout.test.mjs`:
  - Boot applies the stored layout; reopen, expand, restore, double-click and drag each save.
  - A pointerup with no drag saves nothing; a drag past the terminal clamps before saving.
- `webui_js/app_layout_corrupt.test.mjs`: a corrupt layout boots at the defaults.
- `webui_js/timewindow.test.mjs`: tick steps and bounds, anchor alignment in tick and rel, sub-second decimals, no `-0`, empty windows, zoom span text, the delta label.
- `webui_js/can_head.test.mjs`:
  - The folded head's line and hidden controls across first frame, clear, paused-and-cleared, and resume.
  - A no-match filter is not empty; the CAN-view CSS; the static head line and tooltip match can.js.
- `webui_js/chrome.test.mjs`: palette slots per name; the zoom chip on every selector; a span click leaves and the chip exits.
- `webui_js/terminal_empty_state.test.mjs`: text and title asserted separately, the tooltip is not stale on a reused element, and every pane message fits one line.
- `test_sim.py`:
  - `--demo` signals read at 30 s and move at 5 s (edges per window, shortest level, enum step, analog period); mutation-checked against the `--plot` set.
  - Demo narration steps with the slow enum while `--plot` keeps 1 s.
- Updated for the new keys: 18 existing JS files (literal `p1|` / `-|` keys, `digitalIngest(port, ...)`, `layout.js` in the smoke list), and the CAN/pane copy asserts.

## Gates

- `uv run python -m ruff check .`: all checks passed.
- `uv run python -m pytest tests/test_webui_js.py tests/test_webui.py tests/test_sim.py tests/test_plot.py -q`: 87 passed.
- `node --test` over `host/tests/webui_js`: 503 pass, 0 fail.
- Also `tests/test_daemon_startup.py tests/test_sim_tcp.py tests/test_cli.py -k "plot or sim or startup"`: 29 passed. Full suite not run.

## Screenshots

Throwaway daemon on 8797 with the review-dir db, `config:` and `database:` lines confirmed each start; daemons, sim and Firefox stopped by PID; .toml, .db, .lock and logs deleted.

- `batch-c-demo.png`, `batch-c-light.png`: `mcuscoped --sim`, 360 px sidebar, Both view, dark and light (`ff-shot-light` profile, user.js override).
  - One chart with the chips and no legend; tri, ramp and ftest are separate slow traces.
  - All four lanes, the ruler on 10 s steps matching the chart, and `ARMED` fitting in its segment.
- `batch-c-ports.png`: a plain `mcu-sim --plot` attached as `bench`. Taken before the head-control grouping and the count nowrap fix, so `9 channels` wraps there.
  - Shows `stream 0 bench` and `ad-hoc (!p) bench` beside the demo's chart, and `↓ 2 below` naming the other port's chart and the lanes.
- `batch-c-empty.png`: empty daemon, taken after both owner changes.
  - Folded CAN head reads `CAN no frames yet` with only pause; one-line plots empty state; no Digital / Enum head.
- The demo, light and ports shots predate the one-line empty states; none of them shows an empty state.

## Owner decisions needed

- Colour stays keyed by name: two boards' `temp` share one colour, with the port tag telling them apart. Keying by port would drop colours saved by name.
- The zoom chip sits inside the window selector; a window click leaves the zoom but stays paused; resuming the lanes alone now drops the zoom.
- Hiding `export` on an empty CAN table also hides the frame-history export after a `clear`, until a frame arrives.
- Seeding a name declared on two ports restores only the newest port's history; a per-port `/plot/channels` seed would need one request per attached port.
- Restore after expand returns to the dragged width, not 360; a dragged width ends the expanded state.
- The Digital / Enum head takes two lines at 360 px (title plus count do not leave room for the controls).

## Manual checks owed

- Chart rename: click, type, Enter, Escape, blur, and keyboard focus returning to the title.
- The zoom chip's look and click, and a window click while zoomed.
- Cursor time tag on a chart, and its flip near the right edge.
- Chips updating on another chart's hover: this assumes uPlot fires `setCursor` hooks on synced charts.
- The fold cue's smooth scroll; the lane gutter port tag's look (below the fold in the ports shot).
- Sidebar drag, expand, hide and CAN cap surviving a reload, and in a narrower window.
- The solo axis unit label; the light-theme chip and cue colours.
- The empty-state tooltips (`cursor: help`), the folded CAN head tooltip, and the CAN-view body line.
