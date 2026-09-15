# Owner rulings: daemon side (2026-09-15)

Lint clean. Touched tests plus `test_webui_js.py`: 476 passed (22 files, random order).
Revert-verify harness: `~/tt-data/mutate.py` copies the file, mutates it, runs the named tests with `PYTHONDONTWRITEBYTECODE=1`, then restores from the copy; checksums were confirmed afterwards.
The first pass cached bytecode, so one mutant falsely survived; the table below is the second pass.

## 1. Version

- `host/mcuscope/__init__.py:31` is `0.5.0`.
- No test hard-codes the running build's version: the `0.4.0` strings are fake-daemon fixtures and minimum-version gates, left alone.

## 2. E-8 config revision

- `config.py:57` `config_revision(bytes | None)` gives sha256 hex, or `""` for no file.
- `config.py:163` `read_config` returns `(Config, revision)` from a single read, so the two always agree. `load_config` wraps it.
- `config.py:494` `_read_doc(path, revision)` compares on the bytes it then parses, and raises `ConfigConflict` (`config.py:37`, deliberately not a `ConfigError`).
- `config.py:535` `_write_doc` writes bytes and returns the new revision. Every `save_*` (`:562-602`) takes `revision=None` and returns the new revision.
- The compare and the write run in one `to_thread` call under the existing `config_write_lock`, with no await between them.
- `server.py`:
  - `revision` added to the five config bodies (`:311-355`).
  - `_save_error` maps `ConfigConflict` to 409 (`:1075`).
  - `GET /config` returns `revision` and derives `exists` from it (`:1086-1099`).
  - Every PUT passes `body.revision` and returns `revision` (`:1145, 1183, 1201, 1221, 1295`).
- `revision` has no length bound, so any mismatch is a 409, as the contract says, never a 422.
- Tests: `tests/test_rulings_daemon_config.py`.
  - Empty file vs absent file vs BOM.
  - Every route: returned revision matches the file and GET.
  - Every route: a hand edit between GET and PUT gives 409, file bytes unchanged, nothing applied live.
  - `""` against a file created since; a revision against a file deleted since.
  - No revision (or `null`) skips the check; a garbage revision is a 409.
  - Two concurrent writers holding one revision, with a slowed write: exactly one 200, one 409.

## 3. A-5 warnings

- `config.py:48` `_warn` sends every loader warning to a per-call sink when `load_config(..., warnings=list)` is given, and logs otherwise (the default is unchanged).
- `daemon.py:365-372` collects the warnings, logs each once under `mcuscope.config`, and passes them on through `create_app(config_warnings=)` (`server.py:377, 423`).
- `/status` gains `config_warnings` (`server.py:925`). `GET /config` and `PUT /config/ports` read with a discarded sink.
- Scope choice: `config_warnings` carries every loader warning (unknown keys, out-of-range values, skipped ports, `server.token`), not only unknown keys.
- Tests:
  - Through `main`: 3 reads plus a save log each unknown key once, and `/status` lists exactly the logged warnings.
  - After a later edit, `/status` still shows the startup set.
  - Clean config gives `[]`; `create_app` default gives `[]`; a plain `load_config` still logs.

## 4. Named config must exist

- `daemon.py:368-369`: a path named by `--config` or `MCUSCOPED_CONFIG` that does not exist gives stderr `mcuscoped: no such config file: <path>` and exit **1**, the same code as an invalid config.
- The refusal happens before the files notice, the capture lock and the pid claim.
- Child under `mcu daemon start --config`: `python -m mcuscope.daemon --config <path>` runs the same `main`, so it prints that line to its `.err` file and exits 1.
  - Reasoned, not spawned: a subprocess daemon could touch the real capture.
  - The CLI now refuses before spawning in any case.
- Difference between the two sides:
  - The CLI (`_named_config`) uses `isfile`, so a named directory reads `no such config file`.
  - `mcuscoped` uses `exists`, so it reads `cannot read: ... Is a directory`.
  - Both exit 1. Not changed.
- Tests:
  - Option missing, env missing, flag wins over env both ways.
  - Missing default still starts with `not found, using defaults`; empty env means default.
  - A named directory gives `cannot read`, not the new message; a dangling symlink counts as missing.
- Existing tests fixed:
  - Tests that used an absent `-c` path for isolation now point at an empty file: `test_daemon_startup.py`, `test_review_r2_config.py`, `test_capture_lock.py`, `test_regressions.py`.
  - Removed `test_startup_says_a_named_config_was_not_found` and `test_startup_names_the_env_config_when_it_is_missing` (superseded).
  - Per the orchestrator: `test_daemon_start_warns_...` became `test_daemon_start_refuses_a_named_config_that_is_missing` (option/env: rc 1, message, no Popen; present: spawns).

## 5. C-3 detached definitions

- `serial_link.py:78` `learn_stored_plot_defs(store, alias, decoder)` is the bounded scan, now shared by `prime_plot_defs` (`:981`).
- `server.py:1771-1781`: a row whose port has no decoder gets a fresh decoder primed from that port's rows. The result is cached per request.
- The name-merged `PortManager.plot_channel_meta` is deleted: it had no other caller. `tests/test_plot.py:146` now uses `plot_channel_meta_by_port`.
- Choice: with no definition, `type/unit/scale/labels/group/bit/sid` are null and `kind` stays `analog`, the existing default for any undefined channel (attached boards included). Flag if `kind` should be null too.
- `test_decode_per_port.py:105` asserted the old fallback and now expects b's own labels.
- Tests:
  - Detached beside attached with the same names (enum and unit/scale).
  - Two detached boards with the same names.
  - A `!pd` below the lookback floor gives null fields; a board that never declared gives null fields.
  - Reconnect window (alias popped); unfiltered list labels each row from its row port.
  - An attached board does not rescan.

## 6. Negative deadbands

- `server.py:2979-2981` refuses with `deadband for <name> must be >= 0`, after the grammar check, and no longer takes `abs()`. `-0` is accepted as zero.
- `tests/webui_js/exportdlg_guards.mjs:182` mirrors it.
- GUARD_URLS adds `v=-0` and `v=-1e-9`; the existing `v=-1` now compares as a refusal.
- Tests: `-1`, `-0.5`, `-1e-9` refused with the exact text; `--1` still gets the grammar message; `-0` and `0` accepted; a positive band still filters.

## 7. Bundle plot files

- `store.py:2220` `plot_streams` groups by (port, sid) through a join to `lines`.
- `server.py:1472-1492` builds one member per (port, sid): `plot_<port>_<sid>.csv`, rows and definitions scoped to that port. `<port>` uses `_FILENAME_UNSAFE`, as `export_filename` does.
- Gap the ruling did not name: ad-hoc points are split per port too, as `plot_<port>_adhoc.csv`, since the long CSV has no port column. Flag if one `plot_adhoc.csv` was wanted.
- Existing tests renamed (`test_session_bundle.py`, `test_daemon_r2026_09_12_bundle.py`); `test_decode_per_port.py:191` now checks each board's file.
- Tests: one sid on two ports gives two files, adhoc per port, old names absent; an unsafe port string is sanitised.

## 8. C-8 failed start

- `daemon.py:283` `_FirstError` keeps the last line of the first ERROR record on `uvicorn.error`: the exception line of a lifespan traceback, or the bind OSError.
- `daemon.py:297` `_serve` catches uvicorn's `SystemExit` and, if `started` was never reached, rewrites the startup log: `mcuscoped <v> failed to start, pid N, exit 3`, then `reason: ...`, then the interpreter report. It then exits 3.
- Tests (`tests/test_rulings_daemon_startlog.py`):
  - Lifespan failure; bind failure (`reason: [Errno ...`).
  - A served start leaves the `started` log.
  - The handler is removed afterwards.
  - The finding's scenario through `main` (a corrupt `db_path`): exit 3, reason names the database, no pid record.

## 9. A-12

- SPEC 3.4 (`The upper bound is exact ...` then `(The lower bound is the weaker half ...)`) reads consistently. No change.

## SPEC / CHANGELOG

- SPEC edits:
  - 3.1: startup-log failure bullet.
  - 3.3: named config refused (both sides; this also replaces the old `mcu daemon start` warning line); warnings once plus `config_warnings`.
  - 3.3.1: Revision bullets, the GET shape, and PUT bodies and returns.
  - 3.4: 409 in the error list; the `/status` shape and a `config_warnings` sentence; the bundle members.
  - 9.2: the `/plot/channels` definition source; the negative deadband refusal.
- CHANGELOG:
  - Changed: named config, warnings once, negative deadband, bundle names.
  - Added: `config_warnings`, `revision`.
  - Fixed: C-3, C-8.
- `tests/test_webui.py:91` stale comment fixed.

## Revert-verify

| Branch | Mutation | Result |
|---|---|---|
| R2a | drop compare in `_read_doc` | fails |
| R2b | GET `revision` = "" | fails |
| R2c | GET `exists` = True | fails |
| R2d | drop 409 mapping | fails (500) |
| R2e | `_write_doc` returns "" | fails |
| R2f x5 | each route passes `None` for revision | all 5 fail |
| R2g x5 | each route omits `revision` from response | all 5 fail |
| R2h | no lock on `/config/ports` | fails (two 200s) |
| R2i | drop `ConfigConflict` from `/config/update` except | fails |
| R3a | GET /config logs | fails |
| R3b | PUT ports load logs | fails |
| R3c | daemon does not log | fails |
| R3d | `/status` field [] | fails |
| R3e | daemon does not hand warnings to app | fails |
| R3g | sink inverted | fails |
| R4 | drop named-config existence check | fails |
| R5a | skip stored-def learning | fails |
| R5b | rescan for attached ports | fails |
| R5c | scan not scoped to port | fails (B_ON for x) |
| R5d | scan unbounded | fails |
| R6a | drop negative refusal | fails |
| R6b | drop JS mirror line | fails (guard parity) |
| R7a | rows not port-scoped | fails |
| R7b | member name without port | fails |
| R7c | port not sanitised | fails |
| R7d | streams not grouped by port | fails |
| R7e | `_plot_export_defs(port=None)` | survives: equivalent, decoders are per port either way; `port=` only narrows the scan |
| R7f | `first_export_line_id(port=None)` | survives: equivalent, an earlier first id only moves defs from primed to in-window, applied in id order |
| R8a | no `SystemExit` catch | fails |
| R8b | keep last error, not first | fails |
| R8c | handler not removed | fails |
| R8d | rewrite even when started | fails |

- A `_warn_sink.reset` in `read_config` survived because it was unobservable (every read sets the sink). It was deleted.
- Not driven: `_serve`'s `if server.started: raise` for a `SystemExit` after a successful start (no path reaches it in tests).
