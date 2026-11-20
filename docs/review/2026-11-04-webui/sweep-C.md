# Registry leg C: classes 74, 76, 77, 79, 80, 83, 102 (web UI only)

Scope: `host/mcuscope/webui/*.js`, `index.html`, `style.css` (vendor/ ignored). Read-only; line numbers are the tree as of this run.
Two scratch probes were driven under the DOM stub (not in the repo): `p77.test.mjs`, `p77b.test.mjs` in this directory.

Totals: 74: 0 violations. 76: 0. 77: 2 (one root). 79: green (20/20). 80: 0. 83: 0. 102: 0.

## Class 74

Command 1: `grep -n "max\|cap\|limit" host/mcuscope/webui/statusbar.js host/mcuscope/webui/settings.js`: **42 lines**.
Command 2 (also): `grep -nwiE "cap|limit|max_[a-z_]+|MAX_[A-Z_]+" host/mcuscope/webui/*.js | grep -iE "textContent|title|print|err\(|die\(|echo"`: **9 lines**.

Command 1 sites:

| Site | Verdict |
|---|---|
| statusbar.js:13 `Math.max(0, ...)` uptime floor | exempt because no limit is displayed |
| statusbar.js:69 comment on fmtBytes | exempt, comment |
| statusbar.js:85, 87 daemon chip title `db <content> / <cap>` | complies: `dbFigures` pairs `db_max_bytes` with `db_content_bytes`, the enforced figure (SPEC 3.4 line 733; server.py:1330-1333); file size is only the "on disk" aside |
| statusbar.js:185 session title text ("capture") | exempt, the word "capture", no limit |
| statusbar.js:245-247 comments | exempt, comments (state the content-vs-cap pairing) |
| statusbar.js:251 `cap:` from `db_max_bytes` | complies (same pairing) |
| statusbar.js:258, 259 bar text `db <content> / <cap>` | complies (content against cap) |
| statusbar.js:263 trimmed title | complies, names content and lines trimmed |
| statusbar.js:404, 410, 429, 434 drop/dead chips ("capture") | exempt, no limit displayed |
| settings.js:155, 156, 158 comments | exempt, comments |
| settings.js:160 `capShown` state | exempt, not a display |
| settings.js:165-167 cap field value in MB | complies: the field is the cap itself, edited |
| settings.js:175-177 comment, `CAP_TITLE` | exempt, constant text with no figure |
| settings.js:187, 188 hint `0 = no cap; now <db_content_bytes>` | complies, content is the enforced figure |
| settings.js:194 hint without a figure (status failed) | complies, shows no figure at all |
| settings.js:398, 435, 442, 455 session export/delete texts ("capture") | exempt, no limit |
| settings.js:475 `/sessions?limit=50` | exempt, a request parameter, not displayed |
| settings.js:752-757 cap validation and send | complies (validation message names the unit the field uses) |
| settings.js:762, 765 body/capShown | exempt, not a display |
| settings.js:833 comment | exempt |

(42 lines: statusbar 18, settings 24; every one listed above.)

Command 2 sites:

| Site | Verdict |
|---|---|
| settings.js:176, 177 | exempt (comment, constant text with no figure) |
| settings.js:188 | complies (content beside cap) |
| settings.js:194 | complies (no figure) |
| settings.js:661 `Baud must be 1-${MAX_BAUD}` | complies: the bound is enforced against the entered baud, which is what the message is about (mirrors ConfigPortEntry.baud) |
| settings.js:755 size cap message | complies |
| statusbar.js:259 | complies |
| statusbar.js:696 attach dialog baud bound | complies (same as settings.js:661, PortAttach.baud) |
| terminal.js:724 `${panes.length} / ${MAX_PANES}` | complies: the cap is enforced on `panes.length` (terminal.js:723, 834) |

## Class 76

Command 1: `grep -n "Version\b\|View\b\|needsRebuild\|!==.*prev" host/mcuscope/webui/*.js`: **30 lines**.
Command 2 (also): `grep -nE "Version\b|View\b|needsRebuild|!==.*prev|drawn[A-Z]\w*|sizeChanged|const need\b|shiftWindow|\.dirty\b" host/mcuscope/webui/*.js`: **64 lines** (a superset of command 1's 30).

The memoised views and rebuild predicates the hits belong to, with the build's inputs:

- **CAN table view key** (`can.js:298-340`, `:427`; hits can.js:29, 30, 32, 48, 152, 161, 291, 298, 313, 323, 325, 326, 327, 340, 427, 545, 547, 559, 699, 705, 730: 21 lines): **complies**.
  Key = row-set version (frozen version while paused), `canFilter`, `canLayoutVersion` (collapse).
  Build reads: entries (version), filter (key), collapsed set (layout, bumped in `toggleCollapsed`), `multi` (derived from entries), `canCapWarned` in the count text (set only together with a `canRowsVersion` bump at :152, reset at :698 with one), `portColor` (a pure hash of the alias, state.js:188), `groupLabel` (port, bus). Per-cell content is diffed against `r.last` in `updateCanRow`, which compares every field it writes (ext, rtr, dlc, hex, mask, count, period, age).
- **Digital lane repaint predicate** (`digital.js:553-569`; hits digital.js:105, 142, 298, 312, 553, 556, 557, 568, 569, 893): **complies**.
  Key = `dirty`, `_sizedirty`, canvas size at dpr, `drawnEdge` (xmax), theme.
  Inputs: data (ingest sets dirty), width/dpr (sizeChanged), window span and time mode (`markDigitalDirty` from the window buttons :399, time mode terminal.js:890, plots.js:989/999, resize plots.js:1241), zoom (onSelect/clearZoom call `markDigitalDirty`), show (`applyLaneShow` sets dirty), colour (sets dirty), pause/resume (:893).
- **Chart rebuild predicate** (`plots.js:1195-1197`; hits plots.js:646, 697, 716, 800, 988, 998, 1195, 1202, 1204, 1206, 1219, 1372, terminal.js:888): **complies**.
  Key = no uPlot, series count, theme. Axis splits/values, range, value formatters and `isInt` are closures reading live state. Unit change rebuilds explicitly (plots.js:645, and `renderChans` via `newChannel`), the shown-count boundary (:802, :814), colour (:777). A width change re-sizes and re-decimates (:1199-1203, `resizePlots`).
- **Terminal `shiftWindow` append path** (terminal.js:235, 295; pane.js:33): **complies**. Keyed on row identity per element; `state.anchorTick` now forces a full render (terminal.js:232-233, the R76-1 fix); time mode, regex and port change go through `render`/`rebuild` without `shift`.
- **`scopedCount` cache** (terminal.js:184-187, reached via `drawnTop`/`fedId` hits pane.js:37, terminal.js:352, 369, 421): **complies**. Key = buffer head, `clearId`; the filter inputs of `inScope` (port, channels) reset it through `rebuild` -> `resetHistory` (`pane.scope = null`, terminal.js:539); pause/resume switch `top`/`src` without double counting (snapshot shares the head).
- statusbar.js:100, 102, 103 (`pageVersion` reload badge), :115, :140 (`dismissedVersion`): **complies**, recomputed live on each status poll, not a cache.
- app.js:35, 44 `setView`; plots.js:224 `DataView`; terminal.js:969 `scrollIntoView`; plots.js:841, 1067, 1169, 1180 `drawnValue`: **exempt**, name matches only, no cache.
- Widened clause (resize): sidebar width re-clamped on `resize` (app.js:72, layout.js:53-58); the CAN cap is a percent, scale-free. **complies**.

## Class 77

Command: `grep -n "lastTick\|+ 1e-4\|Math.max(.*tick" host/mcuscope/webui/*.js` (webui half): **12 lines**.

| Site | Verdict |
|---|---|
| digital.js:125 host nudge in `pushVertex` | complies: `x.host` is already continued past a >10 s backward step (`continueHost`, plots.js:302, before `digitalIngest`); a smaller step is the stated reorder slack |
| digital.js:126 tick nudge | complies: tick is `continueTick`'s `c.x` with a null break vertex on restart (:90-97) |
| digital.js:156 `noteLaneId` host nudge | complies (continued host) |
| plots.js:466 initial `lastHost/lastTick` | exempt, initial state |
| plots.js:586, 587 nudges | complies (host continued at :302, tick at :578, `breakChart` on restart or host epoch change :584) |
| plots.js:589 assignment | complies |
| plots.js:655, 656 `breakChart` one-point gap | complies, a deliberate break past the newest sample |
| plots.js:1017 `Math.max(Y_AXIS_MIN_PX, ...)` | exempt, pixels (`ticks.size` matched the grep) |
| timewindow.js:226 widest label | exempt, pixels |
| timewindow.js:356 `continueTick` epoch offset | complies, the restart handler itself; host gap clamped at 0 |

(12 lines: 3 + 6 + 1 + 2.)

Also-sweep (comparisons against a last/previous stamp, live-edge maxima). Command: `grep -nE "(<=|<|>=|>) *[a-zA-Z_.]*(last|prev|Last|Prev)[a-zA-Z_.]*(ts|tick|Tick|host|Host|Ts)\b|max\([^)]*(ts|tick)[^)]*\)" host/mcuscope/webui/*.js`: 9 lines (5 overlap the above), plus a read of every `row.ts` / anchor comparison (`grep -nE "\.ts\s*(>|<|>=|<=)|..."`):

| Site | Verdict |
|---|---|
| api.js:808 `chartFloor` | exempt, an id floor |
| digital.js:116, 117 `digitalLast` live edge | complies, continued host and tick |
| timewindow.js:304-305 anchor thinning | complies, a reset or wrap is always kept |
| timewindow.js:352 jump detection | complies, it is the restart test |
| timewindow.js:407 host step | complies, the host restart handler |
| api.js:351-353 `seedLastMs` | complies, clamped at 0 |
| terminal.js:670-671 export min/max ts | complies, a range over rows, bounded by ids too |
| can.js:165 period `dt` | complies, a negative dt is skipped |
| **can.js:130, 134 CAN age anchor (`tsAnchor`)** | **violates (C77-1)** |
| **can.js:146-149 eviction by least `lastTs`** | **violates (C77-2, same root)** |

### C77-1 (can.js:128-136, read at :520): CAN ages read the full step after a host clock step back

- `tsAnchor` only moves forward: rows at :134 (`row.ts > tsAnchor.ts`), the daemon reading at :130 (`ts > liveNow()`).
  `liveNow()` keeps running from the pre-step anchor on `performance.now()`.
- Sequence: a 10 Hz id 0x100 is live at T. The daemon host's wall clock steps back by S (an NTP step or a manual change; SPEC 9.2 line 2053 treats this as legitimate). Frames keep arriving with `ts = T - S + e`.
- On screen: every age reads about S, and every periodic id is coloured `age-dead` (red) while it is transmitting at 10 Hz. The /status `now` cannot correct it (it is behind `liveNow`). It ends only once a row's ts passes the old maximum, S seconds later, or on clear, reset or reload.
- Driven (`p77.test.mjs`): before the step the age is `1ms age-fresh`. After a 3600 s step back with frames still flowing, it is `59m58s age-dead`.
- Refutation tried:
  - No other writer of `tsAnchor`: grep shows only :130, :135 and the clear at :712.
  - `continueHost` (timewindow.js:396) covers charts and lanes only.
  - The tests pin the opposite case: can_table.test.mjs:86 "a daemon reading older than the rows already seen does not pull the clock back", for a stale round trip. Nothing covers a real step.
- History: this is the CAN half of owner decision O1, option (b), in docs/review/2026-09-15-prerelease/sweep-webui-panes.md:207-215. The 2026-09-23 R77-1 fix implemented that option for charts and lanes only. The 2026-10-05 RES-4 widening puts host-clock steps in class 77, so this half is now in scope and still open.

### C77-2 (can.js:146-149): eviction at the 256-id cap evicts the live ids after a step back

- "Least recently seen" is taken as the minimum `lastTs`.
- Sequence: 256 ids are seen at T, then go quiet. The clock steps back. Two ids now alternate at 10 Hz.
- Each new frame evicts the other live id, because it has the lowest `lastTs`, while the 254 silent pre-step rows stay.
- On screen: the live ids churn in and out, and their count and period never accumulate. This is against the comment's own aim, "so live traffic stays visible under the cap".
- Driven (`p77b.test.mjs`): after the step, 0x100 is absent and 0x101 is present, with size 256.
- A fix has to hand: `e.lastId` is already kept (:186), and ids only climb within a capture.

## Class 79

Command: `node --test host/tests/webui_js/module_load_order.test.mjs` (node v22.23.4).

Result: **20 tests, 20 pass, 0 fail**:

- 18 module cases, one per `webui/*.js`: api, app, can, chrome, cmdbar, digital, exportdlg, exportrange, freeze, layout, pane, plots, settings, state, statusbar, terminal, theme, timewindow. `ls webui/*.js` returns 18.
- "the module list is non-empty and plausible".
- The probe's own positive control: "the load probe reports a module that throws at import time".

The cycles are broken by registered callbacks: `onSeedBump` and `onLanesChanged` in digital.js:61-65, `onZoomControls` in chrome.js:109, and hooks. No violation.

## Class 80

Command 1: `grep -n "staging\|queue\|pending\|frozen" host/mcuscope/webui/*.js`: **205 lines**.
Command 2 (also): `grep -nE "staging|queue|pending|frozen|buffer|history|seed" host/mcuscope/webui/*.js`: **402 lines** (a superset). Per file: api 120, terminal 84, plots 61, digital 56, pane 27, can 17, cmdbar 11, state 10, chrome 5, timewindow 5, exportdlg 2, app 1, exportrange 1, freeze 1, settings 1.

Clear tokens: pane `clearId`/`clearGen`, clear-all (`clearAll.n`), `canClearGen`, `plotSeedGen` (bumped by both `clearAllCharts` and `clearAllDigital`), and the capture reset (`resetForDbReset`, `captureGen`).
Every queue or buffer that feeds a view, and how each is reset or gated:

| Feed (sites) | Verdict |
|---|---|
| `staging` (api.js:402, 551-552, 577, 628-637, 674, 684-686, 702-708, 717, 725-767, 795-804, 824-841, 859-864): the live rows held during a backfill | complies: `noteClears` records a per-surface arrival cut (panes, all, CAN, charts). `feedStaged` raises the pane `clearId` and passes `i < cut.can` / `i < cut.charts` into `routeLiveRow`. A dropped area leaves id floors (`canFloor`, `chartFloor`, pane `clearId`). Tests: api_backfill_clear*, clear_staged_backfill, api_staging_overflow |
| backfill rows (api.js:539-606): `runBackfill` loop | complies: `canCleared`/`chartsCleared` from tokens read before the first await, plus the floors. Pane `clearId` is raised past the batch for every pane whose token moved, including panes born meanwhile |
| reconnect gap divider (api.js:437-447, 590; `gapRow`) | complies: buffer only; panes filter by `clearId` |
| shed notice `pendingGap` (api.js:238-257, 741, 766) | complies: the divider goes through `pushRow(covered)` and `feedPanes`, gated by `clearId` (raised by `feedStaged` before it). Re-zeroed at `armStaging` |
| plot history seed (api.js:278-447 seed lines, 574-584; plots.js:76-79, 290-291, 298, 319-458, 499, 1501-1507, 1519) | complies: `seedPlotHistory` drops the seed when `cleared !== plotSeedGen()` after its awaits (api.js:423). `seedMaxId` is cleared with the charts. `!pd` rows pass deliberately (definitions survive a clear, api.js:603-607) |
| `seedPlotDefs` (api.js:286-317) | exempt because it feeds only `!pd` definitions, which no clear drops by design (resetForDbReset comment, api.js:207-209) |
| pane `queue` (api.js:111-112, 123-151; terminal.js:22-24, 284, 367, 371, 429, 432, 441-465, 782, 929, 940; pane.js:27) | complies: `feedPanes` skips `row.id <= p.clearId`. Per-pane clear and clear-all empty it (terminal.js:782, 929). `rebuild` empties it (:432) |
| pane `pending` / `pendingDirty` (api.js:142-143; terminal.js:204, 344, 430-431, 455-459, 782, 929; pane.js:28-29) | complies: zeroed by both clears and re-derived by `countPending`, which gates on `clearId` (terminal.js:408) |
| shared `buffer` (state.js:147, 286, 296-307; api.js:1, 46, 84, 183, 187; terminal.js:1, 17-20, 183, 263-264, 388-416) | complies: every pane read filters `row.id > clearId` (scopedCount :190, renderEmpty :266, countPending :408, rebuild :420). The charts, lanes and CAN never re-derive from it. A capture reset empties it (api.js:187) |
| pane `frozenRows` / `frozenId` / `frozenAnchors` (terminal.js:60, 180-183, 348-358, 391-397, 403-416, 584, 663, 677; pane.js:36-39; api.js:194-200) | complies: the snapshot is taken as `buffer.filter(id > clearId)` (:357), and `rebuild` re-filters it by `clearId`. A capture reset nulls it |
| pane history paging (terminal.js:3, 527-616; pane.js:42-47, 180-182, 190, 203, 214, 222-240) | complies: `since_id = clearId` on every page (:566), `historyIdTo` floors at `clearId + 1`, and `resetHistory` bumps `historyGen` on every clear, rebuild and reset (:537-539, checked at :578) |
| high-rate shed (api.js:43, 84, 123-125) | complies: no feed while high; `setHighRate(false)` rebuilds from the buffer gated by `clearId` |
| chart `frozen` / `frozenMaxId` (plots.js:468, 473, 637, 646, 830, 955, 993, 1094, 1123-1143, 1186, 1359-1365, 1384, 1412-1413) | complies: clear-all destroys the charts wholesale (`charts.clear()`, plots.js:1511-1524). Paused new charts are born frozen |
| lanes `frozen`, `laneIds`, `digitalFrozen` (digital.js:30, 36, 53, 57, 104, 108, 115, 149-151, 165-188, 220-226, 429-430, 447, 476, 481, 526, 563-564, 636, 756-757, 767, 869-884, 911-940) | complies: `clearAllDigital` clears lanes, `laneIds`, the edges and the clocks, and bumps `plotSeedGen` so a backfill or staging cut gates the lanes too |
| CAN `canFrozen*` (can.js:43, 51, 122, 309, 324, 495, 561, 567, 639, 644, 656-679, 733) | complies: `clearAllCan` empties `canRows` and, while paused, the snapshot (can.js:703-707). Its `clearGen` gates staging and backfill. The CAN history export reads the capture by design ("a view-only clear does not empty" it, :309) |
| digital.js:735, 737, 818-823, 831-832, 840-841, 862-863 (`pendingCursorX`/`pendingClientX`) | exempt because these are cursor positions, not rows |
| cmdbar.js:10, 13, 14, 21, 169, 223, 247, 255, 271, 299, 300 | exempt because they are command history and the pending verdict, not captured rows |
| chrome.js:9, 10, 101, 115, 167; timewindow.js:31, 32, 66, 293, 377; exportdlg.js:24, 233; exportrange.js:48; freeze.js:46; settings.js:684 (save chain); app.js:216; api.js:11, 646, 662, 881 (timers); plots.js:22, 224, 1258, 1276, 1346; state.js:371, 403, 414; api.js:522; pane.js:59 | exempt because they are comments, name matches, timers, or download queues that feed no view |

No violation. Browser drive (`page.route` holding `/lines`) was not run in this leg; the node tests above cover the stub path.

## Class 83

Command: `grep -nE "monotonic in|rises with|in id order|contiguous|MAX\(id\)|MIN\(id\)|ORDER BY ts|ORDER BY id DESC LIMIT 1" host/mcuscope/webui/*.js` (webui half): **9 lines**.

| Site | Verdict |
|---|---|
| api.js:469 `planBackfillStep` gap "exact while a capture's ids are contiguous" | exempt: a stated premise behind a displayed gap count. Ids have holes after a rolled-back batch, a mid-capture purge or session delete, or a cap trim inside the gap, so the divider can overstate. Same ruling as REVIEW_LOG.md:122 (2026-10-05) |
| pane.js:192 `gapRow` (exact while contiguous) | exempt, same |
| pane.js:259 `planHistoryPage` divider | exempt, same |
| terminal.js:597 divider between contiguous rows | exempt, same |
| api.js:630 staging "merged in id order" | complies: the drain sorts by id within each capture segment (api.js:835-856) |
| can.js:560 "Rows are written in id order, so state.maxId is the last line this table has seen" | complies: one writer assigns ids and broadcasts each committed batch in order. The page merges staging by sort, and history pages never move `maxId` |
| digital.js:180 same premise for `digitalFrozenId` | complies, same |
| plots.js:1364 same premise for `frozenMaxId` | complies, same |
| timewindow.js:120 "contiguous slice" | exempt, an array slice, not an id or ts premise |

Writers named: the store's single writer for every channel (lines, markers, cmd/tx, sys rows) assigns ids in commit order. `ts` is stamped per reader thread, and no webui site derives an id bound from `ts`.

## Class 102

Command: `grep -n "pointerdown\|setPointerCapture" host/mcuscope/webui/*.js`: **4 lines**.

| Site | Verdict |
|---|---|
| app.js:116, 117 `#resizer` | complies: ended by `pointerup`, `pointercancel` and `lostpointercapture` (app.js:140); `.resizer { touch-action: none }` (style.css:147) |
| app.js:168, 169 `#canPlotDivider` | complies: the same three events (app.js:185); `.hdivider { touch-action: none }` (style.css:249) |

Extra check: `grep -n "mousedown\|touchstart\|dragstart" webui/*.js` returns 0 lines, so there is no other gesture.

## Aside, out of class (not counted)

- plots.js:668, :619: a typed chart's `isInt` is set only at `addChannel` and flips only for ad-hoc charts (`chart.sid === null`).
- A stream redefined with the same name from an integer type to `f4` therefore keeps `isInt` true, and `fmtPlotVal` rounds its readouts (`3.7` reads `4`).
- The unit has the analogous redefinition fix (:598-601); the type has none.
- Reasoned only, not driven.
