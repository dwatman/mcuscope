# Sweep: daemon side, classes 65, 66, 67, 68, 70 (server), 77 (Python)

Scope: `host/mcuscope/` minus `cli*.py` and `webui/`, swept at a259062 over the whole tree.
Line numbers are what each command returned at a259062, before any fix.

## Class 65: stop signal missing a late or lagging receiver

Sweep: `grep -n "subscribe(\|is None\|None in" host/mcuscope/server.py host/mcuscope/store.py`.
Site count: **86**.

Subscribe callers and sentinel consumers:

- store.py:1060 `subscribe`: complies, refuses after `stop_subscribers` with the shutdown message.
- store.py:1080 `unsubscribe`: complies.
- store.py:685 `_fail_queued` skipping the writer sentinel: complies, fails the writes queued behind it.
- store.py:737, 750 writer consuming its sentinel: complies.
  - Its only receiver is the writer.
  - A submit after the writer exits is refused by `writer_alive`; anything queued behind the sentinel is failed by `_fail_queued`.
  - Ports are stopped before `store.stop()` in the lifespan.
- store.py:1052 `drain_writes`: exempt because it is a no-op with no writer running.
- server.py:1970 `/ws` subscribe: complies, closes 1001 after close and 1013 at the cap.
- server.py:2006 `/ws` pump sentinel: complies, sends the rows ahead of it, then returns.
- server.py:2055 `/ws` unsubscribe: complies.
- server.py:2143 `CaptureWatch.open`: complies, and a refusal answers the shutdown 503.
- server.py:2147 `CaptureWatch.close`: complies.
- server.py:2179 `q is None` guard: exempt because it is a misuse guard, not a receiver.
- server.py:2193 `next_batch` sentinel: complies, hands out the rows ahead and raises on the next call.

Plain `None` checks with no subscriber or sentinel involvement, each exempt for that reason:

- store.py: 293, 374, 406, 409, 412, 569, 664, 677, 705, 1114, 1143, 1283, 1336, 1362, 1448, 1468, 1485, 1486, 1573, 1606, 1732, 2070, 2085, 2328, 2515, 2516, 2670, 2672 (28).
- server.py: 169, 583, 758, 973, 980, 1025, 1179, 1343, 1370, 1377, 1397, 1447, 1459, 1479, 1511, 1582, 1598, 1962, 2206, 2247, 2281, 2355, 2501, 2504, 2507, 2514, 2618, 2622, 2689, 2692, 2699, 2700, 2706, 2707, 2718, 2721, 2733, 2773, 2784, 2802, 2977, 3036, 3042, 3046, 3087 (45).

Accounting: 13 ruled above + 28 + 45 = 86.

Widened, because the grep only sees `subscribe(` and misses the sentinel producer and handlers busy elsewhere when it lands:

- store.py:638-644 `stop_subscribers`: **violates**.
  The drop-oldest that makes room for the sentinel did not count the row it shed, so `/ws` sent no gap for it and `ws_dropped` under-counted.
- server.py:2327/2329 `/wait` send (raw and cmd) and server.py:2586/2588 live `/assert` send: **violates**.
  A handler inside `send_command` (up to `timeout_ms`) or parked on the raw write lock never read the queue.
  The sentinel therefore sat past uvicorn's 5 s grace and the call was cancelled into a 500.
- store.py:1109 fan-out closed check: complies.
- Level-triggered stop flags, complying because a receiver arriving after `set()` still reads it:
  - serial_link.py 327/399/514/535/571/579/613 and 646/673, the reader stop event.
  - serial_link.py 1332/1351/1437, the `PortManager._closed` flag checked before and under the lock.
  - sim.py 778/827/984, the sim server stop event.
- server.py:178 WS `writable` event: exempt because it is backpressure, not a stop signal.
  uvicorn closes live sockets itself at shutdown.

## Class 66: loop-owned state mutated from a signal handler

Sweep: every `signal.signal` handler and `handle_exit` override (`grep -rn "add_signal_handler\|atexit\|SIGINT\|signal.signal\|handle_exit\|capture_signals"` over the owned modules).
Site count: **3 handlers**.

- daemon.py:273 `Server.handle_exit`: complies.
  It schedules `store.stop_subscribers` with `call_soon_threadsafe`.
  uvicorn 0.52.0 installs it only inside `capture_signals`, while the loop runs, so `get_running_loop()` holds.
- daemon.py:339 `_release_pid_on_terminating_signal._handler`: complies, touching only the pid file and re-raising with SIG_DFL.
- _stdio.py:71 Windows console ctrl handler: complies, calling only `_thread.interrupt_main()` from its OS thread.

## Class 67: internal freeze read as a caller bound

Sweep: `grep -n "max_id()" host/mcuscope/server.py`.
Site count: **6**.

- 1374 `DELETE /sessions/{id}?data=true`: complies, no relative parameter.
- 1462 bundle `hi`: complies, the bundle takes no relative parameter.
- 1602 purge `all`: complies, no relative parameter.
- 1605 purge `id_to` default: complies, `before_ts` is a separate absolute selector.
- 2142 `CaptureWatch` watermark: complies, the live window is timed from now and not from the id.
- 2708 `_resolve_window` freeze: complies.
  `floor_ts` is resolved at 2692 from the request's own bound, before both the `until_ts` fold (2705) and the freeze.
  Only `floor_ts` reaches the store, so no page re-anchors.

Widened to every `last_ms` consumer and every `_window_floor` call:

- store.py:1555 `_window_terms`: complies, anchoring only at an `id_to` the caller passes.
- server.py:1819 `/plot/series`: complies, its `id_to` is the request's own or an ended session's (SPEC 3.4 line 855).
- store.py:1739 `count_lines` dropping a capture-wide `id_to`: complies, done only with no `last_ms` in play.
- serial_link.py:81 `learn_stored_plot_defs` floor: exempt because it is a lookback, not a window.

## Class 68: refusal judged on primed state

Sweep: `grep -n "primed\|_plot_export_defs\|declared_kinds" host/mcuscope/server.py`.
Site count: **13**.

- 1487 bundle `_plot_export_defs`: exempt because the bundle refuses nothing on the decoders.
- 1890 `/plot/export` label-deadband refusal: complies, judging the primed decoders plus `_def_decoders` of the in-window `!pd` rows.
- 1896: comment on the same refusal.
- 3008 `_renders_as_label`: complies, reading every declaration of the name.
- 3105, 3108, 3118, 3128, 3134, 3136, 3138: `_plot_export_defs` itself, exempt because it defines the state rather than refusing on it.
- 3142, 3153 `_wide_header`: exempt because it builds the header and refuses nothing.

Widened to the other refusals in `/plot/export`:

- server.py:1870 wide `export_sids_safe(**win.scope)`: complies, judging the window.
- server.py:1876 `no such plot channel` refusal on `query_plot_channels_safe`: **violates**.
  `_rebuild_plot_summary` cleared the dirty flag and replaced the summary with an empty dict before awaiting its scan.
  A concurrent read in that span saw only the rows written since the rebuild began.
  The first read after start or after any delete rebuilds, so right after start `/plot/export` refused a channel with stored points (400), and `/plot/channels` briefly listed nothing.
  A scan that raised or was cancelled left that partial summary in place until the next delete.
- server.py:1779 `/plot/channels` meta from stored defs: exempt because it is metadata, not a refusal.

## Class 70, server side: every site emitting one status for several causes

Sweep: `grep -n "status_code\|HTTPException\|close(code\|def _bad_request\|..."` over server.py, widened to raw ASGI `"status":` sends, the middlewares, `exception_handler` and `Response(` constructors.
Site count: **26** from the first grep, **9 more** from the widening (640, 778, 796, 634, 772, 790, and the three `_TempFileResponse`/`StreamingResponse` 200 paths, which emit no error code).

| Code | Emitting sites (causes) | Client keying | Verdict |
|---|---|---|---|
| 400 | `_bad_request` (many validation causes), `StarletteHTTPException(400)` at 2690/2723 (unknown session) | CLI: generic exit 1 on the body | complies |
| 401 | `_TokenGuard._deny` 778 (missing or invalid token) | web UI state.js:81 prompts | complies, one cause |
| 403 | `_SameOriginGuard._deny` 640 (Host or Origin), `/shutdown` 975 (non-loopback), `_config_write_denied` 1068 (network config write without a token) | CLI InvalidStatus 403 exits 1; nothing else keys on 403 | complies, every cause is "daemon present, refused" |
| 404 | routing and StaticFiles only; the daemon raises none | CLI cli_client.py:199 reads it as "older daemon" | complies |
| 409 | `_save_error` 1077 (`ConfigConflict`, only from a revision mismatch) | web UI settings.js:565 | complies, one cause |
| 422 | `RequestValidationError` 520 | none | complies |
| 429 | `_deny_rate_limited` 796 | none | complies |
| 500 | unhandled 527, config save 1078, config read 1088 | none | complies |
| 503 | 1928/1937 (`CaptureStopped`), 2301/2579 (`StoreError`: subscriber cap or subscribers closed) | CLI cli_client.py:193 keys on status plus the body prefix `daemon is shutting down` | complies, both shutdown messages carry the prefix and the cap does not |
| WS 1008 | `_SameOriginGuard` 634, `_TokenGuard` 772, `/ws` 1966 (no such port) | web UI api.js:621 treats 1008 as an auth failure; CLI cli.py:1114 exits 1 | **see owner decision O1** |
| WS 1013 | `_deny_rate_limited` 790, `/ws` 1975 (subscriber cap) | CLI cli.py:1120 prints "too many subscribers" | **see owner decision O1** |
| WS 1001 | `/ws` 1973 (subscribers closed) | CLI exits 3 | complies |

Driven through uvicorn (`~/tt-data/sweep-daemon/ws_refusal_probe.py`): a mismatched Origin arrives as `handshake HTTP 403`, and `?port=nope` as `close 1008 reason='no such port: nope'`.
Every middleware refusal sends `websocket.close` before accept, and uvicorn turns that into an HTTP 403 handshake in all three WS implementations (websockets_impl.py:296-303).
So the guards' 1008 and 1013 never reach a client; only `/ws`'s own post-accept closes do.

## Class 77, Python side: monotonic fix-ups on a restarting clock

Sweep: `grep -n "lastTick\|+ 1e-4\|Math.max(.*tick" host/mcuscope/*.py` (Python files only).
Site count: **0**.

Widened to `max(`, tick, seq and wrap handling over the owned modules; every relevant site:

- store.py:2074 `_note_plot`: complies, choosing the newest point by host `line_id` and never by tick, so a reset tick simply becomes `last_tick`.
- store.py:806 `_next_id = max(...)`: exempt because it resyncs the host id sequence from SQL, not a device clock.
- serial_link.py:1156 `next_seq`, protocol.py:257: complies, wrapping 65535 to 1 per SPEC 2.3 as a host counter.
  The seq carried across a reattach (1365/1399) is host state too.
- server.py:2261 `_repeat_send` re-anchor: exempt because `loop.time()` is monotonic and never restarts.
- pjstream.py:161: complies, forwarding the raw tick with no ordering fix-up.
- sim.py:82 `tick_ms`: complies, wrapping at 2^32 as firmware does, with no consumer-side fix-up.
- store.py:1649 `_window_id_floor` assuming `ts` rises with `id`: exempt because SPEC 3.4 (line 866) documents it as the weaker half, and it is the host wall clock, not a device tick.
- The remaining `max(` hits (lockfile 125, link 272, sim 638, update_check 91, store 499/1218/1399/1671/1932/2171/2172/2483/2588/2628/2630, server 2695/3120) are exempt because they clamp or size values and involve no clock.

## Fixes

- F1 store.py:646-648: `stop_subscribers` counts the shed row in `_sub_dropped` and `ws_dropped`, so `/ws` announces the gap.
- F2 store.py:449, 641: `_subscribers_stopped` event, set with the sentinel.
  server.py:2165 `CaptureWatch.until_stopped` races a send against it: a lost race cancels the send and raises `CaptureStopped` (the existing 503), and the losing wait is cancelled.
  It is used at server.py:2347/2349 (`/wait`) and 2606/2608 (live `/assert`).
- F3 store.py:2068 `_settled_plot_summary`: both summary readers also wait when a rebuild holds the lock.
  store.py:2149: a failed or cancelled scan re-dirties the summary.
- CHANGELOG.md Unreleased/Fixed: two lines.
  SPEC is unchanged, since SPEC 3.4 line 743 already promises the 503 for a call parked at shutdown.

## Tests

`host/tests/test_sweep_daemon_classes.py`, 10 cases, all failing before the fixes:

- `test_the_row_the_sentinel_sheds_is_counted_as_dropped`
- `test_a_call_parked_in_its_send_answers_503_at_the_sentinel[{/wait,/assert}-{cmd,raw}]`: a dropped sim response or a held raw lock parks the send; the call must answer the exact shutdown message inside 4 s, with nothing left parked.
- `test_a_send_that_completes_leaves_no_stop_waiter_behind[/wait,/assert]`
- `test_a_summary_read_during_a_rebuild_waits_for_it[query_plot_channels_safe,plot_ports_safe]`
- `test_a_failed_rebuild_scan_leaves_the_summary_dirty`

Run: the new file plus the 22 existing files touching these paths, 614 passed.
`ruff check .` is clean.

## Revert table

Script: `~/tt-data/sweep-daemon/revert.py`.
It checks every anchor before writing, mutates one branch at a time, restores from a copy, and asserts byte equality at the end.

| # | Branch reverted | Result |
|---|---|---|
| R1 | shed row per-queue count | fails |
| R2 | shed row `ws_dropped` | fails |
| R3 | stop event `set()` | fails (4 send cases) |
| R4 | `/wait` raw send raced | fails |
| R5 | `/wait` cmd send raced | fails |
| R6 | `/assert` raw send raced | fails |
| R7 | `/assert` cmd send raced | fails |
| R8 | lost race raises `CaptureStopped` | fails |
| R9 | losing send cancelled | fails |
| R10 | losing stop wait cancelled | fails (2) |
| R11 | reader waits for an in-flight rebuild | fails (2) |
| R12 | failed scan re-dirties | fails |

## Broken existing tests

None.

## Owner decisions

- **O1 (class 70), owner should pick.**
  SPEC 3.1 (lines 358-359) and 3.4 (881, 884) promise WS close 1008 for Host, Origin and token failures and 1013 for the token lockout.
  On the wire each is an HTTP 403 handshake, so web UI api.js:621's "1008 means prompt for a token" never fires for a token failure (the REST 401 prompt still does).
  - (a) Correct SPEC to "handshake refused with HTTP 403" and drop the dead 1008 auth branch from the web UI.
  - (b) Send the ASGI denial response (`websocket.http.response.start`, supported by uvicorn) with 401, 403 or 429 to match the HTTP guards.
    SPEC and the web UI then key on the handshake status; the CLI already maps InvalidStatus 401/403 to exit 1 and would add 429.
  - (c) Accept, then close 1008 or 1013, making SPEC true as written.
    1013 would then mean both lockout and subscriber cap, and cli.py:1120 would tell a locked-out client "too many subscribers": a new class 70 instance.
  - Recommendation: (b).
- **O2, owner should pick.**
  `POST /cmd` (server.py:1648) parked in `send_command` at shutdown still answers 500 after the 5 s grace, since it holds no subscription.
  SPEC defines no shutdown answer for it.
  - (a) Race it against the same stop event and answer 503 `daemon is shutting down; ...`, which the CLI already maps to exit 3.
  - (b) Leave it.

## The two questions

1. **Least confident: that the token and lockout refusals are also HTTP 403 on the wire.**
   Only the Origin refusal was driven, because the token guard exempts loopback and the in-process stack cannot present another client address.
   The claim rests on the shared pre-accept `websocket.close` path and uvicorn 0.52.0's source.
   Re-driven: handler cancellation during a raced send (`~/tt-data/sweep-daemon/cancel_probe.py`) propagates `CancelledError`, cancels the send and leaves no stop waiter.
2. **What we had not checked: handlers that block at shutdown without a subscription.**
   `/cmd` is O2.
   The summary race was found by widening class 68 past its grep: `query_plot_channels_safe` is the input to a refusal, and nothing in the registry covers a read during a rebuild's await (closest is class 37).
   Candidate class for the orchestrator: "a rebuild that publishes an empty or partial structure before its await, with readers not waiting on it".
   Sweep: every `self.<x> = {}` or `= []` assigned before an `await` in store.py.
