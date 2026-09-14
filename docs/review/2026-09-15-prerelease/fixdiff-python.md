# Fix-round diff review: Python (`fdd30a2..HEAD`, `host/mcuscope/*.py`, `host/tests/*.py`)

Scratch probes: `~/tt-data/prerelease-2026-09-15/fixdiff-py/` (`test_fixdiff_window.py`, `test_fixdiff_pages.py`, `test_fixdiff_cli.py`, the diffs `core.diff`, `cli.diff`, `link.diff`).
Run from `host/`: `uv run python -m pytest -s -q -p no:cacheprovider --rootdir=. ~/tt-data/prerelease-2026-09-15/fixdiff-py/<file>`; each probe prints its observation to stderr.
No daemon was started; every probe is a TestClient app on a scratch file DB or a canned CLI transport.

Totals: 0 HIGH, 2 MED, 6 LOW.

## Findings

### FP-1 MED: `mcu lines` / `mcu log export` with `--session` and `--last-ms` now exit 1 naming `since_ts`, where the daemon answers the session's tail

- Where: `cli.py:776` and `cli.py:1652` (`_absolute_window` turns `--last-ms` into `since_ts = now - N`), meeting the new A-10 refusal at `server.py:2670`.
- Defect: SPEC 3.4 (lines 846-848) defines `last_ms` with an ended session as the tail of that session, and SPEC 4 (line 1089) says the bounds intersect alike on `lines`, `log export`, `can dump` and `plot export`.
  - `can dump` and `plot export` send `last_ms` raw and get the tail; `lines` and `log export` send a now-anchored `since_ts`.
  - Before the fix that divergence was a silent empty answer; A-10 turns it into a 400 whose text names a parameter the user never passed.
- Failure: session `old` ended 1.2 s ago over 3 lines.
  - `mcu lines --session old --last-ms 1000` and `mcu log export --session old --last-ms 1000`: rc 1, `error: the end of session old is before since_ts`.
  - `GET /lines?session=old&last_ms=1000`: 200 with the session's last rows.
- DRIVEN: `test_fixdiff_window.py::test_probe_cli_lines_session_last_ms`.
- Class: 19 (two engines judging one bound) with 67's shape (a client-side conversion changes a relative parameter's meaning).
- Fix: with `--session`, send `last_ms` raw and pin paging with `id_to` from the first page instead of converting it to `since_ts`.

### FP-2 MED: A-1 is fixed per request, but the CLI's paged walks still re-derive the `until_ts` ceiling on every page

- Where: `server.py:2671-2672` (`_resolve_window` walks the ceiling once per request), reached once per 1000-row page by `cli.py` `_iter_pages_asc` (`log export --decode/--changes/--names`) and `_fetch_lines` (`mcu lines --limit N` above 1000).
- Defect: A-1 named `mcu ... --to` among the affected surfaces and asked that every page reuse one ceiling; only `/lines/export`, `/can/frames?format=csv` and `/plot/export` do.
  - The walk is now off the loop, so the stall is gone, but the export stays quadratic.
- Failure: 20 000 rows, `--to` at 90 % of the capture.
  - `mcu log export --to T -o a.txt`: 1 ceiling walk.
  - `mcu log export --to T --decode -o b.txt`: 19 walks.
  - `mcu lines --to T --limit 15000`: 15 walks.
  - At leg A's 1M-row measurement (161 ms per walk at 90 %), a decoded export is about 1000 pages times 0.16 s of ceiling walks alone.
- DRIVEN (walk count, spy on `Store._window_id_ceiling`): `test_fixdiff_pages.py`. Cost REASONED from `A-daemon-api.md` A-1.
- Class: 44 (a bound re-derived on every page of a paged walk), with 1 and 20.
- Fix: skip `id_ceiling_safe` when the request carries an `id_to` at or below the ceiling's row, and have both CLI pagers pin `id_to` from their first page.

### FP-3 LOW: an ended session whose lines were purged refuses `last_ms` with a false "the end of session S is before the last_ms window"

- Where: `server.py:2661` calls `store._window_floor(last_ms, bound)`, which falls back to `time.time()` when no row sits at or below the bound (`store.py:1492`); the crossing check at `server.py:2670` then compares that now-anchored floor with the session's `ended_ts`.
- Defect: sessions outlive retention (`store.py` deletes a session only on request), so every session older than `retention_days` has no rows below its `end_id`.
  - SPEC 3.4 anchors the window at the session's end, so it cannot start after that end; before the fix the answer was an empty 200 (a 0-line verdict on `/assert`).
- Failure: session `old` ended 1.2 s ago, `DELETE FROM lines`, then `last_ms=1000`:
  - `/lines`, `/lines/export`, `/can/frames` and `POST /assert {session, last_ms}`: 400 `the end of session old is before the last_ms window`.
  - Same request before the delete: 200 with the tail.
- DRIVEN: `test_fixdiff_window.py::test_probe_purged_session_last_ms`.
- Class: 67 (the anchor silently becomes now when the bound names no row).
- Fix: in `_resolve_window`, anchor a session-bounded `last_ms` at `row["ended_ts"]` when `_window_floor` finds no row, or skip the crossing check for a floor that fell back to now.

### FP-4 LOW: the A-10 session-start refusal is reachable without a clock step; the test fixture called unreachable (class 63) is reachable

- Where: `server.py:2670` compares `until_ts` with `started_ts`, while rows are scoped by id. `serial_link.py:575-587` stamps a burst on the reader thread and posts it with `call_soon_threadsafe`; `store.py:1255` reads `time.time()` for `started_ts` after `drain_writes`.
- Defect: a burst stamped before `start_session`'s clock read but ingested after its drain gets an id inside the session and a `ts` before `started_ts`.
  - `fix-daemon-core.md` rewrote `test_until_ts_intersects_a_session_rather_than_replacing_it` as "a fixture the producer cannot reach"; the triage decision on A-10 names only a backwards clock step.
- Failure: stamp taken, `start_session("run")`, row added with that stamp. Row id 5 > `start_id` 4 and its ts < `started_ts`; `GET /lines?session=run&until_ts=<that stamp>` -> 400 `until_ts is before the start of session run`, although the window holds the row.
- DRIVEN (refusal): `test_fixdiff_window.py::test_probe_burst_stamped_before_session_start`. REASONED (producer ordering): the gap is one loop hop, milliseconds under load.
- Class: 63 (the batch's class-63 verdict on that fixture is wrong).
- Fix: fold into the owner's A-10 decision: refuse only ts-against-ts pairs, or widen the session side by the ingest latency.

### FP-5 LOW: `session export --bundle -o NAME` is refused when `NAME` is an existing directory, which used to write `NAME.zip`

- Where: `cli.py:1347`: `os.path.isdir(out_file)` is tested before the `.zip` suffix is appended.
- Defect: B-10 was the trailing separator (`-o adir/` wrote `adir/.zip`). An extensionless `-o run-3` beside a `run-3/` directory (last week's bundle, unzipped) named a sibling file, `run-3.zip`, and worked.
- Failure: `mcu session export run-3 --bundle -o run-3` with `run-3/` present: rc 1, `-o run-3 is a directory; give a file path`, no request made. `test_session_export_refuses_a_directory_target[existing---bundle]` pins the regression.
- DRIVEN (new behaviour): `test_fixdiff_cli.py::test_probe_bundle_into_a_name_that_is_a_directory`. REASONED (old behaviour, from the removed lines of the diff).
- Class: none (B-10 sibling).
- Fix: refuse the trailing separator up front; test `isdir` on the final path, after the suffix.

### FP-6 LOW: a command with `--from`/`--to` and a gated option sends two `GET /status`

- Where: `cli.py:644` (`_clock_bounds` gates `--from/--to`) and `cli.py:2173` (`plot export` gate), likewise `can dump --csv` (`client.require_daemon("--csv")` then `_clock_bounds`).
- Defect: `Client.require_daemon` documents "One GET /status, only on these paths"; the two gates each fetch it.
- Failure: `plot export --names v --from 10:00 --decode -o p.csv`, `-p board plot export ... --from 10:00` and `can dump --csv --from 10:00 -o c.csv` each request `/status` twice before the export.
- DRIVEN: `test_fixdiff_cli.py::test_probe_status_requests_per_gated_command`.
- Class: none.
- Fix: gate once per command, passing every flag to one `require_daemon` call (or cache the version on the `Client`).

### FP-7 LOW: an export dying mid-stream through a symlinked `-o` leaves the partial body in the link's target

- Where: `cli_output.py:149-157` (`remove_partial` keeps anything `lstat` does not call a regular file), used by `_OutFile.discard` and `Client.download`.
- Defect: class 49's invariant is that a short export does not stay where the user asked for it. Keeping the link is right, but its target was truncated and now holds the partial bytes, which read like a whole export.
- Failure: `latest.csv -> target.csv` holding a complete earlier export; `mcu log export --csv -o latest.csv` with the body dying after `id,ts\n1,2\n`: rc 3, link kept, `target.csv` now `id,ts\n1,2\n`. `test_a_stream_dying_mid_export_removes_a_file_but_not_a_link` never asserts on the target.
- DRIVEN: `test_fixdiff_cli.py::test_probe_symlink_target_after_mid_stream_death`.
- Class: 49 (with 69).
- Fix: in `remove_partial`, truncate a symlink's regular-file target (`os.truncate(path, 0)`) instead of leaving the partial bytes; leave FIFOs and devices alone.

### FP-8 LOW: `mcu session export NAME` now fails with "no such session" against a pre-0.3.0 daemon for any session past the newest 50

- Where: `cli.py:1368` `_resolve_session`, now on the export path (B-9).
- Defect: v0.2.0's `GET /sessions` has no `name=` and answers its default 50-row page; the identity re-check then finds nothing. Before B-9, `/sessions/{name}/export` resolved server-side on that daemon for every session.
- Failure: 0.2.0 daemon, 60 sessions, `mcu session export <oldest>` -> `no such session: <oldest>`, rc 1 (a false negative). `session delete` already had this limit; export did not.
- REASONED: `git show v0.2.0:host/mcuscope/server.py` `list_sessions(request, limit: int = 50)`, `v0.3.0` adds `name`.
- Class: 53 (a parameter the older peer does not declare).
- Fix: when the lookup misses and `older_daemon` reports a version below 0.3.0, fall back to the path form for names without `/`, `?` or `#`.

## Checked and clean

- A-6 / C-1 / C-2 / A-7: every `subscribe` caller (2) refuses after close; `/wait` and live `/assert` hand out rows ahead of the sentinel, then raise; a final drain past the deadline answering `timeout` covers a finished window, so it is honest. `handle_exit` always runs with a running loop (uvicorn installs it only on the main thread inside `serve()`; the Windows `/shutdown` path raises from a loop callback). `tail -f` maps the new 1001 to exit 3 through its generic close arm.
- A-2 / `count_lines`: passing `floor_ts` instead of `last_ms` lets `count_lines` drop an `id_to` equal to `max_id`; that is only a plan change, since the floor no longer depends on `id_to`.
- A-3: `decs` is never None where `judged` is built (inside `if decode and first_id is not None`).
- `_window_terms` without the fold: every caller passing `until_ts` with its own `id_to` keeps `ts <= until_ts`, so only the plan changes.
- C-4 `adopt`: `PlotDecoder` holds only `_defs`; pop-and-insert keeps the wire's declaration order.
- C-5 / C-6: `_close_link_locked` is the only place `_link` is nulled (reader `finally` and `stop()`), so the streak reset cannot be skipped.
- G-1: v0.3.0 and v0.4.0 answer 503 only for the subscriber cap, never for shutdown, so the prefix rule maps an older daemon correctly. No 404 is emitted by v0.2.0, v0.3.0 or HEAD's handlers, so B-18's route inference holds.
- B-3 attach gate: `--eol` defaults to `lf` there, so a plain attach pays no `/status`.

## The two questions

1. Least confident, and rechecked:
   - The first FP-3 probe backdated `started_ts`/`ended_ts` and reported a 400 even with rows present. That was my own class-63 fixture: `stop_session` writes a marker stamped now, so a backdated `ended_ts` sits before the session's newest row. Rerun with real stamps: rows present answer 200; only the purged form (FP-3) and the stamp-before-start form (FP-4) refuse.
   - FP-2 is a call count, not a timing; the cost per walk is leg A's number, not remeasured.
   - FP-5 and FP-8 old behaviour is read from the diff and the v0.2.0 source, not run.
   - Nothing was run on Windows.
2. What should have been checked:
   - Every consumer of a new refusal, not only the endpoints: the CLI's own conversions (`_absolute_window`) and pagers (`_iter_pages_asc`, `_fetch_lines`) are clients of `_resolve_window` and were outside every batch's sweep.
   - A fallback inside a helper that a new check now trusts: `_window_floor`'s `time.time()` fallback was harmless while only filenames used it.
   - The producer's real ordering before calling a fixture unreachable: the reader stamps before the loop ingests.
