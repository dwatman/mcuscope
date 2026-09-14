# Standards review: origin/main...HEAD (2026-09-15)

## Clean (checked)

- No em or en dashes added anywhere, `docs/review/**` included.
- No new dependencies. No hard-coded paths. No C changes.
- Every new text write passes `newline=`.
- CLI changes (`attach --serial`, `can dump --session`, the `--to`/`-f` refusal, the `daemon start` warning) update AI_GUIDE and the SPEC 4 table in the same commit.
- New refusals are driven by tests, and the tests assert on their messages.
- Class sweeps 53, 57 and 59 found no new instance.

## Hard violations

1. **Registry class 54 (a wire shape built by hand at two sibling sites).** `webui/digital.js:385-397` and `webui/plots.js:1222-1234` each build the `/plot/export` decode/changes/deadband params. Both repeat the "changes forces decode" rule and the same option list. Class 54 counts two JS builders as a violation unless both call one helper.
2. **Owner global, "Code comments ... drop the incident that produced it".** New comments tell the history instead of stating the constraint:
   - `server.py:1655`: `"this run captured nothing" and "you typed the name wrong" were the same ...`. Pasted verbatim at 5 sites (1655, 1702, 1758, 1825, 1871).
   - `server.py:1888-1893`: `names=good,typo used to export ... That reasoning expired when ...`
   - `server.py:2928`: `One condition used to carry two messages' worth of meaning`
   - `cli.py:1157`: `a bare "timeout" made an agent run a second command`
   - `cli.py:1592`: `naming a fixed pair reported a flag that was never given`
   - `tests/webui_js/exportdlg_guards.mjs:3`: `The previous double answered 200 ...`
3. **Owner global Markdown, "A bullet past roughly 200 characters is always wrong".** Added bullets over 200 characters: CHANGELOG 24 of 114 (longest 327), SPEC 29 of 78, REVIEW.md 8 of 32.

## Judgement calls (baseline smells)

- **Duplicated Code.** `server.py` repeats `if span.unknown: return _bad_request(f"no such session: {session}")` in 6 handlers.
  - `_session_range` could refuse itself.
  - Its docstring still says an unknown reference "yields a range that matches nothing", which no caller now relies on.
- **Duplicated Code.** The EOL choice list is now spelled 4 times: `settings.js:14 EOL_OPTIONS` (new), `index.html` `#attachEol` (new), `#cmdEol`, and `state.js:364 EOL_CHOICES`. The orders differ.
- **Duplicated Code (class 19/27 shape).** `exportdlg_guards.mjs` copies server.py's refusal guards by hand ("update it beside them"). It will drift the first time a guard changes.
- **Divergent Change (commit level).** Commit 7e5ae30 is titled "Review registry".
  - It also changes CLI per-port decoding, the `daemon start` warning, export dialog behaviour and CSS.
  - This conflicts with the owner memory "commit per feature".
- **Mysterious Name.** Five new test files are named after the review round instead of what they test.
  - `test_cli_r2026_09_12.py`, `test_daemon_r2026_09_12_*.py`.
  - Six older files already follow this pattern.

## Contradiction

- The owner global says long-lived files carry no incident narrative, at most a one-line "Real instance".
- REVIEW.md's registry format needs a multi-sentence "Bit:" incident per class. The new class 56-61 entries follow that format and break the 200-character bullet rule.
- It is unclear which rule governs the registry.
