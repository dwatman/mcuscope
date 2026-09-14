# Owner-reported web UI fixes (2026-09-14)

HEAD at start: `9240081ab250c059a2fc8fbfb75192837299b657`. Nothing committed.

All four fixed. Gates: ruff clean; `node --test tests/webui_js/*.mjs` 578/578 on the shared tree (batch D in progress included), 521/521 with only these hunks applied to HEAD.

## 1. Regex box jumps to the next line on focus

- Root cause: `.match .match:focus { width: 260px }` grows the flex item's base size, and `flex-wrap` breaks lines on base size, so a full toolbar wrapped on focus.
- A second trap found while rendering: with no `min-width`, the group's automatic minimum follows the input's intrinsic width once max-width lifts, which still wrapped at 520 px.
- Fix (CSS only): the group has `flex: 1000000 0 90px; min-width: 0; max-width: 90px`, and `:focus-within` lifts max-width to 260 px.
  - Line breaking still sees 90 px, so nothing reflows; the grow factor out-bids the spacer, so the box takes free space up to 260 px.
  - The clear-x stays pinned to the group's right end; `:focus-within` keeps the box wide while the x itself has focus.
- Visual check: `regex-layout.png` in this folder (static page on the real style.css, focus emulated with inline `max-width: 260px`, html deleted).
  - 1 pane at 1100 and 520 px, 3 panes at 1100 and 780 px: no row changes line in any focused case.
  - Where the row has little free space (520 px, 780 px narrow panes) the box grows only as far as that space.

## 2. Command bar port select too wide

- Root cause: the auto option read `auto (sim)` and `#cmdPort` was fixed at `width: 96px`, so the label clipped or the select sat wider than its aliases.
- Fix: the auto option reads `(sim)`, or `(auto)` when auto resolves to nothing (no port, or several connected); explicit options stay bare aliases; value stays `auto`.
- `#cmdPort` is now `max-width: 160px`, so the select follows its widest option; the tooltip explains the brackets.

## 3. No timestamp for DBG lines under `tick`

- Root cause: `state.js computeTick` returns a tick only for `!can`, `!p`, `!ps` and `!m @<tick>`; every other line rendered `-`.
- SPEC 2 gives plain lines no tick, and the daemon exposes no per-port offset, so no better anchor exists.
- `rel` and `delta` have no gap: both use `row.ts`, which every line has.
- Fix: an estimate per the orchestrator's rule, in DOM-free `timewindow.js` (`newTickAnchors`, `noteTickAnchor`, `estimateTick`).
  - Anchor: the nearest EARLIER (by id) tick line on the same port; estimate = its tick + host gap in ms, wrapped at 2^32. Shown `~<n>`, zeroed like a real tick.
  - No earlier anchor on that port: `~-`. Gap divider rows keep `-`.
  - Anchors come from every `pushBuffer` row and every history page's served rows; inserted by id, so paged anchors slot below live ones.
  - Bounded: a newest anchor within 1 s that the previous one predicts to 200 ms is skipped (a reset or jump is always kept); cap 10000 per port.
  - Cleared on a capture reset (`resetForDbReset`).
  - Hovering such a line in tick mode places the chart cursor at the estimate (`plots.js xForRow`), matching its column.
- Copy and export: double-click copy writes the raw line as before (it never included a timestamp); exports are daemon-side and unchanged.
- Known limits:
  - A reboot's banner lines before the first post-reboot tick line use the pre-reboot anchor, so they read wrong until one arrives.
  - A history page filtered to dbg only brings no tick lines, so its older lines read `~-` unless an older anchor is already known.

## 4. Digital lanes after clear-all draw the current state as stable

- Root cause: clear-all does drop the lanes, but `drawBits`/`drawEnum` extended the first vertex's level to the window's left edge (`i === 0 ? 0 : X(xs[i])`, `moveTo(0, ...)`).
  - The first post-clear sample therefore drew as held across all 30 s. Analog charts are destroyed and uPlot draws from the first point.
- Fix: DOM-free `laneSegments(xs, win)` in `timewindow.js`; a lane starts at its first sample, clamped to the window, and both draw functions use it.
- Other clears: pane clear is terminal-only; there is no per-chart or digital-panel clear. A paused panel cleared re-freezes at its first post-clear sample and draws nothing earlier.
- Side effect, same rule: a fresh page or a trimmed ring also starts at its first retained sample instead of back-filling the left edge, as charts do.

## Files touched (hunks), for a separate commit

The JS hunks alone are in `/home/daniel/tt-data/owner-fixes.patch` (applies to HEAD). Batch D also edited cmdbar.js, terminal.js, style.css, index.html, SPEC and CHANGELOG; its hunks there are not mine.

- `host/mcuscope/webui/style.css`: the `.match-group` / `.match-group:focus-within` / `.match-group .match` rules with their comment; the `#cmdPort` line.
- `host/mcuscope/webui/index.html`: the `#cmdPort` `title` attribute only.
- `host/mcuscope/webui/cmdbar.js`: `syncCmdMode` comment and `const label` line. Not the `setRadios`/`rovingRadios` hunks (batch D).
- `host/mcuscope/webui/state.js`: header comment and `timewindow.js` import; `tickAnchors` + `noteRowTick` before `pushBuffer`; `pushBuffer`'s tick lines; export list.
- `host/mcuscope/webui/terminal.js`: state.js and timewindow.js import lines; `fmtTs` tick branch; `noteRowTick` line in `loadHistoryPage`. Not the radios hunks.
- `host/mcuscope/webui/api.js`: state.js import line; `tickAnchors.clear()` in `resetForDbReset`.
- `host/mcuscope/webui/plots.js`: state.js and timewindow.js import lines; `xForRow`.
- `host/mcuscope/webui/timewindow.js`: `laneSegments` above `lastAtOrBefore`; the tick-estimate block at the end of the file.
- `host/mcuscope/webui/digital.js`: timewindow.js import line; `drawBits`; head of `drawEnum`.
- `host/tests/webui_js/cmdbar_sole.test.mjs`: the auto-label test and the no-port test.
- `host/tests/webui_js/terminal_logic.test.mjs`: state.js import line; timestamp-column test.
- `host/tests/webui_js/api_db_reset_misfire.test.mjs`: imports; the new-capture-token test.
- New: `host/tests/webui_js/terminal_tick_estimate.test.mjs`, `host/tests/webui_js/digital_clear_segments.test.mjs`.
- `docs/SPEC.md` section 9: the cmd port label line; a tick-estimate sub-bullet under "Lines are color-coded"; a lane-start bullet after "One vertex per value change".
- `CHANGELOG.md` Unreleased: amended the `auto` label line and the regex box line (both from this cycle); one Added line (tick estimate) and one Fixed line (lanes after clear-all).

## Tests added

- cmdbar_sole: `(mcu)`, `(auto)` with none or two connected, explicit options equal their alias. Mutant (`()` for none) killed.
- terminal_tick_estimate (10): no anchor, other port only, anchor after the line, reboot, reset inside the thinning gap, out-of-order paging, 2^32 wrap, non-lines.
  - Also through the column: `~-`, real tick, `~750`, marker without @tick, other port; and a history page anchoring its own and newer lines.
- api_db_reset_misfire: a new capture token drops old anchors.
- Mutants killed: any-anchor lookup, cross-port lookup, thinning ignoring resets, history not noted, reset keeping anchors, no wrap.
- digital_clear_segments (6): DOM-free start at own time, pre-window level still reaches the edge, zoom before/straddling the first sample, wider window, empty.
  - Through the panel: clear-all then draw calls (bits stroke and fill, enum envelope start at 200 px), paused clear then resume, tick mode.
  - Mutants killed: left-edge extension in `laneSegments` (5 fail), `drawBits` stroke from x=0 (1 fail).

## Manual checks owed (real browser, against the simulator)

- [ ] Regex box: focus in 1 and 3 panes, nothing jumps line; click the x while widened clears and keeps focus (also in Chrome, where button focus differs).
- [ ] Command bar: port select width with `sim` only, with two ports, with a long alias; tooltip text.
- [ ] Tick base: sim debug lines read `~n` rising in step with `!ps` ticks; scroll-to-top paging on a paused pane; hover a dbg line moves the chart cursor.
- [ ] Digital lanes: clear-all while live and while paused; lanes start at the first new sample; zoom and window buttons after the clear.

## Leftovers

- Throwaway worktree `/home/daniel/tt-data/wt-fixes` was not removed (recursive delete needs your confirmation): `git worktree remove --force /home/daniel/tt-data/wt-fixes`.
- Headless Firefox profile `docs/review/2026-09-14-webui-ux/ff-shot-owner/` (my own, so batch D's `ff-shot` stayed untouched) also left for the same reason.
- My first headless attempt with the shared `ff-shot` profile hung; its detached Firefox (PIDs 182003, 182009, 182091) was killed by PID.
