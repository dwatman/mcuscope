# Review round 2: registry sweeps 7, 8, 15, 16

Repo: /home/daniel/git/mcuscope at HEAD fd76735 ("POST /ports held to the config-write bar").
Read-only run. All probes executed from `host/` with `uv run`.

Summary:

| Class | Sites | Violates (confirmed) | Violates (suspected) |
|---|---|---|---|
| 7. Pid record lifecycle | 20 matrix cells | 1 | 0 |
| 8. Thread teardown | 8 threads/pools | 0 | 0 |
| 15. Shipped artifact vs stand-in | 7 deliverables | 0 | 0 |
| 16. One bad item ends the loop | 41 loops | 0 | 0 |

---

## Class 7. Pid record lifecycle

Sweep: the state matrix {no record, stale, live other process, live parent, our own} x {claim, release, stop, failed startup}. 20 cells, all listed.

Code read: `host/mcuscope/pidfile.py` (249 lines), `host/mcuscope/cli_daemonctl.py` (234 lines), plus the two callers the matrix runs through: `host/mcuscope/daemon.py:295-364` (claim/release around the whole serve) and `host/mcuscope/cli.py:1458-1499` (`mcu daemon stop`).

### The residual comment (registry's explicit ask)

The registry says the {stale} x {two concurrent claims} cell is narrowed but NOT closed, with the residual stated in `claim()`'s comment.

**Still there and still accurate.** `host/mcuscope/pidfile.py:201-205`:

> Residual, deliberately not closed: this narrows the window, it does not eliminate it. Windows has no atomic compare-and-delete, so a write landing between this re-read and the remove below is still lost. Do not file this as fixed.

Accurate against the code it sits in: `pidfile.py:206` re-reads, `:207` bails on any change, `:209` re-checks liveness, `:212` removes. Nothing between `:206` and `:212` is atomic, so the stated window is real and unclosed. The narrowing is asserted by `tests/test_pidfile.py:327 test_claim_does_not_remove_a_record_that_changed_under_it`, whose docstring itself says "the remaining window is documented in claim() and is not what this test covers" - so the test does not overclaim either.

Verdict: **complies** (narrowed, honestly documented, not filed as fixed).

### The 20 cells

#### claim (pidfile.claim, pidfile.py:150-236)

| # | Record state | Test asserting the outcome | Behaviour vs invariant | Verdict |
|---|---|---|---|---|
| 1 | no record | `tests/test_pidfile.py:39 test_claim_writes_own_pid_and_release_removes_it` | O_EXCL create succeeds, own pid written (`:176`, `:223`) | complies |
| 2 | stale | `tests/test_pidfile.py:62 test_claim_overwrites_a_stale_record` | re-read, liveness re-check, remove, retry once (`:206-215`) | complies |
| 3 | live other process | `tests/test_pidfile.py:72 test_claim_leaves_alone_a_live_record_that_is_not_our_parent` (drives a real `subprocess.Popen` child, not a synthetic pid) | left alone, returns None, daemon runs unrecorded by design (`:190-191`) | complies |
| 4 | live parent | `tests/test_pidfile.py:48 test_claim_does_not_clobber_a_live_record` (uses `os.getppid()`) | same branch as cell 3; the Windows CTRL_BREAK group-id reason is in the module docstring | complies |
| 5 | our own | `tests/test_pidfile.py:115 test_claim_reclaims_our_own_record`, `:104 test_claim_twice_from_the_same_process`, `tests/test_regressions.py:1055 test_claim_keeps_a_record_that_is_already_ours` (asserts no `os.remove` at all) | returns the path without remove+recreate (`:185-189`), so no no-pid-file window | complies |

Two claim-side edge cells outside the 5x4 grid, listed for completeness because the sweep reaches them:
- unwritable data dir: `tests/test_pidfile.py:361 test_claim_gives_up_when_the_data_dir_cannot_be_made` - **complies** (costs the record, not the startup).
- write fails after create (full disk): `tests/test_pidfile.py:203 test_claim_removes_the_record_when_the_pid_write_fails`, which also pins the close-before-remove ordering Windows needs - **complies**.
- empty record mid-write by another claimer: `tests/test_pidfile.py:246 test_claim_does_not_take_a_record_another_claimer_is_still_writing` (CLAIM_SETTLE_S) - **complies**.

#### release (pidfile.release, pidfile.py:239-248)

| # | Record state | Test asserting the outcome | Behaviour vs invariant | Verdict |
|---|---|---|---|---|
| 6 | no record | none directly; `tests/test_pidfile.py:125` asserts the `release(None)` no-op | `read_pid_record` returns None on a missing file (`pidfile.py:142-143`), None != getpid, returns without touching anything | complies (cell folded into the asserted sibling; the single `!= os.getpid()` guard at `:243` covers all four non-ours states) |
| 7 | stale | `tests/test_pidfile.py:125 test_release_keeps_a_record_someone_else_rewrote` (record rewritten to "12345", a foreign pid) | kept | complies |
| 8 | live other process | `tests/test_pidfile.py:72` (its tail: `release(path)` then `assert os.path.exists(path)`) | kept | complies |
| 9 | live parent | none directly | identical code path to cells 7/8 - one comparison against `os.getpid()`, no liveness branch, so the parent case cannot diverge | complies (exempt from a separate test: the code has no branch to distinguish it) |
| 10 | our own | `tests/test_pidfile.py:39`; and via signal, `tests/test_pidfile.py:135 test_daemon_releases_pid_file_on_sigterm` (the uvicorn signal-replay path, `daemon.py:239-256`); and `tests/test_pidfile.py:370 test_release_survives_a_record_it_cannot_remove` for the unremovable case | removed; an unremovable record does not raise out of the signal handler | complies |

#### stop (`mcu daemon stop`, cli.py:1458-1499 -> cli_daemonctl._stop_running_daemon)

| # | Record state | Test asserting the outcome | Behaviour vs invariant | Verdict |
|---|---|---|---|---|
| 11 | no record | `tests/test_cli.py:978 test_daemon_stop_no_pidfile_exit1` (no daemon: exit 1, "no pid file") and `tests/test_cli.py:1806 test_daemon_stop_falls_back_to_the_api_when_no_record_exists` (daemon up: stops it via /status + POST /shutdown) | both branches of `cli.py:1461-1471` | complies |
| 12 | **stale** | **NONE** | correct behaviour (probed below): removes the record, exit 1, "removed stale pid file (was pid N)" (`cli.py:1497`) | **VIOLATES (confirmed)** - see below |
| 13 | live other process | `tests/test_cli.py:1747 test_daemon_stop_keeps_the_record_of_a_pid_that_is_still_running` | record kept, exit 1, message names the pid and the url (`cli.py:1489-1494`) | complies |
| 14 | live parent | none directly | same branch as cell 13; `pid_running(pid)` does not distinguish a parent from any other live process | complies (exempt from a separate test: no branch distinguishes it) |
| 15 | our own (a live daemon answering /status) | `tests/test_e2e.py:70 test_shutdown_invokes_the_daemon_callback` plus `tests/test_capture_lock.py:221 test_a_daemon_that_started_first_releases_the_lock_on_the_way_out`; `_serving_pid` (cli_daemonctl.py:138) prefers the /status pid over the recorded one | POST /shutdown, wait, then remove the record; still-answering afterwards is reported rather than claimed as success (`cli_daemonctl.py:182-184`) | complies |

Corrupt/unreadable record, the fifth stop-side state, is separately pinned: `tests/test_cli.py:1003 test_daemon_stop_corrupt_pidfile_exit1` and `tests/test_cli.py:1830 test_daemon_stop_asks_status_before_giving_up_on_a_corrupt_record`. **complies**.

#### failed startup

| # | Record state at the failure | Test asserting the outcome | Behaviour vs invariant | Verdict |
|---|---|---|---|---|
| 16 | no record (claim succeeded, then serve failed) | `tests/test_daemon_startup.py:66 test_a_startup_failure_after_the_claim_leaves_no_pid_record`; `tests/test_regressions.py:1035` for the EADDRINUSE-after-claim origin | everything after the claim is inside the `try` (`daemon.py:294-364`), so the `finally` releases | complies |
| 17 | stale (claim overwrote it, then serve failed) | same test path: after the overwrite the record is ours, so cell 16's assertion covers it | released | complies |
| 18 | live other process (claim declined) | `tests/test_cli.py:1761 test_daemon_start_leaves_a_pid_record_that_names_another_daemon` (the CLI half) | `claim` returned None -> `pid_path` is None -> `release(None)` is a no-op; the other daemon's record survives | complies |
| 19 | live parent (claim declined) | as cell 18 | identical branch | complies |
| 20 | our own (`daemon start` recorded the child pid, child never answered) | `tests/test_cli.py:821 test_daemon_start_timeout_does_not_orphan_the_child` | `_abandon_daemon` (cli_daemonctl.py:104) removes the record only via `_remove_pid_record`, which re-reads and bails if another daemon has since claimed it (`:87-101`); if the child cannot be stopped the record is kept so it stays addressable (`:133-135`) | complies |

### Finding C7-1 (CONFIRMED violates): the {stale record} x {stop} cell has no asserted outcome

Site: `host/mcuscope/cli.py:1495-1497`.

```
        with contextlib.suppress(OSError):
            os.remove(pid_path)
        die(f"no daemon responding at {s.url}; removed stale pid file (was pid {pid})", 1)
```

This is the **only** cell in the whole matrix where `mcu daemon stop` DELETES a record, and it is the one cell the sweep leaves unasserted. Its three immediate neighbours in the same `if body is None:` block are each pinned by a test asserting on text unique to that path ("no pid file", "left it in place", "left its record ... in place"); the delete branch has nothing. A regression that widened this branch - say by dropping the `pid_running(pid)` guard three lines above, so a live daemon's record was deleted - would keep every existing test green, because each of them asserts a *different* refusal message.

Probe (the record is removed, and no test names that message):

```
$ grep -rn "removed stale pid file" host/tests/*.py
(no output)

$ T=$(mktemp -d); D=$(XDG_DATA_HOME=$T uv run python -c \
      "import platformdirs;print(platformdirs.user_data_dir('mcuscope'))")
$ mkdir -p "$D"; printf '2147483646' > "$D/mcuscoped-127.0.0.1-1.pid"
$ XDG_DATA_HOME=$T uv run mcu --url http://127.0.0.1:1 daemon stop
no daemon responding at http://127.0.0.1:1; removed stale pid file (was pid 2147483646)
exit=1
record still there: NO-removed
```

The **code complies** with the invariant (the pid is provably stale: 2147483646 is not running, and nothing answers at the url). What violates is the sweep's own exit condition, "every cell has an asserted outcome". Fix is one test in `tests/test_cli.py` beside `test_daemon_stop_corrupt_pidfile_exit1`: write a record naming a dead-but-valid pid, run `daemon stop` against a dead url, assert exit 1, assert "removed stale pid file" in stderr, and assert the file is gone.

---

## Class 8. Thread teardown on detach and shutdown

Sweep: per thread the daemon owns, enumerate the outlive scenarios (join timeout, loop closed, exception mid-read) and say whether the handle is closed and what happens to the callback.

Site discovery: `grep -rn "threading.Thread|to_thread|run_in_executor|ThreadPoolExecutor|threading.Timer" host/mcuscope/*.py` returns **33 lines across 8 distinct thread/pool sites**. All 8 listed.

### 1. Serial reader thread - `serial_link.py:316` (`threading.Thread(target=self._reader, name=f"serial-{alias}", daemon=True)`)

The only thread that owns an OS device handle, and the one the class exists for.

| Outlive scenario | Handle | Callback fate | Test |
|---|---|---|---|
| join timeout, reader wedged inside `link.read()` | `stop()` closes it itself from the loop side, off-loop via `to_thread(self._close_link_locked, link)` (`serial_link.py:333-348`) - Windows serial handles are exclusive, so this is what unblocks re-attach | any later `_post` is dropped once the loop is closed | `tests/test_reconnect.py:863 test_stop_closes_the_handle_of_a_reader_that_is_not_coming_back` - asserts `port._thread.is_alive()` first, so it cannot pass against a reader that simply exited |
| join timeout, then the reader's own `finally` runs later | `_close_link_locked` clears `self._link` under `_write_lock` (`:872-884`), so a concurrent write reports "not connected" rather than "write failed" | - | `tests/test_reconnect.py:1355` (asserts `port._link is None` and `pytest.raises(PortError, match="not connected")`) |
| open completes AFTER the join deadline (a `socket://` connect runs to pyserial's 5 s POLL_TIMEOUT, past the 2 s join) | the reader closes it itself: `if self._stop.is_set(): link.close(); break` (`:484-489`) - nobody else holds a reference to it | none posted | `tests/test_reconnect.py:899 test_a_link_opened_after_the_join_deadline_is_closed_not_leaked` |
| loop closed while the reader is still running | n/a | `_post` swallows `RuntimeError` only when `self._loop.is_closed()`, and re-raises otherwise (`:545-550`) - so a genuine bug is not masked | `tests/test_reconnect.py:929 test_a_callback_posted_after_the_loop_closed_is_dropped_not_raised` (its `_ClosedLoop` stub overrides `is_closed`) |
| exception mid-read | `finally` at `:512-523`: `cancel_write()` then close under `_write_lock`, both suppressed; `self._link = None` | `_on_error` then `_on_disconnect` posted, the loop continues into the retry wait | the reconnect suite (`tests/test_reconnect.py`) drives this on every reconnect case |
| exception in `_resolve_device` (port enumeration hiccup) | n/a | charged to the retry, thread survives (`:459-465`) | covered in `tests/test_reconnect.py` device-lookup cases |
| join queued behind other executor work | the join owns a private `_join_pool` (`serial_link.py:42`) no other caller can reach | - | `tests/test_reconnect.py:499 test_reader_join_does_not_queue_behind_the_default_executor` (starves the default pool to a single occupied worker) |
| stranded queued rx lines at detach | - | counted into `rx_dropped` and reported as a sys row (`:355-368`); in-flight sys-row tasks are awaited with a 2 s cap, then cancelled, so none dies pending at loop close (`:374-380`) | `tests/test_reconnect.py` detach cases |

Verdict: **complies**. Every enumerated scenario has both a handle disposition and a callback disposition, and each has a test asserting on text or state unique to it.

### 2. Sim serving thread - `sim.py:872` (`threading.Thread(target=serve, name="mcu-sim", daemon=True)`)

Owns a listening socket, not a device handle. `SimHandle.stop()` (`sim.py:845-850`) sets the event, closes the listener, joins with a 2 s timeout. The listener close is ALSO in the thread's own `finally` (`:867-870`), so the two orderings converge on one place - which is the point: a listener left bound with no thread behind it keeps completing handshakes out of the backlog and the daemon reconnects to a corpse.

- join timeout: the socket is already closed by `stop()` before the join, so an outliving thread holds nothing bindable.
- exception mid-serve: `serve_listener`'s per-client guard (`:696-698`) keeps the thread alive; anything escaping `serve_listener` still hits the `finally` close.
- loop closed: n/a, this thread never touches the event loop.

Tests: `tests/test_sim_tcp.py:89 test_spawn_closes_the_listener_when_its_serving_thread_ends`, `:107 test_spawn_stop_is_idempotent`, `:139 test_a_second_listener_on_a_live_port_is_refused`.

Verdict: **complies**.

### 3. `_join_pool` - `serial_link.py:42` (`ThreadPoolExecutor(thread_name_prefix="mcu-join")`)

A module-level pool reserved for the reader join. Never shut down explicitly; `ThreadPoolExecutor` joins its workers at `atexit`, and its workers only ever run `Thread.join(2.0)`, so the bound on process exit is the JOIN_TIMEOUT. Holds no handle.

Verdict: **exempt because it owns no handle and its work item is itself bounded by JOIN_TIMEOUT.** (Its existence is class 1's fix, and it is asserted by `tests/test_reconnect.py:499`.)

### 4. `match_executor()` - `store.py:314-335`

Bounded named pool for `regex` matching. Holds no handle; work items are bounded by the per-call regex `timeout=`. The registry's class 1 entry notes the pool is bounded specifically so an unbounded one cannot delay exit via `atexit`.

Verdict: **exempt because it owns no handle and every work item is timeout-bounded.**

### 5. Default-executor `asyncio.to_thread` workers

15 call sites: `serial_link.py:348` (`_close_link_locked`), `:944` and `:970` (`_write_bytes`), `server.py:340, 927, 960, 1008, 1028, 1059, 1079, 1106, 1142, 1243`, `update_check.py:275, 282`, `cli.py:490`.

Only the `serial_link` three touch a device handle, and all three take `_write_lock`, which is the same lock the reader's close takes - so a write in flight cannot have the handle closed underneath it (`serial_link.py:886-900`, and the Windows FILE_FLAG_OVERLAPPED reason is stated there). The rest are file/config/enumeration work.

- join timeout: not applicable, none of these is joined.
- loop closed: `to_thread` results are awaited by a task the loop cancels at shutdown; a worker outliving the loop finishes into a cancelled future, which is discarded. No handle is left open because the handle disposition is the reader's `finally` or `_close_link_locked`, both of which run inside the worker.
- exception mid-call: `_write_bytes` translates a closed/broken handle into `PortError` so `send_command` cleanup runs and the endpoint returns an envelope, not a 500.

Verdict: **complies**.

### 6. `threading.Timer` for the browser launch - `daemon.py:346-348`

`timer.daemon = True`, fires `webbrowser.open` once, 1 s after start, only under `--open`. Holds no handle, touches no loop, and a daemon thread cannot outlive the process.

Verdict: **exempt because it is a one-shot daemon timer holding no handle and touching no loop.**

### 7. Store writer - `store.py:495` (`asyncio.create_task(self._writer())`)

Not a thread: a task on the loop. Listed because a sweep that omits it cannot claim to have enumerated the daemon's concurrent workers. Its teardown has the same shape as a join: `stop()` (`store.py:508-536`) queues the sentinel, waits 5 s, cancels on the deadline, then `_fail_pending_writes` resolves every future the writer never reached - so nothing dies pending at loop close.

Test: `tests/test_regressions.py:1158 test_store_stop_fails_queued_writes_instead_of_stranding_them`.

Verdict: **complies** (as a task; **exempt** from the handle clause, it owns a sqlite connection closed by `stop()`, not by a thread).

### 8. Retention / initial-sweep tasks - `store.py:498-499`

Loop tasks, cancelled by `stop()`. No handle, no thread.

Verdict: **exempt because they are loop tasks owning no handle.**

---

## Class 15. Shipped artifact vs stand-in

Sweep: enumerate the deliverables and name the test or CI job that exercises each IN SHIPPED FORM. The registry names them: the three console scripts, wheel contents, web UI and vendored assets, exports, the `tools/mcu_sim.py` shim. **7 deliverables** (the three scripts counted separately, since the origin defect was one script covered and another not).

Sources read: `.github/workflows/ci.yml` (all jobs), `.github/workflows/release.yml` (all jobs), `host/pyproject.toml`.

| # | Deliverable | Exercised in shipped form by | Verdict |
|---|---|---|---|
| 1 | `mcuscoped` console script | ci.yml:227 "Install the built wheel and run its console scripts" (`$RUNNER_TEMP/wheelcheck/bin/mcuscoped --version`, from the built wheel in a clean venv, Linux); ci.yml:268 wheel smoke (windows) runs `Scripts\mcuscoped.exe --version` and throws on `$LASTEXITCODE`; release.yml:164 repeats it on the release artifact. Editable-install coverage additionally at `tests/test_scaffold.py:61 test_console_scripts_run` | complies |
| 2 | `mcu` console script | same three jobs (`mcu --version` / `mcu.exe --version`), plus `tests/test_scaffold.py:61` | complies |
| 3 | `mcu-sim` console script | same three jobs, via `--help` (it has no `--version` and its bare form binds a listener - the workflow comment names exactly this). This is the deliverable the registry's own note flags: "test_scaffold covers mcu-sim only against the editable install, so a break in its entry point (or its removal from [project.scripts]) shipped undetected" - closed by the explicit third invocation | complies |
| 4 | Wheel + sdist contents | ci.yml:149 "Assert package data is present in the built artifacts": derives the expected file list by `rglob` over `mcuscope/webui/`, so a newly added asset is covered the moment it is committed; cross-checks 5 hard-coded sentinels against the source tree so the derived list cannot be vacuously empty; asserts exactly 1 wheel and 1 sdist; asserts each file is present AND non-zero-size in the wheel and present in the sdist. Plus `uvx twine check dist/*` (ci.yml:221, release.yml:132). release.yml:111 repeats a sentinel check on the release artifact | complies |
| 5 | Web UI + vendored assets (`uPlot.iife.min.js`, `uPlot.min.css`) | the derived-list check above covers every file including `vendor/`, with the empty-file case called out separately (a zero-byte vendored bundle installs and imports fine and only fails at the user's browser). Behaviour of the JS itself: `tests/test_webui_js.py` -> `node --test` over the same source files the wheel ships, with ci.yml:100 asserting the JS tests actually RAN rather than skipped | complies. Note: the pixels are not covered - a browser render of the UI served from an installed wheel remains the standing manual leg (docs/SCREENSHOTS.md), not a CI job. That is a known, recorded open leg, not a new finding |
| 6 | Exports (session capture file, `log export`, `plot export` CSV/JSON) | `tests/test_cli.py:1067, 1560, 1576, 1586, 608, 623, 636`; failure paths at `:649, 2297, 2353, 2361, 2431`. These drive the CLI as `python -m mcuscope.cli` rather than the console script, but the *artifact* here is the written file, and it is produced byte-for-byte by the same shipped code either way - the only shipped-form divergence the entry point could introduce is stream/encoding setup, and the export writes pass explicit `newline=` (class 2) with the tests asserting on file bytes | complies |
| 7 | `tools/mcu_sim.py` back-compat shim | ci.yml:111 "Simulator TCP smoke (python tools/mcu_sim.py, >1 ping)" -> `tools/ci_sim_smoke.py`, which spawns the shim at `tools/mcu_sim.py:78` and exchanges a real `>1 ping` over TCP. No `if:` guard, so it runs on the full 2 OS x 3 Python matrix. This is the deliverable nothing in pytest covers - the suite drives the simulator core in process (`link.SourceLink`), never the standalone script and its listener - and the workflow comment says so | complies |

No violates.

Cross-check on the workflows themselves (the registry's third bit, "Windows CI jobs that never ran"): the `test` job matrix is `[ubuntu-latest, windows-latest] x [3.11, 3.12, 3.13]` with `fail-fast: false`, and both `wheel-smoke (windows)` jobs (ci.yml:251, release.yml:147) declare `needs: build` and have no `if:` gate. Two "did it actually run" guards exist for the skip-prone legs: ci.yml:92 (firmware C tests) and ci.yml:100 (web UI JS tests), both grepping the pytest `-rs` report. Verdict: **complies**.

---

## Class 16. One bad item ends the loop

Sweep: every loop that processes external input, asked BOTH questions -
(a) does one bad item end the loop?
(b) does a guard that keeps looping still recognise the errors that are not per-item (a dead fd, a permanently broken source)?
Plus the staged/buffered twin of any guarded loop.

**41 loop sites**, all listed. Derived from `grep -n "for |while "` over `host/mcuscope/*.py` and `grep -n "for (const|forEach|for (let|while ("` over `host/mcuscope/webui/*.js` (105 raw hits), then filtered to loops whose items come from outside the process (UART bytes, socket data, HTTP/WS responses) and their internal twins.

### Simulator (`host/mcuscope/sim.py`) - 6 sites

| # | Loop | (a) one bad item | (b) non-per-item errors | Verdict |
|---|---|---|---|---|
| 1 | `serve_listener` accept loop, `:669` | per-`OSError` guard prints and retries after `ERROR_BACKOFF_S`; the client session is separately guarded at `:696-698` with the close INSIDE the guard (`with conn:` used to let the implicit close escape) | yes: `srv.fileno() == -1 or exc.errno in _FD_DEAD_ERRNOS` breaks (`:679-681`) | complies |
| 2 | `_serve_socket_client` recv loop, `:714` | `BlockingIOError`/`InterruptedError` continue | `OSError` returns; empty chunk returns | complies |
| 3 | `_sock_send_lines` send loop, `:783` | this is the registry's "mirror's mirror" site: `BlockingIOError` is classified as a live-but-slow reader (continue on writability), matching the recv side three lines up | yes: hard `OSError` returns False, and `SEND_STALL_TIMEOUT_S` with zero bytes accepted ends the session, so a permanently stuck peer is not retried forever. The select is sliced to 0.5 s so a stopping sim is not wedged for the whole budget | complies |
| 4 | `serve_pty` loop, `:918` | per-iteration `except Exception` resets `sim`/`rx` and keeps serving, mirroring what a fresh TCP accept does | yes: `isinstance(exc, OSError) and exc.errno in _FD_DEAD_ERRNOS` breaks (`:937-940`) - this is the exact site the registry's mirror-image note came from, and the fix is present | complies |
| 5 | `_process_incoming` segment loop, `:581` | each segment is length-capped and each complete line goes through `sim.handle_line`, which returns error responses rather than raising (`:146-176`); an overflowed line is answered `ERR 8 overflow` and `continue`s | n/a (pure) | complies |
| 6 | `encode_lines` loop, `:743` | an oversized line is truncated (or answered `ERR 8`) per line and reported, never raised | n/a (pure) | complies |

### Serial link (`host/mcuscope/serial_link.py`) - 6 sites

| # | Loop | (a) one bad item | (b) non-per-item errors | Verdict |
|---|---|---|---|---|
| 7 | `_reader` outer reconnect loop, `:452` | a failed `_resolve_device` becomes `msg` and a retry (`:459-465`, the fix for a setupapi hiccup killing the thread); a failed open becomes `_on_error` + retry | yes: `self._stop.is_set()` and `_retry_wait` returning None both break; presence gating (`_device_present`) keeps an absent device cheap. An indefinite retry on an absent device is deliberate (a replug must reconnect) | complies |
| 8 | `_reader` inner read loop, `:493` | the `except Exception` at `:511` charges the read error and falls through to `finally` (close + `_on_disconnect`) and then the retry | yes: same stop event; the handle is always released in `finally` | complies |
| 9 | `_on_bytes` parts loop, `:652` | an oversized terminated line is counted into `rx_dropped` and `continue`d, with a once-per-episode sys row; the unterminated case at `:629-641` is counted too | queue overflow sheds oldest and reports once per episode (`:673-681`) | complies |
| 10 | `_consume`, `:687` | `except Exception` around `_store_rx_batch` logs and keeps consuming | `asyncio.CancelledError` is re-raised first (`:701`), so shutdown is not swallowed | complies |
| 11 | `_store_rx_batch` submit loop, `:723` | **this is the registry's origin instance.** Now a per-line `try` with `_drop_rx_line`, counted into `rx_dropped` and reported once per episode | `CancelledError` re-raised | complies |
| 12 | `_store_rx_batch` settle loop, `:731` | the staged twin of #11 over the same batch - and it carries the same per-item guard and the same `CancelledError` re-raise | as above | complies (the guard travelled with the twin) |

### Store (`host/mcuscope/store.py`) - 4 sites

| # | Loop | (a) | (b) | Verdict |
|---|---|---|---|---|
| 13 | `_writer` outer loop, `:600` | a batch that fails `executemany` is redone row by row to isolate the bad row (`:625-632`); if the row-by-row pass itself fails, this batch's callers are failed and the loop CONTINUES (`:637-651`) - the comment names the consequence of letting it escape (queue fills, serial consumer blocks for good); a commit failure fails the batch, rolls back and continues (`:661-673`) | yes: the `None` sentinel returns, and `stop` is honoured on every failure path (`:648, 671`) | complies |
| 14 | `_writer` batch-absorb loop, `:612` | pure queue draining, bounded by `_MAX_BATCH_ROWS`; the `_Drain` barrier is released in `finally` on every exit including failures (`:685-688`) | as above | complies |
| 15 | `iter_plot_export` fetchmany loop, `:1874` | rows are our own writer's, already validated at ingest | breaks on an empty batch; the private connection is closed in `finally` | exempt because the items are rows this process wrote, not external input |
| 16 | retention delete-chunk loops, `:1986`, `:2134`, `:2151` | as above | each breaks on `n == 0` and yields to the loop between chunks | exempt, same reason |

### Server (`host/mcuscope/server.py`) - 6 sites

| # | Loop | (a) | (b) | Verdict |
|---|---|---|---|---|
| 17 | WS `pump()`, `:1550` | rows are store rows (already ingested and validated); a `send_text` failure is how a vanished peer surfaces, and it ends the pump by design | yes: `asyncio.wait(..., FIRST_COMPLETED)` at `:1602` means either half ending ends the connection - the fix for a socket that had lost its pump looking healthy forever | complies |
| 18 | pump coalesce loop, `:1568` | bounded by `WS_BATCH_MAX`, breaks on `QueueEmpty` | n/a | complies |
| 19 | `watch()` receive loop, `:1592` | returns on `websocket.disconnect`, `WebSocketDisconnect` and `RuntimeError` | yes | complies |
| 20 | `CaptureWatch.next_batch` drain, `:1729` | bounded `get_nowait` drain; shed rows counted via `take_dropped` | n/a | complies |
| 21 | `_do_wait` window loop, `:1784` | matching runs off-loop with a per-call and whole-query timeout (`_match_timeout`) | yes: `remaining <= 0 or candidates is None` ends it, and `watch.close()` is in `finally` | complies |
| 22 | `_do_assert` window loop, `:1990` | as above, plus a forbid hit breaking early | yes: `remaining <= 0` breaks | complies |

CSV export generators (`server.py:2086`, `:2099-2116`) iterate store rows: **exempt because the items are rows this process wrote**.

### CLI (`host/mcuscope/cli.py`) - 6 sites

| # | Loop | (a) | (b) | Verdict |
|---|---|---|---|---|
| 23 | `log` / `_tail_snapshot` print loops, `:362`, `:397` | one malformed row raises out of `fmt_line` (`cli_output.py:181`, a bare `row['ts']`) and ends the print | n/a (one-shot) | complies - a one-shot query refuses as a WHOLE rather than emitting a partial answer that reads complete, and the refusal is a mapped exit code, not a traceback: `tests/test_cli.py:2243 test_a_null_field_in_a_daemon_body_is_an_exit_code_not_a_traceback` plus the class-wide sweep at `:2261` over 6 commands. Partial output + exit 0 would be the worse contract here |
| 24 | `_follow_ws` `handle()` row loop, `:556` | per-row `try` charging `KeyError/TypeError/ValueError` to `_DropCounter` and continuing; control objects (`gap`, `capture`) recognised by their own key rather than by the absence of `id`, so a row that merely lost its id is still charged; JSON decode failure charged per frame | yes: `emit_stream` is deliberately OUTSIDE the guard, so EPIPE ends the follow; `BrokenPipeError`, `OSError` (exit 3) and `ConnectionClosed` (incl. the 1008 auth case) are handled outside the loop | complies |
| 25 | `_follow_ws` recv loop, `:591` | each payload goes through the guarded `handle()` | yes: the `except BaseException` at `:611` consumes the handed-back `pending` recv so the socket teardown does not leave it unretrieved | complies |
| 26 | `_stage_backfill` staged drain, `:600` | the staged twin of #24, and it calls the SAME `handle()` - the guard travels with the twin by construction rather than by duplication | as above | complies |
| 27 | `can dump -f` poll loop, `:1094` | a failed poll is charged to `polls` and retried (the fix for one transient httpx error ending the follow with a traceback) | yes, and this is the registry's mirror question answered explicitly: `_poll_frames` (`:1147`) dies on `httpx.InvalidURL` and on any 4xx - answers no retry can change - while transport failures/timeouts/5xx are counted and retried; and `FOLLOW_GIVE_UP_S` is measured against the monotonic clock, not an iteration count, because each failed poll can pay the 10 s connect timeout | complies |
| 28 | `can dump -f` frame loop, `:1130` | per-frame `try` charging to `frame_drops` and continuing; two SEPARATE counters, because a poll and a frame are different items and sharing one made 149 undecodable frames turn a transient error into "unreachable for 30s" after 0.011 s | `emit_stream` outside the guard | complies |

### Web UI (`host/mcuscope/webui/*.js`) - 13 sites

| # | Loop | (a) | (b) | Verdict |
|---|---|---|---|---|
| 29 | `api.js:583` live `onmessage` row loop | per-row `try/catch` logging "row dropped"; staging re-checked per ROW (not per frame) so a capture token applied mid-frame routes the rows behind it to the re-seed | yes: `sock.onclose` handles socket death, code 1008 goes to the auth path and does NOT re-enter the backoff, `onerror` closes | complies |
| 30 | `api.js:650` `drainStaging` queue loop | the staged twin of #29. Segments at capture tokens rather than sorting across them, then... | ... | complies |
| 31 | `api.js:647` `flushSegment` inner loop -> `feedStaged` (`:658`) | ...`feedStaged` carries the same per-row `try/catch`, with a comment naming the twin relationship. **This is the exact class-16 staged-twin instance from 2026-08-11, and the guard is present** | `staging` re-armed mid-drain re-stages rather than racing the re-seed | complies |
| 32 | `api.js:489` backfill rows loop | per-row `try/catch` with a `bad` accumulator reported once at the end, plus the gap-divider push separately guarded (`:485-487`) | outer `catch` reports via `hooks.reportError` - silence used to make a failed backfill indistinguishable from an idle target | complies |
| 33 | `api.js:240` `seedPlotDefs` row loop | per-row `try/catch`, `bad` accumulator, one console.error per episode | outer catch is non-fatal by design (the seed only ADDS definitions) | complies |
| 34 | `api.js:365` `oldestId` min scan | skips non-numeric ids rather than trusting the last element - a malformed row must not decide where the next page starts | n/a | complies |
| 35 | `api.js:315` `seedPlotHistory` per-channel map | per-request `try/catch` inside the map, deliberately NOT `Promise.all`'s all-or-nothing: one channel's failure returns `points: []` instead of discarding every other channel's history | outer catch non-fatal | complies |
| 36 | `plots.js:355` `plotSeed` group loop | **no per-entry guard**, unlike its siblings #29/#32/#33. Malformed entries are filtered at the head of the function (`:349`) and every per-point malformation is dropped inside `mergeSeedSeries` (`:295` non-numeric `line_id`, `:301` non-finite `value`, the class-6 gate) | outer `try/catch` in `seedPlotHistory` | **complies** - probed, see below |
| 37 | `plots.js:293/294` `mergeSeedSeries` nested loops | per-point `continue` on a bad `line_id` or a non-finite value | n/a | complies |
| 38 | `plots.js:265` `routePoints` points loop | items are `[name, value]` pairs built by #37, already filtered | n/a | complies |
| 39 | `plots.js:131` `parseEnumLabels` loop | returns `null` for the whole definition on any bad item, never throws - and rejecting the whole `!pd` is correct, it mirrors the daemon's own decode so the UI and `mcu plot` cannot disagree about the same definition | n/a | complies |
| 40 | `digital.js:39` `digitalIngest` points loop | non-finite `x` returns before the loop (class-6 gate); the lane cap warns once (`laneCapWarned`) and `continue`s rather than throwing | n/a | complies |
| 41 | `terminal.js:267/283/321` and `can.js:83/167/270` render loops | items are rows already through the guarded `handleWsRow` path and held in the shared buffer | n/a | exempt because the items are internal, post-guard state, not external input |

#### Probe for site 36 (`plotSeed`)

`plotSeed` is the seed-path twin of the live `plotIngest` path, and it is the one row-ish loop in the UI without an explicit per-item guard. Probed with 8 malformed daemon-shaped channel payloads, each paired with a healthy second group under a different `sid`, checking whether the healthy group still gets seeded:

```
$ node /tmp/claude-1000/review-r2/probe_plotseed2.mjs
ok     labels non-array (enum)    later-group-seeded=true
ok     lanes non-array (bits)     later-group-seeded=true
ok     name is a number           later-group-seeded=true
ok     name is null               later-group-seeded=true
ok     kind is an object          later-group-seeded=true
ok     scale non-numeric          later-group-seeded=true
ok     channel has no name        later-group-seeded=true
ok     points is a string         later-group-seeded=true
```

No reachable per-item failure: the head filter plus `mergeSeedSeries`'s two per-point rejections mean nothing malformed survives to `routePoints`. **Verdict: complies.** Recorded as an asymmetry worth knowing about (its three siblings guard explicitly and say why in a comment), not as a defect - adding a guard here would be speculative.

### Both questions, summarised

Question (b) - a guard that keeps looping must still recognise a dead fd or a permanently broken source - is answered explicitly at every guarded site: `_FD_DEAD_ERRNOS` in the sim's accept loop and its pty sibling; `SEND_STALL_TIMEOUT_S` plus hard-`OSError` in `_sock_send_lines`; `CancelledError` re-raised ahead of the broad catch in `_consume` and both `_store_rx_batch` halves; the `stop` sentinel honoured on every failure path in `store._writer`; `FIRST_COMPLETED` across the WS pump and watch; `_poll_frames`'s 4xx/InvalidURL refusals plus the monotonic `FOLLOW_GIVE_UP_S` in `can dump -f`; `emit_stream` kept outside both CLI follow guards so EPIPE ends the follow; `onclose`/`onerror`/1008 outside the browser row loop.

No violates.
