# Registry leg, classes 15-28

HEAD f31ecd995ee2ed193d8d60637b76620ddc880be3. Brief: `registry-brief.md`.
Classes complete: 15, 17, 24. The rest are in progress.
Evidence (probes, logs, built artifacts): `~/tt-data/mcuscope-2026-09-24/registry-leg/15-28/`.

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

In progress.

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

In progress.

### Class 19

In progress.

### Class 20

In progress.

### Class 21

In progress.

### Class 22

In progress.

### Class 23

In progress.

### Class 24

No findings.
One driver difference that the registry entry does not name was confirmed; it is filed below under "Registry sweep precision", not as a finding, because no site depends on it today.

### Class 25

In progress.

### Class 26

In progress.

### Class 27

In progress.

### Class 28

In progress.


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

In progress.

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

In progress.

## Class 19. Two engines validating one thing

In progress.

## Class 20. Non-sargable bound on a hot query

In progress.

## Class 21. Wall-clock granularity as a test ordering assumption

In progress.

## Class 22. A stdlib predicate standing in for a wire grammar

In progress.

## Class 23. A rebuild path silently un-freezes a paused surface

In progress.

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

In progress.

## Class 26. A frozen view re-derived from a ring buffer that has rotated past it

In progress.

## Class 27. A test double gentler than the thing it stands in for

In progress.

## Class 28. An assertion the test's own guard swallows

In progress.


## The two questions, whole leg

In progress.
