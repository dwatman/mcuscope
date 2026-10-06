# Fix batch cli4, 2026-10-06

Scratch: `~/tt-data/mcuscope-2026-10-05/fix4-cli/` (`orig/` pre-edit copies, `revert.py` and `revert.txt`, `t*.txt` test logs, `mut.bak`).
No daemon started.

## V98-1: daemon stderr log created at the default mode

- `host/mcuscope/cli_daemonctl.py:72` `_open_append` POSIX branch: `open(path, "ab", opener=private_opener)`. The import is at `:23`.
- The data dir already comes from `pidfile`'s `make_private_dirs`, through `_pid_file` before the open.
- An existing log keeps its mode: the opener's mode only applies when a file is created.
- Tests in `host/tests/test_cli_daemonctl_private_files.py`, run under umask 022:
  - `test_the_daemon_stderr_log_is_created_owner_only`
  - `test_an_existing_stderr_log_keeps_its_mode` (also checks the log is appended, not truncated)
- Revert-verify: dropping the opener fails `test_the_daemon_stderr_log_is_created_owner_only`.

## V98-2: replaced pid record lost 0600

- `host/mcuscope/cli_daemonctl.py:263` `_replace_pid_record`: the temp record opens with `opener=private_opener`, as `pidfile.py:304` does.
- Test: `test_a_replaced_pid_record_is_owner_only`. A 0644 record is replaced by a 0600 one with no `.tmp` left behind.
- Revert-verify: dropping the opener fails that test.

## V104-1: `--last-ms` had a floor but no ceiling on the CLI

- Decision: the CLI keeps resolving the window itself and does not pass `last_ms` to the daemon. Both alternatives break one end of the window:
  - Sending `last_ms` on every page lets the daemon re-anchor at now on each request, so the floor slides.
  - Pinning `id_to` makes the daemon anchor at the newest row, the "quiet board's hour-old tail" that `_resolve_window`'s docstring rules out.
- `host/mcuscope/cli.py:794` `_absolute_window(s, since_ts, until_ts, last_ms, session)` now returns `(since_ts, until_ts)`.
  - Both ends come from one anchor. The ceiling is `anchor + WINDOW_TS_SLACK_S` (`:833`), and the smaller of that and `--to` is kept.
  - The anchor is `/status` `now`, or the newest line of an ended session.
- `cli.py:839` `WINDOW_TS_SLACK_S = 10.0` duplicates `store.WINDOW_TS_SLACK_S`, as `MAX_TIMEOUT_MS` is duplicated. A test pins the two as equal.
- The three call sites (`lines` `:1035`, `log export` `:2051`, `can dump` `:2247`) take both bounds.
- Tests are in `host/tests/test_cli_last_ms_ceiling.py`. They drive `cli.main` against the in-process app, with rows stamped an hour ahead of now:
  - `test_last_ms_on_the_cli_leaves_out_rows_stamped_ahead_of_now`, for `lines`, `log export` (stream), `log export --limit` (paged) and `log export --names` (the ascending walk). Each asserts against the daemon's own `/lines?last_ms=` answer, with a positive control.
  - `test_can_dump_last_ms_leaves_out_frames_stamped_ahead_of_now`, for `-n` and `--csv`, with a positive control.
  - `test_an_ended_sessions_last_ms_ceiling_counts_from_its_newest_line`: a session ended an hour ago, where a ceiling counted from now would let one row through.
  - `test_a_later_to_does_not_lift_the_last_ms_ceiling` and `test_an_earlier_to_still_bounds_a_last_ms_window`.
  - `test_the_cli_slack_is_the_daemons`.
- Revert-verify (`revert.py`, each mutation restored afterwards):

| Mutation | Result |
|---|---|
| No ceiling | 8 fail |
| `min` dropped, so `--to` wins | the later-`--to` test fails |
| `--to` replaced by the ceiling | the earlier-`--to` test fails |
| Ceiling counted from `time.time()` instead of the anchor | the ended-session test fails |

- Existing files rerun green, 493 tests in total:
  - `test_cli_can_dump`, `test_flow_cli_windows`, `test_cli_daemonctl`, `test_cli_read_scope`, `test_cli_port_scope`, `test_cli_output_rows`, `test_cli_response_shape`, `test_cli_contract`
  - `test_cli`, `test_e2e`, `test_assert`, `test_cli_send_verdicts`
- `ruff check .` is clean.

## Existing tests edited

None.

## SPEC edits

- SPEC 4, the `--from`/`--to`/`--last-ms` paragraph (line 1295): `--last-ms` also sets `until_ts` to its anchor plus the 10 s slack, or `--to` if that is smaller, which is the ceiling the daemon gives a `last_ms` window in 3.4.

## Guide wording

No change. `AI_GUIDE` never described the ceiling, and the CLI now matches `mcu assert` and the daemon.

## Changelog

- `mcu lines`, `mcu log export` and `mcu can dump` with `--last-ms` now leave out rows stamped more than 10 s after the window's anchor (a clock stepped back since), as `mcu assert --last-ms` and the daemon already did.
- On POSIX, `mcu daemon start` creates the daemon's stderr log owner-only (0600), and a pid record it rewrites stays 0600.

## Not done

Nothing.

## Doubts

- Least sure: `can dump -f --last-ms` now sends an `until_ts` of now plus 10 s on its backfill.
  - The follow polls by `since_id` and ignores the bound, so live frames are unaffected. Only a frame stamped ahead is dropped from the backfill, which is the point of the change.
  - I did not drive a follow with ahead-stamped frames.
- Not checked: the extra `_pin_ceiling` request that `log export --limit`/`--decode` now makes for `--last-ms`.
  - That request's `until_ts` is above every stored `ts`, so `_window_id_ceiling` takes its two-lookup path by its docstring.
  - I measured nothing on `big.db`.
- Windows: V98 is POSIX-only by design, so the new mode tests skip there. The V104 tests have not run on Windows.
