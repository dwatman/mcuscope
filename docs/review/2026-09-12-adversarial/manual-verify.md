# Manual-verify list, 2026-09-12 round (browser only, against `mcuscoped --sim`)

Each line is one check a human runs; none has been run yet. The 2026-09-07 items in REVIEW_LOG.md "Carried open" still apply.

## Adversarial web UI leg


Each line is runnable against `mcuscoped --sim --plot` at `http://127.0.0.1:8558/ui/`.

1. In a terminal pane untick the `sys` channel, click `export`, press Export, and confirm a file downloads rather than an error toast (this is W1; expect the toast today).
2. Pause a terminal pane immediately after a hard reload, before any line lands, click `export` and press Export; confirm the message shown (W4).
3. Open a chart's `export`, untick `decode values`, tick `changes only`, press Export, and read the toast (W5).
4. With the settings dialog, add a second port that cannot connect, leave one connected, then check the command bar's line-ending select and which mode button is lit (W3).
5. Pick a line ending in the command bar, then look for any way back to "port default" without clearing site data (W8).
6. Pause a chart, export with `Shown window`, then export the CAN table, then reopen the chart's export and check whether `Shown window` is still selected (W10).
7. Delete the session the export dialog last remembered, reopen any export dialog, and check whether anything says the remembered range changed (W11).
8. Hide every channel on a chart and click `export`; confirm the button appears to do nothing (W12).
9. Tab through the export dialog: the three radios are one `name="expRange"` group, `expSession`/`expFrom`/`expTo` toggle `disabled` with the mode, and the dynamic option inputs get ids `expOpt_*` but **no `<label for>`** on the select/text fields (the label is a sibling, not associated) - check a screen reader announces them.
10. Resize the export dialog narrow: `.radio-row` is a flex row with `white-space: nowrap` labels and two `datetime-local` inputs at `flex: 1` - check the clock row does not overflow the dialog.
11. Press Escape on the export dialog and confirm it closes without saving the range (the `cancel` handler preventDefaults then calls `closeExport`).
12. Click Export the instant the dialog opens, before `/sessions` answers, and check which session the download covers (the select is emptied synchronously and filled after the fetch).
13. Confirm the `whole session` button, on a capture whose only session has ended, selects that session rather than the whole capture.
14. Export a plot with `changes only` and a deadband of `vbat=0.5`, then with a name that is not in the selection, and check both outcomes are legible to the user.
15. Bundle a long-running open session from Settings and confirm the browser saves `bundle.zip` with the daemon's own filename from `Content-Disposition`.

## Charts and terminal batch


1. Drag on one chart's x axis: every chart and every digital lane must show the dragged range, and all panels plus the terminal must go paused together.
2. Under `cursor.sync`, the drag must not leave a stale selection rectangle on the sibling charts.
3. uPlot's own double-click reset must not fight the shared hook: one double-click on a chart returns every panel to the window selector's range and resumes.
4. Double-click on the digital lanes must do the same as a double-click on a chart.
5. With a zoom standing, the linked cursor must land on the same x on a chart and on a lane (the pixel agreement the stub cannot lay out).
6. Shift-click a window button: every chart head and the digital head must repaint their own "on" state, not just the one clicked.
7. Alt-click a channel name, then alt-click it again: one trace, then all of them, with the y axis appearing and disappearing at the single-trace boundary.
8. Hide every channel on a chart and hover the export button: it must be visibly disabled and the tooltip must say why (same for the digital head with no lane shown).

## Panels and dialogs batch


1. CAN panel: watch the sim's 10 Hz heartbeat on id 0x100 and confirm the counter byte lights and the tint fades on a quiet id.
2. CAN panel: click an id and confirm the last terminal pane fills with that id's frames, scrolls into view, and that `unfilter` appears and then clears both the pane and itself.
3. CAN panel: press `pause`, confirm the payloads, counts and ages stop, `paused` shows in the head, `pause all` reads `resume all`, and `resume` catches up.
4. CAN panel paused: open `export` and confirm `Shown window` is selectable and the download covers the frozen span.
5. Port chip: with the ST-LINK attached, confirm the chip shows the target name and a lines/s figure that settles, and that the figure does not make the chip jump or steal focus from the reconnect button every 5 s.
6. Port chip: move the probe to the other board, wait one poll, and confirm the target name changes on screen.
7. Attach dialog: attach a CRLF board with `Save to config`, then check Settings > Ports and the config file both read `crlf`.
8. Command bar: pick a line ending, then pick `port default` again and confirm the select reads `port default (<the port's own>)` and sends nothing extra.
9. Export a large capture with no token set and confirm the browser's own download progress appears immediately (no frozen tab while the body buffers) and the daemon's filename is used.
10. Export with a token set and a bad deadband name, and confirm the message appears inside the dialog with the dialog still open.
11. Export the session bundle from Settings with a token and confirm it still saves as `bundle.zip`.
12. A CAN id whose device writes it in lower-case hex (`!can 1 - 1abc ...`): confirm whether the id-click filter matches, since the pattern uses the daemon's upper case.
13. Tab through the attach dialog and confirm the new serial-number and line-ending fields are announced with their labels.

