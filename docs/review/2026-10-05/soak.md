# soak: what grows or degrades over hours

Setup: `mcu-sim --tcp-port 18701 --plot --flood 30` attached by `socket://` to an isolated `mcuscoped` on 18700, `--config` in scratch and `MCUSCOPE_*_DIR` in scratch.
- The brief's `mcuscoped --sim --plot` does not exist: `mcuscoped` has only `--sim` (the `--demo` stream set), and `--plot`/`--flood` are `mcu-sim` flags. The standalone sim gives the requested mix: two charts (typed `!ps` and ad-hoc `!p`), 4 digital/enum lanes, 7 CAN ids including the 0x100 heartbeat, and 30 flood lines/s. That is 134 lines/s, the same as webui-cpu.md's "realistic" rate of 130.
- Headless Chromium 1.62.0 at 1600x1400 with the default "Both" view. Terminal, CAN table, both charts and all 4 lanes were on screen (`start.png`).
- Sampled every 60 s for 80.5 min: 62 min visible, 10 min hidden (emulated, see Not covered), 30 s at 1 s resolution after show, then 8 min visible.
  - Heap was read before and after a forced GC, through CDP `Performance.getMetrics`.
  - Also sampled: DOM elements, main-thread task/layout ms/s, renderer and daemon CPU and RSS from `/proc`, daemon fds and threads, ESTAB TCP connections on the daemon port, db/wal/shm size, `/status` counters, long tasks and the sizes of the page's own arrays.
- Scratch: `~/tt-data/mcuscope-2026-10-05/soak/` (210 MB, mostly `capture.db` 157 MB and `ab.db`).
  - Scripts: `soak.py`, `ab.py`, `fresh_ctl.py`, `floor_probe.py`.
  - Data: `samples.csv`, `internals.jsonl`, `catchup.jsonl`, `ab.jsonl`; old smoke run in `smoke/`.
  - Isolated state dirs: `state/`, `abstate/`. Every process was stopped by PID; Playwright's temporary profiles were removed when it closed.

Headline: nothing in the page or the daemon leaks. The one unbounded quantity is the capture on disk: with the default config, a daemon left running never expires anything.

## SOAK-1: a daemon left running never ages out its capture, so the default config grows the disk without bound

- MEDIUM, CONFIRMED. `host/mcuscope/store.py:2982-3004` (`retention_floor_id`) with `auto_session = true` and `min_sessions = 5` (`host/mcuscope/config.py:93-107`).
- Failure: `auto_session` opens one session per daemon run, and the floor protects the newest 5 sessions, or every session while fewer than 5 exist, "however old they get".
  - A bench daemon that runs for weeks is a single session, so `retention_days = 10` never deletes a line. With `max_db_bytes = 0` (the default) nothing else bounds it.
  - The soak's 80-minute run was one session (`sessions` has 1 row, `auto-2026-10-05_18-48-41`).
  - The capture grew linearly at 1.95 MB/min (240 B per line, `plot_points` and its indexes about 45%): 157 MB after 647k lines.
  - That rate is 2.8 GB/day: 28 GB at the 10-day mark where the owner expects a plateau, and still growing.
  - When the disk fills, every write fails (`write_errors`), and from then on capture is lost.
- Repro: `floor_probe.py` opens one auto session as the daemon does, writes lines stamped 40, 20 and 11 days old plus one now, then sweeps at `retention_days = 10`.
  - Result: `floor_id 1 deleted 0`. All four lines are kept.
- The documents contradict each other:
  - `config.py:104-106` says "a capture is bounded by retention_days alone, so nothing is ever dropped for size unless the owner opts in". With the floor, that is false.
  - SPEC 3.3 (`docs/SPEC.md:441-445`) states both rules separately. It never says that together they leave a running daemon unbounded.
- Suggested fix (owner pick):
  - Do not protect the running session: the floor would cover ended runs only, and the live run would age out like unsessioned lines. This keeps the "quiet fortnight" guarantee for finished runs.
  - Alternatively, cap what the floor protects at a multiple of `retention_days`.
  - Either way, correct the `config.py` comment, and add a SPEC 3.3 line saying a continuously running daemon is one session.
- Known class: none fits. Candidate new class: "two retention rules that each bound the store, composed into one that does not".

## Checked and fine

- DOM: 929 elements at every sample from minute 0 to minute 80.5. CDP `Nodes` swings between 2200 and 4500 with detached rows awaiting GC, and returns to the same floor after each GC (`samples.csv` `dom_elements`, `nodes`).
- Event listeners: 233 throughout. Documents: 2 throughout. Page errors: 0. Long tasks: 2 in 80 min, max 63 ms, both during the contended stretch.
- JS heap after GC rose from 2.7 MB to 19.7 MB in 80 min, about 0.22 MB/min. All of it is the chart and lane rings filling toward `PLOT_CAP` (100k, `state.js:326`).
  - `internals.jsonl` shows `xsHost`/`xsTick`/`ids` reaching 95,654 at minute 79.6, and the lane arrays in proportion.
  - Arithmetic check: 20 Hz times about 21 rings times 8 B is 0.2 MB/min.
  - The two 2.5-4 MB steps (minutes 43 and 64) are V8 backing-store growth at about 51k and 77k elements, not events. The block trim at `plots.js:625-630` and `digital.js:129-160` bounds them.
- Shared buffer and pane: `buffer` stays 5000-5512 and `pane0.queue` stays 0-25 while visible (`BUFFER_MAX`/`BUFFER_SLACK`). CAN rows: 7.
- Daemon RSS rose from 60 MB to 135.5 MB, then stayed flat from minute 34 to the end (128.8-139.3 MB).
  - The rise matches the writer's 64 MB page cache (`store.py:795`, `cache_size=-65536`) filling with new pages.
  - Its slope, 2.27 MB/min, tracks the db's 1.95 MB/min, and it stopped at about cache size.
- Daemon fds: 23, threads 9, flat for 80 min. While hidden, fds went to 22 and connections to 1: the paused status poll let its keep-alive connection close, and both came back on show.
  - The WebSocket connection stayed up throughout. `ws_dropped` 0, `rx_dropped` 0, `write_errors` 0, no warnings in `daemon.out`.
- WAL: 4.16-4.39 MB for the whole run. Checkpoints keep up, and `journal_size_limit` is never needed.
- CPU does not rise with uptime:
  - Uncontended (minutes 1-41): main-thread task 85-99 ms/s, layout 13-15, renderer process 211-242 ms/s, daemon 34-39 ms/s, flat from minute 1 to 41.
  - From minute 42 the shared machine got busy (load 3.2, other agents' browsers): page and daemon CPU rose together by the same factor, about 2.4x.
  - Control at minute 49 (`fresh_ctl.py`): a fresh page on the same daemon cost 305-338 ms/s, against the 49-minute-old page's 247-328 in the same minutes.
  - `ab.py`: two same-age pages in separate browsers stayed within 3% of each other for 27 min, and a fresh third page joining at minute 28 cost the same as both (295-310 against 267-295).
- Against webui-cpu.md: its "realistic-late" (VIEW_MAX reached) was 247/219 ms/s main thread before WEBUI-CPU-1, and 110/131 with the fix prototyped. This soak's uncontended 85-99 ms/s after an hour at VIEW_MAX shows that the landed fixes (`terminal.js:465-470`, `api.js:151`) hold over time.
- Hidden for 10 min, then shown (`catchup.jsonl`, `ab.jsonl`):
  - While hidden: 17 ms/s main thread with layout 0 (`soak.py`), and 22-28 ms/s against the visible twin's 300-440 (`ab.py`). That is ingest only. `pane0.queue` stays at VIEW_MAX+slack (5319-5491), not growing.
  - On show: the queue flushed to 1 within the first second. There were 0 long tasks in the 30 s after show, and the heap stayed at 19.3-20.8 MB.
  - Per-second task time after show (300-500 ms/s) is the same as the contended steady state that followed.
  - In `ab.py` the shown page A tracked never-hidden B at 460-505 against 445-486 ms/s from the first minute after show. Nothing persists after a hide.

## Not covered

- A real background tab: headless Chromium reports every tab visible. Neither `bring_to_front` of another tab nor a CDP window minimise (both headless modes, `vis_test.py`) changed `visibilityState`.
  - Hidden was emulated as in webui-cpu.md: `document.hidden` was overridden and `visibilitychange` dispatched. The page's own gates ran, but Chromium's timer throttling, intensive throttling (1/min after 5 min hidden) and rAF suspension did not.
  - A headed or Xephyr run would open a window on the owner's desktop, so none was made.
- Steady state past `PLOT_CAP`: the rings reached 95.6k of 100k at minute 79.6, so the trim regime (from about minute 83 at 20 Hz) was not soaked. Earlier rounds measured the trim's per-sample cost.
- WebSocket subscriber count: `/status` does not expose it, so ESTAB TCP connections on the daemon port were counted instead (2 visible, 1 hidden).
- Runs longer than 80 min. A second port. Session rollover (`POST /session`) during a soak. Windows and Firefox.
- The SOAK-1 rate is for this sim mix. A board with no plot traffic stores roughly half the bytes per line.

## The two questions

1. Least confident: that CPU does not rise with uptime, because the machine became contended at minute 42.
   - Rechecked by driving controls rather than reading: `fresh_ctl.py` (fresh against aged page in the same minute) and `ab.py` (twin pages, one hidden, plus a late fresh page).
   - Both put the rise on the machine, not the page. The uncontended first 41 minutes are flat on their own.
2. What we had not thought about: the soak's per-process figures all plateaued, and the unbounded quantity was the one no browser metric shows, the disk.
   - That led to the composition of `auto_session` and `min_sessions` with retention (SOAK-1). Earlier rounds tested each rule alone (`test_sessions.py:486-605`), and never a single long session aged past `retention_days`.
