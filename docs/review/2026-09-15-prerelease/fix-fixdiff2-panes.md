# Fix batch: fix-diff 2, web UI panes (PD-4, PD-5, PD-7)

Base `5489d6e`. Files touched: `webui/pane.js`, `webui/digital.js`, `webui/plots.js`, `tests/webui_js/rulings_panes_export.test.mjs`, `tests/webui_js/export_paused_window.test.mjs`, two new `tests/webui_js/fixdiff2_panes_*.test.mjs`, `manual-verify.md`.
Copies for the revert legs: `~/tt-data/prerelease-2026-09-15/fix-fixdiff2-panes/`.

## PD-4: a history divider that counts nothing

- Changed: `pane.js:151`, `planHistoryPage` prepends the divider only when `rows[0].id - 1 > floor`.
- Test: `tests/webui_js/fixdiff2_panes_history.test.mjs`, 3 tests.
  - The budget spent on the first row past the clear point: a complete page, no `gap` row.
  - One row higher (the positive control): the divider is there, `gap: 1 lines not loaded`, id just below the page.
  - An uncleared pane (`floor` 0) paged to capture id 1: no divider either, which is the reachable case, since it needs no clear.
- Revert (`rows[0].id - 1 > floor` removed): tests 1 and 3 fail, the control passes. Restored, 3 pass.

## PD-5: a digital-only clear did not move the seed generation

- Changed: `plots.js:1325` `bumpSeedGen()` owns the `seedGen++` that `clearAllCharts` did inline (`plots.js:1330`); `plots.js:460` registers it with `onSeedBump(bumpSeedGen)` beside the existing `onLanesChanged` wiring; `digital.js:28,53` hold the callback and its setter; `digital.js:819` `clearAllDigital` bumps it.
- **Deviation from the brief, deliberate:** the brief said export the bump from `plots.js` and import it into `digital.js`. Driven, that static import is a crash, not a style question: `plots.js` calls `onLanesChanged(...)` at its top level (`plots.js:459`), so whichever of the pair evaluates first reads a `let` of the other still in its TDZ. With the import in place, `node --test` on a file importing `digital.js` first died at load with `ReferenceError: Cannot access 'lanesChanged' before initialization`. In the app today `api.js` happens to pull `plots.js` first, so the page would have survived by accident. The registration callback is the codebase's own cycle-breaker for this exact pair (`app.js:23`, `onLanesChanged`), and it keeps the token in `plots.js` as briefed.
- Test: `tests/webui_js/fixdiff2_panes_clear.test.mjs`, 3 tests, importing `digital.js` before `plots.js` so the cycle fails the file if it is reintroduced.
  - Ingest alone leaves `plotSeedGen()` unchanged (the control), `clearAllDigital()` alone moves it.
  - Two clears in a row move it twice, so they cannot alias inside one backfill.
  - `clearAllCharts()` still moves it on its own.
- Reverts, both branches: `bumpPlotSeed()` dropped from `clearAllDigital` gives 2 failures, the `clearAllCharts` control passing; `onSeedBump(bumpSeedGen)` dropped from `plots.js` gives the same 2. Restored, 3 pass.

## PD-7: two `deepEqual(seen.refusals, [])` with no positive control

- `rulings_panes_export.test.mjs:97`: a chart export with `changes` ticked and deadband `nosuch=0.5`, a channel the export does not carry. The refusal is recorded with the daemon's own words, `expErr` reads `plot export failed: deadband names no exported channel: nosuch=0.5`, and the dialog stays open. The list is cleared afterwards, so the assertions further down are unchanged. An `opt()` helper (the same one `exportdlg.test.mjs` uses) was added for the option fields.
- `export_paused_window.test.mjs:214`: a pane whose over-long pattern was not dropped (`regexSrc` 201 chars with a compiled `regex`, the state the drop guard exists to prevent) exports, `match` goes out, and `match regex too long (max 200 chars)` is recorded. This is the mirror of the test above it, which asserts `match` is *not* sent for that source.
- Both are reachable paths through the real dialog, not a direct call into the double.
- Revert: `exportdlg_guards.mjs` is not mine to edit, so the controls were falsified from my own side instead. Deadband changed to a real channel (`v=0.5`): the control fails, "the guard the assertions above rely on never fired". Pane `regex` set back to `null` so `match` is not sent: the control fails. Both restored and passing.
- Each of the two files, and each new test run alone with `--test-name-pattern`, passes.

## Suite

`node --test` on each of my files: green. Full JS suite (`uv run python -m pytest tests/test_webui_js.py -q`): 798/802, with `test_export_guard_double_agrees_with_the_daemon` passing.
The 4 failures are `sweep_chrome_settings.test.mjs` (3, E-8) and `rulings_chrome_settings.test.mjs` (1), against the chrome batch's in-flight `settings.js`. No file of mine is involved; noted and left.

## CHANGELOG lines

```
- Web UI: a pane scrolled to the top until its 5000-row budget ran out could show a `gap: 0 lines not loaded` divider when there was nothing older to load; the divider is now shown only when lines were left behind.
```

PD-5 gets no line: no call site clears the digital panel without clearing the charts beside it, so nothing user-visible changes today. PD-7 is test-only.

## Not done

- Nothing dropped from the three items.
- `manual-verify.md`: both lists appended under "Fix-diff 2 additions (2026-09-15)". One chrome item was dropped as already covered: "a second click on the gear or `+ Attach` is unreachable behind the backdrop" is what the existing E-5 line checks. All 7 panes items kept; the tick-reset cursor item is distinct from the existing "Tick reset" line, which covers the break and the hover but not a cursor parked on the gap point.
- No file outside my list was needed. `dom_stub.mjs` untouched.

## The two questions

1. **Least confident, rechecked.** That PD-4's guard is the whole of the off-by-one, rather than `floor` being the wrong quantity in the first place. `loadHistoryPage` passes `floor: pane.clearId` (`terminal.js:520`) while `historyIdTo` computes its own `floor = clearId + 1`, two different numbers under one name, so the count `rows[0].id - 1 - floor` is only right because `since_id = clearId` makes `clearId + 1` the smallest servable id. I re-drove it both ways rather than reading it: with `floor` 1000 and a page starting at 1001 the count is 0 (fixed: no divider), at 1002 it is 1 (one line, id 1001, left behind), which matches the capture. The uncleared default `floor = 0` with a page starting at id 1 is the same arithmetic and is now covered by its own test. Still reasoned rather than driven: that no *other* caller of `planHistoryPage` passes a `floor` with the other meaning. `terminal.js:519` is the only one (grep over `webui/`).
2. **What should have been checked.** PD-5 was filed as "the lanes are covered only through `plotSeedGen`", and the fix was read as a one-line bump. What that framing hid is the module graph: `digital.js` had no edge to `plots.js` for a reason, and the obvious fix introduces a load-order crash that no existing test would have caught, because every test file that imports both happens to import `plots.js` first. The general question is which other pair of webui modules is one static import away from the same thing. `app.js:23` names two cycles (plots<->digital, \*->terminal) that are broken by hand wiring; nothing tests that they stay broken. My new test pins one direction of one of them. A cheap sweep for the round: for each webui module, import it alone in a fresh process, first, and assert it loads. `terminal.js`, `can.js` and `exportdlg.js` are the candidates with the most inbound edges.
