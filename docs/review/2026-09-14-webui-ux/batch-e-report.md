# Batch E report: registry and manual-verify close-out

HEAD: d6c14d70fa0245049f0e7f30419d5b4e3051d9c6 (uncommitted working tree on top).

## Decisions per candidate

- CAN highlight diffed against the last render (S-F4): new class 56, since a sweep over change baselines can find a sibling (it did).
- Charts and lanes keyed by stream id or name alone (S-F6): new class 57, a sweep over name-keyed stores (it found one in the CLI).
- Tooltip naming the ignored `server.token` (D-F1): new class 58, help tokens can be diffed mechanically against what the code reads.
- `mcuscoped --config <missing>` silent on defaults: existing class 12 (healthy-while-dead), a setting that silently did not apply.
- Bundle test asserting `"1.0" not in` a row with a ts column: existing class 21, the absence-of-a-string trap over a wall-clock value.
- `.plot-head { display: flex }` beating `hidden` (L-16): new class 59, every hidden-toggled element is sweepable.
- Pydantic `min_length` before the handler strip: new class 60, every constrained field with a later transform is sweepable.
- `refreshConfig` returning the stale config: existing class 12, Settings read as live while the daemon was dead.
- Export option change calling `render()`: new class 61, every `.value =` writer and its callers are sweepable (it found a sibling).
- Digital lanes back-filling to the left edge: not a class; the two draw sites share `laneSegments` and no other surface extrapolates.
- Select width from its widest option: not a class; a layout preference with no invariant a sweep could check.

## Sweeps

### Class 56 (change baselines)

- 150 grep lines; most are `preventDefault`, `preview`, `changes=` plumbing and prose. The baselines among them:
  - can.js `e.moved`: set per frame at ingest, consumed per paint. Complies (the S-F4 fix).
  - terminal delta (`fmtTs` prev): previous displayed row, as SPEC 9.1 defines it. Complies.
  - statusbar `prevRx`: a per-poll rate by definition. Complies.
  - terminal `knownAliases` prev and the tick-anchor thinning prev: not change markers. Exempt.
  - server `_changes_long` / `_csv_wide`: per row within one request. Complies (port keying: see owner decisions).
  - CLI `_decode_pages` across pages: one decoder shared by every page. Complies.
  - CLI `mcu tail -f --changes`: the snapshot decoded with its own decoder, so the follow reprinted each stream's last sample. **Violates, fixed.**
- Fix: `LineDecoder.share_changes`; `_decode_pages(..., baseline=dec)` from `_tail_snapshot` (cli.py, cli_output.py).
- Test: `test_timeline.py::test_tail_snapshot_hands_its_changes_baseline_to_the_follow`; mutation (no-op share) fails it.

### Class 57 (per-board keys)

- JS: 49 `new Map` / `Set` / `Object.create(null)` sites. Charts, lanes, groups, seeds, CAN rows, CAN groups, tick anchors carry the port.
  - Exempt: `paletteSlots` and the colour store (owner decision), and stores keyed by section, option name, element or alias.
- Python: 10 dicts keyed by sid, name or key. serial_link has one `PlotDecoder` per port; pjstream nests under the alias. Comply.
  - CLI `LineDecoder`: one `!pd` cache and one `--changes` baseline for every port, so `mcu lines --decode` without `-p` rendered board B with board A's definition. **Violates, fixed.**
  - server `/plot/export` without `port=`: decoder and changes baseline keyed by sid/name. Owner decision below.
- Fix: `LineDecoder` keeps a decoder and baseline per port; every caller passes `row["port"]` (cli.py, cli_output.py).
- Test: `test_timeline.py::test_line_decoder_keeps_two_boards_streams_apart`; mutations (port ignored in the cache, in the baseline) each fail it.

### Class 58 (help naming what nothing reads)

- Config keys named in `host/mcuscope`, README and docs: 11 distinct, all in `_KNOWN_KEYS`; the other `x.y` hits are runtime attributes (`ports.get`).
- Env vars named: 6, all read (`MCUSCOPE_UPDATE_CHECK` via `update_check.ENV_ENABLE`).
- `mcuscoped` / `mcu-sim` flags named: 12, all declared. `mcu` commands named in the web UI: 5, all exist.
- One hit: state.js section comment "optional server.token". **Fixed** (comment only, no test).

### Class 59 (display beating hidden)

- 49 JS `hidden` toggles plus 11 static `hidden` in index.html, over about 25 elements.
- Every element with a display rule already had a per-selector `[hidden]` patch (14 lines); the rest have no display rule. No live instance.
- Fix: one `[hidden] { display: none !important; }` in style.css, the 14 patches deleted. No `display: <not none> !important` exists.
- Inline `style.display` toggles (`devCustom`, `bindRow`, `baudCustom`) are never also toggled with `hidden`. Exempt.
- Test: `plots_chrome.test.mjs` pins the global rule, no per-selector patch, no loud display; it fails against HEAD's style.css.
  - The patch assertion in `can_head.test.mjs` was removed as superseded.

### Class 60 (constraint before normalisation)

- 6 constrained string fields in server.py; no `Query(min_length=)` anywhere.
  - `SessionBody.name`: the batch D validator. Complies.
  - `ConfigServerBody.host`: re-checks empty after strip. Complies.
  - `ConfigPlotJugglerBody.dest`, `PlotJugglerBody.dest`: `parse_dest` strips and refuses empty. Complies.
  - `MarkerBody.text`: not transformed. Complies.
  - `ConfigStorageBody.db_path` (unconstrained, empty allowed) and port `device`/`serial_number` (re-checked after strip). Comply.
- Web UI: session name, alias, device and bind host each trim before the required check. Comply.

### Class 61 (re-render over typed input)

- 52 `.value =` writers. Settings renders run on open and after that section's own save; attach and session on open; alias only until typed.
- `setPaneRegex` from a CAN id click is the action the user asked for. Exempt.
- exportdlg.js range radios: `setMode` called `render()`, which rewrote typed clock bounds. **Violates, fixed**: mode changes call `paint()`.
- Test: `exportdlg.test.mjs` "switching the range choice away and back keeps clock bounds"; fails before the fix.

### Class 12 (extended)

- Web UI: 31 `catch` sites near an `await api(`. `refreshConfig` was the one returning stale data (batch D fixed it).
  - `loadDevices` clears, the export session list falls back to "whole capture" and the export itself fails loudly. Comply.
- Named config files: `mcuscoped` prints the notice (batch A), but `mcu daemon start -c typo.toml` discards the child's stdout. **Violates, fixed.**
- Fix: `daemon_start` warns on stderr when `--config` or `MCUSCOPED_CONFIG` names a missing file (cli.py); SPEC 3.3 and the ai-guide line say so.
- Test: `test_daemon_startup.py::test_daemon_start_warns_that_the_named_config_is_missing` (option, env, present); fails before the fix.

### Class 21 (extended)

- 120 `not in` assertions in host/tests; 38 with a needle that is not a letter-bearing literal.
- None has a digits-and-punctuation needle over text with a clock value, apart from the bundle site fixed in a813ef1.
  - `"0.14090123772621155" not in out` (test_cli_r2026_09_12.py) runs over canned channels whose `last_ts` is None. Complies.

### Nits fixed on the way

- digital.js ingest comment still said the draw extends the first segment to the left edge (stale since the owner fix).
- `theme_a11y_static.test.mjs` title claimed "nothing asks through window.prompt"; the token prompt in state.js does, by design. Title narrowed.

## Registry changes

- docs/REVIEW.md: classes 56-61 added; class 12 gains two instances and two sweep lines; class 21 gains the absence-over-clock bullet.
- CHANGELOG.md: four Fixed lines (CLI decode per port, tail baseline, daemon start warning, export range switch).

## Owner decisions needed

- `/plot/export` without `port=` on a capture where two boards declare one sid: decode primes one `PlotDecoder` over every port, and `changes` keys by sid and name.
  - SPEC 9.2 already merges names without `port=`, but labels can come from the other board's definition. Refuse `decode` without `port=` when names span ports, key per port, or document?
- CLI priming reads the newest 40 `!pd` rows across all ports; with many boards one port's definitions can fall outside them until its next rebroadcast.

## Manual verify

- docs/review/2026-09-14-webui-ux/manual-verify.md: 62 checks in 11 groups, merged from the five reports.
- Dropped as moot: the uPlot legend space, the 4 + 4 only CAN wrap, `auto (sim)` label, Digital head clipping and live pause (fixed in C).
- Batch E added no browser check: its web UI fixes are pinned by node tests.

## Gates

- `uv run python -m ruff check .`: all checks passed.
- `node --test tests/webui_js/*.mjs`: 579 tests, 579 pass, 0 fail.
- pytest over the touched and dependent files: 256 passed.
  - `test_timeline`, `test_daemon_startup`, `test_cli_export`, `test_cli_r2026_09_12`, `test_port_health`.
  - `test_regressions`, `test_cli_contract`, `test_webui_js`, `test_webui`.
- Full suite not run (reserved). No daemon or browser was started.
