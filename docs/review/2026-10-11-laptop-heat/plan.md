# Laptop heat while streaming: plan

Goal: find what heats the owner's laptop while the web UI shows streaming data, fix it with measured A/B savings, and decide whether a native client is still worth considering.

## Starting point

- `docs/review/2026-09-23-opus55/webui-cpu.md` measured the web UI's main-thread cost; all 8 findings are fixed (95e2442, 4462986, later rounds).
- That review did not cover, and this plan does:
  - a headed browser on the laptop (headless rasterises in software, has no real compositor or GPU process);
  - per-process attribution (daemon, sim, browser main, renderer, GPU process) and package power;
  - the owner's real browser, display scaling and refresh rate;
  - real hidden or minimised tabs (headless emulated `document.hidden`).
- Reusable tooling: `~/git/mcuscope-tools/2026-10-05/soak/` (`soak.py`, `ab.py`: isolated daemon, CDP metrics, `/proc` sampling) and `~/git/mcuscope-tools/browser/drv.py` (`Stack`).

## Step 0: owner inputs

Ask with AskUserQuestion, one call:

- Laptop OS, browser (Chrome, Firefox, Edge), display scaling and refresh rate.
- What was streaming when it got hot: a real board (which, what rates) or `mcuscoped --sim`.
- Which view was on screen (Terminal, Plots, CAN, Both) and roughly how many panes, charts and lanes.
- Whether this session runs on the laptop. If not, stop: desktop numbers do not answer the question.

## Step 1: attribute the load by process

Run on the laptop, on AC power, with an isolated daemon (`--config` on a throwaway TOML with its own `db_path`, `MCUSCOPE_*_DIR` in scratch).
Drive the stream with `mcu-sim --plot --flood N` at the owner's rates.

Per scenario, 3 windows of 30 s after 60 s settle, median:

- CPU ms/s per process: daemon, sim, browser main, renderer, GPU process.
  - Linux: `/proc/<pid>/stat`; Windows: `psutil` from a scratch venv.
- Package power, the actual heat measure: Linux `/sys/class/powercap/intel-rapl:0/energy_uj` (or `amd_energy`); Windows: skip, CPU totals only.
- Scenarios:
  - desktop idle, nothing running (floor);
  - daemon + sim, no browser;
  - `mcu tail` in a terminal instead of the browser (the "native client" control: the lowest any client can cost);
  - browser tab open, each view: Terminal, Plots, CAN, Both;
  - same tab minimised, and another tab in front;
  - browser closed again (confirms the daemon returns to its no-client figure).

Decision gate, written up before any fix:

- Daemon dominates: profile it with `py-spy record` (scratch venv, not a project dependency); suspects are per-client WS encode, SQLite commit cadence, plot decode.
- Renderer main thread dominates: Step 2a.
- GPU process or browser main dominates: Step 2b.
- Total is small against the floor: report that the heat is not mcuscope and stop.

## Step 2a: renderer main thread

- Headed Chromium via Playwright (`headless=False`), CDP `Tracing` for 10 s per hot view; or a Firefox Profiler capture if Firefox is the owner's browser.
- Rank self time by function; compare against the 2026-09-23 figures to see whether headed differs from headless.

## Step 2b: paint, compositing, GPU

- DevTools Rendering: paint flashing, layer borders, FPS meter, per view while streaming.
- Suspects to check:
  - what repaints at the terminal flush rate (30 Hz) and the plot tick (5 Hz): is it only the changed region, or the whole window;
  - canvas backing stores at `devicePixelRatio` on a scaled display (chart and lane canvases are redrawn whole);
  - status bar rate counter and chips invalidating a large region;
  - CSS that forces layers or full repaints: `box-shadow`, `filter`, `backdrop-filter`, `position: sticky` inside a scroller, `will-change`;
  - whether the browser runs the display at its full refresh rate while anything animates.
- Check paint area with CDP `LayerTree` / `Paint` events, not by eye only.

## Step 3: fixes

- One fix per finding, each with a same-run A/B (original against patched modules served through `page.route`, as `ab.py` does), reporting CPU ms/s and package power.
- Candidate knobs if the browser cost is inherent: a lower terminal flush rate when lines arrive faster than anyone can read, or no repaint while the window is unfocused (not only hidden).
  - Any such knob is an owner pick (AskUserQuestion), not a silent default change.
- Each fix: JS test in `host/tests/webui_js/` where the logic is DOM-free, `test_webui_js.py` and ruff green, suite run once at the end; commit per fix, message shown for approval.
- Read the REVIEW.md class headings before touching daemon code; add a registry class if a fix confirms one.

## Step 4: report and native-client decision

- Write `docs/review/2026-10-11-laptop-heat/findings.md`: per-scenario table before and after, each fix with its saving, what was not covered.
- Native client: worth pursuing only if, after the fixes, the browser tab still costs well above the `mcu tail` control and the excess is in paint or GPU that the page cannot avoid.
  - If so, the earlier recommendation stands: PySide6 + pyqtgraph as an optional `mcuscope[gui]` extra; raise it with the owner, do not start it.

## Rules for the session

- Kill every process by PID; delete the Playwright profile dir after each run; scratch under `~/scratch/mcuscope/laptop-heat/`.
- Copy measurement scripts worth reusing to `~/git/mcuscope-tools/laptop-heat/`.
- Headed runs open a window on the owner's screen: say so before the first one.
