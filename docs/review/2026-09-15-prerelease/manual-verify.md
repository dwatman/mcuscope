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

Owner rulings:

- [ ] Export preflight: a token-less `/lines/export` of a large capture shows one request cancelled at the headers in DevTools, then a streamed download with browser progress.
- [ ] Export preflight: plot export with deadband `ftest:0.5` keeps the dialog open with `plot export failed: deadband needs name=value: ftest:0.5`, no file saved.
- [ ] Export preflight: daemon `kill -STOP`ped, Export shows `no reply from daemon` after about 4 s; after `kill -CONT` nothing downloads.
- [ ] Settings > Sessions: delete a run in another tab, export it here: `session export failed: no such session: <id>`, no file.
- [ ] Config revision: Settings open in tab A, save a section in tab B, save in A: 409 text with the reopen hint, typed fields kept.
- [ ] Config warnings: a config with an unknown key lists the warning under the path in Settings; gone with a clean config.
- [ ] Tick reset: a board reset by hand mid-stream breaks the chart trace and the lanes, both keep scrolling, and a post-reset terminal-line hover lands on post-reset samples.
- [ ] Shown window under zoom: drag a zoom, export "shown window" (host and tick base): the CSV spans the zoom; after a window button it spans the selector.
- [ ] Digital pause: pause all before any enum or bits stream, start one: lanes stay blank with no ruler; resume fills them.
- [ ] Shift-click 5m, clear all, start a new stream: its chart comes up with 5m lit.
- [ ] Dialogs: `kill -STOP` the daemon, click the gear, then click into the command input: focus stays there; Settings reads "loading..." until 4 s, then read-only. Same for `+ Attach` with "loading devices...".

## Fix-diff 2 additions (2026-09-15)

- [ ] Tick reset, chart cursor: park the cursor on the gap point; every channel reads `--` and the y axis does not jump.
- [ ] Host base, board reset mid-stream: the trace and the lanes break there too (PD-3 picks the SPEC wording from what this shows).
- [ ] 64 lanes live with one stream quiet: CPU, with every visible lane repainting on each 5 Hz tick.
- [ ] Two boards on one port under the tick base: the lower-uptime board's lanes sit off screen, the charts are unaffected.
- [ ] Host wall clock stepped back (`timedatectl set-time`) with charts, lanes and the CAN table live: a glued edge and stale CAN ages, as SPEC 9.2 now documents.
- [ ] A 2^32 tick wrap (seed the tick near 0xFFFFF000): the axis continues and the trace breaks once.
- [ ] `kill -STOP` the daemon, reload, clear-all while the backfill hangs, `kill -CONT`: the board's streams still chart.
- [ ] FD2-1: `kill -STOP` the daemon, open Settings, type into Bind host and Keep newest sessions, `kill -CONT`: what survives, and whether Close warns about unsaved changes.
- [ ] FD2-2: the same with the 4 s deadline expiring while typing: where the caret ends up.
- [ ] FD2-3: daemon stalled on `POST /ports`, attach, Cancel, `+ Attach`: whether Attach looks pressable while the list still says `loading devices...`, and whether pressing it says anything.
- [ ] Attach `devSel` (`autofocus`, opens holding only `loading devices...`): what `showModal` focuses, that type-ahead before the real list lands does nothing surprising, and that replacing the options fires no `change`.
- [x] Owner session findings: a drag on a chart draws a tinted box with edges while dragging; the chip's x drops the zoom with everything still paused. Passed 2026-09-15.
- [ ] A double-click on an unzoomed chart still flickers sometimes on a touchpad; likely a micro-drag registering as a selection while the chart scrolls under the cursor. Left as is; recheck with a mouse.
- [ ] FD2-7: with a wrong token in the browser, the `/ws` handshake is an HTTP 403 reported as close 1006, and the page recovers through the `/status` 401 prompt within one backoff.
