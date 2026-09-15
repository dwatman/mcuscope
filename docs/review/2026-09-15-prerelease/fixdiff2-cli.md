# Fix-round diff review: CLI Python (`9ad910c..HEAD`, `host/mcuscope/cli*.py`, the CLI test files, SPEC 4, CHANGELOG)

HEAD verified `5489d6e`.
Scratch probes: `~/tt-data/prerelease-2026-09-15/fixdiff2-cli/` (`test_probe_lastms_pages.py`, `test_probe_win_guard_stack.py`, `test_probe_daemonctl.py`, the diff `cli.diff`).
Run from `host/`: `uv run python -m pytest -s -q -p no:cacheprovider --rootdir=. ~/tt-data/prerelease-2026-09-15/fixdiff2-cli/<file>`; each probe prints its observation to stderr and passes, so the evidence is the printed line, not the verdict.
No daemon was started and port 8558 was not touched; every probe is a canned `httpx.MockTransport` behind `cli.Client.open`.

Totals: 1 HIGH, 2 MED, 6 LOW.

Ruled out, kept as negative results:

- `_fetch_newest` does not mutate the caller's `params`: `params = dict(params)` at `cli.py:602` (pre-existing context of the hunk), so `can_dump`'s dict is intact when `_dump_follow` runs. `_poll_new_frames` copies too (`page_params = {**params, ...}`).
- `can dump -n` paging against an older peer is not class 53: v0.2.0, v0.3.0 and v0.4.0 all declare `id_to` on `/can/frames` (`ge=1`), and the CLI never sends `id_to=0` (both pagers stop at `oldest <= 1` / `oldest <= since + 1`). The stale-page guard is belt-and-braces, not the load-bearing path.
- The `truncated` note is right when the stale-page guard fires: the discarded page's flag is the honest one (rows do exist below), and `test_can_dump_against_a_daemon_ignoring_id_to_stops_and_notes` drives it.
- A follow poll loses no frame arriving while it pages: `cli.py:2059` advances `since` from the frames actually returned, so anything minted above page 1's head is picked up on the next poll.
- `assert --last-ms` bounds match the daemon exactly: CLI `min=1, max=10**15` against `AssertBody.last_ms = Field(gt=0, le=MAX_MS)` with `MAX_MS = 10**15` (`server.py:117,274`). No class 19 gap, and the usage error is exit 1 (`_dispatch`'s `USAGE_ERRORS` arm), not 2.
- `_dump_follow` really does pass `{"limit": 1000}`, so `test_a_follow_poll_*`'s fixture is a state the producer reaches (not class 63).
- `emit_stream(json.dumps(row))` encodes identically to the `out_json` it replaced (`out_json` is `print(json.dumps(obj), flush=True)`), so the JSONL bytes are unchanged on a cp1252 Windows console.
- The Windows stream-wrapper nesting below (FC-4) does **not** change a closed-pipe exit code: driven, rc 0 on both runs.

## Findings

### FC-1 HIGH: `mcu can dump -n` above 1000 with `--last-ms` silently drops the oldest frames and says the walk was complete

- Where: `cli.py:1946` (`params["last_ms"] = last_ms`) carried unchanged into every page of `_fetch_newest` (`cli.py:592-619`), reached by `cli.py:1960`.
- Defect: class 44 exactly. The daemon evaluates `last_ms` against its own clock per request (`_resolve_window`, no freeze for `format=json`), so the window's old edge slides forward by however long the earlier pages took, and those rows are the ones pages 2..n are walking towards.
  - Before this diff `can dump -n` made one request, so the class did not apply; the diff introduced the paged walk without the conversion `lines` and `log export` already do (`_absolute_window`).
  - SPEC 4 line 1086 states the invariant the code now breaks: "`--last-ms` is converted to one absolute `since_ts` before paging, so a walk that takes time does not slide its old edge."
- Failure: 3000 frames 1 ms apart, `mcu can dump -n 2500 --last-ms 3000`, 0.5 s per page.
  - Requests: `{last_ms:3000, limit:1000}`, `{last_ms:3000, limit:1000, id_to:2000}`, `{last_ms:3000, limit:500, id_to:1000}`.
  - 2001 frames printed instead of 2500, oldest printed id 1000 where the window holds 501; rc 0 and **stderr empty**: the last page's `truncated` came back false because its floor had already eaten the rows, so `note_truncated` said nothing.
- DRIVEN: `test_probe_lastms_pages.py::test_probe_can_dump_last_ms_is_resent_per_page`. Its sibling `test_probe_lines_last_ms_is_converted` is the control: `mcu lines --last-ms 5000` sends one `since_ts=1789465536.919073` and no `last_ms`.
- Class: 44 (with 17's client face: the report is the last request's answer).
- Fix: in `can_dump`, convert `--last-ms` through `_absolute_window` as `lines` does, or pin `id_to` from the first page and drop `last_ms` from every page after it. `plot export` sends `last_ms` raw too but does not page, so it is unaffected today.

### FC-2 MED: `mcu daemon restart` refuses a running daemon whose `config_path` is relative to the daemon's own cwd

- Where: `cli.py:2511-2514`: `carried = body.get("config_path")` is handed to `_named_config` (`cli.py:2363`), which does `os.path.abspath(os.path.expanduser(named))` against the **CLI's** cwd and `die`s when that misses.
- Defect: `_named_config` was written for a path the user typed; a `config_path` the daemon reports is already resolved in the daemon's frame. `server.py:424` stores `Path(config_path)` unmodified, and `daemon.py:190,369` checks it relative to the daemon's cwd, so `mcuscoped --config etc/mcuscoped.toml` is a legitimate running state with a relative `config_path`.
  - A daemon started this way cannot be restarted from any other directory, and nothing was stopped, so the user is left guessing.
  - The mirror is quieter: a same-named file in the CLI's cwd restarts the daemon on a different config.
- Failure: `/status` reporting `config_path: "etc/mcuscoped.toml"`, `mcu daemon restart` from a directory without it: rc 1, `no such config file: <cwd>/etc/mcuscoped.toml`, no `/shutdown`, no spawn, daemon still running.
- DRIVEN: `test_probe_daemonctl.py::test_probe_restart_carries_a_relative_config_path`; the absolute-path control in the same file carries and forwards it.
- Class: 67's shape (a value the handler supplied is read as a caller-supplied bound).
- Fix: run `_named_config` only on a `--config` the user gave. A carried `config_path` is the daemon's proof the file existed; forward it unchanged.

### FC-3 MED: `mcu daemon start` reports exit 1 for a daemon it started successfully, when that daemon is behind a token

- Where: `cli.py:2460-2461`, the post-spawn readiness loop, now reaching `_status_body`'s new `die` (`cli_daemonctl.py:118-121`).
- Defect: the refusal was ruled for the *pre-spawn* probe, where "it is running" must stop a second spawn. On the readiness loop the same 401 means "the daemon I just started is up and I have no token", which is a success the CLI cannot report.
  - Reachable without a remote: `mcu daemon start --config secured.toml` where the file's daemon reads `MCUSCOPED_TOKEN`, with no `MCUSCOPE_TOKEN` in the CLI's environment (the CLI's token comes only from `--token` / `MCUSCOPE_TOKEN`, `cli.py:123`).
  - It contradicts the invariant written two hunks above at `cli.py:2449-2453`: once spawned, this path must not leave a running daemon behind a failure.
- Failure: spawn succeeds, `/status` answers `401 {"error": "missing or invalid access token"}`: rc 1, `daemon at <url> refused the request (HTTP 401): missing or invalid access token`, the pid record written, the daemon left running, no `ui_url` printed. A retry then hits the pre-spawn probe and dies the same way, so `start` is unusable for that daemon.
- DRIVEN: `test_probe_daemonctl.py::test_probe_start_whose_spawned_daemon_refuses_the_probe` (first probe a connect refusal, every later one the 401).
- Class: 70's neighbour (one status, two causes) and 9.
- Fix: in the readiness loop only, treat a guard refusal as "it answered": break, print the started line plus a note that the daemon wants a token, exit 0. The round's own test covers `status` and `start` refused *before* the spawn; the post-spawn arm has no test.

### FC-4 LOW: on Windows `guard_stdout()` defeats `translate_closed_pipe_errors()`'s idempotence, so two stream layers leak per `main()`

- Where: `cli_output.py:226-229` installs `_GuardedStdout`; `_stdio.py:259` skips only a stream that `isinstance(..., _PipeErrorStream)`, and `main()` (`cli.py:2901-2902`) calls the translator first, the guard second.
- Defect: after one call `sys.stdout` is `_GuardedStdout(_PipeErrorStream(real))`, which the translator no longer recognises. Windows only, since `PIPE_CLOSE_IS_EINVAL` gates the whole path; `main()` runs more than once in a process in the test suite, which is the leg still owed on Windows.
- Failure: with `PIPE_CLOSE_IS_EINVAL` forced true, one run gives `['_GuardedStdout', '_PipeErrorStream', '_ClosedPipe']` and two runs give `['_GuardedStdout', '_PipeErrorStream', '_GuardedStdout', '_PipeErrorStream', '_ClosedPipe']`, growing by two per call. Every attribute access then walks the chain.
- DRIVEN: `test_probe_win_guard_stack.py`. The exit code is **not** affected (rc 0 on both runs): the inner `_PipeErrorStream` translates EINVAL before any guard sees it, so `_OUT_FAILED` stays clear. That was the hypothesis and it did not hold.
- Class: 14 (a fix whose idempotence is platform-gated).
- Fix: make the translator's skip walk the `_stream` chain, or give `_GuardedStdout` the same marker the translator tests for.

### FC-5 LOW: `max_size=None` removes the follow's frame cap entirely rather than raising it

- Where: `cli.py:1102-1105`.
- Defect: the comment states the real bound (500 rows of up to 4 KB, about 2 MB), then passes `None`, which is "no limit". `mcu tail -f` against a wrong service on the port, a proxy, or a future daemon that coalesces harder will buffer whatever arrives into the client's memory with nothing to stop it.
- Failure: not driven; no daemon in tree emits an oversized frame, which is why the finding is LOW and latent.
- REASONED: `websockets` treats `max_size=None` as unbounded.
- Class: none (the mirror of 48: a budget widened past the wire's rather than to it).
- Fix: `max_size=16 * 1024 * 1024`, a number the comment's own arithmetic justifies.

### FC-6 LOW: `mcu wait`'s new exit 1 for a daemon that never answers has a SPEC line and a CHANGELOG line but no `AI_GUIDE` line

- Where: `cli.py:1220-1222` (`timeout_code=1`); the guide's `mcu wait` block still reads "exit 2 on timeout ... exit 3 if the daemon stops during the wait; a daemon at its subscriber cap ... is exit 1".
- Defect: the guide is what an agent reads, and exit 2 on `wait` is a verdict an agent branches on. SPEC 4 now says exit 2 means only that nothing matched; the guide does not, so the one reader the rule exists for cannot learn it. `test_cli_contract.py` only checks option names, so nothing catches this.
- Failure: `mcu ai-guide | grep -c "never answers"` is 0 while SPEC 4 and the CHANGELOG both carry the sentence.
- DRIVEN (the text): read from `AI_GUIDE`.
- Class: 58's neighbour (in-product help that does not match the behaviour).
- Fix: one clause on the existing `mcu wait` line: "a daemon that accepts the wait but never answers is exit 1, not 2".

### FC-7 LOW: `_stop_daemon`'s "one behind a token this CLI does not hold" branch is now unreachable, and its comment still claims it

- Where: `cli.py:2559-2565`, reached only when `_status_body` returns None; a 401, 403 or 429 with an `error` envelope now `die`s inside `_status_body` first.
- Defect: the surviving branch is the startup-in-progress case alone. A reader fixing the token case will edit dead code, and the exit-1 message the comment describes is no longer the one a token-guarded stop prints.
- Failure: `mcu daemon stop` against a token-guarded daemon prints `daemon at <url> refused the request (HTTP 401): ...`, never `no usable /status from <url>, but pid N is still running`.
- REASONED from the call order; both paths are exit 1, so no behaviour changed.
- Class: none (comment drift).
- Fix: cut the token clause from the comment.

### FC-8 LOW: the two new closed-output child suites bind only `user_data_dir`, so a child's config dir is still the user's

- Where: `test_rulings_cli_closed_pipe.py:17-32` and the sibling child in `test_sweep_cli_closed_output.py` patch `platformdirs.user_data_dir` in the child and build `env` from `os.environ` rather than `support.child_env()`.
- Defect: the class-78 trap (an argv-bound lambda) is correctly avoided here, and the data dir is genuinely redirected, so the `files == []` assertions are sound. But `user_config_dir` and `user_cache_dir` are untouched, where `child_env()` moves all three on Linux. The project rule is that a spawned child uses `child_env()`.
  - Low, not higher, because the only consumer found is `config.default_config_path` (`config.py:141`) and no CLI path under test reads it.
- Failure: none observed; the suites pass and nothing was written to the real dirs in this run.
- REASONED: grep of `user_config_dir` / `user_cache_dir` across `cli*.py`, `config.py`, `update.py`.
- Class: 33.
- Fix: `env = child_env(MCUSCOPE_URL=...)` and keep the in-child `user_data_dir` patch for Windows.

### FC-9 LOW: `mcu detach a/b` reports "no such port" for a name the CLI refused without looking

- Where: `cli.py:406`: `die(f"error: no such port: {alias!r} (an alias cannot contain '/')", 1)`.
- Defect: two things at once. The lead phrase claims a lookup that never happened, and the `error: ` prefix contradicts `die`'s convention in the same file (`die` does not prefix; the neighbouring new call at `cli.py:2372` is `no such config file: ...` with no prefix, while `cli.py:1151` has one).
- Failure: `mcu detach 'a/b'` prints `error: no such port: 'a/b' (an alias cannot contain '/')`. The daemon's own wording for a real miss is `no such port: a/b`, so the two are indistinguishable by their lead.
- DRIVEN (the text): read from the source; the refusal itself is correct and `_ALIAS_RE` (`server.py:194`) confirms no alias can contain `/`, `?` or `#`, so the quoting fix beside it is right.
- Class: none.
- Fix: `die(f"invalid alias {alias!r}: an alias cannot contain '/'", 1)`.

## Hunks read

- `host/mcuscope/cli.py`: 28 of 28.
- `host/mcuscope/cli_client.py`: 1 of 1 (`probe_status`).
- `host/mcuscope/cli_daemonctl.py`: 1 of 1 (`_status_body`).
- `host/mcuscope/cli_output.py`: 2 of 2 (`err_write`'s OSError widening, `_stdout_unwritable` / `_GuardedStdout` / `guard_stdout`).
- `docs/SPEC.md`: the section 4 hunks (the exit-code paragraph, the command table rows for `detach`, `can dump`, `daemon`, the `--last-ms` bounds line) and the 3.1 / 3.4 hunks the CLI keys on (401/403/429, 503, the `/ws` close codes, `/cmd`'s shutdown 503). The 2.5, 3.3, 9.1 and 9.2 web-UI hunks were read only where a CLI surface shares the sentence.
- `CHANGELOG.md`: 8 of 8, CLI lines checked against code and SPEC.
- `AI_GUIDE` in `cli.py`: every hunk (exit codes, `detach`, `tail -f`, `can dump`, `daemon start`/`restart`/`status`).
- Tests: `test_sweep_followups.py` (full read), `test_rulings_cli_follow.py` (full), `test_rulings_cli_closed_pipe.py` (full), `test_sweep_cli_closed_output.py` and `test_rulings_cli_config.py` (negative-assertion sweep plus the helpers and every cited line), `test_cli.py` (20 hunks: the `child_env` conversions and the 1013 exit-code change), `test_cli_contract.py`, `test_cli_export.py`, `test_cli_r2026_09_12.py`, `test_cli_ux.py`, `test_prerelease_cli_fixes.py`, `test_prerelease_fixdiff_py.py`, `test_review_r2_cli.py` (removed-line diff plus the changed assertions).
- Deliberately not read: the `webui/*.js`, `server.py`, `store.py` and firmware hunks (other legs), and the non-section-4 SPEC prose.

Test notes worth carrying, none severe enough to file:

- `test_cli.py`'s `for exc, expected in ((close_1008, 1), (http_403, 1), (close_1013, 1), (http_502, 3))` now asserts only the code, and 1008 and 1013 share it, so that loop cannot tell the two refusals apart. `test_rulings_cli_follow.py:25,52` carries the text assertion both ways, so the coverage exists; the loop is just no longer the place it lives.
- Every new negative assertion in the round's CLI tests has a positive control: `files == []` against `test_a_crash_with_a_closed_stderr_is_still_logged_and_exits_1`, `"truncated" not in cap.err` against the sibling asserting `truncated at 2500 rows`, `"refused the request" not in err` against the `guard-sentinel-ZZ` case, `"too many subscribers" not in err` against the case that asserts it. Class 78 is clean here.
- The `can dump` doubles in `test_sweep_followups.py` honour the 1000-frame cap and `id_to` like the real endpoint, and the `-f` tests run against the in-process stack, so class 27 does not bite.

## The two questions

1. Least confident, and rechecked:
   - FC-1's magnitude. The first probe's fixture put the whole 3 s of frames inside a 5 s window, so nothing was dropped and it read as clean. Re-driven with `--last-ms 3000` against a 3 s span, where the floor actually crosses the walk: 499 of 2500 frames gone and no note. The finding is real; the loss scales with per-page latency, not with the number of pages.
   - FC-4 was hypothesised as an exit-code regression on Windows (`mcu ... | head -1` returning 1). Driven, and it does not happen: the layering still puts a translator under every guard. What is left is the nesting, which is why it is LOW and not MED. Nothing else here was run on Windows.
   - FC-3's reachability rests on the CLI taking its token only from `--token` / `MCUSCOPE_TOKEN` (`cli.py:123`) while the daemon takes it from `MCUSCOPED_TOKEN`. Read, not run against a real token-guarded daemon.
   - FC-5 is latent: no producer in tree emits a frame over 1 MiB, so the old bound's removal is not observable today.

2. What should have been checked and was not thought about:
   - **Every other caller of a helper the round turned into a pager.** `_fetch_newest` was reviewed as "the `/lines` pager generalised", and its `/lines` callers all pre-convert `--last-ms`. The new caller does not, and nobody asked what `can_dump` puts in `params` before handing it over. The class-44 sweep greps the CLI for `id_to`, `since_id`, `LINES_PAGE`; a *new* paged walk is exactly what it should have been re-run against, in the session that created one.
   - **A refusal added to a shared helper, at each of its call sites.** `_status_body` has six of them, and the ruling was made for two (`status`, `start`'s pre-check). The post-spawn readiness loop (FC-3) and the stop-wait loop at `cli_daemonctl.py:279` are the same function in a context where "the daemon refused" means the opposite thing. The same shape produced FC-2: `_named_config` was written for a user's path and then handed a daemon's.
   - **Dead comments left by a new early exit.** FC-7 is harmless now, but it is the same edit that made FC-3: an early `die` in a shared helper silently retires branches downstream, and nothing in the round enumerated them.
