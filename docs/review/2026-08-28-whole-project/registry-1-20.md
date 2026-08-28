# Registry sweep leg, classes 1-20

Round: 2026-08-28. Repo `/home/daniel/git/mcuscope`.
HEAD checked: `fd76735 POST /ports held to the config-write bar` (matches the expected fd76735).

Every sweep below was executed under `docs/REVIEW.md` "Sweep discipline": the site count the sweep command returned is stated first, and every site carries a verdict.
Probes ran from `/home/daniel/git/mcuscope/host` with `uv run python`.

Verdict summary: **22 violates (22 confirmed, 0 suspected)**, plus 1 sweep-coverage gap where the code complies but no test asserts the cell.

| Class | Sites swept | Violates (confirmed) |
|-------|-------------|----------------------|
| 1 Blocking work on the loop | 1 grep line + 33 routes + 4 locks | 0 |
| 2 Text writes without `newline=` | 19 grep hits + 3 filtered writes + 2 non-`open` writes | 0 |
| 3 Listeners without Windows exclusivity | 12 grep hits, 3 real sockets | 0 |
| 4 Per-attach state lost on reattach | 7 reported fields + 9 unreported attributes | 0 |
| 5 argv hoisting | 636 matrix cells + 1 degradation probe | 0 |
| 6 Non-finite into chart arrays | 19 producers | 0 |
| 7 Pid record lifecycle | 20 matrix cells | 0 code, 1 unasserted cell |
| 8 Thread teardown | 8 threads/pools | 0 |
| 9 CLI exit-code contract | 47 executable sites + 61 driven failure modes | 5 |
| 10 `--json` stdout purity | 40 invocations + 21 error paths + 112 write sites | 1 (class 9's root seen through `--json`) |
| 11 Codec symmetry | 19,484 round trips + 139,000 fuzz cases | 0 |
| 12 Healthy-while-dead | 4 workers + 4 PRAGMAs | 1 |
| 13 Windows file/encoding semantics | 3 + 3 + 16 + 18 grep hits | 0 |
| 14 Platform-gated fixes | 20 gates | 0 |
| 15 Shipped artifact vs stand-in | 7 deliverables | 0 |
| 16 One bad item ends the loop | 41 loops | 0 |
| 17 Reported value is the request | 22 fields + 4 applied settings | 3 |
| 18 Unmapped third-party exceptions | 45 grep lines, 25 call sites | 2 (both re-counted from class 9) |
| 19 Two engines validating one thing | 43 duplicated validations | 10 |
| 20 Non-sargable bound on a hot query | 47 statements | 1 |

Distinct defects, after removing the double-counting between classes 9/10/18 and between 12/17: **17**.

(The per-class sections below and the four delegated files are authoritative; the table is a reading aid.)

---

## Findings

### F1 (class 12 + 17, CONFIRMED). `auto_vacuum` is applied and never read back, so the reclaim is silently a no-op on every pre-existing capture

`host/mcuscope/store.py:453` executes `PRAGMA auto_vacuum=INCREMENTAL` and discards the result.
The pragma only takes effect before the database header is materialised, so on any capture file that already exists it stays 0 and every `_reclaim_pages()` call (`store.py:159`) is a no-op.
Nothing reads it back, nothing logs, and no `/status` field moves: the sibling PRAGMA three lines down (`journal_mode=WAL`) *is* read back and warned about, for the identical reason.
This is the original class-17 bit ("auto_vacuum silently stayed 0, so every incremental_vacuum was a no-op") still open in the read-back sense.
The code comment acknowledges the plateau behaviour, but class 12's sweep requires the read-back and a health surface that changes state.

Probe:

```
$ cd host && uv run python -   # pre-create the DB with auto_vacuum=NONE + WAL, then Store.start()
auto_vacuum after start = 0
foreign_keys after start = 1
journal_mode = wal
synchronous = 1
```

One of two siblings guarded, which the registry names as the shape to look for.

### F2 (class 17, CONFIRMED). `write_errors` reads 0 while lines are being lost, and `_fail_write`'s stated invariant is false

`host/mcuscope/store.py:567-576` `_fail_write` docstring: "Every path that loses a line goes through here so `write_errors` is the count of lines the capture was handed and did not store, whatever the reason".
`submit_line` (`store.py:840-842`) raises `StoreError("store writer is not running")` **before** constructing the `_WriteReq`, so the fast-fail path never reaches `_fail_write` and never increments the counter.
`/status` documents the field (`server.py:829-831`) as "Lines the capture was handed and could not store. Non-zero means received lines were lost, which no other field on this response reveals."
It reads 0 in exactly the state it exists to reveal.

Probe (in-process sim+daemon stack, store writer task cancelled):

```
baseline rx_dropped 0 lines_rx 1
writer dead: rx_dropped 23 lines_rx 24 write_errors 0 writer_alive False
```

23 lines lost, `write_errors` 0.
Loss is not invisible (`rx_dropped` moves and `writer_alive` goes False), so this is a reporting defect rather than a healthy-while-dead one, but the invariant is stated and untrue and the /status wording is wrong.
A stated invariant is a claim, not a mechanism (class 1's note); this one has no test.

### F3 (class 17, CONFIRMED). `lines_rx` counts lines that were never stored

Same probe: `lines_rx` moved 1 -> 24 across a dead writer.
`serial_link.py:788` does `self.lines_rx += 1` on the statement immediately **before** `await self._store.submit_line(...)`, which is the registry's wording of the bit verbatim.
This is the registry's own class-17 bit verbatim ("`lines_rx` incremented before the write was submitted, so a full disk showed lines arriving and nothing stored").
It is arguably correct-by-name (lines *received*), and `rx_dropped` carries the loss, so the honest verdict is: the field is defensible, the pair of it with a 0 `write_errors` is not.
Filed together with F2; fixing F2 resolves the misleading surface.

### F4 (class 17, CONFIRMED-by-reading). A port attached by `serial_number` reports the serial number, never the device it opened

`serial_link.py:1043`: `"device": self.device if self.device is not None else self.serial_number`.
For a `serial_number=` attach, `self.device` stays `None` for the port's whole life; `_resolve_device()` (`serial_link.py:379`) computes the real `/dev/tty*` or `COMx` into a **local** `dev` at `serial_link.py:456` and hands it to `_open_link`, and nothing stores it.
So the health surface reports what was asked for (a serial number) and never what happened (which device it landed on), which matters precisely when two boards with similar serials are on the bench.
No probe against hardware was available; the code path is unambiguous by reading and no test asserts a resolved device is reported.

### F5 (class 20, CONFIRMED). `active_session` scans the whole `sessions` table on the event loop

Delegated sweep, full evidence in `/tmp/claude-1000/review-r2/class20.md`.
`store.py:939` plans `SCAN sessions` for `WHERE ended_ts IS NULL ORDER BY id DESC LIMIT 1` (no index on `ended_ts`).
It short-circuits only while a session is running; with no active session it reads every row, on the loop, from `GET /status` (`server.py:837`) and four other handlers.
Probed at 5000 sessions with no `sqlite_stat1`: `PLAN: SCAN sessions`, 1.860 ms/call.

### F6-F10 (classes 9, 10, 18, CONFIRMED). Five CLI paths reach the user as a traceback, and each one also emits no JSON document

Delegated sweep, full evidence in `/tmp/claude-1000/review-r2/classes-9-10-18.md`.
All five were driven through the **installed console script**, as class 9 requires.

- **F6** `cli_client.py:104` `_daemon_errors` omits `ValueError` from its tuple, so a `UnicodeEncodeError` raised by httpx while encoding a header or query escapes every handler.
  Independently re-probed here: `uv run mcu --token 'tökén' status` -> rc=1, empty stdout, rich traceback and a crash file at `~/.local/share/mcuscope/mcu-crash.log`.
  Reached from `MCUSCOPE_TOKEN` too, and from any non-UTF-8 argv byte (`mcu lines --match $'\xff\xfe'`, `cmd`, `attach`, `session start`).
  The sibling `probe` at `cli_client.py:125` already catches `ValueError`, so `mcu --token 'tökén' daemon status` is clean at rc=3: one of two siblings fixed, and the class-18 strict-subset shape exactly.
- **F7** `cli.py:550`, guarded at `:552` and `:632` by `json.JSONDecodeError` alone. A WebSocket binary frame whose bytes are not valid UTF-8 raises `UnicodeDecodeError`, which is a `ValueError` but not a `JSONDecodeError`, so it clears both guards. Sibling sites at `cli_client.py:41, 125, 133` all catch `(json.JSONDecodeError, ValueError)`. Class 18 as well.
- **F8** `cli.py:1322`, `fh.close()` in `plot_export`'s `finally`. The buffered write is flushed by the close, outside every handler, so `_dispatch`'s OSError arm re-raises it: `mcu plot export --names t -o /dev/full` is a traceback where `log export` and `session export` on the same target exit 1 cleanly.
- **F9** `cli.py:294` -> `cli_client.py:104`: `mcu cmd x --timeout 99999999999999999999` raises `OverflowError`. `finite_option` guards `purge --before-days` and `daemon start --timeout`; the millisecond timeouts on `cmd`, `wait` and `assert` have no range guard. Class 22's "is it finite and in range?" face, reached through class 9.
- **F10** `cli.py:149, 268, 739, 771, 782` read scalar and object daemon fields unguarded. A stub answering `{"session":"x","port":"notadict","expect":null}` makes `status`, `attach`, `assert`, `session start` and `session stop` exit 1 with `TypeError`/`AttributeError` tracebacks. `_list_field` vouches for list fields and their elements (class 9's own 2026-08-10 bullet); nothing vouches for the rest.
  Design tension to resolve rather than assume: `cli.py:1737-1740` argues `TypeError` must stay unmapped because it is the shape of a genuine CLI bug. That holds for the dispatcher, but these five inputs are daemon-controlled, which is the case the guard bullet covers. The `_list_field` precedent points at a per-field guard at the point of use, not a dispatcher arm.

Class 10 has no violation independent of these: the unmapped exception never reaches `die()`, which is where the `--json` error object is written (`cli_output.py:78-79`), so every one of the five emits an empty stdout under `--json`.
The registry's named class-10 regression (`mcu --json status --url`) is closed, all three JSONL exemptions behave, and all 45 print sites target the right stream.

### F11-F20 (class 19, CONFIRMED). Ten duplicated validations where the mirror dropped a clause

Delegated sweep, full evidence in `/tmp/claude-1000/review-r2/classes-6-19.md`. 43 duplicated validations enumerated; 10 violate.
Every one is the shape the registry predicts: a **bound or a guard** the mirror omitted while copying the check next to it.

- **F11** `plots.js:144` `parseEnumLabels` tests the *value* (`!signed && v < 0`) where `protocol.py:642` and `monitor.c:345` test the *sign character*. Verified here by reading both: `-0` on an unsigned channel builds the enum lane in the browser and returns `None` from `parse_plot_def`. The digit-count cap added for this same function last round sits one line above the defect.
- **F12** `protocol.py:707` `_decode_field` has no finiteness check; its mirror `plots.js:195` does. `7F800000` stores as `inf`; REST reports `null`, `/plot/export` writes a literal `inf`, and three consumers each carry their own hand-patch.
- **F13** `protocol.py:755` `decode_plot_sample` does not re-check finiteness after `*scale`; `plots.js:227` does. Hits integer channels, which F12's fix would not reach.
- **F14** `monitor.c:434` `parse_plot_body` has no cross-field name uniqueness check. `dup:u2 dup:u2` and `flags:u1 x:u1:/flags,b1` both return 0 and emit, and `parse_plot_def` (`protocol.py:610-622`) drops both definitions whole. The function's own header comment says it exists to prevent this.
- **F15** `monitor.c:642` `emit_can_event` clamps `dlc` but not the id, so `id=0x800, ext=false` emits `!can 0 - 800 AA`, which `parse_can_event` refuses. `format_can_event` (`protocol.py:352-358`) carries this exact check with the comment "so format and parse accept the same set".
- **F16** `monitor.c:626` `monitor_mark` checks only for empty text. Verified here by reading: with no `tick_ms` hook it emits `monitor_eventf("m %s", text)`, so `monitor_mark("@1234 hello")` is read back by `parse_marker` as tick 1234 with text "hello". `format_marker` refuses that input.
- **F17** `config.py:331` the loader accepts `baud=999999999` (`_as_int(..., 1, _INT_MAX, strict=False)`) while `ConfigPortEntry` refuses it, so the settings dialog's ports save 422s on an entry the daemon started with.
- **F18-F20** (low consequence) `settings.js:383`, `statusbar.js:413`, `settings.js:423`, `cmdbar.js:115`: the lower bound was copied and the upper bound (`MAX_BAUD`, `1<<42`, `MAX_TIMEOUT_MS`) omitted. The daemon refuses and its message surfaces, so the cost is a round trip rather than a divergence, but they are the same omitted-bound shape and are listed so a later round does not have to rediscover them.

### Out-of-range observation (class 39, not in this leg's range)

The class-12 probe produced, on the daemon's stderr:

```
Task exception was never retrieved
future: <Task finished name='Task-13' coro=<SerialPort._store_sys() done, defined at host/mcuscope/serial_link.py:865>
        exception=StoreError('store writer is not running')>
```

`_spawn_sys` creates a task whose `StoreError` nothing retrieves, so asyncio prints the traceback at collection time.
Reported here rather than acted on; class 39 belongs to another leg.

---

## Class 1. Blocking work on the event loop or default executor

**Sweep A: `grep -rn "run_in_executor(None" host/mcuscope` returned 1 line.**

- `serial_link.py:37` - exempt because it is a comment explaining the inverted rule, not an executable line. The sweep's stated requirement ("must return no executable line") holds.

**Sweep B: every `async def` endpoint in server.py, traced for store/os/serial calls. AST enumeration returned 33 routes.**

Store reads offload through `Store._offload` (`store.py:1457`), which runs on `match_executor()`, never the default pool; the `*_safe` suffix marks the offloaded variant. Verdicts:

| Route | Handler | Verdict |
|---|---|---|
| GET `/` | `_root` L737 | complies (no I/O) |
| GET `/status` | `status` L801 | complies - `db_size_bytes` (2 `os.path.getsize`), `content_bytes` (3 O(1) header PRAGMAs), `max_db_bytes` (attribute), `active_session` (see class 20 F5, which is the finding; the *offload* question is ruled complies because the query is intended as a bounded seek) |
| POST `/shutdown` | `shutdown` L852 | complies |
| GET `/ports` | `get_ports` L878 | complies (in-memory) |
| POST `/ports` | `attach_port` L882 | complies |
| DELETE `/ports/{alias}` | `detach_port` L899 | complies (join goes to `serial_link._join_pool`) |
| POST `/ports/{alias}/reconnect` | `reconnect_port` L906 | complies |
| GET `/devices` | `devices` L921 | complies - `asyncio.to_thread(_enumerate_devices)` at L927 |
| GET `/config` | `get_config` L957 | complies - `asyncio.to_thread(load_config, path)` at L960 |
| PUT `/config/server` | `put_config_server` L1000 | complies - `to_thread(save_server, ...)` L1008 |
| PUT `/config/storage` | `put_config_storage` L1016 | complies - `to_thread(save_storage, ...)` L1028; the three setters are attribute writes |
| PUT `/config/update` | `put_config_update` L1054 | complies - `to_thread(save_update, ...)` L1059 |
| PUT `/config/plotjuggler` | `put_config_plotjuggler` L1070 | complies - `to_thread(save_pj, ...)` L1079 |
| GET `/plotjuggler` | `get_plotjuggler` L1089 | complies |
| PUT `/plotjuggler` | `put_plotjuggler` L1094 | complies - `to_thread(pj.configure, ...)` L1106 (DNS resolution is the blocking part) |
| PUT `/config/ports` | `put_config_ports` L1114 | complies - `to_thread(save_ports, ...)` L1142 |
| GET `/sessions` | `list_sessions` L1170 | complies - `list_sessions_safe` L1175 |
| POST `/sessions` | `start_session` L1180 | complies |
| POST `/sessions/stop` | `stop_session` L1184 | complies |
| DELETE `/sessions/{id}` | `delete_session` L1200 | complies - `get_session`/`max_id` are indexed single-row reads; the bulk work is `delete_range` (chunked, yields) |
| GET `/sessions/{ref}/export` | `export_session` L1228 | complies - `to_thread(store.export_session_db, ...)` L1243; the `os.close`/`os.unlink` are on a just-created temp file |
| POST `/purge` | `purge` L1264 | complies - `count_lines_safe` L1303 offloads; `last_id_before_ts` is a documented one-seek index lookup (`store.py:1485`); `delete_range` chunks |
| POST `/send` | `send` L1310 | complies - the write reaches `_write_bytes` through `to_thread` |
| POST `/cmd` | `cmd` L1322 | complies (same) |
| GET `/lines` | `lines` L1333 | complies - `query_lines_safe` L1358 |
| GET `/can/frames` | `can_frames` L1375 | complies - `query_can_frames_safe` L1395 |
| GET `/plot/channels` | `plot_channels` L1402 | complies - `query_plot_channels_safe` L1409 |
| GET `/plot/series` | `plot_series` L1432 | complies - `query_plot_series_safe` L1445 |
| GET `/plot/export` | `plot_export` L1452 | complies - `export_sids_safe` L1469, `count_plot_export_safe` L1477, `open_plot_export` L1487 |
| POST `/wait` | `wait` L1498 -> `_do_wait` L1743 | complies - regex through `run_in_executor(match_executor(), ...)` L1788 |
| POST `/assert` | `assert_` L1508 -> `_do_assert` L1854 | complies - `match_executor()` L2010, L2020 |
| POST `/marker` | `marker` L1515 | complies |
| WS `/ws` | `ws` L1535 | complies - `subscribe`/`unsubscribe`/`take_dropped` are dict operations |

**Sweep C (the lock clause): `grep -rn "threading.Lock\|threading.RLock\|Lock()" host/mcuscope` returned 10 sites, 4 of them `threading` locks.**

- `serial_link.py:277` `_write_lock` - complies. Three acquirers, all off-loop: the reader thread's `finally` (L525), `_close_link_locked` reached via `to_thread` (L344, L880), and `_write_bytes` reached via `to_thread` (L909). The loop-side acquire that caused the 2026-08-09 finding is gone and the reason is in the comment at L344.
- `serial_link.py:77` `_comports_lock` - complies. Held only around the cache install (L127-133), never across the scan; callers are the reader thread and `to_thread`.
- `store.py:311` `_match_pool_lock` - exempt because it guards a one-time `ThreadPoolExecutor` construction (L331-336) with no I/O under it; the loop-side caller (`server.py:1789`) pays it once.
- `link.py:207` `SourceLink._lock` - exempt because both acquirers (`write` from `_write_bytes` via `to_thread`, `read` from the reader thread) are off-loop.

Verdict: class 1 clean.

## Class 2. Text writes without explicit newline

**Sweep: `grep -rn "open(" host/mcuscope | grep -v 'newline\|"rb"\|os.open'` returned 19 lines, plus 1 `write_text(`.**

| Site | Verdict |
|---|---|
| `serial_link.py:107` | exempt - read of `/sys/class/tty/*/type` |
| `_stdio.py:147` | exempt - `CONOUT$`, a console handle; there is no file on disk whose byte count could differ |
| `_stdio.py:155` | exempt - `os.devnull`; output is discarded |
| `_stdio.py:167` | exempt - `CONIN$`, a read |
| `pidfile.py:113` | exempt - read of `/proc/{pid}/stat` |
| `pidfile.py:140` | exempt - read of the pid record |
| `server.py:1681`, `:1724`, `:1768`, `:1973` | exempt - `CaptureWatch.open()`, a method name, not a file open |
| `cli.py:1157` | exempt - `client.open()`, an httpx client |
| `cli.py:1419` | exempt - `subprocess.Popen`, matched on the substring |
| `cli_client.py:89`, `:103`, `:121`, `:158` | exempt - `Client.open()`, an httpx client |
| `cli_client.py:165` | complies - `open(out_file, "wb")`, binary |
| `config.py:387` | complies - `tmp.write_text(..., encoding="utf-8", newline="")` |

Writes the filter removed (verified they carry `newline=`, so the exclusion is sound rather than a truncation):

- `cli.py:953` `open(out_file, "w", encoding="utf-8", newline="\n")` - complies
- `cli.py:1309` `open(out_file, "w", encoding="utf-8", newline="")` - complies
- `cli.py:1427` `open(tmp_path, "w", encoding="utf-8", newline="")` - complies

Non-`open` writes checked as well: `update_check.py:206-209` writes **bytes** then `replace_atomic` (comment says "no newline translation, so the file is identical on both"), and `pidfile.py:223` uses `os.write(fd, ...encode("ascii"))` under `_O_BINARY`.
No CSV writer exists in `server.py` (the CSV export is built by hand in `_csv_long`/`_csv_wide` and streamed as text over HTTP, where the client's `newline=` governs).

Verdict: class 2 clean.

## Class 3. Listening sockets without Windows exclusivity

**Sweep: `grep -rn "socket.socket\|\.bind(" host/mcuscope` returned 12 lines, 3 of them real socket creations.**

- `sim.py:627`/`:638` `open_tcp_listener` - complies. `SO_EXCLUSIVEADDRUSE` under `os.name == "nt"` (L634), `SO_REUSEADDR` on POSIX, with the reason in the comment.
- `daemon.py:214`/`:227` the pre-bind probe - complies. `hasattr(socket, "SO_EXCLUSIVEADDRUSE")` (L218) on Windows, `SO_REUSEADDR` on POSIX to match what asyncio does for uvicorn's bind, and **every** resolved address is probed.
- `pjstream.py:117` - exempt because it is a UDP **sender**, never bound and never listening.

The remaining 9 hits (`sim.py:659, 708, 766, 840`, `pjstream.py:89, 94`) are type annotations.

Verdict: class 3 clean.

## Class 4. Per-attach state lost on reattach

**Sweep: diff `SerialPort.__init__`'s zeroed attributes against everything `/status` reports. `/status`'s per-port object has 7 fields (`serial_link.py:1040-1053`).**

| Field | Verdict |
|---|---|
| `alias` | complies - passed to the constructor on every attach |
| `device` | complies for a device attach; see F4 for the `serial_number` case (a class 17 finding, not class 4) |
| `baud` | complies - passed in |
| `connected` | complies - per-connection by design |
| `lines_rx` | complies - carried, `PortManager._carried` (`serial_link.py:1121`, `:1144`) |
| `lines_tx` | complies - carried |
| `rx_dropped` | complies - carried |

Test: `host/tests/test_reconnect.py:429-434` sets all four carried values and asserts `(lines_rx, lines_tx, rx_dropped, _seq)` come back identical after a detach/attach cycle.
`_seq` is carried too though it is not reported, for the reason stated at `serial_link.py:1074-1081`.

Attributes `__init__` zeroes that `/status` does **not** report, ruled anyway: `_rx_bytes`, `_rx_lines`, `_pending`, `_err_seen`, `_err_suppressed`, `_open_failures`, the four `_EpisodeNotice`es and `plot_decoder` are all per-connection by design (a reattach is a new physical link, and SPEC 2.5 has the firmware rebroadcast `!pd` every 5 s).
Store-level counters on `/status` (`lines_trimmed`, `write_errors`, `ws_dropped`, `capture`) live above the object reconnect recreates, which is exactly what the invariant asks.

Verdict: class 4 clean.

## Class 5. argv hoisting in cli.main()

**Sweep: the full {option position} x {subcommand} x {value-taking option} matrix, enumerated from the live typer app. 38 subcommand leaves, 53 value-taking options across them, 4 global options, 3 positions = 636 cells.**

Probe (all 636 cells driven through `cli_argv.split_global_opts`):

```
subcommand leaf count: 38
subcommands whose own value-opts collide with globals: []
matrix cells: 636
failures: 0
```

The three assertions per cell were: a global-looking token in a subcommand option's value position is not hoisted; a global appended at the tail is hoisted; a leading global does not break the value guard (the 187a0e4 bit).

Degradation direction (the 2026-08-10 sub-finding) probed separately by making `typer.main.get_command` raise:

```
value_taking_opts -> None
split -> ([], ['lines', '--limit', '5', '--json'])
```

Failure degrades to **no** hoisting, as the invariant requires, not to hoisting without the guard.

`uv run python -m pytest -k "hoist or argv"` : 15 passed.

Verdict: class 5 clean.

## Class 11. Codec symmetry in protocol.py and the sim

**Sweep: property-test `parse(format(x))` over the full domain, plus malformed-input fuzz asserting None or a documented `ProtocolError`, never a bare exception. 10 parsers and 4 formatters enumerated from `protocol.py`, plus the simulator's dispatch.**

Round-trip results:

- CAN: **19,392** cases - `{std, ext} x {0, 1, hi-1, hi, 400 random ids} x {data, rtr} x {dlc 0,1,4,8} x {tick 0, 1, TICK_MS_MAX}`. 0 asymmetries, 0 raises, 0 `None` returns on a self-formatted line.
- Command / response OK / response ERR (every code in `ERROR_NAMES`) / marker: **92** cases across `seq in {1, 2, 255, 1000, 65535}`. 0 asymmetries.

Fuzz results: **99,000** malformed inputs across `parse_can_event`, `parse_marker`, `parse_plot_adhoc`, `parse_plot_def`, `parse_response`, `parse_command`, `parse_seq_token`, `parse_hex_int`, `parse_plot_value`, `parse_can_flags`, `parse_can_tx_args`, `decode_plot_sample`, including `٣` (U+0663), `²`, `+5`, `1_7`, a 30-digit token and a 4400-digit token.
**Undocumented raises: none.**
The plot fuzz used three real SPEC 2.5 definitions (`!pd 0 tri:s2*0.01:V ramp:u2 ftest:f4`, `!pd 1 state:u1:=0=IDLE,1=ARMED,2=RUN`, `!pd 2 gpio:u1:/led,irq,pwm_en`), verified to parse before use, so the sample decode was exercised against a live definition rather than against `None`.

Simulator (the second implementation): **40,000** command lines through `Simulator.handle_line` (well-formed, garbled and headless) plus 3,000 `poll_events()` calls. 0 raises.
`_parse_dec` grammar spot-checked: accepts `3` and `007`, refuses `٣`, `²`, `+3`, `1_3`, `-1`, `0x3`, `' 3'`, `'3 '`, `''` and a 4400-digit token.

One asymmetry ruled deliberately exempt: `format_response_err` refuses codes outside `ERROR_NAMES` while `parse_response` accepts any code.
That is the tolerant direction (a foreign firmware may emit codes this host does not know), and the registry's own note says a real board is a second independent implementation whose error envelopes must decode.

Verdict: class 11 clean.

## Class 12. Healthy-while-dead surfaces

**Sweep: the probe checklist. 4 workers plus the PRAGMA/config read-back leg. This class is nominally the measurement leg's; the mechanical half is run here and the residue is named below.**

| Worker | Probe | Verdict |
|---|---|---|
| Store writer | cancelled `store._writer_task` on a live stack | complies - `/status.writer_alive` flipped `True` -> `False` immediately. But see **F2**: `write_errors` stayed 0 while 23 lines were lost. |
| Serial reader thread | set `port._stop` and joined the thread | complies - `/status.ports[0].connected` flipped `True` -> `False` within 1.5 s and `lines_rx` froze (1 -> 1 over the next 2 s) |
| Sim serving thread | code sweep of `serve_listener` (`sim.py:655-700`) | complies - the accept loop only breaks on `srv.fileno() == -1` or `_FD_DEAD_ERRNOS`, every client session is wrapped (`except Exception`), the `conn.close()` is inside the guard, and `serve_tcp` closes `srv` when the loop returns, so the listener can no longer outlive its thread |
| WS feed | code sweep of the `/ws` handler (`server.py:1535-1616`) | complies - `pump` and `watch` are raced with `FIRST_COMPLETED` and the `finally` cancels and awaits both, then closes the socket; a dead pump ends the connection rather than reading live |
| PRAGMA `journal_mode=WAL` | read back at `store.py:457-463` and warned | complies |
| PRAGMA `auto_vacuum=INCREMENTAL` | probed on a pre-existing DB | **violates, CONFIRMED (F1)** - reads 0, no read-back, no surface |
| PRAGMA `synchronous=NORMAL` | probed | complies - reads 1 (NORMAL). Not read back in code, but it cannot silently refuse the way the two above can. Exempt on that ground. |
| PRAGMA `foreign_keys=ON` | probed | complies - reads 1. Not read back in code; the class-29 mutation ("`foreign_keys=OFF` left 631 tests green") is a test-coverage finding, out of this leg's range. |

Owed to the measurement leg and **not** run here: killing the sim serving thread on a live TCP stack (only the in-process link stack was available), and the web UI's `streamOnline` watermark surface.

## Class 13. Windows file-sharing and encoding semantics

**Sweep A: `grep -rn "os.replace\|os.rename" host/mcuscope` outside `replace_atomic` returned 3 lines.**

- `config.py:360` - exempt, the docstring of `replace_atomic` itself
- `config.py:373` `os.replace(src, dst)` - complies, this **is** `replace_atomic`'s body, with the Windows sharing-violation retry
- `update_check.py:279` - exempt, a comment; the actual write at `:209` calls `replace_atomic`

**Sweep B: `encoding=` at every read of a user-editable file.**

- `config.py:136` `read_text(encoding="utf-8-sig")` - complies (BOM tolerated)
- `config.py:354` `read_text(encoding="utf-8-sig")` - complies
- `pidfile.py:140` `open(path, encoding="utf-8")` - complies; the record is machine-written ASCII digits, never hand-edited, so `utf-8-sig` is not owed

**Sweep C: `grep -rn "os.remove\|os.unlink" host/mcuscope` returned 16 lines. Each ruled on whether this process may still hold the path open.**

| Site | Verdict |
|---|---|
| `sim.py:957`, `:966` | complies - a symlink, no fd held |
| `cli_daemonctl.py:101` | complies - `read_pid_record` uses `with open`, closed before the remove |
| `cli_daemonctl.py:171`, `:179` | complies - no fd held on the pid path in either branch |
| `pidfile.py:196` | exempt - a comment |
| `pidfile.py:212` | complies - the stale-record remove; nothing of ours is open on it (the re-read used `with open`) |
| `pidfile.py:233` | complies - **this is the class's own bit**: `os.close(fd)` is in the inner `finally` (L230-232) and the remove is in the outer `except`, so the fd is closed first. The comment at L245-248 states the rule. |
| `pidfile.py:246` | complies - `release()`, no fd held |
| `server.py:1241` | complies - `os.close(fd)` on the line above |
| `server.py:1252` | complies - `export_session_db` closes its connection in a `finally` (`store.py` L1185-1186) before raising |
| `server.py:1816` | complies - `_unlink_later` runs as a `BackgroundTask`, i.e. after `FileResponse` has finished streaming and closed the file |
| `cli_client.py:180` | complies - `with open(out_file, "wb")` has closed by the time the outer `finally` runs |
| `cli.py:1328` | complies - `fh.close()` is the statement before `os.remove` in the same `finally` |
| `cli.py:1438` | complies - the `with open(tmp_path, ...)` block has exited |
| `cli.py:1497` | complies - no fd held |

**Sweep D: `grep -rn "EINVAL\|except BrokenPipeError" host/mcuscope` returned 18 lines.**

- `_stdio.py:204, 211, 215, 242, 243, 249, 252, 255` - complies. This is the boundary: `PIPE_CLOSE_IS_EINVAL` is `sys.platform == "win32"`, `translate_closed_pipe_errors` is tty-gated (L252-255), and the re-raise checks `exc.errno != errno.EINVAL` and passes everything else through unchanged.
- `cli_output.py:45`, `:140`, `cli_client.py:203`, `cli.py:610`, `:1353`, `:1673` - complies. Six consumers, all relying on the boundary translation; none classifies an errno itself, which is the rule the 2026-08-09 revert established.
- `cli_output.py:104`, `cli.py:1663`, `:1722` - exempt, comments.
- `sim.py:648` - exempt. `EINVAL` here is a member of `_FD_DEAD_ERRNOS` for a **socket** accept, a different subject from a closed pipe on stdout.

Verdict: class 13 clean.

## Class 14. Platform-gated fixes

**Sweep: `grep -rnE "sys\.platform|os\.name|hasattr\((socket|signal|os), |getattr\(os, " host/mcuscope` returned 20 lines** - matching the count the registry records for this form (13 for the `sys.platform|os.name` form alone).
One line per gate naming the other platform's enforcement:

| Site | Gate | Other platform's enforcement |
|---|---|---|
| `sim.py:634` | `os.name == "nt"` -> `SO_EXCLUSIVEADDRUSE` | POSIX `bind()` already refuses an actively-listening address; the `else` sets `SO_REUSEADDR` only to skip TIME_WAIT. complies |
| `sim.py:898` | `os.name == "nt"` -> `--pty` refuses | The invariant is "a transport exists"; TCP is the default on both, `--pty` is an opt-in POSIX extra. complies |
| `daemon.py:218` | `hasattr(socket, "SO_EXCLUSIVEADDRUSE")` | Same as above; the POSIX branch sets `SO_REUSEADDR` deliberately so the probe asks the same question uvicorn's bind will. **The probe itself runs on both platforms**, which is the fix for the 77e5a69 bit. complies |
| `daemon.py:257` | `hasattr(signal, "SIGBREAK")` | `SIGTERM` is handled unconditionally on both; SIGBREAK is added because it is what `mcu daemon stop` sends on Windows. complies |
| `pidfile.py:45` | `getattr(os, "O_BINARY", 0)` | 0 on POSIX, which has no newline translation to suppress. complies |
| `pidfile.py:80` | `sys.platform == "win32"` -> `OpenProcess`/`WaitForSingleObject` | POSIX branch uses `os.kill(pid, 0)`; both map ACCESS_DENIED / `PermissionError` to "exists", both bound the pid before the syscall. complies |
| `serial_link.py:101` | `sys.platform != "linux"` -> `_is_onchip_uart` | The invariant is "never hide a port on a guess": off Linux nothing is hidden, which satisfies it. The filter is cosmetic, not a guarantee. complies |
| `serial_link.py:403` | `os.name == "nt"` -> COM-name match | POSIX branch is `os.path.exists(dev)`; both answer "is this device present" for the presence-gated reconnect. complies |
| `_stdio.py:45` | `sys.platform != "win32"` -> `have_console` returns True | POSIX always has a console concept; the Windows branch probes `GetConsoleCP`. complies |
| `_stdio.py:64` | `install_console_ctrl_handler` returns False off Windows | POSIX gets SIGINT from the tty for free; the handler exists only for a late `AttachConsole`. complies |
| `_stdio.py:105` | `_ensure_console` returns False off Windows | POSIX std streams are never None at startup, so there is nothing to repair. complies |
| `_stdio.py:132` | repair attempted only when a stream is None | Same. complies |
| `_stdio.py:140`, `:165` | `CONOUT$` / `CONIN$` | The `if stream is None` fallback to `os.devnull` runs on both. complies |
| `_stdio.py:211` | `PIPE_CLOSE_IS_EINVAL` | POSIX raises `BrokenPipeError` natively; the translation exists to make Windows spell it the same way. complies |
| `_stdio.py:357` | wording of the repair notice | Cosmetic. complies |
| `lockfile.py:41` | `sys.platform == "win32"` -> `msvcrt.locking` | POSIX branch uses `fcntl`; both give a non-blocking exclusive byte lock that raises `OSError` when held. complies |
| `lockfile.py:103` | `getattr(os, "O_BINARY", 0)` | 0 on POSIX. complies |
| `server.py:2146` | `os.name != "posix"` -> `_by_id_map` empty | Windows has no `/dev/serial/by-id`; the map is enrichment of the device list, and its absence hides nothing. complies |
| `cli.py:1413` | `os.name == "nt"` -> `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` | POSIX branch is `start_new_session=True`; both detach the daemon from the launching terminal's signal group. complies |

Verdict: class 14 clean. 20 sites, 20 with a named counterpart.

## Class 17. Reported value is the request, not the result

**Sweep: every reported field on `/status` (18 top-level + 7 per-port) and every applied setting, traced to where its value comes from. 22 distinct value sources.**

| Field | Source | Verdict |
|---|---|---|
| `version` | module constant | exempt |
| `pid` | `os.getpid()` | complies (result) |
| `uptime_s` | `time.time() - start_time` | complies |
| `db_path` | `resolve_db_path(cfg)` on the **running** config, which `put_config_storage` deliberately does not update for `db_path` (`server.py:1040-1044` updates only retention/max/min/auto) | complies - the running value is the one in force |
| `db_size_bytes` | `os.path.getsize` on the file and its WAL | complies (measured) |
| `db_content_bytes` | `PRAGMA page_count/freelist_count/page_size` | complies (measured) |
| `db_max_bytes` | `store._max_db_bytes`, the cap in force, with the reason in the docstring | complies |
| `lines_trimmed` | store counter | complies |
| `write_errors` | store counter | **violates, CONFIRMED (F2)** - the fast-fail path bypasses `_fail_write` |
| `writer_alive` | `not self._writer_task.done()` | complies (the task's real state) |
| `ws_dropped` | store counter | complies |
| `capture` | `meta` row read from the DB at start | complies |
| `session` | `SELECT ... FROM sessions` | complies (read back) |
| `update` | previous check's answer, documented as such at `server.py:838-841` | complies - the staleness is stated, so it is not passing a request off as a result |
| `plotjuggler.enabled` | `app.state.pj.enabled`, runtime not config, documented at `server.py:843-844` | complies |
| `plotjuggler.dest` | `app.state.pj.dest` | complies (the class-40 fix made this and the socket one immutable tuple) |
| `ports[].alias` | constructor | exempt (identity, not health) |
| `ports[].device` | `self.device or self.serial_number` | **violates, CONFIRMED-by-reading (F4)** - the resolved device is never stored |
| `ports[].baud` | `self.baud`, echoed from the attach request; never read back from the opened link | violates in shape, but ruled **exempt** here: the `Link` abstraction has no baud to read back (`socket://` has none at all), and pyserial raises rather than silently coercing on the platforms in scope. Named so a later round does not have to rediscover it. |
| `ports[].connected` | set from the reader thread's real state | complies |
| `ports[].lines_rx` | incremented on receive, not on store | **violates, CONFIRMED (F3)** - see F2/F3; the field name is defensible, the pairing with a 0 `write_errors` is not |
| `ports[].lines_tx` / `rx_dropped` | counters incremented at the event | complies |

Applied settings, read back after applying:

- `journal_mode=WAL` - complies (read back and warned, `store.py:457-463`)
- `auto_vacuum=INCREMENTAL` - **violates, CONFIRMED (F1)**
- `synchronous=NORMAL`, `foreign_keys=ON` - complies (probed as applied; neither reports refusal in a result set)
- `set_retention_days` / `set_max_db_bytes` / `set_min_sessions` - complies; `/status` reports `db_max_bytes` from the store, not from the request body

---

## Delegated sweeps

Classes 6, 7, 8, 9, 10, 15, 16, 18, 19 and 20 were run by parallel sub-sweeps under the same discipline; their full per-site verdict lists are the files named below, and their findings are folded into the Findings section above.
Cheap claims were re-checked here rather than taken on report: the class-7 grep, the class-19 `parseEnumLabels` / `monitor_mark` / `config.py` sites, and the class-9 `--token 'tökén'` probe were each reproduced independently before being written up.

- class 20: `/tmp/claude-1000/review-r2/class20.md` - 47 statements, 1 violates, 36 complies, 10 exempt
- classes 9, 10, 18: `/tmp/claude-1000/review-r2/classes-9-10-18.md` - 5 / 1 / 2 violates
- classes 6, 19: `/tmp/claude-1000/review-r2/classes-6-19.md` - 0 / 10 violates
- classes 7, 8, 15, 16: `/tmp/claude-1000/review-r2/classes-7-8-15-16.md` - 0 / 0 / 0 / 0 violates, 1 unasserted matrix cell

### Class 6 (no violations, recorded so the count is on the record)

19 producers writing into plot or digital data arrays. All four value gates (parse, typed decode, post-scale, history seed) and both x gates are present; the remaining sites are removal-only splices, `null` gap fills or slice copies.
Probe: `!ps 0 3E8 7F800000` followed by a finite sample leaves `ys = [1]`.
Note that F12 and F13 are the *daemon* side of the same value never gating on finiteness; the browser is the side that is guarded.

### Class 7: the code complies, one matrix cell has no asserted outcome

`cli.py:1495-1497`, the {stale record} x {stop} cell, is the only cell where `mcu daemon stop` deletes a record.
Its three siblings in the same block are each pinned by a test asserting on unique text; dropping the `pid_running(pid)` guard would leave every existing test green.
Verified here: `grep -rn "removed stale pid file" host/tests/` returns nothing, and the string exists only at `cli.py:1498`.
The code is correct (the record is provably stale); what fails is the sweep's own exit condition, "every cell has an asserted outcome".

Class 7's stated residual is intact and still accurate: the comment at `pidfile.py:201-205` sits between the re-read (`:206`) and the remove (`:212`), neither of which is atomic on Windows.
`tests/test_pidfile.py:327` covers the narrowing only, and says so in its own docstring.

### Class 15 and 16: previously flagged gaps closed

- Class 15: all three console scripts now run from the **built wheel** (ci.yml:227, ci.yml:268 on Windows, release.yml:164), `mcu-sim` via `--help`. The only uncovered deliverable aspect is the browser render of the wheel-shipped UI, which is the already-recorded manual leg.
- Class 16: the 2026-08-11 staged-twin instance is closed - `api.js` `feedStaged` carries the same per-row guard as the live `onmessage` loop, with a comment naming the relationship. The one remaining unguarded UI loop, `plots.js:355 plotSeed`, was probed with 8 malformed daemon-shaped payloads; no per-item failure is reachable past `mergeSeedSeries`'s filters, so it complies (recorded as an asymmetry, not a defect).

## Contradictions found

None between these instructions and the docs.

Three documentation/code disagreements, each recorded as a finding rather than worked around:

- **F2**: `_fail_write`'s docstring (`store.py:570-573`) and `/status`'s `write_errors` comment (`server.py:829-831`) both state an invariant the `submit_line` fast-fail path breaks.
- **F10**: `cli.py:1737-1740` argues `TypeError` must stay unmapped, while class 9's own registry bullet says a guard that checks the container vouches for the contents. Both readings are defensible on their own territory; the resolution is a per-field guard at the point of use, not a dispatcher arm.
- **F14/F15/F16**: three `firmware/monitor/monitor.c` functions emit lines the host's own `protocol.py` refuses, in two cases directly contradicting the emitting function's own header comment.

One registry verdict is corrected: class 17's `ports[].baud` was ruled *exempt* here, with the reasoning written out, because the `Link` abstraction has no baud to read back. It is the request-echoed-as-result shape and is named so a later round does not have to re-derive why it was let through.
