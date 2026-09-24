# Changelog

All notable changes to MCUscope are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the major version is 0, the interfaces in `docs/SPEC.md` (wire protocol, REST API, CLI exit codes) may still change between minor releases.

## [Unreleased]

Entries marked **Upgrade:** change behaviour a script may rely on.

### Changed

- **Upgrade:** `mcu assert` and `/assert` over a window that checked no lines report `empty`, exit 1, where they passed; `--allow-empty` (`allow_empty: true`) accepts it.
  - A live window whose lines were all dropped unjudged (shed, or a scan cut at the deadline) stays `empty` even with `--allow-empty`, with the reason `no lines were judged: N dropped unjudged`.
- **Upgrade:** a daemon that accepts a request but never answers is exit 1 on every command ("stopped answering"), no longer 2.
  - That includes `mcu tail -f` and `mcu can dump -f` against a daemon that accepts and then stops answering (was exit 3, or a traceback on Python 3.10).
  - Exit 2 stays for a timeout the board or the wait reported.
- **Upgrade:** with more than one port attached, a write without `-p` (`port`) is refused whatever the ports' state.
  - The only connected port is no longer picked, in the web UI command bar either.
  - Reads without `-p` still span every port.
- **Upgrade:** a `-p`/`port=` naming a board neither attached nor in the capture is refused (`no such port`: exit 1, HTTP 400) on reads, retrospective `/assert` and `/marker` too.
- **Upgrade:** `mcu lines --since-id N` returns the next `--limit` rows above N, not the newest ones; when `truncated`, call again from the newest id returned.
- **Upgrade:** `/wait` and `/assert` (`mcu wait`, `mcu assert`), live and retrospective, judge only rows the board sent.
  - The host's own rows (the call's own command and earlier tx rows, markers, sys notices) are neither matched nor counted in `checked_lines` unless `chan` names their channel (`--chan cmd`, `marker` or `sys`).
  - A silent board's window holding only a marker or a disconnect notice is `empty`.
- **Upgrade:** `mcu wait --send` whose command the monitor refuses exits 1 with the ERR on stderr, even when a line matched.
  - `mcu assert --send` fails its verdict on a send answered with ERR or not at all.
- **Upgrade:** confirmation prompts (`purge`, `session delete --data`) are refused unless stdin is a terminal: pass `-y`.
- **Upgrade:** `mcu mark` and `mcu send` take text starting with `-` as it is, and `mcu send -` is refused.
- **Upgrade:** firmware `can filter <id> <mask> x` passes only extended frames, and a filter without `x` only standard ones; the simulator matches.
- **Upgrade:** the firmware cuts an over-long event (`!p`, marker) at its last space and follows it with `!e event <type> overflow`, instead of cutting it mid-number.
  - A cut that would keep only the type (or a marker's `@tick`) sends the notice alone, never a bare `!m @7`; the simulator matches.
- Text output names each row's port (`[port]`) when rows from more than one board can be shown.
  - Without `-p`, `mcu tail -f`, `wait` and `log export`, `/lines/export?format=text` (the web UI's all-ports pane export too) and a bundle's `lines.txt` carry it when the attached boards and the boards with stored rows together number more than one, so a detached board's history keeps it.
  - The daemon's own rows do not count as a board.
- `mcu ai-guide` has a PITFALLS block, polls REST with `order=asc` while `truncated`, and is shorter.
- CLI refusals name CLI options (`--repeat-ms`, `--min-window`, `--eol`, `--send`), not daemon fields.
- `mcu cmd` prints `ok` when the response carries no data, and `mcu status` says when no ports are attached.
- Received lines split into tokens on spaces only (SPEC 2.1), in the daemon, the firmware and the web UI alike.
  - A tab or control byte between tokens no longer makes a line resolve a command, decode as a CAN frame or plot sample, set a tick anchor, or name the target.
- Simulator: unknown `spi`/`adc`/`can<N>` subcommands answer `ERR 1 badcmd`, `-` is refused as `can tx` flags, and a long unknown command no longer answers `ERR 8`.
- Firmware: the monitor formats with its own bounded formatter instead of `snprintf`.
  - 2.2 KB less flash and 0.4 KB less RAM on a board with no printf that does not call `monitor_eventf`.
  - Standard newlib's float printf is linked only when `monitor_eventf` is used.
- Commits coalesce only under load (above about 200 lines/s averaged over about half a second, at most one per 100 ms), cutting WAL writes.
- Session export and bundle builds run at most 2 at a time with 2 queued; one more answers 503, or waits for a slot with `wait=1` (at most 8 waiting), as the web UI's `.db` download does.
- The first start on an existing capture builds two indexes, about 2.5 s per million lines, before ports attach, and logs which indexes it is building and when it has finished.
  - `mcu daemon start` waits for that build instead of stopping the daemon at `--timeout` (which started the build over next time), for at most 600 s, then exits 1 and leaves the daemon running.
- Web UI: lower CPU.
  - Terminal panes keep appending instead of redrawing their window once they hold 5000 lines, and a hidden tab no longer trims each pane's queue per row.
  - Fast plot streams draw min/max per pixel (about 4x cheaper per redraw at 800 Hz), and digital lanes draw fast toggling as a block (about 10x cheaper).
  - A hidden sidebar stops the chart and CAN redraws; the "N below" cue and the "N / M lines" count no longer run per frame.
- Web UI: raw mode sends the line exactly as typed, leading spaces and an empty line included.
- Web UI: the CAN frame-history export is one board's, with a Port choice when there are several.
- Web UI: a pane regex the export filter would read differently is refused with the reason, and `.` matches as the daemon's does.
  - Refused: `\A`, `\Z`, `\p{..}`, POSIX classes, `{,n}` and backreferences.
- Web UI: a repeated enum value reads its last label, as the CLI does.
- Web UI: session names, notes and device text show every invisible (default-ignorable) character and bidi control as `<U+XXXX>`, so a direction override cannot disguise a name or reorder the text around it.

- Web UI: a drag on a chart's x axis zooms every chart and the digital lanes to that range and pauses them (it zoomed that chart alone); double-click anywhere restores the window selector's range.
- `mcuscoped --port` takes ASCII decimal digits in 1..65535 only, and a bad value is a usage error (exit 2, was 1).
- `mcu-sim --tcp-port`, `--drop-response` and `--flood` take ASCII decimal digits only, and the last two refuse negatives; `--flap` refuses `nan`, `inf` and negatives.
- Simulator: plot stream channels.
  - The typed plot stream declares a unit and a scale per channel (`tri` V, `ramp` mA, `ftest` degC).
  - The ad-hoc `!p` stream adds an `rpm` channel.
  - The unit, scale and independent-y-scale paths have more than one channel behind them.
- Simulator: `sim alive n=<count>` is replaced by narrated plain-text output.
  - It narrates state transitions, a 0.5 Hz reading, and a warning and an `ERR`-shaped line about once a minute.
  - It stays under 2 lines/s, so the demo terminal is readable with no command typed.
- `mcu wait` says what it timed out on, instead of the bare word `timeout`.
  - It names the pattern, the port given with `-p`, how long it waited and, with `--send`, how many sends went out.
  - Exit code and `--json` output are unchanged.
- `mcu status` prints `trimmed=N` when the capture has dropped lines to stay under its size cap, and stays quiet when it has not.
- `mcu plot channels` renders `last` through the `--decode` formatter, so a 32-bit float reads `0.140901` rather than seventeen significant figures; `--json` keeps the full value.
- `mcu devices` labels its columns.
- `mcu log export --csv` names the option it is refusing (`--limit`, `--decode`, `--changes` or `--names`) instead of always naming the first two.
- A daemon that stops during a long poll (`mcu wait`, `mcu assert`) is exit 3 (daemon unreachable), not exit 1 with `Internal Server Error`.
  - The daemon wakes parked polls at the start of shutdown, before uvicorn's graceful wait.
- `/ws` (so `mcu tail -f` and the web UI stream) closes at the start of shutdown rather than at the end of the graceful wait.
- `mcu attach DEV` without `--alias` sanitises and truncates the derived alias into the alias grammar (a `/dev/serial/by-id/...` path, refused before, attaches), and `--serial SN` derives one the same way.
- `/plot/export` deadband with no `=` says `deadband needs name=value`.
- Importing httpx no longer drags in its command-line interface: 55 `rich` modules and about 37 ms off every `mcu` call that makes a request, and off `mcuscoped` startup.
- Web UI: exports with no access token configured stream straight to disk instead of being buffered whole in the tab; after a cancelled token prompt they go through fetch and report the 401.
  - They are fetched first until the headers arrive, so a refusal (or no answer within 4 s) is shown in the export dialog, which stays open, instead of being saved as the file.
  - A session `.db` export checks the session is still listed and reports `no such session` rather than downloading the error.
- An unresolvable `session=` is a 400 on `/lines`, `/lines/export`, `/can/frames`, `/plot/series` and `/plot/export`, as it already was on `/assert`.
  - A session that exists and holds no lines is still an empty 200.
- `/plot/export` refuses every unknown channel name, not only an entirely unknown selection.
- `/wait` and `/assert` answer 503 when the daemon stops under them, instead of a generic 500 after the graceful-shutdown cap.
- An unrecognised config key or section is now warned about by name, with a spelling suggestion; the value is still ignored and the load still succeeds.
- Freed database pages are handed back on every maintenance tick, age-based retention included, so a capture file no longer only grows.
- The capture writer gets a 64 MB page cache.
- `mcuscoped --sim` leaves the sidebar room for the CAN table, the chart and the digital panel together.
  - It shows one analog chart (the typed stream: `tri`, `ramp`, `ftest`) beside the digital/enum panel, dropping the ad-hoc `!p` chart.
  - It shows four CAN ids instead of seven: 0x100, extended 0x18A, remote 0x400, and 0x610 on bus 2.
- Simulator: `ramp` counts one step per sample and wraps at 256 (0 to 25.5 mA over 12.8 s) instead of counting milliseconds to 65535.
- Web UI: version and daemon chip.
  - The version sits beside the brand.
  - The daemon chip keeps the address and `rx N/s` (the total across all ports), with uptime and capture size in its hover.
  - The size shows in the bar only once the cap has trimmed lines.
- Web UI: command bar.
  - It labels `auto` with the port it resolves to in brackets, `(sim)`, or `(auto)` when that is ambiguous, so the select is only as wide as its widest alias.
  - It disables the command input and the marker button while no port is attached (the marker text stays editable), acknowledges a sent marker, and keeps the timeout box's space in raw mode.
- Web UI: terminal lines carry the port tag only while more than one port is attached, and the light theme draws it dim.
- Web UI: a port chip's lines/s sits in a reserved box, and the connect dot has a hit area of about 20 by 24 px.
- Web UI: regex box and pane labels.
  - The regex box widens into the toolbar's free space while focused, without moving anything to another line.
  - Its tooltip gives the dialect and two examples.
  - The pane counter, the prompt glyph and the restart badge say what they mean.
- Web UI: the CAN table fits the default 360 px sidebar with `age` visible.
  - Data keeps at least 4 bytes a line and wraps only as 6 + 2 or 4 + 4, taking one line on a wider sidebar.
  - `ms` is renamed `period` and suffixes its unit like `age`.
  - The message count moved to the row's hover.
  - Every header has a title.
- Web UI: CAN `age` reads plain while fresh, instead of green until a fixed 3 s and grey after.
  - A periodic id goes amber past 5 missed periods and red past 10 (floors 250 ms and 500 ms).
  - An irregular id, or one with fewer than 3 gaps measured, is never coloured.
- Web UI: in the Both view the CAN section shrinks to fit its rows, capped at 45 percent (the divider drag sets the cap), and folds to its head while empty, so the plots keep the room.
- Web UI: the CAN table's `Reset` is `clear`; its export labels `Source` and disables the ids field under a table snapshot.
- Web UI: every empty state (terminal pane, CAN table, plots) is one short line saying what to do.
  - The grammar example and the firmware doc and SPEC pointers are its tooltip.
  - A folded, empty CAN section's head reads `no frames yet` and hides the id filter and `clear`.
- `mcu-sim --demo` (what `mcuscoped --sim` runs) slows the typed signals so each trace and lane reads at the 30 s window.
  - `tri` 10 s, `ftest` 24 s, `state` a step every 6 s (the narration follows), `led` every 2 s, `irq` a 300 ms pulse every 2.5 s, `pwm_en` 1 s on in 3 s.
  - `--plot` is unchanged.
- Web UI: charts are one per port and stream, and digital lanes one per port and name.
  - Two boards declaring the same stream or channel no longer merge into one trace.
  - Chart heads and lane gutters name the port once a second port has contributed, and exports pass that port.
- Web UI: uPlot's legend is gone; each channel chip shows its value (under the cursor, else the newest), the cursor line carries its time, and a soloed channel's y axis names its unit.
- Web UI: a collapsed chart's head lists its shown channels; chart and lane heads wrap their controls together instead of clipping `5m`; the Digital / Enum head stays hidden until the first lane.
- Web UI: palette colours are handed out per name across charts and lanes, so a chart's first channel and the first lane no longer share a colour.
- Web UI: under `delta` the Plots head reads `x: host (delta is terminal only)` and the button's title says the charts stay on host time.
- Web UI: hint and empty-state text passes WCAG AA in both themes (`--text-faint` #646f7b light, #7b8692 dark).
  - The dark `--text-dim` lifts to #949fac so hints stay quieter than labels.
  - The light accent is #0a6d7d (AA over its own soft tint too).
  - A lit window button uses dark text on the dark accent.
- Web UI: the light theme's dialog scrim is a light dim instead of near black, and the port hover card uses the theme shadow.
- Web UI: Settings marks a section with unsaved edits (`Save *`) and asks before Escape or the close button discards them; against an unreachable daemon it opens read-only, except the access token.
- Web UI: dialogs are named by their headings with hints attached to their fields, focus their first field, and submit on Enter (not from the session note or a button).
- Web UI: segmented controls, chart window selectors included, are one tab stop moved with the arrow keys; the sidebar divider resizes with Left and Right; hide and reopen hand focus to each other.
- Web UI: dialog hints and labels.
  - The attach dialog prefills the alias as `mcu attach` derives it, and its hints say what each field is for.
  - Storage has one hint per field, with the capture size beside the cap.
  - Refusals name fields by their labels.
  - The PlotJuggler setup folds into a disclosure.
- Web UI: the Export dialog's heading names what it exports, and `whole session` is `reset range`.
- Web UI: below 860 px the header keeps the brand and the Attach, settings and theme buttons on its first row.
- `mcuscoped` refuses a config file named by `--config` or `MCUSCOPED_CONFIG` that does not exist (`no such config file: <path>`, exit 1); a missing default config still means defaults.
- Config loader warnings (unknown keys, out-of-range values) are logged once at startup, not again on every `GET /config` or `PUT /config/ports`.
- `/plot/export` refuses a negative deadband (`deadband for <name> must be >= 0`); it was taken as its magnitude.
- Session bundles hold one `plot_<port>_<sid>.csv` per port and stream, and one `plot_<port>_adhoc.csv` per port, instead of `plot_<sid>.csv` and `plot_adhoc.csv` mixing every board's rows.

### Added

- `/plot/export` accepts `since_id` (exclusive), as `/lines` does.
- `MCUSCOPE_DATA_DIR`, `MCUSCOPE_CONFIG_DIR` and `MCUSCOPE_CACHE_DIR` override the platformdirs locations, on Windows too (where the XDG variables have no effect).
- `GET /status` carries `now`, the daemon's wall clock.
- `/plot/channels` lists `ports`, every port holding stored plot points.
- `mcuscoped` prints the config file it read, or that it was not found and defaults apply, and the capture database path at startup and in its startup log.
- `mcu-sim --demo`: the set `mcuscoped --sim` runs, `--plot` without the ad-hoc `!p` stream and with CAN cut to four ids over two buses.
- Web UI: an empty terminal pane says why.
  - The reasons: no ports attached, waiting for the first line, cleared, no channels ticked, nothing on those channels, nothing matching the regex.
  - The status bar says `no ports attached`.
- Web UI: a drag zoom shows its span as a chip in every window selector, with no window button lit; the chip's x and a window button leave the zoom and keep everything paused, a double-click leaves it and resumes.
- Web UI: the digital lanes have a time ruler and gridlines on the same steps as the chart x axis.
- Web UI: click a chart title to rename it for this browser.
- Web UI: the sidebar width, expand and hide state and the CAN cap are remembered per browser.
- Web UI: the Plots head carries a gesture hint (drag zooms, double-click resets) and a `↓ N below` control that names and scrolls to charts or lanes below the visible part.
- Web UI: lanes exported from more than one port offer a `Port` choice.
- Web UI: a failed detach, disconnect, reconnect, session or export action leaves its reason in a strip under the status bar until dismissed, replaced, or cleared by the next action that succeeds.
- Web UI: under the MCU tick time base a line with no tick of its own reads `~` and an estimate from its port's last earlier tick line, or `~-` before one; copies and exports keep the raw line.
- Web UI: the pane footer says that double-click copies a line and, on a paused pane, whether scrolling to the top loads older lines.
- `mcuscoped` names the PlotJuggler destination and the `--plotjuggler` flag in its startup output when streaming is on; `--plot` abbreviates to it silently otherwise.
- `mcu attach --serial SN` attaches by USB serial number (the fourth column of `mcu devices`).
  - A debugger that comes back under a different device name still attaches.
  - The device argument and `--serial` refuse each other, and one of them is required.
- `mcu can dump --session S`, matching every other read command.
- Web UI: alt-click (or Shift+Enter) on a channel or lane name shows only that one, and shows them all again when it is already the only one.
- Web UI: shift-click a window button to set that span on every chart and the digital lanes at once, including a chart created later (a new stream, after clear-all or a capture reset).
- Web UI: the CAN table highlights each payload byte that changed in any frame since its last repaint.
  - A byte that changes and changes back within the second still lights.
  - The highlight clears on the next repaint with no change.
- Web UI: a filter box in the CAN panel head shows only ids containing the typed hex.
- Web UI: clicking a CAN id filters the last terminal pane to that id's raw frames; an `unfilter` control in the panel head restores the filter the click replaced.
- Web UI: the CAN table is a pause-all surface, with its own pause button; a frozen table exports the window it shows (`id_to`), including the shown-window range mode.
- Web UI: the port chip names the board behind the port (`target` from `OK monitor`, in italics, not repeated when it equals the alias) and shows its lines/s, so a silent board and a moved probe are both visible.
- Web UI: the attach dialog offers a line ending and a serial number, sends both, and "save to config" writes the values the attach used (a CRLF board no longer lands on lf).
- Web UI: the command bar's line-ending select has a port-default entry labelled with the value the port will append, `(LF)`, so an override can be dropped without clearing site data.
- Web UI: starting a session opens a dialog with a name and an optional note (`mcu session start --note` already took one), replacing the browser prompt.
- Web UI: Settings > Ports has an EOL column, so a saved port's line ending no longer needs a config file edit.
- Web UI: Settings says theme, colours, layout and export range are kept per browser.
- `GET /status` carries `config_warnings`, the loader's warnings for the config the daemon started with.
- `GET /config` carries `revision` (sha256 of the file); every `PUT /config/*` accepts it back and answers 409 when the file changed since, writing nothing, and returns the new `revision` on success.
- Web UI: Settings lists the daemon's config warnings (`/status` `config_warnings`), one per line.
- Web UI: Settings saves and the attach dialog's "save to config" send the config `revision` they read; a file changed since is refused (409) and the fields keep what was typed.
- `/assert` answers carry `cmd_result` (the `send`'s result) and `reason`.
- `/can/frames` rows carry the `port` each frame came from.
- `mcu attach` notes when it moves an existing alias to another device, a serial binding included; re-attaching the same serial is quiet.
- `GET /ports` reports `stored`, the ports with stored rows; `/status` reports `ppid`; port rows report `serial_number`.
- The size cap's startup trim records a `sys` row.
- Firmware: opt-in `-DMON_NO_CAN`, `-DMON_NO_I2C`, `-DMON_NO_SPI`, `-DMON_NO_GPIO` and `-DMON_NO_ADC` leave a command family out; its commands answer `ERR 7 nosup`.
- Firmware: `monitor_eventf` arguments are checked against its format string on GCC and Clang.
- Firmware: a command handler may call `monitor_poll()` while it waits.
- Web UI: a "daemon updated: reload" badge appears when the daemon is upgraded under an open page, also in a page restored from the browser cache (the daemon stamps its version into the page).
- Rows committing more than 10 s out of time order (a long loop stall, a slow disk, a backwards clock step) are announced in the capture with a `sys` row, and the count follows when order returns.

### Fixed

- Web UI: "Shown window" export from a paused chart no longer drops or adds the samples of a serial burst at either edge, under the tick base and under a host-base zoom; charts, lanes and the paused CAN table now export by line id.
- `/plot/export`, `/lines/export` and `/can/frames` exports bounded by `since_id` name their file from the lines they cover, not `start-end`.
- Web UI: after a reload, a stream's chips and lanes, and their colours within that stream, follow its `!pd` field order instead of alphabetical order.
- Web UI: a clear-all pressed while the page's backfill is loading no longer brings back the plot history seed on the charts and digital lanes.
- Web UI: a clear (a pane, clear-all, or the CAN table's) pressed while the backfill is loading also covers the live lines that arrived meanwhile; lines arriving after the click still show.
- Web UI: a session export clicked again while the daemon is still building the copy no longer downloads it twice; the button is held for 5 s with a "preparing download..." note, keeping keyboard focus.
- Web UI: a burst of live lines during a slow backfill no longer loses a capture reset notice; the oldest waiting lines are dropped instead of the newest.
- Web UI: relative time after a clear-all during a backfill starts at the first line shown, not at a cleared one.
- Web UI: Settings against an unreachable daemon shows the reason in a fixed banner at the top of the dialog and no longer moves the caret to the access-token box, which sat below the notice.
- Web UI: the dialogs and the export list give a stalled daemon 2 s, not 4, before opening read-only or offering the whole capture.
- Web UI: the drag zoom draws its range while dragging (uPlot's default box was invisible on the dark theme).
- Web UI: a double-click on an unzoomed chart no longer flickers to the whole buffer for a frame (uPlot's own double-click reset is off).
- `mcu can dump -n` with `--last-ms`: the window is fixed before paging, so a walk past the 1000-frame cap no longer drops the oldest frames and calls the dump complete.
- `mcuscoped` reports `config_path` absolute, so `mcu daemon restart` run from another directory checks and carries the file the daemon runs on rather than a same-named one under its own cwd.
- `mcu daemon start` reports a daemon it started that answers behind a token as started (exit 0, with a note that later commands need `--token` or `MCUSCOPE_TOKEN`), instead of exit 1 for a daemon left running.
- `mcu tail -f` caps a WebSocket frame at 16 MiB rather than accepting one of any size.
- `mcu detach a/b` says `invalid alias 'a/b': an alias cannot contain '/'` instead of claiming a port lookup it never made.
- `mcu ai-guide` states `mcu wait`'s exit 1 for a daemon that never answers, and `daemon start`'s exit 0 behind a token.
- `GET /plot/channels` no longer re-reads a detached board's stored `!pd` definitions on every request; the learned set is kept until the capture is replaced or the alias is attached again.
- A session bundle holding two stored ports whose names differ only in characters the member naming replaces (`a/b` and `a_b`) writes one member per board (`plot_a_b-2_3.csv`) instead of the same name twice, which lost the first board's rows and listed it twice in `manifest.json`.
- The WebSocket handshake refusal is documented as the HTTP 403 it is on the wire (a browser can only report it as close 1006), replacing the close 1008/1013 wording (SPEC 3.1).
- Web UI: the Settings dialog holds its fields, not just its Save buttons, until the config file has loaded, so a slow daemon cannot overwrite what was typed into one.
- Web UI: a Settings dialog that opened before an unreachable daemon answered no longer moves the caret to the access-token box while the user is typing in it.
- Web UI: Attach stays held while a reopened attach dialog loads its device list, even when an attach from an earlier opening finishes meanwhile.
- Web UI: a refused config save shows the daemon's message once, instead of following it with a second reload instruction in other words.
- Web UI: a session export checks the session by an encoded name, so a name carrying `&` or `#` is no longer read as another session.
- Web UI: clear-all pressed during a backfill no longer discards the stream definitions it carried, which left every later plot sample on those streams undecodable.
- Web UI: a terminal pane created while a backfill is out and then cleared no longer refills with the rows it cleared.
- Web UI: a pane scrolled to the top until its 5000-row budget ran out could show a `gap: 0 lines not loaded` divider when there was nothing older to load; the divider is shown only when lines were left behind.
- Web UI: two Settings section saves fired within one request's round trip made the second refuse itself with a 409; saves now run one at a time, each with the revision the previous one returned.
- Web UI: after an attach, detach, reconnect, hold or session change, the status bar could show the state from before it for up to 5 s (the refresh reused a poll already in flight); it now waits for a fresh poll.
- Web UI: a pane clear, clear-all or CAN clear clicked while the page's backfill was still loading was refilled by that backfill's rows; the clear now covers them.
- Web UI: Settings and Attach open from the click in a loading state instead of up to 4 s later, which moved focus from wherever the user had gone meanwhile; Save and Attach are held until the daemon answers.
- `mcu can dump -n` above 1000 showed only 1000 frames, and `-f` dropped frames when more than 1000 arrived between polls; both now page past the cap, and `-n` notes on stderr when older frames exist.
- `mcu daemon status/start/stop` read a running daemon's 401, 403 or 429 refusal as "not running" (`start` then spawned a second daemon that died on the port); they now exit 1 naming the refusal.
- `POST /cmd` waiting on the target when the daemon stops answers the shutdown 503 (`mcu cmd` exit 3) instead of a 500 after the grace period.
- `mcu assert --last-ms` outside 1 to 10^15 is a usage error before any request, as `--last-ms` is on the other commands.
- A FIFO at the daemon's pid record path no longer hangs `mcu daemon stop` and startup; it reads as no record.
- A closed or full stdout or stderr no longer changes an `mcu` exit code.
  - A failing `mcu assert | head -0`, or `mcu daemon status` with no daemon, kept exit 0 over its 1 or 3.
  - Output into a full disk (`> /dev/full`) is exit 1 with `cannot write output`, not a crash log; inside `mcu tail -f` it was exit 3.
  - An error message into a full stderr exited 120 with a crash log.
- `mcu --json tail -f` and `mcu --json can dump -f` end when their reader closes the pipe, instead of following into nowhere.
- `mcu tail -f` accepts a stream frame over 1 MiB (a burst of long lines) instead of ending with exit 3.
- `mcu can dump -f` giving up after 30 s of failed polls is exit 1 when the daemon kept answering errors; exit 3 stays for a daemon that cannot be reached.
- `mcu detach` quotes the alias, so `board?x` no longer detaches `board`; an alias containing `/` is refused before any request.
- `mcu lines`/`mcu log export` with `--session S --last-ms N` on an ended session return its tail, as `can dump` and `plot export` do.
- A paged `mcu lines`/`mcu log export --to` walk resolves the `until_ts` ceiling once, not once per page.
- Web UI: Cancel ends an Export still waiting for the session list, and that list gives up after 4 s against a stalled daemon.
- Web UI: a paused panel's shown-window export in tick mode covers the samples drawn, a filtered CAN table's covers the rows shown, and a pane's leaves out lines it cleared from the same serial read.
- Web UI: a late PlotJuggler answer no longer overwrites a destination or checkbox being edited.
- Web UI: a Server or Storage save whose re-read fails still raises the restart badge.
- Web UI: a plot history seed landing after a clear-all is dropped.
- `until_ts` resolves to an id ceiling once per request: an export to a minute ago no longer walks the index on every page (292 s to about 11 s on a 1M-row capture), and `/lines` with `until_ts` runs off the loop.
- `last_ms` on an export or a retrospective `/assert` counts back from now when the caller gave no upper bound, not from the newest stored line of a quiet capture.
- A deadband on a channel first declared as a label inside the export window is refused, as it is when declared before it.
- A session bundle queued behind that session's deletion is refused instead of answering an empty bundle for it.
- A `/wait` or `/assert` match committed just before shutdown is still answered, and `/ws` sends the rows queued ahead of shutdown before closing.
- A subscriber lagging at shutdown, or arriving after it began, gets the shutdown 503 (`/ws`: close 1001) instead of a 500 after the grace period.
  The same holds for a `/wait` or live `/assert` still inside its `send`, and a row shed from a full `/ws` queue at shutdown is announced as a gap.
- `/plot/channels` and `/plot/export` read during a plot summary rebuild (the first read after start or a delete) wait for it, instead of seeing only the newest rows and refusing a channel that has points; a rebuild whose scan fails is retried on the next read.
- `deadband` values follow the SPEC 2.5 value grammar (`+5`, `1_0`, `.5` and padding are refused), and a name given twice is refused.
- A negative `last_ms` is a 422, an export whose window crosses its session (or its `last_ms` span) no longer names a backwards file, and a name listed twice in `/plot/export?names=` is a 400.
- Web UI: a terminal history page still loading when the pane is cleared, resumed, refiltered or the capture resets is dropped instead of landing in the new rows.
- Web UI: a paused panel's "shown window" export covers the window it draws, not a span ending at a later line on another channel or port, and while a drag zoom stands it exports the zoom range.
- Web UI: under the tick base an MCU reset or a 2^32 tick wrap no longer draws every later sample glued to the last tick before it and stops the lanes' live edge; the axis continues by the host-time gap, with a break in the line at the reset.
- Web UI: a digital panel paused before its first lane stays empty until resumed, instead of showing a sample from after the pause and moving its export watermark to it.
- Web UI: the pause-all button relabels when a chart, lane or CAN row is born live or cleared.
- Web UI: an export dialog left open across a capture reset refuses Export instead of sending the old capture's watermark, window or session.
- Web UI: an answer arriving late no longer lands in a view replaced meanwhile.
  - Settings: a section save keeps fields typed while it was out (still marked unsaved), and one landing after Settings was reopened writes nothing there and adopts no revision.
  - Settings: an older `/status` or PlotJuggler read no longer overwrites a newer one; the attach dialog honours "save to config" as ticked at submit.
  - A late attach, session start or marker no longer closes or writes into a reopened dialog, clears a failure shown after it, overwrites a newer command result, or wipes a marker label typed meanwhile.
- Web UI: a digital lane whose stream went quiet keeps scrolling with the other lanes and follows a theme toggle; a resumed lane's readout shows the value that arrived while paused.
- Web UI: leaving a chart rename by clicking elsewhere keeps focus where the click sent it.
- Web UI: a pane regex's 200-character cap counts characters as the daemon does, and a pane's older-lines divider no longer counts lines at or below its clear point.
- Web UI: CAN ages count from the daemon's clock, so a page loaded onto a silent board no longer shows its frames as fresh.
- Web UI: clicking a CAN id filters the pane for lower-case ids, `!can1` and whitespace runs too, and tells standard from extended frames.
- Web UI: a remote frame no longer wipes the CAN byte-change highlight of the data frames around it.
- Web UI: collapsing a CAN group divider works while the table is paused.
- Web UI: a single-trace chart's y axis label follows a unit redefinition.
- Web UI: a long marker row ends in an ellipsis and shows the whole text on hover.
- Web UI: hand-edited terminal pane settings in localStorage are type-checked field by field.
- Web UI: a pane regex the daemon's engine refuses is not sent with that pane's export.
- Web UI: a Storage save no longer re-rounds a size cap that is not a whole MiB.
- Web UI: a Settings save whose re-read of the config fails keeps the fields as typed and says so, instead of showing the old config as saved.
- Web UI: a `/status` poll no longer moves keyboard focus into the command input when the command mode flips.
- Web UI: a list refilled while an earlier fill is still loading (export sessions, attach devices, Settings) no longer shows its options twice.
- Web UI: Settings and Attach open within 4 s against a daemon that accepts and never answers.
- Web UI: with the daemon unreachable, the command bar's `auto` entry reads `(offline)`.
- Web UI: the status bar and Settings compare the capture content with the size cap, not the file size on disk.
- Web UI: inline errors and the command result are announced to screen readers; the resizer reports its range.
- Web UI: the sidebar collapse and expand buttons are hidden in the narrow layout, where they did nothing.
- Web UI: the PlotJuggler destination typed while a toggle is saving is no longer overwritten.
- `mcu wait`/`mcu assert` against a daemon at its subscriber cap exit 1, not 3; only the shutdown answer maps to "unreachable".
  - `mcu tail -f` refused at the cap (WebSocket close 1013) exits 1 too, naming "too many subscribers".
- `mcu can dump --session S -f` stays inside the session.
- A refused export no longer truncates or deletes what `-o` names; a failed one removes only a regular file, never a symlink, FIFO or device.
- `mcu log export --json` ends with a parseable error line when the daemon dies mid-row.
- `--from`/`--to` at the calendar's ends are usage errors, not a traceback, and no longer cost a request before a usage refusal.
- The paged `mcu log export` (`--limit`, `--decode`) writes LF to a redirected Windows stdout.
- A usage error with stderr closed keeps exit 1 and its `--json` object.
- `mcu session export` works for session names containing `/`, `?` or `#`, and refuses a directory `-o` instead of writing a hidden `DIR/.zip`.
- The truncation note names options the command has.
- `mcu can dump -o F --json` prints a file summary instead of refusing.
- `--last-ms` is bounded (0 to 10^15) client-side.
- `mcu attach --serial` refuses a blank serial and strips surrounding spaces.
- Against a daemon older than 0.4.0, `-p` on `plot export`, `--eol` and `--repeat-ms` are refused naming its version, and a missing route (`log export`, `session export --bundle`, `break`, `sysrq`) names the version instead of `Not Found`.
- A port reconnect keeps `last_write_error` and `last_write_error_ts`, and decodes with a `!pd` the old connection stored while the new one was priming.
- Two failing writes at once both count toward `write_failures`, and a disconnect during a failing write ends the streak.
- A SIGTERM landing inside the subscriber fan-out can no longer drop the shutdown sentinel: it is scheduled on the loop.
- Web UI: a reload restores each board's chart history for a channel name two boards share, under that board's own definition, including a detached board shadowed on every name.
- `/plot/channels` takes each row's unit, scale, kind and labels from that row's own attached port, not whichever port declared the name last.
- `POST /purge` refuses a non-finite `before_ts` instead of answering `deleted: 0`.
- A non-finite `since_ts` or `until_ts` is a 400 naming the field instead of a 500 on the export endpoints; an export bound past the platform clock no longer fails its filename.
- `mcu lines/tail/log export --decode` without `-p` decodes each port's samples with that port's own `!pd` definitions, and `--changes` compares per port.
- `/plot/export?decode` without `port=`, and session bundles, render each board's samples from that board's own `!pd` when two boards declare one sid, and `changes` compares per port.
- `--decode` priming (CLI, `/plot/export`, and the web UI on load) reads every `!pd` in the 20000-row lookback rather than the newest 40, 1000 or 50.
  - With many boards, no port's definition is crowded out by another's rebroadcasts.
- `mcu lines --session S --decode` decodes a stream whose `!pd` was declared just before the session started.
- Web UI: a pane's timestamp column no longer shifts rows right where a stamp gains a digit (`9.901s` to `11.901s`).
  - It is right-aligned at the widest stamp shown in the current time base, marker and gap rows included.
- Web UI: the pane toolbar fits one row in two panes beside the default sidebar at 1600 px.
  - A narrower pane wraps export and clear together to the right of a second row, instead of dropping `clear` alone.
- `mcu tail -f --decode --changes` no longer repeats each stream's last snapshot sample as a change when the follow starts.
- `mcu daemon start --config` (or `MCUSCOPED_CONFIG`) naming a missing file is refused with exit 1 and `no such config file: <path>`, before anything is spawned; it used to start on defaults.
  - `~` and a relative path are resolved before the check, and `daemon restart` refuses before stopping the running daemon.
  - A missing default config still means defaults, and `restart` of a daemon running on it is not refused.
- Every `mcu` call with stdout closed and stderr a closed pipe exited 120; the startup and crash notices now drop quietly.
- Web UI: switching the export dialog's range choice away from clock and back no longer wipes typed clock bounds.
- `--from`/`--to`, `plot export --decode/--changes/--deadband` and `can dump --csv` against a daemon older than 0.4.0 are refused naming its version.
  - They no longer silently export the unfiltered window at exit 0 (an older daemon drops a query parameter it does not declare).
- `mcu session export --bundle -o run.DB` is refused like `run.db` (on Windows they are one file), and `-o -` is refused on every command taking `-o` (`log export`, `plot export`, `can dump`, `session export`) rather than writing a file called `-`.
- A streamed export to stdout writes the same bytes as `-o FILE` on Windows: `mcu log export --csv > run.csv` was CRLF where the `-o` form was LF.
- `mcu can dump --to T -f` is refused: the follow could not honour the upper bound and streamed past it for ever.
- Web UI: after clear-all a digital or enum lane drew its first post-clear value back to the left edge of the window, as if held for the whole span.
  - A lane now starts at its first sample, as a chart trace does.
- Web UI: the Digital / Enum head showed a live `pause` with no lanes: `.plot-head`'s display overrode `hidden`.
- Web UI: "No plot data yet" stayed on screen beside live lanes from a digital-only stream.
- Web UI: a chip kept a channel's old unit after its stream was redefined with a new one.
- Web UI: fixed a terminal pane export sending its channel filter as one comma-joined value, which the daemon refused with 422 for any pane with two to five channels ticked.
- Web UI: a plot or digital export with "changes only" now always sends decode, which the daemon requires; the checkbox follows the decode box in the dialog.
- Web UI: the chart and digital export buttons are disabled, saying why, while the panel shows no channel or lane, instead of doing nothing when clicked.
- Web UI: an export refused by the daemon keeps the dialog open with the reason beside the range that produced it, instead of closing and flashing a toast.
- Web UI: the export dialog's remembered range and session list.
  - It no longer forgets a remembered `shown` range when opened from a panel that has no frozen window.
  - It says so when a remembered session has gone.
  - It lists the newest 200 sessions rather than 50.
- A deadband on a channel name that one stream declares as a label and another as a number is accepted; only a name every declaring stream renders as a label is refused.
- Web UI: the daemon chip's tooltip no longer tells the user to set `server.token` in `config.toml`, which the daemon ignores; it names `--host 0.0.0.0` and `MCUSCOPED_TOKEN`.
- Web UI: the command result strip no longer leaves a blank band at the bottom of every pane after it closes.
- Web UI: ticking or changing an export option no longer wipes clock bounds typed into the dialog but not yet exported.
- Web UI: the `none` line-ending tooltip no longer promises a Ctrl-C that a text input cannot type.
- Web UI: port aliases that shadow `Object.prototype` (`constructor`, `toString`, `valueOf`) are treated as ordinary ports by the send-mode and line-ending state.
- `/can/frames?id=A,B`: a multi-element id list no longer sorts every match through a temp b-tree (266 ms against 0.26 ms at 300k frames; the paged CSV export paid it per page).
- Session bundle: every member covers one frozen id span, including a session still running.
  - `manifest.json` records it as `from_id`/`to_id`.
  - Every purge and retention sweep waits for a bundle in progress (the lock is store-wide) instead of deleting rows mid-build.
- Export filenames: `last_ms` is anchored where the rows are, not at the request, so a window can no longer be named backwards.
- `until_ts` no longer drops the newest rows after a backwards clock step.
- A deadband on a decoded bit lane is refused rather than silently ignored, and a value of `inf`, `nan` or another script's digits is refused instead of parsed.
- A marker or captured line holding CR/LF is stored as one row and exports as one line.
- `id_to=0` (a surface paused before its first line) is an empty window, not a 422.
- A `?id=` list with an empty element says so.
- `POST /sessions` refuses a blank or whitespace-only name (422) instead of storing a session with an empty name.
- Web UI: a saved access token is confirmed in plain text, not in the error colour.
- Web UI: Settings reopened after the daemon went away no longer offers the last loaded config as editable.
- `/plot/channels` gives a detached board (or one mid-reconnect) the definitions from its own stored `!pd` rows, or null fields, instead of another board's definition of the same name.
- A `mcuscoped` start that never serves (a capture that will not open, a failed bind) rewrites its startup log as `failed to start` with the exit code and the reason, instead of leaving `started`.
- An `mcu` stdout closed at start (`>&-`) is exit 1, reported only as `cannot write output`, and ends a `-f` follow at once; `mcuscoped` and `mcu-sim` keep the stream-repair warning.
- An empty `port=` on reads and a retrospective `/assert` selects the daemon's own rows (port `""`) instead of every port.
- `mcu` refuses an empty `-p` on every command (exit 1); reads used to drop it and span every port while `wait` and `assert` forwarded it.
- `mcu daemon stop` signals a pid only when the local pid record names the process `/status` reports; a daemon on another machine is asked to shut down and never signalled, and neither is a process that inherited a stale record's pid.
- A `mcu daemon start` that loses a race to a running daemon no longer wipes its logs.
  - The CLI appends to the daemon's `.err` file, and on Windows the spawned daemon does too.
  - The losing daemon writes its startup log under its own pid.
- `mcu tail -f` quotes `-p` in its WebSocket URL, and an HTTP refusal of the upgrade is exit 1 (502 and 504 stay exit 3).
- `mcu plot channels` honours `-p`.
- `mcu sysrq` refuses a non-ASCII character before sending the break.
- `--decode` shows `!pd`-prefixed lines it cannot learn (`!pdo ...`, malformed definitions) instead of dropping them.
- Match-bearing reads and retrospective `mcu assert` wait past the daemon's 30 s match budget, so the daemon's own answer arrives.
- `mcu purge` refuses `--id-from` above `--id-to`.
- `mcu tail -n 0 -f` and `mcu can dump -n 0 -f` print no truncation note.
- Text rendering (`mcu lines`/`tail`/`log export`, `/lines/export?format=text`) shows VT, FF, FS, GS, RS, NEL, U+2028 and U+2029 escaped, so each row stays one line.
- A foreground `mcuscoped` whose terminal closes (SIGHUP), or on Windows whose console window closes (in-flight requests then get 3 s, inside the 5 s Windows allows), shuts down cleanly: `daemon stop` row, session closed, pid record removed.
- `server.host = ""`, or one with spaces or control characters, warns and binds 127.0.0.1 instead of every interface; `--host` refuses the same values.
- A config port the settings dialog could not save back is skipped with a warning, so the dialog can save ports again: a device the API refuses (`spy://`, `?` options), a device over 512 or serial number over 128 characters, a control character in either, a blank device with no serial, or a repeated alias (the later entry is kept).
- `PUT /config/plotjuggler` refuses an enabled destination the stream would refuse; a startup that cannot enable it says so in `config_warnings`.
- A restarted daemon closes a crashed run's automatic session where that run ended, and session rollover leaves no lines outside every session.
- An over-long received line arriving across several reads is dropped whole and counted once; its tail is no longer stored as a line of its own.
- Received lines lost at a disconnect or detach are counted in `rx_dropped`.
  - A partial line cut off by a disconnect or detach is also named in its sys row.
  - Lines a detach cuts off from a store that is behind count too.
  - The row says whether a detach, a disconnect or a re-attach stopped the port.
- Only one trailing CR is stripped from a received line (SPEC 2.1).
- Device writes (`/send`, `/cmd`, `/break`) no longer queue behind session exports; `latency_ms` and the stored tx row's `ts` start when the bytes were written.
- A reconnect racing a detach, a re-attach or a disconnect of the same port no longer undoes it; it answers 400.
- Live `/wait` and `/assert` matching has its own pool and answers near its deadline, instead of queueing behind history reads; a forbid match found before the deadline still fails the window.
- A `/lines/export` regex over budget on the first page is a 400.
- A cancelled session export or bundle stops its copy, including one whose client disconnects while it waits or builds, and one a daemon stop cancels logs no false `export failed` error; temp copies are removed on cancel, on stop and at the next start.
- Streamed exports release their database snapshot when the client disconnects (the WAL could grow without bound).
- `since_id` below -2^63 is a 422, `POST /ports` bounds `device` and `serial_number` length, and control characters in saved ports are refused.
- `Authorization` with a non-Bearer scheme no longer hides `X-Auth-Token`.
- `purge before_ts` deletes exactly the lines older than the cutoff, by timestamp rather than an id range, and its dry run counts the same rows; lines committed while it runs are kept, so it deletes only the id span it reports.
- `/lines`, `mcu lines` and exports filtered by both port and channel no longer stall the daemon (and drop captured lines) on large captures.
- `since_ts`/`last_ms` windows no longer miss lines when two ports or a marker commit out of stamp order.
- Line ids never go backwards after a purge followed by a failed write.
- An aborted `/plot/export` no longer pins the WAL, and a whole plot export's first byte comes in milliseconds rather than tens of seconds, with no temp-file spill.
- `/plot/channels` after a start or a purge answers in a few seconds rather than 20; retention and size trims no longer trigger a rebuild.
- `/plot/series` with no window is 200x faster on long histories.
- The per-minute page reclaim no longer stalls the daemon for up to 2.5 s; a large freelist now drains about 8x slower.
- A regex scan stopped by the window budget says to narrow the window rather than simplify the regex.
- A non-finite typed value (NaN, infinity) drops that point only, in the daemon, PlotJuggler and the web UI; the rest of the sample is kept.
- Web UI: rows the live stream shed, or that the page dropped while its first backfill ran, show as a divider in the panes and a break in every chart and lane; a reconnect gap breaks the charts too; scrolling to the top of a pane pages past a divider, removing it once the loaded lines fill its hole (a partly filled one moves above them with the count still missing).
- Web UI: ad-hoc channels printed on separate `!p` lines draw as held steps instead of nothing.
- Web UI: a malformed `!can`/`!p` line (bad CAN flags, for example) no longer sets the tick anchor.
- Web UI: a long session name no longer wraps the header.
- Web UI: long tick labels no longer overlap or clip, values near 1e308 draw across the chart (a constant or a 0 to 1.7e308 series too), a soloed y axis ticks at any magnitude, and a long unit is cut on the y axis.
- The plot channel summary rebuild reads one snapshot, so a purge during it can no longer give wrong counts or fail the read.
- `mcu tail -f` survives a row whose `raw` is not a string (under `--decode` too), and `mcu assert` check lines keep device text on one line.
- Windows: `mcu daemon start` from a venv reports success, and `mcu daemon stop` can signal the launcher it recorded (the daemon reports it as `ppid`).
  - The launcher is signalled only while `/status` still names it after the grace, and `mcu daemon restart` waits for it to exit before starting.
- A config port's `device` and `serial_number` are loaded stripped, so a padded serial number matches its board.
- Firmware: `i2c scan` on a shorted bus lists one more address (82), using the same payload budget as the read commands.
- `pydantic>=2.0.2,<3` is declared: an environment holding pydantic 1.x no longer installs a daemon that cannot start.
- Firmware: a received CAN frame on a bus above `MON_CAN_BUSES` is announced once per init as `!e can bus <n> dropped`, no longer dropped without a trace.
- Firmware: an i2c/spi read whose hex answer would not fit the response budget is refused with `ERR 8`, never answered with fewer bytes (internal `monitor_dispatch` callers only; unreachable over the wire).

### Security

- Requests a browser marks `Sec-Fetch-Site: cross-site` or `same-site` are refused (403), except a navigation to the UI.
- Every response forbids framing (`X-Frame-Options: DENY`, `frame-ancestors 'none'`).
- `/lines/export?format=csv` guards the `raw` cell against spreadsheet formulas (CSV injection), as it does device-declared cells.
  - `dir` is daemon vocabulary and stays unguarded; jsonl stays the faithful format.
- **Upgrade:** REST bodies and query strings are validated strictly.
  - An unknown body field or query parameter is a 422 naming it, not ignored.
  - Body types are strict: `"5"` for a number is refused.
- On a terminal, `mcu` shows control bytes from a board escaped (`\x1b`, `\x07`), stderr included, keeping SGR colour; `--json`, pipes and files carry the captured bytes.

## [0.4.0] - 2026-09-09

### Changed

- `/plot/export` no longer refuses selections over a million rows; exports stream in 64 kB chunks.
- Daemon hot path: `/plot/channels` answers from an in-memory summary instead of a full scan, offloaded reads keep one SQLite connection per worker, each received line is tokenized once, WS rows are serialised once for all subscribers, and retention sweeps yield 5 ms between chunks. The CLI no longer imports httpx for `--help`, `--version` or `ai-guide`.
- A named session survives a daemon restart: shutdown closes only the automatic session, and a daemon starting with a named one open resumes it. A restart mid-run used to close it silently and file the rest under an auto session.
- Flash and reset are dropped from the P2 backlog: the agent drives the vendor tools directly.
- Default daemon port is now **8558** (was 8765, which AnkiConnect and other tools also default to). A saved `server.port` in config.toml keeps its value; only the default moves. Clients follow `MCUSCOPE_URL` or `--url` as before.

### Added

- `mcu daemon restart`; `daemon start` prints the web UI URL, takes `--open`, and keeps the daemon's stderr beside the pid file (`mcuscoped-<host>-<port>.err`), printing its tail when the start fails; `restart` comes back on the running daemon's config file and sim port; `--open` is refused with `--json` (the browser's output would follow the JSON).
- "daemon unreachable" at the default URL says how to start one; an ambiguous `-p` lists the aliases; `mcu ports` says when nothing is attached.
- `mcu config path`; `mcu lines --order asc|desc`; shell completion (`--install-completion`, accepted only right after `mcu`).
- `/status` reports `config_path`.
- Web UI: scroll to the top of a pane to page older lines out of the capture; drag on a chart to zoom (double-click resets); a y axis when one trace is shown; a delta time base; regex match highlight and a shown/total readout; double-click copies a line; panes and segmented controls are keyboard and screen-reader reachable.
- Simulator: `--flap SECONDS` drops the TCP client on a timer; an unsolicited `!m` marker every 15 s.
- Exports: `GET /lines/export` (text, jsonl, csv), `/can/frames?format=csv` with id lists, `/plot/export` `decode` (enum labels, `<channel>.<lane>` bit columns), `changes` and `deadband`, `since_ts`/`until_ts` wall-clock bounds on every export, downloads named `<session>_<kind>_<from>-<to>`, and `GET /sessions/{ref}/bundle` (zip of the session db, lines text, per-stream plot CSV, CAN CSV, manifest).
- CLI: `log export --csv` streams from the daemon; `plot export --decode/--changes/--deadband --from/--to`; `can dump --csv -o --id ... --from/--to`; `session export --bundle`.
- Web UI: an export dialog shared by the terminal, plot, digital and CAN panels, with a remembered range (session, clock, or the paused window) and a reset to the whole session; a bundle button in the sessions list.
- Web UI: the outgoing line ending is a select in the command bar, seeded from the port's own setting; the settings-dialog control is gone.
- `mcu lines`, `mcu tail` and `mcu log export` page past the `/lines` 1000-row cap, so any `--limit` is honoured; `log export` writes every matching row by default. Raising `--limit` used to change nothing above 1000.
- `--decode` on `lines`, `tail` and `log export` renders plot samples as named fields from the stream's `!pd` (`s0 state=CHARGING vbat=25.54V io=relay|bat`); `--changes` prints a sample only when a field changed; `--names` picks the fields.
- `--from HH:MM:SS` / `--to HH:MM:SS` wall-clock bounds on `lines` and `log export` (`YYYY-MM-DDTHH:MM:SS` for another day; `--from` after `--to` is refused).
- `log export` without `--limit` streams the window page by page instead of holding it in memory.
- Per-port `identify = false` in config.toml skips the connect-time ping for firmware that is not a monitor.
- `mcu status` shows when the running session started.
- `--retry-ms` on `cmd` and `can tx` retries `ERR 6 busy` until the deadline.
- `mcu plot channels` shows the age of each channel's last sample; `--active S` hides channels not seen in S seconds.
- Port status carries `write_failures`, `last_write_error`, `last_write_error_ts` and `target`; `mcu status` shows a port whose writes fail as `DEGRADED` with the streak, and a failed `mcu cmd` names it.
- The daemon pings once on every connect and reports the monitor's name as the port's `target` (an ST-LINK moved between boards keeps its alias).
- Firmware: `monitor_plot()` emits `!e plot <sid> badarg def|body|len|full` once when it rejects a stream (`!e plot ? badarg sid` for a bad sid or NULL body), which used to be invisible; channel and bit-lane names share one namespace per stream (documented).
- `mcu ai-guide` states that `cmd` and `--send` take the monitor grammar (`can tx ID DATA x`), not the `mcu` sugar.
- `POST /ports/{alias}/disconnect` closes a port and stops retrying while keeping the attachment; `reconnect` resumes it. The web UI's port dot is the switch (green: disconnect, red: reconnect). Held state is in memory only.
- Port status carries `resolved_device` (a by-id path resolved to its `/dev/ttyACM*`) and `description`; the port chip and `mcu status` show them.
- Attach dialog "Bind to this device" box: attach by the stable by-id path instead of the port name. Unticked, the port name is attached as picked (it used to be swapped for the by-id path silently).
- Digital cursor shows the time under it.
- Web UI: the command bar's cmd/raw mode is remembered per port and defaults to raw until the port answers `OK monitor`, so a plain console no longer gets a seq and a 1000 ms timeout on every line.
- Settings dialog: an Identify checkbox per port (the connect-time `ping`; off, the daemon sends nothing to a port that is not a monitor). `GET /config` reports `identify`.

### Fixed

- An unnamed send (CLI without `-p`, web UI port "auto") is no longer refused as ambiguous when a second attached port is disconnected and retrying; only several connected ports are ambiguous.
- Digital lanes froze at the first sample when every field held a constant value: the window's right edge followed the newest transition rather than the newest sample.
- Port chips no longer show the full by-id path, which wrapped the header buttons onto a second line.

## [0.3.0] - 2026-08-31

### Added

- Multi-bus CAN (SPEC 2.4): a digit on the family token selects the controller, `can2 tx ...`, `can2 filter ...`, `can2 stat`, with frames from bus 2 to 9 arriving as `!can2` to `!can9`.
  - `mcu can tx/dump/stat/filter` take `--bus N`; `/can/frames` rows carry a `bus` column (old rows read as bus 1 by migration).
  - The web UI CAN table groups by port and bus with collapsible dividers; the simulator exposes a second bus (`info` answers `can=2`) so the feature is testable with no hardware.
  - The firmware monitor's `mon_can_*` shims gain a bus field; a single-bus port needs no change.
- PlotJuggler streaming (SPEC 3.7): decoded plot points mirror to PlotJuggler's stock UDP Server source as one JSON datagram per line, fire-and-forget from the ingest path so a viewer can never cost a capture row.
  - `mcuscoped --plotjuggler [host:port]` (or `--pj`), a `[plotjuggler]` config section, live toggle via `mcu plotjuggler on|off` / `mcu pj` and `PUT /plotjuggler`, and a settings section in the web UI.
  - Destination grammar is strict: ASCII-digit ports, `[addr]:port` for IPv6, multicast/unspecified/broadcast refused; non-finite samples are dropped rather than emitted as JSON PlotJuggler rejects.
- `mcu status` reports an available release.
  - The check (SPEC 3.6) previously reached only the web UI badge, so nobody driving the CLI - an agent, or any headless bench - ever learned a newer version existed.
- `GET /sessions?name=` filters by name; `mcu` resolves session names through it.
- A cross-language plot-grammar fixture (68 cases) drives `protocol.py` and `plots.js` from one case list, and a CSV-cell fixture does the same for the daemon's export and the web UI's.

### Security

- `POST /ports` is held to the config-write bar (token required off loopback), like `PUT /config/*` and `PUT /plotjuggler`.
  - A device string can name a network destination (`socket://`, `rfc2217://`), so on a tokenless non-loopback bind a network client could point a port at a host of its choosing. Detach and reconnect stay open; loopback clients are unaffected.

### Changed

- Python 3.10 is now supported (the floor was 3.11).
  - Config reading moved from stdlib `tomllib` to `tomlkit`, which the write-back path already used; `LineClass` no longer needs `enum.StrEnum`.
  - websockets 17 requires 3.11, so the 3.10 CI leg runs websockets 16; both majors are supported.
- Dependency floors now name the oldest versions that work: typer 0.26 (`mcu` failed to import below it), uvicorn 0.35 (WS backpressure shedding was silently off below it), fastapi 0.115.7; dev: pytest-asyncio 0.23.5, starlette 0.44.
- The release check is driven by demand rather than by a polling task: one check at daemon startup, and one per `GET /status` when a check is due.
  - The daily cache was always the real rate limit, so the timer decided nothing the cache did not.
- Dismissing the web UI's update badge now hides that version only; a newer release shows it again.
  - It replaces a day/week/month/permanent snooze ladder whose stored rung index needed guarding against corruption.
- `MCUSCOPE_UPDATE_CHECK=0|1` overrides `[update] check` in both directions, as SPEC 3.6 said; the code had ANDed them, so a config `false` could not be lifted.
- The daemon's attach-time `!pd` scan uses the same 20000-id lookback as the web UI (SPEC 2.5 names the shared bound).
- Duplicate channel or lane names in one `!p`/`!pd` line are malformed on the daemon and in the browser alike (SPEC 2.5); two writers for one name misaligned chart arrays.
- SPEC 2.4 pins `can stat`: counters cumulative since init, state current rather than latched.
- `cli.py` split into `cli_output`, `cli_client`, `cli_argv` and `cli_daemonctl`; the `mcu` entry point and exit-code contract are unchanged.
- Firmware C sources indent with tabs.

### Fixed

- A stalled WebSocket client buffered the whole capture in the daemon instead of being shed: uvicorn's websockets-sansio protocol gates send on a writable Event that nothing cleared. The two callbacks are wired to it; shed engages in seconds and per-connection memory is bounded.
- `mcu tail -f` subscribes before its snapshot, so the overlap is staged and deduplicated instead of lost; the web UI pages its reconnect backfill past the 1000-row clamp and draws a divider for what it left behind.
- `GET /lines?since_ts=` planned as a full reverse scan on the event-loop connection; it takes the id-anchor treatment its `last_ms` sibling had.
- Firmware monitor: three ASan-confirmed overreads closed (`emit_ok`, `cmd_info`, `drain_can`); `emit_err` clamps codes to the wire table; `emit_can_event` masks the id to the flag width; `monitor_mark` refuses a tick-sigil forgery and returns `int`.
- Daemon and store: unbounded integer query/body params are 422s instead of 500s; `/purge` refuses a future `before_ts`; the session-export temp copy lives beside the capture and is removed on disconnect; `write_errors` counts the fast-fail path; `active_session` runs on a partial index; `delete_range` joins the sweep lock and sessions serialize under a store lock.
- Serial link: the detach handle-close joins the pool; a shutdown-window attach is a 400; a `serial_number` attach reports the device it opened; a `/cmd` cancelled mid-write no longer leaks its pending entry.
- Config and startup: wrong-typed boolean keys refuse the load (SPEC 3.3); a port whose baud the API would refuse is skipped; a corrupt capture-lock record no longer crashes the refusal that names the holder; `--host ""` is refused; startup refusals go to stderr.
- CLI: `daemon start` no longer clobbers a live daemon's pid record or reports success with a dead child; a closed stderr no longer turns errors into exit 0; every `--json` error path emits one JSON object even with stdout a closed pipe; `purge --before-days` refuses values below 1 (a negative silently meant `--all`); non-ASCII tokens, WS binary frames and unbounded `--timeout` map to exit codes instead of tracebacks.
- Simulator and protocol: non-finite `f4` samples and post-scale overflows decode to generic events instead of raising out of the store; the sim sheds a slow reader instead of dropping it; the host tokenizer matches `monitor.c` byte for byte; `can.js` CSV quoting matches the daemon's export (leading tab/CR formula guard).
- Web UI: seeded `/plot/series` duplicates no longer misalign a chart's y array; the paused analog chart snapshots at freeze like the digital panel; a lane named `toString` no longer draws in the previous lane's colour; the hidden digital panel keeps its repaint request; `/status` polls coalesce; a capture reset re-seeds the way the first connect does.
- PlotJuggler streamer state is one immutable (socket, address) pair swapped whole, so a concurrent reconfigure cannot raise into the ingest path or pair a reported destination with another request's address.

## [0.2.0] - 2026-08-09

### Added

- `GET /status` reports `write_errors`, and the web UI port chip flags it. A capture write that failed was invisible on every surface: lines counted as received, nothing stored, everything green.
- `GET /status` reports `writer_alive`; `mcu status` and the web UI announce a stopped capture writer loudly. A dead or wedged writer previously read healthy everywhere and made shutdown hang forever.

### Removed

- The pre-release unkeyed `mcuscoped.pid` fallback in `mcu daemon stop`. Every released version writes the host-port-keyed record, so the fallback could only match a pre-0.1.0 development install.

### Changed

- With `--json`, a destructive command refuses to prompt on a non-interactive stdin instead of blocking on it. `echo y | mcu --json purge --all` now fails; pass `-y`.
- Web UI CPU use under load cut sharply.
  - The terminal appends new rows instead of rebuilding its window every frame, and digital readouts and the cursor batch their DOM writes.
  - Tables and chips repaint only on change, and timers idle when the tab is hidden.
- The simulator enforces the firmware monitor's limits, so behaviour certified against `--sim` matches a real board.
  - 12 tokens per command, 255-byte lines, oversized responses answered `ERR 8 overflow`.
- `mcu attach` reports "(connecting; see 'mcu status')" instead of implying the link is already live; `mcuscoped --port` is validated 1..65535 up front.
- `mcuscoped` always writes a pid file and a `mcuscoped-startup.log` (URL, pid, interpreter report, stop instructions) in the data directory, so `mcu daemon stop` works however the daemon was started.
  - Previously only `mcu daemon start` wrote the pid record, and a daemon launched as `mcuscoped` was invisible to it.
- `--version` flags the windowless-interpreter case explicitly (`[windowless: no console - output and Ctrl-C unavailable]`).
- Install docs: on Windows, pin a real interpreter with `uv tool install mcuscope --python 3.12` when PATH is led by a vendored runtime (KiCad, GIMP, Blender).
- `POST /shutdown` (loopback only): a graceful stop over REST, now the primary channel of `mcu daemon stop`.
  - `GET /status` reports the daemon's `pid`, so a fallback kill targets the serving process rather than a Windows launcher shim.
- Update notice: the daemon asks PyPI once a day (cached across restarts) whether a newer MCUscope exists, and the web UI shows a badge naming it.
  - Dismissing snoozes on a ladder (a day, a week, a month, then permanently for that version) rather than silencing it for good on the first click.
  - Off with `[update] check = false`, the Settings dialog, or `MCUSCOPE_UPDATE_CHECK=0`.

### Fixed

- On Windows, piping `mcu` into a closed reader (`mcu tail -f | head`) produced a crash log and a traceback instead of the clean exit 0 POSIX gets.
  - Windows reports a closed pipe as `OSError(EINVAL)`, which no handler recognised.
- `mcu can dump -f` went silent forever after `mcu purge --all` or a database recreate; it now notices the capture change and re-seeds.
- A rejected WebSocket token exited 3 ("daemon unreachable") where the same failure over REST exits 1.
- Detaching a port erased the drop count of lines lost in that same detach; the counters now survive reattach.
- `mcu plot export -o` left an empty file behind when the daemon refused the request.
- A `/cmd` cancelled mid-write (client disconnect) leaked its pending-response entry until the next disconnect.
- A slow startup and a token-guarded daemon's 401 both read as "no answer" inside the 2 s status timeout.
  - A pid record is now removed only when its pid is dead, and stop falls back to `POST /shutdown` when there is no record to read at all.
- `mcu daemon start` deleted a pid record naming a different daemon, and `mcuscoped` took over a live one. Two daemons on one port could trade the record and leave the survivor unrecorded.
- One malformed line discarded the rest of its receive batch, up to 1000 lines, counted nowhere.
  - A number above CPython's 4300-digit limit raised past the protocol error handlers; six parsers now gate token length, and an oversized terminated line is dropped and counted like an unterminated one.
- The simulator's listener could outlive its serving thread after a transient accept error, so the daemon reconnected to a socket nothing was reading and reported the port connected.
- `GET /sessions` counted each session's lines with an open-ended scan: 2.06 s at 1M lines, 19.2 s at 500 sessions, on the event loop. Now 88 ms and 67 ms.
- `POST /purge --dry-run` and `POST /assert` counted rows on the event loop, the latter undoing the containment of the regex work beside it.
- `GET /can/frames` with `port=` or `last_ms=` drove its join from the line table and sorted every matching frame before applying the limit: 131 ms against 0.4 ms at 1M lines.
  - `GET /plot/channels?port=` scanned the line table a second time to build an id list, 190 ms against 138 ms.
- `POST /cmd` answered 500, with a full traceback in the daemon log, for an empty or whitespace-only command instead of 400. `POST /wait` and `POST /assert` shared the path.
- A session reference of more than 4300 digits answered 500 with a traceback, on `GET /sessions/{ref}/export` and on every endpoint taking `session=`.
  - The id branch of the lookup reached `int()` past CPython's conversion limit.
  - A session *named* with another script's digit also resolved to the id that digit converts to, returning a different session's lines.
- The CAN RTR length digit was accepted in any script, on both the receive and the `can tx` path, so `!can 1 r 100 ٣` decoded into a stored CAN frame instead of being kept as a generic event.
  - The simulator accepted the same tokens for its command arguments.
- Web UI: a typed-stream definition carrying an out-of-range enum value built a chart in the browser that the daemon had rejected outright.
  - The panel showed a stream `mcu plot` and `/plot/series` had never decoded.
- Web UI: the CAN sidebar showed frames whose id is out of range for their own flags, which the daemon drops from `can_frames`.
  - The table disagreed with `GET /can/frames` and `mcu can` about the same line.
- Web UI: a freshly loaded page could not decode the typed `!ps` samples in its own backfill, so the typed and digital charts came up empty while the ad-hoc chart was full.
  - A sample is undecodable until its `!pd` definition has been seen, and the definitions rebroadcast less often than the backfill window is wide, so whether the charts drew anything was luck.
  - The definitions are now fetched and applied before the backfill replays.
- The store writer commits at most 1000 rows at a time, cutting worst-case event-loop occupancy from 92 ms to 8-11 ms, and warns on a commit over 100 ms.
- `journal_mode=WAL` reports refusal in its result set rather than raising, so a capture silently running in rollback-journal mode now warns.
- `mcu tail -f --match` compiled the pattern with stdlib `re` while the daemon uses `regex`, so a pattern the daemon accepted printed one matching line and then crashed the client.
  - The client now also carries the daemon's per-match timeout: without it a catastrophic pattern hung the follow with no error and no working Ctrl-C.
- `mcu-sim --pty` retried a dead pty master ten times a second forever instead of exiting.
- A malformed `--url`, an unsupported scheme, a non-numeric port and a null `uptime_s` from a stray responder produced tracebacks instead of exit codes.
- `--json` emitted prose for `ai-guide` and `--version`, nothing at all for a usage error, and a bare newline for a `log export` that matched nothing.
- Web UI: a terminal rebuild left rows in the pane queue that it had already folded into the view, so a backfill landing mid-stream duplicated lines.
- `mcuscoped` output could die with `UnicodeEncodeError` when redirected to a file on a Windows console code page, losing the startup diagnostic it was trying to print.
- `mcuscoped`'s port probe checked only the first resolved address, so a conflict on any other address of a multi-homed host slipped past it.
- `mcu session export` left a truncated file at the destination when the transfer failed.
- `db_max_bytes` in `GET /status` reported the configured size cap rather than the one in force.
- `mcu devices` on Linux listed 32 phantom `/dev/ttyS*` ports, burying the one real adapter.
  - A port is now hidden only when the kernel itself reports `PORT_UNKNOWN` for it.
    - A real on-chip UART (a Raspberry Pi mini-UART, an ARM SoC's `ttyS1`, a `ttyAMA0`) is still listed, and a USB adapter is never judged at all.
  - pyserial means to hide these already, but its check went stale when Linux 6.7 moved the devices onto the `serial-base` bus.
- Web UI: a failed backfill froze the whole stream.
  - The error path referenced an unimported name, so it raised, and the staging area it should have drained was never released.
    - Every later row was queued into it instead of rendered, while the stream pill stayed green and the rate readout kept counting.
- A second `mcuscoped` on a port already in use deleted the running daemon's pid record on its way out, leaving the first daemon running but unstoppable by `mcu daemon stop`.
  - The port probe now runs on Linux too, before anything is claimed; it was Windows-only, and POSIX only learns of the collision from inside uvicorn, after the pid record is taken.
- `mcu --json` could emit a stream-repair warning on stdout, ahead of the JSON object, breaking any parsing consumer.
  - It goes to stderr now, and no longer claims to have "reattached to the console" on Linux, where it never does.
- Web UI: a paused terminal pane retained every matching row just to count it.
  - A backgrounded tab throttles the flush to about once a minute, so a fast capture held tens of thousands of rows, past the point the shared buffer had evicted them.
- Config integers were read with bare `int()`, the other half of the `bool()` defect below.
  - `port = true` became port **1** (a bool is an int in Python), `port = 8558.7` truncated in silence.
  - A typo'd `port = 99999999` was taken as written and failed much later from inside the bind, naming neither the file nor the key.
  - A wrong type now fails the load with a message naming the key, and an out-of-range value warns and keeps the default.
- A `[[ports]]` entry's `autoconnect = "false"` was read as **true**, so the port opened itself on every start - the exact opposite of the setting - and `baud = true` became **1 baud**.
  - Both are now refused with a warning that names the port, and one bad entry no longer affects its neighbours.
- Web UI: every numeric settings field was read with `parseInt`, which takes the leading digits and stops, so `1e9` in the port box passed the 1-65535 check and saved port **1**.
  - A field that is not a whole number is now rejected outright.
- `[update] check` and `[storage] auto_session` were read with `bool()`, so a hand-edited `check = "false"` enabled the update check and `check = 0` disabled it.
  - A non-boolean is now refused with a warning and the default kept.
- The update check re-asked PyPI on every restart when upstream had only pre-releases: the cache it wrote for that case was rejected by its own loader, voiding the once-a-day guarantee.
- A reader thread that outlived detach could raise `RuntimeError: Event loop is closed` and leak the device handle it had just opened; a `socket://` open blocks longer than the shutdown join allows.
- Line counters carried across a detach and re-attach covered `lines_rx` and `rx_dropped` but not `lines_tx`, so `mcu port reconnect` reset the transmit count to zero.
- Port enumeration was cached for less time than the reconnect poll interval, so the cache never hit and every poll paid for a full scan.
- A store shutdown that cancelled its writer left queued writes with futures nobody resolved; the awaiting task hung until the loop closed.
- Plot channel export ran on the default thread pool, where it could queue ahead of the reader-thread joins that detach and shutdown depend on.
- `mcu daemon stop` waited out its full grace period and then failed after a shutdown that had worked, when the daemon was left unreaped as a zombie by the script that spawned it.
- Web UI: cancelling a colour picker leaked a focusable hidden input into the page, one per cancel.
- The simulator died permanently on `can tx 7FF` (and `can tx 1FFFFFFF x`).
  - The echo frame is id+1, which at the top of the range is out of range, so formatting it raised from inside the event pump and unwound the serving thread.
    - The listening socket stayed open, so the daemon reconnected into a backlog nobody was accepting from and reported a healthy port that never produced another byte.
  - The echo id now wraps within its own range, and a client session can no longer take the listener down with it.
- `mcu -p board lines --match -p ...`: a global option before the subcommand stopped argv hoisting from resolving that subcommand, which disabled the guard protecting subcommand option values.
  - `--port` could silently become the next option (`--port=--limit`), or the command failed with a confusing "unexpected extra argument".
- `mcu wait --send ...` could report a timeout without examining a single captured line.
  - The send is given the same timeout as the whole wait, so a slow command consumed the window and the loop exited before draining a queue that may already hold the match.
  - Exit 2 on a run that actually matched.
- A cancelled `/cmd` (client disconnect, Ctrl-C) leaked its pending-sequence entry, because `CancelledError` is a `BaseException` and escaped the cleanup that `TimeoutError` triggered.
- Events are dispatched on their whole first token rather than a prefix, so a future `!candy`/`!power` line is no longer forced through the CAN or plot decoder and logged as a bogus decode failure.
- Sequence numbers are parsed strictly (ASCII decimal only): bare `int()` accepted `+17`, `1_7` and non-ASCII digits, so a garbled response could resolve the pending command for seq 17.
  - The plot, enum and marker-tick grammars likewise use `[0-9]` rather than `\d`.
- SPEC 2.4: the simulator refused to reject `can filter <id> <mask> r`, answering `OK` to a filter it could not honour.
- A startup failure between opening the store and serving left the writer task, the retention task and the SQLite connection running with nothing to stop them.
- `PortManager` kept one carried-counter entry per alias ever attached, with nothing to prune it.
- Web UI: a large `*<scale>` factor could carry a finite sample to `Infinity`, and uPlot's auto-range then returned `[NaN, NaN]`, silently erasing every series on that chart.
- Web UI: the shared tick anchor was set from an unbounded value, so one corrupt line could shift every terminal timestamp and chart x-axis for the rest of the session.
- Web UI: two overlapping stream reconnects could drop the rows captured across the gap entirely, because the staging buffer was a single global shared by every socket. Staging is now per-connection.
- Web UI: after a capture-database reset the terminal stayed empty until new traffic arrived, and a failed backfill was completely silent while the UI still looked live.
- Web UI: the colour picker never opened in Firefox, which cannot drive a detached `<input type=color>`.
- Windows: saving settings from the web UI rewrote the whole `config.toml` with CRLF endings, the one text write in the package that did not pin `newline=`.
- Windows: a serial port could be closed while a write was still in flight in the driver.
  - The reader thread's handle was left held if its join timed out, blocking a re-attach of the same COM port (which Windows opens exclusively).
- Windows: `.js` and `.css` content types are pinned rather than read from the registry.
  - A stale `HKEY_CLASSES_ROOT` entry would make the browser refuse `app.js` as a module script and leave the whole UI blank.
- Windows: session-export filenames avoid the reserved device names (`CON`, `COM1`, ...), which cannot be saved even with an extension.
- Windows: database paths are compared case- and separator-insensitively, so re-entering the same path no longer reports a spurious restart requirement.
- Windows: the simulator's listener uses `SO_EXCLUSIVEADDRUSE`, since `SO_REUSEADDR` there permits binding an address that is already actively listening.
  - A second `mcu-sim` started silently and was never connected to.
- A `config.toml` saved with a UTF-8 byte-order mark is now read normally, and a save writes it back without one.
  - `tomllib` rejects a BOM with "Invalid statement (at line 1, column 1)", naming neither the cause nor the fix.
    - On Windows a BOM is what the ordinary tools produce (PowerShell's `Out-File -Encoding utf8` always writes one).
    - Hand-editing the config the obvious way there stopped the daemon starting over an invisible character.
- Windows: `mcuscoped` now refuses a port that is already being listened on, instead of binding it anyway.
  - uvicorn sets `SO_REUSEADDR` unconditionally, which on Windows (unlike POSIX) permits that bind.
    - A second daemon, or a first one on a port some other service held, started, printed its web UI URL and was never reachable.
- Windows: settings saves, the `daemon start` pid record and the update-check cache retry the atomic file replace.
  - The replace fails there whenever another process holds a transient handle on either file (an on-access virus scan or the Search indexer is enough).
  - POSIX `rename(2)` never fails this way, so a save that always worked on Linux could be lost on Windows.
- Windows: `mcu daemon start` no longer exits with a traceback if the pid file cannot be written.
  - The daemon records itself on startup anyway, so it warns and carries on rather than breaking the exit-code contract with a live daemon already spawned.
- `GET /devices` enumerates serial ports on a worker thread.
  - That call is a cheap sysfs walk on Linux but a setupapi query on Windows, where it held the event loop - freezing every WebSocket feed and every other request - for as long as the scan took.
- Exporting a session that is still running answered `400`.
  - With no `end_id` yet, the copy resolved its upper bound through the event loop's SQLite connection from the worker thread it runs on, which sqlite3 refuses.
    - Every existing test stopped the session first, so the branch was never exercised.
  - This affected the automatic session the daemon always has open, on every platform.
- Windows: `mcu devices` could die with a `UnicodeEncodeError` when redirected to a file or pipe, breaking the exit-code contract, because a redirected stdout falls back to the locale encoding.
- Importing `mcuscope` on Python older than 3.11 now says so, naming the interpreter, instead of failing later with `No module named 'tomllib'`.
- A port that could not be opened wrote a `sys` row per retry, so an unplugged board buried the capture (and the terminal panes) in thousands of identical "open failed" lines.
  - The reason is now recorded once per disconnected episode and the reconnect reports the retries as a count: `port board connected: /dev/ttyACM0 (after 214 failed attempts)`.
- The status bar's lines/s readout appeared and vanished with the traffic, shifting the port chips sideways every second.
  - Its box is now reserved (fixed width, tabular figures) and the "terminal paused" notice moved to its own badge, so the chips hold still.
- Windows: under a GUI-subsystem interpreter (`pythonw.exe`), `mcuscoped` ran with no output and could not be stopped with Ctrl-C.
  - uv can select `pythonw.exe` as a tool venv's base via KiCad's vendored runtime.
  - The daemon now attaches to the parent's console (`AttachConsole`, falling back to a new one), reattaches the std streams to it, and installs the console control handler that a late attach never gets.
    - The banner appears in the launching terminal and Ctrl-C shuts down gracefully again.
- Windows: `mcu daemon stop` was never actually graceful, and its liveness probe (`os.kill(pid, 0)`) could itself disrupt or miss the daemon.
  - `CTRL_BREAK_EVENT` cannot reach a process on another console, and the detached daemon has none.
  - Stop now goes through `POST /shutdown`, waits for the process to exit with a real non-signalling probe, and hard-terminates only as a last resort, verifying afterwards that nothing still answers.
- A pid record left behind by a crashed daemon could block the next daemon's claim once the pid was recycled, leaving it unstoppable by `mcu daemon stop`.
  - A claim now only defers to a record naming its own live parent (the `daemon start` launcher).
  - The record is also claimed atomically, written atomically by `daemon start`, and released even when startup fails before the server runs.
- Closing the terminal window on Windows hard-killed an attached daemon before its graceful shutdown could run; the console close event now holds the ~5s grace window open while shutdown proceeds.
- Release workflow: the changelog section is extracted and validated before the PyPI publish, so a forgotten changelog roll no longer burns the version number.

## [0.1.1] - 2026-07-28

### Added

- Firmware markers (SPEC 2.5): `!m [@<tick>] <text>` lets the MCU annotate the timeline itself; a well-formed marker is stored on the `marker` channel alongside `mcu mark` and session boundaries.
  - Firmware calls `monitor_mark("calibration start")`, or just `printf("!m boot done\n")` with no library at all.
- Scientific notation in plot values and `*<scale>` factors (SPEC 2.5).
  - Float `printf("%g")` output such as `1.2e-05` is plotted instead of silently dropped, and `*9.8e-4` reads better than `*0.00098`.
- Simulator: a `mark <text>` command, so the marker path is exercisable end to end with no hardware.
- `mcuscoped --version` and `mcu --version` report which Python interpreter is running.
- Any startup crash is also written to a `mcuscoped-crash.log` in the data directory, so a failing install can always be diagnosed.

### Fixed

- Windows: `mcuscoped` exited 1 with no output at all when run under a Python whose standard streams are null - notably KiCad's bundled interpreter, which `uv tool install` can select from `PATH`.
  - Null streams are now reattached to the console (`CONOUT$`) at startup, and uvicorn's colour autodetection (the crash site) is bypassed with an explicit `use_colors=False`.
- An automatic session whose only device traffic was a firmware marker is no longer dropped as empty when it closes.

## [0.1.0] - 2026-07-28

First public release.

- `mcuscoped` daemon: owns the serial port, timestamps and stores every line in SQLite, and serves a REST + WebSocket API on `127.0.0.1:8558`.
  - Capture continues with no client attached, and an OS-level lock enforces one daemon per capture database.
- `mcu` CLI: the primary human and AI interface over that API, with `--json` output everywhere and a stable exit-code contract (0 success/match, 1 error, 2 timeout, 3 daemon unreachable).
- `mcu wait` and `mcu assert`: block on a pattern, or judge a whole capture window with a pass/fail exit code, so agents and CI can branch on results instead of reading logs.
  - Multiple `--expect`/`--forbid` conditions, live or retrospective.
- Sessions: name a span of the capture, list, export as a standalone SQLite database, and delete (label alone or with its data).
  - The daemon opens an automatic session per run; retention keeps the newest N sessions regardless of age, with an optional size cap.
- Web UI: multi-pane terminal, port setup, decoded CAN view, realtime analog plots, and a combined digital/enum panel sharing one time base and cursor.
  - Settings page edits the full config (bind address, storage, saved ports) with the TOML file staying hand-editable.
- LAN access with an optional access token (`MCUSCOPED_TOKEN` / `--token`), rate-limited against brute force; loopback clients stay friction-free.
- Portable C firmware monitor module (`firmware/monitor/`) implementing the command/event protocol, with host-compiled tests and an integration guide.
- Hardware-free simulator (`mcu-sim`, or in-process via `mcuscoped --sim --open`): fake I2C, SPI, GPIO, ADC and a CAN heartbeat, so the full stack runs and is tested with no board attached.
- Cross-platform: Linux and Windows 10/11, `COMx`, `/dev/tty*` and `socket://host:port` device strings.

[Unreleased]: https://github.com/dwatman/mcuscope/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/dwatman/mcuscope/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/dwatman/mcuscope/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/dwatman/mcuscope/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/dwatman/mcuscope/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/dwatman/mcuscope/releases/tag/v0.1.0
