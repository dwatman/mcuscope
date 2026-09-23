# Review brief, 2026-09-23 (branch review/2026-09-23-opus55)

Shared by every check agent. Your aspect, report file and port range are in your own prompt.

## Goal

Find real defects in your aspect: wrong behaviour, data loss, crashes, security holes, contract breaks, misleading output.
The project has had many review rounds; `docs/REVIEW.md` lists 80 known defect classes (skim the `###` headings so you recognise a repeat).
Do not re-run old sweeps for their own sake: the value of this round is what earlier rounds missed.
A new instance of a known class still counts; say which class.

## Grounding

- Read `CLAUDE.md` and `docs/ARCHITECTURE.md` first, then the SPEC sections and code your aspect covers.
- `docs/SPEC.md` is the contract: when code and SPEC disagree, report which one is wrong and why.
- Out of scope: the "Phase P2 backlog" in `docs/IMPLEMENTATION_PLAN.md`, flash/reset features, Safari.

## Evidence

- Drive it, do not just reason about it. A finding with a repro (command, script, output) beats one argued from reading.
- Mark each finding CONFIRMED (driven) or SUSPECTED (reasoned; say what would confirm it).
- Windows cannot be tested here; flag Windows-only suspicions as such.

## Rules

- **Read-only on the repo.** Edit no tracked file; your only write in the repo is your report. No git commits, checkout, stash or reset.
- Probe scripts, configs, databases and logs go in your scratch dir `~/tt-data/mcuscope-2026-09-23/<aspect>/`, never `/tmp` and never the repo.
- Daemon isolation: every `mcuscoped` you start uses `--config` on a throwaway TOML with its own `db_path` in scratch, plus `MCUSCOPE_DATA_DIR`, `MCUSCOPE_CONFIG_DIR`, `MCUSCOPE_CACHE_DIR` pointing into scratch. Never touch the real daemon on port 8558 or `/dev/ttyACM0`.
- Use only the ports in your range (daemon and sim alike). Other agents run at the same time.
- Kill processes by PID you recorded. Never `pkill -f` / `pgrep -f`. Stop everything you started before you finish; delete any browser profile dir.
- Tests: run single test files or `-k` selections only. Never the whole pytest suite and never the whole JS suite (the machine has 16 GB and seven other agents).
- `curl`/`wget` are blocked: use `python3 -c` with urllib, or httpx from `host/.venv`.
- Work from `host/` with `uv run ...`.
- No em dashes or en dashes in anything you write.
- If the spec in your prompt contradicts the code or docs, report the contradiction rather than working around it.

## Report

Write `docs/review/2026-09-23-opus55/<aspect>.md`:

- One section per finding, most severe first: id (`<ASPECT>-n`), severity (HIGH / MEDIUM / LOW), CONFIRMED or SUSPECTED, `file:line`, the concrete failure (input or state, then the wrong result), the repro, a suggested fix, and the known class if any.
- Severity: HIGH = data loss or corruption, crash or hang, security hole, silent wrong answer an agent would act on. MEDIUM = a contract break or wrong output a user would notice. LOW = cosmetic, docs, latent.
- Then a short "Checked and fine" list: what you drove that held, one line each with the probe, so the next reader does not redo it.
- Then "Not covered": what you did not get to.
- Keep it tight: no narrative of how you got there.

Reply with only: the report path and the count of findings by severity.
