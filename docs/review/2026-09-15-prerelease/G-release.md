# Leg G: release readiness and compatibility

Scope: `v0.4.0..fdd30a2` (13 commits), judged as the next release.
Scratch and evidence: `~/tt-data/prerelease-2026-09-15/G/` (build from `git archive HEAD`, compat matrix outputs `c-*/`, floor suite log `suite310low.log`, helper reports `agent-changelog.md` and `agent-docs.md`).
Daemons ran only on 18617/18627 with scratch TOMLs; all stopped by PID. Each left a `mcuscoped-127.0.0.1-<port>-startup.log` in the platformdirs data dir.

## Findings

1 MED, 8 LOW.

### G-1 MED: the CLI turns every 503 into exit 3, including a live daemon's subscriber cap

- Where: `host/mcuscope/cli_client.py:165`.
- Defect: `Client.fail` maps any 503 to exit 3 ("daemon unreachable"), but `/wait` and `/assert` also answer 503 for `too many subscribers (max 256)` (SPEC 3 line 579, `server.py:2283`, `server.py:2563`).
- Failure: an agent's `mcu wait` against a busy but healthy daemon exits 3 and the agent concludes the daemon is gone (restart, abort), where 0.4.0 exited 1.
- DRIVEN: `subs.py` opens 300 `/ws` connections to the HEAD daemon, then runs `mcu wait --match NEVER --timeout 500`.
  - HEAD CLI: `rc=3 error: too many subscribers (max 256)`.
  - 0.4.0 CLI, same daemon: `rc=1`, same message.
- Class: new class candidate: "a client maps a status code to one cause when the server emits it for several" (class 55's shape on the wire rather than on a kind name).
- Fix: map to 3 only on the shutdown answer (`_SHUTDOWN_MSG`, or a distinct field in that body), and leave other 503s at 1; add a subscriber-cap test.

### G-2 LOW: CHANGELOG is missing three user-visible changes

- Where: `CHANGELOG.md` Unreleased.
- Defect and scenario: a user reading the notes does not learn:
  - `mcu attach DEV` without `--alias` now sanitises and truncates the derived alias (`cli.py` `_derive_alias`). A `/dev/serial/by-id/...` attach, refused in 0.4.0 (alias grammar), now attaches as `usb-STMicroelectronics_STLINK-V3`.
  - `/ws` (so `mcu tail -f` and the web UI stream) closes at the start of shutdown (`store.stop_subscribers` via `handle_exit`). Lines 26-27 and 33 name only `/wait` and `/assert`.
  - `/plot/export` deadband with no `=` now says `deadband needs name=value` (`server.py:2906`).
- DRIVEN (alias: `python -c "from mcuscope.cli import _derive_alias as d; print(d('/dev/serial/by-id/usb-STMicroelectronics_STLINK-V3_004A00283137510B39383538-if02'))"`); the other two REASONED from the diff.
- Class: none (release notes).
- Fix: add the three entries; the alias one replaces Fixed line 171 (see G-3).

### G-3 LOW: CHANGELOG entries describe fixes to states no release shipped

- Where: `CHANGELOG.md` lines 49, 59, 116, 127, 169, 171.
- Defect: entries written against intermediate commits, not against 0.4.0:
  - 171 (`--serial` alias fix) and 169 (cancelled token prompt export) fix code added in this same range (`git show v0.4.0:host/mcuscope/cli.py | grep -c '"--serial"'` is 0).
  - 59 and 127 ("port default" entry "again", "instead of `port default (lf)`"): 0.4.0's cmdbar had no port-default entry (`git show v0.4.0:host/mcuscope/webui/cmdbar.js` line 70).
  - 49 (port chip target italics, not repeated) refines Added 125, which is itself new.
  - 116 (drag zoom "now" zooms every chart) is under Added, but 0.4.0 had a per-chart drag zoom: a Changed.
- Scenario: a 0.4.0 user reads that bugs they never had were fixed, and cannot tell what actually changed for them.
- DRIVEN via `git show v0.4.0:...` as cited (helper report F4-F8).
- Fix: fold 169 into Changed 29, 171 into the alias entry of G-2, 49 into Added 125, 59 into 127 without "again", move 116 to Changed.

### G-4 LOW: CHANGELOG (and SPEC 4) overstate or misstate five changes

- Where: `CHANGELOG.md` lines 20, 26, 28, 152, 180; `docs/SPEC.md:1058`.
- Defect:
  - 20 and SPEC:1058: the `mcu wait` timeout names the port only with `-p` (`cli.py:1159`). DRIVEN: `mcu wait --match NEVERMATCH --timeout 1000` printed `timeout: no line matched 'NEVERMATCH' in 1001 ms`.
  - 26: "exit 3 when the daemon stops" is true, but the code does it for every 503 (G-1).
  - 28: "about 45 ms" disagrees with the code comments (37 ms of 66 ms, `cli_client.py:26`).
  - 152: `-o -` is refused on `log export`, `plot export`, `can dump` and `session export`, not only `--bundle`. DRIVEN: `mcu log export -o -` exits 1 naming `-o -`.
  - 180 and SPEC 3.4: "a purge or retention sweep of that span waits"; the bundle holds the store-wide sweep lock (`server.py:1441`), so every purge and sweep waits. REASONED.
- Scenario: a reader relies on the port being named, or on `-o -` still writing a file on the other commands.
- Class: none.
- Fix: correct the wording (the 26 wording follows the G-1 ruling).

### G-5 LOW: SPEC names a stderr log file the CLI does not write

- Where: `docs/SPEC.md:1074`.
- Defect: SPEC says `mcu daemon start` writes stderr to `<data dir>/mcuscoped.err`; the file is `mcuscoped-<host>-<port>.err` (`cli_daemonctl.py:49-54`).
- Scenario: a user debugging a failed start opens a file that does not exist.
- DRIVEN (helper report F2): `_stderr_log_path(pid_file_path('127.0.0.1', 8558))` gives `~/.local/share/mcuscope/mcuscoped-127.0.0.1-8558.err`.
- Class: 58 (doc naming a file nothing writes). The doc is wrong; shipped in 0.4.0 as well.
- Fix: correct SPEC:1074.

### G-6 LOW: SPEC 3 status-code list omits the new shutdown 503

- Where: `docs/SPEC.md:579`.
- Defect: the "no other shape" list gives 503 only for the subscriber cap; SPEC:734 adds a shutdown 503 in this range.
- Scenario: a client author following 3's list writes the G-1 bug the other way round.
- REASONED: `server.py:1926`, `server.py:1935`.
- Class: none.
- Fix: add the shutdown case to the list, with the exit code each maps to.

### G-7 LOW: SCREENSHOTS.md recipe is wrong on persistence and the port

- Where: `docs/SCREENSHOTS.md:51` and `:28`.
- Defect:
  - 51 says divider positions are not persisted. New in this range, the sidebar width and CAN cap are stored per browser (`layout.js:15-25`, SPEC 9.1). The recipe uses a fresh profile, so the capture itself is unaffected, but the sentence is false.
  - 28 opens `http://127.0.0.1:8799/ui/`, while step 1's TOML sets no port. The demo binds 8558 and collides with the owner's live daemon. Pre-existing.
- DRIVEN: `git blame` puts line 51 in a813ef1 (after v0.4.0). The port is REASONED from the recipe text.
- Class: 58.
- Fix: say only pane widths are not stored; add `[server] port = 8799` to step 1.

### G-8 LOW: SPEC says 400 where the API answers 422

- Where: `docs/SPEC.md:715`.
- Defect: `/can/frames` `bus` "is 1 to 9 (400 otherwise)"; the bound is a `Query(ge=1, le=9)`, so 422 (as SPEC:576 says of every bound).
- DRIVEN (helper report F1): `GET /can/frames?bus=0` gives 422. Pre-existing.
- Class: none. The doc is wrong.
- Fix: say 422.

### G-9 LOW: stale doc text (bundle of nits, all doc-side)

- `docs/SPEC.md:326,335`: "two console scripts"; there are three (`mcu-sim`).
- `docs/SPEC.md:966`: the migration paragraph omits the `can_frames.bus` ADD COLUMN (`store.py:131-135`).
- `README.md:303`: "the UI status bar shows the size"; since this range it is in the daemon chip hover and in the bar only once trimmed.
- `README.md:187`: CAN table "with counts"; the count column moved to the row hover in this range.
- `README.md:325`: "`--token` overrides `[server]`"; the token is not a config key (the loader warns `server.token in the config file is ignored`, DRIVEN).
- `README.md:298`, `docs/SPEC.md:462`: default db path written as `<user_data_dir>/mcuscope/capture.db`. The code is `user_data_dir("mcuscope")/capture.db`, which on Windows is `%LOCALAPPDATA%\mcuscope\mcuscope\capture.db`.
- REASONED from the cited lines, except where marked (helper report F6, F7, F9-F12).
- Class: 58 for the `--token` line; the rest none.
- Fix: one-line edits each.

## Checks

1. **Build and contents.** PASS.
   - `uv build` of the exported copy gave `mcuscope-0.4.0-py3-none-any.whl` (423 kB, 48 entries) and `mcuscope-0.4.0.tar.gz` (831 kB, 190 entries).
   - The wheel holds the 20 Python modules, the 22 web UI files (`git ls-files host/mcuscope/webui`: 22 of 22, including the new `layout.js`), the metadata and the license.
   - Every reference is present: index.html's `href`/`src` (`vendor/uPlot.min.css`, `style.css`, `vendor/uPlot.iife.min.js`, `app.js`; favicon is a data URI, no fonts or images), and every `./x.js` import (17 distinct).
   - Nothing test-only or large is in the wheel.
   - The sdist carries `tests/` (including 81 `webui_js` files) and `contrib/`, as 0.4.0 did.
   - CI `ci.yml` build job asserts every `webui/` source file ships.
2. **Floor install.** PASS.
   - Python 3.10.20 venv. Direct deps resolved to exactly the floors: pyserial 3.5, fastapi 0.115.7 (starlette 0.45.3), uvicorn 0.35.0, typer 0.26.0, httpx 0.27.0, platformdirs 4.0.0, websockets 14.0, tomlkit 0.12.0, regex 2024.4.16. Resolving the wheel itself with `lowest-direct` lowers nothing, because its deps are transitive; `-r pyproject.toml` then `--no-deps` wheel was needed.
   - `mcuscoped --sim --config new.toml` started. `mcu status`, `devices`, `plot channels`, `wait` (timeout text), `log export --csv --changes`, `can dump --session/--to -f`, `session export --bundle -o run.DB` / `-o -`, `attach --serial` / device plus `--serial`, `lines/tail --decode --changes`, `plot export --decode --changes`, `tail -f` and `can dump -f` (WS) all behaved.
   - All 22 assets plus `/ui/` answered 200 with `text/javascript` / `text/css` / `text/html; charset=utf-8` and `cache-control: no-cache`. `/` and `/ui` answer 307 to `/ui/`.
   - The installed package is byte-identical to HEAD `host/mcuscope` (`diff -r`).
3. **3.10 floor over the diff.** PASS.
   - All 81 `.py` files compile under 3.10.
   - A grep of added lines for 3.11+ names found nothing: tomllib, ExceptionGroup, `except*`, datetime.UTC, Self, StrEnum, TaskGroup, asyncio.timeout, NotRequired, assert_never, itertools.batched, add_note, fromisoformat and others.
   - Full suite on 3.10 with lowest-direct `.[dev]`: `1608 passed, 1 skipped in 453.70s`. The web UI JS (node 22) and firmware C tests ran.
4. **Compatibility.** PASS except G-1.
   - Matrix: 41 commands times {HEAD CLI, 0.4.0 CLI} times {HEAD daemon, 0.4.0 daemon} (`compat.sh`, `tab.py`, outputs `c-*.txt`).
   - HEAD CLI against 0.4.0 daemon: every command ran with the same exit code as against HEAD. `can dump --session` works (0.4.0 declares `session`); decode priming (`order=asc&id_to=`) decodes correctly.
   - 0.4.0 CLI against HEAD daemon: only differences are the old CLI's own surface (`can dump --session`, `attach --serial` are usage errors, exit 1, clear). `can dump --to T -f` streams for ever there (the 0.4.0 bug, CLI-side). `session export --bundle -o run.DB` is accepted by the old CLI.
   - The unknown `session=` 400 reads clearly in the old CLI: `error: no such session: nosuch`, exit 1, on `lines`, `plot export`, `log export`.
   - Response shapes are additive only: `/plot/channels` gains `ports`, the manifest gains `from_id`/`to_id`. The helper's OpenAPI dumps show identical routes and params between v0.4.0 and HEAD.
   - New web UI on-load GETs against a 0.4.0 daemon: `/status`, `/ports`, `/sessions?limit=200`, `/plot/channels[?port=]`, `/lines?order=desc&limit=200`, `/lines?match=^!pd &order=asc&limit=1000&since_id=0&id_to=N`, `/plot/series?...&port=`, `/can/frames`, `/config`, `/devices` and `/plotjuggler` all answered 200.
   - `id_to=0` (a surface paused before its first line) is 422 there. The UI is always served by its own daemon, so this is reachable only with a tab left open across a downgrade.
5. **CHANGELOG.** FAIL (LOW): G-2, G-3, G-4.
   - Coverage otherwise complete: every CLI, REST, daemon, sim and web UI change maps to an entry, and all 45 drafted bullets in the 2026-09-12 batch reports are present.
   - No em or en dash.
   - Version strings to bump: `host/mcuscope/__init__.py:31` only (hatch reads it), plus the CHANGELOG heading `## [Unreleased]` to `## [X.Y.Z] - date` and the compare links at the bottom.
   - Do not bump `cli.py:611` `CLOCK_BOUND_MIN_VERSION = "0.4.0"`, nor the version fixtures in tests.
   - Built artefacts currently say 0.4.0, which PyPI would refuse; `release.yml` checks tag against `__version__`.
6. **Docs vs code.** PASS on existence, FAIL (LOW) on behaviour text: G-5 to G-9.
   - 845 tokens extracted from README.md, host/README.md, SCREENSHOTS.md, SPEC.md: 818 comply, 27 exempt, 0 name a missing flag, key, variable, command, route or parameter.
   - The SPEC 4 table was checked both ways against the click tree (52 entries). `mcu ai-guide`: 0 unknown flags.
   - `host/README.md` image is absolute and answers 200, but see Decisions.
7. **DB schema.** PASS.
   - No CREATE/ALTER/DROP change in `store.py` over the range; only `PRAGMA cache_size`.
   - Upgrade: a 0.4.0-written capture (34425 lines, 5 sessions, auto_vacuum=2) opened under the HEAD daemon on floor deps. Old sessions listed; `lines --session 1 [--decode]`, `can dump --session 1`, `lines --from/--to`, `session export` and `--bundle` (manifest has `from_id`/`to_id`) all worked. The schema object list is unchanged.
   - Downgrade: the same file, after HEAD wrote to it, reopened under the 0.4.0 daemon. HEAD's session 6 read, decoded and bundled.
   - SPEC promises in-place migration only (SPEC:966), nothing about downgrade; it holds in practice for this release.
   - Config: a 0.4.0-written `config.toml` (all four PUT sections plus a port with `eol`, `identify`) loads under HEAD with no warning. Unknown-key warnings print at daemon startup, DRIVEN with `storge`/`retention_dayz`.

## Sweeps

### Class 15 (shipped artifact vs stand-in)

Enumeration: the deliverables listed in the class, 5.

- `mcuscoped`, `mcu`, `mcu-sim` console scripts: complies. `release.yml` wheel-smoke runs all three from an installed wheel on Windows. This leg ran `mcuscoped` and `mcu` from the installed wheel on 3.10 floor deps, and `mcu-sim --help`.
- Wheel contents: complies. `ci.yml` build job checks every `webui/` file against the wheel; driven here 22/22.
- Web UI and vendored assets: complies. `test_webui.py` serves from the source tree, and this leg GET 22/22 from the installed wheel, byte-identical to HEAD. No CI job GETs assets from an installed wheel; the byte-identity makes that exempt.
- Exports: complies. `test_cli_export.py`, `test_session_bundle.py`; driven here through the installed CLI.
- `tools/mcu_sim.py` shim: complies. It gains `_plot_signals` in this range; imported by the suite via `conftest.py`.

### Class 24 (fix resting on one runtime's driver behaviour)

Enumeration: driver, stdlib or library-behaviour mechanisms added in the diff (`git diff v0.4.0..HEAD -- host/mcuscope/store.py host/mcuscope/cli_client.py host/mcuscope/update_check.py`, reading every PRAGMA, execute/executescript, fetch and `sys.modules` hunk): 6 sites.

- `store.py:2667` `_reclaim_backlog`: complies. `PRAGMA freelist_count` fetchone, then `_reclaim_pages` via `executescript` (the class's own fix). Tests `test_successive_ticks_hand_the_freelist_back...`, `test_one_tick_reclaims_at_most_the_bound`, `test_the_age_sweep_path_reclaims_too` pass on 3.10 floor and on 3.11.15 (the class's failing version).
- `store.py:1581-1584` MAX(id) over `INDEXED BY idx_lines_ts`, None row handled: complies. Passes on 3.10 and 3.11; the index is created by migration on any older capture (driven on the 0.4.0 DB).
- `store.py:546` `PRAGMA cache_size=-65536`: complies. A setter pragma, no rows to step.
- `store.py:618` `stop_subscribers` (asyncio.Queue sentinel): complies. The floor suite passes (shutdown 503 tests).
- `cli_client.py:30`, `update_check.py:44` `sys.modules["httpx._main"] = None`: complies. It rests on httpx's `except ImportError` stub, present in httpx 0.27.0 (floor) and 0.28.1 (latest `<1.0`); DRIVEN 0 `rich` modules on import.

### Class 42 (exception class the floor does not raise)

Enumeration: `grep -nE 'except .*TimeoutError|suppress\(.*TimeoutError' mcuscope/*.py tests/*.py`: 13 sites.

- `serial_link.py:1184`, `server.py:1983`, `server.py:2171`: complies. `asyncio.TimeoutError` around `asyncio.wait_for`.
- `server.py:2082`, `server.py:2416`, `store.py:418`, `cli.py:864`: complies. Builtin `TimeoutError` from `regex` `timeout=`.
- `sim.py:781`, `tests/test_sim_tcp.py:43`, `:198`, `:247`, `tests/test_plotjuggler.py:409`, `tests/test_sim.py:534`: complies. Socket timeouts; `socket.timeout` is `TimeoutError` from 3.10.
- None of these sites are new in the range; no new handler names a 3.11-unified class. The floor suite on 3.10 passed.

### Class 43 (dependency floor no install resolves to)

Enumeration: `pyproject.toml` `>=` entries, 9 runtime plus 7 dev (unchanged since v0.4.0), plus `grep -n 'except (ImportError|ModuleNotFoundError)' mcuscope/*.py`: 1 site.

- 9 runtime floors: complies. Installed at exactly the floor and the daemon, CLI, WS and web UI ran.
- 7 dev floors (pytest 8.0.0, pytest-asyncio 0.23.5, starlette 0.44.0, pytest-timeout 2.3.1, pytest-cov 5.0.0, pytest-randomly 3.15.0, ruff 0.5.0): complies. Full suite passed at the floor.
- `server.py:158` `except ImportError`: complies. It logs a warning before returning.

### Class 46 (client reads a field a newer daemon added)

Enumeration:
- (a) fields added to SPEC 3 in the range (`git diff v0.4.0..HEAD -- docs/SPEC.md`): `/plot/channels.ports`, manifest `from_id`/`to_id`, the shutdown 503 body. 3 fields; `grep -nE '\["(ports|from_id|to_id)"\]' mcuscope/cli*.py`: 0 reads.
- (b) every subscript read added to `cli*.py` (`git diff -U0 v0.4.0..HEAD -- 'mcuscope/cli*.py' | grep -E "^\+.*\[['\"][a-z_]+['\"]\]"`): 10 sites.

- `cli.py` `body['db_path']` (status): complies, in `/status` since 0.1.0.
- `port['alias']` (attach): complies, since 0.1.0.
- `r["raw"]`, `defs[di]["raw"]`, `row["raw"]` twice (decode priming): complies; the `port` beside them is read with `.get`.
- `res['sends']`: complies, guarded by `"sends" in res`.
- `ch["last_value"]`, `ch['count']` (plot channels): complies, in 0.1.0 `server.py:1081-1084`.
- `params["session"] = session`: exempt, a write, not a read.
- `lines_trimmed` and `waited_ms` are read with `.get`.

### Class 58 (in-product help naming what nothing reads)

Product side. Enumeration over `host/mcuscope` (py, js, html):
- `section.key` grep: 16 distinct tokens.
- `MCUSCOPED?_*`: 6 variables.
- `mcuscoped ... --x`: 5 tokens.
- `mcu-sim ... --x`: 2 flags.
- `mcu <cmd>` in webui/daemon/server: 14 tokens.

- `plotjuggler.dest/enabled`, `storage.{auto_session,db_path,max_db_bytes,min_sessions,retention_days}`, `update.check`: complies (in `_KNOWN_KEYS`).
- `server.token` (8): complies. Attribute use, or text saying the key is ignored (`config.py:341`, `statusbar.js:22`).
- `update.available/checked_at/latest`: exempt, `/status` fields.
- `ports.hold/stop_all`, `server.started`, `server._csv_wide`: exempt, Python attributes.
- `MCUSCOPED_CONFIG`, `MCUSCOPED_TOKEN`, `MCUSCOPE_URL`, `MCUSCOPE_TOKEN`, `MCUSCOPE_START_TIMEOUT`, `MCUSCOPE_UPDATE_CHECK`: complies, each has an `environ` read.
- `mcuscoped --config/--host/--sim`: complies. `--plot` (`sim.py:1219`) names `mcu-sim --plot`: complies. A `-------------------` match is exempt (rule text).
- `mcu-sim --demo/--pty`: complies.
- `mcu assert/can/daemon/daemon start/stop/restart/log/mark/plot/status/wait --timeout/-p`: complies (`mcu daemon --help`, `mcu wait --help`). `mcu status ... DEGRADED` is `cli.py:209`. "mcu on the", "mcu status calls" are exempt (prose).

Docs side: 845 sites over README.md, host/README.md, SCREENSHOTS.md, SPEC.md; 818 comply, 27 exempt, 0 violate on existence. Per-category counts and extraction commands are in `agent-docs.md`. Behaviour-text violations are G-5, G-7, G-9 (`--token`).

## Decisions for the owner

- **Version number.** The unknown `session=` going from an empty 200 to a 400 is an interface change. CHANGELOG line 6 allows that in a minor while on 0.x, so 0.5.0 rather than 0.4.1 is the reading; the owner picks.
- **PyPI README image.** `host/README.md` links `raw.githubusercontent.com/.../main/docs/img/webui.png`.
  - Until `main` is pushed, it serves the 0.4.0 screenshot.
  - After release, any later screenshot commit changes the image on this version's PyPI page.
  - Pin to the tag (`.../v0.5.0/docs/img/webui.png`) at release, or accept tracking `main`.
- **G-1 exit code.** Which exit a subscriber-cap 503 should carry: 1 ("error", as 0.4.0) or a documented retryable code. SPEC 4 has no "busy" code.

## The two questions

1. **Least confident, and rechecked:**
   - "New CLI works against a 0.4.0 daemon" rested on the sim-only matrix. Rechecked the one silent-degradation path (unknown `session=` on an old daemon is an empty 200) and the decode priming paging against 0.4.0. Both behave; the empty 200 is 0.4.0's documented behaviour, not new.
   - The G-1 claim came from a helper as a reasoned note. Re-driven with 300 real WS subscribers, against both CLIs.
   - The class 24 driver mechanisms were run on 3.10 and on 3.11, the version where class 24 originally bit.
   - Not verified, platform owed: Windows. The wheel-smoke CI covers `--version`/`--help` only; web UI asset content types on Windows rely on `server.py:813`'s mimetypes override, untested here. The web UI was not loaded in a browser from the wheel: HTTP GETs and byte-identity only, and JS module execution is covered by the node suite against the same bytes.
2. **Not thought about, and checked or left open:**
   - Config round-trip across versions (a 0.4.0-written `config.toml` under HEAD's new unknown-key warning): checked, no spurious warning.
   - Browser cache across an upgrade mixing old and new JS modules: checked, `cache-control: no-cache` plus ETag.
   - Left open: whether the sdist's `tests/` can run standalone. `conftest.py` imports `../tools` and `test_firmware_monitor.py` needs `../firmware`, neither in the sdist. Pre-existing, not driven.
   - Left open: `_derive_alias` truncation means two identical debuggers' by-id paths derive the same 32-character alias. The second `mcu attach` without `--alias` is then refused as a duplicate (reasoned, not driven); a new flow, since 0.4.0 refused both.
