# Fix batch: CLI Python (FC-1 .. FC-9)

HEAD at start `5489d6e`.
Tests in `host/tests/test_fixdiff2_cli.py` (18, all canned `httpx.MockTransport` behind `cli.Client.open` and a fake `Popen`; no daemon started, port 8558 untouched).
Revert method: the fixed files were snapshotted to `~/tt-data/prerelease-2026-09-15/fix-fixdiff2-cli/fixed/`, each branch reverted by `revert.py <item>` in that directory and restored from the snapshot; no `git checkout`, no `git stash`.

## FC-1 HIGH: `can dump --last-ms` converted once, before paging

- `cli.py:1941`: `since_ts = _absolute_window(s, since_ts, last_ms, session)` replaces `params["last_ms"] = last_ms`, so both the `-n` walk and the `--csv` request carry an absolute `since_ts` and `last_ms` never reaches `/can/frames` from this command.
- Tests: `test_can_dump_n_with_last_ms_keeps_the_window_it_started_with` (3000 frames over 2.9 s, `-n 2500 --last-ms 3000`, a daemon whose clock jumps 0.5 s per request), `test_can_dump_csv_with_last_ms_also_sends_the_absolute_window`, and the control `test_can_dump_without_last_ms_sends_no_window_at_all` (no bound is invented).
- Revert: fails. 2068 frames printed instead of 2500, oldest printed id 933 (432 rows gone) and no truncation note; the CSV test fails on `last_ms` in the request.

## FC-2 MED: a carried `config_path` is forwarded unchanged

- `cli.py:2537-2545` (`daemon_restart`): `_named_config` now runs on the user's `--config` only, and the daemon's `config_path` is taken afterwards and forwarded as reported.
- `cli.py:2400-2415`: `daemon_start` became the typer command (the `--open`/`--json` refusal, then `_named_config`) plus `_start_daemon(ctx, config, sim, wait_s, open_ui)`, which holds the spawn. Needed because `daemon_start` ran `_named_config` a second time on whatever `restart` passed it, so forwarding a relative path still died there. Refusal order is unchanged.
- Tests: `test_restart_forwards_a_relative_config_path_the_daemon_reported` (a daemon reporting `etc/mcuscoped.toml`, asserted on the spawn argv) and the control `test_start_still_refuses_a_config_the_user_typed_that_does_not_exist` (exit 1, `no such config file`, nothing spawned).
- Revert: fails with `no such config file: <cwd>/etc/mcuscoped.toml`, rc 1.
- SPEC 4 daemon row: one clause, a non-default `config_path` a running daemon reports is forwarded unchanged.

## FC-3 MED: a guard refusal in the post-spawn readiness loop is a started daemon

- `cli_daemonctl.py:112-131`: new `_status_or_refusal` returns `(body, (code, message))`; `_status_body` is now its wrapper and dies with the same wording as before.
- `cli.py:2474-2501`: the readiness loop breaks on a body **or** a refusal; the `_serving_pid` mismatch check is guarded by `body is not None` (a refusal carries no pid and used to crash there); on a refusal one stderr note is printed (`note: the daemon requires a token (HTTP 401: ...); pass --token or set MCUSCOPE_TOKEN for later commands`) and the normal started line follows, exit 0. `ui_url` is unchanged: it comes from `_ui_url(s)`, never from the body, so there was no body-derived URL to drop.
- The pre-spawn probe still calls `_status_body` and still exits 1 without spawning.
- Tests: `test_start_reports_a_daemon_it_spawned_that_answers_with_its_guard` (rc 0, started line, note, pid record written), plus two controls: `test_start_before_the_spawn_still_refuses_a_daemon_behind_a_guard` (rc 1, `refused the request (HTTP 401)`, nothing spawned) and `test_start_whose_daemon_never_answers_is_still_a_failed_start` (silence is still a failed start).
- Revert: fails on both the loop (`_status_body` dying inside the wait) and the removed `body is not None` guard (AttributeError in `_serving_pid`); the AI_GUIDE clause has its own failing assertion.
- SPEC 4: exception sentence after the 401/403/429 paragraph (line 1064) and a clause in the `mcu daemon` row; AI_GUIDE `DAEMON CONTROL` block matches.

**The stop-wait loop, asked for in the brief.** `cli_daemonctl.py:280` (`_wait_daemon_gone`) and the belt-and-braces probe at `:264` both call `_status_body`, so a guard refusal there still exits 1 - after the stop was accepted. Left unchanged, and it is nearly unreachable for a token: `_wait_daemon_gone` only polls `/status` when `real_pid is None` (a pre-0.1.2 daemon with no pid record), and a token-guarded daemon refuses `POST /shutdown` first, which routes to the "did not accept a shutdown request" refusal instead. What can still reach it is a 429 lockout appearing between the accepted shutdown and the poll: a successful stop then reports `refused the request (HTTP 429)` and exit 1. Worth a ruling of its own; not in this batch's decisions.

## FC-4 LOW: the Windows stream wrappers stack once

- `_stdio.py:248-264` new `_already_translated`, used at `:277`: the skip walks the `_stream` delegation chain (bounded at 8) instead of testing the outermost type, so a `_GuardedStdout` wrapping a `_PipeErrorStream` is recognised.
- Test: `test_a_second_main_adds_no_stream_layer_on_windows` (`PIPE_CLOSE_IS_EINVAL` forced true, installer run four times, chain asserted as `['_GuardedStdout', '_PipeErrorStream', '_FakePipe']` and stderr as `['_PipeErrorStream', '_FakePipe']`), with `test_a_console_stream_is_still_left_alone` as the control for the skip.
- Revert: fails, chain grows to 9 entries.

## FC-5 LOW: the follow's frame cap is raised, not removed

- `cli.py:1105-1108`: `max_size=16 * 1024 * 1024` with the comment's arithmetic (500 rows of up to 4 KB, about 2 MB) as the reason.
- Test: `test_the_follow_frame_cap_is_a_number_not_unbounded` asserts the value handed to `websockets.connect` and that it is above 500 * 4096.
- Revert: fails (`max_size` is None).

## FC-6 LOW: the guide carries `mcu wait`'s exit 1

- `cli.py:2699-2700`: one clause on the existing `mcu wait` line, "A daemon that accepts the wait but never answers is exit 1, not 2".
- Tests: `test_the_guide_gives_mcu_wait_the_exit_code_spec_4_gives_it` (asserted on the wait block, not the whole guide) and the control `test_the_guide_still_names_the_wait_timeout_verdict`.
- Revert: fails.

## FC-7 LOW: the dead token clause is gone

- `cli.py:2584-2586`: the `_stop_daemon` comment now names the startup-in-progress case only.
- No test and no revert check: comment only, no branch changed. The branch itself is still covered where it was.

## FC-8 LOW: the child suites use `child_env`

- `test_rulings_cli_closed_pipe.py:38,54` and `test_sweep_cli_closed_output.py:56` build `env` from `support.child_env(...)`; the in-child `user_data_dir` patch stays for Windows.
- Test: `test_the_child_suites_build_their_env_from_child_env` (both files, parametrised: `child_env(` present, `dict(os.environ` absent).
- Revert: fails for both files. Both suites still pass with the change (90 tests over the three owned suites plus `test_cli_contract.py`).

## FC-9 LOW: `detach` names the alias it refused

- `cli.py:407`: `die(f"invalid alias {alias!r}: an alias cannot contain '/'", 1)`.
- Tests: `test_detach_says_it_refused_the_alias_rather_than_looked_it_up` (`invalid alias 'a/b'`, and `no such port` absent, with no request made) and its positive control `test_a_real_miss_still_reports_the_daemons_no_such_port` (a 404 `no such port: ab` for a valid alias does reach the user).
- Revert: fails.
- The existing `test_sweep_cli_closed_output.py:277` assertion pins only `an alias cannot contain '/'`, which both wordings satisfy; left as it is.

## CHANGELOG lines

```
- `mcu can dump -n` with `--last-ms`: the window is now fixed before paging, so a walk past the 1000-frame cap no longer drops the oldest frames and calls the dump complete.
- `mcu daemon restart` no longer refuses a running daemon whose reported `config_path` is relative to the daemon's own directory; the path is forwarded as the daemon reported it.
- `mcu daemon start` reports a daemon it started that answers behind a token as started (exit 0, with a note that later commands need `--token` or `MCUSCOPE_TOKEN`), instead of exit 1 for a daemon left running.
- `mcu tail -f` caps a WebSocket frame at 16 MiB rather than accepting one of any size.
- `mcu detach a/b` says `invalid alias 'a/b': an alias cannot contain '/'` instead of claiming a port lookup it never made.
- `mcu ai-guide` states `mcu wait`'s exit 1 for a daemon that never answers, and `daemon start`'s exit 0 behind a token.
```

## Not done

- **Five `monkeypatch.setattr(cli, "_status_body", ...)` sites now bypassed** (files owned by other batches). The post-spawn readiness loop calls `_status_or_refusal`, so a fake bound to `_status_body` no longer reaches it and the start hangs to its deadline. Each site needs the same fake exposed under both names, e.g. after the existing line: `monkeypatch.setattr(cli, "_status_or_refusal", lambda s, timeout=2.0: (status_body(s, timeout), None))`.
  - `tests/test_cli_ux.py:140` (`_answering`) - fixes `test_restart_with_no_daemon_running_just_starts_one`, `test_an_unwritable_stderr_log_falls_back_to_devnull_with_a_warning`, `test_start_prints_the_web_ui_url_and_opens_it_only_on_request`.
  - `tests/test_cli_ux.py:203` - `test_restart_carries_the_running_daemons_config_and_sim`.
  - `tests/test_review_r2_cli.py:106` - `test_daemon_start_refuses_when_another_daemon_serves_the_url`.
  - `tests/test_rulings_cli_config.py:52` (the shared `_running` helper) - the five failures in that file.
  - `tests/test_cli_ux.py:260` needs nothing: it drives `daemon status` only.
  - These are the only failures in the neighbouring CLI suites; the run was 10 failed, 173 passed over `test_cli_ux.py`, `test_review_r2_cli.py`, `test_rulings_cli_config.py`, `test_prerelease_cli_fixes.py`, `test_rulings_cli_follow.py`, `test_cli_export.py`, and all ten trace to this one seam.
  - The fakes in those files may also want a case for the new behaviour (a refusal in the readiness loop), but that is covered in `test_fixdiff2_cli.py` already.
- **`CHANGELOG.md`**: not edited (not my file); lines above.
- **A pre-existing ruff error outside my files**: `tests/test_rulings_daemon_startlog.py:4` I001 unsorted imports (another batch's file). Ruff is clean over every file I own.
- **`mcu plot export` still sends `last_ms` raw** (`cli.py:2290` region). Correct today, since that command does not page, but it is the next instance of class 44 if it ever does. Not in this batch's decisions.
- **`cli_daemonctl.py:264,280`**: the stop-path `_status_body` calls, see FC-3 above.

## The two questions

1. **Least confident, and rechecked.**
   - That FC-1's test would actually fail without the fix rather than merely differ. Re-driven under the hand-revert: 2068 of 2500 frames, oldest id 933 against the expected 501, and stderr silent. It fails for the defect's own reason, not on a fixture detail.
   - `--last-ms 0` on `can dump` changes meaning slightly: it used to be the daemon's own inclusive floor, and is now `nextafter(now, -inf)` computed in the CLI, exactly as `mcu lines --last-ms 0` has always been. Read, not driven against hardware; it is the deliberate shape of `_absolute_window` and SPEC 4 already states the conversion.
   - FC-3's reachability is canned, not driven against a real token-guarded daemon: the test asserts the CLI's behaviour given a 401 with an error envelope, and takes from the report that a daemon reading `MCUSCOPED_TOKEN` produces one.
   - The `_start_daemon` split reorders nothing, but it is the largest structural change here. Checked: `--open`/`--json` still refuses before `_named_config`, and `test_cli_ux.py`'s refusal-order test passes.

2. **What should have been checked and was not thought about.**
   - **Who monkeypatches the helper you are about to split.** Splitting `_status_body` into a probe plus a wrapper is invisible in production and silently disarms five test fakes bound to the old name, in three files this batch does not own. A grep for `setattr(.*"<name>"` belongs in the same breath as the split, before the design is chosen: the seam is part of the function's contract.
   - **A per-caller invariant that has no chokepoint.** FC-1 is the second caller of `_fetch_newest` to get the `--last-ms` conversion wrong, and nothing stops a third. `_fetch_newest` could assert that `params` carries no `last_ms`, which would make the class unrepeatable instead of re-swept; that is a ruling for the owner, not a change this batch was asked for.
   - **The mirror of the refusal ruling on the way out.** The round ruled what a guard refusal means while starting; nobody asked what it means while stopping. `cli_daemonctl.py:264,280` still turn a refusal into exit 1 on a stop that already succeeded.
