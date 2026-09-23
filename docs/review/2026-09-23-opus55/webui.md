# webui: hostile input, endurance, disruption

Headless Chromium (Playwright 1.62.0) against own daemons on 18720-18739, fed by a Python fake board (`fake.py` + `scenarios.py`).
All scripts, logs and screenshots: `~/tt-data/mcuscope-2026-09-23/webui/` (one dir per probe, `run.log` in each).

## WEBUI-1 HIGH CONFIRMED: the UI can be framed by any web page and clickjacked (one click detaches a port)

- Where: `host/mcuscope/server.py:807-818` (`_NoCacheStatic`) and `:846` (the `/ui` mount): no `X-Frame-Options` and no `Content-Security-Policy: frame-ancestors`, on any response.
- Failure: a page on another origin loads `http://127.0.0.1:<port>/ui/` in an invisible iframe.
  - Requests made from inside the frame have the daemon's own origin, so the same-origin guard (SPEC 3.1) passes them.
  - A user click on the bait lands on the framed UI: the port chip `×` detaches the port (`DELETE /ports/<alias>`, no confirm) and capture stops.
  - Other one-click actions reachable the same way: disconnect, reconnect, session stop, Marker. `confirm()` is blocked in cross-origin frames, so session delete is not reachable.
- Repro: `clickjack.py` serves `evil/index.html` from `http://localhost:18736` (opacity 0.0001 iframe) and clicks at the detach button's coordinates. Output: `ports before: ['b1']`, `ports after: []`.
- SPEC 3.1 already puts "a web page the operator visits" inside the threat model; framing is the hole beside the CSRF guard.
- Fix: send `X-Frame-Options: DENY` and `Content-Security-Policy: frame-ancestors 'none'` on every response (at least `/ui/*` and `/`). Name it in SPEC 3.1.

## WEBUI-2 MEDIUM CONFIRMED: ad-hoc channels printed on separate `!p` lines draw as nothing

- Where: `host/mcuscope/webui/plots.js:599-600` (a channel absent from a sample gets `null`) with `:954` (`spanGaps: false`, stepped paths).
- Failure: an ad-hoc chart holds one shared x array for all of its channels.
  - Firmware printing `!p <t> temp=..` in one place and `!p <t> volt=..` in another (the SPEC 2.5 `monitor_eventf("p %lu ax=%ld", ...)` one-variable idiom, used twice) alternates `value, null`, so every point is isolated between nulls.
  - Result: the chips show live values while the chart area stays empty apart from a few stray dots. The same happens after a reload (the seed merges into the same array).
  - It also hits the bundled `stream` scenario shape: `!p fast=` at 50 Hz beside `!p adhoc=` at 10 Hz leaves `adhoc` invisible (`wsgap/gap.png`).
  - SPEC 9.2 says traces are stepped hold-last. These are not.
- Repro: `adhoc2.py` (scenario `adhoc2`: `temp` and `volt` on alternate lines at 10 Hz each). Screenshots `adhoc2/live_crop.png` and `adhoc2/reload_crop.png` show an empty chart. The canvas pixel census has no series colour among the top 8.
- Fix: in the ad-hoc chart (sid null), hold a channel's previous value for samples it is absent from, or give each channel its own x array. Keep `null` for the real breaks (a tick reset gap point). A typed stream is unaffected: every `!ps` carries every field.

## WEBUI-3 MEDIUM CONFIRMED: shed WebSocket rows are drawn as a held level; the terminal joins across the hole

- Where: `host/mcuscope/webui/api.js:222-227` (`{gap}` ignored by design, SPEC 3.4) and plots.js ingest, which appends the next sample after the gap as if it were adjacent.
- Failure: rows shed for a slow subscriber are announced by `{"gap": n}`.
  - The charts draw a flat stepped hold across the whole shed span: 5 s of a sine wave rendered as a constant. This is a fabricated trace, not a visible gap.
  - The terminal shows the rows before and after as adjacent, with no divider, although the pane already has one for backfill gaps (`gapRow`).
  - SPEC 3.4 sanctions not re-fetching, but it does not say the surfaces may draw across the hole.
- Repro: `wsgap.py` proxies `/ws` with `page.route_web_socket`, drops 996 rows (5 s), then sends `{"gap": 996}` ahead of the next frame.
  - Buffer: id 2735 is followed by 3732 (dt 5.04 s), `gapRows: 0`.
  - `wsgap/gap.png` shows ax/ay/az flat across the span.
- Fix: on `{gap}`, push a `gapRow` divider to the buffer and a null break point into every chart and lane of that port, so the hole shows as a hole. Update the SPEC 3.4 paragraph to match.
- Reaching it for real needs a renderer that stops reading. A 150 s `Page.setWebLifecycleState frozen` did not shed: the network process kept buffering, `ws_dropped` delta 0. Throttled CPU with a high line rate was not tried.

## WEBUI-4 LOW CONFIRMED: time labels collide or clip once ticks reach 10 digits

- Where: `host/mcuscope/webui/digital.js:584-586` (the rightmost ruler label is clamped inside the ruler, onto its neighbour). The chart x axis clips its last label instead.
- Failure: under the tick base with ticks of 10 or more digits (MCU uptime past about 11.6 days, or after a jump) the ruler prints `21474800002147490000`. The chart axis shows `214749000` with the last digit cut off.
- Repro: `ticks.py clockjump` renders `ticks-clockjump/combo.png`.
- Fix: skip a label that would overlap the previous one, or pick the tick count from the measured label width.

## WEBUI-5 LOW CONFIRMED: very large finite values blank the chart

- Where: plots.js y scales (`auto: true`), in uPlot's range computation.
- Failure: an ad-hoc channel alternating `1.7e308` / `-1.7e308` (both finite, both accepted) shows its value in the chip and draws no trace. The cause is suspected, not traced: max - min overflows to Infinity.
- Repro: `values.py` (scenario `values`), screenshot `values/v.png`.
- Known class 6 (non-finite reaching the chart), reached here from finite inputs.
- Fix: clamp or explicitly range a y scale whose span is not finite.

## WEBUI-6 LOW CONFIRMED: long or bidi free text distorts the header and the axis

- A session name (REST, up to 128 chars) is shown unclipped on the session chip, which wraps the header onto two rows.
  - A U+202E in the name spoofs the chip: `gpj.exe` renders `exe.jpg`.
  - The same happens in its `title` tooltip, where the rest of the sentence is reversed.
  - `inject/main.png`.
- A 200-character `!pd` unit (SPEC 2.5 allows any character) becomes the soloed chart's y-axis label and runs down the whole chart height (`inject2/plots.png`). The chip row clips it correctly.
- Fix: `max-width` + ellipsis on the session chip, and `unicode-bidi: isolate` (or `<bdi>`) around user text in chips and titles. Truncate the axis label.

## WEBUI-7 LOW CONFIRMED: a page left open across a daemon upgrade never offers a reload

- Where: `host/mcuscope/webui/statusbar.js:80` shows `/status` `version` as the daemon version; nothing compares it with the version that served the page.
- Failure: after `uv tool upgrade` and a daemon restart, the open tab keeps running the old modules against the new API. The brand label reads the new version, which reads as "this page is current".
- Repro: `version.py new` rewrites `/status` to `9.0.0`: the brand shows `9.0.0`, and no badge or console notice appears.
- Fix: remember the first `version` seen and show a "daemon updated, reload" badge when it changes.

## Checked and fine

- No script execution from data. Injected payloads (`<img onerror>`, `<svg onload>`, `<script>`, `</div><iframe srcdoc>`, `javascript:`, template syntax, `<style>`, `<a href=javascript:>`, attribute breakers) went through:
  - serial lines, `!m` markers, `!e`, `!pd` units, malformed `!can`, the `ping` target name, `ERR` detail, an `OK` echo, the device string, a REST session name and note, and a 3000-char REST marker.
  - Result: `__x()` never called, no `<img/svg/iframe/style/script>` element or `on*` attribute in the DOM, no dialogs (`inject.py`, `inject2.py`).
  - There is no `innerHTML`/`insertAdjacentHTML` anywhere in `webui/*.js`, and none in vendored uPlot.
- Payloads show as text: terminal, marker dividers, chips, port chip (target), cmd result, Settings sessions list, `title` tooltips.
- Control characters (ESC, BEL, BS, VT, FF, NUL, DEL, embedded CR, TAB) render as glyphs or spaces in one 18 px row. They never break the virtualiser.
- 4000-char lines render as one clipped 18 px row with an ellipsis. Non-ASCII bytes arrive as U+FFFD (daemon decodes ASCII with replace), so bidi and zero-width characters cannot come from the target.
- Session export filenames are sanitised by the daemon (`_img_src_x_onerror___x_40___.._.._etc_passwd__x_.db__gpj.exe.db`): no path separators, RLO stripped.
- Wire-named object stores are null-prototyped (`chrome.js` colours, `state.js` port maps, `PLOT_TYPES`, `DISCONNECT_WHY`).
- f4 NaN/+Inf/-Inf samples, and a `*1e300` scale overflowing to inf, are rejected without errors or a broken chart (`values.py`). Enums at s4 MIN/MAX render.
- Endurance, 16 min at about 187 lines/s (3 typed streams, digital, ad-hoc, CAN 40/s, text), `endure/samples.jsonl`:
  - DOM nodes flat at 2130-2555, listeners constant at 255.
  - Frame time ends at 16.7 ms mean, p95 16.7, no long tasks. Earlier dips to p95 133 ms coincided with the parallel rotate run.
  - Post-GC heap grew 2.35 to 9.8 MB, linear, which is the plot rings filling toward `PLOT_CAP`.
- Rotating names at about 800 lines/s for 6 min (`rotate/`, `heap-rotate/`):
  - Caps hold at 64 channels, 64 lanes (65 `.dlane`) and 256 CAN rows. Each cap warns once.
  - Heap growth (17 to 25 MB over 180 s) traced by snapshot retainers to chart `xsHost`/`ys` ring arrays, which `PLOT_CAP` bounds.
- Disruption (`disrupt.py`): no duplicate or backward ids in the buffer after any of these steps.
  - Board killed and restarted.
  - Daemon SIGTERM and restart on the same capture: scrollback kept.
  - Daemon restart on a deleted capture: the page re-seeds, first id 27.
  - Daemon SIGSTOP for 10 s, then SIGCONT.
  - Page frozen for 150 s via `Page.setWebLifecycleState`.
  - Status reads "daemon unreachable" while down or stalled, and recovers.
- Emulated hidden tab for 180 s (document.hidden plus visibilitychange, rAF held): on show, no long task and the terminal is at the newest row (`hidden.py`).
- Tick glitch (one sample 49 days ahead) and 2^31 forward and back jumps behave as SPEC 9.2 describes: continuity offset, a break, and the lanes follow (`ticks.py onespike|clockjump`).
- Version skew via rewritten `/status` and `/plot/channels` (`version.py old|new`):
  - Old daemon without `capture`/`now`/`ws_dropped`/`session`/`target`/`ports`: no errors.
  - New daemon with changed shapes (object `capture`, string `now`/`lines_rx`, unknown `kind`): no errors, charts intact.
  - Dropping the per-channel `port` duplicates charts, but every released daemon (0.1.0 to 0.4.0) sends it.

## Not covered

- A real hidden tab: headless Chromium keeps every tab visible, and minimising the window via CDP did not change `visibilityState`. Timer throttling therefore went untested.
- WS shedding caused by a genuinely slow renderer (CPU throttling plus a high rate). WEBUI-3 was driven by a proxy instead.
- Endurance up to `PLOT_CAP` (about 33 min at 50 Hz) and multi-hour runs: the heap plateau is projected, not observed.
- Host wall-clock steps in the daemon's timestamps (no faketime here).
- Settings dialog config values with hostile content written through `/config` (db_path, plotjuggler dest), and the attach dialog device list (OS USB descriptors).
- Windows and Firefox.
