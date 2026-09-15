# Owner rulings: web UI chrome (E-3/E-9, E-8, A-5)

Node suite via `uv run python -m pytest tests/test_webui_js.py -q`: 2 passed (whole node suite green, 21 new tests).

## 1. E-3/E-9: token-less export refusals

Changed:

- `host/mcuscope/webui/state.js:103` api() errors carry `status` (used by item 2).
- `state.js:110` `refusalText(r)`: the daemon's JSON `error`, else `HTTP <status>`; shared by the token path and the preflight.
- `state.js:325-355` `preflight(path)`:
  - A streamed export is fetched with an AbortController; ok headers abort the body, not-ok throws the refusal.
  - A session `.db` (`/sessions/{id}/export`) fetches `GET /sessions?name=<id>` instead and throws `no such session: <id>` on an empty list.
  - A 4 s timer (`STATUS_TIMEOUT_MS`, the statusbar.js setTimeout pattern) aborts and throws `no reply from daemon`.
- `state.js:364` `downloadPath(path, name, label, wanted)`: token-less streamed exports preflight, then navigate; `wanted()` false after the preflight (`:370`) or after the token path's body (`:380`) saves nothing.
- `exportdlg.js:271` passes `() => gen === dialogGen`, so Cancel between preflight and navigation downloads nothing.
- `settings.js:322` session row export and bundle buttons are held disabled while their download is out (double click downloads once). The export dialog was already guarded.
- Preflight uses plain `fetch`, not `authFetch`: a 401 on the token-less path is reported, not prompted.

Tests: `host/tests/webui_js/rulings_chrome_export.test.mjs` (9), plus the two E-9 row tests in `rulings_chrome_settings.test.mjs`.

Existing tests adjusted (their "no fetch at all" assertion contradicts the ruling; now "body never read"):

- `state_logic.test.mjs` "a streaming export with no token is a navigation": counts body reads instead of fetches.
- `prerelease_chrome_export.test.mjs` F-18/FW-9: one fetch per path instead of none.
- `exportdlg.test.mjs` "Enter in an option exports once": fetched+navigated delta 2 (preflight + navigation).

## 2. E-8: config revision

- `settings.js:556-568` `revision` and `putConfig(section, body)`: sends `revision`, adopts `answer.revision` when present, appends `; reopen Settings to load the current file` on a 409 only. Fields are not re-rendered on refusal.
- `settings.js:679` revision set from the GET that opened the dialog (undefined when read-only).
  - A follow-up re-read after a save does not adopt its revision: the other sections were rendered from the older read.
- Call sites `:243` (plotjuggler), `:589` server, `:620` storage, `:639` update, `:655` ports.
- `settings.js:745` attach "save to config" sends `current.revision` from the GET just before.
- Older daemon: `revision` undefined is dropped by `JSON.stringify`, so the body is unchanged.

Tests: `rulings_chrome_settings.test.mjs` E-8 (7), against a double keeping the contract (409 on stale, no check when absent, new revision per PUT, `revisioned: false` older daemon).

## 3. A-5: config_warnings

- `index.html:213` `<ul id="cfgWarnings" hidden>` under the config path; `style.css:409` `.cfg-warn` (no display rule, class 59).
- `settings.js:179` `renderWarnings(list)`: one `li` per entry, hidden when empty, absent or not an array.
  - Fed from the `/status` read `renderDbNow` already makes (`:164`), cleared when that read fails (`:173`) and in the read-only open (`:683`).

Tests: `rulings_chrome_settings.test.mjs` A-5 (3).

## 4. Test doubles: not done

- The existing `/config` and `/status` doubles (`settings_*.test.mjs`, `prerelease_chrome_settings.test.mjs`, `statusbar_attach_eol.test.mjs`) were left as they are.
  - They are not broken by the change, and the brief limits edits to broken existing tests.
  - Without `revision` and `config_warnings` they still model a supported shape (an older daemon).
- The contract double lives in `rulings_chrome_settings.test.mjs`. Folding it into the old files is left to the agent reworking them.

## Revert-verify

Each row: source copied, branch mutated, test file run, source restored from the copy (byte-compared). Script: `~/tt-data/rulings_chrome_mutate.py`.

| Branch | Mutation | Fails |
|---|---|---|
| state.js api `err.status` | removed | settings E-8 tests 2, 3 |
| preflight call before navigation | removed | export 1, 2 |
| preflight `!r.ok` throws | removed | export 1, 2 |
| refusalText JSON `error` | removed | export 1 |
| refusalText `HTTP <status>` fallback | returns "" | export 2 |
| `ac.abort()` after ok headers | removed | export 3 |
| timeout mapped to `no reply from daemon` | rethrow raw | export 4 |
| 4 s timer armed | `timer = 0` | export 4 |
| session `.db` checks `/sessions?name=` | fetch the export | export 8, 9 |
| empty list is `no such session` | never | export 8 |
| `wanted()` before navigation | removed | export 5 |
| `wanted()` before saveBlob | removed | export 7 |
| exportdlg passes `wanted` | removed | export 5, 7 |
| putConfig sends revision | dropped | settings 1, 2 |
| adopt revision from PUT answer | removed | settings 1, 4 |
| 409 hint | never | settings 2, 3 |
| hint only on 409 | always | settings 5 |
| revision loaded at open | removed | settings 1, 3 |
| server / storage / update / ports / plotjuggler via putConfig | each back to `api("PUT", ...)` (5 rows) | settings 1 (each) |
| attach save sends `current.revision` | dropped | settings 7 |
| renderWarnings from /status | removed | settings 8, 9 |
| hidden when no warnings | always shown | settings 9, 10 |
| cleared on /status failure | removed | settings 10 |
| cleared in read-only open | removed | settings 10 |
| absent `config_warnings` tolerated | `warnings = list` | settings 1, 2 (unhandled rejection) |
| session row button held while downloading | guard removed | settings 12 |

## Manual browser checks

- [ ] A token-less `/lines/export` of a large capture: one preflight request aborted at the headers (DevTools shows it cancelled), then the download streams with browser progress, not buffered.
- [ ] Plot export with deadband `ftest:0.5`: the dialog stays open with `plot export failed: deadband needs name=value: ftest:0.5`, no file saved.
- [ ] Daemon `kill -STOP`ped: Export shows `no reply from daemon` after about 4 s; after `kill -CONT` nothing downloads.
- [ ] Settings > Sessions: delete a run in another tab, then click export in this one: chip strip shows `session export failed: no such session: <id>`, no file.
- [ ] Two tabs: Settings open in A, save a section in B, save in A: the 409 text with the reopen hint, fields keep the typing, Escape asks before discarding.
- [ ] `typo.toml` with an unknown key: Settings lists the warning in the warn colour under the path, wrapped on its own row; gone with a clean config.
- [ ] Whether the browser keeps the preflighted connection's server-side query running briefly after the abort (daemon log), and whether a proxy or extension turns the aborted preflight into a visible failed download.

## SPEC and CHANGELOG

- `docs/SPEC.md` 9.1:
  - Export dialog: the token-less preflight and its timeout.
  - Settings sessions: the `.db` session check.
  - Settings: the revision on every save, the 409 hint, and `config_warnings`.
  - The attach "save to config" revision.
- `CHANGELOG.md` `[Unreleased]`:
  - Changed: two sub-bullets under the existing token-less export line.
  - Added: Settings config warnings, and the config revision on saves.
