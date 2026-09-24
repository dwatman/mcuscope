# Fix: registry sweep instances (classes 84, 85, 86, `port=""`)

HEAD at start: `88c1accf508ad40e88651244ea46e5caa54a8e91`, clean tree. Nothing committed.
Scratch, probes and mutant runner: `~/tt-data/mcuscope-2026-09-24/fix-registry/` (`probes-before.out`, `probes-after.out`, `mutants.py`, `mutants.out`, `suite.out`).

## 1. Class 84/85: host-written rows are not judged

- `host/mcuscope/server.py:2427` `_verdict_rows(chan)`: the one rule.
  - With no `chan`, only `dir` `rx` rows; with a `chan`, every row of that channel.
  - Host rows are `dir` `tx` (sends) and `-` (markers the host wrote, sys rows, daemon port `""` or a board's alias); a firmware `!m` marker is `rx` and still counts.
- `server.py:2438` `_in_rows(row, terms)` applies the terms in memory the way `store._window_terms` applies them in SQL.
- Live path: `CaptureWatch` holds `_verdict_rows(chan)` (`server.py:2464`) and filters with it (`server.py:2542`); `/wait` and live `/assert` both use it.
- Retrospective `/assert`: the scope takes `**_verdict_rows(body.chan)` (`server.py:3104`), so the match queries and the `checked_lines` count share it.
- `host/mcuscope/store.py:1820`: new `dir` term in `_window_terms`, threaded through `query_lines` and `count_lines`.
- Tests, `host/tests/test_server_live_verdicts.py` ("Class 84/85" section):
  - `test_a_live_wait_does_not_match_a_marker_the_host_wrote` (real `/marker` during a live `/wait`; `chan: marker` control matches).
  - `test_a_live_wait_does_not_match_a_sys_row` (`chan: sys` control).
  - `test_a_live_wait_named_to_a_channel_skips_the_others` (added after M5 survived).
  - `test_a_retrospective_assert_does_not_pass_on_the_hosts_own_rows` (a marker, and the tx row of a `/cmd` answered `err`; `chan` controls pass on the `-` and `tx` rows).
  - `test_a_silent_port_with_a_marker_and_a_sys_row_is_still_empty` (`chan` controls count 1; an rx row makes it `pass`).
  - `test_a_live_window_holding_only_a_marker_is_empty` (an rx row control makes it `pass`).
  - `host/tests/test_store_lines_plan.py` `test_a_dir_term_selects_by_writer`.
- Probes (`probes-before.out` / `probes-after.out`):

| probe | before | after |
|---|---|---|
| 1 retro `--expect` on a marker | pass on the marker row | fail |
| 2 retro `--expect` on own tx (cmd answered err) | pass on the tx row | fail |
| 3 live `/wait` with a marker in the window | match on the marker | timeout |
| 4 live `/wait --send` (CLI-1 control) | match on the rx reply | same |
| a silent port, no marker | empty 0 | empty 0 |
| b silent + marker, retro | pass 1 | empty 0 |
| c silent + marker, live | pass 1 | empty 0 |
| d live control | empty 0 | empty 0 |

## 2. Class 86: `console_entry` closed-stdout suppression

- `host/mcuscope/_stdio.py:414`: `console_entry(..., *, reports_closed_stdout=False)`; the warning is skipped only when that flag is set and stdout was closed (`:425`).
- `host/mcuscope/cli.py:3265`: `mcu` passes `reports_closed_stdout=True`; `mcuscoped` and `mcu-sim` do not, so they warn again as at 6e4f6f7.
- Test: `host/tests/test_stdio.py` `test_a_stdout_closed_at_start_is_warned_unless_the_script_reports_it`, parametrized over the three installed console scripts, run through `sh -c 'exec "$@" >&-'` with `child_env()` and `--help`.
  - `mcuscoped`, `mcu-sim`: warning present, exit 0. `mcu`: no warning, `closed when mcu started` present.

## 3. `port=""` selects the daemon's own rows

- `store.py:1815` `_window_terms`: `if port is not None` (every lines, CAN, plot series and plot export read goes through it).
- `store.py:163` `_lines_index`: the port+chan index hint applies to `""` too.
- Siblings: `store.py:2274` `query_plot_channels` and `store.py:2344` `_plot_channels_from_summary`: `port is not None`.
- `_unknown_port` agrees: `has_port_rows("")` is true once the daemon has written its start row, so retrospective handlers accept `""`.
  Live-only handlers refuse it (`_resolve_port`, `/ws`: `""` is never attached), and `/marker` keeps treating `""` as "no port" (it stores `""`).
  The store's subscriber broadcast already compared `is not None`.
- Tests:
  - `test_store_lines_plan.py` `test_the_daemon_port_selects_only_the_daemons_rows` (query, count, plot channels SQL and summary, with unfiltered controls).
  - A `("daemon port, chan list", "", ["sys", "marker"])` shape in `test_port_and_chan_seek_both_columns`.
  - `test_server_live_verdicts.py` `test_the_daemon_port_scopes_to_the_daemons_own_rows` (`/lines?port=`, `/plot/channels?port=`, retrospective `/assert port ""`).

## 4. Docs

- `docs/SPEC.md` 3.4: the `/wait` candidate rule (rx only unless `chan`, same on `/assert` live and retrospective); `checked_lines` counts only candidates; an empty `port=` names the daemon's port.
- `docs/SPEC.md` 4: `mcu wait` and `mcu assert` rows.
- `AI_GUIDE` (`cli.py`) PITFALLS bullet; `docs/CLAUDE_SNIPPET.md` pitfall.
- `CHANGELOG.md`: the Upgrade entry for the verdict rule rewritten with two sub-bullets; the closed-stdout Fixed entry names `mcu` and keeps the warning for the other two; a Fixed entry for `port=""`.

## Revert-verify (`mutants.py` over a private copy with its own venv, so child console scripts import the copy)

| mutant | result |
|---|---|
| M1 rule admits host rows on both paths | caught, 7 tests |
| M2 live filter back to the CLI-1 tx-only form | caught, 3 |
| M3 retrospective scope without the `dir` term | caught, 2 |
| M4 `_in_rows` ignores `dir` | caught, 5 |
| M5 `_in_rows` ignores `chans` | survived at first; caught after `test_a_live_wait_named_to_a_channel_skips_the_others` |
| M6 store drops the `dir` clause | caught, 3 |
| M7 `_window_terms` back to `if port:` | caught, 7 |
| M8 `_lines_index` back to `if port and chans` | caught, 5 (plan shapes) |
| M9 plot SQL back to `if port:` | caught, 2 |
| M10 plot summary back to `if port and` | caught, 3 |
| M11 `_stdio` suppresses for every script (the HEAD form) | caught, 2 (`mcuscoped`, `mcu-sim`) |
| M12 `_stdio` never suppresses | caught, 2 |
| M13 `cli` does not pass the flag | caught, 2 |

## Suite

- Touched files one at a time (`test_stdio`, `test_server_live_verdicts`, `test_store_lines_plan`, `test_cli_contract`, `test_cli_closed_stdio`, `test_store_plot_summary`): green.
- First whole run (`suite.out`): 17 failed, 2615 passed. Every failure was an existing test pinning the old rule:
  - `test_assert.py` (13): `_lines` seeded "device" lines as markers, and the CaptureWatch tests seeded candidates as sys rows.
    `_lines` now writes `dir` rx rows; a new `_rx` helper feeds the four watch-candidate calls; `_sys` stays where the test is about sys rows.
  - `test_cli.py` `test_assert_retrospective_pass_and_fail`, `test_assert_json_verdict`: judge `mcu mark` rows, so they now pass `--chan marker`.
  - `test_store_match_budget.py` `test_catastrophic_pattern_refused_on_retrospective_assert`: the poison target is an rx row, not a marker.
  - `test_wait_repeat.py` `test_a_later_match_keeps_the_writes_coming`: its stimulus is a marker, so the wait names `chan: marker`.
  - `test_webui_js.py` `test_export_guard_double_agrees_with_the_daemon`: the JS double (`tests/webui_js/exportdlg_guards.mjs:209`) mirrored `if port:` with `!port`; now `port === null`.
- Second whole run (`suite2.out`): 2632 passed, 1 skipped, 0 failed (7 min). `ruff check .`: all checks passed.

## Not done, and notes

- The empty `-p`, the retrospective count's cost and the stale `store.py` comment are closed in the Follow-up below.

## Follow-up (HEAD `88c1acc`, uncommitted)

Scratch: `~/tt-data/mcuscope-2026-09-24/regperf/` (`bench.py`, `bench-head.out`, `bench-wt.out`, `bench-fix.out`, `revert/mutants.py`, `revert/mutants.out`, `suite.out`, `suite-js.out`).

### 1. Retrospective `/assert` cost of the `dir` term

- Method: `bench.py` runs the handler's sequence (`_resolve_window`, one `query_lines_safe` per pattern, `count_lines_safe`) on a copy of `perf/big.db` (6M lines: 6M board rx, 1200 tx, 96 `-`, 48 sessions of 125k).
  - HEAD from a `git worktree` at 88c1acc (removed), the working tree via `PYTHONPATH`; `time.time` pinned just past the newest row; median of 3 after a warm-up.
  - Two shapes: an expect that hits at once (the count dominates) and a forbid that never matches (reads the whole window).
- The pattern queries cost the same on both (the `dir` term is a filter on rows they read anyway). The count did not: with the term, no index serves it.

| window, scope | HEAD count | `dir` term | fixed |
|---|---|---|---|
| last 60 s | 5.1 ms | 12.6 ms | 4.9 ms |
| last 60 s, `-p board` | 15.6 | 21.4 | 16.1 |
| last 1 h | 10.3 | 24.1 | 10.2 |
| last 1 h, `-p board` | 33.8 | 40.0 | 31.1 |
| last 1 d (500k) | 43.6 | 97.0 | 44.4 |
| last 1 d, `-p board` | 128.3 | 160.1 | 139.6 |
| whole capture (6M) | 47.4 | 932.0 | 44.0 |
| whole capture, `-p board` | 531.9 | 3751.4 | 509.1 |
| `--session run-047` | 7.4 | 20.1 | 7.5 |
| `--session run-047`, `-p board` | 10.4 | 35.4 | 10.6 |
| `--session run-023` | 12.0 | 22.7 | 12.3 |
| `--session run-023`, `-p board` | 11.7 | 37.1 | 12.5 |

- A never-matching forbid: 60 s about 170 ms, 1 h 360 to 400 ms, 1 d 1.4 to 1.6 s, whole capture 17.7 to 18.8 s, the same on all three.
- Plans: the `dir` term took the count off `COVERING INDEX idx_lines_ts` / `idx_lines_port_id` onto the table (`SCAN lines` for the whole capture).
- Fix, `store.py`:
  - Partial index `idx_lines_host ON lines(id) WHERE dir <> 'rx'` (the host's rows only; built on an older capture's first start, 0.9 s at 6M lines).
  - `count_lines(dir="rx")` is one statement, every row of the window less `INDEXED BY idx_lines_host ... dir <> 'rx'`; plan `SCALAR SUBQUERY 1 ... COVERING INDEX idx_lines_ts|idx_lines_port_id | SCALAR SUBQUERY 2 ... idx_lines_host`.
  - One statement, so both counts read one snapshot. Other `dir` values keep the plain term (no caller).
  - Fixed `checked_lines` equal the unfixed ones in every row (the `dir` term's own count), and 6000048 = 6001344 less 1296 host rows.
- `docs/SPEC.md` 3.5 lists the index; `CHANGELOG.md` "builds one index" is now "two indexes".
- Tests, `test_store_lines_plan.py`:
  - `test_an_rx_count_is_every_row_less_the_hosts_in_any_scope`: nine scopes (port, `""`, absent port, chans, id bounds, `last_ms`) against the rx rows `query_lines` returns; tx and `-` controls.
  - `test_an_rx_count_reads_dir_only_through_the_host_index`: plan uses `idx_lines_host` and a covering index, never a bare `SCAN lines`.

### 2. Empty `-p`

- `cli.py` `_global`: `port == ""` is `die("-p/--port is empty: name a port alias, or leave -p out to span every port", 1)`, before any request, for every command (`-p` is only a global option).
- `AI_GUIDE` PITFALLS and SPEC 4 (after the unknown `-p` sentence) say so; `CHANGELOG.md` Fixed entry.
- Test: `test_cli_argv.py` `test_an_empty_port_is_refused_before_any_request`: `lines`, `tail`, `cmd` (`--port=`), `wait`, `assert`, `plot channels`, `-p` after the subcommand, `status`, and `--json`; against an unreachable url, with a named-port control that reaches it (exit 3).

### 3. Stale comment

- `store.py` `query_can_frames`: "exactly as `+port` does in _window_terms" removed.

### Revert-verify (`revert/mutants.py`, private copy of `host/`, three touched test files)

| revert | result |
|---|---|
| R1 no empty `-p` check | caught, 1 |
| R2 no subtraction, `dir` dropped | caught, 4 |
| R2b the unfixed form (plain `dir` term) | caught, 1 (plan test) |
| R3 subtraction with the `dir` term also kept | caught, 1 (plan test) |
| R4 host subquery without `dir <> 'rx'` | caught, 7 |
| R5 host subquery counts tx only | caught, 3 |
| R6 schema without `idx_lines_host` | caught, 7 |
| R7 every `dir` dropped from the window terms | caught, 1 |

- Unmutated copy: 97 passed. The comment edit has no test.

### Suite

- `uv run python -m pytest -q`: 2635 passed, 1 skipped (7 min 4 s). Then `tests/test_webui_js.py -p no:randomly`: 2 passed. `ruff check .`: clean.
