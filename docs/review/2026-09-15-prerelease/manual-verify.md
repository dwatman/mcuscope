# Manual verify: pre-release round 2026-09-15

Run against `mcuscoped --sim --config <throwaway TOML>` in a real browser; the DOM stub has no layout and its `<select>` keeps any value.
Items marked "scripted" ran in headless Chromium (Playwright); evidence is in the named `browser-*.md` report.

- [x] E-4 (scripted, browser-reset.md): type in the marker box while a board first answers `OK monitor`; the caret stays in the marker box and Enter adds a marker.
- [x] E-5 (passed 2026-09-15): double-click `+ Attach` and the gear against a slow daemon; one dialog, one list.
- [x] E-6 (passed 2026-09-15): `kill -STOP` the daemon; the gear opens Settings read-only about 2 s later, `+ Attach` opens with "no reply from daemon"; `kill -CONT` recovers.
- [x] E-7 (passed 2026-09-15): stop the daemon; the port select's auto entry reads `(offline)`, an empty command input shows `daemon unreachable`, and an open port dropdown is not closed by the 5 s polls.
- [x] E-10 (passed 2026-09-15; the figure is in the top status bar): with a cap set and lines trimmed, the bar shows content against the cap, and the hovers show the on-disk size.
- [ ] E-11: with a screen reader, a bad baud in Attach is announced; a command result is announced; the resizer reports a sane range.
  - [x] Proxy (scripted, browser-dialogs.md): the texts land in `role=alert` / `role=status` regions and the resizer carries min/now/max; the announcement itself needs a screen reader.
- [x] E-12 (passed 2026-09-15): under 860 px, the `»` and `expand` buttons are gone; widening again brings them back and the sidebar state is unchanged.
- [x] D-2 (scripted, browser-charts.md): pause a chart on a stopped `sim` stream after a later marker, export "shown window": the CSV holds the shown samples; the same for a filtered pane and the digital lanes.
- [x] D-5 (scripted, browser-reset.md): reload onto a board silent for minutes: CAN ages read the silence within one status poll.
- [x] D-8 (passed 2026-09-15): two CAN groups, pause, click a divider: rows collapse and the caret flips; click again restores.
- [x] D-10 (scripted, browser-edges.md): a board redefining `!pd 0 v:u2:mV` to `:V` on a soloed trace: the y axis label changes with the chip.
- [x] D-11 (passed 2026-09-15): a 240-character marker ends in an ellipsis inside the pane, the dashed rules shrink first, and the hover shows the whole marker.

Fix-diff fixes:

- [x] FW-1 (stall half passed 2026-09-15, after-CONT half scripted, browser-dialogs.md): `kill -STOP` the daemon; pane export, Export, Cancel, open a chart export. Its Export is enabled; about 2 s later the list shows `whole capture` and `could not list sessions: no reply from daemon`. After `kill -CONT` nothing downloads from the cancelled dialog.
- [x] FW-1 (Firefox passed 2026-09-16; Safari out of scope by owner): Firefox reports the list timeout as `TimeoutError`, shown as `no reply from daemon`.
- [x] FW-9 (scripted as a proxy, browser-dialogs.md): with no token, Settings > sessions > export on a large run hands the download to the browser as soon as headers arrive. The headers wait for the daemon's copy (about 5 s for 600k lines); a second click in that window downloaded twice (fixed, fix-browser-export-hold.md; the held button looks dimmed with its note, and a click on it downloads nothing, passed 2026-09-16).
- [x] FW-2 (scripted with a fake board of known ticks, browser-reset.md): under the tick base, chart and lanes paused, shown-window CSV: first and last rows match the drawn edges.
- [x] FW-4 (scripted, browser-charts.md): `clear` a pane mid-burst at high rate, pause, export shown: none of the cleared lines are in the file. A cleared row sharing a timestamp with the first kept row never occurred, so the `since_id` edge is unexercised.

Owner rulings:

- [x] Export preflight (scripted, browser-dialogs.md): a token-less `/lines/export` of a large capture shows one request cancelled at the headers, then a streamed download.
- [x] Export preflight (scripted, browser-dialogs.md): plot export with deadband `ftest:0.5` keeps the dialog open with `plot export failed: deadband needs name=value: ftest:0.5`, no file saved.
- [x] Export preflight (stall half passed 2026-09-15, after-CONT half scripted): daemon `kill -STOP`ped, Export shows `no reply from daemon` after about 2 s; after `kill -CONT` nothing downloads.
- [x] Settings > Sessions (scripted, browser-dialogs.md): delete a run in another tab, export it here: `session export failed: no such session: <id>`, no file.
- [x] Config revision (scripted, browser-dialogs.md): Settings open in tab A, save a section in tab B, save in A: the 409 text, typed fields kept.
- [x] Config warnings (scripted, browser-dialogs.md): a config with an unknown key lists the warning under the path in Settings; gone with a clean config.
- [x] Tick reset (scripted, browser-reset.md): a board reset mid-stream breaks the chart trace and the lanes, both keep scrolling, and a post-reset terminal-line hover lands on post-reset samples.
- [x] Shown window under zoom (scripted, browser-charts.md): drag a zoom, export "shown window" (host and tick base): the CSV spans the zoom; after a window button it spans the selector.
- [x] Digital pause (scripted, browser-charts.md): pause all before any enum or bits stream, start one: lanes stay blank with no ruler; resume fills them.
- [x] (passed 2026-09-15) Shift-click 5m, clear all, start a new stream: its chart comes up with 5m lit.
- [x] Dialogs (passed 2026-09-15): `kill -STOP` the daemon, click the gear, then click into the command input: focus stays there; Settings reads "loading..." until 2 s, then read-only. Same for `+ Attach` with "loading devices...".

## Fix-diff 2 additions (2026-09-15)

- [x] Tick reset, chart cursor (scripted, browser-reset.md): with the cursor on the gap point every channel reads `--` and the y axis does not jump. The gap point sits about 1e-6 px after the last pre-reset sample, so a real pointer reads that sample instead.
- [x] Host base, board reset mid-stream (scripted, browser-reset.md): the trace and the lanes break there too.
- [x] 64 lanes live with one stream quiet (scripted, browser-edges.md): script time about 2x the 8-lane run; every lane repaints each tick, off-screen ones too. Owner ruling: leave it.
- [x] Two boards on one port under the tick base (scripted, browser-edges.md): charts unaffected; the lower-uptime board's lanes show a held level. Owner ruling: SPEC 9.2 now says so.
- [ ] Host wall clock stepped back (`timedatectl set-time`) with charts, lanes and the CAN table live: a glued edge and stale CAN ages, as SPEC 9.2 now documents. Needs sudo and moves this machine's clock.
- [x] A 2^32 tick wrap (scripted, browser-edges.md): the axis continues and the trace breaks once.
- [x] Clear-all during a backfill (scripted with `page.route` holding `/lines`, browser-charts.md): failed, pre-clear seed samples and staged live rows came back; fixed (fix-browser-clear.md), rerun passes in both modes.
- [x] FD2-1: fields held while loading. Passed 2026-09-15.
- [x] FD2-2: failed 2026-09-15 (the caret jumped to the token box below the notice); fixed as a fixed banner with no focus move, recheck passed the same day.
- [x] FD2-3 (checked 2026-09-15): after the 2 s deadline the reopened dialog offers sim and custom and Attach is live by design; pressing it starts a POST that holds the button until the daemon answers.
- [x] Attach `devSel` (scripted, browser-dialogs.md): `showModal` focuses it, type-ahead during `loading devices...` does nothing, and replacing the options fires no `change`.
- [x] Owner session findings: a drag on a chart draws a tinted box with edges while dragging; the chip's x drops the zoom with everything still paused. Passed 2026-09-15.
- [x] A double-click on an unzoomed chart still flickers sometimes on a touchpad; with a mouse it does not (passed 2026-09-16).
- [x] FD2-7 (scripted, browser-dialogs.md): with a wrong token, the `/ws` handshake is an HTTP 403 seen as close 1006, and the page recovers through the 401 prompt within one backoff.
