# Review 2026-09-23: cli (the `mcu` CLI as an AI agent's interface)

Driven against `mcuscoped --sim` on 18660/18662/18663 plus `mcu-sim --tcp-port 18661` as a second port, throwaway configs and `MCUSCOPE_*_DIR` in `~/tt-data/mcuscope-2026-09-23/cli/`.
Probe runner: `~/tt-data/mcuscope-2026-09-23/cli/run.py` (prints rc, stdout, stderr, JSON validity per invocation).
Tree at 6e4f6f7.

## CLI-1 HIGH CONFIRMED: `wait --send` / `assert --send` match their own outgoing line

- Where: server.py:2406-2420 (`/wait` loop), server.py:2664+ (`/assert` live loop); guide cli.py:2701.
- Failure: the send is stored as a `tx`/`cmd` row inside the watched window, so a `--match`/`--expect` that also matches the command text matches the command itself, instantly.
- Repro:
  - `mcu -p sim --json wait --send "can tx 7FF AABB" --match "7FF AABB"` -> `status: match`, `line.dir: "tx"`, `raw: ">55 can tx 7FF AABB"`, exit 0 in 12 ms.
  - `mcu -p sim assert --send "selftest run" --expect selftest --timeout 2000` -> `ok expect 'selftest': >56 selftest run`, `PASS`, exit 0, although the monitor answered `ERR 1 badcmd unknown selftest`.
  - `--raw` too: `wait --send "BOOTMSG hello" --raw --match BOOTMSG` matches its own tx row.
- An agent writing the natural `--send "gpio set relay 1" --match relay` gets exit 0 for nothing. The guide's own example dodges it only because it waits for `301` after sending `300`, and never says why.
- Fix: exclude this call's own tx row from the match by default (the daemon knows its id), or exclude `dir=tx` unless `--chan cmd` is asked for; state it in the guide either way.

## CLI-2 HIGH CONFIRMED: a rejected `--send` is invisible; `assert` passes on it

- Where: server.py:2655-2662 (`/assert` awaits `send_command` and discards its result; the response has no `cmd_result`); cli.py:1241-1253 (`wait` text mode never looks at `cmd_result`).
- Failure:
  - `mcu -p sim assert --send reset --forbid PANIC --timeout 2000` -> `PASS`, exit 0. `reset` is `ERR 1 badcmd` on the sim; any typo or wrong argument does the same.
  - `mcu -p sim wait --send "i2c rd 99 2" --match X --timeout 1500` -> `timeout: no line matched 'X' in 1501 ms (sent 1)`, exit 2. The `ERR 2 badarg` is only in `--json`'s `cmd_result`; text mode never mentions it, and exit 2 reads as "the effect did not happen in time", so the agent retries with a longer timeout.
- The guide's headline live pattern is `assert --send reset --expect ... --forbid ...`; a forbid-only variant is a test that cannot fail when the stimulus never happened.
- Fix: `/assert` returns `cmd_result` and fails (or refuses, exit 1) when the send answered `err`/`timeout`; `wait` text mode prints the ERR to stderr and exits 1 when `cmd_result.status` is `err`, since the wait's premise is gone.

## CLI-3 HIGH CONFIRMED: a verdict over zero lines passes every `--forbid`; a typo'd `-p` scopes to nothing

- Where: server.py:2583-2584 (`verdict` passes with `checked_lines: 0`), server.py:2612 (retrospective `port` is a bare filter, never resolved); cli.py:1338-1351 prints `PASS 0 lines checked` with no warning.
- Failure:
  - `mcu -p nosuch --json assert --forbid PANIC --session run-3` -> `{"status": "pass", "checked_lines": 0}`, exit 0.
  - `mcu -p sim assert --forbid PANIC --last-ms 1` -> pass, 0 lines. A board that crashed and went silent passes `--last-ms 10000 --forbid ERR` the same way.
  - The read commands share the silence: `mcu -p nosuch lines`, `tail -n 1`, `log export --limit 1`, `can dump -n 1` all print nothing and exit 0, while `cmd`, `wait` and a live `assert` refuse `no such port: nosuch`.
- SPEC 3.4 already refuses an unknown `session` for exactly this reason ("not an empty scope that would vacuously satisfy every forbid"); the port was left out.
- Fix: refuse a `port` that no attached port and no captured row carries (a detached board's history stays queryable); make a retrospective verdict with `checked_lines == 0` fail, or at least warn on stderr and say so in the guide.

## CLI-4 HIGH CONFIRMED: the guide's REST polling recipe drops rows; `--since-id` returns the newest N, not the next N

- Where: guide cli.py:2843-2844; cli.py:581-620 (`_fetch_newest` always walks newest first); cli_output.py:469 (remedy text).
- Failure: `GET /lines?since_id=N&limit=1000` answers the NEWEST 1000 rows above N. Following the guide ("keep the highest id seen and pass it back as N"), any poll that finds more than 1000 new rows skips the older ones for good.
  - Driven: after 20 s of sim traffic, `since_id=7409` returned ids 7917..8916 with `truncated: true`; ids 7410..7916 (507 rows) are never seen by the loop. `order=asc` returns 7410..8409, the rows the loop needs.
  - `mcu lines --since-id N` has the same shape (newest 100 above N), and its note says `raise --limit or use --since-id` to a caller already using `--since-id`.
- Fix: recipe `GET /lines?since_id=N&order=asc&limit=1000`, repeat while `truncated`; give `mcu lines --since-id` oldest-first paging (or document it as "newest N after").

## CLI-5 MEDIUM CONFIRMED: text output has no port column, and read commands span every port without `-p`

- Where: render.py:18-19 (`fmt_line`); guide cli.py:2644-2645 and the `-p` help (cli.py:111).
- Failure: with `sim` and `b2` attached, `mcu lines`, `tail`, `wait --match "^!can"` and `log export` interleave both boards as `HH:MM:SS.mmm  event| ...` with nothing saying which board a line came from; `wait` matched b2's frame and printed it bare.
- The guide and `--help` say `-p` is "required when several are connected", which is true for `cmd`/`send` only, so an agent seeing no error assumes its query was scoped.
- Fix: show the port in text rows whenever more than one port appears in the result (or always); correct the `-p` wording to say which commands default to all ports.

## CLI-6 MEDIUM CONFIRMED: "the whole run as state transitions" is the last ~100 raw rows

- Where: guide cli.py:2744; cli.py:829 (`--limit 100`), 848-851 (limit applied before decoding).
- Failure: `mcu lines --session run-3 --decode --changes` on a 20 s run printed 49 rows from its last 1.5 s; `log export` of the same printed 751. Only a stderr note (`truncated at 100 rows`, although 49 printed) says so, and `--json` hides it in `truncated`.
- An agent concluding "the state never reached FAULT during the run" is reading 7% of it.
- Fix: correct the example to `log export` or `--limit 0`-style unbounded, and say `--limit` counts raw rows before `--changes`/`--names` filter.

## CLI-7 MEDIUM CONFIRMED: `--json` row order is stated wrong for tail, log export and can dump

- Where: guide cli.py:2725 and 2734-2735.
- Failure: the section header says "lines, tail and log export share these options", then "--json is newest first". Only `lines --json` is. `tail -n 3 --json`, `log export --limit 3 --json` and `can dump -n 3 --json` emit JSONL oldest first (ids ascending).
- An agent taking the first JSONL row as "the latest value" reads the oldest.
- Fix: say "`lines --json` is newest first; JSONL output (tail, log export, can dump) is oldest first".

## CLI-8 MEDIUM CONFIRMED: `mcu send` of a monitor command prints `ok` and does nothing

- Where: guide cli.py:2693 (`mcu send "reset"`); cli.py:485.
- Failure: `mcu cmd "gpio set led 0"; mcu send "gpio set led 1"` -> `ok`, exit 0; `mcu cmd "gpio get led"` -> `0`. The monitor drops any line not starting with `>` (monitor.c:918).
- The guide's "WHAT IT IS" says the target runs the monitor, and its `send` example is a plausible monitor verb.
- Fix: guide line "a monitor ignores lines without a seq: use `cmd` for monitor commands; `send` is for non-monitor consoles and bootloaders"; pick a non-monitor example.

## CLI-9 MEDIUM CONFIRMED: the guide's wait-for-connect pattern is racy, and "no port-state flag" is false

- Where: guide cli.py:2663-2664, 2670-2673.
- Failure: `wait --chan sys --match "port b2 connected"` only sees rows after it starts. With b2 already connected: timeout, exit 2, while `mcu ports` says `connected`. A power-up that lands before the agent runs `wait` reads as "the board never came up".
- `mcu ports --json` / `status --json` carry `ports[].connected`, the flag the guide says does not exist.
- Fix: guide pattern "check `mcu ports --json` first; wait on the sys row only if still disconnected", and drop the "no port-state flag" sentence.

## CLI-10 MEDIUM CONFIRMED: a wedged daemon is exit 2 on every command but wait/assert (class 70)

- Where: cli_client.py:86 and 132 (`timeout_code=2` default); cli.py:1830-1832.
- Failure: against a listener that accepts and never answers (`hang.py` on 18670): `mcu cmd ping --timeout 500` -> `request timed out`, exit 2 after 6 s; `mcu status` -> exit 2 after 30 s.
- On `cmd`, exit 2 is also "the board did not answer" (`emit_cmd_result` prints `timeout`), so an agent resets or reflashes a healthy board for a stuck daemon. SPEC 4 moved `wait`/`assert` off 2 for this reason and left the rest.
- Fix: transport timeouts exit 1 everywhere (the daemon is reachable but not answering), keeping 2 for a timeout the daemon reported; or 3, if "not answering" is to count as unreachable. Owner should pick.

## CLI-11 MEDIUM CONFIRMED: stdout closed at start is devnull: exit 0, and `tail -f` never ends (class 9)

- Where: _stdio.py:150-156 (`repair_std_streams` swaps a None stdout for devnull), _stdio.py:393.
- Failure: `mcu status >&-` -> exit 0 plus a five-line interpreter warning; `mcu --json status >&-` the same. `timeout 10 mcu -p sim tail -f >&-` ran until killed (124).
- SPEC 4: "output that could not be written turns a 0 into 1 ... a closed stdout ends a -f follow with 0". The pipe case holds (`tail -f | head -1` -> 0); the closed-fd case breaks both halves.
- Fix: on POSIX, treat a stdout that was None at startup as unwritable (exit 1, and end a follow at once); keep the devnull repair for Windows' no-console case it was written for.

## CLI-12 MEDIUM CONFIRMED: text-mode confirmation blocks on a non-tty stdin

- Where: cli_output.py:482-496 (`confirm_or_exit` refuses only in `--json` mode; text mode reads stdin whatever it is).
- Failure: `sleep 15 | mcu purge --session junk1` hung at the prompt until the 8 s `timeout` killed it (124). `--json` refuses at once with "pass -y". An agent harness that spawns with stdin as an open pipe hangs on `purge` or `session delete --data`; one whose stdin carries a protocol stream loses a line of it.
- Fix: refuse whenever stdin is not a tty, in both modes ("pass -y to confirm").

## CLI-13 LOW CONFIRMED: `open_failed` advice sends the agent to a REST call it does not need

- Where: guide cli.py:2665-2667; serial_link.py:556-565 (retries with backoff up to 5 s), serial_link.py:483-484 (a URL device always counts as present).
- Failure: the guide says `open_failed` needs "free it, then POST /ports/<alias>/reconnect (no CLI verb)". The reader retries on its own: b2 went from `open_failed` back to `connected` with no action once its server returned. For `socket://`, a server that is simply down also reads `open_failed`, not `no_device`, so the "another process holds it, or permissions" diagnosis is wrong there.
- Fix: guide "retries every few seconds on its own; reconnect only skips the wait"; mention `socket://` reads `open_failed` when the far end is down.

## CLI-14 LOW CONFIRMED: `--eol none` leaves bytes that make the next monitor command time out

- Where: guide cli.py:2711-2715; monitor.c:918.
- Failure: `mcu -p sim send --eol none $'\x03'` then `mcu -p sim cmd ping` -> `timeout`, exit 2; the next `cmd` works. The unterminated byte prefixes the next line, which then does not start with `>`.
- Fix: one guide line: follow an `--eol none` send to a monitor with `mcu send ""` (a bare LF) before the next `cmd`.

## CLI-15 LOW CONFIRMED: the follow's WebSocket URL does not quote `-p`; a WS 400/404/500 is exit 3

- Where: cli.py:1038-1039, 1156-1159.
- Failure: `mcu -p 'sim&chan=sys' tail -f` followed port `sim` (every channel) where `lines -p 'sim&chan=sys'` found nothing; `mcu -p 'sim x' tail -f` -> `websocket refused by daemon: HTTP 400`, exit 3 ("unreachable") from a daemon that answered.
- Fix: `urllib.parse.quote(s.port, safe='')`; map any HTTP status on the upgrade to 1, keeping 3 for no answer.

## CLI-16 LOW CONFIRMED: guide gaps and weight

- Missing, and each one cost a probe to find:
  - `mcu cmd ping` / `mcu cmd info` (board identity, `can=N` bus count); the guide's `gpio led`, `adc vbat`, `spi imu` names are port-layer names an agent cannot discover.
  - `wait` sees only lines arriving after it starts; `mcu mark X; mcu wait --match X` times out.
  - Which commands emit JSONL (`tail`, `log export`, `can dump`, `-f` or not): "streaming cmds" reads as `-f` only.
  - `--limit 0` means "every row" on `log export` and "no rows" on `lines`.
  - A retrospective `assert` with no scope judges the whole capture.
  - `mcu daemon start` on a running daemon is exit 1 ("daemon already running"), so "ensure it is up" needs `daemon status` first.
- Weight: 240 lines, 18 KB. PlotJuggler, `--install-completion`, `session export --bundle` internals, the pre-0.4.0 version gating and the purge matrix sit between the agent and the traps above. Suggest moving those to `--help` and adding a short "pitfalls" block near the top holding CLI-1, -2, -3, -5, -8, -9.

## CLI-17 LOW CONFIRMED: output and message nits an agent trips on

- Refusals in daemon vocabulary rather than option names: `repeat_ms must be between 10 and timeout_ms`, `min_window_ms needs a live window (set timeout_ms too)`, `eol applies to send; set send too` (cli.py:1205-1209, 1305-1311).
- `plot export --names vbat` -> `no such plot channel: vbat; see /plot/channels`, a REST route, not `mcu plot channels`.
- `mcu daemon stop` with nothing running -> `no pid file; daemon not started by this CLI`, exit 1 (cli.py:2564): reads as "a daemon exists".
- `mcu status` with no ports attached prints no port line at all (cli.py:189); `ports` says "no ports attached".
- A successful `cmd` with no data (`can tx`, `can filter all`) prints nothing, while `send` prints `ok`.
- `tail -n 0 -f` and `can dump -n 0 -f` always print `results truncated at 0 rows; older matches exist`, for the documented follow-only form.
- `mcu mark "-pwm duty 50"` -> `Missing argument 'text'`: the hoister took `-pwm duty 50` as `-p` (cli_argv.py:134-135); `--` works.
- `mcu send -` writes a literal `-` line to the board.
- `purge --id-from 500 --id-to 100 --dry-run` is accepted and prints `(ids 500-100)`; `--from` after `--to` is refused elsewhere.
- `mcu attach DEV --alias board` over an existing `board` silently retargets it.

## CLI-18 LOW SUSPECTED (owner should pick): the `-p` default follows whichever board is connected

- Where: SPEC 4 ("or the only connected one when others are still reconnecting").
- Driven: with `b2` down and `sim` up, `mcu cmd ping`, `send`, `break` went to `sim` without `-p`, and the output does not name the port.
- On a two-board bench, an agent driving board B while B power-cycles sends `gpio set relay 1` to board A. By design per SPEC; flagged because the guide's "default: the only connected port" does not warn about it.

## Checked and fine

- `--json` gives one object on stdout (JSONL for tail/log export/can dump) for every command swept, success and error, including usage errors, `--json --url` with no value, unknown commands, `--version`, `ai-guide`.
- Exit codes: bus ERR 1, cmd timeout 2 (`mcu-sim --drop-response 3`), wait timeout 2, assert fail 1, usage 1, unreachable 3 on every command incl. `tail -f`, `can dump -f`, `log export`, `daemon status` (`{"running": false}`).
- Daemon stopping under `wait`, `assert`, `tail -f`, `--json tail -f` -> 3; the JSONL follow ends with `{"error", "exit_code": 3}`; `can dump -f` gives up after 30 s with 3.
- Ctrl-C: `wait` 1 "interrupted", `tail -f` and `can dump -f` 0.
- Pipes: `lines | head -1`, `--json tail -f | head -1`, `can dump -f | head -1`, `log export | head -1`, `ai-guide | head` -> 0; `>/dev/full` -> 1 with `cannot write output`; closed or full stderr keeps the code (1, 2, 3 checked).
- Daemon starting: `status` during `daemon start` is 3 until bound, then 0; `daemon start` twice -> 1 "already running"; `restart --sim` keeps the config path; `daemon stop` -> 0.
- Quoting: commands with spaces, 13+ tokens (refused client-side), non-ASCII (refused), `-`-leading values after `--`, global options after the subcommand, `--match --json` / `--match -p` kept as values.
- Two ports: `cmd` without `-p` lists the aliases; disconnected port refuses cmd/send/break/sysrq/can tx with exit 1; `--repeat-ms` against it counts failures (`sends 0, 10 writes failed`), exit 2.
- Refusals: `--chan evnt`, bad regex, `--order up`, `--last-ms -5`, `--from 25:00`, `--from` after `--to`, unknown session, assert mode mixing, `--repeat-ms` bounds, `--csv` with `--json`/`--limit`, `--bundle -o x.db`, purge selector count, `--json purge` without `-y`.
- Session start/stop/list/export/`--bundle`/delete `--data --yes`, `log export -o` (JSON summary), `can dump --csv -o`, `plot channels --active`, `plotjuggler`, `config path`.

## Not covered

- Token-protected daemon (401/403/429 paths) and a pre-0.4.0 daemon (version gating, 404 naming).
- `--decode` correctness beyond the row count in CLI-6; `plot export --decode/--changes/--deadband`.
- `--repeat-ms` against a port that connects mid-wait (bootloader catch).
- Windows (CRLF, console repair, `COMx`), and real hardware.
- `mcu --install-completion`, `daemon start --open`, `MCUSCOPE_START_TIMEOUT`.
