# SPEC-vs-reality drift, review round 2

HEAD checked: `fd76735 POST /ports held to the config-write bar` (matches the expected sha).
Leg: SPEC-vs-reality drift in both directions, whole tree, read-only.
Probes run from `/home/daniel/git/mcuscope/host` with `uv run python`; probe scripts in `/tmp/p*.py`.

Effort was weighted to the older sections as briefed: wire protocol 2, API 3.1-3.6, CLI 4, firmware 5, DB schema 3.5, web UI 9. Section 3.7 (PlotJuggler) got a spot check only.

Headline: the SPEC and the implementation are in very close agreement. Every numeric bound, default, status code and error-class claim I drove came back conformant. The six entries below are the whole list, and none of them is a behaviour defect.

## Drift list

### 1. Wrong-typed boolean config keys warn and default instead of failing the load

- SPEC `docs/SPEC.md:470-471`: "TOML types are not coerced. A value of the wrong type in `[server]`, `[storage]`, `[update]` or `[plotjuggler]` fails the load and the daemon refuses to start, naming the file and the key."
- Code: `host/mcuscope/config.py:148-155` (`_as_bool`) never raises. It logs a warning and returns the default. `_as_int` (`config.py:172-186`) and `_as_str` (`config.py:196-212`) do raise, so the rule holds for int and string keys and fails only for bool keys.
- Affected keys: `storage.auto_session`, `update.check`, `plotjuggler.enabled`.
- **CONFIRMED**, probe `/tmp/p4.py`:

```
storage.auto_session = "yes"  -> WARNING ... using True   ; LOADED
update.check = 1              -> WARNING ... using True   ; LOADED
plotjuggler.enabled = "on"    -> WARNING ... using False  ; LOADED
server.host = 5               -> REFUSED: [server] host must be text, not 5
server.port = "abc"           -> REFUSED: [server] port must be a whole number, not 'abc'
```

- Severity: **MED**. It is a stated contract that a hand-edited `check = 1` refuses the start; instead the daemon starts with the check *on*, which is the exact opposite of what a user writing `check = 1` in a private-bench config file expects to happen if they meant `0`. (`update.check = 0` would also load as `True`.) That is the one switch SPEC 3.6:850-851 says must resolve a typo *against* phoning home.
- Which side is intended: ambiguous, and worth an explicit ruling. The code's behaviour is deliberate and argued in `_as_bool`'s docstring (no coercion), but it stops one step short of what 3.3 promises. Two consistent resolutions: (a) make `_as_bool` raise like its two siblings, or (b) narrow the SPEC sentence to int/string keys and add a line saying a wrong-typed bool warns and defaults. Given SPEC 3.6's own "resolving a typo to 'make the request' is the wrong way to be wrong", **(a) is the reading that keeps the two sections consistent**, so I believe SPEC wins here.

### 2. Simulator does not sanitize its outgoing lines

- SPEC `docs/SPEC.md:64`: "A firmware also sanitizes each outgoing line: any byte outside printable ASCII is replaced before the line is pushed, so application text reaching an event or response payload cannot embed an LF and forge a second protocol line."
- SPEC 7:1189 makes the simulator the second reference implementation ("doubles as executable documentation of the protocol").
- Code: `firmware/monitor/monitor.c:213` does sanitize (`if (c < 0x20 || c > 0x7E)`). `host/mcuscope/sim.py` has no equivalent on the emit path.
- **CONFIRMED**:

```
$ feed b'>1 mark hi\x01\x7fthere\n'
  reply: b'<1 OK\n'
  event: b'!m @0 hi\x01\x7fthere\n'
```

- Severity: **LOW**. LF cannot be forged this way (an LF in the input terminates the command line before it reaches the handler), so the stated attack is not reachable; what leaks is non-printable bytes into the capture's `lines.raw`.
- Which side is intended: SPEC wins, but the fix is cheap and belongs in the simulator, not the SPEC.

### 3. Simulator accepts an input byte above 0x7F; the firmware rejects it, and SPEC 2 states no receiver rule

- SPEC `docs/SPEC.md:41`: "Encoding: 7-bit printable ASCII." SPEC 5.4:1123 states the rule only for the firmware: "a byte above 0x7F ... fails the whole line with `ERR 2 badarg`".
- Code: `firmware/monitor/monitor.c:751` rejects (`g_line[i] > 0x7F`). The simulator has no such check.
- **CONFIRMED**, probe `/tmp/p6.py`: `src.feed(b">5 ping \xff\n")` -> `b'<5 OK monitor 1 sim\n'`.
- Severity: **LOW**.
- Which side is intended: this is a **reverse-direction gap**, not a code defect. SPEC 2.1 states the encoding as a fact about the wire but no receiver rule, and 2.4:126-130 already documents one deliberate strict/tolerant divergence between the two references. This one is undocumented. Either add a sentence to 2.1 saying a receiver *may* reject a non-ASCII byte and that the two references differ, or make the sim reject it. SPEC section 2 is silent, so the code is not currently wrong.

### 4. Web UI JS test file count is stale

- SPEC `docs/SPEC.md:1216`: "runs `node --test` over the 27 `*.test.mjs` files in `host/tests/webui_js/`".
- **CONFIRMED**: `ls host/tests/webui_js/*.test.mjs | wc -l` -> **28**.
- Severity: **LOW**. Which side is intended: SPEC is evidently stale; the code is right.

### 5. `ARCHITECTURE.md` overstates the whole-stack tier by roughly 2x

- `docs/ARCHITECTURE.md:111`: "**Whole-stack tests** (`tests/support.py:Stack`, the `stack` fixture, roughly 280 tests) attach `sim://board`".
- **CONFIRMED** by collecting with a plugin that inspects each item's `fixturenames` (`/tmp/cnt.py`):

```
TOTAL 940 STACK 148
```

  148 collected items request `stack` or `make_stack`, across 9 files (`test_assert, test_cli, test_e2e, test_hardening, test_plot, test_plotjuggler, test_regressions, test_security, test_webui`).
- Severity: **LOW**. The tier statement itself is correct; only the count is wrong.
- Which side is intended: the doc is stale. Either correct it to ~150 or drop the number, since it will rot again.

### 6. `README.md` says the test suite drives the standalone TCP simulator; it does not

- `README.md:278`: "The simulator also runs standalone as `mcu-sim` (prints `socket://127.0.0.1:9900`, attachable like any device), **which is how the test suite exercises the stack**."
- This contradicts SPEC 8:1200 ("its port attached to the simulator over `sim://`"), SPEC 7:1156 ("what the host test suite drives"), `docs/ARCHITECTURE.md:111-112` ("No listener, no ephemeral serial port, no accept loop") and `CLAUDE.md`.
- **CONFIRMED** by `host/tests/support.py:198` (`device="sim://board"`) and `support.py:6-8`. Only `test_sim_tcp.py` / `test_sim_pty.py` use the listener.
- Severity: **LOW** (docs only), but it is a direct contradiction of the contract, so worth correcting: the clause should read "which is how a daemon in another process attaches it", and `test_sim_tcp.py` keeps the listener under test.
- Which side is intended: SPEC wins; README is stale.

## Smaller notes (below the drift bar, listed so they are not re-found)

- `README.md:288-315` config sample omits the `[plotjuggler]` table that SPEC 3.3:452-454 shows and that README:73 tells the reader about. Gap, not a contradiction.
- `README.md:355-366` repo layout enumerates `docs/` file by file and omits `ARCHITECTURE.md`, `REVIEW.md`, `REVIEW_LOG.md`, `SCREENSHOTS.md`. `ARCHITECTURE.md` is the one `CLAUDE.md` tells a contributor to read first.
- SPEC 8:1196 "Several hundred tests in `host/tests/`" against 940 collected. Understates by about 2x; "roughly 4 minutes" I did not time.
- `CLAUDE.md` says "a uv-managed 3.12 virtualenv lives at `host/.venv`"; the venv on this machine reports `python 3.13.5`. Local-environment drift, not repo drift, and CI covers 3.11-3.13.
- SPEC 4:920 documents `mcu plot export --names a,b --last-ms N [--wide] -o file.csv` while the command also takes `--session` (which SPEC 3.4:716 requires of the endpoint). The SPEC line reads as an example, not an enumeration, so this is not drift.
- `host/mcuscope/server.py:545` `_LOOPBACK_CLIENTS` is three literals rather than 127.0.0.0/8. I probed a request to `127.0.0.2` against a `0.0.0.0` bind with a token set; it was allowed, because Linux picks 127.0.0.1 as the source address on `lo`. Not reachable as written; noted only so the next round does not re-derive it.

## Verified conformant (so this ground is not re-walked)

Everything below was driven and matched. Probe scripts: `/tmp/p2.py` (protocol), `/tmp/p3.py` `/tmp/p7.py` `/tmp/p8.py` `/tmp/p11.py` `/tmp/p12.py` (API), `/tmp/p5.py` `/tmp/p6.py` (simulator), `/tmp/p9.py` (CLI).

**Section 2 (wire protocol).** `MAX_LINE_BYTES` 255; `SEQ_MIN/MAX` 1..65535; `MAX_DECIMAL_DIGITS` 20 and non-ASCII digits (`١٢`) refused; `0x` prefix accepted on ids and not on payloads; error code/name table exact both sides; ad-hoc `!p` value grammar including `1e999` -> malformed and duplicate-name-in-line -> malformed; `!pd` grammar in full (enum on integer types only, enum + `*scale` invalid, bits on unsigned only, lane count bounded by type width, at least one lane, sid single digit, 16-char name cap, duplicate channel and channel/lane collision both invalid); `!m` classification, `@tick` sigil, tick > 2^32-1 -> generic event, text-only and tick-only forms; 12-token cap and 13-token `ERR 2 badarg`; over-length line -> `ERR 8 overflow` then clean recovery; unparseable seq -> silence; valid seq with no command -> `ERR 1 badcmd`; CRLF tolerated; non-`>` lines ignored; `PLOT_DEF_LOOKBACK` 20000 on both the daemon (`serial_link.py:71`) and the web UI (`webui/api.js:218`).

**Section 3.1-3.4 (daemon and API).** Route set is exactly SPEC 3.4 plus `/ws` and the `/` -> `/ui/` redirect, with nothing extra. `MAX_MATCH_LEN` 200 (201 -> 400), `MATCH_TIMEOUT_S` 0.25, `MATCH_BUDGET_S` 30. `MAX_TIMEOUT_MS` 300000 on `/cmd`, `/wait`, `/assert.timeout_ms` and `/assert.min_window_ms`, all 422 at 300001. `/lines` limit clamped to 0..1000 (`-5` and `0` both return 0 rows with `truncated: true` on a non-empty window; `5000` accepted). `/assert`: 16-pattern total is a 400 with the total's message, 17 in one list is a 422, no patterns is a 400, and all four mode-exclusivity refusals (`send` without a live window, `session` with one, `min_window_ms > timeout_ms`, unknown session) carry distinct messages. `/marker` text 1..4096 as 422s and the port-grammar 400. `/purge` "exactly one selector" for both zero and two. `/wait` `since=then` -> 400. `POST /ports` requires device or serial_number (400), baud ceiling 100000000 (422), alias grammar (422). `PUT /config/storage` refuses a non-zero cap under 1048576 (400) and above 4398046511104 (422); `PUT /config/ports` caps at 64 (422); `ConfigServerBody` host 1..255, `db_path` 1024, `retention_days` 1..3650, `min_sessions` 0..1000, PJ dest 1..255. Host allowlist and Origin guard both 403 with the identical `cross-origin request refused`. 404 and 500 both carry the `{"error"}` envelope. `POST /shutdown` answers 400 with no callback and is loopback-gated at `server.py:860`. Session list `limit` default 50 clamped to 0..1000; `DELETE /sessions/{id}` refuses a name (422); `POST /sessions/stop` reports "no session is running" against an automatic session; export of an unknown ref is a 400. `/status` and `/config` key sets are exactly the SPEC JSON blocks, port objects included. `dropped` present on both `/wait` and `/assert`, live and retrospective. `id_to` confirmed inclusive; unknown `session=` matches nothing; `last_ms` re-anchors to the effective upper bound (18 rows unbounded vs 13 with `id_to`) and an ended session plus `last_ms` returns that session's tail rather than empty. Device allowlist: `spy://...?file=` and any `?` query refused, `loop://` refused by scheme, bare paths / `socket://` / `rfc2217://` accepted; `POST /send` caps a line at 255 bytes. The config-write bar (`_config_write_denied`) is applied at exactly seven sites: `POST /ports`, `PUT /plotjuggler`, and the five `PUT /config/*`. Token guard constants 10 / 60 s / 60 s. `WS_KEEPALIVE_S` 20.0, `WS_BATCH_MAX` 500, and the first `/ws` frame carries `{"capture": "..."}`.

**Section 3.2 / 3.5 (capture, storage, schema).** `BACKOFF_MIN/MAX` 0.5/5.0, `PRESENCE_POLL_S` 0.25, `PRESENCE_SETTLE_S` 0.15. Retention defaults 10 days / `min_sessions` 5, size-cap trim target `int(cap * 0.9)`, size cap ticked once a minute with an hourly age sweep (`_RETENTION_TICKS = 60`). Pid file `mcuscoped-<host>-<port>.pid`. Live schema on disk: tables `lines, sessions, can_frames, meta, plot_points`; indexes `idx_lines_ts, idx_lines_chan_id, idx_lines_port_id, idx_sessions_name, idx_can_id_line, idx_plot_name_line, idx_plot_line`; column lists exact; `sessions` carries `AUTOINCREMENT`. Pragma order in `store.py:453-469` is `auto_vacuum=INCREMENTAL, journal_mode=WAL, synchronous=NORMAL, foreign_keys=ON`, and the created file reads `journal_mode=wal, auto_vacuum=2`. A firmware `!m` lands as `chan marker, dir rx` carrying its port; session start/stop write marker rows.

**Section 3.6 (release check).** `PYPI_URL`, 24 h interval, 1 h retry hold-off, 5 s HTTP timeout, `user_cache_dir/update.json`, no timer (`maybe_check()` at startup and per `/status`), `MCUSCOPE_UPDATE_CHECK` two-way override.

**Section 4 (CLI).** The command tree matches the SPEC 4 table exactly, top level and every subgroup, with nothing extra and nothing missing. Global options are exactly `--json --port/-p --url --token --version`. `--version` prints version plus interpreter and honours `--json`. Exit codes driven live: unreachable daemon -> 3 on `status` and `cmd`, `daemon status` -> 3 with "not running", bad usage -> 1, `wait` timeout -> 2, `assert` fail -> 1, `cmd` on an `ERR` -> 1. `--json` emits exactly one object everywhere except `tail`, `can dump` and `log export`, which emit JSONL, and notes go to stderr in every case; `plot export --json` wraps the CSV in one object. `MCUSCOPE_START_TIMEOUT` default 20.0 floored at 0.5; `MCUSCOPED_CONFIG`, `MCUSCOPED_TOKEN`, `MCUSCOPE_URL`, `MCUSCOPE_TOKEN` all present. `mcuscoped` flags are exactly `--version -c/--config --host --port --token --sim --plotjuggler/--pj --open --ignore-capture-lock`.

**Section 5 (firmware).** `monitor.h` matches the SPEC 5.2 block line for line: `MONITOR_LINE_MAX 255`, `MONITOR_PROTO_VERSION 1`, `MON_OK_PAYLOAD_MAX (MONITOR_LINE_MAX - 10)`, the `MON_WEAK` cascade, the nine `MONITOR_ERR_*` codes, every public function and every 5.3 shim signature. Internals: `MON_MAX_DATA 128`, `MON_REG_SLOTS 8`, `MON_PLOT_MAX_STREAMS 4`, `MON_PLOT_MAX_FIELDS 16`, `MON_PLOT_PD_PERIOD_MS 5000`, 64-byte `g_stage`, 12-token cap. `i2c scan` sweeps `0x08..0x77` (`monitor_cmds.c:201`), addresses bounded at `0x7F`, `i2c rd/wrrd` n bounded 1..64, `can tx` id range-checked per flag width, `can filter ... r` rejected with `MONITOR_ERR_BADARG` and the reason stated in the source. `.bss` measured with `gcc -O2 -std=c99` on x86-64: 1096 (`monitor.c`) + 172 (`monitor_cmds.c`) = **1268 bytes**, exactly the SPEC 5.1 figure. `firmware/tests/test_monitor.c` holds **31** cases, matching SPEC 8.

**Section 7 (simulator).** TCP default port 9900; `i2c scan` finds exactly `48 50`; SPI inverting echo on `imu`/`flash`; `gpio` `led`/`en_5v`; `adc vbat` around 3300 mV; CAN heartbeat id 0x100 at 10 Hz; echo id+1 after 20 ms; standing bus exactly `(0x200, 2 Hz, dlc 2), (0x18A ext, 1 Hz, dlc 8), (0x321, 5 Hz, dlc 1), (0x400 rtr, 0.5 Hz, dlc 8)`; `mark` answers OK and emits `!m @tick`, empty text -> `ERR 2 badarg`; `FLOOD_MAX_BURST` 5000.

**Section 9 (web UI).** `HIGH_RATE_ON` 2000 / `HIGH_RATE_OFF` 800; `BUFFER_MAX` and `VIEW_MAX` 5000; 200-row backfill; `PLOT_CAP` 100000; `MAX_CHANNELS` 64 and `MAX_LANES` 64; `SEED_CHANNELS` 32, `SEED_POINTS` 2000, `SEED_MAX_MS` 3600000; `PLOT_WINDOWS` `5s / 30s / 5m`; baud dropdown 9600 through 3000000 plus custom; module list matches SPEC 9.1 exactly. `/plot/channels` returns every field SPEC 9.2 names (`name, port, sid, type, unit, scale, kind, labels, group, bit, last_value, last_tick, last_ts, count`); `/plot/series` default limit 10000 clamped to 0..100000 with `decimate` floored at 1; `/plot/export` refuses a bad `format` and enforces `MAX_EXPORT_ROWS` 1000000 with the count in the message, and `wide` refuses channels spanning sids.

## Sections deliberately not checked

- **3.7 PlotJuggler**, beyond a spot check of `GET/PUT /plotjuggler` (bad dest -> 400, multicast dest refused by name, omitted `dest` keeps and echoes the current one, `PUT` on the config-write bar, `--plotjuggler/--pj` flag present, `mcu plotjuggler`/`pj` present). Excluded per the brief as just-reviewed. **Not driven**: the datagram wire format and its `ts`/`tick`/port-alias nesting, the `ts_`/`tick_` port-name escape, non-finite value dropping, the shared-socket torn-read discipline, and "only plot points are streamed".
- **SPEC 1** (goals and constraints) and **SPEC 10** ([P2] design intent): no testable implementation claims.
- **SPEC 6** (AI integration): `mcu ai-guide` and `docs/CLAUDE_SNIPPET.md` both exist; I did not read their contents against SPEC 6's list of what each must cover.
- **Windows-specific claims** throughout (`_stdio.py` console attach and crash file, `SO_EXCLUSIVEADDRUSE` bind probe, `msvcrt.locking`, COM enumeration presence test, launcher-shim pid). Unreachable from Linux. This overlaps the standing "Windows check pending" item.
- **SPEC 3.2 reconnect narration** (one loss row, at most three distinct reasons, one reconnect row carrying the failed-attempt count). Read in `serial_link.py` but not driven; `test_reconnect.py` covers this tier.
- **SPEC 3.5 migration** ("in place on open and idempotent", the `sessions` rebuild for `AUTOINCREMENT`, index replace-before-drop, `sqlite_sequence` seeding). I confirmed the end state on a fresh capture only, not the upgrade of a pre-0.2 file.
- **SPEC 3.2 retention semantics under load**: the `min_sessions` floor, size-cap trimming into protected sessions with a warning, and the `lines_trimmed` sys row. Constants verified, behaviour not driven.
- **SPEC 3.4 `/ws` shed path**: the `{"gap": n}` object and the 503/1013 subscriber cap. `MAX_SUBSCRIBERS` and the drop-oldest fan-out exist in `store.py`; producing a real shed needs the flood rate, which is a standing open leg from the previous round.
- **SPEC 9.1/9.2 rendering**: everything reached only through a laid-out canvas (chart drawing, uPlot glue, the digital lane envelopes, the settings dialog). Out of the DOM stub's range by design, and manual-verify per `CLAUDE.md`.
- **SPEC 2.3 firmware response-overflow paths** (`ERR 8` rather than truncation, the `i2c scan` whole-token exception, event-line truncation). Read in the source; not executed, since the C harness has no line-feed driver I could call without building a new one. `firmware/tests/` covers these under ASan/UBSan.
