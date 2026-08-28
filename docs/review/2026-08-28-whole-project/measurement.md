# Review round r2: measurement and coverage leg (Linux)

HEAD: `fd76735 POST /ports held to the config-write bar` (matches the expected sha).
Platform: Linux 6.8.0-137, python 3.13 venv at `host/.venv`.
Repo touched read-only. All daemon state was redirected into `/tmp` via `XDG_DATA_HOME`/`XDG_CONFIG_HOME`, so the user's real capture database was never opened.

## Summary

- 2 CONFIRMED findings (1 HIGH, 1 MED), 3 SUSPECTED (all LOW).
- Coverage total **87%** (5937 statements, 772 missing) against a `fail_under` of 78. Prior round recorded 84%.
- Suite: 939 passed, 1 skipped, 372 s.
- No latency regression against any prior recorded number. Two prior fixes reconfirmed still holding (`/devices`, `/status` under load).

## Findings

### F1. CONFIRMED, HIGH. A negative `--before-days` turns a retention purge into a full wipe

`host/mcuscope/cli.py:863` declares `--before-days: float` with only `finite_option` (nan/inf) as its callback, and `host/mcuscope/cli.py:887` computes `body["before_ts"] = time.time() - before_days * 86400`.
A negative value puts `before_ts` in the future, so "older than N days" selects every line in the capture, including the lines of the currently running session.
`host/mcuscope/server.py:224` does not bound it either: `before_ts: float | None = None`, where its siblings in the same model carry `Field(ge=1)` and the config route's `retention_days` carries `Field(ge=1, le=3650)`.

Probe, against a live capture of 387k lines across 5 sessions:

```
$ mcu purge --before-days -1 --dry-run
would delete 22908 lines (ids 1-23008)

$ mcu purge --before-days -1 -y
deleted 387123 lines

$ mcu session list
5  auto-2026-08-28_13-48-00  running  826 lines     (only lines captured after the delete)
4  auto-2026-08-28_13-47-55  ended    0 lines
3  auto-2026-08-28_13-43-38  ended    0 lines
2  review-r2                 ended    0 lines
1  auto-2026-08-28_13-41-26  ended    0 lines
```

The same hole is reachable directly over REST, which confirms the server model is the second site:

```
POST /purge {"before_ts": 1e18, "dry_run": true}  ->  200 {"deleted":7137,"id_from":1,"id_to":394360}
```

Why HIGH: this is the one destructive selector whose whole purpose is a bounded age, and the out-of-range value silently means `--all`.
The confirmation prompt does show the count, but `-y` is the scripted path, and a computed `--before-days` (a variable that goes negative) is the realistic way in.
`docs/REVIEW.md` class 22 already states the rule this breaks: "an out-of-range number has a sane default. Falling back beats clamping wherever the value governs deletion."
So this is a repeat-class instance sitting in a destructive selector.

Note the neighbouring behaviours are correct and should not be changed: an inverted id range (`--id-from 500 --id-to 100`) yields 0 lines, and `--session` with an unknown name is a clean 400.

### F2. CONFIRMED, MED. The truncation note reports the requested limit and prescribes a remedy that cannot work

`host/mcuscope/store.py:1372` clamps every `/lines` query to 1000 rows (`limit = max(0, min(int(limit), 1000))`).
`host/mcuscope/server.py:1343` accepts `limit: int = 100` with no upper bound declared, so the request is accepted as written and quietly reduced.
`host/mcuscope/cli_output.py:202` then prints the *requested* limit and tells the user to raise it:

```
$ mcu log export --limit 20000 -o /tmp/m-l2.txt
wrote 1000 lines to /tmp/m-l2.txt                                          (stdout)
note: results truncated at limit 20000; older matches exist (raise --limit or use --since-id)   (stderr)
```

The capture held 28k matching lines at the time.
The row count printed is honest, but the note misattributes the cap to the user's own request, and the first remedy it offers ("raise --limit") is inert at any value above 1000.
This is `docs/REVIEW.md` class 17 (reported value is the request, not the result), landing on the export path where the silent loss is 95% of the data.
The `--since-id` half of the advice does work.

Suggested shape of a fix, for the fix leg to judge: report `len(body["lines"])` in the note, and either declare the ceiling on the server model (`Field(le=1000)`) so an over-limit request is refused, or state the ceiling in the note text.

### F3. SUSPECTED, LOW. `None` leaks into user-facing purge output

`host/mcuscope/cli.py:901` formats `f"(ids {preview['id_from']}-{preview['id_to']})"`, and the server answers `id_from: null, id_to: null` when nothing matches (`server.py:1290`).

```
$ mcu purge --before-days 999 --dry-run
would delete 0 lines (ids None-None)
```

The same expression is on the confirmation prompt at `cli.py:912`, though that path is unreachable for a zero count (the `deleted == 0` early exit at `cli.py:902` fires first).

### F4. SUSPECTED, LOW. A mistyped plot channel exports an empty CSV with exit 0

`host/mcuscope/server.py:1461` refuses only an empty `names` list; it never checks that a name exists.

```
$ mcu plot export --names nosuchchan -o /tmp/m-none.csv
wrote 0 rows to /tmp/m-none.csv        rc=0, file is the 26-byte header alone
```

A typo is indistinguishable from a channel that genuinely has no points.
`mcu plot channels` is right there to resolve the name, and the CLI already treats "empty or truncated CSV where the user asked for an export" as a hazard worth code (`cli.py:1325`).
Filed SUSPECTED because refusing an unknown name may be a deliberate choice for the multi-name case, where one dead name among several should not fail the export.

### F5. SUSPECTED, LOW. CLAUDE.md states the wrong interpreter version

`CLAUDE.md` says "a uv-managed 3.12 virtualenv lives at `host/.venv`"; the venv is python 3.13 (`host/.venv/lib/python3.13/`).
Documentation drift only, but the setup command it sits beside (`uv venv --python 3.12`) now reproduces a different environment than the one the suite is green on.

## Numbers

All measurements on one machine on one day, medians of 15 requests (8 for the `limit=1000` rows), taken through the real HTTP surface of a live `mcuscoped`.
Per the measurement leg's rule, none of these is proposed as a test threshold.

### Endpoint latency

| Endpoint | Idle, 28k lines | Idle, 385k lines | Under live ingest (4.1k lines/s, 260k lines) | Prior recorded | Verdict |
|---|---|---|---|---|---|
| `/status` | 1.70 ms | 1.73 ms | 2.35 ms (max 11.1) | 1.6 to 2.5 ms under 3 concurrent heavy queries | No regression |
| `/ports` | 1.60 ms | 1.53 ms | 1.48 ms | none | baseline |
| `/devices` | 1.60 ms | 1.85 ms | 1.58 ms | 0.70 s freeze, since fixed | Fix holding |
| `/lines?limit=100` | 3.38 ms | 2.95 ms | 3.29 ms | none | baseline |
| `/lines?limit=1000` | 14.56 ms | 9.74 ms | 15.68 ms | none | baseline |
| `/lines?match=sine&limit=100` | 9.38 ms | 34.61 ms | 148.93 ms | none | baseline, see note |
| `/lines?last_ms=5000&limit=1000` | 6.20 ms | 8.73 ms | 15.56 ms | none | baseline |
| `/plot/channels` | 32.69 ms | 39.78 ms | 53.42 ms | 84 ms with `?port=`, known class 20 disposition | No regression |
| `/can/frames?limit=100` | 11.92 ms | 5.51 ms | 10.66 ms | 0.4 ms at 1M after the CROSS JOIN fix (different shape) | Not comparable, see not-run |
| `/sessions` | 12.55 ms | 66.12 ms | 81.06 ms | 88 ms at 1M lines after the COALESCE fix | No regression, see note |

Notes on the two that stand out:

- `/sessions` at 66 ms over 385k lines and 5 sessions is the documented cost, not a regression.
  `store.py:1011` already states it: "still O(lines in the span): the count steps every id in each session's range".
  I re-explained the shipped statement against the real capture with no `sqlite_stat1` present, which is the condition class 20 requires, and the plan is sargable at both ends:
  `SCAN s` / `CORRELATED SCALAR SUBQUERY 1` / `SEARCH l USING INTEGER PRIMARY KEY (rowid>? AND rowid<?)`.
  The `SCAN s` is the small sessions table and is exempt. Nothing to fix; the standing concern (a web UI timer polling an O(capture) query) is unchanged from prior rounds.
- The `match=` regex scan rises to 149 ms under live ingest but does not block the loop: `/status` stayed at 2.35 ms in the same window, so `query_lines_safe`'s offload is doing its job.

### Lifecycle and throughput

| Measurement | Value |
|---|---|
| Time to first captured line after `mcuscoped --sim` start | 1.23 s |
| Sustained sim ingest, `mcu-sim --flood 4000 --plot` over `socket://` | 4.10k lines/s for 45 s, `write_errors` 0, `lines_trimmed` 0, `ws_dropped` 0, `writer_alive` true throughout |
| Capture DB after 385k lines | 47.6 MB main file, `db_content_bytes` 47,964,160 |

### Export duration and size

Wall clock includes `uv run` CLI process start, so treat these as upper bounds on the server work.

| Export | Rows | Bytes | Wall clock |
|---|---|---|---|
| `log export --limit 20000` (clamped to 1000, see F2) | 1000 | 45,445 | 866 ms |
| `plot export --names sine` (long) | 5832 | 225,114 | 3418 ms |
| `plot export --names sine,noisy --wide` | 5899 | 235,941 | 2763 ms |
| `session export 1` | n/a | 2,748,416 | 1006 ms |

## Part A: what was driven

### Zero-hardware demo, end to end

All through the installed console scripts against `mcuscoped --sim` on 127.0.0.1:8765.

- `mcu status`, `mcu ports`: correct, `writer_alive: true`.
- `mcu cmd 'i2c scan'` -> `48 50`. `mcu i2c rd 0x48 2` -> `0641`. `mcu i2c wr 0x50 00dead` then `mcu i2c rd 0x50 2 --reg 00` -> `DEAD` (write and read back agree).
- `mcu gpio set led 1` then `mcu gpio get led` -> `1`. `mcu adc read vbat` -> `raw=4093 mv=3299`. `mcu spi xfer imu 010203` -> `FEFDFC`.
- `mcu can stat` -> `rx=1308 tx=0 err=0 state=active`. `mcu can dump -n 4` and `--id 100 -n 3` both decode (ids 100, 200, 321 present as the sim documents).
- `mcu lines --limit 5`, `--match sine`: rows on stdout, truncation note on stderr (stdout stays a clean stream, class 10 shape holds).
- Plot: `mcu plot channels` lists 9 channels with counts; long and wide exports both produced well-formed CSV (`ts,tick_ms,sine,noisy` header on the wide one).
- `mcu mark "review-r2 marker"` -> `marker 7181`, and the marker is queryable back through `/lines`.
- `mcu session start review-r2` / `stop` -> `session 2 ended: review-r2 (lines 12912-13245)`; `session list` shows the auto session reopening after the stop.
- `mcu session export review-r2 -o ...` -> 143,360 bytes standalone db.
- `mcu purge` driven across every selector, see the refusal table below.
- `mcu pj on` plus a UDP receiver bound on 127.0.0.1:9870 caught **333 real datagrams in 4 s**, each valid JSON of the documented shape: `{"ts":1787892297.102793,"tick":211.007,"sim":{"sine":0.044,"noisy":0.0244}}`. `mcu pj off` stopped the stream.
- `mcu daemon stop` shut it down cleanly.

Error-path behaviour worth recording: a device `ERR` is written to **stderr** with exit code 1 while stdout stays empty (`mcu cmd 'bogus verb'` -> `ERR 1 badcmd unknown bogus`, rc 1). That is the exit-code contract holding.

### Purge selectors and refusals

| Probe | Result | Verdict |
|---|---|---|
| `purge` (no selector) | rc 1, "exactly one of --session, --before-days, --id-from/--id-to, --all is required" | Correct |
| `purge --session review-r2 --all --dry-run` (two selectors) | rc 1, same refusal | Correct |
| `purge --session review-r2 --dry-run` | would delete 334 lines (ids 12912-13245) | Correct |
| `purge --session nosuchsession --dry-run` | rc 1, "no such session: nosuchsession" | Correct |
| `purge --id-from 1 --id-to 100 -y` | deleted 100 lines | Correct |
| `purge --id-from 500 --id-to 100 --dry-run` (inverted) | 0 lines, accepted | Correct by design (`server.py:1298` `hi < lo`) |
| `purge --before-days 999 --dry-run` | 0 lines, "(ids None-None)" | F3 |
| `purge --before-days -1 -y` | **deleted 387123 lines, the whole capture** | **F1** |
| `purge --all --dry-run` | would delete 23162 lines | Correct |

### Daemon lifecycle

| Probe | Result | Verdict |
|---|---|---|
| Start `mcuscoped --sim --port 8765` | up, first line at 1.23 s | Correct |
| Second daemon, same db and port | rc 1, names the holding pid, the hostname, the lock time, and `--ignore-capture-lock` | Correct |
| Second daemon, different db, same port | rc 1, "127.0.0.1:8765 is already in use (Address already in use)" with the `--port` remedy | Correct |
| Pid record after both failed starts | still `2709457`, the live daemon | **Class 14 fix holding** (the POSIX-side clobber is gone) |
| `mcu daemon stop` | "stopped mcuscoped (pid ...)", pid record removed from disk | Correct |
| `kill -9` then `mcu status` | rc **3**, "daemon unreachable at http://127.0.0.1:8765: [Errno 111] Connection refused" | Correct, matches the exit-code contract |
| `kill -9` then `mcu daemon status` | "not running" (no false positive from the stale record) | Correct |
| `kill -9` then `mcu daemon start --sim` | starts, rewrites the pid record | Correct, stale-pid path clean |

The capture lock behaves as its own error message claims: after `kill -9` the OS released it and the restart was not blocked.

## Part B: coverage

Command: `uv run python -m pytest --cov=mcuscope --cov-report=term-missing --cov-fail-under=0` from `host/`.
Result: **939 passed, 1 skipped, 372 s. TOTAL 5937 statements, 772 missing, 87%.**

The single skip is `tests/test_reconnect.py:83` "Windows COM enumeration", the known platform-inert test; it is live only on the Windows leg.

| Module | Stmts | Miss | Cover | Prior round |
|---|---|---|---|---|
| `__init__.py` | 2 | 0 | 100% | |
| `_stdio.py` | 173 | 52 | 70% | |
| `cli.py` | 846 | 346 | 59% | 49% |
| `cli_argv.py` | 75 | 3 | 96% | |
| `cli_client.py` | 105 | 17 | 84% | |
| `cli_daemonctl.py` | 113 | 54 | 52% | |
| `cli_output.py` | 110 | 39 | 65% | |
| `config.py` | 214 | 2 | 99% | |
| `daemon.py` | 150 | 24 | 84% | 82% |
| `link.py` | 124 | 6 | 95% | |
| `lockfile.py` | 82 | 11 | 87% | |
| `pidfile.py` | 107 | 20 | 81% | |
| `pjstream.py` | 86 | 1 | 99% | |
| `protocol.py` | 487 | 7 | 99% | |
| `serial_link.py` | 581 | 19 | 97% | |
| `server.py` | 1088 | 67 | 94% | |
| `sim.py` | 598 | 55 | 91% | |
| `store.py` | 861 | 46 | 95% | |
| `update_check.py` | 135 | 3 | 98% | |
| **TOTAL** | **5937** | **772** | **87%** | **84%** |

The 87% still understates `cli.py`, which the e2e and CLI suites drive as a real subprocess that coverage does not measure. The `fail_under = 78` comment in `pyproject.toml` documents this and remains accurate in substance, though its stated figures (84% total, cli.py 49%) are now out of date by 3 and 10 points respectively.

### Disposition of uncovered shipped branches

Verdicts below cover every uncovered range in `server.py` and `store.py` (the request and destructive surface), and the notable ones elsewhere.
Branches marked **driven** were executed live against the running daemon during this leg.

#### Untested request parameters (the category the leg exists for)

| Branch | What it is | Verdict |
|---|---|---|
| `server.py:1777`, `server.py:1982` | `send_mode: "raw"` on `/wait` and `/assert`. **Zero coverage on both**, despite the model's own docstring naming `send_mode` as a field that was previously only ever tested one way. | **Correct-but-untested. Driven:** `POST /wait {"send":">7 i2c scan","send_mode":"raw"}` -> `{"status":"match", ... "raw":"<7 OK 48 50"}`, and the `/assert` twin -> `{"status":"pass"}`. Both correct; a test is owed. |
| `server.py:1462` | `/plot/export` with an all-empty `names` | **Correct-but-untested. Driven:** 400 "names is required". |
| `server.py:1464` | `/plot/export?format=` outside long/wide | **Correct-but-untested. Driven:** 400 "format must be 'long' or 'wide'". |
| `server.py:1389-1390` | `/can/frames?id=` not a CAN id | **Correct-but-untested. Driven:** 400 "bad can id: zz". |
| `server.py:1760` | `/wait` match regex over `MAX_MATCH_LEN` | **Correct-but-untested. Driven:** 400 "match regex too long (max 200 chars)". |
| `server.py:1763-1764` | `/wait` uncompilable regex | **Correct-but-untested. Driven:** 400 "bad match regex: unterminated character set at position 9". |
| `server.py:1904` | `/assert` with neither expect nor forbid | **Correct-but-untested. Driven:** 400 "at least one expect or forbid pattern is required". |
| `server.py:1325-1326`, `916-917`, `1754-1755`, `1969-1970` | `PortError` on `/cmd`, `/ports/{p}/reconnect`, `/wait`, `/assert` | **Correct-but-untested. Driven:** all 400 "no such port: nosuch". |

#### Refusal and error branches

| Branch | What it is | Verdict |
|---|---|---|
| `store.py:374-375`, `store.py:1553-1559`, `server.py:1504-1505`, `server.py:1624` | The regex match budget: `TimeoutError` from the SQLite REGEXP callback, translated to `MatchBudgetExceeded` and a 400. **The suite never trips this.** | **Correct-but-untested. Driven:** planted a 400-character `aaaa...` marker line, then `GET /lines?match=(a|a)%2Bb` -> 400 "match pattern exceeded the matching time budget; simplify the regex" in 0.25 s. Mechanism confirmed working; a test is owed on a path whose whole job is to stop a stall. |
| `server.py:861-866` | `/shutdown` refused 403 for a non-loopback client | **Correct-but-untested.** A security refusal with no test, unreachable from a loopback probe without spoofing `request.client`. Recommend a test that injects a non-loopback client host. Flagged as the most valuable untested refusal after the match budget. |
| `server.py:587`, `server.py:607` | Token rate limiter: the failure-window reset and the prune-expiry delete | **Correct-but-untested.** Both are time-expiry branches on a security path (`_TokenGuard`), so nothing in the suite has ever let a window lapse. The eviction half of `_prune` (past `TOKEN_FAIL_TABLE_MAX`) is covered; the expiry half is not. |
| `server.py:685-686` | WS auth denial, close code 1013 | **Correct-but-untested.** |
| `server.py:1540-1542` | `/ws` close 1013 on `StoreError`, subscriber cap reached | **Correct-but-untested.** Related to the WS-shed leg listed as not-run. |
| `server.py:1032-1033`, `1060-1061`, `1082-1083`, `1143-1144` | `except (ConfigError, OSError) -> _save_error` on all four `PUT /config/*` routes | **Correct-but-untested.** Four identical untested config-write failure branches. Worth one shared test with an unwritable config path, particularly as HEAD (`fd76735`) is the commit that held `POST /ports` to this same bar. |
| `server.py:1250-1254` | `export_session` generic `except`, unlinks the temp file and logs | **Correct-but-untested**, and the partial-file cleanup is the interesting half: a prior round fixed exactly this shape for `plot export -o`. |
| `server.py:447-448`, `459-460`, `619-620`, `623`, `628-629` | `UnicodeDecodeError` on Origin/Host/Authorization header decode | **Correct-but-untested.** Defensive decodes at a trust boundary; reachable only with non-UTF-8 header bytes. Low value to test, non-zero value to keep. |
| `server.py:406` | `_http_error` JSON envelope for a raised `HTTPException` | **Correct-but-untested.** |
| `server.py:1597` | `watch` early return | **Correct-but-untested.** |
| `store.py:505-506` | Startup retention sweep failure is logged, not fatal | **Correct-but-untested.** |
| `store.py:523-525`, `528-529` | Writer queue full at shutdown, and writer not exiting within 5 s, both cancel the task | **Correct-but-untested.** These are the "shutdown does not hang forever" guards a prior round added; reaching them needs a wedged writer, which is what the existing test hook is for. Candidate for the reach-for-the-unit approach the leg's charter describes. |
| `store.py:546`, `559-560` | `_fail_queued` early return and drain resolution | **Correct-but-untested.** |
| `store.py:617-618`, `640-652` | Writer stop sentinel, and the row-by-row fallback itself failing | **Correct-but-untested**, second-order defensive. |
| `store.py:911-912`, `915-916` | Broadcast queue empty and full, the WS shed path | **Correct-but-untested.** Directly under the "WS shed at flood rate" item that is still not-run. |
| `store.py:1799`, `1828`, `1862`, `1983`, `2066` | Empty-input early returns in `export_sids`, `count_plot_export`, `iter_plot_export`, `delete_range`, `_sweep_size_locked` | **Correct-but-untested**, trivial guards. `delete_range` returning 0 is on the destructive path but is the no-op case. |
| `store.py:2139`, `2153-2154` | Retention loop tick body | **Correct-but-untested**, timing-driven. |
| `store.py:2200-2201` | `db_size_bytes` `except OSError` when the WAL is absent | **Correct-but-untested.** |
| `store.py:1471`, `1530` | `_offload` synchronous path and a re-raise | **Correct-but-untested.** |

#### Dead by design on this leg

| Branch | Reason |
|---|---|
| `server.py:137-138` | `ImportError` for a websocket implementation without the backpressure hook (wsproto build variant). Not this build. |
| `server.py:2148-2154` | `_by_id_map` listing `/dev/serial/by-id`. That directory does not exist on this machine (no USB serial attached), so the branch is unreachable without the bench board. Overlaps the not-run bench item. |
| `server.py:1724` | `RuntimeError("CaptureWatch.next_batch before open()")`, a programming-error assertion. |
| `store.py:298` | `RuntimeError` when the SCHEMA constant lacks an expected marker, a build-time invariant. |
| `_stdio.py` (52 missing), `cli_daemonctl.py` (much of 54), `daemon.py:124-135` | The `ctypes`/`msvcrt`/`SIGBREAK`/console-attach families. Windows only, per the leg's charter. |
| `pidfile.py:81-99` | The Windows file-sharing branch family. |

No branch in the uncovered set is **defect-suspected**. F1 and F2 were both found by execution, not by coverage: F1's code path is fully covered by the suite (the tests simply never pass a negative), and F2's clamp at `store.py:1372` is a covered line.
That is the useful negative result for this leg: line coverage at 87% did not point at either confirmed defect, which is the same lesson a prior round recorded ("better evidence than line coverage").

## Not run

Owned by other legs, other platforms, or absent hardware. None of these is counted as done.

- **Windows 10/11, entirely.** The whole measurement leg is owed on the other PC: full suite, console and socket semantics, the `SO_EXCLUSIVEADDRUSE` probe, classes 8 and 13, and the `_stdio.py`/`cli_daemonctl.py`/`pidfile.py` branch families dispositioned above as Windows-only. This is the third consecutive round carrying this.
- **Browser web UI.** No visual check, no uPlot glue, no settings dialog, no reload-repetition check. The leg's charter keeps the human visual check here and it did not happen.
- **Bench board.** No STLINK-V3 at `/dev/ttyACM0` was driven, so the real-UART path, `drain_counted`, and `/dev/serial/by-id` enumeration (`server.py:2148-2154`) are all unexercised.
- **WS shed at flood rate.** Carried open from the 2026-08-09 round and still open. I drove 4.1k lines/s of ingest with no WS client stalled, so `ws_dropped` stayed 0 and `store.py:911-916` was never entered. Driving it needs a deliberately stalled consumer.
- **`/can/frames` query plan.** I measured its latency but did not `EXPLAIN QUERY PLAN` the shipped statement, so my number is not comparable to the prior round's 0.4 ms figure and I make no regression claim either way. The `/sessions` plan was explained; this one was not.
- **Class 15 shipped-artifact sweep.** Named in the coverage-and-artifact leg's charter; not run here.
- **Class 12 probe checklist.** Named in the measurement leg's charter; not run as a checklist, though `writer_alive`, `write_errors`, `ws_dropped` and `lines_trimmed` were all observed healthy under load.

## Probe defects made and corrected

Recorded because the leg's own discipline says a 4xx to your own probe is a probe defect, not a result.

- Invented arguments for `gpio`/`adc`/`spi` (`gpio read PA5`, `adc read 0`, `spi xfer 01 02 03`) before reading the grammar. Three `ERR 2 badarg` responses that measured nothing. Corrected by reading `sim.py` (`ADC_NAMES = ("vbat",)`, `SPI_CS_NAMES = ("imu","flash")`) and the CLI signatures: DATA is one unspaced hex token.
- Used `--grep` and `can dump --limit`, neither of which exists. Corrected to `--match` and `-n`.
- Read `rc=$?` after a pipe into `head`, so several early probes reported `head`'s exit status and hid the real one. This is the exact trap that made `mcu cmd 'bogus verb'` first read as rc 0; it is rc 1 with the error on stderr.
- Judged `send_mode: "raw"` a timeout defect before checking the wire format. `raw` writes the line verbatim, so it needs the `>SEQ CMD` form from `protocol.format_command`; with `">7 i2c scan"` it works. No finding.
- Filtered `--help` output through `sed -n '/Arguments/,$p'` and read the empty result as a broken command. `mcu purge --help` prints 2117 bytes and simply has no Arguments section.
- One transient `error: Failed to spawn: mcu` while a concurrent `uv run mcu-sim` was touching the venv. A `uv` race, not a product defect; retried clean.

## Processes

Every process this leg started was killed by a PID recorded at spawn, and the teardown verified with `ps`.

| PID | What | Ended by |
|---|---|---|
| 2709454 / 2709457 | first `mcuscoped --sim --port 8765` | `mcu daemon stop` |
| 2726474 | `mcu daemon start --sim` | `kill -9` (the crash probe) |
| 2726679 | restart over the stale pid record | `mcu daemon stop` |
| 2727323 / 2727325 | `mcu-sim --tcp-port 9900 --plot --flood 4000` | `kill` by PID |

`ps` confirms none of the above survives, and port 8765 is free.
`pkill -f` and `pgrep -f` were not used at any point.
One `mcuscoped` remains alive on port **8799** under `/tmp/claude-1000/review-r2/dd/capture.db` (PIDs 2711483/2711495): it belongs to another leg sharing this scratch directory, was not started by me, and was deliberately left running.
