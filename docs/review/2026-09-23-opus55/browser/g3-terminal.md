# Browser checks: g3-terminal

Headless Chromium (Playwright 1.62.0) against `mcuscoped` 0.5.0 at 91d3649, throwaway config and `MCUSCOPE_*_DIR` per item, port 8811. Scripts: `~/tt-data/mcuscope-tools/browser/`; outputs and screenshots: `~/tt-data/mcuscope-2026-09-25/browser/<group>/<item>/`. Run 2026-09-25, final pass `run_all.py`.

- passed: terminal: each pane empty state (no ports, waiting, cleared, no channels, none on channels, none matching) is one line; a tooltip with cursor: help where the state has detail (`g3-terminal/empty`)
  - tooltip present {'no ports': True, 'waiting for the first line': False, 'cleared': True, 'no channels ticked': False, 'none on the ticked channels': False, 'none matching the regex': False}; states {'no ports': {'text': 'No ports attached: + Attach one', 'title': 'Attach a serial port with + Attach above, or start the daemon as mcuscoped --sim for the zero-hardware demo.', 'cursor': 'help', 'h': 30, 'oneLine': True}, 'waiting for the first line': {'text': 'Waiting for the first line', 'title': '', 'cursor': 'auto', 'h': 30, 'oneLine': True}, 'cleared': {'text': 'Cleared (view only)', 'title': 'Clear empties this view; the capture keeps every line.', 'cursor': 'help', 'h': 30, 'oneLine': True ...
- passed: terminal (off-list probe): a paused pane jumped straight to the top pages on the first top hit (`g3-terminal/firstscroll`)
  - history requests after one jump to the top: 1; after a second top hit: 1; selfScroll now False
- FAILED: terminal (off-list probe): after a channel filter change, a paused pane jumped straight to the top pages on the first top hit (`g3-terminal/firstscroll`)
  - selfScroll before the jump True; history requests after one jump: 0; after a second top hit: 1
- FAILED: terminal (off-list probe): after a regex change, a paused pane jumped straight to the top pages on the first top hit (`g3-terminal/firstscroll`)
  - selfScroll before the jump True; history requests after one jump: 0; after a second top hit: 1
- passed: terminal: paused pane footer reads `scroll to the top for older lines`, then `loading older lines...`, then `no older lines to load` (`g3-terminal/footer`)
  - live 'dbl-click a line to copy'; paused 'scroll to the top for older lines'; seen while paging ['loading older lines...', 'no older lines to load', 'scroll to the top for older lines']
- passed: terminal: the port tag shows only with two ports attached (`g3-terminal/porttag`)
  - one port: 0 tags; two ports: tags ['sim', 'bench']; bench detached again: 0 tags
- passed: terminal: regex box focused in 1 and in 3 panes widens, and no toolbar row changes line (Chromium) (`g3-terminal/regex`)
  - per pane [toolbar height, each control's top, regex width] before -> focused: {1: ([{'h': 42, 'rows': '95,98,96,93,92', 'match': 90}], [{'h': 42, 'rows': '95,98,96,93,92', 'match': 260}]), 3: ([{'h': 72, 'rows': '93,97,126,123,92', 'match': 90}, {'h': 72, 'rows': '93,97,126,123,92', 'match': 90}, {'h': 72, 'rows': '93,97,126,123,92', 'match': 90}], [{'h': 72, 'rows': '93,97,126,123,92', 'match': 255}, {'h': 72, 'rows': '93,97,126,123,92', 'match': 90}, {'h': 72, 'rows': '93,97,126,123,92', 'match': 90}])}
- passed: terminal: the regex x clicked while widened clears the box and keeps focus (Chromium) (`g3-terminal/regex`)
  - widened 255 px; after x: value '', focused True, width 255 px
- passed: terminal: tick base: sim debug lines read ~n rising in step with the !ps ticks (within the 200 ms anchor slack) (`g3-terminal/tick`)
  - 6 debug lines checked between their neighbours' ticks, out of step []; estimates [253, 253, 2251, 4249, 6244, 6244]
- passed: terminal: tick base: hovering a debug line moves the chart cursor to its estimate (`g3-terminal/tick`)
  - everything paused; line ts '~4249'; chart cursor at x 10005 (anchor 5756); cursor minus estimate 0 ms (samples are 50 ms apart); tick-hover.png
- passed: terminal: tick base: paged lines (scroll to the top of a paused pane) also read ~n (`g3-terminal/tick`)
  - 400 rows paged in (ids below 443); 417 of them read on screen; debug among them [[154, '~-3751', 'debug', 'vbat=24.96V iout=1.27A t'], [302, '~-1748', 'debug', 'vbat=25.01V iout=1.33A t']]

## Defect (off-list): after a filter change, a paused pane's first top hit pages nothing

- Failed twice (09:46 and the final pass), for a channel change and for a regex change; a pane paused without a filter change pages on the first hit (control, passed).
- Smallest repro: untick `sys` on a pane, pause it, drag its scrollbar to the top in one move (or `scrollTop = 0`): no `/lines?...id_to` request; scroll down a little and back up: the page loads.
- Suspected: `host/mcuscope/webui/terminal.js:389`, `rebuild` sets `pane.selfScroll = true` whether or not the rebuild moves the scroll offset; the flag then swallows the user's next real scroll at `terminal.js:737`, which is the top hit.
- A mouse wheel masks it (the wheel sends several scroll events); a scrollbar drag or Home key does not.
