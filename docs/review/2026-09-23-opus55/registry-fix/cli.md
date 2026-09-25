# Fix batch cli (registry leg, 2026-09-25)

HEAD at start: `3f10153c7558104570a90817148b91e1ffb6f5db`.
Every finding is fixed; D-16 follows the owner's pick B (a version header), relayed by the coordinator, which also handed this batch `host/tests/support.py`.

## Findings

"Fails" names the test that fails with the fix reverted or mutated in a private copy (`~/tt-data/mcuscope-2026-09-25/fix-cli/mutate.py`, 37 mutants, results in `mutants2.txt` there).

- R16-1 fixed: `LineDecoder._fields` keys points by name; a missing point renders `-`.
  - Fails: `test_cli_decode_dropped_points.py::test_a_dropped_point_renders_as_a_dash` (the `points[...]` mutant and the one-per-channel walk).
- R16-2 fixed: a non-list `frames` is a failed poll (counted, retried); non-object entries fall to the per-frame guard.
  - Fails: `test_cli_follow.py::test_a_malformed_poll_or_frame_costs_that_item_not_the_follow`.
- R16-3 fixed: `_highest_id` continues an ascending walk from the highest integer id; a truncated page with none is exit 1 naming the page.
  - `lines --since-id` (`_fetch_after`) had the same last-row rule and uses the same helper.
  - Fails: `test_cli_read_scope.py::test_an_export_walk_continues_from_the_highest_integer_id`, `::test_a_truncated_page_with_no_row_id_ends_the_export_as_a_failure`, `::test_since_id_walk_continues_from_the_highest_integer_id`.
- R17-4 fixed (D-4 A): `started mcuscoped (pid <serving>; launcher <spawned>)` when they differ; `--json` `pid` (serving) plus `launcher_pid`.
  - Fails: `test_cli_daemon_start_pid.py` (serving-pid and `launcher_pid` mutants).
- R18-1 fixed: the follow applies the daemon's 200-character cap and catches `RecursionError`.
  - Fails: `test_cli_follow.py::test_follow_match_over_the_daemons_length_cap_is_refused`, `::test_follow_match_nested_too_deeply_is_refused_not_a_crash`.
- R18-2 fixed: `_follow_ws` validates host and port first and answers through `die_bad_url` (exit 3).
  - Fails: `test_cli_follow.py::test_a_bad_url_on_the_websocket_path_is_exit_3`.
- R19-2 fixed (D-5, CLI half): the follow compiles with `regex.ASCII`, duplicated with a comment.
  - Fails: `test_cli_follow.py::test_follow_match_digit_class_is_ascii_as_in_the_daemon`.
- R20-2 fixed (D-7, CLI half): each poll moves `since` to `max(since, next_since_id)`, with or without frames.
  - Fails: `test_cli_follow.py::test_the_follow_advances_on_next_since_id_with_no_frames` (no advance; advancing backwards).
- R22-2 fixed: `cli_output.AsciiNumbersGroup` (the root group) swaps all 33 numeric click types for ASCII-grammar subclasses that keep their ranges.
  - Fails: `test_cli_numeric_grammar.py` under four mutants (group not applied, int grammar, float grammar, range dropped).
- R22-3 fixed: `decimal_float` grammar; an unreadable value warns naming `MCUSCOPE_START_TIMEOUT` and 20 s applies.
  - Fails: `test_cli_numeric_grammar.py::test_an_unreadable_start_timeout_warns_and_uses_the_default`.
- R25-3 fixed: `tail -f` starts from the boards attached or stored and adds each printed row's port; the column is on once two boards are in the set (port `""` not counted).
  - Fails: `test_cli_follow.py::test_a_board_attached_during_a_follow_turns_the_port_column_on`, `::test_the_daemons_own_rows_do_not_turn_the_port_column_on`, `::test_a_follow_under_p_prints_no_port_column`.
- R27-15 fixed: `s.port or` dropped; the handler scopes by `port`, and the mixed-rows half is gone. Restoring the clause fails nothing, since nothing reaches it.
- R29-3 fixed: the frame-cap test asserts exit 3 and `websocket error:`.
  - The KeyError arm is reachable (a snapshot row missing a key) and is now tested.
  - Fails: each arm's exit code swapped.
- R44-1 fixed: `--last-ms` anchors on `/status` `now`; the local clock is used only when `/status` carries no number.
  - Fails: `test_cli_read_scope.py::test_last_ms_is_anchored_on_the_daemons_now`.
- R53-1, R53-2 fixed by R53-3's mechanism: an older daemon is refused before any option can be dropped.
- R53-3 fixed (D-16, owner pick B):
  - `DAEMON_MIN_VERSION = __version__`.
  - `Client.open()` installs an httpx response hook (`check_daemon_version`), and the follow checks `ws.response.headers` after the handshake.
  - A missing header is exit 1 with `URL is not an mcuscope daemon (no version header), or is one older than Y`. An older version is exit 1 with `daemon at URL is mcuscope X, this mcu needs >= Y`.
  - A version `is_newer` cannot order (a dev build) is let through.
  - `probe` clears the hook, so `mcu daemon status/stop/restart` still reach an older daemon to replace it.
  - Deleted as dead: `require_daemon`, `older_daemon`, the gate lists, `_clock_bounds`' `gated` argument and the 404 version message.
  - Also deleted as dead:
    - the ambiguous-port `/ports` fallback (the daemon always lists the aliases);
    - `log export`'s client-side rendering of a pre-0.5.0 daemon's multi-board text (its comment said "drop this with that daemon's support").
  - Fails, all in `test_cli_client_version.py`:
    - `test_a_rest_answer_from_an_older_or_foreign_server_is_exit_1`: hook not installed, missing-header branch, older branch.
    - `test_a_current_newer_or_unorderable_daemon_is_let_through`: version compared by equality.
    - `test_daemon_status_still_answers_for_an_older_daemon`: probe exemption removed.
    - `test_a_follow_handshake_without_a_current_version_is_exit_1`: WS check removed.
    - Also covers the `--json` form, a download refused before its file exists, and a CAN poll refused rather than retried.
- R54-1 fixed: `_can_params` builds the filters for `can dump` and its follow.
  - The batch file said existing tests cover both paths; dropping `bus` or `id` failed none of them.
  - Added `test_cli_follow.py::test_can_dump_and_its_follow_send_every_filter`, which fails on dropping `bus`, `id` or `session`.
- R63-2 fixed: the fixture's failed send has `checked_lines 0`; a forbid under a failed send prints `-       forbid 'PANIC': not judged`.
  - Fails: `test_cli_send_verdicts.py::test_assert_names_a_send_that_failed`.
- R63-3 fixed: the positive control's truncated page holds a row (test-only).
- O-56a fixed: `_lines_params` lost its dead `last_ms` argument.

## Contradictions found

- R22-2's test asked for exit 2 on a refused number. SPEC 4 makes bad usage exit 1, so the tests assert 1.
- R18-2 said `status` answers port 99999 with `bad daemon url`. It says `daemon unreachable` (httpx accepts the port), still exit 3.
- R53-3 option A (judge an empty `pass` in the CLI) was superseded by D-16.

## CHANGELOG lines

- **Upgrade:** `mcu` refuses a daemon older than itself (exit 1, naming both versions), or a server sending no `X-Mcuscope-Version`.
  - `mcu daemon status/stop/restart` work against any version.
  - Restart the daemon after upgrading.
- **Upgrade:** numeric options and arguments take ASCII decimal only. Other scripts' digits, `_`, `+`, padding, `nan` and `inf` are usage errors (exit 1); `mcu i2c rd 48 ٣` used to send `i2c rd 48 3`.
- **Upgrade:** `mcu daemon start` names the serving process: `started mcuscoped (pid N; launcher M)` under a Windows venv launcher; `--json` `pid` (serving) and `launcher_pid`.
- **Upgrade:** `mcu tail -f --match` uses the daemon's ASCII classes and its 200-character limit.
- `mcu tail -f` shows `[port]` from the first row of a board attached during the follow.
- `--last-ms` counts back from the daemon's clock.
- `mcu can dump -f` on a quiet port reads only what is new on each poll.
- `mcu assert --send` whose send failed prints its `--forbid` patterns as `not judged`.
- A dropped non-finite point renders as `-` under `--decode` instead of ending `tail -f --decode`.
- A malformed `can dump -f` poll or frame is skipped and counted instead of ending the follow.
- An export or `--since-id` walk continues past a malformed last row; a truncated page with no row id is exit 1.
- A bad `--url` on `tail -f` is exit 3 (`bad daemon url`).
- `MCUSCOPE_START_TIMEOUT` that is not a number warns and uses 20 s.

## Needs another batch

`tests/support.py` now has `VERSION_HEADERS`, `versioned(handler)` and `ScriptedWS` (a `/ws` double with a current handshake). `canned`, `recorder`, `record_params` and `record_requests` keep the real `open`, so the version check runs under them.

- daemon-api:
  - Send `X-Mcuscope-Version` on every response, including the guard refusals (401/403/429), 404s and error envelopes, and on the `/ws` handshake. A refusal without it reads "not an mcuscope daemon" instead of the daemon's message.
  - Define the header in SPEC 3.4.
  - `/can/frames` `next_since_id` (R20-2).
  - If `store.py`'s match-flags constant is not exactly `regex.ASCII`, change `cli.py`'s `regex.compile(match, flags=regex.ASCII)` to match.
  - Until the header lands, every test against a real daemon (`Stack`) that runs a CLI command fails.
- cli-tests, `test_cli.py`:
  - Delete `class _ScriptedWS` (line 1991) and add `from tests.support import ScriptedWS as _ScriptedWS`, which fixes the 4 WS failures there and the 7 in files importing it.
  - At lines 2125, 2157, 2193, 2266 and 2826, wrap the handler: `Client(s, transport=httpx.MockTransport(versioned(handler)))` (import `versioned` from `tests.support`).
- cli-tests, `test_cli_daemon_stop_scope.py:314`: expect `started mcuscoped (pid 4242; launcher 999997)`.
- cli-tests, `test_cli_version_gate.py`: `require_daemon` is gone and `test_cli_client_version.py` covers the check.
  - Delete the file, keeping `test_inverted_bounds_are_still_refused_before_the_version_check` (rename it "...before any request") if a home is wanted.
  - `test_a_query_without_clock_bounds_makes_no_extra_request` fails because `lines --last-ms` now asks `/status` for `now` (R44-1).
- Unowned `test_cli_follow_frames.py`:
  - Lines 105 and 149: wrap handlers in `versioned(...)`.
  - The two `_ScriptedWS` cases are fixed by the `test_cli.py` change.
- Unowned `test_cli_output_rows.py`, `test_cli_decode_rejected_defs.py`: fixed by the `test_cli.py` change.
- Unowned `test_cli_small_refusals.py::test_an_ambiguous_port_names_the_aliases_once_and_the_option`: drop the second (older-daemon) pair from the loop.
- Unowned `test_port_health.py::test_last_ms_is_fixed_before_paging`:
  - In `_canned_lines`' handler, answer `/status` with `{"now": time.time()}` before popping an answer.
  - Keep the `seen` assertions on `/lines` requests only.
- Unowned `test_status_ppid_serial.py:79`: `shim_pid = int(out.split("; launcher ")[1].split(")")[0])`, and assert `f"(pid {status['pid']}; launcher {shim_pid})" in out`.
- Not caused by this batch (my diff has no JS or daemon code):
  - `test_plotjuggler.py` (4, a `target` key);
  - `test_e2e.py::test_empty_cmd_is_client_error_not_500` (tab no longer blank);
  - `test_webui_js.py` (2).

## Needs Windows

- R17-4 on a real venv launcher: `mcu daemon start` prints `(pid <serving>; launcher <spawned>)`, and `taskkill /pid <serving>` stops the daemon.

## Needs a browser

- None.

## REVIEW.md questions

1. Least confident:
   - The header check has not run against a real daemon: daemon-api's header had not landed.
     - What was driven: the hook through httpx's real client on `MockTransport`, and the WS check through a real `websockets` server (the over-1-MiB test, given the header via `process_response`, and failing without it).
     - Still open: whether guard refusals and error responses carry the header. That decides between the guard's message and "not an mcuscope daemon". Recheck with `Stack` once daemon-api lands.
   - The check reads the first answer, so a write command (`send`, `cmd`) to an older daemon has already reached it when it is refused. SPEC 4 says so. This is the price of pick B over a pre-flight `/status`.
2. Not yet thought about:
   - A reverse proxy that strips unknown headers now makes every command exit 1. Nothing tests a proxied daemon.
   - The web UI has no equivalent check (webui batches).
   - Compatibility branches for older daemons remain on paths the check now covers:
     - `cli.py:1240` (a bare-object `/ws` frame);
     - `cli.py:1442` (no `sends` count);
     - `cli.py:187` (no `update` block);
     - `cli_output.py:140`.
   - The ones in `cli_daemonctl.py` and `daemon restart` (`cli.py:2810`) must stay: those paths are exempt from the check.
