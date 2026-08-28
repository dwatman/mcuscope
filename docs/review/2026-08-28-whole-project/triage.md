# Round 2 triage rulings (orchestrator)

Fix batches launch after all legs report; batches are partitioned by file to avoid agent collisions.
Every fix: revert-verify its test, ruff+suite green, no em/en dashes, class-wide close.

## core-daemon leg

- CD1 (MED, confirmed) unbounded int query/body params -> 500 on 7 endpoints.
  RULING: fix. Pydantic bounds: since_id/id_from/id_to le=2**63-1 (SQLite INTEGER), last_ms and other ms fields le=10**15, decimate le=10**9 (fixer may pick tighter, consistent with existing bounds style). Enumerate ALL int params mechanically, not just the probed 7. SPEC 3.3.1 tighter-bounds list gains the rule. Tests drive each 422 refusal.
- CD2 (MED, confirmed) detach handle-close on default executor, serial_link.py:348.
  RULING: fix, use _join_pool. Structural test like the existing join-pool starvation test: starve default executor, assert detach close completes.
- CD3 (MED-LOW) session export: unbounded size into /tmp (tmpfs=RAM), temp leak on client disconnect, unlink/reopen window.
  RULING: fix by (a) mkstemp(dir=<db directory>) instead of system tmp: bounds it to the DB's own filesystem, kills the world-writable-window and tmpfs-RAM cases; drop the early unlink; (b) replace BackgroundTask with a FileResponse subclass whose __call__ unlinks in finally (covers disconnect). No row ceiling added. SPEC notes the temp location beside the DB.
- CD4 (LOW-MED) same-origin guard wording overclaims (no-cors subresource GETs carry no Origin).
  RULING: docs fix only: SPEC 324 + guard docstring gain the caveat (guard blocks reads/CSRF-with-Origin; blind cross-site GET triggering is inherent to browsers). No code change.
- CD5 (LOW) pjstream allows 255.255.255.255.
  RULING: fix: refuse ip == IPv4Address("255.255.255.255") too; docstring/SPEC state exactly what is enforced (multicast, unspecified, limited broadcast; directed broadcast is indistinguishable without a netmask and stays allowed but inert). Test the refusal.
- CD6 (LOW) attach primes before port cap and store liveness.
  RULING: fix: cheap pre-checks before prime (cap count, store started), authoritative checks stay under the lock; a stopped store must answer PortError/400 not 500. Test the shutdown-window refusal.
- CD7 (LOW) dead one-shot regexp closure registered at Store.start().
  RULING: fix: drop the registration (live paths register their own); failure mode becomes loud "no such function: REGEXP" for any future direct caller. Run match tests.

## webui leg

- W-H1 (HIGH, confirmed) seed path duplicate (line_id, name) permanently misaligns ys vs xs.
  RULING: fix: per-row name-uniqueness gate in mergeSeedSeries matching the wide-form collapse rule (last row for a (line, name) wins, same as the daemon's wide collapse; fixer verifies which row wide keeps and mirrors it). Node test from the probe.
- W-M1 (MED, confirmed) JS enum parser accepts -0 on unsigned; daemon rejects on the sign char.
  RULING: fix: reject on the sign character exactly like protocol.py:643. Test mirrors tests/test_protocol.py:480.
- W-M2 (MED, confirmed) markDigitalDirty clears _sizedirty unconditionally; hidden panel loses the repaint.
  RULING: fix: clear the flag only for lanes actually painted (or let redrawDigital own the clear). Node test from probe p2.
- W-M3 (MED, confirmed) idle tick clobbers the cursor readout with pendingVal.
  RULING: fix: the live-value write must not run on an idle tick while a cursor readout is showing; apply pendingVal only when the lane actually repaints or no cursor override is active. Node test from probe p3.
- W-L1 RULING: fix: apply CMD_HISTORY_MAX in loadCmdHistory (slice on load).
- W-L2 RULING: fix: clear portColorCache in resetForDbReset and cap it at 64 like the sibling maps.
- W-L3 RULING: fix: re-test seed-path channel/group names against PLOT_NAME_RE at seedDef.
- W-L4 RULING: fix as hygiene: make charts and panes watermarks share one shape with an explicit empty-set answer (0), no Infinity path.
- W-L5 RULING: no change; note stays in the report (bound is 2x by design, class 26).
- W-L6 RULING: fix class-wide: shared JSON case list (plot_grammar_cases.json style, like csv_cell_cases.json) of (!pd, valid?) and (!p/!ps, valid?) driven from both test_protocol.py and a new .test.mjs. This is the class-19 close for the plot grammar; M1 becomes one of its cases.
- Manual-verify items (cursor pixel alignment, color-picker focus cleanup): stay open with browser leg.

## cli-config leg

- F1 (HIGH, confirmed) daemon start clobbers live daemon's pid record and reports success with a dead pid.
  RULING: fix: (a) success requires the spawned child alive AND /status pid == proc.pid; if the URL answers with a different pid, report "another daemon is already serving (pid X)" as a failure, write nothing, remove nothing; (b) the record write goes through a guarded path that refuses to overwrite a record naming a live process (reuse pidfile's liveness check). Regression test from the two-concurrent-starts probe shape (can be driven with a fake child).
- F2 (MED) RULING: fix: all three dispatcher arms emit exactly one JSON object on --json (match the usage-error arm). Drive each arm.
- F3 (MED) RULING: fix: compute the pid path before spawning, and wrap both call sites' OSError into the exit-code contract (message naming the path); test with XDG_DATA_HOME as a file.
- F4 (MED) RULING: fix: load_config catches OSError -> ConfigError naming the file. Test unreadable file and directory-as-config.
- F5 (MED) RULING: fix: _describe validates started (finite number in a sane time_t range) and formats defensively (catch OverflowError/ValueError/OSError, omit the timestamp); LockError must always be constructible from a corrupt record. Test with 1e300.
- F6 (MED) RULING: fix: mcu purge --before-days requires > 0, refusal message unique to the flag; test the refusal. --all stays the only route to everything.
- F7 (LOW) RULING: fix: both removals in _stop_running_daemon go through _remove_pid_record.
- F8 (LOW) RULING: fix: hoisting must track value consumption (a token consumed as a value_opt's value is never a boundary), not compare the literal previous token.
- F9 (LOW) RULING: fix: hoist -p only as exact '-p' followed by a value or '-p<digits>' attached; '-pulse' stays for click's no-such-option error. Tests for both.
- F10 (LOW) RULING: fix: unique temp sibling names (pid or mkstemp) in update_check and config writers.
- F11 (LOW) RULING: fix: daemon startup refusals print to stderr; update any tests asserting stdout.
- F12 (LOW) RULING: fix: session name resolution pages through /sessions until found or exhausted.
- F13 (LOW) RULING: SPEC edit: --token is the global option in the mcu daemon start row.
- F14 RULING: fix all three one-liners: start timeout env read at call time; --host uses `is not None` and refuses empty like --port; signal registration guarded for non-main-thread (try/except ValueError, record and continue).

## spec-drift leg

- SD1 (MED, confirmed) wrong-typed bool config keys warn-and-default; SPEC 3.3 says fail the load.
  RULING: SPEC wins (the agent's option a): _as_bool raises like _as_int/_as_str, naming file and key. Tests drive all three bool keys refusing. The 3.6 typo argument decides it.
- SD2 (LOW) sim does not sanitize outgoing lines. RULING: fix sim.py emit path to replace non-printable bytes like monitor.c:213; test with a mark containing 0x01.
- SD3 (LOW) sim accepts a >0x7F input byte, firmware rejects. RULING: docs: SPEC 2.1 gains a sentence that a receiver may reject a non-ASCII byte and the two references differ (pattern of 2.4). No code change.
- SD4 (LOW) RULING: SPEC drops the hardcoded 27 count ("the *.test.mjs files").
- SD5 (LOW) RULING: ARCHITECTURE drops the stale 280 count.
- SD6 (LOW) RULING: README sentence corrected (suite attaches sim:// in process; mcu-sim is for a daemon in another process).
- Smaller notes: README config sample gains [plotjuggler]; README repo layout gains the missing docs/ files; SPEC 8 count reworded without a number. CLAUDE.md venv-version note: local drift, no repo change.

## firmware leg

Root-cause class across FW1/FW2/FW3 and the i2c/spi partial-fill: the monitor reads callee-filled memory beyond what the contract obliges the callee to write. Candidate registry class 41; sweep = every shim/handler output buffer or struct, checked for bounded read or explicit fill contract.

- FW1 (HIGH, ASan) emit_ok unbounded %s over g_resp. RULING: fix: bound the read (%.*s with the resp buffer size) AND state "NUL-terminated" in monitor.h/SPEC 5.2. Test: handler memsets resp full, ASan leg must stay green.
- FW2 (HIGH, ASan) cmd_info reads past extra when mon_info_extra fills max bytes. RULING: fix: call the shim with max-1 and force-terminate after return; contract line in monitor.h + INTEGRATION + port_template comment. Test with a filling shim.
- FW3 (HIGH, ASan) drain_can hands the shim an uninitialised frame. RULING: fix: memset the frame like cmd_can_tx does; contract note that unfilled fields read as zero. Fake shim must stop pre-zeroing (see FW8) so the memset is load-bearing; test with a fill-only-id/dlc/data shim.
- FW4 (MED) parse_plot_body misses SPEC 2.5 within-body name uniqueness. RULING: fix: pairwise check over channels+lanes (<=16+lanes, O(n^2) fine, no new statics) -> BADARG; SPEC 5.2's "one gap remains" stays true afterwards. Tests: both probe bodies.
- FW5 (MED) INTEGRATION.md kind-sigil paragraph contradicts code. RULING: rewrite to match code (tail fully validated, MONITOR_ERR_BADARG named).
- FW6 (MED) clockless port never rebroadcasts !pd. RULING: fix: poll-count fallback (documented constant, e.g. rebroadcast every MON_PLOT_PD_POLLS polls when tick_ms is NULL), consistent with monitor_mark's documented degradation; monitor.h/INTEGRATION state tick_ms optional plus both degradations. Test with .tick_ms = NULL.
- FW7 (MED) negative/out-of-table error codes on the wire. RULING: fix: emit_err clamps non-zero codes outside 1..9 to MONITOR_ERR_INTERNAL. Tests: -5 and 4242.
- FW8 (MED) fake shims gentler than a real port. RULING: fix the harness: uart_read over-return clamp test; can pop poisons then fills only contract fields; mon_info_extra/mon_spi_xfer real shims added for their data paths; i2c/spi RX buffers zeroed by the monitor before the shim call (plus contract lines); can-queue test raised past 64 to assert the per-poll bound; fake_feed/fake_uart_write gain bounds asserts.
- FW9 (LOW) RULING: fix: parse_plot_body refuses non-printable unit bytes (7-bit printable only).
- FW10 (LOW) RULING: SPEC edit: 5.4 wins; 2.1 notes the parser tolerates low control bytes mid-line.
- FW11 (LOW) RULING: INTEGRATION example made self-contained (tick var), plus one sentence on where volatile runs out (dual-core needs DMB).
- Downstream: charger-test vendors this module; note in the round close that monitor.c/monitor_cmds.c/monitor.h/INTEGRATION.md changed and need re-vendoring.

## sim-protocol leg

- SP-H1 (HIGH, confirmed) _decode_field accepts non-finite f4; NaN raises IntegrityError out of the store, Inf 500s /plot/series.
  RULING: fix at the decoder: non-finite f4 answers None (generic event), matching parse_plot_value and SPEC 2.5. Tests: all three bit patterns end-to-end (stored as generic event, /plot/series stays 200). pjstream's isfinite filter stays as defense.
- SP-M1 (MED, confirmed) serve_pty wedges in blocking os.write with no reader.
  RULING: fix: non-blocking master, unsent-offset resume, stall budget mirroring SEND_STALL_TIMEOUT_S; on a stall past the budget drop the backlog (sim output is disposable) and keep serving. POSIX-only test in test_sim_pty: no reader plus heavy output, thread stays live and recovers when a reader attaches.
- SP-M2 (MED, confirmed) pty slave left in canonical+echo mode.
  RULING: fix: set the pair raw at openpty (clear ECHO/ICANON/OPOST via termios on the slave). Test asserts the termios flags.
- SP-M3 (MED, confirmed) parse_command splits on any whitespace; monitor.c tokenize splits on ' ' only.
  RULING: fix: parse_command mirrors monitor.c tokenize byte-for-byte (space runs as separators, leading-space handling aligned with the firmware; fixer reads tokenize/recover_seq first and encodes the same L6 asymmetry the firmware has). Tests: the tab and vertical-tab probes agree with the sim/firmware verdicts.
- SP-M4 (MED, confirmed) sim reflects control bytes in responses. RULING: merged with SD2: encode_lines gains the printable-ASCII replacement (one site, covers all outgoing lines). Test: NUL-in-command reflection comes back replaced.
- SP-L1 RULING: fix: encode_lines([]) encodes to b"".
- SP-L2 RULING: fix: format_command/format_response_ok/format_marker/event emitters raise on embedded \n or \r, same spirit as the existing sigil refusal. Suite must stay green (upstream splits on LF, so no live caller can hit it).
- SP-L3 RULING: fix: format_can_event raises on an RTR frame carrying a payload, like every sibling inconsistency.
- SP-L4 RULING: fix: normalize_line strips exactly one trailing CRLF/LF/CR pair as its docstring states.
- SP-L5 RULING: fix: the sim's can filter honours can_filter_ext (ext-only filter passes only ext frames); test.
- SP-L6: folded into SP-M3.
- SP-L7 RULING: fix: _encode_wire enforces the 12-token cap locally next to its length check; /send answers 400; test.
- SP-L8 RULING: fix: mcu-sim --tcp-port bounds-checked as a usage error, not a crash file; test refusal text.

## registry 21-40 leg (3 violates, all confirmed)

- R35 (class 35) out_json unguarded on a closed stdout: every --json error exit becomes 0.
  RULING: fix: mirror the stderr half exactly (guarded write, suppress the pipe failure, repoint stdout at devnull so the shutdown flush cannot raise over the mapped code). Regression test from the probe shape: closed stdout, --json, unreachable daemon still exits 3.
- R30 (class 30) both C-suite wrappers trust make's exit code.
  RULING: fix: parse "(N)/(M) checks passed" in test_firmware_monitor.py run and asan wrappers, assert nonzero and N == M, mirroring the JS wrapper's guard.
- R39 (class 39) _stage_backfill orphans the snapshot task on cancellation.
  RULING: fix: the except BaseException arm consumes both tasks (cancel and await task too); test from probe39b shape (cancel mid-snapshot, no "Task exception was never retrieved").
- Cross-observation: non-ASCII --token crashes with a traceback and crash log (class 18/9 shape).
  RULING: fix: validate the token as ASCII at the CLI boundary (both --token and MCUSCOPE_TOKEN) with a usage-error refusal; test the refusal text. Belongs with the cli-config batch.
- Full verdict lists for classes 21-40 are complete in the report; file verbatim into REVIEW_LOG at close.

## test-quality leg (all against fd76735; worktree started stale and was corrected)

- TQ-F1 (HIGH) the torn-read/retired-socket fix has no test.
  RULING: add the cheap deterministic check the leg names: configure(True,a) then configure(True,b) -> _retired holds the old pair, still open (fileno != -1); third configure -> first socket closed. Lives in test_plotjuggler.py.
- TQ-F2 (HIGH) class-39 fix half B (_follow_ws) survives deletion.
  RULING: add the half-B test: drive _follow_ws with a pending recv and a handle raising BrokenPipeError, assert the recv is consumed (no "never retrieved"). Revert-verify against M31's deletion.
- TQ-F3 (MED) settings_pj echo test is tautological; blank-dest path undriven.
  RULING: fix the test: stub answers differ from what the test typed (blank dest -> stub returns previous dest and the field must show it; checkbox echo asserted against a differing answer). Also drive dest||null (blank sends null, never "").
- TQ-F4 (MED) parse_dest bracket-guidance branch untested and its message is wrong.
  RULING: fix the message to suggest bracketing the WHOLE input ("[2001:db8::1]:<port>"), and pin distinct refusal messages with pytest.raises(match=) on the cases that own one.
- TQ-F5 (LOW) conftest offline veto is a setdefault.
  RULING: plain assignment.
- Deferred to next round (filed): mutation pass over the firmware C suite, non-PJ webui_js tests, test_hardening bound tests.

## registry 1-20 leg (17 distinct confirmed)

- RG-F1 auto_vacuum applied, never read back, no-op on pre-existing captures.
  RULING: read back and warn, mirroring the journal_mode sibling three lines down. No VACUUM conversion (startup stall on big captures is worse than the plateau); the warning names the consequence.
- RG-F2 submit_line fast-fail bypasses _fail_write, so write_errors reads 0 while lines are lost.
  RULING: route the fast-fail through _fail_write before raising. Test from the dead-writer probe (write_errors must move).
- RG-F3 lines_rx: resolved by RG-F2 per the report; no separate change.
- RG-F4 serial_number attach never reports the resolved device.
  RULING: store the resolved device in a NEW field (self.device must stay None so reconnect re-resolves the serial); /status port object reports the resolved device when there is one. SPEC 3.4 port object updated. Test.
- RG-F5 active_session scans sessions on the loop.
  RULING: partial index (WHERE ended_ts IS NULL), added to the schema + in-place migration + SPEC 3.5 index list; EXPLAIN-plan check test per the house pattern.
- RG-F6 _daemon_errors omits ValueError (UnicodeEncodeError escapes). RULING: tuple gains ValueError like the probe sibling; plus the ASCII token validation already ruled (registry 21-40 cross-obs). Both.
- RG-F7 WS json guards catch JSONDecodeError only. RULING: align with siblings: (json.JSONDecodeError, ValueError).
- RG-F8 plot_export finally-close flush escapes. RULING: close inside the guarded region mapping to exit 1 like log export; test on a full-disk stand-in.
- RG-F9 ms timeouts unbounded client-side (OverflowError). RULING: client-side range guard on cmd/wait/assert timeout options with a refusal message; test.
- RG-F10 five commands read daemon fields unguarded. RULING: per-field guard at point of use (a _field sibling of _list_field vouching type), per the registry bullet; dispatcher TypeError arm stays. Tests with a stub daemon answering wrong shapes.
- RG-F11 = W-M1 (ruled). RG-F12 = SP-H1 (ruled). RG-F13 post-scale finiteness re-check missing in decode_plot_sample. RULING: folded into SP-H1 fix (re-check after *scale like plots.js:227). RG-F14 = FW4 (ruled).
- RG-F15 monitor.c emit_can_event does not bound the id to the flag width. RULING: mask the id to the declared width, mirroring the dlc clamp one line up; INTEGRATION notes the shim owns id validity. Test.
- RG-F16 monitor_mark accepts text starting with a tick sigil (forges a tick). RULING: refuse with ERR 2 badarg, mirroring format_marker. Test.
- RG-F17 config loader accepts baud above MAX_BAUD that the API refuses. RULING: lenient per-port convention: warn-and-skip the port entry, keeping saved-state round-trips consistent. Test.
- RG-F18..F20 UI missing upper bounds (MAX_BAUD, cap, MAX_TIMEOUT_MS). RULING: add the client-side upper bounds; low.
- Class-7 unasserted cell: RULING: add the test asserting "removed stale pid file" on the stale-stop cell.
- Class-39 observation: _spawn_sys orphans StoreError. RULING: _store_sys treats StoreError as expected shutdown noise (swallow with debug log); test no "never retrieved" warning.
- Verdict lists complete (incl. 4 delegated files); file into REVIEW_LOG at close.

## fix batching plan (launch after measurement leg lands)

Partition by file so agents never collide; each agent runs only its own tests; I run the full gates after integration.
- A1 store.py: RG-F1, RG-F2, RG-F5, CD7 (+tests incl. plan check)
- A2 server.py: CD1, CD3, CD4 (+SPEC wording), (+tests)
- A3 serial_link.py: CD2, CD6, RG-F4, class-39 _store_sys (+tests), PLUS routed from batch E: _encode_wire gains the 12-token outbound cap (format_command covers /cmd only; /send's raw path reaches _encode_wire directly), /send answers 400, test.
- B pjstream.py: CD5, TQ-F1, TQ-F4 - I do this one myself (delicate concurrency file)
- C1 cli.py/cli_argv/cli_daemonctl/cli_output/cli_client: F1,F2,F3,F6(purge),F7(pid record),F8,F9(argv),F12, RG-F6..F10, R35, R39-halfB test (TQ-F2), token-ASCII validation (+tests)
- C2 config.py/lockfile.py/daemon.py/update_check.py: F4,F5,F10(tmp names),F11(stderr),F14 trio, SD1, RG-F17 (+tests)
- D webui JS: W-H1, W-M1..M3, W-L1..L4, RG-F18..F20, TQ-F3, plus the shared plot-grammar fixture (new JSON + new test_plot_grammar_fixture.py + new .test.mjs; do not touch test_protocol.py)
- E sim.py/protocol.py: SP-H1+RG-F13, SP-M1..M4(+SD2), SP-L1..L8, R30-adjacent none (+tests; keep out of the fixture files)
- F firmware: FW1..FW11, RG-F15, RG-F16, R30 (wrapper count parse) (+C tests, INTEGRATION/SPEC 5 edits)
- G docs-only: SD3..SD6, FW10 (SPEC: 5.4 wins, 2.1 notes the parser tolerates low control bytes mid-line; not covered by batch F), smaller README/SPEC notes, F13 (SPEC token row), CLAUDE.md venv version line, pyproject coverage-comment figures (87%, cli 59%), SPEC 7 --garbage gains "(bypasses the outgoing sanitizer by design)" next to the SPEC 2.2 sim sanitization note.
- DONE by orchestrator between waves: pjstream batch B (broadcast refusal, bracket message, retired-socket test, message-match tests, all revert-verified); conftest veto assignment; sim RawJunk bypass keeping --garbage a real fault injector (revert-verified); REVIEW.md class 41 filed with the host-side sweep run clean (11 caller-owned buffer sites, Link.read contracts comply).
- Me: conftest TQ-F5, registry class 41, REVIEW_LOG filing, fix-diff leg, gates, commits.

## pending legs
(measurement)
