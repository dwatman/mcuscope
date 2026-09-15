# Fix: fix-diff review of clear during a backfill and the session export hold

Findings: `fixdiff-browser-clear.md`. Files: `webui/api.js`, `settings.js`, `state.js`, `style.css`; tests under `host/tests/webui_js/`.
No browser runs (brief). Reverts ran on a copy of the tree (`~/tt-data/fd2-clear/tree`, script `revert.py`, log `revert.log`), so the shared tree was never mutated.

## M1. A dropped staging area re-delivered rows a clear covered

- `api.js:742` `dropStaging()`: runs `noteClears` on the area, then turns each cut into an id floor.
  - The floor is `lastId`, the id of the last line staged when that clear was seen (`noteClears`, `api.js:720`; `stageRow` tracks it).
  - Panes: `clearId` raised at once. CAN and charts: module `canFloor`/`chartFloor` (`api.js:601`), read by the backfill row loop (`api.js:565`).
- Called from `onclose` (`api.js:680`) and from `armStaging` when a newer handshake replaces an undrained area (`api.js:714`).
- First connect still at watermark 0 under a chart floor (a superseded first-connect staging area): the history seed gets a token no clear holds, so it is dropped (`api.js:519`); `chartsCleared` reads the saved real token.
- Capture reset in between: `resetForDbReset` zeroes both floors (`api.js:179`); pane `clearId`s were already zeroed there.
  - The daemon sends the capture token on every connection's first frame (SPEC 3.4), so a token lost with a dropped area comes back and still resets.
  - Test: new-capture ids 1 to 3, below the old floor 7, all show in panes, CAN and charts after the reset.
- Floors stay after the watermark passes them. They are inert: only a capture reset takes the watermark back below them, and it zeroes them.

## M2. Staging overflow dropped the newest rows and capture tokens

- `api.js:692` `stageRow`: every row is kept on arrival with its arrival number in `at`.
  - Past `BUFFER_MAX + BUFFER_SLACK` the oldest non-token rows are dropped down to `BUFFER_MAX`, in one pass.
  - A per-row `splice` measured 35 us per row at 5000 staged; the trim runs in blocks, as `pushBuffer` does.
- Cuts count arrivals (`st.count`), and the drain passes `st.at[i]` (`api.js:783`), so a cut still falls at the click after a trim.
- Comment corrected. `BUFFER_SLACK` exported from `state.js:475`.

## L1. `feedStaged` id guard untested

Test: a `{gap: 3}` notice staged before one pane's clear; after a rebuild that pane still shows its later rows.

## L2. Relative time re-zeroed on a covered row

`api.js:149` `pushRow(row, covered)`: restores `anchorTs`/`anchorTick` after `pushBuffer` for a row a clear-all covers. Used by `routeLiveRow` (chart cut) and the backfill loop (`chartsCleared` or under `chartFloor`).

## L3. SPEC wording (no code)

Proposed replacement for the clause at `SPEC.md:1524`, from "a clear (a pane" to the end of the sentence:

> a clear (a pane, clear-all, or the CAN table's) clicked while the page's backfill is still loading also covers the rows that backfill delivers and the live rows that reached the page while it loaded; live rows arriving after the click still show, unless that backfill also delivered them.

## L4, L5, L6, L7. Session export hold

- `settings.js:330` `holds`: path to `{end, views, timer}`, measured on `performance.now()` (L4).
- `syncHold`/`holdPath` (`settings.js:340`, `:350`) update every rendered row of a path together and delete the entry when the hold ends (L7).
- L5: a navigation takes a provisional hold at the click (`settings.js:397`), `STATUS_TIMEOUT_MS + EXPORT_HOLD_MS`.
  - `finally` sets the exact end, or releases the hold on a refusal.
  - A row rendered meanwhile joins the hold (`settings.js:410`), so it is held, and it is freed with the original row.
- L6: busy and held buttons are `aria-disabled="true"`, never `disabled` (`setBusy`, `settings.js:334`), with the early return on it (`:393`). This also covers the fetch path and the preflight.
  - `style.css:124`, `:131`: `aria-disabled` styled as disabled, no hover. Manual check owed: a held export button looks dimmed.
- L7 comment: `staging` fields are now documented at `armStaging`.

## Tests

- `clear_staged_backfill.test.mjs` (23, 10 new):
  - L1 gap notice; L2 anchor.
  - M1: control, clear-all plus close, CAN clear plus token save, pane clear plus token save, capture reset after the drop, first connect superseded (with control).
  - M2: token kept past the cap; oldest dropped and cut kept, with the warn count.
  - New M1/M2 pages start on a new capture, since floors are module state.
- `settings_export_hold.test.mjs` (14, 5 new): ended hold lets go of its rows; wall clock stepped back; re-render during the preflight; re-render during a refused preflight; focus kept.
  - The focus test gives the button browser blur-on-disable, with a positive control that disabling blurs.
  - Existing assertions moved from `disabled` to `aria-disabled`.
- `api_ws_backoff.test.mjs`: "staging is capped" asserted drop-newest (`maxId === BUFFER_MAX`). Now it asserts the newest rows are kept; the cap is asserted in M2.
- `rulings_chrome_settings.test.mjs:261`: E-9 asserts `aria-disabled`.
- Files run, one at a time with `node --test --test-concurrency=1`: `clear_staged_backfill` 23/23, `settings_export_hold` 14/14 (each test also alone), `rulings_chrome_settings` 14/14, `api_ws_backoff` 3/3.
  - Every new `clear_staged_backfill` test also passes run alone.
- Before the coordinator's instruction, one `tests/test_webui_js.py` run: 898 pass, 1 fail, the `api_ws_backoff` cap test (since updated). Nothing in the other agent's files failed.
- Review probes rerun (`~/tt-data/fd-clear/probe.test.mjs`): Q1 now `[8,9]`, P2 `[8]`, P3 anchor on the first shown row. R1 now resets (`maxId` 4, not 5004); its zero count is the probe's fake table still holding old rows.

## Revert results (each branch alone)

| Revert | Fails | Unique text |
|---|---|---|
| `routeLiveRow` keeps anchor | 1 | "relative time zeroed on a backfill or staged row the clear-all covers" |
| backfill keeps anchor | 2 | same, and "relative time zeroed on a row the dropped area's clear covered" |
| reset zeroes `canFloor` / `chartFloor` | 1 / 3 | "a new-capture CAN frame below the old capture's floor stayed hidden" / "...sample below..." |
| seed dropped under chart floor | 1 | "the history seed landed on charts a staged clear-all covered" |
| `chartsCleared` from saved token | 1 | "the next first connect plotted a sample staged before the clear, or lost the later one" |
| chart / CAN floor in row loop | 2 / 1 | "a chart sample staged before the clear came back through the next backfill" / "a CAN frame staged before the CAN clear came back through the next backfill" |
| `onclose` drops with floors | 2 | "rows staged before the clear came back through the next backfill" |
| `armStaging` drops with floors | 3 | "the superseding handshake brought back rows staged before the pane's clear" |
| pane `clearId` raised at drop | 4 | same texts as above |
| `canFloor` / `chartFloor` raised at drop | 1 / 2 | CAN / chart texts above |
| `noteClears` at drop | 5 | all M1 texts |
| pane / CAN / chart floor noted | 4 / 1 / 2 | as the raises |
| `lastId` tracked | 5 | all M1 texts |
| trim spares tokens | 1 | "the capture token was dropped from full staging: old-capture rows survived" |
| staging capped | 1 | "staging was not capped" |
| drain passes arrival number | 1 | "full staging lost rows after the click, or showed rows before it" |
| cut counts arrivals | 1 | same |
| drop newest (old behaviour) | 1 | "full staging dropped the newest rows" (`api_ws_backoff`) |
| `feedStaged` id guard (L1) | 1 | "a staged {gap} before the clear left the pane blank on rebuild" |
| token site passes arrival number | 0 | inert: a token takes no cut. Kept for symmetry with the line site |
| `performance.now` to `Date.now` | 12 | "the re-rendered row's hold followed the wall clock" among them |
| provisional hold | 7 | "a row rendered during the preflight handed back a live button" |
| exact end after navigation | 6 | "the provisional hold outlived the exact one" |
| refusal releases | 8 | "a refused preflight left the re-rendered row held" |
| early return on busy | 4 | "a click on the re-rendered row during the preflight preflighted again" |
| `disabled` set as well | 1 | "focus lost during the preflight" |
| render joins a hold | 5 | "the re-rendered row handed back a live button" |
| ended hold pruned | 1 | "the second hold reached a row of the first, still kept" |
| fetch path released | 2 | "the blob path took the navigation hold" |

A separate `isHeld(path)` early return failed nothing when reverted: every view of a hold is already `aria-disabled`. It was deleted.

## Known residuals

- Residual 2 of `fix-browser-clear.md` (owner ruled leave it): a capture token staged before the click resets and re-seeds at drain, so new-capture rows that reached the page before the click come back.
  - The dropped-area variant is the same case. The next connection's token resets and lifts the floors, and the re-seed shows those rows.
- A backfill superseded before it landed leaves no floor for its own rows. They never reached the page, so the next backfill shows them, as the ruling reads.
- Token path (fetch) export: a re-render during the fetch still builds a live button. Pre-existing, not in the findings.

## Proposed CHANGELOG (`Fixed`)

- Web UI: a clear pressed while the page's backfill was loading no longer comes undone when the stream reconnects before the backfill finishes.
- Web UI: a burst of live lines during a slow backfill no longer loses a capture reset notice; the oldest waiting lines are dropped instead of the newest.
- Web UI: relative time after a clear-all during a backfill starts at the first line shown, not at a cleared one.
- Web UI: the session export button keeps keyboard focus while held, stays held if Settings re-renders during the check, and its hold is timed on a monotonic clock.

## Proposed SPEC

- 9.1, `SPEC.md:1524`: the L3 replacement above.
