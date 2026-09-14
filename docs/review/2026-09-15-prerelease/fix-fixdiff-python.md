# Fix batch: fix-round diff review, Python (FP-1..FP-8)

Scratch: `~/tt-data/prerelease-2026-09-15/fix-fixdiff-py/` (`revert.py`/`revert.out` round 1, `revert2.py`/`revert2.out` round 2, `probe_fp2_equivalence.py`, `suite.log`, `suite2.log`).
Gates after round 2: full suite (`uv run python -m pytest -q`) 1824 passed, 1 skipped; `ruff check .` clean; no em or en dashes.
New test file: `host/tests/test_prerelease_fixdiff_py.py` (38 tests).

## Fixed

Revert method: copy the sources, check every anchor before writing, apply one undo per changed branch, run that branch's tests, restore from the copy, check sha256.
Round 1: 14 undos, all KILLED (R1 and R2 re-run without `-x`: every credited test fails on its own assertion).
Round 2: 9 undos, all KILLED, run without `-x`. Files restored byte for byte both rounds.

| Id | Change | Test | Revert |
|---|---|---|---|
| FP-3, FP-4 (and FP-1's 400) | `_resolve_window` no longer refuses a crossing against a session's stamps or the `last_ms` floor; `until_ts is before since_ts` stays | `test_a_purged_sessions_last_ms_window_is_an_empty_answer` (`/lines`, `/can/frames` json and csv, `/lines/export`, `/assert`), `test_a_row_stamped_before_its_session_start_is_answered_by_until_ts` | R1 (refusal restored): KILLED |
| A-10 filename | when `lo > hi`, `lo = hi`, so `<from>-<to>` never reads backwards | existing test rewritten: `test_prerelease_daemon_core_windows.py::test_a_window_crossing_its_session_is_answered_and_names_no_backwards_file` (3 crossings, 200, both stamps equal the upper bound) | R2 (clamp removed): KILLED on `/lines/export`, `/can/frames`, `/plot/export` |
| FP-1 CLI half | `_absolute_window` counts `--last-ms` back from the ended `--session`'s newest line (`/sessions?name=` via `_match_session`, then `/lines?id_to=end_id&limit=1`, the daemon's own anchor query), else now; the cut is nudged one float down so the strict `since_ts` matches the daemon's inclusive floor | `test_session_with_last_ms_on_the_cli_is_the_daemons_tail` (5 forms: `lines` one page and paged, `log export` streamed, paged and the ascending decode walk; `last_ms` 100 and 0; row ids equal the daemon's `/lines?session&last_ms`), `test_can_dump_and_plot_export_agree_on_the_sessions_tail`, `test_a_running_sessions_last_ms_still_counts_back_from_now` | F1a (anchor ignored), F1b (nudge removed: the `--last-ms 0` forms), F1c (running session anchored at its newest row): all KILLED |
| FP-2 daemon half | `_resolve_window` skips `id_ceiling_safe` when the bound's newest row (`Store.newest_ts_at_or_below`, one primary-key seek, shared with `_window_floor`) has `ts <= until_ts`, or when no row is at or below the bound | `test_the_ceiling_walk_is_skipped_only_when_the_bound_row_is_inside_until_ts` (bound row before and after `until_ts` across a clock step, and `id_to=0`: walk counts and rows) | F2a (skip removed), F2b (skip whenever bounded), F2c (walk on an empty bound): all KILLED |
| FP-2 CLI half | `_iter_pages_asc` with `until_ts` probes `/lines?until_ts=T&order=desc&limit=1` once, pins `id_to` (min with a given one), and keeps `until_ts` on every page; an empty probe pins 0, so the page still carries the daemon's refusals. `_fetch_lines` already pages by `id_to` | `test_a_paged_to_walk_runs_the_ceiling_walk_once` (`log export --names`, `log export --limit`, `lines --limit`, pages of 5: exactly one walk), `test_a_paged_to_walk_matches_one_request_across_a_clock_step` (ids equal one `/lines?until_ts` request, 20 rows below the ceiling stamped after `--to`), `test_a_to_before_every_row_still_asks_the_daemon` | F2d (pin removed), F2e (pages drop `until_ts`), F2f (empty probe sends no page): all KILLED |
| FP-5 | trailing separator refused up front; `isdir` tested on the final path after `.zip` | `test_bundle_beside_a_directory_of_the_same_name_writes_the_zip`, `test_a_bundle_target_ending_in_a_separator_is_refused_before_the_suffix`; existing `test_session_export_refuses_a_directory_target` makes `adir.zip/` for the bundle case | R3, R4, R5: all KILLED |
| FP-6 | `_clock_bounds(s, from_, to, gated)` judges `--from/--to` plus the command's other gated flags in one `require_daemon` | `test_every_gated_option_is_judged_by_one_status_request` (5 forms, old and current daemon) | R9, R10, R11: all KILLED |
| FP-7 | `remove_partial` resolves the path and removes the target when that is a regular file; the link is kept, a FIFO or device left | `test_a_dead_stream_through_a_symlink_removes_the_file_it_resolves_to` (two-link chain, target gone), `test_a_symlink_to_a_fifo_keeps_both` | R12, R13: KILLED |
| FP-8 | a returned page with no exact id or name match falls back to `/sessions/<quote(name, safe="")>/...`; an empty answer stays `no such session` | `test_a_page_without_the_name_falls_back_to_the_quoted_path[db, bundle]`, `test_a_page_holding_the_name_exports_that_row_by_id`, `test_a_fallback_to_a_session_the_daemon_lacks_downloads_nothing`; existing `test_session_export_of_no_such_session_downloads_nothing` answers `{"sessions": []}` | R6, R7, R8, R14: all KILLED |

Docs: SPEC 3.4 (crossing sentence replaced with the no-refusal rule and the filename rule), SPEC 4 (`-o` removal through a symlink; `session export -o` directory rule on the final path), the CHANGELOG A-10 line.
SPEC 4's "`--session`, `--last-ms`, `--from` and `--to` intersect ... alike" sentence needed no edit: FP-1 makes it true. AI_GUIDE unchanged.

## Not done / owed

- CHANGELOG lines for round 2 (outside my edit list):
  - "`mcu lines`/`mcu log export` with `--session S --last-ms N` on an ended session return its tail, as `can dump` and `plot export` do."
  - "Decoded `mcu log export --to` no longer re-derives the `until_ts` ceiling on every page."
- FP-1 cost: `--session` with `--last-ms` on `lines`/`log export` adds two requests (the session row, then its newest line). The session row alone does not carry the anchor: `end_id` is the closing marker, stamped before `ended_ts`, and the daemon anchors at that marker's `ts`.
- FP-8, reasoned not driven: a name containing `/` against a pre-0.3.0 daemon falls back to `%2F`, which the server decodes before routing, so it 404s and B-18 reports `daemon 0.2.0 does not serve ...`.
- Nothing ran on Windows (FP-5 `os.sep`, FP-7 `realpath` on a Windows symlink).

## The two questions

1. Least confident, and rechecked:
   - FP-1 mechanism: sending `last_ms` raw for an ended session would have been one request, but the daemon anchors `last_ms` at the effective `id_to`, so the FP-2 pin would move the anchor off the session's end. The absolute `since_ts` avoids that interaction. The strict/inclusive edge was driven (`--last-ms 0`).
   - FP-2's skip rule is exact because `until_ts` still filters every page; the rows-identical test is what kills a CLI that drops it (F2e), and the walk counts kill both halves of the optimisation.
   - FP-8 deviates slightly from "if none matches, fall back": an empty answer does not fall back. A daemon honouring `name=` answers at most one row and it always matches, so only a non-empty page without the name comes from a daemon ignoring `name=`.
   - The filename clamp takes `hi` for both sides.
2. What should have been checked:
   - Every `_iter_pages_asc` caller (3): only `log export`'s decode walk carries `until_ts`; `_make_decoder` and the filtered `!pd` lookup pass none, so they pay no probe.
   - Every `_resolve_window` caller (5) meets the new skip: the rows are unchanged wherever `until_ts` is also filtered, which every store read given the scope does.
   - Every consumer of `_Window.lo`/`hi` (3 `export_filename` calls), `remove_partial` (2) and `_clock_bounds` (4): covered above.
