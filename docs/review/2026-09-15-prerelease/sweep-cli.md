# CLI registry sweeps: classes 69, 70, 74 and closed output (9/35)

Scope: `host/mcuscope/cli.py` (AI_GUIDE included), `cli_client.py`, `cli_output.py`, `cli_daemonctl.py`, `cli_argv.py`, `_stdio.py`.
Line numbers are from the pre-fix tree unless marked.
Driver, raw tables and mutation log: `~/tt-data/mcuscope-sweep-cli/` (`driver.py`, `sweep2.txt` pre-fix, `sweep5.txt` post-fix, `mutate.py`, `mutate.txt`).

## Class 69: output targets and removals

Command: `grep -n 'open(out\|os.remove\|unlink' host/mcuscope/cli*.py` returned 5 sites.
The grep misses output opens not spelled `open(out` (`_OutFile`, the daemon `.err` log, the pid `.tmp`, crash reports) and the shared remover, so it was widened to `grep -n 'open(\|os.remove\|unlink\|remove_partial\|rmtree\|replace_atomic\|os.replace' host/mcuscope/cli*.py host/mcuscope/_stdio.py`: 31 lines.

| Site | Verdict |
|---|---|
| cli_client.py:251 `open(out_file, "wb")` in `download` | complies: after `status_code >= 400` (247) |
| cli_client.py:265 `remove_partial(out_file)` | complies: only when `started` (the open succeeded) and not `ok`; lstat regular-file check inside |
| cli_output.py:149, 159 `remove_partial` / `os.remove(real)` | complies: `lstat(realpath)` is `S_ISREG`; a FIFO or device is kept, a symlink keeps the link |
| cli.py:1588 `_OutFile.write` opens on first write | complies: first chunk or row exists only after `stream_text`/`json_or_die` checked the status; `close()` opens an empty file only after a complete answer |
| cli.py:1602 `_OutFile.discard` -> `remove_partial` | complies: returns when nothing was opened |
| cli_daemonctl.py:138 `os.remove(pid_path)` | exempt: pid record, not a user target; removed only while it still names our pid (class 7) |
| cli_daemonctl.py:162, 164, 167 pid `.tmp` open, `replace_atomic`, remove | exempt: a private pid-suffixed temp name this command creates in the data dir |
| cli_daemonctl.py:155, 157 | exempt: import and comment |
| cli.py:2505 `os.remove(pid_path)` | exempt: class 7, a record provably stale (pid not running and no /status) |
| cli.py:2357 `open(err_path, "wb")` | complies: the command's own per-start log (SPEC 4 "truncated per start"), opened after the "already running" refusal |
| cli_daemonctl.py:62 | exempt: read-only open |
| _stdio.py:147, 155, 167 | exempt: stream repair (CONOUT$, devnull, CONIN$) |
| _stdio.py:326 | exempt: the app's own crash/startup report, no peer |
| _stdio.py:362, cli_output.py:145 `os.open(os.devnull)` | exempt: devnull repoint |
| cli_client.py:17, 124, 140, 160, 244, 280; cli.py:68, 2047 | exempt: an import or the httpx `Client.open()` method, not a file |
| cli.py:2376 `Popen`, 2426 `webbrowser.open` | exempt: not a file open |

Violations: none.

## Class 70 client side: status, close-code and exception branches

Command: `grep -n "status_code ==\|status ===\|\.code ==" host/mcuscope/cli*.py` returned 4 sites.
It misses `>= 400`, `400 <= x < 500`, `raise_for_status`, `status in (...)`, the body-prefix keys and every exception-type branch, so it was widened to `grep -n "status_code\|\.code\b\|rcvd\|except \|raise_for_status\|status in\|startswith(SHUTDOWN\|startswith(\"port is"` over the CLI files and `_stdio.py`: 92 lines (81 in `cli*.py`, 11 in `_stdio.py`).

Server emitters read from `server.py`: 503 at 1928/1937 (`_SHUTDOWN_MSG`) and 2301/2579 (`StoreError`: `daemon is shutting down; no new watch can start` or `too many subscribers`); no explicit 404; 400/422/409/500 from handlers; 401/403/429 from the guards.
On `/ws`: 1008 at 634 (Host/Origin), 772 (token), 1966 (no such port, with reason); 1013 at 790 (token lockout) and 1975 (subscriber cap); 1001 at 1973.
Driven (`probe_ws.py`): a close sent before `accept()` reaches the client as HTTP 403 `InvalidStatus`, not as a close code, so 634/772/790 all arrive as 403 and only 1966 (1008) and 1975 (1013) arrive as closes.

| Lines | Branch | Verdict |
|---|---|---|
| cli_client 193 | 503 with `daemon is shutting down` prefix -> 3 | complies: both shutdown messages share the prefix; the cap's 503 stays 1 |
| cli_client 199 | 404 -> "daemon X does not serve" only when the daemon is older than 0.4.0 | violates (narrow): a path segment with `/` also 404s; `mcu detach a/b` on an old daemon blamed the version. Found beside it: `detach` put the alias in the path unquoted, so `board?x` or `board#1` detached `board`. Fixed (F5) |
| cli_client 205 | `port is ambiguous` body prefix | complies: keyed on the body |
| cli_client 215, 247, 283 | `>= 400` -> fail, exit 1 | complies: SPEC 4 "HTTP error" is 1 for every such code |
| cli_client 57, 219 | JSON decode -> raw text / "malformed response" 1 | complies |
| cli_client 98 | ConnectError/ConnectTimeout -> 3 | complies |
| cli_client 100 | TimeoutException -> 2 (1 on assert) | owner should pick (O1) |
| cli_client 102, 106 | InvalidURL -> 3; other HTTPError -> 3 | complies: transport failures; the CLI never raises HTTPStatusError on this path |
| cli_client 108 | ValueError -> 1 | complies |
| cli_client 164 | probe: any failure -> None ("not running") | owner should pick (O2) for the daemon subcommands |
| cli_client 258, 288, 290 | download/stream OSError -> "cannot write" 1; BrokenPipe re-raised | complies: httpx maps socket errors to its own types |
| cli.py 1114, 1119 | ConnectionClosed 1008 -> 1, reason or "not authorised" | complies: only the port refusal (with reason) arrives as a close; the fallback wording is unreachable against uvicorn |
| cli.py 1120 | 1013 -> 1 "too many subscribers" | complies: the lockout's 1013 arrives as HTTP 403, so a close 1013 is only the cap |
| cli.py 1125 (other closes) | -> 3 "stream closed by daemon" | violates: websockets' 1 MiB default `max_size` closed a healthy follow (1009) on a frame of up to 500 rows x 4 KB. Fixed (F4) |
| cli.py 1126, 1127, 1129 | InvalidStatus 401/403 -> 1, else 3 | complies: the handshake refusals are all 403 |
| cli.py 1106 | OSError -> 3 "daemon unreachable" | violates: an OSError from stdout in the snapshot thread (`tail -f -n 1500 > /dev/full`) exited 3. Fixed (F6) |
| cli.py 1104 | BrokenPipe re-raised | complies |
| cli.py 1130 | WebSocketException -> 3 | complies: handshake/URI failures |
| cli.py 1038, 1070, 1132, 1136 | frame JSON / row shape -> skip or 1 | complies |
| cli.py 2053 | 4xx in the can follow poll -> 1 at once | complies: 400/401/403/422/429 come from a running daemon |
| cli.py 2055, 1990 | 5xx and malformed body retried, give-up -> 3 "unreachable" | violates: a daemon answering 500 for 30 s is running. Fixed (F3) |
| cli.py 2051 | InvalidURL -> 3 | complies |
| cli.py 2022 | per-frame shape -> skip | complies |
| cli.py 2898-2939 dispatcher | Exit, usage, Abort, OSError (BrokenPipe -> 0 else crash), KeyboardInterrupt 1, KeyError/IndexError 1, SystemExit | OSError arm violates (class 9): a stdout ENOSPC crash-logged. Fixed (F2). Usage `show()` guarded only a closed pipe: a full stderr crashed. Fixed (F2). Others comply; KeyError/IndexError is exempt by the class 18 ruling |
| cli.py 2843, 2846 main flush | BrokenPipe -> 0, OSError -> 1, over the code | violates (class 35): `assert` FAIL with stdout closed exited 0; `daemon status` with no daemon exited 0 (closed) or 1 (full) over 3. Fixed (F1) |
| cli_output 55 | err_write guards BrokenPipe only | violates (class 35): a full stderr turned `mcu status` (3) into 120 plus a crash log. Fixed (F2) |
| cli_output 200, 202, 215, 220 | out_json / emit_stream | complies |
| cli_output 272, 369, 448, 460 | parse_clock, fmt_num, confirm, isatty | complies |
| cli.py 908, 989, 998, 1020, 1092, 1101, 1143, 1634, 1659, 1661, 1757, 2028, 2358, 2386, 2910, 2912, 2925, 2927, 2938, 2939 | regex timeout, task cleanup, bad pattern, Ctrl-C, stdout export pipe, file write errors, daemon log/pid warnings | complies |
| cli_daemonctl 27, 44, 64, 165, 194, 245 | bad url 3, pid path 1, stderr tail, tmp cleanup, wait, stop failure 1 | complies |
| cli_argv 74 | resolver failure -> no hoisting | exempt: class 5 |
| _stdio 151, 168, 199, 228, 234, 264, 294, 328, 363, 390 | stream repair, reconfigure, EINVAL translation, crash-dir fallback, report write, crash backstop | complies |
| _stdio 360 `_note` | guards BrokenPipe only | violates (class 35): a crash notice into a full stderr exited 120. Fixed (F2) |
| cli_argv 33, cli_output 98, cli.py 1112, 2877 | docstring or comment | exempt |

## Class 74 CLI side: displayed limits

Command: `grep -n "max\|cap\|limit" host/mcuscope/cli.py` returned 107 lines.
It is case-sensitive and cli.py only, so it misses `MAX_TIMEOUT_MS`, `FOLLOW_*_S` and the other modules; widened with `-i` over every owned file plus `grep -n '{[A-Z_]\+[}:]\|:g}s\|[0-9] to [0-9]\|[0-9]\.\.[0-9]'`: 20 more sites.

Displayed limits (18 of the 107):

| Lines | Figure shown | Enforced against (SPEC) | Verdict |
|---|---|---|---|
| 484 | `--ms` 1..2000 (range error, help) | `ms`, SPEC 3.4 `/break` 1..2000 | complies |
| 666 | `--last-ms` max 10^15 | `last_ms`, SPEC 4, server `MAX_MS` | complies |
| 1238 | `--min-window` max 300000 | `min_window_ms`, SPEC 3.4 | complies |
| 1809, 1867 | `--bus` CAN_BUS_MIN..MAX | `bus` 1..9, SPEC 3.4 | complies |
| 835, 1777, 1778 | "truncated at N rows" | rows returned, the figure `/lines` caps | complies |
| 1678 | `--limit` 0 means every row | not a cap | complies |
| 1884 | "no -n limit and no row cap" | SPEC 3.4 csv ignores `limit` | complies |
| 1123, 2612, 2622 | subscriber cap, no figure | subscriber count | complies |
| 2642, 2643, 2753 | AI_GUIDE "1000-row answers", `limit=1000` | SPEC 3.4 `/lines` 0..1000 | complies |
| 2646 | "not with --limit" | a refusal | complies |
| 542 | comment "the /lines cap (SPEC 4)" | SPEC 3.4 | violates (nit): wrong section. Fixed |

Exempt, not a displayed limit (89): parameter plumbing, `max()` arithmetic, comments, and `capture`/`captured` matching `cap`: 93, 148, 158, 188, 424, 548, 552, 574, 575, 579, 584, 586, 591, 607, 610, 651, 659, 710, 713, 715, 716, 806, 818, 823, 825, 944, 946, 1053, 1110, 1121, 1122, 1234, 1248, 1255, 1319, 1329, 1355, 1359, 1384, 1388, 1390, 1430, 1451, 1456, 1496, 1500, 1502, 1513, 1552, 1677, 1694, 1703, 1706, 1717, 1721, 1728, 1732, 1733, 1737, 1745, 1882, 1921, 1927, 1939, 1940, 1945, 1954, 1964, 1971, 2008, 2009, 2012, 2015, 2016, 2020, 2166, 2175, 2260, 2266, 2331, 2396, 2446, 2620, 2682, 2683, 2693, 2704, 2765, 2876.

Widened sites:

- cli.py 428 `expected 1 to 300000 ms`: SPEC 3.4 `timeout_ms` cap. complies.
- cli.py 909 `over 0.25s on one line`: `pat.search(timeout=0.25)` per row. complies.
- cli.py 1827 `--rtr` 0 to 8: SPEC 2.4 DLC. complies.
- cli.py 2001 `for 30s`: measured from the first failed poll, a lower bound. complies.
- cli.py 2629, 2639 AI_GUIDE `1..2000 ms`, `0 to 10^15`: as above. complies.
- cli_client 188, 204 `needs daemon 0.4.0`: SPEC 4. complies.
- cli_daemonctl 201, 204 `within {wait_s}s`: the value the deadline used. complies.
- cli_daemonctl 250 `within 10s`: `_wait_pid_gone(..., DAEMON_STOP_GRACE_S)`. complies.
- cli_daemonctl 83 env timeout floored at 0.5: the message shows the floored value. complies.
- cli_output 401, 412, 413, 420, 421 note_truncated: complies.
- cli_output 234, cli_client 197, cli_argv 76, 108, 109: exempt (arithmetic, comments).

## Closed output (classes 9/35 shape)

Every command path, driven through `console_entry` in a child against an in-process stack (`Stack(["--plot"])` plus 300 markers so whole-capture streams overflow a pipe buffer).
182 cases (91 argv x text/`--json`, plus the crash control): every command, `-o FILE`, `-o -`, bad `-o` dir, `tail -f`, `can dump -f` (text and JSON), prompts (`purge`, `session delete --data` with EOF, with `y`, and the `--json` refusal), and daemon control (`status`, `stop`, `start` already-running, and `start`/`stop`/`restart`/abandon through patched `Popen` and `_status_body`; no real daemon spawned).
7 modes per case: attached, stdout closed pipe, stderr closed pipe, stdout None, stderr None, stdout `/dev/full`, stderr `/dev/full` (the last two widened from "closed" because a full stream is the same write-failure primitive): 1274 cells.
Checked per cell: exit code against the attached run, no `Traceback`/`Exception ignored`, no crash log in the bound data dir, nothing written to the child's cwd.
The crash dir is `platformdirs.user_data_dir` patched in the child to a path bound before `sys.argv` is replaced; positive control: a raising `main()` lands `mcu-crash.log` in that dir in all 7 modes.

Pre-fix deviations (`sweep2.txt`, and `sweep1.txt` for the first 37 cases):

- `assert` FAIL and `assert --forbid .` hit, stdout closed: exit 0 (attached 1).
- `daemon status` with no daemon, stdout closed: exit 0; stdout full: 1 (attached 3).
- `lines --limit 1500`, `tail -n 1500`, `log export --decode` (text and JSON), `ai-guide`, stdout full: crash log and traceback; `--help`: exit 120 with a crash log.
- `tail -f -n 1500`, stdout full: exit 3 "daemon unreachable".
- `--json tail -f` (3 cases) and `--json can dump -f`, stdout closed or full: never ended (the backfill's `out_json` swallowed the failure and the follow wrote into devnull).
- stderr full (driven separately before the mode was added): `mcu status` exit 120 plus a crash log.

Post-fix (`sweep5.txt`, rerun for the follow cases after the last code change): every cell equals the attached run, except by design: a closed stdout ends a follow with 0; a full stdout turns 0 into 1 wherever stdout was written (`can tx`, `can filter`, `i2c wr`, `gpio set` print nothing and stay 0).
No crash log, traceback or cwd file in any cell but the control.
No stray crash log, pid, `.err`, `-` or `-.zip` under `host/` or the repo root (checked with `find -newer` a marker set before the first run).

## Fixes

- F1 `cli.main` final flush: a closed pipe keeps the command's code (was 0); a failed flush returns `code or 1` (was 1) and reports `cannot write output`.
- F2 A write failure on either stream no longer crashes or owns the code.
  - `cli_output._GuardedStdout`, installed by `main()`, records a stdout write or flush failure (`_stdout_unwritable`, now reporting once) and re-raises it; the dispatcher's OSError arm returns 1 when `output_failed()`, else re-raises as before.
  - `err_write`, the usage-error `show()` and `_stdio._note` guard any OSError, not only a closed pipe.
- F3 `_dump_follow` give-up: exit 3 only when the last failure is an `httpx.TransportError`; otherwise exit 1 `the daemon at URL kept failing for 30s`.
- F4 `_follow_ws` connects with `max_size=None`.
- F5 `detach` quotes the alias (`safe=""`) and refuses one containing `/` before any request.
- F6 `_tail_snapshot` and the `can dump` backfill print through `emit_stream`, so a closed or full stdout ends a JSON follow.
- F7 LINES_PAGE comment cites SPEC 3.4.

Docs: AI_GUIDE (exit codes, `can dump -f` give-up, detach), SPEC 4 (two sentences after the follow exit rule, detach in the ports row), CHANGELOG Fixed.

## Tests

`host/tests/test_sweep_cli_closed_output.py`, 20 tests; the `/dev/full` ones skip where it does not exist.

## Revert verification

Each changed branch mutated back from a copy, its tests run, the file restored from the copy and compared byte for byte (`mutate.txt`).

| Mutation | Tests | Result |
|---|---|---|
| M1 main flush closed pipe `return 0` | closed_still_exits_1, closed_is_still_3 | 2 failed |
| M2 main flush full `return 1` | into_a_full_disk_is_3 | 1 failed |
| M3 main flush full `return code` | final_flush_as_1 | 1 failed |
| M4 dispatcher `output_failed()` arm removed | without_a_crash_log | 4 failed |
| M5 guard write records nothing | without_a_crash_log | 2 failed |
| M6 guard flush records nothing | without_a_crash_log | 2 failed |
| M7 guard not installed | without_a_crash_log | 4 failed |
| M8 report-once removed | json_error_object | 1 failed (first run survived; count assertion added, rerun) |
| M9 err_write BrokenPipe only | full_stderr_keeps | 1 failed |
| M10 `_note` BrokenPipe only | crash_notice | 1 failed |
| M11 usage `show()` BrokenPipe only | full_stderr_keeps and usage | 1 failed |
| M12 tail snapshot back to out_json/print | json_tail_follow, follow_snapshot | 2 failed |
| M13 can dump backfill back to out_json/print | json_can_dump_follow | 1 failed |
| M14 give-up always 3 | answering_500 | 1 failed |
| M15 detach `/` refusal removed | refuses_a_slash | 1 failed |
| M16 detach alias unquoted | quotes_the_alias | 1 failed |
| M17 default `max_size` | over_one_mib | 1 failed |

All restored byte-identical. F7 is a comment. A `_follow_ws` `output_failed()` re-raise added mid-round was unreachable once F6 landed and was deleted, not tested.

Gates: `test_sweep_cli_closed_output.py` 20 passed; `test_cli_contract.py` passed; `ruff check .` clean.
Also run: the 14 CLI-adjacent test files (663 tests).

## Existing tests broken

- `tests/test_regressions.py::test_only_the_documented_commands_emit_jsonl` finds per-row emitters as `out_json` called in a loop; F6 moved `_tail_snapshot` and `can_dump` to `emit_stream(json.dumps(...))`, so it now finds none.
  - Suggested edit: count an `emit_stream` call whose argument is `json.dumps(...)` as an emitter too.
  - That also finds `_dump_follow` and `_follow_ws`'s `handle`, which belong to `can dump` and `tail`, already in SPEC's list; the expected set needs those names.
- `test_cli_export.py::test_the_window_list_is_every_command_taking_from` and `test_cli_r2026_09_12.py::test_the_export_and_bounded_lists_are_every_command_taking_the_option` failed once in a combined run while other agents were editing those files. Both passed on rerun, alone and in the combined set: not caused by this batch.

## Owner should pick

- O1 `mcu wait` maps a transport read timeout (a wedged daemon, no answer within `timeout + 5 s`) to exit 2, the code for "nothing matched". `assert` was already moved to 1.
  - (a) Keep: SPEC 4 says 2 is a timeout.
  - (b) Exit 1 as `assert` does, so 2 always means a clean negative.
  - (c) Exit 3.
- O2 The `mcu daemon` subcommands read any non-status body as "not running".
  - Affected: a running daemon refusing with 403 (a `--url` hostname it does not bind), or 401/429 for a remote client.
  - `daemon status` then exits 3 and `daemon start` spawns a second daemon that dies on the port.
  - (a) Keep (SPEC 4: absent is 3).
  - (b) On the daemon's `{"error"}` envelope with 401/403/429, exit 1 naming the refusal, and `start` refuses rather than spawns.
- O3 `mcu can dump` ignores `/can/frames`' `truncated`.
  - `-n 5000` silently shows 1000.
  - The `-f` poll (`limit=1000`, newest first) drops the older frames when more than 1000 arrive between polls, which a failed-poll episode makes likely.
  - (a) A stderr note as `lines` gives.
  - (b) Page like `lines`; ascending polls need `order=asc`, which an older daemon drops silently (class 53), so it needs a version gate.
  - (c) Both.

## For other owners (not in my files)

- SPEC 3.1 (line 359) says a token lockout is "WS close 1013", and 3.4 (line 881) says Host/token failures are "close 1008". A close before `accept()` reaches every client as an HTTP 403 handshake refusal. The CLI handles both; the SPEC text is inaccurate.
- `pidfile.read_pid_record` opens the record with a blocking read, so a FIFO at the pid path hangs `mcu daemon stop` (class 7 area).

## The two questions

1. Least confident: the full-disk half on Windows.
   - `_GuardedStdout` wraps `_PipeErrorStream` there, and every `/dev/full` test skips on Windows, so ENOSPC through both wrappers has never run on it. The closed-pipe tests do run there.
   - F4 rests on a fake server: the real daemon reached only 357 KB per frame under a marker flood (`probe_frame3.py`). Over 1 MiB is reasoned from `WS_BATCH_MAX` x `RX_SAFETY_CAP`, not driven.
   - Rechecked by driving: the guard under rich help rendering (`--help`, all 7 modes) and the follow cases after the last code change.
2. Not thought about before this round:
   - A full stream as well as a closed one. It found the stderr 120 crash, which the closed-pipe-only guard had hidden.
   - The refusal-reads-as-absent shape in `probe()` (O2).
   - The `truncated` flag `can dump` never reads (O3).
   - What a closed-before-accept WebSocket actually looks like on the wire, which settled that the 1013 mapping is right.
