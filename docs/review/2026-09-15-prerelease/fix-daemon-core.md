# Fix batch: daemon core (`store.py`, `server.py`)

Scratch: `~/tt-data/prerelease-2026-09-15/fix-daemon-core/` (`revert.py`, `revert.out`, `revert_verbose.out`, `measure.py`, `measure.out`, `run2.log`).
Gates: 452 passed over the new files, every owned test file, and `test_assert`, `test_hardening`, `test_plot`, `test_sessions`, `test_store_*`, `test_review_r2_store`, `test_decode_per_port`, `test_wait_repeat`, `test_cli_export`; ruff clean.
New test files: `host/tests/test_prerelease_daemon_core_{windows,shutdown,plot,store}.py` (W, SH, PL, ST below).

## Fixed

Every revert below re-introduced the defect from a copy, ran the credited tests, and restored; all 28 KILLED, and the risky ones were re-run to confirm each failed on its assertion, not a syntax error or hang.

| Id | Change | Test | Revert |
|---|---|---|---|
| A-1 | `_resolve_window` folds `id_ceiling_safe(until_ts)` (offloaded) into `id_to` once; `_window_terms` adds the ceiling only when `id_to` is None | W `test_every_page_of_an_export_reuses_one_until_ts_ceiling`, `test_the_folded_ceiling_keeps_every_row_after_a_clock_step` | KILLED (guard removed; fold removed) |
| A-1 | `query_lines_safe` offloads whenever `until_ts` is given | W `test_lines_with_until_ts_runs_off_the_loop` | KILLED |
| A-2 | `last_ms` resolved to `floor_ts` against the caller's bound before the freeze; `floor_ts` threaded through `query_lines`, `count_lines`, `query_can_frames`, the plot export reads; retrospective `/assert` had the same defect (sweep) and uses the helper | W `test_last_ms_on_a_quiet_capture_counts_back_from_now_on_every_export` | KILLED |
| A-3 | label refusal judged over primed decoders plus one fresh decoder per distinct in-window `!pd` (`_def_decoders`) | PL `..._enum_first_declared_inside_the_window_is_refused`, `..._redefined_as_an_enum_inside_the_window_is_accepted` | KILLED (primed only; defs learned into primed) |
| A-4 | bundle re-reads the session by id under `_sweep_lock` (400 if gone) and takes `lo`/`hi` there | W `test_a_bundle_queued_behind_its_sessions_deletion_is_refused`, `test_an_open_sessions_bundle_takes_its_span_after_the_wait` | KILLED (both) |
| A-6 | `CaptureWatch.next_batch` returns the rows ahead of the sentinel, raises on the next call; `/ws` pump sends them, then returns | SH `test_a_match_queued_ahead_of_the_sentinel_is_still_judged[/wait,/assert]`, `test_the_websocket_sends_the_rows_ahead_of_the_sentinel_then_closes` | KILLED (both) |
| A-8, B-16 | deadband value via `p.parse_plot_value` (SPEC 2.5 grammar, finite); repeated name is `deadband names <n> twice`; `-5` still its magnitude | PL `test_a_deadband_value_outside_the_grammar_is_refused`, `..._accepted`, `..._naming_one_channel_twice_is_refused` | KILLED (both) |
| A-9 | `ge=0` on the five `last_ms` queries (0 stays legal: a paused span can round to 0, the W4 shape) | W `test_a_negative_last_ms_is_refused` | KILLED |
| A-10 | `_resolve_window` refuses a crossing pair: `until_ts is before the start of session run`, `the end of session run is before since_ts`, `until_ts is before the last_ms window` (all four window endpoints) | W `test_a_window_crossing_its_session_is_refused_by_name` | KILLED |
| A-11 | `names lists <n> twice` (400, before `format`) | PL `test_a_name_listed_twice_is_refused[long,wide]` | KILLED |
| C-1 | `stop_subscribers` sets `_subscribers_closed`; `_broadcast_batch` returns when set | SH `test_the_sentinel_survives_rows_committed_after_it` | KILLED |
| C-2 | `subscribe` raises `StoreError(SUBSCRIBERS_CLOSED_MSG)` = `daemon is shutting down; no new watch can start`; `/wait` and `/assert` answer it 503 as before; `/ws` closes 1001 with that reason (1013 kept for the cap) | SH `test_a_subscriber_after_the_sentinel_is_refused_as_shutdown`, `test_wait_and_assert_arriving_after_the_sentinel_answer_503`, `test_a_websocket_after_the_sentinel_closes_as_going_away` | KILLED (flag check; 1001 reason) |
| E-13 | stale `ConfigPortEntry.eol` comment cut to `# same` | none (comment) | n/a |
| D-5 | `GET /status` gains `now` (daemon `time.time()`) | W `test_status_carries_the_daemon_clock` | KILLED |
| F-1 | both shutdown-503 tests poll `len(store._subscribers)` before firing | existing tests | KILLED (poll removed: late-subscriber message) |
| F-2 | clock assertion deleted; build held until the purge queues on `_sweep_lock` | existing test | KILLED (bundle without the lock: `0 == 7`) |
| F-5 | `hold_temp_file_body` gates the first body message until the temp file is listed (`test_session_bundle.py`, used by `test_review_r2_server.py`) | existing tests | not deterministic (a race removed, not a branch) |
| F-7 | empty-session test asserts empty rows, frames, points, header-only CSV | existing test | KILLED (session lower bound dropped) |
| F-10 | bare `\r` fold | ST `test_a_bare_carriage_return_is_folded_too` | KILLED (P31) |
| F-11 | ceiling walk branch with a row past the cutoff after a clock step | ST `test_the_ceiling_walk_names_the_highest_id_not_the_newest_ts` | KILLED (P33) |
| F-27 | covered by the A-6 WS test | SH as A-6 | KILLED (P13) |
| F-28 | newest pre-window definition labels a shared lane | PL `test_the_newest_definition_before_the_window_labels_a_shared_lane` | KILLED (P29) |
| F-29 | lane first declared inside the window | PL `test_a_lane_first_declared_inside_the_window_is_qualified_in_the_header` | KILLED (P73) |
| F-30 | `plot_ports_safe` driven alone after a delete (not dead: reachable by a purge between the handler's two awaits) | ST `test_plot_ports_rebuilds_after_a_delete_on_its_own` | KILLED (P38) |

A-1 re-measured on a fresh 1M-row capture (scratch daemon on 18710, stopped by PID):

- `/lines/export` with `until_ts` at 30 % of the capture: 2.15 s, against 1.93 s for the `id_to` equivalent. Leg A measured 34.58 s against 3.01 s.
- `/lines?until_ts` at 99.9 %: `/version` latency 1.9 ms, where leg A saw 107 ms.
  - The poller shares a process with the client, as leg A's first run did.

Existing test changed for a fixture the producer cannot reach (class 63): `test_export_lines_can.py::test_until_ts_intersects_a_session_rather_than_replacing_it` stamped rows in 2023 inside a session stamped now; it now uses wall-clock stamps and spins past the bound.

## Not done / owed

- **Failing now: `test_webui_js.py::test_export_guard_double_agrees_with_the_daemon`** (6 URLs), fix in `host/tests/webui_js/exportdlg_guards.mjs`:
  - After `if (!names.length) return "names is required";`: `const twice = names.find((n, i) => names.indexOf(n) < i); if (twice !== undefined) return \`names lists ${twice} twice\`;`
  - Replace `D`, `PY_FINITE`, `deadbandNumber` with `const deadbandNumber = (v) => /^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$/.test(v) && Number.isFinite(Number(v));` (comment: the SPEC 2.5 value grammar, finite).
  - In the deadband loop, after the `no exported channel` check and before the value check: a `seen` set, `if (seen.has(name)) return \`deadband names ${name} twice\`;`.
  - `last_ms` specs (lines, CAN, PLOT): `{ ge: 0n, le: MAX_MS }`.
  - `GUARD_URLS` in `test_webui_js.py`: add `_DB + "v=1,v=2"`, `"/plot/export?names=v,v"`, `"/lines/export?last_ms=-1"`.
- **SPEC 3.4**:
  - Errors list: `503`: "the capture's subscriber cap is reached, or the daemon is shutting down".
  - `/status`: add `now`: "the daemon's wall clock (unix seconds, the clock row `ts` is stamped with); a client measuring a row's age uses it rather than its own clock".
  - `last_ms` paragraph: after "with no upper bound it counts back from now", add "an export freezing its own upper end at the newest line is not an upper bound for this".
  - After the `until_ts is before since_ts` sentence: "A window whose effective `from` is after its `to` is a 400 naming the pair, on `/lines`, `/lines/export`, `/can/frames` and `/plot/export`: `until_ts is before the start of session <name>`, `the end of session <name> is before since_ts`, `until_ts is before the last_ms window`."
  - `last_ms` below 0 is a 422 on `/lines`, `/lines/export`, `/can/frames`, `/plot/series`, `/plot/export`; 0 is a window.
  - `/wait`: "A call arriving after shutdown began answers `503 {"error": "daemon is shutting down; no new watch can start"}`; a match committed before shutdown began is still answered `match`."
  - `/ws`: "**close 1001** with reason `daemon is shutting down; no new watch can start` for a handshake after shutdown began; rows queued before shutdown are sent, then the socket closes 1000."
- **SPEC 9.2**:
  - Deadband 400 list: "a value outside the SPEC 2.5 `<value>` grammar or not finite (`inf`, `nan`, other scripts' digits, `+5`, `1_0`, padding, `.5`, `5.`)", and "a name given twice (`deadband names <name> twice`)".
  - Label refusal: "judged over every definition of the channel, the ones inside the window included".
  - `names`: "a name listed twice is a 400 (`names lists <name> twice`)".
- **CHANGELOG**: every row above that changes behaviour. That is A-1 (speed), A-2, A-3, A-4, A-6, A-8/B-16 (`+5` is now refused), A-9, A-10, A-11, C-1/C-2 (new 503 message, WS 1001) and `/status now`.
- **CLI batch**: `mcu tail -f` should map WS close 1001 whose reason starts `daemon is shutting down` to exit 3, as 1013 is (`cli.py:1068-1076`, test beside `test_cli.py:2303`). The `/wait` and `/assert` late-subscriber 503 carries the prefix, so the prefix rule already covers it. AI_GUIDE only if it lists WS close codes.
- **Owner, next to A-12**: the A-10 refusal compares `since_ts`/`until_ts` with the session's wall-clock `started_ts`/`ended_ts`, while rows are scoped by id. After a backwards clock step inside a session, rows the window holds can sit outside those stamps, and the request is refused (loud, not silent). Keep, or refuse only the ts-vs-ts pairs.

## The two questions

1. Least confident, rechecked:
   - A-1's effect, driven above rather than inferred from the call count.
   - Every KILLED verdict on a structural mutant (moved `hi`, removed lock, WS return, subscribe flag, learned-into-primed, F-1 poll) re-run with its failure line: each failed on its own assertion.
   - Still unverified:
     - The A-10 clock-step false refusal is reasoned, not driven.
     - F-5 cannot be revert-verified deterministically.
     - A-7's `call_soon_threadsafe` belongs to the lifecycle batch: `stop_subscribers` is now also the close flag, so it must stay on the loop.
     - Nothing ran on Windows.
2. What we should have checked:
   - The internal-freeze-as-anchor shape beyond the three exports. The sweep found retrospective `/assert` with `last_ms` judging an hour-old tail on a quiet board; fixed through the same helper. `/plot/series` and the bundle have no freeze plus `last_ms`, so they comply.
   - Every `subscribe` caller (2: `/ws`, `CaptureWatch`) and every sentinel consumer (2): all handled.
   - The web UI's JS refusal mirror, which no leg listed as a consumer of the daemon's refusal wording; its contract test caught the drift (owed above).
