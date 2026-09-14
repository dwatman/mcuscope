# Leg C: simulator, protocol, daemon lifecycle, serial link

Scope: `git diff v0.4.0..HEAD` (fdd30a2) of `sim.py`, `protocol.py`, `daemon.py`, `serial_link.py`, and the code they touch.
`firmware/` is untouched by the range, and SPEC section 2 has no wire change, so the monitor was not opened.
Scratch scripts and logs: `~/tt-data/prerelease-2026-09-15/C/`. Every daemon ran on 18613 with a scratch `db_path`; far ends on 18623.

## Findings

### C-1 MED: the shutdown sentinel is shed by the drop-oldest fan-out, so a lagging `/wait` answers 500, not 503

- Where: `host/mcuscope/daemon.py:274` calls `store.stop_subscribers()` (`store.py:618`) while the writer and reader are still live; `_broadcast_batch` (`store.py:1087`) keeps feeding every queue and drops the oldest item once full.
- Defect: after `handle_exit` queues `None`, more rows arrive for up to `GRACEFUL_SHUTDOWN_S` (5 s); a subscriber whose queue is full loses the sentinel to drop-oldest and parks until uvicorn cancels it.
- Scenario: `mcu wait` with a slow pattern under a 5000 lines/s capture, then SIGTERM -> `error: Internal Server Error`, exit 1, 5.22 s later. SPEC 3.4 promises 503 and the CLI exit 3. Code is wrong.
- DRIVEN (`drive_wait_shutdown.py`, `mcu-sim --flood` over TCP):

  ```
  flood=5000 pattern '( |\w|\d|[A-F0-9])+(\w|\d|[A-F0-9])+=(\w|\d|[A-F0-9])+Q'  -> mcu_wait_exit=1 answered 5.22s after SIGTERM, "Internal Server Error"
  flood=0    same pattern                                                       -> exit 3 in 0.10s
  flood=5000 pattern NEVER_MATCHES_ZZZ                                          -> exit 3 in 0.08s
  ```

- `/assert` shares `CaptureWatch.next_batch`, so the same path applies there (REASONED).
- Class 38: `stop()` calls `stop_subscribers()` after the writer has exited, so nothing follows the sentinel; the `handle_exit` re-run lacks that discipline.
  - New class candidate: a sentinel put into a drop-oldest queue that keeps being fed.
- Fix: `stop_subscribers()` sets a closed flag and `_broadcast_batch` returns at once when it is set.

### C-2 MED: a `/wait` or `/assert` that subscribes after `handle_exit` never gets the sentinel

- Where: `store.py:1051` `subscribe()` has no shutdown state; `daemon.py:274` sends the sentinel only to queues that exist at that instant.
- Defect: uvicorn serves requests until its main loop sees `should_exit` (up to 0.1 s). A handler reaching `watch.open()` in that window parks until the graceful cancel.
- Scenario: `mcu daemon stop` racing an agent's `mcu wait` -> 500 after 5.2 s, CLI exit 1, where SPEC 3.4 says 503 / exit 3.
- DRIVEN (`drive_late_subscriber.py`, POST /wait right after SIGTERM):

  ```
  fresh connection, delay 0:      4/4 trials  status 500 after 5.12-5.20s
  keep-alive connection, delay 0: 5/5 trials  status 500 after 5.14-5.21s
  keep-alive, delay 50 ms:        2/3 status 500, 1/3 BrokenPipe
  ```

- Class 38 (the re-run does not cover subscribers born after it); class 25's "birth is the forgotten half".
- Fix: the C-1 flag also makes `subscribe()` raise `StoreError("daemon is shutting down; ...")`, which both `CaptureWatch.open()` callers already answer 503.
  - The WS subscribe maps every `StoreError` to close 1013 "subscriber cap" (`server.py:1969`); give shutdown its own reason there.

### C-3 MED: `/plot/channels` labels a detached board's history with another board's definitions

- Where: `host/mcuscope/server.py:1776` falls back to `manager.plot_channel_meta()` (`serial_link.py:1388`, merged by name) for a port not in `plot_channel_meta_by_port()`.
- Defect: per-board definitions are replaced by whichever attached board declares the same name. With no other board attached, an enum or bit channel falls back to `kind: analog` with no labels.
- Scenario: board `far` declares `state` as `OFF,ON` and `tri` in mA, beside `mcuscoped --sim`. After `DELETE /ports/far`, `far`'s history reports the sim's labels and unit.
- DRIVEN (`farend.py` plus two-port daemon):

  ```
  attached:     port=far {'state': ('far','enum',None,[[0,'OFF'],[1,'ON']]), 'tri': ('far','analog','mA',None)}
  far detached: port=far {'state': ('far','enum',None,[[0,'IDLE'],[1,'ARMED'],[2,'RUN']]), 'tri': ('far','analog','V',None)}
  ```

- Also REASONED: during `POST /ports/{alias}/reconnect` the alias is absent from `_ports` between `_detach_locked` and the re-insert (`serial_link.py:1343-1348`), so a page load in that window takes the same fallback for a board that is attached.
- Class 57. SPEC 9.2 (line 1636) prescribes this fallback, which contradicts line 1645 ("Channel names are unique only within a port") and the class 57 invariant. SPEC 9.2 is the wrong side; see Decisions.
- Fix: for a port with stored points but no decoder, learn its definitions from its own stored `!pd` rows with the bounded scan `prime_plot_defs` uses, or return null definition fields.

### C-4 LOW: a reattach misses a `!pd` the old port stored after the new port's prime, and decodes with the stale definition

- Where: `host/mcuscope/serial_link.py:1331` primes before the lock; the old port keeps capturing until `_detach_locked` at `:1343`. Only the new, primed decoder survives.
- Scenario: the old port is on `!pd 0 x:u2`; `!pd 0 x:s2*0.5` arrives during the reconnect; after it, `!ps 0 10 FFFE` stores `x = 65534.0` instead of `-1.0`, until the next rebroadcast. A width change stores the samples as generic events instead.
- DRIVEN (`drive_reattach_def_samewidth.py`, prime wrapped to let the redefinition land after it):

  ```
  stored after reattach: ['!ps 0 10 FFFE', '!pd 0 x:s2*0.5', '!pd 0 x:u2']
  new port meta: {'b': {'x': {'type': 'u2', ...}}}
  x points: [{... 'value': 65534.0}]
  ```

- Window: the prime query (up to the 30 s match budget on a large capture), the lock wait, and the reader join (2 s). Pre-existing since before v0.4.0, still live.
- Class 38 (a reconnect re-runs the prime but has a concurrent producer the first attach does not); class 4.
- Fix: after `_detach_locked`, have the new decoder learn the old port's in-memory definitions, which are never older than the primed ones.

### C-5 LOW: a reconnect erases `last_write_error` and `last_write_error_ts`

- Where: `host/mcuscope/serial_link.py:356` builds a fresh `_WriteHealth`; `_carried` (`:1344-1346`, `:1377`) carries only rx, tx, drops and seq.
- Defect: SPEC 3.4 (line 629) says these "stay on record after the streak", and a disconnect keeps them (`:690-691`). `POST /ports/{alias}/reconnect` is a detach plus an attach, and it wipes them.
- Scenario: write fails ("Write timeout"), user reconnects the flaky port -> `/status` shows `last_write_error: null`. This is the record the carried-counters comment says a reconnect must keep.
- DRIVEN (`drive_reattach_health.py`):

  ```
  before reattach: {'write_failures': 1, 'last_write_error': 'Write timeout', 'last_write_error_ts': 1789407415.08, ...}
  after reattach:  {'write_failures': 0, 'last_write_error': None, 'last_write_error_ts': None, ...}
  ```

- Class 4. Pre-existing, still live.
- Fix: carry `_WriteHealth(0, last_error, last_ts, None)` in `_carried`.

### C-6 LOW: `_write_health` read-modify-write runs outside `_write_lock`

- Where: `host/mcuscope/serial_link.py:1038-1054` (worker thread, after the lock is released) against `:690-691` (loop, `_on_disconnect`).
- Defect 1: `/cmd` and `/send` hold different asyncio locks, so two failing writes run in two worker threads. Both read `failures = n` and both store `n + 1`, losing a count.
- Defect 2: a failing write whose `prev` load predates `_on_disconnect`'s reset stores a streak the disconnect ended, contradicting SPEC 3.4 line 629.
- REASONED. Class 40: a single immutable swap, but two threaded calls interleave.
- Fix: build and store the new health inside the `with self._write_lock:` block. Do the disconnect reset in the reader under the same lock (`:600`), not in `_on_disconnect`.

### C-7 LOW: argv integers bypass the wire grammar; `--flap` takes nan

- Where: `host/mcuscope/daemon.py:55` (`--port type=int`), `host/mcuscope/sim.py:1162` (`_tcp_port_arg`, bare `int()`), `:1200` (`--drop-response type=int`), `:1237` (`--flood type=int`), `:1229` (`--flap type=float`).
- DRIVEN:

  ```
  mcuscoped --port '١٨٦١٣' --sim --config sim.toml  -> "web UI: http://127.0.0.1:18613/ui/" (serving)
  mcu-sim --tcp-port '١٨٦٢٣'                        -> socket://127.0.0.1:18623
  mcu-sim --tcp-port ' +1_8623 '                    -> socket://127.0.0.1:18623
  parse_args(['--flap','nan','--flood','٥','--drop-response','+1_0']) -> nan 5 10
  ```

- `--flap nan` passes `flap > 0` as False (`sim.py:825-826`), so flapping is silently off.
- Class 22 (argv); class 64 for `--flap`.
- Fix: one argparse type built on `p.is_decimal_token` plus a range, and `math.isfinite` for `--flap`.

### C-8 LOW: the startup log says "started" for a daemon whose startup failed

- Where: `host/mcuscope/daemon.py:410` writes `-startup.log` before `_serve`; the lifespan (store open) and the bind run after it.
- Scenario: a capture file that is not a database -> exit 3, uvicorn's "Application startup failed". No crash log, pid record removed, and the startup log reads `mcuscoped 0.4.0 started, pid 384409`. A windowless start (stderr on devnull) has only that record.
- DRIVEN (`corrupt.toml` with 8 KiB of random bytes as `db_path`, `XDG_DATA_HOME` in scratch).
- Class 12 (healthy-while-dead). Pre-existing; the range edited this block.
- Fix: in `_serve`, when `not server.started`, rewrite the startup log as a failure with the exit code.

### C-9 LOW: `_serve` and a real signal reaching `handle_exit` have no test

- Where: every daemon test replaces `_serve` (`tests/test_daemon_startup.py:176,203`, `tests/test_capture_lock.py:185`).
  - The exit-3-on-not-started branch and the KeyboardInterrupt swallow are unpinned; `drive_bind_fail.py` drove them green: `SystemExit 3`, no pid record left.
- `tests/test_daemon_r2026_09_12_server.py:216,246` run `handle_exit` as a loop callback over a queue with room.
  - Production runs it as a signal handler interrupting loop code, and C-1's saturated queue is not reached.
- REASONED (grep). Class 27 (double gentler than production), class 29.
- Fix: one subprocess test for the bind failure exit code, and one SIGTERM test with a full subscriber queue (pins C-1).

## Sweeps

### Class 4: per-attach state lost on reattach

Enumeration: `SerialPort.status()` (`serial_link.py:1231-1266`) against `__init__` (`:300-370`) and `_carried` (`:1346`, `:1377`). 18 fields.

| Field | Verdict |
|---|---|
| alias, device, baud, eol | complies: the attach arguments, re-passed by reconnect (`server.py:1011`) |
| connected, held, disconnect_reason | exempt: per connection by design |
| resolved_device, description | exempt: re-landed on the next connect |
| lines_rx, lines_tx, rx_dropped | complies: carried (DRIVEN, counters kept in `drive_reattach_health.py`) |
| write_failures, write_failing_since | exempt: a streak, which a disconnect also ends (SPEC 3.4) |
| last_write_error, last_write_error_ts | violates: C-5 |
| target | exempt: re-identified on connect |

Not in `/status` but per-attach: `plot_decoder` is re-primed (complies on first attach, violates on the concurrent reconnect window, C-4); `_seq` carried (complies).

### Class 7: pid record lifecycle

Enumeration: `tests/test_pidfile.py` (19 tests) and `tests/test_daemon_startup.py` run green (31 passed). Cells whose path changed since v0.4.0 (`_serve`, `handle_exit`) were driven:

| Cell | Verdict |
|---|---|
| our own record x SIGTERM stop (graceful plus replay) | complies: record gone after each run of `drive_wait_shutdown.py` and `drive_late_subscriber.py` |
| our own record x failed startup, bind after the probe (`_serve` exit 3) | complies: `drive_bind_fail.py`, no record |
| our own record x failed startup, lifespan (corrupt capture) | complies: record gone, exit 3 (log wrong, C-8) |
| live other process x claim (two concurrent `mcu daemon start`) | complies: loser exits 1 naming the winner's pid, record left naming the winner, `mcu daemon stop` stopped it |
| remaining cells | covered by the existing matrix tests; the range did not change `pidfile.py` |

### Class 8: thread teardown on detach and shutdown

Enumeration: `grep -n "threading.Thread(\|threading.Timer(\|ThreadPoolExecutor(" sim.py daemon.py serial_link.py`: 4 sites.

| Site | Verdict |
|---|---|
| `serial_link.py:374` reader | complies: the range did not touch it; join timeout, closed loop (`_post`, `:606`) and stop-during-open (`:559`) handled, tests in `test_review_r2_serial.py`, `test_reconnect.py`; SIGTERM with a flooding TCP port attached exited cleanly (DRIVEN) |
| `serial_link.py:43` `_join_pool` | complies: runs joins only, holds no handle |
| `sim.py:1010` `start_tcp_sim` server thread | complies: daemon thread, `SimHandle.stop` closes the socket; `mcuscoped --sim` uses the in-process `SourceLink` and not this thread |
| `daemon.py:423` browser Timer | exempt: daemon thread calling `webbrowser.open`, owns no handle |

### Class 11: codec symmetry

Enumeration: `grep -n "^def format_\|^def _format" protocol.py sim.py`: 9 encoders.

| Site | Verdict |
|---|---|
| `format_can_event` / `parse_can_event` | complies: 60000 random frames over buses 1, 2, max, full std/ext id and tick edges, 0 asymmetries |
| `format_can_family`, `format_can_flags`, `format_can_id` | complies: round-tripped in the same runs |
| `format_command` / `parse_command` | complies: 50000 round trips |
| `format_response_ok`, `format_response_err` / `parse_response` | complies: 50000 each |
| `format_marker` / `parse_marker` | complies: tick round trip; a text starting with `@<digits>` is refused by the formatter |
| `sim._format_typed_sample` / `PlotDecoder` | complies: 20000 samples of `!pd 0` (s2, u2, f4 with inf and nan) and 20000 of s1/u4/s4; non-finite f4 never reaches points |
| malformed input | complies: 200000 mutated lines (alphabet includes `٣`, `²`, NUL, tab) through 7 parsers plus `learn`, `points_from_tokens`, `channel_meta`, `declared_kinds`: 0 raises, 0 `ProtocolError` where None is documented |

Scripts: `fuzz_codec.py`, `fuzz_codec2.py`. The sim `--demo`/`--plot`/plain output over 120 virtual seconds (`drive_sim_demo.py`): 0 undecodable `!ps`, CAN ids exactly as SPEC 7 lists per mode, every narrated state step matches the next enum sample, and no narration line classifies as anything but debug.

### Class 22: stdlib predicate for a wire grammar

Enumeration: `grep -n "isdigit()\|isdecimal()\|isalnum()\|isnumeric()"` over the four files: 7 lines. `grep -n "\bint(\|\bfloat(\|\bbool("`: 39 lines, of which 25 are code. Plus argparse `type=int|float`: 4 sites.

| Site | Verdict |
|---|---|
| `protocol.py:206` | complies: the `is_decimal_token` helper itself; the other 6 predicate lines are comments |
| `protocol.py:178` | complies: explicit hex set plus a length bound |
| `protocol.py:232, 364, 507, 689, 1083`; `serial_link.py:196`; `sim.py:655` | complies: `is_decimal_token` or regex plus length gate before `int()` |
| `protocol.py:423, 520, 565` | complies: single digit tested against an explicit set |
| `protocol.py:670` | complies: grammar regex plus `isfinite` |
| `protocol.py:766` | complies: regex plus length and sign gate |
| `protocol.py:867` | complies: `_TICK_HEX_RE` gate, base 16 |
| `protocol.py:839, 846, 879, 882` | exempt: struct and bit decoding of already-validated bytes |
| `sim.py:133, 395, 499, 530, 617, 621, 622, 624` | exempt: arithmetic on internal values |
| `sim.py:1169` `_tcp_port_arg` | violates: C-7 |
| `sim.py:1200` `--drop-response`, `:1237` `--flood`, `:1229` `--flap` | violates: C-7 |
| `daemon.py:55` `--port` | violates: C-7 (range-checked at `:108`, grammar not) |

### Class 36: periodic catch-up without a burst cap

Enumeration: `grep -n "_due_beats(\|while .*\(now\|next_\|_due\|deadline\|time()\)" host/mcuscope/*.py`: 7 sites, plus `_poll_flood` found by reading: 8.

| Site | Verdict |
|---|---|
| `sim.py:423` heartbeat | complies: `_due_beats`, cap `PERIODIC_MAX_BURST`, re-anchor |
| `sim.py:441` standing CAN (skipped ids under `--demo` never scheduled) | complies |
| `sim.py:470` marker | complies |
| `sim.py:526` reading, `:532` fault (new) | complies: `if beats:` emits one line whatever is owed |
| state narration (new, `sim.py:520-524`) | complies: no schedule, one line per observed change |
| `sim.py:560` plot samples | complies |
| `sim.py:499` `_poll_flood` | exempt: SPEC 7 makes flood backlog owed data, capped at `FLOOD_MAX_BURST` per pass |
| `cli.py:2268` | exempt: a deadline wait, emits nothing per period |

DRIVEN: a 600 s stall under `--demo` produced at most 38 lines in one poll pass.

### Class 38: reset re-run missing the first run's discipline

Enumeration: callers of each initialisation routine in the four files.

| Routine and caller | Verdict |
|---|---|
| `store.stop_subscribers` from `store.stop()` (first) and `Server.handle_exit` (re-run) | violates: C-1, C-2 |
| `prime_plot_defs` on first attach against replacing attach | violates: C-4 |
| `SerialPort.start` via attach and reconnect | complies: same path, counters carried (except C-5) |
| reader reconnect loop (`_reader` re-open, `_on_connect` identify) | complies: one path for first and later connects |
| `Simulator()` re-created on TCP accept, `SimSource` open, pty session restart | complies: `SimState.start_ns` is fresh each time, so `narr_state` (`sim.py:158`) and the enum agree at tick 0 in both modes |

### Class 40: multi-attribute state shared between loop and worker, torn on read

Enumeration: `grep -n "to_thread(\|run_in_executor(\|threading.Thread("` over the four files: 7 sites.

| Site | Verdict |
|---|---|
| `serial_link.py:1103, 1161` `_write_bytes` | complies for torn reads (single immutable `_WriteHealth`); violates on interleaving: C-6 |
| `serial_link.py:1132` `_break_locked` | complies: writes nothing shared |
| `serial_link.py:374` reader | complies: `_link` swapped under `_write_lock` on close; everything else goes through `_post` to the loop |
| `serial_link.py:398, 416` joins | complies: no writes |
| `sim.py:1010` | complies: its own `Simulator` per client, shares only a stop Event |
| `PlotDecoder` (new `declared_kinds`, `plot_channel_meta_by_port`) | complies: learned in `_consume` and `prime_plot_defs`, both on the loop |

### Class 52: per-daemon artefact not keyed like the pid record

Enumeration: `grep -n "user_data_dir"` over `host/mcuscope/*.py`: 4 sites (`pidfile.py:61`, `config.py:55` comment, `config.py:122`, `_stdio.py:293`), plus `cli_daemonctl.py:49`.

| Artefact | Verdict |
|---|---|
| `mcuscoped-<host>-<port>.pid` | complies: the key |
| `-startup.log`, `-crash.log` (`_stdio.set_report_key`, `daemon.py:381`) | complies after the key is set; exempt before it: a crash before config load has no host:port to key by |
| `.err` (`cli_daemonctl.py:49`) | complies: derived from the pid path; two concurrent starts on one port were driven and the `.err` stayed consistent (empty, the loser's refusal reached its own CLI) |
| `capture.db` and its `.lock` (`config.py:122`, `lockfile.py:115`) | exempt: per config by design |

### Class 57: per-board state keyed by a name unique only within a port

Enumeration: `grep -n "dict\[\|: dict\|= {}\|defaultdict\|setdefault\|Counter("` over the four files, excluding signatures and returns: 12 lines, plus the `next_can` and `by_port` comprehensions found by reading: 14.

| Site | Verdict |
|---|---|
| `sim.py:73` gpio, `:76` can buses, `:150-151` `next_can` | exempt: one simulated board |
| `serial_link.py:334` `_pending` | complies: per `SerialPort` |
| `serial_link.py:866` | exempt: a local, not a store |
| `serial_link.py:1284` `_ports`, `:1296` `_carried` | complies: keyed by alias |
| `serial_link.py:1394` `plot_channel_meta` merged by name | violates where `server.py:1776` uses it for a detached port: C-3 |
| `serial_link.py:1401` `plot_channel_meta_by_port` | complies |
| `protocol.py:910` `_defs`, `:999` `channel_meta` | complies: one decoder per port at every construction site (`serial_link.py:336`, `server.py:3004, 3065, 3082, 3090`) |
| `protocol.py:40, 44, 55, 584` | exempt: constant tables |

## Decisions for the owner

- C-3: what a detached board's channels carry.
  - (a) Another attached board's definition: SPEC 9.2 today, wrong labels.
  - (b) Its own stored `!pd`, via a bounded scan per port.
  - (c) Null definition fields.
- SPEC contradicts itself on channel-name scope.
  - Line 272 (2.5): names "must be unique across all streams and ad-hoc names (the host keys channels by name alone)".
  - Line 1645 (9.2): "Channel names are unique only within a port".
  - 2.5 predates per-port keying and reads as the stale side.
- C-8: record a failed start in the startup log, or write the log only after `server.started`.
- C-7: `--flap nan`: refuse, or read it as 0 (off).

## The two questions

1. Least confident, rechecked:
   - C-1's realism. It needs a subscriber lagging by 2000 rows at shutdown. I reproduced it only with a 0.8 ms per line regex at 5000 lines/s; a cheap pattern at the same rate answered 503. Re-driven three ways (table in C-1).
   - C-2's window is one uvicorn main-loop tick (0.1 s). Re-driven with fresh and keep-alive connections, 9 of 9 at delay 0.
   - Signal-handler reentrancy of `stop_subscribers` (it runs mid-bytecode in the loop thread on POSIX): REASONED benign.
     - Both it and the fan-out suppress `QueueFull`/`QueueEmpty`, and the handler does not mutate `_subscribers`.
     - Not driven.
   - Windows: `/shutdown` delivers SIGTERM from a loop callback, so reentrancy does not arise there. C-1 and C-2 are platform independent (REASONED); nothing was run on Windows.
2. What we had not thought about:
   - The WS pump now ends at SIGTERM rather than at store stop. A web UI stops receiving live rows up to 5 s before the socket closes; the rows are in the capture (REASONED, acceptable).
   - C-2's fix makes WS subscribe refuse during shutdown, where it currently reports "subscriber cap reached".
   - `/assert` shares C-1 and C-2 through `CaptureWatch`.
   - The reconnect window in `/plot/channels` (C-3, second bullet).
   - Startup log truth on failed starts (C-8) sits in the block the range edited, and no leg of the earlier rounds opened it.

Side effect: the SIGTERM runs wrote `~/.local/share/mcuscope/mcuscoped-127.0.0.1-18613-startup.log` in the real data dir. The pid records were released; the owner may delete the log.
