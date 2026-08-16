# Review sweep runbook

How to run a review round so one round finds what previously took several.
Derived from the rounds 99eab7c, 0c676ec, e563a94, 8c4138a, 187a0e4, 77e5a69, 4d7b4ef, 6e3d1ed and the 2026-08-01 round; each rule cites the finding that justifies it.
Per-round evidence lives in `docs/REVIEW_LOG.md`; this file holds only what transfers to the next round.

Two principles govern everything below:

- **Close every finding class-wide, not site-wide.**
  A finding is open until every site of the same primitive is ruled in or out explicitly and the class is in the registry with a sweep.
  - Evidence: half the findings of the last two fix rounds were repeat instances of an already-confirmed class (77e5a69: 3 of 6; 4d7b4ef: 6 of 12).
    replace_atomic() swept config saves, the pid record and the update cache in one commit (77e5a69) and never recurred; single-site fixes (executor, newline, counters) recurred for up to four rounds.
  - **The close is not the fix, it is the sweep after the fix.**
    Fixing the sites a probe happened to reach and filing that as class-wide is the failure mode, and it is seductive because the fixed sites are the ones you have in hand.
    Real instance 2026-08-02: C1 was fixed on `/wait` and `/assert` and filed closed; the sweep, run afterwards, found `/lines?chan=` doing the same thing and `/lines?order=` silently sorting ascending on any unrecognised value.
  - Enumerate the sites *mechanically* - by AST, by grep, by reading every handler signature - never by listing the ones you can think of.
    Write the site count first, then rule each one; a close with no count behind it has not been swept.
- **The registry finds known classes; the legs exist to find unknown ones.**
  Sweeps are the cheap first pass, but a round that only re-runs them cannot end the campaign, whose exit is a full round producing no *new* class.
  Budget genuine reading and driving with no target list (legs 2, 3 and 5); their job is the defect shape nothing here names yet, and every class below was once that.

## Sweep discipline

How to execute any registry sweep; each rule was bought with a sweep that failed this way.

- A sweep's output is a verdict list: every site marked "violates", "complies", or "exempt because <reason>". An unlisted site means the sweep was not run.
- Open the list with the site count the sweep command returned, and account for every one.
  A truncated sweep is worse than none, because it files a verdict list that reads complete.
  Real instances 2026-08-01: class 22's first run was piped through `head -40` and filed as closed with two live instances below the cut; class 21's list said "16 sites" for a grep that returns 36 lines, and the unlisted ones were only established exempt when the list was audited later.
- Sweep with the real command surface, never invented inputs.
  A class 10 run with subcommand names the CLI does not have (`config`, `sessions`, `can --limit`) "passed" every case by measuring the unknown-command error path, which is a different contract.
- File the verdict list in `docs/REVIEW_LOG.md` before the round closes.
  A list that lived only in the session that ran it is unrecoverable: the 2026-08-01 round closed citing classes 1-20 lists that exist nowhere.
- Run a new class's sweep in the session that files it, before the round closes.
  Class 22's sweep, run immediately after filing, found `config.py` (finding N5) in a module no leg had opened.

## Defect-class registry

Each entry: the invariant, where it bit, and the sweep that finds new instances.
When a round confirms a new class, add it here with its sweep, and run that sweep under the discipline above.

### 1. Blocking work on the event loop or default executor
- Invariant: no SQLite, regex, filesystem or device-enumeration work runs on the event loop, and the serial reader join never queues behind other thread work.
- Bit: /can/frames (99eab7c), plot_points retention scan blocking the loop 70 s (99eab7c), /plot/series and /plot/channels (0c676ec), GET /devices (77e5a69), plot export (4d7b4ef).
- Sweep: for every `async def` endpoint in server.py, trace each store/os/serial call; blocking work must go through match_executor, a named pool, or `asyncio.to_thread`.
  - `grep -rn "run_in_executor(None" host/mcuscope` must return no executable line.
  - New endpoints and new store queries are in scope by default, not on suspicion.
- The second clause was once enforced by *reserving* the default executor for the join.
  That rule was unenforceable and had been broken nine times unnoticed, because `asyncio.to_thread` is `run_in_executor(None, ...)`: the obvious stdlib idiom silently joined the reserved pool, and two of the nine (session export, device enumeration) are slow enough to matter.
  - Inverted 2026-08-01: the join owns a private `serial_link._join_pool` no other caller can reach, `to_thread` is unrestricted, and the sweep is the absence above rather than a convention.
    A rule that the obvious idiom breaks is a bad rule; prefer removing it to restating it.
  - The test is behavioural, not a grep: starve the default pool to a *single occupied* worker, then assert `SerialPort.stop()` still completes.
    Loading the pool rather than starving it passes either way on spare capacity.
  - A `threading.Lock` acquired on the loop is blocking work the moment any holder runs off-loop.
    Moving one side of a lock to `to_thread` silently converts the other side's synchronous acquire into a loop freeze bounded only by what the holder does (2026-08-09: `stop()` vs the offloaded `_write_bytes`).
    Whenever a sweep moves work off the loop, re-rule every other acquirer of every lock that work holds.

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
  - And the degradation direction itself (2026-08-10): `_value_taking_opts`'s `except Exception: return set()` made a resolver failure degrade to hoisting *without* the value guard - the invariant says failure degrades to NO hoisting, and an empty set re-arms the first bit silently on any typer upgrade that breaks the walk.
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
  - a new matrix cell (2026-08-10): {stale record} x {two concurrent claims} - both judge the record stale, A removes and recreates it, B's remove-by-path then deletes A's fresh record.
    Narrowed by re-reading immediately before the remove (skip if the record changed); NOT closed - Windows has no atomic compare-and-delete, and the residual window is stated in the claim() comment so a later round does not file it as fixed.
- Sweep: a state matrix test - {no record, stale, live other process, live parent, our own} x {claim, release, stop, failed startup}; every cell has an asserted outcome.

### 8. Thread teardown on detach and shutdown
- Invariant: a reader thread always releases its handle and never touches a closed loop, in every ordering of detach, join timeout and loop close.
- Bit: handle held after a join timeout blocked Windows re-attach (187a0e4); raise on closed loop and a leaked handle (4d7b4ef).
- Sweep: per thread, enumerate outlive scenarios (join timeout, loop closed, exception mid-read) and assert handle close plus callback fate in each.

### 9. CLI exit-code contract (SPEC 4)
- Invariant: every path out of `mcu` maps to 0/1/2/3; a traceback reaching the user is a defect.
- Bit: `assert` exiting 2 (99eab7c); EPIPE and JSON errors as tracebacks (99eab7c, 0c676ec); `daemon start` traceback after the daemon was already spawned (77e5a69).
  - And one level inside a shape guard (2026-08-10): `_list_field` validated "is a list" but not the elements, so a daemon answering `{"lines": ["x"]}` was a rich TypeError traceback plus a crash log - a guard that checks the container vouches for the contents until it explicitly does not.
- Sweep: enumerate `raise`, `except` and `Exit` sites in cli.py; each exception type reaching main() has a mapping, and each failure mode is driven through the installed console script, not `python -m`.

### 10. --json stdout purity
- Invariant: with `--json`, stdout carries exactly one JSON document; prompts, warnings and repair notices go to stderr.
- Bit: prompts and `-o` paths (0c676ec); stream-repair warnings on stdout (4d7b4ef).
  - And before the mode exists (2026-08-10): a global option missing its value died inside `_split_global_opts`, which runs before `set_json_mode`, so `mcu --json status --url` emitted no JSON document at all - an error path that fires before the contract's flag is readable must derive the flag from what it has already parsed.
- Sweep: run every subcommand with `--json` and assert `json.loads(stdout)`; grep new print/write sites for the stream they target.
  - Three commands are exempt and emit JSONL by design: `mcu tail` (its `-f` form is an unbounded stream), `mcu log export` and `mcu can dump`.
    SPEC 4 asserted "exactly one JSON object" for *every* command while the table two lines above it said `log export` dumps JSONL; the sentence now carries the exemption, so a later round cannot "fix" `tail -f --json` into something no follower can parse.

### 11. Codec symmetry in protocol.py and the sim
- Invariant: format_x and parse_x accept the same domain, and parse returns None where documented instead of raising.
- Bit: format_can_event accepting ids parse_can_event rejects (99eab7c); an out-of-range echo id raising inside sim poll_events and killing the listener (187a0e4).
- Sweep: property-test `parse(format(x))` over the full id/data domain; fuzz parse with malformed input asserting None, never an exception.
  - A real board is a second, independent implementation of SPEC 5, so a bench session exercises this class harder than the sim can: the 2026-08-01 bench run decoded every error envelope a foreign firmware produced.

### 12. Healthy-while-dead surfaces
- Invariant: when a worker dies or a setting fails to apply, the surface that reports health must change state.
- Bit: the highest-severity shape in the whole series.
  - sim listener open after its serving thread died, so the port read healthy (187a0e4)
  - UI showed live while queueing rows into an undrained staging area (4d7b4ef)
  - second daemon printed its URL and was never reached (77e5a69)
  - auto_vacuum silently stayed 0, so every incremental_vacuum was a no-op (99eab7c)
  - Windows CI reported green while its jobs had never run (setup-uv pin, context of e563a94)
  - the web UI reading "live" with its watermark jammed past every arriving row (2026-08-11): a capture reset landing mid-staging let the drain's id-sort fold dead-capture rows in after the new ones, pushing `state.maxId` back to the old watermark; every later row dropped, the daemon never resends the token, `streamOnline` stayed true.
- Sweep: a probe checklist, not a grep; the measurement leg runs it, and it is the most expensive sweep and the one that finds the worst class.
  - Kill each worker (store writer, reader thread, sim serving thread, WS feed) on a live stack and assert the health surface reflects it.
  - Read back every PRAGMA and config setting after applying it.

### 13. Windows file-sharing and encoding semantics
- Invariant: replace/rename goes through config.replace_atomic(); user-editable text is read tolerating a BOM; output survives a non-UTF-8 or redirected console.
- Bit: os.replace losing a settings save to a transient antivirus handle, BOM in config.toml (77e5a69); `mcu devices` dying redirected on a non-ASCII port description (187a0e4).
  - Also: `pidfile.claim` removed its half-written record from inside the `except`, with the fd still open in the enclosing `finally`.
    Windows refuses to unlink an open file, so the suppressed error left exactly the empty record the removal exists to prevent. POSIX allows it, so only the Windows CI leg saw it.
- Sweep: `grep -rn "os.replace\|os.rename" host/mcuscope` outside replace_atomic; check `encoding=` at every read of user-editable files; run output-producing commands redirected.
  - Also every `os.remove`/`os.unlink` on a path this process may still hold open: the close has to precede the unlink, and a POSIX-only test passes either way.
    Emulate the rule locally (patch `os.remove` to fail while an fd on the path is open) rather than waiting for the Windows leg.
  - Windows spells a write to a closed pipe `OSError(EINVAL)`, not `BrokenPipeError`, so every `except BrokenPipeError` is inert there.
    Classify at the boundary that knows the context: `_stdio.translate_closed_pipe_errors` re-raises EINVAL from a non-tty stdout/stderr write as `BrokenPipeError`, and no handler classifies errnos.
    Widening the handlers instead swallows real EINVALs as success (found and reverted the same round, 2026-08-09).
    Sweep: `grep -rn "EINVAL\|except BrokenPipeError" host/mcuscope`; every pipe-close consumer relies on the boundary translation, and the translation itself stays tty-gated.

### 14. Platform-gated fixes
- Invariant: a platform gate may gate the mechanism, never the invariant; for each gate, name what enforces the same guarantee, in the same order, on the other OS.
- Bit: the port probe shipped Windows-only, so on POSIX a failing second daemon still clobbered the pid record before uvicorn reported EADDRINUSE (77e5a69, found 4d7b4ef).
- Sweep: `grep -rnE "sys\.platform|os\.name|hasattr\((socket|signal|os), |getattr\(os, " host/mcuscope`; for each gate write one line naming the other platform's enforcement.
  - The `sys.platform|os.name` form alone misses the capability probes, which is most of them: it returns 13 sites where this returns 20 (19 after `_legacy_pid_file` was deleted in 71c3f7e, back to 20 when 3a2bf4d added `_stdio.py`'s `PIPE_CLOSE_IS_EINVAL`).
    The ones it drops include `daemon.py`'s `SO_EXCLUSIVEADDRUSE` - the site class 14 is named after - plus SIGBREAK and both `O_BINARY` uses.
    A gate written as "does this attribute exist" is still a gate.

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
- The mirror image, found by the fix-diff leg in the guard that fixed the third instance: a guard that keeps looping must still recognise the errors that are not per-item.
  `serve_pty` retried a dead pty master at 10 Hz forever, printing the same line, while its TCP sibling already broke on the fd-dead errnos. Ask both questions of every such guard.
- The mirror's mirror (2026-08-10): a guard that classifies errors as fatal must recognise the ones that are not.
  The sim's nonblocking send treated `BlockingIOError` (an OSError subclass, meaning a live-but-slow reader) as a dead peer and dropped the session, resetting all sim state - while the recv side three lines up already classified the same errno as transient.
  One-of-two-siblings, with the classification inverted.
- And the staged twin of a guarded loop (2026-08-11): the web UI's live-row path wrapped each row in try/catch with a comment explaining exactly why, and the staged-drain loop over the same rows had no guard, so one malformed staged row cost every row behind it.
  When a loop is duplicated for a buffered/deferred variant, the guard must travel with it.

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
- Sweep: for each httpx, websockets, json and urllib call site, diff its `except` tuple against the other call sites of the same library in the same file.
  A tuple that is a strict subset of its sibling's is the finding.
- Not the fix: a blanket `except Exception` in the CLI dispatcher.
  `_stdio.console_entry` is already that backstop and returns 1, so a blanket clause buys no exit-code correctness, and it replaces the crash log a genuine bug needs with an indistinguishable "error: something".

### 19. Two engines validating one thing
- Invariant: a check performed in two places uses the same implementation, or the looser side is not a check at all.
- Bit: `mcu tail -f --match` compiled with stdlib `re` while the daemon compiled with `regex`, so a pattern the daemon accepted crashed the client after it had already printed a matched line.
- And then the fix for it copied the engine without the guard: the client had no `timeout=`, so `(a|a)+$` hung the follow with no error, no exit code and no working Ctrl-C. Adopting the same library is not adopting the same check.
- And the web UI's `!can` decoder, which mirrors `protocol.parse_can_event` by hand but omitted its id range check, so a frame with an id past 0x7FF appeared in the CAN sidebar while the daemon had kept the line as a generic event with no `can_frames` row.
  The table and `GET /can/frames` disagreed about the same line.
  The test file asserting the mirror already said "the browser and the daemon must agree"; its list of malformed inputs was simply missing the case.
- Sweep: list every validation duplicated between client and daemon, or between host and firmware, and name the single implementation both use.
  Where the two cannot share code, list what the daemon's version does beyond calling the library, and check each item separately.
  - And `plots.js`'s `parseEnumLabels`, which mirrors `_parse_enum_labels` without its digit-count cap, so a `!pd` the daemon dropped whole still built a definition in the browser and charted a stream `/plot/series` had never decoded.
  - For a hand-written mirror, diff it against the original clause by clause.
    A mirror is a copy that stops being one silently, so an existing "these must agree" test is not evidence that they do.
    Both misses found this round were a *bound* the mirror omitted while copying the character-set check next to it.

### 20. Non-sargable bound on a hot query
- Invariant: a query the daemon issues on the event loop plans as a bounded seek, not an open-ended scan.
- Bit: `list_sessions` bounded its per-session count with `(s.end_id IS NULL OR l.id <= s.end_id)`, giving the planner a lower bound only: 2.06 s at 1M lines, 19.2 s at 500 sessions.
  `COALESCE` made it 88 ms and 67 ms.
- Also `GET /can/frames?port=` and `?last_ms=`, where the filter lands on the joined `lines` table.
  With no index on `lines.port` the planner reads the predicate as selective, drives the join from `lines`, and thereby discards the `ORDER BY cf.line_id DESC` index order, sorting every match through a temp b-tree before `LIMIT` applies: 131 ms against 0.4 ms at 1M lines.
  `CROSS JOIN` pins the drive order, and costs nothing on the filters that were already fast. `GET /plot/channels?port=` was the same shape in an `IN (SELECT ...)`.
- Sweep: `EXPLAIN QUERY PLAN` every statement reachable from a handler; a `SEARCH` with only `rowid>?` or a `SCAN` of the table btree on a hot path is the finding.
  Pin the plan in a test, not just the result: a correctness test passes either way.
  - Explain the statement the daemon issues, not a copy of it (`_captured_plan` in test_hardening.py takes it off the connection's trace callback).
  - Run the sweep against a capture with **no `sqlite_stat1`** and more than one port.
    The store never runs `ANALYZE`, so that is the shipped condition, and it is the one where the planner guesses wrong: with stats present every one of these plans is already correct, which is why the first synthetic run missed both.
    A two-row database reproduces the plan choice, so this needs no bulk data.
  - Not every scan is a finding: an aggregate over a whole table (`/plot/channels` counts every point of every channel) cannot be a bounded seek.
    Judge such a query on whether a filter makes it *worse*, and on whether it runs off the loop.
  - An index is a change to *every* query over its table, so its plan test covers more than the query it was added for.
    `idx_lines_port_id` fixed `/lines?port=` and regressed two others: `/plot/channels?port=` 90 ms to 208 ms (caught before commit by an existing test, and only because that one asserts positively) and `/lines?port=&chan=` 0.09 ms to 319 ms on the event loop (shipped, because the new test pinned only the port-alone query).
    Re-explain every statement over the table, and pin the combinations rather than the motivating case alone.

### 21. Wall-clock granularity as a test ordering assumption
- Invariant: a test that needs strict ordering against a stored `ts` derives the boundary from the data, or spins until the clock reads strictly past it.
  It may not assume that two `time.time()` calls differ.
- Bit: `test_purge_before_ts_deletes_only_what_predates_it` was 50% flaky on Windows (4 failures in 8 isolated runs), and inert-but-passing on Linux.
  `time.time()` there has a resolution of **15.625 ms**, and 199,990 of 199,999 consecutive calls returned the identical float; the test's `time.sleep(0.01)` was shorter than one tick, so `cut` landed in the same tick as the row it had to exclude.
- Sweep: every test comparing a captured `time.time()` against a stored `ts`.
  Each must derive the boundary from the data or spin the clock; a bare `sleep()` under 16 ms is not a boundary.
  Passing `time.time()` *as* a row's ts is the exempt shape; capturing it as a comparison boundary is the suspect one.
- A wall-clock *threshold* is the same class as a wall-clock ordering assumption.
  The web UI's regex-budget test asserted `spent < 20 s` for one catastrophic match that runs ~1 s here and 26 s on the Windows runner, so it measured the machine, not the guard.
  The guard's promise is countable: one row matched, then the pattern is dropped. Count the work, never time it.
- A one-shot stimulus into a shedding queue is the same class without a clock in sight (2026-08-11).
  test_assert's needle was broadcast once into a test-injected 4-slot drop-oldest queue, so a ~220 ms consumer stall on a loaded runner shed the only matchable row and `/wait` honestly answered timeout.
  The assumption "the consumer drains within N arrivals of the stimulus" is a scheduling assumption exactly like "two time.time() calls differ".
  A stimulus a queue may shed is re-armed until acknowledged, never fired once.
- The same shape one level up, found by the fix-diff leg: an assertion phrased against one implementation's *vocabulary* rather than the invariant.
  `assert "SCAN l" not in plan` passes on any SQLite that words the plan differently (it said `SCAN TABLE lines AS l` before 3.36), so the test goes quietly green on the build where it needs to speak up.
  Assert the good state positively, never the absence of a string some other version spells another way.

### 22. A stdlib predicate standing in for a wire grammar
- Invariant: a value arriving from the wire, the CLI, a URL or a hand-editable config file is matched against the grammar it is documented to have - explicit character set, explicit bounds, explicit type - never against `isdigit()`, `isdecimal()`, `bool()` or the tolerance of bare `int()`.
- Bit: the most-repeated class after the pid record, and the one that keeps coming back under a new name because each fix was written as "use isdecimal() instead of isdigit()" rather than as this invariant.
  - seq numbers accepted `+17`, `1_7` and other scripts' digits, so a garbled response resolved the pending command for seq 17
  - the plot, enum and marker-tick grammars had the same hole
  - `parse_can_event` and `parse_can_tx_args` gated the RTR dlc digit on `isdecimal()`, three lines below a comment explaining why that is wrong for the tick token in the same function.
    `'٣'.isdecimal()` is True and `int('٣')` is 3, so a garbled line decoded into a `can_frames` row instead of staying a generic event
  - the simulator's `_parse_dec` answered commands no firmware would
  - `store.resolve_session` had both halves at once: a session *named* `٣` resolved to session **id 3**, and because `isdecimal()` bounds no length, a 5000-digit ref reached `int()` and raised past CPython's 4300-digit conversion limit - an unhandled 500 with a traceback on `GET /sessions/{ref}/export` and on every endpoint taking `session=`
  - `config.py` read every integer with bare `int()`, which is the same defect as the `check = "false"` one that `_as_bool` was written for, from the other side.
    `port = true` became port **1** (a bool *is* an int in Python), `port = 8765.7` truncated silently, and `port = 99999999` was taken as written and failed later from inside the bind, naming neither the file nor the key.
    Found by running class 22's own sweep after filing it, in a module this round's module leg never opened.
  - The ports loop of the same file kept both coercions after the sections above were fixed: `autoconnect = "false"` read as **True**, the literal string `_as_bool` exists for, 25 lines below it, and `baud = true` became **1 baud**.
  - The web UI read every numeric field with `parseInt`, which takes the leading digits and stops.
    `1e9` in the settings port box parses as **1**, passes the 1..65535 check, and saves port 1; `<input type=number>` accepts exponent notation, so nothing unusual has to be pasted.
    Same class, different language, and the sweep has to be run per language to see it.
- Sweep: `grep -rn "isdigit()\|isdecimal()\|isalnum()" host/mcuscope`, plus every `int(`, `float(` and `bool(` whose argument came from outside the process - the wire, argv, a URL, or the config file.
  Each site is `is_decimal_token` (or an explicit `in "0123456789"` for a single digit, or an `_as_*` config helper), or exempt with a stated reason.
  - Also `grep -rn "parseInt\|parseFloat" host/mcuscope/webui`: `parseInt` is JavaScript's version of the same permissiveness.
    Exempt shapes: a base-16 use on a hex wire token, and a `parseFloat` gated by an explicit grammar regex plus `Number.isFinite` (`parsePlotValue`).
  - A coercion helper written for one type is a signal, not a fix: `_as_bool` sat beside four unguarded `int()` calls in the same function through every round since it landed.
  - Decide per value whether a bad one fails the load or falls back.
    A wrong *type* is unrecoverable, so it fails and names the key; an out-of-range number has a sane default.
    Falling back beats clamping wherever the value governs deletion: clamping `retention_days = 0` to its floor of 1 deletes almost everything, where the default keeps it.
  - `isdecimal()` is not the fixed form of `isdigit()`. It fails the same two ways: other scripts' digits, which `int()` silently converts, and no length bound at all.
  - The class has a second face: a value that parses but is not a *quantity*.
    `float("nan")` satisfies every `except ValueError` guard in the tree, and `max(nan, 0.0)` is `nan`, so `mcu daemon start --timeout nan` spawned a daemon and killed it immediately with advice that could never work.
    Ask "is it finite and in range?", never only "did it parse?" - a `try: float(x) except ValueError` reads like validation and is not.
  - The discriminating test input is `٣` (U+0663), not `²`. A test using only the superscript passes against `isdecimal()` and proves nothing.
  - Two more faces (2026-08-10).
    A pid record's 20-digit grammar bound passed tokens the syscalls overflow on: `pid_running(2**32+1234)` raised ctypes.ArgumentError out of daemon startup and `mcu daemon stop` - grammar-checked is not range-checked, and real pids fit in 7 digits, so the width past that was pure attack surface (now bounded 1..0x7FFFFFFF at read, with the syscalls guarded too).
    And `/marker`'s `port` was the one endpoint field reaching the capture without any grammar at all, beside a `text` field bounded for exactly that reason: a bound on one field is defeated through the unbounded field next to it.

### 23. A rebuild path silently un-freezes a paused surface
- Invariant: a surface with a paused state is frozen against *every* writer, not only against the arrival path the pause was written for.
  The pause flag and the frozen contents are two different things, and freezing the flag alone reads as paused while the contents move.
- Bit: a paused terminal pane.
  `rebuild()` recomputed `pane.rows` from the shared buffer and zeroed `pane.pending`, and two sibling paths called it on every pane unconditionally: the end of every backfill (so every WS open and reconnect) and the high-rate release.
  A pane paused on rows 1-3 with "3 new" came back showing 1-6 with the counter cleared, while the pill still read "paused".
  Its own comment claimed it "preserves the pane's live/paused state", which was true only of the `autoscroll` flag.
- The sibling that got it right is the argument for the class: `plots.js` had already fixed exactly this for charts ("Without this a paused chart silently crept forward one sample per arrival ... i.e. it un-paused itself"), and the terminal pane was the one that never got the same care.
  One of two siblings fixed is the shape to look for.
- Sweep: for every surface with a paused, frozen or held state, list *every* writer of the contents that state covers, not just the arrival path.
  Each writer is bounded by the freeze or is ruled exempt with a reason.
  A test must pause, drive the *other* writer (a reconnect, a backfill, a mode change), and assert the contents did not move - asserting the flag is what missed this.
- Also here: an export or download button that ignores its surface's freeze.
  `plots.js exportChart` sends `last_ms` resolved against *now*, so a chart paused on a transient exports a window that does not contain it, under a button whose own title says "the current window".

### 24. A fix that rests on one runtime version's driver behaviour
- Invariant: a fix whose mechanism is "the driver steps/consumes/coerces this for us" holds on every supported Python, or it is not a fix.
  The support floor (3.11) is a leg of the sweep, not a formality.
- Bit: `_reclaim_pages` fixed the one-page reclaim by consuming the pragma's rows with `.fetchall()`.
  On 3.12+ that yields a row per page and drains; on 3.11 `PRAGMA incremental_vacuum` yields no rows at all, so the fetch is empty, the statement steps once and one page comes back - the original defect, alive on the floor version only.
  Both pinning tests passed locally and failed the 3.11 CI legs. `executescript` steps it to completion everywhere.
- Sweep: for every fix resting on driver-side row consumption, type coercion or transaction handling, run its test on the support floor.
  Where a local run of the floor is not on hand, the test emulates the floor's behaviour (subclass the connection, rewrite the statement) so the fault is local rather than CI-only.

### 25. A group state that only reaches the members that already existed
- Invariant: a state applied to "all of X" governs the X created after it too, or it is not a group state.
  - Fan-out freezes the current members. Birth is the other half, and the half that gets forgotten.
- Bit: "pause all" in the web UI froze every pane, chart and the digital panel, and then:
  - a pane added afterwards came up live;
  - a chart rebuilt after clear-all came up live;
  - clear-all resumed the digital panel outright;
  - all three under a button still reading "resume all", i.e. the state announcing itself as in force.
- The label is the tell.
  - Text derived from the members reads correctly at the instant of the action and lies from the first membership change.
  - So any group state with a rendered label recomputes it on add and on remove, not only on the action.
- Sweep: for every group operation (pause all, clear all, select all, mute all), find each site that creates a member and each that destroys one.
  - Every create consults the group state; every create and destroy recomputes whatever renders it.
  - A test creates a member *after* the group action and asserts the member's own state, not the label.
- Related: a reset path that resumes as part of clearing is this class inverted - it drops the group state instead of propagating it.
  - `clearAllDigital` called `setDigitalPaused(false)`. The reason is usually a stale anchor, not the pause: fix the anchor, keep the intent.

### 26. A frozen view re-derived from a ring buffer that has rotated past it
- Invariant: a view frozen at a point in a bounded history keeps its own copy of what it froze.
  - A freeze bound (`id <= frozenId`) alone assumes the rows behind it are still there, and a ring will drop them.
- Bit: a paused terminal pane. `rebuild()` re-derived `pane.rows` from the 5000-row shared buffer, bounded above by `pane.frozenId`.
  - At the sim's line rate the buffer turns over in about 20 s, after which every row in it sat *past* the freeze.
  - Editing the pane's regex then blanked it, and clearing the regex brought nothing back: the rows were gone, not filtered.
  - Intermittent by construction - it depends only on how long the pane has been paused.
- This is class 23's other half: there the freeze failed to hold the view still, here the freeze held and the source moved out from under it.
- Third instance (2026-08-11): the paused ANALOG chart, the last of the three freeze surfaces, and the one the 22219d6 round's sweep ruled compliant.
  Its `frozenLen` slid down with every trim, which is not eviction-honesty but the same rotation loss on a delay: ~104k samples after pause (100 s at 1 kHz) it reached 0 and the paused chart silently blanked.
  A sliding index into a rotating ring is a snapshot that decays; the sweep verdict "slides with eviction, so compliant" was wrong and is corrected here.
  All three surfaces now snapshot at freeze.
- Sweep: for every frozen or held view, ask what backs it and whether that backing is bounded.
  - Where it is a ring, cache, LRU or TTL store, the freeze snapshots.
  - A test pauses, rotates the *whole* backing store past the freeze, then re-derives; rotating part of it passes on a bug.

### 27. A test double gentler than the thing it stands in for
- Invariant: a double diverges from the shipped implementation anywhere *except* the respect under test, or it certifies a defect.
  - Silent by construction: the test passes, and it passes on the broken code.
- Bit: the pause-all latch.
  Every shipped freeze surface calls `freezeChanged()` from its own `setPaused`, which ends the latch whenever anything is still live.
  - So the first surface frozen wiped the latch while the rest of the fan-out was still running.
  - The double's `setPaused` did not call `freezeChanged()`, so the test asserted `bornPaused() === true` and got it while the browser got `false`.
  - The defect class 25 was written for survived its own fix, green.
- Second instance the same round: the harness link opener answered for *every* device, so the suite's dead-`socket://` ports connected to a simulator.
  - Nothing asserted `connected == False`, so nothing went red; four attach tests simply stopped exercising the path they document.
  - The production opener had the identical bug, where it meant `--sim` served a *real* configured board out of the simulator.
- Sweep: for each double, list what the real implementation does on the call the double implements - side effects, callbacks, ordering, what it dispatches on - and either mirror it or state why not.
  - A double that only records calls is the shape to look for: recording is not behaving.
  - Where the real thing dispatches (on a device string, a scheme, a type), the double dispatches the same way or the test set silently changes subject.
- A test whose window is a few bytecodes wide is not a detector.
  - An unlocked `SourceLink` passed a 200-command race test 200 of 200. Assert the exclusion, not the outcome of a race.

### 28. An assertion the test's own guard swallows
- Invariant: a negative test names the refusal it expects and asserts on it.
  `raise AssertionError(...)` inside a `try` whose `except` can catch it is not an assertion, and a bare `except Exception: pass` around the act under test cannot tell a refusal from a bug.
- Bit: the only test asserting that a WebSocket needs a token.
  `raise AssertionError("unauthenticated WS was accepted")` sat inside `try: ... except Exception: pass`, so removing the guard from `server.py` entirely left it green, and a probe against that mutant connected and read the capture.
- Sweep: `grep -rn "raise AssertionError" host/tests` and rule each one in or out by whether an enclosing `except` can reach it; every `except Exception` in a test body is a finding unless it re-raises or asserts on what it caught.
  - The correct shape, already used by `test_source_link.py`, is `try / except <the expected error> / else: raise AssertionError`: the raise sits in the `else`, where no `except` can reach it.
  - `pytest.raises` on a narrow project type (`ProtocolError`, `PortError`, `StoreError`, `ConfigError`, `LockError`) is self-discriminating and needs no `match=`, because only the code under test raises it.
    On a stdlib type (`ValueError`, `OSError`, `RuntimeError`, `sqlite3.*`) it does, or an unrelated bug of the same type satisfies the test.
  - `pytest.raises(typer.Exit)` asserts the *code* too, or it does not test the SPEC 4 contract it looks like it tests.
- The shape to look for is a test that can only fail by *timing out*, never by asserting.

### 29. The negative is never asserted
- Invariant: for every state a guard exists to produce - not connected, not acquired, refused, deleted, absent - some test asserts that state directly.
  A suite that only ever asserts success cannot tell a working guard from a missing one.
- Bit: the widest class this round, and the root cause behind four separate green-suite mutations.
  - `grep` for a negative `connected` assertion across the whole suite returned **zero**, so both class-27 opener-dispatch fixes were revertible in silence - including the shipped one, where the bug served a real configured board out of the simulator.
  - `lock.acquire()` -> `pass` left 631 tests green: SPEC 3.2's single-writer guard had only an argparse test.
  - `PRAGMA foreign_keys=OFF` left 631 tests green, because the test that should have caught it asserted through an inner join that hides orphaned children.
- Sweep: for each guard, name the observable it produces on the refused path and grep for an assertion of *that value*. Asserting the happy path twice is not covering both.
- Asserting absence through a query that joins away the evidence is the trap: assert on the child table itself, not on a view that cannot show it.

### 30. A wrapper that trusts an external runner's exit code
- Invariant: a test that shells out to another test runner asserts the runner did a plausible amount of work, not merely that it exited 0.
- Bit: `test_webui_js.py` checked only the return code of `node --test`, and **`node --test` exits 0 in a directory with no test files**.
  Moving the 15 `.test.mjs` files away made the wrapper pass in 0.17 s against 22.53 s - the entire web UI suite, green having run nothing.
- Its firmware sibling had the guard (a CI step asserting the C tests did not skip) and the JS one never got it, which is the one-of-two-siblings shape again.
- Sweep: every `subprocess.run` in a test whose assertion is `returncode`.
  Each must additionally assert a count parsed from the runner's own summary.
  Pin the toolchain version in CI too: a skip condition plus an unpinned runtime is a suite that can vanish on an image update.
- The count-parse itself is class 21's vocabulary trap one level down: the guard added for this class parsed the summary as `# pass N`, which is the tap reporter's spelling, and node 22 changed the piped default to spec (`ℹ pass 155`), so on node 24 the wrapper failed a green suite (2026-08-10, Windows).
  Parse every dialect the runner has shipped, or pin the reporter with a flag the support floor has.

### 31. A field the model accepts and the path never reads
- Invariant: every field a request model accepts is read on every path that accepts it, or refused on the paths that ignore it.
  Silently dropping it means the scope the caller asked for is not the scope they were answered about.
- Bit: `/assert` read `session` and `last_ms` only in its retrospective branch.
  `{"session": "typo"}` was a 400 retrospectively and a confident **200 pass** with `timeout_ms` set - a verdict over a window nobody selected.
  The mirror mistake was already refused in the same handler (`min_window_ms needs a live window`), which is what the fix was modelled on.
- Nine further parameters could be ignored outright with the full suite green, `/lines?since_ts` among them with zero occurrences anywhere in the suite.
- Sweep: for each request model, list its fields; for each field, grep the handler for a read on every branch.
  A field read on one branch and accepted on all of them is the finding.
  Then per parameter, one test asserting it *changes the result* - a test that only asserts "no error" measures nothing.

### 32. A function tested as pure that mutates module state
- Invariant: a helper tested as a pure transformation has no side effects, or its test restores what it touched.
  Otherwise test order decides the result, and the state it leaks becomes an unstated precondition of every later test.
- Bit: `_hoist_global_opts` is tested as an argv rewriter and flips the global `cli._JSON_MODE`, which nothing resets.
  One shuffled run in three went red - and the worse half is that the CLI test it breaks only passed because that global was `False`, which production never is, so it asserted against behaviour the shipped code does not have.
- Sweep: run the suite under random ordering with several seeds, not once. For each module-level mutable, grep for writes outside the entry point that owns it.
- An order-dependence failure is worth chasing past the flake: the reordering does not create the wrong assertion, it reveals one.

### 33. A test that runs the real entry point inherits the user's real environment
- Invariant: no test reads or writes the real platformdirs locations (data, config, cache) or any other per-user state, whatever path the code under test takes to resolve them.
- Bit: a `daemon.main()` test with a default config wrote `capture.db` and its lock into the user's live data dir during a revert-verify run (2026-08-09).
  The first fix had the class's second face: an in-process monkeypatch that subprocess-driven tests inherit nothing from, which left one pid-file test asserting against the wrong directory while green.
- Sweep: the autouse `_isolated_user_dirs` fixture in `conftest.py` is the enforcement; verify it patches every platformdirs function the package calls (`grep -rn "platformdirs\." host/mcuscope`).
  Then list every test that spawns a child (`grep -rn "Popen\|subprocess" host/tests`) and rule each: explicit paths passed through, or the child's resolution handled the way `_child_data_dir()` does.
  An in-process patch is not evidence for a child process.

### 34. A wire-named key on a prototype-bearing object store
- Invariant: a JavaScript store keyed by names that arrive from the wire (channel names, lane names, port aliases) is a `Map` or a null-prototype object, and values read back from persisted storage are type-checked.
  SPEC 2.5's name grammar admits `toString`, `constructor` and `__proto__`, so a plain `{}` answers an unsaved name with an inherited function and silently drops a write to `__proto__`.
- Bit: `chrome.js`'s colour store (`savedColors`), found 2026-08-10 while `plots.js` had already null-protoed its own `PLOT_TYPES` for exactly this, with a comment naming the attack.
  One of two siblings fixed is the class 23 shape again: a lane named `toString` drew in whatever colour the previous lane left in the canvas context, and recolouring a lane named `__proto__` never persisted.
- Sweep: for every name-keyed store in `host/mcuscope/webui`, `grep -n "JSON.parse\|= {}\|= Object" host/mcuscope/webui/*.js` plus every `x[name]` where `name` came from a row, a definition or persisted JSON.
  Each store is a `Map`, `Object.create(null)`, or exempt because its keys cannot come from the wire.
  Values re-read from `localStorage` are additionally type-checked, because it is hand-editable.
- The type-check clause bit again on its own (2026-08-11): the update badge's snooze record guarded `until` with `Number.isFinite` and a comment about a stored `"1e999"`, while `step` five lines down was read with `Number(x) || 0`, no integer or range check.
  `{"step":1.5}` indexed the ladder at undefined and the render threw, which the status poll's catch then painted as "daemon unreachable" forever.
  The sibling-guarded field is the tell, and a localStorage value needs the full grammar (type, integrality, range), not a truthiness coercion.

### 35. An error-reporting write failing hijacks the exit code the error owned
- Invariant: a failure to deliver an error message never changes the exit code the error mapped to.
  The boundary that writes the message owns the failure; nothing above it may reinterpret the write's own exception as the command's outcome.
- Bit: 2026-08-10, two faces in one finding.
  `err()`'s unguarded stderr print raised BrokenPipeError out of `die()` before its `typer.Exit`, landing in the dispatcher's broken-pipe-means-done arm (whose reasoning is about stdout), so with stderr a closed pipe every error exit (1/2/3) became 0: `mcu ... 2>&1 | head -1` read "daemon unreachable" as success.
  And suppressing the write alone was not enough: the bytes the failed write left in stderr's buffer made CPython's shutdown flush raise, exiting 120 over the mapped code - the fix must also repoint the stream at devnull (`_to_devnull`), the same shutdown hazard `_silence_stdout` already answered for stdout.
- Sweep: enumerate every write on an error path (stderr prints, log-and-die helpers, JSON error emitters); each is guarded at the boundary that knows the stream, and the guard both suppresses the write's failure and neutralises the buffered bytes against the shutdown flush.
  A test closes the stream's read end and asserts the exit code differentially against the stream attached.

### 36. A periodic catch-up loop without a burst cap
- Invariant: a loop that emits one item per elapsed period caps how many it emits per pass and re-anchors past a large stall; missed beats of a periodic signal are dropped, not backfilled.
  `FLOOD_MAX_BURST` was added for exactly this invariant and got applied to one of five sibling loops.
- Bit: the sim's heartbeat, CAN-bus, `sim alive` and plot loops (2026-08-10).
  Windows monotonic advances through suspend, so one suspend/resume produced 324,008 lines (6.8 MB) from a single poll pass, all carrying the identical tick - and the giant write then hit the send path's backpressure mishandling (class 16's mirror, same round).
- Sweep: grep every `while` whose condition compares a schedule variable against now; each names its per-pass cap and its re-anchor, or is exempt because its backlog is data that must not be dropped (a capture drain is; a heartbeat is not).

### 37. An async read-modify-write spanning an await without a lock
- Invariant: an async method that reads shared state, awaits, and then acts on what it read holds a lock across the span, or states why an interleaving mutator is impossible.
  The single-threaded loop removes data races, not logical ones; every await is a yield point where a sibling call can run.
- Bit: three instances in one round (2026-08-10, store.py), all probe-confirmed.
  - `delete_range` ran outside `_sweep_lock`, so a size sweep applied its precomputed trim target to a capture a concurrent purge was already shrinking: ~5000 rows destroyed beyond what the cap required.
    The `_sweep_lock` docstring documented this exact mechanism for the two sweeps and the third bulk-deleter never took it.
  - two concurrent `start_session` calls both saw the same active session across `stop_session`'s end-marker await, both closed it, both opened one: overlapping sessions (forbidden by SPEC) plus a permanently stranded open row.
    Fixed with a store-level `_session_lock`.
  - `start_session` sampled `_next_id` while up to 10,000 enqueued lines had no ids yet, so a pre-session backlog landed inside the new session; fixed with a write-queue drain barrier before the sample.
- Sweep: for every async def in store.py and the port manager that both reads and writes shared state with an await between, name the lock it holds or the reason interleaving is safe.
  Specifically, every bulk-delete path runs under `_sweep_lock` and every sessions-table open/close mutation under `_session_lock` (grep the delete chunks and the session methods, verdict per site).

### 38. A reset re-run missing the discipline the first run has
- Invariant: a path that re-runs initialization (reconnect, capture reset, re-seed, re-prime) carries every ordering discipline the first run acquired - staging, generation guards, watermark handling - or states per omission why the re-run cannot need it.
  First-run bugs get fixed where they were found; the re-run path quietly keeps the old behaviour.
- Bit: `resetForDbReset` re-ran the backfill WITHOUT the staging the first connect has (staging existed only between onopen and the first drain), so live rows in the reset token's own frame advanced the watermark before the re-seed's fetch resolved and every history row was dropped - 0 of 5 survived in the probe, on the exact path whose own comment says it exists to fix "the terminal stayed empty".
  The first-connect path had the discipline; the reset re-run was written later and did not inherit it.
  Class 25's "birth is the forgotten half" phrased for re-initialization: the re-run is the forgotten twin of the first run.
- Sweep: enumerate every caller of each initialization routine (backfill, seed, prime, subscribe); for each caller beyond the first, diff the setup it performs against the first caller's, line by line.
  Any discipline present in one and absent in the other is the finding unless the omission is argued.
  A test drives the re-run with the same adversarial ordering the first run is tested under.

### 39. A raced task orphaned by the exceptional exit
- Invariant: every task a function creates to race against another (create_task, ensure_future) is consumed - awaited, or cancelled and awaited - on every exit path, the exceptional ones included.
  A task left holding an exception nothing retrieves has asyncio print its traceback to stderr at collection time, which is GC-timing dependent: clean locally, noise on a loaded runner, and a broken contract for any caller that promised a quiet stderr (class 35's territory).
  Confirmed 2026-08-11: the tail follow's staging abandoned its in-flight recv when the snapshot raised on a closed pipe; the socket teardown resolved it with ConnectionClosedOK and the never-retrieved traceback failed the pipe-closed test on the CI windows py3.11/py3.13 legs only.
- Two traps in testing it: a fake that merely hangs cannot reproduce the orphan (loop shutdown cancels pending tasks, and a cancelled task files no report), and calling `.exception()` on a done task already marks it retrieved.
  The discriminating fake resolves the raced task with an exception *after* the exit under test, the way a real teardown does.
- Sweep: for every `create_task`/`ensure_future` whose result is raced (asyncio.wait, FIRST_COMPLETED, gather with return_exceptions absent), walk each exit of the enclosing function - returns, raises, and the branches of the race - and name where the task is consumed.
  Any exit that can leave it unconsumed while it may still resolve with an exception is the finding.
  The test forces the teardown shape: exit taken, then the orphan resolved with an exception while the loop and its exception handler are still alive, then gc.collect.

## The legs

A round is these legs, run in this order; each leg owns its output list.
Every leg records what it refuted, with the probe that refuted it: the capture-lock and plot-seed-gating refutations (2026-08-01) each exist to stop the next reader from making the same plausible wrong change.

1. **Registry leg** - executes every sweep under the sweep discipline above and files the verdict lists.
   Runs first because it is mechanical and its results seed the other legs: 9 of the 18 findings in the last two fix rounds were repeat-class instances a sweep would have caught earlier.
2. **Measurement leg** - drives the real stack and measures; fixes only what measurements justify.
   Owns: the sim demo end to end, a live daemon lifecycle (start, collide, stop, crash), the CLI through the installed console scripts, the web UI in a browser, the real board on the bench, and class 12's probe checklist.
   Runs per platform; Windows console and socket semantics cannot be asserted from CI, so this leg includes the Windows machine.
   Highest severity yield of any leg: the sim brick (`can tx 7FF`), the 0.70 s /devices freeze, the BOM config failure, the phantom ttyS* ports and the running-session export 400 all came from execution, not reading.
   **A fix this leg justifies leaves a check behind, and the check pins the mechanism, never the elapsed time.**
   A measurement is a fact about one machine on one day; a wall-clock threshold in the suite encodes that machine, goes flaky under load, and gets rerun rather than read (this project's own timing assumptions produced class 21 and a Windows CI hang).
   Assert the thing that made it fast:
   - the **plan**, via `EXPLAIN QUERY PLAN` off the connection's trace callback (class 20)
   - the **bound**, where one exists: that a query carries its `since_id` anchor, or that a loop reclaims at most N per call. An unbounded version passes a correctness-only test.
   - the **structural property**, where the win is architectural: the join-pool test starves the default executor to one occupied worker and asserts `stop()` still completes, which is "it cannot queue behind anything", not "it was quick".

   Evidence, from one round: of four performance fixes, the two with plan checks caught a regression before commit (`/plot/channels`, 90 ms to 208 ms) and the two without shipped one (`/lines?port=&chan=`, 0.09 ms to 319 ms).
   Not every speedup earns a check - it must be cheap and deterministic, or it becomes the flaky test everyone reruns - and no check replaces this leg, because the 0.70 s freeze and the 36 s regex stall were both found by execution and by nothing else.
   Record the numbers in `REVIEW_LOG.md` so the next round can compare rather than re-derive.
   Probe discipline, each rule bought with a wasted or misread probe:
   - Assert visibility, not text presence: a `hidden` "reconnecting" chip read as live through `textContent` and filed a false alarm.
   - A 4xx answered to your own probe is a probe defect, not a result; fix the parameters and retry. `/plot/series` went unmeasured for a whole leg because its 422 was logged as a gap.
   - Keep the human visual check in this leg, and repeat it when behaviour could be luck-dependent: M5 showed full, partial or empty charts depending on the load, so repeated reloads were the discriminating check and a single look was not.
     That one visual check found the defect no automated probe had.
   - Never build an ambient control channel to reach inside a running daemon.
     A leg wrote a `sitecustomize.py` on `PYTHONPATH` that opened a socket and `exec()`d what it received; the permission classifier refused to load it three times.
     In-process measurement (killing the store writer, reading back per-connection PRAGMAs) needs a deliberate, reviewed test hook or it does not happen.
     "It is only a probe" is how a remote-code-execution surface gets built.
   - Read the model or the signature before writing the probe body; never guess field names.
     Four instances now: invented subcommands measuring the unknown-command path (twice), and an `/assert` sweep posting `patterns` where the model has `expect`/`forbid`, so three cases all measured the same "at least one pattern is required" error.
     A probe naming nothing real answers a different question and reads as a pass.
3. **Invariant legs** - each owns one cross-cutting invariant across the whole tree, candidate invariants taken from SPEC and CLAUDE.md mandates that have no registry entry yet.
   These exist because the module partition hides cross-cutting classes: 99eab7c ran ten agents by module plus a seams agent and repeat classes still leaked; seams between modules are not one invariant over all modules.
4. **Coverage and artifact leg** - runs coverage without the floor, reads uncovered branches in shipped paths as candidate dead branches, and executes the class 15 sweep.
   Measured 2026-08-01: total 77.6% against the 55% floor, so the floor alerts on nothing.
   cli.py reads 33% and daemon.py 63% in-process because the suite drives them via subprocess, so their gaps need manual disposition or subprocess coverage collection.
   Read the uncovered lines as a list of **untested request parameters**, not only of untested code: the miss that mattered was `POST /purge {before_ts}`, the one destructive selector with no test, both of its branches shipped unexercised.
   The leg's output is a verdict per branch, not a fix per branch: of the five branches it flagged in 2026-08-01, only `purge --before` held a defect, and the other four were driven and ruled correct, which is the useful negative result.
   Reach for the unit under test when the request layer cannot express the precondition (a table bound needing a thousand distinct addresses, a trim needing a protected session larger than the cap); a test that cannot reach the branch is how those two stayed uncovered.
   Dead-by-design and needing no test: every `ctypes`/`msvcrt`/`SIGBREAK` branch (Windows, see the measurement leg), and `drain_counted`, which needs a native serial port.
5. **Module leg** - deep single-module reading, kept because genuinely new classes still come from it: most of 99eab7c's and 0c676ec's volume, and class 22 in the 2026-08-01 round.
   Pick modules by *least prior attention*, not by size or suspicion: `lockfile.py` (untouched in 20 commits) and `can.js` (never reviewed, outside the Python coverage report) produced four findings and the round's new class.
   Read for whatever is wrong, not for the registry; the sweeps already cover the registry, and this leg's value is the shape nothing names yet.
   File the list of modules deliberately not read; it is the next round's starting map (the current one is in `docs/REVIEW_LOG.md`).
6. **Test-quality leg** - revert-verifies each new regression test, hunts tautological and platform-inert tests, checks that asserted behaviour matches the attack direction.
   Prior evidence: one test asserted the DNS-rebinding attack backwards (99eab7c); two tests were tautological on Linux (4d7b4ef).
   Revert-verification belongs in the fix step itself, not only in this leg: in the first round run this way, three of six fix agents caught a non-discriminating test in their own work before reporting it.
   The shapes seen so far:
   - a plan test that explained a hand-written copy of the query rather than the one the daemon issues
   - a CLI test where the daemon rejected the input first, so the client fix was never exercised
   - a guard using `pytest.raises`, which a skip also satisfies
   - a test asserting a specific errno, which asserts more than the invariant: Windows drops the SYN where Linux refuses it, so `ConnectionRefusedError` was the wrong evidence for "the listener is closed".
     Ask of every test what it would take to make the assertion true on the OS it was not written on.
   - a test asserting on the wrong surface: the enum-cap test asserted `charts.has("s7") === false`, but an enum channel renders as a *digital lane*, so it held either way.
     Revert-verification caught it, which is the argument for doing that on every test rather than the ones that look risky.
   - a fix in two halves where only one is load-bearing: `can dump -f` charged per-frame drops to a "the daemon is gone" bound *and* measured that bound in iterations rather than seconds.
     The counter-split test passed with the counters shared again, because the wall-clock half alone cures that symptom.
     Revert each half separately, or the test pins the half you did not break.

   A fix with nothing to revert (a test covering an existing branch) is verified by mutating the source instead: break the branch, watch the test fail.
7. **Fix-diff leg** - runs last, on the round's own diff: re-read every hunk for the platform it was not written on and against the registry invariants.
   Evidence: 2 of 12 findings in 4d7b4ef were Linux regressions from the Windows rounds (the Windows-only port probe, the backfill staging path).
   The diff includes the round's new tests.
   Narrowed to source files on 2026-08-01, on the argument that the test-quality leg owns tests, and the one thing this round shipped broken was a new test that could not pass on Windows.
   Two legs each assuming the other covers tests is how it escaped.

## The two questions

Ask both at the end of **every major stage** - each leg, each fix batch, before every commit of substance, and again before closing the round.
Not once at the end: the answers are cheapest to act on while the work is still in hand, and both questions below were first asked by the owner after a stage was already reported as done.

1. **What am I least confident about here? Go and recheck it.**
   Name the specific claim, not a general area, and re-drive it rather than re-reading it.
   On 2026-08-02 this produced, in one pass: a `+port` fix whose mirror case had never been measured (562 ms, and only a baseline measurement showed it was pre-existing rather than a new regression), and a C1 close that had covered two of four sites.
   The candidates are reliably: a fix whose *measurement* covered one direction only, a class filed closed without a mechanical enumeration, anything reasoned about rather than driven, and anything whose verification needs a platform or device you do not have (say so explicitly instead of quietly counting it as done).

2. **What should we have checked that we have not thought about?**
   Distinct from the first question, and harder: it asks for the gap, not the doubt.
   Work outward from what the change *touches* rather than from what it fixes - the sibling caller, the other endpoint with the same shape, the surface that reports on it, the consumer downstream.
   On 2026-08-02 this produced the `/wait` and `/assert` subscribers silently dropping the line being waited for, which no leg had looked at because the round's WebSocket finding had been framed as a *streaming* problem and those two are not streaming endpoints.
   A finding phrased as "X is broken in context Y" hides "X is broken" - ask what else is in X.

Record both answers, including "nothing found", in `docs/REVIEW_LOG.md` for the stage.
A stage with no answers filed did not ask.

## Exit criterion

A round does not end when the agents stop reporting; it ends when all of the following hold.

- Every registry sweep was executed and its verdict list, complete per the sweep discipline, is filed in `docs/REVIEW_LOG.md`.
- Every finding of the round is closed class-wide; each new class has a registry entry, and its sweep was run before close.
- The measurement checklist ran on both platforms, with numbers recorded and a ruled-out list.
- The coverage report was reviewed and every uncovered branch in a shipped path is marked dead-by-design or now covered.
- Every new regression test was verified to fail with its fix reverted (or its branch mutated, where there is no fix).
- The fix-diff leg reviewed the round's own diff and reported.
- The two questions were asked at every stage and their answers filed, including the empty ones.

The evidence a round must produce: the sweep verdict lists, the measurement and ruled-out log, the coverage disposition list, the revert-verification list, the fix-diff report, and the two questions' answers per stage.
That evidence is filed in `docs/REVIEW_LOG.md`, one section per leg per platform; a claim in the close-out table with no section behind it does not count (the 2026-08-01 round closed with the classes 1-20 lists unfiled and unrecoverable).
The campaign, as opposed to the round, ends when a full round produces no new defect class; repeat instances found by sweeps prove the sweeps work and do not extend the campaign.
