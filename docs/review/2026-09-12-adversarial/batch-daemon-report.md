# Batch: daemon fixes, 2026-09-12 adversarial round

Tree at `c15b7c6` plus four other batches' in-flight edits. Files touched:
`host/mcuscope/{server,store,config}.py`, `docs/SPEC.md`, and the test files listed below.
`serial_link.py` and `protocol.py` needed no production change (L1 was a missing test, not a
missing guard). `CHANGELOG.md` untouched; its lines are at the end of this file.

## What changed

### Adversarial fixes

| id | file, function | change |
|---|---|---|
| D1 | `store.py` `query_can_frames` | `+cf.can_id IN (...)`: the `+` de-optimisation keeps the planner driving from `can_frames`' primary key, so `ORDER BY cf.line_id` stays index order and `LIMIT` stops it early. The `len(ids) == 1` branch is unchanged and pinned in the same test. |
| D2 | `server.py` `bundle_session` / `_build_bundle` | `export_session_db(id_to=hi)`, the same frozen `hi` every other member uses; the copied session row keeps `end_id` NULL for an open session. `manifest.json` gains `from_id`/`to_id`. |
| D3 | `server.py` `_effective_bounds` | takes `id_to` and resolves `last_ms` through `store._window_floor(last_ms, id_to)` instead of `time.time()`. All three call sites pass their frozen `id_to`. |
| D4 | `server.py` `_csv_cell`, `_csv_lines` | `formula_guard=False` for `raw` **and** for `dir`: both are exempt from the apostrophe prefix, RFC-4180 quoting unchanged. `dir` is the class-wide half the leg did not name: the guard was rewriting every sys/marker row's `-` into `'-`, so the csv column disagreed with the JSON one. |
| D5 | `store.py` `count_lines` | `until_ts` parameter dropped (no caller anywhere). |
| D6 | `store.py` `_window_id_ceiling` | correct now: the newest row's `ts` inside the window answers `MAX(id)` by primary key, otherwise `SELECT MAX(id) FROM (SELECT id FROM lines INDEXED BY idx_lines_ts WHERE ts <= ?)`. See the cost note below. `_window_id_floor` left alone, its assumption stated in SPEC. |
| D7 | `server.py` `_is_enum` -> `_renders_as_label` | refuses a deadband on any channel whose decoded cell is a label: `kind in ("enum", "bit")`. Message: `deadband is numeric, but X renders as a label (an enum or a decoded bit lane)`. |
| D8 | `server.py` `_parse_deadband` | two messages: `deadband needs name=value: X` against `deadband names no exported channel: X`. |
| D9 | test only | the `ts > ?` term of `since_ts` is now pinned on its own. |
| D10 | `server.py` `can_frames` | an empty element is `empty can id in list`. |
| W4 | `server.py`, five `Query` sites | `id_to ... ge=0`. A freeze taken before any line arrived is an empty window, not a 422. |
| C1 | `store.py` `_write_req` | the single write entry point every path converges on (serial rx, `/marker`, tx echo) folds CR/LF inside `raw` to one space. One stored row is one line. |
| C2 | `server.py` `_parse_deadband` | `value.isascii()` and `math.isfinite(band)`, existing "not a number" wording. |
| V10, V18, V19, L1 | tests only | one test each; all four mutations now caught. |

`_window_id_ceiling` cost, measured at 300k rows and written into the docstring: 0.01 ms
when the newest row is inside the window (what "no upper bound" looks like), 0.01 ms on an
empty window, 23 ms for a window covering half the capture (index-only, no `raw` blobs).
`INDEXED BY` is load-bearing: without it SQLite takes `MAX(id)` as a backwards rowid walk,
which is 48 ms on an *empty* window and reads the table btree.

### Improvements

| id | file, function | change |
|---|---|---|
| 2 | `config.py` `_check_unknown`, `_warn_unknown`, `_from_dict` | warns per section and per `[[ports]]` entry, `difflib.get_close_matches` for the hint, known keys from one tuple per section. Warn, never refuse. |
| 4 | `server.py`, five handlers | `if span.unknown: return _bad_request(f"no such session: {session}")` on `/lines`, `/lines/export`, `/can/frames`, `/plot/series`, `/plot/export`. |
| 5 | `store.py` `sweep_tick`, `_reclaim_backlog`, `_RECLAIM_MIN_PAGES` | the freelist drains from the tick whenever it exceeds 256 pages, bounded by `_VACUUM_PAGES`, on the age-sweep path too. `_reclaim_pages` docstring corrected. The two existing `if dropped:` calls stay as the prompt first step after a large delete. |
| 7 | `store.py` `stop`/`_stop_subscribers`, `server.py` `CaptureStopped`, `CaptureWatch.next_batch`, `/wait`, `/assert`, the `/ws` pump | `None` sentinel into every subscriber queue (drop-oldest to make room); the long polls answer `503 {"error": "daemon is shutting down; the wait was cut short"}`. The `/ws` pump returns on the sentinel rather than raising inside a `join`. |
| 9 | `server.py` `plot_export` | the `known`/`unknown` scan is hoisted out of the empty-selection branch and refuses any unknown name. A known name over an empty window is still a header-only 200. |
| 12 | `store.py` `start` | `PRAGMA cache_size=-65536` on the writer connection, comment carrying the measurement. `wal_autocheckpoint` untouched. |

### From the coordinator

1. **Purge racing a bundle.** `bundle_session` holds `store._sweep_lock` from the moment
   `hi` is frozen until `build` returns (the body moved into a nested `_build_bundle`, so
   the diff is one `async with`, not a re-indent of the handler). The lock releases before
   the zip streams, so the download does not block the sweeps. SPEC 3.4's bundle paragraph
   says a purge waits.
2. **Class 22 sweep** over every `float(`/`int(`/`bool(` the export endpoints added in
   `fd5d63d..HEAD` of `server.py`. **7 sites**, all listed:

   | site | verdict |
   |---|---|
   | `can_frames`: `p.parse_hex_int(element)` | complies - the project's hex wire grammar, `ProtocolError` on refusal, range-checked against `CAN_ID_MAX_EXT` on the next line |
   | `_csv_can`: `int(r['ext'])`, `int(r['rtr'])` (2 sites) | exempt - SQLite INTEGER columns of the daemon's own `can_frames` table, never external text |
   | `_parse_deadband`: `abs(float(value))` | **violates** - C2, fixed with `isascii()` + `isfinite` |
   | `_renders_as_label`: `bool(meta and ...)` | exempt - truthiness of an internal dict, not a coercion |
   | `_decode_map`: `int(v)` over `meta["labels"]` | complies - already an `int`: `protocol._parse_enum_labels` gates the token on `_ENUM_VAL_RE` and `MAX_DECIMAL_DIGITS` before `int(val_s, 10)` |
   | `_render`: `int(row["value"])` (2 sites) | exempt - a REAL from `plot_points`, finite by `parse_plot_value`'s post-conversion `isfinite` check; `int()` of a finite float cannot raise |

## Revert verification

27 mutations, each applied alone to the current file (which carries the other fixes),
restored from a `cp` copy afterwards. Driver and raw results:
`/tmp/rev-2026-09-12/fix-daemon/verify.py`, `verify-results.txt`.

**27 applied, 27 caught, 0 survivors.**

D1, D2 (span), D2 (manifest), D3, D4 (raw), D4 (dir), D5, D6, D7, D8, D9, D10, W4, C1, C2,
V10, V18, V19, L1, improvement 2, 4, 5 (reclaim), 5 (the `_VACUUM_PAGES` bound), 7, 9, 12,
and the bundle's `_sweep_lock` - each reverted, each named test failed, each restored.

## D1 timing, re-run

300k frames, two-id list, the statement the store issues, 100-row page:

| form | plan | best of 3 |
|---|---|---|
| `cf.can_id IN (?,?)` | `SEARCH cf USING INDEX idx_can_id_line`, `USE TEMP B-TREE FOR ORDER BY` | **265.85 ms** |
| `+cf.can_id IN (?,?)` | `SCAN cf`, `SEARCH l USING INTEGER PRIMARY KEY` | **0.26 ms** |

The leg measured 133 ms -> 2.21 ms on its own probe; same shape, same cause.

## Gates

- `uv run python -m ruff check .` - **All checks passed** (whole `host/` tree).
- `grep -nP '[\x{2013}\x{2014}]'` over every file touched - **empty**.
- Gate suite (my files plus `test_e2e`, `test_sessions`, `test_hardening`, `test_reconnect`):
  **438 passed, 1 skipped, 1 failed** in 99 s. The one failure is not mine, see below.
- Whole suite (`pytest -q --ignore=tests/test_webui_js.py`): **1557 passed, 1 skipped,
  1 failed** in 297 s - the same single failure. No `test_cli*.py` breakage.

## Not done / owed

- **`tests/test_port_health.py::test_to_is_one_until_ts_on_the_query_itself` fails, and the
  cause is the CLI batch, not this one.** It asserts that `mcu lines --to` issues exactly
  one request; the CLI batch's C3 fix adds a `GET /status` version probe ahead of it
  (`cli.py`, "One extra GET /status"), so `seen` is now `[{}, {...until_ts...}]`. The file
  is mine but the contract is theirs, so I left the test alone rather than encode a
  contract I cannot confirm is final. Orchestrator to reconcile: either the CLI batch
  updates the assertion to "one *query*, plus the version probe", or the probe is cached.
- **Three test files outside my list were edited, all because this batch's contract change
  makes their old assertion false.** Each was a pin on the behaviour improvement 4 and 9
  deliberately replace, and SPEC changed with them:
  - `tests/test_sessions.py::test_unknown_session_matches_nothing` ->
    `test_unknown_session_is_refused_not_answered_empty` (now drives all five endpoints).
  - `tests/test_assert.py::test_unknown_session_is_empty_for_lines_and_a_400_for_assert` ->
    `test_unknown_session_is_a_400_on_lines_and_on_assert`.
  - `tests/test_review_r2_server.py::test_plot_export_still_exports_when_one_name_of_several_is_unknown`
    -> `test_plot_export_refuses_one_unknown_name_among_several` (this one *is* my file).
- **`_window_id_floor` is still the weaker half.** D6 fixed the ceiling only, as scoped: a
  row stamped before a backwards clock step can still fall outside a `since_ts`/`last_ms`
  window its time is inside. Stated in SPEC's `until_ts` paragraph rather than fixed, since
  the floor has no cheap exact form either and nothing drives it today.
- **The in-memory-capture branch of the new exports** (`store.py` `open_lines_export` /
  `open_can_export`, the leg's Q2) is untouched: it materialises the whole selection on the
  loop and no test in the repo drives it. Out of this batch's scope, worth a round.
- **`--deadband` client-side validation** stays with the CLI batch; the daemon now refuses
  the values either way.

## SPEC changes (docs/SPEC.md)

- 3.3: unknown keys are warned about by name with a spelling hint, never refused.
- 3.4 `/can/frames`: an empty `?id=` element is `empty can id in list`.
- 3.4 `/lines/export`: the csv is faithful for `raw` and `dir`; the formula guard applies to
  the device-declared cells only.
- 3.4 `/wait`, `/assert`: the shutdown 503 and why it is not a 200 timeout.
- 3.4 `/marker`: a stored row is one line, CR/LF folded to a space.
- 3.4 bundle: `from_id`/`to_id` in the manifest, one span for every member, a purge waits.
- 3.4 `session=`: `/lines/export` added to the list; an unresolvable ref is a 400, an empty
  session is still a 200.
- 3.4 `until_ts`: exact over the rows whatever the wall clock did, with the floor's
  remaining assumption stated.
- 3.4 `/plot/export`: refuses **every** unknown name; a name with no points in the window is
  not unknown.
- 9.2 deadband: the four faults it refuses, labels (enum and bit lane) included.

## CHANGELOG lines (not applied - CHANGELOG.md is owned elsewhere)

```
### Fixed
- `/can/frames?id=A,B`: a multi-element id list no longer sorts every match through a temp
  b-tree (266 ms against 0.26 ms at 300k frames; the paged CSV export paid it per page).
- Session bundle: every member covers one frozen id span, including a session still
  running, and `manifest.json` records it as `from_id`/`to_id`. A purge or retention sweep
  of that span now waits for a bundle in progress instead of deleting rows mid-build.
- Export filenames: `last_ms` is anchored where the rows are, not at the request, so a
  window can no longer be named backwards.
- `/lines/export?format=csv`: `raw` and `dir` are carried through unchanged; the
  spreadsheet-formula guard applies only to device-declared cells.
- `until_ts` no longer drops the newest rows after a backwards clock step.
- A deadband on a decoded bit lane is refused rather than silently ignored, and a value of
  `inf`, `nan` or another script's digits is refused instead of parsed.
- A marker or captured line holding CR/LF is stored as one row and exports as one line.
- `id_to=0` (a surface paused before its first line) is an empty window, not a 422.
- A `?id=` list with an empty element says so.

### Changed
- An unresolvable `session=` is a 400 on `/lines`, `/lines/export`, `/can/frames`,
  `/plot/series` and `/plot/export`, as it already was on `/assert`; a session that exists
  and holds no lines is still an empty 200.
- `/plot/export` refuses every unknown channel name, not only an entirely unknown selection.
- `/wait` and `/assert` answer 503 when the daemon stops under them, instead of a generic
  500 after the graceful-shutdown cap.
- An unrecognised config key or section is now warned about by name, with a spelling
  suggestion; the value is still ignored and the load still succeeds.
- Freed database pages are handed back on every maintenance tick, age-based retention
  included, so a capture file no longer only grows.
- The capture writer gets a 64 MB page cache.
```
