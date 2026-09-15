# Test-suite sweeps 2026-09-15: class 75, unproven absence, user dirs

Scope: the existing tests (`host/tests/*.py`, `support.py`, `conftest.py`, `host/tests/webui_js/*.mjs`), not `test_sweep_*` or `sweep_*`.
Tree: HEAD a259062 plus other agents' uncommitted product edits, which moved under the sweep.
Scripts and raw output: `~/tt-data/sweep-tests/`.
Per-site verdicts: [sweep-tests-sites-75.md](sweep-tests-sites-75.md) and [sweep-tests-sites-absence.md](sweep-tests-sites-absence.md).

Mutation method: the brief says to mutate product files in place, and also not to touch `host/mcuscope/`.
Other agents were running tests on that tree, so every mutation ran in a mirror instead.
- The mirror is `~/tt-data/sweep-tests/mut/`, a copy of host, firmware and tools; `PYTHONPATH` shadows the editable install there.
- Each mutation checked its anchor, copied the file, wrote the mutant, ran the tests, and restored from the copy (`mutate.py`).

## Sweep 1: class 75, hand-kept lists standing in for "every X"

Commands:
- Registry grep `grep -n "^[A-Z_]* = \[\|^[A-Z_]* = (" host/tests/*.py`: 25 lines.
  It misses dicts, sets, digits in names, lowercase names and literal `parametrize` lists, so it was widened.
- `enum75.py` (AST: module and class-level collection literals, literal `parametrize` lists): **140 Python sites**, a superset of the 25.
- JS: `grep -nE "(const|let|var) NAME = [|new Set([|{$" plus "for (const x of ["` over `webui_js/*.mjs`: **241 sites**.
  48 of them have a coverage word (every/each/all/covers) at the site, in its 3 lines above or in its test title (`jsctx.py`); all 48 were read.

Each verdict compared the list to a set derived from the source.
The sources: the click tree (`_option_strings`), `app.openapi()`, `app.routes`, and ASTs of cli.py and protocol.py.
Also index.html ids, `[project.scripts]`, `EOL_BYTES` and firmware `test_monitor.c`.

Violations, all fixed with a guard that derives the set and asserts a plausible count:

| list | what was wrong |
|---|---|
| test_eol `EOL_BODIES` ("every entry point") | 4 of 5 top-level `eol` bodies; POST /ports was driven with one bad value, not the set |
| test_review_r2_server config write failure | 4 of 5 `_save_error` routes (`/config/ports` missing); now parametrized over the guarded `BODIES` |
| test_protocol `PLOT_BODIES_*` ("both suites pin the same list") | drifted: 5 firmware unit bodies missing; now read from test_monitor.c |
| test_protocol `_DECIMAL_POSITIONS` ("every position") | CAN id slot never got a loose token; `!can id`, `can tx id` added; guard: every int()-calling function reached |
| test_prerelease_fixdiff_py `GATES` ("every gated option") | `--changes`, `--deadband` missing; case added; guard derives gated flags from cli.py |
| test_cli_contract negative limit | `log export --limit -1` missing (4 of 5 derived) |
| test_prerelease_cli_fixes refused export | `session export` missing; added, driven to `/sessions/1/export` |
| smoke.test.mjs `expect` ("every module") | 14 of 18 modules; keys now asserted equal to ORDER |
| complete today, now guarded | test_cli_export `WINDOWED`, test_cli_r2026_09_12 `EXPORTS` and `BOUNDED`, test_rulings_daemon_config `BODIES` |
| complete today, now guarded | test_cli `LIST_FIELDS` (+ `forbid` case), test_scaffold `CONSOLE_SCRIPTS`, test_webui_js `GUARD_URLS` (25 of 25 clauses) |
| complete today, now guarded | cmdbar_eol eol set, settings_offline `DAEMON_CONTROLS`, rulings_chrome_settings E-8 saves |

`GUIDE_EXEMPT` complies (an exemption on a mechanical walk); a stale-exemption check was added.

Violates, not fixed (product files):
- The derived `--last-ms` commands include `assert`, which has no client-side bound.
  `mcu assert --expect x --last-ms -5000` goes to the daemon (exit 3 unreachable), where lines, log export, can dump and plot export refuse with exit 1.
- The derived last_ms routes include POST /assert: `-60000` is a 422, but so is `0` (`gt=0`, server.py:274), where the GET routes take 0 as a window (A-9).

## Sweep 2: a negative assertion whose observation point never received the thing

Commands:
- `enum_neg.py`, a Python AST pass over `assert` statements for `x not in y`, `x == []/()/{}/set()/""/0/b""`, `not x` and `x is False`: **759 sites**.
  237 are exit or status code `== 0` (success, exempt).
  The other 522 were bucketed by what is observed (`bucket_neg.py`): filesystem 57, calls 32, text 128, db 56, state 249.
  Every filesystem and calls site was read; text, db and state sites were read where no same-root positive exists in the test.
- `jsneg.py`, for `equal(x, 0|false|""|null|undefined)`, `deepEqual(x, []|{})`, `ok(!x)` and `doesNotMatch`: **487 sites**.
  Every recorder negative without a same-root positive was read.
  So were all 48 `byId` negatives and all 29 URL-param `has()` negatives.
- `fromimport.py`: monkeypatch targets intersected with `from .mod import name` bindings.
  114 resolved patch sites, 51 targets, 7 bound by name elsewhere; all comply:
  - `cli_client.DEFAULT_URL` is patched on both modules.
  - `pidfile.pid_running`, `read_pid_record`, `pid_file_path` and `config.replace_atomic` are imported at call time or used through their own module.
  - `cli_output._silence_stdout`: the driven path is cli_output's own global.
  - `serial_link.cached_comports`: the tests drive `port_identity` in serial_link.
- Child spawns (the class 33 half), AST over `subprocess.run/Popen` and `mcu_sim.spawn`: **51 sites**, ruled below.

Violations and fixes:

| site | why it could not fail, or was never shown to | fix |
|---|---|---|
| can_age_tick "an idle tick re-rendered every cell" | the CAN count is drawn only in the row hover, never in a cell; `!cells.includes("777")` was always true | observe `tr.title`; positive: `renderCan()` writes `777 frames` there |
| statusbar_logic `bindById.checked === false` | followed an unticked attach, and the stub checkbox starts false | also asserted after the ticked attach |
| prerelease_chrome_settings stale `cfgSessionsErr` | nothing anywhere showed a failed fill writes that slot | positive-control test added |
| test_cli `_stage_backfill` "never retrieved" | loop exception-handler capture never shown to receive an orphan report | same-loop orphaned recv asserted to reach `reports` |
| test_review_r2_cli follow drain "never retrieved" | caplog capture of a GC-time report never shown to receive | orphaned-task control inside the same caplog |
| test_rulings_daemon_plot "does not rescan" | the spy on `server.learn_stored_plot_defs` never shown to see a rescan | detached board in the same test: `calls == [alias]` |
| test_regressions orphan-port race `started == []` | the patched `SerialPort.start` never shown called by this attach | sibling test: an unraced attach gives `started == ["b"]` |
| test_review_r2_config `glob("*.tmp") == []` x2 | nothing tied the glob to the name the writer uses | `seen[0].endswith(".tmp")` |
| test_cli orphaned daemon pid dir | hand-joined `data_home/mcuscope` instead of the child's resolution | `_child_data_dir(data_home)` |
| test_hardening plan negatives (8 sites) | "TEMP B-TREE"/"SCAN lines" absence passes on a build that words it differently (the helper's own docstring) | vocabulary control test on this SQLite |

Already fixed before this sweep: test_rulings_cli_closed_pipe (positive control at `:105`).
Notable complies (the rest are in the sites file):
- The 21 filesystem absences on `-o` or config paths: exports open `-o` themselves, with no temp sibling (cli.py `_stream_export`, cli_client download).
- The pid-dir absences: pidfile resolves platformdirs at call time; M6 red.
- `"Traceback" not in stderr` on children: console_entry re-raises after the crash log; driven, a crashing child prints Traceback and writes `mcu-crash.log`.

Child spawns, 51 sites:
- 28 violated, fixed. All now go through `support.child_env()`: XDG data, config and cache under a suite-private temp home.
  - 16 inherited the user's dirs entirely, so a crash wrote `mcu-crash.log` into `~/.local/share/mcuscope`.
    test_cli (10: run_mcu, run_mcu_closed_pipe x2, the can-dump and follow Popens, ai-guide x2, --version x2, usage error).
    Also test_break ai-guide, test_scaffold, test_sim_pty x2, test_sim_tcp, test_prerelease_cli_fixes closed stderr.
  - 12 isolated only the data dir, so a spawned daemon read `~/.cache/mcuscope/update.json`.
    test_cli: `_spawn_env` users x5, `_run_mcu_data_home`, `_child_data_dir` probe, `daemon stop` x2; plus test_cli_ux restart, test_pidfile SIGTERM, test_review_r2_cli unusable data dir.
- 2 comply: test_rulings_cli_closed_pipe patches platformdirs inside the child, and those commands resolve nothing else.
- 21 exempt, because no mcuscope console entry runs or nothing resolves a user dir:
  - 9 `mcu_sim.spawn` threads, in process under conftest;
  - 3 node and 2 make/cc runs;
  - 4 `python -c` with no mcuscope (pass x2, sleep, a stdout write) and 1 explicit-path CaptureLock child;
  - 2 in-child `cli.main` import checks with no crash handler.
- Windows ignores XDG, so there a crashing child still writes its log to the real `%LOCALAPPDATA%`: unverified, needs the Windows leg.

Mutation results, all expected red:

| id | mutant | result |
|---|---|---|
| M1 | `_stage_backfill` leaves its recv | red: `'Task exception was never retrieved'` |
| M2 | follow drain leaves its pending recv | red (the follow raised ConnectionClosedOK) |
| M3 | /plot/channels rescans attached boards | red: `calls` gained `board` |
| M4 | attach skips the post-prime closed check | red, at the `PortError` assertion; the new sibling proves `started` is observed |
| M5 | `_remove_pid_record` never removes | red: the pid record exists |
| M6 | daemon main never releases the pid | red in test_daemon_startup and test_rulings_daemon_startlog |
| G1-G5, G7, G11, G12 | drop one member of each guarded list, or add a stale exemption | red, naming the missing member |
| G6 | drop `!m tick` | red: `parse_marker` unreached |
| G6 (first try) | drop `!can id` and `can tx id` | green: every `!can` template reaches `parse_hex_int` through its valid id; the guard's granularity is per function |
| G8, G9 | drop the unit exemption; firmware accepts a host-refused body | red |
| G10 | exportdlg_guards gains a clause no URL reaches | red (dropping one URL stayed green: 3 URLs produce "names lists ... twice") |
| J1 | statusbar no longer resets bindById | red at the new assertion |
| J2, J2b | stale fill writes its error; failed fill writes nothing | red (negative, then the new control) |
| J3 | the idle tick calls `renderCan` | red |
| J4, J5, J6 | protocol gains an eol; index gains `cfgFooSave`; smoke map loses app.js | red |

Verification: every touched Python file passes (1077 + 143); the one failure is not mine.
`test_regressions.py::test_only_the_documented_commands_emit_jsonl` fails `set() == {'_tail_snapshot', 'can_dump'}`.
The uncommitted cli.py change moved per-row emitters from `out_json` to `emit_stream`, and that test derives by the name `out_json`.
The batch that made the change owns that test update.
All 732 JS tests pass (non-sweep files); ruff clean.

## Sweep 3: the real user dirs

Group run (test_daemon_startup, test_rulings_daemon_startlog, test_stdio, test_rulings_cli_closed_pipe, test_capture_lock), 57 passed:
- The before/after name+mtime lists are identical.
- strace `%file` shows zero syscalls under `~/.local/share/mcuscope`, `~/.cache/mcuscope` or `~/.config/mcuscope`.
- No new directories under host/ or the repo root.

Wider strace, all 23 existing child-spawning files: no writes, but 5 reads of the real `~/.cache/mcuscope/update.json`:
- child daemons of `test_cli_ux.py::test_restart_of_a_running_daemon_swaps_the_pid` (2), `test_pidfile.py::test_daemon_releases_pid_file_on_sigterm` and `test_cli.py::test_daemon_start_pid_file_is_keyed_by_host_port`;
- in process: the module-scoped `stack` fixture in test_timeline.py, set up before conftest's per-test patch.

Fixed: `child_env()` for children, and `conftest.isolate_user_dirs` applied inside the module fixture.
After: strace over 27 files, zero syscalls on the three real dirs.

Unattributed: `~/.local/share/mcuscope` (the directory itself) has mtime 13:44:35 today, before any run of mine.
An entry was created and removed there; `capture.db` and `capture.db.lock` still carry 2026-09-14 mtimes.
My strace runs cannot reproduce it.

## Other findings

- Firmware refuses a unit with µ, control bytes or DEL (7-bit printable only, monitor.c); the host accepts all three.
  This is the safe direction and is named as an exemption in the test. Owner should pick whether the host refuses them too.
- test_monitor.c does not pin `st:u1:=-0=A`; the host test does. A driver build under `~/tt-data/sweep-tests/fw/` shows the firmware refuses it (BADARG).

## The two questions

1. Least confident: the mechanical sweep 2 rules.
   - The 160 "direct return value" and 21 "captured output" Python sites were assigned by bucket and root-name matching, then sampled, not read one by one.
     The re-drive was the "Traceback reaches a child's stderr" ruling, which had been reasoned; it holds (crashing child: rc 1, Traceback, `mcu-crash.log`).
   - The same doubt covers the JS keyword filter: 193 sweep 1 sites with no coverage word nearby were ruled without reading.
   - Windows: `child_env` does nothing there, and the class 33 child fix is Linux-only until the Windows leg runs.
2. Not yet checked:
   - The `test_sweep_*` and `sweep_*` files the other agents are adding, which this sweep excluded.
   - Hand lists in firmware/tests (C).
   - Negatives written as `assert.notEqual`, `strictEqual(x.includes(..), false)`, or pytest helpers returning a bool that the test then negates; none of these match the enumerators.
   - Whether `!can` event ids should accept `0x10`: `parse_hex_int` tolerates `0x` for SPEC 3.4 REST, and the event path takes it too; not checked against SPEC 2.5.

## Does sweep 2 deserve a registry class

Yes: 10 instances in one pass, 3 of which could never fail (can_age_tick count, bindById after an unticked attach, the closed-pipe crash dir).
Class 29 asks that the negative is asserted; this is the next step: the negative is asserted where the thing never goes.

- Invariant: every assertion of absence has a positive control, in the test or in a sibling it cites, showing that its observation point receives the thing: a file dir, recorder, capture, spy, DOM property or plan text.
- Sweep:
  - `grep -nE "not in |== \[\]|== \{\}|== set\(\)|is False|assert not |\.exists\(\)" host/tests/*.py`
  - `grep -nE "(equal|strictEqual)\([^,]+, (0|false|\"\"|null|undefined)|deepEqual\([^,]+, (\[\]|\{\})|assert\.ok\(!|doesNotMatch" host/tests/webui_js/*.mjs`
  - Rule each; exit-code `== 0` sites are exempt.
  - Then intersect every monkeypatch target with `from .mod import name` bindings in the package, and rule every child spawn's platformdirs.
  - Scripts that do all of this: `~/tt-data/sweep-tests/enum_neg.py`, `jsneg.py`, `fromimport.py`.
