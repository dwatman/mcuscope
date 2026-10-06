# Fix-diff brief, 2026-10-05 (branch review/2026-10-05, uncommitted tree over 5eaaeeb)

Shared by every fix-diff reviewer. Your area, report file and port range are in your own prompt.

## Goal

Review the round's own fixes (`git diff 5eaaeeb -- <your files>` plus the new untracked files in your area) as the least-reviewed code in the tree (docs/REVIEW.md, leg 7):

- Read every hunk for the platform it was not written on (Windows: paths, file modes, sharing semantics, signals) and against the `###` registry classes in docs/REVIEW.md.
- Check each fix against its finding and ruling (`triage.md`, "Owner rulings" at the end) and the batch report (`fix-<batch>.md`): did it close the class, not just the site? Grep every caller of a changed function.
- New tests: would each fail with its fix reverted? Does it assert on text unique to its path? Does a negative assertion have a positive control? Re-drive the cheap ones by mutating the source.
- A changed line no test catches is a finding (untested, or redundant and to be deleted).

## Rules

- Read-only on the repo apart from your report: no edits, no git state changes. Copy a file to your scratch dir before a mutation and restore it from the copy; confirm with `git diff --stat` that the tree is as you found it.
- Single test files or `-k` selections only; never the whole pytest or JS suite.
- Daemons isolated as in `brief.md` (throwaway `--config`, own `db_path`, `MCUSCOPE_*_DIR` in scratch, your ports only, kill by PID).
- Scratch `~/tt-data/mcuscope-2026-10-05/fixdiff-<area>/`. Spawn no sub-agents. No em or en dashes.

## Report

Write `docs/review/2026-10-05/fixdiff-<area>.md`: findings most severe first (id `FD-<AREA>-n`, HIGH / MEDIUM / LOW / NIT, CONFIRMED or SUSPECTED, `file:line`, failure, repro, suggested fix, class), then "Checked and fine", "Not covered", and the two questions from docs/REVIEW.md.
Reply with only the report path and the count by severity.
