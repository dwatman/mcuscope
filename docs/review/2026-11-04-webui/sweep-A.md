# Registry leg A: web UI sweep, classes 6, 19, 25, 26, 34, 44, 51, 53, 54, 55

Scope: `host/mcuscope/webui/*.js`, `index.html`, `style.css` (vendor/ excluded). Read-only.
Tree: HEAD 7df68db plus an uncommitted working-tree diff that appeared mid-sweep (another leg is
editing `terminal.js`, `pane.js`, `index.html`: aria-invalid on the regex box, `gapTitle`). The
diff is cosmetic for these classes; line numbers below are the working tree at the end of the
sweep (counts re-run then, unchanged).

Result: 0 violations across all ten classes. One latent drift noted under class 19 (csvField),
not a violation because no reachable input exercises it.

## Class 6 (non-finite values reaching chart arrays)

Command: none (enumerate every producer writing into plot/digital data arrays). Found by
`grep -n "\.push(\|isFinite\|\.data\[\|setData\|\.ys\b\|\.xs\b" plots.js digital.js` plus
`grep -n "ys\.set\|xsHost\s*=\|routePoints(\|digitalIngest(\|addSample(" *.js`. 13 producers.

| Site | Verdict |
|---|---|
| plots.js:96 parsePlotValue (ad-hoc `!p` value, `*scale`) | complies: grammar + `Number.isFinite` |
| plots.js:102 parsePlotAdhoc tick | complies: isDecimalToken + `<= 0xFFFFFFFF` |
| plots.js:249-263 decodePlotSample analog | complies: one isFinite after the scale, drops the point |
| plots.js:244-248 decodePlotSample enum/bits | complies: ENUM_TYPES/BITS_TYPES are integer types, decodePlotField integer path is finite |
| plots.js:231-236 decodePlotSample tick | complies: hex grammar + range |
| plots.js:338 mergeSeedSeries value | complies: `Number.isFinite(pt.value)` |
| plots.js:575 addSample x (host, tick) | complies: isFinite gate covers live and seed x |
| plots.js:616 `ys.push(val)` | complies: val only from the three gated producers above |
| plots.js:629 hold-last push | complies: repeats an already-stored value or null |
| plots.js:654-657 breakChart, 663 addChannel backfill | complies: null only (uPlot gap) |
| digital.js:72 digitalIngest x | complies: isFinite gate; vals routed only from gated producers |
| digital.js:124 pushVertex / 136 breakLanes | complies: inputs are gated x or a prior finite vertex; null break |
| digital.js:155 noteLaneId | complies: tickX/host derived from gated finite x; continueTick/continueHost return finite for finite input (timewindow.js:344, 403) |

Snapshots (plots.js:1361, digital.js:174) copy already-gated arrays.

## Class 19 (two engines validating one thing)

Command: `grep -rniE "mirror|mirrors|mirrored|duplicated like" host/mcuscope/webui --include=*.js | grep -v vendor`
Count: 33. Plus the non-grep duplicated validations: pane regex (JS RegExp vs daemon `regex`),
`markerTick` (state.js) vs `protocol.parse_marker`.

| Site | Verdict |
|---|---|
| digital.js:31, 280, 368 | exempt: UI behaviour mirrored between panels, no validation |
| pane.js:5 | exempt: comment about the pane model shape |
| app.js:74 CAN_CAP_DEFAULT | complies: layout.js:12 `45` equals style.css:245 `var(--can-h, 45%)` |
| app.js:162, settings.js:785, statusbar.js:321 | exempt: UI wiring mirrored, no validation |
| terminal.js:477 MAX_MATCH_LEN=200 | complies: server.py:117 is 200, counted in code points as len(); the daemon's `timeout=` is matched by the wall-clock budget, dialect differences by `regexDialectIssue`/`asciiSpaces` (pane.js:68-127, fixture tests/regex_dialect_cases.json), and a daemon refusal of `match` is retried without it and re-filtered client-side (terminal.js:579-583) |
| can.js:54 parseCanEvent vs protocol.parse_can_event_tokens | complies, diffed clause by clause: 5 tokens; family `!can[1-9]?` = parse_can_family; tick isDecimalToken + `<= 0xFFFFFFFF`; flags `-` or `[xr]+`; id `(0x)?hex{1,16}` = parse_hex_int; id range per ext; rtr single ASCII digit `<= 8`; `-` = no data; data even hex pairs `<= 16` chars |
| can.js:602 csvField vs server._csv_cell | complies in effect, latent drift: `_FORMULA_START` (server.py:3821) also guards a formula char after an embedded `;`, TAB, CR or LF, csvField guards only the leading position, and csv_cell_cases.json has no embedded-separator case. Not a violation: the only caller (can.js:623) passes `e.port`, an alias under `^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$` (config.ALIAS_RE, server _ALIAS_RE) or `"-"`, so no reachable input contains `;`/TAB/CR/LF. The "rule for rule" comment is inaccurate. |
| settings.js:657 baud bounds | complies: `1..MAX_BAUD` = ConfigPortEntry.baud gt=0 le=MAX_BAUD (server.py:419, config.MAX_BAUD 100_000_000) |
| statusbar.js:694 baud bounds | complies: = PortAttach.baud (server.py:266) |
| timewindow.js:377 HOST_STEP_S=10 | complies: = store.WINDOW_TS_SLACK_S 10.0 |
| plots.js:26 PLOT_TYPES | complies: identical table to protocol._PLOT_TYPES, null-prototype |
| plots.js:36 ENUM_TYPES/BITS_TYPES | complies: identical sets |
| plots.js:48 (hooks, not a copy) | complies: state.js computeTick delegates to the decoders |
| plots.js:92-93 parsePlotValue | complies: same regex, same isFinite |
| plots.js:115 parsePlotAdhoc | complies: tick decimal + range; `name=` with name non-empty, PLOT_NAME_RE, `<=16`; duplicate name rejects line; daemon ADHOC_NAMES_MAX overflow files the line as chan debug (serial_link.py:998-1003), which the UI never plots |
| plots.js:167, 175 parseEnumLabels | complies: label regex, `-?\d+`, 20-digit cap (sign stripped), unsigned rejects any `-` |
| plots.js:201 parsePlotDef uniqueness | complies: channel names and lanes in one namespace, as _parse_plot_def_tokens |
| plots.js:252 scale divisor | complies: `1/scale` integer and `< 2^53`; scale 0 falls to multiply in both |
| plots.js:1138 | exempt: rendering approach mirrored from the digital panel |
| plots.js:1529 | exempt: comment about the shared grammar fixture |
| api.js:445 LINES_LIMIT_MAX=1000 | complies: store.py clamp `min(int(limit), 1000)` |
| state.js:172, 175 MAX_BAUD / MAX_TIMEOUT_MS | complies: 100_000_000 / 300_000 equal config.MAX_BAUD, server.MAX_TIMEOUT_MS |
| state.js:225, 229 isDecimalToken | complies: ASCII `\d+`, `<= 20` = is_decimal_token / MAX_DECIMAL_DIGITS |
| state.js:262 computeTick delegated | complies |
| cmdbar.js:13 | exempt: in-RAM history cap |
| markerTick (state.js:275) vs parse_marker | complies: `!m ` prefix, first token `@digits`, 20-digit cap, `<= 0xFFFFFFFF`, text must follow; only called for chan "marker", which the daemon assigns only after parse_marker accepted |

That is 33 grep lines accounted for (several table rows group two or three lines of one site: plots.js:92/93, 167/175, state.js:172/175, 225/229, digital.js x3, the three UI-mirror rows).

## Class 25 (group state reaching only existing members)

Command: `grep -nE 'canModel\(\)|of rows\)|chart\.names\.(forEach|map|filter)|soloShow\(' host/mcuscope/webui/*.js`
Count: 24.

Group operations: pause all (freeze.js latch `allPaused`/`bornPaused`), clear all (terminal.js
clearAll, clearAllCharts, clearAllDigital, clearAllCan). Member births: addPane (terminal.js:845
`bornPaused()`), ensureChart (plots.js:475 `bornPaused()`), addDigitalLane (digital.js:226 empty
frozen snapshot under the panel-level `digitalPaused`), CAN rows (table-level `canPaused`,
snapshot). Destroys: closePane calls updateShared; clears call freezeChanged.

| Site | Verdict |
|---|---|
| api.js:304 seedPlotDefs loop | exempt: `!pd` rows only, caches definitions, creates no member |
| api.js:599 backfill loop | complies: members born through pushRow/canIngest/plotIngest, each birth consults the group state above; clear tokens gate |
| api.js:704 WS frame loop | complies: as above via handleWsRow |
| can.js:52 canModel | complies: one table-level pause reads one snapshot |
| can.js:303, 340, 547, 614, 621 | complies: every reader goes through canModel |
| chrome.js:234 soloShow | exempt: one-shot show-map computation, no latched group state or group label |
| digital.js:327 lane solo | exempt: as soloShow |
| plots.js:407 plotSeed `!pd` rows | exempt: definitions only |
| plots.js:528 syncChartTitle | complies: re-run by renderChans on every addChannel (plots.js:823) |
| plots.js:755 renderChans | complies: called on newChannel in addSample |
| plots.js:799 solo | exempt: as soloShow |
| plots.js:835 paintChanValues, 876 shownCount, 1034/1055 buildUplot, 1136/1154 currentData, 1404 exportChart | complies: derived per call from current names; addChannel triggers renderChans (syncExportBtn, title, count) |
| settings.js:639 | exempt: form rows |
| terminal.js:66 snapshotAnchors | exempt: per-pane snapshot, not a group op |

## Class 26 (frozen view re-derived from a rotated ring)

Command: `grep -nE 'splice\(0,|\.shift\(\)|\.delete\(|\.clear\(\)' host/mcuscope/webui/*.js`
Count: 36.

Frozen views and their backing: paused pane -> `frozenRows` + `frozenAnchors` snapshots;
paused chart -> `chart.frozen`; paused digital -> `lane.frozen`, `ix.frozen`, `digitalFrozen`;
paused CAN -> `canFrozen` (deep entry copies, can.js:558).

| Site | Verdict |
|---|---|
| api.js:151 pane queue trim | exempt: live panes only; a paused pane counts `pending` (api.js:142) |
| api.js:192 tickAnchors.clear | complies: capture reset; frozenAnchors carries `gen` and is ignored across it |
| can.js:151 LRU evict | complies: canFrozen is a copy |
| can.js:286 collapsed set toggle | exempt: UI preference, not an eviction |
| can.js:698 clear | complies: clear empties the snapshot deliberately (SPEC 9.1) |
| chrome.js:50 palette slot memo | exempt: colour memo, no view derived from it |
| chrome.js:176 windowGroups.delete | exempt: chart destroyed |
| cmdbar.js:195 | exempt: command history |
| digital.js:131 lane ring trim | complies: lane.frozen snapshot (digital.js:174) |
| digital.js:160 laneIds trim | complies: ix.frozen snapshot (digital.js:177) |
| digital.js:932-935 clear-all (4) | complies: clear |
| exportdlg.js:95, 122 | exempt: dialog derived-value map |
| freeze.js:63 | exempt: test reset |
| plots.js:635, 636 chart ring trim | complies: chart.frozen snapshot (plots.js:1360) |
| plots.js:1518-1520 clear-all (3) | complies: clear |
| settings.js:362, 424 | exempt: in-flight bookkeeping sets |
| state.js:201 port colour memo | exempt: memo |
| state.js:307 shared buffer trim | complies: paused pane reads frozenRows (terminal.js:416) |
| state.js:328 | exempt: comment |
| terminal.js:334 renderQueue | exempt |
| terminal.js:427 rebuild VIEW_MAX trim | complies: trims the view, source is frozenRows while paused |
| terminal.js:468 flush trim | exempt: live panes only |
| terminal.js:582 `q.delete("match")` | exempt: URLSearchParams |
| terminal.js:616 history splice | exempt: replaces leading dividers, not an eviction |
| terminal.js:762 channel set | exempt: filter toggle |
| timewindow.js:307 tick anchors ring | complies: paused pane snapshot, cap Infinity |
| timewindow.js:359 TICK_EPOCH_CAP, 409 HOST_EPOCH_CAP | exempt (concurring with 2026-09-23 registry-15-28): chart and lane x are computed at ingest and held in the snapshots; only the hover projection (plots.js:1289 xForRow) re-reads epochs, after 1000 resets/steps on one port, with the same exposure for live rows |

## Class 34 (wire-named key on a prototype-bearing store)

Command: `grep -n "JSON.parse\|= {}\|= Object" host/mcuscope/webui/*.js` count 20;
also `grep -nE '(const|let|var) [A-Za-z_]+ = \{|Object\.fromEntries'` count 36; plus every
`x[name]` with a wire or persisted key and every `localStorage.getItem`.

| Site | Verdict |
|---|---|
| api.js:697 WS JSON.parse | exempt: rows array, not a name store |
| can.js:279 collapsed | complies: Array check, strings only, into a Set |
| can.js:363, 430 COL_TITLES | exempt: keys from code |
| chrome.js:26/28 savedColors | complies: null-proto, `#rrggbb` type check |
| cmdbar.js:25 history | complies: Array, strings, capped |
| exportdlg.js:20, 59 `values` | exempt: keys are code-defined option names |
| exportrange.js:31 | complies: validate() mode enum, session string/number re-checked against the /sessions list (exportdlg.js:190), bounds isFinite |
| exportrange.js:59, pane.js:15, state.js:109 | exempt: default parameters |
| layout.js:20 parseLayout | complies: fixed keys, isFinite and range per field |
| layout.js:69/71 parseTitles | complies: null-proto, string, cleaned |
| plots.js:31 PLOT_TYPES | complies: null-proto |
| state.js:489/491 cmdModes | complies: null-proto, value in MODE_CHOICES |
| statusbar.js:309 DISCONNECT_WHY | complies: null-proto |
| statusbar.js:520 aliasMap | complies: fromEntries copied onto Object.create(null); state.js:144-145 initial values null-proto |
| terminal.js:897 termState | complies: timeMode in TIME_MODES, panes through paneCfgFromStorage (type-checked) |
| digital.js:210 LANE_KINDS | complies: indexed only by lane.kind, which routePoints admits only as "enum"/"bits" |
| terminal.js:26 TAG | exempt: `chan` is a daemon-written vocabulary, never device text; `|| chan` fallback |
| timewindow.js:181 TIME_AXIS_LABELS | complies: timeMode validated on load |
| chrome.js:200, layout.js:38/47 key tables | exempt: indexed by KeyboardEvent.key |
| exportdlg.js:32 TITLES, 294 ext | exempt: code-defined kinds/formats |
| remaining object literals (can.js:98, 417, 664; chrome.js:123; digital.js:217, 504; exportdlg.js:282; layout.js:18; plots.js:64, 71, 292, 982, 1033, 1049, 1073; settings.js:160, 648, 762, 884; state.js:110, 142, 156; statusbar.js:703; terminal.js:831, 868) | exempt: records with fixed fields, not name-keyed stores |
| theme.js:16, state.js:17, 462, statusbar.js:117, plots.js:504, app.js:52 (getItem without JSON.parse) | complies: theme in {light,dark}; token opaque; eol isEol; dismissed version compared by equality; titles via parseTitles; layout via parseLayout |

## Class 44 (relative bound re-evaluated per page)

Command: `grep -n "id_to\|since_id\|LINES_PAGE" host/mcuscope/webui/*.js` count 18; plus `last_ms` and `Date.now()`.

| Site | Verdict |
|---|---|
| api.js:293 seedPlotDefs walk | complies: since_id advances to an absolute id, id_to fixed absolute |
| api.js:382, 440, 441, 546, 548 | exempt: comments |
| api.js:408-409 plot series seed | complies: one page per channel; `last_ms` measured from the `id_to` row (store._window_floor), both daemon timestamps |
| api.js:483-484 backfillPath / fetchSince | complies: since_id fixed, id_to walks absolute ids; first page open-topped is the walk's own anchor |
| can.js:46, digital.js:37 | exempt: watermark declarations |
| exportrange.js:50, 53, 56 | exempt: comments |
| exportrange.js:70, 73 | complies: one request, absolute edges |
| terminal.js:574, 575 history walk | complies: absolute id_to/since_id |
| can.js:117 Date.now | complies: only before any daemon timestamp is seen |
| statusbar.js:292 Date.now | exempt: local interval for a local rate |

## Class 51 (boundary fetch whose empty result cannot re-fire)

Command: `grep -n "scrollTop\b.*<\|IntersectionObserver\|atTop" host/mcuscope/webui/*.js`
Count: 2.

| Site | Verdict |
|---|---|
| terminal.js:804 atBottom | exempt: no fetch |
| terminal.js:809 loadHistory | complies: loadHistory walks up to HISTORY_HOPS=5 pages when the filter empties them, nextIdTo moves below the served page (pane.js planHistoryPage), then the hint names the miss and the "search older" button (terminal.js:198-199, 827) walks on; a fetch error ends the walk with reportError |

Aside, not class 51: a paused pane whose rows do not overflow the viewport fires no scroll event at all, so the hint "scroll to the top for older lines" cannot be acted on until a miss shows the button. Not verified in a browser; not reported.

## Class 53 (bound sent as a parameter the peer may not declare)

Command: enumerate `params[...] =`, `p.set(`, `p.append(`, `q.append(` in webui/*.js. Count: 20
(terminal.js:68 is `map.set(`, noise). Plus q.set / URLSearchParams ctor / string-built queries
(api.js:292, 371, 406-414, 483, 561; exportdlg.js:169; settings.js:475; terminal.js:574-575,
635, 637) and 12 POST/PUT/DELETE bodies (cmdbar.js:207, 227, 275; settings.js:249, 458, 693,
888; statusbar.js:204, 234, 537, 548, 559, 711).

All sites: exempt because the web UI is served by the daemon it talks to (same build); a daemon
restarted under another version is announced by the reload badge (statusbar.js:101-104
renderReload). Same ruling as docs/review/2026-09-23-opus55/registry-43-56.md:242.

## Class 54 (one parameter, two hand-built shapes)

Command: per SPEC 3.4 parameter name, `grep -n "<name>" host/mcuscope/webui/*.js`.

| Parameter | Builders | Verdict |
|---|---|---|
| chan (repeated) | terminal.js:636 paneFilterParams only, shared by history (580) and export (692) | complies |
| names (comma) | exportdlg.js:215 plotExportPath only, called by plots.js:1420 and digital.js:514 | complies |
| id (CAN, comma hex) | can.js:685 only (prefill from visibleCanIds); CLI passes the same user string form | complies |
| since_ts / until_ts | exportrange.js params() only | complies |
| since_id / id_to | api.js:293, 409, 483-484; exportrange.js:70, 73; terminal.js:574-575 | exempt: scalar integer passthrough, one form (String(int)) |
| match | api.js:292 (literal `^!pd `), terminal.js:637 | exempt: scalar string; both encodings are valid query encoding |
| port, limit, order, format, session, decode, changes, deadband, name, last_ms | single or scalar | exempt: scalar passthrough |

## Class 55 (refusal keyed on kind name, not the rendering property)

Command: `grep -nE "kind ==|type ==|\.kind\b|\.type\b" host/mcuscope/webui/*.js` count 29; plus
`LANE_KINDS[` dispatch (3, included).

| Site | Verdict |
|---|---|
| plots.js:244, 247 decode branch | complies: mirrors protocol branch by kind, which is the property (bits split, enum unscaled) |
| plots.js:306 routePoints | complies: "drawn as a digital lane" is exactly enum or bits, the only two non-analog kinds |
| plots.js:366, 377, 380, 381 seed name check / kind conversion | complies: the daemon's "bit" per-lane entries are the only grouped kind |
| plots.js:242 `ch.type` | complies: field width/sign key |
| plots.js:678 channelIsInt | complies: integer-ness keys on type f4 and the scale, both checked |
| digital.js:216, 218 isBit grouping | complies: grouping is a bits-only property |
| digital.js:564, 643, 778 LANE_KINDS | complies: dispatch over exactly the two lane kinds |
| exportdlg.js:67, 79, 99 | exempt: option-field type (code vocabulary) |
| exportdlg.js:235, 297 ctx.kind | exempt: title/filename only |
| chrome.js:68, exportdlg.js:69, settings.js:397, 434, 441, 558, 575, 582, 588, digital.js:735 | exempt: DOM `.type` / event type noise |

No client-side refusal keyed on a kind exists (deadband refusal is the daemon's).
