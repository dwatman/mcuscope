# Fix batch: server

Revert-verification: `~/tt-data/mcuscope-2026-10-05/fix-server/mutate.py` applies 21 mutations to `server.py` one at a time and runs the pinning test file. All 21 are caught (`mutate.log`), and the file is restored byte-identical (cmp).

## SEC-3
- `server.py:3718` `_FORMULA_START`, used by `_csv_cell`: a formula character (`= + - @` TAB CR) is guarded at the start of the cell and after `;`, TAB, CR or LF inside it.
  - LF and CR go beyond the triage text. A `;`-reader that ignores the quotes starts a row there.
- Test: `tests/test_server_csv_formula.py`, unit cases plus `/lines/export?format=csv`.
- Revert: caught for the whole regex and for the LF/CR lookbehind alone.

## SEC-4
- `server.py:970` `_BodyLimit`, registered innermost at `:655` so the guards refuse first. It answers 413 when `Content-Length` > 64 KiB, before reading any of the body. A chunked body is read up to the cap and then replayed.
- `:636`: `got` in a 422 is cut to 80 characters, followed by `... (N characters)`.
- Test: `tests/test_server_request_limits.py`, over real uvicorn: a 2 MB declared length with no body sent, a chunked body over the cap, a chunked body under the cap that reaches the route whole, exactly at the cap and one byte over, the 403-before-413 order, and the 422 cut.
- Revert: caught for the length cap, the chunked cap, the replay, the middleware order and the cut.
- I dropped a non-digit `Content-Length` check: uvicorn's parser refuses that first, so no test could reach it.

## SEC-7
- `server.py:800` `_throttle_key`: an IPv6 client is keyed by its /64, and an IPv4-mapped address by its IPv4 address. `_TokenGuard` uses this key for lookup, failure and clear.
- Test: `tests/test_server_token_throttle.py`, which rotates addresses inside one /64 (and checks that the next /64 is separate) and covers IPv4 and the mapped form.
- Revert: caught for the /64 key, the mapped form, and the key's use in the guard.

## AGENTUX-4 (daemon side)
- `server.py:2914`: a cmd-mode `send` on `/wait` answered `err` or `timeout` returns at once with `status: "send_failed"`, `line: null` and the answer in `cmd_result`. A raw send is unaffected.
- Test: `tests/test_server_verdict_outcomes.py`, which covers ERR with `--match .` (the old false "match"), a send timeout, an ok send as positive control, and a raw send.
- Revert: caught.

## AGENTUX-7 (server wording)
- `server.py:3330-3355`: every `/assert` mode refusal that names `timeout_ms` now names the flag too (`; --timeout on the CLI`, or `; drop --timeout on the CLI`).
- Test: `test_a_mode_refusal_names_the_cli_flag`, which checks the full text of all 5 messages. Revert: caught.

## AGENTUX-9 (`/send` line_id)
- `server.py:2021`: the reply is `{"ok": true, "line_id": n}`, taken from the row `send_raw` returns (the link batch's half has landed).
- Test: `test_e2e.py::test_send_raw_logged` now asserts that the id equals the stored tx row's id. Revert: caught.

## `/status` fields
- `server.py:1280`: adds `lines_expired`, `db_locked_since` and `capture_error`, read directly from the store attributes.
- The ports' `rx_replaced` and `plot_name_refused` come through `pt.status()` unchanged.
- Test: `tests/test_server_status_and_listing.py`, which sets the store values and reads them back, and checks the port counters on a Stack. Revert: caught.

## Capture lock tick check (from the daemon batch)
- `server.py:485-487`: the lifespan calls `store.add_tick_check(app.state.capture_lock.verify)` when the daemon has set a lock.
- Tests:
  - A spy shows the check is registered with a lock and not without one.
  - End to end, a real `CaptureLock` whose file is unlinked makes `_run_tick_checks()` return False. `/status` then shows `capture_error` "capture lock file replaced or removed..." and `writer_alive: false` (POSIX only).
- Revert: caught.

## OP-1
- SPEC 3.1 now says the daemon is for a single-user machine, and that on a shared host every local account controls it, including `POST /ports` and squatting 8558. No code change.

## OP-2 (`/plot/channels`)
- `server.py:2190-2215`: `limit` (default and clamp `PLOT_CHANNELS_MAX = 1000`, negative is 422) and `truncated`. Past the limit the most recently sampled channels stay (`last_line_id`), still in name order.
- Tests: `test_plot_channels_keeps_the_most_recent_under_a_limit_and_says_so` and `..._is_clamped_and_validated`.
- Revert: caught for most-recent, clamp and truncated.

## OP-9
- `server.py:331` adds `AssertBody.allow_dropped`. At `:3380`, a verdict with `dropped > 0` and `checked > 0` is `incomplete` unless a forbid matched (a decided `fail`) or `allow_dropped` is set.
  - Reason text: `N lines were dropped unjudged; retry, or set allow_dropped`.
  - This also covers an unmet `expect` over holes, which is `incomplete` rather than `fail`, because SPEC says not to treat that as a negative.
- Tests: `tests/test_server_verdict_outcomes.py` (5 cases plus a 422 for a non-boolean).
- Revert: caught for the branch, the opt-in, and the forbid-decided condition.

## Existing tests edited
- `test_e2e.py::test_send_raw_logged`: `/send` now carries `line_id`, and the test asserts it.
- `test_e2e.py` (raw junk test): `== {"ok": True}` became `["ok"] is True`.
- `test_server_live_verdicts.py::test_wait_with_send_still_matches_when_the_send_used_the_whole_window`: it used a timed-out send, which now ends as `send_failed`. I switched it to a send answered at the deadline, as its `/assert` sibling already does, so the drain stays pinned.
- `test_server_request_validation.py::test_marker_port_is_bounded_like_the_alias_grammar`: the 100k-character port became 60k so the route, not the 413, refuses it.
- `test_server_scope.py`: `/plot/channels` added to the route set that takes a `limit`, and to the negative-limit cases.

## SPEC edits
- 3.1: the single-user trust boundary (OP-1), and the token lockout per IPv6 /64.
- 3.4 error list: 413 added, and the 422 quote cut at 80 characters.
- 3.4 `/status`: `lines_expired`, `db_locked_since`, `capture_error`, and the port fields `rx_replaced` and `plot_name_refused`, one line each.
- 3.4 `/send`: returns `line_id`.
- 3.4 csv: the guard after `;`, tab and line breaks.
- 3.4: a new `GET /plot/channels?port=&limit=1000` paragraph with `truncated`. The route's main description lives in 9.2, which belongs to the webui batch.
- 3.4 `/wait`: `send_failed`.
- 3.4 `/assert`: `allow_dropped`, `incomplete`, and its reason text.

## Guide wording (for cli)
- `wait`: "status `send_failed` (exit 1): the `--send` command was answered ERR or not at all; the call ends at once and `cmd_result` says which."
- `assert`: "status `incomplete` (exit 1): rows were shed from the window (`dropped > 0`) and no forbid matched, so the verdict is unproven; retry, or pass `--allow-dropped` to judge it anyway."
- `send --json`: "`line_id` is the stored tx row; pass it as `--since-id` to read the board's answer."
- `plot channels --json`: "at most 1000 channels, the most recently sampled; `truncated: true` when more exist."
- `status`: "`capture_error` non-null means capture has stopped (the file was replaced or its lock lost), and restarting the daemon is the fix. `db_locked_since` means another process holds the capture's write lock. `lines_expired` counts lines deleted for age."

## Changelog
- Request bodies over 64 KiB are refused with 413, and a 422 quotes at most 80 characters of the rejected value.
- CSV exports guard formula characters after `;`, tab and line breaks, for `;`-separated spreadsheet locales.
- Wrong-token lockout is per IPv6 /64.
- `/wait` with a cmd-mode send answered ERR or unanswered ends at once with status `send_failed`.
- `/assert` over a window with shed rows answers `incomplete` unless a forbid matched; `allow_dropped` opts out.
- `/send` returns the stored row's `line_id`.
- `/plot/channels` takes `limit` (max 1000) and reports `truncated`.
- `/status` reports `lines_expired`, `db_locked_since` and `capture_error`.
- A lost or replaced capture lock stops the capture and is reported on `/status`.
- SPEC states the single-user trust boundary.

## Not done
- cli: `--allow-dropped` on `mcu assert` (body `allow_dropped: true`). It also needs exit 1 for `incomplete` and `send_failed`, the SPEC 4 table, and the guide lines above.
- webui (SPEC 9.2, line about `GET /plot/channels`): optionally mention `limit`/`truncated`. The UI asks for at most 32 channels and is unaffected.
- Store batch: these fail with the original `server.py` as well, so they are not mine:
  - `test_assert.py::test_sweep_tick_survives_a_failing_sweep` and `::test_sweep_tick_runs_the_age_sweep_only_when_the_hour_divides`.
  - `test_server_scope.py::test_plot_export_streams_the_window_its_count_guarded` and `::test_a_retrospective_assert_judges_every_pattern_over_one_window` ("the window floor never read the clock this test moves").
  - `test_server_live_verdicts.py::test_a_silent_port_with_a_marker_and_a_sys_row_is_still_empty`: `chan: sys` with `port: quiet` now counts 2 rows. That is OP-7's `port IN (X, '')` reaching a verdict with a named chan; per OP-7, verdicts were to keep excluding the host's own rows.

## Doubts
- 64 KiB versus the largest legitimate body. `PUT /config/ports` with 64 ports of 512-character devices is about 47 KB in ASCII, but escaped non-ASCII could pass the cap. I did not measure a maximal config.
- SPEC says `db_locked_since` means "captured lines wait meanwhile". That comes from the store batch's RES-1 design, which I did not read.
- Not checked:
  - The 413 over a real `0.0.0.0` bind with a token (the middleware order is pinned only in process).
  - Windows behaviour of the chunked path.
  - Whether `send_failed` should also carry a `reason` field like `/assert`. I left the shape as `/wait`'s, with the answer in `cmd_result`.
