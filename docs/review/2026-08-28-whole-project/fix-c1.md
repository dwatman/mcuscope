# Fix batch C1 (cli.py, cli_argv, cli_client, cli_daemonctl, cli_output)

Files changed: `host/mcuscope/cli.py`, `cli_argv.py`, `cli_client.py`, `cli_daemonctl.py`, `cli_output.py`, new `host/tests/test_review_r2_cli.py`.
Nothing else in the tree was touched; nothing committed.

Gates run (not the full suite, per brief):
- `uv run python -m ruff check` on all five source files and the new test file: clean.
- `uv run python -m pytest tests/test_review_r2_cli.py tests/test_cli.py tests/test_pidfile.py -q` -> **207 passed** (86 s).
- `uv run python -m pytest tests/test_regressions.py -q` -> **82 passed** (it covers cli_argv hoisting and out_json, both touched here).

## Items fixed

### F1 (HIGH) `daemon start` no longer clobbers a live daemon's record or reports a dead pid
- New `cli_daemonctl._write_pid_record(pid_path, pid) -> bool`: reads the existing record with `read_pid_record`, and returns False (writing nothing) when it names a **running** process, reusing `pidfile.pid_running`. Otherwise an atomic temp+replace as before; the temp name now carries the writer's pid so two concurrent starts do not write each other's bytes. `daemon start` warns and carries on when the guard refuses, exactly as it already did for an OSError on that write.
- `daemon_start` now keeps the `/status` body from the readiness loop and, before reporting success, requires that no *other* pid is serving: `_serving_pid(body, None)` differing from `proc.pid` is a failure, `another daemon is already serving at <url> (pid X)`, exit 1, with nothing written and nothing removed. A child that exited while the URL answers is a second failure arm. A daemon too old to report `pid` yields None and is not judged (unchanged behaviour there).
- The record write stays *before* the readiness wait, deliberately: `_abandon_daemon`'s "still running as pid N (pid file ...)" message depends on the record existing. The guard is what closes the clobber, per the ruling.

### F2 the three dispatcher arms owe --json one object
New `cli._dispatch_error(msg, code)` (err + the `{"error", "exit_code"}` object when `json_mode()`), used by the Abort, KeyboardInterrupt and KeyError/IndexError arms.
Note for the record: typer converts a Ctrl-C raised **inside** a command into `Exit(130)` (`typer/core.py:204`), so the KeyboardInterrupt arm only ever sees one escaping the app call; the test drives it at that boundary and says so.

### F3 pid path resolved before the spawn, OSError mapped
`_pid_file` now wraps `pid_file_path` in `try/except OSError -> die("cannot use the daemon pid file: {exc}", 1)` - one guard where both call sites route, so `daemon stop` is covered too. `daemon_start` resolves it **before** `Popen`, so an unusable data dir can no longer leave a spawned daemon behind a traceback. The exception text carries the path.

### F6 + measurement F1 `--before-days` refuses <= 0
`die("--before-days must be greater than 0 (use --all to delete everything)", 1)` before any request is sent. Message unique to the flag. (The server half is another batch's.)

### Measurement F2 truncation note
`note_truncated` reports `len(body["lines"])` ("truncated at N rows") and offers "raise --limit" only when the returned count equals the requested limit; otherwise only `--since-id`. No 1000 mirrored anywhere.

### Measurement F3 no `(ids None-None)`
New `cli._ids_clause(preview)` returns "" when either end is null; used by both the dry-run print and the confirmation prompt.

### F7 both pid-record removals in `_stop_running_daemon` are guarded
Both now go through `_remove_pid_record`. That needs the pid the record named when it was read, which is **not** `real_pid` (/status can report a different process to the launcher shim the record names), so `_stop_running_daemon` gained an optional `recorded_pid` parameter and `daemon_stop` passes it.
Deliberate behaviour change to note: when the record was unreadable at read time (`recorded_pid is None`) the tidy-up now skips it instead of `os.remove`-ing it. Removing an unreadable record is exactly what `pidfile`'s settle window says not to do (it may be a claimer mid-write), and the alternative leaves a corrupt record that the next `daemon stop` reports and leaves in place.

### F8 hoisting tracks value consumption
`split_global_opts` now carries an `is_value` flag set when a token that went to `rest` is a subcommand value-taking option, instead of comparing the literal previous token. `mcu lines --match --limit --json` hoists `--json` again.

### RG-F6 `_daemon_errors` gains ValueError, plus token validation
- `cli_client._daemon_errors` gained `except ValueError -> die("cannot send request to {url}: {exc}", 1)` after the httpx arms (httpx raises `UnicodeEncodeError` while encoding a header).
- `--token` / `MCUSCOPE_TOKEN` are validated as ASCII in the global callback: `die("token must be ASCII (--token, or MCUSCOPE_TOKEN)", 1)`. Both halves, as ruled.

### RG-F7 WS json guards
Both sites (`handle`'s per-frame parse and the follow's outer `json.JSONDecodeError` arm) now catch `(json.JSONDecodeError, ValueError)`, matching `cli_client`'s siblings.

### RG-F8 plot_export's close
`fh.close()` moved inside the guarded region with `except BrokenPipeError: raise` / `except OSError -> die("cannot write ...", 1)`; the `finally` keeps a suppressed close (a no-op after a successful one) and the truncated-file removal. A flush failure now exits 1 like `log export`.

### RG-F9 millisecond timeouts bounded client-side
`cli.MAX_TIMEOUT_MS = 300_000` (mirroring `server.MAX_TIMEOUT_MS`, duplicated for the same reason `FOLLOW_MATCH_TIMEOUT_S` is) and a `timeout_ms_option` click callback on `cmd`, `wait` and `assert`. Out of range is a usage refusal, not an OverflowError.

### RG-F10 `_field` guard
New `cli_output._field(body, key, optional=False)`: sibling of `_list_field`, vouching that a field a command subscripts or calls `.get()` on is an object. Applied in `status` (session, plotjuggler, update - all `optional=True`), `attach` (port), `assert` (expect/forbid via `_list_field`, each `line` via `_field`), `session start` and `session stop`. The dispatcher's TypeError reasoning is unchanged.

### R35 (class 35) `out_json` guarded
`out_json` now prints with `flush=True` inside `try/except BrokenPipeError -> _silence_stdout()`, mirroring `err_write`. A `--json` error exit on a closed stdout keeps its mapped code.

### TQ-F2 half B and the class-7 unasserted cell
Tests only (no source change needed): the `_follow_ws` staged-drain cleanup test and the "removed stale pid file" assertion. Both revert-verified (below).

## Revert verification

Each mutation applied to the shipped source, the test run, then the source restored and re-run. Automated by `/tmp/claude-1000/review-r2/rv_c1.py`; raw log in `/tmp/claude-1000/review-r2/rv_c1.txt`.

| fix reverted | test | mutated | restored |
|---|---|---|---|
| `_write_pid_record` liveness guard removed | test_write_pid_record_refuses_a_record_naming_a_live_process | 1 failed | 1 passed |
| serving-pid check removed from daemon_start | test_daemon_start_refuses_when_another_daemon_serves_the_url | 1 failed | 1 passed |
| `_pid_file` OSError guard removed | test_daemon_start_reports_an_unusable_data_dir_without_spawning | 1 failed | 1 passed |
| Abort arm back to `err()+return 1` | ..._abort_arm | 1 failed | 1 passed |
| KeyboardInterrupt arm back to `err()+return 1` | ..._keyboard_interrupt_arm | 1 failed | 1 passed |
| KeyError/IndexError arm back to `err()+return 1` | ..._key_error_arm | 1 failed | 1 passed |
| `--before-days` guard disabled | test_purge_before_days_refuses_a_non_positive_age | 3 failed | 3 passed |
| `_ids_clause` back to unconditional | test_purge_dry_run_omits_the_id_range_when_nothing_matched | 1 failed | 1 passed |
| note_truncated back to the request limit | test_truncation_note_reports_the_returned_rows_not_the_request | 1 failed | 1 passed |
| hoisting back to the previous-token compare | test_hoisting_sees_a_global_after_a_value_that_looks_like_an_option | 1 failed | 1 passed |
| token ASCII check disabled | test_non_ascii_token_* (2) | 2 failed | 2 passed |
| `_daemon_errors` ValueError arm removed | test_a_value_error_from_the_transport_is_mapped_not_raised | 1 failed | 1 passed |
| ms timeout bound disabled | test_ms_timeout_out_of_range_is_a_usage_refusal (3) | 3 failed | 3 passed |
| `_field` type check removed | test_a_wrongly_typed_daemon_field_... | 4 of 5 failed | 5 passed |
| `out_json` guard removed | test_json_error_exit_survives_a_closed_stdout | 1 failed | 1 passed |
| "removed stale pid file" text changed | test_daemon_stop_reports_removing_a_stale_pid_record | 1 failed | 1 passed |
| `_follow_ws` cleanup block deleted (M31) | test_follow_ws_consumes_its_pending_recv_when_the_staged_drain_raises | 1 failed, stderr "Task exception was never retrieved" | 1 passed |

Notes on two of these:
- The `_field` mutation kills 4 of the 5 parametrised cases; the fifth (`assert` with `"expect": null`) is caught by `_list_field`, which the mutation did not touch. Correct attribution, not a hole.
- Half B (TQ-F2) needed two corrections before it discriminated, both worth recording: the orphan window only exists when the frame is **staged** and a second recv is in flight (so the fake snapshot has to be slower than the first frame), and `pytest.raises` keeps the traceback - and with it the frame holding the recv - alive, so the task is never collected and files no report. The test catches the Exit by hand and then `gc.collect()`s. A `pytest.raises` version of this test passes with the fix deleted.

## Skipped, with reasons

### F12 (session name paging) - SKIPPED, contradiction reported
The ruling is "session name resolution pages through /sessions until found or exhausted". The API cannot do that:
- `GET /sessions` takes `limit` only (`server.py:1170`) - no offset, no cursor.
- `store.list_sessions` hard-clamps `limit = max(0, min(int(limit), 1000))` (`store.py:1004`).

So the CLI's existing `limit=1000` is already the whole of what the endpoint can return, and any client-side loop would either spin or terminate on the same 1000 rows. Nothing was changed. The fix belongs on the server side, and there is a cheap one: `GET /sessions/{ref}/export` already resolves a **name or id** server-side (`export_session(ref: str)`), so either `DELETE /sessions/{ref}` gaining the same resolution, or an offset/`before_id` parameter on `GET /sessions`, closes it. That file belongs to another batch.

### F9 (`-p` hoisting narrowed to `-p<digits>`) - SKIPPED, contradiction reported
The ruling says only `-p` followed by a value or `-p<digits>` attached may hoist, so `-pulse` stays for click's error. Implementing that re-breaks a previously fixed, deliberately tested regression:

```
tests/test_regressions.py:480  def test_hoisting_handles_the_attached_short_form()
    """`mcu lines -psim` was a usage error while `mcu -psim lines` worked."""
    assert hoist(["lines", "-psim", "--limit", "1"]) == ["-psim", "lines", "--limit", "1"]
```

`-psim` and `-pulse` are the same token shape; no rule distinguishes them, so the ruled change turns the supported attached alias form back into a usage error. I did not implement it and did not edit `test_regressions.py` (outside this batch's files). The orchestrator needs to decide: either drop the attached short form (and delete that regression test, which is a documented behaviour change worth a SPEC/AI-guide line), or accept `-pulse` mis-hoisting as the cost of it. F8, the other half of the hoisting work, is implemented and tested.

## Anything a later batch should know
- `_stop_running_daemon` gained a fourth parameter (`recorded_pid`, defaulted), and `cli.daemon_start` no longer keeps an `up` flag - it keeps the `/status` body.
- `out_json` now flushes on every call. Any test asserting interleaving of stdout writes would see the new ordering; none in test_cli/test_regressions did.
- `note_truncated`'s message text changed ("truncated at N rows" instead of "at limit N"); no existing test asserted the old wording, but any doc or SPEC line quoting it needs the update.
