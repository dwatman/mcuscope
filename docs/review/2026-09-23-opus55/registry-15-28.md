# Registry leg, classes 15-28

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3. Brief: `registry-brief.md`.
Classes complete: 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28.
Evidence (probes, logs, built artifacts): `~/tt-data/mcuscope-2026-09-24/registry-leg/15-28/`.

## Leg notes

- Tally: 14 classes, about 5,000 sites ruled; 76 findings, 0 HIGH, 20 MEDIUM, 56 LOW, plus O-1 (MEDIUM, outside class 18's scope, since fixed as R13-1).
- Swept by six parallel agents, one per class group (15/17/24, 16/18, 19/22, 20/21, 23/25/26, 27/28); their per-class files are assembled here verbatim, with each class's findings moved to the top.
  - Two agents reported the leg brief (one file in the repo) and their sub-brief (per-class files in scratch) as contradictory; the sub-brief was an intermediate step, and this file is the single output the leg brief names.
- Cross-agent contamination: the class 15 agent's `tools/webui_smoke.py` run landed on the class 16/18 agent's daemon (that is R15-1 itself). The 16/18 agent was told to disregard 21:39:46-51 +0900 in its capture.
- Coordinator spot-checks, re-driven or read at HEAD: R15-2 (five `import mcu_sim` sites), R15-8 (`ci.yml:103` pipeline), R17-1 (`resolve_db_path` returns `expanduser(raw)`), R22-1 (`bytes.fromhex('0a\t0b')` returns `b'\n\x0b'`), O-1 (`pidfile.py:143` under `except OSError` only), R20-3 (`store.py:3071` builds `ts < ? AND id < ?` under a floor), R28-1 (`test_store_writer.py:341-347` wraps both `wait_for` calls in `suppress(Exception)`).
- HEAD moved during the leg (f31ecd9 to 1a1251a, other legs' commits). Source changes: `pidfile.py:145` now catches `ValueError`, which fixes O-1 (filed as R13-1 by the classes 1-14 leg), and two `serial_link.py` comment edits. The sweeps below were run at f31ecd9; no other swept file changed.
- SPEC contradiction for the owner, under R25-2: SPEC 9.2 says "every window selector shows a chip" while a zoom stands, and the pinned F-23 test (`plots_pause_edge.test.mjs`) says a chart born live then is not on the zoom. Recommended: SPEC reads "every selector of a surface frozen on it".

## Findings

### Class 15

- **R15-1 MEDIUM** `tools/webui_smoke.py:176` (with `:85`, `:128`, `:137`).
  - Failure: when the harness's own uvicorn cannot bind `--port`, `_wait_ready(base)` still succeeds because some other daemon answers there, so every auto-check runs against that foreign daemon and reports PASS/FAIL for it.
  - The checks mutate it: `POST /cmd ping` goes to its only port's board, `POST /ports {"alias": "board2", ...}` replaces any port of that name (attach replaces an existing alias) and `DELETE /ports/board2` then detaches it.
  - The default `--port` is 8558, the user's real daemon, so a developer running the smoke with their daemon up drives commands at the real board and can detach a real `board2`.
  - The "is port already in use?" hint at `:178` only fires when nothing answers at all.
  - Confirmed: driven (by accident, see "Surprising" in class-24.md). `TMPDIR=<scratch> python tools/webui_smoke.py --no-wait --port 18616` printed `error while attempting to bind on address ('127.0.0.1', 18616): address already in use`, then ran 6 checks against the class 16/18 agent's daemon (`command box ... status=ok data='monitor 1 sim'`, `setup bar: attach + detach second sim - attached=True detached=True`), rc=1.
  - Fix shape: wait on `server.started` of its own uvicorn, or require `/status` `pid == os.getpid()`, before any check. Also class 82's shape (acting on a daemon without proof it is the local one).
- **R15-2 LOW** sdist `tests/` (`host/tests/test_sim.py:18`, `test_sim_tcp.py:20`, `test_serial_link_tx.py:13`, `test_sim_error_codes.py:9`, `test_break.py:80`).
  - Failure: the published sdist ships the whole test suite, but five modules `import mcu_sim`, which conftest finds in the repo's `tools/`, not in the sdist. Collection from the unpacked sdist stops: `Interrupted: 4 errors during collection` (`ModuleNotFoundError: No module named 'mcu_sim'`); test_break imports it inside a test, so that one fails at run time instead.
  - Five sibling files already use `from mcuscope import sim as mcu_sim`, which works in both trees.
  - Confirmed: driven. `uv build` of a `git archive HEAD` copy, sdist unpacked, `host/.venv/bin/python -m pytest --collect-only -q -p no:randomly` in it, rc=2.
  - Owner should pick: drop `tests/` from the sdist, or make it self-contained.
- **R15-3 LOW** `.github/workflows/release.yml:111-130`.
  - Failure: the artifact actually uploaded to PyPI gets a 4-name sentinel check: no size check, no `vendor/uPlot.min.css`, and nothing on the sdist, which is published too.
  - The exhaustive derived-list check (`ci.yml:149-219`) runs only on CI's own build of the branch push, a separate build that resolves hatchling on its own. So the complete check runs on a stand-in for the published file.
  - Confirmed: reasoned only (read both workflows).
  - Fix shape: move the `ci.yml` check into a script that both workflows run.
- **R15-4 LOW** (Windows owed) the daemon's serving path is not exercised through its console script anywhere.
  - Tests spawn `sys.executable -m mcuscope.daemon` (`tests/test_daemon_process.py:112`, and `mcu daemon start` itself at `mcuscope/cli.py:2616`).
  - Every automated `mcuscoped` wrapper run is `--version` (`test_scaffold.py:75`, `ci.yml:231`, `ci.yml:282`, `release.yml:178`), plus `--help` with stdout closed (`test_stdio.py:334`).
  - A user running `mcuscoped` directly on Windows goes through the `.exe` launcher, where the registry's origin bugs lived, and nothing starts it serving.
  - Confirmed on Linux by driving: the built wheel installed into a clean 3.10 venv, `mcuscoped -c <scratch toml> --sim` on 18615 served `/status` and `/ui/`, answered `mcu cmd ping` (`monitor 1 sim`), `mcu log export -o` wrote 246 lines, and SIGTERM stopped it.
  - Windows: owed.
  - Fix shape: in the Windows wheel-smoke job, start `mcuscoped.exe --sim --port N`, poll `/status`, then `mcu.exe daemon stop`.
- **R15-5 LOW** firmware shipped forms that no automated job builds.
  - `make arm-check` (`firmware/tests/Makefile:75`) is, per `host/tests/test_firmware_monitor.py:11-13`, "the only enforcement of SPEC 5.1's freestanding rules", and no CI job runs it. `ubuntu-latest` can install `gcc-arm-none-eabi`, so this is a CI gap, not a developer-box one.
  - `firmware/monitor/port_template/monitor_port_template.c` is compiled by no test, and its six `#if 0` shim blocks drift silently against `monitor.h`.
  - Confirmed as currently compliant by driving: arm-none-eabi-gcc 13.3 (`-Werror -mcpu=cortex-m4 -ffreestanding`) compiles `monitor.c` and `monitor_cmds.c`. The template compiles with its six blocks switched to `#if 1` in a scratch copy, with host gcc and arm gcc. It also links with host gcc against `monitor.c`/`monitor_cmds.c` (no duplicate symbols against the weak defaults).
- **R15-6 LOW** `host/contrib/config.example.toml` is a shipped file (in the sdist; the file users copy), and no test loads it. A renamed key would ship an example that warns or refuses.
  - Confirmed compliant today by driving: `config.load_config(Path('contrib/config.example.toml'))` returns a full Config and logs no warning.
- **R15-7 LOW** `tools/webui_smoke.py:162` `tempfile.mkdtemp(prefix="webui-smoke-")` is never removed, so every run leaves a capture database in the system temp dir.
  - Confirmed: driven; the dir remained under the scratch `TMPDIR` after exit.
- **R15-8 LOW** (latent) `.github/workflows/ci.yml:103`, the "web UI JS tests actually ran" guard.
  - Failure: `if grep "SKIPPED" pytest-report.txt | grep -q "test_webui_js"` runs under the `shell: bash` step's `-eo pipefail`. Once the SKIPPED lines outgrow the pipe buffer, `grep -q` exits at an early match and the producer dies of SIGPIPE. The pipeline then fails, the `if` is false, and the guard passes silently.
  - Confirmed: driven with synthetic reports under `bash --noprofile --norc -eo pipefail`, webui line first:
    - 32 KB: fired 20/20.
    - 65 KB: silent 2/20.
    - 86 KB: silent 6/20.
    - 162 KB: silent 20/20.
  - Today's ~35 skip sites stay far below that, so it cannot fire now.
  - The firmware guard at `:95` is safe: its middle grep consumes all input and writes only the few matching lines.
  - Fix: `grep -q 'SKIPPED.*test_webui_js' pytest-report.txt`.
- **R15-9 LOW** (nit) `host/tests/test_scaffold.py:78` docstring: "The rest of the suite drives the CLI as `python -m mcuscope.cli`". This is false since `tests/test_cli.py:33` (`_mcu_command` uses the console script), and it contradicts `docs/ARCHITECTURE.md:144`.

### Class 16

**R16-1 MEDIUM** `host/mcuscope/cli_output.py:429` (`LineDecoder._fields`), reached from `cli.py:853` (`_decode_pages`), `cli.py:1217` (`_follow_ws.handle`), `cli.py:970`, `cli.py:1035`, `cli.py:1970`/`1993`.
- `_fields` walks `sample.points` with `next(points)` in lockstep with `definition.channels`. `protocol._decode_plot_sample_tokens` drops a non-finite analog point (SPEC 2.5: "drops that point only"), so the iterator is one short and `next()` raises `StopIteration`.
- Input: `!pd 0 a:f4 b:f4` then `!ps 0 2 3F800000,7F800000` (b = +inf; a NaN sensor reading is the realistic source). Any typed stream with an f4 channel carrying NaN/inf hits it.
- One-shot (`mcu lines --decode`, `mcu log export --decode [-o F]`, `mcu tail -n N --decode`): the generator turns it into `RuntimeError: generator raised StopIteration` (PEP 479), a traceback, a crash log, exit 1, and every row after the bad sample is lost (with `-o` the file is discarded).
- Follow (`mcu tail -f --decode`): the per-row guard names `(AttributeError, KeyError, TypeError, ValueError)`, so the `StopIteration` escapes it and the coroutine turns it into `RuntimeError: coroutine raised StopIteration`; the follow ends, exit 1, crash log, and the next line (`after`) is never printed.
- Confirmed by driving: unit call `LineDecoder().decode(...)` raised `StopIteration`; then an isolated daemon (port 18616, scratch config and data dirs) over a capture built with `Store.add_line`: `mcu lines --decode`, `mcu log export --decode`, `mcu log export --decode -o F`, `mcu tail -n 10 --decode` all rc=1 with the RuntimeError; `mcu tail -f -n 0 --decode --match '^(!ps|after)'` against a live `mcu-sim --tcp-port 18617` port, fed with `mcu -p b send '!ps 0 2 3F800000,7F800000'`, printed the good sample, then rc=1 `coroutine raised StopIteration`, crash log written, `after` never printed. (All runs 21:35-21:37, before the foreign rows the class-15 agent reported at 21:39:46-51.)
- The web UI decoder (`plots.js:239`) keys points by name, so it is not affected; the daemon's `/plot/export` renders stored points by name too.

**R16-2 LOW** `host/mcuscope/cli.py:2280` (`_poll_new_frames`) with `cli_output.py:113` (`_list_field`).
- `can dump -f` guards each poll (`httpx.HTTPError, KeyError, TypeError, ValueError`, charged to `polls`, give-up after 30 s) and each frame (`KeyError, TypeError, ValueError`, charged to `frame_drops`). But each page goes through `_list_field`, which calls `die()` (typer.Exit) on a `frames` that is not a list or holds one non-object entry. typer.Exit is in neither tuple, so one malformed poll ends the follow with exit 1 and loses the good frames in the same page; the per-frame TypeError arm for a non-object frame is unreachable.
- Confirmed by driving: a stdlib HTTP stub (port 18618) answering polls `[good(5), {"line_id": 4}]` then `[good(7), "junk"]`: the first was charged (`warning: skipping bad frame: 'ext'`, frame 5 printed), the second ended the follow, rc=1, `unexpected response from daemon: 'frames' has non-object entries`, frame 7 never printed.
- Needs a non-conforming daemon; the shape (a whole-exit check in front of a per-item guard) is the defect.

**R16-3 LOW** `host/mcuscope/cli.py:714` (`_iter_pages_asc`).
- The walk continues only while the LAST row of a page has an integer id; otherwise it returns as if the window were exhausted. Used by `mcu log export --decode/--names/--changes` and the decoder priming.
- Confirmed by driving: stub (port 18628) answering page 1 `[id 1, id "2"]` with `truncated: true`, page 2 `[id 3, id 4]`: `mcu log export --decode --limit 0 -o F` exited 0, printed `wrote 2 lines`, the file held lines 1-2 only; page 2 was never requested. Silent truncation reported as complete (the CLAUDE.md "never cut captured data silently" rule).
- The web UI's backfill avoids this with a min-id scan (`api.js:450 oldestId`, comment: "a malformed row must not decide where the next page starts"); `_fetch_after` and `_fetch_newest` at least keep `truncated` true.

**R16-4 LOW** `firmware/monitor/monitor_cmds.c:270` (`cmd_i2c_scan`).
- The probe loop reads every non-zero `mon_i2c_xfer` result as "no device at this address" and keeps going. `MONITOR_ERR_NOSUP` (the weak default, i.e. every project without an I2C shim that did not pass `-DMON_NO_I2C`) and `MONITOR_ERR_BUSERR`/`TIMEOUT` are not per-address answers, yet the scan answers `OK`.
- SPEC 1266 and 1394 say a family without a shim answers `ERR 7 nosup` "as an unimplemented shim does"; `i2c rd` on the same build does, `i2c scan` does not. This is the registry's mirror question ("a guard that keeps looping must still recognise the errors that are not per-item") in C.
- Confirmed by driving: `scratch-16-18/fw/probe.c` compiled with `cc -std=c11 -Wall -Wextra` against `monitor.c`/`monitor_cmds.c`: weak shims -> `<1 OK` (empty list) beside `<2 ERR 7 nosup` for `i2c rd`; a shim answering 0 at 0x48 and BUSERR elsewhere -> `<1 OK 48`, the bus errors invisible. `test_families.c:83` covers only `MON_NO_I2C` (NOSUP) and the fake shim, not the weak default.

No other violations among the sites below.

### Class 17

- **R17-1 MEDIUM** `host/mcuscope/config.py:156` (`resolve_db_path` returns `os.path.expanduser(raw)`, not absolute), surfaced at `host/mcuscope/server.py:1069` (`/status` `db_path`), in `mcu status` (`db <path>`) and in the startup banner `database: <path>` (`daemon.py:197`).
  - Failure: a relative `storage.db_path` is reported as the configured string, not as the file the store opened (the daemon's CWD joined to it).
  - `mcu daemon restart` spawns the new daemon from the CLI's CWD, while it deliberately forwards `config_path` absolute. A restart from another directory therefore opens a different, empty capture. The old one is orphaned, and every surface reads the same before and after.
  - Confirmed: driven with the installed wheel, config `db_path = "rel.db"`:
    - `mcu daemon start` from dir A: `/status db_path='rel.db' capture=a1e037d0...`.
    - `mcu daemon restart` from dir B: `/status db_path='rel.db' capture=7a33d9c2...`.
    - Both A and B now hold `rel.db` and `rel.db.lock`.
  - Clients do see a new capture id, so they re-seed, but nothing says why or where the old data is.
  - SPEC 3.3 is silent on relative `db_path`. Owner should pick: resolve it at load (against the config file's directory or the startup CWD) and report it absolute, as `config_path` already is, or refuse a relative one.
- **R17-2 LOW** `host/mcuscope/daemon.py:494-502`.
  - Failure: `PlotJuggler: streaming plot points to <dest> (--plotjuggler)` is printed from the requested config before the lifespan tries to enable the stream (`server.py:448-454`). A destination that fails to resolve, or that `_resolve` refuses (multicast, unspecified), leaves the stream off while stdout says it is streaming.
  - Confirmed: driven. Wheel `mcuscoped --sim --plotjuggler nosuch.invalid:9870`:
    - stdout: `PlotJuggler: streaming plot points to nosuch.invalid:9870 (--plotjuggler)`.
    - `/status`: `{'enabled': False, 'dest': 'nosuch.invalid:9870'}`, with `config_warnings` `plotjuggler: cannot enable ...`.
  - Not silent overall (the stderr warning and `config_warnings` carry it), but the one line addressed to the user asserts the request.
  - Fix shape: print the line after the enable, or word it "requested".
- **R17-3 LOW** `host/mcuscope/server.py:1108`, `server.py:1391`, `server.py:1411` (`pj.dest`).
  - Failure: `/status`, `GET /plotjuggler` and the `PUT /plotjuggler` reply report the destination string asked for. The address datagrams actually go to (`pjstream.py:74`, first `getaddrinfo` result, resolved once at enable) appears only in an INFO log line.
  - A hostname whose first result is `::1` while PlotJuggler listens on IPv4, or a host whose address changed after enable, reads as healthy with nothing arriving.
  - Confirmed: partly driven. `configure(True, 'localhost:9870')` reports `localhost:9870` with the target `('127.0.0.1', 9870)`, and nothing exposes the target. On this host localhost resolves to IPv4 only, so the divergent case is reasoned, not reproduced.
- **R17-4 LOW** (Windows owed) `host/mcuscope/cli.py:2728`, `cli.py:2730`.
  - Failure: `mcu daemon start` reports `started mcuscoped (pid {proc.pid})` and `--json` `"pid": proc.pid`, the process it spawned. On Windows that is the venv launcher, which `/status` reports as the daemon's `ppid` (SPEC 3.4, line 694). The serving `pid` is in hand at that point (`body`) and is not what is printed.
  - A user acting on the printed pid (`taskkill /PID`) targets the launcher.
  - Confirmed: reasoned only; on Linux the two are equal (driven: `started mcuscoped (pid 299204)` equalled `/status` pid 299204).
  - Owner should pick: the SPEC documents the recorded pid as the launcher, but not which pid the start line names.

### Class 18

**R18-1 LOW** `host/mcuscope/cli.py:1191` (`_follow_ws`: `regex.compile(match)` under `except regex.error`).
- `regex.compile` raises `RecursionError`, not `regex.error`, for deep group nesting: measured thresholds 331 nested `(` (662 chars), 199 `(?:`, 248 `(?=`. Every daemon compile site (`server.py:2628`, `:2990`, `:3347`) checks `MAX_MATCH_LEN` = 200 first, which keeps it below the threshold; the CLI follow has no cap.
- Confirmed by driving: `mcu tail -f -n 0 --match "$(python3 -c 'print("("*400+")"*400)')"` -> rc=1, `RecursionError: maximum recursion depth exceeded` traceback, `mcu-crash.log` written. The same pattern on `mcu tail -n 5`, `mcu lines` and `mcu wait` -> rc=1 `error: match regex too long (max 200 chars)` from the daemon.
- The registry's own "Bit" line (`re.error` reached the user as a traceback) in a new type.

**R18-2 LOW** `host/mcuscope/cli.py:1254` (`websockets.connect` in `_follow_ws.run`).
- An unparseable port or host in `--url` raises `ValueError` from inside `websockets.connect` (its `urlsplit().port`); the handler list's frame clause `except (json.JSONDecodeError, ValueError)` catches it and reports `malformed frame from daemon` with exit 1. The httpx siblings map the same url to `bad daemon url` exit 3 (`die_bad_url`, SPEC 4).
- Confirmed by driving, `mcu --url U status` / `tail -f -n 0` / `can dump -f -n 0`:
  - `http://127.0.0.1:99999`: 3 / **1 `malformed frame from daemon: Port out of range 0-65535`** / 3
  - `http://[::1`: 3 `bad daemon url` / **1 `malformed frame ...: Invalid IPv6 URL`** / 3
  - `http://127.0.0.1:abc`: 3 / **1** / 3; `http://127.0.0.1:18616 x`: 3 / **1** / 3
  - `ftp://...` and `http://` agree (3 on all three).
- The registry's own "Bit" line (`urlsplit().port` raises `ValueError`), at the one boundary the httpx fix did not reach.

**R18-3 MEDIUM** `host/mcuscope/update_check.py:186` (`UpdateChecker._load_cache`).
- The handler `(OSError, ValueError, KeyError, TypeError)` misses `OverflowError`: `float(data["checked_at"])` on a JSON integer past the float range. `_load_cache` runs in `UpdateChecker.__init__` whatever `[update] check` says, inside the lifespan.
- Confirmed by driving: `update.json` = `{"latest": "0.1.0", "checked_at": 1` + 400 zeros + `}` in a scratch `MCUSCOPE_CACHE_DIR`, config `check = false`, port 18626: `mcuscoped` exits rc=3, `OverflowError: int too large to convert to float`, `Application startup failed`. The docstring promises "missing or corrupt: simply means the next check happens now", and disabling the check does not help.
- MEDIUM for the impact (no capture at all, an error naming neither the file nor the fix); the trigger needs a hand-edited or foreign-written cache.

**R18-4 LOW** `host/mcuscope/serial_link.py:1168` (`_break_locked`), with `link.py:187` and `server.py:1815` (POSIX only).
- pyserial's POSIX `send_break` is `termios.tcsendbreak`, which raises `termios.error` (MRO: error, Exception; neither OSError nor SerialException). `_break_locked` maps `(serial.SerialException, OSError)`; `/break` catches `PortError`. So a break on a vanished adapter or a driver without break support reaches the global handler: HTTP 500 `{"error": "(5, 'Input/output error')"}` with no port named and an "unhandled error" traceback in the daemon log, where its sibling `_write_bytes` answers 400 `port X: ...`.
- Confirmed by driving at the function the endpoint awaits: a pty opened with `serial.Serial`, master and slave fds closed, then `SerialPort._break_locked(0.01)` on a `SerialLink` over it: `termios.error (5, 'Input/output error')` escaped (healthy pty: no raise). The endpoint's 500 is reasoned from `server.py:1815` and `_unhandled_error`, not driven (the reader thread races the hang-up into a disconnect first).

**O-1 MEDIUM (outside class 18's library scope; classes 7 and 13 territory)** `host/mcuscope/pidfile.py:143` (`read_pid_record`).
- `open(path, encoding="utf-8")` under `except OSError` only: a record holding non-UTF-8 bytes raises `UnicodeDecodeError`. Its sibling `lockfile._read_holder` maps `UnicodeDecodeError`, `pid_running` reads with `errors="replace"`, and the docstring treats the record as hand-editable and promises None for anything not a pid.
- Confirmed by driving with `\xff\xfe12\n` at `mcuscoped-127.0.0.1-18627.pid` in a scratch data dir: `mcu --url http://127.0.0.1:18627 daemon stop` rc=1 with a `UnicodeDecodeError` traceback and crash log; `mcuscoped` on that port rc=1, same error out of `pidfile.claim` (`daemon.py:475`), crash log. `mcu daemon status` answered `not running` (rc 3) cleanly.

No other violations among the sites below.

### Class 19

- **R19-1** LOW `host/mcuscope/webui/state.js:261` (markerTick, reached from `computeTick` at :243) and `host/mcuscope/webui/terminal.js:99` (divider text)
  - Failure: the browser decodes every `chan === "marker"` row as a firmware `!m` line, but the daemon parses `!m` only on rx lines (`serial_link.py:939`); host markers from `POST /marker` (`server.py:2179`, dir `-`, raw = user text) are never parsed there.
  - Input: `mcu mark "!m @4000000000 hello"` (or the web UI marker box). The web UI gives that row tick 4000000000, which can become the sticky `state.anchorTick` (`state.js:285`) if it is the first tick-bearing row, and shows it as `marker: hello` with the text the user typed cut off. The daemon, CLI and exports treat it as tickless text `!m @4000000000 hello`.
  - The mirror is missing one clause: the daemon's `dir == "rx"` precondition. Fix shape: gate markerTick and the strip on `row.dir === "rx"`.
  - Confirmed: driven. `scratch-19-22/probe_intfield.mjs` imports the real `state.js` under `tests/webui_js/dom_stub.mjs`: `lineTick({chan:"marker", dir:"-", raw:"!m @4000000000 hello"})` returned 4000000000.
- **R19-2** LOW `host/mcuscope/webui/pane.js:57` (`SAME_LETTER_ESCAPES`) and `terminal.js:587` (`new RegExp(src, "s")`) against `store.py:453` `_make_regexp` (the `regex` module)
  - Failure: the dialect check treats `\d \D \w \W \s \S \b \B` as identical in both engines. That holds only for ASCII subject text. `regex` on a str pattern is Unicode-aware; ECMAScript without the `u` flag is not.
  - Non-ASCII text does reach the capture. rx lines are decoded ASCII-with-replacement (`serial_link.py:810`), but marker rows carry arbitrary Unicode: `POST /marker` text (1..4096 chars), plus session names and notes in the `session start:` marker.
  - Driven differences (`scratch-19-22/dialect_chars.py` through the real `_make_regexp`, then node's RegExp with flag `s`):

    | Pattern | Subject | daemon | pane |
    |---|---|---|---|
    | `\d` | `٣` | match | no match |
    | `\w` | `é`, `٣` | match | no match |
    | `\s` | U+0085 | match | no match |
    | `\s` | U+FEFF | no match | match |
    | `^.$` | U+1F600 | match | no match |
    | `\D`, `\W` | (the inverses) | | |

  - End to end on a throwaway daemon: posting marker `reading ٣`, `GET /lines?match=\d&chan=marker` returned it. A pane filtered on `\d` hides that row live, while the same pane's history pages (`terminal.js:522` sends `match=`) and its export return it. So one pane can show a row in one part of its view and not another.
  - `tests/regex_dialect_cases.json` "same" lines contain no non-ASCII digit or letter, so its "must agree" test cannot see this.
- **R19-3** LOW: marker text is validated differently by each entry point
  - Web UI `cmdbar.js:263` trims the text and refuses a blank one.
  - CLI `mcu mark` (`cli.py:572`) and `POST /marker` (`server.py:307`, `MarkerBody.text` min_length=1, no strip) store the text verbatim, whitespace-only included.
  - `SessionBody.name` beside it strips before its length check (`server.py:316`).
  - Driven: `mcu mark "   "` answered `marker 37237` and `GET /lines?chan=marker` returned raw `'   '`. The UI refuses the same input, and turns `  x  ` into `x` where the CLI keeps `  x  `.
  - SPEC 3.5 `/marker` states only "1..4096 characters"; SPEC 2.5 requires "at least one non-space character" for a wire `!m`. Owner should pick which rule holds for a host marker. I have not changed or assumed either.
- R22-1 (filed under class 22) is also a class 19 miss. The web UI's `!can` and `!ps` decoders, and the firmware's `mon_hex_decode`, refuse hex tokens carrying tab, VT or FF. The daemon's and simulator's shared `protocol.hex_to_bytes` / `_decode_field` accept them through `bytes.fromhex`. So `GET /can/frames` and `mcu can dump` show a frame the CAN table does not have. Counted once, in class 22.

### Class 20

**R20-1 MEDIUM** `host/mcuscope/store.py:2654-2656` (`first_export_line_id`) and `store.py:2619-2623` (`export_sids`), reached from `server.py:2109`, `server.py:2097` (`GET /plot/export`) and `server.py:1658` (session bundle).
- With two or more names plus `port`, the planner drives the join from `lines` by `idx_lines_port_id`, then sorts every match through a temp b-tree before `LIMIT 1`: the class's own `/can/frames?port=` shape.
  Plan: `SEARCH l USING INDEX idx_lines_port_id (port=? AND id>? AND id<?) | SEARCH pp ... (name=? AND line_id=?) | USE TEMP B-TREE FOR ORDER BY`. With one name it is `SEARCH pp USING COVERING INDEX idx_plot_name_line` and stops at the first row.
- 1M-line capture, no `sqlite_stat1`, busy port A (89% of rows), `since_ts` at the capture start: `first_export_line_id` 2712-3702 ms for `names=temp,volt` against 0.03 ms for `names=temp`; `export_sids` (wide) 3461 ms with `since_ts`+`until_ts`. Bundle shape (id range only, 600k-row session): 1544 ms against 0.1 ms.
- End to end (`e2e20.py`, TestClient on the 1M capture, `/plot/export?port=A&since_ts=...&decode=1`): one name 2.7 s (long) / 2.5 s (wide); adding a second name with 5 points takes it to 7.0 s / 7.5 s, repeated 6.6 s / 7.3 s.
- The web UI always sends several names plus `port` (`webui/exportdlg.js:197-209`, `webui/digital.js:483`), so this is the shipped default export. Off the loop (a read worker), so it delays the export and holds a worker rather than freezing the daemon.
- Without `port`, two names still sort every point of both names before `LIMIT 1`: 170-240 ms against 0.03 ms for one name (the same statement, same fix).
- Confirmed: driven (`bulk20d.py`, `bundle20.py`, `e2e20.py`; plans on three SQLite versions in `plans_ver.py`). Probe `probe20.py` first surfaced it on a 171-row capture: plans do not need bulk data, only a list-valued `names` with two elements.

**R20-2 MEDIUM** `host/mcuscope/store.py:2240` (`query_can_frames`) polled by `host/mcuscope/cli.py:2181` / `cli.py:2278` (`mcu can dump -f`).
- The follow starts with `since = 0` and advances it only when a frame arrives, so a follow scoped to a port (or bus) that has no frames, or whose last frame is old, sends `since_id=0` (or the stale id) every `FOLLOW_POLL_S` = 0.2 s.
  Plan: `SEARCH cf USING INTEGER PRIMARY KEY (rowid>?) | SEARCH l USING INTEGER PRIMARY KEY (rowid=?)` with `l.port = ?` read per row: a lower bound only, walking every frame above the watermark.
- 1M lines / 100k frames: 94.5 ms per poll for `port=B` (no frames), 87 ms with `since_id=5`, 14 ms for `bus=2` with no frames; 0.07-0.13 ms once the watermark is recent.
- Driven end to end (`follow20.py`): a throwaway `mcuscoped` on the 1M capture (port 18621), `mcu --port B can dump -f` for 6 s cost the daemon 1.88 s and 2.00 s of CPU (30-32% of a core, continuous); `--port A` 0.08 s (1%); idle 0%. Scales linearly with frames stored.
- Not the loop (query_can_frames_safe offloads), but a permanent load on the read pool for as long as the follow runs.

**R20-3 MEDIUM** `host/mcuscope/store.py:3071-3076` (`_delete_expired_chunk`), run on the event loop by the hourly age sweep (`store.py:3090-3099`).
- With a floor (`min_sessions`, default 5), expired rows at or above the floor are protected, and `SELECT id FROM lines WHERE ts < ? AND id < ? ORDER BY ts LIMIT ?` walks every one of them through `idx_lines_ts` to find nothing: the `SEARCH ... (ts<?)` plan hides the range length, exactly as the entry warns.
- This is the default steady state of any daemon run longer than `retention_days` (auto sessions are on by default, so the current run's own session is protected).
- 1M-line capture, 500,203 rows older than a 10-day cutoff, all inside one protected session (`sweep20.py`): each sweep deleted 0 rows in 70.9 / 75.7 / 82.3 ms; `sweep_tick(60)` measured a longest loop gap of 89.7 ms. Linear in protected-expired rows, so about 1 s per hour at the 6M-line captures the registry quotes.
- Same magnitude as the defect this function's docstring records as fixed (the nothing-expired case, 45 ms at 300k on the loop); the plan pin `test_store_lines_plan.py:552` asserts the index name only, which this case satisfies.
- Confirmed: driven (`sweep20.py`, `bulk20.py`).

**R20-4 LOW** `host/tests/test_sessions.py:903-910` and `host/tests/test_store_lines_plan.py:500-506`: plan tests that explain a hand-written copy, against the entry's "explain the statement the daemon issues".
- `test_sessions.py:906` explains `SELECT id FROM sessions WHERE name = ?...`; the daemon issues `SELECT {_SESSION_COLS} FROM sessions WHERE name = ? ORDER BY id DESC LIMIT 1` (`store.py:1415`). A change to the real statement passes this test.
- `test_store_lines_plan.py:503` re-types `_window_id_floor`'s SELECT (`store.py:1918`) because `captured_plan` returns only the last statement; identical text today, free to drift.
- Confirmed: reasoned (read and compared against the trace in `trace20.json`).

**R20-5 LOW** `host/tests/test_store_lines_plan.py:306-331` pins the plan of `Store.query_plot_channels` (`store.py:2264-2312`), which no handler reaches.
- `GET /plot/channels` goes through `query_plot_channels_safe`, which serves from the summary and rebuilds it with `_scan_plot_rows` (`store.py:2397-2437`); `grep -rn "query_plot_channels(" host/mcuscope` has no caller. The probe's 13,000 requests never issued `store.py:2312`.
- The registry text for this class ("`GET /plot/channels?port=` was the same shape") now points at code the daemon does not run; the daemon path is pinned by `test_store_plot_summary.py:84-103` instead.
- Confirmed: grep plus the dynamic trace (site absent from `templates20.json`).

### Class 21

**R21-1 MEDIUM** `host/tests/test_e2e.py:606-613` (`test_lines_since_ts_excludes_what_predates_it`).
- `cut` is the middle row's own `ts`, which is data-derived, but the test then needs some *later* row with a strictly greater stamp. The reader stamps a whole read burst with one `ts` by design (`test_reconnect.py:598` pins that), and on a 15.625 ms clock the sim's next bursts land in the same tick, so `since_ts=cut` (strict `>`) can exclude every row: `assert newer` fails with `[]`.
- Driven: under the emulated clock, 4 failures in 21 isolated runs of the test and 3 in 10 runs of the whole file; without it, 0 in 17 isolated runs and 0 in 3 file runs.
- Fix direction: derive the cut from two rows with distinct stamps, or write rows with explicit `ts` values instead of reading the sim's.

**R21-2 LOW** `host/tests/test_assert.py:127-143` (`test_last_ms_window`).
- The spin past `old_ts + 30 ms` is right, but `recent` is then written *before* the `wide` and `narrow` `/assert` calls, so the third call's 30 ms window (anchored at request time, `store.py:1766`) must still reach back over two full round trips to `recent`'s stamp.
- Driven (`lastms21.py`): the gap from `recent`'s stamp to the third call's floor is 5.3-8.5 ms (median 6.7, 20 runs) on this Linux desktop. On a 15.625 ms clock the test fails once the two stamps fall two ticks apart, which starts at a 15.6 ms gap and is certain past 31.25 ms; on any clock it fails past 30 ms. The in-repo record of a 0.45 s Windows round trip (`test_serial_link_devices.py:82-86`) is far past both.
- Passed all five emulated runs here only because this machine's gap stays under one tick.

**R21-3 LOW** `host/tests/test_wait_repeat.py:326-332`: `elapsed < 0.1` over an HTTP round trip to the stack.
- The claim ("refused before the first write, not after the window") is countable: spy `send_raw` and assert it was never called. The 0.1 s budget is a quarter of the round trip `test_serial_link_devices.py:82-86` records on a Windows box.
- Confirmed: reasoned (passes here and under the emulation; the failure needs a slow runner).

**R21-4 LOW** `host/tests/test_wait_repeat.py:350-369`: `max(gaps) < 0.2` after the stall.
- One loop stall of 200 ms anywhere in the 600 ms window fails it; the registry's own precedent is a 220 ms consumer stall on a loaded runner. The same test already counts writes (`len(starts) >= 5`, `<= 25`), which is the class's prescribed form; the gap floor adds a wall-clock threshold back.
- Confirmed: reasoned.

**R21-5 LOW** `host/tests/test_serial_link_devices.py:80-87`: `/status` must answer within 1.2 s while a fake 2 s scan runs.
- Widened once already after a Windows failure (0.4 s against a 0.6 s scan); 1.2 s against a recorded 0.45 s round trip leaves 0.75 s. The claim (the loop is not blocked) can be made clock-free: hold the fake scan on an Event that the test sets only after `/status` has answered.
- Confirmed: reasoned.

**R21-6 LOW** promptness thresholds with about 1 s of headroom over the healthy path.
- `test_reconnect.py:110`: `elapsed < 1.5` where the healthy path is the 0.3 s replug plus one 0.25 s poll (`serial_link.py:56`); the alternative it guards against is the 5 s interval.
- `test_reconnect.py:146`: `elapsed < 1.0` where the healthy path is the 0.1 s stop; the alternative is the 30 s interval.
- `test_e2e.py:544`: `(loop.time() - t0) < 1.0` where the healthy path is a few ms; the alternative is the 2 s timeout.
- Each could keep its discrimination with a budget several times larger (widen the interval or timeout it is measured against). Confirmed: reasoned; all three passed under the emulation.

### Class 22

- **R22-1** MEDIUM `host/mcuscope/protocol.py:165` (`hex_to_bytes`) and `protocol.py:861` (`_decode_field`)
  - Cause: `bytes.fromhex` skips ASCII whitespace (tab, LF, VT, FF, CR, space; checked on 3.13.5 and 3.10.20) anywhere between byte pairs. SPEC 2.1 requires hex data to be "hex pairs with no separators", and a tab is part of its token.
  - (a) An rx `!ps` line whose f4 field carries tabs (`!ps 0 3 0001,\t\tABCDEF` against `a:u2 b:f4`) makes `_decode_field` hand 3 bytes to `struct.unpack(">f")`.
    - The resulting `struct.error` is not a `ValueError`, so it escapes `PlotDecoder.feed`.
    - `serial_link._store_rx_batch` (`serial_link.py:881`) then drops the whole line from the capture (`rx_dropped` +1, a `dropped an rx line` sys row), where SPEC 3.5 says an undecodable plot line is stored as a generic event.
  - (b) An integer field of the right character width decodes a tab-padded token as fewer bytes: u2 `\t\tAB` stores the point `a=171`.
  - (c) A `!can` payload `DE\tAD\t` (or `\x0b\x0cDEAD`) stores a `can_frames` row with dlc 2.
    - The web UI's decoders refuse all of (a)-(c), so `GET /can/frames` and `mcu can dump` show a frame the CAN table never has, and `plot_points` has samples the charts do not (a class 19 miss as well).
  - (d) The simulator shares `hex_to_bytes`, so it answers `OK` to `can tx 100 DE\tAD\t`, `can tx 100 \x0cDEAD\x0c` and `spi xfer imu \tAB\x0b`, where the firmware's `mon_hex_decode` answers `ERR 2 badarg`.
  - (e) Latent, reasoned only: the CLI's `LineDecoder` (`--decode`) shares the same raise, and `_follow_ws` guards only `(AttributeError, KeyError, TypeError, ValueError)` (`cli.py:1242`). No stored row reaches it, because the daemon drops the line that would.
  - Confirmed, all driven:
    - `scratch-19-22/probe_fromhex.py` through `SerialPort._store_rx_batch` and a real Store.
    - End to end on a throwaway daemon, with `fake_board2.py` as a `socket://` device: `mcu -p fake2 lines` shows the `!ps 0 3` line missing, `/status` shows `rx_dropped 1`, and `mcu can dump` shows `id=100 dlc=2 data=DEAD`.
    - `probe_js_decoders.mjs`: the real `can.js`/`plots.js` return null for all three lines.
    - `fw_hex_probe.c`, linked against `monitor.c`: `mon_hex_decode` returns -1 for all three.
    - `Simulator.handle_line` for (d).
- **R22-2** LOW: every numeric option and argument of the `mcu` CLI, 33 params (list in sweep G), e.g. `cli.py:2040` `--rtr`, `cli.py:2332` `i2c rd N`, `cli.py:935` `--limit`
  - Cause: typer/click's INT and FLOAT types are bare `int()`/`float()`, so other scripts' digits, `+`, `_` grouping and padding are all accepted and rewritten.
  - Driven against a throwaway daemon:

    | Typed | Sent on the wire |
    |---|---|
    | `mcu i2c rd 48 ٣` | `>4 i2c rd 48 3` |
    | `mcu i2c rd 48 1_0` | `>5 i2c rd 48 10` |
    | `mcu can tx 100 --rtr ٣` | `>3 can tx 100 3 r` |

  - The firmware would answer `badarg` to the typed token. `mcu lines --limit ٣`, `tail -n ٣` and `cmd ping --timeout ٣٠٠٠` were likewise all accepted.
  - The daemon's own argv uses `protocol.int_arg`; the CLI does not.
- **R22-3** LOW `host/mcuscope/cli_daemonctl.py:149`: `MCUSCOPE_START_TIMEOUT` is read with `float()`.
  - `٣` gives 3.0 and `1_0` gives 10.0.
  - An unparseable value (`abc`) falls back to 20 s silently, with no warning naming the variable.
  - Driven with `_start_timeout_default()` under each value.
- **R22-4** LOW: the daemon's URL query and path parameters, 35 int/float/bool params (list in sweep H), e.g. `server.py:1522` `DELETE /sessions/{session_id}` and `server.py:1848` `/lines?limit=`
  - Cause: request bodies are `strict=True` (`server.py:210`), but query and path params use pydantic's lax parsing.
    - Accepted: `+2`, ` 3 `, `1_0`, `3.0`, a thin-space pad, and bools `yes/on/t/1`. U+0663 is rejected (pydantic-core).
  - Driven on a throwaway capture:
    - `DELETE /sessions/+2` deleted session 2.
    - `DELETE /sessions/%203%20?data=yes` deleted session 3 and the lines it covered.
    - `/lines?limit=1_0` answered 10 rows.
  - One endpoint is destructive, which is why this is worth a line despite the low odds of typing it.
- **R22-5** LOW `host/mcuscope/webui/state.js:198` (intField)
  - Cause: `Number()` accepts `0x3E8`, `0b1111101000`, `0o1750`, `1e3`, `+1000`, `1000.0` and any Unicode whitespace padding, all as 1000.
  - Two fields are plain text inputs where this is reachable as typed: `cmdTimeout` (`index.html:128`) and `baudCustom` (`index.html:158`). `0x3E8` becomes a 1000 ms command timeout, and `0x1C200` becomes 115200 baud.
  - It correctly rejects `٣`, `1_000` and `Infinity`.
  - Driven: `probe_intfield.mjs` on the real `state.js`.
  - The `type=number` inputs are presumably filtered by the browser first (owed, below).
- **R22-6** LOW: `str.strip()`'s whitespace set stands in for SPEC 2.1's U+0020-only separator
  - Rx side, `protocol.py:1121` (`parse_marker`): `!m @5 \x1f`, `!m \t`, `!m \x1c\x1d` and `!m @5 \x0b` return None (stored as generic events). SPEC 2.5 asks for "at least one non-space character", and SPEC 2.1 makes tab and 0x1C-0x1F token bytes, not separators.
  - Firmware `monitor_mark` treats only space and tab as blank. The three sides use three sets.
  - Tx side, `protocol.py:297` (`format_command`, called at `serial_link.py:1194` on every `/cmd`): `cmd.strip()` silently turns `ping\x1f`, `\x0bping`, `ping\x0c`, `ping\x85` into `>7 ping`. The firmware would have read the first as token `ping\x1f` and answered badcmd.
    - The tx row stores what was really sent, so the capture is honest; the user's input is not.
  - Driven at protocol level (`parse_marker`, `format_command`).
  - Contradiction to flag: SPEC 2.5 says "non-space" and "surrounding whitespace is trimmed" without defining either. The owner should pick the reading (U+0020, or the monitor's space+tab) before anyone fixes the code.

### Class 23

**R23-1 LOW** `host/mcuscope/webui/terminal.js:307-327` (with the flush at `:410-412`)
- Failure: a live pane holds rows in `pane.queue` for up to one flush (33 ms, longer while rendering lags at high rates). Pausing it then sets `frozenId = state.maxId` and snapshots `frozenRows` from the buffer, both of which include the queued rows, but it leaves `pane.rows` as drawn. The next flush drops the queue without counting it.
- Result: the first rebuild after the pause (a reconnect's backfill end, the high-rate release, a filter edit, any other `rebuild()` caller) adds those rows to the paused view. The pane grows while the pill reads "paused", and the rows never appear in the "N new" count.
- Driven, twice:
  - Stub: `node --test scratch-23-25-26/probe_d7_pause_midflush.test.mjs`. Pane shows rows 1-10, rows 11-13 queued, pause, `rebuild()`: rows become 1-13, `pending` 0, `frozenId` 13.
  - Headless Chromium against `mcuscoped --sim` (`browser_rotation.py`, second run). The pane read `428 lines` at the pause and `431 lines` after a regex edit and clear, still paused.
- Sibling contrast: charts, lanes and the CAN table repaint from their snapshot at the pause itself (`setChartPaused` sets `dirty`, `setDigitalPaused` redraws, `setCanPaused` renders), so what they show from the first paint on is the snapshot. Only the pane keeps drawing its pre-pause `rows`.
- Fix direction (not applied): fold `pane.queue` into `pane.rows` and render inside the pause branch, or freeze at the newest row the pane drew rather than at `state.maxId`.

**R23-2 LOW** `host/mcuscope/webui/settings.js:393-406`, `:415`
- Failure: a session row's download on the fetch path is held busy only on its button element (`setBusy`). That covers the bundle always, and the `.db` export once a token is set.
- The navigation hold is kept by path so that "a table re-render (a delete, a reopen) must not hand back a live button during the build" (`:327-329`). The fetch path gets none of that. `renderSessions()` (on reopen, a delete, or a storage save) builds a fresh, live button while the first fetch is still out, and a click on it starts a second download. For the `.db` export that means a second full copy build on the daemon.
- Driven: `node --test scratch-23-25-26/probe_d4_session_refetch.test.mjs`. Both cases fetched twice: `['/sessions/2/bundle', '/sessions/2/bundle']` and `['/sessions/2/export', '/sessions/2/export']`. The re-rendered button read `aria-disabled` false.
- Existing pins: `settings_export_hold.test.mjs` "with a token a second click while the fetch is in flight still fetches once" re-clicks the same element, and "the bundle, always fetched, is never held" asserts the absence of the nav hold. No test re-renders during a fetch.
- Also class 25 (a member born after the group state: the hold is by path, the re-rendered row does not consult it). Filed once, here.

No other violations. Every surface and writer below was checked. Where the freeze is pinned, it is by tests that pause, drive the other writer and assert the contents: `terminal_paused_freeze`, `plots_paused_freeze`, `digital_paused_freeze`, `can_logic` P12, `plots_seed_paused`, `api_high_rate_pending`, `api_capture_reset_paused_pane`, `plots_zoom`, `digital_zoom`, `can_head`, `can_bytediff`. Those files and `freeze`, `can_freeze_surface`, `export_paused_window`, `chrome_window_group`, `plots_zoom_chip`, `terminal_tick_estimate`, `digital_frozen_edge` and `settings_export_hold` were each run alone with `node --test <file>` at HEAD. All passed: 8, 7, 4, 5, 3, 2, 1, 4, 11, 5, 7, 10, 2 and 14 tests, 0 failures.

### Class 24

No findings.
One driver difference that the registry entry does not name was confirmed; it is filed below under "Registry sweep precision", not as a finding, because no site depends on it today.

### Class 25

**R25-1 MEDIUM** `host/mcuscope/webui/terminal.js:768-781` (addPane), against clear-all at `:848-860`
- Failure: clear-all sets every existing pane's clear point (`clearId = state.maxId`). A pane added afterwards starts at `clearId` 0 (`newPaneModel`) and `rebuild()` fills it from the shared buffer, so every line clear-all just cleared is back in the new pane.
- Clear-all also re-zeroed the relative-time and tick anchors, so under `rel` those resurrected rows read negative.
- The `+ pane` button already copies the last pane's port, channels and regex (`:838-839`), but not its clear point.
- Driven:
  - Stub: `node --test scratch-23-25-26/probe_d1_clearall_newpane.test.mjs` test 1. After clear-all the existing pane holds 0 rows and the added pane 50.
  - Headless Chromium (`browser_probe.py`, sim daemon on 18623): straight after clear-all the first pane read `36 lines` (live since) and the added pane `691 lines`. Screenshot `r25-1-added-pane.png`.
- The class sweep names clear-all explicitly. Every other member born after it complies: charts and lanes are fed only new samples, and a seed in flight is dropped by the seed generation.
- Fix direction, an owner call: inherit the clone source's `clearId` in `addPane`, or keep a clear-all watermark that `newPaneModel` takes. Whether a pane cloned after a per-pane clear should inherit it too is the open half.

**R25-2 LOW** `host/mcuscope/webui/chrome.js:146` and `:150-156` (a selector born painted from the global `zoomText`), reached from `plots.js:466-467`
- Failure: resuming a pane or the CAN table ends the pause-all latch but, by design, not the drag zoom. A chart born after that (a new stream, or any chart after clear-all) is live and follows its tail, as pinned by `plots_pause_edge.test.mjs` "a chart born live while a drag zoom stands follows its own tail" (F-23).
- Its window selector, though, is born showing the zoom chip (`9.04 s ×`) as the checked item with no span lit. The chip's title says "Zoomed to the dragged range, with every chart and the lanes paused on it". The label claims a state the member does not have.
- The F-23 pin asserts the member's own state but not its label: the exact gap the entry warns about.
- Driven:
  - Stub: `node --test scratch-23-25-26/probe_d2_zoom_newchart.test.mjs`. The new chart is `paused: false` and draws x 1043-1047, with the chip shown and `lit: []`.
  - Headless Chromium (`browser_probe.py`): drag zoom, click the pane pill, clear all. The rebuilt "stream 0" chart shows pause button `pause`, paused tag hidden, and the `9.04 s ×` chip visible. Screenshot `r25-2-live-chart-zoom-chip.png`.
- Contradiction to settle (reported, not worked around):
  - SPEC 9.2 says "While the zoom stands no window button is lit, and every window selector shows a chip". Read literally, that mandates this label on a live chart.
  - F-23 (and SPEC 9.2's own "a zoom always freezes what it zooms", plots.js:952-957) says a live chart is not on the zoom.
  - Recommended reading: the sentence covers the surfaces frozen on the zoom. A live chart's selector lights its own span and hides the chip, and SPEC 9.2 gets "every window selector of a surface frozen on it".

**R25-3 MEDIUM** `host/mcuscope/cli.py:997`
- Failure: `mcu tail -f` decides its `[port]` column once, at start, from the ports attached or stored then (`_port_column(s)`, commented "judged ... once"). A board attached during the follow, whose alias has no stored rows yet, interleaves its lines with the first board's with no port tag. Nothing in the text output says which board sent a line.
- This contradicts the AI guide PITFALLS ("their text rows then carry [port] when more than one board is attached or has stored rows") and SPEC 4 (docs/SPEC.md:1141), both of which the follow then breaks.
- Driven against a throwaway `mcuscoped --sim` on 18623, with a second `mcu-sim --tcp-port 18625`.
  - `mcu --url http://127.0.0.1:18623 tail -f -n 2` ran for 14 s, and `mcu attach socket://127.0.0.1:18625 --alias board2` came 4 s in.
  - The follow printed 1219 lines, with no `[` anywhere. Board2's rows appear bare, for example `21:37:23.751  event| !can 109 - 100 00000001` beside sim's `21:37:23.757  event| !can 16605 - 100 000000A6`.
  - A fresh `mcu tail -n 6` straight after rendered `[sim]` and `[board2]`. Output in `tail_follow.txt`.
- `--json` rows carry `port`, so only the text form loses it. `mcu wait` (`cli.py:1392`) judges at print time and complies.
- Fix direction: re-judge on each row (a port seen that is not in the start set turns the column on), or on the daemon's `sys` "port ... connected" row.

**R25-4 LOW** `host/mcuscope/webui/terminal.js:838-839` and `:783-795`, with `can.js:443-454`
- Failure: the CAN-click filter is a group state over "panes still showing what a click applied" (`filterPaneTo("")` restores each one). Its rendered label is the `unfilter` button (`canFilterClear`).
- Birth: `+ pane` clones the last pane's `regexSrc` but not `canFilter`. A pane cloned from a CAN-filtered pane shows the CAN pattern, and unfilter leaves it filtered while restoring the original.
- Death: `closePane` does not recompute the button. Closing the only CAN-filtered pane leaves `unfilter` shown, and its one effect is then to hide itself.
- Driven: `probe_d1_clearall_newpane.test.mjs`.
  - Test 2: after unfilter the original reads `mine` and the clone still holds the `^!can1? ...0*100 ` pattern.
  - Test 3: after closing the filtered pane, "panes left: 1, any CAN-filtered: false, unfilter hidden: false".

**R25-5 LOW** `host/mcuscope/webui/index.html:53-54`
- Failure: both group buttons name fewer members than they govern.
  - `pauseAllBtn` is titled "Pause or resume every pane", but it freezes the charts, the lanes and the CAN table too (SPEC 9.1 "every freezable surface").
  - `clearAllBtn` is titled "Clear every pane (view only)", but it also destroys the charts and clears the lanes, and it re-zeroes the rel/tick anchor.
- Static help text rather than a membership-derived label. Filed here because it misstates the group's membership. Confirmed by reading only.

### Class 26

**R26-1 LOW** `host/mcuscope/webui/terminal.js:47` (fmtTs, tick base), reading `timewindow.js:275` and `:306`
- Failure: under the tick base, a paused pane's `~N` estimate for a line with no tick of its own is re-derived on every re-render (a scroll, a time-base click, a resize, a port-set change). It comes from `tickAnchors`, a per-port store capped at `ANCHOR_CAP` 10000 that drops its oldest anchors first.
- Once live traffic on that port has added 10000 newer anchors, no anchor precedes the frozen rows, and every such estimate on the paused pane turns into `~-`.
- The pane's rows are snapshotted (`frozenRows`), but this input to how they are drawn is not. The row's own tick is memoized on the row (state.js:226-231) and survives.
- Driven: `node --test scratch-23-25-26/probe_d3_tick_anchor_rotation.test.mjs`. A pane paused on `!can 7000` plus a debug line reads `["0", "~250"]`. After 10001 later `!can` rows on the same port, whose ticks jump so the thinning keeps each one, `render(pane)` gives `["0", "~-"]`.
- How soon, reasoned only (not driven at a real rate):
  - The thinning (timewindow.js:303-304) skips an anchor that a continuing millisecond clock predicts within 1 s, so a steady board adds about one anchor a second per port. The store then turns over in about 2.8 h of pause (an overnight soak left paused).
  - Every tick line the thinning cannot predict adds one: a reset, a wrap, host jitter over 200 ms, or a tick not counting ms.
- Second consumer, reasoned only: hovering a frozen tick-less row places the chart cursor through `estimateTickX(tickAnchors, ...)` (plots.js:1263). After the same rotation that returns null, and no cursor is drawn.
- Fix direction (the entry's prescription): snapshot the port anchor lists the frozen rows need at pause, or memoize each row's estimate when it is first drawn. The second option freezes an estimate that a later history page could have improved: an owner call.

No other violations: every other frozen or held view snapshots its backing store, or reads something that does not rotate.

### Class 27

- R27-1 MEDIUM, firmware/tests/fake_shims.c:244 `mon_i2c_xfer` (`(void)wr;` at :253). The write half of every I2C transfer is never looked at. The fake's own comment says 0x50 "returns A0 A1 ... from write offset", but it answers A0.. whatever the offset.
  - Failure: the monitor could drop or corrupt the bytes of `i2c wr` and the register pointer of `i2c wrrd`, and every firmware check would stay green. On a real bus that write is lost, or the read comes from the wrong register. The monitor is vendored by three projects.
  - Driven: in scratch copy-me, monitor_cmds.c:299 passed `wr_len` 0 and :345 passed `NULL, 0` for the write. `make -C firmware/tests run` gave `288/288 checks passed`.
  - Fix shape: record the last write (bytes and length) in the fake and assert it, or make 0x50 answer from the written offset as its comment says.
- R27-2 LOW, fake_shims.c:222 `mon_can_filter`: records the bus only, `(void)id; (void)mask; (void)ext;`, and never drops a frame. The id, mask and ext the monitor programs into a hardware filter are unobserved.
  - Driven: monitor_cmds.c:217 changed to `mon_can_filter(bus, mask, id, !ext)` (swapped and inverted). `make -C firmware/tests run` gave 288/288 passed.
- R27-3 MEDIUM, reasoned only, a product gap the double hides (not itself class 27): `can filter all` and `can filter none` (monitor_cmds.c:187-194) change only the software filter and never call `mon_can_filter`.
  - A shim that programs a hardware filter, which INTEGRATION.md:313 permits, therefore cannot be widened again after a mask filter.
  - `can filter all` then answers OK while the hardware keeps discarding frames: a silent capture loss.
  - Owner call: re-program the hardware on all/none, or have the contract forbid narrowing hardware filters.
- R27-4 MEDIUM = FB1-1, test_cli_daemon_stop_scope.py:142-153: the "still answering after stop" check is tested only on the pid-None path, through doubles that contradict each other. No test covers the corroborated-pid case the check exists for.
  - I re-drove it: cli_daemonctl.py:380 guarded with `pid is None and`, then `pytest tests/test_cli_daemon_stop_scope.py tests/test_cli_daemonctl.py`: 50 passed.
- R27-5 MEDIUM = FB2-1, test_cli_version_gate.py:203-217: the recorder captures the gated request but the test asserts only its paths. Dropping `--eol`, `--repeat-ms` or `-p` from the request stays green.
  - I re-drove it: cli.py:2016 sending `"eol": None`, then test_cli_version_gate.py, test_eol.py and test_cli.py: 273 passed.
- R27-6 MEDIUM = FC2-2, test_serial_link_devices.py:101: the realpath trap has no positive control, and no test anywhere asserts a non-null `by_id`.
  - I re-drove it: server.py:3797 `"by_id": None`, then test_serial_link_devices.py and test_e2e.py: 45 passed.
- R27-7 MEDIUM = FD-2, test_sessions.py:766 `FailingCopy` fails the rebuild before `DROP TABLE`, so the atomicity the test is named for is never exercised.
  - I re-drove it: a `commit(); BEGIN IMMEDIATE` inserted after the DROP (store.py:394), then test_sessions.py and test_store_schema.py: 55 passed.
- R27-8 MEDIUM = F-JS-3, cmdbar_detached_pick.test.mjs:30-41: the stub `<select>` keeps a value no option carries, so the test passes on the exact defect it pins (a command going to a default port). Helper-driven; not re-run by me.
- R27-9 MEDIUM = F-JS-4, hand-rolled `closest()` doubles ignore their selector: digital_tick_reset:95, plots_hover_tick:38, plots_pause_edge:154, plots_tick_reset:74, settings_dirty:182 and :188.
  - I re-drove M5: settings.js:799 changed to `closest(".cfg-secX")`, then `node --test tests/webui_js/settings_dirty.test.mjs`: pass 10, fail 0.
- R27-10 LOW = FC1-1 + FD-1: the update-check transports answer every URL (test_config_api.py:347, test_update_check.py:29-37 and its handlers at :164, :165, :257), and no test pins `PYPI_URL`.
  - I re-drove it: `PYPI_URL` pointed at a typo'd `/xml` URL, then test_update_check.py and test_config_api.py: 75 passed.
- R27-11 LOW = FC2-1, test_reconnect.py:696 `_Sock` ignores `timeout`, so removing link.py:149 `ser.timeout = 0` passes the test written for it. Only test_sim_tcp.py catches it. Helper-driven.
- R27-12 LOW = FC2-3, test_server_scope.py:25 `FloorClock` dispatches on the caller's function name, so a pure rename of `_window_floor` blinds both tests that use it (:60, :77). Helper-driven.
- R27-13 LOW = FD-3, test_store_reclaim_budget.py:221 `OneStepPerExecute` emulates only `Connection.execute`, so a `cursor().execute(...)` regression passes on 3.13. Helper-driven.
- R27-14 LOW = FB1-2, test_cli_daemonctl.py:183-210 (sites 188, 195, 205, 207): the Windows ctypes double ignores `use_last_error` and `restype`. Helper-driven; the Windows consequence is reasoned only.
- R27-15 LOW = FB2-3, test_cli_read_scope.py:67 ignores `port`: it returns two boards' rows under `-p a`, which the daemon cannot produce (class 63 overlap). Helper-driven.
- R27-16 LOW = FA-3, test_cli.py:1776 `_StoppableDaemon` treats a POST to any path as a shutdown. A `/shutdownX` mutant passes both stop tests, test_cli_daemon_stop_scope.py and test_cli_ux.py. Helper-driven.
- R27-17 LOW = FA-4, test_cli_attach.py:129 cans a 404 for a detach miss where the daemon answers 400 (server.py:1174). Helper-driven.
- R27-18 LOW = FA-5, test_cli_contract.py:244: the "older daemon" wait test answers `/status` with a body no daemon sends, so it passes the version gate a real 0.3.0 daemon is refused at. Helper-driven.
- R27-19 LOW = FA-2, test_cli.py:2592 `_ScriptedWS` raises KeyboardInterrupt inside the coroutine. On 3.11+ a real Ctrl-C arrives as a cancellation, so moving the Ctrl-C arm inside the coroutine passes the test and exits 1 on a real SIGINT. Helper-driven.
- R27-20 LOW = FA-6, test_cli.py:409-410 `_DeadPipe.fileno()` answers the runner's own fd 1, so under `pytest -s` the session summary vanishes (class 32 face, not a false green). Helper-driven.
- R27-21 LOW = FA-1, test_cli_contract.py:118: the `_iter_pages_asc` double is never reached (`--limit 0` takes the streamed path), so the test runs nothing it names. The guard is covered in test_cli_read_scope.py. Helper-driven.
- R27-22 LOW = F-JS-1, exportdlg_guards.mjs:226 runs without the port and plot-channel guards in every browser test, so an export with `port=zz` is accepted by the double. Helper-driven.
- R27-23 LOW = F-JS-2, dom_stub.mjs:153 and :222 return a detached element for a missing class selector, and nothing compares class selectors with index.html. Renaming `side-body` stays green. Helper-driven.
- Not class 27, recorded where they were found:
  - FA-7 LOW (test_assert.py:644, no check that the failing-sweep double ran);
  - FB1-3 LOW (test_cli_daemonctl.py:365 asserts only text both outcomes share);
  - FB2-2 LOW (test_cli_ux.py:183 `assert probes == [] or cmd == "restart"` after the loop, always true).
  - All three are helper-driven.

### Class 28

- R28-1 MEDIUM, host/tests/test_store_writer.py:341 (and :343): the "lifespan's own shutdown sequence must still complete" step wraps `asyncio.wait_for(store.stop_session(), timeout=5.0)` in `contextlib.suppress(Exception)`.
  - A hang therefore becomes a TimeoutError that is swallowed, and the test passes after 5 s.
  - The real lifespan (server.py:540-543) has no timeout, so the same hang wedges daemon shutdown. That is the SIGKILL incident this test was written for.
  - Driven: in copy-me, `if not self.writer_alive: await asyncio.Event().wait()` at the top of `Store.stop_session`.
    - `pytest "tests/test_store_writer.py::test_a_dead_store_writer_fails_writes_instead_of_hanging" --durations=1` gave 1 passed in 5.05 s.
  - Fix shape: drop the suppress, or catch StoreError only, so a TimeoutError fails the test.
  - :343 has the same shape around `add_line`, but the `pytest.raises(StoreError)` on add_line at :332 already fails on a hang there.
- R28-2 LOW, host/tests/test_pane_regex_dialect.py:29: `except Exception: got = "refused"` counts any exception as the daemon's refusal. The fixture's refused cases therefore pass whether the matcher refuses the pattern or crashes on it.
  - Driven: store.py `_make_regexp` changed to raise `KeyError` in place of `regex.error`. `pytest tests/test_pane_regex_dialect.py` gave 82 passed.
  - Fix shape: `except regex.error`.
- R28-3 LOW, host/tests/test_daemon_startup.py:75: `pytest.raises(RuntimeError)` with no `match=` and no check that the claim happened.
  - A RuntimeError raised before `pidfile.claim` satisfies it, and then "no pid record" holds vacuously.
  - Driven: `raise RuntimeError(...)` inserted before `pidfile.claim` in daemon.main. The test passed.
  - `match="boom"` alone is not enough: moving `create_app` before the claim would still pass. It needs a positive control that the claim ran (class 78 overlap).
- R28-4 LOW, host/tests/test_store_fastpaths.py:218: `assert` statements inside a `threading.Thread` target (:231, :232).
  - pytest reports an exception in a thread as a PytestUnhandledThreadExceptionWarning, not a failure. pyproject.toml sets no `filterwarnings = error`.
  - :231 is backstopped by the main thread's `len(seen) == 3`. :232, the second `== 3`, is not: a wrong count on that call leaves `seen` at 3.
  - Reasoned only (no production mutant flips only the second read). Fix shape: collect results in the thread and assert on the main thread.


## Class 15. Shipped artifact vs stand-in

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3 (checked with `git rev-parse HEAD` before sweeping).
Scratch: `~/tt-data/mcuscope-2026-09-24/registry-leg/15-28/scratch-15-17-24/`.

### Findings

Listed at the top of this file.

### Sweep

Registry method: enumerate deliverables and name the test or CI job that exercises each in shipped form.
Enumerated mechanically rather than from the registry's list:
- `[project.scripts]`, plus `unzip -l` of the wheel and `tar tzf` of the sdist, both built by `uv build --out-dir <scratch>/dist` from a `git archive HEAD` copy.
- `git ls-files host/contrib firmware/monitor tools`.
- `release.yml` upload steps.

Counts:
- The wheel has 48 entries: 21 modules, 22 webui files (equal to `git ls-files host/mcuscope/webui`), 5 dist-info.
- The sdist has 349 files: the same 22 webui files, 299 test files (equal to `git ls-files host/tests`), 2 contrib files.
- The whole enumeration gave **16 deliverables**.

### Verdicts

1. `mcuscoped` console script: complies for startup (`test_scaffold.py:75`, `ci.yml:231`, `ci.yml:282`, `release.yml:178`); serving path see R15-4.
2. `mcu` console script: complies. `tests/test_cli.py:33` runs every `run_mcu` case through it, plus the scaffold and wheel smokes.
3. `mcu-sim` console script: complies. `tests/test_sim_tcp.py:270` serves a TCP `>1 ping` through the installed script; CI wheel smokes run `--help`.
4. Wheel contents: complies (`ci.yml:149` derived list with sizes; driven: `uvx twine check` PASSED, entry_points names all 3, `Requires-Python: >=3.10`).
5. Wheel as uploaded by release: violates, R15-3.
6. Web UI and vendored assets: complies.
   - Driven from the installed wheel: all 22 files fetched from `/ui/`; 21 are byte-identical to the wheel.
   - `index.html` differs only by the `__MCUSCOPE_VERSION__` stamp, by design (`server.py` `_stamped_index`).
   - The JS suite runs the same sources (`test_webui_js.py`, run-not-skipped guard at `ci.yml:100`).
7. Exports: complies. Through the console script (`run_mcu`): `log export` in 13 invocations, `plot export` in 5, `session export` in 2.
   - `--csv` and `--bundle` run in process only (`test_cli_export_files.py`). Exempt: `-o` file bytes come from the same code either way, and the wrapper's stdout path is covered by `log export` without `-o`.
8. `tools/mcu_sim.py` shim: complies. `ci.yml:111` runs `tools/ci_sim_smoke.py:78`, which spawns the shim and exchanges `>1 ping` on every matrix leg.
9. sdist package and webui contents: complies (`ci.yml:200-210`).
10. sdist test suite: violates, R15-2.
11. `host/contrib/config.example.toml`: violates (no test), R15-6.
12. `host/contrib/mcuscoped.service`: exempt. A systemd user unit whose `ExecStart=%h/.local/bin/mcuscoped` depends on the user's install method; a CI runner has no user systemd to drive.
13. `firmware/monitor` sources, host build: complies (`test_firmware_monitor.py` run/asan/families/families-asan, both OSes, run-not-skipped guard `ci.yml:92`).
14. `firmware/monitor` sources, Cortex-M build, and `port_template/monitor_port_template.c`: violates (no automated build), R15-5.
15. `firmware/monitor/INTEGRATION.md` C snippets: complies at the identifier level.
    - 11 fenced blocks; all 17 `monitor_*`/`MON_*` identifiers they use exist in `monitor.h`/`monitor.c`/`monitor_cmds.c` (scripted check).
    - The snippets call vendor LL/HAL, so they cannot compile standalone.
16. `tools/webui_smoke.py` (documented manual harness): violates, R15-1 and R15-7.

CI cross-check (the registry's "Windows CI jobs that never ran"):
- The `test` matrix is `[ubuntu, windows] x [3.10, 3.11, 3.12, 3.13]` with `fail-fast: false`.
- Both `wheel-smoke (windows)` jobs have `needs: build` and no `if:` gate.
- Complies, except the guard defect R15-8.

### Registry sweep precision

The registry's deliverable list (three scripts, wheel contents, web UI, exports, shim) misses:
- the sdist and its tests;
- `host/contrib`;
- the firmware monitor (target build, port template, integration guide);
- `tools/webui_smoke.py`;
- the difference between a script starting (`--version`) and serving.

Improved sweep: enumerate from the artifacts, not a list:

```
uv build --out-dir <scratch>/dist   # from a git-archive copy
unzip -l dist/*.whl; tar tzf dist/*.tar.gz
git ls-files host/contrib firmware/monitor tools
grep -A5 '^\[project.scripts\]' host/pyproject.toml
```

Then, per item, name the job that runs it in the form the user runs it: installed wrapper serving, target compiler, unpacked sdist.

### Owed on Windows or a real browser

- `mcuscoped.exe` serving from an installed wheel (R15-4).
- A browser render of the UI served from an installed wheel (the standing manual leg in `docs/SCREENSHOTS.md`). Linux served-bytes equality is driven above.

## Class 16. One bad item ends the loop

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3 (checked with `git rev-parse HEAD` before sweeping).

### Findings

Listed at the top of this file.

### Sweep method and counts

Python, every loop construct by AST (for, async for, while, and list/set/dict comprehensions and generator expressions), over `host/mcuscope/*.py`, then each ruled on whether its items come from outside the process:

```
cd host && uv run python ~/tt-data/mcuscope-2026-09-24/registry-leg/15-28/scratch-16-18/loops.py mcuscope
```
(`loops.py` walks `ast.walk` for `ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp` and prints `file:line`, kind, enclosing function.) **329 sites** (153 For, 48 While, 59 ListComp, 47 GeneratorExp, 13 DictComp, 9 SetComp; no AsyncFor).

Web UI JS, grep:
```
cd host/mcuscope/webui && grep -nE '\bfor\s*\(|\bwhile\s*\(|\.(forEach|map|filter|reduce|some|every|find|findIndex|flatMap)\(' *.js
```
**277 lines** (a line holding two constructs is one site; `vendor/` excluded as third-party).

Firmware C, grep:
```
cd firmware/monitor && grep -nE '\b(for|while)\s*\(' *.c *.h
```
**62 sites** (`grep -nE '\bdo\s*\{'` finds none; `port_template/` holds no loop).

Drives for the rulings marked "complies" that were not only read: a 200 000-line fuzz of `Simulator.handle_line` + `poll_events` over the sim command vocabulary (`scratch-16-18/fuzz_sim.py`): 0 exceptions escaped, so `_process_incoming`'s per-command mapping holds.

### Registry sweep imprecision

- The entry names no mechanical enumeration ("for every loop that processes external input"), so each round re-derives a list; 2026-08-28 filtered 105 grep hits to 41 by hand. The AST command above plus the two greps are the improved enumeration; filter by ruling, not before counting.
- Three shapes the entry does not name, all found here:
  - A per-item guard that names exception classes cannot see `StopIteration`, which PEP 479 re-raises as `RuntimeError` inside a generator or coroutine (R16-1). Ask of every guard: what does the body raise that the tuple does not name?
  - A whole-command exit (`_list_field`/`die`) placed in front of a per-item guard makes that guard unreachable (R16-2).
  - A paging loop whose continuation is judged on one item (the last row's id) ends the walk silently on a bad item (R16-3).
- Sweep addition: `grep -n "next(" host/mcuscope/*.py` for lockstep iteration of a filtered sequence against its schema (R16-1's root cause); here it returns 11 lines: `cli_output.py:432/435/439` (the R16-1 lockstep), `serial_link.py:1461/1480` (`next(iter(dict))` on non-empty internal dicts), `cli.py:397/1654` and `server.py:2065` (`next(gen, None)` searches, cannot raise), and three comments (`server.py:2306`, `store.py:2680/2813`).

### Owed on Windows or a real browser

- Nothing class-16-specific is platform-gated in the four findings. The web UI rulings are by reading; no browser run and no JS test run in this leg.

### Verdict list, Python (329)

- `host/mcuscope/_stdio.py:150` For in `repair_std_streams`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/_stdio.py:221` For in `widen_stdout_encoding`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/_stdio.py:285` For in `_already_translated`: exempt because the items are a fixed in-process list, not external input (bounded exception-context walk)
- `host/mcuscope/_stdio.py:304` For in `translate_closed_pipe_errors`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/_stdio.py:428` GeneratorExp in `console_entry`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/cli.py:201` For in `status`: complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:249` For in `ports`: complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:307` For in `devices`: complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:641` While in `_fetch_after`: complies: a page with no integer tail id stops paging with truncated=True, which the caller announces
- `host/mcuscope/cli.py:672` While in `_fetch_newest`: complies: stops on a non-int oldest id with the daemon's truncated flag intact (announced by note_truncated)
- `host/mcuscope/cli.py:714` While in `_iter_pages_asc`: VIOLATES (R16-3): a page whose last row has no integer id ends the walk as if the window were exhausted; later pages are dropped silently, exit 0, 'wrote N lines', truncated false
- `host/mcuscope/cli.py:815` For in `_make_decoder`: complies: learn() never raises; rows filtered to dicts (_list_field already refused anything else)
- `host/mcuscope/cli.py:835` For in `_decode_pages`: VIOLATES (R16-1): _decoded_row inside this generator lets the StopIteration from _fields escape; PEP 479 turns it into RuntimeError, a traceback and crash log, and every later row is lost
- `host/mcuscope/cli.py:970` For in `lines`: VIOLATES (R16-1) under --decode (the rows come from _decode_pages); otherwise complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:1024` For in `_tail_snapshot`: complies: watermark scan skips rows without an integer id
- `host/mcuscope/cli.py:1035` For in `_tail_snapshot`: VIOLATES (R16-1): the snapshot print loop consumes _decode_pages; under --decode a non-finite f4 sample raises RuntimeError. Without --decode: complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:1580` For in `session_list`: complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:2014` While in `_run_cmd`: exempt because it polls one resource until a deadline or stop, not a loop over items (busy retry)
- `host/mcuscope/cli.py:2151` For in `can_dump`: complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:2280` While in `_poll_new_frames`: VIOLATES (R16-2): _list_field dies (typer.Exit) on a page with one non-object entry, which bypasses both the per-poll and the per-frame guard and ends `can dump -f` with exit 1
- `host/mcuscope/cli.py:2428` For in `plot_channels`: complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:2674` While in `_start_daemon`: exempt because it polls one resource until a deadline or stop, not a loop over items
- `host/mcuscope/cli.py:3134` For in `_is_broken_pipe_exit`: exempt because the items are a fixed in-process list, not external input (bounded __context__ walk)
- `host/mcuscope/cli.py:814` ListComp in `_make_decoder`: complies: learn() never raises; rows filtered to dicts (_list_field already refused anything else)
- `host/mcuscope/cli.py:853` For in `_decode_pages`: VIOLATES (R16-1): _decoded_row inside this generator lets the StopIteration from _fields escape; PEP 479 turns it into RuntimeError, a traceback and crash log, and every later row is lost
- `host/mcuscope/cli.py:1104` ListComp in `_new_rows`: complies: dedupe filter that keeps any row without an integer id so the follow can charge it
- `host/mcuscope/cli.py:1134` While in `_stage_backfill`: complies: stages raw frames; each is judged later by the guarded handle()
- `host/mcuscope/cli.py:1373` ListComp in `wait`: exempt because the items are a fixed in-process list, not external input (flag gate list)
- `host/mcuscope/cli.py:1510` For in `assert_`: complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:1516` For in `assert_`: complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:1993` For in `log_export`: VIOLATES (R16-1): the row loop over _decode_pages dies with RuntimeError under --decode on a non-finite f4 sample; the -o file is discarded, stdout output stops mid-stream
- `host/mcuscope/cli.py:2210` While in `_dump_follow`: complies: per-poll and per-frame guards with separate counters; see _poll_new_frames for the gap (R16-2)
- `host/mcuscope/cli.py:2485` ListComp in `plot_export`: exempt because the items are a fixed in-process list, not external input (flag gate list)
- `host/mcuscope/cli.py:803` ListComp in `_make_decoder`: complies: learn() never raises; rows filtered to dicts (_list_field already refused anything else)
- `host/mcuscope/cli.py:836` ListComp in `_decode_pages`: complies: dict filter (redundant after _list_field)
- `host/mcuscope/cli.py:837` ListComp in `_decode_pages`: complies: id filter
- `host/mcuscope/cli.py:855` While in `_decode_pages`: complies: learn-only walk; KeyError on a def without id is the dispatcher's exit 1
- `host/mcuscope/cli.py:898` ListComp in `_stream_port_column`: complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:1217` For in `handle`: VIOLATES (R16-1): the per-row guard names AttributeError, KeyError, TypeError, ValueError; the StopIteration from --decode escapes it and the coroutine turns it into RuntimeError, ending tail -f
- `host/mcuscope/cli.py:1654` GeneratorExp in `_match_session`: complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:1970` For in `log_export`: VIOLATES (R16-1): the row loop over _decode_pages dies with RuntimeError under --decode on a non-finite f4 sample; the -o file is discarded, stdout output stops mid-stream
- `host/mcuscope/cli.py:2254` For in `_dump_follow`: complies: per-frame guard (KeyError, TypeError, ValueError) charged to frame_drops; but see R16-2, a non-object frame never reaches it
- `host/mcuscope/cli.py:2420` ListComp in `plot_channels`: complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:397` GeneratorExp in `attach`: complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:851` ListComp in `_decode_pages`: complies: collects the window's !pd rows; each learned with learn(), which never raises
- `host/mcuscope/cli.py:885` SetComp in `_port_column`: complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:902` SetComp in `_stream_port_column`: complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:1263` While in `run`: complies: every payload, staged or live, goes through the guarded handle() (subject to R16-1)
- `host/mcuscope/cli.py:2769` GeneratorExp in `daemon_restart`: complies: one-shot render of one daemon response; a malformed item refuses the whole command via _list_field or the dispatcher's KeyError arm (exit 1), the 2026-08-28 ruling
- `host/mcuscope/cli.py:1261` For in `run`: complies: every payload, staged or live, goes through the guarded handle() (subject to R16-1)
- `host/mcuscope/cli_argv.py:106` While in `split_global_opts`: exempt because argv is parsed as one command line; a bad token is a usage error for the whole invocation
- `host/mcuscope/cli_argv.py:45` For in `value_taking_opts`: exempt because argv / the click tree is one command line, not a stream of items
- `host/mcuscope/cli_argv.py:72` For in `value_taking_opts`: exempt because argv / the click tree is one command line, not a stream of items
- `host/mcuscope/cli_argv.py:32` GeneratorExp in `wants_json`: exempt because argv is one command line
- `host/mcuscope/cli_argv.py:75` For in `value_taking_opts`: exempt because argv / the click tree is one command line, not a stream of items
- `host/mcuscope/cli_client.py:289` For in `stream_text`: complies: chunks of one HTTP body; mapped by _daemon_errors
- `host/mcuscope/cli_client.py:211` ListComp in `fail`: complies: filters non-dict entries, a hint only
- `host/mcuscope/cli_client.py:256` For in `download`: complies: chunks of one HTTP body; a transport failure is the body's failure, mapped by _daemon_errors and the partial file removed
- `host/mcuscope/cli_daemonctl.py:122` For in `_index_build`: complies: lines of the daemon's stderr file are regex-matched; a non-matching or undecodable line (errors=replace) is skipped
- `host/mcuscope/cli_daemonctl.py:403` While in `_wait_daemon_gone`: exempt because it polls one resource until a deadline or stop, not a loop over items
- `host/mcuscope/cli_daemonctl.py:427` While in `_wait_pid_gone`: exempt because it polls one resource until a deadline or stop, not a loop over items
- `host/mcuscope/cli_output.py:304` For in `fmt_age`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/cli_output.py:392` For in `prime`: complies: PlotDecoder.learn returns False on a malformed def and never raises
- `host/mcuscope/cli_output.py:429` For in `_fields`: VIOLATES (R16-1): walks sample.points in lockstep with definition.channels; a dropped non-finite analog point leaves the iterator short and next() raises StopIteration
- `host/mcuscope/cli_output.py:129` GeneratorExp in `_list_field`: complies: shape check over one response list
- `host/mcuscope/cli_output.py:410` ListComp in `decode`: exempt because the items are this process's own state, not external input (fields built by _fields)
- `host/mcuscope/cli_output.py:414` GeneratorExp in `decode`: exempt because the items are this process's own state, not external input (fields built by _fields)
- `host/mcuscope/cli_output.py:426` ListComp in `_fields`: complies: ad-hoc samples carry their own names, no lockstep
- `host/mcuscope/cli_output.py:431` ListComp in `_fields`: exempt because the items are this process's own state, not external input
- `host/mcuscope/cli_output.py:432` ListComp in `_fields`: VIOLATES (R16-1): walks sample.points in lockstep with definition.channels; a dropped non-finite analog point leaves the iterator short and next() raises StopIteration
- `host/mcuscope/cli_output.py:413` GeneratorExp in `decode`: exempt because the items are this process's own state, not external input (fields built by _fields)
- `host/mcuscope/config.py:307` For in `control_char_field`: exempt because the items are two fields of one entry
- `host/mcuscope/config.py:346` For in `_warn_unknown`: complies: one warning per key
- `host/mcuscope/config.py:357` For in `_check_unknown`: complies: warns per unknown key and keeps going
- `host/mcuscope/config.py:378` For in `_check_shape`: complies: shape refusal of the whole file names the section (a non-table entry is not a port)
- `host/mcuscope/config.py:451` For in `_from_dict`: complies: each bad [[ports]] entry is warned and skipped (strict=False helpers), the rest load
- `host/mcuscope/config.py:584` For in `replace_atomic`: exempt because it polls one resource until a deadline or stop, not a loop over items (bounded retry of one replace)
- `host/mcuscope/config.py:665` For in `save_ports`: exempt because the entries were validated by PUT /config/ports before this write
- `host/mcuscope/config.py:363` For in `_check_unknown`: complies: warns per unknown key and keeps going
- `host/mcuscope/config.py:508` For in `_from_dict`: complies: each bad [[ports]] entry is warned and skipped (strict=False helpers), the rest load
- `host/mcuscope/config.py:479` ListComp in `_from_dict`: complies: each bad [[ports]] entry is warned and skipped (strict=False helpers), the rest load
- `host/mcuscope/config.py:71` GeneratorExp in `check_host`: exempt because the items are characters of one value; a bad one refuses that value
- `host/mcuscope/config.py:523` ListComp in `_from_dict`: complies: each bad [[ports]] entry is warned and skipped (strict=False helpers), the rest load
- `host/mcuscope/config.py:308` GeneratorExp in `control_char_field`: exempt because the items are two fields of one entry
- `host/mcuscope/config.py:384` GeneratorExp in `_check_shape`: complies: shape refusal of the whole file names the section (a non-table entry is not a port)
- `host/mcuscope/daemon.py:241` For in `_port_conflict`: complies: a bind failure on any resolved address refuses startup, as uvicorn's own bind would; an unavailable family is skipped
- `host/mcuscope/daemon.py:382` For in `_release_pid_on_terminating_signal`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/daemon.py:172` ListComp in `_start_sim`: exempt because the items are this process's own state, not external input
- `host/mcuscope/daemon.py:423` For in `main`: complies: prints each config warning
- `host/mcuscope/daemon.py:296` ListComp in `emit`: exempt because the items are this process's own state, not external input
- `host/mcuscope/link.py:150` While in `drain`: exempt because it drains one transport buffer; a read failure is the link's failure, charged by _reader
- `host/mcuscope/lockfile.py:126` While in `acquire`: exempt because it polls one resource until a deadline or stop, not a loop over items
- `host/mcuscope/pidfile.py:175` For in `claim`: exempt because it polls one resource until a deadline or stop, not a loop over items (two-attempt claim)
- `host/mcuscope/pjstream.py:140` For in `close`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/pjstream.py:154` DictComp in `send`: exempt because the points were decoded and finiteness-gated by protocol.py
- `host/mcuscope/protocol.py:57` DictComp in `<module>`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/protocol.py:717` For in `_parse_plot_adhoc_tokens`: exempt because the items are tokens of ONE received line or def: a malformed token makes that line malformed per SPEC 2.5 and it is stored as a plain event, nothing else is lost
- `host/mcuscope/protocol.py:757` For in `_parse_plot_def_tokens`: exempt because the items are tokens of ONE received line or def: a malformed token makes that line malformed per SPEC 2.5 and it is stored as a plain event, nothing else is lost
- `host/mcuscope/protocol.py:777` For in `_parse_enum_labels`: exempt because the items are tokens of ONE received line or def: a malformed token makes that line malformed per SPEC 2.5 and it is stored as a plain event, nothing else is lost
- `host/mcuscope/protocol.py:895` For in `_decode_plot_sample_tokens`: exempt because the items are tokens of ONE received line or def: a malformed token makes that line malformed per SPEC 2.5 and it is stored as a plain event, nothing else is lost
- `host/mcuscope/protocol.py:291` ListComp in `split_tokens`: exempt because the items are tokens of ONE received line or def: a malformed token makes that line malformed per SPEC 2.5 and it is stored as a plain event, nothing else is lost
- `host/mcuscope/protocol.py:764` For in `_parse_plot_def_tokens`: exempt because the items are tokens of ONE received line or def: a malformed token makes that line malformed per SPEC 2.5 and it is stored as a plain event, nothing else is lost
- `host/mcuscope/protocol.py:796` ListComp in `_parse_bit_lanes`: exempt because the items are tokens of ONE received line or def: a malformed token makes that line malformed per SPEC 2.5 and it is stored as a plain event, nothing else is lost
- `host/mcuscope/protocol.py:962` For in `adopt`: exempt because the items are this process's own state, not external input
- `host/mcuscope/protocol.py:1012` For in `declared_kinds`: exempt because the items are this process's own state, not external input
- `host/mcuscope/protocol.py:1031` For in `channel_meta`: exempt because the items are this process's own state, not external input (validated defs)
- `host/mcuscope/protocol.py:797` GeneratorExp in `_parse_bit_lanes`: exempt because the items are tokens of ONE received line or def: a malformed token makes that line malformed per SPEC 2.5 and it is stored as a plain event, nothing else is lost
- `host/mcuscope/protocol.py:901` For in `_decode_plot_sample_tokens`: exempt because the items are tokens of ONE received line or def: a malformed token makes that line malformed per SPEC 2.5 and it is stored as a plain event, nothing else is lost
- `host/mcuscope/protocol.py:1006` ListComp in `points_from_tokens`: exempt because the items are this process's own state, not external input
- `host/mcuscope/protocol.py:1013` For in `declared_kinds`: exempt because the items are this process's own state, not external input
- `host/mcuscope/protocol.py:1032` For in `channel_meta`: exempt because the items are this process's own state, not external input (validated defs)
- `host/mcuscope/protocol.py:178` GeneratorExp in `parse_hex_int`: exempt because the items are tokens of ONE received line or def: a malformed token makes that line malformed per SPEC 2.5 and it is stored as a plain event, nothing else is lost
- `host/mcuscope/protocol.py:456` GeneratorExp in `parse_can_flags`: exempt because the items are tokens of ONE received line or def: a malformed token makes that line malformed per SPEC 2.5 and it is stored as a plain event, nothing else is lost
- `host/mcuscope/protocol.py:799` GeneratorExp in `_parse_bit_lanes`: exempt because the items are tokens of ONE received line or def: a malformed token makes that line malformed per SPEC 2.5 and it is stored as a plain event, nothing else is lost
- `host/mcuscope/protocol.py:763` GeneratorExp in `_parse_plot_def_tokens`: exempt because the items are tokens of ONE received line or def: a malformed token makes that line malformed per SPEC 2.5 and it is stored as a plain event, nothing else is lost
- `host/mcuscope/protocol.py:1034` For in `channel_meta`: exempt because the items are this process's own state, not external input (validated defs)
- `host/mcuscope/protocol.py:1044` ListComp in `channel_meta`: exempt because the items are this process's own state, not external input (validated defs)
- `host/mcuscope/render.py:15` DictComp in `<module>`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/serial_link.py:93` For in `learn_stored_plot_defs`: complies: learn() returns False on a malformed stored def, never raises
- `host/mcuscope/serial_link.py:147` ListComp in `cached_comports`: complies: _is_absent_uart returns False on any OSError, so one unreadable sysfs node keeps its port
- `host/mcuscope/serial_link.py:171` For in `port_identity`: complies: enumeration guarded by except Exception
- `host/mcuscope/serial_link.py:535` While in `_retry_wait`: exempt because it polls one resource until a deadline or stop, not a loop over items (presence poll; _device_present never raises)
- `host/mcuscope/serial_link.py:550` While in `_reader`: complies: lookup, open and read failures each charged and retried; stop and _retry_wait None break (both questions)
- `host/mcuscope/serial_link.py:736` For in `_fail_pending`: exempt because the items are this process's own state, not external input
- `host/mcuscope/serial_link.py:800` For in `_on_bytes`: complies: oversized lines counted into rx_dropped and latched once per episode; overflow sheds oldest, counted
- `host/mcuscope/serial_link.py:837` While in `_consume`: complies: except Exception per batch, CancelledError re-raised
- `host/mcuscope/serial_link.py:872` For in `_store_rx_batch`: complies: per-line guard in both the submit loop and its settle twin, _drop_rx_line counts and latches
- `host/mcuscope/serial_link.py:882` For in `_store_rx_batch`: complies: per-line guard in both the submit loop and its settle twin, _drop_rx_line counts and latches
- `host/mcuscope/serial_link.py:1479` While in `stop_all`: exempt because the items are this process's own state, not external input
- `host/mcuscope/serial_link.py:469` For in `stop`: exempt because the items are this process's own state, not external input
- `host/mcuscope/serial_link.py:479` For in `_resolve_device`: complies: called inside _reader's guard
- `host/mcuscope/serial_link.py:826` For in `_on_bytes`: complies: oversized lines counted into rx_dropped and latched once per episode; overflow sheds oldest, counted
- `host/mcuscope/serial_link.py:1460` While in `_detach_locked`: exempt because the items are this process's own state, not external input
- `host/mcuscope/serial_link.py:1472` DictComp in `plot_channel_meta_by_port`: exempt because the items are this process's own state, not external input
- `host/mcuscope/serial_link.py:594` While in `_reader`: complies: lookup, open and read failures each charged and retried; stop and _retry_wait None break (both questions)
- `host/mcuscope/serial_link.py:844` ListComp in `_consume`: complies: except Exception per batch, CancelledError re-raised
- `host/mcuscope/serial_link.py:503` GeneratorExp in `_device_present`: complies: the whole presence test is guarded by except Exception -> True
- `host/mcuscope/server.py:2358` For in `_search_batch`: complies: as _scan_batch
- `host/mcuscope/server.py:2579` While in `_repeat_send`: complies: a failed write is counted per tick and logged once; the loop re-anchors (class 36)
- `host/mcuscope/server.py:2762` For in `_remove_export_file`: complies: each unlink suppresses OSError
- `host/mcuscope/server.py:2881` While in `_client_left`: exempt because it polls one resource until a deadline or stop, not a loop over items
- `host/mcuscope/server.py:2893` While in `_admit_and_build`: exempt because it polls one resource until a deadline or stop, not a loop over items
- `host/mcuscope/server.py:2970` For in `_scan_batch`: complies: a budget stop raises MatchBudgetExceeded for the batch, reported as unjudged, never skipped
- `host/mcuscope/server.py:2986` For in `_compile_patterns`: complies: request validation; a bad element refuses the whole request with a 400 naming it, which is the contract for a parameter (nothing is dropped silently)
- `host/mcuscope/server.py:3357` For in `_check_window`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/server.py:3425` For in `_text_lines`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/server.py:3438` For in `_jsonl_lines`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/server.py:3444` For in `_csv_lines`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/server.py:3470` For in `_csv_can`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/server.py:3488` For in `_chunked`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/server.py:3505` For in `_csv_long`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/server.py:3550` For in `_csv_wide`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/server.py:3576` For in `_parse_deadband`: complies: request validation; a bad element refuses the whole request with a 400 naming it, which is the contract for a parameter (nothing is dropped silently)
- `host/mcuscope/server.py:3603` For in `_def_decoders`: complies: learn() returns False on a malformed def, never raises
- `host/mcuscope/server.py:3634` For in `_decode_map`: exempt because the items are this process's own state, not external input (validated defs)
- `host/mcuscope/server.py:3687` For in `_export_rows`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input; learn() never raises
- `host/mcuscope/server.py:3711` For in `_changes_long`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/server.py:3735` While in `_plot_export_defs`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input; paging ends on truncated=False or an empty page (daemon-internal query)
- `host/mcuscope/server.py:3750` For in `_plot_export_defs`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input; paging ends on truncated=False or an empty page (daemon-internal query)
- `host/mcuscope/server.py:3767` For in `_wide_header`: exempt because the items are this process's own state, not external input; learn() never raises
- `host/mcuscope/server.py:3770` For in `_wide_header`: exempt because the items are this process's own state, not external input; learn() never raises
- `host/mcuscope/server.py:3773` For in `_wide_header`: exempt because the items are this process's own state, not external input; learn() never raises
- `host/mcuscope/server.py:3787` For in `_enumerate_devices`: complies: pyserial ListPortInfo fields are optional-typed and read with guards; the enumeration itself fails as one 500
- `host/mcuscope/server.py:384` SetComp in `_refuse_undeclared_query`: complies: request validation; a bad element refuses the whole request with a 400 naming it, which is the contract for a parameter (nothing is dropped silently)
- `host/mcuscope/server.py:566` For in `_validation_error`: exempt because the items are this process's own state, not external input (pydantic's error list)
- `host/mcuscope/server.py:784` For in `_prune`: exempt because the items are this process's own state, not external input
- `host/mcuscope/server.py:1424` For in `put_config_ports`: complies: request validation; a bad element refuses the whole request with a 400 naming it, which is the contract for a parameter (nothing is dropped silently)
- `host/mcuscope/server.py:1988` For in `plot_channels`: complies: per-port decoder learned from stored defs; learn() never raises
- `host/mcuscope/server.py:2525` While in `next_batch`: complies: bounded drain; shed rows counted via take_dropped
- `host/mcuscope/server.py:2673` While in `_do_wait`: complies: as _do_assert; unjudged rows reported in `dropped`
- `host/mcuscope/server.py:2775` For in `_sweep_export_orphans`: complies: per-file unlink suppresses OSError; listdir failure suppressed
- `host/mcuscope/server.py:2831` For in `_remove_all`: complies: each removal suppresses OSError
- `host/mcuscope/server.py:2971` For in `_scan_batch`: complies: a budget stop raises MatchBudgetExceeded for the batch, reported as unjudged, never skipped
- `host/mcuscope/server.py:3110` For in `_do_assert`: complies: window loops judge per batch; a budget stop counts the batch as unjudged, never a silent pass
- `host/mcuscope/server.py:3113` For in `_do_assert`: complies: window loops judge per batch; a budget stop counts the batch as unjudged, never a silent pass
- `host/mcuscope/server.py:3161` While in `_do_assert`: complies: window loops judge per batch; a budget stop counts the batch as unjudged, never a silent pass
- `host/mcuscope/server.py:3243` ListComp in `_upper_bound`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/server.py:3622` ListComp in `_renders_as_label`: exempt because the items are this process's own state, not external input
- `host/mcuscope/server.py:3669` GeneratorExp in `_stream_rows`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/server.py:3684` DictComp in `_export_rows`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input; learn() never raises
- `host/mcuscope/server.py:3688` While in `_export_rows`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input; learn() never raises
- `host/mcuscope/server.py:3740` For in `_plot_export_defs`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input; paging ends on truncated=False or an empty page (daemon-internal query)
- `host/mcuscope/server.py:3771` For in `_wide_header`: exempt because the items are this process's own state, not external input; learn() never raises
- `host/mcuscope/server.py:3780` ListComp in `_wide_header`: exempt because the items are this process's own state, not external input; learn() never raises
- `host/mcuscope/server.py:3813` For in `_by_id_map`: complies: the listdir walk is guarded by except OSError (best effort, documented)
- `host/mcuscope/server.py:499` For in `lifespan`: complies: autoconnect charges PortError per entry and keeps going; the other raises attach can produce (StoreError, sqlite3 errors from the prime query) are store-wide, not per entry
- `host/mcuscope/server.py:780` ListComp in `_prune`: exempt because the items are this process's own state, not external input
- `host/mcuscope/server.py:788` For in `_prune`: exempt because the items are this process's own state, not external input
- `host/mcuscope/server.py:993` SetComp in `<module>`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/server.py:1422` DictComp in `put_config_ports`: complies: request validation; a bad element refuses the whole request with a 400 naming it, which is the contract for a parameter (nothing is dropped silently)
- `host/mcuscope/server.py:1423` DictComp in `put_config_ports`: complies: request validation; a bad element refuses the whole request with a 400 naming it, which is the contract for a parameter (nothing is dropped silently)
- `host/mcuscope/server.py:1636` For in `_build_bundle`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/server.py:1939` For in `can_frames`: complies: request validation; a bad element refuses the whole request with a 400 naming it, which is the contract for a parameter (nothing is dropped silently)
- `host/mcuscope/server.py:2062` ListComp in `plot_export`: complies: request validation; a bad element refuses the whole request with a 400 naming it, which is the contract for a parameter (nothing is dropped silently)
- `host/mcuscope/server.py:2103` SetComp in `plot_export`: complies: request validation; a bad element refuses the whole request with a 400 naming it, which is the contract for a parameter (nothing is dropped silently)
- `host/mcuscope/server.py:2104` ListComp in `plot_export`: complies: request validation; a bad element refuses the whole request with a 400 naming it, which is the contract for a parameter (nothing is dropped silently)
- `host/mcuscope/server.py:2214` While in `pump`: complies: rows are store rows already JSON; a send failure is the vanished peer and ends the pump by design
- `host/mcuscope/server.py:2281` For in `ws`: exempt because the items are this process's own state, not external input
- `host/mcuscope/server.py:2542` ListComp in `next_batch`: complies: bounded drain; shed rows counted via take_dropped
- `host/mcuscope/server.py:3101` GeneratorExp in `_do_assert`: complies: window loops judge per batch; a budget stop counts the batch as unjudged, never a silent pass
- `host/mcuscope/server.py:3282` GeneratorExp in `_resolve_window`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/server.py:3283` GeneratorExp in `_resolve_window`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/server.py:3321` GeneratorExp in `_named_by_ids`: exempt because the items are this process's own state, not external input
- `host/mcuscope/server.py:3412` GeneratorExp in `_csv_cell`: exempt because the items are characters of one cell
- `host/mcuscope/server.py:3432` SetComp in `_several_ports`: exempt because the items are this process's own state, not external input
- `host/mcuscope/server.py:3778` For in `_wide_header`: exempt because the items are this process's own state, not external input; learn() never raises
- `host/mcuscope/server.py:992` SetComp in `<module>`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/server.py:1110` ListComp in `status`: exempt because the items are this process's own state, not external input
- `host/mcuscope/server.py:1142` ListComp in `get_ports`: exempt because the items are this process's own state, not external input
- `host/mcuscope/server.py:1273` ListComp in `get_config`: exempt because the items are this process's own state, not external input
- `host/mcuscope/server.py:1313` GeneratorExp in `put_config_storage`: complies: request validation; a bad element refuses the whole request with a 400 naming it, which is the contract for a parameter (nothing is dropped silently)
- `host/mcuscope/server.py:2065` GeneratorExp in `plot_export`: complies: request validation; a bad element refuses the whole request with a 400 naming it, which is the contract for a parameter (nothing is dropped silently)
- `host/mcuscope/server.py:2125` ListComp in `plot_export`: complies: request validation; a bad element refuses the whole request with a 400 naming it, which is the contract for a parameter (nothing is dropped silently)
- `host/mcuscope/server.py:2266` While in `watch`: complies: ends on disconnect, WebSocketDisconnect and RuntimeError
- `host/mcuscope/server.py:3073` ListComp in `verdict`: exempt because the items are this process's own state, not external input
- `host/mcuscope/server.py:3077` ListComp in `verdict`: exempt because the items are this process's own state, not external input
- `host/mcuscope/server.py:3547` GeneratorExp in `emit`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/server.py:3623` GeneratorExp in `_renders_as_label`: exempt because the items are this process's own state, not external input
- `host/mcuscope/server.py:3774` GeneratorExp in `_wide_header`: exempt because the items are this process's own state, not external input; learn() never raises
- `host/mcuscope/server.py:388` GeneratorExp in `_refuse_undeclared_query`: complies: request validation; a bad element refuses the whole request with a 400 naming it, which is the contract for a parameter (nothing is dropped silently)
- `host/mcuscope/server.py:1043` GeneratorExp in `_resolve_port`: exempt because the items are this process's own state, not external input
- `host/mcuscope/server.py:1646` While in `_build_bundle`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/server.py:1692` For in `build`: exempt because a zip member is one artifact: a failure fails the build, reported, temp files removed
- `host/mcuscope/server.py:2231` While in `pump`: complies: rows are store rows already JSON; a send failure is the vanished peer and ends the pump by design
- `host/mcuscope/server.py:3057` GeneratorExp in `verdict`: exempt because the items are this process's own state, not external input
- `host/mcuscope/server.py:3057` GeneratorExp in `verdict`: exempt because the items are this process's own state, not external input
- `host/mcuscope/server.py:3178` ListComp in `_do_assert`: complies: window loops judge per batch; a budget stop counts the batch as unjudged, never a silent pass
- `host/mcuscope/server.py:3186` For in `_do_assert`: complies: window loops judge per batch; a budget stop counts the batch as unjudged, never a silent pass
- `host/mcuscope/server.py:3193` ListComp in `_do_assert`: complies: window loops judge per batch; a budget stop counts the batch as unjudged, never a silent pass
- `host/mcuscope/server.py:3202` For in `_do_assert`: complies: window loops judge per batch; a budget stop counts the batch as unjudged, never a silent pass
- `host/mcuscope/server.py:3545` GeneratorExp in `emit`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/server.py:567` GeneratorExp in `_validation_error`: exempt because the items are this process's own state, not external input (pydantic's error list)
- `host/mcuscope/server.py:3166` GeneratorExp in `_do_assert`: complies: window loops judge per batch; a budget stop counts the batch as unjudged, never a silent pass
- `host/mcuscope/server.py:3528` GeneratorExp in `_csv_wide`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/server.py:3540` GeneratorExp in `emit`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/server.py:3639` DictComp in `_decode_map`: exempt because the items are this process's own state, not external input (validated defs)
- `host/mcuscope/server.py:1694` For in `build`: exempt because a zip member is one artifact: a failure fails the build, reported, temp files removed
- `host/mcuscope/server.py:2124` GeneratorExp in `plot_export`: complies: request validation; a bad element refuses the whole request with a 400 naming it, which is the contract for a parameter (nothing is dropped silently)
- `host/mcuscope/server.py:2678` ListComp in `_do_wait`: complies: as _do_assert; unjudged rows reported in `dropped`
- `host/mcuscope/server.py:3189` GeneratorExp in `_do_assert`: complies: window loops judge per batch; a budget stop counts the batch as unjudged, never a silent pass
- `host/mcuscope/server.py:534` ListComp in `lifespan`: complies: autoconnect charges PortError per entry and keeps going; the other raises attach can produce (StoreError, sqlite3 errors from the prime query) are store-wide, not per entry
- `host/mcuscope/server.py:3196` ListComp in `_do_assert`: complies: window loops judge per batch; a budget stop counts the batch as unjudged, never a silent pass
- `host/mcuscope/sim.py:603` For in `_format_typed_sample`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/sim.py:696` For in `_process_incoming`: complies: per-line overflow handling; handle_line maps ProtocolError per command (200k-line fuzz: no escape), and the session guard in serve_listener/serve_pty is the backstop
- `host/mcuscope/sim.py:784` While in `serve_listener`: complies: per-OSError retry with backoff; _FD_DEAD_ERRNOS and a closed fd break (both questions)
- `host/mcuscope/sim.py:833` While in `_serve_socket_client`: complies: EAGAIN/EINTR continue, other OSError or EOF ends the session
- `host/mcuscope/sim.py:905` For in `encode_lines`: complies: an oversized line is cut or answered ERR 8 per line and reported, never raised
- `host/mcuscope/sim.py:955` While in `_sock_send_lines`: complies: BlockingIOError is a slow reader (resume at offset), hard OSError or a 5 s stall ends the session
- `host/mcuscope/sim.py:1086` While in `_pty_write_lines`: complies: EAGAIN retried, other OSError left to serve_pty's guard
- `host/mcuscope/sim.py:430` For in `poll_events`: exempt because the items are this process's own state, not external input (the sim's own schedule)
- `host/mcuscope/sim.py:442` For in `poll_events`: exempt because the items are this process's own state, not external input (the sim's own schedule)
- `host/mcuscope/sim.py:464` For in `poll_events`: exempt because the items are this process's own state, not external input (the sim's own schedule)
- `host/mcuscope/sim.py:477` For in `poll_events`: exempt because the items are this process's own state, not external input (the sim's own schedule)
- `host/mcuscope/sim.py:508` For in `_poll_flood`: exempt because the items are this process's own state, not external input
- `host/mcuscope/sim.py:567` For in `_poll_plot`: exempt because the items are this process's own state, not external input
- `host/mcuscope/sim.py:762` GeneratorExp in `<module>`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/sim.py:877` ListComp in `_cut_event`: exempt because the items are tokens of ONE received line or def: a malformed token makes that line malformed per SPEC 2.5 and it is stored as a plain event, nothing else is lost
- `host/mcuscope/sim.py:1130` While in `serve_pty`: complies: per-session except Exception resets the sim; a dead master breaks (both questions)
- `host/mcuscope/sim.py:1167` For in `serve_pty`: complies: per-session except Exception resets the sim; a dead master breaks (both questions)
- `host/mcuscope/sim.py:153` DictComp in `__init__`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/sim.py:341` For in `_i2c_write`: exempt because the items are tokens of ONE received line or def: a malformed token makes that line malformed per SPEC 2.5 and it is stored as a plain event, nothing else is lost
- `host/mcuscope/sim.py:443` For in `poll_events`: exempt because the items are this process's own state, not external input (the sim's own schedule)
- `host/mcuscope/sim.py:549` ListComp in `burst_debug`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/sim.py:863` GeneratorExp in `_sanitize`: exempt because the items are characters of one line
- `host/mcuscope/sim.py:154` DictComp in `__init__`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/sim.py:372` GeneratorExp in `_spi`: exempt because the items are this process's own state, not external input
- `host/mcuscope/sim.py:409` GeneratorExp in `_mark`: exempt because the items are tokens of ONE received line or def: a malformed token makes that line malformed per SPEC 2.5 and it is stored as a plain event, nothing else is lost
- `host/mcuscope/sim.py:448` For in `poll_events`: exempt because the items are this process's own state, not external input (the sim's own schedule)
- `host/mcuscope/sim.py:78` DictComp in `<module>`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/sim.py:312` GeneratorExp in `_i2c`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/sim.py:357` GeneratorExp in `_i2c_read`: exempt because the items are this process's own state, not external input
- `host/mcuscope/store.py:227` While in `_reclaim_pages`: exempt because the items are this process's own state, not external input
- `host/mcuscope/store.py:350` For in `_apply_migrations`: exempt because the items are a fixed in-process list, not external input (migration table)
- `host/mcuscope/store.py:412` For in `_schema_statement`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/store.py:396` For in `_rebuild_sessions_for_autoincrement`: exempt because the items are a fixed in-process list, not external input (index list of a one-time migration)
- `host/mcuscope/store.py:737` For in `stop`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/store.py:785` For in `stop_subscribers`: exempt because the items are this process's own state, not external input
- `host/mcuscope/store.py:799` For in `_close_read_conns`: complies: each close suppresses Exception
- `host/mcuscope/store.py:829` While in `_fail_queued`: exempt because the items are this process's own state, not external input
- `host/mcuscope/store.py:884` While in `_writer`: complies: batch insert falls back to row by row, a row-by-row or commit failure fails that batch and keeps draining; stop honoured on every path (both questions)
- `host/mcuscope/store.py:1020` For in `_check_stamp_order`: exempt because the items are this process's own state, not external input
- `host/mcuscope/store.py:1067` For in `_insert_batch`: complies: a raise aborts the executemany and _writer retries row by row (_insert_individually)
- `host/mcuscope/store.py:1110` For in `_insert_individually`: complies: per-row except Exception, counted via _fail_write
- `host/mcuscope/store.py:1331` For in `_broadcast_batch`: complies: rows are daemon-built scalars (json.dumps cannot raise on them); a full queue sheds oldest, counted
- `host/mcuscope/store.py:1708` For in `_forget_plot_points`: exempt because the items are this process's own state, not external input
- `host/mcuscope/store.py:2244` For in `query_can_frames`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2339` For in `_note_plot`: exempt because the items are this process's own state, not external input (points decoded and finiteness-gated at ingest)
- `host/mcuscope/store.py:2352` For in `_plot_channels_from_summary`: exempt because the items are this process's own state, not external input
- `host/mcuscope/store.py:2413` For in `_scan_plot_rows`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2424` For in `_scan_plot_rows`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2436` For in `_scan_plot_rows`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2571` For in `plot_streams`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2981` While in `_trim_oldest`: exempt because the items are this process's own state, not external input
- `host/mcuscope/store.py:3094` While in `_sweep_retention_locked`: exempt because the items are this process's own state, not external input
- `host/mcuscope/store.py:3111` While in `_retention_loop`: complies: sweep_tick catches Exception per tick (a failed sweep must not kill the daemon)
- `host/mcuscope/store.py:3160` For in `db_size_bytes`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/store.py:351` SetComp in `_apply_migrations`: exempt because the items are a fixed in-process list, not external input (migration table)
- `host/mcuscope/store.py:411` GeneratorExp in `_schema_statement`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/store.py:656` ListComp in `start`: exempt because the items are a fixed in-process list, not external input
- `host/mcuscope/store.py:907` While in `_writer`: complies: batch insert falls back to row by row, a row-by-row or commit failure fails that batch and keeps draining; stop honoured on every path (both questions)
- `host/mcuscope/store.py:1074` For in `_insert_batch`: complies: a raise aborts the executemany and _writer retries row by row (_insert_individually)
- `host/mcuscope/store.py:1335` For in `_broadcast_batch`: complies: rows are daemon-built scalars (json.dumps cannot raise on them); a full queue sheds oldest, counted
- `host/mcuscope/store.py:1439` ListComp in `list_sessions`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2031` ListComp in `stored_ports`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2312` ListComp in `query_plot_channels`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2369` ListComp in `_plot_channels_from_summary`: exempt because the items are this process's own state, not external input
- `host/mcuscope/store.py:2396` DictComp in `_scan_plot_rows`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2405` DictComp in `_scan_plot_rows`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2416` For in `_scan_plot_rows`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2466` For in `_rebuild_plot_summary`: exempt because the items are this process's own state, not external input
- `host/mcuscope/store.py:2542` ListComp in `query_plot_series`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2573` ListComp in `plot_streams`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2623` ListComp in `export_sids`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2715` While in `iter_plot_export`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2765` While in `_iter_export_pages`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2934` While in `_delete_chunks`: exempt because the items are this process's own state, not external input (retention chunks; sweep_tick guards the tick)
- `host/mcuscope/store.py:985` For in `_writer`: complies: batch insert falls back to row by row, a row-by-row or commit failure fails that batch and keeps draining; stop honoured on every path (both questions)
- `host/mcuscope/store.py:1964` ListComp in `query_lines`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2327` SetComp in `plot_ports_safe`: exempt because the items are this process's own state, not external input
- `host/mcuscope/store.py:2526` ListComp in `query_plot_series`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:2727` For in `iter_plot_export`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:1001` For in `_writer`: complies: batch insert falls back to row by row, a row-by-row or commit failure fails that batch and keeps draining; stop honoured on every path (both questions)
- `host/mcuscope/store.py:1168` ListComp in `_insert_children`: complies: raises to _insert, which deletes the orphan line and fails that row only
- `host/mcuscope/store.py:1334` ListComp in `_broadcast_batch`: complies: rows are daemon-built scalars (json.dumps cannot raise on them); a full queue sheds oldest, counted
- `host/mcuscope/store.py:929` ListComp in `_writer`: complies: batch insert falls back to row by row, a row-by-row or commit failure fails that batch and keeps draining; stop honoured on every path (both questions)
- `host/mcuscope/store.py:975` For in `_writer`: complies: batch insert falls back to row by row, a row-by-row or commit failure fails that batch and keeps draining; stop honoured on every path (both questions)
- `host/mcuscope/store.py:2722` ListComp in `iter_plot_export`: exempt because the items are rows this daemon wrote (schema-typed columns), and the response is one artifact; not external input
- `host/mcuscope/store.py:947` For in `_writer`: complies: batch insert falls back to row by row, a row-by-row or commit failure fails that batch and keeps draining; stop honoured on every path (both questions)
- `host/mcuscope/update_check.py:78` GeneratorExp in `parse_version`: exempt because the items are parts of one version string (validated by _VERSION_RE first)

### Verdict list, web UI JS (277)

- `host/mcuscope/webui/chrome.js:29`: complies: a localStorage value, parsed inside try and filtered by type before use
- `host/mcuscope/webui/chrome.js:120`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/chrome.js:129`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/chrome.js:159`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/chrome.js:180`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/chrome.js:187`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/chrome.js:199`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/chrome.js:229`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/chrome.js:231`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/can.js:147`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:178`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:209`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:219`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:280`: complies: a localStorage value, parsed inside try and filtered by type before use
- `host/mcuscope/webui/can.js:311`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:329`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:334`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:340`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:364`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:389`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:394`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:540`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:551`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:562`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:607`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:614`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:627`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:638`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:651`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:652`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:654`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/can.js:660`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/pane.js:63`: exempt because it hashes one string
- `host/mcuscope/webui/pane.js:100`: complies: a localStorage value, parsed inside try and filtered by type before use
- `host/mcuscope/webui/pane.js:185`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/layout.js:64`: complies: a localStorage value, parsed inside try and filtered by type before use
- `host/mcuscope/webui/layout.js:77`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/api.js:80`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/api.js:125`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/api.js:188`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/api.js:286`: complies: best-effort !pd seed pages; a page with no numeric tail id ends the seed, the next !pd rebroadcast covers it
- `host/mcuscope/webui/api.js:299`: complies: per-row try/catch, one console.error per episode (the seedPlotDefs guard)
- `host/mcuscope/webui/api.js:354`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/api.js:357`: complies: filters channels to objects with a string name
- `host/mcuscope/webui/api.js:361`: complies: filters ports to non-empty strings
- `host/mcuscope/webui/api.js:362`: complies: as :361
- `host/mcuscope/webui/api.js:365`: complies: per-port fetches inside try/catch with a fallback to the unfiltered list
- `host/mcuscope/webui/api.js:367`: complies: each per-port body filtered by valid()
- `host/mcuscope/webui/api.js:400`: complies: per-channel try/catch inside the map, not all-or-nothing
- `host/mcuscope/webui/api.js:450`: complies: min-id scan skips rows without a numeric id
- `host/mcuscope/webui/api.js:490`: complies: backfill paging decided by planBackfillStep over the min id, not the last element
- `host/mcuscope/webui/api.js:528`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/api.js:592`: complies: per-row try/catch in the backfill merge, `bad` reported once
- `host/mcuscope/webui/api.js:607`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/api.js:617`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/api.js:696`: complies: live per-row try/catch; staging re-checked per row (stageRow handles null and non-objects)
- `host/mcuscope/webui/api.js:741`: complies: staging trim; drops counted in st.dropped and marked as a shed notice
- `host/mcuscope/webui/api.js:766`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/api.js:791`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/api.js:831`: complies: staged twin; feedStaged carries the live path's per-row try/catch
- `host/mcuscope/webui/api.js:836`: complies: segmentation only; every row goes through feedStaged's guard
- `host/mcuscope/webui/api.js:852`: complies: inside feedStaged's try
- `host/mcuscope/webui/timewindow.js:81`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/timewindow.js:100`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/timewindow.js:115`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/timewindow.js:124`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/timewindow.js:143`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/timewindow.js:146`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/timewindow.js:148`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/timewindow.js:151`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/timewindow.js:161`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/timewindow.js:203`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/timewindow.js:205`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/timewindow.js:210`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/timewindow.js:225`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/timewindow.js:226`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/timewindow.js:285`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/timewindow.js:326`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/timewindow.js:356`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/app.js:41`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/freeze.js:19`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/freeze.js:30`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/freeze.js:38`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/exportdlg.js:57`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/exportdlg.js:76`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/exportdlg.js:118`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/exportdlg.js:167`: complies: one daemon response rendered whole inside the caller's try/catch; a malformed entry fails that refresh, reported, and the next poll re-renders
- `host/mcuscope/webui/exportdlg.js:168`: complies: one daemon response rendered whole inside the caller's try/catch; a malformed entry fails that refresh, reported, and the next poll re-renders
- `host/mcuscope/webui/exportdlg.js:174`: complies: one daemon response rendered whole inside the caller's try/catch; a malformed entry fails that refresh, reported, and the next poll re-renders
- `host/mcuscope/webui/exportdlg.js:253`: exempt because it awaits a promise chain, not items
- `host/mcuscope/webui/statusbar.js:76`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/statusbar.js:295`: complies: one daemon response rendered whole inside the caller's try/catch; a malformed entry fails that refresh, reported, and the next poll re-renders
- `host/mcuscope/webui/statusbar.js:323`: complies: one daemon response rendered whole inside the caller's try/catch; a malformed entry fails that refresh, reported, and the next poll re-renders
- `host/mcuscope/webui/statusbar.js:338`: complies: one daemon response rendered whole inside the caller's try/catch; a malformed entry fails that refresh, reported, and the next poll re-renders
- `host/mcuscope/webui/statusbar.js:359`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/statusbar.js:519`: complies: one daemon response rendered whole inside the caller's try/catch; a malformed entry fails that refresh, reported, and the next poll re-renders
- `host/mcuscope/webui/statusbar.js:522`: complies: one daemon response rendered whole inside the caller's try/catch; a malformed entry fails that refresh, reported, and the next poll re-renders
- `host/mcuscope/webui/statusbar.js:640`: complies: one daemon response rendered whole inside the caller's try/catch; a malformed entry fails that refresh, reported, and the next poll re-renders (the fetch has its own catch; render follows)
- `host/mcuscope/webui/statusbar.js:661`: complies: one daemon response rendered whole inside the caller's try/catch; a malformed entry fails that refresh, reported, and the next poll re-renders
- `host/mcuscope/webui/digital.js:72`: complies: per-point routing of already-decoded points; lane cap warned once, not thrown
- `host/mcuscope/webui/digital.js:131`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:166`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:169`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:268`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:269`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:301`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:320`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:321`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:414`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:437`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:444`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:466`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:471`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:486`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:488`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:489`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:494`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:500`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:527`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:543`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:594`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:624`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:651`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:653`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:657`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:680`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:684`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:687`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:701`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:712`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:764`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:784`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:790`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:838`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:872`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:876`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:885`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/digital.js:911`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:167`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:211`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:234`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:256`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:271`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:274`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:277`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:293`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:303`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:321`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:333`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:334`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:362`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:375`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:406`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:417`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:502`: exempt because it bounds history hops (HISTORY_HOPS), one fetch per hop
- `host/mcuscope/webui/terminal.js:521`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/terminal.js:534`: complies: history page filtered to numeric ids; a row that still throws (non-string raw) fails the page, reported by the catch (one response, one refusal)
- `host/mcuscope/webui/terminal.js:535`: complies: as :534
- `host/mcuscope/webui/terminal.js:537`: complies: as :534
- `host/mcuscope/webui/terminal.js:539`: complies: min scan over numeric ids
- `host/mcuscope/webui/terminal.js:549`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:553`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:605`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:625`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/terminal.js:641`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/terminal.js:655`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/terminal.js:658`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:661`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:667`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:690`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:800`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/terminal.js:816`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:819`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:830`: complies: a localStorage value, parsed inside try and filtered by type before use
- `host/mcuscope/webui/terminal.js:832`: complies: a localStorage value, parsed inside try and filtered by type before use
- `host/mcuscope/webui/terminal.js:841`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:851`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/terminal.js:880`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/settings.js:88`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/settings.js:103`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/settings.js:104`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/settings.js:106`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/settings.js:107`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/settings.js:109`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/settings.js:198`: complies: renderWarnings coerces each entry with String() and filters non-arrays
- `host/mcuscope/webui/settings.js:344`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/settings.js:365`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/settings.js:466`: complies: one daemon response rendered whole inside the caller's try/catch; a malformed entry fails that refresh, reported, and the next poll re-renders
- `host/mcuscope/webui/settings.js:478`: complies: one daemon response rendered whole inside the caller's try/catch; a malformed entry fails that refresh, reported, and the next poll re-renders
- `host/mcuscope/webui/settings.js:579`: complies: one daemon response rendered whole inside the caller's try/catch; a malformed entry fails that refresh, reported, and the next poll re-renders
- `host/mcuscope/webui/settings.js:588`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/settings.js:589`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/settings.js:607`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/settings.js:800`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/settings.js:818`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/settings.js:835`: complies: one daemon response rendered whole inside the caller's try/catch; a malformed entry fails that refresh, reported, and the next poll re-renders
- `host/mcuscope/webui/state.js:80`: exempt because it re-prompts for a token on 401, one request
- `host/mcuscope/webui/state.js:175`: exempt because it hashes one string
- `host/mcuscope/webui/state.js:240`: exempt because the items are tokens of ONE line or def: a malformed token rejects that line (returns null, never throws), as protocol.py does
- `host/mcuscope/webui/state.js:303`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/state.js:440`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/state.js:443`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/state.js:484`: complies: a localStorage value, parsed inside try and filtered by type before use
- `host/mcuscope/webui/cmdbar.js:28`: complies: a localStorage value, parsed inside try and filtered by type before use
- `host/mcuscope/webui/cmdbar.js:59`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/cmdbar.js:99`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/cmdbar.js:118`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/cmdbar.js:286`: exempt because the items are the page's own DOM or model objects, not external input
- `host/mcuscope/webui/plots.js:107`: exempt because the items are tokens of ONE line or def: a malformed token rejects that line (returns null, never throws), as protocol.py does
- `host/mcuscope/webui/plots.js:160`: exempt because the items are tokens of ONE line or def: a malformed token rejects that line (returns null, never throws), as protocol.py does
- `host/mcuscope/webui/plots.js:184`: exempt because the items are tokens of ONE line or def: a malformed token rejects that line (returns null, never throws), as protocol.py does
- `host/mcuscope/webui/plots.js:185`: exempt because the items are tokens of ONE line or def: a malformed token rejects that line (returns null, never throws), as protocol.py does
- `host/mcuscope/webui/plots.js:186`: exempt because the items are tokens of ONE line or def: a malformed token rejects that line (returns null, never throws), as protocol.py does
- `host/mcuscope/webui/plots.js:195`: exempt because the items are tokens of ONE line or def: a malformed token rejects that line (returns null, never throws), as protocol.py does
- `host/mcuscope/webui/plots.js:203`: exempt because the items are tokens of ONE line or def: a malformed token rejects that line (returns null, never throws), as protocol.py does
- `host/mcuscope/webui/plots.js:207`: exempt because the items are tokens of ONE line or def: a malformed token rejects that line (returns null, never throws), as protocol.py does
- `host/mcuscope/webui/plots.js:221`: exempt because the items are tokens of ONE line or def: a malformed token rejects that line (returns null, never throws), as protocol.py does
- `host/mcuscope/webui/plots.js:225`: exempt because the items are tokens of ONE line or def: a malformed token rejects that line (returns null, never throws), as protocol.py does
- `host/mcuscope/webui/plots.js:239`: exempt because the items are tokens of ONE line or def: a malformed token rejects that line (returns null, never throws), as protocol.py does; non-finite analog points dropped by name, no lockstep (the JS twin of R16-1 is sound)
- `host/mcuscope/webui/plots.js:245`: exempt because the items are tokens of ONE line or def: a malformed token rejects that line (returns null, never throws), as protocol.py does
- `host/mcuscope/webui/plots.js:295`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:323`: complies: per-point skip of non-numeric line_id and non-finite value
- `host/mcuscope/webui/plots.js:324`: complies: as :323
- `host/mcuscope/webui/plots.js:347`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:358`: complies: seedNameOk grammar check per entry
- `host/mcuscope/webui/plots.js:367`: complies: entries already filtered by seedNameOk
- `host/mcuscope/webui/plots.js:384`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:398`: complies: skips non-event, non-string raw; parsePlotDef returns null, never throws
- `host/mcuscope/webui/plots.js:404`: complies: malformed entries skipped by shape and seedNameOk
- `host/mcuscope/webui/plots.js:413`: complies: per-group try/catch
- `host/mcuscope/webui/plots.js:443`: complies: per-row try/catch, `bad` reported once
- `host/mcuscope/webui/plots.js:481`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:482`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:486`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:519`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:584`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:613`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:623`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:644`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:647`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:741`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:761`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:821`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:826`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:862`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:895`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:902`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:974`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:984`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1006`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1027`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1038`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1085`: exempt because it is pure arithmetic over in-page arrays (projection, ticks, search)
- `host/mcuscope/webui/plots.js:1108`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1126`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1131`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1132`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1133`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1145`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1147`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1165`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1188`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1211`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1291`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1309`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1330`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1346`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1347`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1364`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1365`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1372`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1407`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1417`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1421`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1431`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1454`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)
- `host/mcuscope/webui/plots.js:1481`: exempt because the items are the page's own chart/lane/row model, built only from rows that passed the guarded ingest (handleWsRow/plotIngest/canIngest)

### Verdict list, firmware C (62)

- `firmware/monitor/monitor_cmds.c:65`: exempt because the items are characters/tokens of ONE received line: a bad byte or token rejects that line with badarg/badcmd (or silence without a seq), the next line is unaffected
- `firmware/monitor/monitor_cmds.c:270`: VIOLATES (R16-4): keeps looping on every non-zero probe result and reads it as absence; NOSUP (the weak default shim) and BUSERR are not per-address, yet the scan answers OK
- `firmware/monitor/monitor_cmds.c:506`: exempt because it is bounded arithmetic over the monitor's own buffers or tables (registration table)
- `firmware/monitor/monitor_cmds.c:534`: exempt because it is bounded arithmetic over the monitor's own buffers or tables (command table)
- `firmware/monitor/monitor_cmds.c:543`: exempt because it is bounded arithmetic over the monitor's own buffers or tables (command table)
- `firmware/monitor/monitor_cmds.c:549`: exempt because it is bounded arithmetic over the monitor's own buffers or tables (registration table)
- `firmware/monitor/monitor.c:97`: exempt because it is bounded arithmetic over the monitor's own buffers or tables
- `firmware/monitor/monitor.c:101`: exempt because it is bounded arithmetic over the monitor's own buffers or tables
- `firmware/monitor/monitor.c:113`: exempt because it is bounded arithmetic over the monitor's own buffers or tables
- `firmware/monitor/monitor.c:115`: exempt because it is bounded arithmetic over the monitor's own buffers or tables
- `firmware/monitor/monitor.c:130`: exempt because it is bounded arithmetic over the monitor's own buffers or tables
- `firmware/monitor/monitor.c:143`: exempt because the items are characters/tokens of ONE received line: a bad byte or token rejects that line with badarg/badcmd (or silence without a seq), the next line is unaffected (hex decode of one argument)
- `firmware/monitor/monitor.c:163`: exempt because the items are characters/tokens of ONE received line: a bad byte or token rejects that line with badarg/badcmd (or silence without a seq), the next line is unaffected
- `firmware/monitor/monitor.c:182`: exempt because the items are characters/tokens of ONE received line: a bad byte or token rejects that line with badarg/badcmd (or silence without a seq), the next line is unaffected
- `firmware/monitor/monitor.c:218`: exempt because the items are characters/tokens of ONE received line: a bad byte or token rejects that line with badarg/badcmd (or silence without a seq), the next line is unaffected
- `firmware/monitor/monitor.c:267`: exempt because the items are characters/tokens of ONE received line: a bad byte or token rejects that line with badarg/badcmd (or silence without a seq), the next line is unaffected
- `firmware/monitor/monitor.c:346`: exempt because it is bounded arithmetic over the monitor's own buffers or tables (event cut back on a token boundary)
- `firmware/monitor/monitor.c:349`: exempt because it is bounded arithmetic over the monitor's own buffers or tables
- `firmware/monitor/monitor.c:354`: exempt because it is bounded arithmetic over the monitor's own buffers or tables
- `firmware/monitor/monitor.c:364`: exempt because it is bounded arithmetic over the monitor's own buffers or tables
- `firmware/monitor/monitor.c:368`: exempt because it is bounded arithmetic over the monitor's own buffers or tables
- `firmware/monitor/monitor.c:373`: exempt because it is bounded arithmetic over the monitor's own buffers or tables
- `firmware/monitor/monitor.c:398`: exempt because the items are characters/tokens of ONE received line: a bad byte or token rejects that line with badarg/badcmd (or silence without a seq), the next line is unaffected
- `firmware/monitor/monitor.c:410`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:426`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:468`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:470`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:474`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:504`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:506`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:530`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:549`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:558`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:593`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:600`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:610`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:618`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:623`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:628`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:648`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:652`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:678`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:679`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:686`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:691`: exempt because the items are fields of an application-supplied plot def or enum/lane list; a malformed def is refused whole with a latched `!e plot` notice (plot_reject)
- `firmware/monitor/monitor.c:760`: exempt because it is bounded arithmetic over the monitor's own buffers or tables (stream table)
- `firmware/monitor/monitor.c:771`: exempt because it is bounded arithmetic over the monitor's own buffers or tables (stream table)
- `firmware/monitor/monitor.c:807`: exempt because it is bounded arithmetic over the monitor's own buffers or tables (stream table)
- `firmware/monitor/monitor.c:855`: exempt because it is bounded arithmetic over the monitor's own buffers or tables (field widths of a registered stream)
- `firmware/monitor/monitor.c:858`: exempt because it is bounded arithmetic over the monitor's own buffers or tables
- `firmware/monitor/monitor.c:890`: exempt because the items are characters/tokens of ONE received line: a bad byte or token rejects that line with badarg/badcmd (or silence without a seq), the next line is unaffected (marker text)
- `firmware/monitor/monitor.c:897`: exempt because the items are characters/tokens of ONE received line: a bad byte or token rejects that line with badarg/badcmd (or silence without a seq), the next line is unaffected
- `firmware/monitor/monitor.c:908`: exempt because the items are characters/tokens of ONE received line: a bad byte or token rejects that line with badarg/badcmd (or silence without a seq), the next line is unaffected
- `firmware/monitor/monitor.c:985`: complies: drain_can drops a frame on an undeclared bus (announced once per init) and continues; bounded at 64 pops per poll, so a shim that never empties cannot starve the line parser
- `firmware/monitor/monitor.c:1027`: exempt because the items are characters/tokens of ONE received line: a bad byte or token rejects that line with badarg/badcmd (or silence without a seq), the next line is unaffected
- `firmware/monitor/monitor.c:1028`: exempt because the items are characters/tokens of ONE received line: a bad byte or token rejects that line with badarg/badcmd (or silence without a seq), the next line is unaffected
- `firmware/monitor/monitor.c:1038`: exempt because the items are characters/tokens of ONE received line: a bad byte or token rejects that line with badarg/badcmd (or silence without a seq), the next line is unaffected
- `firmware/monitor/monitor.c:1053`: exempt because the items are characters/tokens of ONE received line: a bad byte or token rejects that line with badarg/badcmd (or silence without a seq), the next line is unaffected
- `firmware/monitor/monitor.c:1084`: exempt because the items are characters/tokens of ONE received line: a bad byte or token rejects that line with badarg/badcmd (or silence without a seq), the next line is unaffected (forbidden byte rejects the line with badarg)
- `firmware/monitor/monitor.c:1125`: complies: assemble_one; an over-length line sets g_overflow and is answered ERR 8 at its LF; one command per poll, the rest stays staged
- `firmware/monitor/monitor.c:1159`: exempt because it is bounded arithmetic over the monitor's own buffers or tables (stream table reset)
- `firmware/monitor/monitor.c:1196`: exempt because it is bounded arithmetic over the monitor's own buffers or tables (stream table rebroadcast)

## Class 17. Reported value is the request, not the result

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3.
Every daemon I started used port 18615, a scratch `--config` with its own `db_path`, and `MCUSCOPE_CONFIG_DIR`/`DATA_DIR`/`CACHE_DIR` under scratch, and was stopped by PID or by `mcu daemon stop` against its own scratch pid record.

### Findings

Listed at the top of this file.

### Sweep

Registry method: for every reported field and every applied setting, find where the value comes from.
Enumeration, done mechanically:
- the `/status` dict literal (`server.py:1051-1111`) and `SerialPort.status()` (`serial_link.py:1283-1331`);
- every `return` of each route in `grep -n "@app\.\(get\|post\|put\|delete\|websocket\)" mcuscope/server.py` (37 routes) that reports an outcome;
- the daemon's stdout banner and startup log (`daemon.py:188-197`, `:493-510`);
- `mcu daemon start/stop` result lines (`grep -n "started mcuscoped\|stopped mcuscoped" mcuscope/*.py`);
- every setting the daemon applies: `grep -n "PRAGMA\|set_retention_days\|set_max_db_bytes\|set_min_sessions\|set_enabled\|pj.configure\|_apply_overrides" mcuscope/*.py`.

**88 sites**: 20 `/status` top-level, 18 per-port, 27 other reporting surfaces (2 `GET /ports`, 19 route replies, 6 startup outputs and sys rows), 3 CLI result lines, 20 applied settings.

### Verdicts, `/status` top level (20)

- `version`: exempt, a module constant.
- `pid`: complies, `os.getpid()`.
- `ppid`: complies, `os.getppid()`.
- `uptime_s`: complies, measured.
- `now`: complies, measured.
- `db_path`: violates, R17-1.
- `config_path`: complies, the absolute path the daemon loaded.
- `config_warnings`: complies, the loader's own warnings plus the startup PlotJuggler failure.
- `db_size_bytes`: complies, `os.path.getsize` of file and WAL.
- `db_content_bytes`: complies, PRAGMA page and freelist counts.
- `db_max_bytes`: complies, `store.max_db_bytes()`, the cap in force.
- `lines_trimmed`: complies, a counter bumped by the delete that happened.
- `write_errors`: complies. `_write_req` now counts the dead-writer refusal (`store.py:1204-1209`), closing 2026-08-28 F2.
- `writer_alive`: complies, the task's state.
- `ws_dropped`: complies, counted at the shed.
- `capture`: complies, from the `meta` row.
- `session`: complies, read from `sessions`.
- `update`: complies; the previous check's answer, documented as such.
- `plotjuggler.enabled`: complies, `_target is not None`.
- `plotjuggler.dest`: violates, R17-3.

### Verdicts, per port (18)

- `alias`: exempt, identity.
- `device`: complies. `device or resolved_device or serial_number` closes 2026-08-28 F4 once connected; before the first connect the request is all there is, and the comment says so.
- `serial_number`: exempt, the binding itself, documented as such.
- `baud`: exempt; `Link` exposes no read-back (`socket://` has none), and pyserial raises rather than coercing. Same ruling as 2026-08-28.
- `eol`: complies; a host-applied default, validated by the `Eol` Literal on attach and `_as_choice` in the loader.
- `connected`: complies, set by the reader's real state.
- `held`: complies.
- `disconnect_reason`: complies, set at the event.
- `resolved_device`: complies, set in `_on_connect` from the opened device.
- `description`: complies, pyserial's description of it.
- `lines_rx`: complies. Counted on receive, before the store; loss now shows in `write_errors` and `rx_dropped`, so the pair is honest (2026-08-28 F3's pairing defect closed by F2's fix).
- `lines_tx`: complies, incremented after `_write_bytes` returns (`serial_link.py:1152`, `:1221`).
- `write_failures`: complies.
- `last_write_error`: complies.
- `last_write_error_ts`: complies.
- `write_failing_since`: complies.
- `target`: complies, parsed from the board's own `ping` answer.
- `rx_dropped`: complies, counted at each shed.

### Verdicts, other reporting surfaces (27)

- `GET /ports` `ports`: complies, the same `status()`.
- `GET /ports` `stored`: complies, read from the store.
- `POST /ports`: complies, the reply is the constructed port's `status()`.
- `POST /ports/{alias}/reconnect`: complies.
- `POST /ports/{alias}/disconnect`: complies.
- `DELETE /ports/{alias}`: complies, `ok` is `detach()`'s own result.
- `PUT /config/server`: complies; `restart_required` compares the normalised request with the running config (CLI overrides included), and `revision` is hashed from the bytes written (`config.py:605-608`).
- `PUT /config/storage`: complies, same; the live setters are applied before the reply.
- `PUT /config/update`: complies.
- `PUT /config/plotjuggler`: complies; it saves only, and its reply claims nothing about the running stream.
- `PUT /config/ports`: complies.
- `GET /plotjuggler` / `PUT /plotjuggler`: complies for `enabled`; `dest` violates, R17-3.
- `POST /sessions`: complies, read back with `resolve_session(str(id))`, where the id branch wins.
- `POST /sessions/stop`: complies; the verdict is `stop_session`'s own result (comment at `server.py:1508`).
- `DELETE /sessions/{id}`: complies. `lines_deleted` is this request's own count; `ok` ignores `delete_session()`'s bool, but the label is gone either way (idempotent end state).
- `POST /purge`: complies. `deleted` is the delete's count, bounded by the counted span (`max_id=hi_id`); dry run reports a count.
- `POST /send`: complies, `ok` after the write returned.
- `POST /break`: complies.
- `POST /cmd`: complies, the board's response.
- `POST /marker`: complies, the stored row's id.
- `POST /shutdown`: complies; `ok` means scheduled, which is all it claims.
- Banner `config:`: complies, with "not found, using defaults" when absent.
- Banner `database:`: violates, R17-1.
- Banner `web UI:`: complies; `--port` refuses 0, so the bound port is the configured one, and a failed bind exits 3.
- Banner `PlotJuggler: streaming`: violates, R17-2.
- Startup log (`_stdio.write_startup_log`): complies; pid from `os.getpid()`, no PlotJuggler claim.
- sys rows `port X connected: <dev>` / `target: ...`: complies, from the opened device and the board's reply.

### Verdicts, CLI result lines (3)

- `started mcuscoped (pid N)` and `--json` `pid`: violates on Windows, R17-4.
- `stopped mcuscoped (...)`: complies; success is `/status` going quiet, and the pid named is the proven one.
- Index-build `(pid N)` notices: same source as R17-4, same ruling.

### Verdicts, applied settings (20)

- `PRAGMA auto_vacuum=INCREMENTAL`: complies, read back and warned (`store.py:620-627`).
- `PRAGMA journal_mode=WAL`: complies, read back and warned (`store.py:632-640`).
- `PRAGMA synchronous=NORMAL`: complies.
- `PRAGMA cache_size` (writer): complies.
- `PRAGMA journal_size_limit`: complies.
- `PRAGMA foreign_keys=ON`: complies. Not read back and not reported; it must not run inside a transaction and does not.
  - The four above were read back by a probe on Store's connection on 3.10.20 and 3.11.15: `foreign_keys 1, synchronous 1, journal_mode wal, auto_vacuum 2, cache_size -65536, journal_size_limit 67108864, in_transaction False`.
  - Every `DELETE` runs on that connection, so the `ON DELETE CASCADE` the purge path relies on is in force.
- Reader `PRAGMA cache_size=-8000`: complies, perf only.
- `store.set_retention_days`: complies.
- `store.set_max_db_bytes`: complies, reported from the store.
- `store.set_min_sessions`: complies.
- `running.storage.auto_session` plus the live `start_session`: complies.
- `update_checker.set_enabled`: complies; `/status.update` comes from the checker.
- `pj.configure` (PUT /plotjuggler): complies; state swaps only after every raising step (`pjstream.py:112-131`).
- Startup `pj.configure(True)`: complies on `/status` (failure leaves `enabled` False and adds a warning); the banner is R17-2.
- `--host`: complies, written into the running config.
- `--port`: complies, written into the running config.
- `--plotjuggler`: complies, written into the running config.
- `--token` / `MCUSCOPED_TOKEN`: complies, written into the running config.
- Config loader coercions (`_as_int`, `_as_bool`, `_as_choice`): complies; they warn or refuse rather than coerce silently (class 60 owns the details).
- `SerialPort.eol` fallback (`serial_link.py:324`): complies; unreachable from the API or loader, both of which validate first.

### Registry sweep precision

The registry's wording ("every reported field") reads as `/status` only. Three of the four findings sit on other surfaces: the startup banner, the start line, and `/plotjuggler`.
Improved sweep: the enumeration above. Run the route-return grep, the banner/startup-log lines and the CLI result lines as well as the `/status` literal.
Also, "a value read back passes" is not enough for a *path*: `db_path` is read back from the config the daemon runs, yet it is still the request, because it is relative. Paths must be reported resolved.

### Owed on Windows

- R17-4: confirm what `started mcuscoped (pid N)` names against `/status` `pid`/`ppid` under a venv launcher.

## Class 18. Unmapped exception types at a third-party boundary

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3.

### Findings

Listed at the top of this file.

### Sweep method and counts

Every call into httpx, websockets, json, urllib, regex, tomlkit, pyserial and sqlite3, enumerated by AST (`scratch-16-18/libcalls.py`): module-qualified calls resolved through each file's imports (including function-local `import httpx`/`import regex`/`from serial.tools import list_ports`), plus method calls whose name belongs to one of those libraries' objects (`resp.json`, `http.request`, `ws.recv`, `pattern.search`, `conn.execute`, `ser.read`, ...). For each site it prints the except tuples of every enclosing `try` and `contextlib.suppress` inside the function.

```
cd host && uv run python ~/tt-data/mcuscope-2026-09-24/registry-leg/15-28/scratch-16-18/libcalls.py mcuscope
```
**346 sites**: 185 sqlite3-method, 53 regex-method, 39 pyserial-method, 17 httpx-method, 2 httpx/websockets-method, 4 websockets-method, 1 tomlkit-method; module calls: 16 json, 7 tomlkit, 6 urllib, 5 regex, 4 serial, 4 sqlite3, 2 httpx, 1 websockets.
- The method-name half matches noise by design (it cannot know the receiver's type): 99 of the 346 are stdlib `re` patterns, str methods, files, fds, sockets or project objects, each ruled "not a call into the library" below (`pidfile.py:144`, a file read, is ruled separately as the O-1 site). Every true library site was then ruled against the tuple in force where the error leaves the module, not only the local one.

Probes run to learn what each library raises beyond its own hierarchy:
- `regex` 2026.7.19: 18 pathological patterns; only nesting depth escapes `regex.error` (RecursionError); `timeout=` 0/inf raise TimeoutError, -1/nan do not.
- pydantic at the API: lone surrogates in `db_path`, `device`, `text`, `name`, `dest` all answer 422 before tomlkit or sqlite see them (`scratch-16-18/surrogate_probe.py`), so tomlkit.dumps and the store's inserts never get one.
- pyserial POSIX `send_break`: `termios.error` (R18-4). `write`, `read`, `in_waiting` wrap or raise OSError/SerialException, which every caller maps.
- JS extension (not in the registry's library list, cheap to close): `grep -n "JSON.parse\|new RegExp\|\.json()" host/mcuscope/webui/*.js` returns 13 sites (`chrome.js:27`, `can.js:279`, `cmdbar.js:24`, `state.js:99`, `state.js:110`, `state.js:369`, `state.js:482`, `layout.js:20`, `layout.js:62`, `exportrange.js:31`, `api.js:689`, `terminal.js:587`, `terminal.js:827`): all inside try/catch, complies. `state.js:369` (`r.json()` in the db-reference check) sits in a try whose catch re-throws as the caller's error, complies.

### Registry sweep imprecision

- "Diff its except tuple against the other call sites of the same library in the same file; a strict subset is the finding" finds only a class that SOME sibling already names. None of R18-1..4 is findable that way: `RecursionError` (regex), `ValueError` from `websockets.connect`, `OverflowError` from `float()` on a json value, and `termios.error` through pyserial are named by no sibling. Improved method: probe each library for what it raises (above), then check every site's in-force tuple against that list.
- Local tuples mislead in both directions: `cli_client.request` and all 185 store sqlite sites have empty local tuples by design (the mapping sits in `_daemon_errors`, `_writer`, `sweep_tick`, `_budget_error` or the global 500 handler), while `cli.py:1254`'s tuple is wide and still wrong because a frame-parsing clause swallows a connect-time ValueError. Rule on the tuple where the error leaves the module, and ask whether each clause's message fits every exception it can catch.
- The library list should name pyserial (R18-4) and regex (R18-1) alongside httpx, websockets, json and urllib; tomlkit and sqlite3 were clean. O-1 suggests adding strict text decodes of hand-editable files (`open(..., encoding=...)` under `except OSError`); `grep -n "open(.*encoding\|read_text(\|\.decode(" host/mcuscope/*.py | grep -v errors=` returns 22 lines (one a comment, `config.py:192`), of which `pidfile.py:143` is the only unmapped decode of an external file (`serial_link.py:128` reads a kernel sysfs attribute as ascii; the rest decode with a mapped handler or are writes).

### Owed on Windows or a real browser

- R18-4 is POSIX-only by construction: Windows pyserial `send_break` goes through `SetCommBreak` and raises `SerialException`, which is mapped. Unverified on Windows.
- R18-2 and O-1 have nothing platform-specific, but the Windows leg has not run them.

### Contradiction

- The leg brief (`docs/review/2026-09-23-opus55/registry-brief.md`) says one file, `docs/review/2026-09-23-opus55/registry-<first>-<last>.md`, and edits nothing else; the sub-brief says one file per class under `~/tt-data/mcuscope-2026-09-24/registry-leg/15-28/`. I followed the sub-brief (the narrower instruction for this sub-leg) and wrote nothing in the repo.

### The two questions (classes 16 and 18)

1. **What am I least confident about?**
   - The web UI class-16 rulings: 277 sites ruled from each grep line plus reading the ingest paths (`api.js` onmessage/backfill/staging, `plots.js` seed, `terminal.js` history), not every loop body. The render and DOM loops are ruled by category (the page's own model). No JS test file was run.
   - The 185 sqlite3 rulings are per function family (start, writer, loop reads, retention, export), each family's mapping read once; individual statements' failure modes were not driven.
   - `server.py:499` (lifespan autoconnect) is ruled compliant on the reasoning that the non-PortError raises from `attach` (StoreError, sqlite3 errors from the `!pd` prime query) are store-wide, not per entry. If a per-port prime can fail alone (a MatchBudgetExceeded on one busy port's 20 000-id lookback), one port would abort startup for all. Not driven.
   - R18-4's HTTP 500 is reasoned from the handler chain; only the function-level escape was driven.
2. **What should we have checked that we have not?**
   - No producer in the tree emits a non-finite f4: the sim's typed stream never does, so no end-to-end test crosses the "drop that point only" path that R16-1 breaks. A `mcu-sim --plot` sample carrying NaN would have caught it; worth a sim switch and a CLI decode test over it.
   - Every other consumer of a dropped-point sample: checked `plots.js` (by name, sound), `server._render`/exports (stored points, by name), `pjstream.send` (by name). Not checked: `mcu plot` subcommands beyond `export`, which read `/plot/*` JSON rather than decoding lines.
   - `i2c scan`'s mirror (R16-4) has siblings: other firmware handlers that loop over a shim call (`spi`, `adc`, `gpio` take one call each, so none loops; `drain_can` loops over `mon_can_rx_pop`, whose bool return has no error to misread). Nothing further found.
   - The update cache and pid record are two hand-editable files that each broke startup (R18-3, O-1). The config file, the capture lock file and the exported-file names were checked (mapped); the crash-log directory and `plotjuggler` state were not.

### Verdict list (346)

- `host/mcuscope/__init__.py:28` `sys.version.split` in `<module>` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/_stdio.py:256` `self._stream.write` in `write` (local handlers: OSError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/_stdio.py:366` `re.sub` in `set_report_key` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/_stdio.py:374` `fh.write` in `_write_report` (local handlers: OSError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/_stdio.py:321` `sys.version.split` in `interpreter_report` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/_stdio.py:330` `sys.version.split` in `python_line` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/cli.py:2314` `resp.raise_for_status` in `_poll_frames` (local handlers: -): complies: the caller (_dump_follow) maps httpx.HTTPError, KeyError, TypeError, ValueError; InvalidURL and 4xx die in _poll_frames
- `host/mcuscope/cli.py:2315` `resp.json` in `_poll_frames` (local handlers: -): complies: JSONDecodeError is a ValueError, charged per poll by _dump_follow
- `host/mcuscope/cli.py:1132` `ws.recv` in `_stage_backfill` (local handlers: -): complies: a failed recv is handed back as `pending` and surfaces through run()'s handlers
- `host/mcuscope/cli.py:1163` `urllib.parse.urlsplit` in `_accepts_tcp` (local handlers: OSError,ValueError): complies: (OSError, ValueError)
- `host/mcuscope/cli.py:1631` `urllib.parse.quote` in `session_export` (local handlers: -): exempt because quote() of a str cannot raise
- `host/mcuscope/cli.py:1804` `self.fh.write` in `write` (local handlers: -): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/cli.py:1808` `self.write` in `close` (local handlers: -): exempt because not a call into the library: the receiver is an object of this project or stdlib (heuristic method-name match)
- `host/mcuscope/cli.py:1809` `self.fh.close` in `close` (local handlers: -): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/cli.py:1856` `sys.stdout.write` in `_stream_export` (local handlers: -): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/cli.py:1872` `out.close` in `_stream_export` (local handlers: BrokenPipeError|OSError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/cli.py:1061` `pat.search` in `_follow_match` (local handlers: TimeoutError): complies: TimeoutError mapped (a slow pattern is counted, not fatal)
- `host/mcuscope/cli.py:1191` `regex.compile` in `_follow_ws` (local handlers: regex.error): VIOLATES (R18-1): maps regex.error only and has no length cap; a nested pattern raises RecursionError (traceback, crash log) where every daemon sibling refuses it at MAX_MATCH_LEN first
- `host/mcuscope/cli.py:1816` `self.fh.close` in `discard` (local handlers: suppress(OSError)): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/cli.py:1867` `out.write` in `to_file` (local handlers: -): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/cli.py:1960` `json.dumps` in `render` (local handlers: -): exempt because the row came out of resp.json
- `host/mcuscope/cli.py:1975` `out.close` in `log_export` (local handlers: OSError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/cli.py:2652` `err_fh.close` in `_start_daemon` (local handlers: -): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/cli.py:434` `urllib.parse.quote` in `detach` (local handlers: -): exempt because quote() of a str cannot raise
- `host/mcuscope/cli.py:803` `names.split` in `_make_decoder` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/cli.py:1038` `json.dumps` in `_tail_snapshot` (local handlers: -): exempt because the row came out of json.loads/resp.json, so it re-serialises
- `host/mcuscope/cli.py:1186` `urllib.parse.quote` in `_follow_ws` (local handlers: -): exempt because quote() of a str cannot raise
- `host/mcuscope/cli.py:1208` `json.loads` in `handle` (local handlers: json.JSONDecodeError,ValueError): complies: (JSONDecodeError, ValueError), charged per frame
- `host/mcuscope/cli.py:1254` `websockets.connect` in `run` (local handlers: BrokenPipeError|TimeoutError,asyncio.TimeoutError|OSError|websockets.exceptions.ConnectionClosed|websockets.exceptions.InvalidStatus|websockets.exceptions.WebSocketException|json.JSONDecodeError,ValueError|KeyError): VIOLATES (R18-2): an unparseable port/host in --url raises ValueError from websockets.connect, which the frame clause `(json.JSONDecodeError, ValueError)` catches: exit 1 'malformed frame from daemon' where every httpx sibling exits 3 'bad daemon url'
- `host/mcuscope/cli.py:1972` `out.write` in `log_export` (local handlers: OSError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/cli.py:2152` `json.dumps` in `can_dump` (local handlers: -): exempt because the frame came out of resp.json
- `host/mcuscope/cli.py:326` `re.sub` in `_derive_alias` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/cli.py:1140` `ws.recv` in `_stage_backfill` (local handlers: BaseException): complies: as :1132
- `host/mcuscope/cli.py:1848` `sys.stdout.write` in `to_stdout` (local handlers: BrokenPipeError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/cli.py:1241` `json.dumps` in `handle` (local handlers: AttributeError,KeyError,TypeError,ValueError): exempt because the row came out of json.loads (inside the per-row guard anyway)
- `host/mcuscope/cli.py:2257` `json.dumps` in `_dump_follow` (local handlers: KeyError,TypeError,ValueError > KeyboardInterrupt): exempt because the frame came out of resp.json (inside the per-frame guard anyway)
- `host/mcuscope/cli.py:1268` `ws.recv` in `run` (local handlers: BaseException > BrokenPipeError|TimeoutError,asyncio.TimeoutError|OSError|websockets.exceptions.ConnectionClosed|websockets.exceptions.InvalidStatus|websockets.exceptions.WebSocketException|json.JSONDecodeError,ValueError|KeyError): complies: ConnectionClosed (1008/1013/other), InvalidStatus, WebSocketException, OSError, timeouts mapped
- `host/mcuscope/cli_client.py:57` `resp.json` in `error_text` (local handlers: json.JSONDecodeError,ValueError): complies: (JSONDecodeError, ValueError), the same set as json_or_die
- `host/mcuscope/cli_client.py:128` `httpx.Client` in `open` (local handlers: -): complies: constructed inside _daemon_errors / probe_status's handler at every caller
- `host/mcuscope/cli_client.py:221` `resp.json` in `json_or_die` (local handlers: json.JSONDecodeError,ValueError): complies: (JSONDecodeError, ValueError) -> exit 1
- `host/mcuscope/cli_client.py:228` `self.request` in `get` (local handlers: -): complies: via request()
- `host/mcuscope/cli_client.py:231` `self.request` in `post` (local handlers: -): complies: via request()
- `host/mcuscope/cli_client.py:234` `self.request` in `put` (local handlers: -): complies: via request()
- `host/mcuscope/cli_client.py:237` `self.request` in `delete` (local handlers: -): complies: via request()
- `host/mcuscope/cli_client.py:136` `http.request` in `request` (local handlers: -): complies: _daemon_errors maps ConnectError/ConnectTimeout (3), TimeoutException (1), InvalidURL (3), HTTPError (3), ValueError (1)
- `host/mcuscope/cli_client.py:160` `http.request` in `probe_status` (local handlers: httpx.InvalidURL,httpx.HTTPError,json.JSONDecodeError,ValueError): complies: (InvalidURL, HTTPError, JSONDecodeError, ValueError) = _daemon_errors' set, as 'absent'
- `host/mcuscope/cli_client.py:247` `http.stream` in `download` (local handlers: OSError): complies: inside _daemon_errors; OSError from the file maps to 'cannot write'
- `host/mcuscope/cli_client.py:283` `http.stream` in `stream_text` (local handlers: BrokenPipeError|OSError): complies: inside _daemon_errors
- `host/mcuscope/cli_client.py:289` `resp.iter_text` in `stream_text` (local handlers: BrokenPipeError|OSError): complies: inside _daemon_errors
- `host/mcuscope/cli_client.py:163` `r.json` in `probe_status` (local handlers: httpx.InvalidURL,httpx.HTTPError,json.JSONDecodeError,ValueError): complies: as :160
- `host/mcuscope/cli_client.py:251` `resp.read` in `download` (local handlers: OSError): complies: resp.read() inside _daemon_errors
- `host/mcuscope/cli_client.py:256` `resp.iter_bytes` in `download` (local handlers: OSError): complies: inside _daemon_errors
- `host/mcuscope/cli_client.py:287` `resp.read` in `stream_text` (local handlers: BrokenPipeError|OSError): complies: resp.read() inside _daemon_errors
- `host/mcuscope/cli_client.py:257` `fh.write` in `download` (local handlers: OSError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/cli_daemonctl.py:27` `urlsplit` in `_host_port` (local handlers: ValueError): complies: urlsplit and .port inside except ValueError -> die_bad_url (3)
- `host/mcuscope/cli_daemonctl.py:252` `fh.write` in `_write_pid_record` (local handlers: OSError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/cli_daemonctl.py:98` `fh.read` in `_stderr_lines` (local handlers: OSError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/cli_output.py:54` `_CONTROLS.sub` in `visible` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/cli_output.py:73` `sys.stderr.write` in `err_write` (local handlers: OSError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/cli_output.py:233` `self._stream.write` in `write` (local handlers: BrokenPipeError|OSError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/cli_output.py:268` `json.dumps` in `out_json` (local handlers: BrokenPipeError|OSError): exempt because the document is built by the CLI from daemon JSON (always serialisable); write errors mapped
- `host/mcuscope/cli_output.py:231` `self._stream.write` in `write` (local handlers: BrokenPipeError|OSError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/config.py:664` `tomlkit.aot` in `save_ports` (local handlers: -): exempt because tomlkit.aot() takes no input
- `host/mcuscope/config.py:187` `<call>.unwrap` in `read_config` (local handlers: tomlkit.exceptions.TOMLKitError|OSError|TypeError,ValueError,AttributeError): complies: TOMLKitError, OSError, (TypeError, ValueError, AttributeError) -> ConfigError naming the file
- `host/mcuscope/config.py:563` `tomlkit.document` in `_read_doc` (local handlers: -): exempt because tomlkit.document() takes no input
- `host/mcuscope/config.py:567` `tomlkit.parse` in `_read_doc` (local handlers: Exception): complies: except Exception -> ConfigError naming the file
- `host/mcuscope/config.py:613` `tomlkit.table` in `_table` (local handlers: -): exempt because tomlkit.table() takes no input
- `host/mcuscope/config.py:666` `tomlkit.table` in `save_ports` (local handlers: -): exempt because tomlkit.table() takes no input
- `host/mcuscope/config.py:605` `tomlkit.dumps` in `_write_doc` (local handlers: -): complies: values reaching dumps are validated first (pydantic refuses lone surrogates, driven: 422; control characters refused by control_char_field)
- `host/mcuscope/config.py:187` `tomlkit.parse` in `read_config` (local handlers: tomlkit.exceptions.TOMLKitError|OSError|TypeError,ValueError,AttributeError): complies: TOMLKitError, OSError, (TypeError, ValueError, AttributeError) -> ConfigError naming the file
- `host/mcuscope/config.py:457` `ALIAS_RE.fullmatch` in `_from_dict` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/daemon.py:264` `probe.close` in `_port_conflict` (local handlers: -): exempt because not a call into the library: the receiver is a socket (heuristic method-name match)
- `host/mcuscope/link.py:196` `serial.serial_for_url` in `open_link` (local handlers: -): complies: _reader's except Exception around _open_link
- `host/mcuscope/link.py:128` `self._ser.read` in `read` (local handlers: -): complies: _reader's except Exception charges it and reconnects
- `host/mcuscope/link.py:163` `self._ser.write` in `write` (local handlers: -): complies: _write_bytes maps (SerialException, OSError); pyserial's POSIX write converts termios/OS errors
- `host/mcuscope/link.py:187` `self._ser.send_break` in `send_break` (local handlers: -): VIOLATES (R18-4): POSIX pyserial send_break is termios.tcsendbreak, which raises termios.error (not OSError, not SerialException); _break_locked maps only (SerialException, OSError)
- `host/mcuscope/link.py:191` `self._ser.close` in `close` (local handlers: -): complies: called under _close_link_locked's suppress(Exception)
- `host/mcuscope/link.py:160` `ser.read` in `drain` (local handlers: -): complies: as :151
- `host/mcuscope/link.py:169` `self._ser.cancel_read` in `cancel_read` (local handlers: suppress(Exception)): complies: suppress(Exception)
- `host/mcuscope/link.py:176` `self._ser.cancel_write` in `cancel_write` (local handlers: suppress(Exception)): complies: suppress(Exception)
- `host/mcuscope/link.py:282` `serial.SerialException` in `write` (local handlers: -): exempt because it constructs an exception
- `host/mcuscope/link.py:296` `serial.SerialException` in `send_break` (local handlers: -): exempt because it constructs an exception
- `host/mcuscope/link.py:53` `device.split` in `validate_device` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/link.py:151` `ser.read` in `drain` (local handlers: <finally-only>): complies: as :128 (drain; the burst is posted in _reader's finally first)
- `host/mcuscope/lockfile.py:162` `os.write` in `_write_holder` (local handlers: -): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/lockfile.py:149` `os.close` in `release` (local handlers: -): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/lockfile.py:169` `os.read` in `_read_holder` (local handlers: OSError,ValueError,UnicodeDecodeError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/lockfile.py:155` `json.dumps` in `_write_holder` (local handlers: -): exempt because the record is built from fixed scalar types
- `host/mcuscope/lockfile.py:170` `json.loads` in `_read_holder` (local handlers: OSError,ValueError,UnicodeDecodeError): complies: (OSError, ValueError, UnicodeDecodeError), diagnostic only
- `host/mcuscope/lockfile.py:133` `os.close` in `acquire` (local handlers: -): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/pidfile.py:63` `re.sub` in `pid_file_path` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/pidfile.py:116` `fh.read` in `pid_running` (local handlers: OSError,ValueError,IndexError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/pidfile.py:117` `<expr>.split` in `pid_running` (local handlers: OSError,ValueError,IndexError): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/pidfile.py:226` `os.write` in `claim` (local handlers: <finally-only> > OSError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/pidfile.py:144` `fh.read` in `read_pid_record` (local handlers: OSError): exempt from class 18 (a stdlib text read, not a library call), but it is the O-1 site: open(encoding="utf-8") under except OSError only, so a non-UTF-8 record raises UnicodeDecodeError
- `host/mcuscope/pidfile.py:229` `os.close` in `claim` (local handlers: suppress(OSError) > OSError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/pjstream.py:59` `_HOST_RE.fullmatch` in `parse_dest` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/pjstream.py:52` `_V6_RE.fullmatch` in `parse_dest` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/pjstream.py:134` `<expr>.close` in `configure` (local handlers: -): exempt because not a call into the library: the receiver is a UDP socket (pjstream) (heuristic method-name match)
- `host/mcuscope/pjstream.py:142` `<expr>.close` in `close` (local handlers: -): exempt because not a call into the library: the receiver is a UDP socket (pjstream) (heuristic method-name match)
- `host/mcuscope/pjstream.py:159` `json.dumps` in `send` (local handlers: OSError): exempt because the payload is finite floats and names from the protocol grammar; send OSError mapped
- `host/mcuscope/protocol.py:777` `body.split` in `_parse_enum_labels` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/protocol.py:810` `spec.split` in `_parse_channel_spec` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/protocol.py:891` `values_s.split` in `_decode_plot_sample_tokens` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/protocol.py:1107` `_MARKER_TICK_RE.fullmatch` in `parse_marker` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/protocol.py:691` `_PLOT_VALUE_RE.fullmatch` in `parse_plot_value` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/protocol.py:885` `_TICK_HEX_RE.fullmatch` in `_decode_plot_sample_tokens` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/protocol.py:1087` `_MARKER_TICK_RE.fullmatch` in `format_marker` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/protocol.py:291` `body.split` in `split_tokens` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/protocol.py:677` `_PLOT_NAME_RE.fullmatch` in `_valid_plot_name` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/protocol.py:796` `body.split` in `_parse_bit_lanes` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/protocol.py:779` `_LABEL_RE.fullmatch` in `_parse_enum_labels` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/protocol.py:779` `_ENUM_VAL_RE.fullmatch` in `_parse_enum_labels` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/protocol.py:1087` `<call>.split` in `format_marker` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/serial_link.py:125` `_TTY_NAME_RE.fullmatch` in `_is_absent_uart` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/serial_link.py:796` `buf.split` in `_on_bytes` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/serial_link.py:147` `list_ports.comports` in `cached_comports` (local handlers: -): complies: every caller maps Exception (port_identity, _device_present, _reader) or answers 500 (/devices)
- `host/mcuscope/serial_link.py:419` `link.cancel_read` in `stop` (local handlers: suppress(Exception)): complies: suppress(Exception)
- `host/mcuscope/serial_link.py:1042` `link.close` in `_close_link_locked` (local handlers: suppress(Exception)): complies: suppress(Exception) around the transport close
- `host/mcuscope/serial_link.py:1082` `link.write` in `_write_bytes` (local handlers: serial.SerialException,OSError): complies: (SerialException, OSError) -> PortError; pyserial write raises only those
- `host/mcuscope/serial_link.py:588` `link.close` in `_reader` (local handlers: suppress(Exception)): complies: suppress(Exception) around the transport close
- `host/mcuscope/serial_link.py:599` `link.read` in `_reader` (local handlers: Exception): complies: except Exception -> read_error + reconnect
- `host/mcuscope/serial_link.py:625` `link.cancel_write` in `_reader` (local handlers: suppress(Exception)): complies: suppress(Exception)
- `host/mcuscope/serial_link.py:1168` `link.send_break` in `_break_locked` (local handlers: serial.SerialException,OSError): VIOLATES (R18-4): the mapping for link.py:187; termios.error escapes to /break as a 500
- `host/mcuscope/serial_link.py:129` `fh.read` in `_is_absent_uart` (local handlers: OSError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/serial_link.py:938` `self._pj.send` in `_submit_rx_line` (local handlers: -): exempt because not a call into the library: the receiver is the PlotJuggler streamer (pjstream) (heuristic method-name match)
- `host/mcuscope/server.py:999` `re.sub` in `_safe_download_stem` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/server.py:2329` `json.dumps` in `_ws_json` (local handlers: -): exempt because the object is built from daemon scalars
- `host/mcuscope/server.py:3576` `<expr>.split` in `_parse_deadband` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/server.py:618` `h.split` in `_hostname_of` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/server.py:2628` `regex.compile` in `_do_wait` (local handlers: regex.error): complies: MAX_MATCH_LEN (200) checked first, then regex.error; RecursionError needs >= 662 chars (measured)
- `host/mcuscope/server.py:2822` `os.close` in `mkstemp` (local handlers: -): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/server.py:3214` `watch.close` in `_do_assert` (local handlers: -): exempt because not a call into the library: the receiver is an object of this project or stdlib (heuristic method-name match)
- `host/mcuscope/server.py:3347` `regex.compile` in `_check_match` (local handlers: regex.error): complies: as :2628
- `host/mcuscope/server.py:3379` `_FILENAME_UNSAFE.sub` in `export_filename` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/server.py:603` `o.split` in `_origin_matches_host` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/server.py:806` `parse_qs` in `_provided_token` (local handlers: -): exempt because parse_qs without strict_parsing/max_num_fields does not raise on a latin-1 str
- `host/mcuscope/server.py:1939` `id.split` in `can_frames` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/server.py:2360` `pattern.search` in `_search_batch` (local handlers: TimeoutError): complies: TimeoutError -> MatchBudgetExceeded
- `host/mcuscope/server.py:2717` `watch.close` in `_do_wait` (local handlers: -): exempt because not a call into the library: the receiver is an object of this project or stdlib (heuristic method-name match)
- `host/mcuscope/server.py:2776` `own.fullmatch` in `_sweep_export_orphans` (local handlers: suppress(OSError)): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/server.py:1815` `port.send_break` in `send_break` (local handlers: PortError): VIOLATES (R18-4): the endpoint catches PortError only, so the termios.error reaches the global 500 handler
- `host/mcuscope/server.py:2062` `names.split` in `plot_export` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/server.py:2175` `re.fullmatch` in `marker` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/server.py:2196` `websocket.close` in `ws` (local handlers: -): exempt because not a call into the library: the receiver is a Starlette WebSocket (heuristic method-name match)
- `host/mcuscope/server.py:2973` `pattern.search` in `_scan_batch` (local handlers: TimeoutError): complies: as :2360
- `host/mcuscope/server.py:2990` `regex.compile` in `_compile_patterns` (local handlers: regex.error): complies: as :2628
- `host/mcuscope/server.py:3439` `json.dumps` in `_jsonl_lines` (local handlers: -): exempt because the rows are daemon-written scalars
- `host/mcuscope/server.py:525` `pj.close` in `lifespan` (local handlers: suppress(Exception)): exempt because not a call into the library: the receiver is an object of this project or stdlib (heuristic method-name match)
- `host/mcuscope/server.py:1004` `stem.split` in `_safe_download_stem` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/server.py:1691` `zf.write` in `build` (local handlers: <finally-only>): exempt because not a call into the library: the receiver is a zipfile (heuristic method-name match)
- `host/mcuscope/server.py:2287` `websocket.close` in `ws` (local handlers: suppress(Exception)): exempt because not a call into the library: the receiver is a Starlette WebSocket (heuristic method-name match)
- `host/mcuscope/server.py:1712` `json.dumps` in `build` (local handlers: <finally-only>): exempt because the manifest is built from session scalars
- `host/mcuscope/server.py:2203` `websocket.close` in `ws` (local handlers: -): exempt because not a call into the library: the receiver is a Starlette WebSocket (heuristic method-name match)
- `host/mcuscope/server.py:2205` `websocket.close` in `ws` (local handlers: -): exempt because not a call into the library: the receiver is a Starlette WebSocket (heuristic method-name match)
- `host/mcuscope/server.py:1644` `_FILENAME_UNSAFE.sub` in `_build_bundle` (local handlers: MatchBudgetExceeded): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/server.py:1696` `fh.write` in `build` (local handlers: <finally-only>): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/sim.py:695` `chunk.split` in `_process_incoming` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/sim.py:878` `re.fullmatch` in `_cut_event` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/sim.py:1062` `srv.close` in `serve_tcp` (local handlers: -): exempt because not a call into the library: the receiver is a socket (heuristic method-name match)
- `host/mcuscope/sim.py:877` `rest.split` in `_cut_event` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/sim.py:957` `conn.send` in `_sock_send_lines` (local handlers: BlockingIOError,InterruptedError|OSError): exempt because not a call into the library: the receiver is a socket (sim) (heuristic method-name match)
- `host/mcuscope/sim.py:1020` `self._sock.close` in `stop` (local handlers: suppress(OSError)): exempt because not a call into the library: the receiver is a socket (heuristic method-name match)
- `host/mcuscope/sim.py:1088` `os.write` in `_pty_write_lines` (local handlers: BlockingIOError,InterruptedError): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/sim.py:677` `<expr>.split` in `_recover_seq` (local handlers: p.ProtocolError): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/sim.py:816` `conn.close` in `serve_listener` (local handlers: OSError): exempt because not a call into the library: the receiver is a socket (sim) (heuristic method-name match)
- `host/mcuscope/sim.py:839` `conn.recv` in `_serve_socket_client` (local handlers: BlockingIOError,InterruptedError|OSError): exempt because not a call into the library: the receiver is a socket (sim) (heuristic method-name match)
- `host/mcuscope/sim.py:1042` `sock.close` in `serve` (local handlers: suppress(OSError)): exempt because not a call into the library: the receiver is a socket (heuristic method-name match)
- `host/mcuscope/sim.py:1169` `os.close` in `serve_pty` (local handlers: suppress(OSError)): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/sim.py:243` `<call>.split` in `dispatch` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/sim.py:1135` `os.read` in `serve_pty` (local handlers: BlockingIOError|OSError > Exception > KeyboardInterrupt): exempt because not a call into the library: the receiver is a file, fd, stream or socket (stdlib I/O) (heuristic method-name match)
- `host/mcuscope/store.py:141` `re.findall` in `<module>` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/store.py:365` `<call>.fetchone` in `_rebuild_sessions_for_autoincrement` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:384` `conn.execute` in `_rebuild_sessions_for_autoincrement` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:401` `conn.commit` in `_rebuild_sessions_for_autoincrement` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:412` `bare.split` in `_schema_statement` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/store.py:172` `<call>.fetchone` in `_in_schema` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:229` `conn.executescript` in `_reclaim_pages` (local handlers: -): complies: retention/size path; sweep_tick catches Exception per tick
- `host/mcuscope/store.py:230` `conn.commit` in `_reclaim_pages` (local handlers: -): complies: retention/size path; sweep_tick catches Exception per tick
- `host/mcuscope/store.py:386` `conn.execute` in `_rebuild_sessions_for_autoincrement` (local handlers: Exception): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:387` `conn.execute` in `_rebuild_sessions_for_autoincrement` (local handlers: Exception): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:392` `conn.execute` in `_rebuild_sessions_for_autoincrement` (local handlers: Exception): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:393` `conn.execute` in `_rebuild_sessions_for_autoincrement` (local handlers: Exception): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:394` `conn.execute` in `_rebuild_sessions_for_autoincrement` (local handlers: Exception): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:395` `conn.execute` in `_rebuild_sessions_for_autoincrement` (local handlers: Exception): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:602` `sqlite3.connect` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:616` `conn.execute` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:620` `<call>.fetchone` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:632` `<call>.fetchone` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:643` `conn.execute` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:649` `conn.execute` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:652` `conn.execute` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:653` `conn.execute` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:662` `conn.executescript` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:664` `conn.commit` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:683` `<call>.fetchone` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:712` `<call>.fetchone` in `_close_crashed_auto_session` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:719` `self._conn.execute` in `_close_crashed_auto_session` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:723` `self._conn.commit` in `_close_crashed_auto_session` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:1082` `self._conn.executemany` in `_insert_batch` (local handlers: -): complies: inside _writer's per-batch except Exception (row-by-row fallback, then fail the batch and keep draining)
- `host/mcuscope/store.py:1145` `self._conn.execute` in `_insert` (local handlers: -): complies: inside _writer's per-batch except Exception (row-by-row fallback, then fail the batch and keep draining)
- `host/mcuscope/store.py:1371` `<call>.fetchone` in `active_session` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1380` `<call>.fetchone` in `_max_session_ref_id` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1393` `<call>.fetchone` in `get_session` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1415` `<call>.fetchone` in `resolve_session` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1438` `<call>.fetchall` in `list_sessions` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1481` `self._conn.execute` in `_open_session_locked` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1485` `self._conn.commit` in `_open_session_locked` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1523` `self._conn.execute` in `_stop_session_locked` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1527` `self._conn.commit` in `_stop_session_locked` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1544` `<call>.fetchone` in `_captured_traffic` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1555` `self._conn.execute` in `delete_session` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1556` `self._conn.commit` in `delete_session` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1582` `sqlite3.connect` in `export_session_db` (local handlers: -): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/store.py:1653` `self._conn.execute` in `_new_capture` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1655` `self._conn.commit` in `_new_capture` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1674` `self._conn.execute` in `_delete_lines` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1675` `self._conn.commit` in `_delete_lines` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1696` `<call>.fetchall` in `_plot_points_in` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1735` `<call>.fetchone` in `_max_id_sql` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1774` `<call>.fetchone` in `newest_ts_at_or_below` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:1886` `<call>.fetchone` in `_window_id_ceiling` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:1891` `<call>.fetchone` in `_window_id_ceiling` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:1918` `<call>.fetchone` in `_window_id_floor` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:1962` `<call>.fetchall` in `query_lines` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2109` `sqlite3.connect` in `_open_read_conn` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2111` `conn.execute` in `_open_read_conn` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2124` `conn.create_function` in `_query_lines_on` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2240` `<call>.fetchall` in `query_can_frames` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2386` `c.execute` in `_scan_plot_summary` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2541` `<call>.fetchall` in `query_plot_series` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2563` `<call>.fetchall` in `plot_streams` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2656` `<call>.fetchone` in `first_export_line_id` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2815` `sqlite3.connect` in `_open_export_conn` (local handlers: -): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/store.py:2849` `<call>.fetchone` in `retention_floor_id` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:2856` `<call>.fetchone` in `retention_floor_id` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:2919` `<call>.fetchone` in `before_ts_span` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:353` `conn.execute` in `_apply_migrations` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:365` `conn.execute` in `_rebuild_sessions_for_autoincrement` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:397` `conn.execute` in `_rebuild_sessions_for_autoincrement` (local handlers: Exception): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:399` `conn.rollback` in `_rebuild_sessions_for_autoincrement` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:483` `regex.compile` in `regexp` (local handlers: -): exempt because every pattern reaching REGEXP passed _check_match/_compile_patterns (length and regex.error) or is a fixed '^!pd '
- `host/mcuscope/store.py:686` `conn.execute` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:688` `conn.commit` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:763` `self._conn.close` in `stop` (local handlers: -): complies: closing our own connection in a finally/shutdown path
- `host/mcuscope/store.py:1087` `self._conn.executemany` in `_insert_batch` (local handlers: -): complies: inside _writer's per-batch except Exception (row-by-row fallback, then fail the batch and keep draining)
- `host/mcuscope/store.py:1092` `self._conn.executemany` in `_insert_batch` (local handlers: -): complies: inside _writer's per-batch except Exception (row-by-row fallback, then fail the batch and keep draining)
- `host/mcuscope/store.py:1166` `self._conn.executemany` in `_insert_children` (local handlers: -): complies: inside _writer's per-batch except Exception (row-by-row fallback, then fail the batch and keep draining)
- `host/mcuscope/store.py:1171` `self._conn.execute` in `_insert_children` (local handlers: -): complies: inside _writer's per-batch except Exception (row-by-row fallback, then fail the batch and keep draining)
- `host/mcuscope/store.py:1410` `<call>.fetchone` in `resolve_session` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1586` `conn.executescript` in `export_session_db` (local handlers: <finally-only>): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/store.py:1592` `conn.execute` in `export_session_db` (local handlers: <finally-only>): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/store.py:1641` `conn.close` in `export_session_db` (local handlers: -): complies: closing our own connection in a finally/shutdown path
- `host/mcuscope/store.py:2018` `<call>.fetchone` in `has_port_rows` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2390` `c.rollback` in `_scan_plot_summary` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2416` `c.execute` in `_scan_plot_rows` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2428` `<call>.fetchone` in `_scan_plot_rows` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2437` `<call>.fetchone` in `_scan_plot_rows` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2525` `<call>.fetchall` in `query_plot_series` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2973` `<call>.fetchone` in `content_bytes` (local handlers: -): complies: retention/size path; sweep_tick catches Exception per tick
- `host/mcuscope/store.py:2974` `<call>.fetchone` in `content_bytes` (local handlers: -): complies: retention/size path; sweep_tick catches Exception per tick
- `host/mcuscope/store.py:2975` `<call>.fetchone` in `content_bytes` (local handlers: -): complies: retention/size path; sweep_tick catches Exception per tick
- `host/mcuscope/store.py:3119` `<call>.fetchone` in `_reclaim_backlog` (local handlers: -): complies: retention/size path; sweep_tick catches Exception per tick
- `host/mcuscope/store.py:172` `conn.execute` in `_in_schema` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:351` `<call>.fetchall` in `_apply_migrations` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:411` `line.split` in `_schema_statement` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/store.py:492` `pat.search` in `regexp` (local handlers: TimeoutError): complies: TimeoutError flagged and re-raised for _budget_error
- `host/mcuscope/store.py:620` `conn.execute` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:632` `conn.execute` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:677` `<call>.fetchone` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:683` `conn.execute` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:712` `self._conn.execute` in `_close_crashed_auto_session` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:801` `c.close` in `_close_read_conns` (local handlers: suppress(Exception)): complies: suppress(Exception)
- `host/mcuscope/store.py:1371` `self._conn.execute` in `active_session` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1380` `self._conn.execute` in `_max_session_ref_id` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1393` `self._conn.execute` in `get_session` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1415` `self._conn.execute` in `resolve_session` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1438` `c.execute` in `list_sessions` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1544` `self._conn.execute` in `_captured_traffic` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1604` `conn.execute` in `export_session_db` (local handlers: <finally-only> > <finally-only>): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/store.py:1610` `conn.execute` in `export_session_db` (local handlers: <finally-only> > <finally-only>): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/store.py:1616` `conn.execute` in `export_session_db` (local handlers: <finally-only> > <finally-only>): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/store.py:1621` `conn.execute` in `export_session_db` (local handlers: <finally-only> > <finally-only>): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/store.py:1631` `conn.commit` in `export_session_db` (local handlers: <finally-only> > <finally-only>): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/store.py:1638` `conn.execute` in `export_session_db` (local handlers: <finally-only>): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/store.py:1696` `self._conn.execute` in `_plot_points_in` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1735` `c.execute` in `_max_id_sql` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1774` `c.execute` in `newest_ts_at_or_below` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:1886` `c.execute` in `_window_id_ceiling` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:1891` `c.execute` in `_window_id_ceiling` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:1918` `c.execute` in `_window_id_floor` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:1962` `c.execute` in `query_lines` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2012` `<call>.fetchone` in `count_lines` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2031` `c.execute` in `stored_ports` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2155` `self._conn.create_function` in `query_lines_safe` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2240` `conn.execute` in `query_can_frames` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2312` `<call>.fetchall` in `query_plot_channels` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2397` `c.execute` in `_scan_plot_rows` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2406` `<call>.fetchone` in `_scan_plot_rows` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2541` `c.execute` in `query_plot_series` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2563` `c.execute` in `plot_streams` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2623` `<call>.fetchall` in `export_sids` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2656` `conn.execute` in `first_export_line_id` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2716` `<call>.fetchall` in `iter_plot_export` (local handlers: <finally-only>): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/store.py:2732` `conn.close` in `iter_plot_export` (local handlers: -): complies: closing our own connection in a finally/shutdown path
- `host/mcuscope/store.py:2773` `conn.close` in `_iter_export_pages` (local handlers: -): complies: closing our own connection in a finally/shutdown path
- `host/mcuscope/store.py:2849` `self._conn.execute` in `retention_floor_id` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:2856` `self._conn.execute` in `retention_floor_id` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:2919` `c.execute` in `before_ts_span` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2958` `<call>.fetchone` in `_estimated_rows` (local handlers: -): complies: retention/size path; sweep_tick catches Exception per tick
- `host/mcuscope/store.py:954` `self._conn.commit` in `_writer` (local handlers: Exception > Exception): complies: inside _writer's per-batch except Exception (row-by-row fallback, then fail the batch and keep draining)
- `host/mcuscope/store.py:1153` `self._conn.execute` in `_insert` (local handlers: suppress(Exception)): complies: inside _writer's per-batch except Exception (row-by-row fallback, then fail the batch and keep draining)
- `host/mcuscope/store.py:1334` `json.dumps` in `_broadcast_batch` (local handlers: -): exempt because rows are daemon-built scalars (surrogates cannot reach them: pydantic refuses them at the API, driven)
- `host/mcuscope/store.py:1410` `self._conn.execute` in `resolve_session` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:1637` `conn.rollback` in `export_session_db` (local handlers: suppress(sqlite3.Error) > <finally-only>): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/store.py:1682` `<call>.fetchone` in `_delete_lines` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:2018` `c.execute` in `has_port_rows` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2428` `c.execute` in `_scan_plot_rows` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2437` `c.execute` in `_scan_plot_rows` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2525` `c.execute` in `query_plot_series` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2706` `<call>.fetchone` in `iter_plot_export` (local handlers: <finally-only>): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/store.py:2973` `self._conn.execute` in `content_bytes` (local handlers: -): complies: retention/size path; sweep_tick catches Exception per tick
- `host/mcuscope/store.py:2974` `self._conn.execute` in `content_bytes` (local handlers: -): complies: retention/size path; sweep_tick catches Exception per tick
- `host/mcuscope/store.py:2975` `self._conn.execute` in `content_bytes` (local handlers: -): complies: retention/size path; sweep_tick catches Exception per tick
- `host/mcuscope/store.py:3119` `self._conn.execute` in `_reclaim_backlog` (local handlers: -): complies: retention/size path; sweep_tick catches Exception per tick
- `host/mcuscope/store.py:351` `conn.execute` in `_apply_migrations` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:677` `conn.execute` in `start` (local handlers: -): complies: Store.start/migration; any sqlite3.Error refuses startup with the error (the daemon prints it and exits), which is the mapping every sibling in start() shares
- `host/mcuscope/store.py:2012` `c.execute` in `count_lines` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2312` `c.execute` in `query_plot_channels` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2406` `c.execute` in `_scan_plot_rows` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2623` `c.execute` in `export_sids` (local handlers: -): complies: read statement; a REGEXP budget stop is translated by _budget_error (query_lines_safe/_query_lines_threadsafe), any other sqlite3.Error is a 500 envelope, same as its siblings
- `host/mcuscope/store.py:2716` `conn.execute` in `iter_plot_export` (local handlers: <finally-only>): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/store.py:2958` `self._conn.execute` in `_estimated_rows` (local handlers: -): complies: retention/size path; sweep_tick catches Exception per tick
- `host/mcuscope/store.py:936` `self._conn.rollback` in `_writer` (local handlers: suppress(Exception) > Exception): complies: inside _writer's per-batch except Exception (row-by-row fallback, then fail the batch and keep draining)
- `host/mcuscope/store.py:972` `self._conn.rollback` in `_writer` (local handlers: suppress(Exception) > Exception): complies: inside _writer's per-batch except Exception (row-by-row fallback, then fail the batch and keep draining)
- `host/mcuscope/store.py:1603` `<call>.fetchone` in `export_session_db` (local handlers: <finally-only> > <finally-only>): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/store.py:1682` `self._conn.execute` in `_delete_lines` (local handlers: -): complies: loop-connection statement with no local handler, like every sibling in this family; a sqlite3.Error reaches the endpoint's global handler as a 500 JSON envelope (SPEC 3.4), or _writer/_retention's guards on the background paths
- `host/mcuscope/store.py:2706` `conn.execute` in `iter_plot_export` (local handlers: <finally-only>): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/store.py:1603` `conn.execute` in `export_session_db` (local handlers: <finally-only> > <finally-only>): complies: export copy/stream; the build's caller (_run_export) catches Exception and reports it, the temp files removed by _ExportJob
- `host/mcuscope/update_check.py:209` `json.dumps` in `_save_cache` (local handlers: -): exempt because the payload is a str/None and a float
- `host/mcuscope/update_check.py:76` `_VERSION_RE.fullmatch` in `parse_version` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/update_check.py:186` `json.loads` in `_load_cache` (local handlers: OSError,ValueError,KeyError,TypeError): VIOLATES (R18-3): (OSError, ValueError, KeyError, TypeError) misses OverflowError from float() of a JSON integer past the float range; the daemon then refuses to start
- `host/mcuscope/update_check.py:78` `cleaned.split` in `parse_version` (local handlers: -): exempt because not a call into the library: the receiver is a stdlib `re` pattern or str method with a fixed pattern (heuristic method-name match)
- `host/mcuscope/update_check.py:268` `httpx.AsyncClient` in `check_once` (local handlers: Exception): complies: except Exception, a background check that must never raise (documented)
- `host/mcuscope/update_check.py:272` `resp.raise_for_status` in `check_once` (local handlers: Exception): complies: as :268
- `host/mcuscope/update_check.py:273` `resp.json` in `check_once` (local handlers: Exception): complies: as :268

## Class 19. Two engines validating one thing

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3 (checked with `git rev-parse HEAD`).

### Findings

Listed at the top of this file.

### Sweep method and count

The registry's sweep is a method, not a command: "list every validation duplicated between client and daemon, or between host and firmware, and name the single implementation both use; for a hand-written mirror, diff clause by clause."

How I enumerated the list:
- Read every decoder and validator in `protocol.py`, `sim.py`, `server.py` bodies and routes, the CLI option callbacks, `firmware/monitor/monitor.c` and `monitor_cmds.c`, and the web UI's `state.js`, `can.js`, `plots.js`, `pane.js`, `terminal.js`, `settings.js`, `statusbar.js`, `cmdbar.js`, `exportrange.js` and `api.js`.
- Seeded the list with a grep for self-declared mirrors: `grep -rniE "mirror|mirrors|mirrored|duplicated like|same as (server|protocol|config)|as protocol\.|protocol\.[a-z_]+ (does|reads)" host/mcuscope --include=*.py --include=*.js | grep -v vendor`. It returned 47 lines, 27 of them about validation or grammar; every one is in the list below.
- CLI numeric ranges came from walking the click tree (the class 22 G sweep) and comparing each against the server's `Field`/`Query` bound.

**70 sites.** Every hand-written mirror was diffed clause by clause against its original.

### Site rulings

Web UI against daemon:
1. `state.js:237` splitTokens vs `protocol.split_tokens`+`normalize_line`: complies (one CR/LF/CRLF dropped, U+0020 runs only).
2. `state.js:218` isDecimalToken vs `is_decimal_token`: complies (JS `\d` is ASCII, 20-digit cap present).
3. `state.js:261` markerTick vs `parse_marker`: violates (R19-1). For rx rows every clause matches: `!m ` prefix, space-split first token, `@digits`, text required, 20 digits, 2^32-1.
4. `terminal.js:99` marker prefix strip vs `parse_marker`: violates (R19-1, display half); for rx rows, display only.
5. `state.js:249` computeTick tag dispatch vs `parse_can_family`/serial_link dispatch: complies.
6. `can.js:58` parseCanEvent vs `parse_can_event_tokens`: violates via the Python side (R22-1). The other clauses match: 5 tokens, family, tick, flags, 0x+16-digit id, id range, RTR digit <= 8, payload pairs <= 8.
7. `can.js:234` canFilterPattern vs the `!can` grammar: exempt because it is a pane text filter over the id and admits undecodable lines of that id on purpose. It is not a validity check.
8. `can.js:599` csvField vs `server._csv_cell`: complies (same formula set `= + - @ \t \r`, same quote set).
9. `plots.js:24-39` PLOT_TYPES, ENUM_TYPES, BITS_TYPES, PLOT_NAME_RE, LABEL_RE vs protocol constants: complies.
10. `plots.js:95` parsePlotValue vs `parse_plot_value`: complies (same regex, finiteness check).
11. `plots.js:101` parsePlotAdhoc vs `_parse_plot_adhoc_tokens`: complies (tick, `=` split, name, duplicate names).
12. `plots.js:122` parseChannelSpec vs `_parse_channel_spec`: complies.
13. `plots.js:159` parseEnumLabels vs `_parse_enum_labels`: complies. The 20-digit cap and sign-character rule are present. Values past 2^53 lose precision in JS, but neither side bounds a value to its type width, so no decoded field can reach one.
14. `plots.js:182` parseBitLanes vs `_parse_bit_lanes`: complies.
15. `plots.js:189` parsePlotDef vs `_parse_plot_def_tokens`: complies (single-digit sid, one shared name space).
16. `plots.js:218` decodePlotField vs `protocol._decode_field`: violates via the Python side (R22-1).
17. `plots.js:230` decodePlotSample vs `_decode_plot_sample_tokens`: complies apart from 16.
18. `plots.js:265` plotIngest `chan === "event"` gate vs the daemon's rx-only decode: complies (tx rows are always chan `cmd`, `serial_link.py:1156,1224`).
19. `pane.js:61` + `terminal.js:587` pane regex vs `store._make_regexp`: violates (R19-2).
20. `terminal.js:433` MAX_MATCH_LEN 200 vs `server.MAX_MATCH_LEN`: complies.
21. `state.js:156-158` MAX_BAUD, MAX_TIMEOUT_MS, MAX_DB_BYTES vs `config.MAX_BAUD`, `server.MAX_TIMEOUT_MS`, `ConfigStorageBody`: complies.
22. `settings.js:696-722` port, retention, size cap and session floor bounds vs ConfigServerBody/ConfigStorageBody: complies (the cap is whole MB, so the 1 MiB floor cannot be undershot).
23. `settings.js:622` per-port baud bound vs ConfigPortEntry: complies.
24. `statusbar.js:693` attach baud bound vs PortAttach: complies.
25. `cmdbar.js:210` timeout bound vs CmdBody: complies (the grammar of intField is R22-5).
26. `cmdbar.js:263` marker blank refusal and trim vs MarkerBody: violates (R19-3).
27. `statusbar.js:601` deriveAlias vs `cli._derive_alias`: complies (same replacement, 32-char cut, leading `_.-` strip, `board` fallback).
28. `exportrange.js:40` inverted vs `server._check_window`: complies.
29. `api.js:268` PLOT_DEF_LOOKBACK vs `serial_link.PLOT_DEF_LOOKBACK`: complies (20000).
30. `api.js:440` LINES_LIMIT_MAX vs the `store.query_lines` clamp (1000): complies.

CLI against daemon:

31. `protocol.py:116` repeat_refusal: complies (one implementation used by both).
32. `cli.py:1181` `--match` engine vs store: complies (both `regex`).
33. `cli.py:1048` FOLLOW_MATCH_TIMEOUT_S vs `store.MATCH_TIMEOUT_S`: complies (0.25 both).
34. `cli.py:617` MATCH_BUDGET_S vs `store.MATCH_BUDGET_S`: complies.
35. `cli.py:445` MAX_TIMEOUT_MS vs server: complies.
36. `cli.py:758` MAX_MS vs `server.MAX_MS`: complies (IntRange max 10^15 on every `--last-ms`).
37. `break`/`sysrq` `--ms` 1..2000 vs BreakBody: complies.
38. `cli.py:2028` BUS_OPTION 1..9 vs protocol CAN_BUS_MIN/MAX: complies (shared constants).
39. `assert --min-window`/`--last-ms` ranges vs AssertBody: complies.
40. `cli.py:2045` `--rtr` 0..8 vs SPEC 2.4: complies.
41. `cli.py:333` EOL_CHOICES from `protocol.EOL_BYTES`: complies (shared).
42. `cli.py:317` `_derive_alias` vs `config.ALIAS_RE`: complies.
43. `--chan`, no client check, vs server `Chan` Literal: complies. The looser side is no check at all, and the daemon refused `tail -f -n 0 --chan bogus` (driven, exit 1).
44. `--match` length, no client check, vs `MAX_MATCH_LEN`: complies (driven: `tail -f -n 0` with a 243-char pattern got the daemon's refusal).
45. `cli.py:583` DEF_LOOKBACK vs `serial_link.PLOT_DEF_LOOKBACK`: complies.
46. `cli_output.py:365` LineDecoder vs the daemon decode: complies (the same `protocol.PlotDecoder`, which is where R22-1 lives).
47. `server.py:199` `_ALIAS_RE` (a pydantic-core pattern, and `re.fullmatch` at /marker) vs `config.py:64` ALIAS_RE: complies. They are two copies of one pattern, driven identical on `sim`, `sim\n`, `s٣`, `-x`, 32 and 33 chars.
48. `cli_argv.py:21` `_ATTACHED_P`: exempt because it is an argv hoisting heuristic, not a check (the daemon validates the alias).
49. click numeric option parsing vs the daemon's strict body grammar: violates, filed as R22-2.

Host against firmware, and the simulator that stands in for it:

50. `protocol.split_tokens` vs `monitor.c:1024` tokenize: complies (U+0020 only).
51. MAX_COMMAND_TOKENS 12 vs tokenize's 13 sentinel and `sim.py:195`: complies.
52. MAX_LINE_BYTES 255 vs MONITOR_LINE_MAX: complies.
53. `parse_seq_token` 1..65535 vs `process_line` (`mon_parse_dec_u32` + range): complies. The digit-count bound differs, and SPEC 2.1 lets a receiver bound it.
54. `sim._recover_seq` vs `recover_seq`: complies.
55. ERROR_NAMES vs `err_name`/MONITOR_ERR_*: complies.
56. sim `can tx` (`parse_can_tx_args`) vs `cmd_can_tx`: violates (R22-1, whitespace in hex data).
    - RTR `08` is exempt: SPEC 2.4 documents the firmware's tolerance.
    - An id with more than 16 hex digits of leading zeros (firmware accepts, sim refuses) is exempt as a receiver-side bound; see the two questions in class-22.md.
57. sim `_i2c_addr` vs `cmd_i2c_*` address: complies.
58. sim `_parse_dec(1..64)` vs `mon_parse_dec_u32` n 1..64: complies.
59. sim `i2c wr`/`wrrd` and `spi xfer` data (`hex_to_bytes`) vs `mon_hex_decode`: violates (R22-1). Driven: sim answered `OK` to `spi xfer imu \tAB\x0b`; `fw_hex_probe.c` shows `mon_hex_decode` returns -1 for all three tab/VT/FF cases.
60. sim `gpio set` vs `cmd_gpio_set`: complies (exact `0`/`1`).
61. sim `can filter` vs `cmd_can_filter`: exempt because SPEC 2.4 documents both differences (the 32-bit cap and the `xx` flags token).
62. sim can family/bus vs `family_match`/`can_bus_of`: complies (the badcmd/badarg split is the same).
63. `!pd` grammar, `monitor.c:405-737` validators vs `_parse_plot_def` vs `plots.js`: complies. `*1e999` is exempt (a documented gap, `monitor.h` and SPEC 2.5).
64. marker: `monitor_mark`/`starts_with_tick_sigil` vs `format_marker`/`parse_marker`: violates, filed as R22-6 (three whitespace sets).
65. `emit_can_event` vs `parse_can_event`: complies (bus <= 9 is a compile-time check, the id is masked, the RTR digit is clamped).
66. `monitor_plot` `!ps` emitter vs `_decode_plot_sample`: complies (unpadded hex tick, fixed-width fields).
67. `sim._cut_event` vs `monitor.c` event_end: complies. Diffed clause by clause: space search over indices 2..255, trailing-space strip, a type over 16 chars reads `?`, a lone marker `@tick` is not sent.
68. `sim._sanitize` vs `write_line`: complies (0x20..0x7E, else `.`).
69. `assemble_one` drops every CR vs `normalize_line`, which strips one trailing: exempt because the host never sends a CR (`serial_link._encode_wire` refuses it).
70. 7-bit input: the firmware rejects bytes above 0x7F and the simulator accepts them: exempt (SPEC 2.1 documents it).

### Sweep precision

- The registry's sweep names no mechanical enumeration, so its completeness depends on the sweeper.
  - Proposed seed, run above: `grep -rniE "mirror|mirrors|mirrored|duplicated like" host/mcuscope --include=*.py --include=*.js | grep -v vendor`.
  - Plus a walk of the click tree and the FastAPI routes for duplicated bounds (the scripts are in the class 22 G and H sweeps).
- The two class 19 misses found here (R19-1, R19-2) had the same shape as the registry's examples. Each mirror dropped a precondition or domain of the original: the rx direction, and ASCII-only subject text. Neither was a character-set clause.
  - Suggested addition to the entry: "diff the mirror's *preconditions* (which rows reach it) and its *input domain* (which text can reach them), not only its clauses".

### Owed on a real browser

- R19-1 visual: a host marker typed as `!m @N x` shows tick N in the tick column and loses its prefix in the divider. Headless-scriptable; not run.
- R19-2 needs no browser. ECMAScript fixes `\d`, `\w`, `\s` and `.` without the `u` flag, so every engine gives node's answer.

Windows: nothing class 19 specific.

## Class 20. Non-sargable bound on a hot query

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3. SQLite 3.47.1 (the host venv, CPython 3.13.5); the three finding plans re-explained on 3.45.1 and 3.53.1 with the same result.

HEAD moved during the sweep (f31ecd9 to 1a1251a, other legs' commits). None touch store.py, server.py, cli.py or the webui; serial_link.py changed two comments; `host/tests/test_pidfile.py` gained a test at line 63, so its line numbers below are +19 at 1a1251a (clock sites 155, 156, 285, 286 are now 174, 175, 304, 305). Every `file:line` here is at f31ecd9.
Scratch: `~/tt-data/mcuscope-2026-09-24/registry-leg/15-28/scratch-20-21/` (every script and log named below is there).

### Findings

Listed at the top of this file.

### Sweep method

1. Mechanical static enumeration, `sites.py` (AST: every `.execute`/`.executemany`/`.executescript` call in `host/mcuscope/*.py`): **88 sites**, all in `store.py`; `server.py` and `lockfile.py` import sqlite3 but execute nothing.
2. Dynamic enumeration, `probe20.py`: a 171-row capture with three ports (`""`, `A`, `B`), CAN frames, ad-hoc and stream plot points, `!pd` rows, closed sessions, a crashed auto session, then the app under TestClient with `sqlite3.connect` wrapped so every connection the daemon opens (loop, read workers, export, bundle) has a trace callback recording the statement, the call site and loop/worker.
   Requests: `/lines` 4110 (4 ports x 4 chan sets x match x 64 window combinations x 2 orders), `/lines/export` 2060, `/can/frames` 3096, `/plot/series` 204, `/plot/export` 3468, `/plot/channels` 4, `/assert` 133, `/wait` 16, `/sessions*` (list, export, bundle, start, stop, delete with data), `/purge` 8 (dry and real), `/marker`, `/ws`, `/status`, plus `sweep_tick` with and without a 1-byte cap.
   Result: 85,357 statements, 14,876 distinct, 2,191 templates, 84 call sites: 74 of the 88 static sites, plus 10 implicit COMMIT/ROLLBACK sites.
3. `EXPLAIN QUERY PLAN` of every distinct statement on the daemon's own loop connection (`store._conn`, as `captured_plan` does), `sqlite_stat1` checked absent at explain time. REGEXP lives only on the read connections, so a no-op REGEXP was registered on `_conn` for the explain; the plan does not depend on its body.
4. Every plan with a table SCAN, full index SCAN, TEMP B-TREE or a lower-bound-only rowid SEARCH (562 templates) was judged, and each suspect timed on a 1M-line capture (`bulk20.py` and siblings): the entry's "time it with the bound near the end, count it per request" applied. Per-request counts from the trace: `_window_id_ceiling` 1, `_window_id_floor` at most 6, `newest_ts_at_or_below` at most 3.

Not reached by any request (14 sites), and why: the sessions AUTOINCREMENT rebuild and column-migration DDL (startup on an older capture, 9 sites: 353, 384-397), the one-row insert fallback after a failed batch (4 sites: 1145, 1153, 1166, 1171), `query_plot_channels` (2312, no caller). Ruled below.

### Verdicts, 88 static sites (`store.py`)

- 172 `_in_schema`: exempt because it reads `sqlite_master` at startup only.
- 229 `PRAGMA incremental_vacuum`: exempt because it is a pragma with a bounded step.
- 351 `PRAGMA table_info`: exempt because it is startup DDL introspection.
- 353 migration DDL: exempt because it is startup DDL.
- 365 read of `sqlite_master`: exempt because it is the startup migration.
- 384 `BEGIN IMMEDIATE`: exempt because it is the startup migration.
- 386 CREATE `sessions_autoinc`: exempt because it is the startup migration.
- 387 INSERT...SELECT into `sessions_autoinc`: exempt because it is a one-time whole-table copy at startup.
- 392 DROP INDEX: exempt because it is the startup migration.
- 393 DROP INDEX: exempt because it is the startup migration.
- 394 DROP TABLE: exempt because it is the startup migration.
- 395 ALTER TABLE RENAME: exempt because it is the startup migration.
- 397 re-create index: exempt because it is the startup migration.
- 616 `PRAGMA auto_vacuum=INCREMENTAL`: exempt because it is a pragma.
- 620 `PRAGMA auto_vacuum`: exempt because it is a pragma.
- 632 `PRAGMA journal_mode`: exempt because it is a pragma.
- 643 `PRAGMA synchronous`: exempt because it is a pragma.
- 649 `PRAGMA cache_size`: exempt because it is a pragma.
- 652 `PRAGMA journal_size_limit`: exempt because it is a pragma.
- 653 `PRAGMA foreign_keys`: exempt because it is a pragma.
- 662 `executescript(SCHEMA)`: exempt because it is DDL.
- 677 `SELECT MAX(ts)`: complies, covering-index MAX (one seek).
- 683 meta lookup: complies, `sqlite_autoindex_meta_1 (key=?)`.
- 686 INSERT meta: exempt because it is a keyed write.
- 712 newest row: complies, PK walk from the end with LIMIT 1 (plan text `SCAN lines`, one row read).
- 719 UPDATE sessions by id: complies, `rowid=?`.
- 1082 batch INSERT lines: exempt because it is an insert.
- 1087 batch INSERT plot_points: exempt because it is an insert.
- 1092 batch INSERT can_frames: exempt because it is an insert.
- 1145 single INSERT lines: exempt because it is an insert (fallback after a failed batch; not reached).
- 1153 DELETE by id: complies, PK (fallback; not reached).
- 1166 single INSERT plot_points: exempt because it is an insert (fallback; not reached).
- 1171 single INSERT can_frames: exempt because it is an insert (fallback; not reached).
- 1371 `active_session`: complies, partial index `idx_sessions_active` (at most one row), 0.005 ms at 1M.
- 1380 `_max_session_ref_id`: complies, aggregate over `sessions` only (a table of runs, not lines).
- 1393 `get_session`: complies, `rowid=?`.
- 1410 `resolve_session` by id: complies, `rowid=?`.
- 1415 `resolve_session` by name: complies, `idx_sessions_name (name=?)`.
- 1438 `list_sessions`: complies, both ends bound (`rowid>? AND rowid<?`), off the loop; 47 ms at 1M (one 600k session), as its docstring states.
- 1481 INSERT sessions: exempt because it is an insert.
- 1523 UPDATE sessions by id: complies, `rowid=?`.
- 1544 `_captured_traffic`: complies, MULTI-INDEX OR over `idx_lines_chan_id` bounded at both ends, LIMIT 1.
- 1555 DELETE sessions by id: complies, `rowid=?`.
- 1586 `executescript(SCHEMA)` on the export file: exempt because it is DDL on a new file.
- 1592 ATTACH: exempt because it is not a query.
- 1603 `MAX(id)` of `src.lines`: complies, PK max (reasoned: runs against the ATTACHed file, not explained).
- 1604 copy lines by id range: complies, `id >= ? AND id <= ?` on the PK (reasoned, same reason).
- 1610 copy can_frames by line_id range: complies, `line_id` is the PK (reasoned).
- 1616 copy plot_points by line_id range: complies, `idx_plot_line` (reasoned).
- 1621 INSERT the session row: exempt because it is an insert.
- 1638 DETACH: exempt because it is not a query.
- 1653 INSERT OR REPLACE meta: exempt because it is a keyed write.
- 1674 `_delete_lines`, four shapes: oldest chunk (PK walk, LIMIT) complies; id-range chunk (`rowid>? AND rowid<?`) complies; `id < floor` oldest chunk (`rowid<?`) complies; expired chunk with a floor **violates** (R20-3); expired chunk without a floor complies.
- 1682 `SELECT MAX(ts)`: complies, covering-index MAX.
- 1696 `_plot_points_in`: complies, bounded by the chunk's id list (temp b-tree over at most one chunk).
- 1735 `MAX(id)`: complies, PK max.
- 1774 `newest_ts_at_or_below`: complies, PK seek, 0.005 ms at 1M.
- 1886 newest row: complies, PK walk from the end, LIMIT 1.
- 1891 `_window_id_ceiling` walk: complies, off the loop, once per request (trace), 182 ms at 1M for a mid-capture bound, the cost its docstring accepts.
- 1918 `_window_id_floor`: complies, `ORDER BY ts DESC LIMIT 1` seek, 0.006 ms at 1M near either end.
- 1962 `query_lines`, 25 plan shapes: complies; every filtered shape seeks an index at both ends or rides the PK with LIMIT; the multi-channel `IN` shapes carry a temp b-tree but measured 1.2-1.8 ms at 1M on the loop (SQLite stops each IN branch at LIMIT); `SCAN lines` only for the unfiltered read.
- 2012 `count_lines`, 19 shapes: complies, off the loop, every one a seek or the whole-window aggregate the verdict needs.
- 2018 `has_port_rows`: complies, covering seek, 0.005 ms at 1M.
- 2031 `stored_ports`: complies, recursive skip-scan (one seek per port), 0.016 ms.
- 2111 `PRAGMA cache_size`: exempt because it is a pragma.
- 2240 `query_can_frames`, 8 shapes: `rowid>?` with a port or bus filter **violates** (R20-2); the rest comply (bounded ranges, `idx_can_id_line` seeks, PK walk with LIMIT when unfiltered; pinned by `test_store_lines_plan.py:261`).
- 2312 `query_plot_channels`: exempt because no handler calls it (see R20-5).
- 2386 `BEGIN`: exempt because it opens the read snapshot.
- 2397 per-name counts: complies, whole-table aggregate off the loop, 15 ms at 1M.
- 2406 per-port count: complies, aggregate off the loop, 83 ms for the busy port at 1M.
- 2416 per-port name aggregate: complies, aggregate off the loop, 57 ms at 1M, order pinned by INDEXED BY/CROSS JOIN.
- 2428 newest point per name: complies, index seek.
- 2437 last sample: complies, index seek.
- 2525 `query_plot_series` newest: complies, `idx_plot_name_line` seek.
- 2541 `query_plot_series` decimation: complies, window aggregate off the loop.
- 2563 `plot_streams` (bundle): complies, bounded by the session's id range.
- 2623 `export_sids`: with 2+ names and `port` **violates** (R20-1); single-name forms comply (the DISTINCT needs every row in the window, off the loop).
- 2656 `first_export_line_id`: with 2+ names **violates** (R20-1); single-name forms comply.
- 2706 `MAX(line_id)` of plot_points: complies, covering max.
- 2716 export page: complies, `idx_plot_line (line_id>? AND line_id<?)`.
- 2849 `retention_floor_id`: complies, PK walk with a small OFFSET (`min_sessions`).
- 2856 `MIN(start_id)`: complies, aggregate over `sessions` only.
- 2919 `before_ts_span`: complies, off the loop, once per purge, cost proportional to what is purged (104 ms for half of 1M).
- 2958 `_estimated_rows` `COUNT(*)` on the loop: complies, 1.8 ms at 1M (btree count), runs only when over the cap.
- 2973 `PRAGMA page_size`: exempt because it is a pragma.
- 2974 `PRAGMA page_count`: exempt because it is a pragma.
- 2975 `PRAGMA freelist_count`: exempt because it is a pragma.
- 3119 `PRAGMA freelist_count`: exempt because it is a pragma.

Dynamic-only sites (10: store.py 688, 723, 954, 1485, 1527, 1556, 1631, 1655, 1675, 2390): exempt because each is the COMMIT of `conn.commit()` or the ROLLBACK closing the read snapshot.

Existing plan tests audited for the "explain what the daemon issues" rule (32 grep hits for `EXPLAIN QUERY PLAN|captured_plan|_plan(`): comply except `test_sessions.py:906` and `test_store_lines_plan.py:503` (R20-4) and `test_store_lines_plan.py:320` (R20-5). `test_store_sessions.py:28` explains `SESSION_LIST_SQL`, the constant the store executes: complies.

### Where the registry's sweep is imprecise

- "Every statement reachable from a handler" needs the parameter *shapes* driven, not just the handlers: R20-1 appears only when a list-valued parameter has two or more elements. Improved: drive every list-valued parameter (`names`, `chan`, `id`) with one and with several values, and when grouping by template never fold `IN (?)` into `IN (?,?)`.
- A lower-bound-only `rowid>?` read under LIMIT is only open-ended when a non-indexed filter can reject every row. Improved: explain and time each such read with a filter value that matches nothing (a quiet port, an absent bus) and a stale watermark, since that is what a follow polls with (R20-2).
- The `(ts<?)` note should name non-indexed guards: a `SEARCH ... (ts<?)` with an extra `id < ?` filter walks every row the guard rejects (R20-3). Time it with the guard rejecting most of the range.
- Grouping by statement text merges sites: `SELECT COUNT(*) FROM lines` is both `count_lines` (off the loop) and `_estimated_rows` (on it). Improved: record the call site and thread with each traced statement (`probe20.py` does).
- The entry's `/plot/channels?port=` example now names code no handler runs (R20-5).

### Owed on Windows

- The plans were explained on SQLite 3.45.1, 3.47.1 and 3.53.1 (Linux builds). The Windows CPython installers bundle their own SQLite; run `plans_ver.py` (and ideally `probe20.py`) under each Windows interpreter in the 3.10-3.13 range.
- All timings are from this Linux desktop under concurrent load from other agents; relative gaps (0.03 ms against 2.7 s) are the evidence, not the absolute numbers.

## Class 21. Wall-clock granularity as a test ordering assumption

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3. Scratch: `~/tt-data/mcuscope-2026-09-24/registry-leg/15-28/scratch-20-21/` (plugins in `plug/`, per-file logs in `q/`).

HEAD moved during the sweep (f31ecd9 to 1a1251a, other legs' commits). None touch store.py, server.py, cli.py or the webui; serial_link.py changed two comments; `host/tests/test_pidfile.py` gained a test at line 63, so its line numbers below are +19 at 1a1251a (clock sites 155, 156, 285, 286 are now 174, 175, 304, 305). Every `file:line` here is at f31ecd9.

Drive used throughout: `plug/quantclock.py`, a pytest plugin that makes `time.time` and `time.monotonic` tick at 15.625 ms, which is Windows CPython 3.10 (time.time also on 3.11-3.12).
Positive control: `ctrl/test_ctrl.py` asserts both clocks read a multiple of 15.625 ms; it passes with `-p quantclock` and fails without it.
36 test files were run one at a time under it (`runq.sh`, `q/summary.txt`); seven of them four more times (`q/repeat.txt`). The emulation reproduces tick granularity only, not a slow runner's scheduling.

### Findings

Listed at the top of this file.

### Sweeps, commands and counts

All commands run from `/home/daniel/git/mcuscope`.

- S1, captured clocks: `grep -nE "time\.(time|monotonic|perf_counter|time_ns|monotonic_ns)\(\)|_time\.(time|monotonic)\(\)|loop\.time\(\)" host/tests/*.py` returned **390** lines (64 files).
  265 fall to five mechanical shapes (poll deadline, clock passed as a row's ts, clock passed as a reader stamp, fake write returning the clock, prose); the other 125 were read in context. Every line is ruled below.
- S2, wall-clock thresholds (not in the registry; added): `grep -nE "assert .*(elapsed|monotonic\(\) *-|time\(\) *-|perf_counter\(\) *-|loop\.time\(\) *-|took|spent|\bdt\b|duration)" host/tests/*.py | grep -vE "time\.time\(\) - [0-9]+[^.]|ts=|\"ts\""` returned **26** lines.
- S3, boundaries taken from stored stamps (not in the registry; added, it is what found R21-1): `grep -nE "\[\"(ts|started_ts|ended_ts)\"\]" host/tests/*.py` returned **23** lines.
- S4, one-shot stimuli into shedding queues: `grep -nE "maxsize=|\.subscribe\(|RX_QUEUE_MAX|_WRITE_QUEUE_MAX|_Watch\(|Watch\(|queue_max|QUEUE_MAX" host/tests/*.py` returned **36** lines.
- S5, the registry's own: `grep -rn "assert .* not in " host/tests` returned **202** lines; 4 lines have a digits-and-punctuation needle, 4 a punctuation-only needle, 16 a non-literal needle, a container-membership test or a positive assertion the grep caught, and 178 an alphabetic needle.
- S6, plan-vocabulary absence (not in the registry; added, since the entry's `"SCAN l" not in` shape is written as `not any(...)` in this tree): `grep -nE "assert not any\(\s*[\"'][^\"']+[\"'] in " host/tests/*.py` returned **22** lines.
- S7, sub-tick sleeps: `grep -nE "sleep\(0?\.0(0[0-9]*|1[0-5]?[0-9]*)\)|sleep\(0\.01\)|sleep\(1e-" host/tests/*.py` returned **37** lines.
- S8, JS: `grep -rnE "Date\.now\(\)|performance\.now\(\)|new Date\(" host/tests/webui_js/` returned **6** lines; JS negative assertions `grep -rnE "assert\.ok\(\s*!|doesNotMatch|notEqual|notDeepEqual|\.includes\([^)]*\)\s*,\s*false|=== -1|indexOf\([^)]*\) === -1" host/tests/webui_js/*.mjs` returned **66**, of which 2 carry a numeric needle; JS thresholds `grep -rnE "assert[^;]*(elapsed|took|spent|Date\.now|performance\.now|< *[0-9]{3,})" host/tests/webui_js/*.mjs` returned **12**.

### Verdicts

#### S2 thresholds (26)

- `test_assert.py:219` complies: 0.3 < elapsed < 3.0 around a 0.4 s window.
- `test_assert.py:241` complies: lower bound 0.85 on a 0.9 s window, 50 ms over a tick.
- `test_assert.py:242` complies: < 4.0 against a 5 s timeout, healthy path 0.9 s.
- `test_assert.py:262` complies: < 2.0 against a 3 s window, healthy path about 0.15 s.
- `test_assert.py:496` exempt because it is a docstring the grep matched.
- `test_serial_link_rx_framing.py:203` exempt because it is a poll deadline.
- `test_session_bundle.py:460` exempt because it counts rows (the grep matched "took").
- `test_reconnect.py:109` complies: lower bound with 50 ms slop.
- `test_reconnect.py:110` violates (R21-6).
- `test_reconnect.py:120` complies: lower bound with 50 ms slop.
- `test_reconnect.py:130` complies: lower bound with 50 ms slop.
- `test_reconnect.py:146` violates (R21-6).
- `test_server_live_verdicts.py:434` exempt because it compares a reported latency with the value the test injected.
- `test_capture_lock.py:136` complies: lower bound 0.25 on a 0.3 s timer.
- `test_store_writer_commits.py:62` complies: the bound scales with the measured elapsed.
- `test_store_writer_commits.py:98` complies: the bound scales with the measured elapsed.
- `test_store_writer_commits.py:144` exempt because it is a queue size (the grep matched "took").
- `test_serial_link_devices.py:87` violates (R21-5).
- `test_e2e.py:544` violates (R21-6).
- `test_protocol.py:753` exempt because it is a message string the grep matched.
- `test_store_match_budget.py:135` complies: 5 s against a 0.25 s per-call timeout, the claim being the timeout.
- `test_store_match_budget.py:137` complies: a tick rate relative to the measured elapsed (3x margin at Windows 3.10 sleep granularity).
- `test_store_match_budget.py:161` complies: 10 s against a 0.25 s timeout.
- `test_store_match_budget.py:180` complies: 5 s against a 0.25 s timeout.
- `test_store_match_budget.py:185` complies: 5 s against a 0.25 s timeout.
- `test_wait_repeat.py:332` violates (R21-3).

(`test_wait_repeat.py:369`, `max(gaps) < 0.2`, is R21-4 and was missed by this grep: the improved S2 below catches it.)

#### S3 stored-stamp boundaries (23)

- `test_assert.py:130` violates (R21-2): the spin from this stamp is right; the later window is the defect.
- `test_assert.py:322` complies: the cut is spun strictly past the newest stored stamp (`_after`), and again past the cut.
- `test_assert.py:430` exempt because it checks `ended_ts is None`.
- `test_flow_cli_windows.py:178` complies: the row is stamped a full second before the session.
- `test_flow_cli_windows.py:246` complies: expected ids computed from the stored stamps with the same inclusive rule.
- `test_cli.py:1020` exempt because it checks `ended_ts is None`.
- `test_e2e.py:609` violates (R21-1).
- `test_e2e.py:612` violates (R21-1), the same test.
- `test_serial_link_tx.py:109` complies: non-strict on one clock, 0.3 s lock hold.
- `test_serial_link_tx.py:122` complies: same shape as :109.
- `test_plotjuggler.py:120` exempt because it compares a ts the test fed in.
- `test_plotjuggler.py:177` exempt because it compares a ts the test fed in.
- `test_plotjuggler.py:204` exempt because it compares a ts the test fed in.
- `test_plotjuggler.py:406` exempt because it checks the type.
- `test_reconnect.py:598` complies: asserts one burst shares one stamp (design, not ordering).
- `test_server_export_windows.py:152` complies: bounds 1 s outside the session's stamps.
- `test_sessions.py:75` exempt because it checks `ended_ts is not None`.
- `test_sessions.py:110` exempt because it checks `ended_ts is None`.
- `test_sessions.py:228` exempt because it checks `ended_ts is None`.
- `test_sessions.py:881` exempt because it checks `ended_ts is not None`.
- `test_store_time_window.py:32` complies: the oracle applies the same predicate to stored stamps.
- `test_store_session_bounds.py:117` complies: equality between two stored values.
- `test_store_session_bounds.py:150` exempt because it checks `ended_ts is None`.

#### S4 shedding queues (36)

- `test_serial_link_rx_framing.py:11` exempt because it is an import.
- `test_serial_link_rx_framing.py:231` complies: no consumer runs; the queue is filled synchronously and inspected.
- `test_serial_link_rx_framing.py:233` complies: same test.
- `test_serial_link_rx_framing.py:236` complies: same test.
- `test_server_exports.py:23` exempt because it is an import of admission constants, not a shedding queue.
- `test_server_exports.py:90` exempt because export admission refuses, it does not shed.
- `test_server_export_pool.py:18` exempt because it is an import.
- `test_server_export_pool.py:24` exempt because export admission refuses, it does not shed.
- `test_server_export_pool.py:272` exempt because export admission refuses, it does not shed.
- `test_server_export_pool.py:311` exempt because export admission refuses, it does not shed.
- `test_sessions.py:130` exempt because it is a comment.
- `test_store_fastpaths.py:277` exempt because the write queue blocks, it does not shed.
- `test_store_fastpaths.py:313` complies: the test is the only consumer and reads after the writes land.
- `test_store_fastpaths.py:314` complies: same test.
- `test_store_fastpaths.py:315` complies: same test.
- `test_store_fastpaths.py:338` complies: deterministic, read after all five commits.
- `test_server_shutdown.py:58` complies: deterministic, the test fills and reads the queue itself.
- `test_server_shutdown.py:76` exempt because it checks the closed-subscriber refusal.
- `test_server_shutdown.py:81` exempt because it checks the closed-subscriber refusal.
- `test_server_shutdown.py:241` complies: deterministic.
- `test_assert.py:481` complies: default 2000-row watch, rows written after `open`, drained by the test.
- `test_assert.py:510` complies: same shape.
- `test_assert.py:537` complies: same shape.
- `test_assert.py:561` complies: deterministic overflow of a 2-row watch.
- `test_assert.py:588` complies: subscription count only.
- `test_assert.py:722` complies: a 0.3 s one-shot flood, but driven with the handler reaching its subscription 0.4 s late (`plug/slowsub.py`) it still passes: the sim's own bursts overflow the 4-row queue, so the assertion does not depend on the flood (see the two questions).
- `test_assert.py:753` complies: the needle is re-armed until the wait answers.
- `test_store_subscribers.py:20` complies: deterministic.
- `test_store_subscribers.py:21` complies: deterministic.
- `test_store_subscribers.py:34` exempt because it checks the subscriber cap.
- `test_store_subscribers.py:36` exempt because it checks the subscriber cap.
- `test_store_subscribers.py:39` exempt because it checks the subscriber cap.
- `test_store_subscribers.py:58` complies: deterministic.
- `test_reconnect.py:1031` exempt because it is a docstring.
- `test_reconnect.py:1176` complies: both bursts are synchronous `_on_bytes` calls, so the overflow cannot race the consumer.
- `test_server_live_verdicts.py:351` complies: the same timed flood as `test_assert.py:722`, driven the same way, still passes for the same reason.

#### S6 plan-vocabulary absence (22)

- `test_break.py:69` exempt because it is row text, not plan text.
- `test_break.py:109` exempt because it is row text.
- `test_break.py:127` exempt because it is row text.
- `test_config_loader.py:141` exempt because it is a warning text.
- `test_config_loader.py:423` exempt because it is a status text.
- `test_sim.py:882` exempt because it is sim output.
- `test_session_bundle.py:176` exempt because it is file text.
- `test_store_time_window.py:175` complies: `"SCAN lines"` absence has its positive control (`test_store_lines_plan.py:240` asserts this build spells a scan that way) and sits beside two positive assertions.
- `test_store_lines_plan.py:91` complies: `TEMP B-TREE` is covered by the same positive control.
- `test_store_lines_plan.py:161` exempt because it is log text.
- `test_store_lines_plan.py:184` exempt because it is log text.
- `test_store_lines_plan.py:215` complies: positive control plus a positive assertion.
- `test_store_lines_plan.py:289` complies: same.
- `test_store_lines_plan.py:324` complies: `BLOOM` has no control, but a SQLite without bloom filters also lacks the regression it guards, and the positive `SEARCH li ... PRIMARY KEY` assertion carries the claim.
- `test_store_lines_plan.py:363` complies: control plus positive assertion.
- `test_store_lines_plan.py:496` complies: control plus positive assertion.
- `test_store_lines_plan.py:583` complies: control plus positive assertion.
- `test_store_can_frames_port.py:40` complies: control plus positive assertion.
- `test_store_sessions.py:131` complies: control plus positive assertion.
- `test_eol.py:189` exempt because it is row text.
- `test_store_plot_summary.py:102` exempt because it matches statement text the test traced.
- `test_store_plot_reads.py:134` complies: control plus positive assertion.

#### S7 sub-tick sleeps (37)

- `test_assert.py:31` exempt because it is a docstring.
- `test_assert.py:40` complies: the yield inside a spin that tests the clock (`_after`).
- `test_assert.py:135` complies: the yield inside a spin that tests the clock.
- `support.py:466` exempt because it is a poll-loop yield under a deadline.
- `test_reconnect.py:583` exempt because it is a poll-loop yield.
- `test_reconnect.py:659` exempt because it is a poll-loop yield.
- `test_reconnect.py:677` exempt because it is a poll-loop yield.
- `test_reconnect.py:888` exempt because it is a poll-loop yield.
- `test_reconnect.py:1018` exempt because it is a poll-loop yield.
- `test_reconnect.py:1090` exempt because it is a poll-loop yield.
- `test_reconnect.py:1190` exempt because it is a poll-loop yield.
- `test_reconnect.py:1335` exempt because it is a poll-loop yield.
- `test_reconnect.py:1390` exempt because it is a poll-loop yield.
- `test_reconnect.py:1413` exempt because it is a poll-loop yield.
- `test_port_health.py:349` exempt because it is a poll-loop yield.
- `test_port_health.py:361` exempt because it is a poll-loop yield.
- `test_serial_link_tx.py:307` exempt because it is a poll-loop yield.
- `test_serial_link_tx.py:365` exempt because it is a poll-loop yield.
- `test_serial_link_tx.py:398` complies: a ticker whose count (not its timing) is asserted, one tick needed in 100 ms.
- `test_e2e.py:534` exempt because it is a poll-loop yield.
- `test_serial_link_rx_framing.py:160` exempt because it is a poll-loop yield.
- `test_serial_link_rx_framing.py:204` exempt because it is a poll-loop yield.
- `test_cli.py:1405` exempt because it is a fake websocket's poll yield.
- `test_session_bundle.py:435` exempt because it is a poll-loop yield.
- `test_session_bundle.py:482` exempt because it is a poll-loop yield.
- `test_serial_link_attach.py:167` exempt because it is a poll-loop yield.
- `test_serial_link_attach.py:546` exempt because it is a poll-loop yield.
- `test_sim.py:1030` exempt because it is a poll-loop yield.
- `test_store_fastpaths.py:102` exempt because it waits on an Event.
- `test_server_shutdown.py:44` exempt because it is a poll-loop yield.
- `test_server_shutdown.py:171` exempt because it is a poll-loop yield.
- `test_server_shutdown.py:274` exempt because it is a poll-loop yield.
- `test_server_shutdown.py:297` exempt because it is a poll-loop yield.
- `test_server_shutdown.py:323` exempt because it is a poll-loop yield.
- `test_server_shutdown.py:339` exempt because it is a poll-loop yield.
- `test_server_shutdown.py:351` exempt because it is a poll-loop yield.
- `test_store_match_budget.py:123` complies: a heartbeat whose tick count is judged against the measured elapsed.

#### S8 JS

- `plots_seed_paused.test.mjs:18` exempt because it is a fixture's `last_ts`.
- `plots_seed_paused.test.mjs:20` exempt because it is a fixture's `last_ts`.
- `terminal_logic.test.mjs:123` exempt because it formats a fixed instant.
- `can_logic.test.mjs:240` complies: a daemon 30 s ahead; the assertion is on the sign of the age, not its size.
- `can_logic.test.mjs:252` exempt because it seeds an hour-ahead capture before the reset.
- `can_logic.test.mjs:254` complies: `age < 1000` ms (`:257`) for synchronous in-process work whose failure value is 3.6e6 ms.
- `can_age_tick.test.mjs:44` complies: fixed ts, a positive control follows (`renderCan()` then writes 777).
- `statusbar_logic.test.mjs:117` complies: a fixed uptime, no clock value in the title.
- The other 11 JS threshold hits compare armed timer constants or fixed data (`exportrange_shown_params:12`, `exportdlg_pending_close:132`, `settings_save_reread:223`, `statusbar_attach_open:86`, `plots_decimate:51`, `terminal_logic:151`) or matched prose (`digital_repaint:56`, `pane_history_plan:21`, `plots_hover_tick:61`, `settings_export_hold:283/319`): exempt because none measures elapsed time.
- The other 64 JS negative assertions carry alphabetic or structural needles: exempt because no clock rendering contains them.

#### S1 clock sites (390)

- `support.py:271` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `support.py:272` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `support.py:286` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `support.py:287` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `support.py:430` exempt because it passes the clock as a row's ts
- `support.py:463` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `support.py:465` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_assert.py:24` exempt because it passes the clock as a row's ts
- `test_assert.py:30` exempt because it is prose (comment or docstring)
- `test_assert.py:37` complies: `_after` spins until the clock reads strictly past the bound
- `test_assert.py:133` exempt because it is prose (comment or docstring)
- `test_assert.py:134` violates (R21-2): spins past the old line correctly, but the 30 ms window must then also hold the new line through two more round trips
- `test_assert.py:214` see :219
- `test_assert.py:219` complies: 0.3 < elapsed < 3.0 around a 0.4 s window; 100 ms under, 2.6 s over
- `test_assert.py:232` see :241-242
- `test_assert.py:238` complies: >= 0.85 on a 0.9 s minimum window (50 ms over a tick), < 4.0 against a 5 s timeout
- `test_assert.py:254` see :262
- `test_assert.py:259` complies: < 2.0 against a 3 s window; the healthy path is 0.15 s
- `test_assert.py:319` exempt because it is prose (comment or docstring)
- `test_assert.py:459` exempt because it passes the clock as a row's ts
- `test_assert.py:466` exempt because it passes the clock as a row's ts
- `test_capture_lock.py:132` complies: start of a lower-bound measurement (see :136)
- `test_capture_lock.py:136` complies: lower bound 0.25 s on a 0.3 s timer; 50 ms margin exceeds a 15.6 ms tick, and load only lengthens it
- `test_cli.py:423` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_cli.py:841` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_cli.py:842` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_cli.py:896` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_cli.py:897` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_cli.py:1322` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_cli.py:1323` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_cli.py:1644` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_cli.py:1645` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_cli.py:1668` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_cli.py:1669` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_cli_can_dump.py:51` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart) (a canned daemon's clock)
- `test_cli_daemon_stop_scope.py:201` exempt because it scripts a fake daemon's lifetime; the assertion is the exit code, with the CLI's own wait far longer than 0.4 s
- `test_cli_daemon_stop_scope.py:204` exempt: same fake-daemon lifetime (0.4 s)
- `test_cli_export.py:45` complies: stamps recorded as stored, windows derived from them
- `test_cli_export.py:100` complies: a window 10-20 s back over rows the match excludes anyway
- `test_cli_export.py:101` complies: same window as :100
- `test_cli_ux.py:341` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_cli_ux.py:342` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_config_api.py:323` exempt because it sets checker state to now so no real check starts; no comparison
- `test_daemon_process.py:117` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_daemon_process.py:120` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_decode_per_port.py:38` exempt because it passes the clock as the reader's stamp
- `test_decode_per_port.py:47` exempt because it passes the clock as a row's ts
- `test_e2e.py:28` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_e2e.py:29` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_e2e.py:307` exempt because it passes the clock as the reader's stamp
- `test_e2e.py:530` exempt because a fake write returns the clock as its stamp
- `test_e2e.py:536` exempt because it passes the clock as the reader's stamp
- `test_e2e.py:539` see :544
- `test_e2e.py:544` violates (R21-6): promptness threshold 1.0 s against a 2 s timeout, about 1 s of headroom
- `test_export_lines_can.py:77` exempt because it passes the clock as a row's ts
- `test_export_lines_can.py:79` complies: the boundary is taken, then the clock is spun past it (:81)
- `test_export_lines_can.py:81` complies: spins until the clock reads strictly past the cut
- `test_export_lines_can.py:83` exempt because it passes the clock as a row's ts
- `test_export_lines_can.py:85` exempt because it passes the clock as a row's ts
- `test_export_lines_can.py:193` exempt because it passes the clock as a row's ts
- `test_flow_cli_windows.py:35` exempt because it passes the clock as a row's ts
- `test_flow_cli_windows.py:77` exempt because it passes the clock as a row's ts (early and late rows 150 ms apart, a 100 ms window: 50 ms margin over a tick)
- `test_flow_cli_windows.py:78` exempt because it passes the clock as a row's ts
- `test_flow_cli_windows.py:79` exempt because it passes the clock as a row's ts
- `test_flow_cli_windows.py:173` exempt because it is prose (comment or docstring)
- `test_flow_cli_windows.py:175` complies: stamped a whole second before the session starts (the comment names class 21)
- `test_flow_cli_windows.py:223` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart)
- `test_flow_cli_windows.py:236` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart)
- `test_flow_cli_windows.py:252` exempt because it passes the clock as a row's ts
- `test_flow_cli_windows.py:254` complies: a bound 5000 s before every row
- `test_flow_cli_windows.py:265` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart)
- `test_pidfile.py:155` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_pidfile.py:156` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_pidfile.py:285` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_pidfile.py:286` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_plot.py:24` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_plot.py:25` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_plot.py:130` exempt because it passes the clock as the reader's stamp
- `test_plot.py:231` exempt because it passes the clock as a row's ts (a parked filler row)
- `test_plot.py:418` exempt because it passes the clock as the reader's stamp
- `test_plot_export_decode.py:29` exempt because it passes the clock as the reader's stamp
- `test_plot_export_decode.py:185` exempt because it passes the clock as the reader's stamp
- `test_plot_export_decode.py:318` exempt because it passes the clock as a row's ts
- `test_plot_export_refusals.py:27` exempt because it passes the clock as the reader's stamp
- `test_plotjuggler.py:413` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_plotjuggler.py:414` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_port_column_stored.py:35` exempt because it passes the clock as a row's ts
- `test_port_column_stored.py:48` exempt because it passes the clock as a row's ts
- `test_port_health.py:39` complies: non-strict <=
- `test_port_health.py:61` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_port_health.py:63` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_port_health.py:90` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart) (an hour back, canned body)
- `test_port_health.py:92` exempt because it fills a canned /status body
- `test_port_health.py:157` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart) (5 s and 4 days back against --active 1 and 60)
- `test_port_health.py:254` complies: abs(... - (before - 60)) < 5
- `test_port_health.py:308` exempt because a fake write returns the clock as its stamp
- `test_port_health.py:343` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_port_health.py:345` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_port_health.py:359` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_port_health.py:360` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_port_health.py:564` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_port_health.py:565` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:104` see :109-110
- `test_reconnect.py:106` complies at :109 (lower bound, 50 ms slop over a 15.6 ms tick); violates at :110 (R21-6, 1.5 s budget over a ~0.55 s healthy path)
- `test_reconnect.py:117` see :120
- `test_reconnect.py:119` complies: lower bound with 50 ms slop (:120)
- `test_reconnect.py:127` see :130
- `test_reconnect.py:129` complies: lower bound with 50 ms slop (:130)
- `test_reconnect.py:140` see :146
- `test_reconnect.py:142` violates (R21-6): feeds `elapsed < 1.0` at :146 against a 30 s interval
- `test_reconnect.py:291` exempt because it passes the clock as the reader's stamp
- `test_reconnect.py:357` exempt because it passes the clock as the reader's stamp
- `test_reconnect.py:579` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:580` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:657` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:658` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:675` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:676` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:886` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:887` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:1016` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:1017` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:1052` exempt because it passes the clock as the reader's stamp
- `test_reconnect.py:1056` exempt because it passes the clock as the reader's stamp
- `test_reconnect.py:1088` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:1089` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:1123` exempt because it seeds a pending command's send stamp
- `test_reconnect.py:1126` exempt because it passes the clock as the reader's stamp
- `test_reconnect.py:1183` exempt because it passes the clock as the reader's stamp
- `test_reconnect.py:1188` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:1189` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:1193` exempt because it passes the clock as the reader's stamp
- `test_reconnect.py:1308` exempt because a fake write returns the clock as its stamp
- `test_reconnect.py:1324` exempt because a fake write returns the clock as its stamp
- `test_reconnect.py:1333` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:1334` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:1388` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:1389` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:1411` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_reconnect.py:1412` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_serial_link_attach.py:164` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_serial_link_attach.py:166` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_serial_link_attach.py:318` exempt because it passes the clock as the reader's stamp
- `test_serial_link_devices.py:80` see :87
- `test_serial_link_devices.py:87` violates (R21-5): 1.2 s budget on one HTTP round trip, which the in-repo comment records at 0.45 s on Windows
- `test_serial_link_rx_framing.py:30` exempt because it passes the clock as the reader's stamp
- `test_serial_link_rx_framing.py:59` exempt because it passes the clock as the reader's stamp
- `test_serial_link_rx_framing.py:60` exempt because it passes the clock as the reader's stamp
- `test_serial_link_rx_framing.py:61` exempt because it passes the clock as the reader's stamp
- `test_serial_link_rx_framing.py:91` exempt because it passes the clock as the reader's stamp
- `test_serial_link_rx_framing.py:94` exempt because it passes the clock as the reader's stamp
- `test_serial_link_rx_framing.py:104` exempt because it passes the clock as the reader's stamp
- `test_serial_link_rx_framing.py:114` exempt because it passes the clock as the reader's stamp
- `test_serial_link_rx_framing.py:119` exempt because it passes the clock as the reader's stamp
- `test_serial_link_rx_framing.py:157` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_serial_link_rx_framing.py:159` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_serial_link_rx_framing.py:201` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_serial_link_rx_framing.py:203` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_serial_link_rx_framing.py:215` exempt because it passes the clock as the reader's stamp
- `test_serial_link_rx_framing.py:216` exempt because it passes the clock as the reader's stamp
- `test_serial_link_rx_framing.py:217` exempt because it passes the clock as the reader's stamp
- `test_serial_link_rx_framing.py:232` exempt because it passes the clock as the reader's stamp
- `test_serial_link_rx_tokens.py:31` exempt because it passes the clock as the reader's stamp
- `test_serial_link_rx_tokens.py:32` exempt because it passes the clock as the reader's stamp
- `test_serial_link_rx_tokens.py:33` exempt because it passes the clock as the reader's stamp
- `test_serial_link_tx.py:43` complies: a stamp compared non-strictly (<=) with the row's ts on the same clock
- `test_serial_link_tx.py:46` complies: same as :43
- `test_serial_link_tx.py:93` complies: lock release stamp, compared non-strictly; the lock is held 0.3 s so a wrong stamp is 300 ms off
- `test_serial_link_tx.py:106` complies: latency_ms <= done - released, both on time.time(), and the latency interval lies inside that one, so quantisation cannot invert it
- `test_serial_link_tx.py:143` exempt because it seeds a pending command's send stamp; only the disconnect path is asserted
- `test_serial_link_tx.py:298` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_serial_link_tx.py:299` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_serial_link_tx.py:305` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_serial_link_tx.py:306` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_serial_link_tx.py:352` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_serial_link_tx.py:353` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_serial_link_tx.py:359` exempt because a fake write returns the clock as its stamp
- `test_serial_link_tx.py:363` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_serial_link_tx.py:364` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_serial_link_tx.py:404` exempt because a fake write returns the clock as its stamp
- `test_serial_link_tx.py:509` exempt because a fake write returns the clock as its stamp
- `test_server_export_windows.py:90` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart) (an hour back)
- `test_server_export_windows.py:95` complies: inclusive bounds with 1 s slack at second resolution (:108)
- `test_server_export_windows.py:108` complies: inclusive, second-resolution file-name stamps
- `test_server_export_windows.py:123` exempt because it passes the clock as a row's ts
- `test_server_export_windows.py:137` exempt because it passes the clock as a row's ts
- `test_server_export_windows.py:138` exempt because it passes the clock as a row's ts
- `test_server_export_windows.py:153` complies: a bound 120 s before the session
- `test_server_export_windows.py:310` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart) (ids 1 s apart; the 3.5 s window keeps the floor off a row, comment names class 21)
- `test_server_exports.py:237` exempt because it passes the clock as a row's ts
- `test_server_exports.py:421` exempt because it passes the clock as a row's ts
- `test_server_exports.py:510` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_exports.py:511` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_guards.py:161` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_guards.py:162` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_guards.py:297` exempt because it injects one instant into the token guard; no clock comparison
- `test_server_live_verdicts.py:42` exempt because it passes the clock as a row's ts
- `test_server_live_verdicts.py:188` exempt because it passes the clock as a row's ts
- `test_server_live_verdicts.py:211` exempt because it passes the clock as a row's ts
- `test_server_live_verdicts.py:268` exempt because it passes the clock as a row's ts
- `test_server_live_verdicts.py:464` exempt because it passes the clock as a row's ts
- `test_server_offloop.py:40` exempt because it passes the clock as a row's ts
- `test_server_offloop.py:74` exempt because it passes the clock as a row's ts
- `test_server_offloop.py:113` exempt because it passes the clock as a row's ts
- `test_server_offloop.py:133` exempt because it passes the clock as a row's ts
- `test_server_purge_and_sessions.py:42` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart)
- `test_server_purge_and_sessions.py:63` exempt because it passes the clock as a row's ts
- `test_server_purge_and_sessions.py:93` complies: an hour in the future against the refusal
- `test_server_purge_and_sessions.py:104` complies: 5 s ahead, inside the 60 s slack by 55 s
- `test_server_purge_and_sessions.py:125` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart)
- `test_server_purge_and_sessions.py:151` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart)
- `test_server_request_validation.py:260` exempt because it passes the clock as a row's ts
- `test_server_scope.py:40` exempt because it is a fake clock that steps 10 s per window read
- `test_server_scope.py:53` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart) (5 s apart, fake clock)
- `test_server_scope.py:73` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart) (1.5 s before a 2 s window, fake clock)
- `test_server_shutdown.py:41` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_shutdown.py:43` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_shutdown.py:168` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_shutdown.py:170` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_shutdown.py:271` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_shutdown.py:273` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_shutdown.py:296` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_shutdown.py:320` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_shutdown.py:322` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_shutdown.py:336` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_shutdown.py:338` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_shutdown.py:350` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_status.py:75` complies: non-strict on both sides (:77)
- `test_server_status.py:77` complies: before <= now <= time.time()
- `test_server_status.py:88` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_status.py:89` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_ws.py:24` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_server_ws.py:25` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_session_bundle.py:37` exempt because it passes the clock as the reader's stamp
- `test_session_bundle.py:78` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_session_bundle.py:79` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_session_bundle.py:433` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_session_bundle.py:434` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_session_bundle.py:479` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_session_bundle.py:481` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_session_bundle.py:522` exempt because it passes the clock as a row's ts
- `test_sessions.py:30` exempt because it passes the clock as a row's ts
- `test_sessions.py:142` exempt because it passes the clock as a row's ts
- `test_sessions.py:453` exempt because it passes the clock as a row's ts
- `test_sessions.py:473` exempt because it passes the clock as a row's ts
- `test_sessions.py:548` exempt because it passes the clock as a row's ts
- `test_sessions.py:819` exempt because it passes the clock as a row's ts
- `test_sessions.py:840` exempt because it passes the clock as a row's ts
- `test_sessions.py:846` exempt because it passes the clock as a row's ts
- `test_sessions.py:849` exempt because it passes the clock as a row's ts
- `test_sessions.py:869` exempt because it passes the clock as a row's ts
- `test_sim.py:243` exempt because it injects the instant the sim's schedule is due at
- `test_sim.py:310` exempt because it pushes the schedule 60 s out
- `test_sim.py:311` exempt because it pushes the schedule 60 s out
- `test_sim.py:344` exempt because it drives the sim with explicit instants 50 ms apart
- `test_sim.py:402` complies: next_marker = now then poll_events reads the clock again; passes under the 15.625 ms emulation (due is >=)
- `test_sim.py:431` exempt because it back-dates the schedule an hour
- `test_sim.py:460` exempt because it passes an explicit instant 10 s out
- `test_sim.py:529` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim.py:530` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim.py:531` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim.py:657` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim.py:658` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim.py:666` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim.py:668` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim.py:829` exempt because it sets the sim's start to explicit elapsed values
- `test_sim.py:866` exempt because it back-dates the sim's start 60 s
- `test_sim.py:875` exempt because it back-dates the sim's start in 0.25 s steps
- `test_sim.py:891` exempt because it passes the clock as the poll instant; no comparison
- `test_sim.py:1026` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim.py:1028` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim_pty.py:31` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim_pty.py:32` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim_pty.py:33` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim_pty.py:65` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim_pty.py:66` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim_pty.py:182` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim_pty.py:184` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim_tcp.py:39` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim_tcp.py:40` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim_tcp.py:193` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim_tcp.py:195` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim_tcp.py:242` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_sim_tcp.py:244` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_store_can_frames_port.py:25` exempt because it passes the clock as a row's ts
- `test_store_can_frames_port.py:54` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart) (an hour apart)
- `test_store_export.py:32` exempt because it passes the clock as a row's ts
- `test_store_export.py:55` exempt because it passes the clock as a row's ts
- `test_store_fastpaths.py:28` exempt because it passes the clock as a row's ts
- `test_store_fastpaths.py:141` exempt because it passes the clock as a row's ts
- `test_store_fastpaths.py:281` exempt because it passes the clock as a row's ts
- `test_store_fastpaths.py:301` exempt because it passes the clock as a row's ts
- `test_store_fastpaths.py:446` exempt because it passes the clock as the reader's stamp
- `test_store_id_sequence.py:23` exempt because it passes the clock as a row's ts
- `test_store_lines_plan.py:40` exempt because it passes the clock as a row's ts
- `test_store_lines_plan.py:42` exempt because it passes the clock as a row's ts
- `test_store_lines_plan.py:43` exempt because it passes the clock as a row's ts
- `test_store_lines_plan.py:56` complies: a plan-only bound 60 s back
- `test_store_lines_plan.py:104` exempt because it passes the clock as a row's ts
- `test_store_lines_plan.py:105` exempt because it passes the clock as a row's ts
- `test_store_lines_plan.py:124` exempt because it passes the clock as a row's ts
- `test_store_lines_plan.py:125` exempt because it passes the clock as a row's ts
- `test_store_lines_plan.py:126` exempt because it passes the clock as a row's ts
- `test_store_lines_plan.py:162` exempt because it passes the clock as a row's ts
- `test_store_lines_plan.py:299` exempt because it passes the clock as a row's ts
- `test_store_lines_plan.py:316` exempt because it passes the clock as a row's ts
- `test_store_lines_plan.py:353` exempt because it passes the clock as a row's ts
- `test_store_lines_plan.py:411` exempt because it passes the clock as a row's ts
- `test_store_lines_plan.py:436` complies: rows were shifted 999,999 s back first
- `test_store_lines_plan.py:459` exempt because it passes the clock as a row's ts
- `test_store_lines_plan.py:470` complies: used for plan assertions only, no row membership
- `test_store_lines_plan.py:531` complies: boundary taken from the data, three rows share it exactly (comment names class 21)
- `test_store_lines_plan.py:572` complies: a day back, plan assertion
- `test_store_match_budget.py:31` exempt because it passes the clock as a row's ts
- `test_store_match_budget.py:127` see :135
- `test_store_match_budget.py:129` see :135
- `test_store_match_budget.py:154` exempt because it passes the clock as a row's ts
- `test_store_match_budget.py:155` see :161
- `test_store_match_budget.py:157` see :161
- `test_store_match_budget.py:177` see :180
- `test_store_match_budget.py:180` complies: 5 s against a 0.25 s per-call timeout
- `test_store_match_budget.py:182` see :185
- `test_store_match_budget.py:185` complies: 5 s against a 0.25 s per-call timeout
- `test_store_plot_reads.py:26` exempt because it passes the clock as a row's ts
- `test_store_plot_reads.py:87` exempt because it passes the clock as a row's ts
- `test_store_plot_reads.py:106` exempt because it passes the clock as a row's ts
- `test_store_plot_reads.py:165` exempt because it passes the clock as a row's ts
- `test_store_plot_summary.py:26` exempt because it passes the clock as a row's ts
- `test_store_plot_summary.py:45` exempt because it passes the clock as a row's ts
- `test_store_plot_summary.py:115` complies: an hour before rows stamped now
- `test_store_plot_summary.py:227` exempt because it passes the clock as a row's ts
- `test_store_plot_summary.py:282` exempt because it passes the clock as a row's ts
- `test_store_plot_summary.py:308` exempt because it passes the clock as a row's ts
- `test_store_reclaim_budget.py:23` exempt because it passes the clock as a row's ts
- `test_store_reclaim_budget.py:182` exempt because it passes the clock as a row's ts
- `test_store_schema.py:128` exempt because it passes the clock as a row's ts
- `test_store_schema.py:143` exempt because it passes the clock as a row's ts
- `test_store_session_bounds.py:20` exempt because it passes the clock as a row's ts
- `test_store_session_bounds.py:100` exempt because it passes the clock as a row's ts
- `test_store_session_bounds.py:119` exempt because it passes the clock as a row's ts
- `test_store_size_cap.py:18` exempt because it passes the clock as a row's ts
- `test_store_size_cap.py:230` exempt because it passes the clock as a row's ts
- `test_store_stamp_order.py:20` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart) (10.5 s, 11 s offsets)
- `test_store_stamp_order.py:40` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart) (60 s offset)
- `test_store_stamp_order.py:57` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart) (9.5 s offset)
- `test_store_stamp_order.py:77` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart)
- `test_store_stamp_order.py:88` exempt because it passes the clock as a row's ts
- `test_store_stamp_order.py:92` exempt because it passes the clock as a row's ts (30 s back)
- `test_store_stamp_order.py:119` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart)
- `test_store_startup_trim.py:20` exempt because it passes the clock as a row's ts
- `test_store_time_window.py:19` exempt because it anchors synthetic stamps whose spacing is fixed in the data (seconds apart)
- `test_store_time_window.py:242` complies: cutoff 30 s ahead of every row
- `test_store_time_window.py:244` exempt because it passes the clock as a row's ts
- `test_store_time_window.py:247` exempt because it passes the clock as a row's ts
- `test_store_writer.py:23` exempt because it passes the clock as a row's ts
- `test_store_writer.py:105` exempt because it passes the clock as a row's ts
- `test_store_writer.py:124` exempt because it passes the clock as a row's ts
- `test_store_writer.py:143` exempt because it passes the clock as a row's ts
- `test_store_writer.py:146` exempt because it passes the clock as a row's ts
- `test_store_writer.py:149` exempt because it passes the clock as a row's ts
- `test_store_writer.py:176` exempt because it passes the clock as a row's ts
- `test_store_writer.py:215` exempt because it passes the clock as a row's ts
- `test_store_writer.py:336` exempt because it passes the clock as a row's ts
- `test_store_writer.py:345` exempt because it passes the clock as a row's ts
- `test_store_writer.py:372` exempt because it passes the clock as a row's ts
- `test_store_writer_commits.py:34` complies: the commit bound scales with the measured elapsed (:62)
- `test_store_writer_commits.py:40` complies: same
- `test_store_writer_commits.py:44` exempt because it passes the clock as a row's ts
- `test_store_writer_commits.py:93` complies: the hold bound scales with the measured elapsed (:98)
- `test_store_writer_commits.py:95` exempt because it passes the clock as a row's ts
- `test_store_writer_commits.py:97` complies: same
- `test_store_writer_commits.py:116` exempt because it passes the clock as a row's ts
- `test_store_writer_commits.py:123` exempt because it passes the clock as a row's ts
- `test_store_writer_commits.py:141` exempt because it passes the clock as a row's ts
- `test_timeline.py:44` complies: stamps 50 ms apart, boundaries are midpoints of the stored stamps
- `test_timeline.py:111` complies: an hour ahead
- `test_update_check.py:104` complies: approx within 30 s
- `test_update_check.py:125` exempt because it writes a cache stamp; the assertion is on the parsed version
- `test_update_check.py:328` exempt because it is prose (comment or docstring)
- `test_update_check.py:344` exempt because it writes a future stamp ten intervals out
- `test_update_check.py:349` complies: non-strict <=
- `test_update_check.py:381` complies: approx within 1 s
- `test_wait_repeat.py:326` see :332
- `test_wait_repeat.py:328` violates (R21-3): feeds `elapsed < 0.1` at :332
- `test_wait_repeat.py:350` violates (R21-4): feeds `max(gaps) < 0.2` at :369
- `test_webui.py:110` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_webui.py:111` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_webui.py:123` exempt because it passes the clock as the reader's stamp
- `test_webui.py:147` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock
- `test_webui.py:149` exempt because it is a poll deadline: a timeout on waiting for a condition, not a claim about the clock

#### S5 `assert .* not in` sites (202)

- `test_serial_link_rx_framing.py:239` exempt because the needle (`line 0`) has letters, which no ts, tick or clock rendering contains
- `test_server_shutdown.py:98` exempt because the needle (`too many subscribers`) has letters, which no ts, tick or clock rendering contains
- `test_port_health.py:101` exempt because the needle (`connected`) has letters, which no ts, tick or clock rendering contains
- `test_port_health.py:106` exempt because the needle (`target=`, `DEGRADED`) has letters, which no ts, tick or clock rendering contains
- `test_port_health.py:169` exempt because the needle (`present`) has letters, which no ts, tick or clock rendering contains
- `test_port_health.py:225` exempt because the needle (`id_to`) has letters, which no ts, tick or clock rendering contains
- `test_port_health.py:258` exempt because it is key membership in recorded request params
- `test_port_health.py:283` exempt because the needle (`running)`) has letters, which no ts, tick or clock rendering contains
- `test_port_health.py:543` exempt because the needle (`(manual)`) has letters, which no ts, tick or clock rendering contains
- `test_port_health.py:550` exempt because the needle (`None`) has letters, which no ts, tick or clock rendering contains
- `test_cli_can_dump.py:96` exempt because it is key membership in recorded request params
- `test_cli_can_dump.py:107` exempt because it is key membership in recorded request params
- `test_cli_can_dump.py:116` exempt because it is key membership in recorded request params
- `test_cli_can_dump.py:178` exempt because the needle (`unreachable`) has letters, which no ts, tick or clock rendering contains
- `test_cli_can_dump.py:239` exempt because the needle (`truncated`) has letters, which no ts, tick or clock rendering contains
- `test_store_lines_plan.py:147` exempt because the needle (`INDEX`) has letters, which no ts, tick or clock rendering contains
- `test_cli_ux.py:46` exempt because the needle (`daemon start`) has letters, which no ts, tick or clock rendering contains
- `test_cli_ux.py:53` exempt because the needle (`daemon start`) has letters, which no ts, tick or clock rendering contains
- `test_cli_ux.py:115` exempt because the needle (`line-01\n`) has letters, which no ts, tick or clock rendering contains
- `test_cli_ux.py:116` exempt because the needle (`OLD JUNK`) has letters, which no ts, tick or clock rendering contains
- `test_cli_ux.py:128` exempt because the needle (`last`, `.err`) has letters, which no ts, tick or clock rendering contains
- `test_cli_ux.py:221` exempt because the needle (`--sim`) has letters, which no ts, tick or clock rendering contains
- `test_cli_ux.py:261` exempt because the needle (`one of:`) has letters, which no ts, tick or clock rendering contains
- `test_cli_ux.py:357` exempt because the needle (`no ports`) has letters, which no ts, tick or clock rendering contains
- `test_server_offloop.py:158` exempt because it is thread-identity membership
- `test_store_plot_summary.py:123` exempt because the needle (`aux_only`) has letters, which no ts, tick or clock rendering contains
- `test_store_plot_summary.py:124` exempt because the needle (`aux`) has letters, which no ts, tick or clock rendering contains
- `test_scaffold.py:106` exempt because the needle (`requires Python 3.10`) has letters, which no ts, tick or clock rendering contains
- `test_cli_attach.py:33` exempt because the needle (`note:`) has letters, which no ts, tick or clock rendering contains
- `test_cli_attach.py:61` exempt because the needle (`note:`) has letters, which no ts, tick or clock rendering contains
- `test_cli_attach.py:76` exempt because the needle (`device`) has letters, which no ts, tick or clock rendering contains
- `test_cli_attach.py:85` exempt because the needle (`serial_number`) has letters, which no ts, tick or clock rendering contains
- `test_cli_attach.py:124` exempt because the needle (`no such port`) has letters, which no ts, tick or clock rendering contains
- `test_flow_cli_windows.py:106` exempt because the needle (`early`) has letters, which no ts, tick or clock rendering contains
- `test_flow_cli_windows.py:121` exempt because the needle (`early`) has letters, which no ts, tick or clock rendering contains
- `test_config_api.py:95` exempt because the needle (`[[ports]]`) has letters, which no ts, tick or clock rendering contains
- `test_config_api.py:118` exempt because the needle (`token`) has letters, which no ts, tick or clock rendering contains
- `test_config_api.py:562` exempt because the needle (`identify`) has letters, which no ts, tick or clock rendering contains
- `test_config_api.py:584` exempt because the needle (`\r\n`) has letters, which no ts, tick or clock rendering contains
- `test_server_export_windows.py:267` exempt because the needle (`!p v=`) has letters, which no ts, tick or clock rendering contains
- `test_server_export_windows.py:330` exempt because it is a positive assertion (the grep matched its message)
- `test_cli_start_index_build.py:103` exempt because the needle (`building index`) has letters, which no ts, tick or clock rendering contains
- `test_cli_start_index_build.py:128` exempt because the needle (`building index`) has letters, which no ts, tick or clock rendering contains
- `test_cli_start_index_build.py:142` exempt because the needle (`did not come up`) has letters, which no ts, tick or clock rendering contains
- `test_cli_start_index_build.py:160` exempt because the needle (`is building index`) has letters, which no ts, tick or clock rendering contains
- `test_pidfile.py:37` exempt because the haystack is a file name built from host and port, no clock value
- `test_capture_lock.py:46` exempt because the needle (`held by pid`) has letters, which no ts, tick or clock rendering contains
- `test_capture_lock.py:163` exempt because it is path membership
- `test_cli_terminal_controls.py:28` exempt because the haystack is `visible()` of a fixed string with no clock value
- `test_cli_terminal_controls.py:57` exempt because the needle (`\x07`) has letters, which no ts, tick or clock rendering contains
- `test_cli_argv.py:138` exempt because the needle (`-p/--port is empty`) has letters, which no ts, tick or clock rendering contains
- `test_assert.py:295` exempt because the needle (`big useless capture`) has letters, which no ts, tick or clock rendering contains
- `test_assert.py:309` exempt because the needle (`line 2`) has letters, which no ts, tick or clock rendering contains
- `test_assert.py:339` exempt because the needle (`old one`, `old two`) has letters, which no ts, tick or clock rendering contains
- `test_assert.py:361` exempt because the needle (`inside`) has letters, which no ts, tick or clock rendering contains
- `test_assert.py:388` exempt because the needle (`before the run`, `after the run`) has letters, which no ts, tick or clock rendering contains
- `test_assert.py:422` exempt because the needle (`before the run`) has letters, which no ts, tick or clock rendering contains
- `test_cli_follow_frames.py:128` exempt because the needle (`[a]`) has letters, which no ts, tick or clock rendering contains
- `test_cli_closed_stdio.py:36` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli_closed_stdio.py:51` exempt because the needle (`unreachable`) has letters, which no ts, tick or clock rendering contains
- `test_cli_closed_stdio.py:78` exempt because the needle (`[y/N]`) has letters, which no ts, tick or clock rendering contains
- `test_cli_closed_stdio.py:99` exempt because the needle (`WARNING`, `no console`) has letters, which no ts, tick or clock rendering contains
- `test_cli_small_refusals.py:114` exempt because the needle (`note:`) has letters, which no ts, tick or clock rendering contains
- `test_cli_small_refusals.py:117` exempt because the needle (`note:`) has letters, which no ts, tick or clock rendering contains
- `test_port_column_stored.py:209` exempt because the needle (`/lines`) has letters, which no ts, tick or clock rendering contains
- `test_export_lines_can.py:91` exempt because the needle (`inside-late`) has letters, which no ts, tick or clock rendering contains
- `test_export_lines_can.py:92` exempt because the needle (`before`) has letters, which no ts, tick or clock rendering contains
- `test_daemon_startup.py:194` complies: the whole address, not a bare port number (the comment names why)
- `test_daemon_startup.py:219` exempt because the needle (`not found`) has letters, which no ts, tick or clock rendering contains
- `test_daemon_startup.py:250` exempt because the needle (`no such config file`) has letters, which no ts, tick or clock rendering contains
- `test_daemon_startup.py:386` exempt because the needle (`mcuscoped:`) has letters, which no ts, tick or clock rendering contains
- `test_daemon_startup.py:396` exempt because the needle (`is in use`) has letters, which no ts, tick or clock rendering contains
- `test_e2e.py:373` exempt because it is integer-set membership
- `test_server_exports.py:488` exempt because it is set membership
- `test_cli_send_verdicts.py:45` exempt because the needle (`timeout: no line matched`) has letters, which no ts, tick or clock rendering contains
- `test_cli_send_verdicts.py:81` exempt because the needle (`refused`) has letters, which no ts, tick or clock rendering contains
- `test_cli_send_verdicts.py:106` exempt because the needle (`PASS`) has letters, which no ts, tick or clock rendering contains
- `test_cli_send_verdicts.py:122` exempt because the needle (`allow_empty`) has letters, which no ts, tick or clock rendering contains
- `test_cli_send_verdicts.py:140` exempt because the needle (`repeat_ms`, `timeout_ms`, `min_window_ms`) has letters, which no ts, tick or clock rendering contains
- `test_cli_send_verdicts.py:186` exempt because the needle (`(sent `) has letters, which no ts, tick or clock rendering contains
- `test_cli_send_verdicts.py:196` exempt because the needle (`timeout: no line matched`) has letters, which no ts, tick or clock rendering contains
- `test_cli_send_verdicts.py:208` exempt because the needle (`sent `) has letters, which no ts, tick or clock rendering contains
- `test_cli_send_verdicts.py:216` exempt because it is a positive assertion (the grep matched its text)
- `test_cli_send_verdicts.py:217` exempt because the needle (`unreachable`) has letters, which no ts, tick or clock rendering contains
- `test_reconnect.py:458` exempt because the needle (`a0`) has letters, which no ts, tick or clock rendering contains
- `test_reconnect.py:605` exempt because it is a positive list assertion (`not in` filters a comprehension)
- `test_cli_daemon_stop_scope.py:152` exempt because the needle (`stopped`) has letters, which no ts, tick or clock rendering contains
- `test_cli_daemon_stop_scope.py:266` exempt because the needle (`the serving daemon's warning`) has letters, which no ts, tick or clock rendering contains
- `test_decode_per_port.py:544` exempt because the needle (`plot_3.csv`, `plot_adhoc.csv`) has letters, which no ts, tick or clock rendering contains
- `test_cli_read_scope.py:65` exempt because the needle (`[a]`) has letters, which no ts, tick or clock rendering contains
- `test_cli_read_scope.py:68` complies: `[` against rendered lines whose clock text is digits, `:` and `.` only
- `test_cli_read_scope.py:88` exempt because the needle (`/lines/export`) has letters, which no ts, tick or clock rendering contains
- `test_cli_read_scope.py:150` exempt because the needle (`--since-id`) has letters, which no ts, tick or clock rendering contains
- `test_cli_read_scope.py:161` exempt because the needle (`truncated`) has letters, which no ts, tick or clock rendering contains
- `test_cli_read_scope.py:163` exempt because the needle (`truncated`) has letters, which no ts, tick or clock rendering contains
- `test_cli_read_scope.py:222` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli_read_scope.py:306` exempt because the needle (`raise --limit`) has letters, which no ts, tick or clock rendering contains
- `test_cli_export_files.py:124` exempt because the needle (`\r\n`) has letters, which no ts, tick or clock rendering contains
- `test_cli_export_files.py:262` exempt because the needle (`\r\n`) has letters, which no ts, tick or clock rendering contains
- `test_store_schema.py:100` exempt because the needle (`idx_sessions_active`) has letters, which no ts, tick or clock rendering contains
- `test_server_purge_and_sessions.py:53` exempt because the needle (`older-high-id`) has letters, which no ts, tick or clock rendering contains
- `test_store_time_window.py:125` exempt because the needle (`until_ts`) has letters, which no ts, tick or clock rendering contains
- `test_store_time_window.py:178` exempt because the needle (`B-TREE`) has letters, which no ts, tick or clock rendering contains
- `test_status_ppid_serial.py:37` exempt because the needle (`note:`) has letters, which no ts, tick or clock rendering contains
- `test_plot_export_decode.py:448` exempt because the needle (`IDLE`, `OTHER_ZERO`) has letters, which no ts, tick or clock rendering contains
- `test_cli_contract.py:64` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli_contract.py:88` exempt because the needle (`unreachable`) has letters, which no ts, tick or clock rendering contains
- `test_cli_contract.py:250` exempt because the needle (`Traceback`, `unexpected response`) has letters, which no ts, tick or clock rendering contains
- `test_cli_contract.py:509` exempt because the needle (`OverflowError`) has letters, which no ts, tick or clock rendering contains
- `test_cli_contract.py:539` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli_contract.py:565` exempt because the needle (`dict(os.environ`) has letters, which no ts, tick or clock rendering contains
- `test_cli_export.py:92` exempt because the needle (`bound-b`) has letters, which no ts, tick or clock rendering contains
- `test_cli_export.py:172` exempt because the needle (`lim-a`) has letters, which no ts, tick or clock rendering contains
- `test_cli_output_rows.py:35` exempt because the needle (`skipping bad frame`) has letters, which no ts, tick or clock rendering contains
- `test_sessions.py:54` exempt because the needle (`before`, `after`) has letters, which no ts, tick or clock rendering contains
- `test_sessions.py:82` exempt because the needle (`in b`) has letters, which no ts, tick or clock rendering contains
- `test_sessions.py:238` exempt because the needle (`outside the run`) has letters, which no ts, tick or clock rendering contains
- `test_sessions.py:239` exempt because the needle (`before the run`) has letters, which no ts, tick or clock rendering contains
- `test_sessions.py:325` exempt because the needle (`inside the open run`) has letters, which no ts, tick or clock rendering contains
- `test_sessions.py:338` exempt because the needle (`inside the open run`) has letters, which no ts, tick or clock rendering contains
- `test_sessions.py:419` exempt because it is integer membership
- `test_sessions.py:501` exempt because the needle (`run 0 payload`) has letters, which no ts, tick or clock rendering contains
- `test_sessions.py:574` exempt because the needle (`run-1 payload`) has letters, which no ts, tick or clock rendering contains
- `test_sessions.py:615` exempt because the needle (`run-1 payload`) has letters, which no ts, tick or clock rendering contains
- `test_sessions.py:638` exempt because the needle (`ambient`) has letters, which no ts, tick or clock rendering contains
- `test_sessions.py:888` exempt because the needle (`s0`) has letters, which no ts, tick or clock rendering contains
- `test_stdio.py:199` exempt because the haystack is a file name built from host and port, no clock value
- `test_stdio.py:309` exempt because it is tuple membership
- `test_stdio.py:347` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli_daemonctl.py:144` exempt because the needle (`no such config file`) has letters, which no ts, tick or clock rendering contains
- `test_cli_daemonctl.py:173` exempt because the needle (`no such config file`) has letters, which no ts, tick or clock rendering contains
- `test_cli_daemonctl.py:179` exempt because the needle (`warns if missing`) has letters, which no ts, tick or clock rendering contains
- `test_cli_daemonctl.py:319` exempt because the needle (`no such config file`) has letters, which no ts, tick or clock rendering contains
- `test_cli_daemonctl.py:376` exempt because the needle (`started mcuscoped`) has letters, which no ts, tick or clock rendering contains
- `test_cli_daemonctl.py:455` exempt because the needle (`started mcuscoped`) has letters, which no ts, tick or clock rendering contains
- `test_cli_daemonctl.py:477` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli_daemonctl.py:532` exempt because the needle (`refused the request`) has letters, which no ts, tick or clock rendering contains
- `test_server_live_verdicts.py:134` exempt because the needle (`no such port`) has letters, which no ts, tick or clock rendering contains
- `test_server_live_verdicts.py:168` exempt because the needle (`ambiguous`) has letters, which no ts, tick or clock rendering contains
- `test_server_live_verdicts.py:179` exempt because the needle (`ambiguous`) has letters, which no ts, tick or clock rendering contains
- `test_config_loader.py:156` exempt because it is dict-key membership
- `test_serial_link_attach.py:516` exempt because the needle (`board`) has letters, which no ts, tick or clock rendering contains
- `test_cli_version_gate.py:232` exempt because the needle (`/status`) has letters, which no ts, tick or clock rendering contains
- `test_cli_version_gate.py:261` exempt because the needle (`does not serve`) has letters, which no ts, tick or clock rendering contains
- `test_plot.py:152` exempt because the needle (`gpio`) has letters, which no ts, tick or clock rendering contains
- `test_cli_follow.py:90` exempt because the needle (`stopped answering`) has letters, which no ts, tick or clock rendering contains
- `test_cli_follow.py:262` exempt because the needle (`never retrieved`) has letters, which no ts, tick or clock rendering contains
- `test_cli_follow.py:277` exempt because the needle (`stream closed by daemon`) has letters, which no ts, tick or clock rendering contains
- `test_cli_follow.py:303` exempt because the needle (`too many subscribers`) has letters, which no ts, tick or clock rendering contains
- `test_store_subscribers.py:69` exempt because it is object membership
- `test_cli_prompts.py:60` exempt because the needle (`DELETE`) has letters, which no ts, tick or clock rendering contains
- `test_cli_closed_pipe.py:94` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli_closed_pipe.py:117` exempt because the needle (`BrokenPipeError`) has letters, which no ts, tick or clock rendering contains
- `test_render_line_breaks.py:32` exempt because the needle (`[board2]`) has letters, which no ts, tick or clock rendering contains
- `test_store_fastpaths.py:416` exempt because the needle (`pause_writing`) has letters, which no ts, tick or clock rendering contains
- `test_session_bundle.py:164` exempt because the needle (`before the run`, `after the run`) has letters, which no ts, tick or clock rendering contains
- `test_session_bundle.py:205` exempt because the needle (`can.csv`) has letters, which no ts, tick or clock rendering contains
- `test_session_bundle.py:378` complies: membership over value cells split from the row, the ts column excluded (the comment names the trap)
- `test_session_bundle.py:379` complies: same as :378
- `test_webui.py:260` exempt because the needle (`__MCUSCOPE_VERSION__`) has letters, which no ts, tick or clock rendering contains
- `test_daemon_config_path.py:125` exempt because the needle (`no such config file`) has letters, which no ts, tick or clock rendering contains
- `test_plotjuggler.py:440` exempt because the needle (`plotjuggler`) has letters, which no ts, tick or clock rendering contains
- `test_cli_closed_output.py:127` exempt because the needle (`Traceback`, `Exception ignored`) has letters, which no ts, tick or clock rendering contains
- `test_cli_closed_output.py:208` exempt because the needle (`daemon unreachable`) has letters, which no ts, tick or clock rendering contains
- `test_timeline.py:70` exempt because the needle (`truncated`) has letters, which no ts, tick or clock rendering contains
- `test_sim.py:787` exempt because the needle (`\x01`) has letters, which no ts, tick or clock rendering contains
- `test_server_request_validation.py:165` exempt because the needles are CR and LF, which no clock rendering holds
- `test_cli_status.py:27` exempt because the needle (`trimmed`) has letters, which no ts, tick or clock rendering contains
- `test_cli_status.py:47` complies: a 19-digit float repr beside the positive `last=0.140901 V`; the canned haystack carries no clock value
- `test_store_match_budget.py:48` exempt because the needle (`simplify`) has letters, which no ts, tick or clock rendering contains
- `test_store_match_budget.py:63` exempt because the needle (`window`) has letters, which no ts, tick or clock rendering contains
- `test_daemon_startlog.py:46` exempt because the needle (`started, pid`) has letters, which no ts, tick or clock rendering contains
- `test_daemon_startlog.py:114` exempt because the needle (`started, pid`) has letters, which no ts, tick or clock rendering contains
- `test_sim_pty.py:170` exempt because the needle (`exc`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:148` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:161` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:173` exempt because the needle (`Exception ignored`, `Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:182` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:656` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:839` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:1030` exempt because the needle (`after cli-run`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:1084` exempt because the needle (`junk payload`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:1150` exempt because the needle (`delete this line`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:1170` exempt because the needle (`Traceback`, `Abort`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:1235` exempt because the needle (`spare`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:1574` exempt because the needle (`not authorised`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:1625` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:1632` exempt because the needle (`drop-that-one`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:1684` exempt because the needle (`bus=`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:1924` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:2094` exempt because the needle (`skipping bad frame`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:2096` exempt because the needle (`abc`, `abc`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:2382` exempt because the needle (`CAPTURE STOPPED`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:2431` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:2438` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:2446` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:2500` exempt because the needle (`write_errors`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:2524` exempt because the needle (`update available`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:2537` exempt because the needle (`Missing command`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:2574` exempt because the needle (`[y/N]`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:2612` exempt because the needle (`ports`) has letters, which no ts, tick or clock rendering contains
- `test_cli.py:2784` exempt because the needle (`Traceback`) has letters, which no ts, tick or clock rendering contains

### Where the registry's sweep is imprecise

- The entry names "every test comparing a captured `time.time()`" but R21-1's boundary is a *stored* stamp, and the defect is the assumption that a later row has a strictly later stamp. The clock grep cannot see it. Added S3 (`\["(ts|started_ts|ended_ts)"\]`), and the rule: a boundary taken from one stored row must also prove a strictly greater stamp exists on the far side.
- Wall-clock thresholds have no sweep in the entry. S2 is the added one; it still missed R21-4 (`assert max(gaps) < 0.2`) because the name is `gaps`. Improved, run:
  `grep -nE "assert .*(elapsed|gap|monotonic\(\) *-|time\(\) *-|perf_counter\(\) *-|loop\.time\(\) *-|took|spent|\bdt\b|duration|latency)" host/tests/*.py` returned 33 lines: the 26 above and 7 more, ruled here: `test_wait_repeat.py:369` violates (R21-4); `test_serial_link_tx.py:112` complies (latency and the bound are on one clock and nested, see S1 `:106`); `test_e2e.py:185` complies (`latency_ms >= 0` holds at a zero reading); `test_e2e.py:595` and `:596` exempt (a counted WS shed gap, not a clock); `test_server_shutdown.py:247` exempt (a drop count); `test_sessions.py:902` exempt (a comment).
- The `not in` sweep's literal form misses the entry's own example shape as written in this tree (`assert not any("SCAN lines" in r ...)`); S6 is the added grep.
- The sweep is a read, not a drive. Running the suspect files under an emulated 15.625 ms clock found R21-1, which reading had ruled compliant ("derived from the data"). Recommend adding `quantclock.py` (or a conftest option) to the entry as the drive for this class.

### Owed on Windows

- Run R21-1's test and `test_assert.py::test_last_ms_window` 20 times each on Windows CPython 3.10, 3.11 and 3.12 (tick-granular `time.time`); the emulation covers granularity only.
- R21-2 to R21-6 need a slow Windows runner to fail; the emulation does not produce scheduling delay.

### The two questions (classes 20 and 21)

1. **Least confident, and rechecked.**
   - R21-1 rests on an emulation, not a Windows run. Re-driven: five more whole-file runs under the emulated clock (one more failure, same test) and three whole-file runs without it (all pass), so the failure follows the clock, not the order or the load. Still owed on real Windows 3.10-3.12.
   - R20-1's severity rested on direct store calls. Re-driven end to end through `GET /plot/export` on the 1M capture (2.7 s to 7.0 s from adding one name), and the bundle path separately (1.5 s against 0.1 ms). Plans re-explained on SQLite 3.45.1 and 3.53.1: identical.
   - R20-3 depends on protected expired rows being the normal case. Driven through the default path (`min_sessions` 5, fewer than 5 sessions, so the oldest session's start is the floor), which is every daemon whose auto session outlives `retention_days`. The frequency (hourly) and the 6M-line extrapolation (about 1 s) are reasoned from the measured 75 ms per 500k rows, not measured at 6M.
   - Reasoned only: R21-2 to R21-6 (they need a slow runner), the `export_session_db` copy statements (they run against an ATTACHed file and were not explained), and every plan on a Windows-bundled SQLite.
   - Timings came from a desktop shared with other agents' test runs; only the relative gaps are relied on.
2. **What we should have checked and have not.**
   - The other watermark readers. `mcu lines -f` follows over the WebSocket, but the web UI terminal's history paging (`webui/terminal.js:515-521`) sends `id_to`, `since_id=clearId` and the pane's `match`: for a rare pattern each page is an offloaded REGEXP walk down to the clear point, up to `HISTORY_HOPS` pages per scroll. Bounded by the match budget; not measured.
   - `can dump -f` is the only CLI follow that polls; R20-2 asks whether it should follow over `/ws` like `lines -f`, which removes the walk rather than bounding it.
   - The two timed floods (`test_assert.py:722`, `test_server_live_verdicts.py:351`) pass even when the flood lands before the handler subscribes, because the sim's own bursts overflow the 4-row queue. The assertion is satisfied by something other than the stimulus the test names: class 78's shape (an observation point that receives the thing from elsewhere), for that class's leg to rule.
   - R20-5 means the registry text for class 20 cites `/plot/channels?port=` for code no handler runs; the entry should point at `_scan_plot_rows` and `test_store_plot_summary.py:84`.
   - Class 21 swept `host/tests` and the web UI tests only; `firmware/tests` use a fake clock (`fake_shims.c:21`) and were not swept line by line.
   - My first `follow20.py` run met another leg's daemon already bound to 127.0.0.1:18620 and polled it read-only (`GET /status`, `GET /can/frames`) for about 18 s before I moved to 18621. The port rule in the sub-brief lets two legs derive the same port.

## Class 22. A stdlib predicate standing in for a wire grammar

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3 (checked with `git rev-parse HEAD`).
The discriminating input throughout is U+0663 (`٣`); the superscript was not used.

### Findings

Listed at the top of this file.

### Sweeps, verbatim, with counts

Registry sweeps:
- A: `grep -rn "isdigit()\|isdecimal()\|isalnum()" host/mcuscope`: **11** lines.
- B: `grep -rnE "\b(int|float|bool)\(" host/mcuscope --include=*.py`: **122** lines.
  - 29 of them are comments or docstrings; 93 are code.
- C: `grep -rn "parseInt\|parseFloat" host/mcuscope/webui`: **5** lines.

Added, because A-C miss them (see "Sweep precision"):
- D: `grep -rnE "\bNumber\(" host/mcuscope/webui --include=*.js`: **6** lines.
- E: `grep -nE "(^|[=(,:?!&|] *|return +)\+[a-zA-Z_$][a-zA-Z0-9_$.]*(\[[^]]*\])*" host/mcuscope/webui/*.js | grep -vE "^\S+:\s*//" | grep -vE "\+\+"`: **8** unary-plus coercions.
- F: `grep -rn "fromhex\|struct\.unpack" host/mcuscope --include=*.py`: **3**.
- G: the click tree walk (`typer.main.get_command(cli.app)`, every param whose type is Int*/Float*): **33**.
- H: the FastAPI route walk (`create_app(Config())`, `route.dependant.query_params + path_params` annotated int/float/bool): **35**.
- I: `grep -nE "\.(l|r)?strip\(\)|\.split\(\)|isspace\(\)" host/mcuscope/protocol.py host/mcuscope/serial_link.py host/mcuscope/sim.py`: **9**.
- J: `grep -rn "ip_address\|ip_network" host/mcuscope --include=*.py`: **2**.
- K: `grep -rn "os.environ\|getenv" host/mcuscope --include=*.py`: **9**.
- L: `grep -rnE "atoi|atol|strto|sscanf|isdigit|isxdigit|isspace|isalnum|isalpha|ctype.h" firmware/monitor`: **0**, plus a hand list of the **19** firmware input parsers.

### Site rulings

A (11):
- `sim.py:657` exempt: comment.
- `protocol.py:205` exempt: docstring.
- `protocol.py:208` complies: the definition of `is_decimal_token` (`isascii()` + `isdecimal()` + length cap).
- `protocol.py:520` exempt: comment.
- `protocol.py:534` exempt: comment.
- `protocol.py:535` exempt: comment.
- `protocol.py:748` exempt: comment.
- `protocol.py:749` exempt: comment.
- `pjstream.py:45` complies: gated by `isascii()`, range 1..65535; an over-long token raises the ValueError `parse_dest` documents.
- `store.py:1401` exempt: comment.
- `store.py:1403` exempt: comment.

B (122):
- `sim.py:134` exempt: internal scheduling arithmetic.
- `sim.py:401` exempt: internal waveform.
- `sim.py:505` exempt: internal flood count.
- `sim.py:536` exempt: internal waveform.
- `sim.py:623` exempt: internal waveform.
- `sim.py:627` exempt: internal bool-to-int.
- `sim.py:628` exempt: internal bool-to-int.
- `sim.py:630` exempt: internal waveform.
- `sim.py:658` exempt: comment.
- `sim.py:661` complies: `_parse_dec` gates on `is_decimal_token`, then range.
- `cli_output.py:59` exempt: isatty result.
- `cli_output.py:306` exempt: formatting a duration.
- `cli_output.py:307` exempt: formatting a duration.
- `cli_output.py:331` complies: `parse_clock` groups gated by `[0-9]` in `_CLOCK_RE`.
- `cli_output.py:335` complies: same.
- `cli_output.py:336` complies: same.
- `cli_output.py:362` exempt: formatting a decoded float.
- `cli_output.py:435` exempt: an already-decoded enum point.
- `cli_output.py:446` exempt: display of a daemon JSON number, tolerant by design (`?`).
- `cli_output.py:454` exempt: docstring.
- `protocol.py:173` exempt: docstring.
- `protocol.py:180` complies: explicit hex set, <= 16 digits (`parse_hex_int`).
- `protocol.py:193` exempt: comment.
- `protocol.py:197` exempt: comment.
- `protocol.py:203` exempt: docstring.
- `protocol.py:206` exempt: docstring.
- `protocol.py:220` complies: `int_arg` gates on `is_decimal_token`.
- `protocol.py:244` exempt: docstring.
- `protocol.py:252` complies: `parse_seq_token`.
- `protocol.py:378` exempt: comment.
- `protocol.py:383` complies: ERR code gated by `is_decimal_token`.
- `protocol.py:442` complies: last char in `"123456789"`.
- `protocol.py:521` exempt: comment.
- `protocol.py:526` complies: tick gated by `is_decimal_token` + TICK_MS_MAX.
- `protocol.py:535` exempt: comment.
- `protocol.py:539` complies: single char in `"0123456789"`.
- `protocol.py:588` complies: single char in `"0123456789"`.
- `protocol.py:625` exempt: comment.
- `protocol.py:629` exempt: comment.
- `protocol.py:693` complies: `_PLOT_VALUE_RE` fullmatch + isfinite.
- `protocol.py:712` complies: `is_decimal_token` + range.
- `protocol.py:781` exempt: comment.
- `protocol.py:789` complies: `[0-9]` regex + 20-digit cap + sign rule.
- `protocol.py:865` violates: its operand comes from `bytes.fromhex` at :861; `struct.error` escapes (R22-1).
- `protocol.py:866` violates: the same operand (R22-1).
- `protocol.py:887` exempt: comment.
- `protocol.py:888` complies: `[0-9a-fA-F]+` fullmatch; base 16 has no digit limit; range checked.
- `protocol.py:900` exempt: an already-decoded value.
- `protocol.py:903` exempt: internal.
- `protocol.py:1110` exempt: comment.
- `protocol.py:1115` complies: `@([0-9]+)` fullmatch + 20-digit cap + range.
- `serial_link.py:211` exempt: comment.
- `serial_link.py:215` complies: `_response_seq` gates on `is_decimal_token`.
- `update_check.py:66` exempt: comment.
- `update_check.py:78` complies: `[0-9]` regex + 64-char cap.
- `update_check.py:188` exempt: its own cache file; any bad value, parsed or not, degrades to "check now", and non-finite values are refused at :191.
- `update_check.py:192` exempt: comment.
- `cli_daemonctl.py:149` violates: environment value through bare `float()` (R22-3).
- `cli_daemonctl.py:223` exempt: the match is inside a comment; the call is `read_pid_record`.
- `render.py:23` exempt: formatting.
- `_stdio.py:56` exempt: Win32 return value.
- `_stdio.py:104` exempt: Win32 return value.
- `pjstream.py:38` exempt: docstring.
- `pjstream.py:47` complies: gated by ASCII digits at :45 and a range.
- `server.py:865` exempt: a constant.
- `server.py:3166` exempt: internal.
- `server.py:3473` exempt: stored 0/1 columns.
- `server.py:3588` exempt: comment.
- `server.py:3623` exempt: internal.
- `server.py:3639` exempt: labels already parsed by protocol.
- `server.py:3659` exempt: a stored enum value.
- `server.py:3661` exempt: same.
- `cli.py:646` exempt: a daemon JSON boolean.
- `cli.py:678` exempt: same.
- `cli.py:963` exempt: internal.
- `cli.py:1033` exempt: internal.
- `cli.py:1671` exempt: the CLI's own flag rendered as `true`/`false`.
- `cli.py:1918` exempt: internal.
- `cli.py:1957` exempt: internal.
- `cli.py:3220` exempt: click exit code.
- `cli.py:3262` exempt: SystemExit code.
- `store.py:597` complies: the value comes from `config._as_int` or a strict body.
- `store.py:598` complies: same.
- `store.py:621` exempt: PRAGMA result.
- `store.py:716` exempt: DB row.
- `store.py:1079` exempt: decoder bool.
- `store.py:1080` exempt: decoder bool.
- `store.py:1179` exempt: decoder bool.
- `store.py:1180` exempt: decoder bool.
- `store.py:1365` exempt: DB column.
- `store.py:1383` exempt: DB row.
- `store.py:1394` complies here: an int from the route; the route's lax grammar is R22-4.
- `store.py:1402` exempt: comment.
- `store.py:1405` exempt: comment.
- `store.py:1411` complies: `resolve_session` gates on `is_decimal_token`.
- `store.py:1437` complies here: the limit is already an int (upstream grammar is R22-4).
- `store.py:1483` exempt: internal.
- `store.py:1628` exempt: internal.
- `store.py:1890` exempt: DB row.
- `store.py:1895` exempt: DB row.
- `store.py:1922` exempt: DB row.
- `store.py:1945` complies here (upstream R22-4).
- `store.py:2012` exempt: DB count.
- `store.py:2150` exempt: internal.
- `store.py:2198` complies here (upstream R22-4).
- `store.py:2254` exempt: DB column.
- `store.py:2255` exempt: DB column.
- `store.py:2500` complies here (upstream R22-4).
- `store.py:2501` complies here (upstream R22-4); `decimate` has no `ge` on the route and is clamped to 1 here, which is the documented floor.
- `store.py:2657` exempt: DB row.
- `store.py:2823` complies: a strict body value.
- `store.py:2832` complies: a strict body value.
- `store.py:2924` exempt: DB row.
- `store.py:2958` exempt: DB count.
- `store.py:3029` exempt: arithmetic.
- `store.py:3030` exempt: arithmetic.
- `config.py:204` exempt: docstring.
- `config.py:245` exempt: docstring.
- `config.py:254` exempt: comment. The config loader reads every value through `_as_int`/`_as_bool`/`_as_str`/`_as_choice`; no bare coercion is left.
- `pidfile.py:126` exempt: docstring.
- `pidfile.py:127` exempt: docstring.
- `pidfile.py:149` complies: `is_decimal_token` + 1..PID_MAX.

C (5):
- `state.js:189` exempt: comment.
- `can.js:70` exempt: base-16 use on a hex wire token, gated by `/^(0[xX])?[0-9a-fA-F]{1,16}$/`.
- `plots.js:97` exempt: `parseFloat` gated by the grammar regex + `Number.isFinite`.
- `plots.js:221` exempt: base 16 on a pair gated by `/^[0-9a-fA-F]+$/` and the width.
- `plots.js:234` exempt: base 16 on a tick gated by `/^[0-9a-fA-F]+$/` + range.

D (6):
- `state.js:192` exempt: comment.
- `state.js:194` exempt: comment.
- `state.js:198` violates: intField's `Number()` grammar (R22-5).
- `timewindow.js:240` exempt: formatting an internal number.
- `chrome.js:155` exempt: its own `data-secs` attribute.
- `plots.js:177` complies: gated by `/^-?\d+$/` + `isDecimalToken`.

E (8):
- `can.js:61` complies: after `/^!can[1-9]?$/`.
- `can.js:62` complies: after `isDecimalToken`.
- `can.js:78` complies: after `/^\d$/`.
- `can.js:79` complies: same.
- `can.js:87` complies: after `isDecimalToken`.
- `plots.js:104` complies: after `isDecimalToken`.
- `plots.js:119` complies: same.
- `state.js:266` complies: after `/^@(\d+)$/` + `isDecimalToken`.

F (3):
- `protocol.py:165` violates (R22-1).
- `protocol.py:861` violates (R22-1).
- `protocol.py:865` violates (R22-1; the unguarded `struct.error`).

G (33 click params), each violating R22-2 by the bare `int()`/`float()` grammar. The bounds are fine and are ruled in class 19.
- `assert --timeout` violates.
- `assert --min-window` violates.
- `assert --last-ms` violates.
- `attach --baud` violates.
- `break --ms` violates.
- `can dump --bus` violates.
- `can dump --last-ms` violates.
- `can dump -n` violates.
- `can filter --bus` violates.
- `can stat --bus` violates.
- `can tx --rtr` violates.
- `can tx --bus` violates.
- `can tx --retry-ms` violates.
- `cmd --timeout` violates.
- `cmd --retry-ms` violates.
- `daemon restart --timeout` violates (float; finiteness is already checked by `finite_option`).
- `daemon start --timeout` violates (float; same).
- `i2c rd N` violates.
- `lines --last-ms` violates.
- `lines --limit` violates.
- `lines --since-id` violates.
- `log export --last-ms` violates.
- `log export --limit` violates.
- `plot channels --active` violates (float; `positive_option` already refuses nan).
- `plot export --last-ms` violates.
- `purge --before-days` violates (float; `finite_option` already applies).
- `purge --id-from` violates.
- `purge --id-to` violates.
- `session list --limit` violates.
- `sysrq --ms` violates.
- `tail -n` violates.
- `wait --timeout` violates.
- `wait --repeat-ms` violates.

H (35 route params), each violating R22-4 through the lax pydantic grammar (not `bool()`: the bool vocabulary is closed). Where a float is involved, finiteness is already guarded by `_check_window` (`server.py:3353`).
- `GET /sessions limit` violates.
- `DELETE /sessions/{session_id} data` violates.
- `DELETE /sessions/{session_id} session_id` violates.
- `GET /sessions/{ref}/export wait` violates.
- `GET /sessions/{ref}/bundle wait` violates.
- `GET /lines since_id` violates.
- `GET /lines since_ts` violates.
- `GET /lines until_ts` violates.
- `GET /lines last_ms` violates.
- `GET /lines id_to` violates.
- `GET /lines limit` violates.
- `GET /lines/export since_id` violates.
- `GET /lines/export since_ts` violates.
- `GET /lines/export until_ts` violates.
- `GET /lines/export last_ms` violates.
- `GET /lines/export id_to` violates.
- `GET /can/frames bus` violates.
- `GET /can/frames last_ms` violates.
- `GET /can/frames since_ts` violates.
- `GET /can/frames until_ts` violates.
- `GET /can/frames since_id` violates.
- `GET /can/frames id_to` violates.
- `GET /can/frames limit` violates.
- `GET /plot/series last_ms` violates.
- `GET /plot/series since_id` violates.
- `GET /plot/series id_to` violates.
- `GET /plot/series limit` violates.
- `GET /plot/series decimate` violates.
- `GET /plot/export last_ms` violates.
- `GET /plot/export since_id` violates.
- `GET /plot/export since_ts` violates.
- `GET /plot/export until_ts` violates.
- `GET /plot/export id_to` violates.
- `GET /plot/export decode` violates.
- `GET /plot/export changes` violates.

I (9):
- `protocol.py:288` exempt: docstring.
- `protocol.py:297` violates: `format_command`'s `strip()` (R22-6).
- `protocol.py:346` exempt: the simulator's response emitter; the data is the simulator's own.
- `protocol.py:348` exempt: same emitter.
- `protocol.py:357` exempt: same emitter.
- `protocol.py:1082` exempt: `format_marker` is used only by the simulator, on text it built.
- `protocol.py:1087` exempt: same.
- `protocol.py:1121` violates: `parse_marker`'s `strip()` (R22-6).
- `serial_link.py:129` exempt: a kernel sysfs attribute.

J (2):
- `pjstream.py:75` exempt: an address `getaddrinfo` already resolved.
- `server.py:641` complies: `ipaddress.ip_address` rejects `١٢٧.0.0.1` and `٣` (driven on 3.13.5 and 3.10.20), `127.1` and `0x7f.0.0.1`.

K (9):
- `update_check.py:108` complies: a closed vocabulary, anything else vetoes and warns.
- `daemon.py:121` exempt: a string token.
- `daemon.py:411` exempt: a path.
- `cli_daemonctl.py:146` violates (R22-3, parsed at :149).
- `dirs.py:18` exempt: a path.
- `cli.py:128` exempt: a URL string.
- `cli.py:129` complies: the token is checked ASCII at :131.
- `cli.py:2568` exempt: a path.
- `cli.py:2641` exempt: a write, not a read.

L, firmware input parsers (the wire grammar applies to them: they receive command lines):
- `monitor.c:82` hexval complies: explicit ranges.
- `monitor.c:138` mon_hex_decode complies: even length, byte cap, hexval (driven: refuses tab, VT, FF).
- `monitor.c:155` mon_parse_hex_u32 complies: optional 0x, non-empty, 32-bit overflow check.
- `monitor.c:177` mon_parse_dec_u32 complies: `'0'..'9'`, non-empty, overflow check; no digit-count bound, which SPEC 2.1 permits.
- `monitor.c:323` is_dec_digit complies.
- `monitor.c:1024` tokenize complies: U+0020 only.
- `monitor.c:1051` recover_seq complies.
- `monitor.c:1064` process_line complies: 7-bit and NUL check, seq 1..65535, 12 tokens.
- `monitor.c:1124` assemble_one complies for host input: every CR is dropped, but the host never sends one.
- `monitor_cmds.c:53` can_bus_of complies.
- `monitor_cmds.c:62` parse_can_flags complies.
- `monitor_cmds.c:141` cmd_can_tx complies (RTR multi-digit is SPEC 2.4 documented).
- `monitor_cmds.c:180` cmd_can_filter complies.
- `monitor_cmds.c:285` cmd_i2c_wr complies.
- `monitor_cmds.c:302` cmd_i2c_rd complies.
- `monitor_cmds.c:325` cmd_i2c_wrrd complies.
- `monitor_cmds.c:359` cmd_spi_xfer complies.
- `monitor_cmds.c:386` cmd_gpio_set complies (exact `0`/`1`).
- `monitor_cmds.c:523` family_match complies: `'0'..'9'` then NUL.

### Sweep precision

The registry's three commands miss every site behind R22-1, R22-2, R22-4 and R22-6, and one behind R22-5:
- `bytes.fromhex` is a stdlib parser with its own tolerance, and matches none of `int(`, `float(`, `bool(`. Add sweep F.
- click's INT/FLOAT and pydantic's lax query/path coercion call `int()` inside the library, so no `int(` appears in this tree. Add the G and H walks. The scripts are one-liners over `typer.main.get_command` and `app.routes[].dependant`.
- `str.strip()`/`str.split()` on wire text is the whitespace face of the same class. Add sweep I.
- JavaScript's `Number()` is as permissive as `parseInt` in a different direction (`0x`, `0b`, `0o`, exponent). Add D, and E for unary `+`.
- Noise: 29 of B's 122 lines are comments or docstrings; every one is ruled above.

### Owed on Windows or a real browser

- Browser: whether a real `<input type=number>` hands `intField` an empty string for `0x10`/`0b1`, so that only the two text inputs are exposed (R22-5). The dom_stub cannot answer this.
- Windows: nothing class specific. `bytes.fromhex`, click and pydantic behave the same; not run there.

### The two questions (classes 19 and 22)

1. What am I least confident about here? Rechecked:
   - R22-1(e), the CLI half. I reasoned it is latent and then re-drove the one path that could reach it: `fake_board.py` sends the bad `!ps` before its `!pd`, so the daemon stores it as a generic event.
     - `mcu -p fake tail -n 10 --decode` printed that row raw and exited 0. The CLI primes as of the window's first row, so it never has the definition the daemon lacked.
     - `LineDecoder` does raise `struct.error` when handed that def and line directly. So the guard gap is real, and I found no stored row that reaches it.
   - R22-6 rests on an undefined SPEC word ("non-space"). Owner pick.
   - R19-2's pane side: a node run stands in for the browser, which is valid because ECMAScript fixes `\d \w \s .` without the `u` flag. The history-page half was verified by reading `terminal.js:522` (it sends `match=`), not driven in a browser.
   - R22-5 on `type=number` inputs needs a real browser (owed).
   - Everything else in the findings was driven.
2. What should we have checked that we have not thought about?
   - `GET /sessions?limit=` is clamped to 1000 at `store.py:1437`. I did not check whether the response announces the clamp (the "never silently cut" convention). Not class 19/22.
   - The export dialog's clock mode sends `since_ts = fromTs` (`exportrange.js:65`), which the daemon treats as exclusive, while the dialog calls `fromTs` inclusive. A row exactly on the bound is lost. Boundary semantics, not swept.
   - Hex-token digit counts: the simulator (via `parse_hex_int`) refuses ids and addresses past 16 hex digits of leading zeros, which the firmware accepts. SPEC 2.1 permits a receiver bound only for decimal tokens and says nothing for hex. Owner may want the SPEC to say it.
   - `canFilterPattern` (`can.js:234`) matches undecodable `!can` lines of the chosen id, so a pane filtered from the CAN table shows garbled lines the table does not count. Harmless as a text filter, but its comment claims decoder-grammar parity.
   - Neither host nor firmware bounds an enum value to its type width (`!pd 0 m:u1=300=HOT` is valid everywhere). Every side agrees, so it is not a mirror miss, but it is an unchecked wire bound.

## Class 23. A rebuild path silently un-freezes a paused surface

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3 (checked with `git rev-parse HEAD` before sweeping).
Scratch, probes and logs: `~/tt-data/mcuscope-2026-09-24/registry-leg/15-28/scratch-23-25-26/`.

### Findings

Listed at the top of this file.

### Sweep 1: every paused, frozen or held surface (the state setters)

Command, from `host/mcuscope/webui`:
`grep -n -E '\b(pane\.autoscroll|chart\.paused|digitalPaused|canPaused|highRate|allPaused)\s*=[^=]|\bsetZoom\(|holdPath\(|setBusy\(|devicesLoading\s*=[^=]|\.disabled\s*=\s*true' *.js`
Site count: 33.

- exportdlg.js:245 Export button busy: complies. Reopening frees it deliberately (`:217`), and the closed dialog's download saves nothing (`wanted()` is `gen === dialogGen`).
- plots.js:969 `setZoom(z)`, the held zoom range: complies. The range object is never rewritten, only replaced or dropped by the SPEC 9.2 exits.
- plots.js:982 `setZoom(null)` in clearZoom: complies. Its callers are the SPEC 9.2 exits (window button, chip, time base, chart or lane resume), each a user action.
- plots.js:1321 `chart.paused`: surface "charts"; writers ruled in sweep 2b.
- api.js:49 `highRate` declaration: exempt, automatic guard, not a freeze (SPEC 9.1 "deliberately not part of this state").
- api.js:79 `highRate = on`: exempt as above. Its release rebuild is bounded by each pane's freeze (terminal.js:369-371, pinned by api_high_rate_pending).
- statusbar.js:231 session Start busy: complies. The same element survives a dialog reopen, since `openSessionDialog` does not touch it.
- statusbar.js:584 `devicesLoading = true`: complies, generation-guarded (`populateDevices` returns false to a superseded fill).
- statusbar.js:585 Attach held while loading: complies (same guard).
- statusbar.js:587 release after its own fill: complies.
- statusbar.js:593 release on close: complies. The close bumps `devicesGen`, so the late fill writes nothing.
- statusbar.js:618 declaration: no writer.
- statusbar.js:708 Attach busy during the POST: complies. The finally releases it only for the opening that sent it (`:718-722`).
- timewindow.js:46 `setZoom` definition: no writer.
- freeze.js:16 latch declaration: group state, ruled under class 25.
- freeze.js:39 latch set: class 25.
- freeze.js:57 latch end: class 25.
- freeze.js:63 `resetSurfaces`: exempt, test-only reset.
- settings.js:264 PlotJuggler Save busy: exempt, not a frozen view. A reopen's `setReadOnly(false)` (`:103`, `:769`) can re-enable it mid-save, and the config revision decides the second PUT. Reasoned only.
- settings.js:334 `setBusy` definition: no writer.
- settings.js:344 `syncHold` over the path's views: complies for the navigation hold.
- settings.js:350 `holdPath` definition: no writer.
- settings.js:394 `setBusy(btn, true)` on click: violates for the fetch path (R23-2).
- settings.js:397 provisional nav hold: complies.
- settings.js:404 exact nav hold or release: complies.
- settings.js:405 fetch-path release on the element only: part of R23-2.
- settings.js:410 a re-rendered `.db` row consults the nav hold: complies for navigation. The bundle row (`:415`) and the token fetch path have no equivalent (R23-2).
- settings.js:672 section Save busy: exempt as for `:264`, reasoned only.
- terminal.js:308 `pane.autoscroll`: surface "panes"; writers in sweep 2a.
- can.js:44 declaration: surface "CAN table".
- can.js:548 `canPaused`: surface "CAN table"; writers in sweep 2d.
- digital.js:30 declaration: surface "digital panel".
- digital.js:864 `digitalPaused`: surface "digital panel"; writers in sweep 2c.

Sweep imprecision: the command misses holds written as `.disabled = <variable>`. Improved command: add `|\.disabled\s*=\s*(on|none)\b`. It adds 5 sites:
- settings.js:103 daemon Save buttons read-only during load: exempt, an edit guard. Class 61/62 own the re-render-over-typing shape.
- settings.js:104 daemon fields read-only during load: exempt (same).
- settings.js:107 ports-table controls read-only during load: exempt (same).
- cmdbar.js:70 command input disabled with no port: exempt, reflects port presence, not a hold.
- cmdbar.js:74 marker button disabled with no port: exempt (same).

### Sweep 2: every writer of each frozen surface's contents

**2a panes.** Command, from `host/mcuscope/webui`:
`grep -n -E '\b(p|pane)\.(rows|pending|queue|frozenRows|frozenId|clearId|autoscroll)\s*(=[^=]|\+=|-=)|\b(p|pane)\.(rows|queue)\.(push|splice|unshift|length\s*=)' terminal.js api.js`
Site count: 28.

- api.js:137 `pending += 1` for a paused pane: complies, the designed backlog count.
- api.js:145 `queue.push`, live panes only (paused ones `continue` above): complies.
- api.js:146 queue trim, live only: complies.
- api.js:194 capture reset `clearId = 0`: exempt, a capture identity change clears every surface and keeps the pause (SPEC 9.1, pinned by api_capture_reset_paused_pane).
- api.js:195 capture reset drops `frozenRows`, `rows` and `pending`: exempt (same, deliberate and pinned; the old capture's rows name nothing in the new id space).
- api.js:608 clear-during-backfill raises `clearId`: complies. It only fires for a pane whose own clear already emptied it.
- api.js:791 staging floor raises `clearId`: complies (same premise).
- api.js:852 staged-row cut raises `clearId`: complies (same premise).
- terminal.js:308 the pause flag: the freeze itself.
- terminal.js:309 `pending = 0` on a pause or resume: complies.
- terminal.js:313 resume drops the snapshot: complies.
- terminal.js:317 `frozenId = state.maxId`: violates, R23-1. It covers rows still queued, which the pane never drew.
- terminal.js:321 `frozenRows` snapshot: part of R23-1. It includes those queued rows.
- terminal.js:376 rebuild selects from `frozenRows` up to `frozenId`: complies (bounded; the bound itself is R23-1).
- terminal.js:380 re-select after a dropped regex: complies (same bound).
- terminal.js:381 VIEW_MAX trim of the rebuilt rows: complies.
- terminal.js:384 `pending = 0` when live: complies.
- terminal.js:385 paused `pending` re-derived from the buffer: complies. The ring cap is documented at `:358-359`; see class 26.
- terminal.js:386 queue dropped on rebuild: complies.
- terminal.js:412 paused flush drops the queue: part of R23-1. The dropped rows are neither counted nor drawn, yet sit under `frozenId`.
- terminal.js:417 flush appends queued rows, live only: complies.
- terminal.js:418 queue cleared: complies.
- terminal.js:421 VIEW_MAX trim, live only: complies.
- terminal.js:555 history page prepend on a paused pane: exempt. It runs only on the user's own scroll to the top, and the rows are older than the pane's oldest row, never past the freeze.
- terminal.js:724 per-pane clear point: complies, a user clear. It does not resume (SPEC 9.1).
- terminal.js:725 per-pane clear empties the rows: complies (same).
- terminal.js:852 clear-all clear point: complies. It does not resume (SPEC 9.1).
- terminal.js:853 clear-all empties the rows: complies.

Presentation re-derivation on a paused pane (re-render triggers). Command: `grep -n -E '\brender\((p|pane)\b|scheduleRender\((p|pane)\)|renderEmpty\(p' terminal.js api.js`. Site count: 17. Each re-render reads the frozen `pane.rows`. What it re-derives besides is ruled per input in class 26.
- terminal.js:191 `render` definition: rows come from `pane.rows` only; complies.
- terminal.js:219 empty state from the frozen source: complies.
- terminal.js:230 `renderEmpty` definition: reads `frozenRows || buffer` bounded by `frozenId`; complies.
- terminal.js:256 1 Hz empty-state tick: complies (same).
- terminal.js:293 resize re-render: complies. Geometry only; the tick estimates it redraws are class 26 R26-1.
- terminal.js:295 `scheduleRender` definition: no writer.
- terminal.js:303 rAF render: complies (as `:191`).
- terminal.js:391 render after a rebuild: complies (as 2a).
- terminal.js:426 flush render, live only: complies.
- terminal.js:558 history render: complies (as `:555`).
- terminal.js:561 history render: complies (same).
- terminal.js:661 port-tag column change: complies. Presentation of the same rows follows the current port set.
- terminal.js:727 clear render: complies.
- terminal.js:742 scroll re-virtualize: complies. Estimates are R26-1.
- terminal.js:816 time base change: complies, a user action.
- terminal.js:855 clear-all render: complies.
- api.js:197 capture reset render: exempt (as api.js:194).

**2b charts.** Command, from `host/mcuscope/webui`:
`grep -n -E 'chart\.(xsHost|xsTick|ids|lastHost|lastTick|frozen|frozenMaxId|window|paused|prevTick)\s*(=[^=]|\+=)|chart\.(xsHost|xsTick|ids|names)\.(push|splice)|chart\.ys\.(set|get\(name\)\.push)|arr\.(push|splice)' plots.js`
Site count: 19.

- plots.js:570 `prevTick`, tick continuity state, not drawn: complies.
- plots.js:575 `lastHost`, live nudge state: complies.
- plots.js:576 `lastTick`: complies.
- plots.js:577 live x push: complies. A paused draw reads `chart.frozen` (chartDrawData, `:1098`).
- plots.js:578 live tick x push: complies (same).
- plots.js:580 live id push: complies (same; the export reads the frozen ids).
- plots.js:603 live y push: complies.
- plots.js:616 hold-last or gap push: complies (live).
- plots.js:622 block trim of the live rings: complies. The snapshot is a copy (pinned by "survives the whole ring rotating").
- plots.js:623 y trim: complies (same).
- plots.js:642 break nudges: complies (live).
- plots.js:643 break point: complies (live).
- plots.js:644 null y on a break: complies (live).
- plots.js:651 a new channel's y array: complies. The frozen view draws it as a gap, pinned by "a channel first seen while paused draws nothing".
- plots.js:652 name push: complies (a chip appears, reading `--`; same pin).
- plots.js:703 window span: complies, a user action.
- plots.js:1321 the flag: the freeze itself.
- plots.js:1328 snapshot: the freeze itself.
- plots.js:1334 export watermark: complies.

Metadata writers the command misses. Improved command: `grep -n -E 'chart\.(unit|isInt|show)\.set|chart\.show\s*=[^=]|chart\.dirty\s*=\s*true' plots.js terminal.js`. Site count: 14.
- terminal.js:819 dirty on a time base change: complies, a user action.
- plots.js:589 unit follows a redefinition: exempt. SPEC 2.5 makes the newest definition govern render metadata, for live and paused charts alike.
- plots.js:607 ad-hoc `isInt` flips on a fractional value: exempt. It changes the frozen value's formatting (`25` to `25.000`); the value and the trace stay.
- plots.js:633 dirty only when live: complies.
- plots.js:653 new channel unit: complies (as `:651`).
- plots.js:654 new channel shown: complies (as `:651`).
- plots.js:655 new channel `isInt`: complies.
- plots.js:684 expand repaints: complies. It draws from the snapshot.
- plots.js:703 window span: complies (as above).
- plots.js:785 solo: complies, a user action.
- plots.js:786 solo dirty: complies.
- plots.js:792 toggle: complies, a user action.
- plots.js:1191 resize re-decimates from the snapshot: complies.
- plots.js:1341 pause and resume dirty: complies.

**2c digital panel.** Command, from `host/mcuscope/webui`:
`grep -n -E '\b(lane|l)\.(xsHost|xsTick|vs|frozen|prevTick|pendingVal|labels)\s*(=[^=])|\blane\.(xsHost|xsTick|vs)\.(push|splice)|\bix\.(ticks|hosts|ids|frozen|trimmed)\s*(=[^=])|\bix\.(ticks|hosts|ids)\.(push|splice)|\bdigital(Last|Frozen|FrozenId|Window|Paused)\s*=[^=]|digitalLast\.(host|tick)\s*=|digitalLanes\.(set|clear|delete)|laneIds\.(set|clear)' digital.js`
Site count: 36.

- digital.js:30, 31, 34, 36, 40 declarations: no writer.
- digital.js:87 `lane.prevTick` continuity state: complies.
- digital.js:101 `pendingVal` only while live: complies.
- digital.js:107 live right edge set: complies. A paused draw uses `digitalFrozen` (`:751`).
- digital.js:109 live edge host: complies.
- digital.js:110 live edge tick: complies.
- digital.js:120 live vertex push: complies. Paused draws, readouts and the cursor go through `laneDrawData` (`:180`).
- digital.js:124 live ring trim: complies. The snapshot is a copy (pinned by "survives the whole ring rotating").
- digital.js:145 a new id index born paused gets an empty `frozen`: complies.
- digital.js:148 live index tick push: complies. The export reads `ix.frozen`.
- digital.js:149 host push: complies.
- digital.js:150 id push: complies.
- digital.js:153 index trim: complies (pinned by `digital_shown_trimmed` "a trim while paused moves neither bracket").
- digital.js:154 `trimmed` flag, live: complies. The snapshot copies it.
- digital.js:160 frozen edge: the freeze itself.
- digital.js:167 lane snapshot: the freeze itself.
- digital.js:170 index snapshot: the freeze itself.
- digital.js:174 export watermark: complies.
- digital.js:219 a lane born paused gets an empty snapshot: complies (pinned).
- digital.js:252 a new lane row appears in a paused panel, blank: complies (SPEC 9.2 "stays empty until resumed").
- digital.js:391 window span: complies, a user action.
- digital.js:864 the flag: the freeze itself.
- digital.js:868 resume drops the edge: complies.
- digital.js:869 resume drops the watermark: complies.
- digital.js:873 resume drops lane snapshots: complies.
- digital.js:874 resume readout: complies.
- digital.js:876 resume drops index snapshots: complies.
- digital.js:921 clear-all drops the frozen edge: complies. It stays paused and empty (SPEC 9.2, pinned by `digital_zoom_export`).
- digital.js:922 clear-all drops the live edge: complies.
- digital.js:923 clear-all watermark at the clear: complies (pinned).
- digital.js:924 lanes cleared: complies, a user clear.
- digital.js:925 index cleared: complies.

**2d CAN table.** Command, from `host/mcuscope/webui`:
`grep -n -E 'canRows\.(set|delete|clear)|canFrozen(Now|Id|Version)?\s*=[^=]|canPaused\s*=[^=]|\be\.(moved|ext|rtr|dlc|hex|base|lastTs|lastId|count|period|jitter|gaps)\s*(=[^=]|\+=|\|=)|tsAnchor\s*=[^=]' can.js`
Site count: 36.

- can.js:44, 45, 46, 47, 48 declarations: no writer.
- can.js:114 `tsAnchor` declaration: no writer.
- can.js:130 daemon-clock anchor: complies. Paused ages use `canFrozenNow` (`:122`).
- can.js:135 row-clock anchor: complies (same).
- can.js:151 eviction from the live map: complies. The snapshot is copies; a whole-map rotation was driven in class 26.
- can.js:160 insert into the live map: complies. A paused render reads `canModel()`.
- can.js:167 jitter on the live entry: complies. Snapshot entries are shallow copies of primitive fields.
- can.js:168 period: complies (same).
- can.js:169 gaps: complies.
- can.js:178 byte mask: complies (live entry).
- can.js:180 mask reset: complies.
- can.js:182 base payload: complies.
- can.js:184 ext, rtr, dlc and hex: complies.
- can.js:185 `lastTs`: complies.
- can.js:186 `lastId`: complies.
- can.js:187 count: complies.
- can.js:477 mask consumed only while live: complies (pinned by `can_bytediff` "keeps the highlight it froze with").
- can.js:497 same: complies.
- can.js:548 the flag: the freeze itself.
- can.js:550 frozen now: the freeze itself.
- can.js:551 snapshot: the freeze itself.
- can.js:552 frozen row-set version: the freeze itself.
- can.js:555 export watermark: complies.
- can.js:557 resume: complies.
- can.js:558 resume: complies.
- can.js:559 resume: complies.
- can.js:562 resume clears masks: complies (SPEC 9.1 "resuming lights nothing that moved while frozen").
- can.js:688 clear: complies, a user clear.
- can.js:694 a paused clear empties the snapshot and stays paused: complies (SPEC 9.1, pinned by `can_logic` "clearing a paused table").
- can.js:695 version: complies.
- can.js:696 watermark at the clear: complies.
- can.js:702 age clock reset with the rows: complies.

### Sweep 3: export and download entry points against their surface's freeze (the entry's "Also here")

Command, from `host/mcuscope`: `grep -n 'openExportDialog(\|saveBlob(' webui/*.js`. Site count: 8.

- can.js:621 `saveBlob`, the table snapshot CSV: complies. It uses `canModel()` and `canNow()`, both frozen while paused.
- can.js:656 CAN export: complies. It passes `canFrozenId` as the watermark and the shown window `sinceId` from the shown rows' `lastId`.
- terminal.js:612 pane export: complies. It passes `frozenId`, and the shown window carries `sinceId` from the first row's id (FW-4). The window is taken from the drawn `pane.rows`, so it excludes R23-1's undrawn rows.
- digital.js:497 lanes export: complies. It passes `digitalFrozenId`, and the ids come from `ix.frozen`. Time is used only past a trim, as documented.
- plots.js:1378 chart export: complies. It passes `frozenMaxId`, and `chartShownWindow` reads the snapshot's ids.
- exportdlg.js:214 dialog: complies. The watermark rides in every mode (exportrange.js:56-58).
- state.js:331 `saveBlob` definition: no surface.
- state.js:417 fetch-path save: no surface.

### Sweep 4: the CLI

Command, from `host/mcuscope`: `grep -n -i -E 'paus|frozen|freez|\bheld\b|\bhold' cli.py cli_client.py cli_output.py cli_argv.py cli_daemonctl.py`. Site count: 26.
The CLI has no paused, frozen or held view. `tail -f` prints its snapshot once and never re-derives it. Every hit is another sense of the word:
- cli_client.py:243 "hold in memory": exempt, not a surface.
- cli_daemonctl.py:3 "holds the": exempt (prose).
- cli_daemonctl.py:60 file handle: exempt.
- cli_daemonctl.py:65 file handle: exempt.
- cli_daemonctl.py:105 test holds equal: exempt.
- cli_daemonctl.py:156 env read "frozen at import": exempt, configuration.
- cli.py:191 retention prose: exempt.
- cli.py:219 port `held`, a daemon state: exempt.
- cli.py:220 same: exempt.
- cli.py:523 break duration: exempt.
- cli.py:529 break: exempt.
- cli.py:1446 "window that held no lines": exempt, a verdict.
- cli.py:1461 `--min-window`: exempt.
- cli.py:1523 EMPTY verdict: exempt.
- cli.py:1954 streamed decode: exempt.
- cli.py:2197 can follow capture watermark: exempt. A live follow with no frozen view; class 77 territory.
- cli.py:2246 same: exempt.
- cli.py:2652 child handle: exempt.
- cli.py:2698 token prose: exempt.
- cli.py:2712 port holder: exempt.
- cli.py:2888 guide text: exempt.
- cli.py:2918 guide text: exempt.
- cli.py:2967 guide text: exempt.
- cli.py:3019 guide text: exempt.
- cli_output.py:6 prose: exempt.
- cli_output.py:173 prose: exempt.

### Owed on a real browser or Windows

- Driven headless (Playwright 1.62.0 Chromium, `browser_rotation.py`, profile deleted after): R23-1 (428 to 431 lines on a paused pane).
- Nothing in this class is platform-specific, so nothing is owed on Windows.
- Still needs eyes: nothing new. The drawing of a frozen chart and frozen lanes after rotation is pinned in the stub. Only the canvas paint itself is the standing manual item (CLAUDE.md), and it is not changed by any finding here.

## Class 24. A fix that rests on one runtime version's driver behaviour

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3.

### Findings

Listed at the top of this file.

### Floor interpreters

uv provides both floor interpreters locally: `cpython-3.10.20` and `cpython-3.11.15` (`uv python list`, both already installed).
I built scratch venvs `scratch-15-17-24/venv3.10` and `venv3.11` (`uv venv --python 3.10|3.11`, `uv pip install -e ".[dev]"` from a `git archive HEAD` copy), plus a clean 3.10 venv with the built wheel.
Caveat: both bundle SQLite 3.53.1. CI's `setup-python` 3.10/3.11 link a different SQLite. So the local runs test the Python `sqlite3` module's behaviour on the floor, not the oldest SQLite library.
The 2026-08 round covered the library floor (REVIEW_LOG.md:526: window functions need 3.25; the suite passed on an `LD_PRELOAD`ed 3.37.2). CI's matrix now includes 3.10 on both OSes (`ci.yml:34`).

Driver probes, same script on five interpreters (`scratch-15-17-24/probe_iv.py`, 500 free pages, `incremental_vacuum(100)`):

| Python | SQLite | execute() only | execute().fetchall() | executescript |
|---|---|---|---|---|
| 3.10.20 | 3.53.1 | 1 page | 100 rows, 100 pages | 100 pages |
| 3.11.15 | 3.53.1 | 1 page | **0 rows, 1 page** | 100 pages |
| 3.12.3 | 3.45.1 | 1 page | 100 rows, 100 pages | 100 pages |
| 3.12.11 | 3.50.4 | 1 page | 100 rows, 100 pages | 100 pages |
| 3.13.5 | 3.47.1 | 1 page | 100 rows, 100 pages | 100 pages |

The 3.11 quirk reproduces with a new SQLite, so it belongs to the Python module, not the library, and 3.10 does not share it.
`executescript` reclaims fully on all five, which is what `_reclaim_pages` rests on.

Rollback and commit probe (3.10.20 vs 3.11.15):
- `rowcount` and `lastrowid` survive `commit()` and `rollback()` on both.
- A SELECT cursor survives `commit()` on both.
- A SELECT cursor with rows left **raises on 3.10 after `rollback()`**, with `InterfaceError: Cursor needed to be reset because of commit/rollback and can no longer be fetched from.`. On 3.11 it keeps yielding.

Test files run on the floor, one file per pytest invocation, from the scratch copy (logs `scratch-15-17-24/floor-*.log`, `floor2-*.log`); every run passed:
- 3.10 and 3.11: test_store_reclaim_budget, test_store_size_cap, test_store_schema, test_sessions, test_store_sessions, test_store_export, test_session_bundle, test_server_exports, test_server_export_pool, test_store_plot_summary, test_store_fastpaths, test_store_plot_reads, test_store_writer, test_store_writer_commits, test_store_match_budget, test_cli_start_index_build, test_store_id_sequence, test_server_purge_and_sessions, test_store_startup_trim, test_reconnect, test_serial_link_tx, test_server_ws, test_cli_transport_timeouts, test_cli_follow, test_wait_repeat, test_server_live_verdicts, test_scaffold.
- 3.10 only: test_cli (167 passed, run through the 3.10 venv's own `mcu` console script).

A PRAGMA read-back on the Store connection after `start()` gave the same on 3.10 and 3.11: foreign_keys 1, synchronous 1, journal_mode wal, auto_vacuum 2, cache_size -65536, journal_size_limit 67108864, in_transaction False.

### Sweep

Driver row consumption and transaction handling:

```
cd host; grep -n "PRAGMA\|BEGIN\|COMMIT\|ROLLBACK\|SAVEPOINT\|RELEASE \|\.commit()\|\.rollback()\|with conn:\|with self._conn:\|executescript\|rowcount\|lastrowid\|total_changes\|in_transaction" mcuscope/*.py
```

It returned **55 lines**.
Added for driver coercion, connection setup and non-sqlite version behaviour:

```
grep -n "create_function\|set_progress_handler\|check_same_thread=False)\|row_factory = \|wait_for(\|not builtin TimeoutError\|fromisoformat\|3\.1[0-3]" mcuscope/*.py
```

That gave **20 more sites** (listed after the first 55).

Verdicts, the 55:
1. `sim.py:808`: exempt, a comment about a socket `with conn:`, not sqlite.
2. `store.py:203`: exempt, docstring.
3. `store.py:212`: exempt, docstring.
4. `store.py:214`: exempt, docstring.
5. `store.py:229` `executescript(PRAGMA incremental_vacuum)`: complies. Probe table above, and test_store_reclaim_budget passes on 3.10 and 3.11.
6. `store.py:230` `commit()`: complies (executescript already committed; harmless).
7. `store.py:254` `_SLOW_COMMIT_S`: exempt, constant.
8. `store.py:258`: exempt, comment.
9. `store.py:267` `_COMMIT_INTERVAL_S`: exempt, constant.
10. `store.py:275`: exempt, arithmetic on the constant.
11. `store.py:351` `PRAGMA table_info(...).fetchall()`: complies; a column-bearing read pragma returns rows on every version (test_store_schema on the floor).
12. `store.py:371`: exempt, comment.
13. `store.py:374`: exempt, comment.
14. `store.py:384` `BEGIN IMMEDIATE`: complies. Legacy isolation opens no implicit transaction for the preceding DDL-only migrations (3.6+); the rebuild tests in test_sessions and test_store_schema pass on 3.10/3.11.
15. `store.py:399` `rollback()`: complies; every cursor in the rebuild is consumed, so 3.10's reset has nothing to reset.
16. `store.py:401` `commit()`: complies.
17. `store.py:614`: exempt, comment.
18. `store.py:616` `PRAGMA auto_vacuum=INCREMENTAL`: complies; a setter needs one step, and it is read back at `:620`.
19. `store.py:620` read-back: complies (2 on both floors).
20. `store.py:629`: exempt, comment.
21. `store.py:632` `journal_mode=WAL` fetchone: complies (`wal` on both floors).
22. `store.py:643` `synchronous=NORMAL`: complies (1 on both floors).
23. `store.py:648`: exempt, comment.
24. `store.py:649` `cache_size`: complies (-65536 on both).
25. `store.py:652` `journal_size_limit`: complies (67108864 on both).
26. `store.py:653` `foreign_keys=ON`: complies (1, and not inside a transaction, on both).
27. `store.py:662` `executescript(SCHEMA)`: complies; DDL, and it commits anything pending first on every version.
28. `store.py:664` `commit()`: complies.
29. `store.py:688` `commit()`: complies.
30. `store.py:723` `commit()`: complies.
31. `store.py:936` writer `rollback()`: complies. Every read on the loop connection is fetched whole with no await between execute and fetch, so no cursor is live at a rollback and 3.10's reset is harmless. test_store_writer and test_store_writer_commits pass on the floor.
32. `store.py:954` `commit()`: complies.
33. `store.py:962`: exempt, a timing compare.
34. `store.py:972` `rollback()`: complies, as 31.
35. `store.py:1053`: exempt, docstring (ids are taken in Python, not from `lastrowid`).
36. `store.py:1485` `commit()`: complies.
37. `store.py:1486` `lastrowid` after commit: complies (survives commit on 3.10/3.11, probed).
38. `store.py:1527` `commit()`: complies.
39. `store.py:1556` `commit()`: complies.
40. `store.py:1557` `rowcount` after commit: complies (probed).
41. `store.py:1586` `executescript(SCHEMA)` on the export copy: complies; a fresh connection, run before the ATTACH.
42. `store.py:1609` `rowcount` of `INSERT ... SELECT`: complies (`sqlite3_changes`, the same on all versions; test_store_export and test_session_bundle on the floor).
43. `store.py:1631` `commit()`: complies.
44. `store.py:1637` `rollback()` before `DETACH`: complies. On 3.10 the rollback resets statements; on 3.11+ the only SELECT (`MAX(id)`) is already at DONE from fetchone's prefetch. test_server_exports passes on both.
45. `store.py:1655` `commit()`: complies.
46. `store.py:1675` `commit()`: complies.
47. `store.py:1676` `rowcount` after commit: complies (probed).
48. `store.py:1683` `rowcount` after `_new_capture()`'s commit: complies (probed).
49. `store.py:2111` reader `cache_size`: complies.
50. `store.py:2386` `BEGIN` read snapshot: complies; the same semantics on all versions.
51. `store.py:2390` `rollback()`: complies; `_scan_plot_rows` consumes every cursor into dicts before the `finally` (test_store_plot_summary on the floor).
52. `store.py:2973` `PRAGMA page_size`: complies.
53. `store.py:2974` `PRAGMA page_count`: complies.
54. `store.py:2975` `PRAGMA freelist_count`: complies (returns its row on 3.11, probed).
55. `store.py:3119` `PRAGMA freelist_count`: complies.

Verdicts, the 20 added:
56. `store.py:603` `row_factory = sqlite3.Row`: complies; `dict(row)` and key access are the same on all versions.
57. `store.py:2109` `check_same_thread=False`: complies.
58. `store.py:2110` `row_factory`: complies.
59. `store.py:2124` `create_function(..., deterministic=True)`: complies (test_store_match_budget on the floor).
60. `store.py:2155` `create_function`: complies (same).
61. `store.py:2815` `check_same_thread=False`: complies.
62. `store.py:2816` `row_factory`: complies.
63. `server.py:2807` `set_progress_handler`: complies (test_server_exports and test_server_export_pool on the floor).
64. `server.py:2218` `wait_for`: complies (test_server_ws on the floor).
65. `server.py:2391` `wait_for`: complies (test_wait_repeat and test_server_live_verdicts on the floor).
66. `server.py:2524` `wait_for`: complies (same).
67. `serial_link.py:1235` `wait_for`: complies. `tests/test_reconnect.py:1345` accepts both the 3.11 and the 3.12+ outcome and asserts the version-independent invariant; it passes on 3.10 and 3.11.
68. `server.py:2219` `asyncio.TimeoutError`: complies (class 42 owns it; floor runs pass).
69. `server.py:2394` `asyncio.TimeoutError`: complies (same).
70. `server.py:2523` `asyncio.TimeoutError`: complies (same).
71. `serial_link.py:1236` `asyncio.TimeoutError`: complies (same).
72. `cli.py:1286` asyncio TimeoutError note: complies (test_cli_transport_timeouts on the floor).
73. `cli_output.py:323` `parse_clock`: complies; an explicit grammar replaces `fromisoformat`, whose 3.10/3.11 rules differ.
74. `protocol.py:67` `(str, Enum)` in place of `StrEnum`: complies (imports and passes on 3.10).
75. `mcuscope/__init__.py:25` version gate: exempt, the floor guard itself.

The registry's emulation rule: `tests/test_store_reclaim_budget.py:221` `OneStepPerExecute` emulates 3.11's pragma stepping on any interpreter. Complies.

### Registry sweep precision

- The entry names 3.11's pragma behaviour but no 3.10-only one. Add the probed difference: on 3.10, `rollback()` resets every cursor on the connection, and the next fetch raises `InterfaceError`; 3.11+ keeps it fetchable. Any future read that holds a cursor on a connection that can be rolled back (a paged export sharing the writer's connection, a generator over the loop connection) would fail on the floor only. Today none does (items 15, 31, 34, 44 and 51).
- The sweep text lists no grep. The two commands above are the enumeration; the second one catches the non-sqlite driver sites (asyncio `wait_for`, `fromisoformat`) that the same invariant covers.
- "Run its test on the support floor" is now routinely possible on this machine: uv has 3.10.20 and 3.11.15 installed. The scratch venv recipe above takes under a minute.

### Owed on Windows or a real browser

- Nothing class-24-specific. CI's windows py3.10 leg runs the full suite on the floor; this leg ran Linux only.

---

## The two questions (classes 15, 17 and 24)

### 1. What am I least confident about? Rechecked

- **R17-1's reach.** A relative `db_path` could have been resolved somewhere I had not read. I re-drove it end to end with the installed wheel: start from A, restart from B, two capture ids, `rel.db` in both dirs. It holds.
- **Whether the 3.11 pragma quirk is Python or SQLite.** The registry attributes it to Python, but CI links a different SQLite. I re-drove it on five interpreters. It is 3.11-module-only (it reproduces with SQLite 3.53.1), and 3.10 is not affected.
- **R15-8's threshold.** One synthetic run could have been luck. I ran 20 trials at each of 11 sizes. The silence starts near the 64 KB pipe capacity and is intermittent there, so I file it only as latent.
- **Whether 3.10's rollback reset reaches a live cursor.** This rests on reading, not driving: I read all five rollback sites and each consumes its cursors first. The floor runs of every test file touching them pass on 3.10, but those tests would not necessarily build the interleaving. I did not construct a probe that holds a cursor across the writer's rollback, because no code path does that today.
- **R17-3 is not reproduced here**: localhost resolves IPv4-only on this host. It is reasoned only.
- **R15-4 and R17-4 need Windows**: owed, not counted as done.

### 2. What should we have checked that we have not thought about?

- **Relative paths other than `db_path` (R17-1's siblings).** Reasoned only, not driven.
  - `MCUSCOPE_DATA_DIR`, `MCUSCOPE_CONFIG_DIR` and `MCUSCOPE_CACHE_DIR` are "used as given" (`dirs.py:17-22`), so a relative override has the same restart-from-another-directory shape. It would reach the default capture path, the pid record (so `mcu daemon stop` from elsewhere falls back to POST /shutdown) and the crash and startup logs.
  - The export temp dir and `<db_path>.lock` follow `db_path`, so they move with it.
- **The concurrent `DELETE /sessions/{id}?data=true` race.** Reasoned only; this belongs to class 37, and I am handing it over, not ruling it.
  - Two requests both pass `get_session`, both await `delete_range`, and both then see `active_session() is None`.
  - Both can call `start_session(auto)`. The second closes (and, with no traffic, drops) the automatic session the first just opened.
- **Other tools that act on "whatever answers on the port" (R15-1's siblings).**
  - `tools/ci_sim_smoke.py` pings the port its own child printed, so it complies.
  - `tests/support.py` stacks bind port 0 or run in process.
  - Nothing else found.
- **An unguarded skip-prone leg in the release workflow.** `release.yml`'s own `Test` step (`:98`) runs the suite without `-rs` or the run-not-skipped guards. On the tag's runner, a skipped firmware or JS suite passes. CI on the same commit carries the guards, so this is a stand-in again (the R15-3 shape). Reasoned only.

### Surprising

- Running `tools/webui_smoke.py --no-wait --port 18616` hit the class 16/18 agent's daemon (`scratch-16-18`, capture `probe.db`), because 18616 was taken. That was my error: my assigned port was 18615.
  - The harness pinged that daemon's port `b` once and attached and detached a `board2` port (`socket://127.0.0.1:<own sim>`).
  - That capture now holds those cmd/resp and sys rows at 21:39:46-21:39:51 +0900. Its port `b` was left attached and connected, which I checked read-only via `/status`.
  - That agent should know, in case it counts rows.
- 3.10 and 3.11 each have a sqlite3 quirk the other lacks (the rollback cursor reset on 3.10, the empty incremental_vacuum result on 3.11). So "the floor" is two interpreters, not one.

## Class 25. A group state that only reaches the members that already existed

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3. Probes and logs: `~/tt-data/mcuscope-2026-09-24/registry-leg/15-28/scratch-23-25-26/`.

### Findings

Listed at the top of this file.

### Sweep 1: every fan-out over a member collection (candidate group operations)

Command, from `host/mcuscope/webui`:
`grep -n -E '\b(panes|charts|digitalLanes|canRows|canModel\(\)|windowGroups|laneIds|laneGroups|surfaces|h\.views)\b' *.js | grep -E 'for \(|forEach|\.some\(|\.map\(|\.filter\(|\.find\(|\[\.\.\.' | grep -v '^\s*//'`
Site count: 69.

- freeze.js:30 `anyLive`, the pause-all label's derivation: complies. Every create and destroy re-runs it, through `freezeChanged` (terminal.js:326, plots.js:467/1492, digital.js:257/940, can.js:162/704) or the panes' `updateShared` (terminal.js:778/792).
- freeze.js:38 pause-all fan-out (G1): complies. Creates consult `bornPaused`: pane terminal.js:777 (driven, probe_d1 test 4 "born-paused pane autoscroll: false, label: resume all") and chart plots.js:466. The panel-level flags persist for lanes and CAN.
- can.js:147 eviction scan: exempt, not a group operation.
- can.js:551 the table's own freeze snapshot: exempt, not a group operation.
- can.js:562 resume clears masks: exempt (same).
- api.js:80 high-rate release (automatic, over panes): complies. A pane added during a shed is rebuilt at release like the rest (reasoned).
- api.js:125 feedPanes, delivery: exempt, not a group operation. It iterates the live array, so later panes are included.
- api.js:188 capture reset over panes: complies. A pane added during the re-seed reads clear token 0 (api.js:527-528; PD-2 `api_backfill_clear_tokens`).
- api.js:528 clear-token snapshot: complies (as above).
- api.js:607 clears during a backfill, over the live array: complies (pinned PD-2).
- api.js:617 rebuild after a backfill: exempt, delivery.
- api.js:766 noteClears over live panes: complies.
- api.js:791 staging floors: complies.
- api.js:852 staged cuts: complies.
- digital.js:131 breakLanes: exempt, data.
- digital.js:166 lane snapshot at pause: complies. A lane born later gets an empty snapshot (digital.js:219).
- digital.js:169 index snapshot: complies. An index born later gets an empty `frozen` (digital.js:144).
- digital.js:268 lane port tags (G8): complies. A new lane reads `lanePortTags` (digital.js:245).
- digital.js:269 packed-group headers (G8): complies. A new header is painted at create (digital.js:226).
- digital.js:301 colour by name (G7): complies. A new lane takes `colorFor(name)` (digital.js:212).
- digital.js:319 solo over lanes (G10): exempt. A one-shot reassignment of the current lanes; nothing records the solo and no label claims it. A lane born later is shown (see two questions).
- digital.js:320 same: exempt.
- digital.js:321 same: exempt.
- digital.js:414 export-button state from shown lanes: complies. Recomputed on add (:255), toggle (:325) and clear (:938).
- digital.js:437 shown-window scan: exempt, not a group operation.
- digital.js:466 hostAtTick: exempt.
- digital.js:471 same: exempt.
- digital.js:486 export lanes: exempt.
- digital.js:527 redraw: exempt.
- digital.js:712 repaint flag: exempt.
- digital.js:784 cursor snap: exempt.
- digital.js:790 cursor readouts: exempt.
- digital.js:838 hover reference lane: exempt.
- digital.js:872 resume: exempt, the panel's own state.
- digital.js:876 resume: exempt.
- digital.js:885 repaint: exempt.
- digital.js:911 readouts: exempt.
- terminal.js:256 empty-state tick: exempt.
- terminal.js:293 resize: exempt.
- terminal.js:333 panes `isLive`, the label: complies.
- terminal.js:334 panes `setPaused` (G1 member side): complies.
- terminal.js:406 flush: exempt.
- terminal.js:658 port options (G8): complies. A new pane gets `populatePortSelect` (:773).
- terminal.js:661 port-tag column (G8): complies. A new pane's `buildLine` reads `knownAliases`.
- terminal.js:667 close buttons from the pane count: complies. Recomputed on add (:779) and close (:793).
- terminal.js:800 persistState: exempt.
- terminal.js:816 time base (G6): complies. A new pane renders `state.timeMode`.
- terminal.js:819 time base on charts (G6): complies. A new chart draws in `state.timeMode`.
- terminal.js:830 loadState: exempt.
- terminal.js:851 clear-all (G2): violates at create, R25-1.
- terminal.js:880 CAN unfilter (G11): violates at create and destroy, R25-4.
- plots.js:481 multi-port derivation (G8): complies. Recomputed on chart create (:468), lane create (`lanesChanged`), clearAllCharts (:1491) and clearAllDigital (:939).
- plots.js:482 same: complies.
- plots.js:486 chart port tags (G8): complies. A new chart reads `multiPort` (:516 via :719).
- plots.js:647 breakCharts: exempt, data.
- plots.js:761 colour by name (G7): complies. A new chart strokes `colorFor(name)` at build.
- plots.js:974 zoom repaint (G5 member side): complies for charts that exist, and paused ones draw the zoom.
- plots.js:984 zoom drop repaint: complies.
- plots.js:1165 redraw: exempt.
- plots.js:1188 resize: exempt.
- plots.js:1291 cursor: exempt.
- plots.js:1309 cursor clear: exempt.
- plots.js:1346 charts `isLive`, the label: complies.
- plots.js:1347 charts `setPaused` (G1 member side): complies.
- plots.js:1407 fold-cue items from the charts: complies. The ResizeObserver reruns it on build and clear (reasoned).
- plots.js:1481 clearAllCharts destroy loop: complies. It drops window groups (:1483), resyncs the chrome (:1491) and the label (:1492).
- settings.js:344 hold views (G13): violates on the fetch path, a re-rendered row not consulting the hold. Filed once, as class 23 R23-2.
- chrome.js:128 shift-click window (G4): complies. A new chart takes `groupWindow()` (plots.js:459); pinned by `chrome_window_group`, 5 tests passing.
- chrome.js:159 paint every selector (G4/G5 label): the painter complies, but a selector born under the zoom is painted with the zoom while its chart is live, R25-2.

Sweep imprecision: `\bcanModel\(\)\b` never matches (no word boundary after `)`). The command also misses iteration through a local alias (`of rows`) and the per-chart channel fan-outs. Supplementary command:
`grep -n -E 'canModel\(\)|of rows\)|chart\.names\.(forEach|map|filter)|soloShow\(' *.js | grep -v '^\S*:\s*//'`
Site count: 23.

- chrome.js:228 `soloShow` definition (G10): exempt as digital.js:319.
- can.js:52 `canModel` definition: exempt.
- can.js:303 render reads the model: complies. The id filter and collapsed groups (G12) are consulted on every rebuild (can.js:329, :380, :412), so a row born later obeys both.
- can.js:340 row updates: exempt, not a group operation.
- can.js:540 age tick: exempt.
- can.js:607 shown rows: complies (as :303).
- can.js:614 snapshot CSV rows: exempt.
- api.js:299 fetched `!pd` rows: exempt, noise (not a member collection).
- api.js:592 backfill rows: exempt, noise.
- api.js:696 staged frame rows: exempt, noise.
- digital.js:320 duplicate of sweep 1: exempt.
- settings.js:607 ports-table rows: exempt, noise.
- plots.js:398 `!pd` rows of the seed: exempt, noise.
- plots.js:519 collapsed head's shown names: complies. Recomputed by `syncChartTitle` on every toggle and add.
- plots.js:741 chips render: exempt.
- plots.js:785 solo (G10): exempt as digital.js:319. A channel first seen later is born shown (plots.js:654).
- plots.js:821 chip values: exempt.
- plots.js:862 shown count: exempt.
- plots.js:1006 series build: exempt.
- plots.js:1027 single-trace axis: exempt.
- plots.js:1108 empty data: exempt.
- plots.js:1126 draw arrays: exempt.
- plots.js:1372 export names: exempt.

### Sweep 2: the group operations, each with its create and destroy sites

- G1 pause all, over panes, charts, lanes and the CAN table.
  - Create: addPane consults `bornPaused` (terminal.js:777; driven). ensureChart does too (plots.js:466; pinned by `plots_seed_paused`).
  - Lanes are born into the panel flag, with an empty snapshot (digital.js:219). CAN rows land in a table whose `canPaused` persists.
  - Destroy: closePane re-renders the label (`updateShared`, :792); clearAllCharts (:1492), clearAllDigital (:940) and clearAllCan (:704) run `freezeChanged`.
  - Label: freeze.js:50. Complies; the latch comes after the fan-out (freeze.test "sibling's freezeChanged").
- G2 clear all, over panes, charts and lanes, plus the anchors.
  - Create: addPane violates (R25-1).
  - ensureChart and addDigitalLane comply: fed only new samples, and a seed in flight is dropped by `plotSeedGen` (api.js:418).
  - Destroy: n/a.
- G3 capture reset, which clears every surface and keeps the pause.
  - Create during the re-seed: complies (clear tokens; `api_capture_reset_*`).
- G4 shift-click window span.
  - Create: ensureChart (plots.js:459). The lanes' single head keeps `digitalWindow` across a clear.
  - Destroy: `dropWindowButtons` (plots.js:1483).
  - Complies; pinned.
- G5 drag zoom.
  - Create while the latch holds: the chart is born paused and draws the zoom. Complies.
  - Create once the latch has ended: the chart is born live, but its selector is born showing the chip. R25-2.
- G6 time base: panes, charts and lanes read `state.timeMode` at draw. Complies.
- G7 colour by name: creates read `colorFor`. Complies.
- G8 port tags: charts, lanes, pane options and the pane tag column are all recomputed on create and destroy. Complies.
- G9 theme: charts and lanes stamp `theme` per member and rebuild on mismatch. Complies.
- G10 solo (per chart, and over lanes): exempt, a one-shot with no standing state or label.
- G11 CAN-click filter and unfilter: violates on create and destroy (R25-4).
- G12 CAN id filter and collapsed groups: consulted per rebuild. Complies.
- G13 the settings download hold by path: violates on the fetch path (R23-2).
- G14 high-rate guard (automatic): complies.

### Sweep 3: the CLI

Command, from `host/mcuscope`: `grep -n '_port_column\|_stream_port_column' cli.py`. Site count: 8.

- cli.py:873 `_port_column` definition: n/a.
- cli.py:881 stream branch: n/a (the definition).
- cli.py:888 `_stream_port_column` definition: n/a.
- cli.py:969 `mcu lines`, judged on the finished rows: complies.
- cli.py:997 `tail -f`, judged once for a stream: violates, R25-3.
- cli.py:1022 the snapshot's own fallback: complies (finished rows).
- cli.py:1392 `mcu wait`'s matched line, judged at print: complies.
- cli.py:1929 `log export` stream: complies, reasoned only. The daemon renders the column over a window whose rows all exist at the request.

`mcu can -f` rows carry no port at all, which the AI guide states ("not can dump's"). Exempt here: not a group label decided early. See the two questions in class-26.md.

### Owed on a real browser or Windows

- Driven headless (Playwright 1.62.0 Chromium, `browser_probe.py`, profile deleted after): R25-1 and R25-2.
- R25-3 was driven on Linux; the follow's port rule has no platform branch, so nothing is owed on Windows.
- Still needs eyes: nothing.

## Class 26. A frozen view re-derived from a ring buffer that has rotated past it

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3. Probes and logs: `~/tt-data/mcuscope-2026-09-24/registry-leg/15-28/scratch-23-25-26/`.

### Findings

Listed at the top of this file.

### Sweep 1: every frozen or held view and what backs it

Method: each surface from class 23 sweep 1, with its draw, readout, cursor and export paths read in full. Those are `render`, `buildLine`, `fmtTs`, `renderEmpty`, `scopedCount`, `countPending`, `exportPane` (terminal.js); `currentData`, `chartShownWindow`, `paintChanValues`, `buildUplot`, `xForRow` (plots.js); `laneDrawData`, `valueAt`, `setDigitalCursorAt`, `digitalShownWindow`, `hostAtTick`, `drawDigitalLane`, `digitalRightEdge` (digital.js); `canModel`, `renderCan`, `updateCanRow`, `canNow`, `canShownWindow`, `exportCan` (can.js); plus the zoom, the settings holds and the export dialog. Inputs found: 26.

- Pane rows: the shared ring (`BUFFER_MAX` 5000 + 512 slack), snapshotted as `frozenRows`. Complies.
  - Pinned: `terminal_paused_freeze` "survives the shared buffer rotating past its freeze".
  - Driven headless with `browser_rotation.py`: a pane paused at 431 rows kept 431 through 5378 later lines, including across a regex edit and its clear (`348 / 431` filtered).
- Pane tick estimate: `tickAnchors`, capped at 10000 per port. Violates (R26-1).
- Pane own tick: memoized on the row object (state.js:226-231). Complies.
- Pane rel and tick zero: the `state.anchorTs` and `anchorTick` scalars. Complies: they move only on clear-all and capture reset, and both empty the pane.
- Pane delta: the previous row in `pane.rows`. Complies (own copy).
- Pane port-tag colour: `portColor`, a memo of a deterministic hash (state.js:170-179). Complies: identical past its cap.
- Pane port-tag column: `state.knownAliases`. Exempt, presentation that follows the current port set; the rows do not move.
- Pane "N new": `countPending` over the ring. Exempt: documented as "a backlog past BUFFER_MAX reads as BUFFER_MAX" (terminal.js:358-359), and it is what resume can fold in. See question 2.
- Pane empty state and regex scope count: `frozenRows` bounded by `frozenId`. Complies.
- Pane history rows: held in `pane.rows` only, capped by `HISTORY_MAX` on growth, never rotated. Complies.
- Pane hover cursor: `tickAnchors` (R26-1, second consumer) and `tickClocks` (exempt, see sweep 2 timewindow.js:319).
- Chart samples: per-channel rings (`PLOT_CAP` 100000), snapshotted as `chart.frozen`. Complies (pinned by `plots_paused_freeze` "survives the whole ring rotating past the freeze").
- Chart export ids: `chart.frozen.ids`. Complies (pinned by `plots_shown_ids`).
- Chart stroke colour: `paletteSlots`, cleared at 512 names (chrome.js:49). Exempt: a presentation memo, with the same exposure live; the samples do not move.
- Chart unit and integer format: live metadata, unbounded. Exempt (SPEC 2.5, newest definition; see class 23 sweep 2b).
- Chart draw scale: derived from the drawn arrays each draw. Complies.
- Lane vertices: rings (`PLOT_CAP`), snapshotted as `lane.frozen`. Complies (pinned by `digital_paused_freeze`).
- Lane right edge: `digitalFrozen`, copied at pause. Complies.
- Lane id index: `laneIds` rings, snapshotted as `ix.frozen`. Complies (pinned by `digital_shown_ids` and `digital_shown_trimmed`).
- Lane labels and colour: set on the lane at birth. Complies.
- CAN rows: the LRU map (`MAX_CAN_IDS` 256), snapshotted as `canFrozen` copies. Complies.
  - Driven: `node --test scratch-23-25-26/probe_can_whole_rotation.test.mjs`. Pause, then 300 new ids evict every frozen id from the live map (positive control asserted). A filter-driven rebuild still shows `[["100","DE"],["200","AA"]]`.
  - No test in the suite rotates the whole map; this probe is the one.
- CAN ages: `canFrozenNow`. Complies.
- CAN collapsed groups: localStorage, user-set. Complies.
- Zoom range: its own object (`setZoom(z)`), never mutated. Complies.
- Settings download hold: `holds` by path, deleted when ended, never rotated. Complies (its fetch-path gap is class 23 R23-2).
- Export dialog context: `ctx` captured at open. The dialog is modal, and a capture reset while it is open is refused (exportdlg.js `openCapture`). Complies.

### Sweep 2: every bounded store (does a frozen view read it?)

Command, from `host/mcuscope/webui`: `grep -n -E '^\s*(export )?const [A-Z_]*(MAX|CAP)[A-Z_]*\s*=' *.js`. Site count: 29.

- cmdbar.js:13 `CMD_HISTORY_MAX`: exempt, command history; no frozen view reads it.
- api.js:337 `SEED_MAX_MS`: exempt, a query bound, not a store.
- api.js:440 `LINES_LIMIT_MAX`: exempt, a query clamp.
- api.js:444 `BACKFILL_MAX`: exempt, a fetch bound.
- api.js:635 `WS_RECONNECT_MAX_MS`: exempt, a timer.
- digital.js:22 `MAX_LANES`: complies. A creation cap (new lanes refused, none evicted).
- terminal.js:26 `VIEW_MAX`: complies. The live trim; a paused pane re-derives from its own snapshot.
- terminal.js:27 `MAX_PANES`: exempt, a count cap.
- terminal.js:433 `MAX_MATCH_LEN`: exempt, an input bound.
- chrome.js:43 `PALETTE_NAMES_MAX`: exempt (as the chart stroke colour above).
- state.js:27 `TOKEN_PROMPT_MAX`: exempt, a retry budget.
- state.js:129 `BUFFER_MAX`: complies (as pane rows above).
- state.js:157 `MAX_BAUD`: exempt, an input bound.
- state.js:158 `MAX_TIMEOUT_MS`: exempt, an input bound.
- state.js:159 `MAX_DB_BYTES`: exempt, an input bound.
- state.js:168 `PORT_COLOR_MAX`: complies (a deterministic memo).
- state.js:217 `MAX_DECIMAL_DIGITS`: exempt, grammar.
- state.js:311 `PLOT_CAP`: complies (charts, lanes and the lane index all snapshot).
- layout.js:12 `CAN_CAP_MIN`/`CAN_CAP_MAX`: exempt, layout percent.
- layout.js:13 `TITLE_MAX`: exempt, a title length.
- pane.js:58 `SAME_LETTER_ESCAPES`: exempt, noise ("CAP" inside "ESCAPES").
- pane.js:173 `HISTORY_MAX`: complies (history rows are held, not rotated).
- settings.js:169 `CAP_TITLE`: exempt, noise (a string).
- can.js:19 `CAN_JITTER_MAX`: exempt, a threshold.
- can.js:20 `MAX_CAN_IDS`: complies (a snapshot of copies; driven).
- plots.js:40 `MAX_CHANNELS`: complies. A creation cap.
- plots.js:1079 `AXIS_UNIT_MAX`: exempt, a display length.
- timewindow.js:275 `ANCHOR_CAP`: violates (R26-1).
- timewindow.js:319 `TICK_EPOCH_CAP`: exempt, reasoned only. Frozen chart and lane x values are computed at ingest and held in the snapshot. Only the hover projection re-reads the epochs, and it rotates after 1000 resets on one port, with the same exposure for live data.

Sweep imprecision: the name grep matches noise (`ESCAPES`, `CAP_TITLE`, thresholds). It also misses stores bounded by a slack constant, or with no MAX or CAP in their name (the staging area, `pane.queue`, the single-entry decode memos). The store that R26-1 turned on is found only because its cap happens to be named `ANCHOR_CAP`.
Improved command, which enumerates the evictions themselves: `grep -n -E 'splice\(0,|\.shift\(\)|\.delete\(|\.clear\(\)' *.js | grep -v '^\S*:\s*//'`. Site count: 31.

- chrome.js:49 palette slots cleared at the cap: exempt (as above).
- chrome.js:170 window group removed on chart destroy: exempt, not a store a view reads.
- api.js:146 live queue trim: complies (live panes only).
- api.js:187 anchors cleared on capture reset: complies. The panes are emptied in the same reset.
- cmdbar.js:190 history trim: exempt.
- freeze.js:63 test reset: exempt.
- timewindow.js:306 anchor eviction: violates (R26-1).
- timewindow.js:358 epoch eviction: exempt (as `TICK_EPOCH_CAP`).
- digital.js:124 lane ring trim: complies (snapshot).
- digital.js:153 lane index trim: complies (snapshot).
- digital.js:924 lanes cleared: complies, a user clear.
- digital.js:925 index cleared: complies.
- digital.js:926 groups cleared: complies.
- digital.js:927 clocks cleared: complies. The clear empties every chart and lane that used them.
- state.js:183 port colours cleared on reset: complies (a deterministic memo).
- state.js:292 shared ring trim: complies (`frozenRows`).
- can.js:151 LRU eviction: complies (snapshot copies; driven).
- can.js:286 collapsed-set toggle: exempt, a user action.
- can.js:688 table clear: complies, a user clear.
- terminal.js:302 render queue: exempt, scheduling.
- terminal.js:381 rebuilt rows trimmed to `VIEW_MAX`: complies. It trims the pane's own re-derivation from its snapshot, oldest first, with the same bound the pane had at pause.
- terminal.js:421 live flush trim: complies (live only).
- terminal.js:528 query parameter delete: exempt, noise.
- terminal.js:555 history prepend (splice at 0): exempt, noise (an insert).
- terminal.js:698 channel toggle: exempt, a user action.
- settings.js:346 ended hold removed: complies.
- plots.js:622 chart ring trim: complies (snapshot).
- plots.js:623 same: complies.
- plots.js:1486 charts cleared: complies, a user clear or reset.
- plots.js:1487 seed ids cleared: complies.
- plots.js:1488 channel meta cleared: complies.

### Sweep 3: the CLI

- The CLI has no frozen or held view (class 23 sweep 4: 26 hits, none a surface).
- The only held state a follow keeps is the `--changes` baseline, `LineDecoder._last` (cli_output.py:381). It is an unbounded dict, so it never rotates. Complies.

### Owed on a real browser or Windows

- Driven headless (Playwright 1.62.0 Chromium, `browser_rotation.py`, profile deleted after): a paused pane kept its rows through a full rotation of the shared ring.
- R26-1 is display logic reached only through `render`, and the stub drove it. Seeing the `~-` column in a real browser would take hours of pause at a steady clock, so it was not run.
- The lane and chart paint after a full `PLOT_CAP` rotation is pinned in the stub. Rotating 100k points in a browser takes tens of minutes at the sim's rate, so it was not run. Neither is owed by a finding.
- Nothing is owed on Windows for classes 23, 25 or 26. Nothing platform-specific is involved.

### Brief note

The leg brief (`docs/review/2026-09-23-opus55/registry-brief.md`) asks for one file at `docs/review/2026-09-23-opus55/registry-<first>-<last>.md`. The sub-brief asks for one file per class under `~/tt-data/mcuscope-2026-09-24/registry-leg/15-28/`. I followed the sub-brief, as the caller instructed; the leg's file is the caller's to assemble.

### The two questions (classes 23, 25 and 26)

**1. What am I least confident about?**
- R23-1's browser evidence depends on timing. The first headless run read 431 before and 431 after (no row was queued at the click); the second read 428 then 431. The deterministic evidence is the stub probe. I re-ran it: 10 rows at pause, 13 after the rebuild.
- R26-1's real-world timescale is reasoned from the thinning rule (about 1 anchor a second, so about 2.8 h), not measured. The probe forced unthinnable anchors. The code path is certain; the rate is not.
- R25-1's severity rests on reading clear-all as a standing group state (the class sweep names it). The code has no stated intent either way, and SPEC 9.1 says only that clear-all empties the views. I rated it MEDIUM because the resurrected lines are silent, and marked the fix an owner call.
- R25-2 needs a SPEC call. SPEC 9.2's "every window selector shows a chip" literally endorses the label I call wrong. The F-23 pin says a live chart is not on the zoom.
- Class 23 sweep 1 enumerates surfaces by the setter names I knew (`autoscroll`, `paused`, `digitalPaused`, `canPaused`, holds, disables). A freeze kept under a new name would be missed. As a cross-check I grepped `paus|frozen|freez|hold|held` over settings, statusbar, cmdbar, exportdlg, exportrange, state, timewindow, layout and theme, and ruled every non-surface hit by reading. I did not rule each of the 436 such mentions across the web UI one by one.
- Reasoned only, not driven: the hover-cursor consumer of R26-1 (plots.js:1263); settings Save re-enabled by a reopen mid-save (settings.js:264, :672); `log export` port column (cli.py:1929); the high-rate release rebuilding a pane added during a shed.

**2. What should we have checked that we have not thought about?**
- Solo (alt-click, plots.js:785 and digital.js:319) is a "show only X" over a chart's channels and over the lanes. A channel or lane born afterwards is shown, so the solo quietly stops holding. I ruled it exempt (no standing state, no label), but it is the class 25 shape without the label. Owner: should a solo persist?
- `mcu can` and `mcu can -f` text rows carry no port at all (`fmt_frame`, cli_output.py:467-475; the AI guide says "not can dump's"). With two boards, their frames interleave without attribution, the same harm as R25-3. It is documented, and outside these classes, so it is not filed.
- A paused pane after a capture reset says "Waiting for the first line" while its jump button counts the new capture's rows. `renderEmpty` sees `frozenId` 0 and `clearId` 0 (terminal.js:230-250 with api.js:194-195). Reasoned only; a class 12-style label, not filed here.
- The paused pane's "N new" is a running count that the next rebuild re-derives from the ring (terminal.js:352-359). On a reconnect after a long pause it drops from the true arrivals (for example 20000) to at most 5512. That is documented as what resume can show, but the label reads as arrivals. Owner choice; ruled exempt.
- History paging at the anchor cap: `noteTickAnchor` inserts a page's older anchors in id order and then splices the oldest, which are the ones just inserted (timewindow.js:305-306). So once a port holds 10000 anchors, a history page can leave its own tick-less lines at `~-` even on a live pane. Reasoned only; the same store as R26-1.

## Class 27. A test double gentler than the thing it stands in for

Swept at f31ecd9 (checked at start). HEAD moved to 1a1251a during the sweep. The only code change in scope is one new test in test_pidfile.py, which adds no double, and it shifts that file's lines after 62 by +19. `git diff f31ecd9 1a1251a -- host firmware tools` was checked.
Split over seven helpers (groups A, B1, B2, C1, C2, D, JS) plus my own share: support.py, conftest.py and firmware.
Each group's full verdict list is reproduced below under "Group verdict lists", with the helper's own finding ids (FA-n, FB1-n, ...). The R27 ids here map onto them.
The four MEDIUM helper findings I re-drove myself were FB1-1, FB2-1 (the `cmd --eol` part), FC2-2 and FD-2. I also re-drove FC1-1/FD-1 and F-JS-4 (M5). The rest carry the helper's own driven evidence and I have not re-run them.

### Findings

Listed at the top of this file.

### Sweep method and site counts

The registry states no mechanical enumeration for this class ("for each double"), so I built one, verbatim below. Files are under scratch-27-28/.
- `c27_ast.py patch` (every `*.setattr(`/`setattr(`/`.setitem(` call in host/tests/*.py by AST): 412 sites.
- `c27_ast.py classes` (every ClassDef in host/tests): 101.
- `c27_ast.py assign` (attribute assignments of a lambda/def/class outside monkeypatch): 29.
- `grep -nE "MockTransport|\bcanned\(|\brecorder\(|run_mcu_canned\(|record_params\(|record_requests\(|_json_body\(" host/tests/*.py`: 215.
- `grep -nE "\w+_fn\s*=|opener\s*=|open_link\s*=|open_fn|link_factory|source=" host/tests/*.py | grep -v ^support.py`: 25.
- Partition into groups (groups.txt), checked to cover all 119 test_*.py files exactly once. Per group, patch/classes/assign/canned/injected:
  - A 71/14/2/65/0
  - B1 75/13/2/26/0
  - B2 43/4/0/81/0
  - C1 81/11/4/27/9
  - C2 70/32/10/2/15
  - D 65/21/11/6/1
  - support+conftest 7/6/0/8/0
  - The sums match the totals above.
- Coverage check: `check_cover.py <group> out-<group>.md` confirms every enumerated file:line appears in that group's list.
  - A 152/152, B1 116/116, B2 128/128, C1 132/132, C2 129/129, D 104/104.
  - Shared harness: 21 of 21, listed below.
- Extras found by the helpers' own greps (SimpleNamespace, hand-built fakes, attribute pokes): A 26, B1 7, C1 several, C2 45, D 19. Each is ruled in its group's list.
- JS, with commands verbatim in the JS list:
  - dom_stub 93+12 items;
  - exportdlg_guards 23;
  - the union of globalThis assignments, function-member replacements and AST object-literal fakes: 495 lines;
  - extras: select read-backs 16, non-literal DOM replacements 15, hand-built objects in production collections 22.
- Firmware: `grep -nE "^[a-z].*\(" firmware/tests/fake_shims.c`: 33 lines (32 definitions, 1 noise).

### Registry sweep imprecise

- "For each double" names no enumeration, so a run can rule the doubles it happens to know. Replace it with the commands above: the AST patch/classes/assign lists, the canned-transport grep, the injected-callable grep, the JS union, and the fake_shims.c definitions.
- The entry does not name the canned-daemon shape explicitly: a MockTransport or recorder that answers every path, method or URL, where the test never asserts the request. That shape produced R27-5, R27-10, R27-15, R27-16 and R27-17.
  - Add: "a transport double that does not dispatch on path and method must be paired with an assertion on the request".
- Also missing: a double that ignores an argument the real call consumes (`(void)wr`, a `closest()` ignoring its selector, `_Sock` ignoring timeout). That shape is R27-1, R27-2, R27-9 and R27-11.
  - Improved grep for C: `grep -nE "\(void\)[a-z_]+;" firmware/tests/fake_shims.c` gives 2 lines (223, 253), and both are findings (R27-2, R27-1).
  - For Python doubles: an AST check for parameters the replacement function never reads (not run class-wide here; the helpers read each double).

### Owed on Windows or a real browser

- Windows:
  - R27-14 (the ctypes double's `use_last_error`/`restype`: whether the real fallback message is wrong).
  - The `_on_windows` EINVAL doubles in test_cli.py.
  - conftest's platformdirs double keeps data, config and cache apart, while Windows shares data/config and nests cache inside them. No shipped code enumerates those dirs today.
- Browser:
  - R27-8, detached-pick `<select>` behaviour.
  - R27-9, hover-to-cursor and Enter-to-save selectors.
  - R27-23, the `.side-body` divider.
  - N-JS-2, the session dialog's device select: only reachable with values the device list does not carry.

### Shared harness and firmware verdicts (my share)

- host/tests/conftest.py:41 | `platformdirs.user_{data,config,cache}_dir` -> `base/userdirs/<fn>/<app>` | complies: dirs.user_dir (dirs.py:23) is the only caller and passes one positional APP_NAME, which the lambda takes. Divergence noted, harmless today: on Windows platformdirs returns the same dir for data and config and nests cache inside it, the double keeps all three apart; no shipped code enumerates a user dir except `_sweep_export_orphans`, which matches only its own file names (server.py:2767).
- host/tests/conftest.py:135 | `_stdio._repaired_at_start = set()` | exempt because a per-test state reset, not a behavioural double
- host/tests/conftest.py:136 | `cli_output._OUT_FAILED = False` | exempt because a state reset
- host/tests/conftest.py:137 | `cli_output._JSON_MODE = False` | exempt because a state reset
- host/tests/support.py:342 (`canned`) | `cli.Client.open -> httpx.Client(transport=MockTransport(handler))` | complies: the real open (cli_client.py:124-128) is `httpx.Client(transport=self._transport)`, so only the transport differs, which is the respect under test. A MockTransport handler's exceptions reach the CLI unwrapped where a real transport raises httpx.TransportError subclasses; the tests that need a transport failure raise httpx errors themselves (per-site, groups A/B1/B2).
- host/tests/support.py:399 (`record_params`) | same seam, recording (path, params) | complies: as 342
- host/tests/support.py:495 (`record_requests`) | same seam on `cli_client.Client.open` (the same class object `cli` imports, cli.py:27) | complies: as 342
- host/tests/support.py:339, :341 | `canned` definition | complies: as 342
- host/tests/support.py:345, :364 | `recorder`: canned bodies keyed by path, recording requests; any unmatched path answers 200 `{}` and the method is never looked at | complies as a helper, with a condition: the real daemon dispatches on path and method (404/405 otherwise), so a test built on it pins the request only if it asserts `paths(seen)` or keys a body the output depends on. Each caller is ruled in its group's list (A, B1, B2, C1, D).
- host/tests/support.py:391 | `record_params` definition | complies: as 399
- host/tests/support.py:488, :496 | `record_requests` definition | complies: as 495
- host/tests/support.py:91 `SpyLink(SourceLink)` | overrides cancel_read/cancel_write to count and answer `cancellable` | complies: production ignores both return values (serial_link.py:419 in stop(), :625 in the reader's finally), so answering True without unblocking changes no control flow; SourceLink.read returns on its idle timeout, so the double is harsher than a native cancel, never gentler.
- host/tests/support.py:114 `Scripted` | a scripted source: bursts, timeouts, raised exceptions, exhausted -> SerialException("script exhausted") or idle | complies: the documented gap (feed matches on the exact write payload where the sim assembles at the newline) is bounded: the only `replies` user is test_source_link.py:82, which writes one whole line.
- host/tests/support.py:164 `SimEndpoint.open` | sim:// gets a SourceLink onto a simulator, anything else goes to the real `open_link` | complies: dispatches exactly as the shipped `--sim` opener (daemon.py:180-183). Differences: the harness passes `on_break` (records durations, raises when unplugged) where `open_sim_link` passes none (sim.py:1003, break answers True and does nothing), and it builds the sim from the Stack's args rather than `--demo`; both are observation and fixture, not the respect any test asserts on the product. Pinned by test_reconnect.py:1230.
- host/tests/support.py:206 `_Unpluggable` | a SimSource that raises SerialException("simulator went away") on feed/poll/break once the endpoint is down | complies: a dropped socket:// peer makes pyserial raise SerialException from read and from open, the same type the reader dispatches on.
- host/tests/support.py:231 `Stack` | the real app under a real uvicorn `daemon_mod.Server` in a thread, attached via SimEndpoint | complies: harness, not a double of a call; it skips daemon.main's pid claim, capture lock and signal wiring, which have their own tests (test_daemon_*.py, test_pidfile.py).
- host/tests/support.py:411 `CommitBoom` | connection proxy whose first commit raises OperationalError("disk I/O error") without committing, everything else delegated | complies: the writer rolls back after any failed commit (store.py:972-973), which covers both real outcomes (SQLite may or may not have rolled the transaction back itself on SQLITE_IOERR/FULL); rollback is delegated to the real connection.

### Firmware: firmware/tests/fake_shims.c (33 lines: 32 function definitions, 1 noise)

Command: `grep -nE "^[a-z].*\(" firmware/tests/fake_shims.c`; line 31 is noise (a static variable whose comment holds a parenthesis). test_monitor.c and test_families.c define test functions and registered application commands only, no shim stand-ins (families builds use the weak defaults in monitor_cmds.c). Each definition compared with the monitor.h port contract (lines 72-186), INTEGRATION.md and the call site in monitor.c / monitor_cmds.c.

- fake_reset, fake_can_stat_set_mode, fake_spi_set_mode, fake_info_set_mode, fake_i2c_set_all_ack, fake_i2c_set_short_read, fake_tx_reset, fake_feed, fake_feed_raw, fake_tx_set_reject, fake_tx, fake_set_tick, fake_uart_read_over_config, fake_can_reset, fake_can_set_partial_fill, fake_can_push, fake_can_last_tx, fake_can_last_filter_bus | test controls and observers, not stand-ins for a shim | exempt because harness plumbing
- fake_uart_read | returns min(avail, max) | complies: monitor.c:1173 reads into a 64-byte stage once per poll, so any line over 64 bytes already crosses reads and polls; the over-reporting mode is fake_uart_read_over
- fake_uart_write | whole line or reject (full-ring mode) | complies: the contract is an atomic whole-line push (monitor.h:76-77)
- fake_tick_ms | settable counter | complies (a knob; wrap is not modelled, and no wrap arithmetic is asserted by a test here)
- fake_uart_read_over | over-reporting read (avail instead of copied, SIZE_MAX) | complies: the documented hostile mode
- mon_can_tx | records the frame, always returns 0 | complies: the frame is asserted field by field (test_monitor.c:180-192, 376-389); no error mode, but the return passes straight through (monitor_cmds.c:177)
- mon_can_rx_pop | full copy, or partial-fill mode without pre-zero | complies: models both contract shapes (monitor.h:153-157)
- mon_can_filter | records the bus only; `(void)id; (void)mask; (void)ext;`, and never drops a frame | VIOLATES (R27-2): the id, mask and ext the monitor programs into a hardware filter are unobserved
- mon_can_stat | per-bus counts, or NULL-state mode | complies
- mon_i2c_xfer | ACK/NACK by address; read data fixed per address; `(void)wr;` | VIOLATES (R27-1): the write half (bytes and length) of `i2c wr` and `i2c wrrd` is never observed, contrary to the fake's own comment ("returns A0 A1 ... from write offset")
- mon_spi_xfer | MISO = MOSI inverted, cs "imu" only, nosup and short-fill modes | complies: the transmitted bytes are observable in the response
- mon_info_extra | nosup, tokens, fill-every-byte modes | complies
- mon_gpio_set / mon_gpio_get | one "led" line with state | complies: set is observed through get
- mon_adc_read | vref, neg (mv n/a), min (INT32_MIN raw) | complies

### Group verdict lists (helpers' files, headings demoted two levels)

#### Group A (scratch-27-28/out-A.md)
#### Class 27 sweep, group A

Pinned HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3 verified at start.
During the run HEAD moved to 3f94c23 (41cff9a, 3f94c23: docs/review files only; `git diff --stat f31ecd9 3f94c23` touches no code or test), so every ruling holds for both.
Mutants ran in `copy-A/` only; the copy was restored and `diff -rq` against the repo came back clean.
Files: test_cli.py, test_cli_attach.py, test_cli_can_dump.py, test_cli_closed_output.py, test_cli_closed_pipe.py, test_cli_closed_stdio.py, test_cli_contract.py, test_cli_argv.py, test_assert.py, test_capture_lock.py, test_break.py.

##### Findings

###### FA-1 LOW: test_cli_contract.py:118, the `_iter_pages_asc` double is never reached
- Test: `test_log_export_removes_a_partial_file_when_the_daemon_dies`.
- The concrete failure: `log export --limit 0 -o F` has `paged = bool(0 or ...) = False` (cli.py log_export), so it takes the streamed `/lines/export` path.
  - At the unreachable url that path dies with exit 3 before `_OutFile` opens anything.
  - So `rc == 3` and "no file" both hold whatever the paged path's `out.discard()` does. The test's subject is never run.
- Driven (A): removing `out.discard()` in the paged `finally` (cli.py:1985). Command: `pytest tests/test_cli_contract.py::test_log_export_removes_a_partial_file_when_the_daemon_dies -p no:randomly`. Result: 1 passed.
- Driven (B): making the fake `pages` raise `AssertionError("PAGES WAS CALLED")` on entry. Result: 1 passed, so the double is never called.
- Mitigation: `test_cli_read_scope.py::test_a_paged_export_that_dies_mid_walk_leaves_no_file` fails on mutant A (driven), so the guard itself is covered.
  - `test_cli_export.py` (35) and `test_cli_export_files.py` (33) both pass on mutant A.
  - Fix: add `--decode` (or `--names`) so the paged path is taken, or delete the test as redundant.

###### FA-2 LOW: test_cli.py:2592, `_ScriptedWS` delivers Ctrl-C in a way asyncio never does on 3.11+
- Test: `test_ctrl_c_ends_a_follow_with_success`.
- The double raises `KeyboardInterrupt` from `recv()` inside the coroutine.
- A real SIGINT under `asyncio.run` (3.11+ Runner) cancels the main task. The coroutine sees `CancelledError`, and `KeyboardInterrupt` is raised only out of `asyncio.run`.
- The concrete failure: moving the `except KeyboardInterrupt: raise typer.Exit(0)` arm from around `asyncio.run(run())` into `run()`'s handler list is a plausible refactor.
  - The test still passes on it, while a real Ctrl-C then exits 1 "interrupted" instead of 0.
- Driven: with that mutant, `pytest tests/test_cli.py::test_ctrl_c_ends_a_follow_with_success` gives 1 passed.
  - `copy-A/sigint_probe.py` sends a real `os.kill(SIGINT)` into `cli.main(["tail","-n","0","-f"])` over a socket whose recv blocks.
  - Result: original `RC=0`; mutant prints `interrupted`, `RC=1`.
- Fix: drive the interrupt through cancellation. Either the probe's real-signal shape, or a recv that blocks while the test cancels the task and raises `KeyboardInterrupt` from outside.

###### FA-3 LOW: test_cli.py:1776, `_StoppableDaemon` answers every POST path as a shutdown
- Sites: 1886 and 1911 (4 cases: `test_daemon_stop_falls_back_to_the_api_when_no_record_exists`, `test_daemon_stop_asks_status_before_giving_up_on_a_corrupt_record[3 params]`).
- `do_POST` replies `{"ok": true}` and stops the server for any path. `do_GET` answers the status body for any path.
- The real daemon serves `POST /shutdown` only, and 404s anything else.
- The concrete failure: the CLI posting to the wrong route passes both tests.
- Driven: mutant `probe("POST", "/shutdownX")` in cli_daemonctl.py `_request_shutdown`. Result: 4 passed.
  - Positive control: a `raise SystemExit(99)` mutant at the same line fails all 4, so the child runs the copy.
  - Only `test_status_ppid_serial.py::test_a_daemon_behind_a_launcher_shim_starts_and_stops` caught the mutant.
  - `test_cli_daemon_stop_scope.py` (20 passed) and `test_cli_ux.py` (27 passed) did not.
- Also a class 63 shape, documented as deliberate: no `pid` plus a working `/shutdown` is a hybrid.
  - Pre-0.1.2 daemons have no `/shutdown`, and current ones always send `pid`. The stub drops `pid` so that a fallback kill cannot hit pytest.
- Fix: dispatch `do_POST` on `self.path == "/shutdown"` and 404 anything else.

###### FA-4 LOW: test_cli_attach.py:129, detach miss canned as 404 where the daemon answers 400
- Test: `test_a_real_miss_still_reports_the_daemons_no_such_port` (the positive control for FC-9).
- The daemon answers `DELETE /ports/<unknown>` with `_bad_request` (400, server.py:1174).
- The canned 404 sends `Client.fail` down its 404 branch, which runs an extra `/status` probe. Production never takes that branch for a miss.
- The concrete failure: a regression that loses the daemon's wording on a 400 passes this test.
- Driven: mutant in `cli_client.fail`: `if resp.status_code == 400: msg = "bad request"`.
  - At 404 the test passes. With the canned status changed to 400 it fails.
  - Unmutated code at 400 passes, so the fix is safe.
  - The live detach-miss test (test_cli.py:1259) asserts only `returncode == 1`.
- Fix: canned status 400.

###### FA-5 LOW: test_cli_contract.py:244, the "older daemon" wait test passes the version gate with a body no daemon sends
- Test: `test_wait_repeat_survives_a_daemon_without_the_send_counters`.
- `_canned` answers every path with the wait body. So the `--repeat-ms` gate's `GET /status` sees no `version`, and `is_newer` lets it through.
- `--repeat-ms` and `sends` both landed in d888057, first tagged v0.4.0, which equals `DAEMON_MIN_VERSION`.
- A real older daemon is refused at the gate. The docstring's scenario therefore never reaches the render path this test drives.
  - The only real route to a missing `sends` is an unorderable (dev) version.
- Driven: `copy-A/wait_gate_probe.py`.
  - The test's canned `/status` gives `rc=2 paths=['/status', '/wait']`.
  - A real `0.3.0` `/status` gives `rc=1 paths=['/status']` with "daemon 0.3.0 ignores --repeat-ms".
- It tests a different subject than it states (class 63 overlap). The missing-`sends` robustness is still exercised.
- Fix: answer `/status` with an unorderable version, e.g. `"0.4.0.dev1+local"`, and say that in the docstring.

###### FA-6 LOW: test_cli.py:409-410, `_DeadPipe.fileno()` returns the runner's real fd 1
- Test: `test_windows_einval_while_rich_renders_help_is_success`, both params.
- This is a process-state leak (class 32 face), not a false green.
- The double stands in for a child's redirected pipe but answers fd 1 of the pytest process, so every "silence" path repoints the runner's stdout.
- Driven with a `-p dup2probe` plugin (`copy-A/host/dup2probe.py`, log in `copy-A/dup2probe.log`) over every `_DeadPipe` test (`-k "einval or stream_wrapper or rich_renders"`). Only this test dup2s onto fd 1, three ways:
  - `[True-0]`: rich `console.py:2041 on_broken_pipe`.
  - `[False-1]`: `cli_output._silence_stdout` via `_stdout_unwritable`. The test patches `cli._silence_stdout` only, not the `cli_output` copy.
  - `cli_output._silence_stderr` via `err_write`, because the stderr `_DeadPipe` also answers fd 1.
- Under `pytest -s` both params make the session's own summary vanish (empty output file). A control test under `-s` prints normally.
  - Under default fd capture pytest restores fd 1; the 2- and 4-test runs I made were unaffected.
- `_FullStdout` (test_cli_contract.py:29) already states the rule this breaks: "No fileno(): _silence_stdout must not repoint the test runner's own descriptor".
- Fix: have `fileno()` return an fd the test owns, e.g. `os.open(os.devnull, os.O_WRONLY)` closed at teardown.

###### FA-7 LOW (not class 27: missing positive control): test_assert.py:644
- Test: `test_sweep_tick_survives_a_failing_sweep`.
- The `boom` double is faithful, but nothing asserts it ran. On an empty store `sweep_tick` returns 0 whether or not the failing sweep was reached.
- Driven: mutant `store.py:3009` `trimmed = 0` (the size sweep is never called). Result: 1 passed.
- Fix: record the call in `boom` and assert it.

##### Per-enumeration counts (filter: the A= regex from groups.txt)

- c27-patch.txt: 71. A plain grep `setattr\(|setitem\(` over the same files also gives 71.
- c27-classes.txt: 14
- c27-assign.txt: 2
- c27-canned.txt: 65
- c27-injected.txt: 0
- Extra sites found. Grep used: `grep -nE "_canned\(|_frames_daemon\(|_sliding_can_daemon\(|_serve_http\(|SimpleNamespace|ASGITransport|TestClient\(|routes="`, plus nested defs passed positionally (`grep -nE "^\s{4,}(async )?def "`) and child-script source strings.
  - 26 double sites, listed at the end, plus two real-object families ruled exempt.

##### Rulings

###### c27-patch.txt (71)

- test_assert.py:721 | Store.subscribe with maxsize forced to 4 | exempt because a knob: the queue depth is shrunk so shedding is reachable. Real `subscribe(port_filter, maxsize, as_json)`. The lambda drops `as_json`, and no /ws subscriber runs in this test. The subject (reporting shed rows) is unchanged.
- test_assert.py:752 | same Store.subscribe knob | exempt, same reason.
- test_assert.py:765 | server._search_batch wrapped to flood the feed once, then call the real one | complies. The real scan result is kept, and the flood is the stimulus. Note: `_flood` calls `store._broadcast` from the live-match executor thread, where the real writer broadcasts on the loop. asyncio.Queue ops off-loop can only lose counts (a flake), not fabricate them, and the assertion compares against the spy's own reading. See Surprising.
- test_assert.py:778 | CaptureWatch.dropped_total spy (calls the real one, records the value) | complies: real behaviour, recording added.
- test_capture_lock.py:158 | os.open tracker (calls real os.open, records the fd) | complies. lockfile.py:124 uses os.open, so the tracker sees the lock fd.
- test_capture_lock.py:159 | os.close tracker (calls real os.close) | complies.
- test_capture_lock.py:182 | platformdirs.user_data_dir, one-positional lambda | complies. `dirs.user_dir` calls it with exactly `APP_NAME` and the conftest clears MCUSCOPE_DATA_DIR, so the patch is live, which the lock-refusal test proves.
- test_capture_lock.py:185 | mcuscope._stdio._report_key reset to "" | exempt because state restoration (class 32 hygiene), not a behavioural double.
- test_capture_lock.py:187 | daemon._serve replaced with a recorder | complies. Real `_serve` blocks in uvicorn then returns None, and `main` releases the lock afterwards. The subject is whether main reached serve with the lock handled; bind and lifespan are outside it.
- test_cli.py:241 | _stdio.PIPE_CLOSE_IS_EINVAL | exempt because a knob (platform flag selecting the Windows spelling, the respect under test).
- test_cli.py:255 | sys.stdout = _DeadPipe (Windows closed pipe: write and flush raise EINVAL) | complies for this test (no silence path runs).
- test_cli.py:270 | sys.stdout = _Denied (write raises EACCES) | complies: a real non-pipe OSError.
- test_cli.py:281 | sys.stdout = _Console (a _DeadPipe that isatty) | complies: a console stream.
- test_cli.py:287 | sys.stdout = _DeadPipe (POSIX leg) | complies.
- test_cli.py:320 | cli._silence_stdout no-op | complies. The real one's only effect is a dup2 on the process fd, which is invisible in-process. `_FlushFailsStdout` (a StringIO) has no fileno, so the unpatched cli_output copy is suppressed.
- test_cli.py:321 | sys.stdout = _FlushFailsStdout (buffered output whose flush fails) | complies: the small-output case of a real dead pipe.
- test_cli.py:337 | cli._silence_stdout no-op | complies.
- test_cli.py:342 | cli.app raising EINVAL | complies: fault injection at the app boundary, the respect under test.
- test_cli.py:349 | cli.app raising BrokenPipeError | complies, same.
- test_cli.py:360 | cli_output._silence_stdout no-op | complies. `emit_stream` resolves the cli_output name, so the patch is on the path used.
- test_cli.py:361 | sys.stdout = _DeadPipe | complies (the silence path is patched here).
- test_cli.py:384 | cli_output._silence_stdout no-op | complies.
- test_cli.py:386 | websockets.connect -> _ScriptedWS([row]) | complies. The URL is not dispatched on and not asserted; the subject is the write failure.
- test_cli.py:389 | sys.stdout = _DeadPipe | complies.
- test_cli.py:408 | cli._silence_stdout no-op | violates (FA-6): leaves `cli_output._silence_stdout` and `_silence_stderr` live against a fileno of 1.
- test_cli.py:409 | sys.stdout = _DeadPipe | violates (FA-6).
- test_cli.py:410 | sys.stderr = _DeadPipe | violates (FA-6).
- test_cli.py:701 | cli.Client.open -> _FakeHttp (stream yields chunks; `request` and `.text` raise) | complies. Real `stream_text` checks status then `iter_text`, and the fake models exactly that. `plot export --names X -o-less` makes one request (`_clock_bounds` gates nothing here), so the path is not dispatched on and not needed.
- test_cli.py:917 | cli.Client.open over MockTransport (run_mcu_canned helper) | complies: a fresh httpx.Client per call, as the real `open`. Rulings go to each handler at its call site.
- test_cli.py:1445 | websockets.connect -> _FakeWs (records connect order) | complies. `order` would record any extra non-/ports request as another "snapshot", so the count is checked.
- test_cli.py:1470 | websockets.connect raising AssertionError | complies: a tripwire, loud if reached.
- test_cli.py:2035 | cli_output._JSON_MODE True | complies: the state `_dispatch` sets for `mcu --json tail -f`, which is why the docstring patches it.
- test_cli.py:2047 | websockets.connect -> _ScriptedWS(bad frames) | complies. Malformed frames are the subject; the bare-object frame is what an older daemon sends.
- test_cli.py:2076 | cli_output._JSON_MODE True | complies, same as 2035.
- test_cli.py:2083 | websockets.connect -> _ScriptedWS(control objects) | complies. `{"capture"}` and `{"gap"}` without an id are real SPEC 3.4 shapes.
- test_cli.py:2123 | time.sleep no-op | exempt because a knob (skips poll waits; give-up is not reached in two failures).
- test_cli.py:2126 | client.get (priming) -> {"frames": []} | complies. `_dump_follow` uses `get` only for the priming read; an empty capture is a real state.
- test_cli.py:2154 | time.sleep advances a fake clock | complies: sleeping advances monotonic, as it does for real.
- test_cli.py:2155 | time.monotonic reads the fake clock | complies.
- test_cli.py:2158 | client.get priming -> empty | complies.
- test_cli.py:2190 | fake-clock sleep | complies.
- test_cli.py:2191 | fake-clock monotonic | complies.
- test_cli.py:2194 | client.get priming -> empty | complies.
- test_cli.py:2218 | time.sleep no-op | exempt because a knob.
- test_cli.py:2226 | client.get priming -> empty | complies.
- test_cli.py:2264 | time.sleep no-op | exempt because a knob.
- test_cli.py:2367 | cli.app raising TypeError | complies: fault injection, the subject.
- test_cli.py:2592 | websockets.connect -> _ScriptedWS([row, KeyboardInterrupt()]) | violates (FA-2).
- test_cli.py:2658 | typer.main.get_command raising RuntimeError | complies: fault injection, the subject.
- test_cli.py:2824 | time.sleep no-op | exempt because a knob.
- test_cli.py:2306 | websockets.connect -> refusing(exc), raising real websockets exception types (ConnectionClosedError 1008/1013, InvalidStatus 403/502) | complies. The server really produces these: 1008 close after accept (server.py:2196), 1013 subscriber cap (2205), and a pre-accept close that reaches the client as HTTP 403. Raised at connect() rather than at the first recv(); the inner `except BaseException` re-raises into the same outer handler.
- test_cli_can_dump.py:36 | cli._dump_follow no-op | complies. The follow loop is outside the subject (that --from with -f is not refused before it).
- test_cli_can_dump.py:136 | time.sleep no-op | exempt because a knob.
- test_cli_can_dump.py:171 | cli.FOLLOW_GIVE_UP_S 0.2 | exempt because a knob.
- test_cli_can_dump.py:172 | cli.FOLLOW_POLL_S 0.01 | exempt because a knob.
- test_cli_can_dump.py:183 | cli.FOLLOW_GIVE_UP_S 0.2 | exempt because a knob.
- test_cli_can_dump.py:184 | cli.FOLLOW_POLL_S 0.01 | exempt because a knob.
- test_cli_can_dump.py:218 | cli.Client.open -> _frames_daemon | complies. It mirrors store.query_can_frames: newest first, limit default 100 capped at 1000, `truncated` = more than the cap, `since_id` exclusive, `id_to` inclusive. It omits `port` and uses int ext/rtr; the tests print only `line_id` under --json. `honour_id_to=False` models a pre-id_to daemon.
- test_cli_closed_stdio.py:71 | sys.stdin = StringIO (a piped stdin) | complies: not a tty, as a pipe.
- test_cli_closed_stdio.py:86 | sys.stdin = StringIO("y\n") | complies: the answer a terminal user types.
- test_cli_closed_stdio.py:87 | cli_output._stdin_is_interactive -> True | complies: the terminal half; its StringIO pair supplies the typed answer.
- test_cli_contract.py:51 | cli.Client.open over a JSON-200 transport (`_canned` helper) | complies as a helper; ruled per call site below.
- test_cli_contract.py:59 | sys.stdout = _FullStdout (write raises ENOSPC, no fileno) | complies. A real full-disk stream raises at flush (buffered), where this raises at write; both reach `_GuardedStdout`'s OSError arm. The real /dev/full is driven in test_cli_closed_output.
- test_cli_contract.py:71 | sys.stdout = _FullStdout | complies.
- test_cli_contract.py:84 | sys.stdout = _FullStdout (against the live stack) | complies.
- test_cli_contract.py:99 | Client.get raising KeyboardInterrupt | complies: a Ctrl-C landing in a sync request.
- test_cli_contract.py:118 | cli._iter_pages_asc -> pages then die(3) | violates (FA-1): never called.
- test_cli_contract.py:279 | cli.Client.open -> text-200 transport | complies. The /ports probe gets text, reads as absent, and takes the streamed path. The export request succeeds, so the open then fails on the 444 file, which is the subject.
- test_cli_contract.py:446 | Client.get raising exc (Abort, KeyboardInterrupt) | complies: fault injection at the command boundary.
- test_cli_contract.py:474 | cli._split_global_opts -> (["--json"], ["status"]) | complies: the result the real hoist gives for this argv (pinned by test_hoisting_is_a_pure_rewrite).
- test_cli_contract.py:475 | cli.app raising KeyboardInterrupt | complies: fault injection, the subject.

###### c27-classes.txt (14)

- test_cli.py:217 | _DeadPipe, a redirected stdout whose reader left (Windows EINVAL) | complies as a stream (write and flush raise EINVAL; isatty False, as a pipe). Violates where a silence path runs (FA-6): `fileno()` answers the runner's fd 1.
- test_cli.py:230 | _FlushFailsStdout, a buffered stdout whose flush fails | complies.
- test_cli.py:1388 | _FakeWs, a /ws with frames pushed mid-snapshot, then ConnectionClosedOK | complies. Real recv returns text frames and raises ConnectionClosedOK on a clean close; the test's exit 3 matches the real "stream closed by daemon".
- test_cli.py:1776 | _StoppableDaemon, an HTTP /status + /shutdown stand-in | violates (FA-3): answers every path and method.
- test_cli.py:1991 | _ScriptedWS, a /ws replaying frames then ConnectionClosedOK | complies for text frames. Violates for exception frames carrying KeyboardInterrupt (FA-2, site 2592).
- test_cli.py:266 | _Denied, a stdout raising EACCES | complies.
- test_cli.py:277 | _Console, a tty _DeadPipe | complies.
- test_cli.py:670 | _Resp, a streamed httpx.Response (status 200, iter_text; `.text` raises) | complies.
- test_cli.py:680 | _Stream, the context manager from http.stream | complies.
- test_cli.py:687 | _FakeHttp, httpx.Client (stream only; request raises) | complies (see 701).
- test_cli.py:1494 | _ClosableWs, a recv pending until the socket closes, then ConnectionClosedOK | complies. It models the teardown resolving a pending recv, and has a positive control.
- test_cli.py:1846 | _Died, a Popen whose child exited (poll 3) | complies with Popen.poll.
- test_cli.py:1852 | _Unresponsive, a Popen alive until terminate; wait returns 0 | complies. `_abandon_daemon` terminates then waits, so this is "stoppable, never answered HTTP". No real pid is signalled (terminate is the double's).
- test_cli_contract.py:29 | _FullStdout, a full-disk stdout | complies (see 59).

###### c27-assign.txt (2)

- test_assert.py:644 | store._sweep_size_async raising RuntimeError | complies as a double (`sweep_tick` guards `Exception`; the real failure is sqlite3/OSError, and the same arm catches it). No positive control that it ran: FA-7.
- test_assert.py:667 | store._sweep_retention_async spy returning 0 | complies. It asserts cadence only, with a positive control (`len(calls) == 1`).

###### c27-canned.txt (65)

`status` makes exactly one request (GET /status, cli.py:149), so an every-path body is equivalent there.

- test_cli_can_dump.py:19 | recorder, can_frames {"frames": []} | complies. `seen[0]` must carry `session`, and any other path would get `{}` and fail `_list_field`.
- test_cli_can_dump.py:35 | recorder for --from -f; /status answers STATUS 0.4.0 through the gate | complies.
- test_cli_can_dump.py:78 | record_params(_sliding_can_daemon) | complies. It models the daemon's per-request `last_ms` floor, `since_ts`, `id_to`, the cap and newest-first order. The CSV header is not the daemon's; the CSV test asserts params only.
- test_cli_can_dump.py:135 | canned handler: /status + frames, KeyboardInterrupt on the first live poll | complies. It asserts `session` on every frames request.
- test_cli_can_dump.py:149 | recorder can_frames as CSV text | complies (content-type text/plain vs text/csv is not read).
- test_cli_can_dump.py:173 | record_requests(_failing_poll 500 JSON) | complies. A JSON 500 is producible (server.py:574-579).
- test_cli_can_dump.py:189 | record_requests(_failing_poll ConnectError) | complies.
- test_cli_can_dump.py:219 | MockTransport(_frames_daemon handler) | complies (see patch 218).
- test_cli_contract.py:50 | MockTransport in `_canned` | helper; ruled per call site (extras).
- test_cli_contract.py:278 | MockTransport text "a line\n" | complies (see patch 279).
- test_cli_contract.py:316 | canned 503 "daemon is shutting down; the wait was cut short" | complies: the daemon's own message (server.py:2398).
- test_cli_contract.py:325 | canned 400 "bad regex" | complies.
- test_cli_contract.py:358 | canned 503 "too many subscribers (max 256)" | complies: store.py's format.
- test_cli_contract.py:368 | canned 503 proxy text | complies: a proxy page is the subject.
- test_cli_contract.py:429 | 200 {"ok": true} for purge --all -y | complies: a deliberately short body (no `deleted`) for the KeyError arm.
- test_cli_contract.py:488 | handler raising UnicodeEncodeError | complies. Raised at the transport rather than while httpx encodes; both are inside `_daemon_errors`.
- test_cli_contract.py:503 | 200 {} for cmd/wait/assert with an overflowing timeout | complies. The assertion names the client refusal ("300000 ms"), so the body is never reached.
- test_cli_contract.py:534 | 200 wrongly-typed fields | complies: deliberate skew shapes. `'port'` vs `'ports'` is distinct in the asserted text.
- test_cli_attach.py:30 | _ports_then_ok(malformed /ports), dispatching on method | complies: malformed probe bodies are the subject.
- test_cli_attach.py:37 | _ports_then_ok({"ports": [{"alias", ...before}]}) | complies. The fixtures match `SerialPort.status()`: device = resolved device, or the serial until connected; `serial_number` is set only for a serial bind (serial_link.py:1287-1293).
- test_cli_attach.py:71 | recorder ports=ATTACHED (GET and POST /ports get the same body) | complies. `seen[-1].content` parses as the POST body; a GET has none.
- test_cli_attach.py:80 | same | complies.
- test_cli_attach.py:108 | same | complies.
- test_cli_attach.py:117 | record_params 404 "no such port: a", refused before any request | complies: `seen == []` is asserted.
- test_cli_attach.py:129 | record_params 404 "no such port: ab" | violates (FA-4).
- test_cli_attach.py:150 | recorder ports connected False | complies.
- test_cli_attach.py:159 | record_requests 400 "no such port" | complies: the daemon's real status; it asserts the raw path.
- test_cli_attach.py:168 | record_requests 404, refused before any request | complies: `seen == []`.
- test_cli.py:905 | run_mcu_canned definition | helper; ruled per call site.
- test_cli.py:916 | MockTransport in run_mcu_canned | helper, as 917.
- test_cli.py:923 | _json_body (every request answers `body`) | helper; ruled per call site.
- test_cli.py:949 | {"hello": 1} to daemon status | complies: a stray JSON service is the subject.
- test_cli.py:957 | 501 HTML to daemon status | complies: a proxy is the subject.
- test_cli.py:1446 | tail -f handler (/ports probe, else snapshot plus a ws push) | complies.
- test_cli.py:1471 | plain tail, lines body for any path | complies: `len(calls) == 1` pins the request count.
- test_cli.py:2125 | Client(transport) for the failed-poll follow; /status {"capture": "A"} | complies (`_capture_token` reads only `capture`).
- test_cli.py:2157 | Client(transport) always ConnectError, clock +10 s | complies: models the connect timeout.
- test_cli.py:2193 | Client(transport) answering undecodable frames to every path, /status included | complies. /status reading as token None is harmless: frames are non-empty, so no token check runs.
- test_cli.py:2222 | Client(transport) 400 to every path | complies. The `/status` probe reads None; the subject is the poll's 4xx.
- test_cli.py:2266 | Client(transport) dispatching on /status and since_id | complies.
- test_cli.py:2318 | _json_body({"lines": null}) | complies: deliberate skew.
- test_cli.py:2354 | _json_body({key: null}) per command | complies: asserts `repr(key)`, so the failing field is identified.
- test_cli.py:2377 | _json_body(status, writer_alive False) | complies.
- test_cli.py:2381 | _json_body(status, writer_alive True) | complies.
- test_cli.py:2389 | 422 to plot export -o | complies: one request, /plot/export, so the refusal lands on the stream after the defect's open point.
- test_cli.py:2408 | `refuse` raising AssertionError in the transport | complies: not caught by `_daemon_errors` or `_dispatch`, so loud.
- test_cli.py:2410 | _json_body({"status": "ok", "data": ""}) for can tx --rtr 8 | complies.
- test_cli.py:2411 | same call as 2410 | complies.
- test_cli.py:2467 | dies_midway (/sessions, then a download raising ReadError) | complies: asserts the download path.
- test_cli.py:2477 | _json_body(status, uptime null) | complies.
- test_cli.py:2478 | same, daemon status | complies.
- test_cli.py:2480 | same, status | complies.
- test_cli.py:2495 | _json_body(status +/- write_errors) | complies.
- test_cli.py:2516 | _json_body(status +/- update) | complies.
- test_cli.py:2608 | status body missing `ports` | complies: deliberate skew.
- test_cli.py:2609 | same | complies.
- test_cli.py:2628 | Client(transport) text-200 for stream_text with a failing sink | complies.
- test_cli.py:2691 | 500 JSON to --json status | complies (a JSON 500 is producible).
- test_cli.py:2781 | _json_body({"lines": ["x"]}) | complies: deliberate skew.
- test_cli.py:2786 | status with ports ["p"] | complies: deliberate skew.
- test_cli.py:2787 | same | complies.
- test_cli.py:2826 | Client(transport) whose first /status raises, then capture B | complies.
- test_cli_closed_output.py:34 | child-script Client.open over a path route table, 404 when unlisted | complies. It dispatches on path. The can-follow route re-serves frame 5 to `since_id=5`, where the real daemon answers []. Driven harmless: mutant backfill `out_json(fr)` in can_dump makes `test_a_json_can_dump_follow_ends_when_stdout_closes` fail ("hung").
- test_cli_closed_stdio.py:72 | _purge_handler (every request returns deleted 5; records POST bodies) | complies. A GET would record `{}` and fail `b["dry_run"]`, so only POSTs happen.
- test_cli_closed_stdio.py:88 | same | complies.

###### c27-injected.txt (0)

None for these files.

###### Extra sites (not in any enumeration)

- test_cli_contract.py:58 | `_canned` status body, full stdout | complies (one request).
- test_cli_contract.py:69 | `_canned` lines body, --json tail -n 1 | complies.
- test_cli_contract.py:135 | `_canned` empty bodies for negative-limit refusals | complies: with the refusal removed, the canned body answers rc 0 and the test goes red.
- test_cli_contract.py:159 | `_canned` /assert pass body | complies.
- test_cli_contract.py:244 | `_canned` wait body (also answers /status) | violates (FA-5).
- test_cli_contract.py:265 | `_canned` {"ok": true} for sysrq | complies.
- test_cli_can_dump.py:87, 102, 113 | `_sliding_can_daemon` call sites | complies (see 78).
- test_cli_can_dump.py:224, 234, 243, 257, 263, 269, 281 | `_frames_daemon` call sites | complies (see 218).
- test_cli.py:1886, 1911 | `_serve_http(_StoppableDaemon)` | violates (FA-3).
- test_cli.py:1513 | `broken_snapshot` passed positionally to `_stage_backfill` as the backfill callable | complies: raising BrokenPipeError is the stimulus.
- test_cli.py:2623 | `sink` raising ENOSPC, passed to stream_text | complies: the stimulus.
- test_cli_closed_output.py:96, 98, 124, 181 | `_child(routes=...)` route tables | complies. They dispatch on path (404 otherwise); bodies lack non-read fields.
- test_cli_closed_output.py:26 (CHILD source) | `platformdirs.user_data_dir = lambda *a, **k: DATA` in the child | complies. MCUSCOPE_DATA_DIR wins anyway, and this is belt and braces.
- test_cli_closed_output.py:37 (CHILD) | crashing `main` passed to `_stdio.console_entry` | complies: the stimulus.
- test_cli_closed_pipe.py:22 (CHILD) | platformdirs.user_data_dir lambda | complies, as above.
- test_cli_closed_pipe.py:26 (CHILD) | `sys.stdout = None` | complies: what CPython sets when fd 1 is closed at start.
- test_cli_closed_pipe.py:28 (CHILD) | crashing `main` | complies.
- Real objects, not doubles:
  - test_break.py:93 | ASGITransport over the real `create_app` | exempt because the real app.
  - test_assert.py TestClient(mk_app) at 53, 67, 79, 90, 110, 128, 154, 178, 186, 196, 212, 226, 248, 271, 285, 303, 317, 344, 352, 371, 409, 435, 686, 816, 835, 853, 867 | exempt because the real app.
  - test_cli.py:762 | a real listening socket that accepts and stalls | exempt because a real TCP peer.
  - test_cli.py:429, 431 and test_cli_argv.py:109 | monkeypatch.setenv | exempt because real environment input.

##### Surprising

- The `/shutdownX` mutant also passes all 20 tests of test_cli_daemon_stop_scope.py (group B1's file). That file's fake `/shutdown` handler should be checked for the same path-blind dispatch.
- test_assert.py `_flood` (lines 724, 758, 789) calls `store._broadcast` from plain threads and from the live-match executor. asyncio.Queue and `_sub_dropped` are loop-owned, so these are cross-thread mutations of loop state (class 40 or 66 adjacent).
  - This is not a class 27 false green: it can only lose counts. It is a flake source on a loaded runner.

#### Group B1 (scratch-27-28/out-B1.md)
#### Class 27 sweep, group B1

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3 (checked with `git rev-parse HEAD`).
Files: test_cli_daemonctl, test_cli_daemon_stop_scope, test_cli_decode_rejected_defs, test_cli_export_files, test_cli_export, test_cli_follow_frames, test_cli_follow, test_cli_output_rows, test_cli_prompts.
Mutants were run in copy-B1 and each file was restored from the repo afterwards (`diff -q` clean each time).

##### Findings

###### FB1-1 MEDIUM: test_cli_daemon_stop_scope.py:142-153 (sites 144, 145, 146), the "still answering" check is tested only on a path that a race alone can reach
- The doubles: `_request_shutdown` returns True, `_wait_daemon_gone` returns True and ignores its `pid`, and `_status_body` returns `{"version": "9"}`. The body passed in is `{"pid": 4242}` and there is no record, so `pid` is None.
- With `pid` None, the real `_wait_daemon_gone` judges "gone" on that same `_status_body`. The two fakes contradict each other: /status went quiet, and then it answers on the very next probe. The real composition gets there only if a new process takes the URL between two probes.
- The check's own comment covers a different case: the process it waited on or signalled is gone, yet something still answers. That means a corroborated `pid`, and no test drives it.
- Driven, mutant: in `cli_daemonctl._stop_running_daemon`, `if _status_body(s, timeout=1.0) is not None:` became `if pid is None and _status_body(...) is not None:`.
  - `pytest tests/test_cli_daemon_stop_scope.py tests/test_cli_daemonctl.py`: 50 passed.
  - Also clean on the mutant: test_cli.py `-k "stop or shim or daemon"` (23 passed), test_status_ppid_serial, test_cli_ux, test_cli_small_refusals, test_pidfile, test_cli_transport_timeouts.
  - What the mutant would ship: `daemon stop` prints "stopped mcuscoped (pid N)" and exits 0 while another process still serves the URL.
- Driven, fix shape: a scratch test (since deleted) called `_stop_running_daemon(s, body, rec, 4242)`, with a body naming pid 4242 and `_wait_daemon_gone` returning `pid == 4242`. It passed on the original code and failed on the mutant.

###### FB1-2 LOW: test_cli_daemonctl.py:183-210 (sites 205, 207, classes 188, 195), the Windows ctypes double ignores `use_last_error` and `restype`
- `WinDLL` returns `K32()` whatever `use_last_error` is, and `get_last_error` always returns 5. `CreateFileW` returns the handle as given, whatever `restype` is set to.
- Driven, mutant M1: `use_last_error=True` changed to False in `_open_append`. `pytest tests/test_cli_daemonctl.py`: 30 passed.
- Driven, mutant M2: the `k32.CreateFileW.restype = wintypes.HANDLE` line deleted. 30 passed.
- On Windows (reasoned only):
  - M1: the warning reads `[WinError 0] The operation completed successfully` instead of the real cause.
  - M2: INVALID_HANDLE_VALUE comes back as c_int -1 and misses the `== HANDLE(-1).value` check, so the failure surfaces from `open_osfhandle(-1)` with the wrong message.
  - Either way the no-log fallback still happens. The fixture replaces everything, so the Windows leg does not pin these two settings either.
- Fix: the fake returns 5 only when WinDLL was built with `use_last_error=True`, and returns the raw -1 unless `restype is wintypes.HANDLE`. Asserting both settings directly would also work.

###### FB1-3 LOW (weak assertion, not class 27): test_cli_daemonctl.py:365 `test_start_whose_daemon_never_answers_is_still_a_failed_start`
- The docstring promises that "the spawned daemon is dealt with". The test only asserts "did not come up", and the "stopped it" message and the "could not be stopped; still running" message both contain that text. `_FakeProc.terminate` does nothing, and `wait` returns 0 whether or not terminate was called.
- Driven, mutant M3: `_abandon_daemon` skips `terminate()`/`wait()` and sets `stopped = True`.
  - test_cli_daemonctl.py: 30 passed. test_cli_daemon_stop_scope.py: 20 passed.
  - test_cli_start_index_build.py (group B2) fails 3 tests, so the behaviour is covered; this test's claim is not.
- Not a class 27 violation: a real Popen would pass the same assertion on the mutant too. The problem is the assertion, not the double.

##### Site counts (grep -E on each enumeration)
- c27-patch.txt: 75
- c27-classes.txt: 13
- c27-assign.txt: 2
- c27-canned.txt: 26
- c27-injected.txt: 0
- Extra sites the enumerations miss: 7, found with `grep -nE "SimpleNamespace|serve\(|HTTPServer\(|transport=httpx\.MockTransport|store\.add_line|\._[a-z_]+ = |app\.state\.[a-z_.]+ = "` over the group, plus reading for `backfill=` and fixtures. They are listed at the end.

##### Rulings: c27-patch (75)

test_cli_daemon_stop_scope.py
- test_cli_daemon_stop_scope.py:88 | DAEMON_STOP_GRACE_S=1.0 | exempt because a knob, not a behavioural double
- test_cli_daemon_stop_scope.py:144 | _request_shutdown -> True (POST /shutdown accepted) | violates: with 145/146 it puts the check on the pid-None path (FB1-1)
- test_cli_daemon_stop_scope.py:145 | _wait_daemon_gone -> True, ignores pid | violates: claims gone by /status while 146 says /status answers (FB1-1)
- test_cli_daemon_stop_scope.py:146 | _status_body -> {"version": "9"} | violates: real _status_body never returns a body without uptime_s/ports; part of FB1-1
- test_cli_daemon_stop_scope.py:220 | sys.platform = linux | complies: _serving_pids reads sys.platform, the subject
- test_cli_daemon_stop_scope.py:222 | sys.platform = win32 | complies: same
- test_cli_daemon_stop_scope.py:261 | subprocess.Popen = spawn (writes to the inherited stderr fd, dies with 3) | complies: mirrors a child writing to its inherited handle and exiting
- test_cli_daemon_stop_scope.py:284 | cli.time.monotonic fake clock | complies: advanced only by the patched sleep; nothing else reads it on this path
- test_cli_daemon_stop_scope.py:285 | cli.time.sleep advances the clock | complies: same
- test_cli_daemon_stop_scope.py:286 | cli._status_or_refusal = probe -> (None, None), counted | complies: the real answer when nothing answers; the count is the measurement
- test_cli_daemon_stop_scope.py:287 | cli._status_body -> None | complies: pre-spawn "nothing running"
- test_cli_daemon_stop_scope.py:288 | Popen -> _Proc(999998, alive) | complies: terminate sets -15, wait returns it
- test_cli_daemon_stop_scope.py:297 | cli._status_body -> None | complies: pre-spawn probe
- test_cli_daemon_stop_scope.py:298 | cli._status_or_refusal -> (body, None) | complies: bodies carry version/uptime_s/ports plus pid/ppid, a shape the real one returns
- test_cli_daemon_stop_scope.py:299 | cli._open_append -> open(path, "ab") | complies: the real function's POSIX branch, substituted because sys.platform is patched to win32 and the ctypes branch cannot run on Linux
- test_cli_daemon_stop_scope.py:300 | Popen -> _Proc(shim, alive) | complies
- test_cli_daemon_stop_scope.py:310 | sys.platform = win32 | complies: selects the _serving_pids ppid branch, the subject; the other win32 branches on this path (pid_running, _open_append) are not reached (fresh data dir, 299)
- test_cli_daemon_stop_scope.py:324 | sys.platform = parametrised | complies: same
- test_cli_daemon_stop_scope.py:354 | cli_daemonctl.sys = SimpleNamespace(platform="win32") | complies: only .platform is read there (lines 68, 314), any other attribute fails loudly, and pidfile keeps the real platform, so the victim's liveness is real
- test_cli_daemon_stop_scope.py:394 | _wait_daemon_gone -> False | complies: the grace running out is the respect under test; real polling would see ppid 1 on every probe after the first, so the outcome is the same, just 1 s faster
- test_cli_daemon_stop_scope.py:412 | _wait_daemon_gone -> False | complies: makes "grace ran out, quiet at the re-check" reachable, a real sequence (the daemon exits between the last poll and the re-check)
- test_cli_daemon_stop_scope.py:435 | cli._start_daemon -> records pid_running(shim.pid) | complies: the assertion is the launcher's liveness at the moment start is called, and the real start changes nothing before its first probe
- test_cli_daemon_stop_scope.py:438 | DAEMON_STOP_GRACE_S=10.0 | exempt because a knob (restores the default for the shim wait)

test_cli_daemonctl.py
- test_cli_daemonctl.py:42 | cli._pid_file -> tmp path | complies: keeps the record out of user dirs; the resolver's OSError branch is not the subject
- test_cli_daemonctl.py:43 | _Daemon.spawned = [] | exempt because it resets test state, not a double
- test_cli_daemonctl.py:44 | cli.subprocess.Popen = _Daemon | complies: alive child whose pid matches the fixture's status pid; without terminate/wait an abandon path would fail loudly
- test_cli_daemonctl.py:51 | cli._stop_daemon = stop (records, clears running) | complies: the restart's next probe sees the stop take effect, as with a real stop
- test_cli_daemonctl.py:52 | cli.Client.probe -> {"ports": []} for any path | complies: only restart's GET /ports reaches it (status and stop are faked); no test asserts sim carrying
- test_cli_daemonctl.py:61 | cli._status_body = status_body | complies: status-valid body (version, uptime_s, ports, pid) after the spawn, the running body before; no test in the fixture needs a guard refusal
- test_cli_daemonctl.py:62 | cli._status_or_refusal | complies: consistent with 61 (built on it), refusal always None
- test_cli_daemonctl.py:203 | sys.platform = win32 | complies: selects _open_append's Windows branch, the subject
- test_cli_daemonctl.py:204 | sys.modules["msvcrt"] = SimpleNamespace(open_osfhandle) | complies: returns an fd like the real one; the ignored flags are harmless because the CLI never writes to the fd (the child inherits it) and only fstats it
- test_cli_daemonctl.py:205 | ctypes.WinDLL -> K32() | violates: ignores use_last_error (FB1-2, driven M1)
- test_cli_daemonctl.py:207 | ctypes.get_last_error -> 5 | violates: answers 5 without use_last_error=True (FB1-2)
- test_cli_daemonctl.py:208 | ctypes.WinError -> OSError(code, "access denied") | complies: the real one is an OSError subclass, caught by the same except
- test_cli_daemonctl.py:230 | cli._status_body -> None | complies
- test_cli_daemonctl.py:231 | cli._status_or_refusal -> (None, None) | complies
- test_cli_daemonctl.py:244 | Popen = popen (records the stderr kw, returns a dead Proc) | complies: the assertion is the stderr handle passed, which recording captures
- test_cli_daemonctl.py:279 | Popen = popen (_fake_spawn, records argv) | complies: _FakeProc pid matches the phased status pid
- test_cli_daemonctl.py:280 | cli._pid_file -> tmp | complies
- test_cli_daemonctl.py:315 | cli._stop_daemon -> None | complies: the next phase answers "nothing there", as after a real stop
- test_cli_daemonctl.py:434 | Popen -> _FakeChild(exited=1) | complies: the losing start's child died on the port
- test_cli_daemonctl.py:435 | cli._pid_file -> tmp | complies
- test_cli_daemonctl.py:436 | pidfile.pid_running -> pid == 777 | complies: dispatches on pid as the real one does; _write_pid_record imports it at call time
- test_cli_daemonctl.py:447 | cli._status_body = status | complies: None, then a valid body naming 777
- test_cli_daemonctl.py:448 | cli._status_or_refusal | complies: consistent with 447
- test_cli_daemonctl.py:507 | cli.Client.open -> MockTransport answering every path with one code | complies: a guarded daemon answers every route with the guard's code, and {"error": str} is the guard's body shape
- test_cli_daemonctl.py:513 | Popen = fake_popen raising _Spawned | complies: tripwire; a spawn escapes as an exception, never passes quietly

test_cli_decode_rejected_defs.py
- test_cli_decode_rejected_defs.py:48 | websockets.connect -> _ScriptedWS(frames) | complies: rows carry id/ts/port/chan/raw; the end is ConnectionClosedOK(rcvd None), the same exit 3 a daemon close 1001 gives

test_cli_export_files.py
- test_cli_export_files.py:79 | builtins.open = spy delegating to the real open | complies: records, then behaves exactly like the real open
- test_cli_export_files.py:120 | sys.stdout = TextIOWrapper(newline="\r\n") | complies: stands in for Windows' translating stdout, the subject
- test_cli_export_files.py:258 | same | complies

test_cli_follow.py
- test_cli_follow.py:61 | websockets.connect = partial(real, open_timeout=0.3) | exempt because a knob on the real connect
- test_cli_follow.py:86 | websockets.connect = _HandshakeTimesOut | complies: the real handshake timeout raises TimeoutError from __aenter__, the same class at the same point
- test_cli_follow.py:95 | same | complies
- test_cli_follow.py:114 | socket.create_connection records, raises ConnectionRefusedError | complies: the subject is the address dialled; refusal is what the real one raises with nothing listening
- test_cli_follow.py:121 | time.sleep fake clock | complies
- test_cli_follow.py:122 | time.monotonic fake clock | complies
- test_cli_follow.py:132 | client.get -> {"frames": []} (priming read) | complies: /can/frames shape; the transport handler raises on every non-/status path by design
- test_cli_follow.py:165 | websockets.connect records kwargs, raises InvalidURI | complies: the kwargs are the subject; InvalidURI raised at call time rather than at __aenter__, both inside the same try
- test_cli_follow.py:234 | websockets.connect -> _StagingWs | complies: a pending recv resolves with ConnectionClosedOK on close, and __aexit__ awaits, as a real close does
- test_cli_follow.py:235 | sys.stdout = _ClosedStdout | complies: write/flush raise BrokenPipeError; its fileno raising OSError where a real closed pipe has an fd is harmless (_to_devnull suppresses Exception; the asserted code is 0)
- test_cli_follow.py:268 | store.MAX_SUBSCRIBERS = 0 | exempt because a knob: reaches the cap branch without 256 subscribers, and the subject stays the cap refusal

test_cli_follow_frames.py
- test_cli_follow_frames.py:26 | websockets.connect -> _ScriptedWS([bad bytes, good]) | complies: a binary frame arrives as bytes from recv; the bad frame is the injected fault
- test_cli_follow_frames.py:45 | connect records the URL, returns _ScriptedWS([]) | complies: the URL is the subject
- test_cli_follow_frames.py:61 | connect raises InvalidStatus(Response(status)) | complies: the real connect raises this on a non-101 answer; the mapping under test does not depend on the status
- test_cli_follow_frames.py:75 | connect raises ConnectionRefusedError | complies
- test_cli_follow_frames.py:102 | time.sleep fake clock, KeyboardInterrupt at 35 s | complies
- test_cli_follow_frames.py:103 | time.monotonic fake clock | complies
- test_cli_follow_frames.py:106 | client.get -> {"frames": []} | complies
- test_cli_follow_frames.py:123 | websockets.connect -> _ScriptedWS(rows of a and b) | complies: ignores the ?port= scope, so the -p a run also gets b's row, which the daemon would never send; harmless because only the column and "from a" are asserted, and test_the_ws_url_quotes_the_port pins the scope (driven: a mutant dropping ?port= fails only that test)
- test_cli_follow_frames.py:147 | cli_output._OUT_FAILED = True | complies: the flag main() sets when stdout was closed at start

test_cli_output_rows.py
- test_cli_output_rows.py:24 | websockets.connect -> _ScriptedWS([null raw, kept]) | complies: the null raw is the injected malformed row, the subject

test_cli_prompts.py
- test_cli_prompts.py:21 | sys.stdin = StringIO(answer) | complies: confirm_or_exit reads sys.stdin.readline(), and a terminal yields the same strings ("" at EOF)
- test_cli_prompts.py:22 | _stdin_is_interactive -> True | complies: a terminal is the respect under test

##### Rulings: c27-classes (13)
- test_cli_daemon_stop_scope.py:31 | _FakeDaemon (the daemon's /status and /shutdown) | complies:
  - its /status carries version/uptime_s/ports/pid. The real one always adds ppid; that is harmless on POSIX, and _ppid_daemon adds it where needed.
  - once dead it answers 404 {"error"} where the real daemon refuses the connection; both read as "not running".
  - the 403 matches the real non-loopback /shutdown refusal. An accepted shutdown goes quiet at once, where the real one takes 0.2 s; assign 207 covers the delay.
- test_cli_daemon_stop_scope.py:38 | H (the handler inside _FakeDaemon) | complies: as above
- test_cli_daemon_stop_scope.py:230 | _Proc (Popen) | complies: terminate/kill set a returncode that wait returns; wait never raises TimeoutExpired on a live child, which is harmless here (line 271 asserts the probe count, and the _start_answered_by children are never abandoned)
- test_cli_daemonctl.py:26 | _Daemon (Popen) | complies: see patch 44
- test_cli_daemonctl.py:254 | _FakeProc (Popen) | complies for class 27: the assertion, not the double, lets M3 pass (FB1-3)
- test_cli_daemonctl.py:401 | _FakeChild (Popen) | complies: poll/terminate/wait keep one exit state
- test_cli_daemonctl.py:497 | _Spawned | exempt because a tripwire exception, not a double
- test_cli_daemonctl.py:188 | CreateFileW | violates: returns the handle whatever restype is (FB1-2, driven M2)
- test_cli_daemonctl.py:195 | K32 (kernel32) | violates: built whatever use_last_error is (FB1-2)
- test_cli_daemonctl.py:234 | Proc (Popen, dead child) | complies
- test_cli_follow.py:69 | _HandshakeTimesOut | complies: see patch 86
- test_cli_follow.py:192 | _StagingWs | complies: see patch 234
- test_cli_follow.py:217 | _ClosedStdout | complies: see patch 235

##### Rulings: c27-assign (2)
- test_cli_daemon_stop_scope.py:207 | do_POST = slow_shutdown | complies: accepts, then /status answers 0.4 s longer, like the real call_later(0.2) plus teardown
- test_cli_daemon_stop_scope.py:348 | do_GET = do_get | complies: adds ppid per probe, and falls back to the base handler once dead

##### Rulings: c27-canned (26)
- test_cli_daemonctl.py:301 | record_params in _phased | complies: /status per phase, with ConnectError (the real class) for "nothing there"; any other path gets {"ports": []}, and restart's /ports probe is the only such request
- test_cli_daemonctl.py:508 | MockTransport in _answer_status | complies: see patch 507
- test_cli_prompts.py:36 | run_mcu_canned with _purge (every path answers a preview) | complies: the assertion `sent == [dry_run True]` pins exactly one request, the preview POST (a GET would record {} and KeyError); the missing dry_run field is not read
- test_cli_prompts.py:56 | run_mcu_canned with _session (dispatches on method) | complies: the GET row carries the id/name/lines the prompt reads, and DELETE answers lines_deleted (the real one adds ok)
- test_cli_prompts.py:68 | same | complies: the positive control asserts the DELETE; its path is not the subject
- test_cli_export_files.py:86 | recorder lines_export (unmatched paths get {} 200) | complies: only the open's newline is asserted, so a wrong endpoint passes this test (driven: /lines/exportx), but four siblings catch it; harmless
- test_cli_export_files.py:96 | recorder lines | complies: the paged path; rows lack port/dir/seq, which is harmless for single-board output
- test_cli_export_files.py:116 | recorder with three streamed bodies | complies: `b"\n" in` fails on a {} body, so routing is pinned (driven: fails on the /lines/exportx mutant)
- test_cli_export_files.py:134 | recorder 400 {"error"} | complies: the daemon's refusal shape; the "error: " prefix tells it apart from the CLI's own "no such session" die
- test_cli_export_files.py:165 | recorder REFUSAL on every export endpoint | complies: an unmatched path would answer 200 {} and write the file, failing the keep assertion (driven: argv0 fails on the mutant)
- test_cli_export_files.py:184 | canned dying body on every path | complies: the /ports probe also gets a dying body, which probe reads as "no /ports", giving the streamed branch a real single-board daemon also takes; ReadError is the real class
- test_cli_export_files.py:205 | canned sessions + dying body | complies
- test_cli_export_files.py:221 | recorder empty bodies | complies
- test_cli_export_files.py:233 | canned dying JSONL | complies: --json skips the /ports probe
- test_cli_export_files.py:244 | canned "one\ntwo" on every path | complies: the /ports probe reads non-JSON as "no /ports" (streamed branch); the missing final newline is a fault injection for the CLI's pass-through
- test_cli_export_files.py:255 | recorder lines row | complies
- test_cli_export_files.py:271 | recorder sessions + bundle | complies: asserts the exact paths
- test_cli_export_files.py:284 | recorder() with no bodies | complies: asserts no request was made; the positive control that the recorder does receive requests is line 271
- test_cli_export_files.py:310 | canned sessions + dying body | complies
- test_cli_follow_frames.py:105 | Client(transport=MockTransport(handler)) | complies: /status gives the capture, polls fail with ConnectError (the real class) outside the answered window
- test_cli_follow_frames.py:124 | run_mcu_canned _two_ports | complies: /ports lacks "stored" (an older daemon's shape) and other paths answer a /lines body; tail -f only uses the attached aliases, and /lines with -n 0 gives []
- test_cli_follow_frames.py:127 | same, -p a | complies: -p skips the /ports probe
- test_cli_follow_frames.py:139 | run_mcu_canned wait handler | complies: a /wait match with its line; the missing waited_ms/dropped/cmd_result are read with .get
- test_cli_follow_frames.py:149 | MockTransport that calls pytest.fail | complies: tripwire
- test_cli_output_rows.py:54 | run_mcu_canned /assert | complies: missing reason/dropped/cmd_result are read with .get, and the line carries raw, the field printed; other paths answer a status body
- test_cli_follow.py:131 | Client(transport=MockTransport(handler)) | complies: raises the real httpx timeout classes on polls

##### Rulings: c27-injected (0)
- none in this group

##### Extra sites (not in any enumeration)
- test_cli_follow.py:29 | stalling_listener, a TCP listener that accepts and never answers (a stalled daemon) | complies: real socket behaviour
- test_cli_follow.py:241 | backfill=slow_snapshot (for _tail_snapshot) | complies: prints nothing and returns watermark 0, as the real snapshot does under -n 0
- test_cli_follow.py:298 | store._subscribers_closed = True (for stop_subscribers) | complies: subscribe() refuses on this flag alone; the event and sentinel stop_subscribers also sets only matter to existing subscribers, and there are none
- test_cli_follow.py:320 | a real websockets serve() standing in for the daemon | complies: sends a JSON array frame over 1 MiB then close 1001, as the daemon would; the missing port/dir/seq are unread with show_port False
- test_cli_export.py:47 | store.add_line on the real stack | exempt because it is the real store seeded at its own boundary, not a double
- test_cli_daemon_stop_scope.py:77 | victim fixture, a real child process whose pid is named | exempt because a real process
- test_cli_daemonctl.py:200 | msvcrt SimpleNamespace | ruled with patch 204

#### Group B2 (scratch-27-28/out-B2.md)
#### Class 27 sweep, group B2

HEAD checked: f31ecd995ee2ed193d8d60637b76620ddc880be3.
Files: test_cli_(read_scope|send_verdicts|sessions|small_refusals|start_index_build|status|terminal_controls|transport_timeouts|ux|version_gate).py.

##### Findings

###### FB2-1 MEDIUM: test_cli_version_gate.py:203-217 records the gated request but asserts only its path
- `test_the_same_fields_reach_a_current_daemon` is the only test for 5 of its 7 GATED cases that reaches the CLI's request builder with the gated option, and it checks only `sent[0] == "/status" and len(sent) == 2`.
  - The recorder captures each request's body and params, but nothing reads them. The test's name says the fields reach the daemon; it never checks that they do.
- Concrete failure: `mcu cmd --eol crlf`, `mcu wait --send X --eol none`, `mcu assert --send X --eol none`, `mcu wait --send X --repeat-ms 50` and `mcu -p board plot export` can drop the option from the request and the test still passes.
  - That exit-0 silent drop is exactly the class 53 defect the gate exists to prevent.
- Driven:
  - Mutant: cli.py sends `"eol": None` in `_run_cmd`, `body["eol"] = None` in wait and assert, no `body["repeat_ms"]`, and no `params["port"]` in plot export.
  - Result, all green with `-p no:randomly` on each file: test_cli_version_gate.py 44 passed, test_eol.py 62, test_cli_send_verdicts.py 28, test_cli_contract.py 53, test_wait_repeat.py 20, test_cli.py 167, test_cli_read_scope.py 40, test_cli_export.py 35, test_cli_export_files.py 33, test_cli_small_refusals.py 16, test_plot_export_refusals.py 18, test_plot_export_decode.py 22, test_plot_export_since_id.py 19, test_plot.py 20, test_flow_cli_windows.py 24.
  - `mcu send --eol` and `mcu attach --eol` are pinned elsewhere (test_eol.py:309, :321); the other five are not, going by a grep of every test for `--eol`, `repeat-ms` and `-p ... plot export`.
- Fix shape, driven in the copy:
  - After the path check, read the second non-`GET /ports` request's JSON body (or its params) and assert the gated key carries the typed value (`eol`, `repeat_ms`, `port`).
  - Against the mutant: 5 failed, 2 passed (the send and attach cases, whose fields the mutant kept).
  - Against clean cli.py: 7 passed.
- Secondary divergence in the same test (harmless to the path count): the recorder answers 200 to cases a real current daemon would refuse.
  - `-p nosuch` gets a 400 "no such port".
  - send, cmd and wait get "no ports attached", because STATUS lists `ports: []`.
  - A body assertion must not rely on those answers.

###### FB2-2 LOW: test_cli_ux.py:183 is a vacuous assertion over a recording double
- `assert probes == [] or cmd == "restart"` runs after the `for cmd in ("start", "restart")` loop, where `cmd` is always `"restart"`, so it can never fail.
- The docstring promises "start must not even probe", but nothing checks it.
- Driven: mutant cli.py calls `_status_body(settings_of(ctx), timeout=1.0)` at the top of `daemon_start`, before the `--open`/`--json` refusal. `pytest tests/test_cli_ux.py -k open_with_json -p no:randomly`: 1 passed.
- The property holds on the clean tree today, so this is a missing detector, not a live defect.
- Fix: assert `probes == []` inside the loop for `start`, or snapshot `len(probes)` per iteration.

###### FB2-3 LOW: test_cli_read_scope.py:67 feeds a page the daemon cannot produce and so pins a clause no real answer can observe
- `_lines_handler` ignores the `port` param. Under `-p a` it returns rows from ports a and b.
- The daemon filters on strict equality (store.py:1819-1822), so a `-p a` page never carries port b. This is class 27 (the double does not dispatch like the daemon) and class 63 (unreachable fixture).
- The half-test passes only because `_port_column`'s rows branch has `if s.port or ...` (cli.py:882).
  - Against any real answer, that clause is redundant: one port's rows never make a column.
  - So the test pins a clause whose only observable effect exists on impossible data. The subject it claims ("under -p, no column") holds whether or not the clause is there.
- Driven: removing `s.port or` from the rows branch fails only `test_rows_from_one_board_or_under_p_carry_none` (1 failed, 39 passed, test_cli_read_scope.py).
- Fix: either drop the clause and the mixed-rows half, or label it a defensive check on an unreachable input. Port forwarding itself is pinned by :39, whose handler does dispatch on `port`.

##### Per-enumeration counts (grep -E over the B2 regex)
- c27-patch.txt: 43
- c27-classes.txt: 4
- c27-assign.txt: 0
- c27-canned.txt: 81 (send_verdicts 18, status 6, read_scope 17, transport_timeouts 4, sessions 5, small_refusals 17, version_gate 14)
- c27-injected.txt: 0
- Extra shapes: `grep -nE "SimpleNamespace|Mock\b|MagicMock|mock\.|setenv|delenv|chdir|lambda"` over the ten files, minus monkeypatch.setattr lines.
  - New doubles found: none. The lambdas are the `probe` callables fed to start_index_build's `_status_or_refusal` double (ruled at :72) and ux:255's probe callables (ruled at ux:256).
  - setenv, delenv and chdir are environment isolation, not doubles.

##### Rulings: c27-patch.txt (43)
- test_cli_send_verdicts.py:239 | Client.open -> MockTransport raising httpx.ReadTimeout on every request (a daemon that accepts, then goes silent) | complies: real httpx raises ReadTimeout on a read timeout, driven end to end over a socket at test_cli_transport_timeouts.py:45; wait and assert here carry no gated option, so the one request is POST /wait or /assert and path-agnosticism is harmless.
- test_cli_send_verdicts.py:254 | Client.open -> MockTransport answering `{"status": "timeout", "line": None}` | complies: the daemon's /wait timeout body (server.py:2697-2704) minus fields the exit-code path does not read; single POST /wait.
- test_cli_start_index_build.py:135 | cli_daemonctl.INDEX_BUILD_CEILING_S 600 -> 30 | exempt because a knob, not a behavioural double; :134 asserts the shipped 600 first; subject unchanged.
- test_cli_start_index_build.py:69 | time.monotonic (cli.time is the stdlib module, so process-wide) -> the test clock | complies: it advances only on the readiness loop's sleep; both /status probes are patched, so no real wait goes uncounted, and every elapsed-time assertion is on the loop's own sleeps.
- test_cli_start_index_build.py:70 | time.sleep -> advance the test clock | complies (as :69).
- test_cli_start_index_build.py:71 | cli._status_body -> None | complies: the pre-spawn "already running" check at a dead url is None for real (probe_status maps ConnectError to (0, None), _status_or_refusal returns (None, None)).
- test_cli_start_index_build.py:72 | cli._status_or_refusal -> (probe(n, t), None) | complies: BODY passes the real _is_status_body filter (version, numeric uptime_s, ports), and its pid 4242 equals proc.pid, so _serving_pids agrees; never a refusal, which is not this file's subject.
- test_cli_start_index_build.py:73 | subprocess.Popen -> spawn writing `first` into the given stderr handle, returning _Proc | complies: the real child writes to the same inherited append handle after err_start was measured; writing before Popen returns is equivalent for _index_build and _stderr_tail, which read from err_start.
- test_cli_terminal_controls.py:34 | cli_output._JSON_MODE = False | complies: the module global set_json_mode() sets; _GuardedStdout.write reads it at write time.
- test_cli_terminal_controls.py:42 | cli_output._JSON_MODE = False | complies (as :34).
- test_cli_terminal_controls.py:46 | cli_output._JSON_MODE = True | complies (as :34).
- test_cli_terminal_controls.py:55 | sys.stderr -> _Tty | complies: err_write reads sys.stderr and _isatty() at call time.
- test_cli_terminal_controls.py:59 | sys.stderr -> io.StringIO | complies: StringIO.isatty() is False, the pipe case.
- test_cli_transport_timeouts.py:65 | cli.READ_TIMEOUT_S 30 -> 0.3 | exempt because a knob: shrinks the read timeout on a real socket; _get_rows reads the global at call time; subject (a real ReadTimeout mapped to exit 1) unchanged.
- test_cli_ux.py:27 | cli_client.DEFAULT_URL -> dead url | exempt because a knob: start_hint compares against this binding (cli_client.py:71).
- test_cli_ux.py:28 | cli.DEFAULT_URL -> dead url | exempt because a knob: the only other binding (cli.py:27, used at :128); both patched, so default resolution and the hint agree.
- test_cli_ux.py:98 | cli._pid_file -> tmp path | complies: start and stop in cli.py read cli._pid_file; the fake name follows the real host-port naming, so the real _stderr_log_path derives the .err sibling the test reads.
- test_cli_ux.py:99 | cli.subprocess.Popen (process-wide) -> _FakeDaemon | complies: see the _FakeDaemon class ruling.
- test_cli_ux.py:100 | _FakeDaemon.spawned = [] | exempt because a knob (per-test state of the fake).
- test_cli_ux.py:101 | _FakeDaemon.exit = 2 | exempt because a knob (a child that died).
- test_cli_ux.py:102 | _FakeDaemon.stderr_lines = 12 lines | exempt because a knob.
- test_cli_ux.py:124 | _FakeDaemon.stderr_lines = [] | exempt because a knob.
- test_cli_ux.py:141 | cli._status_body -> status_body | complies: the body passes _is_status_body; pid 4242 equals proc.pid.
- test_cli_ux.py:142 | cli._status_or_refusal -> (status_body(), None) | complies: the real _status_body is _status_or_refusal minus the refusal; one probe counter for both, as both hit GET /status.
- test_cli_ux.py:149 | _FakeDaemon.exit = None | exempt because a knob (a live child).
- test_cli_ux.py:152 | webbrowser.open -> recorder | complies: cli imports webbrowser and calls .open(url) at call time; the recorder receives it at :164.
- test_cli_ux.py:173 | _FakeDaemon.exit = None | exempt because a knob.
- test_cli_ux.py:175 | webbrowser.open -> pytest.fail | complies: Failed is a BaseException that cli.main's handlers do not catch; backed by `spawned == []` at :182. The probe assertion at :183 is vacuous (FB2-2).
- test_cli_ux.py:190 | _FakeDaemon.exit = None | exempt because a knob.
- test_cli_ux.py:206 | cli._status_body -> status_body with config_path on probe 1, None on probe 2 | complies: real restart probes /status, then start's already-running probe runs after the stop; config_path is a real /status field (server.py:1070).
- test_cli_ux.py:207 | cli._status_or_refusal -> (status_body(), None) | complies (as :142).
- test_cli_ux.py:209 | Client.probe -> a sim port list for any path | complies: restart's only probe is GET /ports (cli.py:2768); `sim://demo` is the device daemon.py:173 attaches for --sim.
- test_cli_ux.py:211 | cli._stop_daemon -> no-op | complies: the subject is the carried args; the stop's effect (daemon gone) is modelled by probe 2 answering None.
- test_cli_ux.py:216 | Client.probe -> {"ports": []} | complies (as :209, no sim port).
- test_cli_ux.py:226 | _FakeDaemon.exit = None | exempt because a knob.
- test_cli_ux.py:228 | cli._stderr_log_path -> a path under a missing directory | complies: the real _open_append (`open(path, "ab")`) raises FileNotFoundError, reaching the real OSError branch; init_accepting_devnull asserts DEVNULL reached Popen.
- test_cli_ux.py:237 | _FakeDaemon.__init__ -> init_accepting_devnull | complies: asserts the DEVNULL fallback, records like the real fake.
- test_cli_ux.py:241 | _FakeDaemon.__init__ -> real_init | exempt because a restore, not a double (monkeypatch's undo also restores it).
- test_cli_ux.py:256 | Client.probe on one instance -> None, or a port with no alias | complies: None is probe's real answer to any failure; a port without alias is not a shape the daemon sends (pt.status() always carries alias), but the filter for it is the named subject.
- test_cli_ux.py:265 | cli._status_body -> None | complies: daemon_status calls only _status_body; the patch also keeps the default-url half from probing a real daemon on 8558.
- test_cli_ux.py:280 | webbrowser.open -> recorder | complies: a failed start never reaches open; the same recorder is shown receiving calls at :164.
- test_cli_ux.py:290 | _FakeDaemon.exit = None | exempt because a knob.
- test_cli_ux.py:293 | cli._stop_daemon -> recorder | complies: with no daemon found, the real _stop_daemon would exit 1 ("nothing to stop"); the recorder asserts it is never called.

##### Rulings: c27-classes.txt (4)
- test_cli_start_index_build.py:31 | _Proc, a Popen stand-in | complies: poll is None until terminate or kill, then -15; wait is reached only after terminate (_abandon_daemon), so the real TimeoutExpired on a live wait is never on a tested path; pid matches BODY's pid.
- test_cli_terminal_controls.py:19 | _Tty, a terminal stream | complies: a StringIO whose isatty() is True; _isatty and visible() are the shipped code.
- test_cli_ux.py:65 | _FakeDaemon, a Popen stand-in | complies:
  - Writes bytes to the given handle; the real _open_append opens "ab".
  - Flushes before the parent closes its copy.
  - poll returns the configured exit, terminate and kill set it, and wait after terminate returns -15.
  - wait on a live fake is never reached.
- test_cli_ux.py:248 | _Resp, an httpx.Response in Client.fail | complies: fail reads status_code, text and json(); `.request` is read only on a 404, not this 400.

##### Rulings: c27-assign.txt (0)
None.

##### Rulings: c27-canned.txt (81)
Helpers:
- run_mcu_canned (test_cli.py:905) patches Client.open with a MockTransport.
- recorder (support.py:345) dispatches on path only and answers any unmatched path `{}` with 200.
- `_answer` (send_verdicts:19) answers /ports with one port and everything else with the given body.

Lines:
- test_cli_send_verdicts.py:41 | wait --send refused (ERR) | complies: no gate, so one POST /wait; a timeout with an ERR cmd_result and sends 1 is reachable (server.py:2660-2703); the path is not asserted here but is pinned live by the wait tests in test_cli.py.
- test_cli_send_verdicts.py:50 | match with an ERR cmd_result | complies: reachable, since the match loop runs after send_command returns err.
- test_cli_send_verdicts.py:58 | same, --json | complies.
- test_cli_send_verdicts.py:68 | cmd_result timeout | complies: reachable (send_command times out, sends=1).
- test_cli_send_verdicts.py:78 | ok cmd_result, match | complies.
- test_cli_send_verdicts.py:93 | assert fail with a failed send | complies: server.py:3060 sets fail when cmd_result is not ok; no reason or dropped field, but the text path reads dropped via .get and never reads reason.
- test_cli_send_verdicts.py:102 | empty verdict | complies: checked_lines 0 gives "empty" (server.py:3067).
- test_cli_send_verdicts.py:110 | empty, --json | complies.
- test_cli_send_verdicts.py:118 | allow_empty recorded | complies: `seen` records every non-/ports request body; the only one is POST /assert, so seen[-1] is it.
- test_cli_send_verdicts.py:121 | allow_empty absent | complies (as :118).
- test_cli_send_verdicts.py:137 | option refusals | complies: refused before any request; the message is unique to the CLI refusal, and a regressed refusal would send, get `{}`, and fail on the message.
- test_cli_send_verdicts.py:148 | recorder, wait timeout | complies: dispatches on path; without --send the count clause never reads the missing sends.
- test_cli_send_verdicts.py:159 | sends 1, failures 0 | complies: the only reachable single-send timeout (a failed write is a 400).
- test_cli_send_verdicts.py:181 | no-send, repeat and old-daemon bodies | complies:
  - sends=0 without --send is reachable.
  - The repeat case passes the gate against STATUS 0.4.0.
  - `{}` is an older daemon, reachable because nothing gates that argv.
- test_cli_send_verdicts.py:191 | JSON body equality | complies.
- test_cli_send_verdicts.py:202 | one timeout body for every path, including the --repeat-ms gate's GET /status | complies:
  - A /status with no version is not a shape any daemon sends.
  - The gate treats it the same as an unorderable dev version (test_cli_version_gate.py:50), so it passes as it would for a real daemon.
  - The subject is the /wait body without sends.
- test_cli_send_verdicts.py:240 | ReadTimeout transport (the patch at :239) | complies.
- test_cli_send_verdicts.py:255 | timeout-body transport (the patch at :254) | complies.
- test_cli_status.py:16 | recorder /status with lines_trimmed | complies: status GETs only /status; lines_trimmed is a real field (server.py:1084).
- test_cli_status.py:25 | lines_trimmed 0 and absent | complies: absent is an older daemon.
- test_cli_status.py:42 | recorder /plot/channels | complies:
  - Path-dispatched: a wrong path gets `{}`, prints "no plot channels", and fails.
  - The rows lack port, kind, labels and last_tick, and last_ts None with count 3 is not what the store emits.
  - Harmless: the text path reads name, sid, type, unit, last_value, last_ts and count, and last_ts only drives age=, which is not asserted.
- test_cli_status.py:52 | same, --json | complies.
- test_cli_status.py:62 | recorder /devices | complies: the row keys match _enumerate_devices (server.py:3793-3800); by_id is optional in the CLI.
- test_cli_status.py:74 | /devices empty | complies: indistinguishable from the `{}` fallback, but :62 fails on a wrong path.
- test_cli_read_scope.py:39 | 400 "no such port: nosuch", dispatched on the port param | complies: /lines, /lines/export, /can/frames and /plot/channels answer exactly that via _unknown_port (server.py:1013); a CLI dropping `port` gets 200 and fails rc == 1.
- test_cli_read_scope.py:57 | _lines_handler, newest-first page | complies: the rows path makes no /ports probe.
- test_cli_read_scope.py:64 | same, one board | complies.
- test_cli_read_scope.py:67 | `-p a` answered with a/b rows | violates (LOW, FB2-3): ignores the port param the daemon filters on.
- test_cli_read_scope.py:85 | multi-board export | complies: /ports without `stored` is the pre-0.5.0 daemon the docstring names; asserts /lines/export was not requested.
- test_cli_read_scope.py:100 | single-board export, positive control | complies: path-dispatched.
- test_cli_read_scope.py:120 | --since-id walk | complies: asserts order=asc and each page's params; any other request (a /ports probe) raises KeyError in the handler and fails rc == 0.
- test_cli_read_scope.py:136 | --since-id note | complies: the same strictness; 2 rows, truncated, at limit 2 is reachable.
- test_cli_read_scope.py:147 | note without --since-id | complies: a newest-first truncated page is reachable.
- test_cli_read_scope.py:160 | tail -n 0 | complies: limit 0 with truncated and no rows is the daemon's no-backfill answer (cli.py:668-670, SPEC 4).
- test_cli_read_scope.py:162 | can dump -n 0 | complies (the same answer from /can/frames).
- test_cli_read_scope.py:165 | tail -n 1, positive control | complies: no rows with truncated at limit 1 is not reachable (the daemon returns the row), but the note fires on the reachable (1 row, truncated) answer too, so the control still discriminates the `if n:` guard.
- test_cli_read_scope.py:179 | plot channels -p sim | complies: asserts exactly one request per run and its params.
- test_cli_read_scope.py:180 | plot channels, no -p | complies (as :179).
- test_cli_read_scope.py:201 | export dying mid-walk | complies: dispatches on path and on the decoder's `match` prime; 500 {"error"} is the daemon's catch-all answer (server.py:574-579).
- test_cli_read_scope.py:244 | recorder, truncated /lines | complies: path-dispatched; the rows lack port, dir and seq, which fmt_line reads with .get; the assertion is on the note.
- test_cli_read_scope.py:285 | recorder, last_ms bounds | complies: /lines takes last_ms 0..MAX_MS (server.py:1845 ge=0), matching the CLI bound.
- test_cli_transport_timeouts.py:23 | _hang, ReadTimeout | complies:
  - Real httpx raises ReadTimeout, confirmed over a socket at :45.
  - log export's /ports probe maps it to None, as it would a real timeout.
  - Every command maps it through _daemon_errors.
- test_cli_transport_timeouts.py:33 | ConnectTimeout | complies: the real transport's connect-timeout exception.
- test_cli_transport_timeouts.py:38 | /cmd {"status": "timeout"} | complies: the daemon's board-timeout result.
- test_cli_transport_timeouts.py:87 | _read_timeouts | complies: reads the `extensions["timeout"]["read"]` httpx sets from the timeout argument; min(with_match) would fail on any extra short-timeout request.
- test_cli_sessions.py:16 | recorder, session export by id | complies: asserts paths and the name param.
- test_cli_sessions.py:29 | no such session | complies: a current daemon answers `[]` for an unknown name (server.py:1480); asserts paths.
- test_cli_sessions.py:69 | a page ignoring name= | complies: path-dispatched; the pre-0.3.0 daemon as named; asserts raw paths.
- test_cli_sessions.py:83 | a page holding the name | complies: asserts paths.
- test_cli_sessions.py:91 | fallback to a missing session | complies: 400 "no such session: nope" is the export route's own refusal (server.py:1563); asserts paths.
- test_cli_small_refusals.py:29 | _recorder, mark | complies: asserts method, path and body; {"line_id": 5} is /marker's shape (server.py:2188).
- test_cli_small_refusals.py:42 | send -x | complies: body asserted; {"ok": True} is /send's shape.
- test_cli_small_refusals.py:45 | send -, refused | complies: calls == [], with :42 as the positive control on the same recorder.
- test_cli_small_refusals.py:55 | sysrq with a non-ASCII char, refused | complies: calls == [], with :58 as the positive control.
- test_cli_small_refusals.py:58 | sysrq b | complies: asserts /break then /send.
- test_cli_small_refusals.py:64 | purge with an inverted range | complies: calls == [] plus the refusal's unique message.
- test_cli_small_refusals.py:75 | status, no ports | complies: the only request is GET /status.
- test_cli_small_refusals.py:81 | cmd ok, empty data | complies: the /cmd result shape.
- test_cli_small_refusals.py:85 | cmd ok with data | complies.
- test_cli_small_refusals.py:96 | 400 for every path, plot export | complies: the daemon's exact message (server.py:2107); the only request is /plot/export.
- test_cli_small_refusals.py:108 | attach retarget | complies:
  - Dispatches on method like the daemon: GET /ports gives the list, POST /ports gives {"port": status}.
  - The POST answer's alias is fixed at "board", but it only feeds the "attached" line, not the asserted note.
- test_cli_small_refusals.py:112 | attach, new alias | complies (as :108).
- test_cli_small_refusals.py:115 | attach, same target | complies (as :108).
- test_cli_small_refusals.py:142 | ambiguous port, current and older wording | complies: the current wording is exact (server.py:1042-1044); the /ports probe fires only on the older wording.
- test_cli_small_refusals.py:158 | purge --before-days <= 0, refused | complies: seen == [] plus a unique message.
- test_cli_small_refusals.py:170 | dry run, 0 deleted with null ids | complies: the before_ts branch returns ids None when n == 0 (server.py /purge).
- test_cli_small_refusals.py:180 | dry run with an id range | complies.
- test_cli_version_gate.py:31 | recorder, an old /status | complies: asserts paths == ["/status"].
- test_cli_version_gate.py:42 | recorder, a current daemon | complies: asserts one /status; bodies path-dispatched.
- test_cli_version_gate.py:52 | dev version | complies: the real is_newer behaviour on an unorderable string.
- test_cli_version_gate.py:60 | no bounds | complies: asserts paths == ["/lines"].
- test_cli_version_gate.py:68 | inverted bounds | complies: seen == []; the same recorder receives calls in the neighbouring tests.
- test_cli_version_gate.py:149 | plot export --decode against 0.3.0 | complies: asserts paths and that no file was written.
- test_cli_version_gate.py:162 | can dump --csv against 0.3.0 | complies (as :149).
- test_cli_version_gate.py:193 | GATED refused | complies: asserts paths == ["/status"].
- test_cli_version_gate.py:206 | GATED reaching a current daemon | violates (MEDIUM, FB2-1): records the request but asserts only its path.
- test_cli_version_gate.py:228 | no gate without a gated option | complies: asserts /status absent; path-dispatched.
- test_cli_version_gate.py:249 | missing route on 0.3.0 | complies: an unknown route answers 404 {"error": "Not Found"} through the daemon's envelope (server.py:555-557); the subject is the version sentence.
- test_cli_version_gate.py:258 | 404 from a current daemon | complies: a current daemon never 404s /lines/export, but this is the positive control keeping the version sentence off current daemons.
- test_cli_version_gate.py:313 | GATES refused | complies: asserts paths == ["/status"].
- test_cli_version_gate.py:319 | GATES current | complies: asserts one /status.

##### Rulings: c27-injected.txt (0)
None.

##### Not verified
- The whole suite under the FB2-1 mutant; only the 15 files listed were run. No other test file mentions `--eol`, `repeat-ms` or `-p ... plot export` (grep), but a test reaching them another way would not show up in that grep.
- Windows behaviour of the fakes (the _open_append Windows branch was not exercised).

##### Surprising
- test_cli_version_gate.py:75-140 are 66 consecutive blank lines, left behind by removed tests.
- test_cli_send_verdicts.py:234 and :249 re-import httpx inside the test bodies, which already import it at module level.

#### Group C1 (scratch-27-28/out-C1.md)
#### Class 27 sweep, group C1 (HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3)

##### Findings

###### FC1-1 LOW: the update-check URL is pinned by no test; the canned PyPI transport answers every path
- Site: host/tests/test_config_api.py:347 (`httpx.MockTransport(handler)`: `handler` returns `{"info": {"version": "99.0.0"}}` for any method and URL, and the test never reads `calls[0].url`).
- Same shape in test_update_check.py `mock_transport` (group D's file, noted here because it is the other half of the gap): no test in the suite asserts the checker GETs `PYPI_URL`.
- Concrete failure: a wrong `PYPI_URL` (typo, wrong project, `/xml`) makes every real check 404, so `/status` never reports an update, while the suite stays green.
- Driven: in copy-C1, `PYPI_URL = "https://pypi.org/pypi/mcuscope-typo/xml"`, then `pytest tests/test_config_api.py tests/test_update_check.py -q -p no:randomly`: 75 passed. Copy restored.
- Fix shape: one assertion `calls[0].method == "GET" and str(calls[0].url) == uc.PYPI_URL` (here or in test_update_check), or make the handler answer 404 off that URL.

No other site in C1 violates.

##### Per-enumeration counts (filtered with the C1= regex from groups.txt)

| enumeration | expected | got |
|---|---|---|
| c27-patch.txt | 81 | 81 |
| c27-classes.txt | 11 | 11 |
| c27-assign.txt | 4 | 4 |
| c27-canned.txt | 27 | 27 |
| c27-injected.txt | 9 | 9 |

Extra (unenumerated) sites found with:
- `grep -nE "SimpleNamespace\(|Mock\(|MagicMock|mock\.patch|unittest\.mock|Scripted\(|SpyLink|SimEndpoint|ASGITransport|MockTransport|on_break=|_fn=|opener|transport=" <C1 files>`
- `grep -nE "^\s+[a-z_]+(\.[a-zA-Z_]+)*\.(state\.)?[a-zA-Z_]+ = " <C1 files>` (attribute pokes)
- `grep -nE "\bdef (fake|stub)|Fake|Stub|_fake" <C1 files>`
- `grep -n "_ports\[\|_ports.pop" test_decode_per_port.py`
- test_firmware_monitor.py drives firmware/tests/fake_shims.c (a C double of the port shims).
- 13 C1 files have no enumerated site. A scan for monkeypatch/lambda/class/fake/stub/SimpleNamespace/mock found only `monkeypatch.setenv` (real inputs) and pytest `ids=` lambdas: test_dirs_override, test_eol, test_firmware_monitor, test_pane_regex_dialect, test_config_bools, test_config_loader_host_device, test_config_ports_eol, test_daemon_token_exposure, test_plot_export_refusals, test_plot_export_since_id, test_plot_grammar_fixture, test_protocol_strict, test_protocol_tokenizer.

##### Rulings

###### c27-patch.txt (81)
- test_config_api.py:632 | os.replace in replace_atomic: raises PermissionError(13, ..., winerror 5) twice, then the real replace | complies: the WinError 5 sharing violation replace_atomic retries; delegates to the real replace.
- test_config_api.py:638 | os.replace, always PermissionError | complies: a handle that is never released; the real retry re-raises the same type.
- test_config_api.py:672 | config_mod.replace_atomic: records the src name, calls the real one | complies: pass-through spy. It drops `**kw`, but _write_doc passes none.
- test_config_api_revision.py:158 | config_mod._write_doc: sleeps 0.3 s, then the real write | complies: widens the race window, keeps the behaviour.
- test_config_loader.py:158 | serial_link.cached_comports -> [SimpleNamespace(device, serial_number)] | complies: _resolve_device reads only .device and .serial_number, the ListPortInfo fields.
- test_config_loader.py:395 | platformdirs.user_data_dir -> tmp/data | complies: a path redirect like conftest's own (it drops the app suffix, which nothing asserts).
- test_config_loader.py:410 | daemon._serve -> fake_serve running a TestClient over the app | complies: the lifespan still runs, as under uvicorn; the subject is config warnings on /status.
- test_daemon_config_path.py:26 | platformdirs.user_data_dir | complies: path redirect.
- test_daemon_config_path.py:27 | platformdirs.user_config_dir | complies: path redirect; the default-config test reads it back.
- test_daemon_config_path.py:31 | daemon._serve records the app | complies: the subject is config selection before serving; the recording is the "served" observation.
- test_daemon_config_path.py:39 | pidfile.claim spy over the real claim | complies: pass-through, with a positive control (test_a_start_that_gets_past_the_config_does_claim).
- test_daemon_config_path.py:62 | daemon.create_app spy over the real one | complies: pass-through.
- test_daemon_console.py:22 | _stdio.install_console_ctrl_handler records its kwargs | complies: the subject is whether and how daemon.main calls it. Its own behaviour, keep_ctrl_c_ignored included, is pinned in test_stdio.py:281-308 by a recording kernel32.
- test_daemon_console.py:24 | daemon._release_pid_on_terminating_signal -> no-op | complies: keeps real signal handlers out of the test process; not the subject.
- test_daemon_console.py:25 | daemon._port_conflict -> None | complies: fixed port 18558; not the subject.
- test_daemon_console.py:26 | daemon._serve records "serve" | complies: marks the order relative to the install.
- test_daemon_console.py:39 | _stdio._ctrl_handler_ref = None | exempt because a state reset (knob), not a behavioural double.
- test_daemon_console.py:40 | _stdio.have_console -> True | complies: the branch selector under test.
- test_daemon_console.py:45 | _stdio._ctrl_handler_ref = None | exempt because a state reset (knob).
- test_daemon_console.py:46 | _stdio.have_console -> False | complies: the branch selector under test.
- test_daemon_console.py:62 | sys.platform = "win32" | exempt because a knob that reaches the Windows branch; the kernel32 fake below supplies what that branch calls.
- test_daemon_console.py:63 | ctypes.windll = WinDLL() fake | complies: SetConsoleCtrlHandler returns 1 like the real one. Not recording is fine: these tests assert on the handler's event behaviour, and the flag handling is pinned in test_stdio.py.
- test_daemon_console.py:64 | ctypes.WINFUNCTYPE -> identity | complies: the real thunk calls through to the same Python fn with an int event.
- test_daemon_console.py:65 | _stdio._ctrl_handler_ref = None | exempt because a state reset (knob).
- test_daemon_console.py:66 | _stdio.console_close_hook records "hook" | complies: the order (hook before sigint) is the subject.
- test_daemon_console.py:67 | _thread.interrupt_main records "sigint" | complies: stands in for the delivery; the order is the subject.
- test_daemon_console.py:68 | time.sleep records "hold s" | complies: the hold length and its position are the subject.
- test_daemon_console.py:99 | _stdio.console_close_hook = None | exempt because a state reset (knob).
- test_daemon_console.py:100 | daemon.Server = FakeServer | complies: it receives the real uvicorn.Config, and started=True matches a served run. The subject is the hook's effect on timeout_graceful_shutdown.
- test_daemon_process.py:66 | pidfile.pid_file_path raises PermissionError | complies: the real one raises OSError from makedirs on an unusable data dir.
- test_daemon_process.py:74 | _stdio._report_key = "" | exempt because a state reset (knob).
- test_daemon_process.py:75 | daemon._release_pid_on_terminating_signal -> no-op | complies: not the subject (report keying).
- test_daemon_process.py:76 | daemon._port_conflict -> None | complies: the scenario is "the loser passed the probe", which this reproduces exactly.
- test_daemon_process.py:239 | uvicorn.Server.startup raises KeyboardInterrupt | complies: KeyboardInterrupt leaves run() with started False, as a Ctrl-C landing before capture_signals would. Inside capture_signals a real SIGINT only sets should_exit (uvicorn 0.52 handle_exit). So this window is narrower than the docstring suggests, but _serve's check is the subject and the double reaches it the same way.
- test_daemon_process.py:248 | uvicorn.Server.main_loop -> return at once | complies: started True, then the real shutdown.
- test_daemon_startlog.py:21 | platformdirs.user_data_dir | complies: path redirect.
- test_daemon_startlog.py:22 | _stdio._report_key = "" | exempt because a state reset (knob).
- test_daemon_startlog.py:77 | daemon.Server = OneTick (subclass, main_loop returns) | complies: real startup and shutdown run; only the serving loop is skipped.
- test_daemon_startlog.py:103 | pidfile.claim spy over the real one | complies: pass-through, and it is the positive control for the glob.
- test_daemon_startup.py:50 | socket.getaddrinfo -> fixed dual-stack infos | complies: the real 5-tuple shape, carrying the probed port. The test only resolves the one fake host name.
- test_daemon_startup.py:70 | platformdirs.user_data_dir | complies: path redirect.
- test_daemon_startup.py:71 | daemon.create_app raises RuntimeError | complies: any exception after the claim; the subject is the finally.
- test_daemon_startup.py:176 | platformdirs.user_data_dir | complies: path redirect.
- test_daemon_startup.py:177 | daemon._serve -> None | complies: the subject is the startup printout.
- test_daemon_startup.py:207 | daemon._serve -> None | complies: same.
- test_daemon_startup.py:246 | cli.subprocess.Popen raises _Spawned | complies: the "present" arm asserts _Spawned is raised, which is the positive control that the double sits on the spawn path.
- test_daemon_startup.py:307 | daemon._port_conflict -> "busy" | complies: the return shape of the real probe on a conflict.
- test_daemon_startup.py:308 | daemon._serve -> pytest.fail | complies: a tripwire for "must not reach".
- test_daemon_startup.py:352 | CaptureLock.acquire raises OSError(30) | complies: the real acquire raises OSError from makedirs/os.open on a read-only dir. Same signature.
- test_daemon_startup.py:390 | platformdirs.user_data_dir | complies: path redirect.
- test_daemon_startup.py:391 | daemon._port_conflict -> "in use" | complies: as at :307.
- test_daemon_startup.py:402 | platformdirs.user_data_dir | complies: path redirect.
- test_daemon_startup.py:405 | daemon._serve -> pytest.fail | complies: a tripwire.
- test_daemon_startup.py:422 | signal.getsignal -> SIG_DFL | exempt because a knob: it forces the registration attempt a previous in-process main() may have pre-empted. The ValueError comes from the real signal.signal.
- test_decode_per_port.py:265 | server.learn_stored_plot_defs spy over the real one | complies: pass-through, with a positive control (`scans == ["far"]` on the first call).
- test_decode_per_port.py:482 | the same, as a dotted path | complies: pass-through, with a positive control (`calls == [a]` after the pop).
- test_export_lines_can.py:135 | store._EXPORT_PAGE = 2 | exempt because a knob, not a behavioural double. Read at call time (store.py:2782/2792).
- test_flow_cli_windows.py:103 | cli.LINES_PAGE = 5 | exempt because a knob. Referenced only inside cli.py, so the patch reaches every use.
- test_flow_cli_windows.py:206 | Store._window_id_ceiling spy over the real one | complies: pass-through with the real signature (self, until_ts, conn=None); the test's cases include expected-walk counts of 1.
- test_flow_cli_windows.py:226 | cli.LINES_PAGE = 5 | exempt because a knob.
- test_flow_cli_windows.py:270 | cli.LINES_PAGE = 5 | exempt because a knob.
- test_pidfile.py:27 | platformdirs.user_data_dir -> tmp/data/<app> | complies: keeps the app suffix like the real function.
- test_pidfile.py:235 | os.write raises ENOSPC on the pid bytes, real otherwise | complies: the failure a full disk gives the one write.
- test_pidfile.py:236 | os.open tracking the open fds, real open | complies: pass-through bookkeeping for windows_remove.
- test_pidfile.py:237 | os.close tracking, real close | complies: pass-through.
- test_pidfile.py:238 | os.remove refuses an open file (Windows semantics) | complies: it models the one respect under test, and is load-bearing. Driven: moving the remove before the close in claim() fails test_claim_removes_the_record_when_the_pid_write_fails (copy-C1, test_pidfile.py: 1 failed, 21 passed).
- test_pidfile.py:300 | os.kill raises PermissionError | complies: EPERM from another user's process; the POSIX-only skip matches the branch.
- test_pidfile.py:353 | pidfile.read_pid_record: the real read, then a concurrent rewrite after the first read | complies: models the second claimer; claim() sees the same file states as in the real race.
- test_pidfile.py:364 | platformdirs.user_data_dir under a regular file | complies: the real makedirs fails (NotADirectoryError, an OSError).
- test_pidfile.py:377 | os.remove raises PermissionError | complies: the Windows refusal release() must survive.
- test_pidfile.py:389 | pidfile.pid_file_path -> fixed path in an existing dir | complies: the real one only adds the makedirs.
- test_pidfile.py:397 | os.remove recording spy over the real remove | complies: pass-through, and load-bearing. Driven: a remove-and-recreate of our own record in claim() fails test_claim_keeps_a_record_that_is_already_ours (copy-C1: 1 failed, 21 passed).
- test_plot_export_decode.py:306 | store._EXPORT_CHUNK = 7 | exempt because a knob (read at call time, store.py:2714).
- test_plot_export_decode.py:307 | store._EXPORT_PAGE = 7 | exempt because a knob.
- test_plotjuggler.py:385 | pjstream._resolve raises gaierror | complies: the dest "viewer.lan:9870" is well-formed, so the real function passes parse_dest and fails exactly here with the same type.
- test_plotjuggler.py:196 | pjstream.socket.getaddrinfo raises gaierror(-2) | complies: what a dead resolver raises for example.invalid.
- test_plotjuggler.py:200 | pjstream.socket.getaddrinfo = the real one | exempt because a restore, not a double.
- test_plotjuggler.py:296 | setattr(config.plotjuggler, key, value) | exempt because it builds a real Config from test values, not a double.
- test_plotjuggler.py:337 | pjstream._resolve raises gaierror | complies: as at :385 ("resolves.not:9870" is well-formed).
- test_port_column_stored.py:157 | cli.Client.open -> MockTransport answering every path with the /ports body | complies: _stream_port_column makes the one GET /ports. The path is pinned by the real-daemon test in the same file. Driven: with the probe changed to /status, test_a_detached_boards_history_carries_the_port_in_every_cli_text_read fails (1 failed, 13 passed).
- test_protocol.py:802 | protocol-module `int` = recording spy over builtin int, with from_bytes kept | complies: pass-through; `len(derived) >= 10` plus the set equality give it a positive control.

###### c27-classes.txt (11)
- test_daemon_console.py:55 | class K32 (kernel32.SetConsoleCtrlHandler -> 1) | complies: see :63 above.
- test_daemon_console.py:59 | class WinDLL (holds kernel32) | complies: see :63.
- test_daemon_console.py:88 | class FakeServer (uvicorn Server: holds the config, run() fires the hook) | complies: see :100.
- test_daemon_process.py:182 | class _Store (stop_subscribers records the current task), in app.state via SimpleNamespace (:189) | complies: the only store method handle_exit calls. What it records (where it ran) is the subject.
- test_daemon_startlog.py:73 | class OneTick(daemon.Server) | complies: see :77.
- test_daemon_startup.py:223 | class _Spawned(Exception) | exempt because a marker exception raised by the Popen double, not a double itself.
- test_link.py:16 | class _FakeSer (pyserial handle: send_break records the seconds, returns None) | complies: SerialLink dispatches on the device string, which each test passes as the real value for its case. The real send_break also returns None, and a native port does send.
- test_port_health.py:23 | class _Bad (link.write raises SerialTimeoutException), used at :31 and :49 | complies: the exception pyserial raises on a write timeout.
- test_port_health.py:27 | class _Good (link.write succeeds), used at :43 | complies.
- test_port_health.py:303 | class _NoStore (add_line -> {"id": 1}) | complies: the test only asserts the identify task is created; a row dict carrying an id is what callers read.
- test_protocol.py:778 | class Calls(ast.NodeVisitor) | exempt because a source analyser, not a double. Checked: protocol.py has no int() inside a comprehension, so the frame-name match also holds on 3.10/3.11.

###### c27-assign.txt (4)
- test_e2e.py:513 | store.query_can_frames = boom (RuntimeError) | complies: query_can_frames_safe offloads self.query_can_frames, so the instance attribute is what runs, and the {"error": "boom"} body proves it was reached.
- test_e2e.py:530 | port._write_bytes -> time.time() | complies: the real one returns the write's float start time; the subject is resolving a malformed reply.
- test_port_health.py:308 | loud._write_bytes -> time.time() | complies: same.
- test_port_health.py:340 | port._device_present -> fixed bool | complies: it pins the presence answer the real one gives for an existing file on Linux, and on Windows it is the only way to model "present but busy" (the docstring says so).

###### c27-canned.txt (27)
- test_config_api.py:347 | UpdateChecker transport: PyPI JSON, every path 200 | violates: FC1-1 (no test pins the URL; driven).
- test_flow_cli_windows.py:47 | canned(handler=_forward): forwards method, path, params and body to the real in-process app | complies: the answer is the daemon's own. It forces content-type JSON and drops the CLI's headers; the app has no token and dispatches on no header these commands send.
- test_port_column_stored.py:156 | MockTransport, every path -> /ports body | complies: see patch :157 (driven).
- test_port_column_stored.py:169 | run_mcu_canned `lines`, every path -> the lines body | complies: `mcu lines` requests only GET /lines (recorded with a path-logging transport, probe_paths.py in copy-C1). A real row shape; port "" is the daemon's own rows.
- test_port_column_stored.py:173 | same, positive control with two boards | complies.
- test_port_column_stored.py:206 | run_mcu_canned `log export`, dispatching on /ports, /lines/export and the rest | complies: dispatches on the path and asserts "/lines" not in paths. Note: a /ports -> /status mutant still passes this test (single-board streaming gives the same outcome); the real-daemon test catches it.
- test_port_health.py:94 | `status`, every path -> the status body | complies: only GET /status is requested (recorded). Fields the body omits are all read with .get and defaults, the older-daemon shape.
- test_port_health.py:104 | same, a healthy port | complies.
- test_port_health.py:129 | `can tx --retry-ms`, _busy_then_ok | complies: asserts path == /cmd. The err/ok bodies match serial_link.py:1265-1281 field for field, and 6 is `busy` in protocol.py.
- test_port_health.py:134 | `can tx`, no retry | complies: same handler; rc 1, "ERR 6 busy", one call.
- test_port_health.py:138 | `cmd --retry-ms 100` | complies: same handler.
- test_port_health.py:151 | `cmd x --retry-ms 500`, not_busy answering every path with badarg | complies: only POST /cmd is requested (recorded); the body is the real err shape.
- test_port_health.py:165 | `plot channels`, every path -> channels | complies: only GET /plot/channels is requested (recorded); --active is filtered client-side (cli.py:2417).
- test_port_health.py:168 | `--active 60` | complies.
- test_port_health.py:171 | `--active 1` | complies.
- test_port_health.py:174 | `--json --active 60` | complies.
- test_port_health.py:194 | `lines --decode`, dispatching on the match param | complies: it records every param set and asserts the priming bounds.
- test_port_health.py:202 | `tail -n 5 --decode` | complies: same handler; asserts since_id on the priming query.
- test_port_health.py:219 | `lines --to`: records params, every path -> the lines body | complies. The /status version probe gets a body without `version`, which older_daemon reads as "not old", so the gate passes. That shape no daemon produces, but the gate is not the subject (test_cli_version_gate, group B2), and the query assertion filters out the parameterless probe.
- test_port_health.py:226 | `--json lines --to` | complies: same.
- test_port_health.py:255 | `lines --last-ms --limit 5000`, _canned_lines queue | complies: the recorded run makes GET /lines only, so no probe consumes a page, and len(seen)==2 would catch one.
- test_port_health.py:266 | `--json lines --limit 0` | complies.
- test_port_health.py:273 | `plot channels --active 0` | complies: refused before any request.
- test_port_health.py:281 | `status` with a session block | complies: the real session shape (id, name, note, started_ts, ended_ts, start_id, end_id, auto).
- test_port_health.py:534 | `status`, disconnected with reason, looped | complies: real disconnect_reason values.
- test_port_health.py:541 | held + manual | complies.
- test_port_health.py:547 | disconnected, no reason (older daemon) | complies: the older-daemon shape is the subject.

###### c27-injected.txt (9)
- test_daemon_startup.py:100 | open_link_fn = daemon._start_sim(config) | exempt because it is the production opener, not a double (the test pins its dispatch on sim://).
- test_daemon_startup.py:107 | create_app(open_link_fn=that opener) | exempt: the same production opener.
- test_daemon_process.py:158 | Popen preexec_fn sets SIGHUP to SIG_IGN in the child | exempt because not a double: the real disposition `nohup` gives.
- test_port_health.py:393 | opener raising OSError("could not open port: no such file") | complies: _reader catches Exception and picks the reason from _device_present, not from the exception type, and the node is really absent.
- test_port_health.py:414 | opener raising OSError(busy), present=True | complies: same dispatch; presence pinned (see assign :340).
- test_port_health.py:452 | _drop_then_gate opener: a SourceLink over Scripted that dies with SerialException, then blocks | complies: a real SourceLink. SerialException is what pyserial raises on a dropped port, and the gate only holds the retry.
- test_port_health.py:469 | same opener | complies.
- test_port_health.py:495 | opener: busy until allowed, then a quiet SourceLink | complies.
- test_port_health.py:515 | opener -> _quiet_link(device) | complies: a real SourceLink carrying the device.

###### Unenumerated sites
- test_decode_per_port.py:29 | board(): an unstarted SerialPort inserted straight into manager._ports (a hand "attach") | complies. It skips what real attach does (start, priming, carried counters, the detached_meta drop in POST /ports). But feed() drives the real _store_rx_line and the server reads only plot_channel_meta_by_port (serial_link.py:1470-1472). The one test that needs a real re-attach (:325) uses POST/DELETE /ports.
- test_decode_per_port.py:103, 270, 383, 399, 417, 433, 462 | hand "detach" by `del manager._ports[alias]` | complies: plot_channels depends only on membership; the ports removed are unstarted board() ports, so no reader thread leaks.
- test_decode_per_port.py:446/450, 486/490 | the stack's own running port popped and reinserted | complies: restored in finally; the subject is manager membership.
- test_decode_per_port.py:414/420 | serial_link.PLOT_DEF_LOOKBACK = 10 by direct assignment, restored in finally | exempt because a knob. It reaches learn_stored_plot_defs (read at call time, serial_link.py:88) and is load-bearing: with 20000 the def would be found and the assertion fail.
- test_e2e.py:72/78 | app.state.shutdown_cb = threading.Event.set (the real one raises SIGTERM) | complies: the subject is that /shutdown accepts and invokes the callback; the event is its observation.
- test_e2e.py:86 | httpx.ASGITransport(client=("203.0.113.5", 4444)) | complies: the real app; the fake client address is the respect under test.
- test_daemon_startup.py:108 | httpx.ASGITransport over the real app | complies.
- test_port_column_stored.py:69-72 | SimpleNamespace request (app.state.ports.list(), app.state.store.stored_ports()) for server._several_ports | complies: _several_ports reads exactly .alias from list() and the names from stored_ports() (server.py:3432).
- test_link.py:51, 60 | SourceLink(Scripted(idle_after=True)) | complies: calls the shared helper (support.py, not mine); SourceLink is production.
- test_port_health.py:369 | _quiet_link: SourceLink over Scripted([]) | complies: as above.
- test_port_health.py:31, 43, 49 | port._link = _Bad()/_Good() | complies: see classes :23/:27.
- test_port_health.py:584-587 | port.disconnect_reason / connected set by hand | exempt because a state set-up, not a double. Note for class 63: connected with a stored "manual" is a state the producer no longer reaches (connect clears the reason, :495), but the test targets status()'s mask on its own.
- test_plot.py:234 | store._next_id bumped after a direct insert | exempt because state set-up that keeps the writer's sequence consistent with the inserted row.
- test_plot.py:345 | store._retention_days = 0 | exempt because a knob.
- test_config_api.py:318-323, 349-350 | UpdateChecker enabled/latest/checked_at set by hand, installed on app.state | exempt because knobs overriding the suite's environment veto. Its transport is the FC1-1 site.
- test_config_api.py:697/702 | app.state.config_path pointed under a file | exempt because a knob producing a real OSError.
- firmware/tests/fake_shims.c (driven by test_firmware_monitor.py) | C doubles of monitor_port_t and the mon_* bus shims | complies at contract level against monitor.h:73-186. uart_write is whole-line atomic with a reject mode. The deliberate slip modes (over-reporting read, short I2C/SPI fill, partial CAN pop without pre-zero, info without NUL) model the documented shim failures. Reads come through a 64-byte stage (monitor.c:28), so line assembly across reads is exercised. mon_can_tx has no error mode, but its return passes straight through (monitor_cmds.c:177), the same path gpio's BADARG covers. Reasoned only.

##### Notes (outside class 27)
- An in-process `daemon.main()` that does not patch `_release_pid_on_terminating_signal` installs real SIGTERM/SIGHUP handlers in the pytest process and never restores them. conftest has no signal restore. Affected: test_config_loader.py:410's test, test_daemon_config_path.py, test_daemon_startup.py `_startup_output`/`_startup_with_config`, test_daemon_startlog.py corrupt-capture. The handlers end the process much as SIG_DFL would, so this is harmless today; test_daemon_startup.py:422 already works around it.
- The real `_serve` (test_daemon_process, test_daemon_startlog) leaves `_stdio.console_close_hook` bound to a dead server. test_daemon_console resets it; nothing else does.

#### Group C2 (scratch-27-28/out-C2.md)
#### Class 27 sweep, group C2

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3 (checked).
Files: test_reconnect.py, test_render_line_breaks.py, test_scaffold.py, test_security.py, test_serial_link_*.py, test_server_*.py.
Mutants ran in copy-C2 (rsync of the repo). Each mutated file was restored with `cp` from the repo afterwards, and `grep -c MUTANT` returned 0.

##### Findings

###### FC2-1 LOW: test_reconnect.py:696 `_Sock` ignores `timeout`
- The double stands in for pyserial's socket:// `Serial`.
- The real `read(n)` loops on `select(timeout.time_left())` until it has n bytes or the timeout runs out (protocol_socket.py:148-189). With timeout 0.2 and n = 8192, every drain waits the full 0.2 s.
- `_Sock.read` returns whatever it has at once, whatever `timeout` is set to.
- So `test_socket_drain_does_not_trust_in_waiting` passes when `ser.timeout = 0` is removed (link.py:149). It asserts only that the first read was sized over 1 byte and that the timeout ends at 0.2, and 0.2 is also the class default.
- Driven: replaced link.py:149 with `pass`.
  - `pytest tests/test_reconnect.py::test_socket_drain_does_not_trust_in_waiting`: 1 passed.
  - `tests/test_link.py`: 5 passed.
  - `tests/test_sim_tcp.py`: 1 failed (`test_a_port_captures_over_a_real_socket_connection`, KeyError 'data' at :350). The baseline for that test passes. So only a real-socket test in group D catches it.
- Fix: have `_Sock.read` record `(n, self.timeout)`, and assert that every sized read ran at timeout 0.
- Also, this test and its two siblings (`_Native`, `_NoCancel`) pin link.py but live in test_reconnect.py.

###### FC2-2 MEDIUM: test_serial_link_devices.py:101 realpath trap, with no positive control anywhere
- `os.path.realpath` is replaced with `pytest.fail`, and `_by_id_map` with `dict` (an empty map).
- The test asserts that realpath is not called and `by_id is None`. Nothing in the suite shows that `by_id` resolves when a map exists.
  - test_e2e.py:102 and test_cli.py:726 check only that the key is present.
  - `grep -rn by_id host/tests` finds no other assertion on its value.
- So the trap test passes on code that never reports a by-id path.
- Driven: server.py:3797 changed to `"by_id": None,`. `pytest tests/test_serial_link_devices.py`: 6 passed.
- Fix: a sibling case with a non-empty `_by_id_map` (`{realpath: "/dev/serial/by-id/x"}`) asserting `dev["by_id"]`. That is also the positive control for the trap.

###### FC2-3 LOW: test_server_scope.py:25 `FloorClock` dispatches on the caller's function name
- It stands in for store's `time` module. `time()` advances 10 s only when `sys._getframe(1).f_code.co_name == "_window_floor"`.
- No test asserts that the clock ever advanced. After a pure rename of `_window_floor`, both tests that use it (sites :60 and :77) stop detecting the defect they were written for.
- Driven:
  - Mutant A2: server.py:3298 scope `floor_ts` re-derived by a second `store._window_floor(last_ms, None)`. `pytest test_server_scope.py::test_plot_export_streams_the_window_its_count_guarded ::test_a_retrospective_assert_judges_every_pattern_over_one_window`: 2 failed.
  - Mutant B2: A2 plus `sed s/_window_floor\b/_window_floor_at/g` over server.py and store.py. The same two tests: 2 passed.
- Fix: count the advances in `FloorClock` and assert `>= 1` after the request, so a rename fails loudly.

##### Counts

Filter: `grep -E '^host/tests/(test_reconnect\.py|test_render_line_breaks\.py|test_scaffold\.py|test_security\.py|test_serial_link_[a-z_]*\.py|test_server_[a-z_]*\.py):'` on c27-patch/classes/assign. The same regex without the prefix was used on canned/injected.

- patch 70, classes 32, assign 10, canned 2, injected 15. All match the expected counts.
- Extra sites: 45 lines (listed at the end).
- test_render_line_breaks.py and test_security.py have no enumerated site. test_security.py has one extra site.

##### Rulings: c27-patch (70)

- test_reconnect.py:62 | cached_comports -> [_Info('/dev/x','SN1')] (pyserial enumeration behind the TTL cache) | complies: `_resolve_device` reads only .serial_number/.device; the list shape matches the real one
- test_reconnect.py:85 | cached_comports -> [_Info('COM12')] | complies: the Windows branch reads .device through `_normalize_com` (skipped on Linux)
- test_reconnect.py:97 | cached_comports, presence toggled by an Event | complies: models a replug; the serial_number branch reads the same two fields
- test_reconnect.py:125 | cached_comports -> [] | complies: no ports is a real answer
- test_reconnect.py:136 | cached_comports -> [] | complies
- test_reconnect.py:154 | _comports_cache reset to (0.0, []) | exempt: restores the module's own initial value, not a behavioural double
- test_reconnect.py:155 | list_ports.comports -> recording lambda returning [] | complies: production calls comports() with no args and filters the list; the recording is asserted beside the real cache logic
- test_reconnect.py:171 | _comports_cache reset | exempt, as :154
- test_reconnect.py:181 | list_ports.comports -> slow_scan returning an older list | complies: a blocking enumeration is the real shape; `_is_absent_uart` runs over it as over real results
- test_reconnect.py:186 | list_ports.comports -> lambda returning a new list | complies
- test_reconnect.py:811 | cached_comports, presence toggled by the waiter | complies (as :97)
- test_reconnect.py:871 | JOIN_TIMEOUT 0.2 | exempt: a knob read at call time in stop(); the subject (a reader outliving the join) is unchanged
- test_reconnect.py:906 | JOIN_TIMEOUT 0.2 | exempt, as :871
- test_reconnect.py:1176 | RX_QUEUE_MAX 10 | exempt: a knob read at runtime by both `_on_bytes` (overflow) and `_consume` (half-drain rearm), so both halves see the same value
- test_reconnect.py:1367 | JOIN_TIMEOUT 0.2 | exempt, as :871
- test_reconnect.py:1210 | MAX_PORTS 3 | exempt: a knob read at call time by attach; `_write_pool` was sized at import, is unaffected and is not under test
- test_reconnect.py:313 | p.classify wrapped to raise ValueError on "poison" | complies: delegates for every other line; the raise is the fault under test, and `_store_rx_batch` catches Exception, so the type does not select the path
- test_reconnect.py:382 | cached_comports -> raises OSError | complies: comports can raise (setupapi, sysfs); `_reader` catches Exception, and the asserted row "device lookup failed" is unique to that branch
- test_reconnect.py:485 | prime_plot_defs -> raises RuntimeError | complies: the real prime can raise (StoreError is a RuntimeError); attach catches nothing, so the type selects no path
- test_scaffold.py:114 | the module's own `_console_script` -> None | complies: None is that helper's "not found" answer; the test asserts the fail outcome type
- test_serial_link_attach.py:105 | prime_plot_defs -> slow_prime gated on self.device == OLD | complies: the real prime only reads the store; its ordering against the manager lock is the subject
- test_serial_link_attach.py:382 | prime_plot_defs -> slow_prime | complies
- test_serial_link_attach.py:383 | SerialPort.start -> counting_start wrapping the real one | complies: records and delegates
- test_serial_link_attach.py:415 | SerialPort.start -> counting_start | complies: this is the positive control for :383
- test_serial_link_attach.py:40 | prime_plot_defs -> slow_prime | complies
- test_serial_link_attach.py:265 | prime_plot_defs -> prime_then_redefine | complies: runs the real prime, then injects the race (a `!pd` through the old port)
- test_serial_link_attach.py:267 | prime_plot_defs <- the real `prime` | exempt: re-installs the real implementation
- test_serial_link_attach.py:347 | prime_plot_defs -> slow_prime | complies
- test_serial_link_devices.py:33 | cached_comports -> [_InfoWithDescription] | complies: port_identity calls it with no args and reads .device/.description, both present
- test_serial_link_devices.py:39 | cached_comports -> [] | complies
- test_serial_link_devices.py:44 | cached_comports -> [_InfoWithDescription('COM7', ...)] | complies
- test_serial_link_devices.py:51 | cached_comports -> raises OSError | complies: port_identity's `except Exception` covers the real setupapi failure
- test_serial_link_devices.py:69 | server.cached_comports -> slow_scan (2 s, []) | complies: `_enumerate_devices` calls it with no args on a worker thread; blocking is the real shape
- test_serial_link_devices.py:99 | server.cached_comports -> [_Info()] | complies: carries exactly the five attributes `_enumerate_devices` reads
- test_serial_link_devices.py:100 | server._by_id_map -> dict ({}) | complies: {} is the real answer off Linux or with no /dev/serial/by-id
- test_serial_link_devices.py:101 | os.path.realpath -> pytest.fail trap | violates: FC2-2 (a non-occurrence trap with no positive control)
- test_serial_link_devices.py:132 | builtins.open -> fake_open rebasing /sys/class/tty/ into tmp | complies: delegates every call to the real open, so production's OSError handling runs on real files
- test_serial_link_devices.py:167 | cached_comports -> [_Info('/dev/ttyFAKE7','SN1')] | complies, harmless divergence: _Info has no .description, so port_identity raises AttributeError inside its `except Exception` and returns description None (a real ListPortInfo always has one); the test asserts only `status()["device"]` and `port.device`, which come from `_resolve_device` and realpath
- test_server_export_pool.py:57 | Store.export_session_db -> _Build | complies: calls on_open before any work, as the real copy does, and raises `_ExportAbandoned` from it; the temp file comes from the server's mkstemp; connects :memory: instead of dest_path, which the asserted file removal does not depend on (the Windows open-handle unlink is not modelled, reasoned only)
- test_server_export_pool.py:214 | export_session_db -> _BlockedBuild (the `blocked` fixture) | complies: skips on_open, so an abandon cannot stop it; its one user (`test_wait_is_a_declared_parameter...`) abandons nothing and asserts status codes
- test_server_export_pool.py:244 | Store.open_lines_export -> async fn returning an endless generator | complies: the real returns a generator for a file-backed capture; endless rows make the member write reach `job.checkpoint()`, the subject; the caplog positive control is present
- test_server_export_pool.py:303 | export_session_db -> _GatedBuild | complies: an admission test with no abandon; the bundle path zips the empty mkstemp file, and only status is asserted
- test_server_export_pool.py:311 | EXPORT_QUEUE_MAX 0 | exempt: a knob read at runtime by `_admit_and_build`; EXPORT_WAITERS_MAX was fixed at import and is unaffected
- test_server_export_windows.py:43 | store._EXPORT_PAGE 2 | exempt: a knob read at call time by the lines and can iterators. It does not reach /plot/export (which pages on _EXPORT_CHUNK), but that loop's final empty fetch still exposes a per-page derivation. Driven: `_window_id_ceiling` called per iteration in iter_plot_export gives "/plot/export derived the ceiling 3 times", 1 failed
- test_server_export_windows.py:51 | Store._window_id_ceiling -> spy | complies: records and delegates
- test_server_exports.py:74 | export_session_db -> _BlockedBuild | complies: skips on_open, so an abandon never stops the build, and `test_a_cancelled_export_removes_its_copy_when_the_build_returns` tests exactly that order (finish after abandon), with a positive control that the copy existed; the mid-copy interrupt is covered by :246 over the real method
- test_server_exports.py:246 | export_session_db -> _SlowCopy wrapping the real one | complies: runs the real copy and chains the job's handler through _ProgressHook, so the real interrupt fires; "interrupted" is asserted
- test_server_exports.py:339 | open_lines_export -> endless | complies (as export_pool:244)
- test_server_exports.py:437 | store.iter_{plot,lines,can}_export -> endless generator with a finally | complies: the instance attribute is reached through the real `open_*_export` (the stack is file-backed, function-scoped); release in finally matches how the real iterators close their connection; `started` is the positive control
- test_server_guards.py:85 | WS_KEEPALIVE_S 0.2 | exempt: a knob read at runtime in the pump (server.py:2218)
- test_server_guards.py:148 | _LOOPBACK_CLIENTS frozenset() | complies: lifts the exemption at all three read sites (server.py:817/1122/1222); the 101 with the token is the positive control
- test_server_lifespan.py:56 | stop_all / aclose / PlotJugglerStreamer.close -> raise RuntimeError | complies: fault injection matching each step's sync or async shape; the "daemon start" row is the positive control
- test_server_lifespan.py:127 | prime_plot_defs -> slow_prime then the real one | complies
- test_server_live_verdicts.py:324 | _search_batch / _scan_batch -> stuck | complies: same signature, and returns each one's no-match value (None, []); `entered` is the positive control
- test_server_live_verdicts.py:351 | Store.subscribe -> maxsize forced to 4 | complies: forwards the port filter; drops `as_json`, which CaptureWatch (server.py:2477) never passes (the /ws pump does, and that would fail loudly); the shrink is the subject
- test_server_live_verdicts.py:482 | _scan_batch -> stall wrapper | complies: delegates unless a pattern is armed
- test_server_live_verdicts.py:483 | LIVE_SCAN_GRACE_S 0.1 | exempt: a knob read at call time (server.py:2392)
- test_server_offloop.py:36 | Store._query_lines_threadsafe -> spy | complies
- test_server_offloop.py:61 | server._search_batch -> spy | complies
- test_server_offloop.py:95 | Store.count_lines -> spy (keeps the last thread) | complies: the only caller is count_lines_safe (server.py:1484/1790/3119, one call per request), so the last call is the only call
- test_server_offloop.py:155 | Store.query_lines -> spy | complies: records every thread; the "plain poll stays inline" control is present
- test_server_purge_and_sessions.py:139 | store.before_ts_span_safe -> the real one, then a late add_line | complies: wraps the real one and injects the race row
- test_server_purge_and_sessions.py:159 | same | complies
- test_server_purge_and_sessions.py:69 | Store.start_session -> a row, then the real one | complies
- test_server_request_validation.py:70 | WS_KEEPALIVE_S 0.2 | exempt: a knob
- test_server_scope.py:60 | mcuscope.store.time -> FloorClock | violates: FC2-3
- test_server_scope.py:77 | mcuscope.store.time -> FloorClock | violates: FC2-3
- test_server_scope.py:148 | store.stop_session -> slow first, then the real one | complies
- test_server_ws.py:37 | WS_KEEPALIVE_S 0.1 | exempt: a knob
- test_server_ws.py:100 | store.take_dropped -> raises RuntimeError | complies: fault injection for "the pump died"; the real one never raises, but the subject is the receive loop's reaction, and ConnectionClosed is asserted

##### Rulings: c27-classes (32)

- test_reconnect.py:35 | _Info: ListPortInfo (device, serial_number) | complies: used only by the presence and cache tests, which read those two fields; never reaches port_identity
- test_reconnect.py:837 | _WedgedLink(Link): a native link whose read blocks without a Python lock and cannot be cancelled | complies: the uncancellable blocked read is the subject, and the test asserts the thread is still alive, so a cancelling double would show. After release it returns b"", where a closed pyserial port raises PortNotOpenError; the reader's `except Exception` would turn that into a withheld sys row, which does not affect the asserted close
- test_reconnect.py:1247 | _NoStore: Store.add_line for the tx row | complies: returns a row with an id; `fail` raises RuntimeError where the real one raises StoreError (a subclass); send_command's cleanup catches BaseException
- test_reconnect.py:1259 | _Unretrieved: an observer of the loop's exception handler | exempt: an observer, not a stand-in. Positive control driven: removing `_discard_pending_future(fut)` from send_command's write-failure path makes `test_a_disconnect_during_a_command_leaves_no_unretrieved_future` fail (1 failed)
- test_reconnect.py:696 | _Sock: pyserial socket:// Serial | violates: FC2-1
- test_reconnect.py:721 | _Native: a native pyserial Serial | complies: in_waiting is a byte count and read(n) returns what is waiting; a read not sized from in_waiting would show in `asked`
- test_reconnect.py:742 | _NoCancel: a URL-handler Serial | complies: protocol_socket and rfc2217 `Serial` define neither cancel method (only serialposix.py:604 and serialwin32.py:463 do)
- test_reconnect.py:938 | _LiveButRefusing: a loop whose call_soon_threadsafe raises while open | exempt: fault injection for `_post`'s re-raise branch. asyncio raises RuntimeError there only through `_check_closed`, so the state is one asyncio does not produce (class 63 shape; the branch it pins is defensive)
- test_reconnect.py:992 | _AngryCancel(SpyLink): cancel_* raise | exempt: fault injection on the reader and stop() guards; no shipped Link reaches it (SerialLink suppresses internally, SourceLink returns False)
- test_reconnect.py:1142 | _Broken: a write that raises SerialException | complies: pyserial raises PortNotOpenError or SerialException on a dead handle; `_write_bytes` dispatches on (SerialException, OSError), and "write failed" is unique to that branch
- test_reconnect.py:1369 | _ClosedHandle: a write that raises once closed | complies (as :1142)
- test_serial_link_attach.py:71 | _Once: SourceLink source that emits once | complies: follows the feed/poll contract; identify=False, so no reply is needed
- test_serial_link_attach.py:193 | _Pipe: SourceLink source with fail_writes | complies: raises SerialTimeoutException('Write timeout'), pyserial's own type and text
- test_serial_link_attach.py:424 | _Wedged(Link): an uncancellable read released by close | complies: a cancellable double would never reach stop()'s close branch, which is the subject
- test_serial_link_devices.py:21 | _InfoWithDescription: ListPortInfo | complies: carries the two fields port_identity reads
- test_serial_link_devices.py:147 | _Info: ListPortInfo (device, serial_number) | complies, harmless divergence (see patch :167)
- test_serial_link_devices.py:95 | _Info (class attributes): ListPortInfo | complies: carries the five attributes `_enumerate_devices` reads
- test_serial_link_rx_framing.py:133 | _Once | complies
- test_serial_link_rx_framing.py:168 | _StalledStore: submit_line_nowait/submit_line/add_line | complies: raises asyncio.QueueFull as the real put_nowait does (store.py:1232), submit_line blocks as a full queue does, and rows carry an id
- test_serial_link_rx_tokens.py:40 | _Rows: Store.add_line | complies
- test_serial_link_tx.py:22 | _RowStore: Store.add_line | complies: fail raises RuntimeError (StoreError's base); the test matches on its own text
- test_serial_link_tx.py:36 | _Wire: a connected Link's write/send_break | complies: returns at once where a real write can block; the tests assert pool placement and stamp order, not duration; `_write_bytes` and `_break_locked` call only these two methods
- test_serial_link_tx.py:180 | _SlowText: pyserial write-timeout exception | complies: a SerialException with the same str as SerialTimeoutException('Write timeout'); the blocking __str__ is the hook for the race
- test_serial_link_tx.py:196 | _FailingLink: a link whose write times out | complies: raises SerialTimeoutException('Write timeout') as pyserial does
- test_serial_link_tx.py:437 | _StuckLink: a link for stop()'s close branch | complies: stop() calls only cancel_read and close
- test_serial_link_tx.py:444 | _StuckReader: a reader past its join deadline | complies: join returns without waiting and is_alive stays True; the subject is the close after the join
- test_server_export_pool.py:27 | _Build | complies (see patch :57)
- test_server_export_pool.py:286 | _GatedBuild | complies (see patch :303)
- test_server_exports.py:50 | _BlockedBuild | complies (see patch :74)
- test_server_exports.py:186 | _ProgressHook: the job side of sqlite3's one-handler-per-connection rule | complies: captures the job's handler so _SlowCopy's crawl calls it, preserving the real interrupt
- test_server_exports.py:196 | _SlowCopy | complies: wraps the real method
- test_server_scope.py:25 | FloorClock | violates: FC2-3

##### Rulings: c27-assign (10)

- test_reconnect.py:1324 | port._write_bytes -> lambda returning time.time() | complies: the real one returns the write's start time; the subject is future consumption
- test_reconnect.py:977 | port._store_sys -> wedged | complies: a store that never answers; stop()'s bounded barrier is the subject
- test_reconnect.py:1048 | port._store_rx_batch -> wedged_store | complies, harmless divergence: on cancellation the real one requeues what it never submitted, while this double swallows its first burst uncounted. The test asserts rx_dropped == 50 (the second burst only). The requeue branch is covered by test_serial_link_rx_framing.py `test_lines_in_a_consumer_batch_cancelled_by_detach_are_counted` (_StalledStore, 490 dropped)
- test_reconnect.py:1121 | store.submit_line_nowait -> the real one, then a failed future for "<7 " | complies: the real row still queues; the failed future stands in for a storage failure, and `_settle_rx_line` catches Exception
- test_reconnect.py:1310 | port._write_bytes -> posts the disconnect, then raises PortError | complies: PortError is what the real one translates a write failure into
- test_serial_link_rx_tokens.py:56 | port.send_command -> {"status","data"} | complies: `_identify` reads only status and data, and calls it positionally
- test_serial_link_tx.py:406 | port._write_bytes -> blocking_write | complies
- test_serial_link_tx.py:511 | port._write_bytes -> slow_write | complies
- test_serial_link_tx.py:361 | port._write_bytes -> blocked_write | complies
- test_server_live_verdicts.py:421 | port.send_command -> answered_at_the_deadline | complies: an ok envelope. line_id None is not producible on the real ok path, and seq 0 is below real seqs, but /assert reads only `status` (server.py:3059/3154). No tx row is written, which is harmless because the window judges rx lines

##### Rulings: c27-canned (2)

- test_scaffold.py:137 | MockTransport answering every path 200 with a /status body | complies: the child runs `mcu status` and asserts rc 0, "mcuscoped 0.4.0" and the import set, so answering every path does not matter for this subject. The body lacks fields the daemon always sends (session, write_errors): a class 63 shape, harmless here
- test_scaffold.py:160 | recorder(lines={...}) | complies: asserts `paths(seen) == ["/lines"]`

##### Rulings: c27-injected (15)

- test_reconnect.py:565 | open_link_fn -> SpyLink over Scripted | complies: one fixed device per port, so the real opener's dispatch on the device string is not in play
- test_reconnect.py:654 | open_link_fn -> SourceLink(Scripted) | complies: its SerialException from poll matches pyserial's read failure
- test_reconnect.py:882 | open_link_fn -> _WedgedLink | complies
- test_reconnect.py:920 | open_link_fn -> slow_opener blocking until stop() returned | complies: models a socket:// connect running past the join deadline
- test_reconnect.py:1012 | open_link_fn -> _AngryCancel | complies (fault injection as classes :992)
- test_reconnect.py:1084 | open_link_fn -> SourceLink(Scripted) | complies
- test_reconnect.py:1384 | open_link_fn -> _ClosedHandle | complies
- test_serial_link_attach.py:31 | open_link_fn -> _never_opens (OSError) | complies: every device is a nonexistent path, for which pyserial raises SerialException (an OSError); the reader catches Exception
- test_serial_link_attach.py:87 | open_link_fn -> _never_opens | complies
- test_serial_link_attach.py:162 | open_link_fn -> SourceLink(_Once) | complies: link.device stays "sim://" while the port's device is OLD, but nothing reads link.device (port_identity takes the reader's dev)
- test_serial_link_attach.py:217 | open_link_fn -> SourceLink(pipe, device=dev) | complies
- test_serial_link_attach.py:483 | open_link_fn -> the same _Wedged every time | complies: only one open happens before stop
- test_serial_link_devices.py:172 | open_link_fn -> SourceLink(Scripted idle) | complies
- test_serial_link_rx_framing.py:155 | open_link_fn -> SourceLink(_Once) | complies
- test_serial_link_rx_framing.py:199 | open_link_fn -> SourceLink(_Once(data)) | complies

##### Extra sites (45), not caught by the enumerations

Greps used, over the group's files:
- `grep -nE "SimpleNamespace|MagicMock|Mock\(|mock\.|patch\(|__setattr__|_link *=[^=]|_store *=[^=]|_loop *=[^=]|sys\.modules\[|setenv|lambda"`
- `grep -nE "^\s*[a-z_]+(\.[a-z_]+)+ = [^=]"`, excluding `self.` and the counter fields `lines_rx/lines_tx/rx_dropped/_seq/held/connected/target`, which are state set-up, not doubles
- `grep -nE "_retry_wait\(|SerialPort\((None|_|[a-z_]+\(\))"`
- `grep -nE "_pending\[[^]]*\] *=|_rx_lines\.extend|_consumer_task(\.cancel\(\)| = loop)|store\._broadcast\(|ASGITransport\([^)]*client="`
- a manual read of test_security.py

- test_scaffold.py:138 | `cli.Client.open = lambda` inside the CHILD script | complies: pairs with canned :137 (same ruling)
- test_reconnect.py:1147 | port._link = _Broken() | complies: `_write_bytes` gates only on `_link`
- test_serial_link_tx.py:53 | port._link = _Wire | complies: as :1147
- test_serial_link_tx.py:216 | port._link = _FailingLink(_SlowText) | complies
- test_serial_link_tx.py:237 | port._link = _FailingLink | complies
- test_serial_link_tx.py:261 | port._link = _FailingLink (old) | complies
- test_serial_link_tx.py:263 | port._link = _FailingLink (reopened) | complies: models the reader's reopen before the loop runs `_on_disconnect`
- test_serial_link_tx.py:470 | port._link = _StuckLink | complies
- test_serial_link_tx.py:471 | port._thread = _StuckReader | complies (see classes :444)
- test_serial_link_tx.py:165 | store._conn = CommitBoom | complies: the support helper (the caller rules on the class); the test asserts StoreError and the counter
- test_server_status.py:28 | app.state.store._conn = CommitBoom | complies: as tx:165
- test_serial_link_attach.py:229 | pipe.fail_writes = True | exempt: configures the double
- test_serial_link_attach.py:234 | pipe.fail_writes = False | exempt: configures the double
- test_server_shutdown.py:277 | stack._sim_args.drop_response | exempt: configures the real simulator, which reads args live (sim.py:201)
- test_server_shutdown.py:340 | same | exempt: as :277
- test_server_live_verdicts.py:481 | scan.patterns = set() | exempt: configures the stall double
- test_server_live_verdicts.py:492 | stall.patterns | exempt: as :481
- test_server_live_verdicts.py:502 | stall.patterns | exempt: as :481
- test_server_live_verdicts.py:517 | stall.patterns | exempt: as :481
- test_server_live_verdicts.py:528 | stall.patterns | exempt: as :481
- test_server_live_verdicts.py:356 | store._broadcast(synthetic row: id/port/dir/chan/raw, no ts/seq) | complies: CaptureWatch reads none of the missing fields on this path (it would raise loudly if it did); the test asserts only `dropped > 0`
- test_server_ws.py:77 | SimpleNamespace(writable=Event) standing in for WebSocketsSansIOProtocol | complies: the installed pause_writing/resume_writing touch only `self.writable`
- test_reconnect.py:45 | SerialPort(None, None, ...) in `_port` | complies: presence and backoff touch neither store nor loop
- test_reconnect.py:936 | SerialPort(None, closed loop) | complies: a real closed loop
- test_reconnect.py:947 | SerialPort(None, _LiveButRefusing()) | exempt (see classes :938)
- test_reconnect.py:1293 | SerialPort(_NoStore(...)) | complies (see classes :1247)
- test_reconnect.py:1323 | SerialPort(_NoStore()) | complies
- test_serial_link_tx.py:214 | SerialPort(None, None) | complies: `_write_bytes` and status touch neither
- test_serial_link_tx.py:234 | SerialPort(None, None) | complies: `_on_disconnect` with connected False spawns no row, so the loop is never touched; the asserted write health does not depend on it
- test_serial_link_tx.py:259 | SerialPort(None, None) | complies: as :234
- test_serial_link_rx_tokens.py:51 | SerialPort(_Rows()) | complies
- test_reconnect.py:789 | `_retry_wait(..., never_stops)` | complies: the documented `wait` seam; same contract as Event.wait (True means stop)
- test_reconnect.py:790 | never_stops | complies
- test_reconnect.py:792 | stops | complies
- test_reconnect.py:799 | third_wait_stops | complies
- test_reconnect.py:823 | waiter (flips presence) | complies
- test_reconnect.py:1268 | loop.set_exception_handler(recorder) | exempt: observer (see classes :1259, positive control driven)
- test_serial_link_attach.py:531 | loop.set_exception_handler(recorder) | exempt: an observer. No positive control: `_store_sys`'s StoreError catch is the only thing it watches (not driven)
- test_serial_link_tx.py:143 | port._pending[5] = _Pending(...) | exempt: a pending entry registered by hand, in the shape send_command registers
- test_reconnect.py:1123 | port._pending[7] = _Pending(...) | exempt: as tx:143
- test_reconnect.py:1049 | port._consumer_task = the real `_consume` task | exempt: the real consumer started by hand, as start() does
- test_reconnect.py:1187 | same | exempt: as :1049
- test_serial_link_attach.py:315 | `_consumer_task.cancel()` "stands in for the store is behind" | complies: the subject is the carry snapshot after stop(), which counts whatever is queued; how the lines came to be queued does not matter to it
- test_serial_link_attach.py:318 | `_rx_lines.extend(...)` | complies: the (ts, str) tuples `_on_bytes` queues
- test_security.py:67 | ASGITransport(client=("203.0.113.5", 4444)) | complies: the real app with a forged remote peer; the asserted "token" text is unique to the config-write refusal

The ASGITransport sites with a loopback client (export_pool.py:189 and :250; exports.py:133, :154, :257, :276 and :344) are the real app in-process, not doubles. Exempt.

##### What I verified and how

- HEAD: `git rev-parse HEAD`.
- Site counts: `grep -cE` with the filter above.
- Baseline in copy-C2: `pytest tests/test_reconnect.py -k "socket_drain or disconnect_during_a_command"`, 2 passed; the test_sim_tcp baseline test, 1 passed.
- Mutants, each run as a single test file or test (commands and results above):
  - FC2-1: link.py:149.
  - FC2-2: server.py:3797.
  - FC2-3: A2 and B2, plus a sanity run with the rename only (1 passed).
  - The observer positive control for classes :1259.
  - The plot per-page ceiling mutant (export_windows patch :43).
- pyserial behaviour was read from the installed package: protocol_socket.py read/in_waiting/write, and where cancel_read/cancel_write are defined.

##### Not verified

- The Windows-only site test_reconnect.py:85 is skipped on Linux.
- _Build's `:memory:` copy against the Windows unlink-vs-open-handle case.
- The remaining "complies" rulings were reasoned, not driven.
- No whole group file was run; only targeted tests.

##### Surprising

- The three link.py adapter tests (`_Sock`, `_Native`, `_NoCancel`) live in test_reconnect.py, not test_link.py, against the naming rule in CLAUDE.md.
- test_serial_link_attach.py defines `_never_opens` twice (lines 24 and 67), with identical bodies.
- My first FloorClock mutant (re-deriving the floor inside the plot stream) was not a defect: `freeze=True` anchors the stream's floor at the newest row, so `_window_floor` reads no clock there. Do not re-drive it as a detector check.
- test_server_ws.py `test_ws_backpressure_callbacks_are_wired` checks the callbacks only under `if proto.pause_writing.__module__ == "mcuscope.server"`. A uvicorn that ships its own pause_writing skips them silently, and the test then asserts only that the method exists. Production defers to uvicorn on purpose; noted for the class 28 helper.

#### Group D (scratch-27-28/out-D.md)
#### Class 27 sweep, group D

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3 (checked with `git rev-parse HEAD` before starting).
Mutants ran in copy-D only. Each one was restored from the repo afterwards, and `diff -rq` of copy-D host/mcuscope and host/tests against the repo came back empty at the end.
The repo tree was not modified (`git status --short` shows only the pre-existing untracked registry-brief.md).

##### Findings

###### FD-1 LOW: the update-check transport answers every URL, so a wrong PyPI endpoint passes the whole suite
- Site: test_update_check.py:29-37 `mock_transport`, plus the handlers at :164, :165 and :257.
  - Each one answers every URL and every method with the same body.
  - No test in the suite asserts `request.url`, the method or the `Accept` header.
  - The same holds for test_config_api.py:340 (not in this group).
- Real behaviour: `check_once` GETs `PYPI_URL` (`https://pypi.org/pypi/mcuscope/json`) and reads `info.version`.
  - A wrong path gets a 404, or an HTML 200 (for example `/project/mcuscope/`).
  - Either way `check_once` returns False silently, so the release check is dead and nothing reports it.
- Driven:
  - Mutant: `PYPI_URL = "https://pypi.org/project/mcuscope/"`.
  - Command: `pytest tests/test_update_check.py -q -p no:randomly`.
  - Result: 35 passed.
- Why LOW: this is advisory (SPEC 3.6), with no captured data involved. The fix is for one test to assert `calls[0].url == uc.PYPI_URL` and the method.

###### FD-2 MEDIUM: `FailingCopy` fails the rebuild before the destructive step, so it cannot test the atomicity the test exists for
- Site: test_sessions.py:766, `test_an_interrupted_sessions_rebuild_keeps_every_row`.
  - The docstring says the rebuild "must be one transaction, or a crash loses the lot silently".
  - The double raises on `INSERT INTO sessions_autoinc`. Every row is still in the untouched `sessions` table at that point.
  - So the test only proves that nothing is lost before `DROP TABLE sessions`.
- The comment calls this "its most damaging point". It is not: the damaging window is after `DROP TABLE sessions` and before `ALTER TABLE sessions_autoinc RENAME TO sessions` (store.py:394-395).
- Driven, three runs:
  - (a) Mutant: `conn.commit(); conn.execute("BEGIN IMMEDIATE")` right after `DROP TABLE sessions` (not atomic past the drop). `pytest tests/test_sessions.py -k "interrupted_sessions_rebuild or autoinc"` gave 2 passed.
  - (b) Same mutant, with the double's trigger moved to `ALTER TABLE SESSIONS_AUTOINC`: the test FAILED.
  - (c) Real store.py with the moved trigger: the test passed.
- Fix: inject at the rename (or parametrize over both points).

###### FD-3 LOW: `OneStepPerExecute` models the Python 3.11 pragma hazard on `Connection.execute` only
- Site: test_store_reclaim_budget.py:221.
  - The docstring says the test "emulates the 3.11 driver on whatever version runs it".
  - `conn.cursor().execute(...)` bypasses the override, and it is the same 3.11 hazard (`Connection.execute` is a shortcut over the cursor).
- Driven:
  - Mutant 1: `_reclaim_pages` uses `conn.cursor().execute(f"PRAGMA incremental_vacuum({step})").fetchall()`.
    - `pytest tests/test_store_reclaim_budget.py -q -p no:randomly` on Python 3.13.5: 7 passed.
  - Positive control, mutant 2: `conn.execute(...).fetchall()`.
    - The same file FAILED `test_the_reclaim_does_not_lean_on_execute_stepping_the_pragma`.
- Why LOW: the 3.11 CI leg still catches it through the sibling tests, which is how the original defect was found. Overriding `cursor()` to return a cursor with the same rewrite closes it.

##### Per-enumeration counts (grep of each list, filtered to the group D regex)
- c27-patch.txt: 65
- c27-classes.txt: 21
- c27-assign.txt: 11
- c27-canned.txt: 6
- c27-injected.txt: 1
- These match the expected counts in the brief.
- Extra sites found with the extra greps below: 19. The greps:
  - `grep -nE '\b(Scripted|SpyLink|CommitBoom|canned|recorder|record_params|record_requests|FakeLink|BurstThenError)\(' <group files>` finds support doubles used here.
  - `grep -n "mock_transport(" test_update_check.py` finds callers of a helper defined in this file.
  - `grep -nE '\b(store|port|app|link|sim|c|s|stack|checker)\.[a-z_]+\s*=' <group files>` finds attribute swaps onto production objects. It found no double beyond CommitBoom; everything else it matched was state priming (`_retention_days`, `_plot_dirty`, sim timers) or a restore of the real `_conn`.
  - `grep -nE 'SimpleNamespace|Mock\(|MagicMock|mock\.patch|Fake|Stub|stub|fake'` found nothing beyond the listed classes.
- Not covered: the JS doubles under host/tests/webui_js/ (dom_stub.mjs, fetch doubles). The group regex matches only the Python runner test_webui_js.py, which defines no double. They belong to the JS helper.

##### Rulings

###### c27-patch.txt (65)
- test_session_bundle.py:67 | `_TempFileResponse.__call__` (`held`) | complies: it delegates to the real `__call__` and only delays the body message.
- test_session_bundle.py:397 | `Store.export_session_db` (`boom`, RuntimeError) | complies:
  - It raises at the same point in `build()` where the real one fails: after both mkstemp files exist, before the zip is written.
  - `bundle` catches `Exception`, so RuntimeError and sqlite3.Error take the same path.
- test_session_bundle.py:445 | `Store.export_session_db` (`held`) | complies: it waits for a lock waiter, then calls the real export.
- test_sim.py:652 | `Simulator.poll_events` (`flaky_poll`) | complies:
  - The exception is the respect under test, and later calls delegate to the real poll.
  - KeyboardInterrupt is the exit `serve_pty` already handles.
- test_sim.py:693 | `Simulator.poll_events` (`dead_fd_poll`, OSError EBADF) | complies:
  - A real dead master raises EBADF from `select`/`os.write` inside the same try.
  - The guard dispatches only on type and errno, not on where the error came from.
- test_sim.py:704 | `os.name = "nt"` (process-global through `mcu_sim.os`) | exempt because a platform knob; the Windows gate itself is the subject.
- test_status_ppid_serial.py:71 | `cli.sys` = `_Sys(executable=shim)` | complies: `executable` is the respect under test, and every other attribute delegates to the live `sys`.
- test_status_ppid_serial.py:72 | `cli_daemonctl.sys` = `_Sys(platform="win32")` | complies:
  - cli_daemonctl reads `sys.platform` in two places, `_open_append` (:68) and `_serving_pids` (:314).
  - `_serving_pids` is the subject. `_open_append` is neutralised by the next site.
- test_status_ppid_serial.py:73 | `cli._open_append` | complies: the lambda is the real non-win32 branch verbatim (`open(path, "ab")`).
- test_stdio.py:32, :33, :34 | `sys.stdout/stderr/stdin = None` | exempt because this is the input state under test (pythonw), not a stand-in.
- test_stdio.py:57, :58 | `sys.stdout/stderr = None` | exempt, same reason.
- test_stdio.py:59 | `_stdio._ensure_console -> False` | complies: that is what the real one returns off Windows; on Windows the forced probe is the respect under test.
- test_stdio.py:98, :113, :145, :161, :177 | `_stdio._crash_dir -> tmp_path` | complies: same return shape (a str dir), and the real one never raises.
- test_stdio.py:178 | `_stdio._report_key = ""` | exempt because a state reset, not a double.
- test_stdio.py:210 | `_stdio._crash_dir -> <file>/mcuscope` | complies: reachable, since `user_dir` does not create the dir and an `MCUSCOPE_DATA_DIR` override can name a path under a file.
- test_stdio.py:129, :147 | `sys.stdout` = TextIOWrapper(cp1252, strict) | complies: this is the real stream type Windows gives a redirected stdout.
- test_stdio.py:249, :269 | `PIPE_CLOSE_IS_EINVAL = True` | exempt because a platform knob.
- test_stdio.py:250, :251 | `sys.stdout/stderr = _FakePipe()` | complies: covered under class _FakePipe (:223).
- test_stdio.py:270, :271 | `sys.stdout/stderr = _Console()` | complies: covered under class _Console (:265).
- test_stdio.py:292 | `sys.platform = "win32"` | exempt because a platform knob; the kernel32 it reaches is the double at :293.
- test_stdio.py:293 | `ctypes.windll = WinDLL()` | complies: see classes K32 (:284) and WinDLL (:289).
- test_stdio.py:294 | `ctypes.WINFUNCTYPE -> identity` | complies:
  - The real call returns a thunk. The tests assert only identity (`_ctrl_handler_ref is first`) and the call list, and those hold for either.
- test_stdio.py:295 | `_stdio._ctrl_handler_ref = None` | exempt because a state reset.
- test_stdio.py:324 | `repair_std_streams -> (["stderr"], False)` | complies, with a note:
  - The real function returns this only after pointing `sys.stderr` at devnull, and the double leaves stderr live, so it is a state the producer cannot reach.
  - That is harmless: a warning routed to stdout still fails `captured.out == ""`.
- test_store_fastpaths.py:277 | `_WRITE_QUEUE_MAX = 2` | exempt because a knob; a full queue is reachable at 10 000.
- test_store_fastpaths.py:412 | uvicorn `WebSocketsSansIOProtocol = NoWritable` | complies: see class NoWritable (:408).
- test_store_match_budget.py:38 | `_make_regexp` = partial(real, budget_s=...) | exempt because a knob: it is the real function with the budget shrunk.
- test_store_plot_reads.py:80 | `_EXPORT_CHUNK = 3` | exempt because a knob:
  - A line wider than a page is not reachable at 10 000.
  - The page-boundary ordering it pins is reachable at the default size with more rows.
- test_store_plot_reads.py:100 | `_EXPORT_CHUNK = 10` | exempt because a knob.
- test_store_reclaim_budget.py:36 | `_RECLAIM_BUDGET_S = 0.0` | exempt because a knob; a spent budget is reachable.
- test_store_reclaim_budget.py:48 | `_RECLAIM_BUDGET_S = 3600` | exempt because a knob.
- test_store_reclaim_budget.py:49 | `_VACUUM_PAGES = 3*step+5` | exempt because a knob.
- test_store_reclaim_budget.py:88, :117, :140 | `_RECLAIM_MIN_PAGES = 1` | exempt because a knob.
- test_store_reclaim_budget.py:118 | `_VACUUM_PAGES = 20` | exempt because a knob.
- test_store_reclaim_budget.py:165, :210 | `_RECLAIM_BUDGET_S = 3600` | exempt because a knob.
  - Aside: in both tests the setattr comes before the docstring, so the "docstring" is a no-op expression.
- test_store_schema.py:253 | `_NoWal.__setattr__` forwarding to the real conn | complies: part of the proxy, so `row_factory` lands on the real connection.
- test_store_schema.py:281 | `sqlite3.connect -> _NoWal(real)` (process-global) | complies: see class _NoWal (:242).
- test_store_time_window.py:111 | `_RETENTION_CHUNK = 3` | exempt because a knob.
- test_store_writer.py:29 | `_MAX_BATCH_ROWS = 1` | exempt because a knob.
- test_store_writer.py:36 | `store._broadcast_batch` (`boom`) | complies: same signature `(rows)`; a raise outside the insert and commit guards is the respect under test.
- test_store_writer.py:58 | `store._note_plot` (`boom`) | complies: same signature `(row, plot)`, and it raises at the real call point.
- test_store_writer_commits.py:70, :108 | `_commit_hold` records and delegates | complies:
  - It returns the real value.
  - Positive controls are present: `holds` is non-empty at :76, and `len(holds) > 50` at :130.
- test_store_writer_commits.py:88 | `_commit_hold` records non-zero holds and delegates | complies: the walrus expression returns `h` for both 0.0 and non-zero.
- test_store_writer_commits.py:137 | `_commit_hold -> 5.0` | exempt because a knob:
  - The real policy caps a hold at `_COMMIT_INTERVAL_S` (0.1 s).
  - 5.0 only widens the window for the cancellation. That path is reachable at 0.1 s, so the subject is unchanged.
- test_timeline.py:130, :305 | `datetime.date = _FixedDate` (process-global through `cli_output.datetime`) | complies: see class _FixedDate (:121).
- test_update_check.py:400 | `uc.replace_atomic` records and delegates | complies: it drops `**kw`, but `_save_cache` calls `replace_atomic(tmp, path)` with no kwargs.
- test_wait_repeat.py:306 | `port.send_raw` (`dead_writer`, StoreError) | complies:
  - While `tally.sends == 0`, the real one runs with `log=True`, so a dead writer makes every call raise StoreError from `add_line`.
  - The double raises on every call, same as the real one.
  - It skips the wire write, which the test does not assert.
- test_wait_repeat.py:354 | `port.send_raw` (`slow_once`) | complies:
  - It stands in for a write that blocks; the subject is the server's re-anchor loop.
  - It skips the wire write and the tx row, which the test does not assert (NEVER match).
- test_webui.py:281 | `server_mod.__version__ = "9.9.9"` | exempt because a knob simulating an upgrade. The lru cache is cleared, as a new process would start with it empty.

###### c27-classes.txt (21)
- test_sessions.py:766 | `FailingCopy`, sqlite3.Connection proxy failing the INSERT of the rebuild | **violates**: FD-2 (the fault point precedes the destructive step, so the atomicity subject is untested).
- test_sim.py:466 | `_AcceptFailsOnce`, the listener socket | complies:
  - One transient OSError, then the real `accept`.
  - `settimeout` and `fileno` delegate, and those are what `serve_listener` uses.
- test_sim.py:488 | `_CloseFails`, a client socket whose `close` raises | complies:
  - The raise is the respect under test.
  - `fileno`, `recv`, `send` and `setblocking` delegate, so `select` still works.
- test_sim.py:503 | `_ClientCloseFails`, a listener handing out `_CloseFails` | complies: it wraps the real `accept`.
- test_sim.py:594 | `_DeadListener` | complies:
  - It implements exactly the three calls `serve_listener` makes (`settimeout`, `accept`, `fileno`).
  - Both parametrised shapes match a real dead socket: EBADF with a live fd, and `fileno() == -1`.
- test_source_link.py:103 | `Blocking`, a SourceLink source | complies: it implements the whole source contract (`feed`, `poll`), and the test asserts exclusion, not a race outcome.
- test_status_ppid_serial.py:44 | `_Sys` | complies: override plus `__getattr__` to the live `sys`.
- test_stdio.py:223 | `_FakePipe`, a redirected Windows stream | complies:
  - `translate_closed_pipe_errors` and `guard_stdout` read only `isatty`/`write`/`flush`/`_stream` on it.
  - A missing attribute would raise, not pass.
- test_stdio.py:265 | `_Console`, a console stream | complies: `isatty()` True is the only thing the skip dispatches on.
- test_stdio.py:284 | `K32.SetConsoleCtrlHandler` records and returns 1 | complies:
  - The real call returns nonzero on success.
  - The recorded call list is the subject, and failure of the real call is not part of these tests.
- test_stdio.py:289 | `WinDLL` exposing `kernel32` | complies: `install_console_ctrl_handler` reads only `ctypes.windll.kernel32`.
- test_store_fastpaths.py:408 | `NoWritable`, a uvicorn protocol without `writable` | complies: the missing name in `__init__.__code__.co_names` is exactly what `_enable_ws_backpressure` dispatches on.
- test_store_fastpaths.py:127 | `BrokenCommitOnce` | complies:
  - The first `commit` raises sqlite3.OperationalError, as a disk error does. After that it delegates.
  - The writer catches `Exception` and rolls back through the real connection.
- test_store_id_sequence.py:27 | `_CommitFailsOnce` | complies: the same shape as BrokenCommitOnce.
- test_store_plot_summary.py:206 | `_DeleteBeforeSecondStatement`, a read-conn proxy | complies:
  - It counts `execute` and runs a real committed delete before the third statement.
  - Every statement runs on the real connection, so the snapshot semantics are SQLite's own.
- test_store_reclaim_budget.py:221 | `OneStepPerExecute(sqlite3.Connection)` | **violates** (LOW): FD-3. Only `Connection.execute` is emulated; `cursor().execute` passes through.
- test_store_schema.py:242 | `_NoWal`, a connection answering the WAL pragma with another mode | complies:
  - A real filesystem without shm returns a result row naming the old mode, and so does this double.
  - The proxy forwards `__setattr__`, so `row_factory` lands on the real connection.
- test_store_stamp_order.py:99 | `_FailingCommit` (OSError) | complies:
  - A real disk-full commit raises sqlite3.OperationalError, not OSError.
  - Both reach the same `except Exception` in the writer, and the test asserts only `StoreError("commit failed")` and `write_errors`.
- test_store_writer.py:235 | `_SlowCommit` | complies: it sleeps, then does the real commit.
- test_timeline.py:121 | `_FixedDate(datetime.date)`, only `today()` fixed | complies: it is a real subclass, so the `datetime.date(y, m, d)` construction and `datetime.combine` behave identically.
- test_wait_repeat.py:30 | `Stimulus` | exempt because not a double: it drives the real `/marker` endpoint.

###### c27-assign.txt (11)
- test_store_fastpaths.py:105 | `store._scan_plot_summary = slow_scan` | complies:
  - It blocks, then calls the real scan with the same kwargs.
  - The caller passes `conn=` and `high=` by keyword (store.py:2462 through `_read_on_private_conn`).
- test_store_fastpaths.py:194 | `_scan_plot_summary = counting` | complies:
  - It records and delegates.
  - Positive control, driven: mutant setting `_plot_dirty = True` in `query_plot_channels_safe`; `pytest tests/test_store_fastpaths.py -k does_not_scan_between_deletes` FAILED. So the seam receives the calls.
- test_store_fastpaths.py:443 | `store.submit_line_nowait = nowait` | complies:
  - It raises `asyncio.QueueFull`, the real refusal, on odd calls and delegates otherwise.
  - The real one builds a request (a future) before `put_nowait` raises; nothing else, so skipping that build is harmless.
- test_store_fastpaths.py:444 | `store.submit_line = slow` | complies: it counts and delegates.
- test_store_fastpaths.py:475 | `store._read_conn = racy` | complies: it returns the real conn and then runs the real `_close_read_conns`, which bumps the epoch as `stop()` does.
- test_store_plot_reads.py:129 | `store._open_export_conn = traced` | complies:
  - It is the real opener plus a trace callback.
  - `seen[...][-1]` raises if nothing was traced, so the test has its own positive control.
- test_store_plot_summary.py:60 | `_scan_plot_summary = scan` (`_counting`) | complies: it counts and delegates.
- test_store_plot_summary.py:170 | `_scan_plot_summary = parked` | complies: the real scan runs, then the double parks.
- test_store_plot_summary.py:269 | `_scan_plot_summary = slow_scan` | complies: it blocks, then delegates.
- test_store_plot_summary.py:315 | `_scan_plot_summary = failing_scan` (OSError) | complies:
  - A real scan fails with sqlite3.OperationalError.
  - `_rebuild_plot_summary` catches BaseException, and `_on_read_conn` retries only ProgrammingError, which neither type is. The dirty flag, which is the subject, is type-agnostic.
- test_store_writer.py:209 | `store._insert_batch = spy` | complies: it records the batch size and delegates.

###### c27-canned.txt (6)
- test_update_check.py:3 | a docstring mention of MockTransport | exempt because prose, not a site.
- test_update_check.py:37 | the `mock_transport` helper | **violates** (LOW): FD-1. It answers every URL and method, and no test asserts the request.
- test_update_check.py:164 | MockTransport(`_raises_offline`) | covered by FD-1:
  - It raises httpx.ConnectError, the real offline error.
  - It answers every URL, and the test asserts only `False`/`None`.
- test_update_check.py:165 | MockTransport(`_html_body`) | covered by FD-1:
  - An HTML 200 reaching `resp.json()` is a real shape.
  - It is path-agnostic, like :164.
- test_update_check.py:257 | MockTransport(`offline`), records and raises ConnectError | covered by FD-1: path-agnostic; the call count is the subject.
- test_timeline.py:263 | `recorder(lines={"lines": rows})` | complies, with a note:
  - It answers every `/lines` request with the same body whatever the params, so the `^!pd` priming queries (`_make_decoder`, `_decode_pages`) also receive the `!ps` row.
  - The test asserts nothing about params.
  - Harmless to the subject (the snapshot hands its --changes baseline to the follow). Driven: mutant replacing `dec.share_changes(baseline)` (cli.py:841) with `pass`; `pytest tests/test_timeline.py -k tail_snapshot_hands` FAILED.

###### c27-injected.txt (1)
- test_source_link.py:198 | `create_app(open_link_fn=mcu_sim.open_sim_link)` | complies, with a note:
  - `open_sim_link` answers every device, unlike production's dispatching opener (daemon._start_sim).
  - The config here holds one `sim://board` port, so dispatch is never exercised, and the subject is only that the seam reaches SerialPort.
  - The dispatching opener is driven at test_sim.py:941 and test_daemon_startup.py:100.

###### Extra sites (19)
- test_store_writer.py:85 | `CommitBoom(store._conn)` (support double) | complies: its first commit raises sqlite3.OperationalError, the real type, and the test asserts `StoreError` and that the writer lives on.
- test_source_link.py:32, :37, :43, :56, :73, :81, :129, :144 | `Scripted` (support), a source feeding SourceLink | complies:
  - The unit under test is SourceLink, and every test writes whole lines.
  - So the documented divergence in `Scripted.feed` (exact-payload dispatch, support.py:142) is not reached.
- test_update_check.py:96, :109, :136, :163, :182, :192, :209, :231, :361 | callers of `mock_transport` | covered by FD-1:
  - Each asserts the parsed version, the cache or the call count, never the URL.
  - :109 and :209 assert `calls == []`, and their positive control is :231 (`len(calls) == 1`).

##### Surprises
- test_store_reclaim_budget.py:165 and :210 put `monkeypatch.setattr` above the intended docstring, so both tests have no docstring. Cosmetic.
- Several sites patch process-global modules: `os.name` (test_sim.py:704), `sys.platform` (test_stdio.py:292), `datetime.date` (test_timeline.py:130/305) and `sqlite3.connect` (test_store_schema.py:281).
  - All are restored by monkeypatch.
  - Any background thread left running from another test sees the patched value for that window.

#### Group JS, class 27 part (scratch-27-28/out-JS.md)
#### Classes 27 and 28, group JS: host/tests/webui_js/* against host/mcuscope/webui/*.js

Pinned at f31ecd995ee2ed193d8d60637b76620ddc880be3 (checked at start).
HEAD moved to 1a1251a during the run (pidfile.py, serial_link.py, test_pidfile.py and docs); `git diff --stat f31ecd9 HEAD -- host/mcuscope/webui host/tests/webui_js host/tests/test_webui_js.py host/tests/test_webui.py host/mcuscope/server.py host/mcuscope/config.py` is empty, so nothing in scope changed.
Mutants ran in copy-JS/ (rsync of the repo), one `node --test` file at a time; the copy was restored after each and diffed clean against the repo's webui and webui_js at the end.

#### Class 27

##### Findings

###### F-JS-1 LOW: the export double runs without the port and plot-channel guards in every browser test
- Site: exportdlg_guards.mjs:226 `refuse(seen.lastUrl, { sessions, channels })`: no `ports`, and no caller of installExportDaemon passes `channels` (22 calls, `grep -c "installExportDaemon(" tests/webui_js/*.test.mjs`).
- `refuse()` skips a guard whose state is null, so "no such port" and "no such plot channel" never fire in a browser test.
- export_paused_window.test.mjs:225-244 says the double "applies the endpoints' own guards" and asserts `seen.refusals == []` as "an export the panel builds must be one the daemon will answer".
- Driven, M2: exportdlg.js plotExportPath sends `port=zz` for every plot export.
  - These pass unchanged: export_paused_window, plots_export_button, plots_zoom_export, digital_zoom_export, plots_shown_ids, plots_pause_edge, exportdlg, digital_frozen_edge, plots_shown_tick, digital_shown_tick.
  - Only value assertions catch it: plots_ports (3 failures), digital_shown_ids, digital_shown_trimmed, exportdlg_session_fill.
  - With `ports: ["p1"]` added to the refuse() call in the copy, export_paused_window fails on M2 ("no such port: zz" x4 in refusals). The control (shipped exportdlg.js, ports passed) passes 11/11.
- Driven, M1: terminal.js exportPane sends `port=all` for an all-ports pane, which the daemon refuses (`_unknown_port`, server.py:1013).
  - Survives every pane-export test (export_paused_window, terminal_shown_first_id, exportdlg_capture_reset, exportdlg_pending_close, terminal_filter_pane, terminal_logic), even with ports passed, because no test exports an all-ports pane.
  - That part is a coverage gap, not the double.
- Fix: have installExportDaemon derive `ports` from what the page has seen (state.knownAliases plus the row ports the test fed), and pass the channels where the test ingests them.
  - Add one all-ports pane export to export_paused_window's W6 road list.

###### F-JS-2 LOW: querySelector returns a detached element for a missing class, and nothing compares the classes with index.html
- Sites: dom_stub.mjs:153 (element) and :222 (document). The null-to-detached swap is documented at dom_stub.mjs:9-13.
- test_webui.py:215 pins every `$("id")` against index.html (its comment at :160 names this stub gap for ids). Class selectors get no such check.
- Production resolves by class: app.js:148 `.side-body` at module scope, and terminal.js createPane's `.closepane`, `.scrollback`, `.port-sel`, `.match`, `.pill`, `.jump`, `.shown`, `.hint`, `.match-clear`, `.clear`, `.exportpane` and `.chk`.
  - All exist today (grep of class= in index.html).
- Driven: renaming `class="side-body"` in index.html leaves app_layout.test.mjs (5/5) and test_webui.py (15 passed) green.
  - In a browser `sideBody` would be null, and every pointermove on the CAN/plots divider would throw (app.js:155).
- Fix: extend test_webui.py's id scan to `querySelector(All)?("\.<class>")` literals against index.html class attributes.

###### F-JS-3 MEDIUM: cmdbar_detached_pick passes on the defect it pins, because the stub `<select>` keeps a value no option carries
- Site: cmdbar_detached_pick.test.mjs:30-41 reads `env.byId("cmdPort").value` after populateCmdPort. The stub select keeps any value it is given (FakeEl `value` field, dom_stub.mjs:90).
- Driven, M3: cmdbar.js populateCmdPort drops `opts.push(cur)` for a detached pick and sets `sel.value = cur`.
  - cmdbar_detached_pick passes 2/2, and cmdbar_sole, cmdbar_eol, cmdbar_mode and statusbar_logic stay green.
  - In a browser the select then reads "", so cmdPortValue() gives null and the command goes to the remaining board. That is exactly SPEC 4's "a write never goes to a default port" defect the test names.
- With cmdbar_eol's browserSelect wrapper applied to #cmdPort in the copy, M3 fails the test ("the pick survives its port detaching"). Shipped code passes 2/2.
- Sweep of the same shape:
  - I gave the copy's stub browser `<select>` semantics: createElement("select") and the six index.html select ids, with index.html's static baud options.
  - Then I ran all 172 test files one at a time against shipped code.
  - All pass except statusbar_session_dialog:173 (N-JS-2).
  - So no other test leans on the lax select for a verdict. Before the baud options were added, 5 statusbar files failed only because index.html's static `<option>`s are absent from the stub.
- Fix: wrap #cmdPort with browserSelect in this test. Better: give FakeEl browser select semantics, since the CLAUDE.md note about the lax select is now load-bearing in two files.

###### F-JS-4 MEDIUM: hand-rolled `closest()` ignores its selector, so hover-to-cursor and Enter-to-save pass with a broken selector
- Sites: digital_tick_reset:95, plots_hover_tick:38, plots_pause_edge:154 and plots_tick_reset:74 use `elementFromPoint = () => ({ closest: () => row })`. settings_dirty:182 and :188 use `target.closest = () => section`.
- Underneath: FakeEl.closest (dom_stub.mjs:182) returns null for any selector, so each test builds its own, and none dispatches on the selector the way Element.closest does.
- Driven, M4: plots.js:1233 `closest(".ln")` changed to `".lnX"`. plots_hover_tick 2/2, plots_tick_reset 5/5, digital_tick_reset 4/4 and plots_pause_edge 5/5 all pass.
  - In a browser, hovering a terminal line would never move the plot and lane cursor.
- Driven, M5: settings.js:799 `closest(".cfg-sec")` changed to `".cfg-secX"`. settings_dirty 10/10, settings_revision 14/14, settings_save_reread 15/15 and settings_late_answers 17/17 pass.
  - In a browser, Enter in a settings field would save nothing.
- Fix: implement FakeEl.closest by walking parentNode with selectorTest. Build the hit element as a FakeEl with className "ln" (and the field inside a `section.cfg-sec` with the right id), instead of an object whose closest ignores its argument.

##### Nits (not findings: no divergence lets a test pass on broken code today)
- N-JS-1: exportdlg_guards.mjs:5-6 "Not mirrored" omits the regex match-budget refusal (MatchBudgetExceeded, server.py:1903 on /lines/export and the /plot/export decode path).
  - It is data-dependent and cannot be mirrored, but the list reads as complete.
- N-JS-2 (class 63 shape): statusbar_session_dialog.test.mjs:173-202 picks devSel values "/dev/ttyUSB3" and "COM4" that the /devices answer does not list (stub :15 answers `devices: []`).
  - Only the lax select accepts them. A browser select reads "" and the alias would be "board".
  - The alias derivation is still exercised, but through a state the user cannot reach.
  - Fix: have that stub list both devices.
- N-JS-3: three daemon contracts are mirrored by hand with no contract test:
  - the config revision rule (settings_revision:42 and settings_late_answers:79 against config.py:560);
  - the 409 text;
  - the /lines limit clamp (api_backfill_paging SERVER_CLAMP and api.js:440 LINES_LIMIT_MAX against store.py:1945).
  - All agree today. Production displays the 409 text and never dispatches on it, and paging follows `truncated`, so a drift would change no current verdict.
- N-JS-4: count-only console.error assertions: statusbar_logic:549 and plots_seed_grammar:123 and :148.
  - The injected fault is the only error source in each window, so they cannot pass on a different error today. They would, though, if another error started being logged there.

##### Enumerations (commands verbatim, counts)
Run from /home/daniel/git/mcuscope/host unless stated. AST scripts use acorn 8 from /opt/st/STM32CubeMX2/.../node_modules and live in scratch-27-28/js/.
- (1) dom_stub items: `grep -nE "^(export )?(class|function) |^  (get |set )?[a-zA-Z]+\(.*\) *\{|globalThis\.[a-zA-Z.]+ =|URL\.[a-zA-Z]+ =|^    (hidden|getElementById|createElement|createDocumentFragment|createTextNode|querySelector|querySelectorAll|elementFromPoint|addEventListener|removeEventListener|emit|get activeElement)\b|^    (getItem|setItem|removeItem|clear|key|get length)\b" tests/webui_js/dom_stub.mjs` gives 93, plus 12 members the grep misses, added by reading (listed).
- (2) non-test helpers: `ls tests/webui_js | grep -v '\.test\.mjs$'` gives 2 (dom_stub.mjs, exportdlg_guards.mjs).
  - Guard items: `grep -nE "^(export )?function |^const [A-Za-z_]+ = |globalThis\.fetch =|createElement =|el\.click =" tests/webui_js/exportdlg_guards.mjs` gives 23.
- (3a) `grep -nE 'globalThis\.\w+\s*=[^=]' tests/webui_js/*.test.mjs` gives 150.
- (3b) `grep -nE "^\s*(\w+\.)+\w+\s*=\s*(async\s*)?(\(|function|\w+\s*=>)" tests/webui_js/*.test.mjs` gives 214 (232 over `*.mjs`, the 18 extra in the two helpers; all 232 are ruled).
- (3c) fetch stubs, AST (`node js/fetchdump.mjs host`): 123 `globalThis.fetch =` assignments over `*.mjs`.
  - 45 take no parameter (37 reject every request, 8 answer every URL alike), 72 take the URL, and 6 restore a saved fetch.
- (4) AST (`node js/enum.mjs host`, 704 production names = functions defined in webui/*.js plus member names webui calls):
  - object literals with a function-valued property: 285, on 247 lines;
  - of those, 278 have a key matching a production name;
  - member assignments of a function literal outside `globalThis.`: 94;
  - `globalThis.`/`window.` assignments: 173 (on 172 lines);
  - class expressions or declarations: 8;
  - `new Proxy`: 7.
- Union of 3a, 3b and 4 by file:line: 495 lines, every one ruled in the table below (tags: G=3a, R=3b, L=literal, A=fn member assignment, G2=AST global assignment, C=class/Proxy). Verdicts: 450 complies, 39 exempt, 6 violates.
- Extra shapes the enumerations do not catch, each ruled below:
  - (x1) `<select>` value read-backs: `grep -nE 'byId\("(cmdPort|cmdEol|expSession|devSel|baudSel|attachEol)"\)\.value|[sS]el(\(\))?\.value|opt\("\w+"\)\.value|_fields\.\w+Sel\.value' tests/webui_js/*.test.mjs | grep -vE '\.value\s*=[^=]'` gives 16.
  - (x2) DOM members replaced by a non-literal: `grep -nE "\.(getBoundingClientRect|getContext|focus|click|closest|elementFromPoint|querySelector|createElement|showModal|close|after|appendChild)\s*=[^=]" tests/webui_js/*.test.mjs | grep -vE "=\s*(async\s*)?(\(|function|\w+\s*=>)"` gives 15.
  - (x3) hand-built objects put into production collections: `grep -nE "(charts|digitalLanes|lanes|panes|surfaces|canRows|frames)\.(set|push|unshift)\(" tests/webui_js/*.test.mjs | grep -v makePane` gives 22.
  - Layout knobs assigned as plain values (`clientWidth =`, `scrollTop =`) were not enumerated; they are inputs, not behaviour.

##### Contract check for exportdlg_guards.mjs (item 2)
- test_webui_js.py:278 test_export_guard_double_agrees_with_the_daemon sends all 210 GUARD_URLS to the real app and to refuse().
  - It asserts equal answers, word for word.
  - It also asserts that every refusal literal refuse() can return is reached by some URL, and that the list holds both accepted and refused URLs.
- Daemon refusals the double leaves out:
  - regex compile errors, the wide-export one-stream check and the label deadband check (listed at exportdlg_guards.mjs:5-6);
  - the match-budget refusal (not listed, N-JS-1).
- The contract covers refuse(). installExportDaemon's own divergences are F-JS-1 (no ports or channels) and "every non-export route answers 200 {}". The latter is harmless: no test reads such a body.

##### Rulings, item 1: dom_stub.mjs items (93 grep lines + 12 by reading)
- dom_stub.mjs:23 | webuiUrl | exempt because a path helper, not a double
- dom_stub.mjs:27 | webuiDir | exempt because a path helper
- dom_stub.mjs:31 | FakeClassList (DOMTokenList) | complies on the members below; diverges in being separate from className (see 41)
- dom_stub.mjs:32 | constructor | complies
- dom_stub.mjs:33 | add | complies
- dom_stub.mjs:34 | remove | complies
- dom_stub.mjs:35 | contains | complies
- dom_stub.mjs:36 | toggle(c, force) with the return value | complies
- dom_stub.mjs:41 | value | complies as a read; diverges: className and classList are two stores here, one attribute in a browser, and selectorTest reads className only | complies for every current use: the negative classList asserts (app_layout_corrupt:15, chrome_radios:48, digital_edge:71, can_head:41/57/145, plots_solo:94, terminal_filter_pane:53, terminal_logic:212/224) name classes production sets only through classList (grep of className= in webui), so neither store can hide the other
- dom_stub.mjs:44 | FakeStyle | complies: setProperty/removeProperty/getPropertyValue on own properties
- dom_stub.mjs:45 | setProperty | complies
- dom_stub.mjs:46 | removeProperty | complies
- dom_stub.mjs:47 | getPropertyValue ('' when unset) | complies
- dom_stub.mjs:51 | fakeContext: every method a no-op returning undefined | complies: see sites table dom_stub.mjs:53
- dom_stub.mjs:64 | selectorTest: '.class', 'tag', '#id', 'tag.class' only | complies for the selectors production passes (grep querySelector in webui: .x, tr, button, '#id button'); a descendant selector such as '#timeSeg button' is read as the id 'timeSeg button' and never matches (and ids resolved by getElementById are not under body anyway): the three roving groups get no buttons in tests; nothing asserts through them (app_resizer_keys:77-80 checks only that a handler exists)
- dom_stub.mjs:75 | FakeEl | members below
- dom_stub.mjs:76 | constructor defaults (hidden/disabled/checked false, clientWidth 0, clientHeight 300) | complies: layout values are knobs tests set
- dom_stub.mjs:108 | textContent setter clears children | complies (the UI's clear idiom)
- dom_stub.mjs:109 | textContent getter concatenates the subtree | complies
- dom_stub.mjs:114 | appendChild: fragments spliced; also clears the element's own text | diverges from a browser (a text node stays beside appended children); settings.js:363-370 (session name + tag span) is the one production site that sets text and then appends, and settings_user_text:21 avoids it on purpose; no test asserts the collapsed text | complies
- dom_stub.mjs:128 | append | complies
- dom_stub.mjs:129 | replaceChildren | complies
- dom_stub.mjs:130 | remove | complies
- dom_stub.mjs:138 | firstElementChild: a fresh element when there are no children (for <template>.content) | complies: documented; clones of an empty template
- dom_stub.mjs:143 | content: a lazily made fragment | complies
- dom_stub.mjs:148 | descendants (test helper) | exempt because not a DOM member
- dom_stub.mjs:152 | querySelectorAll | complies within selectorTest's range (see 64)
- dom_stub.mjs:153 | querySelector: a detached element where a browser returns null | violates: F-JS-2 (driven: .side-body renamed in index.html, app_layout and test_webui.py stay green)
- dom_stub.mjs:155 | cloneNode: tag, class, id, text; drops attributes and dataset | diverges; no test clones a template carrying data-* (createPane's .chk data-ch is never exercised through a clone) | complies
- dom_stub.mjs:164 | addEventListener | complies
- dom_stub.mjs:168 | removeEventListener | complies
- dom_stub.mjs:174 | emit: calls the element's own listeners with the given object; no bubbling, no capture, no target unless given | complies: every delegated listener test passes target itself (chrome_radios:30/102/119, settings_dirty:184-189, exportdlg:401); stopPropagation (plots.js:752/1068, digital.js:297) cannot be observed here
- dom_stub.mjs:176 | setAttribute | complies
- dom_stub.mjs:177 | getAttribute | complies
- dom_stub.mjs:178 | removeAttribute | complies
- dom_stub.mjs:179 | hasAttribute | complies; `open` is kept by showModal/close, which is what production reads (hasAttribute('open'))
- dom_stub.mjs:181 | getBoundingClientRect all zeros | complies: tests set rects as knobs
- dom_stub.mjs:182 | closest() always null, whatever the selector or ancestors | violates: F-JS-4 (tests that need it hand-roll a closest that ignores the selector)
- dom_stub.mjs:183 | contains | complies
- dom_stub.mjs:187 | focus sets document.activeElement | complies
- dom_stub.mjs:188 | blur | complies
- dom_stub.mjs:189 | setSelectionRange no-op | complies: unobserved
- dom_stub.mjs:190 | select no-op | complies
- dom_stub.mjs:191 | click(): emits click even when disabled; a browser's click() on a disabled control does nothing | complies: every production handler for a control it disables re-checks disabled itself (exportdlg.js:244, statusbar.js:687, cmdbar.js:260, terminal.js:769 addPane cap); driven: with emit('click') ignoring disabled elements in the copy's stub, all 66 test files that click pass one at a time
- dom_stub.mjs:192 | setPointerCapture no-op | complies
- dom_stub.mjs:193 | releasePointerCapture no-op | complies
- dom_stub.mjs:194 | scrollTo no-op | complies
- dom_stub.mjs:195 | showModal sets `open`; never throws | complies: production opens only when !hasAttribute('open') (statusbar.js:583, settings.js:754)
- dom_stub.mjs:196 | close removes `open`; fires no close event | complies: production listens for `cancel` only and closes itself (grep addEventListener('close') in webui: none)
- dom_stub.mjs:201 | installDom | installs the items below
- dom_stub.mjs:215 | document.hidden false | complies
- dom_stub.mjs:216 | activeElement | complies
- dom_stub.mjs:218 | getElementById manufactures any id | complies: test_webui.py::test_index_declares_every_id_the_modules_resolve pins every $() id against index.html
- dom_stub.mjs:219 | createElement | complies
- dom_stub.mjs:220 | createDocumentFragment | complies
- dom_stub.mjs:221 | createTextNode | complies
- dom_stub.mjs:222 | document.querySelector -> body.querySelector (detached element on a miss) | violates as 153 (F-JS-2); app.js:148 `.side-body` is the live instance
- dom_stub.mjs:223 | document.querySelectorAll -> body descendants only: index.html's markup is absent, so '#sideSeg button' etc. find nothing | complies: see 64
- dom_stub.mjs:224 | elementFromPoint null | complies: tests that hover replace it (F-JS-4 for how)
- dom_stub.mjs:225 | document.addEventListener | complies
- dom_stub.mjs:229 | document.removeEventListener no-op | complies: no production caller
- dom_stub.mjs:230 | document.emit (test trigger) | exempt because not a DOM member
- dom_stub.mjs:235 | localStorage.getItem | complies
- dom_stub.mjs:236 | localStorage.setItem stringifies | complies
- dom_stub.mjs:237 | localStorage.removeItem | complies
- dom_stub.mjs:238 | localStorage.clear | complies
- dom_stub.mjs:239 | localStorage.key | complies
- dom_stub.mjs:240 | localStorage.length | complies
- dom_stub.mjs:251 | see the sites table, dom_stub.mjs:251
- dom_stub.mjs:252 | see the sites table, dom_stub.mjs:252
- dom_stub.mjs:253 | see the sites table, dom_stub.mjs:253
- dom_stub.mjs:254 | see the sites table, dom_stub.mjs:254
- dom_stub.mjs:256 | see the sites table, dom_stub.mjs:256
- dom_stub.mjs:258 | see the sites table, dom_stub.mjs:258
- dom_stub.mjs:259 | see the sites table, dom_stub.mjs:259
- dom_stub.mjs:260 | see the sites table, dom_stub.mjs:260
- dom_stub.mjs:261 | see the sites table, dom_stub.mjs:261
- dom_stub.mjs:262 | see the sites table, dom_stub.mjs:262
- dom_stub.mjs:263 | see the sites table, dom_stub.mjs:263
- dom_stub.mjs:264 | see the sites table, dom_stub.mjs:264
- dom_stub.mjs:265 | see the sites table, dom_stub.mjs:265
- dom_stub.mjs:266 | see the sites table, dom_stub.mjs:266
- dom_stub.mjs:267 | see the sites table, dom_stub.mjs:267
- dom_stub.mjs:268 | see the sites table, dom_stub.mjs:268
- dom_stub.mjs:272 | see the sites table, dom_stub.mjs:272
- dom_stub.mjs:273 | see the sites table, dom_stub.mjs:273
- dom_stub.mjs:274 | see the sites table, dom_stub.mjs:274
- dom_stub.mjs:286 | see the sites table, dom_stub.mjs:286
- dom_stub.mjs:294 | see the sites table, dom_stub.mjs:294
- dom_stub.mjs:298 | see the sites table, dom_stub.mjs:298
- dom_stub.mjs:307 | makePane: newPaneModel over fake elements | complies: the element set is createPane's (terminal.js 678-688); born live whatever bornPaused() says, and a pane made paused by hand has frozenRows null, the state api.js:195 leaves after a capture reset; the freeze tests pause through the real setAutoscroll
- dom_stub.mjs:325 | makeRow: a /lines row | complies: SPEC 3.4 fields
- dom_stub.mjs:330 | tick: real setTimeout | exempt because a wait, not a double
- dom_stub.mjs:90 (added by reading) | value: a plain field, so a <select> keeps a value no option carries (a browser select reads "") | violates: F-JS-3 (cmdbar_detached_pick); documented in CLAUDE.md; with browser semantics in the copy every other file passes (see F-JS-3)
- dom_stub.mjs:100 (added by reading) | canvas width/height 300x150 | complies: the HTML defaults
- dom_stub.mjs:269 (added by reading) | FakeBlob constructor | complies
- dom_stub.mjs:270 (added by reading) | FakeBlob.text | complies
- dom_stub.mjs:281 (added by reading) | FakeWebSocket.send no-op | complies: production never sends
- dom_stub.mjs:282 (added by reading) | FakeWebSocket.close sets readyState 3, no onclose | complies: see sites table :274
- dom_stub.mjs:287 (added by reading) | FakeUplot constructor | complies: see sites table :286
- dom_stub.mjs:288 (added by reading) | FakeUplot.setData stores | complies
- dom_stub.mjs:289 (added by reading) | FakeUplot.setSize no-op | complies
- dom_stub.mjs:290 (added by reading) | FakeUplot.setCursor no-op, fires no hook | complies: production always calls it with _fire=false (plots.js:1300-1311)
- dom_stub.mjs:291 (added by reading) | FakeUplot.destroy | complies
- dom_stub.mjs:292 (added by reading) | FakeUplot.valToPos 0 | complies: only reached with a zero-width chart

##### Rulings, items 3 and 4: the union of 3a, 3b and 4 (495 lines; [tags] as in the enumeration list)
- api_backfill_clear.test.mjs:29 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; first-connect /lines held for the race, other routes empty (a merged {lines,channels} body, never read for the missing key) | complies
- api_backfill_clear.test.mjs:31 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_backfill_clear_tokens.test.mjs:34 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; the !pd seed query is recorded and answered empty below the window | complies
- api_backfill_clear_tokens.test.mjs:36 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_backfill_paging.test.mjs:37 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; /lines?since_id serves since_id-exclusive, id_to-inclusive, desc, limit clamped at 1000 with `truncated`, as store.query_lines (clamp 1000 at store.py:1945); `order` ignored (production always sends desc) | complies
- api_backfill_paging.test.mjs:48 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_backfill.test.mjs:19 [G G2 R] | fetch that rejects every request (backfill failure) | complies: the subject is the failure path; test asserts rows still drain
- api_backfill.test.mjs:29 [A R] | hooks.reportError (a toast in app.js) replaced by a recorder | complies: its only effect is the message, which is asserted
- api_capture_reset_history.test.mjs:10 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads (no headers member; api() never reads it) | complies
- api_capture_reset_history.test.mjs:16 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_capture_reset_paused_pane.test.mjs:12 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; match= query separated from the backfill | complies
- api_capture_reset_paused_pane.test.mjs:18 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_capture_reset_stage.test.mjs:31 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; backfill counted and gated | complies
- api_capture_reset_stage.test.mjs:41 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_db_reset_misfire.test.mjs:25 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; backfill counted | complies
- api_db_reset_misfire.test.mjs:32 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_high_rate_pending.test.mjs:18 [G G2 L R] | daemon stand-in answering every path {lines:[]} (an empty capture) | complies: /plot/channels lacks `channels` but api.js valid() reads it as []; subject is live-row counting, the backfill carries nothing
- api_pane_queue.test.mjs:20 [G G2 L R] | daemon stand-in answering every path {lines:[]} (an empty capture) | complies: as api_high_rate_pending:18; subject is the live path
- api_plot_def_seed_ports.test.mjs:24 [G G2 R] | daemon stand-in dispatching on path and query: answers each route production calls in the daemon's shape for the fields production reads; the !pd seed honours since_id, id_to, order and limit | complies
- api_plot_def_seed_ports.test.mjs:39 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_plot_def_seed.test.mjs:33 [G G2 R] | daemon stand-in: match= queries get the !pd rows, others the seed rows, ignoring since_id/id_to/limit | complies: the test asserts the def query is anchored (since_id, limit) rather than relying on it; DEF_ROWS lie below the window as the daemon would serve
- api_plot_def_seed.test.mjs:36 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_plot_seed_ports_fallback.test.mjs:12 [G G2 R] | daemon stand-in whose per-port /plot/channels answers 500 | complies: production's fallback catch is untyped (api.js 363-369), so the status is immaterial; test asserts the per-port path ran and the logged text
- api_plot_seed_ports_fallback.test.mjs:17 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_plot_seed_ports_fallback.test.mjs:25 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_plot_seed_ports_fallback.test.mjs:34 [A R] | console.error recorder, restored in finally | complies: asserts the text
- api_plot_seed_ports.test.mjs:18 [G G2 R] | daemon stand-in dispatching on path and port: answers each route production calls in the daemon's shape for the fields production reads; per-port /plot/channels and /plot/series answer by `port` | complies
- api_plot_seed_ports.test.mjs:33 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_plot_seed.test.mjs:71 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; /plot/series answers by `name`, as the daemon keys it | complies
- api_plot_seed.test.mjs:83 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_seed_clear_reset.test.mjs:14 [G G2 R] | router in front of the export double (route first, else installExportDaemon) | complies: routes are per-test holds; fallthrough ruled at exportdlg_guards.mjs:238
- api_seed_clear_reset.test.mjs:18 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_seed_group_window.test.mjs:15 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; /plot/series recorded (the asserted window) | complies
- api_seed_group_window.test.mjs:25 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_staging_overflow.test.mjs:14 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; backfill held | complies
- api_staging_overflow.test.mjs:22 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_ws_backoff.test.mjs:15 [G G2 L R] | daemon stand-in answering every path {lines:[]} | complies: the subject is the socket backoff; the first-connect backfill of an empty capture fetches nothing else
- api_ws_backoff.test.mjs:29 [G G2 R] | setTimeout capture returning 0 | complies: api.js only passes the id to clearTimeout, stubbed at :30
- api_ws_backoff.test.mjs:30 [G G2 R] | clearTimeout no-op paired with :29 | complies
- api_ws_backoff.test.mjs:31 [G G2 G2] | restore of setTimeout/clearTimeout | exempt because a restore
- api_ws_backoff.test.mjs:67 [G G2 R] | held backfill: every request waits on `release`, which each call overwrites | complies: a first connect of an empty capture issues exactly one request (api.js runBackfill: no rows, no seeds), so the one release is the backfill's
- api_ws_backoff.test.mjs:69 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- api_ws_gap.test.mjs:18 [G G2 R] | daemon stand-in dispatching on path and query: answers each route production calls in the daemon's shape for the fields production reads; since_id pages served by the test's pages() | complies
- api_ws_gap.test.mjs:29 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- app_layout_corrupt.test.mjs:8 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- app_layout.test.mjs:9 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- app_layout.test.mjs:52 [A R] | workspace rect | exempt because a layout knob (the stub has no layout)
- app_layout.test.mjs:70 [A R] | FakeEl.prototype rect for the drag, restored in finally | exempt because a layout knob; note the stub's querySelector('.side-body') is a detached element, see F-JS-2
- app_resizer_keys.test.mjs:9 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- app_resizer_keys.test.mjs:19 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- app_resizer_keys.test.mjs:70 [A] | focus recorder | complies: production reads no activeElement (grep), so recording the call is the whole effect under test
- app_resizer_keys.test.mjs:71 [A] | focus recorder | complies: as :70
- can_age_tick.test.mjs:9 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- can_age_tick.test.mjs:35 [A R] | performance.now advanced 5 s, restored | exempt because a clock knob
- can_age_tick.test.mjs:123 [A R] | performance.now advanced 2.5 s, restored | exempt because a clock knob
- can_bytediff.test.mjs:12 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- can_decode_once.test.mjs:10 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- can_decode_once.test.mjs:21 [G G2 R] | parseInt spy delegating to the real one | complies: counts calls, behaviour unchanged
- can_decode_once.test.mjs:32 [G G2] | restore | exempt because a restore
- can_head.test.mjs:11 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- can_logic.test.mjs:12 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- can_logic.test.mjs:333 [G G2 L] | localStorage whose setItem throws | complies: fault injection; every production write is in an untyped catch (grep localStorage.setItem), so the plain Error vs a QuotaExceededError DOMException is immaterial
- can_logic.test.mjs:345 [G G2] | restore | exempt because a restore
- chrome_radios.test.mjs:21 [A R] | focus recorder on test-built buttons | complies: asserts the click-then-focus order; no activeElement reader
- chrome_radios.test.mjs:30 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- chrome_radios.test.mjs:33 [L] | test-local helper object (not passed to production) | exempt because not a double
- chrome_radios.test.mjs:92 [L] | zoom controls {leave, exit} for chrome.js in isolation | complies: the shipped leave (plots.js clearZoom) ends in showZoom(null), which the fake calls too
- chrome_radios.test.mjs:102 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- chrome_radios.test.mjs:119 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- chrome_radios.test.mjs:122 [L] | test-local helper object | exempt because not a double
- chrome.test.mjs:154 [L] | zoom controls recording leave/exit and hiding the chip as the shipped ones do | complies
- chrome.test.mjs:169 [L] | no-op zoom controls installed at test end | exempt because never invoked afterwards
- clear_staged_backfill.test.mjs:43 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; each route recorded and gateable; /lines serves since_id-exclusive desc pages of 200 | complies
- clear_staged_backfill.test.mjs:56 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- clear_staged_backfill.test.mjs:504 [A R] | console.warn recorder, restored | complies
- cmdbar_bounds.test.mjs:18 [G G2 R] | daemon stand-in: /cmd answered as the daemon does, POSTs recorded with url and body | complies: tests find the /cmd post by url
- cmdbar_bounds.test.mjs:22 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- cmdbar_bounds.test.mjs:24 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- cmdbar_bounds.test.mjs:43 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_bounds.test.mjs:57 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_bounds.test.mjs:71 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_bounds.test.mjs:81 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_bounds.test.mjs:84 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_bounds.test.mjs:92 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_bounds.test.mjs:94 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_detached_pick.test.mjs:10 [G G2 R] | daemon stand-in answering every request with a /cmd reply; POSTs recorded | complies for the transport: the test asserts the body's port (the double at issue in this file is the <select>, see F-JS-3)
- cmdbar_detached_pick.test.mjs:12 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- cmdbar_detached_pick.test.mjs:23 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_eol.test.mjs:16 [G G2 R] | daemon stand-in answering every request with a /cmd reply; POSTs recorded | complies: send() selects the post whose url is /cmd
- cmdbar_eol.test.mjs:19 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- cmdbar_eol.test.mjs:26 [L] | browser <select> value semantics wrapped onto the stub (value no option carries reads "") | complies: makes the stub stricter; this is the wrapper cmdbar_detached_pick lacks (F-JS-3)
- cmdbar_eol.test.mjs:32 [A R] | appendChild wrapper selecting the first option, as a browser select does | complies
- cmdbar_eol.test.mjs:75 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_marker_answer.test.mjs:10 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- cmdbar_marker_answer.test.mjs:21 [G G2 R] | daemon stand-in: /cmd reply for /cmd, {line_id} for the rest (POST /marker answers {line_id}, server.py:2187); holdable | complies
- cmdbar_marker_answer.test.mjs:49 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_marker_answer.test.mjs:54 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_marker_answer.test.mjs:79 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- cmdbar_mode.test.mjs:14 [G G2 R] | daemon stand-in answering every request with a /cmd reply; POSTs recorded | complies: the test asserts posts[0].url (/send vs /cmd), and /send's reply is read only for ok (cmdbar.js 201-204)
- cmdbar_mode.test.mjs:17 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- cmdbar_mode.test.mjs:34 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_raw_line.test.mjs:9 [G G2 R] | daemon stand-in answering every request with a /cmd reply; POSTs recorded | complies: the test asserts url /send vs /cmd
- cmdbar_raw_line.test.mjs:11 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- cmdbar_raw_line.test.mjs:24 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_raw_line.test.mjs:41 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_refusal_inflight.test.mjs:11 [G G2 R] | daemon stand-in holding /cmd, answering every request with a /cmd reply | complies: dispatches on the /cmd path it holds
- cmdbar_refusal_inflight.test.mjs:13 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- cmdbar_refusal_inflight.test.mjs:32 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_sole.test.mjs:12 [G G2 R] | daemon stand-in answering every request with a /cmd reply; POSTs recorded | complies: tests assert url and body.port
- cmdbar_sole.test.mjs:14 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- cmdbar_sole.test.mjs:49 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_sole.test.mjs:104 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_sole.test.mjs:129 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_sole.test.mjs:164 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_sole.test.mjs:171 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- cmdbar_status_sync.test.mjs:12 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- cmdbar_status_sync.test.mjs:14 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; /status can fail as a network error (TypeError) | complies
- cmdbar_status_sync.test.mjs:43 [A] | focus recorder | complies: the assertion is that focus is (not) called
- cmdbar_status_sync.test.mjs:55 [A] | focus recorder | complies: as :43
- digital_clear_segments.test.mjs:46 [A] | path calls of the stub 2d context replaced by recorders | complies: the real methods return nothing
- digital_enum_label.test.mjs:10 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- digital_lane_draw.test.mjs:19 [C L L] | counting 2d context with measureText | complies: counts draw calls; digital.js reads measureText only on the ruler
- digital_lane_draw.test.mjs:35 [A R] | canvas.getContext returning the counting context | complies
- digital_repaint_key.test.mjs:20 [A C L R] | recording 2d context (every method returns calls.push's number) | complies: drawDigitalLane reads no return value; the only measureText read is the ruler's (digital.js:598)
- digital_repaint.test.mjs:28 [A R] | getContext spy delegating to the stub | complies
- digital_tick_reset.test.mjs:62 [C L] | recording 2d context | complies: as digital_repaint_key.test.mjs:20
- digital_tick_reset.test.mjs:80 [A R] | lane getContext -> recorder, plus width knob | complies
- digital_tick_reset.test.mjs:81 [A R] | lane getContext -> recorder, plus width knob | complies
- digital_tick_reset.test.mjs:95 [A L R] | elementFromPoint returning an element whose closest() ignores its selector | violates: F-JS-4 (driven M4)
- digital_tick_reset.test.mjs:118 [A R] | elementFromPoint reset to null | exempt because a restore to the stub's default
- digital_zoom_export.test.mjs:81 [L] | uPlot instance for onSelect {select, posToVal, setSelect} | complies: onSelect reads select, calls posToVal and setSelect(_, false) (plots.js 961-966); setSelect with fire=false fires no hook in uPlot either
- digital_zoom.test.mjs:15 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- dom_stub.mjs:31 [C] | DOMTokenList | complies on add/remove/contains/toggle(force)/value; diverges: independent of className (a real classList and className are one attribute) and of selectorTest; no instance found where production mixes both on one element and a test reads the other
- dom_stub.mjs:44 [C] | CSSStyleDeclaration | complies: setProperty/removeProperty/getPropertyValue; getComputedStyle answers empty and production falls back (`|| "monospace"`)
- dom_stub.mjs:53 [C] | CanvasRenderingContext2D: every method a no-op returning undefined | complies: only reached with clientWidth 0 or through test recorders; a measureText(...).width read on it would throw, so no passing test reaches one (the ruler tests supply their own context)
- dom_stub.mjs:54 [L] | Proxy handler of :53 | complies
- dom_stub.mjs:75 [C] | Element | complies except where the stub item list (section 1) says otherwise: querySelector and closest are findings F-JS-2 and F-JS-4, the <select> value is F-JS-3
- dom_stub.mjs:102 [A R] | canvas getContext returning the shared no-op context | complies
- dom_stub.mjs:212 [L] | document | complies except where the stub item list (section 1) says otherwise (getElementById manufactures any id: covered by test_webui.py:215)
- dom_stub.mjs:234 [L] | Storage over a Map | complies: getItem null when missing, setItem stringifies, key(), length
- dom_stub.mjs:251 [G2] | document install | complies: see :212
- dom_stub.mjs:252 [G2] | window === globalThis | complies
- dom_stub.mjs:253 [G2] | localStorage install | complies: see :234
- dom_stub.mjs:254 [G2 L R] | matchMedia never matching | complies: theme.js reads .matches once
- dom_stub.mjs:256 [G2] | location of a loopback page | complies: api.js builds ws:// from protocol/host
- dom_stub.mjs:258 [G2 R] | getComputedStyle -> empty style | complies (see :44)
- dom_stub.mjs:259 [G2 R] | window.addEventListener dropped | complies: window listeners (chrome.js:85 focus) are never driven by a test; nothing asserts their effect
- dom_stub.mjs:260 [G2 R] | window.removeEventListener no-op | complies
- dom_stub.mjs:261 [G2 R] | prompt cancelled | complies: a browser's cancel
- dom_stub.mjs:262 [G2 R] | alert no-op | complies
- dom_stub.mjs:263 [G2 R] | confirm answering Cancel | complies
- dom_stub.mjs:264 [G2 R] | requestAnimationFrame captured, run by the test | complies: production never cancels a frame (grep cancelAnimationFrame)
- dom_stub.mjs:265 [G2 R] | cancelAnimationFrame no-op | complies: no production caller
- dom_stub.mjs:266 [G2 R] | setInterval captured, never armed | complies: module-scope intervals ticked by hand
- dom_stub.mjs:267 [G2 R] | clearInterval no-op | complies: no production caller
- dom_stub.mjs:268 [C G2] | Blob holding parts; text() joins | complies: production passes Blobs through to saveBlob only
- dom_stub.mjs:272 [A R] | URL.createObjectURL recording the blob | complies
- dom_stub.mjs:273 [A R] | URL.revokeObjectURL no-op | complies
- dom_stub.mjs:274 [C G2] | WebSocket that never opens or closes by itself; close() fires no onclose | complies: tests call onopen/onmessage/onclose; connectWs nulls onclose before a deliberate close (api.js 657-659), so the missing close event cannot matter
- dom_stub.mjs:286 [C G2] | uPlot constructor storing opts/data; no hooks, no posToIdx/setSeries | complies: charts only build when a test sets a width, and those tests assign what they drive (plots_chrome posToIdx); a missing member throws, it does not pass
- dom_stub.mjs:294 [G2 L] | uPlot.paths.stepped | complies: builds a path fn never drawn
- dom_stub.mjs:298 [G2 L R] | uPlot.sync group with sub() only | complies: tests publish through the kept subscriber, as a hovered chart would
- exportdlg_capture_reset.test.mjs:11 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- exportdlg_capture_reset.test.mjs:14 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; exports recorded, /sessions holdable | complies
- exportdlg_capture_reset.test.mjs:24 [A R] | createElement wrapper arming anchors | complies: see :26
- exportdlg_capture_reset.test.mjs:26 [A] | anchor click recorded as a navigation | complies: a navigation is the whole effect of the real click; production registers no click listener on it
- exportdlg_capture_reset.test.mjs:44 [L] | a panel's export context {kind, watermark, shown, options, build} | complies: the full contract openExportDialog reads (ctx.* in exportdlg.js)
- exportdlg_guards.mjs:230 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- exportdlg_guards.mjs:233 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- exportdlg_guards.mjs:238 [G2 R] | the export double's fetch: /sessions answered from `sessions`; every other URL judged by refuse() and answered 400 {error} or 200 | complies for export URLs (contract-tested, test_webui_js.py:278); answers non-export routes 200 {} (refuse() returns null for an unknown path), harmless as no test using it reads such a body; installed without `ports`/`channels`, see F-JS-1
- exportdlg_guards.mjs:241 [L L] | /sessions Response of the export double | complies: shape {sessions}
- exportdlg_guards.mjs:249 [A R] | createElement wrapper arming anchors | complies
- exportdlg_guards.mjs:254 [A R] | anchor click judged by refuse() as a navigation, blob saves skipped | complies
- exportdlg_pending_close.test.mjs:14 [G G2 R] | router in front of the export double | complies: fallthrough ruled at exportdlg_guards.mjs:238
- exportdlg_pending_close.test.mjs:18 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- exportdlg_pending_close.test.mjs:39 [L] | test-local table of close actions | exempt because not a double
- exportdlg_pending_close.test.mjs:42 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- exportdlg_pending_close.test.mjs:49 [L] | panel export context whose build returns null (the CAN snapshot contract) | complies
- exportdlg_pending_close.test.mjs:54 [L] | panel export context, build returns null | complies
- exportdlg_pending_close.test.mjs:76 [L] | panel export context, build returns null | complies
- exportdlg_pending_close.test.mjs:89 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- exportdlg_pending_close.test.mjs:93 [L] | panel export context returning a /lines/export path | complies
- exportdlg_pending_close.test.mjs:99 [L] | panel export context returning a /plot/export path | complies
- exportdlg_pending_close.test.mjs:119 [A R] | AbortSignal.timeout capture; fired with a TimeoutError DOMException (:133) | complies: the reason production keys on
- exportdlg_pending_close.test.mjs:126 [L] | panel export context recording the session param | complies
- exportdlg_preflight_busy.test.mjs:14 [G G2 R] | daemon stand-in: /sessions?limit= answered, every other request by the per-test route (held/refused/answered) | complies: fetches recorded with url and opt and asserted
- exportdlg_preflight_busy.test.mjs:22 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- exportdlg_preflight_busy.test.mjs:23 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- exportdlg_preflight_busy.test.mjs:33 [A R] | held request rejecting with signal.reason on abort | complies: as fetch does
- exportdlg_preflight_busy.test.mjs:43 [A R] | createElement wrapper arming anchors | complies
- exportdlg_preflight_busy.test.mjs:46 [A R] | anchor click recorded as a navigation (blob saves skipped) | complies
- exportdlg_preflight_busy.test.mjs:62 [L] | panel export context returning a path | complies
- exportdlg_session_fill.test.mjs:17 [G G2 R] | daemon stand-in answering every request with the sessions list, holdable | complies: the tests' build() returns null, so no export request is issued; only /sessions is fetched
- exportdlg_session_fill.test.mjs:19 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- exportdlg_session_fill.test.mjs:33 [L] | panel export context recording the session param | complies
- exportdlg.test.mjs:321 [G G2 R] | spy over the export double recording the /sessions url | complies: delegates to the real stand-in
- exportdlg.test.mjs:326 [G G2] | restore of the export double | exempt because a restore, not a double
- exportdlg.test.mjs:389 [A] | focus recorders on the mode radios | complies
- exportdlg.test.mjs:401 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- exportdlg.test.mjs:402 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- freeze.test.mjs:19 [L] | freeze surface {isLive, setPaused} whose setPaused calls freezeChanged() | complies: as every shipped surface does (terminal.js:326, plots/digital/can setters); the class 27 bit, fixed
- freeze.test.mjs:65 [L] | surface lacking setPaused, for the registration refusal | complies: never invoked
- freeze.test.mjs:66 [L] | surface lacking isLive, for the registration refusal | complies: never invoked
- freeze.test.mjs:117 [L] | surface whose setPaused skips freezeChanged() | complies: that test never calls pauseAll, so setPaused never runs
- plots_adhoc_hold.test.mjs:13 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- plots_channel_cap.test.mjs:11 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- plots_channel_cap.test.mjs:21 [A R] | console.warn recorder, restored | complies
- plots_chrome.test.mjs:11 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- plots_chrome.test.mjs:58 [A R] | posToIdx on the built FakeUplot | complies: an index knob for paintChanValues
- plots_chrome.test.mjs:66 [A R] | posToIdx returning an index past the data | complies: stricter than uPlot (whose closestIdx stays in range); tests the guard
- plots_chrome.test.mjs:226 [A C L R] | ruler 2d context: measureText width 20, fillText recorded, other members no-ops, property sets dropped | complies: the ruler reads back no property it set
- plots_chrome.test.mjs:227 [L] | Proxy handler of :226 | complies
- plots_collapsed_sidebar.test.mjs:14 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- plots_decode_once.test.mjs:17 [A R] | DataView getFloat32 spy delegating | complies
- plots_decode_once.test.mjs:48 [G G2 R] | parseFloat spy delegating, restored | complies
- plots_decode_once.test.mjs:58 [G G2] | restore | exempt because a restore
- plots_export_button.test.mjs:49 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- plots_export_button.test.mjs:74 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- plots_export_button.test.mjs:91 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- plots_finite.test.mjs:21 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- plots_fold_cue.test.mjs:12 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- plots_fold_cue.test.mjs:16 [C G G2] | ResizeObserver whose callback the test fires with no entries | complies: plots.js:1453 ignores the entries
- plots_fold_cue.test.mjs:35 [A R] | rect spy counting forced layouts | complies: a layout knob that counts
- plots_fold_cue.test.mjs:38 [L] | write-counting descriptors on hidden/title | complies: behaviour unchanged
- plots_hover_tick.test.mjs:38 [A L R] | elementFromPoint returning an element whose closest() ignores its selector | violates: F-JS-4 (driven M4)
- plots_ingest_scope.test.mjs:13 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- plots_paused_freeze.test.mjs:16 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- plots_pause_edge.test.mjs:121 [L] | uPlot instance for onSelect | complies: as digital_zoom_export.test.mjs:81
- plots_pause_edge.test.mjs:154 [A L R] | elementFromPoint returning an element whose closest() ignores its selector | violates: F-JS-4 (driven M4)
- plots_pause_edge.test.mjs:162 [A R] | reset to null | exempt because a restore
- plots_rename_focus.test.mjs:13 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- plots_rename_focus.test.mjs:27 [A R] | titleEl.after recorder (the stub lacks after()) | complies: the box is not inserted, but the test drives it directly and removal of a detached FakeEl is a no-op either way
- plots_rename_focus.test.mjs:29 [A R] | focus counter | complies
- plots_rename_focus.test.mjs:47 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- plots_seed_grammar.test.mjs:98 [A R] | lane stand-in whose xsHost.push throws once | complies: fault injection; the next row landing in its vs ([0]) proves the row path ran on it
- plots_seed_grammar.test.mjs:105 [A R] | console.error recorder | complies: count-only (nit N-JS-4)
- plots_seed_grammar.test.mjs:129 [L] | lane stand-in whose `vs` getter throws | complies: fault injection for the group guard
- plots_seed_grammar.test.mjs:133 [A R] | console.error recorder | complies: count-only (nit N-JS-4)
- plots_seed_paused.test.mjs:35 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; /plot/series by name | complies
- plots_seed_paused.test.mjs:43 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- plots_solo.test.mjs:12 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- plots_solo.test.mjs:70 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- plots_solo.test.mjs:76 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- plots_solo.test.mjs:80 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- plots_theme_rebuild.test.mjs:17 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- plots_tick_reset.test.mjs:74 [A L R] | elementFromPoint returning an element whose closest() ignores its selector | violates: F-JS-4 (driven M4)
- plots_tick_reset.test.mjs:98 [A R] | reset to null | exempt because a restore
- plots_y_axis.test.mjs:15 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- plots_y_axis.test.mjs:68 [L] | host window for loading the vendored uPlot in a vm | exempt because scaffolding to run the real rangeNum, not a double of it
- plots_y_axis.test.mjs:69 [L] | matchMedia of that scaffold | exempt as :68
- plots_y_axis.test.mjs:71 [L L L] | document/canvas of that scaffold | exempt as :68
- plots_y_axis.test.mjs:73 [C] | CustomEvent class of that scaffold | exempt as :68
- plots_zoom_chip.test.mjs:10 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- plots_zoom_chip.test.mjs:45 [L] | uPlot instance for onSelect | complies: as digital_zoom_export.test.mjs:81
- plots_zoom_export.test.mjs:68 [L] | uPlot instance for onSelect | complies: as digital_zoom_export.test.mjs:81
- plots_zoom.test.mjs:11 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- plots_zoom.test.mjs:40 [L] | uPlot instance recording setSelect(opts, fire) | complies: the test asserts fire === false, the one argument that decides whether uPlot would re-enter the hook
- seed_backfill_pd_order.test.mjs:23 [G G2 R] | daemon stand-in dispatching on path and id_to: answers each route production calls in the daemon's shape for the fields production reads; the !pd query honours id_to | complies
- seed_backfill_pd_order.test.mjs:34 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_dirty.test.mjs:22 [G G2 R] | daemon stand-in dispatching on path and method: answers each route production calls in the daemon's shape for the fields production reads; PUTs recorded, one path can fail 500 | complies
- settings_dirty.test.mjs:27 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_dirty.test.mjs:29 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_dirty.test.mjs:31 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_dirty.test.mjs:32 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_dirty.test.mjs:33 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_dirty.test.mjs:34 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_dirty.test.mjs:35 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_dirty.test.mjs:40 [G G2 R] | confirm recorder with a canned answer | complies: the dialog text and the answer are the whole effect
- settings_dirty.test.mjs:159 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- settings_dirty.test.mjs:175 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- settings_dirty.test.mjs:182 [L] | event target whose closest() ignores its selector | violates: F-JS-4 (driven M5)
- settings_dirty.test.mjs:184 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- settings_dirty.test.mjs:188 [L L] | event target whose closest() returns an id-less section whatever the selector | violates: F-JS-4 (driven M5)
- settings_dirty.test.mjs:189 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- settings_export_hold.test.mjs:21 [A R] | performance.now as a test clock | exempt because a clock knob
- settings_export_hold.test.mjs:23 [A R] | Date.now as a test wall clock | exempt because a clock knob
- settings_export_hold.test.mjs:26 [G G2 R] | setTimeout capturing delays >= 100 ms | complies: fired with no extra args, and no production setTimeout passes any (grep)
- settings_export_hold.test.mjs:32 [G G2 R] | clearTimeout for captured and real ids | complies
- settings_export_hold.test.mjs:44 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_export_hold.test.mjs:46 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; the session .db answers with a Content-Disposition, as the daemon does | complies
- settings_export_hold.test.mjs:51 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_export_hold.test.mjs:55 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_export_hold.test.mjs:71 [A R] | createElement wrapper arming anchors | complies
- settings_export_hold.test.mjs:74 [A R] | anchor click sorted into navigation or blob save | complies
- settings_export_hold.test.mjs:82 [A R] | hooks.reportError recorder | complies
- settings_export_hold.test.mjs:100 [G G2 R] | holder wrapping the stand-in for /sessions?name= | complies: delegates
- settings_export_hold.test.mjs:105 [G G2 L] | restore of the stand-in | exempt because a restore
- settings_export_hold.test.mjs:245 [L] | browser `disabled` semantics (disabling blurs) wrapped onto the stub | complies: stricter than the stub, with a positive control (:257)
- settings_late_answers.test.mjs:10 [G G2 R] | confirm always true | complies
- settings_late_answers.test.mjs:13 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_late_answers.test.mjs:14 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_late_answers.test.mjs:79 [G G2 R] | daemon stand-in with held answers; answer() dispatches on path and method and keeps the revision contract (409 on a stale revision, config.py:560) | complies; the 409 text is mirrored by hand but production only displays it (no dispatch on text or err.status)
- settings_loading_hold.test.mjs:16 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_loading_hold.test.mjs:29 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; /config holdable or 503 | complies
- settings_loading_hold.test.mjs:32 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_loading_hold.test.mjs:115 [A R] | focus spy delegating to the stub | complies
- settings_offline.test.mjs:19 [G G2 R] | daemon stand-in dispatching on path and method: answers each route production calls in the daemon's shape for the fields production reads; `down` rejects every request as a network error | complies
- settings_offline.test.mjs:23 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_offline.test.mjs:24 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_offline.test.mjs:25 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_offline.test.mjs:26 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_offline.test.mjs:27 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_offline.test.mjs:30 [G G2 R] | confirm counter answering false | complies
- settings_offline.test.mjs:63 [A] | focus recorder | complies
- settings_pj_restart.test.mjs:15 [G G2 R] | router in front of the export double; settingsRoute dispatches on path and method | complies
- settings_pj_restart.test.mjs:19 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_pj_restart.test.mjs:57 [L] | test-local held-request record | exempt because not a double
- settings_pj_restart.test.mjs:110 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_pj.test.mjs:33 [G G2 R] | daemon stand-in dispatching on path and method: answers each route production calls in the daemon's shape for the fields production reads; PUT /plotjuggler echoes the applied state, PUT /config/plotjuggler answers {ok,restart_required} | complies
- settings_pj.test.mjs:42 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_pj.test.mjs:49 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_pj.test.mjs:53 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_pj.test.mjs:55 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_pj.test.mjs:59 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_pj.test.mjs:61 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_ports_baud.test.mjs:26 [G G2 R] | daemon stand-in: /devices, else the config body, PUTs recorded | complies: a PUT answered with the GET body lacks {ok,revision}; settings.js reads only answer.revision/restart_required (651-686), absent reads as an older daemon; tests assert the PUT body
- settings_ports_baud.test.mjs:30 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_ports_baud.test.mjs:32 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_ports_bound.test.mjs:21 [G G2 R] | as settings_ports_baud.test.mjs:26 | complies: asserts the PUT body
- settings_ports_bound.test.mjs:24 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_ports_bound.test.mjs:28 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_ports_eol.test.mjs:23 [G G2 R] | daemon stand-in dispatching on path and method: answers each route production calls in the daemon's shape for the fields production reads | complies
- settings_ports_eol.test.mjs:27 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_ports_eol.test.mjs:29 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_ports_eol.test.mjs:30 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_ports_eol.test.mjs:31 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_ports_eol.test.mjs:32 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_ports_eol.test.mjs:33 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_ports_identify.test.mjs:22 [G G2 R] | as settings_ports_baud.test.mjs:26 | complies
- settings_ports_identify.test.mjs:25 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_ports_identify.test.mjs:27 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_revision.test.mjs:39 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_revision.test.mjs:40 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_revision.test.mjs:42 [G G2 R] | daemon stand-in keeping the config revision contract by hand (stale revision 409, none unchecked, accepted PUT answers the next) | complies: matches config.py:560 (`revision is not None and revision != config_revision`); no contract test pins it, and the 409 text is only displayed
- settings_revision.test.mjs:83 [A R] | createElement wrapper arming anchors | complies
- settings_revision.test.mjs:85 [A] | anchor click recorded as a navigation | complies
- settings_revision.test.mjs:93 [A R] | hooks.reportError recorder | complies
- settings_revision.test.mjs:153 [A R] | daemon hook: a write lands between the dialog's GET and its PUT | complies: models another writer
- settings_revision.test.mjs:154 [A R] | daemon hook paired with :153 | complies
- settings_revision.test.mjs:164 [L] | daemon hook refusing a PUT 400 | complies
- settings_revision.test.mjs:187 [A R] | daemon hook bumping the revision once | complies
- settings_revision.test.mjs:270 [A R] | daemon hook holding PUTs | complies
- settings_revision.test.mjs:287 [A R] | daemon hook failing the first PUT 500 | complies
- settings_save_reread.test.mjs:39 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_save_reread.test.mjs:41 [G G2 R] | daemon stand-in dispatching on path and method: answers each route production calls in the daemon's shape for the fields production reads; holdable by `METHOD path` | complies
- settings_save_reread.test.mjs:64 [L] | test-local held-request record | exempt because not a double
- settings_save_reread.test.mjs:74 [A R] | hooks.reportError recorder | complies
- settings_save_reread.test.mjs:174 [G G2 R] | wrapper refusing every PUT 422 | complies: the refusal text is only displayed
- settings_save_reread.test.mjs:175 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_save_reread.test.mjs:180 [G G2] | restore | exempt because a restore
- settings_save_reread.test.mjs:191 [G G2 R] | wrapper failing the second GET /config as a network error | complies
- settings_save_reread.test.mjs:198 [G G2] | restore | exempt because a restore
- settings_save_reread.test.mjs:202 [G G2 R] | wrapper refusing every PUT 500 | complies
- settings_save_reread.test.mjs:203 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_save_reread.test.mjs:205 [G G2] | restore | exempt because a restore
- settings_save_reread.test.mjs:214 [A R] | AbortSignal.timeout capture, fired with a TimeoutError DOMException (:225) | complies
- settings_save_reread.test.mjs:242 [A R] | showModal counter keeping the stub's open attribute | complies
- settings_sessions_bundle.test.mjs:26 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; GETs recorded | complies
- settings_sessions_bundle.test.mjs:29 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_sessions_bundle.test.mjs:30 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_sessions_bundle.test.mjs:31 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_storage_cap.test.mjs:23 [G G2 R] | as settings_ports_baud.test.mjs:26 | complies: asserts the PUT body
- settings_storage_cap.test.mjs:26 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_storage_cap.test.mjs:28 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_user_text.test.mjs:27 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads | complies
- settings_user_text.test.mjs:30 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_user_text.test.mjs:33 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_user_text.test.mjs:34 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- settings_user_text.test.mjs:54 [G G2 R] | confirm recorder answering false | complies
- smoke.test.mjs:16 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- state_decoder_tick.test.mjs:11 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- state_download_navigate.test.mjs:15 [G G2 R] | daemon stand-in answering every request 200 with a sessions list | complies: F-18 asserts one fetch and the anchor href, not the preflight's url; the url each preflight takes is pinned by state_preflight_session_ref and state_download_preflight
- state_download_navigate.test.mjs:18 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_download_navigate.test.mjs:28 [A R] | createElement spy collecting anchors | complies: the real click still runs
- state_download_navigate.test.mjs:33 [L] | restore helper | exempt because a restore
- state_download_preflight.test.mjs:16 [G G2 R] | daemon stand-in: /sessions?limit= answered, every other request by the per-test route | complies: fetches recorded and asserted
- state_download_preflight.test.mjs:24 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_download_preflight.test.mjs:25 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_download_preflight.test.mjs:35 [A R] | held request rejecting with signal.reason on abort | complies
- state_download_preflight.test.mjs:45 [A R] | createElement wrapper arming anchors | complies
- state_download_preflight.test.mjs:48 [A R] | anchor click recorded as a navigation | complies
- state_download_preflight.test.mjs:65 [L] | panel export context returning a path | complies
- state_download_preflight.test.mjs:106 [G G2 R] | setTimeout capturing only STATUS_TIMEOUT_MS | complies: fires production's own callback
- state_download_preflight.test.mjs:114 [G G2] | restore | exempt because a restore
- state_download_preflight.test.mjs:139 [L] | panel export context returning a path | complies
- state_download_wait.test.mjs:13 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; the session export answers 503 BUSY, as the build pool does | complies
- state_download_wait.test.mjs:17 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_download_wait.test.mjs:21 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_download_wait.test.mjs:23 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_download_wait.test.mjs:29 [A R] | createElement wrapper arming anchors | complies
- state_download_wait.test.mjs:32 [A R] | anchor click recorded as a navigation | complies
- state_eol.test.mjs:18 [G G2 L] | Map-backed localStorage | complies: getItem null for a missing key, setItem stringifies, as Storage does
- state_eol.test.mjs:74 [G G2 L] | localStorage that throws on every access | complies: fault injection; production catches untyped
- state_eol.test.mjs:86 [G G2] | restore | exempt because a restore
- state_line_tick.test.mjs:13 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- state_line_tick.test.mjs:19 [A R] | hooks.canTick recorder with a canned answer | complies: the subject is state.js's dispatch; state_decoder_tick.test.mjs drives the real decoders
- state_line_tick.test.mjs:20 [A R] | hooks.adhocTick recorder | complies: as :19
- state_line_tick.test.mjs:21 [A R] | hooks.plotSampleTick recorder | complies: as :19
- state_logic.test.mjs:15 [G G2 R] | indirection to a per-test fetchImpl | complies: each fetchImpl is ruled with its test (statuses and bodies are what the test is about)
- state_logic.test.mjs:28 [A R] | hooks.plotSampleTick hand decoder for a two-field stream 0 on p1 | complies: state_plot_tick.test.mjs drives state.js with the real plots.js hook
- state_logic.test.mjs:69 [A R] | counting wrapper over :28, restored | complies
- state_logic.test.mjs:159 [A R] | createElement spy collecting anchors | complies
- state_logic.test.mjs:166 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_logic.test.mjs:168 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_logic.test.mjs:191 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_logic.test.mjs:194 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_logic.test.mjs:206 [A R] | createElement spy collecting anchors | complies
- state_logic.test.mjs:212 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_logic.test.mjs:248 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_logic.test.mjs:263 [G G2 R] | prompt counter | complies: asserts it is not called
- state_logic.test.mjs:273 [A R] | hooks.authFailed counter | complies
- state_logic.test.mjs:274 [G G2 R] | prompt cancelled (null), as a browser returns | complies
- state_logic.test.mjs:282 [G G2 R] | prompt returning a wrong token each time | complies
- state_logic.test.mjs:293 [G G2 R] | prompt returning a token | complies
- state_logic.test.mjs:297 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_logic.test.mjs:298 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_logic.test.mjs:307 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_logic.test.mjs:309 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_logic.test.mjs:363 [A R] | createElement spy collecting anchors | complies
- state_logic.test.mjs:371 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_logic.test.mjs:375 [G G2 R] | prompt cancelled | complies
- state_plot_tick.test.mjs:15 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- state_preflight_session_ref.test.mjs:13 [G G2 R] | daemon stand-in answering every request with a sessions list; urls recorded | complies: the tests assert the exact url fetched
- state_preflight_session_ref.test.mjs:15 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- state_preflight_session_ref.test.mjs:26 [A R] | createElement spy collecting anchors | complies
- state_preflight_session_ref.test.mjs:31 [L] | restore helper | exempt because a restore
- statusbar_attach_eol.test.mjs:12 [G G2 R] | daemon stand-in dispatching on path and method: answers each route production calls in the daemon's shape for the fields production reads; requests recorded with method and body | complies
- statusbar_attach_eol.test.mjs:16 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_attach_eol.test.mjs:17 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_attach_eol.test.mjs:18 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_attach_loading.test.mjs:10 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_attach_loading.test.mjs:16 [G G2 R] | daemon stand-in dispatching on path and method: answers each route production calls in the daemon's shape for the fields production reads; /devices and POST /ports holdable | complies
- statusbar_attach_open.test.mjs:12 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_attach_open.test.mjs:14 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads; /devices holds, or stalls until its signal aborts with signal.reason, as fetch does | complies
- statusbar_attach_open.test.mjs:38 [A R] | showModal counter keeping the stub's open attribute | complies
- statusbar_attach_open.test.mjs:76 [A R] | AbortSignal.timeout capture, fired with a TimeoutError DOMException (:87) | complies
- statusbar_can_now.test.mjs:11 [G G2 R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads | complies
- statusbar_can_now.test.mjs:16 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_late_answers.test.mjs:10 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_late_answers.test.mjs:11 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_late_answers.test.mjs:31 [G G2 R] | daemon stand-in dispatching on path and method: answers each route production calls in the daemon's shape for the fields production reads; calls recorded, holdable | complies
- statusbar_logic.test.mjs:22 [G G2 R] | daemon stand-in: /devices, else the status body; writes recorded with path, method, body; can fail | complies: tests assert `posted`; write answers are not read by statusbar.js
- statusbar_logic.test.mjs:29 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_logic.test.mjs:30 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_logic.test.mjs:541 [A R] | console.error recorder, restored | complies: count-only (nit N-JS-4)
- statusbar_proto.test.mjs:17 [G G2 R] | daemon stand-in dispatching on path and method: answers each route production calls in the daemon's shape for the fields production reads; posts recorded | complies
- statusbar_proto.test.mjs:19 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_proto.test.mjs:20 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_proto.test.mjs:38 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- statusbar_refresh_fresh.test.mjs:10 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_refresh_fresh.test.mjs:23 [G G2 R] | daemon stand-in dispatching on path and method: answers each route production calls in the daemon's shape for the fields production reads; DELETE /ports/sim detaches, /status reads state at answer time | complies
- statusbar_reload_notice.test.mjs:12 [A R] | document.querySelector answering the version <meta> as {content} | complies: statusbar reads only .content
- statusbar_reload_notice.test.mjs:15 [G G2 R] | daemon stand-in answering every request with the status body | complies: statusbar reads only /status on these paths
- statusbar_reload_notice.test.mjs:17 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_reload_notice.test.mjs:20 [G2 R] | location.reload counter | complies
- statusbar_reload_stamp.test.mjs:13 [A R] | as statusbar_reload_notice.test.mjs:12 | complies
- statusbar_reload_stamp.test.mjs:15 [G G2 L R] | as statusbar_reload_notice.test.mjs:15 | complies
- statusbar_session_dialog.test.mjs:15 [G G2 R] | daemon stand-in dispatching on path and method: answers each route production calls in the daemon's shape for the fields production reads; every POST (/sessions, /sessions/stop) answers {session} and is recorded with its path | complies
- statusbar_session_dialog.test.mjs:21 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_session_dialog.test.mjs:22 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_session_dialog.test.mjs:24 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_session_dialog.test.mjs:25 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- statusbar_session_dialog.test.mjs:28 [G G2 R] | prompt counter | complies: asserts the dialog replaced the prompt
- statusbar_session_dialog.test.mjs:49 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- statusbar_session_dialog.test.mjs:141 [L] | event stand-in {key/altKey/shiftKey/target, preventDefault}: the only members the handlers read (grep e.<field> over webui) | complies: emitted on the element holding the listener, so no bubbling is needed
- statusbar_user_text.test.mjs:14 [G G2 L R] | daemon stand-in dispatching on path: answers each route production calls in the daemon's shape for the fields production reads | complies
- terminal_createpane.test.mjs:31 [G G2 R] | capture stand-in: rows below id_to down to since_id, always `truncated: true`, ignoring limit/port/chan/match | complies: the pane is port all, every channel, no pattern, and the subject is a writer racing a held page, not paging termination
- terminal_createpane.test.mjs:38 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- terminal_delta_mark.test.mjs:9 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- terminal_delta_mark.test.mjs:95 [A R] | performance.now charging 1 ms per call, restored | exempt because a clock knob
- terminal_empty_state.test.mjs:11 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- terminal_filter_pane.test.mjs:11 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- terminal_history.test.mjs:20 [G G2 R] | capture stand-in honouring id_to, since_id, limit, port and chan, answering `truncated`; ignores match unless refusing it | complies: the file says so (line 226) and the pane re-filters each page (terminal.js 531); the no-match hop test thereby runs the dialect-refused fallback walk, a reachable state
- terminal_history.test.mjs:24 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- terminal_history.test.mjs:39 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- terminal_history.test.mjs:187 [G G2 R] | fetch rejecting every request | complies: the subject is the failure report
- terminal_history.test.mjs:190 [A R] | hooks.reportError recorder | complies: asserts the text
- terminal_history.test.mjs:199 [G G2] | restore | exempt because a restore
- terminal_history.test.mjs:200 [A R] | reset to a no-op | exempt because a restore (the state.js default is a no-op until app.js installs the toast)
- terminal_limits.test.mjs:17 [G G2 R] | capture stand-in honouring id_to, since_id, limit | complies: panes are unfiltered
- terminal_limits.test.mjs:25 [L L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- terminal_logic.test.mjs:12 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- terminal_logic.test.mjs:180 [A R] | RegExp.prototype.test spy delegating, restored | complies
- terminal_paused_freeze.test.mjs:16 [G G2 L R] | daemon stand-in answering every request with `served` | complies: the !pd seed query also gets `served` rows, which carry no !pd and decode to nothing; the subject is the pane freeze
- terminal_regex_count.test.mjs:20 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- terminal_tick_estimate.test.mjs:76 [G G2 L R] | daemon stand-in answering every request with `served` | complies: as terminal_paused_freeze.test.mjs:16
- terminal_tick_estimate.test.mjs:77 [L] | Response stand-in {ok,status,headers.get,json,blob}: the only members state.js api/refusalText/preflight/downloadPath read | complies; ruled with this file's fetch stub
- terminal_timemode.test.mjs:13 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- terminal_ts_width.test.mjs:12 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- theme_storage.test.mjs:16 [G G2 L] | localStorage that throws on every access | complies: fault injection
- timewindow_axis_fit.test.mjs:13 [G G2 R] | fetch stand-in for an unreachable daemon: rejects every request | complies: real fetch rejects (TypeError) and production keys on no error type but TimeoutError; nothing asserted depends on daemon data
- timewindow_axis_fit.test.mjs:55 [A C L R] | ruler 2d context measuring 6.1 px per character, recording fillText | exempt because the metric is a knob (the stub has no font metrics); the fit rule under test is fed through it
- timewindow_axis_fit.test.mjs:57 [L] | Proxy handler of :55 | exempt as :55
- timewindow_axis_fit.test.mjs:79 [A C L R] | ruler 2d context with moveTo recorder | exempt as :55
- timewindow_axis_fit.test.mjs:82 [L] | Proxy handler of :79 | exempt as :55

##### Rulings, item 2: exportdlg_guards.mjs (23 items)
- exportdlg_guards.mjs:8 MAX_LINE_ID | complies: pinned by GUARD_URLS since_id/id_to 9223372036854775807 and 9223372036854775808
- exportdlg_guards.mjs:9 MIN_LINE_ID | complies: pinned by since_id=-9223372036854775809
- exportdlg_guards.mjs:10 MAX_MS | complies: pinned by last_ms 1000000000000000 and 1000000000000001
- exportdlg_guards.mjs:11 MAX_MATCH_LEN | complies: pinned by match of 200 and 201 characters (and 200/201 emoji)
- exportdlg_guards.mjs:12 MAX_DECIMAL_DIGITS | complies: no GUARD_URL has a numeric session ref over 20 digits, but past the cap the double falls back to the name lookup, which gives the same "no such session" unless a session is named with 21+ digits
- exportdlg_guards.mjs:13 CAN_ID_MAX_EXT | complies: pinned by id 0x1FFFFFFF and 0x20000000
- exportdlg_guards.mjs:14 CHANS | complies: pinned by the chan cases
- exportdlg_guards.mjs:15 BOOLS | complies: pinned by decode=2, "", " true", 00, Yes/ON
- exportdlg_guards.mjs:18 pyRepr | complies: pinned by the repr cases (quotes, backslash, controls, NBSP, soft hyphen, LS, emoji)
- exportdlg_guards.mjs:35 trimWs | complies: pinned by " 1 ", NBSP and BOM since_ts cases
- exportdlg_guards.mjs:38 pyInt | complies: pinned by the id_to lax-int cases
- exportdlg_guards.mjs:46 pyFloat | complies: pinned by the since_ts float cases
- exportdlg_guards.mjs:56 deadbandNumber | complies: pinned by the deadband value cases (the 2026-09-15 v=+5 class)
- exportdlg_guards.mjs:60 validate | complies: 422 pass order and joining pinned by the multi-error URLs
- exportdlg_guards.mjs:92 LINES_SPEC | complies: pinned
- exportdlg_guards.mjs:97 CAN_SPEC | complies: pinned
- exportdlg_guards.mjs:102 PLOT_SPEC | complies: pinned
- exportdlg_guards.mjs:110 DECLARED | complies: unknown-parameter refusal pinned by chans/limit and bogus. Not pinned: that each list names every parameter server.py declares (a route gaining a parameter would be refused by the double, which fails a browser test loudly, not silently)
- exportdlg_guards.mjs:122 refuse | complies: contract-tested, test_webui_js.py:278 (210 URLs, word for word; every refusal literal reached)
- exportdlg_guards.mjs:222 installExportDaemon | violates: F-JS-1 (never passes ports; no caller passes channels)
- exportdlg_guards.mjs:238 fetch | see the union table
- exportdlg_guards.mjs:249 createElement wrapper | see the union table
- exportdlg_guards.mjs:254 anchor click | see the union table

##### Rulings, extra shapes

###### x1: `<select>` value read-backs (16)
- cmdbar_eol.test.mjs:48 | complies: #cmdEol wrapped with browserSelect (:24-33)
- cmdbar_eol.test.mjs:85 | complies: wrapped
- cmdbar_eol.test.mjs:128 | complies: wrapped
- cmdbar_eol.test.mjs:144 | complies: wrapped
- cmdbar_detached_pick.test.mjs:36 | violates: F-JS-3 (driven M3)
- cmdbar_sole.test.mjs:101 | complies: "auto" is always the first option (cmdbar.js:55)
- exportdlg_session_fill.test.mjs:53 | complies: the expected "7" is a listed option; a stale unlisted value would read differently and fail
- exportdlg.test.mjs:88 | complies: "wide" is among that chart's choices by construction (plots.js:1385)
- exportdlg.test.mjs:198 | exempt because #expOpt_ids is an input, not a select
- exportdlg.test.mjs:305 | complies: as exportdlg_session_fill:53
- exportdlg.test.mjs:312 | complies: "4" is listed
- settings_ports_bound.test.mjs:40 | complies: the options are asserted at :38
- settings_ports_bound.test.mjs:41 | complies: as :40
- statusbar_attach_eol.test.mjs:87 | complies: the options are asserted at :25
- settings_user_text.test.mjs:66 | complies: option 0 carries NAME (asserted at :65)
- settings_ports_eol.test.mjs:53 | complies: settings.js:547 writes only values of EOL_CHOICES

###### x2: DOM members replaced by a non-literal (15; the command in the enumeration list returns these 15)
- app_layout.test.mjs:77 | exempt because a restore
- plots_fold_cue.test.mjs:49 | exempt because a layout knob (rect factory)
- plots_fold_cue.test.mjs:50 | exempt because a layout knob
- plots_fold_cue.test.mjs:70 | exempt because a layout knob
- plots_chrome.test.mjs:198 | exempt because a layout knob
- plots_chrome.test.mjs:199 | exempt because a layout knob
- plots_chrome.test.mjs:200 | exempt because a layout knob
- plots_chrome.test.mjs:201 | exempt because a layout knob
- plots_chrome.test.mjs:209 | exempt because a layout knob
- plots_chrome.test.mjs:210 | exempt because a layout knob
- state_logic.test.mjs:184 | exempt because a restore
- state_logic.test.mjs:234 | exempt because a restore
- state_logic.test.mjs:382 | exempt because a restore
- state_download_navigate.test.mjs:33 | exempt because a restore
- state_preflight_session_ref.test.mjs:31 | exempt because a restore

###### x3: hand-built objects put into production collections (22)
- makePane panes pushed into terminal.js `panes` | complies, ruled at dom_stub.mjs:307. Each file builds its panes only with makePane (`grep -c 'makePane('`, no other builder):
  - api_backfill_paging:59
  - api_backfill:32, :47
  - api_capture_reset_history:23
  - api_high_rate_pending:31, :50
  - api_pane_queue:51
  - api_ws_gap:52
  - terminal_regex_count:49
  - terminal_empty_state:78, :126, :140
  - terminal_filter_pane:23, :113
  - terminal_logic:234, :253, :305, :321
  - terminal_flush_append:18
  - terminal_paused_freeze:23
- plots_seed_grammar.test.mjs:102 | complies: fault-injection lane, see union table :98
- plots_seed_grammar.test.mjs:129 | complies: fault-injection lane, see union table :129

## Class 28. An assertion the test's own guard swallows

Swept at f31ecd9. HEAD moved to 1a1251a during the sweep; its one new test (test_pidfile.py) adds no except, raise or suppress. Every test_pidfile.py line after 62 is +19 at 1a1251a.
Python is my own sweep. JS is the JS helper's; its list is reproduced below.

### Findings

Listed at the top of this file.

### Sweep method and site counts

Registry sweeps, run as written:
- `grep -rn "raise AssertionError" host/tests`: 19 sites (sweep A).
- Every `except` in a test file, by AST (`c28_ast.py except`): 49 handlers (sweep B).
- Every `pytest.raises`, by AST with the `as` name's later uses (`c28_raises.py`): 209 (sweep C).
  - The rulings were generated from a per-site map (`c28_rule_raises.py`, 0 unruled).
  - 108 are narrow project types. 101 were read one by one, including every stdlib type without `match=` and every typer.Exit/SystemExit.

Improved sweeps (the registry's misses a site shape, see below):
- `grep -n "suppress(" host/tests/*.py`: 21 (sweep D).
- Assertions inside a try or suppress that catches bare/BaseException/Exception/AssertionError (`c28_swallow.py`): 12 guarded bodies, none holding an assertion (sweep E).
- Assertions inside nested functions or methods that something other than the test calls (`c28_nested.py`): 149 (sweep F).
- tools/ci_sim_smoke.py and tools/webui_smoke.py (CI and manual smoke scripts): 7 except sites. All are timeouts or cleanup that `_fail` or a returned 1 follow, so they comply.
- JS: see the JS list: 4 throws/rejects, 42 try (41 try/finally, 1 try/catch), 1 code catch, 0 `.catch(`, 1 `.then(`, 81 nested asserts. No findings.

### Registry sweep imprecise

- The entry names `raise AssertionError` and `except Exception`, but not `contextlib.suppress(Exception)`, which is the same swallow by another spelling. R28-1 was found only through the improved sweep D.
  - Add `grep -n "suppress(" host/tests/*.py`, and rule any suppress wrapping `wait_for` as a timeout swallow.
- The entry misses the indirect form: an assertion in a double, thread target or handler that production code or the thread excepthook can swallow. R28-4 is one.
  - Add the nested-assert AST (`c28_nested.py`) and rule every site whose caller is a Thread, an executor, or production code with a broad except.
- "pytest.raises on a stdlib type without match=": a `match=` is not always the fix. R28-3 needs a positive control that the code under test was reached.
  - Say "match= or a positive control", and point at class 78.
- "pytest.raises(typer.Exit) asserts the code too" is overbroad as written. Six sites raise Exit only as the scripted end of a stream, and assert the output that is their subject.
  - Scope the rule to tests whose subject is the exit: test_cli.py:2087, :2268, :2828, test_cli_decode_rejected_defs.py:53, test_cli_follow_frames.py:46, test_daemon_startlog.py:87 (a SystemExit).

### Owed on Windows or a real browser

Nothing class-specific. The EINVAL paths some of these tests drive in process (test_cli.py:399-417) are Windows-simulated and owed with class 13.

### The two questions (classes 27 and 28)

1. What am I least confident about?
   - That the helpers' "complies" rulings hold: most are reasoned from the production code, not driven (A and B1 drove a few).
     - I re-drove 4 of the 7 MEDIUM helper findings (FB1-1, FB2-1 cmd part, FC2-2, FD-2), plus FC1-1/FD-1 and F-JS-4 M5. All reproduced.
     - I listed the 53 `recorder(` callers by AST with their request assertions and read the small_refusals ones (a local `_recorder` asserting every call). Nothing contradicted the helpers' rulings.
     - I rechecked coverage mechanically (check_cover.py, every enumerated file:line present).
     - Not re-driven: F-JS-3 and the LOW helper findings.
   - R27-3 (hardware filter never widened) is reasoned only and needs the owner's contract call.
   - R28-4 is reasoned only.
2. What should we have checked that we have not?
   - Whether the simulator and the C monitor agree (they are doubles of each other for the host tests).
     - Driven: `scratch-27-28/diffmon/diff.py` sends 120 command lines to both and compares verdicts.
     - The only difference is `mark` (C: ERR 1 badcmd, sim: OK). SPEC:1503 documents `mark` as the simulator's stand-in for `monitor_mark()`, so it is intended.
     - Payloads were not compared (the device models differ by design).
   - Not done: a class-wide AST check for Python doubles that ignore a parameter the real call consumes. That is the shape behind R27-1, R27-2, R27-9 and R27-11.
     - The helpers read each double instead. A mechanical version (replacement function parameters never read in its body) would make it repeatable.
   - Also not done: layout-knob assignments in the JS tests (clientWidth, scrollTop) were not enumerated. They are inputs, not behaviour.

### Python verdict lists
### Sweep A (registry): `grep -rn "raise AssertionError" host/tests`: 19 sites

Enclosing try per site taken by AST (`scratch-27-28/c28_ast.py raise`), then each read.

- host/tests/test_port_health.py:350 | wait_reason helper, no enclosing try | complies
- host/tests/test_source_link.py:49 | in the `else:` of try/except SerialException | complies (the registry's correct shape)
- host/tests/test_source_link.py:64 | in `else:` | complies
- host/tests/test_source_link.py:137 | in `else:` | complies (reasoned: Scripted.feed never raises, so without the closed check write() returns and the else fires)
- host/tests/test_plot_export_refusals.py:152 | helper, no try | complies
- host/tests/test_sim_tcp.py:55 | helper, no try | complies
- host/tests/test_sim_tcp.py:201 | inside `except OSError` handler of the recv try; the outer try is try/finally | complies
- host/tests/test_sim_tcp.py:293 | outer try is try/finally only | complies
- host/tests/test_protocol.py:949 | raised from `except Exception` as the assertion (re-raise with the type named) | complies
- host/tests/test_server_guards.py:357 | raised by the inner_app double when called; `_TokenGuard.__call__` has no except around `self.app` | complies
- host/tests/test_cli.py:678 | `_Resp.text` double raising if the body is read whole; cli.py has no `except Exception`/AssertionError arm (only BaseException arms that re-raise, 1143/1152/1270/1279) | complies
- host/tests/test_cli.py:698 | `_FakeHttp.request` double; as 678 | complies
- host/tests/test_cli.py:1468 | websockets.connect double; as 678 | complies
- host/tests/test_cli.py:1651 | no try | complies
- host/tests/test_cli.py:1677 | no try | complies
- host/tests/test_cli.py:2403 | MockTransport handler double | complies (driven: mutant `if False:` for the --rtr range check at cli.py:2045 fails the test with "AssertionError: the command reached the daemon")
- host/tests/test_sim_pty.py:44 | helper, no try | complies
- host/tests/test_sim_pty.py:61 | outer try is try/finally only | complies
- host/tests/test_sim_pty.py:106 | helper, no try | complies

### Sweep B (registry): every `except` in a test file: 49 handlers

Command: `scratch-27-28/c28_ast.py except` (AST over host/tests/*.py; every ExceptHandler with its caught types and body).

- host/tests/support.py:280 | except httpx.HTTPError: pass in the readiness poll | exempt because a poll loop, not a test body; failure surfaces as the RuntimeError at 285
- host/tests/support.py:292 | wait_connected poll | exempt because as 280; the caller asserts the bool
- host/tests/test_cli.py:413 | except OSError -> `assert not windows, exc` | complies (asserts on what it caught)
- host/tests/test_cli.py:550 | except TimeoutExpired: proc.kill() in finally cleanup | exempt because teardown
- host/tests/test_cli.py:813 | `_answers` probe returns False on HTTPError | exempt because a liveness probe helper, callers assert its result
- host/tests/test_cli.py:1333 | TimeoutExpired cleanup | exempt because teardown
- host/tests/test_cli.py:1981 | except BaseException -> result.append(exc), asserted isinstance typer.Exit and exit_code == 1 at 1989 | complies
- host/tests/test_cli_closed_output.py:72 | TimeoutExpired -> rc "hung", which every caller's `rc == N` assertion rejects | complies
- host/tests/test_cli_follow.py:40 | OSError: pass in a stalling listener's accept thread | exempt because harness server, not the act under test
- host/tests/test_cli_follow.py:242 | except typer.Exit -> code; code asserted later in the test | complies
- host/tests/test_cli_transport_timeouts.py:60 | accept thread OSError: pass | exempt because harness server
- host/tests/test_daemon_config_path.py:132 | symlink unsupported -> pytest.skip | exempt because platform capability skip, narrow types
- host/tests/test_daemon_process.py:124 | readiness poll | exempt because poll loop
- host/tests/test_daemon_process.py:216 | `_serve_outcome` returns the exception; every caller asserts its type and code | complies
- host/tests/test_daemon_startup.py:27 | ipv6 capability probe | exempt because capability probe
- host/tests/test_daemon_startup.py:31 | as 27 | exempt
- host/tests/test_daemon_startup.py:428 | BaseException -> errors, asserted `errors == []` | complies
- host/tests/test_pane_regex_dialect.py:29 | `except Exception: got = "refused"` | VIOLATES (R28-2)
- host/tests/test_pidfile.py:166 | OSError while polling a file being written -> not ready | exempt because poll loop; content asserted after
- host/tests/test_plotjuggler.py:417 | TimeoutError -> break (drain loop); the `else: pytest.fail` is the assertion | complies
- host/tests/test_protocol.py:649 | ProtocolError -> continue (a refused input is skipped in a round-trip fuzz) | complies (narrow project type)
- host/tests/test_protocol.py:732 | ProtocolError -> False helper | complies (narrow type)
- host/tests/test_protocol.py:744 | as 732 | complies
- host/tests/test_protocol.py:948 | except Exception -> raise AssertionError naming the type | complies (re-raises)
- host/tests/test_scaffold.py:55 | PackageNotFoundError -> False | exempt because install probe, narrow type
- host/tests/test_scaffold.py:121 | BaseException -> outcome, asserted isinstance pytest.fail.Exception | complies
- host/tests/test_server_export_pool.py:45 | build double: records and re-raises _ExportAbandoned | complies (re-raises)
- host/tests/test_server_export_pool.py:77 | TimeoutException -> "gave up" marker; tests assert on it | complies
- host/tests/test_server_exports.py:225 | build double records and re-raises | complies
- host/tests/test_sim.py:534 | (TimeoutError, OSError) -> "" (the healthy-while-dead symptom), callers assert the line | complies
- host/tests/test_sim_pty.py:83 | TimeoutExpired cleanup | exempt because teardown
- host/tests/test_sim_pty.py:89 | OSError on close | exempt because teardown
- host/tests/test_sim_pty.py:114 | cleanup | exempt because teardown
- host/tests/test_sim_pty.py:161 | BaseException -> result["exc"], asserted absent at 169 | complies
- host/tests/test_sim_pty.py:191 | fd close in finally | exempt because teardown
- host/tests/test_sim_tcp.py:43 | TimeoutError -> break, then the helper raises AssertionError | complies
- host/tests/test_sim_tcp.py:198 | TimeoutError -> continue under a deadline, `assert answered` after | complies
- host/tests/test_sim_tcp.py:200 | OSError -> raise AssertionError | complies
- host/tests/test_sim_tcp.py:247 | TimeoutError -> break, then `assert dropped` | complies
- host/tests/test_sim_tcp.py:249 | OSError -> dropped = True (a reset is a drop) | complies
- host/tests/test_sim_tcp.py:305 | cleanup | exempt because teardown
- host/tests/test_source_link.py:46 | except SerialException -> assert "gone" in str(exc) | complies
- host/tests/test_source_link.py:61 | except SerialException: pass, else raise | complies (the only SerialException source is the scripted "eof")
- host/tests/test_source_link.py:134 | except SerialException: pass, else raise | complies (see sweep A)
- host/tests/test_status_ppid_serial.py:89 | readiness poll | exempt because poll loop
- host/tests/test_store_fastpaths.py:229 | sqlite3.OperationalError -> errors, asserted len == 1 at 238 | complies (the bad query is the only failing statement)
- host/tests/test_wait_repeat.py:54 | stimulus thread stops on HTTPError | exempt because harness stimulus
- host/tests/test_webui_js.py:35 | node probe | exempt because capability probe
- host/tests/test_webui_js.py:39 | node version parse | exempt because capability probe

### Sweep C (registry): every `pytest.raises` on a stdlib type without match=, and every typer.Exit/SystemExit/click raise without a code assertion: 209 pytest.raises sites

Command: `scratch-27-28/c28_raises.py` (AST: every `with pytest.raises(...)` plus call forms, with the `as` name's later uses). 108 are narrow project types (complies by the registry rule); 101 were read one by one.

host/tests/test_capture_lock.py:32 | raises(LockError) | complies: narrow project type
host/tests/test_capture_lock.py:91 | raises(LockError) | complies: narrow project type
host/tests/test_capture_lock.py:161 | raises(LockError) | complies: narrow project type
host/tests/test_capture_lock.py:249 | raises(LockError) | complies: narrow project type
host/tests/test_cli.py:258 | raises(BrokenPipeError) | complies: the stream double raises OSError(EINVAL); BrokenPipeError is the translation under test and nothing else in write() raises it
host/tests/test_cli.py:260 | raises(BrokenPipeError) | complies: as 258, for flush()
host/tests/test_cli.py:272 | raises(OSError) match= | complies: match= present
host/tests/test_cli.py:343 | raises(OSError) match= | complies: match= present
host/tests/test_cli.py:365 | raises(typer.Exit) | complies: exit code asserted ['367:ei.value.exit_code']
host/tests/test_cli.py:392 | raises(typer.Exit) | complies: exit code asserted ['396:ei.value.exit_code']
host/tests/test_cli.py:1874 | raises(typer.Exit) | exempt because the exit code of this call is asserted on the same function two lines up (1870); this one pins the record cleanup
host/tests/test_cli.py:1959 | raises(typer.Exit) | complies: exit code asserted ['1961:ei.value.exit_code']
host/tests/test_cli.py:2051 | raises(typer.Exit) | complies: exit code asserted ['2054:ei.value.exit_code']
host/tests/test_cli.py:2087 | raises(typer.Exit) | exempt because the Exit is the scripted end of the stream (frames or polls run out); the test's subject is the output, asserted positively after it
host/tests/test_cli.py:2128 | raises(typer.Exit) | complies: exit code asserted ['2131:ei.value.exit_code']
host/tests/test_cli.py:2160 | raises(typer.Exit) | complies: exit code asserted ['2162:ei.value.exit_code']
host/tests/test_cli.py:2196 | raises(typer.Exit) | complies: exit code asserted ['2198:ei.value.exit_code']
host/tests/test_cli.py:2228 | raises(typer.Exit) | complies: exit code asserted ['2230:ei.value.exit_code']
host/tests/test_cli.py:2268 | raises(typer.Exit) | exempt because the Exit is the scripted end of the stream (frames or polls run out); the test's subject is the output, asserted positively after it
host/tests/test_cli.py:2368 | raises(TypeError) | complies: the only TypeError source is the patched cli.app double; the claim is that _dispatch does not catch it
host/tests/test_cli.py:2597 | raises(typer.Exit) | complies: exit code asserted ['2600:ei.value.exit_code']
host/tests/test_cli.py:2632 | raises(typer.Exit) | complies: exit code asserted ['2635:ei.value.exit_code']
host/tests/test_cli.py:2828 | raises(typer.Exit) | exempt because the Exit is the scripted end of the stream (frames or polls run out); the test's subject is the output, asserted positively after it
host/tests/test_cli.py:771 | raises(typer.Exit) | complies: exit code asserted ['773:ei.value.exit_code']
host/tests/test_cli.py:1520 | raises(BrokenPipeError) | complies: the snapshot double is the only BrokenPipeError source; subject (no orphaned recv) asserted after
host/tests/test_cli.py:1868 | raises(typer.Exit) | complies: exit code asserted ['1870:ei.value.exit_code']
host/tests/test_cli.py:2307 | raises(typer.Exit) | complies: exit code asserted ['2309:ei.value.exit_code', '2309:ei.value.exit_code']
host/tests/test_cli_daemon_stop_scope.py:148 | raises(typer.Exit) | complies: exit code asserted ['151:ei.value.exit_code']
host/tests/test_cli_decode_rejected_defs.py:53 | raises(typer.Exit) | exempt because the Exit is the scripted end of the stream (frames or polls run out); the test's subject is the output, asserted positively after it
host/tests/test_cli_follow.py:133 | raises(typer.Exit) | complies: exit code asserted ['135:ei.value.exit_code']
host/tests/test_cli_follow.py:328 | raises(cli.typer.Exit) | complies: exit code asserted ['332:exit_info.value.exit_code']
host/tests/test_cli_follow_frames.py:28 | raises(typer.Exit) | complies: exit code asserted ['31:ei.value.exit_code']
host/tests/test_cli_follow_frames.py:46 | raises(typer.Exit) | exempt because the Exit ends an empty scripted stream; subject is the quoted URL, asserted after
host/tests/test_cli_follow_frames.py:62 | raises(typer.Exit) | complies: exit code asserted ['64:ei.value.exit_code']
host/tests/test_cli_follow_frames.py:76 | raises(typer.Exit) | complies: exit code asserted ['78:ei.value.exit_code']
host/tests/test_cli_follow_frames.py:107 | raises(typer.Exit) | complies: exit code asserted ['109:ei.value.exit_code']
host/tests/test_cli_follow_frames.py:151 | raises(typer.Exit) | complies: exit code asserted ['153:ei.value.exit_code']
host/tests/test_cli_output_rows.py:25 | raises(typer.Exit) | complies: exit code asserted ['28:ei.value.exit_code']
host/tests/test_cli_ux.py:257 | raises(cli.typer.Exit) | complies: exit code asserted ['259:ex.value.exit_code']
host/tests/test_config_api.py:433 | raises(ConfigError) | complies: narrow project type
host/tests/test_config_api.py:439 | raises(ConfigError) match= | complies: narrow project type (and match=)
host/tests/test_config_api.py:639 | raises(PermissionError) | complies: the patched os.replace is the only raiser; the claim is 'the real error rather than a hang'
host/tests/test_config_api.py:540 | raises(ConfigError) | complies: narrow project type
host/tests/test_config_bools.py:63 | raises(ConfigError) match= | complies: narrow project type (and match=)
host/tests/test_config_loader.py:173 | raises(ConfigError) | complies: narrow project type
host/tests/test_config_loader.py:184 | raises(ConfigError) | complies: narrow project type
host/tests/test_config_loader.py:341 | raises(ConfigError) match= | complies: narrow project type (and match=)
host/tests/test_config_loader.py:268 | raises(ConfigError) match= | complies: narrow project type (and match=)
host/tests/test_config_loader.py:355 | raises(ConfigError) match= | complies: narrow project type (and match=)
host/tests/test_config_loader.py:343 | pytest.raises(ConfigError, ...) call form | complies: project type
host/tests/test_daemon_process.py:89 | raises(SystemExit) | complies: exit code asserted ['93:exc.value.code']
host/tests/test_daemon_process.py:161 | raises(subprocess.TimeoutExpired) | complies: TimeoutExpired from proc.wait is the assertion that the child is still alive
host/tests/test_daemon_startlog.py:41 | raises(SystemExit) | complies: exit code asserted ['44:exc.value.code']
host/tests/test_daemon_startlog.py:87 | raises(SystemExit) | exempt because the subject is the handler list restored, asserted after; the exit code of the same path is pinned at 41-44
host/tests/test_daemon_startlog.py:109 | raises(SystemExit) | complies: exit code asserted ['111:exc.value.code']
host/tests/test_daemon_startlog.py:59 | raises(SystemExit) | complies: exit code asserted ['63:exc.value.code']
host/tests/test_daemon_startup.py:75 | raises(RuntimeError) | VIOLATES (R28-3): any RuntimeError satisfies it, including one raised before the pid claim, where 'no pid record' is vacuous
host/tests/test_daemon_startup.py:258 | raises(SystemExit) | complies: exit code asserted ['260:exc.value.code']
host/tests/test_daemon_startup.py:248 | raises(_Spawned) | complies: narrow project type
host/tests/test_daemon_startup.py:369 | raises(SystemExit) | complies: exit code asserted ['371:exc.value.code']
host/tests/test_daemon_token_exposure.py:17 | raises(ConfigError) match= | complies: narrow project type (and match=)
host/tests/test_e2e.py:440 | raises(websockets.exceptions.ConnectionClosed) | complies: close code (rcvd) asserted at 443
host/tests/test_link.py:54 | raises(serial.SerialException) | complies: send_break on a closed SourceLink is the only SerialException source (Scripted raises only when polled); reasoned: without the closed check send_break returns True and the test fails
host/tests/test_plotjuggler.py:59 | raises(ValueError) | complies: parse_dest is pure and every ValueError it can raise is a refusal the server maps to 400; wording pinned per branch at 66-70
host/tests/test_plotjuggler.py:66 | raises(ValueError) match= | complies: match= present
host/tests/test_plotjuggler.py:68 | raises(ValueError) match= | complies: match= present
host/tests/test_plotjuggler.py:70 | raises(ValueError) match= | complies: match= present
host/tests/test_plotjuggler.py:213 | raises(ValueError) | complies: ValueError is the non-unicast refusal; without it 0.0.0.0/239.x connect and 255.255.255.255 raises PermissionError (not ValueError), so the test would fail
host/tests/test_plotjuggler.py:279 | raises(ConfigError) | complies: narrow project type
host/tests/test_plotjuggler.py:286 | raises(ConfigError) | complies: narrow project type
host/tests/test_plotjuggler.py:492 | raises(ConfigError) | complies: narrow project type
host/tests/test_plotjuggler.py:161 | raises(TimeoutError) | exempt because TimeoutError from recv is the absence assertion itself (class 78 territory, not a refusal)
host/tests/test_plotjuggler.py:179 | raises(TimeoutError) | exempt because as 161
host/tests/test_plotjuggler.py:191 | raises(ValueError) | complies: configure's only ValueError source for this input is parse_dest's refusal; state kept is asserted after
host/tests/test_plotjuggler.py:193 | raises(ValueError) | complies: without the unicast check connect() to 239.x succeeds, so only the refusal raises
host/tests/test_plotjuggler.py:198 | raises(OSError) | complies: the patched getaddrinfo is the only raiser
host/tests/test_port_health.py:32 | raises(PortError) match= | complies: narrow project type (and match=)
host/tests/test_port_health.py:34 | raises(PortError) match= | complies: narrow project type (and match=)
host/tests/test_port_health.py:50 | raises(PortError) match= | complies: narrow project type (and match=)
host/tests/test_port_health.py:577 | raises(PortError) match= | complies: narrow project type (and match=)
host/tests/test_protocol.py:97 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:99 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:106 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:108 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:133 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:135 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:147 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:149 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:151 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:166 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:210 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:311 | raises(p.ProtocolError) match= | complies: narrow project type (and match=)
host/tests/test_protocol.py:320 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:340 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:342 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:344 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:670 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:672 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:674 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:827 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:835 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:837 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:1086 | raises(p.ProtocolError) match= | complies: narrow project type (and match=)
host/tests/test_protocol.py:1088 | raises(p.ProtocolError) match= | complies: narrow project type (and match=)
host/tests/test_protocol.py:1090 | raises(p.ProtocolError) match= | complies: narrow project type (and match=)
host/tests/test_protocol.py:1096 | raises(p.ProtocolError) match= | complies: narrow project type (and match=)
host/tests/test_protocol.py:1104 | raises(p.ProtocolError) match= | complies: narrow project type (and match=)
host/tests/test_protocol.py:185 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:689 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:843 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:846 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol.py:1084 | raises(p.ProtocolError) match= | complies: narrow project type (and match=)
host/tests/test_protocol_strict.py:24 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol_strict.py:36 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol_strict.py:38 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol_strict.py:48 | raises(PortError) | complies: narrow project type
host/tests/test_protocol_strict.py:50 | raises(PortError) | complies: narrow project type
host/tests/test_protocol_strict.py:52 | raises(PortError) | complies: narrow project type
host/tests/test_protocol_strict.py:67 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol_strict.py:69 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol_strict.py:103 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol_strict.py:105 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol_strict.py:122 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol_strict.py:130 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol_strict.py:132 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_protocol_tokenizer.py:32 | raises(p.ProtocolError) match= | complies: narrow project type (and match=)
host/tests/test_protocol_tokenizer.py:49 | raises(p.ProtocolError) match= | complies: narrow project type (and match=)
host/tests/test_reconnect.py:684 | raises(PortError) | complies: narrow project type
host/tests/test_reconnect.py:946 | raises(RuntimeError) | complies: the _LiveButRefusing double is the only raiser; the claim is that _post does not swallow it
host/tests/test_reconnect.py:1148 | raises(PortError) match= | complies: narrow project type (and match=)
host/tests/test_reconnect.py:1239 | raises(serial.SerialException) | complies: SimEndpoint hands a non-sim device to open_link, and the unopenable name is the only raiser; links count asserted after
host/tests/test_reconnect.py:1131 | raises(PortError) | complies: narrow project type
host/tests/test_reconnect.py:1350 | raises((asyncio.CancelledError, PortError)) | complies: as 1312; the type is a Python-version detail by design (comment 1343-1349)
host/tests/test_reconnect.py:1395 | raises(PortError) match= | complies: narrow project type (and match=)
host/tests/test_reconnect.py:486 | raises(RuntimeError) | complies: attach raises only PortError for its own refusals, so RuntimeError can come only from the prime_plot_defs double
host/tests/test_reconnect.py:1217 | raises(PortError) | complies: narrow project type
host/tests/test_reconnect.py:1312 | raises((PortError, RuntimeError)) | complies: the exception type is not the subject (future consumed, pending empty, no unretrieved report), all asserted after
host/tests/test_security.py:36 | raises(ValueError) match= | complies: match= present
host/tests/test_security.py:47 | raises(PortError) | complies: narrow project type
host/tests/test_security.py:49 | raises(PortError) | complies: narrow project type
host/tests/test_security.py:51 | raises(PortError) | complies: narrow project type
host/tests/test_security.py:241 | raises(InvalidStatus) | complies: response status asserted at 244
host/tests/test_security.py:42 | raises(PortError) | complies: narrow project type
host/tests/test_serial_link_attach.py:121 | raises(PortError) match= | complies: narrow project type (and match=)
host/tests/test_serial_link_attach.py:138 | raises(PortError) match= | complies: narrow project type (and match=)
host/tests/test_serial_link_attach.py:56 | raises(PortError) match= | complies: narrow project type (and match=)
host/tests/test_serial_link_attach.py:230 | raises(PortError) match= | complies: narrow project type (and match=)
host/tests/test_serial_link_attach.py:389 | raises(serial_link.PortError) | complies: narrow project type
host/tests/test_serial_link_attach.py:514 | raises(PortError) match= | complies: narrow project type (and match=)
host/tests/test_serial_link_tx.py:127 | raises(RuntimeError) match= | complies: match= present
host/tests/test_serial_link_tx.py:145 | raises(PortError) match= | complies: narrow project type (and match=)
host/tests/test_serial_link_tx.py:166 | raises(StoreError) | complies: narrow project type
host/tests/test_serial_link_tx.py:310 | raises(asyncio.CancelledError) | complies: CancelledError after task.cancel() is the expected outcome; pending table asserted after
host/tests/test_serial_link_tx.py:368 | raises(asyncio.CancelledError) | complies: as 310
host/tests/test_server_exports.py:323 | raises(server_mod._ExportAbandoned) | complies: narrow project type
host/tests/test_server_exports.py:563 | raises(ConnectionResetError) | complies: the send double is the only ConnectionResetError source; temp copy removal asserted after
host/tests/test_server_exports.py:312 | raises(sqlite3.OperationalError) match= | complies: match= present
host/tests/test_server_guards.py:88 | raises(WebSocketDisconnect) | complies: the control connection at 90 differs only in the Sec-Fetch headers, which only the same-origin guard reads; asserting code == 1008 (as 236 does) would make it self-describing
host/tests/test_server_guards.py:175 | raises(websockets.exceptions.InvalidStatus) | complies: response asserted at 180
host/tests/test_server_guards.py:233 | raises(WebSocketDisconnect) | complies: code 1008 asserted at 236
host/tests/test_server_scope.py:185 | raises(websockets.exceptions.ConnectionClosed) | complies: close code asserted at 188
host/tests/test_server_shutdown.py:104 | raises(WebSocketDisconnect) | complies: code and reason asserted at 106-107
host/tests/test_server_shutdown.py:123 | raises(WebSocketDisconnect) | complies: code asserted at 125
host/tests/test_server_shutdown.py:80 | raises(StoreError) match= | complies: narrow project type (and match=)
host/tests/test_server_ws.py:101 | raises(websockets.exceptions.ConnectionClosed) | complies: any close is the expected outcome; a hang fails via wait_for's TimeoutError, which is not ConnectionClosed
host/tests/test_sessions.py:780 | raises(sqlite3.OperationalError) | complies: an unrelated OperationalError in the rebuild also breaks the Store.start at 792 (driven: mutant 'SELEC broken' fails the test)
host/tests/test_sim.py:797 | raises(SystemExit) | complies: exit code asserted ['799:exc.value.code']
host/tests/test_sim.py:859 | raises(p.ProtocolError) | complies: narrow project type
host/tests/test_sim_flags.py:19 | raises(SystemExit) | complies: exit code asserted ['21:exc.value.code']
host/tests/test_sim_flags.py:32 | raises(SystemExit) | complies: exit code asserted ['34:exc.value.code']
host/tests/test_sim_flags.py:41 | raises(SystemExit) | complies: exit code asserted ['43:exc.value.code']
host/tests/test_sim_tcp.py:103 | raises(OSError) | complies: refused connect is the claim; a still-bound listener would accept from the backlog and the test would fail
host/tests/test_sim_tcp.py:150 | raises(OSError) | complies: errno asserted at 154
host/tests/test_stdio.py:103 | raises(ValueError) | complies: the boom double is the only raiser; crash log contents asserted after
host/tests/test_stdio.py:118 | raises(KeyboardInterrupt) | complies: the double is the only raiser; no crash log asserted after
host/tests/test_stdio.py:213 | raises(ValueError) | complies: as 184
host/tests/test_stdio.py:184 | raises(ValueError) | complies: _explode is the only raiser; per-port files asserted after
host/tests/test_store_export.py:68 | raises(sqlite3.Error) match= | complies: match= present
host/tests/test_store_fastpaths.py:242 | raises(MatchBudgetExceeded) | complies: narrow project type
host/tests/test_store_fastpaths.py:299 | raises(StoreError) match= | complies: narrow project type (and match=)
host/tests/test_store_fastpaths.py:144 | raises(StoreError) match= | complies: narrow project type (and match=)
host/tests/test_store_fastpaths.py:251 | raises(sqlite3.ProgrammingError) | complies: read conns are check_same_thread=False, so ProgrammingError means closed (driven: mutant that never closes fails the test)
host/tests/test_store_fastpaths.py:284 | raises(asyncio.QueueFull) | complies: QueueFull is raised only by submit_line_nowait's own put_nowait
host/tests/test_store_id_sequence.py:57 | raises(StoreError) match= | complies: narrow project type (and match=)
host/tests/test_store_id_sequence.py:83 | raises(sqlite3.IntegrityError) | complies: the CHECK-violating row is the only bad row; id placement asserted after
host/tests/test_store_id_sequence.py:104 | raises(sqlite3.IntegrityError) | complies: as 83
host/tests/test_store_match_budget.py:44 | raises(MatchBudgetExceeded) | complies: narrow project type
host/tests/test_store_match_budget.py:55 | raises(MatchBudgetExceeded) match= | complies: narrow project type (and match=)
host/tests/test_store_match_budget.py:60 | raises(MatchBudgetExceeded) | complies: narrow project type
host/tests/test_store_match_budget.py:178 | raises(MatchBudgetExceeded) | complies: narrow project type
host/tests/test_store_match_budget.py:183 | raises(MatchBudgetExceeded) | complies: narrow project type
host/tests/test_store_match_budget.py:72 | raises(sqlite3.OperationalError) match= | complies: match= present
host/tests/test_store_plot_summary.py:317 | raises(OSError) match= | complies: match= present
host/tests/test_store_schema.py:207 | raises(sqlite3.OperationalError) match= | complies: match= present
host/tests/test_store_stamp_order.py:123 | raises(StoreError) match= | complies: narrow project type (and match=)
host/tests/test_store_subscribers.py:35 | raises(StoreError) | complies: narrow project type
host/tests/test_store_writer.py:308 | raises(StoreError) | complies: narrow project type
host/tests/test_store_writer.py:334 | raises(StoreError) | complies: narrow project type
host/tests/test_store_writer.py:41 | raises(StoreError) match= | complies: narrow project type (and match=)
host/tests/test_store_writer.py:65 | raises(StoreError) match= | complies: narrow project type (and match=)
host/tests/test_store_writer.py:86 | raises(StoreError) | complies: narrow project type
host/tests/test_store_writer.py:102 | raises(sqlite3.IntegrityError) | complies: chan CHECK violation is the only integrity error available; writer survival asserted after
host/tests/test_store_writer.py:122 | raises(sqlite3.IntegrityError) | complies: NOT NULL can_id is the only integrity error; rollback (max_id 0) asserted after
host/tests/test_store_writer.py:152 | raises(sqlite3.IntegrityError) | complies: as 102; neighbours asserted after
host/tests/test_store_writer.py:365 | raises(asyncio.CancelledError) | complies: CancelledError after cancel() is the expected outcome
host/tests/test_store_writer.py:370 | raises(StoreError) match= | complies: narrow project type (and match=)
host/tests/test_store_writer_commits.py:150 | raises(StoreError) match= | complies: narrow project type (and match=)
host/tests/test_timeline.py:315 | raises(typer.BadParameter) | complies: typer.BadParameter is raised only by parse_clock's refusals; a unit test of the parser, the exit mapping is _dispatch's
host/tests/test_timeline.py:328 | raises(typer.BadParameter) | complies: as 315

### Sweep D (improved): `contextlib.suppress` in tests: 21 sites

The registry sweep does not name `suppress`, which is an `except: pass` by another spelling. Command: `grep -n "suppress(" host/tests/*.py`.

- host/tests/test_cli.py:1927 | suppress(Exception) around httpd.server_close() in finally | exempt because teardown
- host/tests/test_store_writer_commits.py:146 | CancelledError after cancel | complies
- host/tests/test_protocol.py:944 | suppress(ProtocolError) in the fuzz: the documented refusal type, anything else reaches the `except Exception -> AssertionError` | complies
- host/tests/test_protocol.py:946 | as 944 | complies
- host/tests/test_server_export_pool.py:198 | CancelledError after cancel | complies
- host/tests/test_server_export_pool.py:255 | as 198 | complies
- host/tests/test_serial_link_attach.py:316 | as 198 | complies
- host/tests/test_port_health.py:300 | suppress(CancelledError, Exception) awaiting a cancelled background task with no store | exempt because cleanup of a task the test cancelled, not the act under test
- host/tests/test_port_health.py:313 | CancelledError | complies
- host/tests/test_server_exports.py:138 | CancelledError | complies
- host/tests/test_server_exports.py:262 | CancelledError | complies
- host/tests/test_server_exports.py:349 | CancelledError | complies
- host/tests/test_sim_pty.py:176 | OSError in a drain thread reading until the fd closes | exempt because harness reader
- host/tests/test_serial_link_tx.py:209 | suppress(PortError) in `_write_quietly`, the failing writes are the stimulus and the counts are asserted | complies (narrow type)
- host/tests/test_serial_link_tx.py:412 | CancelledError | complies
- host/tests/test_serial_link_tx.py:482 | CancelledError | complies
- host/tests/test_store_writer.py:300 | CancelledError | complies
- host/tests/test_store_writer.py:320 | docstring text, not code | exempt because not a call
- host/tests/test_store_writer.py:330 | CancelledError | complies
- host/tests/test_store_writer.py:341 | suppress(Exception) around `wait_for(store.stop_session(), 5.0)` | VIOLATES (R28-1)
- host/tests/test_store_writer.py:343 | suppress(Exception) around `wait_for(store.add_line(...daemon stop), 5.0)` | VIOLATES (R28-1, same test; covered in practice by the pytest.raises(StoreError) on add_line at 332, which a hang fails)

### Sweep E (improved): an assertion inside a try or suppress that can catch AssertionError: 12 sites

Command: `scratch-27-28/c28_swallow.py` (AST: every try whose handler catches bare/BaseException/Exception/AssertionError, and every suppress naming one, listing assert / raise AssertionError / pytest.fail in the guarded body, nested defs excluded).
All 12 guarded bodies hold no assertion: test_cli.py:1927, test_cli.py:1979, test_daemon_process.py:214, test_daemon_startup.py:426, test_pane_regex_dialect.py:27, test_port_health.py:300, test_protocol.py:928, test_scaffold.py:119, test_server_exports.py:223, test_sim_pty.py:158, test_store_writer.py:341, test_store_writer.py:343.
Each is ruled under sweep B or D above; only the last three and test_pane_regex_dialect.py:27 violate, for the reasons given there.

### Sweep F (improved): assertions in a function that something other than the test calls: 149 sites

The indirect form of the class: an `assert` inside a double, a thread target or a handler runs where production code, a worker thread or the thread excepthook may swallow it.
Command: `scratch-27-28/c28_nested.py` (AST: asserts inside nested defs and test-file class methods, with how the enclosing function uses the name).

- 131 sites are coroutines or functions run via `asyncio.run(run())`, `run_coro(run)`, or called directly on the test thread (listed in `scratch-27-28/c28-nested.txt` as `called-directly` or `arg-of:run_coro`): complies, asyncio.run and a direct call re-raise.
- host/tests/test_cli.py:677, :697 (both listed twice: method and nested) | doubles called by cli.py, which has no arm catching AssertionError | complies
- host/tests/test_cli.py:1467 | websockets.connect double | complies (same reason)
- host/tests/test_cli.py:2402 | MockTransport handler | complies (driven, sweep A)
- host/tests/test_cli_read_scope.py:111 | MockTransport handler asserting the request | complies (httpx MockTransport does not wrap handler exceptions)
- host/tests/test_cli_ux.py:232 | Popen `__init__` double | complies (cli's Popen call catches OSError only)
- host/tests/test_cli_ux.py:72 | `_FakeDaemon.__init__` | complies (same)
- host/tests/test_config_loader.py:398 | patched daemon._serve | complies (daemon.py has no `except Exception`/BaseException arm)
- host/tests/test_daemon_console.py:31 | fixture's run() called by each test | complies
- host/tests/test_port_column_stored.py:136 | reader Thread target; its only assert is `proc.stdout is not None` and the outcome is asserted on the main thread | complies
- host/tests/test_port_health.py:112 | MockTransport handler | complies
- host/tests/test_port_health.py:342 | helper awaited by the test | complies
- host/tests/test_reconnect.py:910 | opener run on the reader thread; a failure there means no link, and `assert opened` on the main thread catches it | complies
- host/tests/test_reconnect.py:1300 | `_write_bytes` double run in the write pool; send_command re-raises BaseException, and AssertionError does not match the expected (PortError, RuntimeError) | complies
- host/tests/test_server_export_pool.py:38, :293; host/tests/test_server_exports.py:59, :66 | build doubles' `assert <event>.wait(20)` | exempt because they guard the test's own release protocol (a test bug), not the product
- host/tests/test_server_guards.py:356 | inner_app double | complies (sweep A)
- host/tests/test_store_fastpaths.py:218 | Thread target `worker` asserting at 231 and 232 | VIOLATES (R28-4, LOW)


Full sweep F site list (149, from c28_nested.py; `called-directly` / `arg-of:run_coro` sites comply as stated above, the others are ruled individually above):

- host/tests/test_assert.py:476 nested test_watch_ignores_rows_committed_before_it_opened.run asserts@[486] used=['called-directly']
- host/tests/test_assert.py:506 nested test_watch_drains_a_queued_burst_after_the_deadline_has_passed.run asserts@[515] used=['called-directly']
- host/tests/test_assert.py:533 nested test_watch_separates_an_empty_window_from_a_filtered_one.run asserts@[540, 542] used=['called-directly']
- host/tests/test_assert.py:557 nested test_watch_counts_the_rows_the_feed_shed.run asserts@[567, 568] used=['called-directly']
- host/tests/test_assert.py:583 nested test_watch_close_releases_the_subscription.run asserts@[590, 592, 594] used=['called-directly']
- host/tests/test_assert.py:615 nested test_sweep_tick_writes_the_trim_into_the_capture.run asserts@[622, 624] used=['called-directly']
- host/tests/test_assert.py:637 nested test_sweep_tick_survives_a_failing_sweep.run asserts@[645] used=['called-directly']
- host/tests/test_assert.py:657 nested test_sweep_tick_runs_the_age_sweep_only_when_the_hour_divides.run asserts@[669, 671] used=['called-directly']
- host/tests/test_cli.py:1467 nested test_tail_without_follow_makes_one_rest_fetch.boom asserts@[1468] used=['arg-of:monkeypatch.setattr']
- host/tests/test_cli.py:2402 nested test_can_tx_rejects_an_impossible_rtr.refuse asserts@[2403] used=['arg-of:run_mcu_canned']
- host/tests/test_cli.py:677 method _Resp.text asserts@[678]
- host/tests/test_cli.py:677 nested test_plot_export_streams_the_response.text asserts@[678] used=['unused-by-name']
- host/tests/test_cli.py:697 method _FakeHttp.request asserts@[698]
- host/tests/test_cli.py:697 nested test_plot_export_streams_the_response.request asserts@[698] used=['unused-by-name']
- host/tests/test_cli_read_scope.py:111 nested test_since_id_returns_the_next_rows_above_the_id_across_pages.handler asserts@[114] used=['arg-of:run_mcu_canned']
- host/tests/test_cli_ux.py:232 nested test_an_unwritable_stderr_log_falls_back_to_devnull_with_a_warning.init_accepting_devnull asserts@[233] used=['arg-of:monkeypatch.setattr']
- host/tests/test_cli_ux.py:72 method _FakeDaemon.__init__ asserts@[77]
- host/tests/test_config_loader.py:398 nested test_warnings_are_logged_once_at_startup_and_served_on_status.fake_serve asserts@[402, 404] used=['arg-of:monkeypatch.setattr']
- host/tests/test_daemon_console.py:31 nested run_daemon.run asserts@[32] used=['unused-by-name']
- host/tests/test_port_column_stored.py:136 nested test_a_detached_boards_history_carries_the_port_in_every_cli_text_read.read asserts@[137] used=['arg-of:threading.Thread(target=)']
- host/tests/test_port_health.py:112 nested _busy_then_ok.handler asserts@[113] used=['unused-by-name']
- host/tests/test_port_health.py:342 nested _reason_port.wait_reason asserts@[350] used=['unused-by-name']
- host/tests/test_reconnect.py:1209 nested test_the_attach_cap_refuses_the_port_past_it.run asserts@[1219] used=['called-directly']
- host/tests/test_reconnect.py:1300 nested test_a_disconnect_during_a_command_leaves_no_unretrieved_future.write asserts@[1305] used=['unused-by-name']
- host/tests/test_reconnect.py:212 nested test_repeated_open_failures_record_one_row.run asserts@[221, 228] used=['called-directly']
- host/tests/test_reconnect.py:236 nested test_distinct_reasons_still_reported_but_bounded.run asserts@[248] used=['called-directly']
- host/tests/test_reconnect.py:256 nested test_each_episode_reports_its_reason_again.run asserts@[268, 269, 271] used=['called-directly']
- host/tests/test_reconnect.py:302 nested test_one_raising_line_does_not_abandon_the_burst.run asserts@[316, 318, 319] used=['called-directly']
- host/tests/test_reconnect.py:332 nested test_an_overlong_seq_token_costs_no_lines_at_all.run asserts@[339, 340] used=['called-directly']
- host/tests/test_reconnect.py:351 nested test_an_oversized_terminated_line_is_dropped_and_counted.run asserts@[358, 359, 361] used=['called-directly']
- host/tests/test_reconnect.py:375 nested test_reader_survives_a_failing_device_lookup.run asserts@[386, 388] used=['called-directly']
- host/tests/test_reconnect.py:399 nested test_reattach_continues_the_command_seq.run asserts@[408] used=['called-directly']
- host/tests/test_reconnect.py:423 nested test_carried_counters_follow_the_alias_not_the_device.run asserts@[433, 434] used=['called-directly']
- host/tests/test_reconnect.py:447 nested test_carried_counters_are_bounded_and_evict_the_oldest.run asserts@[457, 458, 459, 463] used=['called-directly']
- host/tests/test_reconnect.py:475 nested test_a_failed_reattach_leaves_the_running_port_alone.run asserts@[488] used=['called-directly']
- host/tests/test_reconnect.py:512 nested test_reader_join_does_not_queue_behind_the_default_executor.run asserts@[524] used=['called-directly']
- host/tests/test_reconnect.py:910 nested test_a_link_opened_after_the_join_deadline_is_closed_not_leaked.slow_opener asserts@[911] used=['arg-of:SerialPort(open_link_fn=)']
- host/tests/test_serial_link_attach.py:287 nested test_detach_and_reattach_carries_the_tx_counter.run asserts@[296] used=['called-directly']
- host/tests/test_serial_link_attach.py:465 nested test_detach_handle_close_does_not_queue_behind_the_default_executor.run asserts@[477, 489] used=['called-directly']
- host/tests/test_serial_link_attach.py:509 nested test_attach_against_a_stopped_store_is_a_port_error.run asserts@[516] used=['called-directly']
- host/tests/test_serial_link_attach.py:528 nested test_sys_row_on_a_stopped_store_is_not_an_orphaned_task.run asserts@[539, 547, 550] used=['called-directly']
- host/tests/test_serial_link_devices.py:164 nested test_serial_number_port_reports_the_device_it_opened.run asserts@[174, 181, 182, 183] used=['called-directly']
- host/tests/test_serial_link_rx_framing.py:225 nested test_rx_queue_overflow_drops_oldest.run asserts@[233, 234, 238, 239] used=['called-directly']
- host/tests/test_serial_link_tx.py:136 nested test_disconnect_fails_pending_promptly.run asserts@[147] used=['called-directly']
- host/tests/test_serial_link_tx.py:160 nested test_failed_write_is_counted.run asserts@[164, 168, 170] used=['called-directly']
- host/tests/test_serial_link_tx.py:281 nested test_cancelling_a_command_does_not_leak_its_pending_entry.run asserts@[301, 308, 312] used=['called-directly']
- host/tests/test_serial_link_tx.py:334 nested test_cancelling_a_command_mid_write_does_not_leak_its_pending_entry.run asserts@[355, 366, 370] used=['called-directly']
- host/tests/test_server_export_pool.py:188 nested test_a_queued_build_whose_handler_is_cancelled_never_starts.cancel_queued asserts@[196] used=['called-directly']
- host/tests/test_server_export_pool.py:249 nested test_a_bundle_cancelled_after_its_copy_logs_no_failure.cancel_mid_member asserts@[253] used=['called-directly']
- host/tests/test_server_export_pool.py:293 method _GatedBuild.__call__ asserts@[296]
- host/tests/test_server_export_pool.py:38 method _Build.__call__ asserts@[41]
- host/tests/test_server_exports.py:256 nested test_a_cancelled_export_interrupts_its_copy.cancel_mid_copy asserts@[260] used=['called-directly']
- host/tests/test_server_exports.py:343 nested test_a_cancelled_bundle_stops_while_writing_a_member.cancel_mid_member asserts@[347] used=['called-directly']
- host/tests/test_server_exports.py:59 method _BlockedBuild.__call__ asserts@[63]
- host/tests/test_server_exports.py:66 method _BlockedBuild.wait_entered asserts@[68]
- host/tests/test_server_export_windows.py:306 nested test_export_bound_by_id_to_reanchors_its_last_ms_window.run asserts@[324, 329, 330, 331, 334, 335, 339, 341, 357, 359, 360] used=['called-directly']
- host/tests/test_server_guards.py:356 nested test_token_guard_handles_non_ascii_credentials.inner_app asserts@[357] used=['arg-of:_TokenGuard']
- host/tests/test_server_guards.py:375 nested test_token_guard_handles_non_ascii_credentials.run asserts@[376, 377] used=['called-directly']
- host/tests/test_server_shutdown.py:237 nested test_the_row_the_sentinel_sheds_is_counted_as_dropped.run asserts@[243, 246, 247, 248] used=['called-directly']
- host/tests/test_server_shutdown.py:54 nested test_the_sentinel_survives_rows_committed_after_it.run asserts@[63, 64] used=['called-directly']
- host/tests/test_server_shutdown.py:72 nested test_a_subscriber_after_the_sentinel_is_refused_as_shutdown.run asserts@[77, 79] used=['called-directly']
- host/tests/test_sessions.py:137 nested test_a_queued_backlog_stays_outside_the_session_it_precedes.run asserts@[150, 151, 156, 158] used=['called-directly']
- host/tests/test_sessions.py:166 nested test_stop_without_a_session_is_a_noop.run asserts@[169, 170] used=['called-directly']
- host/tests/test_sessions.py:178 nested test_resolve_by_name_takes_the_newest.run asserts@[184, 185, 186] used=['called-directly']
- host/tests/test_sessions.py:196 nested test_line_count_reflects_retention.run asserts@[202, 212] used=['called-directly']
- host/tests/test_sessions.py:38 nested test_session_bounds_what_it_contains.run asserts@[47, 48, 53, 54, 56, 57, 58] used=['called-directly']
- host/tests/test_sessions.py:431 nested test_empty_automatic_session_is_dropped_on_close.check asserts@[435] used=['called-directly']
- host/tests/test_sessions.py:447 nested test_automatic_session_with_traffic_is_kept.run asserts@[457] used=['called-directly']
- host/tests/test_sessions.py:467 nested test_automatic_session_with_only_a_firmware_marker_is_kept.run asserts@[477] used=['called-directly']
- host/tests/test_sessions.py:487 nested test_automatic_sessions_carry_the_retention_floor.run asserts@[501, 502] used=['called-directly']
- host/tests/test_sessions.py:529 nested test_capture_predating_the_auto_column_is_migrated.run asserts@[534, 535, 536] used=['called-directly']
- host/tests/test_sessions.py:561 nested test_min_sessions_floor_survives_age_expiry.run asserts@[574, 575, 576] used=['called-directly']
- host/tests/test_sessions.py:584 nested test_all_sessions_protected_when_fewer_than_the_floor.run asserts@[597] used=['called-directly']
- host/tests/test_sessions.py:605 nested test_min_sessions_zero_is_pure_age_retention.run asserts@[615] used=['called-directly']
- host/tests/test_sessions.py:625 nested test_lines_outside_any_session_are_not_protected.run asserts@[638, 639] used=['called-directly']
- host/tests/test_sessions.py:652 nested test_session_ids_are_never_reused_after_a_delete.run asserts@[657, 659, 660] used=['called-directly']
- host/tests/test_sessions.py:66 nested test_starting_a_session_closes_the_previous_one.run asserts@[75, 76, 77, 82] used=['called-directly']
- host/tests/test_sessions.py:680 nested test_session_ids_stay_unique_across_a_restart.second asserts@[684] used=['called-directly']
- host/tests/test_sessions.py:709 nested test_a_capture_predating_autoincrement_is_migrated.run asserts@[714, 715, 719, 723, 725] used=['called-directly']
- host/tests/test_sessions.py:791 nested test_an_interrupted_sessions_rebuild_keeps_every_row.run asserts@[798] used=['called-directly']
- host/tests/test_sessions.py:834 nested test_automatic_session_with_only_the_connect_ping_is_dropped.run asserts@[843, 853] used=['called-directly']
- host/tests/test_sessions.py:98 nested test_two_concurrent_starts_leave_one_open_session.run asserts@[111, 113, 118, 120] used=['called-directly']
- host/tests/test_store_can_frames_port.py:49 nested test_can_frames_filters_each_select_what_they_name.run asserts@[70, 71, 72, 73, 74, 75, 76, 77, 78, 80, 81, 84, 86] used=['called-directly']
- host/tests/test_store_export.py:25 nested test_an_in_memory_capture_can_still_be_exported.run asserts@[40, 41] used=['called-directly']
- host/tests/test_store_fastpaths.py:218 nested test_cached_read_conn_survives_a_query_error_and_is_closed_at_stop.worker asserts@[231, 232] used=['arg-of:threading.Thread(target=)']
- host/tests/test_store_id_sequence.py:117 nested test_id_sequence_continues_across_restart.run asserts@[129, 131] used=['called-directly']
- host/tests/test_store_id_sequence.py:146 nested test_line_ids_are_not_reused_after_the_table_empties.first asserts@[156] used=['called-directly']
- host/tests/test_store_lines_plan.py:200 nested test_can_id_list_keeps_driving_from_the_frame_table.run asserts@[214, 215] used=['arg-of:run_coro']
- host/tests/test_store_lines_plan.py:226 nested test_a_can_id_list_selects_every_id_in_it.run asserts@[233] used=['arg-of:run_coro']
- host/tests/test_store_lines_plan.py:246 nested test_the_plan_words_the_negative_assertions_rely_on.run asserts@[253, 254] used=['called-directly']
- host/tests/test_store_lines_plan.py:269 nested test_can_frames_always_drives_from_the_frame_table.run asserts@[288, 289] used=['called-directly']
- host/tests/test_store_lines_plan.py:311 nested test_plot_channels_port_filter_does_not_scan_lines.run asserts@[323, 324, 326, 327] used=['called-directly']
- host/tests/test_store_lines_plan.py:347 nested test_lines_port_filter_seeks_rather_than_scans.run asserts@[356, 361, 363, 373, 387] used=['called-directly']
- host/tests/test_store_lines_plan.py:405 nested test_a_last_ms_window_seeks_by_id_rather_than_reading_the_table.run asserts@[414, 426, 433, 436] used=['called-directly']
- host/tests/test_store_lines_plan.py:418 nested run.plan_and_rows asserts@[422] used=['called-directly']
- host/tests/test_store_lines_plan.py:418 nested test_a_last_ms_window_seeks_by_id_rather_than_reading_the_table.plan_and_rows asserts@[422] used=['called-directly']
- host/tests/test_store_lines_plan.py:453 nested test_since_ts_seeks_by_id_rather_than_scanning_the_table.run asserts@[463, 493, 496, 507, 511] used=['called-directly']
- host/tests/test_store_lines_plan.py:524 nested test_since_ts_keeps_its_strictly_greater_boundary.run asserts@[540, 541, 543, 545] used=['called-directly']
- host/tests/test_store_lines_plan.py:561 nested test_the_age_sweep_does_not_read_the_table_when_nothing_has_expired.run asserts@[569, 581, 583, 592, 594] used=['called-directly']
- host/tests/test_store_plot_reads.py:159 nested test_plot_series_can_be_scoped_to_one_port.run asserts@[172, 173, 174, 179, 180, 182] used=['called-directly']
- host/tests/test_store_plot_summary.py:187 nested test_plot_ports_rebuilds_after_a_delete_on_its_own.run asserts@[197, 199] used=['called-directly']
- host/tests/test_store_plot_summary.py:278 nested test_a_summary_read_during_a_rebuild_waits_for_it.run asserts@[291, 293] used=['called-directly']
- host/tests/test_store_plot_summary.py:304 nested test_a_failed_rebuild_scan_leaves_the_summary_dirty.run asserts@[321] used=['called-directly']
- host/tests/test_store_reclaim_budget.py:120 nested test_one_tick_reclaims_at_most_the_bound.run asserts@[126, 129, 130] used=['arg-of:run_coro']
- host/tests/test_store_reclaim_budget.py:142 nested test_the_age_sweep_path_reclaims_too.run asserts@[152, 156] used=['arg-of:run_coro']
- host/tests/test_store_reclaim_budget.py:175 nested test_the_page_reclaim_stays_bounded_per_call.run asserts@[194, 198, 202] used=['called-directly']
- host/tests/test_store_reclaim_budget.py:90 nested test_successive_ticks_hand_the_freelist_back_with_nothing_left_to_trim.run asserts@[96, 100, 102, 106, 107] used=['arg-of:run_coro']
- host/tests/test_store_schema.py:136 nested test_a_capture_predating_the_bus_column_is_migrated.run asserts@[141, 149, 151] used=['called-directly']
- host/tests/test_store_schema.py:175 nested test_the_autoincrement_rebuild_keeps_every_session_index.run asserts@[179, 180] used=['called-directly']
- host/tests/test_store_schema.py:202 nested test_the_loop_connection_carries_no_regexp_function.run asserts@[211] used=['called-directly']
- host/tests/test_store_schema.py:229 nested test_the_writer_connection_gets_a_page_cache.run asserts@[233, 235] used=['arg-of:run_coro']
- host/tests/test_store_schema.py:322 nested test_a_capture_predating_the_meta_table_is_given_an_identity.run asserts@[336] used=['called-directly']
- host/tests/test_store_schema.py:348 nested test_deleting_the_highest_id_mints_a_new_capture.run asserts@[359, 360, 362, 363, 373] used=['called-directly']
- host/tests/test_store_sessions.py:115 nested test_active_session_does_not_read_every_session_when_none_is_running.run asserts@[123, 124, 129, 131, 136, 138] used=['called-directly']
- host/tests/test_store_sessions.py:18 nested test_session_line_count_is_bounded_at_both_ends.run asserts@[24, 33] used=['called-directly']
- host/tests/test_store_sessions.py:48 nested test_delete_session_does_not_fall_back_to_a_name_match.run asserts@[53, 55, 56] used=['called-directly']
- host/tests/test_store_sessions.py:72 nested test_session_ref_is_an_ascii_decimal_or_a_name.run asserts@[82, 83, 84, 86, 87] used=['called-directly']
- host/tests/test_store_size_cap.py:104 nested test_size_cap_spends_unprotected_lines_first_and_forces_only_the_remainder.run asserts@[112, 117, 120, 125, 126, 130] used=['called-directly']
- host/tests/test_store_size_cap.py:149 nested test_a_purge_running_beside_the_size_sweep_is_not_paid_for_twice.run asserts@[161, 162, 165] used=['called-directly']
- host/tests/test_store_size_cap.py:178 nested test_size_cap_off_by_default_never_trims.run asserts@[183, 184, 186] used=['called-directly']
- host/tests/test_store_size_cap.py:196 nested test_db_size_counts_the_wal.run asserts@[203, 204] used=['called-directly']
- host/tests/test_store_size_cap.py:220 nested test_size_trim_actually_returns_pages_to_the_filesystem.run asserts@[225, 239] used=['called-directly']
- host/tests/test_store_size_cap.py:31 nested test_size_cap_trims_oldest_and_converges.run asserts@[39, 40, 41, 44, 46, 50, 51] used=['called-directly']
- host/tests/test_store_size_cap.py:63 nested test_size_cap_trims_into_protected_sessions_rather_than_being_ignored.run asserts@[71, 77, 80, 81, 84, 86] used=['called-directly']
- host/tests/test_store_subscribers.py:17 nested test_stop_wakes_every_subscriber_with_a_sentinel.run asserts@[24, 25] used=['arg-of:run_coro']
- host/tests/test_store_subscribers.py:54 nested test_a_slow_subscriber_is_told_it_missed_rows.run asserts@[61, 63, 64, 66, 69] used=['called-directly']
- host/tests/test_store_time_window.py:140 nested test_until_ts_wider_than_the_capture_keeps_every_row_after_a_clock_step.run asserts@[149, 153, 154, 155] used=['arg-of:run_coro']
- host/tests/test_store_time_window.py:165 nested test_the_until_ts_ceiling_stays_inside_the_ts_index.run asserts@[173, 174, 175, 178] used=['arg-of:run_coro']
- host/tests/test_store_time_window.py:197 nested test_since_ts_excludes_its_own_instant_where_the_id_floor_cannot.run asserts@[204, 207] used=['arg-of:run_coro']
- host/tests/test_store_time_window.py:218 nested test_the_ceiling_walk_names_the_highest_id_not_the_newest_ts.run asserts@[227, 229] used=['called-directly']
- host/tests/test_store_writer.py:116 nested test_failed_child_insert_leaves_no_orphan_line.run asserts@[127] used=['called-directly']
- host/tests/test_store_writer.py:138 nested test_bad_row_in_a_batch_does_not_lose_its_neighbours.run asserts@[151, 154, 156, 159] used=['called-directly']
- host/tests/test_store_writer.py:169 nested test_batched_children_attach_to_their_own_line.run asserts@[185, 187] used=['called-directly']
- host/tests/test_store_writer.py:198 nested test_writer_splits_a_backlog_across_capped_commits.run asserts@[221, 222, 223, 225, 228] used=['called-directly']
- host/tests/test_store_writer.py:277 nested test_a_bare_carriage_return_is_folded_too.run asserts@[281, 283] used=['called-directly']
- host/tests/test_store_writer.py:295 nested test_store_stop_fails_queued_writes_instead_of_stranding_them.run asserts@[307] used=['called-directly']
- host/tests/test_store_writer.py:325 nested test_a_dead_store_writer_fails_writes_instead_of_hanging.run asserts@[328, 332] used=['called-directly']
- host/tests/test_store_writer.py:358 nested test_a_line_refused_by_a_dead_writer_counts_as_a_write_error.run asserts@[363, 367, 375] used=['called-directly']
- host/tests/test_store_writer.py:81 nested test_writer_survives_commit_failure.run asserts@[90] used=['called-directly']
- host/tests/test_store_writer.py:98 nested test_writer_survives_bad_insert.run asserts@[108] used=['called-directly']
- host/tests/test_update_check.py:135 nested test_no_update_when_running_the_newest.run asserts@[137, 138] used=['called-directly']
- host/tests/test_update_check.py:161 nested test_failed_check_is_silent.run asserts@[168, 169, 174, 175] used=['called-directly']
- host/tests/test_update_check.py:181 nested test_unexpected_body_is_a_successful_check_with_nothing_to_report.run asserts@[183, 184, 185] used=['called-directly']
- host/tests/test_update_check.py:191 nested test_pre_release_only_project_does_not_notify.run asserts@[193, 194, 195, 198] used=['called-directly']
- host/tests/test_update_check.py:207 nested test_config_disabled_never_requests.run asserts@[210, 211, 216] used=['called-directly']
- host/tests/test_update_check.py:229 nested test_repeated_demand_still_makes_one_request.run asserts@[235, 236, 241] used=['called-directly']
- host/tests/test_update_check.py:250 nested test_a_failed_check_is_not_retried_on_the_next_status.run asserts@[260, 261, 265] used=['called-directly']
- host/tests/test_update_check.py:354 nested test_unwritable_cache_dir_does_not_break_the_check.run asserts@[363, 364] used=['called-directly']
- host/tests/test_update_check.py:95 nested test_check_once_records_and_caches.run asserts@[97, 98, 99, 100, 103, 104, 110, 111, 112] used=['called-directly']

### JS verdict list (scratch-27-28/out-JS.md, class 28 part)

##### Findings
None. No assertion in the JS tests sits where a catch, in the test or in production, can reach it. No negative assertion lacks a discriminating matcher.

##### Enumerations (commands verbatim, counts)
Run from host/tests/webui_js.
- `grep -nE 'assert\.(throws|rejects|doesNotThrow|doesNotReject)' *.mjs` gives 4.
- `grep -nE '\btry\b' *.mjs` gives 48: 42 try statements plus 6 comment or string mentions (plots_seed_grammar:127, settings_revision:13, settings_late_answers:49, state_download_wait:11, terminal_tick_estimate:3, terminal_ts_width:2).
  - AST (`node js/finally.mjs host`) gives 42 TryStatements: 41 try/finally and 1 try/catch.
- `grep -nE '\bcatch\b' *.mjs` gives 5: 1 code (pane_regex_dialect:50) and 4 comments (digital_paused_freeze:4, statusbar_logic:362, plots_paused_freeze:5, state_plot_tick:86).
- `grep -nE '\.catch\(' *.mjs` gives 0.
- `grep -nE '\.then\(' *.mjs` gives 1.
- The JS translation of "an assertion inside a try whose catch can reach it" includes a production catch: an assert inside a callback that production code calls under its own try/catch (api.js onmessage, runBackfill, seedChannelList and friends).
  - AST (`node js/nested_assert.mjs host`): 81 assert calls outside a test() callback. 11 are in inner functions; 70 are in 53 top-level helpers.
  - AST (`node js/helper_refs.mjs host`): none of the 53 helpers is ever used other than as a direct call, so none is handed to production.

##### Rulings
- freeze.test.mjs:65 assert.throws(..., /needs a setPaused/) | complies: text unique to registerSurface's refusal
- freeze.test.mjs:66 assert.throws(..., /needs a isLive/) | complies: as :65
- state_logic.test.mjs:308 await assert.rejects(..., /bad regex/) | complies: the daemon envelope text, which reaches the message only through the `data.error` path (state.js:101)
- state_logic.test.mjs:310 await assert.rejects(..., /HTTP 500/) | complies: unique to the non-JSON-body path
- pane_regex_dialect.test.mjs:50 try { new RegExp(...); test lines } catch { js = "refused" } | complies: the try holds no assertion; it classifies whether the JS engine accepts the pattern. RegExp's only throw is SyntaxError, and the assertion sits after the try.
- try/finally, 41 sites | complies: no finally holds a return, throw or assertion (AST), so each finally only restores state and cannot mask a failure in its try:
  - api_plot_seed_ports_fallback:35
  - api_ws_backoff:31
  - app_layout:72
  - can_age_tick:36, :124
  - can_decode_once:22
  - can_logic:338
  - clear_staged_backfill:247, :505
  - digital_tick_reset:112
  - digital_zoom:76
  - plots_channel_cap:22
  - plots_chrome:231
  - plots_decimate:177
  - plots_decode_once:49
  - plots_hover_tick:60, :71
  - plots_pause_edge:140
  - plots_seed_grammar:106, :134
  - plots_tick_reset:86
  - plots_zoom:112
  - settings_export_hold:203, :227, :261, :302
  - state_download_preflight:107
  - state_download_wait:60
  - state_eol:79
  - state_logic:70
  - statusbar_logic:542
  - terminal_createpane:180
  - terminal_delta_mark:33, :46, :96
  - terminal_history:191
  - terminal_logic:184, :306
  - timewindow_axis_fit:59, :84, :99
- chrome.test.mjs:60 `return import(...).then((m) => { assert... })` | complies: the promise is returned, so the runner awaits it and a failed assertion rejects the test
- Nested asserts in inner functions (11), all invoked by test code, never by production:
  - api_ws_backoff:55 | complies: inside withCapturedTimers' callback (test helper; try/finally with no catch)
  - can_export_port:55, :65 | complies: inside exportWith's `set` callback, called by the test helper
  - can_table:126 | complies: CORPUS.forEach in the test body
  - chrome.test:61, :62 | complies: inside the returned .then above
  - digital_zoom:44, index_a11y_static:14, settings_ports_eol:110, style_layout_static:45, terminal_toolbar_static:15 | complies: helper arrows held in consts and called from test bodies
- Nested asserts in 53 top-level helpers (70 calls) | complies: helper_refs finds no helper passed as a value, so each runs only when a test calls it, outside any production catch
- Count-only console.error assertions (statusbar_logic:549, plots_seed_grammar:123, :148) | complies, nit N-JS-4


## The two questions, whole leg

Each group's own answers sit at the end of its last class: class 24 (15, 17, 24), class 18 (16, 18), class 22 (19, 22), class 21 (20, 21), class 26 (23, 25, 26), class 28 (27, 28).
Coordinator answers for the leg as a whole:

1. Least confident: class 27's verdict list.
   - The 27/28 agent split class 27 over seven helper agents, so most of its "complies" rulings come two delegation levels down and are reasoned, not driven. Its own re-drives cover FB1-1, FB2-1, FC2-2, FD-2, FC1-1/FD-1 and F-JS-4; the rest of its LOW findings were driven by helpers only.
   - R27-3, R28-4 and R17-3 are reasoned only; R15-3 is read only.
   - Every "owed on Windows" and "owed in a real browser" item listed per class is unverified, not passed.
2. Not yet checked: the sweeps ran at f31ecd9, and HEAD moved to 1a1251a mid-leg.
   - Only `pidfile.py` changed in scope, and that fixes O-1, so the class 18 and 22 rulings on `pidfile.py:143` describe the old code.
   - Across the classes, the registry's sweeps were imprecise in 15, 18, 27 and 28 at least (see each class's precision note). Before the next round, fold the improved commands into `docs/REVIEW.md`.
   - Agents sharing one machine collided on a daemon port (R15-1 drove itself). A leg that starts daemons should assign each agent a disjoint port and check its bind before trusting a `/status` answer.
