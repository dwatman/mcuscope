# Web UI UX review 2026-09-14: merged summary

Sources: `terminal-cmdbar-status.md` (T-Fn), `sidebar-can-plots-digital.md` (S-Fn), `dialogs-onboarding-a11y.md` (D-Fn), `live-screens.md` (L-n, numbered in file order).
80 findings in, 1 dropped after checking it against the code.

## Top 12

**1. The digital / enum panel is off screen in the default view (M, L-2, owner-flagged).**
With two analog plots at the 360 px default sidebar width the digital panel sits below the fold, and nothing on screen hints it exists.
A user with a digital-only stream sees an empty-looking Plots section and concludes the feature is missing.
Fix: cap each analog chart's height so N charts plus the digital panel fit, or pin the digital panel as its own collapsible section below the charts, or add a widget chip strip at the top of the Plots section that scrolls to and toggles each widget.
L-3 (a third plot widget is below the fold too) is the same root cause.

**2. The status bar tells the user to set a token in a place the daemon ignores (S, D-F1).**
The daemon chip tooltip says to set `server.token` in `config.toml`; `config.py:341` explicitly ignores that key and logs a warning, so the user ends up with an unauthenticated daemon on 0.0.0.0 and a page that never asks for a token.
It is the only in-product instruction for the one security-relevant setup step, and it disagrees with the Settings copy, which names `--token` / `MCUSCOPED_TOKEN` correctly.
Fix: rewrite the tooltip to name `--host 0.0.0.0` and `MCUSCOPED_TOKEN`, byte-identical in `index.html:16` and `DAEMON_TITLE` at `statusbar.js:43`.

**3. The terminal, the largest surface on the page, has no empty state (S to M, T-F1 + D-F3 + L-14).**
A pane with all channels unticked, a pane whose regex matches nothing, a fresh daemon with no ports and a dead WebSocket all render as the same empty black rectangle.
CAN and Plots both carry written empty states; the panel a newcomer actually looks at carries none.
Fix: one `.empty-state` in `render` when `pane.rows.length === 0`, with copy chosen from state already in hand (waiting for the first line, N in scope but none match, nothing in scope, cleared), plus `no ports attached` in `#ports`.

**4. The CAN byte-change highlight diffs across a second, not across a frame (S, S-F4).**
`updateCanRow` compares against the last rendered payload and the render tick is 1 Hz, so at the simulator's 10 Hz a diff spans ten frames and at 100 Hz the whole payload lights up.
The feature exists to answer "which byte moved when I pressed the button" and above a few Hz it stops answering it.
Fix: keep `e.prevHex` in `canIngest` and OR a per-frame change mask into `e.moved`, cleared when `updateCanRow` paints.
This also settles the SPEC 9.1 wording, which promises a per-frame diff.

**5. Hint text and empty states fail WCAG AA in both themes (S, D-F2).**
`--text-faint` measures 2.98:1 light and 2.82:1 dark against its own background, and it carries every hint, every empty state and every Settings section heading at 10.5 to 12 px.
That is exactly the copy a newcomer needs, rendered as decoration.
Fix: `--text-faint: #646f7b` light and `#8b96a3` dark (5.12:1 and 6.10:1), and `--accent: #0b7a8d` in the light block, since the current `#0e8ba0` is 4.02:1 on white and carries the update-badge link and `.iconbtn.on`.
Verified: all three measured ratios reproduce exactly.

**6. Two boards' identical stream ids and channel names merge silently (S to surface, M to fix, S-F6).**
Charts are keyed `"s" + sid` and digital lanes by name alone, while `plotDefs` and `canRows` are correctly keyed by port.
Attach two boards that both declare `temp` and the two traces interleave into one zigzag that reads as a hardware fault, with no port named anywhere in either panel.
Fix (cheap): record the contributing ports per chart and lane and append them to the title and lane gutter when more than one has contributed.
Fix (right): key `charts` and `digitalLanes` by `port|sid|name`, which is what SPEC 9.2 already anticipates.

**7. CAN staleness uses a fixed 3 s and the colours are the wrong way round (S, S-F5).**
An id that arrives once a minute cycles green to grey every three seconds, while a 1 kHz id that has dropped 2000 frames still reads fresh.
Fresh is rendered in `--good` and dead in the dimmest colour on the panel, so "everything normal" is the loudest state.
Fix: threshold `max(3, 5 * period/1000)` using the EWMA period the row already holds, and re-map to plain `--text` fresh, `--warn` stale, `--crit` past ten periods.

**8. A failed detach, disconnect, reconnect or session action leaves no readable message (M, D-F5).**
`flashDaemonError` reddens the chip border for 2500 ms and puts the reason in a `title`, so reading it means noticing the flash and landing the pointer inside 2.5 s.
These are the destructive actions, and after 2.5 s there is no trace the action failed at all.
Fix: keep the flash as the cue and add a persistent one-line strip under the status bar with the last failure and a dismiss `x`, cleared on the next successful poll.
Cheaper interim (S): 8 s timeout with the message as visible text in the chip.

**9. A drag zoom leaves no visible state and no visible way out (S to signal, M to do fully, S-F3).**
Dragging across a chart zooms every chart and lane and pauses all four surfaces, while the window selector still shows `30s` and nothing names the drawn span.
The only exits are an undocumented double-click or resuming a chart, which drops the zoom globally and leaves the other panels frozen.
Fix: clear the `.on` state on every window group while `getZoom()` stands, and put a chip in each `.plot-head` and `#digitalHead` reading the zoom span with an `x` that calls `clearZoom()` plus `pauseAll(false)`.

**10. The CAN table scrolls sideways at the default width and `age` is what falls off (S, S-F1 + L-7).**
Six columns at 10 px padding come to roughly 435 px of content in a 360 px pane, so the column that says a bus went quiet is off screen until the user drags the divider.
The 8-byte DATA cell also wraps over three lines, so the table jumps in height as payloads change.
Fix: tighten `table.can td/th` padding to `5px 6px`, move `count` to a row `title` or a narrow right-aligned cell, and verify at exactly 360 px.

**11. The command bar never says which port "auto" resolved to (S, T-F3).**
`targetAlias()` already computes the resolution exactly as the daemon does and uses it only to seed eol and mode; the select shows the literal string `auto`.
This is the control that writes bytes to hardware, and with two boards attached "which one am I about to talk to" is the question it does not answer.
Fix: label the option `auto (sim)` in `populateCmdPort` when `targetAlias()` is a real alias, leaving the value `auto` so `cmdPortValue()` is untouched.

**12. Settings loses typing silently, and each section saves separately (M, D-F9).**
Five sections, five Save buttons, no dirty indication: edit Bind host, then edit Retention, press the Storage Save, close, and the host edit is gone with no sign it existed.
Escape and the `x` discard everything unsaved, and removing a Ports row deletes it on screen immediately while changing nothing until Save.
Fix: mark a dirty `.cfg-sec` with a primary-coloured `Save *`, cleared on a successful save, and confirm on close when any section is dirty.

## Quick wins (remaining S)

- T-F2: add `.chip .meta.rate` a reserved tabular-nums box like `#lineRate`, or the per-port rate keeps shoving the detach and reconnect buttons sideways.
- T-F4: a CAN id click overwrites the last pane's regex with no undo; stash the old pattern in `filterPaneTo` and have `unfilter` restore it.
- T-F5: call `scheduleResizeRedraw` from `showResult` and `hideResult`, so the auto-dismissing strip stops leaving a blank band at the bottom of every pane.
- T-F6: prefix the global figure `rx 78/s` and widen its box to 9ch, so it cannot be read as a property of `db 8.6 MB` beside it.
- T-F7: scroll-to-top history paging is undiscoverable; render a first row reading "scroll up for older lines from the capture" on a paused pane.
- T-F8: replace the per-pane constant `5000 cap` with `dbl-click a line to copy` and move the cap into the `.shown` title.
- T-F9: grow `.match-group .match` to 260 px on focus and put two worked examples in its resting `title`.
- T-F10: give `.chip .meta.target` its own treatment (italic, or an arrow prefix) so the target is not four dim tokens beside `/dev/ttyACM0`.
- T-F12: `docs/img/webui.png` is three releases stale and is the PyPI landing image; recapture per `docs/SCREENSHOTS.md`.
- T-F13: `submitMarker` is silent on success while visible on failure; call `showResult("ok", "marker", text, null)`.
- T-F14 + L-11: `title="panes open / maximum"` on `#paneCount`, since `2 / 5` is an unlabelled number.
- T-F15: the `none` line-ending tooltip promises a Ctrl-C a browser text input cannot capture; fix the copy to say it appends nothing.
- T-F16 + L-17: with no ports attached, disable `#cmdInput` and `#markerBtn` with the placeholder "attach a port to send commands".
- T-F17 + L-19: the brand's `web ui` tag is filler in the page's most prominent slot; drop it or make it carry the daemon version.
- S-F2: collapse the CAN section to its head while `canRows` is empty, so a board with no CAN bus stops losing 45 percent of the sidebar.
- S-F8: move the palette index into `chrome.js` so a chart's first channel and the first digital lane stop sharing one teal.
- S-F10: the chart legend's time readout is labelled `Value:`; set `label` on series[0] from `state.timeMode`.
- S-F11: a soloed channel's y axis has no unit; add `label: chart.unit.get(shown[0])`.
- S-F12 + L-8: no CAN header has a `title`, and `ms` is an EWMA period that prints `1.5s` in some cells; rename it `period` and gloss all six.
- S-F13: drag-to-zoom, double-click reset and hover-a-log-line-to-scrub announce themselves nowhere; one dim hint span beside `#plotXLabel`.
- S-F14: the CAN export leaves the `ids` field enabled and inert under `snapshot`; gate it on `format === "history"` and relabel `What` to `Source`.
- S-F15 + D-F12: sidebar width, the CAN/Plots split and the expand state are the only layout state not persisted; three validated `localStorage` keys.
- S-F16: a collapsed chart's head reads only `stream 0`; append the shown channel names so collapse stops hiding what you collapse for.
- S-F18: the CAN table's `Reset` is the only capitalised control in the sidebar and means what `clear` means everywhere else.
- S-F19: the `delta` time base drives the panes only; say so in the button's `title` and in `#plotXLabel`.
- D-F4: `settings.js:68` prints "saved; reconnecting stream" into the slot styled `--crit`, so a success looks like a refusal.
- D-F6: the Export dialog's heading is the literal word "Export" for all five sources; use the `ctx.kind` it already receives.
- D-F7: rename the `whole session` button to `reset range`, since it resets the remembered range rather than exporting anything.
- D-F10: the connect/disconnect dot is an 8 px target; grow the hit area with the negative-margin trick `.resizer::after` already uses.
- D-F11: no dialog autofocuses a field or submits on Enter, so `showModal()` lands the user on the close `x`.
- D-F13: the Sessions empty row points at a "record button" that does not exist; the control reads `session`.
- D-F14: the Attach dialog's hints lead with the mechanism (`populated from GET /devices`); rewrite them around the user's goal.
- D-F15: the Attach dialog demands an alias the CLI defaults; prefill from the device basename in `syncDevCustom`.
- D-F16: the restart-required badge gives no next step; extend the title to name Ctrl-C and restart.
- D-F18: two validation messages name a field by a different word than its own label.
- D-F19: the update-check line reports the daemon's internal ambiguity and an env-var name the user did not set.
- D-F20: with the daemon down, Settings opens blank with all five Save buttons live; disable them and say the token field still works.
- D-F21: fold the 200-character PlotJuggler hint into a native `<details>` so the section collapses to its two controls.
- D light theme: `dialog::backdrop` is tuned for dark and turns the light page near black on every dialog open.
- D light theme: `.chip[data-tip]::after` hardcodes its shadow instead of going through `--shadow`.
- D a11y: `.reopen` is not keyboard reachable, so a collapsed sidebar cannot be reopened without a mouse.
- D persistence: nothing says theme, colours, pane layout and export range are per browser; one line under the Settings meta row.
- L-1: plot window buttons overflow at the default sidebar width, clipping `5m` to `5n`.
- L-4: the uPlot legend costs 50 px per chart to show dashes; collapse to one row or hide values until hover.
- L-6: plot titles are protocol names (`stream 0`, `ad-hoc (!p)`); use the firmware-declared name, else an editable per-browser title.
- L-9: the daemon chip prints the word `sim` three times (alias, device, rate label).
- L-10: with one port attached every terminal line still carries a port tag column; hide it until a second port appears.
- L-12: the prompt glyph flips between `>` and `$` with no explanation, next to a toggle that already says cmd/raw.
- L-13: the command bar reflows when the mode changes, because the timeout box hides in raw mode.
- L-15: the CAN and plot empty states name protocol strings without saying what the board must print or linking the firmware doc.
- L-16: the Digital / Enum header shows a live `pause` button before any lane exists; hide the header until the first lane arrives.
- L-18: in the light theme the blue port tag makes the six-colour terminal busy; drop the tag to `--text-dim`.
- L-19: put uptime and db size in the daemon chip's tooltip and leave the address, which is the only thing anyone copies.

## Bigger changes (remaining M and L)

- T-F11 (M): the three segmented controls declare `role="radiogroup"` but give every button its own tab stop and no arrow keys; one roving-tabindex helper in `chrome.js`.
- T-F15 variant (M): translate a typed `^C` escape in the command input when eol is `none`, only if the owner wants the break-signal workflow.
- S-F7 (M): the digital panel has no time axis, so a digital-only stream cannot be measured at all; add a ruler row and gridlines from the existing `laneWindow` projection.
- S-F9 (M): every chart carries two legends around a 150 px canvas; turn uPlot's own legend off and paint the live value into `.plot-chans`.
- S-F17 (M): a 256-row CAN table has no way to find an id; add a substring filter to the sub-head, wired through `canRowsVersion`.
- D-F8 (M): the Storage section is four stacked paragraphs detached from their fields; one line per field, with `#cfgDbNow` moved beside the cap.
- D-F17 (S to M): a saved port's line ending can only be fixed by editing `config.toml`; add an `Eol` column defaulting to `(keep)`.
- D-F22 (M): naming a session uses `window.prompt`, the one place the dialog system stops feeling like one product, and it drops the note the CLI accepts.
- D-F23 (M): no dialog has `aria-labelledby` and no `.hint` is referenced by `aria-describedby`; about 20 attributes.
- D-F24 (M): below 860 px the header wraps to four or five rows and the action buttons land furthest from where the eye starts.
- D a11y (M): `#resizer` has no keyboard equivalent for drag-to-resize.
- L-5 (M): mixed-unit series share one y axis, so a mA ramp flattens degC and V on the same chart; per-unit axes or a per-series normalise toggle.
- Queued elsewhere: per-chart height with a drag handle (2026-09-12 P11) is made worse by S-F2 and S-F9, both of which are cheaper.

## SPEC vs code contradictions

- SPEC 9.1 line 21 says a port chip shows "alias, device, baud, connected state"; line 28 and the code put baud and the requested device in the hover. Reduce line 21 to a pointer.
- SPEC 9.1 says the CAN highlight marks "the bytes that moved since the previous frame"; the code diffs against the last 1 Hz render (S-F4).
- SPEC 9.2 says channel names are unique only within a port and that the port field makes the collision visible; charts key on sid and lanes on name, and no port is shown (S-F6).
- SPEC 9.1 says "Reset clears the table"; everything with the same semantics is called `clear` (S-F18).
- SPEC 9.1's Settings ports row list omits `identify`, which the code renders and SPEC 3.3.1 lists in the PUT body.
- SPEC 9.1 says "Errors from the API shown inline"; detach, disconnect, reconnect and session errors are a 2.5 s chip flash with the reason in a `title` (D-F5).
- The daemon chip tooltip tells the user to set `server.token` in `config.toml`; `config.py:341` ignores that key (D-F1).
- SPEC 9.1 says "a fresh install is fully configurable from the browser"; a saved port's `eol` is not editable in Settings (D-F17).
- SPEC 9.1 calls the session control "a record button"; the button, the CLI and the dialog all say "session" (D-F13).

## Keep

- The freeze discipline: every surface snapshots what it froze and carries an `id_to` watermark into every export mode, so a rotating ring cannot blank a paused view.
- The CAN age clock is the daemon's, anchored on the newest row of any channel, so a quiet bus on a chatty link still ages correctly across a LAN.
- Failure states written in plain English: `DISCONNECT_WHY`, the regex-budget explanation, the high-rate notice that says CAN and plots are still live.
- The pause vocabulary is one coherent model across the pane pill, the jump button, the shared `pause all` label and clear-all.
- Controls that refuse to lie: the export buttons disable themselves and say why, and `syncWindowButtons` repaints every group after a shift-click.
- The delete-session confirmation names the run and the line count and states it cannot be undone; it is the template for every other destructive confirm.
- CAN bus grouping: a divider naming the port in the port's own colour, per-bus tints, bus 1 left untinted, the collapsed set persisted by label.
- The enum lanes drawn as an FPGA bus envelope with X-crossings and a clipped centred label, and the stepped hold-last analog paths.
- The visual language: one hue per channel reused by toggle, tag and text, tabular-nums in fixed boxes, uppercase micro-headers, `prefers-reduced-motion` honoured.
- The light theme holds up on contrast and channel colour, and the accent primary button stays legible.
- Section-level "applies live" and "applies on daemon restart" hints, and the remembered-session warning before it falls back to the newest run.
- Channel filter chips, the marker divider line, the byte-change highlight idea, warning pills instead of modals, and the live pill with jump-to-latest.

## Dropped after verification

- L-20 ("a top-level `db_path` key started with no warning"): `config.py` `_check_unknown` warns on any unknown top-level key at load, so the gap does not exist at HEAD 2149806.
- T-F3 is kept but narrowed: `#cmdPort` does carry `title="Target port (auto = the sole connected port)"`, so the rule is documented; what is missing is the resolved alias itself.
