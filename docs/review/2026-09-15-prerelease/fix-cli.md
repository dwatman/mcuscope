# CLI fix batch (pre-release round 2026-09-15)

Files changed: `host/mcuscope/cli.py`, `cli_client.py`, `cli_output.py`; tests `test_cli_r2026_09_12.py`, `test_cli_contract.py`, new `test_prerelease_cli_fixes.py`.
Gates: the five owned test files pass (203 tests), ruff clean on all of them, no en or em dashes.

Revert method: `~/tt-data/prerelease-cli/revert.py` backs up the three sources, applies one undo per changed branch (every anchor checked first), runs that fix's tests, restores from the copy and checks the sha256.
Log: `~/tt-data/prerelease-cli/revert.log`. Every undo below turned its tests red; all files restored byte for byte.

## Fixed

- **G-1** `Client.fail`: a 503 exits 3 only when the error starts with `daemon is shutting down` (`SHUTDOWN_PREFIX`); the subscriber cap and any other 503 exit 1.
  - Test: `test_the_subscriber_cap_503_is_exit_1_not_unreachable` (wait, assert, `--json` object), `test_a_503_that_only_mentions_shutdown_is_not_the_shutdown_answer`; the existing shutdown-is-3 test still passes.
  - Revert (plain `== 503`): 3 failed.
- **B-1** `_dump_follow` takes `session` and puts it into the priming and poll params.
  - Test: `test_can_dump_follow_polls_inside_the_session`. Revert: 1 failed.
- **B-2** plot export gates `-p` with the decode options; the gate message names only the flags passed.
  - Test: `test_a_field_an_older_daemon_drops_is_refused[-p ...]`, `test_no_gate_request_without_a_gated_option`. Revert: 1 failed.
- **B-3** the version gate moved to `Client.require_daemon` (one `DAEMON_MIN_VERSION`), now covering `--eol` on send, cmd (and bus sugar through `_run_cmd`), wait, assert, attach (only a non-`lf` ending, since a pre-0.4.0 port appends LF), and `--repeat-ms`.
  - The `sent N times` line prints only when the daemon sent `sends`; no defaulted 0.
  - Tests: the `GATED` parametrisation (refused against 0.3.0 with only `/status` requested; passes against 0.4.0), `test_no_gate_request_without_a_gated_option`, `test_repeat_counts_are_not_invented_when_the_daemon_sends_none`.
  - Reverts, one per site (attach, attach lf, send, cmd, wait eol, wait repeat, assert, sends default): each 1 or 2 failed.
- **B-4** `-o` is opened at the first write (`_OutFile`), so nothing is opened before the daemon answers below 400; an empty accepted export still creates an empty file.
  - On failure only an opened path is touched, and `cli_output.remove_partial` removes it only when `lstat` says regular file (not a symlink, FIFO or device); `Client.download` uses the same helper.
  - Tests: `test_a_refused_export_keeps_the_file_and_the_link` (streamed, `--limit`, `--decode`, plot, can csv), `test_a_stream_dying_mid_export_removes_a_file_but_not_a_link`, `test_a_session_download_dying_mid_stream_keeps_a_link` (also removes a plain file), `test_an_empty_accepted_export_still_writes_an_empty_file`.
  - `test_cli_contract.py::test_log_export_keeps_a_file_it_could_not_open` now answers 200 through a MockTransport, since the open follows the answer.
  - Reverts: eager open streamed 3 failed, eager open paged 1, regular-file check 2, empty file 1, remove-unopened 1, download symlink 1, download removal 1.
- **B-5** the streamed stdout export writes whole lines only and holds back the partial tail, so the `--json` error object is its own final line; a body without a final newline is flushed at the end.
  - Tests: `test_a_mid_row_failure_leaves_every_stdout_line_parseable`, `test_a_body_without_a_final_newline_is_still_written_whole`. Reverts: 1 and 1 failed.
- **B-6** `parse_clock` converts inside the `try` and maps `ValueError`, `OverflowError`, `OSError` to the usage error.
  - Test: `test_a_clock_at_the_calendar_limit_is_bad_usage` (9999-12-31, 0001-01-01, `--to`, can dump; one JSON object). Revert: 4 failed.
- **B-7** the paged `log export` to stdout calls `_stdout_untranslated()`.
  - Test: `test_the_paged_export_to_stdout_is_not_newline_translated` (`--limit`, `--decode`, CRLF wrapper). Revert: 2 failed.
- **B-8** the usage arm guards `exc.show()` against `BrokenPipeError` (`_silence_stderr`), then emits the JSON object.
  - Test: `test_a_usage_error_with_stderr_closed_keeps_exit_1` (child process, stderr a pipe with its read end closed; POSIX only). Revert: 3 failed.
- **B-9** `session export` resolves the name through `/sessions?name=` (shared `_resolve_session`, also used by delete) and puts the id in the path.
  - Tests: `test_session_export_goes_by_id_whatever_the_name` (`run?x=1#3/b`), `test_session_export_of_no_such_session_downloads_nothing`. Revert: 2 failed.
- **B-10** `session export` refuses a `-o` ending in a separator or naming an existing directory, before any request, with or without `--bundle`.
  - Test: `test_session_export_refuses_a_directory_target`. Reverts: whole check 4 failed, isdir half 2 failed.
- **B-11** `note_truncated` takes the remedy from the caller: tail `raise -n` / `use 'mcu log export' for every row`; log export `raise --limit` / `use --limit 0 for every row`; lines keeps the defaults.
  - Test: `test_the_truncation_note_names_only_the_commands_own_options` resolves every flag in the note against that command's click params. Reverts: tail 2, log export 2, remedy 4 failed.
- **B-12** `_clock_bounds` runs after the local refusals in plot export and log export.
  - Test: `test_a_usage_error_with_clock_bounds_costs_no_request` (unreachable url, exit 1). Reverts: plot 2, log 1 failed.
- **B-14** `can dump -o F --json` prints `{"file", "frames", "bytes"}`; `--csv --json` without `-o` is still refused.
  - Test: `test_can_dump_to_a_file_with_json_prints_the_summary`. Reverts: refusal 1, summary 1 failed.
- **B-15** shared `LAST_MS_OPTION` with `min=0, max=MAX_WINDOW_MS` (10^15, mirrors `server.MAX_MS`) on lines, log export, can dump, plot export.
  - Tests: `test_last_ms_out_of_range_is_bad_usage` (-5000 and 10^15+1 on all four), `test_last_ms_at_its_bounds_is_accepted`. Revert: 8 failed.
- **B-17** `--serial` is stripped; a blank one is refused as `--serial is blank`.
  - Tests: `test_attach_refuses_a_blank_serial`, `test_attach_strips_the_serial_it_posts`. Reverts: blank 2, strip 2 failed.
- **B-18** `Client.fail` on a 404 probes `/status`; an older daemon gets `daemon X does not serve PATH; it needs daemon 0.4.0 or newer`. The daemon answers no 404 of its own, so a 404 is a missing route.
  - Covers `/lines/export`, `/sessions/{id}/bundle`, `/break` (so `break` and `sysrq`).
  - Tests: `test_a_missing_route_names_the_daemon_version`, `test_a_404_from_a_current_daemon_keeps_its_own_message`. Revert: 3 failed.
- **F-4** both version-gate tests chdir to `tmp_path` and assert it stays empty; the plot one asserts `ignores --decode (`.
  - Revert (gate off for `--decode`): 1 failed, and no `x.csv` appeared in `host/`.
- **F-12** `test_follow_learns_a_filtered_out_redefinition_under_its_port`: a primed `volts` definition, a `--match`-hidden `!pd 7 amps:u1` on port a, then a sample renders `amps=5`.
  - Revert (mutant P75, learn under `None`): 1 failed.
- AI_GUIDE: the version-gate and missing-route paragraph under GLOBAL OPTIONS, the subscriber-cap exit 1 under `wait`, `can dump --session -f` scope, `can dump -o --json` summary, `--last-ms` range.

## Not done / owed

- **B-13** (SPEC only), `docs/SPEC.md` line 1058: "A timeout (exit 2) names the pattern, the port given with `-p`, the wait and, after `--send`, the send count on stderr."
- SPEC 3, line 734: after "which the CLI maps to exit 3", add "; any other 503 (the subscriber cap, lines 579 and 870) exits 1".
- SPEC 4 table:
  - Line 1064 (`mcu can dump`): add "`-f` with `--session` polls inside the session. With `-o`, `--json` prints `{"file", "frames", "bytes"}`; `--csv` with `--json` and no `-o` is a usage error."
  - Line 1058 (`mcu wait`): also say that the `sent N times` line appears only when the daemon reports a count.
- SPEC 4 prose:
  - Line 1082: add "`--last-ms` takes 0 to 10^15 on `mcu lines`, `mcu log export`, `mcu can dump` and `mcu plot export`; outside that range it is a usage error."
  - Line 1084: replace with "An option riding on a parameter or body field a daemon older than 0.4.0 does not declare is refused by the CLI, naming the daemon's version: `--from`/`--to`, `--eol` (on `attach`, only an ending other than `lf`), `--repeat-ms`, `can dump --csv`, and `-p`/`--decode`/`--changes`/`--deadband` on `plot export`. A 404 from a route that daemon lacks names its version and the minimum."
  - Line 1093: after "removes the partial file", add "A refusal leaves `-o` untouched: the file is opened only once the daemon has accepted the request, and only a regular file is ever removed (a symlink, FIFO or device is left alone)."
  - Line 1094: add "`mcu session export -o` naming a directory, or ending in a path separator, is a usage error."
- CHANGELOG Unreleased, Fixed (one line each):
  - `mcu wait`/`mcu assert` against a daemon at its subscriber cap exit 1, not 3; only the shutdown answer is "unreachable".
  - `mcu can dump --session S -f` stays inside the session.
  - A refused export no longer truncates or deletes what `-o` names; a failed one removes only a regular file.
  - `mcu log export --json` ends with a parseable error line when the daemon dies mid-row.
  - `--from`/`--to` at the calendar's ends are usage errors, not a traceback.
  - The paged `mcu log export` (`--limit`, `--decode`) writes LF to a redirected Windows stdout.
  - A usage error with stderr closed keeps exit 1 and its `--json` object.
  - `mcu session export` works for session names containing `/`, `?` or `#`, and refuses a directory `-o` (no hidden `DIR/.zip`).
  - The truncation note names options the command has.
  - `--from`/`--to` no longer cost a request before a usage refusal on `plot export` and `log export`.
  - `mcu can dump -o F --json` prints a file summary instead of refusing.
  - `--last-ms` is bounded (0 to 10^15) client-side.
  - `mcu attach --serial` refuses a blank serial and strips surrounding spaces.
  - Against a daemon older than 0.4.0: `-p` on `plot export`, `--eol` and `--repeat-ms` are refused naming its version; a missing route (`log export`, `session export --bundle`, `break`, `sysrq`) names the version instead of `Not Found`.
- `tests/test_cli.py::test_session_export_removes_a_partial_file` (not mine) now passes vacuously: its handler answers the new `/sessions?name=` lookup with the dying body, so exit 3 comes from the lookup and `download` is never reached.
  - Fix: answer `request.url.path == "/sessions"` with `{"sessions": [{"id": 1, "name": "run"}]}`. The removal is covered meanwhile by `test_a_session_download_dying_mid_stream_keeps_a_link`.
- Not run, per the brief: the rest of the suite. Most at risk from call-order changes: `test_cli.py` (session export, bad-url forms), `test_eol.py`, `test_wait_repeat.py`, `test_break.py`, `test_review_r2_cli.py`.
- REVIEW.md class candidates from B-3 (body fields beside query parameters in class 53), B-4 (output opened before the peer accepted) and G-1 (one status code, several causes): for the docs batch.

## The two questions

1. Least confident, and rechecked:
   - B-4 removal rule. The brief says "removes only a regular file this command created"; I remove a regular file this command opened, created or truncated.
     - Why: SPEC 4 says a stream dying mid-transfer removes the partial file, and a pre-existing file we truncated holds only the partial bytes.
     - Rechecked: refusals never open (5 command forms, symlink and plain file kept); mid-stream, a symlink survives and a plain file goes. If "created only" is meant, `_OutFile` needs an `lexists` check before the open.
   - G-1 has a WebSocket sibling I left alone: `_follow_ws` maps close 1013 (capacity) and an HTTP 503 `InvalidStatus` to exit 3 by an earlier deliberate ruling ("1013 is capacity, and stays 3"). The subscriber-cap code is an owner decision in triage, so `tail -f` at the cap still exits 3.
   - B-5 holds back a partial last line in text and CSV mode too, so a mid-line death drops that fragment from stdout rather than printing it.
   - B-8 is driven on POSIX only. B-2, B-3 and B-18 are driven against canned `/status` bodies, not a real v0.3.0 extract.
2. What should have been checked:
   - A real old-daemon drive of the gate and the 404 message, and a Windows run of B-7 and B-8.
   - The FIFO form of B-4 has no test: undoing the fix would block the test on `open()` with no reader. It is covered by reasoning only (nothing opens before the daemon answers).
   - The cost side: each gated option adds one `GET /status`, `session export` adds one lookup, and `mcu -p X plot export` now always pays the version request.
