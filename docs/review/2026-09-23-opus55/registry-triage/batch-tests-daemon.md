# Batch tests-daemon

Test-only fixes on the daemon side. No source file is edited; a fix that turns out to need one is reported, not made.
Each fix is revert-verified: apply the mutant the finding names in a scratch copy, confirm the fixed test fails, restore.

## Files

- Tests you may edit: `test_e2e.py`, `test_assert.py`, `test_wait_repeat.py`, `test_serial_link_devices.py`, `test_reconnect.py`, `test_serial_link_attach.py`, `test_session_bundle.py`, `test_config_api.py`, `test_server_scope.py`, `test_server_live_verdicts.py`, `test_server_export_windows.py`, `test_sim_flags.py`, `test_store_reclaim_budget.py`, `test_store_writer.py`, `test_capture_lock.py`, `test_sim_pty.py`.
- No SPEC or AI_GUIDE section. `docs/REVIEW.md` is firmware-packaging's; hand it any sweep wording change.
- The emulated 15.625 ms clock used to find R21-1 is `quantclock.py` under `~/tt-data/mcuscope-2026-09-24/registry-leg/15-28/`.

## Findings

### R21-1 MEDIUM: `since_ts` test assumes a later row has a later stamp
- Checked: test_e2e.py:606-613 takes `cut` from the middle row's `ts`; one read burst shares one stamp, so on a 15.6 ms clock every newer row can share `cut`.
- Fix: write the rows with explicit, distinct `ts` through the store, or poll until a row with a stamp strictly above `cut` exists before querying.
- Verify: 20 isolated runs under the emulated clock, 0 failures (the leg saw 4 in 21 before).

### R21-2 LOW: `test_last_ms_window` needs two round trips inside 30 ms
- Checked: test_assert.py:127-143 writes `recent`, then makes two `/assert` calls; the third call's 30 ms window must still reach `recent`.
- Fix: widen the windows so the margin is seconds, not milliseconds (spin past `old_ts + 2 s`, narrow window 1000 ms), or write `recent` straight before the call that must see it.
- Verify: passes under the emulated clock with an injected 50 ms delay between calls.

### R21-3 LOW: `elapsed < 0.1` stands in for "refused before the first write"
- Checked: test_wait_repeat.py:326-332.
- Fix: spy `send_raw` and assert it was never called; drop the wall-clock bound.

### R21-4 LOW: `max(gaps) < 0.2` after the stall
- Checked: test_wait_repeat.py:350-369 already counts writes (`>= 5`, `<= 25`).
- Fix: drop the gap bound, or replace it with a count of writes in the window after the stall.

### R21-5 LOW: `/status` within 1.2 s during a fake 2 s scan
- Checked: test_serial_link_devices.py:80-87.
- Fix: the fake scan blocks on an `Event` the test sets only after `/status` has answered; no clock.

### R21-6 LOW: promptness thresholds with about 1 s headroom
- Sites: test_reconnect.py:110 (`< 1.5`, alternative 5 s), :146 (`< 1.0`, alternative 30 s), test_e2e.py:544 (`< 1.0`, alternative 2 s).
- Fix: raise the alternative the test discriminates against (monkeypatch the interval or timeout to 60 s) and give the healthy path a budget several times larger (e.g. 10 s).

### R27-6 MEDIUM (FC2-2): realpath trap with no positive control
- Checked: test_serial_link_devices.py:101 asserts `by_id is None`; nothing in the suite asserts a non-null `by_id`.
- Fix: a sibling case with `_by_id_map` answering `{realpath: "/dev/serial/by-id/x"}` asserting `dev["by_id"]`.
- Revert-verify: server.py:3797 `"by_id": None` fails it.

### R27-11 LOW (FC2-1): `_Sock` ignores `timeout`
- Checked: test_reconnect.py:696.
- Fix: `_Sock.read` records `(n, self.timeout)`; assert every sized read ran at timeout 0. Consider moving the three `link.py` doubles' tests (`_Sock`, `_Native`, `_NoCancel`) to a `test_link_*.py` file of their own.
- Revert-verify: link.py:149 `ser.timeout = 0` replaced by `pass` fails it.

### R27-12 LOW (FC2-3): `FloorClock` dispatches on the caller's name
- Checked: test_server_scope.py:25.
- Fix: count advances in `FloorClock`; both users (:60, :77) assert at least one.
- Revert-verify: mutant B2 (A2 plus the `_window_floor` rename) fails.

### R27-13 LOW (FD-3): `OneStepPerExecute` emulates `Connection.execute` only
- Checked: test_store_reclaim_budget.py:221.
- Fix: override `cursor()` too, returning a cursor whose `execute` steps once.
- Revert-verify: `_reclaim_pages` using `conn.cursor().execute(...)` fails on 3.13.

### R28-1 MEDIUM: shutdown step wrapped in `suppress(Exception)`
- Checked: test_store_writer.py:341-347; a hang becomes a swallowed `TimeoutError`.
- Fix: catch `StoreError` only (or nothing), so a timeout fails the test.
- Revert-verify: `Store.stop_session` awaiting a never-set Event when the writer is dead fails in 5 s.

### R75-1 LOW (Python half): hand-kept "every X" lists
- Sites: test_server_live_verdicts.py:123 READS and :152 WRITES, test_server_export_windows.py:276, test_server_scope.py:90, test_sim_flags.py:16.
- Fix: derive each from its source (`app.openapi()` query params, the `_resolve_port` callers, the sim's argparse actions), or assert the hand list equals the derived one. Derivation scripts: `derive75*.py` in the 71-80 scratch dir.

### R78-1 LOW: bundle-route leftover check globs the wrong prefix
- Checked: test_session_bundle.py:404 globs `mcuscope-session-*`; the bundle names its temps `mcuscope-bundle-*` (server.py:1679).
- Fix: glob `mcuscope-bundle-*`, with a positive control listing the dir during the build.

### R78-2 LOW: `assert not port._pending` cannot fail
- Checked: test_reconnect.py:1314; `_fail_pending` clears `_pending` itself (serial_link.py:739).
- Fix: stub `_fail_pending` to record only in this test, so `send_command`'s own cleanup is what empties `_pending`.
- Revert-verify: removing the four `_pending.pop(seq, None)` cleanups fails it.

### R78-3 LOW: `unhandled` recorder has no positive control
- Checked: test_serial_link_attach.py:550.
- Fix: a sibling where `_store_sys` re-raises and `unhandled` is non-empty.

### R78-6 LOW: `update_checker.enabled is False` holds for the environment, not the config
- Checked: test_config_api.py:287; conftest sets `MCUSCOPE_UPDATE_CHECK=0` and the environment wins.
- Fix: `monkeypatch.delenv("MCUSCOPE_UPDATE_CHECK")` in this test so the config decides; positive control with `check = true`.

### R78-7 LOW (non-store sites): absence checks with no positive control
- Sites: test_sim_pty.py:130-133 (show the slave flags set before the sim clears them), test_capture_lock.py:163 (the `os.open` spy records the lock path in a positive case), test_store_writer.py:77 (a writer death that does log "store writer died"), test_config_api.py:95 (`save_ports` output contains `[[ports]]` when ports exist).
- Per-site notes: `verdicts_py2.md` in the 71-80 scratch dir. The store sites are daemon-api's.

### FA-7 LOW: the failing sweep double is never shown to run
- Checked: test_assert.py:644.
- Fix: `boom` records its call; assert it ran.
- Revert-verify: store.py:3009 `trimmed = 0` fails it.
