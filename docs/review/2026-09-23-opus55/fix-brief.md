# Fix brief, 2026-09-23 (branch review/2026-09-23-opus55)

Shared by every fix batch. Your batch name, files, findings and port range are in your own prompt.

## Inputs

- `triage.md` in this directory: the owner's and orchestrator's rulings, and the batch table. A ruling there overrides the fix a report suggests.
- The aspect reports (`capture.md`, `api.md`, ...) for each finding's evidence and repro; probe scripts are in `~/tt-data/mcuscope-2026-09-23/<aspect>/`.
- `CLAUDE.md`, `docs/ARCHITECTURE.md`, and the `###` headings of `docs/REVIEW.md` before touching daemon code.

## Scope

- Edit only the files your batch owns. Nine batches run at once on one tree.
- **SPEC.md** is shared: edit only the sections your prompt names. On a failed edit anchor, re-read the file and retry; never rewrite a section you do not own.
- **Existing tests** that pin behaviour you changed: update the assertion minimally, in whatever test file it lives, and list each edit in your report. The tests batch owns `conftest.py`, `support.py`, `test_e2e.py`, `test_plot_export_decode.py`, `test_session_bundle.py`, `test_sim_pty.py`, `test_sessions.py`, `test_cli_contract.py`, `test_eol.py`: for those, report the needed change instead of making it.
- **New tests** go in new files named after the module and behaviour under test (`test_store_time_window.py`, `webui_js/terminal_trim.test.mjs`), never after this review round.
- A finding that needs a change in a file you do not own: do your half, and put the other half under "Not done" with the exact change needed.
- `CHANGELOG.md`, `README.md` and `docs/REVIEW*.md` are the orchestrator's: list changelog-worthy changes in your report.
- If a ruling contradicts the code, SPEC or another ruling, or turns out wrong once implemented, stop on that item and report it; do not work around it.

## Quality

- Root cause, once, where every caller routes through it. Grep every caller of what you change.
- Write the test that tries to break the fix, not the one that shows it working; assert on text unique to the path.
- Revert-verify every changed branch: revert it (or mutate it, where nothing was reverted), watch a test fail, restore. A change no test catches is either untested (add one) or redundant (delete it).
- Performance fixes leave a structural check (a query plan, a bound, a count), never a wall-clock threshold.
- Keep comments to the constraint that makes the code non-obvious. No incident narratives, no em or en dashes.
- `uv run python -m ruff check .` clean from `host/` for files you touched.

## Running things

- Single test files or `-k` selections only; never the whole pytest suite, never the whole JS suite (`node --test <one file>` from `host/tests/webui_js/`).
- Daemons: `--config` on a throwaway TOML with its own `db_path`, all three `MCUSCOPE_*_DIR` in your scratch dir `~/tt-data/mcuscope-2026-09-23/fix-<batch>/`, ports from your range only. Kill by recorded PID; never `pkill -f`/`pgrep -f`. Stop everything before you finish.
- `curl`/`wget` are blocked: use python urllib or httpx.
- No git commits, checkout, stash, reset or restore. To undo your own edit, keep a copy first.

## Report

Write `docs/review/2026-09-23-opus55/fix-<batch>.md`:

- Per finding: what changed (`file:line`), the test that pins it, and the revert-verification result. One short block each.
- "Existing tests edited": file, test, why.
- "SPEC edits": section and one line each.
- "Changelog": one line per user-visible change.
- "Not done": anything skipped, blocked, or needing another batch's file, with the exact change needed.
- "Doubts": the claim you are least sure of, and what you did not think to check.

Reply with only the report path and a one-line count (fixed / not done).
