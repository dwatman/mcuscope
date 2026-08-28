# Fix batch A2 (server.py, one cli.py call site, SPEC)

HEAD at start: `0b5eed9 Review round 2, wave 1: the HIGH-carrying fixes across firmware, CLI, web UI, sim` (as expected).
Files changed: `host/mcuscope/server.py`, one call site in `host/mcuscope/cli.py` (`session_delete`), new `host/tests/test_review_r2_server.py`, `docs/SPEC.md` (3.1 guard caveat, 3.3.1 bounds rule, 3.4 purge/export/plot-export/sessions notes).
Nothing committed. `host/mcuscope/store.py` was NOT touched (batch A1 owns it and was editing it live).

## Items

### 1. CD1: every unbounded integer parameter now carries a ceiling

New constants in `server.py`: `MAX_LINE_ID = 2**63 - 1` (SQLite INTEGER), `MAX_MS = 10**15`, `MAX_DECIMATE = 10**9`.
Enumerated mechanically from every handler signature and body model, not from the reported seven. Bounded:

| Site | Parameters |
|---|---|
| `GET /lines` | `since_id`, `last_ms`, `id_to` |
| `GET /can/frames` | `since_id`, `last_ms`, `id_to` |
| `GET /plot/series` | `since_id`, `last_ms`, `id_to`, `decimate` |
| `GET /plot/export` | `last_ms`, `id_to` |
| `DELETE /sessions/{session_id}` | the path param (`fastapi.Path`, imported as `PathParam` because `Path` here is pathlib's) |
| `PurgeBody` | `id_from`, `id_to` |
| `AssertBody` | `last_ms` |

Left alone deliberately, with reasons:
- `limit` on all four list endpoints: SPEC 3.3.1/3.4 require **clamping** to 0..1000, and a huge int is safe there (`min()` of a Python int, no bind, no float). A test pins that it stays clamped, so the CD1 work cannot quietly turn it into a refusal.
- No `ge=` was added to `since_id`/`decimate`: the lower ends are already clamped in the store, and a new refusal there would be an unrequested behaviour change.
- Already bounded before this round: `baud`, `timeout_ms`, `min_window_ms`, `port`, `retention_days`, `max_db_bytes`, `min_sessions`, list/string lengths.

### 2. Measurement F1, REST half: a purge cutoff in the future

`POST /purge` refuses `before_ts` more than `PURGE_FUTURE_SKEW_S = 60` s ahead of the daemon clock with a 400 naming `all: true`. Inside the slack it is accepted unchanged, so a client clock a few seconds fast still purges.

### 3. CD3: session export temp handling

- `mkstemp(dir=_export_tmp_dir(request))`, the directory holding the capture DB (falls back to the system temp dir only when that parent does not exist, i.e. an in-memory capture).
- The early `os.unlink` is gone: a zero-length file is a valid empty SQLite database, so nothing needed the name freed, and unlinking left a known unclaimed path.
- `BackgroundTask(_unlink_later, ...)` replaced by `_TempFileResponse(FileResponse)`, whose `__call__` unlinks in a `finally`, so a client that disconnects mid-download no longer leaks the copy. `starlette.background` import dropped.

### 4. CD4: same-origin guard wording

`_SameOriginGuard.__doc__` and SPEC 3.1 (the "blocks cross-site CSRF..." line) gain the caveat: a no-cors subresource GET carries no `Origin`, so a visited page can trigger a GET (not read it), and the bound on that is cheap side-effect-free GETs, not this guard. No behaviour change.

### 5. Measurement F4: `/plot/export` and unknown names

When the selection counts **0** rows, the handler checks the requested names against `/plot/channels` and answers 400 naming the unknown ones only if **none** of them exists. A known name plus an unknown one exports as before. The channel scan runs only on the already-empty path, so nothing that selects rows pays for it.

### 6. Routed from C1 (F12): `GET /sessions?name=`

`name` is a session ref, resolved by `store.resolve_session` (numeric id first, then newest by name) - one indexed lookup, with `lines` computed via `session_span` + `count_lines_safe` so the row keeps the shape `session delete` prints. Empty list for an unknown name.
`cli.py:session_delete` now sends `name=` instead of paging `limit=1000`, and **re-checks the returned row's id/name** before deleting: a daemon too old to know the parameter answers the default page, whose first row is the newest session, and deleting that is not what was asked for. The "no such session: X" text is unchanged.
Plan-checked in a test: `WHERE name = ? ORDER BY id DESC LIMIT 1` uses `idx_sessions_name`.

### 7. Owed tests (no source change needed)

- `send_mode: "raw"` on both `/wait` and `/assert`, using the `>SEQ CMD` wire form.
- The regex match budget: a planted 400-character line plus `(a|a)+b` -> 400 "budget".
- The four `PUT /config/*` save-failure arms: one parametrised test pointing `app.state.config_path` at a path *under a file*, so the save raises OSError on any platform -> 500 "config save failed".
- `/shutdown` 403 for a non-loopback client: **already covered** by `tests/test_e2e.py:84 test_shutdown_refused_from_non_loopback` (the exact ASGITransport pattern the disposition asked for). The measurement leg's "no test" claim is wrong on this one; nothing added, a comment in the new file records it.

## Revert verification

Script `/tmp/claude-1000/review-r2/rv_a2.py` (mutate shipped source, run the test, restore, re-run):

| fix reverted | test | mutated | restored |
|---|---|---|---|
| all `le=` bounds stripped | test_out_of_range_integer_params_are_refused_not_a_500 | 1 failed | 1 passed |
| purge future-ts guard disabled | test_purge_before_ts_in_the_future_is_refused | 1 failed | 1 passed |
| mkstemp back in the system temp dir | test_session_export_builds_its_temp_copy_beside_the_capture | 1 failed | 1 passed |
| `_TempFileResponse` unlink removed | test_a_disconnected_download_still_removes_the_temp_copy | 1 failed | 1 passed |
| no-cors caveat removed from the docstring | test_the_same_origin_guard_documents_the_no_cors_gap | 1 failed | 1 passed |
| unknown-name refusal disabled | test_plot_export_refuses_when_no_requested_name_exists | 1 failed | 1 passed |
| `name=` filter disabled | test_sessions_name_filter_finds_a_session_past_the_default_page | 1 failed | 1 passed |
| `name=` filter disabled | test_cli_session_delete_resolves_a_name_past_the_default_page | 1 failed | 1 passed |

## Gates

- `ruff check mcuscope/server.py mcuscope/cli.py tests/test_review_r2_server.py`: clean.
- `pytest tests/test_review_r2_server.py tests/test_e2e.py tests/test_hardening.py -q`: **138 passed** (41 s).
- `pytest tests/test_cli.py -k session -q`: 9 passed (the changed CLI path).
- No em dashes or en dashes in any file touched.

## For the orchestrator

- The `name=` filter had to live in `server.py` alone: `SESSION_LIST_SQL` and `list_sessions` are in `store.py`, which batch A1 was editing concurrently (its line numbers moved under me mid-batch). The handler composes existing public store methods (`resolve_session`, `session_span`, `count_lines_safe`) instead, which is one indexed row lookup plus the same count the list does. If a later round wants the filter inside `list_sessions`, the handler collapses to one call.
- `/sessions?name=` deliberately accepts an id as well as a name, matching `session=` everywhere else and keeping `mcu session delete <id>` working; SPEC says so.
- Two of the new tests need plot data and use `make_stack(["--plot"])` rather than the shared `stack` fixture.
- `tests/test_review_r2_server.py` imports `run_mcu` from `tests.test_cli` for the two CLI-path tests.
