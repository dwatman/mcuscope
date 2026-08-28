# Fix batch G (docs only)

HEAD at start: `0b5eed9 Review round 2, wave 1: the HIGH-carrying fixes across firmware, CLI, web UI, sim`.
Files touched: `docs/SPEC.md`, `docs/ARCHITECTURE.md`, `README.md`, `CLAUDE.md`, `host/pyproject.toml`. No source or test changes, nothing committed.

## 1. SD3 + 2. FW10, SPEC 2.1 receiver rules (new sub-bullets under the encoding bullet)

Before: no receiver rule at all under "Encoding: 7-bit printable ASCII."
After (two new sub-bullets):

```
  - A receiver **may** reject a byte above 0x7F, and the two reference implementations differ: the firmware fails the whole line with `ERR 2 badarg` (5.4), the simulator accepts it. Both are conformant.
  - The firmware's command parser additionally tolerates low control bytes (0x01-0x1F and 0x7F) mid-line; 5.4 is the operative clause for what a command line must reject.
```

## 3. SD4, SPEC 8 web UI JS test sentence

Before: `runs `node --test` over the 27 `*.test.mjs` files in `host/tests/webui_js/``
After: `runs `node --test` over the `*.test.mjs` files in `host/tests/webui_js/``

Verified myself: 33 `*.test.mjs` files at this HEAD (the evidence file recorded 28 at `fd76735`; batch D has since added more). The number was going to rot again either way, hence dropping it.

## 4. SD5, ARCHITECTURE.md whole-stack tier

Before: `- **Whole-stack tests** (`tests/support.py:Stack`, the `stack` fixture, roughly 280 tests) attach `sim://board` ...`
After: `- **Whole-stack tests** (`tests/support.py:Stack`, the `stack` fixture) attach `sim://board` ...`

No replacement number: the measured figure was 148 of 940 collected, so any wording carrying a count rots.

## 5. SD6, README simulator sentence

Before: `The simulator also runs standalone as `mcu-sim` (prints `socket://127.0.0.1:9900`, attachable like any device), which is how the test suite exercises the stack.`
After (two lines):

```
The simulator also runs standalone as `mcu-sim` (prints `socket://127.0.0.1:9900`, attachable like any device), which is how a daemon in another process attaches it.
The test suite instead attaches the simulator core in process over `sim://`, with `test_sim_tcp.py` keeping the standalone listener under test.
```

## 6. README config sample and repo layout

Config sample: `[plotjuggler]` table added between `[update]` and `[[ports]]`, matching SPEC 3.3's example (`enabled = false`, `dest = "127.0.0.1:9870"`), with the section reference dropped since README does not use SPEC numbering.

Repo layout: four `docs/` entries added after `docs/RELEASING.md`:

```
docs/ARCHITECTURE.md         Module map and the design constraints behind it (read before changing one)
docs/REVIEW.md               Review runbook: defect classes and the sweep that finds each
docs/REVIEW_LOG.md           Per-round review findings and open legs
docs/SCREENSHOTS.md          How to refresh docs/img/webui.png
```

## 7. SPEC 8 test-count sentence

Before: `Several hundred tests in `host/tests/`, roughly 4 minutes, no hardware and no daemon subprocess by default.`
After: `The `host/tests/` suite, roughly 4 minutes, no hardware and no daemon subprocess by default.`

## 8. F13, SPEC 4 `mcu daemon start` row

Before: `| `mcu daemon start [--config FILE] [--sim] [--timeout S] [--token T]` / `stop` / `status` | Convenience: spawn/kill mcuscoped as a detached process, cross-platform (...); a systemd user unit is also provided as a Linux convenience |`
After: `| `mcu daemon start [--config FILE] [--sim] [--timeout S]` / `stop` / `status` | Convenience: spawn/kill mcuscoped as a detached process, cross-platform (...); the global `--token` both forwards to the spawned daemon and authenticates this CLI; a systemd user unit is also provided as a Linux convenience |`

## 9. SPEC 7 `--garbage` bullet

Before: `... `--garbage` (occasionally emit binary junk).`
After: `... `--garbage` (occasionally emit binary junk; bypasses the outgoing sanitizer by design, so it stays a real fault injector).`

## 10. CLAUDE.md venv version

Before: "a uv-managed 3.12 virtualenv lives at `host/.venv`" and `uv venv --python 3.12`
After: "a uv-managed 3.13 virtualenv lives at `host/.venv`" and `uv venv --python 3.13`

Verified: `host/.venv/lib/python3.13`. The "a bare `uv venv` may pick a <3.11 python" warning is unchanged.

Not changed, flagged instead: `README.md:367` still shows `uv venv --python 3.12` ("creates .venv on a known-good interpreter"). Outside the listed items and not wrong (3.12 is supported), but the two files now name different versions.

## 11. pyproject.toml coverage comment

Before: `# A regression alarm, not a target. Measured total is 84% (cli.py 49%, daemon.py 82%), so 78`
After: `# A regression alarm, not a target. Measured total is 87% (cli.py 59%, daemon.py 84%), so 78`

Figures from `measurement.md` (total 87%, cli.py 59%, daemon.py 84%). The rest of the comment (subprocess undercount, `fail_under` rationale) is unchanged; the `~85% / ~89%` COVERAGE_PROCESS_START figures were left as-is since this round did not re-measure them.

## Dash check

`grep -nP '[\x{2014}\x{2013}]'` over all five changed files: no matches (exit 1).
