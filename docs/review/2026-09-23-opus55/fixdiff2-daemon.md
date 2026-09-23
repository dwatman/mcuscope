# Fix-diff leg 2: daemon slice (2026-09-24)

HEAD: `b889e2b8fb848866054da6ba09cd2e651d9f69c7` (the diff under review is `b994076..4462986`; `b889e2b` adds review docs only, no `host/` change).

Scope: `server.py`, `store.py`, `serial_link.py`, `daemon.py`, `config.py`, `sim.py`, `pyproject.toml` hunks, their tests, and the SPEC 3 / ARCHITECTURE hunks.
Scratch: `~/tt-data/mcuscope-2026-09-24/fixdiff2/daemon/` (`host/` is a copy of `4462986`; `tests/test_zz_probe.py` asserts `mcuscope.store`/`server` import from it; probes, `mutate.py`, `logs/`).

## Findings

### 1. Medium: a `wait=1` export whose client leaves still runs a build, and waiters are unbounded
- `server.py:2857` parks a waiter on `export_freed`; nothing watches for `http.disconnect`, and uvicorn never cancels a handler when its client goes.
- SPEC 3.4 (`SPEC.md:866`, "a client that leaves while waiting starts no build") and the `_run_export` docstring (`server.py:2851`) are therefore false under the real server.
- Scenario: the pool is busy, and a user clicks the `.db` download five times and cancels each one.
  - Each click leaves a waiter, and there is no cap on waiters.
  - Every waiter is admitted in turn and copies the whole session, and each result is written to a closed socket.
- Confirmed, driven (`probe_waiter_disconnect.py`): real uvicorn, 4 builds admitted, then a `wait=1` client with a 0.5 s timeout that gave up.
  - After release: `builds entered 5 (admitted 4)`, so the departed waiter ran a build.
- Test gap (class 27): `test_a_waiter_that_disconnects_claims_no_slot_and_leaves_no_file` cancels an `httpx.ASGITransport` task, which cancels the app in-process. A TCP disconnect does not do that.
- Fix:
  - While parked, race `export_freed.wait()` against a task awaiting `request.receive()` for `http.disconnect`, and return on disconnect.
  - Cap the number of waiters (503 past it).
  - Drive the test through a real socket (the `support` stack) with a client timeout.

### 2. Low: `_top_ts` never falls, so a purge leaves every new row counted as late
- `store.py:1023` only raises `_top_ts`, and `store.py:673` seeds it only at start. No delete path re-reads it.
- Scenario: the clock ran an hour fast and was stepped back (announced correctly). The user then runs `purge --all`.
  - For the next hour every row counts toward the old episode, although nothing stored is ahead of the clock.
  - A genuine inversion in that hour (a loop stall) gets no start row, because `_late_rows > 0` suppresses it.
  - The eventual "back in time order; N committed" row counts rows that no window could miss, and the capture no longer holds its start row.
- Confirmed, driven (`probe_top_ts_purge.py`): after `delete_range` emptied the table, 5 new rows gave `late rows counted after the purge: 6`, `top_ts ahead by 3602 s`, and no sys row.
  - Positive control: a restart reseeds to 0 s ahead.
- Fix:
  - After any delete that can take the newest `ts` (`delete_range`, `purge before_ts`, the identity reset), re-read `_top_ts = MAX(ts)` (one seek).
  - If an episode is open and nothing stored is ahead any more, close it.

### 3. Low (reasoned, Windows): the CTRL_CLOSE hold is shorter than the graceful wait it has to cover
- `_stdio.py:89` holds the close for 4.5 s. `daemon.py:129` `GRACEFUL_SHUTDOWN_S = 5` lets uvicorn wait up to 5 s for in-flight requests before the lifespan finaliser runs.
- `Server.handle_exit` wakes only the long polls.
- Scenario: close the console window while a session export or bundle is building, or while a large `/lines/export` is streaming (or a `wait=1` waiter is admitted during shutdown).
  - Windows ends the process when the handler returns at 4.5 s, which is before store stop.
  - There is no `daemon stop` row, the session stays open until the next start, and the pid record stays behind.
  - SPEC 3.2 (`SPEC.md:470`, "the same graceful shutdown") overstates this. On POSIX SIGHUP the same case completes in about 5 s.
- Confirmed: reasoned only, no Windows machine.
- Fix, either:
  - on CTRL_CLOSE, shorten the graceful wait (for example set the server's `timeout_graceful_shutdown` to about 2 s before `interrupt_main`) and abandon export jobs at `handle_exit`;
  - or qualify the SPEC line.
  - Then add a Windows-leg item.

### 4. Low (cross-slice, CLI owns the fix): the port-column rule counts attached and stored ports separately
- `cli.py:893` (`_stream_port_column`) and `server.py:3376` (`_several_ports`) show the column only when more than one port is attached, or more than one port has stored rows. They never test the union.
- Scenario: board `a` was captured and detached; board `b` is attached and has not printed yet.
  - `mcu tail -f` backfills `a`'s last lines, then follows `b`'s live rows, both without `[port]`.
  - The daemon's own text export is not affected: its window is frozen at the request, so it holds only stored ports.
- Confirmed, at the function level: with attached `[b]` and stored `[a]`, both functions return `False`.
- SPEC 3.4 (`SPEC.md:778`, "more than one port is attached or has stored rows") reads naturally as the union. The test `test_the_column_rule_counts_attached_and_stored_ports_apart` pins the counts, not the union.
- Fix: `len({attached aliases} | set(stored)) > 1` in `_stream_port_column`; use the same in `_several_ports` for one rule (class 19).

### 5. Nit: the loader judges blankness stripped but keeps the unstripped value
- `config.py:469` says "Stripped, as the PUT judges them", but `device`/`serial_number` load as written. PUT /config/ports stores them stripped.
- Scenario: a hand-written `serial_number = " 0672FF3 "` loads with no warning, and `serial_link.py:480` compares it raw, so the port never resolves. A save from the dialog silently fixes it.
- Confirmed, driven (`pad.toml`): loaded as `' 0672FF3 '` and `' /dev/ttyACM0'`, 0 warnings. The unstripped half predates this diff.
- Fix: strip both in the loader, as the PUT does.

### 6. Nit: `pydantic>=2.0.2` has no upper bound, and SPEC's dependency list does not name it
- `pyproject.toml:44`: every other runtime dependency carries `<1.0`. Only fastapi's own current range stops pydantic 3.
- `SPEC.md:341` lists the direct dependencies "exactly", and pydantic is now one.
- Fix: `pydantic>=2.0.2,<3`, and name it in SPEC 3.1 (fastapi's pydantic, pinned to v2).

### 7. Nit: stale sim comment
- `sim.py:280-283` still says `x` is "passed to the port layer" and "matching is defined over id/mask alone". `x` now also selects the frame kind (`sim.py:301`).

### 8. Nit: two changed branches no test distinguishes (revert-verified)
- `store.py` `_scan_plot_summary`: `own = not c.in_transaction`, mutated to `own = True`, SURVIVED `test_store_fixdiff_reads`, `test_store_plot_reads` and `test_store_plot_summary`.
  - No caller runs it inside a transaction (the offload's cached read connection, or the `:memory:` writer connection between batches).
  - Delete the guard, or test a caller that is inside one.
- `store.py` `_check_stamp_order`: removing `req.future.add_done_callback(...)` SURVIVED `test_store_fixdiff_writer`.
  - It only silences asyncio's "exception never retrieved" log on a failed notice. Add a failed-commit case asserting no such log, or accept it.

## Checked, nothing found

- Mutants in `logs/mutants.log`: 28 of 30 KILLED. They cover:
  - the loader's dedupe, stripped-blank, length and control-char skips;
  - `_several_ports` (both halves), `/ports` `stored` excluding `""`, the bundle and export column wiring;
  - the `empty` verdict for dropped lines, and the forbid decision before the expect scan;
  - the `/purge` id bound and the `n == 0` early return;
  - the `export_freed` clear and set (both hang without them), the progress handler, the index-only stamping, `ppid`, `serial_number`;
  - the reconnect identity and disconnect checks (including `was_held` read early), and the stop cause wording;
  - the sim `x` filter and the `!m @tick` cut, `stored_ports`' floor, the console hold, and the ctrl-handler idempotence.
- `stored_ports()` callers: `/ports`, `_several_ports` and the plot rebuild (`include_daemon=True`). No other caller relies on `""`. The plan is `SEARCH ... COVERING INDEX idx_lines_port_id` with and without the floor (EXPLAIN).
- Reconnect `replaces`: a port held before the reconnect still resumes; a concurrent second reconnect is refused as a re-attach. No caller still passes `require_existing`.
- `stop(cause)`: every `_detach_locked` caller has the right cause. Shutdown keeps "detach". No SPEC text names the cause.
- `_check_stamp_order`: every `_WriteReq` carries `ts`. The notice is counted in `write_errors` on a failed commit. All row writers stamp `time.time()`, and no API lets a client supply `ts`.
- EWMA: a `dt` of 0 (the Windows monotonic tick) adds `n/tau`, and a large first `dt` decays to 0 with no underflow.
- The cancel during a hold fails `req` once, and `_fail_queued` cannot double count it.
- `delete_before_ts(max_id=)`: the chunk select is `ts < ? AND id < max_id+1`. Rows committed later always have larger ids.
- `_scan_plot_summary` BEGIN/rollback: the connections use legacy `isolation_level`, so `rollback()` ends the snapshot (on Python 3.10 floor and 3.13).
- Index build notice, driven end to end:
  - a 4M-line capture lacking `idx_lines_port_chan_id`, then `mcu daemon start --timeout 2` (repo CLI, throwaway `--config`, `MCUSCOPE_*_DIR` in scratch);
  - it printed the waiting note and started in 10.5 s; the err file holds `building index ...` and then `built index ... in 9.7 s`; `daemon stop` was clean.
  - The repo's `cli.py` had other agents' uncommitted edits at the time.
- `_run_export` wait loop: no await between the re-check and the claim; a spurious wake re-parks.
- `_NoCacheStatic.file_response`, driven under real uvicorn (h11):
  - the stamp is applied, `If-None-Match` answers 304, HEAD answers with no body, and keep-alive after HEAD works;
  - the signature matches starlette 0.44 and 1.3.1.
- `_FrameDenial` on every scope: lifespan and WS messages pass through unchanged.
- `/assert` restructure: `forbid_scan`/`expect_scan` are never read unbound. A forbid hit ends the batch before the expect scan, as before the first fix-diff.
- The sim `_cut_event` matches monitor.c `event_end` on:
  - `!m @7`;
  - `!m @` (sent);
  - `!m @7x`;
  - space runs;
  - no space;
  - a type longer than 16.
- Windows, reasoned:
  - the new tests use explicit stamps or scale with elapsed time;
  - `test_link_fixdiff_console` fakes kernel32 on Linux and uses the real ctypes on Windows;
  - the shim test is skipped on win32;
  - `index.html` stamping is newline-agnostic;
  - `getppid()` exists on Windows.
- SPEC 3 hunks against the code (`ppid`, port `serial_number`, `stored`, reconnect 400s, `wait=1`, the `/assert` reason strings, the `/purge` bound, the stamp-order row texts, the schema index, the 3.2 index notices, the EWMA line) and the ARCHITECTURE hunks all match, except findings 1, 3 and 4.
- Tests, each file run alone from the copy (`logs/summary.txt`): 25 files, all pass.
  - The 10 new files pass on the old floor venv (Python 3.10, fastapi 0.115.7, starlette 0.44, uvicorn 0.35, pydantic 2.13.5).
  - A `--resolution lowest-direct` venv now resolves pydantic 2.0.2 (`floor-venv/`). On it, 12 files pass, including e2e, assert, request_validation, config_api and regressions (`logs/floor-pyd202.txt`).

Not verified: anything on Windows (findings 3 and 4 are reasoned or function-level), and the whole suite.
