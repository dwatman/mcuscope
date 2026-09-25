# Browser checks: g4-can

Headless Chromium (Playwright 1.62.0) against `mcuscoped` 0.5.0 at 91d3649, throwaway config and `MCUSCOPE_*_DIR` per item, port 8811. Scripts: `~/tt-data/mcuscope-tools/browser/`; outputs and screenshots: `~/tt-data/mcuscope-2026-09-25/browser/<group>/<item>/`. Run 2026-09-25, final pass `run_all.py`.

- passed: can: divider drag above a short table shrinks and scrolls it, below does nothing, double-click restores 45 percent (`g4-can/divider`)
  - natural {'sec': 219, 'wrapH': 178, 'scrollH': 178, 'canH': '', 'body': 860}; dragged up 100 px {'sec': 122, 'wrapH': 81, 'scrollH': 178, 'canH': '14.2%', 'body': 860}; dragged down 400 px {'sec': 219, 'wrapH': 178, 'scrollH': 178, 'canH': '61.1%', 'body': 860}; double-click {'sec': 219, 'wrapH': 178, 'scrollH': 178, 'canH': '45%', 'body': 860} (CSS default max-height 45%)
- passed: can: empty daemon: the folded head reads `no frames yet` with the grammar tooltip; the CAN view shows the body line instead (`g4-can/empty`)
  - Both view head [True, 'no frames yet'], tooltip starts 'One line per frame: !can <tick> <flags> <id> <data', table body display none; CAN view body [True, 'No CAN frames yet: the board prints !can lines'], head shown False
- passed: can: export: Source snapshot disables the ids field; history re-enables it (`g4-can/exportsrc`)
  - [('snapshot', True), ('history', False)]
- passed: can: id filter widens while typing, narrows the rows, shows a no-match row, ignores 0x and case (`g4-can/filter`)
  - box 42 -> 69 px focused; rows per query {'18a': ['0000018Aext'], '0x18A': ['0000018Aext'], '18A': ['0000018Aext'], '0X18a': ['0000018Aext'], 'zz': ['no id contains ZZ']}; no-match text ['no id contains ZZ']
- passed: can: the head wraps cleanly with `paused` and `unfilter` both shown (sidebar 260 and 360 px) (`g4-can/head`)
  - {260: {'h': 71, 'rows': 5, 'overlap': [], 'outside': [], 'paused': True, 'unfilter': True}, 360: {'h': 71, 'rows': 5, 'overlap': [], 'outside': [], 'paused': True, 'unfilter': True}}; head-*.png
- passed: can: a changing byte lights, and the highlight clears on a bus gone silent (at the next 1 s repaint) (`g4-can/highlight`)
  - lit bytes on 0x100 over 1 s of samples [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]; highlight gone 1.88 s after the board stopped
- passed: can: bench 0x100 heartbeat plain while running; board stopped: amber about 0.5 s, red about 1 s (1 s repaint) (`g4-can/highlight`)
  - running ['age-fresh']; after the stop [s, class, lit bytes, age text] at each change: [(0.0, 'age-fresh', 1, '30ms'), (0.91, 'age-stale', 1, '930ms'), (1.88, 'age-dead', 0, '1.9s'), (2.9, 'age-dead', 0, '2.9s')]; class matches the age text throughout: True
- passed: can: column header and row hovers; the row hover gives the frame count (`g4-can/hovers`)
  - headers [['id', 'CAN id in hex; ext is a 29-bit id, rtr a remote request. Click an id to filter the last pane to it'], ['dlc', 'Payload length in bytes'], ['data', 'Latest payload in hex; highlighted bytes changed in a frame since the last repaint'], ['period', 'Estimated period: an EWMA of the time between frames'], ['age', 'Since the last frame. A periodic id is amber past 5 missed periods and red past 10; an irregular or new id is never coloured']]; rows [('100', '24 frames since clear'), ('0000018Aext', '2 frames since clear'), ('400rtr', '1 frame since clear')]
- passed: can: clicking an id fills the last pane with that id's frames (lower-case hex ids too), `unfilter` shows, then clears the pane and itself (`g4-can/idclick`)
  - {'sim 0x100': {'regex': '^!can1? +\\d+ +(?:-|r+) +(?:0[xX])?0*100 ', 'lines': ['!can 4509 - 100 0000002D', '!can 4602 - 100 0000002E', '!can 4705 - 100 0000002F', '!can 4810 - 100 00000030', '!can 4902 - 100 00000031', '!can 5005 - 100 00000032'], 'unfilter': True}, 'lower-case std 1ab': {'regex': '^!can1? +\\d+ +(?:-|r+) +(?:0[xX])?0*1[Aa][Bb] ', 'lines': ['!can 4500 - 1ab 0902', '!can 5000 - 1ab 0a02', '!can 5500 - 1ab 0b02', '!can 6000 - 1ab 0c02', '!can 6500 - 1ab 0d02', '!can 7000 - 1ab 0e02'], 'unfilter': True}, 'lower-case ext 1abcdef0': {'regex': '^!can1? +\\d+ +[xr]*x[xr]* +(?:0[xX])?0*1[Aa][Bb][Cc][Dd][Ee][Ff]0 ', 'lines': ['!can 6000 x 1abcdef0 0c', '!can 6500 x 1abcdef0 0d', '!ca ...
- passed: can: a one-off `can tx` id stays plain however old it gets (8 s here) (`g4-can/oneoff`)
  - /cmd {'status': 'ok', 'seq': 2, 'data': '', 'latency_ms': 1.9447803497314453, 'line_id': 151}; row 0x124 [{'id': '124', 'cells': ['124', '2', '01 02', '-', '7.4s'], 'title': '1 frame since clear', 'chg': 0, 'age': 'age-fresh'}]
- passed: can: pause freezes payloads, counts and ages; `paused` shows; resume catches up (`pause all` keeps its label while charts and panes run, SPEC 9.1) (`g4-can/pause`)
  - unchanged over 1.5 s True; tag True; pause all 'pause all'; button 'resume'; 0x100 after resume ['53 frames since clear']
- passed: can: paused: export offers Shown window and the file covers the frozen span (`g4-can/pause`)
  - shown enabled True; 13 frames, columns ['id', 'ts', 'tick_ms', 'bus', 'can_id', 'ext', 'rtr', 'dlc', 'data']; ts 1790299636.1402655..1790299637.0428638; paused at 1790299637.123
- passed: can: an extended remote id (EXT and RTR chips) fits without sideways scroll with a space-taking scrollbar (Chromium Linux) (`g4-can/scrollbar`)
  - scrollbar 15 px; sideways False; row [{'id': '1ABCDEF1extrtr', 'cells': ['1ABCDEF1extrtr', '8', 'remote', '-', '1.4s'], 'title': '1 frame since clear', 'chg': 0, 'age': 'age-fresh'}]
- passed: can: data wraps only before byte 5 or 7, no sideways scroll, age visible (sidebar 300-520 px, default 360) (`g4-can/wrap`)
  - per sidebar width {rows: [bytes, line breaks before byte index], sideways, ageVisible}: {260: {'rows': [{'n': 4, 'breaks': []}, {'n': 8, 'breaks': [4]}, {'n': 4, 'breaks': []}], 'sideways': True, 'ageVisible': False, 'scrollbar': 0}, 300: {'rows': [{'n': 4, 'breaks': []}, {'n': 8, 'breaks': [4]}, {'n': 4, 'breaks': []}], 'sideways': False, 'ageVisible': True, 'scrollbar': 0}, 360: {'rows': [{'n': 4, 'breaks': []}, {'n': 8, 'breaks': [6]}, {'n': 4, 'breaks': []}], 'sideways': False, 'ageVisible': True, 'scrollbar': 0}, 420: {'rows': [{'n': 4, 'breaks': []}, {'n': 8, 'breaks': []}, {'n': 4, 'breaks': []}], 'sideways': False, 'ageVisible': True, 'scrollbar': 0}, 520: {'rows': [{'n': 4, 'breaks' ...
- FAILED: can (off-list probe): at the 260 px minimum sidebar width the table fits without sideways scroll (`g4-can/wrap`)
  - {'rows': [{'n': 4, 'breaks': []}, {'n': 8, 'breaks': [4]}, {'n': 4, 'breaks': []}], 'sideways': True, 'ageVisible': False, 'scrollbar': 0}; wrap-260.png

## Checklist lines the code has moved past

- 2026-09-12 panels 3 "`pause all` reads `resume all`" after pausing the CAN table alone: SPEC 9.1 has the label follow every surface, so it stays `pause all` while charts and panes run.
- 2026-09-14 review fix "amber after about 0.5 s and red after about 1 s": the table repaints once a second (`can.js:722`), so the colours land at 0.86 s and 1.89 s; the class matches the age text at every sample.
- Off-list: at the 260 px minimum sidebar the table scrolls sideways and hides the age column (`wrap-260.png`); from 300 px it fits. Owner call O-12 in `owner.md`.
