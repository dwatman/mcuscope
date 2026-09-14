# Final fixes report: toolbar, timestamp column, per-port decode, decode priming

HEAD: 11b8b1efbaf0bda2e4c1aec2d30502aa503e972f (uncommitted working tree on top).

## 1. Pane toolbar wraps

- Root cause: measured in headless Firefox at a 585 px pane, the row needed 599 px against 583 available.
  - Items: port select 96, chips 219, regex basis 90, export 57, clear 43 (505), plus 5 gaps of 10 and 12 + 32 padding.
  - `flex-wrap` breaks on basis, and export and clear were separate items, so `clear` dropped to a row alone.
- Fix:
  - Gaps 10 to 6 px, left padding 12 to 10 px; chip padding 6 to 5 px; export padding matches clear (7 px). The 32 px close-button reserve is kept.
  - Regex group basis 90 to 60 px; `max-width` stays 90 at rest and 260 on focus, with the grow factor unchanged, so it still rests at 90 and widens into free space.
  - Export and clear sit in one `.pane-actions` item with `margin-left: auto`, so they wrap together and right-aligned.
- Result: one row down to about a 531 px pane; 24 px spare at 585 with the box at 90 px.
  - Below that: export and clear move together to a right-aligned second row (480).
  - At 360 the regex box also moves to row 2, left, with the actions on its right. The close button stays pinned. The footer hint is untouched.
- Skipped: icon-sized export and clear. The text labels fit, and icons would need tooltips to say the same.
- Files: `host/mcuscope/webui/style.css`, `host/mcuscope/webui/index.html`.
- Test: `host/tests/webui_js/terminal_toolbar_static.test.mjs` (3), static, since the stub cannot lay out.
  - Pins gap, padding, chip padding, the 60/90/260 regex widths, `flex-wrap`, the pinned close button.
  - Pins export and clear as the only members of one right-aligned group, with close outside it.
  - Mutations each failing it: basis back to 90, gap back to 10, clear moved out of the group, the group's `margin-left: auto` removed.

## 2. Timestamp column misaligns

- Root cause: `.ln .ts` was `flex: none` with no width, so each row's stamp took its own text width and the columns after it moved with the digit count.
  - Host is always 12 characters; rel, delta and tick grow with the value, and tick adds `~`, `~-` and a gap's `-`.
- Fix: a grow-only width per pane and time base.
  - `pane.js tsColumnWidth(prev, mode, text)` (DOM-free): the longest stamp drawn so far in this mode, in ch; a new mode starts from its own stamps.
  - It returns `prev` itself while unchanged, so `terminal.js buildLine` writes `--ts-ch` on the scrollback only when the width grows.
  - `.ln .ts { min-width: var(--ts-ch, 0); text-align: right; }`. Marker and gap rows carry the same `.ts` cell, so they align too.
  - The scrollback is monospace, so length in characters is width in ch. Row height is untouched (still 18 px, no wrapping).
- Trade-off: a pane that once showed a long stamp (a raw 10-digit tick) keeps that width until the time base changes.
- Files: `host/mcuscope/webui/pane.js`, `host/mcuscope/webui/terminal.js`, `host/mcuscope/webui/style.css`.
- Test: `host/tests/webui_js/terminal_ts_width.test.mjs` (4).
  - Per mode: host, rel across 9.901s to 11.901s and negative, delta with `-`, tick with `~-`, `~750`, gap `-` and a 2^32 - 1 tick.
  - Identity when unchanged; a mode switch does not carry rel's width into tick.
  - Rendered pane: one width over a window straddling the digit, with a marker row; scrolling back to short stamps keeps it; a second pane is independent; host then rel resets.
  - Static: `min-width: var(--ts-ch, 0)`, `text-align: right`, and no other `.ts` rule sets width or alignment.
  - Mutations each failing it: width may shrink, mode ignored, no style write, left alignment.

## 3. `/plot/export` without `port=` merges boards

- Root cause: `_plot_export_defs` primed one `PlotDecoder` from every port's `!pd`, and the export rows carried no port.
  - Newest-first `keep_existing` gave a shared sid to whichever board declared last, so board A's rows rendered board B's labels.
  - A mid-range redefinition on one port relabelled the other's later rows.
  - `_changes_long` keyed (sid, name) and `_csv_wide` keyed name, so one board's sample was the other's baseline.
- Same flaw elsewhere:
  - Session bundles call the same helper with `port=None`: fixed by the same change.
  - `/lines/export` does not decode (it renders raw lines daemon-side), and `mcu plot export` passes through to `/plot/export`: covered.
- Fix:
  - `iter_plot_export` selects `l.port`.
  - `_plot_export_defs` returns one decoder per port, and in-window defs as `(id, port, raw)`.
  - `_export_rows` keeps a decode map per port and renders each row from its own.
  - `_changes_long` keys (port, sid, name); `_csv_wide` keys its baseline (port, name).
  - `_renders_as_label` takes the kinds across every port's decoder, so a band stays refused only where every declaration is a label.
  - `_wide_header`: where two boards name a lane's group differently, the port with the newest definition before the window labels the column.
    - A header is fixed per file; cells still render per port.
- Files: `host/mcuscope/server.py`, `host/mcuscope/store.py`, `docs/SPEC.md` 9.2.
- Tests in `host/tests/test_decode_per_port.py`:
  - `test_long_decode_labels_each_board_from_its_own_definition`: A_RUN and B_ON, where one decoder gave B's labels to A.
  - `test_long_changes_keep_one_baseline_per_board`: B's first 5 must emit after A's 5; A's second 5 must not after B's 9.
  - `test_wide_decode_and_changes_are_per_board`: labels per board, and A's unchanged sample dropped though B's differs.
  - `test_a_redefinition_on_one_board_does_not_reach_the_other`: A2_HIGH for A only, B_ON after it.
  - `test_port_scoped_decode_ignores_the_other_boards_later_definition`.
  - `test_session_bundle_decodes_each_board_from_its_own_definition`: definitions declared before the session.
  - Mutations each failing them: decoders merged into one key, long baseline without port, wide baseline without port.

## 4. Decode priming covers every port

- Root cause: the CLI primed from the newest 40 `!pd` rows over all ports. Board A's rebroadcasts filled them, so B's samples printed raw until B's next rebroadcast.
- Same shape elsewhere:
  - `/plot/export` primed from the newest 1000 `!pd` rows over all ports.
  - The web UI `seedPlotDefs` read the newest 50 over all ports.
  - Exempt: the daemon's attach priming (`serial_link`) is already scoped to its own port.
- Fix: each reads every `!pd` in the existing 20000-row lookback, paged 1000 at a time and oldest first, then primes newest-first per port and sid.
  - The query stays bounded by the lookback: at most 20 pages, and one page in any capture where under 5 percent of rows are `!pd`.
  - The web UI search now also stops at the window's oldest row (`id_to`); the window's own definitions arrive with it, in order.
  - The CLI priming dropped its `session` bound, matching `/plot/export`'s documented behaviour: a `!pd` just before a session start now decodes the session. `_make_decoder` lost the unused parameter.
- Files: `host/mcuscope/cli.py`, `host/mcuscope/server.py`, `host/mcuscope/webui/api.js`, `host/tests/test_timeline.py` (call site), `docs/SPEC.md` 4.
- Tests:
  - `test_decode_per_port.py::test_cli_priming_reaches_a_board_behind_many_rebroadcasts_of_another` (60 rebroadcasts).
  - `test_decode_per_port.py::test_cli_priming_covers_more_boards_than_the_old_cap`: 50 boards on sid 4, each with its own field name.
  - `test_decode_per_port.py::test_cli_session_window_primes_from_before_the_session`.
  - `test_decode_per_port.py::test_a_board_whose_only_definition_is_older_than_a_page_of_rebroadcasts` (server, 1001 rebroadcasts).
  - `webui_js/api_plot_def_seed_ports.test.mjs`: 1101 definitions over two pages, per-port charts, `id_to` and `since_id` of each page.
  - Mutations each failing them: CLI newest 40, CLI session bound restored, server newest 1000, web one page only, web newest 50 desc.

## Registry and docs

- `docs/REVIEW.md` class 57: two bit lines (export decode and bundles; newest-N priming across ports).
  - The `/plot/*` exemption narrowed to rows, and a sweep line for newest-N queries feeding per-port stores.
- `docs/SPEC.md`: 9.1 timestamp column alignment; 9.2 `decode` per port, wide header rule, `changes` per port; 4 priming per port and sid, past a `--session` start.
- `CHANGELOG.md` Unreleased, Fixed: six lines (export decode per port, priming, session priming, timestamp column, toolbar).
- `mcu ai-guide` unchanged: no option changed.

## Gates

- `uv run python -m ruff check .`: all checks passed.
- `node --test tests/webui_js/*.mjs`: 587 tests, 587 pass, 0 fail (`final-gates-node.log`).
- pytest, 523 passed, 0 failed, 1 warning (Starlette's httpx TestClient deprecation, pre-existing) (`final-gates-pytest.log`). Files:
  - `test_decode_per_port`, `test_plot_export_decode`, `test_session_bundle`, `test_cli_export`, `test_timeline`, `test_plot`.
  - `test_cli_r2026_09_12`, `test_cli_contract`, `test_hardening`, `test_webui_js`, `test_webui`, `test_cli`, `test_regressions`.
- Full suite not run (reserved). No daemon was started outside the test stacks, and no visible browser window was opened.

## Renders

- `final-585.png`, `final-480.png`, `final-360.png`: three panes per width (rel with marker and gap rows, tick with `~` stamps and a regex, a focused regex emulated by inline `max-width: 260px`).
  - Headless Firefox on the real style.css; the measured item widths and stamp right edges are printed under the panes. The html files are deleted.
- `final-before-headcss.png`: the same page on HEAD's CSS (light, 585/585/480/360), showing `clear` on its own row.
- The `ff-shot` profile was locked by a live Firefox (pid 189510, running since 22:40) that I did not touch; I used `ff-shot-light`, whose lock was stale.

## Owner decisions needed

- None blocking. Two judgement calls to confirm:
  - A wide export without `port=` over two boards that name a lane's group differently takes the header from the port with the newest definition before the window.
  - The timestamp width never shrinks within a time base, even after a clear or a capture reset (the brief allowed either rule).

## Manual checks owed

- [ ] Real page, two panes beside the default sidebar at 1600 px: toolbar on one row, regex focus widens without wrapping.
- [ ] Drag the sidebar wider until panes pass about 531 px: export and clear drop together, right-aligned; close stays in the corner.
- [ ] rel mode on the sim past 10 s and 100 s: rows shift once at each digit, never misaligned against each other; tick mode with `~` estimates.
- [ ] Windows (Segoe UI, Cascadia Mono): the one-row budget at 585 px, since the 24 px spare was measured with this machine's fonts.
