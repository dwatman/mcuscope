# Leg B: CLI (pre-release review of v0.4.0..fdd30a2), Linux

Scope: `host/mcuscope/cli.py`, `cli_client.py`, `cli_output.py`, `AI_GUIDE`, SPEC 4.
Stacks: HEAD `mcuscoped --sim` on 18612 with a scratch TOML and db; the real v0.3.0 and v0.4.0 daemons (`git archive` extracts run with `PYTHONPATH`) on the same port, one at a time.
Scratch and drivers: `~/tt-data/prerelease-2026-09-15/B/` (`jsonsweep.py`, `closedstderr.py`, `winstdout.py`, `cap503.py`, logs).
All commands through the installed console script `host/.venv/bin/mcu` with `MCUSCOPE_URL=http://127.0.0.1:18612` unless stated.
Side effect: the B-6 drives wrote the real `~/.local/share/mcuscope/mcu-crash.log` (the crash handler has no override).

## Findings

### B-1 MED: `can dump --session S -f` follows past the session

- Where: `cli.py:1818-1819`, `_dump_follow(client, s, ",".join(can_id) or None, bus)` drops `session`.
- Defect: the backfill is scoped to the session, the follow is not, so an ended session streams frames from outside it for ever.
  - The same shape as `--to` with `-f`, which this diff refuses; SPEC 4 and AI_GUIDE ("that run's frames only") both promise the session scope.
- Scenario: session `runA` is lines 6490-6585; `mcu --json can dump --session runA -n 1 -f` -> first row `line_id` 6581, then 34 rows with ids 6791..6969, rc 124 (killed by `timeout 3`).
- DRIVEN: `mcu session start runA; sleep 1; mcu session stop; sleep 2; timeout 3 mcu --json can dump --session runA -n 1 -f > f1.jsonl`.
- Class 47 (a live-only surface accepts a scope only a live object can satisfy).
- Fix: put `session` into the follow's poll params (the daemon already filters `/can/frames?session=`), which is right for both a running and an ended session.

### B-2 MED: `-p` on `plot export` is still sent ungated to a pre-0.4.0 daemon

- Where: `cli.py:2110-2111` gates only `decode or changes or deadband`; `cli.py:2121-2122` sends `port`.
- Defect: the 2026-09-12 fix-diff sweep ruled `port -> /plot/export` a class 53 violator (F2 lists `-p`); the fix gated the other four and left this one.
- Scenario: against a real v0.3.0 daemon, `mcu -p nosuchboard plot export --names ramp -o p030b.csv` -> `wrote 351 rows`, rc 0 (HEAD daemon: `error: no such plot channel`, rc 1).
- DRIVEN (v0.3.0 extract, `mcu status` -> `mcuscoped 0.3.0`).
- Class 53.
- Fix: add `or s.port` to the gate condition.

### B-3 MED: body fields added in 0.4.0 are dropped silently by an older daemon

- Where: `eol` in `cli.py:367` (attach body), `458` (send), `1136` (wait), `1235` (assert), `1685` (cmd); `repeat_ms` at `1138`.
- Defect: pydantic ignores unknown body fields exactly as FastAPI ignores unknown query parameters, and v0.3.0's models declare neither `eol` nor `repeat_ms`; no gate covers them.
- Scenario, v0.3.0 daemon:
  - `mcu attach socket://127.0.0.1:1 --alias e1 --eol crlf` -> `attached e1 ...`, rc 0; `mcu --json ports` shows no `eol` on `e1`.
  - `mcu -p sim send eoltest --eol none` -> `ok`, rc 0, LF still appended.
  - `mcu wait --match ZZZ --send "" --repeat-ms 50 --timeout 300` -> `sent 0 times, 0 writes failed` (a defaulted count, not a result, class 17) and exit 2; the resend never happened.
- DRIVEN.
- New class candidate: widen class 53 to "a field the peer may not declare, query or body"; its sweep enumerates only `params[...]`.
- Fix: route `--eol` and `--repeat-ms` through the version gate (rename `_require_export_daemon` to a general one); drop the `sends` default of 0.

### B-4 MED: a refused export destroys what `-o` pointed at

- Where: `cli.py:1531` (and `1637`) open `-o` with `"w"` before the request; the `finally` at `1549-1554` / `1653-1659` then `os.remove`s it.
- Defect: a 4xx refusal (no byte streamed) truncates and deletes the target.
  - The docstring at `1500-1502` claims "a file this command never wrote must survive the failure".
  - `Client.download` (session export) opens after the status check and keeps the file.
- Scenario:
  - `echo data > target.txt; ln -s target.txt link.txt; mcu log export --match '(' -o link.txt` -> rc 1, `link.txt` removed, `target.txt` now 0 bytes.
  - `mkfifo fifo1; (cat fifo1 &); mcu log export --match '(' -o fifo1` -> rc 1, the FIFO removed.
  - `echo keep > p.txt; mcu plot export --names nosuchchan -o p.txt` -> rc 1, `p.txt` gone; `mcu session export nosuch -o p2.txt` keeps `p2.txt`.
  - REASONED: as root (CI containers), `mcu log export --match '(' -o /dev/null` removes `/dev/null`.
- DRIVEN (all but the root case).
  - The 2026-09-12 CLI leg ruled "on a failure the guard removes it" the class 49 contract.
  - That ruling covered a stream failing after bytes were written, not a refusal before any.
- New class candidate: an output target opened before the peer has accepted the request.
- Fix: open on the first chunk after a status below 400 (as `download` does), and remove only a regular file this command created or wrote.

### B-5 MED: the in-band error object is glued onto a partial JSONL row

- Where: `cli.py:1514-1524` writes chunks unaligned to lines; `die()` in JSON mode then prints `{"error", "exit_code"}` with no leading newline.
- Defect: SPEC 4 promises the error object "as the final JSONL line"; when the daemon dies mid-row neither the row nor the error parses.
- Scenario: `mcu --json log export | (sleep 3; cat) > kill.jsonl`, daemon SIGKILLed at 1.2 s -> rc 3.
  - Last line: `{"id": 42628, ..., "seq": nul{"error": "daemon unreachable ...", "exit_code": 3}`; `json.loads` fails on it.
- DRIVEN.
- Class 10.
- Fix: remember whether the last chunk ended in `\n` and write one before the error object (or hold back the partial tail).

### B-6 MED: `--from`/`--to` near the calendar limits is a traceback

- Where: `cli_output.py:259`, `datetime.combine(day, clock).timestamp()` sits outside the `try` that maps `ValueError` to `BadParameter`.
- Scenario: `mcu lines --limit 1 --from 9999-12-31T23:59` -> rc 1, no JSON with `--json`.
  - stderr: `mcu: fatal error; traceback written to .../mcu-crash.log` and a rich traceback ending `ValueError: year 10000 is out of range`.
  - Same for `--from 0001-01-01T00:00`, `--to 0001-01-01T00:00`, `mcu can dump -n 1 --from 9999-12-31T23:59` (every command taking the pair).
- DRIVEN.
- Class 9.
- Fix: move the `.timestamp()` call inside the `try` and catch `(ValueError, OverflowError, OSError)`.

### B-7 LOW: the paged `log export` still writes CRLF to a Windows stdout

- Where: `cli.py:1664-1666` (`print(render(row))`); `_stdout_untranslated()` is called only from `_stream_export` (`1512`).
- Defect: C10's fix covered the streamed path; `--limit` and `--decode/--changes/--names` without `-o` still translate, while their `-o` form is opened `newline="\n"`.
- Scenario, stdout replaced by `TextIOWrapper(newline="\r\n")` (what a redirected Windows stdout is):
  - `log export --decode`: stdout 51973 bytes with 1115 CR, `-o` 50858 bytes with 0 CR.
  - `log export --limit 20`: 857 bytes with 20 CR against 837 with 0.
  - `--csv`, `plot export` and `can dump --csv` are equal.
- DRIVEN by emulation (`winstdout.py`); not run on Windows.
- Class 2/13.
- Fix: call `_stdout_untranslated()` in the paged branch before printing.

### B-8 LOW: a usage error with stderr closed exits 120 and loses the `--json` object

- Where: `cli.py:2754-2760`, `exc.show()` writes to stderr unguarded, before `out_json`.
- Scenario, stderr a pipe with its read end closed:
  - `mcu lines --bogus`, `mcu lines --limit -1`, `mcu nosuchcmd`, `mcu lines --from 23:00 --to 01:00` -> rc 120 (attached: rc 1).
  - `mcu --json lines --bogus` -> rc 120, stdout empty.
- DRIVEN (`closedstderr.py`, differential against stderr attached; every `die()` path in the diff kept its code).
- Class 35 (and 10).
- Fix: guard `exc.show()` the way `err()` is guarded (suppress, `_to_devnull`), then emit the JSON object.

### B-9 LOW: the unencoded session name in the export path

- Where: `cli.py:1347`, `f"/sessions/{name}/..."`.
- Defect: SessionBody allows any 1..128 characters, and `--session` on the query commands handles them, but `session export` puts the name into the path raw.
- Scenario, sessions named `fw/v2`, `run?x=1`, `run#3`:
  - `mcu session export fw/v2 -o s.db` -> `error: Not Found`, rc 1.
  - `run?x=1` and `run#3 --bundle` -> `error: Method Not Allowed`, rc 1.
  - `mcu can dump --session fw/v2` works.
- DRIVEN.
- New class candidate: user text interpolated into a URL path.
- Fix: resolve the name to an id through `/sessions?name=` first, as `session delete` already does (`%2F` would still 404 daemon-side).

### B-10 LOW: `session export --bundle -o DIR/` writes a hidden `DIR/.zip`

- Where: `cli.py:1345-1346`; `splitext("adir/")` has no extension, so `.zip` is appended to the separator.
- Scenario: `mcu session export 1 --bundle -o adir/` -> `wrote 621071 bytes to adir/.zip`, rc 0; without `--bundle` the same `-o adir` is `Is a directory`, rc 1.
- DRIVEN.
- No registry class (nit-level sibling of C9).
- Fix: refuse a target ending in a separator or naming an existing directory before appending.

### B-11 LOW: the truncation note names options the command does not have

- Where: `cli_output.py:401`, remedy `"raise --limit or use --since-id"`, called from `tail` (`cli.py:844`) and `log export` (`1669`).
- Scenario:
  - `mcu tail -n 3` -> `note: ... (raise --limit or use --since-id)`; `mcu tail --limit 5` -> `No such option: --limit`.
  - `mcu log export --limit 3 -o f` names `--since-id`; `mcu log export --since-id 5` -> `No such option: --since-id`.
- DRIVEN.
- Class 58.
- Fix: pass the remedy from the caller (`-n` for tail, `--limit` and a narrower `--from` for log export).

### B-12 LOW: `--from`/`--to` costs a request before the usage refusals

- Where: `cli.py:2102` (`_clock_bounds`, which now calls `/status`) precedes the refusals at `2105-2108`, contradicting the comment at `2109`; `cli.py:1592` precedes the `--csv` refusal at `1595`.
- Scenario, daemon down:
  - `mcu plot export --names ramp --changes` -> rc 1 `changes requires decode`; add `--from 10:00` -> rc 3 `daemon unreachable`.
  - `mcu log export --csv --limit 5` -> rc 1; add `--from 10:00` -> rc 3.
- DRIVEN.
- Class 9.
- Fix: call `_clock_bounds` after the local refusals in both commands (can dump already does).

### B-13 LOW: SPEC 4 says a `wait` timeout names the port; it does only with `-p`

- Where: `docs/SPEC.md:1058` against `cli.py:1159`.
- Scenario: `mcu wait --match ZZZ_never --timeout 200` (sole port auto-resolved) -> `timeout: no line matched 'ZZZ_never' in 200 ms`.
- DRIVEN.
- SPEC is the one to change: the `/wait` response carries no port, so the CLI cannot name a port it did not choose.
- Class 19 (two descriptions of one thing).
- Fix: SPEC wording "the port given with -p".

### B-14 LOW: `can dump -o F --json` is refused naming `--csv`

- Where: `cli.py:1780` (`csv = csv or out_file is not None`) then `1788-1789`.
- Defect: the refusal names an option the user did not pass, and the siblings answer `-o --json` with a JSON summary.
  - `log export` gives `{"file","lines","bytes","truncated"}`, `plot export` gives `{"file","rows","bytes"}`.
- Scenario: `mcu --json can dump -o cj.csv` -> `--csv and --json are two output formats; pick one`, rc 1.
- DRIVEN.
- Same shape as C8 and F5 (a refusal naming an untyped option).
- Fix: with `-o`, let `--json` describe the file as the siblings do.

### B-15 LOW: `--last-ms` takes negative and oversized values client-side

- Where: `--last-ms` options at `cli.py:758`, `1561`, `1758`, `2072` have no bounds; `_absolute_window` (`597-607`) converts before the daemon's `le=MAX_MS` can see it.
- Scenario:
  - `mcu plot export --names ramp --last-ms -5000` -> header-only CSV, rc 0; `lines`, `log export`, `can dump` with `-5000` are empty at rc 0.
  - `mcu lines --last-ms 99999999999999999999999` answers 100 rows; `mcu can dump` with the same value is refused by the daemon.
- DRIVEN.
- Class 19 (two engines validating one thing).
- Fix: `min=0, max=MAX_MS` on the option, shared by all four commands.

### B-16 LOW: `--deadband` still accepts a non-decimal spelling

- Where: `server.py:2910-2917` (`_parse_deadband`), reached only through `mcu plot export --deadband`.
- Scenario: `--deadband ramp=1_0` -> taken as 10, `wrote 2 rows`, rc 0; `ramp=1,ramp=2` -> the last wins silently.
- DRIVEN. (`inf`, `nan`, `1e400`, `0x10`, empty value, missing `=` and an unexported name are refused.)
- Class 22.
- Fix: an explicit decimal grammar (`[+-]?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?`) and refuse a repeated name.

### B-17 LOW: blank `--serial` values

- Where: `cli.py:357-360` (`if not device and not serial`).
- Scenario:
  - `mcu attach --serial ""` -> `error: give a device, or --serial SN`, although `--serial` was given.
  - `mcu attach --serial "   " --alias x` -> `attached x -> serial     (connecting; ...)`, rc 0, a port that can never connect.
- DRIVEN (port detached afterwards).
- Class 22 (parses but is not a value).
- Fix: strip, and refuse a blank serial with its own message.

### B-18 LOW: a new command against a daemon lacking its endpoint says only `Not Found`

- Where: `cli.py:1607` (`/lines/export`), `cli.py:1347` (`/sessions/{ref}/bundle`); no version gate on these paths.
- Scenario, v0.3.0 daemon: `mcu log export -o l.txt` (the plain form) -> `error: Not Found`, rc 1; `mcu session export 1 --bundle -o b` -> `error: Not Found`.
- DRIVEN.
- The gate's own message is right where it fires (`error: daemon 0.3.0 ignores --from/--to ... needs daemon 0.4.0 or newer`, pinned by `test_cli_r2026_09_12.py`).
- Class 53 (loud, so ruled exempt in 2026-09-12; the message is the defect).
- Fix: on a 404 from these routes, name the daemon version and the minimum, as the gate does.

## Sweeps

### Class 2 (text writes without explicit newline), CLI files

`grep -rn "open(" host/mcuscope/cli*.py | grep -v 'newline\|"rb"\|os.open'` -> 11 lines; `write_text(` -> 0.

- `cli_client.py:119, 135, 155, 209, 246`, `cli.py:1931`: exempt, `httpx.Client` opens.
- `cli_client.py:216`, `cli.py:2227`: exempt, binary `"wb"`.
- `cli.py:2246` (`Popen(`), `2296` (`webbrowser.open`), `cli_daemonctl.py:157` (comment): exempt, not file opens.

Opens carrying `newline=` (4): `cli.py:1531` `""`, `1637` `"\n"`, `cli_daemonctl.py:62` (read), `162`: comply.
stdout as a text sink (2): `cli.py:1517` complies (untranslated); `cli.py:1666` violates (B-7).

### Class 5 (argv hoisting)

No global option added. New value-taking subcommand options: `attach --serial`, `can dump --session`.
10 cases through `cli._hoist_global_opts`, all comply:

- `can dump --session 1 -p sim -n 1` hoists `-p sim`; `can dump --session -p sim` keeps `-p` as the value.
- `can dump --session 1 --json` hoists; `can dump --session --json` keeps it as the value.
- `attach --serial SN --json --alias x` hoists; `attach --serial --json` keeps it; `attach --serial SN -p sim` hoists; `_value_taking_opts` lists `--serial`.
- `wait --repeat-ms -p sim` and `plot export --deadband -p --json` keep the value position; `--url U can dump --session run-3 --json` hoists `--json`.

### Class 9 (exit-code contract)

`grep -c '^\s*raise\b\|^\s*except\b' host/mcuscope/cli.py` -> 59 sites.

- `95`: `--version` Exit 0, complies.
- `319, 415, 644, 751, 1716, 1719`: `BadParameter` to the usage arm, exit 1, comply (closed-stderr face is B-8).
- `864`: slow `--match` to die 1; `976`: bad `--match` to die 1; comply.
- `945, 954, 956, 1048, 1057, 1059`: `BaseException` cleanup and re-raise, comply.
- `994, 1026, 1084, 1088, 1874, 1906`: per-item or JSON mapping inside follows, comply.
- `1060-1061, 1545-1546`: BrokenPipe re-raised to the main arm (0), comply.
- `1062, 1532, 1547, 1638, 1651`: `OSError` to die 1, comply.
- `1064, 1078, 1082`: websockets exceptions to mapped dies, comply.
- `1095-1096, 1912-1913`: Ctrl-C in a follow, exit 0 per SPEC 4, comply.
- `1155, 1168, 1260`: wait 0/2, assert 0/1, comply (driven).
- `1455, 1461`: purge Exit 0, comply.
- `1518-1522`: stdout EPIPE to 0, complies.
- `1935`: InvalidURL to 3, complies.
- `2228, 2256`: warnings, no exit, comply.
- `2385`: daemon status 3, complies.
- `2697, 2700`: shutdown flush mapping, comply.
- `2752, 2761, 2776, 2778, 2789`: dispatcher arms, comply.
- `2754`: violates on a closed stderr (B-8).
- `2763, 2773`: non-EPIPE `OSError` re-raised to the crash handler, exempt by design.
- `2802`: entry point, exempt.

Exception types reaching `main()` unmapped: `ValueError` from `cli_output.py:259` (B-6).
Refusal order: B-12. Driven through the console script: 78 `--json` commands, 16 closed-stderr and unreachable cases.

### Class 10 (`--json` stdout purity)

`jsonsweep.py` over `cmds1.txt` (49) and `cmds2.txt` (29) = 78 commands, every subcommand plus each refusal in the diff.

- 72 printed exactly one document.
- 5 printed JSONL and are SPEC 4 exempt: `log export --limit 2`, `can dump -n 2`, `can dump -n 2 --session 1`, `tail -n 2`, `tail -n 2 --decode --changes`.
- 1 violates: `lines --limit 1 --from 9999-12-31T23:59`, empty stdout plus a traceback (B-6).

Outside the sweep, also violating: a mid-stream failure (B-5) and a usage error with stderr closed (B-8).
New print/write sites in the diff (`status` trimmed, `devices` header, `attach` shown, `plot channels` value, `wait` timeout text, `daemon start` warning, `_refuse_stdout_token`): the text branches go to stdout only outside JSON mode, and notes and warnings go to stderr; all comply.

### Class 13 (Windows sharing and encoding)

- `os.replace|os.rename` in `cli*.py`: 0 sites.
- `os.remove` (6), all comply:
  - `cli.py:1554` and `1659` close first (explicit close, `with fh:`); `cli_client.py:231`'s inner `with open` exits first.
  - `cli_daemonctl.py:138`, `167`, `cli.py:2370` act on closed pid and tmp files.
- `EINVAL|except BrokenPipeError` (11), all comply:
  - `cli_client.py:254`, `cli_output.py:54, 186, 201`, `cli.py:1060, 1518, 1545, 2697` rely on the boundary translation.
  - `cli_output.py:130`, `cli.py:2684, 2770` are comments.
- Redirected non-UTF-8 console, comply:
  - `PYTHONIOENCODING=ascii LC_ALL=C` over `log export` (plain, `--csv`, `--limit`, `--decode`), `lines`, `lines --json`, `tail`, with a `✓ é` marker.
  - rc 0 and valid UTF-8 each; cp1252 too; `-o` equals stdout.
- stdout against `-o` byte equality: 7 export forms equal on Linux; the Windows emulation fails for the paged form (B-7).

### Class 35 (an error write hijacking the exit code)

`closedstderr.py`: 10 error paths of the diff with stderr closed against attached, plus 6 against an unreachable daemon.

- All `die()` and `err()` paths comply:
  - `-o -`, both `attach` refusals, `--to -f`, `--csv --changes`, `changes requires decode`, `--bundle .DB` (1 and 1).
  - Both `wait` timeout forms (2 and 2) and the six unreachable forms (3 and 3).
- Violates: the click usage arm, 1 differs of 10 in the first batch and all 5 in a second batch (B-8).

### Class 46 (reading a field a newer daemon added)

43 backticked names added to SPEC since v0.4.0, each grepped as `["name"]` / `['name']` in `cli*.py`; subscript reads found:

- `pt['lines_rx']` (`cli.py:188`): complies, present at v0.1.0.
- `pt['target']` (`215`) and `pt['write_failures']` (`209`): comply, both behind `.get` checks.
- `r["raw"]` (`677, 717, 728, 1016-1020`) and `check[...]['raw']` (`1249, 1254`): comply, row field since v0.1.0.

All other hits are writes. `lines_trimmed`, `waited_ms` and `sends` use `.get` / `in`. Driven against a real v0.4.0 daemon: `status`, `wait` both forms, `can dump --session`, the decode exports and `plot channels` all rc 0 or their mapped code, no traceback.

### Class 53 (a bound sent as a parameter the peer may not declare)

`grep -n 'params\[\|params=\|params: dict' host/mcuscope/cli*.py` -> 53 lines; the sites are the 37 listed in the 2026-09-12 fix-diff, unchanged except the new `can dump --session` at `1797`.
Declared sets re-extracted from v0.1.0..v0.4.0 `server.py`; no query parameter was added to the server since v0.4.0 (only the `id_to` floor), and HEAD still reports `0.4.0`.

- `1797` `session -> /can/frames`: complies, declared since v0.1.0.
- `2122` `port -> /plot/export`: violates (B-2).
- `1809` `format`, `2124-2128` `decode/changes/deadband`, `550-552` and `1805-1807` and `2116-2118` `since_ts/until_ts`: comply, gated.
- `1801, 1846` `bus -> /can/frames`: violates against v0.1.x and v0.2.0 (declared from v0.3.0). Silent, and pre-existing.
- `1606` `format -> /lines/export`: exempt as loud, but the message is B-18.
- The remaining sites: comply per the 2026-09-12 list.

Body fields (the same mechanism, outside the sweep's grep): 17 `post/put/delete` sites.
`eol` (5 sites) and `repeat_ms` violate against v0.3.0 (B-3); `serial_number`, `send_mode`, `min_window_ms`, `session`, `last_ms` and the purge fields are declared since v0.1.0.

### Class 58 (help naming what nothing reads), CLI scope

- Flags per command: every `mcu <cmd> ... --flag` in `AI_GUIDE` and SPEC 4, resolved against the click tree (`guide_flags.txt`): 0 flags missing from their command in either text.
- Env vars named in `cli*.py` (5: `MCUSCOPE_URL`, `MCUSCOPE_TOKEN`, `MCUSCOPE_START_TIMEOUT`, `MCUSCOPED_CONFIG`, `MCUSCOPED_TOKEN`): all read, comply.
- `section.key` tokens in `cli*.py`: 0 (one `ports.get` is code). `mcuscoped --host`: declared, complies.
- Runtime text: the truncation note violates (B-11); the `attach --alias` help "or 'board' for a URL" complies (driven through `_derive_alias` over 11 inputs).
- `test_ai_guide_names_every_flag` checks `opt in AI_GUIDE` over the whole text, so a flag documented under the wrong command passes it. The per-command check above closes that for this round only.

## Decisions for the owner

- A 503 for the subscriber cap maps to exit 3 ("daemon unreachable").
  - Driven: 260 WebSockets open, then `mcu wait --match x --timeout 500` -> `error: too many subscribers (max 256)`, rc 3; `mcu assert` the same.
  - SPEC 3.4 codes only the shutdown 503 as 3; an agent following the unreachable hint runs `mcu daemon start` and gets `daemon already running`.
  - The 2026-09-12 fix-diff ruled the blanket mapping coherent. Keep, or map the cap to 1?
- `mcu daemon start --config typo.toml` warns and starts on defaults, which means the default capture db.
  - Driven with XDG dirs isolated: warning, `started`, rc 0, `db_path .../xdg/data/mcuscope/capture.db`. Refuse a named config that does not exist?
- The version gate compares against `0.4.0` and HEAD also reports `0.4.0`.
  - Nothing added since needs a gate today, but any parameter added before the version bump is ungateable. Bump first in each release cycle?
- `--deadband ramp=-1` is taken as its magnitude (`abs`), so a sign typo is silently a different band. Refuse negatives?

## The two questions

1. Least confident, rechecked:
   - B-7 rests on an emulated Windows stdout; a real Windows run is still owed.
     - Rechecked: under the same wrapper the streamed path's `reconfigure(newline="")` took the CR count to 0, and the paged path kept 1115.
   - B-2 and B-3 were driven against the real v0.3.0 code, not a renamed parameter.
     - It ran from a `git archive` extract on the current venv's dependencies, so newer dependencies than v0.3.0 shipped with are the residual.
   - B-4's root-owned `/dev/null` case is reasoned only; the symlink and FIFO cases show the removal is path-blind.
2. What we had not thought about:
   - The in-band JSONL error line (B-5): every earlier class 49 and class 10 drive checked the file or the exit code, never the last stdout line after a mid-stream death.
   - Body fields as the mirror of class 53 (B-3): the registry sweep greps only query parameters.
   - The class 58 contract test matches a flag anywhere in the guide text, not under its own command.
