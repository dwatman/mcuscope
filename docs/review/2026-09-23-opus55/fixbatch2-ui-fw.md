# Fix batch, fix-diff leg 2: web UI and firmware

HEAD: `b889e2b8fb848866054da6ba09cd2e651d9f69c7` (uncommitted working tree).
Revert copy and mutation driver: `~/tt-data/mcuscope-2026-09-24/fixbatch2-ui-fw/` (`copy/`, `mut.py`, `sim_trim.py`).

## U-1 (docs follow the code)
- `firmware/monitor/INTEGRATION.md:455-456`: a `!p` whose first pair does not fit goes out as `!p <tick>` then the notice, and is stored as a generic event row. Only a line with nothing past its type, or a marker with no text past `@tick`, goes out as the notice alone.
- `firmware/monitor/README.md` does not describe the cut, so it is unchanged. cli.py is not touched.
- Pinned: `test_monitor.c:723-729` ("eventf p cut to its tick") and `test_sim_error_codes.py:88` (`!p 5 v=1...` gives `["!p 5", notice]`). These pin existing behaviour; no branch changed.

## U-2 (divider between contiguous rows)
- `pane.js:152-164`: `gapRow` also records `lo` (the lowest missing id) and `why`. The new `narrowGap(g, oldestServedId, floor, ts)` returns what is left of the hole above the clear point, or null.
- `terminal.js:544-566` (`loadHistoryPage`): after each page, the leading dividers are replaced.
  - A divider whose hole the page reached the bottom of is removed.
  - A partly filled hole moves ahead of the page with its count cut.
  - All leading dividers go when the walk ends: nothing older, or `planHistoryPage`'s own divider counts everything below.
  - The scroll offset moves by the net rows added.
  - A page the filter empties still narrows the divider, and the hops continue.
- Tests in `terminal_history.test.mjs`:
  - The existing shed-divider test (40-row hole) now asserts the divider is gone, the rows are contiguous and the scroll is `199*18`.
  - New tests:
    - partial fill: `gap: 800`, then `gap: 600`, one divider, ts taken from the page.
    - walk exhausted.
    - walk spent at HISTORY_MAX: only the plan's `gap: 4789`.
    - filter-emptied pages: narrowed, 5 hops, divider ts kept.
    - cleared pane: `gap: 89`, not 300.
    - pure `narrowGap` cases.
  - Each new test also passes alone (`--test-name-pattern`).
- Revert (`mut.py`, 11 mutants): all killed.
  - Mutants: the done branch, the early return, the return value, the scroll offset, the floor in `narrowGap`, `n > 0`, both ts sources, the clearId pass-through, `gapRow.lo`, and the whole fix.

## U-3 ("type is exactly `m`" guard)
- C: `test_monitor.c:716-722` ("eventf type starting with m keeps its @7"): `mode @7 ` + y*292 gives `!mode @7` then `!e event mode overflow`.
- Sim: `test_sim_error_codes.py:89-90`, same case through `_encode`.
- Revert:
  - C: `if (g_out[1] == 'm')` fails that check (287/288).
  - Sim: `first.startswith("m")` fails `test_a_cut_that_keeps_only_the_header_sends_only_the_notice` at line 90.

## U-4 (staging fold notices)
- `api.js:732-747` (`stageRow`): adjacent notices are merged in the trim, whether folds or the daemon's own.
  - A count below 1 joins as 0, as `handleWsRow` reads it.
  - Every trim leaves one notice per hole, which removes the growth, the O(k) trim and the `ponytail:` note. The comment is rewritten.
  - Merging is equivalent at the drain: adjacent notices each end a segment and sum into `pendingGap`.
- Test: `fixbatch_chrome_staging.test.mjs:78-98`, "repeated staging trims keep one notice, so the area does not trim early".
  - `{gap:-3}` and `{gap:-2}` are staged, then the stream ends 4 entries before the merged area's third trim.
  - It asserts one divider, `BUFFER_MAX + BUFFER_SLACK - 4` lines behind it, and the exact count.
- Revert: all 4 mutants killed (no merge, the `prev` clamp, the `r` clamp, merging any row into a notice).
  - A first version that ended exactly on a trim did not discriminate: at trim points both variants keep 4999 lines (`sim_trim.py`).

## U-5 (decimateColumns defaults)
- `timewindow.js:137-139`: the `xmin`/`xmax` defaults are removed, and the comment says callers pass the window.
- The seven test calls in `plots_decimate.test.mjs` pass `xs[0], xs.at(-1)`. The only production caller, `plots.js:1129`, already passes `winMin, winMax`.
- Revert: putting the defaults back survives, as expected. No caller relies on them, and JS cannot enforce a required parameter.

## U-7 (i2c scan budget)
- `monitor_cmds.c:266-267`: the clamp is `MON_OK_PAYLOAD_MAX + 1` (the payload plus its NUL), as in `read_into_resp`.
- Test: `test_monitor.c:1022`. `test_i2c_scan_bus_shorted` now pins 0x08..0x59 (82 addresses, a 255-byte line at seq 65535).
- Revert: the old clamp fails "shorted-bus scan exact list".

## Runs
- C:
  - `make run` 288/288, `make asan` 288/288, `families` and `families-asan` 18/18.
  - Strict ARM, `arm-none-eabi-gcc` 13.3 from `/opt/st/stm32cubeide`, both files: clean on M0+ and M4F.
  - Flags: `-Os -Wall -Wextra -Wformat=2 -Wconversion -Wsign-conversion -Wshadow -pedantic -Werror`.
- Python: `pytest tests/test_sim_error_codes.py tests/test_firmware_monitor.py` 26 passed; ruff clean on the changed test.
- JS, run one file at a time: all green.
  - Changed files: `terminal_history` 20, `fixbatch_chrome_staging` 5, `plots_decimate` 10.
  - 31 neighbouring files that mention gap, staging or decimate, including `module_load_order` and `smoke`.

## SPEC wording
- 9.1 (line ~1627): after "...which is what fills the hole the divider names.", add: "Once a page reaches the bottom of that hole, or the walk ends, the divider goes. A partly filled hole keeps its divider ahead of the page, counting only the lines still missing."
- 2.3 or 5 (the event-cut paragraph), to match U-1: "A `!p` whose first pair does not fit keeps its tick (`!p <tick>`, stored as a generic event); only a bare type, or a marker's lone `@<tick>`, is dropped for the notice alone."

## CHANGELOG lines
- Web UI: a gap divider that history paging has filled is removed, and a partly filled one moves above the loaded page with the count still missing.
- Web UI: live rows staged behind a slow first backfill keep one shed notice per hole, however long the backfill stalls.
- Firmware: `i2c scan` on a shorted bus lists one more address (82), using the same payload budget as the read commands.

## Doubts
- U-2 "divider stays" for a partial fill is read as "stays in the pane, moved above the page with a smaller count". Left in place, it would sit between contiguous rows, which is the defect being fixed.
- U-2 counts are id arithmetic. They are exact only while capture ids are contiguous, the same assumption `planHistoryPage` already makes.
- U-2 needs a manual check in a browser against the simulator: a filtered paused pane after a reconnect, scrolled to the top.
- U-5: a future caller that omits the window now gets NaN columns, and so no decimation, rather than coarse columns. It is still silent. A throw would add a branch no current caller needs.
- `docs/ARCHITECTURE.md` was not checked for divider wording (not my file).
