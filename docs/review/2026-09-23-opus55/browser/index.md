# Browser checks, 2026-09-25 (HEAD 91d3649)

Headless Chromium 1.62.0 (Playwright) on Linux against throwaway `mcuscoped` daemons, sims and fake boards; scripts in `~/tt-data/mcuscope-tools/browser/` (README there), output in `~/tt-data/mcuscope-2026-09-25/browser/`.
One line per checklist item: passed, failed (defect), or needs owner (steps in `owner.md`).
Evidence per group: `g1-owed.md`, `g2-chrome.md`, `g3-terminal.md`, `g4-can.md`, `g5-plots.md`, `g6-dialogs.md`.

Counts over the item lines below: 103 passed, 3 failed, 16 needs owner, 1 skipped; plus one off-list failure (F-2).

## Failures

- F-1: a soloed chart's y labels are cut past about 31 px (`10000`, `8e+307`); `plots.js:1034` fixed axis `size: 46`. `g1-owed.md`
- F-2 (off-list): after a channel or regex change, a paused pane's first top hit pages nothing; `terminal.js:389` sets `selfScroll` with no scroll to follow. `g3-terminal.md`
- F-3: the `bench` lane gutter tag is cut to `ben…` beside `pwm_en`; `style.css:317` lets the tag shrink first. `g5-plots.md`
- F-4: the export dialog's Clock row overflows the dialog at every width; `style.css:395`, `:461-464`. `g6-dialogs.md`

## Skipped

- 2026-09-15 pre-release list: every ticked item was verified by the owner 2026-09-15 or by the scripted leg 2026-09-16 (`browser-*.md`); only E-11 and the clock step were open, listed below.
- 2026-09-12 panels 9 (large export shows progress at once, daemon filename): verified 2026-09-16 as FW-9 and "Export preflight" (`browser-dialogs.md`).
- 2026-09-12 charts 2 (no stale selection box), 3 (double-click reset): owner-verified 2026-09-15/16; rerun anyway, passed.

## 2026-09-23 "Owed > Browser checks" (`g1-owed.md`)

- passed (Chromium): 60-character session name is one row ending in an ellipsis; `<U+202E>` shows in the clipped chip and its title.
- needs owner: the same in Firefox (O-1).
- passed (Chromium): reload badge after Back and after a session restore; a restored old build shows it.
- needs owner: reload badge after Back in Firefox (O-2).
- passed (Chromium): real `Sec-Fetch-*` headers; same-site and cross-site subresources 403, a UI link 200, a typed URL 200.
- needs owner: real `Sec-Fetch-*` headers from Firefox (O-3).
- passed (Chromium): decimation on a 1 kHz stream after a 40 s silence and on an 800 Hz stream at 30 s: no gap over 0.8 px.
- needs owner: decimation look in Firefox (O-4).
- passed (Chromium DPR 1, 1.25, 1.5 emulation): x-axis end labels whole in host and tick, ticks placed 1, 5 and 40 px from each edge; the label code measures the font and divides by the DPR, which is the 125% mechanism.
- needs owner (optional): 125% on Windows itself (O-8).
- passed: under 860 px with a saved hidden sidebar, charts and CAN draw and advance; past 860 px the reopen tab shows.
- failed (F-1): a soloed channel at 0 and 1.7e308 keeps its y labels whole.
- passed: a filtered paused pane after a reconnect, scrolled to the top: every marker in order, no divider between loaded rows (10 and 250 in the hole).
- passed (Chromium): four session exports in flight, a fifth from Settings waits 14 s and saves SQLite.
- needs owner: the five exports in Firefox (O-5).
- needs owner: U+2068/U+2069 show as nothing on Windows (O-7).
- needs owner: Safari, if supported (O-13).

## 2026-09-15 pre-release list, open items

- needs owner: E-11 with a screen reader (O-9); the ARIA proxy passed 2026-09-16 and again here (`g6-dialogs.md`).
- needs owner: host wall clock stepped back, needs sudo (O-10).

## 2026-09-14 web UI UX list (`g2`-`g6`)

- passed: version beside the brand; daemon chip hover has uptime and capture size. `g2`
- passed: daemon chip, restart badge and port chip hovers; the token hover names `MCUSCOPED_TOKEN`. `g2`
- passed: a failed detach keeps the red strip across polls; its x closes it; a later success clears it. `g2`
- passed: empty daemon shows `no ports attached`; the port dot's hit area reaches 7 px past it. `g2`
- passed: pane empty states are one line, tooltip with `cursor: help` where they carry detail. `g3`
- passed: paused pane footer: `scroll to the top...`, `loading older lines...`, `no older lines to load`. `g3`
- passed: regex box widens in 1 and 3 panes with no toolbar row change. `g3`
- passed (Chromium): regex `x` while widened clears and keeps focus. `g3`
- needs owner: the regex `x` in Firefox (O-6).
- passed: port tag only with two ports. `g3`
- passed: tick base: debug lines read `~n` in step, paged lines too, hovering one puts the chart cursor on its estimate (0 ms off, all paused). `g3`
- passed: port select as narrow as its widest option (sim, bench, long alias capped at 160 px). `g2`
- passed: auto option reads `(sim)` / `(auto)`; its tooltip explains the brackets. `g2`
- passed: line-ending select only as wide as its options. `g2`
- passed: empty daemon: command input disabled with its placeholder; Marker greyed. `g2`
- passed: CAN data wraps only before byte 5 or 7, no sideways scroll, age visible (300-520 px; 260 px is owner call O-12). `g4`
- passed: a changing byte lights; the highlight clears on a silent bus. `g4`
- passed: id filter widens, narrows, shows a no-match row, ignores `0x` and case. `g4`
- passed: CAN head wraps cleanly with `paused` and `unfilter` shown. `g4`
- passed: CAN column header and row hovers (frame count). `g4`
- passed: CAN divider drag up shrinks and scrolls, down does nothing, double-click restores 45%. `g4`
- passed: empty daemon: folded CAN head `no frames yet` with the grammar tooltip; the CAN view shows the body line. `g4`
- passed: CAN export Source snapshot disables ids; history re-enables them. `g4`
- passed (Chromium Linux, scrollbars unhidden at 15 px): an EXT RTR id fits without sideways scroll; the Windows item's mechanism. `g4`
- passed: no uPlot legend; chips show name, value, unit and follow the cursor. `g5`
- passed: hovering one chart updates every other chart's chips. `g5`
- passed: cursor time tag flips near the right edge. `g5`
- passed: a drag lights the zoom chip everywhere; its x and a double-click return to the window. `g5`
- passed: a window button while zoomed leaves the zoom and stays paused. `g5`
- passed: chart title rename: Enter, Escape, blur; focus returns on keys. `g5`
- passed: solo a channel: its axis is labelled with the unit. `g5`
- passed: with bench: charts and group headers name their port; the `N below` cue scrolls smoothly. `g5`
- passed: empty daemon: plots empty state one line with a tooltip; no Digital / Enum head. `g5`
- passed: the ruler lines up with the chart's time axis; `ARMED` fits its segment. `g5`
- failed (F-3): with bench attached, the lane gutter port tag reads well.
- passed: clear all while live and while paused: lanes start at their first new sample. `g5`
- passed: after clear all, zoom and the window buttons behave. `g5`
- passed: at 360 px the Digital / Enum head wraps its controls. `g5`
- passed: sidebar drag, expand, hide and CAN cap survive a reload. `g2`
- passed: in a narrower window the stored width is clamped; restore returns to it. `g2`
- passed: Settings: Bind host `Save *` and back. `g6`
- passed: Settings: Escape asks, naming the section; Cancel keeps the edit. `g6`
- passed: Settings: removing a port row marks Ports; an EOL change saved survives a reopen. `g6`
- passed: Settings with the daemon stopped: read-only, Saves disabled, token saveable (no focus move, since FD2-2). `g6`
- passed: Settings: Enter in Retention saves Storage only; the PlotJuggler disclosure opens by key and mouse. `g6`
- passed: Attach: device select focused; alias follows the device; Enter attaches; a held Enter attaches once. `g6`
- passed: Session: name selected; Enter starts; Enter in Note adds a newline. `g6`
- passed: Session: the note shows as the row's hover. `g6`
- passed: Export: heading names the panel; focus on the mode in force; `reset range`; Enter exports. `g6`
- passed: Tab: one stop per segmented control and window selector; arrows select; the zoom chip is the stop while zoomed. `g6`
- passed: cmd / raw by arrows keeps focus on the group. `g6`
- passed: divider: Tab stop, Left/Right resize with the accent, reload keeps it. `g6`
- passed: hide the sidebar with Enter: focus to the reopen tab and back. `g6`
- needs owner: screen reader announces dialog titles and attach hints (O-9; the ARIA proxy passed). `g6`
- passed: both themes: quiet text is 4.95-5.25:1 (AA) and below labels. `g2`
- passed: dark: labels and chip metadata below body text. `g2`
- needs owner: light: scrim, hover card shadow, accent on Attach and the brand (eye check, screenshots `g2-chrome/light/`).
- needs owner: light: dimmed port tag, CAN amber and red ages, chip and fold-cue colours; measured amber 3.8:1 and a lilac port tag 2.4:1 (O-11).
- needs owner: light: the darker accent on a lit icon button and a changed CAN byte (eye check, `light-main-paused-chart.png`).
- passed: line ending offered LF, CRLF, none in the bar, the attach dialog and Settings > Ports. `g2`
- passed: bench and sim sharing a channel name: both charts come back with history after a reload. `g5`
- passed: chart and lanes export with `changes only` (and a chart deadband) download; a bad deadband shows inline. `g5`
- passed: CRLF picked, reload with the daemon unreachable: the select still shows CRLF. `g2`
- passed: detach every port: Marker greys out with a hover, its box types, Enter sends nothing. `g2`
- passed: CAN bench 0x100 plain while running; stopped: amber then red at the 1 s repaint. `g4`
- passed: a one-off `can tx` id stays plain. `g4`
- passed: two boards' `state` enum lanes keep their own labels after a reload. `g5`
- passed: 700 px with two ports and badges: brand and actions on row 1. `g2`

## 2026-09-12 adversarial list (`g6-dialogs.md`)

- passed: 1 (W1) sys unticked, pane export downloads.
- passed: 2 (W4) a pane paused before any line exports a file, no 422.
- passed: 3 (W5) `changes only` disabled with decode off.
- passed: 4 (W3) second port never connecting: the bar refuses under `(auto)` (CLI-18).
- passed: 5 (W8) the bracketed port default is the way back.
- passed: 6 (W10) Shown window kept across a CAN export.
- passed: 7 (W11) a deleted remembered session is announced.
- passed: 8 (W12) an all-hidden chart's export is disabled with a reason. `g5`
- passed: 9 radio group, disabled toggling, labels associated (announcement: O-9).
- failed (F-4): 10 the clock row does not overflow the dialog.
- passed: 11 Escape closes without saving the range.
- passed: 12 Export before `/sessions` answers exports the remembered session.
- passed: 13 `reset range` selects the only, ended session.
- passed: 14 deadband errors (`vbat=0.5` not exported, `tri:0.5` malformed) legible inline.
- passed: 15 an open session's bundle saves under the daemon's filename.

## 2026-09-12 charts and terminal batch (`g5-plots.md`)

- passed: 1 a drag zooms every chart and lane, everything pauses together.
- passed: 2 no stale selection box on sibling charts.
- passed: 3 one double-click returns every panel and resumes.
- passed: 4 double-click on the lanes does the same.
- passed: 5 with a zoom standing, chart and lane cursors land within 0.6 px of their own times.
- passed: 6 shift-click lights every head.
- passed: 7 alt-click solo and back, y axis appears and goes.
- passed: 8 hidden channels: export visibly disabled with a reason (chart and lanes).

## 2026-09-12 panels and dialogs batch (`g4-can.md`, `g6-dialogs.md`)

- passed: 1 0x100 counter byte lights; the tint fades on a quiet id.
- passed: 2 id click fills the last pane; `unfilter` clears both.
- passed: 3 CAN pause freezes and resumes (label per SPEC 9.1).
- passed: 4 paused CAN export: Shown window covers the frozen span.
- passed (fake monitor in place of the ST-LINK): 5 chip shows the target and a settling rate, no jump, focus kept.
- passed (fake monitor): 6 a board swap changes the target name within a poll.
- passed: 7 CRLF attach with Save to config reads crlf in Settings and the file.
- passed: 8 picking the port default again sends nothing extra.
- skipped: 9 (verified 2026-09-16).
- passed: 10 token set, bad deadband name shows in the open dialog.
- passed: 11 bundle with a token saves (daemon filename).
- passed: 12 lower-case hex ids (std and ext) match the id-click filter.
- needs owner: 13 attach fields announced (O-9; labels and hints present).
