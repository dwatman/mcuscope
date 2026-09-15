# Fix-diff review: browser leads 1 and 2 (ids)

Scope: `fix-browser-ids.md`'s changes to `server.py`, `plots.js`, `digital.js`, `exportrange.js`, `exportdlg.js`, `chrome.js`, and their tests.
Scratch probes: `~/tt-data/fd-ids/` (`probe_seed_order.mjs`).

## HIGH

### H1. Tick-base lanes export a shorter window than they draw once the id index has trimmed

- Where: `digital.js:135-138` (cap at PLOT_CAP samples per stream), `digital.js:417-421` (a window reaching past the oldest kept tick takes `first = 0`).
- Scenario: one 1 kHz stream with a bit field, lanes window "5m" (an offered choice, `chrome.js:8`), tick base, pause, export "Shown window".
  - The lanes draw 300 s, since their ring holds transitions, not samples.
  - The index keeps about 100 s of samples, so `since_id` is the oldest kept id. The file holds the newest 100 s of 300 s, with nothing saying so.
  - The same happens at the 30 s default above about 3.4 kHz, and for any zoom straddling the index start.
  - Before the fix, `hostAtTick` fell back to the first vertex and exported the whole window, so this is a regression.
- The `ponytail:` comment (`digital.js:50-51`) and the "ceiling" test (`fix_browser_ids.test.mjs:257`) pin the truncation as intended. At 1 kHz it is not a high-rate corner.
- Fix: mark an index that has trimmed. When the window's lower edge falls before its oldest tick, do not offer the shown window, with a reason (or bound that side by time). Alternatively, size the index to the longest window.

## MEDIUM

### M1. A `!pd` inside the first connect's backfill is primed after the seed, so that stream seeds in name order

- Where: `api.js:251` (`seedPlotDefs` stops at `id_to=rows[0].id`), `api.js:535` (seed) before `api.js:558` (the backfill's `!pd` rows reach `plotIngest`), and `plots.js:408` (`declared` undefined, so no sort).
- Scenario: a board is reset or the sim is restarted, and the page reloads while the new `!pd` is still among the newest 200 lines.
  - The seed window reaches the pre-reset samples, but no definition is primed, so chips and lanes come back alphabetical.
  - The same happens when the only `!pd` is older than the 20000-line lookback.
  - Driven: `probe_seed_order.mjs` gives chips `za, zt` with the `!pd` in the backfill; the control (`!pd` in the lookback) gives `zt, za`.
- The ruling and the new SPEC line (`SPEC.md:1745`) are unconditional. The author's claim that `seedPlotDefs` primes before the seed holds only for definitions older than the backfill.
- Fix: before the seed, read field order from the newest `!pd` among the backfill rows (parse only, no caching). Or have `/plot/channels` carry each field's index.

### M2. Every chart and tick-base lanes shown-window export is now named `capture_plot_start-end.csv`

- Where: `server.py:1905-1909` folds `since_id` into the scope only. `win.lo`/`win.hi` come from ts bounds, so the filename at `server.py:1956` reads unbounded.
- Driven: `since_id`+`id_to` gives `capture_plot_start-end.csv`, and the equivalent ts window gives `capture_plot_20231115T071321-20231115T071323.csv`.
- A 30 s paused-chart export now claims the whole capture in its name, and repeated exports differ only by the browser's `(1)`.
- This is SPEC-compliant (3.4 names only ts and `last_ms` bounds) but a regression on the exact path fixed.
- Fix: when `since_id` is given, name `from`/`to` from the ts of the first and last rows in the id scope. Add the rule to SPEC 3.4's filename paragraph.

## LOW

### L1. Disabled shown-window title tells a paused user to pause

- Where: `exportdlg.js:136`.
- Scenario: a paused chart under the host base with a drag zoom holding no sample. This is newly reachable through the behaviour change the author notes, and also through tick-base lanes with no sample.
  - The radio is disabled with "pause the panel to export exactly what it shows" (class 58 face: help naming a remedy that does nothing).
- Fix: when `ctx.watermark != null`, the title says the window holds no sample.

### L2. Host-base lanes: the residual is larger than "one row", and the ruling says end to end

- Where: `digital.js:409-411`, and `pushVertex` `digital.js:115`.
- Scenario: a lane toggling on every sample in a 20-sample burst draws 20 vertices spread over 2 ms (1e-4 each).
  - A zoom edge inside that spread misses (left edge) or adds (right edge) up to 19 rows, not one.
  - With bursts every 5 to 60 ms, a random edge lands in such a spread a few percent of the time.
- Defensible if a lane row's position is its stored ts, since vertex nudges are per lane and name no row order. But the owner ruled ids end to end, and this is recorded as settled in SPEC 9.1.
- Fix: flag it as an owner pick. Either SPEC says host-base lanes select by stored ts, or the index stores host ts and the host base goes by id too.

### L3. The index snapshot outlives resume

- Where: `digital.js:785-798` never clears `ix.frozen`. The comment at `digital.js:148` ("resume drops it") is now true only of lane vertices.
- Scenario: pause and resume once with 10 streams at cap. About 2 million numbers stay held while live, until the next pause or clear.
  - Correct, since nothing reads it while live. The deleted branch was memory, not behaviour.
- Fix: restore `ix.frozen = null` on resume. Otherwise, correct the comment and accept the memory.

### L4. `noteLaneId` indexes streams no lane accepted

- Where: `digital.js:102-103` runs even when every point was refused by `MAX_LANES`.
- Scenario: at the lane cap, a new stream's index still widens its port's `[min first, max last]`.
  - `seedTargetHasData` (`plots.js:367`) sees no lane there, so a seed landing after live samples appends older ids at a clamped tick.
  - That drags `since_id` far back, and other streams' rows outside the window come in.
- Fix: note the id only when at least one lane took a point, i.e. `tickX` came from a lane.

### L5. SPEC 9.1 wording is off by one

- Where: `SPEC.md:1604` says a chart "sends the line ids of the first and last samples it draws, as `since_id` / `id_to`".
- `since_id` is exclusive: the code sends the first id minus one. A client built from this sentence drops the first sample.
- Fix: "`since_id` one below the first drawn sample's id, `id_to` the last one's". Same for the lanes clause.

### L6. Test gaps and test-only code

- `test_plot_export_since_id.py` never combines `since_id` with `last_ms`, `until_ts` or `decode=1`. The decode anchor (`first_export_line_id` under the folded scope) is unpinned.
  - Add one case each. `decode=1` needs a `!pd` redefinition between an earlier row and the cursor.
- `digitalIngest(..., stream = port)` (`digital.js:62`): production always passes the chart key, so the default serves only tests. Make `stream` required and pass it in tests.
- Comment `digital.js:440` ends mid-thought ("per port, since tick-base ids are."). Say "ids differ per port".

## Checked and clean

- Server: `since_id` intersects `session` (tighter wins), `since_ts`, `until_ts` and `last_ms`.
  - `last_ms` is still anchored on the caller's bound, computed before the fold (classes 44 and 67).
  - The scope reaches `export_sids`, `first_export_line_id` and `open_plot_export` alike.
  - `MAX_LINE_ID` binds with no 500; negative values are accepted, as `/lines` accepts them. Store clauses are ANDed and sargable on `idx_plot_line`.
- SPEC 3.4's "intersects every other bound": the store appends `since_id` as an AND clause in all three query builders.
- Class 53: the page is served by the daemon it talks to, so `since_id` cannot reach an older `/plot/export`.
- Class 54: `since_id` has one JS builder (`exportrange.params`).
- Class 57: `laneIds` is keyed by chart key (port|sid) and filtered by `ix.port`. Charts are per port, and two ports export independently.
- Two ports, one exported: each Port choice uses its own window, and a port with none sends `since_id = id_to = watermark`.
- Class 25: a stream born while paused has an empty frozen index, and a chart born paused snapshots empty `ids`.
- Class 26: `chart.ids` and `ix.ids` are snapshotted at pause and trimmed in step with their rings.
  - A ring trim, or an index trim, while paused leaves the export alone (driven by the tests).
- Class 73: `wins` is computed at dialog open. The function lookup after the `sessionsReady` await reads no live state, and a capture reset in between is refused by `openCapture`.
- Class 77: board reset or 2^32 wrap inside a window.
  - Chart x arrays and index ticks are both continued (`continueTick`), and the gap point's null id is skipped on either edge.
  - The index's non-decreasing clamp only handles sub-100 ms repeats.
- Capture reset: `resetForDbReset` clears charts (and their `ids`) and `laneIds`, so no pre-reset id survives in a frozen view.
- `!pd` redefinition after the seed: chips keep their order and new names append, as on the live path. `byName` key order covers bit lanes (group then lanes).
- Memory: `chart.ids` adds one array per chart (at most PLOT_CAP+PLOT_SLACK). `laneIds` is bounded by 10 sids per port times the same cap, plus L3.
- Author's departures:
  - Per-stream index: sound; the per-port form flattens the second seeded stream.
  - Chart host base by id: sound, since nudged `xsHost` is the drawn position.
  - A host-base zoom with no sample not offering the window: sound and in SPEC (see L1 for the title).
  - Deleted resume branch: behaviourally sound (see L3).
- Tests:
  - All 17 new JS tests and the 11 changed ones pass run alone (`--test-name-pattern`), and the py file passes (14).
  - Spot revert of P8a (gap skip on the first index) fails only "a reset's gap point on either zoom edge", as the author reported.
  - Negative assertions have positive controls: born-paused, empty zoom, two ports, and `since_id >= id_to`.
  - The guard double declares `since_id` as the daemon does.
- `chrome.js:14-16` comment is now true. No em or en dashes in the diff. No CLI option changed, so the AI guide and SPEC 4 are unaffected.
- Outside scope, not reviewed: `can.js:617` still bounds its shown window's lower edge by ts. Whether "end to end" covers the CAN table is for the owner.
