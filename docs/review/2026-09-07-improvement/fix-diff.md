# Fix-diff stage: the round's own fixes (working tree over 6f1441d)

Fixes were applied in-session, partitioned by file as three batches (CLI, daemon, web UI and sim). Every fix carries a test; each test was mutated or reverted in a worktree carrying the same diff (`/tmp/review-2026-09-07/` scripts) and confirmed to fail.

## Fixes

| finding | fix |
|---|---|
| C1 | `_stderr_log_path` is the pid record's name with `.err`, so the file is keyed by host:port. |
| C2 | `daemon start` and `restart` refuse `--open` with `--json` before anything is spawned or stopped (class 10). |
| C3 | The guide lists the completion flags outside GLOBAL OPTIONS and says they are accepted only right after `mcu`. |
| C4 | The completion exemption in `test_cli_contract.py` is removed; the substring check pins the guide line again. |
| C5 | `/status` gains `config_path` (SPEC 3.3); `restart` carries it and the running daemon's `sim://` port unless overridden. |
| C6 | Tests for the log-open fallback to DEVNULL and for the alias hint when `/ports` cannot be listed. |
| C7 | `daemon status` carries the start hint at the default URL; "last 1 line". |
| D1 | The race test parks the worker before the scan (class 50). |
| D2 | A test drives `SerialPort` against a `QueueFull` fast path and asserts the fallback stores every line. |
| D3 | Every worker-side read goes through `_on_read_conn`, which retries once on a fresh handle when `stop()` closed the cached one between the epoch check and the query; the first test version exposed that the match-query path bypassed the retry. |
| D5 | The inert `_plot_dirty = True` on commit failure is removed. |
| W1 | One top hit walks up to `HISTORY_HOPS` (5) pages until rows land or the walk ends (class 51). |
| W2 | `historyIdTo` floors at the pane's clear point and the request carries `since_id=clearId`. |
| W3 | The refill test drives a 1 ms-per-call clock, so 40 marks a render exhaust the 250 ms budget by the seventh render without the refill. |
| W4 | The idle-tick test changes a count in the model with no frame and asserts no cell shows it. |
| W5 | The marker test pins `next_marker == now + 15`. |
| W6 | The test drives a NaN range; the guard comment names NaN. |

Left as documented: D4 (`max_id` fast path after a top delete), D6 (rebuild at most once per size tick), D7 (unreachable duplicate names), W7 (`updateShown` walks the ring per render).

## Mutation verification of the fixes

| mutation | failed |
|---|---|
| C1 err file unkeyed again | yes |
| C2 start accepts --open with --json | yes |
| C2 restart accepts --open with --json | yes |
| C5 restart drops the running config | yes |
| C5 restart drops the sim port | yes |
| C7 daemon status without hint | yes |
| C4 guide drops the completion line | yes |
| /status without config_path | yes |
| D1 summary scan ignores `high` | yes |
| D3 read retry removed | yes |
| D2 QueueFull drops the line | yes |
| W1 no hop loop | yes |
| W1 unbounded hops (50) | yes |
| W2 no clear floor | yes |
| W2 since_id not sent | yes |
| W3 render does not refill | yes |
| W4 idle tick rebuilds | yes (after the first test version survived: a row added to the model was not observable, a changed count is) |
| W5 marker period 1.5 | yes |
| W6 NaN range accepted | yes |

## New class sweeps (run before close)

- Class 50: 5 spins on an Event in `host/tests`: `test_store_fastpaths.py` slow_scan (was the finding, now parks before the scan); `test_assert.py:788` needle, `test_regressions.py:373` heartbeat, `test_wait_repeat.py:51` `_run`, `test_cli.py:1331` (each spin is the double's own loop or a stop flag: exempt).
- Class 51: 1 fetching edge handler in the web UI, the scroll handler's `loadHistory` (was the finding, now hops); the bottom edge only toggles autoscroll (exempt).
- Class 52: 4 `user_data_dir` sites: pid record (keyed), `capture.db` (per config, exempt), `_stdio` startup and crash logs (keyed by `set_report_key`), stderr log (was the finding, now keyed). The update cache is under the config dir and shared by design (exempt).

## The two questions

Q1, least confident: the retry in `_on_read_conn` catches `sqlite3.ProgrammingError`, which is also what a genuinely wrong call raises; the epoch check limits the retry to the closed-handle case, and a wrong call re-raises on the same epoch. Second: `HISTORY_HOPS` at 5 with a 200-row page is 1000 server rows per top hit under a filter the daemon does not apply; the daemon applies `match` itself when the dialect agrees, so the hop loop only pays under a refused pattern.
Q2, the gap: `restart` now reads `config_path` from `/status` of a daemon that may be older than the CLI (class 46): it uses `.get`, so an older daemon restarts on the default config as before rather than failing. The W2 `since_id` floor is applied by the daemon; the JS fake was taught it for the test, which is class 27's shape, so the real `/lines` was checked to honour `since_id` with `id_to` (the backfill already pairs them).
