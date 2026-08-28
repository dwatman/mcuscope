# Fix batch D: web UI JS (+ the shared plot-grammar fixture)

All 12 items done. No contradictions found between the triage rulings and the code; one ruling
was implemented at a slightly wider seam than named, flagged below (W-L3).

Files touched (only within the batch's allowance):
`host/mcuscope/webui/{plots,digital,freeze,state,api,cmdbar,settings,statusbar,terminal}.js`,
`host/tests/webui_js/{dom_stub.mjs,freeze,settings_pj,settings_ports_baud,statusbar_logic}.test.mjs`,
plus five new node tests and the three new fixture files.
`index.html` needed no change. `protocol.py`, `test_protocol.py` and `docs/` untouched.

## 1. W-H1 (HIGH) duplicate (line_id, name) in a seeded series

Which row wide keeps, verified first: `server.py:2094 _csv_wide` accumulates
`values[r["name"]] = r["value"]` per line_id, so the **last** row for a (line, name) wins.
`mergeSeedSeries` now accumulates each row's points in a `Map` keyed by channel name and
materialises it to an array at the end, which is the same rule. Browser and `--wide` CSV
therefore show the same value for a legacy capture, and `ys(name).length` stays equal to
`xsHost.length`.

Test: `tests/webui_js/plots_seed_grammar.test.mjs`, cases 1-2 (built from probe `p1_seed_dup.mjs`;
case 2 adds a clean sibling channel on the same lines, which the probe did not cover).

## 2. W-M1 (MED) `parseEnumLabels` accepted `-0` on an unsigned channel

Now refuses on the sign character (`valStr.startsWith("-")`) before converting, exactly as
`protocol.py:643` does. Covered as a fixture case rather than a standalone test, since item 11
asked for it there and this is the instance the class exists for: the fixture holds both
`!pd 0 st:u1:=-0=IDLE,1=RUN` (invalid) and `!pd 0 st:s1:=-0=IDLE,1=RUN` (valid), mirroring
`tests/test_protocol.py:480`.

## 3. W-M2 (MED) `markDigitalDirty` dropped the repaint request for a hidden panel

`redrawDigital` now owns the clear: it sets `lane._sizedirty = false` only on the lanes it
actually painted, and `markDigitalDirty` no longer clears anything. A lane with
`clientWidth <= 0` keeps its request until it is shown.

Test: `digital_repaint.test.mjs` case 1, built from probe `p2_digital_stale.mjs`, asserting the
repaint count and the surviving flag at each step.

## 4. W-M3 (MED) idle tick clobbered the cursor readout

Two parts:
- `redrawDigital` computes `repaint` (dirty | _sizedirty | sizeChanged) once, and writes
  `pendingVal` only when `repaint || !cursorReadout`.
- a module-level `cursorReadout` flag: set by `setDigitalCursorAt` where it writes the
  readouts, cleared by `refreshDigitalReadouts` (the mouseleave / `clearHoverCursor` path).

I used a flag rather than reading `$("dCursor").hidden` because `setDigitalCursorAt` writes the
readouts on a path that then hides the cursor (no visible lane), so the DOM state and the
readout state are not the same thing.

Test: `digital_repaint.test.mjs` cases 2-4, from probe `p3_readout.mjs`. Case 3 drives the
opposite direction (a lane that does repaint must still take the live value) with the cursor
parked on a *different* value, so it cannot pass by coincidence; case 4 drives the return to
the live edge.

## 5. W-L1 `loadCmdHistory` applies `CMD_HISTORY_MAX`

`.slice(-CMD_HISTORY_MAX)` on the loaded array. Test: `cmdbar_bounds.test.mjs` case 1 (150
stored entries, ArrowUp walked to exhaustion, exactly 100 distinct, oldest reachable `cmd50`).

## 6. W-L2 `portColorCache` cleared on reset and capped

`state.js` gains `PORT_COLOR_MAX = 64` (past the cap the colour is still returned, just
recomputed: it is a memo, not state) and an exported `clearPortColors()`, called from
`api.js resetForDbReset` beside `clearAllCan/Charts/Digital`.

**No test.** Neither half is observable: `portColor` is a pure function of the alias, so the cap
and the clear change memory only, and exporting the cache to assert its `size` would be
test-only surface with no behaviour behind it. Recorded here rather than tested.

## 7. W-L3 seed-path names re-tested against `PLOT_NAME_RE`

Implemented one level up from where the ruling names it. New `seedNameOk(channel)` (channel
name always, plus `channel.group` when `kind === "bit"`), applied in `plotSeed`'s entry filter
beside the other malformed-row drops, rather than inside `seedDef`. `seedDef` is only one of
three consumers of the same entry (`mergeSeedSeries` and `seedTargetHasData` are the others),
so gating at the entry covers all of them with one check instead of one per consumer. The
behaviour asked for is unchanged: a failing name is dropped like any other malformed seed row.

Test: `plots_seed_grammar.test.mjs` cases 3-5 (bad channel name, bad bit-lane group, and a
valid row that must still seed).

## 8. W-L4 watermark shape

New `minWatermark(ids)` in `freeze.js`, used by both `charts` (plots.js) and `panes`
(terminal.js): `[]` -> null (nothing frozen, the surface is live); all-null -> 0 (frozen before
it held a row, so the export covers nothing); otherwise the min of the known ids. No `Infinity`
path on either surface.

Test: appended to `freeze.test.mjs`, driving all five shapes including `[null]` and `[null, 5]`.

## 9. RG-F18..F20 missing client-side upper bounds

The three bounds now live once, in `state.js` (the shared leaf both dialogs already import),
named after the server constants they mirror: `MAX_BAUD` (1e8), `MAX_TIMEOUT_MS` (300000),
`MAX_DB_BYTES` (2**42). Call sites:

- `settings.js:383` `collectPorts`: `baud > MAX_BAUD` refused, message now `baud must be 1-100000000`.
- `statusbar.js:413` `submitAttach`: same bound, message `baud must be 1-100000000`.
- `settings.js:423` storage cap: `capMb > MAX_DB_BYTES / MB` refused, message `size cap must be 0-4194304 MB` (the sibling fields already named their full range).
- `cmdbar.js:115` timeout: over-bound falls back to 1000 and shows it in the field, as a zero or a blank already did. This one also disarms the `AbortSignal.timeout(timeout + 5000)` overrun that V10 called out.

Tests: `settings_ports_baud.test.mjs` (+2 cases), `statusbar_logic.test.mjs` (+1),
`settings_storage_cap.test.mjs` (new), `cmdbar_bounds.test.mjs` cases 2-3. Each pins the
inclusive bound as well as the refusal.

## 10. TQ-F3 `settings_pj.test.mjs`

The stub gained `echoAs`: the runtime state the next PUT answers with when it differs from what
was sent. The rewritten test types `enabled = true` with a **blank** dest and the stub answers
`{enabled: false, dest: "127.0.0.1:9870"}`, so both assertions can only be reading the daemon's
reply. It also asserts the request body is `{enabled: true, dest: null}`, driving the
`dest || null` path. A second test keeps the typed-dest case.

Revert-verified against both named mutations: N4 (delete the two echo lines) and N5
(`dest: dest` instead of `dest: dest || null`) are now both KILLED by case 2.

## 11. Class-19 close (W-L6): shared plot-grammar fixture

- `host/tests/plot_grammar_cases.json` (new): 32 `!pd` cases, 14 `!p` cases, 22 `!ps` cases,
  each with a `why`. Includes every case the brief listed: `-0` enum label on unsigned
  (invalid, with the signed counterpart valid as contrast), duplicate channel names,
  channel/lane name collision, lane/lane collision, `*scale` on enum and on bits, the three
  non-finite f4 bit patterns 7F800000 / FF800000 / 7FC00000, post-scale overflow
  (`v:f4*1e300` against 7F7FFFFF), plus a spread of valid cases and the ordinary grammar
  refusals (types, name grammar, digit caps, token counts, tick range, sid).
- `host/tests/test_plot_grammar_fixture.py` (new): parametrised over every case against
  `mcuscope.protocol`, plus a populated-file guard that also asserts each section exercises
  both answers (a parser that refuses everything must not pass).
- `host/tests/webui_js/plot_grammar.test.mjs` (new): the same file against
  `plots.js parsePlotDef / parsePlotAdhoc / decodePlotSample`.

`plots.js` now exports those three parsers (nothing else in the app calls them from outside the
module); that is the mirror boundary the fixture pins, and driving it through `plotIngest`
instead could not tell "invalid definition" from "valid definition, nothing charted yet".

**Python side run last, as briefed: 68 passed, nothing pending.** By the time I ran it,
batch E's work had landed in `protocol.py` (`_decode_field` line 769 non-finite -> None, and the
post-scale re-check at line 811), so every case agrees on both sides with no exception to
record.

## 12. Manual-verify items, unchanged and still open

- **Cursor pixel alignment.** `applyHoverCursor` (`plots.js`) reads `u.scales.x`, `u.valToPos`,
  `u.over.clientHeight`; `setDigitalCursorAt` (`digital.js`) derives the gutter width from
  `$("digitalWrap").clientWidth - ref.canvas.clientWidth`. Whether the digital cursor and the
  analog cursors land on the same pixel column is only observable against a laid-out page.
  The projection arithmetic itself is in `timewindow.js` and is covered by `timewindow.test.mjs`.
- **`openColorPicker` focus cleanup.** `chrome.js:49-72` relies on a `window` `focus` event
  firing when a dismissed native colour dialog returns focus; `dom_stub.mjs` stubs
  `globalThis.addEventListener` as a no-op, so neither the leak nor its fix is drivable.
  Needs a real browser, on Firefox and Chromium both.

## Revert-verification

Each behaviour fix was reverted in place, the owning node test run, and the source restored.
13 of 13 killed, no survivors:

```
KILLED  | W-H1 seed dedupe            -> plots_seed_grammar   | not ok 1 - a duplicate (line_id, name) keeps the y array aligned...
KILLED  | W-L3 seed name gate         -> plots_seed_grammar   | not ok 3 - a seed channel name that fails PLOT_NAME_RE is dropped...
KILLED  | W-M1 enum sign char         -> plot_grammar         | not ok 1 - !pd definitions
KILLED  | W-M2 sizedirty clear        -> digital_repaint      | not ok 1 - a repaint requested while the panel is hidden survives...
KILLED  | W-M3 pendingVal clobber     -> digital_repaint      | not ok 2 - an idle tick does not clobber the cursor readout...
KILLED  | W-L1 history cap on load    -> cmdbar_bounds        | not ok 1 - a stored history longer than the cap is trimmed on load...
KILLED  | RG-F20 timeout upper        -> cmdbar_bounds        | not ok 2 - a timeout above the daemon's bound falls back...
KILLED  | W-L4 minWatermark           -> freeze               | not ok 10 - minWatermark answers a line id or null, never Infinity
KILLED  | RG-F18 settings baud upper  -> settings_ports_baud  | not ok 6 - a baud above the daemon's bound is refused here...
KILLED  | RG-F18b statusbar baud      -> statusbar_logic      | not ok 15 - the attach dialog refuses a baud above the daemon's bound
KILLED  | RG-F19 size cap upper       -> settings_storage_cap | not ok 2 - a size cap above the daemon's bound is refused by name...
KILLED  | TQ-F3 N4 echo lines         -> settings_pj          | not ok 2 - a change applies live, and both fields show the daemon's answer
KILLED  | TQ-F3 N5 blank dest as ""   -> settings_pj          | not ok 2 - a change applies live, and both fields show the daemon's answer
```

Script: `/tmp/rv.py`. W-L2 is the only item with no test, for the reason given above.

## Gates run

- `uv run python -m pytest tests/test_webui_js.py` -> 1 passed (34 `.test.mjs` files, 5 new).
- `uv run python -m pytest tests/test_plot_grammar_fixture.py` -> 68 passed.
- `uv run python -m ruff check mcuscope/ tests/` -> one I001 in `tests/test_sim.py:690`, which is
  batch E's file, not mine. Nothing in this batch's files.
- No em dashes or en dashes in any file touched (checked with a codepoint grep).

## Notes for the round close

- `dom_stub.mjs` gained a no-op `setSelectionRange()` on `FakeEl`; the cmdbar history walk calls
  it and the stub had no such method.
- `plots.js` newly exports `parsePlotDef`, `parsePlotAdhoc`, `decodePlotSample` for the fixture.
  That is test-driven surface: worth it, since it is the exact seam the class-19 fixture pins,
  but worth knowing it exists for no other caller.
- `state.js` is now where the three daemon-mirrored UI bounds live. Anyone changing
  `server.MAX_BAUD` / `MAX_TIMEOUT_MS` / `ConfigStorageBody.max_db_bytes` has three constants to
  follow, in one file.
