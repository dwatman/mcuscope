# Fix batch: web UI chrome (fix-diff F1-F7)

HEAD: `b994076c88c875ad43c30789cda6039d147b7abe` (uncommitted on top).
Revert runner: `~/tt-data/mcuscope-2026-09-24/fixbatch-chrome/revert.py` (22 mutants; each copies the file aside, mutates, runs the named test alone with `--test-name-pattern`, restores). All 22 killed.

## Per finding

### F1 reload badge against the served version
- `index.html:7`: `<meta name="mcuscope-version" content="__MCUSCOPE_VERSION__">`.
- `statusbar.js:99-100`: `pageVersion` starts as the meta's content; the literal placeholder or a missing meta falls back to the first `/status` version.
- Tests:
  - `fixbatch_chrome_reload_stamp.test.mjs`: stamped 0.5.0, first poll 0.6.0 shows the badge; 0.5.0 hides it. Also pins the placeholder meta in index.html.
  - `statusbar_reload_notice.test.mjs`: now runs with the placeholder meta (the fallback path).
- Revert: stamp ignored, placeholder taken as a version, meta removed: each killed.
- Chromium drive against a throwaway `mcuscoped --sim` (the server batch's stamping already in the tree): meta reads `0.5.0`, badge hidden.

### F2 session `.db` navigation queues
- `state.js:405-408`: the navigation `href` gets `wait=1` when the path is `/sessions/{ref}/export` (`&` if it already has a query).
- The bundle is not `streamable`, so it never navigates: it stays a fetch and reports the 503.
  - So widening `SESSION_DB` to `bundle` would have been dead code; not done.
- The token path's `.db` is a fetch too, and also reports the 503.
- Tests:
  - `fixbatch_chrome_export_wait.test.mjs`: the `.db` navigation URL (with and without an existing query), `/lines/export` navigates unchanged, and bundle and token-path `.db` 503s are reported as `... export failed: too many session exports in progress; try again shortly` with no `wait` in the fetched URL.
  - Navigation URL pins updated to `?wait=1`:
    - `settings_export_hold.test.mjs` (`NAV`)
    - `rulings_chrome_export.test.mjs:180`
    - `rulings_chrome_settings.test.mjs:260`
    - `prerelease_chrome_export.test.mjs:115`
    - `fixdiff2_chrome_export.test.mjs:43`
- Revert: no wait, wrong separator, wait on every navigation, wait on the fetch path: each killed.
- Chromium drive: Settings > export saved `SQLite format 3` from `http://127.0.0.1:<port>/sessions/3/export?wait=1`.

### F3 staging overflow marks its hole
- `api.js:736-739`: the trim exempts shed notices as well as capture tokens. Each run of dropped lines becomes a `{gap: run}` notice ahead of the next kept row, so `drainStaging`/`markShed` draw the divider and the chart and lane breaks.
- `api.js:251`: `markShed` counts `min(notice total, row.id - 1 - state.maxId)`, and marks nothing at `<= 0` (this replaces the old backfill-covered guard).
  - Needed because the rows a trim drops are the oldest staged ones, the ones the backfill most likely fetched: without the cap the divider overstated the hole.
  - It is never an undercount, because shed rows are always within the ids between the two rows.
- Tests (`fixbatch_chrome_staging.test.mjs`):
  - An overflow with a staged `{gap: 5}` leaves one divider counting every missing id, and the chart reads `[1, 2, 3, null, next]`.
  - A dropped run the backfill partly fetched counts only `next - 1 - 150`.
- Revert: notices not exempt, no fold notice, cap removed, `n <= 0` return removed: each killed (the last one by `api_ws_gap` "nothing actually missing").
- No change needed in terminal.js or the charts.

### F4 port select tooltip
- `index.html:122`: now says "The bracketed entry is auto: the sole attached port. With several attached, pick one; the bar will not send under (auto)."
- No test (static text; no test reads titles).

### F5 `userText` from the Unicode property
- `state.js:146-149`: `[\p{Default_Ignorable_Code_Point}\p{Bidi_Control}]` with `/gu`, named with `codePointAt` (a tag character shows as `<U+E0041>`).
- Carve-out: U+FE0E/FE0F right after a non-ASCII `\p{Emoji}` is kept, so `❤️` stays readable. After a letter or a digit it is escaped, and U+FE00-FE0D always are.
- Test `state_user_text.test.mjs` (rewritten):
  - Enumerates U+0000-U+10FFFF: every property member is escaped, and nothing else changes. The count found must be above 4000 (it is 4174).
  - Adds an astral case and an emoji selector case.
- Revert: the old hand list, `charCodeAt`, no carve-out, a carve-out taking ASCII emoji, a carve-out taking any VS: each killed.

### F6 test self-contained
- `settings_user_text.test.mjs`: an `openSettings()` helper, which both tests call.
- The ports test passes alone. Reverting the call makes it fail alone.

### F7 surviving mutants
- `api.js:255` `pushRow(g, chartsCleared)`: `fixbatch_chrome_staging` "a divider staged under a clear-all..." asserts `state.anchorTs` stays null. The mutant is killed.
- `cmdbar.js:183` `cmdGen++`: `fixbatch_chrome_cmdbar.test.mjs` holds a `/cmd`, gets the refusal, then releases the command. The strip still reads the refusal, and the mutant is killed.
- `cmdbar.js:177` whitespace-only raw line: `"   "` added to `cmdbar_raw_line.test.mjs`'s as-typed loop. The mutant is killed.
- `api.js:233` guard kept:
  - The test sends `{gap: "5"}, {gap: -3}, {gap: 0}, {gap: 2}` ahead of a 5-id hole (3 ids lost to failed commits, SPEC 3.2) and expects `gap: 2`.
  - Both the accept-zero/negative mutant and the non-integer mutant are killed.
  - `{gap: 0}` alone is an equivalent mutant (adding 0), so the `> 0` part is pinned by the `-3`.

## Verification
- Each new or changed test run alone with `--test-name-pattern`: 17 runs, all pass.
- Whole files run one at a time with `node --test`, all green:
  - `fixbatch_chrome_*` (4 files).
  - `state_user_text`, `settings_user_text`, `cmdbar_raw_line`, `cmdbar_sole`, `cmdbar_detached_pick`, `cmdbar_eol`.
  - `statusbar_reload_notice`, `statusbar_user_text`, `statusbar_logic`.
  - `settings_export_hold`, `rulings_chrome_export`, `rulings_chrome_settings`, `prerelease_chrome_export`, `fixdiff2_chrome_export`, `fixdiff2_chrome_backfill`.
  - `api_ws_gap`, `api_capture_reset_stage`, `api_backfill`, `api_backfill_paging`, `clear_staged_backfill`, `followups_backfill_clear`.
  - `module_load_order`, `fix_browser_ids`, `fixdiff_browser_ids`, `prerelease_chrome_static`, `state_logic`, `smoke`.
- `uv run python -m pytest tests/test_webui.py`: 12 passed.
- The browser drive used Playwright 1.62.0 headless Chromium against a throwaway config and db under the scratch dir.
  - The daemon was killed by PID. The context was ephemeral, so no profile dir was left.

## SPEC wording needed
- 9, the reload badge (line ~1562): "When `/status` reports a version other than the one the daemon stamped into the page it served (`<meta name="mcuscope-version">`), a `daemon updated: reload` badge offers a reload. A page carrying no stamp compares against the first version it saw."
- 3.4, the session export: "The web UI's navigation download sends `wait=1`, since a browser download cannot show a refusal." This goes beside the server batch's `wait` sentence.
- 9, the divider bullet (line ~1605): "`gap: N lines shed by the live stream` for a `{"gap": n}` notice (3.4), or for live rows the page dropped while its first backfill ran. N counts at most the ids missing between the rows on either side."

## Proposed CHANGELOG lines
- Web UI: the "daemon updated: reload" badge compares against the version stamped into the served page, so a tab rebuilt by Back after an upgrade still offers the reload.
- Web UI: a session `.db` download waits for a free export slot instead of failing silently when four are already building.
- Web UI: live rows dropped while the first backfill ran now show as a gap divider and a chart break, and a shed notice staged then is kept; the divider no longer counts rows the backfill fetched.
- Web UI: session names and device strings show every invisible (default-ignorable) character as `<U+XXXX>`, not only the bidi and zero-width ones.
- Web UI: the command bar's port tooltip describes auto as it now works.

## Not done
- The browser drive checked the stamp and the `wait=1` URL, not the Back-after-upgrade scenario or a queued download under a full pool. Both need two daemon builds or a patched pool.
- `api.js` drain still logs `console.warn` for dropped staged rows. It is harmless beside the divider; delete it if the log is not wanted.

## Doubts
- The UI change F2 depends on the server declaring `wait`. The tree has it now (`server.py:1543`); without it every `.db` navigation would be a 422 saved as `.db`. Land both together.
- F1 depends on the server stamping only index.html. If it also replaced the literal in statusbar.js, every page would read as unstamped, silently. The tree's `_stamped_index` is index.html-only.
- The `ponytail:` note at `api.js:733`:
  - Fold notices are not merged, so the staging area keeps one extra object per trim block (1 per 513 rows) while a backfill stalls.
  - Merging was an untestable branch, so it was left out.
- The unreachable `FD2-5` name path `/sessions/run&a#b/export` gets `?wait=1` after the `#`, so it would land in the fragment.
  - This is pre-existing: the navigation to such a name was already broken by the `#`.
  - The UI only ever builds id paths.

## Needs a human in a browser
- Firefox: carried from fixdiff-chrome.
  - Open the UI, navigate away, upgrade and restart the daemon, press Back, and check that "daemon updated: reload" shows.
- Firefox: start 4 session exports on a large capture, then a 5th from Settings. Check it queues and then saves a SQLite file, not JSON.
- Safari, if supported: check the UI loads (the `\p{...}` regex in state.js needs `u`-flag property escapes, Safari 11.1+).
