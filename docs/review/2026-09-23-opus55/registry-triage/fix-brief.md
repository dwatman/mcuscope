# Fix batch brief (registry leg, 2026-09-25)

You implement one batch: `registry-triage/batch-<name>.md`. Print `git rev-parse HEAD` first.

## Read first

- Your batch file, then `decisions.md`: the "Owner rulings" sections at its end override any option in a batch file.
- `CLAUDE.md` (conventions, cross-platform mandate), `docs/ARCHITECTURE.md` for the modules you touch, and the `docs/REVIEW.md` class entries your findings cite.

## Rules

- Edit only the files your batch lists. A fix that needs another batch's file: describe the exact change in your report under "Needs another batch", do not make it.
- No git commands that change state (no add, commit, stash, checkout, reset). Other batches edit the same tree at the same time.
- Spawn no sub-agents.
- A contradiction between your batch file, a ruling, SPEC or the code: report it and pick the reading that keeps SPEC and the ruling true; never paper over it.
- Tests: write the test that breaks the fix (see the "Tests" rules in the owner's global instructions, summarised: drive refusals, assert on text unique to the path, positive control for every absence check).
  - Revert-verify every changed branch: in a private copy under your scratch dir, revert or mutate each branch and show a test fails. Never mutate the repo tree.
  - Run single test files only (`uv run python -m pytest tests/<file>`, `node --test <file>`), never a whole suite; ruff clean on files you touched (`uv run python -m ruff check <files>`).
- Any `mcuscoped` you start: `--config` on a throwaway TOML with its own `db_path`, `MCUSCOPE_CONFIG_DIR`/`MCUSCOPE_DATA_DIR` in scratch, a port other than 8558; stop it by PID.
- Scratch: `~/tt-data/mcuscope-2026-09-25/fix-<name>/`, never `/tmp` or the repo. Delete private copies and databases when done; keep nothing else there you would not rerun.
- A CLI change updates `AI_GUIDE` and SPEC 4 only if your batch owns them; otherwise hand the text over under "Needs another batch".
- No em or en dashes anywhere.

## Report

Write `docs/review/2026-09-23-opus55/registry-fix/<name>.md` (under 30 kB):

- One line per finding: id, fixed / not fixed (why), the test that fails with the fix reverted.
- CHANGELOG lines for the orchestrator to merge (user-visible changes only, breaking ones marked).
- "Needs another batch", "Needs Windows", "Needs a browser".
- The two questions from `docs/REVIEW.md`, answered.

Reply with only the report path and a one-line tally.
