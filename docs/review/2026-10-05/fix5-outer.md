# Fix batch outer5 (FD-OUTER3-1 to 6)

Scratch: `~/tt-data/mcuscope-2026-10-05/fix5-outer/` (one copy per file: `cli.py.orig/.new`, `cli_output.py.orig/.new`, `serial_link.py.orig/.new`). No daemon started.

## FD-OUTER3-1 `--last-ms` with a bound outside its window

- `host/mcuscope/cli.py:843` (`_absolute_window`): returns `lo, max(hi, lo)`. An inversion here can only come from the computed floor or ceiling, since `_clock_bounds` already refuses a user's own `--from` after `--to`.
- Test: `test_cli_last_ms_ceiling.py::test_a_bound_outside_the_last_ms_window_gives_an_empty_answer[--from|--to]`. It covers `lines`, all three `log export` forms and `can dump`: exit 0 and no rows. A positive control checks that the bound alone selects rows.
- Revert (`return lo, hi`): both cases fail.

## FD-OUTER3-2 episode open at stop, store still failing

- `host/mcuscope/serial_link.py:956` (`_close_unstorable`): the WARNING is now the full row text (count, first and last time, cause), not the count-only line. This holds at every close, not only at stop.
- Test: `test_serial_link_rx_ingest.py::test_an_episode_open_at_stop_with_the_store_still_failing_is_logged_whole`.
  - `submit_line_nowait` and `add_line` both raise `StoreError` through `stop()`, with a settable clock.
  - It asserts the exact logged text with both stamps and "disk is full", and that no sys row landed.
- Revert (original file): this test and the existing `test_an_open_unstorable_episode_is_recorded_at_stop` fail.

## FD-OUTER3-3 eviction warning

- `host/mcuscope/serial_link.py:1550`: the warning ends "(further evictions are not logged)" in place of the pointer to `carried_evicted`. The attribute stays as the latch and the test's count.
  - Not exposed on `/status`, because that would need server.py and SPEC 3.4.
- Test: `test_reconnect.py::test_carried_counters_are_bounded_and_evict_the_oldest` now counts every record containing "detached aliases: counters of" (exactly one) and checks that it names 'a0'.
- Mutation `if True:` on the latch: the test fails (before this change it passed).

## FD-OUTER3-4 reserved Windows names

- `host/mcuscope/cli_output.py:254`: `device = st is None and os.name == "nt" and is_reserved_name(real)`.
- `host/mcuscope/cli_output.py:224`: the set now matches `ntpath.isreserved`, adding CONIN$, CONOUT$ and COM/LPT with superscript 1, 2 and 3. COM0 and LPT0 stay ordinary, as CPython has them.
- Tests in `test_cli_output_reserved_names.py`:
  - New `test_a_reserved_name_that_stats_as_a_regular_file_is_replaced_whole`: with `os.name` patched to "nt", an existing `con.csv` gets a temp, stays untouched until commit, then is replaced.
  - New recognised cases: `CONIN$`, `conout$.txt`, `COM¹`, `LPT³`.
- Revert:
  - Precedence reverted: the new test fails.
  - CONIN$ and CONOUT$ removed: 2 fail.
  - Superscripts removed: 2 fail.

## FD-OUTER3-5 and 6 guide wording, move-aside trigger

- `AI_GUIDE` (`host/mcuscope/cli.py`):
  - Line 3175 now reads "plot lines (!p, !ps) past the 256 plot names per port".
  - Line 3166 now says "When <why> contains" in place of "ends".
- Comment at `cli.py:271` now says "!p/!ps lines past the plot name cap".
- `cli.py:197`: new `MOVE_ASIDE = "aside before restarting"`, which `status` (line 226) keys on.
- Tests now import it rather than copying it:
  - `test_store_capture_wal_aside.py` imports `MOVE_ASIDE` from `mcuscope.cli`. Its tests that assert the store's real `capture_error` contains it (lines 174, 241 region) therefore tie the store's wording to the CLI's trigger.
  - `test_cli_status_capture_health.py::test_a_move_aside_cause_says_then_restart` builds its cause from `cli.MOVE_ASIDE`.
- Revert:
  - Constant mutated: 3 store tests fail.
  - `then = "restart"` unconditionally: the status test fails.
- `test_cli_contract.py` passes.

## Class 100 sweep: AI_GUIDE (coordinator follow-up)

Changes in `host/mcuscope/cli.py` (`AI_GUIDE`):

- V100-1, line 3158: the `mcu status` port states now include "held (disconnected on request)", pointing at the `manual` reason.
- V100-2, line 3331: "--changes with --decode", "--deadband ... with --changes".
- V100-3, line 3339: the `can tx` synopsis is split into a DATA form and a `mcu can tx 1A3 --rtr 4` form that "takes no DATA".
- Sysrq observation, line 3240: "On a monitor port, follow it with `mcu send ""`, as for `--eol none`".
- Assert example, line 3292: `--forbid "PANIC"` replaces `--forbid "ERR"`. The sim has no PANIC (`grep PANIC mcuscope/sim.py` finds nothing).

Re-run on my own `mcuscoped --sim` (port 19340, throwaway TOML and dirs; scripts `examples.sh`/`held.sh`, output `examples.out` in scratch).
`run.py` was not used, because it hard-codes 19260, which is busy.

| Example | Result |
|---|---|
| `assert --last-ms 10000 --forbid PANIC` | PASS, rc 0 |
| `plot export ... --decode --changes` | rc 0 |
| `plot export ... --decode --changes --deadband ramp=0.05` | rc 0 |
| `plot export ... --changes` without `--decode` | rc 1, "changes requires decode" (the reason for the edit) |
| `can tx 1A3 DEADBEEF` | rc 0 |
| `can tx 1A3 --rtr 4` | rc 0 |
| `sysrq b`, then `cmd ping` | rc 2, timeout (observation confirmed) |
| `send ""`, then `cmd ping` | rc 0 |
| `POST /ports/sim/disconnect`, then `mcu status` | `held (disconnected on request)`; `--json` has `disconnect_reason: "manual"` |

`test_cli_contract.py` passes, and ruff is clean on cli.py.
SPEC 4's `sysrq` row (line 1278) does not mention the follow-up `send ""`. I left it alone because the guide is the agent-facing text; say if SPEC 4 should carry it too.

## Existing tests edited

- `test_serial_link_rx_ingest.py::test_an_open_unstorable_episode_is_recorded_at_stop`: the log assertion now expects the row text, because the count-only WARNING is gone.
- `test_reconnect.py::test_carried_counters_are_bounded_and_evict_the_oldest`: the filter now matches every eviction warning.
- `test_store_capture_wal_aside.py`: the local `MOVE_ASIDE` literal is replaced by an import from `mcuscope.cli` (import block re-sorted by ruff).
- `test_cli_status_capture_health.py::test_a_move_aside_cause_says_then_restart`: the cause is built from `cli.MOVE_ASIDE`, and the import gains `cli`.

## SPEC edits

- 2.2: "Every episode's row text is also logged at WARNING when the episode closes." An episode open at detach, reconnect or shutdown "closes then: its row is recorded if the store takes it, and the log line is the record if not."

## Changelog

- `mcu lines`, `log export` and `can dump`: with `--last-ms`, a `--from` past the window's ceiling (or a `--to` before its floor) now gives an empty answer, exit 0, instead of "until_ts is before since_ts".
- The log line for an unstorable rx episode now carries its count, window and cause, so a store still failing at stop no longer loses them.
- Exports on Windows: a reserved device name that is in fact a regular file is replaced atomically, and CONIN$, CONOUT$ and COM/LPT with superscript digits are recognised as devices.
- `mcu ai-guide`: `plot_name_refused` covers `!ps` samples too.
- `mcu ai-guide` corrections:
  - It names the `held (disconnected on request)` state.
  - `plot export --changes` needs `--decode`, and `--deadband` needs `--changes`.
  - `can tx --rtr` takes no DATA.
  - `sysrq` on a monitor port needs a following `mcu send ""`.

## Not done

- SPEC 3.2 line 507 (not mine): it says `capture_error` "ends `move <path>-wal and <path>-shm aside before restarting`", but the store's text continues ", or the restart replays it...". Change "ends" to "contains".
- FD-OUTER3-3 alternative not taken: exposing `carried_evicted` on `/status` would need server.py and SPEC 3.4. The warning now claims nothing about it.
- `server._WIN_RESERVED` (the download-stem sanitizer) lacks CONIN$, CONOUT$ and superscripts. No change is needed: its regex already turns `$` and non-ASCII into `_`.
- `ruff check .` from `host/` reports one error in `mcuscope/store.py:3727` (the other batch's in-flight edit). Ruff on every file this batch touched is clean.

## Doubts

- FD-OUTER3-4 is still unrun on Windows. It is not checked that `os.stat` on a bare `NUL` raises or returns S_IFCHR there. Either way the result is now correct: with no stat, the name decides, and with a stat, the mode decides.
- In the `--from` case of FD-OUTER3-1, the positive control relies on a row stamped +300 s being written last. A row stamped ahead with a lower id is hidden by the store's `idx_lines_ts` id cutoff (a documented price), which is why the first draft's control using `_ahead_and_now` found nothing.
