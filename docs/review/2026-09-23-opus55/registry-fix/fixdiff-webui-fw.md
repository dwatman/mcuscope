# Fix-diff review: webui-fw (a597ae6)

Tally: 9 findings (2 MEDIUM, 7 LOW). Scratch: `~/tt-data/mcuscope-2026-09-25/fixdiff-webui-fw/` (driver scripts `js/`, `mutate.py`, `mutate.out`).

Cleanup owed, recursive delete needing the owner's confirmation: `rv/` (a copy of `host/mcuscope/webui`, `host/tests/webui_js`, the JSON fixtures and `protocol.py`) and `fw/` (a copy of `firmware/` with build outputs). No databases were created.

## Findings

### X-webui-fw-1 MEDIUM: a paused pane with a sparse filter freezes at its last match, not at the pause
- `host/mcuscope/webui/terminal.js:365` `drawnTop` (R23-1) returns the newest row the pane *drew*. A pane whose filter last matched long ago freezes there, although every row after it was fed and filtered before the pause.
- Failure: widening the filter while paused (the refilter the comment at `terminal.js:388` promises) shows none of the rows that arrived before the pause; they count as "N new" and appear only on resume.
- Driven (`js/zz_fixdiff_sparse_pause.test.mjs` in scratch, run beside the stub): rows 1 (marker) and 2-10 (debug), pane on `marker` only, flushed, pause, tick every channel, rebuild: rows `[1]`, expected up to 10.
- The four `terminal_pause_queue` tests stay green with any fix that separates "fed but unmatched" from "queued or never fed": e.g. api.js keeps the newest id it fed to the panes, and `drawnTop` returns `queue[0].id - 1`, else that watermark.

### X-webui-fw-2 LOW: `ping` and `can stat` now truncate silently, against SPEC 2.3
- `firmware/monitor/monitor_cmds.c:114` and `:246` (R48-1) bound the buffer at `wire_max`, and neither handler checks `b.over`: a long port name or state string goes out cut mid-token as `OK`.
- SPEC 2.3 (`docs/SPEC.md:108-109`): an over-long response is `ERR 8 overflow` "rather than sent truncated", with `i2c scan` "the one exception". A cut `state=busof...` or port name is the altered-token case the event path is built to avoid.
- Driven: `firmware/tests/test_monitor.c` "ping long name at seq 1/65535" pins the cut name (235 of 236 characters) as `OK`.
- The contract disagrees with itself: `INTEGRATION.md:386-388` tells handlers "never truncate" and in the next line "clamp it to `MON_OK_PAYLOAD_MAX`", which is what R48-1 did.
- Owner should pick. Recommended: `return b.over ? MONITOR_ERR_OVERFLOW : 0;` in both (ERR 8 at every seq still meets R48-1) and INTEGRATION.md's "clamp" becomes "check `MON_OK_PAYLOAD_MAX` and answer `MONITOR_ERR_OVERFLOW`". Otherwise SPEC 2.3 names `ping` and `can stat` as exceptions next to `i2c scan`.

### X-webui-fw-3 LOW: the `token needed` badge hides during the daemon's token lockout
- `host/mcuscope/webui/state.js:102` `setTokenNeeded(r.status === 401)` hides the badge on any other status, including the guard's 429 lockout (`server.py` `_deny_rate_limited`).
- A stored wrong token (a rotated daemon token) is sent by the 5 s poll and the WS reconnects: 10 failures a minute reach `TOKEN_FAIL_MAX`, the next 60 s answer 429, and the badge vanishes while `streamWarn` still says `click "token needed"`. It then flickers with the lockout cycle.
- Reasoned from the code, not driven. Fix: hide only on `r.ok` (or on any status but 401 and 429).

### X-webui-fw-4 MEDIUM: R77-1's 1 s host-step threshold fires on the daemon's own cross-port stamp inversion
- `host/mcuscope/webui/timewindow.js:377` `HOST_STEP_S = 1`, applied in `continueHost` to rows of every port in id order.
- The daemon stamps `ts` before the per-port rx queue and the write queue, so across ports a later id can carry an earlier ts: `store.py:189-197` puts that at about 1.4 s per port at saturation and keeps 10 s of slack (`WINDOW_TS_SLACK_S`) before calling it an episode.
- Failure: a two-port capture under load opens a spurious epoch. Every chart and lane breaks there, and every later sample of every port is drawn late by the inversion until clear-all or a reload; host labels and the hover mapping carry the same skew.
- Driven (`js/host_inv.mjs` in scratch): ids 1 (ts 100.0), 2 (ts 98.6, the other port), 3 (ts 100.1): epoch `{id 2, offset 1.4}` opened, row 3 drawn at 101.5.
- Fix direction: a threshold above the daemon's slack (10 s, the value the writer itself announces as a step), or key the step per port; SPEC 9.2's "more than 1 s" moves with it.

### X-webui-fw-5 LOW: the port template still tells a shim to answer a failed probe NACK
- `firmware/monitor/port_template/monitor_port_template.c:96-97`: "address probe: return 0 if the device ACKs, else ERR_NACK. `i2c scan` relies on exactly this convention."
- R16-4 changed the convention (`monitor.h:149-152`, INTEGRATION.md, SPEC 5.3): BUSERR, TIMEOUT or BUSY when the bus cannot be probed. The template is the file an integrator copies; one written from it maps a stuck bus to NACK, and `i2c scan` answers `OK` with an empty list, the defect R16-4 fixed.
- Reasoned (text). Fix: the template line says what `monitor.h` says.

### X-webui-fw-6 LOW: the firmware ends an episode only on `monitor_eventf`/`monitor_mark` lines, SPEC 2.3 on any whole event of the type
- SPEC 2.3: "The episode ends at the next event of its type sent whole". `emit_can_event` (`monitor.c:1035`), `monitor_plot`'s `!ps` (`:925`), `emit_pd` and the `!e can bus` notice go out through `write_line`, never `event_end`, so they never end an episode of their type; the sim ends it on any whole `!` line.
- The fix report names this as "known difference, not fixed". Reachable only when an application cuts its own over-long `!can`/`!ps`/`!pd`/`!e` through `monitor_eventf`, so the count is late (the quiet second still closes it), not lost.
- Fix: either the direct writers call the same-type check `event_end` makes (one `strcmp` while an episode is open), or SPEC 2.3 and the sim narrow "sent whole" to events built by `monitor_eventf`/`monitor_mark`.

### X-webui-fw-7 LOW: a pane added after clear-all during a backfill shows the rows that clear covered
- `terminal.js:830` gives a new pane clear-all's `clearId`, but not its clear generation. `api.js:531` `clearTokens` reads a pane born during the backfill as 0 and `api.js:611-613` raises `clearId` only for a pane whose `clearGen` moved, so the rows the backfill still delivers past clear-all's point land in the new pane.
- SPEC 9.1: a clear clicked while the backfill loads "also covers the rows that backfill delivers", and a pane added after clear-all "holding none of the lines it cleared".
- Reasoned, not driven (needs a backfill held open across the click and the add). Narrow: a reconnect or first-load backfill still loading when both happen.

### X-webui-fw-8 LOW: leftovers the fix batches handed over and nobody took
- `host/mcuscope/webui/state.js:173-176`: `MAX_DB_BYTES = 2 ** 42` has no reader since R71-1, and the comment above it still says it mirrors `ConfigStorageBody.max_db_bytes`, which is now bounded at 2^63 - 1.
- Class 27 (R27-9): `plots_host_step.test.mjs:62`, `:138` and `terminal_paused_hover.test.mjs:45` still hand-roll `closest: () => x`, ignoring the selector, although `dom_stub.mjs` now exports `lineCell(row)`. A renamed `.ln` class would not fail them.
- `api.js:715` comment says "the /status 401 path prompts for the token"; since R72-1 the poll is background and shows the badge instead.

### X-webui-fw-9 LOW: seven changed branches no test reaches (revert-verify sample below)
- Untested and live:
  - `terminal.js:198` `historyIdTo(pane) !== null`: a walk that reaches the start of the capture with no match leaves `historyMiss` set, and without this gate `search older` shows beside "no older lines to load" and does nothing.
  - `settings.js:49` the `TimeoutError` wording: reached when `/config` answers within the deadline and `/devices` does not.
- Redundant as written (delete, or drive the state that needs them):
  - `terminal.js:61` `!pane.autoscroll` in `anchorsFor`: the only writer of `autoscroll` (`setAutoscroll`) nulls `frozenAnchors` on resume.
  - `terminal.js:198` `!pane.autoscroll` for the button: resume rebuilds, which zeroes `historyMiss`.
  - `digital.js:399` `() => digitalPaused`: the lanes exist before any zoom and only a hand resume, which drops the zoom, unfreezes them. The fix report deleted the matching lanes repaint for the same reason.
- Untested, low stakes: `terminal.js:233`'s tick-mode gate (only saves a full redraw in the other modes) and `timewindow.js:417`'s `<=` at an epoch's exact start x.

## Checked, nothing found

- Owner rulings:
  - D-13: `grep -rn 'mon_can_filter\|hardware filter'` over firmware, host, docs finds only the forbidding text (SPEC 2.4, 5.3, INTEGRATION.md, `monitor_cmds.c:36`) and the review inputs. The hook is gone from `monitor.h`, the weak defaults, the template and the fake.
  - D-9: `monitor_mark`, `starts_with_tick_sigil` and `tokenize` treat only U+0020 as blank; `cmdbar.js stripSpaces` for commands and markers; the divider strip in `terminal.js buildLine` is space-only. A tab in a command now reaches the firmware, which refuses the control byte (`ERR 2`), as D-9 intends.
  - D-16: nothing in this partition gates on a daemon version.
- Overflow episode (`monitor.c`): read in full. The notice is built in its own buffer, so a whole event still in `g_out` survives; type sanitisation matches `write_line`; the quiet check runs in nested polls without touching `g_out`; wrap-safe tick arithmetic. In a scratch copy: `make run` and `make asan` 317/317, `make port-template` 10/10 twice.
- R16-4, R27-1, R48-1 bounds: `wire_max` is used by every variable-length builtin; `info` stays small (`up=`, `can=`, 63-byte extras). User handlers keep `resp_max`, as INTEGRATION.md documents (see X-webui-fw-2 for the wording clash).
- `asciiSpaces` (R19-2): the set equals `regex` ASCII `\s`, driven: `\x0b` matches, `\x1c`, `\x1f`, `\x85`, `\xa0`, ` ` do not. Escaped backslashes, `\S` in and out of classes, and a `-` after the class form read correctly.
- R72-1 sweep, class 72: all 34 `api(`/`authFetch(` sites enumerated. Background: the status poll and the six api.js stream requests. All others follow a user action, except the restart-badge prime at page load (`settings.js:868`), which may still prompt at load, when nothing is being typed.
- Class 59: `#tokenBadge` (`.restart-badge`, `inline-flex`) and `.older` (`.iconbtn`) are under the global `[hidden] { display: none !important }` (`style.css:40`).
- Class 34: the new stores (`fetching`, `derived`, `frozenAnchors.map`, host epochs) are Map, Set or arrays.
- Class 6: `continueHost` returns a non-finite ts untouched, so each member's own finite gate still drops it.
- R77-1 capture reset: `clearAllDigital` (reached from `resetForDbReset` and clear-all) empties the host epochs, so a new capture's low ids never read an old epoch.
- `tools/check_dist.py`: the env names it sets (`MCUSCOPE_UPDATE_CHECK`, `MCUSCOPE_{DATA,CONFIG,CACHE}_DIR`) are the ones `update_check.py` and `dirs.py` read; `db_path` identity check refuses a foreign daemon. `import urllib.error` is unused (tools/ is outside ruff's scope).
- `tools/webui_smoke.py`, class 3: its `socket.create_server` listener has no `SO_EXCLUSIVEADDRUSE`. Exempt: it is outside `host/mcuscope`, and the pid check in `_wait_ready` keeps the checks off any foreign daemon. On Windows a daemon bound to the wildcard address does not make the harness's `127.0.0.1` bind fail, so the docstring's "refused if anything holds it" is loose there.
- CI: the firmware and JS skip guards no longer pipe into `grep -q`; the Windows jobs check out the repo for `check_dist.py` before downloading to `dist`, from the workspace root.
- Tests run, one file at a time: the 45 JS test files the commit touched (`node --test`, all pass); `test_check_dist.py` 7, `test_webui_smoke.py` 5, `test_firmware_monitor.py` 7, `test_webui.py` 19, all passed.

## Revert-verify sample

`mutate.py` in scratch: 15 mutations of branches the fix reports do not list as revert-verified, each run against every JS test file in a private copy (`rv/`), one file at a time; the unmutated baseline passed every file. 8 caught, 7 missed (X-webui-fw-9).

| Mutation | Result |
|---|---|
| badge hides only on 401 (never hides) | caught: `state_token_background` |
| `setChartPaused` repaint dropped | caught: `plots_zoom_chip`, `plots_zoom_live_chip` |
| `anchorsFor` without `!pane.autoscroll` | missed |
| `search older` shown with `historyIdTo` null | missed |
| `search older` without the autoscroll gate | missed |
| devices `TimeoutError` wording | missed |
| colour grammar case-sensitive | caught: `chrome_color_grammar` |
| lanes `onZoom` always true | missed |
| `hostTsAt` `<=` to `<` | missed |
| derived export field kept after typing | caught: `can_export_port` |
| tick-zero redraw in every time mode | missed |
| clone without `canFilterPrev` | caught: `terminal_can_unfilter` |
| attach-save badge without `cfgGen` | caught: `settings_config_gen` |
| token re-rendered in the unreachable branch | caught: `settings_loading_hold` |
| anchor snapshot sharing the live lists | caught: `terminal_paused_anchors`, `terminal_paused_hover` |

## The two questions

1. Least confident, rechecked:
   - X-webui-fw-4's premise, that a cross-port inversion past 1 s happens in practice. Driven: `continueHost` opens the epoch on it. The inversion size is quoted from `store.py`'s own estimate (about 1.4 s per port at saturation), not measured here.
   - X-webui-fw-1: first read as intended (the fix report says the high-rate case must count as new). Re-driven with a sparse filter, which is not that case: rows fed and filtered before the pause drop out of the frozen view.
   - Not verified: X-webui-fw-3 and X-webui-fw-7 (reasoned only); the CI YAML (the apt `gcc-arm-none-eabi` under `-Werror`, pwsh `check_dist.py serve` on Windows); any browser behaviour (F-1, F-2, F-4 layout).
2. Not thought about before:
   - The one-line fix R48-1 took ("clamp as `i2c scan` does") turned `ping` and `can stat` into silent truncators, which SPEC 2.3 forbids and INTEGRATION.md both forbids and recommends.
   - R72-1 ties the badge to "the last answer was a 401", but the token guard answers a stored wrong token with 429 for a minute at a time.
   - A pane born after clear-all meets the backfill's clear-covering logic, which only knows panes that existed when the backfill started.
