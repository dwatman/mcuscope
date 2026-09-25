# Class 78, web UI JS residue

Tree: `bd3eff5` (review/2026-09-23-opus55). Line numbers below are at that commit, before the fixes.

## Count

- Registry grep (REVIEW.md class 78, JS form) over `host/tests/webui_js/*.mjs`: 700 sites (655 at the 2026-09-24 run).
- Mechanical pass, `~/tt-data/mcuscope-tools/sweeps/class-78/c78js.mjs` unchanged: 188 in-test control, 193 same-root control, 128 file-sibling control.
- Residue read by hand: 191 (180 no control found, 11 outside a `test()` body).
- Verdicts: 157 complies, 26 exempt, 8 violate (7 distinct fixes in 6 files).

Rules applied:

- Complies: a positive control shows the watched place receives the thing, in the same test, a sibling test in the file, or a named test in another file (`x-file`).
- Exempt: the assertion is not an absence (a presence written as `hidden false` / `disabled false` / `open ""`, a sentinel `null`, a value), a setup precondition, or a fixture statement.
- All 80 files import `node:assert/strict`, so `equal(x, null)` fails on a missing field (`undefined`); a misnamed field cannot pass a null assertion silently.

## Violations and fixes

Each fix adds the positive control; each control was shown to fail when the watched place is broken, by mutating a private copy of the tests (`node --test --test-name-pattern=<test> <file>`, rc=1, failing with the control's own message).

| Site | Defect | Fix | Mutation (copy) | Result |
|---|---|---|---|---|
| `clear_staged_backfill:525` | `digitalLanes.has("p1\|s0\|st")`: lane keys are `port\|name` (`digital.js:60`), so the key names no lane and the assertion could never fail | key `p1\|st` (the seed's enum channel); control in the uncleared pass: `has("p1\|st")` true | control key back to `p1\|s0\|st` | fails, "control: the seed built no lane" |
| `clear_staged_backfill:553` | "no old rows in buffer" with nothing showing old rows ever land | after the first settle: `buffer.some(raw === "old")` | stored rows `raw: "OLD"` | fails, "control: the old capture's rows never landed" |
| `digital_tick_reset:88` | `lineTo` at y 26 asserted absent; no lane in the fixture is ever low | stream gains bit lane `b1` (always low); `Y_LO = 26`; its recorder must hold a `lineTo` at `Y_LO` | `Y_LO = 27` (a lane-height change) | fails, "control: a low level is not drawn at Y_LO" |
| `plots_fold_cue:58` | `layouts` counts a rect read nothing showed the cue makes | after the tick loop, a scroll must count one | `watch()` hooks `btn` instead of the scroller | fails, "control: the cue's own layout read is not counted" |
| `settings_offline:72` | focus hook never shown to record | `document.getElementById("cfgToken").focus()` must set `focused` | hook put on `cfgHost` | fails, control message |
| `settings_loading_hold:166, 178` | `tokenFocus` hook never shown to record | same control at the end of both tests | hook put on `cfgHost` | both fail, control message |
| `terminal_regex_count:62` | `reads` counter never shown to count a rescan | `reads = 0; rebuild(pane)`, then `reads > 0` (before `fullCount`, which reads the rows itself) | getter defined on `chan2` | fails, "control: rebuild's full recount read no counted row" |

Unmutated, each edited file passes whole and each changed test passes alone (`--test-name-pattern`): clear_staged_backfill 26/26, digital_tick_reset 4/4, plots_fold_cue 4/4, settings_offline 5/5, settings_loading_hold 8/8, terminal_regex_count 5/5.

## Verdicts

| Site (`<file>.test.mjs:line`) | Verdict | Control or reason |
|---|---|---|
| `api_staging_overflow:126` | C | x-file state_logic:113 (pushBuffer sets anchorTs); :125 shows the rows reached the buffer |
| `api_plot_seed_ports:49` | C | :48 the same `seen` log holds the /plot/series requests |
| `api_backfill_clear_tokens:128` | C | :143 the backfill fills a pane the clear does not cover |
| `api_ws_gap:92` | C | :73-74, :145 a gap divider lands in `buffer` |
| `api_ws_gap:126` | C | :73-74, :145 |
| `api_high_rate_pending:63` | E | setup: the stubbed backfill is empty; later tests feed rows and count them (:77) |
| `api_high_rate_pending:90` | C | :87 the same `pending` counter reaches 3010 on the paused pane |
| `can_export_port:65` | C | :55 two boards build `opt("port")` |
| `api_pane_queue:66` | E | setup: the stubbed backfill is empty |
| `api_pane_queue:93` | C | :92 the live pane's queue receives the burst |
| `api_pane_queue:94` | C | :92 |
| `chrome_radios:134` | C | :127 enter() answers true, :129 pressed() counts 2 |
| `chrome_radios:148` | C | :127, :129 |
| `chrome_radios:149` | C | :129 |
| `app_layout_corrupt:16` | C | x-file app_layout:20 a saved layout writes `--can-h` |
| `cmdbar_sole:126` | C | :62 the same `posts` log records POSTs; x-file cmdbar_space_trim:46 the marker URL is "/marker" |
| `cmdbar_sole:145` | C | :138 the test typed into the same field first |
| `can_table:229` | C | seen.refusals: x-file exportdlg_deadband_refusal:67 (declared positive control) |
| `app_resizer_keys:62` | C | :25 key("ArrowLeft") answers true |
| `app_resizer_keys:63` | C | :25 |
| `app_resizer_keys:64` | C | :25 |
| `api_backfill_paging:84` | C | :112-113 an unclosable gap is marked |
| `api_backfill_paging:99` | C | :112-113 |
| `api_backfill_paging:150` | C | :112-113 |
| `api_backfill_paging:169` | C | :112-113 |
| `digital_shown_trimmed:193` | C | x-file digital_repaint:116 digitalIngest keys lanes "port |
| `chrome:122` | C | :105 a shift-click drives a live selector's callback |
| `chrome:146` | E | presence: hidden false asserts the chip is shown |
| `can_logic:343` | C | :205 a collapse writes `canCollapsed` |
| `digital_paused_freeze:112` | C | :68 laneDrawData holds the 10 frozen vertices |
| `digital_paused_freeze:121` | C | :80 the paused view survives the ring rotating, only through lane.frozen (digital.js:188) |
| `exportrange_shown_params:17` | C | :10, :13 the same params() emits both bounds |
| `api_seed_clear_reset:75` | C | :92 freshPage seeds a chart when not cleared |
| `digital_tick_reset:88` | V | y 26 had no control: FIXED, low lane b1 must draw at Y_LO |
| `digital_clear_segments:60` | C | :64-69 post-clear ingests rebuild the lanes and draw them |
| `digital_zoom_export:45` | E | presence: the helper asserts the shown mode is offered |
| `digital_zoom_export:129` | C | :45 exportShown reads the same expModeShown.disabled as false |
| `clear_staged_backfill:126` | C | :119-121 control test, the undisturbed seed builds chart and lane |
| `clear_staged_backfill:133` | C | :119-121 |
| `clear_staged_backfill:139` | C | :119-121 |
| `clear_staged_backfill:525` | V | key "p1 |
| `clear_staged_backfill:553` | V | no control that "old" rows reach `buffer`: FIXED, asserted after the first settle |
| `digital_repaint:126` | E | presence: null is the break sentinel; strict equal fails on undefined |
| `index_a11y_static:22` | C | :20 at least 9 slots matched in the same html |
| `exportrange:59` | C | :61 params() emits `session` once picked |
| `exportrange:83` | C | :78-79 the same params() emits both bounds |
| `exportrange:132` | C | :80 id_to sent with a watermark |
| `plots_host_step:44` | E | presence: null is the break sentinel |
| `can_head:43` | C | x-file can_logic:40 an ingest fills canRows |
| `can_head:95` | E | presence: hidden false asserts the tag is shown |
| `can_head:261` | C | :260 the same html matched positively |
| `pane_regex_dialect:25` | C | :34-39 refused cases are refused through the same check |
| `pane_regex_dialect:65` | C | :34-39 |
| `plots_event_tokens:27` | C | :29-30 a valid !pd parses and decodes |
| `plots_event_tokens:28` | C | :29-30 |
| `plots_seed_grammar:67` | C | :37, :56 valid seeds chart under "- |
| `plots_seed_grammar:77` | C | :87 a valid seed lane "- |
| `export_paused_window:127` | C | :99, :116, :142 a paused chart sends id_to |
| `export_paused_window:178` | C | :170 a paused pane sends id_to |
| `export_paused_window:199` | C | :186 a real port is sent |
| `export_paused_window:217` | C | :188 a filtered pane sends chan |
| `export_paused_window:218` | C | :187 a filtered pane sends match |
| `export_paused_window:225` | C | :187, :238 |
| `plots_decimate:139` | E | value: index 0 kept is a presence |
| `layout:60` | C | :63 the same parser keeps a usable title |
| `layout:61` | C | :63 |
| `layout:66` | E | value: a null prototype is the property asserted; :65 reads the parsed data |
| `plots_fold_cue:58` | V | `layouts` never shown to count the cue's read: FIXED, a scroll must count one |
| `plots_zoom:149` | E | fixture statement, not a claim about behaviour |
| `plots_seed_paused:69` | C | x-file freeze:46 anyLive() answers true |
| `plots_solo:94` | C | :95 the same class reads true on a hidden row |
| `settings_offline:71` | E | presence: the token save stays enabled; :70 lists the disabled ones |
| `settings_offline:72` | V | focus hook never shown to record: FIXED, a focus() via getElementById must record |
| `plots_export_button:86` | C | :130-135 exportChart then expGo sets seen.lastUrl in the same file |
| `plots_export_button:104` | C | :130-135 |
| `plots_export_button:136` | C | :122-123 changes on sends decode and changes |
| `plots_export_button:137` | C | :122 |
| `settings_ports_refusals:82` | C | :91 a valid save is recorded in `puts` |
| `settings_config_gen:78` | C | :54 the code's badge write goes through the same `hidden` setter |
| `plots_pause_edge:96` | C | seen.refusals: x-file exportdlg_deadband_refusal:67 |
| `plots_pause_edge:130` | C | x-file freeze:98 bornPaused() answers true |
| `plots_pause_edge:134` | C | :136 the live chart draws its own 20 samples |
| `state_decoder_tick:25` | C | x-file state_line_tick:64 an accepted line anchors |
| `settings_loading_hold:84` | E | presence: the token box stays enabled; :81 the held fields read disabled |
| `settings_loading_hold:166` | V | focus hook never shown to record: FIXED |
| `settings_loading_hold:178` | V | same hook: FIXED |
| `settings_pj:86` | C | :107, :133 the render writes checked=false over a typed true |
| `settings_pj:107` | C | :96 the test typed true into the same box first |
| `settings_pj:133` | C | :127 |
| `settings_pj:135` | C | :146-147 save as default PUTs /config/plotjuggler |
| `settings_pj:159` | C | :146-147 |
| `plots_shown_ids:43` | E | presence: the helper asserts the shown mode is offered |
| `plots_shown_ids:49` | C | seen.refusals: x-file exportdlg_deadband_refusal:67 |
| `plots_shown_ids:144` | E | presence: null is the gap sentinel |
| `state_line_tick:61` | C | :64 positive control in the same test |
| `settings_revision:120` | C | :131, :145, :168 refusals land in the same slots |
| `settings_revision:177` | C | :117 revisions are sent when the daemon has them |
| `settings_revision:213` | C | :204 warnings render into the same box |
| `settings_revision:281` | C | :131, :293 |
| `statusbar_attach_eol:85` | C | :82 via attach():38 the serial was typed into the same field |
| `statusbar_refresh_fresh:53` | C | :43 the chip was present before the detach |
| `exportdlg:115` | C | :104 changes sent when ticked |
| `exportdlg:116` | C | :105 deadband sent with changes |
| `exportdlg:126` | C | :103 decode sent by default |
| `exportdlg:141` | E | presence: open "" means the dialog is open |
| `exportdlg:225` | C | :207 an id list is sent |
| `exportdlg:235` | C | :141, :357 the same attribute reads open |
| `exportdlg:287` | C | :149 clock mode sends since_ts |
| `exportdlg:357` | E | presence |
| `exportdlg:364` | C | :357 the same dialog was open |
| `exportdlg:383` | C | :381 htmlFor set on a field label |
| `exportdlg:408` | C | x-file state_download_preflight:110 expGo reads disabled while waiting |
| `statusbar_proto:59` | E | value: a null prototype is the property asserted |
| `statusbar_proto:60` | E | value |
| `settings_export_hold:158` | C | :155-156 the release fires only through the recorded timer |
| `settings_export_hold:245` | C | :155-156 |
| `settings_export_hold:292` | C | :155-156 |
| `settings_export_hold:372` | C | :238, :154 held() reads true on a held row |
| `state_logic:266` | C | :284 the prompt stub counts calls |
| `state_logic:285` | C | :284 in the same test |
| `state_logic:379` | C | :218 the same createElement hook records the navigation anchor |
| `settings_storage_cap:66` | C | :56 a valid value returns its PUT |
| `settings_storage_cap:85` | C | :56 |
| `terminal_history:378` | C | :58 historyIdTo answers 499 |
| `plots_chrome:44` | E | value: the uPlot option built from the chart, read off the instance :43 |
| `plots_chrome:85` | C | :82 a unit writes the label |
| `plots_chrome:148` | C | :42-46 an analog stream builds a chart in the same file |
| `plots_chrome:166` | C | :163 the same css matched positively |
| `state_eol:51` | C | :36 setEol stores the key |
| `state_eol:68` | C | x-file cmdbar_eol:48 a valid stored value survives the load |
| `state_eol:69` | C | x-file cmdbar_eol:48 |
| `plots_ports:167` | C | x-file can_export_port:55 the same lookup finds expOpt_port for two boards |
| `plots_finite:108` | C | :114 the finite twin lands |
| `plots_finite:136` | C | :143 |
| `plots_finite:140` | C | :143 |
| `plots_finite:217` | C | :114, :235 a legal definition builds its chart |
| `plots_finite:218` | C | :114, :235 |
| `plots_finite:230` | C | :235 |
| `plots_finite:242` | C | :75 stream0 asserts its baseline sample landed |
| `plots_finite:243` | C | :75 |
| `plots_finite:303` | C | :306-307 lanes land; :114 analog charts build |
| `plots_finite:335` | C | :339-340 |
| `plots_finite:354` | C | :364-365 |
| `plots_finite:359` | C | :364 |
| `terminal_createpane:89` | C | :75-81 control test, an undisturbed page lands 200 rows |
| `terminal_createpane:91` | C | x-file terminal_history:156 a landed page moves historyNext |
| `terminal_createpane:100` | C | :75-81 |
| `terminal_createpane:111` | C | :75-81 |
| `terminal_createpane:121` | C | :75-81 |
| `terminal_createpane:130` | C | :75-81 |
| `plots_zoom_export:32` | E | presence: the helper asserts the shown mode is offered |
| `plots_zoom_export:88` | C | seen.refusals: x-file exportdlg_deadband_refusal:67 |
| `plots_zoom_export:109` | C | :32 exportShown reads the same field enabled |
| `plots_zoom_export:143` | C | :32, :136 |
| `statusbar_late_answers:68` | C | setup; the test at :71 passes true, so the box was ticked first |
| `statusbar_late_answers:89` | C | x-file statusbar_logic:493, statusbar_attach_open:93 dlgErr receives refusals |
| `statusbar_late_answers:104` | C | x-file statusbar_session_dialog:102 sesErr receives a refusal |
| `settings_late_answers:274` | C | x-file settings_pj:132 cfgPjErr receives a refusal |
| `settings_late_answers:284` | C | x-file settings_pj:132 |
| `settings_late_answers:309` | C | x-file settings_pj:132 |
| `settings_late_answers:349` | C | x-file settings_save_reread:290-301 (declared positive control) |
| `settings_ports_baud:62` | C | :86, :97 a valid save PUTs /config/ports |
| `settings_ports_baud:75` | C | :86 |
| `terminal_logic:80` | C | x-file terminal_createpane:97 autoscroll reads true after a jump |
| `terminal_logic:81` | C | x-file terminal_self_scroll:67-78 a code move marks its scroll event ours |
| `terminal_logic:309` | C | :304 the test set viewH 240 first |
| `terminal_regex_count:62` | V | `reads` never shown to count a rescan: FIXED, a rebuild must read counted rows |
| `terminal_self_scroll:55` | E | setup: the helper set autoscroll false itself |
| `terminal_self_scroll:74` | C | x-file terminal_createpane:97 |
| `settings_ports_eol:110` | C | :68 a valid save is recorded in `puts` |
| `theme_a11y_static:87` | E | static scan of a file readFileSync reads (a missing file throws); behaviour pinned by statusbar_session_dialog:59 |
| `state_plot_tick:67` | C | :55 plotAccepts() reads true for valid lines through charts.size |
| `state_plot_tick:68` | C | :73 the real line anchors |
| `state_plot_tick:73` | E | value: a zero offset is the anchor's positive control for :68 |
| `state_plot_tick:81` | C | :55 |
| `state_plot_tick:82` | C | :73 lineTick of a real line is a number |
| `terminal_paused_anchors:61` | C | :46 frozenAnchors holds a map while paused |
| `style_layout_static:37` | C | :33 the same rules() and hides() find the narrow rule |
| `statusbar_session_dialog:59` | C | x-file state_logic:284 the globalThis.prompt stub receives calls |
| `statusbar_session_dialog:61` | C | :79 the same field carries typed text in a sibling test |
| `statusbar_session_dialog:62` | C | :159 the same `posts` log records a POST |
| `statusbar_session_dialog:105` | C | :120-133 one post while in flight, through btn.disabled |
| `terminal_shown_first_id:39` | C | seen.refusals: x-file exportdlg_deadband_refusal:67 |
| `terminal_shown_first_id:46` | C | :35 shown mode sends since_id |
| `statusbar_logic:199` | E | vacuous by construction and says so (:211-212); :213 is the controlled form |
| `statusbar_logic:213` | C | :202 the box was ticked before the reopen |
| `cmdbar_eol:26` | E | value: "" is the default option's real value |
| `digital_shown_trimmed:49` | C | seen.refusals: x-file exportdlg_deadband_refusal:67 |
| `digital_zoom:44` | E | presence: the cursor is shown |
| `digital_shown_ids:49` | C | seen.refusals: x-file exportdlg_deadband_refusal:67 |
| `terminal_toolbar_static:41` | C | :40 the same group lists its buttons; :42 closepane is in the template |

## The two questions

1. What am I least confident about?
   - The 30 cross-file (`x-file`) rulings: the control sits in another file with its own fixture, so it proves the watched place can receive the thing, not that this fixture routes it there.
     Rechecked one by driving it: `statusbar_session_dialog:59` (`prompts` 0) fails when a copy of `statusbar.js` calls `window.prompt` on the session button (`window` is `globalThis` in the stub, `dom_stub.mjs:319`).
     The other 29 are reasoned, not driven.
   - The 509 mechanical rulings were not re-sampled this round; the 2026-09-24 sample (10 JS same-root sites) is the only check on them.
   - `theme_a11y_static:87` (exempt) greps for `window.prompt` only; a bare `prompt(` would pass it. The behavioural test above covers both forms.
2. What should we have checked and have not?
   - Negative assertions the registry grep cannot reach: `assert.ok(x === 0)`, `notEqual`, `!some(...)` inside a message-less `ok`, and counts compared with `<=`. The regex matches only the listed literal forms.
   - The mutation checks broke the observation point, not the product. Only one product mutation was run (`digital.js` pen lift; it fails on the stroke count at :86 before reaching :88), so which of the fixed negatives catch a real regression on their own is unmeasured.

## Scratch

`~/tt-data/mcuscope-2026-09-25/c78-js/`: `s78mjs.txt` (grep), `c78js_out.txt` (mechanical pass), `residue.txt`, `ctx.mjs` (per-site test bodies), `verdicts.txt`, `mut/` (the private mutation copy: copies of `host/tests/webui_js/` and `host/mcuscope/webui/`, about 200 files; not yet deleted, a recursive delete needs the owner's confirmation).
