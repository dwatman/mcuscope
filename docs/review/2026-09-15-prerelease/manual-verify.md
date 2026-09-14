# Manual verify: pre-release round 2026-09-15

Run against `mcuscoped --sim --config <throwaway TOML>` in a real browser; the DOM stub has no layout and its `<select>` keeps any value.

- [ ] E-4: type in the marker box while a board first answers `OK monitor`; the caret stays in the marker box and Enter adds a marker.
- [ ] E-5: double-click `+ Attach` and the gear against a slow daemon; one dialog, one list.
- [ ] E-6: `kill -STOP` the daemon; the gear opens Settings read-only about 4 s later, `+ Attach` opens with "no reply from daemon"; `kill -CONT` recovers.
- [ ] E-7: stop the daemon; the port select's auto entry reads `(offline)`, an empty command input shows `daemon unreachable`, and an open port dropdown is not closed by the 5 s polls.
- [ ] E-10: with a cap set and lines trimmed, the bar shows content against the cap, and the hovers show the on-disk size.
- [ ] E-11: with a screen reader, a bad baud in Attach is announced; a command result is announced; the resizer reports a sane range.
- [ ] E-12: under 860 px, the `»` and `expand` buttons are gone; widening again brings them back and the sidebar state is unchanged.
- [ ] D-2: pause a chart on a stopped `sim` stream after a later marker, export "shown window": the CSV holds the shown samples; the same for a filtered pane and the digital lanes.
- [ ] D-5: reload onto a board silent for minutes: CAN ages read the silence within one status poll.
- [ ] D-8: two CAN groups, pause, click a divider: rows collapse and the caret flips; click again restores.
- [ ] D-10: a board redefining `!pd 0 v:u2:mV` to `:V` on a soloed trace: the y axis label changes with the chip.
- [ ] D-11: a 240-character marker ends in an ellipsis inside the pane, the dashed rules shrink first, and the hover shows the whole marker.

Fix-diff fixes:

- [ ] FW-1: `kill -STOP` the daemon; pane export, Export, Cancel, open a chart export. Its Export is enabled; about 4 s later the list shows `whole capture` and `could not list sessions: no reply from daemon`. After `kill -CONT` nothing downloads from the cancelled dialog.
- [ ] FW-1: Firefox and Safari report the list timeout as `TimeoutError`.
- [ ] FW-9: with no token, Settings > sessions > export on a large run shows the browser's own download progress at once.
- [ ] FW-2: a real board under the tick base, chart and lanes paused, shown-window CSV: first and last rows match the drawn edges.
- [ ] FW-4: `clear` a pane mid-burst at 115200, pause, export shown: none of the cleared lines are in the file.
