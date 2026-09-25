# Batch cli

Every CLI change updates `AI_GUIDE` in `cli.py` and the SPEC 4 table in the same commit (CLAUDE.md).
Owner-pick items use the recommended option in `decisions.md` unless the owner answered otherwise.

## Files

- Source: `host/mcuscope/cli.py`, `cli_output.py`, `cli_client.py`, `cli_daemonctl.py`.
- Tests you may edit: `test_cli_read_scope.py`, `test_cli_follow.py`, `test_cli_send_verdicts.py`.
- New tests go in new files (e.g. `test_cli_decode_dropped_points.py`, `test_cli_version_gate_reads.py`, `test_cli_numeric_grammar.py`).
  Do not edit `test_cli.py`, `test_cli_contract.py`, `test_cli_attach.py`, `test_cli_daemon_stop_scope.py`, `test_cli_daemonctl.py`, `test_cli_version_gate.py`, `test_cli_ux.py` (cli-tests) or `test_cli_sessions.py` (firmware-packaging).
- Docs: SPEC 4, SPEC 6, `AI_GUIDE`; the CLI sections of `docs/ARCHITECTURE.md`.

## Findings

### R16-1 MEDIUM: a dropped non-finite point ends `--decode` output
- Checked: `LineDecoder._fields` (cli_output.py:429) walks `sample.points` with `next()` in step with the definition; SPEC 2.5 drops a non-finite point, so `next()` raises `StopIteration`, which becomes `RuntimeError` in the generator and escapes the follow's guard.
- Fix: key the sample's points by name (`dict(sample.points)`); a channel with no point renders `-`.
- Test: `!pd 0 a:f4 b:f4` then `!ps 0 2 3F800000,7F800000`: `LineDecoder().decode` renders `a` and `b -`; `mcu tail -f --decode` over a scripted stream prints the next line after it.

### R16-2 LOW: one malformed poll ends `can dump -f`
- Checked: `_poll_new_frames` (cli.py:2280) calls `_list_field`, whose `die()` is outside both per-poll and per-frame guards.
- Fix: a non-list `frames` raises `ValueError` (a failed poll, counted and retried); non-object entries go to the per-frame guard (a frame drop).
- Test: stub polls answering `[good(5), {"line_id": 4}]` then `[good(7), "junk"]`: frames 5 and 7 print, two drop warnings, the follow keeps polling.

### R16-3 LOW: a malformed last row ends an export walk as complete
- Checked: `_iter_pages_asc` (cli.py:714) continues only when the last row's id is an int.
- Fix: continue from the highest integer id on the page (as `api.js` `oldestId`); a truncated page with no integer id dies with exit 1 naming the page.
- Test: page 1 `[id 1, id "2"]` truncated, page 2 `[id 3, id 4]`: `log export --decode --limit 0 -o F` requests page 2.

### R17-4 LOW, owner-pick D-4: `daemon start` prints the launcher's pid on Windows
- Checked: cli.py:2728-2730 print `proc.pid`; `/status` `pid` (in `body`) is the serving process.
- Fix (D-4 option A): `started mcuscoped (pid <serving>; launcher <spawned>)` when they differ, else `(pid N)`; `--json` `pid` is the serving pid, plus `launcher_pid`.
- Test: stub `/status` `pid` 5678, `ppid` equal to the spawned pid, `sys.platform` win32: the line names 5678.
- SPEC 4 `daemon start`, AI_GUIDE.

### R18-1 LOW: `tail -f --match` deep nesting crashes with RecursionError
- Checked: cli.py:1191 catches `regex.error` only; `regex.compile("("*400 + ")"*400)` raises `RecursionError` (verified).
- Fix: apply the daemon's 200-character cap here (duplicated like `MAX_TIMEOUT_MS`, with the comment saying so) and catch `RecursionError`.
- Test: that pattern exits 1 with `too long`, no crash log.

### R18-2 LOW: a bad `--url` on the WS path is "malformed frame", exit 1
- Checked: `websockets.connect` raises `ValueError` from `urlsplit().port`; the frame clause at cli.py:1311 catches it.
- Fix: validate the ws URL (host and port) before connecting and answer as `die_bad_url` does (exit 3).
- Test: `--url http://127.0.0.1:99999 tail -f -n 0` and `can dump -f -n 0` exit 3 with `bad daemon url`, like `status`.

### R19-2 LOW, owner-pick D-5 (CLI half)
- The follow's local compile (cli.py:1191) uses the same flags constant the daemon adopts (daemon-api defines it in `store.py`; import it, or duplicate it with a comment).
- Test: a follow with `--match '\d'` skips a row `reading ٣`.

### R20-2 MEDIUM, owner-pick D-7 (CLI half)
- `_dump_follow` advances `since` to `next_since_id` when the answer carries it and it is higher, even with no frames; an older daemon without the field keeps today's behaviour.
- Test: a stub answering `frames: []`, `next_since_id: 500`; the next poll sends `since_id=500`.

### R22-2 LOW: numeric options accept other scripts' digits and `_`
- Checked: click INT/FLOAT are `int()`/`float()`; `mcu i2c rd 48 ٣` sends `i2c rd 48 3` (leg-driven).
- Fix: one click `ParamType` per kind with `protocol.int_arg`'s grammar (ASCII digits), used by every numeric option and argument (the 33 in `registry-15-28.md` class 22 sweep G).
- Test: `mcu i2c rd 48 ٣` and `1_0` exit 2 and send nothing; `mcu lines --limit ٣` refused; a plain `10` still works.
- SPEC 4: one line on the numeric grammar.

### R22-3 LOW: `MCUSCOPE_START_TIMEOUT` parsed with `float()` and silently ignored when bad
- Checked: cli_daemonctl.py:149.
- Fix: ASCII decimal grammar; an unparseable value warns naming the variable, then uses 20 s.
- Test: `abc` and `٣` each warn with `MCUSCOPE_START_TIMEOUT` in stderr and give 20.0.

### R25-3 MEDIUM: `tail -f` judges its `[port]` column once
- Checked: cli.py:997 decides `show_port` at start; a board attached later prints bare lines. Contradicts SPEC 4 and the AI_GUIDE pitfall.
- Fix: a row whose port (not `""`) is outside the start set turns the column on for the rest of the follow.
- Test: scripted stream with rows from `sim` then `board2` (not in `/ports` at start): `board2` rows carry `[board2]`, later `sim` rows carry `[sim]`.

### R27-15 LOW (FB2-3): `_port_column` keeps a clause only impossible data reaches
- Checked: `if s.port or s.json_out` (cli.py:882); a `-p a` page never carries port b.
- Fix: drop `s.port or` from the rows branch; the test's handler dispatches on `port` and the mixed-rows half goes.

### R29-3 LOW: two follow refusal arms with no assertion
- Checked: the `websocket error:` arm (cli.py:1317, exit 3) is reached by test_cli_follow.py:161-170 without asserting; the `unexpected row shape` arm (exit 1) is reached by nothing.
- Fix: assert exit 3 and `websocket error:`; add a case that reaches the `KeyError` arm, or delete the arm if nothing can.
- Revert-verify: swapped exit codes fail.

### R44-1 LOW: `--last-ms` anchored on the client's clock
- Checked: `_absolute_window` (cli.py:751) uses `time.time()`; `/status` carries `now` for exactly this.
- Fix: anchor on `/status` `now`; fall back to the local clock for a daemon without it.
- Test: stub `/status` `now` = local + 300 s; the sent `since_ts` is `now - last_ms/1000`.

### R53-1 MEDIUM: `can dump --last-ms` sends `since_ts` ungated
- Checked: `can_dump` gates only `--from/--to` and `--csv` (cli.py:2122); `/can/frames since_ts` is declared from 0.4.0.
- Fix: add `--last-ms` and `--bus` to the gated list when given.
- Test: stub `/status` version `0.3.0`: `can dump --last-ms 1000` exits 1 naming `--last-ms`.

### R53-2 LOW: `id_to` sent ungated by the anchor lookup and decoder priming
- Checked: cli.py:747, :813, :849.
- Fix: gate `--session` with `--last-ms`, and `--decode`, through the same `require_daemon` call.
- Test: stub version `0.1.0`: `lines --decode` exits 1 naming `--decode`.

### R53-3 MEDIUM, owner-pick D-16: an empty scope passes against a 0.4.0 daemon
- Checked: cli.py:1477-1496 gates only `--eol`; a 0.4.0 daemon answers `pass` with `checked_lines 0`. `DAEMON_MIN_VERSION`'s comment (cli_client.py:34) claims every body field is declared, but `allow_empty` is new.
- Fix (D-16 option A): judge it here: `status == "pass"`, `checked_lines == 0` and no `--allow-empty` prints `EMPTY`, exits 1, and `--json` reports `status: "empty"`. Correct the comment.
- Test: stub 0.4.0 answer `{"status": "pass", "checked_lines": 0, ...}` without `--allow-empty`: exit 1, `EMPTY`; with it: exit 0.
- SPEC 4 `mcu assert`, AI_GUIDE.

### R54-1 LOW: CAN filter params built twice
- Checked: `can_dump` (cli.py:2127-2141) and `_dump_follow` (:2182-2190).
- Fix: one helper both call. Existing tests cover both paths.

### R63-2 LOW: failed-send fixture shows an output the daemon cannot produce
- Checked: `_verdict` (test_cli_send_verdicts.py:31-33) gives a failed send `checked_lines: 5`; the daemon returns `verdict(0, ...)` (server.py:3154).
- Fix: use `checked_lines 0`; assert the real output. The real output prints an unjudged forbid as `ok forbid 'PANIC': never seen`: render it as unjudged (`-  forbid 'PANIC': not judged`) when the send failed.
- Test: the failed-send case asserts `not judged` and `FAIL  0 lines checked`.

### R63-3 LOW: truncated positive control with no rows
- Checked: test_cli_read_scope.py:155-166 answers `{"lines": [], "truncated": true}`.
- Fix: the control's page holds at least one row.

### O-56a LOW: `_lines_params`'s `last_ms` argument is dead
- Checked: cli.py:587-600; every caller passes None since `_absolute_window`.
- Fix: delete the argument and its branch.

## Owner rulings 2026-09-25 (override the options above)

- D-16: no backward compatibility before v1.0. Raise `DAEMON_MIN_VERSION` to the current package version so the CLI refuses older daemons; drop per-command version gates this makes dead. This also closes R53-1. `test_cli_version_gate.py` belongs to cli-tests: list the test changes it needs in your report.
