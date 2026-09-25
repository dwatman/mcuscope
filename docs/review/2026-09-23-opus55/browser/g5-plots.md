# Browser checks: g5-plots

Headless Chromium (Playwright 1.62.0) against `mcuscoped` 0.5.0 at 91d3649, throwaway config and `MCUSCOPE_*_DIR` per item, port 8811. Scripts: `~/tt-data/mcuscope-tools/browser/`; outputs and screenshots: `~/tt-data/mcuscope-2026-09-25/browser/<group>/<item>/`. Run 2026-09-25, final pass `run_all.py`.

- passed: plots: with bench attached, charts and group headers name their port (`g5-plots/bench`)
  - chart heads [['stream 0', 'bench'], ['ad-hoc (!p)', 'bench'], ['stream 0', 'sim']]; lane group/port tags ['gpio (packed) on bench', 'bench', 'bench', 'bench', 'bench', 'gpio (packed) on sim', 'sim', 'sim', 'sim', 'sim']
- passed: plots: the `N below` cue scrolls smoothly to the first hidden widget (`g5-plots/bench`)
  - cue '↓ 2 below'; first hidden 'stream 0'; scrollTop samples every 20 ms [0, 2, 11, 31, 147, 239, 351, 384, 410, 447, 460, 471]...; after: widget tops [['stream 0', -496], ['ad-hoc (!p)', -253], ['stream 0', 0], ['Digital / Enum', 241]]
- passed: plots: no uPlot legend; each chip shows name, value and unit, and follows the cursor (`g5-plots/chips`)
  - visible legends 0; first chip parts [['swatch', ''], ['', 'tri'], ['val', '-15.240'], ['unit', 'V']]; chips at 3 cursor x [['3.580', '4.100', '0.512'], ['-16.460', '9.100', '0.930'], ['-16.760', '10.800', '0.988']]
- passed: plots: hovering one chart updates the chips on every other chart (`g5-plots/chips`)
  - second chart (bench|s0) chips per hover [['8.160', '3.700', '-0.448'], ['-8.160', '8.700', '0.894'], ['3.680', '10.400', '-0.598']]
- passed: plots: cursor time tag on a chart, flipping side near the right edge (`g5-plots/chips`)
  - tags [('10:27:27.821', False), ('10:27:30.328', True), ('10:27:31.173', True)] at 30%, 80%, 97% of the width; chips-cursor.png
- passed: digital: clear all while live and while paused: each lane starts at its first new sample, not the left edge (`g5-plots/clear`)
  - first inked column as a fraction of the lane: before [0.722, 0.722, 0.722, 0.722]; 2.5 s after clear (live) [0.922, 0.926, 0.926, 0.926]; paused+cleared [None, None, None, None]; 2.5 s after resume [0.883, 0.887, 0.887, 0.887]
- passed: digital: after clear all, a drag zooms and a window button leaves the zoom (`g5-plots/clear`)
  - zoom after drag True chips [True, True] ranges [(1790299759.2899108, 1790299763.8104587)]; after 5s: zoom False, spans [5.0]
- passed: digital: at 360 px the Digital / Enum head puts its controls on a second line rather than clipping them (`g5-plots/dhead`)
  - {'w': 360, 'h': 59, 'clipped': [], 'rows': 5, 'bx': [['iconbtn plot-collapse', 1246, 597, 1268], ['ptitle', 1274, 602, 1367], ['count', 1373, 602, 1419], ['plot-win', 1394, 627, 1479], ['BUTTON', 1395, 628, 1421], ['on', 1421, 628, 1452], ['BUTTON', 1452, 628, 1478], ['iconbtn', 1485, 624, 1535], ['iconbtn exportbtn', 1541, 624, 1594]]}; dhead-360.png
- passed: plots: empty daemon: the plots empty state is one line with its tooltip; no Digital / Enum head (`g5-plots/empty`)
  - empty state ['No plot data yet: the board prints !p or !pd / !ps lines', 'One line per sample: !p <tick> <name>=<value> ..., for examp', 33, 'help']; digital head hidden True
- passed: export: chart with `changes only` and a deadband downloads, lanes with `changes only` download; a bad deadband (chart: `tri:0.5`; lanes: any, they render labels) shows inline (`g5-plots/exportchanges`)
  - {'chart': {'mode': 'expModeSession', 'url': 'session=1&names=tri%2Cramp%2Cftest&port=sim&format=wide&decode=1&changes=1&deadband=tri%3D0.5', 'lines': 98, 'bad': 'plot export failed: deadband needs name=value: tri:0.5', 'open': True}, 'lanes': {'mode': 'expModeSession', 'url': 'session=1&names=led%2Cirq%2Cpwm_en%2Cstate&port=sim&format=long&decode=1&changes=1', 'lines': 20, 'bad': 'plot export failed: deadband is numeric, but state renders as a label (an enum or a decoded bit lane)', 'open': True}}
- passed: plots: every channel hidden: the chart's export is visibly disabled and says why; same for the digital head with no lane shown (`g5-plots/exportoff`)
  - chart export [disabled, title, opacity] [True, 'Nothing is shown on this chart: tick a channel to export it', '0.5']; digital export [True, 'No lanes are shown: enable one to export it']
- FAILED: digital: with bench attached the lane gutter port tag reads whole (eye check on gutter-bench.png) (`g5-plots/gutter`)
  - [port tag, lane, width px, unclipped, colour] [['sim', 'led', 17, True, 'rgb(111, 178, 255)'], ['sim', 'irq', 17, True, 'rgb(111, 178, 255)'], ['sim', 'pwm_en', 17, True, 'rgb(111, 178, 255)'], ['sim', 'state', 17, True, 'rgb(111, 178, 255)'], ['bench', 'led', 29, True, 'rgb(180, 140, 232)'], ['bench', 'irq', 29, True, 'rgb(180, 140, 232)'], ['bench', 'pwm_en', 27, False, 'rgb(180, 140, 232)'], ['bench', 'state', 29, True, 'rgb(180, 140, 232)']]
- passed: zoom: with a zoom standing the linked cursor lands on the same x on a chart and on a lane (each at its own time, same zoom range) (`g5-plots/linked`)
  - zoom {'mode': 'host', 'min': 1790299665.6987817, 'max': 1790299668.1987817}; per hover [{'tag': '10:27:46.100', 'chartTag': '10:27:46.298', 'dShown': True, 'lanePxErr': 0.57, 'chartPxErr': -0.0, 'apart_ms': -198}, {'tag': '10:27:46.898', 'chartTag': '10:27:46.949', 'dShown': True, 'lanePxErr': 0.55, 'chartPxErr': 0, 'apart_ms': -52}, {'tag': '10:27:47.105', 'chartTag': '10:27:47.549', 'dShown': True, 'lanePxErr': 0.55, 'chartPxErr': -0.0, 'apart_ms': -445}]
- passed: plots: rename a chart title: Enter commits, Escape cancels, blur commits; focus returns to the title on Enter and Escape (`g5-plots/rename`)
  - was 'stream 0'; Enter ('Supply', 'ptitle'); Escape ('Supply', 'ptitle'); blur to the marker box ('Rail', 'markerInput'); after reload 'Rail'
- passed: digital: the ruler under the lanes lines up with the chart's time axis (hovering a lane at a ruler tick puts the chart cursor at that time) (`g5-plots/ruler`)
  - [ruler label, chart cursor time, fraction of a second off]: [('10:28:40', '10:28:40', 0.976)]
- passed: digital: ARMED fits its segment at 30 s (`g5-plots/ruler`)
  - ARMED [text width, segment inner width] px: [(30.1, 36.0), (30.1, 36.0)]; ruler-lanes.png
- passed: plots: bench and sim both declare stream 0's names; after a reload both boards' charts come back with history (`g5-plots/shared`)
  - seeded chart sizes {'sim|s0': 230, 'bench|s0': 226}
- passed: digital: two boards' enum lanes named `state` show their own labels after a reload (`g5-plots/shared`)
  - state lanes [{'key': 'sim|state', 'port': 'sim', 'name': 'state', 'n': 0, 'labels': {'0': [0, 'IDLE'], '1': [1, 'ARMED'], '2': [2, 'RUN']}}, {'key': 'bench|state', 'port': 'bench', 'name': 'state', 'n': 0, 'labels': {'0': [0, 'IDLE'], '1': [1, 'ARMED'], '2': [2, 'RUN']}}, {'key': 'enum2|state', 'port': 'enum2', 'name': 'state', 'n': 0, 'labels': {'0': [0, 'OFF'], '1': [1, 'ON'], '2': [2, 'FAULT']}}]; gutters ['sim:led=1', 'sim:irq=0', 'sim:pwm_en=0', 'sim:state=ARMED', 'bench:led=0', 'bench:irq=1', 'bench:pwm_en=0', 'bench:state=RUN', 'enum2:state=ON']; shared-after-reload.png
- passed: plots: shift-click a window button: every chart head and the digital head show it on (`g5-plots/shift`)
  - lit per selector [['5'], ['5'], ['5'], ['5']]
- passed: plots: alt-click a channel name: one trace with a y axis labelled with the unit; again: every trace, no y axis (`g5-plots/solo`)
  - solo tri: shown ['tri'], y axis True, label 'V' (unit 'V'); again: shown ['tri', 'ramp', 'ftest'], y axis False
- passed: zoom: a drag zooms every chart to the dragged range and lights the zoom chip in every window selector (`g5-plots/zoom`)
  - zoom {'mode': 'host', 'min': 1790299637.6693985, 'max': 1790299646.7104945}; chart x ranges {(1790299637.669, 1790299646.71)}; zoom chips shown [True, True, True, True]
- passed: zoom: every panel and the terminal pause together; no stale selection box on any chart (`g5-plots/zoom`)
  - charts paused [True, True, True]; lanes paused True; panes live [False]; selection widths [0, 0, 0]
- passed: zoom: the chip's x returns to the window and stays paused; a window button while zoomed leaves the zoom and stays paused (`g5-plots/zoom`)
  - after the chip: zoom False, charts paused [True, True, True]; after 5s: zoom False, paused [True, True, True]
- passed: zoom: one double-click on a chart returns every panel to the window and resumes (`g5-plots/zoom`)
  - {'zoom': False, 'panesLive': [True], 'lanesPaused': False, 'pauseAll': 'pause all', 'zoomChips': [False, False, False, False]}; charts paused [False, False, False]
- passed: zoom: a double-click on the digital lanes does the same as on a chart (`g5-plots/zoom`)
  - {'zoom': False, 'panesLive': [True], 'lanesPaused': False, 'pauseAll': 'pause all', 'zoomChips': [False, False, False, False]}; charts paused [False, False, False]

## Defect: the lane gutter's port tag is cut on a lane with a long name

- Failed twice (10:02 and the final pass): with `sim` and `bench` attached, the `bench` tag reads `ben…` on the `pwm_en` lane only (`gutter-bench.png`); `led`, `irq`, `state` show it whole.
- Smallest repro: `mcuscoped --sim`, attach `mcu-sim --plot` as `bench`, look at the Digital / Enum gutter at the default 360 px sidebar.
- Suspected: `host/mcuscope/webui/style.css:317`, `.dlane .gut .pt { flex: 0 1 auto; max-width: 6ch }` lets the tag shrink before the name (`.nm`, which has its own ellipsis) does.
