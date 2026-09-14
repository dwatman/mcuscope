# Spec review: origin/main..d2bbee3

All 16 owner-notes items trace to code (can.js, cmdbar.js:86-93, timewindow.js:233, sim.py:606-628, digital.js).
The SUMMARY "SPEC vs code contradictions" list is closed.
Every CLI option added (`attach --serial`, `can dump --session`, `-o -`) is in AI_GUIDE and SPEC 4.
`test_webui_js.py` and `test_cli_contract.py` pass (26 tests).

## (a) Missing or partial

- improve-host.md #3: "When `--send` was used, append `(sent 1, failures 0)`".
  - cli.py:1163 prints only the pattern, port and time.
  - The counts are printed only on the `--repeat-ms` path (cli.py:1147-1151).
  - batch-cli-report.md:27 calls the clause "optional", but the proposal does not.
- Two places where the sources disagree. SPEC now follows the code in both, so the owner should pick one:
  - SUMMARY.md:87 says "disable `#cmdInput` and `#markerBtn`". The marker stays enabled (cmdbar.js:60), and SPEC.md:1497 was rewritten to match.
  - SUMMARY.md:45 says "threshold `max(3, 5 * period/1000)`". can.js:477-478 uses a 1 s floor (2 s for crit).

## (b) Scope creep

- sim.py:615: the `ramp` wrap at 256 also applies to `--plot`, but owner-notes.md:4 asked for it under "Demo".
  - CHANGELOG says "`--plot` is unchanged" for the slowed signals.
  - SPEC 8's `--plot` bullet does not mention the wrap.
- Commit 7e5ae30 is titled "Review registry" but also ships CLI per-port decode, the `daemon start` config warning and export dialog fixes. This is commit hygiene, not a spec breach.

## (c) Implemented but looks wrong

- SPEC.md:1427: "meets WCAG AA (4.5:1) on every surface it sits on ... and so does the light accent as text."
  - Light `--accent` #0b7a8d measures 4.46:1 on `--panel-2`.
  - As text on `--accent-soft`, it measures 4.27:1 over white and 3.95:1 over `--bg`: `.iconbtn.on` (style.css:131) and `table.can .byte.chg` (style.css:279).
- SPEC.md:726: the formula guard covers "`=`, `+`, `-`, `@` or a control character".
  - server.py:2743 guards only `\t` and `\r` among control characters.
- SPEC.md:1590 describes a shortcut instead of fixing it: "an unfiltered `/plot/channels` names only the port of each name's newest sample, so the page seed restores that port's history for a shared name and the other board's fills in live".
  - SUMMARY S-F6 "Fix (right)" wanted per-port keying.
  - api.js:317 seeds from the unscoped list, so after a reload the second board's stored history for a shared name never comes back.
  - Querying `/plot/channels?port=` per port would fix it.
  - This looks like SPEC edited to paper over the gap.
