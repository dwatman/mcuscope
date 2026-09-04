# Adversarial review leg: web UI (JS)

HEAD: `7a1120f`

Scope: `host/mcuscope/webui/*.js` (15 modules, 4796 lines), `index.html`, and `host/tests/webui_js/` (36 test files, 254 node tests, all green at HEAD).
SPEC section 3 read as the REST/WS contract the UI consumes.

Method:
1. `docs/REVIEW.md` read in full; the sweeps for classes 6, 16, 23, 25, 26, 34, 36 and 44 run mechanically over the JS (site counts and per-site verdicts below).
2. A second pass with no target list, driving the failure shapes the brief names, plus `git diff 412c014..HEAD -- host/mcuscope/webui` (eol setting, disconnect_reason chip).
3. Test quality: candidate mutations named per file, six of them applied to a scratch copy and run; four confirmed uncaught, two caught. Tree restored and verified clean (`git status --porcelain` shows only this new report directory).

Findings are ranked most severe first.

---

## W1 (HIGH) - a settings ports-table save silently deletes every port's `eol`

`host/mcuscope/webui/settings.js:385-411` (`collectPorts`), with the server half at `host/mcuscope/server.py:319` and `host/mcuscope/config.py:505`.

`collectPorts` builds each entry as `{alias, autoconnect, device?, serial_number?, baud}` and never carries `eol`, which `GET /config` does return per port (`server.py:1066`, SPEC 3.3.1 / line 600).
`ConfigPortEntry.eol` defaults to `PortConfig.eol` (`"lf"`), and `save_ports` writes the key only when it differs from that default, so the omitted field is not "leave it alone" - it rewrites `config.toml` without the line.

Failure scenario: a port is configured `eol = "crlf"` by hand (or by `mcu attach --eol crlf`). The user opens Settings for an unrelated reason - to change a baud, add a port, or fix a device path - and presses Save on the Ports section. The PUT carries no `eol`, the daemon defaults it to `lf`, `save_ports` drops the key from the file, and the next restart sends bare LF to a target that needs CRLF. Nothing in the UI mentions eol at all, so there is no wording anywhere connecting the two.
`saveAttachedPortToConfig` (`settings.js:548-560`) has the same shape for the alias it replaces.

Registry: repeat instance of **class 31** (a field the model accepts and the path never reads), in the **one-of-two-siblings** shape class 23 names. The sibling is three lines away and got it right: `put_config_ports` at `server.py:1195-1198` types `identify: bool | None = None` and merges the saved value back, with the comment *"`identify` is config-file only (the settings dialog does not offer it), so a save that omits it must not flip a hand-written `identify = false` back to the default."* That is verbatim the argument for `eol`, which the settings dialog also does not offer.

Proposed minimal fix (root cause, one place, covers every client including a hand-rolled PUT):

- `host/mcuscope/server.py:319`, change `eol: Eol = PortConfig.eol` to `eol: Eol | None = None   # omitted: keep the saved value for this alias`.
- `host/mcuscope/server.py:1198`, beside `saved_identify`, add `saved_eol = {pc.alias: pc.eol for pc in saved.ports}`.
- `host/mcuscope/server.py:1216`, change `eol=entry.eol` to
  `eol=(entry.eol if entry.eol is not None else saved_eol.get(entry.alias, PortConfig.eol))`.

Client-only alternative if the wire contract must not move: in `settings.js addPortRow` stash `tr._eol = pc.eol` (`pc` is the `GET /config` entry) and in `collectPorts` add `if (tr._eol) entry.eol = tr._eol;`. A row added by `+ port` has no `_eol` and correctly takes the daemon default.

Test that fails without the fix (DOM-free on the server side, which is where the fix belongs): a pytest that writes a `config.toml` with `eol = "crlf"` on alias `board`, PUTs `/config/ports` with the body the dialog sends (`{"ports": [{"alias": "board", "device": "...", "baud": 115200, "autoconnect": true}]}`), then asserts `load_config(path).ports[0].eol == "crlf"`. The existing `identify` test for this handler is the template.
Browser-side twin, matching `settings_ports_baud.test.mjs`: stub `GET /config` returning a port with `eol: "crlf"`, open the dialog, click `cfgPortsSave`, assert the captured PUT body's port entry carries `eol: "crlf"`.

## W2 (MED) - a port that receives but cannot send draws a healthy chip

`host/mcuscope/webui/statusbar.js:198-236` (`renderPorts`).

`/status` carries `write_failures`, `last_write_error`, `last_write_error_ts` and `write_failing_since` per port (SPEC 3.3, `serial_link.py:1245-1248`, with the comment *"a port that receives but cannot send shows here and nowhere else"*).
`renderPorts` reads none of them: not in `portsSig`, not in the dot class (`statusbar.js:229`), not in the `data-tip`. The dot class is driven only by `held`, `connected` and the store-wide `writeErrors`/`writerDead`.

Failure scenario: the bench STLINK-V3PWR case the serial layer documents - a target power cycle leaves RX flowing while every write times out. `mcu status` prints `DEGRADED: 12 write failures since 14:03:11` (`cli.py:191-196`). The browser shows a green dot, a climbing rx count and no indication at all, and every command typed into the command bar reports a daemon-side error with the port still reading healthy beside it.

Registry: repeat instance of **class 12** (healthy-while-dead surfaces), and **class 31** for the unread field. Same "the CLI renders it, the browser does not" split the `disconnect_reason` chip (5cbaa69/1455674) was closing; that commit carried `disconnect_reason` across and left `write_failures` behind.

Proposed minimal fix, mirroring the `rx_dropped` chip immediately above it:

- `statusbar.js:203`, add `p.write_failures || 0` to the signature array (a change in the count must repaint).
- `statusbar.js:229-230`, make a write-failing port critical like a store-wide write error:
  `dot.className = "dot" + (pt.held ? " crit" : pt.connected ? (writeErrors || writerDead || pt.write_failures ? " crit" : "") : " off");`
- after the `rx_dropped` block at `statusbar.js:252-258`, add the same shape:
  ```js
  if (pt.write_failures) {
    const wf = document.createElement("span");
    wf.className = "meta drop";
    wf.textContent = `${pt.write_failures} write fail` + (pt.write_failures === 1 ? "" : "s");
    wf.title = pt.last_write_error || "Consecutive failed writes to this port; it receives but cannot send";
    chip.appendChild(wf);
  }
  ```

Test idea (extends `statusbar_logic.test.mjs`, which already drives `renderPorts` through `refreshStatus` with a stubbed `/status`): a port with `connected: true, write_failures: 12, last_write_error: "Write timeout"` must render a `.dot.crit` and a chip child whose text contains `12 write fails`. Assert on that exact wording, not on `.drop` alone - `rx_dropped` and `writeErrors` already use `.drop`, so a class-only assertion cannot tell the three apart.

## W3 (MED) - the one unguarded `localStorage.setItem` wedges the CAN group dividers

`host/mcuscope/webui/can.js:165` (`toggleCollapsed`).

Eleven `localStorage` write sites exist in the web UI. Ten are wrapped in `try { ... } catch { /* private mode */ }`: `state.js:20` and `state.js:338` (token, eol), `chrome.js:36`, `cmdbar.js:28`, `theme.js:23`, `terminal.js:528`, `statusbar.js:97`. `can.js:165` is the only bare one - and its own file guards the matching *read* three lines earlier (`loadCollapsed`, `can.js:155-160`), so the asymmetry is inside one function pair.

Failure scenario: Firefox private browsing, Safari with storage blocked for the origin, or a full storage quota. `localStorage.setItem` throws `QuotaExceededError`/`SecurityError`. The throw escapes `toggleCollapsed`, so the two lines after it - `canRowsVersion += 1; renderCan();` - never run: clicking a bus divider does nothing at all, with no error the user can see, for every click for the life of the page. The multi-port/multi-bus CAN view is then permanently un-collapsible.

Registry: repeat instance of **class 34**'s storage-hostility clause (localStorage is not a reliable store and every access is guarded), in the one-of-N-siblings shape. Also **class 16**'s mirror: the failure is charged to the whole operation rather than to the write.

Proposed minimal fix, `can.js:165`:
```js
  try { localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...set])); } catch { /* private mode */ }
```
The collapse then applies for this page and is simply not remembered, which is the behaviour every sibling already has.

Test idea (DOM-free enough for the stub; `theme_storage.test.mjs` is the exact template - it already drives "the write is refused"): replace `localStorage.setItem` with a thrower, call the divider row's click handler, and assert the group collapsed in the rendered table. Assert on the collapsed row's text (`(N ids)` / the `▸` caret), not on the absence of a throw.

## W4 (MED) - `timeMode` is type-checked out of localStorage but not range-checked

`host/mcuscope/webui/terminal.js:551`.

```js
if (st && typeof st.timeMode === "string") state.timeMode = st.timeMode;
```
Any string is accepted. Its two siblings both validate the enum: `theme.js:142` (`saved === "light" || saved === "dark" ? saved : sys`) and, added in this very diff, `state.js:330` (`EOL_CHOICES.includes(saved)`).

Failure scenario: `termState` is hand-edited (or written by a future build that adds a fourth mode and is then rolled back) to `{"timeMode": "abs", "panes": [...]}`. On load `syncTimeSeg` (`terminal.js:531-535`) lights no button in `#timeSeg` - all three are off, so the control claims nothing is selected - and `$("plotXLabel").textContent = {host, tick, rel}["abs"]` is `undefined`, which the textContent setter stringifies: the plots header literally reads **"undefined"**. Every consumer (`fmtTs`, `spanFor`, `currentData`, `laneDrawData`, `xAxisValues`) silently falls through to its host-time branch, so the axis is host time under a label that says nothing. Recoverable only by clicking a mode button, which the user has no reason to connect to the broken label.

Registry: repeat instance of **class 34**'s type-check clause - *"a localStorage value needs the full grammar (type, integrality, range), not a truthiness coercion"*, and *"the sibling-guarded field is the tell"*. This is the third instance of that clause (update-badge `step`, then eol on load, now this).

Proposed minimal fix, `terminal.js:551`:
```js
const TIME_MODES = ["host", "tick", "rel"];   // beside VIEW_MAX / MAX_PANES at the top
...
if (st && TIME_MODES.includes(st.timeMode)) state.timeMode = st.timeMode;
```
The existing `else if (st && st.rel === true)` migration arm below it stays as is.

Test idea (DOM-free apart from the stub's `getElementById`, which the terminal tests already use): seed `localStorage.termState` with `{"timeMode":"abs"}`, call `initTerminal()`, and assert `state.timeMode === "host"` **and** `$("plotXLabel").textContent === "x: host"`. The second assertion is the one that fails today; the first would pass on a fix that only silenced the label.

## W5 (LOW) - the browser-side line-ending setting is not shown when the daemon is unreachable

`host/mcuscope/webui/settings.js:506-518` (`openSettings`).

The `if (!cfg)` early-return branch renders `renderToken()` and returns, with the comment *"entering a token is most useful exactly when requests are failing"*. `renderEol()` is on the other side of that return (`settings.js:517`), although `getEol()` is a pure module read that needs no daemon at all - the section's own hint even says *"stored in this browser, not in the config file"*.

Failure scenario: eol is set to `crlf`; the daemon is restarting or the token is wrong, so `GET /config` fails. The dialog opens, and the Line ending select shows its markup default, `port default` (`index.html:211`), because nothing wrote the real value into it. The setting in force and the setting displayed disagree, on the one screen whose whole job is to show it. Closing the dialog does not corrupt the stored value (`setEol` only fires on `change`), so this is a display lie rather than data loss - but a user who "corrects" it back to CRLF and then to something else has been shown a false starting point.

Registry: **class 25**'s label clause read at one remove (*text derived from state, correct at the instant it is written and a lie afterwards*), and again the one-of-two-siblings shape - the other browser-side setting in this dialog is handled in exactly this branch.

Proposed minimal fix, `settings.js:513`, in the `if (!cfg)` branch beside `renderToken()`:
```js
    renderToken();   // entering a token is most useful exactly when requests are failing
    renderEol();     // browser-side too: it needs no daemon and must not read as the default
```

Test idea (`settings_pj.test.mjs` already stubs `api` per path and opens the dialog): `setEol("crlf")`, stub `GET /config` to reject, call the `settingsBtn` click handler, assert `$("cfgEol").value === "crlf"`. It reads `""` today.

## W6 (LOW) - the seed's ingest loops carry no per-item guard, where all four siblings do

`host/mcuscope/webui/plots.js:370-396` (`plotSeed`), both the entry loop and the group loop.

Every other loop over daemon-supplied rows in this stack guards per item and says why: `api.js:578-584` (live frame), `api.js:490-493` (backfill), `api.js:246-248` (definition seed, *"Per row, as the live path is: one malformed definition must not abandon the rest"*), `api.js:660-663` (staged drain, which exists because REVIEW class 16 caught precisely this duplicated-loop miss). `plotSeed` is the fifth such loop and the only unguarded one; its caller catches at whole-operation granularity (`api.js:336`), so one throw inside `routePoints` discards every group behind it in the same seed - the charts come up partly filled with no indication.

Honest bound on the severity: I could not construct a per-item throw that is reachable today. `seedNameOk`, the `Number.isFinite` gate and `routePoints`' kind check between them keep the reachable faults to whole-DOM ones (a missing `#plotCharts`), which cost every group anyway. This is filed as the class-16 discipline being absent, not as a live bug.

Registry: **class 16**, the duplicated-loop clause (*"When a loop is duplicated for a buffered/deferred variant, the guard must travel with it"*).

Proposed minimal fix, `plots.js:389-394`:
```js
    let maxId = 0, bad = null;
    for (const row of mergeSeedSeries(group)) {
      // Per row, as the live path is (api.js): one malformed seed row must not abandon the rest.
      try { routePoints(key, sid, row.points, row.x, def); } catch (err) { bad = err; continue; }
      if (row.id > maxId) maxId = row.id;
    }
    if (bad) console.error("plot history seed: some rows were dropped, last error:", bad);
```
and wrap the `for (const [key, group] of groups)` body the same way, so one bad group does not cost the others.

Test idea: extend `plots_seed_grammar.test.mjs` - seed two groups, monkey-patch `digitalIngest`'s target so the first group's first row throws, and assert the second group's chart still holds its samples. Assert on the second chart's sample count, not on "no throw escaped": the outer catch already swallows it, so an exit-status assertion passes on the bug.

## W7 (LOW) - the disconnect_reason chip shows the raw wire token with no gloss

`host/mcuscope/webui/statusbar.js:220-224`.

The tooltip appends `pt.disconnect_reason` verbatim, so a hovering user reads `no_device` or `open_failed`. The source comment one line above translates both (*"no_device = power or cable, open_failed = busy or permissions"*) and `mcu ai-guide` (`cli.py:2022`) documents "what to do about each" - none of which reaches the browser. `mcu status` at least frames it as `disconnected (no_device)`; the chip's tip is a bare token beside a baud.

New class candidate is not warranted - it is the plain-English half of the same commit's intent. Proposed minimal fix, `statusbar.js:223`:
```js
const DISCONNECT_WHY = Object.assign(Object.create(null), {
  manual: "disconnected on request",
  no_device: "device not present (power, cable, or still enumerating)",
  open_failed: "device present but the open failed (busy, or permissions)",
  read_error: "the link dropped mid-session",
});
...
  .concat(`@${pt.baud}`, pt.connected ? null : (DISCONNECT_WHY[pt.disconnect_reason] || pt.disconnect_reason))
```
Null-prototyped because the key comes off the wire (class 34); the `||` keeps an unknown future reason visible rather than blanking the line.

Test idea: extend the existing `statusbar_logic.test.mjs` disconnect_reason assertion to check the tip contains `device not present`, and add a second case with `disconnect_reason: "future_reason"` asserting the tip still contains `future_reason`. The second case is the one that catches a fix which maps only the four known values and drops the rest.

---

## Sweep verdicts

### Class 6 - non-finite values reaching chart arrays
7 producer sites that write into a plot/digital data array. **Clean.**

| site | verdict |
| --- | --- |
| `plots.js:199` `decodePlotField` (f4) | complies - `Number.isFinite(f) ? f : null` |
| `plots.js:231` `decodePlotSample` post-`*scale` | complies - re-checks after scaling |
| `plots.js:66-69` `parsePlotValue` (ad-hoc + scale literal) | complies - `Number.isFinite(v)` after `parseFloat` |
| `plots.js:305` `mergeSeedSeries` (`/plot/series` values) | complies - own gate, comment names the daemon's missing post-scale check |
| `plots.js:431` `addSample` x arrays | complies - gates `x.host` and `x.tick` |
| `digital.js:41` `digitalIngest` x arrays | complies - same gate at its own boundary |
| `digital.js:75` `lane.vs.push(val)` | complies (transitively) - `val` reaches here only via `routePoints`, and every producer of a `points` entry is gated above |

### Class 16 - one bad item ends the loop
5 loops over daemon-supplied items. **1 violation (W6).**

| site | verdict |
| --- | --- |
| `api.js:578-584` live frame rows | complies |
| `api.js:490-493` backfill rows | complies |
| `api.js:246-248` `!pd` seed rows | complies |
| `api.js:660-663` `feedStaged` | complies |
| `plots.js:372-395` `plotSeed` entry + group loops | **violates** - W6 |

Non-input loops ruled exempt: the render/redraw loops (`terminal.js:307`, `plots.js:745`, `digital.js:311`, `can.js:223`) iterate the client's own models, and `state.js:79` (the 401 retry) is bounded by `TOKEN_PROMPT_MAX`.

### Class 23 - a rebuild path silently un-freezes a paused surface
3 freeze surfaces, 12 writers of frozen content. **Clean.**

- **panes**: writers are `flush` (`terminal.js:308-315`, skips the view while frozen), `rebuild` (`terminal.js:270-293`, bounded by `frozenId` and served from `frozenRows`), `routeLiveRow` (`api.js:122-131`, counts only), the per-pane and global clear (`terminal.js:464`, `terminal.js:573`, both preserve `autoscroll`), `resetForDbReset` (`api.js:166-172`, explicitly resets `frozenId`/`frozenRows`), `setHighRate(false)` (`api.js:78`, routes through the same bounded `rebuild`). Export: `downloadCsv(..., idTo)` carries the freeze.
- **charts**: `addSample` writes only the live rings; every draw goes through `chartDrawData` (`plots.js:708`). `buildUplot`/`redrawPlots` re-enter through `currentData`, so a theme toggle or a series-count change rebuilds from the frozen slice. Export: `exportChart` (`plots.js:914-919`) sends `frozenMaxId` as `id_to`.
- **digital**: every consumer (`drawDigitalLane`, `valueAt`, `setDigitalCursorAt`) goes through `laneDrawData` (`digital.js:113`); `digitalRightEdge` returns the frozen edge. Export: `exportDigital` sends `digitalFrozenId`.

The class's export clause (`exportChart` resolving `last_ms` against now) is fixed at HEAD and pinned by `export_paused_window.test.mjs`.

### Class 25 - a group state that only reaches the members that already existed
3 create sites, 3 destroy sites. **Clean.**

| site | verdict |
| --- | --- |
| create `terminal.js:503` `addPane` | complies - `if (bornPaused()) setAutoscroll(pane, false)` |
| create `plots.js:414` `ensureChart` | complies - `if (bornPaused()) setChartPaused(chart, true)` |
| create `digital.js:148` `addDigitalLane` | complies - lane born after a freeze gets an empty snapshot; the panel's pause is global, so the lane is already inside it |
| destroy `terminal.js:518` `closePane` | complies - `updateShared()` recomputes the label |
| destroy `plots.js:948` `clearAllCharts` | complies - keeps the latch (`clearAllBtn` calls `updateShared`, not `freezeChanged`), so the next chart is born paused |
| destroy `digital.js:613` `clearAllDigital` | complies - the class's inverted case, fixed: it clears the edge and not the pause, with the comment saying so |

### Class 26 - a frozen view re-derived from a rotating ring
3 frozen views, each over a bounded backing store. **Clean.**

| view | backing | verdict |
| --- | --- | --- |
| paused pane | `buffer` (BUFFER_MAX 5000) | complies - `pane.frozenRows` snapshot at `terminal.js:222`, consumed at `terminal.js:273` |
| paused chart | `xsHost`/`xsTick`/`ys` (PLOT_CAP 100k) | complies - `chart.frozen` snapshot at `plots.js:890`, consumed at `plots.js:708` |
| paused digital lane | `lane.xsHost`/`vs` (PLOT_CAP) | complies - `lane.frozen` snapshot at `digital.js:103`, consumed at `digital.js:113` |

`countPending` (`terminal.js:262`) deliberately reads the live `buffer` rather than the snapshot: it counts what arrived *after* the freeze, so the ring is the correct source and its cap is documented as the count's ceiling. Exempt with reason.

### Class 34 - a wire-named key on a prototype-bearing object store
11 name-keyed stores plus 6 localStorage values. **2 violations (W3, W4).**

Stores keyed by wire-supplied names:

| store | verdict |
| --- | --- |
| `plots.js:25` `PLOT_TYPES` | complies - `Object.create(null)` |
| `chrome.js:24` `savedColors` | complies - `Object.create(null)`, values type-checked on load |
| `plots.js:39` `plotDefs`, `:54` `seedMaxId`, `:55` `charts`, `:173`/`:342` `byName`, `:296` `rows`, `:371` `groups`, `:499` `plotChannelMeta` | comply - `Map` |
| `digital.js:20` `digitalLanes` | complies - `Map` |
| `can.js:17` `canRows` | complies - `Map` |
| `state.js:135` `portColorCache` | complies - `Map` |
| `freeze.js:12` `surfaces`, `:71` `out = {}` | exempt - keys are the three hard-coded surface names (`"panes"`, `"charts"`, `"digital"`); nothing from the wire reaches them |
| `digital.js:134` `LANE_KINDS` (plain object, indexed by `lane.kind`) | exempt - `kind` is set only by `routePoints`' `"enum"`/`"bits"` gate, never from a wire string |

localStorage values:

| key | read verdict | write verdict |
| --- | --- | --- |
| `mcuscope.token` | complies (string or null) | complies - guarded |
| `mcuscope.eol` | complies - `EOL_CHOICES.includes` | complies - guarded, validated |
| `mcuscope.colors` | complies - object + per-value `typeof === "string"` | complies - guarded |
| `mcuscope.updateDismissed` | complies - compared, never indexed | complies - guarded |
| `theme` | complies - enum-checked | complies - guarded |
| `cmdHistory` | complies - `Array.isArray` + per-item `typeof` + cap | complies - guarded |
| `canCollapsed` | complies - `Array.isArray` + per-item `typeof` | **violates** - unguarded write, W3 |
| `termState.timeMode` | **violates** - typed but not range-checked, W4 | complies - guarded |
| `termState.panes[]` | complies in effect - `port`/`regex` are coerced harmlessly, `channels` goes through `new Set(...)`, and `addPane` enforces `MAX_PANES` | complies - guarded |

### Class 36 - a periodic catch-up loop without a burst cap
0 sites. **Clean (exempt class).**

Grepped every `while` and every timer in the web UI. The four `while` loops are binary searches (`state.js:249`, `timewindow.js:48`, `timewindow.js:57`, `digital.js:468`), one is a bounded unit reduction (`statusbar.js:63`), and one is the 401 retry bounded by `TOKEN_PROMPT_MAX` (`state.js:79`). No loop compares a schedule variable against now. The seven `setInterval` sites are fixed-period and idle on `document.hidden`; `tickRate` (`api.js:85-94`) re-anchors `rateStart = now` every pass and drops missed beats rather than backfilling them, which is the class's prescribed shape.

### Class 44 - a relative bound re-evaluated on every page of a paged walk
5 request-issuing sites, 1 multi-request loop. **Clean.**

| site | parameters carried | verdict |
| --- | --- | --- |
| `api.js:401-430` `fetchSince` (the only paged walk) | `since_id`, `id_to`, `limit` - all absolute; no `last_ms`, no locally computed now | complies |
| `api.js:303-341` `seedPlotHistory` | 32 parallel requests, each `last_ms` computed once from `anchor.ts` **and** pinned by `id_to=anchor.id`, so the daemon measures every window back from the same line rather than from arrival | complies |
| `api.js:232-256` `seedPlotDefs` | one request, absolute `since_id` floor | complies |
| `api.js:456` first-connect fetch | one request | complies |
| `state.js:307-312` `downloadCsv` | one request; `last_ms` relative, but `id_to` pins it for a frozen surface (class 23's export clause) | complies |
| `settings.js:271` `/sessions?limit=50` | one request, no window | complies |

---

## Test quality

254 tests over 36 files, and a genuinely adversarial suite: it pins the freeze snapshots against a fully rotated ring, drives capture-reset-mid-staging, and asserts the grammar mirrors against a shared fixture. Six mutations applied to a scratch copy and run; **four were not caught**, two were. Tree restored and verified.

Verified uncaught (mutation applied, full suite still 254/254 green):

1. **`statusbar_logic.test.mjs`** - `statusbar.js:346`, replace `if (!renderFaultLogged) { renderFaultLogged = true; console.error(...) }` with a bare `console.error(...)`. "a render fault is not an unreachable daemon" asserts the daemon dot and the kept paint, never the *once* in the guard's own reasoning (*"this runs every 5 s, so a deterministic fault would otherwise fill the console"*). A stated invariant with no mechanism testing it - class 29's shape.
2. **`terminal_logic.test.mjs`** - `terminal.js:172-174`, delete the per-element identity loop in `shiftWindow`. "a forward flush appends the new rows instead of rebuilding the window" drives only the happy slide; the identity check exists for the case its own comment names (*"a VIEW_MAX trim renumbers the whole array"*), and no test drives a flush across a trim boundary. The suite cannot tell a correct reuse from an unchecked one.
3. **`plots_finite.test.mjs`** (and the whole plots set) - `plots.js:35`, `MAX_CHANNELS = 64` to `640000`. No test drives the channel cap at all, so the DOM/heap bound against a device emitting rotating channel names is asserted nowhere. Same holds for `digital.js:17` `MAX_LANES` and `can.js:14` `MAX_CAN_IDS`, though `can_logic.test.mjs` at least renders the `(limit N)` suffix text.
4. **`cmdbar_bounds.test.mjs`** - `cmdbar.js:94`, replace the consecutive-duplicate check with `if (true)`. The file tests the load-side cap and the timeout bound; nothing drives two identical submits, so re-sending the same command ten times would fill the history with ten copies undetected.

Verified caught (so these files are stronger than the list above implies): dropping `disconnect_reason` from `portsSig` fails `statusbar_logic.test.mjs:116`; dropping the write-side validation in `setEol` fails 8 subtests in `state_eol.test.mjs`; dropping `if (anyLive()) allPaused = false` from `freezeChanged` fails "a member created while the UI is frozen is born paused".

Named but not run, one per remaining file:

- `api_backfill.test.mjs` - drop the `let bad = null` / `console.error` reporting in `runBackfill`; the drop is asserted by row count, never by the report.
- `api_backfill_paging.test.mjs` - change `BACKFILL_MAX` from `BUFFER_MAX - 1` to `BUFFER_MAX`; the "one slot short, or the divider is the row that gets trimmed" reasoning is not driven.
- `api_capture_reset_stage.test.mjs` - remove `staging.dropped` from `drainStaging`'s `console.warn`; the count is asserted only via the retained rows.
- `api_db_reset_misfire.test.mjs` - change `noteCapture`'s `typeof id !== "string"` to `id == null`; no test sends a non-string `capture`.
- `api_high_rate_pending.test.mjs` - change `HIGH_RATE_OFF` from 800 to 1999; the hysteresis gap (a burst must not flap the mode) is not driven at the boundary.
- `api_pane_queue.test.mjs` - change the queue trim to `splice(0, 1)`; the test asserts the cap, not that the trim is a block.
- `api_plot_def_seed.test.mjs` - change `PLOT_DEF_LOOKBACK` to `Infinity`; the floor's whole purpose (bounding the regex scan) is a performance claim with no assertion.
- `api_plot_seed.test.mjs` - drop the `q.set("port", channel.port)` line; no test seeds two ports declaring the same channel name, which is the case its comment names.
- `api_ws_backoff.test.mjs` - change `WS_RECONNECT_MAX_MS` to 150000; the cap is not asserted, only the doubling.
- `can_logic.test.mjs` - change `CAN_ALPHA` from 0.3 to 0.5; the EWMA weight is never pinned to a value.
- `chrome.test.mjs` - drop `inp.tabIndex = -1` in `openColorPicker`; the leaked-into-tab-order fix is untested.
- `digital_edge.test.mjs` - change `digitalLast` updates from per-field maxima to whole-object replacement; caught only if a test interleaves host and tick disagreeing in direction.
- `digital_paused_freeze.test.mjs` - drop `digitalFrozenId = state.maxId` from `anchorDigitalFreeze`; the export bound is asserted in `export_paused_window.test.mjs` instead, so this file alone cannot see it.
- `digital_repaint.test.mjs` - change `lane._sizedirty = false` to clear inside `markDigitalDirty`; the "hidden lane keeps the flag" case is the file's own subject, so this one may in fact be caught.
- `export_paused_window.test.mjs` - change `chart.paused ? chart.frozenMaxId : null` to `chart.frozenMaxId`; a live chart's `frozenMaxId` is already null, so the ternary is redundant on the paths tested - a tautology.
- `freeze.test.mjs` - change `minWatermark`'s `known.length ? Math.min(...known) : 0` to `Math.min(...known)`; the all-null case (every member frozen before holding a row) is not driven.
- `plot_grammar.test.mjs` - it drives a shared JSON fixture, so any mutation inside the grammar is caught; the untested mutation is to the *fixture loader* (point it at an empty array and the file passes vacuously).
- `plots_paused_freeze.test.mjs` - drop `chart.dirty = true` from `setChartPaused`; the snapshot is asserted through `currentData` directly, not through a redraw.
- `plots_seed_grammar.test.mjs` - change `seedNameOk`'s length cap from 16 to 1600; the file drives `PLOT_NAME_RE` rejections but not the length bound.
- `plots_seed_paused.test.mjs` - change `seedTargetHasData` to check only the chart and not the lanes; a digital-only stream is the case it exists for.
- `plots_theme_rebuild.test.mjs` - move `chart.theme` back to a module-level variable; the file's own subject is the per-chart stamp, so likely caught.
- `settings_pj.test.mjs` - drop the re-`GET /plotjuggler` in `applyPj`'s catch; "a refused change re-syncs the checkbox" would catch it, so instead: change the `dest || null` coercion to `dest`, which no test drives with an empty field.
- `settings_ports_baud.test.mjs` - change `baud < 1` to `baud < 0`; zero is not driven.
- `settings_ports_bound.test.mjs` - drop the `matched` tracking and always `sel.value = current`; a saved value absent from the device list is the case, and only one row is tested.
- `settings_storage_cap.test.mjs` - change `capMb < 0` to `capMb < -1`; the 0-means-unlimited boundary is driven, -1 is not.
- `smoke.test.mjs` - it is a module-export census, so the mutation it cannot catch is any behavioural one; adding an unused export passes.
- `state_logic.test.mjs` - change `pushBuffer`'s trim threshold from `BUFFER_MAX + BUFFER_SLACK` to `BUFFER_MAX * 2`; "trims in blocks" asserts the post-trim length, not the slack.
- `state_plot_tick.test.mjs` - change `inTickRange`'s upper bound to `Number.MAX_SAFE_INTEGER`; `decodePlotSample`'s own bound would still reject, so the two-gate redundancy is invisible - an assertion shared between two paths.
- `terminal_paused_freeze.test.mjs` - drop `p.frozenRows = null` from `resetForDbReset`; a capture reset while a pane is paused is not driven here (it is in the api set), so this file cannot see it.
- `theme_storage.test.mjs` - change the fallback from `matchMedia(...)` to a literal `"dark"`; no test varies the OS preference.
- `timewindow.test.mjs` - change `timeWindow`'s `width = 0` default to `width = 1`; every test passes a width explicitly.

---

## The two questions

**1. Least confident claim, and how I rechecked it.**

W2, that the web UI genuinely never surfaces `write_failures`. It would be easy to be wrong: the chip already carries three different "drop" badges, and a per-port degraded state could plausibly have been folded into the store-wide `writeErrors` path. I rechecked three ways rather than by reading `renderPorts` again: `grep -n 'write_failures\|last_write_error\|write_failing' host/mcuscope/webui/*.js` returns nothing at all; the `portsSig` array at `statusbar.js:201-204` is the complete list of fields the chips are allowed to depend on and it is not there; and the dot's class expression at `statusbar.js:229-230` is a single line with three inputs, none of them per-port health. I also confirmed the field is really populated and really means what I claim by reading `serial_link.py:1245` and `cli.py:191-196`, where the CLI calls the same state DEGRADED.

W6 is the finding I am least confident *matters*, and I have said so in the finding itself rather than inflating it: I could not build a per-item throw that reaches that loop today, so it is a discipline gap, not a live defect.

**2. What should have been checked that nobody asked for.**

The brief scoped this to the JS, but the highest-severity finding is a **round trip**, and a round trip has two halves. Reading only the browser half would have shown `collectPorts` omitting `eol` and left it looking like a harmless omission - the damage is entirely in the server's default and in `save_ports` deleting the key. The check nobody asked for, and the one worth institutionalising: for every settings dialog that PUTs a whole collection back, diff the fields the dialog *renders* against the fields the corresponding `GET` *returns*, mechanically. That diff at HEAD is exactly one field, `eol`, and it took reading `server.py` and `config.py` to see that it is destructive rather than merely incomplete. The registry has no class for it yet; the invariant would be **"a client that PUTs a whole collection back either renders every field the GET returns, or the server treats the omitted ones as keep-saved"** - the server already knows this rule, because `identify` is written to it three lines away.

Second, smaller: the `docs/SPEC.md` 9.1 line describing the port chip (line 1381) was updated for `disconnect_reason` in the same commit that added the chip, and it is now the only place that enumerates what the chip shows. It does not mention `write_failures` either, so the SPEC and the code agree on an incomplete health surface. A leg that diffs SPEC 9.1's chip description against `/status`'s port fields would have found W2 without reading any JS.
