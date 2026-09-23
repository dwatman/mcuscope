# Handoff, 2026-09-23 round (state at session end)

Branch `review/2026-09-23-opus55`. Read `triage.md` (all rulings), then this file, then the `fix-*.md` reports as needed.

## Done and committed

- `4e618e6` nine review reports and triage.
- `84d57eb` daemon batch, `0f5b206` link batch, `3ea4a45` tests batch, `a49e52d` firmware batch, `dd39ee1` link tokenizer follow-up, `95e2442` web UI (chrome + panes).
- Vendored monitors in `~/Syncthing/auto-charger/` (charger-test, charger_control, relay_control) re-copied from `a49e52d`; all three were byte-identical to the pre-round upstream, ARM compile with `-Wall -Wextra -Wformat=2 -Wconversion` clean. Owner compares against their git.

## Uncommitted in the working tree

- Source: `store.py`, `server.py`, `cli.py`, `cli_client.py`, `cli_output.py`, `cli_argv.py`, `cli_daemonctl.py`, `_stdio.py`, `render.py` (and `pidfile.py` if touched), plus the server batch's late edits to `store.py` (`on_open` hook, `last_id_before_ts` deleted) and `serial_link.py` (`PortManager.resolve` deleted).
- Docs: `docs/SPEC.md` (every batch's sections), `CHANGELOG.md`, `README.md`, `host/README.md`, `docs/CLAUDE_SNIPPET.md`, `docs/ARCHITECTURE.md` (docs pass, see `docs-pass.md`).
- Tests: the store, cli and server batches' new files (`test_store_*`, `test_cli_*`, `test_server_*`, `test_render_line_breaks.py`) and their edits to shared files (`test_hardening.py`, `test_regressions.py`, `test_cli.py`, `test_wait_repeat.py`, `test_timeline.py`, `test_assert.py`, `test_store_fastpaths.py`, and others listed in each report's "Existing tests edited").
- Reports: `fix-store.md`, `fix-cli.md`, `fix-server.md`, `docs-pass.md`, `handoff.md`.

## Next steps, in order

1. **Full suite is red by cascade.** `uv run python -m pytest` (random order, pytest-randomly): 181 failed, 909 errors, nearly all `OSError: [Errno 9] Bad file descriptor`. Log: `~/tt-data/mcuscope-2026-09-23/final/suite1.log`.
   - `test_plot_grammar_fixture.py`, `test_pane_regex_dialect.py`, `test_hardening.py` pass 248/248 alone, so a test closes an fd the pytest process still uses. First suspects: the cli batch's new `test_cli_closed_stdio.py` (CLI-11) and anything driving `_stdio.repair_std_streams` in process.
   - Find it (run with `-p no:randomly` and bisect, or grep new tests for `os.close`/`dup2`/`sys.stdout = None`), fix it, rerun the whole suite.
   - Possibly real, check after the cascade is gone: `test_server_scope.py::test_two_concurrent_stops_give_exactly_one_success`, `test_cli_transport_timeouts.py` (3), `test_sweep_followups.py` can dump (4), `test_sweep_cli_closed_output.py` (3), `test_prerelease_cli_fixes.py` (several), `test_prerelease_daemon_core_windows.py::test_last_ms_on_a_quiet_capture_counts_back_from_now_on_every_export`.
   - Then the whole JS suite once (`tests/test_webui_js.py`; it passed except the export guard double, which the server batch has since fixed).
2. Commit store + cli + server (+ shared test edits), then SPEC + CHANGELOG + docs, each on a green suite. `uv run python -m ruff check .` was clean.
3. Fix-diff leg over `6e4f6f7..HEAD`: opus reviewers by file, reports written to this directory. Include:
   - The server's undeclared-query-param 422 reads a FastAPI internal; verify on the dependency floor (`fastapi>=0.115.7`) per REVIEW.md class 43 (`uv pip install --resolution lowest-direct` in a throwaway venv).
   - Each batch's "Doubts" section.
4. HEALTH-27 test reorganisation by module under test (owner ruling: separate commit after the fixes).
5. `docs/REVIEW.md`: add the new classes this round confirmed (candidates: a streamed response holding a DB cursor across yields (PERF-2); a pid from a peer's answer acted on locally (LIFECYCLE-1); an ordering premise (`ts` rises with `id`) false across writers (CAPTURE-1); a verdict matching its own stimulus (CLI-1); a vacuous verdict over an empty scope (CLI-3)), each with a sweep run before close. Add a `docs/REVIEW_LOG.md` entry for the round.
6. Update memory `review-round-open-legs.md`.

## Tell the owner

- `/tmp/tmp.nMY1BQF4F5` (firmware agent's ARM build dir, 10 files, ~200 KB) awaits delete confirmation; `/tmp` clears on reboot anyway.
- Browser checks owed (not verifiable headless): `<U+XXXX>` inside the clipped session chip, the reload badge, chart decimation on a sparse fast stream, real Sec-Fetch headers from Firefox/Chrome.
- Known residuals and judgement calls made by agents:
  - The reload badge compares against the first `/status` version seen, not the serving one.
  - An over-long `!p` at high rate doubles its line count (one overflow notice per cut line).
  - `/marker` with an unknown port is now 400.
  - A failed `--send` ends an `/assert` window at once; the live-scan 1 s grace is the server agent's own number.
  - `mcu send -` is refused.
  - CAPTURE-1 slack is 10 s; the store agent trusts that bound least.
  - A copy interrupted exactly at open reports "unable to open database" rather than "interrupted".
- Windows leg still owed for everything.
