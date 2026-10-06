# Fix-diff review core3, 2026-10-05 (third pass: fix3, fix4-store, fix4-server)

Scope: `git diff 5eaaeeb` hunks named in `fix3.md`, `fix4-store.md`, `fix4-server.md`, against classes 92-104 and `sweeps-92-104.md`.
Scratch: `~/tt-data/mcuscope-2026-10-05/fixdiff-core3/` (probes, `mutate.py`, `mutate.log`, `run1.log`; tmp capture dirs under it).
Tree checked unchanged after mutation (`git diff --stat` before and after identical).

## Findings

### FD-CORE3-1 LOW CONFIRMED: `/plot/export` still echoes client names and deadband items whole
- Where: `server.py:2389-2391` (`"no such plot channel: " + ", ".join(unknown)`) and `server.py:3976`, `:3978`, `:3980`, `:3985`, `:3987` (`_parse_deadband` formats `item` / `name` uncut, returned by `_bad_request(str(exc))` at `:2366`).
- Failure: V96-2's fix cut `names lists X twice` (`:2353`) in this same handler but not the two refusals a few lines below it. The class is not closed.
- Repro (`probe_echo.py`, TestClient, 5000-character value):
  - `/plot/export?names=<5000 a>` -> 400, 5054-byte body, `no such plot channel: aaaa...` whole.
  - `/plot/export?names=x&changes=1&decode=1&deadband=<5000 a>` -> 400, 5039 bytes, `deadband needs name=value: aaaa...`.
  - `deadband=zz=<5000 a>` -> 400, 5051 bytes, `deadband names no exported channel: zz=aaaa...`.
- Fix: `_excerpt` each unknown name and cap the list with `_error_list`-style counting; `_excerpt(item)` / `_excerpt(name)` in all five `_parse_deadband` messages. Add these three URLs to `SITES` in `test_server_error_excerpts.py`.
- Class 96.

### FD-CORE3-2 LOW CONFIRMED: `_db_realpath`'s `:memory:` branch is untested
- Where: `server.py:1223`.
- Failure: with the branch removed, `realpath(":memory:")` is `<cwd>/:memory:`, so `_export_dir` returns the daemon's working directory: an in-memory capture (`--sim`) writes session-export temp copies into the cwd, and the startup orphan sweep lists the cwd.
- Repro: mutation D in `mutate.py` (`return os.path.realpath(db_path)`) survives `test_server_db_identity.py`, `test_server_exports.py` and `test_config_api.py` (67 passed).
- Fix: a test with `db_path=":memory:"` asserting `app.state.db_realpath == ":memory:"` and that a session export's temp copy is not created in the cwd (or `_export_dir(app.state.db_realpath) is None`).
- Class: untested changed line (leg 7).

### FD-CORE3-3 LOW CONFIRMED: a purge refused by another process's lock logs a traceback and does not join the lock episode
- Where: `store.py:3598-3610` (`_delete_chunks`), answered by `server.py:650-657` (`_unhandled_error`, `log.exception`).
- Failure: the sweeps now treat `_is_busy` as the lock episode (V103-1), but a purge or `DELETE /sessions/{id}?data=true` hitting the same lock goes `StoreError` -> re-raised by `_store_error` -> `log.exception("unhandled error ...")` with a full traceback, a 500, and no `db_locked_since`. The message itself is right.
- Repro (`probe_purge.py`: `_delete_range_chunk` raises `database is locked` on its second chunk, 3 rows per chunk): `500 {"error":"purge stopped after deleting 3 lines: database is locked"}`, version header present, traceback in the log, `/status` unchanged.
- Fix: in `_delete_chunks`, call `self._sweep_refused(exc)` for a busy error (opens the episode, rolls back), and have the server answer that case 503 (`capture locked by another process`) without `log.exception`. Pin with the probe as a test.
- Class 103.

### FD-CORE3-4 LOW CONFIRMED: a sweep cut short at stop with no room to queue its row loses the count with no log line
- Where: `store.py:3699-3722` (`_sweep_row`).
- Failure: on `QueueFull` / `StoreError` it returns None before `log.warning`, keeping the count pending for "the next sweep". On the cancel path (`stop()`) there is no next sweep, and nothing else reads `_trim_unannounced` / `_expire_unannounced`, so the deleted lines are recorded neither in the capture nor in the log.
- Repro (`probe_sweeprow.py`): pending 1234, `submit_line_nowait` raising `QueueFull`, `exc=CancelledError()` -> returns None, no log output, then `stop()`.
- Fix: log the row text at warning on the failure path too (e.g. `...; not recorded in the capture: write queue full`), so the stop case leaves at least the log line. Pin with the probe.
- Class 94.

### FD-CORE3-5 NIT CONFIRMED: a folded trim row names the latest trim's cap
- Where: `store.py:3733` (`self._trim_cap = cap`).
- Failure: `fix4-store.md` says a carried row "names that cap and not the current one". That holds only when the next sweep trims nothing; if it trims under a cap changed live (`PUT /config/storage`), the folded row names the new cap for lines trimmed under both.
- Fix: write the carried row before a sweep that changes `_trim_cap`, or word the row without a cap when counts from two caps fold.
- Class 94.

### FD-CORE3-6 NIT CONFIRMED: `write_new_file` comments overstate two things
- Where: `config.py:605-617`.
- `os.fchmod` exists on Windows from Python 3.13 (the venv's version), so the "POSIX only" note in `fix3.md` is wrong. Behaviour is benign: it sets the read-only flag the `os.open` mode had already set.
- "Never chmods an existing file": the temp is opened `O_CREAT | O_TRUNC` without `O_EXCL`, so a leftover `<name>.<pid>.tmp` from a crashed run with a reused pid is an existing file that gets truncated and chmodded. Benign in a 0700 config dir; either add `O_EXCL` (unlink a stale temp first) or drop the claim.

### FD-CORE3-7 NIT CONFIRMED: `_keep_recent_names` walks every port's keys per new name, now for typed names too
- Where: `store.py:171-192`, called from `_note_plot` (`:2964`) on the writer.
- Measured (`bench_prune.py`, 300 new names per port): 4 ports 0.04 s total, 16 ports 0.54 s, 64 ports (the config maximum) 8.1 s of writer CPU, worst single call 3.2 ms.
- Fix: keep a per-port key count and prune only when that port passes `ADHOC_NAMES_MAX`, iterating that port's keys only.

## Checked and fine

- **V104-2, `ceil_ts` mandatory.** Every production reader is reached through `_resolve_window`, whose scope (`server.py:3675-3679`) sets `floor_ts` and `ceil_ts` from the same `anchor`, both None or both set. `**win.scope` feeds `/lines`, `/lines/export`, `/can/frames` and its CSV, `/plot/export` (`export_sids_safe`, `first_export_line_id_safe`, `open_plot_export`); `/assert` unpacks and forwards both (`:3476-3486`). Every `_safe`/`open_*`/`iter_*` wrapper forwards `**kwargs` to a reader with an explicit `ceil_ts`. No `floor_ts` caller exists outside `store.py`/`server.py`.
  - A missing one fails loudly: `ValueError` -> `_unhandled_error` 500. The lines export primes its first page before the 200 (`_pull_first`), and the plot export calls `first_export_line_id_safe` first. Only the CAN CSV export would raise after its 200, which no production scope can reach.
  - The 11 edited tests pass `store._window_anchor(<same bound>)`, which is the value the removed fallback derived; M20 (fixer) caught.
- **Sweeps cut short by a lock.** Per-chunk counting in `_trim_oldest` and `_sweep_retention_locked`, the row on every exit, the `_sweep_refused` rollback, the age-sweep retry, the startup retry, and `_close_lock_episode`'s queue-room guard all read right. Mutations A (drop the rollback after `BEGIN IMMEDIATE`) and B (drop the `stopped early` suffix) CAUGHT, on top of the fixer's 22.
  - The `stopped early: database is locked` row goes through the writer the lock is holding (class 93 shape), but it is only delayed: the count stays pending if it cannot queue, and the log line is immediate.
  - A success-path `await fut` can park the retention loop until a held lock clears; harmless, since the writer closes the episode itself.
- **Typed-name pruning.** The newest key always survives (ids are monotonic); the rebuild applies the same function; an evicted name returning marks the summary dirty and rebuilds once per batch of returns. The decoder caps each attachment at 256 names, so a port cannot churn more than that per attachment.
- **`_db_realpath` once at lifespan.** Keyed as `CaptureLock` keys (`realpath`). A `db_path` symlink retargeted later leaves export copies and the orphan sweep with the file actually open, and `restart_required` compares the saved path's current target with the start-time file, so it reports a restart. The store's own identity check refuses the replaced path.
- **`_same_path` on Linux.** `normcase` is the identity on POSIX: `Cap.db` vs `cap.db` (two real files) compares False, `./cap.db` True, `:memory:` both sides True (driven in-process).
- **422 cap and `_excerpt`.** `where` cut, list capped at five plus `; and N more`, the undeclared-query 422 cut and capped. `msg` texts are pydantic or constant (`_url_grammar`, `_stripped`). Mutation E (no cap) CAUGHT. Other `_bad_request` f-strings checked: `host {exc}` (255-char field), port aliases (pattern-bounded), `match` errors (200-char cap), `can id out of range` (18 chars) are bounded.
- **WebSocket close reason.** `raw[:120].decode("utf-8", "ignore") + "..."` is at most 123 bytes and never splits a character; the test's 3-byte case lands mid-character (106 mod 3 = 1). Mutation C (cut at 123 with no room for `...`) CAUGHT. Starlette's query parsing yields no lone surrogates, so the `encode` cannot raise.
- **Token guard log.** Once per episode, reset when expiry alone frees room; mutation H CAUGHT.
- **fix3.** `O_BINARY` reaches `os.open`; `_move_wal_aside` returns False on an unreadable stat (mutation F CAUGHT); `fchmod` (mutation G CAUGHT). The lockfile identity error is an `OSError`, which `daemon.py:482` handles outside the `--ignore-capture-lock` branch, so "does not apply" is accurate.
- **V92-3 open identity:** `_open_writer` closes the connection before raising; `self._conn` stays None.
- The ten new test files run together: 56 passed (`run1.log`).

## Not covered

- Windows for all of it: `normcase(realpath)` on mapped drives and UNC, `fchmod` on 3.13, `O_BINARY` and the CRT.
- `_close_lock_episode` against a writer mid-backoff (the fixer's doubt), not driven.
- Python 3.10 floor; the whole suite (per brief).

## The two questions

1. **Least confident:** that the stop-path count loss (FD-CORE3-4) cannot also happen with the writer alive. Re-driven: `_sweep_row` with `QueueFull` returns None with no log, and `grep _trim_unannounced` shows no other reader, so it is lost only at stop; outside stop the next sweep folds it.
2. **Not thought about:** the sibling refusals in a handler the fix touched. V96-2 was swept as a list of sites, and the `/plot/export` handler's own lines below the fixed one were not on it (FD-CORE3-1). Likewise V103's lock handling was framed around the sweeps, and the purge path that shares `_delete_lines` was not (FD-CORE3-3).
