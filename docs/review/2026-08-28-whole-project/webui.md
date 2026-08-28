# Review round 2, leg: web UI modules

HEAD checked: `fd76735 POST /ports held to the config-write bar` (matches the expected fd76735).

Scope read end to end: `host/mcuscope/webui/{api,app,can,chrome,cmdbar,digital,freeze,pane,plots,settings,state,statusbar,terminal,theme,timewindow}.js`, `index.html`, `style.css`, and `host/tests/webui_js/` (all 28 test files plus `dom_stub.mjs`). `vendor/` excluded as instructed.

Baseline: `uv run python -m pytest tests/test_webui_js.py` passes (1 passed, 30.7 s), so every finding below is new rather than a known-failing case.

Probes live in `/tmp/claude-1000/review-r2/probe/` and were run with `node` against the real modules through the existing `dom_stub.mjs`.

Counts: 1 HIGH, 3 MED, 6 LOW, 2 manual-verify.

---

## HIGH

### H1. A duplicate (line_id, name) in a seeded series permanently misaligns the chart's y array against its x array

CONFIRMED. `host/mcuscope/webui/plots.js:291` (`mergeSeedSeries`) feeding `plots.js:393` (`addSample`).

Invariant broken: a chart keeps one x array shared by every channel, so `chart.ys.get(name).length` must equal `chart.xsHost.length` after every `addSample`.

`mergeSeedSeries` groups `/plot/series` points by `line_id` and pushes `[channel.name, pt.value]` per point, with no per-row name-uniqueness gate. `addSample` then pushes one x but iterates `points` and pushes one y **per entry**, so two entries naming the same channel push two y values against one x. `present = new Map(points)` dedupes, so the trailing gap-fill loop does not compensate, and the block trim at `plots.js:442` splices the same `drop` off both arrays, so the offset is permanent for the life of the page.

This is not a hypothetical input. SPEC 9.2 states it outright: "A capture written by a pre-0.2.1 daemon may hold duplicate `plot_points` rows for one (line, name)" and "`long` emits every stored row while `wide` collapses them". `/plot/series` is the long form, so a legacy capture answers exactly this shape.

Second, narrower route on a current capture: `seedPlotHistory` (`api.js:324`) only sets `port=` when `channel.port` is truthy. Two boards both declaring `temp` with a falsy `port` field give two unfiltered entries named `temp` over the same line ids, producing the same duplicate.

Failure scenario: the user reloads the page against a capture recorded by an older daemon. Every trace on the affected chart is drawn one sample out of position from the duplicate onward, silently, and stays wrong through every later live sample, every window change and every pause. No console error, no visual tell.

Probe `p1_seed_dup.mjs`:

```
xsHost.length = 3
ys(ax).length = 4
ys(ax)        = [1,2,2,3]
ALIGNED       = false
```

Note this is the same defect class a prior round already filed against the live path: `parsePlotAdhoc` (`plots.js:83`) and `parsePlotDef` (`plots.js:171`) both carry an explicit uniqueness gate with a comment naming this exact consequence. The seed path, which is the third producer into `addSample`, has none.

---

## MED

### M1. The JS enum-label parser accepts `-0` where `protocol.py` rejects it

CONFIRMED. `host/mcuscope/webui/plots.js:144` (`parseEnumLabels`).

Invariant broken: the JS decoder must accept exactly the definitions the daemon accepts (the stated rule this module already enforces for `*scale`, `!pd` name uniqueness, decimal digit counts and post-scale finiteness).

`protocol.py:643` rejects on the **sign character**: `if not signed and val_s.startswith("-")`, with the comment "monitor.c rejects any '-' on an unsigned channel". `plots.js:144` rejects on the **value**: `if (!signed && v < 0) return null;`. `Number("-0")` is `-0`, and `-0 < 0` is `false`, so the browser takes it.

Failure scenario: firmware emits `!pd 0 state:u1:=-0=IDLE,1=RUN`. The daemon stores the line as a generic event and caches no definition, so every following `!ps` for that sid is a generic event too, and `/plot/channels`, `/plot/export` and `mcu plot` show nothing. The browser meanwhile builds the enum lane and charts it live. The user sees a working panel that exists only in the tab, cannot export it, and loses it on reload. This is precisely the failure the `*scale` rejection at `plots.js:107-111` was added to prevent.

Probe `p4_negzero.mjs`:

```
JS accepted the definition: true
--- daemon side ---
daemon accepted the definition: False
daemon accepts 0=IDLE (control): True
```

The Python side has a dedicated regression test for this case (`tests/test_protocol.py:480-483`, with the comment "monitor.c rejects the '-' itself, not the value, so `-0` (which is 0) has to go too"). The JS mirror has no counterpart, which is how it drifted. See L6.

### M2. `markDigitalDirty` drops its own repaint request when the panel is hidden, leaving lanes drawn in the previous time base

CONFIRMED. `host/mcuscope/webui/digital.js:405-409`.

```js
function markDigitalDirty() {
  for (const l of digitalLanes.values()) l._sizedirty = true;
  redrawDigital();
  for (const l of digitalLanes.values()) l._sizedirty = false;
}
```

`redrawDigital` skips any lane whose `canvas.clientWidth <= 0` (`digital.js:308`), with the comment "panel hidden; leave the lane dirty for when it is shown". But the flag it set is `_sizedirty`, and line 408 clears it unconditionally whether or not the redraw happened. `lane.dirty` was never set, so nothing survives.

The analog charts are not affected: `redrawPlots` `continue`s before touching `chart.dirty`, so the sticky flag holds.

Failure scenario: the user switches the sidebar to the CAN view (plots section is `display:none`), changes the shared time base with the `#timeSeg` control (`terminal.js:546` calls `markDigitalDirty`), then switches back to Plots. `app.js setView` only schedules `resizePlots()`, which does not touch the lanes, and `redrawDigital`'s `sizeChanged` check is false because the sidebar width did not change. Every lane keeps the waveform it drew in the old time base. A live lane self-corrects within 200 ms (any ingested point sets `lane.dirty`), but a stopped or quiet stream stays wrong indefinitely, on the same x axis as charts that did repaint.

Probe `p2_digital_stale.mjs`:

```
baseline paints: 1  canvas.width now: 200
paints while hidden: 0 (expected 0)
paints after re-showing: 0
lane.dirty: false  lane._sizedirty: false
STALE (drawn in the old time base): true
```

### M3. The digital gutter readout reverts from the value under the cursor to the live value within one redraw tick

CONFIRMED. `host/mcuscope/webui/digital.js:309-311`, interacting with `plots.js:906-912` (`redrawTick`).

```js
if (lane.pendingVal !== undefined) {
  setLaneVal(lane, LANE_KINDS[lane.kind].fmt(lane, lane.pendingVal));
}
const sizeChanged = ...;
if (!lane.dirty && !lane._sizedirty && !sizeChanged) continue;
```

The `pendingVal` write sits **above** the dirty check, so it runs on every 200 ms tick for every visible lane, whether or not anything changed, and overwrites whatever `setDigitalCursorAt` wrote.

Invariant broken: SPEC 9.2 requires "a cursor value readout with unit"; while the pointer rests, the readout must show the value at the cursor, not at the live edge.

Failure scenario: the pointer rests on an analog chart or the digital panel at an earlier time and the stream goes quiet. On the next tick `redrawDigital` clobbers every readout back to `pendingVal`. `redrawTick` only re-applies the cursor when `plotsChanged || digitalChanged || hoverXVal() !== lastHoverX`, and on an idle tick with an unmoved pointer all three are false, so the clobbered value is what stays on screen. The waveform still shows the cursor line at the earlier time while the numbers beside it read the live edge.

Probe `p3_readout.mjs`:

```
readout at live edge : RUN
readout under cursor : IDLE
readout after a tick : RUN
CLOBBERED: true
```

---

## LOW

### L1. `loadCmdHistory` does not apply `CMD_HISTORY_MAX` on load

`cmdbar.js:17-22`. `saveCmdHistory` writes `slice(-100)` and `submitCmd` trims, but the load path pushes every string the stored array holds. A localStorage value written by an older build, or edited by hand, is held in full until the next submit. Bounded in practice by what was written; cosmetic memory only.

### L2. `portColorCache` grows without bound on wire-supplied aliases

`state.js:123-133`. Keyed by `row.port` for every distinct alias ever seen, never pruned, including across a capture-identity reset (`resetForDbReset` does not touch it). Bounded by the number of distinct aliases a capture carries, which is small in practice, but it is the one wire-keyed map in the UI with no cap, next to `canRows` (256), `plotChannelMeta` (64) and `digitalLanes` (64). It is a `Map`, so there is no prototype exposure.

### L3. A seed-path channel name reaches a DOM `id` without client-side validation

`digital.js:146-148`: `grp.id = "dgrp-" + ch.name`, with the matching `document.getElementById("dgrp-" + ch.name)` lookup. On the live path `ch.name` has passed `PLOT_NAME_RE` (`[A-Za-z_][A-Za-z0-9_.]*`, max 16). On the seed path `seedDef` (`plots.js:317-329`) takes `channel.group || channel.name` straight from the `/plot/channels` JSON and never re-tests it. Not an injection sink (the value is assigned to the `.id` property, and the group label goes through `textContent`), and the `dgrp-` prefix rules out collision with an `index.html` id, so the worst case is a group header that fails to dedupe. Filed because it is the only device-derived string in the UI reaching an attribute sink without a grammar check at its own boundary.

### L4. `charts` watermark would return `Infinity` for an all-null frozen set

`plots.js:892-895`: `Math.min(...frozen.filter((v) => v != null))` returns `Infinity` when the filter empties a non-empty list. Currently unreachable, because `setChartPaused` always assigns `chart.frozenMaxId = state.maxId` alongside `chart.paused = true`, so the two cannot disagree. The `panes` surface has the sibling shape without the filter (`terminal.js:236-239`) and returns `0` for a pane paused before any data, which flows into an export as `id_to=0`. Both are defensive code that is one refactor away from mattering; the `filter` here is the tell that somebody already doubted the invariant.

### L5. Freeze snapshots double peak heap at the documented caps

`plots.js:872-875` copies `xsHost`, `xsTick` and every `ys` array on pause; `digital.js:98-100` copies all three arrays for every lane. At the SPEC caps (64 analog channels and 64 lanes at `PLOT_CAP` 100k points) that is a full second copy of the ring set, held for as long as the surface stays paused. Both comments say "Bounded", which is true, but the bound is 2x the live rings rather than 1x. The design is right (an index into a rotating ring was REVIEW class 26); this is only worth knowing before someone raises `PLOT_CAP`.

### L6. Nothing pins the JS and Python plot-grammar mirrors to shared cases

`tests/csv_cell_cases.json` is the only cross-language fixture in the tree, and it covers `csvField` / `_csv_cell` only. The plot grammar is mirrored by hand across `protocol.py` and `plots.js` in at least seven places (value grammar, name grammar, enum labels, bit lanes, channel spec, def uniqueness, sample decode), each with a comment explaining a past drift. M1 is the next instance: the daemon has an explicit test for it, the browser has none. A shared JSON case list of `(!pd line, valid?)` and `(!p line, valid?)` driven from both `tests/test_protocol.py` and a `.test.mjs` would close the class rather than the instance.

---

## Manual-verify (not reachable through the stubbed DOM)

Listed, not guessed at, per the brief. `FakeEl.clientWidth` is 0 and `getBoundingClientRect()` returns all zeros, so nothing below is assertable in place.

- **The uPlot glue and the cursor pixel projection.** `applyHoverCursor` (`plots.js:829-851`) reads `u.scales.x`, `u.valToPos` and `u.over.clientHeight`; `setDigitalCursorAt` (`digital.js:487-491`) computes the gutter width as `$("digitalWrap").clientWidth - ref.canvas.clientWidth` and positions `#dCursor` from it. Whether the digital cursor and the analog cursors land on the same pixel column is only observable against a laid-out page. The projection arithmetic itself is in `timewindow.js` and is covered by `timewindow.test.mjs`.
- **`openColorPicker`'s cleanup on a cancelled picker.** `chrome.js:49-72` relies on `window.addEventListener("focus", drop)` firing when a dismissed native colour dialog returns focus. `dom_stub.mjs:249` stubs `globalThis.addEventListener` as a no-op, so neither the leak nor its fix can be driven under test. The code documents the Firefox and Chromium behaviours it was written against; re-checking it needs a real browser on both.

## Areas read and found sound

Recorded so a later round does not re-walk them. The WS reconnect and staging machinery (`api.js:503-680`): generation checks after every await, per-row staging re-check for a mid-frame capture token, segmented drain across tokens, stable-connection gate on the backoff reset, and the 1008 auth path sharing one prompt budget with the HTTP 401 path. The high-rate shed (`api.js:33-139`): timer-driven window, `pending` counted rather than queued while a pane is frozen, `pushBuffer`/CAN/plot ingest deliberately ahead of the shed so `setHighRate(false)` can rebuild from the buffer. Freeze/export watermarks: charts, panes and the digital panel all take `state.maxId` at the pause instant, which is exact because `plotIngest`/`canIngest`/`pushBuffer` all run ahead of the shed and staged rows have not advanced the watermark. XSS: every device-derived string reaches the DOM through `textContent`, a `title`/`aria-label` property or a `Map` key; the one `innerHTML` (`digital.js:154`) interpolates nothing device-derived; the one `href` (`statusbar.js:124`) is scheme-validated at the sink. Prototype pollution: `PLOT_TYPES` and the colour store are both null-prototyped, and every other wire-keyed collection is a `Map` or `Set`.
