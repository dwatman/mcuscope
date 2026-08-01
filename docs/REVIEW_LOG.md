# Review round log

One section per leg per platform. The runbook is `docs/REVIEW.md`; this file is the evidence
it requires ("the sweep verdict lists, the measurement and ruled-out log, the coverage
disposition list, the revert-verification list, and the fix-diff report").

## 2026-08-01 - Close-out of M1 and M2, Linux

Both open class 20 findings from the Windows measurement leg, reproduced on Linux and fixed.

The variable the Windows run was missing was not the port count but **`sqlite_stat1`**. Its
synthetic capture had been `ANALYZE`d; a shipped capture has not, because the store never runs
`ANALYZE`. With stats present, every plan below is already correct - which is exactly why M2
"did not reproduce on synthetic data". Reproduced here on 1M lines across two ports
(`/dev/ttyACM0` 3:1 `/dev/ttyUSB1`), stats dropped:

| query | before | after |
|-------|--------|-------|
| `/can/frames?port=` | 131.3 ms, `SCAN l` + temp b-tree | 0.4 ms, `SCAN cf` |
| `/can/frames?port=&last_ms=` | 132.6 ms, same shape | 0.4 ms |
| `/plot/channels?port=` | 190.2 ms, `SCAN lines` + bloom filter + 2 temp b-trees | 137.9 ms |
| `/plot/channels` (control) | 33.3 ms | unchanged |

**M2** is `CROSS JOIN`, which in SQLite forbids reordering rather than asking for a cartesian
product. Driving from `lines` also discards the `ORDER BY cf.line_id DESC` index order, so the
whole matching set goes through a temp b-tree before `LIMIT` can apply - the 300x. The other
five filter combinations (`since_id`, `can_id`, `last_ms` alone, `port+last_ms`, unfiltered)
plan identically before and after, so pinning the order costs nothing.

**M1** is a join in place of `line_id IN (SELECT id FROM lines WHERE port = ?)`. It stays
linear, and that is not a defect: the aggregate counts every point of every channel, so its
unfiltered form is linear too (33 ms) and no rewrite makes it a seek. What the fix removes is
the *extra* scan of `lines` the filter added. Residual cost is one primary-key probe per point,
unavoidable without denormalising `port` into `plot_points`.

**Third site swept, complies.** `query_plot_series` has the same `plot_points JOIN lines` shape,
but its mandatory `pp.name = ?` equality on the leading column of `idx_plot_name_line` pins the
drive order at every filter combination measured, with and without stats. Left alone.

Tests `test_can_frames_always_drives_from_the_frame_table` (six filter combinations) and
`test_plot_channels_port_filter_does_not_scan_lines` pin the plans. Both explain the statement
the store actually issued, taken off the connection's trace callback, rather than a copy of it -
leg 6 lists a hand-written copy as a known way for a plan test to prove nothing. Revert-verified
against both reverts. They need no bulk data: without `sqlite_stat1` the planner makes the same
choice on a two-row capture.

Registry updated: class 20 gains the join-order shape and the no-stats sweep condition, and the
Windows leg's proposed class 21 (wall-clock granularity in tests) is now filed.

## 2026-08-01 - Measurement leg, Windows

Host: Windows 11 Home 10.0.26200, Python 3.12.9 (uv venv), mcuscoped 0.1.1, all commands
driven through the **installed console scripts** in `host/.venv/Scripts` (class 15), never
`python -m`.

Capture under test: the live user capture, 114,121 lines / 21,291 CAN frames / 204,734 plot
points / 23.7 MB, plus a synthetic capture grown to 1M lines for the scaling numbers.

Hardware present, so this run covers the bench item as well as the sim:
COM7 = FTDI 0403:6001, COM4 = Espressif 303A:1001. COM7 was attached and carried
**rx=840** real bytes, so the native pyserial read path was exercised, not only `socket://`.

### Findings

**M1. `/plot/channels?port=` plans as an open-ended scan and grows linearly (class 20).**
Plan on the live capture: `SCAN lines` + `CREATE BLOOM FILTER` + `USE TEMP B-TREE FOR GROUP BY`
+ `USE TEMP B-TREE FOR ORDER BY`. Measured on the synthetic capture:

| lines | `/plot/channels` | `/plot/channels?port=` |
|-------|------------------|------------------------|
| 100k  | 2.7 ms           | 34.7 ms                |
| 500k  | 16.9 ms          | 198.4 ms               |
| 1M    | 34.7 ms          | 406.5 ms               |

Both variants are linear in table size rather than bounded seeks, which is the class 20
signature. Severity is moderate, not severe, and the reason is worth recording: the query runs
off the loop via `match_executor` (class 1 is satisfied), the web UI never calls this endpoint
at all (it builds plots from the WS stream), and `mcu plot channels` (cli.py:1189) never passes
`port`. So the 406 ms variant is reachable only by a direct REST caller. It is also on the
coverage leg's existing "shipped path with no test" list.

**M2. `/can/frames?port=` picks a scanning plan on the real capture (class 20).**
`WHERE l.port = ?` plans as `SCAN l` + `SEARCH cf` + `USE TEMP B-TREE FOR ORDER BY`, 19.8 ms
against 0.1 ms for every other filter on the same endpoint (`since_id`, `can_id`, `last_ms`,
and `port` combined with `since_id`) - a ~200x gap. `lines` has no index on `port`, so the
predicate cannot seek, and driving from `lines` loses the `ORDER BY cf.line_id DESC` index
order, forcing the whole matching set through a temp b-tree before `LIMIT` applies.

Honest limit on this one: it did **not** reproduce on synthetic data, where every row shares
one port and the planner drives from `can_frames` instead (0.2 ms flat to 1M lines). It is
planner- and distribution-dependent, so any fix needs a test that pins the *plan*, not the
time (REVIEW.md class 20 already says this).

Exposure is small: `mcu can -f` passes `since_id` in its poll loop (cli.py:1092), which takes
the fast plan; only the one-shot `mcu can -p X` and the follow-prime hit the slow one.

### Ruled out, with the probe that ruled it out

- **Export of a running session.** The 77e5a69 regression (400 on every platform) is fixed and
  holds on Windows: `GET /sessions/3/export` on the *active* session answered 200 / 1.99 MB.
- **`format=` ignored on session export.** Looked like a defect (`text`, `jsonl`, `csv` all
  returned byte-identical bodies). It is not: SPEC 3.4 gives this endpoint no `format`
  parameter - it always emits a standalone SQLite db (`application/vnd.sqlite3`). Equal sizes
  were the SQLite page count, which moves in 4096-byte steps.
- **The sim brick (`can tx 7FF`).** Sent `can tx 7FF 1122`; the sim answered OK and `ping`
  still answered afterwards. Listener and serving thread both survived.
- **Daemon collision, db-lock path (classes 3, 7, 12).** A second `mcuscoped --sim --port 8765`
  exited 1, named the holder (`pid 13144 on DANIEL-PC since ...`), left the first daemon's pid
  record byte-identical, and the original kept serving.
- **Daemon collision, port-bind path.** The above is caught by the *db lock*, so the port path
  was probed separately with a second config pointing at a different `db_path`: exited 1 with
  "127.0.0.1:8765 is already in use", pid record preserved, original still answering. No
  second daemon printed a URL it could not serve (the 77e5a69 class-12 shape).
- **Daemon lifecycle.** Clean `mcu daemon stop` -> exit 0, record removed, no processes left.
  `mcu status` with no daemon -> exit 3, no traceback. `mcu daemon stop` twice -> exit 1,
  "no pid file". After `taskkill /F`: stale record left, `status` -> exit 3, and
  `daemon stop` detected staleness, removed the record and exited 1.
- **Class 8, thread teardown on Windows re-attach.** 10 detach/re-attach cycles against the
  real COM7 (not the sim): 0 failures, `connected` after every cycle.
- **Class 10, `--json` stdout purity.** Ten subcommands run through `mcu.exe`; stdout parsed as
  exactly one JSON document in every case, including the four that exited 1 - those emit a JSON
  error document on stdout and the human usage text on stderr, which is the contract.
- **Class 9, exit codes.** `--help`/`--version` 0, no args 1, unknown subcommand 1, no daemon 3,
  bad regex 1, `InvalidURL` 3, port-out-of-range 3, unparseable URL 3, monitor error 1, no such
  session 1. No traceback reached the user on any path.
- **Class 19, the client-side regex timeout.** `mcu lines --match "(a|a)+$"` returned promptly
  instead of hanging, so the `timeout=` the second fix added is in force.
- **Class 13, redirected output under a non-UTF-8 console.** `mcu devices` redirected to a file
  under cp437, cp932 and cp1252: exit 0, byte-identical 151-byte output, no traceback.
- **Phantom ports.** `mcu devices` listed exactly COM7 and COM4, matching
  `[System.IO.Ports.SerialPort]::getportnames()`. The 6e3d1ed `/dev/ttyS*` fix has no Windows
  analogue leaking through.
- **`mcu attach <absent device>` exits 0.** Investigated as a possible class 12
  (healthy-while-dead) and rejected: presence-gated reconnect makes attaching an absent device
  legitimate, and `ports`/`status` both report it `disconnected`, so the health surface tracks
  reality. Only the CLI's "attached" wording is optimistic.
- **Web UI "stream reconnecting..." chip.** Looked like a dead-while-healthy inversion - the
  chip's text was present while the rate counter climbed 98->103/s. False alarm caused by the
  probe: it read `textContent`, which includes hidden nodes. The element is `hidden=true`,
  `display:none`, `offsetWidth 0`, and `document.body.innerText` contains no warning. Recorded
  because the probe error is the reusable lesson: assert visibility, not text presence.
- **Web UI load.** Loads at `/ui/`, no console errors for the whole session, WS accepted a
  fresh connection in 4 ms and delivered 55 frames of real data in 2.5 s, status bar showed
  both ports and a live rate.
- **Daemon stderr.** Empty across the entire run, including both collision attempts, the
  crash, and the 10 attach/detach cycles.

### Endpoint latency, live capture (n=5, median)

`/status` 0.9 ms, `/ports` 0.4 ms, `/devices` 0.8 ms, `/config` 0.8 ms, `/sessions` 5.3 ms,
`/lines?limit=100` 0.9 ms, `/lines?limit=1000` 2.5 ms, `/can/frames?limit=200` 3.6 ms,
`/plot/channels` 21.9 ms.

`GET /devices` is 0.8 ms, so the 0.70 s freeze of 77e5a69 stays fixed on Windows.
`GET /sessions` is 5.3 ms at 3 sessions, so the class 20 `COALESCE` fix holds here, though
this capture is far below the 500-session case that produced the original 19.2 s.

### Bench session: real STM32 over ST-Link (added after the first pass)

Board: ST-Link VCP on COM5, `0483:3754`, serial `0033003F3235511738363730`. Firmware
identifies as `monitor 1 charger-test` and predates the `mark` command. No CAN node on the
bus. This closes the "no board speaking the monitor protocol" gap listed below.

**Monitor round-trip latency, 40 pings over the real UART: min 3.00 ms, median 4.00 ms,
max 4.65 ms, mean 3.94 ms.** REST wall time around it, median 4.79 ms. This is the number
the sim cannot produce and the baseline any future latency regression is measured against.

Command surface as answered by real firmware: `ping`, `info`, `i2c scan`, `can stat`,
`can filter`, `can tx` all `ok`; `i2c rd/wr/wrrd`, `spi xfer`, `adc read` answer `nosup`;
`gpio get/set` answer `badarg`, `gpio read` and `adc get` `badcmd`; `mark` answers `badcmd`
as expected for this build. Every one of those decoded into a correct error envelope, so
class 11 (codec symmetry) holds against a second, independent implementation of SPEC 5 -
which is a stronger test of the codec than the sim can give.

**M3 (fixed this session). `POST /cmd` answered HTTP 500 for an empty command.**
Class 18: `send_command` calls `format_command`, which raises `ProtocolError`, while every
sibling validation on the same outgoing path (`_encode_wire`, `_write_bytes`) raises
`PortError`, which the handlers already map to 400. Unmapped, it reached FastAPI as a 500.

Confirmed by sweep, not by inspection: of 13 malformed-input cases across `/cmd`, `/send`
and `/marker`, exactly two returned 5xx - `cmd=""` and `cmd="   "`. Oversize, embedded
newline and non-ASCII all correctly returned 400 on the same endpoint.

Two real costs. Any REST consumer saw a server fault for its own bad input; and the daemon
log took a full unhandled-exception traceback for a routine typo, which is the crash log a
genuine bug needs being spent on a rejected empty string. The CLI still exited 1, so the
class 9 contract was not affected - this was invisible from the CLI, which is why a bench
session found it and the suite had not.

Fixed in `serial_link.send_command` rather than in the handler, because all three
`send_command` callers (`/cmd`, and `body.send` on `/wait` and `/assert`) are reachable with
user text and all three are closed by the one change. `serial_link.py:849` already documents
this convention ("Translate that into PortError so send_command's ...").
Regression test `test_empty_cmd_is_client_error_not_500` covers all three endpoints, and
asserts the neighbouring 400s and a working command alongside, so it discriminates the
mapping and nothing else. Revert-verified: with the fix backed out it fails with the
original `ProtocolError` traceback.

**M4 (fixed this session). `test_purge_before_ts_deletes_only_what_predates_it` was 50%
flaky on Windows.** Found by running the suite, not by reading it: 4 failures in 8 isolated
runs. Not caused by the M3 change - it touches no serial code.

Mechanism, measured rather than guessed: `time.time()` on Windows 11 has a resolution of
**0.015625 s**, and 199,990 of 199,999 consecutive calls returned the identical float. The
test took `cut = time.time()` immediately after writing "old two", so `cut` landed in the
same tick as that line's `ts`; `last_id_before_ts` selects `WHERE ts < ?`, which then spared
it. Its `time.sleep(0.01)` compounded this by being shorter than a single clock tick.

The store is correct - "older than" is a sane exclusive boundary - so the test was fixed,
not the code: the cut is now derived from the stored rows and the clock is spun until it
reads strictly past each boundary. 20 consecutive runs pass, up from 4 in 8. Swept
class-wide: this was the only test taking a bare `time.time()` as an ordering boundary; the
one other timestamp comparison (`test_regressions.py:909`) uses a 1.0 s tolerance.

This is a test-quality class the registry does not yet name, and the runbook's own question
("what would it take to make the assertion true on the OS it was not written on") points
straight at it. Proposed registry entry, to be added by whoever closes the round:

> **Wall-clock granularity as a test ordering assumption.** A test that takes `time.time()`
> as a boundary and requires strict ordering against a stored `ts` assumes the clock
> advances between calls. On Windows it does not: the resolution is 15.625 ms and
> consecutive calls routinely return the same float, so `sleep()` under one tick may not
> advance it at all. Sweep: every test comparing a captured `time.time()` against a stored
> `ts`; each must derive the boundary from the data or spin until the clock strictly
> advances.

**M5 (found by the owner's visual check, fixed). A freshly loaded web UI cannot decode
the typed `!ps` streams in its own backfill, so the typed and digital charts start empty
while the ad-hoc chart is full.**

Reported as "approx 2 cycles of sine on ad-hoc, only 4 points on the other graph, one sample
on the digital monitor". Not a daemon defect - the store is exactly symmetric: over the same
4,000-line window all nine channels hold exactly 1,000 points each, and over the whole
capture the typed streams hold 32,904 each against ad-hoc's 33,004.

The cause is a window-size mismatch. A first-ever connect seeds
`GET /lines?order=desc&limit=200` (api.js:174). The sim emits 4 lines per 50 ms tick, so
200 rows is about **2 s** of capture - while `!pd` definitions rebroadcast only every
**5 s**. A typed `!ps` sample is undecodable until its `!pd` has been seen, so whether the
charts populate depends on whether a `!pd` happened to fall inside that 2 s window.

Measured on two consecutive loads of the same live stack:

| load | `!pd` in window | ad-hoc `!p` | typed `!ps` decoded | dropped |
|------|-----------------|-------------|---------------------|---------|
| 1    | yes (3 defs)    | 39 points   | 25 of 40 per stream | 15 each |
| 2    | none            | 40 points   | **0 of 40** per stream | all 122 |

So most page loads land in case 2 and show empty typed and digital charts for up to 5 s,
while the ad-hoc chart is fully populated immediately. The owner's "4 points / one sample"
is the boundary case, with the `!pd` burst near the end of the window.

Fix shape, not applied: seed the definitions before replaying the backfill, by fetching the
newest `!pd` rows (`GET /lines?match=^!pd&order=desc`) and pushing them through the existing
`plotIngest` path. No new endpoint and no new parsing - the raw definitions are already
queryable and decode with the code that is already there. The `!pd` rows must go to
`plotIngest` only, not `pushBuffer`, or they would double up in the terminal and advance the
`state.maxId` watermark.

`/plot/channels` is *not* sufficient for this: it returns `type`, `kind`, `group` and `bit`
per channel, but orders by name, so the field order inside a packed sample is lost
(sid 0 comes back ftest, ramp, tri against a wire order of tri, ramp, ftest).

The one design question - `/lines` has `since_id` but no upper id bound, so "the definition
in force at the *start* of the window" cannot be fetched directly - was put to the owner,
who ruled that definitions change rarely if ever, so the newest `!pd` is the right one to
take. Implemented on that basis (`seedPlotDefs` in api.js), applying the fetched rows
oldest-first so a genuine change still leaves the newest definition in the cache.

One thing the fix had to avoid, found by measuring rather than by reasoning: `match=` is a
regex scan, so an unbounded `^!pd ` search over a capture that contains **no** plot streams
(a board that never emits `!pd` - the very board on the bench) is a full table scan on every
page load. Measured: **170 ms over 169k lines and linear from there**, against 25 ms once
anchored. The search is therefore bounded with `since_id` to `PLOT_DEF_LOOKBACK` (20,000)
rows below the seed window, and the JS test asserts the bound is present, not just that the
query happens - an unbounded version would pass a correctness-only test while reintroducing
registry class 20 on the page-load path.

Verified end to end against the live simulator, replaying the UI's own two queries across
three consecutive loads:

| load | typed samples decoded before | after |
|------|------------------------------|-------|
| 1    | 21 of 40                     | **40 of 40** |
| 2    | **0 of 40**                  | **40 of 40** |
| 3    | 22 of 40                     | **40 of 40** |

Regression test `tests/webui_js/api_plot_def_seed.test.mjs` drives the real
`connectWs`/backfill path with a seed window deliberately containing no `!pd`.
Revert-verified: with the call site removed it fails with "expected one !pd seeding query,
got /lines?order=desc&limit=200".

Confirmed visually by the owner across several reloads ("charts look equal on reload"),
which is the check that found the defect and so is the one that closes it. The old
behaviour was luck-dependent - a given load showed a partial chart, an empty one, or a full
one - so repeated reloads were the discriminating check, not a single look.

### Ruled out on real hardware

- **Presence-gated reconnect across a physical unplug (owner drove two unplug/replug
  cycles).** Both cycles identical: the daemon saw `connected=False` within 500 ms of the
  unplug and `/devices` dropped COM5 in the same sample, so the port never read healthy
  while the cable was out (class 12). On replug it reattached on its own within 500 ms of
  the device reappearing (t=8.63 present -> t=9.13 connected; t=30.72 -> t=31.22).
  `rx_dropped` stayed 0 and `lines_rx` did not move across either cycle, so nothing phantom
  was charged to the port.
- **First command after a fresh physical connect is lost.** Seen twice, and reproducible:
  the very first `ping` after the initial attach, and the first `ping` after each replug,
  answered `timeout` at ~1000 ms; the next answered `ok` in ~4 ms. Ruled out as a host
  defect - the board resets on USB re-enumeration and is still booting, and the monitor
  answers in 4 ms once up. Recorded because it argues for a longer first-command timeout (or
  one retry) immediately after a reconnect, which is a design question, not a bug.
- **Command lost on detach/re-attach without a physical disconnect.** Investigated as the
  cause of the above and disproved: 5 detach/attach cycles each followed immediately by two
  pings gave 10/10 `ok`, and 10 back-to-back pings gave 0 timeouts. The device stays
  enumerated and the board never resets, so the symptom is specific to a physical replug.
- **`can tx` onto a bus with no other node.** The monitor is honest about it: the first
  transmit drove the error counter to 16 and the state to `passive`, and once the TX
  mailboxes filled, 10 of 20 further transmits were refused with `busy` rather than
  accepted. Nothing reported a clean send onto a dead bus.
  One SPEC observation, not a host defect: after those transmits `can stat` read
  `err=0 state=passive`, and `tx=3` after 10 accepted commands. Error-passive is entered by
  the error counter exceeding its threshold, so `err=0` alongside `state=passive` means this
  firmware's `err` is a since-last-read delta while `state` is a latch. SPEC 5 does not say
  which `can stat` reports, so two firmwares can both be conformant and disagree, and the
  host displays the number either way. Worth pinning in SPEC.

### Gaps this run did not close

- **uPlot rendering was verified by the owner, and it found M5.** The browser pane does not
  composite in this environment so `screenshot` fails; the owner looked at the charts
  directly. That single visual check produced the one defect no probe in this leg had found,
  which is the argument for keeping a human in this tier rather than declaring it covered.
  The settings dialog remains unverified.
- ~~No board speaking the monitor protocol.~~ Closed by the bench session above.
- **`/plot/series` was not measured** - the probe's parameters were rejected (422) and it was
  not retried.
- **No plot or digital channels on the bench board.** `charger-test` emits no `!p`/`!pd`, so
  the real-hardware plot path is still unexercised; the plot numbers above are all sim or
  synthetic.
- **`can stat` semantics are unpinned in SPEC 5** (see the CAN entry above). Not a defect
  found, but a spec gap this session surfaced.
