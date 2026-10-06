# agentux: does `mcu ai-guide` tell the truth, and does an agent following it succeed

Setup: isolated `mcuscoped --sim` on 18660 (scratch config, db and `MCUSCOPE_*_DIR`), a second `mcu-sim` on 18661-18663 attached as extra boards, a second daemon on 18665 for `mcu daemon` paths.
Probes were run as `uv run mcu ...` from `host/`; outputs quoted below are from those runs.

## AGENTUX-1 MEDIUM CONFIRMED: `mcu daemon start/restart` silently overrides the config's `[server] host`/`port` with the `--url`'s

- `host/mcuscope/cli.py:2644-2648`, `host/mcuscope/cli_daemonctl.py:25-28`.
- Failure: `start` always spawns `mcuscoped --host <url host> --port <url port>` (default `127.0.0.1:8558`), and `--host`/`--port` win over the file.
  - A config with `port = 18667` started with `MCUSCOPE_URL=...:18665`: `started mcuscoped ...; web UI: http://127.0.0.1:18665/ui/`; `ps` shows `-m mcuscope.daemon --host 127.0.0.1 --port 18665 --config ...`; `ss` shows it listening only on 18665.
  - `mcu daemon restart` after editing `server.port` keeps the old port. SPEC 3.3 says `server.host/port` "only apply on restart", which reads as "apply on restart".
  - README "LAN access" says set `host = "0.0.0.0"` in the config: a daemon started with `mcu daemon start` binds 127.0.0.1 regardless, with no warning.
- Nothing in `ai-guide`, the SPEC 4 table or either README says `start` takes its address from `--url`.
- Fix: when a config names `[server]` host/port and no `--url`/`MCUSCOPE_URL` was given, start on the config's address and probe that. When they differ, refuse or warn, naming both. Then document the rule in the guide and SPEC 4.

## AGENTUX-2 MEDIUM CONFIRMED: `mcu mark` without `-p` is invisible to every `-p` read; the guide's agent pattern hits this

- `host/mcuscope/cli.py:576-583` (mark sends `port: s.port`), SPEC 3.5 (`''` is the daemon's own rows).
- Failure: `mcu mark NOPORT1` stores `port=""`. `mcu -p sim lines --chan marker` then lists only `-p sim` marks (`PM1`) and firmware `!m` rows, not `NOPORT1`.
  - Session start/end markers are hidden from a `-p` read the same way.
- The guide's TYPICAL AGENT PATTERN puts `-p board` on every read, and its `mcu mark "starting test"` example has no `-p`. An agent that marks, then reads with `-p`, never sees its own marker. `assert -p board --chan marker` and `log export -p board` behave the same.
- The guide does not say that `mark` takes `-p`, or that `-p` excludes daemon-level rows.
- Fix: say both in the guide's PITFALLS. Better, have a `-p` read include `port=''` rows, or have `mark` default to the only/addressed port.

## AGENTUX-3 MEDIUM CONFIRMED: with two boards, finished `lines`/`tail -n` output can drop the `[port]` tag, contrary to the guide

- `host/mcuscope/cli.py:888-900` (`_port_column` judges a finished result on its own rows), AI_GUIDE `cli.py:2941`.
- Guide: "their text rows then carry [port] when more than one board is attached or has stored rows".
- Actual: SPEC 4 and the code decide per result. `mcu tail -n 3`, run with `sim` and `board` both attached and connected, printed `17:20:29.061  debug| state: IDLE -> ARMED` (a `board` row) with no tag. The next run, whose rows spanned both boards, printed `[sim]`/`[board]`.
- An agent running `mcu lines --match ERR` across two boards gets an unattributed hit whenever only one board matched, and can act on the wrong board.
- Fix: decide the column for finished results by the stream rule (attached plus stored ports), which matches the guide and keeps the output's shape stable. Otherwise correct the guide to the SPEC rule and tell agents to pass `-p` or `--json`.

## AGENTUX-4 MEDIUM CONFIRMED: `wait --json` reports `"status": "match"` on an exit-1 failure, and a refused `--send` still waits out the full timeout

- `host/mcuscope/cli.py:1428-1435`.
- `mcu wait --send bogus --match x --timeout 1000 --json` gives exit 1 and `{"status": "match", ... "cmd_result": {"status": "err", ...}}`. Text mode prints the matched line to stdout, then exits 1.
- The guide's TYPICAL AGENT PATTERN step 2 says to "check status". An agent doing that at step 3 reads success.
- The refusal arrived after 1.7 ms, but the daemon kept waiting: `waited_ms` was 624 for an unrelated match and 1000 with no match. With `--timeout 30000`, a typo'd command costs 30 s before the exit-1 answer.
- Fix: add a top-level verdict field (`"ok": false` or `"status": "send_refused"`), or document in the guide that `cmd_result.status` must be checked as well. Have the daemon end the window when the `cmd`-mode send gets ERR, as `/assert` already judges it failed.

## AGENTUX-5 MEDIUM SUSPECTED (doc gap CONFIRMED): `dropped` is not in the guide; an assert that passes over a holed window exits 0

- `host/mcuscope/server.py:3215-3230` (`verdict`: `dropped` only changes the status when `checked == 0`), `cli.py:1535-1537` (stderr warning only).
- SPEC 3.4 says a non-zero `dropped` means "the caller should retry rather than treat it as a negative result". `grep dropped` over `mcu ai-guide` finds only the CAN and read_error lines.
- A forbid-only `assert` with `checked_lines > 0` and `dropped > 0` returns `status: pass` and exit 0. An agent following "pass/fail on an exit code" accepts a window whose shed rows may have held the forbidden line.
- Not driven: a 200k lines/s flood sim and 16 slow forbids gave `dropped: 0` in four runs. To confirm, make the subscriber feed shed (a test double or a smaller queue) and run `assert --forbid`.
- Fix: put `dropped` in the guide (VERDICTS and wait), and either make `pass` with `dropped > 0` a distinct status or exit code, or document "check dropped == 0".

## AGENTUX-6 MEDIUM CONFIRMED: JSON errors carry only free text; an agent must parse prose to choose its recovery

- `{"error", "exit_code"}` is the whole error object. Exit 1 covers all of these, each needing a different next step:
  - `port is ambiguous; specify one of: sim, board (with -p)`: add `-p`.
  - `port board is not connected`: wait for the reconnect.
  - `no such port`: list the ports.
  - `bad match regex`: fix the pattern.
  - `no such session`: check the name.
  - A usage error.
- The text is also inconsistent: `"error: no such port: nope"` keeps the `error: ` prefix inside the field, while `"No such command 'nonexistent'."` does not. Some messages are daemon validation paths: `error: chan.0: Input should be ...`, `forbid: List should have at most 16 items after validation`.
- Fix: add a stable `"kind"` (or the daemon's own code) to the error object, strip the `error: ` prefix, and map pydantic paths to the CLI flag name. Put the 16-pattern cap in the guide.

## AGENTUX-7 LOW CONFIRMED: error and timeout messages that do not say what to do next

- `mcu cmd ping` with no response: stderr is the single word `timeout` (`cli_output.py:640`). It names no port and no command, and does not suggest `--timeout`/`mcu status`. `wait`'s timeout message names all of these.
- `-p board wait` on a disconnected port runs to the full timeout and says only `timeout: no line matched 'x' on port board in 501 ms`. Nothing says the port is `disconnected (open_failed)`.
- `daemon unreachable at URL: [Errno 111]` does not suggest `mcu daemon start`.
- `can dump -f` against a stopped daemon prints `warning: skipping bad update: [Errno 111] ...`, then `warning: skipped 125 updates` (`cli.py:1099-1103`). An "update" is a poll; the real cause (`daemon unreachable`) arrives only 30 s later.
- `assert --session S --timeout N` is refused with `session needs a retrospective window (leave timeout_ms at 0)` (`server.py:3195`), which names the wire field rather than `--timeout`.
- Fix: one clause per message naming the next command or flag.

## AGENTUX-8 LOW CONFIRMED: guide and snippet statements that are wrong or misleading

- `docs/CLAUDE_SNIPPET.md`: "`mcu assert --session <name> ...`; add `--timeout MS` to judge a live window". Read literally, this adds `--timeout` to the `--session` command, and that is refused (AGENTUX-7).
- Guide `cli.py:3048`: `--last-ms` is "back from the daemon's clock". For an ended `--session` it counts back from that session's newest line (SPEC 4). `assert --last-ms 5000 --session boot-test2` judged a run that ended 3 minutes earlier, which the guide's wording says should be empty.
- Guide `cli.py:2949`: "`mcu mark X; mcu wait --match X` times out" is given as the example of "sees only later lines". A marker never matches without `--chan marker`, so the example times out for a different reason.
  - With `--chan marker`, a wait started before the mark matches (driven).
- Guide `cli.py:2928`: `mcu cmd ping (OK monitor 1 <board name>)`; the CLI prints `monitor 1 sim` without `OK`.
- Guide `cli.py:3099`: "one session per run". `session stop` immediately opens a new `auto-...` session in the same run (ids 4 and 6 here).
- Session names are not unique: `session start boot-test` twice gives no warning, and `--session boot-test`, `session delete boot-test` and `purge --session` act on the newest. The guide never says so, or that an id (`--session 2`) reaches an older one (driven, works).
- Guide "DELETING CAPTURE ... the count is always shown first". With stdin not a tty and no `-y`, `purge --all` refuses without printing the count (`cli.py:1782-1784`, `cli_output.py:608-612`).
- Unknown `-p` "is refused ... on reads too", yet `-p nosuch session start q` and `-p nosuch status` succeed, ignoring it, while `-p ''` is refused on both.
- Fix: correct each line.

## AGENTUX-9 LOW CONFIRMED: `--json` output gaps

- The `tail`, `can dump` and `log export --limit N` JSONL streams carry truncation only as the stderr `note:`. `lines --json` has `"truncated"`, and `-o --json` has it too. Fix: a final `{"truncated": true}` line, or document it.
- `plot export --json` without `-o` returns `{"names", "format", "rows", "csv": "ts,tick_ms,...\n..."}`: CSV inside a string. Fix: `"rows": [{...}]`.
- `can stat`, `adc read` and `cmd info` `--json` return `data` as the firmware string (`"rx=1858 tx=6 err=0 state=active"`). The sugar commands know the grammar and could add parsed fields.
- `send --json` is `{"ok": true}` with no `line_id`, while `cmd` and `mark` return one, so there is no `--since-id` anchor after a raw send.
- `purge --id-from 1 --id-to 50 -y --json` with nothing left to delete prints `{"deleted": 0, ..., "dry_run": true}` (`cli.py:1776-1780`): the caller asked for a real delete.
- `plot export` values carry float noise (`19.200000000000003`, `12.030000000000001`), while `plot channels` prints `20.9 mA`.

## AGENTUX-10 LOW CONFIRMED: ambiguous text output

- `session list --limit 0` prints `no sessions recorded` while six exist and one is running (`cli.py:1617-1618`). The `--json` form is `{"sessions": [], "active": {...}}`.
- `session delete boot-test` (label only) prints `deleted session boot-test (0 lines)`, which reads as an empty session. It means 0 lines deleted.
- `--limit 0` means "no rows" on `lines` and "every row" on `log export`. `log export`'s note says `use --limit 0 for every row` right beside `lines` notes that never do.
- Daemon-level rows in a multi-board text view render as `[] marker| QQ` (`render.py:33`), one column narrower than `[sim]  event|`.
- `lines --names nosuchfield` silently drops every sample and shows the other rows. `plot export --names nosuch` refuses (`no such plot channel`). The guide's `--names state,vbat` example gets no warning for `vbat` on a board without it.
- `assert` prints PASS/FAIL summaries to stdout and the EMPTY summary to stderr.

## Checked and fine

- Exit codes:
  - `cmd` ok 0, ERR 1, `--timeout 1` 2.
  - `wait` timeout 2, bad regex 1.
  - Unreachable `--url` 3 for `status`/`cmd`/`ports`/`daemon status`.
  - `wait` and `tail -f` exit 3 when the daemon stops; `tail -f --json` ends with `{"error": "stream closed by daemon", "exit_code": 3}`.
  - `can dump -f` exits 3 after 30 s.
- `--json` stdout purity: every error case above wrote one JSON object to stdout and prose to stderr. This covered usage errors, unknown commands, `-p ''`, `--csv --json` and the purge prompt refusal.
- Multi-port write refusal lists the aliases, for `cmd`, `send` and REST `/send`. `mark` needs no `-p`.
- `cmd --json` shapes for ok, err and timeout.
- `wait --send` and `--raw` timeout messages with send counts.
- `--repeat-ms` stderr counts.
- `--eol none $'\x03'`, then `cmd` times out, then `send ""` recovers, exactly as the guide says.
- `assert` live, retrospective, `--min-window`, empty, failed send ("not judged"), and unknown session.
- `--session` with `--timeout` is refused.
- `assert --last-ms 0` is refused, and `lines --last-ms 0` is accepted.
- Sessions: start, stop, list, `--session` by name and id, export `.db` and `--bundle`.
- Refusals:
  - `--bundle` with a `.db` name.
  - `-o` naming a directory.
  - `-o -`.
  - `--from` after `--to`.
  - `purge --id-from > --id-to`.
- `lines --since-id` paging note, `--order asc`, and `--limit 0` giving `truncated` only.
- `log export` default every row, `--csv` refusals, and `-o --json` fields `{"file", "lines", "bytes", "truncated"}`.
- `can tx --ext` stores `can tx C0103 B400 x`. `--bus 2` works and `--bus 3` gives `ERR 2 badarg`. `can dump` prints `bus=` only for bus 2. CSV `can_id` is decimal, as SPEC says. `--to` with `-f` is refused.
- `plot channels`, `--active`, `plot export` long, `--wide` and `-o --json`. Unknown names are refused with a pointer to `plot channels`.
- `pj` on/off/`--save`/`--json`.
- `attach` by URL, by `--serial` (`no_device`), retarget note, `--eol`/`--baud`. `detach` handles `/` and unknown aliases.
- The guide's reconnect recipe `wait --chan sys --match "port board2 connected"` matched once the sim came up.
- `daemon start`/`status`/`stop`/`restart` messages and exit codes: already running gives 1, and nothing to stop gives 1.
- Text escaping: VT, U+2028 and ESC in a marker are escaped in text and raw in `--json`.
- The guide's REST primitives: `POST /send` with `eol`, and `GET /lines?since_id=&order=asc&limit=`.
- Flag sweep: every `--flag` named in `ai-guide`, `host/README.md` and `CLAUDE_SNIPPET.md` exists in the click tree. The README extras are `mcuscoped`/`uv` flags.
- Every runnable example in `ai-guide`, both READMEs and the snippet was run. Failures came only from firmware-specific names the docs call examples (`vbat,temp`, `selftest`, `CALIB DONE`, `1A3`), and those errors are clear.

## Not covered

- Windows quoting: `host/README.md:69` uses single quotes (`mcu cmd 'i2c scan'`). The snippet warns these break under `cmd.exe`, and `host/README.md` lacks the main README's PowerShell note. Not driven.
- The version-skew refusal, token/401 paths, `DEGRADED` status, `daemon start` index-build waits, and the subscriber cap.
- `sysrq` and `break` on a real target. `--open`.
- A real `dropped > 0` (AGENTUX-5).

## The two questions

1. Least confident: AGENTUX-3, because the code's docstring treats the per-result rule as deliberate (SPEC agrees). Re-drove `tail -n 3` five times with two connected boards: untagged single-board results recurred.
   AGENTUX-5's pass-with-holes branch is reasoned from `server.py`, not driven.
2. Not thought about: no test pins `mcu daemon start` honouring a config's `[server]` address (AGENTUX-1). Commands that ignore `-p` are also untested against the "unknown -p is refused" contract.
   The guide is checked against the click tree for option names (`test_cli_contract.py`) but not for behavioural claims. A test that runs each guide example against the sim, and asserts on its exit code, would have caught AGENTUX-3 and the AGENTUX-8 lines.

Scratch: `~/tt-data/mcuscope-2026-10-05/agentux/` (56 MB: two isolated configs, capture DBs, exports, `flags.py`). All processes started here are stopped.
