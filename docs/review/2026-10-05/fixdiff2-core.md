# Fix-diff, second pass: core2

Scope: the second fix round's hunks in `store.py`, `server.py`, `daemon.py`, `lockfile.py`, `pidfile.py`, `_stdio.py`, `dirs.py`, `config.py`, `update_check.py` and their tests (fix2-store, fix2-server, fix2-leftovers, fix-server2 "RES-8 traceback").
Scratch: `~/tt-data/mcuscope-2026-10-05/fixdiff-core2/` (probes, `mutate.py`, outputs). No daemon started; tree unchanged (`git diff --stat` compared before and after).

## Findings

### FD-CORE2-1 HIGH SUSPECTED (Windows): `write_new_file` opens in CRT text mode, so a config save writes CRLF and the second one corrupts config.toml
- `config.py:613`: `os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)` has no `O_BINARY`.
  - On Windows `os.open` defaults to text mode, and `os.fdopen(fd, "wb")` keeps the fd's mode, so every `\n` is written as `\r\n`.
  - The repo already knows this: `pidfile.py:179` adds `O_BINARY` "no CRT text-mode translation", and `lockfile.py:163` does too. `tempfile` adds it for the same reason.
- Failure, from `_write_doc` (settings save, `mcu config set`):
  - The file holds CRLF while `_write_doc` returns `config_revision(data)` of the LF bytes, so the next save with that revision is refused as `ConfigConflict`.
  - tomlkit keeps the CRLF it parsed, so the next save writes `\r\r\n`. The load after that fails.
- Repro (Linux simulation of the CRT `_write` translation, `b.replace(b"\n", b"\r\n")` per save): save 0 ok, save 1 load fails with `InvalidControlChar ... at line 2`. So the daemon would refuse its own config after two saves.
- `test_config_api.py:594` (`b"\r\n" not in raw`) should fail on the Windows CI leg; `test_config_private_files.py` skips there.
- Also hits `update_check.py:218` (the cache; JSON, so parse survives, bytes differ).
- Fix: `os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)`.
- Class 2. Its sweep excludes `os.open` (`grep -v ... os.open`), which is how this passed: widen it to "every `os.open` whose fd is written carries `O_BINARY`". Swept now: `config.py:613` is the only hit (`dirs.private_opener` receives `O_BINARY` from `io.open`).

### FD-CORE2-2 LOW CONFIRMED: an unreadable `stat` of an intact capture lets `_move_wal_aside` rename the capture's own WAL
- `store.py:934-936`: `with contextlib.suppress(OSError): if self._file_identity() == self._db_identity: return True`. Any `OSError` other than "not found" (EIO, ESTALE, EINTR) is read as "not ours" and the rename runs.
  - This is the FD-STORE-9 path: `_check_path` now fails capture on such an error, and if a read is open the TRUNCATE is refused and `_move_wal_aside` follows.
- Repro (`probe_aside2.py`): 600 rows, a reader in a child process holding `BEGIN; SELECT`, `os.stat` of the main path patched to raise EIO, `sweep_tick`, child killed.
  - Both `-wal` and `-shm` were renamed `.stale-*` while the main file was still the capture.
  - The main file at the path then had **no `lines` table** (`no such table: lines`): everything since the last checkpoint is gone from it, and `capture_error` does not say to move anything back.
  - With the reader closing cleanly instead (`probe_aside.py`) its close checkpoints the renamed WAL and all 600 rows survive, which is why the failure needs a reader that dies.
- Fix: rename only when the main path is proven not ours (`FileNotFoundError`, or an identity that differs); any other `OSError` returns False, so `capture_error` carries the "move aside" instruction instead.
- Class 12 (a remedy chosen on an unknown); the same "unknown read as proof" shape as class 92.

### FD-CORE2-3 LOW CONFIRMED: "a replace keeps what the user set" holds only for modes the umask leaves alone
- `config.py:605-613`: the old file's mode is passed to `os.open`, which applies the umask.
- Repro (`probe_modes.txt`):
  - umask 022, existing 0664: 0644 after the replace.
  - umask 077, existing 0644: 0600 after the replace.
  - umask 022, existing 0640: 0640 (the case `test_config_private_files.py` tests).
- Fix: on POSIX, `os.fchmod(fd, mode)` after the open when `like` existed. That chmods only our new temp, so the class 98 rule "an existing file is never chmodded" still holds.
- Test: an existing 0664 under umask 022, and 0644 under umask 077.
- Class 98.

### FD-CORE2-4 LOW CONFIRMED: the FD-STORE-1 guide wording did not land
- fix2-store "Guide wording" handed two items to the cli batch. `grep -n "stale-\|aside" host/mcuscope/cli.py` finds nothing, and fix2-cli.md does not mention them.
- `mcu status` (`cli.py:219-221`) prints `CAPTURE STOPPED: ...; its WAL could not be emptied: move <db>-wal and <db>-shm aside before restarting ...; restart the daemon (mcu daemon restart)`. The last clause is the one an agent acts on.
- `mcu ai-guide` says nothing about `.stale-<stamp>` files or the move-aside step.
- Fix: in `cli.py`, print "then restart the daemon" when the reason contains "aside before restarting". Add the two guide lines from fix2-store.
- Class 100 (and 12: the surface's remedy is the damaging one if read last).

### FD-CORE2-5 NIT CONFIRMED (by reading): the lock-identity give-up has no escape hatch
- `lockfile.py:151-158` turns the hang into an `OSError`, and `daemon.py:482` says `--ignore-capture-lock` does not make it survivable.
- The module docstring promises an override for "some network mounts". A filesystem with unstable file ids now refuses to start with no override, and `verify()` would stop capture on the first tick anyway.
- Fix: either let `--ignore-capture-lock` skip the identity check and `verify`, or say in the error that this filesystem is unsupported for the capture.

## Checked and fine

- **The `.stale` rename cannot take a new capture's live files.**
  - The `(st_dev, st_ino)` compared are those of `-wal`/`-shm` our writer holds open, so those inodes cannot be reused. A new opener after a delete gets new inodes, which do not match and stay.
  - A lock-lost failure on an intact file returns before any rename (mutation "intact guard" CAUGHT).
  - SQLite skips the close-time checkpoint **and** the by-name WAL/SHM unlink once the main file has moved (`databaseIsUnmoved` gates both). So no later close deletes a new capture's `-wal` at the name.
  - Windows: the replace and the delete are refused, and a rename of the open `-wal` is refused too, which takes the announce path.
- **The ~1 s TRUNCATE retry.** It runs on the loop (the writer is loop-owned), not in an executor, but it yields between tries and each try is bounded by the 5 ms busy timeout.
  - Measured (`probe_aside.py` stall case): 3300 rows, a reader open, the main file moved. The failure took 1.13 s and the worst loop stall was 9.9 ms.
  - `stop()` during the wait: the `finally` still moves the files aside and swaps in `_FailedConn` (tested).
- **`_BodyLimit` reading every body.**
  - It buffers at most 64 KiB per request, plus the joined copy. It sits inside the token and origin guards, so an unauthenticated body is never buffered.
  - WebSocket and lifespan scopes pass through untouched. A disconnect is replayed to the app.
  - After the replay, `receive` is the real one, so Starlette's disconnect listeners still see `http.disconnect`.
  - No client sends on `/ws`, which makes `ws_max_size=MAX_BODY_BYTES` safe: no `.send(` in `webui/*.js`, and the CLI only receives.
- **`ceil_ts` reaches every reader.** `**win.scope` feeds seven store entry points: `query_lines_safe`, `count_lines_safe`, `open_lines_export`, `open_can_export`, `export_sids_safe`, `first_export_line_id_safe` and `open_plot_export`.
  - Each `_safe`/`open_*`/`iter_*` wrapper forwards `**kwargs` to a reader with an explicit `ceil_ts` parameter.
  - The retrospective `/assert` unpacks and forwards it. No other `floor_ts` caller exists in `server.py`.
  - The anchor is taken once, before the until-fold and the freeze.
- **Private modes.** pidfile claim, the no-link fallback and `create_record` are covered, and so are the lockfile, `_stdio._write_report` and `make_private_dirs`, including the race and the file-at-path case.
  - `private_opener` gets `O_BINARY` from `io.open`'s flags, so it is not affected by FD-CORE2-1.
- **`_ShortCaptureError`** is installed and removed beside `_FirstError`. It rewrites only a record whose last line names `CaptureUnreadable`.
- **Revert-verification** (`mutate.py`, every anchor checked first, files restored and `cmp`-verified): 6 of 6 CAUGHT.
  - The intact-file guard, `_WAL_EMPTY_TRIES = 1`, `like` mode ignored and `ceil_ts: None` in `_resolve_window`.
  - The body count skipped when a length is declared, and the short-traceback filter off.
- The touched test files run together: 86 passed, 1 skipped (`run_tests.txt`).

## Not covered

- Windows, for all of it. FD-CORE2-1 rests on CRT semantics and a Linux simulation, and the new Windows-only test `test_windows_refuses_to_replace_or_delete_an_open_capture` has not run anywhere.
- An opener of the restored file between the replace and the tick that detects it (up to one 60 s tick, plus the new ~1 s wait). It pairs the restored file with the old WAL. This is inherent to tick detection, not new.
- During the ~1 s wait, `start_session`/`delete_session` still commit through the real writer into the replaced file and answer success. That is the same loss the pre-detection window already has, only a second longer.
- NFS/SMB rename and inode semantics; an outside writer.
- The lock-identity error on Windows, and the real `mcuscoped` 1009 close.

## The two questions

1. **Least confident:** that Windows `os.fdopen(os.open(...), "wb")` translates newlines (FD-CORE2-1). I rechecked it against the repo's own `O_BINARY` comments and `tempfile`'s use of the flag, but did not drive it. The Windows CI leg's `test_config_api.py:594` settles it.
2. **Not thought about:** class 2's sweep excludes `os.open` by construction, so a helper that writes through a raw fd escapes it. More generally, "unknown" branches (`suppress(OSError)`) in a destructive remedy. FD-CORE2-2 came from asking what the new FD-STORE-9 `OSError` path feeds into downstream.
