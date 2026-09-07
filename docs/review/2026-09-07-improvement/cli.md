# CLI leg, adversarial review with measurement, 2026-09-07

Worktree HEAD 6f1441dc61a2e110f8446b74ad02ba8eda22485c (confirmed). Scope: `git diff e573e93..HEAD` over cli.py, cli_client.py, cli_daemonctl.py, README.md, SPEC.md, CHANGELOG.md, test_cli_ux.py, test_cli.py, test_cli_contract.py. Nothing fixed. Every daemon in this leg ran under `XDG_DATA_HOME=/tmp/review-2026-09-07/data`, `--config` on a throwaway TOML with its own `db_path`, ports 8591/8592; stopped by `mcu daemon stop`, verified gone with `ps`/`ss`.

## Findings

| id | sev | file:line | claim | how verified |
|---|---|---|---|---|
| C1 | medium | host/mcuscope/cli_daemonctl.py:49 (`_stderr_log_path`), cli.py:1982 (`open(err_path, "wb")`) | `mcuscoped.err` is one file per data dir, not keyed by host:port like the pid record and the daemon's own `-startup.log`/crash log (daemon.py:327 comment: "a second daemon on another port must not overwrite this one's startup log or crash log"). A `daemon start` on port B truncates the file the running daemon A still holds open (no O_APPEND), so A's later stderr lands at its old offset over B's text, and B's failure tail then reads a spliced line. | Driven: A on 8591, failed start on 8592 with a broken TOML, one garbage HTTP request to A. File content afterwards: `mcuscoped: /tmp/review-2026-09-07/cfg-broWARNING: Invalid HTTP request received.\n is" at line 1 col 8`. Log in measurement 3. |
| C2 | low (class 10) | host/mcuscope/cli.py:2050-2053 (`webbrowser.open` after `out_json`) | `daemon start --json --open` breaks stdout purity when the browser command writes to stdout: `webbrowser`'s GenericBrowser/BackgroundBrowser inherit the CLI's stdout, so `BROWSER=<cmd>`, a console browser (lynx/w3m on a headless box) or a chatty xdg-open appends text after the JSON object. The daemon's own `--open` has the same shape but no `--json` contract. | Driven: `BROWSER=/bin/echo mcu --json daemon start --open` gives `{"ok":...}\nhttp://127.0.0.1:8591/ui/`; `json.tool` says "Extra data: line 2". Measurement 3. |
| C3 | low | host/mcuscope/cli.py:2166 (AI_GUIDE "GLOBAL OPTIONS"), cli_argv.py:15 (`_GLOBAL_FLAGS`) | The guide lists `--install-completion, --show-completion` under GLOBAL OPTIONS, and SPEC 4 / the hoister promise globals anywhere on the line, but neither flag is hoisted: `mcu status --show-completion` is `Error: No such option`, exit 1. Either hoist them or list them outside GLOBAL OPTIONS. | Driven, measurement 3. |
| C4 | low (test quality) | host/tests/test_cli_contract.py:175 (`GUIDE_EXEMPT`) | The exemption added for the completion flags is dead and disarming: the test is a substring check (`opt not in cli.AI_GUIDE`) and the guide text already contains both strings, so reverting the exemption passes; with the exemption in place, deleting the guide's completion line also passes. The one line the exemption was written for is the one line the test no longer pins. | Both mutations run (revert table rows 24 and 25). |
| C5 | low (usability) | host/mcuscope/cli.py:2055-2071 (`daemon_restart`) | `restart` starts with the options given on the restart line only; the running daemon's `--sim` and `-c` are not carried. `mcu daemon start -c x.toml --sim` then `mcu daemon restart` silently comes back with the default config (a different capture db) and no sim port. Docstring says "with the given options", but the name reads as "same daemon again". | Driven: after restart without `--sim`, `mcu ports` prints "no ports attached". Measurement 5. |
| C6 | low (class 29) | cli.py:1983-1986 (`except OSError` on the log open), cli_client.py:158-162 (`fail()` when `/ports` is absent or has no aliases) | Two refusal paths with no test: the log-file open failing (falls back to DEVNULL with a warning and `err_path=None`) and the ambiguity hint when the `/ports` probe returns None or a body without `alias` entries (message stays bare). Neither negative is asserted anywhere. | grep of test_cli_ux.py for "cannot write the daemon log" and for a `fail`/`probe` stub: zero hits. |
| C7 | nit | cli.py:2129, cli_daemonctl.py:63 | `mcu daemon status` at the default URL says only `not running` (exit 3), no start hint, while every "unreachable" arm has one; and the tail header says "last 1 lines". | Driven, measurement 5 and 2. |

Ruled out (each driven or read, listed so nobody re-checks):

- Lazy httpx import breaking an except clause: all `httpx.` references in cli.py are inside `_dump_follow` and `_poll_frames`, each with its own `import httpx` above the try; cli_client's `_daemon_errors` imports before `try`, `probe`/`open`/`request` import first; `error_text` and `fail` use no httpx name. `python -X importtime` for `--help`/`--version`/`ai-guide` shows zero httpx lines; `status` shows 24 (66 ms).
- `Client.fail()` probing `/ports` on a stream: `download`/`stream` call `resp.read()` first and the probe opens a fresh `httpx.Client` (new connection, the daemon is async). In practice "port is ambiguous" is only raised by send paths that go through `request`, where the response is complete. Token forwarded (`self.s.headers()` in `probe`).
- Ambiguity hint on the wrong text: the only daemon source is serial_link.py:1410 `"port is ambiguous; specify one"`; the CLI appends ` with -p, one of: sim, spare`, measured. `-p x` gives `no such port: x` with no hint.
- `mcuscoped.err` on Windows: opened "wb" in the parent and closed right after `Popen`; the child's inherited handle carries the CRT's default share read|write, so a later `open` for read (the tail) or with O_TRUNC (the next start) is not refused, and nothing ever unlinks it. Child stderr encoding is UTF-8 because `console_entry` runs `widen_stdout_encoding()` on stderr too. Reasoned, not driven (no Windows machine here, see Q1).
- Truncation racing a still-running old daemon on the same port: `daemon start` probes `/status` and dies "already running" before the open; `restart` waits for the pid to be gone before `daemon_start` opens the file. Only the different-port case (C1) truncates a live file.
- `restart` when stop succeeds and start fails: pid record removed by `_abandon_daemon`, `daemon status` says `not running` exit 3, no orphan (measurement 2, C3 block).
- `--open` with `--json`: only C2. `--open` after a failed start is not honoured (test and driven). No browser at all (`BROWSER=/nonexistent`, no DISPLAY): silent rc 0, `webbrowser.open` returns False and raises nothing; the daemon's own `--open` discards the result the same way.
- `--order` with paging past 1000 rows: `--limit 2500 --order desc` returned 834 rows ids 834..1, `asc` 861 rows 1..861, `truncated: false`; text desc prints newest first, 914 lines. `--order` is applied after the walk, so the class 44 anchor is untouched. `--last-ms 2000 --order desc` correct. `lines` has no `--follow`; `tail` has no `--order`, so no interaction exists.
- `config path --json` piped through `python -m json.tool`: one object, rc 0; text form one line; `default_config_path()` touches no disk.
- `ports` empty text under `--json`: `{"ports": []}` only, no prose.
- `add_completion` and hoisting (class 5): `mcu --show-completion status` prints the script (eager flag, "status" not consumed as a value); `mcu lines --order desc -p sim --json --url U` and `mcu --json lines --order asc` both hoist correctly; `mcu lines --order --json` refuses "got '--json'" with no JSON on stdout, which is the same value-guard face the pre-existing `--match --json` has (there the regex `--json` matched nothing, rc 0), so consistent with SPEC's "a token in a value position is the value".
- Start hint with a trailing slash: `--url http://127.0.0.1:8558/` gets the hint (`Settings.url` is `rstrip("/")`ed and `start_hint` strips again). Uppercase scheme `HTTP://127.0.0.1:8558` and `http://localhost:8558` get no hint: conservative, not wrong.
- Class 46: the diff reads no new daemon field; `fail()` reads `ports`/`alias` with `.get` and `"alias" in pt`.
- Stale docstring on `_stop_running_daemon` fixed in the diff ("dies on any failure"): `quiet` returns normally, callers checked (`_stop_daemon` two sites, `restart`).

## Sweep verdicts

Site counts are from the diff hunks (`git diff e573e93..HEAD` over the three source files), enumerated by reading every hunk; the tests file is swept where the class is about tests.

### Class 5 (argv hoisting)
Sites: global option surface changed at 1 place (`add_completion=True` adds two root flags). `_GLOBAL_FLAGS`/`_GLOBAL_VALUE_OPTS` unchanged; no new alias or subcommand option that takes a value except `lines --order` (1).
- `--install-completion`, `--show-completion`: not hoisted, so only accepted before the subcommand (C3, low; hoisting itself is not broken).
- `lines --order VALUE`: complies; value guard covers it (`--order desc -p sim --json` hoists `-p`/`--json`, `--order --json` keeps `--json` as the value).
- Hoist tests: the existing hoist matrix in test_cli.py was not extended for the two new root flags; verdict: gap, low, folded into C3.

### Class 9 (exit-code contract)
Sites in the diff: 11.
1. `order_option` `raise typer.BadParameter`: USAGE_ERRORS, exit 1 with usage text; `--json` form emits the error object. Complies (driven).
2. `open(err_path, "wb")` `except OSError`: warning to stderr, DEVNULL fallback. Complies (read; untested, C6).
3. `subprocess.Popen` in `try/finally`: no except; a Popen OSError propagates as before this diff (pre-existing path to the crash-log backstop). Complies/unchanged.
4. `_stderr_tail` `except OSError` returns "". Complies.
5. `_abandon_daemon` two `die(..., 1)` with the tail appended. Complies (driven: rc 1).
6. `fail()` `die(..., 1)` plus the unreachable `raise AssertionError`. Complies (driven: rc 1, JSON error object).
7. `_follow_ws` `die(..., 3)` with hint. Complies (test).
8. `_daemon_errors` two `die(..., 3)` with hint. Complies (driven: rc 3).
9. `webbrowser.open`: returns False on every failure I could produce, raises nothing. Complies.
10. `daemon_restart` calling `daemon_start` directly: `typer.Exit` from `die` propagates to the dispatcher. Complies (driven: rc 1 in the C3 block).
11. `config_path`: no raise site. Complies.
Each failure mode was driven through the installed console script (`.venv/bin/mcu`), not `python -m`.

### Class 10 (--json stdout purity)
Sites in the diff writing to a stream: 8.
1. `ports` "no ports attached" `print`: text mode only, JSON path returns first. Complies (driven).
2. `daemon start` `print`/`out_json` with `ui_url`. Complies (driven, `json.tool`).
3. `daemon start --open` `webbrowser.open` after the JSON: violates when the browser writes to stdout (C2).
4. `daemon restart` `err("no daemon running...")`: stderr. Complies (driven: one object on stdout).
5. `_stop_running_daemon` `quiet` return: the stop's object suppressed, one object per restart. Complies (driven and revert-verified).
6. `config path` `print`/`out_json`. Complies (driven).
7. `_stderr_tail` text inside `die`: goes into the error object's string, stderr otherwise. Complies (driven).
8. `err("warning: cannot write the daemon log")`: stderr. Complies (read).
Exempt by record: `mcu --json --show-completion` prints the script (typer eager option), already listed as left in REVIEW_LOG 2026-09-07.

### Class 13 (Windows file-sharing and encoding)
Sites: 3 new `open(` calls, 0 new `os.replace`/`os.rename`/`os.remove`.
1. `open(err_path, "wb")`: binary, `newline=` not applicable; handle closed in `finally` right after the spawn; the child holds its own. Complies.
2. `open(err_path, encoding="utf-8", errors="replace", newline="")`: explicit encoding, tolerant, `splitlines()` handles CRLF. Complies. Encoding matches the child (stderr widened to UTF-8 in `console_entry`).
3. `_FakeDaemon` writes bytes to the handle in the test: binary. Complies.
Redirected output: `--json daemon start > file` and `config path | json.tool` driven. No unlink of a file this process holds. Verdict: complies; the sharing claim about a live child's handle is reasoned, not driven on Windows (Q1).

### Class 29 (the negative is asserted)
Guards in the diff: 12. Asserted negatives: `start_hint` off for a custom `--url` and for `MCUSCOPE_URL` (2 tests), `ports` JSON carries no prose, `--order` refusal, `--open` absent means not opened, `--open` on a failed start, `restart` with nothing running skips `stop`, `quiet` suppresses the second object (json.loads would raise), empty err file gives no tail, the file is truncated per start, `_FakeDaemon` refuses DEVNULL, `fail()` with a non-ambiguous error stays plain (`no such port: x`, driven only, not in a test).
Not asserted: log-open failure fallback; `fail()` when the `/ports` probe answers None or an alias-less body (C6).

### Class 33 (tests inheriting the real environment)
Tests in test_cli_ux.py that spawn a child: 3.
1. `test_restart_of_a_running_daemon_swaps_the_pid`: `_spawn_env` sets `XDG_DATA_HOME` and `MCUSCOPE_URL`, `-c` names a tmp config with its own `db_path`. Data dir and pid record isolated. `XDG_CACHE_HOME` is not set, and `update.check` defaults to True, so the child daemon reads (and after 24 h would write) the user's real `~/.cache/mcuscope/update.json`. Same shape as the 6 pre-existing `_spawn_env` users in test_cli.py, so a pre-existing class 33 residue, now 7 instances; verified the real cache was not written during this leg (mtime 00:26 today, before the runs). Low.
2. `test_httpx_is_not_imported_for_help_version_or_the_guide`: `--help`, `--version`, `ai-guide` touch no platformdirs path. Complies.
3. `test_port_help_names_the_rule`: `mcu --help` only. Complies.
In-process tests: `fake_spawn` patches `_pid_file` to tmp_path so `mcuscoped.err` lands there; `config path` tests run under the autouse `_isolated_user_dirs`. Complies.

### Class 35 (error-path write hijacking the exit code)
Error-path writes in the diff: 8 (six `die` strings, two `err` calls). All go through `err()`/`die()` in cli_output, the guarded boundary; no new bare `print(..., file=sys.stderr)`. Complies.

### Class 46 (field skew)
Fields newly read from a daemon body in the diff: 2 (`ports`, `alias` in `fail()`), both tolerant (`.get`, membership test). `ui_url` is client-derived. No SPEC 3 addition in the diff. Complies.

## Measurement log

Environment wrapper `/tmp/review-2026-09-07/m.sh` (venv on PATH, `XDG_DATA_HOME`/`XDG_CONFIG_HOME` under /tmp, `MCUSCOPE_URL` unset). Configs: `cfg.toml` (port 8591, own db), `cfg2.toml` (8592), `cfg-badport.toml` (missing `/dev/ttyDOESNOTEXIST`, autoconnect), `cfg-broken.toml` (invalid TOML). Nothing listened on 8558 during the leg (`ss -ltn`).

1. Unreachable hints (nothing running):
```
$ mcu status
daemon unreachable at http://127.0.0.1:8558: [Errno 111] Connection refused; start it with 'mcu daemon start' (or run 'mcuscoped')   rc=3
$ mcu --url http://127.0.0.1:8599 status
daemon unreachable at http://127.0.0.1:8599: [Errno 111] Connection refused   rc=3
$ mcu --url http://127.0.0.1:8558/ status      -> hint present, rc=3
$ mcu --url HTTP://127.0.0.1:8558 status       -> no hint, rc=3
$ mcu --url http://localhost:8558 status       -> no hint, rc=3
$ MCUSCOPE_URL=http://127.0.0.1:8558 mcu status -> hint present, rc=3
$ mcu --json status -> stderr line plus {"error": "...; start it with ...", "exit_code": 3}
$ mcu daemon status -> not running   rc=3 (no hint)
```

2. `daemon start` with a missing serial device (`cfg-badport.toml`): start succeeds in 1.30 s wall, rc 0; `status` shows `nope /dev/ttyDOESNOTEXIST @115200 disconnected (no_device)`; `mcuscoped.err` exists, 0 bytes; the daemon's own `mcuscoped-127.0.0.1-8591-startup.log` (570 bytes, host:port keyed) sits beside it. A bad device is not a failed start.

3. `daemon start` with 8591 held by a foreign listener (`cfg.toml`):
```
mcuscoped exited with status 1 without answering at http://127.0.0.1:8591
last 1 lines of /tmp/review-2026-09-07/data/mcuscope/mcuscoped.err:
mcuscoped: 127.0.0.1:8591 is already in use (Address already in use). Another mcuscoped or another service is listening there; stop it, or start this one on a different port with --port.
wall 1.77s  rc=1
```
`--json` form: stderr identical, stdout one object `{"error": "<same text with \n>", "exit_code": 1}`, `json.tool` accepts it. No pid record left.

4. `daemon start` with `cfg-broken.toml`: `exited with status 1 ... last 1 lines of ...: mcuscoped: /tmp/review-2026-09-07/cfg-broken.toml: invalid TOML: Invalid key "this is" at line 1 col 8`, rc 1, no pid record.

5. `daemon restart`:
- nothing running: stderr `no daemon running at http://127.0.0.1:8591; starting one`, stdout `started mcuscoped (pid 552262); web UI: http://127.0.0.1:8591/ui/`, rc 0.
- one running, `--json`: stdout `{"ok": true, "pid": 552266, "ui_url": "http://127.0.0.1:8591/ui/"}` only, stderr empty, rc 0, pid changed.
- stop ok then start fails (`cfg-broken.toml`): the tail message, rc 1; data dir holds only the startup log and `mcuscoped.err`; `daemon status` -> `not running` rc 3.
- running with `--sim`, restarted bare: rc 0, `mcu ports` -> `no ports attached (see 'mcu devices', then 'mcu attach DEV')` (C5).

6. `daemon start --json` (fresh): `{"ok": true, "pid": 552278, "ui_url": "http://127.0.0.1:8591/ui/"}`, stderr empty, rc 0.

7. `mcu config path --json | python -m json.tool` -> `{"path": "/tmp/review-2026-09-07/config/mcuscope/config.toml"}`, rc 0; text form prints the path; one line on stdout+stderr combined. `mcu config path --url http://127.0.0.1:1` rc 0 (no daemon needed).

8. `mcu ports`: with the sim attached `sim  sim://demo  @115200  connected target=sim`; after `detach sim` -> `no ports attached (see 'mcu devices', then 'mcu attach DEV')` rc 0; `--json` -> `{"ports": []}`.

9. Ambiguity with two ports (`sim://demo` as `sim`, `socket://127.0.0.1:9` as `spare`):
```
$ mcu send hi          -> error: port is ambiguous; specify one with -p, one of: sim, spare   rc=1
$ mcu --json send hi   -> same on stderr; stdout {"error": "error: port is ambiguous; ...", "exit_code": 1}
$ mcu -p x send hi     -> error: no such port: x   rc=1
$ mcu -p sim send hi   -> ok   rc=0
$ mcu lines --limit 2 / mcu tail -n 1 with two ports: not ambiguous (retrospective), rc=0
```

10. `mcu lines --order` (three markers first/second/third):
- default text: first, second, third; `--order desc`: third, second, first; `--order asc` equals default.
- `--json` ids default `[377, 357, 330]`; `--order asc` `[330, 357, 377]`.
- `--order newest`: `Error: Invalid value for --order: expected asc or desc, got 'newest'` rc 1; with `--json` the same plus `{"error": "...", "exit_code": 1}`.
- paging: `--json --limit 2500 --order desc` -> 834 rows, first id 834, last id 1, truncated false; `asc` -> 861 rows, 1..861; text desc 914 lines, first line the newest sample.
- `--last-ms 2000 --order desc --limit 3`: newest first, rc 0.

11. Completion: `mcu --show-completion bash` prints the script rc 0; `mcu status --show-completion` -> `Error: No such option: --show-completion` rc 1 (C3); `mcu --show-completion status` prints the script; `mcu --json --show-completion` prints the script (recorded as left); `mcu --help` lists both flags.

12. `--open`:
- `BROWSER=/bin/echo mcu --json daemon start --sim --open` -> stdout `{"ok": true, "pid": 552542, "ui_url": ...}` then `http://127.0.0.1:8591/ui/`; `json.tool`: `Extra data: line 2 column 1` (C2).
- `BROWSER=/nonexistent/browser ... --open` and no DISPLAY/BROWSER: rc 0, silent.

13. Two daemons, one `mcuscoped.err` (C1): A on 8591 (`--sim`), B on 8592 started and stopped, then a garbage request to A: file holds `WARNING: Invalid HTTP request received.\n` (41 bytes, uvicorn on A's stderr). Failed start on 8592 with `cfg-broken.toml` while A runs, then another garbage request to A: file is 103 bytes reading `mcuscoped: /tmp/review-2026-09-07/cfg-broWARNING: Invalid HTTP request received.\n is" at line 1 col 8\n`. A second failed 8592 start prints that spliced line as its "last 1 lines".

14. Import and wall time (`python -X importtime`, best of 5 wall through the console script):
- `import mcuscope.cli`: 53.4 ms cumulative, typer 27.0 ms of it, zero httpx lines.
- `main(['--help'])`: zero httpx lines. `main(['status'])`: httpx 65.5 ms cumulative, 24 httpx lines.
- wall: `mcu --help` 0.189 s, `--version` 0.111 s, `ai-guide` 0.112 s, `config path` 0.130 s, `status` (unreachable) 0.230 s.

## Revert verification

Baseline: `tests/test_cli_ux.py` + `tests/test_cli_contract.py` 45 passed in 10.6 s. Driver `/tmp/review-2026-09-07/revert.py` checks every anchor before mutating, restores after each run and asserts the restore; log in `revert.log`. Tree clean afterwards (`git status` empty).

| test | mutation | failed |
|---|---|---|
| test_unreachable_at_the_default_url_says_how_to_start_one | `start_hint` returns "" | yes |
| test_unreachable_at_a_custom_url_gets_no_start_hint | drop the `!= DEFAULT_URL` gate | yes |
| test_unreachable_via_the_env_url_gets_no_start_hint | same | yes |
| test_follow_unreachable_at_the_default_url_hints_too | remove `{start_hint(s.url)}` from `_follow_ws` | yes |
| test_a_failed_start_shows_the_tail_of_the_daemons_stderr | `_stderr_tail` returns "" | yes |
| same test | open mode `"ab"` (no truncation) | yes |
| test_a_failed_start_with_an_empty_stderr_file_shows_no_tail | drop the empty-file guard | yes |
| test_start_prints_the_web_ui_url_and_opens_it_only_on_request | `if True:` around `webbrowser.open` | yes |
| same test | drop `ui_url` from the JSON | yes |
| test_open_is_not_honoured_when_the_start_fails | open the browser right after the spawn | yes |
| test_restart_with_no_daemon_running_just_starts_one | call `_stop_daemon` unconditionally | yes |
| test_restart_of_a_running_daemon_swaps_the_pid | `_stop_daemon(s)` without `quiet` | yes |
| test_ports_with_none_attached_says_so_in_text_only | `if False:` on the empty message | yes |
| test_lines_order_desc_reverses_the_text_output | ignore `order` in the text path | yes |
| test_lines_order_asc_reverses_the_json_output | ignore `order` in the JSON path | yes |
| test_lines_order_rejects_anything_else | `order_option` accepts anything | yes |
| test_an_ambiguous_port_lists_the_aliases | `fail()` never probes | yes |
| test_port_help_names_the_rule | `add_completion=False` | yes |
| same test | "several" -> "many" in the `-p` help | yes |
| test_config_path_prints_the_default_location | print the parent dir | yes |
| test_config_path_does_not_need_a_daemon | `config path` calls `/status` first | yes |
| test_httpx_is_not_imported_for_help_version_or_the_guide | top-level `import httpx` in cli.py | yes |
| test_the_guide_gives_the_powershell_control_character_form | `([char]3)` -> `(char 3)` | yes |
| test_cli_contract::test_ai_guide_names_every_flag | revert `GUIDE_EXEMPT` to `{"--follow", "--out"}` | no (C4: exemption dead) |
| test_cli_contract::test_ai_guide_names_every_flag | delete the guide's completion line, exemption kept | no (C4: line unpinned) |

## The two questions

Q1, least confident: the Windows claims about `mcuscoped.err`, which are reasoned from CRT share semantics and not driven: that a later `open(..., "wb")` truncates a file the child still holds, that `_stderr_tail` can read it, and that `DETACHED_PROCESS` plus a file handle for stderr does not leave the parent's handle open. The Windows leg should run `daemon start` twice on two ports and once with a bound port, then read the tail. Second: the `--order` paging measurement ran on a live sim, so row counts moved between runs (834 then 861); the invariant asserted is only "walk complete, order applied afterwards", not a fixed row set.

Q2, the gap: the daemon already keeps a host:port keyed `-startup.log` and crash log through `_stdio.set_report_key`; the diff added a second, unkeyed trace beside it instead of extending the existing one, and nothing in the round compared the two. The sibling worth checking is `_stdio._write_crash_log`: with stderr now a file, a daemon crash writes both the crash log and the traceback into `mcuscoped.err`, and the next start on any port truncates the latter before anyone reads it. Also the `--open` shape in daemon.py (a timer calling `webbrowser.open` with the result dropped) is the same silent-on-failure shape as C2's neighbour and was not in scope.
