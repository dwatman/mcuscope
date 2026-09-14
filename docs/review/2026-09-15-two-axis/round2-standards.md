# Standards review, round 2: origin/main...HEAD, priority c5bed6f

Clean: no em or en dashes; CHANGELOG, SPEC and REVIEW.md have no added bullet over 200 characters; no mid-sentence wraps in the fix commit's docs; class 54 sweep has one `/plot/export` builder; SPEC 4 and AI_GUIDE carry the `wait` counts; unknown session returns `{"error": ...}` on all six paths.

## Hard violations

1. **Class 27/19: the guard contract covers a hand-picked list, and the fix weakened the double.**
   - `exportdlg_guards.mjs:35` swapped `names is required` for `names: Field required`, so `names=` now passes; the daemon refuses it.
   - Also accepted by the double, refused by the daemon (probe script `r2_guard_probe.py`): `deadband=v=abc`, `deadband=v=inf`, `names=nope`, `session=nosuch`, `since_ts=abc`, `id=0x100,`.
   - The comment on line 3 ("fails when they drift") claims more than the test (`test_webui_js.py:54`) checks. Class 19 says to diff the mirror clause by clause.
2. **Tests that break nothing (owner Tests rule, class 29).** These mutations, run on a copy, left every JS test green:
   - `statusbar.js:658` `fillEolOptions($("attachEol"))` removed: in a browser the select has no options and attach posts `eol=""`.
   - `api.js:313` `.catch(() => null)` removed: if `/status` fails, nothing is seeded.
   - REVIEW_LOG says "every new test fails on hand-revert".
3. **Owner global, incident narratives in comments: first round fixed 6 sites, and at least 10 remain in the range.**
   - `exportdlg.test.mjs:405` still tells the "old double answered 200" story that was cut from `exportdlg_guards.mjs`.
   - `test_plot_export_decode.py:90` still tells the story cut at `server.py:2889`.
   - Also: `exportdlg.test.mjs:266,339`, `chrome.test.mjs:31`, `plots_solo.test.mjs:2`, `statusbar_attach_eol.test.mjs:1-3`.
   - And: `statusbar_logic.test.mjs:86`, `test_cli_r2026_09_12.py:509`, `test_sim.py:867`.

## Judgement calls

- **Test order production cannot give.** `cmdbar_eol.test.mjs:34` expects `["lf","crlf","none",""]`.
  - In `index.html` `""` comes first, and `manual-verify.md` says the choices come "after the bracketed default".
- **Middle Man.** `server.py:1272` `_session_range` only calls `_session_range_for`. That function's docstring (2627) just says "`_session_range` without a Request". Keep one.
- **Duplicated Code.** Two copies of one test, both added in this range: `test_sessions.py:273` and `test_daemon_r2026_09_12_server.py:189`, same five endpoints and same message.
- **Duplicated Code.** The default EOL `"lf"` is still hard-coded outside `EOL_CHOICES`: `cmdbar.js:108`, `statusbar.js:532`, `settings.js:428`.
- **Mysterious Name / Data Clump.** In `plotExportPath(p, v, {names, port, format})`, `v` supplies decode, changes and deadband, but `format` comes separately (plots passes `v.format`).
- Long bullets in the fix commit's own evidence files: `2026-09-15-two-axis/standards.md` (342, 241, 207 characters), `spec.md` (241).

## First-round findings

- Class 54, EOL list, session check, CHANGELOG/SPEC/REVIEW bullets: resolved.
- Incident comments: only partly resolved (hard 3).
- Guard-double drift: only partly resolved (hard 1).
- Divergent commit, round-named test files, REVIEW.md contradiction: left as they are on purpose, with reasons logged.
