# Browser checks

Checks the registry legs owe that the DOM stub cannot make: real `<select>`, focus, `window.prompt`, canvas and layout.
Run after webui-panes and webui-settings land, against `mcuscoped --sim` on a throwaway config and port.
Script each in headless Chromium (Playwright 1.62.0) where it says so; delete the profile dir and stop the browser after.
Only the items marked `eyes` need the owner.

## Scriptable (headless)

- [ ] R12-1: route `GET /devices` to answer 500, open Settings: the ports section shows `could not list devices`.
- [ ] R19-1: `mcu mark '!m @4000000000 hello'`: tick mode shows no tick for that row, the divider reads `marker: !m @4000000000 hello`, other rows' ticks do not shift.
- [ ] R22-5: type `0x10` and `1e3` into the baud (text) and command timeout fields, and into a `type=number` settings field: each is refused. Record what a real number input hands the code for `0x10`.
- [ ] R23-1: pane live at the sim's rate, pause, then edit and clear the regex: the line count shown at the pause does not grow.
- [ ] R25-1: clear all, then `+ pane`: the new pane is empty, then fills with new lines only.
- [ ] R25-2: drag-zoom a chart, click a pane's pill, clear all: the rebuilt chart is live and its window selector shows no zoom chip.
- [ ] R27-8: pick a port in the command bar, detach it (`mcu detach`): the select still shows the pick and the next command is refused by the daemon, not sent to the other board.
- [ ] R27-9: hover a terminal line: the chart and lane cursors move to it. Press Enter in a Settings field: that section saves.
- [ ] R27-23: drag the CAN/plots divider: it resizes, no console error.
- [ ] N-JS-2: in the session dialog, pick a listed device: the alias fills from it.
- [ ] R51-1: pane filter matching nothing in the last 5 pages, scroll to top: the "search older" control appears and works.
- [ ] R57-1: two sim boards with different CAN ids, export CAN history for one: the ids field holds that board's ids and follows the Port select.
- [ ] R61-1: route `/config` to hang, open Settings, paste a token: it is still there after the 2 s timeout, and after a normal answer.
- [ ] Class 80: `page.route` holding `/lines`, clear the pane, release the route: the cleared lines do not come back.
- [ ] Class 72: a background refresh (status poll, settings re-read) while typing in the marker box and in a Settings field: focus and caret stay put.

## Needs eyes

- [ ] eyes: R34-1, replace one saved colour in `localStorage["mcuscope.colors"]` with `"garbage"`, reload: that series draws in its default colour.
- [ ] eyes: R72-1, restart the daemon with a token while typing a marker: after the D-18 fix no prompt steals the keys; the chip says a token is needed and a click prompts.
- [ ] eyes: R77-1, only if the owner picks D-19 A: step the host clock back 1 h while the sim streams; charts break and continue, lanes' live edge keeps moving. Needs a VM or a spare machine.
- [ ] eyes: a browser render of the UI served from an installed wheel (the standing check in `docs/SCREENSHOTS.md`).

## Still owed from the round itself (REVIEW_LOG 2026-09-23 "Owed", not repeated above)

The chip ellipsis and `<U+XXXX>` cases, the reload badge after Back and session restore, `Sec-Fetch-*` headers, chart decimation, 125% scaling (Windows), under-860 px layout, soloed channel labels, U-2, the five-export queue, U+2068/U+2069 on Windows, Safari.
