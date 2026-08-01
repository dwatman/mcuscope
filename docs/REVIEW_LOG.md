# Review round log

## 2026-08-02 - Measurement leg, Linux

Driven through the installed console scripts against `mcuscoped --sim`. No bench board
(`/dev/ttyACM0` absent), and no browser check (this environment cannot composite the pane;
that stays with the owner). Note for every class 12 claim below: **there is no `/health`
route** - `/health` and `/healthz` both 404, and `/status` is the only health surface.

**D1 (fixed, class 17). The size cap returned essentially no space to the filesystem.**
`PRAGMA incremental_vacuum` yields one row per page freed, and sqlite3 steps a statement
only as its rows are consumed, so `conn.execute(...)` with no fetch advances it exactly
once. Both call sites did that. Measured on a 20.5 MB capture with 5012 free pages,
SQLite 3.50.4:

| call | freelist | file |
|------|----------|------|
| `execute()` alone | 5012 -> 5011 | 20566016 -> 20566016 (unchanged) |
| `execute().fetchall()` | 5012 -> 0 | 20566016 -> **12288** |

Observed live first: a daemon under a 2 MiB cap trimmed 26,519 lines over 90 s while the
freelist grew 17.0 -> 17.6 MB and the file never moved. So the cap was trimming rows
correctly and handing back about 0.02% of the space, and `contextlib.suppress(Exception)`
around it meant even a hard failure would have been silent. The same request-versus-result
shape as the `auto_vacuum` defect (99eab7c) that this very mechanism was built to fix.

Fixed with a bounded, fetched reclaim (`_reclaim_pages`). Bounded because both callers run
on the event loop and an unbounded reclaim is O(freelist): 2000 pages is 8 MB at the 4 kB
page size, measured at 15.8 ms, against 55 ms to drain 7518 at once. The retention sweep
runs periodically, so a backlog drains over successive ticks.

**D2 (confirmed, NOT fixed, class 12). A WebSocket subscriber that stops reading loses rows
and nothing counts them.** Probed with a raw socket (real TCP backpressure, not a library
buffer): handshake, 60 s without reading, then drain and diff against `/lines`.

```
store produced ids 62337..68313 = 5977 rows
WS delivered 3984 distinct ids; 2194 of the span missing (36.7% lost)
during and after the stall: connected=true lines_rx=58199 rx_dropped=0 write_errors=0
```

`Store._broadcast` drops the oldest on a full queue with no counter and no gap marker. The
serial side has `rx_dropped`; the subscriber side has no equivalent. The web UI builds its
plots from this stream, so a tab that falls behind renders a chart with holes while every
health field stays green. Carried: the fix is a counter plus a gap marker in the stream, and
the marker is a wire-format question, so it wants the same pass as M5.

**D3 (confirmed, NOT fixed, class 17). `/status` pairs two size numbers measured
differently.** `db_size_bytes` is main file + `-wal`; the cap is enforced against
`content_bytes()` = `(page_count - freelist) * page_size`. Measured with the cap working
correctly throughout: `db_size_bytes=24130888` beside `db_max_bytes=2097152` and
`lines_trimmed=0`, i.e. a working cap that reads as a broken one. No field exposes the
number the cap actually governs, and `db_size_bytes`'s own docstring claims it "matters both
for the status display and for the size cap" - it is not what the cap uses. D1 masked this:
with the file now shrinking, the two numbers converge, so it is much less visible but still
wrong. Carried.

**D4 (fixed with R2). `mcu can dump --json` is a third JSONL command SPEC did not name.**
Found independently by this leg and by the class 10 sweep.

**D5 (rolled into D1, class 17). `auto_vacuum` is applied with no readback**, unlike
`journal_mode` two lines below it, which reads its result set and warns. A capture created
with `auto_vacuum=NONE` (an older one, or one created by another tool) starts normally,
reports nothing anywhere, and its reclaim path is simply dead.

**Observation, not a finding.** Both daemon collision diagnostics go to **stdout** on an
exit-1 path. `mcuscoped` has no `--json` contract so nothing is violated, but it is the
opposite of the CLI's convention.

### Ruled out, with the probe that ruled it out

- **Daemon lifecycle.** `status` 0; `daemon stop` 0 and the process gone; `status` with no
  daemon 3 ("[Errno 111]"); a second `daemon stop` 1. No traceback anywhere.
- **Collision, capture-lock path (classes 3, 7, 12).** Second daemon, same db: exit 1 naming
  the holder's pid and host; the pid record's md5 identical before and after; the first
  daemon still answering. Port-bind path with a different db: exit 1, "Address already in
  use", record unchanged. No daemon printed a URL it could not serve (the 77e5a69 shape).
- **Crash.** SIGKILL left the record with a dead pid; `status` exit 3 with no traceback;
  `daemon stop` detected staleness, removed it, exit 1; a restart on the same db succeeded,
  so the OS had released the capture lock.
- **Class 12, reader thread dies with its peer.** SIGKILL'd the sim: `/status` flipped to
  disconnected in under 1 s and stayed there; `mcu cmd ping` exit 1 "not connected".
- **Class 12, listener alive but sessions dead (the 187a0e4 shape).** A stub accepting and
  immediately closing: `connected:false` throughout, 13 reconnects in 10 s (~1.3 Hz, not a
  busy loop), daemon CPU 0.7%.
- **Class 12, hung-but-connected peer.** A stub that accepts and never speaks: `connected:
  true, lines_rx=0`, which is correct (the link is up), and `mcu cmd ping` returned `timeout`
  exit 2 in 1.26 s rather than hanging.
- **`/plot/series`, last round's explicit unmeasured gap, now measured.** Signature is
  `name` (required), `port`, `last_ms`, `since_id`, `session`, `limit`, `decimate`. All seven
  variants 200. Runs off the loop: `/status` median 2.72 ms during the request against a
  1.51 ms baseline. The previous round's 422 was a probe defect, as the runbook said.
- **`/plot/export` does not block the loop.** 896 ms for 256 kB while `/status` stayed at a
  1.93 ms median over n=41, max 3.51 ms. Its 400s are contract, not probe defects: `wide`
  across two streams is refused by design, and the same call on one stream returns 200.
- **Class 17, every config PUT read back.** Out-of-range values 422 with the saved config
  unchanged; `max_db_bytes=100` 400 (below the 1 MiB floor); accepted values read back
  identically on both `/config` and `/status`. No clamped-but-reported-as-requested value.
- **Class 19, the client-side regex timeout.** `mcu lines --match '(a|a)+$'` returned
  immediately; `--match '('` exit 1 with no traceback. (The web UI had no such guard: M1.)
- **Phantom ports.** `mcu --json devices` returns `{"devices": []}` with 32 `/dev/ttyS*`
  present.
- **CLI contract, 82 invocations** (40 human-form, 21 `--json`, 21 error paths): no traceback
  on any path, and no daemon stderr contained one. Codes as specified: success 0, usage and
  operational failures 1, wait timeout 2, unreachable daemon and malformed URL 3.

### Probe defects caught and re-run, recorded per the runbook

The first CLI sweep invented `session show`, `i2c read/write`, `gpio read/write`,
`can --limit`, `--pattern`, `--last`: 14 of 53 cases were measuring the unknown-command
path again, which is *the exact error the previous Linux leg logged*. Re-run against
`--help`. Second pass: `spi xfer 1`, `adc read 0`, `gpio get PA5` exit 1 because the sim
rejects invented peripheral names; with real ones all exit 0.

### Endpoint latency, live sim capture, n=5 median

62k lines, 9 plot channels, ~5.9k points each, 13 MB.

| endpoint | Linux now | Linux prev | Windows leg |
|---|---|---|---|
| `/status` | 0.75 ms | 1.09 | 0.9 |
| `/ports` | 0.62 | 0.73 | 0.4 |
| `/devices` | 0.85 | 1.33 | 0.8 |
| `/sessions` | 1.67 | 0.76 | 5.3 |
| `/lines?limit=1000` | 3.80 | 6.11 | 2.5 |
| `/can/frames?limit=200` | 4.50 | 6.80 | 3.6 |
| `/plot/channels` | 6.46 | 1.36 | 21.9 |
| `/plot/channels?port=sim` | 15.94 | 1.66 | (M1: 34.7 at 100k) |
| `/plot/series?name=sine` | 17.98 (5892 pts) | unmeasured | unmeasured |
| `/plot/export?names=sine` | 788 (227 kB) | - | - |

`/devices` at 0.85 ms, so the 77e5a69 0.70 s freeze stays fixed on Linux. `/can/frames?port=`
did **not** reproduce the Windows M2 here (4.62 against 4.50 ms) because this capture has one
port, exactly as the Windows leg predicted.

### Could not measure

- **Killing the store writer task, and reading back `synchronous`/`foreign_keys`.** Both need
  in-process access to a live daemon. The agent built an out-of-band control channel for it
  and the permission classifier denied loading it into the daemon three times; it did not
  route around the denial, and the artifact was deleted. That channel was an `exec()`-on-a-
  socket surface and should not be rebuilt: if this measurement is wanted, it needs a
  deliberate, reviewed test hook, not an ambient one.
- **A real store write failure.** The db sits on a 271 GB filesystem, so no disk-full path.
- **Any native serial read path.** Sim and `socket://` only.

## 2026-08-02 - Module leg, Linux: the web UI modules never read

Modules by least prior attention, from the previous round's own not-read list: `terminal.js`,
`api.js`, `statusbar.js`, `state.js`, and `plots.js`'s rendering half. Four confirmed
findings, four refutations, and a **new class (23)**. Every finding was probed before it was
reported and every fix revert-verified.

**M1 (fixed, class 19, the round's highest severity). The pane filter took the daemon's
length cap and dropped its ReDoS guard.** `terminal.js:218`'s comment says the 200-char cap
"mirrors the daemon's MAX_MATCH_LEN". The daemon does two things at that boundary, the cap
*and* `regex.compile` plus `.search(text, timeout=...)`, and `cli.py:616` already carries a
comment saying in terms that taking the engine without the timeout gains nothing. The web UI
is the third client of this grammar and the only one with no protection. Measured with
`(a+)+$`, six characters, which passes the length check:

| input | one `test()` call |
|-------|-------------------|
| 22 chars | 70 ms |
| 24 chars | 300 ms |
| 26 chars | 1258 ms |
| 29 chars | **35992 ms** |
| `rebuild()` over 200 buffered rows | **60749 ms** (the buffer holds 5000) |

`applyRegex` is wired to the `input` event, so the pattern goes live per keystroke and the
freeze is unrecoverable: the filter box that would undo it stops responding.

JavaScript has no regex timeout, so the mechanism could not be copied; the invariant was
kept instead. A 250 ms wall-clock budget is spent around every match at the single
chokepoint both callers route through; past it the pattern is dropped, remembered so it is
never re-armed, and the box goes `.invalid` with a title saying the lines below are
UNFILTERED (class 12: the surface must not show an unfiltered view as though it were
filtered). One uninterruptible call still gets through, which is the honest ceiling without
a Worker; what is fixed is the unrecoverable state, not the single hiccup.

**M2 (fixed, new class 23). A paused terminal pane silently un-froze.** `rebuild()`
recomputed `pane.rows` from the shared buffer and zeroed `pane.pending`, and two sibling
paths called it on every pane unconditionally: the end of every backfill (so every WS open
and reconnect) and the high-rate release. Driven through the real `connectWs`/`onopen` path:
a pane paused on rows [1,2,3] with "3 new" came back as [1,2,3,4,5,6] with the counter
cleared, while the pill still read "paused". Its own comment claimed it preserved the
paused state, which was true only of the `autoscroll` flag. `plots.js` had already fixed
exactly this for charts; the terminal pane was the sibling that never got the same care.

**M3 (fixed, class 12). The port chips read connected after the daemon died.**
`statusbar.js`'s `refreshStatus` catch called `setDaemonOnline(false)` and
`renderSession(null)` but never `renderPorts`, so the per-port surface held its last good
reading: a green connected chip and a stale db size beside "daemon unreachable". The
inconsistency inside the one catch block is the tell.
This is also a test-quality finding: the existing test named "an unreachable daemon says so
instead of holding the last good reading" asserted only on `daemonVer`/`daemonUptime`/
`daemonDot`, so it passed over exactly the surface it was named for. A sixth instance of the
"asserting on the wrong surface" shape; the test now asserts the chips and the db size.

**M4 (fixed, class 19). `lineTick`'s `!ps` branch took a tick from lines every other decoder
rejects.** `state.js:175` read `parts[2]` as a hex tick with no check on the line's arity and
no check that the sid was declared, where `plots.js` requires exactly 4 tokens and a matching
`def.sid` and the daemon keeps such a line as a generic event. It sets the sticky global
`state.anchorTick`. Probed: `!ps 0 ABCD` is rejected by plots.js (0 charts before and after)
yet set `anchorTick = 43981`, so a real tick 0x64 then rendered as **-43881**, shifting every
terminal timestamp and the tick-mode x-axis for the session, recoverable only by "clear all".
The module's own comment says this defect was fixed; it was fixed for *range* and re-entered
through *validity*.

### Class 23 sweep, run immediately after filing it

Three surfaces in the web UI carry a paused state. Writers of the frozen contents, each ruled:

- **Terminal panes** (`autoscroll`, now `frozenId`) - live arrival (counted into `pending`
  only, correct), `rebuild()` from backfill, `rebuild()` from the high-rate release. Was the
  violation, now bounded by `frozenId`.
- **Analog charts** (`paused`, `frozenLen`) - `addSample` gated correctly, and the ring
  eviction slides `frozenLen` so the freeze survives capping; `currentData` clamps. Complies.
- **Digital lanes** (`digitalPaused`) - readouts gated, and the shared right edge is frozen on
  pause. Complies.

**M5 (confirmed, NOT fixed - needs an owner decision).** Both export buttons ignore their
surface's freeze. `exportChart` and `exportDigital` send only `last_ms`, which the daemon
resolves against *now*, so a chart paused on an interesting transient exports a window that
does not contain it, under a button whose own title says "the current window". Probed: paused
at `frozenLen = 120` with 180 samples buffered, the request asked for the 30 s ending at ts
1179 while the chart showed up to ts 1119.

Not fixed because it cannot be: `GET /plot/export` takes `names`, `last_ms`, `session` and
`format` and has **no upper bound parameter at all**, so honouring the freeze needs a new one
and that is a SPEC 9.2 change, not a client fix. This is the same gap last round recorded for
`/lines` ("`since_id` but no upper id bound"), now biting a second endpoint, which argues for
deciding the shape once for both. Left for the owner.

Also noted, unfixed and minor: with every trace toggled off, `downloadCsv` returns early and
the export button does nothing at all, silently.

### Refuted, with the probe that refuted it

- **`intField` is another class 22 site.** No: `"1e9"` gives 1000000000, and `"12abc"`, `""`,
  `"1e400"`, `"1_7"`, `"٣"` all give NaN. The leading-digit truncation the class is about is
  already gone, and every caller range-checks afterwards.
- **A non-finite y value can still reach a uPlot series array.** No: `!pd 0 big:u4*1e308`
  with a u4-max sample, and `!p 100 a=1e999`, both produced no series entries. The parse and
  scale gates hold. (The *x* arrays are a real gap; see below.)
- **`lineTick`'s marker branch is looser than `parse_marker`.** No: matches clause for clause,
  and JS `\d` is ASCII-only so the `٥٥` case cannot arise there.
- **The update badge's href is a sink-validation hole.** No: gated on `/^https?:\/\//i` with a
  fallback, and the `javascript:`/`data:`/empty cases are already covered by a test.

### Carried

**M6. The chart x arrays have no `Number.isFinite` boundary.** Class 6's producer list names
the parse and scale paths, both y-side. `x.host = row.ts` goes into `xsHost` ungated and
`handleWsRow` validates only `typeof row.id === "number"`; probed, `xsHost` took
`[1003, null, "abc"]`. The monotonic bump does not catch it either, since `undefined <= 1003`
is false. Only reachable from a malformed daemon or proxy response, not from device output,
so it is carried rather than fixed, and class 6's sweep gains the x boundary.

Not read from these modules' import graph: `app.js`, `theme.js`, and `plots.js`'s decode half
(read for grounding this round, audited last round).

## 2026-08-02 - Registry leg, Linux: the classes 1-20 verdict lists

Filed here because the previous round closed without them and called that unrecoverable.
Four sweeps, run under the sweep discipline, no `head`/`tail` anywhere. Findings from this
leg are prefixed R (registry) to keep them apart from the module leg's.

### Class 1 - blocking work on the event loop or default executor. 2 sites, 1 finding.

`grep -n "run_in_executor(None" host/mcuscope` returned **2**.

- `serial_link.py:317` - complies - the reader-thread join, the reserved use itself.
- `store.py:195` - exempt - prose inside `match_executor()`'s docstring, no call.

**R1 (fixed). The sweep command cannot see the class it is written for.** `asyncio.to_thread`
*is* `loop.run_in_executor(None, ...)`, so 9 further sites sat in the pool the invariant
reserved, and the grep finds none of them: `server.py:785` (`_enumerate_devices`, once measured
at 0.70 s), `818, 862, 882, 913, 952` (config load and saves), `1035` (`export_session_db`,
unbounded), `update_check.py:243, 250`. Both slow ones are named by the invariant's own text.

Fixed by inverting the invariant rather than re-arming it at 9 sites: the join owns a private
`serial_link._join_pool`, `to_thread` is unrestricted, and the sweep becomes an absence. The
argument, and the design alternative that was rejected, are in the registry entry. A rule the
obvious stdlib idiom breaks is a bad rule, and 9 unnoticed violations is the evidence.

### Class 2 - text writes without explicit newline. 12 sites, 0 findings.

`grep -rn "open(" host/mcuscope | grep -v 'newline\|"rb"\|os.open'` returned **11**, plus 1
`write_text(`.

- `config.py:301` - complies - `write_text(..., encoding="utf-8", newline="")`.
- `serial_link.py:97`, `_stdio.py:166`, `pidfile.py:98`, `pidfile.py:109`, `cli.py:1377`,
  `cli.py:1530` - exempt - read mode; `newline=` is a write-side concern.
- `_stdio.py:146` - exempt - `CONOUT$`, the live console handle; CRLF is correct there.
- `_stdio.py:154` - exempt - `os.devnull`, content discarded.
- `cli.py:213` - exempt - `"wb"`, binary.
- `cli.py:1469` - exempt - grep false positive, matched `Popen(`.
- `cli.py:1471` - exempt - the match is inside a comment.

The one text write with a real byte-count concern, the pid record, already carries
`newline=""` and was excluded by the filter itself.

### Class 3 - listening sockets without Windows exclusivity. 8 sites, 0 findings.

`grep -rn "socket.socket\|\.bind(" host/mcuscope` returned **8**.

- `daemon.py:201`, `daemon.py:207` - complies - the probe socket and its bind, both under the
  `SO_EXCLUSIVEADDRUSE` path.
- `sim.py:526`, `sim.py:537` - complies - `SO_EXCLUSIVEADDRUSE` on `nt`, `SO_REUSEADDR` on
  POSIX, both before the bind.
- `sim.py:524`, `sim.py:558`, `sim.py:607`, `sim.py:650` - exempt - type annotations, not
  constructions.

### Class 4 - per-attach state lost on reattach. 7 reported fields, 0 findings.

Diffed `SerialPort.__init__`'s attributes against every field `/status` reports.

- `lines_rx`, `lines_tx`, `rx_dropped` - complies - carried across reattach by
  `PortManager._carried` (`serial_link.py:1035`), asserted by
  `test_carried_counters_follow_the_alias_not_the_device` and
  `test_carried_counters_are_bounded_and_evict_the_oldest`.
- `connected`, `device`, `baud` - per-connection by design (a baud change is done *by*
  re-attaching).
- `alias` - exempt - caller-supplied identity.
- `_seq`, not in `/status` but carried by the same tuple - asserted by
  `test_reattach_continues_the_command_seq`.

### Class 5 - argv hoisting in cli.main(). 0 findings.

No change to global options, aliases or subcommands since the last round, so the matrix was
re-run rather than extended: 8 tests across `test_regressions.py`, `test_cli.py` and
`test_hardening.py`, all passing. Cells covered: global option before the subcommand, after
the subcommand args, the attached short form (`-psim`), the `--` guard, a subcommand option
whose *value* looks like a global flag, a leading global value before subcommand resolution
(the 187a0e4 bit), `--token=abc` against `--token abc`, and `--version`.

### Class 6 - non-finite values reaching chart arrays. 8 producers, 1 finding.

- `plots.js:157` (float decode), `plots.js:189` (after `*= scale`), `plots.js:48`
  (`parsePlotValue`, which gates `1e999`-shaped literals) - complies - three gates, where the
  registry text names only two. Registry updated.
- `plots.js:282`, `digital.js:56` - complies - values arrive already gated.
- `plots.js:290`, `plots.js:311` - exempt - `null` gap markers by design.
- `plots.js:264` - **finding, see M6 below** - the x arrays have no gate.

### Class 7 - pid record lifecycle. 21 matrix cells, 5 unasserted, 2 live defects.

Cells with an asserted outcome: claim x {no record, stale, live other, live parent, our own};
release x {no record, live other, our own, our own externally rewritten}; stop x {no record,
live-but-not-answering, our own}; failed startup x {another daemon's record, live other,
readiness timeout}.

Cells with no asserted outcome: release-on-live-parent; stop-on-a-well-formed-stale-record;
stop-on-a-shim/serving-pid-mismatch; `daemon_start`'s own pid-write failure (`cli.py:1480`);
`daemon.main()` claiming then raising before `uvicorn.run()` (`daemon.py:331`, whose
`test_daemon_declines_to_start_on_a_taken_port` mocks the conflict *before* the claim, so the
release-on-exception path is never reached).

The module leg then found two live defects in this class; see P1 and P2 in its section.

### Class 8 - thread teardown on detach and shutdown. 4 scenarios, 3 unasserted.

- Normal stop - asserted indirectly by every detach in the suite, though nothing asserts the
  handle close positively.
- Join timeout (`serial_link.py:316`) - unasserted. This is the 187a0e4 bit, and the code
  guarding it has no test named for it.
- Loop closed under a live reader (`_post`, `serial_link.py:496`) - unasserted.
- Exception mid-read on an already-open handle - unasserted. The existing tests cover *open*
  failures, which is a different branch.

Not fixed this round: three tests, no known live defect. Carried to the next round's list.

### Class 9 - CLI exit-code contract. 74 sites, 0 findings.

Every `raise`, `except` and `typer.Exit` in cli.py was walked; all 74 map to 0/1/2/3 or are
exempt (three `raise AssertionError("unreachable")` after a `die()` that always raises, and
the deliberate `BrokenPipeError` re-raises that `_dispatch` turns into exit 0). Full list in
the leg's working notes; no site contradicted the contract.

The residual: `_dispatch` catches a finite list, so a *new* third-party exception type escapes
to `_stdio.console_entry`, which writes a crash log and re-raises (exit 1 with a traceback).
That is the intended backstop, not a blanket catch, and the registry's "not the fix" clause
still stands. Correction to the registry wording: `console_entry` does not *return* 1, it
re-raises after writing the crash file; the effective contract is the same.

### Class 10 - --json stdout purity. 37 commands, 1 finding.

Enumerated from the actual decorators (36 subcommands plus `--version`), driven through the
installed `host/.venv/bin/mcu` against a live `mcuscoped --sim`, asserting `json.loads`.

All 37 comply except the two documented exemptions (`mcu tail`, `mcu log export`) and:

**R2 (fixed). `mcu can dump --json` emits JSONL and SPEC did not say so.** `cli.py:1073`
prints one object per frame. The implementation is right - its `-f` form is an unbounded live
stream, exactly `tail`'s argument - and SPEC's exemption list named two of the three. This is
the *same defect as last round's N7*: that round corrected the sentence for `tail` and nobody
swept for the other emitters. Closed class-wide this time by an AST test that finds every
`out_json` inside a loop and pins the set, so a new per-row emitter fails until SPEC names it.

### Class 11 - codec symmetry. 5 pairs, 13 parsers, 0 findings.

Property-tested rather than read: seq boundaries, every code in `ERROR_NAMES`, CAN ids at 0,
1, max-1, max and max+1 across {standard, extended} x {rtr, not} x dlc 0..8, marker ticks at
None/0/mid/max - 0 round-trip failures, and `format_can_event`/`parse_can_event` confirmed to
reject the *same* ids in both directions. Then 3000 malformed inputs per parser, including
`٣٤٥`, oversized digit runs and null bytes: every `None`-contracted parser returned `None`,
every raise-contracted one raised only `ProtocolError`. Exempt: `parse_plot_adhoc`,
`parse_plot_def`, `decode_plot_sample` have no formatter, being firmware-to-host only.

### Class 12 - healthy-while-dead. 4 workers plus PRAGMA readback, 1 finding.

- Store writer - complies by construction: every exception inside `_writer` is caught, failed
  callers are counted in `write_errors`, and the loop can only exit on its sentinel. Thin
  spot: nothing reads `_writer_task.done()` back into `/status`, so the surface depends on
  that construction holding rather than on a check.
- Serial reader thread - complies, driven live: `_on_disconnect` flips `connected` from the
  `finally` of the read loop, so every ending flips it. `DELETE /ports/sim` removed the port
  from `/status.ports` outright, and a re-`POST` read `connected: false` until the thread
  actually connected.
- Sim serving thread - complies; this is the class's worst historical bug and both halves of
  the fix (per-client guard, and `conn.close()` inside its own `try`) are present.
- WS feed - complies; broadcast is inline in the writer task, so it has no separate liveness
  to lie about, and a dead subscriber closes only its own connection.
- `journal_mode` read back and warned on (`store.py:318`); `auto_vacuum` read back by
  `test_created_capture_has_incremental_autovacuum`. `synchronous` and `foreign_keys` are set
  and never read back - no known silent-refusal mode, filed as a gap, not a defect.

**M4 below is this class's live finding**, in the web UI rather than the daemon.

### Class 13 - Windows file-sharing and encoding. 3 sites, 0 findings.

`grep -rn "os.replace\|os.rename" host/mcuscope` returned **3**: `config.py:274` (docstring),
`config.py:287` (the sanctioned `replace_atomic` body), `update_check.py:247` (a comment; the
real write at :170 calls `replace_atomic`). Every replace in the tree goes through the helper.
Reads of user-editable files: `config.py:125` and `config.py:268` both `utf-8-sig`
(BOM-tolerant, as required). Redirected-output probe passed for `mcu devices`, `mcu status`,
`mcu log export`; the non-ASCII-description case is Windows-specific and stays with that leg.

### Class 14 - platform-gated fixes. 15 real gates, 0 findings.

The raw grep over-reports: `lockfile.py:144`, `cli.py:1296` and `server.py:350/351/368/369/378`
match `os.name` inside `gethostname`/`hostname` through the unescaped `.`, and contain no
platform branch. The 15 real gates each name the other platform's enforcement: `msvcrt.locking`
against `fcntl.flock`; `SO_EXCLUSIVEADDRUSE` against POSIX bind semantics; `OpenProcess`
against `os.kill(pid, 0)`; `DETACHED_PROCESS` against `start_new_session`; COM-name
normalisation against `os.path.exists`. Four gate a condition confined to one OS with no
equivalent failure to guard (the `_stdio.py` null-std-stream repair, the Linux phantom-`ttyS*`
filter, `--pty`, the `/dev/serial/by-id` map, whose cross-platform mechanism is
`serial_number`).

### Class 15 - shipped artifact vs stand-in. 6 deliverables, 1 finding.

- `mcuscoped`, `mcu` - covered twice: `test_console_scripts_run` on the installed wrapper, and
  CI's `build` job installing the real wheel into a fresh venv and running both.
- Wheel and sdist contents, web UI and vendored assets - covered by CI's package-data check,
  which walks the source tree rather than a hard-coded list, so new assets are automatic.
- `tools/mcu_sim.py` shim - covered by `test_sim_pty.py` spawning it as a real subprocess.
- Exports - driven through the REST/CLI surface; no packaging-dependent asset, so the class
  does not bite.
- **R3 (not fixed). `mcu-sim` is never run from the built wheel.** CI's wheel step
  (`ci.yml:207-212`) runs `mcuscoped --version` and `mcu --version` and omits `mcu-sim`.
  `test_scaffold.py` covers it, but always against the *editable* install. A regression in its
  entry point, or a packaging change dropping it from `[project.scripts]`, ships undetected.
  One line of CI; carried, as this round did not touch CI.

### Class 16 - one bad item ends the loop. 35 loops, 4 findings.

Complies (both questions asked of each): `cached_comports`; `_reader` and its inner byte loop;
`_on_bytes`; `_consume`; `_store_rx_batch` submit and settle passes; `stop_all`; the sim accept
loop and per-client loop; `serve_pty`; the store `_writer`, `_insert_individually`;
`_port_conflict`; the update poll and its `aiter_bytes`; `config._from_dict`'s ports loop; the
WS `pump`; `_do_wait` and `_do_assert`; `_enumerate_devices`. Exempt (no external input): the
sim's internal timer loops, `_fail_queued`, `_retention_loop`, `iter_plot_export`, the signal
release loop, `pidfile.claim`'s bounded CAS, `lockfile.acquire`'s deadline-bounded retry, the
CSV export loops, and the single-fetch display loops in cli.py.

Findings: `sim.py:510` `_process_incoming` (no per-line guard, so a non-`ProtocolError`
abandons the rest of the buffered chunk and ends the session); `cli.py:651/655` `_follow_ws`
(one malformed frame ends `mcu tail -f`); `cli.py:1090` `_dump_follow` (no guard at all, so a
transient httpx error crashes `mcu can dump -f` with a traceback, also class 9);
`server.py:1786` `_by_id_map` (whole-loop `try`, so a mid-iteration failure silently drops the
remaining entries, minor and best-effort).

### Class 17 - reported value is the request, not the result. 34 fields, 1 finding.

All comply or are exempt, including the three previously-fixed bits (`db_max_bytes` now reads
the enforcement variable, `journal_mode` reads its result set, `lines_rx` increments after
receipt with loss tracked separately in `rx_dropped`/`write_errors`). `GET /health` does not
exist as a route; the sweep's field list came from `/status` and `/config`.

**R4 (not fixed). `ports[].baud` echoes the request.** `serial_link.py:987` reports
`self.baud`, the constructor argument handed to `serial_for_url`, never the opened port's
actual `.baudrate`. This is the class's exact shape. Not fixed this round only because it
needs a real native port to verify against (a `socket://` transport has no baud rate to read
back), so it belongs to the bench session; carried with that note.

### Class 18 - unmapped exception types. 34 call sites, 0 findings.

Every httpx, websockets, json, urllib and regex site was diffed against its in-file siblings.
The three historical bits are fixed and consistent: `httpx.InvalidURL` handled explicitly at
all four httpx sites, `urlsplit().port`'s `ValueError` at its sole site, `regex.error` at all
three compile sites. `store.py:243`'s `regex.compile` has no guard of its own and relies on
its callers pre-validating; it has no in-file sibling, so it is not a finding under the
literal rule, but it is the one site whose safety is an argument rather than a clause.

### Class 19 - two engines validating one thing. 27 mirrors, 6 findings.

Complies, diffed clause by clause: `parseEnumLabels` (last round's fix, cap still present),
`parsePlotAdhoc`, `parseChannelSpec`, `parseBitLanes`, `parsePlotDef`, `decodePlotSample`,
`parsePlotValue`, `lineTick`'s marker and `!can`/`!p` branches, `cli.py`'s follow matcher
(same engine *and* the same 0.25 s timeout), and `can.js`'s id range check (last round's fix).

Findings: **M1 below** (terminal.js, the highest-severity of the round); `state.js`'s `!ps`
branch (**M5**); `can.js` accepts only lowercase `0x` where `parse_hex_int` also takes `0X`
(narrow, unreachable from compliant firmware); and four sim-against-firmware divergences -
`parse_can_flags` treating `-` as a no-flags sentinel that firmware rejects, the sim's I2C
address having no 7-bit bound where firmware answers `badarg`, `_can_filter` missing
firmware's optional third `[flags]` token, and `_can_filter` accepting a >32-bit id/mask.
A fifth is the firmware being the looser side: `mon_parse_dec_u32` has no length restriction,
so real firmware accepts an RTR dlc that SPEC and the host both reject.

The sim divergences are not fixed this round: the sim is the reference the host is tested
against, so changing it moves the target for every test, and the RTR one needs a SPEC ruling
on which side is wrong. Carried as the next round's first item, with the firmware question
flagged for the owner.

### Class 20 - non-sargable bound on a hot query. 35 statements, 3 findings, 1 fixed.

Explained against a two-row capture with **no `sqlite_stat1`** and two ports, which is the
shipped condition. All three previously-fixed instances are confirmed still fixed
(`list_sessions`' COALESCE, `/can/frames`' CROSS JOIN, `/plot/channels`' JOIN-not-IN). 28
comply; 4 are exempt (`active_session`'s `IS NULL` over a human-scale table, the REGEXP scan
which no index can serve and which is already offloaded, and the two `/plot/channels`
whole-table aggregates, where the filter was checked and does not make it worse).

**R5 (fixed). `GET /lines?port=` with no `chan` planned as `SCAN lines`, on the event loop.**
`lines` had indexes on `(ts)` and `(chan, id)` but nothing on `port`, and `query_lines_safe`
offloads only a `match`-bearing query, so this ran inline. Measured at 1M rows, no ANALYZE:

| port | plan | time |
|------|------|------|
| busy (1M rows) | `SCAN lines` | 0.3 ms |
| quiet (3 rows) | `SCAN lines` | 80 ms |
| absent | `SCAN lines` | 81 ms |
| any, with `idx_lines_port_id` | `SEARCH lines USING INDEX idx_lines_port_id` | under 0.3 ms |

The busy number is why this survived: the `LIMIT` fills from the newest rows and the cost is
invisible. It is paid in full on a *quiet* port, which is the normal case rather than the
exotic one - a board silent while idle still gets polled, and this bench's board is exactly
that. Linear in table size, so 800 ms at 10M rows, on the loop. Insert cost of the third index
measured at 200k rows: 1.31 s against 1.43 s, i.e. within noise.

Two remaining, not fixed: `GET /lines?last_ms=` with no port or chan also plans as a scan
(`idx_lines_ts` cannot serve `ORDER BY id DESC`), masked in practice because `ts` tracks `id`
monotonically so the LIMIT fills immediately; and `count_lines` with a bare `port` filter,
reached from `POST /assert` retrospective mode, which has the same root cause as R5 but runs
off the loop. Both carried with their measurements.

## 2026-08-01 - Round close-out

The round that opened with the registry leg is **closed against the runbook's exit criterion**.
Sections below, newest first, hold the evidence for each leg.

| Exit criterion | Status |
|----------------|--------|
| Every registry sweep executed, verdict list filed | Executed, but only classes 21 and 22 have their verdict lists filed here. The classes 1-20 lists lived only in the session that ran the registry leg and were never written to this file, so that evidence is unrecoverable. |
| Every finding closed class-wide, each new class in the registry with a sweep | Yes. Classes 21 and 22 added, 16, 19 and 20 extended. |
| Measurement checklist on both platforms | Windows in full, including a bench session on real hardware. Linux against the simulator; see the gaps below. |
| Coverage reviewed, every uncovered shipped branch dispositioned | Yes. The four left open were driven; 77.6% total. |
| Every new regression test revert-verified | Yes, including two that were not discriminating until it was done. |
| Fix-diff leg ran on the round's own diff | Yes, twice: once on the Windows leg's diff, once on this session's. |

Totals: 10 fixes landed this session, closing the 8 findings new to it (N1-N8) plus M1 and M2,
the two the Windows leg (5 findings, M1-M5) left open. Also: one class-20 site swept and ruled
compliant, one refuted with a probe (the capture lock), one refuted change the fix-diff leg was
about to make, and two new registry classes. Suite 539 -> 551.

**What the round did not cover**, for whoever opens the next one:

- Linux has no bench board attached and the web UI was not driven in a browser there. On
  Windows that browser check is what found M5, which no automated probe had.
- The module leg read `lockfile.py`, `can.js`, `cmdbar.js`, `settings.js`, `digital.js` and
  `plots.js`'s decode half. Not read this round: `terminal.js`, `api.js`, `statusbar.js`,
  `state.js`, `plots.js`'s rendering half, `_stdio.py`, `pidfile.py`, `update_check.py`.
- `can stat`'s `err`/`state` semantics remain unpinned in SPEC 5 (Windows leg, bench session).
- The classes 1-20 verdict lists were never filed (see the table above); the next round's
  registry leg re-runs them and files the lists here.

The **campaign** stays open: a round ends when the exit criterion is met, but the campaign ends
only when a full round produces no new defect class, and this one produced two.

One section per leg per platform. The runbook is `docs/REVIEW.md`; this file is the evidence
it requires ("the sweep verdict lists, the measurement and ruled-out log, the coverage
disposition list, the revert-verification list, and the fix-diff report").

## 2026-08-01 - Measurement leg, Linux

The Windows leg's checklist, run on Linux through the **installed console scripts** in
`host/.venv/bin` (class 15), against `mcuscoped --sim`. No bench board attached this run
(`/dev/ttyACM0` absent), so the real-hardware items stay with the Windows leg's bench session.

One finding, a SPEC self-contradiction. Everything else confirmed working.

**N7. SPEC 4 promised one JSON object from every command, and two commands emit JSONL.**
"With `--json`, every command prints exactly one JSON object" sits two lines below a table row
saying `mcu log export` dumps **JSONL**. `mcu tail` does the same, deliberately - cli.py's own
comment routes the truncation note to stderr "so a JSONL stdout stream stays parseable" - and
it has to: `tail -f --json` is an unbounded stream no single object could hold. The
implementation is right and the sentence was wrong, so SPEC now carries the exemption. Left
unstated, the next round reads the sweep as failing and "fixes" `tail -f` into something no
follower can parse.

**Confirmed working**

- Daemon lifecycle: start via `mcuscoped --sim`, `mcu status` exit 0, `daemon stop` exit 0 and
  the process gone, `status` afterwards exit 3 with no traceback.
- A second daemon on the same port and db exited 1 naming the holder by pid and host, and the
  first kept serving. The capture lock, not the port, is what caught it.
- Class 9 exit codes: `--version`/`--help` 0, unreachable daemon 3, unparseable URL 3,
  out-of-range port 3, bad regex 1, unknown session 1, empty command 1. No traceback anywhere.
- Class 10: every subcommand's stdout parsed, with the two JSONL exemptions above; the
  truncation note goes to stderr in both.
- The 5000-digit session ref fixed this round: `/sessions/<ref>/export` now 400 (was 500 with a
  traceback), `/lines?session=<ref>` 200 with an empty range, which is the documented answer for
  an unknown ref. `POST /cmd {cmd:""}` 400, holding the Windows leg's M3 fix on this platform.
- Daemon stderr empty across the whole run.

**Endpoint latency, sim capture (n=5, median):** `/status` 1.09 ms, `/ports` 0.73,
`/devices` 1.33, `/config` 0.92, `/sessions` 0.76, `/lines?limit=100` 1.37,
`/lines?limit=1000` 6.11, `/can/frames?limit=200` 6.80, `/plot/channels` 1.36,
`/plot/channels?port=sim` 1.66.

**Probe error worth keeping.** The first pass swept class 10 with subcommand names the CLI does
not have (`config`, `sessions`, `can --limit`). All three "passed" the one-document check,
because an unknown command emits a JSON error document on stdout and its usage text on stderr -
which is a real contract, just not the one being swept. A sweep that invents its own inputs
measures the error path and reports it as coverage.

## 2026-08-01 - Test-quality and fix-diff legs, Linux

**Class 21 sweep (wall-clock granularity), verdict list.** `grep -rn "time.time()" tests/`, 16
sites: one violation, already fixed by the Windows leg (`test_purge_before_ts...`, now deriving
its cut from the stored rows). Every other site is exempt, with the reason: nine pass
`time.time()` *as a row's ts* rather than as a boundary (test_webui 120, test_plot 130, test_e2e
271/456, test_reconnect 264/330, test_hardening 443/462); four compare with a margin far above
the 15.625 ms granularity (test_update_check 92 at 30 s, 113 and 230 at a whole check interval,
test_regressions 987 at 1 s); one is the fixed test's own `_after` helper.

Audit note (round close, same day): the grep this list cites returns 36 lines, not 16; the list
counted only the sites it judged worth a verdict and did not say so. The unlisted lines were
re-checked and all are exempt: comments, `ts=time.time()` row timestamps (test_sessions,
test_hardening, test_assert), hour-scale synthesized margins (test_hardening 573), and the spin
helper itself (test_assert 43). The conclusion stands; the list as first filed violated the
sweep discipline by understating its own command's output.

**Test quality.** Every fix this round was revert-verified as it landed. The four coverage-leg
tests had no fix to revert, so they were checked the equivalent way: the source was mutated
(port clause deleted, forced-trim branch disabled, `_prune` call removed, `_carried` trim
removed) and all four failed. Platform-inert tests are down to one on Linux (Windows COM
enumeration), and the Windows leg ran the suite on the machine where it is live.

**Fix-diff, on this round's own diff.** Two findings, one of them a refutation of a change this
leg was about to make.

**N6 (fixed). The new plan tests asserted the absence of a version-specific string.**
`"SCAN l" not in plan` passes on any SQLite that words its output differently - it read
`SCAN TABLE lines AS l` before 3.36 - so the test would go quietly green on the exact build
where it needs to speak up. This is the class 21 shape one level up: an assertion phrased
against one implementation's vocabulary rather than against the invariant. Both tests now
assert positively (the outer loop names `cf`; `lines` is reached by primary-key probe) and
were revert-verified again in that form.

**Refuted: making the plot-definition seed first-load-only.** `seedPlotDefs` runs on every
backfill, including reconnects, while its own comment says it covers the first load - so
gating it on an empty definition cache looks free. It is not: a port appearing *mid-session*
has no cached definition, the gate would skip the seed because some *other* port's definition
is cached, and its typed samples would not decode until the next `!pd` rebroadcast. That is
M5 again in a narrower case, and it is the "inverse of the fix" pattern the registry already
names. Left unconditional; the cost is one bounded query per backfill, which is the price of
being correct across ports. Recorded because the next reader will have the same idea.

Also checked across the round's diff and found clean: `payload_s not in "0123456789"` cannot
match the empty string because the length test short-circuits first; `intField` returning NaN
reaches callers that already guard NaN (`chosenBaud`, the timeout fallback, every settings
save); `is_decimal_token`'s 20-digit bound cannot reject a legitimate session id or sim
argument; and no test or document uses `port = 0` from a config file, which the new 1..65535
bound would now reject.

## 2026-08-01 - Coverage leg close-out, Linux

The four shipped-but-untested paths the coverage leg listed and left open, now driven. Total
coverage 76.9% -> 77.6%, suite 546 -> 550.

| path | verdict |
|------|---------|
| `/can/frames` `port`, `since_id`, `last_ms`, `id_from`/`id_to`, truncation flag | correct; pinned, including that filters intersect rather than replace |
| token fail-table eviction at `TOKEN_FAIL_TABLE_MAX` | correct; bounded at 3x the cap in distinct addresses, oldest-first, lockout still bites |
| `_carried` eviction at `CARRIED_MAX` | correct; oldest alias evicted, survivors keep their own counters |
| forced retention trim inside protected sessions | correct; asserted on the warning, not just on the row count, so an ordinary trim cannot satisfy it |

**No defect in any of the four.** That is worth recording rather than glossing: the leg's
output is a verdict per branch, and `purge --before` (which did hold one) is the exception that
justifies driving the rest. An untested branch is a candidate, not a finding.

The reusable lesson is in how two of them had to be reached. The table bound needs a thousand
distinct client addresses and the forced trim needs a protected session bigger than the cap -
neither is expressible through a request. Both were driven at the unit (`_TokenGuard`,
`Store._sweep_size_async`). A branch whose precondition the HTTP layer cannot express is exactly
the branch that stays uncovered, so the leg should reach past that layer by default.

## 2026-08-01 - Module leg, Linux

Modules chosen by *least prior attention* rather than by size or suspicion: `lockfile.py`
(untouched in the last 20 commits), `webui/can.js` (never touched, and outside the Python
coverage report), with `protocol.py`'s CAN section pulled in by what can.js turned up.

Four findings, one refutation. The leg earned its place in the runbook again: it produced a
**new registry class (22)** rather than another instance of a known one, and that class then
explained three further sites in modules this leg never opened.

**N1. `webui/can.js` accepted CAN ids the daemon rejects (class 19).** `parseCanEvent` mirrors
`protocol.parse_can_event` by hand and had no id range check, so `!can 100 - 800 DEAD` produced
a row in the CAN sidebar while the daemon had stored the line as a generic event with no
`can_frames` row. The sidebar and `GET /can/frames` / `mcu can` disagreed about the same line.
The hex token was also uncapped where `parse_hex_int` stops at 16 digits.

**N2. The RTR dlc digit, in both directions (new class 22).** `parse_can_event` gates it on
`payload_s.isdecimal()`, ten lines below its own comment explaining why that is the wrong test
for the tick token in the same function. `'٣'.isdecimal()` is True and `int('٣')` is 3, so
`!can 1 r 100 ٣` decoded into a `can_frames` row. `parse_can_tx_args` has the identical line on
the outgoing path, where the token comes from user text.

**N3. The simulator's `_parse_dec` (class 22).** Same predicate, so the sim accepted arguments
no firmware would - and the sim is the reference the host is tested against.

**N4. `store.resolve_session` (class 22), the one with real severity.** `isdecimal()` fails both
ways at once here:

- A session *named* `٣` resolved to session **id 3**, which is precisely the wrong-session bug
  the branch already carries a comment about having fixed.
- It bounds no length, so a 5000-digit ref reached `int()` and raised past CPython's 4300-digit
  limit. Confirmed end to end: `GET /sessions/{ref}/export` and `GET /lines?session=<ref>`
  both answered **500 with a full traceback** in the daemon log. `POST /assert` answered 400 for
  the same ref, because its body field is length-bounded by pydantic - so the defect was hidden
  behind whichever endpoint anyone happened to test.

**Refuted: the capture lock is not defeated by an unnormalised path.** `CaptureLock` derives its
lock file from `db_path + ".lock"` with no `abspath`/`realpath`, and `resolve_db_path` only
expands `~`, which reads like two daemons naming one capture differently could both acquire.
Probed rather than argued: `data/cap.db` against `./data/cap.db`, against `data/../data/cap.db`,
and against a symlinked parent - all three blocked correctly. The kernel resolves the path at
`open()`, so the lock is on the inode and the *name* never has to be canonical. Windows
case-insensitivity resolves the same way. Only distinct inodes for one capture (a hard-linked
db file) would escape, which nothing produces by accident.

Registry: class 22 filed with its sweep, class 19 gains the hand-written-mirror case. All four
fixes revert-verified individually. Suite 539 -> 545.

### Second pass: cmdbar.js, settings.js, digital.js, plots.js

**N8. `plots.js parseEnumLabels` omitted the daemon's digit cap (class 19).** The third
hand-written mirror to lose a *bound* while faithfully copying the character-set check beside
it. `_parse_enum_labels` rejects a value past `MAX_DECIMAL_DIGITS` (CPython's `int()` raises
past it), which drops the whole `!pd`; the browser accepted it, built a definition the daemon
does not have, and charted a typed stream `/plot/series` had never decoded.

The test for it was **non-discriminating on the first attempt** and revert-verification caught
that: it asserted `charts.has("s7") === false`, but an enum channel renders as a *digital lane*,
so the assertion held either way. Now asserted on `digitalLanes`, and failing with the cap
removed. Filed as a fifth shape in the test-quality leg.

**Swept, no finding.** `cmdbar.js` (the gen-guard supersedes in-flight commands correctly, the
history walk is sound, all text goes through `textContent`); `settings.js` (duplicate port
aliases are rejected by the daemon's `PUT /config/ports`, so the UI cannot create them; the
MB round-trip only ever perturbs a cap that was not a whole number of MB); `digital.js`.

**Exempt with a reason: `digital.js`'s lane cap ignores new lanes where `can.js` evicts.** The
two siblings chose opposite policies, which reads like an inconsistency. It is not: a CAN row is
a table row, while a digital lane owns a `<canvas>`, so evicting under the rotating-name stream
the cap exists for would create and destroy canvases at line rate - worse than freezing. The
count already shows "(limit 64 reached)", so the surface is not silent.

### Class 22 sweep, run immediately after filing it

`grep -rn "isdigit()\|isdecimal()\|isalnum()" host/mcuscope` now returns only `is_decimal_token`
itself and comments citing it. Every `int()` on a wire token is gated by an explicit length
check with its reasoning written down (`protocol.py` lines 288, 391, 563, 615, 711, 787), and
`get_session` takes a FastAPI-typed `int` path parameter.

The sweep then found a fifth site, in `config.py`, which the module leg had not opened:

**N5. Every config integer was read with bare `int()`.** The same defect as the `check =
"false"` one that `_as_bool` was written for, from the other side, sitting four lines below it
in the same function. Measured against a hand-edited file:

| written | was read as | now |
|---------|-------------|-----|
| `port = true` | **1** (a bool is an int in Python) | fails the load, naming the key |
| `port = 8765.7` | 8765, silently | fails the load |
| `port = "9000"` | 9000 | fails the load (TOML has real types) |
| `port = 99999999` | 99999999, then a bind failure naming neither file nor key | warns, keeps 8765 |
| `retention_days = 0` | clamped to 1 day, deleting nearly everything | warns, keeps the default |

Wrong *type* fails the load, because that is already the contract `port = "abc"` had through the
`ConfigError` wrapper and a test pins it; out of range warns and falls back, because there a
default is a sane answer. The distinction matters most where the value governs deletion:
clamping `retention_days = 0` up to its floor of 1 destroys almost the whole capture, while
falling back to the default keeps it. That asymmetry is now in the class 22 sweep.

The lesson worth keeping: a coercion helper written for one type is a signal, not a fix.
`_as_bool` had been sitting beside four unguarded `int()` calls in the same function since it
landed, and no round had asked what else in that function coerced.

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
