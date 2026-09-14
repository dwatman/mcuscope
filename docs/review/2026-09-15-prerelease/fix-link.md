# Fix batch: link and lifecycle

Files: `daemon.py`, `serial_link.py`, `sim.py`, `protocol.py`, `tests/test_daemon_startup.py`, `tests/test_port_health.py`; new `tests/test_prerelease_link_fixes.py` (test ids below are in it unless named).
Revert-verify driver and output: `~/tt-data/prerelease-2026-09-15/fix-link/mutate.py`, `mutate.out`. Each mutation restored from a copy; 21 of 21 killed.
No daemon was started, so no startup logs were written.

## Fixed

| Finding | Change | Test | Revert-verify |
|---|---|---|---|
| A-7 | `Server.handle_exit` schedules `store.stop_subscribers` with `asyncio.get_running_loop().call_soon_threadsafe` | `test_a_signal_landing_in_loop_code_schedules_the_sentinel`: a real `raise_signal(SIGTERM)` inside a task; asserts the call is deferred and runs with no current task | direct call restored: killed |
| C-9 / F-31 | Test only: real uvicorn `Server`, no `_serve` stub | `test_serve_exits_3_when_the_bind_fails`, `test_serve_exits_3_on_ctrl_c_before_the_server_started`, `test_serve_returns_normally_once_the_server_started` | P66 `if False`: killed; `if True`: killed; `except KeyboardInterrupt` removed: killed |
| C-7 | `protocol.int_arg(lo, hi=None)`: `is_decimal_token` grammar plus range, an argparse `type=`. Used by `mcuscoped --port` (1..65535) and `mcu-sim --tcp-port` (0..65535), `--drop-response` and `--flood` (>= 0). `--flap` uses `sim._seconds_arg`: `parse_plot_value` (finite ASCII number) and >= 0. `_tcp_port_arg` and the `_apply_overrides` range check are deleted (dead) | `test_sim_integer_flags_refuse_what_int_would_take` (27 cases), `test_sim_integer_flags_refuse_out_of_range`, `test_flap_refuses_a_value_that_is_not_a_finite_duration` (nan, inf, -inf, 1e999, -1, `1_0`, ` 1`, `٥`, `+1`), `test_daemon_port_flag_is_on_the_grammar_and_bounded`, `test_sim_flags_still_take_their_documented_values` | each flag back to `int`/`float`: killed (5); sign strip removed, `hi` ignored, flap negative allowed: killed (3) |
| C-6 | `_write_bytes` books health inside `_write_lock`. The streak reset moved from `_on_disconnect` into `_close_link_locked`, which the reader's `finally` now calls instead of its inline copy | `test_two_failing_writes_in_two_threads_both_count`, `test_a_close_during_a_failing_write_ends_the_streak_after_it`, `test_a_late_disconnect_callback_keeps_the_next_links_streak`; `test_port_health.py::test_write_failures_are_counted_named_and_reset` now drives `_close_link_locked` | health outside the lock: killed (2 tests); reset back in `_on_disconnect`: killed (2 tests) |
| C-5 | `_carried` holds a fifth field, the port's `_WriteHealth`, restored on attach | `test_a_reattach_keeps_the_last_write_error[replace]`, `[detach then attach]` | field not restored: killed; reset removed from the locked close: killed |
| C-4 | On replace, the new port's decoder calls `PlotDecoder.adopt(old.plot_decoder)` after `_detach_locked`; the old port's defs replace primed ones per sid | `test_a_reattach_decodes_with_a_def_stored_after_its_prime` (end to end: stored point is -1.0), `test_adopt_replaces_same_sid_and_keeps_the_rest` | adopt call removed: killed; adopt keeps existing: killed |
| F-32 | Test only | `test_declared_kinds_skips_the_name_of_a_bits_group` | P69: killed |
| F-3 | `test_daemon_startup.py`: asserts `"127.0.0.1:9870" not in quiet` | itself | not mutated: the assertion only got stricter |

Differs from the spec:

- C-5: the spec said carry `_WriteHealth(0, last_error, last_ts, None)`. The reset form survived its mutation because `stop()` already ends the streak through the locked close, so the port's health is carried as it is.
- C-9: uvicorn exits 3 by itself on a bind failure (`Server.startup`), so the bind test pins the contract but cannot kill P66. P66 is killed by the Ctrl-C-before-start test.
- Ran: owned test files plus `test_regressions`, `test_hardening`, `test_daemon_r2026_09_12_server`, `test_sim_tcp`, `test_review_r2_config`, `test_decode_per_port`, `test_plot`, `test_e2e`. Result: 510 passed, 1 failed (owed below). New file: 3 runs, all green. Ruff is clean on my files.
  - `ruff check mcuscope/` reports F821 `_def_decoders` at `server.py:1860`, in the daemon-core batch's file while it is mid-edit.

## Not done / owed

- `host/tests/test_regressions.py:1628-1638` (`test_port_override_is_bounded_like_the_config_key`) fails: a bad `--port` is now an argparse usage error. Replace the loop body with:

  ```python
  for bad in ("99999", "0", "-1"):
      with pytest.raises(SystemExit) as exc:
          daemon_mod.main(["--port", bad])
      assert exc.value.code == 2
      assert "argument --port: must be 1..65535" in capsys.readouterr().err
  ```

- CHANGELOG (behaviour changes):
  - `mcuscoped --port` with a bad value exits 2 (usage error) instead of 1. Non-ASCII digits, `+`, `_` and padding are refused.
  - `mcu-sim --tcp-port`, `--drop-response` and `--flood` take ASCII decimal digits only. `--drop-response` and `--flood` refuse negatives, which used to mean off.
  - `mcu-sim --flap` refuses `nan`, `inf`, negatives and forms like `.5` (write `0.5`).
  - A port reconnect keeps `last_write_error` and `last_write_error_ts`.
- SPEC 7 (lines 1361, 1378-1379): optional. It could say the fault flags take a non-negative decimal integer and `--flap` a finite number of seconds >= 0. No SPEC text contradicts the change.
- `REVIEW.md` class 22 sweep: add `argparse type=int|float` as a site kind; each is `protocol.int_arg` or a grammar-gated float type.
  - `cli.py` is typer, outside this sweep; the CLI batch owns it.
- A-7 new class candidate ("loop-owned state mutated from a signal handler"): registry entry for the orchestrator.
  - Sweep: every `signal.signal` handler and every `handle_exit` override; each only sets flags or schedules through `call_soon_threadsafe`.
  - `_release_pid_on_terminating_signal` complies: it touches no loop state.

## The two questions

1. Least confident, rechecked:
   - A-7 uses `get_running_loop()` inside the handler. It holds while uvicorn installs its handlers: they run only on the main thread, inside `serve()`. A call with no running loop would raise.
     - Driven with a real signal on Linux.
     - The Windows `/shutdown` path (`raise_signal` from a loop callback) is reasoned only, not run.
   - C-6: the race tests need a hook between the health load and its store, and use the exception's `__str__` for it.
     - Both failed against the unlocked form. They depend on `str(exc)` being read in that window, which the current code does.
   - C-4 `adopt` assumes the old decoder is never older than the prime. Reasoned: the prime reads rows the old port had already consumed. Driven end to end for the same-width case only; the width-change case was not driven.
   - Nothing was run on Windows.
2. What we should have checked:
   - Other argv surfaces: `tools/mcu_sim.py` re-exports the parser (no change needed). `mcu daemon start` passes `str(int)` to `--port`, so it cannot hit the new refusal.
   - C-3's reattach window (the alias is absent from `_ports` during a replace) is unchanged: it is an owner decision.
   - `_close_link_locked` now also ends the streak on the stop path, where the reader outlived its join. That is consistent with SPEC 3.4, but no test covers that exact path.
