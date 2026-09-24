# Registry leg, classes 1-14 (2026-09-24)

HEAD `f31ecd995ee2ed193d8d60637b76620ddc880be3`, checked before starting.
Scratch: `~/tt-data/mcuscope-2026-09-24/registry-leg/1-14/` (probes `*.py`, 1M-row capture `big.db`, daemon logs `d1/`).

Status: all 14 classes swept. Findings: 1 HIGH, 1 MEDIUM, 3 LOW. No new class.

## Findings

### R1-1 HIGH `server.py:3347`, `server.py:2628`, `server.py:2990`: user regexes compile on the event loop, and compiling one can stall it for seconds or exhaust memory

- Where: `_check_match` (`/lines`, `/lines/export`), `_do_wait` (`/wait`) and `_compile_patterns` (`/assert`, up to 16 patterns) all call `regex.compile(user_pattern)` inline on the loop.
  - The only guard is `MAX_MATCH_LEN = 200`.
  - The `regex` budget (`timeout=`) covers matching, not compiling.
  - Nested counted repeats expand when compiled, so a short pattern costs time and memory that grow with the product of its repeat counts.
- Measured with a standalone `regex.compile` (`rc.py`):

  | Pattern | Length | Compile |
  |---|---|---|
  | `(?:a{1000}){1000}` | 17 chars | 0.78 s |
  | `(?:(?:a{100}){100}){100}` | 24 chars | 0.94-1.08 s, 290 MB RSS |
  | ten levels of `{100}` | 71 chars | MemoryError under a 1.5 GB cap |

  Without a cap, the first probe run (8 patterns, including the 71-char one) was SIGKILLed with exit 137, most likely by the OOM killer.
- Driven against a live daemon (`mcuscoped --sim`, port 8673, isolated config, 3 GB `ulimit -v`, `stall.py`):
  - `GET /lines?match=(?:(?:a{100}){100}){100}`: `/ports` latency rose to 582 ms against 0 ms with `match=abc`.
  - A five-level `{100}` pattern (~45 chars): the loop stalled for 3.3 s, then the response was `500 {"error":""}` (a MemoryError).
  - An uncapped daemon would keep allocating until the OOM killer ends it, and the capture stops with it. Reasoned from the standalone exit 137, not driven against a daemon.
- `/lines` needs no config-write privilege, so any client that may read can do this, including the web UI's filter box, which allows 200 characters.
- Realistic patterns are unaffected: `\d{65535}` compiles in 38 ms and `[0-9a-f]{64}` in 0.6 ms.
- Moving the compile off the loop fixes the stall but not the memory. A bound on the expanded size (for example the product of nested repeat counts) is what closes it. Owner's call.

### R13-1 MEDIUM `pidfile.py:143`: a pid record that is not valid UTF-8 raises out of `read_pid_record`, so the daemon cannot start and `mcu daemon stop` prints a traceback

- `open(path, encoding="utf-8")` followed by `fh.read()` raises `UnicodeDecodeError`, which is a `ValueError`. The function catches only `OSError`.
- Driven with the record `b"\xff\xfe1\x002\x00"`, a UTF-16LE "12" of the kind PowerShell 5's `>` or `Set-Content` writes:
  - `read_pid_record` raises `UnicodeDecodeError`.
  - `mcu --url http://127.0.0.1:8671 daemon stop` exits 1 with a rich traceback and writes `mcu-crash.log`. That breaks the class 9 contract.
  - `mcuscoped --config c.toml` (port 8671): startup dies inside `pidfile.claim`, exits 1 with a traceback and writes `mcuscoped-127.0.0.1-8671-crash.log`. The daemon cannot start until the user finds and deletes the record.
  - `mcu --url http://127.0.0.1:8676 daemon start -c c.toml`: exits 1 with a traceback after the spawn, in `_write_pid_record`. The spawned daemon died in `claim` with its own crash log, and no process was left.
- By reasoning, the same exception hits every other reader:
  - `pidfile.release`, including from the SIGTERM replay handler.
  - `cli_daemonctl._remove_pid_record`.
  - `daemon._report_key`, at `daemon.py:346`.
- The class 7 matrix misses this state:
  - Every corrupt-record test writes with `encoding="utf-8"`: `test_cli.py:996` and `:1902`, `test_pidfile.py:183`.
  - None writes undecodable bytes.
- Fix: read bytes and decode ASCII inside the guard, or catch `ValueError`. Then add `b"\xff"` to the corrupt-record parametrisations.

### R1-2 LOW `server.py:2954-2958`: `_TempFileResponse` unlinks the finished export on the event loop

- The `finally` in `async def __call__` calls `_remove_export_file`, which is `os.unlink` for the copy and its `-journal`, synchronously on the loop after every session export or bundle download.
- A session copy is as large as the session. On this NVMe ext4, unlinking a 2 GB written file took 480 ms (`os.unlink` timed directly), and the loop is stalled for that long.
- The module's own rule, stated at `server.py:1568`, is "every filesystem call on the worker thread ... the capture directory can be a network mount (class 1)". The lifespan finaliser already follows it with `asyncio.to_thread`.
- A second, rare site is `_ExportJob.abandon()` (`server.py:2846`). It is called on the loop, and when the build has already finished it unlinks under `self._lock`. The window is a build finishing just before its client leaves or the handler is cancelled.
- How confirmed: I timed the unlink cost; that it runs on the loop is reasoned from the code. Not driven end-to-end with a multi-GB export.

### R1-3 LOW `update_check.py:268`: `httpx.AsyncClient(...)` is built on the loop for every release check

- Building the client makes the SSL context and loads the CA bundle synchronously.
  - Measured 219 ms for the first construction in a process and 75 ms for later ones.
  - Driven through `UpdateChecker.check_once()` against an unreachable URL (`lag.py`): the largest gap in the event loop was 110 ms.
- It runs once a day, plus hourly (`RETRY_INTERVAL_S`) while offline, and it is started from `GET /status`.
- Fix: build the client (or the `ssl` context) once, or off the loop.

### R12-1 LOW `webui/settings.js:38-44`: a failed `GET /devices` renders as "no devices detected" in the settings ports editor

- `loadDevices` catches every failure and returns `[]`.
- `openSettings` (`:764-768`) stores that in `devicesCache` with no error state. `buildDeviceSelect` (`:474`) then offers only the saved value and "custom...", which is what a host with nothing plugged in looks like.
- The offline banner fires only when `/config` fails too, so a `/devices`-only failure is silent. That failure can be a 500 from an enumeration error, or a timeout on a slow setupapi scan.
- The sibling at `statusbar.js:629-636` (the attach dialog) keeps `err` and shows it, so the two device pickers disagree about the same failure.
- How confirmed: reasoned from the code. Not driven in a browser (owed).

## Class 1. Blocking work on the event loop or default executor (complete)

Sweeps:

- `grep -rn "run_in_executor(None" host/mcuscope` returned 1 line, `serial_link.py:39`. It is a comment, so it complies (no executable line).
- Endpoints: 37 route handlers, enumerated with `grep -n "@app\.\(get\|post\|put\|delete\|patch\|websocket\)"` plus the next `def`, and each one read.
- Candidate blocking calls: `calls.py`, an AST walk of `server.py` for calls on `store.<sync SQL method>`, `os.*`, `Path`, `open`, `tempfile`, `re`/`regex`, `sqlite3` and `socket`, with the chain of enclosing functions. 65 calls; 34 have an `async def` as the innermost function (on the loop), 31 are inside sync helpers, each traced to its caller.
- Timing of on-loop SQL: `looptime.py` over a 1M-row capture with a quiet port, rare channels and time floors, without ANALYZE (as in production). Every on-loop store read, and every match-free `query_lines` shape the handlers can send, took 0.0-1.7 ms.

Per endpoint:

| Endpoint | Verdict |
|---|---|
| `GET /` | complies (redirect) |
| `GET /status` | **violates** through `_update_status` -> `check_once` (R1-3). `db_size_bytes` (2 stats) complies: same filesystem the loop's own writer commits to. `content_bytes` (3 PRAGMAs, 0.0 ms) and `active_session` (partial-index seek) comply |
| `POST /shutdown` | complies |
| `GET /ports` | complies (`stored_ports` 0.2 ms at 1M, one seek per port, documented on-loop in ARCHITECTURE.md) |
| `POST /ports`, `DELETE /ports/{a}`, `POST .../reconnect`, `POST .../disconnect` | comply: prime is `query_lines_safe` with match (offloaded), join and close run on `_join_pool` |
| `GET /devices` | complies (`to_thread`) |
| `GET /config`, every `PUT /config/*`, `PUT /plotjuggler` | comply (`to_thread` for read, save, resolve); `pjstream.parse_dest` is pure |
| `GET /plotjuggler` | complies |
| `GET /sessions` | complies (`resolve_session` indexed, 0.1 ms; counts offloaded) |
| `POST /sessions`, `POST /sessions/stop` | comply (writer path) |
| `DELETE /sessions/{id}` | complies (PK seek, one-row DELETE + commit on the loop connection, which is the documented single-writer design) |
| `GET /sessions/{ref}/export`, `/bundle` | build complies (export pool). Download completion **violates** (R1-2) |
| `POST /purge` | complies (counts and deletes offloaded, `max_id` in memory) |
| `POST /send`, `/break`, `/cmd` | comply (`_write_pool`) |
| `GET /lines`, `/lines/export` | **violates** at `_check_match` (R1-1). Queries comply: match -> `match_executor`, match-free timed <= 1.7 ms, first page via `to_thread` |
| `GET /can/frames` | complies (offloaded or streamed) |
| `GET /plot/channels`, `/plot/series`, `/plot/export` | comply (`_safe` wrappers; `learn_stored_plot_defs` and `_plot_export_defs` use match queries, so they are offloaded) |
| `POST /wait` | **violates** at `server.py:2628` (R1-1); live matching complies (`_live_scan` pool) |
| `POST /assert` | **violates** at `_compile_patterns` (R1-1, up to 16 compiles) |
| `POST /marker` | complies (`re.fullmatch` of a fixed alias grammar on a bounded field) |
| `WS /ws` | complies (in-memory queue) |
| static `/ui` | complies (`_stamped_index` reads on the loop once per file change, accepted in its docstring) |

Lock sub-rule: 7 `threading.Lock` sites.

- `_comports_lock`: never taken on the loop.
- `_write_lock`: the loop does not take it; `stop` closes through `_join_pool`.
- `link.SourceLink._lock`: held for tiny sections.
- `_match_pool_lock`, `_pools_lock`: creation only.
- `_read_conns_lock`: held only for set swaps.
- `_ExportJob._lock`: taken on the loop by `abandon()`. The worker holds it across unlinks only when already abandoned, so the loop never waits on it.

All comply.

Sweep imprecision:

- The invariant says "no SQLite ... on the event loop", while ARCHITECTURE.md and `query_lines_safe` deliberately run bounded, index-seek reads on the loop.
  - Proposed wording: "no unbounded SQLite work; each on-loop read is a seek whose cost does not grow with the capture, pinned by a 1M-row timing."
- "regex work" should say "user-supplied patterns, compile included". Fixed `re` patterns on bounded protocol tokens (`protocol.py`) are by design.
- Improved sweep command: `grep -n "regex.compile\|os\.\(unlink\|remove\|stat\|path.getsize\)\|httpx.AsyncClient(" host/mcuscope/server.py host/mcuscope/update_check.py`. For each hit, check whether the enclosing function is `async def`, as `calls.py` does.

## Class 2. Text writes without explicit newline (complete)

Sweep: `grep -rn "open(" host/mcuscope | grep -v 'newline\|"rb"\|os.open'` returned 27 lines, plus `grep -rn "write_text("`, which returned 0.

| Site | Verdict |
|---|---|
| `cli_daemonctl.py:60`, `:246` | exempt: docstring and comment |
| `cli_daemonctl.py:69`, `:87` | exempt: binary `"ab"` |
| `pidfile.py:113` (`/proc` stat), `:143` (pid record) | exempt: reads |
| `_stdio.py:161` (CONOUT$), `:169` (devnull) | exempt: replacement std streams, where console CRLF translation is the stdout behaviour and devnull discards. The hit is the `open(` line; `newline` is absent from the multi-line call |
| `_stdio.py:181` | exempt: CONIN$ read |
| `server.py:1693` | exempt: `ZipFile.open(..., "w")` is binary |
| `server.py:2471`, `:2518`, `:2634`, `:2799`, `:3136` | exempt: `CaptureWatch.open`, `on_open` |
| `cli.py:2306` | exempt: httpx client open |
| `cli.py:2649` | exempt: `subprocess.Popen` |
| `cli.py:2734` | exempt: `webbrowser.open` |
| `store.py:1585` | exempt: sqlite `on_open` hook |
| `cli_client.py:124`, `:135`, `:159`, `:247`, `:283` | exempt: httpx client opens |
| `cli_client.py:254` | exempt: `"wb"` |
| `serial_link.py:128` | exempt: sysfs read |

Text writers the grep drops, because `newline=` is on the same line: `cli_daemonctl.py:251`, `cli.py:1803` (`_OutFile`, callers pass `""` and `"\n"`), `_stdio.py:373`. All comply.

Byte writers: `config.py:606` and `update_check.py:217` (`write_bytes`), `pidfile.py:226` and `lockfile.py:162` (`os.write`). Not text, so exempt.

Sweep imprecision: none found. `TextIOWrapper`/`fdopen`/`io.open` grep returned 0.

## Class 3. Listening sockets without Windows exclusivity (complete)

Sweep: `grep -rn "socket.socket\|\.bind(" host/mcuscope` returned 11 lines.

- `sim.py:740`, `:742`, `:753`: complies. The listener sets `SO_EXCLUSIVEADDRUSE` on nt and `SO_REUSEADDR` on POSIX before `bind`.
- `sim.py:774`, `:823`, `:938`, `:1012`: exempt, type annotations.
- `daemon.py:243`, `:256`: complies. `_port_conflict` probes every resolved address with `SO_EXCLUSIVEADDRUSE` where it exists, otherwise `SO_REUSEADDR` plus bind. It runs before `pidfile.claim` (`daemon.py:464` < `:475`).
- `pjstream.py:94`, `:99`: exempt, annotations. `pjstream.py:122`: exempt, a UDP sender that never binds.

Sweep imprecision: the grep cannot see the daemon's real listener, which uvicorn binds inside `loop.create_server`. Only the `_port_conflict` probe stands in front of it, with a stated TOCTOU gap.

- Improved sweep: add `grep -rn "uvicorn.Config\|create_server\|start_server" host/mcuscope`, and rule each such listener by the probe that fronts it.

Owed (Windows): the `SO_EXCLUSIVEADDRUSE` probe, already on the REVIEW_LOG "Owed" list.

## Class 4. Per-attach state lost on reattach (complete)

Method: compared the attributes `SerialPort.__init__` sets (`serial_link.py:304-388`) with the 22 keys `status()` reports (`:1283-1321`).

- Parameters passed on reattach: `alias`, `device`, `serial_number`, `baud`, `eol`. Comply.
- Per-connection by design: `connected`, `disconnect_reason`, `target` (re-identified on every connect, SPEC 2.4). Comply.
- `held`: per attachment by design. Reconnect is the documented way to resume. Complies.
- `resolved_device`, `description`: exempt. SPEC 3.4 (line 671) says "null until the first connect, kept across a disconnect". A reattach is a new attachment, so they read null until it lands.
  - Ambiguity for the owner: after `POST /ports/{a}/reconnect` of an absent board, the status bar loses the short name it showed.
- Carried by `PortManager._carried`: `lines_rx`, `lines_tx`, `rx_dropped` (and `_seq`), plus all four write-health fields through `_write_health`. Comply, each with a test:
  - `test_reconnect.py:395`, `:429`, `:455`
  - `test_serial_link_attach.py:223`, `:284`, `:319`

## Class 5. argv hoisting in cli.main() (complete)

- Ran `tests/test_cli_argv.py`: 10 passed.
- Mechanical matrix (`hoist.py`): walked the click tree (52 commands). For every non-flag option of every leaf, crossed 5 global spellings (`--json`, `-p sim`, `--url`, `-psim`, `--token`) with:
  - every position before, between and after the subcommand path;
  - the option's value spelled as a global followed by a real `--json`.
- 2035 cells, 0 failures. No option has `nargs > 1`. No subcommand redefines a global's spelling. No group-level value options.
- Degrade-to-no-hoisting is pinned by `test_cli.py:2650` (`test_hoisting_survives_a_command_tree_it_cannot_read`). Complies.

## Class 6. Non-finite values reaching chart arrays (complete)

Method: `grep -n "isFinite\|\.push(\|setData\|\.data\[" plots.js digital.js pane.js timewindow.js freeze.js layout.js`, then every producer that writes into a chart or lane array traced to its gate.

| Producer | Gate | Verdict |
|---|---|---|
| `plots.js:577-580` x arrays | `:566` | complies |
| `plots.js:603` y push | callers gate: `parsePlotValue` `:98`, `decodePlotSample` `:255` after scale, `mergeSeedSeries` `:329` | complies |
| `plots.js:616`, `:643-644` | push null or the previous (already finite) value | complies |
| `plots.js:245` bits lanes, `:249` enum | `(bits>>b)&1`, and enum/bits restricted to integer types (`parseChannelSpec` `:143`, `:148`) | complies |
| `digital.js:120` `pushVertex` | x gated at `:69`; values are integer enum or 0/1, or null | complies |
| `digital.js:149-150` `noteLaneId` | host and tick from the gated `x` | complies |

`node --test plots_finite.test.mjs`: 18 passed.

## Class 7. Pid record lifecycle (complete)

Matrix, record state x operation, with the test that asserts each cell:

| State | claim | release | stop | failed startup |
|---|---|---|---|---|
| none | `test_pidfile.py:40` | no-op (by code) | `test_cli.py:973`, `:1880`; `stop_scope:97`, `:110` | `test_daemon_startup.py:66` |
| stale (dead pid) | `test_pidfile.py:63` | n/a | `stop_scope:196`, `:455` | claim takes it, then as none |
| live other | `test_pidfile.py:49`, `:73` | `:126` (rewritten) | `stop_scope:163`; `test_cli.py:1821` | `test_daemon_process.py:70`, `test_daemon_startup.py:297` |
| live parent (Windows shim) | not located | n/a | `stop_scope:218`, `:391`, `:427` | not located |
| own | `test_pidfile.py:105`, `:116`, `:382` | `:40` | n/a | n/a |
| peer pid (remote or tunnelled) | n/a (local file) | n/a | `stop_scope` module (never signalled) | n/a |
| garbled (empty, non-decimal, `٣`, too wide, FIFO, mid-write) | `test_pidfile.py:244`, `:304` | by code | `test_cli.py:996`, `:1902` | not located |
| **undecodable bytes** | **violates (R13-1)** | **violates (R13-1)** | **violates (R13-1)** | **violates (R13-1)** |

The residual compare-and-delete window stated in `claim()` is unchanged, and not re-filed.

## Class 8. Thread teardown on detach and shutdown (complete)

Sweep: `grep -n "threading.Thread(\|Timer(\|ThreadPoolExecutor(" host/mcuscope/*.py` returned 7 sites.

| Thread or pool | Outlive scenarios | Verdict |
|---|---|---|
| serial reader `serial_link.py:396` | join timeout; open after the deadline; loop closed; exception mid-read. Details below | complies |
| sim server `sim.py:1044` (tests, `mcu-sim`; `--sim` uses in-process `SourceLink`) | `SimHandle.stop` closes the listener from outside and the thread's `finally` closes it again (suppressed). It never touches a loop | complies (reasoned) |
| `_join_pool` `serial_link.py:44` | runs joins and the fallback handle close only | complies |
| `_write_pool` `serial_link.py:53` | writes hold `_write_lock`; every close takes the same lock | complies |
| `match_executor` `store.py:447` | `stop()` closes the cached read connections and bumps the epoch; a worker racing it retries on a fresh handle (`test_store_fastpaths.py:456`) | complies. Residual below |
| export pool `server.py:2739` | abandon runs through the progress handler and `checkpoint()`; files are removed by whichever of build and handler finishes second (`test_server_export_pool.py:176`) | complies |
| `webbrowser` Timer `daemon.py:518` | holds no handle | exempt |

Serial reader scenarios, each with its test:

- Join timeout: `stop` closes the link through `_close_link_locked`, which is idempotent. `test_reconnect.py:866`.
- Link opened after the deadline: `test_reconnect.py:901`.
- Loop closed: `_post` drops the callback. `test_reconnect.py:931`.
- Exception mid-read: the `finally` closes the link. `test_reconnect.py:1358`.

Residual (reasoned, not driven): the retry in `_on_read_conn` does not check whether the store has stopped.

- A read that is already inside a worker when `stop()` runs reopens a connection after `_close_read_conns`, and nothing closes that connection before the process exits.
- My probe (`r8.py`) could not queue a read across `stop()`, because the pool has spare workers, so this stays unproven.

## Class 9. CLI exit-code contract (complete)

Sweep: `raises.py`, an AST walk of `raise` and `except` nodes. It found 116 sites: `cli.py` 63, `cli_client.py` 15, `cli_daemonctl.py` 9, `cli_output.py` 28, `cli_argv.py` 1.

Ruled by type:

| Type | Sites | Mapping in `main()` | Verdict |
|---|---|---|---|
| `typer.Exit` | 19 | `EXIT_EXCEPTIONS` -> `_mapped_exit` | complies |
| `typer.BadParameter` | 9 | `USAGE_ERRORS` -> 1 | complies |
| `ValueError` | `cli_output.py:328`, `:346` | raised inside the `try` at `:348` and re-raised as `BadParameter` | complies |
| `AssertionError` | `cli_client.py:140`, `:215`, `:269`; `cli_daemonctl.py:48` | unreachable, after `die()` | exempt |
| `ctypes.WinError` | `cli_daemonctl.py:85` | an `OSError`; the caller catches it at `cli.py:2631` | complies |
| `SystemExit` | `cli.py:3274` | the entry point | exempt |
| bare re-raise | 10 | each sits in an `OSError` arm whose `output_failed()` or `_stdout_unwritable` already set the exit | complies |
| `except` arms | 70 | each catches and maps to `die`, `_dispatch_error` or an exit code | complies |
| undeclared `ValueError` from a library call | `pidfile.read_pid_record` | none | **violates (R13-1)** |

Driven through the installed `mcu` console script, not `python -m`:

- `lines --match <memory-heavy pattern>`: exit 1, `error: ` with an empty message (the daemon answered `500 {"error":""}`, see R1-1).
- `--url 'http://[::1' status`: exit 3.
- `status`, `lines` and `--json lines` against a plain `http.server` answering 404 HTML: exit 1, no traceback.
- `daemon stop` and `daemon start` with an undecodable pid record: traceback (R13-1).

Test files run singly, all passing:

- `test_cli_contract.py`: 53 passed.
- `test_cli_small_refusals.py`: 16 passed.
- `test_cli_closed_pipe.py`: 21 passed.

Sweep imprecision: enumerating `raise` and `except` in `cli*.py` cannot find an exception a *callee* in another module raises undeclared, and R13-1 is exactly that.

- Improved sweep: for every non-CLI function the CLI calls (`pidfile.*`, `config.*`, `dirs.*`, `protocol.*`), list the exception types it can raise, and check each against `main()`'s arms.
- Treat any `open(..., encoding=...)` followed by `.read()` as able to raise `ValueError`.

## Class 10. --json stdout purity (complete)

Sweep: `c10.py` walked the click tree (42 leaf commands). It ran every leaf through the installed `mcu` with `--json` against an isolated `mcuscoped --sim` on port 8673:

- once with no arguments (the refusal paths);
- once more for 25 leaves, with a plausible argument set (the success paths).

It asserted `json.loads(stdout)` on the whole of stdout, and per line for the three JSONL exemptions (`tail`, `log export`, `can dump`).

- 64 runs, 0 failures, no traceback.
- `daemon start`, `daemon restart` and `daemon stop` were driven separately against the same isolated daemon: each printed exactly one JSON object, exit 0.
- `mcu ui` does not exist as a leaf, so no browser was opened.

## Class 11. Codec symmetry in protocol.py and the sim (complete)

Sweep (`c11.py`):

- Round trips of `format_can_event`/`parse_can_event`: 3660 frames over ext x rtr x id (0, 1, the limit, the limit+1, -1, random) x bus {1, 2, 9} x dlc x tick.
  - In domain: every frame round-trips field for field.
  - Out of domain: `format` refuses with `ProtocolError`.
- Round trips of command, family, flags and marker: all pass.
  - My probe's three "failures" were `seq=0`, which `format_command` refuses symmetrically with `parse` (SEQ_MIN is 1). Probe error, not a defect.
- Fuzz, 360,000 calls: `parse_can_event`, `parse_marker`, `parse_plot_adhoc`, `parse_plot_def`, `parse_plot_value`, `parse_can_family` and `decode_plot_sample`, on malformed input, never raised.
- `parse_command`, `parse_response`, `parse_hex_int`, `parse_seq_token`, `parse_can_flags` and `parse_can_tx_args` raised only `ProtocolError`.
- Sim fuzz (`c11sim.py`): 40,000 command lines through `Simulator.handle_line` plus `poll_events`. No exception, and every `!can` the sim emitted parsed back.

Complies.

## Class 12. Healthy-while-dead surfaces (Linux part complete; the live worker-kill probe belongs to the measurement leg)

- PRAGMA read-back.
  - Applied at `store.py:616`-`:653`. The read-back probe on a fresh store gave `foreign_keys 1`, `synchronous 1`, `journal_size_limit 67108864`, `auto_vacuum 2`, `journal_mode wal`, `cache_size -65536`.
  - `auto_vacuum` and `journal_mode` are read back at startup (`:620`, `:632`). `foreign_keys` is not, but `test_plot.py:338` pins it.
  - Complies.
- Web UI fetch helpers: 34 `await api(`/`fetch(` sites and 89 `catch` arms.
  - I ruled the helpers whose failure value a caller renders:
    - `settings.js:33` `refreshConfig` returns null and the banner shows. Complies.
    - `settings.js:41` `loadDevices`: **violates (R12-1)**.
    - `statusbar.js:633` keeps `err`. Complies.
    - `api.js:368` (per-port seed list): falls back to the successful unfiltered list, which is less history, not stale data. Complies.
    - `api.js:413`, `:420` (seed): no history, and live data still draws. Complies.
  - The remaining catch arms guard row ingest, socket close or JSON parse; none returns a value rendered as current.
- User-named input files missing:
  - `mcuscoped --config nö-such-ß.toml`: exit 1, `no such config file`.
  - `MCUSCOPED_CONFIG=<missing> mcuscoped`: exit 1, announced.
  - `MCUSCOPED_CONFIG=<missing> mcu daemon start`: exit 1, announced before any spawn.
  - `mcu daemon start -c <missing>`: pinned by `test_cli_daemonctl.py:324`.
- Worker death.
  - Store writer: `writer_alive` is on `/status`, tested in `test_store_writer.py` and `test_server_status.py`.
  - Serial reader: every statement outside its guards was checked, and none can raise. `status()` has no `is_alive()` backstop, so a future unguarded raise would freeze `connected` at its last value. Observation, not a finding.
  - The live kill-each-worker probe is owed to the measurement leg.
- Owed (browser): R12-1 and the rest of the checklist's UI half.

## Class 13. Windows file-sharing and encoding semantics (complete)

- `grep -rn "os.replace\|os.rename"` returned 3 lines.
  - `config.py:573`: docstring. `config.py:586`: inside `replace_atomic`. Both comply.
  - `update_check.py:288`: comment, exempt.
  - Alternate spellings (`shutil.`, `.rename(`, `Path.replace`): 0 hits.
- Reads of user-editable files:
  - `config.py:180`/`:187` and `:557`/`:567` use `utf-8-sig`. Comply.
  - `update_check.py:186`: a cache, not user-editable. Exempt.
  - `lockfile.py:170` catches `UnicodeDecodeError`. Complies.
  - `pidfile.py:143`: **violates (R13-1)**.
- `os.remove`/`os.unlink` on a path this process may hold open: 13 sites.
  - `sim.py:1172`, `:1181`: symlinks, exempt.
  - `cli_output.py:179`: both callers close first (`cli.py:1814`, `cli_client.py` `with` exits before `finally`). Complies.
  - `update_check.py:222`: `write_bytes` closed. Complies.
  - `cli_daemonctl.py:227`: not held. Complies.
  - `cli_daemonctl.py:256`: the `with` closes before `except`. Complies.
  - `pidfile.py:215`: stale, not held. Complies.
  - `pidfile.py:236`: close precedes the remove, emulated by `test_pidfile.py:201`. Complies.
  - `pidfile.py:249`: complies.
  - `cli.py:2815`: complies.
  - `server.py:2764`: `_TempFileResponse` after `FileResponse` closed its file; the build path after the connection and zip closed. Complies.
    - The lifespan finaliser may unlink a file a still-running build holds. On Windows that fails, suppressed, and the next start's sweep removes it; this is documented at `server.py:2946`. Exempt.
  - `server.py:2778`: the startup sweep; earlier runs' files are not held. Complies.
- `grep -rn "EINVAL\|except BrokenPipeError"` returned 20 lines.
  - Every `except BrokenPipeError` consumer (`cli_output.py:234`, `:243`, `:269`, `:284`; `cli.py:1282`, `:1849`, `:1874`, `:3162`; `cli_client.py:291`) relies on `_stdio.translate_closed_pipe_errors`, which is tty-gated (`_stdio.py:309`). Comply.
  - No write bypasses the translator: grep for `.buffer`, `os.write(1`, `sys.__stdout__` returned 0.
  - `sim.py:763`: socket errno set. Exempt.
  - `_stdio.py:233`-`:302`: the translator itself. Complies.
- `grep -rnE "CreateFileW|OpenProcess\("` returned 4 lines.
  - `cli_daemonctl.py:82`: the handle is used by `open_osfhandle`, `FileIO` `fstat` (`FILE_READ_ATTRIBUTES`), a seek to end (none needed), `os.fstat` at `cli.py:2630`, and `DuplicateHandle` for the child (same access), whose writes need `FILE_APPEND_DATA`. All present. Complies, reasoned.
  - `pidfile.py:86`: `WaitForSingleObject` needs `SYNCHRONIZE`, present. Complies.
- Redirected output driven with `PYTHONIOENCODING=ascii:strict` and `latin-1:strict`:
  - `mcu --url http://ünï.invalid:1 status`: exit 3, no traceback.
  - `mcuscoped --config nö-such-ß.toml`: exit 1, a clean message.

Owed (Windows): the real `CreateFileW` handle rights and the sharing-violation paths.

## Class 14. Platform-gated fixes (complete)

`grep -rnE "sys\.platform|os\.name|hasattr\((socket|signal|os), |getattr\(os, " host/mcuscope` returned **26** sites. The registry's last count was 20, and it has grown with `_stdio.py`.

| Site | What enforces the invariant on the other OS | Verdict |
|---|---|---|
| `daemon.py:247` | POSIX `SO_REUSEADDR` probe bind fails `EADDRINUSE` on a live listener, same order (before claim) | complies |
| `daemon.py:378` | POSIX stop sends SIGTERM, same handler list | complies |
| `daemon.py:380` | Windows terminal close is `CTRL_CLOSE` -> `console_close_hook` (`daemon.py:306`, `:406`) | complies |
| `serial_link.py:122` | ghost serial8250 slots are a Linux kernel artefact; setupapi lists present ports only | complies |
| `serial_link.py:167` | COM names have no symlink to resolve | complies |
| `serial_link.py:500` | POSIX `os.path.exists(dev)`; Windows enumeration | complies |
| `cli_daemonctl.py:68` | POSIX `O_APPEND` on the shared description | complies |
| `cli_daemonctl.py:86`, `lockfile.py:123`, `pidfile.py:45` | `O_BINARY`: POSIX fds do no translation | complies |
| `cli_daemonctl.py:314` | POSIX spawns the interpreter directly (`sys.executable -m`), so `pid` is the record | complies |
| `sim.py:749` | class 3 | complies |
| `sim.py:1102` | `--pty` refused with exit 2 on Windows; TCP is the cross-platform default | complies |
| `lockfile.py:42` | `flock` against a `msvcrt` byte lock: both exclusive, non-blocking, released on death | complies |
| `pidfile.py:80` | POSIX `kill(0)` + zombie check; Windows `OpenProcess`+`Wait`; both treat access denied as alive | complies |
| `_stdio.py:49`, `:73`, `:119` | POSIX: CPython wires SIGINT itself; SIGHUP handled at `daemon.py:380` | complies |
| `_stdio.py:146`, `:154`, `:179` | POSIX: devnull plus `stdout_was_closed` reporting | complies |
| `_stdio.py:203` | Windows: a missing stdout is reattached to a console (delivered), so there is nothing to report | complies, residual below |
| `_stdio.py:240` | POSIX raises `BrokenPipeError` natively | complies |
| `_stdio.py:426` | message wording only | complies |
| `cli.py:2642` | POSIX `start_new_session`; Windows `DETACHED_PROCESS` + new group | complies |
| `server.py:3810` | by-id is a Linux concept; COM names are stable | complies |

Residual at `_stdio.py:203`: on Windows with no console available (AllocConsole fails; `pythonw`, not a shipped launcher), `mcu` output goes to devnull and the command exits 0. POSIX reports this case. Reasoned only; owed on Windows.

Gates the sweep misses:

- `sim.py:764`, `hasattr(errno, name)`: an errno capability gate, complies.
- `link.py:166`, `:173`, `:185`, `hasattr(self._ser, ...)`: transport gates, not OS gates (class 8 territory).
- `server.py:166`, `except ImportError`: a uvicorn implementation gate.

Improved sweep: `grep -rnE "sys\.platform|os\.name|platform\.system|hasattr\((socket|signal|os|errno), |getattr\((os|errno|signal|socket), |except (ImportError|ModuleNotFoundError)" host/mcuscope`.

## Owed (needs Windows or a browser)

- Class 3: the `SO_EXCLUSIVEADDRUSE` probe on Windows.
- Class 13:
  - `CreateFileW` handle rights and `fstat` on Windows.
  - The sharing-violation paths: `replace_atomic`, and `_TempFileResponse` unlinking against an open handle.
- Class 14: the `_stdio.py:203` consoleless residual.
- Class 12: R12-1 in a real browser, and the live worker-kill probe.

## The two questions (docs/REVIEW.md)

1. **What am I least confident about?**
   - R1-1's claim that an uncapped daemon is OOM-killed.
     - Driven: the loop stall (582 ms, 3.3 s) and a capped daemon's MemoryError -> 500.
     - Not driven: the uncapped kill. It rests on a standalone process exiting 137.
     - I did not re-drive it, because doing so risks freezing this 16 GB desktop.
   - R1-2: the loop stall is the product of a measured unlink cost (480 ms for 2 GB) and a code reading that the unlink runs in `async def __call__`. It was not driven with a real multi-GB export.
   - Re-driven here: R13-1's `daemon start` path, which confirmed a traceback after the spawn.
2. **What should we have checked that we have not thought about?**
   - The compile-time cost of *every* user-supplied grammar, not only regex: `--deadband`, `names=`, `id=` lists and CAN id lists all reach server-side parsing. Only regex has a known expansion blow-up, but none of them was timed at its maximum length.
   - The other readers of hand-editable files, for bytes that do not decode. Rechecked:
     - the lockfile holder read catches `UnicodeDecodeError`;
     - `update_check._load_cache` catches `ValueError` (`update_check.py:189`);
     - `config.py` decodes inside its guard.
     - So `pidfile.py:143` is the only such reader.
   - The empty `{"error":""}` 500 that a MemoryError produces is a class 18 shape (an unmapped exception at a boundary). It is outside this leg's range and is flagged for the 15-28 leg.
- No new defect class: every finding is an instance of classes 1, 9/13/7 and 12.
