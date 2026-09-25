# Fix-diff fixes, partition cli (base 01f7a66)

Findings from `registry-fix/fixdiff-cli.md`, under `registry-triage/fix-brief.md`.
Revert-verification ran in a private copy (`~/tt-data/mcuscope-2026-09-25/fix-fixdiff-cli/`, `mutate.py`), one pytest process per file: 22 mutants, all killed.

## Findings

- **X-cli-1 MEDIUM: fixed.**
  - `_follow_ws` compiles through `store.compile_user_regex` (lazy import, about 20 ms, only with `--match`).
    It catches `PatternTooLarge` first (`bad --match pattern: too large: repeats expand to N (max 100000)`), then `regex.error`, which now covers the daemon batch's inline `(?u)`/`(?L)`/`(?au)` refusal. The 200-character cap stays.
  - Tests in `test_cli_follow.py`:
    - `test_follow_match_over_the_daemons_repeat_budget_is_refused`: the 24-character pattern exits 1 before any `/ws` connect, with a positive control at expansion 50,103;
    - `test_follow_match_with_an_inline_flag_against_ascii_is_refused` (3 flags).
  - Mutants killed: back to `regex.compile`, no `PatternTooLarge` arm, no `regex.error` arm.
- **X-cli-2 LOW: fixed.**
  - `check_daemon_version` no longer reuses `update_check.is_newer`. `cli_client._version_key` orders `X.Y[.Z](a|b|rc)N`, with a pre-release below its release and `0.5 == 0.5.0`.
  - Any other version (dev, local, over 64 characters) passes only when it equals the CLI's exactly. The earlier rule let it pass, which let an older daemon through.
  - Tests in `test_cli_client_version.py`: `test_a_current_or_newer_daemon_is_let_through` (8 pairs) and `test_an_older_or_unorderable_daemon_is_refused` (7 pairs, including `rc` in both directions and a 5000-digit version).
  - Mutants killed: `is_newer` back, no equality, no zero strip, no length cap, no `have`/`need` None check, flat pre-release rank.
  - SPEC 4 and `AI_GUIDE` updated.
- **X-cli-3 LOW: fixed.** All seven doubles now go through the real `open`, so the version hook runs:
  - `test_cli_send_verdicts.py` (2) and `test_cli_can_dump.py` use `support.canned`;
  - `test_port_column_stored.py` uses `canned`;
  - `test_cli.py::test_plot_export_streams_the_response` uses `canned` with a chunked `SyncByteStream`, and patches `httpx.Client.request` and `Response.text` to raise, so the "never read whole" check is kept;
  - the child scripts in `test_cli_closed_output.py` and `test_scaffold.py` wrap the real `open` and send the header.
  - Revert-verify: a mutant `check_daemon_version` that always refuses fails every rerouted test except `_ports_body`'s cases. Those go through `probe`, which clears the hook by design.
- **X-cli-4 LOW: fixed (per D-16).** Deleted:
  - `/ws` bare-object frames: a non-array frame is now a bad frame, `skipping bad frame: frame is not an array of rows`;
  - `wait` without `sends`, and the `--repeat-ms` counts line without `sends`/`send_failures`: a missing counter is now `unexpected response from daemon: 'sends'`, exit 1;
  - the pre-0.3.0 session fallback (quoted-name path) and `_match_session`'s re-check. `name=` answers the one session, by id or name (server `resolve_session`).
    `session export` now uses `_resolve_session`.
  - `_absolute_window`'s `time.time()` fallback: `--last-ms` against a `/status` whose `now` is absent or not a number is exit 1 (`/status 'now' is not a number`), with no read sent.
  - Tests pinning the old paths: `test_cli_contract.py` R27-18 case, `test_cli_send_verdicts.py` `old-daemon` param and `test_repeat_counts_are_not_invented...`, `test_cli_sessions.py` FP-8 section (3 tests).
    Two canned `/status` bodies gained `now` (`test_cli_read_scope.py`, `test_cli_can_dump.py`).
  - New `tests/test_cli_response_shape.py`:
    - non-array frame;
    - `now` absent, bool or string;
    - `now` positive control;
    - a session anchor `ts: true` falls back to `now`;
    - missing `sends` and missing `send_failures`.
    The session path is pinned by the existing `test_session_export_goes_by_id_whatever_the_name`.
  - Kept, with comments reworded to drop the older-daemon rationale: the `.get` defaults for `write_errors`, `writer_alive` and `update` in `mcu status`.
    Subscripting them would turn a skewed answer into an exit 1 on the command used to diagnose the daemon.
  - Kept, not touched: the two "a daemon ignoring `id_to`" loop guards (`cli.py` `_iter_pages_asc`, `_poll_new_frames`). They are infinite-loop backstops against any wrong answer.
  - Also kept: `cli_daemonctl.py`'s older-daemon handling, since the `mcu daemon` commands are exempt from the check.
- **X-cli-5 LOW: fixed.**
  - M12: `test_a_paged_export_with_p_prints_no_port_column` (two stored boards, `-p a`), with a positive control that has no `-p`.
  - M12b: kept, not redundant. The clause short-circuits `_stream_boards`, so `--json` sends no `/ports` probe.
    Pinned by `test_a_json_export_does_not_ask_for_the_boards`, with a text-mode positive control.
  - M15: `_ascii_type`'s early return deleted; `test_cli_numeric_grammar.py` passes, 32 tests.
  - M2: `test_a_bool_id_is_no_row_id_to_continue_from`. M24: `test_a_bool_next_since_id_is_not_a_watermark`. M21: `now: true` case above.
- **X-cli-6 LOW: fixed.** `test_port_health.py::test_to_is_one_until_ts_on_the_query_itself` asserts on `seen` whole, and the stale comment is gone.

## Files

- Source: `host/mcuscope/cli.py`, `cli_client.py`, `cli_output.py`, `docs/SPEC.md` (SPEC 4, one line).
- Tests:
  - `test_cli.py`, `test_cli_can_dump.py`, `test_cli_client_version.py`, `test_cli_closed_output.py`, `test_cli_contract.py`;
  - `test_cli_follow.py`, `test_cli_read_scope.py`, `test_cli_send_verdicts.py`, `test_cli_sessions.py`;
  - new `test_cli_response_shape.py`.
- Outside the listed `test_cli*.py`, because the findings name them: `test_port_column_stored.py`, `test_scaffold.py`, `test_port_health.py`. These are test-only edits, and no other batch had these files modified when I edited them.

## Verified

- Every `tests/test_cli*.py` file, plus `test_port_column_stored`, `test_port_health` and `test_scaffold`, run alone: all pass (`test_cli.py` 167, `test_cli_follow.py` 33, `test_cli_sessions.py` 8, `test_cli_response_shape.py` 12).
  `test_cli_sessions.py` and `test_cli_follow.py` were rerun after the last edits.
- `ruff check` is clean on every touched file, and the added lines contain no em or en dashes (grep).
- Mutants: 22 killed, none survived (`mutate.py`).
- Session export by name, by id and unknown, against a real in-process daemon (Stack), in the copy: passes.

## Not verified

- Whole suite (not run, per the brief).
- A real 0.4.0 daemon wheel against this `mcu`: only doubles and the current daemon were used.
- Windows.

## CHANGELOG

- `mcu tail -f --match` refuses a pattern whose counted repeats expand past the daemon's budget, as `mcu lines --match` does.
- **Breaking:** the daemon version check orders pre-releases: `mcu` 0.6.0rc1 refuses a 0.5.0 daemon, and 0.5.0 refuses 0.5.0rc1. A dev or local build is accepted only when its version equals the CLI's.
- **Breaking:** the CLI drops its fallbacks for older daemon answers. These are now errors:
  - a `/ws` frame that is not an array;
  - a `/status` without `now` under `--last-ms`;
  - a `/wait` answer without `sends`/`send_failures`.
  A session reference is taken as the daemon resolves it.

## Needs another batch / Windows / browser

- None.
- Scratch for the owner to confirm deleting: `~/tt-data/mcuscope-2026-09-25/fix-fixdiff-cli/copy/` (12 MB, host copy). The global rule asks for a manifest and confirmation before a recursive delete. Keep `mutate.py` beside it (rerunnable).

## The two questions

1. Least confident:
   - Dropping `_match_session`'s name/id re-check trusts `name=` to answer only the one session. I re-drove it: the server resolves `name` through `store.resolve_session` (id first, then newest by name).
   - A Stack run exported by name and by id, and refused an unknown name.
2. Not yet thought about:
   - A dev build of `mcu` (a hand-edited `__version__`) now refuses every released daemon. That is intended under D-16, but a developer running a source checkout against an installed daemon will meet it.
   - The `id_to` loop guards were left in place, as a judgement call rather than a ruling.
