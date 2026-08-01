# Review sweep runbook

How to run a review round so one round finds what previously took several.
Derived from the eight rounds 99eab7c, 0c676ec, e563a94, 8c4138a, 187a0e4, 77e5a69, 4d7b4ef, 6e3d1ed; each rule cites the one finding that justifies it.

The core failure of past rounds: a defect class confirmed at one site was fixed at that site only, and the next round found the same class elsewhere.
In the last two fix rounds, half the findings were repeat instances of an already-confirmed class (77e5a69: 3 of 6; 4d7b4ef: 6 of 12).

## Prioritised recommendations

Ranked by expected findings per unit of effort.

1. **Run the defect-class registry sweeps below before any fresh reading.**
   Cheap and mechanical: most sweeps are a grep plus a per-site verdict.
   Evidence: 9 of the 18 findings in the last two fix rounds were repeat-class instances a sweep would have caught earlier.
2. **Close every new finding class-wide, not site-wide.**
   A finding is open until every call site of the same primitive is ruled in or out explicitly, and the class is added to the registry.
   Evidence: replace_atomic() swept config saves, the pid record and the update cache in one commit (77e5a69) and never recurred; single-site fixes (executor, newline, counters) recurred for up to four rounds.
3. **Run the measurement leg: drive the real stack before reading code.**
   Moderate effort, highest severity yield.
   Evidence: the sim brick (`can tx 7FF`), the 0.70 s /devices freeze, the BOM config failure, the phantom ttyS* ports and the running-session export 400 all came from execution, not reading.
   77e5a69 also ruled out four suspected issues by probing.
4. **Run a coverage-gap pass ignoring the 55% floor.**
   Cheap: one coverage run, then read the uncovered branches in shipped paths as a candidate defect list.
   Evidence: exporting a running session answered 400 on every platform because every test stopped the session first (77e5a69), and the console scripts had zero executions until test_scaffold.py (187a0e4).
   `fail_under = 55` (host/pyproject.toml) surfaces neither.
5. **Re-review each round's own fix diff before closing the round.**
   Moderate effort: read the diff once per platform, asking what each hunk changes on the OS it was not written for.
   Evidence: 2 of 12 findings in 4d7b4ef were Linux regressions from the Windows rounds (the Windows-only port probe, the backfill staging path).
6. **Audit test quality as a review target.**
   Cheap per test: revert the fix, confirm the test fails; list tests inert on the current platform.
   Evidence: one test asserted the DNS-rebinding attack backwards (99eab7c); two tests were tautological on Linux (4d7b4ef); three tests remain Windows-only and inert on Linux today.
7. **Run the healthy-while-dead probe checklist.**
   Expensive (needs a live stack per probe) but finds the worst class.
   Evidence: four shipped defects reported healthy while producing nothing (registry class 12).

## Defect-class registry

Each entry: the invariant, where it bit, and the sweep that finds new instances.
A sweep's output is a list of sites each marked "violates", "complies", or "exempt because <reason>"; an unlisted site means the sweep was not run.
When a round confirms a new class, add it here with its sweep before the round closes.

### 1. Blocking work on the event loop or default executor
- Invariant: no SQLite, regex, filesystem or device-enumeration work runs on the event loop; the default executor is reserved, because detach and shutdown join the serial reader through it.
- Bit: /can/frames (99eab7c), plot_points retention scan blocking the loop 70 s (99eab7c), /plot/series and /plot/channels (0c676ec), GET /devices (77e5a69), plot export (4d7b4ef).
- Sweep: for every `async def` endpoint in server.py, trace each store/os/serial call; blocking work must go through match_executor or a named pool.
  - `grep -n "run_in_executor(None" host/mcuscope` must return only the reader-thread join in `SerialPort.stop`, which is the reserved use this invariant describes.
  - New endpoints and new store queries are in scope by default, not on suspicion.

### 2. Text writes without explicit newline
- Invariant: every text-mode file write passes `newline=`, or Windows rewrites `\n` as CRLF and byte counts stop matching.
- Bit: JSONL export (e563a94), config write-back, the one write then missing it (187a0e4).
- Sweep: `grep -rn "open(" host/mcuscope | grep -v 'newline\|"rb"\|os.open'` plus every `write_text(`; rule each hit in or out.

### 3. Listening sockets without Windows exclusivity
- Invariant: every listener sets SO_EXCLUSIVEADDRUSE or is probed with one before bind; SO_REUSEADDR on Windows binds over a live listener.
- Bit: sim listener (187a0e4), daemon bind, where a second daemon printed its URL and was never reached (77e5a69).
- Sweep: `grep -rn "socket.socket\|\.bind(" host/mcuscope`; each listener must reference SO_EXCLUSIVEADDRUSE or the probe, on every platform (see class 14).

### 4. Per-attach state lost on reattach
- Invariant: counters describing the port's lifetime live above the object that reconnect recreates.
- Bit: line/drop counters (0c676ec), lines_tx (4d7b4ef).
- Sweep: diff the attributes SerialPort.__init__ zeroes against everything /status reports; each is either per-connection by design or carried across reattach with a test.

### 5. argv hoisting in cli.main()
- Invariant: hoisting resolves the subcommand and every value position before moving a token, and any resolution failure degrades to no hoisting.
- Bit: `--limit`'s value stolen as `-p`'s (99eab7c); a leading global option broke subcommand resolution and disabled the value guard (187a0e4).
- Sweep: any change to global options, aliases or subcommands reruns the hoist tests across {option position} x {subcommand} x {value-taking option}.

### 6. Non-finite values reaching chart arrays
- Invariant: nothing pushes NaN or Infinity into a uPlot data array; one such value blanks the series.
- Bit: a single Infinity sample (99eab7c); a large scale factor carrying a finite sample to Infinity (187a0e4).
- Sweep: list every producer writing into plot/digital data arrays; each must gate on Number.isFinite at its own boundary (parse and scale paths in plots.js are the two known).

### 7. Pid record lifecycle
- Invariant: a pid record is deleted or overwritten only by the daemon it names, or when provably stale.
- Bit: four rounds in a row, the most-repeated class.
  - kept-on-failure and host:port keying (0c676ec)
  - pid reuse, atomic claim, release-on-failed-startup (8c4138a)
  - unwritable record breaking the exit contract (77e5a69)
  - a failing second daemon deleting the running one's record, and zombie stop grace (4d7b4ef)
- Sweep: a state matrix test - {no record, stale, live other process, live parent, our own} x {claim, release, stop, failed startup}; every cell has an asserted outcome.

### 8. Thread teardown on detach and shutdown
- Invariant: a reader thread always releases its handle and never touches a closed loop, in every ordering of detach, join timeout and loop close.
- Bit: handle held after a join timeout blocked Windows re-attach (187a0e4); raise on closed loop and a leaked handle (4d7b4ef).
- Sweep: per thread, enumerate outlive scenarios (join timeout, loop closed, exception mid-read) and assert handle close plus callback fate in each.

### 9. CLI exit-code contract (SPEC 4)
- Invariant: every path out of `mcu` maps to 0/1/2/3; a traceback reaching the user is a defect.
- Bit: `assert` exiting 2 (99eab7c); EPIPE and JSON errors as tracebacks (99eab7c, 0c676ec); `daemon start` traceback after the daemon was already spawned (77e5a69).
- Sweep: enumerate `raise`, `except` and `Exit` sites in cli.py; each exception type reaching main() has a mapping, and each failure mode is driven through the installed console script, not `python -m`.

### 10. --json stdout purity
- Invariant: with `--json`, stdout carries exactly one JSON document; prompts, warnings and repair notices go to stderr.
- Bit: prompts and `-o` paths (0c676ec); stream-repair warnings on stdout (4d7b4ef).
- Sweep: run every subcommand with `--json` and assert `json.loads(stdout)`; grep new print/write sites for the stream they target.

### 11. Codec symmetry in protocol.py and the sim
- Invariant: format_x and parse_x accept the same domain, and parse returns None where documented instead of raising.
- Bit: format_can_event accepting ids parse_can_event rejects (99eab7c); an out-of-range echo id raising inside sim poll_events and killing the listener (187a0e4).
- Sweep: property-test `parse(format(x))` over the full id/data domain; fuzz parse with malformed input asserting None, never an exception.

### 12. Healthy-while-dead surfaces
- Invariant: when a worker dies or a setting fails to apply, the surface that reports health must change state.
- Bit: the highest-severity shape in the whole series.
  - sim listener open after its serving thread died, so the port read healthy (187a0e4)
  - UI showed live while queueing rows into an undrained staging area (4d7b4ef)
  - second daemon printed its URL and was never reached (77e5a69)
  - auto_vacuum silently stayed 0, so every incremental_vacuum was a no-op (99eab7c)
  - Windows CI reported green while its jobs had never run (setup-uv pin, context of e563a94)
- Sweep: a probe checklist, not a grep.
  - Kill each worker (store writer, reader thread, sim serving thread, WS feed) on a live stack and assert the health surface reflects it.
  - Read back every PRAGMA and config setting after applying it.

### 13. Windows file-sharing and encoding semantics
- Invariant: replace/rename goes through config.replace_atomic(); user-editable text is read tolerating a BOM; output survives a non-UTF-8 or redirected console.
- Bit: os.replace losing a settings save to a transient antivirus handle, BOM in config.toml (77e5a69); `mcu devices` dying redirected on a non-ASCII port description (187a0e4).
- Sweep: `grep -rn "os.replace\|os.rename" host/mcuscope` outside replace_atomic; check `encoding=` at every read of user-editable files; run output-producing commands redirected.

### 14. Platform-gated fixes
- Invariant: a platform gate may gate the mechanism, never the invariant; for each gate, name what enforces the same guarantee, in the same order, on the other OS.
- Bit: the port probe shipped Windows-only, so on POSIX a failing second daemon still clobbered the pid record before uvicorn reported EADDRINUSE (77e5a69, found 4d7b4ef).
- Sweep: `grep -rn "sys.platform\|os.name" host/mcuscope`; for each gate write one line naming the other platform's enforcement.

### 15. Shipped artifact vs stand-in
- Invariant: a test must exercise the artifact the user runs, not a stand-in for it.
- Bit: console scripts never executed while the suite drove `python -m mcuscope.cli`, the origin of every Windows startup bug (187a0e4).
  - Also: web UI assets unverified in the wheel (0c676ec), and Windows CI jobs that never ran (context of e563a94).
- Sweep: enumerate deliverables - the three console scripts, wheel contents, web UI and vendored assets, exports, the tools/mcu_sim.py shim - and name the test or CI job that exercises each in shipped form.

### 16. One bad item ends the loop
- Invariant: a loop over many items charges a failure to the item, never to the loop; the loop keeps going and the drop is counted.
- Bit: four instances in one round (2026-08-01).
  - `_store_rx_batch` was a list comprehension, so one unparseable line silently discarded up to `RX_BATCH_MAX` = 1000 following lines
  - the sim accept loop broke on any `OSError`, so one transient EMFILE left the listener bound with no thread behind it
  - `serve_pty` had no per-session guard at all, so one raise ended the process
  - `with conn:` let an `OSError` from the implicit close escape the per-client guard
- Sweep: for every loop that processes external input, ask what one bad item does. The failure must be caught inside the loop body, counted, and reported once per episode rather than per item.
- The mirror image, found by the fix-diff leg in the guard that fixed the third instance: a guard that keeps looping must still recognise the errors that are not per-item. `serve_pty` retried a dead pty master at 10 Hz forever, printing the same line, while its TCP sibling already broke on the fd-dead errnos. Ask both questions of every such guard.

### 17. Reported value is the request, not the result
- Invariant: a health surface reports what happened, not what was asked for.
- Bit: three instances in one round, plus the original `auto_vacuum` (99eab7c).
  - `lines_rx` incremented before the write was submitted, so a full disk showed lines arriving and nothing stored
  - `db_max_bytes` echoed the configured cap rather than the cap in force
  - `journal_mode=WAL` discarded its result, and this PRAGMA reports refusal in its result set rather than by raising
- Sweep: for every reported field and every applied setting, find where the value comes from. A value read back from the thing itself passes; a value echoed from the request does not.

### 18. Unmapped exception types at a third-party boundary
- Invariant: every exception a third-party call can raise is mapped, and sibling call sites map the same set.
- Bit: `httpx.InvalidURL` is not an `HTTPError`, so it escaped two handlers whose sibling `Client.request` had already been fixed for it; `urlsplit().port` raises `ValueError`; `re.error` reached the user as a traceback.
- Sweep: for each httpx, websockets, json and urllib call site, diff its `except` tuple against the other call sites of the same library in the same file. A tuple that is a strict subset of its sibling's is the finding.
- Not the fix: a blanket `except Exception` in the CLI dispatcher. `_stdio.console_entry` is already that backstop and returns 1, so a blanket clause buys no exit-code correctness, and it replaces the crash log a genuine bug needs with an indistinguishable "error: something".

### 19. Two engines validating one thing
- Invariant: a check performed in two places uses the same implementation, or the looser side is not a check at all.
- Bit: `mcu tail -f --match` compiled with stdlib `re` while the daemon compiled with `regex`, so a pattern the daemon accepted crashed the client after it had already printed a matched line.
- And then the fix for it copied the engine without the guard: the client had no `timeout=`, so `(a|a)+$` hung the follow with no error, no exit code and no working Ctrl-C. Adopting the same library is not adopting the same check.
- And the web UI's `!can` decoder, which mirrors `protocol.parse_can_event` by hand but omitted its id range check, so a frame with an id past 0x7FF appeared in the CAN sidebar while the daemon had kept the line as a generic event with no `can_frames` row. The table and `GET /can/frames` disagreed about the same line. The test file asserting the mirror already said "the browser and the daemon must agree"; its list of malformed inputs was simply missing the case.
- Sweep: list every validation duplicated between client and daemon, or between host and firmware, and name the single implementation both use. Where the two cannot share code, list what the daemon's version does beyond calling the library, and check each item separately.
  - For a hand-written mirror, diff it against the original clause by clause. A mirror is a copy that stops being one silently, so an existing "these must agree" test is not evidence that they do.

### 20. Non-sargable bound on a hot query
- Invariant: a query the daemon issues on the event loop plans as a bounded seek, not an open-ended scan.
- Bit: `list_sessions` bounded its per-session count with `(s.end_id IS NULL OR l.id <= s.end_id)`, giving the planner a lower bound only: 2.06 s at 1M lines, 19.2 s at 500 sessions. `COALESCE` made it 88 ms and 67 ms.
- Also `GET /can/frames?port=` and `?last_ms=`, where the filter lands on the joined `lines` table: with no index on `lines.port` the planner reads the predicate as selective, drives the join from `lines`, and thereby discards the `ORDER BY cf.line_id DESC` index order, sorting every match through a temp b-tree before `LIMIT` applies. 131 ms against 0.4 ms at 1M lines. `CROSS JOIN` pins the drive order, and costs nothing on the filters that were already fast. `GET /plot/channels?port=` was the same shape in an `IN (SELECT ...)`.
- Sweep: `EXPLAIN QUERY PLAN` every statement reachable from a handler; a `SEARCH` with only `rowid>?` or a `SCAN` of the table btree on a hot path is the finding. Pin the plan in a test, not just the result: a correctness test passes either way.
  - Explain the statement the daemon issues, not a copy of it (`_captured_plan` in test_hardening.py takes it off the connection's trace callback).
  - Run the sweep against a capture with **no `sqlite_stat1`** and more than one port. The store never runs `ANALYZE`, so that is the shipped condition, and it is the one where the planner guesses wrong: with stats present every one of these plans is already correct, which is why the first synthetic run missed both. A two-row database reproduces the plan choice, so this needs no bulk data.
  - Not every scan is a finding: an aggregate over a whole table (`/plot/channels` counts every point of every channel) cannot be a bounded seek. Judge such a query on whether a filter makes it *worse*, and on whether it runs off the loop.

### 21. Wall-clock granularity as a test ordering assumption
- Invariant: a test that needs strict ordering against a stored `ts` derives the boundary from the data, or spins until the clock reads strictly past it. It may not assume that two `time.time()` calls differ.
- Bit: `test_purge_before_ts_deletes_only_what_predates_it` was 50% flaky on Windows (4 failures in 8 isolated runs), and inert-but-passing on Linux. `time.time()` there has a resolution of **15.625 ms**, and 199,990 of 199,999 consecutive calls returned the identical float; the test's `time.sleep(0.01)` was shorter than one tick, so `cut` landed in the same tick as the row it had to exclude.
- Sweep: every test comparing a captured `time.time()` against a stored `ts`. Each must derive the boundary from the data or spin the clock; a bare `sleep()` under 16 ms is not a boundary.

### 22. A stdlib predicate standing in for a wire grammar
- Invariant: a value arriving from the wire, the CLI, a URL or a hand-editable config file is matched against the grammar it is documented to have - explicit character set, explicit bounds, explicit type - never against `isdigit()`, `isdecimal()`, `bool()` or the tolerance of bare `int()`.
- Bit: the most-repeated class after the pid record, and the one that keeps coming back under a new name because each fix was written as "use isdecimal() instead of isdigit()" rather than as this invariant.
  - seq numbers accepted `+17`, `1_7` and other scripts' digits, so a garbled response resolved the pending command for seq 17
  - the plot, enum and marker-tick grammars had the same hole
  - `parse_can_event` and `parse_can_tx_args` gated the RTR dlc digit on `isdecimal()`, three lines below a comment explaining why that is wrong for the tick token in the same function. `'٣'.isdecimal()` is True and `int('٣')` is 3, so a garbled line decoded into a `can_frames` row instead of staying a generic event
  - the simulator's `_parse_dec` answered commands no firmware would
  - `store.resolve_session` had both halves at once: a session *named* `٣` resolved to session **id 3**, and because `isdecimal()` bounds no length, a 5000-digit ref reached `int()` and raised past CPython's 4300-digit conversion limit - an unhandled 500 with a traceback on `GET /sessions/{ref}/export` and on every endpoint taking `session=`
  - `config.py` read every integer with bare `int()`, which is the same defect as the `check = "false"` one that `_as_bool` was written for, from the other side. `port = true` became port **1** (a bool *is* an int in Python), `port = 8765.7` truncated silently, and `port = 99999999` was taken as written and failed later from inside the bind, naming neither the file nor the key. Found by running class 22's own sweep after filing it, in a module this round's module leg never opened.
- Sweep: `grep -rn "isdigit()\|isdecimal()\|isalnum()" host/mcuscope`, plus every `int(`, `float(` and `bool(` whose argument came from outside the process - the wire, argv, a URL, or the config file. Each site is `is_decimal_token` (or an explicit `in "0123456789"` for a single digit, or an `_as_*` config helper), or exempt with a stated reason.
  - A coercion helper written for one type is a signal, not a fix: `_as_bool` existed for two years' worth of rounds beside four unguarded `int()` calls in the same function.
  - Decide per value whether a bad one fails the load or falls back. A wrong *type* is unrecoverable, so it fails and names the key; an out-of-range number has a sane default. Falling back beats clamping wherever the value governs deletion: clamping `retention_days = 0` to its floor of 1 deletes almost everything, where the default keeps it.
  - `isdecimal()` is not the fixed form of `isdigit()`. It fails the same two ways: other scripts' digits, which `int()` silently converts, and no length bound at all.
  - The discriminating test input is `٣` (U+0663), not `²`. A test using only the superscript passes against `isdecimal()` and proves nothing.

## Review legs

A round is these legs, run in this order; each leg owns its output list.

1. **Registry leg** - executes every sweep in the registry above and files the per-site verdict lists.
   Runs first because it is mechanical and its results seed the other legs.
2. **Measurement leg** - drives the real stack and measures; fixes only what measurements justify.
   Owns: the sim demo end to end, a live daemon lifecycle (start, collide, stop, crash), the CLI through the installed console scripts, the web UI in a browser, the real board on the bench.
   Runs per platform; Windows console and socket semantics cannot be asserted from CI, so this leg includes the Windows machine.
   Records what it ruled out, with the probe that ruled it out, as first done in 77e5a69.
3. **Invariant legs** - each owns one cross-cutting invariant across the whole tree, candidate invariants taken from SPEC and CLAUDE.md mandates that have no registry entry yet.
   These exist because the module partition hides cross-cutting classes.
   99eab7c ran ten agents by module plus a seams agent and repeat classes still leaked: seams between modules are not one invariant over all modules.
4. **Coverage and artifact leg** - runs coverage without the floor, reads uncovered branches in shipped paths as candidate dead branches, and executes the class 15 sweep.
   Measured 2026-08-01: total 76.9% against the 55% floor, so the floor alerts on nothing.
   cli.py reads 33% and daemon.py 63% in-process because the suite drives them via subprocess, so their gaps need manual disposition or subprocess coverage collection.
   Read the uncovered lines as a list of **untested request parameters**, not only of untested code: the miss that mattered was `POST /purge {before_ts}`, the one destructive selector with no test, both of its branches shipped unexercised. `mcu purge --before` reaches it.
   Still open from the 2026-08-01 disposition, each a shipped path with no test: `/can/frames` `since_ts`, `port`, `since_id` and `last_ms` filters (store.py); the token fail-table eviction at `TOKEN_FAIL_TABLE_MAX` (server.py); `_carried` eviction at the port cap (serial_link.py); the forced retention trim (store.py).
   Dead-by-design and needing no test: every `ctypes`/`msvcrt`/`SIGBREAK` branch (Windows, see the measurement leg), and `drain_counted`, which needs a native serial port.
5. **Module leg** - deep single-module reading, kept because genuinely new classes still come from it (most of 99eab7c's and 0c676ec's volume did).
6. **Test-quality leg** - revert-verifies each new regression test, hunts tautological and platform-inert tests, checks that asserted behaviour matches the attack direction.
   Revert-verification belongs in the fix step itself, not only in this leg: in the first round run this way, three of six fix agents caught a non-discriminating test in their own work before reporting it.
   The three shapes seen: a plan test that explained a hand-written copy of the query rather than the one the daemon issues; a CLI test where the daemon rejected the input first, so the client fix was never exercised; and a guard using `pytest.raises`, which a skip also satisfies.
   A fourth, which only CI caught: a test that asserts a specific errno asserts more than the invariant, and the other OS answers differently. Windows drops the SYN where Linux refuses it, so `ConnectionRefusedError` was the wrong evidence for "the listener is closed"; the listening socket itself is the right one. Ask of every test what it would take to make the assertion true on the OS it was not written on.
7. **Fix-diff leg** - runs last, on the round's own diff: re-read every hunk for the platform it was not written on and against the registry invariants.
   The diff includes the round's new tests. Narrowed to source files on 2026-08-01, on the argument that the test-quality leg owns tests, and the one thing this round shipped broken was a new test that could not pass on Windows. Two legs each assuming the other covers tests is how it escaped.

## Exit criterion

A round does not end when the agents stop reporting; it ends when all of the following hold.

- Every registry sweep was executed and its per-site verdict list is filed.
- Every finding of the round is closed class-wide, and each new class has a registry entry with a sweep.
- The measurement checklist ran on both platforms, with numbers recorded and a ruled-out list.
- The coverage report was reviewed and every uncovered branch in a shipped path is marked dead-by-design or now covered.
- Every new regression test was verified to fail with its fix reverted.
- The fix-diff leg reviewed the round's own diff and reported.

The evidence a round must produce: the sweep verdict lists, the measurement and ruled-out log, the coverage disposition list, the revert-verification list, and the fix-diff report.
That evidence is filed in `docs/REVIEW_LOG.md`, one section per leg per platform.
The campaign, as opposed to the round, ends when a full round produces no new defect class; repeat instances found by sweeps prove the sweeps work and do not extend the campaign.
