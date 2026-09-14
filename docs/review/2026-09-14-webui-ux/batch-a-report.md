# Batch A report: web UI UX round 2026-09-14

HEAD: 214980660d9fbde01705f29a64cb8cfd4971bbd8 (uncommitted working tree on top).

## Per id

- **Item 1, demo shows one analog chart**: done, as corrected by the owner three times.
  - New simulator flag `--demo` (renamed from an interim `--plot-demo` once it also covered CAN; never shipped). It implies `--plot`, drops the ad-hoc `!p` stream (typed stream 0 stays), and cuts standing CAN to 0x100, extended 0x18A, remote 0x400 and 0x610 on bus 2. `--plot --demo` is still the demo set.
  - Default chosen: `mcu-sim` with no flag is unchanged (it never plotted, so its default is not the demo), `--plot` keeps the full set every test uses, and `mcuscoped --sim` passes `--demo`.
  - `ramp` wraps at 256 in the sim core, since no test pins its range or values (grep of tests and tools). It counts one step per 20 Hz sample (`(tick // 50) % 256`, 0 to 25.5 mA over 12.8 s) rather than `tick & 0xFF`, which would be a 4 Hz sawtooth of five samples.
  - Docs: SPEC 7 (`--demo` bullet), README demo sentence, SCREENSHOTS "Composing the frame" (temporary sim.py edit removed, stale "sidebar defaults to can" note fixed). The ai-guide and SPEC 4 table list no sim flags, so they are unchanged.
- **Item 2, daemon startup notice**: done. Before the capture lock, stdout prints `config: <path>` or `config: <path> not found, using defaults`, then `database: <path>`; the same lines go into the startup log. Covers `--config`, `MCUSCOPED_CONFIG` and the default path. SPEC 3.3 says so.
- **D-F1**: done. Tooltip names `--host 0.0.0.0` and `MCUSCOPED_TOKEN`, identical in index.html and `DAEMON_TITLE`.
- **T-F1 + D-F3 + L-14**: done, with changes.
  - Copy chosen in DOM-free `pane.js emptyPaneText`: no ports (attach or `--sim`), waiting for the first line, cleared, no channels ticked, N lines none on the ticked channels, no lines from <port>, N in scope none match the regex. Re-derived once a second for empty panes, since non-matching rows never re-render a pane.
  - Changed: the reviewer's cleared copy said "scroll up to pull older lines", but `historyIdTo` returns null for an empty pane and the clear floor forbids refilling, so the copy says the capture keeps every line instead.
  - L-14's empty daemon still shows the `daemon start` sys line, so its pane is not empty; `no ports attached` in `#ports` (not shown while the daemon is unreachable) covers that screen.
- **D-F5**: done as the full persistent strip, with one change. The strip lives in the header as a full-width flex line, so it needs no grid row. It is replaced by the next failure, closed by its `x`, and cleared by the next detach, disconnect, reconnect or session action that succeeds. Changed from "cleared on the next successful poll": every action polls straight after, so that rule would erase each failure immediately. The chip still flashes 2.5 s but no longer rewrites its title.
- **T-F3**: done. The option reads `auto (sim)` when auto resolves to a port, refreshed in `syncCmdMode` on every poll; the value stays `auto`.
- **T-F2**: done (`.chip .meta.rate` reserved 6ch, tabular).
- **T-F4**: done. `filterPaneTo` stashes the replaced pattern; unfilter restores it on every pane still showing the clicked pattern. A second click keeps the first stash, and a pane edited by hand since is left alone. Tooltip reads "Restore the pane's previous filter".
- **T-F5**: done, redraw only when `hidden` actually flips.
- **T-F6**: done with a change: `rx N/s` and a total-across-ports title, but the box is 10ch rather than 9ch so a 5-digit rate does not shift the chips.
- **T-F7**: changed. The hint is in the pane footer rather than a first row: a non-row element above the virtualized list breaks its fixed 18 px offset math and jumps the view on pause. Paused panes read "scroll to the top for older lines", "loading older lines..." or "no older lines to load" (from `historyIdTo`); live panes read the T-F8 copy.
- **T-F8**: done. The footer reads `dbl-click a line to copy` and the 5000 cap moved to the `.shown` title.
- **T-F9**: done (260 px on focus; title names the dialect, case sensitivity and two examples).
- **T-F10**: done (italic, `→ ` prefix).
- **T-F13**: done.
- **T-F14 + L-11**: done.
- **T-F15**: done, copy only.
- **T-F16 + L-17**: partly skipped. The command input is disabled with "attach a port to send commands". The Marker button stays enabled because the finding does not hold for it: `POST /marker` accepts no port (server.py, SPEC 3.5).
- **T-F17 + L-19 brand**: done. The brand tag shows the daemon version.
- **L-9**: done, cause corrected. The third `sim` is the port's `target` (the sim answers `OK monitor` as `sim`), not a rate label. The target span is omitted when it equals the alias; the hover still names it.
- **L-10**: done. The port tag shows only while more than one port is attached, and panes re-render whole when that flips.
- **L-12**: done as a title on the glyph, keeping `>` and `$`.
- **L-13**: done (`visibility: hidden`).
- **L-18**: done (light theme only).
- **L-19 daemon chip**: done. Uptime and db size moved to the chip's hover. The 1 Hz local uptime tick was deleted, since nothing visible needs it now. The size stays in the bar only once the cap has trimmed lines, as the existing warning.
- **D-F10**: done (`::after` overlay, about 20 by 24 px).
- **D-F16**: done; the title also names `mcu daemon stop`.
- **SPEC 9.1**: bar line 21 rewritten (no baud or device), "errors shown inline" replaced by the strip rule, port tag rule, empty state and footer hint, capture size location, command box (auto label, disabled input, timeout box), unfilter restore, marker acknowledgement. Also SPEC 3.3 (startup notice) and SPEC 7 (`--demo`).
- **CHANGELOG**: Changed, Added and Fixed entries under Unreleased. The existing unreleased `unfilter` entry was reworded.

Totals: 27 ids done (4 of them changed from the recommendation: D-F5, T-F6, T-F7, L-9 cause), 1 partial skip (the Marker half of T-F16), 0 fully skipped.

## Tests added

- `host/tests/test_daemon_startup.py`:
  - A mistyped `--config` reports "not found, using defaults" and the default database, and does not create the file.
  - A found config is named with no "not found", and its own `db_path` is named rather than the default.
  - A missing `MCUSCOPED_CONFIG` is named instead of the platformdirs default.
- `host/tests/test_sim.py`:
  - `--demo` drops only `!p` and implies plotting; `--plot --demo` still drops it; no flag means no plot lines; `--plot` is unchanged.
  - `--demo` CAN is exactly 0x100, 0x18A, 0x400 plus 0x610 on bus 2, with ext and rtr present; the default keeps all seven ids.
  - `mcuscoped --sim` (`_start_sim`) runs `--demo`.
- `host/tests/webui_js/terminal_empty_state.test.mjs` (new):
  - Every copy branch, asserted by its unique text, including cleared-with-no-ports and the "nothing would be empty" null.
  - The hint for live, empty paused, busy, done and at the clear floor.
  - A rendered pane following ports arriving and a non-matching line arriving (the 1 s refresh), no channels, a regex, and a live append replacing the message through the shift path.
  - The pause and resume hint swap.
  - The port tag column: a second port arrives, a different second port, the second leaves, and a single-port pane.
- `host/tests/webui_js/terminal_filter_pane.test.mjs`:
  - Unfilter restores, and a second unfilter is a no-op.
  - Two clicks in a row restore the user's pattern.
  - A hand edit after the click survives unfilter, and a later click stashes the edit.
  - The filtered pane is restored after another pane becomes last, and a look-alike pattern on an untouched pane is kept.
- `host/tests/webui_js/cmdbar_sole.test.mjs`:
  - The auto label as ports arrive, connect-state changes alone, the second port leaves, and under an explicit pick; the send still carries `port: null`.
  - No ports disables the input with its placeholder, keeps Marker enabled, and the first attach re-arms the input.
  - Marker success is acknowledged.
  - The result strip redraws on open and close only, not when replaced while open or when hiding a hidden strip.
- `host/tests/webui_js/statusbar_logic.test.mjs`:
  - The version sits by the brand, and uptime and db size are in the hover.
  - The hover names `MCUSCOPED_TOKEN` and not `server.token`/`config.toml`.
  - The trimmed size shows in the bar; an unreachable daemon shows no uptime and no "no ports attached".
  - The strip: replaced by a second error, dismissed.
  - A failed detach survives two polls, is replaced by a failed disconnect, and is cleared by a detach that works.
  - `no ports attached` appears, goes with the first chip and returns; a target equal to the alias is hidden but kept in the hover.
- Updated for intended behaviour changes: `smoke.test.mjs` (`tickUptime` export removed), `terminal_logic.test.mjs` (the port tag needs two ports), `dom_stub.mjs` (`hintEl` on `makePane`).

## Gates

- `uv run python -m ruff check .`: all checks passed.
- `uv run python -m pytest tests/test_webui_js.py tests/test_webui.py tests/test_sim.py tests/test_daemon_startup.py tests/test_sim_tcp.py tests/test_plot.py -q`: 106 passed.
- `node --test` over `host/tests/webui_js`: 421 pass, 0 fail.
- Full suite: 1568 passed, 1 skipped. That run started before the `--demo` rename and the CAN trim. Those two changes touch only the demo path and were re-run through the gate set above.

## Screenshots

- `batch-a-demo.png`: `mcuscoped --sim` on 8797, dark theme, 1600 by 900 CSS px (3200 by 1800 at DPR 2), sidebar 360 px, Both view.
- `batch-a-empty.png`: empty daemon (no `--sim`, auto_session off). It shows `no ports attached`, the disabled input with its placeholder, the timeout box keeping its space in raw mode, and the brand version.
- Startup output of both throwaway daemons confirmed port 8797 and the review-dir db before the browser opened. Daemons and Firefox were stopped by PID, and the .db/.lock/.toml files deleted.

## For the owner

- **The demo sidebar still does not fit the digital lanes at 1600 by 900.** Measured in CSS px from `batch-a-demo.png`:
  - Sidebar body is about 760, and the CAN section is its 45 percent, about 340.
  - The four CAN rows end about 73 px short of that section's bottom, which stays blank.
  - Plots get about 420: sub-head 36, the stream 0 chart about 270 (its uPlot legend alone about 50), and the digital head 32.
  - Only the `GPIO (PACKED)` label and the `irq` lane show. `led`, `pwm_en` and the `state` enum group are cut off, about 110 px.
  - The blank CAN space (batch B, 45 percent split) plus one row of legend (L-4 / S-F9, batch C) would roughly close it; neither was changed here.
- **Chart readability.** `ramp` (0 to 25.5 mA sawtooth) reads clearly. `tri` (1 Hz) and `ftest` (0.5 Hz) are readable but dense at the 30 s window, 30 and 15 cycles interleaved.
  - SCREENSHOTS.md's claim that `ramp` "flattens the other series" does not hold: each series has its own y scale. HEAD's `light.png` already shows the 65535 ramp as a slow line beside full-height tri and ftest. The claim was removed with the section.
- **Capture size moved to the hover** unless trimmed. SPEC 9.1 "Capture size" was changed deliberately to match. Confirm this is wanted, since the size used to be always visible.
- **The port tag rule counts attached ports.** Lines still in the buffer from a port detached earlier lose their tag while one port remains.
- **The reserved `#lineRate` box is now 10ch.** On an idle daemon the chip shows that much blank space after the address (visible in `batch-a-empty.png`).
- **Manual verify owed.** There is no xdotool here, so nothing needing a click or hover could be driven:
  - the error strip's look, and the hover tooltips (daemon chip, restart badge, prompt);
  - each pane empty-state variant, and the paused-pane footer hint;
  - the regex box growing on focus, and the dot's enlarged hit area;
  - the dimmed port tag in the light theme.
- **Seen but out of scope:** the Digital / Enum head still clips its window selector (L-1), and it shows a live `pause` with no lanes (L-16).
