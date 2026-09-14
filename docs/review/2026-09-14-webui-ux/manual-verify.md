# Manual verify: web UI UX round 2026-09-14

Merged from the batch A-D and owner-fix reports; checks later batches made moot are dropped.

Setup:

- Demo: `mcuscoped --sim`, Firefox and Chrome, sidebar at its 360 px default, Both view.
- Empty: `mcuscoped` on a throwaway config with `auto_session = false` and no ports.
- Second board: `mcu-sim --plot`, attached from the UI as `bench` on `socket://127.0.0.1:9900`.

## Status bar

- [ ] Version beside the brand; the daemon chip hover shows uptime and capture size.
- [ ] Daemon chip, restart badge and port chip hovers read correctly; the token hover names `MCUSCOPED_TOKEN`.
- [ ] Detach a port that fails: the red strip stays across polls, its `x` closes it, a later success clears it.
- [ ] Empty daemon: `no ports attached` shows; the port dot's hit area is easy to click.

## Terminal

- [ ] Each pane empty state is one line with its detail in the tooltip (`cursor: help`):
  - [ ] no ports, waiting for the first line, cleared;
  - [ ] no channels ticked, none on the ticked channels, none matching the regex.
- [ ] Paused pane footer: "scroll to the top for older lines", then "loading older lines...", then "no older lines to load".
- [ ] Regex box: focus in 1 and in 3 panes; it widens and no toolbar row changes line.
- [ ] Regex box: click its `x` while widened; it clears and keeps focus (Firefox and Chrome).
- [ ] Port tag shows only with two ports attached.
- [ ] Tick time base: sim debug lines read `~n` rising in step with the `!ps` ticks.
  - [ ] Scroll to the top of a paused pane; paged lines also read `~n`.
  - [ ] Hovering a debug line moves the chart cursor to that estimate.

## Command bar

- [ ] Port select: as narrow as its widest option with `sim` only, with `bench` too, and with a long alias.
- [ ] Auto option reads `(sim)` with one port and `(auto)` with none or two connected; its tooltip explains the brackets.
- [ ] Line-ending select is only as wide as none / LF / CRLF.
- [ ] Empty daemon: the command input is disabled with "attach a port to send commands"; Marker still works.

## CAN

- [ ] Data wraps only before byte 5 or byte 7 (8, 6 + 2 or 4 + 4), with no sideways scroll and `age` visible.
- [ ] A byte that changes lights, and the highlight fades on a bus that goes silent.
- [ ] Id filter: typing widens the box and narrows the rows; a no-match row shows; `0x` and case are ignored.
- [ ] Head wraps cleanly with `paused` and `unfilter` both shown.
- [ ] Column header and row hovers (row hover gives the frame count).
- [ ] Divider: drag above a short table shrinks and scrolls it, below does nothing, double-click restores 45 percent.
- [ ] Empty daemon: folded head reads `no frames yet` with the grammar tooltip; the CAN view shows the body line instead.
- [ ] Export: `Source` snapshot disables the ids field; history re-enables it.
- [ ] Windows (classic scrollbars): an extended remote id (EXT and RTR chips) still fits without sideways scroll.

## Plots

- [ ] No uPlot legend; each chip shows name, value and unit, and follows the cursor.
- [ ] Hovering one chart updates the chips on every other chart.
- [ ] Cursor time tag on a chart, flipping side near the right edge.
- [ ] Zoom: drag lights the zoom chip in every window selector; its `x` and a double-click both return to the window.
- [ ] A window button clicked while zoomed leaves the zoom and stays paused.
- [ ] Rename a chart title: click, type, Enter, Escape and blur each behave; focus returns to the title.
- [ ] Solo a channel: its axis is labelled with the unit.
- [ ] With `bench` attached: charts and group headers name their port; the `↓ N below` cue scrolls smoothly to the first hidden widget.
- [ ] Empty daemon: the plots empty state is one line with its tooltip, and no Digital / Enum head shows.

## Digital

- [ ] Ruler under the lanes lines up with the chart's time axis; `ARMED` fits its segment.
- [ ] With `bench` attached, the lane gutter port tag reads well.
- [ ] Clear all while live and while paused: each lane starts at its first new sample, not at the left edge.
- [ ] After clear all, zoom and the window buttons still behave.
- [ ] At 360 px the Digital / Enum head puts its controls on a second line rather than clipping them.

## Layout

- [ ] Sidebar drag, expand, hide and the CAN cap all survive a reload.
- [ ] The same in a narrower window: the stored width is clamped, and restore returns to the dragged width.

## Dialogs

- [ ] Settings: edit Bind host, see `Save *`; type it back and the mark clears.
- [ ] Settings: Escape asks, naming the section; Cancel keeps the edit.
- [ ] Settings: remove a port row and Ports is marked; change a port's EOL, save, reopen, value kept.
- [ ] Settings with the daemon stopped: read-only line, Saves disabled, token field focused and saveable.
- [ ] Settings: Enter in Retention saves Storage only; the PlotJuggler disclosure opens by keyboard and mouse.
- [ ] Attach: device select focused on open; the alias follows the device; Enter attaches, a held Enter attaches once.
- [ ] Session: the button opens the dialog with the name selected; Enter starts; Enter in Note adds a newline.
- [ ] Session: the note shows as the session row's hover.
- [ ] Export: heading names the panel; focus on the range choice in force; `reset range`; Enter exports.

## Keyboard and screen reader

- [ ] Tab: one stop per segmented control and window selector; arrows move and select; the zoom chip is the stop while zoomed.
- [ ] cmd / raw by arrows keeps focus on the group, not the command input.
- [ ] Divider: Tab to it, Left / Right resize with the accent highlight, reload keeps the width.
- [ ] Hide the sidebar with Enter: focus lands on the reopen tab; Enter reopens and focus returns.
- [ ] Screen reader: dialogs announce their titles; attach fields read their hints.

## Theme and contrast

- [ ] Both themes: hints, empty states, CAN headers and Settings headings are quieter than labels but legible.
- [ ] Dark: labels and chip metadata (brighter `--text-dim`) still sit below body text.
- [ ] Light: dialog scrim, port hover card shadow, accent on Attach and the brand.
- [ ] Light: dimmed port tag, CAN amber and red ages, chip and fold-cue colours.

## Narrow window

- [ ] 700 px with two ports and badges: brand and actions on row 1, as in `batch-d-header-700.png`.
