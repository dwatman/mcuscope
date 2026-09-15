# Sweep: every webui module must load when imported first

Base `5489d6e` plus the in-flight fix batches. From the PD-5 follow-up (`fix-fixdiff2-panes.md`, question 2): the webui's hand-wired import cycles (`app.js:23`) are only ever evaluated in the order `app.js` happens to reach them, and nothing tested that they stay broken.

Added: `host/tests/webui_js/module_load_order.test.mjs`. It reads `webui/` with `readdirSync`, keeps `*.js` (18 modules today, asserted `>= 10` and asserted to contain the four named in the cycle comment), and spawns one fresh `node --input-type=module -e` per module that calls `installDom()` and then imports that module and nothing else. One `test()` per module, so the failing one is named. A fresh process per module is the point: module evaluation happens once per process, so importing a module a previous test already pulled in as a dependency proves nothing.

## Modules that failed alone

None. 20/20 pass: 18 modules, the list-plausibility check, and the harness control.

The sweep is therefore a pin, not a fix: nothing to repair, so no `webui/*.js` file was changed and no Python was touched (`ruff` not run, nothing for it to read).

## That the probe can fail

A green sweep over a green tree says nothing on its own, so the harness carries a control and the known defect was re-driven.

- Harness control, in the file: importing a module that does not exist must exit non-zero with `ERR_MODULE_NOT_FOUND`. Without it, a spawn that silently never ran reads as 18 passes.
- The PD-5 cycle, re-driven: `digital.js:9` given back the static import the brief originally asked for (`import { plotSeedGen } from "./plots.js"`). Copy first to `~/tt-data/prerelease-2026-09-15/module-load-order/digital.js`, restored from that copy afterwards, no git.
  - Result: 4 of 20 fail, all with `ReferenceError: Cannot access 'lanesChanged' before initialization` - `digital.js`, and also `api.js`, `can.js` and `settings.js`, which reach the pair from above. `plots.js` and `app.js` still pass, which is exactly why no existing test could see it.
  - Restored, 20/20 pass again.

Each test also passes run alone under `--test-name-pattern` (`digital.js loads when imported first`, the control, the list check).

## Suite

`uv run python -m pytest tests/test_webui_js.py -q`: 2 passed. The 4 chrome-batch failures noted in `fix-fixdiff2-panes.md` are gone; the whole JS suite is green.

## CHANGELOG

No line. Nothing user-visible changed: `index.html:375` loads `app.js` and only `app.js`, so the browser has exactly one evaluation order and it was already working. The test file is the whole of the change.

## The two questions

1. **Least confident, rechecked.** That "no module fails alone" means the cycles are safe, rather than the probe being blind. Re-driven rather than reasoned: reintroducing the exact static import that crashed in the PD-5 batch turns 4 of the 20 tests red with the reported message, and removing it turns them green. What stays reasoned is narrower and stated here rather than counted as done: the probe evaluates each module under `dom_stub.mjs`, which returns a detached element where a real `document.querySelector` returns `null` (`dom_stub.mjs:10`), so a module that loads here could still throw at module scope in a browser. Load *order* is what this pins; load under real DOM semantics is not, and belongs to the browser checklist.

2. **What should have been checked.** Two gaps, both outside this sweep's reach and neither reachable today.
   - Only the *first* module is varied. Each root induces one evaluation order over its own subtree, and 18 roots cover every cycle reachable from any of them, but not an arbitrary pairwise order imposed from outside - a test file that imports two modules in an order neither root produces. That is a test-only shape; a future second `<script type="module">` in `index.html` would make it a real one, and there is nothing that would notice.
   - `vendor/` is excluded by the `*.js` filter over `webui/` (uPlot lives one directory down and is a classic `<script>`, not a module). Nothing in the sweep says the modules that lean on the `uPlot` global behave when it is absent, which is what a broken vendor path would produce.
