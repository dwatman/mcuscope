# Batch webui-settings

Settings dialog, command bar, and the JS test harness (`dom_stub.mjs`, `exportdlg_guards.mjs`) with the tests that lean on it.
Run JS test files one at a time (`node --test <file>`); the one whole-suite run comes at the end, alone.

## Files

- Source: `host/mcuscope/webui/settings.js`, `cmdbar.js`.
- Harness: `tests/webui_js/dom_stub.mjs`, `exportdlg_guards.mjs`, `host/tests/test_webui.py`.
- JS tests you may edit: `cmdbar_detached_pick`, `digital_tick_reset`, `plots_hover_tick`, `plots_pause_edge`, `plots_tick_reset`, `settings_dirty`, `statusbar_session_dialog`, `statusbar_logic`, `plots_seed_grammar`, `settings_loading_hold`, `export_paused_window`, `state_marker_tick`, `settings_revision`, `settings_late_answers`, `api_backfill_paging`, `settings_export_hold`, plus new files.
- Docs: `CLAUDE.md`'s sentence on the stub `<select>` (R27-8 changes it). No SPEC section (SPEC 9 is webui-panes'; hand it any text).

## Findings

### R12-1 LOW: a failed `GET /devices` reads as "no devices"
- Checked: `loadDevices` (settings.js:38-44) returns `[]` on any failure; the attach dialog (statusbar.js:629-636) shows the error.
- Fix: keep the error and show `could not list devices: ...` in the ports section.
- Test: `/devices` answering 500 shows that note; a good answer shows none.

### R23-2 LOW: a re-rendered session row starts a second fetch download
- Checked: fetch-path downloads are held on the button only (settings.js:393-406); `renderSessions()` builds a live button while the fetch is out.
- Fix: hold fetch-path downloads by path too (an in-flight set the row render consults, as nav holds are).
- Test: click export, re-render, click again: one fetch, for both the bundle and the `.db` with a token.
- Keep `settings_export_hold`'s "the bundle is never nav-held" pin true: this is a fetch hold, not a nav hold.

### R61-1 LOW: a token typed while settings load is overwritten
- Checked: `renderToken` (settings.js:122) runs after the awaits in both branches (:781, :784); the token field is deliberately live.
- Fix: render the token once at open, before the awaits; after the load, only when the token section is clean.
- Test: type `typed-secret` while `/config` is held, release (answered and unreachable branches): the field keeps it.

### R71-1 LOW, owner-pick D-17 (dialog half)
- Checked: the dialog bounds retention at 3650, sessions at 1000, the cap at 2^42 (settings.js:707-719); a hand-edited larger value blocks every Storage save.
- Fix (D-17 option A): the dialog uses the bounds daemon-api moves into `config.py` (mirror them with a comment naming the source, or read them from `/config` if daemon-api adds them there).
- Test: a loaded `retention_days` 5000; editing "Keep newest sessions" saves.

### R73-1 LOW: an older `/config` answer overwrites a newer one
- Checked: `refreshConfig` (settings.js:28-35) and `saveAttachedPortToConfig` write `cfg` and the badge with no generation.
- Fix: a generation counter; only the newest-issued read writes `cfg` and the badge.
- Test: the init prime held, the dialog opens on a file needing a restart, the prime then answers without it: the badge stays shown.

### R73-2 LOW: a marker retyped identically during a send is cleared unsent
- Checked: `submitMarker` (cmdbar.js:272) clears by value (`input.value === sent`).
- Fix: clear only if no `input` event arrived since the send (an edit counter).
- Test: send `x`, retype `x` before the ack: the field keeps `x`.

### O-70a LOW: clearing an existing port's alias deletes it on save, silently
- Checked: `collectPorts` (settings.js:604-610) drops any row with no alias.
- Fix: drop only an untouched new row; an existing row with a cleared alias refuses the save naming the row ("use remove to delete a port").
- Test: clear an existing alias, save: error shown, no PUT sent.

### R27-8 MEDIUM (F-JS-3): the stub `<select>` keeps values no option carries
- Checked: `cmdbar_detached_pick.test.mjs:30-41` reads a value the stub keeps (dom_stub.mjs:90).
- Fix: `FakeEl` gets browser `<select>` semantics (a value no option carries reads `""`); the six `index.html` select ids get their static options. Update the `CLAUDE.md` sentence about the lax select.
- Revert-verify: M3 (populateCmdPort drops `opts.push(cur)`) fails `cmdbar_detached_pick`. The leg ran all 172 files against such a stub: only `statusbar_session_dialog` needs N-JS-2.

### R27-9 MEDIUM (F-JS-4): hand-rolled `closest()` ignores its selector
- Checked: dom_stub.mjs:182 returns null; five tests hand-roll `closest: () => x` (digital_tick_reset:95, plots_hover_tick:38, plots_pause_edge:154, plots_tick_reset:74, settings_dirty:182, :188).
- Fix: `FakeEl.closest` walks `parentNode` matching the selector; the tests build a real `.ln` hit element and a field inside `section.cfg-sec`.
- Revert-verify: M4 (`closest(".lnX")` in plots.js:1233) and M5 (`closest(".cfg-secX")` in settings.js:799) fail.

### R27-22 LOW (F-JS-1): the export double runs without port and channel guards
- Checked: exportdlg_guards.mjs:226 passes no `ports` and no caller passes `channels`.
- Fix: derive `ports` from `state.knownAliases` plus the fed rows; pass channels where a test ingests them; `export_paused_window` adds an all-ports pane export.
- Revert-verify: M2 (`port=zz` on every plot export) fails `export_paused_window`.

### R27-23 LOW (F-JS-2): class selectors never checked against `index.html`
- Checked: dom_stub.mjs:153, :222 return a detached element on a miss; test_webui.py:215 checks ids only.
- Fix: test_webui.py scans `querySelector(All)?("\.<class>")` literals in the JS against `index.html` class attributes.
- Revert-verify: renaming `class="side-body"` fails it.

### N-JS-1 LOW: "Not mirrored" list omits the match-budget refusal
- Fix: name `MatchBudgetExceeded` in exportdlg_guards.mjs:5-6.

### N-JS-2 LOW: session dialog test picks devices the stub never lists
- Fix: the `/devices` stub in statusbar_session_dialog.test.mjs lists `/dev/ttyUSB3` and `COM4` (needed once R27-8 lands).

### N-JS-3 LOW: three daemon contracts mirrored by hand, untested
- The config revision rule (settings_revision:42, settings_late_answers:79 vs config.py:560), the 409 text, the `/lines` limit clamp (api_backfill_paging `SERVER_CLAMP`, api.js:440 vs store.py:1945).
- Fix: a Python test (in test_webui.py) reads each JS constant and compares it with the daemon's value.

### N-JS-4 LOW: count-only `console.error` assertions
- Fix: statusbar_logic:549 and plots_seed_grammar:123, :148 assert the message text too.

### R75-1 LOW (JS half): hand-kept lists
- `settings_loading_hold.test.mjs:67` FIELDS: derive from `index.html`'s `cfg*` inputs minus the token (or assert equality in test_webui.py).
- `exportdlg_guards.mjs:109` DECLARED: test_webui.py compares it with `app.openapi()` for the three routes.

### R75-2 LOW (JS half)
- `state_marker_tick.test.mjs:15` `NON_SPACE_WS`: the comment claims `str.split()`'s set; derive the JS `\s` members plus U+001C-001F and U+0085 and say so.

## Added by owner rulings 2026-09-25

- This batch also owns `host/mcuscope/webui/statusbar.js`.
- Reload badge: compare `/status` `version` against the version of the daemon that served the page (the daemon-api batch exposes it), not the first `/status` seen.
