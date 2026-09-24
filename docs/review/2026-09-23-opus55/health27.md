# HEALTH-27: tests moved to per-module files (2026-09-24)

- Python: 50 round-named files removed (19 by `git mv`, the rest split); 17 new topical files; cross-module flows in `test_flow_cli_windows.py`.
- JS: 39 round-named files removed (25 by `git mv`, 14 split into 21 `<module>_<area>.test.mjs` files); no JS test merged into an existing file.
- Collection: Python 2616 before and after; the only id changes are two param ids naming moved files. JS 959 top-level titles before and after, identical; 256 moved JS tests pass run alone.
- Helpers merged into `tests/support.py` and conftest: `stack_client`, the `client` TestClient fixture, `mk_app`, `on_loop`, the canned-daemon CLI helpers.
- Mutants the finding listed as killed only by round files are now killed by the module's own file (K01, P03, A05, A06; M24, M26, M34, M44, M50, M54, M69, M70, M75). M51 stays killed only by `api_high_rate_pending.test.mjs` (not round-named).
- Suites: whole Python suite 2615 passed, 1 skipped; JS suite green; ruff clean.
- Per-test maps and collection lists: `~/tt-data/mcuscope-2026-09-24/health27/` (`pymap_tests.txt`, `js-report.md`, `collect_before.txt`, `collect_after.txt`).
- Note: commit 7d2421b (registry) swept in this move's staged `git mv` renames without their content; its tree is not green. The move is complete from the commit that follows it.
