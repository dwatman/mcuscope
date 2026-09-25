# Browser checks: g1-owed

Headless Chromium (Playwright 1.62.0) against `mcuscoped` 0.5.0 at 91d3649, throwaway config and `MCUSCOPE_*_DIR` per item, port 8811. Scripts: `~/tt-data/mcuscope-tools/browser/`; outputs and screenshots: `~/tt-data/mcuscope-2026-09-25/browser/<group>/<item>/`. Run 2026-09-25, final pass `run_all.py`.

- passed: chip: 60-char session name is one row ending in an ellipsis (Chromium) (`g1-owed/chip`)
  - button h 29.0->29.0, header h 46.0->46.0, scrollW 573 > clientW 170, text-overflow ellipsis; chip-header.png
- passed: chip: U+202E shows as <U+202E> in the clipped chip and its title (Chromium) (`g1-owed/chip`)
  - text '■ \u2068NNNNNNNNNNNNNNNNNNNN<U+202E>gpj.exexx'...
- passed: decim: a 1 kHz stream 15 s after a 40 s silence, 30 s window (left edge in the silence): no coarse columns (Chromium) (`g1-owed/decim`)
  - [{'key': 'burst|adhoc', 'total': 379, 'inWindow': 378, 'raw': 16335, 'maxGapPx': 0.77, 'widthPx': 271, 'first': 1790299135.3668044, 'lo': 1790299161.5846813}] (first sample 26.2 s before the window); decim-mid-*.png
- passed: decim: an 800 Hz sine plus 1 Hz square at 30 s: decimated, no gap over 2 px (Chromium) (`g1-owed/decim`)
  - [{'key': 'fast|adhoc', 'total': 758, 'inWindow': 757, 'raw': 59646, 'maxGapPx': 0.83, 'widthPx': 292, 'first': 1790299179.793237, 'lo': 1790299179.7995436}]; decim-full-*.png
- passed: exports: four session exports in flight, a fifth from Settings queues and saves SQLite (Chromium) (`g1-owed/exports`)
  - no-wait probe while full: 503 b'{"error":"too many session exports in progress; try again shortly"}'; link None; download big-run.db after 13.5 s, header b'SQLite format 3\x00', 454385 lines; the four: {0: (200, 74645504, 4.6), 1: (200, 74645504, 4.7), 2: (200, 74645504, 9.0), 3: (200, 74645504, 9.0)}
- passed: labels: chart x-axis end labels are whole at DPR 1, 1.25, 1.5, host and tick (Chromium emulation) (`g1-owed/labels`)
  - 0 labels left out while sampling; ticks placed 1, 5 and 40 px inside the plot area (key 'placed'); dpr 1 host 5s: {'samples': 32, 'cut': 0, 'dropped': 0, 'min_drawn': 2, 'worst_left': 2.6, 'worst_right': -10.899999999999977}; dpr 1 host 30s: {'samples': 32, 'cut': 0, 'dropped': 0, 'min_drawn': 3, 'worst_left': 13.2, 'worst_right': -10.600000000000023, 'placed': {'L1': {'label': '10:16:50', 'edgeInk': 0, 'inkBetween': 74, 'tickPx': 1, 'widthPx': 292}, 'L5': {'label': '10:17:00', 'edgeInk': 0, 'inkBetween': 74, 'tickPx': 5, 'widthPx': 292}, 'L40': {'label': '10:17:10', 'edgeInk': 0, 'inkBetween': 74, 'tickPx': 40, 'widthPx': 292}, 'R1': {'label': '10:17:20', 'edgeInk': 0, 'inkBetween': 79, 'ti ...
- passed: narrow: under 860 px with a saved hidden sidebar, charts draw and advance (Chromium) (`g1-owed/narrow`)
  - layout {"sideW":null,"expanded":false,"hidden":true,"canCap":null}; sidebar 800x330 at y 579; charts [{'key': 'sim|s0', 'up': True, 'n': 183, 'last': 1790298998.8567097}]; lanes 4; buttons display ['none', 'none']
- passed: narrow: under 860 px with a saved hidden sidebar, the CAN table draws and its ages move (Chromium) (`g1-owed/narrow`)
  - ages ['▾ sim CAN1', '70ms', '173ms', '1.2s', '▾ sim CAN2', '173ms'] -> ['▾ sim CAN1', '67ms', '172ms', '1.2s', '▾ sim CAN2', '172ms']
- passed: narrow: widened past 860 px, the sidebar is hidden again and the reopen tab shows (Chromium) (`g1-owed/narrow`)
  - reopen tab box {'x': 1534.640625, 'y': 483.5, 'w': 65.359375, 'h': 27, 'sw': 64, 'cw': 64}, workspace collapsed True; narrow-widened-1600.png
- passed: reload: a tab left open across the upgrade shows the badge (control) (`g1-owed/reload`)
  - before {'build': 'old', 'badge': False, 'brand': '0.5.0', 'stamp': '0.5.0', 'nav': 'navigate'}; after {'build': 'old', 'badge': True, 'brand': '0.6.0', 'stamp': '0.5.0', 'nav': 'navigate'}
- passed: reload: a tab brought back by Back after the upgrade: old build shows the badge, a fresh load does not (Chromium) (`g1-owed/reload`)
  - after Back {'build': 'old', 'badge': True, 'brand': '0.6.0', 'stamp': '0.5.0', 'nav': 'back_forward'}
- passed: reload: a tab brought back by session restore after the upgrade: same rule (Chromium) (`g1-owed/reload`)
  - before quit {'build': 'old', 'badge': False, 'brand': '0.5.0', 'stamp': '0.5.0', 'nav': 'navigate'}; restored pages ['about:blank', 'http://127.0.0.1:8811/ui/']; restored UI {'build': 'old', 'badge': True, 'brand': '0.6.0', 'stamp': '0.5.0', 'nav': 'back_forward'}
- passed: sec-fetch: the UI's own requests carry real Sec-Fetch headers and pass (Chromium) (`g1-owed/secfetch`)
  - 35 responses, (site, mode) pairs [('none', 'navigate'), ('same-origin', 'cors'), ('same-origin', 'no-cors')], refused [], stream up True
- passed: sec-fetch: a page on 127.0.0.1:8812 (same-site) is refused for subresources, allowed to navigate to the UI (Chromium) (`g1-owed/secfetch`)
  - (site, mode, dest, status): img+fetch [('same-site', 'no-cors', 'image', 403), ('same-site', 'no-cors', 'empty', 403)]; UI link [('same-site', 'navigate', 'document', 200)]; /status link [('same-site', 'navigate', 'document', 403)]
- passed: sec-fetch: a page on localhost:8812 (cross-site) is refused for subresources, allowed to navigate to the UI (Chromium) (`g1-owed/secfetch`)
  - (site, mode, dest, status): img+fetch [('cross-site', 'no-cors', 'image', 403), ('cross-site', 'no-cors', 'empty', 403)]; UI link [('cross-site', 'navigate', 'document', 200)]; /status link [('cross-site', 'navigate', 'document', 403)]
- passed: sec-fetch: a typed URL (Sec-Fetch-Site none) is served (Chromium) (`g1-owed/secfetch`)
  - site none, status 200
- FAILED: solo: a channel at 0 and 1.7e308 keeps its y labels whole (Chromium) (`g1-owed/solo`)
  - labels ['0', '8e+307', '1.6e+308'], widths [6.9, 41.1, 51.2] px, left edges [24.1, -10.1, -20.2] px (axis 46 px, tick 10, gap 5), label ink in canvas column 0-1: 16 px; chip 0; solo-1.7e308.png
- FAILED: solo: a channel at 0 and 12345 keeps its y labels whole (Chromium) (`g1-owed/solo_sweep`)
  - labels ['0', '10000'], widths [6.9, 34.3] px, left edges [24.1, -3.3] px (axis 46 px, tick 10, gap 5), label ink in canvas column 0-1: 9 px; chip 0; solo-12345.png
- FAILED: solo: a channel at 0 and 1234567 keeps its y labels whole (Chromium) (`g1-owed/solo_sweep`)
  - labels ['0', '1000000'], widths [6.9, 48.0] px, left edges [24.1, -17] px (axis 46 px, tick 10, gap 5), label ink in canvas column 0-1: 8 px; chip 0; solo-1234567.png
- FAILED: solo: a channel at 0 and -1234567 keeps its y labels whole (Chromium) (`g1-owed/solo_sweep`)
  - labels ['-1000000', '0'], widths [51.9, 6.9] px, left edges [-20.9, 24.1] px (axis 46 px, tick 10, gap 5), label ink in canvas column 0-1: 8 px; chip -1234567; solo--1234567.png
- passed: solo: a channel at 0 and 0.001234 keeps its y labels whole (Chromium) (`g1-owed/solo_sweep`)
  - labels ['0.000', '0.001'], widths [30.7, 30.7] px, left edges [0.3, 0.3] px (axis 46 px, tick 10, gap 5), label ink in canvas column 0-1: 12 px; chip 0.000; solo-0.001234.png
- FAILED: solo: a channel at 0 and 1.7e308 keeps its y labels whole (Chromium) (`g1-owed/solo_sweep`)
  - labels ['0', '8e+307', '1.6e+308'], widths [6.9, 41.1, 51.2] px, left edges [24.1, -10.1, -20.2] px (axis 46 px, tick 10, gap 5), label ink in canvas column 0-1: 16 px; chip 0; solo-1.7e308.png
- passed: u2: a filtered paused pane after a reconnect, 10 markers in the hole, scrolled to the top: every marker in order, no divider between loaded rows (Chromium) (`g1-owed/u2`)
  - pill after click 'paused'; buffer dividers ['gap: 5061 lines not loaded']; pane before paging: 101 rows, head ['gap: 5061 lines not loaded', 'during-00', 'during-01']; after: 160 rows, head ['before-00', 'before-01', 'before-02']; my markers 160/160 in order True; dividers below the first loaded row []; dividers after each top hit [[], [], [], [], [], [], [], [], [], []]; footer 'paused\n160 lines\nno older lines to load'; u2-pane-top.png
- passed: u2: a filtered paused pane after a reconnect, 250 markers in the hole, scrolled to the top: every marker in order, no divider between loaded rows (Chromium) (`g1-owed/u2_partial`)
  - pill after click 'paused'; buffer dividers ['gap: 4901 lines not loaded']; pane before paging: 101 rows, head ['gap: 4901 lines not loaded', 'during-00', 'during-01']; after: 400 rows, head ['before-00', 'before-01', 'before-02']; my markers 400/400 in order True; dividers below the first loaded row []; dividers after each top hit [['gap: 50 lines not loaded'], [], [], [], [], [], [], [], [], []]; footer 'paused\n400 lines\nno older lines to load'; u2-pane-top.png

## Defect: a soloed chart's y labels are cut past about 31 px

- Failed on three runs (09:07, the 09:08 sweep, the final pass); the label's first characters are off the canvas (`solo-*.png`).
- Smallest repro: a board sending `!p 1 big=0` and `!p 2 big=12345` alternately; the ad-hoc chart is soloed by having one channel, and its y label reads `0000` with the `1` cut.
- Where it breaks: `8e+307`, `1.6e+308`, `10000`, `1000000`, `-1000000` all start left of the canvas; `0.000`/`0.001` just fit (left edge 0.3 px).
- Suspected: `host/mcuscope/webui/plots.js:1034`, the soloed axis has a fixed `size: 46` (46 - tick 10 - gap 5 = 31 px of text), while its labels (`plots.js:1038-1041`, `fmtPlotVal` at `plots.js:932`) run to 9 characters (about 58 px).
- The 1.7e308 fix itself holds: the trace draws and the chip reads the value; only the axis width is short.
