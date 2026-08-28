# Registry sweep leg, defect classes 21-40

HEAD checked: `fd76735 POST /ports held to the config-write bar` (as expected).
Read-only leg: no repo file was edited. Probes ran from `/home/daniel/git/mcuscope/host` with `uv run python` / the installed console scripts.

Result: **3 violates, all CONFIRMED by probe** (classes 30, 35, 39). One cross-class observation (outside 21-40) found while probing.

## Findings

### F1 (class 35) CONFIRMED: `--json` turns every error exit into 0 when stdout is a closed pipe

- Site: `host/mcuscope/cli_output.py:127-128` (`out_json` writes stdout unguarded), reached from `die()` at `host/mcuscope/cli_output.py:79` and from the usage-error arm at `host/mcuscope/cli.py:1710`.
- Mechanism: exactly class 35, on the other stream. `die()` calls the guarded `err()` first, then `out_json`, which raises `BrokenPipeError` before `raise typer.Exit(code)`. The exception lands in the dispatcher's broken-pipe arm (`host/mcuscope/cli.py:1720-1726`), whose reasoning is about a reader that has finished, and that arm returns **0**.
- The stderr half of this was fixed (`_write_err` + `_silence_stderr`, `cli_output.py:37-51`); `out_json` never got the same boundary guard. One of two siblings fixed.
- Probe (stdout read end closed the way `| head` closes it):

```
mcu --json status --url http://127.0.0.1:1   -> exit 0, stderr "daemon unreachable ..."
mcu       status --url http://127.0.0.1:1   -> exit 3   (correct)
mcu --json status --url http://127.0.0.1:1   -> exit 3 with stdout open (correct)
```

- Not one command: every `--json` error exit tested came back 0.

```
mcu --json nosuchcmd                                  -> 0 (owes 2)
mcu --json status --url            (missing value)    -> 0 (owes 1)
mcu --json cmd x  --url http://127.0.0.1:1            -> 0 (owes 3)
mcu --json mark hi --url http://127.0.0.1:1           -> 0 (owes 3)
```

- Same fix shape as the stderr half: guard the write at the boundary that knows the stream, suppress the write's failure, and repoint the stream at devnull (`_silence_stdout` already exists) so the shutdown flush cannot raise over the mapped code.

### F2 (class 30) CONFIRMED: the firmware C-suite wrapper trusts `make`'s exit code alone

- Site: `host/tests/test_firmware_monitor.py:52-55` (`test_firmware_monitor_c_suite`) and `:59-75` (the ASan twin). Both assert only `proc.returncode`.
- The runner prints its own summary (`153/153 checks passed`, `firmware/tests/test_monitor.c:836`) and the wrapper never reads it, so a build whose `main()` runs no checks exits 0 and the wrapper is green.
- The CI guard (`.github/workflows/ci.yml:92-96`) checks only that the pytest case was not SKIPPED, which is a different property. Its JS sibling (`host/tests/test_webui_js.py:70-91`) has the full count-parse guard. One of two siblings fixed, again.
- Probe: pointed `FW_TESTS` at a stub `Makefile` whose `run` target prints `0/0 checks passed` and exits 0.

```
RESULT: wrapper PASSED against a C suite that ran 0 checks
```

- Fix shape: parse `(\d+)/(\d+) checks passed` from stdout and assert a nonzero, non-declining count, as the JS wrapper does.

### F3 (class 39) CONFIRMED: `_stage_backfill`'s exceptional exit orphans the snapshot task

- Site: `host/mcuscope/cli.py:490` (`task = asyncio.create_task(asyncio.to_thread(backfill))`), exit at `host/mcuscope/cli.py:503-513`.
- The `except BaseException` consumes `recv` (cancel + await) and does not consume `task`. On the `task.result()` raise path `task` is consumed by the raise itself, but a cancellation arriving while both are pending leaves `task` pending; when the backfill thread then raises, asyncio prints "Task exception was never retrieved" at collection time.
- `asyncio.run`'s shutdown does not cover it: `asyncio.all_tasks()` excludes tasks that are already done, so a task that completed with an exception before shutdown is never retrieved. This is the exact reporting shape that made the pipe-closed test noisy on CI in the fix that created this handler.
- Probe, driving the shipped function (`/tmp/claude-1000/review-r2/probe39b.py`): cancel the enclosing task while the snapshot is in flight, then let `backfill` raise.

```
outer: CancelledError
STDERR: Task exception was never retrieved
future: <Task finished ... to_thread() ... exception=RuntimeError('snapshot blew up')>
```

- Fix shape: consume both in the handler (cancel and await `task` as well), or wrap the pair in one `finally`.

## Cross-class observation (outside classes 21-40, reported not worked around)

`mcu --token <non-ASCII> ...` reaches the user as a rich traceback plus a crash log, not a mapped exit code: httpx raises `UnicodeEncodeError` from `_normalize_header_value`, which is neither an `HTTPError` nor in any handler's tuple (class 18 shape, class 9 contract).

```
$ mcu --token "sesameopen" status --url http://127.0.0.1:1     # with a U+00F6 in the token
exit=1
mcu: fatal error; traceback written to ~/.local/share/mcuscope/mcu-crash.log
```

This belongs to the classes 1-20 leg; noted here because the crash log surfaced while probing.

## Verdict lists

Every list opens with the count the sweep command returned. "exempt" carries its reason.

### Class 21, wall-clock granularity as a test ordering assumption

Sweep A, `grep -rn "time\.time()" tests`: **65 sites**.

- 52 sites of the shape `ts=time.time()` (or `_store_rx_line(time.time(), ...)`, `_on_bytes(time.time(), ...)`, `_Pending(..., time.time())`) pass the clock read *as* a row's ts: **exempt**, the registry names this the exempt shape.
  Files and counts: test_webui.py 1, test_assert.py 2, test_reconnect.py 8, test_e2e.py 2, test_regressions.py 5, test_sessions.py 5, test_plot.py 2, test_hardening.py 27.
- test_assert.py:36,139-140 - **complies**: spins the clock (`while time.time() <= old_ts + window`) rather than sleeping, with the 15.625 ms tick named.
- test_assert.py:325 - **complies**: comment plus a cut pinned to the stored data.
- test_config_api.py:316 (`checker.checked_at = time.time()`) - **exempt**, an assignment, not a boundary.
- test_update_check.py:103 - **complies**, `pytest.approx(..., abs=30)`.
- test_update_check.py:124,343 - **exempt**, written into a cache file as data.
- test_update_check.py:348 (`assert c.checked_at <= time.time()`) - **complies**: the stored value is `min(future, earlier now)`, and `<=` admits an equal tick.
- test_regressions.py:1112 - **complies**, `approx(stamp, abs=1.0)`.
- test_hardening.py:664 (`now`) - **complies**, the three rows get derived ts values (`now - 3600`, `now`).
- test_hardening.py:1455 - **complies**: every row was moved 999999 s back first, so no tick can be shared.
- test_hardening.py:1489 (`cut`) - **complies**: the assertions are on the query plan text, not on row membership.
- test_hardening.py:1545 (`base`) - **complies**: explicit derived ts values, and the comment names class 21.
- test_hardening.py:1586 (`cutoff = time.time() - 86400`) - **complies**, a day of margin.
- test_hardening.py:1767,1912 (`now`) - **complies**: rows get `now - k` / a shared `now`, with no ordering read off two clock calls.

Sweep B, the wall-clock *threshold* face, `grep -rn "perf_counter\|monotonic" tests`: **106 sites**, of which 84 are `deadline = time.monotonic() + N` poll loops (**exempt**: an upper bound on waiting, not an assertion) and 22 are elapsed-time assertions.

- Lower bounds (`elapsed >= X`): test_capture_lock.py:134, test_reconnect.py:104-142 (4), test_assert.py:246 - **comply**, a lower bound cannot go green on a fast or slow machine.
- Upper bounds paired with a discriminating positive assertion - **comply**: test_regressions.py:384 (with `resp.status_code == 400` and `"budget" in error`), :408, :429, :433, test_assert.py:225 (with `status == "fail"`), :248 (with `status == "pass"`), :268 (with `status == "fail"`), test_reconnect.py:110.
- test_regressions.py:750 (`elapsed < 1.2`, the /devices loop-block test) - **complies with a caveat**: latency *is* the property under test and there is no countable substitute, and the comment records the earlier tight-budget failure and the 1.9 s vs round-trip spread it now sits between.
- test_regressions.py:386 (`ticks[0] >= elapsed * 20`) - **complies**: a rate floor at 20 percent of the heartbeat's nominal 100 Hz, and the GIL half of the property is not otherwise countable.
- webui_js: **no elapsed-time assertion remains**; the regex budget is asserted by counting work (`plots_finite.test.mjs`, `terminal_logic.test.mjs` assert the pattern is dropped and never re-armed).

Sweep C, the shed-queue face: the `/wait` and `/assert` stimulus tests re-arm rather than firing once (test_assert.py emits from a thread in a loop, and the drop counter is asserted). **complies**.

### Class 22, a stdlib predicate standing in for a wire grammar

Sweep A, `grep -rn "isdigit()\|isdecimal()\|isalnum()" mcuscope`: **11 hits, 2 executable**.

- `mcuscope/protocol.py:158` - **complies**, this *is* `is_decimal_token` (`isascii() and isdecimal() and len <= max_digits`).
- `mcuscope/pjstream.py:44` - **complies**: `port_s.isascii() and port_s.isdigit()` (ASCII-gated, so no other script's digits), followed by `int()` and an explicit `1 <= port <= 65535`. A pathological length raises `ValueError`, which is the type `parse_dest` documents and its callers already handle.
- The other 9 hits are comments explaining why the helper is used instead. **exempt**.

Sweep B, `grep -rn "\bint(\|\bfloat(\|\bbool("` over `mcuscope`: **78 lines**, of which 24 are comments/docstrings (**exempt**) and 54 executable. Ruling the ones whose argument comes from outside the process:

- `sim.py:546` - **complies**, gated by `p.is_decimal_token` plus lo/hi range (`_parse_dec`).
- `protocol.py:130,184,292,395,408,453,554,570,644,736,748,919` - **comply**: each is preceded by `is_decimal_token` / `parse_hex_int`'s 16-digit cap / an explicit `in "0123456789"` character test, and each has a range check.
- `serial_link.py:173` - **complies**, `is_decimal_token` then `0 <= seq <= SEQ_MAX`.
- `pidfile.py:146` - **complies**, the record grammar plus the 1..0x7FFFFFFF bound.
- `update_check.py:71` - **complies**, `_VERSION_RE` is `[0-9]` (not `\d`) with a 64-char cap.
- `update_check.py:181` - **complies**, `float()` inside `except (OSError, ValueError, KeyError, TypeError)` followed by `math.isfinite` and a future clamp.
- `cli_daemonctl.py:48` - **complies**, `suppress(ValueError)` plus `finite()` plus `max(wait_s, 0.5)`.
- `config.py` - **complies**: every read goes through `_as_bool` / `_as_int` / `_as_str` / `_as_cap`, including the ports loop (`config.py:262-333`). No bare coercion of a TOML value remains.
- `store.py:438-439,950,961,978,1004,1352,1372,1447,1590,1721-1722,1834,1913,1922,2010,2068-2069`, `server.py:695,1995`, `cli.py:852,962,1704,1744`, `_stdio.py:52,90`, `cli_output.py:149,160,241`, `sim.py:109,354,450,484` - **exempt**: the argument is a SQLite column, a validated pydantic field, an internal counter or a `ctypes` return, not a wire/argv/config token. `store.py:961,978` sit behind `resolve_session`'s `is_decimal_token` gate.

Sweep C, `grep -rn "parseInt\|parseFloat" mcuscope/webui`: **5 hits**.

- `can.js:35`, `plots.js:188`, `plots.js:207` - **exempt**, base-16 uses on hex wire tokens already matched whole by an explicit hex regex.
- `plots.js:68` (`parsePlotValue`) - **exempt** by the registry's stated exemption: an explicit grammar regex plus `Number.isFinite`.
- `state.js:139` - a comment on `intField`, which uses `Number()` + `Number.isInteger`. **complies**.

### Class 23, a rebuild path silently un-freezes a paused surface

Registered freeze surfaces (`grep -rn registerSurface mcuscope/webui`): **3** (`panes` terminal.js:232, `charts` plots.js:889, `digital` digital.js:572).

- panes: writers of `pane.rows` are the live queue flush, `rebuild()` (terminal.js:274-289), the clear-all handler (terminal.js:576-580) and `resetForDbReset` (api.js:170). `rebuild` reads `pane.frozenRows` while paused and bounds at `pane.frozenId`; the live path counts into `pending` instead of appending (api.js:120-131). **complies**.
- charts: `chartDrawData` (plots.js:691) serves `chart.frozen` while paused; the ingest path buffers without setting `dirty` (plots.js:452). **complies**.
- digital: `laneDrawData` (digital.js:110) is the single seam every consumer (draw, cursor, readout, export) goes through. **complies**.
- Export buttons bound by the freeze: `exportChart` uses `chart.frozenMaxId` (plots.js:903), `exportDigital` uses `digitalFrozenId` (digital.js:287), and `state.js:293-294` sends it as `id_to`. **complies** (this was the class's own "export ignores its freeze" clause).
- `exportCan` (can.js:264) - **exempt**, the CAN table is not a freeze surface and has no paused state.
- Observation (not a violation): `clearAllDigital` drops `digitalFrozen` while paused by design (digital.js:598-601) and `showDigital` re-anchors on the next ingest (digital.js:82). Between the clear and that re-anchor, the first ingest batch adds a lane whose `frozen` snapshot is not taken (`digital.js:144` requires `digitalFrozen`), so a paused panel can creep by one batch. Documented and bounded; recording it so a later round does not rediscover it as new.

### Class 24, a fix that rests on one runtime version's driver behaviour

Sweep, driver-side row consumption / coercion / transaction handling: **9 `fetchall()` sites, 3 `executescript` sites, 1 version gate**.

- `store.py:159` `_reclaim_pages` - **complies**: uses `executescript`, with the 3.11-yields-no-rows reasoning recorded at `store.py:149-151`.
- `store.py:470,1132` `executescript(SCHEMA)` - **exempt**, DDL, no row consumption.
- `store.py:261-264` - **complies**: explicitly refuses `executescript` inside the rebuild transaction because it would commit first.
- `store.py:241,1005,1406,1615,1685,1746,1760,1805,1834` `fetchall()` - **exempt**: ordinary SELECT result reads, not a mechanism a fix rests on.
- `mcuscope/__init__.py:25` `sys.version_info < (3, 11)` - **exempt**, the support-floor guard itself.
- The floor leg: CI runs 3.11 (the class's own bit was caught there); no fix in this sweep now depends on driver row consumption.

### Class 25, a group state that only reaches the members that already existed

Sweep, group operations and the sites that create/destroy a member:

- pause all (`terminal.js:569-571` -> `freeze.pauseAll`): fan-out then latch, set *after* the fan-out (`freeze.js:39-45`). **complies**.
- member birth, panes: `addPane` consults `bornPaused()` (terminal.js:506) and otherwise recomputes the shared label. **complies**.
- member birth, charts: chart creation consults `bornPaused()` (plots.js:389). **complies**.
- member birth, digital lanes: `addDigitalLane` gives a lane born after the freeze an empty snapshot (digital.js:144). **complies**.
- clear all (`terminal.js:573-580` -> `clearAllCharts`, `clearAllDigital`, `clearAllCan`): `clearAllDigital` no longer calls `setDigitalPaused(false)`; its comment states "Clearing empties the panel; it does not resume it" (digital.js:598). **complies** (the registry's inverted case is closed).
- label: one definition, `pauseAllLabel()` from `anyLive()` (freeze.js:54), recomputed from every surface's own `setPaused` via `freezeChanged()` and on add/remove (`updateShared`, terminal.js:243). **complies**.
- `resetForDbReset` (api.js:161-197): keeps each pane's live/paused state and re-runs clear-all on the sidebar models. **complies**.

### Class 26, a frozen view re-derived from a ring buffer that has rotated past it

Sweep, every frozen/held view and what backs it: **3 views**.

- pane (`pane.frozenRows`, terminal.js:222): snapshots `buffer.filter(...)` at pause; `rebuild` reads `pane.frozenRows || buffer`. Backing is the bounded 5000-row shared buffer, so the snapshot is required and present. **complies**.
- chart (`chart.frozen`, plots.js:872): snapshotted at `setChartPaused(true)`; `frozenLen` is gone. **complies** (the third instance the registry corrected).
- digital lanes (`lane.frozen`, digital.js:99): full `xsHost/xsTick/vs` copies at `anchorDigitalFreeze`, plus `digitalFrozen` for the time pin, with the class named in the comment. **complies**.
- No other view holds a bound into a ring: `seedMaxId` and `plotChannelMeta` are Maps cleared with their charts, and `state.maxId` is a live watermark, not a freeze.

### Class 27, a test double gentler than the thing it stands in for

Sweep, doubles declared in `host/tests`: **41 classes** (`grep -rn "^class \|^    class " tests`), of which the ones standing in for something with dispatch or a side effect:

- `tests/support.py:143-153` `SimEndpoint.open` - **complies**: dispatches on the device string (`sim://` only), so dead `socket://` ports still fail to connect, which is what the attach tests document.
- `tests/webui_js/freeze.test.mjs:22-26` surface double - **complies**: its `setPaused` calls `freeze.freezeChanged()`, the side effect the whole latch bug turned on, and the latch test uses **two** live surfaces so a sibling's callback can run mid-fan-out.
- `tests/support.py:56` `SpyLink` - **complies**, a real `SourceLink` subclass; only the cancel answer is substituted, and it defaults to the base behaviour.
- `tests/test_cli.py:1393` `_FakeWs`, `:1921` `_ScriptedWS`, `:1497` `_ClosableWs` - **comply**: `recv` raises the real `websockets.exceptions.ConnectionClosedOK`, not a bare return, and `_ScriptedWS` can raise an arbitrary exception through `recv` for the failures that arrive that way.
- `tests/test_cli.py:670-698` `_Resp`/`_Stream`/`_FakeHttp` - **complies**: the unstreamed paths (`.text`, `.request`) raise rather than answering, so the double cannot be gentler than the real client on the respect under test.
- httpx `MockTransport` users (`run_mcu_canned`, test_cli.py:919) - **exempt**, a real httpx object; only the socket is replaced.
- Failure-injection doubles (`_CommitBoom`, `_SlowCommit`, `_NoWal`, `_AcceptFailsOnce`, `_CloseFails`, `_ClientCloseFails`, `_DeadListener`, `_StuckLink`, `_StuckReader`, `_WedgedLink`, `_LiveButRefusing`, `_AngryCancel`, `_Broken`, `_NoStore`, `_Unretrieved`, `_ClosedHandle`, `_DeadPipe`, `_FlushFailsStdout`, `_Denied`, `_Console`, `_Died`, `_Unresponsive`, `_NoCancel`, `_Sock`, `_Native`, `_Info`, `Blocking`, `FailingCopy`, `Scripted`, `_Unpluggable`, `OneStepPerExecute`, `_StoppableDaemon`) - **exempt**: each is *harsher* than the real thing (that is the point of it), which is the safe direction for this class.
- `tests/webui_js/dom_stub.mjs` - **exempt with a stated ceiling**: it cannot fake a laid-out canvas (`clientWidth` is always 0), which `CLAUDE.md` records, and the logic that needs one lives in DOM-free modules.

### Class 28, an assertion the test's own guard swallows

Sweep A, `grep -rn "raise AssertionError" tests`: **15 sites**.

- test_source_link.py:49, :64, :137 - **comply**, the `try / except <expected> / else: raise` shape.
- test_protocol.py:837 - **complies**: the raise is inside `except Exception as exc` and asserts on what it caught (the exception type is the failure).
- test_sim_tcp.py:55, test_sim_pty.py:44 - **comply**, after the read loop at function level, no enclosing except.
- test_sim_tcp.py:201 - **complies**: raised from `except OSError`; `AssertionError` is not an `OSError`, and there is no enclosing handler in the function.
- test_sim_tcp.py:253, test_sim_pty.py:61, test_cli.py:1631 - **comply**, plain function-level raises.
- test_hardening.py:933 (`inner_app`) - **complies**: reaching it either propagates the `AssertionError` out of `await guard(...)` or leaves `sent` empty; neither is swallowed.
- test_cli.py:678, :698 - **comply**: raised inside a double, and the test additionally asserts `rc == 0` and exact stdout, so a swallowed raise cannot read as a pass.
- test_cli.py:1471 - **complies**, paired with `rc == 0 and len(calls) == 1`.
- test_cli.py:2315 - **complies weakly**: the surrounding assertion is `rc == 1` only, but `cli.main()` does not catch `AssertionError` (the `except` tuples are `EXIT_EXCEPTIONS`, `USAGE_ERRORS`, `ABORT_EXCEPTIONS`, `OSError`, `KeyboardInterrupt`, `KeyError/IndexError`, `SystemExit`), so a reached daemon surfaces as an error, not as a green refusal. Worth a stderr-text assertion; not filed as a violation.

Sweep B, `grep -rn "except Exception" tests`: **3 sites**, none around an act under test.

- test_protocol.py:836 - **complies** (asserts on what it caught).
- test_update_check.py:157, test_hardening.py:742 - **exempt**, both are comments, the second recording this very fix.

Sweep C, `pytest.raises` on stdlib types: the project types (`ProtocolError`, `PortError`, `StoreError`, `ConfigError`, `LockError`, `MatchBudgetExceeded`) are self-discriminating; `pytest.raises(typer.Exit)` sites assert `.exit_code` (test_cli.py). **complies**.

### Class 29, the negative is never asserted

Sweep, per guard, the observable on the refused path and an assertion of that value:

- link opener dispatch -> `connected is False`: **asserted**, test_e2e.py:118, test_e2e.py:594, test_daemon_startup.py:122. **complies** (the zero-hit grep that filed this class now returns hits).
- capture lock -> `LockError`: **asserted**, test_capture_lock.py:30, :89, :159, test_daemon_startup.py:79. **complies**.
- `PRAGMA foreign_keys` -> read back as 1, and the child table asserted directly rather than through a join: test_plot.py:327-341, test_hardening.py:95. **complies**.
- token guard -> 401/403 and the WS close code: test_security.py (7 assertions), test_hardening.py:739-745 (close code 1008 vs 1013 pinned). **complies**.
- config write bar -> refusal body: test_config_api.py. **complies**.
- `/assert` and `/wait` scope guards -> the 400 message text per field: server.py:1888-1897's four refusals are each driven with their own message in test_assert.py. **complies**.
- pid record -> "no pid file", the corrupt-record message, and the wrong-URL miss: test_cli.py:978-1010, :862-895. **complies**.

### Class 30, a wrapper that trusts an external runner's exit code

Sweep, `grep -rn "subprocess.run\|Popen" tests`: **33 sites**, of which 29 drive the shipped artifact itself (`mcu`, `mcuscoped`, `mcu-sim`, a `python -c` helper) rather than a test runner. **exempt** for those: the artifact's exit code *is* the contract under test, and each is paired with stdout/stderr assertions.

- test_webui_js.py:61 (`node --test`) - **complies**: asserts files exist, asserts they declare tests, parses both `# pass N` and `ℹ pass N` dialects, and fails if the reported pass count is below the declared count.
- test_firmware_monitor.py:52 (`make run`) - **VIOLATES**, see F2. CONFIRMED.
- test_firmware_monitor.py:59 (`make asan`) - **VIOLATES**, same shape, same runner, same missing count parse. CONFIRMED by the same probe (the wrapper reads only `returncode`).
- test_scaffold.py:80 (`<script> --version|--help`) - **exempt**, not a test runner; it asserts on version/usage text, not only the code.
- test_webui_js.py:33 (`node --version`) - **exempt**, a capability probe with `check=True`.

### Class 31, a field the model accepts and the path never reads

Sweep A, request models (AST over server.py): **15 models, 48 fields**. Every field has a read; per-branch rulings for the branchy handlers:

- `AssertBody` (10 fields) - **complies**. The retrospective-only fields are now refused by the live branch and vice versa: `session` and `last_ms` return 400 when `timeout_ms > 0` (server.py:1889-1894), `send` returns 400 when `timeout_ms == 0` (server.py:1895-1898), `min_window_ms` requires a live window (server.py:1882-1886). Both directions, which is what the class asked for.
- `WaitBody` (7 fields) - **complies**, one branch, all seven read (`_do_wait`, server.py:1743-1800).
- `PurgeBody` (6 fields) - **complies**: `session`/`before_ts`/`all`/`id_from`/`id_to` are the arms of one selector chain and `dry_run` is read on both outcomes (server.py:1273-1307).
- `ConfigPortEntry` (5 fields) - **complies**, all five read at server.py:1119-1138 via `entry.` (my first regex missed the `entry.` prefix; re-run confirms `autoconnect` is read at :1137).
- `PortAttach`, `SendBody`, `CmdBody`, `MarkerBody`, `SessionBody`, `ConfigServerBody`, `ConfigStorageBody`, `ConfigUpdateBody`, `PlotJugglerBody`, `ConfigPlotJugglerBody`, `ConfigPortsBody` - **comply**, single-branch handlers, every field read.

Sweep B, GET/WS handler parameters (AST, "appears once only" test): **36 endpoints, 0 unused parameters**. `/lines`'s ten (`port, chan, match, since_id, since_ts, last_ms, session, id_to, limit, order`), `/can/frames`'s seven, `/plot/series`'s eight, `/plot/export`'s five and `/ws`'s `port` are each read. **complies**.

### Class 32, a function tested as pure that mutates module state

Sweep A, `grep -rn "^    global \|^global " mcuscope`: **5 module-level mutables**.

- `cli_output._JSON_MODE` (written at cli_output.py:61) - **complies**: `main()` resets it per invocation (cli.py:1690), the hoist path's write is argued at cli_argv.py:120-125, and two tests pin it (`test_hoisting_is_a_pure_rewrite` test_cli.py:2570, `test_the_output_mode_does_not_leak_into_the_next_invocation` test_cli.py:2584).
- `_stdio._report_key` - **complies**: owned by `set_report_key`, called from `daemon.main` (daemon.py:314), and conftest.py:64-77 restores it around every test.
- `_stdio._ctrl_handler_ref` - **exempt**, written once by the installer at process start.
- `store._match_pool` - **exempt**, a lock-guarded lazy singleton, never reset.
- `serial_link._comports_cache` - **complies**: TTL cache; tests that care patch it with `monkeypatch.setattr` (test_reconnect.py:154,171), which restores.

Sweep B, random ordering: `pytest-randomly 4.1.0` is installed, so every local and CI run is already a shuffled run with a fresh seed. **complies**.

### Class 33, a test that runs the real entry point inherits the user's real environment

Sweep A, `grep -rn "platformdirs\." mcuscope`: **5 call sites** (`config.py:111`, `config.py:119`, `pidfile.py:61`, `_stdio.py:293`, `update_check.py:90`), all three functions (`user_data_dir`, `user_config_dir`, `user_cache_dir`) patched by the autouse `_isolated_user_dirs` fixture (conftest.py:31-41). **complies**.

Sweep B, tests that spawn a child: **10 files**, ruled individually.

- test_cli.py `_spawn_env(data_home, url)` users (daemon start/stop/keyed-pid tests, test_cli.py:820-905) - **comply**, explicit `XDG_DATA_HOME`.
- test_cli.py `_run_mcu_data_home` and `_child_data_dir` (test_cli.py:968-1010) - **comply**, and `_child_data_dir` resolves the path *in the child* rather than in-process, which is exactly the second face the class names.
- test_pidfile.py:148 - **complies**, `XDG_DATA_HOME` + `XDG_CONFIG_HOME` + an emptied `MCUSCOPED_CONFIG`.
- test_pidfile.py:88, :285 - **exempt**, bare `python -c` children that never import the package's path helpers.
- test_capture_lock.py:82 - **complies**, the db path is passed explicitly (`tmp_path`); only `PYTHONPATH` is added.
- test_scaffold.py:80 - **exempt**, `--version` / `--help` only, plus `MCUSCOPE_UPDATE_CHECK=0`.
- test_sim_tcp.py:245, test_sim_pty.py:49 - **exempt**, `mcu-sim` resolves no platformdirs path.
- test_webui_js.py, test_firmware_monitor.py - **exempt**, node and make.
- The general `run_mcu` helper (test_cli.py:53-65) passes `MCUSCOPE_URL` only, so its children inherit the real `HOME`. Ruled **complies** after probing what the child actually resolves: only `cli_daemonctl` touches `pid_file_path` (grep over `mcuscope/cli*.py` returns that one site), and the four `run_mcu(..., "daemon", ...)` tests take paths that exit first.

Probes behind that ruling (`HOME` redirected to an empty temp dir so any creation is visible):

```
mcu daemon status --url http://127.0.0.1:1          -> exit 3, no dir created
mcu daemon start --timeout nan --url ...:1          -> exit 1, no dir created
mcu daemon stop  --url http://127.0.0.1:notaport    -> exit 3, no dir created
mcu daemon status --url <live daemon>               -> exit 0, no dir created
mcu daemon start  --url <live daemon>               -> exit 1, no dir created  ("already running")
```

The four in-suite invocations are test_cli.py:437, :787, :939, :945 (plus :2340, :2347), each matching one of the rows above. **No site violates.**

Residual worth recording (not a violation, no current test reaches it): `pid_file_path` calls `os.makedirs(data_dir, exist_ok=True)` (pidfile.py:62), so a `daemon stop`/`daemon start` child with a *resolvable* URL and nothing answering does create the real user data dir, and `daemon start` goes on to write `capture.db`, `capture.db-shm`, `capture.db.lock`, the pid record and a startup log there. Probed with a redirected `HOME`:

```
mcu daemon stop  --url http://127.0.0.1:1  -> creates <data>/mcuscope/
mcu daemon start --url http://127.0.0.1:1  -> creates capture.db.lock, and spawns a daemon
```

Any future test added to `run_mcu` for `daemon stop`/`start` inherits the defect immediately; the cheap guard is to give `run_mcu` the same `XDG_DATA_HOME` treatment `_spawn_env` has.

### Class 34, a wire-named key on a prototype-bearing object store

Sweep, `grep -n "JSON.parse\|= {}\|= Object\|localStorage" mcuscope/webui/*.js`: **21 hits**.

- `chrome.js:24-33` `savedColors` - **complies**: `Object.create(null)`, and the loader type-checks every value (`typeof parsed[k] === "string"`) and the container.
- `plots.js:25` `PLOT_TYPES` - **complies**, `Object.assign(Object.create(null), ...)`.
- `freeze.js:60` `const out = {}` in `watermarks()` - **exempt**, keys are the three surface names registered in code, never from the wire.
- `pane.js:15` `cfg = {} / els = {}` - **exempt**, parameter defaults, not a store.
- `api.js:570` `JSON.parse(ev.data)` - **complies**, the parsed rows are routed by field, and every name-keyed collection they feed is a `Map` (`charts`, `digitalLanes`, `seedMaxId`, `plotChannelMeta`, `portColorCache`, `surfaces`).
- `terminal.js:531,553` `termState` - **complies** for this class: `st.timeMode` is `typeof`-checked, `st.panes` is `Array.isArray`-checked, and nothing is used as an object key. Minor observation: the per-pane `cfg.channels` / `cfg.port` / `cfg.regex` elements are not individually type-checked, which can produce a nonsense filter from a hand-edited value but no prototype exposure.
- `cmdbar.js:19-21` `cmdHistory` - **complies**, `Array.isArray` plus a `typeof x === "string"` filter plus the length cap.
- `statusbar.js:89-99` update badge - **complies**: the snooze *record* is gone, replaced by a single dismissed-version string compared with `===` (statusbar.js:118), so the `step`/`until` grammar the registry's last instance turned on no longer exists. The `url` field is validated at the sink (`/^https?:\/\//i`, statusbar.js:124).
- `state.js:15-21` `authToken`, `theme.js:16-23` theme - **comply**, plain strings, no keying.

### Class 35, an error-reporting write failing hijacks the exit code the error owned

Sweep, every write on an error path:

- `cli_output.py:43-46` `_write_err` (used by `err()`, and so by `die()` and every `err(...)` caller) - **complies**: suppresses the write's failure and calls `_silence_stderr()` -> `_to_devnull`.
- `cli_output.py:127-128` `out_json`, reached from `die()` (cli_output.py:79) and from the usage-error arm (cli.py:1710) - **VIOLATES**, F1. CONFIRMED.
- `cli_output.py:138-144` `emit_stream` - **complies**, catches `BrokenPipeError`, silences stdout, exits 0 (a follow ending on a closed reader is not a failure).
- `cli.py:1720-1726` the dispatcher's `OSError` arm - **complies as written** (`BrokenPipeError` only, everything else re-raised); it is the arm F1 lands in, but the defect is the unguarded write above it, not this arm.
- `cli.py:1670-1678` `main()`'s final `sys.stdout.flush()` - **complies**, both `BrokenPipeError` (0) and other `OSError` (1) are mapped after silencing.
- `_stdio.py:371,381` the crash/startup-log notices - **complies**, inside `console_entry`'s own guarded reporting, with the file written before the notice.
- `sim.py:682,698,752,899,940,942,969` - **exempt**, the simulator's stderr diagnostics, none of which own an exit code (the process keeps serving).

### Class 36, a periodic catch-up loop without a burst cap

Sweep, every loop emitting one item per elapsed period: **6 schedules**.

- `sim.py:382` heartbeat, `:396` CAN bus (per id), `:421` `sim alive`, `:474` plot - **comply**, all four go through `_due_beats` (sim.py:101-112), which caps at `PERIODIC_MAX_BURST = 4` and re-anchors to `now + period` when the cap bites.
- `sim.py:448-453` `--flood` - **complies**, `min(owed, FLOOD_MAX_BURST)` (5000) with the schedule advanced by the emitted count.
- `sim.py:467` `next_plot_def` - **exempt**, a one-shot (`if now >= self.next_plot_def`), not a catch-up.
- `store.py:2141` `_retention_loop`, `update_check._due()`, `serial_link._retry_wait` - **exempt**, sleep-and-check loops that emit nothing per missed beat; a store drain is the registry's own "backlog that must not be dropped" exemption.

### Class 37, an async read-modify-write spanning an await without a lock

Sweep, `async def` in store.py (**28**) and the port manager (**7**) that both read and write shared state across an await:

- `delete_range` (store.py:1968-1984) - **complies**, `async with self._sweep_lock` for the whole chunk loop.
- `_sweep_size_async` (store.py:2040-2054) and `_sweep_retention_async` (store.py:2119-2127) - **comply**, same lock.
- `_trim_oldest` (store.py:2029) - **complies**, called only from `_sweep_size_locked`.
- `start_session` (store.py:1017-1037) and `stop_session` (store.py:1054-1063) - **comply**, `async with self._session_lock`, with the body factored into `_stop_session_locked` so the lock is taken exactly once; the write-queue drain barrier before the `_next_id` sample is present.
- `PortManager.attach` (serial_link.py:1087-1125) - **complies**: the unlocked `prime_plot_defs()` is argued in the code, and the `self._closed` re-check *inside* the lock is exactly the interleaving guard the class asks for.
- `PortManager.detach` / `_detach_locked` / `stop_all` - **comply**, all under `self._lock`.
- `SerialPort.send_command` / `send_raw` (serial_link.py:934-970) - **comply**, `_cmd_lock` and `_raw_lock`.
- `POST /purge` (server.py:1273-1307) - **complies**: `lo`/`hi` are resolved with synchronous reads and then handed to `delete_range`; a concurrent sweep can only shrink what the id range finds, and no precomputed *count* is applied.
- The remaining store `async def`s (`query_*_safe`, `count_*_safe`, `export_*_safe`, `_offload`, `submit_line`, `add_line`, `drain_writes`, `open_plot_export`) - **exempt**, read-only or single-append, no read-then-act span.

### Class 38, a reset re-run missing the discipline the first run has

Sweep, callers of each initialization routine:

- `runBackfill`: first connect (`api.js` onopen path) and the re-run in `resetForDbReset` (api.js:186-197). The re-run now sets `staging = { gen, rows: [], dropped: 0 }` before the fetch and drains after it, with the reasoning inline. **complies**.
- `plotSeed` / `seedPlotDefs`: called from the same two, and `seedMaxId` is cleared with the charts on reset (plots.js:939). **complies**.
- `rebuild(pane)`: called from add, filter change, backfill end and the high-rate release; each bounded by `frozenId`/`clearId`. **complies**.
- `resetForDbReset` itself re-zeros `anchorTs`/`anchorTick`, `clearId`, `frozenId` and `frozenRows`, i.e. every watermark the first run starts from. **complies**.
- Daemon side: `SerialPort.start()` on reconnect re-runs `prime_plot_defs` via `PortManager.attach`, and the carried counters (`_carried`, serial_link.py:1119-1121) are the class-4 discipline the re-run inherits. **complies**.

### Class 39, a raced task orphaned by the exceptional exit

Sweep, `grep -rn "create_task\|ensure_future" mcuscope`: **9 create sites**, of which 3 are raced.

- `cli.py:490,492,500` (`_stage_backfill`) - **VIOLATES**, F3. CONFIRMED.
- `server.py:1599-1600` (`pump` / `watch` under `FIRST_COMPLETED`) - **complies**: the `finally` cancels and awaits *both*, under `suppress(CancelledError, Exception)`, on every exit including the exceptional ones.
- `serial_link.py:315` `_consumer_task` - **exempt**, not raced; consumed in `stop()`.
- `serial_link.py:568` `_store_sys` - **exempt**, not raced; the code holds a strong reference precisely so it is not GC'd mid-flight (comment at :558).
- `store.py:495,498,499` writer / initial sweep / retention - **exempt**, not raced; awaited in `stop()`.
- `update_check.py:237` - **exempt**, not raced; a detached singleton whose body has its own blanket handler (`test_update_check.py:154-159` pins that it must stay blanket).

### Class 40, multi-attribute state shared between the loop and a worker thread, torn on read

Sweep, `grep -rn "to_thread" mcuscope`: **13 executable call sites** (plus 4 comment references).

- `server.py:1106` `pj.configure(...)` - **complies**: the finding's own fix. `pjstream` swaps one immutable `(socket, sockaddr)` tuple read once per send, and the endpoint holds `request.app.state.config_write_lock`, so two `configure` calls cannot interleave.
- `server.py:340` `pj.configure(True)` (startup) - **complies**, single-threaded startup, same one-store swap.
- `server.py:960,1008,1028,1059,1079,1142,1243` config loads/saves - **complies**: each mutating one takes `config_write_lock`; the threaded callable writes the *file*, not daemon attributes, and the loop-side readers re-read the file.
- `server.py:927` `_enumerate_devices` - **exempt**, pure function, returns its result.
- `serial_link.py:348` `_close_link_locked` - **complies**: holds `_write_lock`, which every other acquirer also takes (the class-1 lock clause), and it clears one attribute.
- `serial_link.py:944,970` `_write_bytes` - **complies**, holds `_write_lock`; the counters it advances are single integers read individually by `/status`.
- `update_check.py:275,282` `_save_cache` - **complies**: the threaded callable only writes the cache file; `latest`/`checked_at` are assigned on the loop before the hop (update_check.py:270-282), so no loop-side reader can see a partial set from the thread.
- `cli.py:490` `to_thread(backfill)` - **exempt** for this class (single-process CLI, no loop-side reader of its attributes); it is F3's site under class 39.
- Other daemon-owned threads: the serial reader thread bridges through `loop.call_soon_threadsafe` with one payload per call (serial_link.py), and the store writer thread is the sole writer of its connection. **comply**.

## Method notes

- Site counts come from the commands quoted in each section, run at HEAD fd76735; nothing was ruled from memory.
- Probes are kept at `/tmp/claude-1000/review-r2/` (`probe39.py`, `probe39b.py`, `fwstub/Makefile`, `fakedaemon.py`).
- The probes created files under `~/.local/share/mcuscope/` (a startup log, a pid record, `capture.db*`) and started one daemon on port 8791; it was stopped with `mcu daemon stop` and confirmed gone. Nothing in the repo was touched.
- No contradiction was found between the instructions and `docs/REVIEW.md`. One registry statement needed re-reading rather than correction: class 30's "its firmware sibling had the guard" refers to the CI skip check, not to a count assertion in the wrapper, which is why F2 is still open.
