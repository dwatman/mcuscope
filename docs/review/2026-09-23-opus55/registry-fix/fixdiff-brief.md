# Fix-diff brief (registry fixes)

Review the registry fix commit's diff for defects the fixes introduced. Read-only: edit nothing but your report. No git commands that change state. Spawn no sub-agents.

## Scope

- The diff: `git show <FIX> -- <your paths>` (the commit id is in your prompt). Read the changed code in full context, not only the hunks.
- Inputs that say what each change was meant to do: `registry-triage/batch-*.md`, `registry-triage/decisions.md` (owner rulings at its end override batch options), `registry-fix/<batch>.md`.
- Partitions:
  - `daemon`: `host/mcuscope/{server,store,config,pjstream,daemon,sim,_stdio,update_check,serial_link,link,protocol,render}.py` and the tests that pin them; SPEC 2, 3, 7.
  - `cli`: `host/mcuscope/cli*.py`, `host/tests/support.py`, the CLI tests; SPEC 4, `AI_GUIDE`.
  - `webui-fw`: `host/mcuscope/webui/*`, `host/tests/webui_js/*`, `firmware/**`, `.github/workflows/*`, `tools/*`, `host/pyproject.toml`; SPEC 5, 9.

## What to look for

- A fix that is wrong, partial (a sibling caller left out), or breaks another contract; SPEC or `AI_GUIDE` drift from the code; a changed branch no test reaches (revert-verify a sample in a private copy under `~/tt-data/mcuscope-2026-09-25/fixdiff-<part>/`).
- The owner's rulings implemented as ruled, notably D-9 (only U+0020 is space), D-13 (the monitor never programs hardware), D-16 (the version header, no backward compatibility).
- The registry classes in `docs/REVIEW.md` against the new code.
- `daemon` only: draft registry class 87, "a response header added by middleware misses the 500 Starlette sends outside all middleware" (see `registry-fix/daemon-api.md`), with its sweep, run it, and put the draft entry text in your report.

## Rules

- Single test files only, never a whole suite. Any `mcuscoped` you start: a throwaway `--config` with its own `db_path`, `MCUSCOPE_*_DIR` in scratch, a port other than 8558; stop it by PID.
- Delete your private copies and databases when done.

## Report

`docs/review/2026-09-23-opus55/registry-fix/fixdiff-<part>.md`, under 30 kB: findings first (id `X-<part>-<n>`, severity, `file:line`, concrete failure, driven or reasoned), then "Checked, nothing found", then the two questions from `docs/REVIEW.md`. Reply with only the path and a one-line tally.
