# Fix-diff leg: cli slice (2026-09-24)

HEAD: `b994076c88c875ad43c30789cda6039d147b7abe` (diff `6e4f6f7..HEAD`).
Scratch: `~/tt-data/mcuscope-2026-09-24/fixdiff/` (`cli-copy/` mutated copy, `old-copy/` the 6e4f6f7 tree, probes `cli-copy/tests/test_zz_probe_*.py`, `cli-mut/mut.sh`).
Probes run on the repo venv (3.13, websockets 17.0.1) and the floor venv (3.10.20, websockets 14.0); each probe printed the imported `mcuscope.cli` path.

## Findings

### 1. HIGH: `daemon stop` signals an unrelated local process named by a stale pid record (regression)
- Where: `cli_daemonctl.py:260` (`_wait_daemon_gone(s, pid, ...)`), `:295-304`, `:265-271`; `cli.py:2715-2733`.
- Defect: the stop now judges "gone" on the recorded pid and falls back to SIGTERM on it, without any check that the record names the daemon that answered.
- Scenario: a crashed daemon's record names pid P, P is recycled by an unrelated process, and a new daemon (unrecorded: `pidfile.claim` never overwrites a live record) or a tunnelled/remote daemon serves the URL.
  - `/shutdown` is accepted, P never exits, and after the grace P is sent SIGTERM; the output is `stopped mcuscoped (pid P)`, exit 0.
- Second face: a record naming a dead pid returns "gone" at once, before a slow shutdown finishes, so the stop exits 1 "a process is still answering ... stop it from the process list" while the daemon is exiting.
- Confirmed: driven.
  - `test_zz_probe_stop.py::test_probe_record_naming_an_unrelated_live_pid`: the victim died with -15 on HEAD, survived on 6e4f6f7.
  - The dead-pid face fails on both trees, so it is not new, but it sits in the code this fix rewrote.
- Contradiction: the LIFECYCLE-1 ruling ("signalled only when a local pid record names it") assumes a record names the daemon.
  - `pidfile.py:22-27` documents the recycled-record case and still promises that stop "acts on the pid /status reports ... can neither miss the live daemon nor kill the innocent process". That is now false.
  - The SPEC 4 `daemon` row and the guide repeat the promise.
- Fix: judge the stop by `/status` going quiet on every path.
  - Signal the recorded pid only when it is corroborated: `/status`'s pid equals it, or `/status` names it as its parent (the Windows launcher shim; the daemon would report `ppid`).
  - Then correct `pidfile.py`'s docstring and the SPEC row.

### 2. MEDIUM: CLI-10 left two follow siblings at exit 3 (a traceback on the 3.10 floor)
- Where: `cli.py:1240` (`_follow_ws` `except OSError` → 3) and `cli.py:2175` (`_dump_follow` give-up: every `TransportError` → 3).
- Defect: a daemon that accepts and then stalls is exit 3 "daemon unreachable" on `tail -f` and `can dump -f`, where SPEC 4 now says exit 1 "on every command".
- Scenario 1: `tail -f` against an accept-and-stall listener prints `daemon unreachable at ...: timed out during opening handshake`, rc 3.
  - On Python 3.10 with websockets 14.0 (both the declared floors), `asyncio.exceptions.TimeoutError` is not an `OSError`, so it escapes every arm as a traceback (classes 9 and 42).
- Scenario 2: `can dump -f` with every poll a `ReadTimeout` prints `daemon unreachable ... for 30s: timed out`, rc 3.
- Confirmed: driven (`test_zz_probe_wsstall.py` on both venvs, `test_zz_probe_dumpstall.py`).
- Fix:
  - In `_follow_ws`, catch `TimeoutError` and `asyncio.TimeoutError` ahead of `OSError`, and die 1 with "accepted the connection but stopped answering".
  - In `_dump_follow`, map `httpx.TimeoutException` other than `ConnectTimeout` to 1.
  - Add one test per site.

### 3. MEDIUM: `[port]` column missing from `log export` (and the `tail -f` backfill) when the history spans a detached board
- Where: `cli.py:1876` (`show_port = not csv and _port_column(s)`) and `cli.py:968`: both judged on the ports attached now.
- Defect: the CLI-5 failure survives for any retrospective read of a board that is no longer attached, and SPEC 4's "whenever more than one board can appear" is not met.
- Scenario: boards `sim` and `b2` capture, `b2` is detached, and `mcu log export` goes to the daemon's text render. The rows `from-b2` and `from-sim` come out with no port, while `mcu lines` over the same rows shows `[b2]`/`[sim]`.
- Confirmed: driven live (`--sim` daemon plus `mcu-sim --tcp-port`, attach, mark, detach), and via a canned transport (`test_zz_probe_canned.py`).
- Fix: without `-p`, always show the column on the client-rendered path (CLI-5's "or always"). The alternative is to judge on the ports the window's rows carry, not the ports attached now.

### 4. MEDIUM: README's headline agent examples now always fail
- Where: `README.md:222` (`mcu wait --match 'BOOT OK' --send 'reset'`) and `README.md:250` (`mcu assert --send reset ...`).
- Defect: `reset` is neither a monitor command nor a sim command, so with CLI-2 both examples exit 1. The guide's copy was changed to `selftest` for this reason; the README was not.
- Confirmed: driven against `mcuscoped --sim`: `--send 'reset' was refused: ERR 1 badcmd unknown reset`, rc 1; the assert gives FAIL, rc 1.
- Fix: use a command the sim answers (`ping`), or mark the command as the firmware's own.

### 5. LOW: `mcu attach` crashes with a traceback on a non-object `/ports` probe body
- Where: `cli.py:388-389` (`(listed or {}).get("ports")`, where `probe` returns any JSON value).
- Scenario: a proxy or skewed responder answers `GET /ports` with a JSON list. The result is `AttributeError: 'list' object has no attribute 'get'` out of `main()`, which is a crash log (class 9).
- Confirmed: driven (`test_zz_probe_canned.py::test_probe_attach_with_non_object_ports`).
- Fix: `listed if isinstance(listed, dict) else {}`, and type-check `ports` as a list.

### 6. LOW: a row whose `raw` is not a string now ends `tail -f` with a traceback (regression)
- Where: `render.py:29` (`row['raw'].translate(...)`); the per-row guard in `_follow_ws` catches only `KeyError`, `TypeError` and `ValueError`.
- Scenario: a frame row with `"raw": null` raises `AttributeError` through the follow. On 6e4f6f7 it printed `None` and the follow went on (classes 9 and 16).
- Confirmed: driven on both trees (`test_zz_probe_rawnone.py`).
- Fix: `str(row['raw']).translate(_BREAKS)`.

### 7. LOW (Windows, reasoned): the LIFECYCLE-5 append does not append in the spawned daemon on Windows
- Where: `cli.py:2571` (`open(err_path, "ab")` handed to `Popen(stderr=)`).
- Defect: on Windows, `O_APPEND` is emulated by the opening process's CRT (seek-to-end before each write). The child inherits a raw HANDLE without append access, so it writes at its own file pointer.
  - Two racing daemons then write at shared offsets and can overwrite each other's lines. On POSIX, `O_APPEND` lives on the inherited open file description, so appends are atomic.
- Test gap: `test_a_start_appends_to_the_stderr_file_and_shows_only_its_own_lines` writes through the parent's own fd (`os.write(kw["stderr"].fileno())`), which the CRT does append. It passes on Windows without modelling the child (class 27).
- Confirmed: reasoned from CRT semantics, not driven; the Windows leg must confirm.
- Fix: on Windows, open the handle with `FILE_APPEND_DATA` only (`CreateFileW` through ctypes, then `msvcrt.open_osfhandle`), so every write through the inherited handle goes to EOF.

### 8. LOW (test gap): declining at a real prompt is no longer tested
- Where: `cli_output.py:533-534`; `test_cli.py:1157` and `:1172`; `test_cli_closed_stdio.py:80-89`.
- Defect: after CLI-12, every prompt test pipes stdin and is refused before the read. The only tty control answers `y` and does not assert that stdout stays empty.
- Mutation: `if answer.strip().lower() not in {"y", "yes"}:` → `if False:` passed all 7 files that drive prompts. `test_regressions.py` had one environmental failure (no `docs/` in the copy), unrelated.
- `test_purge_without_yes_asks_and_deletes_nothing_when_refused` no longer reaches a prompt; its name is stale.
- Fix: tty-patched tests for `n` and EOF on purge and session delete: `cancelled`, exit 1, only the dry-run request sent, stdout empty.

### 9. LOW (docs): "pipes and files carry the captured bytes" is false for text output
- Where: SPEC.md:1134 and `AI_GUIDE` (`cli.py:2810`) against `render.py:15-18,29`; SPEC 3.4's `text` definition (`SPEC.md:768`).
- Defect: `fmt_line` escapes VT, FF, FS, GS, RS, NEL, U+2028 and U+2029 on every sink, and the daemon's `/lines/export?format=text` does too. Only `--json` (and jsonl/csv) is faithful. SPEC 3.4 does not mention the escaping.
- Fix: "`--json` carries the captured bytes; text output escapes line boundaries everywhere, and other controls only on a terminal". Add the escaping to the 3.4 `text` sentence.

### 10. MEDIUM (Windows, pre-existing, reasoned): `daemon start` reports failure after a successful start from a venv
- Where: `cli.py:2633-2635` (`serving != proc.pid` → "another daemon is already serving"). This is not a changed line, but the diff restated it as correct in `cli_daemonctl.py:232-239`.
- Defect: a Windows venv's `sys.executable` is the launcher redirector, so `proc.pid` is the shim and `/status`'s pid is its child. `daemon.py:335` and `pidfile.py:13-16` already say so.
  - Every `mcu daemon start` from a uv, pipx or venv install then exits 1 while its daemon runs.
- Coverage: the real-spawn tests are skipped on Windows (`_PIDDIR_ENV_SKIP`), and the Windows wheel smoke runs only `--version`.
- Confirmed: reasoned, not driven; present since 0b5eed9.
- Fix: accept a serving pid whose parent is `proc.pid` (have `/status` report `ppid`), and add the Windows leg item "`mcu daemon start` then `stop` from a venv".

### 11. NIT: the repair warning misdescribes a POSIX `>&-`
- Where: `_stdio.py:404-418`.
- Driven: `mcu ai-guide >&-` prints a 5-line `WARNING ... no console is attached, so that output goes to devnull` plus the interpreter report, then `cannot write output: ... stdout was closed when mcu started`, rc 1.
- Fix: when `stdout_was_closed()`, skip the warning, or name the real cause in one line.

### 12. NIT: the attach retarget note misses a rebind and is not in SPEC 4
- Where: `cli.py:390-395`.
- Missed rebind: re-attaching a connected serial-bound alias by its resolved device (`/dev/ttyACM0`) prints nothing, although the port stops following the serial.
- Wrong wording: an unconnected serial-bound port reads `was attached to 0672FF3` without the word "serial" (SPEC 3.4: `device` holds the serial until connect). Reasoned.
- Missing from SPEC: the SPEC 4 `attach` row does not mention the note; CLAUDE.md requires it in the same commit.

### 13. NIT: `assert`'s check lines print `raw` unrendered
- Where: `cli.py:1461` and siblings.
- Defect: device text bypasses `fmt_line`, so a U+2028 or VT in a matched line splits the verdict output on pipes (on a terminal, `visible` catches all but U+2028/2029).
- Fix: apply `render._BREAKS`.

### 14. NIT (test): a stop test drives a state no caller produces
- Where: `test_cli_daemon_stop_scope.py:144`.
- Defect: `_stop_running_daemon(s, None, 4242)` passes a pid without its record path, which no caller does (class 63). Use `(s, None, None)` or a real record.

### 15. NIT (latent, class 32): `_isolate_output_state` does not reset `_JSON_MODE`
- Where: `conftest.py:124`.
- Defect: `_dispatch` resets `_JSON_MODE` per call, like the two flags the fixture resets, but an in-process `--json` `main()` leaves it True for the next test that calls internals (`die`, `_GuardedStdout.write`).
- Probed: forcing it True before every test left all 476 tests in 19 CLI test files green, so nothing depends on it yet.
- Fix: add it to the fixture.

## Doubts verified (`fix-cli.md` "Doubts")

- WS upgrade 502/504 stays 3: holds, and the owner ruled it (triage CLI-15).
  - REST 502/504 is exit 1 (driven through a canned transport: `lines` rc 1 for both), so the asymmetry stands by ruling.
  - SPEC 4's "as the same refusal over REST" is accurate only for other statuses.
- `wait` exits 1 on an ERR send even after a match: holds. The matched line is printed and the ERR goes to stderr; this matches the CLI-2 ruling (`test_cli_send_verdicts.py`).
- The port-column rule is two rules: holds.
  - The `lines` case (the newest N all from one board) is harmless.
  - The attached-ports rule misfires for history (finding 3).
- `fmt_line` escaping applies to pipes and files: holds; the docs say otherwise (finding 9).
- `send -` is refused, so a lone `-` cannot be sent: holds. `send -- -` is refused too, since the check is on the value. `POST /send` still can.
- LIFECYCLE-1 on Windows (the fallback signals the recorded shim): reasoned to work.
  - The CPython venv launcher puts its child in a kill-on-close job, so TerminateProcess on the shim ends the daemon. Not driven.
  - The three stop tests are `posix_only`, although nothing in them is POSIX-specific (`os.kill` is TerminateProcess, `pid_running` uses OpenProcess). Run them on the Windows leg.
- The repair warning prints beside `cannot write output` on POSIX: holds, driven (finding 11).
- PERF-3's budget assumption: holds.
  - `server.py:3013-3024` runs one `query_lines_safe` per pattern, each under its own `MATCH_BUDGET_S`, then an unbudgeted `count_lines_safe` over the same id range, which the +30 s covers.
- CLI-11 on Windows: `stdout_was_closed()` is always False there.
  - The repair first attaches or allocates a console, so the output is delivered and exit 0 is truthful.
  - Only when no console can be had does devnull swallow the output with exit 0. That sub-case is a class-14 gate on the invariant. SPEC 4 scopes the rule to POSIX; the owner may want to pick.

## Checked, nothing found

- Process-level state in the slice:
  - `_repaired_at_start` and `_OUT_FAILED` are reset by the fixture; `_report_key` has its own fixture.
  - `_ctrl_handler_ref` is Windows-only and set once.
  - `_GuardedStdout` caches `_tty` per wrapped stream, re-wrapped per capsys stream.
  - `cli_argv`, `cli_client`, `cli_daemonctl` and `render` hold constants only. `_JSON_MODE` is finding 15.
- `cli_argv._ATTACHED_P` matches `config.ALIAS_RE`; the value guard and degrade-to-no-hoist are unchanged; the repointed hoist tests pass.
- `_fetch_after`: `limit=0`, the stop conditions, the since_id advance, the `_pin_ceiling` reuse; class 44 holds (bounds are absolute before the walk).
- Match budget (`_get_rows`): 6 `/lines` request sites in `cli.py`.
  - 618, 632 and 704 go through `_get_rows`.
  - 689 (the ceiling), 736 and 795 carry no `match`.
  - The decoder prime reaches `_get_rows` through `_iter_pages_asc`.
- `assert` `allow_empty`: exempt from class 53. It is sent only when given, older daemons ignore unknown body fields (`_Body extra="forbid"` is new this round), and they never answer `empty`.
- The `wait`/`assert` `cmd_result` branches against the server verdict (`server.py:2968` fails a non-ok send). `cmd_err_text` is shared by `emit_cmd_result`.
- `visible()`: `mcu --help` on a pty (`script`) carries only SGR and no escaped text. `_stdout_untranslated` reconfigures in place and keeps the guard. `err_write` escapes only on a tty stderr.
- `Client.fail`: the CLI-18 dedupe of the aliases the daemon names, and the older-daemon fetch; the plot-channels hint rewrite.
- Revert-verified:
  - the B08 give-up clock test (`giveup_at = None` → `pass` is caught);
  - conftest `_no_child_crashed` (unmarking the crash test in `test_dirs_override.py` fails at teardown). All three `child_crash_expected` tests write to tmp dirs.
- New test files: every negative assertion has a positive control in the file.
  - Files: `test_cli_send_verdicts`, `test_cli_read_scope`, `test_cli_small_refusals`, `test_cli_transport_timeouts`, `test_cli_terminal_controls`, `test_render_line_breaks`, `test_cli_decode_rejected_defs`, `test_cli_follow_frames`, `test_cli_closed_stdio`.
  - `test_stdio`'s new assert is platform-conditional, not inert.
- SPEC 4 rows against code: `cmd`, `send`, `sysrq`, `tail`, `lines`, `wait`, `assert`, `purge`, `plot channels` and the exit-code paragraph agree. The `daemon` row is wrong only as in finding 1.
- The README, `host/README.md` and `CLAUDE_SNIPPET.md` hunks agree with the code on exit codes, `-p`, JSONL and `empty` (apart from finding 4).
- `AI_GUIDE` PITFALLS against the code: agrees, except `[port]` on `log export` (finding 3) and the stop promise (finding 1). `test_cli_contract.py`'s whole-token flag check passes.
- Other stall paths: `probe` (the `mcu daemon` subcommands) is exempt by design (ARCHITECTURE.md:120). `download`/`stream_text` go through `_daemon_errors`.
