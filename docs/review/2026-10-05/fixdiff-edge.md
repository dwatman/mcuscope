# Fix-diff review: edge (link, protocol, firmware, webui)

Scope: `git diff 5eaaeeb` of serial_link.py, protocol.py, plots.js, app.js, style.css, the plot grammar fixture, firmware/monitor/*, firmware/tests/test_monitor.c, SPEC 2.x / 5.1 / 9 and INTEGRATION.md, plus the new `test_serial_link_rx_ingest.py` and `app_sidebar_resize.test.mjs`.
link.py and test_firmware_monitor.py have no diff.

Scratch: `~/tt-data/mcuscope-2026-10-05/fixdiff-edge/` (mut.py, fwmut.py, test_probe_edge.py, probe_*.mjs, logs; `fw/` firmware copy and `fp/out/` ARM builds, about 10 MB).

## Findings

### FD-EDGE-1 MEDIUM CONFIRMED: the name-cap notice re-announces on every interleave, one sys row per refused line

- `host/mcuscope/serial_link.py:991` (`elif plot and tag == "!p": self._name_overflow.clear()`), SPEC 2.5 "a line that plots ends the episode".
- Failure: the cap never frees a slot within the run, so the condition the notice reports is permanent once hit. A device that streams a steady channel and also mints new names (`!p t=..` plus `!p err_<n>=1`, the very bug the cap exists for) closes the episode with every steady line and reopens it with every new name.
- Repro: `test_probe_edge.py::test_probe_interleaved_overflow_rows`: 256 names, then 50 pairs of `!p n0=1` / `!p run<i>=1`, gives `refused=50 sys_rows=50`.
- Fix: latch `_name_overflow` once per port for the run (never clear it; `plot_name_refused` carries the rest), reword SPEC 2.5, and turn the last block of `test_a_name_past_the_cap_is_stored_as_an_event_counted_and_announced_once` into the interleave case asserting one row.
- Class: once-per-episode notice (as the oversized/unterminated latches); the episode boundary is wrong for a condition that cannot end.

### FD-EDGE-2 MEDIUM CONFIRMED: the browser plots a `!p` line the daemon refused

- `host/mcuscope/webui/plots.js:285` (`plotIngest` decodes every `!p` event row); the cap lives only in `protocol.PlotDecoder.points_from_tokens`.
- Failure: a refused line is stored as a plain event row, identical in shape to an admitted one, so the live chart draws a channel that `/plot/channels`, `/plot/series` and `mcu plot` do not have, and a reload loses it. Bites when the page loads after the device has rotated names (the browser's own 64-channel cap is then not yet full).
- Repro: `node ~/tt-data/mcuscope-2026-10-05/fixdiff-edge/probe_refused.mjs`: `plotIngest({chan:"event", raw:"!p 2 over1=1"})` charts `over1` (`PROBE browser charted refused line: true ["over1"]`).
- Fix: the browser cannot re-derive the daemon's admitted set, so mark the row: e.g. store the refused line with a distinct chan or a flag the row carries, and have `plotIngest` skip it; pin it with a JS test fed a refused row.
- Class 19 (two engines validating one thing: the mirror lacks the bound).

### FD-EDGE-3 LOW CONFIRMED: a detach and re-attach resets the 256-name cap while its counters carry

- `host/mcuscope/serial_link.py:1483` (`adopt` only on a replacing attach) and `:1520` (`_carried` holds the counters, not the name set).
- Failure: the ruling counts distinct names "per port within the daemon run". After `mcu detach` then `mcu attach` on the same alias the decoder starts empty and admits 256 more, while `plot_name_refused` and `rx_replaced` carry over as if nothing reset.
- Repro: `test_probe_edge.py::test_probe_detach_attach_resets_cap`: `refused_before=True admitted_after=True`.
- Fix: carry `_adhoc_names` in `_carried` beside the counters (or keep a per-alias set on the manager) and extend `test_the_replaced_and_refused_counters_survive_a_re_attach` to the name set.
- Class 4 (per-attach state lost on reattach).

### FD-EDGE-4 LOW CONFIRMED: the CLI decoder inherited the ingest cap

- `host/mcuscope/cli_output.py:512` (`LineDecoder.decode` calls `PlotDecoder.feed`), used by `mcu tail` (`cli.py:1058`) and the paged decode (`cli.py:905`).
- Failure: the cap was added to the shared `PlotDecoder` for ingest, so a client-side decoder over stored rows also stops decoding after the 256th distinct name it happens to see, which need not be the set the daemon admitted (window start, restarts, FD-EDGE-3). Past it lines print raw.
- Repro: `LineDecoder(names=None, changes=False)` over `!p 1 n0=1` .. `n257`: the first prints `p:n0 n0=1`, the 257th prints `!p 1 n256=1`.
- Fix: put the cap behind a flag the serial port sets (or apply it in `SerialPort` around `points_from_tokens`), leaving replay decoders uncapped; test that `LineDecoder` decodes a 300-name history.
- Class 86 (a helper reused under a changed contract).

### FD-EDGE-5 NIT CONFIRMED: `|k| >= 1` is redundant in both engines

- `host/mcuscope/protocol.py:822`, `host/mcuscope/webui/plots.js:254`.
- Both parsers reject a non-finite scale, so `k = 1/scale` is never 0, and 0 is the only integer below 1 in magnitude. Mutation removing the clause survives in both (`mut.py "k>=1 guard py"` / `"k>=1 guard js"`: SURVIVED).
- Fix: delete the clause in both and say in SPEC 2.5 "k an integer below 2^53". The Python `if not scale` guard is needed (ZeroDivisionError; mutation caught).

### FD-EDGE-6 NIT CONFIRMED: the `g_ovf_send_hook` guard in `monitor_init` is redundant and costs 16 B

- `firmware/monitor/monitor.c:1279`.
- `g_ovf_count` is only made non-zero after `event_end` set the hook, so the guard changes no behaviour (`fwmut.py "init ovf reset unconditional"`: 323/323 under `make run` and `make asan`), and an unguarded core build is 7578 B against 7594 B with the same RAM (M0+ -Os, buildx.sh).
- Fix: reset `g_ovf_count` unconditionally.

### FD-EDGE-7 NIT CONFIRMED: changed lines no test catches

- `serial_link.py:991`, the `tag == "!p"` half: mutating to `elif plot:` (a `!ps` sample also ends the name episode) survives. Moot if FD-EDGE-1 removes the clear; otherwise add a `!ps` case.
- `serial_link.py:929-934`: the episode's first and last timestamps and the `type(exc).__name__` fallback. Mutating "stamp first on every drop", "never stamp last" and "drop the fallback" all survive; the test checks only the prefix and suffix of the row. Add a case with a patched clock asserting `between A and B`, and one raising a message-less exception.

### FD-EDGE-8 NIT CONFIRMED: an unstored `!p` line still spends cap slots

- `host/mcuscope/protocol.py:1029` admits names in `points_from_tokens`, before `_submit_rx_line` submits the row; when the store refuses the line the names stay admitted with no row behind them.
- Repro: `test_probe_edge.py::test_probe_unstorable_names_admitted`: `ghost` held after a refused write.
- Fix: accept it and say so, or admit only after a successful submit. Bounded by the store-failure episode, so low value.

### FD-EDGE-9 NIT: the U+FFFD notice wording

- `serial_link.py:835`: "received bytes above 0x7F stored as U+FFFD (first in 2 lines)" reads as a mistake. Suggest "(2 lines in the first burst)" or drop the count, which `rx_replaced` already carries.

## Checked and fine

- Fixture in both engines: `pytest tests/test_plot_grammar_fixture.py tests/test_serial_link_rx_ingest.py tests/test_firmware_monitor.py` 114 passed; `node --test plot_grammar.test.mjs app_sidebar_resize.test.mjs` 13 passed.
- FUZZ-1 reaches every live decode path: `_submit_rx_line` folds before `classify`, `parse_response`, `_decode_can`, the plot decoder and `parse_marker`; every other decoder (`prime_plot_defs`, server.py learners, cli_output, JS) reads stored, already-folded text. JS agrees with Python on the folded forms (trailing space on `!p`, `!pd`/`!ps`, `!can`: `probe_fold_js.mjs`). Fold removed: caught.
- Exact-decimal scaling: Python and JS compute the same `1/scale` and integer test; zero, negative, subnormal (`1e-320` gives inf, multiplies) and past-2^53 cases behave alike. Zero guard and 2^53 bound mutations caught.
- OP-5: count, clear, carry mutations caught; a chunk with no LF returns before the latch, so a line split across reads does not reopen the episode.
- OP-2 decoder: cap off by one, adopt removed, admit removed: all caught.
- OP-3: host keeps `!can 21 r 200 12`, `!can 5 - 1234 07`, `!can 6 x 2FFFFFFF 07` as events with one `!can decode failure` sys row (probe); `can.js` rejects the same three. Firmware mutations re-mask id, re-clamp RTR dlc, drop the data clamp: each caught by `make run` and `make asan`.
- OP-4 hooks, each mutated alone in a scratch copy (`fwmut.py`, setter mutations made conditional so `-Werror` still compiles): all 15 caught under both `make run` and `make asan` except FD-EDGE-6. Core build links none of `ovf_on_*`, `plot_poll`, `plot_reset`, `event_end` (map after gc-sections); the plot build links all, so INTEGRATION.md's claim holds.
- Footprint re-measured with the round's scripts (APP printf, one bus): core 4445 / 4489 / 6004 B, plot 7325 / 7377 / 10356 B, RAM 968 / 1132; savings -Os (-O2) CAN 1136 (1736) / 16 B RAM, I2C 480 (536), all five 2324 (3156). All match SPEC 5.1 and INTEGRATION.md. Pre-batch source: core 4997 B / 1112 B RAM, plot 7205 B, so the changelog's "-0.5 KB / -144 B core, +0.12 KB plot" holds.
- Webui: resize listener, resizer `pointercancel`, divider `lostpointercapture` removals each caught; each test passes run alone (`--test-name-pattern`).
- No em/en dashes in the area's files; ruff clean on the changed Python; tree restored (`cmp` against the `.good` copies, `git status` unchanged).

## Not covered

- `touch-action: none` and the drag-end events on a real touch device; Windows (nothing in the area is platform-specific).
- Store-side half of OP-2 (summary rebuild keeps 256 names) and the server/cli surfaces of the new counters: other areas.
- `buses=2` RAM (the "12 B per extra bus" line) not re-measured.

## The two questions

1. Least confident: whether the browser case (FD-EDGE-2) shows in practice given the browser's own 64-channel cap. Rechecked by driving `plotIngest` directly; it does chart the refused line whenever the page has room, which is the reload-during-rotation case.
2. What would a fresh reviewer find that I did not look for: the decoder cap's other consumers. Found one (FD-EDGE-4, the CLI); the server's decoders only `learn`, so they are unaffected.
