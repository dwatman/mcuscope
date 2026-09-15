# Fix-diff review: clear during a backfill, session export hold

Scope: `webui/api.js`, `settings.js`, `state.js` and `clear_staged_backfill.test.mjs`, `settings_export_hold.test.mjs`, `rulings_chrome_settings.test.mjs` (uncommitted tree, 2026-09-16).
Probes ran from a harness copy outside the repo (`~/tt-data/fd-clear/probe.test.mjs`); every hand-revert was restored from a copy and checked with `cmp`.

## MEDIUM

### M1. A staging area discarded before its drain re-delivers the rows a clear covered (class 80)
- `api.js:662` (`onclose` sets `staging = null`) and `api.js:628` (`armStaging` on a superseding `connectWs`).
  The cut lives only in the discarded staging area.
  The next backfill fetches those rows again, and its `clearTokens()` snapshot (`api.js:505`) is taken after the click, so nothing covers them.
- Scenario, driven (probe Q1): reconnect backfill answers row 5; rows 6 and 7 are staged; clear-all; the socket closes before the drain; the next connect shows `[6,7,8,9]` where the ruling wants `[8,9]`.
  Same through `reconnectStream()` (a token save) mid staging (probe P2): `[5,6,7,8]` back after a clear-all.
- Fix: when a staging area is dropped, run `noteClears` on it and turn each cut into an id floor (the id of the last covered staged row).
  Raise the pane `clearId`s to it at once, and keep module-level CAN and chart floors that the next backfill's row loop reads as `row.id <= floor`.

### M2. Staging overflow drops the newest rows, capture tokens included (pre-existing, left by this fix)
- `api.js:673-677`: a full staging area discards each arriving row, whatever it is.
  - A capture token that arrives while it is full is lost for good (the daemon never repeats it).
    Every new-capture row then reads as a duplicate of the old watermark until a reconnect.
    Probe R1 drove it: 5000 staged rows, then `{capture: new}` and new rows 1 to 3; afterwards the buffer holds 0 new-capture rows, `maxId` 5004.
  - With a clear: staging keeps the 5000 pre-click rows, all now hidden, and drops the post-click rows the ruling says still show.
    CAN and charts stay live under `highRate`, so they lose those frames and samples silently.
- The comment at `api.js:670-672` ("anything past BUFFER_MAX is what pushBuffer would evict anyway") is untrue: `pushBuffer` evicts the oldest rows, staging drops the newest.
- Fix: never drop a capture token.
  On overflow, shift the oldest row and count arrivals in an absolute counter (`st.base`), so the `cut` indices stay valid.

## LOW

### L1. The `feedStaged` id guard has no test (revert-verify)
- `api.js:747`: `if (row && typeof row.id === "number")` reverted to `if (row)`: all 13 tests and the probes still pass.
- Without the guard, a `{gap}` notice or a capture token staged before a pane clear sets `clearId = Math.max(n, undefined)`, which is NaN.
  `rebuild` (`terminal.js:361`, `row.id > NaN`) then shows nothing for the life of the pane.
  `{gap}` notices come from a lagging subscriber, the same conditions that make a backfill slow.
- Fix: a test that stages `{gap: 3}` before one pane's clear and asserts that pane's later rows show after a rebuild.

### L2. Relative time re-zeroes on a covered row, not at the click (pre-existing, same in the backfill)
- `state.js:261` via `api.js:112` and `api.js:552`: clear-all sets `state.anchorTs = null`, and the next `pushBuffer` re-anchors it on whatever row comes next, covered or not.
- Scenario: a first connect whose 200-row backfill spans 10 minutes of a slow board; clear-all during it.
  Relative zero lands on the oldest backfill row, so the first post-click line reads about +600 s.
  Probe P3: anchor 1000.001 (row 1, covered), first shown row 1000.009.
- Fix: while `chartsCleared` (the clear-all token) covers a row, save `anchorTs`/`anchorTick` before its `pushBuffer` and restore them after.

### L3. SPEC 9.1 "rows arriving after the click still show" overclaims (`SPEC.md:1525`, `api.js:500-504`)
- Request mode, the author's residual 1: rows captured between the click and the moment a delayed `/lines` reaches the daemon come back in that backfill and are hidden.
  The same rows also arrive live on `/ws` after the click and are deduped away.
  So live rows arriving after the click stay hidden, against the new clause.
- Fix: narrow the clause to "live rows arriving after the click still show, unless the backfill also delivered them", or have the owner rule on the gap.

### L4. Session export hold measured on the wall clock (`settings.js:333`, `settings.js:380`)
- A re-rendered row computes its hold from `Date.now()`.
  If NTP steps the clock back an hour during a hold, a row rendered afterwards stays held for an hour (its `setTimeout` gets 3.6e6 ms).
  A forward step frees it early.
  The first row's own timer is monotonic, so only re-rendered rows are affected.
- Fix: `performance.now()` in both places; the test's clock fake patches `performance.now` instead of `Date.now`.

### L5. A re-render during the preflight still hands back a live button (class 73, author-acknowledged)
- `settings.js:377-382`: `heldUntil` is set only after `downloadPath` resolves.
  A Settings reopen, or a delete of another run, while the preflight (at most 2 s) is out builds a fresh live button.
  The old click's `finally` then holds only the detached one, so a second click during the build downloads twice.
- Fix: when `nav`, set a provisional `heldUntil` (`now + STATUS_TIMEOUT_MS + EXPORT_HOLD_MS`) before the await; after it, replace it with the exact end, or delete it on `err`.

### L6. The 5 s hold disables a focused button (class 72 neighbour; verify in the browser)
- `settings.js:375` and `holdIfHeld`: a keyboard user who pressed Enter on export is holding focus on a control that becomes `disabled` for 5 s.
  Browsers blur a focused control when it is disabled, so focus falls to the document and the next Tab starts over from the top.
  The preflight already did this for up to 2 s; the hold makes it happen on every export.
- Fix: hold with `aria-disabled="true"` plus the existing early return keyed on `heldUntil`, or add the check to the browser checklist.

### L7. Nits
- `settings.js:329`: `heldUntil` is never pruned; `holdIfHeld` can `heldUntil.delete(path)` when `left <= 0`.
- `api.js:579`: the comment says "`staging` is {gen, rows}", but it now also carries `dropped`, `seen` and `cut`.

## Open owner pick (not re-ruled here)

- `fix-browser-clear.md:61`, residual 2, is still open: a capture token staged before the click resets and re-seeds at drain.
  New-capture rows that reached the page before the click then come back.

## Checked and clean

- Arrival order equals id order within a capture: `store._insert_batch` assigns consecutive ids and `_broadcast_batch` queues each batch in order, so the raised `clearId` never covers a post-click row.
- A capture token mid staging: the pre-token cuts raise `clearId`s that the reset zeroes; the rows after it re-stage in arrival order under the reset's post-bump snapshot.
- `highRate` during a drain: the `clearId` raise runs before `routeLiveRow`'s early return, so `setHighRate(false)`'s rebuild hides covered rows; CAN and chart gates sit above the return.
- A pane closed mid staging (probe P4): the stale `cut`/`seen` entries touch only the detached object, with no throw. A pane added mid staging reads as 0 (R10 revert fails its test).
- Several clears in one staging window: the latest cut wins, and it covers a superset of the earlier ones.
- `!pd` exemption: `plotIngest` only caches a definition (`plots.js:252-256`), with no chart or lane created.
- No digital-only clear exists: `clearAllDigital` is called only from clear-all and the capture reset, so folding it into the chart token over-gates nothing.
- Seed token (class 73 note): `runBackfill` reads `clearTokens()` before its first await, and the reset re-seed snapshots after `clearAllCharts`/`clearAllDigital` bumped the token. SPEC 9.2 `SPEC.md:1771` matches.
- Class 44 (none of the paged walks changed), 62 (no select), 76 (`clearTokens` names all three clear tokens, and the per-pane map follows pane birth).
- Two sessions exported back to back: the hold is keyed by path, and each row has its own note and timer. A deleted row's timer touches detached nodes only. Session ids are AUTOINCREMENT, so a new run never inherits a hold.
- Bundle and token paths are never held; `navigates` is judged before the await (revert K in the author's table).
- Revert claims spot-checked: R8 (`noteClears` at drain) and R10 (`?? 0`) each fail exactly the named test; settings E (no `holdIfHeld` on render) fails the re-render test.
- Every test in `clear_staged_backfill.test.mjs` (13) and `settings_export_hold.test.mjs` (9) passes run alone with `--test-name-pattern`; all three files pass whole (13, 9, 14).
- Negative assertions have controls: the B controls and the later live rows for the empty panes, charts and lanes; "positive control: the first click navigates" for the hold; the A control for `charts.size === 0`.
- Comments at `api.js:122` ("a live row always sits above clearId"), `api.js:679-682` (watermark still during staging) and `api.js:741-743` hold.
