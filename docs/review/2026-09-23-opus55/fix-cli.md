# Fix batch: cli (2026-09-23)

Files: `cli.py` (incl. `AI_GUIDE`), `cli_client.py`, `cli_output.py`, `cli_argv.py`, `cli_daemonctl.py`, `_stdio.py`, `render.py`; SPEC 4.
`pidfile.py` needed no change.
Revert-verification: `~/tt-data/mcuscope-2026-09-23/fix-cli/mutate.py` applies each mutation alone, runs its pinning test, restores the file; all anchors are checked before any file is touched.
Every mutation listed below was CAUGHT (the pinning test failed) unless the block says otherwise.
Scratch: `~/tt-data/mcuscope-2026-09-23/fix-cli/`.

## Per finding

### CLI-1 (guide half)
- Guide PITFALLS: wait/assert `--send` never match their own `dir=tx` row unless `--chan` names its channel. SPEC 4 `mcu wait` row says the same.
- Driven live (sim daemon, server batch in tree): `wait --send "can tx 7FF AABB" --match "7FF AABB"` now times out (exit 2) instead of matching its own line.

### CLI-2 (client half)
- `cli.py` `wait`: `cmd_result.status == "err"` prints `--send '<cmd>' was refused: ERR ...` on stderr and exits 1 (text and `--json`), also when a line matched (the ruling says "exits 1 when err"). A send with no response keeps exit 2 and the timeout line says `(sent 1, the command got no response)`.
- `cli.py` `assert`: a `cmd_result` of err/timeout prints `  FAILED  send '<cmd>': ERR ... ; the window judged no stimulus`.
- `cmd_err_text` moved into `cli_output.py`, shared with `emit_cmd_result`.
- Tests: `test_cli_send_verdicts.py` (6 wait/assert send cases, incl. the ok positive control).
- Mutations M22, M23, M24 caught.
- Driven live: `assert --send reset --forbid PANIC` gives FAIL with the ERR line, exit 1; `wait --send "i2c rd 99 2"` gives `ERR 2 badarg`, exit 1.

### CLI-3 (client half)
- `assert --allow-empty` sends `allow_empty: true` (only when given); `status: "empty"` prints `EMPTY  the window held no lines ...; --allow-empty accepts it` on stderr, exit 1.
- Reads refused with 400 `no such port` exit 1 through the existing `Client.fail`.
- Tests: `test_cli_send_verdicts.py` (empty text/json, allow_empty body present/absent), `test_cli_read_scope.py::test_a_read_naming_no_such_port_is_exit_1` (lines, tail, log export paged and streamed, can dump, plot channels).
- Mutations M25, M26 caught.
- Driven live: `-p nosuch lines/tail/log export/can dump` all exit 1 `error: no such port: nosuch`; `--last-ms 1` is EMPTY exit 1, `--allow-empty` PASS exit 0.

### CLI-4
- `cli.py` `_fetch_after`: `lines --since-id N` walks upwards with `order=asc`, paging, returning the `--limit` rows just above N; `truncated` means more follow.
- The note reads `newer matches exist (raise --limit or call again with --since-id <newest>)`.
- `note_truncated` default remedy (was `use --since-id`, cli_output.py:469) is now `use 'mcu log export' for every row`.
- `_pin_ceiling` factored out of `_iter_pages_asc` for the `until_ts` id ceiling, shared by both walks.
- Guide REST recipe: `GET /lines?since_id=N&order=asc&limit=1000`, repeat from the last id while `truncated`.
- Tests: `test_cli_read_scope.py` (1200 rows above id 100 over two pages, the exact page requests; the newer-note text; the older-note no longer offers `--since-id`).
- Mutations M11, M19, M46 caught.

### CLI-5
- `render.fmt_line(row, show_port=False)`: `HH:MM:SS.mmm [port] chan| raw`.
- `cli._port_column`: a finished result (lines, tail snapshot) shows the column when its rows span more than one port; a stream (tail -f, log export, the wait line) when no `-p` and more than one port is attached (one `GET /ports` probe).
- A multi-board text `log export` renders the paged rows client-side instead of the daemon's text (which has no port column).
- `-p` help and guide say which commands need it and that reads span every port.
- Tests: `test_render_line_breaks.py`, `test_cli_read_scope.py` (two ports, one port, `-p`, export routing both ways), `test_cli_follow_frames.py` (tail -f, wait).
- Mutations M2, M20, M21, M43 caught.

### CLI-6, CLI-7, CLI-8, CLI-9, CLI-13, CLI-14 (guide)
- CLI-6: the "whole run" example is `log export`; `lines --limit` help and guide say it counts raw rows before `--changes`/`--names`.
- CLI-7: `lines --json` newest first; the JSONL of tail/log export/can dump oldest first.
- CLI-8: PITFALLS line and `send` docstring: a monitor ignores `send`; the example is `mcu send "boot 0"`.
- CLI-9: check `mcu ports --json` (`ports[].connected`) first; the "no port-state flag" sentence is gone.
- CLI-13: `open_failed` retries on its own; `socket://` reads `open_failed` when the far end is down.
- CLI-14: follow an `--eol none` send to a monitor with `mcu send ""` (driven live: the next `cmd ping` answered).

### CLI-10
- `cli_client._daemon_errors`: a transport timeout after connect is exit 1, `the daemon at URL accepted the request but stopped answering`. The `timeout_code` parameter is gone.
- Tests: `test_cli_transport_timeouts.py` (six commands on a ReadTimeout, a real accept-and-stall socket, ConnectTimeout stays 3, a board timeout the daemon reported stays 2).
- Mutation M12 caught.

### CLI-11
- `_stdio.stdout_was_closed()` (POSIX, stdout None at start); `cli._dispatch` marks output unwritable, so 0 becomes 1 with `cannot write output: ... stdout was closed when mcu started`.
- `_follow_ws` and `_dump_follow` end at once with 1. The devnull repair stays for Windows.
- Tests: `test_cli_closed_stdio.py` (`ai-guide` and `--json ai-guide` under `>&-`, open-stdout control, `tail -f >&-` ends before contacting the daemon with the exit-3 control), `test_cli_follow_frames.py::test_can_dump_follow_ends_at_once_when_stdout_was_closed`.
- Mutations M15, M36, M37, M38 caught.
- Driven: `.venv/bin/mcu status >&-` exit 1; `tail -f >&-` exit 1.
- Not through `uv run`: uv hands the child another fd 1 (driven: exit 0), which is uv's behaviour, not the CLI's.

### CLI-12
- `confirm_or_exit` refuses whenever stdin is not a terminal, both modes: `stdin is not a terminal; pass -y to confirm`.
- Test: `test_cli_closed_stdio.py` (text and json: preview only, no stdin byte consumed; a tty positive control deletes).
- Mutation M9 caught.

### CLI-15
- WS URL quotes `-p`.
- An HTTP status on the upgrade is exit 1, except a gateway's 502/504, which stays 3 (see Doubts).
- Tests: `test_cli_follow_frames.py` (quoted URL; 400/404/500/401 exit 1; no answer exit 3); existing `test_cli.py::test_follow_ws_auth_and_capacity_refusals_are_exit_1` pins 502 as 3.
- Mutations M34, M35, M35b caught.

### CLI-16 (guide)
- PITFALLS block near the top covers CLI-1, 2, 3, 5, 8, 9, 18: `-p` on writes, unknown `-p`, send vs cmd, wait sees only later lines, tx rows, a refused send, the empty verdict, `--limit` counting, `--`, prompts, terminal escaping.
- Added:
  - `cmd ping`/`cmd info`;
  - which commands print JSONL;
  - `lines --limit 0` versus `log export`'s every-row default;
  - a retrospective assert with no scope judges the whole capture;
  - `daemon start` on a running daemon is exit 1.
- Condensed: PlotJuggler, the purge matrix (one line), bundle internals, pre-0.4.0 gating (one sentence), completion.
- 18.0 KB to 16.9 KB. Every option is still named as a token (`test_cli_contract.py::test_ai_guide_names_every_flag` passes, including HEALTH-21's `-c`, `-t`, `-y`).

### CLI-17
- Refusals in option names: `--repeat-ms needs --send`, `--repeat-ms must be between 10 and --timeout (N)`, `--min-window needs a live window (give --timeout too)`, `--min-window cannot exceed --timeout`, `--eol applies to --send; give --send too`. Mutations M45, M47, M48 caught.
- `plot export` error `see /plot/channels` becomes `see 'mcu plot channels'` (`Client.fail`). M13.
- `daemon stop` with nothing running: `no daemon is running at URL; nothing to stop`. M44.
- `status` with no ports prints `no ports attached (...)`. M42.
- `cmd` with no data prints `ok`. M10.
- `tail -n 0` and `can dump -n 0` print no truncation note. M39, M40.
- `mark "-pwm duty 50"`: the attached `-pNAME` hoist takes only an alias-shaped remainder (`cli_argv._ATTACHED_P`), and `mark`/`send` take unknown options as their text (`ignore_unknown_options`). M14, M32.
- `send -` refused (`send does not read stdin`). M31.
- `purge --id-from 500 --id-to 100` refused before any request. M29.
- `attach` over an existing alias with a different target prints `note: X was attached to OLD; it now names NEW` (one `GET /ports` first); the same target is quiet. M30, M30b.
- Tests: `test_cli_small_refusals.py`, `test_cli_read_scope.py`, `test_cli_send_verdicts.py`.

### CLI-18 (client half)
- Guide, `-p` help and SPEC 4 state the rule.
- `Client.fail` no longer doubles the alias list the server now names (`... one of: sim, b2 (with -p)`); against an older daemon it still fetches and lists them.
- Test: `test_cli_small_refusals.py::test_an_ambiguous_port_names_the_aliases_once_and_the_option`. Mutations M49, M50 caught.
- Driven live with two ports: `send hello` and `cmd ping` without `-p` refused, exit 1.

### API-7
- `cli_output.visible()`: SGR (`ESC [ ... m`) kept, every other C0/C1 control but TAB/LF shown as `\xNN`.
- Applied in `_GuardedStdout.write` when stdout is a TTY and not `--json` (every print and export write crosses it), and in `err_write` when stderr is a TTY. `render.fmt_line` makes no TTY decision.
- Tests: `test_cli_terminal_controls.py`. Mutations M3, M4, M5, M6 caught.
- Driven under `script` (a pty): OSC 52 shown escaped, SGR kept; through a pipe the bytes are as captured.

### CAPTURE-9
- `render.fmt_line` shows `\n \r VT FF FS GS RS NEL U+2028 U+2029` as `\xNN`/`\uNNNN`, so each row is one line in the daemon's text export and CLI text.
- Test: `test_render_line_breaks.py` (each boundary, and a positive control for TAB/ESC/non-ASCII). Mutation M1 caught.

### LIFECYCLE-1
- `cli_daemonctl._stop_running_daemon(s, pid_path, pid)`: only the pid a local record names is signalled or waited on.
- With no record: `/shutdown` alone, success judged by `/status` going quiet, printed as `stopped mcuscoped at URL (no local pid record: asked it to shut down, signalled nothing)`. A refused shutdown dies with `no local pid record names it, so no process was signalled`.
- `/status`'s pid is now only compared (in `daemon start`), never signalled.
- Tests: `test_cli_daemon_stop_scope.py`, a fake daemon reporting a live local process's pid:
  - refused shutdown: the process survives;
  - accepted shutdown: the process survives, exit 0;
  - a record naming it: it is signalled and the record removed (positive control).
- Mutation M16 (pass the /status pid again) caught: the victim was killed.
- No new wire field was needed.

### LIFECYCLE-5
- Driven before the fix (two concurrent `daemon start` on 18880, configs with distinct unknown keys): the serving daemon's `bogus_key_two` warning was wiped from the `.err`, only the loser's lines remained. After the fix both lines are present.
- `cli._start_daemon` opens the `.err` with `"ab"` and records the offset; `_stderr_tail(start=)` shows only this start's lines.
- The file grows by the daemon's warnings only (log level warning); no rotation.
- Test: `test_cli_daemon_stop_scope.py::test_a_start_appends_to_the_stderr_file_and_shows_only_its_own_lines`. Mutations M17, M18 caught.

### HEALTH-2
- `LineDecoder.decode`: a `!pd`-prefixed line `learn()` rejects is returned as is (`!pdo 5V 3A`, `!pd`, a malformed definition), where it was dropped.
- Test: `test_cli_decode_rejected_defs.py`. Mutation M7 caught.
- The suggested first-token dispatch was implemented, then removed: `learn()`/`feed()` check the tag themselves, so it changed no output (mutations M8/M8b survived) and was redundant.

### HEALTH-3
- `plot channels` sends `port` when `-p` is given (the parameter exists since 0.2.0, so no gate).
- Test: `test_cli_read_scope.py::test_plot_channels_sends_the_port`. Mutation M41 caught.

### HEALTH-6
- `sysrq` requires `char.isascii() and char.isprintable()` before any request.
- Test: `test_cli_small_refusals.py` (no request at all for `é`; `b` sends break then send). Mutation M33 caught.

### HEALTH-15 pinning tests
- C04: `test_cli_follow_frames.py::test_a_non_utf8_binary_frame_is_skipped_not_fatal`.
- C06: `test_cli_read_scope.py::test_a_paged_export_that_dies_mid_walk_leaves_no_file`.
- D01: `test_cli_daemon_stop_scope.py::test_a_daemon_still_answering_after_the_stop_is_reported`.
- C11: `test_cli_daemon_stop_scope.py::test_start_timeout_below_half_a_second_is_honoured`. A fake clock advances only by the loop's sleep; it asserts exactly one readiness probe for `--timeout 0.05`.
- B08: `test_cli_follow_frames.py::test_a_successful_poll_restarts_the_give_up_clock`. One failure, answered polls to 10 s, then failures; interrupted at 35 s, which only a reset clock survives.
- Probes are adapted from `health/mut/probes/`. The mutants were not re-run under these files: C04, C06 and D01 match the probe tests; C11 and B08 are new.

### HEALTH-21 (guide half)
- `-c/--config`, `-t/--timeout`, `-y (--yes)` appear as tokens.

### HEALTH-24
- Deleted `cli._value_taking_opts`, `cli._hoist_global_opts` and `cli_argv.hoist_global_opts`; `cli._split_global_opts` stays (used by `_dispatch`). Tests repointed (see below).

### HEALTH-26 (from the tests batch)
- `cli_client.py` docstring: `probe` does not route through `_daemon_errors`.

### PERF-3
- Match-bearing row reads (`_get_rows`: /lines pages, tail, the decoder's prime) wait `READ_TIMEOUT_S + MATCH_BUDGET_S` (60 s), others 30 s.
- A retrospective `assert` waits `(patterns) x MATCH_BUDGET_S + 30 s`; a live one waits its window + 30 s.
- Tests: `test_cli_transport_timeouts.py` reads the read timeout httpx was given. Mutations M27, M28 caught.

## Existing tests edited

- `test_cli.py`:
  - `test_hoisting_survives_a_command_tree_it_cannot_read` and `test_hoisting_is_a_pure_rewrite`: through `cli_argv.value_taking_opts`/`_split_global_opts` (HEALTH-24).
  - `test_read_timeout_exit2_not_unreachable` renamed `..._exit1_...`, asserts 1 (CLI-10).
  - `test_daemon_start_pid_file_is_keyed_by_host_port` and `test_daemon_stop_no_pidfile_exit1`: `nothing to stop` (CLI-17).
  - `test_purge_without_yes_asks_...`, `test_purge_prompt_never_lands_on_stdout` and `test_session_delete_prompt_never_lands_on_stdout`: a piped stdin is refused with `pass -y` (CLI-12).
  - `test_tail_follow_subscribes_before_its_snapshot`: its handler answers the `/ports` probe apart from the snapshot (CLI-5).
- `test_cli_contract.py` (tests batch, finished): min-window and eol refusal wording (CLI-17).
- `test_regressions.py`: a local `hoist()` over `_split_global_opts` (HEALTH-24).
- `test_review_r2_cli.py`: the hoist test through `_split_global_opts` (HEALTH-24); the truncation remedy text (CLI-4).
- `test_hardening.py::test_hoist_token_equals_form`: through `_split_global_opts` (HEALTH-24).
- `test_sweep_followups.py::test_a_daemon_that_never_answers_the_verdict_is_exit_1_not_2`: `stopped answering` (CLI-10).
- `test_cli_ux.py`:
  - the failed-start test: the old bytes stay in the file, and are not in the tail (LIFECYCLE-5);
  - `test_a_sole_connected_port_is_not_ambiguous` becomes `test_a_write_without_p_is_refused_with_two_ports_attached` (CLI-18: server behaviour, plus the aliases listed once);
  - `test_port_help_names_the_rule`: the new `-p` help.
- `test_rulings_cli_closed_pipe.py::test_the_repair_warning_on_a_closed_stderr_does_not_own_the_exit`: `--help` with stdout closed at start is 1 (CLI-11).
- `test_cli_r2026_09_12.py`, three attach tests, and `test_prerelease_cli_fixes.py::test_attach_strips_the_serial_it_posts`: read the POST as `seen[-1]`, since the attach alias check comes first (CLI-17).
- `test_prerelease_cli_fixes.py::test_the_same_fields_reach_a_current_daemon`: the request count leaves out attach's `GET /ports`.
- `test_wait_repeat.py::test_cli_refuses_a_repeat_with_nothing_to_send` and `::test_cli_refuses_a_period_outside_the_window`: option-name wording (CLI-17). The server batch is editing the same file; my edits were two exact-anchor replacements.

- `test_timeline.py::test_lines_limit_above_the_cap_is_honoured`: the truncation remedy text (CLI-4).

## Test runs (one file per pytest call, final tree)

- All pass: `test_cli` (167), `test_cli_ux`, `test_review_r2_cli`, `test_cli_r2026_09_12`, `test_fixdiff2_cli`, `test_prerelease_cli_fixes`, `test_rulings_cli_closed_pipe`, `test_rulings_cli_config`, `test_rulings_cli_follow`, `test_sweep_cli_closed_output`, `test_cli_export`, `test_wait_repeat`, `test_assert`, `test_break`, `test_stdio`, `test_decode_per_port`, `test_security`, `test_hardening`, `test_regressions`, `test_pidfile`, `test_sweep_followups`, `test_cli_contract`, `test_daemon_startup`, `test_e2e`, `test_eol`, `test_plot_export_decode`, `test_plot_export_since_id`, `test_export_lines_can`, `test_session_bundle`, `test_sessions`, `test_timeline`, `test_webui`, and the 10 new files.
- An earlier `test_cli` run had 69 errors ("stack did not become ready") from another batch's in-flight `server.py`/`store.py` state. The final run is clean.
- The whole suite and the JS suite were not run (brief).
- ruff is clean on every file I touched.

## SPEC edits (section 4 only)

- Global options: the `-p` rule for writes (refused whenever more than one port is attached), that reads span ports with a `[port]` column, and that an unknown `-p` is refused.
- Exit codes: 1 includes a daemon that stopped answering, on every command; 2 is a timeout the daemon reported. Match-bearing requests outwait the daemon's match budget.
- stdout closed at start is exit 1 and ends a follow; terminal control rendering (SGR kept); prompts need a tty; a WS upgrade status is 1 (3 for no answer or a gateway 502/504).
- Table rows:
  - `cmd` prints `ok`;
  - `send`: a monitor ignores it, a text may start with `-`, a lone `-` is refused;
  - `sysrq`: ASCII, refused before the break;
  - `tail`: `[port]` format, `-n 0 -f` gives no note;
  - `lines`: `--limit` counts raw rows, `--since-id` semantics;
  - `wait`: an ERR send is exit 1, tx rows are skipped;
  - `assert`: `--allow-empty`/`empty`, a failed send;
  - `purge`: an inverted id range is refused;
  - `plot channels`: `-p`;
  - `daemon`: the `.err` is appended, `stop` signals only a recorded pid.

## Changelog

- `mcu wait --send` whose command the monitor refuses exits 1 with the ERR on stderr; `mcu assert --send` prints the failed send and fails.
- `mcu assert` over a window with no lines reports `empty` (exit 1); `--allow-empty` accepts it.
- `mcu lines --since-id N` returns the next rows above N (oldest first from the daemon); `truncated` means call again from the newest id.
- Text output names each row's port (`[port]`) when rows from more than one board are shown.
- On a terminal, control bytes from a board are shown escaped (`\x1b`, `\x07`); colour is kept. JSON, pipes and files are unchanged.
- Text rendering (`mcu lines`/`tail`/`log export` and `/lines/export?format=text`) shows VT, FF, FS, GS, RS, NEL, U+2028 and U+2029 escaped, so each row is one line.
- A daemon that accepts a request but never answers is exit 1 on every command ("stopped answering"), no longer 2.
- A stdout closed at start (`>&-`) is exit 1 and ends `-f` follows at once.
- Confirmation prompts are refused unless stdin is a terminal (pass `-y`).
- `mcu daemon stop` signals a pid only when a local pid record names it; a daemon on another machine is asked to shut down and never signalled.
- `mcu daemon start` appends to the daemon's `.err` file, so a start that loses a race no longer wipes the running daemon's log.
- `mcu tail -f` quotes `-p` in its WebSocket URL; an HTTP refusal of the upgrade is exit 1.
- `mcu plot channels` honours `-p`.
- `mcu sysrq` refuses a non-ASCII character before sending the break.
- `--decode` shows `!pd`-prefixed lines it cannot learn (`!pdo ...`, malformed definitions) instead of dropping them.
- Match-bearing reads and retrospective `mcu assert` wait past the daemon's 30 s match budget.
- `mcu mark`/`mcu send` accept text starting with `-`; `mcu send -` is refused.
- `mcu purge` refuses `--id-from` above `--id-to`.
- `mcu attach` notes when an alias is moved to another device.
- `mcu cmd` prints `ok` when the response carries no data.
- `mcu status` says when no ports are attached.
- `mcu tail -n 0 -f` / `can dump -n 0 -f` print no truncation note.
- Refusals name CLI options (`--repeat-ms`, `--min-window`, `--eol`, `--send`), not daemon fields.
- `mcu ai-guide`:
  - gains a PITFALLS block and the missing items;
  - its REST polling recipe uses `order=asc` and repeats while truncated;
  - it is shorter.

## Not done

- `ARCHITECTURE.md` (tests batch's file):
  - line 94 exit-code summary: `2 timeout` becomes "2 a timeout the daemon reported; 1 includes a daemon that stopped answering";
  - lines 108-109: "every request policy (request, download, stream_text) routes through it; `probe` does not, since for `mcu daemon` any transport failure means not running" (HEALTH-26).
- `can dump` rows carry no port column: `/can/frames` rows have no `port` field (`store.query_can_frames` selects none). Needs `l.port` in that SELECT (store batch) and the frame row shape (server batch); then `fmt_frame` can take the same `show_port`.
- CLI-18 depends on the server's refusal keeping the `port is ambiguous` prefix; the client keys on it.
- CLI-11 on Windows is unchanged by design (the devnull repair stays for a consoleless interpreter); not driven there.

## Doubts

- WS upgrade 502/504 stays exit 3, against CLI-15's "any HTTP status is 1". The existing `test_follow_ws_auth_and_capacity_refusals_are_exit_1` pins 502 as 3: a gateway saying nothing answered behind it. REST's `Client.fail` maps 502 to 1, so the two paths still differ on a proxy's 502. Owner may want to pick.
- `wait` exits 1 on an ERR send even when a line matched (ruling taken literally); the matched line is still printed.
- The port-column rule is two rules:
  - rows spanning ports, for a finished result;
  - attached ports, for a stream.
  - So `lines` over two boards whose newest N rows all came from one shows no column.
- `fmt_line`'s line-boundary escaping applies to pipes and files too (it is the text format, and the daemon's text export shares it). Only `--json` carries those bytes raw, so "pipes stay faithful" holds for `--json` and for every other byte.
- `send -` refused: there is now no way to send a line that is exactly `-`.
- Not checked:
  - LIFECYCLE-1 on Windows: the fallback signals the recorded launcher shim, and whether that ends the daemon depends on the launcher's job object;
  - the 5-line repair warning still prints on POSIX beside `cannot write output`;
  - PERF-3's client timeout assumes one 30 s budget per retrospective pattern plus a count, as the store batch left it when I read it.
