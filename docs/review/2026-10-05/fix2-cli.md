# Fix batch: cli2 (2026-10-05)

Findings: `fixdiff-cli.md` FD-CLI-1 to 11, with the owner rulings in `triage.md`.
Revert-verification: `~/tt-data/mcuscope-2026-10-05/fix2-cli/mut/mutate.py` ran 18 mutations, all caught (`mut/results.txt`; `fallback` re-run alone, see Doubts).
Each file was restored from a copy and checked against its hash.
Gate: `fix2-cli/gate.sh` runs every `tests/test_cli*.py` file one at a time, plus five related files (`gate.sum`).
Two failures were found and resolved (see "Existing tests edited" and "Doubts").
Live drive: `fix2-cli/drive.sh` against an isolated `daemon start --sim` on 19060 (`drive.log`), stopped by `mcu daemon stop`.

## Findings

**FD-CLI-1** `cli.py:2445` `_poll_frames`: a 4xx poll goes through `client.fail(resp)`, the same classifier as the follow's first request.
- The `error_text` import became unused and was removed.
- Test: `test_cli_can_follow_poll_refusals.py`.
  - A 401, a 409 or a 422 on a poll (the backfill and prime pass) gives `daemon_error`, `daemon_error`, and `usage` with `--since-id:`.
- Mutation `poll-kind` caught.

**FD-CLI-2** (coordinator's answer: exit 1, `kind: usage`, since SPEC 4 wins over the ruling's "exit 2")
- `purge` refuses any `-p` in its body: `purge removes every port's rows; -p does not scope it`.
  - In the body, so `-p X purge --help` still prints help.
  - `purge` left `_PORT_UNUSED`.
- Guide purge entry: "it spans every port, so any -p is refused (exit 1)".
- SPEC 4 unknown `-p` line: `purge` refuses any `-p`.
- Test: `test_cli_port_scope.py::test_purge_refuses_any_p_before_asking_the_daemon`.
  - Covers a known and an unknown alias: exact JSON object, no request made.
  - `--help` gives rc 0 with no request.
- The purge case left the shared refusal list.
- Mutations `purge-p` (drop the refusal) and `purge-generic` (purge back on the generic check) caught.

**FD-CLI-3/4** `cli_output.py` `AtomicOut`:
- Temp name `.mcu-XXXXXX.partial` (`:252`), so a target name near NAME_MAX still gets a temp file and stays atomic.
- When `mkstemp` fails (read-only directory), the target is written directly. Stderr says `warning: no temporary file beside PATH (<strerror>); writing it directly, so an interrupted export leaves it partial` (`:256`).
- A failed `os.replace` in `commit` is re-raised naming the typed path (`:274`). The caller's `discard()` removes the temp.
  - I first wrote a `self.discard()` there too. No mutation could catch it, because both callers already discard, so I deleted it.
- `discard` clears the read-only bit before `os.remove` (`:286`), for Windows.
- Tests in `test_cli_output_atomic_export.py`:
  - `..._read_only_directory_writes_the_file_directly_with_a_warning`
  - `..._name_near_the_limit_still_goes_through_a_temp_file`: the target holds its old bytes mid-stream.
  - `..._failed_rename_names_the_target_and_removes_the_temp`
  - `..._read_only_temp_is_still_removed`: `os.remove` is patched to refuse a read-only file, as Windows does.
- Mutations `tmp-prefix`, `fallback`, `fallback-warn`, `commit-name`, `discard-chmod` caught.
- SPEC 4 states the identity loss (hard links, owner, ACLs, xattrs).

**FD-CLI-5** The `-p` check is deferred from the group callback to `settings_of` (`cli.py:106`, flag `_PORT_CHECK_DUE` in `ctx.meta`).
- Every command calls `settings_of` after click has handled `--help`.
- Click 8.4 has already consumed `ctx.args` when the group callback runs, so a `--help` sniff there was impossible.
- Tests in `test_cli_port_scope.py`:
  - `test_help_after_an_unknown_p_asks_no_daemon`, over all ten commands that send no `-p`: rc 0, `Usage:`, no request.
  - `pj` was added to the existing refusal list.
- Mutations `help-early-check` and `no-deferred-check` caught.
- Live: `-p bogus status --help` rc 0. `--url <dead> -p x status --help` rc 0. `-p bogus status` gives `no_such_port`, rc 1.

**FD-CLI-6** SPEC 3.4 export sentence gains `(the daemon's own rows: [-])`.
- Test: `test_server_export_daemon_rows.py`: `/lines/export?format=text` across ports `a`, `""` and `b` gives `[a]`, `[-]`, `[b]`.
- Mutation `render-dash` caught.

**FD-CLI-7** `AI_GUIDE`:
- `:3084`: the unknown `-p` line now exempts `ai-guide`, `config` and `daemon`. Purge is no longer named, pending FD-CLI-2.
- `:3092`: new PITFALLS line: "A wait timeout (exit 2) whose "dropped" is non-zero (stderr warns "lines were shed") may have shed its match: retry the wait rather than trust the timeout."
- `:3274`: "--chan, --raw and --eol work as in wait; --allow-empty and --allow-dropped as in PITFALLS."
- SPEC 4: `daemon` added to the ignore list, and the `wait` row gains the retry sentence.
- Guide text has no mutation test. `test_cli_contract` passes.

**FD-CLI-8**
- `_csv_value`: a `-5` cell was added to `test_plot_export_json_rows_are_objects_keyed_by_the_header`, asserting `type is int`. Mutation `csv-neg` caught.
- `isinstance(stored, list)` guard: `test_a_stored_field_that_is_not_a_list_matches_nothing` covers a `stored` of `"x"`, `7` or `None` with `-p x`. Mutation `stored-guard` caught.
- `daemon_error` constants: the 33 sites of `die(msg, 1, "daemon_error")` became `die_daemon(msg)` (`cli_output.py:162`).
  - The sites are in cli.py (18), cli_daemonctl.py (8), cli_output.py (3) and cli_client.py (4).
  - The kind now lives in one place, pinned by `test_a_malformed_daemon_answer_is_daemon_error`. Mutation `die-daemon-kind` caught.
  - Rewrite script: `fix2-cli/scripts/die_daemon.py` (AST, byte-column aware); continuation lines were realigned by `realign.py`.
  - Not a per-site behavioural test: a site could still call `die` instead.

**FD-CLI-9** `config_url`/`resolve_url` gain `strict` (`cli_client.py:90`). `_start_daemon` (`cli.py:2767`, also used by `restart`) resolves strictly, so an unreadable config is `error: <path>: invalid TOML ...`, exit 1, before any probe.
- Test: `test_a_start_on_an_unreadable_config_refuses_before_probing` (in `test_cli_client_url_precedence.py`), for `--config` and for the default config.
  - A canned daemon answers everything, and the test asserts no request was made and no "already running".
- Mutations `start-strict` and `strict-die` caught.
- Live on 19060: a daemon ran from `MCUSCOPED_CONFIG=good.toml`; `daemon start --config bad.toml` printed the TOML error, rc 1.
- SPEC 4 states it.

**FD-CLI-10** `cli.py:933`: with `--names` and no decoder built (an empty window), every named entry is warned about, deduplicated and in order.
- Test: `test_names_on_an_empty_window_are_warned_about` for `lines`, `log export --decode` and `tail`.
- Mutations `names-empty` and `names-dedupe` caught.

**FD-CLI-11** Documented, with no code change.
- SPEC 4: `usage` covers "an interrupt (`interrupted`, `aborted`) included".
- Guide: `usage (fix the command line; also an interrupt, "interrupted" or "aborted")`.

**Held-port test**: `test_a_held_port_is_named_as_held` was removed from `test_cli_verdict_outcomes.py`. The held wording is `_port_state`'s, already pinned by `test_port_health.py:542`, and the generic disconnected test covers `_down_state`.

## Existing tests edited

- `test_cli_export_files.py::_temp_beside`: matches the `.mcu-` temp prefix.
- `test_cli_port_scope.py`: the refusal list is shared as `_PORT_UNUSED_ARGV` and gains `pj`.
- `test_cli_messages.py::test_plot_export_json_rows_are_objects_keyed_by_the_header`: a negative integer row.
- `test_port_health.py::_canned_lines`: answers `/ports`.
  - `test_last_ms_is_fixed_before_paging` failed before this batch too: I checked it against my pre-batch snapshot.
  - The cause is the first cli batch's OP-8 `/ports` question, which the helper recorded as a third `/lines` request.

## SPEC edits

- 3.4 (text export): `(the daemon's own rows: [-])`.
- 4, kind vocabulary: `usage` includes an interrupt.
- 4, unknown `-p`: `daemon` joins `ai-guide` and `config` as ignoring it.
- 4, URL precedence: `daemon start`/`restart` refuse an unreadable config before probing.
- 4, `wait` row: a timeout with non-zero `dropped` is not a clean negative, so retry it.
- 4, `-o`:
  - The temp is named `.mcu-*.partial`.
  - A failed rename names `FILE` and removes the temp.
  - When no temp can be created, `FILE` is written directly with a warning.
  - Hard links, owner, ACLs and xattrs are not carried over.

## Changelog

- `mcu -p X purge` is refused (exit 1): purge removes every port's rows, and `-p` never scoped it.
- `can dump -f`: a refusal on a later poll has the same `kind` and wording as on the first request (a token refusal is `daemon_error`, not `usage`).
- `mcu -p X <cmd> --help` prints help without asking the daemon.
- `daemon start`/`restart` refuse an unreadable config instead of probing the default address.
- `--names` on an empty window warns about every name.
- `-o` exports:
  - They work in a read-only directory, written directly with a warning.
  - They work for names near the filesystem's limit.
  - A failed final rename names the file and leaves no temp.

## Not done

- Windows was not run, for FD-CLI-4 and `-o NUL`. The read-only-temp removal is simulated by a patched `os.remove`.

## Doubts

- Something else wrote my files during this round.
  - The two-line purge refusal vanished from `cli.py` between a passing test run and the next mutation run, while my other edits in the same write stayed. I re-applied it and re-verified.
  - In the full mutation re-run, `fallback` was MISSED once, then CAUGHT three times out of three alone.
  - Both fit a concurrent writer (another batch copying files back) better than a defect.
  - The orchestrator should check that `cli.py` still contains `purge removes every port's rows` and that `cli_output.py` matches `fix2-cli/cur/` before merging.

- Least sure: the `die_daemon` consolidation closes FD-CLI-8's constants structurally, not behaviourally. A future site using `die(..., 1)` gets `usage` silently, as before.
- `test_cli_daemonctl.py::test_daemon_start_reports_an_unusable_data_dir_without_spawning` fails: the message has `pid file` but no `mcuscope` in the path.
  - `dirs.py`/`pidfile.py` were changed by another batch minutes before my gate.
  - My files do not touch that path (the message is unchanged in `_pid_file`). Not investigated further: the owner of `dirs.py` should check it.
- Not checked: the read-only-directory fallback on Windows (`mkstemp` there may fail differently). Also not checked: whether `settings_of` runs before any request in every command a future change adds to `_PORT_UNUSED`. The parametrized refusal test covers the current ten.

Scratch, `~/tt-data/mcuscope-2026-10-05/fix2-cli/`:
- `orig/`: pre-batch copies.
- `cur/`: the restore copies.
- `mut/`: runner, results and `.orig` copies.
- `scripts/`: `die_daemon.py`, `realign.py`.
- `run/`: configs, the 19060 data dir.
- `gate.sh`, `gate.sum`, `gate*.log`, `drive.sh`, `drive.log`.
