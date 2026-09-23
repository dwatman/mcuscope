# Lifecycle review, 2026-09-23

Aspect: daemon lifecycle, concurrency and resource hygiene.
Tree: `6e4f6f7` (branch review/2026-09-23-opus55), Linux only.
Probe scripts and logs: `~/tt-data/mcuscope-2026-09-23/lifecycle/`.

## LIFECYCLE-1: `mcu daemon stop` signals a local process that has the pid of a daemon on another machine

- Severity: HIGH. CONFIRMED.
- Where: `host/mcuscope/cli_daemonctl.py:259-271` (`_stop_running_daemon`), `:294` (`_wait_daemon_gone`), `:334` (`os.kill`); reached from `host/mcuscope/cli.py:2565` (no-record branch).
- Failure: the pid `/status` reports is treated as a local pid whatever host the URL reaches.
  - Non-loopback URL (`mcu --url http://<lan-host>:8558 daemon stop`, or `MCUSCOPE_URL` set for a remote bench):
    - `/shutdown` answers 403 ("shutdown is a local operation"), so the CLI falls straight back to `os.kill(<remote pid>, SIGTERM)` on this machine.
    - It kills whatever local process owns that number, then exits 1 with "a process is still answering".
  - Loopback URL that is tunnelled to another host (`ssh -L`, a published container port):
    - `/shutdown` is accepted and the remote daemon exits.
    - The CLI then waits `DAEMON_STOP_GRACE_S` (10 s) for the *local* pid to vanish, SIGTERMs it, and reports `stopped mcuscoped (pid N)`, exit 0.
  - On Windows the fallback is `TerminateProcess`: a hard kill of the unrelated process (not driven).
- Repro: `fake_remote.py` answers `/status` with the pid of a local `sleep 600`, and answers `/shutdown` as a remote daemon would.
  - Refuse mode (403, as `server.py:975` answers a non-loopback client): `mcu --url http://127.0.0.1:18625 daemon stop` gives exit 1, and the `sleep` dies with status 143.
  - Accept mode (200, then the server exits after 0.3 s): exit 0 after 10.8 s, "stopped mcuscoped (pid <sleep's pid>)", and the `sleep` is dead.
- Suggested fix: only signal a pid proven to be local.
  - Have `/status` report a host identity (hostname, or `/proc/sys/kernel/random/boot_id` where present).
  - Signal and pid-wait only when it matches this machine.
  - Otherwise rely on `/shutdown` alone and judge "gone" by `/status` going quiet (the `real_pid is None` path).
  - At the least, refuse the signal fallback for a URL whose host is not loopback.
- Class: a new instance near 7 (a pid acted on without proving whose it is). Suggest a registry entry: "a pid from a peer's answer acted on as a local pid".

## LIFECYCLE-2: stopping the daemon during a session export leaves the temp copy beside the capture for good

- Severity: MEDIUM. CONFIRMED.
- Where: `host/mcuscope/server.py:1427` (`await asyncio.to_thread(build)`), and the same shape at `:1565` (bundle).
- Failure: `build()` removes its temp file only when it raises.
  - A handler cancelled by the 5 s graceful-shutdown cap never receives the path, and nothing else knows it.
  - Nothing sweeps `mcuscope-session-*` or `mcuscope-bundle-*` at startup or at a later export.
    The `_TempFileResponse` docstring (`server.py:2481`) says an orphan "sits beside the capture until the next export", which is false: no code removes it.
  - SIGTERM (what `mcu daemon stop` and `POST /shutdown` deliver): the signal replay kills the process mid-INSERT, leaving a partial `.db` plus a hot `-journal`.
  - SIGINT (Ctrl-C on a foreground daemon): `main()` returns and releases the pid record and the capture lock, but interpreter exit joins the export thread.
    For the length of the export the process stays alive and unreachable, and `mcu daemon status` says "not running".
    It then leaves a *complete* copy behind.
- Repro: `export_term.py`, run on a 3 M-row closed session (389 MB capture), with `GET /sessions/1/export` in flight.
  - SIGTERM after 1 s: daemon rc -15 after 5.2 s, client gets 500, and `mcuscope-session-tzt9adms.db` (38 MB) plus its `-journal` are left.
  - SIGINT after 1 s: daemon rc 0 only after 116.6 s, client gets 500, and `mcuscope-session-zyluzym3.db` (389 MB) is left.
- Suggested fix:
  - Keep the in-flight temp paths on `app.state`, and unlink them in the lifespan finaliser.
  - On `CancelledError` in the handler, call `conn.interrupt()` on the export connection (thread-safe in sqlite3), so the copy stops at once and `build()`'s own unlink runs.
  - Add a startup sweep of this capture's export prefixes. Key the prefix by the db name so that another daemon sharing the directory is never touched.
- Class: 49, daemon-side instance (an output left partial on non-completion).

## LIFECYCLE-3: a crashed run's automatic session is closed at the next start with that start's time and rows

- Severity: MEDIUM. CONFIRMED.
- Where: `host/mcuscope/server.py:439-456`. The `daemon start` row is written first, and the stale automatic session is closed after it (by `start_session` at `:454`, or `stop_session` at `:456`).
- Failure: after SIGKILL, SIGHUP, a power cut or a double Ctrl-C, the crashed run's session gets these values:
  - `ended_ts` = the next start's time, however much later that is;
  - `end_id` = the new run's `session end` marker;
  - its span contains the new run's `daemon start` row.
  - `mcu session` listings, session exports and any duration an agent computes all report a run that lasted until the restart.
- Repro: `sig.py KILL 500`, then wait 10 s and start `mcuscoped --config b.toml`.
  - Session 1 `ended_ts` is 10.6 s past its last device row.
  - Its last rows are `(1587, debug, flood line ...)`, `(1588, sys, 'daemon start')`, `(1589, marker, 'session end: ...')`.
- Suggested fix: close a stale automatic session before writing `daemon start`, with `ended_ts` = the `ts` of its last row and `end_id` = the max id at that moment.
  - Put the closing marker's timestamp at the crash, not the restart, or omit the marker for a crashed run.
- Class: none registered. Closest is 17 (the reported value is the time of the request, not the fact).

## LIFECYCLE-4: a reconnect racing a detach brings the detached port back

- Severity: LOW. CONFIRMED.
- Where: `host/mcuscope/server.py:1027-1031` (the reconnect reads the port, then calls `attach`), and `host/mcuscope/serial_link.py:1353` (`replacing` is re-derived under the lock).
- Failure: `attach()` primes before it takes the manager lock. A `DELETE /ports/{alias}` completing during the prime removes the alias, and the reconnect then attaches it afresh.
  - Both requests answer 200, and the device is open again after a detach that reported success.
  - On Windows a COM port is exclusive, so the tool the user detached for cannot open it.
  - No ordering of the two requests produces this result: a reconnect after the detach would answer 400 "no such port".
- Repro: `race_rd.py` (attach `r`, then fire `POST /ports/r/reconnect` and `DELETE /ports/r` together): 40/40 runs end `(200, 200, present=True)`.
- Suggested fix: give `attach` a `require_existing` flag for the reconnect path, and refuse under the lock when the alias is gone.
- Class: none registered. The shape is class 37's (a check before an await, acted on after it).

## LIFECYCLE-5: a start that loses the bind race overwrites the running daemon's startup log

- Severity: LOW. CONFIRMED.
- Where: `host/mcuscope/daemon.py:316-323` (the failed-start report). It is keyed by host:port from `:427`, the same key the winner uses.
- Failure: two starts on one host:port with different `db_path` both pass the port probe. The loser fails the bind and rewrites `mcuscoped-<host>-<port>-startup.log`.
  - The log then says "failed to start, pid <loser>, exit 3" while the winner is serving.
  - For a windowless start, that file is the only trace (ARCHITECTURE, `_stdio`).
  - Both CLIs also open the shared `.err` path with `"wb"` (`cli.py`, `_start_daemon`), so the loser truncates the file the winner's stderr points at (SUSPECTED, not inspected).
- Repro: two concurrent `mcu --url http://127.0.0.1:18626 daemon start -c c.toml` / `-c c2.toml` runs.
  - One exits 0 (pid 172246 serving), the other exits 1.
  - `data/mcuscoped-127.0.0.1-18626-startup.log` then reads `failed to start, pid 172220, exit 3 ... address already in use`.
- Suggested fix: write the failed-start report under a pid-suffixed name, or skip the overwrite when the pid record names a live process other than this one.
- Class: 52 (a per-daemon artefact keyed like the record but without the record's live-owner rule), next to class 7's documented residual.

## LIFECYCLE-6: SIGHUP skips graceful shutdown

- Severity: LOW. CONFIRMED.
- Where: `host/mcuscope/daemon.py:327-359`. Only SIGTERM (and SIGBREAK) get the release handler, and uvicorn catches only SIGINT and SIGTERM.
- Failure: closing the terminal or SSH session of a foreground `mcuscoped` delivers SIGHUP, which kills the process with none of the shutdown steps. It leaves:
  - no `daemon stop` row;
  - the automatic session left open (then mis-closed per LIFECYCLE-3);
  - a stale pid record;
  - queued rows lost.
  - `mcu daemon start` is immune (`start_new_session`).
- Repro: `sig.py HUP 5000` gives rc -1 in 0.01 s, the pid record still present, the session still open, and the last sys row `port f target: ...` (no `daemon stop`).
- Suggested fix: on POSIX, treat SIGHUP as SIGTERM, for example with a handler that calls `signal.raise_signal(SIGTERM)` inside uvicorn's handler, or by adding it to `Server`'s captured signals.
- Class: none. Same family as the class 7 "release on every exit" rule.

## Checked and fine

- 420 s soak on one daemon (`soak.py`): 1992 WS connects (half aborted without a close frame), 1350 port ops (attach/detach of a live sim port and a dead one, hold/reconnect), 270 `/wait`, and 13 sim kills and restarts.
  - Zero failed ops.
  - fds went 16 to 17 (one match-executor read connection).
  - Threads stayed at 6-8 (pool threads), with no growth.
- The same daemon at 2300 s uptime: fds 17, threads 6, `write_errors` 0, `ws_dropped` 0, `rx_dropped` 0, and `writer_alive` true.
- `/status` reported `connected` false 3 s after every sim kill (13/13), and the port reconnected after each restart.
- `mcu status` is truthful for a held port ("held (disconnected on request)"), an absent device ("disconnected (no_device)") and a refused socket ("open_failed").
- SIGTERM and SIGINT under a 5000-20000 lines/s flood (`sig.py`):
  - exit in 0.5-0.8 s;
  - pid record removed and capture lock free;
  - `integrity_check` ok;
  - `daemon stop` row present and session closed.
- SIGKILL under the same flood:
  - the kernel released the lock and `integrity_check` is ok;
  - `mcu daemon status` exits 3;
  - `mcu daemon stop` removes the stale record and exits 1;
  - the next start overwrites the stale record.
- SIGTERM while the port is retrying (sim down): exit in 0.16 s.
- SIGTERM with a `/wait` parked: the client gets 503 "daemon is shutting down; the wait was cut short".
- Two `mcuscoped` on one config: the loser exits 1 with the lock message naming the holder's pid.
- Two `mcu daemon start` on one port with different dbs: one exits 0, the other exits 1 "another daemon is already serving at ... (pid N)".
  - The survivor is unrecorded, as the documented residual in `pidfile.py` says.
  - `mcu daemon stop` still stops it through `/status`.
- 10 ports detached while connecting (`blackhole.py`): threads and fds back to baseline.
- `mcu daemon stop` with 8 ports hung in a TCP connect against a full accept queue (`synhole.py`, `stop_slow.py`): stopped in 4.2 s, with one join timeout logged.
- Double SIGINT (force exit): exit 0, a `CancelledError` traceback on stderr, no `daemon stop` row, and the session left open.
  - Documented as a force exit in `daemon.py:477-482`. It still feeds LIFECYCLE-3.
- Class 66 sweep: `Server.handle_exit` schedules `stop_subscribers` with `call_soon_threadsafe`, and the pid-release handler touches only the file system after the loop has exited.

## Not covered

- Windows as a whole: the `TerminateProcess` form of LIFECYCLE-1, the CTRL_CLOSE path in `_stdio`, and exclusive-handle release on detach.
- RSS: 67 MB rose to 133 MB over 38 min of churn with no plateau seen.
  - tracemalloc on a separate daemon over 856 WS cycles showed +4 MB of traced heap, all in-flight rx rows, and no per-connection retention.
  - The rise fits the 64 MB main page cache (`store.py:552`) plus 8 MB per reader filling as the capture grows. A run of several hours would settle it.
- Bundle export stopped mid-build (same code shape as LIFECYCLE-2, not driven).
- SIGTERM during a slow lifespan startup (large capture), a real `/dev/tty*` unplug, the pty transport, and `--ignore-capture-lock` with two writers.
