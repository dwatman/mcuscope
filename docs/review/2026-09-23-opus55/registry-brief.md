# Registry leg brief, classes 1-80 (2026-09-24)

Run the registry sweeps for your class range over HEAD `f31ecd9` (print `git rev-parse HEAD` first and stop if it differs).
The registry is `docs/REVIEW.md`; read its "Sweep discipline" section and every entry in your range before sweeping.

## Output

- One file: `docs/review/2026-09-23-opus55/registry-<first>-<last>.md`. Reply with its path and a one-line tally only.
- Per class: the sweep command or method, the site count it returned, then every site ruled "violates", "complies" or "exempt because <reason>".
  An unlisted site means the sweep was not run; never truncate a sweep's output (no `head`).
- Findings first, at the top of the file: id `R<class>-<n>`, severity (HIGH/MEDIUM/LOW), `file:line`, the concrete failure (inputs, state, wrong result), and how you confirmed it (driven, or reasoned only).
- A sweep the registry states imprecisely (it misses a site shape you found, or matches noise): say so under the class, with the improved command.
- Classes needing Windows or a real browser: run what Linux can, then list what stays owed.
- End with the two questions from `docs/REVIEW.md`, answered for your leg, including "nothing found".

## Rules

- Read-only on the repo: edit nothing but your report file. No git commands that change state.
- Scratch (probes, copies, logs) under `~/tt-data/mcuscope-2026-09-24/registry-leg/<first>-<last>/`, never `/tmp` or the repo.
- A probe that mutates source does so in a private copy under scratch, never the repo tree.
- No whole test suites: run single test files only (another agent may be running tests; two whole JS runs froze the machine once).
- Any `mcuscoped` you start uses `--config` on a throwaway TOML with its own `db_path`, `MCUSCOPE_CONFIG_DIR`/`MCUSCOPE_DATA_DIR` in scratch, and a port other than 8558; stop it by PID when done.
- A contradiction in this brief or in a registry entry: report it, do not work around it.
- Commands run from `host/` with `uv run python`.
