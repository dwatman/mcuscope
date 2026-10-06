# Fix brief, 2026-10-05 (branch review/2026-10-05)

Shared by every fix batch. Your batch name, files, findings and port range are in your own prompt.

## Inputs

- `triage.md` in this directory: the fix-now table, the batch table, and "Owner rulings" at the end. A ruling overrides the batch table and any fix a report suggests (the batch table's "OP-2 summary (option 2)" is superseded: OP-2 is option 1, N = 256).
- The aspect reports (`fuzz.md`, `security.md`, `resilience.md`, `agentux.md`, `modules.md`, `soak.md`) for each finding's evidence and repro; probe scripts are in `~/tt-data/mcuscope-2026-10-05/<aspect>/`.
- `CLAUDE.md`, `docs/ARCHITECTURE.md`, and the `###` headings of `docs/REVIEW.md` before touching daemon code.
- Interface names fixed in `triage.md` ("Fix batches") so batches can run in parallel: use them exactly.

## Scope

- Edit only the files your batch owns. Several batches run at once on one tree.
- **SPEC.md** is shared: edit only the sections your prompt names. On a failed edit anchor, re-read the file and retry; never rewrite a section you do not own.
- **Existing tests** that pin behaviour you changed: update the assertion minimally, in whatever test file it lives, re-reading the file just before the edit (another batch may have edited it), and list each edit in your report.
- **New tests** go in new files named after the module and behaviour under test (`test_store_retention_floor.py`, `webui_js/app_sidebar_resize.test.mjs`), never after this review round.
- A finding that needs a change in a file you do not own: do your half, and put the other half under "Not done" with the exact change needed.
- `CHANGELOG.md` and `docs/REVIEW*.md` are the orchestrator's: list changelog-worthy changes in your report.
- If a ruling contradicts the code, SPEC or another ruling, or turns out wrong once implemented, stop on that item and report it; do not work around it.

## Quality

- Root cause, once, where every caller routes through it. Grep every caller of what you change.
- Write the test that tries to break the fix, not the one that shows it working; assert on text unique to the path.
- Revert-verify every changed branch: revert it (or mutate it, where nothing was reverted), watch a test fail, restore. A change no test catches is either untested (add one) or redundant (delete it).
- Performance and resource fixes leave a structural check (a query plan, a bound, a count), never a wall-clock threshold.
- Every new loss, refusal or cap is counted or announced (CLAUDE.md conventions).
- Keep comments to the constraint that makes the code non-obvious. No incident narratives, no em or en dashes.
- `uv run python -m ruff check .` clean from `host/`.

## Running things

- Single test files or `-k` selections only; never the whole pytest suite, never the whole JS suite (`node --test <one file>` from `host/tests/webui_js/`).
- Daemons: `--config` on a throwaway TOML with its own `db_path`, all three `MCUSCOPE_*_DIR` in your scratch dir `~/tt-data/mcuscope-2026-10-05/fix-<batch>/`, ports from your range only. Kill by recorded PID; never `pkill -f`/`pgrep -f`. Stop everything before you finish.
- `curl`/`wget` are blocked: use python urllib or httpx.
- No git commits, checkout, stash, reset or restore. To undo your own edit, keep a copy in your scratch dir first (one copy, reused).
- Spawn no sub-agents.

## Report

Write `docs/review/2026-10-05/fix-<batch>.md`:

- Per finding: what changed (`file:line`), the test that pins it, and the revert-verification result. One short block each.
- "Existing tests edited": file, test, why.
- "SPEC edits": section and one line each.
- "Guide wording" (server and store only): text the cli batch should put in `AI_GUIDE`.
- "Changelog": one line per user-visible change.
- "Not done": anything skipped, blocked, or needing another batch's file, with the exact change needed.
- "Doubts": the claim you are least sure of, and what you did not think to check.

Reply with only the report path and a one-line count (fixed / not done).
