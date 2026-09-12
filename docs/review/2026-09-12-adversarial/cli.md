# CLI leg, adversarial review, 2026-09-12

HEAD `c15b7c6` (confirmed at the start of the leg and again in the worktree).
Scope: the CLI side of `git diff fd5d63d..HEAD` over `host/mcuscope/cli.py`, `cli_output.py`, `host/tests/test_cli_export.py`, `test_cli_ux.py`, `test_cli_contract.py`, plus SPEC section 4, `README.md`, `host/README.md` and `CHANGELOG.md` for the documented behaviour.
Nothing was fixed; the main tree is untouched and the worktree was restored and removed.

Driven against a throwaway stack: `mcuscoped --sim --config /tmp/rev-2026-09-12/cli/cfg.toml --port 18602` with its own `db_path`, under `XDG_DATA_HOME`/`XDG_CONFIG_HOME`/`XDG_CACHE_HOME` beneath `/tmp/rev-2026-09-12/cli/`, and `mcu --url http://127.0.0.1:18602 ...` through the installed console script `host/.venv/bin/mcu`.
The real capture and the default config were never touched.
87 scripted commands plus 34 ad-hoc ones; the capture grew from 5 k to 77 k lines over the leg, so row counts move between runs and only the frozen-window comparisons below are exact.

Scripts and logs:

- `/tmp/rev-2026-09-12/cli/drive.sh` and `drive.log` - the 87-command matrix (sections A to G below).
- `/tmp/rev-2026-09-12/cli/mutate.py` - the 12 mutations, anchors all checked before any write, each restore asserted.
- `/tmp/rev-2026-09-12/cli/mutate_full.py` - the two survivors re-run against the full 1465-test suite.
- `/tmp/rev-2026-09-12/cli/cfg.toml`, `daemon.log`, `out/` - config, daemon stderr, every exported artefact.

## Findings

| id | sev | where | claim | how verified |
|---|---|---|---|---|
| C1 | MED | `cli.py:1399-1402` (`measure` in `_stream_export`), consumed at `1496-1500` | `log export -o` reports a **physical newline count**, not a row count, on the new streamed path, while the paged path of the same command (`cli.py:1534-1536`) counts rows. One captured row whose `raw` contains a newline is reported as two lines by `--csv`/text and as one by `--limit`. SPEC 4 says "Either way `-o FILE` prints `wrote N lines to FILE`", and with `--json` this number is `{"lines": N}`, which is what an agent acts on. | Driven. `mcu mark "$(printf 'multi\nline marker')"` stores raw `'multi\nline marker'` (one row, id 20682, confirmed in SQLite). Then `mcu log export --match 'line marker' -o f` -> `wrote 2 lines`; `mcu log export --match 'line marker' --limit 5 -o f` -> `wrote 1 lines`; `--json --limit 5` -> `{"lines": 1}`. Same window, same command, two answers. |
| C2 | MED | option at `cli.py:1956` (`--deadband`), parsed at `server.py:2845` `bands[name] = abs(float(value))` | `--deadband NAME=V` is passed through verbatim and parsed with a bare `float()`, so `inf`, `nan` and other scripts' digits are accepted (class 22's second face: "parses but is not a quantity"). `--deadband ramp=inf` collapses the whole export to its first row and exits 0 - the header-only-at-exit-0 artefact SPEC 9.2 explicitly forbids for this endpoint. `cli_output.finite_option` exists in the tree for exactly this and is not reachable from the option. | Driven: `mcu plot export --names ramp --decode --changes --deadband ramp=inf -o u3.csv` -> `wrote 1 rows`, rc 0; `ramp=nan` -> `wrote 1 rows`, rc 0; `ramp=٣` (U+0663) -> `wrote 12235 rows`, rc 0, silently taken as 3. A sane band (`ramp=0.05`) on the same data writes 12235. |
| C3 | MED | `cli.py:580-588` (`_clock_bounds`), `cli.py:522`, `1682`, `1984` | The diff replaced the client-side `id_to` computation for `--to` with an `until_ts` **query parameter**. FastAPI ignores query parameters it does not declare, so against a daemon older than 0.4.0 the upper bound silently disappears and `--to` exports the whole capture at exit 0. The old code worked against any daemon. `--csv` and `--bundle` fail loudly there (404 on the new routes); `--to` is the one silent regression, and it hits `lines`, `tail`, `log export`, `can dump` and `plot export`. | Driven two ways. (a) `curl "…/lines?limit=1&totally_bogus_param=42"` -> 200, the param ignored. (b) In the worktree, the CLI's param renamed to a name no daemon declares (a pre-0.4.0 daemon by construction), same command, same second: bound honoured `wrote 70654 lines`, bound ignored `wrote 76967 lines`, rc 0 and no warning either way. Worktree restored, `git diff --stat` empty. |
| C4 | LOW | `cli.py:1265` `if out_file.endswith(".db")` | The `--bundle` guard against writing a zip under a `.db` name is case-sensitive, so `-o run.DB` is accepted - on Windows, where `.DB` and `.db` name the same file, which is the OS the cross-platform mandate exists for. `os.path.splitext` on the next line has the same character-set assumption. | Driven (drive.log #72): `mcu session export 1 --bundle -o out/s4.DB` -> `wrote 646688 bytes to …/s4.DB`, rc 0, and `file` reports a Zip archive. `-o s1.db` is refused rc 1. |
| C5 | LOW (class 19) | `docs/SPEC.md:1055` (SPEC 4 `plot export` row) vs `docs/SPEC.md:833` (9.2) and `cli.py:1979-1994` | The SPEC 4 table spells the bounds as alternatives, `[--session S \| --last-ms MS \| --from T --to T]`, while SPEC 9.2 says "Every bound given is applied, so `until_ts` intersects `session=`, `id_to=` and `last_ms=` rather than replacing any of them" and the code sends all of them. Two descriptions of one thing; the table is the one an agent reads first. | Driven: `mcu plot export --names ramp --session 1 --last-ms 5000 --from 00:00 --to 23:59 -o x1.csv` -> `wrote 101 rows`, rc 0, no refusal. Same for `log export` with all four. |
| C6 | LOW (class 29) | `cli.py:1422` `open(out_file, "w", encoding="utf-8", newline="")` | Nothing in the repo pins `newline=` on the export file open. Dropping it (mutation M5) left **1465 passed, 1 skipped** - the whole suite green. The comment directly above says the argument exists so "the body reaches the file byte for byte", so the only guard against a Windows CRLF export, and against the `bytes` figure the same function reports, is unprotected on the one platform where it does anything. The paged path's sibling `newline="\n"` (`cli.py:1523`) is equally unpinned. | Mutation M5, run against the three CLI test files (86 passed) and then against the full suite (1465 passed). Class 13's own note applies: "a POSIX-only test passes either way ... emulate the rule locally". |
| C7 | LOW | `cli.py:1487-1500` (the `/lines/export` path) | `mcu log export --csv --session <typo> -o run.csv` writes a header-only CSV and prints `wrote 0 lines`, exit 0. SPEC 9.2 reasons about exactly this shape for `/plot/export` ("a header-only CSV at exit 0 cannot be told from a mistyped name") and refuses it there; the new `/lines/export` CSV path reintroduces it on a second surface. `mcu session export <typo>` on the same name is a clean `error: no such session`, so the CLI already has the refusal text. | Driven (drive.log #29): `--session auto-x` -> `wrote 0 lines`, rc 0, 28-byte file holding only `id,ts,port,dir,chan,seq,raw`. Non-CSV and `--limit` forms are silently empty at rc 0 too, and `plot export --session <typo>` writes a header-only CSV at rc 0. |
| C8 | nit | `cli.py:1486` | The `--csv` refusal message names only `--limit` and `--decode`, but the guard (`paged = bool(limit or decode or changes or names)`, `cli.py:1484`) also fires for `--changes` and `--names`, so two of the four refusals report an option the user did not pass. | Driven (drive.log #18, #19): `--csv --changes` and `--csv --names vbat` both print `--csv exports the whole window; it does not take --limit or --decode`. |
| C9 | nit | `cli.py:1265-1268` | `mcu session export 1 --bundle -o -` writes a file literally named `-.zip`: the extensionless branch appends `.zip` to the conventional stdout token. Neither supported nor refused. | Driven (drive.log #78): `wrote 678945 bytes to -.zip`, rc 0, and a file named `-.zip` on disk. |
| C10 | LOW (class 2/13, reasoned) | `cli.py:1408` `sys.stdout.write(chunk)` vs `cli.py:1422` | The stdout arm of `_stream_export` has no newline guard while the file arm does, so on Windows `mcu log export --csv > run.csv` writes CRLF where `mcu log export --csv -o run.csv` writes LF. Two spellings of the same export, different bytes and different sizes. `_stdio.widen_stdout_encoding` reconfigures `encoding=` only, never `newline=`. The shape pre-existed in `plot export`; the diff extends it to `log export --csv` and `can dump --csv`. | Read (`_stdio.py:177-198`, no `newline=` anywhere) and the bodies confirmed LF-only on Linux (`e1.csv`, `c1.csv`, `t5.csv`: CR count 0). Not driven - no Windows machine on this leg; see Q1. |

## Ruled out (driven or read; listed so nobody re-checks)

- **`--from` after `--to`**: refused as a usage error on all five commands that take the pair (`lines`, `log export`, `can dump`, `plot export`, `tail`), exit 1, and with `--json` the error object still lands on stdout (`{"error": "Invalid value for --to: --from 20:00 is after --to 19:00", "exit_code": 1}`). drive.log #1-#5.
- **`--from`/`--to` grammar (class 22)**: `_CLOCK_RE` (`cli_output.py:229`) is explicit `[0-9]`, not `\d`, so Arabic-Indic digits are refused; a bare epoch number, a relative `-5m` and an ISO string with a `+09:00` offset are all refused with the documented grammar in the message. Matches SPEC 4's `[YYYY-MM-DDT]HH:MM[:SS[.fff]]` exactly. drive.log #6-#9.
- **`--from == --to`**: empty, exit 0. Correct: `since_ts` is exclusive and `until_ts` inclusive per SPEC 9.2, so the window is genuinely empty, not a bug.
- **`--id` token grammar**: the CLI parses nothing, it joins and forwards; the daemon's grammar refuses `٣`, `+100`, `1_00`, `" 100"`, `"100 "`, `""`, `100-200` and `zz` with `error: bad can id: <token>` exit 1, and `0xFFFFFFFF` / `0x20000000` with `error: can id out of range`. A duplicate (`-i 100 -i 100`) and a comma inside one token (`-i 100,200`) both work. `-i 256` selects 0x256 (hex by default, as SPEC says) and correctly returns nothing. `0X100` is accepted, tolerant but harmless. drive.log #37-#46, plus the class 22 probe block.
- **Class 49, the partial-file guard**: `_stream_export`'s `finally` removes the file on every non-completion. Driven for a daemon 400 (`can dump --csv -i zz -o c5.csv` and every failed `plot export -o`: no file left), for a mid-download SIGINT (`session export --bundle`, killed at 0.9 s of a 2.3 s transfer: rc 1, `int1.zip` absent), and for a full disk (`-o /dev/full` -> `cannot write /dev/full: [Errno 28]`, rc 1). Mutation M4 caught.
- **The removal guard arming after the open**: `-o` onto a directory leaves the directory intact (`cannot write …: [Errno 21] Is a directory`, rc 1) and `-o` into a missing directory exits 1 with no file created. The `die` for a failed open sits outside the `try/finally`, as its comment claims. drive.log #25-#27, #49, #50, #64, #65, and `test_an_export_keeps_a_file_it_could_not_open`.
- **Broken pipe**: `mcu log export --csv | head -2` exits 0 with two lines and nothing on stderr, per SPEC 4.
- **`-o` onto an existing file**: truncated then rewritten; on a failure the guard removes it. Removing a file the command did truncate is the class 49 contract, not a violation.
- **Exported row counts against the daemon**: on a frozen window (`--to` 10 s in the past) `log export` text, `--csv` and `--json` all reported 17308 and all three files held 17308 data rows; SQLite `select count(*) from lines where ts<=…` returned 17308. `can dump --csv` reported 3621 against a SQLite join returning 3621. The CSV header subtraction is right, and `until_ts` really is inclusive. 3909 of those rows carry a comma or a quote and round-trip through `csv.DictReader` unchanged.
- **`--json` purity (class 10)**: every new `--json` invocation that is not a documented JSONL command parsed as exactly one document (`json.load`), including all nine refusals. The three JSONL forms that fail the one-document check are `log export --json`, `can dump --json` and `tail -f`, which SPEC 4 exempts by name.
- **Exit codes (class 9)**: every refusal in the diff exits 1 and every success 0; no traceback reached the user on any of the 121 commands. `--bus 99` is typer's own range check (exit 1, usage text).
- **Class 5, argv hoisting**: the diff adds no global option. Three new value-taking subcommand options (`can dump -i/--id`, `can dump -o/--out`, `plot export --deadband`) and the hoister's value guard sees all three: `mcu can dump -i 100 -p sim -n 2` hoists `-p` correctly, `mcu --json can dump -i 100` and `mcu can dump -i 100 --json` both enter JSON mode, and `-i --json`, `--deadband --json`, `-o --json` each take `--json` as the option's value (`error: bad can id: --json`; a file named `--json`). That last face is the pre-existing documented rule ("a token in a value position is the value") already recorded for `--match --json` in the 2026-09-07 leg, so it is consistent, not new.
- **Class 46, fields read from a daemon body**: the diff reads **zero** new response fields - all 11 `params[...]` sites are writes. The skew hazard here runs the other way and is C3.
- **Class 17**: `bytes` is now counted from what was actually encoded and written rather than echoed from the request (`plot export -o --json` used to call `os.path.getsize`); `wrote N` is derived from the same stream. Reports the result, not the request. The `lines` figure is still wrong for a different reason (C1).
- **Class 27/28, the new tests**: `test_cli_export.py` drives the real `Stack` (sim plus daemon in process), not a hand-rolled double, so there is nothing gentler than the shipped thing to diverge. Zero `raise AssertionError`, zero `except Exception` and zero bare `except` in the file; every test asserts both the exit code from `cli.main()` and text unique to the path.
- **Class 30**: one `subprocess` site in the three test files under review and it is not a test-runner wrapper.
- **Class 33**: `test_cli_export.py` spawns no child and takes every path through `tmp_path` plus the autouse `_isolated_user_dirs`; nothing reads a real platformdirs location.
- **`Client.download`'s removal guard**: mutation M11 survives the three CLI files but is caught by the full suite (`tests/test_cli.py::test_session_export_removes_a_partial_file`). Covered elsewhere, not a gap. Note that `test_a_bundle_the_daemon_refuses_leaves_no_file` does **not** pin it: on a 400 the file is never opened, so `started` stays False and the mutant passes that test.
- **`--limit 0` on `log export`**: falsy, so it takes the streamed path, which is what "every matching row" means. Consistent with the default.
- **`can dump --csv` ignoring `-n`**: silently, as SPEC 4 documents ("no `-n` limit"). Driven with `-n 2` returning 1269 frames.
- **`--csv`/`--json` and `--csv`/`-f` refusals**: both exit 1 on both commands, with the JSON error object present in JSON mode.
- **`session export --bundle` extension handling**: an extensionless `-o out/s3` becomes `out/s3.zip` and `splitext` is basename-scoped, so a dotted directory in the path does not confuse it.
- **Timing** (25478-line capture, best of one): `log export -o` 0.45 s, `log export --csv -o` 0.49 s, `can dump --csv -o` 0.33 s, `plot export -o` 0.38 s, `plot export --decode --changes -o` 0.54 s. Two over a second: `log export --decode -o` at **1.43 s** (the paged path, which the diff deliberately left in place for decoding) and `session export --bundle` at **2.35 s** (it builds a zip holding the whole DB). Both are explained by what they do; the streamed path is roughly 3x faster than the paged one it replaced for the plain case, which was the point of the change.

## Drive log

Full matrix in `/tmp/rev-2026-09-12/cli/drive.log`. Every command is prefixed `mcu --url http://127.0.0.1:18602`.

| # | command | rc | first line of output |
|---|---|---|---|
| 1 | `log export --from 20:00 --to 19:00` | 1 | (stderr) `Usage: mcu log export [OPTIONS]` |
| 2 | `--json log export --from 20:00 --to 19:00` | 1 | `{"error": "Invalid value for --to: --from 20:00 is after --to 19:00", "exit_code": 1}` |
| 6 | `log export --from 1757000000 --limit 1` | 1 | (stderr) usage, `expected HH:MM[:SS[.mmm]] or YYYY-MM-DDTHH:MM:SS` |
| 8 | `log export --from -5m --limit 1` | 1 | (stderr) same grammar refusal |
| 12 | `log export --from 00:00 --to 00:00` | 0 | (empty) |
| 14 | `log export --csv --limit 0` | 0 | `id,ts,port,dir,chan,seq,raw` (5293 lines) |
| 15 | `--json log export --csv` | 1 | `{"error": "--csv and --json are two output formats; pick one", "exit_code": 1}` |
| 16 | `log export --csv --limit 5` | 1 | (stderr) `--csv exports the whole window; it does not take --limit or --decode` |
| 19 | `log export --csv --names vbat` | 1 | (stderr) same message, `--names` not named (C8) |
| 20 | `log export --csv -o e1.csv` | 0 | `wrote 5388 lines to …/e1.csv` |
| 23 | `--json log export -o e4.txt` | 0 | `{"file": "…/e4.txt", "lines": 5460, "bytes": 709365, "truncated": false}` |
| 24 | `--json log export` | 0 | JSONL, 5496 lines (SPEC 4 exempt) |
| 25 | `log export -o <missing dir>/x.txt` | 1 | (stderr) `cannot write …: [Errno 2] No such file or directory` |
| 27 | `log export -o <a directory>` | 1 | (stderr) `cannot write …: [Errno 21] Is a directory` |
| 29 | `log export --csv --session auto-x -o e5.csv` | 0 | `wrote 0 lines to …/e5.csv` (C7; 28-byte header-only file) |
| 32 | `can dump --csv` | 0 | `id,ts,tick_ms,bus,can_id,ext,rtr,dlc,data` (1187 lines) |
| 33 | `can dump -o c1.csv` | 0 | `wrote 1193 frames to …/c1.csv` |
| 34 | `can dump --csv -f` | 1 | (stderr) `--csv does not follow` |
| 38 | `can dump -i 100 -i 200 -n 3` | 0 | `14:09:47.925  id=100 - dlc=4 data=00000235` |
| 40 | `can dump -i "" -n 3` | 1 | (stderr) `error: bad can id: ` |
| 44 | `can dump -i 100-200 -n 3` | 1 | (stderr) `error: bad can id: 100-200` |
| 45 | `can dump -i 99999 -n 3` | 0 | (empty; 0x99999 is a valid extended id with no frames) |
| 47 | `can dump --csv -n 2 -o c3.csv` | 0 | `wrote 1269 frames …` (`-n` ignored, as documented) |
| 51 | `can dump --csv -i zz -o c5.csv` | 1 | (stderr) `error: bad can id: zz`; **no c5.csv on disk** |
| 54 | `plot export --names vbat --changes -o p3.csv` | 1 | (stderr) `error: changes requires decode` |
| 56 | `plot export --names vbat --deadband vbat=0.05 -o p4.csv` | 1 | (stderr) `error: deadband requires changes` |
| 59 | `plot export … --deadband garbage -o p7.csv` | 1 | (stderr) `error: deadband names no exported channel: garbage` |
| 62 | `--json plot export --names vbat` | 1 | `{"error": "error: no such plot channel: vbat; see /plot/channels", "exit_code": 1}` |
| 68 | `session export 1 --bundle -o s1.db` | 1 | (stderr) `--bundle writes a zip, not a .db` |
| 70 | `session export 1 --bundle -o s2.zip` | 0 | `wrote 633271 bytes to …/s2.zip` |
| 71 | `session export 1 --bundle -o s3` | 0 | `wrote 639539 bytes to …/s3.zip` |
| 72 | `session export 1 --bundle -o s4.DB` | 0 | `wrote 646688 bytes to …/s4.DB` (C4) |
| 73 | `--json session export 1 --bundle -o s5.zip` | 0 | `{"file": "…/s5.zip", "bytes": 653949}` |
| 75 | `session export nosuchsession --bundle -o s7.zip` | 1 | (stderr) `error: no such session: nosuchsession` |
| 78 | `session export 1 --bundle -o -` | 0 | `wrote 678945 bytes to -.zip` (C9) |
| 79 | `can dump -i 100 -p sim -n 2` | 0 | frames; `-p` hoisted past the new `-i` value |
| 82 | `can dump -i --json -n 2` | 1 | (stderr) `error: bad can id: --json` (value guard, documented) |
| 84 | `log export -o --json` | 0 | `wrote 7349 lines to --json` (same rule, a file named `--json`) |
| ad-hoc | `plot export … --deadband ramp=inf -o u3.csv` | 0 | `wrote 1 rows to …/u3.csv` (C2) |
| ad-hoc | `plot export … --deadband ramp=nan -o u2.csv` | 0 | `wrote 1 rows to …/u2.csv` (C2) |
| ad-hoc | `plot export … --deadband ramp=٣ -o u1.csv` | 0 | `wrote 12235 rows to …/u1.csv` (C2) |
| ad-hoc | `log export --match 'line marker' -o nl3.txt` | 0 | `wrote 2 lines` for one captured row (C1) |
| ad-hoc | `log export --match 'line marker' --limit 5 -o nl2.txt` | 0 | `wrote 1 lines` for the same row (C1) |
| ad-hoc | `log export --csv -o /dev/full` | 1 | (stderr) `cannot write /dev/full: [Errno 28] No space left on device` |
| ad-hoc | `log export --csv \| head -2` | 0 | header plus one row, stderr empty |
| ad-hoc | `session export 1 --bundle -o int1.zip` + SIGINT at 0.9 s | 1 | `interrupted`; no `int1.zip` on disk |
| ad-hoc | `plot export --names ramp --session 1 --last-ms 5000 --from 00:00 --to 23:59 -o x1.csv` | 0 | `wrote 101 rows` (bounds intersect, C5) |
| ad-hoc | `log export --to <60 s ago> -o sk1.txt` | 0 | `wrote 70654 lines` (bound honoured) |
| ad-hoc | same, param name an older daemon does not declare | 0 | `wrote 76967 lines` (bound silently gone, C3) |

## Sweep verdicts

Site counts come from enumerating the 229 added lines of `cli.py` and `cli_output.py` in `git diff -U0 fd5d63d..HEAD`, matched by the pattern named with each class. Every site is accounted for.

### Class 2 / 13 (text writes without explicit newline; Windows sharing and encoding)

New `open(` sites: **1**. New `os.remove`/`os.replace`/`os.rename` sites: **1**.

- `cli.py:1422` `open(out_file, "w", encoding="utf-8", newline="")` - **complies**: explicit encoding and explicit `newline=`. Unpinned by any test, which is C6.
- `cli.py:1445` `os.remove(out_file)` inside `contextlib.suppress(OSError)` in the `finally` - **complies**: `fh.close()` on the line above precedes it, so the handle is closed before the unlink, which is the rule Windows enforces and POSIX does not.
- Beyond the `open(` grep, one further write site: `cli.py:1408` `sys.stdout.write(chunk)` - **violates** the spirit of the class (C10): no newline guard on the redirect path, so the same export is CRLF there and LF via `-o` on Windows. Reasoned, not driven.

### Class 5 (argv hoisting)

Global option surface changed at **0** places. New value-taking subcommand options: **3** (`can dump -i/--id`, `can dump -o/--out`, `plot export --deadband`); new boolean options: **4** (`--bundle`, `log export --csv`, `can dump --csv`, `plot export --decode`, `--changes` - five with `--changes`).

- `can dump -i/--id` - **complies**: `mcu can dump -i 100 -p sim -n 2` hoists `-p` past the value; `-i --json` keeps `--json` as the value, the documented rule.
- `can dump -o/--out` - **complies**: same guard, `log export -o --json` writes a file named `--json` rather than stealing the flag.
- `plot export --deadband` - **complies**: `--deadband --json` takes `--json` as its value.
- The four/five new booleans - **exempt**: no value position to guard.
- Hoist test matrix extension: **gap, nit** - `test_cli.py`'s hoist matrix was not extended for the three new value-taking options; I drove all three by hand instead.

### Class 9 (CLI exit-code contract)

`raise`/`die`/`except`/`typer.Exit` sites in the diff: **16** (two of the 16 grep hits are comments naming the mechanism, not sites).

1. `cli.py:1266` `die("--bundle writes a zip, not a .db", 1)` - complies (driven, rc 1, JSON object present).
2. `cli.py:1409-1413` stdout `except BrokenPipeError` -> `_silence_stdout()` + `Exit(0)` - complies (driven: `| head`, rc 0).
3. `cli.py:1423-1424` `except OSError` on the open -> `die(..., 1)` - complies (driven: missing dir, directory).
4. `cli.py:1436-1437` `except BrokenPipeError: raise` - complies (handled in `main()`, rc 0).
5. `cli.py:1438-1439` `except OSError` on the write/close -> `die(..., 1)` - complies (driven: `/dev/full`, rc 1).
6. `cli.py:1481` `--csv` + `--json` refusal - complies (rc 1, JSON object).
7. `cli.py:1486` `--csv` + paged refusal - complies (rc 1); message wording is C8.
8. `cli.py:1667` `--csv does not follow` - complies (rc 1).
9. `cli.py:1669` `can dump --csv` + `--json` refusal - complies (rc 1, JSON object).
10. `cli.py:1975` `changes requires decode` - complies (rc 1, and the `error: ` prefix matches `Client.fail`'s, so the client-side and daemon-side refusals read alike as the comment claims).
11. `cli.py:1977` `deadband requires changes` - complies (rc 1).
12. `typer.BadParameter` from `_clock_bounds` (one site, reached from four commands) - complies: mapped through `USAGE_ERRORS` to rc 1 with the JSON object, driven on all five commands.
13. Uncaught by the diff but reachable through it: `typer.Exit` from `Client.fail` inside `_stream_export` - complies (rc 1, and the `finally` still removes the file).

No traceback reached the user on any of the 121 commands driven.

### Class 10 (`--json` stdout purity)

Sites in the diff writing to a stream: **7**.

1. `cli.py:1408` `sys.stdout.write(chunk)` - **exempt**: the streamed body is the command's output; in `--json` mode the format is `jsonl`, which SPEC 4 exempts for `log export` and `can dump` by name.
2. `cli.py:1416` `sys.stdout.flush()` - complies (no bytes of its own).
3. `cli.py:1498` `out_json({"file", "lines", "bytes", "truncated"})` - complies (driven, one document).
4. `cli.py:1500` `print(f"wrote {count} lines …")` - complies: text mode only; the `--json` arm returns first.
5. `cli.py:1687` `print(f"wrote {…} frames …")` - complies: `can dump --csv` refuses `--json`, so this line is unreachable in JSON mode.
6. `cli.py:2017` `out_json({"file", "rows", "bytes"})` - complies (driven).
7. `cli.py:2019` `print(f"wrote {rows} rows …")` - complies (text only).

Every `--json` invocation in the matrix that is not a documented JSONL command parsed as exactly one document, including all nine refusals.

### Class 17 (reported value is the request, not the result)

Reported values in the diff: **4** (`lines`, `rows`, `bytes`, `truncated`).

- `bytes` - complies, and is an improvement: counted from what was encoded and written, where `plot export -o --json` previously called `os.path.getsize`.
- `rows` (`can dump`, `plot export`) - complies: derived from the written stream, and the CSV bodies have no multi-line field.
- `truncated: False` on the streamed path - complies: `/lines/export` has no cap, so false is the result, not an echo.
- `lines` (`log export`) - **violates**: it reports newlines in the body, not rows the daemon returned. C1.

### Class 19 (two engines validating one thing)

Duplicated checks in the diff: **3**.

- `changes requires decode` / `deadband requires changes`, refused client-side (`cli.py:1975-1977`) and daemon-side (`server.py:1812-1814`) - **complies**: same words, and `Client.fail`'s `error: ` prefix makes the two indistinguishable to a caller. Verified by removing `--decode` and comparing both refusal texts.
- `--from` after `--to`, refused client-side (`cli.py:584-588`) and daemon-side (`_check_window`) - **complies**: the comment says so explicitly and both produce rc 1.
- The row count, computed two ways for one command (`measure()` vs the paged loop's `count += 1`) - **violates**: two implementations of one number that disagree. C1.
- SPEC 4's table row vs SPEC 9.2 on whether the bounds are alternatives - **violates**: C5.

### Class 22 (a stdlib predicate standing in for a wire grammar)

Sites in the diff where a value from argv is tested or coerced: **3**, plus **2** values forwarded to a daemon-side parser.

- `cli.py:1265` `out_file.endswith(".db")` - **violates**: a case-sensitive substring test standing in for "is this a database filename", leaky on a case-insensitive filesystem. C4.
- `cli.py:1267` `os.path.splitext(out_file)[1]` - **complies** in practice (basename-scoped, and the `-` case is C9's nit rather than a grammar hole).
- `cli.py:1484` `bool(limit or decode or changes or names)` - **exempt**: every operand is already a typed click value, not a wire token.
- `--id` tokens forwarded to the daemon - **complies**: the daemon's grammar refuses `٣`, `+100`, `1_00`, leading and trailing space, the empty token and an out-of-range id. Driven with class 22's own discriminating input.
- `--deadband` values forwarded to the daemon - **violates**: `server.py:2845` is a bare `abs(float(value))`, so `inf`, `nan` and `٣` are all accepted. C2.
- `parse_clock` / `_CLOCK_RE` (unchanged by the diff but newly reached from `plot export` and `can dump`) - **complies**: explicit `[0-9]`, not `\d`.

### Class 27 (a double gentler than the thing it stands in for)

Doubles introduced by the new tests: **0**. `test_cli_export.py` drives the real in-process `Stack` (simulator plus daemon plus store) and the real `cli.main()`. Nothing to diverge. Complies.

### Class 28 (an assertion the test's own guard swallows)

`raise AssertionError` in the three test files: **0**. `except Exception` / bare `except` in test bodies: **0**. `pytest.raises(typer.Exit)` sites: **0** - the file uses `rc = cli.main([...])` and asserts the integer, which asserts the code by construction. Complies.

### Class 29 (the negative is never asserted)

Guards added by the diff: **11**. Asserted negatives: the `--csv`/`--json` refusal on both commands, the `--csv`/paged refusal (parametrised over all four options), `--csv does not follow`, `-o` implies `--csv` and still refuses to follow, `changes requires decode`, `deadband requires changes`, `--bundle` refusing `.db`, `-n` being ignored under `--csv`, the partial file being absent after a refusal, a file the command could not open surviving, and an empty window producing an empty file.

Not asserted: **2**.

- `newline=` on the export open - C6 (mutation M5 survives the full suite).
- The case-insensitive form of the `.db` refusal - C4 (no test drives `-o run.DB`).

### Class 30 (a wrapper trusting an external runner's exit code)

`subprocess.run` sites in the three test files whose assertion is `returncode`: **0** test-runner wrappers (the one `subprocess` import in `test_cli_export.py` is not one, and `test_cli_ux.py`'s eight are daemon spawns with their own output assertions). Complies.

### Class 33 (a test inheriting the user's real environment)

Tests in the new file that spawn a child: **0**. Every test takes `tmp_path` for its output and runs under the autouse `_isolated_user_dirs` fixture; the `Stack` fixture owns its own db. Complies. Separately, this leg's own daemon ran under `--config` on a throwaway TOML with its own `db_path` and its own `XDG_*` roots, per the standing rule.

### Class 35 (an error-path write hijacking the exit code)

Error-path writes in the diff: **9**, all `die(...)`. Every one routes through `cli_output.err()` -> `err_write()`, the guarded boundary that suppresses the write's own failure and repoints the stream at devnull. No new bare `print(..., file=sys.stderr)` anywhere in the diff. Complies.

### Class 46 (a client reading a field a newer daemon added)

Response fields newly **read** by the CLI in this diff: **0**. All 11 `params[...]` hits are request writes.

The class's shape is present in mirror image and is C3: the CLI now **sends** `until_ts`, and a daemon that does not declare the parameter ignores it rather than refusing, so the bound silently disappears instead of raising a `KeyError`. The registry entry covers reads only; if this round wants the sending side covered, that is the sweep to add.

### Class 49 (a streamed-to-file export left partial)

`open(out_file, "w")` sites in the CLI after the diff: **3** (`_stream_export:1422`, the paged `log export` path `:1523`, `Client.download`'s `open(out_file, "wb")`).

- `_stream_export:1422` - **complies**: the `finally` removes the file unless `ok` was set, `ok` is set only after a successful `close()`, and the guard is armed after the open. Driven for a daemon 400, a mid-download SIGINT and a full disk; mutation M4 caught.
- The paged path `:1523` - **complies** (pre-existing, unchanged by the diff, mutation not needed).
- `Client.download` - **complies**, and is the bundle path's guard. Pinned by `test_cli.py::test_session_export_removes_a_partial_file`, not by the new file's `test_a_bundle_the_daemon_refuses_leaves_no_file` (see the ruled-out list).

## Mutations

Driver `/tmp/rev-2026-09-12/cli/mutate.py`: all 12 anchors checked unique before any file was written, each run restored the file from a byte copy and asserted the restore, and the worktree was `git diff --stat`-clean afterwards. Test set: `test_cli_export.py`, `test_cli_ux.py`, `test_cli_contract.py` (86 tests baseline, green).

| id | mutation | caught |
|---|---|---|
| M1 | `log export`: drop the `--csv`/`--json` refusal | yes (`test_log_export_csv_and_json_refuse_each_other`) |
| M2 | `log export`: swap the `--csv`+paged refusal exit code 1 -> 2 | yes (`test_log_export_csv_refuses_the_paged_options[extra0]`) |
| M3 | `session export`: drop the `--bundle` `.db` refusal | yes (`test_session_export_bundle_refuses_a_db_name`) |
| M4 | `_stream_export`: drop the partial-file removal | yes (`test_a_refused_export_leaves_no_partial_file`) |
| M5 | `_stream_export`: drop `newline=""` from the file open | **no** - 86 passed; re-run against the full suite: **1465 passed, 1 skipped**. C6 |
| M6 | `log export --csv`: stop subtracting the CSV header | yes (`test_log_export_csv_round_trips_a_comma_and_a_quote`) |
| M7 | `_lines_params`: stop sending `until_ts` | yes (`test_to_in_the_past_excludes_newer_rows`) |
| M8 | `can dump`: `-o` no longer implies `--csv` | yes (`test_can_dump_out_file_implies_csv_and_still_refuses_to_follow`) |
| M9 | `plot export`: drop the client-side `changes requires decode` refusal | yes (`test_plot_export_changes_without_decode_is_refused_client_side`) |
| M10 | `session export --bundle`: stop appending `.zip` | yes (`test_session_export_bundle_adds_the_zip_extension`) |
| M11 | `Client.download`: drop the partial-file removal | no in these three files; **caught by the full suite** (`test_cli.py::test_session_export_removes_a_partial_file`). Covered elsewhere, not a finding |
| M12 | `log export`: `--limit` no longer forces the paged path | yes (`test_log_export_csv_refuses_the_paged_options[extra0]`) |

One true survivor: M5. The new tests are otherwise a strong set - ten of twelve mutations died, several to a test named for exactly the behaviour mutated.

## Coverage of the diff hunks

`uv run python -m pytest --cov --cov-report=term-missing tests/test_cli_export.py tests/test_cli_ux.py tests/test_cli_contract.py tests/test_cli.py` - 252 passed, `cli.py` 71 %, `cli_client.py` 87 %, `cli_output.py` 79 %.
(The run reports `Coverage failure: total of 73 is less than fail-under=78`, which is expected for a four-file subset and not a finding.)

Of the **225 lines the diff adds to `cli.py`, 16 are uncovered by that subset**:

| lines | what | disposition |
|---|---|---|
| 1409-1413 | `_stream_export` stdout `BrokenPipeError` -> `_silence_stdout()` + `Exit(0)` | **gap**. The existing broken-pipe tests in `test_cli.py` cover `main()`, `die()` and `--help`, not this arm. Driven by hand (`log export --csv \| head -2`, rc 0). Refactored from `plot_export`, where it was equally untested, but now serves three commands. |
| 1437 | `except BrokenPipeError: raise` in `_stream_export` | gap, same arm. |
| 1439 | `except OSError` on the mid-stream write/close -> `die(..., 1)` | **gap**. Distinct from the open failure, which `test_an_export_keeps_a_file_it_could_not_open` covers. Driven by hand (`-o /dev/full`, rc 1). |
| 1680, 1682 | `can dump` `since_ts` / `until_ts` params | **gap**. No test passes `--from`/`--to` to `can dump`; driven by hand (drive.log #48). |
| 1694 | `_dump_follow` with the joined id list | **gap**. `-f` with `-i` repeated is untested; a follow is awkward to pin, so the honest label is a gap, not dead-by-design. |
| 1982, 1984 | `plot export` `since_ts` / `until_ts` params | **gap**. No test passes `--from`/`--to` to `plot export`; driven by hand (drive.log #63). |
| 1999-2003 | `plot export --json` without `-o`, the buffered newline counter | covered-elsewhere: `tests/test_plot.py::test_plot_export_long_and_wide` drives the JSON-to-stdout form. |
| 2017 | `plot export --json -o` JSON object | covered-elsewhere: `tests/test_plot.py`. |

Five gaps, all inside paths this leg drove by hand, none inside a refusal the diff added.

## Not done

- **C10 was not driven.** There is no Windows machine on this leg, so the CRLF claim about the redirect path is read from `_stdio.widen_stdout_encoding` (which sets `encoding=` only) and from the LF-only bodies observed on Linux. The Windows leg should run `mcu log export --csv > run.csv` and `mcu log export --csv -o run.csv` and compare sizes.
- **C3 was driven by simulating an older daemon** (the CLI's parameter renamed to one the daemon does not declare), not against a real 0.3.x install. The mechanism - FastAPI silently dropping an undeclared query parameter - was confirmed directly with `curl`.
- The daemon under test **exited without a message** partway through the leg, between the drive matrix and the class 22 probes, with nothing in `daemon.log` and no crash log in the data dir. It was most likely swept by the concurrent full-suite mutation run rather than by anything the CLI did; every finding above was either recorded before it went or re-driven against the restarted daemon. Flagged here rather than chased, because it is a daemon-leg question.

## The two questions

**Q1, what am I least confident about?**
C3's severity, not its existence. The mechanism is proven and the silence is real, but how much it matters depends on how often `mcu` and `mcuscoped` actually skew in practice, and I have no data on that. The registry asserts they do skew (class 46's opening line), and the diff moved a bound that used to work against every daemon into one that needs a 0.4.0 peer, so I have filed it at MED; a maintainer who knows the deployment story may reasonably call it LOW. Second: C10, which is reasoned rather than driven, and the class 13 note in REVIEW.md is explicit that a POSIX-only check passes either way - so it should be treated as unverified until the Windows leg runs it.

**Q2, what did the round not look at that it should have?**
Three things, in order of how much they bother me.

First, **the daemon side of `--deadband` and `/lines/export`**. C2's actual defect is in `server.py`, and C7's "unknown session is an empty window at exit 0" is a store-level resolution question. Both were reached from the CLI surface the diff added, which is why they are filed here, but neither will be fixed here, and the server leg's own class 22 sweep needs to run over `server.py:2831-2848` and over every other `float(`/`int(` the export endpoints added in this diff. I only swept the CLI files, so I do not know whether `_parse_deadband` is the only one.

Second, **the row-count contract as a whole**. C1 is one instance of a pattern the diff repeats four times: `wrote N lines`, `wrote N frames`, `wrote N rows` and `{"lines": N}` are each derived from counting `\n` in a body the daemon rendered, on the assumption that one row is one physical line. That assumption is nowhere stated and nowhere tested, and it is false for `text` and `csv` the moment a captured `raw` contains a newline - which `mcu mark` will produce on request. The lazy fix is one response header from the daemon carrying the row count, which would close all four sites at once; patching the counter per command would not.

Third, **`can dump -f` combined with the new bounds**. `mcu can dump --to 19:54 -f` prints the bounded backfill and then follows live past `--to` forever, because `_dump_follow` (cli.py:1694) receives the id list and the bus but neither `since_ts` nor `until_ts`. I noticed it while reading, it is uncovered (the coverage table above), and I did not drive it because a follow needs a timeout harness this leg did not have. It is a small, real contradiction between the option and the flag, and the next round should either refuse the combination the way `--csv -f` is refused, or stop the follow at the bound.
