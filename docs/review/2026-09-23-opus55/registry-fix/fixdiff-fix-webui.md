# Fix batch: fix-diff web UI findings (X-webui-fw-1, 3, 4, 7, 8 and 9, web UI parts)

Base: `7db70d8`. Scratch: `~/tt-data/mcuscope-2026-09-25/fix-fixdiff-webui/` (`mutate.py`, `mutate.out`; the private copy `rv/` is deleted by the script).
Tally: 6 findings fixed, plus 1 defect the X-1 fix would have created (fixed, tested); 21 mutations, 20 caught, 1 equivalent.

## Findings

- **X-webui-fw-1 fixed.** A pause now freezes at the newest row the pane's filter saw, not the newest row it drew.
  - New `pane.fedId` (`pane.js`): set per row by `api.js feedPanes` (after the high-rate return, so shed rows stay "new"), by every `rebuild`, and zeroed by `resetForDbReset`.
  - `terminal.js drawnTop`: `queue[0].id - 1` if rows are queued, else `fedId`.
  - Tests: `api_pane_pause.test.mjs` (new; live feed, shed, pause between a capture reset and its re-seed), `terminal_pause_queue.test.mjs` "a pane rebuilt over rows its filter skips...".
  - The fourth `terminal_pause_queue` test changed: an idle pane with unfed rows now freezes at 10 (what it filtered), not 13; the old expectation contradicted the file's own "never fed count as new" test.
  - `terminal_regex_count.test.mjs` hand-rolls `feedPanes`; its emulation now sets `fedId` as the real feed does.
- **Found while fixing X-1, fixed:** pause-all calls `setAutoscroll(p, false)` on panes already paused.
  - With `fedId`, that re-froze such a pane at its newest fed row, folding everything since its own pause into the frozen view at the next rebuild.
  - Before this batch it re-froze at its last drawn row and zeroed its "N new" (pre-existing).
  - Fix: `setAutoscroll` returns early for a pause of a paused pane (only `freeze.js pauseAll` reaches it, and it calls `freezeChanged` itself). Test: `api_pane_pause` "pause-all leaves a pane already paused...".
- **X-webui-fw-3 fixed.** `state.js authFetch` shows the badge on 401 and hides it only on a success (`r.ok`); a 429 lockout or any other failure leaves it as it was.
  - Test: `state_token_background` "the token guard's 429 lockout leaves the badge showing...".
- **X-webui-fw-4 fixed, at 10 s.** `timewindow.js HOST_STEP_S = 10`, mirroring `store.WINDOW_TS_SLACK_S`; SPEC 9.2 says "more than 10 s" and names 3.4's slack as the reason.
  - I found no reason for a different value: 10 s is where the daemon's own writer calls a stamp inversion an episode, and a per-port key would not help, because a real clock step hits every port and the epoch list is shared.
  - Cost, stated in SPEC 9.2: a real backwards step of 1 to 10 s is now a reordered burst, with samples nudged at the pre-step edge until the clock catches up.
  - `continueHost` compares against the newest row *by id*, not the highest x, so two inversions of 9 s each in a row open no epoch. That is by design, and the new test drives it.
  - Tests: `plots_host_step` "the daemon's cross-port stamp inversion, up to its 10 s slack, opens no epoch" (1.4 s and 9.9 s give none, 10.5 s opens one). The existing 2 s step became 12 s.
- **X-webui-fw-7 fixed.** A pane added after clear-all carries clear-all's token, so a backfill or staging area that was in flight across the click reads it as cleared.
  - `terminal.js`: `clearAll.n` counts clear-alls. `addPane` sets `pane.clearGen = clearAll.n`, and `clearAllGen()` is exported.
  - `api.js`: `clearTokens()` carries `all`, and a pane absent from a snapshot reads as `all`, not 0.
  - `noteClears` records clear-all's own cut and floor. A pane born after arming and not cleared since takes that cut, not the arrival count at the call that notices it: rows staged between the click and the add still show.
  - Tests: `api_backfill_clear_tokens` (the pane added after clear-all during the backfill holds none of its rows; control: a backfill started after the clear-all fills the new pane).
  - `clear_staged_backfill` covers the staged path (the born pane shows only the rows after the click, including one staged between the click and the add) and the dropped-staging floor ("M1: clear-all, a staged row, a pane added...").
- **X-webui-fw-8 fixed (web UI part).**
  - `state.js MAX_DB_BYTES` is deleted, and its comment no longer claims a mirror of `max_db_bytes`. `server.py` holds no such constant, so it is untouched.
  - The `api.js` onclose comment now says the status poll's 401 shows the token badge.
  - Class 27: `plots_host_step` uses `lineCell(row)`, and `terminal_paused_hover` returns the rendered line's inner cell, so `closest(".ln")` is exercised (mutation: a renamed class fails `plots_host_step`).
- **X-webui-fw-9 fixed (web UI part).**
  - Tested, kept:
    - `syncHint`'s `historyIdTo(pane) !== null` gate. Test: `terminal_history_stall` "a walk that reaches the capture's start with no match offers nothing more".
    - The settings `TimeoutError` wording. Test: `settings_ports_refusals` "a /devices answer missing the deadline /config met is named as no reply".
    - `hostTsAt`'s `<=`. Test: `plots_host_step` "an epoch's own start x maps back to the ts of the row that opened it".
  - Deleted as redundant:
    - `!pane.autoscroll` in `anchorsFor` (resume nulls `frozenAnchors`).
    - `!pane.autoscroll` in the `search older` gate (resume rebuilds, zeroing `historyMiss`; a page landing after a resume is dropped by `historyGen`).
    - digital.js's `() => digitalPaused` zoom predicate.
    - The tick-mode gate on the tick-zero redraw (it only saved one full redraw per clear-all in the other modes).

## Revert-verify (`mutate.py`, private copy, the named files run one at a time)

| Mutation | Result |
|---|---|
| feed sets no `fedId` | caught: `api_pane_pause` |
| `fedId` set before the high-rate return | caught: `api_pane_pause` |
| rebuild sets no `fedId` | caught: `terminal_pause_queue` |
| reset keeps `fedId` | caught: `api_pane_pause` |
| `drawnTop` back to the last drawn row | caught: `api_pane_pause`, `terminal_pause_queue` |
| re-pause guard dropped | caught: `api_pane_pause` |
| born pane without clear-all's token | caught: `api_backfill_clear_tokens`, `clear_staged_backfill` |
| clear-all does not count | caught: both |
| backfill reads an absent pane as 0 | caught: `api_backfill_clear_tokens` |
| staging reads an absent pane as 0 | missed, equivalent (below) |
| born cut at the noting call | caught: `clear_staged_backfill` |
| born floor at the noting call | caught: `clear_staged_backfill` |
| clear-all cut never noted | caught: `clear_staged_backfill` |
| badge hides on any non-401 | caught: `state_token_background` |
| badge hides on any answer but 401 | caught: `state_token_background` |
| `HOST_STEP_S` 1 | caught: `plots_host_step` |
| `HOST_STEP_S` 11 | caught: `plots_host_step` |
| `search older` without the `historyIdTo` gate | caught: `terminal_history_stall` |
| devices timeout wording | caught: `settings_ports_refusals` |
| `hostTsAt` `<` | caught: `plots_host_step` |
| `lineCell`'s class renamed | caught: `plots_host_step` |

The equivalent mutant is `noteClears`'s `was ?? st.seen.all` changed to `?? 0`.
A pane born with no clear-all since arming then reads as moved, but `born` routes it to `cut.all`/`floor.all`, whose initial 0 covers nothing, so the outcome is identical.
I kept the explicit form: it matches the backfill path's `?? clears.all`, and the `?? 0` form is only correct by that coincidence.

## Verification run

- Every JS test file, one at a time, serially, after the last edit: 193 files, 0 failing (`node --test <file>` in a loop).
- `uv run python -m pytest tests/test_webui.py` 19 passed, `tests/test_webui_smoke.py` 5 passed.
- No Python touched (no ruff run needed). A dash grep over the touched files and SPEC found no U+2013/U+2014.

## CHANGELOG lines

- Web UI: a paused pane keeps every line that arrived before the pause, so widening its filter while paused shows them instead of counting them as new.
- Web UI: "pause all" leaves a pane that was already paused at its own pause point and "N new" count.
- Web UI: the "token needed" badge stays up while the daemon locks out a wrong token (429).
- Web UI: a pane added after clear-all while the page's backfill is loading shows none of the lines the clear covered.
- Amend the unreleased line at `CHANGELOG.md:268`: "stepping back over 1 s" becomes "stepping back over 10 s". The daemon's own cross-port stamp reordering (up to its 10 s slack) no longer breaks every chart and lane.

## Needs another batch

- `CHANGELOG.md` is not in this batch's files: the lines above, including the line-268 amendment.

## Needs Windows

- Nothing: JS only, no platform branch.

## Needs a browser

- The token badge through a real 429 lockout: store a wrong token, wait for 10 failures, and check the badge stays up for the lockout minute.
- The Digital / Enum zoom chip still shows while zoomed and hides on resume, now that its predicate is gone (`digital_zoom*` and `chrome_window_group` pass in the stub).

## The two questions

1. Least confident, rechecked:
   - The staged cut for a pane born after clear-all. Re-driven with a row staged between the click and the add, on both the drained path and the dropped-staging floor. The naive fix (cut at birth) hid that row, and both mutants are caught.
   - Whether the re-pause guard changes any other caller. Enumerated every `setAutoscroll(` call: the pill toggle, the scroll-up pause and the born-paused pane all start live; only the freeze surface's `setPaused` reaches a paused pane.
   - X-4's inversion size is still the daemon's estimate (`store.py`), not a measurement; 10 s is its documented bound.
2. Not thought about before:
   - The X-1 fix interacts with pause-all re-pausing already paused panes. That path was already wrong (it zeroed the backlog) and would have become worse (it would fold rows in). No finding had framed pause-all as a caller of the per-pane pause.
   - The export of a paused pane sends `watermark: frozenId` (`terminal.js exportPane`). The freeze now sits at `fedId`, so the watermark can be higher, but the export carries the pane's filter, so rows between the last match and `fedId` do not match and the export is unchanged. `export_paused_window` passes; not driven with a sparse filter.
